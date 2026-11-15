"""Periodic update, stage 1 (design 2026-11-16-periodic-update-design.md §四, §五 row 1, §7.2, §7.3; plan
docs/superpowers/plans/2026-11-16-update-stage-1.md; the owner 11-16: 「接下来按照你的节奏走吧，不用等我点头了」): a
stored answer clears what the previous answer wrote; every list row read is kept (list_row); a look at a list stops
where a whole page holds nothing new, the full walk's record untouched (list_look); growth caps of an update run.
Made-up rows and pages only."""

from __future__ import annotations

from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import settle

HOST = "a.nknu.edu.tw"
QUEUE = f"ip:{HOST}"
URL = f"https://{HOST}/news-a.aspx"
PAGE = ("<html><head><title>測試公告</title></head><body><p>公告內文</p>"
        f"<a href='https://{HOST}/news-b.aspx'>另一則</a></body></html>")
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
ANSWER_COLS = ("flags", "title", "sniff", "filename", "text_chars", "n_links", "n_postbacks")


def _back(f, uid: int) -> None:
    f.x("UPDATE url SET state='pending', reason='chain_restart', prio=0, next_at=0 WHERE id=?", (uid,))


def _twice(tmp_path, second):
    """A page answered 200, then (put back, an hour later) answered `second`: the row after each."""
    answers = [snap(URL, 200, html=PAGE), second]
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/news-a.aspx": lambda u: answers.pop(0)})
    seed(crawler, URL)
    settle(crawler, net, [QUEUE])
    first = crawler.frontier.row(URL)
    crawler.frontier.x("UPDATE url SET flags='thin' WHERE id=?", (first["id"],))  # a mark of answer 1 (reconcile)
    _back(crawler.frontier, first["id"])
    clock.t += 3600
    settle(crawler, net, [QUEUE])
    return first, crawler.frontier.row(URL)


# ---------------------------------------------------------------- Task 1


def test_a_404_after_a_page_leaves_none_of_the_pages_columns(tmp_path):
    first, row = _twice(tmp_path, snap(URL, 404, html="<html><body>不在了</body></html>"))
    assert first["title"] == "測試公告" and first["sniff"] == "html" and first["n_links"] >= 1 and first["text_chars"]
    assert row["status"] == 404 and row["reason"] == "http_404"
    assert {k: row[k] for k in ANSWER_COLS} == dict.fromkeys(ANSWER_COLS)


def test_a_file_after_a_page_keeps_no_page_columns(tmp_path):
    _first, row = _twice(tmp_path, snap(URL, 200, body=PDF, headers={"content-type": "application/pdf"}))
    assert (row["status"], row["sniff"], row["kind"]) == (200, "pdf", "file")
    assert (row["title"], row["n_links"], row["n_postbacks"], row["text_chars"], row["flags"]) == (None,) * 5
