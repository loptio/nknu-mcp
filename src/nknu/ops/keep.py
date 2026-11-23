"""What the panel's background work found, kept in its own SQLite file (ops.db, beside the nights' record) so that a
restart shows it at once (2026-11-22 night; owner, of results held only in the process's memory: 「为什么是存在面板里面？
不应该是在资料库吗？」, then 「改，然后你看还有哪些偷懒的设计也应该改的就一起改了」).

Until then a restart of 檢視台 emptied three pages for minutes while their threads worked again: 內容's numbers of
every site (overview.State: a few minutes over the library), 上次換庫的變動 (changes.Changes: both libraries read,
about a gigabyte each) and every step of the pipeline (pipeline.Pipeline). Each now puts what it found here with the
key of what it was found from (the files' identity and time, the day), and reads it back on start: the same key, the
same result, nothing computed again; another key, computed as before. A reading of the pipeline's steps is shown as
the one before the restart (said so) only until the first new reading ends.

Never a reason to fail: a file that cannot be opened or written is logged and the work goes on as before, in memory.
网页核查's per-page results are kept by nknu.ops.sweep in tables of its own. 【Claude】
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = ("CREATE TABLE IF NOT EXISTS kept (name TEXT PRIMARY KEY, key TEXT NOT NULL, at TEXT NOT NULL, "
          "value TEXT NOT NULL)")


def key_text(key: object) -> str:
    """A key as kept: JSON of it, whatever it holds turned to text (tuples of a file's identity, dates)."""
    return json.dumps(key, default=str, sort_keys=True, ensure_ascii=False)


class Kept:
    """name → (key, value) in ops.db's table kept. path None: nothing kept (get gives None, put does nothing)."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute(SCHEMA)
        return conn

    def get(self, name: str, key: object) -> object | None:
        """The value kept under name when it was kept for this key, else None."""
        if self.path is None or not self.path.exists():
            return None
        try:
            with self._lock:
                conn = self._connect()
                try:
                    row = conn.execute("SELECT key, value FROM kept WHERE name = ?", (name,)).fetchone()
                finally:
                    conn.close()
        except sqlite3.Error:
            log.exception("留存的结果读不了：%s", name)
            return None
        if row is None or row[0] != key_text(key):
            return None
        try:
            return json.loads(row[1])
        except ValueError:
            return None

    def put(self, name: str, key: object, value: object) -> None:
        """Keep value under name for key (the one before is replaced)."""
        if self.path is None:
            return
        try:
            text = json.dumps(value, default=str, ensure_ascii=False)
            with self._lock:
                conn = self._connect()
                try:
                    with conn:
                        conn.execute("INSERT OR REPLACE INTO kept (name, key, at, value) VALUES (?, ?, ?, ?)",
                                     (name, key_text(key), datetime.now().astimezone().isoformat(timespec="seconds"),
                                      text))
                finally:
                    conn.close()
        except (sqlite3.Error, TypeError, ValueError):
            log.exception("留存结果写不了：%s", name)
