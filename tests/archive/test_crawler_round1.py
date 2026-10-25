"""The round-1 crawler's core (round-1 design §17 step 4; §4–§11, §12; review 10-26 B1–B8): robots.txt read but never
a block, only video and audio left undownloaded, the configurable gap, the queue order, round1.admit() for every
address (owner_site, later, held, unhandled:google), what each page uses (page_res), the first bytes (sniff), error
pages, caps and paging depth, the http/https switch, server-wide pauses on connection failures, the run that ends
when only waits are left, where each request was made from, one request row per request on the wire, short links,
login re-checks, and the run's end. Everything runs on a fake network and a fake clock."""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nknu.archive import crawler as mod
from nknu.archive import outcome, round1, scope
from nknu.archive.monitor import politeness, scope_audit
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import KEEPALIVE_LIMITS, FetchError, RetryableStatus, Sent, Snapshot
from tests.archive.plans import plan, site
from tests.archive.test_archive import Clock, Net, Sink, make, seed, snap

QC = "ip:140.127.62.110"  # c.nknu.edu.tw's server
QS = "ip:140.127.40.173"  # sso.nknu.edu.tw's
ACA = site("c.nknu.edu.tw/aca", imp="全校重要", name="教务处", home="https://c.nknu.edu.tw/aca/default.aspx")
CCEE = site("c.nknu.edu.tw/ccee", name="特教中心", home="https://c.nknu.edu.tw/ccee/")
NEWS = site("sso.nknu.edu.tw/news", imp="全校重要", name="SSO 公告中心",
            home="https://sso.nknu.edu.tw/News/SubjectList.aspx")
WS = site("sso.nknu.edu.tw/webservices", rnd="resource")
AE = site("c.nknu.edu.tw/ae", rnd="2", why="other_site")
W3 = site("w3.nknu.edu.tw", rnd="2", why="js_site")
COUNSEL = site("sites.google.com/mail.nknu.edu.tw/counseling", name="谘商中心",
               home="https://sites.google.com/mail.nknu.edu.tw/counseling/home")
VD = site("vd.nknu.edu.tw", name="视设系", home="https://vd.nknu.edu.tw/")
PLAN = plan(ACA, CCEE, NEWS, WS, AE, W3, COUNSEL, VD)
QUEUES = {"c.nknu.edu.tw": QC, "sso.nknu.edu.tw": QS, "news.nknu.edu.tw": QS, "sites.google.com": "google"}
HOME = "https://c.nknu.edu.tw/aca/default.aspx"
NOT_FOUND = snap("x", 404)


def crawl(tmp_path, routes, **kw):
    kw.setdefault("plan", PLAN)
    kw.setdefault("queues", QUEUES)
    return make(tmp_path, {"robots.txt": NOT_FOUND, **routes}, **kw)


def page(html, status=200, headers=None):
    return lambda u: snap(u, status, html=html, headers=headers)


def drive(crawler, client, queue, sink=None):
    """A queue's worker without its thread: rows one by one until nothing is due or the queue is paused."""
    sink = sink or Sink(crawler.root)
    while not crawler._paused(queue) and crawler.frontier.paused_until(queue) <= crawler.clock():
        row = crawler._next(queue)  # the run's sites only, the host its connection is on first
        if row is None:
            return
        crawler._prefer[queue] = row["host"]
        crawler._handle(queue, client, sink, row)


def settle(crawler, client, queues, rounds=60):
    """What run() does over time, without threads: every queue driven, then the clock moved on to when something is
    next due (a retry, a crawl delay, a pause), until nothing is left or nothing comes due within the rounds."""
    for _ in range(rounds):
        for q in queues:
            drive(crawler, client, q)
        left, nxt = crawler.frontier.pending_left()
        if left == 0:
            return
        waits = [t for t in (nxt, crawler.frontier.paused_until("*"),
                             *(crawler.frontier.paused_until(q) for q in queues)) if t and t > crawler.clock()]
        crawler.clock.t = (min(waits) if waits else crawler.clock()) + 1.0


def rows(crawler):
    return {r["url"]: r for r in crawler.frontier.q("SELECT * FROM url")}


def res(crawler, page_url):
    pid = rows(crawler)[page_url]["id"]
    return {r["res_key"]: r for r in crawler.frontier.q("SELECT * FROM page_res WHERE page_id=?", (pid,))}


def result(crawler, url):
    return crawler.frontier.q("SELECT result FROM url_result WHERE url=?", (url,))[0][0]


def events(crawler, code):
    return [r["message"] for r in crawler.frontier.q("SELECT message FROM event WHERE code=? ORDER BY rowid", (code,))]


def links_of(crawler, url):
    uid = rows(crawler)[url]["id"]
    return {r["dst"]: r["verdict"] for r in crawler.frontier.q("SELECT dst, verdict FROM link WHERE src=?", (uid,))}


def conn_err(msg="[Errno 61] Connection refused"):
    exc = FetchError(f"https://x/: {msg}")
    exc.__cause__ = httpx.ConnectError(msg)
    return exc


def status_err(status):
    exc = FetchError(f"https://x/: HTTP {status}")
    exc.__cause__ = RetryableStatus(status)
    return exc


def timeout_err():
    exc = FetchError("https://x/: timed out")
    exc.__cause__ = httpx.ReadTimeout("timed out")
    return exc


# ---------------------------------------------------------------- robots.txt: read and kept, never a block (§6.8)


def test_robots_txt_is_read_and_kept_but_never_blocks_a_page(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"robots.txt": snap("x", html="User-agent: *\nDisallow: /\nCrawl-delay: 20\n"),
                                       "/aca/default.aspx": page("<p>教務處</p>")})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    assert rows(crawler)[HOME]["state"] == "done" and HOME in net.seen
    h = crawler.frontier.host("c.nknu.edu.tw")
    assert (h["robots_state"], h["crawl_delay"]) == ("rules", 20.0) and "Disallow: /" in h["robots_body"]
    reqs = crawler.frontier.q("SELECT started, ended FROM request ORDER BY id")
    assert all(b["started"] - a["ended"] >= 20 for a, b in itertools.pairwise(reqs))  # Crawl-delay kept


KNOWN = "User-agent: *\nCrawl-delay: 20\n"


def _known_rules(crawler):
    crawler.frontier.set_host("c.nknu.edu.tw", queue=QC, robots_state="rules", robots_body=KNOWN, crawl_delay=20.0,
                              robots_at=0.0, probe_status=404)


@pytest.mark.parametrize("answer", [status_err(503), status_err(500), timeout_err()])
def test_a_robots_txt_that_fails_never_overwrites_the_rules_already_known(tmp_path, answer):
    """B3: fine-arts asks for 20 s; a 5xx or no answer when robots.txt is read again must not lose it."""
    crawler, net, _clock = crawl(tmp_path, {"robots.txt": answer, "/aca/default.aspx": page("<p>x</p>")})
    _known_rules(crawler)
    seed(crawler, HOME)
    drive(crawler, net, QC)
    h = crawler.frontier.host("c.nknu.edu.tw")
    assert (h["robots_state"], h["robots_body"], h["crawl_delay"]) == ("rules", KNOWN, 20.0)


def test_a_5xx_robots_txt_is_no_rules_and_the_page_is_fetched(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"robots.txt": status_err(503), "/aca/default.aspx": page("<p>x</p>")})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    assert rows(crawler)[HOME]["state"] == "done"
    h = crawler.frontier.host("c.nknu.edu.tw")
    assert h["robots_state"] == "none" and h["robots_fail"] == 0 and events(crawler, "robots_5xx")


def test_only_video_and_audio_are_left_undownloaded():
    assert mod.SKIP_TYPES == ("video/", "audio/")


# ---------------------------------------------------------------- the gap (§6.3, B6)


class TimedNet(Net):
    """Each request takes the seconds its route says (default 1 s) on the fake clock."""

    def __init__(self, clock, routes, took):
        super().__init__(clock, routes)
        self.took = took

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        took = next((s for k, s in self.took.items() if k in target.url), 1.0)
        self.clock.t += took - 1.0
        return super().fetch(target, rate, max_bytes, pass_statuses)


def _gaps(crawler):
    reqs = crawler.frontier.q("SELECT started, ended FROM request ORDER BY id")
    return [(b["started"] - a["ended"], a["ended"] - a["started"]) for a, b in itertools.pairwise(reqs)]


def test_the_default_gap_is_2_to_5_seconds_and_a_slow_answer_is_waited_out(tmp_path):
    assert mod.GAP == (2.0, 5.0) and mod.SLOW_CAP_S == 30.0
    links = "".join(f'<a href="/aca/p{i}.aspx">{i}</a>' for i in range(4))
    crawler, _, clock = crawl(tmp_path, {})
    net = TimedNet(clock, {"robots.txt": NOT_FOUND, "/aca/default.aspx": page(links),
                           "/aca/p": page("<p>x</p>")}, {"/aca/p1": 12.0, "/aca/p2": 45.0})
    crawler.client_factory = net
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    for gap, took in _gaps(crawler):
        assert gap >= max(2.0, min(took, 30.0)) - 1e-9 and gap <= max(5.0, min(took, 30.0)) + 1e-9


def test_the_gap_can_be_set_to_1_to_2_seconds_and_never_below_1(tmp_path):
    links = "".join(f'<a href="/aca/p{i}.aspx">{i}</a>' for i in range(4))
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(links), "/aca/p": page("<p>x</p>")},
                            gap=mod.GAP_FAST)
    assert mod.GAP_FAST == (1.0, 2.0)
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    assert all(1.0 <= gap <= 2.0 for gap, _took in _gaps(crawler))
    for bad in ((0.5, 1.0), (2.0, 1.0)):
        with pytest.raises(ValueError):
            make(tmp_path / "bad", {}, gap=bad)


def test_the_run_start_says_which_gap_the_run_used(tmp_path):
    crawler, _, _ = crawl(tmp_path, {}, gap=(1.0, 2.0))
    crawler.halt("limit")
    crawler.run(poll_s=0.01)
    start = json.loads(events(crawler, "run_start")[-1])
    assert start["gap"] == [1.0, 2.0] and start["sites"] is None


# ---------------------------------------------------------------- the order a queue's rows go in (§6.7)


def test_next_for_orders_by_tier_prio_same_host_depth(tmp_path):
    crawler, _, clock = crawl(tmp_path, {})
    f = crawler.frontier
    for h in ("a.nknu.edu.tw", "b.nknu.edu.tw"):
        f.set_host(h, queue="ip:1")
    spec = [("t1p0", "a", 1, 0, 0), ("t0p2", "a", 0, 2, 0), ("t0p1b", "b", 0, 1, 0), ("t0p1a2", "a", 0, 1, 2),
            ("t0p1a1", "a", 0, 1, 1)]
    for name, h, tier, prio, depth in spec:
        f.add(f"https://{h}.nknu.edu.tw/{name}", host=f"{h}.nknu.edu.tw", site=f"{h}.nknu.edu.tw", unit=None,
              tier=tier, kind="page", depth=depth, found_via="seed", found_from=None, prio=prio,
              owner_site=f"{h}.nknu.edu.tw")

    def order(prefer):
        out = []
        while (row := f.next_for(["a.nknu.edu.tw", "b.nknu.edu.tw"], clock(), prefer_host=prefer)) is not None:
            out.append(row["url"].rsplit("/", 1)[-1])
        f.x("UPDATE url SET state='pending'")
        return out

    assert order(None) == ["t0p1b", "t0p1a1", "t0p1a2", "t0p2", "t1p0"]
    assert order("a.nknu.edu.tw") == ["t0p1a1", "t0p1a2", "t0p1b", "t0p2", "t1p0"]


# ---------------------------------------------------------------- the one way in: round1.admit() (B7, B8; §4.3–4.5)

LINKS = """<a href="/aca/news.aspx">最新消息</a>
<a href="https://www.moe.gov.tw/files/rule.pdf">教育部附件</a>
<a href="https://www.moe.gov.tw/news.html">教育部</a>
<a href="https://c.nknu.edu.tw/ae/x.aspx">成教系</a>
<a href="https://w3.nknu.edu.tw/">學校首頁</a>
<a href="https://sso.nknu.edu.tw/">登入</a>
<a href="https://drive.google.com/file/d/1AbCdEfGhIjK/view">雲端檔案</a>
<a href="https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx?f=1">附件下載</a>
<a href="/aca/news.aspx?unid=9">賀！本系陳O丙同學榮獲全國競賽第一名</a>"""


def one(crawler, client, queue):
    """One row of a queue, as its worker takes it."""
    row = crawler._next(queue)
    crawler._handle(queue, client, Sink(crawler.root), row)


def test_every_link_goes_through_admit_and_lands_where_round_1_says(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(LINKS)})
    seed(crawler, HOME)
    one(crawler, net, QC)
    r = rows(crawler)
    home = r[HOME]
    assert (home["owner_site"], home["tier"], home["prio"], home["unit"]) == ("c.nknu.edu.tw/aca", 0, 0, "教务处")
    news = r["https://c.nknu.edu.tw/aca/news.aspx"]
    assert (news["state"], news["owner_site"], news["tier"], news["prio"], news["depth"], news["kind0"]) == \
        ("pending", "c.nknu.edu.tw/aca", 0, 1, 1, "page")
    pdf = r["https://www.moe.gov.tw/files/rule.pdf"]  # the owner 10-26 (record 五·15): off-campus attachments, now
    assert (pdf["state"], pdf["owner_site"], pdf["tier"], pdf["leaf"], pdf["kind0"], pdf["prio"]) == \
        ("pending", "c.nknu.edu.tw/aca", 0, 1, "file", 2)
    moe = r["https://www.moe.gov.tw/news.html"]
    assert (moe["state"], moe["reason"], moe["tier"], moe["leaf"]) == ("later", "round2:offsite", 2, 1)
    assert (r["https://c.nknu.edu.tw/ae/x.aspx"]["state"], r["https://c.nknu.edu.tw/ae/x.aspx"]["reason"]) == \
        ("later", "round2:other_site")
    assert r["https://w3.nknu.edu.tw/"]["reason"] == "round2:js_site"
    ws = r["https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx?f=1"]
    assert (ws["state"], ws["owner_site"], ws["kind0"]) == ("pending", "c.nknu.edu.tw/aca", "file")
    assert r["https://c.nknu.edu.tw/aca/news.aspx?unid=9"]["state"] == "pending"  # honour notices are followed
    assert "https://sso.nknu.edu.tw/" not in r and not any("drive.google.com" in u for u in r)
    verdicts = links_of(crawler, HOME)
    assert verdicts["https://sso.nknu.edu.tw/"] == "out:login"
    drive_link = "https://drive.google.com/file/d/1AbCdEfGhIjK/view"
    assert verdicts[drive_link] == "unhandled:google"  # B8: never out:never_host (规则不抓) before the Google step
    pr = res(crawler, HOME)[f"google:{drive_link}"]
    assert (pr["role"], pr["need"], pr["why"], pr["res_id"]) == ("drive", "must", "unhandled:google", None)
    assert crawler.frontier.host("w3.nknu.edu.tw") is None  # a round-2 address is not even looked up
    assert crawler.frontier.host("www.moe.gov.tw")["queue"] == "ip:www.moe.gov.tw"


def test_a_round_two_row_a_round_1_page_uses_becomes_its_resource(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="https://www.moe.gov.tw/b.pdf">b</a>')})
    assert crawler.seed_url("https://www.moe.gov.tw/b.pdf", source="test") == "round2:offsite_file"  # no page
    seed(crawler, HOME)
    drive(crawler, net, QC)
    pdf = rows(crawler)["https://www.moe.gov.tw/b.pdf"]
    assert (pdf["state"], pdf["reason"], pdf["owner_site"], pdf["tier"]) == ("pending", None, "c.nknu.edu.tw/aca", 0)


def test_caps_hold_rows_back_and_say_which_cap(tmp_path):
    html = ("".join(f'<a href="/aca/p{i}.aspx">{i}</a>' for i in range(4))
            + '<a href="/aca/a.pdf">a</a><a href="/aca/b.pdf">b</a>'
            + '<img src="/aca/uploadfile/1.jpg"><img src="/aca/uploadfile/2.jpg">')
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(html)}, site_cap=4, file_cap=1, image_cap=1)
    seed(crawler, HOME)
    drive(crawler, net, QC)
    held = dict(crawler.frontier.q("SELECT reason, count(*) FROM url WHERE state='held' GROUP BY reason"))
    assert held == {"cap:site": 1, "cap:file": 1, "cap:image": 1}  # the home and 3 pages, 1 file, 1 image
    cal = "".join(f'<a href="/ccee/cal.aspx?d={i}">{i}</a>' for i in range(3))
    crawler, net, _ = crawl(tmp_path / "variants", {"/ccee/": page(cal)}, variant_cap=2)
    seed(crawler, "https://c.nknu.edu.tw/ccee/")
    drive(crawler, net, QC)
    held = dict(crawler.frontier.q("SELECT reason, count(*) FROM url WHERE state='held' GROUP BY reason"))
    assert held == {"cap:variant": 1}
    assert crawler.enqueue("https://c.nknu.edu.tw/aca/deep.aspx", depth=mod.MAX_DEPTH + 1, found_via="link") \
        == "cap:depth"
    assert result(crawler, "https://c.nknu.edu.tw/aca/deep.aspx") == "held"


def test_a_pager_link_adds_no_depth_and_goes_first(tmp_path):
    html = ('<a href="/aca/newslist.aspx?page=2">2</a><a href="/aca/newslist.aspx?page=3">下一頁</a>'
            '<a href="/aca/item.aspx?id=1">公告</a>')
    crawler, net, _ = crawl(tmp_path, {"/aca/newslist.aspx": page(html)})
    seed(crawler, "https://c.nknu.edu.tw/aca/newslist.aspx?page=1")
    drive(crawler, net, QC)
    r = rows(crawler)
    p2 = r["https://c.nknu.edu.tw/aca/newslist.aspx?page=2"]
    assert (p2["depth"], p2["prio"]) == (0, 0)
    assert r["https://c.nknu.edu.tw/aca/item.aspx?id=1"]["depth"] == 1


# ---------------------------------------------------------------- what a page uses: page_res (§7.1, §7.2, §7.8)

PARTS = """<img src="/aca/uploadfile/poster.jpg" alt="海報">
<img src="/aca/images/dot.gif" width="1" height="1">
<img src="/aca/CheckCode.aspx">
<iframe src="/aca/frame.aspx"></iframe>
<iframe src="https://www.canva.com/design/DAF/view"></iframe>
<iframe src="https://www.youtube.com/embed/ZyXwVuTsRqP" title="畢業典禮"></iframe>
<script src="/aca/js/menu.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<a href="javascript:void(0)" onclick="showTab(3)">分頁</a>
<a href="javascript:__doPostBack('MasterGrid$ctl02$lnkDetail','')">看</a>
<a href="javascript:__doPostBack('MasterGrid','Page$2')">2</a>
<a href="https://forms.gle/AbCdEf123">報名</a>
<a href="https://docs.google.com/forms/d/e/1FAIpQLSdXyZ/viewform">表單</a>
<a href="/aca/uploadfile/rule.pdf">辦法</a>"""


def test_each_part_of_a_page_has_a_page_res_row_with_its_need_and_why(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(PARTS)})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    pr, r = res(crawler, HOME), rows(crawler)

    def entry(key):
        x = pr[key]
        return x["how"], x["role"], x["need"], x["why"]

    poster = "https://c.nknu.edu.tw/aca/uploadfile/poster.jpg"
    assert entry(poster) == ("get", "img", "must", None) and pr[poster]["res_id"] == r[poster]["id"]
    assert (r[poster]["kind0"], r[poster]["prio"], r[poster]["leaf"]) == ("image", 3, 1)
    assert entry("https://c.nknu.edu.tw/aca/images/dot.gif")[2:] == ("no", "tiny")
    assert entry("https://c.nknu.edu.tw/aca/checkcode.aspx")[2:] == ("no", "captcha")
    assert "https://c.nknu.edu.tw/aca/checkcode.aspx" not in r and "https://c.nknu.edu.tw/aca/images/dot.gif" not in r
    frame = "https://c.nknu.edu.tw/aca/frame.aspx"
    assert entry(frame) == ("get", "iframe", "must", None) and r[frame]["leaf"] == 0
    canva = "https://www.canva.com/design/DAF/view"
    assert entry(canva)[2] == "round2" and r[canva]["state"] == "later"
    yt = "https://www.youtube.com/embed/ZyXwVuTsRqP"
    assert entry(yt) == ("link_only", "iframe", "must", "out:social")
    js = "https://c.nknu.edu.tw/aca/js/menu.js"
    assert entry(js) == ("get", "script", "no", "script") and (r[js]["kind0"], r[js]["prio"]) == ("script", 3)
    assert not any("jquery" in k for k in pr) and not any("jquery" in u for u in r)
    assert entry("js_link:showTab") == ("unhandled", "click", "must", "unhandled:unknown")  # no platform file yet
    assert entry("postback:MasterGrid$ctl*$lnkDetail") == ("unhandled", "click", "must", "unhandled:unknown")
    assert entry("postback:MasterGrid") == ("unhandled", "click", "must", "unhandled:unknown")
    pb = {x["target"]: x for x in crawler.frontier.q("SELECT * FROM postback")}
    assert (pb["MasterGrid"]["argument"], pb["MasterGrid"]["text"], pb["MasterGrid"]["decision"]) == \
        ("Page$2", "2", "unknown")
    short = "https://forms.gle/AbCdEf123"
    assert entry(short) == ("get", "shortlink", "must", None) and r[short]["kind0"] == "shortlink"
    form = "https://docs.google.com/forms/d/e/1FAIpQLSdXyZ/viewform"
    assert entry(form) == ("get", "form", "must", None) and (r[form]["kind0"], r[form]["leaf"]) == ("form", 1)
    rule = "https://c.nknu.edu.tw/aca/uploadfile/rule.pdf"
    assert entry(rule) == ("get", "file", "must", None)
    video = crawler.frontier.q("SELECT video_id, title, how FROM video")[0]
    assert tuple(video) == ("ZyXwVuTsRqP", "畢業典禮", "embed")
    level = crawler.frontier.q("SELECT level FROM page_completeness WHERE id=?", (r[HOME]["id"],))[0][0]
    assert level != "complete"  # nothing it uses is fetched yet, and its type is not judged


def test_a_page_read_again_replaces_its_page_res(tmp_path):
    state = {"html": PARTS}
    crawler, net, _clock = crawl(tmp_path, {"/aca/default.aspx": lambda u: snap(u, html=state["html"])})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    state["html"] = '<a href="/aca/uploadfile/new.pdf">新</a>'
    crawler.frontier.x("UPDATE url SET state='pending' WHERE url=?", (HOME,))
    drive(crawler, net, QC)
    assert set(res(crawler, HOME)) == {"https://c.nknu.edu.tw/aca/uploadfile/new.pdf"}


def test_an_image_on_most_pages_and_never_in_a_body_is_a_template_image(tmp_path):
    pages = "".join(f'<a href="/aca/p{i}.aspx">{i}</a>' for i in range(6))
    crawler, net, _clock = crawl(tmp_path, {"/aca/default.aspx": page(pages + '<img src="/aca/img/top_pic.png">'),
                                           "/aca/p": page('<p>x</p><img src="/aca/img/top_pic.png">')})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    pic = rows(crawler)["https://c.nknu.edu.tw/aca/img/top_pic.png"]
    assert (pic["state"], pic["reason"]) == ("skipped", "decor:repeat")
    assert not any("top_pic" in u for u in net.seen) and result(crawler, pic["url"]) == "rule"


# ---------------------------------------------------------------- the first bytes say what it is (§7.3)

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"


def test_a_page_address_that_answers_a_pdf_is_a_file(tmp_path):
    cd = "attachment; filename*=UTF-8''%E7%B0%A1%E7%AB%A0.pdf"
    url = "https://c.nknu.edu.tw/aca/doc.aspx?id=1"
    crawler, net, _ = crawl(tmp_path, {"/aca/doc.aspx": lambda u: snap(u, body=PDF, headers={
        "content-type": "application/octet-stream", "content-disposition": cd})})
    seed(crawler, url)
    drive(crawler, net, QC)
    row = rows(crawler)[url]
    assert (row["kind"], row["sniff"], row["filename"], row["n_links"]) == ("file", "pdf", "簡章.pdf", None)
    assert result(crawler, url) == "ok"


def test_an_attachment_that_answers_a_web_page_is_not_a_file_and_is_not_followed(tmp_path):
    home = "https://c.nknu.edu.tw/aca/default.aspx"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="/aca/uploadfile/x.pdf">x</a>'),
                                       "/aca/uploadfile/x.pdf": page('<a href="/aca/elsewhere.aspx">e</a>')})
    seed(crawler, home)
    settle(crawler, net, [QC])
    x = rows(crawler)["https://c.nknu.edu.tw/aca/uploadfile/x.pdf"]
    assert (x["kind"], x["sniff"]) == ("file", "html") and result(crawler, x["url"]) == "not_file"
    assert "https://c.nknu.edu.tw/aca/elsewhere.aspx" not in rows(crawler)


def test_a_web_page_sent_as_plain_text_is_read_as_a_page(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page("<html><a href='/aca/next.aspx'>n</a></html>",
                                                                  headers={"content-type": "text/plain"})})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    assert rows(crawler)[HOME]["kind"] == "page" and "https://c.nknu.edu.tw/aca/next.aspx" in rows(crawler)


def test_an_image_that_answers_a_pdf_becomes_the_pages_attachment(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<img src="/aca/uploadfile/scan.jpg">'),
                                       "/aca/uploadfile/scan.jpg": lambda u: snap(u, body=PDF, headers={
                                           "content-type": "image/jpeg"})})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    assert rows(crawler)["https://c.nknu.edu.tw/aca/uploadfile/scan.jpg"]["kind"] == "file"
    assert res(crawler, HOME)["https://c.nknu.edu.tw/aca/uploadfile/scan.jpg"]["role"] == "file"


def test_an_image_answer_is_stored(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<img src="/aca/uploadfile/a.png">'),
                                       "/aca/uploadfile/a.png": lambda u: snap(u, body=png, headers={
                                           "content-type": "image/png"})})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    a = rows(crawler)["https://c.nknu.edu.tw/aca/uploadfile/a.png"]
    assert (a["state"], a["kind"], a["sniff"]) == ("done", "image", "png") and result(crawler, a["url"]) == "ok"


# ---------------------------------------------------------------- error pages (§10.1)

SSO_ITEM = "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=AbC1"


def test_a_redirect_to_ssos_not_found_page_is_gone_and_the_error_page_is_not_queued(tmp_path):
    to = "https://sso.nknu.edu.tw/ErrorPage/PageNotFound.aspx?aspxerrorpath=/News/lastNews.aspx"
    crawler, net, _ = crawl(tmp_path, {"lastnews.aspx": snap("x", 302, headers={"location": to})})
    seed(crawler, SSO_ITEM)
    drive(crawler, net, QS)
    row = rows(crawler)[SSO_ITEM]
    assert row["flags"] == "gone_redirect" and result(crawler, SSO_ITEM) == "gone"
    assert not any("pagenotfound" in u for u in rows(crawler))
    assert set(links_of(crawler, SSO_ITEM).values()) == {outcome.ERROR_LINK}


@pytest.mark.parametrize("to", ["https://sso.nknu.edu.tw/ErrorPage/ErrorPage.aspx",
                                "https://c.nknu.edu.tw/affair/filenotfound.htm?aspxerrorpath=/affair/sitemap.aspx"])
def test_a_redirect_to_an_error_page_is_retried_then_a_server_error(tmp_path, to):
    url = SSO_ITEM if "sso" in to else "https://c.nknu.edu.tw/aca/sitemap.aspx"
    crawler, net, clock = crawl(tmp_path, {"lastnews.aspx": snap("x", 302, headers={"location": to}),
                                           "/aca/sitemap.aspx": snap("x", 302, headers={"location": to})})
    seed(crawler, url)
    queue = QS if "sso" in url else QC
    drive(crawler, net, queue)
    row = rows(crawler)[url]
    assert (row["state"], row["attempts"], row["reason"]) == ("pending", 1, "error_page")
    for _ in range(2):
        clock.t = row["next_at"] + 1 if (row := rows(crawler)[url])["state"] == "pending" else clock.t
        drive(crawler, net, queue)
    assert (rows(crawler)[url]["state"], result(crawler, url)) == ("failed", "server_error")


def test_ssos_not_found_page_fetched_directly_is_gone(tmp_path):
    url = "https://sso.nknu.edu.tw/errorpage/pagenotfound.aspx?aspxerrorpath=/x"
    crawler, net, _ = crawl(tmp_path, {"pagenotfound": page("<p>404</p>")},
                            plan=plan(NEWS, site("sso.nknu.edu.tw/errorpage")))
    seed(crawler, url)
    drive(crawler, net, QS)
    assert result(crawler, url) == "gone"


# ---------------------------------------------------------------- redirects: 3xx and script (§8.3)

ELEC = """<script type='text/javascript'>alert("sorry! page close!");</script>
<script Language='JavaScript'>location.href='index.html';</script>"""


def test_a_short_page_that_sends_the_browser_on_is_a_redirect_with_no_added_depth(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": NOT_FOUND, "/job.html": page(ELEC), "/index.html": page("<p>x</p>")})
    seed(crawler, "https://elec.nknu.edu.tw/job.html")
    drive(crawler, net, "ip:elec.nknu.edu.tw")
    r = rows(crawler)
    job, target = r["https://elec.nknu.edu.tw/job.html"], r["https://elec.nknu.edu.tw/index.html"]
    assert (job["flags"], job["final_url"]) == ("js_redirect", target["url"])
    assert (target["found_via"], target["depth"]) == ("js_redirect", 0)
    assert result(crawler, job["url"]) == "redirect"


def test_a_short_page_that_sends_the_browser_to_a_login_is_a_login(tmp_path):
    step = "<script>alert('請先登入'); document.location.href = '../login.php'</script>"
    crawler, net, _ = make(tmp_path, {"robots.txt": NOT_FOUND, "apply_step1.php": page(step)})
    url = "https://clcts.nknu.edu.tw/clctm/apply_step1.php"
    seed(crawler, url)
    drive(crawler, net, "ip:clcts.nknu.edu.tw")
    row = rows(crawler)[url]
    assert "login" in row["flags"].split(",") and row["n_links"] == 0 and result(crawler, url) == "login"
    assert links_of(crawler, url)["https://clcts.nknu.edu.tw/login.php"] == "out:login"


def test_an_attachment_that_redirects_off_campus_stays_the_pages_attachment(tmp_path):
    cdn = "https://cdn.example.org/files/abc?sig=1"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="/aca/uploadfile/a.pdf">a</a>'),
                                       "/aca/uploadfile/a.pdf": snap("x", 302, headers={"location": cdn})})
    seed(crawler, HOME)
    settle(crawler, net, [QC])
    target = rows(crawler)[cdn]
    assert (target["state"], target["kind0"], target["leaf"], target["owner_site"], target["found_via"]) == \
        ("pending", "file", 1, "c.nknu.edu.tw/aca", "redirect")


def test_a_page_that_redirects_off_campus_is_round_two_and_a_seed_that_moved_keeps_its_site(tmp_path):
    crawler, net, _ = crawl(tmp_path, {
        "/aca/default.aspx": page('<a href="/aca/link.aspx">l</a>'),
        "/aca/link.aspx": snap("x", 302, headers={"location": "https://www.moe.gov.tw/news.html"}),
        "c.nknu.edu.tw/ccee/": snap("x", 301, headers={"location": "https://ccee.nknu.edu.tw/"})})
    seed(crawler, HOME)
    seed(crawler, "https://c.nknu.edu.tw/ccee/")
    settle(crawler, net, [QC])
    r = rows(crawler)
    assert (r["https://www.moe.gov.tw/news.html"]["state"], r["https://www.moe.gov.tw/news.html"]["reason"]) == \
        ("later", "round2:offsite")
    moved = r["https://ccee.nknu.edu.tw/"]
    assert (moved["state"], moved["owner_site"], moved["tier"], moved["depth"]) == \
        ("pending", "c.nknu.edu.tw/ccee", 1, 0)


# ---------------------------------------------------------------- http and https (§6.6)


def _one_scheme(serves):
    def route(u):
        if not u.startswith(serves + "://"):
            raise conn_err()
        if u.endswith("/robots.txt") or "nknu-archive-probe" in u:
            return snap(u, 404)
        return snap(u, html='<a href="http://one.nknu.edu.tw/b">b</a>')

    return route


def test_a_host_that_answers_only_https_moves_the_http_rows_and_remembers_it(tmp_path):
    crawler, net, _ = make(tmp_path, {"one.nknu.edu.tw": _one_scheme("https")})
    seed(crawler, "http://one.nknu.edu.tw/a")
    settle(crawler, net, ["ip:one.nknu.edu.tw"])
    r = rows(crawler)
    old = r["http://one.nknu.edu.tw/a"]
    assert (old["state"], old["reason"], old["final_url"]) == ("skipped", "moved_scheme", "https://one.nknu.edu.tw/a")
    new = r["https://one.nknu.edu.tw/a"]
    assert (new["state"], new["status"], new["found_via"]) == ("done", 200, "scheme_switch")
    assert crawler.frontier.host("one.nknu.edu.tw")["scheme_only"] == "https"
    assert "http://one.nknu.edu.tw/b" not in r and r["https://one.nknu.edu.tw/b"]["state"] == "done"
    assert links_of(crawler, "https://one.nknu.edu.tw/a")["http://one.nknu.edu.tw/b"] == "scheme_switch"
    assert result(crawler, old["url"]) == "redirect"
    assert crawler.frontier.q("SELECT count(*) FROM page_completeness WHERE id=?", (old["id"],))[0][0] == 0


def test_a_page_refused_on_http_is_tried_once_on_https(tmp_path):
    crawler, net, clock = make(tmp_path, {"one.nknu.edu.tw": _one_scheme("https")})
    seed(crawler, "http://one.nknu.edu.tw/a")
    crawler.frontier.set_host("one.nknu.edu.tw", queue="ip:one.nknu.edu.tw", robots_state="none",
                              robots_at=clock(), probe_status=404)
    drive(crawler, net, "ip:one.nknu.edu.tw")
    assert net.seen[:2] == ["http://one.nknu.edu.tw/a", "https://one.nknu.edu.tw/a"]
    assert rows(crawler)["https://one.nknu.edu.tw/a"]["found_via"] == "scheme_switch"
    assert crawler.frontier.q("SELECT count(*) FROM event WHERE code='scheme_switch'")[0][0] == 1


def test_a_page_refused_on_both_schemes_pauses_the_server_and_costs_no_attempt(tmp_path):
    crawler, net, clock = make(tmp_path, {"one.nknu.edu.tw": conn_err()})
    seed(crawler, "http://one.nknu.edu.tw/a")
    crawler.frontier.set_host("one.nknu.edu.tw", queue="ip:one.nknu.edu.tw", robots_state="none",
                              robots_at=clock(), probe_status=404)
    drive(crawler, net, "ip:one.nknu.edu.tw")
    row = rows(crawler)["http://one.nknu.edu.tw/a"]
    assert (row["state"], row["attempts"]) == ("pending", 0)
    assert crawler.frontier.paused_until("ip:one.nknu.edu.tw") > clock()


# ---------------------------------------------------------------- B2: a server that does not connect


def _two_names_one_server(tmp_path, routes, **kw):
    q = "ip:140.127.9.9"
    crawler, net, clock = make(tmp_path, {"robots.txt": NOT_FOUND, **routes},
                               queues={"a.nknu.edu.tw": q, "b.nknu.edu.tw": q}, **kw)
    return crawler, net, clock, q


def test_a_connection_failure_pauses_the_whole_server_15_minutes_and_costs_no_attempt(tmp_path):
    crawler, net, clock, q = _two_names_one_server(tmp_path, {"a.nknu.edu.tw/p": conn_err(),
                                                             "b.nknu.edu.tw/": page("<p>b</p>")})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    seed(crawler, "https://b.nknu.edu.tw/x")
    drive(crawler, net, q)
    failed_at = crawler.frontier.q("SELECT max(ended) FROM request WHERE url LIKE '%/p1'")[0][0]
    assert mod.CONN_PAUSE_S == 900 and 900 <= crawler.frontier.paused_until(q) - failed_at <= 905  # after its gap
    row = rows(crawler)["https://a.nknu.edu.tw/p1"]
    assert (row["state"], row["attempts"], row["reason"]) == ("pending", 0, "conn_pause")
    assert "https://b.nknu.edu.tw/x" not in net.seen  # the other name on that server waits too: one server, one count
    clock.t = crawler.frontier.paused_until(q) + 1
    drive(crawler, net, q)
    again = crawler.frontier.q("SELECT max(ended) FROM request WHERE url LIKE '%/p1'")[0][0]
    assert 900 <= crawler.frontier.paused_until(q) - again <= 905  # fixed: never doubled
    assert rows(crawler)["https://a.nknu.edu.tw/p1"]["attempts"] == 0
    assert crawler.frontier.q("SELECT conn_fail FROM server WHERE queue=?", (q,))[0][0] == 2


def test_an_answer_ends_the_servers_failure_streak(tmp_path):
    state = {"down": True}

    def a(u):
        if state["down"]:
            raise conn_err()
        return snap(u, html="<p>a</p>")

    crawler, net, clock, q = _two_names_one_server(tmp_path, {"a.nknu.edu.tw/p": a})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    drive(crawler, net, q)
    state["down"] = False
    clock.t = crawler.frontier.paused_until(q) + 1
    drive(crawler, net, q)
    assert crawler.frontier.q("SELECT conn_fail FROM server WHERE queue=?", (q,))[0][0] == 0
    assert rows(crawler)["https://a.nknu.edu.tw/p1"]["state"] == "done"


def _down_for_good(crawler, net, clock, q):
    for _ in range(mod.CONN_GIVE_UP):
        drive(crawler, net, q)
        clock.t = max(clock(), crawler.frontier.paused_until(q)) + 1


def test_a_school_server_that_never_answered_waits_for_the_lab_machine(tmp_path):
    crawler, net, clock, q = _two_names_one_server(tmp_path, {"robots.txt": conn_err()})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    seed(crawler, "https://b.nknu.edu.tw/p2")
    _down_for_good(crawler, net, clock, q)
    assert {(x["state"], x["reason"]) for x in rows(crawler).values()} == {("failed", "host_unreachable")}
    assert {result(crawler, u) for u in rows(crawler)} == {"lab"} and events(crawler, "host_unreachable")


def test_a_server_that_answered_before_is_down_for_now_never_lab(tmp_path):
    """B3: a host the old archive got answers from off campus: its connection failures are temporary. B2 (second
    review 10-26): however long it is down, its rows keep every attempt; one warning says it is down."""
    crawler, net, clock, q = _two_names_one_server(tmp_path, {"robots.txt": conn_err()})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    crawler.frontier.set_host("a.nknu.edu.tw", answered_off=1)
    _down_for_good(crawler, net, clock, q)
    row = rows(crawler)["https://a.nknu.edu.tw/p1"]
    assert (row["state"], row["attempts"], row["reason"]) == ("pending", 0, "conn_pause")
    assert result(crawler, row["url"]) == "todo" and len(events(crawler, "server_down")) == 1


def test_three_school_servers_down_while_the_internet_works_pause_the_schools_queues(tmp_path):
    hosts = {f"s{i}.nknu.edu.tw": f"ip:140.127.1.{i}" for i in range(3)}
    hosts["o.example.org"] = "ip:203.0.113.9"
    crawler, net, clock = make(tmp_path, {"nknu.edu.tw/robots.txt": conn_err(), "o.example.org/": page("<p>o</p>")},
                               queues=hosts, netcheck=lambda: True)
    for h in hosts:
        if h.endswith("nknu.edu.tw"):
            seed(crawler, f"https://{h}/a")
    seed(crawler, "https://s0.nknu.edu.tw/b")
    for q in sorted(set(hosts.values()) - {"ip:203.0.113.9"}):
        drive(crawler, net, q)
    assert crawler.frontier.pause_of("net:school") >= clock() + 800
    assert crawler.frontier.paused_until("*") == 0 and crawler.frontier.pause_of("net:other") == 0
    assert crawler.frontier.q("SELECT coalesce(sum(conn_fail), 0) FROM server")[0][0] == 0  # nothing held against them
    assert all(x["attempts"] == 0 for x in rows(crawler).values())
    assert any("學校" in m or "school" in m for m in events(crawler, "offline"))


# ---------------------------------------------------------------- B1: a run ends when only waits are left


def test_a_run_with_only_retries_waiting_ends_and_says_so(tmp_path):
    crawler, _, clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    crawler.frontier.x("UPDATE url SET attempts=1, reason='timeout', next_at=?", (clock() + 1800,))
    crawler.run(poll_s=0.01)
    end = json.loads(events(crawler, "run_end")[-1])
    assert end["reason"] == "waiting" and end["pending"] == 1 and end["next_due"] == pytest.approx(clock() + 1800)
    assert crawler.end_reason == "waiting"


def test_a_run_with_nothing_left_is_finished(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    crawler.run(poll_s=0.01)
    assert json.loads(events(crawler, "run_end")[-1])["reason"] == "finished"


def test_a_run_waits_out_a_short_wait(tmp_path):
    import threading

    crawler, _, clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    crawler.frontier.set_host("c.nknu.edu.tw", not_before=clock() + 20)  # a Crawl-delay
    t = threading.Thread(target=crawler.run, kwargs={"poll_s": 0.01})
    t.start()
    t.join(0.3)
    assert t.is_alive()  # 20 s is waited for, not left for another run
    crawler.halt("stop_file")
    t.join(5)
    assert json.loads(events(crawler, "run_end")[-1])["reason"] == "stop_file"


def test_the_run_covers_only_the_sites_it_was_given(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page("<p>a</p>"), "/ccee/": page("<p>c</p>")},
                            sites=frozenset({"c.nknu.edu.tw/ccee"}))
    seed(crawler, HOME)
    seed(crawler, "https://c.nknu.edu.tw/ccee/")
    drive(crawler, net, QC)
    assert rows(crawler)[HOME]["state"] == "pending" and rows(crawler)["https://c.nknu.edu.tw/ccee/"]["state"] == "done"
    crawler.run(poll_s=0.01)
    end = json.loads(events(crawler, "run_end")[-1])
    assert end["sites"] == ["c.nknu.edu.tw/ccee"] and end["reason"] == "finished"


# ---------------------------------------------------------------- B4: on campus or off campus


def test_the_campus_check_goes_on_the_sso_queue_and_every_request_says_where_it_was_made(tmp_path):
    addr = {"now": "192.168.1.5"}
    off = page("<p>系統訊息：您的IP 不在校內網路，請連線VPN</p>")
    crawler, net, clock = crawl(tmp_path, {"checkcampusip": off, "/aca/default.aspx": page("<p>a</p>")},
                                campus_url=mod.CAMPUS_URL, local_address=lambda: addr["now"])
    assert mod.CAMPUS_URL == "https://sso.nknu.edu.tw/zthat/checkcampusip.aspx?curl=edocap.nknu.edu.tw"
    assert crawler.campus_queue() == QS
    sink = Sink(tmp_path)
    assert crawler._campus_step(QS, net, sink) and crawler.campus == "off"
    assert not crawler._campus_step(QS, net, sink)  # not due again within the hour
    seed(crawler, HOME)
    drive(crawler, net, QC)
    reqs = crawler.frontier.q("SELECT purpose, campus FROM request ORDER BY id")
    assert reqs[0]["purpose"] == "campus" and {r["campus"] for r in reqs[1:]} == {"off"}
    addr["now"] = "140.127.200.9"  # the Mac moved to the school's Wi-Fi
    crawler.client_factory = Net(clock, {"checkcampusip": snap("x", 302, headers={
        "location": "https://edocap.nknu.edu.tw/"})})
    assert crawler._campus_step(QS, crawler.client_factory, sink) and crawler.campus == "on"
    assert any("校内" in m for m in events(crawler, "campus"))
    clock.t += mod.CAMPUS_EVERY_S + 1
    assert crawler._campus_due()
    assert scope_audit(crawler.frontier, crawler.plan) == []  # the check page is a login system's: allowed only so


def test_a_host_that_answered_off_campus_is_marked(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page("<p>a</p>")})
    crawler.campus = "off"
    seed(crawler, HOME)
    drive(crawler, net, QC)
    assert crawler.frontier.host("c.nknu.edu.tw")["answered_off"] == 1


# ---------------------------------------------------------------- B5: politeness by server address, the lock


def _req(f, queue, url, start, end, ip=None, purpose="page"):
    f.request(queue, scope.urlsplit(url).hostname, url, purpose, start, end, 200, None, ip=ip, scope="r1")


def test_politeness_is_checked_per_server_address_across_queues_purposes_and_runs(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    f = crawler.frontier
    _req(f, "ip:140.127.1.1", "https://a.nknu.edu.tw/robots.txt", 100.0, 101.0, ip="140.127.1.1", purpose="robots")
    _req(f, "ip:140.127.1.2", "https://b.nknu.edu.tw/x", 102.0, 103.0, ip="140.127.1.1")  # same server, 1 s later
    _req(f, "ip:140.127.1.3", "https://c.nknu.edu.tw/y", 103.5, 104.0)  # no address: its queue's
    _req(f, "ip:140.127.1.3", "https://c.nknu.edu.tw/z", 106.5, 107.0)
    out = politeness(f)
    assert len(out) == 1 and "140.127.1.1" in out[0] and "间隔不足" in out[0]


def test_politeness_uses_the_gap_each_run_was_set_to(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    f = crawler.frontier
    f.event("info", "run_start", json.dumps({"pid": 1, "gap": [1.0, 2.0], "sites": None}))
    f.x("UPDATE event SET at='1970-01-01T00:00:50+00:00'")  # the run started before the requests
    _req(f, "ip:9", "https://a.nknu.edu.tw/1", 100.0, 101.0, ip="140.127.1.1")
    _req(f, "ip:9", "https://a.nknu.edu.tw/2", 102.2, 103.0, ip="140.127.1.1")  # 1.2 s: fine at 1–2 s
    assert politeness(f) == []
    _req(f, "ip:9", "https://a.nknu.edu.tw/3", 103.8, 104.0, ip="140.127.1.1")  # 0.8 s: below 1 s
    assert len(politeness(f)) == 1


def test_politeness_checks_a_hosts_crawl_delay(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    f = crawler.frontier
    f.set_host("fine-arts.nknu.edu.tw", queue="ip:1", crawl_delay=20.0)
    _req(f, "ip:1", "https://fine-arts.nknu.edu.tw/a", 100.0, 101.0, ip="140.127.5.5")
    _req(f, "ip:1", "https://fine-arts.nknu.edu.tw/b", 105.0, 106.0, ip="140.127.5.5")
    assert any("Crawl-delay" in x for x in politeness(f))


def test_the_url_table_has_a_sha256_index(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    assert "url_sha" in {r[1] for r in crawler.frontier.q("PRAGMA index_list(url)")}


def test_archive_run_refuses_while_another_program_holds_the_school_lock(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from nknu.admin.cli import app

    class NoCrawler:
        def __init__(self, *a, **k):
            raise AssertionError("built a crawler while another program held the school lock")

    monkeypatch.setattr(mod, "Crawler", NoCrawler)
    lock = schoollock.hold()
    try:
        out = CliRunner().invoke(app, ["archive", "run", "--dir", str(tmp_path)])
    finally:
        lock.close()
    assert out.exit_code == 1 and "学校" in out.output and str(__import__("os").getpid()) in out.output


# ---------------------------------------------------------------- B7: one request row per request on the wire


class WireClient:
    """A client whose answer carries what went out on the wire: a kept connection the server had closed (attempt 1),
    then the resend on a new connection (attempt 2)."""

    def __init__(self, clock):
        self.clock, self.seen = clock, []

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        self.seen.append(target.url)
        t0 = self.clock()
        self.clock.t += 2.5
        if target.url.endswith("/robots.txt") or "nknu-archive-probe" in target.url:
            return snap(target.url, 404)
        first = Sent(attempt=1, method="GET", started=t0, ended=t0 + 0.1, status=None, error="ReadError: reset",
                     bytes=None, new_conn=False, connect_ms=None, tls_ms=None, ip="140.127.62.110", idle_closed=True)
        second = Sent(attempt=2, method="GET", started=t0 + 2.2, ended=t0 + 2.5, status=200, error=None, bytes=9,
                      new_conn=True, connect_ms=3600.0, tls_ms=45.0, ip="140.127.62.110", warc_offset=1234,
                      warc_len=99)
        body = b"<p>ok</p>"
        return Snapshot(Target(target.url), target.url, 200, {"content-type": "text/html"}, body, "<p>ok</p>",
                        datetime.now(UTC), hashlib.sha256(body).hexdigest(), sent=(first, second),
                        warc_offset=1234, warc_len=99)

    def reset_host(self, host):
        pass

    def close(self):
        pass


def test_each_request_on_the_wire_has_its_row_and_an_idle_close_is_no_failure(tmp_path):
    crawler, _, clock = crawl(tmp_path, {})
    client = WireClient(clock)
    seed(crawler, HOME)
    drive(crawler, client, QC)
    page_reqs = crawler.frontier.q("SELECT * FROM request WHERE url=? ORDER BY id", (HOME,))
    assert [(r["attempt"], r["new_conn"], r["status"]) for r in page_reqs] == [(1, 0, None), (2, 1, 200)]
    second = page_reqs[1]
    assert (second["connect_ms"], second["tls_ms"], second["ip"], second["bytes"], second["warc_offset"]) == \
        (3600.0, 45.0, "140.127.62.110", 9, 1234)
    assert "ReadError" in page_reqs[0]["error"]
    row = rows(crawler)[HOME]
    assert (row["state"], row["warc_offset"], row["warc_len"]) == ("done", 1234, 99)
    assert crawler.frontier.host("c.nknu.edu.tw")["cooldown_s"] == 0 and not events(crawler, "host_cooldown")


def test_the_archive_client_sends_once_keeps_its_connection_and_uses_the_crawls_clock(tmp_path):
    crawler = mod.Crawler(tmp_path, plan=PLAN, clock=Clock(), campus_url=None)
    client = crawler._new_client(None)
    try:
        assert client._attempts == 1 and client._clock is crawler.clock
        for pool in (client._nknu, client._other):
            p = pool._transport._pool
            assert (p._max_connections, p._max_keepalive_connections, p._keepalive_expiry) == \
                (KEEPALIVE_LIMITS.max_connections, 1, 60)
        assert client._resend_gap_s == crawler.gap[0]
    finally:
        client.close()


# ---------------------------------------------------------------- short links (§7.7)

REURL = (Path(__file__).parent.parent / "fixtures" / "archive_shortlink" / "reurl_redirecting.html").read_text("utf-8")
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSdAbCdEfGhIjKlMnOp/viewform"


def _short(tmp_path, href, answer):
    host = scope.urlsplit(href).hostname
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(f'<a href="{href}">連結</a>'), host: answer},
                            queues={**QUEUES, host: f"ip:{host}"})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    drive(crawler, net, f"ip:{host}")
    return crawler, net


def test_a_short_links_redirect_is_followed_as_if_the_page_linked_its_target(tmp_path):
    crawler, net = _short(tmp_path, "https://forms.gle/AbCdEf123", snap("x", 302, headers={"location": FORM}))
    r = rows(crawler)
    short = r["https://forms.gle/AbCdEf123"]
    assert (short["final_url"], result(crawler, short["url"])) == (FORM, "redirect")
    form = r[FORM]
    assert (form["state"], form["kind0"], form["owner_site"], form["found_via"]) == \
        ("pending", "form", "c.nknu.edu.tw/aca", "redirect")
    assert net.seen.count("https://forms.gle/AbCdEf123") == 1  # one GET, its redirect never followed in place


def test_reurls_own_page_is_read_for_its_target(tmp_path):
    crawler, _ = _short(tmp_path, "https://reurl.cc/3Lp06M", lambda u: snap(u, html=REURL))
    short = rows(crawler)["https://reurl.cc/3Lp06M"]
    target = scope.canonical(round1.short_answer(short["url"], 200, None, REURL).target)
    assert short["final_url"] == target and result(crawler, short["url"]) == "redirect"
    assert links_of(crawler, short["url"])[target] == "out:social"  # it leads to a Facebook page: a link only


def test_a_short_link_that_answers_404_is_dead_and_one_unread_needs_a_person(tmp_path):
    crawler, _ = _short(tmp_path, "https://lurl.cc/Dead1", snap("x", 404))
    assert result(crawler, "https://lurl.cc/Dead1") == "short_dead"
    crawler, _ = _short(tmp_path / "b", "https://lihi1.cc/Odd1", page("<p>稍候</p>"))
    assert result(crawler, "https://lihi1.cc/Odd1") == "unhandled"


def test_a_short_link_to_drive_is_unhandled_until_the_google_step(tmp_path):
    to = "https://drive.google.com/file/d/1XyZ/view"
    crawler, _ = _short(tmp_path, "https://bit.ly/3xYz", snap("x", 301, headers={"location": to}))
    short = rows(crawler)["https://bit.ly/3xYz"]
    assert links_of(crawler, short["url"])[to] == "unhandled:google"
    got = crawler.frontier.q("SELECT result FROM page_resource WHERE page_id=? AND res_key=?",
                             (rows(crawler)[HOME]["id"], "https://bit.ly/3xYz"))[0][0]
    assert got == "unhandled"


# ---------------------------------------------------------------- login re-checks (§4.2)


@pytest.mark.parametrize("html, login", [('<form><input type="password"></form>', True),
                                         ("<p>無權限，請先登入</p>", True),
                                         ('<p>公開頁</p><a href="/aca/open.aspx">o</a>', False)])
def test_a_listed_login_url_gets_one_plain_get(tmp_path, html, login):
    url = "https://c.nknu.edu.tw/aca/admin_login.aspx"
    crawler, net, _ = crawl(tmp_path, {"admin_login": page(html)}, plan=plan(ACA, login_checks=(url,)))
    assert crawler.seed_login_check(url) == "queued"
    drive(crawler, net, QC)
    row = rows(crawler)[url]
    assert (row["kind0"], row["prio"], row["state"]) == ("login_check", 0, "done") and url in net.seen
    assert ("login" in (row["flags"] or "")) is login
    assert ("https://c.nknu.edu.tw/aca/open.aspx" in rows(crawler)) is (not login)
    assert scope_audit(crawler.frontier, crawler.plan) == []


# ---------------------------------------------------------------- forms, embed boxes, sitemaps (§4.4, §5 ⑥, §7.6)


def test_a_form_is_read_once_and_a_closed_one_says_so(tmp_path):
    closed = "https://docs.google.com/forms/d/e/1FAIpQLSdCl0sed/closedform"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(f'<a href="{FORM}">報名</a><a href="{closed}">舊</a>'),
                                       "docs.google.com": page('<a href="https://c.nknu.edu.tw/aca/x.aspx">x</a>')},
                            queues={**QUEUES, "docs.google.com": "google"})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    drive(crawler, net, "google")
    r = rows(crawler)
    assert (r[FORM]["kind"], r[FORM]["state"], result(crawler, FORM)) == ("embed", "done", "ok")
    assert r[closed]["flags"] == "form_closed" and "https://c.nknu.edu.tw/aca/x.aspx" not in r


def test_a_google_sites_embed_box_counts_on_the_page_that_embeds_it(tmp_path):
    home = "https://sites.google.com/mail.nknu.edu.tw/counseling/home"
    box = "https://abc123-atari-embeds.googleusercontent.com/embeds/16cb/inner.html"
    inner = '<a href="https://c.nknu.edu.tw/aca/uploadfile/form.pdf">表格</a><img src="https://lh3.example.com/p.png">'
    crawler, net, _ = crawl(tmp_path, {"counseling/home": page(f'<iframe src="{box}"></iframe>'),
                                       "atari-embeds": page(inner)},
                            queues={**QUEUES, "abc123-atari-embeds.googleusercontent.com": "google"})
    seed(crawler, home)
    drive(crawler, net, "google")
    drive(crawler, net, "google")
    r = rows(crawler)
    assert (r[box]["kind0"], r[box]["kind"]) == ("embed", "embed")
    pr = res(crawler, home)
    pdf = "https://c.nknu.edu.tw/aca/uploadfile/form.pdf"
    assert pr[pdf]["role"] == "file" and r[pdf]["found_from"] == r[home]["id"]
    assert r[pdf]["owner_site"] == "c.nknu.edu.tw/aca"  # 教務處's own file: its site's (round1.verdict r1)
    assert pr["https://lh3.example.com/p.png"]["role"] == "img"


def test_a_sitemap_named_in_robots_txt_is_read_and_its_addresses_queued(tmp_path):
    index = (b"<?xml version='1.0'?><sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
             b"<sitemap><loc>https://vd.nknu.edu.tw/post-sitemap.xml.gz</loc></sitemap></sitemapindex>")
    urls = (b"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            b"<url><loc>https://vd.nknu.edu.tw/2026/10/news/</loc></url>"
            b"<url><loc>https://vd.nknu.edu.tw/about/</loc></url></urlset>")
    crawler, net, _ = make(tmp_path, {
        "vd.nknu.edu.tw/robots.txt": snap("x", html="User-agent: *\nSitemap: https://vd.nknu.edu.tw/sitemap.xml\n"),
        "vd.nknu.edu.tw/sitemap.xml": lambda u: snap(u, body=index, headers={"content-type": "application/xml"}),
        "post-sitemap.xml.gz": lambda u: snap(u, body=gzip.compress(urls), headers={"content-type": "application/gzip"}),
        "vd.nknu.edu.tw/": page("<p>x</p>")}, plan=PLAN)
    seed(crawler, "https://vd.nknu.edu.tw/")
    settle(crawler, net, ["ip:vd.nknu.edu.tw"])
    r = rows(crawler)
    sm = r["https://vd.nknu.edu.tw/sitemap.xml"]
    assert (sm["kind0"], sm["prio"], sm["kind"], sm["sniff"]) == ("sitemap", 0, "file", "xml")
    assert r["https://vd.nknu.edu.tw/post-sitemap.xml.gz"]["kind0"] == "sitemap"
    assert r["https://vd.nknu.edu.tw/2026/10/news/"]["state"] == "done" and "https://vd.nknu.edu.tw/about/" in r


# ---------------------------------------------------------------- the scope audit reads what admit() said (B7)


def test_the_scope_audit_accepts_what_admit_let_in_and_flags_what_nothing_did(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(LINKS), "moe.gov.tw/files": lambda u: snap(u, body=PDF)},
                            queues={**QUEUES, "www.moe.gov.tw": "ip:www.moe.gov.tw"})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    drive(crawler, net, "ip:www.moe.gov.tw")
    assert "https://www.moe.gov.tw/files/rule.pdf" in net.seen
    assert scope_audit(crawler.frontier, crawler.plan) == []
    crawler.frontier.request("ip:x", "www.moe.gov.tw", "https://www.moe.gov.tw/news.html", "page", 1.0, 2.0, 200, None)
    crawler.frontier.request("ip:x", "sso.nknu.edu.tw", "https://sso.nknu.edu.tw/", "page", 3.0, 4.0, 200, None,
                             scope="admit:page")
    out = scope_audit(crawler.frontier, crawler.plan)
    assert len(out) == 2 and any("news.html" in x for x in out) and any("login" in x for x in out)


# ---------------------------------------------------------------- name lookups timed (§6.2), retry of later and held


def test_each_hosts_name_lookup_is_timed_when_resolved_and_again_every_hour(tmp_path):
    calls = []

    def resolver(h):
        calls.append(h)
        return QUEUES.get(h, f"ip:{h}"), None, h

    crawler, _, clock = crawl(tmp_path, {}, resolver=resolver)
    seed(crawler, HOME)
    assert crawler.frontier.host("c.nknu.edu.tw")["dns_ms"] is not None and calls == ["c.nknu.edu.tw"]
    assert not crawler._dns_step(QC)  # timed just now
    clock.t += mod.DNS_TIME_EVERY_S + 1
    assert crawler._dns_step(QC) and calls == ["c.nknu.edu.tw", "c.nknu.edu.tw"]
    assert crawler.frontier.host("c.nknu.edu.tw")["queue"] == QC  # timing a lookup never moves a host


def test_retry_brings_back_round_two_rows_and_rows_held_by_a_cap(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="https://www.moe.gov.tw/x.html">x</a>'
                                                                  '<a href="/aca/a.aspx">a</a>')}, site_cap=1)
    seed(crawler, HOME)
    one(crawler, net, QC)
    r = rows(crawler)
    assert r["https://c.nknu.edu.tw/aca/a.aspx"]["state"] == "held"
    assert crawler.frontier.retry("cap:") == (1, 1)
    assert rows(crawler)["https://c.nknu.edu.tw/aca/a.aspx"]["state"] == "pending"
    assert crawler.frontier.retry("round2") == (1, 1)
    assert rows(crawler)["https://www.moe.gov.tw/x.html"]["state"] == "pending"


def test_a_picture_only_a_handler_opens_is_still_the_pages_image(tmp_path):
    html = '<button onclick="window.open(\'/aca/uploadfile/map.jpg\')">地圖</button>'
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(html)})
    seed(crawler, HOME)
    one(crawler, net, QC)
    pic = "https://c.nknu.edu.tw/aca/uploadfile/map.jpg"
    assert rows(crawler)[pic]["kind0"] == "image" and res(crawler, HOME)[pic]["role"] == "img"
