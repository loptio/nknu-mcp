"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.4; the owner 10-25 「只多
抓一层」, 11-16 「校外只多抓一层，由程序强制」): an off-campus page round two fetches one page of (tier 2, leaf 1) is
stored and nothing more: no link of it is read, no part of it recorded, and its host is not asked for a missing page
first (the probe only judges the host's other pages). A round-one leaf page (性别所's Wix frame, tier 1) is read as
always. The real crawler on a fake network; made-up pages."""

from __future__ import annotations

from tests.archive.test_archive import snap
from tests.archive.test_pilot_fixes import make, work

URL = "https://news.example.org/story/1"
HTML = ("<html><head><title>報導</title></head><body><p>本校師生獲獎的報導一則。</p>"
        "<a href=\"https://news.example.org/story/2\">下一則</a><a href=\"https://other.example.net/\">別站</a>"
        "<img src=\"https://news.example.org/pic.jpg\"></body></html>")


def _one(tmp_path, tier: int, leaf: int):
    crawler, net, _clock = make(tmp_path, {("GET", "news.example.org/story/1"): snap(URL, 200, html=HTML)})
    f = crawler.frontier
    f.add(URL, host="news.example.org", site="news.example.org", unit=None, tier=tier, kind="page", depth=1,
          found_via="link", found_from=None, reason="round2:offsite" if tier == 2 else None,
          owner_site="news.example.org", leaf=leaf, kind0="page")
    queue = crawler.queue_of("news.example.org")
    work(crawler, net, queue)
    return f, f.row(URL), net


def test_an_off_campus_page_of_round_two_is_that_page_only(tmp_path):
    f, row, net = _one(tmp_path, 2, 1)
    assert row["state"] == "done" and row["n_links"] == 0
    assert not f.q("SELECT 1 FROM page_res WHERE page_id=?", (row["id"],))
    assert [r["url"] for r in f.q("SELECT url FROM url ORDER BY id")] == [URL]
    asked = [u for m, u in net.seen if "news.example.org" in u and not u.endswith("/robots.txt")]
    assert asked == [URL]  # no probe for a missing page first


def test_a_round_two_page_that_is_no_leaf_is_read_as_always(tmp_path):
    f, row, _net = _one(tmp_path, 2, 0)
    assert row["n_links"] > 0 and f.q("SELECT 1 FROM page_res WHERE page_id=?", (row["id"],))
