"""The two reviews of phase 4 (10-26), each note that held first written here as a test that failed: a round-1 row no
page uses (a Common Crawl attachment the school refuses) and a jump menu fired on load the browser never opened are
counted, verify item 7 waits for this round's jscheck, `jscheck --set` makes a reconciliation stale, the coverage is
written as the estimate it is (R5 and verify's G item, which a note of these reviews was about too, were taken out
10-26: test_decisions_1026), a gate that compared nothing says so, the top bar is green only when every site is 爬完,
a stale reconciliation says it is, R6 never passes a page the browser did not load whole, the crawler reconciles a finished
site on a thread of its own with its own connection and stops it with the run, and Chrome gets no secret and keeps
Playwright's own switches.

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
from nknu.archive import jscheck, monitor, pagetypes, profiles, progress, reconcile, seeds, viewer
from nknu.archive.frontier import Frontier
from nknu.archive.jscheck import Ask
from nknu.archive.reconcile import Sources
from tests.archive.plans import plan, site
from tests.archive.test_jscheck import FakeBrowser, FakePage
from tests.archive.test_round1_after_crawl import (
    BODY,
    CONTACT,
    EMPTY,
    HOME,
    NEWS,
    PLAN,
    SITE,
    TESTED,
    Clock,
    School,
    World,
    _all_steps,
    _by_no,
    _resolver,
    make_world,
    page,
    pages,
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
    """The made-up school, refusing (403) some addresses, and failing to connect to some hosts."""

    def __init__(self, html: dict[str, str], refused=(), unreachable=()):
        super().__init__(html)
        self.refused, self.unreachable = set(refused), set(unreachable)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host in self.unreachable:
            self.asked.append((request.method, str(request.url)))
            raise httpx.ConnectError("made-up: the host does not connect", request=request)
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
    got = w.not_done(monitor.ERROR, reconciled=True)
    assert any("连接失败" in x for x in got.sites[SITE].why), got.sites[SITE].why
    c17 = _by_no(got.checks)["17"]
    assert c17.status == monitor.FAIL and any(OLD_PDF in x and "Common Crawl" in x for x in c17.lines), c17.lines
    assert any(i.url == OLD_PDF for i in got.sites[SITE].issues)


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


# ---------------------------------------------------------------- 2. jscheck never ran, a jump menu fired on load (S12)


MENU_PAGE = page("消息", f"<p>最新消息列表。{BODY}</p><p>類別：<select class='category option'><option value='4'>106學年度</option>"
                       "<option value='5'>109學年度</option></select></p>"
                       "<script>$(function(){ var html = 'news'; $('.option').change(function(){ location.href = "
                       "html + '.html?getId=' + $(this).val(); }); var getId = ''; if (getId == '') "
                       "{ $('.category').change(); } })</script>")


def _ignore_menus(site_: str, key: str):
    """The platform file's decision for this test's site: its menus need nothing (as if a person decided so)."""
    return "ignore" if key.startswith("select:") else profiles.decision(site_, key)


def _all_types_tested(tmp_path: Path) -> None:
    """The 10-25 test knew every type of the made-up site, contact.html's too: nothing is todo."""
    d = tmp_path / "jstest"
    lines = [{"type": pagetypes.type_key(u), "url": u, "direct_status": 200, "direct_chars": 120,
              "rendered_chars": 120, "method": "direct"} for u in (*TESTED, CONTACT)]
    (d / pagetypes.RESULTS).write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    (d / pagetypes.TYPES).write_text(json.dumps({x["type"]: [x["url"]] for x in lines}), encoding="utf-8")


def _menu_world(tmp_path: Path) -> World:
    w = make_world(tmp_path)
    _all_types_tested(tmp_path)
    w.school.html[NEWS] = MENU_PAGE
    w.shown[NEWS] = MENU_PAGE
    crawler = w.crawler

    def with_decisions():
        c = crawler()
        c.decide = _ignore_menus
        return c

    w.crawler = with_decisions
    return w


def test_without_this_rounds_jscheck_item_7_waits_whatever_is_left_to_judge(tmp_path):
    w = make_world(tmp_path)
    _all_types_tested(tmp_path)
    _all_steps(w, jscheck_too=False)
    got = w.verify()
    c7 = _by_no(got.checks)["7"]
    assert c7.status == monitor.WARN and any("jscheck_end" in x for x in c7.lines), (c7.status, c7.lines)
    assert got.text.splitlines()[4].startswith("- 结论：还没完成")
    assert w.jscheck().reason == "finished"
    assert _by_no(w.verify().checks)["7"].status == monitor.PASS


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
    w.reconcile()
    w.not_done(monitor.LOOK, reconciled=True)
    assert w.jscheck().reason == "finished"  # it opens the page: the menu sends the browser nowhere here
    assert result_of(w, NEWS) == "ok"
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.sites[SITE].why
    assert _by_no(got.checks)["7"].status == monitor.PASS


# ---------------------------------------------------------------- 3. jscheck --set and a stale reconciliation (S11)


def test_a_persons_verdict_after_the_reconciliation_makes_it_stale(tmp_path):
    w = make_world(tmp_path, empty=True)
    w.seed_and_run()
    key = pagetypes.type_key(EMPTY)
    f = Frontier(w.root / "frontier.db")
    try:
        f.x("UPDATE page_type SET verdict='js', source='import' WHERE key=?", (key,))  # as 10-25's import may say
    finally:
        f.close()
    assert w.jscheck().reason == "finished"
    w.reconcile()
    w.not_done(monitor.LOOK, reconciled=True)  # a page that needs JavaScript is round two's
    sites = jscheck.set_verdict(w.root, key, "ok", "人看过")
    assert sites == [SITE]
    got = w.not_done(monitor.UNRECONCILED)  # never judged empty while its type was js: reconcile again
    assert any("jscheck --set" in x for x in got.sites[SITE].why), got.sites[SITE].why
    w.reconcile()
    assert result_of(w, EMPTY) == "thin"
    w.not_done(monitor.ERROR, reconciled=True)


def test_the_set_command_says_which_sites_to_reconcile_again(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from nknu.admin.cli import app
    from nknu.archive import round1

    monkeypatch.setattr(round1, "load", lambda *a, **k: PLAN)
    w = make_world(tmp_path)
    _all_steps(w)
    key = pagetypes.type_key(CONTACT)
    got = CliRunner().invoke(app, ["archive", "jscheck", "--dir", str(w.root), "--set", key, "ok"])
    assert got.exit_code == 0, got.output
    assert "要重新对账" in got.output and SITE in got.output
    f = ro(w)
    try:
        ev = f.q("SELECT message FROM event WHERE code='verdict_set'")
        assert [json.loads(r[0])["sites"] for r in ev] == [[SITE]]
    finally:
        f.close()


# ---------------------------------------------------------------- 5. the coverage, as the estimate it is (S0)


def test_the_coverage_says_what_it_rests_on_and_never_that_nothing_is_missed(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w)
    f = ro(w)
    try:
        r3b = json.loads(f.q("SELECT checks FROM reconcile_site WHERE site=?", (SITE,))[0][0])["R3b"]
    finally:
        f.close()
    assert r3b["known_ok"] == r3b["linked"] == 2
    assert "至少还漏约 0" not in r3b["note"] and "估不出还漏多少" in r3b["note"], r3b
    assert r3b["missed_at_least"] is None
    dash, html = w.dashboard()
    assert dash["state"] == "done"
    assert "覆盖率估计 100%" in html and "偏高" in html and "至少还漏约 0" not in html
    got = w.verify()
    c18 = _by_no(got.checks)["18"]
    line = next(x for x in c18.lines if SITE in x and "Common Crawl" in x)
    assert "2 个里，2 个" in line and "偏高" in line and "估不出还漏多少" in line, c18.lines


def test_a_lower_bound_is_written_as_one():
    sr = reconcile._SiteRows(ok={1, 2, 3, 4, 5, 6}, linked={1, 2, 3, 4})
    fr = Frontier.__new__(Frontier)
    rows = {f"https://{SITE}/{i}": i for i in (1, 2, 5)}
    sr.land = {i: (i, "ok", True) for i in range(1, 7)}
    orig = reconcile._row_of
    try:
        reconcile._row_of = lambda _fr, u: {"id": rows[u]} if u in rows else None
        check, cover = reconcile._coverage(fr, sr, list(rows), "Common Crawl ")
    finally:
        reconcile._row_of = orig
    # M = 3 remembered, m = 2 linked, n = 4 linked this round: N̂ = 6, this round has 6 ok: none beyond what was got
    assert round(cover, 2) == 0.67 and check.counts["estimate"] == 6
    assert check.counts["missed_at_least"] is None and "估不出还漏多少" in check.note
    rows[f"https://{SITE}/9"] = 6
    sr.ok, sr.land[9] = {1, 2, 3, 4, 6}, (9, "ok", True)
    try:
        reconcile._row_of = lambda _fr, u: {"id": rows[u]} if u in rows else None
        check, _c = reconcile._coverage(fr, sr, [f"https://{SITE}/{i}" for i in (1, 5, 9)], "Common Crawl ")
    finally:
        reconcile._row_of = orig
    # M = 3, m = 1, n = 4: N̂ = 12, this round has 5 ok: at least about 7 missed
    assert check.counts["missed_at_least"] == 7 and "至少还漏约 7 个（下限）" in check.note


# ---------------------------------------------------------------- 6. a gate that compared nothing


def test_a_gate_that_compared_nothing_says_so():
    r1, _found = reconcile._r1(None, {})
    assert r1.status == reconcile.PASS and r1.counts["compared"] is False and "没比到" in r1.note
    r3, _found = reconcile._imported(None, None, [], "R3a", "Common Crawl ")
    assert r3.counts["compared"] is False and "没得比" in r3.note


def test_the_acceptance_and_the_dashboard_say_which_gates_compared_nothing(tmp_path):
    w = make_world(tmp_path)
    w.seed_and_run()
    (tmp_path / "cc" / "CC-TEST.cdx").write_text(  # by the reconciliation, Common Crawl knows only another site
        f"tw,edu,nknu,v)/ 20260101000000 {json.dumps({'url': 'https://v.nknu.edu.tw/', 'status': '200'})}\n",
        encoding="utf-8")
    assert w.jscheck().reason == "finished"
    w.reconcile()
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE)  # whether it may: the main session's to decide (listed)
    st = got.sites[SITE]
    assert any("没比到" in x and "R3a" in x for x in st.warnings), st.warnings
    _d, html = w.dashboard()
    assert "没比到" in html
    report = reconcile.report_text(w.reconcile())
    assert "R3a" in report and "没比到" in report


# ---------------------------------------------------------------- 7. the top bar and a stale reconciliation (S1)


def test_the_top_bar_is_green_only_when_every_site_is_done(tmp_path):
    w = make_world(tmp_path)
    cc_also(tmp_path, OLD_PDF)
    w.school = Refusing(w.school.html, refused=(OLD_PDF,))
    _all_steps(w)
    _dash, html = w.dashboard()
    assert "c-green'>✓ 上次" not in html and "不等于爬完" in html
    (tmp_path / "b").mkdir()
    w2 = make_world(tmp_path / "b")
    _all_steps(w2)
    _dash, html = w2.dashboard()
    assert "c-green'>✓ 上次" in html


def test_the_site_page_says_a_reconciliation_is_stale(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w)
    reconcile.rejudge(w.root, [SITE], plan=PLAN)
    d = progress.Dashboard(w.root, background=False, cache_s=0)
    try:
        d.refresh_slow()
        html = viewer.progress_site_html(d.site(SITE))
    finally:
        d.close()
    assert "已过期" in html


# ---------------------------------------------------------------- 8. R6 and a page the browser did not load whole


def test_r6_never_passes_a_page_whose_data_did_not_come(tmp_path):
    data = "https://data.nknu.edu.tw/api/home.json"
    w = make_world(tmp_path)
    w.school = Refusing(w.school.html, unreachable=("data.nknu.edu.tw",))
    shown = dict(w.shown)

    def browser() -> FakeBrowser:
        return FakeBrowser({u: FakePage(h, [Ask(data, kind="xhr")] if u == HOME else []) for u, h in shown.items()})

    w.browser = browser
    _all_steps(w)
    f = ro(w)
    try:
        r6 = json.loads(f.q("SELECT checks FROM reconcile_site WHERE site=?", (SITE,))[0][0])["R6"]
        assert f.q("SELECT count(*) FROM request WHERE purpose='spotcheck' AND url=?", (data,))[0][0] >= 1
    finally:
        f.close()
    assert r6["status"] != "pass", r6
    assert any(i.get("problem") == "incomplete" and i["url"] == HOME for i in r6["items"]), r6
    got = w.not_done(monitor.UNRECONCILED)
    assert any("R6" in x for x in got.sites[SITE].why)


def test_the_spot_checker_says_what_kept_a_page_from_loading_whole():
    log = [jscheck.Exchange(HOME, "document", "GET", True, status=200),
           jscheck.Exchange("https://x.nknu.edu.tw/a.js", "script", "GET", False, error="connect"),
           jscheck.Exchange("https://x.nknu.edu.tw/p.png", "image", "GET", False, blocked="type")]
    obs = jscheck.Observation(HOME, HOME, "<html></html>", log=log)
    assert "a.js" in jscheck.incomplete(obs)
    assert jscheck.incomplete(jscheck.Observation(HOME, HOME, "", log=log[:1], not_idle=True)) == "not_idle"
    assert jscheck.incomplete(jscheck.Observation(HOME, HOME, "", log=log[::2])) is None  # an image is not needed


# ---------------------------------------------------------------- 9. the crawler's own reconciliation, on its thread


def _two_sites(tmp_path: Path):
    """Two sites: SITE finishes at once, the other one is slow in real time, so SITE is reconciled while it is still
    being crawled."""
    slow_site = "v.nknu.edu.tw"
    two = plan(site(SITE, imp="全校重要", name="测试单位", home=HOME),
               site(slow_site, imp="本系重要", name="慢的单位", home=f"https://{slow_site}/"))
    fast = School(pages())
    slow_pages = {f"https://{slow_site}/": page("首頁", "".join(f"<p><a href='/n{i}.html'>第{i}則</a></p>"
                                                                for i in range(30)) + f"<p>{BODY}</p>")}
    slow_pages |= {f"https://{slow_site}/n{i}.html": page(f"第{i}則", f"<p>{BODY}</p>") for i in range(30)}
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == slow_site:
            asked.append((time.monotonic(), str(request.url)))
            time.sleep(0.05)
            body = slow_pages.get(str(request.url))
            return httpx.Response(200 if body else 404, headers=School.HTML,
                                  content=(body or "<html><body>not found</body></html>").encode())
        return fast(request)

    clock = Clock()

    def client(warc):
        return mod.archive_client(warc, transport=httpx.MockTransport(answer), clock=clock, sleep=clock.sleep)

    root = tmp_path / "archive"
    c = mod.Crawler(root, plan=two, clock=clock, sleep=clock.sleep, resolver=_resolver, client_factory=client,
                    netcheck=lambda: True, disk_free=lambda p: 10**12, campus_url=None,
                    reconcile_sources=Sources(cc_dir=None, old=None))
    seeds.seed_round1(c, old=None, facts_db=None, cc_dir=None, probe=None, jstest=None)
    return c, asked


def test_the_crawler_reconciles_on_a_thread_of_its_own_and_a_stop_ends_it(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "SITE_LOOK_S", 0.0)
    began, seen = threading.Event(), {}

    def slow_reconcile(fr, root, site_, *, stop=None, **kw):
        seen.update(fr=fr, thread=threading.current_thread(), stop=stop)
        began.set()
        while stop is not None and not stop():  # a long reconciliation that minds the run's stop
            time.sleep(0.01)
        raise reconcile.Stopped("the run is stopping")

    monkeypatch.setattr(reconcile, "site_done", slow_reconcile)
    c, asked = _two_sites(tmp_path)
    t = threading.Thread(target=c.run, kwargs={"poll_s": 0.01}, daemon=True)
    t.start()
    try:
        assert began.wait(30), "the finished site was never reconciled"
        n0 = len(asked)
        time.sleep(0.6)
        assert len(asked) > n0  # the other site is crawled on meanwhile
        assert seen["fr"] is not c.frontier and seen["fr"].lock is not c.frontier.lock  # its own connection
        assert seen["thread"] is not threading.main_thread() and seen["thread"].name.startswith("reconcile")
        t0 = time.monotonic()
        c.halt("stop")
        t.join(20)
        assert not t.is_alive() and time.monotonic() - t0 < 10
    finally:
        c.halt("stop")
        t.join(20)
        c.frontier.close()
    assert c.end_reason == "stop"
    f = Frontier(tmp_path / "archive" / "frontier.db", readonly=True)
    try:
        codes = [r[0] for r in f.q("SELECT code FROM event WHERE code IN ('reconcile_failed', 'reconcile_stopped', "
                                   "'run_end') ORDER BY rowid")]
    finally:
        f.close()
    assert codes[-1] == "run_end" and "reconcile_failed" not in codes


def test_a_reconciliation_stops_between_pages_when_asked(tmp_path):
    w = make_world(tmp_path)
    w.seed_and_run()
    f = Frontier(w.root / "frontier.db")
    try:
        calls = []

        def stop() -> bool:
            calls.append(1)
            return len(calls) > 1  # the first page is read, then the run stops

        with pytest.raises(reconcile.Stopped):
            reconcile.site_done(f, w.root, SITE, plan=PLAN, batch="t", sources=w.sources, stop=stop)
        assert [r[0] for r in f.q("SELECT batch FROM reconcile_site")] != ["t"]  # nothing half-done written as done
        assert not f.q("SELECT 1 FROM event WHERE code='reconcile' AND message LIKE '%\"t\"%'")
    finally:
        f.close()


def test_what_a_site_lost_is_asked_of_its_own_pages_only(tmp_path):
    w = make_world(tmp_path)
    w.seed_and_run()
    f = Frontier(w.root / "frontier.db")
    asked = []
    q = f.q

    def spy(sql, args=()):
        asked.append((sql, args))
        return q(sql, args)

    try:
        b = reconcile._Batch(f, w.root, PLAN, w.sources, "t", None, False, None)
        f.q = spy
        b.lost(SITE)
    finally:
        f.close()
    (sql, args), = [(s, a) for s, a in asked if "page_resource" in s]
    assert "owner_site" in sql and args == (SITE,)


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


def test_the_commands_that_open_chrome_hold_no_secret_from_their_start(monkeypatch, tmp_path):
    import os

    from typer.testing import CliRunner

    from nknu.admin.cli import app

    for args in (["jscheck"], ["reconcile", "--batch", "x", "--no-r6"]):
        monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-not-a-secret-0000")
        CliRunner().invoke(app, ["archive", *args, "--dir", str(tmp_path / "none")])
        assert "GOOGLE_API_KEY" not in os.environ, args


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
def test_this_machine_s_chrome_sends_no_stun_or_turn_by_itself(tmp_path):
    pytest.importorskip("playwright.async_api", reason="没装 Playwright（uv sync --extra browser）")
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
    url = f"http://127.0.0.1:{server.server_address[1]}/webrtc.html"
    got = []
    try:
        from tests.archive.test_jscheck import make_root, opened, resolve

        root = make_root(tmp_path)
        with opened(root) as f, jscheck.SpotChecker(root, f, gap=(1.0, 1.0), resolver=resolve, shots=False,
                                                     browser=jscheck.PlaywrightBrowser(settle_ms=2500)) as spot:
            view = spot(url)
        if view.error and "打不开" in view.error:
            pytest.skip(f"这台机器打不开 Chrome：{view.error}")
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
