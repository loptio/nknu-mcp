"""The archive crawl's to-fetch list (design §3.4) in SQLite: one row per canonical URL, the links between them, the
hosts with their queue, robots.txt and 404 fingerprint, a request log for the politeness audit, and events.

SQLite on the maintainer's Mac (one writer: the process that holds run.lock); the lab machine's frontier is PostgreSQL
(design §5) and takes these rows over as they are. Every call goes through one lock: workers are threads of one process.
Opening an older frontier.db adds the columns and tables it lacks (_migrate), so a schema change never needs a rebuild;
the monitor opens it read-only instead (it never alters the database of a running crawl), and refuses one that lacks
columns until `archive seed` or `archive run` has brought it up to date.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

TABLES = """
CREATE TABLE IF NOT EXISTS url(
  id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL, site TEXT NOT NULL, unit TEXT,
  tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
  found_via TEXT NOT NULL, found_from INTEGER,
  state TEXT NOT NULL DEFAULT 'pending', reason TEXT, next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  status INTEGER, final_url TEXT, ctype TEXT, bytes INTEGER, sha256 TEXT, warc TEXT, title TEXT, flags TEXT,
  fetched_at TEXT, elapsed_ms INTEGER, n_links INTEGER, n_postbacks INTEGER, directory INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS link(src INTEGER NOT NULL, dst TEXT NOT NULL, text TEXT, verdict TEXT NOT NULL,
  PRIMARY KEY(src, dst)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS offsite(domain TEXT PRIMARY KEY, n INTEGER NOT NULL, first_src INTEGER, sample TEXT);
CREATE TABLE IF NOT EXISTS postback(site TEXT NOT NULL, target TEXT NOT NULL, n INTEGER NOT NULL, sample INTEGER,
  PRIMARY KEY(site, target)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS host(host TEXT PRIMARY KEY, queue TEXT NOT NULL, ip TEXT, cname TEXT,
  robots_state TEXT, robots_at REAL, robots_body TEXT, crawl_delay REAL,
  probe_status INTEGER, probe_sha TEXT, probe_len INTEGER, probe_title TEXT,
  cooldown_until REAL NOT NULL DEFAULT 0, cooldown_s REAL NOT NULL DEFAULT 0, robots_fail INTEGER NOT NULL DEFAULT 0,
  robots_sha TEXT, dns_fail INTEGER NOT NULL DEFAULT 0, dns_at REAL NOT NULL DEFAULT 0,
  not_before REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS deny(dir TEXT PRIMARY KEY, host TEXT NOT NULL, run INTEGER NOT NULL DEFAULT 0,
  until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS request(id INTEGER PRIMARY KEY, queue TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL,
  purpose TEXT NOT NULL, started REAL NOT NULL, ended REAL NOT NULL, status INTEGER, error TEXT,
  scope TEXT, robots_sha TEXT);
-- a scope rule added during a run: requests for that reason before `at` were fetched under the old rules
CREATE TABLE IF NOT EXISTS rule_change(reason TEXT PRIMARY KEY, at REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS event(at TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, host TEXT, url TEXT,
  message TEXT);
CREATE TABLE IF NOT EXISTS robots_version(sha TEXT PRIMARY KEY, host TEXT NOT NULL, body TEXT NOT NULL, at REAL);
CREATE TABLE IF NOT EXISTS directory_site(site TEXT PRIMARY KEY, via_url TEXT NOT NULL, via_id INTEGER, text TEXT,
  at TEXT);
CREATE TABLE IF NOT EXISTS pause(queue TEXT PRIMARY KEY, until REAL NOT NULL, why TEXT);
"""
# the caps count the rows that will be fetched: not those skipped, nor any held back by a cap (F7); both counts are
# answered from an index alone
_WILL_FETCH = "state != 'skipped' AND coalesce(reason, '') NOT IN ('site_cap', 'file_cap', 'variant_cap', 'depth')"
COUNT_SITE = f"SELECT count(*) FROM url WHERE site=? AND kind=? AND {_WILL_FETCH}"
COUNT_PATH = f"SELECT count(*) FROM url WHERE site=? AND path=? AND {_WILL_FETCH}"
INDEXES = """
DROP INDEX IF EXISTS url_site_kind;
DROP INDEX IF EXISTS url_site_path;
CREATE INDEX IF NOT EXISTS url_next ON url(host, state, next_at, tier, kind, depth, id);
CREATE INDEX IF NOT EXISTS url_site ON url(site);
CREATE INDEX IF NOT EXISTS url_site_kind_state ON url(site, kind, state, reason);
CREATE INDEX IF NOT EXISTS url_site_path_state ON url(site, path, state, reason);
CREATE INDEX IF NOT EXISTS url_path ON url(path);
CREATE INDEX IF NOT EXISTS link_dst ON link(dst);
CREATE INDEX IF NOT EXISTS request_queue ON request(queue, started);
"""
ALL_QUEUES = "*"  # the pause row that holds every queue (our network is down)
NETWORKS = ("school", "other")  # the school's addresses (140.127.*) and everything else, told apart when one is down
_NET = "net:"  # the pause row of one network: net:school, net:other
RETRY_MIN_PREFIX = 3


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def network_of(queue: str) -> str:
    return "school" if queue.startswith("ip:140.127.") else "other"


def net_key(network: str) -> str:
    """The pause row of one network's queues."""
    return _NET + network


class OutdatedFrontier(RuntimeError):
    """A frontier.db written by an older version, opened read-only: it lacks what the monitor reads."""


def _missing(conn: sqlite3.Connection) -> list[tuple[str, tuple]]:
    """The tables and columns TABLES has and the database lacks: (table, None) or (table, PRAGMA table_info row)."""
    want = sqlite3.connect(":memory:")
    try:
        want.executescript(TABLES)
        out: list[tuple[str, tuple]] = []
        for (table,) in want.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not have:
                out.append((table, None))
                continue
            out += [(table, col) for col in want.execute(f"PRAGMA table_info({table})").fetchall() if col[1] not in have]
        return out
    finally:
        want.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add the columns an older frontier.db lacks (tables are created by TABLES itself). A column that cannot be added
    (part of the key, or NOT NULL without a default) stops here, before any request goes out."""
    for table, col in _missing(conn):
        if col is None:
            continue
        _cid, name, typ, notnull, default, pk = col
        if pk or (notnull and default is None):
            raise RuntimeError(f"frontier.db: table {table} lacks column {name}, which cannot be added in place")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}" + (" NOT NULL" if notnull else "")
                     + (f" DEFAULT {default}" if default is not None else ""))


class Frontier:
    def __init__(self, path: Path, *, readonly: bool = False):
        """readonly: the monitor's view, which never writes (no table, column or index is added); the file must exist
        and be up to date (OutdatedFrontier otherwise)."""
        self.path = path
        self.lock = threading.RLock()
        if readonly:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False, isolation_level=None,
                                        timeout=30)
            self.conn.row_factory = sqlite3.Row
            gaps = _missing(self.conn)
            if gaps:
                self.conn.close()
                names = ", ".join(f"{t}.{c[1]}" if c else t for t, c in gaps[:6])
                raise OutdatedFrontier(f"{path} was written by an older version (no {names})")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(TABLES)
        _migrate(self.conn)
        self.conn.executescript(INDEXES)

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.conn.execute(sql, args).rowcount

    def close(self) -> None:
        self.conn.close()

    # ---- urls
    def add(self, url: str, *, host: str, site: str, unit: str | None, tier: int, kind: str, depth: int,
            found_via: str, found_from: int | None, state: str = "pending", reason: str | None = None,
            next_at: float = 0.0, directory: bool = False) -> bool:
        """A new URL; False when it is already known (the first discovery keeps its tier and depth). directory: an
        official directory page (design §2), whose off-campus links are crawled too."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO url(url, path, host, site, unit, tier, kind, depth, found_via, found_from, state, "
                "reason, next_at, directory) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (url, url.split("?", 1)[0], host, site, unit, tier, kind, depth, found_via, found_from, state, reason,
                 next_at, int(directory)))
            return cur.rowcount == 1

    def exists(self, url: str) -> bool:
        return bool(self.q("SELECT 1 FROM url WHERE url=?", (url,)))

    def mark_directory(self, urls) -> int:
        """Mark these URLs (already in the frontier) as official directory pages."""
        urls = list(urls)
        if not urls:
            return 0
        return self.x(f"UPDATE url SET directory=1 WHERE directory=0 AND url IN ({','.join('?' * len(urls))})", urls)

    def count_site(self, site: str, kind: str) -> int:
        """Pages (kind 'page') or attachments ('file') of a site that will be fetched: the two have their own caps."""
        return self.q(COUNT_SITE, (site, kind))[0][0]

    def count_path(self, site: str, path: str) -> int:
        return self.q(COUNT_PATH, (site, path))[0][0]

    def next_for(self, hosts: list[str], now: float) -> sqlite3.Row | None:
        """The next URL of one queue (its hosts), skipping hosts in cool-down or still within their Crawl-delay: lower
        tier first, pages before files, shallower first, then in discovery order. The row is marked active. (Queue
        pauses are the caller's check.)"""
        if not hosts:
            return None
        marks = ",".join("?" * len(hosts))
        with self.lock:
            row = self.conn.execute(
                f"""SELECT u.* FROM url u JOIN host h ON h.host = u.host
                     WHERE u.host IN ({marks}) AND u.state = 'pending' AND u.next_at <= ? AND h.cooldown_until <= ?
                       AND h.not_before <= ?
                     ORDER BY u.tier, u.kind = 'file', u.depth, u.id LIMIT 1""", (*hosts, now, now, now)).fetchone()
            if row is not None:
                self.conn.execute("UPDATE url SET state='active' WHERE id=?", (row["id"],))
            return row

    def finish(self, uid: int, **cols) -> None:
        cols.setdefault("fetched_at", utc_now())
        sets = ", ".join(f"{k}=?" for k in cols)
        self.x(f"UPDATE url SET {sets} WHERE id=?", (*cols.values(), uid))

    def requeue_active(self) -> int:
        """Rows left active by a stopped run go back to pending (only the run that holds run.lock calls this)."""
        return self.x("UPDATE url SET state='pending' WHERE state='active'")

    def retry(self, prefix: str) -> tuple[int, int]:
        """`nknu archive retry`: the URLs whose reason starts with prefix (any case, at least three characters) get
        every attempt back, now: those failed, skipped or waiting, and of the archived answers only those kept as a
        refusal (http_4xx, e.g. http_403 after a host blocked us), never a page fetched well. Their hosts too
        (robots.txt read again, cool-down, refusal streaks and DNS back-off cleared), so the run can really finish
        them. Returns (URLs, hosts); ValueError for a prefix that is too short."""
        prefix = prefix.strip()
        if len(prefix) < RETRY_MIN_PREFIX:
            raise ValueError(f"a reason prefix of at least {RETRY_MIN_PREFIX} characters is needed (e.g. http_5, dns)")
        match = ("lower(substr(coalesce(reason, ''), 1, ?)) = lower(?) AND (state IN ('failed','skipped','pending') "
                 "OR (state = 'done' AND reason GLOB 'http_4[0-9][0-9]'))")
        args = (len(prefix), prefix)
        with self.lock:
            hosts = [r[0] for r in self.conn.execute(f"SELECT DISTINCT host FROM url WHERE {match}", args)]
            n = self.conn.execute(f"UPDATE url SET state='pending', next_at=0, attempts=0 WHERE {match}", args).rowcount
            for i in range(0, len(hosts), 500):
                part = hosts[i:i + 500]
                marks = ",".join("?" * len(part))
                self.conn.execute(f"UPDATE host SET robots_fail=0, robots_at=0 WHERE host IN ({marks}) "
                                  "AND robots_state='unreachable'", part)
                self.conn.execute(f"UPDATE host SET cooldown_until=0, cooldown_s=0 WHERE host IN ({marks})", part)
                self.conn.execute(f"DELETE FROM deny WHERE host IN ({marks})", part)
                self.conn.execute(f"UPDATE host SET dns_fail=0, dns_at=0 WHERE host IN ({marks}) AND queue='dns-fail'",
                                  part)
            return n, len(hosts)

    # ---- hosts
    def host(self, host: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM host WHERE host=?", (host,))
        return rows[0] if rows else None

    def set_host(self, host: str, **cols) -> None:
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO host(host, queue) VALUES (?, ?)", (host, cols.get("queue", "?")))
            sets = ", ".join(f"{k}=?" for k in cols)
            self.conn.execute(f"UPDATE host SET {sets} WHERE host=?", (*cols.values(), host))

    def queues(self, now: float) -> dict[str, list[str]]:
        """queue -> its hosts, for the queues that have work due and are not paused (nor their network)."""
        if self.paused_until(ALL_QUEUES) > now:
            return {}
        paused = {r[0] for r in self.q("SELECT queue FROM pause WHERE until > ?", (now,))}
        rows = self.q("""SELECT DISTINCT h.queue, h.host FROM host h JOIN url u ON u.host = h.host
                          WHERE u.state = 'pending' AND u.next_at <= ? AND h.cooldown_until <= ? AND h.not_before <= ?""",
                      (now, now, now))
        out: dict[str, list[str]] = {}
        for r in rows:
            if r["queue"] not in paused and net_key(network_of(r["queue"])) not in paused:
                out.setdefault(r["queue"], []).append(r["host"])
        return out

    def hosts_of(self, queue: str) -> list[str]:
        return [r["host"] for r in self.q("SELECT host FROM host WHERE queue=?", (queue,))]

    def next_due(self, hosts: list[str]) -> float | None:
        """When the next pending row of these hosts is due (its retry, its host's cool-down or Crawl-delay)."""
        if not hosts:
            return None
        marks = ",".join("?" * len(hosts))
        return self.q(f"SELECT min(max(u.next_at, h.cooldown_until, h.not_before)) FROM url u JOIN host h "
                      f"ON h.host = u.host WHERE u.host IN ({marks}) AND u.state = 'pending'", hosts)[0][0]

    def pending_left(self) -> tuple[int, float | None]:
        """How many URLs still wait (pending or active), and the earliest time one of the pending is due."""
        n = self.q("SELECT count(*) FROM url WHERE state IN ('pending','active')")[0][0]
        nxt = self.q("SELECT min(max(u.next_at, h.cooldown_until, h.not_before)) FROM url u JOIN host h ON h.host=u.host "
                     "WHERE u.state='pending'")[0][0]
        return n, nxt

    # ---- refusals (401/403/451) in a row, per directory
    def deny(self, directory: str, host: str) -> int:
        """One more refusal from this directory: the streak so far."""
        with self.lock:
            self.conn.execute("INSERT INTO deny(dir, host, run) VALUES (?,?,1) ON CONFLICT(dir) DO UPDATE SET "
                              "run = run + 1", (directory, host))
            return self.conn.execute("SELECT run FROM deny WHERE dir=?", (directory,)).fetchone()[0]

    def deny_rest(self, directory: str, until: float) -> None:
        """The streak has been answered by a wait: it starts again from 0, and the directory waits until then."""
        self.x("UPDATE deny SET run=0, until=? WHERE dir=?", (until, directory))

    def deny_clear(self, directory: str) -> None:
        self.x("DELETE FROM deny WHERE dir=?", (directory,))

    def deny_until(self, directory: str) -> float:
        rows = self.q("SELECT until FROM deny WHERE dir=?", (directory,))
        return rows[0][0] if rows else 0.0

    # ---- pauses: a server queue resting (Retry-After), one network ("net:school", "net:other") or every queue ("*")
    # while our network is down
    def pause(self, queue: str, until: float, why: str) -> None:
        self.x("INSERT INTO pause(queue, until, why) VALUES (?,?,?) ON CONFLICT(queue) DO UPDATE SET "
               "until = max(until, excluded.until), why = excluded.why", (queue, until, why))

    def unpause(self, queue: str) -> None:
        self.x("DELETE FROM pause WHERE queue=?", (queue,))

    def pause_of(self, key: str) -> float:
        """That one pause row's end (0 when there is none)."""
        return self.q("SELECT coalesce(max(until), 0) FROM pause WHERE queue=?", (key,))[0][0]

    def paused_until(self, queue: str) -> float:
        """Until when a queue may not send: its own pause, its network's or the all-queues one; 0 when it may. For "*"
        or a network's key, that pause and the all-queues one."""
        keys = (queue, ALL_QUEUES)
        if queue != ALL_QUEUES and not queue.startswith(_NET):
            keys += (net_key(network_of(queue)),)
        return self.q(f"SELECT coalesce(max(until), 0) FROM pause WHERE queue IN ({','.join('?' * len(keys))})",
                      keys)[0][0]

    # ---- robots.txt versions and the directory's off-campus sites
    def robots_version(self, sha: str, host: str, body: str, at: float) -> None:
        self.x("INSERT OR IGNORE INTO robots_version(sha, host, body, at) VALUES (?,?,?,?)", (sha, host, body, at))

    def add_directory_site(self, site: str, via_url: str, via_id: int | None, text: str | None) -> bool:
        return self.x("INSERT OR IGNORE INTO directory_site(site, via_url, via_id, text, at) VALUES (?,?,?,?,?)",
                      (site, via_url, via_id, text, utc_now())) == 1

    def directory_sites(self) -> dict[str, sqlite3.Row]:
        return {r["site"]: r for r in self.q("SELECT * FROM directory_site")}

    # ---- links and logs
    def link(self, src: int, dst: str, text: str, verdict: str) -> None:
        self.x("INSERT OR IGNORE INTO link(src, dst, text, verdict) VALUES (?,?,?,?)", (src, dst, text, verdict))

    def offsite(self, domain: str, src: int, sample: str) -> None:
        self.x("INSERT INTO offsite(domain, n, first_src, sample) VALUES (?,1,?,?) "
               "ON CONFLICT(domain) DO UPDATE SET n = n + 1", (domain, src, sample))

    def postback(self, site: str, target: str, src: int) -> None:
        self.x("INSERT INTO postback(site, target, n, sample) VALUES (?,?,1,?) "
               "ON CONFLICT(site, target) DO UPDATE SET n = n + 1", (site, target, src))

    def request(self, queue: str, host: str, url: str, purpose: str, started: float, ended: float,
                status: int | None, error: str | None, *, scope: str | None = None, robots_sha: str | None = None) -> None:
        """One request that may have reached the server; scope and robots_sha: what allowed it at that moment."""
        self.x("INSERT INTO request(queue, host, url, purpose, started, ended, status, error, scope, robots_sha) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", (queue, host, url, purpose, started, ended, status, error, scope,
                                               robots_sha))

    def event(self, level: str, code: str, message: str, host: str | None = None, url: str | None = None) -> None:
        self.x("INSERT INTO event(at, level, code, host, url, message) VALUES (?,?,?,?,?,?)",
               (utc_now(), level, code, host, url, message))


def wall() -> float:
    return time.time()
