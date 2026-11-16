"""Round 1 from its seeds to its acceptance, end to end (design §5, §8.4, §10.5, §13, §14, §18.2a step 3, §18.2b): a
made-up school site is seeded (seed_round1: the list, a made-up 10-24 archive, a Common Crawl index, a v1 notice and
the 10-25 page types), crawled on a fake network (Crawler.run, which reconciles the site offline in its own process
as it finishes), its page types checked in a made-up browser (jscheck), reconciled with R6 through jscheck's spot
checker and the same browser (reconcile), and accepted (verify). After every step where something is still missing,
neither the dashboard (progress.Dashboard, drawn by viewer) nor verify says the site is 「✓ 爬完」; only once everything
is there do both say so. Then each way something can be missing, one at a time, on its own copy of the site.

No address leaves this machine: the school is httpx.MockTransport's, behind the crawl's own client (and
tests/conftest.py's guard). Every text is made up; no person's name."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import crawler as mod
from nknu.archive import jscheck, monitor, pagetypes, progress, reconcile, round1, scope, seeds, viewer
from nknu.archive.frontier import Frontier
from nknu.archive.reconcile import Sources
from nknu.crawl import schoollock
from tests.archive.plans import plan, site
from tests.archive.test_jscheck import FakeBrowser, FakePage
from tests.archive.test_round1_e2e import FastClock
from tests.archive.test_seeds_round1 import OLD_SCHEMA

SITE = "w.nknu.edu.tw"
W = f"https://{SITE}"
HOME, ABOUT, NEWS, CONTACT, EMPTY = f"{W}/", f"{W}/about.html", f"{W}/news.html", f"{W}/contact.html", f"{W}/empty.html"
PDF_URL = f"{W}/files/a.pdf"
PLAN = plan(site(SITE, imp="全校重要", name="测试单位", home=HOME))
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
BODY = "這是一段測試用的內文，介紹本單位的業務與服務項目，內容足夠長，不會被當成空白的頁面。" * 2
QUOTE = "關於我們的內文"
TESTED = (HOME, ABOUT, NEWS)  # the 10-25 test knew these types: contact.html's is new this round (todo until jscheck)


def page(title: str, main: str) -> str:
    return (f"<html><head><meta charset='utf-8'><title>{title}</title></head><body>"
            '<nav><a href="/">首頁</a><a href="/about.html">關於我們</a><a href="/news.html">最新消息</a></nav>'
            f"<main>{main}</main><footer><a href='/contact.html'>聯絡我們</a></footer></body></html>")


def pages(*, empty: bool = False) -> dict[str, str]:
    home = f"<p>{BODY}</p><p><a href='/files/a.pdf'>招生簡章.pdf</a></p>"
    if empty:
        home += "<p><a href='/empty.html'>活動說明</a></p>"
    out = {HOME: page("首頁", home), ABOUT: page("關於", f"<p>{QUOTE}：{BODY}</p>"),
           NEWS: page("消息", f"<p>最新消息列表。{BODY}</p>"), CONTACT: page("聯絡", f"<p>聯絡方式。{BODY}</p>")}
    if empty:
        out[EMPTY] = page("說明", "<p>敬請期待</p>")  # almost nothing of its own: thin once reconciled
    return out


class School:
    """What the fake network answers (robots.txt and the probe 404; a missing path 404 too, an erring one 500), and
    what it was asked."""

    HTML: ClassVar[dict[str, str]] = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, html: dict[str, str], missing: tuple[str, ...] = (), erring: tuple[str, ...] = ()):
        self.html, self.missing, self.erring = html, set(missing), set(erring)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.asked.append((request.method, url))
        if request.url.host != SITE:
            return httpx.Response(404, headers=self.HTML, content=b"<html><body>not here</body></html>")
        if url in self.erring:
            return httpx.Response(500, headers=self.HTML, content=b"<html><body>server error</body></html>")
        if url in self.missing or request.url.path.endswith("/robots.txt") or "nknu-archive-probe-" in url:
            return httpx.Response(404, headers=self.HTML, content=b"<html><body>not found</body></html>")
        if url == PDF_URL:
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF)
        if url in self.html:
            return httpx.Response(200, headers=self.HTML, content=self.html[url].encode("utf-8"))
        return httpx.Response(404, headers=self.HTML, content=b"<html><body>not found</body></html>")


class Clock(FastClock):
    """One clock for every step (crawl, jscheck, R6): the politeness audit reads their requests together."""

    async def asleep(self, s: float) -> None:
        self.sleep(s)
        await asyncio.sleep(0)


def _resolver(host: str):
    return f"ip:{host}", None, host


# ---------------------------------------------------------------- the sources the reconciliation compares with


def _old(root: Path) -> Path:
    """A made-up 10-24 archive (read only: no round mark), which fetched the home and 關於 well."""
    d = root / "archive-2026-10-24"
    d.mkdir()
    conn = sqlite3.connect(d / "frontier.db")
    conn.executescript(OLD_SCHEMA)
    for i, url in enumerate((HOME, ABOUT), 1):
        conn.execute("INSERT INTO url(id, url, path, host, site, tier, kind, depth, found_via, state, status) "
                     "VALUES (?,?,?,?,?,0,'page',?,'seed','done',200)", (i, url, url, SITE, SITE, i - 1))
    conn.commit()
    conn.close()
    return d


def _cc(root: Path) -> Path:
    d = root / "cc"
    d.mkdir()
    (d / "CC-TEST.cdx").write_text("".join(f"tw,edu,nknu,w)/ 20260101000000 {json.dumps({'url': u, 'status': '200'})}\n"
                                           for u in (HOME, ABOUT)), encoding="utf-8")
    return d


def _facts(root: Path) -> Path:
    """v1's facts.db as seeds.v1_known reads it: one notice v1 knew (verify item 12)."""
    path = root / "facts.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE doc(url TEXT, unit TEXT, title TEXT, category TEXT, withdrawn_at TEXT)")
    conn.execute("INSERT INTO doc VALUES (?, '测试单位', '最新消息', 'news', NULL)", (NEWS,))
    conn.commit()
    conn.close()
    return path


def _jstest(root: Path) -> Path:
    """The five files of the 10-25 test, for the made-up site: each type it knew tested ok."""
    d = root / "jstest"
    d.mkdir()
    lines = [{"type": pagetypes.type_key(u), "url": u, "direct_status": 200, "direct_chars": 120,
              "rendered_chars": 120, "method": "direct"} for u in TESTED]
    (d / pagetypes.RESULTS).write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    (d / pagetypes.RETEST).write_text("", encoding="utf-8")
    (d / pagetypes.TYPES).write_text(json.dumps({x["type"]: [x["url"]] for x in lines}), encoding="utf-8")
    (d / pagetypes.REVIEW).write_text(json.dumps({"kinds": []}), encoding="utf-8")
    (d / pagetypes.CLICK_SCAN).write_text(json.dumps({"kinds": []}), encoding="utf-8")
    return d


# ---------------------------------------------------------------- one site, step by step


@dataclass
class World:
    root: Path
    clock: Clock
    school: School
    sources: Sources
    shown: dict[str, str] = field(default_factory=dict)  # what the browser shows of each page (the page itself)

    def client(self, warc):
        return mod.archive_client(warc, transport=httpx.MockTransport(self.school), clock=self.clock,
                                  sleep=self.clock.sleep)

    def browser(self) -> FakeBrowser:
        return FakeBrowser({u: FakePage(h) for u, h in self.shown.items()})

    def crawler(self) -> mod.Crawler:
        return mod.Crawler(self.root, plan=PLAN, clock=self.clock, sleep=self.clock.sleep, resolver=_resolver,
                           client_factory=self.client, netcheck=lambda: True, disk_free=lambda p: 10**12,
                           campus_url=None, reconcile_sources=self.sources)

    def seed_and_run(self) -> mod.Crawler:
        c = self.crawler()
        seeds.seed_round1(c, old=self.sources.old, facts_db=self.root.parent / "facts.db", cc_dir=self.sources.cc_dir,
                          probe=None, jstest=self.root.parent / "jstest")
        self.run(c)
        return c

    def run(self, c: mod.Crawler | None = None) -> mod.Crawler:
        c = c or self.crawler()
        try:
            c.run(poll_s=0.01)
        finally:
            c.frontier.close()
        return c

    def jscheck(self) -> jscheck.JsCheckReport:
        check = jscheck.JsCheck(self.root, old=self.sources.old, browser=self.browser(), client_factory=self.client,
                                resolver=_resolver, netcheck=lambda: True, plan=PLAN, clock=self.clock,
                                sleep=self.clock.sleep, asleep=self.clock.asleep, rng=random.Random(7))
        return check.run()

    def reconcile(self, *, r6: bool = True, sources: Sources | None = None) -> reconcile.Reconciled:
        def spot(fr):
            return jscheck.SpotChecker(self.root, fr, browser=self.browser(), client_factory=self.client,
                                       resolver=_resolver, clock=self.clock, sleep=self.clock.sleep,
                                       rng=random.Random(3))

        src = sources or self.sources
        if not r6:
            return reconcile.reconcile(self.root, batch="e2e", plan=PLAN, sources=src)[0]
        with schoollock.hold():
            return reconcile.reconcile(self.root, batch="e2e", plan=PLAN, sources=src, spot_factory=spot)[0]

    def verify(self) -> monitor.Verified:
        return monitor.verify(self.root, plan=PLAN, resource_check=monitor.extract_check(PLAN),
                              write=False)

    def dashboard(self) -> tuple[dict, str]:
        """The site's page of the dashboard (its state and what it lacks: the cards say only progress and 错 N since
        the 11-04 dashboard)."""
        d = progress.Dashboard(self.root, background=False, cache_s=0)
        try:
            d.refresh_slow()
            site = d.site(SITE)
        finally:
            d.close()
        assert site is not None and "problem" not in site, (site or {}).get("problem")
        return site, viewer.progress_site_html(site)

    def state(self) -> tuple[str, str, monitor.Verified]:
        """The site's state as the dashboard shows it and as verify gives it, with verify's whole answer."""
        dash, html = self.dashboard()
        got = self.verify()
        assert ("✓ 爬完" in html) == (dash["state"] == "done")  # the mark drawn is the state worked out
        return dash["state"], got.sites[SITE].state, got

    def not_done(self, *expected: str, reconciled: bool = False) -> monitor.Verified:
        """Neither the dashboard nor verify says 爬完, and verify's conclusion is 还没完成; both give one of these
        states. reconciled: whether verify's item 18 (a fresh reconciliation, every gate passed) holds; it says
        nothing of the pages (the other items do), so a site may pass it and still lack a file."""
        dash, ver, got = self.state()
        assert dash != "done" and ver != monitor.DONE, (dash, ver, got.sites[SITE].why)
        assert dash == ver, (dash, ver)  # the dashboard shows monitor.site_status's state, never one of its own
        if expected:
            assert ver in expected, (ver, got.sites[SITE].why)
        assert _by_no(got.checks)["18"].status == (monitor.PASS if reconciled else monitor.FAIL)
        assert got.text.splitlines()[4].startswith("- 结论：还没完成") and got.code != 0
        return got


def make_world(tmp_path: Path, *, missing: tuple[str, ...] = (), erring: tuple[str, ...] = (), empty: bool = False,
               cc: bool = True, shown: dict[str, str] | None = None) -> World:
    _jstest(tmp_path)
    _facts(tmp_path)
    html = pages(empty=empty)
    world = World(tmp_path / "archive", Clock(), School(html, missing, erring),
                  Sources(cc_dir=_cc(tmp_path) if cc else None, old=_old(tmp_path)))
    world.shown = {**html, **(shown or {})}
    return world


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_POLL_S", 0.02)


def _by_no(cs) -> dict[str, monitor.Check]:
    return {c.no: c for c in cs}


# ---------------------------------------------------------------- the whole way, the site never 爬完 before it is


def test_seed_run_jscheck_reconcile_verify_says_done_only_once_everything_is_there(tmp_path):
    w = make_world(tmp_path)
    # seeded only: nothing fetched yet
    c = w.crawler()
    seeds.seed_round1(c, old=w.sources.old, facts_db=tmp_path / "facts.db", cc_dir=w.sources.cc_dir, probe=None,
                      jstest=tmp_path / "jstest")
    c.frontier.close()
    w.not_done()
    # crawled: the crawler reconciled the finished site offline itself, R6 left for later (gates_ok 0)
    w.run()
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        rec = f.q("SELECT batch, gates_ok, checks FROM reconcile_site WHERE site=?", (SITE,))[0]
        assert rec["batch"].startswith("run:") and rec["gates_ok"] == 0
        checks = json.loads(rec["checks"])
        assert checks["R6"]["status"] == "skip"
        assert {k for k, v in checks.items() if v["status"] != "pass"} == {"R6"}  # the offline ones all pass
        events = [r["code"] for r in f.q("SELECT code FROM event WHERE code IN ('reconcile', 'run_end') ORDER BY rowid")]
        assert events == ["reconcile", "run_end"]  # reconciled before the run ended: older than that run_end
        assert f.q("SELECT verdict FROM page_type WHERE key=?", (pagetypes.type_key(CONTACT),))[0][0] == "todo"
    finally:
        f.close()
    # contact.html's type is new and no browser judged it: got since the owner's 11-16 decision (export batch T5),
    # nothing 待判定 any more
    got = w.not_done(monitor.LOOK)
    assert SITE not in got.sites or not any("待判定" in x for x in got.sites[SITE].why)
    # the browser judged the new type: what is left is a reconciliation newer than the run
    report = w.jscheck()
    assert report.reason == "finished" and report.todo_left == 0
    got = w.not_done(monitor.UNRECONCILED)
    assert any("旧" in x for x in got.sites[SITE].why)  # the crawler's own reconciliation is older than its run_end
    # reconciled without the browser: R6 not checked, never 爬完
    res = w.reconcile(r6=False)
    assert res.sites[0].checks["R6"].status == reconcile.SKIP and not res.sites[0].gates_ok
    got = w.not_done(monitor.UNRECONCILED)
    assert any("R6" in x for x in got.sites[SITE].why)
    # reconciled with R6 through jscheck's spot checker: every gate passes, the site is 爬完 on both
    res = w.reconcile()
    assert {k: v.status for k, v in res.sites[0].checks.items() if v.status != reconcile.PASS} == {}
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.sites[SITE].why
    cs = _by_no(got.checks)
    assert {no: c.status for no, c in cs.items() if c.status != monitor.PASS} == {"16": monitor.WARN}, \
        {no: (c.status, c.lines[:3]) for no, c in cs.items() if c.status != monitor.PASS}
    assert "结论：第一轮完成，有已知缺口" in got.text and got.code == 1  # never all green while a known gap is open
    # what was sent: one request at a time per server, nothing out of scope, R6 and jscheck in the request log
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        purposes = {r[0] for r in f.q("SELECT DISTINCT purpose FROM request")}
        assert {"jscheck", "spotcheck"} <= purposes
        assert monitor.politeness(f) == [] and monitor.scope_audit(f, PLAN) == []
    finally:
        f.close()
    # a rejudge, then a run, each make the reconciliation stale: 爬完 no more until it is reconciled again
    reconcile.rejudge(w.root, [SITE], plan=PLAN)
    w.not_done(monitor.UNRECONCILED)
    w.reconcile()
    assert w.state()[:2] == ("done", monitor.DONE)
    w.run()
    w.not_done(monitor.UNRECONCILED)


# ---------------------------------------------------------------- each way something can be missing, on its own


def _all_steps(w: World, *, jscheck_too: bool = True, r6: bool = True, sources: Sources | None = None) -> None:
    w.seed_and_run()
    if jscheck_too:
        assert w.jscheck().reason == "finished"
    w.reconcile(r6=r6, sources=sources)


def test_an_attachment_that_errs_keeps_the_site_from_done(tmp_path):
    w = make_world(tmp_path, erring=(PDF_URL,))
    _all_steps(w)  # the run ends waiting (B1: only a retry is left), the site reconciled as it finished
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert f.q("SELECT result FROM url_result WHERE url=?", (PDF_URL,))[0][0] == "retry"
        end = json.loads(f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")[0][0])
        assert end["reason"] == "waiting"
    finally:
        f.close()
    got = w.not_done(monitor.CRAWLING, reconciled=True)  # every gate passed: the page lacks its file all the same
    assert any("等重试" in x for x in got.sites[SITE].why)


def test_an_attachment_the_school_no_longer_has_is_complete_on_the_schools_side(tmp_path):
    # design §10.2, §10.4: a 404 asked twice is 学校那边已经不存在, the page 完整（学校那边缺）, which §10.5 counts as
    # complete: the site may be 爬完 (verify lists it among what the school lacks)
    w = make_world(tmp_path, missing=(PDF_URL,))
    _all_steps(w)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert f.q("SELECT result FROM url_result WHERE url=?", (PDF_URL,))[0][0] == "gone"
        assert f.q("SELECT level FROM page_completeness pc JOIN url u ON u.id = pc.id WHERE u.url=?",
                   (HOME,))[0][0] == "complete_school"
    finally:
        f.close()
    assert w.state()[:2] == ("done", monitor.DONE)


def test_a_page_with_almost_nothing_on_it_is_thin_once_reconciled_and_red(tmp_path):
    w = make_world(tmp_path, empty=True)
    _all_steps(w)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert f.q("SELECT result FROM url_result WHERE url=?", (EMPTY,))[0][0] == "thin"
    finally:
        f.close()
    got = w.not_done(monitor.ERROR, reconciled=True)
    assert _by_no(got.checks)["6"].status == monitor.FAIL


def test_what_only_the_browser_shows_fails_r6(tmp_path):
    more = pages()[ABOUT].replace("</main>", "<p><a href='/files/only-in-browser.pdf'>另一份附件.pdf</a></p></main>")
    w = make_world(tmp_path, shown={ABOUT: more})
    _all_steps(w)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        checks = json.loads(f.q("SELECT checks FROM reconcile_site WHERE site=?", (SITE,))[0][0])
    finally:
        f.close()
    assert checks["R6"]["status"] == "fail"
    got = w.not_done(monitor.ERROR)
    assert any("R6" in x for x in got.sites[SITE].why)


def test_without_jscheck_a_new_type_waits_yellow_whatever_the_reconciliation_says(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w, jscheck_too=False)
    got = w.not_done(monitor.LOOK, reconciled=True)
    assert _by_no(got.checks)["7"].status == monitor.WARN  # jscheck has not run: yellow, not yet red


def test_without_r6_the_site_is_recorded_never_done(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w, r6=False)
    w.not_done(monitor.UNRECONCILED)


def test_without_the_common_crawl_index_r3_is_not_checked_and_the_site_never_done(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w, sources=Sources(cc_dir=None, old=w.sources.old))
    got = w.not_done(monitor.UNRECONCILED)
    assert any("R3a" in x for x in got.sites[SITE].why)


def test_a_type_the_browser_cannot_judge_is_red_once_jscheck_ended(tmp_path):
    w = make_world(tmp_path)
    w.shown = {u: h for u, h in w.shown.items() if u != CONTACT}  # the browser shows nothing of it: no conclusion
    _all_steps(w)
    got = w.not_done(monitor.ERROR)
    assert _by_no(got.checks)["7"].status == monitor.FAIL


# ---------------------------------------------------------------- the commands


@pytest.fixture
def done_world(tmp_path, monkeypatch):
    monkeypatch.setattr(round1, "load", lambda *a, **k: PLAN)
    w = make_world(tmp_path)
    _all_steps(w)
    assert w.state()[:2] == ("done", monitor.DONE)
    return w


def test_verify_command_writes_its_report_and_exits_by_its_worst_item(done_world):
    w = done_world
    got = CliRunner().invoke(app, ["archive", "verify", "--dir", str(w.root)])
    assert got.exit_code == 1, got.output  # item 16 stays open until the owner agrees to use Wayback
    assert "第一轮完成，有已知缺口" in got.output and "✓ 爬完" in got.output
    assert list((w.root / "reports").glob("verify-*.md"))
    missing = CliRunner().invoke(app, ["archive", "verify", "--dir", str(w.root / "no-archive")])
    assert missing.exit_code == 1 and "找不到" in missing.output


def test_reconcile_rejudge_and_review_commands(done_world):
    w = done_world
    runner = CliRunner()
    got = runner.invoke(app, ["archive", "rejudge", "--dir", str(w.root), "--sites", SITE])
    assert got.exit_code == 0, got.output
    assert "过期" in got.output
    got = runner.invoke(app, ["archive", "reconcile", "--dir", str(w.root), "--batch", "cli", "--no-r6"])
    assert got.exit_code == 1, got.output  # R6 not checked: not every gate passed
    assert f"{SITE}：没过" in got.output and "R6" in got.output
    got = runner.invoke(app, ["archive", "reconcile", "--dir", str(w.root), "--no-r6"])
    assert got.exit_code == 1 and "--batch" in got.output
    listed = w.root.parent / "to-fetch.txt"
    listed.write_text(f"{W}/later.html\t人看过，要抓\n# 不抓的写理由\n", encoding="utf-8")
    got = runner.invoke(app, ["archive", "reconcile", "--dir", str(w.root), "--enqueue", str(listed)])
    assert got.exit_code == 0 and "写进待抓清单 1 个" in got.output, got.output
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert f.row(f"{W}/later.html")["found_via"] == "reconcile"
    finally:
        f.close()
    got = runner.invoke(app, ["archive", "review", "--dir", str(w.root)])
    assert got.exit_code == 0 and "可以标记的网址 0 个" in got.output
    got = runner.invoke(app, ["archive", "review", "--dir", str(w.root), "--mark", "school_side", "1"])
    assert got.exit_code == 1 and "没有标记" in got.output  # a mark says why, and only a markable result


def test_jscheck_set_and_refusals(done_world):
    w = done_world
    runner = CliRunner()
    key = pagetypes.type_key(CONTACT)
    got = runner.invoke(app, ["archive", "jscheck", "--dir", str(w.root), "--set", key, "js", "--note", "人看过"])
    assert got.exit_code == 0 and "已改判" in got.output, got.output
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        row = f.q("SELECT verdict, source FROM page_type WHERE key=?", (key,))[0]
        assert (row["verdict"], row["source"]) == ("js", "manual")
    finally:
        f.close()
    assert w.state()[0] != "done"  # a page that needs JavaScript is round two's: no longer 爬完
    got = runner.invoke(app, ["archive", "jscheck", "--dir", str(w.root), "--set", key, "maybe"])
    assert got.exit_code == 1
    lock = mod.hold_run_lock(w.root)
    try:
        for args in (["jscheck"], ["reconcile", "--batch", "x", "--no-r6"], ["rejudge", "--sites", SITE],
                     ["review", "--mark", "school_side", "1", "--note", "x"]):
            got = runner.invoke(app, ["archive", *args, "--dir", str(w.root)])
            assert got.exit_code == 1 and "pid" in got.output, (args, got.output)
    finally:
        lock.close()


def test_the_old_archive_is_refused_by_every_command_that_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(round1, "load", lambda *a, **k: PLAN)
    old = _old(tmp_path)
    before = (old / "frontier.db").read_bytes()
    runner = CliRunner()
    for args in (["jscheck"], ["reconcile", "--batch", "x", "--no-r6"], ["rejudge", "--sites", SITE],
                 ["review"], ["jscheck", "--set", "k", "ok"]):
        got = runner.invoke(app, ["archive", *args, "--dir", str(old)])
        assert got.exit_code == 1 and "旧爬虫的存档" in got.output, (args, got.output)
    got = runner.invoke(app, ["archive", "verify", "--dir", str(old)])
    assert got.exit_code == 1, got.output
    assert (old / "frontier.db").read_bytes() == before
    assert scope.site_key(HOME) == SITE


# ---------------------------------------------------------------- the wiring, one piece at a time


def test_the_crawler_reconciles_a_finished_site_once_and_a_failure_never_stops_it(tmp_path, monkeypatch):
    w = make_world(tmp_path)
    calls = []

    def broken(fr, root, site_, *, plan, batch, sources, stop=None):
        calls.append((site_, batch, sources))
        raise RuntimeError("a reconciliation that broke")

    monkeypatch.setattr(reconcile, "site_done", broken)
    c = w.seed_and_run()
    assert c.end_reason == "finished"
    assert calls == [(SITE, f"run:{c.run_id}", w.sources)]  # once: the site finished once in this run
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        codes = [r[0] for r in f.q("SELECT code FROM event WHERE code IN ('reconcile_failed', 'run_end') ORDER BY rowid")]
        assert codes == ["reconcile_failed", "run_end"]
        assert f.q("SELECT count(*) FROM url WHERE state IN ('pending', 'active')")[0][0] == 0
    finally:
        f.close()


def test_a_crawl_without_sources_reconciles_nothing(tmp_path):
    w = make_world(tmp_path)
    w.sources = None
    c = w.crawler()
    seeds.seed_round1(c, old=None, facts_db=None, cc_dir=None, probe=None, jstest=tmp_path / "jstest")
    w.run(c)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert not f.q("SELECT 1 FROM reconcile_site") and not f.q("SELECT 1 FROM event WHERE code='reconcile'")
    finally:
        f.close()


def _requests(root: Path, rows) -> Frontier:
    f = Frontier(root / "frontier.db")
    for i, (url, purpose) in enumerate(rows):
        host = scope.urlsplit(url).hostname
        f.request(f"ip:{host}", host, url, purpose, 1000.0 + 10 * i, 1001.0 + 10 * i, 200, None, scope=None,
                  ip=None, campus="off")
    return f


def test_the_scope_audit_lets_the_browser_check_ask_what_judging_needs_but_never_a_login(tmp_path):
    sheet = "https://docs.google.com/spreadsheets/d/1AbCdEf/gviz/tq"
    login = "https://sso.nknu.edu.tw/login.aspx"
    f = _requests(tmp_path, [(sheet, "jscheck"), (sheet, "spotcheck"), (login, "jscheck"), (sheet, "page")])
    try:
        out = monitor.scope_audit(f, PLAN)
    finally:
        f.close()
    assert len(out) == 2
    assert any(login in x and "login" in x for x in out)  # what jscheck.NEVER_SEND names is a breach for it too
    assert any(sheet in x and "never_host" in x for x in out)  # the crawl's own page request: a breach as before


def test_a_reconciliation_is_newer_than_a_run_by_the_order_the_events_were_written(tmp_path):
    """Same second, either way: the event order decides (monitor.reconciled and reconcile.fresh agree)."""
    f = Frontier(tmp_path / "frontier.db")
    try:
        at = "2026-10-26T01:00:00+00:00"
        checks = json.dumps({g: {"status": "pass"} for g in monitor.GATES})
        f.x("INSERT INTO reconcile_site(site, batch, at, checks, gates_ok) VALUES (?,?,?,?,1)", (SITE, "b", at, checks))

        def ev(code: str, sites) -> None:
            f.x("INSERT INTO event(at, level, code, message) VALUES (?, 'info', ?, ?)",
                (at, code, json.dumps({"reason": "finished", "sites": sites})))

        def fresh() -> tuple[bool, bool]:
            rec = monitor.reconciled(f, monitor.Rounds(f))[SITE]
            return rec.fresh, reconcile.fresh(f, SITE)

        ev("run_end", [SITE])
        ev("reconcile", [SITE])  # written after the run_end, in the same second
        assert fresh() == (True, True)
        ev("run_end", ["other.nknu.edu.tw"])
        assert fresh() == (True, True)
        ev("rejudge", None)  # covers every site
        assert fresh() == (False, False)
        ev("reconcile", [SITE])
        ev("run_end", [SITE])  # the same second again, after it this time
        assert fresh() == (False, False)
    finally:
        f.close()


def test_the_dashboard_lists_what_the_reconciliation_found_with_no_row(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        f.x("INSERT INTO site_plan(site, name, imp, round, home) VALUES (?,?,?,?,?)", (SITE, "测试单位", "全校重要", "1",
                                                                                    HOME))
        checks = {
            "R1": {"status": "fail", "menu_links": 3, "has": 1, "waiting": 1, "missing": 1,
                   "items": [{"target": f"{W}/menu-a.html", "page": HOME, "status": "missing"},
                             {"target": f"{W}/menu-b.html", "page": HOME, "status": "waiting"}]},
            "R3a": {"status": "fail", "known": 30, "ruled_out": 0, "missing": 25,
                    "items": [{"url": f"{W}/cc-{i}.html"} for i in range(20)], "more": 5},
            "R4a": {"status": "pass", "known": 2, "ruled_out": 0, "missing": 0},
            "R6": {"status": "skip", "note": "浏览器抽查要等 run 停下"},
        }
        f.x("INSERT INTO reconcile_site(site, batch, at, checks, gates_ok) VALUES (?,?,?,?,0)",
            (SITE, "b", "2026-10-26T01:00:00+00:00", json.dumps(checks)))
    finally:
        f.close()
    conn = progress.connect(tmp_path / "frontier.db")
    try:
        rec, found = progress._reconciled(conn)
    finally:
        conn.close()
    assert rec[SITE]["failed"] == ["R1", "R3a"]
    assert [(m["item"], m["n"], len(m["urls"])) for m in found] == [("R1", 1, 1), ("R3a", 25, 10)]
    assert found[0]["urls"] == [f"{W}/menu-a.html"]  # a menu item still waiting is no address the crawl lacks


def test_verify_reads_every_answer_back_and_names_a_position_that_holds_another_address(tmp_path):
    w = make_world(tmp_path)
    w.seed_and_run()
    f = Frontier(w.root / "frontier.db")
    try:
        assert monitor.warc_readback(w.root, f)[0] == []
        about, news = f.row(ABOUT), f.row(NEWS)
        f.x("UPDATE url SET warc=?, warc_offset=?, sha256=? WHERE id=?",
            (news["warc"], news["warc_offset"], news["sha256"], about["id"]))  # its row points at news.html's record
        problems, n = monitor.warc_readback(w.root, f)
    finally:
        f.close()
    assert n > 0 and problems == [f"这个位置存的是别的网址：{ABOUT}（存档里是 {NEWS}）"]


def test_the_viewer_answers_only_under_this_machines_own_names_and_builds_its_index_in_the_background(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    w = make_world(tmp_path)
    w.seed_and_run()
    done = threading.Event()
    added = []
    t = viewer.index_in_background(w.root, lambda n: (added.append(n), done.set()))
    t.join(timeout=30)
    assert done.is_set() and added[0] > 0
    server = viewer.serve(w.root, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/", "/progress", "/page?id=1"):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": f"evil.example:{port}"})
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(req, timeout=10)  # this machine's own test server
            assert err.value.code == 403, path
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/progress", timeout=10) as got:
            assert got.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_a_site_is_reconciled_while_another_is_still_being_crawled(tmp_path, monkeypatch):
    import time as real_time

    slow_site = "v.nknu.edu.tw"
    two = plan(site(SITE, imp="全校重要", name="测试单位", home=HOME),
               site(slow_site, imp="本系重要", name="慢的单位", home=f"https://{slow_site}/"))
    fast = School(pages())
    slow_pages = {f"https://{slow_site}/": page("首頁", "".join(f"<p><a href='/n{i}.html'>第{i}則</a></p>" for i in range(6))
                                                + f"<p>{BODY}</p>")}
    slow_pages |= {f"https://{slow_site}/n{i}.html": page(f"第{i}則", f"<p>{BODY}</p>") for i in range(6)}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == slow_site:
            real_time.sleep(0.15)  # this server is slow in real time: the other site finishes first
            body = slow_pages.get(str(request.url))
            return httpx.Response(200 if body else 404, headers=School.HTML,
                                  content=(body or "<html><body>not found</body></html>").encode())
        return fast(request)

    seen = []

    def record(fr, root, site_, **kw):
        seen.append((site_, reconcile.site_finished(fr, slow_site)))
        return real_site_done(fr, root, site_, **kw)

    real_site_done = reconcile.site_done
    monkeypatch.setattr(reconcile, "site_done", record)
    monkeypatch.setattr(mod, "SITE_LOOK_S", 0.0)
    clock = Clock()

    def client(warc):
        return mod.archive_client(warc, transport=httpx.MockTransport(answer), clock=clock, sleep=clock.sleep)

    root = tmp_path / "archive"
    c = mod.Crawler(root, plan=two, clock=clock, sleep=clock.sleep, resolver=_resolver, client_factory=client,
                    netcheck=lambda: True, disk_free=lambda p: 10**12, campus_url=None,
                    reconcile_sources=Sources(cc_dir=None, old=None))
    seeds.seed_round1(c, old=None, facts_db=None, cc_dir=None, probe=None, jstest=None)
    try:
        c.run(poll_s=0.01)
    finally:
        c.frontier.close()
    assert c.end_reason == "finished"
    assert seen[0] == (SITE, False)  # the fast site, while the slow one still had pages to fetch
    assert sorted(s for s, _ in seen) == sorted([SITE, slow_site])
