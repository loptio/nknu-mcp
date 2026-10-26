"""The offline rehearsal (round-1 design §18.1): the real round-1 crawler (seed_round1, then Crawler.run) on a network
that is the 10-24 archive, read back, never the school's. Then reconcile without R6 and verify, as after a real round,
and a check of the known gaps against a list of what each must look like (`rehearsal_expect.json`).

- The old archive is only read: its frontier.db and viewer-index.db as immutable SQLite files (mode=ro&immutable=1:
  neither written nor locked, their -wal and -shm not even opened), its WARC files opened for reading. Nothing there
  is renamed, written or removed; the rehearsal writes its own directory only.
- The network (ReplayTransport, `offline = True`, so fetch.Client asks for no machine-wide lock): a GET is answered
  with the old archive's response to the same address (scope.canonical on both sides), status, headers and body as
  the record holds them, the one the old url row's WARC file holds first (its status), else the newest. Anything else
  (a POST: the old crawl pressed no button; an attachment, a robots.txt or a probe it never asked for; Google's API) is
  fetch.NotArchived: the crawler fails that URL or press at once (not_archived), never retries it, and holds it against
  no server and no network. The transport plays the connection too (httpcore's trace events: a new connection or a
  kept one, the server's address), so that the request table and the politeness audit read as after a real run.
- Names (OldDNS): a host's address is the old host table's; a host it does not know, or knew without an address,
  gets one fixed private address (UNKNOWN_IP), so its queue is one of its own. Nothing is looked up for real.
- Time (ReplayClock, ReplayEvent): every wait only moves a clock on. Each worker thread keeps a time of its own (its
  server's queue: its gaps and its waits, as if it ran alone), which starts where the latest of them got to; the main
  loop's waits move that latest time on, so that what waits (a retry, a server's rest) comes due. A request's own
  duration on its thread is nil, so no answer counts as slow and the gap is the run's own (2–5 s by default): the
  politeness audit of the rehearsal's request table has to pass like any run's.
- The machine is kept off the network while it runs (offline_guard): a name lookup, a connection or a datagram to
  anything but this machine raises.
- Google's API rows need a key to be sent at all (google_api.key_set): the real key is taken out of the process's
  environment for the rehearsal and a placeholder put in, which only ever reaches ReplayTransport (it answers
  not_archived; the WARC writer redacts the header anyway) 【Claude 的执行办法】.

Reports (under <dir>/reports): seed-<time>.md, reconcile-rehearsal-<time>.md, verify-<time>.md and
rehearsal-<time>.md/.json (what the replay answered and did not, the run's end, the page types right after the
import, the paths of the other reports). check() reads the expectations and the rehearsal's frontier.db (read-only)
and says PASS or FAIL for each, with why (a selection is pages, urls, a read-only SQL, or one of verify's audits:
politeness, scope). The rehearsal's frontier.db carries meta rehearsal:started from the moment it is made and
rehearsal:seeded once seeded (frontier.rehearsal_of): rehearse goes on only in such a directory (refuse: never in the
old archive, the 10-24 one, or a real round-1 archive), and every command that may send a request or look a name up
(seed, run, jscheck, reconcile with R6) refuses it, its hosts' addresses being the old archive's or made up; and
rehearsal:page_types_at_seed, the import's counts by the 10-25 test's types right after the seed (the crawl changes
them later).

What is Claude's (【Claude 的执行办法】): the record a GET is answered with when there are several, the placeholder
key, the fixed address, the per-thread clock, the expectation format (EXPECT_KEYS)."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from warcio.archiveiterator import ArchiveIterator

from nknu.archive import google_api, pagetypes, round1, scope, seeds
from nknu.archive.frontier import REHEARSAL_KEY, REHEARSAL_STARTED_KEY, Frontier, rehearsal_of, round_of
from nknu.crawl.fetch import NotArchived

log = logging.getLogger("nknu.archive.rehearsal")

UNKNOWN_IP = "10.254.254.254"  # a private address: a host the old archive never resolved (one queue of its own)
PLACEHOLDER_KEY = "offline-rehearsal-placeholder"  # never a key: only ReplayTransport ever sees it
END_WAIT_S = 30 * 86400.0  # the rehearsal waits out every retry and rest (on its own clock), never ends "waiting"
POLL_S = 5.0  # the main loop's wait, on the rehearsal's clock
MAIN_REAL_WAIT_S = 0.02  # how long the main loop's wait really lasts (it moves the clock on by POLL_S)
WORKER_REAL_WAIT_S = 0.002  # how long a worker's idle wait really lasts
KEEPALIVE_S = 60.0  # fetch.KEEPALIVE_LIMITS' keepalive_expiry: a connection idle longer is a new one
BATCH = "rehearsal"


# ---------------------------------------------------------------- the old archive, read only


def _immutable(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _key(url: str) -> str:
    return scope.canonical(url) or url


def _probe_of(url: str) -> tuple[str, str] | None:
    """(scheme, host and port) when the address is a missing-page probe (crawler.PROBE_PREFIX, its name random),
    else None."""
    from nknu.archive.crawler import PROBE_PREFIX

    parts = scope.urlsplit(url)
    if parts.query or not re.fullmatch(re.escape(PROBE_PREFIX) + r"[0-9a-f]+\.html", parts.path):
        return None
    return parts.scheme.lower(), parts.netloc.lower()


@dataclass(frozen=True)
class Record:
    warc: str  # relative to the old archive's directory
    offset: int


@dataclass(frozen=True)
class Answer:
    status: int
    reason: bytes
    headers: list[tuple[bytes, bytes]]
    body: bytes


@dataclass(frozen=True)
class OldHost:
    queue: str | None
    ip: str | None
    cname: str | None


def _header_bytes(text: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        return text.encode("utf-8")


class OldArchive:
    """The 10-24 archive's responses by address. Its index is viewer-index.db (response records of warc/**), completed
    by reading what it does not cover (a file it never indexed or indexed only in part, and quarantine/, whose files
    the old url rows still point at): those WARC files are read, never written. Which record answers an address: the
    old url row's WARC file first and, there, the record with the row's status, else the newest of them; an address
    with no old row (a robots.txt), its newest record. A missing-page probe (its name random each time, so never the
    same address twice) is answered with the 10-24 crawler's probe of the same host on the same scheme (review 10-26:
    else soft404 could not be rehearsed at all) 【Claude 的执行办法】."""

    def __init__(self, root: Path):
        self.root = Path(root)
        db = seeds._old_db(self.root)
        if not db.is_file():
            raise FileNotFoundError(f"no frontier.db in the old archive {self.root}")
        self.records: dict[str, list[Record]] = {}
        self.rows: dict[str, tuple[str | None, int | None]] = {}  # canonical -> (its WARC file, its status)
        self.hosts: dict[str, OldHost] = {}
        self.scanned: list[str] = []  # WARC files (or their tails) read because the index did not cover them
        self.probes: dict[tuple[str, str], list[Record]] = {}  # (scheme, host) -> the old probe's records
        self._status: dict[Record, int | None] = {}
        self._lock = threading.Lock()
        conn = _immutable(db)
        try:
            for r in conn.execute("SELECT url, warc, status FROM url"):
                key = _key(r["url"])
                if key not in self.rows or (r["warc"] and not self.rows[key][0]):
                    self.rows[key] = (r["warc"], r["status"])
            for h in conn.execute("SELECT host, queue, ip, cname FROM host"):
                self.hosts[h["host"]] = OldHost(h["queue"], h["ip"], h["cname"])
        finally:
            conn.close()
        self._index()

    def _add(self, url: str, warc: str, offset: int) -> None:
        self.records.setdefault(_key(url), []).append(Record(warc, offset))

    def _index(self) -> None:
        done: dict[str, int] = {}
        idx_db = self.root / "viewer-index.db"
        if idx_db.is_file():
            conn = _immutable(idx_db)
            try:
                for r in conn.execute("SELECT url, warc, offset FROM rec"):
                    self._add(r["url"], r["warc"], r["offset"])
                done = {r["warc"]: r["upto"] for r in conn.execute("SELECT warc, upto FROM indexed")}
            finally:
                conn.close()
        files = sorted([*(self.root / "warc").rglob("*.warc.gz"), *(self.root / "quarantine").rglob("*.warc.gz")])
        for path in files:
            rel = str(path.relative_to(self.root))
            start = done.get(rel, 0)
            if path.stat().st_size <= start:
                continue
            self.scanned.append(rel if start == 0 else f"{rel}（从 {start} 起）")
            self._scan(path, rel, start)
        for recs in self.records.values():
            recs.sort(key=lambda r: (r.warc, r.offset), reverse=True)
        for url, recs in self.records.items():
            if (probe := _probe_of(url)) is not None:
                self.probes.setdefault(probe, []).extend(recs)
        for recs in self.probes.values():
            recs.sort(key=lambda r: (r.warc, r.offset), reverse=True)

    def _scan(self, path: Path, rel: str, start: int) -> None:
        with open(path, "rb") as fh:
            fh.seek(start)
            it = ArchiveIterator(fh)
            try:
                for rec in it:
                    if rec.rec_type == "response":
                        self._add(rec.rec_headers.get_header("WARC-Target-URI") or "", rel, it.get_record_offset())
                    rec.content_stream().read()
            except Exception as exc:  # noqa: BLE001 - a record cut short: what came before it is indexed
                log.warning("%s: read up to a broken record (%s)", rel, type(exc).__name__)

    def status_of(self, rec: Record) -> int | None:
        with self._lock:
            if rec in self._status:
                return self._status[rec]
        try:
            got: int | None = self.read(rec).status
        except Exception:  # noqa: BLE001 - a record that cannot be read answers nothing
            got = None
        with self._lock:
            self._status[rec] = got
        return got

    def find(self, method: str, url: str) -> Record | None:
        """The record that answers this request, or None (not_archived): a GET only (the old crawl never sent a
        POST, so a press is never answered with the page's GET)."""
        if method.upper() != "GET":
            return None
        key = _key(url)
        cands = self.records.get(key)
        if not cands and (probe := _probe_of(key)) is not None:
            cands = self.probes.get(probe)
        if not cands:
            return None
        warc, status = self.rows.get(key, (None, None))
        if warc:
            mine = [c for c in cands if c.warc == warc]
            cands = mine or cands
        if status is not None:
            for c in cands:
                if self.status_of(c) == status:
                    return c
        for c in cands:
            if self.status_of(c) is not None:
                return c
        return None

    def read(self, rec: Record) -> Answer:
        """The response as the record holds it: status, reason phrase, every header line, the body as stored (a body
        stored chunked is unchunked, and its Transfer-Encoding and Content-Encoding lines left out, warcio decoding
        both)."""
        with open(self.root / rec.warc, "rb") as fh:
            fh.seek(rec.offset)
            r = next(iter(ArchiveIterator(fh)))
            h = r.http_headers
            line = (h.statusline or "").strip()
            code, _, phrase = line.partition(" ")
            headers = [(k, v) for k, v in h.headers]
            chunked = any(k.lower() == "transfer-encoding" and "chunked" in v.lower() for k, v in headers)
            if chunked:
                body = r.content_stream().read()
                headers = [(k, v) for k, v in headers if k.lower() not in ("transfer-encoding", "content-encoding",
                                                                         "content-length")]
            else:
                body = r.raw_stream.read()
        return Answer(int(code), _header_bytes(phrase), [(_header_bytes(k), _header_bytes(v)) for k, v in headers],
                      body)


# ---------------------------------------------------------------- names


class OldDNS:
    """The crawler's resolver on the old host table: (queue, address, canonical name), as crawler.resolve() makes them
    (a Google front end's canonical name puts a host on the google queue). A host the old table does not know, or
    knew without an address, gets UNKNOWN_IP."""

    def __init__(self, hosts: Mapping[str, OldHost]):
        self.hosts = dict(hosts)

    def resolve(self, host: str) -> tuple[str, str | None, str | None]:
        from nknu.archive.crawler import _GOOGLE_CNAME

        if host == scope.GOOGLE_SITES:
            return "google", None, host
        old = self.hosts.get(host)
        ip = old.ip if old is not None and old.ip else UNKNOWN_IP
        cname = (old.cname if old is not None and old.cname else host).lower().rstrip(".")
        if (old is not None and old.queue == "google") or (_GOOGLE_CNAME.search(cname) and cname != host):
            return "google", ip, cname
        return f"ip:{ip}", ip, cname

    def ip_of(self, host: str) -> str | None:
        """The address a request to this host reaches, as its trace tells it: none for a host on a named queue
        (Google's APIs and forms, google_api.queue_for) or Google Sites, which the crawler never resolves."""
        if host == scope.GOOGLE_SITES or google_api.queue_for(host) is not None:
            return None
        return self.resolve(host)[1]


# ---------------------------------------------------------------- time


class ReplayClock:
    """The rehearsal's clock. The thread that makes it (the one that runs the crawl's main loop) reads the latest time
    any thread got to; every other thread (a server queue's worker) has a time of its own, starting at that latest time
    when it first asks, moved on only by its own waits. Waiting moves the waiting thread's time on and nothing else
    (no real wait); the latest time follows the furthest thread."""

    def __init__(self, start: float | None = None):
        self._lock = threading.Lock()
        self._latest = time.time() if start is None else float(start)
        self._local = threading.local()
        self._main = threading.get_ident()

    def _mine(self) -> bool:
        return threading.get_ident() != self._main

    def __call__(self) -> float:
        if not self._mine():
            with self._lock:
                return self._latest
        t = getattr(self._local, "t", None)
        if t is None:
            with self._lock:
                t = self._latest
            self._local.t = t
        return t

    def sleep(self, seconds: float) -> None:
        s = max(0.0, float(seconds))
        if not self._mine():
            with self._lock:
                self._latest += s
            return
        t = self() + s
        self._local.t = t
        with self._lock:
            self._latest = max(self._latest, t)

    @property
    def latest(self) -> float:
        with self._lock:
            return self._latest


class ReplayEvent(threading.Event):
    """The crawler's stop event on the rehearsal's clock: wait(timeout) moves the clock on by timeout and really waits
    only a moment (a worker's idle wait, the main loop's poll); set() and is_set() as any event."""

    def __init__(self, clock: ReplayClock, main_wait: float = MAIN_REAL_WAIT_S,
                 worker_wait: float = WORKER_REAL_WAIT_S):
        super().__init__()
        self.clock, self.main_wait, self.worker_wait = clock, main_wait, worker_wait

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        if timeout is None:
            return super().wait()
        self.clock.sleep(timeout)
        real = self.worker_wait if self.clock._mine() else self.main_wait
        return super().wait(min(max(timeout, 0.0), real))


# ---------------------------------------------------------------- the network


class _Stream:
    """What httpcore's trace hands back as a connection: the server's address."""

    def __init__(self, ip: str):
        self.ip = ip

    def get_extra_info(self, name: str):
        return (self.ip, 443) if name == "server_addr" else None


@dataclass
class ReplayStats:
    answered: Counter = field(default_factory=Counter)  # host -> GETs answered from the old archive
    not_archived: Counter = field(default_factory=Counter)  # (method, host) -> requests with no answer there
    statuses: Counter = field(default_factory=Counter)
    probes: Counter = field(default_factory=Counter)  # missing-page probes: answered (an old probe), not_archived
    lock: threading.Lock = field(default_factory=threading.Lock)

    def as_dict(self) -> dict:
        with self.lock:
            return {"answered": sum(self.answered.values()), "not_archived": sum(self.not_archived.values()),
                    "probes": {k: self.probes[k] for k in ("answered", "not_archived")},
                    "not_archived_by_method": dict(Counter(m for (m, _h) in self.not_archived.elements())),
                    "statuses": {str(k): v for k, v in sorted(self.statuses.items())},
                    "answered_by_host": dict(self.answered.most_common()),
                    "not_archived_by_host": {f"{m} {h}": n for (m, h), n in self.not_archived.most_common()}}


class ReplayTransport(httpx.BaseTransport):
    """An httpx transport that reaches no server (offline = True: fetch.Client asks for no machine-wide lock): a GET is
    answered from the old archive, anything it does not hold raises fetch.NotArchived. One per client (a worker's):
    it plays that client's one kept connection, a new one when the address changes or it sat idle past
    KEEPALIVE_S, through the request's trace."""

    offline = True

    def __init__(self, old: OldArchive, dns: OldDNS, clock: Callable[[], float], stats: ReplayStats | None = None):
        self.old, self.dns, self.clock = old, dns, clock
        self.stats = stats if stats is not None else ReplayStats()
        self._conn: tuple[str | None, float] | None = None

    def _connect(self, request: httpx.Request, ip: str | None) -> _Stream | None:
        trace = request.extensions.get("trace")
        stream = _Stream(ip) if ip else None
        now = self.clock()
        new = self._conn is None or self._conn[0] != ip or now - self._conn[1] > KEEPALIVE_S
        if trace is not None:
            if new:
                trace("connection.connect_tcp.started", {})
                trace("connection.connect_tcp.complete", {"return_value": stream})
            trace("http11.send_request_headers.started", {"request": request})
            trace("http11.send_request_headers.complete", {})
        self._conn = (ip, now)
        return stream

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = (request.url.host or "").lower()
        stream = self._connect(request, self.dns.ip_of(host))
        rec = self.old.find(request.method, url)
        probe = request.method.upper() == "GET" and _probe_of(url) is not None
        if rec is None:
            with self.stats.lock:
                self.stats.not_archived[(request.method, host)] += 1
                self.stats.probes["not_archived"] += probe
            raise NotArchived(f"{url}: the old archive holds no answer to this {request.method}")
        got = self.old.read(rec)
        with self.stats.lock:
            self.stats.answered[host] += 1
            self.stats.statuses[got.status] += 1
            self.stats.probes["answered"] += probe
        ext: dict[str, Any] = {"http_version": b"HTTP/1.1", "reason_phrase": got.reason}
        if stream is not None:
            ext["network_stream"] = stream
        return httpx.Response(got.status, headers=got.headers, content=got.body, request=request, extensions=ext)


# ---------------------------------------------------------------- off the network


def _local(host) -> bool:
    if str(host).lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class OffNetwork(OSError):
    """Something in the rehearsal tried to reach the network."""


@contextlib.contextmanager
def offline_guard() -> Iterator[None]:
    """While it holds, no name is looked up and nothing is sent to anything but this machine (a tripwire: OffNetwork),
    in every thread. What was there before is put back after."""

    def far(address) -> None:
        if isinstance(address, tuple) and address and not _local(address[0]):
            raise OffNetwork(f"rehearsal guard: nothing is sent to {address[0]!r}")

    real_gai = socket.getaddrinfo
    real = {n: getattr(socket.socket, n) for n in ("connect", "connect_ex", "sendto", "sendmsg")
            if hasattr(socket.socket, n)}
    saved = {(socket, n): getattr(socket, n) for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex")}
    saved.update({(socket.socket, n): fn for n, fn in real.items()})

    def getaddrinfo(host, *args, **kwargs):
        if _local(host):
            return real_gai(host, *args, **kwargs)
        raise OffNetwork(f"rehearsal guard: no name lookup for {host!r}")

    def by_name(host, *args, **kwargs):
        raise OffNetwork(f"rehearsal guard: no name lookup for {host!r}")

    def connect(self, address):
        far(address)
        return real["connect"](self, address)

    def connect_ex(self, address):
        far(address)
        return real["connect_ex"](self, address)

    def sendto(self, data, *rest):
        far(rest[-1] if rest else None)
        return real["sendto"](self, data, *rest)

    def sendmsg(self, buffers, *rest):
        far(rest[2] if len(rest) > 2 else None)
        return real["sendmsg"](self, buffers, *rest)

    patched = {(socket, "getaddrinfo"): getaddrinfo, (socket, "gethostbyname"): by_name,
               (socket, "gethostbyname_ex"): by_name}
    patched.update({(socket.socket, n): fn for n, fn in (("connect", connect), ("connect_ex", connect_ex),
                                                         ("sendto", sendto), ("sendmsg", sendmsg)) if n in real})
    for (owner, name), value in patched.items():
        setattr(owner, name, value)
    try:
        yield
    finally:
        for (owner, name), value in saved.items():
            setattr(owner, name, value)


@contextlib.contextmanager
def placeholder_key() -> Iterator[None]:
    """The rehearsal's process holds no Google key: whatever GOOGLE_API_KEY held is out of the environment while it
    runs, and a placeholder in, so that the Drive and calendar rows are sent (to ReplayTransport, which answers
    not_archived) instead of waiting for a key. Put back as it was after."""
    before = os.environ.pop(google_api.KEY_ENV, None)
    os.environ[google_api.KEY_ENV] = PLACEHOLDER_KEY
    try:
        yield
    finally:
        os.environ.pop(google_api.KEY_ENV, None)
        if before is not None:
            os.environ[google_api.KEY_ENV] = before


# ---------------------------------------------------------------- the rehearsal


class RehearsalRefused(RuntimeError):
    """The directories given would touch the old archive, or the rehearsal's directory is not one."""


@dataclass
class Rehearsed:
    root: Path
    resumed: bool
    run_end: dict
    stats: dict
    page_types: dict | None
    seed_report: Path | None
    reconcile_report: Path | None
    reconcile_gates: dict[str, bool]
    verify_report: Path | None
    verify_code: int | None
    verify_conclusion: str | None
    summary: Path
    seconds: float
    clock_span_h: float
    scanned: list[str] = field(default_factory=list)
    id_probes: dict[str, int] = field(default_factory=dict)  # gone_id's probes sent, and how many were not_archived


def _inside(a: Path, b: Path) -> bool:
    """Whether a is b or inside it."""
    try:
        a.resolve().relative_to(b.resolve())
        return True
    except ValueError:
        return False


def refuse(old: Path, root: Path) -> None:
    """The rehearsal writes only its own directory: never the old archive, nor anything inside it or around it, nor an
    archive that is no rehearsal's: one with no round mark is the 10-24 crawler's; one with the round mark and no
    rehearsal mark (frontier.rehearsal_of) is a real round-1 archive (a pilot's, archive/ after the rename: review
    10-26, a --dir mistyped once would have seeded it, replayed the old answers into its WARC as new ones and marked it
    a rehearsal's). A directory with a frontier.db goes on only when it is a rehearsal's own."""
    if _inside(root, old) or _inside(old, root):
        raise RehearsalRefused(f"彩排的资料夹 {root} 和旧存档 {old} 重叠：旧存档只读，彩排要另开资料夹")
    db = Path(root) / "frontier.db"
    if db.exists() and round_of(db) is None:
        raise RehearsalRefused(f"{root} 里是 10-24 旧爬虫的存档（没有第一轮的标记）：彩排不在它上面跑")
    if db.exists() and rehearsal_of(db) is None:
        raise RehearsalRefused(f"{root} 里是第一轮的真存档（有第一轮的标记、没有彩排的标记）：彩排不在它上面跑，"
                               "要另开一个空资料夹")
    if not seeds._old_db(old).is_file():
        raise RehearsalRefused(f"找不到旧存档的 frontier.db：{old}")


def rehearse(old: Path, root: Path, *, sites: Sequence[str] | None = None, plan: round1.Plan | None = None,
             facts_db: Path | None = None, cc_dir: Path | None = seeds.CC_DIR, probe: Path | None = seeds.PROBE,
             jstest: Path | None = pagetypes.JSTEST_DIR,
             gap: tuple[float, float] | None = None, after: bool = True,
             say: Callable[[str], None] = lambda s: log.info("%s", s)) -> Rehearsed:
    """Seed (unless root holds a rehearsal's frontier already: then the run goes on), run on the old archive, then
    reconcile (no R6) and verify (after=False: neither, for a test of the run alone). Everything under offline_guard and
    placeholder_key. Returns what it came to; its summary is in reports/rehearsal-<time>.md and .json."""
    from nknu.archive import crawler as crawl_mod
    from nknu.archive import monitor, reconcile

    old, root = Path(old).resolve(), Path(root).resolve()
    refuse(old, root)
    plan = plan if plan is not None else round1.load()
    t0 = time.monotonic()
    root.mkdir(parents=True, exist_ok=True)
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    only = frozenset(sites) if sites else None
    with offline_guard(), placeholder_key():
        say(f"读旧存档的索引（只读）：{old}")
        archive = OldArchive(old)
        say(f"旧存档：{sum(len(v) for v in archive.records.values()):,} 笔回应、{len(archive.rows):,} 个旧网址、"
            f"{len(archive.hosts)} 台主机；索引没涵盖、这次补读的 WARC：{len(archive.scanned)} 个")
        dns = OldDNS(archive.hosts)
        clock = ReplayClock()
        start_clock = clock.latest
        stats = ReplayStats()

        def client(warc):
            return crawl_mod.archive_client(warc, transport=ReplayTransport(archive, dns, clock, stats),
                                            clock=clock, sleep=clock.sleep)

        resumed = (root / "frontier.db").exists()
        lock = crawl_mod.hold_run_lock(root)
        seed_path, page_types = None, None
        try:
            if not resumed:  # a rehearsal's from the moment the file is made, before the crawler or the seed touch it
                made = Frontier(root / "frontier.db")
                made.meta(REHEARSAL_STARTED_KEY, stamp)
                made.close()
            crawler = crawl_mod.Crawler(root, plan=plan, clock=clock, sleep=clock.sleep, resolver=dns.resolve,
                                        client_factory=client, netcheck=lambda: True, campus_url=None,
                                        local_address=lambda: None, gap=gap or crawl_mod.GAP, sites=only,
                                        end_wait_s=END_WAIT_S)
            crawler.stop = ReplayEvent(clock)
            f = crawler.frontier
            if f.meta(REHEARSAL_KEY) is None:  # not seeded yet (refuse(): a frontier.db already there is a rehearsal's)
                say("seed：第一轮起点（旧存档只读）")
                imp = pagetypes.import_jstest(f, Path(jstest)) if jstest is not None else None
                report = seeds.seed_round1(crawler, old=old, facts_db=facts_db, cc_dir=cc_dir, probe=probe, jstest=None)
                if imp is not None:
                    report.page_types = imp.lines()
                    page_types = {"by_old": dict(imp.by_old), "by_key": dict(imp.by_key),
                                  "click_profiled": imp.notes.get("click_profiled", 0),
                                  "notes": dict(imp.notes), "merged": len(imp.merged)}
                    f.meta("rehearsal:page_types_at_seed", json.dumps(page_types, ensure_ascii=False))
                seed_path = reports / f"seed-{stamp}.md"
                seed_path.write_text(report.text(), encoding="utf-8")
                f.meta(REHEARSAL_KEY, stamp)
                f.meta("rehearsal:old", str(old))
                say(f"seed 完：待抓清单 {f.q('SELECT count(*) FROM url')[0][0]:,} 行；起点报告 {seed_path}")
            else:
                got = f.meta("rehearsal:page_types_at_seed")
                page_types = json.loads(got) if got else None
                say("彩排库已经 seed 过：接着跑，不重新 seed")
            say("run：真的爬虫程序，网络换成旧存档")
            crawler.run(poll_s=POLL_S)
            end = f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")
            run_end = json.loads(end[0][0]) if end else {}
            got_ids = f.q("SELECT count(*), coalesce(sum(error LIKE 'NotArchived%'), 0) FROM request "
                          "WHERE purpose='id_probe'")[0]
            id_probes = {"sent": got_ids[0], "not_archived": got_ids[1]}
            f.close()
        finally:
            lock.close()
        say(f"run 完：{run_end.get('reason')}；旧存档回答 {sum(stats.answered.values()):,} 个，"
            f"not_archived {sum(stats.not_archived.values()):,} 个")
        rec_path, gates, ver_path, ver_code, ver_conc = None, {}, None, None, None
        if after:
            say("reconcile（不含 R6）")
            got_rec, rec_path = reconcile.reconcile(root, batch=BATCH, sites=list(sites) if sites else None, plan=plan,
                                                    sources=reconcile.Sources(cc_dir=cc_dir, old=old))
            gates = {r.site: bool(r.gates_ok) for r in got_rec.sites}
            say("verify")
            ver = monitor.verify(root, plan=plan, resource_check=monitor.extract_check(plan))
            ver_path, ver_code, ver_conc = ver.path, ver.code, monitor.conclusion(ver.checks)
    done = Rehearsed(root=root, resumed=resumed, run_end=run_end, stats=stats.as_dict(), page_types=page_types,
                     seed_report=seed_path, reconcile_report=rec_path, reconcile_gates=gates, verify_report=ver_path,
                     verify_code=ver_code, verify_conclusion=ver_conc, summary=reports / f"rehearsal-{stamp}.md",
                     seconds=time.monotonic() - t0, clock_span_h=(clock.latest - start_clock) / 3600,
                     scanned=archive.scanned, id_probes=id_probes)
    _write_summary(done, old, sites, stamp)
    return done


def _write_summary(r: Rehearsed, old: Path, sites: Sequence[str] | None, stamp: str) -> None:
    s = r.stats
    w = [f"# 离线彩排 {stamp}", "",
         f"- 旧存档（只读）：{old}", f"- 彩排资料夹：{r.root}", f"- 网站：{'、'.join(sites) if sites else '第一轮全部'}",
         (f"- {'接着上一次跑（没有重新 seed）' if r.resumed else '新 seed'}；实际花了 {r.seconds / 60:.1f} 分钟，"
         f"彩排时钟走了 {r.clock_span_h:.1f} 小时（最慢那条队的等待加起来）"),
         f"- run 结束：{r.run_end.get('reason')}（剩 {r.run_end.get('pending')} 个网址、{r.run_end.get('presses')} 个按钮）",
         (f"- 旧存档回答的 GET：{s['answered']:,}；没有回应（not_archived）：{s['not_archived']:,}"
         f"（{'、'.join(f'{k} {v:,}' for k, v in s['not_archived_by_method'].items()) or '无'}）"),
         f"- 回答的状态码：{'、'.join(f'{k} {v:,}' for k, v in s['statuses'].items()) or '无'}"]
    if r.page_types:
        by_old = r.page_types["by_old"]
        w.append("- 导入时的网页类型（按旧类型）：" + "、".join(f"{k} {by_old.get(k, 0)}" for k in
                                                       ("ok", "click", "js", "frame", "todo"))
                 + f"，合计 {sum(by_old.values())}；click_profiled {r.page_types['click_profiled']}")
    if r.scanned:
        w.append(f"- 索引没涵盖、这次补读的 WARC：{'、'.join(r.scanned)}")
    w += ["", "## 报告", ""]
    for name, p in (("起点", r.seed_report), ("对账（不含 R6）", r.reconcile_report), ("验收", r.verify_report)):
        w.append(f"- {name}：{p if p else '（没有）'}")
    if r.verify_code is not None:
        w.append(f"- 验收结论：{r.verify_conclusion}（退出码 {r.verify_code}）")
    if r.reconcile_gates:
        ok = sum(r.reconcile_gates.values())
        w.append(f"- 对账：{len(r.reconcile_gates)} 个网站，关卡全过 {ok} 个（R6 没做，所以本来就不会全过）")
    probes, ids = s["probes"], r.id_probes
    w += ["", "## 彩排测不到的（盲区）", "",
          (f"- 「看起来像不存在」（soft404）：探针的网址每次是随机的，旧存档不会有同一个网址，所以用旧存档里同一主机、同一协议"
           f"的旧探针回答（旧爬虫每台主机打过一次）。这次 {probes['answered']} 个探针用旧探针回答，"
           f"{probes['not_archived']} 个旧存档没有（not_archived），那些主机的 soft404 测不到"),
          (f"- 「编号不存在」（gone_id）：id 探针（SSO 的 40 个 0、c.nknu 的 nid=999999999 这类）旧爬虫没打过，全是 "
           f"not_archived，彩排测不到：这次送出 {ids.get('sent', 0)} 个，not_archived {ids.get('not_archived', 0)} 个"),
          "- 没按过的按钮（POST）、Google API（云端硬盘、日历）：旧存档没有回应，全是 not_archived，只测得到「失败要照实记」"]
    w += ["", "## not_archived 最多的主机（前 30）", ""]
    w += [f"- {k}：{v:,}" for k, v in list(s["not_archived_by_host"].items())[:30]]
    w.append("")
    r.summary.write_text("\n".join(w), encoding="utf-8")
    data = {k: (str(v) if isinstance(v, Path) else v) for k, v in r.__dict__.items()}
    r.summary.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                                              encoding="utf-8")


# ---------------------------------------------------------------- the check against the expectations

# the keys an expectation may use (【Claude 的执行办法】); anything else is an error of the expectation file, so a typo
# never passes for want of a check
SELECT_KINDS = ("pages", "urls", "sql", "audit")
AUDITS = ("politeness", "scope")  # monitor.politeness, monitor.scope_audit (against the round-1 list): one row a breach
PAGE_FILTERS = ("url", "urls", "url_like", "site", "page_type", "page_types", "where")
URL_FILTERS = PAGE_FILTERS
EXPECT_KEYS = ("n", "n_min", "n_max", "level", "level_not", "own", "own_not", "reasons_has", "reasons_not", "label_not",
               "result", "result_not", "state", "reason", "flags_has", "each", "each_not", "each_has", "each_lacks",
               "value", "value_min", "value_max", "row")


@dataclass(frozen=True)
class ItemResult:
    id: str
    ok: bool
    why: str
    note: str = ""


def _list(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _items(text: str | None) -> set[str]:
    return {x for x in (text or "").split(",") if x}


class _Db:
    """The rehearsal's frontier.db, read only (the views in this connection's temp schema), page_completeness worked
    out once."""

    def __init__(self, root: Path):
        self.f = Frontier(Path(root) / "frontier.db", readonly=True)
        self._pages: list[dict] | None = None

    def pages(self) -> list[dict]:
        if self._pages is None:
            rows = self.f.q("SELECT pc.id, pc.site, pc.page_type, pc.own, pc.level, pc.label, pc.reasons, u.url "
                            "FROM page_completeness pc JOIN url u ON u.id = pc.id")
            self._pages = [dict(r) for r in rows]
        return self._pages

    def close(self) -> None:
        self.f.close()


def _where(filters: Mapping[str, Any], alias: str, allowed: Sequence[str]) -> tuple[str, list]:
    unknown = set(filters) - set(allowed)
    if unknown:
        raise ValueError(f"unknown filter {sorted(unknown)}")
    sql, args = [], []
    if "url" in filters:
        sql.append(f"{alias}.url = ?")
        args.append(_key(filters["url"]))
    if "urls" in filters:
        urls = [_key(u) for u in filters["urls"]]
        sql.append(f"{alias}.url IN ({','.join('?' * len(urls))})")
        args += urls
    if "url_like" in filters:
        sql.append(f"{alias}.url LIKE ?")
        args.append(filters["url_like"])
    if "where" in filters:
        sql.append(f"({filters['where']})")
    return (" AND ".join(sql) or "1"), args


def _select(db: _Db, sel: Mapping[str, Any]) -> list[dict]:
    kinds = [k for k in SELECT_KINDS if k in sel]
    if len(kinds) != 1 or set(sel) - {*SELECT_KINDS, "params"}:
        raise ValueError(f"select needs exactly one of {SELECT_KINDS} (got {sorted(sel)})")
    kind = kinds[0]
    if kind == "audit":
        from nknu.archive import monitor

        if sel["audit"] not in AUDITS:
            raise ValueError(f"unknown audit {sel['audit']!r} (one of {AUDITS})")
        found = monitor.politeness(db.f) if sel["audit"] == "politeness" else monitor.scope_audit(db.f, round1.load())
        return [{"breach": x} for x in found]
    if kind == "sql":
        rows = db.f.q(sel["sql"], tuple(sel.get("params", ())))
        return [dict(r) for r in rows]
    filters = dict(sel[kind])
    if kind == "pages":
        wanted = None
        site, ptype = filters.pop("site", None), filters.pop("page_type", None)
        ptypes = filters.pop("page_types", None)
        if filters:
            where, args = _where(filters, "u", PAGE_FILTERS)
            wanted = {r[0] for r in db.f.q(f"SELECT u.id FROM url u WHERE {where}", tuple(args))}
        out = []
        for p in db.pages():
            if wanted is not None and p["id"] not in wanted:
                continue
            if site is not None and p["site"] != site:
                continue
            if ptype is not None and p["page_type"] != ptype:
                continue
            if ptypes is not None and p["page_type"] not in ptypes:
                continue
            out.append(p)
        return out
    site = filters.pop("site", None)
    ptype = filters.pop("page_type", None)
    ptypes = filters.pop("page_types", None)
    where, args = _where(filters, "r", URL_FILTERS)
    if site is not None:
        where += " AND r.owner_site = ?"
        args.append(site)
    if ptype is not None:
        where += " AND r.page_type = ?"
        args.append(ptype)
    if ptypes is not None:
        where += f" AND r.page_type IN ({','.join('?' * len(ptypes))})"
        args += list(ptypes)
    rows = db.f.q(f"SELECT r.* FROM url_result r WHERE {where}", tuple(args))
    return [dict(r) for r in rows]


def _row_name(r: Mapping[str, Any]) -> str:
    return str(r.get("url") or r.get("id") or r.get("breach") or {k: r[k] for k in list(r)[:3]})


def _judge(rows: list[dict], exp: Mapping[str, Any]) -> list[str]:
    """What does not hold, in words (empty: PASS)."""
    unknown = set(exp) - set(EXPECT_KEYS)
    if unknown:
        raise ValueError(f"unknown expectation {sorted(unknown)}")
    bad: list[str] = []
    n = len(rows)
    some = f"（例如 {'；'.join(_row_name(r) for r in rows[:3])}）" if rows else ""
    if "n" in exp and n != exp["n"]:
        bad.append(f"选到 {n} 行，预期 {exp['n']} 行" + (some if n > exp["n"] else ""))
    if "n_min" in exp and n < exp["n_min"]:
        bad.append(f"选到 {n} 行，预期至少 {exp['n_min']} 行")
    if "n_max" in exp and n > exp["n_max"]:
        bad.append(f"选到 {n} 行，预期至多 {exp['n_max']} 行{some}")
    each = {k: _list(v) for k, v in exp.get("each", {}).items()}
    each_not = {k: _list(v) for k, v in exp.get("each_not", {}).items()}
    each_has = {k: _list(v) for k, v in exp.get("each_has", {}).items()}
    each_lacks = {k: _list(v) for k, v in exp.get("each_lacks", {}).items()}
    for col in ("level", "own", "result", "state", "reason"):
        if col in exp:
            each[col] = _list(exp[col])
        if f"{col}_not" in exp:
            each_not[col] = _list(exp[f"{col}_not"])
    if "label_not" in exp:
        each_not["label"] = _list(exp["label_not"])
    if "reasons_has" in exp:
        each_has["reasons"] = _list(exp["reasons_has"])
    if "reasons_not" in exp:
        each_lacks["reasons"] = _list(exp["reasons_not"])
    if "flags_has" in exp:
        each_has["flags"] = _list(exp["flags_has"])
    # a column the selected rows do not have is a FAIL, never a pass (review 10-26: result_not on pages, which have own
    # and level only, or a column name mistyped, read None for every row and passed): NULL in a column that is there
    # is a value 【Claude 的执行办法】
    cols = sorted({*each, *each_not, *each_has, *each_lacks})
    absent = [c for c in cols if any(c not in r for r in rows)]
    for c in absent:
        bad.append(f"栏位 {c} 不在选到的行里（这种选法的行有 {sorted(rows[0])}）")
    if absent:
        return bad
    for r in rows:
        for col, allowed in each.items():
            if r.get(col) not in allowed:
                bad.append(f"{_row_name(r)}：{col}={r.get(col)!r}，预期 {allowed}")
        for col, banned in each_not.items():
            if r.get(col) in banned:
                bad.append(f"{_row_name(r)}：{col}={r.get(col)!r}，不能是 {banned}")
        for col, need in each_has.items():
            missing = [x for x in need if x not in _items(r.get(col))]
            if missing:
                bad.append(f"{_row_name(r)}：{col}={r.get(col)!r}，缺 {missing}")
        for col, banned in each_lacks.items():
            there = [x for x in banned if x in _items(r.get(col))]
            if there:
                bad.append(f"{_row_name(r)}：{col}={r.get(col)!r}，不能含 {there}")
    if any(k in exp for k in ("value", "value_min", "value_max", "row")):
        first = list(rows[0].values()) if rows else None
        if first is None:
            bad.append("没有结果行")
        else:
            v = first[0]
            if "value" in exp and v != exp["value"]:
                bad.append(f"值是 {v!r}，预期 {exp['value']!r}")
            if "value_min" in exp and (v is None or v < exp["value_min"]):
                bad.append(f"值是 {v!r}，预期至少 {exp['value_min']!r}")
            if "value_max" in exp and (v is None or v > exp["value_max"]):
                bad.append(f"值是 {v!r}，预期至多 {exp['value_max']!r}")
            if "row" in exp and first != list(exp["row"]):
                bad.append(f"第一行是 {first!r}，预期 {list(exp['row'])!r}")
    return bad


def load_expectations(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    seen: set[str] = set()
    for it in items:
        if not it.get("id") or "select" not in it or "expect" not in it:
            raise ValueError(f"an expectation needs id, select and expect: {str(it)[:200]}")
        if it["id"] in seen:
            raise ValueError(f"the expectation id {it['id']} is there twice")
        seen.add(it["id"])
    return items


def check(expect_path: Path, root: Path) -> list[ItemResult]:
    """Each expectation against the rehearsal's frontier.db (read only): PASS when everything it says holds, FAIL with
    what does not (a selection that cannot be read is a FAIL too, never a pass)."""
    items = load_expectations(expect_path)
    db = _Db(root)
    out = []
    try:
        for it in items:
            try:
                rows = _select(db, it["select"])
                bad = _judge(rows, it["expect"])
            except (ValueError, sqlite3.Error) as exc:
                out.append(ItemResult(it["id"], False, f"读不出来：{type(exc).__name__}: {exc}", it.get("说明", "")))
                continue
            if bad:
                more = f"（另有 {len(bad) - 5} 处）" if len(bad) > 5 else ""
                out.append(ItemResult(it["id"], False, "；".join(bad[:5]) + more, it.get("说明", "")))
            else:
                out.append(ItemResult(it["id"], True, f"{len(rows)} 行都对", it.get("说明", "")))
    finally:
        db.close()
    return out
