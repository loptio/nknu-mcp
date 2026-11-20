"""Opening the five JavaScript sites' pages in a browser and archiving what they show (spec 2026-11-17 jsfetch,
jsfetch.py). Offline: a made-up browser drives the bridge with a test transport behind the crawl's own client (so these
run without Playwright or Chrome); tests marked browser open this machine's Chrome through Playwright on pages a server
on 127.0.0.1 serves (skipped, with the reason, when either is missing). No address here reaches the school: hosts are
the school's names answered by httpx.MockTransport, or the loopback server. Page texts are made up, with no names."""

from __future__ import annotations

import asyncio
import contextlib
import html as htmllib
import http.server
import io
import itertools
import json
import random
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin

import httpx
import pytest
from warcio.archiveiterator import ArchiveIterator

from nknu.archive import jscheck, jsfetch
from nknu.archive.crawler import archive_client, hold_run_lock, resolve
from nknu.archive.frontier import Frontier
from nknu.archive.jscheck import Ask
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.warc import WarcSink

NOW = datetime(2026, 11, 17, 3, 30, tzinfo=UTC)
ORD = "https://ord.nknu.edu.tw"
HTML = {"content-type": "text/html; charset=utf-8"}
JSON_T = {"content-type": "application/json; charset=utf-8"}
_REFRESH = re.compile(r'http-equiv="refresh" content="0;url=([^"]+)"')


def page(*paras: str, extra: str = "") -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f"<html><head><title>測試頁</title></head><body>{body}{extra}</body></html>"


def lines(word: str, n: int) -> list[str]:
    return [f"{word}第{i}則：這是一段編出來的測試文字，只用來湊滿字數。" for i in range(n)]


class Server:
    """The school as httpx.MockTransport answers it: url -> (status, headers, body) or a function of the request
    (test_jscheck.py's)."""

    def __init__(self, routes: dict):
        self.routes = dict(routes)
        self.hits: list[httpx.Request] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.hits.append(request)
        got = self.routes.get(str(request.url))
        if callable(got):
            return got(request)
        if got is None:
            return httpx.Response(404, headers=HTML, text=page("找不到這一頁"))
        status, headers, body = got
        return httpx.Response(status, headers=headers, content=body.encode() if isinstance(body, str) else body)

    def asked(self) -> list[tuple[str, str]]:
        return [(r.method, str(r.url)) for r in self.hits]


class Clock:
    """Time passes only when something waits (test_jscheck.py's)."""

    def __init__(self, t: float = 1_000_000.0):
        self.t, self._lock = t, threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, s: float) -> None:
        with self._lock:
            self.t += max(s, 0.0)

    async def asleep(self, s: float) -> None:
        self.sleep(s)
        await asyncio.sleep(0)


@dataclass
class FakePage:
    rendered: str  # what the browser shows once loaded
    asks: list[Ask] = field(default_factory=list)  # the page's requests after its own
    slow: bool = False
    parallel: bool = False  # the page asks them all at once, as a browser does
    goes_to: str | None = None  # where a script sends it after loading
    error: str | None = None  # what the browser could not do once loaded (reading the page, …)


class FakeBrowser:
    """A browser that asks the bridge what a real one would (the page, its 3xx hops, its requests) and shows what the
    test says (test_jscheck.py's, without the parts these tests never use)."""

    def __init__(self, pages: dict[str, FakePage], *, missing: bool = False,
                 on_open: Callable[[str], None] | None = None, breaks: frozenset[str] = frozenset()):
        self.pages, self.missing, self.on_open, self.breaks = pages, missing, on_open, breaks
        self.opened: list[str] = []
        self.started = self.closed = False

    async def start(self) -> None:
        if self.missing:
            raise jscheck.BrowserMissing("没装 Playwright（测试）")
        self.started = True

    async def open(self, url: str, bridge: jscheck.PageBridge, shot: Path | None = None) -> jscheck.Observation:
        self.opened.append(url)
        if self.on_open:
            self.on_open(url)
        if url in self.breaks:
            raise RuntimeError("Target page, context or browser has been closed (test)")
        fp = self.pages.get(url, FakePage(""))
        cur = url
        reply = await bridge.ask(Ask(cur, navigation=True))
        for _ in range(5):
            m = None if reply.abort else _REFRESH.search(reply.body.decode("utf-8", "replace"))
            if m is None:
                break
            cur = urljoin(cur, htmllib.unescape(m.group(1)))
            reply = await bridge.ask(Ask(cur, navigation=True))
        if reply.abort:
            return bridge.observation(final_url="chrome-error://chromewebdata/", rendered="<html></html>")
        if fp.parallel:
            await asyncio.gather(*(bridge.ask(a) for a in fp.asks))
        else:
            for a in fp.asks:
                await bridge.ask(a)
        final, rendered = cur, fp.rendered
        if fp.goes_to:
            r2 = await bridge.ask(Ask(fp.goes_to, navigation=True))
            final = fp.goes_to if not r2.abort else "chrome-error://chromewebdata/"
            rendered = "<html></html>" if r2.abort else rendered
        return bridge.observation(final_url=final, rendered="" if fp.error else rendered, slow=fp.slow,
                                  error=fp.error)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def school():
    held = schoollock.hold()
    try:
        yield held
    finally:
        held.close()


def make_fetcher(root: Path, server: Server, browser, **kw):
    clock = Clock()
    fr = Frontier(root / "frontier.db")
    fx = jsfetch.JsFetcher(root, fr, browser=browser,
                           client_factory=lambda warc: archive_client(warc, transport=httpx.MockTransport(server),
                                                                      clock=clock, sleep=clock.sleep),
                           resolver=lambda h: (f"ip:{h}", None, h), clock=clock, sleep=clock.sleep,
                           rng=random.Random(7), **kw)
    return fx, fr, clock


def _record_at(path: Path, offset: int):
    with open(path, "rb") as fh:
        fh.seek(offset)
        rec = next(iter(ArchiveIterator(fh)))
        return rec.rec_type, dict(rec.rec_headers.headers), rec.content_stream().read()


def _response(root: Path, url: str = "https://ord.nknu.edu.tw/?p=list", body: str = "<html>外框</html>"):
    sink = WarcSink(root / "warc", "jsfetch-ip_x", 1, NOW)
    snap = Snapshot(target=Target(url), url=url, status=200, headers={"content-type": "text/html; charset=utf-8"},
                    body=body.encode(), text=body, fetched_at=NOW, sha256="0" * 64)
    offset, _ = sink.write(snap, {"Accept": "*/*"})
    sink.close()
    return str(sink.path.relative_to(root)), offset


# ---------------------------------------------------------------- Task 1: rendered records and the js_render table


def test_a_rendered_page_is_a_conversion_record_that_reads_back(tmp_path):
    w = jsfetch.RenderedWriter(tmp_path, 42, NOW)
    a = w.write("https://ord.nknu.edu.tw/?p=list&id=7", "<html><div name=\"data_content\">研發處公告內容</div></html>",
                "<urn:uuid:11111111-2222-3333-4444-555555555555>")
    b = w.write("https://ite.nknu.edu.tw/?p=billboard", "<html>系所公告</html>", None)
    w.close()
    assert a[0] == b[0] == "warc/2026/11/17/jsfetch-rendered-42.warc.gz"
    assert a[1] != b[1]
    assert jsfetch.read_rendered(tmp_path, *a) == ("https://ord.nknu.edu.tw/?p=list&id=7",
                                                   "<html><div name=\"data_content\">研發處公告內容</div></html>")
    assert jsfetch.read_rendered(tmp_path, *b) == ("https://ite.nknu.edu.tw/?p=billboard", "<html>系所公告</html>")
    kind, heads, _ = _record_at(tmp_path / a[0], a[1])
    assert kind == "conversion"
    assert heads["WARC-Refers-To"] == "<urn:uuid:11111111-2222-3333-4444-555555555555>"
    assert heads["Content-Type"] == "text/html; charset=utf-8"
    _, heads_b, _ = _record_at(tmp_path / b[0], b[1])
    assert "WARC-Refers-To" not in heads_b


def test_reading_a_response_record_as_rendered_is_refused(tmp_path):
    warc, offset = _response(tmp_path)
    with pytest.raises(ValueError):
        jsfetch.read_rendered(tmp_path, warc, offset)
    with pytest.raises(ValueError):
        jsfetch.read_rendered(tmp_path, warc, offset + 3)


def test_record_id_of_a_response(tmp_path):
    warc, offset = _response(tmp_path)
    rid = jsfetch.record_id_at(tmp_path, warc, offset)
    assert rid is not None and rid.startswith("<urn:uuid:")
    assert jsfetch.record_id_at(tmp_path, warc, offset + 3) is None
    assert jsfetch.record_id_at(tmp_path, "warc/none.warc.gz", 0) is None


def test_a_failed_write_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    w = jsfetch.RenderedWriter(tmp_path, 1, NOW)
    first = w.write("https://see.nknu.edu.tw/", "<html>一</html>", None)
    size = (tmp_path / first[0]).stat().st_size

    def broken(data):
        raise OSError("disk full (test)")

    monkeypatch.setattr(w, "_raw_write", broken)
    with pytest.raises(OSError):
        w.write("https://see.nknu.edu.tw/b", "<html>二</html>", None)
    assert (tmp_path / first[0]).stat().st_size == size
    w.close()
    assert jsfetch.read_rendered(tmp_path, *first)[1] == "<html>一</html>"


def _ledger(tmp_path: Path) -> Frontier:
    return Frontier(tmp_path / "frontier.db")


def _page_row(f: Frontier, url: str, fetched_at: str = "2026-11-16T00:00:00+00:00"):
    host = url.split("/")[2]
    f.x("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status, sniff, ctype, fetched_at, "
        "owner_site, kind0) VALUES (?,?,?,?,1,'page',1,'link','done',200,'html','text/html',?,?,'page')",
        (url, url.split("?", 1)[0], host, host, fetched_at, host))
    return f.row(url)


def test_js_render_rows_are_added_never_replaced(tmp_path):
    f = _ledger(tmp_path)
    try:
        jsfetch.ensure_table(f)
        jsfetch.ensure_table(f)
        row = _page_row(f, "https://ord.nknu.edu.tw/?p=list&category=news")
        got = jsfetch.Rendered(url=row["url"], final_url=row["url"], html="<html>x</html>", verdict="js",
                               raw_chars=60, chars=900, new_chars=840,
                               links=["https://ord.nknu.edu.tw/?p=list&id=9", "https://ord.nknu.edu.tw/?p=list&id=1"],
                               incomplete=None, strike=False, warc="warc/a.warc.gz", warc_offset=7,
                               requests={"sent": 3, "unsent": {"other_host": 2}})
        jsfetch.record(f, row, got, 1, at=NOW)
        jsfetch.record(f, row, jsfetch.Rendered(url=row["url"], final_url=row["url"], html="", incomplete="slow",
                                                strike=True), 2, at=NOW)
        rows = f.q("SELECT * FROM js_render ORDER BY run")
        assert [r["run"] for r in rows] == [1, 2]
        r1 = rows[0]
        assert (r1["url_id"], r1["fetched_at"], r1["final_url"], r1["warc"], r1["warc_offset"]) == (
            row["id"], "2026-11-16T00:00:00+00:00", row["url"], "warc/a.warc.gz", 7)
        assert (r1["raw_chars"], r1["chars"], r1["new_chars"], r1["verdict"]) == (60, 900, 840, "js")
        assert json.loads(r1["links"]) == ["https://ord.nknu.edu.tw/?p=list&id=1",
                                           "https://ord.nknu.edu.tw/?p=list&id=9"]
        assert json.loads(r1["requests"]) == {"sent": 3, "unsent": {"other_host": 2}}
        assert (r1["incomplete"], r1["strike"], r1["at"]) == (None, 0, "2026-11-17T03:30:00+00:00")
        assert (rows[1]["incomplete"], rows[1]["strike"], rows[1]["links"]) == ("slow", 1, "[]")
    finally:
        f.close()


def test_writer_output_is_one_gzip_member_per_record(tmp_path):
    w = jsfetch.RenderedWriter(tmp_path, 3, NOW)
    offs = [w.write(f"https://w3.nknu.edu.tw/p{i}.html", f"<html>{i}</html>", None)[1] for i in range(3)]
    w.close()
    data = (tmp_path / "warc/2026/11/17/jsfetch-rendered-3.warc.gz").read_bytes()
    for off in offs:
        assert data[off:off + 2] == b"\x1f\x8b"
    recs = list(ArchiveIterator(io.BytesIO(data)))
    assert [r.rec_type for r in recs] == ["conversion"] * 3


# ---------------------------------------------------------------- Task 2: opening one page


SHELL = page("研發處")  # a page whose own answer is only its frame


def test_only_the_page_s_own_host_is_asked(tmp_path, school):
    url = f"{ORD}/?p=list&category=news"
    asks = [Ask(f"{ORD}/js/a.js", kind="script"),
            Ask(f"{ORD}/api/list.php?p=list&category=news&id=", "POST", "xhr",
                {"content-type": jscheck._FORM}, b""),
            Ask("https://padlet.com/embed/x", kind="document"),
            Ask("https://sso.nknu.edu.tw/x.aspx", kind="document"),
            Ask("https://cdn.example.com/lib.js", kind="script")]
    server = Server({url: (200, HTML, SHELL), f"{ORD}/js/a.js": (200, {"content-type": "text/javascript"}, "1;"),
                     f"{ORD}/api/list.php?p=list&category=news&id=": (200, JSON_T, '{"status":"success"}')})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page(*lines("消息", 12)), asks)}))
    with fx:
        got = fx(url)
    fr.close()
    assert [u for _, u in server.asked()] == [url, f"{ORD}/js/a.js", f"{ORD}/api/list.php?p=list&category=news&id="]
    blocked = {e.url: e.blocked for e in got.log if e.blocked}
    assert blocked == {"https://padlet.com/embed/x": "other_host", "https://sso.nknu.edu.tw/x.aspx": "other_host",
                       "https://cdn.example.com/lib.js": "other_host"}
    assert got.incomplete == "other_host https://cdn.example.com/lib.js"
    assert got.strike is False
    assert got.requests == {"sent": 3, "unsent": {"other_host": 3}}


def test_a_picture_is_never_asked_whatever_the_browser_calls_it(tmp_path, school):
    url = f"{ORD}/?p=page&id=9"
    asks = [Ask(f"{ORD}/images/logo.png", kind="other"), Ask(f"{ORD}/favicon.ICO?v=2", kind="other"),
            Ask(f"{ORD}/upload/a.JPG", kind="fetch"), Ask(f"{ORD}/js/png.js", kind="script")]
    server = Server({url: (200, HTML, SHELL), f"{ORD}/js/png.js": (200, {"content-type": "text/javascript"}, "1;")})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("x"), asks)}))
    with fx:
        got = fx(url)
    fr.close()
    assert [u for _, u in server.asked()] == [url, f"{ORD}/js/png.js"]
    assert got.requests == {"sent": 2, "unsent": {"type": 3}}
    assert got.incomplete is None


def test_requests_go_one_at_a_time_with_the_gap(tmp_path, school):
    url = f"{ORD}/?p=page&id=1"
    asks = [Ask(f"{ORD}/js/{n}.js", kind="script") for n in "ab"]
    routes = {url: (200, HTML, SHELL)} | {a.url: (200, {"content-type": "text/javascript"}, "1;") for a in asks}
    fx, fr, _ = make_fetcher(tmp_path, Server(routes), FakeBrowser({url: FakePage(page("x"), asks)}))
    with fx:
        fx(url)
    rows = fr.q("SELECT url, purpose, started, ended FROM request ORDER BY id")
    fr.close()
    assert [r["purpose"] for r in rows] == ["jsfetch"] * 3
    for before, after in itertools.pairwise(rows):
        assert after["started"] - before["ended"] >= 2.0


def test_the_rendered_page_is_archived_and_points_at_its_own_answer(tmp_path, school):
    url = f"{ORD}/?p=list&category=news&id=7"
    shown = page(*lines("公告", 12))
    fx, fr, _ = make_fetcher(tmp_path, Server({url: (200, HTML, SHELL)}), FakeBrowser({url: FakePage(shown)}))
    with fx:
        got = fx(url)
    own = fr.q("SELECT warc, warc_offset FROM request WHERE purpose='jsfetch' AND url=?", (url,))[0]
    fr.close()
    assert jsfetch.read_rendered(tmp_path, got.warc, got.warc_offset) == (url, shown)
    with open(tmp_path / got.warc, "rb") as fh:
        fh.seek(got.warc_offset)
        rec = next(iter(ArchiveIterator(fh)))
    assert rec.rec_headers.get_header("WARC-Refers-To") == jsfetch.record_id_at(tmp_path, own["warc"],
                                                                                 own["warc_offset"])
    assert got.warc.startswith("warc/") and "jsfetch-rendered-" in got.warc


def test_script_filled_body_is_js_with_new_links(tmp_path, school):
    url = f"{ORD}/?p=list&category=news&subcategory=all"
    raw = page("研發處", extra='<a href="/">首頁</a>')
    rows = "".join(f'<a href="/?p=list&category=news&id={i}">第{i}則</a>' for i in (1, 2, 3))
    shown = page(*lines("公告", 12), extra='<a href="/">首頁</a>' + rows
                 + '<a href="./api/dl.php?file=upload/attachment/A.PDF">附件</a>'
                 + '<a href="https://www.example.org/x">校外</a>')
    fx, fr, _ = make_fetcher(tmp_path, Server({url: (200, HTML, raw)}), FakeBrowser({url: FakePage(shown)}))
    with fx:
        got = fx(url)
    fr.close()
    assert got.verdict == "js"
    assert sorted(got.links) == sorted([f"{ORD}/?p=list&category=news&id=1", f"{ORD}/?p=list&category=news&id=2",
                                        f"{ORD}/?p=list&category=news&id=3",
                                        f"{ORD}/api/dl.php?file=upload/attachment/A.PDF"])
    assert got.new_chars >= 200 and got.chars > got.raw_chars
    assert got.incomplete is None and got.error is None


def test_a_post_body_left_unsent_is_incomplete_but_no_strike(tmp_path, school):
    # a body our client never sends (an XML one: 11-17, JSON is sent now, the crawler session's json switch)
    url = "https://w3.nknu.edu.tw/"
    api = "https://w3.nknu.edu.tw/api/nknu-soap"
    asks = [Ask(api, "POST", "fetch", {"content-type": "text/xml"}, b"<x/>")]
    server = Server({url: (200, HTML, page("首頁"))})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("示意資料"), asks)}))
    with fx:
        got = fx(url)
    fr.close()
    assert server.asked() == [("GET", url)]
    assert got.incomplete == f"post_body {api}"
    assert got.strike is False


def test_a_json_post_is_sent_now(tmp_path, school):
    # 11-17, the handoff's item ②: jscheck._target(json=True), set by JsFetcher's check.json_post
    url = "https://w3.nknu.edu.tw/"
    api = "https://w3.nknu.edu.tw/api/nknu-soap"
    body = b'{"serviceName":"x"}'
    asks = [Ask(api, "POST", "fetch", {"content-type": "application/json"}, body)]
    server = Server({url: (200, HTML, page("首頁")), api: (200, {"content-type": "application/json"}, "{}")})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("最新消息"), asks)}))
    with fx:
        got = fx(url)
    fr.close()
    assert server.asked() == [("GET", url), ("POST", api)]
    sent = server.hits[1]
    assert sent.headers["content-type"] == "application/json" and sent.content == body
    assert got.incomplete is None and got.strike is False


def test_moved_to_another_host(tmp_path, school):
    url = f"{ORD}/?p=redirect"
    server = Server({url: (302, {"location": "https://w3.nknu.edu.tw/x"}, "")})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({}))
    with fx:
        got = fx(url)
    fr.close()
    assert server.asked() == [("GET", url)]
    assert got.incomplete == "moved:https://w3.nknu.edu.tw/x"
    assert got.strike is False
    assert got.warc is None


def test_the_cap_cuts_a_page_short(tmp_path, school):
    url, url2 = f"{ORD}/?p=page&id=1", f"{ORD}/?p=page&id=2"
    asks = [Ask(f"{ORD}/js/{n}.js", kind="script") for n in "abc"]
    routes = {url: (200, HTML, SHELL), url2: (200, HTML, SHELL)}
    routes |= {a.url: (200, {"content-type": "text/javascript"}, "1;") for a in asks}
    server = Server(routes)
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("x"), asks)}), max_per_host=2)
    with fx:
        got = fx(url)
        got2 = fx(url2)
        assert fx.capped("ord.nknu.edu.tw") and fx.sent_to("ord.nknu.edu.tw") == 2
    fr.close()
    assert [u for _, u in server.asked()] == [url, f"{ORD}/js/a.js"]
    assert (got.incomplete, got.strike) == ("cap:site", False)
    assert (got2.incomplete, got2.strike, got2.warc) == ("cap:site", False, None)


def test_slow_page_is_a_strike(tmp_path, school):
    url = f"{ORD}/?p=page&id=3"
    fx, fr, _ = make_fetcher(tmp_path, Server({url: (200, HTML, SHELL)}),
                             FakeBrowser({url: FakePage(page("x"), slow=True)}))
    with fx:
        got = fx(url)
    fr.close()
    assert (got.incomplete, got.strike) == ("slow", True)


def test_a_page_that_fails_is_no_rendered_record(tmp_path, school):
    url = f"{ORD}/?p=page&id=404"
    fx, fr, _ = make_fetcher(tmp_path, Server({}), FakeBrowser({}))
    with fx:
        got = fx(url)
    fr.close()
    assert got.error == "status 404"
    assert got.warc is None and got.strike is True


# ---------------------------------------------------------------- Task 3: which pages a pass opens

W3 = "https://w3.nknu.edu.tw"
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)


def _look(f: Frontier, row, run: int, *, at: datetime = T0, verdict: str | None = "ok", links=(),
          incomplete: str | None = None, strike: bool = False, new_chars: int = 0) -> None:
    jsfetch.record(f, row, jsfetch.Rendered(row["url"], final_url=row["url"], verdict=verdict, links=list(links),
                                            incomplete=incomplete, strike=strike, new_chars=new_chars), run, at=at)


def _selected(f: Frontier, sites=("ord.nknu.edu.tw", "w3.nknu.edu.tw"), now: datetime = T0, **kw) -> list[str]:
    return [r["url"] for r in jsfetch.select_pages(f, sites, now, **kw)]


def _ledger_with(tmp_path: Path, *urls: str) -> tuple[Frontier, dict]:
    f = _ledger(tmp_path)
    jsfetch.ensure_table(f)
    return f, {u: _page_row(f, u) for u in urls}


def test_first_time_every_page_is_opened_home_first(tmp_path):
    f, _ = _ledger_with(tmp_path, f"{ORD}/?p=page&id=1", f"{ORD}/?p=list&category=news", f"{ORD}/")
    try:
        assert _selected(f) == [f"{ORD}/", f"{ORD}/?p=page&id=1", f"{ORD}/?p=list&category=news"]
    finally:
        f.close()


def test_a_list_page_is_opened_every_run_and_a_page_with_nothing_new_never_again(tmp_path):
    lst, plain = f"{ORD}/?p=list&category=news", f"{ORD}/?p=page&id=1"
    f, rows = _ledger_with(tmp_path, lst, plain)
    try:
        _look(f, rows[lst], 1, verdict="js", links=[f"{ORD}/?p=list&category=news&id=1"])
        _look(f, rows[plain], 1, verdict="ok")
        assert jsfetch.classify(f, ["ord.nknu.edu.tw"]) == {rows[lst]["id"]: "links", rows[plain]["id"]: "none"}
        assert _selected(f, now=T0.replace(day=11)) == [lst]
    finally:
        f.close()


def test_a_text_page_waits_for_a_new_fetch_or_a_week(tmp_path):
    detail = f"{ORD}/?p=list&category=news&id=408"
    f, rows = _ledger_with(tmp_path, detail)
    try:
        _look(f, rows[detail], 1, verdict="js")
        assert jsfetch.classify(f, ["ord.nknu.edu.tw"]) == {rows[detail]["id"]: "text"}
        assert _selected(f, now=T0.replace(day=16)) == []
        assert _selected(f, now=T0.replace(day=17)) == [detail]
        f.x("UPDATE url SET fetched_at='2026-11-18T00:00:00+00:00' WHERE id=?", (rows[detail]["id"],))
        assert _selected(f, now=T0.replace(day=12)) == [detail]
    finally:
        f.close()


def test_attachments_a_page_shows_make_it_no_list(tmp_path):
    detail = f"{ORD}/?p=list&category=news&id=408"
    dl, pdf = f"{ORD}/api/dl.php?file=upload/attachment/A.PDF", f"{ORD}/upload/b.pdf"
    f, rows = _ledger_with(tmp_path, detail)
    try:
        f.x("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status) VALUES "
            "(?,?,?,?,2,'file',2,'link','done',200)", (dl, f"{ORD}/api/dl.php", "ord.nknu.edu.tw", "ord.nknu.edu.tw"))
        _look(f, rows[detail], 1, verdict=None, links=[dl, pdf], new_chars=199)
        assert jsfetch.classify(f, ["ord.nknu.edu.tw"]) == {rows[detail]["id"]: "text"}
    finally:
        f.close()


def test_a_little_new_text_is_no_reason_to_look_again(tmp_path):
    u = f"{W3}/goal.html"
    f, rows = _ledger_with(tmp_path, u)
    try:
        _look(f, rows[u], 1, verdict="ok", new_chars=99)
        assert jsfetch.classify(f, ["w3.nknu.edu.tw"]) == {rows[u]["id"]: "none"}
    finally:
        f.close()


def test_a_menu_on_every_page_makes_no_page_a_list(tmp_path):
    pages = [f"{W3}/{n}.html" for n in ("admin", "center", "about", "goal")]
    menu = [f"{W3}/m{i}.html" for i in range(5)]
    f, rows = _ledger_with(tmp_path, f"{W3}/", *pages)
    try:
        for u in pages:
            _look(f, rows[u], 1, verdict="ok", links=menu)
        _look(f, rows[f"{W3}/"], 1, verdict="ok", links=menu)
        got = jsfetch.classify(f, ["w3.nknu.edu.tw"])
        assert set(got.values()) == {"none"}
        assert _selected(f, now=T0.replace(day=11)) == [f"{W3}/"]  # a site's home is opened every run
    finally:
        f.close()


def test_two_strikes_stop_retries_and_again_brings_them_back(tmp_path):
    u = f"{ORD}/?p=page&id=5"
    f, rows = _ledger_with(tmp_path, u)
    try:
        _look(f, rows[u], 1, verdict=None, incomplete="slow", strike=True)
        assert _selected(f) == [u]
        _look(f, rows[u], 2, verdict=None, incomplete="not_idle", strike=True)
        assert _selected(f) == []
        assert _selected(f, again=True) == [u]
    finally:
        f.close()


def test_an_incomplete_page_without_strike_is_always_retried(tmp_path):
    u = f"{W3}/president.html"
    f, rows = _ledger_with(tmp_path, u)
    try:
        for run in (1, 2, 3):
            _look(f, rows[u], run, verdict="js", incomplete="post_body https://w3.nknu.edu.tw/api/nknu-etp")
        assert _selected(f) == [u]
    finally:
        f.close()


def test_other_sites_and_non_pages_are_left_alone(tmp_path):
    f, _ = _ledger_with(tmp_path, f"{ORD}/?p=page&id=1")
    try:
        f.x("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status, fetched_at) VALUES "
            "(?,?,?,?,2,'file',1,'link','done',200,'2026-11-16T00:00:00+00:00')",
            (f"{ORD}/api/dl.php?file=a.pdf", f"{ORD}/api/dl.php", "ord.nknu.edu.tw", "ord.nknu.edu.tw"))
        missing = _page_row(f, f"{ORD}/?p=page&id=404")
        f.x("UPDATE url SET status=404 WHERE id=?", (missing["id"],))
        later = _page_row(f, f"{ORD}/?p=page&id=6")
        f.x("UPDATE url SET state='later' WHERE id=?", (later["id"],))
        _page_row(f, "https://ite.nknu.edu.tw/?p=billboard")
        assert _selected(f) == [f"{ORD}/?p=page&id=1"]
    finally:
        f.close()


# ---------------------------------------------------------------- Task 4: the pass and the commands

ITE = "https://ite.nknu.edu.tw"
SITES = ("ord.nknu.edu.tw", "ite.nknu.edu.tw")


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "archive-r1"
    Frontier(root / "frontier.db").close()  # a new file: round 1's
    return root


def factories(server: Server, browser) -> dict:
    clock = Clock()
    return {"browser": browser, "resolver": lambda h: (f"ip:{h}", None, h), "clock": clock, "sleep": clock.sleep,
            "rng": random.Random(7),
            "client_factory": lambda warc: archive_client(warc, transport=httpx.MockTransport(server), clock=clock,
                                                          sleep=clock.sleep)}


def make_run(root: Path, server: Server, browser, sites=SITES, **kw) -> jsfetch.JsFetchRun:
    return jsfetch.JsFetchRun(root, sites, campus_url=None, **factories(server, browser), **kw)


def _api_page(url: str, api: str, n: int = 12) -> tuple[FakePage, dict]:
    asks = [Ask(api, "POST", "xhr", {"content-type": jscheck._FORM}, b"")]
    return FakePage(page(*lines("公告", n)), asks), {url: (200, HTML, SHELL), api: (200, JSON_T, '{"data":[]}')}


def test_run_opens_due_pages_records_them_and_reports(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{ORD}/", f"{ORD}/?p=list&category=news", f"{ITE}/?p=billboard"]
    pages, routes = {}, {}
    for i, u in enumerate(urls):
        fp, rs = _api_page(u, f"{u.split('/?')[0].rstrip('/')}/api/x{i}.php")
        pages[u], routes = fp, routes | rs
    with _opened(root) as f:
        for u in urls:
            _page_row(f, u)
    run = make_run(root, Server(routes), FakeBrowser(pages))
    rep = run.run()
    assert run.end_reason == "finished"
    assert rep["ord.nknu.edu.tw"].opened == 2 and rep["ite.nknu.edu.tw"].opened == 1
    assert rep["ord.nknu.edu.tw"].requests == 4 and rep["ord.nknu.edu.tw"].text == 2
    with _opened(root) as f:
        assert f.q("SELECT count(*) FROM js_render")[0][0] == 3
        codes = [r["code"] for r in f.q("SELECT code FROM event WHERE code LIKE 'jsfetch%' ORDER BY rowid")]
        assert codes == ["jsfetch_start", "jsfetch_end"]
    hold_run_lock(root).close()  # the pass let run.lock go


@contextlib.contextmanager
def _opened(root: Path):
    f = Frontier(root / "frontier.db")
    try:
        yield f
    finally:
        f.close()


def test_refused_while_a_run_holds_the_directory(tmp_path):
    root = make_root(tmp_path)
    lock = hold_run_lock(root)
    try:
        browser = FakeBrowser({})
        with pytest.raises(jsfetch.JsFetchRefused, match="archive run"):
            make_run(root, Server({}), browser).run()
        assert not browser.started
    finally:
        lock.close()


def test_refused_while_another_program_sends_to_the_school(tmp_path):
    root = make_root(tmp_path)
    held = schoollock.hold()
    try:
        browser = FakeBrowser({})
        with pytest.raises(jsfetch.JsFetchRefused, match="全机锁"):
            make_run(root, Server({}), browser).run()
        assert not browser.started
    finally:
        held.close()
    hold_run_lock(root).close()


def test_the_10_24_archive_and_an_empty_site_list_are_refused(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    import sqlite3 as _sq
    con = _sq.connect(old / "frontier.db")
    con.execute("CREATE TABLE url(id INTEGER PRIMARY KEY)")
    con.close()
    with pytest.raises(jsfetch.JsFetchRefused, match="第一轮"):
        make_run(old, Server({}), FakeBrowser({})).run()
    with pytest.raises(jsfetch.JsFetchRefused, match="找不到"):
        make_run(tmp_path / "none", Server({}), FakeBrowser({})).run()
    with pytest.raises(jsfetch.JsFetchRefused, match="网站"):
        make_run(make_root(tmp_path), Server({}), FakeBrowser({}), sites=()).run()


def test_without_a_browser_no_page_is_opened_and_the_end_says_why(tmp_path):
    root = make_root(tmp_path)
    with _opened(root) as f:
        _page_row(f, f"{ORD}/")
    run = make_run(root, Server({}), FakeBrowser({}, missing=True))
    run.run()
    assert run.end_reason == "error" and "Playwright" in (run.end_detail or "")
    with _opened(root) as f:
        assert f.q("SELECT count(*) FROM js_render")[0][0] == 0
        assert f.q("SELECT count(*) FROM request")[0][0] == 0
        end = f.q("SELECT level, message FROM event WHERE code='jsfetch_end'")
        assert end[0]["level"] == "error" and "Playwright" in end[0]["message"]


def test_stop_file_ends_after_the_page_in_flight(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{ORD}/?p=page&id=1", f"{ORD}/?p=page&id=2"]
    with _opened(root) as f:
        for u in urls:
            _page_row(f, u)
    routes = {u: (200, HTML, SHELL) for u in urls}
    browser = FakeBrowser({u: FakePage(page("x")) for u in urls},
                          on_open=lambda u: (root / "STOP").write_text("") if u == urls[0] else None)
    run = make_run(root, Server(routes), browser)
    run.run()
    assert run.end_reason == "stop"
    assert browser.opened == [urls[0]]
    with _opened(root) as f:
        assert f.q("SELECT count(*) FROM js_render")[0][0] == 1


def test_max_per_site_holds_across_pages(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{ORD}/?p=page&id={i}" for i in (1, 2, 3)]
    pages, routes = {}, {}
    for i, u in enumerate(urls):
        fp, rs = _api_page(u, f"{ORD}/api/p{i}.php")
        pages[u], routes = fp, routes | rs
    server = Server(routes)
    browser = FakeBrowser(pages)
    run = make_run(root, server, browser, max_per_site=3)
    with _opened(root) as f:
        for u in urls:
            _page_row(f, u)
    rep = run.run()
    assert len(server.hits) == 3
    assert browser.opened == urls[:2]
    assert rep["ord.nknu.edu.tw"].incomplete == 1 and rep["ord.nknu.edu.tw"].skipped == 1
    with _opened(root) as f:
        rows = f.q("SELECT incomplete, strike FROM js_render ORDER BY url_id")
    assert [(r["incomplete"], r["strike"]) for r in rows] == [(None, 0), ("cap:site", 0)]


def test_render_refuses_a_ledger_with_rows(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    with _opened(root) as f:
        _page_row(f, f"{ORD}/")
    server = Server({})
    monkeypatch.setattr(jsfetch, "_factories", factories(server, FakeBrowser({})))
    assert jsfetch.main(["render", f"{ORD}/", "--dir", str(root)]) == 2
    assert server.hits == []
    assert "正式账本" in capsys.readouterr().out


def test_render_in_an_empty_dir_opens_one_page(tmp_path, monkeypatch, capsys):
    url = f"{ORD}/?p=list&category=news"
    fp, routes = _api_page(url, f"{ORD}/api/list.php")
    server = Server(routes)
    monkeypatch.setattr(jsfetch, "_factories", factories(server, FakeBrowser({url: fp})))
    assert jsfetch.main(["render", url, "--dir", str(tmp_path / "scratch"), "--max-per-site", "10"]) == 0
    out = capsys.readouterr().out
    assert "js" in out and "jsfetch-rendered-" in out
    assert len(server.hits) == 2


def test_status_reads_without_writing(tmp_path, capsys):
    root = make_root(tmp_path)
    with _opened(root) as f:
        jsfetch.ensure_table(f)
        row = _page_row(f, f"{ORD}/?p=list&category=news")
        _look(f, row, 1, verdict="js", links=[f"{ORD}/?p=list&category=news&id=1"])
    before = (root / "frontier.db").stat().st_mtime_ns
    assert jsfetch.main(["status", "--dir", str(root)]) == 0
    assert (root / "frontier.db").stat().st_mtime_ns == before
    out = capsys.readouterr().out
    assert "ord.nknu.edu.tw" in out and "列表 1" in out


# ---------------------------------------------------------------- Task 5: this machine's Chrome, offline

PAGES_HOST = "jstest.example"  # a name of its own resolved to the loopback server (test_jscheck.py's way)
_BODY = lines("研究計畫徵件", 12)
_ROWS = [f"系所公告第{i}則" for i in range(4)]
_MENU = [f"/m{i}.html" for i in range(5)]
_SCRIPTS = {
    # a shell whose script POSTs a form to its data address and writes what comes back into the page (ord's way)
    "/js/list.js": ('var x = new XMLHttpRequest(); x.open("POST", "/api/list.php?p=list&id=7");'
                    'x.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");'
                    'x.onload = function () { var d = JSON.parse(x.responseText).data;'
                    'document.querySelector("[name=data_content]").innerHTML = d.content'
                    ' + "<a href=\\"./api/dl.php?file=upload/attachment/A.PDF\\">附件</a>"; }; x.send("");'),
    # a list whose rows a script builds from its data address, two of them hidden by a filter (ite's way)
    "/js/billboard.js": ('fetch("/api/billboard.php", {method: "POST"}).then(r => r.json()).then(d => {'
                         'const box = document.getElementById("list"); d.data.forEach((v, i) => {'
                         'const a = document.createElement("a"); a.href = v.url; a.textContent = v.thetitle;'
                         'if (i >= 2) a.style.display = "none"; box.appendChild(a); }); });'),
    # a menu a custom element draws (w3's components.js)
    "/js/components.js": ('customElements.define("nknu-header", class extends HTMLElement { connectedCallback() {'
                          f'this.innerHTML = {json.dumps("".join(f"<a href={m!r}>選單{i}</a>" for i, m in enumerate(_MENU)))};'
                          '} });'),
}
_LOCAL = {
    "/ord.html": page("研發處", extra='<div name="data_content"></div><script src="/js/list.js"></script>'),
    "/ite.html": page("系所公告", extra='<div id="list"></div><script src="/js/billboard.js"></script>'),
    "/w3.html": page(*lines("學校簡介", 3), extra='<nknu-header></nknu-header><script src="/js/components.js"></script>'),
}
_DATA = {
    "/api/list.php?p=list&id=7": {"status": "success", "data": {"content": "".join(f"<p>{x}</p>" for x in _BODY)}},
    "/api/billboard.php": {"data": [{"url": f"/?p=billboard&id={i}", "thetitle": t} for i, t in enumerate(_ROWS)]},
}


class _Local(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list[tuple[str, str]]] = []

    def log_message(self, *args) -> None:
        pass

    def _answer(self) -> None:
        type(self).hits.append((self.command, self.path))
        if self.command == "POST":
            self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path in _DATA:
            body, ctype = json.dumps(_DATA[self.path], ensure_ascii=False).encode(), "application/json"
        elif self.path in _SCRIPTS:
            body, ctype = _SCRIPTS[self.path].encode(), "text/javascript; charset=utf-8"
        elif self.path in _LOCAL:
            body, ctype = _LOCAL[self.path].encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer


@pytest.mark.browser
def test_this_machine_s_chrome_shows_what_the_scripts_build(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api", reason="没装 Playwright（pyproject 的 extra browser：uv sync --extra "
                                                       "browser），这个测试要用它开本机的 Chrome")
    _Local.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: real("127.0.0.1" if host == PAGES_HOST else host, *a, **k))
    base = f"http://{PAGES_HOST}:{server.server_address[1]}"
    fr = Frontier(tmp_path / "frontier.db")
    try:
        fx = jsfetch.JsFetcher(tmp_path, fr, gap=(1.0, 1.0), resolver=resolve,
                               browser=jscheck.PlaywrightBrowser(settle_ms=300, idle_ms=10_000, goto_ms=60_000))
        try:
            with fx:
                ord_, ite, w3 = (fx(f"{base}/{n}.html") for n in ("ord", "ite", "w3"))
        except jscheck.BrowserMissing as exc:
            pytest.skip(f"这台机器打不开 Chrome：{exc}")
        reqs = fr.q("SELECT method, url FROM request WHERE purpose='jsfetch' ORDER BY id")
    finally:
        fr.close()
        server.shutdown()
    assert (ord_.verdict, ord_.incomplete, ord_.error) == ("js", None, None)
    assert _BODY[5] in jsfetch.read_rendered(tmp_path, ord_.warc, ord_.warc_offset)[1]
    assert f"{base}/api/dl.php?file=upload/attachment/A.PDF" in ord_.links
    assert {f"{base}/?p=billboard&id={i}" for i in range(4)} <= set(ite.links)  # hidden rows are in the page too
    assert {f"{base}{m}" for m in _MENU} <= set(w3.links)
    assert len(_LOCAL_hits := _Local.hits) == len(reqs)  # every request that reached the server went through us
    assert ("POST", "/api/list.php?p=list&id=7") in _LOCAL_hits


# ---------------------------------------------------------------- the review's findings (plan 2026-11-17 Task 6)


def test_a_page_that_asks_all_at_once_is_still_asked_one_at_a_time(tmp_path, school):
    url = f"{ORD}/?p=page&id=11"
    asks = [Ask(f"{ORD}/api/p{i}.php", "POST", "xhr", {"content-type": jscheck._FORM}, b"") for i in range(4)]
    routes = {url: (200, HTML, SHELL)} | {a.url: (200, JSON_T, "{}") for a in asks}
    fx, fr, _ = make_fetcher(tmp_path, Server(routes), FakeBrowser({url: FakePage(page("x"), asks, parallel=True)}))
    with fx:
        fx(url)
    rows = fr.q("SELECT started, ended FROM request WHERE purpose='jsfetch' ORDER BY started")
    fr.close()
    assert len(rows) == 5
    for before, after in itertools.pairwise(rows):
        assert after["started"] - before["ended"] >= 2.0


def test_a_page_the_browser_could_not_read_is_incomplete(tmp_path, school):
    url = f"{ORD}/?p=page&id=12"
    fx, fr, _ = make_fetcher(tmp_path, Server({url: (200, HTML, SHELL)}),
                             FakeBrowser({url: FakePage(page("x"), error="Error: the page is navigating")}))
    with fx:
        got = fx(url)
    fr.close()
    assert got.incomplete == "error Error: the page is navigating" and got.strike is True
    assert got.warc is None


def test_a_later_navigation_that_got_no_answer_is_incomplete(tmp_path, school):
    url, there = f"{ORD}/?p=page&id=13", f"{ORD}/?p=page&id=14"

    def refuse(request):
        raise httpx.ConnectError("refused (test)", request=request)

    server = Server({url: (200, HTML, SHELL), there: refuse})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("x"), goes_to=there)}))
    with fx:
        got = fx(url)
    fr.close()
    assert got.incomplete.startswith(f"nav {there}: ") and got.strike is True
    assert got.warc is None


def test_a_capped_second_hop_is_no_strike(tmp_path, school):
    url, there = f"{ORD}/?p=about", f"{ORD}/?p=about&type=about"
    server = Server({url: (302, {"location": there}, ""), there: (200, HTML, SHELL)})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({}), max_per_host=1)
    with fx:
        got = fx(url)
    fr.close()
    assert (got.incomplete, got.strike) == ("cap:site", False)


def test_the_cap_counts_requests_on_the_wire(tmp_path, school):
    from types import SimpleNamespace

    fx, fr, _ = make_fetcher(tmp_path, Server({}), FakeBrowser({}), max_per_host=2)
    with fx:
        lane = fx.sender.lane("ip:ord.nknu.edu.tw")
        wire = [SimpleNamespace(attempt=n, method="GET", started=0.0, ended=0.0, status=200 if n == 2 else None,
                                error=None if n == 2 else "idle_closed", bytes=0, new_conn=True, connect_ms=None,
                                tls_ms=None, ip=None, warc_offset=None) for n in (1, 2)]
        fx.sender._log(lane, "ord.nknu.edu.tw", f"{ORD}/x", 0.0, SimpleNamespace(sent=wire))
        assert fx.sent_to("ord.nknu.edu.tw") == 2 and fx.capped("ord.nknu.edu.tw")
    fr.close()


def test_a_trial_run_asks_no_campus_check_and_a_full_run_does(tmp_path):
    campus = "https://sso.nknu.edu.tw/zthat/checkcampusip.aspx?curl=x"
    url = f"{ORD}/?p=page&id=1"
    for cap, asked in ((3, False), (None, True)):
        root = make_root(tmp_path / f"cap{cap}")
        with _opened(root) as f:
            _page_row(f, url)
        server = Server({url: (200, HTML, SHELL), campus: (200, HTML, page("您的IP 不在校內網路"))})
        jsfetch.JsFetchRun(root, ["ord.nknu.edu.tw"], max_per_site=cap, again=True, campus_url=campus,
                           **factories(server, FakeBrowser({url: FakePage(page("x"))}))).run()
        assert (campus in [u for _, u in server.asked()]) is asked


def test_a_browser_that_breaks_ends_the_pass_without_a_strike(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{ORD}/?p=page&id={i}" for i in (1, 2)]
    with _opened(root) as f:
        for u in urls:
            _page_row(f, u)
    browser = FakeBrowser({u: FakePage(page("x")) for u in urls}, breaks=frozenset(urls[:1]))
    run = make_run(root, Server({u: (200, HTML, SHELL) for u in urls}), browser)
    run.run()
    assert run.end_reason == "error" and "closed" in run.end_detail
    assert browser.opened == urls[:1]
    with _opened(root) as f:
        rows = f.q("SELECT incomplete, strike FROM js_render")
        assert [(r["incomplete"][:8], r["strike"]) for r in rows] == [("browser ", 0)]
        assert [r["url"] for r in jsfetch.select_pages(f, ["ord.nknu.edu.tw"], NOW)] == urls


def test_a_sender_that_halts_ends_the_pass_as_an_error(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{ORD}/?p=page&id={i}" for i in (1, 2)]
    with _opened(root) as f:
        for u in urls:
            _page_row(f, u)
    holder: dict = {}
    browser = FakeBrowser({u: FakePage(page("x")) for u in urls},
                          on_open=lambda u: holder["run"].fetcher.check.halt("error", "warc_write: 磁碟滿了（測試）"))
    run = holder["run"] = make_run(root, Server({u: (200, HTML, SHELL) for u in urls}), browser)
    run.run()
    assert (run.end_reason, run.end_detail) == ("error", "warc_write: 磁碟滿了（測試）")
    assert browser.opened == urls[:1]


# ---------------------------------------------------------------- for the crawler's export (spec §5 ⑤)


def test_for_export_picks_the_newest_complete_look_that_added_something(tmp_path):
    u = f"{ORD}/?p=list&category=news&id=408"
    f, rows = _ledger_with(tmp_path, u)
    try:
        assert jsfetch.for_export(f, rows[u]["id"]) == (None, None)  # never looked at: the page's own answer
        jsfetch.record(f, rows[u], jsfetch.Rendered(u, final_url=u, verdict="js", new_chars=300, warc="warc/a.gz",
                                                    warc_offset=1), 1, at=T0)
        jsfetch.record(f, rows[u], jsfetch.Rendered(u, final_url=u, incomplete="slow", strike=True), 2, at=T0)
        look, problem = jsfetch.for_export(f, rows[u]["id"])
        assert (look["run"], look["warc"], look["warc_offset"], problem) == (1, "warc/a.gz", 1, None)
        jsfetch.record(f, rows[u], jsfetch.Rendered(u, final_url=u, verdict="ok", warc="warc/b.gz", warc_offset=2),
                       3, at=T0)
        assert jsfetch.for_export(f, rows[u]["id"]) == (None, None)  # the browser adds nothing now: its own answer
    finally:
        f.close()


def test_for_export_says_why_when_no_look_was_ever_complete(tmp_path):
    u = f"{W3}/"
    f, rows = _ledger_with(tmp_path, u)
    try:
        for run in (1, 2):
            jsfetch.record(f, rows[u], jsfetch.Rendered(u, final_url=u, verdict="js", new_chars=65,
                                                        incomplete="post_body https://w3.nknu.edu.tw/api/nknu-soap",
                                                        warc="warc/c.gz", warc_offset=run), run, at=T0)
        assert jsfetch.for_export(f, rows[u]["id"]) == (None, "post_body https://w3.nknu.edu.tw/api/nknu-soap")
    finally:
        f.close()


def test_the_fetcher_asks_for_json_bodies_to_be_sent(tmp_path, school):
    fx, fr, _ = make_fetcher(tmp_path, Server({}), FakeBrowser({}))
    assert fx.check.json_post is True  # read by jscheck once the crawler session lets _target send JSON (spec §5 ②)
    fr.close()


def test_requests_that_will_not_be_sent_take_no_place_under_the_cap(tmp_path, school):
    # the lab trial 11-17: w3's home asks eight POSTs at once that were never sent (post_body; JSON then, sent since
    # the crawler session's json switch, so an XML body here); waiting in the queue they took the cap's places, and a
    # request that would have been sent was refused as cap:site
    url = "https://w3.nknu.edu.tw/"
    soap = [Ask(f"https://w3.nknu.edu.tw/api/nknu-soap?n={i}", "POST", "fetch", {"content-type": "text/xml"},
                b"<x/>") for i in range(6)]
    script = Ask("https://w3.nknu.edu.tw/js/main.js", kind="script")
    server = Server({url: (200, HTML, page("首頁")),
                     script.url: (200, {"content-type": "text/javascript"}, "1;")})
    fx, fr, _ = make_fetcher(tmp_path, server, FakeBrowser({url: FakePage(page("x"), [*soap, script], parallel=True)}),
                             max_per_host=3)
    with fx:
        got = fx(url)
    fr.close()
    assert [u for _, u in server.asked()] == [url, script.url]
    assert got.requests == {"sent": 2, "unsent": {"post_body": 6}}
