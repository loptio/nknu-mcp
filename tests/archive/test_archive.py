import hashlib
import itertools
from datetime import UTC, datetime

import pytest

from nknu.archive import scope
from nknu.archive.crawler import Crawler
from nknu.archive.links import extract
from nknu.archive.monitor import politeness, report, scope_and_robots
from nknu.archive.robots import Robots, judge_answer
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import FetchError, HostCircuitOpen, Snapshot, TooLarge

# ---------------------------------------------------------------- scope


def test_canonical_spelling():
    c = scope.canonical
    # c.nknu is IIS: path and parameter names are case-insensitive there, so they have one (lowercase) spelling (M8)
    assert c("HTTPS://C.NKNU.edu.tw:443/aca/Page.aspx?PN=8#top") == "https://c.nknu.edu.tw/aca/page.aspx?pn=8"
    assert c("page.aspx?PN=2", "https://c.nknu.edu.tw/aca/Default.aspx") == "https://c.nknu.edu.tw/aca/page.aspx?pn=2"
    assert c("../x/./y.html", "https://lis.nknu.edu.tw/a/b/") == "https://lis.nknu.edu.tw/a/x/y.html"
    assert c("https://w3.nknu.edu.tw/?utm_source=fb&a=1&PHPSESSID=z") == "https://w3.nknu.edu.tw/?a=1"
    assert c("mailto:x@nknu.edu.tw") is None and c("#") is None and c("tel:07") is None
    # a file written raw and escaped is one URL
    raw = c("https://c.nknu.edu.tw/aca/UploadFile/招生目錄/U115b.pdf")
    esc = c("https://c.nknu.edu.tw/aca/UploadFile/%E6%8B%9B%E7%94%9F%E7%9B%AE%E9%8C%84/U115b.pdf")
    assert raw == esc


def test_sso_external_link_wrapper_is_unwrapped():
    href = ("/News/ExternalLink.aspx?url=https%3a%2f%2fc.nknu.edu.tw%2faca%2fUploadFile%2fuserfiles%2f4%2ffiles%2f"
            "%25E6%258B%259B%25E7%2594%259F%2fU115b.pdf")
    got = scope.canonical(href, "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1")
    assert got == "https://c.nknu.edu.tw/aca/uploadfile/userfiles/4/files/%E6%8B%9B%E7%94%9F/u115b.pdf"


@pytest.mark.parametrize("url, ok, reason", [
    ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1", True, "in_scope"),
    ("https://sites.google.com/mail.nknu.edu.tw/geo/home", True, "in_scope"),
    ("https://140.127.40.1/x", True, "in_scope"),
    ("https://sso.nknu.edu.tw/", False, "login"),
    ("https://sso.nknu.edu.tw/OSASys/Housing/StudentApplication.aspx", False, "login"),
    ("https://sso.nknu.edu.tw/Recruit/front.aspx?RegisterAction=K", False, "login"),
    ("https://flowing.nknu.edu.tw/EIP/NKNULogin", False, "login"),
    ("https://www.facebook.com/nknu", False, "social"),
    ("https://drive.google.com/file/d/x/view", False, "never_host"),
    ("https://calendar.google.com/calendar/ical/x/basic.ics", False, "never_host"),
    ("https://sites.google.com/view/other", False, "google_other"),
    ("https://www.moe.gov.tw/", False, "offsite"),
    ("https://c.nknu.edu.tw/aca/banner.jpg", True, "in_scope"),  # images are page resources (round-1 design §4.5)
    ("https://c.nknu.edu.tw/aca/clip.mp4", False, "media"),
])
def test_judge(url, ok, reason):
    v = scope.judge(url)
    assert (v.ok, v.reason) == (ok, reason)


def test_site_keys_and_kinds():
    assert scope.site_key("https://c.nknu.edu.tw/aca/Page.aspx?PN=1") == "c.nknu.edu.tw/aca"
    assert scope.site_key("https://sites.google.com/mail.nknu.edu.tw/Geo/home") == "sites.google.com/mail.nknu.edu.tw/geo"
    assert scope.site_key("https://lis.nknu.edu.tw/open-time") == "lis.nknu.edu.tw"
    assert scope.kind_of("https://c.nknu.edu.tw/a/x.PDF") == "file" and scope.kind_of("https://c.nknu.edu.tw/a/") == "page"


# ---------------------------------------------------------------- links

PAGE = """<html><head><title>教務處</title><base href="/aca/"><meta http-equiv="refresh" content="5; url=Default.aspx"></head>
<body><a href="Page.aspx?PN=1">規章</a><a href="javascript:__doPostBack('MasterGrid','Page$2')">2</a>
<area href="map.html" alt="地圖"><iframe src="https://sso.nknu.edu.tw/News/x.aspx"></iframe>
<button onclick="window.open('files/form.pdf')">表單</button></body></html>"""


def test_extract_links_postbacks_and_title():
    p = extract(PAGE, "https://c.nknu.edu.tw/aca/Default.aspx")
    assert p.base == "https://c.nknu.edu.tw/aca/" and p.title == "教務處" and not p.login_form
    hrefs = [h for h, _ in p.links]
    assert {"Page.aspx?PN=1", "map.html", "https://sso.nknu.edu.tw/News/x.aspx", "Default.aspx", "files/form.pdf"} <= set(hrefs)
    assert p.postbacks == ["MasterGrid|Page$2"]
    assert extract('<form><input type="password"></form>', "https://x.nknu.edu.tw/").login_form


# ---------------------------------------------------------------- robots


def test_robots_answers_follow_rfc_9309():
    assert judge_answer(404, None).allows("https://x.nknu.edu.tw/a")
    assert not judge_answer(503, None).allows("https://x.nknu.edu.tw/a")
    assert not judge_answer(None, None).allows("https://x.nknu.edu.tw/a")
    assert judge_answer(302, None).state == "none"  # a redirect not followed: unavailable = no rules (RFC 9309)
    r = judge_answer(200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 8\n")
    assert r.allows("https://x.nknu.edu.tw/a") and not r.allows("https://x.nknu.edu.tw/private/b")
    assert r.crawl_delay() == 8.0


# ---------------------------------------------------------------- crawler with a fake network and a fake clock


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def snap(url, status=200, html=None, headers=None, body=None):
    body = body if body is not None else (html or "").encode()
    hdrs = {"content-type": "text/html; charset=utf-8", **(headers or {})}
    return Snapshot(Target(url), url, status, hdrs, body, html, datetime.now(UTC), hashlib.sha256(body).hexdigest())


class Net:
    """route(url) -> Snapshot or exception; every request takes 1 s on the fake clock."""

    def __init__(self, clock, routes):
        self.clock, self.routes, self.seen = clock, routes, []

    def __call__(self, _warc):
        return self

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        self.seen.append(target.url)
        self.passed = pass_statuses
        self.clock.t += 1.0
        for key, out in self.routes.items():
            if key in target.url:
                if isinstance(out, Exception):
                    raise out
                return out(target.url) if callable(out) else out
        return snap(target.url, 404)

    def reset_host(self, host):
        pass

    def close(self):
        pass


class Sink:
    def __init__(self, root):
        self.path = root / "warc" / "q.warc.gz"


def make(tmp_path, routes, queues=None, **kw):
    """A crawler on the fake network: no DNS, no connectivity check, plenty of disk (each can be replaced)."""
    clock = Clock()
    net = Net(clock, routes)
    queues = queues or {}
    kw.setdefault("resolver", lambda h: (queues.get(h, f"ip:{h}"), None, h))
    kw.setdefault("netcheck", lambda: True)
    kw.setdefault("disk_free", lambda path: 10**12)
    crawler = Crawler(tmp_path, clock=clock, sleep=clock.sleep, client_factory=net, **kw)
    return crawler, net, clock


def drain(crawler, net, queue):
    sink = Sink(crawler.root)
    while (row := crawler.frontier.next_for(crawler.frontier.hosts_of(queue), crawler.clock())) is not None:
        crawler._process(queue, net, sink, row)


def seed(crawler, url, tier=1):
    return crawler.enqueue(url, unit="教務處", tier=tier, depth=0, found_via="seed", found_from=None)


def test_one_queue_waits_after_every_response_even_across_hosts(tmp_path):
    # c and aca share one server: their requests go one at a time with ≥ 2 s after each response
    home = '<a href="https://aca.nknu.edu.tw/b">b</a><a href="/c">c</a>'
    crawler, net, _clock = make(tmp_path, {"robots.txt": snap("x", 404), "/a": snap("https://c.nknu.edu.tw/a", html=home)},
                               queues={"c.nknu.edu.tw": "ip:1", "aca.nknu.edu.tw": "ip:1"})
    seed(crawler, "https://c.nknu.edu.tw/a")
    drain(crawler, net, "ip:1")
    assert politeness(crawler.frontier) == []
    reqs = crawler.frontier.q("SELECT started, ended FROM request ORDER BY started")
    assert len(reqs) >= 5 and all(b["started"] - a["ended"] >= 2.0 for a, b in itertools.pairwise(reqs))


def test_redirect_is_queued_as_its_own_url_not_followed(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404),
                                      "osa.nknu.edu.tw/": snap("https://osa.nknu.edu.tw/", 302,
                                                               headers={"location": "https://sso.nknu.edu.tw/osa/"})},
                           queues={"osa.nknu.edu.tw": "ip:pbws", "sso.nknu.edu.tw": "ip:sso"})
    seed(crawler, "https://osa.nknu.edu.tw/")
    drain(crawler, net, "ip:pbws")
    assert not any("sso.nknu.edu.tw/osa" in u for u in net.seen)  # not fetched on the pbws queue
    row = crawler.frontier.q("SELECT * FROM url WHERE url='https://sso.nknu.edu.tw/osa/'")[0]
    assert row["state"] == "pending" and row["found_via"] == "redirect" and row["tier"] == 1


def test_robots_disallow_and_unreachable(tmp_path):
    crawler, net, clock = make(tmp_path, {"a.nknu.edu.tw/robots.txt": snap("x", html="User-agent: *\nDisallow: /p\n"),
                                          "b.nknu.edu.tw/robots.txt": FetchError("timed out")})
    seed(crawler, "https://a.nknu.edu.tw/p/1")
    seed(crawler, "https://b.nknu.edu.tw/x")
    drain(crawler, net, "ip:a.nknu.edu.tw")
    drain(crawler, net, "ip:b.nknu.edu.tw")
    rows = {r["url"]: r for r in crawler.frontier.q("SELECT * FROM url")}
    assert rows["https://a.nknu.edu.tw/p/1"]["state"] == "skipped" and rows["https://a.nknu.edu.tw/p/1"]["reason"] == "robots"
    assert "https://a.nknu.edu.tw/p/1" not in net.seen
    b = rows["https://b.nknu.edu.tw/x"]
    assert b["state"] == "pending" and "https://b.nknu.edu.tw/x" not in net.seen
    assert crawler.frontier.host("b.nknu.edu.tw")["cooldown_until"] > clock()


def test_login_and_soft404_pages_are_archived_not_followed(tmp_path):
    missing = "<html><title>系統訊息</title>找不到網頁</html>"
    routes = {"robots.txt": snap("x", 404), "nknu-archive-probe": lambda u: snap(u, html=missing),
              "/member": snap("u", html='<form><input type="password"></form><a href="/deep">x</a>'),
              "/gone": lambda u: snap(u, html=missing), "/hub": lambda u: snap(u, html='<a href="/gone">old</a>')}
    crawler, net, _ = make(tmp_path, routes)
    seed(crawler, "https://z.nknu.edu.tw/member")
    seed(crawler, "https://z.nknu.edu.tw/hub")
    drain(crawler, net, "ip:z.nknu.edu.tw")
    rows = {r["url"]: r for r in crawler.frontier.q("SELECT * FROM url")}
    assert rows["https://z.nknu.edu.tw/member"]["flags"] == "login"
    assert rows["https://z.nknu.edu.tw/gone"]["flags"] == "soft404"
    assert "https://z.nknu.edu.tw/deep" not in rows


def test_links_offsite_social_and_postbacks_are_counted(tmp_path):
    html = ('<a href="https://www.moe.gov.tw/">教育部</a><a href="https://www.facebook.com/nknu">FB</a>'
            '<a href="javascript:__doPostBack(\'gvFull\',\'Page$2\')">2</a><a href="/next">下一頁</a>')
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=html)})
    seed(crawler, "https://c.nknu.edu.tw/aca/home")
    drain(crawler, net, "ip:c.nknu.edu.tw")
    f = crawler.frontier
    assert f.q("SELECT n FROM offsite WHERE domain='www.moe.gov.tw'")[0][0] == 1
    assert f.q("SELECT verdict FROM link WHERE dst LIKE '%facebook%'")[0][0] == "out:social"
    assert f.q("SELECT target FROM postback")[0][0] == "gvFull"
    assert f.q("SELECT state, depth FROM url WHERE url='https://c.nknu.edu.tw/next'")[0]["depth"] == 1


def test_failures_retry_later_then_fail(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/flaky": FetchError("HTTP 500")})
    seed(crawler, "https://f.nknu.edu.tw/flaky")
    for _ in range(3):
        drain(crawler, net, "ip:f.nknu.edu.tw")
        clock.t += 4000
    row = crawler.frontier.q("SELECT * FROM url WHERE url='https://f.nknu.edu.tw/flaky'")[0]
    assert row["state"] == "failed" and row["attempts"] == 3 and net.seen.count("https://f.nknu.edu.tw/flaky") == 3


def test_breaker_cools_the_host_down_without_logging_a_request(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/x": HostCircuitOpen("3 failures")})
    seed(crawler, "https://g.nknu.edu.tw/x")
    drain(crawler, net, "ip:g.nknu.edu.tw")
    assert crawler.frontier.host("g.nknu.edu.tw")["cooldown_until"] >= clock() + 800
    assert crawler.frontier.q("SELECT count(*) FROM request WHERE url='https://g.nknu.edu.tw/x'")[0][0] == 0
    assert crawler.frontier.q("SELECT state FROM url WHERE url='https://g.nknu.edu.tw/x'")[0][0] == "pending"


def test_too_large_and_caps(tmp_path):
    many = "".join(f'<a href="/p{i}">{i}</a>' for i in range(10))
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/big.pdf": TooLarge("too big"),
                                      "/home": lambda u: snap(u, html=many)}, site_cap=5)
    seed(crawler, "https://h.nknu.edu.tw/big.pdf")
    seed(crawler, "https://h.nknu.edu.tw/home")
    drain(crawler, net, "ip:h.nknu.edu.tw")
    f = crawler.frontier
    assert f.q("SELECT reason FROM url WHERE url LIKE '%big.pdf'")[0][0] == "too_large"
    assert f.q("SELECT count(*) FROM url WHERE reason='site_cap'")[0][0] >= 1


def test_a_stopped_run_resumes(tmp_path):
    crawler, _net, _ = make(tmp_path, {})
    seed(crawler, "https://r.nknu.edu.tw/")
    crawler.frontier.x("UPDATE url SET state='active'")
    assert crawler.frontier.requeue_active() == 1


# ---------------------------------------------------------------- monitor


def test_monitor_catches_impolite_and_out_of_scope_requests(tmp_path):
    crawler, _, _ = make(tmp_path, {})
    f = crawler.frontier
    f.request("ip:1", "c.nknu.edu.tw", "https://c.nknu.edu.tw/a", "page", 100.0, 101.0, 200, None)
    f.request("ip:1", "c.nknu.edu.tw", "https://c.nknu.edu.tw/b", "page", 101.5, 102.0, 200, None)  # 0.5 s gap
    f.request("ip:1", "c.nknu.edu.tw", "https://c.nknu.edu.tw/c", "page", 101.8, 103.0, 200, None)  # overlaps
    f.request("ip:2", "www.moe.gov.tw", "https://www.moe.gov.tw/", "page", 100.0, 101.0, 200, None)
    gaps = politeness(f)
    assert any("间隔不足" in g for g in gaps) and any("并发" in g for g in gaps)
    assert any("超出范围" in s for s in scope_and_robots(f))
    text, code = report(tmp_path)
    assert code == 2 and "❌" in text and (tmp_path / "reports" / "latest.md").exists()


def test_monitor_is_clean_after_a_polite_crawl(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/a": lambda u: snap(u, html='<a href="/b">b</a>')})
    seed(crawler, "https://c.nknu.edu.tw/a")
    drain(crawler, net, "ip:c.nknu.edu.tw")
    text, code = report(tmp_path)
    assert code == 0 and "✅" in text


def test_robots_body_round_trips_for_the_audit(tmp_path):
    assert Robots("rules", "User-agent: *\nDisallow: /\n").allows("https://x.nknu.edu.tw/") is False


def test_stop_never_cuts_a_wait_short_and_sends_nothing_more(tmp_path):
    # the pilot's limit stop once let in-flight pages send robots → probe → page back to back (0.00 s gaps)
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://s.nknu.edu.tw/a")
    row = crawler.frontier.next_for(["s.nknu.edu.tw"], clock())
    real_request = crawler._request

    def stop_after_first(*args, **kw):
        out = real_request(*args, **kw)
        crawler.stop.set()  # the limit was reached while this page was being processed
        return out

    crawler._request = stop_after_first
    sink = Sink(crawler.root)
    from nknu.archive.crawler import _Stopped

    with pytest.raises(_Stopped):
        crawler._process("ip:s.nknu.edu.tw", net, sink, row)
    assert net.seen == ["https://s.nknu.edu.tw/robots.txt"]  # robots went out, then nothing more
    reqs = crawler.frontier.q("SELECT ended FROM request")
    assert clock() - reqs[0]["ended"] >= 2.0  # and the wait after it was served in full


def test_a_member_login_box_does_not_stop_link_following(tmp_path):
    menu = "".join(f'<a href="/p{i}">選單{i}</a>' for i in range(20))
    home = f'<div class="sidebar"><form><input type="password"></form></div>{menu}'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=home)})
    seed(crawler, "https://elec.nknu.edu.tw/home")
    drain(crawler, net, "ip:elec.nknu.edu.tw")
    row = crawler.frontier.q("SELECT * FROM url WHERE url='https://elec.nknu.edu.tw/home'")[0]
    assert row["flags"] is None and row["reason"] == "has_login_box" and row["n_links"] == 20


def test_a_single_page_app_homepage_is_not_a_fake_page(tmp_path):
    # every path of such a host answers 200 with the same index page: the homepage is real, the copies are not
    index = "<html><title>雲端平台</title><div id=app></div><a href='/x'>x</a></html>"
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "ai.nknu.edu.tw": lambda u: snap(u, html=index)})
    seed(crawler, "https://ai.nknu.edu.tw/")
    drain(crawler, net, "ip:ai.nknu.edu.tw")
    rows = {r["url"]: r for r in crawler.frontier.q("SELECT * FROM url")}
    assert rows["https://ai.nknu.edu.tw/"]["flags"] is None
    assert rows["https://ai.nknu.edu.tw/x"]["flags"] == "soft404"


def test_any_4xx_for_robots_means_no_rules(tmp_path):
    # seducenter answered robots.txt with HTTP 400: RFC 9309 says crawl as if there were no rules
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 400), "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://seducenter.nknu.edu.tw/a")
    drain(crawler, net, "ip:seducenter.nknu.edu.tw")
    assert crawler.frontier.host("seducenter.nknu.edu.tw")["robots_state"] == "none"
    assert "https://seducenter.nknu.edu.tw/a" in net.seen


def test_a_host_unreachable_three_hours_running_waits_for_the_lab_machine(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": FetchError("Connection refused")})
    seed(crawler, "https://lend.nknu.edu.tw/")
    seed(crawler, "https://lend.nknu.edu.tw/other")
    for _ in range(3):
        drain(crawler, net, "ip:lend.nknu.edu.tw")
        clock.t += 3700
    states = {r["url"]: (r["state"], r["reason"]) for r in crawler.frontier.q("SELECT * FROM url")}
    assert set(states.values()) == {("failed", "host_unreachable")}
    assert net.seen.count("https://lend.nknu.edu.tw/robots.txt") == 3 and len(net.seen) == 3
