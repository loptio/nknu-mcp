"""Round 1 from its seeds to a site that is 爬完, end to end (design §5, §8.4, §10.5, §18.2a): a made-up school site
is seeded (seed_round1: the list, a made-up 10-24 archive, a Common Crawl index and a v1 notice; the page types a
browser test judged before the round written as rows), crawled on a fake network (Crawler.run), and its page types
checked in a made-up browser (jscheck). After every step where something is still missing, neither the dashboard
(progress.Dashboard, drawn by viewer) nor monitor.site_status says the site is 「✓ 爬完」; only once everything is
there do both say so. Then each way something can be missing, one at a time, on its own copy of the site, and the
commands a person runs after a crawl (jscheck --set, rejudge, enqueue, review). What came after these steps until
the judgment-layer cut of 11-19 (the reconciliation, the acceptance) is gone: every page whole and nothing waiting
is 爬完.

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
TESTED = (HOME, ABOUT, NEWS)  # a browser test judged these types before: contact.html's is new (todo until jscheck)


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
        out[EMPTY] = page("說明", "<p>敬請期待</p>")  # almost nothing of its own: thin once rejudged
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
    """One clock for every step (crawl, jscheck): the politeness audit reads their requests together."""

    async def asleep(self, s: float) -> None:
        self.sleep(s)
        await asyncio.sleep(0)


def _resolver(host: str):
    return f"ip:{host}", None, host


# ---------------------------------------------------------------- what seeding reads, and the types known before


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
    """v1's facts.db as seeds.v1_known reads it: one notice v1 knew."""
    path = root / "facts.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE doc(url TEXT, unit TEXT, title TEXT, category TEXT, withdrawn_at TEXT)")
    conn.execute("INSERT INTO doc VALUES (?, '测试单位', '最新消息', 'news', NULL)", (NEWS,))
    conn.commit()
    conn.close()
    return path


def known_types(f: Frontier, urls) -> None:
    """These pages' types as a browser test before the round left them (the 10-25 test's import did, until the cut of
    11-19; nothing imports one now): each judged ok (the same text directly and in the browser), source import, no
    page of this round counted yet."""
    for u in urls:
        parts = pagetypes.type_parts(u)
        f.x("INSERT INTO page_type(key, site, host, path_tpl, params, verdict, method, direct_chars, rendered_chars, "
            "sample_url, source) VALUES (?,?,?,?,?,'ok','direct',120,120,?,'import')",
            (parts.key, parts.site, parts.host, parts.path_tpl, json.dumps(list(parts.params)), u))


# ---------------------------------------------------------------- one site, step by step


@dataclass
class World:
    root: Path
    clock: Clock
    school: School
    old: Path  # the made-up 10-24 archive (read only)
    cc_dir: Path | None  # the Common Crawl index seeding reads
    shown: dict[str, str] = field(default_factory=dict)  # what the browser shows of each page (the page itself)
    known: tuple[str, ...] = TESTED  # the pages whose types a browser test judged ok before the round

    def client(self, warc):
        return mod.archive_client(warc, transport=httpx.MockTransport(self.school), clock=self.clock,
                                  sleep=self.clock.sleep)

    def browser(self) -> FakeBrowser:
        return FakeBrowser({u: FakePage(h) for u, h in self.shown.items()})

    def crawler(self) -> mod.Crawler:
        return mod.Crawler(self.root, plan=PLAN, clock=self.clock, sleep=self.clock.sleep, resolver=_resolver,
                           client_factory=self.client, netcheck=lambda: True, disk_free=lambda p: 10**12,
                           campus_url=None)

    def seed(self) -> mod.Crawler:
        """Seeded, the types known before the round written, nothing fetched yet: the caller runs it or closes it."""
        c = self.crawler()
        seeds.seed_round1(c, old=self.old, facts_db=self.root.parent / "facts.db", cc_dir=self.cc_dir, probe=None)
        known_types(c.frontier, self.known)
        return c

    def seed_and_run(self) -> mod.Crawler:
        return self.run(self.seed())

    def run(self, c: mod.Crawler | None = None) -> mod.Crawler:
        c = c or self.crawler()
        try:
            c.run(poll_s=0.01)
        finally:
            c.frontier.close()
        return c

    def jscheck(self) -> jscheck.JsCheckReport:
        check = jscheck.JsCheck(self.root, old=self.old, browser=self.browser(), client_factory=self.client,
                                resolver=_resolver, netcheck=lambda: True, plan=PLAN, clock=self.clock,
                                sleep=self.clock.sleep, asleep=self.clock.asleep, rng=random.Random(7))
        return check.run()

    def rejudge(self) -> reconcile.Rejudged:
        return reconcile.rejudge(self.root, [SITE], plan=PLAN)

    def status(self) -> monitor.SiteStatus:
        """The site's state as monitor.site_status reads it from the ledger."""
        f = Frontier(self.root / "frontier.db", readonly=True)
        try:
            return monitor.site_status(f)[SITE]
        finally:
            f.close()

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

    def state(self) -> tuple[str, str, monitor.SiteStatus]:
        """The site's state as the dashboard shows it and as monitor.site_status gives it, with the whole status."""
        dash, html = self.dashboard()
        got = self.status()
        assert ("✓ 爬完" in html) == (dash["state"] == "done")  # the mark drawn is the state worked out
        return dash["state"], got.state, got

    def not_done(self, *expected: str) -> monitor.SiteStatus:
        """Neither the dashboard nor monitor.site_status says 爬完; both give one of these states."""
        dash, ver, got = self.state()
        assert dash != "done" and ver != monitor.DONE, (dash, ver, got.why)
        assert dash == ver, (dash, ver)  # the dashboard shows monitor.site_status's state, never one of its own
        if expected:
            assert ver in expected, (ver, got.why)
        return got


def make_world(tmp_path: Path, *, missing: tuple[str, ...] = (), erring: tuple[str, ...] = (), empty: bool = False,
               cc: bool = True, shown: dict[str, str] | None = None) -> World:
    _facts(tmp_path)
    html = pages(empty=empty)
    world = World(tmp_path / "archive", Clock(), School(html, missing, erring), old=_old(tmp_path),
                  cc_dir=_cc(tmp_path) if cc else None)
    world.shown = {**html, **(shown or {})}
    return world


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_POLL_S", 0.02)


# ---------------------------------------------------------------- the whole way, the site never 爬完 before it is


def test_seed_run_jscheck_says_done_only_once_everything_is_there(tmp_path):
    w = make_world(tmp_path)
    # seeded only: nothing fetched yet
    w.seed().frontier.close()
    w.not_done(monitor.CRAWLING)
    # crawled: every page fetched, contact.html's type new and judged by nobody
    w.run()
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        end = json.loads(f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")[0][0])
        assert end["reason"] == "finished"
        new = f.q("SELECT verdict, why, source FROM page_type WHERE key=?", (pagetypes.type_key(CONTACT),))[0]
        assert tuple(new) == ("todo", "new", "crawl")
    finally:
        f.close()
    # the page itself is got since the owner's 11-16 decision (export batch T5), nothing 待判定 any more; its type
    # waits for the browser: yellow
    got = w.not_done(monitor.LOOK)
    assert not any("待判定" in x for x in got.why)
    assert any("还没判定" in x and "jscheck 还没跑完" in x for x in got.why), got.why
    # the browser judged the new type: every page whole, nothing waiting, the site is 爬完 on both
    report = w.jscheck()
    assert report.reason == "finished" and report.todo_left == 0
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.why
    assert got.why == ["每一页都完整，没有还在等的"] and got.jscheck_done
    # what was sent: one request at a time per server, nothing out of scope, jscheck's in the request log
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        purposes = {r[0] for r in f.q("SELECT DISTINCT purpose FROM request")}
        assert "jscheck" in purposes
        assert monitor.politeness(f) == [] and monitor.scope_audit(f, PLAN) == []
    finally:
        f.close()
    # a rejudge, then a run with nothing left to fetch, change nothing of that: still 爬完
    w.rejudge()
    assert w.state()[:2] == ("done", monitor.DONE)
    w.run()
    assert w.state()[:2] == ("done", monitor.DONE)


# ---------------------------------------------------------------- each way something can be missing, on its own


def _result(w: World, url: str) -> str:
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        return f.q("SELECT result FROM url_result WHERE url=?", (url,))[0][0]
    finally:
        f.close()


def _all_steps(w: World, *, jscheck_too: bool = True) -> None:
    w.seed_and_run()
    if jscheck_too:
        assert w.jscheck().reason == "finished"


def test_an_attachment_that_errs_keeps_the_site_from_done(tmp_path):
    w = make_world(tmp_path, erring=(PDF_URL,))
    _all_steps(w)  # the run ends waiting (B1: only a retry is left)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        assert f.q("SELECT result FROM url_result WHERE url=?", (PDF_URL,))[0][0] == "retry"
        end = json.loads(f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")[0][0])
        assert end["reason"] == "waiting"
    finally:
        f.close()
    got = w.not_done(monitor.CRAWLING)  # the page lacks its file
    assert any("等重试" in x for x in got.why)


def test_an_attachment_the_school_no_longer_has_is_complete_on_the_schools_side(tmp_path):
    # design §10.2, §10.4: a 404 asked twice is 学校那边已经不存在, the page 完整（学校那边缺）, which §10.5 counts as
    # complete: the site may be 爬完
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


def test_a_page_with_almost_nothing_on_it_is_thin_once_rejudged_and_red(tmp_path):
    w = make_world(tmp_path, empty=True)
    _all_steps(w)
    assert _result(w, EMPTY) == "ok"  # a run marks no page thin: only a rejudge reads the pages back for that
    done = w.rejudge()
    assert (done.thin_set, done.thin_cleared) == (1, 0)
    assert _result(w, EMPTY) == "thin"
    got = w.not_done(monitor.ERROR)
    assert any(i.url == EMPTY and i.result == "thin" for i in got.issues), [i.line() for i in got.issues]


def test_without_jscheck_a_new_type_waits_yellow(tmp_path):
    w = make_world(tmp_path)
    _all_steps(w, jscheck_too=False)
    got = w.not_done(monitor.LOOK)
    assert not got.jscheck_done  # jscheck has not run: yellow, not yet red
    assert got.why == ["1 类有网页的类型还没判定（jscheck 还没跑完）"]


def test_a_type_the_browser_cannot_judge_is_red_once_jscheck_ended(tmp_path):
    w = make_world(tmp_path)
    w.shown = {u: h for u, h in w.shown.items() if u != CONTACT}  # the browser shows nothing of it: no conclusion
    _all_steps(w)
    f = Frontier(w.root / "frontier.db", readonly=True)
    try:
        types = f.q("SELECT verdict FROM page_type WHERE n_pages > 0 AND key=?", (pagetypes.type_key(CONTACT),))
        assert [r[0] for r in types] == ["unresolved"]  # the browser's own conclusion: none (a person says, --set)
    finally:
        f.close()
    assert _result(w, CONTACT) == "unresolved"
    got = w.not_done(monitor.ERROR)
    assert got.jscheck_done and got.why == ["1 个网页或资源要人看"]
    assert [(i.url, i.result) for i in got.issues] == [(CONTACT, "unresolved")]


# ---------------------------------------------------------------- the commands


@pytest.fixture
def done_world(tmp_path, monkeypatch):
    monkeypatch.setattr(round1, "load", lambda *a, **k: PLAN)
    w = make_world(tmp_path)
    _all_steps(w)
    assert w.state()[:2] == ("done", monitor.DONE)
    return w


def test_rejudge_enqueue_and_review_commands(done_world):
    w = done_world
    runner = CliRunner()
    got = runner.invoke(app, ["archive", "rejudge", "--dir", str(w.root), "--sites", SITE])
    assert got.exit_code == 0, got.output
    assert "重算 1 个网站：网页 4 页" in got.output
    assert w.state()[:2] == ("done", monitor.DONE)  # worked out again from what is stored: nothing changed
    listed = w.root.parent / "to-fetch.txt"
    listed.write_text(f"{W}/later.html\t人看过，要抓\n# 不抓的写理由\n", encoding="utf-8")
    got = runner.invoke(app, ["archive", "enqueue", str(listed), "--dir", str(w.root)])
    assert got.exit_code == 0 and "写进待抓清单 1 个" in got.output, got.output
    got = runner.invoke(app, ["archive", "enqueue", str(w.root.parent / "no-such-list.txt"), "--dir", str(w.root)])
    assert got.exit_code == 1 and "找不到清单" in got.output
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
    listed = w.root.parent / "to-fetch.txt"
    listed.write_text(f"{W}/later.html\n", encoding="utf-8")
    lock = mod.hold_run_lock(w.root)
    try:
        for args in (["jscheck"], ["enqueue", str(listed)], ["rejudge", "--sites", SITE],
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
    listed = tmp_path / "to-fetch.txt"
    listed.write_text(f"{W}/later.html\n", encoding="utf-8")
    for args in (["jscheck"], ["enqueue", str(listed)], ["rejudge", "--sites", SITE], ["review"],
                 ["jscheck", "--set", "k", "ok"]):
        got = runner.invoke(app, ["archive", *args, "--dir", str(old)])
        assert got.exit_code == 1 and "旧爬虫的存档" in got.output, (args, got.output)
    assert (old / "frontier.db").read_bytes() == before
    assert scope.site_key(HOME) == SITE


# ---------------------------------------------------------------- the wiring, one piece at a time


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


