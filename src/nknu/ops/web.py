"""检视台 (plan 2026-11-15 T5; spec 2026-11-15 section 二): two pages for the owner, 搜尋測試 (why a search ranked
as it did: nknu.ops.explain) and 文件追蹤 (what one page or file became at each step: nknu.ops.trace; the two were
搜索试验台 and 逐份追踪 until the owner, 11-15: 「很多名字看着不直觉」), and the same two
as JSON for the commands (nknu.ops.cli's --at). The pages draw what the two modules return and compute nothing; how
they look is nknu.ops.look, search_page and trace_page (the third round of 11-15), and the scoreboard of the test
questions is nknu.ops.board.

It reads the library, which holds students' names, so it is read-only (the database is opened read-only on every
request), every route is GET only, and a request is answered only when its Host names this machine (127.0.0.1,
localhost, [::1]): the lab's Docker container listens on 0.0.0.0, Docker forwards only the lab PC's own 127.0.0.1 to
it, and the Mac comes in through an SSH tunnel, as with the archive viewer. An archived page is shown under a policy
that lets it load nothing.

A third page, 網頁核查 (design docs/superpowers/specs/2026-11-16-page-check-design.md), shows one page of the crawler's
archive as it was stored, its regions and its links, images and buttons coloured by the crawler's ledger (nknu.ops.
pagecheck, nknu.ops.check_page). The archive (--archive: the folder holding frontier.db, whose url.warc paths start
there) is read only; the stored page is served from here (/check/raw) under a policy that lets it load nothing but our
own archived images (/check/res), so the browser never reaches a school server from it.

網頁核查 needs no address to be remembered (design section 六; owner 11-16: 「为什么要我输入网址或文件编号？我怎么会记得
网址或者文件编号呀？」): /check with nothing asked lists the sites with their deciding numbers and finds sites and pages as
one types (/check/list), /check?site=… is one site's misses and pages (/check/site), and every page of the ledger is
checked in the background by nknu.ops.sweep (one thread; started by the first request of 網頁核查, stopped with the
app), the site opened last first; /api/check/sweep says how far it has come.

    python -m nknu.ops.web [--db research/pipeline_2026-11-02/aca.db] [--root .] [--host 127.0.0.1] [--port 8878]
                           [--viewer-url http://127.0.0.1:18877] [--questions DIR] [--gold FILE]
                           [--archive /crawl/archive] [--no-dense] [--no-rerank]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import quote

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from nknu.core.timeutil import today_tpe
from nknu.ops import pagecheck
from nknu.ops.board import Board
from nknu.ops.check_page import check_body, empty_raw
from nknu.ops.check_sites import entry_body, entry_list, item_html, site_body, site_parts
from nknu.ops.explain import explain_search
from nknu.ops.look import e, s2t, shell
from nknu.ops.lookup import as_address, columns, has_table, resolve
from nknu.ops.pipeline import Pipeline, Where, site_detail
from nknu.ops.pipeline_page import pipeline_body, sites_html
from nknu.ops.pipeline_page import site_body as pipeline_site_body
from nknu.ops.search_page import board_html, question_nav, search_body, search_form
from nknu.ops.sweep import Sweep
from nknu.ops.trace import WITHIN, site_exports, trace
from nknu.ops.trace_page import FOUND_MAX, MARKUP, examples_html, found_html, trace_body, trace_form
from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import TOP_N
from nknu.retrieval.server import DEFAULT_DB, add_model_flags, aliases_of, config_from, connect, search_answer
from nknu.settings import REPO_ROOT
from nknu.totext.cache import sha256_file

log = logging.getLogger(__name__)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
DEFAULT_PORT = 8878
MAX_LIMIT = 50  # the most results a page or the API explains at once 【Claude】
# what an archived page is shown under: it loads nothing and runs nothing (the archive viewer's PREVIEW_CSP)
ARCHIVED_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; media-src 'none'; "
                "frame-src 'none'; form-action 'none'; sandbox")
# what 網頁核查's archived page is shown under (design section 四): nothing but our own images and its inline styles, in
# a frame of this server's only; and an archived image, which may be an SVG, as a document that runs nothing
CHECK_RAW_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'self'; "
                 "form-action 'none'; base-uri 'none'")
CHECK_RES_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}
OWN_CODE = "nknu/ops/"  # the tools' own package: never the pipeline's (install_state)
STATE_SECONDS = 60.0  # how long an install state is kept before the two source trees are compared again 【Claude】
CHECK_SECONDS, CHECK_KEEP = 120, 8  # a check kept this long, this many at most 【Claude】
EXAMPLES = 5  # how many documents the trace form offers to start from 【Claude】
# an address typed without its scheme: a host of three labels or more (c.nknu.edu.tw/aca); anything shorter typed in
# 網頁核查's box (List.aspx, 教務) is words to find sites and pages by (design section 六 1) 【Claude】
HOSTLIKE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+){2,}(:\d+)?(/|$)", re.IGNORECASE)


def local_host(header: str) -> bool:
    """Whether a Host header names this machine: one of LOCAL_HOSTS, alone or followed by a port, and nothing else
    (「[::1]evil.example」 and 「127.0.0.1.evil.example」 do not)."""
    host = header.strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
    else:
        name, colon, port = host.partition(":")
        rest = colon + port
    return name in LOCAL_HOSTS and (not rest or (rest[0] == ":" and rest[1:].isascii() and rest[1:].isdigit()))


class LocalOnly:
    """The ASGI app that passes a request on only when its Host is one of LOCAL_HOSTS (any port); any other gets 403:
    a page of another site, opened in the owner's browser, cannot read the library under a name of its own."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
            if not local_host(host):
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install_state(code_root: Path, pipeline_src: Path) -> dict | None:
    """What install.sh left of the copy the tools run from, and which files of the pipeline's source have changed
    since: {'installed', 'pack', 'drift'}; None when code_root is no installed copy (no INSTALLED file: the
    repository itself, where the tools and the pipeline are one tree).
    The tools run a copy of the pipeline's source with their own files laid over it (deploy/lab-inspect/install.sh;
    plan 2026-11-15 T8), so after the pipeline's code changes, the scores and the left-out reasons shown here are the
    older rules' until the copy is installed again (review 11-15: nothing on the pages showed it). drift: the
    pipeline's .py files that differ from the copy's, a new file of the pipeline's counting too; a file the pack
    replaced counts only when the pipeline's is neither the version the pack was made from (BASE.sha256) nor the
    copy's own (after a code sync the pipeline has the tools' changes too: nothing to warn of); the tools' own
    package aside."""
    try:
        stamp = (code_root / "INSTALLED").read_text(encoding="utf-8").split()
    except OSError:
        return None
    base: dict[str, str] = {}
    try:
        for line in (code_root / "BASE.sha256").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition("  ")
            if name.strip():
                base[name.strip()] = sha.strip()
    except OSError:
        pass
    drift = []
    for path in sorted(pipeline_src.rglob("*.py")):
        rel = path.relative_to(pipeline_src).as_posix()
        if rel.startswith(OWN_CODE):
            continue
        ours = code_root / "src" / rel
        same = {sha256_file(ours)} if ours.is_file() else set()
        if (made_from := base.get("src/" + rel)) is not None:
            same.add(made_from)
        if sha256_file(path) not in same:
            drift.append(rel)
    return {"installed": stamp[0] if stamp else "", "pack": stamp[1] if len(stamp) > 1 else "", "drift": drift}


def load_questions(folder: Path | None, gold: Path | None) -> list[dict]:
    """The test questions that have gold documents, [{id, question, gold}], in the files' order: core20.json and
    noanswer22.json of folder (research/eval_2026-11-01) and the gold file (default: folder's gold_docs.json). [] when
    a file is missing or unreadable: the pages then offer no question."""
    if folder is None:
        return []
    try:
        answers = json.loads(Path(gold or Path(folder) / "gold_docs.json").read_text(encoding="utf-8"))
        asked = [q for name in ("core20.json", "noanswer22.json")
                 for q in json.loads((Path(folder) / name).read_text(encoding="utf-8"))]
        return [{"id": q["id"], "question": q["question"], "gold": list(answers[q["id"]].get("docs") or [])}
                for q in asked if q.get("id") in answers and q.get("question")]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        log.warning("读不了测试题（%s）：页面上不列题目", folder)
        return []


def asks_a_page(ref: str, library: sqlite3.Connection | None) -> bool:
    """Whether what 網頁核查's box was given names one page to check: an address (with its scheme, or a host as
    HOSTLIKE reads one) or a document id of the library. Anything else is words: the entry finds sites and pages by
    them, and never answers 「帳本裡沒有這個網址」 for words (design section 六 1)."""
    ref = (ref or "").strip()
    if not ref:
        return False
    if ref.lower().startswith(("http://", "https://")) or HOSTLIKE.match(ref):
        return True
    return library is not None and resolve(library, ref).doc_id is not None


def find_titles(conn: sqlite3.Connection, words: str, limit: int = FOUND_MAX) -> list[dict]:
    """Documents whose title holds words, as typed or in Traditional characters, newest first; limit + 1 at most, so a
    caller knows there are more. Works on a library built before pipeline-7 (no site column, no sites table)."""
    forms = list(dict.fromkeys(x for x in (words.strip(), s2t(words.strip())) if x))
    if not forms:
        return []
    have = columns(conn, "docs")
    joined = "site" in have and has_table(conn, "sites")
    site = "d.site" if "site" in have else "''"
    name = "s.name" if joined else "''"
    join = "LEFT JOIN sites s ON s.site = d.site" if joined else ""
    where = " OR ".join("instr(d.title, ?) > 0" for _ in forms)
    sql = (f"SELECT d.doc_id, d.title, d.kind_class, d.ident_date, {site}, {name} FROM docs d {join} WHERE {where}"
           " ORDER BY d.ident_date IS NULL, d.ident_date DESC, d.title LIMIT ?")
    return [{"doc_id": r[0], "title": r[1], "kind": r[2], "date": r[3], "site": r[4], "name": r[5]}
            for r in conn.execute(sql, [*forms, limit + 1])]


def examples(conn: sqlite3.Connection, questions: list[dict]) -> list[dict]:
    """A few documents to follow, [{doc_id, title, kind}]: the first gold answer of the first test questions that
    the library holds."""
    ids = list(dict.fromkeys(q["gold"][0] for q in questions if q["gold"]))[:EXAMPLES * 3]
    if not ids:
        return []
    found = {r[0]: r for r in conn.execute(
        f"SELECT doc_id, title, kind_class FROM docs WHERE doc_id IN ({','.join('?' * len(ids))})", ids)}
    return [{"doc_id": i, "title": found[i][1], "kind": found[i][2]} for i in ids if i in found and found[i][1]][:EXAMPLES]


# ---------------------------------------------------------------- the app

def create_app(db_path: Path, root: Path, *, viewer_url: str = "", questions: Path | None = None,
               gold: Path | None = None, config: HybridConfig | None = None, models: dict | None = None,
               today_fn: Callable[[], date] = today_tpe, code_root: Path | None = None,
               pipeline_src: Path | None = None, overview_url: str = "", tables_url: str = "",
               archive: Path | None = None, watch: tuple[tuple[str, str, str], ...] = ()) -> Starlette:
    """The app over one library file. root: the folder its relative paths start from; viewer_url: where the owner's
    browser reaches the archive viewer ('' : no link); questions and gold: load_questions; config: the search's
    settings (None: the server's); models: embedder and reranker in place of the process's shared ones (tests);
    code_root and pipeline_src: the tree this code runs from (default: the running package's) and the pipeline's
    source (default: root / src), compared by install_state; overview_url and tables_url: where the owner's browser
    reaches 资料总览 and Datasette (流水線 links them; the top bar's addresses are nknu/ops/theme/panels.json); archive: the
    crawler's archive folder (frontier.db and its WARC files) 網頁核查 reads, None: the page says it is not mounted;
    watch: the services 流水線 asks whether they answer, (name, address from this process, how: 'mcp' or 'http')."""
    db_path, root = Path(db_path), Path(root)
    code_root = Path(code_root) if code_root is not None else REPO_ROOT
    pipeline_src = Path(pipeline_src) if pipeline_src is not None else root / "src"
    checked: dict = {"at": 0.0, "state": None}

    def installed() -> dict | None:
        """install_state, compared again at most every STATE_SECONDS."""
        if time.monotonic() - checked["at"] > STATE_SECONDS or not checked["at"]:
            try:
                checked["state"] = install_state(code_root, pipeline_src)
            except OSError:
                log.exception("比不了检视工具装的程序和建库用的程序")
                checked["state"] = None
            checked["at"] = time.monotonic()
        return checked["state"]

    models = dict(models or {})
    asked = load_questions(questions, gold)
    by_id = {q["id"]: q for q in asked}
    busy = threading.Lock()  # one search at a time: the two models are shared by every request

    def run_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]], **kw):
        return hybrid_search(conn, query, today=today, aliases=aliases, config=config, **models, **kw)

    def db_info(conn: sqlite3.Connection) -> dict:
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'schema_version')"))
        return {"built_at": meta.get("built_at"), "schema": meta.get("schema_version"), "install": installed()}

    def asked_for(request: Request) -> tuple[str, list[str], int, dict | None]:
        """(the question, the documents to follow, the limit, the test question when one was picked)."""
        p = request.query_params
        question = by_id.get(p.get("qid", ""))
        docs = [d for value in p.getlist("doc") for d in value.split()]
        asked_limit = p.get("limit", "")  # 「²」 is a digit to str.isdigit and no number to int
        plain = asked_limit.isascii() and asked_limit.isdigit() and len(asked_limit) <= 4
        limit = min(int(asked_limit), MAX_LIMIT) if plain else TOP_N
        if question is not None:
            return question["question"], list(dict.fromkeys([*question["gold"], *docs])), limit, question
        return p.get("q", "").strip(), docs, limit, None

    def explained(conn: sqlite3.Connection, query: str, docs: list[str], limit: int) -> dict:
        with busy:
            return explain_search(conn, query, today=today_fn(), aliases=aliases_of(conn), config=config, limit=limit,
                                  follow=docs, **models)

    def traced(conn: sqlite3.Connection, ref: str) -> dict:
        def own_title(c: sqlite3.Connection, title: str) -> tuple[list[str], str]:
            with busy:
                found = run_search(c, title, today=today_fn(), aliases=aliases_of(c), limit=WITHIN)
            return [h.doc_id for h in found.hits], found.ranking

        return trace(conn, ref, root=root, today=today_fn(), own_title_search=own_title)

    def board_state() -> tuple:
        """What the scoreboard's grades hold for: this library file as it is now, on this day."""
        stat = db_path.stat()
        return (stat.st_mtime_ns, stat.st_size, today_fn().isoformat())

    def explain_question(question: dict) -> dict:
        conn = connect(db_path)
        try:
            return explained(conn, question["question"], question["gold"], TOP_N)
        finally:
            conn.close()

    scores = Board(asked, explain_question, board_state)

    def board_now() -> dict | None:
        """The scoreboard as it stands, or None when the library file cannot be looked at just now (it is being
        swapped): a page then draws without its board instead of failing."""
        try:
            return scores.snapshot()
        except OSError:
            log.exception("成绩板：看不到资料库档")
            return None

    # 流水線, the home (owner 11-16: 「让我知道整个项目是否正常运作，出错的话是哪里错了 能帮助我精准定位」): every step
    # read in the background (nknu.ops.pipeline); the two tools that were the home are /search and /trace, whose
    # empty pages hold the same box and, on /search, the scoreboard
    def sweep_overview() -> dict | None:
        """網頁核查's results as they stand, never starting its sweep (a restart's 17 minutes are its own to start)."""
        return sweep.overview() if sweep is not None else None

    where = Where(root=root, db=db_path, archive=archive, services=tuple(watch),
                  board=lambda: scores.snapshot(start=False), sweep=sweep_overview, today=today_fn)
    flow = Pipeline(where)
    panels = {"overview": overview_url, "board": "/search", "check": "/check", "tables": tables_url,
              "crawl": f"{viewer_url}/progress" if viewer_url else ""}

    def home(request: Request) -> Response:
        snap = flow.snapshot(fresh=request.query_params.get("fresh") == "1")
        conn = connect(db_path)
        try:
            return shell("流水線 · 檢視台", pipeline_body(snap, panels, sites_html(snap)), db_info(conn),
                         tab="pipeline")
        finally:
            conn.close()

    def pipeline_site(request: Request) -> Response:
        """One site through the pipeline (流水線's table links here): its numbers and what it lost, by reason."""
        key = request.query_params.get("site", "").strip()
        detail = site_detail(where, key) if key else None
        conn = connect(db_path)
        try:
            if detail is None:
                body = (f"<section class='card pad'><span class='pill none'>沒有這個網站</span>　正式庫裡沒有「{e(key)}」。"
                        " <a href='/#sites'>回到每個網站的表</a></section>")
                return shell("沒有這個網站 · 流水線", body, db_info(conn), status=404, tab="pipeline")
            name = detail["site"].get("name") or key
            return shell(f"{name} · 流水線", pipeline_site_body(detail, panels), db_info(conn), tab="pipeline")
        finally:
            conn.close()

    def pipeline_api(request: Request) -> Response:
        return JSONResponse(flow.snapshot(fresh=request.query_params.get("fresh") == "1"),
                            headers={"Cache-Control": "no-store"})

    def search_page(request: Request) -> Response:
        query, docs, limit, question = asked_for(request)
        conn = connect(db_path)
        try:
            typed = [] if question else docs
            body = search_form(query, typed, focus=not (query or question))
            if query or question:
                body += question_nav(asked, question)
                report = explained(conn, query, docs, limit)
                gold_ids = {f["doc_id"] for f in report["follow"] if f["doc_id"]} if question else set()
                body += search_body(report, gold_ids, question)
            else:
                body += board_html(board_now())
            return shell(f"{query} · 搜尋測試" if query else "搜尋測試", body, db_info(conn), tab="search")
        finally:
            conn.close()

    def board_api(request: Request) -> Response:
        """The scoreboard as it stands (the page asks again until every question is graded)."""
        return JSONResponse(scores.snapshot(), headers={"Cache-Control": "no-store"})

    def search_api(request: Request) -> Response:
        query, docs, limit, _ = asked_for(request)
        if request.query_params.get("explain", "1") == "0":
            with busy:
                return JSONResponse(search_answer(db_path, query, search_fn=run_search, today=today_fn()))
        conn = connect(db_path)
        try:
            return JSONResponse(explained(conn, query, docs, limit))
        finally:
            conn.close()

    def trace_page(request: Request) -> Response:
        ref = request.query_params.get("ref", "").strip()
        conn = connect(db_path)
        try:
            body = trace_form(ref, focus=not ref)
            if not ref:
                body += examples_html(examples(conn, asked))
            elif resolve(conn, ref).doc_id is None and not as_address(ref):
                # words that are no document id and no address: the documents whose titles hold them
                body += found_html(ref, find_titles(conn, ref))
            else:
                body += trace_body(traced(conn, ref), viewer_url, today_fn())
            return shell(f"{ref} · 文件追蹤" if ref else "文件追蹤", body, db_info(conn), tab="trace")
        finally:
            conn.close()

    def trace_api(request: Request) -> Response:
        conn = connect(db_path)
        try:
            return JSONResponse(traced(conn, request.query_params.get("ref", "").strip()))
        finally:
            conn.close()

    def original(request: Request) -> Response:
        """One file of an export folder the library names, and nothing outside it."""
        folder, rel = request.query_params.get("folder", ""), request.query_params.get("path", "")
        conn = connect(db_path)
        try:
            known = set(site_exports(conn).values())
        finally:
            conn.close()
        if not folder or folder not in known or not rel:
            return PlainTextResponse("not found", status_code=404)
        base = (root / folder).resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            return PlainTextResponse("forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        data, suffix = target.read_bytes(), target.suffix.lower()
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if suffix in MARKUP:
            kind = "image/svg+xml" if suffix == ".svg" else "text/html"
            try:
                data.decode("utf-8")
                kind += "; charset=utf-8"
            except UnicodeDecodeError:
                pass  # not UTF-8: the browser reads the page's own declaration
            return Response(data, media_type=kind, headers={**headers, "Content-Security-Policy": ARCHIVED_CSP})
        if suffix in TYPES:
            return Response(data, media_type=TYPES[suffix], headers=headers)
        name = quote(target.name)
        return Response(data, media_type="application/octet-stream",
                        headers={**headers, "Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    def check_ref(request: Request) -> str:
        p = request.query_params
        return (p.get("url") or p.get("ref") or "").strip()

    # one read-only ledger connection for every check and archived image, and the last few checks kept a short while:
    # the panel and its frame (/check/raw) ask for the same check, which used to be made twice (the review of 11-16)
    ledgers = pagecheck.LedgerCache(archive) if archive is not None else None
    recent: dict[str, tuple[tuple, float, pagecheck.Check]] = {}
    recent_lock = threading.Lock()

    def page_checked(ref: str) -> pagecheck.Check:
        try:
            stamp = (ledgers.stamped() if ledgers is not None else None, db_path.stat().st_mtime_ns)
        except OSError:
            stamp = (None, None)
        with recent_lock:
            hit = recent.get(ref)
            if hit is not None and hit[0] == stamp and time.monotonic() - hit[1] < CHECK_SECONDS:
                return hit[2]
        library = connect(db_path)
        try:
            got = pagecheck.check(archive, ref, library=library, ledgers=ledgers)
        finally:
            library.close()
        with recent_lock:
            recent.pop(ref, None)
            recent[ref] = (stamp, time.monotonic(), got)
            while len(recent) > CHECK_KEEP:
                recent.pop(next(iter(recent)))
        return got

    # the sweep of every page (design section 六 4): made here, started by the first request of 網頁核查
    sweep = Sweep(archive, db_path) if archive is not None else None

    def swept() -> Sweep | None:
        """The sweep, started (or started again on a changed ledger) when 網頁核查 is asked for; None without a
        ledger to sweep."""
        if sweep is None or not (Path(archive) / pagecheck.LEDGER).exists():
            return None
        sweep.ensure()
        return sweep

    def check_panel(request: Request) -> Response:
        """網頁核查: for an address or a document id its check (design section 一), with its site's line (section 六
        3); for a site its page (section 六 2); else the entry: the sites, or what the words in the box find (section
        六 1)."""
        p = request.query_params
        ref, key = check_ref(request), p.get("site", "").strip()
        sw = swept()
        conn = connect(db_path)
        try:
            info = db_info(conn)
            if not key and sw is not None and (named := sw.site_key(ref)):
                key, ref = named, ""  # a site's key pasted back from its row: that site's page
            if asks_a_page(ref, conn):
                summary = page_checked(ref).summary
                crumb = None
                if sw is not None and summary.get("page"):
                    sw.prioritize(summary["page"]["site"])
                    crumb = sw.crumb(summary["page"]["site"], summary["page"]["url"])
                body = check_body(summary, value=ref, crumb=crumb, flash=p.get("n", ""))
                named = (crumb or {}).get("name") or summary.get("title") or ref  # the tab says which page, not its address
                return shell(f"{named} · 網頁核查", body, info, tab="check")
            if key and sw is not None:
                sw.prioritize(key)
                view = sw.site_view(key, q=p.get("q", "").strip(), only=p.get("only", ""))
                name = ((view or {}).get("site") or {}).get("name") or key
                return shell(f"{name} · 網頁核查", site_body(view, key), info, tab="check")
            view = sw.overview(p.get("sort", "")) if sw is not None else None
            found = sw.find(ref) if sw is not None and ref else None
            body = entry_body(view, found, value=ref, archive=sw is not None)
            return shell(f"{ref} · 網頁核查" if ref else "網頁核查", body, info, tab="check")
        finally:
            conn.close()

    def check_list(request: Request) -> Response:
        """The entry's list for what is typed in its box (its script asks as one types, and again while the sweep
        runs): {html, running}."""
        sw = swept()
        if sw is None:
            return JSONResponse({"html": "", "running": False}, headers={"Cache-Control": "no-store"})
        q = request.query_params.get("q", "").strip()
        view = sw.overview(request.query_params.get("sort", ""))
        return JSONResponse({"html": entry_list(view, sw.find(q) if q else None),
                             "running": bool(view["progress"]["running"])}, headers={"Cache-Control": "no-store"})

    def check_site(request: Request) -> Response:
        """A site's page's changing parts for its script: {top, chips, rows, running}."""
        p = request.query_params
        key, sw = p.get("site", "").strip(), swept()
        view = sw.site_view(key, q=p.get("q", "").strip(), only=p.get("only", "")) if sw is not None and key else None
        if view is None or not view.get("ready"):
            return JSONResponse({"top": "", "chips": "", "rows": "", "running": view is not None},
                                status_code=200 if view is not None else 404, headers={"Cache-Control": "no-store"})
        sw.prioritize(key)
        return JSONResponse(site_parts(view), headers={"Cache-Control": "no-store"})

    def check_item(request: Request) -> Response:
        """One row of a merged list opened in place (the drawer of the entry and the site page): {html}; 404 when no
        checked page has it any more."""
        p = request.query_params
        sw = swept()
        view = sw.item_view(p.get("site", "").strip(), p.get("key", "")) if sw is not None else None
        if view is None:
            return JSONResponse({"html": ""}, status_code=404, headers={"Cache-Control": "no-store"})
        return JSONResponse({"html": item_html(view)}, headers={"Cache-Control": "no-store"})

    def sweep_api(request: Request) -> Response:
        """How far the sweep has come (overall, and of ?site=), and the totals of the entry's line."""
        sw = swept()
        if sw is None:
            return JSONResponse({"phase": "no_archive"}, headers={"Cache-Control": "no-store"})
        out = sw.progress(request.query_params.get("site", "").strip())
        out["totals"] = sw.overview()["totals"]
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    def check_raw(request: Request) -> Response:
        """The archived page made safe (pagecheck.Marked.html), for the panel's frame."""
        got = page_checked(check_ref(request))
        page = got.html or empty_raw(got.summary.get("state_words", ""))
        return Response(page, media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": CHECK_RAW_CSP, "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

    def check_res(request: Request) -> Response:
        """An archived image's bytes, its type read from them; nothing else (404)."""
        found = pagecheck.archived_image(archive, request.query_params.get("u", ""), ledgers=ledgers)
        if found is None:
            return PlainTextResponse("not found", status_code=404, headers={"X-Content-Type-Options": "nosniff"})
        body, kind = found
        return Response(body, media_type=kind,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": CHECK_RES_CSP,
                                 "Cache-Control": "private, max-age=3600"})

    def check_api(request: Request) -> Response:
        swept()
        return JSONResponse(page_checked(check_ref(request)).summary)

    def check_go(request: Request) -> Response:
        """Where the live window goes for what the address box was given (design section 三): a document id or an
        address without its scheme resolved here, as /check resolves it, then sent on to the school (302); nothing to
        open: 204, the window stays where it is. The browser lets the box's submit steer the window to this address,
        never to an answer that comes later."""
        library = connect(db_path)
        try:
            ref = check_ref(request)
            if not asks_a_page(ref, library):  # words typed in the box find pages; they never move the school window
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            url, _doc = pagecheck.resolve_ref(ref, library)
        finally:
            library.close()
        target = pagecheck.live_url(pagecheck.press_label(url)[0])
        if not target:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return RedirectResponse(target, status_code=302,
                                headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

    def guarded(endpoint: Callable[[Request], Response], api: bool) -> Callable[[Request], Response]:
        """endpoint, whose failure is said in words (as JSON for the commands, as a page for the owner) and logged
        whole: one odd record never leaves the owner with a bare 500."""

        def run(request: Request) -> Response:
            try:
                return endpoint(request)
            except Exception as exc:
                log.exception("检视台出错：%s", request.url)
                words = f"{type(exc).__name__}: {exc}"
                if api:
                    return JSONResponse({"error": words}, status_code=500)
                body = (f"<section class='card pad'><span class='pill bad'>出錯了</span>　{e(words)}"
                        "<div class='soft small' style='margin-top:6px'>這是檢視台自己的錯，不是你給的東西有問題；"
                        "完整的經過在它的紀錄裡。</div></section>")
                return shell("出錯了", body, {}, status=500)

        return run

    routes = [Route("/", guarded(home, False), methods=["GET"]),
              Route("/search", guarded(search_page, False), methods=["GET"]),
              Route("/trace", guarded(trace_page, False), methods=["GET"]),
              Route("/api/search", guarded(search_api, True), methods=["GET"]),
              Route("/api/trace", guarded(trace_api, True), methods=["GET"]),
              Route("/api/board", guarded(board_api, True), methods=["GET"]),
              Route("/api/pipeline", guarded(pipeline_api, True), methods=["GET"]),
              Route("/pipeline/site", guarded(pipeline_site, False), methods=["GET"]),
              Route("/file", guarded(original, True), methods=["GET"]),
              Route("/check", guarded(check_panel, False), methods=["GET"]),
              Route("/check/raw", guarded(check_raw, True), methods=["GET"]),
              Route("/check/res", guarded(check_res, True), methods=["GET"]),
              Route("/check/go", guarded(check_go, True), methods=["GET"]),
              Route("/check/list", guarded(check_list, True), methods=["GET"]),
              Route("/check/site", guarded(check_site, True), methods=["GET"]),
              Route("/check/item", guarded(check_item, True), methods=["GET"]),
              Route("/api/check", guarded(check_api, True), methods=["GET"]),
              Route("/api/check/sweep", guarded(sweep_api, True), methods=["GET"])]

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        """The sweep's thread is stopped with the app, so the container stops cleanly."""
        try:
            yield
        finally:
            if sweep is not None:
                await run_in_threadpool(sweep.close)

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.sweep = sweep
    app.add_middleware(LocalOnly)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.ops.web", description="检视台：搜索测试和文件追踪（只读）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="内容资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="库里的相对路径从哪个资料夹算起（实验室电脑是 /work；默认是仓库的根）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="在哪个位址上听：默认 127.0.0.1；实验室的 Docker 容器里用 0.0.0.0，由 Docker 只从主机自己的 "
                         "127.0.0.1 转进来。不管听哪里，只回答用本机名字连进来的请求")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--viewer-url", default="", help="从你的浏览器看爬虫检视器的网址（文件追踪第一步会链过去）")
    ap.add_argument("--overview-url", default="", help="从你的浏览器看资料总览的网址（顶栏链过去）")
    ap.add_argument("--tables-url", default="", help="从你的浏览器看 Datasette 的网址（顶栏链过去）")
    ap.add_argument("--questions", type=Path, default=None, help="测试题的资料夹（research/eval_2026-11-01）")
    ap.add_argument("--gold", type=Path, default=None, help="标准答案的档（默认是测试题资料夹里的 gold_docs.json）")
    ap.add_argument("--archive", default="",
                    help="爬虫存档的资料夹（放 frontier.db 的那个；账本里的 WARC 路径从它算起，实验室是 /crawl/archive）；"
                         "不给就是网页核查那一页说没有挂上存档")
    ap.add_argument("--watch", action="append", default=[], metavar="名字=方式:网址",
                    help="流水线页要问「有没有回应」的服务（从这个程序连得到的网址），可以给好几次。方式：mcp（握手，"
                         "每小时真的搜一次）、datasette（看它开着的库是不是正式库）、overview（资料总览的 /stamp）、"
                         "http（只看有没有回应）")
    add_model_flags(ap)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}", file=sys.stderr)
        return 2
    watch = []
    for item in args.watch:
        name, _, rest = item.partition("=")
        how, _, url = rest.partition(":")
        if not name.strip() or how not in ("mcp", "datasette", "overview", "http") or not url.startswith("http"):
            print(f"--watch 要写成 名字=方式:网址（方式是 mcp、datasette、overview 或 http）：{item}", file=sys.stderr)
            return 2
        watch.append((name.strip(), url.strip(), how))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(args.db, args.root, viewer_url=args.viewer_url, questions=args.questions, gold=args.gold,
                     overview_url=args.overview_url, tables_url=args.tables_url,
                     config=config_from(args), archive=Path(args.archive) if args.archive else None,
                     watch=tuple(watch))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
