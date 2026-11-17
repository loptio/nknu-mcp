"""每晚紀錄: one record a night, kept by 檢視台 itself, so 今晚 and a site's page can draw the last fourteen nights (spec
docs/superpowers/specs/2026-11-17-panels-v3-questions.md 一 and 三.3; owner 11-17: 「你再按照安装的插件和发现的问题再优化
dashboard吧」; one of the five causes found that day: there was no record of the nights to draw).

The store is a small SQLite file the ops service may write (on the lab PC the Docker volume nknu-ops, mounted at /ops:
--ops-db /ops/ops.db). Nothing else is ever written: never /work, never /crawl (a path under the library's root or the
crawler's archive is refused). When the path is not given or cannot be written, the pages still work and say the
nights are not being recorded.

A night is recorded once its chain has finished or failed (nknu.ops.nightly.night_state: ok, warn, bad, missed), by a
background thread every RECORD_EVERY seconds, and recorded again (the same night replaces itself: an upsert) while it
is among the last NIGHTS nights, so a run by hand the morning after or a comparison that finished later reaches it.
What a record holds, and where each part comes from:

  the night       its state and word, when the chain began and ended (nightly.one_night's five parts)
  each step       爬取, 導出, 轉文字, 建庫, 換庫: state, word, what it said, who fixes it, start and end
  totals          new, changed, left, unexplained, look: from the last swap's comparison (nknu.ops.changes) when it is
                  this night's (counted_by 'changes'); else from the nightly ingest's history line (its departures and
                  the library before and after: new = after − before + left; counted_by 'ingest'); a night that
                  swapped nothing changed nothing (all 0); otherwise unknown (NULL)
  documents       in the library the night left in use: read from the library only for the latest finished night
                  (and only when that library was not swapped in after it); else the ingest's own count
  per site        documents and undated (公告、網頁、附件 without a date, as the site page counts them) for the latest
                  finished night; new, left, unexplained from the comparison (exact: site_counts 1) or the ingest's
                  unexplained per site

A value a later reading does not have never wipes one an earlier reading wrote (a NULL keeps what is stored), and a
comparison's counts are never replaced by the ingest's coarser ones. The first tick after start backfills every night
since the chain went live (at most nightly.PICKABLE days) from what exists: the ingest's history, the crawler's
update.log, and the last swap's comparison. The rules and the waits are 【Claude】 (redesign 2026-11-17,
research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from nknu.ops import nightly
from nknu.ops import pipeline as pl
from nknu.ops.pipeline import Where

log = logging.getLogger(__name__)

SCHEMA = "1"
RECORD_EVERY = 300.0  # seconds between two looks at the nights (the pipeline's own reading interval) 【Claude】
SHOWN = 14  # nights the pages draw (spec 三.3, 四.5)
FINAL = ("ok", "warn", "bad", "missed")  # a night in one of these has finished (or failed): it is recorded
COUNTS = ("new", "changed", "left", "unexplained", "look")
SITE_COUNTS = ("new", "left", "unexplained")
DATED = ("notice", "page", "attachment")  # the kinds a date is looked for on (site_page.DATED)
SWAPPED = "swapped"
CALM = ("nothing_new", "held", "failed", "paused", "deferred", "busy")  # the library in use did not change

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nights (
  night TEXT PRIMARY KEY,      -- YYYY-MM-DD: the day twelve hours before the chain (nightly.night_of)
  state TEXT NOT NULL,         -- ok, warn, bad, missed (nightly.night_state)
  word TEXT NOT NULL,          -- its word on the pages (正常, 停了, 出錯, 沒跑)
  started_at TEXT,             -- the first part's start, ISO, Taipei
  ended_at TEXT,               -- the last part's end
  swapped INTEGER,             -- 1: the library was swapped that night; 0: not; NULL: cannot tell
  counted_by TEXT,             -- where the totals are from: changes, ingest; NULL: not counted
  site_counts INTEGER NOT NULL DEFAULT 0,  -- 1: the per-site new/left/unexplained are exact (absent = 0)
  new INTEGER, changed INTEGER, left INTEGER, unexplained INTEGER, look INTEGER,
  unexplained_by TEXT,         -- whose count unexplained is: build (its own check), changes, ingest
  unexplained_other INTEGER,   -- the panels' comparison's count, when the build's differs from it
  docs INTEGER,                -- documents in the library the night left in use
  source TEXT NOT NULL,        -- backfill (written by the first look after start) or night
  digest TEXT NOT NULL,        -- of the record's content: an unchanged record is not written again
  written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
  night TEXT NOT NULL, step TEXT NOT NULL,  -- crawl, export, totext, build, swap (nightly.CELLS)
  name TEXT, state TEXT, word TEXT, sev TEXT, say TEXT, fixer TEXT, start TEXT, end TEXT,
  PRIMARY KEY (night, step)
);
CREATE TABLE IF NOT EXISTS sites (
  night TEXT NOT NULL, site TEXT NOT NULL, name TEXT,
  docs INTEGER, undated INTEGER, new INTEGER, left INTEGER, unexplained INTEGER,
  PRIMARY KEY (night, site)
);
"""
NIGHT_COLS = ("night", "state", "word", "started_at", "ended_at", "swapped", "counted_by", "site_counts", *COUNTS,
              "unexplained_by", "unexplained_other", "docs", "source", "digest", "written_at")
FOLLOW = (*COUNTS, "unexplained_by", "unexplained_other")  # stored or replaced together with the totals
ADDED = {"unexplained_by": "TEXT", "unexplained_other": "INTEGER"}  # columns a store made before 11-17 evening lacks
STEP_COLS = ("step", "name", "state", "word", "sev", "say", "fixer", "start", "end")
SITE_COLS = ("site", "name", "docs", "undated", *SITE_COUNTS)


def _inside(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return False
    try:
        path.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return False
    return True


class Store:
    """The records, in one SQLite file. enabled is False (and error says why, in the page's words) when no path was
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
                have = {r[1] for r in conn.execute("PRAGMA table_info(nights)")}
                for col, kind in ADDED.items():
                    if col not in have:
                        conn.execute(f"ALTER TABLE nights ADD COLUMN {col} {kind}")
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA,))
        except (OSError, sqlite3.Error) as exc:
            log.warning("每晚纪录：纪录档写不进去（%s）：%s", self.path, exc)
            self.error = f"紀錄檔寫不進去（{type(exc).__name__}）"
            self.path = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @contextlib.contextmanager
    def _db(self):
        """One connection: committed when the block ends well, rolled back when it does not, closed either way."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---------------------------------------------------------------- reading

    def _load(self, conn: sqlite3.Connection, night: str) -> dict | None:
        row = conn.execute("SELECT * FROM nights WHERE night = ?", (night,)).fetchone()
        if row is None:
            return None
        rec = dict(row)
        rec["steps"] = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(STEP_COLS)} FROM steps WHERE night = ? ORDER BY rowid", (night,))]
        rec["sites"] = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(SITE_COLS)} FROM sites WHERE night = ? ORDER BY site", (night,))]
        return rec

    def get(self, night: str) -> dict | None:
        if not self.enabled:
            return None
        with self._lock, self._db() as conn:
            return self._load(conn, night)

    def nights(self, first: str, last: str) -> list[dict]:
        """The records from night first to night last (both YYYY-MM-DD), oldest first, each with its steps (not its
        sites)."""
        if not self.enabled:
            return []
        with self._lock, self._db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM nights WHERE night BETWEEN ? AND ? ORDER BY night", (first, last))]
            for rec in rows:
                rec["steps"] = [dict(r) for r in conn.execute(
                    f"SELECT {', '.join(STEP_COLS)} FROM steps WHERE night = ? ORDER BY rowid", (rec["night"],))]
        return rows

    def changed_sites(self, first: str, last: str) -> dict[str, list[dict]]:
        """{night: its sites with something new, left or unexplained}, the nights first to last."""
        if not self.enabled:
            return {}
        out: dict[str, list[dict]] = {}
        with self._lock, self._db() as conn:
            for r in conn.execute(
                    "SELECT night, site, name, new, left, unexplained FROM sites WHERE night BETWEEN ? AND ? AND"
                    " (coalesce(new, 0) + coalesce(left, 0) + coalesce(unexplained, 0)) > 0 ORDER BY night, site",
                    (first, last)):
                out.setdefault(r["night"], []).append({k: r[k] for k in ("site", "name", *SITE_COUNTS)})
        return out

    def site_nights(self, site: str, first: str, last: str) -> list[dict]:
        """One site's nights first to last, oldest first: {night, state, counted, docs, undated, new, left,
        unexplained}; counts are None where the night has none for it (not counted, or not per site), 0 where the
        night's per-site counts are exact and the site is absent."""
        if not self.enabled:
            return []
        with self._lock, self._db() as conn:
            rows = conn.execute(
                "SELECT n.night, n.state, n.word, n.counted_by, n.site_counts, s.docs, s.undated, s.new, s.left,"
                " s.unexplained FROM nights n LEFT JOIN sites s ON s.night = n.night AND s.site = ?"
                " WHERE n.night BETWEEN ? AND ? ORDER BY n.night", (site, first, last)).fetchall()
        out = []
        for r in rows:
            exact = bool(r["site_counts"])
            got = {k: (r[k] if r[k] is not None else (0 if exact else None)) for k in SITE_COUNTS}
            out.append({"night": r["night"], "state": r["state"], "word": r["word"], "counted": exact,
                        "docs": r["docs"], "undated": r["undated"], **got})
        return out

    def count(self) -> int:
        if not self.enabled:
            return 0
        with self._lock, self._db() as conn:
            return int(conn.execute("SELECT count(*) FROM nights").fetchone()[0])

    # ---------------------------------------------------------------- writing

    def put(self, rec: dict, *, at: datetime | None = None) -> bool:
        """Write one night's record, merged with what is stored for it (module docstring); True when anything
        changed. The same record put twice writes once."""
        if not self.enabled:
            return False
        with self._lock, self._db() as conn:
            old = self._load(conn, rec["night"])
            merged = merge(old, rec)
            body = {k: v for k, v in merged.items() if k not in ("digest", "written_at")}
            digest = hashlib.blake2b(json.dumps(body, sort_keys=True, ensure_ascii=False).encode(),
                                     digest_size=8).hexdigest()
            if old is not None and old.get("digest") == digest:
                return False
            merged["digest"] = digest
            merged["written_at"] = (at or pl.now_tpe()).isoformat(timespec="seconds")
            night = merged["night"]
            conn.execute("DELETE FROM steps WHERE night = ?", (night,))
            conn.execute("DELETE FROM sites WHERE night = ?", (night,))
            conn.execute(f"INSERT OR REPLACE INTO nights ({', '.join(NIGHT_COLS)}) VALUES "
                         f"({', '.join('?' * len(NIGHT_COLS))})", tuple(merged.get(k) for k in NIGHT_COLS))
            conn.executemany(f"INSERT INTO steps (night, {', '.join(STEP_COLS)}) VALUES "
                             f"(?, {', '.join('?' * len(STEP_COLS))})",
                             [(night, *(s.get(k) for k in STEP_COLS)) for s in merged["steps"]])
            conn.executemany(f"INSERT INTO sites (night, {', '.join(SITE_COLS)}) VALUES "
                             f"(?, {', '.join('?' * len(SITE_COLS))})",
                             [(night, *(s.get(k) for k in SITE_COLS)) for s in merged["sites"]])
            return True


RANK = {None: 0, "ingest": 1, "changes": 2}  # whose totals win: a comparison's over the ingest's own line


def merge(old: dict | None, new: dict) -> dict:
    """A night's record as it is to be stored: new's values, where new has none old's; the totals (and the per-site
    counts with them) from whichever source counted them better (RANK); old's source (backfill stays backfill)."""
    if old is None:
        old = {"steps": [], "sites": []}
    out = {}
    for k in ("night", "state", "word", "started_at", "ended_at", "swapped", "docs"):
        out[k] = new.get(k) if new.get(k) is not None else old.get(k)
    out["source"] = old.get("source") or new.get("source")
    take = bool(new.get("counted_by")) and RANK.get(new.get("counted_by"), 0) >= RANK.get(old.get("counted_by"), 0)
    src = new if take else old
    out["counted_by"] = src.get("counted_by")
    for k in FOLLOW:
        out[k] = src.get(k)
    out["site_counts"] = int(bool(src.get("site_counts")))
    out["steps"] = list(new.get("steps") or old.get("steps") or [])
    sites: dict[str, dict] = {}
    for s in old.get("sites") or []:
        sites[s["site"]] = dict(s)
    for s in new.get("sites") or []:
        row = sites.setdefault(s["site"], {"site": s["site"]})
        for k in ("name", "docs", "undated"):
            if s.get(k) is not None:
                row[k] = s[k]
    if take:  # the counts per site follow the totals: the new reading's, none kept from the old
        mine = {s["site"]: s for s in new.get("sites") or []}
        for site, row in sites.items():
            for k in SITE_COUNTS:
                row[k] = (mine.get(site) or {}).get(k)
    out["sites"] = sorted(({k: row.get(k) for k in SITE_COLS} for row in sites.values()
                           if any(row.get(k) is not None for k in ("docs", "undated", *SITE_COUNTS))),
                          key=lambda s: s["site"])
    return out


# ------------------------------------------------------------------------------------------------ one night


def hours_of(night: date, at: datetime | None) -> float | None:
    """A time as hours after the night's midnight (00:00 of the morning after: night 11-16's 01:30 is 1.5)."""
    if at is None:
        return None
    return (at - nightly.at_night(night, nightly.dtime(0, 0))).total_seconds() / 3600


def _run_counts(run: dict | None) -> tuple[dict, int | None, list[dict]]:
    """What the nightly ingest's own line says of a night: (totals, documents after it, per-site rows): a swap with
    its departures counted (new = after − before + left); a run that swapped nothing: zeros."""
    if run is None:
        return {}, None, []
    outcome = str(run.get("outcome") or "")
    lib = run.get("library") if isinstance(run.get("library"), dict) else {}
    before = lib.get("before") if isinstance(lib.get("before"), dict) else {}
    after = lib.get("after") if isinstance(lib.get("after"), dict) else {}
    if outcome in CALM:
        docs = before.get("docs") if isinstance(before.get("docs"), int) else None
        return {k: 0 for k in COUNTS}, docs, []
    if outcome != SWAPPED:
        return {}, None, []
    docs = after.get("docs") if isinstance(after.get("docs"), int) else None
    left = run.get("departures") if isinstance(run.get("departures"), dict) else {}
    if not left.get("checked") or not isinstance(left.get("left"), int):
        return {}, docs, []
    totals = {"left": left["left"], "unexplained": int(left.get("unexplained_total") or 0)}
    if isinstance(before.get("docs"), int) and docs is not None:
        totals["new"] = docs - before["docs"] + left["left"]
    sites: dict[str, dict] = {}
    for site, n in (left.get("by_site") or {}).items():
        if site:
            sites.setdefault(site, {"site": site})["unexplained"] = int(n)
    for site, n in (after.get("sites") or {}).items():
        if site and isinstance(n, int):
            sites.setdefault(site, {"site": site})["docs"] = n
    return totals, docs, list(sites.values())


def night_record(r: nightly.Reading, night: date, *, source: str, changes: dict | None = None,
                 library: dict | None = None) -> dict | None:
    """The record of one night (module docstring), None while it has not finished. changes: the last swap's
    comparison (used when it is this night's); library: what the library in use holds ({'docs', 'sites': {site:
    [docs, undated]}, 'names': {site: name}}), given only when this night left it in use."""
    n = nightly.one_night(r, night)
    if n["state"] not in FINAL:
        return None
    cells = n["cells"]
    steps = [{"step": c["key"], "name": c["name"], "state": c["state"], "word": c["word"], "sev": c["sev"] or None,
              "say": c["say"] or None, "fixer": c["fixer"] or None, "start": c["since"] or None,
              "end": c["until"] or None} for c in cells]
    starts = [c["since"] for c in cells if c["since"]]
    ends = [c["until"] for c in cells if c["until"]]
    crawl = r.crawler_run(night)
    run, rolled, _others, _first = nightly.chain_ingest(r, night, crawl["start"] if crawl else None)
    swapped = None
    if run is not None:
        swapped = int(run.get("outcome") == SWAPPED and rolled is None)
    elif n["state"] == "missed" or cells[0]["state"] == nightly.STOP:
        swapped = 0
    rec = {"night": night.isoformat(), "state": n["state"], "word": n["word"], "started_at": min(starts, default=None),
           "ended_at": max(ends, default=None), "swapped": swapped, "counted_by": None, "site_counts": 0,
           **{k: None for k in FOLLOW}, "docs": None, "source": source, "steps": steps, "sites": []}
    sites: dict[str, dict] = {}
    if swapped == 0:  # the library in use did not change: nothing came in or left
        rec.update(counted_by="ingest", site_counts=1, **{k: 0 for k in COUNTS})
    totals, docs, per = _run_counts(run if rolled is None else None)
    if swapped == 1 and totals:
        rec.update(counted_by="ingest", **{k: totals.get(k) for k in COUNTS})
        for s in per:
            sites.setdefault(s["site"], {"site": s["site"]}).update(s)
    if docs is not None:
        rec["docs"] = docs
    if (changes and changes.get("state") == "ready" and changes.get("night") == rec["night"] and swapped != 0):
        t = changes.get("totals") or {}
        rec.update(counted_by="changes", site_counts=1, **{k: int(t.get(k) or 0) for k in COUNTS})
        for s in sites.values():  # the ingest's per-site counts give way to the comparison's
            for k in SITE_COUNTS:
                s.pop(k, None)
        for s in changes.get("sites") or []:
            row = sites.setdefault(s["site"], {"site": s["site"]})
            row.update(name=s.get("name"), **{k: int(s.get(k) or 0) for k in SITE_COUNTS})
    # the build's own check of what left (nightly_ingest's departures, 11-17): its count of the unexplained, total and
    # per site, wins over the panels' comparison and the ingest line's; new, changed and left stay theirs (the
    # coordinator's brief, 11-17); the comparison's own count is kept when it differs, for the pages to say so
    dep = nightly.departures_of(run if rolled is None else None, r.swap)
    if dep is not None and rec["counted_by"] is not None and swapped == 1:  # a held library left nothing in use
        other = rec["unexplained"]
        rec.update(unexplained=dep["unexplained"], unexplained_by="build",
                   unexplained_other=other if rec["counted_by"] == "changes" and other != dep["unexplained"] else None)
        for s in sites.values():
            if rec["site_counts"] or "unexplained" in s:
                s["unexplained"] = 0
        for site, count in dep["by_site"].items():
            sites.setdefault(site, {"site": site})["unexplained"] = count
    elif rec["counted_by"] is not None:
        rec["unexplained_by"] = rec["counted_by"]
    if library is not None:
        if library.get("docs") is not None:
            rec["docs"] = library["docs"]
        names = library.get("names") or {}
        for site, (count, undated) in (library.get("sites") or {}).items():
            row = sites.setdefault(site, {"site": site})
            row.update(docs=count, undated=undated)
            if names.get(site) and not row.get("name"):
                row["name"] = names[site]
    rec["sites"] = list(sites.values())
    return rec


def library_now(where: Where, kinds: dict | None) -> dict:
    """What the library in use holds: its documents, per site [documents, undated] (from the site page's per-kind
    counts, None while they are being made), its sites' names, and the night it was swapped in."""
    facts = pl.library_facts(where.library)
    names = {s.get("site"): s.get("name") or "" for s in facts["sites"] if s.get("site")}
    per = None
    if kinds is not None:
        per = {site: [sum(v[0] for v in by.values()), sum(by.get(k, [0, 0])[1] for k in DATED)]
               for site, by in kinds.items() if site}
    docs = sum(v[0] for v in per.values()) if per else (facts["docs"] or None)
    swapped_at = pl.when_of(facts.get("swapped_at") or facts.get("built_at"))
    return {"docs": docs, "sites": per or {}, "names": names,
            "night": nightly.night_of(swapped_at) if swapped_at else None}


class Recorder:
    """Looks at the nights every `every` seconds in a background thread and writes the finished ones (Store.put).
    changes and kinds: the app's Changes and site_page.Kinds (their snapshots are made in their own threads; a look
    that finds them not ready writes what it has and the next look adds the rest)."""

    def __init__(self, store: Store, where: Where, *, changes=None, kinds=None, every: float = RECORD_EVERY) -> None:
        self.store, self.where, self.changes, self.kinds, self.every = store, where, changes, kinds, every
        self.backfilled = False
        self.last: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()

    def tick(self, now: datetime | None = None) -> int:
        """One look: the nights to write (every night since the chain went live on the first look, the last
        NIGHTS after it), each written when it has finished; returns how many records changed."""
        if not self.store.enabled:
            return 0
        with self._tick_lock:
            now = now or pl.now_tpe()
            r = nightly.Reading(self.where, now)
            last = nightly.night_of(now)
            if self.backfilled:
                first = last - timedelta(days=nightly.NIGHTS - 1)
            else:
                live = [d for d in (r.first_crawl, r.first_ingest) if d is not None]
                first = max(min(live, default=last), last - timedelta(days=nightly.PICKABLE))
            source = "night" if self.backfilled else "backfill"
            changes = self.changes.snapshot() if self.changes is not None else None
            kinds = self.kinds.get() if self.kinds is not None else None
            try:
                lib = library_now(self.where, kinds)
            except Exception:
                log.exception("每晚纪录：读不到正式库")
                lib = None
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            states = {d: nightly.one_night(r, d)["state"] for d in days}
            finished = [d for d in days if states[d] in FINAL]
            latest = finished[-1] if finished else None
            # the library is that night's only when no later night has begun and it was not swapped in after it
            later = any(states[d] not in ("wait", "off") for d in days if latest and d > latest)
            owns = (lib is not None and latest is not None and not later
                    and (lib["night"] is None or lib["night"] <= latest))
            wrote = 0
            for d in finished:
                rec = night_record(r, d, source=source, changes=changes, library=lib if owns and d == latest else None)
                if rec is not None and self.store.put(rec, at=now):
                    wrote += 1
            self.backfilled = True
            self.last = {"at": now.isoformat(timespec="seconds"), "wrote": wrote, "nights": len(finished)}
            return wrote

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("每晚纪录：这一次没记成")
            self._stop.wait(self.every)

    def start(self) -> None:
        if not self.store.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nights", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(10)


# ------------------------------------------------------------------------------------------------ for the pages


def window(today_night: date, days: int = SHOWN) -> list[str]:
    """The last `days` nights ending at today_night, oldest first, as YYYY-MM-DD."""
    return [(today_night - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def home_history(store: Store, today_night: date, days: int = SHOWN) -> dict:
    """The fourteen nights 今晚 draws ({'enabled', 'why', 'nights': [one a night, None where none is recorded],
    'sites': {night: changed sites}})."""
    span_ = window(today_night, days)
    if not store.enabled:
        return {"enabled": False, "why": store.error, "days": span_, "nights": [], "sites": {}}
    got = {rec["night"]: rec for rec in store.nights(span_[0], span_[-1])}
    return {"enabled": True, "why": "", "days": span_, "nights": [got.get(d) for d in span_],
            "sites": store.changed_sites(span_[0], span_[-1]), "total": store.count()}


def site_history(store: Store, site: str, today_night: date, days: int = SHOWN) -> dict:
    """One site's fourteen nights for its page."""
    span_ = window(today_night, days)
    if not store.enabled:
        return {"enabled": False, "why": store.error, "days": span_, "nights": []}
    got = {rec["night"]: rec for rec in store.site_nights(site, span_[0], span_[-1])}
    return {"enabled": True, "why": "", "days": span_, "nights": [got.get(d) for d in span_]}
