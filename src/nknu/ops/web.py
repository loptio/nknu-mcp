"""检视台 (plan 2026-11-15 T5; spec 2026-11-15 section 二): two pages for the owner, 搜尋測試 (why a search ranked
as it did: nknu.ops.explain) and 文件追蹤 (what one page or file became at each step: nknu.ops.trace; the two were
搜索试验台 and 逐份追踪 until the owner, 11-15: 「很多名字看着不直觉」), and the same two
as JSON for the commands (nknu.ops.cli's --at). The pages draw what the two modules return and compute nothing; how
they look is nknu.ops.look, search_page and trace_page (the third round of 11-15). The 33 test questions are there to
be tried by hand (picked on 搜尋's second tab: where its answer stands, and why); nothing measures them, or the
question bank, on its own (owner 2026-11-22 night: the bank checks the whole project, never the night).

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
网址或者文件编号呀？」): its entry (since 2026-11-17 文件's page, /doc) holds the school's numbers and misses and finds
sites and pages as one types, one site's misses and pages are a part of that site's page (/site; its script asks
/check/site), and every page of the ledger is checked in the background by nknu.ops.sweep (one thread; started by the
first request that needs it, stopped with the app), the site opened last first; /api/check/sweep says how far it has
come. /check/list, the old entry's list, still answers.

Four entries since the structure design of 2026-11-17 (docs/superpowers/specs/2026-11-17-panels-structure-design.md
section 二; owner: 「可以按你的建议走吧，不用问我了」), the bar's (nknu/ops/theme/bar.py) 每晚運行 | 網站 | 搜尋 | 找文件:
- 今晚 is the home, /: tonight's run, last night's changes and the pipeline's steps, and only the sites to look at
  (every site is on 網站, 資料總覽);
- one site is /site?site=… (nknu.ops.site_page), 網站's page in 檢視台: its steps, its last changes, its 網頁核查 and its
  documents. /pipeline/site?site= and /check?site= come here;
- 搜尋 is /try (nknu.ops.try_page): the service as an agent sees it, and on its second tab (?tab=why) 搜尋測試's
  explanation of the same question; with nothing asked, the scoreboard. /search comes here with what it was asked;
- 文件 is /doc (nknu.ops.doc_page): one box that finds documents and archived pages, and the school's 網頁核查. /trace
  and /check with nothing or with words come here; a document's trace (/trace?ref=<id or address>) and one page's
  check (/check?url=<address or id>) stay where they were.

    python -m nknu.ops.web [--db research/pipeline_2026-11-02/aca.db] [--root .] [--host 127.0.0.1] [--port 8878]
                           [--questions DIR] [--gold FILE]
                           [--archive /crawl/archive] [--no-dense] [--no-rerank]
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from nknu.core.timeutil import today_tpe
from nknu.ops import (
    content_band,
    history,
    keep,
    lineage,
    logs,
    night_view,
    nightly,
    pagecheck,
    panorama,
    raw,
    site_checks,
    standing,
    todo,
    units_page,
    visits,
)
from nknu.ops import overview as overview_mod
from nknu.ops.changes import Changes
from nknu.ops.check_page import check_body, check_form, empty_raw
from nknu.ops.check_sites import entry_list, item_html, site_parts
from nknu.ops.doc_page import DOCS_SHOWN, doc_body, empty_html, results_html, school_body, school_html
from nknu.ops.explain import explain_search
from nknu.ops.home_page import home_body
from nknu.ops.look import e, s2t, shell
from nknu.ops.lookup import as_address, columns, has_table, resolve
from nknu.ops.pipeline import Pipeline, Where, log_tail, site_detail
from nknu.ops.pipeline_page import changes_title
from nknu.ops.search_page import search_body, tests_html, why_head
from nknu.ops.site_page import Kinds, now_line
from nknu.ops.site_page import site_body as one_site_body
from nknu.ops.sweep import Sweep
from nknu.ops.theme import bar as theme
from nknu.ops.tonight_page import handle_rows, night_departures
from nknu.ops.trace import WITHIN, site_exports, trace
from nknu.ops.trace_page import FOUND_MAX, MARKUP, trace_body, trace_form
from nknu.ops.try_page import TryPage
from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import TOP_N
from nknu.retrieval.server import DEFAULT_DB, add_model_flags, aliases_of, config_from, connect, search_answer
from nknu.settings import REPO_ROOT
from nknu.totext.cache import sha256_file

log = logging.getLogger(__name__)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
DEFAULT_PORT = 8878
MAX_LIMIT = 50  # the most results a page or the API explains at once 【Claude】
# the real-AI test's verdicts ({right, partly, wrong, none, n, date, source}), beside it: 總覽's tile, put there by
# hand after a run (research/eval_2026-11-18/tools/strata_agent.py); none, no tile
AITEST_FILE = "aitest.json"
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


def doc_row(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    """One document as find_titles lists one, by its id (None when the library has no such document)."""
    have = columns(conn, "docs")
    joined = "site" in have and has_table(conn, "sites")
    site = "d.site" if "site" in have else "''"
    name = "s.name" if joined else "''"
    join = "LEFT JOIN sites s ON s.site = d.site" if joined else ""
    r = conn.execute(f"SELECT d.doc_id, d.title, d.kind_class, d.ident_date, {site}, {name} FROM docs d {join} "
                     "WHERE d.doc_id = ?", (doc_id,)).fetchone()
    return {"doc_id": r[0], "title": r[1], "kind": r[2], "date": r[3], "site": r[4], "name": r[5]} if r else None


def moved(path: str, params: list[tuple[str, str]], anchor: str = "") -> RedirectResponse:
    """A page that moved (structure design 2026-11-17 section 二): the same question at its new address, a temporary
    redirect (the old addresses keep working; nothing is cached)."""
    query = urlencode([(k, v) for k, v in params if v])
    return RedirectResponse(path + (f"?{query}" if query else "") + (f"#{anchor}" if anchor else ""), status_code=307,
                            headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- the app

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


DOC_HELP = ("可以打：&#10;標題裡的幾個字　繁體、簡體都可以&#10;網址　學校網頁或檔案的網址&#10;文件編號　庫裡每份文件自己的編號&#10;"
            "網站名　列出那個網站&#10;找到的分兩邊：庫裡的文件（點了看它的每一步）、抓到的網頁（點了看有沒有漏抓）")


def design_doc_help(body: str) -> str:
    """文件搜尋 with nothing typed: a ? beside its box says what it takes (the 11-23 design's doc layer styles it)."""
    return re.sub(r"(<form class='ask dc-ask'[^>]*>.*?)</form>",
                  lambda m: m.group(1) + "<button type='button' class='hp pd-hp' aria-label='可以打什麼' "
                                         f"data-tip='{DOC_HELP}'>?</button></form>", body, count=1, flags=re.DOTALL)


def _json(body: object, status: int = 200) -> JSONResponse:
    """A data answer the pages poll: never cached."""
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def create_app(db_path: Path, root: Path, *, viewer_url: str = "", questions: Path | None = None,
               gold: Path | None = None, config: HybridConfig | None = None, models: dict | None = None,
               today_fn: Callable[[], date] = today_tpe, code_root: Path | None = None,
               pipeline_src: Path | None = None, overview_url: str = "", tables_url: str = "",
               archive: Path | None = None, watch: tuple[tuple[str, str, str], ...] = (),
               mcp_url: str = "", ops_db: Path | None = None, record: bool = True,
               disks: tuple[tuple[str, str], ...] = (), public_log: Path | None = None) -> Starlette:
    """The app over one library file. root: the folder its relative paths start from; viewer_url: where the owner's
    browser reaches the archive viewer ('' : no link); questions and gold: no longer used (the 33 questions, gone
    2026-11-22; taken so an older compose still starts); config: the search's
    settings (None: the server's); models: embedder and reranker in place of the process's shared ones (tests);
    code_root and pipeline_src: the tree this code runs from (default: the running package's) and the pipeline's
    source (default: root / src), compared by install_state; overview_url and tables_url: where the owner's browser
    reaches 资料总览 and Datasette (流水線 links them; the top bar's addresses are nknu/ops/theme/panels.json); archive: the
    crawler's archive folder (frontier.db and its WARC files) 網頁核查 reads, None: the page says it is not mounted;
    watch: the services 流水線 asks whether they answer, (name, address from this process, how: 'mcp' or 'http');
    ops_db: the nights' record this service keeps (nknu.ops.history; None: not kept, the pages say so); record: whether
    its background thread runs (tests call app.state.recorder.tick themselves); public_log: the public service's query
    log, read only (None: 查詢服務 says nothing of its use)."""
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
    busy = threading.Lock()  # one search at a time: the two models are shared by every request

    def run_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]], **kw):
        return hybrid_search(conn, query, today=today, aliases=aliases, config=config, **models, **kw)

    def db_info(conn: sqlite3.Connection) -> dict:
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'schema_version')"))
        return {"built_at": meta.get("built_at"), "schema": meta.get("schema_version"), "install": installed()}

    # the 33 test questions with their gold documents, offered on 搜尋's second tab to try by hand (owner 2026-11-22
    # night: 「以后如果要的话再从dashboard那边手动测试那33题就好了」); nothing runs them on its own
    asked = load_questions(questions, gold)

    def asked_for(request: Request) -> tuple[str, list[str], int, dict | None]:
        """(the question, the documents to follow, the limit, the test question when one was picked)."""
        p = request.query_params
        question = {q["id"]: q for q in asked}.get(p.get("qid", ""))
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

    # 今晚, the home (流水線 until the structure design of 2026-11-17; owner 11-16: 「让我知道整个项目是否正常运作，出错的话是
    # 哪里错了 能帮助我精准定位」): every step read in the background (nknu.ops.pipeline)
    def sweep_overview() -> dict | None:
        """網頁核查's results as they stand, never starting its sweep (a restart's 17 minutes are its own to start)."""
        return sweep.overview() if sweep is not None else None

    where = Where(root=root, db=db_path, archive=archive, services=tuple(watch), disks=tuple(disks), public_log=public_log,
                  sweep=sweep_overview, today=today_fn)
    # what the background work found, kept beside the nights' record so a restart shows it at once (2026-11-22)
    found_before = keep.Kept(ops_db)
    # the problems the owner marked known on 總覽 (nknu.ops.todo.Known), in the same file
    known = todo.Known(ops_db)
    flow = Pipeline(where, kept=found_before)
    # where a step's links go by their short names: the scoreboard is on 搜尋, the school's 網頁核查 on 文件
    # the crawler's own board is no panel since 2026-11-21 (the panels became one; viewer_url is taken and not used)
    # the 33 test questions are tried by hand on 搜尋 (#tests), since 2026-11-22 night
    panels = {"overview": overview_mod.PATH, "board": "/try#tests", "check": "/check", "tables": tables_url}
    every_site = theme.entry_href("sites", "ops")  # 網站, the one list of every site (資料總覽)
    tables = (tables_url or theme.panels()["hosts"]["tables"]).rstrip("/")  # 資料庫, its queries of one site
    kinds = Kinds(where.library)  # each site's documents per kind, counted once per library file
    # 今晚 and 昨晚變了什麼 on top of the home (structure design 2026-11-17 三.2-三.4; nknu.ops.nightly, changes): the
    # night read on every request (small files), the two libraries compared in the background
    night_changes = Changes(where.library, root, kept=found_before)
    # 每晚紀錄 (panels v3 2026-11-17 一): the one file this service writes, never under /work or /crawl
    store = history.Store(ops_db, forbidden=(root, archive))
    recorder = history.Recorder(store, where, changes=night_changes, kinds=kinds, flow=flow)

    def tonight_now(request: Request) -> tuple[dict, dict]:
        changes = night_changes.snapshot()
        return nightly.tonight(where, night=request.query_params.get("night"), changes=changes), changes

    def tonight_night(night: dict) -> date:
        """The night 今晚 ends on: the one now is in (nightly.night_of)."""
        return nightly.night_of(datetime.fromisoformat(night["now"]))

    def labels_of(night: dict) -> dict[str, str]:
        return {x["night"]: x["label"] for x in night.get("nights") or [] if x.get("label")}

    def home(request: Request) -> Response:
        """每晚運行 (nknu.ops.home_page): the state now, the pipeline's nodes with a picked one's detail beside them,
        what is not normal, a row a night, the last swap's changes."""
        snap = flow.snapshot(fresh=request.query_params.get("fresh") == "1")
        night, changes = tonight_now(request)
        hist = history.home_history(store, tonight_night(night))
        # 問題 against the list kept for the night before (nknu.ops.standing): none kept yet, the page says from when
        base = store.problem_base(night["default"])
        note = ""
        if base is None and store.enabled:
            note = ("問題清單從這一晚開始記；下一晚跑完，這裡會分成新出現的、之前就有的、好了的"
                    if store.problems_kept(night["default"]) else "這一晚的問題清單還沒記下（這一晚跑完、各步驟讀過一次以後才記）")
        body = home_body(snap, night, changes, hist, panels, every_site, base=base, base_note=note)
        conn = connect(db_path)
        try:
            return shell("每晚運行 · 檢視台", body, db_info(conn), tab="tonight")
        finally:
            conn.close()

    def kept(name: str) -> object:
        """A file kept beside the nights' record (AITEST_FILE) as data; None without it."""
        if ops_db is None:
            return None
        try:
            return json.loads(ops_db.with_name(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def todo_now(snap: dict, night: dict, changes: dict | None) -> dict:
        """要處理 (nknu.ops.todo): the problems of the night shown against the list kept for the night before, this
        machine past its lines, less what the owner said is known."""
        reading = not snap.get("steps")
        rows = todo.night_rows(night, snap, [] if reading else handle_rows(snap, changes), where.archive)
        parts = standing.split(rows, store.problem_base(night["default"]), reading=reading,
                               swap_known=(changes or {}).get("state") != "computing")
        return todo.collect(parts, rows, panorama.machine_meters(snap), known.load(), night=night["default"])

    async def mark_known(request: Request) -> Response:
        """要處理's row marked known (or taken off with off=1), from 總覽's own form only: the request's Origin must be
        this panel (LocalOnly checks the Host; a page of another site cannot post here). Back to 要處理."""
        origin = request.headers.get("origin") or ""
        if not origin or urlsplit(origin).netloc != (request.headers.get("host") or ""):
            return PlainTextResponse("forbidden", status_code=403)
        form = await request.form()
        keys = [k for k in str(form.get("keys") or "").split() if k][:50]
        if keys:
            if form.get("off") == "1":
                await run_in_threadpool(known.unmark, keys)
            else:
                await run_in_threadpool(known.mark, keys, str(form.get("note") or "").strip())
        return RedirectResponse("/#known" if form.get("off") == "1" else "/#todo", status_code=303)

    def front(request: Request) -> Response:
        """總覽 (nknu.ops.panorama), the front page since 2026-11-22: the whole project on one page, mostly charts.
        每晚運行 was here until then: an old link to a night (?night=) or to read again (?fresh=) is sent on to it."""
        if {"night", "fresh"} & set(request.query_params):
            return night_moved(request)
        sites_state.start()
        with sites_state.lock:
            data = sites_state.data
        night, changes = tonight_now(request)
        hist = history.home_history(store, tonight_night(night))
        conn = connect(db_path)
        try:
            built = db_info(conn).get("built_at") or ""
        finally:
            conn.close()
        snap = flow.snapshot()
        tonight = {**night, "changes": changes}
        todo_res = todo_now(snap, night, changes)
        page = panorama.page(snap, tonight, data or {}, hist, built=built, todo=todo_res,
                             more=panorama.gather(store, known, where, tonight, snap, todo_res))
        return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    def night_moved(request: Request) -> Response:
        """每晚運行 is 內容's night view since 11-23 (/sites?date=<run day>): an old link to a night (?night=, the
        night's key) goes to that night, anything else to the default night."""
        n = request.query_params.get("night", "").strip()
        try:
            target = nightly.night_href(n) if n else f"{nightly.NIGHT_VIEW}?date="
        except (ValueError, OverflowError):  # not a date, or one past the calendar's end
            target = f"{nightly.NIGHT_VIEW}?date="
        return RedirectResponse(target, status_code=307, headers={"Cache-Control": "no-store"})

    def history_api(request: Request) -> Response:
        """The nights' record as data: the fourteen nights 今晚 draws, or one site's (?site=)."""
        night, _changes = tonight_now(request)
        today = tonight_night(night)
        site = request.query_params.get("site", "").strip()
        got = history.site_history(store, site, today) if site else history.home_history(store, today)
        return JSONResponse({**got, "recorder": recorder.last, "file": str(store.path or "")},
                            headers={"Cache-Control": "no-store"})

    def tonight_api(request: Request) -> Response:
        """今晚 as data: the night shown (?night=YYYY-MM-DD, else the default one), the last seven nights, the first
        sentence, the crawler's own counts, and the last swap's comparison (its lists capped: changes.LIST_CAP)."""
        night, changes = tonight_now(request)
        if request.query_params.get("brief") == "1":  # the page's own poll: whether to reload, not the lists
            changes = {k: changes[k] for k in ("state", "words", "keys", "night", "totals") if k in changes}
        return JSONResponse({**night, "changes": changes}, headers={"Cache-Control": "no-store"})

    def site_view(request: Request) -> Response:
        """One site on one page (nknu.ops.site_page): its steps, its last changes, its 網頁核查 and its documents.
        Without a site, 網站 (every site is listed there)."""
        p = request.query_params
        key = p.get("site", "").strip()
        if not key:
            return RedirectResponse(every_site, status_code=307, headers={"Cache-Control": "no-store"})
        detail = site_detail(where, key)
        snap = flow.snapshot()
        row = next((r for r in snap.get("sites") or [] if r.get("key") == key), None)
        night, changes = tonight_now(request)
        mine = next((x for x in changes.get("sites") or [] if x.get("site") == key), None)
        sw = swept()
        view = None
        if sw is not None:
            sw.prioritize(key)
            view = sw.site_view(key, q=p.get("q", "").strip(), only=p.get("only", ""))
        conn = connect(db_path)
        try:
            info = db_info(conn)
            # 品質提醒 (site_checks.py): the build's lists of documents behind each reminder, while they are this library's
            try:  # a list that cannot be read leaves the rest of the site's page standing
                quality = site_checks.card(key, site_checks.load(db_path.parent, info.get("built_at") or ""), conn)
            except Exception:
                log.exception("網站頁：品質提醒的清單讀不了（%s）", key)
                quality = site_checks.card(key, None, conn, broken=True)
        finally:
            conn.close()
        if detail is None and row is None and view is None and mine is None:
            body = (f"<section class='card pad'><span class='pill none'>沒有這個網站</span>　正式庫、流水線和爬蟲帳本裡都沒有"
                    f"「{e(key)}」。 <a href='{e(every_site)}'>全部網站</a></section>")
            return shell("沒有這個網站 · 檢視台", body, info, status=404, tab="sites")
        counted = kinds.get()
        lib = (detail or {}).get("site") or {}
        named = ((view or {}).get("site") or {})
        name = lib.get("name") or (row or {}).get("name") or named.get("name") or (mine or {}).get("name") or key
        reasons = changes.get("reasons") or {}
        body = one_site_body(
            key, name=name, unit=lib.get("unit") or "", home=named.get("home") or "",
            now=now_line(row, mine, reasons, read=bool(snap.get("steps"))),
            detail=detail, changes=changes, changes_title=changes_title(changes, night), view=view,
            archive=sw is not None, kinds=None if counted is None else counted.get(key, {}),
            tables=tables, row=row,
            hist=history.site_history(store, key, tonight_night(night)), labels=labels_of(night),
            departures=night_departures(night), read=bool(snap.get("steps")))
        return shell(f"{name} · 檢視台", site_checks.lay_out(body, quality), info, tab="sites", layer="site")

    def pipeline_site(request: Request) -> Response:
        """Moved: one site's page is /site (structure design 2026-11-17 section 二)."""
        return moved("/site", [("site", request.query_params.get("site", "").strip())])

    def pipeline_api(request: Request) -> Response:
        return JSONResponse(flow.snapshot(fresh=request.query_params.get("fresh") == "1"),
                            headers={"Cache-Control": "no-store"})

    def search_page(request: Request) -> Response:
        """Moved: 搜尋測試 is 搜尋's second tab (/try?tab=why…), with what it was asked; asked nothing, 搜尋 itself (its
        scoreboard)."""
        p = request.query_params
        kept = [(k, v) for k in ("qid", "q", "doc", "limit") for v in p.getlist(k)]
        if not any(v.strip() for k, v in kept if k in ("qid", "q")):
            return moved("/try", [], "tests")
        return moved("/try", [("tab", "why"), *kept], "result")

    def why_of(request: Request, conn: sqlite3.Connection) -> tuple[str, str] | None:
        """搜尋's second tab, when asked for (?tab=why with a question or a test question): (the question, search_page's
        explanation of it on the library, under the test questions' picker and the box for documents to follow)."""
        if request.query_params.get("tab") != "why":
            return None
        query, docs, limit, question = asked_for(request)
        if not (query or question):
            return None
        report = explained(conn, query, docs, limit)
        gold_ids = {f["doc_id"] for f in report["follow"] if f["doc_id"]} if question else set()
        return query, why_head(asked, question, query, docs) + search_body(report, gold_ids, question)

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
        """One document's trace (a document id or an address); with nothing or with words, 文件 (/doc finds them)."""
        ref = request.query_params.get("ref", "").strip()
        conn = connect(db_path)
        try:
            if not ref or (resolve(conn, ref).doc_id is None and not as_address(ref)):
                return moved("/doc", [("q", ref)])
            body = trace_form(ref, focus=False) + trace_body(traced(conn, ref), viewer_url, today_fn(),
                                                             archive=archive is not None)
            return shell(f"{ref} · 文件搜尋", body, db_info(conn), tab="doc", layer="trace")
        finally:
            conn.close()

    def doc_found(conn: sqlite3.Connection, q: str) -> str:
        """What 文件's box finds for q (doc_page.results_html): the document q names and the ones whose titles hold it,
        and the archive's sites and pages."""
        docs: list[dict] = []
        named = resolve(conn, q).doc_id
        if named is not None and (row := doc_row(conn, named)) is not None:
            docs.append(row)
        docs += [d for d in find_titles(conn, q, DOCS_SHOWN) if d["doc_id"] != named]
        sw = swept()
        found = sw.find(q) if sw is not None else None
        page_ref = q if asks_a_page(q, conn) else ""
        return results_html(q, docs, found, page_ref=page_ref, archive=sw is not None)

    def doc_view(request: Request) -> Response:
        """文件搜尋: the box, and under it what it found, or (nothing typed) what it takes. Opened with nothing typed it
        does not touch the sweep (全校網頁核查 is /check)."""
        q = request.query_params.get("q", "").strip()
        conn = connect(db_path)
        try:
            # with nothing typed, what the box takes is its ? (11-23 design: the 「可以打什麼」 card went)
            body = doc_body(q, doc_found(conn, q)) if q else design_doc_help(doc_body(q, ""))
            return shell(f"{q} · 文件搜尋" if q else "文件搜尋 · 檢視台", body, db_info(conn), tab="doc",
                         layer="found" if q else "doc")
        finally:
            conn.close()

    def doc_find(request: Request) -> Response:
        """The list under 文件搜尋's box for what is typed (its script asks as one types, and again while the sweep
        runs): {html}; nothing typed: what the box takes. ?school=1: 全校網頁核查's changing part, for its own page."""
        q = request.query_params.get("q", "").strip()
        if request.query_params.get("school") == "1":
            sw = swept()
            html = school_html(sw.overview() if sw is not None else None, archive=sw is not None)
            return JSONResponse({"html": html}, headers={"Cache-Control": "no-store"})
        conn = connect(db_path)
        try:
            html = doc_found(conn, q) if q else empty_html()
        finally:
            conn.close()
        return JSONResponse({"html": html}, headers={"Cache-Control": "no-store"})

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

    def stored(request: Request) -> dict | None:
        """The stored answer a request names: ?id= the ledger's number (u<id> or bare), else an address."""
        ref = request.query_params.get("id", "").strip() or check_ref(request)
        return pagecheck.archived_record(archive, ref, ledgers=ledgers)

    def stored_page(request: Request, what: str) -> Response:
        """A stored page as its source or its visible text (the crawler's viewer /source and /text, until the panels
        became one, 2026-11-21), under the page's address and a way back to its check."""
        rec = stored(request)
        conn = connect(db_path)
        try:
            info = db_info(conn)
        finally:
            conn.close()
        if rec is None or not pagecheck.is_markup(rec["ctype"], rec["body"]):
            why = "存檔裡沒有這一頁的內容" if rec is None else "存的不是網頁，沒有原始碼或文字可以看"
            return shell(f"{what} · 網頁核查", f"<section class='card pad'><span class='pill none'>{e(why)}</span></section>",
                         info, tab="doc", status=404)
        text = pagecheck.decode(rec["body"], rec["ctype"])
        shown = text if what == "原始 HTML" else pagecheck.visible_text(text)
        back = "/check?" + urlencode({"url": rec["url"]})
        other = ("/check/text", "整頁文字") if what == "原始 HTML" else ("/check/source", "原始 HTML")
        head = (f"<section class='card pad stored-h'><b>{e(what)}</b><span class='url mono'>{e(unquote(rec['url']))}</span>"
                f"<span class='soft small'>{len(shown):,} 個字元</span><a class='btn' href='{e(back)}'>核查這一頁</a>"
                f"<a class='btn' href='{e(other[0])}?{urlencode({'url': rec['url']})}'>{other[1]}</a></section>")
        return shell(f"{rec.get('title') or unquote(rec['url'])} · {what}",
                     f"{head}<pre class='card pad stored'>{e(shown)}</pre>", info, tab="doc")

    def check_source(request: Request) -> Response:
        return stored_page(request, "原始 HTML")

    def check_text(request: Request) -> Response:
        return stored_page(request, "整頁文字")

    def check_file(request: Request) -> Response:
        """A stored answer's own bytes (the crawler's viewer /raw until 2026-11-21): a page or an SVG under the
        strictest policy (nothing runs, nothing loads), a PDF, a picture or plain text as the browser shows them (a
        picture and a PDF only when their bytes are what the type says), anything else as a download."""
        rec = stored(request)
        if rec is None:
            return PlainTextResponse("not found", status_code=404, headers={"X-Content-Type-Options": "nosniff"})
        body, ctype = rec["body"], rec["ctype"]
        kind = ctype.split(";", 1)[0].strip().lower()
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if pagecheck.is_markup(ctype, body) or kind == "image/svg+xml":
            return Response(body, media_type=ctype or "text/html", headers={**headers, "Content-Security-Policy": ARCHIVED_CSP})
        if image := pagecheck.image_type(body):
            return Response(body, media_type=image, headers={**headers, "Content-Security-Policy": CHECK_RES_CSP})
        if kind == "application/pdf" and body[:5] == b"%PDF-":
            return Response(body, media_type="application/pdf", headers=headers)
        if kind == "text/plain":
            return Response(body, media_type=ctype, headers={**headers, "Content-Security-Policy": CHECK_RES_CSP})
        name = quote(unquote(urlsplit(rec["url"]).path.rsplit("/", 1)[-1]) or f"u{rec['id']}")
        return Response(body, media_type="application/octet-stream",
                        headers={**headers, "Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

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
    sweep = Sweep(archive, db_path, ops_db=store.path, hold=lambda: nightly_fetching()) if archive is not None else None

    def nightly_fetching() -> bool:
        """Whether the nightly update is in its crawl or export part now (nknu.ops.nightly: small files, read on
        every request): 網頁核查's sweep is not started then (Sweep.ensure's hold)."""
        try:
            night = nightly.tonight(where)
        except Exception:  # the night cannot be read: the sweep behaves as it did
            log.exception("读不了今晚的纪录，网页核查照常")
            return False
        cells = next((x.get("cells") or [] for x in night.get("nights") or [] if x.get("night") == night.get("default")), [])
        return any(c.get("key") in ("crawl", "export") and c.get("state") == nightly.RUN for c in cells)

    def swept() -> Sweep | None:
        """The sweep, started (or started again on a changed ledger) when 網頁核查 is asked for; None without a
        ledger to sweep."""
        if sweep is None or not (Path(archive) / pagecheck.LEDGER).exists():
            return None
        sweep.ensure(hold=nightly_fetching())
        return sweep

    def check_panel(request: Request) -> Response:
        """網頁核查 of one page, for an address or a document id (design section 一), with its site's line (section 六
        3). A site (?site=, or its key pasted back) is its page /site, words are 文件搜尋 (/doc), and nothing asked is
        the school's 網頁核查 (doc_page.school_body)."""
        p = request.query_params
        if number := p.get("id", "").strip():  # the ledger's number of an address (an export row's 存档编号, u<id>)
            address = pagecheck.address_of(archive, number, ledgers=ledgers)
            if address:
                return moved("/check", [("url", address)])
        ref, key = check_ref(request), p.get("site", "").strip()
        sw = swept()
        conn = connect(db_path)
        try:
            if not key and sw is not None and (named := sw.site_key(ref)):
                key = named  # a site's key pasted back from its row: that site's page
            if key and not p.get("lists"):
                return moved("/site", [("site", key), ("q", p.get("q", "").strip()), ("only", p.get("only", ""))],
                             "check")
            if not ref:  # nothing asked: the school's 網頁核查 (2026-11-18; until then it sat under 文件搜尋's box);
                # with ?site=…&lists=1 the design layer puts that site's lists in place of the school's (11-23)
                view = sw.overview() if sw is not None else None
                body = school_body(check_form("", "", live=False),
                                   school_html(view, archive=sw is not None))
                return shell("全校網頁核查 · 檢視台", body, db_info(conn), tab="check", layer="check")
            if not asks_a_page(ref, conn):
                return moved("/doc", [("q", ref)])
            info = db_info(conn)
            summary = page_checked(ref).summary
            crumb = None
            if sw is not None and summary.get("page"):
                sw.prioritize(summary["page"]["site"])
                crumb = sw.crumb(summary["page"]["site"], summary["page"]["url"])
            body = check_body(summary, value=ref, crumb=crumb, flash=p.get("n", ""))
            named = (crumb or {}).get("name") or summary.get("title") or ref  # the tab says which page, not its address
            return shell(f"{named} · 網頁核查", body, info, tab="doc", layer="pagecheck")
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

    # 網站 (資料總覽, a service of its own until 2026-11-21: nknu.ops.overview): its thread computes, a request only
    # hands out what it last made
    sites_state = overview_mod.State(root, kept=found_before)

    def sites_page(request: Request) -> Response:
        """內容: the page of every site as computed for this library (overview_mod.State), under the band that is
        drawn now (content_band: what the crawler missed, what holds no document)."""
        sites_state.start()
        with sites_state.lock:
            page, data = sites_state.page, sites_state.data
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if page is None:
            return Response(overview_mod.WAITING, media_type="text/html; charset=utf-8", headers=headers)
        text = page.decode("utf-8")
        if request.query_params.get("embed") == "1":  # 總覽's frame: the date chart and 全部網站, with this machine
            text = overview_mod.fill(text, embed_side=panorama.machine_block(flow.snapshot()))
        else:  # 內容, and its night view (?date=): the band, the night's band and log (night_view, night.js)
            night, _changes = tonight_now(request)
            text = overview_mod.fill(text, band=content_band.band_for(where, data or {}, flow.snapshot(), night),
                                     night_band=night_view.band_html(), night_log=night_view.log_html())
        html = text.encode("utf-8")  # the kept page already carries its layers (overview.render): never twice
        if "gzip" in (request.headers.get("accept-encoding") or ""):  # the page carries its data: about 2 MB
            return Response(gzip.compress(html, 5), media_type="text/html; charset=utf-8",
                            headers={**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)

    def sites_stamp(_request: Request) -> Response:
        sites_state.start()
        return JSONResponse(sites_state.said(), headers={"Cache-Control": "no-store"})

    def sites_data(_request: Request) -> Response:
        with sites_state.lock:
            data = sites_state.data
        if data is None:
            return JSONResponse({"error": "還在統計"}, status_code=404)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    def log_api(request: Request) -> Response:
        """The last lines of one of the logs the steps write (pipeline.LOG_FILES, by its short name: never a path)."""
        p = request.query_params
        n = p.get("n", "")
        got = log_tail(where, p.get("name", ""), int(n) if n.isascii() and n.isdigit() else 50)
        if got is None:
            return JSONResponse({"error": "沒有這份紀錄"}, status_code=404, headers={"Cache-Control": "no-store"})
        return JSONResponse(got, headers={"Cache-Control": "no-store"})

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

    routes = [Route("/", guarded(front, False), methods=["GET"]),
              Route("/todo/known", mark_known, methods=["POST"]),
              Route(nightly.TONIGHT, night_moved, methods=["GET"]),  # 定時更新 is 內容 at ?date= (11-23)
              Route("/site", guarded(site_view, False), methods=["GET"]),
              Route(overview_mod.PATH, guarded(sites_page, False), methods=["GET"]),
              Route(f"{overview_mod.PATH}/stamp", guarded(sites_stamp, True), methods=["GET"]),
              Route(f"{overview_mod.PATH}/data.json", guarded(sites_data, True), methods=["GET"]),
              Route("/doc", guarded(doc_view, False), methods=["GET"]),
              Route("/doc/find", guarded(doc_find, True), methods=["GET"]),
              Route("/search", guarded(search_page, False), methods=["GET"]),
              Route("/trace", guarded(trace_page, False), methods=["GET"]),
              Route("/api/search", guarded(search_api, True), methods=["GET"]),
              Route("/api/trace", guarded(trace_api, True), methods=["GET"]),
              Route("/api/pipeline", guarded(pipeline_api, True), methods=["GET"]),
              Route("/api/tonight", guarded(tonight_api, True), methods=["GET"]),
              Route("/api/history", guarded(history_api, True), methods=["GET"]),
              Route("/api/log", guarded(log_api, True), methods=["GET"]),
              Route("/pipeline/site", guarded(pipeline_site, False), methods=["GET"]),
              Route("/file", guarded(original, True), methods=["GET"]),
              Route("/check", guarded(check_panel, False), methods=["GET"]),
              Route("/units", units_page.moved, methods=["GET"]),  # 單位 went into 內容 (11-23)
              Route("/check/raw", guarded(check_raw, True), methods=["GET"]),
              Route("/check/source", guarded(check_source, False), methods=["GET"]),
              Route("/check/text", guarded(check_text, False), methods=["GET"]),
              Route("/check/file", guarded(check_file, True), methods=["GET"]),
              Route("/check/res", guarded(check_res, True), methods=["GET"]),
              Route("/check/go", guarded(check_go, True), methods=["GET"]),
              Route("/check/list", guarded(check_list, True), methods=["GET"]),
              Route("/check/site", guarded(check_site, True), methods=["GET"]),
              Route("/check/item", guarded(check_item, True), methods=["GET"]),
              Route("/api/check", guarded(check_api, True), methods=["GET"]),
              Route("/api/check/sweep", guarded(sweep_api, True), methods=["GET"]),
              # the 11-23 design's data, read live (round 2: sources, logs, history, visits, raw, lineage, night_view)
              Route("/api/logs", guarded(lambda r: _json(logs.api_window(where, r.query_params)), True),
                    methods=["GET"]),
              Route("/api/usage/days", guarded(lambda r: _json(history.usage_api(store, where, r.query_params)), True),
                    methods=["GET"]),
              Route("/api/usage/live", guarded(lambda r: _json(visits.live_api(where, r.query_params)), True),
                    methods=["GET"]),
              Route("/api/raw", guarded(lambda r: _json(*reversed(raw.api(where, r.query_params,
                                                                         changes=night_changes.snapshot()))), True),
                    methods=["GET"]),
              Route("/api/file", guarded(lambda r: _json(*reversed(lineage.api(where, r.query_params))), True),
                    methods=["GET"]),
              Route("/api/night", guarded(lambda r: _json(night_view.api(where, store, r.query_params, flow=flow,
                                                                          changes=night_changes)), True),
                    methods=["GET"]),
              Route("/api/night/ticks", guarded(lambda r: _json(night_view.ticks(where, store, r.query_params)), True),
                    methods=["GET"])]

    # 搜尋 (nknu.ops.try_page; structure design 2026-11-17 section 三.1): the search service as a student's AI agent
    # sees it, called from here through mcp_url ('' : the page says it is not set)
    trying = TryPage(mcp_url)

    def try_view(request: Request) -> Response:
        """搜尋: the service called as an agent calls it; its second tab, 搜尋測試's explanation (why_of); with nothing
        asked, the 33 test questions to try by hand under the forms."""
        conn = connect(db_path)
        try:
            why = why_of(request, conn)
            asked_something = why is not None or bool(request.query_params.get("call"))
            # the 33 test questions to try by hand (2026-11-22 night: the question bank checks the whole project, never
            # the night; its scores are no longer shown here while its answers are being cleaned)
            board = "" if asked_something else tests_html(asked)
            title, body = trying.page(request.query_params, why=why, board=board)
            return shell(title, body, db_info(conn), tab="try", layer="why" if why is not None else "try")
        finally:
            conn.close()

    routes.append(Route("/try", guarded(try_view, False), methods=["GET"]))

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        """The nights' recorder starts with the app; it and the sweep's thread are stopped with it, so the container
        stops cleanly."""
        if record:
            recorder.start()
            sites_state.start()  # in a test the page's first request starts it
        try:
            yield
        finally:
            sites_state.close()
            await run_in_threadpool(recorder.close)
            if sweep is not None:
                await run_in_threadpool(sweep.close)

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.sweep = sweep
    app.state.kinds = kinds
    app.state.store, app.state.recorder = store, recorder
    app.state.sites = sites_state
    app.add_middleware(LocalOnly)
    return app


def mcp_url_of(given: str, watch: list[tuple[str, str, str]] | tuple = ()) -> str:
    """The service 搜尋 calls: --mcp-url, else the first --watch entry watched as 'mcp', else '' (not set)."""
    return given.strip() or next((url for _name, url, how in watch if how == "mcp"), "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.ops.web", description="检视台：搜索测试和文件追踪（只读）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="内容资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="库里的相对路径从哪个资料夹算起（实验室电脑是 /work；默认是仓库的根）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="在哪个位址上听：默认 127.0.0.1；实验室的 Docker 容器里用 0.0.0.0，由 Docker 只从主机自己的 "
                         "127.0.0.1 转进来。不管听哪里，只回答用本机名字连进来的请求")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--viewer-url", default="", help="不用了：爬虫看板 11-21 起不再是面板（看原样在 /check）；留着只为旧的 compose 还能开")
    ap.add_argument("--overview-url", default="", help="不用了：资料总览 11-21 并进检视台（/sites）；留着只为旧的 compose 还能开")
    ap.add_argument("--tables-url", default="", help="从你的浏览器看 Datasette 的网址（顶栏链过去）")
    ap.add_argument("--questions", type=Path, default=None, help="不用了：33 题 11-22 拿掉；留着只为旧的 compose 还能开")
    ap.add_argument("--gold", type=Path, default=None, help="不用了：33 题 11-22 拿掉；留着只为旧的 compose 还能开")
    ap.add_argument("--archive", default="",
                    help="爬虫存档的资料夹（放 frontier.db 的那个；账本里的 WARC 路径从它算起，实验室是 /crawl/archive）；"
                         "不给就是网页核查那一页说没有挂上存档")
    ap.add_argument("--watch", action="append", default=[], metavar="名字=方式:网址",
                    help="流水线页要问「有没有回应」的服务（从这个程序连得到的网址），可以给好几次。方式：mcp（握手，"
                         "每小时真的搜一次）、datasette（看它开着的库是不是正式库）、overview（资料总览的 /stamp）、"
                         "public（对外服务的 /health：活着没有、库多旧；不搜，免得进它的查询纪录）、http（只看有没有回应）")
    ap.add_argument("--ops-db", default="",
                    help="每晚纪录的档（检视台自己写的小资料库；实验室是 /ops/ops.db，磁碟区 nknu-ops）；不给或写不进去时，"
                         "页面照常、只说没有在记")
    ap.add_argument("--mcp-url", default="",
                    help="「測試」那一页调用的搜索服务（MCP）网址，从这个程序连得到的（实验室是 http://mcp:8766/mcp）；"
                         "不给就用 --watch 里方式是 mcp 的那一个，都没有就是那一页说没有设定")
    ap.add_argument("--disk", action="append", default=[], metavar="名字=资料夹",
                    help="首页要说剩余空间的硬盘，给它上面的一个资料夹（从这个程序看得到的），可以给好几次；"
                         "实验室：E 槽=/crawl/archive/warc（工作资料都在 E 槽）")
    ap.add_argument("--public-log", default="",
                    help="对外服务的查询纪录 queries.jsonl（只读挂进来的），首页「查詢服務」说它最近 24 小时的使用；不给就不说")
    add_model_flags(ap)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}", file=sys.stderr)
        return 2
    watch = []
    for item in args.watch:
        name, _, rest = item.partition("=")
        how, _, url = rest.partition(":")
        if not name.strip() or how not in ("mcp", "datasette", "overview", "public", "http") or not url.startswith("http"):
            print(f"--watch 要写成 名字=方式:网址（方式是 mcp、datasette、overview、public 或 http）：{item}", file=sys.stderr)
            return 2
        watch.append((name.strip(), url.strip(), how))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(args.db, args.root, viewer_url=args.viewer_url, questions=args.questions, gold=args.gold,
                     overview_url=args.overview_url, tables_url=args.tables_url,
                     config=config_from(args), archive=Path(args.archive) if args.archive else None,
                     watch=tuple(watch), mcp_url=mcp_url_of(args.mcp_url, watch),
                     ops_db=Path(args.ops_db) if args.ops_db else None,
                     disks=tuple((name.strip(), path.strip()) for name, _, path in (d.partition("=") for d in args.disk)
                                 if name.strip() and path.strip()),
                     public_log=Path(args.public_log) if args.public_log else None)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
