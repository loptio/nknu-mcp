"""facts.db connection, schema migration and the data_version counter that triggers rebuilds."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = "1"


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if exists:
        return
    conn.executescript(resources.files("nknu.core").joinpath("schema_facts.sql").read_text())
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [("schema_version", SCHEMA_VERSION), ("data_version", "0")])
    conn.commit()


def data_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()[0])


def bump_data_version(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='data_version'")
    return data_version(conn)
