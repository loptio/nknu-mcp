"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.6; the 11-14 handover's
old debt c-2): Google's "automated queries" page on the google queue (Google Sites pages, their runtime pictures,
docs.google.com) is the service's protection, as on the API's queue: the queue rests (an hour, doubling while it goes
on), the row waits with no attempt used; never a refusal of the page, never a directory's denial streak, never a site's
pictures given up. Any other 403 there is answered as before. The real crawler on a fake network; made-up pages."""

from __future__ import annotations

from nknu.archive import google_api
from tests.archive.test_archive import snap
from tests.archive.test_pilot_fixes import make, work

URL = "https://sites.google.com/view/unit-x/home"
SORRY = "<html><head><title>Sorry...</title></head><body>Our systems have detected unusual traffic … automated queries" \
        "</body></html>"


def _one(tmp_path, body: str):
    crawler, net, clock = make(tmp_path, {("GET", "sites.google.com/view/unit-x"): snap(URL, 403, html=body)},
                               queues={"sites.google.com": "google"})
    f = crawler.frontier
    f.add(URL, host="sites.google.com", site="sites.google.com/view/unit-x", unit=None, tier=1, kind="page", depth=1,
          found_via="link", found_from=None, owner_site="sites.google.com/view/unit-x", kind0="page")
    queue = crawler.queue_of("sites.google.com")
    work(crawler, net, queue)
    return crawler, f, f.row(URL), clock


def test_googles_block_page_on_the_google_queue_rests_the_queue_and_keeps_the_row(tmp_path):
    _crawler, f, row, clock = _one(tmp_path, SORRY)
    assert (row["state"], row["attempts"]) == ("pending", 0)
    assert row["next_at"] >= clock() + google_api.BLOCK_PAUSE_S - 5
    assert google_api.block_state(f)[0] == 1
    assert f.q("SELECT 1 FROM pause WHERE queue='google'")


def test_another_403_there_is_answered_as_before(tmp_path):
    _crawler, _f, row, _clock = _one(tmp_path, "<html><body>Forbidden</body></html>")
    assert (row["state"], row["reason"]) == ("done", "http_403")
