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
    retire_fact,
    set_state,
    upsert_doc,
    withdraw_doc,
)
from tests.factories import DOC, HOLIDAY, LIB_REGULAR, T0, T1, WR_TERM, make_db


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
