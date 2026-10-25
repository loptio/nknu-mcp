"""The 2026-10-24 adversarial review of the archive crawler: one or more tests per finding (the id is in the name).
Everything runs on a fake network and a fake clock; nothing here talks to a school server."""

from __future__ import annotations

import hashlib
import itertools
import os
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from typer.testing import CliRunner

from nknu.archive import scope
from nknu.archive.frontier import Frontier
from nknu.archive.links import extract
from nknu.archive.monitor import report, scope_and_robots
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import FetchError, HostCircuitOpen, RetryableStatus, Snapshot
from tests.archive.test_archive import Sink, make, seed, snap

# ---------------------------------------------------------------- helpers


def conn_err(msg="[Errno 61] Connection refused"):
    exc = FetchError(f"https://x.nknu.edu.tw/: {msg}")
    exc.__cause__ = httpx.ConnectError(msg)
    return exc


def status_err(status):
    exc = FetchError(f"https://x.nknu.edu.tw/: HTTP {status}")
    exc.__cause__ = RetryableStatus(status)
    return exc


def raw(url, status=200, body=b"", headers=None):
    """A snapshot as the real client hands back what it did not decode: no text."""
    return Snapshot(Target(url), url, status, dict(headers or {}), body, None, datetime.now(UTC),
                    hashlib.sha256(body).hexdigest())


def drive(crawler, client, queue, sink=None):
    """What a queue's worker does, without its thread: rows one by one until the queue has nothing due or pauses."""
    sink = sink or Sink(crawler.root)
    while not crawler._paused() and crawler.frontier.paused_until(queue) <= crawler.clock():
        row = crawler.frontier.next_for(crawler.frontier.hosts_of(queue), crawler.clock())
        if row is None:
            return
        crawler._handle(queue, client, sink, row)


def rows(crawler):
    return {r["url"]: r for r in crawler.frontier.q("SELECT * FROM url")}


def events(crawler, code):
    return [r["message"] for r in crawler.frontier.q("SELECT message FROM event WHERE code=?", (code,))]


def cli(*args):
    from nknu.admin.cli import app

    return CliRunner().invoke(app, ["archive", *args])


def real(handler, clock, warc=None):
    """The archive crawl's own client (4xx as answers, no redirects followed, lenient decoding) on a fake network."""
    from nknu.archive.crawler import archive_client

    return archive_client(warc, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock)


def html_response(body="<p>ok</p>", status=200):
    return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"})


# ---------------------------------------------------------------- P1 = R4: one run per archive directory


def test_P1_a_second_run_refuses_while_the_first_holds_the_lock(tmp_path, monkeypatch):
    from nknu.archive import crawler as mod
    from nknu.archive.crawler import hold_run_lock

    crawler, _, _ = make(tmp_path, {})

    class NoRealCrawler:  # the command must refuse before it builds a crawler that would use the real network
        def __init__(self, *a, **k):
            raise AssertionError("archive run built a crawler while another run held the lock")

    monkeypatch.setattr(mod, "Crawler", NoRealCrawler)
    seed(crawler, "https://r.nknu.edu.tw/")
    crawler.frontier.x("UPDATE url SET state='active'")  # the first run is fetching it right now
    (tmp_path / "STOP").write_text("stop\n")
    lock = hold_run_lock(tmp_path)
    try:
        out = cli("run", "--dir", str(tmp_path))
        assert out.exit_code == 1 and str(os.getpid()) in out.output
        assert crawler.frontier.q("SELECT state FROM url")[0][0] == "active"  # not taken from the first run
        assert (tmp_path / "STOP").exists()  # only the lock holder clears a STOP
    finally:
        lock.close()


def test_P1_monitor_and_stop_go_by_the_lock(tmp_path, monkeypatch):
    from nknu.archive import crawler as mod
    from nknu.archive.monitor import _alive

    assert _alive(tmp_path) is None
    lock = mod.hold_run_lock(tmp_path)
    assert _alive(tmp_path) == str(os.getpid())
    monkeypatch.setattr(mod, "STOP_POLL_S", 0.05)
    threading.Timer(0.3, lock.close).start()  # the run finishes its requests in flight, then lets go
    out = cli("stop", "--dir", str(tmp_path))
    assert out.exit_code == 0 and "已停下" in out.output and (tmp_path / "STOP").exists()
    assert _alive(tmp_path) is None


# ---------------------------------------------------------------- P2 = R3: a request that went out is logged and waited for


def test_P2_a_request_that_reached_the_server_is_logged_and_waited_for_whatever_follows(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "d.nknu.edu.tw/a": OSError(5, "I/O error")})
    seed(crawler, "https://d.nknu.edu.tw/a")
    row = crawler.frontier.next_for(["d.nknu.edu.tw"], clock())
    with pytest.raises(OSError):
        crawler._process("ip:d.nknu.edu.tw", net, Sink(tmp_path), row)
    last = crawler.frontier.q("SELECT url, ended, error FROM request ORDER BY id DESC LIMIT 1")[0]
    assert last["url"] == "https://d.nknu.edu.tw/a" and "OSError" in last["error"]
    assert clock() - last["ended"] >= 2.0


def test_P2_a_warc_that_cannot_be_written_stops_the_whole_run(tmp_path):
    from nknu.crawl.fetch import ArchiveWriteError

    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "d.nknu.edu.tw/a": ArchiveWriteError("disk full", 200),
                                      "d.nknu.edu.tw/b": lambda u: snap(u, html="<p>b</p>")})
    seed(crawler, "https://d.nknu.edu.tw/a")
    seed(crawler, "https://d.nknu.edu.tw/b")
    crawler._work("ip:d.nknu.edu.tw")
    assert crawler.stop.is_set() and events(crawler, "warc_write")
    assert rows(crawler)["https://d.nknu.edu.tw/a"]["state"] == "pending"
    assert "https://d.nknu.edu.tw/b" not in net.seen
    assert crawler.frontier.q("SELECT status FROM request WHERE url='https://d.nknu.edu.tw/a'")[0][0] == 200


def test_P2_low_disk_stops_the_run_before_any_request(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404)}, disk_free=lambda path: 1024**3)
    seed(crawler, "https://d.nknu.edu.tw/a")
    crawler._work("ip:d.nknu.edu.tw")
    assert crawler.stop.is_set() and net.seen == [] and events(crawler, "disk_low")
    assert rows(crawler)["https://d.nknu.edu.tw/a"]["state"] == "pending"


# ---------------------------------------------------------------- P3: robots.txt whatever its Content-Type


@pytest.mark.parametrize("headers", [{"content-type": "application/octet-stream"}, {}])
def test_P3_robots_rules_apply_whatever_the_content_type(tmp_path, headers):
    body = b"User-agent: *\nDisallow: /\n"
    crawler, net, _ = make(tmp_path, {"robots.txt": raw("https://p.nknu.edu.tw/robots.txt", body=body, headers=headers)})
    seed(crawler, "https://p.nknu.edu.tw/private/page.html")
    drive(crawler, net, "ip:p.nknu.edu.tw")
    row = rows(crawler)["https://p.nknu.edu.tw/private/page.html"]
    assert (row["state"], row["reason"]) == ("skipped", "robots")
    assert net.seen == ["https://p.nknu.edu.tw/robots.txt"]
    assert "Disallow: /" in crawler.frontier.host("p.nknu.edu.tw")["robots_body"]


def test_P3_a_robots_txt_with_no_rule_in_it_is_logged(tmp_path):
    page = raw("x", body=b"<html><body>Server Error in '/' Application.</body></html>", headers={"content-type": "text/html"})
    crawler, net, _ = make(tmp_path, {"robots.txt": page, "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://pb.nknu.edu.tw/a")
    drive(crawler, net, "ip:pb.nknu.edu.tw")
    assert rows(crawler)["https://pb.nknu.edu.tw/a"]["state"] == "done" and events(crawler, "robots_unparsed")


# ---------------------------------------------------------------- P4 = R2 = M5: 4xx is an answer; cool-downs reset


def test_P4_a_4xx_page_is_an_archived_answer_not_a_failure(tmp_path):
    from warcio.archiveiterator import ArchiveIterator

    from nknu.crawl.warc import WarcSink

    def handler(req):
        if req.url.path.startswith("/upload/"):
            return html_response("forbidden", 403)
        if req.url.path.startswith("/p"):
            return html_response()
        return httpx.Response(404)

    crawler, _, clock = make(tmp_path, {})
    for i in range(3):
        seed(crawler, f"https://k.nknu.edu.tw/upload/{2020 + i}/")
    for i in range(5):
        seed(crawler, f"https://k.nknu.edu.tw/p{i}")
    sink = WarcSink(tmp_path / "warc", "q", 1, datetime.now(UTC))
    drive(crawler, real(handler, clock, warc=sink), "ip:k.nknu.edu.tw", sink)
    sink.close()
    r = rows(crawler)
    forbidden = [r[f"https://k.nknu.edu.tw/upload/{2020 + i}/"] for i in range(3)]
    assert all((x["state"], x["status"], x["reason"]) == ("done", 403, "http_403") for x in forbidden)
    assert all(r[f"https://k.nknu.edu.tw/p{i}"]["state"] == "done" for i in range(5))
    assert not events(crawler, "host_cooldown")
    assert crawler.frontier.q("SELECT count(*) FROM request WHERE status=403")[0][0] == 3  # each asked once
    with open(sink.path, "rb") as fh:
        statuses = [rec.http_headers.get_statuscode() for rec in ArchiveIterator(fh) if rec.rec_type == "response"]
    assert statuses.count("403") == 3


def test_P4_the_cooldown_starts_again_at_15_minutes_once_the_host_answers(tmp_path):
    state = {"open": True}

    def page(u):
        if state["open"]:
            raise HostCircuitOpen("g.nknu.edu.tw: 3 consecutive failures")
        return snap(u, html="<p>ok</p>")

    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "g.nknu.edu.tw/x": page})
    for i in range(3):
        seed(crawler, f"https://g.nknu.edu.tw/x{i}")
    q, sink = "ip:g.nknu.edu.tw", Sink(tmp_path)

    def one():
        crawler._handle(q, net, sink, crawler.frontier.next_for(["g.nknu.edu.tw"], clock()))

    one()
    assert crawler.frontier.host("g.nknu.edu.tw")["cooldown_s"] == 900
    clock.t += 1000
    state["open"] = False
    one()
    assert crawler.frontier.host("g.nknu.edu.tw")["cooldown_s"] == 0
    state["open"] = True
    one()
    assert crawler.frontier.host("g.nknu.edu.tw")["cooldown_s"] == 900  # not 1800: the openings were not in a row


def test_P4_a_directory_that_denies_ten_urls_in_a_row_cools_down(tmp_path):
    # second round: the streak is counted per directory, and that directory's URLs wait (a fixed 15 min), not the host
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "w.nknu.edu.tw/d": lambda u: snap(u, 403, html="no")})
    for i in range(12):
        seed(crawler, f"https://w.nknu.edu.tw/d{i}")
    drive(crawler, net, "ip:w.nknu.edu.tw")
    assert events(crawler, "host_denies")
    waiting = crawler.frontier.q("SELECT next_at FROM url WHERE status IS NULL")
    assert len(waiting) == 2 and all(r["next_at"] >= clock() + 890 for r in waiting)
    assert crawler.frontier.q("SELECT count(*) FROM url WHERE status=403")[0][0] == 10  # the rest waits


# ---------------------------------------------------------------- P5 = R8: Retry-After rests the whole server


def test_P5_retry_after_rests_the_whole_server_queue(tmp_path):
    from nknu.crawl.fetch import RetryLater

    busy = RetryLater("https://a.nknu.edu.tw/p0: HTTP 503 with Retry-After 3600 s", 3600.0)
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "a.nknu.edu.tw/p": busy,
                                          "b.nknu.edu.tw/p": lambda u: snap(u, html="<p>b</p>")},
                               queues={"a.nknu.edu.tw": "ip:1", "b.nknu.edu.tw": "ip:1"})
    seed(crawler, "https://a.nknu.edu.tw/p0")
    seed(crawler, "https://b.nknu.edu.tw/p0")
    start = clock()
    drive(crawler, net, "ip:1")
    assert "https://b.nknu.edu.tw/p0" not in net.seen  # the other name on that server waits as well
    assert crawler.frontier.paused_until("ip:1") >= start + 3600 and crawler.frontier.queues(clock()) == {}
    a = rows(crawler)["https://a.nknu.edu.tw/p0"]
    assert (a["state"], a["attempts"], a["reason"]) == ("pending", 0, "retry_after") and a["next_at"] >= start + 3600
    assert any("60 min" in m for m in events(crawler, "retry_after"))


def test_P5_a_busy_robots_txt_is_not_an_unreachable_host(tmp_path):
    from nknu.crawl.fetch import RetryLater

    busy = RetryLater("https://a.nknu.edu.tw/robots.txt: HTTP 503 with Retry-After 3600 s", 3600.0)
    crawler, net, clock = make(tmp_path, {"robots.txt": busy})
    seed(crawler, "https://a.nknu.edu.tw/x")
    drive(crawler, net, "ip:a.nknu.edu.tw")
    h = crawler.frontier.host("a.nknu.edu.tw")
    assert h["robots_state"] is None and h["robots_fail"] == 0
    assert rows(crawler)["https://a.nknu.edu.tw/x"]["state"] == "pending"
    assert crawler.frontier.paused_until("ip:a.nknu.edu.tw") >= clock() + 3000


def test_P5_a_breaker_refusal_on_robots_txt_sent_nothing_and_counts_nothing(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": HostCircuitOpen("a.nknu.edu.tw: 3 consecutive failures")})
    seed(crawler, "https://a.nknu.edu.tw/x")
    drive(crawler, net, "ip:a.nknu.edu.tw")
    h = crawler.frontier.host("a.nknu.edu.tw")
    assert h["robots_fail"] == 0 and h["robots_state"] is None and h["cooldown_until"] > clock()
    assert crawler.frontier.q("SELECT count(*) FROM request")[0][0] == 0


# ---------------------------------------------------------------- P6 = M3 = R5: DNS failures are transient


def test_P6_a_name_that_did_not_resolve_is_tried_again_and_its_urls_wait(tmp_path):
    answers = iter([("dns-fail", None, None)])
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "spe.nknu.edu.tw/": lambda u: snap(u, html="<p>x</p>")},
                               resolver=lambda h: next(answers, (f"ip:{h}", "140.127.1.1", h)))
    assert seed(crawler, "https://spe.nknu.edu.tw/a") == "queued"
    seed(crawler, "https://spe.nknu.edu.tw/b")
    r = rows(crawler)
    assert {x["state"] for x in r.values()} == {"pending"} and all(x["next_at"] > clock() for x in r.values())
    assert crawler.frontier.host("spe.nknu.edu.tw")["dns_fail"] == 1
    crawler._reresolve()
    assert crawler.frontier.host("spe.nknu.edu.tw")["queue"] == "dns-fail"  # not due yet
    clock.t += 601
    crawler._reresolve()
    assert crawler.frontier.host("spe.nknu.edu.tw")["queue"] == "ip:spe.nknu.edu.tw"
    drive(crawler, net, "ip:spe.nknu.edu.tw")
    assert {x["state"] for x in rows(crawler).values()} == {"done"}


def test_P6_dns_backs_off_to_six_hours_then_gives_up_so_the_run_can_end(tmp_path):
    from nknu.archive.crawler import DNS_GIVE_UP

    crawler, _, clock = make(tmp_path, {}, resolver=lambda h: ("dns-fail", None, None))
    seed(crawler, "https://gone.nknu.edu.tw/a")
    waits = []
    for _ in range(DNS_GIVE_UP + 2):
        row = rows(crawler)["https://gone.nknu.edu.tw/a"]
        if row["state"] != "pending":
            break
        waits.append(row["next_at"] - clock())
        clock.t = row["next_at"]
        crawler._reresolve()
    assert waits[:3] == [600, 1200, 2400] and max(waits) == 6 * 3600
    row = rows(crawler)["https://gone.nknu.edu.tw/a"]
    assert (row["state"], row["reason"]) == ("failed", "dns") and crawler.frontier.pending_left()[0] == 0


def test_P6_archive_retry_dns_lets_the_run_resolve_again(tmp_path):
    crawler, _, _clock = make(tmp_path, {}, resolver=lambda h: ("dns-fail", None, None))
    seed(crawler, "https://gone.nknu.edu.tw/a")
    crawler.frontier.x("UPDATE host SET dns_fail=8")
    crawler.frontier.x("UPDATE url SET state='failed'")
    out = cli("retry", "dns", "--dir", str(tmp_path))
    assert out.exit_code == 0
    assert crawler.frontier.host("gone.nknu.edu.tw")["dns_fail"] == 0
    crawler.resolver = lambda h: ("ip:9.9.9.9", "9.9.9.9", h)
    crawler._reresolve()
    row = rows(crawler)["https://gone.nknu.edu.tw/a"]
    assert crawler.frontier.host("gone.nknu.edu.tw")["queue"] == "ip:9.9.9.9"
    assert (row["state"], row["next_at"], row["reason"]) == ("pending", 0, None)


def test_P6_P15_resolve_takes_the_lowest_ipv4_and_never_raises(monkeypatch):
    from nknu.archive import crawler as mod

    infos = [(socket.AF_INET6, 1, 6, "", ("2001:db8::1", 443, 0, 0)),
             (socket.AF_INET, 1, 6, "lb.nknu.edu.tw", ("140.127.40.135", 443)),
             (socket.AF_INET, 1, 6, "", ("140.127.40.9", 443))]
    monkeypatch.setattr(mod.socket, "getaddrinfo", lambda *a, **k: infos)
    assert mod.resolve("x.nknu.edu.tw") == ("ip:140.127.40.9", "140.127.40.9", "lb.nknu.edu.tw")

    def bad(*a, **k):
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(mod.socket, "getaddrinfo", bad)
    assert mod.resolve("lis..nknu.edu.tw")[0] == "dns-fail"


# ---------------------------------------------------------------- P7 = R6: a link written wrong never fails its page


def _idna_resolver(host):
    host.encode("idna")  # what the real resolver does first: raises UnicodeError for an empty or too long label
    return f"ip:{host}", None, host


def test_P7_a_link_written_wrong_never_fails_its_page(tmp_path):
    bad = ["http://[網址]/", "http://www..nknu.edu.tw/", f"https://{'a' * 64}.nknu.edu.tw/", "http://x.nknu.edu.tw]/y",
           "http://[2001:db8::1/"]
    page = ('<a href="/before">b</a>' + "".join(f'<a href="{u}">x</a>' for u in bad)
            + '<a href="/after">a</a><iframe src="/frame"></iframe>')
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/hub": lambda u: snap(u, html=page)},
                           resolver=_idna_resolver)
    seed(crawler, "https://hub.nknu.edu.tw/hub")
    drive(crawler, net, "ip:hub.nknu.edu.tw")
    r = rows(crawler)
    assert r["https://hub.nknu.edu.tw/hub"]["state"] == "done"
    assert {"https://hub.nknu.edu.tw/before", "https://hub.nknu.edu.tw/after", "https://hub.nknu.edu.tw/frame"} <= set(r)
    verdicts = dict(crawler.frontier.q("SELECT dst, verdict FROM link"))
    assert verdicts["http://www..nknu.edu.tw/"] == "out:bad_host"


def test_P7_canonical_judge_and_links_never_raise():
    for u in ("http://[oops/", "https://c.nknu.edu.tw]/a", "http://[網址]/", "http://x.nknu.edu.tw:99999/"):
        assert scope.canonical(u, "https://c.nknu.edu.tw/a/") is None
        assert scope.judge(u).ok is False
    assert scope.site_key("http://[oops/") == ""
    assert extract('<base href="http://[oops/"><a href="x.html">x</a>', "https://c.nknu.edu.tw/a/").base \
        == "https://c.nknu.edu.tw/a/"


def test_P7_a_bad_robots_location_never_crashes_the_host(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 301, headers={"location": "https://www..nknu.edu.tw/robots.txt"}),
                                      "/a": lambda u: snap(u, html="<p>a</p>")}, resolver=_idna_resolver)
    seed(crawler, "https://rb.nknu.edu.tw/a")
    drive(crawler, net, "ip:rb.nknu.edu.tw")
    assert rows(crawler)["https://rb.nknu.edu.tw/a"]["state"] == "done"


# ---------------------------------------------------------------- P8 / P14: robots.txt redirects


def test_P8_robots_txt_redirecting_to_another_server_means_no_rules(tmp_path):
    to_sso = "https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=578"
    crawler, net, _ = make(tmp_path, {
        "mgt.nknu.edu.tw/robots.txt": snap("x", 302, headers={"location": "https://sso.nknu.edu.tw/robots.txt"}),
        "mgt.nknu.edu.tw/": snap("x", 302, headers={"location": to_sso})},
        queues={"mgt.nknu.edu.tw": "ip:pbws", "sso.nknu.edu.tw": "ip:sso"})
    seed(crawler, "https://mgt.nknu.edu.tw/")
    drive(crawler, net, "ip:pbws")
    assert not any("sso.nknu.edu.tw" in u for u in net.seen)  # the other server is not asked on this connection
    assert crawler.frontier.host("mgt.nknu.edu.tw")["robots_state"] == "none" and events(crawler, "robots_redirect")
    r = rows(crawler)
    assert r["https://mgt.nknu.edu.tw/"]["state"] == "done" and scope.canonical(to_sso) in r


def test_P8_same_server_robots_redirects_are_followed_five_hops(tmp_path):
    routes = {"robots.txt": snap("x", 302, headers={"location": "/r0.txt"})}
    routes.update({f"/r{i}.txt": snap("x", 302, headers={"location": f"/r{i + 1}.txt"}) for i in range(7)})
    routes["/a"] = lambda u: snap(u, html="<p>a</p>")
    crawler, net, _ = make(tmp_path, routes)
    seed(crawler, "https://hop.nknu.edu.tw/a")
    drive(crawler, net, "ip:hop.nknu.edu.tw")
    assert crawler.frontier.q("SELECT count(*) FROM request WHERE purpose='robots'")[0][0] == 6  # robots.txt + 5 hops
    assert crawler.frontier.host("hop.nknu.edu.tw")["robots_state"] == "none"
    assert rows(crawler)["https://hop.nknu.edu.tw/a"]["state"] == "done"


def test_P14_a_robots_redirect_to_a_login_page_is_not_followed(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 302, headers={"location": "/Login.aspx?ReturnUrl=%2frobots.txt"}),
                                      "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://lg.nknu.edu.tw/a")
    drive(crawler, net, "ip:lg.nknu.edu.tw")
    assert not any("login" in u.lower() for u in net.seen)
    assert crawler.frontier.host("lg.nknu.edu.tw")["robots_state"] == "none"


# ---------------------------------------------------------------- P9 = H2 / P14 / H1: what is never fetched


@pytest.mark.parametrize("url, ok, reason", [
    ("https://sites.google.com/mail.nknu.edu.tw/universitydepartment/home", False, "login"),
    ("https://sites.google.com/mail.nknu.edu.tw/nknu-departmentofgeography", False, "login"),
    ("https://sites.google.com/mail.nknu.edu.tw/universitydepartment2/home", True, "in_scope"),
    ("https://sso.nknu.edu.tw/default.aspx?home=my", False, "login"),
    ("https://sso.nknu.edu.tw/Default.aspx", False, "login"),
    ("http://sso.nknu.edu.tw/", False, "login"),
    ("https://sso.nknu.edu.tw/?lang=en", False, "login"),
    ("http://sso.nknu.edu.tw/OSASys/ST/StuAFL.aspx", False, "login"),
    ("https://sso.nknu.edu.tw/Recruit", False, "login"),
    ("http://acc.nknu.edu.tw/APSWIS_Q/x.aspx", False, "login"),
    ("https://sso.nknu.edu.tw//OSASys/ST/", False, "login"),
    ("https://sso.nknu.edu.tw/%4fSASys/Housing/", False, "login"),
    ("https://acc.nknu.edu.tw//apswis_q/", False, "login"),
    ("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=35", True, "in_scope"),
    ("https://sso.nknu.edu.tw/OSA/StuClub.aspx", True, "in_scope"),
])
def test_H2_P9_P14_login_systems_by_host_and_path_whatever_the_spelling(url, ok, reason):
    for u in (url, scope.canonical(url)):
        v = scope.judge(u)
        assert (v.ok, v.reason) == (ok, reason), u


@pytest.mark.parametrize("url, ok, reason", [
    ("https://pbws.nknu.edu.tw/NknuPublishServices.asmx", False, "internal_service"),
    ("https://pbws.nknu.edu.tw/NknuPublishServices.asmx?op=GetRoomUsageCurrent", False, "internal_service"),
    ("https://pbws.nknu.edu.tw/NknuPublishServices.asmx?WSDL", False, "internal_service"),
    ("https://pbws.nknu.edu.tw/", False, "internal_service"),
    ("https://priws.nknu.edu.tw/", False, "internal_service"),
    ("https://w3.nknu.edu.tw/x/ETP.ASMX/GetNews?x=1", False, "internal_service"),
    ("https://c.nknu.edu.tw/x/Service.svc?wsdl", False, "internal_service"),
    ("https://lis.nknu.edu.tw/index.php?op=view", True, "in_scope"),
    ("https://c.nknu.edu.tw/aca/Download.ashx?u=1", True, "in_scope"),
])
def test_H1_internal_web_services_are_never_fetched(url, ok, reason):
    v = scope.judge(url)
    assert (v.ok, v.reason) == (ok, reason)


def test_H1_a_service_row_already_in_the_frontier_is_skipped_without_a_request(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404)})
    crawler.frontier.add("https://pbws.nknu.edu.tw/NknuPublishServices.asmx", host="pbws.nknu.edu.tw",
                         site="pbws.nknu.edu.tw", unit=None, tier=4, kind="page", depth=0, found_via="seed", found_from=None)
    crawler.queue_of("pbws.nknu.edu.tw")
    drive(crawler, net, "ip:pbws.nknu.edu.tw")
    row = rows(crawler)["https://pbws.nknu.edu.tw/NknuPublishServices.asmx"]
    assert (row["state"], row["reason"]) == ("skipped", "out:internal_service") and net.seen == []


def test_H1_service_links_stay_out_of_the_link_table(tmp_path):
    page = '<a href="https://pbws.nknu.edu.tw/NknuPublishServices.asmx?op=X">op</a><a href="/ok">ok</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=page)})
    seed(crawler, "https://c.nknu.edu.tw/aca/home")
    drive(crawler, net, "ip:c.nknu.edu.tw")
    assert not crawler.frontier.q("SELECT 1 FROM link WHERE dst LIKE '%asmx%'")
    assert crawler.frontier.q("SELECT 1 FROM link WHERE dst='https://c.nknu.edu.tw/ok'")


# ---------------------------------------------------------------- M1: a site is what the platform calls a site


def test_M1_site_keys_follow_the_platforms():
    k = scope.site_key
    assert k("https://sso.nknu.edu.tw/Services/Web/Default.aspx?webIdx=595&menuId=1") == "sso.nknu.edu.tw/web/595"
    assert k("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WEBIDX=595") == "sso.nknu.edu.tw/web/595"
    assert k("https://sso.nknu.edu.tw/Services/Site/index.aspx?cLv2=12") == "sso.nknu.edu.tw/site/12"
    assert k("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1") == "sso.nknu.edu.tw/news"
    assert k("https://osa.nknu.edu.tw/ISRC/index.htm") == "osa.nknu.edu.tw/isrc"
    assert k("http://www.nknu.edu.tw/~badminton/") == "www.nknu.edu.tw/~badminton"
    assert k("https://www.nknu.edu.tw/x.html") == "www.nknu.edu.tw"


def test_M1_units_on_one_platform_path_share_neither_a_cap_nor_a_name(tmp_path):
    def unit(n, extra=""):
        menu = "".join(f'<a href="/Services/Web/Default.aspx?webIdx={n}&menuId={i}">m{i}</a>' for i in range(6))
        return lambda u: snap(u, html=menu + extra)

    routes = {"robots.txt": snap("x", 404), "webidx=1": unit(1, '<a href="/Services/Web/Default.aspx?webIdx=3">x</a>'),
              "webidx=2": unit(2), "webidx=3": lambda u: snap(u, html="<p>3</p>")}
    crawler, net, _ = make(tmp_path, routes, variant_cap=3)
    for n, name, tier in ((1, "秘書室", 1), (2, "衛生保健組", 2)):
        crawler.enqueue(scope.canonical(f"https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx={n}"), unit=name,
                        tier=tier, depth=0, found_via="seed", found_from=None)
    crawler.load_site_meta()
    drive(crawler, net, "ip:sso.nknu.edu.tw")
    r = rows(crawler)
    assert not [u for u, x in r.items() if x["reason"] == "variant_cap"]
    menu2 = r["https://sso.nknu.edu.tw/services/web/default.aspx?webidx=2&menuid=3"]
    assert (menu2["unit"], menu2["tier"], menu2["site"]) == ("衛生保健組", 2, "sso.nknu.edu.tw/web/2")
    other = r["https://sso.nknu.edu.tw/services/web/default.aspx?webidx=3"]  # a unit nobody seeded
    assert (other["unit"], other["tier"]) == (None, 3)


def test_M1_variant_cap_counts_per_site_and_path(tmp_path):
    cal = "".join(f'<a href="/cal.php?d={i}">{i}</a>' for i in range(10))
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=cal)}, variant_cap=3)
    seed(crawler, "https://geo.nknu.edu.tw/home")
    drive(crawler, net, "ip:geo.nknu.edu.tw")
    assert crawler.frontier.q("SELECT count(*) FROM url WHERE reason='variant_cap'")[0][0] == 7


def test_M1_attachments_have_their_own_cap(tmp_path):
    links = "".join(f'<a href="/f{i}.pdf">{i}</a>' for i in range(5)) + "".join(f'<a href="/p{i}">{i}</a>' for i in range(5))
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/home": lambda u: snap(u, html=links)},
                           site_cap=3, file_cap=4)
    seed(crawler, "https://att.nknu.edu.tw/home")
    drive(crawler, net, "ip:att.nknu.edu.tw")
    reasons = dict(crawler.frontier.q("SELECT reason, count(*) FROM url WHERE state='skipped' GROUP BY reason"))
    assert reasons == {"site_cap": 3, "file_cap": 1}


def test_M1_the_platform_path_is_not_reported_as_a_trap(tmp_path, monkeypatch):
    from nknu.archive import monitor

    monkeypatch.setattr(monitor, "TRAP_VARIANTS", 3)
    f = Frontier(tmp_path / "frontier.db")
    for i in range(5):
        for url in (f"https://sso.nknu.edu.tw/services/web/default.aspx?webidx=1&menuid={i}", f"https://geo.nknu.edu.tw/cal.php?d={i}"):
            f.add(url, host=scope.urlsplit(url).hostname, site=scope.site_key(url), unit=None, tier=3, kind="page", depth=1,
                  found_via="link", found_from=None)
    text, _ = report(tmp_path)
    assert "cal.php" in text and "default.aspx ×" not in text


# ---------------------------------------------------------------- M2: sites the official directory links to


CENTER = ('<a href="https://sites.google.com/view/taiwanology/home">超現代台灣學研究中心</a>'
          '<a href="https://pairnknu.wixstudio.io/pair-center/blank-2">閱讀評量與教學研究中心</a>'
          '<a href="https://cirn.k12ea.gov.tw/">k12ea</a><a href="https://cirn.moe.edu.tw/">moe</a>'
          '<a href="https://www.facebook.com/nknu">FB</a><a href="/edu_list.html">list</a>')


def test_M2_sites_an_official_directory_links_to_are_crawled(tmp_path):
    inner = '<a href="/view/taiwanology/about">about</a><a href="https://sites.google.com/view/other/x">o</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "center.html": lambda u: snap(u, html=CENTER),
                                      "taiwanology/home": lambda u: snap(u, html=inner)},
                           queues={"sites.google.com": "google"})
    crawler.enqueue("https://w3.nknu.edu.tw/center.html", unit="學校目錄", tier=0, depth=0, found_via="seed", found_from=None)
    drive(crawler, net, "ip:w3.nknu.edu.tw")
    r = rows(crawler)
    home = r["https://sites.google.com/view/taiwanology/home"]
    assert (home["state"], home["unit"], home["tier"]) == ("pending", "超現代台灣學研究中心", 3)
    assert "https://pairnknu.wixstudio.io/pair-center/blank-2" in r
    assert not any("gov.tw" in u or "moe.edu.tw" in u or "facebook" in u for u in r)
    sites = crawler.frontier.directory_sites()
    assert sites["sites.google.com/view/taiwanology"]["via_url"] == "https://w3.nknu.edu.tw/center.html"
    assert dict(crawler.frontier.q("SELECT dst, verdict FROM link"))["https://cirn.k12ea.gov.tw/"] == "out:offsite"
    drive(crawler, net, "google")
    r = rows(crawler)
    assert r["https://sites.google.com/view/taiwanology/about"]["state"] == "done"  # the site's own pages are crawled
    assert "https://sites.google.com/view/other/x" not in r  # a directory site's own links are not directory links
    assert scope_and_robots(crawler.frontier) == []
    text, _ = report(tmp_path)
    assert "目录直链" in text and "sites.google.com/view/taiwanology" in text


def test_M2_a_directory_short_link_admits_its_target_not_the_whole_short_link_service(tmp_path):
    page = '<a href="https://reurl.cc/3Lp06M">某中心</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "center.html": lambda u: snap(u, html=page),
                                      "reurl.cc/3Lp06M": snap("x", 302, headers={"location": "https://unit-x.wixsite.com/home"})})
    crawler.enqueue("https://w3.nknu.edu.tw/center.html", unit="學校目錄", tier=0, depth=0, found_via="seed", found_from=None)
    drive(crawler, net, "ip:w3.nknu.edu.tw")
    drive(crawler, net, "ip:reurl.cc")
    assert set(crawler.frontier.directory_sites()) == {"reurl.cc/3Lp06M", "unit-x.wixsite.com/home"}
    assert rows(crawler)["https://unit-x.wixsite.com/home"]["state"] == "pending"
    assert not scope.judge("https://reurl.cc/other", crawler.directory_sites).ok


# ---------------------------------------------------------------- M4 = R12: what cannot be decoded is still archived


def test_M4_a_page_that_cannot_be_decoded_strictly_is_kept_and_followed(tmp_path):
    page = '<a href="/next">下一頁</a>'.encode("cp950") + b"\xfa\x40"  # a user-defined Big5 character

    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(200, content=page, headers={"content-type": "text/html; charset=big5"})
        return httpx.Response(404)

    crawler, _, clock = make(tmp_path, {})
    seed(crawler, "https://big5.nknu.edu.tw/old")
    drive(crawler, real(handler, clock), "ip:big5.nknu.edu.tw")
    r = rows(crawler)
    assert (r["https://big5.nknu.edu.tw/old"]["state"], r["https://big5.nknu.edu.tw/old"]["flags"]) == ("done", "decode")
    assert "https://big5.nknu.edu.tw/next" in r


def test_M4_robots_txt_with_bytes_that_are_not_utf8_still_applies(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=b"# \xff\xfe\nUser-agent: *\nDisallow: /p\n", headers={"content-type": "text/plain"})
        return html_response()

    crawler, _, clock = make(tmp_path, {})
    seed(crawler, "https://rt.nknu.edu.tw/p/1")
    drive(crawler, real(handler, clock), "ip:rt.nknu.edu.tw")
    row = rows(crawler)["https://rt.nknu.edu.tw/p/1"]
    assert (row["state"], row["reason"]) == ("skipped", "robots")


# ---------------------------------------------------------------- M7 / P11 / R10: the report tells what went wrong


def test_M7_a_seed_that_ends_off_campus_or_at_a_login_is_a_problem(tmp_path):
    routes = {"robots.txt": snap("x", 404),
              "dept.nknu.edu.tw/VD": snap("x", 302, headers={"location": "https://www.nknu-vd.com/"}),
              "lab.nknu.edu.tw/": lambda u: snap(u, html='<form><input type="password"></form>'),
              "ok.nknu.edu.tw/": lambda u: snap(u, html="<p>ok</p>")}
    crawler, net, _ = make(tmp_path, routes)
    for u in ("https://dept.nknu.edu.tw/VD", "https://lab.nknu.edu.tw/", "https://ok.nknu.edu.tw/"):
        seed(crawler, u)
    for q in ("ip:dept.nknu.edu.tw", "ip:lab.nknu.edu.tw", "ip:ok.nknu.edu.tw"):
        drive(crawler, net, q)
    text, _ = report(tmp_path)
    assert "有问题 2" in text and "www.nknu-vd.com" in text
    thin = next(line for line in text.splitlines() if "只有 1–2 个网址的网站" in line)
    assert "dept.nknu.edu.tw" in thin


def test_P11_error_kinds():
    from nknu.archive.crawler import error_kind

    timeout = FetchError("u: timed out")
    timeout.__cause__ = httpx.ReadTimeout("timed out")
    tls = FetchError("u: tls")
    tls.__cause__ = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    assert error_kind(status_err(503)) == "http_503"
    assert error_kind(timeout) == "timeout" and error_kind(tls) == "tls"
    assert error_kind(conn_err("[Errno 8] nodename nor servname provided, or not known")) == "dns"
    assert error_kind(conn_err()) == "connect"
    assert error_kind(FetchError("https://x.nknu.edu.tw/: HTTP 403")) == "http_403"


def test_P11_failures_are_counted_by_kind_and_retried_by_kind(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "m.nknu.edu.tw/err": status_err(500)})
    for i in range(4):
        seed(crawler, f"https://m.nknu.edu.tw/err{i}")
    for _ in range(3):
        drive(crawler, net, "ip:m.nknu.edu.tw")
        clock.t += 4000
    assert {x["reason"] for x in rows(crawler).values()} == {"http_500"}
    text, _ = report(tmp_path)
    assert "http_500 × 4" in text
    out = cli("retry", "http_5", "--dir", str(tmp_path))
    assert out.exit_code == 0
    assert all((x["state"], x["attempts"]) == ("pending", 0) for x in rows(crawler).values())


def test_P11_reasons_written_the_old_way_are_grouped_too(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    for i in range(3):
        f.add(f"https://o.nknu.edu.tw/{i}", host="o.nknu.edu.tw", site="o.nknu.edu.tw", unit=None, tier=1, kind="page",
              depth=0, found_via="seed", found_from=None, state="failed", reason=f"https://o.nknu.edu.tw/{i}: HTTP 500")
    text, _ = report(tmp_path)
    assert "http_500 × 3" in text


def test_R10_stalled_queues_ignore_waits_and_are_not_hidden_by_a_cooling_neighbour(tmp_path):
    from nknu.archive.monitor import stalled_queues

    f = Frontier(tmp_path / "frontier.db")
    now = time.time()

    def add(url, host, queue, next_at=0.0, cooldown=0.0):
        f.set_host(host, queue=queue, cooldown_until=cooldown)
        f.add(url, host=host, site=host, unit=None, tier=1, kind="page", depth=0, found_via="seed", found_from=None)
        f.x("UPDATE url SET next_at=? WHERE url=?", (next_at, url))

    add("https://a.nknu.edu.tw/1", "a.nknu.edu.tw", "ip:2", next_at=now + 1800)  # only waits for its retry
    add("https://c1.nknu.edu.tw/1", "c1.nknu.edu.tw", "ip:3", cooldown=now + 72000)
    add("https://c2.nknu.edu.tw/1", "c2.nknu.edu.tw", "ip:3")  # due, and nothing went out for two hours
    for q in ("ip:2", "ip:3"):
        f.request(q, "x.nknu.edu.tw", "https://x.nknu.edu.tw/", "page", now - 7300, now - 7200, 200, None)
    assert stalled_queues(f, now) == ["ip:3"]
    f.pause("*", now + 300, "network down")
    assert stalled_queues(f, now) == []


# ---------------------------------------------------------------- R1: the network going down is nobody's fault


def test_R1_a_network_outage_pauses_every_queue_and_costs_nothing(tmp_path):
    # second round (F4): failing queues only ask for a fresh connectivity check; the pause needs that check to fail.
    # The first check (at the first error) still passes; the fresh one, once three queues failed, does not.
    hosts = ["n1.nknu.edu.tw", "n2.nknu.edu.tw", "n3.nknu.edu.tw"]
    checks = iter([True])
    crawler, net, clock = make(tmp_path, {"robots.txt": conn_err()}, netcheck=lambda: next(checks, False))
    for h in hosts:
        seed(crawler, f"https://{h}/a")
    for h in hosts:
        drive(crawler, net, f"ip:{h}")
    for h in hosts:
        row = crawler.frontier.host(h)
        assert (row["robots_fail"], row["robots_state"], row["cooldown_until"]) == (0, None, 0)
    assert all((x["state"], x["attempts"]) == ("pending", 0) for x in rows(crawler).values())
    assert crawler.frontier.paused_until("*") >= clock() + 200 and events(crawler, "offline")
    assert crawler.frontier.queues(clock()) == {}


def test_R1_a_failed_connectivity_check_pauses_at_the_first_error(tmp_path):
    crawler, net, clock = make(tmp_path, {"robots.txt": conn_err("[Errno 51] Network is unreachable")},
                               netcheck=lambda: False)
    seed(crawler, "https://n1.nknu.edu.tw/a")
    drive(crawler, net, "ip:n1.nknu.edu.tw")
    assert net.seen == ["https://n1.nknu.edu.tw/robots.txt"] and crawler.frontier.paused_until("*") > clock()
    assert crawler.frontier.host("n1.nknu.edu.tw")["robots_fail"] == 0


def test_R1_one_server_refusing_while_the_rest_answer_is_that_servers_problem(tmp_path):
    crawler, net, _ = make(tmp_path, {"ok.nknu.edu.tw/robots.txt": snap("x", 404), "robots.txt": conn_err()})
    seed(crawler, "https://ok.nknu.edu.tw/a")
    for h in ("n1", "n2", "n3"):
        seed(crawler, f"https://{h}.nknu.edu.tw/a")
    drive(crawler, net, "ip:ok.nknu.edu.tw")
    for h in ("n1", "n2", "n3"):
        drive(crawler, net, f"ip:{h}.nknu.edu.tw")
    assert crawler.frontier.paused_until("*") == 0
    assert [crawler.frontier.host(f"{h}.nknu.edu.tw")["robots_fail"] for h in ("n1", "n2", "n3")] == [1, 1, 1]


def test_R1_the_pause_ends_only_when_the_network_is_back(tmp_path):
    up = {"ok": False}
    crawler, _, clock = make(tmp_path, {}, netcheck=lambda: up["ok"])
    crawler.frontier.pause("*", clock() + 300, "network down")
    assert crawler._paused()
    clock.t += 301
    assert crawler._paused() and crawler.frontier.paused_until("*") > clock()  # still down: five more minutes
    clock.t += 301
    up["ok"] = True
    assert not crawler._paused() and events(crawler, "online")


# ---------------------------------------------------------------- R7: older frontier files


def test_R7_an_older_frontier_gets_the_new_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "frontier.db")
    conn.executescript("""
        CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL,
          site TEXT NOT NULL, unit TEXT, tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
          found_via TEXT NOT NULL, found_from INTEGER, state TEXT NOT NULL DEFAULT 'pending', reason TEXT,
          next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, status INTEGER, final_url TEXT,
          ctype TEXT, bytes INTEGER, sha256 TEXT, warc TEXT, title TEXT, flags TEXT, fetched_at TEXT, elapsed_ms INTEGER,
          n_links INTEGER, n_postbacks INTEGER);
        CREATE TABLE host(host TEXT PRIMARY KEY, queue TEXT NOT NULL, ip TEXT, cname TEXT, robots_state TEXT,
          robots_at REAL, robots_body TEXT, crawl_delay REAL, probe_status INTEGER, probe_sha TEXT, probe_len INTEGER,
          probe_title TEXT, cooldown_until REAL NOT NULL DEFAULT 0, cooldown_s REAL NOT NULL DEFAULT 0);
        CREATE TABLE request(id INTEGER PRIMARY KEY, queue TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL,
          purpose TEXT NOT NULL, started REAL NOT NULL, ended REAL NOT NULL, status INTEGER, error TEXT);
        INSERT INTO host(host, queue) VALUES ('old.nknu.edu.tw', 'ip:1');""")
    conn.close()
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://old.nknu.edu.tw/a")
    drive(crawler, net, "ip:1")
    assert rows(crawler)["https://old.nknu.edu.tw/a"]["state"] == "done"
    assert crawler.frontier.host("old.nknu.edu.tw")["robots_fail"] == 0


# ---------------------------------------------------------------- R9: archive retry really retries


def test_R9_retry_host_unreachable_reads_robots_again_at_once(tmp_path):
    state = {"down": True}

    def robots(u):
        if state["down"]:
            raise FetchError("https://lend.nknu.edu.tw/robots.txt: timed out")
        return snap(u, 404)

    crawler, net, clock = make(tmp_path, {"robots.txt": robots, "lend.nknu.edu.tw/": lambda u: snap(u, html="<p>x</p>")})
    seed(crawler, "https://lend.nknu.edu.tw/")
    seed(crawler, "https://lend.nknu.edu.tw/other")
    for i in range(3):
        drive(crawler, net, "ip:lend.nknu.edu.tw")
        if i < 2:
            clock.t += 3700
    assert {(x["state"], x["reason"]) for x in rows(crawler).values()} == {("failed", "host_unreachable")}
    state["down"] = False  # ten minutes later the host is back
    assert cli("retry", "host_unreachable", "--dir", str(tmp_path)).exit_code == 0
    drive(crawler, net, "ip:lend.nknu.edu.tw")
    assert {x["state"] for x in rows(crawler).values()} == {"done"}


def test_R9_retry_also_wakes_urls_that_wait(tmp_path):
    state = {"down": True}

    def robots(u):
        if state["down"]:
            raise FetchError("https://wk.nknu.edu.tw/robots.txt: timed out")
        return snap(u, 404)

    crawler, net, _ = make(tmp_path, {"robots.txt": robots, "wk.nknu.edu.tw/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://wk.nknu.edu.tw/a")
    drive(crawler, net, "ip:wk.nknu.edu.tw")
    assert rows(crawler)["https://wk.nknu.edu.tw/a"]["reason"] == "robots_unreachable"
    state["down"] = False
    assert cli("retry", "robots_unreachable", "--dir", str(tmp_path)).exit_code == 0
    drive(crawler, net, "ip:wk.nknu.edu.tw")
    assert rows(crawler)["https://wk.nknu.edu.tw/a"]["state"] == "done"


# ---------------------------------------------------------------- P12, P13, P16, R11: robots.txt details


def test_P12_the_request_after_robots_txt_already_waits_the_crawl_delay(tmp_path):
    # second round (F6): the host waits its Crawl-delay (host.not_before), not the queue's thread, so the row comes
    # back when the host is due; the clock is moved on to then, as the run's time would be
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", html="User-agent: *\nCrawl-delay: 30\n"),
                                          "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://cd.nknu.edu.tw/a")
    for _ in range(4):
        drive(crawler, net, "ip:cd.nknu.edu.tw")
        _, due = crawler.frontier.pending_left()
        clock.t = max(clock.t, due or 0) + 0.5
    reqs = crawler.frontier.q("SELECT started, ended FROM request ORDER BY id")
    assert len(reqs) == 3 and all(b["started"] - a["ended"] >= 30 for a, b in itertools.pairwise(reqs))


def test_P13_an_http_link_does_not_lose_an_https_only_host(tmp_path):
    crawler, net, _ = make(tmp_path, {"http://hs.nknu.edu.tw/robots.txt": conn_err(),
                                      "https://hs.nknu.edu.tw/robots.txt": snap("x", 404),
                                      "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "http://hs.nknu.edu.tw/a")
    drive(crawler, net, "ip:hs.nknu.edu.tw")
    assert crawler.frontier.host("hs.nknu.edu.tw")["robots_state"] == "none"
    assert rows(crawler)["http://hs.nknu.edu.tw/a"]["state"] == "done"


def test_P13_a_long_robots_txt_is_kept_to_512_kib(tmp_path):
    body = "User-agent: *\n" + "Disallow: /x\n" * 50_000
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", html=body)})
    seed(crawler, "https://lr.nknu.edu.tw/a")
    drive(crawler, net, "ip:lr.nknu.edu.tw")
    assert len(crawler.frontier.host("lr.nknu.edu.tw")["robots_body"]) == 512 * 1024


def test_P16_the_audit_uses_the_robots_txt_of_the_moment(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", html="User-agent: *\nDisallow: /private/\n"),
                                      "/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "https://rv.nknu.edu.tw/a")
    drive(crawler, net, "ip:rv.nknu.edu.tw")
    crawler.frontier.set_host("rv.nknu.edu.tw", robots_body="User-agent: *\nDisallow: /\n")  # the school changed it later
    assert scope_and_robots(crawler.frontier) == []


def test_R11_each_robots_body_is_parsed_once(monkeypatch):
    from nknu.archive import robots as mod

    calls = []
    real_parse = mod.Protego.parse
    monkeypatch.setattr(mod.Protego, "parse", lambda body: calls.append(1) or real_parse(body))
    r = mod.Robots("rules", "User-agent: *\nDisallow: /r11-only-here/\n")
    for i in range(100):
        r.allows(f"https://x.nknu.edu.tw/{i}")
    assert len(calls) <= 1


# ---------------------------------------------------------------- M8: one spelling per resource


def test_M8_one_spelling_per_iis_page():
    c = scope.canonical
    assert c("https://c.nknu.edu.tw/TSI/Default.aspx") == c("https://c.nknu.edu.tw/tsi/default.aspx")
    assert c("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=593") == \
        c("https://sso.nknu.edu.tw/services/web/default.aspx?webIdx=593")
    assert c("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=AbC9").endswith("lastnews.aspx?unid=AbC9")  # values kept
    assert c("https://reality.nknu.edu.tw/x/Page.aspx?A=1") == "https://reality.nknu.edu.tw/x/page.aspx?a=1"
    assert c("https://lis.nknu.edu.tw/Open-Time/") == "https://lis.nknu.edu.tw/Open-Time/"  # other servers keep case
    assert c("https://lis.nknu.edu.tw/a/%e9%81%8b.pdf") == c("https://lis.nknu.edu.tw/a/%E9%81%8B.pdf") \
        == c("https://lis.nknu.edu.tw/a/運.pdf")
    assert c("https://lis.nknu.edu.tw/%7Euser/%41") == "https://lis.nknu.edu.tw/~user/A"


def test_M8_http_and_https_are_two_urls(tmp_path):
    # second round (M8 regression, F10): the scheme is kept as written; a server's own redirect merges the two
    crawler, _, _ = make(tmp_path, {})
    assert seed(crawler, "http://x.nknu.edu.tw/a") == "queued"
    assert seed(crawler, "https://x.nknu.edu.tw/a") == "queued"
    assert set(rows(crawler)) == {"http://x.nknu.edu.tw/a", "https://x.nknu.edu.tw/a"}


def test_M8_a_redirect_between_schemes_is_followed_as_its_own_url(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404),
                                      "http://y.nknu.edu.tw/a": snap("x", 301, headers={"location": "https://y.nknu.edu.tw/a"}),
                                      "https://y.nknu.edu.tw/a": lambda u: snap(u, html="<p>a</p>")})
    seed(crawler, "http://y.nknu.edu.tw/a")
    drive(crawler, net, "ip:y.nknu.edu.tw")
    r = rows(crawler)
    assert (r["http://y.nknu.edu.tw/a"]["status"], r["https://y.nknu.edu.tw/a"]["status"]) == (301, 200)


def test_M8_sitemap_seeds_keep_the_seed_spelling():
    from nknu.archive.seeds import Seed, sitemap_seeds

    out = sitemap_seeds([Seed("https://c.nknu.edu.tw/TSI/Default.aspx", "x", 1)])
    assert [s.url for s in out] == [scope.canonical("https://c.nknu.edu.tw/TSI/SiteMap.aspx")]


# ---------------------------------------------------------------- L1


def test_L1_the_club_directory_is_a_seed():
    from nknu.archive.seeds import directory_seeds

    club = {s.url: s for s in directory_seeds()}[scope.canonical("https://sso.nknu.edu.tw/OSA/StuClub.aspx")]
    assert club.tier == 0 and "社團" in club.unit
