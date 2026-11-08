"""What the campus check found is kept while this machine's address is the same (second review 11-06). On the lab
machine 'unknown' hands every long-dead school server's rows to the lab machine (_lab_may_reach): after any check that
got no answer (the school's network down, SSO's page timing out), and at every run's start until its first check came
back. A check that cannot tell (no answer, an error page, another page) keeps the last finding, and a run on the same
address starts from it (meta campus:last); the check is still asked at once."""

from nknu.archive import crawler as mod
from tests.archive.test_archive import Net, Sink, seed, snap
from tests.archive.test_crawler_round1 import HOME, QC, QS, conn_err, crawl, events

ADDR = "192.168.50.7"
ON = {"checkcampusip": snap("x", 302, headers={"location": "https://edocap.nknu.edu.tw/"})}


def _lab(tmp_path, addr=ADDR):
    return crawl(tmp_path, ON, campus_url=mod.CAMPUS_URL, local_address=lambda: addr)


def test_a_campus_check_that_cannot_tell_keeps_what_the_last_one_found(tmp_path):
    crawler, net, clock = _lab(tmp_path)
    sink = Sink(tmp_path)
    assert crawler._campus_step(QS, net, sink) and crawler.campus == "on"
    for answer in (conn_err(), snap("x", 503), snap("x", 302, headers={"location": "/ErrorPage/ErrorPage.aspx"})):
        clock.t += mod.CAMPUS_EVERY_S + 1
        crawler.frontier.unpause(QS)
        assert crawler._campus_step(QS, Net(clock, {"checkcampusip": answer}), sink)
        assert crawler.campus == "on"  # this machine did not move: it is still on the school's network


def test_a_run_on_the_same_address_starts_from_the_last_finding(tmp_path):
    crawler, net, _clock = _lab(tmp_path)
    assert crawler._campus_step(QS, net, Sink(tmp_path)) and crawler.campus == "on"
    again, _net, _clock = _lab(tmp_path)  # the crawler restarted on the lab machine
    assert again.campus == "on" and again._campus_due()  # and it still asks at once
    moved, _net, _clock = _lab(tmp_path, addr="10.1.2.3")  # the archive taken to another machine
    assert moved.campus == "unknown"


def test_on_the_lab_a_restart_hands_no_dead_server_to_the_lab_before_its_first_check(tmp_path):
    crawler, net, _clock = _lab(tmp_path)
    assert crawler._campus_step(QS, net, Sink(tmp_path))
    again, _net, _clock = _lab(tmp_path)
    seed(again, HOME)
    again._server_down(QC, mod.CONN_GIVE_UP + 100)  # down for days: its next failure, before SSO's check came back
    assert not events(again, "host_unreachable")
    # on campus by the last finding: down, never the lab's; its rows fail (server_down, the owner 11-08), no attempt lost
    assert {(r["state"], r["reason"], r["attempts"]) for r in again.frontier.q(
        "SELECT state, reason, attempts FROM url WHERE host='c.nknu.edu.tw'")} == {("failed", "server_down", 0)}
