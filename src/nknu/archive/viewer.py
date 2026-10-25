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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from selectolax.lexbor import LexborHTMLParser
from warcio.archiveiterator import ArchiveIterator

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


def build_index(root: Path) -> int:
    """Index the response records of every WARC file under <root>/warc, from where the last run stopped. Returns the
    number of records added."""
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

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.root / 'frontier.db'}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def locate(self, row) -> tuple[str, int] | None:
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

    @staticmethod
    def layout(title: str, body: str) -> str:
        return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
                "<style>body{font:14px/1.5 -apple-system,'PingFang TC',sans-serif;margin:16px;color:#222}"
                "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px 6px;"
                "text-align:left;vertical-align:top}th{background:#f4f4f4;position:sticky;top:0}"
                ".url{color:#666;font-size:12px;word-break:break-all}.dim{color:#888}.bad{color:#b00020}"
                ".meta th{width:9em}iframe.view{width:100%;height:75vh;border:1px solid #ccc}pre{white-space:pre-wrap}"
                "nav a{margin-right:12px}</style></head><body><nav><a href='/'>总览</a><a href='/issues'>问题清单</a>"
                f"<a href='/search'>搜索</a><a href='/choose'>选择放行</a></nav><h1>{html.escape(title)}</h1>{body}</body></html>")


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
            try:
                route = parts.path
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
