"""Periodic update, stage 0a (the owner 11-16: 「可以，先做 0a」; design 2026-11-16-periodic-update-design.md §五 row
0a and §7.1, after 2026-11-15 update.md §6.1 and §7 items 1, 3): before a url row's stored answer can be replaced, a
copy of it is kept in url_version (when the row is taken to be fetched again, or when a later answer takes its place
on the row); and an answer that comes back starts the row's try count again. Made-up rows and pages only."""

from __future__ import annotations

import sqlite3

import pytest

from nknu.archive import postback, profiles, scope
from nknu.archive.frontier import Frontier, _missing
from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import settle, timeout_err
from tests.archive.test_pilot_fixes import make as make_press
from tests.archive.test_pilot_fixes import work
from tests.archive.test_postback import fx

HOST = "a.nknu.edu.tw"
URL = f"https://{HOST}/news-a.aspx"
FIRST = "<html><head><title>測試公告</title></head><body><p>第一次看到的公告內文</p></body></html>"


def _ledger(tmp_path) -> Frontier:
    f = Frontier(tmp_path / "frontier.db")
    f.set_host(HOST, queue=f"ip:{HOST}")
    return f


def _row(f: Frontier, url: str = URL) -> int:
    f.add(url, host=HOST, site=HOST, unit=None, tier=1, kind="page", depth=0, found_via="seed", found_from=None,
          owner_site=HOST)
    return f.row(url)["id"]


def _at(n: int) -> str:
    return f"2026-11-0{n}T01:00:00+00:00"


def _answer(f: Frontier, uid: int, n: int, **cols) -> None:
    """The n-th answer of a fetch, written as crawler._store writes one (made up): its own record in its own file."""
    f.finish(uid, **{"state": "done", "reason": None, "status": 200, "ctype": "text/html", "bytes": 10 * n,
                     "sha256": f"s{n}", "warc": f"warc/q{n}.warc.gz", "warc_offset": 100 * n, "warc_len": 50,
                     "kind": "page", "fetched_at": _at(n), **cols})


def _take(f: Frontier):
    """The worker takes the next due row of the host (every row is due: now is far ahead)."""
    return f.next_for([HOST], 1e12)


def _back(f: Frontier, uid: int) -> None:
    """The row put back to be fetched again, as postback.restart_chain puts a list page back."""
    f.x("UPDATE url SET state='pending', reason='chain_restart', prio=0, next_at=0 WHERE id=?", (uid,))


def _kept(f: Frontier, uid: int) -> list:
    return f.q("SELECT * FROM url_version WHERE url_id=? ORDER BY id", (uid,))


# ---------------------------------------------------------------- the table and its two copy points


def test_an_answer_is_kept_before_the_row_is_taken_again(tmp_path):
    f = _ledger(tmp_path)
    uid = _row(f)
    assert _take(f)["id"] == uid and not _kept(f, uid)  # never answered: nothing to keep
    _answer(f, uid, 1)
    assert not _kept(f, uid)  # the first answer replaced nothing
    f.x("UPDATE url SET flags='thin' WHERE id=?", (uid,))  # a mark added after the answer (reconcile.thin_pages)
    _back(f, uid)
    _take(f)
    _answer(f, uid, 2, status=404, reason="http_404")
    kept = _kept(f, uid)
    assert [(k["warc"], k["warc_offset"], k["warc_len"], k["status"], k["sha256"], k["flags"], k["fetched_at"],
             k["kind"]) for k in kept] == [("warc/q1.warc.gz", 100, 50, 200, "s1", "thin", _at(1), "page")]
    assert kept[0]["kept_at"]
    row = f.row_by_id(uid)
    assert (row["warc_offset"], row["status"], row["reason"]) == (200, 404, "http_404")  # the row says this fetch


def test_a_fetch_that_gets_no_answer_never_changes_what_was_kept(tmp_path):
    f = _ledger(tmp_path)
    uid = _row(f)
    _take(f)
    _answer(f, uid, 1)
    _back(f, uid)
    _take(f)
    # crawler._failed's columns (a timeout, tried again later), then crawler.py's write after a 3xx to a school error
    # page: the row still points at answer 1, but its fetched_at and its status no longer say that answer's
    f.finish(uid, state="pending", attempts=1, reason="timeout", fetched_at=None, next_at=0)
    f.x("UPDATE url SET status=302, final_url='https://a.nknu.edu.tw/error.aspx' WHERE id=?", (uid,))
    _take(f)
    f.finish(uid, state="failed", attempts=2, reason="timeout")
    kept = _kept(f, uid)
    # the first copy stands: answer 1's own status and fetched_at; its state and reason are the row's when it was
    # set aside (put back to be fetched again), never the answer's result
    assert [(k["warc_offset"], k["status"], k["fetched_at"], k["final_url"], k["state"], k["reason"])
            for k in kept] == [(100, 200, _at(1), None, "pending", "chain_restart")]
    row = f.row_by_id(uid)
    assert (row["warc_offset"], row["state"]) == (100, "failed")  # a fetch with no answer never moves the pointer


def test_an_answer_replaced_without_taking_the_row_is_kept(tmp_path):
    f = _ledger(tmp_path)
    uid = _row(f)
    _take(f)
    _answer(f, uid, 1)
    _back(f, uid)
    f.x("UPDATE url SET state='active' WHERE id=?", (uid,))  # crawler._move_scheme / _switch_scheme: no next_for
    _answer(f, uid, 2)
    assert [k["warc_offset"] for k in _kept(f, uid)] == [100]
    assert f.row_by_id(uid)["warc_offset"] == 200


def test_the_same_answer_written_again_keeps_nothing(tmp_path):
    f = _ledger(tmp_path)
    uid = _row(f)
    _take(f)
    _answer(f, uid, 1)
    # reconcile.rejudge (_kept): the stored answer read again, its pointer and fetched_at as they were, new marks
    f.finish(uid, state="done", reason=None, status=200, warc="warc/q1.warc.gz", warc_offset=100, warc_len=50,
             fetched_at=_at(1), flags="thin")
    other = _row(f, f"https://{HOST}/news-b.aspx")
    assert _take(f)["id"] == other
    f.finish(other, state="pending", attempts=1, reason="timeout", fetched_at=None, next_at=0)  # no answer
    _take(f)
    _answer(f, other, 3)  # its first answer: nothing was there to keep
    assert f.q("SELECT count(*) FROM url_version")[0][0] == 0


def test_each_answer_is_kept_once_in_order(tmp_path):
    f = _ledger(tmp_path)
    uid = _row(f)
    for n in (1, 2, 3):
        _take(f)
        _answer(f, uid, n)
        _back(f, uid)
    assert [k["warc_offset"] for k in _kept(f, uid)] == [100, 200]  # answer 3 is the row's own, not yet replaced
    assert f.row_by_id(uid)["warc_offset"] == 300


def test_an_archive_from_before_the_table_still_opens_read_only(tmp_path):
    """The table is the crawler's alone until the export reads it (stage 0b): a read-only view of a frontier.db
    written before it (the dashboard, the export, `archive verify`: frontier._missing) opens as it did; the crawler's
    own open adds it, every url row as it was."""
    path = tmp_path / "frontier.db"
    f = Frontier(path)
    f.set_host(HOST, queue=f"ip:{HOST}")
    uid = _row(f)
    _answer(f, uid, 1)
    f.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE url_version")
    conn.commit()
    before = conn.execute("SELECT * FROM url ORDER BY id").fetchall()
    conn.close()
    ro = Frontier(path, readonly=True)
    try:
        assert _missing(ro.conn) == []
        assert not ro.q("SELECT 1 FROM sqlite_master WHERE name='url_version'")  # a read-only view adds nothing
    finally:
        ro.close()
    rw = Frontier(path)
    try:
        assert rw.q("SELECT count(*) FROM url_version")[0][0] == 0
        assert [tuple(r) for r in rw.q("SELECT * FROM url ORDER BY id")] == [tuple(r) for r in before]
    finally:
        rw.close()


def test_a_page_fetched_again_keeps_its_first_answer_where_the_archive_has_it(tmp_path):
    """The real crawler on a fake network that archives every answer (test_pilot_fixes.PressNet): a page answered
    200, put back, answered 404 the second time. The row says 404; the first answer is kept, and its pointer reads
    that answer back from the WARC file."""
    answers = [snap(URL, 200, html=FIRST), snap(URL, 404, html="<html><body>不在了</body></html>")]
    crawler, net, clock = make_press(tmp_path, {("GET", "/news-a.aspx"): lambda target: answers.pop(0)})
    seed(crawler, URL)
    queue = f"ip:{HOST}"
    work(crawler, net, queue)
    assert crawler.frontier.row(URL)["status"] == 200
    _back(crawler.frontier, crawler.frontier.row(URL)["id"])
    clock.t += 3600
    work(crawler, net, queue)
    row = crawler.frontier.row(URL)
    assert (row["state"], row["status"], row["reason"]) == ("done", 404, "http_404") and not answers
    kept, = _kept(crawler.frontier, row["id"])
    assert kept["status"] == 200 and (kept["warc"], kept["warc_offset"]) != (row["warc"], row["warc_offset"])
    url, status, _headers, body = postback.read_response(crawler.root, kept["warc"], kept["warc_offset"])
    assert (url, status) == (URL, 200) and "第一次看到的公告內文" in body.decode()


# ---------------------------------------------------------------- an answer starts the try count again


@pytest.mark.parametrize("second", ["page", "404"])
def test_an_answer_after_a_fetch_that_got_none_starts_the_tries_again(tmp_path, second):
    calls: list[str] = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise timeout_err()
        return snap(url, 200, html=FIRST) if second == "page" else snap(url, 404, html="<html></html>")

    crawler, net, _clock = make(tmp_path, {"robots.txt": snap("x", 404), "/news-a.aspx": flaky})
    seed(crawler, URL)
    settle(crawler, net, [f"ip:{HOST}"])
    row = crawler.frontier.row(URL)
    assert len(calls) == 2 and row["state"] == "done" and row["status"] == (200 if second == "page" else 404)
    assert row["attempts"] == 0  # a try that got no answer is not held against the answer that came


def test_the_first_look_at_a_not_found_page_still_spends_a_try(tmp_path):
    """SSO's 「檔案不存在」 page for an attachment (review 10-26 B6, profiles.says_step): the first look spends a try
    and waits GONE_RECHECK_S, the second makes it gone. That second look is an answer: the count starts again."""
    att = scope.canonical("https://sso.nknu.edu.tw/news/attachedfile.aspx?cencrptedfilepath=abc&inline=1")
    gone = fx("sso_attachment_gone.html")
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404),
                                          "attachedfile.aspx": lambda url: snap(url, 200, html=gone)})
    crawler.queue_of("sso.nknu.edu.tw")
    crawler.frontier.add(att, host="sso.nknu.edu.tw", site="sso.nknu.edu.tw/news", unit=None, tier=0, kind="file",
                         depth=1, found_via="link", found_from=None, owner_site="sso.nknu.edu.tw/news", kind0="file",
                         prio=2)
    queue = "ip:sso.nknu.edu.tw"
    settle(crawler, net, [queue], rounds=1)
    row = crawler.frontier.row(att)
    assert (row["state"], row["reason"], row["attempts"]) == ("pending", profiles.GONE_RECHECK, 1)
    clock.t = row["next_at"] + 1
    settle(crawler, net, [queue])
    row = crawler.frontier.row(att)
    assert (row["state"], row["flags"], row["attempts"]) == ("done", "gone_page", 0)
