"""The two reviews of phase 4 (10-26), each note that held first written here as a test that failed; the ones still
here are about what is left after the judgment-layer cut of 11-19: a round-1 row no page uses (a Common Crawl
attachment the school refuses) and a jump menu fired on load the browser never opened are counted, `jscheck --set`
writes an event that names the sites of the type, a finished run is never green on the top line by itself (whether
a site is 爬完 is its own page's), a look says what kept a page from loading whole, and Chrome gets no secret and
keeps Playwright's own switches. (The notes about verify's items, the reconciliation's coverage, its gates, its
staleness and R6, and the crawler's own reconciliation on a thread went with what they were about.)

No address leaves this machine: the school is httpx.MockTransport's (tests/conftest.py's guard), the browser tests
open this machine's Chrome on a server on 127.0.0.1 only. Every text is made up; no person's name."""

from __future__ import annotations

import asyncio
import http.server
import json
import re
import socket
import sys
import threading
import time
import types as pytypes
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from nknu.archive import crawler as mod
from nknu.archive import jscheck, monitor, pagetypes, profiles, progress, viewer
from nknu.archive.frontier import Frontier
from tests.archive.plans import OpenPlan
from tests.archive.test_round1_after_crawl import (
    BODY,
    CONTACT,
    HOME,
    NEWS,
    PLAN,
    SITE,
    TESTED,
    School,
    World,
    _all_steps,
    make_world,
    page,
)

OLD_PDF = f"https://{SITE}/files/old.pdf"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_POLL_S", 0.02)


def ro(w: World) -> Frontier:
    return Frontier(w.root / "frontier.db", readonly=True)


def result_of(w: World, url: str) -> str:
    f = ro(w)
    try:
        return f.q("SELECT result FROM url_result WHERE url=?", (url,))[0][0]
    finally:
        f.close()


def cc_also(tmp_path: Path, *urls: str) -> None:
    """Common Crawl remembers these too (with a 200)."""
    with open(tmp_path / "cc" / "CC-TEST.cdx", "a", encoding="utf-8") as fh:
        fh.writelines(f"tw,edu,nknu,w)/x 20260101000000 {json.dumps({'url': u, 'status': '200'})}\n" for u in urls)


class Refusing(School):
    """The made-up school, refusing (403) some addresses."""

    def __init__(self, html: dict[str, str], refused=()):
        super().__init__(html)
        self.refused = set(refused)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) in self.refused:
            self.asked.append((request.method, str(request.url)))
            return httpx.Response(403, headers=self.HTML, content=b"<html><body>forbidden</body></html>")
        return super().__call__(request)


# ---------------------------------------------------------------- 1. a round-1 row no page uses (S10)


def test_a_common_crawl_attachment_no_page_uses_that_the_school_refuses_keeps_the_site_from_done(tmp_path):
    w = make_world(tmp_path)
    cc_also(tmp_path, OLD_PDF)  # no page links to it: only Common Crawl remembers it
    w.school = Refusing(w.school.html, refused=(OLD_PDF,))
    _all_steps(w)
    assert result_of(w, OLD_PDF) == "refused"
    f = ro(w)
    try:
        uid = f.q("SELECT id FROM url WHERE url=?", (OLD_PDF,))[0][0]
        assert not f.q("SELECT 1 FROM page_res WHERE res_id=?", (uid,))  # no page uses it
    finally:
        f.close()
    got = w.not_done(monitor.ERROR)
    assert any("连接失败" in x for x in got.why), got.why
    red = [i.line() for i in got.issues if i.red]  # the site's problem list, in words
    assert any(OLD_PDF in x and "Common Crawl" in x for x in red), red
    assert any(i.url == OLD_PDF for i in got.issues)


def test_a_redirect_a_page_uses_is_not_counted_again_where_it_lands(tmp_path):
    """The rows a page reaches through redirects and presses are its own: only rows nothing uses are added."""
    f = Frontier(tmp_path / "frontier.db")
    try:
        f.x("INSERT INTO site_plan(site, name, imp, round, home) VALUES (?,?,?,?,?)", (SITE, "测试单位", "全校重要", "1",
                                                                                    HOME))

        def url(path: str, **cols) -> int:
            row = {"url": f"https://{SITE}{path}", "path": path, "host": SITE, "site": SITE, "owner_site": SITE,
                   "tier": 0, "kind": "file", "kind0": "file", "depth": 1, "found_via": "link", "state": "done",
                   "status": 200, "sniff": "pdf", "fetched_at": "2026-10-26T00:00:00+00:00"}
            row.update(cols)
            with f.lock:
                return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                                      tuple(row.values())).lastrowid

        home = url("/", kind="page", kind0="page", sniff="html")
        moved = url("/a.pdf", status=302, final_url=f"https://{SITE}/b.pdf", sniff=None)
        url("/b.pdf", status=403, found_via="redirect")
        f.x("INSERT INTO page_res(page_id, res_key, how, role, need, res_id) VALUES (?,?,?,?,?,?)",
            (home, f"https://{SITE}/a.pdf", "get", "file", "must", moved))
        url("/c.pdf", status=403, found_via="commoncrawl")
        got = monitor.facts(f)
    finally:
        f.close()
    assert [r.url for r in got.orphans.get(SITE, [])] == [f"https://{SITE}/c.pdf"]


# ---------------------------------------------------------------- 2. a jump menu fired on load (S12)


MENU_PAGE = page("消息", f"<p>最新消息列表。{BODY}</p><p>類別：<select class='category option'><option value='4'>106學年度</option>"
                       "<option value='5'>109學年度</option></select></p>"
                       "<script>$(function(){ var html = 'news'; $('.option').change(function(){ location.href = "
                       "html + '.html?getId=' + $(this).val(); }); var getId = ''; if (getId == '') "
                       "{ $('.category').change(); } })</script>")


def _ignore_menus(site_: str, key: str):
    """The platform file's decision for this test's site: its menus need nothing (as if a person decided so)."""
    return "ignore" if key.startswith("select:") else profiles.decision(site_, key)


def _menu_world(tmp_path: Path) -> World:
    w = make_world(tmp_path)
    w.known = (*TESTED, CONTACT)  # a browser test judged every type before the round, contact.html's too: none todo
    w.school.html[NEWS] = MENU_PAGE
    w.shown[NEWS] = MENU_PAGE
    crawler = w.crawler

    def with_decisions():
        c = crawler()
        c.decide = _ignore_menus
        return c

    w.crawler = with_decisions
    return w


def test_a_jump_menu_the_page_fires_on_load_is_to_be_judged_until_the_browser_opened_it(tmp_path):
    w = _menu_world(tmp_path)
    w.seed_and_run()
    f = ro(w)
    try:
        news = f.q("SELECT id FROM url WHERE url=?", (NEWS,))[0][0]
        marks = f.q("SELECT need, why, how FROM page_res WHERE page_id=? AND why='select:auto'", (news,))
        assert [tuple(r) for r in marks] == [("no", "select:auto", "auto")]
        menus = f.q("SELECT need, why FROM page_res WHERE page_id=? AND res_key LIKE 'select:%'", (news,))
        assert [tuple(r) for r in menus] == [("no", "unhandled:ignore")]
    finally:
        f.close()
    assert result_of(w, NEWS) == "unchecked"  # every type is ok, but the browser has not opened this page
    w.not_done(monitor.LOOK)
    assert w.jscheck().reason == "finished"  # it opens the page: the menu sends the browser nowhere here
    assert result_of(w, NEWS) == "ok"
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.why


# ---------------------------------------------------------------- 3. jscheck --set (S11)


def test_the_set_command_writes_an_event_that_names_the_sites_of_the_type(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from nknu.admin.cli import app
    from nknu.archive import round1

    monkeypatch.setattr(round1, "load", lambda *a, **k: PLAN)
    w = make_world(tmp_path)
    _all_steps(w)
    key = pagetypes.type_key(CONTACT)
    got = CliRunner().invoke(app, ["archive", "jscheck", "--dir", str(w.root), "--set", key, "ok"])
    assert got.exit_code == 0, got.output
    assert f"已改判：{key} → ok" in got.output
    f = ro(w)
    try:
        ev = f.q("SELECT message FROM event WHERE code='verdict_set'")
        assert [json.loads(r[0])["sites"] for r in ev] == [[SITE]]
    finally:
        f.close()


# ---------------------------------------------------------------- 7. the top bar (S1)


def _top_line(w) -> tuple[dict, str]:
    d = progress.Dashboard(w.root, background=False, cache_s=0)
    try:
        d.refresh_slow()
        snap = d.main()
    finally:
        d.close()
    return snap["run"], viewer.home_html(snap).split("<p class='big'>", 1)[1].split("</p>", 1)[0]


def test_a_finished_run_is_never_green_on_the_top_line_and_done_is_the_site_pages(tmp_path):
    """Review 10-26 (nothing left to fetch is no 爬完), on the 11-04 dashboard: the top line says 抓完了 in no colour of
    its own, with 不等于每个网站都爬完; whether a site is 爬完 is its own page's."""
    w = make_world(tmp_path)
    cc_also(tmp_path, OLD_PDF)
    w.school = Refusing(w.school.html, refused=(OLD_PDF,))
    _all_steps(w)
    run, top = _top_line(w)
    assert run["color"] != "green" and "c-green" not in top
    assert run["words"] != "抓完了" or "不等于每个网站都爬完" in run["tip"]
    dash, page = w.dashboard()
    assert dash["state"] != "done" and "✓ 爬完" not in page
    (tmp_path / "b").mkdir()
    w2 = make_world(tmp_path / "b")
    _all_steps(w2)
    run, top = _top_line(w2)
    assert run["words"] == "抓完了" and run["color"] == "none" and "不等于每个网站都爬完" in run["tip"]
    assert "c-green" not in top
    dash, page = w2.dashboard()
    assert dash["state"] == "done" and "✓ 爬完" in page


# ---------------------------------------------------------------- 8. a page the browser did not load whole


def test_a_look_says_what_kept_a_page_from_loading_whole():
    log = [jscheck.Exchange(HOME, "document", "GET", True, status=200),
           jscheck.Exchange("https://x.nknu.edu.tw/a.js", "script", "GET", False, error="connect"),
           jscheck.Exchange("https://x.nknu.edu.tw/p.png", "image", "GET", False, blocked="type")]
    obs = jscheck.Observation(HOME, HOME, "<html></html>", log=log)
    assert "a.js" in jscheck.incomplete(obs)
    assert jscheck.incomplete(jscheck.Observation(HOME, HOME, "", log=log[:1], not_idle=True)) == "not_idle"
    assert jscheck.incomplete(jscheck.Observation(HOME, HOME, "", log=log[::2])) is None  # an image is not needed


# ---------------------------------------------------------------- 10. Chrome: no secret, Playwright's own switches


def test_chrome_gets_no_secret_from_this_process(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-not-a-secret-0000")
    monkeypatch.setenv("NKNU_TEST_KEPT", "1")
    launched = {}

    class Chromium:
        async def launch(self, **kw):
            launched.update(kw)
            return pytypes.SimpleNamespace()

    class Started:
        chromium = Chromium()

        async def stop(self):
            pass

    class Manager:
        async def start(self):
            return Started()

    fake = pytypes.ModuleType("playwright.async_api")
    fake.async_playwright = Manager
    try:
        import playwright  # noqa: F401 - the real package, if there is one, holds the fake module below
    except ImportError:
        monkeypatch.setitem(sys.modules, "playwright", pytypes.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)
    asyncio.run(jscheck.PlaywrightBrowser().start())
    env = launched["env"]
    assert "GOOGLE_API_KEY" not in env and env.get("NKNU_TEST_KEPT") == "1"
    assert not {"GOOGLE_DEFAULT_CLIENT_ID", "GOOGLE_DEFAULT_CLIENT_SECRET"} & set(env)
    assert launched["args"] == list(jscheck.CHROME_ARGS)


def test_the_command_that_opens_chrome_holds_no_secret_from_its_start(monkeypatch, tmp_path):
    import os

    from typer.testing import CliRunner

    from nknu.admin.cli import app

    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-not-a-secret-0000")
    CliRunner().invoke(app, ["archive", "jscheck", "--dir", str(tmp_path / "none")])
    assert "GOOGLE_API_KEY" not in os.environ


def _playwright_disabled_features() -> list[str] | None:
    """Playwright's own --disable-features list (its chromiumSwitches), read from the installed driver's files."""
    try:
        import playwright
    except ImportError:
        return None
    lib = Path(playwright.__file__).parent.joinpath("driver", "package", "lib")
    for path in [lib / "coreBundle.js", *lib.rglob("chromiumSwitches.js")]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"disabledFeatures = \[(.*?)\]\.filter\(Boolean\)", text, re.DOTALL)
        if m:
            return re.findall(r'^\s*"([^"]+)"', m.group(1), re.MULTILINE)
    return None


def test_one_list_of_disabled_features_and_it_keeps_playwrights_own():
    flags = [a for a in jscheck.CHROME_ARGS if a.startswith("--disable-features=")]
    assert len(flags) == 1  # Chrome reads the last one only: it must hold Playwright's list and ours
    ours = set(flags[0].split("=", 1)[1].split(","))
    assert {"HttpsUpgrades", "HttpsFirstBalancedModeAutoEnable", "HttpsFirstModeV2ForTypicallySecureUsers"} <= ours
    theirs = _playwright_disabled_features()
    if theirs is None:
        pytest.skip("没装 Playwright：读不到它自己的 --disable-features")
    assert theirs and set(theirs) <= ours, sorted(set(theirs) - ours)


@pytest.mark.browser
def test_this_machine_s_chrome_keeps_both_lists_of_disabled_features():
    """What Chrome itself says it was started with (chrome://version, a page of its own: nothing is sent)."""
    pytest.importorskip("playwright.async_api", reason="没装 Playwright（uv sync --extra browser）")
    from playwright.async_api import async_playwright

    async def read() -> str:
        pw = await async_playwright().start()
        try:
            try:
                browser = await pw.chromium.launch(channel="chrome", headless=True, args=list(jscheck.CHROME_ARGS),
                                                   env=jscheck.chrome_env())
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"这台机器打不开 Chrome：{str(exc)[:200]}")
            page = await browser.new_page()
            await page.goto("chrome://version")
            text = await page.inner_text("#command_line")
            await browser.close()
            return text
        finally:
            await pw.stop()

    line = asyncio.run(read())
    last = re.findall(r"--disable-features=(\S+)", line)[-1].split(",")
    assert {"AimEnabled", "OptimizationHints", "NetworkTimeServiceQuerying", "BlockOriginHeaderModificationOnRedirect",
            "AvoidCorsURLLoaderRestartOnRedirect", "HttpsFirstModeV2ForTypicallySecureUsers"} <= set(last), last


def test_chrome_never_sends_webrtc_on_its_own():
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in jscheck.CHROME_ARGS
    assert not any(a.startswith("--force-webrtc-ip-handling-policy") for a in jscheck.CHROME_ARGS)  # no effect
    assert "RTCPeerConnection" in jscheck.NO_WEBRTC


class _Local(http.server.BaseHTTPRequestHandler):
    body: ClassVar[bytes] = b""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


@pytest.mark.browser
def test_this_machine_s_chrome_sends_no_stun_or_turn_by_itself(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api", reason="没装 Playwright（uv sync --extra browser）")
    from tests.archive.test_jscheck import PAGES_HOST, _pages_host_on_loopback, add_page, make_root, opened

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)
    port = udp.getsockname()[1]
    _Local.body = (
        "<html><body><p>測試頁</p><script>"
        "function go(w) { try { var pc = new w.RTCPeerConnection({iceServers: ["
        f"{{urls: 'stun:127.0.0.1:{port}'}}, {{urls: 'turn:127.0.0.1:{port}', username: 'u', credential: 'p'}}]}});"
        "pc.createDataChannel('x'); pc.createOffer().then(function (o) { return pc.setLocalDescription(o); });"
        "} catch (e) {} }"
        "go(window); var f = document.createElement('iframe'); document.body.appendChild(f); go(f.contentWindow);"
        "</script></body></html>").encode()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _pages_host_on_loopback(monkeypatch)  # the page's own host: a name that resolves to the loopback server
    url = f"http://{PAGES_HOST}:{server.server_address[1]}/webrtc.html"
    got = []
    try:
        root = make_root(tmp_path)
        with opened(root) as f:
            add_page(f, url)  # a page whose type is to be judged: jscheck opens it in Chrome
        check = jscheck.JsCheck(root, gap=(1.0, 1.0), plan=OpenPlan(), resolver=mod.resolve, netcheck=lambda: True,
                                browser=jscheck.PlaywrightBrowser(settle_ms=2500))
        report = check.run()
        with opened(root) as f:
            opened_by_chrome = f.q("SELECT count(*) FROM js_page")[0][0]
        assert report.reason == "finished" and opened_by_chrome == 1  # Chrome showed the page, its script ran
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                got.append(udp.recvfrom(2048))
            except TimeoutError:
                pass
    except jscheck.BrowserMissing as exc:
        pytest.skip(f"这台机器打不开 Chrome：{exc}")
    finally:
        server.shutdown()
        server.server_close()
        udp.close()
    assert got == [], f"{len(got)} UDP packets reached 127.0.0.1:{port} straight from Chrome"
