"""The archive crawl: one worker thread per server queue (design §3.3), each sending one request at a time and waiting
2–5 s after every response ends, whichever host on that server it fetches next. Each request opens its own connection
and closes it with the response (no keep-alive), so a server never sees two connections from us.

- One run per archive directory: `archive run` holds run.lock (an flock the kernel drops when the process ends, even
  after kill -9) for its whole life; a second run refuses to start, and only the holder requeues rows or clears STOP.
- Queue = the server that answers: hosts are resolved (getaddrinfo, the lowest IPv4 address) and queued by IP;
  Google-hosted names share one named queue, since their IPs rotate. A name that does not resolve is tried again after
  10 min, doubling up to 6 h (its URLs wait as pending), and given up after DNS_GIVE_UP tries (`archive retry dns`).
- Redirects are not followed inside a request: the target is queued as its own URL, on its own server's queue. http
  and https are two URLs, as written; a server that serves only one of them redirects (or refuses) the other.
- robots.txt is read per host before its first page (robots.py); a 404 probe per host records what a missing page
  looks like, so pages that answer 200 with that look are flagged instead of trusted. A Crawl-delay longer than the
  2–5 s gap (at most 300 s) is the host's own wait (host.not_before): its rows come back when it is over, while the
  other hosts on that server go on and the queue's thread never sleeps longer than the gap, so a stop is seen at once.
- Every response goes to WARC (one file per queue per run), 4xx included; a page with a password form is archived but
  its links are not followed. A WARC write that fails, or free disk under 5 GB, stops the whole run.
- Every request that may have reached a server is logged and followed by the full gap, whatever happens next.
- Failures: a 4xx (but 408/429) is the server's answer, archived as it is and never retried. Timeouts, 5xx and
  connection errors open the client's breaker; the host then cools down 15 min, doubling while the breaker keeps
  opening, back to 0 at the host's next answer. Ten 401/403/451 in a row from one directory (host + first two
  directories of the path) make that directory's URLs wait a fixed 15 min; the rest of the host goes on. A Retry-After
  longer than the client waits (on a page, robots.txt or the probe) rests the whole server queue that long (at least
  15 min, at most 24 h).
- Network down: a connection error on a network (school 140.127.* or other) that answered nothing for 2 min asks the
  connectivity check (a fresh one once three of its queues failed); only a failed check pauses, and only the networks
  that answered nothing for 2 min (all of them: every queue). The pause lasts 5 min, ends when a fresh check passes,
  and the connection failures of those networks in the last 5 min are not held against any URL or host.
- Nothing is parsed into facts here.
"""

from __future__ import annotations

import fcntl
import ipaddress
import logging
import math
import os
import random
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from nknu.archive import scope, seeds
from nknu.archive.frontier import ALL_QUEUES, NETWORKS, Frontier, net_key, network_of, wall
from nknu.archive.links import extract
from nknu.archive.robots import (
    FRESH_S,
    MAX_BODY,
    MAX_CRAWL_DELAY_S,
    RETRY_UNREACHABLE_S,
    Robots,
    decode,
    judge_answer,
    unparsed,
)
from nknu.core.pii import is_honor_title, mask_honor_names
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import (
    BREAKER_THRESHOLD,
    ArchiveWriteError,
    Client,
    DecodeError,
    FetchError,
    HostCircuitOpen,
    RetryableStatus,
    RetryLater,
    Snapshot,
    TooLarge,
    UnwantedType,
)
from nknu.crawl.warc import WarcSink

log = logging.getLogger("nknu.archive")

MAX_BYTES = 50 * 1024 * 1024
SITE_CAP = 4000  # pages per site in this first pass; more are recorded as skipped (site_cap) and reported
FILE_CAP = 6000  # attachments per site, counted apart from the pages
VARIANT_CAP = 2000  # URLs per site + path (query variants), platform paths aside: a guard against endless calendars
MAX_DEPTH = 15
GAP = (2.0, 5.0)
RETRY_AFTER_S = 1800  # a failed URL is tried again after 30 min × attempts (plus up to 5 min), three attempts in all
RETRY_JITTER_S = 300.0
MAX_ATTEMPTS = 3
COOLDOWN = (900.0, 86400.0)  # a host whose breaker opened waits 15 min, doubling while it keeps opening, at most a day
IDLE_EXIT_S = 30
IDLE_POLL_S = 5.0  # an idle queue looks again this often, or when its next row is due if that is sooner
PAGE_4XX = frozenset(range(400, 500)) - {408, 429}  # answers, archived as they are: 408 and 429 are retried instead
ROBOTS_4XX = frozenset(range(400, 500)) - {429}  # RFC 9309: any 4xx for robots.txt means no rules (429 is retried)
DENY_STATUSES = frozenset({401, 403, 451})
DENY_STREAK = 10  # this many URLs of one directory refused in a row: that directory waits DENY_WAIT_S
DENY_WAIT_S = 900.0  # fixed: a protected folder never grows into a day-long wait
UNREACHABLE_TRIES = 3  # robots.txt unreachable this many hours in a row: the host's URLs wait for the lab machine
ROBOTS_HOPS = 5
REDIRECTS = (301, 302, 303, 307, 308)
DNS_RETRY_S = 600.0  # a name that did not resolve is tried again after 10 min, doubling, at most 6 h
DNS_RETRY_MAX_S = 6 * 3600.0
DNS_GIVE_UP = 8  # tries (about a day): then its URLs fail with reason dns, so the run can end
NET_KINDS = frozenset({"dns", "connect"})  # failures that may be our network's, not the server's
TRANSPORT_KINDS = frozenset({"dns", "connect", "tls", "timeout"})  # the request reached no server (and net_<type>)
NET_WINDOW_S = 120.0
NET_QUEUES = 3
NET_PAUSE_S = 300.0
NET_UNDO_S = 300.0
NETCHECK_CACHE_S = 30.0
MIN_FREE_BYTES = 5 * 1024**3
DIRECTORY_TIER = 3  # an off-campus site the official directory links to: crawled like a centre's site
STOP_POLL_S = 1.0  # how often `archive run` looks for STOP, and `archive stop` for the lock to be let go
STOP_WAIT_S = 600.0
NO_RATE = RateLimit(per_host_concurrency=1, min_interval_s=0.0, jitter_s=0.0)  # the worker waits, per queue
# one connection at a time, none kept after its response: the next request on that server, whichever name it uses,
# opens a fresh one (a TLS handshake each time, small next to the 2–5 s gap)
ARCHIVE_LIMITS = httpx.Limits(max_connections=1, max_keepalive_connections=0)
_HTML = ("text/html", "application/xhtml+xml")
_LOGIN_TITLE = re.compile(r"(?i)登入|登錄|log ?in|sign ?in")
LOGIN_MAX_LINKS = 15  # a password form on a page with more links is a sidebar member box, not a login page
_GOOGLE_CNAME = re.compile(r"(?:^|\.)(?:googlehosted\.com|ghs\.google\.com|google\.com|googleusercontent\.com)$")
_DNS_TEXT = re.compile(r"(?i)nodename nor servname|name or service not known|temporary failure in name resolution|"
                       r"no address associated|name resolution|getaddrinfo")
_TLS_TEXT = re.compile(r"(?i)certificate|ssl|tls")
_HTTP_TEXT = re.compile(r"HTTP (\d{3})\b")
_CHARSET = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)


class _Stopped(Exception):
    """The run is stopping: no new request goes out; the URL goes back to pending."""


class _Offline(Exception):
    """The network is down: the URL goes back to pending, and nothing counts against it."""


class _DiskLow(Exception):
    """Less than MIN_FREE_BYTES free under the archive directory: the run stops before the WARC files cannot be written."""


class AlreadyRunning(Exception):
    def __init__(self, pid: str):
        super().__init__(f"another archive run holds the lock (pid {pid})")
        self.pid = pid


def hold_run_lock(root: Path):
    """The run's exclusive lock on <root>/run.lock, held until the returned file is closed or the process ends (the
    kernel lets go even after kill -9). Raises AlreadyRunning with the holder's pid."""
    root.mkdir(parents=True, exist_ok=True)
    fh = open(root / "run.lock", "a+")  # noqa: SIM115 - held for the run's whole life; the caller closes it
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        pid = fh.read().strip() or "?"
        fh.close()
        raise AlreadyRunning(pid) from None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def lock_holder(root: Path) -> str | None:
    """The pid written in run.lock while a run holds it; None when no run does."""
    path = root / "run.lock"
    if not path.exists():
        return None
    with open(path) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return fh.read().strip() or "?"
        fcntl.flock(fh, fcntl.LOCK_UN)
        return None


# design §4.7: images are not downloaded in phases 1–2 (OCR of posters comes in phase 3); a URL without an image
# extension is only known by its answer, so the body is not read once the content type says so
SKIP_TYPES = ("image/", "video/", "audio/")


def archive_client(warc: WarcSink | None, **kw) -> Client:
    """The archive crawl's client: a 4xx (but 408/429) comes back as a snapshot, redirects are handed back, and a body
    that cannot be decoded strictly comes back without text instead of failing."""
    return Client(warc=warc, follow_redirects=False, pass_statuses=PAGE_4XX, strict_decode=False, limits=ARCHIVE_LIMITS,
                  skip_types=SKIP_TYPES, resend_gap_s=GAP[0], **kw)


def resolve(host: str) -> tuple[str, str | None, str | None]:
    """(queue, ip, canonical name) for a host; queue 'dns-fail' when it does not resolve (never raises)."""
    if host == scope.GOOGLE_SITES:
        return "google", None, host
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, flags=socket.AI_CANONNAME)
        cname = next((i[3] for i in infos if i[3]), host).lower().rstrip(".")
        v4 = sorted({ipaddress.ip_address(i[4][0]) for i in infos if i[0] == socket.AF_INET})
        v6 = sorted({ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos if i[0] == socket.AF_INET6})
    except (OSError, UnicodeError, ValueError):
        return "dns-fail", None, None
    ips = v4 or v6
    ip = str(ips[0]) if ips else None
    if _GOOGLE_CNAME.search(cname) and cname != host:
        return "google", ip, cname
    return (f"ip:{ip}" if ip else "dns-fail"), ip, cname


def netcheck() -> bool:
    """Is there a network at all: a route out (connecting a UDP socket sends nothing) and a public name that
    resolves. Never touches a school server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: fails at once when there is no route
        socket.getaddrinfo("www.google.com", 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def error_kind(exc: Exception) -> str:
    """A failure's category for url.reason and the monitor (the full message stays in request.error): http_<status>,
    timeout, connect, dns, tls, retry_after, too_large, decode, host_cooldown, net_<type> or fetch."""
    if isinstance(exc, RetryLater):
        return "retry_after"
    if isinstance(exc, UnwantedType):
        return "media_type"
    if isinstance(exc, TooLarge):
        return "too_large"
    if isinstance(exc, DecodeError):
        return "decode"
    if isinstance(exc, HostCircuitOpen):
        return "host_cooldown"
    cause = exc.__cause__
    if isinstance(cause, RetryableStatus):
        return f"http_{cause.status}"
    if isinstance(cause, httpx.ConnectTimeout):
        return "connect"
    if isinstance(cause, httpx.TimeoutException):
        return "timeout"
    if isinstance(cause, httpx.ConnectError):
        text = str(cause)
        return "dns" if _DNS_TEXT.search(text) else "tls" if _TLS_TEXT.search(text) else "connect"
    if isinstance(cause, httpx.TransportError):
        return "net_" + type(cause).__name__
    m = _HTTP_TEXT.search(str(exc))
    return f"http_{m[1]}" if m else "fetch"


def _transport(kind: str) -> bool:
    return kind in TRANSPORT_KINDS or kind.startswith("net_")


def deny_dir(url: str) -> str:
    """Where a streak of refusals is counted: the host and the first two directories of the path (the file name not
    counted), e.g. c.nknu.edu.tw/aca/uploadfile for every attachment of 教務處's upload folder."""
    parts = urlsplit(url)
    return f"{(parts.hostname or '').lower()}/" + "/".join(parts.path.split("/")[1:-1][:2])


def _dns_wait(fails: int) -> float:
    return min(DNS_RETRY_S * 2 ** (max(fails, 1) - 1), DNS_RETRY_MAX_S)


def _lenient(body: bytes, ctype: str) -> str:
    """Text for link extraction from a body that strict decoding refused: the declared charset, bad bytes replaced."""
    m = _CHARSET.search(ctype or "")
    cs = m.group(1).lower() if m else "utf-8"
    cs = {"big5": "cp950", "big-5": "cp950", "x-big5": "cp950"}.get(cs, cs)
    try:
        return body.decode(cs, errors="replace").removeprefix("﻿")
    except (LookupError, UnicodeError):  # UnicodeError: charset=undefined, or idna (which cannot replace)
        return body.decode("utf-8", errors="replace").removeprefix("﻿")


class _NetWatch:
    """Tells our network going down from a server's own failure. Answers and connection errors are noted per network
    ("school" = 140.127.*, "other"). A connection error on a network that answered within NET_WINDOW_S is that server's
    problem. Otherwise the connectivity check is asked (a fresh one once NET_QUEUES of that network's queues failed in
    that window, the cached one before): the network is down only when the check fails, and the pause then holds the
    networks that answered nothing within NET_WINDOW_S, never one that is answering. What connection errors cost
    (attempts, robots_fail, cool-downs) is noted per network, so a pause can undo the costs on the networks it holds."""

    def __init__(self, clock: Callable[[], float], check: Callable[[], bool]):
        self.clock, self._check = clock, check
        self.lock = threading.Lock()
        self.last_answer: dict[str, float] = {}
        self.errors: list[tuple[float, str, str]] = []
        self.charges: list[tuple[float, str, tuple]] = []
        self._checked_at, self._ok = -math.inf, True

    def answered(self, queue: str) -> None:
        with self.lock:
            self.last_answer[network_of(queue)] = self.clock()

    def grace(self, network: str | None = None) -> None:
        """After a pause (of every network, or of one): assume it works until it shows otherwise."""
        nets = NETWORKS if network is None else (network,)
        with self.lock:
            now = self.clock()
            for n in nets:
                self.last_answer[n] = now
            self.errors = [e for e in self.errors if e[1] not in nets]

    def check(self, fresh: bool = False) -> bool:
        now = self.clock()
        if fresh or now - self._checked_at > NETCHECK_CACHE_S:
            self._ok, self._checked_at = bool(self._check()), now
        return self._ok

    def down_after(self, queue: str) -> frozenset[str]:
        """Called after a connection error from queue: the networks to pause (empty: not our network)."""
        now, net = self.clock(), network_of(queue)
        with self.lock:
            self.errors = [e for e in self.errors if now - e[0] <= NET_WINDOW_S] + [(now, net, queue)]
            if now - self.last_answer.get(net, -math.inf) <= NET_WINDOW_S:
                return frozenset()
            many = len({q for _, n, q in self.errors if n == net}) >= NET_QUEUES
        if self.check(fresh=many):
            return frozenset()
        with self.lock:
            return frozenset(n for n in NETWORKS if now - self.last_answer.get(n, -math.inf) > NET_WINDOW_S)

    def charge(self, queue: str, *undo) -> None:
        with self.lock:
            now = self.clock()
            self.charges = [c for c in self.charges if now - c[0] <= NET_UNDO_S] + [(now, network_of(queue), undo)]

    def take_charges(self, networks: frozenset[str]) -> list[tuple]:
        """The recent costs on these networks (to be undone); those on other networks are kept."""
        with self.lock:
            now = self.clock()
            out = [u for t, n, u in self.charges if now - t <= NET_UNDO_S and n in networks]
            self.charges = [c for c in self.charges if c[1] not in networks]
            return out


class Crawler:
    def __init__(self, root: Path, *, limit: int | None = None, clock: Callable[[], float] = wall,
                 sleep: Callable[[float], None] = time.sleep, resolver: Callable[[str], tuple] = resolve,
                 client_factory: Callable[[WarcSink | None], Client] | None = None, rng: random.Random | None = None,
                 site_cap: int = SITE_CAP, variant_cap: int = VARIANT_CAP, file_cap: int = FILE_CAP,
                 netcheck: Callable[[], bool] = netcheck,
                 disk_free: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free):
        self.root = root
        self.frontier = Frontier(root / "frontier.db")
        self.limit, self.clock, self.sleep, self.resolver = limit, clock, sleep, resolver
        self.client_factory = client_factory or archive_client
        self.rng = rng or random.Random()
        self.site_cap, self.variant_cap, self.file_cap = site_cap, variant_cap, file_cap
        self.disk_free = disk_free
        self.net = _NetWatch(clock, netcheck)
        self.stop = threading.Event()
        self.run_id = int(time.time())
        self.directory_pages = seeds.directory_pages()
        self.frontier.mark_directory(self.directory_pages)  # rows seeded before the marker existed
        self.load_site_meta()
        self._fetched = 0
        self._count_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._tl = threading.local()
        self._last_kind: dict[str, str] = {}

    # ---- discovery
    def load_site_meta(self) -> None:
        """A seed site's tier and unit (or the site a seed redirects to, or an off-campus site the official directory
        links to): a link into that site takes them, not those of the page that linked to it. The directory pages
        themselves are left out: 社團一覽 does not make all of sso.nknu.edu.tw/osa tier 0, nor give it its name."""
        self.site_meta = {r["site"]: (r["tier"], r["unit"]) for r in self.frontier.q(
            "SELECT site, min(tier) AS tier, unit FROM url WHERE directory = 0 AND (found_via IN ('seed','v1_known') "
            "OR (found_via='redirect' AND depth=0)) GROUP BY site")}
        dirs = self.frontier.directory_sites()
        self.directory_sites = set(dirs)
        for site, row in dirs.items():
            self.site_meta.setdefault(site, (DIRECTORY_TIER, row["text"] or None))

    def judge(self, url: str) -> scope.Verdict:
        return scope.judge(url, self.directory_sites)

    def queue_of(self, host: str) -> str:
        row = self.frontier.host(host)
        if row is not None:
            return row["queue"]
        queue, ip, cname = self.resolver(host)
        self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_at=self.clock(),
                               dns_fail=int(queue == "dns-fail"))
        return queue

    def enqueue(self, url: str, *, unit: str | None, tier: int, depth: int, found_via: str,
                found_from: int | None, source_site: str | None = None, directory: bool = False) -> str:
        """Add a canonical URL; returns the link verdict: queued, dup, out:<why> or skipped:<why>. directory: the URL
        is an official directory page (one of seeds.directory_pages(), or where such a page redirects)."""
        verdict = self.judge(url)
        if not verdict.ok:
            return f"out:{verdict.reason}"
        if self.frontier.exists(url):
            return "dup"
        host = urlsplit(url).hostname or ""
        # a school page only: an off-campus page a directory page redirects to admits no further sites
        directory = (directory and verdict.reason == "in_scope") or url in self.directory_pages
        site = scope.site_key(url)
        if found_via in ("link", "redirect"):
            if site in self.site_meta:
                tier, unit = self.site_meta[site]
            elif found_via == "link" and site != source_site:  # a site nobody seeded: not the linking page's unit
                tier, unit = min(max(tier + 1, 3), 4), None
        kind = scope.kind_of(url)
        reason = None
        if depth > MAX_DEPTH:
            reason = "depth"
        elif kind == "file" and self.frontier.count_site(site, "file") >= self.file_cap:
            reason = "file_cap"
        elif kind != "file" and self.frontier.count_site(site, "page") >= self.site_cap:
            reason = "site_cap"
        elif not scope.is_platform_path(url) and self.frontier.count_path(site, url.split("?", 1)[0]) >= self.variant_cap:
            reason = "variant_cap"
        queue = self.queue_of(host)
        state, next_at = ("pending" if reason is None else "skipped"), 0.0
        if queue == "dns-fail" and reason is None:  # waits for the name to resolve (run() tries again)
            h = self.frontier.host(host)
            reason, next_at = "dns", h["dns_at"] + _dns_wait(h["dns_fail"])
            state = "failed" if h["dns_fail"] >= DNS_GIVE_UP else "pending"
        elif reason is None and (until := self.frontier.deny_until(deny_dir(url))) > self.clock():
            reason, next_at = "denied_dir", until  # its directory is waiting out a streak of refusals
        added = self.frontier.add(url, host=host, site=site, unit=unit, tier=tier, kind=kind, depth=depth,
                                  found_via=found_via, found_from=found_from, state=state, reason=reason,
                                  next_at=next_at, directory=directory)
        if not added:
            return "dup"
        if found_via == "redirect" and depth == 0 and not directory:
            self.site_meta.setdefault(site, (tier, unit))  # where a seed really lives
        return "queued" if state == "pending" else f"skipped:{reason}"

    def _add_directory_site(self, target: str, row, text: str | None) -> None:
        site = scope.site_key(target)
        if site in self.directory_sites:
            return
        self.frontier.add_directory_site(site, row["url"], row["id"], text or None)
        self.directory_sites.add(site)
        self.site_meta.setdefault(site, (DIRECTORY_TIER, text or None))
        self.frontier.event("info", "directory_site", f"{site}: linked from the official directory, crawled",
                            urlsplit(target).hostname, target[:300])

    def _reresolve(self, force: bool = False) -> None:
        """Names that did not resolve, tried again when their back-off is over (all of them when force)."""
        now = self.clock()
        for h in self.frontier.q("SELECT host, dns_fail, dns_at FROM host WHERE queue='dns-fail' AND dns_fail < ?",
                                 (DNS_GIVE_UP,)):
            host = h["host"]
            if not force and now < h["dns_at"] + _dns_wait(h["dns_fail"]):
                continue
            queue, ip, cname = self.resolver(host)
            if queue != "dns-fail":
                self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_fail=0, dns_at=now)
                n = self.frontier.x("UPDATE url SET next_at=0, reason=NULL WHERE host=? AND state='pending' "
                                    "AND reason='dns'", (host,))
                self.frontier.event("info", "dns_back", f"resolves now ({queue}): {n} URLs due", host)
                continue
            fails = h["dns_fail"] + (1 if self.net.check() else 0)  # an outage is not the name's fault
            self.frontier.set_host(host, dns_fail=fails, dns_at=now)
            if fails >= DNS_GIVE_UP:
                n = self.frontier.x("UPDATE url SET state='failed' WHERE host=? AND state='pending' AND reason='dns'",
                                    (host,))
                self.frontier.event("warn", "dns_gave_up", f"did not resolve {fails} times: {n} URLs failed "
                                    "(nknu archive retry dns tries again)", host)
            else:
                self.frontier.x("UPDATE url SET next_at=? WHERE host=? AND state='pending' AND reason='dns'",
                                (now + _dns_wait(fails), host))

    # ---- running
    def run(self, poll_s: float = 5.0) -> None:
        """The caller holds run.lock (hold_run_lock): only then may rows left active be taken back."""
        n = self.frontier.requeue_active()
        if n:
            log.info("requeued %d URLs left active by the last run", n)
        self.frontier.event("info", "run_start", f"pid {os.getpid()}")
        self._reresolve(force=True)
        workers: dict[str, threading.Thread] = {}
        try:
            while not self.stop.is_set() and not self._limit_reached():
                self._reresolve()
                if not self._paused():
                    for queue in self.frontier.queues(self.clock()):
                        if queue == "dns-fail":
                            continue
                        t = workers.get(queue)
                        if t is None or not t.is_alive():
                            t = threading.Thread(target=self._work, args=(queue,), name=f"q-{queue}", daemon=True)
                            workers[queue] = t
                            t.start()
                left, _nxt = self.frontier.pending_left()
                alive = [t for t in workers.values() if t.is_alive()]
                if left == 0 and not alive:
                    log.info("nothing left to fetch")
                    break
                self.stop.wait(poll_s)
        finally:
            self.stop.set()
            for t in workers.values():
                t.join(timeout=120)

    def _limit_reached(self) -> bool:
        with self._count_lock:
            return self.limit is not None and self._fetched >= self.limit

    def _count(self) -> None:
        with self._count_lock:
            self._fetched += 1
            if self.limit is not None and self._fetched >= self.limit:
                self.stop.set()

    def _paused(self, queue: str | None = None) -> bool:
        """Whether a network pause holds: every queue's, or (given a queue) also its network's. When one is over, a
        fresh connectivity check decides: resume, or five more minutes."""
        keys = [ALL_QUEUES] + ([net_key(network_of(queue))] if queue else [])
        with self._pause_lock:
            for key in keys:
                until = self.frontier.pause_of(key)
                if until == 0:
                    continue
                now = self.clock()
                if until > now:
                    return True
                if not self.net.check(fresh=True):
                    self.frontier.pause(key, now + NET_PAUSE_S, "the network is still down")
                    return True
                self.frontier.unpause(key)
                network = None if key == ALL_QUEUES else network_of(queue)
                self.net.grace(network)
                self.frontier.event("info", "online", "the network is back: every queue resumes" if network is None
                                    else f"the {network} network is back: its queues resume")
            return False

    def _work(self, queue: str) -> None:
        sink = WarcSink(self.root / "warc", re.sub(r"[^\w.-]", "_", queue), self.run_id, _utc())
        client = self.client_factory(sink)
        idle_since, was_paused = None, False
        try:
            while not self.stop.is_set():
                now = self.clock()
                paused = self._paused(queue) or self.frontier.paused_until(queue) > now
                if paused and not was_paused:  # what this client counted during the outage is forgotten
                    for h in self.frontier.hosts_of(queue):
                        client.reset_host(h)
                was_paused = paused
                hosts = self.frontier.hosts_of(queue)
                row = None if paused else self.frontier.next_for(hosts, now)
                if row is None:
                    idle_since = idle_since or self.clock()
                    if self.clock() - idle_since > IDLE_EXIT_S:
                        return
                    due = None if paused else self.frontier.next_due(hosts)  # e.g. a host's Crawl-delay ending
                    self.stop.wait(min(IDLE_POLL_S, max(0.05, due - self.clock())) if due else IDLE_POLL_S)
                    continue
                idle_since = None
                self._handle(queue, client, sink, row)
        finally:
            with suppress(Exception):
                client.close()
            with suppress(Exception):
                sink.close()

    def _handle(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        """One row, whatever happens: the run stops for what no page can fix (archive, disk, database)."""
        try:
            self._process(queue, client, sink, row)
        except _Stopped:
            self.frontier.finish(row["id"], state="pending", fetched_at=None)
        except _Offline:
            self.frontier.finish(row["id"], state="pending", reason="net_down", fetched_at=None)
            for h in self.frontier.hosts_of(queue):
                client.reset_host(h)
        except (ArchiveWriteError, _DiskLow) as exc:
            self.frontier.finish(row["id"], state="pending", fetched_at=None)
            if isinstance(exc, ArchiveWriteError):
                self.frontier.event("error", "warc_write", f"{exc} — the run stops; fix the archive directory, then "
                                    "nknu archive run again"[:500], row["host"], row["url"])
            self.stop.set()
        except sqlite3.DatabaseError:
            log.exception("queue %s: the frontier database failed, stopping the run", queue)
            self.stop.set()
        except Exception as exc:  # one bad page never stops the queue
            log.exception("queue %s: %s", queue, row["url"])
            self.frontier.finish(row["id"], state="failed", reason=f"crash:{type(exc).__name__}")
            self.frontier.event("error", "crash", f"{type(exc).__name__}: {exc}"[:500], row["host"], row["url"])

    def _gap(self, host: str) -> None:
        """The 2–5 s after a response, on this queue, never cut short. A longer Crawl-delay of the host (at most 300 s)
        is that host's own wait: it may not be asked again before not_before, the other hosts on the server may."""
        ended = self.clock()
        delay = self.rng.uniform(*GAP)
        h = self.frontier.host(host)
        crawl_delay = min(float(h["crawl_delay"]), MAX_CRAWL_DELAY_S) if h is not None and h["crawl_delay"] else 0.0
        if crawl_delay > delay:
            self.frontier.set_host(host, not_before=ended + crawl_delay)
        self.sleep(delay)  # never cut short, not even by a stop: the next request on this server waits it out
        self._tl.gap, self._tl.ended = delay, ended

    def _not_due(self, host: str) -> bool:
        """The host is within its Crawl-delay since its last request."""
        h = self.frontier.host(host)
        return h is not None and (h["not_before"] or 0) > self.clock()

    def _send(self, queue: str, client: Client, target: Target, purpose: str, pass_statuses: frozenset[int] | None,
              scope_reason: str | None, robots_sha: str | None) -> Snapshot | FetchError:
        """One request. Whatever follows a request that may have reached the server (an answer, a failure, an
        exception of any kind), it is logged and the full gap is waited before anything else."""
        host = urlsplit(target.url).hostname or ""
        if self.stop.is_set():
            raise _Stopped
        started, status, error, sent = self.clock(), None, None, True
        try:
            got = client.fetch(target, NO_RATE, max_bytes=MAX_BYTES, pass_statuses=pass_statuses)
            status = got.status
            return got
        except HostCircuitOpen as exc:  # refused before sending: nothing reached the server, nothing to wait for
            sent = False
            return exc
        except FetchError as exc:
            error = str(exc)[:300]
            return exc
        except ArchiveWriteError as exc:
            status, error = exc.status, f"warc_write: {exc}"[:300]
            raise
        except BaseException as exc:
            error = f"crash:{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            if sent:
                try:
                    self.frontier.request(queue, host, target.url, purpose, started, self.clock(), status, error,
                                          scope=scope_reason, robots_sha=robots_sha)
                finally:
                    self._gap(host)

    def _request(self, queue: str, client: Client, target: Target, purpose: str,
                 pass_statuses: frozenset[int] | None = None, scope_reason: str | None = None,
                 robots_sha: str | None = None) -> Snapshot | FetchError:
        got = self._send(queue, client, target, purpose, pass_statuses, scope_reason, robots_sha)
        if isinstance(got, HostCircuitOpen):
            return got
        host = urlsplit(target.url).hostname or ""
        kind = error_kind(got) if isinstance(got, FetchError) else None
        if kind is not None:
            self._last_kind[host] = kind
        if kind not in NET_KINDS:
            self.net.answered(queue)  # something on that network answered (a 5xx is an answer too)
            return got
        networks = self.net.down_after(queue)
        if networks:
            self._go_offline(host, networks)
            raise _Offline
        return got

    def _go_offline(self, host: str, networks: frozenset[str]) -> None:
        """Pause the networks that do not answer (every queue when none does) and undo what their connection errors
        of the last minutes cost."""
        now = self.clock()
        every = networks >= frozenset(NETWORKS)
        keys = [ALL_QUEUES] if every else [net_key(n) for n in sorted(networks)]
        first = all(self.frontier.paused_until(k) <= now for k in keys)
        what = "every queue" if every else " and ".join(
            {"school": "the school network (140.127.*)", "other": "the other (off-campus) network"}[n]
            for n in sorted(networks)) + "'s queues"
        for key in keys:
            self.frontier.pause(key, now + NET_PAUSE_S, "the network is down" if every else
                                "this network does not answer and the connectivity check failed")
        undone = self._undo(self.net.take_charges(networks))
        if first:
            self.frontier.event("warn", "offline", f"the network is down: {what} pause {NET_PAUSE_S / 60:.0f} min; "
                                f"{undone} connection failures are not held against URLs or hosts", host)

    def _undo(self, charges: list[tuple]) -> int:
        """Take back what connection errors cost while the network was in fact down (latest first, so each row ends
        as it was before the outage)."""
        for kind, *args in reversed(charges):
            if kind == "url":
                uid, attempts = args
                self.frontier.x("UPDATE url SET state='pending', attempts=?, next_at=0, reason='net_down' "
                                "WHERE id=? AND state IN ('pending','failed')", (attempts, uid))
            elif kind == "host":
                host, before, mass = args
                self.frontier.set_host(host, **before)
                if mass:
                    self.frontier.x("UPDATE url SET state='pending', reason='net_down' WHERE host=? AND state='failed' "
                                    "AND reason='host_unreachable'", (host,))
            elif kind == "cool":
                host, until, seconds = args
                self.frontier.set_host(host, cooldown_until=until, cooldown_s=seconds)
        return len(charges)

    def _cool_host(self, host: str) -> float:
        """The breaker's cool-down: 15 min, doubling while it keeps opening (reset at the host's next answer)."""
        h = self.frontier.host(host)
        wait = min(max(COOLDOWN[0], (h["cooldown_s"] or 0) * 2), COOLDOWN[1])
        self.frontier.set_host(host, cooldown_until=self.clock() + wait, cooldown_s=wait)
        return wait

    def _rest_queue(self, queue: str, client: Client, host: str, url: str, exc: RetryLater) -> float:
        """The server asked us to come back later: every host on it waits that long (at least 15 min, at most a day)."""
        wait = min(max(exc.seconds, COOLDOWN[0]), COOLDOWN[1])
        until = self.clock() + wait
        self.frontier.pause(queue, until, f"Retry-After {exc.seconds:.0f} s from {host}")
        client.reset_host(host)  # the wait is kept here, in the frontier, not in the client's "skip this run"
        self.frontier.event("warn", "retry_after", f"{host} asked for {exc.seconds:.0f} s (Retry-After): server queue "
                            f"{queue} rests {wait / 60:.0f} min", host, url)
        return until

    def _robots(self, queue: str, client: Client, url: str) -> Robots:
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        age = self.clock() - ((h["robots_at"] or 0) if h is not None else 0)
        if h is not None and h["robots_state"] and age < FRESH_S and (
                h["robots_state"] != "unreachable" or age < RETRY_UNREACHABLE_S):
            return Robots(h["robots_state"], h["robots_body"] or "")
        self._tl.ended = None
        answer, kind = self._read_robots(queue, client, host, f"{parts.scheme}://{parts.netloc}/robots.txt")
        if answer.state == "unreachable" and kind in NET_KINDS and parts.port is None:
            # an http link to an https-only host (or the other way round) must not cost the host its robots.txt
            other = "https" if parts.scheme == "http" else "http"
            alt, _ = self._read_robots(queue, client, host, f"{other}://{parts.netloc}/robots.txt")
            if alt.state != "unreachable":
                answer = alt
        if answer.state == "wait":
            return answer
        if answer.state == "unreachable" and h is not None and h["robots_state"] in ("rules", "none"):
            self.frontier.set_host(host, robots_at=self.clock())  # keep the last good copy, try again later
            return Robots(h["robots_state"], h["robots_body"] or "")
        before = {k: h[k] for k in ("robots_state", "robots_body", "robots_sha", "robots_at", "crawl_delay",
                                    "robots_fail", "cooldown_until", "not_before")} if h is not None else None
        fails = ((h["robots_fail"] or 0) + 1) if answer.state == "unreachable" and h is not None else 0
        if answer.state == "rules":
            self.frontier.robots_version(answer.sha, host, answer.body, self.clock())
        delay, gap, ended = answer.crawl_delay(), getattr(self._tl, "gap", 0.0), getattr(self._tl, "ended", None)
        # the robots.txt just read rules the wait after it: the request after it is the host's next one
        not_before = ended + delay if delay and ended is not None and delay > gap else 0.0
        self.frontier.set_host(host, robots_state=answer.state, robots_body=answer.body[:MAX_BODY],
                               robots_sha=answer.sha, robots_at=self.clock(), crawl_delay=delay, robots_fail=fails,
                               not_before=not_before)
        mass = False
        if answer.state == "unreachable" and fails >= UNREACHABLE_TRIES:
            n = self.frontier.x("UPDATE url SET state='failed', reason='host_unreachable' WHERE host=? AND state "
                                "IN ('pending','active')", (host,))
            mass = True
            self.frontier.event("warn", "host_unreachable", f"robots.txt unreachable {fails} times in a row: {n} URLs "
                                "wait for a run from the lab machine (nknu archive retry host_unreachable)", host, url)
        elif answer.state == "unreachable":
            self.frontier.event("warn", "robots_unreachable", "robots.txt unreachable: host waits an hour", host, url)
        if answer.state == "unreachable" and kind in NET_KINDS and before is not None:
            self.net.charge(queue, "host", host, before, mass)
        return answer

    def _read_robots(self, queue: str, client: Client, host: str, target: str) -> tuple[Robots, str | None]:
        """One host's robots.txt, following redirects that stay on this queue: (answer, failure kind)."""
        for hop in range(ROBOTS_HOPS + 1):
            got = self._request(queue, client, Target(target), "robots", pass_statuses=ROBOTS_4XX)
            if isinstance(got, RetryLater):
                self._rest_queue(queue, client, host, target, got)
                return Robots("wait"), None
            if isinstance(got, HostCircuitOpen):  # nothing was sent: not an unreachable robots.txt
                client.reset_host(host)
                self._cool_host(host)
                return Robots("wait"), None
            if isinstance(got, FetchError):
                return Robots("unreachable"), error_kind(got)
            if got.status not in REDIRECTS:
                text = decode(got.body) if 200 <= got.status < 300 else None
                answer = judge_answer(got.status, text)
                if answer.state == "rules" and unparsed(text):
                    self.frontier.event("warn", "robots_unparsed", "robots.txt answered with no rule in it (an HTML "
                                        "page?): crawled as if there were none", host, target)
                return answer, None
            nxt = scope.canonical(got.headers.get("location", ""), target)
            why = None
            if hop >= ROBOTS_HOPS:
                why = f"more than {ROBOTS_HOPS} redirects"
            elif nxt is None:
                why = "a redirect without a usable Location"
            elif self.judge(nxt).reason in scope.HARD_OUT:
                why = f"a redirect to {nxt} ({self.judge(nxt).reason})"
            elif self.queue_of(urlsplit(nxt).hostname or "") != queue:
                why = f"a redirect to another server ({nxt})"
            if why:
                self.frontier.event("warn", "robots_redirect", f"robots.txt: {why}; read as no robots.txt (RFC 9309)",
                                    host, target)
                return Robots("none"), None
            target = nxt
        return Robots("none"), None

    def _probe(self, queue: str, client: Client, url: str, robots: Robots,
               scope_reason: str) -> float | FetchError | None:
        """Once per host: what a page that does not exist looks like (status, digest, length, title). Returns None to
        go on with the page; the time the server queue rests until, when the probe was told to come back later
        (Retry-After); or the failure, when the probe reached no server (the page would not either, so it is not
        sent, and the probe is tried again with a later page)."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        if h is not None and h["probe_status"] is not None:
            return None
        target = f"{parts.scheme}://{parts.netloc}/nknu-archive-probe-{secrets.token_hex(6)}.html"
        if not robots.allows(target):
            self.frontier.set_host(host, probe_status=-1)
            return None
        got = self._request(queue, client, Target(target), "probe", scope_reason=scope_reason, robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):
            return None  # nothing was sent; the page request meets the same breaker
        if isinstance(got, RetryLater):
            return self._rest_queue(queue, client, host, target, got)
        if isinstance(got, FetchError):
            if error_kind(got) in ("dns", "connect", "tls"):  # the server is not reachable at all: no page request
                return got
            self.frontier.set_host(host, probe_status=0)  # a slow or dropped missing page says nothing about the rest
            return None
        try:
            text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            title = extract(text, got.url).title if text else ""
        except Exception:  # noqa: BLE001 - the probe's answer is kept whatever its title extraction makes of it
            title = ""
        self.frontier.set_host(host, probe_status=got.status, probe_sha=got.sha256, probe_len=len(got.body),
                               probe_title=title)
        if got.status == 200:
            self.frontier.event("info", "soft404_host", "a missing page answers 200 here: matching pages get flagged",
                                host, target)
        return None

    def _process(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        url, host, uid = row["url"], row["host"], row["id"]
        verdict = self.judge(url)  # the rules of today, for rows that entered the frontier under older ones
        if not verdict.ok:
            self.frontier.finish(uid, state="skipped", reason=f"out:{verdict.reason}", fetched_at=None)
            return
        if self.disk_free(self.root) < MIN_FREE_BYTES:
            self.frontier.event("error", "disk_low", f"less than {MIN_FREE_BYTES // 1024**3} GB free under "
                                f"{self.root}: the run stops before the archive cannot be written", host, url)
            raise _DiskLow
        robots = self._robots(queue, client, url)
        if robots.state == "wait":
            self.frontier.finish(uid, state="pending", reason="robots_wait", fetched_at=None)
            return
        if robots.state == "unreachable":
            h = self.frontier.host(host)
            if h is not None and (h["robots_fail"] or 0) >= UNREACHABLE_TRIES:
                self.frontier.finish(uid, state="failed", reason="host_unreachable", fetched_at=None)
                return
            self.frontier.set_host(host, cooldown_until=self.clock() + RETRY_UNREACHABLE_S)
            self.frontier.finish(uid, state="pending", reason="robots_unreachable", fetched_at=None)
            return
        if not robots.allows(url):
            self.frontier.finish(uid, state="skipped", reason="robots")
            return
        if self._not_due(host):  # the Crawl-delay after robots.txt: the row comes back when the host is due
            self.frontier.finish(uid, state="pending", fetched_at=None)
            return
        probed = self._probe(queue, client, url, robots, verdict.reason)
        if isinstance(probed, float):  # the server asked to be left alone: no attempt is used
            self.frontier.finish(uid, state="pending", reason="retry_after", next_at=probed, fetched_at=None)
            return
        if isinstance(probed, FetchError):
            self._failed(queue, row, probed)
            return
        if self._not_due(host):  # the Crawl-delay after the probe
            self.frontier.finish(uid, state="pending", fetched_at=None)
            return
        got = self._request(queue, client, Target(url), "page", scope_reason=verdict.reason, robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):  # nothing was sent
            h = self.frontier.host(host)
            before = (h["cooldown_until"], h["cooldown_s"])
            wait = self._cool_host(host)
            if self._last_kind.get(host) in NET_KINDS:
                self.net.charge(queue, "cool", host, *before)
            client.reset_host(host)
            self.frontier.finish(uid, state="pending", reason="host_cooldown", fetched_at=None)
            self.frontier.event("warn", "host_cooldown", f"{BREAKER_THRESHOLD} timeouts, 5xx or connection errors in a "
                                f"row: the host waits {wait / 60:.0f} min", host, url)
            return
        self._count()
        if isinstance(got, RetryLater):  # not the URL's fault: no attempt is used
            until = self._rest_queue(queue, client, host, url, got)
            self.frontier.finish(uid, state="pending", reason="retry_after", next_at=until, fetched_at=None)
            return
        if isinstance(got, TooLarge):
            self.frontier.finish(uid, state="skipped", reason="media_type" if isinstance(got, UnwantedType) else "too_large")
            return
        if isinstance(got, DecodeError):
            self.frontier.finish(uid, state="failed", reason="decode")
            return
        if isinstance(got, FetchError):
            self._failed(queue, row, got)
            return
        self._answered(host, url, got.status)
        self._store(row, got, sink)

    def _failed(self, queue: str, row, exc: FetchError) -> None:
        """A request for this row reached no answer: tried again later (30 min × attempts), three attempts in all."""
        uid, kind = row["id"], error_kind(exc)
        attempts = row["attempts"] + 1
        if kind in NET_KINDS:
            self.net.charge(queue, "url", uid, row["attempts"])
        if attempts < MAX_ATTEMPTS:
            self.frontier.finish(uid, state="pending", attempts=attempts, reason=kind, fetched_at=None,
                                 next_at=self.clock() + RETRY_AFTER_S * attempts + self.rng.uniform(0, RETRY_JITTER_S))
        else:
            self.frontier.finish(uid, state="failed", attempts=attempts, reason=kind)

    def _answered(self, host: str, url: str, status: int) -> None:
        """The host answered: its breaker's cool-down starts again from 15 min. A refusal (401/403/451) counts towards
        its directory's streak instead; the tenth in a row makes that directory's URLs wait DENY_WAIT_S, a fixed
        15 min, while the rest of the host goes on (a protected upload folder never stalls c.nknu)."""
        h = self.frontier.host(host)
        if h is None:
            return
        directory = deny_dir(url)
        if status in DENY_STATUSES:
            run = self.frontier.deny(directory, host)
            if run >= DENY_STREAK:
                until = self.clock() + DENY_WAIT_S
                n = self._defer_directory(host, directory, until)
                self.frontier.deny_rest(directory, until)
                self.frontier.event("warn", "host_denies", f"{run} URLs in a row under {directory} answered "
                                    f"401/403/451: its {n} other URLs wait {DENY_WAIT_S / 60:.0f} min, the rest of the "
                                    "host goes on (nknu archive retry http_403 puts the refused ones back if it was "
                                    "blocking us)", host, url)
            return
        self.frontier.deny_clear(directory)
        if h["cooldown_s"]:
            self.frontier.set_host(host, cooldown_s=0)

    def _defer_directory(self, host: str, directory: str, until: float) -> int:
        ids = [r["id"] for r in self.frontier.q("SELECT id, url FROM url WHERE host=? AND state='pending'", (host,))
               if deny_dir(r["url"]) == directory]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            self.frontier.x(f"UPDATE url SET next_at = max(next_at, ?), reason = coalesce(reason, 'denied_dir') "
                            f"WHERE id IN ({','.join('?' * len(part))})", (until, *part))
        return len(ids)

    def _store(self, row, snap: Snapshot, sink: WarcSink) -> None:
        uid, ctype = row["id"], snap.headers.get("content-type", "").lower()
        cols = {"state": "done", "reason": None, "status": snap.status, "final_url": snap.url, "ctype": ctype[:100],
                "bytes": len(snap.body), "sha256": snap.sha256, "warc": str(sink.path.relative_to(self.root))}
        if snap.status in REDIRECTS:
            target = scope.canonical(snap.headers.get("location", ""), row["url"])
            if target:
                if (row["site"] in self.directory_sites and row["depth"] <= 1 and scope.directory_link_ok(target)
                        and scope.redirect_keeps_site(row["url"], target)):
                    self._add_directory_site(target, row, row["unit"])  # a directory short link: the unit's real site
                verdict = self.enqueue(target, unit=row["unit"], tier=row["tier"], depth=row["depth"],
                                       found_via="redirect", found_from=uid, source_site=row["site"],
                                       directory=bool(row["directory"]))
                if verdict == "dup" and row["directory"] and scope.judge(target).ok:
                    self.frontier.mark_directory([target])  # a directory page that moved is still one
                if verdict != "out:internal_service":
                    self.frontier.link(uid, target, "[redirect]", verdict)
                if verdict in ("out:offsite", "out:google_other"):
                    self.frontier.offsite(self._offsite_key(target, verdict), uid, target[:300])
                if verdict == "out:login" or urlsplit(target).hostname == "accounts.google.com":
                    cols["flags"] = "login"  # the page sends visitors to a sign-in
                cols["final_url"] = target
            self.frontier.finish(uid, **cols)
            return
        if not (snap.status == 200 and ctype.startswith(_HTML)):
            if snap.status == 200:
                cols["kind"] = "file"
            elif snap.status >= 400:
                cols["reason"] = f"http_{snap.status}"
            self.frontier.finish(uid, **cols)
            return
        flags = []
        text = snap.text
        if text is None:  # strict decoding refused it (archived as it is); links are still read, leniently
            text = _lenient(snap.body, ctype)
            flags.append("decode")
        page = extract(text, snap.url)
        h = self.frontier.host(row["host"])
        root = urlsplit(row["url"]).path in ("", "/") or row["found_via"] == "seed"
        if not root and h is not None and h["probe_status"] == 200 and (snap.sha256 == h["probe_sha"] or (
                page.title and page.title == (h["probe_title"] or "")
                and abs(len(snap.body) - (h["probe_len"] or 0)) <= min(100, 0.005 * len(snap.body)))):
            flags.append("soft404")  # looks exactly like this host's answer for a page that does not exist
        if page.login_form and (len(page.links) < LOGIN_MAX_LINKS or _LOGIN_TITLE.search(page.title)
                                or scope.LOGIN_PATH.search(urlsplit(row["url"]).path)):
            flags.append("login")
        elif page.login_form:
            cols["reason"] = "has_login_box"  # followed: the form is a sidebar box on an ordinary page
        cols.update(title=page.title, flags=",".join(flags) or None, n_postbacks=len(page.postbacks))
        if "login" in flags or "soft404" in flags:  # archived as it is; not a place to look for more pages
            cols["n_links"] = 0
            self.frontier.finish(uid, **cols)
            return
        from_directory = bool(row["directory"])  # an official directory page (design §2), by its marker
        n = 0
        for href, text_ in page.links:
            try:
                target = scope.canonical(href, page.base)
                if target is None:
                    continue
                n += 1
                if is_honor_title(text_):  # honour notices: title, link and date only, never the body (owner, 10-24)
                    self.frontier.link(uid, target, mask_honor_names(text_)[:100], "skipped:honor")
                    continue
                if from_directory and scope.directory_link_ok(target):
                    self._add_directory_site(target, row, text_)
                # a directory page lends neither its tier nor its name: what it links to is someone else's
                verdict = self.enqueue(target, unit=None if from_directory else row["unit"], tier=row["tier"],
                                       depth=row["depth"] + 1, found_via="link", found_from=uid,
                                       source_site=None if from_directory else row["site"])
                if verdict == "out:internal_service":
                    continue  # not even in the link table
                if verdict in ("out:offsite", "out:google_other"):  # counted per site: the candidate list (design §2)
                    self.frontier.offsite(self._offsite_key(target, verdict), uid, target[:300])
                self.frontier.link(uid, target, text_, verdict)
            except Exception as exc:  # noqa: BLE001 - one link written wrong never fails its page
                self.frontier.event("warn", "bad_link", f"{type(exc).__name__}: {exc}"[:300], row["host"],
                                    f"{row['url']} -> {href}"[:500])
        for target in dict.fromkeys(page.postbacks):
            with suppress(Exception):
                self.frontier.postback(row["site"], target.split("|", 1)[0], uid)
        cols["n_links"] = n
        self.frontier.finish(uid, **cols)

    @staticmethod
    def _offsite_key(target: str, verdict: str) -> str:
        return scope.site_key(target) if verdict == "out:google_other" else (urlsplit(target).hostname or "")


def _utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)
