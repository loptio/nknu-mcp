"""Round 1's acceptance (design §10.5, §13: monitor.site_status, monitor.checks, monitor.verify) on small made-up
frontiers: every state a site can be in (✗ 出错, ⚠ 要看, 还在抓, ⚠ 纪录齐了，还没对账, ✓ 爬完, ⚠ 没检查), each check's
four results (✓ ⚠ ✗ 没检查), the exit codes and the conclusion (never all green while a known gap is open), the
archive read back by position, requests made on campus, the dashboard's way of asking, and viewer.locate by the row's
own record. Nothing here connects anywhere; every address is made up, and no fixture holds a name."""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nknu.archive import monitor, outcome, profiles
from nknu.archive.frontier import Frontier
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.warc import WarcSink

T0, T1, T2, T3, T4, T5 = (f"2026-10-26T0{h}:00:00+00:00" for h in range(6))
HOME_HTML = "<html><body><h1>最新消息</h1><p>115 學年度第 1 學期行事曆已公告。</p></body></html>"
_n = itertools.count(1)


class Arch:
    """A frontier.db of made-up rows; each test writes what it needs and reads it back through a read-only open."""

    def __init__(self, root: Path):
        self.root = root
        self.f = Frontier(root / "frontier.db")
        self.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', '*', 'ok', 1)")
        self.sink: WarcSink | None = None

    def url(self, site: str, path: str, **cols) -> int:
        row = {"url": f"https://{site}{path}", "host": site.split("/")[0], "site": site, "owner_site": site,
               "tier": 0, "kind": "page", "kind0": "page", "depth": 1, "found_via": "link", "state": "done",
               "status": 200, "page_type": "t:ok", "sniff": "html", "fetched_at": T0}
        row.update(cols)
        row.setdefault("path", row["url"].split("?", 1)[0])
        with self.f.lock:
            return self.f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                                       tuple(row.values())).lastrowid

    def site(self, site: str, imp: str = "全校重要", base: int | None = None, bare: bool = False, **home) -> int:
        """A round-1 site and its home page, which (as every real page) loads the school's script (page_res, need no),
        unless bare: a page with no resource row at all."""
        self.f.x("INSERT INTO site_plan(site, name, imp, round, home, base_urls) VALUES (?,?,?,?,?,?)",
                 (site, "某单位", imp, "1", f"https://{site}/", base))
        uid = self.url(site, "/", found_via="seed", depth=0, **home)
        self.f.seed_src(f"https://{site}/", "site_home")
        if not bare:
            self.res(uid, f"https://{site}/js/site.js", role="script", need="no", why="script")
        return uid

    def res(self, page_id: int, key: str, role: str = "file", need: str = "must", **cols) -> None:
        self.f.x("INSERT INTO page_res(page_id, res_key, how, role, need, why, res_id, action_id, text) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (page_id, key, cols.get("how", "get"), role, need, cols.get("why"),
                                                cols.get("res_id"), cols.get("action_id"), cols.get("text")))

    def file(self, page_id: int, site: str, path: str, role: str = "file", need: str = "must", **cols) -> int:
        uid = self.url(site, path, **{"kind": "file", "kind0": "file", "page_type": None, "sniff": "pdf", **cols})
        self.res(page_id, f"https://{site}{path}", role=role, need=need, res_id=uid)
        return uid

    def event(self, code: str, at: str, **msg) -> None:
        self.f.x("INSERT INTO event(at, level, code, message) VALUES (?,?,?,?)", (at, "info", code, json.dumps(msg)))

    def reconcile(self, site: str, at: str, gates_ok: int = 1, cover_cc=None, cover_old=None, **gates) -> None:
        checks = {g: {"result": gates.get(g, "pass")} for g in monitor.GATES}
        self.f.x("INSERT OR REPLACE INTO reconcile_site(site, batch, at, checks, cover_cc, cover_old, gates_ok) "
                 "VALUES (?,?,?,?,?,?,?)", (site, "b1", at, json.dumps(checks), cover_cc, cover_old, gates_ok))

    def walk(self, list_id: int, ended_by: str | None, ok: int | None) -> None:
        self.f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, rows, ended_by, ok) VALUES (?,?,?,?,?,?,?,?)",
                 (list_id, "x", "generic", 1, 1, 3, ended_by, ok))

    def request(self, url: str, started: float, ended: float, *, ip: str = "140.127.1.1", host: str | None = None,
                site: str | None = None, campus: str = "off") -> None:
        host = host or url.split("/")[2]
        self.f.request(f"ip:{ip}", host, url, "page", started, ended, 200, None,
                       scope=f"admit:r1:{site or host}:page", ip=ip, campus=campus)

    def archive(self, uid: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        """Store an answer for row uid in a WARC file and point the row at its response record."""
        if self.sink is None:
            self.sink = WarcSink(self.root / "warc", "ip_1", 1, datetime(2026, 10, 26, tzinfo=UTC))
        url = self.f.q("SELECT url FROM url WHERE id=?", (uid,))[0][0]
        sha = hashlib.sha256(body).hexdigest()
        off, length = self.sink.write(Snapshot(Target(url), url, 200, {"content-type": ctype}, body, None,
                                               datetime.now(UTC), sha), {})
        self.f.x("UPDATE url SET warc=?, warc_offset=?, warc_len=?, sha256=?, ctype=?, final_url=url WHERE id=?",
                 (str(self.sink.path.relative_to(self.root)), off, length, sha, ctype, uid))

    def read(self, fn, *args, **kw):
        ro = Frontier(self.root / "frontier.db", readonly=True)
        try:
            return fn(ro, *args, **kw)
        finally:
            ro.close()

    def states(self) -> dict[str, str]:
        return {k: v.state for k, v in self.read(monitor.site_status).items()}

    def close(self) -> None:
        if self.sink is not None:
            self.sink.close()
        self.f.close()


@pytest.fixture
def a(tmp_path):
    arch = Arch(tmp_path)
    yield arch
    arch.close()


def by_no(cs) -> dict[str, monitor.Check]:
    return {c.no: c for c in cs}


# ---------------------------------------------------------------- §10.5: each site's state


def test_every_state_a_site_can_be_in(a):
    a.event("run_end", T1, reason="finished", sites=None)
    home = a.site("done.nknu.edu.tw")
    a.file(home, "done.nknu.edu.tw", "/a.pdf")
    a.reconcile("done.nknu.edu.tw", T2)
    a.site("norec.nknu.edu.tw")  # every page complete, no reconciliation yet
    a.site("stale.nknu.edu.tw")
    a.reconcile("stale.nknu.edu.tw", T0)  # older than the run_end that covers it
    a.site("r6.nknu.edu.tw")
    a.reconcile("r6.nknu.edu.tw", T2, gates_ok=0, R6="skip")  # the crawler's own offline reconciliation (§18.2a)
    bad = a.site("bad.nknu.edu.tw")
    a.file(bad, "bad.nknu.edu.tw", "/x.pdf", state="failed", reason="timeout", status=None)
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:js', 'js.nknu.edu.tw', 'js', 1)")
    a.site("js.nknu.edu.tw", page_type="t:js")
    a.site("wait.nknu.edu.tw", state="pending", status=None, fetched_at=None)
    got = a.read(monitor.site_status)
    assert {k: v.state for k, v in got.items()} == {
        "done.nknu.edu.tw": monitor.DONE, "norec.nknu.edu.tw": monitor.UNRECONCILED,
        "stale.nknu.edu.tw": monitor.UNRECONCILED, "r6.nknu.edu.tw": monitor.UNRECONCILED,
        "bad.nknu.edu.tw": monitor.ERROR, "js.nknu.edu.tw": monitor.LOOK, "wait.nknu.edu.tw": monitor.CRAWLING}
    assert "还没对账" in got["norec.nknu.edu.tw"].why[0]
    assert "旧" in got["stale.nknu.edu.tw"].why[0]
    assert "R6" in got["r6.nknu.edu.tw"].why[0]  # a skipped gate is not red, and never 爬完 (§18.2a step 3)
    assert got["done.nknu.edu.tw"].label == "✓ 爬完" and got["done.nknu.edu.tw"].color == "green"
    assert got["bad.nknu.edu.tw"].label == "✗ 出错" and "连接失败" in got["bad.nknu.edu.tw"].why[0]
    assert [i.bucket for i in got["bad.nknu.edu.tw"].issues] == ["failed"]
    assert next(iter(got)) == "bad.nknu.edu.tw"  # the worst first


def test_pending_check_is_yellow_until_this_rounds_jscheck_ended_then_red(a):
    site = "s.nknu.edu.tw"
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:todo', ?, 'todo', 1)", (site,))
    a.site(site, page_type="t:todo")
    a.event("run_end", T1, reason="finished")
    assert a.states()[site] == monitor.LOOK
    a.event("jscheck_end", T2, reason="stop_file")  # stopped: not this round's end
    assert a.states()[site] == monitor.LOOK
    a.event("jscheck_end", T3, reason="finished")
    st = a.read(monitor.site_status)[site]
    assert st.state == monitor.ERROR and any("jscheck 已经跑完" in w for w in st.why)
    a.event("run_end", T4, reason="finished", sites=["other.nknu.edu.tw"])  # a run that did not cover the site
    assert a.states()[site] == monitor.ERROR
    a.event("run_end", T5, reason="waiting", sites=None)  # a new crawl of it: the browser check is due again
    assert a.states()[site] == monitor.LOOK


def test_lab_and_round_two_resources_are_yellow_and_a_sites_own_round_two_rows_do_not_count(a):
    a.event("run_end", T1)
    h = a.site("lab.nknu.edu.tw")
    a.file(h, "lab.nknu.edu.tw", "/in.pdf", state="pending", reason="dns", status=None)
    h2 = a.site("far.nknu.edu.tw")
    far = a.url("x.example.org", "/far.pdf", owner_site="far.nknu.edu.tw", kind="file", kind0="file", page_type=None,
                state="later", reason="round2:offsite_file", status=None, tier=2)
    a.res(h2, "https://x.example.org/far.pdf", res_id=far)
    a.site("own2.nknu.edu.tw")
    a.url("own2.nknu.edu.tw", "/old/1", state="later", reason="round2:other_site", status=None, tier=2)
    a.reconcile("own2.nknu.edu.tw", T2)
    got = a.read(monitor.site_status)
    assert got["lab.nknu.edu.tw"].state == monitor.LOOK and "实验室" in got["lab.nknu.edu.tw"].why[0]
    assert got["far.nknu.edu.tw"].state == monitor.LOOK and "下一轮" in got["far.nknu.edu.tw"].why[0]
    assert got["own2.nknu.edu.tw"].state == monitor.DONE  # its round-two address is no round-1 gap (§10.5)


def test_list_walks_frames_and_what_the_rules_cannot_name(a):
    a.event("run_end", T1)
    a.walk(a.site("l1.nknu.edu.tw", page_role="list"), "mismatch:page", 0)
    a.site("l2.nknu.edu.tw", page_role="list")  # no walk recorded: 列表没核对
    a.walk(a.site("l3.nknu.edu.tw", page_role="list"), "walking", None)
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:frame', 'fr.nknu.edu.tw', 'frame', 1)")
    a.site("fr.nknu.edu.tw", page_type="t:frame")  # a frame page that names no frame
    got = a.read(monitor.site_status)
    assert got["l1.nknu.edu.tw"].state == monitor.ERROR and "列表没翻完" in got["l1.nknu.edu.tw"].why[0]
    assert got["l2.nknu.edu.tw"].state == monitor.LOOK and "列表没核对" in got["l2.nknu.edu.tw"].why[0]
    assert got["l3.nknu.edu.tw"].state == monitor.CRAWLING
    assert got["fr.nknu.edu.tw"].state == monitor.ERROR and "框架" in got["fr.nknu.edu.tw"].why[0]


def test_a_school_frame_pages_missing_file_makes_the_page_that_holds_it_red(a):
    a.event("run_end", T1)
    parent = a.site("p.nknu.edu.tw")
    frame = a.site("f.nknu.edu.tw")
    a.res(parent, "https://f.nknu.edu.tw/", role="iframe", res_id=frame)
    a.file(frame, "f.nknu.edu.tw", "/gone.pdf", state="failed", reason="connect", status=None)
    a.reconcile("p.nknu.edu.tw", T2)
    got = a.read(monitor.site_status)
    assert got["f.nknu.edu.tw"].state == monitor.ERROR
    assert got["p.nknu.edu.tw"].state == monitor.ERROR  # §10.4 rule 1: the frame's resources count for its holder
    assert any(i.url.endswith("/gone.pdf") for i in got["p.nknu.edu.tw"].issues)


def test_homes_that_got_nothing_and_sites_that_shrank(a):
    a.event("run_end", T1)
    a.f.x("INSERT INTO site_plan(site, name, imp, round, home) VALUES ('nohome.nknu.edu.tw', 'x', '全校重要', '1', "
          "'https://nohome.nknu.edu.tw/')")
    a.site("login.nknu.edu.tw", flags="login")
    a.site("shrunk.nknu.edu.tw", base=100)
    a.site("growing.nknu.edu.tw", base=100)
    a.url("growing.nknu.edu.tw", "/p1", state="pending", status=None, fetched_at=None)
    got = a.read(monitor.site_status)
    assert got["nohome.nknu.edu.tw"].state == monitor.ERROR and "首页" in got["nohome.nknu.edu.tw"].why[0]
    assert got["login.nknu.edu.tw"].state == monitor.ERROR
    assert any("首页没抓到内容" in w for w in got["login.nknu.edu.tw"].why)
    assert got["shrunk.nknu.edu.tw"].state == monitor.ERROR and "旧存档" in got["shrunk.nknu.edu.tw"].why[0]
    assert got["growing.nknu.edu.tw"].state == monitor.CRAWLING  # still fetching: not compared yet


def test_a_failed_gate_is_red_and_a_persons_mark_takes_a_resource_off_the_list(a):
    a.event("run_end", T1)
    a.site("r1.nknu.edu.tw")
    a.reconcile("r1.nknu.edu.tw", T2, gates_ok=0, R1="fail")
    h = a.site("mark.nknu.edu.tw")
    pdf = a.file(h, "mark.nknu.edu.tw", "/home.pdf", sniff="html")  # an attachment that came back as a web page
    assert a.states()["mark.nknu.edu.tw"] == monitor.ERROR
    a.f.x("INSERT INTO review_mark(url_id, mark, result, reason, status, note, at, fetched_at) VALUES "
          "(?, 'school_side', 'not_file', NULL, 200, '学校的链接指错', ?, ?)", (pdf, T2, T0))
    got = a.read(monitor.site_status)
    assert got["r1.nknu.edu.tw"].state == monitor.ERROR and "R1" in got["r1.nknu.edu.tw"].why[0]
    assert got["mark.nknu.edu.tw"].state == monitor.UNRECONCILED
    assert got["mark.nknu.edu.tw"].buckets[outcome.SCHOOL_SIDE] == 1


def test_no_resource_layer_is_not_checked_never_green(a):
    a.event("run_end", T1)
    a.site("bare.nknu.edu.tw", bare=True)
    a.reconcile("bare.nknu.edu.tw", T2)
    a.site("redbare.nknu.edu.tw", bare=True, flags="login")
    got = a.read(monitor.site_status)
    assert got["bare.nknu.edu.tw"].state == monitor.NOT_CHECKED and "资源层" in got["bare.nknu.edu.tw"].why[0]
    assert got["redbare.nknu.edu.tw"].state == monitor.ERROR  # what is wrong stays red


def test_requests_made_on_campus_are_a_warning_beside_the_state(a):
    site = "c.nknu.edu.tw/aca"
    h = a.site(site)
    a.file(h, site, "/a.pdf")
    a.event("run_end", T1)
    a.reconcile(site, T2)
    t = time.time()
    a.request(f"https://{site}/", t, t + 0.5, site=site, campus="on")
    a.request(f"https://{site}/a.pdf", t + 4, t + 4.5, site=site, campus="off")
    st = a.read(monitor.site_status)[site]
    assert st.state == monitor.DONE and any("校内网络" in w for w in st.warnings)
    c = by_no(a.read(monitor.checks, root=a.root))["11c"]
    assert c.status == monitor.WARN and any(site in x for x in c.lines)
    text, _code = monitor.report(a.root)
    assert "在校内网络抓的请求 1 个" in text


def test_a_site_is_never_recorded_or_done_while_a_page_is_not_complete():
    """The one promise §10.5 makes (record 二·5): 纪录齐了 and 爬完 only when every round-1 page is complete. Random
    sites, pages, resources, walks, marks and reconciliations; seeded, so a failure can be repeated."""
    rng = random.Random(1026)
    states = [("done", 200, None, None), ("done", 404, None, None), ("failed", None, "timeout", None),
              ("pending", None, None, None), ("pending", None, "dns", None), ("done", 200, None, "login"),
              ("done", 200, None, "thin"), ("later", None, "round2:offsite", None), ("held", None, "cap:file", None),
              ("skipped", None, "out:social", None), ("done", 302, None, None)]
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        arch = Arch(Path(d))
        for key, verdict in (("t:todo", "todo"), ("t:js", "js"), ("t:frame", "frame")):
            arch.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES (?, '*', ?, 1)", (key, verdict))
        arch.event("run_end", T1)
        for i in range(80):
            site = f"r{i}.nknu.edu.tw"
            home = arch.site(site, page_type=rng.choice(("t:ok", "t:ok", "t:ok", "t:todo", "t:js", "t:frame")),
                             page_role=rng.choice((None, None, "list")))
            pages = [home] + [arch.url(site, f"/p{k}", page_type="t:ok") for k in range(rng.randrange(3))]
            for p in pages:
                if rng.random() < 0.3:
                    arch.walk(p, rng.choice(("last_page", "walking", "mismatch:page", None)), rng.choice((0, 1, None)))
                for k in range(rng.randrange(4)):
                    st, status, reason, flags = rng.choice(states)
                    uid = arch.url(site, f"/f{p}-{k}.pdf", kind="file", kind0="file", page_type=None, sniff="pdf",
                                   state=st, status=status, reason=reason, flags=flags)
                    arch.res(p, f"https://{site}/f{p}-{k}.pdf", role=rng.choice(("file", "img", "iframe")),
                             need=rng.choice(("must", "must", "round2")), res_id=uid)
            if rng.random() < 0.7:
                arch.reconcile(site, rng.choice((T0, T2)), gates_ok=rng.choice((0, 1, 1)),
                               R6=rng.choice(("pass", "pass", "skip", "fail")))
        got = arch.read(monitor.facts)
        sts = arch.read(monitor.site_status)
        arch.close()
    by_site = {}
    for p in got.pages:
        by_site.setdefault(p.site, []).append(p.level)
    seen = set()
    for s, st in sts.items():
        seen.add(st.state)
        if st.state in (monitor.UNRECONCILED, monitor.DONE):
            assert all(lv in ("complete", "complete_school") for lv in by_site.get(s, [])), (s, st.why)
        if st.state == monitor.DONE:
            assert st.reconcile is not None and st.reconcile.fresh and st.reconcile.passed
    assert {monitor.ERROR, monitor.LOOK, monitor.CRAWLING} <= seen  # the grid reaches the states it should


def test_the_dashboard_may_ask_with_its_own_connection_and_its_pages(a):
    """progress.slow_snapshot's way (its read-only connection, its subset of page_completeness's columns): the same
    states, and each reads as a mapping (state, why, cover_cc …)."""
    a.event("run_end", T1)
    a.walk(a.site("l1.nknu.edu.tw", page_role="list"), "mismatch:page", 0)
    h = a.site("ok.nknu.edu.tw")
    a.file(h, "ok.nknu.edu.tw", "/a.pdf")
    a.reconcile("ok.nknu.edu.tw", T2, cover_cc=0.5)
    want = a.states()
    conn = sqlite3.connect(f"file:{a.root / 'frontier.db'}?mode=ro", uri=True, check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        outcome.create_views(conn, temp=True)
        conn.execute("PRAGMA query_only=ON")
        pages = [dict(r) for r in conn.execute(
            "SELECT id, site, tier, content_id, own, level, label, reasons, page_role, ended_by, list_ok, miss_file, "
            "miss_img, miss_other, n_failed, n_review FROM page_completeness")]
        got = monitor.site_status(conn, time.time(), pages=pages)
        assert {k: v.state for k, v in got.items()} == want
        d = dict(got["ok.nknu.edu.tw"])
        assert d["state"] == monitor.DONE and d["cover_cc"] == 0.5 and any("覆盖率" in w for w in d["warnings"])
        cheap = monitor.checks(conn, time.time(), cheap=True)
        assert {c.no for c in cheap} == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "11b", "11c", "12",
                                         "16"}
        assert by_no(cheap)["5"].status == monitor.SKIP  # page completeness not given: 没检查, never a pass
        assert by_no(monitor.checks(conn, time.time(), cheap=True, pages=pages))["5"].status == monitor.PASS
    finally:
        conn.close()


# ---------------------------------------------------------------- §13: the checks and verify


def good_archive(a: Arch) -> str:
    """One site with nothing missing: its home and a PDF archived, two polite requests made off campus, v1's notice
    fetched, this round's run, browser check and reconciliation, in that order."""
    site = "good.nknu.edu.tw"
    home = a.site(site, base=1)
    pdf = a.file(home, site, "/a.pdf")
    a.archive(home, HOME_HTML.encode())
    a.archive(pdf, b"%PDF-1.4 made up", "application/pdf")
    a.f.seed_src(f"https://{site}/a.pdf", "v1_known")
    t = time.time() - 60
    a.request(f"https://{site}/", t, t + 0.5)
    a.request(f"https://{site}/a.pdf", t + 4, t + 4.5)
    a.event("run_end", T1, reason="finished")
    a.event("jscheck_end", T2, reason="finished")
    a.reconcile(site, T3)
    return site


def no_problems(_f, _root) -> list[str]:
    return []


def test_all_passed_is_exit_0_and_a_known_gap_keeps_it_from_all_green(a):
    good_archive(a)
    a.close()
    got = monitor.verify(a.root, wayback_open=False, resource_check=no_problems)
    assert {c.no: c.status for c in got.checks} == {c.no: monitor.PASS for c in got.checks}, got.text
    assert (got.code, monitor.conclusion(got.checks)) == (0, "第一轮完成")
    got = monitor.verify(a.root, resource_check=no_problems)  # the Wayback line stays until the user agrees
    assert by_no(got.checks)["16"].status == monitor.WARN
    assert got.code == 1 and monitor.conclusion(got.checks) == "第一轮完成，有已知缺口 1 项（第 16 项逐项列出）"
    assert got.path is not None and got.path.read_text(encoding="utf-8") == got.text
    assert "✓ 爬完" in got.text and monitor.WAYBACK_GAP in got.text


def test_something_wrong_is_exit_2_and_never_complete(a):
    site = good_archive(a)
    home = a.f.q("SELECT id FROM url WHERE url=?", (f"https://{site}/",))[0][0]
    a.file(home, site, "/lost.pdf", state="failed", reason="timeout", status=None)
    a.close()
    got = monitor.verify(a.root, wayback_open=False, resource_check=no_problems)
    assert got.code == 2 and monitor.conclusion(got.checks).startswith("还没完成")
    assert by_no(got.checks)["17"].status == monitor.FAIL
    assert got.sites[site].state == monitor.ERROR
    assert any("lost.pdf" in x for x in by_no(got.checks)["17"].lines)


def test_something_to_look_at_or_not_checked_is_exit_1():
    assert monitor.exit_code([monitor.Check("1", "x", monitor.SKIP)]) == 1  # 没检查 is no pass
    assert monitor.exit_code([monitor.Check("1", "x", monitor.WARN), monitor.Check("2", "y")]) == 1
    assert monitor.exit_code([monitor.Check("1", "x", monitor.WARN), monitor.Check("2", "y", monitor.FAIL)]) == 2
    assert monitor.exit_code([monitor.Check("1", "x")]) == 0


def test_verify_reads_the_frontier_read_only(a):
    good_archive(a)
    a.close()
    db = a.root / "frontier.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monitor.verify(a.root, resource_check=no_problems)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_item_7_waits_for_jscheck_then_counts_types_and_pages(a):
    site = "t.nknu.edu.tw"
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:todo', ?, 'todo', 1)", (site,))
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:old', ?, 'todo', 0)", (site,))
    a.site(site, page_type="t:todo")
    a.event("run_end", T1)
    assert by_no(a.read(monitor.checks))["7"].status == monitor.WARN
    a.event("jscheck_end", T2, reason="finished")
    c = by_no(a.read(monitor.checks))["7"]
    assert c.status == monitor.FAIL and "1 类" in c.detail and "旧类型这一轮没出现 1 类" in c.detail
    a.f.x("INSERT INTO page_type(key, site, verdict, why, n_pages) VALUES ('t:un', ?, 'unresolved', "
          "'inconclusive:twice', 1)", (site,))
    c = by_no(a.read(monitor.checks))["7"]
    assert any("t:un" in x and "jscheck --set" in x for x in c.lines)


def test_item_8_compares_with_the_platform_file_as_it_is_with_each_buttons_text(a):
    site = "sso.nknu.edu.tw/news"
    home = a.site(site)
    # SSO's bare ctlNN 「查詢」 is never pressed; without its text the rule does not hold (design §10.4 rule 9)
    assert profiles.decision(site, "postback:ctl*", "查詢") == "never"
    assert profiles.decision(site, "postback:ctl*", None) == "unknown"
    a.res(home, "postback:ctl*", how="unhandled", role="click", need="no", why="unhandled:never", text="查詢")
    a.res(home, "postback:gvFull|Page$2", how="unhandled", role="click", why="unhandled:unpressed")
    assert by_no(a.read(monitor.checks))["8"].status == monitor.PASS
    # written while the platform file said nothing; it says never now, and no rejudge has run since
    a.res(home, "select:ddlCategory", how="unhandled", role="click", need="must", why="unhandled:unknown")
    c = by_no(a.read(monitor.checks))["8"]
    assert c.status == monitor.FAIL
    assert any("select:ddlCategory" in x and "还没 rejudge" in x for x in c.lines)
    assert "和现在的平台档案对不上 1 处" in c.detail


def test_item_14_reads_every_answer_back_by_its_position(a):
    site = good_archive(a)
    a.close()
    assert by_no(monitor.verify(a.root, resource_check=no_problems).checks)["14"].status == monitor.PASS
    a.f = Frontier(a.root / "frontier.db")
    a.sink = None
    a.f.x("UPDATE url SET sha256=? WHERE url=?", ("0" * 64, f"https://{site}/a.pdf"))
    a.url(site, "/nowhere", fetched_at=T0)  # came back, and nothing says where its answer is
    broken = a.root / "warc" / "2026" / "10" / "26" / "cut.warc.gz"
    whole = gzip.compress(bytes(random.Random(1).randrange(256) for _ in range(4000)))
    broken.write_bytes(whole[:len(whole) // 2])  # a gzip member cut in half
    a.close()
    c = by_no(monitor.verify(a.root, resource_check=no_problems).checks)["14"]
    assert c.status == monitor.FAIL
    assert any("sha256" in x and "a.pdf" in x for x in c.lines)
    assert any("没有存档位置" in x and "/nowhere" in x for x in c.lines)
    assert any("读不完" in x and "cut.warc.gz" in x for x in c.lines)


def test_item_14_waits_while_a_run_holds_the_lock(a):
    good_archive(a)
    a.close()
    cs = by_no(monitor.checks(Frontier(a.root / "frontier.db", readonly=True), root=a.root, alive=True))
    assert cs["14"].status == monitor.SKIP and "爬虫在跑" in cs["14"].lines[0]


def test_item_11_audits_politeness_by_the_servers_address(a):
    site = good_archive(a)
    t = time.time()
    a.request(f"https://{site}/x", t, t + 2, ip="140.127.62.110", host="c.nknu.edu.tw")
    a.request(f"https://{site}/y", t + 1, t + 3, ip="140.127.62.110", host="tecs.nknu.edu.tw")  # another name, one server
    c = by_no(a.read(monitor.checks))["11"]
    assert c.status == monitor.FAIL and any("并发" in x and "140.127.62.110" in x for x in c.lines)


def test_item_18_every_site_reconciled_newer_than_its_last_crawl(a):
    a.event("run_end", T1)
    for s in ("none.nknu.edu.tw", "old.nknu.edu.tw", "fail.nknu.edu.tw", "low.nknu.edu.tw"):
        a.site(s)
    a.reconcile("old.nknu.edu.tw", T0)
    a.reconcile("fail.nknu.edu.tw", T2, gates_ok=0, R7="fail")
    a.reconcile("low.nknu.edu.tw", T2, cover_cc=0.5)
    a.url("low.nknu.edu.tw", "/cc-only", found_via="commoncrawl")
    a.event("rejudge", T3, sites=["old.nknu.edu.tw"])
    c = by_no(a.read(monitor.checks))["18"]
    assert c.status == monitor.FAIL
    lines = "\n".join(c.lines)
    assert "none.nknu.edu.tw：还没对账" in lines and "old.nknu.edu.tw：对账" in lines and "R7" in lines
    assert "low.nknu.edu.tw：Common Crawl 覆盖率估计 50%" in lines and "/cc-only" in lines
    assert "low.nknu.edu.tw：对账" not in lines  # a low coverage is a warning, never a failed gate
    only_low = by_no(monitor.checks(_only(a, "low.nknu.edu.tw")))["18"]
    assert only_low.status == monitor.WARN


def _only(a: Arch, site: str) -> Frontier:
    """A read-only frontier over a copy of a's database that keeps only one site in the list."""
    copy = a.root / f"only-{next(_n)}.db"
    with sqlite3.connect(a.root / "frontier.db") as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    with sqlite3.connect(copy) as c:
        c.execute("DELETE FROM site_plan WHERE site <> ?", (site,))
    return Frontier(copy, readonly=True)


def test_item_16_lists_the_known_gaps_with_their_numbers(a):
    h = a.site("g.nknu.edu.tw")
    a.res(h, "click:loadX(3)", role="click", need="round2", how="click", why="round2:click")
    a.res(h, "select:#year", role="click", need="round2", how="unhandled", why="round2:unhandled")
    a.res(h, "google:https://docs.google.com/document/d/e/2PACX-1/pub", role="drive", need="round2",
          how="link_only", why="round2:google_published")
    a.file(h, "g.nknu.edu.tw", "/in.pdf", state="pending", reason="host_unreachable", status=None)
    a.url("x.example.org", "/one", owner_site="g.nknu.edu.tw", state="later", reason="round2:offsite", status=None,
          tier=2)
    c = by_no(a.read(monitor.checks))["16"]
    text = "\n".join(c.lines)
    assert c.status == monitor.WARN and monitor.WAYBACK_GAP in text
    for words in ("只限校内网络的网址，等搬到实验室：1 个", "点了才载入内容的网页（分页签、下载按钮），下一轮补：1 页",
                  "外部函数……），下一轮补：1 种", "校外一层，下一轮：1 个", "等你决定怎么读：1 个",
                  "要 JavaScript 的网页，下一轮补：0 页"):
        assert words in text, words


def test_item_15_samples_for_a_person_and_item_6_counts_what_may_be_wrong(a):
    a.event("run_end", T1)
    h = a.site("s.nknu.edu.tw")
    a.url("s.nknu.edu.tw", "/empty", flags="thin")
    a.url("s.nknu.edu.tw", "/gone-ish", flags="soft404")
    a.file(h, "s.nknu.edu.tw", "/x.doc", sniff="html")
    a.res(h, "https://s.nknu.edu.tw/banner.png", role="img", need="no", why="decor:repeat")
    cs = by_no(a.read(monitor.checks))
    assert cs["6"].status == monitor.FAIL and "页面几乎是空的 1、看起来像不存在 1、应该是档案却回来网页 1" in cs["6"].detail
    assert any("还没对账" in x for x in cs["6"].lines)  # thin is judged per batch: that part is 没检查
    assert cs["15"].status == monitor.WARN
    assert any("/empty" in x for x in cs["15"].lines) and any("decor:repeat" in x for x in cs["15"].lines)


def test_item_15_spreads_its_sample_over_the_whole_set(a):
    a.site("m.nknu.edu.tw")
    for i in range(25):
        a.url("m.nknu.edu.tw", f"/e{i:02d}", flags="thin")
    thin = [x for x in by_no(a.read(monitor.checks))["15"].lines if "几乎是空的" in x]
    assert len(set(thin)) == monitor.SAMPLE and "/e00" in thin[0] and not any("/e01（" in x for x in thin)


def test_item_12_follows_v1s_notices_through_their_redirects(a):
    site = "sso.nknu.edu.tw"
    a.site(site)
    a.url(site, "/a", status=302, final_url=f"https://{site}/b")
    a.url(site, "/b")
    a.url(site, "/c", state="failed", reason="timeout", status=None)
    for p in ("/a", "/c", "/d"):
        a.f.seed_src(f"https://{site}{p}", "v1_known")
    c = by_no(a.read(monitor.checks))["12"]
    assert c.status == monitor.FAIL and "v1 已知的公告 3 则：抓到或学校那边已删 1，还在等 0，有问题 2" in c.detail


def test_item_13_reads_the_reconciliations_until_reconcile_gives_its_function(a):
    a.event("run_end", T1)
    a.site("a.nknu.edu.tw")
    a.site("b.nknu.edu.tw")
    a.reconcile("a.nknu.edu.tw", T2)
    a.reconcile("b.nknu.edu.tw", T2, gates_ok=0, V13="fail")
    c = by_no(a.read(monitor.checks))["13"]
    assert c.status == monitor.FAIL and any("b.nknu.edu.tw" in x and "V13" in x for x in c.lines)
    c = by_no(a.read(monitor.checks, resource_check=lambda f, root: ["page 1: img x 没有 page_res"]))["13"]
    assert c.status == monitor.FAIL and c.lines == ["page 1: img x 没有 page_res"]


def test_golden_sources_must_be_listed_complete_and_quoted(a, tmp_path):
    site = good_archive(a)
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({"webAnswers": [
        {"id": "Q1", "sources": [{"url": f"https://{site}/", "quote": "115 學年度第 1 學期行事曆；已公告"}]},
        {"id": "Q2", "sources": [{"url": f"https://{site}/", "quote": "這一句不在頁面上的引文"}]},
        {"id": "Q3", "sources": [{"url": "https://other.nknu.edu.tw/x", "quote": ""}]}]}), encoding="utf-8")
    a.close()
    c = by_no(monitor.verify(a.root, golden=golden, resource_check=no_problems).checks)["G"]
    assert c.status == monitor.FAIL and "都对得上的 1 个" in c.detail
    assert any(x.startswith("Q2：") and "找不到" in x for x in c.lines)
    assert any(x.startswith("Q3：") and "不在清单里" in x for x in c.lines)


# ---------------------------------------------------------------- viewer.locate (design §12)


def test_the_viewer_finds_a_rows_own_record_not_a_press_at_the_same_address(a):
    from nknu.archive.viewer import Viewer, build_index

    site = "c.nknu.edu.tw/reality"
    uid = a.site(site)
    a.archive(uid, b"<p>the list as GET</p>")
    url = f"https://{site}/"
    a.sink.write(Snapshot(Target(url, method="POST", data=[("__EVENTTARGET", "gvFull")]), url, 200,
                          {"content-type": "text/html"}, b"<p>page 2 by a press</p>", None, datetime.now(UTC), "s"), {})
    other = a.url(site, "/old.html")  # a row with no position: by address, as for the 10-24 archive
    a.f.x("UPDATE url SET ctype='text/html', final_url=url WHERE id=?", (other,))
    a.sink.write(Snapshot(Target(f"https://{site}/old.html"), f"https://{site}/old.html", 200,
                          {"content-type": "text/html"}, b"<p>old</p>", None, datetime.now(UTC), "s"), {})
    a.sink.close()
    a.sink = None
    build_index(a.root)
    v = Viewer(a.root)
    assert v.body_of(uid)[1] == b"<p>the list as GET</p>"  # the index alone would give the newest: the press
    assert v.body_of(other)[1] == b"<p>old</p>"
