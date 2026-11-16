"""A local viewer of the archive, for the owner to check what was crawled: the crawl's progress dashboard (crawler
dashboard v4: a card per server, each server's sites, the problem log), sites by unit, every URL with its status,
each page as extracted text, as HTML source, or as a preview that loads nothing from the network, and attachments
(PDF…) inline. Listens on 127.0.0.1 unless told another address (the lab's Docker container listens on 0.0.0.0, and
Docker forwards only the host's own 127.0.0.1 to it); wherever it listens, a page is answered only to a request that
names this machine (the Host check). Reads frontier.db read-only and the WARC files through an offset index
(<dir>/viewer-index.db, built incrementally: a file still being written is indexed up to its last complete record).
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser
from warcio.archiveiterator import ArchiveIterator

from nknu.archive import outcome, progress

log = logging.getLogger("nknu.archive.viewer")
INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS rec(url TEXT NOT NULL, warc TEXT NOT NULL, offset INTEGER NOT NULL,
  PRIMARY KEY(warc, offset)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS rec_url ON rec(url);
CREATE TABLE IF NOT EXISTS indexed(warc TEXT PRIMARY KEY, upto INTEGER NOT NULL);
"""
PREVIEW_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; media-src 'none'; "
               "frame-src 'none'; form-action 'none'; sandbox")
MAX_ROWS = 3000
STATE_ZH = {"done": "已抓", "pending": "待抓", "active": "抓取中", "failed": "失败", "skipped": "跳过"}


_INDEX_LOCK = threading.Lock()  # one build_index at a time (the overview's and `archive browse`'s background one)


def build_index(root: Path) -> int:
    """Index the response records of every WARC file under <root>/warc, from where the last run stopped. Returns the
    number of records added. One at a time in this process."""
    with _INDEX_LOCK:
        return _build_index(root)


def index_in_background(root: Path, done: Callable[[int], None] | None = None) -> threading.Thread:
    """build_index on a daemon thread (design §14.1: `archive browse` starts serving, the dashboard first, at once,
    and never waits for the index, which the dashboard does not read); done(records added) when it ends."""

    def work() -> None:
        try:
            added = build_index(root)
        except Exception:  # the overview builds it again when opened
            log.exception("building the archive index failed")
            return
        if done is not None:
            done(added)

    t = threading.Thread(target=work, name="viewer-index", daemon=True)
    t.start()
    return t


def _build_index(root: Path) -> int:
    idx = sqlite3.connect(root / "viewer-index.db")
    idx.executescript(INDEX_SCHEMA)
    done = dict(idx.execute("SELECT warc, upto FROM indexed"))
    added = 0
    for path in sorted((root / "warc").rglob("*.warc.gz")):
        rel = str(path.relative_to(root))
        start = done.get(rel, 0)
        if path.stat().st_size <= start:
            continue
        upto = start
        with open(path, "rb") as fh:
            fh.seek(start)
            it = ArchiveIterator(fh)
            try:
                for rec in it:
                    offset = it.get_record_offset()  # warcio counts from the file's start, not from the seek
                    if rec.rec_type == "response":
                        url = rec.rec_headers.get_header("WARC-Target-URI") or ""
                        idx.execute("INSERT OR IGNORE INTO rec(url, warc, offset) VALUES (?,?,?)", (url, rel, offset))
                        added += 1
                    rec.content_stream().read()
                    upto = it.get_record_offset() + it.get_record_length()
            except Exception as exc:  # noqa: BLE001 - a record still being written: index up to the last complete one
                log.debug("%s: indexed up to %d (%s)", rel, upto, type(exc).__name__)
        idx.execute("INSERT OR REPLACE INTO indexed(warc, upto) VALUES (?,?)", (rel, upto))
        idx.commit()
    idx.close()
    return added


def read_record(root: Path, warc: str, offset: int) -> tuple[int, dict[str, str], bytes]:
    with open(root / warc, "rb") as fh:
        fh.seek(offset)
        rec = next(iter(ArchiveIterator(fh)))
        status = int(rec.http_headers.get_statuscode() or "0") if rec.http_headers else 0
        headers = dict(rec.http_headers.headers) if rec.http_headers else {}
        return status, {k.lower(): v for k, v in headers.items()}, rec.content_stream().read()


def _decode(body: bytes, ctype: str) -> str:
    for cs in ([ctype.split("charset=", 1)[1].split(";")[0].strip()] if "charset=" in ctype else []) + ["utf-8", "cp950"]:
        try:
            return body.decode(cs)
        except (LookupError, UnicodeError):
            continue
    return body.decode("utf-8", errors="replace")


def page_text(markup: str) -> str:
    tree = LexborHTMLParser(markup)
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body or tree.root
    text = body.text(separator="\n", strip=True) if body else ""
    return "\n".join(line for line in (x.strip() for x in text.splitlines()) if line)


class Viewer:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self._dash: progress.Dashboard | None = None  # the progress dashboard's numbers (design §14), made when asked
        self._dash_lock = threading.Lock()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.root / 'frontier.db'}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def locate(self, row) -> tuple[str, int] | None:
        """Where a row's answer is archived: its own response record (url.warc, url.warc_offset: design §12), which the
        index by address cannot tell from a POST answered at the same address (a list's pager presses: the newest
        would win). By address only for a row that has no offset (the 10-24 archive's rows, which lack the column)."""
        keys = row.keys()
        if "warc_offset" in keys and row["warc_offset"] is not None and row["warc"]:
            return row["warc"], row["warc_offset"]
        idx = sqlite3.connect(self.root / "viewer-index.db")
        try:
            for url in (row["final_url"], row["url"]):
                if url:
                    hit = idx.execute("SELECT warc, offset FROM rec WHERE url=? ORDER BY warc DESC, offset DESC LIMIT 1",
                                      (url,)).fetchone()
                    if hit:
                        return hit[0], hit[1]
        finally:
            idx.close()
        return None

    # ---------------------------------------------------------------- pages
    def home(self, q: dict) -> str:
        with self.lock:
            added = build_index(self.root)
        c = self.db()
        states = dict(c.execute("SELECT state, count(*) FROM url GROUP BY state"))
        size = c.execute("SELECT coalesce(sum(bytes),0) FROM url WHERE state='done'").fetchone()[0]
        # a site's unit is the one its starting point was seeded with (pages found by links carry their source's name)
        rows = c.execute("""SELECT site, coalesce((SELECT s.unit FROM url s WHERE s.site = url.site
                                                     AND s.found_via IN ('seed','v1_known') ORDER BY s.id LIMIT 1), '') unit,
                              min(tier) tier, count(*) n,
                              sum(state='done') done, sum(state IN ('pending','active')) pend, sum(state='failed') failed,
                              sum(state='skipped' AND coalesce(reason,'') NOT LIKE 'quarantined%') skipped,
                              sum(coalesce(reason,'') LIKE 'quarantined%') quar, sum(flags IS NOT NULL) flagged,
                              sum(state='done' AND coalesce(status,0) != 200) not200, coalesce(sum(bytes),0) bytes
                            FROM url GROUP BY site ORDER BY tier, done DESC""").fetchall()
        out = [f"<p>网址共 {sum(states.values())}：" + "、".join(f"{STATE_ZH.get(k, k)} {v}" for k, v in states.items())
               + f"；已存 {size / 1e6:.0f} MB（索引新增 {added} 笔）。"
               "　<a href='/issues'>先看问题清单</a>　<a href='/search'>搜索网址或标题</a></p>",
               ("<table><tr><th>优先</th><th>网站</th><th>单位</th><th>已抓</th><th>待抓</th><th>失败</th><th>跳过</th>"
                "<th>被标记</th><th>非 200</th><th>隔离</th><th>大小</th></tr>")]
        for r in rows:
            out.append(f"<tr><td>{r['tier']}</td><td><a href='/site?k={quote(r['site'])}'>{html.escape(r['site'])}</a></td>"
                       f"<td>{html.escape(r['unit'])}</td><td>{r['done']}</td><td>{r['pend']}</td>"
                       f"<td class='{'bad' if r['failed'] else ''}'>{r['failed']}</td><td>{r['skipped']}</td>"
                       f"<td>{r['flagged']}</td><td>{r['not200']}</td><td>{r['quar']}</td>"
                       f"<td>{r['bytes'] / 1e6:.1f} MB</td></tr>")
        out.append("</table>")
        return self.layout("存档总览", "".join(out))

    def url_table(self, rows) -> str:
        out = ["<table><tr><th>状态</th><th>标题 / 网址</th><th>类型</th><th>大小</th><th>原因 / 标记</th><th>来源</th></tr>"]
        for r in rows:
            label = html.escape(r["title"] or "") or "<span class='dim'>（无标题）</span>"
            state = STATE_ZH.get(r["state"], r["state"]) + (f" {r['status']}" if r["status"] else "")
            note = " ".join(x for x in (r["reason"], r["flags"]) if x)
            out.append(f"<tr><td class='{'bad' if r['state'] == 'failed' else ''}'>{state}</td>"
                       f"<td><a href='/page?id={r['id']}'>{label}</a><br><span class='url'>{html.escape(r['url'])}</span></td>"
                       f"<td>{html.escape((r['ctype'] or r['kind'] or '').split(';')[0])}</td>"
                       f"<td>{(r['bytes'] or 0) / 1024:.0f} KB</td><td>{html.escape(note)}</td>"
                       f"<td>{html.escape(r['found_via'])}</td></tr>")
        out.append("</table>")
        if len(rows) >= MAX_ROWS:
            out.append(f"<p class='dim'>只列前 {MAX_ROWS} 个；用状态筛选或搜索缩小范围。</p>")
        return "".join(out)

    def site(self, q: dict) -> str:
        key = q.get("k", [""])[0]
        state = q.get("state", [""])[0]
        c = self.db()
        args: list = [key]
        where = "site=?"
        if state:
            where += " AND state=?"
            args.append(state)
        rows = c.execute(f"SELECT * FROM url WHERE {where} ORDER BY state != 'done', depth, id LIMIT {MAX_ROWS}",
                         args).fetchall()
        filters = " ".join(f"<a href='/site?k={quote(key)}&state={s}'>{STATE_ZH[s]}</a>" for s in STATE_ZH)
        return self.layout(f"网站 {key}", f"<p>筛选：<a href='/site?k={quote(key)}'>全部</a> {filters}</p>"
                           + self.url_table(rows))

    def search(self, q: dict) -> str:
        term = q.get("q", [""])[0].strip()
        body = ("<form action='/search'><input name='q' size='50' value='" + html.escape(term)
                + "' placeholder='网址或标题的一部分'> <button>搜索</button></form>")
        if term:
            like = f"%{term}%"
            rows = self.db().execute(f"SELECT * FROM url WHERE url LIKE ? OR title LIKE ? ORDER BY id LIMIT {MAX_ROWS}",
                                     (like, like)).fetchall()
            body += f"<p>{len(rows)} 个结果</p>" + self.url_table(rows)
        return self.layout("搜索", body)

    def issues(self, q: dict) -> str:
        c = self.db()
        parts = []
        sections = [
            ("失败", "state='failed'"),
            ("被标记（登录页、疑似假页面、解码问题等）", "flags IS NOT NULL"),
            ("已抓但不是 200", "state='done' AND status NOT IN (200, 301, 302, 303, 307, 308)"),
            ("规则更新前抓到、已隔离", "reason LIKE 'quarantined%'"),
            ("跳过（不含网站上限）", "state='skipped' AND reason NOT LIKE 'quarantined%' AND reason NOT LIKE '%cap'"),
        ]
        for name, where in sections:
            n = c.execute(f"SELECT count(*) FROM url WHERE {where}").fetchone()[0]
            groups = c.execute(f"SELECT coalesce(reason, flags, status) g, count(*) n FROM url WHERE {where} "
                               "GROUP BY g ORDER BY n DESC LIMIT 12").fetchall()
            rows = c.execute(f"SELECT * FROM url WHERE {where} ORDER BY id LIMIT 200").fetchall()
            parts.append(f"<h2>{name}：{n} 个</h2><p class='dim'>"
                         + "、".join(f"{html.escape(str(g['g']))} × {g['n']}" for g in groups) + "</p>"
                         + (self.url_table(rows) if rows else ""))
        return self.layout("问题清单", "".join(parts))

    def page(self, q: dict) -> str:
        uid = int(q.get("id", ["0"])[0])
        c = self.db()
        row = c.execute("SELECT * FROM url WHERE id=?", (uid,)).fetchone()
        if row is None:
            return self.layout("找不到", "<p>没有这个编号。</p>")
        meta = [("网址", (f"<a href='{html.escape(row['url'])}' rel='noreferrer' target='_blank'>{html.escape(row['url'])}</a>"
                          " <span class='dim'>（点这个会连到学校网站）</span>")),
                ("标题", html.escape(row["title"] or "")), ("单位 / 网站", html.escape(f"{row['unit'] or ''} / {row['site']}")),
                ("状态", f"{STATE_ZH.get(row['state'], row['state'])} {row['status'] or ''}"),
                ("原因 / 标记", html.escape(" ".join(x for x in (row["reason"], row["flags"]) if x))),
                ("类型 / 大小", html.escape(f"{row['ctype'] or row['kind']} / {(row['bytes'] or 0) / 1024:.0f} KB")),
                ("抓取时间", html.escape(row["fetched_at"] or "")), ("存档", html.escape(row["warc"] or "")),
                ("怎么找到的", html.escape(row["found_via"]) + (f" <a href='/page?id={row['found_from']}'>来源页</a>"
                                                            if row["found_from"] else ""))]
        body = ["<table class='meta'>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in meta) + "</table>"]
        if row["state"] == "done" and not (row["reason"] or "").startswith("quarantined"):
            loc = self.locate(row)
            if loc is None:
                body.append("<p class='bad'>存档索引里还没有这一页（可能刚抓到）：回总览页会更新索引。</p>")
            else:
                is_html = (row["ctype"] or "").startswith(("text/html", "application/xhtml"))
                if is_html:
                    tabs = [f"<a href='/preview?id={uid}' target='view'>网页预览（不连网）</a>",
                            f"<a href='/text?id={uid}' target='view'>文字</a>",
                            f"<a href='/source?id={uid}' target='view'>原始 HTML</a>"]
                    body.append("<p>看法：" + "　".join(tabs) + "</p>"
                                f"<iframe name='view' src='/preview?id={uid}' sandbox='allow-same-origin' "
                                "class='view'></iframe>")
                else:  # a PDF or an Office file: the browser's own viewer, in its own tab (it refuses sandboxed frames)
                    body.append(f"<p><a href='/raw?id={uid}' target='_blank'>打开原档（新分页）</a></p>")
        elif (row["reason"] or "").startswith("quarantined"):
            body.append("<p class='dim'>这一页在规则更新前抓到，已移到隔离区，这里不显示内容。</p>")
        links = c.execute("""SELECT l.dst, l.text, l.verdict, u.id FROM link l LEFT JOIN url u ON u.url = l.dst
                             WHERE l.src=? ORDER BY l.verdict, l.dst LIMIT 1000""", (uid,)).fetchall()
        if links:
            body.append(f"<h2>这一页的链接（{len(links)}）</h2><table><tr><th>结果</th><th>文字</th><th>网址</th></tr>")
            for link in links:
                dst = html.escape(link["dst"])
                cell = f"<a href='/page?id={link['id']}'>{dst}</a>" if link["id"] else dst
                body.append(f"<tr><td>{html.escape(link['verdict'])}</td><td>{html.escape(link['text'] or '')}</td>"
                            f"<td class='url'>{cell}</td></tr>")
            body.append("</table>")
        return self.layout(row["title"] or row["url"], "".join(body))

    def body_of(self, uid: int) -> tuple[str, bytes] | None:
        row = self.db().execute("SELECT * FROM url WHERE id=?", (uid,)).fetchone()
        if row is None or (row["reason"] or "").startswith("quarantined"):
            return None
        loc = self.locate(row)
        if loc is None:
            return None
        _status, headers, body = read_record(self.root, *loc)
        return headers.get("content-type", row["ctype"] or ""), body

    # ---------------------------------------------------------------- the progress dashboard (design 2026-11-04)
    def dashboard(self) -> progress.Dashboard:
        """The dashboard's numbers (progress.Dashboard), made at the first dashboard page: read-only on frontier.db,
        never build_index; once a run has ended it writes that run's copy of the problem log (freeze_reports)."""
        with self._dash_lock:
            if self._dash is None:
                self._dash = progress.Dashboard(self.root, freeze_reports=True)
            return self._dash

    def _polled(self, title: str, main: str, q: dict) -> str:
        """A page that asks for its <main> again every REFRESH_S seconds: with part=main only that <main>."""
        if _arg(q, "part") == "main":
            return main
        return self.layout(title, PROGRESS_CSS + STALE_NOTE + main + PROGRESS_JS)

    def progress(self, q: dict) -> str:
        """/progress: the run, the whole run's progress and 错 N, a card per server (design §1.1)."""
        return self._polled("进度看板", home_html(self.dashboard().main()), q)

    def progress_server(self, q: dict) -> str:
        """/progress/server/<card key> (or ?card=, ?server=): that card's sites, a row each (design §1.2)."""
        key = _arg(q, "card") or _arg(q, "server")
        snap = self.dashboard().main()
        name = next((c["name"] for c in snap.get("cards", []) + snap.get("small", []) if c["key"] == key), "服务器")
        return self._polled(name, server_html(snap, key), q)

    def progress_log(self, q: dict) -> str:
        """/progress/log[?site=<site>|server=<card key>][&code=<code>]: the problem log (design §1.3); it never asks
        again by itself."""
        site, card, code = _log_args(q)
        view = self.dashboard().log(site=site, card=card, code=code)
        named = (site or card) and "title" in view
        title = (_site_name(view["title"]) if site else view["title"]) if named else ""
        return self.layout("错误记录" + (f" · {title}" if title else ""), PROGRESS_CSS + log_html(view))

    def progress_log_txt(self, q: dict) -> tuple[bytes, str]:
        """/progress/log.txt with the log page's filters: the same lines as a tab-separated file (progress.log_text),
        and its name, problems-<YYYYMMDD-HHMM>.log (Taipei, when its numbers were read)."""
        site, card, code = _log_args(q)
        d = self.dashboard()
        view = d.log(site=site, card=card, code=code)
        text = f"# {view['problem']}\n" if "problem" in view else progress.log_text(view, d.db)
        return text.encode(), f"problems-{datetime.fromtimestamp(view['at'], progress.TZ):%Y%m%d-%H%M}.log"

    def progress_checks(self, q: dict) -> str:
        """/progress/checks: the acceptance checks, worked out when the page is opened (kept until P49 settles what
        爬完 is)."""
        return self.layout("验收", PROGRESS_CSS + checks_html(self.dashboard().checks_page()))

    def progress_site(self, q: dict) -> str:
        """/progress/site?k=<site>[&b=<category>][&l=<page label>]: one site's pages and other addresses (§14.2)."""
        key = q.get("k", [""])[0]
        bucket = q.get("b", [""])[0] or None
        label = q.get("l", [""])[0] or None
        d = self.dashboard().site(key, bucket=bucket if bucket in progress.BUCKET_ORDER else None,
                                  label=label if label in progress.PAGE_ORDER else None)
        if d is None:
            return self.layout("找不到这个网站", PROGRESS_CSS + "<p>名单上没有这个网站。<a href='/progress'>回进度看板</a></p>")
        if "problem" in d:
            return self.layout("进度看板", PROGRESS_CSS + f"<p class='c-red'>✗ {html.escape(d['problem'])}</p>")
        return self.layout(d["name"], PROGRESS_CSS + progress_site_html(d))

    @staticmethod
    def layout(title: str, body: str) -> str:
        return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
                "<style>body{font:14px/1.5 -apple-system,'PingFang TC',sans-serif;margin:16px;color:#222}"
                "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px 6px;"
                "text-align:left;vertical-align:top}th{background:#f4f4f4;position:sticky;top:0}"
                ".url{color:#666;font-size:12px;word-break:break-all}.dim{color:#888}.bad{color:#b00020}"
                ".meta th{width:9em}iframe.view{width:100%;height:75vh;border:1px solid #ccc}pre{white-space:pre-wrap}"
                "nav a{margin-right:12px}</style></head><body><nav><a href='/progress'>进度</a>"
                "<a href='/progress/log'>错误记录</a><a href='/progress/checks'>验收</a><a href='/'>总览</a>"
                "<a href='/issues'>问题清单</a><a href='/search'>搜索</a></nav>"
                f"<h1>{html.escape(title)}</h1>{body}</body></html>")


def make_handler(viewer: Viewer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def send(self, code: int, ctype: str, data: bytes, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = urlsplit(self.path)
            q = parse_qs(parts.query)
            # every page (the dashboard, the archived pages and their text) only for this machine's own pages, never
            # a page another site sent here under a name of its own (DNS rebinding) 【Claude 的执行办法】
            if (self.headers.get("Host") or "").rsplit(":", 1)[0] not in LOCAL_HOSTS:
                return self.send(403, "text/plain; charset=utf-8", b"forbidden")
            try:
                route = parts.path
                if route.startswith(SERVER_PAGE):  # /progress/server/<card key>, the key percent-encoded
                    route, q = "/progress/server", {**q, "card": [unquote(route[len(SERVER_PAGE):])]}
                if route == "/progress/log.txt":
                    data, name = viewer.progress_log_txt(q)
                    return self.send(200, "text/plain; charset=utf-8", data,
                                     {"Cache-Control": "no-store",
                                      "Content-Disposition": f"attachment; filename={name}"})
                if route in DASHBOARD_PAGES:  # the dashboard's numbers change: never kept by the browser
                    fn = getattr(viewer, DASHBOARD_PAGES[route])
                    return self.send(200, "text/html; charset=utf-8", fn(q).encode(), {"Cache-Control": "no-store"})
                if route in ("/", "/site", "/search", "/issues", "/page"):
                    fn = {"/": viewer.home, "/site": viewer.site, "/search": viewer.search, "/issues": viewer.issues,
                          "/page": viewer.page}[route]
                    return self.send(200, "text/html; charset=utf-8", fn(q).encode())
                if route in ("/text", "/source", "/preview", "/raw"):
                    got = viewer.body_of(int(q.get("id", ["0"])[0]))
                    if got is None:
                        return self.send(404, "text/plain; charset=utf-8", "存档里没有这一页的内容".encode())
                    ctype, body = got
                    if route == "/raw":
                        markup = (ctype or "").lower().startswith(("text/html", "application/xhtml", "image/svg"))
                        return self.send(200, ctype or "application/octet-stream", body,
                                         {"Content-Security-Policy": PREVIEW_CSP} if markup else {})
                    text = _decode(body, ctype)
                    if route == "/preview":
                        return self.send(200, "text/html; charset=utf-8", text.encode(),
                                         {"Content-Security-Policy": PREVIEW_CSP})
                    shown = page_text(text) if route == "/text" else text
                    return self.send(200, "text/html; charset=utf-8",
                                     f"<!doctype html><meta charset='utf-8'><pre>{html.escape(shown)}</pre>".encode())
                return self.send(404, "text/plain; charset=utf-8", b"not found")
            except Exception as exc:  # noqa: BLE001 - one broken record never stops the viewer
                return self.send(500, "text/plain; charset=utf-8", f"{type(exc).__name__}: {exc}".encode())

        def do_POST(self):
            # the viewer only shows (the 10-24 choice page that saved was taken out 10-26, design §14.1): nothing here
            # takes a POST
            return self.send(405, "text/plain; charset=utf-8", b"method not allowed", {"Allow": "GET"})

    return Handler


def serve(root: Path, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """The viewer on host:port: 127.0.0.1, this machine only; in the lab's Docker container 0.0.0.0, which Docker
    forwards from the host's own 127.0.0.1 only (design 2026-11-04 §3: the Mac comes in through an SSH tunnel).
    Wherever it listens, a request is answered only when its Host names this machine (LOCAL_HOSTS)."""
    return ThreadingHTTPServer((host, port), make_handler(Viewer(root)))


# ---------------------------------------------------------------- the progress dashboard, drawn (design 2026-11-04)
# progress.py works the numbers out; these functions only draw them, in as few words as say it (the owner 11-04:
# 「字太多了」; 3/25, 0/41, 2:47:18, no 礼貌 unless broken): the codes and the longer words go to the hover text, colour
# only with a sign or words (✓ 完成 / ✗ 卡住 / 错 N)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
SERVER_PAGE = "/progress/server/"
# the dashboard's pages, by route (all GET, Host-checked, never kept by the browser); /progress/log.txt is a file
DASHBOARD_PAGES = {"/progress": "progress", "/progress/server": "progress_server", "/progress/log": "progress_log",
                   "/progress/checks": "progress_checks", "/progress/site": "progress_site"}
_SIGN = {"green": "✓", "yellow": "⚠", "red": "✗", "none": ""}
LOG_LINES = 1000  # the log page lists the newest this many lines (the rehearsal's 26,492: a 10 MB page); the file, all
PROGRESS_CSS = """<style>
.c-green{color:#1a7f37}.c-yellow{color:#9a6700}.c-red{color:#b00020}.c-none{color:inherit}
.pbar{display:inline-block;width:180px;max-width:45vw;height:10px;background:#e4e4e4;border-radius:5px;
vertical-align:middle;margin-right:8px}.pbar span{display:block;height:10px;background:#2563eb;border-radius:5px}
.pbar.done span{background:#111}
.big{font-size:16px}.big .pbar{width:280px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}.card p{margin:3px 0}
.card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:250px}.card.small{min-width:0;color:#555}
.st{border:1px solid currentColor;border-radius:9px;padding:0 6px;font-size:12px;white-space:nowrap}
.st.c-none{color:#666}
.big a,.card a,.card span{white-space:nowrap}
#sites,#hosts{width:auto}#sites td,#sites th,#hosts td,#hosts th{padding:4px 16px 4px 6px}th.num{text-align:right}
#pmain.stale{filter:grayscale(1);opacity:.45}
.stale-note{position:sticky;top:0;z-index:1;background:#444;color:#fff;padding:6px 10px;border-radius:4px;
margin:0 0 8px}
td.when,td.nw{white-space:nowrap}table.log td{font-size:13px;overflow-wrap:anywhere}
span.ref{display:inline-block;max-width:10em}
main section{margin:18px 0}main h2{font-size:17px;margin:14px 0 6px}
details summary{cursor:pointer;font-weight:600;margin:6px 0}pre.cmd{background:#f6f6f6;padding:8px;border-radius:4px}
td.num{text-align:right;white-space:nowrap}main th{white-space:nowrap}
ul.lost{margin:2px 0 0 0;padding-left:18px;font-size:12px}
.filters a{margin-right:10px;white-space:nowrap}.filters a.on{font-weight:600;color:inherit;text-decoration:none}
</style>"""
# design §1.1: no new numbers for 2 minutes greys the page and says so (the tunnel or the viewer gone: old numbers
# never pass for a crawl still going)
STALE_NOTE = "<p id='pstale' class='stale-note' hidden>看不到最新资料</p>"
PROGRESS_JS = """<script>
(function(){
  var last = Date.now(), busy = false;
  function grey(){   // no new numbers for this long: the page greys out and says so
    var old = Date.now() - last > @STALE@;
    var m = document.getElementById('pmain'); if (m) m.classList.toggle('stale', old);
    var n = document.getElementById('pstale'); if (n) n.hidden = !old;
  }
  function tick(){
    if (document.hidden || busy) return;   // a hidden page asks nothing (Page Visibility API)
    busy = true;
    var u = new URL(location.href); u.hash = ''; u.searchParams.set('part', 'main');
    var ctl = new AbortController(), t = setTimeout(function(){ ctl.abort(); }, 2 * @EVERY@);
    fetch(u, {cache: 'no-store', signal: ctl.signal}).then(function(r){ return r.ok ? r.text() : null; })
    .then(function(h){
      var old = document.getElementById('pmain'), tpl = document.createElement('template');
      tpl.innerHTML = h || '';
      var n = tpl.content.getElementById('pmain');
      if (!old || !n) return;
      old.querySelectorAll('details[id]').forEach(function(d){   // what the owner opened or closed stays so
        var m = n.querySelector('#' + d.id); if (m) m.open = d.open; });
      old.replaceWith(n); last = Date.now();
    }).catch(function(){}).then(function(){ clearTimeout(t); busy = false; grey(); });
  }
  setInterval(tick, @EVERY@);
  setInterval(grey, 5000);
  document.addEventListener('visibilitychange', function(){ grey(); if (!document.hidden) tick(); });
  window.addEventListener('pageshow', function(e){ if (e.persisted) { grey(); tick(); } });
})();
</script>""".replace("@EVERY@", str(int(progress.REFRESH_S * 1000))).replace(
    "@STALE@", str(int(progress.STALE_S * 1000)))


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _arg(q: dict, name: str) -> str:
    return q.get(name, [""])[0]


def _log_args(q: dict) -> tuple[str | None, str | None, str | None]:
    """The log's filters: ?site=<site>, ?server=<card key> (?card= the same), ?code=<code>."""
    return _arg(q, "site") or None, _arg(q, "server") or _arg(q, "card") or None, _arg(q, "code") or None


def _href(path: str, **params) -> str:
    """A dashboard address with its filters (those given), each percent-encoded."""
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v not in (None, ""))
    return f"{path}?{query}" if query else path


def _mark(color: str, words: str, sign: str | None = None, tip: str = "") -> str:
    sign = _SIGN.get(color, "") if sign is None else sign
    title = f" title='{_e(tip)}'" if tip else ""
    return f"<span class='c-{color}'{title}>{_e(sign + ' ' if sign else '')}{_e(words)}</span>"


def _n(x) -> str:
    return f"{x or 0:,}"


def _short(label: str) -> str:
    """A kind's words without what its brackets or its second clause add (看起来像不存在（程序比对的） → 看起来像不存在):
    the whole is the hover text."""
    return re.split("[（；]", label, maxsplit=1)[0]


def _site_name(name: str) -> str:
    """A site's name without the note its brackets add (site_plan's 某系网站（SSO，cLv2=…）→ 某系网站; review 11-04: few
    words): the whole is the hover text. A name that is all bracket stays whole."""
    return re.split("[（(]", name, maxsplit=1)[0].strip() or name


def _site_tip(name: str, site: str) -> str:
    """A site's hover text: its whole name, when the page shows it cut (_site_name), and its key."""
    return site if _site_name(name) == name else f"{name} · {site}"


def _bar(share: float, done: bool = False) -> str:
    """A progress bar: blue while there is work left, black once nothing is (the owner 11-04: 「看板的进度条换成蓝色的，
    爬完再用黑色表示」)."""
    return (f"<span class='pbar{' done' if done else ''}'><span style='width:{100 * max(0.0, min(1.0, share)):.1f}%'>"
            "</span></span>")


def _status(s: dict) -> str:
    """A state badge (在抓, 休息中 02:40, 完成 …; design §2.5): ✓ when done, ✗ when red; why in the hover text."""
    sign = "✓ " if s["code"] == "done" else "✗ " if s["color"] == "red" else ""
    title = f" title='{_e(s['tip'])}'" if s.get("tip") else ""
    return f"<span class='st c-{s['color']}'{title}>{_e(sign + s['words'])}</span>"


def _err(e: dict | None, href: str, label: str = "错 ") -> str:
    """错 N (design §2.4): the number only, red with a failure, else yellow, a link to its lines; nothing at 0; a dim
    「错 …」 that says why while it is not known yet. label: the word before the number (none in a table's 错 column)."""
    if e is None or e.get("n") is None:
        return f"<span class='dim' title='{_e((e or {}).get('why') or '错的条数还在算')}'>{_e(label)}…</span>"
    if not e["n"]:
        return ""
    tip = f"失败 {e['failed']:,} · 要人看 {e['review']:,}" + (f" · 待判定 {e['pending']:,}" if e.get("pending") else "")
    return f"<a class='c-{e['color']}' href='{_e(href)}' title='{_e(tip)}'>{_e(label)}{e['n']:,}</a>"


def _busy(seconds: float | None) -> str:
    """用时 h:mm:ss (design §2.3, the owner 11-04: 「用 2:47:18 这种时间来表示就够了」)."""
    return f"<span title='真正在抓的时间，不算休息、等重试和停着的时候'>用时 {_e(progress.fmt_hms(seconds))}</span>"


def _found(x: dict) -> str:
    """发现中 beside a percent (design §1.1): new addresses in the last 10 minutes, the percent rises slowly."""
    return (" <span class='dim' title='最近 10 分钟还在发现新网址，百分比会先涨得慢'>发现中</span>"
            if x["discovering"] else "")


def _problem_main(snap: dict, mid: str = "pmain") -> str | None:
    if "problem" not in snap:
        return None
    return f"<main id='{mid}'><p class='c-red'>✗ {_e(snap['problem'])}</p></main>"


def _card_html(c: dict) -> str:
    """A server card (design §1.1): its name, a link to its page, and 完成的网站数/网站数 (3/25); its state; bar and
    percent (发现中); 用时 h:mm:ss and 错 N, a link to its lines. Its host names in the hover text."""
    key = quote(c["key"], safe="")
    return (f"<div class='card' title='{_e(c['tip'])}'><p><b><a href='{SERVER_PAGE}{key}'>{_e(c['name'])}</a></b>"
            f" {c['sites_done']:,}/{c['sites_total']:,}　{_status(c['state'])}</p>"
            f"<p>{_bar(c['share'], c['left'] == 0)}{_e(c['pct'])}{_found(c)}</p>"
            f"<p>{_busy(c['busy'])}　{_err(c['errors'], _href('/progress/log', server=c['key']))}</p></div>")


def _small_html(c: dict) -> str:
    """A small card (design §1.1): its name, a link to its page (the owner 11-04: 「为什么这三个卡片我点不进去详细看」),
    its state and 完成/总数 (0/41) only: what it holds counts for the sites that use it, so no percent and no 错 (they
    would count twice). Its hosts and 用时 (design §2.3) in the hover text."""
    tip = "；".join(x for x in (c["tip"], f"用时 {progress.fmt_hms(c['busy'])}") if x)
    key = quote(c["key"], safe="")
    return (f"<div class='card small' title='{_e(tip)}'><b><a href='{SERVER_PAGE}{key}'>{_e(c['name'])}</a></b>"
            f" {c['finished']:,}/{c['known']:,}　{_status(c['state'])}</div>")


def _server_tr(r: dict) -> str:
    """One server of a small card on its page: the host name with the most rows (等 N 个 when it has more; all of them
    and the server in the hover text), its state, its bar and percent (完成/总数 in the hover text), 用时, and how many
    sites' rows it holds (which, in the hover text)."""
    hosts = r["hosts"] or [r["queue"]]
    more = f" <span class='dim'>等 {len(hosts)} 个</span>" if len(hosts) > 1 else ""
    if r["queue"] in progress.DNS_QUEUES:  # no server: names that do not resolve
        tip = "查不到地址的主机名：" + "、".join(hosts)
    else:
        tip = f"服务器 {r['queue'].removeprefix('ip:')}：" + "、".join(hosts)
    bar = (f"<span title='{r['finished']:,}/{r['known']:,}'>{_bar(r['share'], r['left'] == 0)}{_e(r['pct'])}"
           "</span>")
    sites = "、".join(f"{_site_name(name)} {n:,}" for name, n in r["sites"])
    return (f"<tr><td title='{_e(tip)}'>{_e(hosts[0])}{more}</td><td>{_status(r['state'])}</td><td>{bar}</td>"
            f"<td class='num'>{_e(progress.fmt_hms(r['busy']))}</td>"
            f"<td class='num' title='{_e(sites)}'>{len(r['sites']):,}</td></tr>")


def home_html(snap: dict) -> str:
    """The home page's <main> (design 2026-11-04 §1.1), what /progress?part=main answers: one line for the run (在跑
    6:12:05, or why it is not going; 另有程序在跑 while another program holds run.lock), the whole run's bar and
    percent, 错 N (礼貌 ✗ N only when broken); a card per server; the small cards. No time left, no sentences."""
    gone = _problem_main(snap)
    if gone:
        return gone
    run, t = snap["run"], snap["total"]
    top = [_mark(run["color"], run["words"], "✗" if run["color"] == "red" else "", tip=run["tip"])]
    if run.get("other") is not None:  # run.lock held by jscheck, reconcile, seed, retry …: never 在跑 (review 11-04)
        top.append(f"<span class='dim' title='{_e(run['tip'])}'>另有程序在跑</span>")
    top += [f"{_bar(t['share'], t['left'] == 0)}{_e(t['pct'])}", _err(snap["errors"], "/progress/log")]
    u = snap.get("update")  # the nightly update (progress.update_state): absent before go-live
    if u:
        top.append(_mark(u["color"], u["words"], tip=u["tip"]))
    if snap["polite"]["hard"]:  # design §2.6: shown only when broken (the owner 11-04: 「礼貌也不需要显示」)
        top.append(f"<a class='c-red' href='/progress/log#polite'>礼貌 ✗ {snap['polite']['hard']:,}</a>")
    if snap.get("only"):
        top.append(f"这次只抓清单里 {snap['only']:,} 个网址")
    cards = "".join(_card_html(c) for c in snap["cards"])
    small = "".join(_small_html(c) for c in snap["small"])
    return (f"<main id='pmain'><p class='big'>{'　'.join(x for x in top if x)}</p><div class='cards'>{cards}</div>"
            + (f"<div class='cards'>{small}</div>" if small else "") + "</main>")


def _row_html(r: dict, shared: bool) -> str:
    """One site on its server's page (design §1.2): its name, a link to its page (on the shared card, the host it is
    on); ✓ when done, 排队 while not reached (never 0%), else its bar and percent; 用时; 错 N, a link to its lines."""
    host = f" <span class='dim'>{_e(r['host'])}</span>" if shared and r["host"] else ""
    if r["done"]:
        mark = f"{_bar(1.0, True)}{_e(r['pct'])}"  # a black full bar (the owner 11-04: 爬完再用黑色表示)
    elif r["queued"]:
        mark = "<span class='dim' title='还没轮到：同一台上先抓全校重要的，再抓各系的'>排队</span>"
    else:
        mark = f"{_bar(r['share'])}{_e(r['pct'])}{_found(r)}"
    busy = "<span class='dim'>—</span>" if r["queued"] else _e(progress.fmt_hms(r["busy"]))
    return (f"<tr><td><a href='{_e(_href('/progress/site', k=r['site']))}' title='{_e(_site_tip(r['name'], r['site']))}'>"
            f"{_e(_site_name(r['name']))}</a>{host}</td><td>{mark}</td><td class='num'>{busy}</td>"
            f"<td class='num'>{_err(r['errors'], _href('/progress/log', site=r['site']), label='')}</td></tr>")


def server_html(snap: dict, key: str) -> str:
    """One card's page (design §1.2), what /progress/server/<key>?part=main answers: the card's line (3/25, state, bar
    and percent, 用时, 错 N), then a row a site, those being fetched first, then those queued, then those done. A small
    card's: its line (完成/总数, state, bar and percent, 用时; no 错, it counts on the sites' own cards), then a row a
    server, the red ones first, then the yellow, those done last."""
    gone = _problem_main(snap)
    if gone:
        return gone
    c = next((x for x in snap["cards"] + snap["small"] if x["key"] == key), None)
    if c is None:
        return "<main id='pmain'><p>没有这张卡片。<a href='/progress'>回进度看板</a></p></main>"
    if c["small"]:
        head = (f"<p class='big' title='{_e(c['tip'])}'>{c['finished']:,}/{c['known']:,}　{_status(c['state'])}"
                f"　{_bar(c['share'], c['left'] == 0)}{_e(c['pct'])}　{_busy(c['busy'])}</p>")
        rows = "".join(_server_tr(r) for r in c["rows"])
        return (f"<main id='pmain'>{head}<table id='hosts'><tr><th>主机</th><th>状态</th><th>进度</th>"
                f"<th class='num'>用时</th><th class='num' title='这台存着几个网站的网址（滑过数字看是哪几个）'>网站</th>"
                f"</tr>{rows}</table></main>")
    head = (f"<p class='big' title='{_e(c['tip'])}'>{c['sites_done']:,}/{c['sites_total']:,}　{_status(c['state'])}"
            f"　{_bar(c['share'], c['left'] == 0)}{_e(c['pct'])}{_found(c)}　{_busy(c['busy'])}"
            f"　{_err(c['errors'], _href('/progress/log', server=c['key']))}</p>")
    rows = "".join(_row_html(r, c["key"] == progress.UNITS) for r in c["rows"])
    return (f"<main id='pmain'>{head}<table id='sites'><tr><th>网站</th><th>进度</th><th class='num'>用时</th>"
            f"<th class='num'>错</th></tr>{rows}</table></main>")


def _ref_html(ref: str) -> str:
    """A line's number, in a narrow box (an entry's, k<page>:<its address>, runs to hundreds of characters): u<id>
    and p<id> open the archived copy (/page?id=…)."""
    m = re.fullmatch(r"[up](\d+)", ref)
    return "<span class='ref'>" + (f"<a href='/page?id={m[1]}'>{_e(ref)}</a>" if m else _e(ref)) + "</span>"


def log_html(view: dict) -> str:
    """The problem log's page (design §1.3): how many lines and their split (failed, review, to be judged; when they
    were read in the hover text) and the download; a link a kind with its count (only that kind); a line a problem,
    newest first (time, server, site, number, kind, detail, address: never a title), at most LOG_LINES of them; then,
    but in a site's view, the server events and the politeness violations, neither counted."""
    gone = _problem_main(view, "plog")
    if gone:
        return gone
    site, card, code, now = view["site"], view["card"], view["code"], view["at"]
    where = {"site": site} if site is not None else {"server": card} if card is not None else {}
    down = f"<a href='{_e(_href('/progress/log.txt', **where, code=code))}'>下载</a>"
    if view["error"]:
        out = [f"<p class='big'><span class='dim'>{_e(view['error'])}</span>　{down}</p>"]
    else:  # design §1.3: 「49 条：失败 4 · 要人看 45」; when its numbers were read, in the hover text
        split = f"失败 {view['failed']:,} · 要人看 {view['review']:,}" + (
            f" · 待判定 {view['pending']:,}" if view["pending"] else "")
        tip = f"错的条数每 2 分钟在背景更新一次；这份是 {progress.fmt_clock(view['slow_at'], now)} 的"
        out = [f"<p class='big'><span title='{_e(tip)}'>{view['n']:,} 条：{_e(split)}</span>　{down}</p>"]
    kinds = [f"<a href='{_e(_href('/progress/log', **where))}'>全部</a>"] if code is not None else []
    for c in view["codes"]:
        on = " class='on'" if c["code"] == code else ""
        kinds.append(f"<a href='{_e(_href('/progress/log', **where, code=c['code']))}'{on} title='{_e(c['label'])}'>"
                     f"{_e(_short(c['label']))} {c['n']:,}</a>")
    if kinds:
        out.append(f"<p class='filters'>{''.join(kinds)}</p>")
    whole = site is None and card is None
    shown = view["rows"][:LOG_LINES]
    if len(shown) < len(view["rows"]):
        out.append(f"<p class='dim'>只列最新 {len(shown):,} 条；全部在下载的档案里</p>")
    if shown:
        cols = "<th>时间</th>" + ("<th>服务器</th>" if whole else "") + ("<th>网站</th>" if site is None else "")
        out.append(f"<table id='lines' class='log'><tr>{cols}<th>编号</th><th>种类</th><th>细节</th><th>网址</th></tr>")
        for r in shown:
            color = "red" if r["kind"] == "failed" else "yellow"
            out.append(f"<tr><td class='when'>{_e(progress.fmt_clock(r['at'], now))}</td>"
                       + (f"<td class='nw'>{_e(r['card_name'])}</td>" if whole else "")
                       + (f"<td title='{_e(_site_tip(r['site_name'], r['site']))}'>{_e(_site_name(r['site_name']))}</td>"
                          if site is None else "")
                       + f"<td>{_ref_html(r['ref'])}</td>"
                       f"<td class='c-{color} nw' title='{_e(r['label'])}（{_e(r['code'])}）'>"
                       f"{_e(r['kind_zh'])}·{_e(_short(r['label']))}</td>"
                       f"<td>{_e(r['detail'])}</td><td class='url'>{_e(r['url'])}</td></tr>")
        out.append("</table>")
    if site is None:  # a server's trouble and politeness: never a site's (design §1.3)
        out.append(f"<h2 title='服务器那一级的事：不算进条数'>服务器事件 {len(view['events']):,}</h2>")
        if view["events"]:
            out.append("<table id='events' class='log'><tr><th>时间</th><th>服务器</th><th>事件</th><th>细节</th></tr>"
                       + "".join(f"<tr><td class='when'>{_e(progress.fmt_clock(e['at'], now))}</td>"
                                 f"<td>{_e(e['card_name'] or '—')}</td>"
                                 f"<td title='{_e(e['code'])}'>{_e(e['label'])}</td>"
                                 f"<td>{_e(e['detail'])}</td></tr>" for e in view["events"]) + "</table>")
        hard = view["polite_hard"] if whole else len(view["polite"])
        out.append(f"<h2 id='polite' title='同时连两条、间隔太短、没照网站要求的间隔：应该一直是 0'>礼貌违规 {hard:,}</h2>")
        if view["polite"]:
            out.append("<table id='violations' class='log'><tr><th>时间</th><th>服务器</th><th>违规</th><th>细节</th>"
                       "<th>网址</th></tr>"
                       + "".join(f"<tr><td class='when'>{_e(progress.fmt_clock(p['at'], now))}</td>"
                                 f"<td>{_e(p['card_name'] or p['server'])}</td>"
                                 f"<td class='c-red'>{_e(p['kind_zh'])}</td>"
                                 f"<td>{_e(progress.mask_text(p['detail']))}</td>"
                                 f"<td class='url'>{_e(progress.mask_url(p['url']))}</td></tr>" for p in view["polite"])
                       + "</table>")
    return "<main id='plog'>" + "".join(out) + "</main>"


def checks_html(d: dict) -> str:
    """The acceptance page (/progress/checks; design §4: kept as it was until P49 settles what 爬完 is), worked out
    when opened: the cheap checks and what the reconciliations found the crawl has no row for, the round-two list, and
    the steps after a run stopped."""
    gone = _problem_main(d, "pchecks")
    if gone:
        return gone
    steps = (f"<section><h2>停下以后（每个网站这几步都过了才算爬完）</h2>"
             f"<pre class='cmd'>{_e(progress.COMMANDS['after'])}</pre></section>")
    return "<main id='pchecks'>" + _checks_html(d) + _round2_html(d) + steps + "</main>"


def _checks_html(snap: dict) -> str:
    """The acceptance page's checks (monitor.checks, cheap) and what the reconciliations found the crawl has no row
    for (§18.2b), open."""
    checks = snap.get("checks")
    if checks is None:
        body = f"<p class='c-yellow'>⚠ 没检查：{_e(snap.get('checks_error') or '还在算')}</p>"
        n_bad = None
    else:
        n_bad = sum(c["result"] in ("warn", "fail", "skip") for c in checks)
        color = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "yellow"}
        words = {"pass": "通过", "warn": "要看", "fail": "出错", "skip": "没检查"}
        body = "<table>" + "".join(
            f"<tr><td>{_e(c['no'])}</td><td>{_e(c['title'])}</td>"
            f"<td>{_mark(color.get(c['result'], 'yellow'), words.get(c['result'], c['result']))}</td>"
            f"<td>{_e(c['detail'])}</td></tr>" for c in checks) + "</table>"
    found = snap.get("missing_found") or []
    if found:
        body += ("<p><b>要人看：对账发现爬虫没找到的网址</b></p><ul>"
                 + "".join(f"<li>{_e(m['site'])}（{_e(m['item'])} 找到 {m['n']} 个）："
                           + "、".join(_e(u) for u in m["urls"]) + "</li>" for m in found) + "</ul>")
    title = "漏抓检查" + ("（没检查）" if n_bad is None else f"（{n_bad} 项要看）" if n_bad else "（都通过）")
    return f"<section><details id='d-checks' open><summary>{_e(title)}</summary>{body}</details></section>"


def _round2_html(snap: dict) -> str:
    """The acceptance page's round-two list (§14.2): what this round only notes, by batch, source and domain."""
    r2 = snap.get("round2")
    if not r2:
        return "<section><details id='d-round2'><summary>第二轮清单（还在算）</summary></details></section>"
    by_src = "、".join(f"{_e(x['source'])} {_n(x['n'])}" for x in r2["by_source"]) or "—"
    doms = "、".join(f"{_e(x['domain'])} {_n(x['n'])}" for x in r2["domains"]) or "—"
    rows = "".join(f"<tr><td title='{_e(x['why'])}'>{_e(x['zh'])}</td><td class='num'>{_n(x['sites'])}</td>"
                   f"<td class='num'>{_n(x['n'])}</td></tr>" for x in r2["by_why"])
    return (f"<section><details id='d-round2' open><summary>第二轮清单（共 {_n(r2['total'])} 个网址，这一轮只记下来）</summary>"
            f"<table><tr><th>哪一批</th><th>网站</th><th>网址</th></tr>{rows}</table><p>按起点来源：{by_src}</p>"
            f"<p>校外网域（前 20）：{doms}</p></details></section>")


def progress_site_html(d: dict) -> str:
    """One site's page (design §14.2): its pages by completeness, then its other addresses and presses by the ten
    categories, each failure with its reason (the codes as hover text)."""
    now, key = d["at"], quote(d["site"])
    sign, words, color = d.get("shown") or progress.STATE_SHOW.get(d["state"], ("⚠", "没检查", "yellow"))
    why = "".join(f"<li>{_e(w)}</li>" for w in d.get("why") or [])
    why += "".join(f"<li class='c-yellow'>⚠ {_e(w)}</li>" for w in d.get("warnings") or [])
    home = (f"<a href='{_e(d['home'])}' rel='noreferrer' target='_blank'>{_e(d['home'])}</a> "
            "<span class='dim'>（点这个会连到学校网站）</span>") if d.get("home") else ""
    rec = d.get("reconcile")
    if rec is None:
        rec_line = "还没对账"
    else:
        covers = rec.get("cover_lines") or ([f"覆盖率估计 {rec['cover_cc']:.0%}（偏高）"] if rec["cover_cc"] is not None
                                            else [])
        rec_line = (f"最近一次对账 {progress.fmt_clock(rec['at'], now)}（{rec['batch']}）"
                    + (f"（已过期：之后又有{rec.get('stale') or '新的抓取'}，要重对）" if rec.get("fresh") is False else "")
                    + "：" + ("关卡全过" if rec["gates_ok"] else "有关卡没过")
                    + (f"（没比到的：{rec['blind']}，不等于没问题）" if rec.get("blind") else "")
                    + (f"；{rec['menu']}" if rec.get("menu") else "")
                    + "".join(f"；{x}" for x in covers))
    slow = (f"完整度更新于 {progress.fmt_clock(d['slow_at'], now)}" if d.get("slow_at")
            else "完整度还在算（第一次要几秒），网页这一块稍后再看")
    out = [f"<p><a href='/progress'>← 回进度看板</a></p><p>{_e(progress.TIER_NAME.get(d['tier'], ''))} · {home}</p>",
           f"<p class='big'>{_mark(color, words, sign)}</p>" + (f"<ul>{why}</ul>" if why else ""),
           f"<p class='dim'>{_e(rec_line)} · {_e(slow)}</p>"]
    # pages, by their completeness
    flt = "".join(f"<a href='/progress/site?k={key}&l={c}'>{_e(zh)} {n:,}</a>" for c, zh, n in d["page_counts"])
    out.append(f"<h2>网页（照完整度排：连接失败在前，完整的在最后）</h2><p class='filters'>筛选：<a href='/progress/site?k={key}'>"
               f"全部</a>{flt}</p>")
    rows = []
    for p in d["pages"]:
        title = _e(p["title"]) or "<span class='dim'>（无标题）</span>"
        extra = []
        if p["came_from"]:
            extra.append(f"由 {p['came_from']} 个网址跳来")
        if p.get("landing"):
            extra.append(f"跳到 {p['landing']}")
        if p.get("in_image"):
            extra.append(outcome.IN_IMAGE_WORDS)
        lost = "".join(f"<li>{_e(x['zh'])}：<code>{_e(x['res_key'])}</code> <span class='dim'>{_e(x['why'])}</span></li>"
                       for x in p["lost"])
        rows.append(f"<tr><td>{_e(p['level_zh'])}</td><td>{_mark(p['color'], p['zh'])}</td>"
                    f"<td>{_e('、'.join(p['reasons_zh']))}</td><td><a href='/page?id={p['id']}'>{title}</a>"
                    f"<br><span class='url'>{_e(p['url'])}</span>"
                    + (f"<br><span class='dim'>{_e('；'.join(extra))}</span>" if extra else "")
                    + (f"<ul class='lost'>{lost}</ul>" if lost else "") + "</td></tr>")
    if d.get("bucket"):  # looking at one category of the other addresses: the pages wait below a link
        out.append(f"<p class='dim'>（正在看其他网址的一类；<a href='/progress/site?k={key}'>显示网页</a>）</p>")
    elif rows:
        out.append("<table><tr><th>完整度</th><th>主标签</th><th>全部原因</th><th>网页</th></tr>" + "".join(rows) + "</table>")
    else:
        out.append("<p class='dim'>没有网页（或完整度还在算）。</p>")
    # the other addresses and presses, by the ten categories
    flt = "".join(f"<a href='/progress/site?k={key}&b={b}'>{_e(zh)} {n:,}</a>" for b, zh, n in d["bucket_counts"])
    out.append(f"<h2>其他网址和网页按钮（附件、图片、云端档案、日历、表单、第二轮、规则不抓……）</h2>"
               f"<p class='filters'>筛选：<a href='/progress/site?k={key}'>全部</a>{flt}</p>")
    if d.get("label"):  # looking at one kind of page: the other addresses wait below a link
        out.append(f"<p class='dim'>（正在看一种网页；<a href='/progress/site?k={key}'>显示其他网址</a>）</p>")
        return "<main id='psite'>" + "".join(out) + "</main>"
    if not d.get("bucket"):
        out.append(f"<p class='dim'>每一类最多列 {progress.PER_BUCKET} 个；点上面的类别看那一类的全部。</p>")
    rows = []
    for r in d["rows"]:
        color = progress.BUCKET_COLOR.get(r["bucket"], "red")
        reason = " ".join(x for x in (r["reason"], f"HTTP {r['status']}" if r["status"] not in (None, 200) else "") if x)
        if r["type"]:
            reason = f"网页类型 {r['type']}" + (f"：{r['type_why']}" if r["type_why"] else "") + (f"；{reason}" if reason else "")
        if r["what"] == "press":
            where = (f"网页按钮「{_e(r['title'])}」（<a href='/page?id={r['list_id']}'>在这一页</a>）"
                     f"<br><span class='url'>{_e(r['url'])}</span>")
        else:
            label = _e(r["title"]) or "<span class='dim'>（无标题）</span>"
            where = f"<a href='/page?id={r['id']}'>{label}</a><br><span class='url'>{_e(r['url'])}</span>"
        rows.append(f"<tr><td>{_mark(color, progress.BUCKET_ZH.get(r['bucket'], r['bucket']))}</td>"
                    f"<td title='{_e(r['result'])}'>{_e(r['zh'])}</td><td>{_e(reason)}</td><td>{where}</td></tr>")
    if rows:
        out.append("<table><tr><th>分类</th><th>结果</th><th>原因</th><th>网址</th></tr>" + "".join(rows) + "</table>")
    else:
        out.append("<p class='dim'>没有。</p>")
    return "<main id='psite'>" + "".join(out) + "</main>"

