"""The owner's 11-08 decisions after the lab run stopped by itself at 11-07 05:45 and sat still for 19 hours: 「同意你的建议：
现在立刻重开爬虫……同时你去修好'数据库写入撞车'和'lis 旧死链不重试'这两个问题，修好测试过就直接换上」, and of the open questions
「连不上的死服务器那 168 个网址：直接标成连不上/跳过，不用再傻等了」.

1. The run stopped on `database is locked` (SQLITE_BUSY_SNAPSHOT): pagetypes.observe read in a savepoint, the
   reconciliation thread's own connection (Frontier.sibling) committed, and observe's write was refused at once, the
   30 s busy timeout no help; every sqlite3.DatabaseError stopped the run. A transaction that reads, then writes, now
   takes the write lock first (frontier.write_first: BEGIN IMMEDIATE), and a database held by another connection never
   stops the run: the row is fetched again a minute later, a press counts as one that got no answer (a data read only
   reads: pending again), the main loop tries again at its next turn. Any other database error stops the run as before.
2. lis: old download addresses only the old archive names (found_via old_frontier, no page of this round links to them)
   answer 500 since lis moved; each was tried 3 times and every 3 in a row rested the whole server 15 min (about 36
   hours for lis's last 388 rows). Such a row that answers 500 fails at once (http_500: `retry http_5` puts it back);
   neither the server's error streak nor the client's breaker counts it. A 502, 503 or 504, and an old address a page
   of this round links to, are handled as before.
3. A server that did not connect CONN_GIVE_UP times in a row (about two hours) no longer keeps its rows waiting for ever
   (B2 of the second review 10-26, Claude's): they fail, reason server_down, no attempt charged (`retry server_down`
   puts them back), and a row found on it later fails at its next failure. A name that does not resolve gives up after
   DNS_GIVE_UP lookups (about an hour; it was a day), and one already past that when a run starts gives its rows up then.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from nknu.archive import crawler as mod
from nknu.archive import pagetypes
from nknu.archive.frontier import Frontier, write_first
from tests.archive.test_archive import Sink, make, seed, snap
from tests.archive.test_crawler_round1 import QC, conn_err, crawl, drive, events, result, rows, settle, status_err

HOME = "https://c.nknu.edu.tw/aca/default.aspx"
LIS, QL = "lis.nknu.edu.tw", "ip:140.127.40.136"


def _busy() -> sqlite3.OperationalError:
    """What SQLite raised at 05:45 (Python 3.11+ gives the code on the exception)."""
    exc = sqlite3.OperationalError("database is locked")
    exc.sqlite_errorcode, exc.sqlite_errorname = sqlite3.SQLITE_BUSY_SNAPSHOT, "SQLITE_BUSY_SNAPSHOT"
    return exc


def _raise(exc):
    def f(*_a, **_k):
        raise exc
    return f


def _between(write) -> threading.Thread:
    """Another connection's write while the caller is between its read and its write (05:45's order). It is given a
    second: with the caller's write lock taken first it waits for the caller's commit instead (its own busy timeout)."""
    t = threading.Thread(target=write)
    t.start()
    t.join(1.0)
    return t


# ---------------------------------------------------------------- 1. the database held by the run's other connection


def test_a_savepoint_that_read_first_is_refused_at_once_when_the_other_connection_commits_between(tmp_path):
    """The failure itself, as SQLite gives it: no wait helps, and db_busy knows it for one that passes."""
    fr = Frontier(tmp_path / "frontier.db")
    other = fr.sibling()
    fr.conn.execute("SAVEPOINT s")
    fr.conn.execute("SELECT count(*) FROM url").fetchone()
    _between(lambda: other.x("INSERT INTO meta(key, value) VALUES ('other', '1')")).join(10)
    with pytest.raises(sqlite3.OperationalError) as caught:
        fr.conn.execute("INSERT INTO meta(key, value) VALUES ('mine', '1')")
    fr.conn.execute("ROLLBACK")
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT and mod.db_busy(caught.value)


def test_write_first_lets_the_other_connection_wait_for_its_commit(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    other = fr.sibling()
    with fr.lock, write_first(fr.conn, "t"):
        fr.conn.execute("SELECT count(*) FROM url").fetchone()
        t = _between(lambda: other.x("INSERT INTO meta(key, value) VALUES ('other', '1')"))
        assert t.is_alive()  # it waits for the write lock
        fr.conn.execute("INSERT INTO meta(key, value) VALUES ('mine', '1')")
    t.join(10)
    assert not t.is_alive() and not fr.conn.in_transaction
    assert {r[0] for r in fr.q("SELECT key FROM meta WHERE key IN ('mine', 'other')")} == {"mine", "other"}


def test_write_first_rolls_back_on_an_error_and_is_a_savepoint_inside_a_transaction(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    with pytest.raises(RuntimeError), write_first(fr.conn, "t"):
        fr.conn.execute("INSERT INTO meta(key, value) VALUES ('gone', '1')")
        raise RuntimeError
    assert not fr.conn.in_transaction and fr.meta("gone") is None
    fr.conn.execute("BEGIN")
    with pytest.raises(RuntimeError), write_first(fr.conn, "t"):
        fr.conn.execute("INSERT INTO meta(key, value) VALUES ('inner', '1')")
        raise RuntimeError
    fr.conn.execute("INSERT INTO meta(key, value) VALUES ('outer', '1')")
    assert fr.conn.in_transaction  # the outer transaction is the caller's to end
    fr.conn.execute("COMMIT")
    assert fr.meta("inner") is None and fr.meta("outer") == "1"


def test_observe_survives_a_commit_of_the_reconciliation_between_its_read_and_its_write(tmp_path, monkeypatch):
    """The 05:45 order end to end: observe reads the url row, the sibling writes (here while observe asks whether the
    page has a click it cannot press, between its read and its writes), observe writes."""
    fr = Frontier(tmp_path / "frontier.db")
    fr.add(HOME, host="c.nknu.edu.tw", site="c.nknu.edu.tw/aca", unit=None, tier=0, kind="page", depth=0,
           found_via="seed", found_from=None)
    uid = fr.row(HOME)["id"]
    other = fr.sibling()
    waited = []

    def hint(conn, page_id):
        waited.append(_between(lambda: other.x("INSERT INTO meta(key, value) VALUES ('r7', '1')")))
        return False

    monkeypatch.setattr(pagetypes, "click_hint", hint)
    m = pagetypes.Measure(text_chars=500, scripts=0, loading=False, spa=False, ajax=False)
    seen = pagetypes.observe(fr, url_id=uid, url=HOME, m=m, site="c.nknu.edu.tw/aca")
    waited[0].join(10)
    assert fr.row(HOME)["page_type"] == seen.key and fr.meta("r7") == "1"


def test_a_busy_database_puts_the_row_back_for_a_minute_and_the_run_goes_on(tmp_path):
    crawler, net, clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    row = crawler._next(QC)
    crawler._process = _raise(_busy())
    crawler._handle(QC, net, Sink(crawler.root), row)
    assert crawler.end_reason is None and not crawler.stop.is_set()
    r = rows(crawler)[HOME]
    assert (r["state"], r["attempts"]) == ("pending", 0)
    assert r["next_at"] == pytest.approx(clock() + mod.DB_BUSY_RETRY_S)
    assert len(events(crawler, "db_busy")) == 1


def test_any_other_database_error_still_stops_the_run(tmp_path):
    crawler, net, _ = crawl(tmp_path, {})
    seed(crawler, HOME)
    row = crawler._next(QC)
    crawler._process = _raise(sqlite3.DatabaseError("database disk image is malformed"))
    crawler._handle(QC, net, Sink(crawler.root), row)
    assert crawler.end_reason == "db_error"


def test_a_busy_database_that_cannot_take_the_row_back_either_stops_the_run(tmp_path):
    crawler, net, _ = crawl(tmp_path, {})
    seed(crawler, HOME)
    row = crawler._next(QC)
    crawler._process = _raise(_busy())
    crawler.frontier.x = _raise(_busy())
    crawler._handle(QC, net, Sink(crawler.root), row)
    assert crawler.end_reason == "db_error"


def _action(crawler, list_url, kind):
    list_id = rows(crawler)[list_url]["id"]
    crawler.frontier.x("UPDATE url SET state='done' WHERE id=?", (list_id,))
    crawler.frontier.x(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
        "target, row_key, state) VALUES (?, 1, 2, ?, ?, 'c.nknu.edu.tw', 'c.nknu.edu.tw/aca', 'cnknu/list', "
        "'w.warc.gz', 0, 'MasterGrid', ?, 'active')", (list_id, kind, QC, f"{kind}-key"))
    return crawler.frontier.q("SELECT * FROM action WHERE row_key=?", (f"{kind}-key",))[0]


def test_a_busy_database_on_a_data_read_puts_it_back_and_the_run_goes_on(tmp_path):
    crawler, net, _ = crawl(tmp_path, {})
    seed(crawler, HOME)
    a = _action(crawler, HOME, "data")
    crawler._press_one = _raise(_busy())
    crawler._press(QC, net, Sink(crawler.root), a)
    assert crawler.end_reason is None
    assert crawler.frontier.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "pending"
    assert events(crawler, "db_busy")


def test_a_busy_database_on_a_page_press_fails_it_as_one_without_an_answer(tmp_path):
    """A POST that may have reached the server is never sent again (§6.4): its chain starts again from its GET once."""
    crawler, net, _ = crawl(tmp_path, {})
    seed(crawler, HOME)
    a = _action(crawler, HOME, "pager")
    crawler._press_one = _raise(_busy())
    crawler._press(QC, net, Sink(crawler.root), a)
    assert crawler.end_reason is None
    got = crawler.frontier.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert (got["state"], got["reason"]) == ("failed", mod.DB_BUSY)
    r = rows(crawler)[HOME]
    assert (r["state"], r["reason"]) == ("pending", "chain_restart")


def test_a_busy_database_in_the_main_loop_never_ends_the_run(tmp_path):
    crawler, _, _ = crawl(tmp_path, {})
    real, calls = crawler._beat, []

    def beat():
        calls.append(1)
        if len(calls) == 1:
            raise _busy()
        real()

    crawler._beat = beat
    crawler.run(poll_s=0.01)
    assert json.loads(events(crawler, "run_end")[-1])["reason"] == "finished"
    assert len(calls) >= 2 and events(crawler, "db_busy")


# ---------------------------------------------------------------- 2. old addresses that answer 500


def _lis(tmp_path, answer):
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/_dl/": answer}, queues={LIS: QL})
    forgiven: list[str] = []
    net.reset_host = forgiven.append
    return crawler, net, clock, forgiven


def _old(crawler, url, via="old_frontier", prio=3):
    """A row only an old source names (seeds.py: the old archive's, found_via old_frontier; Common Crawl's)."""
    crawler.plan.mark(url, 1)
    crawler.seed_url(url, source="old_url" if via == "old_frontier" else via, found_via=via, prio=prio)
    return rows(crawler)[url]


DL = [f"https://{LIS}/_dl/{i:010x}/a{i}.pdf" for i in range(4)]


@pytest.mark.parametrize("via", ["old_frontier", "commoncrawl"])
def test_an_address_only_an_old_source_names_that_answers_500_fails_at_once(tmp_path, via):
    crawler, net, clock, forgiven = _lis(tmp_path, status_err(500))
    for u in DL:
        _old(crawler, u, via)
    drive(crawler, net, QL)
    got = {u: (r["state"], r["attempts"], r["reason"]) for u, r in rows(crawler).items() if "/_dl/" in u}
    assert got == {u: ("failed", 1, "http_500") for u in DL}  # four in a row: no rest, nothing waits
    assert not events(crawler, "server_rest") and crawler.frontier.paused_until(QL) <= clock()
    assert crawler.frontier.q("SELECT coalesce(max(err_fail), 0) FROM server WHERE queue=?", (QL,))[0][0] == 0
    assert forgiven.count(LIS) == len(DL)  # the client's breaker (three in a row on one name) does not count them
    assert crawler.frontier.retry("http_5") == (len(DL), 1)  # and `retry http_5` puts them back


def test_an_old_address_a_page_of_this_round_links_to_is_retried_as_before(tmp_path):
    crawler, net, clock, _ = _lis(tmp_path, status_err(500))
    old = _old(crawler, DL[0])
    seed(crawler, f"https://{LIS}/news")  # a page of this round that links to it
    page_id = rows(crawler)[f"https://{LIS}/news"]["id"]
    crawler.frontier.x("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '附件', 'dup')", (page_id, old["url"]))
    crawler.frontier.x("UPDATE url SET state='done' WHERE id=?", (page_id,))
    drive(crawler, net, QL)
    r = rows(crawler)[DL[0]]
    assert (r["state"], r["attempts"], r["reason"]) == ("pending", 1, "http_500") and r["next_at"] > clock()
    assert crawler.frontier.q("SELECT err_fail FROM server WHERE queue=?", (QL,))[0][0] == 1


@pytest.mark.parametrize(("status", "rests"), [(503, True), (502, False), (504, False)])
def test_an_old_only_address_that_answers_another_5xx_is_handled_as_before(tmp_path, status, rests):
    crawler, net, _clock, _ = _lis(tmp_path, status_err(status))
    _old(crawler, DL[0])
    drive(crawler, net, QL)
    r = rows(crawler)[DL[0]]
    assert (r["state"], r["attempts"], r["reason"]) == ("pending", 1, f"http_{status}")
    assert bool(events(crawler, "server_rest")) == rests
    if not rests:
        assert crawler.frontier.q("SELECT err_fail FROM server WHERE queue=?", (QL,))[0][0] == 1


def test_a_server_that_errs_on_rows_of_this_round_still_rests_between_old_500s(tmp_path):
    """Politeness unchanged for the rows of this round: their three errors in a row still rest the server, the old
    addresses' 500s between them neither add to that streak nor clear it (taken in id order: this round's row, an
    old one, this round's, …)."""
    crawler, net, _clock = make(tmp_path, {"robots.txt": snap("x", 404), f"{LIS}/": status_err(500)},
                                queues={LIS: QL})
    new = [f"https://{LIS}/f{i}.pdf" for i in range(3)]
    old = [f"https://{LIS}/_dl/{i:010x}/o{i}.pdf" for i in range(3)]
    for n, o in zip(new, old, strict=True):
        seed(crawler, n)
        _old(crawler, o, prio=0)
    drive(crawler, net, QL)
    got = rows(crawler)
    assert len(events(crawler, "server_rest")) == 1  # after the third of this round's, as before
    assert [(got[u]["state"], got[u]["attempts"]) for u in new] == [("pending", 1)] * 3
    assert [(got[u]["state"], got[u]["attempts"]) for u in old] == [("failed", 1), ("failed", 1), ("pending", 0)]


# ---------------------------------------------------------------- 3. dead servers and names that do not resolve

DEAD, DQ = "lend.nknu.edu.tw", "ip:lend.nknu.edu.tw"


def _waiting(crawler):
    return {(r["state"], r["reason"], r["attempts"]) for r in
            crawler.frontier.q("SELECT state, reason, attempts FROM url WHERE host=?", (DEAD,))}


def test_on_campus_a_server_down_fails_its_waiting_rows_and_a_later_one_at_its_next_failure(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    crawler.campus = "on"
    seed(crawler, f"https://{DEAD}/")
    seed(crawler, f"https://{DEAD}/other")
    crawler._server_down(DQ, mod.CONN_GIVE_UP)
    assert _waiting(crawler) == {("failed", "server_down", 0)}
    seed(crawler, f"https://{DEAD}/later")  # found on a page after it was given up
    crawler._server_down(DQ, mod.CONN_GIVE_UP + 1)
    assert _waiting(crawler) == {("failed", "server_down", 0)}
    assert len(events(crawler, "server_down")) == 1 and not events(crawler, "host_unreachable")
    assert crawler.frontier.retry("server_down") == (3, 1)
    assert _waiting(crawler) == {("pending", "server_down", 0)}


def test_a_server_down_leaves_held_and_round_two_rows_as_they_are(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    crawler.campus = "on"
    for u, state, reason in ((f"https://{DEAD}/h", "held", "cap:depth"), (f"https://{DEAD}/l", "later", "round2:x")):
        seed(crawler, u)
        crawler.frontier.x("UPDATE url SET state=?, reason=? WHERE url=?", (state, reason, u))
    crawler._server_down(DQ, mod.CONN_GIVE_UP)
    assert _waiting(crawler) == {("held", "cap:depth", 0), ("later", "round2:x", 0)}


def test_a_dead_server_no_longer_keeps_the_run_from_ending(tmp_path):
    q = "ip:140.127.9.9"
    crawler, net, _clock = make(tmp_path, {"robots.txt": conn_err()}, queues={"a.nknu.edu.tw": q})
    crawler.campus = "on"
    urls = [f"https://a.nknu.edu.tw/p{i}" for i in range(3)]
    for u in urls:
        seed(crawler, u)
    settle(crawler, net, [q], rounds=mod.CONN_GIVE_UP * 3)
    assert crawler.frontier.pending_left()[0] == 0
    assert {(r["state"], r["reason"], r["attempts"]) for r in rows(crawler).values()} == {("failed", "server_down", 0)}
    assert {result(crawler, u) for u in urls} == {"failed"}


def test_a_name_that_does_not_resolve_gives_up_after_about_an_hour(tmp_path):
    crawler, _net, clock = make(tmp_path, {}, resolver=lambda h: ("dns-fail", None, None))
    start = clock()
    seed(crawler, "https://gone.nknu.edu.tw/a")
    for _ in range(mod.DNS_GIVE_UP + 2):
        row = rows(crawler)["https://gone.nknu.edu.tw/a"]
        if row["state"] != "pending":
            break
        clock.t = row["next_at"]
        crawler._reresolve()
    row = rows(crawler)["https://gone.nknu.edu.tw/a"]
    assert (row["state"], row["reason"]) == ("failed", "dns") and clock() - start <= 2 * 3600
    assert mod.DNS_GIVE_UP == 4


def test_a_name_already_past_the_limit_gives_its_rows_up_when_a_run_starts(tmp_path):
    """Counted when the limit was 8: dns_fail 5 never looked up again (the lookups stop at the limit), so its rows
    would wait for ever."""
    crawler, _net, clock = make(tmp_path, {}, resolver=lambda h: ("dns-fail", None, None))
    seed(crawler, "https://gone.nknu.edu.tw/a")
    crawler.frontier.set_host("gone.nknu.edu.tw", dns_fail=5, dns_at=clock())
    crawler.frontier.x("UPDATE url SET next_at=? WHERE host='gone.nknu.edu.tw'", (clock() + 9000,))
    crawler.run(poll_s=0.01)
    row = rows(crawler)["https://gone.nknu.edu.tw/a"]
    assert (row["state"], row["reason"]) == ("failed", "dns") and events(crawler, "dns_gave_up")
    assert json.loads(events(crawler, "run_end")[-1])["reason"] == "finished"


# ---------------------------------------------------------------- the review of 11-08 (three reviewers, each finding checked)


def test_a_server_that_answered_before_keeps_its_rows_waiting_when_it_goes_down(tmp_path):
    """Only a server that never answered this archive is dead: one that answered (SSO under maintenance, say) is down
    for now, and its rows wait with every attempt as before (B2), one warning saying so."""
    crawler, _net, clock = make(tmp_path, {})
    crawler.campus = "on"
    seed(crawler, f"https://{DEAD}/")
    crawler.frontier.request(DQ, DEAD, f"https://{DEAD}/x", "page", clock() - 9000, clock() - 8999, 200, None)
    crawler._server_down(DQ, mod.CONN_GIVE_UP)
    assert _waiting(crawler) == {("pending", None, 0)}
    assert "it answered before" in events(crawler, "server_down")[0]


def test_a_server_given_up_that_answers_again_gets_its_rows_back(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    crawler.campus = "on"
    seed(crawler, f"https://{DEAD}/")
    seed(crawler, f"https://{DEAD}/other")
    crawler.frontier.server_set(DQ, mod.CONN_GIVE_UP)
    crawler._server_down(DQ, mod.CONN_GIVE_UP)
    assert _waiting(crawler) == {("failed", "server_down", 0)}
    crawler._server_ok(DQ)  # it answered
    assert _waiting(crawler) == {("pending", None, 0)} and crawler.frontier.server_fails(DQ) == 0
    assert "2 URLs" in events(crawler, "server_back")[0]


def test_a_server_that_errs_on_everything_rests_after_spare_run_old_500s_in_a_row(tmp_path):
    """The safety of the old addresses' 500s: a server that gives nothing but 5xx is erring as a whole, so past
    SPARE_RUN in a row an old address's 500 is the server's again: it rests, the row is tried again later."""
    crawler, net, _clock = make(tmp_path, {"robots.txt": snap("x", 404), f"{LIS}/": status_err(500)}, queues={LIS: QL})
    old = [f"https://{LIS}/_dl/{i:010x}/o{i}.pdf" for i in range(mod.SPARE_RUN + 5)]
    for u in old:
        _old(crawler, u)
    drive(crawler, net, QL)
    got = [(rows(crawler)[u]["state"], rows(crawler)[u]["attempts"]) for u in old]
    # robots.txt answered 404 first; then SPARE_RUN 500s spared (failed at once), then three counted: the rest
    assert got[:mod.SPARE_RUN] == [("failed", 1)] * mod.SPARE_RUN
    assert got[mod.SPARE_RUN:mod.SPARE_RUN + 3] == [("pending", 1)] * 3 and got[-1] == ("pending", 0)
    assert len(events(crawler, "server_rest")) == 1


def test_an_answer_between_old_500s_keeps_them_spared(tmp_path):
    """lis's dead downloads come mixed with ones that still answer (an English file name: a blank page, 200)."""
    def answer(u):
        if u.endswith("ok.pdf"):
            return snap(u, 200, body=b"%PDF-1.4 x", headers={"content-type": "application/pdf"})
        raise status_err(500)

    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), f"{LIS}/": answer}, queues={LIS: QL})
    old = [f"https://{LIS}/_dl/{i:010x}/" + ("ok.pdf" if i % 10 == 9 else f"o{i}.pdf") for i in range(60)]
    for u in old:
        _old(crawler, u)
    drive(crawler, net, QL)
    got = rows(crawler)
    assert {got[u]["state"] for u in old if not u.endswith("ok.pdf")} == {"failed"}
    assert not events(crawler, "server_rest") and crawler.frontier.paused_until(QL) <= clock()


def test_old_only_counts_a_link_in_the_other_scheme(tmp_path):
    """A host that answers on https only: its rows are written in https, a page's link keeps its http spelling."""
    crawler, _net, _clock = make(tmp_path, {})
    row = _old(crawler, DL[0])
    seed(crawler, f"https://{LIS}/news")
    page_id = rows(crawler)[f"https://{LIS}/news"]["id"]
    assert crawler.frontier.old_only(row["id"])
    crawler.frontier.x("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '附件', 'dup')",
                       (page_id, DL[0].replace("https://", "http://")))
    assert not crawler.frontier.old_only(row["id"])


def test_a_busy_database_after_the_row_got_its_end_keeps_it(tmp_path):
    """E.g. in _walk_check, or after a Google Sites page's re-read kept its good answer (§23.9 B2)."""
    crawler, net, _clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    row = crawler._next(QC)

    def done_then_busy(*_a, **_k):
        crawler.frontier.x("UPDATE url SET state='done', status=200 WHERE id=?", (row["id"],))
        raise _busy()

    crawler._process = done_then_busy
    crawler._handle(QC, net, Sink(crawler.root), row)
    assert crawler.end_reason is None
    r = rows(crawler)[HOME]
    assert (r["state"], r["status"]) == ("done", 200) and "kept" in events(crawler, "db_busy")[0]


def test_a_busy_database_after_a_page_press_got_its_answer_walks_the_list_again_from_its_get(tmp_path):
    crawler, net, _clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    a = _action(crawler, HOME, "pager")

    def answered_then_busy(*_a, **_k):
        crawler.frontier.x("UPDATE action SET state='done', status=200 WHERE id=?", (a["id"],))
        raise _busy()

    crawler._press_one = answered_then_busy
    crawler._press(QC, net, Sink(crawler.root), a)
    assert crawler.end_reason is None
    assert crawler.frontier.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "done"  # not a failure
    r = rows(crawler)[HOME]
    assert (r["state"], r["reason"]) == ("pending", "chain_restart")


def test_a_busy_database_after_a_data_read_got_its_answer_reads_it_again(tmp_path):
    crawler, net, clock = crawl(tmp_path, {})
    seed(crawler, HOME)
    a = _action(crawler, HOME, "data")

    def answered_then_busy(*_a, **_k):
        crawler.frontier.x("UPDATE action SET state='done', status=200 WHERE id=?", (a["id"],))
        raise _busy()

    crawler._press_one = answered_then_busy
    crawler._press(QC, net, Sink(crawler.root), a)
    got = crawler.frontier.q("SELECT state, next_at FROM action WHERE id=?", (a["id"],))[0]
    assert got["state"] == "pending" and got["next_at"] == pytest.approx(clock() + mod.DB_BUSY_RETRY_S)


def test_a_row_failed_as_server_down_may_be_marked_the_schools():
    from nknu.archive import outcome

    assert outcome.markable("failed", "server_down") and outcome.markable("failed", "connect")


def test_a_server_that_answered_before_is_given_up_after_a_day_of_failures(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    crawler.campus = "on"
    seed(crawler, f"https://{DEAD}/")
    crawler.frontier.request(DQ, DEAD, f"https://{DEAD}/x", "page", clock() - 9000, clock() - 8999, 200, None)
    crawler._server_down(DQ, mod.CONN_GIVE_UP_ANSWERED - 1)
    assert _waiting(crawler) == {("pending", None, 0)}
    crawler._server_down(DQ, mod.CONN_GIVE_UP_ANSWERED)
    assert _waiting(crawler) == {("failed", "server_down", 0)}
    assert mod.CONN_GIVE_UP_ANSWERED * mod.CONN_PAUSE_S == 24 * 3600
