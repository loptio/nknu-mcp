import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from nknu.core.factsdb import data_version
from nknu.core.records import EntityHint, HoursRule, ParseIssue, content_hash
from nknu.core.writer import add_pending, current_fact, set_state
from nknu.crawl.adapter import Adapter, Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.pipeline import _merge, run_adapter, run_all, run_order
from nknu.settings import load_settings
from tests.factories import DOC, HOLIDAY, LIB_CLOSED, LIB_REGULAR, TERM_BEGIN, VACATION, WR_TERM, make_db

TODAY = date(2026, 10, 22)
OTHER_DOC = replace(DOC, key="sso_news:B2", doc_key="B2", title="其他公告")
PAGE = "<html><body><div id='lbl_Contact'>ok</div></body></html>"


class FakeAdapter(Adapter):
    def __init__(self, records, html=PAGE, selectors=(), name="fake"):
        self.records, self.html, self.name, self.required_selectors = records, html, name, list(selectors)

    def fetch_all(self, ctx):
        body = self.html.encode()
        return [Snapshot(Target("https://c.nknu.edu.tw/x"), "https://c.nknu.edu.tw/x", 200, {}, body, self.html,
                         datetime.now(UTC), hashlib.sha256(body).hexdigest())]

    def parse(self, snap):
        return list(self.records)


class Boom(FakeAdapter):
    def __init__(self):
        super().__init__([], name="boom")

    def fetch_all(self, ctx):
        raise RuntimeError("site exploded")


@pytest.fixture
def conn(tmp_path):
    c = make_db(tmp_path)
    yield c
    c.close()


def run(conn, adapter, *, full_scan=True, readonly=False, today=TODAY):
    return run_adapter(conn, adapter, None, today=today, full_scan=full_scan, readonly=readonly)


def pending(conn):
    return conn.execute("SELECT * FROM pending_change WHERE status='pending'").fetchall()


def alerts(conn, code):
    return conn.execute("SELECT message FROM alert WHERE code=?", (code,)).fetchall()


def last_run(conn):
    return conn.execute("SELECT * FROM crawl_run ORDER BY id DESC LIMIT 1").fetchone()


def seed_history(conn, adapter, counts, full_scan=1):
    t = datetime.now(UTC)
    for i, n in enumerate(counts):
        ts = (t - timedelta(hours=len(counts) - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT INTO crawl_run(adapter, started_at, finished_at, full_scan, n_records, passed) VALUES (?,?,?,?,?,1)",
                     (adapter, ts, ts, full_scan, n))
    conn.commit()


def test_hours_written_directly(conn):
    # U2: venue hours and closures that pass the layout guard are written at once; no review queue
    res = run(conn, FakeAdapter([WR_TERM, LIB_CLOSED]))
    assert res.passed and res.n_written == 2 and not pending(conn)
    assert current_fact(conn, WR_TERM.key)["review"] == "auto_ok" and current_fact(conn, LIB_CLOSED.key)


def test_holiday_written_directly(conn):
    run(conn, FakeAdapter([HOLIDAY]))
    assert current_fact(conn, HOLIDAY.key)["review"] == "auto_ok"


def test_unchanged_is_noop(conn):
    run(conn, FakeAdapter([WR_TERM]))
    n = data_version(conn)
    res = run(conn, FakeAdapter([WR_TERM]))
    assert data_version(conn) == n and res.n_written == 0 and res.n_retired == 0


def test_changed_hours_replace_the_current_version(conn):
    run(conn, FakeAdapter([WR_TERM]))
    first = current_fact(conn, WR_TERM.key)["id"]
    run(conn, FakeAdapter([replace(WR_TERM, rules=(HoursRule((1, 2, 3, 4, 5), "18:00", "21:00"),))]))
    now = current_fact(conn, WR_TERM.key)
    assert now["supersedes"] == first and not pending(conn)


def test_term_fact_filled_before_write(conn):
    run(conn, FakeAdapter([LIB_REGULAR]))
    f = current_fact(conn, LIB_REGULAR.key + ":2026-09-07")
    assert (f["valid_from"], f["valid_to"]) == ("2026-09-07", "2027-01-18")


def test_direct_write_supersedes_a_waiting_review_item(conn):
    # an item queued by an older version stays in `nknu review`; once the crawler writes that key itself, approving
    # the old item would put older content back, so it is marked superseded
    old = replace(WR_TERM, rules=(HoursRule((1,), "07:00", "08:00"),))
    add_pending(conn, adapter="fake", record_key=WR_TERM.key, op="upsert", record=old, content_hash=content_hash(old),
                diff="old", reason="changed", now="2026-10-21T00:00:00Z")
    conn.commit()
    run(conn, FakeAdapter([WR_TERM]))
    assert not pending(conn) and current_fact(conn, WR_TERM.key)


def test_collapsed_run_keeps_old_values(conn):
    seed_history(conn, "fake", [40, 41, 40])
    res = run(conn, FakeAdapter([DOC, DOC]))
    assert not res.passed and last_run(conn)["passed"] == 0 and alerts(conn, "collapsed")
    assert conn.execute("SELECT count(*) FROM doc").fetchone()[0] == 0


def _rows(n):
    return [replace(DOC, key=f"fake:N{i}", doc_key=f"N{i}") for i in range(n)]


def test_a_lower_count_three_runs_in_a_row_is_the_new_normal(conn):
    # C6 (F-band-lockout): a source that legitimately shrinks (fewer closure days in a new year) is not voided for a
    # week: the same lower count three runs in a row, with every other check passing, is accepted as the new normal
    seed_history(conn, "fake", [10, 10, 10])
    results = [run(conn, FakeAdapter(_rows(4))) for _ in range(3)]
    assert [r.passed for r in results] == [False, False, True] and results[2].n_written == 4
    assert alerts(conn, "count_new_normal")
    assert conn.execute("SELECT resolved_at FROM alert WHERE code='count_out_of_band'").fetchone()[0] is not None
    # a broken pager looks the same: the alert is a warning that asks to check the source page
    level, message = conn.execute("SELECT level, message FROM alert WHERE code='count_new_normal'").fetchone()
    assert level == "warn" and "分页失效" in message
    # from now on the new count is the baseline: a small change stays in band
    assert run(conn, FakeAdapter(_rows(3))).passed and run(conn, FakeAdapter(_rows(4))).passed


def test_a_count_that_keeps_moving_is_not_accepted(conn):
    seed_history(conn, "fake", [10, 10, 10])
    assert [run(conn, FakeAdapter(_rows(n))).passed for n in (4, 3, 4, 4, 4)] == [False, False, False, False, True]


def test_a_higher_count_is_not_accepted(conn):
    # C6 names a lower count only: a sudden doubling (a parser that repeats rows) keeps voiding
    seed_history(conn, "fake", [10, 10, 10])
    assert not any(run(conn, FakeAdapter(_rows(25))).passed for _ in range(4))


def test_a_lower_count_with_another_violation_is_not_accepted(conn):
    seed_history(conn, "fake", [10, 10, 10])
    broken = FakeAdapter(_rows(4), html="<html><body>維護中</body></html>", selectors=["#lbl_Contact"])
    assert not any(run(conn, broken).passed for _ in range(4))


def test_a_void_that_persists_is_reminded_once_a_day(conn):
    # C6: the same alert at most once a day, instead of once and never again
    broken = FakeAdapter([HOLIDAY], html="<html><body>維護中</body></html>", selectors=["#lbl_Contact"])
    run(conn, broken)
    first = conn.execute("SELECT created_at FROM alert WHERE code='selector_missing'").fetchone()[0]
    run(conn, broken)
    assert conn.execute("SELECT count(*), max(created_at) FROM alert WHERE code='selector_missing'").fetchone()[:] == (1, first)
    conn.execute("UPDATE alert SET created_at='2026-10-20T00:00:00Z'")
    conn.commit()
    run(conn, broken)
    n, at = conn.execute("SELECT count(*), max(created_at) FROM alert WHERE code='selector_missing'").fetchone()
    assert n == 1 and at >= first


def test_list_the_adapter_cannot_follow_voids_instead_of_crashing(conn):
    # a ParseError while paging (a row without its id, a pager that never ends) is a layout change: void, old values kept
    from nknu.crawl.adapter import ParseError

    class Lost(FakeAdapter):
        def fetch_all(self, ctx):
            raise ParseError("CAMPUS: pager did not end within 50 pages")

    res = run(conn, Lost([]))
    assert not res.passed and alerts(conn, "parse_failed") and last_run(conn)["passed"] == 0


def test_missing_selector_voids_run(conn):
    res = run(conn, FakeAdapter([HOLIDAY], html="<html><body>維護中</body></html>", selectors=["#lbl_Contact"]))
    assert not res.passed and alerts(conn, "selector_missing") and current_fact(conn, HOLIDAY.key) is None


def test_parse_exception_voids_run(conn):
    class Bad(FakeAdapter):
        def parse(self, snap):
            raise ValueError("layout changed")

    res = run(conn, Bad([]))
    assert not res.passed and alerts(conn, "parse_failed")


def test_partial_scan_does_not_count_missing(conn):
    set_state(conn, "sso_news", "sso_news:A", "doc", "h", "2026-10-21T00:00:00Z")
    run(conn, FakeAdapter([], name="sso_news"), full_scan=False)
    assert conn.execute("SELECT missing_full_scans FROM record_state").fetchone()[0] == 0


def test_dated_table_entries_are_not_retired_when_they_leave_the_page(conn):
    # C2: a closure day or term table printed on a table page expires on its own date; a page that drops it (a past
    # closure pruned, next term's table posted) is not a retraction
    run(conn, FakeAdapter([WR_TERM, LIB_CLOSED]))
    for _ in range(4):
        res = run(conn, FakeAdapter([LIB_REGULAR]))
        assert res.n_retired == 0
    assert current_fact(conn, WR_TERM.key) and current_fact(conn, LIB_CLOSED.key) and not pending(conn)


def test_events_missing_three_full_scans_are_retired_directly(tmp_path):
    # C2: calendar events (and other facts) absent from three full scans that could have produced them are retired
    # without review
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([TERM_BEGIN, VACATION, HOLIDAY]))
    results = [run(c, FakeAdapter([VACATION])) for _ in range(3)]
    assert [r.n_retired for r in results] == [0, 0, 2] and not pending(c)
    assert c.execute("SELECT status FROM calendar_event WHERE uid=?", (TERM_BEGIN.key,)).fetchone()[0] == "withdrawn"
    assert current_fact(c, HOLIDAY.key) is None
    assert c.execute("SELECT status FROM calendar_event WHERE uid=?", (VACATION.key,)).fetchone()[0] == "confirmed"
    assert {r[0] for r in c.execute("SELECT record_key FROM record_state WHERE status='retired'")} == {TERM_BEGIN.key,
                                                                                                       HOLIDAY.key}


def test_absence_counts_only_when_the_run_could_have_produced_it(tmp_path):
    # C2: the adapter says whether this run had the chance to produce a record (e.g. a detail page it did not read)
    c = make_db(tmp_path, calendar=False)

    asked = []

    class Blind(FakeAdapter):
        def counts_missing(self, key, kind, span, records, today):
            asked.append((key, kind, span, len(records), today))
            return False

    run(c, FakeAdapter([TERM_BEGIN, VACATION, HOLIDAY], name="blind"))
    for _ in range(4):
        run(c, Blind([VACATION], name="blind"))
    assert c.execute("SELECT status FROM calendar_event WHERE uid=?", (TERM_BEGIN.key,)).fetchone()[0] == "confirmed"
    assert current_fact(c, HOLIDAY.key) and not c.execute("SELECT max(missing_full_scans) FROM record_state").fetchone()[0]
    assert (TERM_BEGIN.key, "event", (date(2026, 9, 7), date(2026, 9, 8)), 1, TODAY) in asked
    assert (HOLIDAY.key, "fact", (date(2026, 10, 24), date(2026, 10, 27)), 1, TODAY) in asked


def test_three_full_scans_retire_doc(conn):
    run(conn, FakeAdapter([DOC, OTHER_DOC], name="sso_news"))
    for _ in range(3):
        run(conn, FakeAdapter([OTHER_DOC], name="sso_news"))
    row = conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()
    assert row[0] is not None


def _withdrawn(conn) -> int:
    return conn.execute("SELECT count(*) FROM doc WHERE withdrawn_at IS NOT NULL").fetchone()[0]


def test_a_listing_far_shorter_than_what_is_in_force_retires_nothing(conn):
    # a pager that stops working lists only the first page: on a fresh install (no count history, no count guard) the
    # rows it no longer reaches are held, not retired after three full scans, and the maintainer is warned once
    run(conn, FakeAdapter(_rows(40)))
    results = [run(conn, FakeAdapter(_rows(15))) for _ in range(5)]
    assert all(r.passed for r in results) and sum(r.n_retired for r in results) == 0 and _withdrawn(conn) == 0
    (level, message), = conn.execute("SELECT level, message FROM alert WHERE code='missing_held'").fetchall()
    assert level == "warn" and "25" in message and "40" in message
    # the pager works again: a count far above the last week's, but of rows already in force, is the listing back (not
    # voided for a week like a parser that picks up new rows); from then on an absence counts as before
    assert run(conn, FakeAdapter(_rows(40))).passed and alerts(conn, "count_back")
    assert not conn.execute("SELECT count(*) FROM record_state WHERE missing_full_scans <> 0").fetchone()[0]
    assert [run(conn, FakeAdapter(_rows(39))).n_retired for _ in range(3)] == [0, 0, 1]


def test_a_broken_pager_accepted_as_the_new_normal_retires_nothing(conn):
    # C6 accepts the same lower count three runs in a row; the rows the short listing no longer shows stay
    seed_history(conn, "fake", [40, 40, 40])
    run(conn, FakeAdapter(_rows(40)))
    results = [run(conn, FakeAdapter(_rows(15))) for _ in range(8)]
    assert [r.passed for r in results][:3] == [False, False, True] and all(r.passed for r in results[2:])
    assert sum(r.n_retired for r in results) == 0 and _withdrawn(conn) == 0
    assert alerts(conn, "count_new_normal") and alerts(conn, "missing_held")


def test_rows_replaced_by_as_many_new_ones_are_still_retired(conn):
    # a source that re-creates its rows under new ids (a calendar's UIDs) lists as many as before: not a short listing
    run(conn, FakeAdapter(_rows(40)))
    fresh = [replace(DOC, key=f"fake:M{i}", doc_key=f"M{i}") for i in range(40)]
    assert [run(conn, FakeAdapter(fresh)).n_retired for _ in range(3)] == [0, 0, 40]
    assert not alerts(conn, "missing_held")


def test_doc_404_withdrawn_immediately(conn):
    run(conn, FakeAdapter([DOC, OTHER_DOC], name="sso_news"))
    res = run(conn, FakeAdapter([DOC, replace(DOC, title="", gone=True), OTHER_DOC], name="sso_news"))
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is not None
    assert res.n_retired == 1


class SeesKnown(FakeAdapter):
    def fetch_all(self, ctx):
        self.known = set(ctx.known_keys)
        return super().fetch_all(ctx)


def test_a_404_announcement_still_listed_does_not_churn(conn):
    # F-404-churn: the list row stays while its detail page answers 404; later runs neither write (no rebuild every
    # 2 hours) nor put the dead notice back, and the adapter is told the key is known (no detail fetch again)
    run(conn, FakeAdapter([DOC, OTHER_DOC], name="sso_news"))
    run(conn, FakeAdapter([DOC, replace(DOC, title="", gone=True), OTHER_DOC], name="sso_news"))
    n = data_version(conn)
    again = SeesKnown([DOC, replace(DOC, title="", gone=True), OTHER_DOC], name="sso_news")
    res = run(conn, again)
    assert (res.n_written, res.n_retired) == (0, 0) and data_version(conn) == n and DOC.key in again.known
    listed_only = SeesKnown([DOC, OTHER_DOC], name="sso_news")
    res = run(conn, listed_only, full_scan=False)
    assert res.n_written == 0 and DOC.key in listed_only.known
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is not None
    # an edited list row is new content: written (and shown) again
    run(conn, FakeAdapter([replace(DOC, title="115學年度第1學期停修申請（更正）"), OTHER_DOC], name="sso_news"), full_scan=False)
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is None


def test_undone_announcement_is_known_to_the_adapter(conn):
    # its detail page is not fetched again on every run (the maintainer took it down)
    from nknu.admin.undo import undo

    run(conn, FakeAdapter([DOC], name="sso_news"))
    undo(conn, DOC.key, now="2026-10-22T05:00:00Z")
    seer = SeesKnown([DOC], name="sso_news")
    assert run(conn, seer).n_written == 0 and DOC.key in seer.known


def test_event_date_change_written_directly(tmp_path):
    # U2: a rescheduled event is written at once
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([TERM_BEGIN]))
    moved = replace(TERM_BEGIN, during_from=date(2026, 9, 14), during_to=date(2026, 9, 15))
    res = run(c, FakeAdapter([moved]))
    assert res.n_written == 1 and not pending(c)
    assert c.execute("SELECT during_from FROM calendar_event").fetchone()[0] == "2026-09-14"


def test_tentative_and_cancelled_events_written_directly(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([replace(TERM_BEGIN, status="tentative"), VACATION]))
    assert not pending(c)
    assert dict(c.execute("SELECT uid, status FROM calendar_event").fetchall()) == {TERM_BEGIN.key: "tentative",
                                                                                     VACATION.key: "confirmed"}
    run(c, FakeAdapter([replace(TERM_BEGIN, status="cancelled"), VACATION]))
    assert c.execute("SELECT status FROM calendar_event WHERE uid=?", (TERM_BEGIN.key,)).fetchone()[0] == "cancelled"
    assert not pending(c)


def test_readonly_writes_nothing(conn):
    res = run(conn, FakeAdapter([HOLIDAY]), readonly=True)
    assert res.passed and current_fact(conn, HOLIDAY.key) is None
    assert conn.execute("SELECT count(*) FROM crawl_run").fetchone()[0] == 0


def test_no_term_dates_alert(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([LIB_REGULAR]))
    assert alerts(c, "no_term_dates") and not c.execute("SELECT count(*) FROM fact").fetchone()[0]


def test_entity_hint_alert(conn):
    run(conn, FakeAdapter([EntityHint("h1", "攀岩場", "和平校區 攀岩場 週一", "https://c.nknu.edu.tw/x")]))
    assert alerts(conn, "new_entity")


def test_staff_text_never_logged(conn):
    run(conn, FakeAdapter([ParseIssue("k", "date", "王小明請假 無法解析")]))
    assert all("王小明" not in r[0] for r in conn.execute("SELECT message FROM alert"))


def test_ics_runs_first():
    assert run_order(["sso_news", "aca_calendar_ics", "pe_news"])[0] == "aca_calendar_ics"


def test_one_adapter_failure_isolated(tmp_path):
    make_db(tmp_path).close()
    settings = load_settings({"NKNU_DATA_DIR": str(tmp_path)})
    results = run_all(settings, today=TODAY, adapters=[Boom(), FakeAdapter([HOLIDAY])], client=object())
    assert [r.passed for r in results] == [False, True]


def test_disabled_source_skipped(tmp_path):
    make_db(tmp_path).close()
    settings = load_settings({"NKNU_DATA_DIR": str(tmp_path), "NKNU_DISABLED_SOURCES": "fake"})
    assert run_all(settings, today=TODAY, adapters=[FakeAdapter([HOLIDAY])], client=object()) == []


def test_event_written_then_unchanged_noop(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([TERM_BEGIN]))
    n = data_version(c)
    run(c, FakeAdapter([TERM_BEGIN]))
    assert data_version(c) == n



def test_an_issue_keyed_on_an_announcement_keeps_the_announcement():
    # a detail page that failed (or a list date that did not parse) is reported under the announcement's key; merging
    # by key must neither drop the list row nor swallow the issue
    issue = ParseIssue(DOC.key, "detail", "公告詳情頁沒讀到")
    merged = _merge([DOC, issue, ParseIssue(OTHER_DOC.key, "date", "x"), OTHER_DOC])
    assert DOC in merged and OTHER_DOC in merged and issue in merged and len(merged) == 4


def test_list_rerun_after_detail_is_noop(conn):
    run(conn, FakeAdapter([DOC, replace(DOC, partial=True, excerpt="停修申請期間為11月3日至16日")], name="sso_news"))
    assert conn.execute("SELECT excerpt FROM doc").fetchone()[0]
    res = run(conn, FakeAdapter([DOC], name="sso_news"))
    assert res.n_written == 0 and conn.execute("SELECT excerpt FROM doc").fetchone()[0]


def test_term_table_is_not_missing_while_no_term_is_in_force(conn):
    # during the winter break the library page still shows its table but the calendar has no current term to date it:
    # the record cannot be written, and the term's lineage must not count as missing either
    run(conn, FakeAdapter([LIB_REGULAR], name="lib_open_time"))
    for _ in range(3):
        run(conn, FakeAdapter([LIB_REGULAR], name="lib_open_time"), today=date(2027, 1, 20))
    assert not pending(conn) and alerts(conn, "no_term_dates")
    assert current_fact(conn, LIB_REGULAR.key + ":2026-09-07")
