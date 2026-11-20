"""11-18 (爬虫judge: the night of 11-18 crawled from 01:00 to 04:13, the last hours on a trickle of tries again — CV pages
answered with no card (no_area) and timeouts, each asked again 30 to 35 minutes later, three times in all — so that
something always came due within the run's end wait and the export, the ingest and the morning's work were pushed back):
a run over sites that are all under periodic update (the nightly run) leaves the rows waiting for a try again to the
next night, as it leaves a resting server's. A row never tried that comes due soon is still waited for; a try again due
now is still fetched; any other run waits for its retries as before. Made-up rows (tests/archive/test_run_only)."""

from __future__ import annotations

import json

from nknu.archive import crawler as mod
from tests.archive.test_run_only import A1, A2, ROUTES, T0, V3, V4, crawl, four, frontier

SITES = frozenset({"c.nknu.edu.tw/aca", "c.nknu.edu.tw/ccee"})


def _night(tmp_path, *, under_update: bool, attempts: int = 1, due_in: float = 300.0):
    """A1, V3, V4 got; A2 waiting `due_in` seconds (a try again when attempts > 0)."""
    ids = four(tmp_path)
    f = frontier(tmp_path)
    if under_update:
        f.meta(mod.UPDATE_SITES_KEY, json.dumps(sorted(SITES)))
    f.x("UPDATE url SET state='done', status=200 WHERE id IN (?, ?, ?)", (ids[A1], ids[V3], ids[V4]))
    f.x("UPDATE url SET attempts=?, reason=?, next_at=? WHERE id=?",
        (attempts, mod.NO_AREA if attempts else None, T0 + due_in, ids[A2]))
    f.close()
    crawler, _net, _clock = crawl(tmp_path, ROUTES, sites=SITES)
    crawler.start_run_caps()  # what run() does at its start
    return crawler


def test_the_nightly_run_leaves_a_try_again_for_the_next_night(tmp_path):
    crawler = _night(tmp_path, under_update=True)
    assert crawler._should_end() is True and crawler.end_reason == "waiting"


def test_a_run_of_sites_not_under_update_waits_for_its_try_again_as_before(tmp_path):
    crawler = _night(tmp_path, under_update=False)
    assert crawler._should_end() is False


def test_a_row_never_tried_that_comes_due_soon_is_still_waited_for(tmp_path):
    crawler = _night(tmp_path, under_update=True, attempts=0)
    assert crawler._should_end() is False


def test_a_try_again_due_now_is_still_fetched(tmp_path):
    crawler = _night(tmp_path, under_update=True, due_in=-1.0)
    assert crawler._should_end() is False and crawler.end_reason is None
