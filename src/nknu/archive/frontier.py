"""The archive crawl's to-fetch list (design §3.4) in SQLite: one row per canonical URL, the links between them, the
hosts with their queue, robots.txt and 404 fingerprint, a request log for the politeness audit, and events.

SQLite on the maintainer's Mac (one writer: the process that holds run.lock); the lab machine's frontier is PostgreSQL
(design §5) and takes these rows over as they are. Every call goes through one lock: workers are threads of one process.
Opening an older frontier.db adds the columns and tables it lacks (_migrate), so a schema change never needs a rebuild;
the monitor opens it read-only instead (it never alters the database of a running crawl), and refuses one that lacks
columns until `archive seed` or `archive run` has brought it up to date.

The round-1 tables (design §12): the site list (site_plan), page types and their browser verdict (page_type), what each
page uses (page_res), the ASP.NET buttons pressed (action: POSTs only, the url table holds GETs) and each list's walk
to its last page (listwalk), Drive items, the id probes, where each seed came from (seed_src), the run's facts
(meta), what the browser check saw on each page it opened (js_page), each site's last reconciliation
(reconcile_site), and a person's word that a row's result is the school's (review_mark). What a row came to is
never stored: each open builds the views url_result, action_result, page_resource and page_completeness from
outcome.py (a read-only open, in its own temp schema), so every reader applies the same rules.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from nknu.archive import outcome

TABLES = """
CREATE TABLE IF NOT EXISTS url(
  id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL, site TEXT NOT NULL, unit TEXT,
  tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
  found_via TEXT NOT NULL, found_from INTEGER,
  state TEXT NOT NULL DEFAULT 'pending', reason TEXT, next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  status INTEGER, final_url TEXT, ctype TEXT, bytes INTEGER, sha256 TEXT, warc TEXT, title TEXT, flags TEXT,
  fetched_at TEXT, elapsed_ms INTEGER, n_links INTEGER, n_postbacks INTEGER, directory INTEGER NOT NULL DEFAULT 0,
  -- round 1 (design §12): the site it counts for (§4.3); 0 lists/seeds/sitemaps/login checks/tabs pressed by rule,
  -- 1 pages, 2 files/Drive/calendars/forms, 3 images/school scripts, +1 when only an old source names it (§6.7); 1: no
  -- links are read from it as a page (§4.4: what a tab, an embed box or a Drive folder listing brings counts on the
  -- page that uses it); the kind expected when found (page/file/image/drive/calendar/form/shortlink/sitemap/
  -- login_check/click: a tab pressed by its platform rule, §7.4/script: a school site's .js, §4.4/embed: a Google
  -- Sites embed box, §4.4/drive_list: one page of a Drive folder's listing, §7.6); home/list/detail/frame/other; its
  -- page type key (§8.1); the format its first bytes say (§7.3); the response record in url.warc.
  -- kind once fetched: page/file/image/drive/calendar/embed (§10.2: what is not HTML is never a page; a form's or an
  -- embed box's HTML is an embed, the page's part, never a round-1 page itself), or shortlink: a short link's row stays
  -- one whatever its service answers, its target in final_url (§7.7, review 10-26 A2; round1.short_answer).
  -- flags: login, soft404, decode, gone_redirect, gone_id, error_page, thin, empty_detail, js_hint, form_closed,
  -- js_redirect (§8.3), click_empty (§7.4): outcome.FLAGS lists them all, any other makes the row unknown.
  -- found_via: seed, v1_known, old_frontier, commoncrawl (the seeds); link, redirect, js_redirect (§8.3),
  -- scheme_switch (§6.6), click (§7.4), drive (§7.6), jscheck_redirect (later rows only, §8.4), reconcile (§18.2b),
  -- rejudge (§18.2a)
  owner_site TEXT, prio INTEGER NOT NULL DEFAULT 1, leaf INTEGER NOT NULL DEFAULT 0, kind0 TEXT, page_role TEXT,
  page_type TEXT, sniff TEXT, text_chars INTEGER, filename TEXT, size_declared INTEGER,
  warc_offset INTEGER, warc_len INTEGER, added_at REAL, old_ref INTEGER,
  -- design §23 B1: a Google Sites runtime picture (and the hop its 302 leads to): the referrer policy of the page that
  -- shows it and that page's address, as JSON {"policy", "from"}, from which the Referer of its request is worked out
  -- (links.referrer_for); NULL for every other row, whose requests carry no Referer
  referrer TEXT);
CREATE TABLE IF NOT EXISTS link(src INTEGER NOT NULL, dst TEXT NOT NULL, text TEXT, verdict TEXT NOT NULL,
  PRIMARY KEY(src, dst)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS offsite(domain TEXT PRIMARY KEY, n INTEGER NOT NULL, first_src INTEGER, sample TEXT);
CREATE TABLE IF NOT EXISTS postback(site TEXT NOT NULL, target TEXT NOT NULL, n INTEGER NOT NULL, sample INTEGER,
  argument TEXT, text TEXT, form_id TEXT,
  decision TEXT NOT NULL DEFAULT 'unknown',  -- press/never/ignore from the platform profile; unknown must reach 0 (§7.4)
  PRIMARY KEY(site, target)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS host(host TEXT PRIMARY KEY, queue TEXT NOT NULL, ip TEXT, cname TEXT,
  robots_state TEXT, robots_at REAL, robots_body TEXT, crawl_delay REAL,
  probe_status INTEGER, probe_sha TEXT, probe_len INTEGER, probe_title TEXT,
  cooldown_until REAL NOT NULL DEFAULT 0, cooldown_s REAL NOT NULL DEFAULT 0, robots_fail INTEGER NOT NULL DEFAULT 0,
  robots_sha TEXT, dns_fail INTEGER NOT NULL DEFAULT 0, dns_at REAL NOT NULL DEFAULT 0,
  not_before REAL NOT NULL DEFAULT 0,
  scheme_only TEXT, dns_ms REAL,  -- the one scheme the host answers on (§6.6); the hourly getaddrinfo time (§6.2)
  -- 1: it has answered us over HTTP from outside the school's network (this crawl off campus, or the 10-24 archive's
  -- requests, which were made from the maintainer's Mac: review 10-26 B3): failing to connect, it is down for now,
  -- never a host only the school's network reaches (lab)
  answered_off INTEGER NOT NULL DEFAULT 0);
-- a server (one queue: one IP) that does not connect (review 10-26 B2): how many times in a row, the last failure and
-- the last answer. Its queue pauses a fixed CONN_PAUSE_S each time and no URL loses an attempt; at CONN_GIVE_UP in a
-- row a warning, and a school host that never answered off campus waits for the lab machine. err_fail: its errors in
-- a row (5xx, 408, timeouts, any host name on it): at BREAKER_THRESHOLD the whole server rests (second review, 10-26)
CREATE TABLE IF NOT EXISTS server(queue TEXT PRIMARY KEY, conn_fail INTEGER NOT NULL DEFAULT 0, failed_at REAL,
  answered_at REAL, err_fail INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS deny(dir TEXT PRIMARY KEY, host TEXT NOT NULL, run INTEGER NOT NULL DEFAULT 0,
  until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS request(id INTEGER PRIMARY KEY, queue TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL,
  purpose TEXT NOT NULL, started REAL NOT NULL, ended REAL NOT NULL, status INTEGER, error TEXT,
  scope TEXT, robots_sha TEXT,
  -- the connection it used (§6.2: a new one or kept alive, its TCP and TLS times, the server's address) and the try
  -- within one call (§6.4: 2 is the resend after a kept-alive connection was closed)
  method TEXT, bytes INTEGER, new_conn INTEGER, connect_ms REAL, tls_ms REAL, ip TEXT,
  attempt INTEGER NOT NULL DEFAULT 1,
  -- where its answer's response record is (the queue's WARC file of that run), and where the request was made from:
  -- on (the school's network), off, unknown (review 10-26 B4: some pages show their full text on campus only)
  warc TEXT, warc_offset INTEGER, campus TEXT);
-- a scope rule added during a run: requests for that reason before `at` were fetched under the old rules
CREATE TABLE IF NOT EXISTS rule_change(reason TEXT PRIMARY KEY, at REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS event(at TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, host TEXT, url TEXT,
  message TEXT);
CREATE TABLE IF NOT EXISTS robots_version(sha TEXT PRIMARY KEY, host TEXT NOT NULL, body TEXT NOT NULL, at REAL);
CREATE TABLE IF NOT EXISTS directory_site(site TEXT PRIMARY KEY, via_url TEXT NOT NULL, via_id INTEGER, text TEXT,
  at TEXT);
CREATE TABLE IF NOT EXISTS pause(queue TEXT PRIMARY KEY, until REAL NOT NULL, why TEXT);
-- round 1 (design §12). The site list (config/archive_round1.json): round 1, resource or 2 (why: js_site, other_site,
-- offsite_unit); paths and params as JSON (params: a parameter's value that must match, cirn.moe's sid=1219); leaf 1:
-- round two fetches that page only (§4.1); base_urls, the rows the old archive holds for the site
CREATE TABLE IF NOT EXISTS site_plan(site TEXT PRIMARY KEY, name TEXT, imp TEXT, round TEXT NOT NULL, why TEXT,
  platform TEXT, home TEXT, paths TEXT, params TEXT, leaf INTEGER NOT NULL DEFAULT 0, base_urls INTEGER);
-- a site's pages that share one address pattern (§8.1) and what the browser test said of them: ok, js, frame or todo
CREATE TABLE IF NOT EXISTS page_type(key TEXT PRIMARY KEY, site TEXT NOT NULL, host TEXT, path_tpl TEXT, params TEXT,
  verdict TEXT NOT NULL DEFAULT 'todo', why TEXT, method TEXT, direct_chars INTEGER, rendered_chars INTEGER,
  data_urls TEXT, sample_id INTEGER, sample_url TEXT, n_pages INTEGER NOT NULL DEFAULT 0,
  n_strong INTEGER NOT NULL DEFAULT 0, n_weak INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0,
  source TEXT, tested_at TEXT, note TEXT);
-- what a page uses (§7.1): how it is fetched (get/postback/drive_api/calendar_api/link_only/unhandled/click), its role
-- (img/file/iframe/frame/drive/calendar/form/detail/download/pager/item/shortlink/click/script), need (must/no/round2,
-- why when not must, and for a resource with no row: round2:<why>, unhandled:<why>, out:<rule>) and the url or action
-- row that holds its result
CREATE TABLE IF NOT EXISTS page_res(page_id INTEGER NOT NULL, res_key TEXT NOT NULL, how TEXT NOT NULL,
  role TEXT NOT NULL, need TEXT NOT NULL, why TEXT, res_id INTEGER, action_id INTEGER, zone TEXT, text TEXT,
  PRIMARY KEY(page_id, res_key)) WITHOUT ROWID;
-- one press of an ASP.NET button (§7.5), built from the form state of the list page archived at form_src_*; the
-- pager's row_key is 'p<step>'. kind: pager/detail/download, or click (a button pressed by its platform rule that posts
-- for a file's address, §7.4: list_id is the page; one press per site and row_key, action_click), or data (a platform's
-- data address its pages ask by script, read once per site, §23 C1: list_id is the first page that asked it, target
-- the address, argument the request's body, row_key <data api>:<value>; action_data)
CREATE TABLE IF NOT EXISTS action(id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL, epoch INTEGER NOT NULL,
  step INTEGER NOT NULL, kind TEXT NOT NULL, queue TEXT NOT NULL, host TEXT NOT NULL, site TEXT NOT NULL,
  profile TEXT NOT NULL, form_src_warc TEXT NOT NULL, form_src_offset INTEGER NOT NULL, form_id TEXT,
  target TEXT NOT NULL, argument TEXT NOT NULL DEFAULT '', row_key TEXT NOT NULL, row_text TEXT,
  state TEXT NOT NULL DEFAULT 'pending', reason TEXT, status INTEGER, ctype TEXT, bytes INTEGER, sha256 TEXT,
  sniff TEXT, filename TEXT, location TEXT, warc TEXT, warc_offset INTEGER, warc_len INTEGER, created REAL, done REAL,
  -- a read of a platform's data address (kind data, design §23 C1): read again after a failure as a url row is (30 min
  -- × attempts, three in all), never before next_at; 0 for every press of a chain
  next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE(list_id, epoch, kind, row_key));
-- the evidence that a list was walked to its last page (§7.5 step 7): claimed is the 「共 N 筆」 the page states
CREATE TABLE IF NOT EXISTS listwalk(list_id INTEGER PRIMARY KEY, site TEXT, profile TEXT, epoch INTEGER,
  pages INTEGER, rows INTEGER, rows_unique INTEGER, claimed INTEGER, ended_by TEXT, ok INTEGER, note TEXT, at REAL);
-- a Drive item whose metadata row is url_id; content_id: the row of its content (export, alt=media), for a folder its
-- listing's first page; none for a video (§7.6)
CREATE TABLE IF NOT EXISTS drive_item(url_id INTEGER PRIMARY KEY, file_id TEXT NOT NULL, name TEXT, mime TEXT,
  size INTEGER, md5 TEXT, modified TEXT, parent_id TEXT, resource_key TEXT, depth INTEGER, album INTEGER,
  content_id INTEGER);
-- what jscheck saw when it opened this one page (§8.3, §8.4): ok/js/frame/click/unresolved, at frontier.utc_now();
-- one row per page, written again when it is opened again. url_result reads it for a page with a strong hint
CREATE TABLE IF NOT EXISTS js_page(url_id INTEGER PRIMARY KEY, page_type TEXT, at TEXT NOT NULL, verdict TEXT NOT NULL,
  why TEXT, final_url TEXT);
-- each site's last reconciliation (§18.2b): checks as JSON (R1–R7, V13, V14: pass/fail/warn/skip and numbers), the
-- discovery coverage (NULL when it cannot be estimated), and whether every gate passed (a site is 爬完 only then)
CREATE TABLE IF NOT EXISTS reconcile_site(site TEXT PRIMARY KEY, batch TEXT NOT NULL, at TEXT NOT NULL,
  checks TEXT NOT NULL, cover_cc REAL, cover_old REAL, gates_ok INTEGER NOT NULL);
-- what a detail page for an id that does not exist looks like, per platform host and detail type (§10.1); empty: 1
-- when that page is an empty detail by the platform file's nonempty (profiles.empty_detail), the second of the three
-- things gone_id needs (NULL: not a 200 page, or no body area to judge)
CREATE TABLE IF NOT EXISTS id_probe(host TEXT NOT NULL, page_type TEXT NOT NULL, url TEXT, status INTEGER,
  final_url TEXT, sha256 TEXT, len INTEGER, title TEXT, at REAL, empty INTEGER, PRIMARY KEY(host, page_type));
CREATE TABLE IF NOT EXISTS seed_src(url TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(url, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
-- a person looked at a row and says what it came to is the school's to answer for (mark school_side, review 10-26
-- B7): the result, reason, status and fetched_at it had then, which the result views compare with the row's (any
-- change, a new fetch included, and the mark lapses for good), the person's note and when (frontier.utc_now());
-- outcome.markable() says which results it may have (Frontier.mark_school_side). For an address that redirects, the
-- result is what it leads to as a page's resource (view page_resource: an attachment sent to the home page is
-- not_file), which page_completeness compares (second look, F5)
-- the YouTube videos a page links or embeds (owner 10-26, record 五·15): never fetched (out:social), kept with the title
-- the school's page gives each (links.Video) for the sorting stage; one row per page and video
CREATE TABLE IF NOT EXISTS video(page_id INTEGER NOT NULL, video_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT,
  how TEXT NOT NULL, zone TEXT, PRIMARY KEY(page_id, video_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS review_mark(url_id INTEGER PRIMARY KEY, mark TEXT NOT NULL, result TEXT NOT NULL,
  reason TEXT, status INTEGER, note TEXT NOT NULL, at TEXT NOT NULL, fetched_at TEXT);
"""
# the caps count the rows this round fetches (design §9): waiting, being fetched, fetched or failed; never those skipped,
# held back by a cap (held, and the 10-24 crawler's skipped site_cap and the like) or left for round two (later: a
# second-round site's rows would otherwise take a round-1 site's 4,000 pages). Both counts are answered from an index
_WILL_FETCH = "state IN ('pending', 'active', 'done', 'failed')"
# by the site a row counts for (url.owner_site, design §2): a page's attachments on another host count for its site
COUNT_SITE = f"SELECT count(*) FROM url WHERE owner_site=? AND kind=? AND {_WILL_FETCH}"
COUNT_PATH = f"SELECT count(*) FROM url WHERE owner_site=? AND path=? AND {_WILL_FETCH}"
INDEXES = """
DROP INDEX IF EXISTS url_site_kind;
DROP INDEX IF EXISTS url_site_path;
CREATE INDEX IF NOT EXISTS url_next ON url(host, state, next_at, tier, kind, depth, id);
CREATE INDEX IF NOT EXISTS url_site ON url(site);
CREATE INDEX IF NOT EXISTS url_site_kind_state ON url(site, kind, state, reason);
CREATE INDEX IF NOT EXISTS url_site_path_state ON url(site, path, state, reason);
CREATE INDEX IF NOT EXISTS url_owner_path_state ON url(owner_site, path, state);
CREATE INDEX IF NOT EXISTS url_path ON url(path);
CREATE INDEX IF NOT EXISTS link_dst ON link(dst);
CREATE INDEX IF NOT EXISTS request_queue ON request(queue, started);
CREATE INDEX IF NOT EXISTS url_next2 ON url(host, state, next_at, tier, prio, depth, id);
CREATE INDEX IF NOT EXISTS url_owner ON url(owner_site, kind, state);
CREATE INDEX IF NOT EXISTS url_ptype ON url(page_type);
CREATE INDEX IF NOT EXISTS url_added ON url(added_at);
CREATE INDEX IF NOT EXISTS page_res_res ON page_res(res_id);
CREATE INDEX IF NOT EXISTS page_res_action ON page_res(action_id);
CREATE INDEX IF NOT EXISTS action_list ON action(list_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS action_click ON action(site, row_key) WHERE kind = 'click';
CREATE UNIQUE INDEX IF NOT EXISTS action_data ON action(site, row_key) WHERE kind = 'data';
CREATE INDEX IF NOT EXISTS request_started ON request(started);
CREATE INDEX IF NOT EXISTS url_sha ON url(sha256);
-- a Drive item by its id and a folder's items (google_api._item, expand, resync, resource_keys): without these each
-- lookup reads the whole table, mostly under the frontier's lock (review 10-26 phase 3: 52k items, 2,000 in one folder,
-- expand 4.1 s and resync 3.5 s, 0.02 s and 0.07 s with them)
CREATE INDEX IF NOT EXISTS drive_item_file ON drive_item(file_id);
CREATE INDEX IF NOT EXISTS drive_item_parent ON drive_item(parent_id);
"""
ALL_QUEUES = "*"  # the pause row that holds every queue (our network is down)
NETWORKS = ("school", "other")  # the school's addresses (140.127.*) and everything else, told apart when one is down
_NET = "net:"  # the pause row of one network: net:school, net:other
RETRY_MIN_PREFIX = 3
OLD_VIAS = ("old_frontier", "commoncrawl")  # the seeds an old source names (seeds.py), until a page links them


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def write_first(conn: sqlite3.Connection, name: str) -> Iterator[None]:
    """A transaction that reads, then writes, on a connection another one writes beside (the crawl's and its
    reconciliation's, Frontier.sibling): the write lock is taken at its start (BEGIN IMMEDIATE, which waits out the busy
    timeout as any write does), so no commit of the other comes between its read and its write. Begun as a read (a
    SAVEPOINT or a BEGIN), that write is refused at once and no wait helps (SQLITE_BUSY_SNAPSHOT: the lab run of 11-07
    05:45 stopped on it, pagetypes.observe). Inside a transaction already: a savepoint of it, the caller's to end."""
    if conn.in_transaction:
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def network_of(queue: str) -> str:
    return "school" if queue.startswith("ip:140.127.") else "other"


def net_key(network: str) -> str:
    """The pause row of one network's queues."""
    return _NET + network


def ids_clause(column: str, ids: Iterable[int] | None) -> tuple[str, list[str]]:
    """The condition (its AND first) that keeps a statement to these url rows: a run given --only (`nknu archive run
    --only`, the owner 11-02) touches no other row. column: the url id it reads (u.id, id, a.list_id: a press goes
    with its list page's row). Its one argument is the ids as a JSON array, so that no list is too long for SQLite's
    host parameters. ('', []) when ids is None: every row; an empty ids is no row at all."""
    if ids is None:
        return "", []
    return f" AND {column} IN (SELECT value FROM json_each(?))", [json.dumps(sorted(int(i) for i in ids))]


ROUND_KEY = "round"  # meta: the round a frontier.db was made for, written when this version creates the file
ROUND = "1"
REHEARSAL_KEY = "rehearsal:seeded"  # meta: the offline rehearsal seeded this archive (rehearsal.py), when
# meta: the offline rehearsal made this frontier.db (written as the file is made, before its seed: review 10-26, so that
# a rehearsal stopped in its seed is still a rehearsal's, never taken for a real archive by either side)
REHEARSAL_STARTED_KEY = "rehearsal:started"


def round_of(path: Path) -> str | None:
    """The round a frontier.db was made for (meta round); None for one the 10-24 crawler made (it never had the key,
    and opening it never adds it: only a new file gets it), or when there is no file. Read only: never migrates, and
    never creates the file (review 10-26: the archive commands refuse the 10-24 archive by this).

    Opened as immutable: SQLite neither writes nor even opens the -wal and -shm files (mode=ro alone creates them and
    writes the -shm: second review, 10-26, the 10-24 archive's -shm). Only when that finds no round and a -wal with
    something in it is there (a new archive whose writer was killed before a checkpoint keeps its first rows there) is
    the file read once more through its WAL; the 10-24 archive's -wal is empty."""
    return _meta_of(path, ROUND_KEY)


def rehearsal_of(path: Path) -> str | None:
    """When the offline rehearsal seeded this frontier.db (meta REHEARSAL_KEY, design §18.1), or made it and was
    stopped before its seed ended (REHEARSAL_STARTED_KEY): its hosts' addresses are the old archive's or a made-up
    private one, so no command that sends requests may run on it. None for any other archive. Read like round_of:
    never migrates, never creates the file."""
    return _meta_of(path, REHEARSAL_KEY) or _meta_of(path, REHEARSAL_STARTED_KEY)


def _meta_of(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    got = _read_meta(f"file:{path}?mode=ro&immutable=1", key)
    wal = Path(f"{path}-wal")
    if got is None and wal.exists() and wal.stat().st_size > 0:
        got = _read_meta(f"file:{path}?mode=ro", key)
    return got


def _read_meta(uri: str, key: str) -> str | None:
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:  # no meta table: the 10-24 crawler's
        return None
    finally:
        conn.close()
    return row[0] if row else None


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
            outcome.create_views(self.conn, temp=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(TABLES)
        if new:  # made by this version: a round-1 archive (an old file opened here never becomes one)
            self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (ROUND_KEY, ROUND))
        _migrate(self.conn)
        self.conn.executescript(INDEXES)
        outcome.create_views(self.conn)

    def sibling(self) -> Frontier:
        """Another connection to the same file for another thread of this process (the crawler's reconciliation,
        review 10-26): its own lock, so that a long read there (page_resource, page_completeness: seconds) never holds
        this one's, which every worker of the run takes; nothing is made (this one made the tables, indexes and
        views). WAL lets its reads go on beside this one's writes; its writes wait for this one's (busy timeout)."""
        f = Frontier.__new__(Frontier)
        f.path, f.lock = self.path, threading.RLock()
        f.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        f.conn.row_factory = sqlite3.Row
        f.conn.execute("PRAGMA synchronous=NORMAL")
        return f

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
            next_at: float = 0.0, directory: bool = False, owner_site: str | None = None, prio: int = 1,
            leaf: int = 0, kind0: str | None = None, added_at: float | None = None,
            old_ref: int | None = None, referrer: str | None = None) -> bool:
        """A new URL; False when it is already known (the first discovery keeps its tier, owner and depth).
        owner_site, prio, leaf, kind0: round 1's (design §4.3, §6.7, §4.4, §12); old_ref: its id in the old archive;
        referrer: a Google Sites runtime picture's (design §23 B1, the column's comment)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO url(url, path, host, site, unit, tier, kind, depth, found_via, found_from, state, "
                "reason, next_at, directory, owner_site, prio, leaf, kind0, added_at, old_ref, referrer) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (url, url.split("?", 1)[0], host, site, unit, tier, kind, depth, found_via, found_from, state, reason,
                 next_at, int(directory), owner_site, prio, leaf, kind0, added_at, old_ref, referrer))
            return cur.rowcount == 1

    def row(self, url: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM url WHERE url=?", (url,))
        return rows[0] if rows else None

    def row_by_id(self, uid: int | None) -> sqlite3.Row | None:
        if uid is None:
            return None
        rows = self.q("SELECT * FROM url WHERE id=?", (uid,))
        return rows[0] if rows else None

    def exists(self, url: str) -> bool:
        return bool(self.q("SELECT 1 FROM url WHERE url=?", (url,)))

    def mark_directory(self, urls) -> int:
        """Mark these URLs (already in the frontier) as official directory pages."""
        urls = list(urls)
        if not urls:
            return 0
        return self.x(f"UPDATE url SET directory=1 WHERE directory=0 AND url IN ({','.join('?' * len(urls))})", urls)

    def count_site(self, site: str, kind: str) -> int:
        """Pages (kind 'page'), attachments ('file') or images ('image') a site will fetch: each has its own cap. By
        owner_site, the site a row counts for."""
        return self.q(COUNT_SITE, (site, kind))[0][0]

    def count_path(self, site: str, path: str) -> int:
        return self.q(COUNT_PATH, (site, path))[0][0]

    def next_for(self, hosts: list[str], now: float, prefer_host: str | None = None,
                 sites: frozenset[str] | None = None, ids: Iterable[int] | None = None) -> sqlite3.Row | None:
        """The next URL of one queue (its hosts), skipping hosts in cool-down or still within their Crawl-delay
        (design §6.7): important sites first (tier), then lists, seeds and sitemaps before pages before files before
        images (prio), the host the queue's connection is on now before another name of that server (prefer_host:
        its connection is warm), shallower first, then in discovery order. A row whose prio is below 0 goes before
        all of them, the lowest first (design §23 B1: a Google Sites page's runtime pictures right after the page,
        the hop a picture's 302 leads to before the next picture, the page read again for new addresses). sites: only
        rows that count for these sites (a run given --sites); ids: only these rows (a run given --only). The row is
        marked active. (Queue pauses are the caller's check.)"""
        if not hosts:
            return None
        marks = ",".join("?" * len(hosts))
        only, args = "", [*hosts, now, now, now]
        if sites is not None:
            only = f" AND u.owner_site IN ({','.join('?' * len(sites))})"
            args += sorted(sites)
        by_id, id_args = ids_clause("u.id", ids)
        with self.lock:
            row = self.conn.execute(
                f"""SELECT u.* FROM url u JOIN host h ON h.host = u.host
                     WHERE u.host IN ({marks}) AND u.state = 'pending' AND u.next_at <= ? AND h.cooldown_until <= ?
                       AND h.not_before <= ?{only}{by_id}
                     ORDER BY min(u.prio, 0), u.tier, u.prio, u.host = ? DESC, u.depth, u.id LIMIT 1""",
                (*args, *id_args, prefer_host or "")).fetchone()
            if row is not None:
                self.conn.execute("UPDATE url SET state='active' WHERE id=?", (row["id"],))
            return row

    def finish(self, uid: int, **cols) -> None:
        cols.setdefault("fetched_at", utc_now())
        sets = ", ".join(f"{k}=?" for k in cols)
        self.x(f"UPDATE url SET {sets} WHERE id=?", (*cols.values(), uid))

    def old_only(self, uid: int) -> bool:
        """Only an old source names this row (found_via OLD_VIAS: the old archive's, Common Crawl's) and no page of this
        round links to it: one of the addresses monitor's R3b and R4b list (§18.2b)."""
        # a page links it in either scheme (a host that answers on one only: its rows are written in that one,
        # crawler._scheme_rewrite, while link.dst keeps the page's spelling; review 11-08)
        return bool(self.q(f"SELECT 1 FROM url u WHERE u.id = ? AND u.found_via IN {OLD_VIAS!r} AND NOT EXISTS "
                           "(SELECT 1 FROM link l WHERE l.dst IN (u.url, CASE WHEN u.url LIKE 'https://%' THEN "
                           "'http://' || substr(u.url, 9) ELSE 'https://' || substr(u.url, 8) END) AND l.src <> u.id)",
                           (uid,)))

    def requeue_active(self, ids: Iterable[int] | None = None) -> int:
        """Rows left active by a stopped run go back to pending (only the run that holds run.lock calls this). ids:
        only these rows (a run given --only leaves every other row as it is, for the next full run to take back)."""
        by_id, id_args = ids_clause("id", ids)
        return self.x(f"UPDATE url SET state='pending' WHERE state='active'{by_id}", id_args)

    def retry(self, prefix: str) -> tuple[int, int]:
        """`nknu archive retry`: the URLs whose reason starts with prefix (any case, at least three characters) get
        every attempt back, now: those failed, skipped or waiting, and of the archived answers only those kept as a
        refusal (http_4xx, e.g. http_403 after a host blocked us), never a page fetched well. Their hosts too
        (robots.txt read again, cool-down, refusal streaks and DNS back-off cleared), so the run can really finish
        them. A host nobody looked up yet (a round-two or a held row's: no host row, or the old archive's queue '?')
        is looked up by the next run before anything is fetched (Crawler.run, second review 10-26). Returns (URLs,
        hosts); ValueError for a prefix that is too short."""
        prefix = prefix.strip()
        if len(prefix) < RETRY_MIN_PREFIX:
            raise ValueError(f"a reason prefix of at least {RETRY_MIN_PREFIX} characters is needed (e.g. http_5, dns)")
        match = ("lower(substr(coalesce(reason, ''), 1, ?)) = lower(?) AND (state IN ('failed','skipped','pending',"
                 "'later','held') OR (state = 'done' AND reason GLOB 'http_4[0-9][0-9]'))")
        args = (len(prefix), prefix)
        with self.lock:
            hosts = [r[0] for r in self.conn.execute(f"SELECT DISTINCT host FROM url WHERE {match}", args)]
            # a round-two row (later, round2:*) or one held by a cap (held, cap:*) comes back as it is: its reason
            # says where it came from (design §4.3, §9)
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

    def queues(self, now: float, sites: frozenset[str] | None = None,
               ids: Iterable[int] | None = None) -> dict[str, list[str]]:
        """queue -> its hosts, for the queues that have work due and are not paused (nor their network). sites: only
        work that counts for these sites; ids: only these rows (a run given --only). A host whose queue is not known
        yet ('?', what the old archive's hosts are seeded with) is in none: it is looked up first
        (Crawler._place_hosts), so that no server is ever asked on two queues at once (second review, 10-26)."""
        if self.paused_until(ALL_QUEUES) > now:
            return {}
        paused = {r[0] for r in self.q("SELECT queue FROM pause WHERE until > ?", (now,))}
        only, args = "", [now, now, now]
        if sites is not None:
            only = f" AND u.owner_site IN ({','.join('?' * len(sites))})"
            args += sorted(sites)
        by_id, id_args = ids_clause("u.id", ids)
        rows = self.q(f"""SELECT DISTINCT h.queue, h.host FROM host h JOIN url u ON u.host = h.host
                          WHERE u.state = 'pending' AND u.next_at <= ? AND h.cooldown_until <= ?
                            AND h.not_before <= ? AND h.queue NOT IN ('?', ''){only}{by_id}""", [*args, *id_args])
        out: dict[str, list[str]] = {}
        for r in rows:
            if r["queue"] not in paused and net_key(network_of(r["queue"])) not in paused:
                out.setdefault(r["queue"], []).append(r["host"])
        return out

    def hosts_of(self, queue: str) -> list[str]:
        return [r["host"] for r in self.q("SELECT host FROM host WHERE queue=?", (queue,))]

    def next_due(self, hosts: list[str], ids: Iterable[int] | None = None) -> float | None:
        """When the next pending row of these hosts is due (its retry, its host's cool-down or Crawl-delay). ids:
        only these rows (a run given --only)."""
        if not hosts:
            return None
        marks = ",".join("?" * len(hosts))
        by_id, id_args = ids_clause("u.id", ids)
        return self.q(f"SELECT min(max(u.next_at, h.cooldown_until, h.not_before)) FROM url u JOIN host h "
                      f"ON h.host = u.host WHERE u.host IN ({marks}) AND u.state = 'pending'{by_id}",
                      [*hosts, *id_args])[0][0]

    def pending_left(self, ids: Iterable[int] | None = None) -> tuple[int, float | None]:
        """How many URLs still wait (pending or active), and the earliest time one of the pending is due. ids: only
        these rows (a run given --only)."""
        by_id, id_args = ids_clause("id", ids)
        n = self.q(f"SELECT count(*) FROM url WHERE state IN ('pending','active'){by_id}", id_args)[0][0]
        by_id, id_args = ids_clause("u.id", ids)
        nxt = self.q("SELECT min(max(u.next_at, h.cooldown_until, h.not_before)) FROM url u JOIN host h ON h.host=u.host "
                     f"WHERE u.state='pending'{by_id}", id_args)[0][0]
        return n, nxt

    def waiting(self, sites: frozenset[str] | None = None,
                ids: Iterable[int] | None = None) -> tuple[int, float | None]:
        """For the run's end (review 10-26 B1): how many URLs (of these sites; of these rows, for a run given --only)
        are pending or active, and when the soonest pending one is due, its host's cool-down, Crawl-delay and DNS
        back-off and its queue's pauses (its own, its network's, every queue's) counted in."""
        only, args = "", []
        if sites is not None:
            only = f" AND u.owner_site IN ({','.join('?' * len(sites))})"
            args = sorted(sites)
        by_id, id_args = ids_clause("u.id", ids)
        n = self.q(f"SELECT count(*) FROM url u WHERE u.state IN ('pending','active'){only}{by_id}",
                   [*args, *id_args])[0][0]
        due = self.q(f"""SELECT min(max(u.next_at, coalesce(h.cooldown_until, 0), coalesce(h.not_before, 0),
                           coalesce((SELECT max(p.until) FROM pause p WHERE p.queue IN
                             (h.queue, '{ALL_QUEUES}', '{_NET}' || CASE WHEN h.queue LIKE 'ip:140.127.%'
                                                                  THEN 'school' ELSE 'other' END)), 0)))
                         FROM url u LEFT JOIN host h ON h.host = u.host
                         WHERE u.state = 'pending'{only}{by_id}""", [*args, *id_args])[0][0]
        return n, due

    # ---- servers that do not connect (review 10-26 B2)
    def server_failed(self, queue: str, now: float) -> int:
        """One more connection failure of this server in a row: the streak so far."""
        with self.lock:
            self.conn.execute("INSERT INTO server(queue, conn_fail, failed_at) VALUES (?,1,?) ON CONFLICT(queue) DO "
                              "UPDATE SET conn_fail = conn_fail + 1, failed_at = excluded.failed_at", (queue, now))
            return self.conn.execute("SELECT conn_fail FROM server WHERE queue=?", (queue,)).fetchone()[0]

    def server_answered(self, queue: str, now: float) -> None:
        """The server answered (any HTTP status): its streak starts again from 0."""
        self.x("INSERT INTO server(queue, conn_fail, answered_at) VALUES (?,0,?) ON CONFLICT(queue) DO UPDATE SET "
               "conn_fail = 0, answered_at = excluded.answered_at", (queue, now))

    def server_set(self, queue: str, conn_fail: int) -> None:
        self.x("INSERT INTO server(queue, conn_fail) VALUES (?,?) ON CONFLICT(queue) DO UPDATE SET "
               "conn_fail = excluded.conn_fail", (queue, conn_fail))

    def server_fails(self, queue: str) -> int:
        rows = self.q("SELECT conn_fail FROM server WHERE queue=?", (queue,))
        return rows[0][0] if rows else 0

    # ---- servers that err (second review 10-26: 「出错或回『太忙』整台休息」)
    def server_erred(self, queue: str) -> int:
        """One more error of this server in a row (a 5xx, a 408, a timeout, whichever of its names): the streak."""
        with self.lock:
            self.conn.execute("INSERT INTO server(queue, err_fail) VALUES (?,1) ON CONFLICT(queue) DO UPDATE SET "
                              "err_fail = err_fail + 1", (queue,))
            return self.conn.execute("SELECT err_fail FROM server WHERE queue=?", (queue,)).fetchone()[0]

    def server_err_clear(self, queue: str) -> None:
        """The server answered (or rested for its errors): its error streak starts again from 0."""
        self.x("UPDATE server SET err_fail = 0 WHERE queue=? AND err_fail > 0", (queue,))

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

    # ---- a person's word on a row (review 10-26 B7)
    def mark_school_side(self, url_id: int, note: str) -> str:
        """「人看过，确认是学校那边的问题」: the row's result, reason, status and fetched_at as they are now are kept with
        the note, and the result views count the row as the school's (school_side) while they stay so: a new fetch, the
        same answer included, ends the mark for good. Only a result a person may mark (outcome.markable: ValueError for
        any other, what is ours to fix or may be ours, or nothing to fix at all), only an existing row, only with a note
        saying why. An address that redirects is marked by what it leads to as a page's resource (view page_resource:
        the school's attachment link sent to its home page is not_file, second look F5): its one markable result there;
        none, or several different ones, and it is refused (mark the rows it lands on instead). One no page uses (a
        Common Crawl attachment) is marked by what it comes to as a round-1 row of its own (monitor.orphan_result),
        which monitor.orphans reads the mark by, under the same outcome.markable as any other row (record 五·16, the
        owner's option A) 【Claude 的执行办法】: sent to a web page, not_file (the owner's 10-26 decision); its
        redirects landing on no row, what the last hop says: a school error page's address is bad_link, a loop or too
        many hops server_error (a page's attachment in a loop is server_error in page_resource too; review 10-26, O
        review: a loop is server_error by Claude's rule, for the owner to overturn); what is ours (not_discovered,
        unhandled) or no fault (rule, login) is refused. Marked again: the newest note. Returns the result marked.
        Whether a run may be going on is the command's to check (`nknu archive review --mark`, which refuses while
        one runs)."""
        if not (note or "").strip():
            raise ValueError("a school_side mark says why (note)")
        with self.lock:
            row = self.conn.execute("SELECT result, reason, status, fetched_at FROM url_result WHERE id=?",
                                    (url_id,)).fetchone()
            if row is None:
                raise ValueError(f"no url row {url_id}")
            result = row["result"]
            if result == outcome.SCHOOL_SIDE:  # marked already, and nothing changed since
                result = self.conn.execute("SELECT result FROM review_mark WHERE url_id=?", (url_id,)).fetchone()[0]
            if result == "redirect":
                led = sorted({r[0] for r in self.conn.execute("SELECT DISTINCT result FROM page_resource WHERE via=?",
                                                              (url_id,))})
                if not led:  # no page uses it: a round-1 row of its own, by what it comes to there (monitor.orphans:
                    # not_file, bad_link, server_error… whatever it is, markable() below decides)
                    from nknu.archive import monitor

                    alone = monitor.orphan_result(self, url_id)
                    led = [alone] if alone else []
                can = [r for r in led if outcome.markable(r, row["reason"])]
                if len(can) != 1:
                    raise ValueError(
                        f"url row {url_id} redirects, and what it leads to as a page's resource "
                        f"({', '.join(led) or 'nothing: no page uses it'}) is not one result that is the school's to "
                        f"answer for: mark the rows it lands on instead")
                result = can[0]
            if not outcome.markable(result, row["reason"]):
                raise ValueError(f"url row {url_id} came to {result} (reason {row['reason']}), which is not the "
                                 f"school's to answer for: a school_side mark takes only "
                                 f"{', '.join(outcome.SCHOOL_SIDE_RESULTS)} (failed only for "
                                 f"{', '.join(outcome.FAILED_SCHOOL_REASONS)} or {outcome.FAILED_SCHOOL_PREFIX}*)")
            self.conn.execute("INSERT OR REPLACE INTO review_mark(url_id, mark, result, reason, status, fetched_at, "
                              "note, at) VALUES (?,?,?,?,?,?,?,?)",
                              (url_id, outcome.SCHOOL_SIDE, result, row["reason"], row["status"], row["fetched_at"],
                               note.strip(), utc_now()))
            return result

    # ---- links and logs
    def link(self, src: int, dst: str, text: str, verdict: str) -> None:
        self.x("INSERT OR IGNORE INTO link(src, dst, text, verdict) VALUES (?,?,?,?)", (src, dst, text, verdict))

    def video(self, page_id: int, video) -> None:
        """A YouTube video the page links or embeds (links.Video): the newest reading of the page wins."""
        self.x("INSERT OR REPLACE INTO video(page_id, video_id, url, title, how, zone) VALUES (?,?,?,?,?,?)",
               (page_id, video.video_id, video.url, video.title, video.how, video.zone))

    def offsite(self, domain: str, src: int, sample: str) -> None:
        self.x("INSERT INTO offsite(domain, n, first_src, sample) VALUES (?,1,?,?) "
               "ON CONFLICT(domain) DO UPDATE SET n = n + 1", (domain, src, sample))

    def postback(self, site: str, target: str, src: int, argument: str | None = None, text: str | None = None,
                 form_id: str | None = None, decision: str = "unknown") -> None:
        """A button of a site (design §7.4): its first sample's argument, text and form; the platform file's decision
        (press/never/ignore, unknown while it says nothing)."""
        self.x("INSERT INTO postback(site, target, n, sample, argument, text, form_id, decision) VALUES (?,?,1,?,?,?,?,?) "
               "ON CONFLICT(site, target) DO UPDATE SET n = n + 1, decision = excluded.decision",
               (site, target, src, argument, text, form_id, decision))

    def request(self, queue: str, host: str, url: str, purpose: str, started: float, ended: float,
                status: int | None, error: str | None, *, scope: str | None = None, robots_sha: str | None = None,
                attempt: int = 1, method: str | None = None, bytes: int | None = None, new_conn: bool | None = None,
                connect_ms: float | None = None, tls_ms: float | None = None, ip: str | None = None,
                warc: str | None = None, warc_offset: int | None = None, campus: str | None = None) -> None:
        """One request that may have reached the server; scope and robots_sha: what allowed it at that moment; the
        rest is what it did on the wire (fetch.Sent, design §6.2, §6.4) and where it was made from (B4)."""
        self.x("INSERT INTO request(queue, host, url, purpose, started, ended, status, error, scope, robots_sha, attempt, "
               "method, bytes, new_conn, connect_ms, tls_ms, ip, warc, warc_offset, campus) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (queue, host, url, purpose, started, ended, status, error, scope, robots_sha, attempt, method, bytes,
                None if new_conn is None else int(new_conn), connect_ms, tls_ms, ip, warc, warc_offset, campus))

    # ---- what a page uses (design §7.1)
    def set_page_res(self, page_id: int, entries) -> None:
        """A page's resources as this reading of it found them: the rows of an older reading go (the newest wins).
        entries: dicts with res_key, how, role, need and optionally why, res_id, action_id, zone, text; one per res_key
        (the first wins)."""
        with self.lock:
            self.conn.execute("DELETE FROM page_res WHERE page_id=?", (page_id,))
            self._insert_page_res(page_id, entries)

    def add_page_res(self, page_id: int, entries) -> None:
        """More resources of a page (what a tab or an embed box it uses brought: design §4.4); existing keys stay."""
        with self.lock:
            self._insert_page_res(page_id, entries)

    def _insert_page_res(self, page_id: int, entries) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO page_res(page_id, res_key, how, role, need, why, res_id, action_id, zone, text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(page_id, e["res_key"], e["how"], e["role"], e["need"], e.get("why"), e.get("res_id"),
              e.get("action_id"), e.get("zone"), e.get("text")) for e in entries])

    # ---- where seeds came from, and the archive's own facts
    def seed_src(self, url: str, source: str) -> None:
        self.x("INSERT OR IGNORE INTO seed_src(url, source) VALUES (?,?)", (url, source))

    def meta(self, key: str, value: str | None = None) -> str | None:
        """Read meta[key]; with a value, write it first."""
        if value is not None:
            self.x("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else None

    def event(self, level: str, code: str, message: str, host: str | None = None, url: str | None = None) -> None:
        self.x("INSERT INTO event(at, level, code, host, url, message) VALUES (?,?,?,?,?,?)",
               (utc_now(), level, code, host, url, message))


def wall() -> float:
    return time.time()
