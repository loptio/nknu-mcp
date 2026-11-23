"""The crawler dashboard v4's numbers (design docs/superpowers/specs/2026-11-04-crawler-dashboard-design.md, the owner's
11-04 notes): progress is the rows and presses with a result over those known; a card is the sum of its sites and a
site's rows on Google or the school's home page count for it, never twice in the total; the sites of the run are the
last run_start's; cards group sites by the server their home is on; 用时 runs from one request's start to the next,
capped; each state rule in its order, and never 在抓 without run.lock; 错 N counts each problem once (a cap, a parked
directory), lab only on campus, 待判定 only after the browser check, and always equals the lines of the problem log;
the log masks keys and carries no title; `archive status` writes it, and one frozen copy per ended run. The pages:
the home page says 3/25, 0/41 and h:mm:ss in few words; a server's page lists its sites working, queued (排队, never
0%), done (✓); the log page counts, filters and downloads the same lines; the home and server pages ask every 10
seconds and grey out after 2 minutes; every route answers on this machine's names only, and `archive browse --host`
listens where it is told. What the review of 11-04 found, fixed: run.lock held by another program is never a running
crawl; a wait whose restart never came says 没重开; the site page says 没抓完 while no run of it goes; a card takes
its worst server, red first; 在抓 needs a request in the last 2 minutes; an entry's ref is masked; a row pages of two
sites use is one line; bracketed notes go to the hover text. All on synthetic archives, no network; titles are made
up and O-masked."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from http.server import ThreadingHTTPServer
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import crawler, google_api, monitor, progress, viewer
from nknu.archive.frontier import Frontier
from nknu.archive.monitor import Issue
from nknu.archive.viewer import Viewer, home_html, log_html, make_handler, progress_site_html, server_html
from tests.archive.test_progress import NOW, add_url, event, plan, ts, visible

ACA, AFF, BM, ARTS = "c.nknu.edu.tw/aca", "c.nknu.edu.tw/affair", "c.nknu.edu.tw/bm", "c.nknu.edu.tw/arts"
UNIT = "sso.nknu.edu.tw/web/31"
C, SSO, SCHOOL_Q, OFF_Q = "ip:140.127.62.110", "ip:140.127.40.173", "ip:140.127.40.1", "ip:104.16.1.1"
GQ, FQ = google_api.QUEUE, google_api.FORMS_QUEUE
MIN, HOUR, DAY = 60.0, 3600.0, 86400.0


def press(f: Frontier, list_id: int, state: str, site: str = ACA, queue: str = C, created: float = NOW - HOUR,
          **cols) -> int:
    row = {"list_id": list_id, "epoch": 1, "step": 1, "kind": "pager", "queue": queue, "host": "c.nknu.edu.tw",
           "site": site, "profile": "c.nknu/x", "form_src_warc": "w", "form_src_offset": 0, "target": "gvFull",
           "row_key": f"p{time.monotonic_ns()}", "state": state, "created": created}
    row.update(cols)
    return f.conn.execute(f"INSERT INTO action({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                          tuple(row.values())).lastrowid


def req(f: Frontier, queue: str, host: str, site: str | None, start: float, took: float = 0.2, purpose: str = "page",
        campus: str | None = None, ip: str | None = None) -> float:
    scope = f"admit:r1:{site}:page" if site else purpose
    f.request(queue, host, f"https://{host}/r{time.monotonic_ns()}", purpose, start, start + took, 200, None,
              scope=scope, ip=ip or queue.removeprefix("ip:"), campus=campus)
    return start + took


def started(f: Frontier, sites: list[str] | None, at: float = NOW - HOUR, pid: int = 1, **more) -> None:
    event(f, at, "info", "run_start", json.dumps({"pid": pid, "gap": [2.0, 5.0], "sites": sites, **more}))


def ended(f: Frontier, at: float, reason: str = "waiting", sites: list[str] | None = None, **more) -> None:
    event(f, at, "info", "run_end", json.dumps({"reason": reason, "sites": sites, "pending": 0, **more}))


@pytest.fixture
def lab(tmp_path):
    """Three 全校重要 sites on c.nknu's server and one SSO site on a server of its own; Google's two queues, the school's
    home page and an off-campus host; every site's home page fetched."""
    f = Frontier(tmp_path / "frontier.db")
    for site, name in ((ACA, "教务处"), (AFF, "总务处"), (BM, "测试系")):
        plan(f, site, name, "全校重要")
    plan(f, UNIT, "某中心", "本系重要")
    plan(f, "other.nknu.edu.tw", "其他", None, rnd="2")
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', ?, 'ok', 5)", (ACA,))
    for host, queue in (("c.nknu.edu.tw", C), ("sso.nknu.edu.tw", SSO), (google_api.API_HOST, GQ),
                        ("docs.google.com", FQ), ("www.nknu.edu.tw", SCHOOL_Q), ("reurl.cc", OFF_Q)):
        f.set_host(host, queue=queue)
    ids = {}
    for site in (ACA, AFF, BM):
        ids[site] = add_url(f, url=f"https://{site}/default.aspx", site=site, owner_site=site, found_via="seed")
    ids[UNIT] = add_url(f, url=f"https://{UNIT}/default.aspx", host="sso.nknu.edu.tw", site=UNIT, owner_site=UNIT,
                        tier=1, found_via="seed")
    yield f, ids, tmp_path
    f.close()


def numbers(f: Frontier, now: float = NOW, running: str | None = None, *, slow: bool = False,
            tracker: progress.Tracker | None = None) -> tuple[dict, dict | None]:
    conn = progress.connect(f.path)
    try:
        bg = progress.slow_snapshot(conn, now, site_status=monitor.site_status, facts=monitor.facts) if slow else None
        return progress.snapshot(conn, now, tracker=tracker, slow=bg, running=running), bg
    finally:
        conn.close()


def card(snap: dict, key: str) -> dict:
    return next(c for c in snap["cards"] + snap["small"] if c["key"] == key)


def row(snap: dict, site: str) -> dict:
    return next(r for c in snap["cards"] for r in c["rows"] if r["site"] == site)


# ---------------------------------------------------------------- §2.2 progress

def test_progress_is_rows_with_a_result_over_rows_known_presses_included(lab):
    f, ids, _root = lab
    for state, reason in (("pending", None), ("active", None), ("done", None), ("failed", "connect"),
                          ("held", "cap:site"), ("skipped", "out:social"), ("later", "round2:offsite")):
        add_url(f, kind="file", kind0="file", page_type=None, sniff="pdf" if state == "done" else None, state=state,
                reason=reason, status=200 if state == "done" else None)
    for state in ("pending", "done", "failed", "mismatch", "superseded"):
        press(f, ids[ACA], state)
    got = row(numbers(f)[0], ACA)
    # known: the home page, pending, active, done, failed and held (skipped and later aside), 4 presses (superseded aside)
    assert (got["known"], got["finished"], got["left"]) == (6 + 4, 4 + 3, 2 + 1)
    assert got["pct"] == "70%" and not got["done"]


def test_a_card_is_the_sum_of_its_sites_and_full_only_when_every_site_is(lab):
    f, _ids, _root = lab
    left = add_url(f, url="https://c.nknu.edu.tw/affair/n.aspx", site=AFF, owner_site=AFF, state="pending",
                   status=None, sniff=None)
    snap = numbers(f)[0]
    c = card(snap, C)
    assert c["name"] == "c.nknu 主机" and (c["sites_done"], c["sites_total"]) == (2, 3)
    assert c["finished"] == sum(r["finished"] for r in c["rows"]) == 3 and c["known"] == 4
    assert c["pct"] == "75%" and snap["total"]["known"] == sum(x["known"] for x in snap["cards"])
    f.x("UPDATE url SET state='done', status=200 WHERE id=?", (left,))
    c = card(numbers(f)[0], C)
    assert c["pct"] == "100%" and (c["sites_done"], c["sites_total"]) == (3, 3) and c["state"]["code"] == "done"


def test_a_sites_rows_on_google_and_the_school_home_count_for_it_and_in_the_small_cards_never_twice(lab):
    f, _ids, _root = lab
    add_url(f, url=google_api.media_url("1AbCdEfGhIjKlMnOp"), host=google_api.API_HOST, kind="file", kind0="drive",
            page_type=None, sniff="pdf")
    add_url(f, url="https://www.nknu.edu.tw/files/a.pdf", host="www.nknu.edu.tw", kind="file", kind0="file",
            page_type=None, sniff=None, state="pending", status=None)
    snap = numbers(f)[0]
    aca = row(snap, ACA)
    assert (aca["known"], aca["finished"]) == (3, 2)
    assert (card(snap, "google")["finished"], card(snap, "google")["known"]) == (1, 1)
    assert (card(snap, "school")["finished"], card(snap, "school")["known"]) == (0, 1)
    assert "offsite" not in {c["key"] for c in snap["small"]}  # a small card shows only when it holds the run's rows
    assert snap["total"]["known"] == sum(c["known"] for c in snap["cards"]) == 6  # the small cards never add to it


# ---------------------------------------------------------------- §2.7, §2.1 the sites of the run and their cards

def test_the_sites_are_the_last_run_starts_every_round1_site_without_one_and_only_is_noted(lab):
    f, _ids, _root = lab
    assert numbers(f)[0]["scope"].sites == tuple(sorted((ACA, AFF, BM, UNIT)))  # no run yet: round 1, never round 2
    started(f, [BM, ACA], at=NOW - 3 * HOUR)
    ended(f, NOW - 2 * HOUR, sites=[ACA, BM])
    started(f, [ACA, BM], at=NOW - HOUR)  # the same sites again: the same run of them
    scope = numbers(f)[0]["scope"]
    assert scope.sites == (ACA, BM) and scope.first_row < scope.start_row
    assert scope.started_at == pytest.approx(NOW - 3 * HOUR, abs=1)
    started(f, [ACA], at=NOW - MIN, only=3, only_pending=3)
    snap = numbers(f)[0]
    assert snap["scope"].sites == (ACA,) and snap["only"] == 3 and snap["scope"].first_row == snap["scope"].start_row
    started(f, None, at=NOW)
    assert numbers(f)[0]["scope"].sites == tuple(sorted((ACA, AFF, BM, UNIT)))


def test_cards_go_by_the_server_of_each_sites_home_and_are_named_as_the_owner_reads_them(lab):
    f, _ids, _root = lab
    for n in (2, 3):  # SSO's server home to three sites: a card of its own
        site = f"sso.nknu.edu.tw/web/{n}"
        plan(f, site, f"单位{n}", "本系重要")
        add_url(f, url=f"https://{site}/default.aspx", host="sso.nknu.edu.tw", site=site, owner_site=site, tier=1)
    plan(f, ARTS, "艺术", "本系重要")  # its home is on a server of its own, with nobody else's
    f.x("UPDATE site_plan SET home='https://arts.nknu.edu.tw/' WHERE site=?", (ARTS,))
    f.set_host("arts.nknu.edu.tw", queue="ip:140.127.9.9")
    add_url(f, url="https://arts.nknu.edu.tw/", host="arts.nknu.edu.tw", site=ARTS, owner_site=ARTS, tier=1)
    f.set_host("gone.example.org", queue="dns-fail")
    for url, host in (("https://reurl.cc/AbC", "reurl.cc"), ("https://gone.example.org/x", "gone.example.org"),
                      ("https://docs.google.com/forms/d/e/x/viewform", "docs.google.com")):
        add_url(f, url=url, host=host, kind="file", kind0="file", page_type=None, sniff=None, state="pending",
                status=None)
    snap = numbers(f)[0]
    assert [(c["key"], c["name"], c["sites_total"]) for c in snap["cards"]] == [
        (C, "c.nknu 主机", 3), (SSO, "SSO 主机", 3), (progress.UNITS, "各单位自己的主机", 1)]
    assert [r["host"] for r in card(snap, progress.UNITS)["rows"]] == ["arts.nknu.edu.tw"]  # each row names its host
    assert {c["key"]: c["name"] for c in snap["small"]} == {"google": "Google 下载和表单", "offsite": "校外网站"}
    assert "ip:140.127.62.110" in card(snap, C)["tip"] and "c.nknu.edu.tw" in card(snap, C)["tip"]
    assert "查不到地址" in card(snap, "offsite")["tip"]  # dns-fail: in the tooltip, never a state of its own


# ---------------------------------------------------------------- §2.3 用时

def _rows(*spec) -> list[dict]:
    """(queue, host, site, start, took[, purpose]) → request rows as the tracker reads them."""
    out = []
    for q, host, site, start, took, *purpose in spec:
        p = purpose[0] if purpose else "page"
        out.append({"queue": q, "host": host, "purpose": p, "started": start, "ended": start + took,
                    "scope": f"admit:r1:{site}:page" if site else p})
    return out


def test_busy_time_runs_to_the_next_start_at_most_own_time_and_a_minute_and_skips_browser_checks():
    t = NOW
    rows = _rows((C, "c.nknu.edu.tw", ACA, t, 0.5), (C, "c.nknu.edu.tw", ACA, t + 3, 0.5),
                 (C, "c.nknu.edu.tw", ACA, t + 4, 0.2, "jscheck"),  # a browser check: no crawling
                 (C, "c.nknu.edu.tw", BM, t + 6, 0.5),
                 (C, "c.nknu.edu.tw", BM, t + 6 + 20 * MIN, 0.5),  # a 20-minute rest: 0.5 + 60 counts
                 ("ip:140.127.9.9", "arts.nknu.edu.tw", ARTS, t, 1.0),
                 ("ip:140.127.9.9", "arts.nknu.edu.tw", ARTS, t + 2 * MIN, 1.0),  # its Crawl-delay 90: 1 + 95
                 (GQ, google_api.API_HOST, ACA, t, 2.0), (GQ, google_api.API_HOST, ACA, t + 30, 2.0))
    b = progress.Busy()
    b.feed(rows, {"arts.nknu.edu.tw": 90.0})
    got = b.seconds(None)
    assert got[(C, ACA)] == pytest.approx(3 + 3)
    assert got[(C, BM)] == pytest.approx(60.5 + 0.5)  # the last one: its own time (no run holds the lock)
    assert got[("ip:140.127.9.9", ARTS)] == pytest.approx(96 + 1)
    assert got[(GQ, ACA)] == pytest.approx(30 + 2)
    assert progress.scope_site("admit:resource:-:file") is None and progress.scope_site("campus") is None


def test_busy_time_fed_in_pieces_is_the_same_and_the_open_segment_runs_while_the_lock_is_held():
    rows = _rows(*((C, "c.nknu.edu.tw", ACA, NOW + 4 * n, 0.5) for n in range(10)))
    whole, parts = progress.Busy(), progress.Busy()
    whole.feed(rows, {})
    parts.feed(rows[:4], {})
    parts.feed(rows[4:], {})
    assert whole.seconds(None) == parts.seconds(None) == {(C, ACA): pytest.approx(9 * 4 + 0.5)}
    last = NOW + 36
    assert whole.seconds(last + 30)[(C, ACA)] == pytest.approx(36 + 30)  # running: the last one counts until now …
    assert whole.seconds(last + 3600)[(C, ACA)] == pytest.approx(36 + 60.5)  # … at most a minute past its own time
    assert whole.seconds(last + 3600, [last + 10])[(C, ACA)] == pytest.approx(36 + 10)  # stopped: until its run_end


def test_busy_time_goes_to_the_site_in_its_scope_and_on_googles_queue_to_the_small_card(lab):
    f, _ids, _root = lab
    t = req(f, C, "c.nknu.edu.tw", ACA, NOW - 600)
    req(f, C, "c.nknu.edu.tw", BM, t + 2.8)
    req(f, GQ, google_api.API_HOST, ACA, NOW - 500, took=1.0)
    req(f, GQ, google_api.API_HOST, ACA, NOW - 470, took=1.0)
    add_url(f, url=google_api.media_url("1AbCdEfGhIjKlMnOp"), host=google_api.API_HOST, kind="file", kind0="drive",
            page_type=None, sniff="pdf")
    snap = numbers(f)[0]
    assert row(snap, ACA)["busy"] == pytest.approx(3.0) and row(snap, BM)["busy"] == pytest.approx(0.2)
    assert card(snap, "google")["busy"] == pytest.approx(31.0)  # never the site's: no more than the wall clock
    assert card(snap, C)["busy"] == pytest.approx(3.2)


def test_times_are_hours_minutes_and_seconds_and_percentages_never_say_done_too_early():
    assert progress.fmt_hms(5) == "0:00:05" and progress.fmt_hms(2 * HOUR + 47 * MIN + 18) == "2:47:18"
    assert progress.fmt_hms(DAY + 2 * HOUR + 5 * MIN + 9) == "26:05:09" and progress.fmt_hms(None) == "—"
    assert progress.fmt_pct(1, 250) == "0.4%" and progress.fmt_pct(497, 1000) == "49%"
    assert progress.fmt_pct(999, 1000) == "99.9%" and progress.fmt_pct(99_999, 100_000) == "99.9%"
    assert progress.fmt_pct(41, 41) == progress.fmt_pct(0, 0) == "100%" and progress.fmt_pct(0, 41) == "0.0%"


# ---------------------------------------------------------------- §2.5 states

def _work(**kw) -> dict:
    w = progress._work()
    w.update(left=5, due=5, hosts=Counter({"c.nknu.edu.tw": 5}), by_site=Counter({ACA: 5}), first_due=NOW)
    w.update(kw)
    return w


def _env(**kw) -> dict:
    env = {"pauses": {}, "servers": {}, "block": (0, None), "last_started": {}, "run_started": NOW - HOUR,
           "running": True, "delay": {}, "card_of_queue": {GQ: "google", C: C}, "card_names": {C: "c.nknu 主机"},
           "run": {"running": "1", "hung": False, "words": "在跑 1:00:00", "tip": ""}}
    env.update(kw)
    return env


def state(queue: str = C, w: dict | None = None, **env) -> dict:
    return progress.queue_state(queue, _work() if w is None else w, _env(**env), NOW)


def test_each_state_rule_in_its_order():
    clock = progress._hm
    assert state(pauses={"*": (NOW + 300, "the network is down")})["words"] == "网络断了"
    assert state(pauses={"net:school": (NOW + 300, "")})["code"] == "offline"
    assert state(GQ, pauses={"net:school": (NOW + 300, "")}, last_started={GQ: NOW})["code"] == "working"
    blocked = {GQ: (NOW + HOUR, f"{google_api.API_HOST}: api:automated_queries")}
    s = state(GQ, pauses=blocked)
    assert (s["words"], s["color"]) == ("被 Google 挡", "yellow") and progress.PLAIN["google_block"] in s["tip"]
    assert state(GQ, pauses=blocked, block=(6, NOW - DAY))["color"] == "red"
    for why in ("no_api_key", "api_key: keyInvalid"):
        s = state(GQ, pauses={GQ: (NOW + HOUR, why)})
        assert (s["words"], s["color"]) == ("没有 Google 金钥", "red")
    s = state(servers={C: (2, 0)})
    assert (s["words"], s["color"]) == ("连不上", "yellow")
    assert state(servers={C: (8, 0)})["color"] == "red"
    assert state(pauses={C: (NOW + 900, "c.nknu.edu.tw: no connection")})["code"] == "conn"
    rest = state(pauses={C: (NOW + 900, "c.nknu.edu.tw: http_503")})
    assert rest["words"] == f"休息中 {clock(NOW + 900, NOW)}" and rest["color"] == "yellow"
    cooling = _work(due=0, cooling={"c.nknu.edu.tw": NOW + 600})
    assert state(w=cooling)["words"] == f"休息中 {clock(NOW + 600, NOW)}"
    assert state(last_started={C: NOW - 20 * MIN})["code"] == "stuck"  # due work, no request for 20 minutes
    assert state(last_started={C: NOW - 20 * MIN}, delay={C: 300.0})["code"] == "quiet"  # 15 past its Crawl-delay
    assert state(last_started={C: NOW - 6 * MIN}, delay={C: 300.0})["code"] == "working"  # its Crawl-delay is long
    assert state(last_started={C: NOW - 20 * MIN}, w=_work(active=1))["code"] == "working"  # a long download
    assert state(last_started={C: NOW - 10})["words"] == "在抓"
    waiting = state(w=_work(due=0, first_due=NOW + 1800))
    assert waiting["words"] == f"等重试 {clock(NOW + 1800, NOW)}" and waiting["code"] == "retry_wait"
    assert progress.queue_state("dns-fail", _work(), _env(), NOW) is None  # never a state of its own


def test_a_cards_state_is_done_not_running_hung_its_queues_worst_or_waiting_on_google():
    c = progress.Card(C, "c.nknu 主机", False, (C,), (ACA,), "")
    nums = {ACA: {"left": 5}}
    assert progress.card_state(c, nums, {}, _env(), NOW)["words"] == "完成"
    work = {C: _work(), GQ: _work(hosts=Counter({google_api.API_HOST: 5}))}
    idle = _env(run={"running": None, "hung": False, "words": "你停的", "tip": ""})
    assert progress.card_state(c, nums, work, idle, NOW)["words"] == "没在跑"
    hung = _env(run={"running": "1", "hung": True, "words": "爬虫没反应", "tip": ""})
    s = progress.card_state(c, nums, work, hung, NOW)
    assert (s["words"], s["color"]) == ("爬虫没反应", "red")
    resting = _env(pauses={C: (NOW + 900, "c.nknu.edu.tw: http_503")}, last_started={C: NOW})
    assert progress.card_state(c, nums, work, resting, NOW)["code"] == "rest"
    only_google = {GQ: work[GQ]}
    blocked = _env(pauses={GQ: (NOW + HOUR, f"{google_api.API_HOST}: api:automated_queries")})
    got = progress.card_state(c, nums, only_google, blocked, NOW)
    assert got["words"] == "等 Google" and "被 Google 挡" in got["tip"]  # never 完成


def test_no_card_says_working_while_no_run_holds_the_lock(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/aca/todo.aspx", state="pending", status=None, sniff=None)
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 30)
    started(f, None, at=NOW - HOUR)
    assert card(numbers(f, running="1")[0], C)["state"]["words"] == "在抓"
    snap = numbers(f, running=None)[0]  # the run was killed: no run_end, no lock
    assert snap["run"]["words"] == "意外停了" and card(snap, C)["state"]["words"] == "没在跑"
    assert all(c["state"]["code"] != "working" for c in snap["cards"] + snap["small"])


def test_a_run_that_holds_the_lock_and_wrote_no_beat_for_minutes_is_hung(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/aca/todo.aspx", state="pending", status=None, sniff=None)
    started(f, None, at=NOW - HOUR)
    snap = numbers(f, running="1")[0]
    assert snap["run"]["words"] == "在跑 1:00:00" and not snap["run"]["hung"]  # no beat written: no such check
    f.meta("run:alive_at", f"{NOW - 5 * MIN:.3f}")
    snap = numbers(f, running="1")[0]
    assert snap["run"]["words"] == "爬虫没反应" and card(snap, C)["state"]["words"] == "爬虫没反应"
    f.meta("run:alive_at", f"{NOW - 30:.3f}")
    assert numbers(f, running="1")[0]["run"]["words"] == "在跑 1:00:00"


def test_the_top_line_says_why_no_run_goes(lab):
    f, _ids, _root = lab
    assert numbers(f)[0]["run"]["words"] == "还没开始"
    started(f, None, at=NOW - 2 * HOUR)
    for reason, words in (("sigterm", "你停的"), ("stop_file", "你停的"), ("disk_low", "出错停了"), ("finished", "抓完了")):
        ended(f, NOW - HOUR, reason)
        assert numbers(f)[0]["run"]["words"] == words, reason
    ended(f, NOW - HOUR, "waiting", next_due=ts(2026, 10, 26, 18, 40))  # 02:40 tomorrow in Taipei
    assert numbers(f)[0]["run"]["words"] == "等重试 明天 02:40 再开"


# ---------------------------------------------------------------- §2.4 错 N and §1.3 the problem log

@pytest.fixture
def troubled(lab):
    """Problems of every kind: 教务处 a failed attachment, a cap's three held pages, a parked directory's two files and
    a lab file; 总务处 a refused page (its title O-masked), a page like a missing one and a list not walked; 测试系 a
    page whose type is still to be judged; and what never counts (a retry, a 404, a sign-in, a rule, round two)."""
    f, ids, root = lab
    news = add_url(f, url="https://c.nknu.edu.tw/aca/news.aspx", title="公告")
    pdf = add_url(f, url="https://c.nknu.edu.tw/aca/a.pdf", kind="file", kind0="file", page_type=None, sniff=None,
                  state="failed", reason="connect", status=None, attempts=3)
    f.add_page_res(news, [{"res_key": "file:1", "how": "get", "role": "file", "need": "must", "res_id": pdf}])
    for n in range(3):
        add_url(f, url=f"https://c.nknu.edu.tw/aca/list.aspx?p={n}", state="held", reason="cap:site", status=None,
                sniff=None, page_type=None)
    for n in range(2):
        add_url(f, url=f"https://c.nknu.edu.tw/aca/uploadfile/{n}.jpg", kind="image", kind0="image", page_type=None,
                sniff=None, state="failed", reason="denied_dir", status=None)
    lab_row = add_url(f, url="https://c.nknu.edu.tw/aca/inner.pdf", kind="file", kind0="file", page_type=None,
                      sniff=None, state="failed", reason="host_unreachable", status=None)
    key = "AIza" + "x" * 35
    refused = add_url(f, url=f"https://c.nknu.edu.tw/affair/list.aspx?key={key}&gkey=abc", site=AFF, owner_site=AFF,
                      status=403, title="王O明的名单")
    add_url(f, url="https://c.nknu.edu.tw/affair/detail.aspx?id=9", site=AFF, owner_site=AFF, flags="soft404")
    walk = add_url(f, url="https://c.nknu.edu.tw/affair/newslist.aspx", site=AFF, owner_site=AFF, page_role="list")
    f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, rows, ended_by, ok, at) VALUES "
        "(?, ?, 'c.nknu/x', 1, 3, 30, 'mismatch:page', 0, ?)", (walk, AFF, NOW - 900))
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:todo', ?, 'todo', 1)", (BM,))
    todo_page = add_url(f, url="https://c.nknu.edu.tw/bm/x.aspx", site=BM, owner_site=BM, page_type="t:todo")
    never = [add_url(f, url=f"https://c.nknu.edu.tw/bm/{n}", site=BM, owner_site=BM, kind="file", kind0="file",
                     page_type=None, sniff=s, **cols)
             for n, (s, cols) in enumerate([
                 (None, {"state": "pending", "attempts": 1, "next_at": NOW + 600, "status": None}),
                 ("html", {"status": 404}), ("html", {"status": 401, "flags": "login"}),
                 (None, {"state": "skipped", "reason": "out:social", "status": None}),
                 (None, {"state": "later", "reason": "round2:offsite", "status": None})])]
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 300, campus="off")
    started(f, None, at=NOW - 2 * HOUR)
    ended(f, NOW - HOUR, "finished")
    return f, {**ids, "pdf": pdf, "lab": lab_row, "refused": refused, "walk": walk, "todo": todo_page,
               "never": never}, root, key


def test_the_errors_are_the_log_lines_per_site_per_card_and_in_the_top_line(troubled):
    f, _ids, _root, _key = troubled
    snap, slow = numbers(f, slow=True)
    by_site = slow["errors_by_site"]
    assert by_site[ACA] == {"n": 3, "failed": 1, "review": 2, "pending": 0}  # the attachment, the cap, the directory
    assert by_site[AFF] == {"n": 3, "failed": 1, "review": 2, "pending": 0}  # refused, looks_gone, the list
    assert BM not in by_site  # 待判定 waits for the browser check
    whole = progress.log_view(snap, slow)
    assert snap["errors"]["n"] == whole["n"] == len(whole["rows"]) == 6
    assert (snap["errors"]["failed"], snap["errors"]["color"]) == (2, "red")
    for c in snap["cards"]:
        assert c["errors"]["n"] == sum(r["errors"]["n"] for r in c["rows"]) == len(
            progress.log_view(snap, slow, card=c["key"])["rows"])
        for r in c["rows"]:
            assert r["errors"]["n"] == len(progress.log_view(snap, slow, site=r["site"])["rows"])


def test_what_never_counts_and_a_page_no_browser_judged_is_no_error(troubled):
    """Since the owner's 11-16 decision (direction §四 2, export batch T5) a type no browser check judged is 抓到了:
    nothing waits for the browser check any more, before or after it ends."""
    f, ids, _root, _key = troubled
    snap, slow = numbers(f, slow=True)
    refs = {r["ref"] for r in progress.log_view(snap, slow)["rows"]}
    assert not refs & {f"u{i}" for i in ids["never"]} and f"p{ids['todo']}" not in refs
    event(f, NOW - 30 * MIN, "info", "jscheck_end", json.dumps({"reason": "finished", "sites": None}))
    snap, slow = numbers(f, slow=True)
    lines = progress.log_view(snap, slow, site=BM)["rows"]
    assert f"p{ids['todo']}" not in {x["ref"] for x in lines}
    assert row(snap, BM)["errors"]["pending"] == 0


def test_a_row_listed_as_a_page_and_as_a_resource_is_one_problem(lab):
    f, ids, _root = lab
    frame = add_url(f, url="https://c.nknu.edu.tw/aca/frame.aspx", status=403)
    f.add_page_res(ids[ACA], [{"res_key": "frame:1", "how": "get", "role": "iframe", "need": "must", "res_id": frame}])
    snap, slow = numbers(f, slow=True)
    assert {f"p{frame}", f"u{frame}"} <= {i.ref for i in slow["statuses"][ACA].issues}  # the monitor lists it twice
    assert [x["ref"] for x in progress.log_view(snap, slow, site=ACA)["rows"]] == [f"p{frame}"]
    assert snap["errors"]["n"] == row(snap, ACA)["errors"]["n"] == 1


def test_one_cap_and_one_parked_directory_are_one_line_each(troubled):
    f, _ids, _root, _key = troubled
    snap, slow = numbers(f, slow=True)
    lines = {x["code"]: x for x in progress.log_view(snap, slow, site=ACA)["rows"]}
    assert lines["held"]["n"] == 3 and "cap:site · 3 个网址没抓" in lines["held"]["detail"]
    assert lines["parked"]["n"] == 2 and "2 个网址没抓" in lines["parked"]["detail"]
    assert lines["parked"]["url"] == "https://c.nknu.edu.tw/aca/uploadfile/"


def test_a_lab_row_is_a_failure_only_on_campus_and_once_it_failed(troubled):
    f, ids, _root, _key = troubled
    snap, slow = numbers(f, slow=True)
    assert f"u{ids['lab']}" not in {r["ref"] for r in progress.log_view(snap, slow)["rows"]}  # off campus: it waits
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 60, campus="on")
    snap, slow = numbers(f, slow=True)
    lab_line = next(r for r in progress.log_view(snap, slow)["rows"] if r["ref"] == f"u{ids['lab']}")
    assert (lab_line["kind"], lab_line["code"]) == ("failed", "lab") and slow["errors_by_site"][ACA]["failed"] == 2
    f.x("UPDATE url SET state='pending' WHERE id=?", (ids["lab"],))  # waiting for its turn on campus: not yet
    snap, slow = numbers(f, slow=True)
    assert f"u{ids['lab']}" not in {r["ref"] for r in progress.log_view(snap, slow)["rows"]}


def test_the_badge_is_red_with_a_failure_yellow_with_review_only_and_absent_at_zero(lab):
    f, _ids, _root = lab
    snap, _slow = numbers(f, slow=True)
    assert snap["errors"]["n"] == 0 and snap["errors"]["color"] == "none"
    assert "错 " not in visible(home_html(snap))
    add_url(f, url="https://c.nknu.edu.tw/aca/detail.aspx?id=9", flags="soft404")
    snap, _slow = numbers(f, slow=True)
    assert (snap["errors"]["n"], snap["errors"]["color"]) == (1, "yellow") and "错 1" in visible(
        home_html(snap))
    assert "失败 0 · 要人看 1" in home_html(snap)  # the hover text
    early = numbers(f)[0]  # before the background snapshot: a dim 「错 …」 that says why, and a log that says so
    assert early["errors"]["n"] is None and "错 …" in visible(home_html(early))
    view = progress.log_view(early, None)
    assert view["rows"] == [] and "还在算" in view["error"]
    assert "# 错的条数还没有：错的条数还在算" in progress.log_text(view, f.path)


def test_politeness_shows_only_when_broken_and_googles_two_queues_on_one_address_are_never_two_connections(lab):
    f, _ids, _root = lab
    ip = "142.250.1.1"
    f.request(FQ, "docs.google.com", "https://docs.google.com/forms/x", "page", NOW - 100, NOW - 98, 200, None, ip=ip)
    f.request(GQ, google_api.API_HOST, "https://www.googleapis.com/drive/v3/files/x", "page", NOW - 99, NOW - 97,
              200, None, ip=ip)
    snap = numbers(f)[0]
    assert snap["polite"]["hard"] == 0 and "礼貌" not in visible(home_html(snap))
    f.request(C, "c.nknu.edu.tw", "https://c.nknu.edu.tw/aca/z", "page", NOW - 50, NOW - 40, 200, None,
              ip="140.127.62.110")
    f.request(C, "c.nknu.edu.tw", "https://c.nknu.edu.tw/aca/z2", "page", NOW - 45, NOW - 44, 200, None,
              ip="140.127.62.110")  # sent while the one before was still going
    snap = numbers(f)[0]
    assert snap["polite"]["overlap"] == 1 and "礼貌 ✗ 1" in visible(home_html(snap))
    record = snap["polite"]["records"][0]
    assert (record["kind"], record["server"]) == ("overlap", "140.127.62.110")


def test_a_log_line_carries_no_title_and_masks_keys_and_secret_parameters(troubled):
    f, ids, root, key = troubled
    snap, slow = numbers(f, slow=True)
    text = progress.log_text(progress.log_view(snap, slow), root / "frontier.db")
    assert "王O明" not in text and "公告" not in text and key not in text and "gkey=abc" not in text
    line = next(x for x in text.splitlines() if f"\tp{ids['refused']}\t" in x)
    assert "key=…&gkey=…" in line and "失败·被拒绝（401、403、451）" in line and "HTTP 403" in line
    assert progress.mask_text(f"https://x.example/a?token=abc&id=1 and {key}") == (
        "https://x.example/a?token=…&id=1 and [redacted]")


def test_the_log_filters_by_site_card_and_code_and_server_events_show_in_card_views_only(troubled):
    f, _ids, _root, _key = troubled
    event(f, NOW - 600, "warn", "server_rest", "c.nknu.edu.tw: http_503: server queue rests 15 min",
          host="c.nknu.edu.tw")
    event(f, NOW - 500, "error", "disk_low", "less than 5 GB free")  # names no host: every card's
    event(f, NOW - 400, "warn", "robots_unparsed", "robots.txt unreadable", host="c.nknu.edu.tw")  # a note
    started(f, None, at=NOW - 30 * MIN)
    snap, slow = numbers(f, slow=True)
    whole = progress.log_view(snap, slow)
    assert [e["code"] for e in whole["events"]] == ["disk_low", "server_rest"] and whole["n"] == 6
    assert progress.PLAIN["server_rest"] == whole["events"][1]["label"]
    by_card = progress.log_view(snap, slow, card=C)
    assert {e["code"] for e in by_card["events"]} == {"server_rest", "disk_low"} and by_card["n"] == 6
    assert progress.log_view(snap, slow, card="google")["events"] == [whole["events"][0]]
    assert progress.log_view(snap, slow, site=AFF)["events"] == []  # never in a site's view
    looks = progress.log_view(snap, slow, code="looks_gone")
    assert [r["code"] for r in looks["rows"]] == ["looks_gone"] and looks["n"] == 6  # the counts stay the whole's
    assert {c["code"]: c["n"] for c in whole["codes"]}["held"] == 1


def test_the_log_file_is_tab_separated_with_header_lines_and_the_pages_lines(troubled):
    f, _ids, root, _key = troubled
    snap, slow = numbers(f, slow=True)
    view = progress.log_view(snap, slow)
    text = progress.log_text(view, root / "frontier.db")
    lines = text.splitlines()
    heads = [x for x in lines if x.startswith("#")]
    assert heads[0].startswith("# 爬虫问题记录") and "6 条（失败 2 · 要人看 4 · 待判定 0）" in heads[1]
    assert f"c.nknu 主机={C}" in heads[2] and heads[3] == "# " + "\t".join(progress.LOG_COLUMNS)
    body = [x.split("\t") for x in lines if not x.startswith("#")]
    assert all(len(x) == 8 for x in body) and len(body) == len(view["rows"])
    assert [x[3] for x in body] == [r["ref"] for r in reversed(view["rows"])]  # oldest first in the file
    assert body[0][0][:4] == "2026" and body[-1][2] in (ACA, AFF)


def test_archive_status_writes_the_latest_log_and_one_frozen_copy_per_ended_run(troubled):
    f, _ids, root, _key = troubled
    f.close()
    runner = CliRunner()
    first = runner.invoke(app, ["archive", "status", "--dir", str(root)])
    assert first.exit_code in (0, 2), first.output
    reports = root / "reports"
    frozen = sorted(reports.glob("problems-2*.log"))
    assert (reports / progress.LATEST_LOG).exists() and len(frozen) == 1 and "问题记录" in first.output
    assert "6 条" in (reports / progress.LATEST_LOG).read_text(encoding="utf-8").splitlines()[1]
    again = runner.invoke(app, ["archive", "status", "--dir", str(root)])
    assert again.exit_code == first.exit_code and sorted(reports.glob("problems-2*.log")) == frozen


def test_a_running_run_writes_no_frozen_copy(lab):
    f, _ids, root = lab
    started(f, None, at=NOW - HOUR)
    snap, slow = numbers(f, running="1", slow=True)
    written = progress.write_logs(root, snap, slow)
    assert written == [root / "reports" / progress.LATEST_LOG]


# ---------------------------------------------------------------- §1.1, §1.2 what the pages are given

def test_discovering_while_a_site_gained_rows_in_the_last_ten_minutes(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/aca/new.aspx", state="pending", status=None, sniff=None, added_at=NOW - 60)
    add_url(f, url="https://c.nknu.edu.tw/affair/old.aspx", site=AFF, owner_site=AFF, state="pending", status=None,
            sniff=None, added_at=NOW - 3600)
    snap = numbers(f)[0]
    assert row(snap, ACA)["discovering"] and not row(snap, AFF)["discovering"] and card(snap, C)["discovering"]


def test_a_servers_sites_go_working_queued_done_and_one_not_reached_yet_is_queued(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/aca/todo.aspx", state="pending", status=None, sniff=None)
    f.x("UPDATE url SET state='pending', status=NULL WHERE owner_site=?", (BM,))
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 30)
    started(f, None, at=NOW - HOUR)  # the run that holds the lock (pid 1)
    rows = card(numbers(f, running="1")[0], C)["rows"]
    assert [(r["site"], r["order"]) for r in rows] == [(ACA, 0), (BM, 1), (AFF, 2)]
    assert rows[1]["queued"] and not rows[0]["queued"] and rows[2]["done"]


def test_the_home_page_says_three_of_twenty_five_and_hours_minutes_seconds_and_none_of_the_old_words(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/affair/todo.aspx", site=AFF, owner_site=AFF, state="pending", status=None,
            sniff=None)
    add_url(f, url="https://www.nknu.edu.tw/a.pdf", host="www.nknu.edu.tw", site=AFF, owner_site=AFF, kind="file",
            kind0="file", page_type=None, sniff=None, state="pending", status=None)
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 2 * HOUR)
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 10)  # 60.2 s after the first, and its own time on: 0:01:10 at NOW
    req(f, SCHOOL_Q, "www.nknu.edu.tw", AFF, NOW - 20)
    started(f, None, at=NOW - 2 * HOUR - 47 * MIN - 18)
    snap = numbers(f, running="1")[0]
    text = " ".join(visible(home_html(snap)).split())
    assert "在跑 2:47:18" in text and "c.nknu 主机 2/3" in text and "学校首页等 0/1" in text
    assert progress.fmt_hms(card(snap, C)["busy"]) in text and "0:01:" in progress.fmt_hms(card(snap, C)["busy"])
    for old in ("个网站，完成", "还剩", "预计", "礼貌", "秒）", "分钟"):
        assert old not in text, old


# ---------------------------------------------------------------- the pages (viewer.py, design §1.1–§1.3)

def table_rows(page: str, tid: str) -> list[str]:
    """The rows of one table of a page (its header row aside), as the text a person sees."""
    body = page.split(f"<table id='{tid}'", 1)[1].split("</table>", 1)[0]
    return [" ".join(visible("<tr" + r).split()) for r in body.split("<tr")[2:]]


def small_cards(page: str) -> list[str]:
    cards = page.split("<div class='card small'")[1:]
    return [" ".join(visible("<div" + x.split("</div>", 1)[0]).split()) for x in cards]


def test_a_card_links_its_servers_page_and_log_and_a_small_card_says_its_state_and_done_of_known_only(troubled):
    f, _ids, _root, _key = troubled
    add_url(f, url="https://www.nknu.edu.tw/a.pdf", host="www.nknu.edu.tw", kind="file", kind0="file", page_type=None,
            sniff=None, state="pending", status=None)
    req(f, SCHOOL_Q, "www.nknu.edu.tw", ACA, NOW - 20)
    snap, _slow = numbers(f, slow=True)
    page = home_html(snap)
    key = quote(C, safe="")
    assert f"href='/progress/server/{key}'" in page and f"href='/progress/log?server={key}'" in page
    assert re.search(r"<a class='c-red' href='/progress/log' title='失败 2 · 要人看 4'>错 6</a>", page)
    assert small_cards(page) == ["学校首页等 0/1 没在跑"]  # no percent, no 错, no time on its face …
    hover = page.split("<div class='card small'")[1].split(">", 1)[0]
    assert f"用时 {progress.fmt_hms(card(snap, 'school')['busy'])}" in hover  # … but in its hover text
    text = " ".join(visible(page).split())
    assert "上次更新" not in text and "看不到最新资料" not in text


def test_a_servers_page_lists_one_row_a_site_working_then_queued_then_done(lab):
    f, _ids, _root = lab
    add_url(f, url="https://c.nknu.edu.tw/aca/todo.aspx", state="pending", status=None, sniff=None)
    f.x("UPDATE url SET state='pending', status=NULL WHERE owner_site=?", (BM,))
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 30)
    started(f, None, at=NOW - HOUR)  # the run that holds the lock (pid 1)
    snap = numbers(f, running="1")[0]
    page = server_html(snap, C)
    head = " ".join(visible(page.split("<table", 1)[0]).split())
    assert head.startswith("1/3 在抓") and "用时 0:00:" in head
    rows = table_rows(page, "sites")
    assert [r.split()[0] for r in rows] == ["教务处", "测试系", "总务处"]  # working, queued, done
    assert "50%" in rows[0] and re.search(r"\b0:00:\d\d\b", rows[0])
    assert "排队" in rows[1] and "%" not in rows[1]  # never 0%
    assert "100%" in rows[2]  # done: a black full bar and its percent (the owner 11-04: 爬完再用黑色表示)
    html = {name: next(x for x in page.split("<tr>") if name in x) for name in ("教务处", "总务处")}
    assert "class='pbar'" in html["教务处"] and "pbar done" not in html["教务处"]  # blue while there is work left
    assert "class='pbar done'" in html["总务处"]
    assert f"href='/progress/site?k={quote(ACA, safe='')}'" in page


@pytest.fixture
def helpers(lab):
    """The school's home page and the 学务处 host (two servers of 学校首页等): www holds a file of 教务处 still waiting and
    one of 总务处 done, and has not connected CONN_GIVE_UP times in a row; osa holds one file of 教务处, done. The run
    holds the lock (pid 1)."""
    f, ids, root = lab
    f.set_host("osa.nknu.edu.tw", queue="ip:140.127.40.135")
    add_url(f, url="https://www.nknu.edu.tw/a.pdf", host="www.nknu.edu.tw", kind="file", kind0="file", page_type=None,
            sniff=None, state="pending", status=None)
    add_url(f, url="https://www.nknu.edu.tw/b.pdf", host="www.nknu.edu.tw", site=AFF, owner_site=AFF, kind="file",
            kind0="file", page_type=None, sniff="pdf")
    add_url(f, url="https://osa.nknu.edu.tw/c.pdf", host="osa.nknu.edu.tw", kind="file", kind0="file", page_type=None,
            sniff="pdf")
    f.x("INSERT INTO server(queue, conn_fail, err_fail) VALUES (?, ?, 0)", (SCHOOL_Q, crawler.CONN_GIVE_UP))
    req(f, SCHOOL_Q, "www.nknu.edu.tw", ACA, NOW - 3 * HOUR, took=40.0)
    started(f, None, at=NOW - 4 * HOUR)
    return f, ids, root


def test_a_small_card_links_its_page(helpers):
    """The owner 11-04: 「为什么这三个卡片我点不进去详细看」."""
    f, _ids, _root = helpers
    page = home_html(numbers(f, running="1")[0])
    card_html = page.split("<div class='card small'")[1].split("</div>", 1)[0]
    assert "href='/progress/server/school'" in card_html


def test_a_small_cards_page_lists_one_row_a_server_red_first_done_last(helpers):
    f, _ids, _root = helpers
    snap = numbers(f, running="1")[0]
    page = server_html(snap, "school")
    head = " ".join(visible(page.split("<table", 1)[0]).split())
    assert head.startswith("2/3") and "连不上" in head and "66%" in head and "用时 0:" in head  # rounded down
    rows = table_rows(page, "hosts")
    assert [r.split()[0] for r in rows] == ["www.nknu.edu.tw", "osa.nknu.edu.tw"]  # the red one first, done last
    assert "连不上" in rows[0] and "50%" in rows[0]
    assert re.search(r"\b0:01:40\b", rows[0])  # its 40 s and, the run going on, at most SEGMENT_EXTRA_S after it
    assert "完成" in rows[1] and "100%" in rows[1]
    table = page.split("<table id='hosts'", 1)[1]  # the card's line names its hosts too, in its hover text
    html = {h: next(x for x in table.split("<tr>") if h in x) for h in ("www.nknu.edu.tw", "osa.nknu.edu.tw")}
    assert "class='pbar'" in html["www.nknu.edu.tw"] and "class='pbar done'" in html["osa.nknu.edu.tw"]
    assert rows[0].endswith(" 2") and "教务处" in html["www.nknu.edu.tw"] and "总务处" in html["www.nknu.edu.tw"]
    assert rows[1].endswith(" 1")  # the sites whose rows it holds: their number, their names in the hover text


def test_a_small_cards_server_waits_with_the_run_and_is_done_once_it_holds_nothing_left(helpers):
    f, _ids, _root = helpers
    ended(f, NOW - MIN, "stop")
    snap = numbers(f)[0]
    states = {r["hosts"][0]: r["state"]["words"] for r in card(snap, "school")["rows"]}
    assert states == {"www.nknu.edu.tw": "没在跑", "osa.nknu.edu.tw": "完成"}


def test_a_small_cards_names_that_do_not_resolve_say_so(helpers):
    f, _ids, _root = helpers
    f.set_host("gone.example.org", queue="dns-fail")
    add_url(f, url="https://gone.example.org/d.pdf", host="gone.example.org", kind="file", kind0="file",
            page_type=None, sniff=None, state="pending", status=None)
    snap = numbers(f, running="1")[0]
    got = card(snap, "offsite")["rows"]
    assert [(r["hosts"], r["state"]["words"]) for r in got] == [(["gone.example.org"], "查不到地址")]  # not 等查地址
    assert card(snap, "offsite")["state"]["words"] == "查不到地址"  # the card's face says the same
    assert "查不到地址的主机名：gone.example.org" in server_html(snap, "offsite")


def dead_file(f: Frontier, host: str) -> int:
    f.set_host(host, queue="dns-fail")
    return add_url(f, url=f"https://{host}/d.pdf", host=host, kind="file", kind0="file", page_type=None, sniff=None,
                   state="pending", status=None)


def test_names_that_do_not_resolve_count_on_the_small_card_of_their_own_kind(lab):
    """Review 11-04: every name that does not resolve is on the crawler's one dns-fail queue, which went whole to the
    small card any one of its names picked (a school name, even one holding no row of the run): an off-campus name was
    shown under 学校首页等. Each now counts on its own kind's."""
    f, _ids, _root = lab
    dead_file(f, "dead.nknu.edu.tw")
    dead_file(f, "gone.example.org")
    f.set_host("old.nknu.edu.tw", queue="dns-fail")  # no row of the run (an older crawl's)
    started(f, None, at=NOW - HOUR)
    snap = numbers(f, running="1")[0]
    dead = {c["key"]: [(r["hosts"], r["state"]["words"]) for r in c["rows"]] for c in snap["small"]}
    assert dead == {"school": [(["dead.nknu.edu.tw"], "查不到地址")], "offsite": [(["gone.example.org"], "查不到地址")]}
    assert card(snap, "offsite")["state"]["words"] == card(snap, "school")["state"]["words"] == "查不到地址"


def test_a_big_card_whose_last_rows_are_on_a_name_that_does_not_resolve_waits_there(lab):
    """Review 11-04: the big card said 等查地址 (its names not looked up yet) while the small card holding the same rows
    said 查不到地址: a big card whose own server has run dry waits on the card that holds the rest (design §2.5 rule 7),
    and says why in its hover text."""
    f, _ids, _root = lab
    dead_file(f, "gone.example.org")
    started(f, None, at=NOW - HOUR)
    snap = numbers(f, running="1")[0]
    big = card(snap, C)["state"]
    assert big["words"] == "等校外网站" and "（那边：查不到地址）" in big["tip"]
    assert card(snap, "offsite")["state"]["words"] == "查不到地址"


def test_a_small_cards_time_is_the_sum_of_its_servers_rows(helpers):
    """Review 11-04: a server whose rows of the run all ended outside the count (skipped: too large) has no row of its
    own, so the card leaves its time out too (design §2.1: a card is the sum of what it shows)."""
    f, _ids, _root = helpers
    f.set_host("vod.nknu.edu.tw", queue="ip:140.127.40.200")
    add_url(f, url="https://vod.nknu.edu.tw/big.mp4", host="vod.nknu.edu.tw", kind="file", kind0="file",
            page_type=None, sniff=None, state="skipped", reason="too_large", status=200)
    req(f, "ip:140.127.40.200", "vod.nknu.edu.tw", ACA, NOW - 2 * HOUR, took=30.0)
    c = card(numbers(f, running="1")[0], "school")
    assert [r["queue"] for r in c["rows"]] == [SCHOOL_Q, "ip:140.127.40.135"]
    assert c["busy"] == sum(r["busy"] for r in c["rows"]) == 100.0


def test_a_server_is_stuck_only_once_its_work_has_waited_past_stall_s(lab):
    """Review 11-04: a server found late in the run, or one whose rest has just ended, showed ✗ 卡住 until its first
    request (quiet was counted from its last request or the run's start): quiet counts from when its work became due
    (the row's due time or when it was found, its host's cool-down, the end of its queue's rest), never before."""
    f, _ids, _root = lab
    started(f, None, at=NOW - 2 * HOUR)
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 20)  # the run is fetching
    f.set_host("new.example.com", queue="ip:198.51.100.7")  # found 5 s ago, its worker not started yet
    add_url(f, url="https://new.example.com/a.pdf", host="new.example.com", kind="file", kind0="file",
            page_type=None, sniff=None, state="pending", status=None, added_at=NOW - 5)
    f.set_host("old.example.com", queue="ip:198.51.100.8")  # its file has waited 20 minutes with nothing asked: stuck
    add_url(f, url="https://old.example.com/a.pdf", host="old.example.com", kind="file", kind0="file",
            page_type=None, sniff=None, state="pending", status=None, added_at=NOW - 20 * MIN)
    req(f, GQ, google_api.API_HOST, ACA, NOW - 61 * MIN)  # Google: blocked, rested until 2 s ago
    add_url(f, url=f"https://{google_api.API_HOST}/drive/v3/files/x?alt=media", host=google_api.API_HOST,
            kind="file", kind0="file", page_type=None, sniff=None, state="pending", status=None, next_at=NOW - HOUR)
    f.pause(GQ, NOW - 2, "automated_queries")
    snap = numbers(f, running="1")[0]
    rows = {r["hosts"][0]: r["state"] for c in snap["small"] for r in c["rows"]}
    assert rows["new.example.com"]["words"] == "在抓" and rows[google_api.API_HOST]["words"] == "在抓"
    assert rows["old.example.com"]["words"] == "卡住" and "这次还没向它发过请求" in rows["old.example.com"]["tip"]


def test_bars_are_blue_while_work_is_left_and_black_once_done():
    """The owner 11-04: 「看板的进度条换成蓝色的，爬完再用黑色表示」."""
    from nknu.archive.viewer import PROGRESS_CSS, _bar

    assert ".pbar span{display:block;height:10px;background:#2563eb" in PROGRESS_CSS
    assert ".pbar.done span{background:#111}" in PROGRESS_CSS
    assert _bar(0.5).startswith("<span class='pbar'>") and "width:50.0%" in _bar(0.5)
    assert _bar(1.0, True).startswith("<span class='pbar done'>")


def test_a_servers_page_gives_each_sites_errors_with_a_link_to_its_lines(troubled):
    f, _ids, _root, _key = troubled
    snap, _slow = numbers(f, slow=True)
    page = server_html(snap, C)
    rows = {r.split()[0]: r for r in table_rows(page, "sites")}
    assert rows["教务处"].endswith(" 3") and rows["总务处"].endswith(" 3") and not rows["测试系"].endswith(" 3")
    assert f"href='/progress/log?site={quote(ACA, safe='')}'" in page
    assert "错 6" in " ".join(visible(page.split("<table", 1)[0]).split())


def test_the_shared_card_names_each_rows_host_and_an_unknown_card_is_said_so(lab):
    f, _ids, _root = lab
    plan(f, ARTS, "艺术", "本系重要")
    f.x("UPDATE site_plan SET home='https://arts.nknu.edu.tw/' WHERE site=?", (ARTS,))
    f.set_host("arts.nknu.edu.tw", queue="ip:140.127.9.9")
    add_url(f, url="https://arts.nknu.edu.tw/", host="arts.nknu.edu.tw", site=ARTS, owner_site=ARTS, tier=1)
    snap = numbers(f)[0]
    rows = table_rows(server_html(snap, progress.UNITS), "sites")
    assert [r.split()[:2] for r in rows] == [["某中心", "sso.nknu.edu.tw"], ["艺术", "arts.nknu.edu.tw"]]
    assert "没有这张卡片" in visible(server_html(snap, "nowhere"))


def test_the_log_page_says_the_count_the_kinds_that_filter_and_the_lines_newest_first(troubled):
    f, ids, _root, key = troubled
    event(f, NOW - 600, "warn", "server_rest", "c.nknu.edu.tw: http_503: server queue rests 15 min",
          host="c.nknu.edu.tw")
    snap, slow = numbers(f, slow=True)
    view = progress.log_view(snap, slow)
    page = log_html(view)
    text = " ".join(visible(page).split())
    assert "6 条：失败 2 · 要人看 4" in text and "/progress/log.txt'" in page
    for c in view["codes"]:  # each kind with its count (its words up to a bracket), a link to only that kind
        short = re.split("[（；]", c["label"], maxsplit=1)[0]
        assert f"href='/progress/log?code={c['code']}'" in page and f"{short} {c['n']}" in text
    assert len(table_rows(page, "lines")) == 6
    lines_table = page.split("<table id='lines'", 1)[1].split("</table>")[0]
    refs = re.findall(r"<span class='ref'>(?:<a [^>]*>)?([^<]+)", lines_table)
    assert refs == [r["ref"] for r in view["rows"]]
    assert [r["at"] for r in view["rows"]] == sorted((r["at"] for r in view["rows"]), reverse=True)  # newest first
    assert f"href='/page?id={ids['refused']}'" in page  # a line's number opens the archived copy
    assert "王O明" not in page and "公告" not in page and key not in page and "gkey=abc" not in page
    assert "服务器事件 1" in text and progress.PLAIN["server_rest"] in text and "礼貌违规 0" in text


def test_the_log_page_of_a_site_a_server_or_a_kind(troubled):
    f, _ids, _root, _key = troubled
    event(f, NOW - 600, "warn", "server_rest", "c.nknu.edu.tw: http_503", host="c.nknu.edu.tw")
    snap, slow = numbers(f, slow=True)
    aff = log_html(progress.log_view(snap, slow, site=AFF))
    assert "3 条：失败 1 · 要人看 2" in " ".join(visible(aff).split()) and len(table_rows(aff, "lines")) == 3
    assert "服务器事件" not in visible(aff) and "礼貌" not in visible(aff)  # never in a site's view
    assert f"href='/progress/log?site={quote(AFF, safe='')}&amp;code=looks_gone'" in aff
    assert f"/progress/log.txt?site={quote(AFF, safe='')}'" in aff
    server = log_html(progress.log_view(snap, slow, card=C))
    assert len(table_rows(server, "lines")) == 6 and "服务器事件 1" in visible(server)
    looks = log_html(progress.log_view(snap, slow, code="looks_gone"))
    assert len(table_rows(looks, "lines")) == 1 and "6 条" in visible(looks)  # the count stays the whole's
    assert "href='/progress/log'>全部</a>" in looks


def test_a_long_log_page_lists_the_newest_lines_and_the_file_keeps_them_all(troubled, monkeypatch):
    f, _ids, _root, _key = troubled
    snap, slow = numbers(f, slow=True)
    view = progress.log_view(snap, slow)
    monkeypatch.setattr(viewer, "LOG_LINES", 2)  # the rehearsal's whole log: 26,492 lines, a 10 MB page
    page = log_html(view)
    text = " ".join(visible(page).split())
    assert len(table_rows(page, "lines")) == 2 and "6 条：" in text and "只列最新 2 条" in text
    assert len([x for x in progress.log_text(view, f.path).splitlines() if not x.startswith("#")]) == 6
    monkeypatch.setattr(viewer, "LOG_LINES", 3)
    assert "只列" not in visible(log_html(progress.log_view(snap, slow, site=AFF)))  # 3 lines, 3 listed: all of them


def test_the_log_page_before_the_errors_are_worked_out_says_why(lab):
    f, _ids, _root = lab
    snap = numbers(f)[0]
    page = log_html(progress.log_view(snap, None))
    assert "错的条数还在算" in visible(page) and "<table id='lines'" not in page


@pytest.fixture
def served(troubled):
    """The viewer over the troubled archive on this machine's 127.0.0.1 (a port the system picks), its numbers
    worked out once."""
    _f, _ids, root, _key = troubled
    v = Viewer(root)
    v._dash = progress.Dashboard(root, clock=lambda: NOW, background=False, cache_s=0)
    v._dash.refresh_slow()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(v))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], v._dash
    finally:
        server.shutdown()
        server.server_close()
        v._dash.close()


def fetch(port: int, path: str, host: str | None = None) -> tuple[int, dict, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""


def test_every_dashboard_route_answers_under_this_machines_names_only(served):
    port, _d = served
    key = quote(C, safe="")
    for path in ("/progress", "/progress?part=main", f"/progress/server/{key}", f"/progress/server?card={key}",
                 f"/progress/server/{key}?part=main", "/progress/server/nowhere", "/progress/log",
                 f"/progress/log?site={quote(AFF, safe='')}", f"/progress/log?server={key}&code=looks_gone",
                 "/progress/log.txt", f"/progress/site?k={quote(ACA, safe='')}"):
        status, head, _body = fetch(port, path)
        assert status == 200 and head["Cache-Control"] == "no-store", path
        assert fetch(port, path, host="evil.example")[0] == 403, path
        assert fetch(port, path, host=f"localhost:{port}")[0] == 200, path
    _s, _h, part = fetch(port, f"/progress/server/{key}?part=main")
    assert part.startswith("<main id='pmain'>") and "教务处" in part and "<nav>" not in part
    assert fetch(port, f"/progress/server?card={key}")[2] == fetch(port, f"/progress/server/{key}")[2]


def test_the_log_file_downloads_with_its_name_and_the_pages_lines(served):
    port, d = served
    status, head, body = fetch(port, f"/progress/log.txt?site={quote(AFF, safe='')}")
    assert status == 200 and head["Content-Type"] == "text/plain; charset=utf-8"
    assert head["Content-Disposition"] == "attachment; filename=problems-20261026-1600.log"  # 16:00 in Taipei
    assert body == d.log_file(site=AFF) and len([x for x in body.splitlines() if not x.startswith("#")]) == 3
    whole = fetch(port, "/progress/log.txt?server=" + quote(C, safe=""))[2]
    assert whole == d.log_file(card=C)


def test_the_home_and_server_pages_ask_every_ten_seconds_and_grey_out_after_two_minutes(served):
    port, _d = served
    for path in ("/progress", f"/progress/server/{quote(C, safe='')}"):
        page = fetch(port, path)[2]
        assert "setInterval(tick, 10000)" in page and "> 120000" in page and "searchParams.set('part', 'main')" in page
        assert "<p id='pstale' class='stale-note' hidden>看不到最新资料</p>" in page
    assert "setInterval" not in fetch(port, "/progress/log")[2]  # the log does not poll


def test_serve_and_archive_browse_listen_where_they_are_told(tmp_path, monkeypatch):
    bound = []

    class Server:
        def __init__(self, address, _handler):
            bound.append(address)

    monkeypatch.setattr(viewer, "ThreadingHTTPServer", Server)
    viewer.serve(tmp_path, 8877)
    viewer.serve(tmp_path, 8877, host="0.0.0.0")
    assert bound == [("127.0.0.1", 8877), ("0.0.0.0", 8877)]
    Frontier(tmp_path / "frontier.db").close()
    calls = []

    class Running:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls.append("closed")

    def fake_serve(root, port, host="127.0.0.1"):
        calls.append((root, port, host))
        return Running()

    monkeypatch.setattr(viewer, "serve", fake_serve)
    monkeypatch.setattr(viewer, "index_in_background", lambda root, done=None: None)
    runner = CliRunner()
    got = runner.invoke(app, ["archive", "browse", "--dir", str(tmp_path), "--host", "0.0.0.0", "--port", "18899"])
    assert got.exit_code == 0, got.output
    assert calls == [(tmp_path.resolve(), 18899, "0.0.0.0"), "closed"]
    assert runner.invoke(app, ["archive", "browse", "--dir", str(tmp_path)]).exit_code == 0
    assert calls[2] == (tmp_path.resolve(), 8877, "127.0.0.1")


def test_the_viewers_dashboard_freezes_the_per_run_log_and_tests_do_not(lab):
    _f, _ids, root = lab
    d = Viewer(root).dashboard()
    try:
        assert d.freeze_reports and not progress.Dashboard(root, background=False).freeze_reports
    finally:
        d.close()


# ---------------------------------------------------------------- what the review of 11-04 found

def todo(f: Frontier, site: str = ACA, host: str = "c.nknu.edu.tw") -> int:
    return add_url(f, url=f"https://{host}/{site.rsplit('/', 1)[-1]}/todo{time.monotonic_ns()}.aspx", host=host,
                   site=site, owner_site=site, state="pending", status=None, sniff=None)


def test_run_lock_held_by_another_program_is_never_a_running_crawl(lab):
    """jscheck, enqueue, rejudge, seed and retry hold run.lock as a run does (crawler.hold_run_lock): the top line
    keeps why the last run ended and says another program runs; no card says 在抓, 没动静 or 卡住."""
    f, _ids, root = lab
    todo(f)
    todo(f, UNIT, "sso.nknu.edu.tw")
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 2 * HOUR)  # the crawl's last request, long ago
    started(f, None, at=NOW - 3 * HOUR, pid=4242)
    ended(f, NOW - HOUR, "finished")
    held = crawler.hold_run_lock(root)  # as jscheck does: this process's pid in run.lock
    try:
        d = progress.Dashboard(root, clock=lambda: NOW, background=False, cache_s=0)
        try:
            snap = d.main()
        finally:
            d.close()
    finally:
        held.close()
    run = snap["run"]
    assert run["running"] is None and run["other"] == str(os.getpid()) and run["words"] == "抓完了"
    assert run["tip"].startswith(f"run.lock 被 pid {os.getpid()} 拿着，不是爬虫的 run")
    states = {c["key"]: c["state"]["words"] for c in snap["cards"] + snap["small"]}
    assert states[C] == states[progress.UNITS] == "没在跑" and not {"在抓", "没动静", "卡住"} & set(states.values())
    assert " ".join(visible(home_html(snap)).split()).startswith("抓完了 另有程序在跑 ")
    assert progress._frozen_name(snap) is not None  # the run has ended: its copy of the log is written


def test_a_run_before_its_run_start_and_one_letting_go_are_not_running(lab):
    f, _ids, _root = lab
    todo(f)
    req(f, C, "c.nknu.edu.tw", ACA, NOW - 40 * MIN)
    started(f, None, at=NOW - 2 * HOUR, pid=7)
    ended(f, NOW - 31 * MIN, "waiting", next_due=NOW - MIN)
    snap = numbers(f, running="9")[0]  # run_loop's next run holds the lock, its run_start not written yet
    assert snap["run"]["words"] == f"等重试 {progress._hm(NOW - MIN, NOW)} 再开" and snap["run"]["other"] == "9"
    assert card(snap, C)["state"]["words"] == "没在跑"  # never 卡住 by the last run's old request
    snap = numbers(f, running="7")[0]  # the run that ended, letting go of the lock as it exits
    assert snap["run"]["running"] is None and snap["run"]["other"] is None
    assert card(snap, C)["state"]["words"] == "没在跑" and "另有程序在跑" not in visible(home_html(snap))
    started(f, None, at=NOW - 10, pid=9)
    snap = numbers(f, running="9")[0]
    assert snap["run"]["words"] == "在跑 0:00:10" and card(snap, C)["state"]["words"] == "在抓"


def test_a_wait_whose_restart_never_came_says_so(lab):
    f, _ids, _root = lab
    started(f, None, at=NOW - 10 * HOUR)
    ended(f, NOW - 9 * HOUR, "waiting", next_due=NOW - 8.5 * HOUR, pending=120)
    snap = numbers(f)[0]
    run = snap["run"]
    assert (run["words"], run["color"]) == ("没重开", "red") and "接着跑的小脚本停了" in run["tip"]
    assert "还有 120 个网址在等" in run["tip"] and "✗ 没重开" in visible(home_html(snap))
    held = numbers(f, running="31")[0]["run"]  # another program holds the lock: the loop waits for it
    assert (held["words"], held["color"]) == ("没重开", "yellow") and "被别的程序占着" in held["tip"]
    ended(f, NOW - 20 * MIN, "waiting", next_due=NOW - 5 * MIN)  # due five minutes ago: the loop is starting it
    assert numbers(f)[0]["run"]["words"] == f"等重试 {progress._hm(NOW - 5 * MIN, NOW)} 再开"


def test_the_site_page_says_not_finished_while_no_run_of_it_goes(lab):
    f, _ids, root = lab
    todo(f)
    todo(f, BM)

    def site_status(conn, now, pages=None):
        return {s: {"state": "crawling", "why": ["1 个网址还在排队"]} for s in (ACA, BM)}

    started(f, [ACA, AFF], at=NOW - 2 * HOUR, pid=os.getpid())
    ended(f, NOW - HOUR, "finished", sites=[ACA, AFF])
    d = progress.Dashboard(root, clock=lambda: NOW, background=False, cache_s=0, site_status=site_status)
    try:
        d.refresh_slow()
        idle = d.site(ACA)
        started(f, [ACA, AFF], at=NOW - 10 * MIN, pid=os.getpid())
        held = crawler.hold_run_lock(root)  # the run of ACA and AFF goes
        try:
            going, left_out = d.site(ACA), d.site(BM)
        finally:
            held.close()
    finally:
        d.close()
    # the state stays the monitor's (crawling: rows wait); its words say whether a run of the site goes
    assert idle["state"] == "crawling" and idle["shown"] == progress.UNFINISHED_SHOW
    assert idle["why"][:2] == ["没在跑：抓完了", "1 个网址还在排队"]
    page = progress_site_html(idle)
    assert visible(page.split("<p class='big'>", 1)[1].split("</p>", 1)[0]).strip() == "没抓完"
    assert "还在抓" not in visible(page)
    assert going["shown"] == progress.STATE_SHOW["crawling"] and "还在抓" in visible(progress_site_html(going))
    assert left_out["shown"] == progress.UNFINISHED_SHOW and left_out["why"][0].startswith("这次 run 没有这个网站")


def test_a_card_of_several_servers_takes_the_worst_red_before_yellow():
    a, b = "ip:140.127.9.1", "ip:140.127.9.2"
    c = progress.Card(progress.UNITS, progress.UNITS_NAME, False, (a, b), ("x.nknu.edu.tw", "y.nknu.edu.tw"), "")
    work = {q: _work(hosts=Counter({h: 5}), by_site=Counter({h: 5}))
            for q, h in ((a, "x.nknu.edu.tw"), (b, "y.nknu.edu.tw"))}
    s = progress.card_state(c, {}, work, _env(servers={a: (1, 0), b: (8, 0)}), NOW)
    assert (s["words"], s["color"]) == ("连不上", "red") and "连续 8 次" in s["tip"]
    rest = {a: (NOW + 600, "x.nknu.edu.tw: http_503")}
    s = progress.card_state(c, {}, work, _env(pauses=rest, last_started={b: NOW - 40 * MIN}), NOW)
    assert (s["words"], s["color"]) == ("卡住", "red")
    assert progress.card_state(c, {}, work, _env(pauses=rest, last_started={b: NOW - 10}), NOW)["code"] == "rest"


def test_working_needs_a_request_in_the_last_two_minutes_then_quiet_then_stuck():
    got = [state(last_started={C: NOW - m * MIN}) for m in (1, 3, 14, 16)]
    assert [s["words"] for s in got] == ["在抓", "没动静", "没动静", "卡住"]
    assert [s["color"] for s in got] == ["green", "yellow", "yellow", "red"] and "0:03:00 前" in got[1]["tip"]
    assert state(last_started={C: NOW - 4 * MIN}, delay={C: 180.0})["words"] == "在抓"  # 1 minute past its Crawl-delay


def test_an_entrys_ref_is_masked_as_its_address_is(lab):
    f, _ids, _root = lab
    key = "AIza" + "y" * 35
    res = f"google:https://docs.google.com/document/d/x/edit?usp=sharing&resourcekey=0-SECRET&key={key}"
    conn = progress.connect(f.path)
    try:
        snap = progress.snapshot(conn, NOW)
        st = {ACA: {"issues": [Issue("review", "entry", "k" + res, res, "unhandled", "x", "unhandled:unknown")],
                    "jscheck_done": True}}
        found = progress.problems(conn, st, [], scope=snap["scope"], on_lab=False)
    finally:
        conn.close()
    assert found[0].ref == "kgoogle:https://docs.google.com/document/d/x/edit?usp=sharing&resourcekey=…&key=…"
    slow = {"at": NOW, "problems": found, "scope_sites": snap["scope"].sites, "events": []}
    view = progress.log_view(snap, slow)
    for text in (progress.log_text(view, f.path), log_html(view)):
        assert "SECRET" not in text and key not in text and "resourcekey=…" in text


def test_a_row_the_pages_of_two_sites_use_is_one_line_of_the_site_it_belongs_to(lab):
    f, _ids, _root = lab
    started(f, [ACA, AFF], at=NOW - 2 * HOUR)
    ended(f, NOW - HOUR, "finished", sites=[ACA, AFF])
    pages = [add_url(f, url="https://c.nknu.edu.tw/aca/a.aspx"),
             add_url(f, url="https://c.nknu.edu.tw/affair/b.aspx", site=AFF, owner_site=AFF)]
    shared = {}
    for owner in (AFF, BM):  # 总务处's own file; 测试系's, a site this run does not name
        shared[owner] = add_url(f, url=f"https://c.nknu.edu.tw/{owner.rsplit('/', 1)[-1]}/shared.pdf", site=owner,
                                owner_site=owner, kind="file", kind0="file", page_type=None, sniff=None,
                                state="failed", reason="connect", status=None, attempts=3)
    for page in pages:
        f.add_page_res(page, [{"res_key": f"file:{n}", "how": "get", "role": "file", "need": "must", "res_id": rid}
                              for n, rid in enumerate(shared.values())])
    snap, slow = numbers(f, slow=True)
    listed = {i.ref for s in (ACA, AFF) for i in slow["statuses"][s].issues}
    assert {f"u{x}" for x in shared.values()} <= listed  # the monitor lists each under both sites
    lines = progress.log_view(snap, slow)["rows"]
    where = Counter((r["ref"], r["site"]) for r in lines if r["ref"] in {f"u{x}" for x in shared.values()})
    assert where == Counter({(f"u{shared[AFF]}", AFF): 1, (f"u{shared[BM]}", ACA): 1})  # its owner's, else the first
    assert snap["errors"]["n"] == len(lines) == sum(r["errors"]["n"] for c in snap["cards"] for r in c["rows"])


def test_a_sites_bracketed_note_and_the_logs_read_time_go_to_the_hover_text(troubled):
    f, _ids, _root, _key = troubled
    f.x("UPDATE site_plan SET name='总务处（SSO，cLv2=000001）' WHERE site=?", (AFF,))
    snap, slow = numbers(f, slow=True)
    page = server_html(snap, C)
    rows = table_rows(page, "sites")
    assert "总务处" in [r.split()[0] for r in rows] and "cLv2" not in " ".join(rows)
    assert f"title='总务处（SSO，cLv2=000001） · {AFF}'" in page
    log = log_html(progress.log_view(snap, slow))
    text = " ".join(visible(log).split())
    assert text.startswith("6 条：失败 2 · 要人看 4 下载") and "cLv2" not in text and "资料 " not in text
    assert "错的条数每 2 分钟在背景更新一次；这份是 " in log
