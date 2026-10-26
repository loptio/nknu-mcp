"""A local viewer of the archive, for the owner to check what was crawled: sites by unit, every URL with its status,
each page as extracted text, as HTML source, or as a preview that loads nothing from the network, and attachments
(PDF…) inline. Listens on 127.0.0.1 only; reads frontier.db read-only and the WARC files through an offset index
(<dir>/viewer-index.db, built incrementally: a file still being written is indexed up to its last complete record).
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from selectolax.lexbor import LexborHTMLParser
from warcio.archiveiterator import ArchiveIterator

from nknu.archive import progress

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
        self.token = secrets.token_urlsafe(16)  # only the choice page served by this process can save
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

    def choose(self, q: dict) -> str:
        data_path = self.root / "choices-data.json"
        if not data_path.exists():
            return self.layout("选择", "<p>还没算资讯量：在终端执行 <code>nknu archive choices</code>，再重新整理。</p>")
        saved = {}
        if (self.root / "choices.json").exists():
            saved = json.loads((self.root / "choices.json").read_text(encoding="utf-8")).get("choices", {})
        payload = json.dumps({"data": json.loads(data_path.read_text(encoding="utf-8")), "saved": saved},
                             ensure_ascii=False).replace("</", "<\\/")
        return self.layout("选择要放行或挡掉的资料", CHOOSE_PAGE.replace("__TOKEN__", self.token)
                           .replace("__PAYLOAD__", payload))

    def save_choices(self, body: bytes) -> dict:
        got = json.loads(body.decode("utf-8"))
        choices = {str(k): str(v) for k, v in (got.get("choices") or {}).items()}
        notes = str(got.get("notes") or "")[:5000]
        record = {"saved_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"), "choices": choices,
                  "notes": notes}
        (self.root / "choices.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "saved_at": record["saved_at"], "n": len(choices)}

    # ---------------------------------------------------------------- the progress dashboard (design §14)
    def dashboard(self) -> progress.Dashboard:
        """The dashboard's numbers (progress.Dashboard), made at the first /progress: read-only, never build_index."""
        with self._dash_lock:
            if self._dash is None:
                self._dash = progress.Dashboard(self.root)
            return self._dash

    def progress(self, q: dict) -> str:
        """/progress, or with part=main only its <main> (what the page asks for every 30 seconds)."""
        main = progress_main_html(self.dashboard().main())
        if q.get("part", [""])[0] == "main":
            return main
        return self.layout("进度看板", PROGRESS_CSS + main + PROGRESS_JS)

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
                "nav a{margin-right:12px}</style></head><body><nav><a href='/progress'>进度</a><a href='/'>总览</a>"
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
                if route in ("/progress", "/progress/site"):
                    fn = viewer.progress if route == "/progress" else viewer.progress_site
                    return self.send(200, "text/html; charset=utf-8", fn(q).encode(), {"Cache-Control": "no-store"})
                if route in ("/", "/site", "/search", "/issues", "/page", "/choose"):
                    fn = {"/": viewer.home, "/site": viewer.site, "/search": viewer.search, "/issues": viewer.issues,
                          "/page": viewer.page, "/choose": viewer.choose}[route]
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
            # a page on another site could post to localhost: only this process's own choice page (its token, its
            # host, a JSON body) may save
            host_ok = (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")
            if (urlsplit(self.path).path != "/choose/save" or not host_ok
                    or self.headers.get("X-Choice-Token") != viewer.token
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                return self.send(403, "text/plain; charset=utf-8", b"forbidden")
            size = int(self.headers.get("Content-Length") or 0)
            if size > 1_000_000:
                return self.send(413, "text/plain; charset=utf-8", b"too large")
            try:
                result = viewer.save_choices(self.rfile.read(size))
            except Exception as exc:  # noqa: BLE001 - a malformed body is the client's problem
                return self.send(400, "text/plain; charset=utf-8", f"{type(exc).__name__}".encode())
            return self.send(200, "application/json", json.dumps(result).encode())

    return Handler


def serve(root: Path, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(Viewer(root)))


# ---------------------------------------------------------------- the progress dashboard, drawn (design §14)
# progress.py works the numbers out; these functions only draw them. Words for the owner, never the codes (§14.5: the
# codes go to the hover text and the site pages); colour only with a sign and words (✓ 正常 / ⚠ 要看 / ✗ 出错)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_SIGN = {"green": "✓", "yellow": "⚠", "red": "✗", "none": ""}
PROGRESS_CSS = """<style>
.c-green{color:#1a7f37}.c-yellow{color:#9a6700}.c-red{color:#b00020}.c-none{color:inherit}
.pbar{display:inline-block;width:280px;max-width:60vw;height:10px;background:#e4e4e4;border-radius:5px;
vertical-align:middle;margin-right:8px}.pbar span{display:block;height:10px;background:#888;border-radius:5px}
.big{font-size:16px}.chips span{margin-right:16px}main section{margin:18px 0}main h2{font-size:17px;margin:8px 0}
details summary{cursor:pointer;font-weight:600;margin:6px 0}pre.cmd{background:#f6f6f6;padding:8px;border-radius:4px}
td.num{text-align:right;white-space:nowrap}main th{white-space:nowrap}ul.lost{margin:2px 0 0 0;padding-left:18px;font-size:12px}
.filters a{margin-right:10px;white-space:nowrap}
</style>"""
PROGRESS_JS = """<script>
(function(){
  var busy = false;
  function tick(){
    if (document.hidden || busy) return;   // a hidden page asks nothing (Page Visibility API)
    busy = true;
    fetch('/progress?part=main', {cache: 'no-store'}).then(function(r){ return r.ok ? r.text() : null; })
    .then(function(h){
      busy = false;
      var old = document.getElementById('pmain');
      if (!h || !old) return;
      var t = document.createElement('template'); t.innerHTML = h;
      var n = t.content.getElementById('pmain'); if (!n) return;
      old.querySelectorAll('details[id]').forEach(function(d){   // what the owner opened or closed stays so
        var m = n.querySelector('#' + d.id); if (m) m.open = d.open; });
      old.replaceWith(n);
    }).catch(function(){ busy = false; });
  }
  setInterval(tick, 30000);
  document.addEventListener('visibilitychange', function(){ if (!document.hidden) tick(); });
})();
</script>"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _mark(color: str, words: str, sign: str | None = None, tip: str = "") -> str:
    sign = _SIGN.get(color, "") if sign is None else sign
    title = f" title='{_e(tip)}'" if tip else ""
    return f"<span class='c-{color}'{title}>{_e(sign + ' ' if sign else '')}{_e(words)}</span>"


def _n(x) -> str:
    return f"{x or 0:,}"


def _server_name(s: dict) -> str:
    q = s["queue"]
    named = {"?": "还没查地址的主机", "dns-fail": "查不到地址的主机", "google": "Google 的网页",
             "googleapis": "Google 云端硬盘和日历（读资料的接口）"}
    if q in named:
        return named[q]
    more = f"（共 {s['n_hosts']} 个主机名）" if s.get("n_hosts", 0) > 1 else ""
    return f"{s['main_host']} 那台{more}"


def _run_html(snap: dict) -> str:
    now, run = snap["at"], snap["run"]
    out = []
    if run["state"] == "running":
        line = (f"<b>● 在跑</b>　{_e(progress.fmt_clock(run['started_at'], now))} 开始，已跑 "
                f"{_e(progress.fmt_dur(now - run['started_at']) if run['started_at'] else '—')}")
        if run["last_request"]:
            line += f"，最后一个请求 {_e(progress.fmt_dur(now - run['last_request']))}前"
        out.append(line)
        if run["stop_file"]:
            out.append(_mark("yellow", "已经要它停下（STOP 档在）：手上的请求做完就停"))
        if run["stalled"]:
            out.append(_mark("yellow", f"有 {len(run['stalled'])} 台服务器 15 分钟没有请求，却有该抓的网址（可能卡住了）"))
    elif run["state"] == "never":
        out.append("还没开跑过")
    elif run["state"] == "crashed":
        out.append(_mark("red", f"上次没正常结束（{progress.fmt_clock(run['started_at'], now)} 开始，程序被关掉或当掉）："
                                "再跑一次会接着抓，已经抓过的不重抓"))
    else:
        reason = run["end_reason"] or "stop"
        when = progress.fmt_clock(run["ended_at"], now)
        if run["state"] == "bad_end":
            out.append(_mark("red", f"上次 {when} {progress.RUN_END_ZH.get(reason, reason)}", tip=reason))
        elif reason == "waiting":
            out.append(_mark("yellow", f"上次 {when} 先停下等重试：还有 {_n(run['pending'])} 个网址在等", sign="⏸"))
            if run["next_due"]:
                out.append(f"<b>最早 {_e(progress.fmt_clock(run['next_due'], now))} 起</b>再执行一次开跑命令就会接着抓"
                           "（命令在最下面）")
        elif reason == "finished":
            sc = snap.get("site_counts") or {}
            if sc.get("all") and sc.get("done") == sc.get("all"):
                out.append(_mark("green", f"上次 {when} 抓完了，每个网站都爬完"))
            else:  # review 10-26: nothing left to fetch is no 爬完 (§10.5): neutral, never green
                out.append(f"<b>上次 {_e(when)} 这次 run 没有要抓的了</b>（不等于爬完：看下面每个网站的状态；停下以后还要 "
                           "jscheck → reconcile → verify，命令在最下面）")
        else:
            out.append(f"上次 {_e(when)} {_e(progress.RUN_END_ZH.get(reason, reason))}")
    for p in run["pauses"]:
        out.append(_mark("yellow", f"{p['what']}暂停到 {progress.fmt_clock(p['until'], now)}", tip=p["why"]))
    camp = snap["campus"]
    if camp["last"] == "on":
        out.append(_mark("yellow", f"这台机器现在在校内网络：照抓，每个请求都记下是在校内抓的（有些页面校内看得到全文、校外只看到"
                                   f"标题）；已有 {camp['on']:,} 个请求在校内抓"))
    elif camp["on"]:
        out.append(f"<span class='dim'>之前有 {camp['on']:,} 个请求是在校内网络抓的</span>")
    if run.get("gap"):
        lo, hi = (list(run["gap"]) + [None, None])[:2]
        out.append(f"<span class='dim'>这次每个请求之后等 {_e(lo)}–{_e(hi)} 秒（服务器变慢时等更久）</span>")
    return "<section><p class='big'>" + "<br>".join(out) + "</p></section>"


def _total_html(snap: dict) -> str:
    t, est, now = snap["total"], snap["eta"], snap["at"]
    pct = round(100 * t["pct"])
    grow = ""
    if t["all"] > t["all_hour_ago"]:
        grow = f"（一小时前共 {_n(t['all_hour_ago'])}，还在增加）"
    lab = f"；另有 {_n(t['lab'])} 个只限校内网络，等搬到实验室" if t["lab"] else ""
    out = [(f"<p class='big'><b>总进度</b>　<span class='pbar'><span style='width:{pct}%'></span></span>{pct}%　"
            f"{_n(t['done'])} / {_n(t['all'])}{_e(grow)}{_e(lab)}</p>")]
    if est["slowest"] is None:
        out.append("<p><b>预计</b>　没有要抓的了</p>")
    else:
        s = est["servers"][est["slowest"]]
        slow = next((x for x in snap["servers"] if x["queue"] == est["slowest"]), None)
        name = _server_name(slow) if slow else est["slowest"]
        guess = "（还没有速度纪录，先照每个 4 秒估计）" if est["guess"] else ""
        if snap["run"]["state"] == "running":
            when = (f"<b>预计</b>　{_e(progress.fmt_clock(est['finish_at'], now))} 左右抓完（约 "
                    f"{_e(progress.fmt_dur(est['seconds']))}）")
        else:
            when = f"<b>还要跑</b>　约 {_e(progress.fmt_dur(est['seconds']))}（照最近的速度，从再开跑算起）"
        out.append(f"<p>{when}——最慢的是 {_e(name)}：还剩 {_n(s['left'])} 个，每个约 {s['per_row']:.1f} 秒{_e(guess)}"
                   + (f"；{_e('；'.join(s['why']))}" if s["why"] else "") + "</p>")
    pol = snap["polite"]
    chips = [_mark("red", f"礼貌违反 {pol['hard']} 次") if pol["hard"] else _mark("green", "礼貌正常")]
    checks = snap.get("checks")
    if checks is None:
        chips.append(_mark("yellow", "漏抓检查没检查"))
    else:
        bad = sum(c["result"] in ("warn", "fail", "skip") for c in checks)
        chips.append(_mark("yellow" if bad else "green", f"漏抓检查 {bad} 项要看" if bad else "漏抓检查都通过"))
    if t["lacking"]:
        chips.append(_mark("yellow", f"有缺 {t['lacking']:,} 个（{t['retry']:,} 个会自动重试）"))
    n_err = sum(e["n"] for e in snap["errors"])
    chips.append(f"<span>最近一小时出错 {n_err:,} 次</span>")
    out.append("<p class='chips'>" + "".join(f"<span>{c}</span>" for c in chips) + "</p>")
    return "<section>" + "".join(out) + "</section>"


def _tiers_html(snap: dict) -> str:
    rows = []
    for t in snap["tiers"]:
        rows.append(f"<tr><td>{_e(t['name'])}</td><td class='num'>{t['sites']}</td>"
                    f"<td class='num'>{t['records_done']}（爬完 {t['done']}）</td>"
                    + "".join(f"<td class='num'>{_n(t[c][0])} / {_n(t[c][1])}</td>" for c in ("page", "file", "image"))
                    + "</tr>")
    r2 = snap.get("round2")
    if r2:
        rows.append(f"<tr><td colspan='6'><b>第二轮</b>（这一轮只记下网址，共 {_n(r2['total'])} 个）</td></tr>")
        for x in r2["by_why"]:
            rows.append(f"<tr><td colspan='3' title='{_e(x['why'])}'>　{_e(x['zh'])}（{_n(x['sites'])} 个网站）</td>"
                        f"<td class='num' colspan='3'>{_n(x['n'])} 个网址</td></tr>")
    return ("<section><h2>按重要程度</h2><table><tr><th>重要程度</th><th>网站</th><th>纪录齐了</th><th>网页（处理过 / 全部）</th>"
            "<th>附件</th><th>图片</th></tr>" + "".join(rows) + "</table></section>")


def _servers_html(snap: dict) -> str:
    rows = []
    for s in snap["servers"]:
        sp, et = s.get("speed") or {}, s.get("eta") or {}
        if sp.get("took") is not None:
            per = f"{sp['c'] * sp['k']:.1f} 秒（回应 {sp['took']:.1f} + 等待 {sp['wait']:.1f}）"
        else:
            per = "—"
        if s["delay"]:
            per += f"；网站要求每次隔 {s['delay']:g} 秒"
        if sp.get("new_share") is not None:
            per += f"；新连线 {sp['new_share']:.0%}"
        names = s.get("site_names") or []
        sites = (f"{'、'.join(names[:1])}等 {len(s['sites'])} 个" if len(s["sites"]) > 1 else "、".join(names)) or "—"
        hour = f"{sp.get('hour_work', 0):,}" if sp else "0"
        left = f"{_n(s['left'])}" + (f"（约 {progress.fmt_dur(et['t'])}）" if et.get("t") else "")
        st = s["status"]
        rows.append(f"<tr><td>{_e(_server_name(s))}</td><td>{_e(sites)}</td><td class='num'>{_n(s['fetched'])}</td>"
                    f"<td class='num'>{_e(left)}</td><td class='num'>{hour}</td><td>{_e(per)}</td>"
                    f"<td>{_mark(st['color'], st['words'], st['sign'])}</td></tr>")
    return ("<section><h2>每台服务器（最慢的在最上面）</h2><table><tr><th>服务器</th><th>上面的网站</th><th>已抓</th><th>还剩</th>"
            "<th>最近一小时</th><th>每个要多久</th><th>状态</th></tr>" + "".join(rows) + "</table></section>")


def _site_mark(s: dict) -> str:
    """A site's state (§10.5), with its discovery coverage beside 「✓ 爬完」 as the estimate it is (it runs high: both
    lists are found by following links; review 10-26) and its warnings, each in words, under it."""
    sign, words, color = progress.STATE_SHOW.get(s["state"], ("⚠", "没检查", "yellow"))
    cover = s.get("cover_cc")
    if s["state"] == "done" and cover is not None:
        words += f" · 覆盖率估计 {cover:.0%}（偏高）"
    tip = "；".join([*(s.get("why") or []), *([s["cover_line"]] if s.get("cover_line") else [])])
    out = _mark(color, words, sign, tip=tip)
    for w in s.get("warnings") or []:
        out += f"<br>{_mark('yellow', w)}"
    return out


def _sites_html(snap: dict) -> str:
    c = snap["site_counts"]
    head = (f"全部 {c['all']} · 有问题 {c['problem']} · 还在抓 {c['crawling']} · 纪录齐了、还没对账 {c['unreconciled']} · "
            f"爬完 {c['done']}" + (f" · 没检查 {c['unchecked']}" if c["unchecked"] else ""))
    rows = []
    for s in snap["sites"]:
        home = f"<br><span class='url'>{_e(s['home'])}</span>" if s.get("home") else ""
        rows.append(f"<tr><td><a href='/progress/site?k={quote(s['site'])}'>{_e(s['name'])}</a>{home}</td>"
                    f"<td>{_e(progress.TIER_NAME.get(s['tier'], ''))}</td><td>{_e(s['summary'])}</td>"
                    f"<td>{_site_mark(s)}</td></tr>")
    return (f"<section><h2>每个网站（{_e(head)}）</h2><p class='dim'>先全校重要、再本系重要；同一组里有连接失败的、缺得多的排前面。"
            "点网站名称看它的每一页和每个网址。</p><table><tr><th>网站</th><th>重要程度</th><th>缺什么</th><th>状态</th></tr>"
            + "".join(rows) + "</table></section>")


def _buckets_html(snap: dict) -> str:
    rows = "".join(f"<tr><td>{_mark(b['color'], b['zh'])}</td><td class='num'>{_n(b['urls'])}</td>"
                   f"<td class='num'>{_n(b['presses'])}</td><td>{_e(b['then'])}</td></tr>"
                   for b in snap["buckets"] if b["urls"] or b["presses"])
    labels = snap.get("page_labels") or []
    pages = ("<p>网页按完整度：" + "、".join(f"{_e(x['zh'])} {_n(x['n'])}" for x in labels) + "</p>") if labels else (
        "<p class='c-yellow'>⚠ 网页的完整度没检查（还在算）</p>")
    return ("<section><h2>缺什么（加总）</h2><table><tr><th>分类</th><th>网址</th><th>网页按钮</th><th>会怎样</th></tr>"
            + rows + "</table><p class='dim'>网址不含网页：网页照完整度另算（下面这行）。</p>" + pages + "</section>")


def _checks_html(snap: dict) -> str:
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
    return f"<section><details id='d-checks'><summary>{_e(title)}</summary>{body}</details></section>"


def _round2_html(snap: dict) -> str:
    r2 = snap.get("round2")
    if not r2:
        return "<section><details id='d-round2'><summary>第二轮清单（还在算）</summary></details></section>"
    by_src = "、".join(f"{_e(x['source'])} {_n(x['n'])}" for x in r2["by_source"]) or "—"
    doms = "、".join(f"{_e(x['domain'])} {_n(x['n'])}" for x in r2["domains"]) or "—"
    rows = "".join(f"<tr><td title='{_e(x['why'])}'>{_e(x['zh'])}</td><td class='num'>{_n(x['sites'])}</td>"
                   f"<td class='num'>{_n(x['n'])}</td></tr>" for x in r2["by_why"])
    return (f"<section><details id='d-round2'><summary>第二轮清单（共 {_n(r2['total'])} 个网址，这一轮只记下来）</summary>"
            f"<table><tr><th>哪一批</th><th>网站</th><th>网址</th></tr>{rows}</table><p>按起点来源：{by_src}</p>"
            f"<p>校外网域（前 20）：{doms}</p></details></section>")


def _polite_html(snap: dict) -> str:
    p = snap["polite"]
    if p["hard"]:
        head = _mark("red", f"礼貌违反 {p['hard']} 次（同时连两条 {p['overlap']}、间隔太短 {p['gap']}、没照网站要求的间隔 "
                            f"{p['delay']}）：要先停下来修")
    else:
        head = _mark("green", f"礼貌正常：查了 {p['checked']:,} 个请求，每台服务器同时只连 1 条、间隔都够")
    warn = (f"<p class='c-yellow'>⚠ 服务器变慢时没多等 {p['slow']} 次（我们自己加的规则，只提醒）</p>" if p["slow"] else "")
    ex = p["hard_examples"] + p["warn_examples"]
    lst = ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in ex) + "</ul>") if ex else ""
    return (f"<section><details id='d-polite'{' open' if p['hard'] else ''}><summary>礼貌检查</summary>"
            f"<p>{head}</p>{warn}{lst}</details></section>")


def _errors_html(snap: dict) -> str:
    now = snap["at"]
    rows = []
    for e in snap["errors"]:
        color = "red" if e["level"] == "error" else "yellow"
        n = f"{e['n']:,} 次" if e["n"] > 1 else ""
        rows.append(f"<tr><td>{_e(progress.fmt_clock(e['at'], now))}</td><td>{_e(e['where'])}</td>"
                    f"<td>{_mark(color, e['plain'], tip=e['raw'])}</td><td class='num'>{_e(n)}</td></tr>")
    body = ("<table><tr><th>时间</th><th>哪里</th><th>出了什么事、会怎样</th><th>次数</th></tr>" + "".join(rows)
            + "</table>") if rows else "<p class='c-green'>✓ 最近一小时没有出错</p>"
    return f"<section><h2>最近一小时的错误</h2>{body}</section>"


def _commands_html() -> str:
    c = progress.COMMANDS
    return ("<section><h2>停止和续跑</h2><p>看板只看不改，这里没有按钮：要停或要跑，在终端机执行下面的命令（续跑会接着抓，已经抓过的不重抓）。</p>"
            f"<p>停下：</p><pre class='cmd'>{_e(c['stop'])}</pre><p>开跑或续跑（在专案资料夹里）：</p>"
            f"<pre class='cmd'>{_e(c['run'])}</pre><p class='dim'>在实验室的机器上先执行：</p>"
            f"<pre class='cmd'>{_e(c['lab'])}</pre><p>停下以后（每个网站要这几步都过了才算爬完）：</p>"
            f"<pre class='cmd'>{_e(c['after'])}</pre></section>")


def progress_main_html(snap: dict) -> str:
    """The dashboard's <main> (design §14.2, §14.6): what /progress?part=main answers every 30 seconds."""
    now = snap["at"]
    if "problem" in snap:
        return (f"<main id='pmain'><p class='dim'>每 30 秒自动更新 · 上次 {_e(progress.fmt_time(now))}</p>"
                f"<p class='c-red'>✗ {_e(snap['problem'])}</p></main>")
    if snap.get("slow_at"):
        slow = f"完整度更新于 {progress.fmt_clock(snap['slow_at'], now)}"
    else:
        slow = "完整度还在算（第一次要几秒）" if not snap.get("slow_error") else f"完整度算不出来：{snap['slow_error']}"
    tiers = {t["tier"]: t["sites"] for t in snap["tiers"]}
    head = (f"<p class='dim'>每 30 秒自动更新 · 上次 {_e(progress.fmt_time(now))} · {_e(slow)}</p>"
            f"<p>第一轮：{sum(tiers.values())} 个重要网站（全校重要 {tiers.get(0, 0)}、本系重要 {tiers.get(1, 0)}）</p>")
    return ("<main id='pmain'>" + head + _run_html(snap) + _total_html(snap) + _tiers_html(snap) + _servers_html(snap)
            + _sites_html(snap) + _buckets_html(snap) + _checks_html(snap) + _round2_html(snap) + _polite_html(snap)
            + _errors_html(snap) + _commands_html() + "</main>")


def progress_site_html(d: dict) -> str:
    """One site's page (design §14.2): its pages by completeness, then its other addresses and presses by the ten
    categories, each failure with its reason (the codes as hover text)."""
    now, key = d["at"], quote(d["site"])
    sign, words, color = progress.STATE_SHOW.get(d["state"], ("⚠", "没检查", "yellow"))
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


CHOOSE_PAGE = """
<style>
.sec{margin:28px 0 8px}.card{border:1px solid #ddd;border-radius:8px;padding:10px 12px;margin:10px 0}
.card h3{margin:0 0 4px;font-size:15px}.meta{color:#555;font-size:13px}.bar{height:8px;background:#e8eef8;border-radius:4px;
margin:6px 0}.bar span{display:block;height:8px;background:#3b6fd6;border-radius:4px}.opts label{display:block;margin:2px 0;
cursor:pointer}.rec{color:#0a7b34;font-size:12px}.samples{font-size:12px;color:#444;margin:4px 0 0 0}
.samples summary{cursor:pointer;color:#3b6fd6}.docs{font-weight:600}#bar{position:sticky;bottom:0;background:#fff;
border-top:1px solid #ccc;padding:10px 0}button{font-size:15px;padding:6px 16px}textarea{width:100%;height:60px}
.changed{border-color:#3b6fd6}
</style>
<p>每张卡片是一项待决定的事。<b>资讯量</b>＝这一项涉及的不同文件或页面数（蓝条按同一组里的最大值比例画），
样本是链接文字或页面标题。绿字是我的建议，默认已选好；改完按最下面的「保存」。</p>
<div id="app"></div>
<div id="bar"><textarea id="notes" placeholder="其他想补充的（可留空）"></textarea>
<button onclick="save()">保存选择</button> <span id="status" class="meta"></span></div>
<script>
const P = __PAYLOAD__;
const app = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let html = '';
P.data.sections.forEach((sec, si) => {
  const max = Math.max(1, ...sec.items.map(i => i.docs || 0));
  html += `<h2 class="sec">${esc(sec.title)}（${sec.items.length} 项）</h2>`;
  sec.items.forEach((it, ii) => {
    const chosen = P.saved[it.id] || it.recommend;
    const recLabel = (it.options.find(o => o[0] === it.recommend) || [, ''])[1];
    html += `<div class="card" id="c${si}_${ii}"><h3>${esc(it.title)}</h3>
      <div class="meta">资讯量：<span class="docs">${(it.docs||0).toLocaleString()}</span>　${esc(it.stats)}</div>
      <div class="bar"><span style="width:${Math.round(100*(it.docs||0)/max)}%"></span></div>
      <div class="meta">为什么被挡：${esc(it.why)}　<span style="color:#888">（${esc(it.where)}）</span></div>
      ${it.units && it.units.length ? `<div class="meta">谁链到它：${esc(it.units.join('、'))}</div>` : ''}
      ${it.samples && it.samples.length ? `<details class="samples"><summary>看样本（${it.samples.length}）</summary><ul>${it.samples.map(s => `<li>${esc(s)}</li>`).join('')}</ul></details>` : ''}
      <div class="opts">${it.options.map(o => `<label><input type="radio" name="${esc(it.id)}" value="${esc(o[0])}" ${o[0]===chosen?'checked':''}> ${esc(o[1])}</label>`).join('')}</div>
      <div class="rec">建议：${esc(recLabel)}</div></div>`;
  });
});
app.innerHTML = html;
async function save() {
  const choices = {};
  P.data.sections.forEach(sec => sec.items.forEach(it => {
    const el = document.querySelector(`input[name="${CSS.escape(it.id)}"]:checked`);
    if (el) choices[it.id] = el.value;
  }));
  const r = await fetch('/choose/save', {method: 'POST', headers: {'Content-Type': 'application/json',
    'X-Choice-Token': '__TOKEN__'}, body: JSON.stringify({choices, notes: document.getElementById('notes').value})});
  const j = r.ok ? await r.json() : null;
  document.getElementById('status').textContent = j ? `已保存 ${j.n} 项（${j.saved_at}）` : '保存失败：' + r.status;
}
</script>
"""
