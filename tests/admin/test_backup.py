"""`nknu backup`: an online copy of facts.db (sqlite3 backup API) into the backup directory, newest 14 kept."""

import re
import sqlite3

from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.core.writer import apply_fact
from tests.factories import HOLIDAY, T0, T1, WR_TERM, make_db

BACKUP_NAME = re.compile(r"facts-\d{8}-\d{6}\.db")


def _facts_db(tmp_path):
    """A facts.db with history: the weight room's term table twice (one superseded version) and a holiday."""
    data = tmp_path / "data"
    conn = make_db(data)
    apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T0)
    apply_fact(conn, WR_TERM, adapter="pe_open_hours", review="verified", now=T1)
    apply_fact(conn, HOLIDAY, adapter="aca_calendar_ics", review="auto_ok", now=T0)
    conn.commit()
    n = conn.execute("SELECT count(*) FROM fact").fetchone()[0]
    conn.close()
    return data, n


def _backup(data, *args, env=None):
    return CliRunner().invoke(app, ["backup", *args], env={"NKNU_DATA_DIR": str(data), **(env or {})})


def _open_ro(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def test_backup_roundtrip(tmp_path):
    data, n = _facts_db(tmp_path)
    assert n == 3
    r = _backup(data, "--to", str(tmp_path / "bak"))
    assert r.exit_code == 0, r.output
    files = list((tmp_path / "bak").iterdir())
    assert len(files) == 1 and BACKUP_NAME.fullmatch(files[0].name), files  # no temp, -wal or -shm file left behind
    copy = _open_ro(files[0])
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT count(*) FROM fact").fetchone()[0] == n
    assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # one self-contained file
    copy.close()


def test_keeps_newest_14_and_removes_only_its_own_files(tmp_path):
    data, _ = _facts_db(tmp_path)
    bak = tmp_path / "bak"
    bak.mkdir()
    old = [f"facts-2020{m:02d}01-033000.db" for m in range(1, 13)] + [f"facts-2021{m:02d}01-033000.db" for m in (1, 2, 3)]
    others = ["facts-manual.db", "facts-20200101-033000.db.bak", "facts-2020011-033000.db", "facts-20200101-0330.db",
              "serve-20200101-033000.db", "notes.txt"]
    for name in old + others:
        (bak / name).write_bytes(b"not a database")
    (bak / "facts-20190101-033000.db").mkdir()  # a directory with the pattern's name is not one of its files
    r = _backup(data, "--to", str(bak))
    assert r.exit_code == 0, r.output
    names = {p.name for p in bak.iterdir()}
    ours = sorted(n for n in names if BACKUP_NAME.fullmatch(n) and (bak / n).is_file())
    assert len(ours) == 14
    assert ours[:13] == sorted(old)[-13:]  # the two oldest went; the new one sorts last
    assert ours[13] not in old
    assert set(others) <= names and "facts-20190101-033000.db" in names


def test_missing_facts_db_fails_and_removes_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    bak = tmp_path / "bak"
    bak.mkdir()
    old = [f"facts-2020{m:02d}01-033000.db" for m in range(1, 13)] + [f"facts-2021{m:02d}01-033000.db" for m in (1, 2, 3)]
    for name in old:
        (bak / name).write_bytes(b"x")
    r = _backup(data, "--to", str(bak))
    assert r.exit_code == 1 and "facts.db" in r.output, r.output
    assert not (data / "facts.db").exists()  # never creates an empty facts.db
    assert sorted(p.name for p in bak.iterdir()) == sorted(old)


def test_target_defaults_to_backup_dir_setting(tmp_path):
    data, n = _facts_db(tmp_path)
    r = _backup(data, env={"NKNU_BACKUP_DIR": str(tmp_path / "nas")})
    assert r.exit_code == 0, r.output
    (copy,) = (tmp_path / "nas").iterdir()
    assert _open_ro(copy).execute("SELECT count(*) FROM fact").fetchone()[0] == n


def test_no_target_is_refused(tmp_path, monkeypatch):
    data, _ = _facts_db(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    for args in ([], ["--to", ""]):  # systemd expands an unset ${NKNU_BACKUP_DIR} to an empty argument
        r = CliRunner().invoke(app, ["backup", *args], env={"NKNU_DATA_DIR": str(data), "NKNU_BACKUP_DIR": ""})
        assert r.exit_code == 2 and "NKNU_BACKUP_DIR" in r.output, r.output
    assert list(cwd.iterdir()) == []
    assert [p.name for p in data.iterdir() if BACKUP_NAME.fullmatch(p.name)] == []
