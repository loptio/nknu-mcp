import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from nknu.core.factsdb import data_version
from nknu.core.records import EntityHint, ParseIssue, from_json
from nknu.core.writer import apply_fact, current_fact, set_state
from nknu.crawl.adapter import Adapter, Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.pipeline import run_adapter, run_all, run_order
from nknu.settings import load_settings
from tests.factories import DOC, HOLIDAY, LIB_REGULAR, TERM_BEGIN, WR_TERM, make_db

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


def approve_all(conn):
    for p in pending(conn):
        rec = from_json(p["record_json"])
        apply_fact(conn, rec, adapter=p["adapter"], review="verified", now="2026-10-22T03:00:00Z")
        set_state(conn, p["adapter"], rec.key, "fact", p["content_hash"], "2026-10-22T03:00:00Z")
        conn.execute("UPDATE pending_change SET status='approved' WHERE id=?", (p["id"],))
    conn.commit()


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


def test_hours_change_goes_to_pending_not_fact(conn):
    res = run(conn, FakeAdapter([WR_TERM]))
    assert res.passed and res.n_pending == 1 and len(pending(conn)) == 1 and current_fact(conn, WR_TERM.key) is None


def test_holiday_written_directly(conn):
    run(conn, FakeAdapter([HOLIDAY]))
    assert current_fact(conn, HOLIDAY.key)["review"] == "auto_ok"


def test_unchanged_is_noop(conn):
    run(conn, FakeAdapter([WR_TERM]))
    approve_all(conn)
    n = data_version(conn)
    res = run(conn, FakeAdapter([WR_TERM]))
    assert data_version(conn) == n and res.n_written == 0 and res.n_pending == 0


def test_term_fact_filled_before_pending(conn):
    run(conn, FakeAdapter([LIB_REGULAR]))
    rec = from_json(pending(conn)[0]["record_json"])
    assert (rec.valid_from, rec.valid_to) == (date(2026, 9, 7), date(2027, 1, 18))


def test_collapsed_run_keeps_old_values(conn):
    seed_history(conn, "fake", [40, 41, 40])
    res = run(conn, FakeAdapter([DOC, DOC]))
    assert not res.passed and last_run(conn)["passed"] == 0 and alerts(conn, "collapsed")
    assert conn.execute("SELECT count(*) FROM doc").fetchone()[0] == 0


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


def test_three_full_scans_retire_doc(conn):
    run(conn, FakeAdapter([DOC, OTHER_DOC], name="sso_news"))
    for _ in range(3):
        run(conn, FakeAdapter([OTHER_DOC], name="sso_news"))
    row = conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()
    assert row[0] is not None


def test_doc_404_withdrawn_immediately(conn):
    run(conn, FakeAdapter([DOC, OTHER_DOC], name="sso_news"))
    run(conn, FakeAdapter([replace(DOC, gone=True), OTHER_DOC], name="sso_news"))
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is not None


def test_event_date_change_pending(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([TERM_BEGIN]))
    assert c.execute("SELECT count(*) FROM calendar_event").fetchone()[0] == 1 and not pending(c)
    moved = replace(TERM_BEGIN, during_from=date(2026, 9, 14), during_to=date(2026, 9, 15))
    run(c, FakeAdapter([moved]))
    assert len(pending(c)) == 1 and c.execute("SELECT during_from FROM calendar_event").fetchone()[0] == "2026-09-07"


def test_tentative_event_pending(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([replace(TERM_BEGIN, status="tentative")]))
    assert len(pending(c)) == 1 and c.execute("SELECT count(*) FROM calendar_event").fetchone()[0] == 0


def test_readonly_writes_nothing(conn):
    res = run(conn, FakeAdapter([HOLIDAY]), readonly=True)
    assert res.passed and current_fact(conn, HOLIDAY.key) is None
    assert conn.execute("SELECT count(*) FROM crawl_run").fetchone()[0] == 0


def test_no_term_dates_alert(tmp_path):
    c = make_db(tmp_path, calendar=False)
    run(c, FakeAdapter([LIB_REGULAR]))
    assert alerts(c, "no_term_dates") and not pending(c)


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



def test_list_rerun_after_detail_is_noop(conn):
    run(conn, FakeAdapter([DOC, replace(DOC, partial=True, excerpt="停修申請期間為11月3日至16日")], name="sso_news"))
    assert conn.execute("SELECT excerpt FROM doc").fetchone()[0]
    res = run(conn, FakeAdapter([DOC], name="sso_news"))
    assert res.n_written == 0 and conn.execute("SELECT excerpt FROM doc").fetchone()[0]


def test_term_table_is_not_missing_while_no_term_is_in_force(conn):
    # during the winter break the library page still shows its table but the calendar has no current term to date it:
    # the record cannot be written, and the term's lineage must not count as missing either
    run(conn, FakeAdapter([LIB_REGULAR], name="lib_open_time"))
    approve_all(conn)
    for _ in range(3):
        run(conn, FakeAdapter([LIB_REGULAR], name="lib_open_time"), today=date(2027, 1, 20))
    assert not pending(conn) and alerts(conn, "no_term_dates")
    assert current_fact(conn, LIB_REGULAR.key + ":2026-09-07")
