"""The second verification round of the archive crawler (2026-10-24): one or more tests per finding (the id is in the
name). Everything runs on a fake network and a fake clock, apart from one test that talks to a server of its own on
127.0.0.1; tests/archive/conftest.py refuses any other name lookup or connection."""

from __future__ import annotations

import itertools
import sqlite3
import threading

import httpx
import pytest

from nknu.archive import scope
from nknu.archive.frontier import Frontier
from nknu.archive.monitor import politeness, scope_and_robots
from nknu.archive.robots import Robots
from nknu.crawl.adapter import Target
from tests.archive.test_archive import Sink, make, seed, snap
from tests.archive.test_review_fixes import cli, conn_err, drive, events, html_response, real, rows


def settle(crawler, net, queues, rounds=80):
    """What run() does over time, without threads: every queue driven until nothing is due, then the clock jumps to
    the next time something is (a retry, a host's crawl delay, a pause), until nothing is left."""
    clock = crawler.clock
    for _ in range(rounds):
        for q in queues:
            drive(crawler, net, q)
        left, nxt = crawler.frontier.pending_left()
        if left == 0:
            return
        waits = [t for t in (nxt, crawler.frontier.paused_until("*"), *(crawler.frontier.paused_until(q) for q in queues))
                 if t and t > clock()]
        clock.t = (min(waits) if waits else clock()) + 1.0


# ---------------------------------------------------------------- F2: robots.txt on case-insensitive (IIS) servers

IIS_ROBOTS = "User-agent: *\nDisallow: /ACA/Private/\n"


def test_F2_robots_rules_match_whatever_the_case_on_iis_hosts():
    r = Robots("rules", IIS_ROBOTS)
    assert not r.allows("https://c.nknu.edu.tw/aca/private/x")  # the canonical (lowercase) spelling on c.nknu
    assert not r.allows("https://c.nknu.edu.tw/ACA/Private/x")
    assert not r.allows("https://dept.nknu.edu.tw/aca/private/page.aspx")  # an ASP.NET page on another school host
    assert r.allows("https://c.nknu.edu.tw/aca/public/x")
    assert r.allows("https://lis.nknu.edu.tw/aca/private/x")  # other servers keep RFC 9309's case-sensitive match
    assert not r.allows("https://lis.nknu.edu.tw/ACA/Private/x")
    assert not Robots("rules", "User-agent: *\nDisallow: /x?cat=News\n").allows("https://c.nknu.edu.tw/x?cat=News")
    assert Robots("rules", "User-agent: *\nDisallow: /a/\nAllow: /A/Open/\n").allows("https://c.nknu.edu.tw/a/open/x")


def test_F2_the_crawler_skips_and_the_monitor_flags_an_iis_page_robots_disallows(tmp_path):
    home = '<a href="/ACA/Private/x">x</a><a href="/ACA/Public/y">y</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", html=IIS_ROBOTS), "/aca/home": lambda u: snap(u, html=home)})
    seed(crawler, "https://c.nknu.edu.tw/aca/home")
    drive(crawler, net, "ip:c.nknu.edu.tw")
    r = rows(crawler)
    private = r["https://c.nknu.edu.tw/aca/private/x"]
    assert (private["state"], private["reason"]) == ("skipped", "robots")
    assert "https://c.nknu.edu.tw/aca/private/x" not in net.seen and r["https://c.nknu.edu.tw/aca/public/y"]["state"] == "done"
    assert scope_and_robots(crawler.frontier) == []
    sha = crawler.frontier.host("c.nknu.edu.tw")["robots_sha"]  # had it been fetched, the audit says so
    crawler.frontier.request("ip:c.nknu.edu.tw", "c.nknu.edu.tw", "https://c.nknu.edu.tw/aca/private/x", "page", 1.0, 2.0,
                             200, None, scope="in_scope", robots_sha=sha)
    assert scope_and_robots(crawler.frontier) == ["robots.txt 不允许却抓了：https://c.nknu.edu.tw/aca/private/x"]


# ---------------------------------------------------------------- F8: no idle connection left to a server


def test_F8_the_archive_client_keeps_no_connection_open_between_requests():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from nknu.archive.crawler import NO_RATE, archive_client

    accepted = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # the server would keep the connection open if the client let it

        def setup(self):
            accepted.append(self.client_address)
            super().setup()

        def do_GET(self):
            body = b"<p>ok</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = archive_client(None)
    try:
        for pool in (client._nknu, client._other):
            p = pool._transport._pool
            assert (p._max_connections, p._max_keepalive_connections) == (1, 0)
        url = f"http://127.0.0.1:{server.server_port}/a"
        for _ in range(2):
            assert client.fetch(Target(url), NO_RATE).status == 200
            assert client._other._transport._pool.connections == []  # closed as soon as the response ended
        assert len(accepted) == 2  # every request opened its own connection
    finally:
        client.close()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- F6: Crawl-delay is the host's, not the queue's


def test_F6_crawl_delay_is_capped_and_waited_by_the_host_not_by_its_server_queue(tmp_path):
    q = "ip:140.127.9.9"
    crawler, net, clock = make(tmp_path, {
        "slow.nknu.edu.tw/robots.txt": snap("x", html="User-agent: *\nCrawl-delay: 86400\n"),
        "fast.nknu.edu.tw/robots.txt": snap("x", 404), "/p": lambda u: snap(u, html="<p>x</p>")},
        queues={"slow.nknu.edu.tw": q, "fast.nknu.edu.tw": q})
    slept = []
    crawler.sleep = lambda s: (slept.append(s), clock.sleep(s))
    seed(crawler, "https://slow.nknu.edu.tw/p0")
    for i in range(3):
        seed(crawler, f"https://fast.nknu.edu.tw/p{i}", tier=2)
    drive(crawler, net, q)
    assert crawler.frontier.host("slow.nknu.edu.tw")["not_before"] > clock()
    assert list(crawler.frontier.queues(clock())) == []  # the slow host waits; nothing else is due
    settle(crawler, net, [q])
    assert max(slept) <= 5.0  # the queue's thread never sleeps longer than the 2–5 s gap: a stop is seen at once
    reqs = crawler.frontier.q("SELECT host, purpose, started, ended FROM request ORDER BY started")
    slow = [r for r in reqs if r["host"] == "slow.nknu.edu.tw"]
    assert [r["purpose"] for r in slow] == ["robots", "probe", "page"]
    assert all(300 <= b["started"] - a["ended"] < 310 for a, b in itertools.pairwise(slow))  # capped
    fast = [r for r in reqs if r["host"] == "fast.nknu.edu.tw"]
    assert len(fast) == 5 and fast[-1]["ended"] < slow[1]["started"]  # the other name went on at 2–5 s
    assert politeness(crawler.frontier) == [] and {x["state"] for x in rows(crawler).values()} == {"done"}


def test_F6_a_crawl_delay_stored_by_an_older_version_is_capped_too(tmp_path):
    assert Robots("rules", "User-agent: *\nCrawl-delay: 86400\n").crawl_delay() == 300.0
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/p": lambda u: snap(u, html="<p>x</p>")})
    seed(crawler, "https://old.nknu.edu.tw/p")
    crawler.frontier.set_host("old.nknu.edu.tw", robots_state="none", robots_at=clock(), crawl_delay=86400.0,
                              probe_status=404)
    drive(crawler, net, "ip:old.nknu.edu.tw")
    end = crawler.frontier.q("SELECT max(ended) FROM request")[0][0]
    assert crawler.frontier.host("old.nknu.edu.tw")["not_before"] == pytest.approx(end + 300)


# ---------------------------------------------------------------- P4 remainder: refusals counted per directory


def test_P4_one_protected_folder_cannot_stall_its_host(tmp_path):
    files = "".join(f'<a href="/aca/UploadFile/f{i}.pdf">{i}</a>' for i in range(25))
    pages = "".join(f'<a href="/tsi/p{i}.html">{i}</a>' for i in range(5))
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/aca/uploadfile/": lambda u: snap(u, 403, html="no"),
                                      "/aca/home": lambda u: snap(u, html=files), "/tsi/home": lambda u: snap(u, html=pages),
                                      "/tsi/p": lambda u: snap(u, html="<p>ok</p>")})
    seed(crawler, "https://c.nknu.edu.tw/aca/home", tier=1)
    seed(crawler, "https://c.nknu.edu.tw/tsi/home", tier=2)
    crawler.load_site_meta()
    settle(crawler, net, ["ip:c.nknu.edu.tw"])
    h = crawler.frontier.host("c.nknu.edu.tw")
    assert (h["cooldown_until"], h["cooldown_s"]) == (0, 0)  # the host itself never cools down
    pages_done = [u for u in net.seen if "/tsi/p" in u]
    files_seen = [u for u in net.seen if "/uploadfile/" in u]
    assert len(pages_done) == 5 and net.seen.index(pages_done[-1]) < net.seen.index(files_seen[10])
    assert len(files_seen) == 25 and all(r["status"] == 403 for u, r in rows(crawler).items() if "/uploadfile/" in u)
    denies = events(crawler, "host_denies")
    assert len(denies) == 2 and all("15 min" in m for m in denies)  # fixed: never 30 min, never a day


# ---------------------------------------------------------------- F5 / P5 remainder: Retry-After on the 404 probe


def test_F5_a_retry_after_on_the_probe_rests_the_whole_server_queue(tmp_path):
    state = {"busy": True}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path.startswith("/nknu-archive-probe-") and state["busy"]:
            return httpx.Response(503, headers={"retry-after": "3600"})
        return html_response()

    q = "ip:140.127.5.5"
    crawler, _, clock = make(tmp_path, {}, queues={"a.nknu.edu.tw": q, "b.nknu.edu.tw": q})
    seed(crawler, "https://a.nknu.edu.tw/p")
    seed(crawler, "https://b.nknu.edu.tw/p", tier=2)
    client = real(handler, clock)
    drive(crawler, client, q)
    reqs = crawler.frontier.q("SELECT url, purpose, ended FROM request ORDER BY id")
    assert [r["purpose"] for r in reqs] == ["robots", "probe"]  # no page request, nothing for b
    probe_end = reqs[-1]["ended"]
    until = crawler.frontier.paused_until(q)
    assert until >= probe_end + 3600
    a = rows(crawler)["https://a.nknu.edu.tw/p"]
    assert (a["state"], a["reason"], a["attempts"], a["next_at"]) == ("pending", "retry_after", 0, until)
    assert crawler.frontier.host("a.nknu.edu.tw")["probe_status"] is None
    assert events(crawler, "retry_after") and not events(crawler, "host_cooldown")
    clock.t = probe_end + 3599
    drive(crawler, client, q)
    assert crawler.frontier.q("SELECT count(*) FROM request")[0][0] == 2  # an hour of silence on that server
    state["busy"] = False
    clock.t = until + 1
    drive(crawler, client, q)
    assert {x["state"] for x in rows(crawler).values()} == {"done"}
    assert crawler.frontier.host("a.nknu.edu.tw")["probe_status"] == 200


# ---------------------------------------------------------------- M8 regression / F10: http and https stay two URLs


def test_M8_F10_both_spellings_are_queued(tmp_path):
    crawler, _, _ = make(tmp_path, {})
    assert seed(crawler, "http://x.nknu.edu.tw/a") == "queued"
    assert seed(crawler, "https://x.nknu.edu.tw/a") == "queued"
    assert set(rows(crawler)) == {"http://x.nknu.edu.tw/a", "https://x.nknu.edu.tw/a"}
    assert not hasattr(crawler.frontier, "upgrade_scheme") and not hasattr(scope, "other_scheme")


@pytest.mark.parametrize("serves", ["https", "http"])
def test_F10_a_host_that_speaks_one_scheme_gets_its_pages_whichever_spelling_came_first(tmp_path, serves):
    wrong = "http" if serves == "https" else "https"

    def handler(req):
        if req.url.scheme != serves:
            raise httpx.ConnectError("[Errno 61] Connection refused")
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/home":
            return html_response(f'<a href="{serves}://one.nknu.edu.tw/a">a</a>')
        if req.url.path == "/a":
            return html_response("<p>a</p>")
        return httpx.Response(404)

    crawler, _, clock = make(tmp_path, {})
    assert seed(crawler, f"{wrong}://one.nknu.edu.tw/a") == "queued"  # the wrong spelling is found first
    seed(crawler, f"{serves}://one.nknu.edu.tw/home")
    settle(crawler, real(handler, clock), ["ip:one.nknu.edu.tw"])
    r = rows(crawler)
    right = r[f"{serves}://one.nknu.edu.tw/a"]
    assert (right["state"], right["status"]) == ("done", 200)
    assert (r[f"{wrong}://one.nknu.edu.tw/a"]["state"], r[f"{wrong}://one.nknu.edu.tw/a"]["reason"]) == ("failed", "connect")
    assert crawler.frontier.host("one.nknu.edu.tw")["probe_status"] == 404  # the probe got its answer over the right scheme
    assert dict(crawler.frontier.q("SELECT dst, verdict FROM link"))[f"{serves}://one.nknu.edu.tw/a"] == "queued"


# ---------------------------------------------------------------- F3: only a short link lends its target the directory


def test_F3_a_directory_site_that_redirects_elsewhere_is_only_a_candidate(tmp_path):
    page = '<a href="https://old-center-nknu.com/">舊中心</a><a href="http://nknu-vd.com/">視設</a>'
    parked = "https://www.hugedomains.com/domain_profile.cfm?d=old-center-nknu.com"
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "center.html": lambda u: snap(u, html=page),
                                      "old-center-nknu.com/": snap("x", 301, headers={"location": parked}),
                                      "http://nknu-vd.com/": snap("x", 301, headers={"location": "https://www.nknu-vd.com/"})})
    crawler.enqueue("https://w3.nknu.edu.tw/center.html", unit="學校目錄", tier=0, depth=0, found_via="seed", found_from=None)
    for q in ("ip:w3.nknu.edu.tw", "ip:old-center-nknu.com", "ip:nknu-vd.com"):
        drive(crawler, net, q)
    assert set(crawler.frontier.directory_sites()) == {"old-center-nknu.com", "nknu-vd.com", "www.nknu-vd.com"}
    assert not any("hugedomains" in u for u in rows(crawler))
    assert crawler.frontier.q("SELECT n FROM offsite WHERE domain='www.hugedomains.com'")[0][0] == 1
    assert rows(crawler)["https://www.nknu-vd.com/"]["state"] == "pending"  # the same site under its www name


@pytest.mark.parametrize("url", ["https://lin.ee/VbHjerV", "https://discord.gg/abcd"])
def test_F3_line_and_discord_invitations_are_social_platforms(url):
    assert scope.judge(url).reason == "social" and not scope.directory_link_ok(url)


# ---------------------------------------------------------------- F4: telling our network from dead servers


def _f4_crawler(tmp_path, netcheck, extra_routes=None):
    dead = [f"dead{i}.example.org" for i in range(3)]
    school = "ip:140.127.1.1"
    queues = {"c.nknu.edu.tw": school, **{h: f"ip:203.0.113.{i}" for i, h in enumerate(dead)}}
    routes = {"c.nknu.edu.tw/robots.txt": snap("x", 404), **(extra_routes or {}),
              "c.nknu.edu.tw/": lambda u: snap(u, html="<p>ok</p>")}
    routes.update({f"{h}/": conn_err() for h in dead})
    crawler, net, clock = make(tmp_path, routes, queues=queues, netcheck=netcheck)
    for h in dead:
        crawler.frontier.add_directory_site(h, "https://w3.nknu.edu.tw/center.html", None, None)
    crawler.load_site_meta()
    for i in range(5):
        seed(crawler, f"https://c.nknu.edu.tw/p{i}")
    for h in dead:
        crawler.enqueue(f"https://{h}/", unit=None, tier=3, depth=1, found_via="link", found_from=None)
    return crawler, net, clock, school, [queues[h] for h in dead]


def test_F4_three_dead_off_campus_hosts_never_pause_the_school_queues(tmp_path):
    checks = []
    crawler, net, clock, school, dead = _f4_crawler(tmp_path, lambda: checks.append(1) or True)
    crawler._handle(school, net, Sink(tmp_path), crawler.frontier.next_for(["c.nknu.edu.tw"], clock()))
    clock.t += 130  # two minutes in which only the school answered
    for q in dead:
        drive(crawler, net, q)
    assert len(checks) >= 2  # three failing queues asked for a fresh check, and it passed
    assert crawler.frontier.q("SELECT count(*) FROM pause")[0][0] == 0 and not events(crawler, "offline")
    assert school in crawler.frontier.queues(clock())
    assert [crawler.frontier.host(f"dead{i}.example.org")["robots_fail"] for i in range(3)] == [1, 1, 1]  # kept


def test_F4_a_failed_check_pauses_only_the_network_that_does_not_answer(tmp_path):
    crawler, net, clock, school, dead = _f4_crawler(tmp_path, lambda: False,
                                                    {"c.nknu.edu.tw/p0": conn_err()})
    sink = Sink(tmp_path)
    crawler._handle(school, net, sink, crawler.frontier.next_for(["c.nknu.edu.tw"], clock()))  # robots, probe answer
    p0 = rows(crawler)["https://c.nknu.edu.tw/p0"]
    assert (p0["state"], p0["attempts"], p0["reason"]) == ("pending", 1, "connect")  # the school answered: p0's own
    drive(crawler, net, dead[0])
    assert crawler.frontier.paused_until("*") == 0
    assert crawler.frontier.paused_until(dead[1]) > clock() and crawler.frontier.paused_until(school) == 0
    assert school in crawler.frontier.queues(clock()) and dead[1] not in crawler.frontier.queues(clock())
    assert rows(crawler)["https://c.nknu.edu.tw/p0"]["attempts"] == 1  # the answering network's costs stay
    assert rows(crawler)["https://dead0.example.org/"]["reason"] == "net_down"
    assert any("校外" in m or "other" in m for m in events(crawler, "offline"))


def test_F4_a_real_outage_pauses_every_queue_and_then_resumes(tmp_path):
    up = {"net": False}

    def route(u):
        if not up["net"]:
            raise conn_err("[Errno 51] Network is unreachable")
        return snap(u, 404) if u.endswith("/robots.txt") else snap(u, html="<p>ok</p>")

    queues = {"s1.nknu.edu.tw": "ip:140.127.1.1", "s2.nknu.edu.tw": "ip:140.127.1.2", "o.example.org": "ip:203.0.113.9"}
    crawler, net, clock = make(tmp_path, {"://": route}, queues=queues, netcheck=lambda: up["net"])
    crawler.frontier.add_directory_site("o.example.org", "https://w3.nknu.edu.tw/center.html", None, None)
    crawler.load_site_meta()
    for h in queues:
        crawler.enqueue(f"https://{h}/a", unit=None, tier=1, depth=0, found_via="seed", found_from=None)
    drive(crawler, net, "ip:140.127.1.1")
    assert crawler.frontier.paused_until("*") > clock() and events(crawler, "offline")
    clock.t = crawler.frontier.paused_until("*") + 1
    assert crawler._paused()  # still down: five more minutes
    up["net"] = True
    clock.t = crawler.frontier.paused_until("*") + 1
    settle(crawler, net, list(dict.fromkeys(queues.values())))
    assert events(crawler, "online")
    assert {(x["state"], x["attempts"]) for x in rows(crawler).values()} == {("done", 0)}
    assert all(crawler.frontier.host(h)["robots_fail"] == 0 for h in queues)


# ---------------------------------------------------------------- F7: caps count what will be fetched


def test_F7_caps_count_only_the_rows_that_will_be_fetched(tmp_path):
    cal = "".join(f'<a href="/cal.php?d={i}">{i}</a>' for i in range(30))
    home = cal + '<a href="/about.html">about</a><a href="/rules.html">rules</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=home)},
                           site_cap=20, variant_cap=5)
    seed(crawler, "https://cap.nknu.edu.tw/home")
    drive(crawler, net, "ip:cap.nknu.edu.tw")
    r = rows(crawler)
    assert r["https://cap.nknu.edu.tw/about.html"]["state"] == "done" and r["https://cap.nknu.edu.tw/rules.html"]["state"] == "done"
    assert dict(crawler.frontier.q("SELECT reason, count(*) FROM url WHERE state='skipped' GROUP BY reason")) == {"variant_cap": 25}


def test_F7_the_cap_counts_use_an_index(tmp_path):
    from nknu.archive import frontier as mod

    f = Frontier(tmp_path / "frontier.db")
    for sql, args in ((mod.COUNT_SITE, ("s", "page")), (mod.COUNT_PATH, ("s", "p"))):
        plan = " ".join(r[3] for r in f.q("EXPLAIN QUERY PLAN " + sql, args))
        assert "COVERING INDEX" in plan, plan


# ---------------------------------------------------------------- M4 remainder: a charset no codec can use


@pytest.mark.parametrize("charset", ["undefined", "idna"])
def test_M4_a_charset_no_codec_can_use_still_gives_a_page_and_a_probe(tmp_path, charset):
    ctype = f"text/html; charset={charset}"

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/old":
            return httpx.Response(200, content=b'<a href="/next">next</a>\xff', headers={"content-type": ctype})
        return httpx.Response(404, content=b"<title>Not Found</title>", headers={"content-type": ctype})

    crawler, _, clock = make(tmp_path, {})
    seed(crawler, "https://cs.nknu.edu.tw/old")
    drive(crawler, real(handler, clock), "ip:cs.nknu.edu.tw")
    r = rows(crawler)
    assert (r["https://cs.nknu.edu.tw/old"]["state"], r["https://cs.nknu.edu.tw/old"]["flags"]) == ("done", "decode")
    assert "https://cs.nknu.edu.tw/next" in r
    h = crawler.frontier.host("cs.nknu.edu.tw")
    assert (h["probe_status"], h["probe_title"]) == (404, "Not Found")


def test_M4_the_probe_is_recorded_whatever_its_title_extraction_does(tmp_path, monkeypatch):
    from nknu.archive import crawler as mod

    def boom(*a, **k):
        raise ValueError("parser trouble")

    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404),
                                      "nknu-archive-probe": lambda u: snap(u, 404, html="<title>找不到</title>")})
    monkeypatch.setattr(mod, "extract", boom)
    seed(crawler, "https://pt.nknu.edu.tw/a")
    drive(crawler, net, "ip:pt.nknu.edu.tw")
    h = crawler.frontier.host("pt.nknu.edu.tw")
    assert (h["probe_status"], h["probe_title"]) == (404, "")


# ---------------------------------------------------------------- R9 remainder: archive retry never undoes good work


def test_R9_retry_refuses_a_short_prefix_and_leaves_good_pages_alone(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    for i, (state, status, reason) in enumerate((("done", 200, "has_login_box"), ("done", 403, "http_403"),
                                                 ("failed", None, "http_500"), ("done", 200, None))):
        f.add(f"https://rt.nknu.edu.tw/{i}", host="rt.nknu.edu.tw", site="rt.nknu.edu.tw", unit=None, tier=1,
              kind="page", depth=0, found_via="seed", found_from=None, state=state, reason=reason)
        f.x("UPDATE url SET status=? WHERE url=?", (status, f"https://rt.nknu.edu.tw/{i}"))
    before = f.q("SELECT url, state FROM url ORDER BY id")
    for prefix in ("", "h", "ht", "  "):
        out = cli("retry", prefix, "--dir", str(tmp_path))
        assert out.exit_code == 1 and "3" in out.output, prefix
    assert f.q("SELECT url, state FROM url ORDER BY id") == before
    assert cli("retry", "has_login_box", "--dir", str(tmp_path)).exit_code == 0
    assert f.q("SELECT state FROM url WHERE url='https://rt.nknu.edu.tw/0'")[0][0] == "done"
    assert cli("retry", "http_", "--dir", str(tmp_path)).exit_code == 0
    states = [r[0] for r in f.q("SELECT state FROM url ORDER BY id")]
    assert states == ["done", "pending", "pending", "done"]


# ---------------------------------------------------------------- F9: which pages are official directory pages


def test_F9_directory_pages_are_marked_and_lend_neither_tier_nor_name(tmp_path):
    from nknu.archive.seeds import CLUB_DIRECTORY, DIRECTORY, directory_seeds

    roster = ('<a href="https://nknu-club.example.com/">吉他社</a><a href="/OSA/Tutor/List.aspx">導師</a>'
              '<a href="https://www.instagram.com/x">ig</a>')
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "stuclub.aspx": lambda u: snap(u, html=roster),
                                      "life.nknu.edu.tw/": lambda u: snap(u, html='<a href="https://sso.nknu.edu.tw/'
                                                                           'OSA/Scholarship/List.aspx">獎學金</a>')})
    for s in directory_seeds():
        crawler.enqueue(s.url, unit=s.unit, tier=s.tier, depth=0, found_via=s.via, found_from=None)
    crawler.enqueue("https://life.nknu.edu.tw/", unit="生活輔導組", tier=2, depth=0, found_via="seed", found_from=None)
    crawler.load_site_meta()
    marked = {r[0] for r in crawler.frontier.q("SELECT url FROM url WHERE directory=1")}
    assert marked == {scope.canonical(u) for u in [*DIRECTORY, CLUB_DIRECTORY[0]]}  # not the SSO announcement lists
    assert "sso.nknu.edu.tw/osa" not in crawler.site_meta
    drive(crawler, net, "ip:sso.nknu.edu.tw")
    drive(crawler, net, "ip:life.nknu.edu.tw")
    r = rows(crawler)
    for url in ("https://sso.nknu.edu.tw/osa/tutor/list.aspx", "https://sso.nknu.edu.tw/osa/scholarship/list.aspx"):
        assert (r[url]["tier"], r[url]["unit"]) == (3, None), url
    assert "nknu-club.example.com" in crawler.frontier.directory_sites()


def test_F9_a_tier_0_page_that_is_not_a_directory_page_admits_no_off_campus_site(tmp_path):
    routes = {"robots.txt": snap("x", 404),
              "life.nknu.edu.tw/": snap("x", 302, headers={"location": "https://sso.nknu.edu.tw/OSA/Life.aspx"}),
              "osa/life.aspx": lambda u: snap(u, html='<a href="https://www.rent-house-example.com.tw/">租屋</a>'),
              "w3.nknu.edu.tw/news.html": lambda u: snap(u, html='<a href="https://shop-example.com/">店</a>')}
    crawler, net, _ = make(tmp_path, routes)
    crawler.enqueue("https://life.nknu.edu.tw/", unit="x", tier=0, depth=0, found_via="seed", found_from=None)
    crawler.enqueue("https://w3.nknu.edu.tw/news.html", unit="x", tier=0, depth=0, found_via="seed", found_from=None)
    for q in ("ip:life.nknu.edu.tw", "ip:sso.nknu.edu.tw", "ip:w3.nknu.edu.tw"):
        drive(crawler, net, q)
    assert crawler.frontier.directory_sites() == {}
    assert {r[0] for r in crawler.frontier.q("SELECT domain FROM offsite")} == {"www.rent-house-example.com.tw",
                                                                                "shop-example.com"}


def test_F9_a_directory_page_that_moved_stays_a_directory_page(tmp_path):
    routes = {"robots.txt": snap("x", 404),
              "w3.nknu.edu.tw/center.html": snap("x", 301, headers={"location": "https://w3.nknu.edu.tw/center2.html"}),
              "center2.html": lambda u: snap(u, html='<a href="https://pair-center.example.com/">中心</a>')}
    crawler, net, _ = make(tmp_path, routes)
    crawler.enqueue("https://w3.nknu.edu.tw/center.html", unit="學校目錄", tier=0, depth=0, found_via="seed", found_from=None)
    drive(crawler, net, "ip:w3.nknu.edu.tw")
    assert "pair-center.example.com" in crawler.frontier.directory_sites()


# ---------------------------------------------------------------- F11: normalisation that would merge different pages


def test_F11_iis_lowercases_only_the_names_of_name_value_pairs():
    c = scope.canonical
    assert c("https://c.nknu.edu.tw/aca/ShowFile.aspx?QmFzZTY0") == "https://c.nknu.edu.tw/aca/showfile.aspx?QmFzZTY0"
    assert c("https://c.nknu.edu.tw/aca/ShowFile.aspx?QmFzZTY0") != c("https://c.nknu.edu.tw/aca/ShowFile.aspx?qmfzzty0")
    assert c("https://c.nknu.edu.tw/aca/ShowFile.aspx?ZmlsZUE=").endswith("?ZmlsZUE=")
    assert c("https://c.nknu.edu.tw/aca/ShowFile.aspx?ZmlsZQ==").endswith("?ZmlsZQ==")
    assert c("https://c.nknu.edu.tw/aca/Page.aspx?PN=3&Sn=AbC").endswith("/aca/page.aspx?pn=3&sn=AbC")


def test_F11_an_external_link_is_unwrapped_only_when_its_target_is_unambiguous():
    base = "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1"

    def c(href):
        return scope.canonical(href, base)

    one = c("/News/ExternalLink.aspx?url=https://c.nknu.edu.tw/aca/Page.aspx?PN=3&Sn=17")
    two = c("/News/ExternalLink.aspx?url=https://c.nknu.edu.tw/aca/Page.aspx?PN=3&Sn=99")
    assert one != two and "/news/externallink.aspx?" in one and "/news/externallink.aspx?" in two
    assert c("/News/ExternalLink.aspx?url=https://c.nknu.edu.tw/aca/Page.aspx?PN=3") == "https://c.nknu.edu.tw/aca/page.aspx?pn=3"
    assert c("/News/ExternalLink.aspx?url=https%3a%2f%2flis.nknu.edu.tw%2fa%3fb%3d1&from=news") == "https://lis.nknu.edu.tw/a?b=1"


# ---------------------------------------------------------------- F12: two scope corner cases


@pytest.mark.parametrize("url, ok, reason", [
    ("https://sso.nknu.edu.tw/Default.aspx/x", False, "login"),
    ("https://sso.nknu.edu.tw/default.aspx/a/b?x=1", False, "login"),
    ("https://sso.nknu.edu.tw/Index.aspx/x", False, "login"),
    ("https://sso.nknu.edu.tw/Recruit/Statistics/ApplyStatisticsOpenData.aspx", True, "in_scope"),
    ("https://sso.nknu.edu.tw/Recruit/front.aspx?RegisterAction=K", False, "login"),
    ("https://sso.nknu.edu.tw/Recruit/StatisticsAdmin/x.aspx", False, "login"),
    ("https://sso.nknu.edu.tw/Recruit", False, "login"),
    ("https://sso.nknu.edu.tw/", False, "login"),
    ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1", True, "in_scope"),
])
def test_F12_login_pages_with_path_info_and_the_public_recruit_statistics(url, ok, reason):
    for u in (url, scope.canonical(url)):
        v = scope.judge(u)
        assert (v.ok, v.reason) == (ok, reason), u


# ---------------------------------------------------------------- F13: a mistyped directory is not an empty archive


@pytest.mark.parametrize("args", [("status",), ("retry", "http_5")])
def test_F13_status_and_retry_refuse_a_directory_without_a_frontier(tmp_path, args):
    typo = tmp_path / "typo"
    out = cli(*args, "--dir", str(typo))
    assert out.exit_code == 1 and str((typo / "frontier.db").resolve()) in out.output
    assert not typo.exists()


def test_F13_the_monitor_opens_the_frontier_read_only(tmp_path):
    conn = sqlite3.connect(tmp_path / "frontier.db")
    conn.executescript("""
        CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL,
          site TEXT NOT NULL, unit TEXT, tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
          found_via TEXT NOT NULL, found_from INTEGER, state TEXT NOT NULL DEFAULT 'pending', reason TEXT);
        CREATE TABLE host(host TEXT PRIMARY KEY, queue TEXT NOT NULL);""")
    conn.close()

    def columns():
        c = sqlite3.connect(tmp_path / "frontier.db")
        try:
            return {t: [r[1] for r in c.execute(f"PRAGMA table_info({t})")] for t in ("url", "host")}
        finally:
            c.close()

    before = columns()
    out = cli("status", "--dir", str(tmp_path))
    assert out.exit_code == 1 and "nknu archive seed" in out.output
    assert columns() == before  # a running crawl's database is never altered by the monitor
    crawler, _, _ = make(tmp_path / "current", {})
    seed(crawler, "https://ro.nknu.edu.tw/")
    ro = Frontier(tmp_path / "current" / "frontier.db", readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.x("UPDATE url SET state='done'")
    assert cli("status", "--dir", str(tmp_path / "current")).exit_code == 0
