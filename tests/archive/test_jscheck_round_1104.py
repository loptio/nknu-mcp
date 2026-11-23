"""A site's browser check counts for this round only when it ended after the site's crawl began (11-04): a run that is
going now has written its run_start but no run_end yet, and a jscheck_end from an earlier round must not make the
dashboard count 待判定 (design 2026-11-04 §2.4) nor turn monitor's 待判定 red. On the lab machine 11-04 the 10-27
check, older than the batch-1 run_start, made 总务处 show 1,380 待判定 in 错."""

import json

from nknu.archive import monitor
from nknu.archive.frontier import Frontier

X, Y = "c.nknu.edu.tw/affair", "c.nknu.edu.tw/edu"


def _event(f: Frontier, code: str, **msg) -> None:
    f.event("info", code, json.dumps(msg))


def test_a_check_older_than_a_run_going_now_does_not_count(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    _event(f, "jscheck_end", reason="finished", sites=None)
    _event(f, "run_start", pid=1, gap=[1, 2], sites=[X])
    rounds = monitor.Rounds(f)
    assert not rounds.jscheck_done(X)  # crawled in this run, not checked since
    assert rounds.jscheck_done(Y)  # this run does not cover it: its check stands


def test_a_check_after_the_run_ended_counts_again(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    _event(f, "jscheck_end", reason="finished", sites=None)
    _event(f, "run_start", pid=1, gap=[1, 2], sites=[X])
    _event(f, "run_end", reason="finished", sites=[X])
    assert not monitor.Rounds(f).jscheck_done(X)
    _event(f, "jscheck_end", reason="finished", sites=[X])
    assert monitor.Rounds(f).jscheck_done(X)


def test_a_check_that_did_not_finish_never_counts(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    _event(f, "run_start", pid=1, gap=[1, 2], sites=[X])
    _event(f, "run_end", reason="finished", sites=[X])
    _event(f, "jscheck_end", reason="stop", sites=[X])
    assert not monitor.Rounds(f).jscheck_done(X)
