import json
import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from nknu.core.records import HoursRule, content_hash, from_json, to_json
from nknu.core.vocab import fill_validity
from nknu.core.writer import (
    add_alert,
    add_pending,
    apply_fact,
    current_fact,
    displaced_by,
    is_suppressed,
    retire_fact,
    set_state,
    suppress,
    upsert_doc,
    withdraw_doc,
)
from tests.factories import DOC, HOLIDAY, LIB_CLOSED, LIB_REGULAR, T0, T1, WR_TERM, make_db


@pytest.fixture
def conn(tmp_path):
    c = make_db(tmp_path)
    yield c
    c.close()


def test_apply_new_fact_is_current(conn):
    fid = apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    row = current_fact(conn, WR_TERM.key)
    assert row["id"] == fid and row["tier"] == 1 and row["recorded_to"] is None and row["review"] == "verified"
    rules = conn.execute("SELECT dows, opens, closes FROM hours_rule WHERE fact_id=? ORDER BY opens", (fid,)).fetchall()
    assert [tuple(r) for r in rules] == [("[4]", "18:00", "20:00"), ("[1, 2, 3, 5]", "19:00", "21:00")]


def test_apply_changed_supersedes(conn):
    a = apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    apply_fact(conn, replace(WR_TERM, rules=(HoursRule((1,), "18:00", "20:00"),)), adapter="pe_open_hours", review="verified", now=T1)
    assert conn.execute("SELECT recorded_to FROM fact WHERE id=?", (a,)).fetchone()[0] == T1
    assert current_fact(conn, WR_TERM.key)["supersedes"] == a


def test_retire_closes_without_successor(conn):
    apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    assert retire_fact(conn, WR_TERM.key, now=T1) is not None
    assert current_fact(conn, WR_TERM.key) is None
    assert retire_fact(conn, WR_TERM.key, now=T1) is None


def test_apply_requires_dates(conn):
    with pytest.raises(ValueError):
        apply_fact(conn, LIB_REGULAR, adapter="lib_open_time", review="verified", now=T0)


def test_same_page_reused_as_one_source(conn):
    apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    apply_fact(conn, HOLIDAY, adapter="aca_calendar_ics", review="auto_ok", now=T0)
    apply_fact(conn, replace(WR_TERM, key="other", entity_id="nknu.yanchao.gym.weight-room"), adapter="pe_open_hours",
               review="verified", now=T0)
    assert conn.execute("SELECT count(*) FROM source_doc WHERE url=?", (WR_TERM.source.url,)).fetchone()[0] == 1


def test_content_hash_ignores_snapshot_sha():
    assert content_hash(WR_TERM) == content_hash(replace(WR_TERM, source=replace(WR_TERM.source, sha256="other")))
    assert content_hash(WR_TERM) != content_hash(replace(WR_TERM, valid_to=date(2027, 1, 16)))


def test_json_roundtrip():
    for rec in (WR_TERM, HOLIDAY, DOC, LIB_REGULAR):
        assert from_json(to_json(rec)) == rec
    assert json.loads(to_json(WR_TERM))["type"] == "FactRecord"


def test_fill_validity_term(conn):
    r = fill_validity(conn, LIB_REGULAR, date(2026, 10, 22))
    assert (r.valid_from, r.valid_to) == (date(2026, 9, 7), date(2027, 1, 18))


def test_term_filled_fact_is_keyed_by_its_term(conn):
    # each term's table is its own lineage: next term's table never closes this term's (C1)
    assert fill_validity(conn, LIB_REGULAR, date(2026, 10, 22)).key == LIB_REGULAR.key + ":2026-09-07"


def test_fill_validity_unknown_term_returns_none(tmp_path):
    c = make_db(tmp_path, calendar=False)
    assert fill_validity(c, LIB_REGULAR, date(2026, 10, 22)) is None


def test_fill_validity_keeps_explicit(conn):
    assert fill_validity(conn, WR_TERM, date(2026, 10, 22)) is WR_TERM


def _pending(conn, h):
    return add_pending(conn, adapter="pe_open_hours", record_key=WR_TERM.key, op="upsert", record=WR_TERM, content_hash=h,
                       diff="+ 19:00–21:00", reason="high_impact", now=T0)


def test_pending_dedup_and_supersede(conn):
    assert _pending(conn, "h1") is not None
    assert _pending(conn, "h1") is None
    _pending(conn, "h2")
    assert [r[0] for r in conn.execute("SELECT status FROM pending_change ORDER BY id")] == ["superseded", "pending"]


def test_rejected_not_reproposed(conn):
    pid = _pending(conn, "h1")
    conn.execute("UPDATE pending_change SET status='rejected' WHERE id=?", (pid,))
    assert _pending(conn, "h1") is None


def test_partial_doc_merges(conn):
    upsert_doc(conn, DOC, adapter="sso_news", now=T0)
    upsert_doc(conn, replace(DOC, partial=True, title="", excerpt="摘錄", attachments=("附件.pdf",)), adapter="sso_news", now=T1)
    row = conn.execute("SELECT title, excerpt, attachments_json FROM doc").fetchone()
    assert (row[0], row[1], json.loads(row[2])) == (DOC.title, "摘錄", ["附件.pdf"])
    upsert_doc(conn, DOC, adapter="sso_news", now=T1)  # list page again without excerpt: keep the excerpt
    assert conn.execute("SELECT excerpt FROM doc").fetchone()[0] == "摘錄"


def test_withdraw_doc(conn):
    upsert_doc(conn, DOC, adapter="sso_news", now=T0)
    withdraw_doc(conn, "sso_news", DOC.doc_key, now=T1)
    assert conn.execute("SELECT withdrawn_at FROM doc").fetchone()[0] == T1


def test_set_state_resets_missing(conn):
    set_state(conn, "sso_news", DOC.key, "doc", "h", T0)
    conn.execute("UPDATE record_state SET missing_full_scans=2")
    set_state(conn, "sso_news", DOC.key, "doc", "h2", T1)
    assert tuple(conn.execute("SELECT content_hash, missing_full_scans FROM record_state").fetchone()) == ("h2", 0)


def test_alert_dedup(conn):
    assert add_alert(conn, adapter="x", level="warn", code="c", message="m", dedup_key="k", now=T0)
    assert add_alert(conn, adapter="x", level="warn", code="c", message="m", dedup_key="k", now=T0) is None


def test_fact_rows_still_append_only(conn):
    fid = apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM fact WHERE id=?", (fid,))


# C1 identity: one source states one thing about a slot; a new statement replaces the old one in the same transaction


def test_write_replaces_overlapping_fact_of_the_same_source(conn):
    old = apply_fact(conn, LIB_CLOSED, adapter="lib_open_time", review="verified", now=T0)
    set_state(conn, "lib_open_time", LIB_CLOSED.key, "fact", "h-old", T0)
    moved = replace(LIB_CLOSED, key=LIB_CLOSED.key.replace("2026-10-25", "2026-10-24"), valid_from=date(2026, 10, 24))
    assert [r["id"] for r in displaced_by(conn, moved)] == [old]
    new = apply_fact(conn, moved, adapter="lib_open_time", review="verified", now=T1)
    assert conn.execute("SELECT recorded_to FROM fact WHERE id=?", (old,)).fetchone()[0] == T1
    assert current_fact(conn, LIB_CLOSED.key) is None and current_fact(conn, moved.key)["id"] == new
    # the old key may come back later as a change, never as "unchanged"
    assert conn.execute("SELECT status FROM record_state WHERE record_key=?", (LIB_CLOSED.key,)).fetchone()[0] == "retired"


def test_write_keeps_other_terms_sources_and_specificities(conn):
    apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    next_term = replace(WR_TERM, key="pe_open_hours:nknu.heping.gym.weight-room:hours:2027-02-22",
                        valid_from=date(2027, 2, 22), valid_to=date(2027, 7, 1))
    notice = replace(WR_TERM, key="pe_news:1:nknu.heping.gym.weight-room:2026-09-07",
                     source=replace(WR_TERM.source, url="https://c.nknu.edu.tw/reality/NewsView.aspx?Nid=1", kind="announcement"))
    exception = replace(WR_TERM, key="pe_open_hours:x", specificity=2, valid_from=date(2026, 10, 24), valid_to=date(2026, 10, 25))
    for rec in (next_term, notice, exception):
        assert displaced_by(conn, rec) == []
        apply_fact(conn, rec, adapter="pe_open_hours", review="verified", now=T1)
    assert all(current_fact(conn, r.key) for r in (WR_TERM, next_term, notice, exception))


def test_holiday_under_a_new_uid_replaces_the_old_one(conn):
    apply_fact(conn, HOLIDAY, adapter="aca_calendar_ics", review="auto_ok", now=T0)
    apply_fact(conn, replace(HOLIDAY, key="ics-holiday:hol-new"), adapter="aca_calendar_ics", review="auto_ok", now=T1)
    assert current_fact(conn, HOLIDAY.key) is None and current_fact(conn, "ics-holiday:hol-new")


def test_suppressed_content_is_remembered_by_hash(conn):
    assert not is_suppressed(conn, "lib_open_time", LIB_CLOSED.key, "h1")
    for _ in range(2):  # idempotent
        suppress(conn, adapter="lib_open_time", record_key=LIB_CLOSED.key, content_hash="h1", kind="fact", ref="f1",
                 note="undo", now=T0)
    assert is_suppressed(conn, "lib_open_time", LIB_CLOSED.key, "h1")
    assert not is_suppressed(conn, "lib_open_time", LIB_CLOSED.key, "h2") and not is_suppressed(conn, "manual", LIB_CLOSED.key, "h1")
