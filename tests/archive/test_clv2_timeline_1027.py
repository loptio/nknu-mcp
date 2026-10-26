"""SSO cLv2 unit sites' timeline (/Unit/Info/ajaxServices/ajaxTimeline.aspx), design §23 C, 【用户同意 Claude 的建议】
(10-27, 「可以，听你的」): round 1 reads it itself, once per site, with the request the page's own script sends
(getTimeLine() → json() in ajaxAnalysis.js: a jQuery POST of JSON.stringify({cFunction: "getTimelineJson", pLv2:
cLv2})), on the crawler's own queue; an empty timeline is ok (「时间轴是空的」), its events' links are taken in as the
site's home page's, and the browser check (jscheck, reconcile's R6) no longer judges a cLv2 page js for it: a POST
the platform file reads is no data_post, nor is another server's.

The page is the 10-26 pilot's (archive-pilot/, read only: sso.nknu.edu.tw/site/100061, index.aspx?clv2=100061) cut
down to its script, the cLv2 value, its menu and the timeline box (tests/fixtures/archive_postback/
clv2_timeline_home.html; no person's name, the form state a placeholder); json()'s lines are ajaxAnalysis.js's as
archived (ajaxanalysis_json.js). The timeline's answers are made up in the two shapes json() reads. All offline."""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from nknu.archive import jscheck, profiles, reconcile
from nknu.archive.crawler import Crawler
from nknu.crawl.warc import WarcSink
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap

FX = Path(__file__).resolve().parents[1] / "fixtures" / "archive_postback"
SITE = "sso.nknu.edu.tw/site/100061"
HOME = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=100061"
P4 = f"{HOME}&page=4"
P7 = f"{HOME}&page=7"
API = "https://sso.nknu.edu.tw/Unit/Info/ajaxServices/ajaxTimeline.aspx"
SSO_Q = "ip:140.127.40.173"
EMPTY_NEW = {"bErrOn": False, "cMessage": "", "oData": [{"events": []}]}
EMPTY_OLD = {"result": {"bErrOn": "false", "cMessage": ""}, "data": [{"events": {}}]}


def page() -> str:
    return (FX / "clv2_timeline_home.html").read_text(encoding="utf-8")


def answer(data, status=200, ctype="application/json; charset=utf-8"):
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
    return lambda target: snap(target.url, status, body=body, headers={"content-type": ctype})


class Net:
    """SSO's server: a GET of a cLv2 page answers the cut-down page, a POST to the timeline what the test gives;
    every request is kept with its method, address, headers and body, and archived in the worker's WARC file."""

    def __init__(self, clock, timeline, pages=None):
        self.clock, self.timeline = clock, timeline
        self.pages = pages if pages is not None else {HOME: page(), P4: page(), P7: page()}
        self.seen: list[tuple[str, str, dict, bytes | None]] = []
        self.sink: WarcSink | None = None

    def __call__(self, _warc):
        return self

    def reset_host(self, host):
        pass

    def close(self):
        pass

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        self.seen.append((target.method, target.url, dict(target.headers), target.body))
        self.clock.t += 1.0
        if target.method == "POST" and target.url == API:
            got = self.timeline(target)
        elif target.method == "GET" and target.url in self.pages:
            got = snap(target.url, html=self.pages[target.url])
        else:
            got = snap(target.url, 404)
        if self.sink is not None:
            offset, length = self.sink.write(got, dict(target.headers))
            got = replace(got, warc_offset=offset, warc_len=length)
        return got

    def posts(self) -> list[tuple[str, dict, bytes | None]]:
        return [(u, h, b) for m, u, h, b in self.seen if m == "POST"]


def make(tmp_path, net):
    return Crawler(tmp_path, plan=OpenPlan(), clock=net.clock, sleep=net.clock.sleep, client_factory=net,
                   resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                   netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)


def work(crawler, net, queue=SSO_Q, rounds=60):
    """One queue's worker without its thread: a press before the queue's rows, as Crawler._work does."""
    sink = WarcSink(crawler.root / "warc", "q", 1, datetime(2026, 10, 27, tzinfo=UTC))
    net.sink = sink
    for _ in range(rounds):
        press = crawler.chains.next_action(queue, crawler.clock(), crawler.sites)
        if press is not None:
            crawler._press(queue, net, sink, press)
            continue
        row = crawler._next(queue)
        if row is None:
            return
        crawler._handle(queue, net, sink, row)


def data_entries(f):
    return f.q("SELECT u.url, pr.* FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE pr.role = 'data' "
               "ORDER BY u.id")


# ---------------------------------------------------------------- C1: the request is the page's own


def test_the_timeline_request_is_what_the_pages_own_script_sends():
    html = page()
    reads = profiles.data_api_reads(SITE, html)
    assert [(api.name, value) for api, value in reads] == [("sso_clv2_timeline", "100061")]
    api, value = reads[0]
    assert api.url == API
    # the body: JSON.stringify of getTimeLine()'s object literal, its members in the script's order, no spaces
    lit = re.search(r"var oOption = \{(.*?)\};", html, re.DOTALL)[1]
    members = re.findall(r"(\w+)\s*:\s*(\"[^\"]*\"|\w+)", lit)
    want = {k: (json.loads(v) if v.startswith('"') else {"cLv2": value}[v]) for k, v in members}
    assert profiles.data_api_body(api, value) == json.dumps(want, separators=(",", ":")).encode()
    assert profiles.data_api_body(api, value) == b'{"cFunction":"getTimelineJson","pLv2":"100061"}'
    # json() in ajaxAnalysis.js: a POST, contentType application/json, dataType 'json' (jQuery's Accept for it)
    js = (FX / "ajaxanalysis_json.js").read_text(encoding="utf-8")
    assert 'type: "POST"' in js and 'contentType: "application/json"' in js and "dataType: 'json'" in js
    assert dict(api.headers) == {"Content-Type": "application/json",
                                 "Accept": "application/json, text/javascript, */*; q=0.01"}
    assert profiles.data_api_reads("sso.nknu.edu.tw/web/595", html) == []  # cLv2's platform file only
    assert profiles.data_api_reads(SITE, "<p>no timeline here</p>") == []


def test_a_clv2_site_reads_its_timeline_once_for_all_its_pages(tmp_path):
    clock = Clock()
    net = Net(clock, answer(EMPTY_NEW))
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    assert {u for _m, u, _h, _b in net.seen if u in (HOME, P4, P7)} == {HOME, P4, P7}  # three pages read
    posts = net.posts()
    assert len(posts) == 1
    url, headers, body = posts[0]
    assert url == API and body == b'{"cFunction":"getTimelineJson","pLv2":"100061"}'
    assert headers == {"Content-Type": "application/json", "Accept": "application/json, text/javascript, */*; q=0.01"}
    reqs = f.q("SELECT * FROM request WHERE url=?", (API,))
    assert len(reqs) == 1 and reqs[0]["purpose"] == "data_api" and reqs[0]["queue"] == SSO_Q
    a = f.q("SELECT * FROM action_result WHERE kind='data'")
    assert len(a) == 1 and (a[0]["result"], a[0]["reason"], a[0]["row_key"]) == ("ok", "read:empty",
                                                                                 "sso_clv2_timeline:100061")
    assert a[0]["warc"] is not None and a[0]["warc_offset"] is not None
    got = data_entries(f)
    assert {r["url"] for r in got} == {HOME, P4, P7}
    assert {(r["res_key"], r["need"], r["action_id"]) for r in got} == {
        ("data:sso_clv2_timeline:100061", "must", a[0]["id"])}
    res = f.q("SELECT DISTINCT result FROM page_resource WHERE res_key='data:sso_clv2_timeline:100061'")
    assert [r[0] for r in res] == ["ok"]
    gaps = f.q("SELECT started, ended FROM request ORDER BY started")
    assert all(b["started"] - a_["ended"] >= 2.0 for a_, b in itertools.pairwise(gaps))  # its queue's gap
    # the GET of the page again reads the same timeline: still one action, one POST
    f.x("UPDATE url SET state='pending', next_at=0 WHERE url=?", (P4,))
    work(crawler, net)
    assert len(net.posts()) == 1 and f.q("SELECT count(*) FROM action WHERE kind='data'")[0][0] == 1


def test_the_older_answer_shape_reads_the_same(tmp_path):
    clock = Clock()
    net = Net(clock, answer(EMPTY_OLD), pages={HOME: page()})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net)
    a = crawler.frontier.q("SELECT result, reason FROM action_result WHERE kind='data'")
    assert [tuple(r) for r in a] == [("ok", "read:empty")]


def test_a_timeline_with_events_brings_their_links_in_as_the_home_pages(tmp_path):
    events = {"bErrOn": False, "cMessage": "", "oData": [{"events": [
        {"start_date": {"year": "2026"}, "text": {"headline": "交通安全週",
                                                   "text": '<p>詳見<a href="/News/x.aspx?UNID=AB12">公告</a></p>'},
         "media": {"url": "https://c.nknu.edu.tw/affair/uploadfile/plan.pdf"},
         "background": {"url": "https://sso.nknu.edu.tw/Upload/Timeline/bg.jpg"}}]}]}
    clock = Clock()
    net = Net(clock, answer(events), pages={HOME: page()})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=3)
    f = crawler.frontier
    a = f.q("SELECT result, reason FROM action_result WHERE kind='data'")[0]
    assert (a["result"], a["reason"]) == ("ok", "read:events:1")
    home = f.row(HOME)
    targets = {r["dst"] for r in f.q("SELECT dst FROM link WHERE src=?", (home["id"],))}
    assert {"https://c.nknu.edu.tw/affair/uploadfile/plan.pdf",
            "https://sso.nknu.edu.tw/news/x.aspx?unid=ab12"} <= {t.lower() for t in targets}  # links, as any page's
    keys = {r["res_key"].lower(): r for r in f.q("SELECT * FROM page_res WHERE page_id=?", (home["id"],))}
    assert keys["https://c.nknu.edu.tw/affair/uploadfile/plan.pdf"]["role"] == "file"
    assert keys["https://sso.nknu.edu.tw/upload/timeline/bg.jpg"]["role"] == "img"
    assert f.row("https://c.nknu.edu.tw/affair/uploadfile/plan.pdf") is not None  # queued as the page's attachment
    # the home read again writes its page_res anew: what the timeline brought is read back from the archive, never
    # lost, and the timeline is not asked again
    f.x("UPDATE url SET state='pending', next_at=0 WHERE url=?", (HOME,))
    work(crawler, net, rounds=1)
    keys = {r["res_key"].lower() for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (home["id"],))}
    assert {"https://c.nknu.edu.tw/affair/uploadfile/plan.pdf", "https://sso.nknu.edu.tw/upload/timeline/bg.jpg",
            "data:sso_clv2_timeline:100061"} <= keys
    assert len(net.posts()) == 1


def test_what_is_no_timeline_is_never_ok(tmp_path):
    for i, (data, ctype, want) in enumerate([
        ({"bErrOn": True, "cMessage": "系統忙碌", "oData": None}, "application/json", ("server_error", "school_error:")),
        ({"result": {"bErrOn": "true", "cMessage": "錯誤"}, "data": None}, "application/json",
         ("server_error", "school_error:")),
        (b"<html><body>Runtime Error</body></html>", "text/html", ("mismatch", "not_json")),
        ({"bErrOn": False, "oData": [{"title": "x"}]}, "application/json", ("mismatch", "shape")),
    ]):
        clock = Clock()
        net = Net(clock, answer(data, ctype=ctype), pages={HOME: page()})
        crawler = make(tmp_path / str(i), net)
        seed(crawler, HOME)
        work(crawler, net, rounds=3)
        a = crawler.frontier.q("SELECT result, reason FROM action_result WHERE kind='data'")[0]
        assert a["result"] == want[0] and (a["reason"] or "").startswith(want[1]), (i, dict(a))


def test_a_failed_read_is_tried_again_later_with_a_new_request_never_given_up_at_once(tmp_path):
    clock = Clock()
    net = Net(clock, answer({"x": 1}, status=500, ctype="text/html"), pages={HOME: page()})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=3)
    f = crawler.frontier
    a = f.q("SELECT * FROM action WHERE kind='data'")[0]
    assert (a["state"], a["attempts"]) == ("pending", 1) and a["next_at"] > clock()
    assert crawler.chains.next_action(SSO_Q, clock(), None) is None  # not before its time
    for _ in range(3):
        clock.t = f.q("SELECT next_at FROM action WHERE kind='data'")[0][0] + 1
        work(crawler, net, rounds=3)
    a = f.q("SELECT * FROM action_result WHERE kind='data'")[0]
    assert (a["state"], a["result"]) == ("failed", "failed") and len(net.posts()) == 3
    assert f.q("SELECT count(*) FROM action WHERE kind='data'")[0][0] == 1  # one row, never a chain's restart


def test_a_page_whose_clv2_cannot_be_read_sends_nothing_and_says_so(tmp_path):
    clock = Clock()
    html = page().replace("<script>var cLv2 = '100061';</script>", "")
    net = Net(clock, answer(EMPTY_NEW), pages={HOME: html})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=3)
    assert net.posts() == []
    got = data_entries(crawler.frontier)
    assert [(r["need"], r["how"], r["why"]) for r in got] == [("must", "unhandled", "unhandled:data_api_value")]
    res = crawler.frontier.q("SELECT result FROM page_resource WHERE role='data'")
    assert [r[0] for r in res] == ["unhandled"]  # 要人看, never fetched


def test_a_left_active_read_goes_back_to_pending_never_a_chains_restart(tmp_path):
    clock = Clock()
    net = Net(clock, answer(EMPTY_NEW), pages={HOME: page()})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=1)
    f = crawler.frontier
    f.x("UPDATE action SET state='active' WHERE kind='data'")
    assert crawler.chains.resume() == 0
    assert f.q("SELECT state FROM action WHERE kind='data'")[0][0] == "pending"
    crawler.chains.restart_chain(f.row(HOME)["id"], "test")  # the list's own restart leaves the read alone
    assert f.q("SELECT state FROM action WHERE kind='data'")[0][0] == "pending"


# ---------------------------------------------------------------- C2: the browser check


def _obs(url, log):
    return jscheck.Observation(url, url, "<html><body>" + "消息" * 200 + "</body></html>", log=log)


def test_the_browser_check_never_makes_a_clv2_page_js_for_the_timeline_it_reads():
    own = jscheck.Exchange(HOME, "document", "GET", True, 200, "<html><body>" + "消息" * 200 + "</body></html>")
    timeline = jscheck.Exchange(API, "xhr", "POST", False, unsent="post_body")
    v = jscheck.judge(_obs(HOME, [own, timeline]))
    assert (v.verdict, v.method) == ("ok", "direct")
    assert jscheck.incomplete(_obs(HOME, [own, timeline])) is None
    assert not jscheck.data_post(timeline, HOME)
    # another data address of the same server the platform file does not read: still the type's (js, data_post)
    other = jscheck.Exchange("https://sso.nknu.edu.tw/Unit/Info/ajaxServices/ajaxOther.aspx", "xhr", "POST", False,
                             unsent="post_body")
    assert jscheck.data_post(other, HOME)
    assert (jscheck.judge(_obs(HOME, [own, other])).verdict, jscheck.judge(_obs(HOME, [own, other])).method) == (
        "js", "data_post")
    # another server's POST is no data_post: §8.4's rule (a request the page needed and the check did not send)
    away = jscheck.Exchange("https://api.example.org/collect", "xhr", "POST", False, unsent="post_body")
    assert not jscheck.data_post(away, HOME)
    assert jscheck.judge(_obs(HOME, [own, away])).why == "inconclusive:request_failed"
    # the same path on a page of another platform is no read of cLv2's
    assert not profiles.known_data_api("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=595", API)
    assert profiles.known_data_api(HOME, API.lower())


def test_r6_keeps_a_clv2_page_ok_for_its_timeline_and_never_overrides_an_imported_or_a_persons_verdict(tmp_path):
    from nknu.archive.frontier import Frontier

    f = Frontier(tmp_path / "frontier.db")
    for i, source in enumerate(("import", "manual", "jscheck")):
        key = f"t{i}"
        f.x("INSERT INTO page_type(key, site, verdict, source) VALUES (?, ?, 'ok', ?)", (key, SITE, source))
        f.add(f"{HOME}&page={i}", host="sso.nknu.edu.tw", site=SITE, unit=None, tier=1, kind="page", depth=0,
              found_via="seed", found_from=None, owner_site=SITE)
        f.x("UPDATE url SET page_type=? WHERE url=?", (key, f"{HOME}&page={i}"))
    other = "https://sso.nknu.edu.tw/Unit/Info/ajaxServices/ajaxOther.aspx"
    for i in range(3):
        reconcile._type_data_post(f, f.row(f"{HOME}&page={i}"), (other,))
    got = {r["key"]: (r["verdict"], r["source"]) for r in f.q("SELECT key, verdict, source FROM page_type")}
    assert got == {"t0": ("ok", "import"), "t1": ("ok", "manual"), "t2": ("js", "spotcheck")}
    assert reconcile._data_posts(f.row(f"{HOME}&page=2"), (API, other)) == (other,)  # the timeline is read: no post
    f.close()


def test_r6_never_judges_a_page_js_for_a_data_address_its_platform_file_reads(tmp_path, monkeypatch):
    """R6 sees what the browser asked by a POST (SpotView.data_posts); one the platform file reads this round (here
    the test site's own timeline address, taken for one as cLv2's is) is no data_post: the type stays what it was."""
    from nknu.archive.reconcile import PASS, Sources, SpotView
    from tests.archive.test_reconcile import PLAN, W, _clean_site, _ok_types, _same_spot
    from tests.archive.test_reconcile import SITE as W_SITE

    c, net = _clean_site(tmp_path)
    fr = c.frontier
    _ok_types(fr)
    same = _same_spot(tmp_path)
    timeline = f"{W}/Unit/Info/ajaxServices/ajaxTimeline.aspx"
    about = fr.row(f"{W}/about.html")
    fr.x("UPDATE page_type SET source='jscheck' WHERE key=?", (about["page_type"],))
    monkeypatch.setattr(profiles, "known_data_api", lambda page_url, request_url: request_url == timeline)

    def spot(url):
        view = same(url)
        return SpotView(view.url, view.final_url, view.html, view.screenshot, data_posts=(timeline,)) \
            if url == about["url"] else view

    res = reconcile.reconcile_sites(fr, tmp_path, [W_SITE], batch="clv2", plan=PLAN, sources=Sources(cc_dir=None),
                                    spot=spot).sites[0]
    r6 = res.checks["R6"]
    assert r6.status == PASS and "data_post" not in r6.counts
    pt = fr.q("SELECT verdict, source FROM page_type WHERE key=?", (about["page_type"],))[0]
    assert (pt["verdict"], pt["source"]) == ("ok", "jscheck")
    assert fr.q("SELECT count(*) FROM event WHERE code='verdict_set'")[0][0] == 0
    net.sink.close()
    fr.close()
