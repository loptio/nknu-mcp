"""Each site's state (design §10.5: monitor.site_status) on small made-up frontiers: every state a site can be in
(✗ 出错, ⚠ 要看, 还在抓, ✓ 爬完, ⚠ 没检查), its problem list, requests made on campus, the politeness audit, where a
start's redirects land, the dashboard's way of asking, and viewer.locate by the row's own record. The acceptance
checks and `verify` that this file was first written for went with the judgment-layer cut of 11-19, and with them the
reconciliation a site needed to be 爬完: every page whole and nothing waiting is 爬完. Nothing here connects anywhere;
every address is made up, and no fixture holds a name."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nknu.archive import monitor, outcome
from nknu.archive.fetch import Snapshot
from nknu.archive.frontier import Frontier
from nknu.archive.target import Target
from nknu.archive.warc import WarcSink

T0, T1, T2, T3, T4, T5 = (f"2026-10-26T0{h}:00:00+00:00" for h in range(6))


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


# ---------------------------------------------------------------- §10.5: each site's state


def test_every_state_a_site_can_be_in(a):
    a.event("run_end", T1, reason="finished", sites=None)
    home = a.site("done.nknu.edu.tw")
    a.file(home, "done.nknu.edu.tw", "/a.pdf")
    a.site("norec.nknu.edu.tw")  # every page complete and never reconciled: 爬完 (the cut of 11-19)
    bad = a.site("bad.nknu.edu.tw")
    a.file(bad, "bad.nknu.edu.tw", "/x.pdf", state="failed", reason="timeout", status=None)
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:js', 'js.nknu.edu.tw', 'js', 1)")
    a.site("js.nknu.edu.tw", page_type="t:js")
    a.site("wait.nknu.edu.tw", state="pending", status=None, fetched_at=None)
    got = a.read(monitor.site_status)
    assert {k: v.state for k, v in got.items()} == {
        "done.nknu.edu.tw": monitor.DONE, "norec.nknu.edu.tw": monitor.DONE,
        "bad.nknu.edu.tw": monitor.ERROR, "js.nknu.edu.tw": monitor.LOOK, "wait.nknu.edu.tw": monitor.CRAWLING}
    assert got["norec.nknu.edu.tw"].why == ["每一页都完整，没有还在等的"]
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
    # a file of a school unit's own site off campus, left for round two (the one file off campus a school page links,
    # round2:offsite_file, is the owner's 「全部不抓」 since 11-17: below)
    far = a.url("x.example.org", "/far.pdf", owner_site="far.nknu.edu.tw", kind="file", kind0="file", page_type=None,
                state="later", reason="round2:offsite_unit", status=None, tier=2)
    a.res(h2, "https://x.example.org/far.pdf", res_id=far)
    h3 = a.site("off.nknu.edu.tw")
    off = a.url("y.example.org", "/off.pdf", owner_site="off.nknu.edu.tw", kind="file", kind0="file", page_type=None,
                state="later", reason="round2:offsite_file", status=None, tier=2)
    a.res(h3, "https://y.example.org/off.pdf", res_id=off)
    a.site("own2.nknu.edu.tw")
    a.url("own2.nknu.edu.tw", "/old/1", state="later", reason="round2:other_site", status=None, tier=2)
    got = a.read(monitor.site_status)
    assert got["lab.nknu.edu.tw"].state == monitor.LOOK and "实验室" in got["lab.nknu.edu.tw"].why[0]
    assert got["far.nknu.edu.tw"].state == monitor.LOOK and "下一轮" in got["far.nknu.edu.tw"].why[0]
    assert got["own2.nknu.edu.tw"].state == monitor.DONE  # its round-two address is no round-1 gap (§10.5)
    assert not any("下一轮" in w for w in got["off.nknu.edu.tw"].why)  # the rule's: nothing waits for round two


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


def test_a_persons_mark_takes_a_resource_off_the_list(a):
    a.event("run_end", T1)
    h = a.site("mark.nknu.edu.tw")
    pdf = a.file(h, "mark.nknu.edu.tw", "/home.pdf", sniff="html")  # an attachment that came back as a web page
    assert a.states()["mark.nknu.edu.tw"] == monitor.ERROR
    a.f.x("INSERT INTO review_mark(url_id, mark, result, reason, status, note, at, fetched_at) VALUES "
          "(?, 'school_side', 'not_file', NULL, 200, '学校的链接指错', ?, ?)", (pdf, T2, T0))
    got = a.read(monitor.site_status)
    assert got["mark.nknu.edu.tw"].state == monitor.DONE and not got["mark.nknu.edu.tw"].issues
    assert got["mark.nknu.edu.tw"].buckets[outcome.SCHOOL_SIDE] == 1


def test_no_resource_layer_is_not_checked_never_green(a):
    a.event("run_end", T1)
    a.site("bare.nknu.edu.tw", bare=True)
    a.site("redbare.nknu.edu.tw", bare=True, flags="login")
    got = a.read(monitor.site_status)
    assert got["bare.nknu.edu.tw"].state == monitor.NOT_CHECKED and "资源层" in got["bare.nknu.edu.tw"].why[0]
    assert got["redbare.nknu.edu.tw"].state == monitor.ERROR  # what is wrong stays red


def test_requests_made_on_campus_are_a_warning_beside_the_state(a):
    site = "c.nknu.edu.tw/aca"
    h = a.site(site)
    a.file(h, site, "/a.pdf")
    a.event("run_end", T1)
    t = time.time()
    a.request(f"https://{site}/", t, t + 0.5, site=site, campus="on")
    a.request(f"https://{site}/a.pdf", t + 4, t + 4.5, site=site, campus="off")
    st = a.read(monitor.site_status)[site]
    assert st.state == monitor.DONE and any("校内网络" in w for w in st.warnings)
    campus = a.read(monitor.campus_requests)  # counted by the site each was made for
    assert (campus.on, campus.unknown, dict(campus.by_site)) == (1, 0, {site: 1})
    text, _code = monitor.report(a.root)
    assert "在校内网络抓的请求 1 个" in text


def test_a_site_is_never_done_while_a_page_is_not_complete():
    """The one promise §10.5 makes (record 二·5): 爬完 only when every round-1 page is complete. Random sites, pages,
    resources and walks; seeded, so a failure can be repeated."""
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
        got = arch.read(monitor.facts)
        sts = arch.read(monitor.site_status)
        arch.close()
    by_site = {}
    for p in got.pages:
        by_site.setdefault(p.site, []).append(p.level)
    seen = set()
    for s, st in sts.items():
        seen.add(st.state)
        if st.state == monitor.DONE:
            assert all(lv in ("complete", "complete_school") for lv in by_site.get(s, [])), (s, st.why)
    assert {monitor.ERROR, monitor.LOOK, monitor.CRAWLING, monitor.DONE} <= seen  # the grid reaches every state


def test_the_dashboard_may_ask_with_its_own_connection_and_its_pages(a):
    """progress.slow_snapshot's way (its read-only connection, its subset of page_completeness's columns): the same
    states, and each reads as a mapping (state, why, warnings …)."""
    a.event("run_end", T1)
    a.walk(a.site("l1.nknu.edu.tw", page_role="list"), "mismatch:page", 0)
    h = a.site("ok.nknu.edu.tw")
    a.file(h, "ok.nknu.edu.tw", "/a.pdf")
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
        assert (d["state"], d["label"], d["why"], d["warnings"]) == (monitor.DONE, "✓ 爬完", ["每一页都完整，没有还在等的"], [])
        bad = dict(got["l1.nknu.edu.tw"])
        assert bad["state"] == monitor.ERROR and any("列表没翻完" in x for x in bad["issues"])
    finally:
        conn.close()


# ---------------------------------------------------------------- what the acceptance items looked at, asked directly


def test_a_type_not_judged_waits_for_jscheck_and_only_types_with_pages_count(a):
    site = "t.nknu.edu.tw"
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:todo', ?, 'todo', 1)", (site,))
    a.f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:old', ?, 'todo', 0)", (site,))
    a.site(site, page_type="t:todo")
    a.event("run_end", T1)
    st = a.read(monitor.site_status)[site]
    # the old type no page of this round has is not counted: 1 类, never 2
    assert st.state == monitor.LOOK and st.why == ["1 类有网页的类型还没判定（jscheck 还没跑完）"]
    a.event("jscheck_end", T2, reason="finished")
    st = a.read(monitor.site_status)[site]
    assert st.state == monitor.ERROR and st.why == ["1 类有网页的类型还没判定（jscheck 已经跑完）"]


def test_politeness_is_audited_by_the_servers_address(a):
    site = "good.nknu.edu.tw"
    a.site(site)
    t = time.time() - 60
    a.request(f"https://{site}/", t, t + 0.5)
    a.request(f"https://{site}/a.pdf", t + 4, t + 4.5)
    assert a.read(monitor.politeness) == []  # one at a time, four seconds apart
    t = time.time()
    a.request(f"https://{site}/x", t, t + 2, ip="140.127.62.110", host="c.nknu.edu.tw")
    a.request(f"https://{site}/y", t + 1, t + 3, ip="140.127.62.110", host="tecs.nknu.edu.tw")  # another name, one server
    hard = a.read(monitor.politeness)
    assert len(hard) == 1 and "并发" in hard[0] and "140.127.62.110" in hard[0]


def test_v1s_notices_are_followed_through_their_redirects(a):
    site = "sso.nknu.edu.tw"
    a.site(site)
    a.url(site, "/a", status=302, final_url=f"https://{site}/b")
    a.url(site, "/b")
    a.url(site, "/c", state="failed", reason="timeout", status=None)
    for p in ("/a", "/c", "/d"):
        a.f.seed_src(f"https://{site}{p}", "v1_known")
    got = {}
    for r in a.f.q("SELECT s.url, u.id FROM seed_src s LEFT JOIN url u ON u.url = s.url WHERE s.source = 'v1_known'"):
        end, nowhere = a.read(monitor.landing, r["id"]) if r["id"] is not None else (None, "no row")
        got[r["url"].rsplit("/", 1)[1]] = (end["url"] if end is not None else None,
                                           end["result"] if end is not None else None, nowhere)
    # stored where its redirect lands; a failure is its own; one never met has no row at all
    assert got == {"a": (f"https://{site}/b", "ok", None), "c": (f"https://{site}/c", "failed", None),
                   "d": (None, None, "no row")}


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
