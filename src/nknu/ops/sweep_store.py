"""網頁核查's sweep kept on disk (nknu.ops.sweep; the owner 2026-11-22: 「改」, after every restart of the panel checked
the whole archive again for about 17 minutes and every change of the ledger did the same): the panel's own writable
SQLite file, the one the nights' record is in (ops.db, nknu.ops.history: on the lab PC the Docker volume nknu-ops at
/ops/ops.db; every other mount of the panel is read-only). Bytes in, bytes out: what the bytes are is sweep.py's.

What is kept, in tables of their own beside history's (each named sweep_…):

  sweep_runs     one row a sweep (Sweep.gen): when it began and ended (NULL: the panel stopped before every page was
                 checked), the ledger it read (LedgerCache.stamped), the rules it checked by (sweep.RULES), its pages
  sweep_listing  the last sweep's pages, sites and the pages' names (one blob): what the pages draw before the ledger
                 is read again
  sweep_pages    one row a listed page: what its last check left for the lists (sweep.Found), the sweep that made it
                 and when
  sweep_reads    one row a stored page read (pagecheck.Read): the record it was read from (pagecheck.record_key) and
                 the rules that read it (sweep.READ_RULES); a page whose record and rules are the same is not read
                 again, only looked up in the ledger
  sweep_nights   one row a night (nightly.night_of): the school-wide totals of the last whole sweep that night

Nothing is written when no path is given or the path cannot be written (enabled False): the sweep then keeps its
results in memory, as it did until 2026-11-22. 【Claude】 the tables and their names."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path

log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS sweep_runs (
  run INTEGER PRIMARY KEY,   -- Sweep.gen
  started REAL NOT NULL,     -- seconds since 1970
  finished REAL,             -- NULL: stopped before every listed page was checked
  stamp TEXT,                -- the ledger's files when it was read (JSON of LedgerCache.stamped)
  rules TEXT NOT NULL,       -- sweep.RULES of the code that checked
  pages INTEGER
);
CREATE TABLE IF NOT EXISTS sweep_listing (
  run INTEGER PRIMARY KEY,   -- the sweep that read it (only the last is kept)
  data BLOB NOT NULL         -- sweep.pack_listing
);
CREATE TABLE IF NOT EXISTS sweep_pages (
  url TEXT PRIMARY KEY,
  run INTEGER NOT NULL,      -- the sweep that checked it
  at REAL NOT NULL,          -- when, seconds since 1970
  found BLOB NOT NULL        -- sweep.pack_found
);
CREATE TABLE IF NOT EXISTS sweep_reads (
  url TEXT PRIMARY KEY,
  rec TEXT NOT NULL,         -- pagecheck.record_key of the record read
  rules TEXT NOT NULL,       -- sweep.READ_RULES of the code that read it
  steps BLOB NOT NULL        -- sweep.pack_steps
);
CREATE TABLE IF NOT EXISTS sweep_nights (
  night TEXT PRIMARY KEY,    -- YYYY-MM-DD (nightly.night_of)
  at TEXT NOT NULL,          -- when its sweep ended, ISO, Taipei
  run INTEGER,
  pages INTEGER, checked INTEGER,
  cls_fix INTEGER, cls_check INTEGER, cls_school INTEGER,         -- addresses (a button once per site) per class
  pages_fix INTEGER, pages_check INTEGER, pages_school INTEGER,   -- on how many pages
  menu INTEGER,              -- the menus' addresses not got, each once
  nofetch INTEGER            -- pages not got themselves
);
"""
NIGHT_COLS = ("night", "at", "run", "pages", "checked", "cls_fix", "cls_check", "cls_school", "pages_fix",
              "pages_check", "pages_school", "menu", "nofetch")
RUNS_KEPT = 60  # rows of sweep_runs kept 【Claude】
NIGHTS_KEPT = 120  # nights kept (history.PROBLEM_KEEP's span) 【Claude】
BATCH = 500  # addresses per IN (…) 【Claude】


def _inside(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return False
    try:
        path.resolve().relative_to(Path(folder).resolve())
    except (ValueError, OSError):
        return False
    return True


class SweepStore:
    """The sweep's tables in one SQLite file. enabled is False (error says why, in the page's words) when no path was
    given, the path is inside a folder this service must never write (forbidden), or the file cannot be made."""

    def __init__(self, path: Path | None, *, forbidden: tuple[Path | None, ...] = ()) -> None:
        self.path = Path(path) if path else None
        self.error = ""
        self._lock = threading.Lock()
        if self.path is None:
            self.error = "沒有設定紀錄檔（--ops-db）"
            return
        if any(_inside(self.path, f) for f in forbidden):
            self.error = f"紀錄檔不能放在資料或爬蟲存檔的資料夾裡（{self.path}）"
            self.path = None
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as conn:
                conn.executescript(DDL)
        except (OSError, sqlite3.Error) as exc:
            log.warning("网页核查：纪录档写不进去（%s）：%s", self.path, exc)
            self.error = f"紀錄檔寫不進去（{type(exc).__name__}）"
            self.path = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @contextlib.contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One connection: committed when the block ends well, rolled back when it does not, closed either way."""
        assert self.path is not None
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _use(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db() as conn:
            yield conn

    # ---------------------------------------------------------------- the sweep as it was left

    def last_run(self) -> dict | None:
        """The last sweep's row ({run, started, finished, stamp, rules, pages}), None when none was kept."""
        if not self.enabled:
            return None
        with self._use() as conn:
            row = conn.execute("SELECT run, started, finished, stamp, rules, pages FROM sweep_runs "
                               "ORDER BY run DESC LIMIT 1").fetchone()
        return dict(zip(("run", "started", "finished", "stamp", "rules", "pages"), row, strict=True)) if row else None

    def last_finished(self) -> dict | None:
        """The last sweep that checked every page ({run, started, finished, stamp, rules, pages}), or None."""
        if not self.enabled:
            return None
        with self._use() as conn:
            row = conn.execute("SELECT run, started, finished, stamp, rules, pages FROM sweep_runs "
                               "WHERE finished IS NOT NULL ORDER BY run DESC LIMIT 1").fetchone()
        return dict(zip(("run", "started", "finished", "stamp", "rules", "pages"), row, strict=True)) if row else None

    def listing(self) -> tuple[int, bytes] | None:
        if not self.enabled:
            return None
        with self._use() as conn:
            row = conn.execute("SELECT run, data FROM sweep_listing ORDER BY run DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else None

    def pages(self) -> list[tuple[str, int, float, bytes]]:
        """Every kept result: (url, run, at, found)."""
        if not self.enabled:
            return []
        with self._use() as conn:
            return conn.execute("SELECT url, run, at, found FROM sweep_pages").fetchall()

    # ---------------------------------------------------------------- a sweep running

    def begin(self, run: int, started: float, stamp: str, rules: str, pages: int, listing: bytes) -> None:
        """A sweep has read the ledger: its row, and its listing in place of the last one."""
        with self._use() as conn:
            conn.execute("INSERT OR REPLACE INTO sweep_runs(run, started, finished, stamp, rules, pages) "
                         "VALUES (?, ?, NULL, ?, ?, ?)", (run, started, stamp, rules, pages))
            conn.execute("DELETE FROM sweep_listing")
            conn.execute("INSERT INTO sweep_listing(run, data) VALUES (?, ?)", (run, listing))
            conn.execute("DELETE FROM sweep_runs WHERE run <= ?", (run - RUNS_KEPT,))

    def reads(self, urls: Iterable[str], rules: str) -> dict[str, tuple[str, bytes]]:
        """The kept reads of these addresses made by these rules: {url: (record key, steps)}."""
        urls = list(urls)
        out: dict[str, tuple[str, bytes]] = {}
        with self._use() as conn:
            for i in range(0, len(urls), BATCH):
                part = urls[i:i + BATCH]
                marks = ",".join("?" * len(part))
                for url, rec, steps in conn.execute(
                        f"SELECT url, rec, steps FROM sweep_reads WHERE rules = ? AND url IN ({marks})", (rules, *part)):
                    out[url] = (rec, steps)
        return out

    def put(self, pages: list[tuple[str, int, float, bytes]], reads: list[tuple[str, str, str, bytes]]) -> None:
        """Results (url, run, at, found) and reads (url, record key, rules, steps), in one transaction."""
        if not pages and not reads:
            return
        with self._use() as conn:
            conn.executemany("INSERT OR REPLACE INTO sweep_pages(url, run, at, found) VALUES (?, ?, ?, ?)", pages)
            conn.executemany("INSERT OR REPLACE INTO sweep_reads(url, rec, rules, steps) VALUES (?, ?, ?, ?)", reads)

    def finish(self, run: int, finished: float, keep: set[str]) -> None:
        """A sweep checked every page: its end, and the results and reads of addresses it no longer lists dropped."""
        with self._use() as conn:
            conn.execute("UPDATE sweep_runs SET finished = ? WHERE run = ?", (finished, run))
            for table in ("sweep_pages", "sweep_reads"):
                gone = [u for (u,) in conn.execute(f"SELECT url FROM {table}") if u not in keep]
                for i in range(0, len(gone), BATCH):
                    part = gone[i:i + BATCH]
                    conn.execute(f"DELETE FROM {table} WHERE url IN ({','.join('?' * len(part))})", part)

    def forget_reads(self, rules: str) -> int:
        """The reads other rules made (another code reads a page otherwise): dropped. How many."""
        with self._use() as conn:
            return conn.execute("DELETE FROM sweep_reads WHERE rules <> ?", (rules,)).rowcount

    # ---------------------------------------------------------------- the nights

    def put_night(self, rec: dict) -> None:
        with self._use() as conn:
            conn.execute(f"INSERT OR REPLACE INTO sweep_nights({', '.join(NIGHT_COLS)}) "
                         f"VALUES ({', '.join('?' * len(NIGHT_COLS))})", tuple(rec.get(k) for k in NIGHT_COLS))
            conn.execute("DELETE FROM sweep_nights WHERE night NOT IN "
                         "(SELECT night FROM sweep_nights ORDER BY night DESC LIMIT ?)", (NIGHTS_KEPT,))

    def night(self, night: str) -> dict | None:
        if not self.enabled:
            return None
        with self._use() as conn:
            row = conn.execute(f"SELECT {', '.join(NIGHT_COLS)} FROM sweep_nights WHERE night = ?", (night,)).fetchone()
        return dict(zip(NIGHT_COLS, row, strict=True)) if row else None

    def nights(self, first: str, last: str) -> list[dict]:
        """The nights from first to last (YYYY-MM-DD, both kept), oldest first."""
        if not self.enabled:
            return []
        with self._use() as conn:
            rows = conn.execute(f"SELECT {', '.join(NIGHT_COLS)} FROM sweep_nights WHERE night BETWEEN ? AND ? "
                                "ORDER BY night", (first, last)).fetchall()
        return [dict(zip(NIGHT_COLS, r, strict=True)) for r in rows]
