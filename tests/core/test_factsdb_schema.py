import sqlite3
import uuid

import pytest

from nknu.core.factsdb import bump_data_version, connect, data_version, migrate

T0 = "2026-10-22T00:00:00Z"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "facts.db")
    migrate(conn)
    conn.execute("INSERT INTO entity(id, type, name_zh) VALUES ('nknu.org.pe','Organization','體育室')")
    conn.execute("INSERT INTO entity(id, type, name_zh, campus) VALUES ('nknu.heping.gym.weight-room','ExerciseGym','重量訓練室','heping')")
    yield conn
    conn.close()


def source(db, url="https://c.nknu.edu.tw/reality/Page.aspx?PN=8", sha=None):
    return db.execute(
        "INSERT INTO source_doc(url, title, publisher, kind, adapter, fetched_at, warc_sha256) VALUES (?,?,?,?,?,?,?)",
        (url, "場地開放時間", "nknu.org.pe", "table_page", "pe_open_hours", T0, sha or uuid.uuid4().hex),
    ).lastrowid


def insert_fact(db, lineage=None, valid=("2026-09-07", "2027-01-09"), url="https://c.nknu.edu.tw/reality/Page.aspx?PN=8",
                specificity=1):
    fid = str(uuid.uuid4())
    db.execute(
        """INSERT INTO fact(id, lineage_id, record_key, entity_id, attribute, value_json, valid_from, valid_to, valid_basis,
                            specificity, source_doc, tier, extraction, review, recorded_from)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fid, lineage or str(uuid.uuid4()), "k", "nknu.heping.gym.weight-room", "hours", '{"closed": false}', valid[0], valid[1],
         "explicit", specificity, source(db, url), 1, "rule", "auto_ok", T0),
    )
    return fid


def test_migrate_idempotent(db):
    migrate(db)
    migrate(db)
    assert data_version(db) == 0


def test_strict_rejects_wrong_type(db):
    with pytest.raises(sqlite3.Error):
        insert_fact(db, specificity="x")


def test_delete_fact_forbidden(db):
    f = insert_fact(db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM fact WHERE id=?", (f,))


def test_update_value_forbidden(db):
    f = insert_fact(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE fact SET value_json='{}' WHERE id=?", (f,))


def test_close_once_allowed_then_frozen(db):
    f = insert_fact(db)
    db.execute("UPDATE fact SET recorded_to='2026-11-01T00:00:00Z' WHERE id=?", (f,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE fact SET recorded_to='2026-12-01T00:00:00Z' WHERE id=?", (f,))


def test_one_current_per_lineage(db):
    insert_fact(db, lineage="L")
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(db, lineage="L", valid=("2027-03-01", "2027-04-01"))


def test_same_source_overlap_rejected(db):
    insert_fact(db, lineage="A")
    with pytest.raises(sqlite3.IntegrityError, match="overlap"):
        insert_fact(db, lineage="B")


def test_same_source_overlap_allowed_after_close(db):
    a = insert_fact(db, lineage="A")
    db.execute("UPDATE fact SET recorded_to=? WHERE id=?", (T0, a))
    insert_fact(db, lineage="B")


def test_cross_source_overlap_allowed(db):
    insert_fact(db, lineage="A")
    insert_fact(db, lineage="B", url="https://other.example/page")


def test_reversed_interval_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(db, valid=("2026-10-02", "2026-10-01"))


def test_cross_midnight_rule_allowed(db):
    f = insert_fact(db)
    db.execute("INSERT INTO hours_rule VALUES (?,?,?,?)", (f, "[5]", "22:00", "02:00"))


def test_hours_rule_no_delete(db):
    f = insert_fact(db)
    db.execute("INSERT INTO hours_rule VALUES (?,?,?,?)", (f, "[5]", "22:00", "02:00"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM hours_rule")


def test_bump_data_version(db):
    assert bump_data_version(db) == 1 and data_version(db) == 1


def test_readonly_connection_cannot_write(db, tmp_path):
    db.commit()
    ro = connect(tmp_path / "facts.db", readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO meta VALUES ('x','y')")
