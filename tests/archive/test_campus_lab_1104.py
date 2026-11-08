"""The first batch runs on the lab machine, on the school's network (owner 11-04: 「在实验室的电脑先爬你说的第一批的网站吧」).
There a school host that does not answer is down, not one only the school's network reaches: _give_up_hosts and
_server_down hand a host that never answered off campus to the lab machine (host_unreachable) only when this run is not
itself on campus. And the main loop writes meta run:alive_at about once a minute, so the dashboard can tell a run that
hangs while holding run.lock from one that rests (dashboard design 2026-11-04 §2.5)."""

from nknu.archive.crawler import ALIVE_S, CONN_GIVE_UP
from tests.archive.test_archive import make, seed

HOST = "lend.nknu.edu.tw"
QUEUE = f"ip:{HOST}"


def _two_rows(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    seed(crawler, f"https://{HOST}/")
    seed(crawler, f"https://{HOST}/other")
    assert HOST in crawler.frontier.hosts_of(QUEUE)
    return crawler, clock


def _rows(crawler):
    return crawler.frontier.q("SELECT state, reason, attempts FROM url WHERE host=?", (HOST,))


def test_on_campus_a_server_down_fails_its_school_rows_never_to_the_lab(tmp_path):
    """Until 11-08 they kept waiting (B2); the owner 11-08: 「连不上的死服务器……直接标成连不上/跳过，不用再傻等了」."""
    crawler, _clock = _two_rows(tmp_path)
    crawler.campus = "on"
    crawler._server_down(QUEUE, CONN_GIVE_UP)
    rows = _rows(crawler)
    assert [(r["state"], r["reason"], r["attempts"]) for r in rows] == [("failed", "server_down", 0)] * 2
    codes = [e["code"] for e in crawler.frontier.q("SELECT code FROM event")]
    assert "server_down" in codes and "host_unreachable" not in codes


def test_off_campus_a_server_down_still_hands_school_rows_to_the_lab(tmp_path):
    crawler, _clock = _two_rows(tmp_path)
    crawler.campus = "off"
    crawler._server_down(QUEUE, CONN_GIVE_UP)
    assert {(r["state"], r["reason"]) for r in _rows(crawler)} == {("failed", "host_unreachable")}


def test_campus_unknown_keeps_the_hand_over_to_the_lab(tmp_path):
    crawler, _clock = _two_rows(tmp_path)
    crawler.campus = "unknown"
    crawler._give_up_hosts([HOST], "conn_refused")
    assert {(r["state"], r["reason"]) for r in _rows(crawler)} == {("failed", "host_unreachable")}


def test_on_campus_giving_up_a_host_charges_an_attempt_instead(tmp_path):
    crawler, _clock = _two_rows(tmp_path)
    crawler.campus = "on"
    lab, charged = crawler._give_up_hosts([HOST], "conn_refused")
    assert (lab, charged) == (0, 2)
    rows = _rows(crawler)
    assert all(r["state"] == "pending" and r["reason"] == "conn_refused" and r["attempts"] == 1 for r in rows)


def test_the_main_loop_beat_is_written_once_a_minute(tmp_path):
    crawler, clock = _two_rows(tmp_path)
    assert crawler.frontier.meta("run:alive_at") is None
    crawler._beat()
    first = crawler.frontier.meta("run:alive_at")
    assert first is not None and abs(float(first) - clock()) < 1
    clock.t += ALIVE_S / 2
    crawler._beat()
    assert crawler.frontier.meta("run:alive_at") == first
    clock.t += ALIVE_S
    crawler._beat()
    assert float(crawler.frontier.meta("run:alive_at")) > float(first)
