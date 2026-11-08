"""The browser check after the round (round-1 design §8.4, jscheck.py). Offline: a made-up browser drives the bridge
and the judging with a test transport behind the crawl's own client (so these run without Playwright or Chrome); one
test marked browser opens this machine's Chrome through Playwright on pages a server on 127.0.0.1 serves (skipped,
with the reason, when either is missing). No address here reaches the school: hosts are the school's names answered by
httpx.MockTransport, or the loopback server. Page texts are made up, with no names."""

from __future__ import annotations

import asyncio
import contextlib
import html as htmllib
import http.server
import itertools
import json
import random
import re
import socket
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

from nknu.archive import jscheck, pagetypes
from nknu.archive.crawler import archive_client, hold_run_lock, resolve
from nknu.archive.frontier import Frontier
from nknu.archive.jscheck import Ask, JsCheck, JsCheckRefused
from nknu.crawl import schoollock
from nknu.crawl.fetch import USER_AGENT
from tests.archive.plans import OpenPlan

EE = "https://ee.nknu.edu.tw"
HTML = {"content-type": "text/html; charset=utf-8"}
JSON_T = {"content-type": "application/json; charset=utf-8"}
OLD_FETCH = "2000-01-01T00:00:00+00:00"
_REFRESH = re.compile(r'http-equiv="refresh" content="0;url=([^"]+)"')  # every row's last fetch: long before any look jscheck writes now


def lines(word: str, n: int, start: int = 0) -> list[str]:
    """n different lines of about 20 characters (made-up notices, no names)."""
    return [f"{word}第{i}則：本學期課程異動與選課注意事項說明" for i in range(start, start + n)]


def page(*paras: str, extra: str = "") -> str:
    return "<html><head><title>系所公告</title></head><body>" + "".join(f"<p>{p}</p>" for p in paras) + extra + \
        "</body></html>"


# ---------------------------------------------------------------- the archive, the server, the browser
@contextlib.contextmanager
def opened(root: Path):
    f = Frontier(root / "frontier.db")
    try:
        yield f
    finally:
        f.close()


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "archive-r1"
    Frontier(root / "frontier.db").close()  # a new file: round 1's
    return root


def add_page(f: Frontier, url: str, *, verdict: str = "todo", why: str | None = "new", source: str = "crawl",
             text_chars: int = 300, flags: str | None = None, status: int = 200, depth: int = 2,
             note: str | None = None, fetched_at: str = OLD_FETCH) -> int:
    """A page this round fetched (state done, sniffed html) and its type's row (one more page of it)."""
    parts = pagetypes.type_parts(url)
    host = urlsplit(url).hostname
    f.x("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status, sniff, ctype, text_chars, "
        "flags, fetched_at, page_type, owner_site, kind0) VALUES (?,?,?,?,1,'page',?,'link','done',?,'html','text/html',"
        "?,?,?,?,?,'page')",
        (url, url.split("?", 1)[0], host, parts.site, depth, status, text_chars, flags, fetched_at, parts.key,
         parts.site))
    f.x("INSERT INTO page_type(key, site, host, path_tpl, params, verdict, why, source, n_pages, note) "
        "VALUES (?,?,?,?,?,?,?,?,1,?) ON CONFLICT(key) DO UPDATE SET n_pages = n_pages + 1",
        (parts.key, parts.site, parts.host, parts.path_tpl, json.dumps(list(parts.params)), verdict, why, source,
         note))
    return f.row(url)["id"]


def add_res(f: Frontier, page_id: int, key: str, *, how: str = "click", need: str = "round2",
            why: str | None = "round2:click", text: str | None = "下一頁") -> None:
    f.x("INSERT INTO page_res(page_id, res_key, how, role, need, why, text) VALUES (?,?,?,'click',?,?,?)",
        (page_id, key, how, need, why, text))


def result(f: Frontier, uid: int) -> str:
    return f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0]


class Server:
    """The school as httpx.MockTransport answers it: url -> (status, headers, body) or a function of the request."""

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
    """The check's clock: time passes only when something waits (thread-safe: each server queue sends on a thread)."""

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
    goes_to: str | None = None  # where a script sends it after loading
    frames: list[str] = field(default_factory=list)
    final_url: str | None = None  # the address bar, when a script changed it without loading (pushState)
    slow: bool = False


class FakeBrowser:
    """A browser that asks the bridge what a real one would (the page, its 3xx hops, its requests, a script's
    navigation) and shows what the test says."""

    def __init__(self, pages: dict[str, FakePage], *, missing: bool = False,
                 on_open: Callable[[str], None] | None = None):
        self.pages, self.missing, self.on_open = pages, missing, on_open
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
        fp = self.pages.get(url, FakePage(""))
        cur = url
        reply = await bridge.ask(Ask(cur, navigation=True))
        for _ in range(5):  # a refresh the bridge hands back for a 3xx is followed, as Chrome does
            m = None if reply.abort else _REFRESH.search(reply.body.decode("utf-8", "replace"))
            if m is None:
                break
            cur = urljoin(cur, htmllib.unescape(m.group(1)))
            reply = await bridge.ask(Ask(cur, navigation=True))
        if reply.abort:
            return bridge.observation(final_url="chrome-error://chromewebdata/", rendered="<html></html>")
        for a in fp.asks:
            await bridge.ask(a)
        rendered, final = fp.rendered, fp.final_url or cur
        if cur != url:
            rendered = self.pages.get(cur, FakePage(reply.body.decode("utf-8", "replace"))).rendered
        if fp.goes_to:
            r2 = await bridge.ask(Ask(fp.goes_to, navigation=True))
            final = fp.goes_to if not r2.abort else "chrome-error://chromewebdata/"
            rendered = self.pages.get(fp.goes_to, FakePage(r2.body.decode("utf-8", "replace"))).rendered
        if shot is not None:
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(b"\x89PNG made up")
        return bridge.observation(final_url=final, rendered=rendered, frames=fp.frames, slow=fp.slow,
                                  screenshot=str(shot) if shot is not None else None)

    async def close(self) -> None:
        self.closed = True


def make_check(root: Path, server: Server, browser, *, netcheck=lambda: True, **kw) -> JsCheck:
    clock = Clock()
    c = JsCheck(root, browser=browser,
                client_factory=lambda warc: archive_client(warc, transport=httpx.MockTransport(server), clock=clock,
                                                           sleep=clock.sleep),
                resolver=lambda h: (f"ip:{h}", None, h), netcheck=netcheck, plan=kw.pop("plan", OpenPlan()),
                clock=clock, sleep=clock.sleep, asleep=clock.asleep, rng=random.Random(7), **kw)
    c.test_clock = clock
    return c


def types(f: Frontier) -> dict[str, dict]:
    return {r["key"]: dict(r) for r in f.q("SELECT * FROM page_type")}


def key(url: str) -> str:
    return pagetypes.type_key(url)


# ---------------------------------------------------------------- the pure parts
def test_text_and_repeats():
    n, got = jscheck.text_lines(page("一則消息", "a", extra="<script>var x='不算';</script><nav>選單選單</nav>"))
    assert got == ["一則消息"] and n == 4  # a one-character line, a script and the nav are not the page's text
    assert jscheck.period_unit("跑馬燈公告跑馬燈公告跑馬燈公告") == "跑馬燈公告"
    assert jscheck.period_unit("跑馬燈公告跑馬燈") == "跑馬燈公告跑馬燈"
    new, chars = jscheck.new_text(["舊的"], ["舊的", "新的消息", "新的消息", "甲乙丙甲乙丙"])
    assert new == ["新的消息", "新的消息", "甲乙丙甲乙丙"] and chars == len("新的消息") + len("甲乙丙")


def test_new_text_is_found_in_a_data_answer_whatever_its_escapes():
    wins = jscheck.windows(["消息第一則：本學期課程異動與選課注意事項說明"])
    assert wins and jscheck.holds_text(json.dumps({"t": "消息第一則：本學期課程異動與選課注意事項說明"}), wins)
    assert not jscheck.holds_text(json.dumps({"t": "完全不同的內容"}), wins)


def test_the_request_our_client_sends_for_the_browser():
    t = jscheck._target(Ask(f"{EE}/_getAJAX.html", "POST", "xhr", {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8", "user-agent": "HeadlessChrome",
        "sec-ch-ua": '"HeadlessChrome"', "accept-encoding": "gzip, br, zstd", "x-requested-with": "XMLHttpRequest",
        "cookie": "ASP.NET_SessionId=abc", ":authority": "ee.nknu.edu.tw"}, b"act=news&cID=2&q=%E6%B8%AC"))
    assert t.method == "POST" and t.data == [("act", "news"), ("cID", "2"), ("q", "測")]
    assert t.headers == {"x-requested-with": "XMLHttpRequest", "cookie": "ASP.NET_SessionId=abc"}
    assert jscheck._target(Ask(f"{EE}/api", "POST", "fetch", {"content-type": "application/json"}, b"{}")) == "post_body"
    assert jscheck._target(Ask(f"{EE}/a", "POST", "xhr", {"content-type": jscheck._FORM}, b"a=%B4%FA")) == "post_body"
    assert jscheck._target(Ask(f"{EE}/a", "PUT", "xhr")) == "method"
    assert jscheck._target(Ask(f"{EE}/a.js", kind="script")).method == "GET"


def test_the_answer_handed_to_the_browser():
    got = jscheck._reply(jscheck.Answer(status=302, body=b"", headers=(
        ("Location", "/next.html"), ("Set-Cookie", "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT"), ("Set-Cookie", "b=2"),
        ("Content-Encoding", "gzip"), ("Content-Length", "10"), ("Connection", "keep-alive"))))
    assert got.status == 302 and got.headers == {"location": "/next.html",
                                                 "set-cookie": "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT\nb=2"}


def test_chrome_reaches_nothing_by_itself():
    assert "--proxy-server=127.0.0.1:9" in jscheck.CHROME_ARGS
    assert "--proxy-bypass-list=<-loopback>" in jscheck.CHROME_ARGS  # loopback goes to the dead proxy too


def test_an_address_changed_without_loading_is_no_redirect():
    log = [jscheck.Exchange(f"{EE}/a.html", "document", "GET", True, 200, page(*lines("消息", 12)))]
    obs = jscheck.Observation(f"{EE}/a.html", f"{EE}/a.html?tab=2", page(*lines("消息", 12)), log=log)
    assert jscheck.judge(obs).verdict == "ok" and jscheck.judge(obs).stop is None


# ---------------------------------------------------------------- before anything
def test_refused_while_a_run_holds_the_directory(tmp_path):
    root = make_root(tmp_path)
    with opened(root) as f:
        add_page(f, f"{EE}/a.html")
    lock = hold_run_lock(root)
    try:
        browser = FakeBrowser({})
        with pytest.raises(JsCheckRefused, match="archive run"):
            make_check(root, Server({}), browser).run()
        assert not browser.started
    finally:
        lock.close()
    with opened(root) as f:
        assert f.q("SELECT count(*) FROM event WHERE code='jscheck_end'")[0][0] == 0


def test_refused_while_another_program_sends_to_the_school(tmp_path):
    root = make_root(tmp_path)
    held = schoollock.hold()
    try:
        browser = FakeBrowser({})
        with pytest.raises(JsCheckRefused, match="全机锁"):
            make_check(root, Server({}), browser).run()
        assert not browser.started
    finally:
        held.close()
    hold_run_lock(root).close()  # jscheck let run.lock go when it was refused


def test_the_10_24_archive_is_never_touched(tmp_path):
    root = tmp_path / "archive-2026-10-24"
    root.mkdir()
    db = root / "frontier.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT)")
    conn.commit()
    conn.close()
    before = db.read_bytes()
    with pytest.raises(JsCheckRefused, match="不是第一轮"):
        make_check(root, Server({}), FakeBrowser({})).run()
    with pytest.raises(JsCheckRefused):
        jscheck.set_verdict(root, "x", "ok")
    assert db.read_bytes() == before and sorted(p.name for p in root.iterdir()) == ["frontier.db"]


def test_without_a_browser_every_type_stays_todo(tmp_path):
    root = make_root(tmp_path)
    with opened(root) as f:
        add_page(f, f"{EE}/a.html")
    server = Server({})
    with pytest.raises(jscheck.BrowserMissing):
        make_check(root, server, FakeBrowser({}, missing=True)).run()
    assert server.hits == []
    with opened(root) as f:
        assert [t["verdict"] for t in types(f).values()] == ["todo"]
        end = f.q("SELECT level, message FROM event WHERE code='jscheck_end'")
        assert end[0]["level"] == "error" and json.loads(end[0]["message"])["reason"] == "error"
        assert f.q("SELECT count(*) FROM request")[0][0] == 0


# ---------------------------------------------------------------- the requests
def test_every_request_goes_out_through_our_own_queues_one_at_a_time_with_the_gap(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/news.html"
    with opened(root) as f:
        add_page(f, url)
    shown = lines("消息", 12)
    gviz = "https://docs.google.com/spreadsheets/d/abc/gviz/tq?tqx=out:csv"
    post = f"{EE}/_getAJAX.html"
    server = Server({url: (200, HTML, page(*shown)), f"{EE}/js/app.js": (200, {"content-type": "text/javascript"},
                                                                          "var a=1;"),
                     gviz: (200, {"content-type": "text/csv"}, "a,b\n"), post: (200, HTML, "<p>ok</p>")})
    asks = [Ask(f"{EE}/js/app.js", kind="script"), Ask(f"{EE}/js/app.js", kind="script"),
            Ask(f"{EE}/css/site.css", kind="stylesheet"), Ask(f"{EE}/img/logo.png", kind="image"),
            Ask(f"{EE}/fonts/a.woff2", kind="font"), Ask(f"{EE}/v/clip.mp4", kind="media"),
            Ask("https://www.googletagmanager.com/gtag/js?id=G-1", kind="script"),
            Ask("https://www.youtube.com/embed/x", kind="document"),
            Ask(gviz, kind="fetch"),  # docs.google.com: never fetched by the crawl, sent to judge (§8.4)
            Ask(post, "POST", "xhr", {"content-type": jscheck._FORM, "x-requested-with": "XMLHttpRequest",
                                      "user-agent": "HeadlessChrome", "accept-encoding": "gzip, br, zstd"},
                b"act=news&cID=2"),
            Ask(f"{EE}/favicon.ico", kind="other")]
    browser = FakeBrowser({url: FakePage(page(*shown), asks)})
    make_check(root, server, browser).run()
    assert server.asked() == [("GET", url), ("GET", f"{EE}/js/app.js"), ("GET", gviz), ("POST", post)]
    posted = server.hits[-1]
    assert posted.content == b"act=news&cID=2" and posted.headers["content-type"] == jscheck._FORM
    assert posted.headers["x-requested-with"] == "XMLHttpRequest"
    assert {r.headers["user-agent"] for r in server.hits} == {USER_AGENT}
    assert posted.headers.get("accept-encoding") != "gzip, br, zstd"  # the client asks only what it can decode
    with opened(root) as f:
        reqs = f.q("SELECT * FROM request ORDER BY id")
        assert len(reqs) == len(server.hits) and {r["purpose"] for r in reqs} == {"jscheck"}
        assert all(r["warc"].startswith("warc/") and "/jscheck-" in r["warc"] for r in reqs)
        by_queue: dict[str, list] = {}
        for r in reqs:
            by_queue.setdefault(r["queue"], []).append(r)
        assert set(by_queue) == {"ip:ee.nknu.edu.tw", "google"}
        for rows in by_queue.values():  # one at a time, and at least the gap's least after each answer
            rows.sort(key=lambda r: r["started"])
            for a, b in itertools.pairwise(rows):
                assert b["started"] - a["ended"] >= 2.0 - 1e-6
        assert types(f)[key(url)]["verdict"] == "ok"
    assert list((root / "warc").rglob("jscheck-*.warc.gz"))


def test_a_login_system_or_an_internal_service_is_never_asked(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/side.html"
    with opened(root) as f:
        add_page(f, url)
    server = Server({url: (200, HTML, page(*lines("消息", 12)))})
    asks = [Ask("https://sso.nknu.edu.tw/", kind="xhr"), Ask("https://pbws.nknu.edu.tw/Service.asmx?op=x", kind="xhr")]
    make_check(root, server, FakeBrowser({url: FakePage(page(*lines("消息", 12)), asks)})).run()
    assert {h.url.host for h in server.hits} == {"ee.nknu.edu.tw"}
    with opened(root) as f:  # what the page wanted from them is unseen: never a quiet ok
        t = types(f)[key(url)]
        assert (t["verdict"], t["why"], t["tries"]) == ("unresolved", "inconclusive:not_sent", 2)


def test_a_data_request_s_redirect_is_followed_through_our_queue_and_a_login_hop_is_never_asked(tmp_path):
    root = make_root(tmp_path)
    url, api, moved = f"{EE}/list.html", f"{EE}/api/old.json", f"{EE}/api/new.json"
    shown = lines("消息", 12)
    with opened(root) as f:
        add_page(f, url)
        add_page(f, f"{EE}/member.html")
    server = Server({url: (200, HTML, page(*shown)), f"{EE}/member.html": (200, HTML, page(*shown)),
                     api: (302, {"location": "/api/new.json"}, ""), moved: (200, JSON_T, "{}"),
                     f"{EE}/api/me.json": (302, {"location": "https://sso.nknu.edu.tw/"}, "")})
    browser = FakeBrowser({url: FakePage(page(*shown), [Ask(api, "POST", "xhr", {"content-type": jscheck._FORM},
                                                            b"a=1")]),
                           f"{EE}/member.html": FakePage(page(*shown), [Ask(f"{EE}/api/me.json", kind="xhr")])})
    make_check(root, server, browser).run()
    assert ("POST", api) in server.asked() and ("GET", moved) in server.asked()  # a POST's 302 goes on as a GET
    assert all(h.url.host == "ee.nknu.edu.tw" for h in server.hits)  # the hop to the login was never sent
    with opened(root) as f:
        t = types(f)
        assert t[key(url)]["verdict"] == "ok"
        assert t[key(f"{EE}/member.html")]["why"] == "inconclusive:not_sent"
        assert f.q("SELECT count(*) FROM request")[0][0] == len(server.hits)


def test_a_page_that_now_answers_3xx_is_sent_on(tmp_path):
    root = make_root(tmp_path)
    url, there = f"{EE}/moved.html", f"{EE}/new/index.html"
    with opened(root) as f:
        uid = add_page(f, url, text_chars=20)
    server = Server({url: (301, {"location": there}, ""), there: (200, HTML, page(*lines("新址", 12)))})
    make_check(root, server, FakeBrowser({there: FakePage(page(*lines("新址", 12)))})).run()
    assert server.asked() == [("GET", url), ("GET", there)]
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "ok" and f.row_by_id(uid)["final_url"] == there
        assert f.row(there)["state"] == "later"


def test_a_redirect_loop_is_cut_short(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/loop.html"
    with opened(root) as f:
        add_page(f, url)
    server = Server({url: (302, {"location": url}, "")})
    make_check(root, server, FakeBrowser({})).run()
    assert 2 <= len(server.hits) <= 2 * (jscheck.MAX_NAVS + 1)  # two looks, each cut at the cap
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "unresolved"


# ---------------------------------------------------------------- the verdicts
def test_a_page_its_script_fills_is_js_and_the_data_request_is_named(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/news.html"
    with opened(root) as f:
        uid = add_page(f, url)
    shown = lines("消息", 15)
    api = f"{EE}/api/news.json"
    server = Server({url: (200, HTML, page("最新消息", "載入中")),
                     api: (200, JSON_T, json.dumps({"items": shown}, ensure_ascii=False))})
    rep = make_check(root, server, FakeBrowser({url: FakePage(page("最新消息", *shown), [Ask(api, kind="xhr")])})).run()
    with opened(root) as f:
        t = types(f)[key(url)]
        assert (t["verdict"], t["source"], t["method"], t["why"], t["tries"]) == ("js", "jscheck", "data_url", None, 1)
        assert json.loads(t["data_urls"]) == {"requests": [{"url": api, "method": "GET", "new_text": True}]}
        assert t["sample_id"] == uid and t["direct_chars"] < t["rendered_chars"]
        assert [tuple(r) for r in f.q("SELECT url_id, verdict FROM js_page")] == [(uid, "js")]
        assert result(f, uid) == "js"
        end = f.q("SELECT message FROM event WHERE code='jscheck_end'")
        assert json.loads(end[-1]["message"])["reason"] == "finished"
    assert rep.reason == "finished" and rep.todo_left == 0


def test_new_text_in_a_data_answer_below_the_js_line_is_never_ok(tmp_path):
    """english's en/index.html of 10-25: 174 new characters, three titles a POST brought: no conclusion, twice, then
    unresolved for a person (§8.4)."""
    root = make_root(tmp_path)
    urls = [f"{EE}/en/index.html?p={i}" for i in (1, 2)]
    shown, extra = lines("消息", 6), lines("新聞", 5, 10)
    routes = {u: (200, HTML, page(*shown)) for u in urls}
    routes[f"{EE}/_getAJAX.html"] = (200, HTML, "".join(f"<li>{x}</li>" for x in extra))
    pages = {u: FakePage(page(*shown, *extra), [Ask(f"{EE}/_getAJAX.html", "POST", "xhr",
                                                    {"content-type": jscheck._FORM}, b"cID=2")]) for u in urls}
    with opened(root) as f:
        ids = [add_page(f, u) for u in urls]
    rep = make_check(root, Server(routes), FakeBrowser(pages)).run()
    with opened(root) as f:
        t = types(f)[key(urls[0])]
        assert (t["verdict"], t["why"], t["tries"]) == ("unresolved", "inconclusive:data_text", 2)
        assert [r[0] for r in f.q("SELECT verdict FROM js_page ORDER BY url_id")] == ["unresolved", "unresolved"]
        assert [result(f, i) for i in ids] == ["unresolved", "unresolved"]
    assert ("unresolved" in {v for _k, v, _w, _n in rep.tested}) and len(rep.unresolved_pages) == 2


def test_a_marquee_s_repeats_are_not_new_text(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/home.php"
    with opened(root) as f:
        add_page(f, url)
    own = lines("消息", 10)
    marquee = "HEIGHLIGNTS NKNU " * 30
    server = Server({url: (200, HTML, page(*own))})
    make_check(root, server, FakeBrowser({url: FakePage(page(*own, marquee, marquee, marquee))})).run()
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "ok"


def test_a_click_this_round_cannot_press_is_click_and_its_elements_are_kept(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{EE}/tabs.php?c={i}" for i in range(4)]
    body = page(*lines("消息", 12))
    with opened(root) as f:
        ids = [add_page(f, u, verdict="todo", why="click", source="import") for u in urls]
        add_res(f, ids[2], "click:showTab(3)", text="第三區")
        add_res(f, ids[2], "click:showTab(4)", text="第四區")
        add_res(f, ids[3], "click:showTab(1)", need="no", why="click:shown_on_load", text="第一區")
        # a tab a platform rule presses is no click this round cannot press
        add_res(f, ids[0], "click:loadSubcategoryContent(16)", need="must", why=None)
    browser = FakeBrowser({u: FakePage(body) for u in urls})
    make_check(root, Server({u: (200, HTML, body) for u in urls}), browser).run()
    assert browser.opened == [urls[2], urls[3], urls[0]]  # the click's pages first, three at most
    with opened(root) as f:
        t = types(f)[key(urls[0])]
        assert (t["verdict"], t["why"], t["tries"]) == ("click", "click", 3)
        assert json.loads(t["data_urls"])["clicks"] == [{"key": "click:showTab(3)", "text": "第三區"},
                                                         {"key": "click:showTab(4)", "text": "第四區"}]
        jp = dict(f.q("SELECT url_id, verdict FROM js_page"))
        assert jp == {ids[2]: "click", ids[3]: "ok", ids[0]: "ok"}


def test_a_tab_shown_on_load_alone_is_judged_by_the_comparison(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/one-tab.php"
    body = page(*lines("消息", 12))
    with opened(root) as f:
        uid = add_page(f, url)
        add_res(f, uid, "click:showTab(1)", need="no", why="click:shown_on_load")
    make_check(root, Server({url: (200, HTML, body)}), FakeBrowser({url: FakePage(body)})).run()
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "ok"


def test_frames_of_the_school_are_frame(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/frame.html"
    with opened(root) as f:
        add_page(f, url)
    body = page(*lines("消息", 3))
    make_check(root, Server({url: (200, HTML, body)}),
               FakeBrowser({url: FakePage(body, frames=[page(*lines("內容", 12))])})).run()
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "frame"


# ---------------------------------------------------------------- sent elsewhere
def test_a_page_the_browser_leaves_is_a_redirect_and_where_it_stopped_is_only_round_two_s(tmp_path):
    root = make_root(tmp_path)
    a, b, c, d = (f"{EE}/job{i}.html" for i in (1, 2, 3, 4))
    home = f"{EE}/index.html"
    short = page("請稍候")
    with opened(root) as f:
        ia = add_page(f, a, text_chars=3, depth=4)
        ib = add_page(f, b, text_chars=3)
        add_page(f, c, text_chars=400)  # its own content: judged by itself, not opened for the redirect
        add_page(f, d, text_chars=3, flags="js_redirect")  # the crawl read its redirect already
        pending_before = f.q("SELECT count(*) FROM url WHERE state='pending'")[0][0]
        n_before = f.q("SELECT count(*) FROM url")[0][0]
    server = Server({a: (200, HTML, short), b: (200, HTML, short), home: (200, HTML, page(*lines("首頁", 12)))})
    browser = FakeBrowser({a: FakePage(short, goes_to=home), b: FakePage(short, goes_to=home),
                           home: FakePage(page(*lines("首頁", 12)))})
    rep = make_check(root, server, browser).run()
    assert browser.opened == [a, b]  # the sample, then the type's other short page without the flag
    with opened(root) as f:
        t = types(f)[key(a)]
        assert t["verdict"] == "ok" and t["source"] == "jscheck"
        assert "js_redirect" in t["note"].split(",") and f"stop:{home}" in t["note"].split(",")
        for uid in (ia, ib):
            row = f.row_by_id(uid)
            assert "js_redirect" in row["flags"].split(",") and row["final_url"] == home
            assert row["fetched_at"] == OLD_FETCH  # the look stays after the page's last fetch
        later = f.q("SELECT * FROM url WHERE url=?", (home,))
        assert len(later) == 1
        assert (later[0]["state"], later[0]["reason"], later[0]["found_via"], later[0]["depth"],
                later[0]["found_from"]) == ("later", "round2:jscheck_redirect", "jscheck_redirect", 4, ia)
        assert f.q("SELECT count(*) FROM url WHERE state='pending'")[0][0] == pending_before == 0
        assert f.q("SELECT count(*) FROM url")[0][0] == n_before + 1
        assert result(f, ia) == "redirect"
    assert [w for _p, _s, w in rep.redirects][1] == "url 表里已经有这一行，照旧"


def test_where_it_stopped_is_left_as_it_is_when_the_round_has_its_row(tmp_path):
    root = make_root(tmp_path)
    a, home = f"{EE}/old.html", f"{EE}/main.html"
    with opened(root) as f:
        add_page(f, a, text_chars=3)
        add_page(f, home, verdict="ok", source="import", why=None)
        before = dict(f.row(home))
    server = Server({a: (200, HTML, page("請稍候")), home: (200, HTML, page(*lines("首頁", 12)))})
    make_check(root, server, FakeBrowser({a: FakePage(page("請稍候"), goes_to=home)})).run()
    with opened(root) as f:
        assert dict(f.row(home)) == before


def test_a_page_sent_to_a_login_is_login_and_the_login_is_never_asked(tmp_path):
    root = make_root(tmp_path)
    a, b = f"{EE}/apply_step1.php", f"{EE}/guide.php"
    login = "https://sso.nknu.edu.tw/"
    with opened(root) as f:
        ia = add_page(f, a, text_chars=10)
        ib = add_page(f, b, text_chars=400, why="new")
        n_before = f.q("SELECT count(*) FROM url")[0][0]
    server = Server({a: (200, HTML, page("請先登入")), b: (200, HTML, page(*lines("說明", 20)))})
    browser = FakeBrowser({a: FakePage(page("請先登入"), goes_to=login),
                           b: FakePage(page(*lines("說明", 20)), goes_to=login)})
    rep = make_check(root, server, browser).run()
    assert all(h.url.host == "ee.nknu.edu.tw" for h in server.hits)
    with opened(root) as f:
        ta, tb = types(f)[key(a)], types(f)[key(b)]
        assert ta["verdict"] == tb["verdict"] == "ok"
        assert "js_redirect_login" in ta["note"].split(",")
        assert set(f.row_by_id(ia)["flags"].split(",")) == {"js_redirect", "login"}
        assert result(f, ia) == "login"
        assert f.row_by_id(ib)["flags"] is None  # it has its own content: judged by it (§8.3's reason)
        assert f.q("SELECT count(*) FROM url")[0][0] == n_before  # a login is never a row
    assert rep.login_with_content == [(b, login)]


# ---------------------------------------------------------------- no conclusion
def test_no_answer_twice_is_unresolved_and_a_failing_sample_is_swapped(tmp_path):
    root = make_root(tmp_path)
    gone = [f"{EE}/x{i}.html" for i in (1, 2)]
    swap = [f"{EE}/y{i}.html" for i in (1, 2)]
    alone = f"{EE}/only.html"
    body = page(*lines("消息", 12))
    with opened(root) as f:
        for u in (*gone, *swap, alone):
            add_page(f, u)
        y2 = f.row(swap[1])["id"]
    server = Server({swap[1]: (200, HTML, body)})  # every other page answers 404 now
    browser = FakeBrowser({u: FakePage(body) for u in (*gone, *swap, alone)})
    make_check(root, server, browser).run()
    with opened(root) as f:
        t = types(f)
        assert (t[key(gone[0])]["verdict"], t[key(gone[0])]["why"], t[key(gone[0])]["tries"]) == \
            ("unresolved", "inconclusive:status_404", 2)
        assert (t[key(swap[0])]["verdict"], t[key(swap[0])]["tries"], t[key(swap[0])]["sample_id"]) == ("ok", 2, y2)
        assert (t[key(alone)]["verdict"], t[key(alone)]["tries"]) == ("unresolved", 2)
    assert browser.opened.count(alone) == 2  # no other page to swap to: the same one once more


def test_a_type_with_no_page_of_this_round_to_open_is_unresolved(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/failed.html"
    with opened(root) as f:
        add_page(f, url)
        f.x("UPDATE url SET state='failed', status=NULL WHERE url=?", (url,))
    server = Server({})
    make_check(root, server, FakeBrowser({})).run()
    assert server.hits == []
    with opened(root) as f:
        assert (types(f)[key(url)]["verdict"], types(f)[key(url)]["why"]) == ("unresolved", "inconclusive:no_sample")


# ---------------------------------------------------------------- pages opened one by one
def test_every_page_with_a_strong_sign_is_opened_whoever_judged_its_type(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{EE}/list.php?id={i}" for i in range(6)]
    body = page(*lines("消息", 12))
    with opened(root) as f:
        ids = [add_page(f, u, verdict="ok", why=None, source="jscheck", flags="js_hint") for u in urls[:5]]
        seen = add_page(f, urls[5], verdict="ok", why=None, source="jscheck", flags="js_hint")
        f.x("INSERT INTO js_page(url_id, page_type, at, verdict) VALUES (?,?,?, 'ok')",
            (seen, key(urls[5]), "2001-01-01T00:00:00+00:00"))  # opened after its last fetch: not again
        stale = add_page(f, f"{EE}/list.php?id=9&x=1", verdict="ok", why=None, source="manual", flags="js_hint")
        f.x("INSERT INTO js_page(url_id, page_type, at, verdict) VALUES (?,?,?, 'ok')",
            (stale, None, "1999-01-01T00:00:00+00:00"))  # opened before its last fetch: again
        assert {result(f, i) for i in ids} == {"unchecked"}
    every = urls + [f"{EE}/list.php?id=9&x=1"]
    browser = FakeBrowser({u: FakePage(body) for u in every})
    make_check(root, Server({u: (200, HTML, body) for u in every}), browser).run()
    assert sorted(browser.opened) == sorted([*urls[:5], f"{EE}/list.php?id=9&x=1"])
    with opened(root) as f:
        assert {result(f, i) for i in (*ids, stale)} == {"ok"}
        assert types(f)[key(urls[0])]["source"] == "jscheck"  # the type is not judged again


def test_a_page_with_a_strong_sign_jscheck_finds_js_is_js_and_its_type_stays(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/tabs.php"
    with opened(root) as f:
        uid = add_page(f, url, verdict="ok", why=None, source="manual", flags="js_hint")
    server = Server({url: (200, HTML, page("載入中"))})
    make_check(root, server, FakeBrowser({url: FakePage(page("載入中", *lines("內容", 15)))})).run()
    with opened(root) as f:
        assert result(f, uid) == "js" and types(f)[key(url)]["verdict"] == "ok"


def test_a_jump_menu_fired_on_load_is_opened_whatever_its_type_and_where_it_lands_is_round_two_s(tmp_path):
    root = make_root(tmp_path)
    notes, stays = f"{EE}/notes.html", f"{EE}/notes_ii.html?getId=5"
    landed = f"{EE}/notes.html?getId=4"
    with opened(root) as f:
        a = add_page(f, notes, verdict="ok", why=None, source="import", text_chars=80, note="js_redirect")
        b = add_page(f, stays, verdict="ok", why=None, source="import")
        for uid in (a, b):  # the menu's own entry (the platform file: round2) and the crawler's mark beside it
            add_res(f, uid, "select:.category", how="unhandled", need="round2", why="round2:unhandled")
            add_res(f, uid, "auto:select:.category", how="auto", need="no", why="select:auto")
    body = page(*lines("筆記", 12))
    server = Server({notes: (200, HTML, page("學年")), stays: (200, HTML, body), landed: (200, HTML, body)})
    browser = FakeBrowser({notes: FakePage(page("學年"), goes_to=landed), stays: FakePage(body)})
    make_check(root, server, browser).run()
    assert sorted(browser.opened) == sorted([notes, stays])
    with opened(root) as f:
        assert f.row(landed)["state"] == "later" and f.row(landed)["reason"] == "round2:jscheck_redirect"
        assert "js_redirect" in f.row_by_id(a)["flags"].split(",")
        assert f.row_by_id(b)["flags"] is None and dict(f.q("SELECT url_id, verdict FROM js_page"))[b] == "ok"
        assert types(f)[key(notes)]["source"] == "import"  # the type's verdict is not changed by it


# ---------------------------------------------------------------- not opened
def test_old_types_with_no_page_this_round_are_listed_with_the_old_archive_s_state(tmp_path):
    root = make_root(tmp_path)
    old = tmp_path / "archive-2026-10-24"
    old.mkdir()
    conn = sqlite3.connect(old / "frontier.db")
    conn.execute("CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT UNIQUE, state TEXT, status INTEGER, reason TEXT)")
    conn.execute("INSERT INTO url(url, state, status) VALUES ('http://ee.nknu.edu.tw/old1.html', 'done', 404)")
    conn.commit()
    conn.close()
    before = (old / "frontier.db").read_bytes()
    with opened(root) as f:
        for k, sample, verdict, why in [
                (key(f"{EE}/old1.html"), f"{EE}/old1.html", "todo", "inconclusive:status_404"),
                (key(f"{EE}/gone.html"), f"{EE}/gone.html", "ok", None),
                ("id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()", "https://id.nknu.edu.tw/uploads/a-021.pn", "todo",
                 "inconclusive:broken_sample")]:
            parts = pagetypes.parse_key(k)
            f.x("INSERT INTO page_type(key, site, host, path_tpl, params, verdict, why, sample_url, source) "
                "VALUES (?,?,?,?,'[]',?,?,?,'import')", (k, parts.site, parts.host, parts.path_tpl, verdict, why, sample))
    server = Server({})
    browser = FakeBrowser({})
    rep = make_check(root, server, browser, old=old).run()
    assert browser.opened == [] and server.hits == []
    assert {k: o for k, _v, _s, o in rep.not_seen} == {key(f"{EE}/old1.html"): "done 404（http://ee.nknu.edu.tw/old1.html）",
                                                       key(f"{EE}/gone.html"): "旧存档里没有"}
    assert rep.withdrawn == ["id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()"]
    assert (old / "frontier.db").read_bytes() == before
    assert sorted(p.name for p in old.iterdir()) == ["frontier.db"]  # immutable: not even a -wal or -shm
    with opened(root) as f:
        assert types(f)[key(f"{EE}/old1.html")]["verdict"] == "todo"  # not opened, and not counted (n_pages=0)
        assert json.loads(f.q("SELECT message FROM event WHERE code='jscheck_end'")[0][0])["todo_left"] == 0


# ---------------------------------------------------------------- servers that rest, our network, a stop
def test_a_server_that_says_503_rests_and_the_page_is_looked_at_after_its_rest(tmp_path):
    root = make_root(tmp_path)
    url, api = f"{EE}/busy.html", f"{EE}/api/list.json"
    body = page(*lines("消息", 12))
    calls = []

    def busy(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, headers=JSON_T, text="{}")

    with opened(root) as f:
        add_page(f, url)
    server = Server({url: (200, HTML, body), api: busy})
    c = make_check(root, server, FakeBrowser({url: FakePage(body, [Ask(api, kind="xhr")])}))
    c.run()
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "ok"  # the second look, after the rest
        pause = f.q("SELECT until FROM pause WHERE queue='ip:ee.nknu.edu.tw'")[0][0]
        assert f.q("SELECT count(*) FROM event WHERE code='server_rest'")[0][0] == 1
        after = f.q("SELECT min(started) FROM request WHERE started >= ?", (pause,))[0][0]
        assert after is not None  # nothing went to that server before its rest was over
        assert f.q("SELECT count(*) FROM request WHERE started > ? AND started < ?",
                   (pause - jscheck.SERVER_REST_S + 1, pause))[0][0] == 0


def test_our_network_down_stops_the_check_and_writes_nothing_of_the_page(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/a.html"

    def down(request):
        raise httpx.ConnectError("[Errno 65] No route to host", request=request)

    with opened(root) as f:
        add_page(f, url)
    rep = make_check(root, Server({url: down}), FakeBrowser({url: FakePage(page("x"))}), netcheck=lambda: False).run()
    assert rep.reason == "error" and "offline" in rep.detail
    with opened(root) as f:
        assert types(f)[key(url)]["verdict"] == "todo"
        assert f.q("SELECT count(*) FROM js_page")[0][0] == 0


def test_a_stop_leaves_every_type_it_had_not_judged_as_it_was(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{EE}/{n}.html" for n in ("alpha", "beta", "gamma")]  # three types
    body = page(*lines("消息", 12))
    with opened(root) as f:
        for u in urls:
            add_page(f, u)
    holder = {}
    browser = FakeBrowser({u: FakePage(body) for u in urls}, on_open=lambda _u: holder["c"].halt("stop", "sigterm"))
    c = make_check(root, Server({u: (200, HTML, body) for u in urls}), browser)
    holder["c"] = c
    rep = c.run()
    assert rep.reason == "stop" and len(browser.opened) == 1
    with opened(root) as f:
        assert {t["verdict"] for t in types(f).values()} == {"todo"}
        assert f.q("SELECT count(*) FROM js_page")[0][0] == 0
        end = json.loads(f.q("SELECT message FROM event WHERE code='jscheck_end'")[0][0])
        assert (end["reason"], end["detail"], end["todo_left"]) == ("stop", "sigterm", 3)
        assert jscheck.last_end(f) is None  # only a finished one counts


def test_only_a_finished_check_after_the_last_run_counts(tmp_path):
    root = make_root(tmp_path)
    with opened(root) as f:
        f.x("INSERT INTO event(at, level, code, message) VALUES ('2026-10-26T01:00:00+00:00', 'info', 'run_end', '{}')")
        f.x("INSERT INTO event(at, level, code, message) VALUES ('2026-10-26T02:00:00+00:00', 'info', 'jscheck_end', ?)",
            (json.dumps({"reason": "finished", "todo_left": 0}),))
        assert jscheck.last_end(f)["at"] == "2026-10-26T02:00:00+00:00"
        f.x("INSERT INTO event(at, level, code, message) VALUES ('2026-10-26T03:00:00+00:00', 'info', 'run_end', '{}')")
        assert jscheck.last_end(f) is None


# ---------------------------------------------------------------- a person's verdict
def test_set_writes_a_person_s_verdict_sends_nothing_and_waits_for_no_run(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/en/index.html"
    with opened(root) as f:
        uid = add_page(f, url, verdict="unresolved", why="inconclusive:data_text", source="jscheck")
        f.x("INSERT INTO js_page(url_id, page_type, at, verdict, why) VALUES (?,?,?,'unresolved',"
            "'inconclusive:data_text')", (uid, key(url), "2026-10-26T00:00:00+00:00"))
        assert result(f, uid) == "unresolved"
    lock = hold_run_lock(root)
    try:
        with pytest.raises(JsCheckRefused, match="archive run"):
            jscheck.set_verdict(root, key(url), "ok", note="內容在 news.html, 直接抓得到")
    finally:
        lock.close()
    with pytest.raises(JsCheckRefused):
        jscheck.set_verdict(root, key(url), "todo")
    with pytest.raises(JsCheckRefused, match="没有这个网页类型"):
        jscheck.set_verdict(root, "x|y|z|()", "ok")
    jscheck.set_verdict(root, key(url), "ok", note="內容在 news.html, 直接抓得到")
    with opened(root) as f:
        t = types(f)[key(url)]
        assert (t["verdict"], t["source"], t["why"]) == ("ok", "manual", None)
        assert t["note"] == "manual:內容在 news.html， 直接抓得到"
        assert result(f, uid) == "ok"  # its page follows the person's verdict
        assert f.q("SELECT count(*) FROM request")[0][0] == 0


# ---------------------------------------------------------------- reconcile's R6
def test_the_spot_checker_shows_a_page_through_the_same_queues_and_writes_no_row(tmp_path):
    root = make_root(tmp_path)
    url, login = f"{EE}/index.html", f"{EE}/login.aspx"
    shown = lines("首頁", 12)
    server = Server({url: (200, HTML, page("載入中")), f"{EE}/api/home.json": (200, JSON_T, "{}")})
    browser = FakeBrowser({url: FakePage(page(*shown), [Ask(f"{EE}/api/home.json", kind="xhr")])})
    clock = Clock()
    with opened(root) as f:
        n_url = f.q("SELECT count(*) FROM url")[0][0]
        with jscheck.SpotChecker(root, f, browser=browser, clock=clock, sleep=clock.sleep, rng=random.Random(1),
                                 client_factory=lambda warc: archive_client(warc, transport=httpx.MockTransport(server),
                                                                            clock=clock, sleep=clock.sleep),
                                 resolver=lambda h: (f"ip:{h}", None, h)) as spot:
            got = spot(url)
            refused = spot(login)
        assert got.final_url == url and "首頁第0則" in got.html and got.error is None
        assert got.screenshot.startswith("spotcheck/") and (root / got.screenshot).exists()
        assert refused.error and "login" in refused.error  # a login is never asked, not even to look
        reqs = f.q("SELECT purpose, url, warc FROM request ORDER BY id")
        assert [(r["purpose"], r["url"]) for r in reqs] == [("spotcheck", url), ("spotcheck", f"{EE}/api/home.json")]
        assert all("/spotcheck-" in r["warc"] for r in reqs)
        assert f.q("SELECT count(*) FROM url")[0][0] == n_url and f.q("SELECT count(*) FROM js_page")[0][0] == 0
    assert browser.started and browser.closed and server.asked() == [("GET", url), ("GET", f"{EE}/api/home.json")]


# ---------------------------------------------------------------- the real browser
_SHOWN = lines("消息", 12)
_MORE = lines("更多", 15, 30)
_LOCAL_PAGES = {
    # a style sheet and a picture are never asked; a WebSocket, which the route does not catch, dies at the dead proxy
    "/plain.html": page(*_SHOWN, extra='<link rel="stylesheet" href="/style.css"><img src="/pic.png"><script>'
                                       'try { new WebSocket("ws://" + location.host + "/ws"); } catch (e) {}</script>'),
    "/js.html": page("最新消息", extra='<div id="box">載入中</div><script>fetch("/data.json").then(r => r.json())'
                                        '.then(d => { document.getElementById("box").innerHTML = '
                                        'd.items.map(x => "<p>" + x + "</p>").join(""); });</script>'),
    "/later.html": page("請稍候", extra='<script>setTimeout(function () { location.href = "/target.html"; }, 50);'
                                       '</script>'),
    "/target.html": page(*lines("首頁", 12)),
    "/click.html": page(*_SHOWN, extra='<button onclick="fetch(\'/more.json\')">更多</button>'),
    "/target2.html": page(*lines("新址", 12)),
}


class _Local(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list[tuple[str, str, str]]] = []

    def log_message(self, *args) -> None:
        pass

    def _answer(self) -> None:
        type(self).hits.append((self.command, self.path, self.headers.get("user-agent", "")))
        if self.command == "POST":
            self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path == "/moved.html":
            self.send_response(302)
            self.send_header("Location", "/target2.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/data.json":
            body, ctype = json.dumps({"items": _MORE}, ensure_ascii=False).encode(), "application/json"
        elif self.path in _LOCAL_PAGES:
            body, ctype = _LOCAL_PAGES[self.path].encode(), "text/html; charset=utf-8"
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


# the test pages' host: a name of its own that resolves to the loopback server, since the crawl's rules leave this
# machine's own addresses out (scope.judge loopback, 11-06: no row for where a page stopped there)
PAGES_HOST = "jstest.example"


def _pages_host_on_loopback(monkeypatch) -> None:
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: real("127.0.0.1" if host == PAGES_HOST else host, *a, **k))


@pytest.mark.browser
def test_this_machine_s_chrome_asks_everything_through_our_queue(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api", reason="没装 Playwright（pyproject 的 extra browser：uv sync --extra "
                                                       "browser），这个测试要用它开本机的 Chrome")
    _Local.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _pages_host_on_loopback(monkeypatch)
    base = f"http://{PAGES_HOST}:{server.server_address[1]}"
    try:
        root = make_root(tmp_path)
        names = ("plain", "js", "later", "click", "moved")
        with opened(root) as f:
            ids = {n: add_page(f, f"{base}/{n}.html", text_chars=4 if n == "later" else 300) for n in names}
            add_res(f, ids["click"], "click:fetch(more.json)", text="更多")
        c = JsCheck(root, gap=(1.0, 1.0), plan=OpenPlan(), resolver=resolve, netcheck=lambda: True,  # no lookup
                    browser=jscheck.PlaywrightBrowser(settle_ms=300, idle_ms=10_000, goto_ms=60_000))
        try:
            c.run()
        except jscheck.BrowserMissing as exc:
            pytest.skip(f"这台机器打不开 Chrome：{exc}")
        with opened(root) as f:
            got = {n: types(f)[key(f"{base}/{n}.html")] for n in names}
            assert {n: t["verdict"] for n, t in got.items()} == {"plain": "ok", "js": "js", "later": "ok",
                                                                "click": "click", "moved": "ok"}
            assert got["js"]["method"] == "data_url"
            assert "js_redirect" in got["later"]["note"].split(",")
            assert "js_redirect" in got["moved"]["note"].split(",")  # a 3xx Chrome followed through our client
            for n in ("target", "target2"):
                assert f.row(f"{base}/{n}.html")["state"] == "later"
            reqs = f.q("SELECT method, url FROM request WHERE purpose='jscheck'")
            assert f.q("SELECT count(*) FROM request")[0][0] == len(reqs)
        paths = [p for _m, p, _ua in _Local.hits]
        assert len(_Local.hits) == len(reqs)  # every request that reached the server went out through our client
        with opened(root) as f, jscheck.SpotChecker(root, f, gap=(1.0, 1.0), resolver=resolve,
                                                     browser=jscheck.PlaywrightBrowser(settle_ms=300)) as spot:
            shown = spot(f"{base}/js.html")
            assert shown.error is None and _MORE[0] in shown.html and (root / shown.screenshot).stat().st_size > 0
            assert f.q("SELECT count(*) FROM request WHERE purpose='spotcheck'")[0][0] == 2
        assert {ua for _m, _p, ua in _Local.hits} == {USER_AGENT}
        assert not {"/style.css", "/pic.png", "/favicon.ico", "/ws"} & set(paths)
        assert paths.count("/more.json") == 0  # the browser only opens: nobody clicks
    finally:
        server.shutdown()
        server.server_close()
