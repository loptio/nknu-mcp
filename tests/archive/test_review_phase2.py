"""The second phase's review (10-26, two reviewers): each finding that held, as a test written before its fix. A run
that never ends (rows `retry` brought back on hosts nobody looked up, a campus check on a paused queue, a row left
active), attempts used up while a whole server or the school's network is down, a server that errs or is too busy
resting as a whole, http and https (an expired certificate, an answer that is no proof), the scope audit (robots.txt
of a form's host, the placement a request was admitted under), the Crawl-delay on every request, a resource's redirect
through admit(), the campus check, the locks, seed and retry, and the seed report. All offline, on fake networks."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import Counter

import httpx
import pytest
from typer.testing import CliRunner

from nknu.archive import crawler as mod
from nknu.archive import round1, scope, seeds
from nknu.archive.frontier import Frontier, round_of
from nknu.archive.monitor import politeness, scope_audit
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Client, FetchError
from tests.archive.test_archive import Net, Sink, make, seed, snap
from tests.archive.test_crawler_round1 import (
    FORM,
    HOME,
    PLAN,
    QC,
    QS,
    QUEUES,
    REURL,
    conn_err,
    crawl,
    drive,
    events,
    page,
    result,
    rows,
    settle,
    status_err,
    timeout_err,
)

AE_HOME = "https://c.nknu.edu.tw/ae/"
W3 = "https://w3.nknu.edu.tw/"


# ---------------------------------------------------------------- a fast real clock, for run() and its threads


class FastClock:
    """Real time a thousand times faster: a 2–5 s gap is 2–5 ms, a 15 min pause 0.9 s, IDLE_EXIT_S 30 ms."""

    def __init__(self):
        self.base, self.start = 1_000_000.0, time.monotonic()

    def __call__(self):
        return self.base + (time.monotonic() - self.start) * 1000

    def sleep(self, s):
        time.sleep(max(s, 0.0) / 1000)


class FastNet:
    """Net's routes, safe for the worker threads; every answer at once."""

    def __init__(self, routes):
        self.routes, self.seen, self.lock = routes, [], threading.Lock()

    def __call__(self, _warc):
        return self

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        with self.lock:
            self.seen.append(target.url)
        for key, out in self.routes.items():
            if key in target.url:
                if isinstance(out, BaseException):
                    raise out
                return out(target.url) if callable(out) else out
        return snap(target.url, 404)

    def reset_host(self, host):
        pass

    def close(self):
        pass


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_POLL_S", 0.005)  # an idle worker looks again after 5 ms of real time


def fast(tmp_path, routes, queues=None, **kw):
    clock = FastClock()
    net = FastNet({"robots.txt": snap("x", 404), "nknu-archive-probe": snap("x", 404), **routes})
    queues = {**QUEUES, **(queues or {})}
    kw.setdefault("resolver", lambda h: (queues.get(h, f"ip:{h}"), None, h))
    kw.setdefault("plan", PLAN)
    kw.setdefault("campus_url", None)
    c = mod.Crawler(tmp_path, clock=clock, sleep=clock.sleep, client_factory=net, netcheck=lambda: True,
                    disk_free=lambda p: 10**12, **kw)
    return c, net, clock


def run_ends(crawler, secs=15.0) -> bool:
    """Whether run() ended by itself within secs of real time (else it is stopped)."""
    t = threading.Thread(target=crawler.run, kwargs={"poll_s": 0.005}, daemon=True)
    t.start()
    t.join(secs)
    if t.is_alive():
        crawler.halt("stop_file")
        t.join(10)
        return False
    return True


def run_end(crawler) -> dict:
    return json.loads(events(crawler, "run_end")[-1])


# ---------------------------------------------------------------- findings 1 and 13: retry brings rows back


def test_retry_round2_rows_whose_host_nobody_looked_up_are_looked_up_fetched_and_the_run_ends(tmp_path, quick):
    c, net, _ = fast(tmp_path, {AE_HOME: page("<p>成教系</p>")})
    assert c.seed_url(AE_HOME) == "round2:other_site" and c.frontier.host("c.nknu.edu.tw") is None
    assert c.frontier.retry("round2") == (1, 1)
    assert run_ends(c)
    assert rows(c)[AE_HOME]["state"] == "done" and AE_HOME in net.seen
    assert c.frontier.host("c.nknu.edu.tw")["queue"] == QC and run_end(c)["reason"] == "finished"


def test_retry_cap_rows_are_looked_up_fetched_and_the_run_ends(tmp_path, quick):
    c, _, _ = fast(tmp_path, {"/aca/default.aspx": page("<p>a</p>")}, site_cap=0)
    c.seed_url(HOME)
    assert (rows(c)[HOME]["state"], c.frontier.host("c.nknu.edu.tw")) == ("held", None)
    c.frontier.retry("cap:")
    assert run_ends(c) and rows(c)[HOME]["state"] == "done"


def test_a_host_the_old_archive_brought_queue_unknown_is_looked_up_and_never_fetched_on_a_queue_of_its_own(
        tmp_path, quick):
    """w3 and c.nknu answer from one server: w3's rows must go out on that server's queue, one request at a time."""
    c, _, _ = fast(tmp_path, {"c.nknu.edu.tw/aca/": page("<p>a</p>"), W3: page("<p>w3</p>")},
                     queues={"w3.nknu.edu.tw": QC})
    c.frontier.set_host("w3.nknu.edu.tw", answered_off=1)  # what seeds._old_hosts writes: queue '?'
    assert c.frontier.host("w3.nknu.edu.tw")["queue"] == "?"
    c.seed_url(W3)  # round two: later
    for i in range(5):
        c.seed_url(f"https://c.nknu.edu.tw/aca/p{i}.aspx")
    c.frontier.retry("round2")
    assert run_ends(c)
    assert rows(c)[W3]["state"] == "done" and c.frontier.host("w3.nknu.edu.tw")["queue"] == QC
    assert {r[0] for r in c.frontier.q("SELECT DISTINCT queue FROM request")} == {QC}


def test_a_row_brought_back_on_a_name_that_does_not_resolve_waits_for_its_name_and_the_run_ends(tmp_path, quick):
    c, _, clock = fast(tmp_path, {}, resolver=lambda h: ("dns-fail", None, None))
    c.seed_url(W3)
    c.frontier.set_host("w3.nknu.edu.tw", queue="dns-fail", dns_fail=2, dns_at=clock())
    c.frontier.retry("round2")  # the row's reason stays round2:js_site; its host's back-off is cleared
    assert run_ends(c)
    end = run_end(c)
    row = rows(c)[W3]
    assert end["reason"] == "waiting" and row["state"] == "pending" and row["next_at"] > clock()


# ---------------------------------------------------------------- findings 2 and 18: a server or the school down


def _down(crawler, net, clock, q, hours):
    start = clock()
    while clock() - start < hours * 3600:
        drive(crawler, net, q)
        _, nxt = crawler.frontier.pending_left()
        waits = [t for t in (nxt, crawler.frontier.paused_until(q)) if t and t > clock()]
        clock.t = (min(waits) if waits else clock()) + 1.0


def test_a_server_down_for_12_hours_costs_no_url_an_attempt(tmp_path):
    """B2: a server that answered before (off campus) and does not connect for hours: its rows wait, every one with
    all its attempts; one warning says so."""
    q = "ip:140.127.62.110"
    crawler, net, clock = make(tmp_path, {"robots.txt": conn_err()}, queues={"a.nknu.edu.tw": q, "b.nknu.edu.tw": q})
    for h in ("a", "b"):
        for i in range(5):
            seed(crawler, f"https://{h}.nknu.edu.tw/p{i}", tier=0)
        crawler.frontier.set_host(f"{h}.nknu.edu.tw", answered_off=1)
    _down(crawler, net, clock, q, 12)
    got = Counter((r["state"], r["attempts"]) for r in crawler.frontier.q("SELECT state, attempts FROM url"))
    assert got == Counter({("pending", 0): 10})
    assert len(events(crawler, "server_down")) == 1 and not events(crawler, "host_unreachable")


def test_the_school_network_down_for_6_hours_while_the_internet_works_costs_the_school_nothing(tmp_path):
    """B2: 4 school servers refuse every connection for 6 hours while an off-campus queue keeps answering. Nothing is
    held against the school's URLs or servers: no attempt, no lab, no failure streak."""
    school = {f"s{i}.nknu.edu.tw": f"ip:140.127.1.{i}" for i in range(1, 5)}
    other = {"files.example.org": "ip:93.184.216.34"}
    routes = {"files.example.org": lambda u: snap(u, html="<p>x</p>"), **{h: conn_err() for h in school}}
    crawler, net, clock = make(tmp_path, routes, queues={**school, **other})
    for h in school:
        for j in range(3):
            seed(crawler, f"https://{h}/p{j}")
    queues = [*school.values(), *other.values()]
    for step in range(360):
        for q in queues:
            drive(crawler, net, q)
        seed(crawler, f"https://files.example.org/f{step}.html")  # the other network keeps the run busy
        clock.t += 60
    school_rows = crawler.frontier.q("SELECT state, reason, attempts FROM url WHERE host LIKE 's_.nknu.edu.tw'")
    assert len(school_rows) == 12 and {(r["state"], r["attempts"]) for r in school_rows} == {("pending", 0)}
    assert all(crawler.frontier.server_fails(q) == 0 for q in school.values())
    assert not events(crawler, "host_unreachable")


# ---------------------------------------------------------------- findings 3 and 17: the campus check on a paused queue


def test_a_paused_sso_queue_with_the_campus_check_due_does_not_keep_the_run_going(tmp_path, quick):
    c, _, clock = fast(tmp_path, {}, campus_url=mod.CAMPUS_URL, local_address=lambda: "192.168.1.5")
    c.seed_url(HOME)
    c.frontier.x("UPDATE url SET attempts=1, reason='timeout', next_at=?", (clock() + 3600,))
    c.frontier.pause(QS, clock() + 86400, "Retry-After")
    assert run_ends(c, secs=5.0) and run_end(c)["reason"] == "waiting"


# ---------------------------------------------------------------- findings 4 and 16: a row left active


def test_a_crash_on_the_row_a_scheme_move_brought_ends_that_row_and_the_run(tmp_path, quick):
    http_home = "http://c.nknu.edu.tw/aca/default.aspx"
    c, _, _ = fast(tmp_path, {"/aca/default.aspx": ValueError("a bug while reading this page")})
    c.seed_url(http_home)
    c.frontier.set_host("c.nknu.edu.tw", scheme_only="https")  # learnt from another page earlier
    assert run_ends(c, secs=5.0)
    r = rows(c)
    assert (r[http_home]["state"], r[http_home]["reason"]) == ("skipped", "moved_scheme")
    assert (r[HOME]["state"], r[HOME]["reason"]) == ("failed", "crash:ValueError")


def test_a_row_left_active_with_no_worker_holding_it_is_a_crash_and_the_run_ends(tmp_path, quick):
    c, _, _ = fast(tmp_path, {"/aca/": page("<p>a</p>")})
    c.seed_url(HOME)
    c.seed_url("https://c.nknu.edu.tw/aca/b.aspx")
    real = c._process

    def buggy(queue, client, sink, row):  # a bug that leaves another row active
        c.frontier.x("UPDATE url SET state='active' WHERE url LIKE '%/b.aspx'")
        real(queue, client, sink, row)

    c._process = buggy
    assert run_ends(c, secs=5.0)
    b = rows(c)["https://c.nknu.edu.tw/aca/b.aspx"]
    assert (b["state"], b["reason"]) == ("failed", "crash:left_active") and events(c, "left_active")


# ---------------------------------------------------------------- findings 5 and 24: http and https


def tls_cert_err():
    exc = FetchError("https://x/: [SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired")
    exc.__cause__ = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has "
                                       "expired (_ssl.c:1000)")
    return exc


def test_an_expired_certificate_whose_http_only_sends_back_to_https_is_a_tls_failure_never_a_loop(tmp_path):
    news = "https://dept.nknu.edu.tw/news.html"

    def route(u):
        if u.startswith("https://"):
            raise tls_cert_err()
        if u.endswith("/robots.txt") or "nknu-archive-probe" in u:
            return snap(u, 404)
        return snap(u, 301, headers={"location": "https://" + u.split("://", 1)[1]})

    crawler, net, _ = make(tmp_path, {"://": route})
    seed(crawler, news)
    settle(crawler, net, ["ip:dept.nknu.edu.tw"])
    r = rows(crawler)
    assert crawler.frontier.host("dept.nknu.edu.tw")["scheme_only"] is None  # an http 404 or 301 proves nothing
    assert (r[news]["state"], r[news]["reason"], r[news]["attempts"]) == ("failed", "tls_cert", 3)
    assert "http://dept.nknu.edu.tw/news.html" not in r and result(crawler, news) == "failed"


def test_an_answer_on_the_other_scheme_that_is_no_page_moves_the_row_but_teaches_nothing_of_the_host(tmp_path):
    def route(u):
        if u.startswith("https://"):
            raise conn_err()
        return snap(u, 404)

    crawler, net, _ = make(tmp_path, {"one.nknu.edu.tw": route})
    seed(crawler, "https://one.nknu.edu.tw/a")
    settle(crawler, net, ["ip:one.nknu.edu.tw"])
    r = rows(crawler)
    assert (r["https://one.nknu.edu.tw/a"]["reason"], r["http://one.nknu.edu.tw/a"]["status"]) == ("moved_scheme", 404)
    assert crawler.frontier.host("one.nknu.edu.tw")["scheme_only"] is None
    assert crawler.frontier.paused_until("ip:one.nknu.edu.tw") == 0  # the server answered: no server pause


def test_a_closed_door_on_a_server_that_answers_costs_the_url_an_attempt_and_never_pauses_the_server(tmp_path):
    """https refused while http answers, but only to send the visitor back to https: the server is up, the address
    cannot be reached; the URL is tried again later and the server's other rows go on."""
    def route(u):
        if u.startswith("https://"):
            raise conn_err()
        if u.endswith("/robots.txt") or "nknu-archive-probe" in u:
            return snap(u, 404)
        if "/loop" in u:
            return snap(u, 301, headers={"location": "https://" + u.split("://", 1)[1]})
        return snap(u, html="<p>ok</p>")

    crawler, net, clock = make(tmp_path, {"one.nknu.edu.tw": route})
    seed(crawler, "https://one.nknu.edu.tw/loop")
    seed(crawler, "http://one.nknu.edu.tw/b")
    drive(crawler, net, "ip:one.nknu.edu.tw")
    r = rows(crawler)
    assert (r["https://one.nknu.edu.tw/loop"]["state"], r["https://one.nknu.edu.tw/loop"]["attempts"]) == ("pending", 1)
    assert r["http://one.nknu.edu.tw/b"]["state"] == "done"
    assert crawler.frontier.paused_until("ip:one.nknu.edu.tw") <= clock()


def test_a_host_taken_to_answer_on_one_scheme_whose_page_sends_the_visitor_back_is_not_taken_so_any_more(tmp_path):
    """http answered a page, so the host was taken to answer on http only; another page there sends the visitor to
    https: the https row is fetched again (and fails as the URL's), never two rows pointing at each other."""
    def route(u):
        if u.startswith("https://"):
            raise conn_err()
        if u.endswith("/robots.txt") or "nknu-archive-probe" in u:
            return snap(u, 404)
        if "/b" in u:
            return snap(u, 301, headers={"location": "https://" + u.split("://", 1)[1]})
        return snap(u, html="<p>a</p>")

    crawler, net, _ = make(tmp_path, {"one.nknu.edu.tw": route})
    seed(crawler, "https://one.nknu.edu.tw/a")
    seed(crawler, "https://one.nknu.edu.tw/b")
    drive(crawler, net, "ip:one.nknu.edu.tw")
    r = rows(crawler)
    assert r["http://one.nknu.edu.tw/a"]["state"] == "done"
    assert r["http://one.nknu.edu.tw/b"]["status"] == 301
    assert crawler.frontier.host("one.nknu.edu.tw")["scheme_only"] is None and events(crawler, "scheme_switch")
    b = r["https://one.nknu.edu.tw/b"]
    assert (b["state"], b["attempts"], b["final_url"]) == ("pending", 1, None)  # tried again: the URL's failure


def test_a_rest_that_begins_inside_a_row_sends_nothing_more_and_costs_the_row_nothing(tmp_path):
    crawler, net, clock, q = _two(tmp_path, {"robots.txt": status_err(503), "a.nknu.edu.tw/p": page("<p>a</p>")})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    drive(crawler, net, q)
    assert net.seen == ["https://a.nknu.edu.tw/robots.txt"]  # its 503 rests the server: no probe, no page
    row = rows(crawler)["https://a.nknu.edu.tw/p1"]
    assert (row["state"], row["attempts"]) == ("pending", 0) and crawler.frontier.paused_until(q) > clock()


# ---------------------------------------------------------------- finding 6: robots.txt of a form's host


def test_a_google_form_behind_a_short_link_leaves_the_scope_audit_empty(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="https://forms.gle/AbCdEf123">報名</a>'),
                                       "forms.gle": snap("x", 302, headers={"location": FORM}),
                                       "docs.google.com/forms": page("<p>表單</p>")},
                            queues={**QUEUES, "forms.gle": "ip:forms.gle", "docs.google.com": "google"})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    drive(crawler, net, "ip:forms.gle")
    drive(crawler, net, "google")
    assert "https://docs.google.com/robots.txt" in net.seen and rows(crawler)[FORM]["state"] == "done"
    assert scope_audit(crawler.frontier, crawler.plan) == []


# ---------------------------------------------------------------- findings 7 and 14: the whole server rests


def _two(tmp_path, routes):
    q = "ip:140.127.9.9"
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), **routes},
                               queues={"a.nknu.edu.tw": q, "b.nknu.edu.tw": q})
    return crawler, net, clock, q


def test_a_503_rests_the_whole_server_and_costs_only_the_url_that_got_it(tmp_path):
    crawler, net, clock, q = _two(tmp_path, {"a.nknu.edu.tw/p": status_err(503), "b.nknu.edu.tw/": page("<p>b</p>")})
    seed(crawler, "https://a.nknu.edu.tw/p1")
    seed(crawler, "https://b.nknu.edu.tw/x")
    drive(crawler, net, q)
    got = crawler.frontier.q("SELECT max(ended) FROM request WHERE url LIKE '%/p1'")[0][0]
    assert crawler.frontier.paused_until(q) - got >= 900  # the whole server, 15 min
    assert not [u for u in net.seen if "b.nknu.edu.tw" in u]  # its other name waits too
    r = rows(crawler)
    assert (r["https://a.nknu.edu.tw/p1"]["attempts"], r["https://b.nknu.edu.tw/x"]["attempts"]) == (1, 0)
    clock.t = crawler.frontier.paused_until(q) + 1
    drive(crawler, net, q)
    assert rows(crawler)["https://b.nknu.edu.tw/x"]["state"] == "done"


def test_three_errors_in_a_row_on_one_server_rest_it_a_fixed_15_minutes(tmp_path):
    crawler, net, clock, q = _two(tmp_path, {"a.nknu.edu.tw/t": timeout_err(), "b.nknu.edu.tw/e": status_err(500),
                                             "b.nknu.edu.tw/ok": page("<p>b</p>")})
    for i in range(2):
        seed(crawler, f"https://a.nknu.edu.tw/t{i}")
    seed(crawler, "https://b.nknu.edu.tw/e1")
    seed(crawler, "https://b.nknu.edu.tw/ok")
    for h in ("a.nknu.edu.tw", "b.nknu.edu.tw"):  # robots.txt and the probe read already: their answers would count
        crawler.frontier.set_host(h, robots_state="none", robots_at=clock(), probe_status=404)
    drive(crawler, net, q)
    last = crawler.frontier.q("SELECT max(ended) FROM request")[0][0]
    assert 900 <= crawler.frontier.paused_until(q) - last <= 905
    assert "https://b.nknu.edu.tw/ok" not in net.seen
    assert {u: x["attempts"] for u, x in rows(crawler).items()} == {
        "https://a.nknu.edu.tw/t0": 1, "https://a.nknu.edu.tw/t1": 1, "https://b.nknu.edu.tw/e1": 1,
        "https://b.nknu.edu.tw/ok": 0}
    assert events(crawler, "server_rest")


def test_a_hosts_breaker_cools_it_a_fixed_15_minutes_never_doubled(tmp_path):
    crawler, _, _ = make(tmp_path, {})
    seed(crawler, "https://g.nknu.edu.tw/x")
    assert crawler._cool_host("g.nknu.edu.tw") == 900
    assert crawler._cool_host("g.nknu.edu.tw") == 900


# ---------------------------------------------------------------- finding 9: seed and retry hold run.lock


def cli(*args):
    from nknu.admin.cli import app

    return CliRunner().invoke(app, ["archive", *args])


def test_retry_holds_run_lock_while_it_writes(tmp_path, monkeypatch):
    root = tmp_path / "new"
    Frontier(root / "frontier.db").close()
    seen = {}

    def retry(self, prefix):
        try:
            mod.hold_run_lock(root).close()
            seen["run could start"] = True
        except mod.AlreadyRunning:
            seen["run could start"] = False
        return 0, 0

    monkeypatch.setattr(Frontier, "retry", retry)
    out = cli("retry", "http_5", "--dir", str(root))
    assert out.exit_code == 0, out.output
    assert seen == {"run could start": False}


def test_seed_holds_run_lock_while_it_writes(tmp_path, monkeypatch):
    from tests.archive.test_seeds_round1 import old_archive

    old = old_archive(tmp_path)
    root = tmp_path / "new"
    seen = {}

    def seed_round1(crawler, **kw):
        try:
            mod.hold_run_lock(root).close()
            seen["run could start"] = True
        except mod.AlreadyRunning:
            seen["run could start"] = False
        return seeds.SeedReport()

    monkeypatch.setattr(seeds, "seed_round1", seed_round1)
    monkeypatch.setattr("nknu.archive.round1.load", lambda path=None: PLAN)
    out = cli("seed", "--round", "1", "--old", str(old), "--dir", str(root))
    assert out.exit_code == 0, out.output
    assert seen == {"run could start": False}


def test_reading_an_archives_round_never_touches_its_wal_or_shm(tmp_path):
    db = tmp_path / "frontier.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('round', '1')")
    conn.commit()
    conn.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["frontier.db"]
    assert round_of(db) == "1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["frontier.db"]  # mode=ro alone writes -wal and -shm


def test_a_new_archives_round_is_read_from_its_wal_when_the_writer_never_closed(tmp_path):
    """immutable reads no WAL: an archive whose writer was killed before a checkpoint keeps its round in the WAL."""
    import subprocess
    import sys

    db = tmp_path / "frontier.db"
    code = (f"import sqlite3, os; c = sqlite3.connect(r'{db}'); c.execute('PRAGMA journal_mode=WAL'); "
            "c.execute('PRAGMA wal_autocheckpoint=0'); c.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)');"
            " c.execute(\"INSERT INTO meta VALUES ('round', '1')\"); c.commit(); os._exit(0)")
    subprocess.run([sys.executable, "-c", code], check=True)
    assert (tmp_path / "frontier.db-wal").stat().st_size > 0
    assert round_of(db) == "1"


# ---------------------------------------------------------------- finding 10: the seed report


def test_the_seed_report_lists_what_waits_for_the_google_step_apart_from_the_rules_and_v1s_left_out(tmp_path):
    from tests.archive.test_seeds_round1 import cc, crawler_for, old_archive, probe

    drive_url = "https://drive.google.com/file/d/1AbCdEfGhIjK/view"
    old = old_archive(tmp_path, extra_urls=[(50, drive_url, "drive.google.com", "page", 1, "link", 1, "done", None, 0,
                                             0)])
    facts = tmp_path / "facts.db"
    conn = sqlite3.connect(facts)
    conn.execute("CREATE TABLE doc(url TEXT, unit TEXT, title TEXT, category TEXT, withdrawn_at TEXT)")
    conn.execute("INSERT INTO doc VALUES ('https://sso.nknu.edu.tw/', '教務處', '登入', '行政資訊', NULL)")
    conn.commit()
    conn.close()
    c = crawler_for(tmp_path / "archive")
    report = seeds.seed_round1(c, old=old, facts_db=facts, cc_dir=cc(tmp_path), probe=probe(tmp_path))
    assert any(b.source == "v1_known" and b.reason == "out:login" for b in report.blocked)
    text = report.text()
    google, rules = text.split("## 等 Google 那一步")[1], text.split("## 被新规则挡掉的网址")[1].split("## ")[0]
    assert drive_url in google and drive_url not in rules
    assert "v1_known" in rules


# ---------------------------------------------------------------- findings 11 and 15: the machine-wide lock


def test_a_crawl_on_the_real_network_refuses_to_start_without_the_school_lock(tmp_path):
    c = mod.Crawler(tmp_path, plan=PLAN, resolver=lambda h: (QUEUES.get(h, f"ip:{h}"), None, h),
                    netcheck=lambda: True, campus_url=None)
    c.seed_url(HOME)
    c.frontier.x("UPDATE url SET state='active'")
    with pytest.raises(schoollock.SchoolLockNotHeld):
        c.run(poll_s=0.01)
    assert rows(c)[HOME]["state"] == "active" and not events(c, "run_start")  # not a row touched


def test_a_request_refused_for_want_of_the_school_lock_stops_the_run_and_is_no_crash(tmp_path):
    class Locked(Net):
        def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
            raise schoollock.SchoolLockNotHeld("c.nknu.edu.tw: not held")

    crawler, _, clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    crawler._handle(QC, Locked(clock, {}), Sink(tmp_path), crawler._next(QC))
    row = rows(crawler)[HOME]
    assert (row["state"], row["reason"]) == ("pending", None) and crawler.end_reason == "no_school_lock"
    assert crawler.stop.is_set() and not events(crawler, "crash")


def test_the_run_start_says_which_school_lock_the_run_holds(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    crawler.run(poll_s=0.01)
    start = json.loads(events(crawler, "run_start")[-1])
    assert start["school_lock"] == str(schoollock.lock_path()) == os.environ["NKNU_SCHOOL_LOCK"]


def test_archive_run_refuses_on_a_machine_with_the_services_env_unless_the_lock_path_is_given(tmp_path, monkeypatch):
    env = tmp_path / "etc-nknu-env"
    env.write_text("# the services' settings\n")
    monkeypatch.setattr(schoollock, "SERVICES_ENV", env)

    class NoCrawler:
        def __init__(self, *a, **k):
            raise AssertionError("built a crawler with a lock the services may not share")

    monkeypatch.setattr(mod, "Crawler", NoCrawler)
    lock = os.environ.pop("NKNU_SCHOOL_LOCK")
    try:
        out = cli("run", "--dir", str(tmp_path / "a"))
    finally:
        os.environ["NKNU_SCHOOL_LOCK"] = lock
    assert out.exit_code == 1 and "NKNU_SCHOOL_LOCK" in out.output


def test_a_lock_file_this_user_may_only_read_is_still_a_lock(tmp_path):
    path = tmp_path / "school-requests.lock"
    path.write_text("")
    path.chmod(0o444)
    try:
        lock = schoollock.hold(path)
        try:
            assert schoollock.held(path)
            with pytest.raises(schoollock.SchoolLockBusy):
                schoollock.hold(path)
        finally:
            lock.close()
    finally:
        path.chmod(0o644)


# ---------------------------------------------------------------- finding 19: only a mock transport is offline


def test_a_real_transport_given_to_the_client_still_needs_the_school_lock():
    client = Client(attempts=1, transport=httpx.HTTPTransport())
    with pytest.raises(schoollock.SchoolLockNotHeld):
        client.fetch(Target("http://c.nknu.edu.tw/y"), mod.NO_RATE)
    offline = Client(attempts=1, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok")))
    assert offline.fetch(Target("http://c.nknu.edu.tw/y"), mod.NO_RATE).status == 200


# ---------------------------------------------------------------- finding 20: a resource's redirect goes through admit()


def test_an_attachment_that_redirects_to_a_short_link_has_the_short_link_read(tmp_path):
    pdf = "https://c.nknu.edu.tw/aca/uploadfile/a.pdf"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="/aca/uploadfile/a.pdf">附件</a>'),
                                       "a.pdf": snap("x", 302, headers={"location": "https://reurl.cc/3Lp06M"}),
                                       "reurl.cc": lambda u: snap(u, html=REURL)},
                            queues={**QUEUES, "reurl.cc": "ip:reurl.cc"})
    seed(crawler, HOME)
    settle(crawler, net, [QC, "ip:reurl.cc"])
    short = rows(crawler)["https://reurl.cc/3Lp06M"]
    target = scope.canonical(round1.short_answer(short["url"], 200, None, REURL).target)
    assert (short["kind0"], short["final_url"]) == ("shortlink", target)
    assert result(crawler, pdf) == "redirect" and result(crawler, short["url"]) == "redirect"


# ---------------------------------------------------------------- findings 12 and 21: the campus check


def test_the_campus_check_says_on_only_when_sso_sends_the_visitor_to_the_system_it_names(tmp_path):
    crawler, _, clock = crawl(tmp_path, {}, campus_url=mod.CAMPUS_URL, local_address=lambda: "140.127.200.9")
    sink = Sink(tmp_path)
    err = Net(clock, {"checkcampusip": snap("x", 302, headers={"location": "/ErrorPage/ErrorPage.aspx?aspxerrorpath=/x"})})
    assert crawler._campus_step(QS, err, sink) and crawler.campus == "unknown"
    assert any("edocap" in m for m in events(crawler, "campus"))
    clock.t += mod.CAMPUS_EVERY_S + 1
    on = Net(clock, {"checkcampusip": snap("x", 302, headers={"location": "https://EDOCAP.nknu.edu.tw/default.aspx"})})
    assert crawler._campus_step(QS, on, sink) and crawler.campus == "on"


def test_a_new_address_makes_the_campus_unknown_until_it_is_checked(tmp_path):
    addr = {"now": "192.168.1.5"}
    off = page("<p>系統訊息：您的IP 不在校內網路，請連線VPN</p>")
    crawler, net, _ = crawl(tmp_path, {"checkcampusip": off}, campus_url=mod.CAMPUS_URL,
                            local_address=lambda: addr["now"])
    assert crawler._campus_step(QS, net, Sink(tmp_path)) and crawler.campus == "off"
    addr["now"] = "140.127.200.9"
    assert crawler._campus_due() and crawler.campus == "unknown"


# ---------------------------------------------------------------- finding 22: the Crawl-delay before every request


def test_the_crawl_delay_is_kept_by_the_request_that_tries_the_other_scheme(tmp_path):
    def route(u):
        if u.startswith("http://"):
            raise conn_err()
        return snap(u, html="<p>ok</p>")

    crawler, net, clock = make(tmp_path, {"one.nknu.edu.tw": route})
    seed(crawler, "http://one.nknu.edu.tw/a")
    crawler.frontier.set_host("one.nknu.edu.tw", queue="ip:one.nknu.edu.tw", robots_state="rules",
                              robots_body="User-agent: *\nCrawl-delay: 20\n", crawl_delay=20.0, robots_at=clock(),
                              probe_status=404)
    drive(crawler, net, "ip:one.nknu.edu.tw")
    assert net.seen[:2] == ["http://one.nknu.edu.tw/a", "https://one.nknu.edu.tw/a"]
    assert not [x for x in politeness(crawler.frontier) if "Crawl-delay" in x]


# ---------------------------------------------------------------- finding 23: the placement a request went out under


def test_each_request_says_the_placement_its_row_was_admitted_under_and_the_audit_checks_it(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<a href="https://www.moe.gov.tw/f/r.pdf">r</a>'),
                                       "moe.gov.tw/f": lambda u: snap(u, body=b"%PDF-1.4 x",
                                                                       headers={"content-type": "application/pdf"})},
                            queues={**QUEUES, "www.moe.gov.tw": "ip:www.moe.gov.tw"})
    seed(crawler, HOME)
    drive(crawler, net, QC)
    drive(crawler, net, "ip:www.moe.gov.tw")
    scopes = {r["url"]: r["scope"] for r in crawler.frontier.q("SELECT url, scope FROM request WHERE purpose='page'")}
    assert scopes[HOME] == "admit:r1:c.nknu.edu.tw/aca:page"
    assert scopes["https://www.moe.gov.tw/f/r.pdf"] == "admit:resource:c.nknu.edu.tw/aca:file"
    assert scope_audit(crawler.frontier, crawler.plan) == []
    # a page of a round-2 site said to be round 1's, and a resource of a site that is no round-1 site's
    crawler.frontier.request(QC, "c.nknu.edu.tw", AE_HOME, "page", 1.0, 2.0, 200, None,
                             scope="admit:r1:c.nknu.edu.tw/ae:page")
    crawler.frontier.request("ip:x", "www.moe.gov.tw", "https://www.moe.gov.tw/g.pdf", "page", 3.0, 4.0, 200, None,
                             scope="admit:resource:c.nknu.edu.tw/ae:file")
    out = scope_audit(crawler.frontier, crawler.plan)
    assert len(out) == 2 and any("c.nknu.edu.tw/ae/" in x for x in out) and any("g.pdf" in x for x in out)
