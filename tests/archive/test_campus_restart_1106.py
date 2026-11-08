"""The lab's restarts on the 11-06 code (the deploy check 11-06, 【Claude 的执行办法】): the ledger the 11-04 code
wrote holds no campus:last, and in Docker the crawler's address (local_address: the container's own) is new at each
start, so a restart there starts unknown. Until SSO's check tells where this machine is, a long-dead school server's
next failure hands nothing to the lab machine (_lab_may_reach: its rows wait, B2); once the check found off campus it
does, and a crawler with no campus check (rejudge's offline one, a test's) keeps the 11-04 hand-over."""

from nknu.archive import crawler as mod
from tests.archive.test_archive import Sink, seed, snap
from tests.archive.test_campus_keep_1106 import ADDR, ON
from tests.archive.test_crawler_round1 import HOME, QC, QS, crawl, events

OFF = {"checkcampusip": snap("x", 200, html="<p>您的IP 不在校內網路</p>")}


def _crawler(tmp_path, routes=ON, addr=ADDR):
    return crawl(tmp_path, routes, campus_url=mod.CAMPUS_URL, local_address=lambda: addr)


def _states(crawler):
    return {r["state"] for r in crawler.frontier.q("SELECT state FROM url WHERE host='c.nknu.edu.tw'")}


def test_a_ledger_with_no_campus_last_hands_no_dead_server_to_the_lab_before_the_first_check(tmp_path):
    crawler, _net, _clock = _crawler(tmp_path)  # the 11-04 code never wrote campus:last
    assert crawler.frontier.meta(mod.CAMPUS_KEY) is None and crawler.campus == "unknown"
    seed(crawler, HOME)
    crawler._server_down(QC, mod.CONN_GIVE_UP + 100)  # down for days: its next failure, before SSO's check came back
    assert _states(crawler) == {"pending"} and not events(crawler, "host_unreachable")


def test_a_restart_in_a_container_with_another_address_hands_none_over_before_the_first_check(tmp_path):
    crawler, net, _clock = _crawler(tmp_path)
    assert crawler._campus_step(QS, net, Sink(tmp_path)) and crawler.campus == "on"
    again, _net, _clock = _crawler(tmp_path, addr="172.18.0.5")  # docker gave the new container another address
    assert again.campus == "unknown"
    seed(again, HOME)
    again._server_down(QC, mod.CONN_GIVE_UP + 100)
    assert _states(again) == {"pending"} and not events(again, "host_unreachable")


def test_once_the_check_found_off_campus_the_hand_over_comes_back(tmp_path):
    crawler, net, _clock = _crawler(tmp_path, routes=OFF)
    assert crawler._campus_step(QS, net, Sink(tmp_path)) and crawler.campus == "off"
    seed(crawler, HOME)
    crawler._server_down(QC, mod.CONN_GIVE_UP + 100)
    assert {(r["state"], r["reason"]) for r in crawler.frontier.q("SELECT state, reason FROM url")} == \
        {("failed", "host_unreachable")}


def test_an_address_change_holds_the_hand_over_until_the_next_check(tmp_path):
    addr = {"now": ADDR}
    crawler, net, _clock = crawl(tmp_path, OFF, campus_url=mod.CAMPUS_URL, local_address=lambda: addr["now"])
    assert crawler._campus_step(QS, net, Sink(tmp_path)) and crawler.campus == "off"
    addr["now"] = "10.9.8.7"
    assert crawler._campus_due() and crawler.campus == "unknown"
    seed(crawler, HOME)
    crawler._server_down(QC, mod.CONN_GIVE_UP + 100)
    assert _states(crawler) == {"pending"}


def test_a_crawler_with_no_campus_check_keeps_the_1104_hand_over(tmp_path):
    crawler, _net, _clock = crawl(tmp_path, ON)  # campus_url None: rejudge's offline crawler, the tests' default
    seed(crawler, HOME)
    crawler._server_down(QC, mod.CONN_GIVE_UP + 100)
    assert _states(crawler) == {"failed"}
