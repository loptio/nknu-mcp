"""The restart of 11-06 (the owner pressed Claude's button 「先修三件再一起重开」), on one ledger shaped like the lab's,
with FIX 1 (the site cap), FIX 2 (this machine), FIX 3 (tables of content), A1 (no hand-over to the lab before the
campus check answered) and A2 (no run for an empty --sites): what the first run of the new code does at once."""

from __future__ import annotations

import json
import time

import pytest

from nknu.archive import crawler as mod
from nknu.archive import scope
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import snap
from tests.archive.test_campus_keep_1106 import ADDR
from tests.archive.test_crawler_round1 import QS, conn_err, crawl, events, page, rows
from tests.archive.test_run_only import _known, run
from tests.archive.test_sso_scope_1103 import ACT, CAMP, OTHER

SERVICES = "sso.nknu.edu.tw/services"
JUNK = "https://sso.nknu.edu.tw/services/campusactivities/import/ascx/www.performer.org.tw"
LOCALHOST_FILE = "http://localhost/WebServices/ajaxDownloadFileCourse.aspx?encode=1&path=Zm9vLnBwdHg="
DEAD = "https://dead.nknu.edu.tw/a.aspx"
QD = "ip:140.127.9.9"
QL = "ip:127.0.0.1"
QUEUES = {"sso.nknu.edu.tw": QS, "dead.nknu.edu.tw": QD, "localhost": QL}
ROUTES = {"checkcampusip": snap("x", 302, headers={"location": "https://edocap.nknu.edu.tw/"}),
          "/services/": page("<p>活動</p>"), "dead.nknu.edu.tw": conn_err(), "localhost": page("<p>never</p>")}


@pytest.fixture(autouse=True)
def _workers_leave_when_idle(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_EXIT_S", -1.0)


def _row(f, url, *, host, state, reason=None, kind="page", depth=2, owner=SERVICES, next_at=0.0):
    f.add(url, host=host, site=scope.site_key(url), unit=None, tier=1, kind=kind, depth=depth, found_via="link",
          found_from=None, state=state, reason=reason, next_at=next_at, owner_site=owner, prio=1, kind0=kind)
    return f.row(url)["id"]


def _lab_ledger(tmp_path):
    seeder, _, clock = crawl(tmp_path, {}, plan=OpenPlan(), queues=QUEUES)
    f = seeder.frontier
    for i in range(2):  # services' pages already counted (site_cap 2 below: the site is full)
        _row(f, f"{OTHER}{i}", host="sso.nknu.edu.tw", state="done")
    _row(f, ACT + "h1", host="sso.nknu.edu.tw", state="held", reason="cap:site")
    _row(f, CAMP + "h2", host="sso.nknu.edu.tw", state="held", reason="cap:site")
    _row(f, JUNK, host="sso.nknu.edu.tw", state="held", reason="cap:site")
    _row(f, ACT + "deep", host="sso.nknu.edu.tw", state="held", reason="cap:depth", depth=16)
    # the 11-04 code's localhost attachment: pending on its own queue, the server failing for days
    _row(f, LOCALHOST_FILE, host="localhost", state="pending", reason="conn_pause", kind="file",
         owner="sso.nknu.edu.tw/web/593")
    f.set_host("localhost", queue=QL)
    # a school server down for days, with one row
    _row(f, DEAD, host="dead.nknu.edu.tw", state="pending", owner="dead.nknu.edu.tw")
    f.set_host("dead.nknu.edu.tw", queue=QD)
    for q, n in ((QL, 200), (QD, mod.CONN_GIVE_UP + 50)):
        f.x("INSERT INTO server(queue, conn_fail, failed_at) VALUES (?,?,?)", (q, n, clock() - 3600))
    _known(f, "sso.nknu.edu.tw", "dead.nknu.edu.tw")
    assert f.meta(mod.CAMPUS_KEY) is None  # the 11-04 code never wrote it
    f.close()


def _once_the_campus_check_told(crawler, err, wait_s: float = 20.0):
    """The dead server's answer (err), given only once SSO's campus check has told where this machine is (review of
    11-09): each queue runs on its own thread, and on a busy machine the dead server's queue failed first now and
    then, while campus was still unknown, which fails nothing (Crawler._server_down: unsure); its next try came after
    the test run's end_wait, so the run ended with the row waiting. What the test asks is what that failure does once
    the check answered, whichever thread runs first. A check that never answers lets it fail after wait_s (real
    seconds), and the test says so."""

    def route(_url):
        deadline = time.monotonic() + wait_s
        while crawler.campus == "unknown" and time.monotonic() < deadline:
            time.sleep(0.005)
        raise err

    return route


def test_the_first_run_of_the_new_code_on_a_lab_shaped_ledger(tmp_path):
    _lab_ledger(tmp_path)
    crawler, net, _clock = crawl(tmp_path, ROUTES, plan=OpenPlan(), queues=QUEUES, site_cap=2,
                                 campus_url=mod.CAMPUS_URL, local_address=lambda: ADDR)
    net.routes["dead.nknu.edu.tw"] = _once_the_campus_check_told(crawler, conn_err())
    assert crawler.campus == "unknown"
    run(crawler)
    got = rows(crawler)
    # FIX 1: the two activity pages the site cap held are fetched; the junk link and the deep page stay held
    assert (got[ACT + "h1"]["state"], got[CAMP + "h2"]["state"]) == ("done", "done")
    assert (got[JUNK]["state"], got[JUNK]["reason"]) == ("held", "cap:site")
    assert (got[ACT + "deep"]["state"], got[ACT + "deep"]["reason"]) == ("held", "cap:depth")
    assert events(crawler, "caps_released") == ["2 rows held by a cap are free of it now (cap:site 2)"]
    # the order the lab's scripts must read: caps_released is written before the run's run_start
    order = [r[0] for r in crawler.frontier.q("SELECT code FROM event WHERE code IN ('caps_released', 'run_start') "
                                              "ORDER BY rowid")]
    assert order == ["caps_released", "run_start"]
    # FIX 2: the localhost row is skipped before anything is sent, robots.txt included
    assert (got[LOCALHOST_FILE]["state"], got[LOCALHOST_FILE]["reason"]) == ("skipped", "out:loopback")
    assert not [u for u in net.seen if "localhost" in u]
    assert crawler.frontier.q("SELECT count(*) FROM request WHERE host='localhost'")[0][0] == 0
    res = crawler.frontier.q("SELECT result FROM url_result WHERE url=?", (LOCALHOST_FILE,))
    assert [r[0] for r in res] == ["bad_link"]
    # A1 + 11-06: the dead school server's row is never handed to the lab, loses no attempt, pauses no school; once
    # the check said on campus, its next failure fails it as server_down (the owner 11-08)
    assert (got[DEAD]["state"], got[DEAD]["attempts"], got[DEAD]["reason"]) == ("failed", 0, "server_down")
    assert not events(crawler, "host_unreachable") and not events(crawler, "offline")
    assert json.loads(crawler.frontier.meta(mod.CAMPUS_KEY))["state"] == "on"
