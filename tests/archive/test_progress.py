"""The progress dashboard (round-1 design §14, record 二·7): the finishing time is the slowest server's and names it,
with the Crawl-delay, retry and pause corrections; the politeness check gives the same answer fed in pieces or at
once, and the monitor's; the dashboard never writes (the file's sha256 stays, a write fails), never makes the crawler
wait for a lock, and refreshes a big archive in under 300 ms; a site with no resource rows is 没检查, never green;
times are Taipei's; a site's page never lists an incomplete page as 抓到了 or 规则不抓; 纪录齐了、还没对账 and 爬完 are
told apart; the routes answer on this machine only. All on synthetic archives, no network."""

from __future__ import annotations

import contextlib
import hashlib
import html
import itertools
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nknu.archive import monitor, progress
from nknu.archive.frontier import Frontier
from nknu.archive.viewer import Viewer, make_handler, progress_main_html, progress_site_html

ACA, AFFAIR, DEPT = "c.nknu.edu.tw/aca", "c.nknu.edu.tw/affair", "dept.nknu.edu.tw"
OTHER = "other.nknu.edu.tw"  # a round-two site
Q_C, Q_D = "ip:140.127.1.1", "ip:140.127.2.2"
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC).timestamp()  # 16:00 in Taipei
_n = itertools.count(1)


def ts(y, mo, d, h, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def add_url(f: Frontier, **cols) -> int:
    row = {"url": f"https://c.nknu.edu.tw/aca/p{next(_n)}.aspx", "host": "c.nknu.edu.tw", "site": ACA,
           "owner_site": ACA, "tier": 0, "kind": "page", "kind0": "page", "depth": 0, "found_via": "link",
           "state": "done", "status": 200, "page_type": "t:ok", "sniff": "html", "added_at": NOW - 7200}
    row.update(cols)
    row.setdefault("path", row["url"].split("?", 1)[0])
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def res(f: Frontier, page: int, key: str, role: str, res_id: int | None, need: str = "must", why: str | None = None):
    f.add_page_res(page, [{"res_key": key, "how": "get", "role": role, "need": need, "why": why, "res_id": res_id}])


def event(f: Frontier, at: float, level: str, code: str, message: str, host: str | None = None) -> None:
    f.x("INSERT INTO event(at, level, code, host, url, message) VALUES (?,?,?,?,?,?)",
        (iso(at), level, code, host, None, message))


def requests(f: Frontier, queue: str, host: str, n: int, start: float, every: float = 3.0, took: float = 0.2,
             **kw) -> float:
    t = start
    for _ in range(n):
        f.request(queue, host, f"https://{host}/x{next(_n)}", kw.get("purpose", "page"), t, t + took, 200, None,
                  ip=queue.removeprefix("ip:"), new_conn=kw.get("new_conn", False), campus=kw.get("campus"))
        t += every
    return t


def plan(f: Frontier, site: str, name: str, imp: str | None, rnd: str = "1") -> None:
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (site, name, imp, rnd, "c.nknu", f"https://{site}/default.aspx"))


@pytest.fixture
def arch(tmp_path):
    """A small round-1 archive: two 全校重要 sites on c.nknu's server, one 本系重要 site on its own, a round-two site;
    pages, files and images in every state that matters here; an hour of requests; a run that ended waiting."""
    f = Frontier(tmp_path / "frontier.db")
    for site, name, imp in ((ACA, "教务处", "全校重要"), (AFFAIR, "总务处", "全校重要"), (DEPT, "测试系", "本系重要")):
        plan(f, site, name, imp)
    plan(f, OTHER, "其他", None, rnd="2")
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', ?, 'ok', 5)", (ACA,))
    f.set_host("c.nknu.edu.tw", queue=Q_C)
    f.set_host("dept.nknu.edu.tw", queue=Q_D)
    f.set_host("other.nknu.edu.tw", queue="ip:140.127.3.3")
    ids = {}
    # 教务处: a complete page, a page whose attachment failed, a page still to fetch
    ids["aca_home"] = add_url(f, url="https://c.nknu.edu.tw/aca/default.aspx", found_via="seed", title="首页")
    ids["aca_img"] = add_url(f, kind="image", kind0="image", page_type=None, sniff="jpeg")
    res(f, ids["aca_home"], "img:1", "img", ids["aca_img"])
    ids["aca_news"] = add_url(f, title="公告")
    ids["aca_pdf"] = add_url(f, kind="file", kind0="file", page_type=None, sniff=None, state="failed",
                             reason="connect", status=None)
    res(f, ids["aca_news"], "file:1", "file", ids["aca_pdf"])
    ids["aca_todo"] = add_url(f, state="pending", status=None, sniff=None, page_type=None)
    # a page that redirects to the home page (judged where it lands, never listed by itself)
    ids["aca_moved"] = add_url(f, url="https://c.nknu.edu.tw/aca/old.aspx", status=302,
                               final_url="https://c.nknu.edu.tw/aca/default.aspx")
    # 总务处: one complete page with an image
    ids["aff_home"] = add_url(f, url="https://c.nknu.edu.tw/affair/default.aspx", site=AFFAIR, owner_site=AFFAIR,
                              found_via="seed")
    ids["aff_img"] = add_url(f, site=AFFAIR, owner_site=AFFAIR, kind="image", kind0="image", page_type=None,
                             sniff="png")
    res(f, ids["aff_home"], "img:1", "img", ids["aff_img"])
    # 测试系: one complete page, no resource rows at all (its resource layer has no data)
    ids["dept_home"] = add_url(f, url="https://dept.nknu.edu.tw/", host="dept.nknu.edu.tw", site=DEPT,
                               owner_site=DEPT, tier=1, found_via="seed")
    # round two: left for later
    ids["other"] = add_url(f, url="https://other.nknu.edu.tw/", host="other.nknu.edu.tw", site=OTHER,
                           owner_site=OTHER, tier=2, state="later", reason="round2:other_site", status=None)
    t = requests(f, Q_C, "c.nknu.edu.tw", 40, NOW - 1800)
    requests(f, Q_D, "dept.nknu.edu.tw", 5, NOW - 1700)
    event(f, NOW - 3000, "info", "run_start", json.dumps({"pid": 1, "gap": [2.0, 5.0], "sites": None}))
    event(f, t + 10, "info", "run_end", json.dumps({"reason": "waiting", "sites": None, "pending": 1, "presses": 0,
                                                     "next_due": ts(2026, 10, 26, 18, 40)}))  # 02:40 Taipei, 10-27
    yield f, ids, tmp_path
    f.close()


def statuses(**by_site):
    """A stand-in for monitor.site_status (module A's): the states this test says, for every site."""
    def site_status(conn, now, pages=None):
        assert pages is not None  # the dashboard hands it the page_completeness rows it worked out
        return {site: {"state": state, "why": [f"因为 {state}"]} for site, state in by_site.items()}
    return site_status


def dash(root: Path, **kw) -> progress.Dashboard:
    kw.setdefault("site_status", statuses(**{ACA: "error", AFFAIR: "done", DEPT: "done"}))
    kw.setdefault("checks", lambda conn, now, cheap: [{"no": 1, "title": "首页", "result": "pass", "detail": ""}])
    return progress.Dashboard(root, clock=lambda: NOW, background=False, cache_s=0, **kw)


def visible(markup: str) -> str:
    """The text a person sees: tags (and so the hover titles) dropped."""
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", markup)))


# ---------------------------------------------------------------- §14.3: how long it still takes

def eta_of(f: Frontier, now: float = NOW) -> dict:
    conn = progress.connect(f.path)
    try:
        t = progress.Tracker()
        t.update(conn)
        return progress.eta(conn, now, t.speeds(now))
    finally:
        conn.close()


def two_servers(tmp_path, left_a: int, left_b: int) -> Frontier:
    f = Frontier(tmp_path / "frontier.db")
    f.set_host("a.nknu.edu.tw", queue="ip:140.127.9.1")
    f.set_host("b.nknu.edu.tw", queue="ip:140.127.9.2")
    for host, n in (("a.nknu.edu.tw", left_a), ("b.nknu.edu.tw", left_b)):
        for _ in range(n):
            add_url(f, url=f"https://{host}/p{next(_n)}", host=host, site=host, owner_site=host, state="pending",
                    status=None)
        requests(f, f"ip:{'140.127.9.1' if host.startswith('a') else '140.127.9.2'}", host, 30, NOW - 600)
    return f


def test_the_round_ends_when_its_slowest_server_does_and_that_server_is_named(tmp_path):
    f = two_servers(tmp_path, 10, 100)
    est = eta_of(f)
    assert est["slowest"] == "ip:140.127.9.2"
    b = est["servers"]["ip:140.127.9.2"]
    assert b["basis"] == "hour" and b["c"] == pytest.approx(3.0) and b["k"] == pytest.approx(1.0)
    assert est["seconds"] == pytest.approx(100 * 3.0) and est["seconds"] == b["t"]
    assert est["servers"]["ip:140.127.9.1"]["t"] == pytest.approx(10 * 3.0)
    assert est["finish_at"] == pytest.approx(NOW + 300)


def test_a_crawl_delay_longer_than_the_pace_holds_its_server_back(tmp_path):
    f = two_servers(tmp_path, 10, 50)
    f.set_host("a.nknu.edu.tw", crawl_delay=20.0)  # fine-arts asks for 20 s
    est = eta_of(f)
    a = est["servers"]["ip:140.127.9.1"]
    assert a["t"] == pytest.approx(10 * 20.0) and est["slowest"] == "ip:140.127.9.1"
    assert any("20" in w for w in a["why"])


def test_a_pause_adds_what_is_left_of_it_and_a_retry_waits_for_its_time(tmp_path):
    f = two_servers(tmp_path, 10, 20)
    f.pause("ip:140.127.9.1", NOW + 900, "a.nknu.edu.tw: http_503")
    f.x("UPDATE url SET attempts = 1, next_at = ? WHERE host = 'b.nknu.edu.tw' AND id = (SELECT max(id) FROM url "
        "WHERE host = 'b.nknu.edu.tw')", (NOW + 5000,))
    est = eta_of(f)
    assert est["servers"]["ip:140.127.9.1"]["t"] == pytest.approx(10 * 3.0 + 900)
    assert est["servers"]["ip:140.127.9.2"]["t"] == pytest.approx(5000 + 3.0)
    assert est["slowest"] == "ip:140.127.9.2"


def test_a_server_with_too_few_requests_this_hour_takes_the_whole_rounds_pace_and_none_is_a_guess(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    f.set_host("a.nknu.edu.tw", queue="ip:140.127.9.1")
    f.set_host("b.nknu.edu.tw", queue="ip:140.127.9.2")
    for host in ("a.nknu.edu.tw", "b.nknu.edu.tw"):
        add_url(f, url=f"https://{host}/p", host=host, site=host, owner_site=host, state="pending", status=None)
    requests(f, "ip:140.127.9.1", "a.nknu.edu.tw", 10, NOW - 9000, every=6.0)  # hours ago, 6 s apart
    est = eta_of(f)
    assert est["servers"]["ip:140.127.9.1"]["basis"] == "round"
    assert est["servers"]["ip:140.127.9.1"]["c"] == pytest.approx(6.0)
    assert est["servers"]["ip:140.127.9.2"]["basis"] == "guess"
    assert est["servers"]["ip:140.127.9.2"]["c"] == progress.GUESS_S
    assert est["slowest"] == "ip:140.127.9.1"  # one row each: 6 s against the 4 s guess


def test_lab_rows_and_unresolved_hosts_are_not_left_for_this_run(tmp_path):
    f = two_servers(tmp_path, 3, 0)
    add_url(f, url="https://a.nknu.edu.tw/lab", host="a.nknu.edu.tw", site="a", owner_site="a", state="pending",
            reason="host_unreachable", status=None)
    f.set_host("gone.nknu.edu.tw", queue="dns-fail")
    add_url(f, url="https://gone.nknu.edu.tw/", host="gone.nknu.edu.tw", site="g", owner_site="g", state="pending",
            status=None)
    est = eta_of(f)
    assert est["servers"]["ip:140.127.9.1"]["left"] == 3 and "dns-fail" not in est["servers"]


# ---------------------------------------------------------------- the politeness check (§6.3, §14.2)

def polite_archive(tmp_path) -> Frontier:
    """Requests to two servers: one gap below 2 s, one slow answer not waited out, one Crawl-delay not kept, one
    request sent while the one before was still going."""
    f = Frontier(tmp_path / "frontier.db")
    event(f, NOW - 5000, "info", "run_start", json.dumps({"gap": [2.0, 5.0]}))
    f.set_host("slow.nknu.edu.tw", queue="ip:140.127.5.5", crawl_delay=20.0)
    t0 = NOW - 4000
    rows = [  # (ip, host, start, took)
        ("140.127.1.1", "c.nknu.edu.tw", 0.0, 0.2),
        ("140.127.1.1", "c.nknu.edu.tw", 3.2, 0.2),  # 3 s after the last: fine
        ("140.127.1.1", "c.nknu.edu.tw", 4.4, 6.0),  # 1 s after: below the run's 2 s
        ("140.127.1.1", "c.nknu.edu.tw", 12.9, 0.2),  # 2.5 s after a 6-second answer: a warning
        ("140.127.1.1", "c.nknu.edu.tw", 13.0, 0.5),  # sent while the one before was still going
        ("140.127.1.1", "c.nknu.edu.tw", 16.5, 0.2),
        ("140.127.5.5", "slow.nknu.edu.tw", 0.04, 0.1),
        ("140.127.5.5", "slow.nknu.edu.tw", 5.14, 0.1),  # its host asks for 20 s
    ]
    for ip, host, start, took in rows:
        f.request(f"ip:{ip}", host, f"https://{host}/{next(_n)}", "page", t0 + start, t0 + start + took, 200, None,
                  ip=ip)
    return f


def test_politeness_in_two_pieces_or_at_once_says_the_same_and_what_the_monitor_says(tmp_path):
    f = polite_archive(tmp_path)
    rows = f.q("SELECT * FROM request ORDER BY started, id")
    runs = progress.run_gaps(f.conn)
    delays = progress.crawl_delays(f.conn)
    whole, parts = progress.Politeness(), progress.Politeness()
    whole.feed(rows, runs, delays)
    parts.feed(rows[:3], runs, delays)
    parts.feed(rows[3:], runs, delays)
    assert whole.result() == parts.result()
    got = whole.result()
    assert got["overlap"] == 1 and got["gap"] == 1 and got["delay"] == 1 and got["slow"] == 1
    assert got["hard"] == len(monitor.politeness(f)) == 3
    assert got["slow"] == len(monitor.politeness_warnings(f))


def test_the_tracker_reads_only_the_new_requests_and_adds_up_the_same(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    event(f, NOW - 5000, "info", "run_start", json.dumps({"gap": [2.0, 5.0]}))
    requests(f, Q_C, "c.nknu.edu.tw", 10, NOW - 1000)
    conn = progress.connect(f.path)
    t1 = progress.Tracker()
    assert t1.update(conn) == 10
    requests(f, Q_C, "c.nknu.edu.tw", 10, NOW - 900, every=1.0)  # 9 gaps below 2 s
    assert t1.update(conn) == 10 and t1.update(conn) == 0
    t2 = progress.Tracker()
    t2.update(conn)
    conn.close()
    assert t1.polite.result() == t2.polite.result() and t1.polite.result()["gap"] == 9
    assert t1.speeds(NOW) == t2.speeds(NOW)


# ---------------------------------------------------------------- §14.4: never slowing the crawler

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_hundred_refreshes_leave_the_file_as_it_was_and_a_write_fails(arch):
    f, _ids, root = arch
    f.close()  # every change is in the file now (WAL checkpointed on close)
    before = sha(root / "frontier.db")
    d = dash(root)
    for i in range(100):
        d.main()
        if i % 25 == 0:
            d.refresh_slow()
        d.site(ACA)
    assert sha(root / "frontier.db") == before
    conn = progress.connect(root / "frontier.db")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE url SET state = 'done'")
    conn.close()
    assert sha(root / "frontier.db") == before


def test_the_dashboard_opens_the_file_read_only_never_immutable(arch, monkeypatch):
    _f, _ids, root = arch
    seen, real = [], sqlite3.connect

    def spy(target, *args, **kw):
        seen.append(str(target))
        return real(target, *args, **kw)

    monkeypatch.setattr(progress.sqlite3, "connect", spy)
    d = dash(root)
    d.main()
    d.refresh_slow()
    d.site(ACA)
    files = [u for u in seen if "frontier.db" in u]  # _missing also opens an in-memory schema to compare with
    assert len(files) == 3 and all(u.endswith("?mode=ro") and "immutable" not in u for u in files)
    conn = progress.connect(root / "frontier.db")
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == progress.BUSY_MS
    conn.close()


RW_SECONDS = float(os.environ.get("NKNU_TEST_PROGRESS_RW_S", "30"))  # design §16: 30 s


def test_reading_while_the_crawler_writes_never_locks_it(arch):
    f, ids, root = arch
    f.close()
    stop, errors, writes = threading.Event(), [], [0]

    def writer():
        # the crawler's connection, with a short busy timeout so that any wait on the dashboard would show
        w = sqlite3.connect(root / "frontier.db", timeout=0.05, isolation_level=None)
        w.execute("PRAGMA journal_mode=WAL")
        i = 0
        try:
            while not stop.is_set():
                i += 1
                try:
                    w.execute("BEGIN IMMEDIATE")
                    w.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, owner_site) "
                              "VALUES (?, ?, 'c.nknu.edu.tw', ?, 0, 'page', 1, 'link', 'pending', ?)",
                              (f"https://c.nknu.edu.tw/aca/w{i}", f"/aca/w{i}", ACA, ACA))
                    w.execute("INSERT INTO request(queue, host, url, purpose, started, ended, status) "
                              "VALUES (?, 'c.nknu.edu.tw', 'u', 'page', ?, ?, 200)", (Q_C, NOW + i, NOW + i + 0.1))
                    w.execute("UPDATE url SET state = 'done', status = 200 WHERE id = ?", (ids["aca_todo"],))
                    w.execute("COMMIT")
                    writes[0] += 1
                except sqlite3.OperationalError as exc:
                    errors.append(str(exc))
                    with contextlib.suppress(sqlite3.Error):
                        w.execute("ROLLBACK")
        finally:
            w.close()

    t = threading.Thread(target=writer)
    t.start()
    d = dash(root)
    end = time.monotonic() + RW_SECONDS
    reads = 0
    try:
        while time.monotonic() < end:
            d.main()
            reads += 1
            if reads % 10 == 0:
                d.refresh_slow()
                d.site(ACA)
    finally:
        stop.set()
        t.join()
    assert errors == [] and writes[0] > 100 and reads > 10


def big_archive(root: Path) -> None:
    """60k url rows over 100 sites and 20 servers, 60k requests, 500k links (design §16)."""
    f = Frontier(root / "frontier.db")
    c = f.conn
    c.execute("BEGIN")
    sites = [f"s{i}.nknu.edu.tw" for i in range(100)]
    c.executemany("INSERT INTO site_plan(site, name, imp, round, home) VALUES (?,?,?,?,?)",
                  [(s, f"网站{i}", "全校重要" if i < 34 else "本系重要", "1", f"https://{s}/") for i, s in enumerate(sites)])
    c.executemany("INSERT INTO host(host, queue) VALUES (?,?)", [(s, f"ip:140.127.0.{i % 20}") for i, s in enumerate(sites)])
    c.executemany("INSERT INTO page_type(key, site, verdict, n_pages) VALUES (?,?,?,?)",
                  [(f"t{i}", s, "ok", 600) for i, s in enumerate(sites)])
    states = ("done",) * 7 + ("pending", "failed", "skipped")
    rows = []
    for i in range(60_000):
        s = i % 100
        kind = ("page", "page", "file", "image")[i % 4]
        st = states[i % 10]
        rows.append((f"https://{sites[s]}/p{i}", f"/p{i}", sites[s], sites[s], sites[s], s // 34 and 1, kind, kind, 1,
                     "link", st, "out:x" if st == "skipped" else ("connect" if st == "failed" else None),
                     200 if st == "done" else None, f"t{s}" if kind == "page" else None,
                     {"page": "html", "file": "pdf", "image": "jpeg"}[kind] if st == "done" else None,
                     NOW - (60_000 - i)))
    c.executemany("INSERT INTO url(url, path, host, site, owner_site, tier, kind, kind0, depth, found_via, state, "
                  "reason, status, page_type, sniff, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.executemany("INSERT INTO request(queue, host, url, purpose, started, ended, status, ip, new_conn) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  [(f"ip:140.127.0.{i % 20}", sites[i % 100], f"https://{sites[i % 100]}/p{i}", "page",
                    NOW - 60_000 + i, NOW - 60_000 + i + 0.2, 200, f"140.127.0.{i % 20}", 0) for i in range(60_000)])
    c.executemany("INSERT INTO link(src, dst, text, verdict) VALUES (?,?,?,?)",
                  ((1 + i // 10, f"https://x.nknu.edu.tw/l{i}", "t", "queued") for i in range(500_000)))
    c.execute("COMMIT")
    f.close()


def test_a_refresh_of_a_big_archive_takes_under_300_ms(tmp_path):
    big_archive(tmp_path)
    d = dash(tmp_path)
    t0 = time.perf_counter()
    first = d.main()
    first_s = time.perf_counter() - t0
    assert first["total"]["all"] > 50_000 and first["polite"]["checked"] == 60_000
    took = []
    for _ in range(3):
        t0 = time.perf_counter()
        d.main()
        took.append(time.perf_counter() - t0)
    assert min(took) < 0.3, (first_s, took)
    assert first_s < 2.0, first_s  # the first one reads the whole request log once (design §14.2)


# ---------------------------------------------------------------- what the page says

def snap_of(root: Path, slow: bool = True, **kw) -> dict:
    d = dash(root, **kw)
    if slow:
        d.refresh_slow()
    return d.main()


def test_a_site_with_pages_and_no_resource_rows_is_not_checked_never_green(arch):
    _f, _ids, root = arch
    s = {x["site"]: x for x in snap_of(root)["sites"]}
    assert s[DEPT]["state"] == "unchecked"  # site_status said done; its pages have no resource row at all
    assert s[AFFAIR]["state"] == "done"
    text = visible(progress_main_html(snap_of(root)))
    assert "没检查" in text
    # before the background snapshot, no site has a colour of its own
    early = snap_of(root, slow=False)
    assert {x["state"] for x in early["sites"]} == {"unchecked"}
    assert "✓ 爬完" not in visible(progress_main_html(early))


def test_a_site_still_crawling_with_no_resource_rows_keeps_its_state_with_a_note(arch):
    _f, _ids, root = arch
    s = {x["site"]: x for x in snap_of(root, site_status=statuses(**{ACA: "error", AFFAIR: "done",
                                                                      DEPT: "crawling"}))["sites"]}
    assert s[DEPT]["state"] == "crawling" and any("资源层没资料" in w for w in s[DEPT]["why"])


def test_what_is_missing_counts_retries_failures_and_incomplete_pages(arch):
    f, _ids, root = arch
    add_url(f, kind="file", kind0="file", page_type=None, state="pending", attempts=1, next_at=NOW + 600,
            status=None, sniff=None)
    t = snap_of(root)["total"]
    # one retry, the failed attachment, the page that lacks it
    assert t["retry"] == 1 and t["lacking"] == 3
    assert "有缺 3 个（1 个会自动重试）" in visible(progress_main_html(snap_of(root)))


def test_without_site_status_wired_the_sites_say_so_and_are_not_green(arch):
    _f, _ids, root = arch
    snap = snap_of(root, site_status=lambda conn, now, pages=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert {x["state"] for x in snap["sites"]} == {"unchecked"}
    assert all("boom" in " ".join(x["why"]) for x in snap["sites"])


def test_records_complete_not_reconciled_is_yellow_and_done_is_green(arch):
    f, ids, root = arch
    res(f, ids["dept_home"], "img:1", "img", ids["aff_img"])  # now it has a resource row
    snap = snap_of(root, site_status=statuses(**{ACA: "error", AFFAIR: "unreconciled", DEPT: "done"}))
    s = {x["site"]: x for x in snap["sites"]}
    assert s[AFFAIR]["state"] == "unreconciled" and s[DEPT]["state"] == "done"
    assert snap["site_counts"]["unreconciled"] == 1 and snap["site_counts"]["done"] == 1
    page = progress_main_html(snap)
    assert "<span class='c-yellow' title='因为 unreconciled'>⚠ 纪录齐了，还没对账</span>" in page
    assert "<span class='c-green' title='因为 done'>✓ 爬完</span>" in page
    tiers = {t["tier"]: t for t in snap["tiers"]}
    assert tiers[0]["records_done"] == 1 and tiers[0]["done"] == 0 and tiers[1]["done"] == 1


def test_times_are_taipeis():
    now = ts(2026, 10, 26, 10, 0)  # 18:00 in Taipei
    assert progress.fmt_clock(ts(2026, 10, 26, 14, 10), now) == "今天 22:10"
    assert progress.fmt_clock(ts(2026, 10, 26, 18, 40), now) == "明天 02:40"
    assert progress.fmt_clock(ts(2026, 10, 25, 15, 5), now) == "昨天 23:05"
    assert progress.fmt_clock(ts(2026, 10, 29, 1, 0), now) == "10-29 09:00"
    assert progress.fmt_time(ts(2026, 10, 26, 8, 42, )) == "16:42:00"


def test_a_waiting_run_says_from_when_it_may_run_again_in_taipei_time(arch):
    _f, _ids, root = arch
    text = visible(progress_main_html(snap_of(root)))
    assert "先停下等重试" in text and "最早 明天 02:40 起" in text


def test_a_run_that_never_ended_is_said_so(arch):
    f, _ids, root = arch
    event(f, NOW - 60, "info", "run_start", json.dumps({"gap": [2.0, 5.0]}))
    assert snap_of(root)["run"]["state"] == "crashed"
    assert "上次没正常结束" in visible(progress_main_html(snap_of(root)))


def test_on_the_schools_network_the_dashboard_warns(arch):
    f, _ids, root = arch
    requests(f, Q_C, "c.nknu.edu.tw", 2, NOW - 30, campus="on")
    snap = snap_of(root)
    assert snap["campus"] == {"last": "on", "on": 2}
    assert "这台机器现在在校内网络" in visible(progress_main_html(snap))


def test_the_main_page_has_no_engineering_words(arch):
    f, _ids, root = arch
    f.pause(Q_D, NOW + 600, "dept.nknu.edu.tw: no connection")
    event(f, NOW - 60, "warn", "conn_pause", "dept.nknu.edu.tw does not connect: server queue ip:140.127.2.2 rests")
    text = visible(progress_main_html(snap_of(root)))
    for word in ("队列", "WARC", "frontier", "Crawl-delay", "postback", "pending", "soft-404", "ip:140"):
        assert word not in text, word
    assert "连不上这台服务器" in text  # the event, in plain words


def test_sites_go_by_importance_then_connection_failures_then_what_they_lack(arch):
    _f, _ids, root = arch
    snap = snap_of(root)
    order = [s["site"] for s in snap["sites"]]
    assert order == [ACA, AFFAIR, DEPT]  # 全校重要 first; 教务处 has a connection failure
    assert snap["sites"][0]["failed"] >= 1 and snap["sites"][0]["missing"] > snap["sites"][1]["missing"]


def test_the_total_leaves_out_rules_round_two_and_redirects(arch):
    _f, _ids, root = arch
    t = snap_of(root)["total"]
    # round 1: aca home, img, news, pdf, todo (moved is a redirect, other is round two) + affair 2 + dept 1
    assert t["all"] == 8 and t["done"] == 7
    assert t["all_hour_ago"] == 8


def test_servers_slowest_first_with_their_pace(arch):
    _f, _ids, root = arch
    snap = snap_of(root)
    s = snap["servers"][0]
    assert s["queue"] == Q_C and s["main_host"] == "c.nknu.edu.tw" and s["left"] == 1
    assert s["speed"]["c"] == pytest.approx(3.0) and s["status"]["words"] == "正常"
    assert "c.nknu.edu.tw 那台" in visible(progress_main_html(snap))


def test_keep_alive_not_working_on_cnknus_server_turns_red(arch):
    f, _ids, root = arch
    requests(f, Q_C, "c.nknu.edu.tw", 30, NOW - 300, new_conn=True)
    s = next(x for x in snap_of(root)["servers"] if x["queue"] == Q_C)
    assert s["status"]["color"] == "red" and "新连线" in s["status"]["words"]


def test_politeness_and_errors_blocks(arch):
    f, _ids, root = arch
    f.request(Q_C, "c.nknu.edu.tw", "https://c.nknu.edu.tw/aca/z", "page", NOW - 100, NOW - 99, None,
              "ConnectTimeout: timed out", ip="140.127.1.1")
    f.request(Q_C, "c.nknu.edu.tw", "https://c.nknu.edu.tw/aca/z2", "page", NOW - 99.5, NOW - 99, 503, None,
              ip="140.127.1.1")  # overlaps the one before, and the server said it was busy
    snap = snap_of(root)
    assert snap["polite"]["overlap"] == 1
    plain = {e["plain"] for e in snap["errors"]}
    assert progress.PLAIN["connect"] in plain and progress.PLAIN["busy"] in plain
    text = visible(progress_main_html(snap))
    assert "礼貌违反 1 次" in text


def test_plain_words_for_failures():
    assert progress.plain_error("http_503") == progress.PLAIN["busy"]
    assert progress.plain_error("http_500") == progress.PLAIN["http_5xx"]
    assert progress.plain_error("net_ReadError") == progress.PLAIN["net"]
    assert progress.plain_error(None, "[Errno 8] nodename nor servname provided") == progress.PLAIN["dns"]
    assert progress.plain_error(None, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed") == \
        progress.PLAIN["tls_cert"]
    assert progress.plain_error(None, "sso.nknu.edu.tw: http_500 (3 errors in a row)") == progress.PLAIN["http_5xx"]
    assert progress.plain_error(None, "Retry-After 600 s from sso.nknu.edu.tw") == progress.PLAIN["retry_after"]
    assert progress.plain_error("server_down") == progress.PLAIN["server_down"]
    assert progress.plain_error(None, None, 404) == progress.PLAIN["http_4xx"]
    assert progress.plain_error(None, "140.127.40.173: something") == progress.PLAIN["fetch"]


# ---------------------------------------------------------------- one site (§14.2)

def test_a_sites_page_never_lists_an_incomplete_page_as_got_or_rule(arch):
    _f, ids, root = arch
    d = dash(root)
    d.refresh_slow()
    detail = d.site(ACA)
    pages = {p["id"]: p for p in detail["pages"]}
    assert pages[ids["aca_news"]]["label"] == "failed"  # its attachment did not connect
    assert pages[ids["aca_home"]]["label"] == "complete"
    assert pages[ids["aca_home"]]["came_from"] == 1  # the old address that redirects here
    assert ids["aca_moved"] not in pages
    listed = {(r["what"], r["id"]): r["bucket"] for r in detail["rows"]}
    for pid in (ids["aca_news"], ids["aca_home"], ids["aca_todo"], ids["aca_moved"]):
        assert ("url", pid) not in listed  # pages show their completeness, never their own result
    assert listed[("url", ids["aca_pdf"])] == "failed" and listed[("url", ids["aca_img"])] == "got"
    # 连接失败 first, 抓到了 last; pages: 连接失败 first, 完整 last
    assert [r["bucket"] for r in detail["rows"]] == ["failed", "got"]
    assert [p["label"] for p in detail["pages"]] == ["failed", "todo", "complete"]
    page = progress_site_html(detail)
    assert "由 1 个网址跳来" in visible(page) and "连接失败" in visible(page)


def test_resources_with_no_row_are_listed_under_their_page(arch):
    f, ids, root = arch
    res(f, ids["aca_home"], "click:x", "click", None, why="unhandled:unknown")
    res(f, ids["aca_home"], "frame:y", "iframe", None, why=None)
    d = dash(root)
    d.refresh_slow()
    home = next(p for p in d.site(ACA)["pages"] if p["id"] == ids["aca_home"])
    got = {(x["res_key"], x["result"]) for x in home["lost"]}
    assert got == {("click:x", "unhandled"), ("frame:y", "not_discovered")}
    assert home["label"] == "review"


def test_a_resource_whose_address_is_an_error_page_is_listed_under_its_page_as_a_broken_link(arch):
    """Review 10-26 (清单②): outcome._kept_out says bad_link (要人看) for a part whose address is a school error page;
    the site's page said 规则不抓 and listed nothing under the page."""
    from nknu.archive import outcome

    f, ids, root = arch
    err = "https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/uploadfile/a.jpg"
    res(f, ids["aca_home"], err, "img", None, why=outcome.ERROR_OUT)
    d = dash(root)
    d.refresh_slow()
    home = next(p for p in d.site(ACA)["pages"] if p["id"] == ids["aca_home"])
    assert {(x["res_key"], x["result"]) for x in home["lost"]} == {(err, "bad_link")}
    assert home["label"] != "complete"
    assert "学校的链接坏了" in visible(progress_site_html(d.site(ACA)))


def test_a_sites_page_filters_by_category_and_by_page_label(arch):
    _f, ids, root = arch
    d = dash(root)
    d.refresh_slow()
    only = d.site(ACA, bucket="failed")
    assert [r["id"] for r in only["rows"]] == [ids["aca_pdf"]]
    assert [p["label"] for p in d.site(ACA, label="complete")["pages"]] == ["complete"]
    assert d.site("nowhere.example") is None


def test_a_press_is_listed_with_its_category(arch):
    f, ids, root = arch
    f.x("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
        "target, row_key, row_text, state, reason) VALUES (?, 1, 1, 'detail', ?, 'c.nknu.edu.tw', ?, 'c.nknu/x', "
        "'w', 0, 'gvFull', 'r1', '公告一', 'mismatch', 'title')", (ids["aca_news"], Q_C, ACA))
    d = dash(root)
    d.refresh_slow()
    rows = d.site(ACA)["rows"]
    press = next(r for r in rows if r["what"] == "press")
    assert press["bucket"] == "review" and press["zh"] == "按到的可能是别的公告"
    assert [r["bucket"] for r in rows][:2] == ["failed", "review"]


# ---------------------------------------------------------------- the routes (viewer.py, design §14.1)

@pytest.fixture
def served(arch):
    _f, _ids, root = arch
    v = Viewer(root)
    v._dash = dash(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(v))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def get(port: int, path: str, host: str | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_the_routes_answer_and_the_dashboard_comes_first(served):
    status, page = get(served, "/progress")
    assert status == 200 and "<main id='pmain'>" in page and "setInterval(tick, 30000)" in page
    nav = page.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert nav.startswith("<a href='/progress'>进度</a>") and "选择放行" not in nav
    status, part = get(served, "/progress?part=main")
    assert status == 200 and part.startswith("<main id='pmain'>") and "<nav>" not in part
    status, site = get(served, f"/progress/site?k={ACA}")
    assert status == 200 and "教务处" in site and "回进度看板" in site
    assert get(served, "/progress/site?k=nowhere")[0] == 200
    assert get(served, "/progress", host="evil.example")[0] == 403  # another site's name for this machine
    assert get(served, "/progress", host=f"localhost:{served}")[0] == 200


def test_the_dashboard_keeps_its_numbers_for_a_while(arch):
    _f, _ids, root = arch
    clock = [NOW]
    d = progress.Dashboard(root, clock=lambda: clock[0], background=False, site_status=statuses(),
                           checks=lambda conn, now, cheap: [])
    a = d.main()
    clock[0] += 10
    assert d.main() is a
    clock[0] += progress.CACHE_S
    assert d.main() is not a


def test_a_missing_or_old_archive_is_said_not_raised(tmp_path):
    d = progress.Dashboard(tmp_path, clock=lambda: NOW, background=False, site_status=statuses(),
                           checks=lambda conn, now, cheap: [])
    assert "找不到待抓清单" in d.main()["problem"]
    old = sqlite3.connect(tmp_path / "frontier.db")
    old.execute("CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT)")
    old.close()
    assert "旧版" in d.main()["problem"]
    assert "✗" in progress_main_html(d.main())
