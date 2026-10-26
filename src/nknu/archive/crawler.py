"""The round-1 archive crawl (round-1 design §1–§11, review 10-26 B1–B8): one worker thread per server queue, each
sending one request at a time over one kept-alive connection and waiting after every response, whichever host on that
server it fetches next. The crawler writes evidence only (state, reason, status, flags, sniff, page_res, …); what a
row or a page came to is outcome.py's views.

- One run per archive directory and one program per machine: `archive run` holds run.lock (an flock the kernel drops
  when the process ends, even after kill -9) and the machine-wide lock on school requests (crawl/schoollock.py, design
  §6.9), which fetch.Client itself asks for before every request to a school host.
- Every address goes in through round1.admit() (review 10-26 A2, B7): a page's links and parts, a 3xx, a script
  redirect, a short link's answer, the seeds. Wrappers come off, a short link is one GET whose answer
  (round1.short_answer) is judged as if the page had linked its target, a Drive, Docs or Calendar address goes to
  the Google step (google_api.step; one it cannot read is unhandled:google, 要人看, never 规则不抓: B8, as every such
  address is with google_step=False), and the rest is scope.judge() then round1.verdict():
  this round's (pending, owner_site, tier, leaf, kind0, prio), round two's (later, round2:<why>), or no row (out:<rule>,
  the link table only). A round-1 page's attachments, images, frames, forms, short links and school scripts are its
  resources: page_res rows with their need (must, no with why, round2), so nothing it uses is dropped unseen.
- Caps hold rows back (held, cap:site/file/image/variant/depth: design §9); a pager link adds no depth. A platform's
  list page goes first (prio 0) and has no variant cap; its detail pages have no page cap (§7.4).
- Queue = the server that answers (getaddrinfo, the lowest IPv4); Google's web names share one named queue (google),
  its APIs another (googleapis: google_api.queue_for). A name that
  does not resolve is tried again after 10 min, doubling up to 6 h, then given up (`archive retry dns`). The order in
  a queue (§6.7): tier, prio, the host the connection is on, depth, id.
- The gap after every response (§6.3, B6): a random wait inside the run's gap (default 2–5 s; 1–2 s once the pilot
  passed and the owner says so: `archive run --gap 1-2`; never below 1 s), and never less than what the server took to
  answer (at most 30 s); a longer Crawl-delay is that host's own wait (host.not_before).
- Every request that may have reached a server is logged, one request row per request on the wire (fetch.Sent: the
  try, a new or kept connection, TCP and TLS times, the server's address, bytes, the WARC record) with where it was
  made from (campus: on, off or unknown, B4: SSO's own check page, on SSO's queue, hourly and whenever this machine's
  address changes; crawling goes on wherever it is, the dashboard warns), and followed by the full gap.
- robots.txt is read per host before its first request (robots.py): for its Crawl-delay, its sitemaps and whether the
  host answers; it blocks nothing (owner 10-25). A 5xx or no answer never overwrites rules already known (B3). A 404
  probe per page host records what a missing page looks like (soft404).
- What came back is sniffed (sniff.py): a page address that answers a PDF is a file, an attachment that answers a web
  page is not one (not_file) and is never read for links. School error pages are known by where a 3xx sends the
  visitor (outcome.ERROR_SIGNATURES): gone, or an error retried like any GET.
- http and https (§6.6): a page whose scheme refuses a connection or a TLS handshake is tried once on the other. An
  answer there moves the row (moved_scheme); only a page or a redirect elsewhere (2xx, a 3xx that does not send the
  visitor back) teaches that the host answers on that scheme only, so that later addresses are written in it. An
  answer that sends the visitor back to the scheme that failed is no way in: the address is the URL's failure (an
  expired certificate: tls_cert), never a loop (second review, 10-26).
- Failures: a 4xx (but 408/429) is the server's answer, archived as it is. Timeouts, 5xx and TLS failures are the URL's:
  tried again after 30 min × attempts, three in all. The server rests as a whole (second review 10-26, CLAUDE.md
  「出错或回『太忙』整台休息」, design §6.3): a 503 at once, and three errors in a row (5xx, 408, timeouts, whichever of
  its names) a fixed SERVER_REST_S; only the requests that went out cost their URLs an attempt. A Retry-After (or a
  429) rests the whole server queue, no attempt used. The client's breaker (three in a row on one name) cools that name
  a fixed 15 min. A connection that cannot be made (refused, timed out, TLS handshake timed out) is the server's (B2):
  its whole queue pauses a fixed 15 min and no URL ever loses an attempt for it; at CONN_GIVE_UP in a row a warning
  (server_down), and a school host that never answered us off campus waits for the lab machine (host_unreachable). A
  door of a server that answers on another (a closed port, or a scheme that only sends the visitor back) is the URL's
  failure instead: no server pause. Ten 401/403/451 in a row from one directory make that directory wait 15 min.
- Network down: a connection failure on a network that answered nothing for 2 min asks the connectivity check; a
  failed check pauses the networks that answer nothing, 5 min at a time; three school servers failing while the
  check passes pause the school's queues 15 min (B2: the school's network is down, not ours). What those failures
  cost is undone. When the school's pause is over, nothing says it is back until one of its servers answers: until
  then a connection failure there costs no server anything (second review: the check asks www.google.com only).
- No request goes out on a queue that is paused, nor to a host before its Crawl-delay is over.
- A run looks up the hosts of the rows it may fetch that nobody looked up (`retry` brings back round two's and held
  rows; the old archive's hosts are seeded with queue '?'), and ends when nothing is left, or when nothing left comes
  due within end_wait_s (B1: retries, DNS back-off, cool-downs and server pauses are left for the next run instead of
  holding the locks for hours; C0: END_WAIT_S by default, `archive run --end-wait`, RUN_END_WAIT_S there); a row left
  active with no worker holding it is a crash (crash:left_active), and rows due that no queue can take end the run
  (stuck). run_end says why (finished, waiting, stuck, stop_file, sigterm, limit, warc_write, disk_low, db_error,
  no_school_lock), which sites the run covered and how many presses wait.
- Platform files (profiles.py, design §7.4) and their postbacks (postback.py, §7.5): a page of a profiled site is read
  with its content area; once stored, its presses as archived (a list's next page, a row's detail or download) are
  action rows of its own server queue, which a worker takes before its url rows, builds from that very page in the
  WARC, sends once (a POST is never sent again by itself: a failure starts the chain again from its GET once, then
  post_failed) and hands back (an answer's links count on the list page, a Location is its resource). A click whose
  writing a clicks rule reads is a GET row (oia's tabs: their JSON's content counts on the page) or a POST press (ee's
  file buttons: the address they answer is the page's attachment). A button to press that no press covers is
  unhandled:unpressed (要人看). Every GET-paged list's walk is written again when one of its pages has its answer.
- Google (google_api.py, §7.6): a Drive, Docs or Calendar address is the Google step's API rows on the googleapis queue
  (its key in its header only), each answer read for the rows it makes; their entries are written on every page that
  uses them, a short link's or an attachment's way there too. With no key the queue waits (no_api_key, no failure).
- Page types (pagetypes.py, §8.3): each 200 web page gets its type and the signs it reads, before it is finished.
- A platform's detail page is checked against its host's id probe once per detail type (§10.1: empty_detail,
  gone_id); a file the school's page says is missing is looked at once more before it is gone (§10.2).
- A page fetched again writes its page_res anew; what its embed boxes, forms, tabs, file buttons and pressed details
  brought is read back from the archive (_replay_parts), never lost and never asked for again.
- Nothing is parsed into facts here.
"""

from __future__ import annotations

import fcntl
import gzip
import html as htmllib
import ipaddress
import json
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
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

from nknu.archive import google_api, links, outcome, pagetypes, postback, profiles, round1, scope, sniff
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
from nknu.crawl import schoollock
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import (
    BREAKER_THRESHOLD,
    KEEPALIVE_LIMITS,
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
SITE_CAP = 4000  # pages per site (design §9); more are held (cap:site) and listed
FILE_CAP = 6000  # attachments per site, counted apart from the pages
IMAGE_CAP = 6000  # images per site: they take none of the 4,000 pages
VARIANT_CAP = 2000  # URLs per site + path (query variants), platform paths aside: a guard against endless calendars
MAX_DEPTH = 15
GAP = (2.0, 5.0)  # the gap after each response (CLAUDE.md, design §6.3): the default
GAP_FAST = (1.0, 2.0)  # the owner 10-26: once the pilot passed (record 一·3); never the default
MIN_GAP_S = 1.0  # no setting goes below this
SLOW_CAP_S = 30.0  # a slow answer is waited out up to this long (Heritrix's maxDelayMs)
RETRY_AFTER_S = 1800  # a failed URL is tried again after 30 min × attempts (plus up to 5 min), three attempts in all
RETRY_JITTER_S = 300.0
MAX_ATTEMPTS = 3
COOLDOWN = (900.0, 86400.0)  # a Retry-After is waited at least 15 min, at most a day; a breaker's cool-down is the least
SERVER_REST_S = 900.0  # second review 10-26: a server that said 503, or erred three times in a row, rests whole, fixed
REST_NOW = frozenset({"http_503"})  # 「太忙」: the server rests at the first one (a 429 or a Retry-After: RetryLater)
CONN_PAUSE_S = 900.0  # B2: a server that does not connect: its whole queue rests this long, never doubled
CONN_GIVE_UP = 8  # B2: that many in a row (about two hours): a warning, and the lab for school hosts never answered off
DOOR_WINDOW_S = 120.0  # a door (scheme, port) of a server that answers on another within this long is closed, not down
END_WAIT_S = 600.0  # B1: a run whose soonest row is due later than this ends, leaving its rows for the next run
# C0 (design §11): `archive run --end-wait`'s own default, longer than the fixed 15-minute rest of a server (and the
# pause of one that does not connect), so that one such rest never ends a long run nobody watches 【Claude 的执行办法】
RUN_END_WAIT_S = 1800.0
DNS_TIME_EVERY_S = 3600.0  # §6.2: each host's name lookup is timed again this often (host.dns_ms)
IDLE_EXIT_S = 30
IDLE_POLL_S = 5.0  # an idle queue looks again this often, or when its next row is due if that is sooner
PAGE_4XX = frozenset(range(400, 500)) - {408, 429}  # answers, archived as they are: 408 and 429 are retried instead
ROBOTS_4XX = frozenset(range(400, 500)) - {429}  # RFC 9309: any 4xx for robots.txt means no rules (429 is retried)
DENY_STATUSES = frozenset({401, 403, 451})
DENY_STREAK = 10  # this many URLs of one directory refused in a row: that directory waits DENY_WAIT_S
DENY_WAIT_S = 900.0  # fixed: a protected folder never grows into a day-long wait
UNREACHABLE_TRIES = 3  # robots.txt unreachable (no connection kind of its own) this many hours in a row: give up
ROBOTS_HOPS = 5
REDIRECTS = (301, 302, 303, 307, 308)
DNS_RETRY_S = 600.0  # a name that did not resolve is tried again after 10 min, doubling, at most 6 h
DNS_RETRY_MAX_S = 6 * 3600.0
DNS_GIVE_UP = 8  # tries (about a day): then its URLs fail with reason dns, so the run can end
NET_KINDS = frozenset({"dns", "connect"})  # failures that may be our network's, not the server's
CONN_KINDS = frozenset({"connect"})  # B2: refused, connect timeout, TLS handshake timeout (httpx.ConnectTimeout)
TLS_KINDS = frozenset({"tls", "tls_cert"})  # a TLS failure; tls_cert: the certificate failed its check (expired …)
SWITCH_KINDS = CONN_KINDS | TLS_KINDS  # §6.6: a scheme the host may not speak
TRANSPORT_KINDS = frozenset({"dns", "connect", "tls", "tls_cert", "timeout"})  # reached no server (and net_<type>)
# the id probe (design §10.1, once per platform host and detail type): one that got no answer is asked again with a
# later detail page; a timeout or a broken answer (net_*) may have reached the server, so at most PROBE_TRIES of those
# a run; a lookup, a connection or a handshake that failed sent nothing, and does not count (review 10-26 phase 3,
# 【Claude 的执行办法】)
PROBE_TRIES = 2
PROBE_UNSENT = frozenset({"dns", "connect", "tls", "tls_cert"})
NET_WINDOW_S = 120.0
NET_QUEUES = 3
NET_PAUSE_S = 300.0
NET_UNDO_S = 300.0
NETCHECK_CACHE_S = 30.0
MIN_FREE_BYTES = 5 * 1024**3
STOP_POLL_S = 1.0  # how often `archive run` looks for STOP, and `archive stop` for the lock to be let go
STOP_WAIT_S = 600.0
NO_RATE = RateLimit(per_host_concurrency=1, min_interval_s=0.0, jitter_s=0.0)  # the worker waits, per queue
# B4: where SSO tells a visitor whether they are on the school's network (research/site_probe_2026-10-23: from off
# campus it says 「您的IP 不在校內網路」; on campus it sends the visitor on to the system named by cURL, never followed)
CAMPUS_URL = "https://sso.nknu.edu.tw/zthat/checkcampusip.aspx?curl=edocap.nknu.edu.tw"
CAMPUS_EVERY_S = 3600.0
_CAMPUS_OFF = re.compile(r"不在校[內内]網路|不在校内网络")
_HTML = ("text/html", "application/xhtml+xml")
_LOGIN_TITLE = re.compile(r"(?i)登入|登錄|log ?in|sign ?in")
# what a listed login address's page says when it is one (design §4.2: 請登入, 無權限, 請連線 VPN)
_LOGIN_WORDS = re.compile(r"(?i)請先?登入|请先?登录|無權限|无权限|請連線\s*VPN|请连线\s*VPN|請使用\s*VPN")
LOGIN_MAX_LINKS = 15  # a password form on a page with more links is a sidebar member box, not a login page
_FORM_CLOSED = re.compile(r"(?i)不再接受回應|不再接受回应|已停止接受回應|no longer accepting responses")
_GOOGLE_CNAME = re.compile(r"(?:^|\.)(?:googlehosted\.com|ghs\.google\.com|google\.com|googleusercontent\.com)$")
_DNS_TEXT = re.compile(r"(?i)nodename nor servname|name or service not known|temporary failure in name resolution|"
                       r"no address associated|name resolution|getaddrinfo")
_TLS_TEXT = re.compile(r"(?i)certificate|ssl|tls")
# a certificate that failed its check (OpenSSL's words): expired, self-signed, another name's, an unknown issuer
_CERT_TEXT = re.compile(r"(?i)certificate[ _]verify[ _]failed|certificate has expired|self[- ]signed certificate|"
                        r"hostname mismatch|unable to get local issuer")
_HTTP_TEXT = re.compile(r"HTTP (\d{3})\b")
_CHARSET = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)
_CTL = re.compile(r"(?i)ctl\d+")  # an ASP.NET row control's number: postback:<target, ctlNN as ctl*> (design §7.4)
_LOC = re.compile(r"(?is)<loc>\s*(.*?)\s*</loc>")
_SEED_VIAS = ("seed", "v1_known", "old_frontier", "commoncrawl")
# what a row is expected to be when found (kind0) -> its kind until its answer says otherwise (design §12), and its
# place in its queue (prio, §6.7): lists, seeds, sitemaps and login checks 0, pages 1, files and forms 2, images and
# school scripts 3
_KIND_OF0 = {"page": "page", "login_check": "page", "file": "file", "sitemap": "file", "click": "file",
             "script": "file", "shortlink": "shortlink", "image": "image", "form": "embed", "embed": "embed",
             "drive": "drive", "drive_list": "drive", "calendar": "calendar"}
_PRIO_OF0 = {"page": 1, "login_check": 0, "sitemap": 0, "click": 0, "file": 2, "form": 2, "embed": 2,
             "shortlink": 2, "drive": 2, "drive_list": 2, "calendar": 2, "image": 3, "script": 3}
PRIO_LIST = 0
# a resource row's redirect is the same resource (Heritrix's TransclusionDecideRule: an embedded resource's redirect
# may leave the scope); a page's is a link of that page
_TRANSCLUDE = frozenset({"file", "image", "form", "script", "click", "embed", "sitemap", "drive", "drive_list",
                         "calendar"})
_ROLE_OF0 = {"file": "file", "image": "img", "form": "form", "shortlink": "shortlink", "script": "script",
             "embed": "iframe", "page": "file", "drive": "drive", "calendar": "calendar"}
_HARD_SKIP = scope.HARD_OUT | {"trap", "too_long"}  # a pending row the rules of today leave out
# what a page uses when it links it, on its own site too (design §4.4: a link to an attachment, a form, a short link)
_USED_KINDS = frozenset({"file", "form", "shortlink", "image"})
# a button the platform file says to press that no press of its page covers (postback.PageRead.unplanned: outside
# the grid the walk reads, a row without its title …): nobody presses it, so the page waits for a person 【Claude 的执行办法】
UNPRESSED = postback.UNPRESSED


class _Stopped(Exception):
    """The run is stopping: no new request goes out; the URL goes back to pending."""


class _Offline(Exception):
    """The network is down: the URL goes back to pending, and nothing counts against it."""


class _Paused(Exception):
    """The queue rests (a pause began while its row was being handled): nothing more is sent; the URL goes back to
    pending, and nothing counts against it."""


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


# design §7.2: images are page resources now (SSO's 465 image attachments too); only video and audio are never read
SKIP_TYPES = ("video/", "audio/")


def check_gap(gap: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(gap[0]), float(gap[1])
    if lo < MIN_GAP_S or hi < lo:
        raise ValueError(f"the gap is from at least {MIN_GAP_S:g} s up to no less than that (got {lo:g}–{hi:g} s)")
    return lo, hi


def archive_client(warc: WarcSink | None, *, resend_gap_s: float = GAP[0], **kw) -> Client:
    """The archive crawl's client (design §6.2, §6.4; B7): one kept-alive connection per server, one request per call
    (the frontier retries), a 4xx (but 408/429) handed back as an answer, redirects handed back, a body that cannot be
    decoded strictly handed back without text, and a resend after an idle close waiting at least the gap's least.
    kw: clock and sleep (the crawl passes its own, so request times are on its clock), a transport for tests."""
    return Client(warc=warc, follow_redirects=False, pass_statuses=PAGE_4XX, strict_decode=False,
                  limits=KEEPALIVE_LIMITS, skip_types=SKIP_TYPES, attempts=1, resend_gap_s=resend_gap_s, **kw)


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


def local_address() -> str | None:
    """This machine's address on its way out (B4: when it changes, the campus check is asked again): a UDP socket's
    connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


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
        if _DNS_TEXT.search(text):
            return "dns"
        if _CERT_TEXT.search(text):
            return "tls_cert"
        return "tls" if _TLS_TEXT.search(text) else "connect"
    if isinstance(cause, httpx.TransportError):
        return "net_" + type(cause).__name__
    m = _HTTP_TEXT.search(str(exc))
    return f"http_{m[1]}" if m else "fetch"


def _erred(kind: str | None) -> bool:
    """A failure that is the server erring (second review 10-26: 「出错」): a 5xx, a 408, a timeout, a connection it
    broke off; never a refusal to connect (B2's), a TLS failure, a 4xx answer or one we would not take."""
    return kind is not None and (kind.startswith(("http_5", "net_")) or kind in ("http_408", "timeout"))


def _door(url: str) -> tuple[str, int]:
    """The door of a server an address knocks on: its scheme and port."""
    parts = urlsplit(url)
    return parts.scheme, parts.port or (443 if parts.scheme == "https" else 80)


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


def _sends_back(url: str, other: str, location: str | None) -> bool:
    """Whether a 3xx from the other scheme's address (other) sends the visitor back to url's scheme on url's host."""
    target = scope.canonical(location or "", other)
    if target is None:
        return False
    a, b = urlsplit(url), urlsplit(target)
    return b.scheme == a.scheme and (b.hostname or "") == (a.hostname or "")


def _other_scheme(url: str) -> str | None:
    """The same address in the other scheme, when it names no port of its own (design §6.6)."""
    parts = urlsplit(url)
    if parts.port is not None or parts.scheme not in ("http", "https"):
        return None
    return urlunsplit(parts._replace(scheme="https" if parts.scheme == "http" else "http"))


@dataclass(frozen=True)
class Enqueued:
    """What admitting one address came to: the link table's verdict (queued, dup, round2:<why>, cap:<x>, failed:dns,
    scheme_switch, out:<rule>, unhandled:<why>), the url row's address and id (none for out and unhandled), and the
    placement round1 gave it."""

    verdict: str
    url: str | None
    id: int | None
    placement: round1.Placement | None


class _NetWatch:
    """Tells our network going down from a server's own failure. Answers and connection errors are noted per network
    ("school" = 140.127.*, "other"). A connection error on a network that answered within NET_WINDOW_S is that server's
    problem. Otherwise the connectivity check is asked (a fresh one once NET_QUEUES of that network's queues failed in
    that window, the cached one before): when it fails, the networks that answered nothing within NET_WINDOW_S pause
    (our network is down). When it passes and NET_QUEUES school servers failed with no school answer, the school's
    network is down (B2: the check alone, www.google.com, cannot tell). What connection errors cost (attempts,
    robots_fail, cool-downs, server pauses) is noted per network, so a pause can undo the costs on the networks it
    holds.

    After a pause the other network is assumed to work until it shows otherwise. The school's is not (second review
    10-26: the check asks www.google.com, which says nothing of the school's): it is unsure until one of its servers
    answers, and while it is a connection failure there is held against no server (Crawler._conn_failed)."""

    def __init__(self, clock: Callable[[], float], check: Callable[[], bool]):
        self.clock, self._check = clock, check
        self.lock = threading.Lock()
        self.last_answer: dict[str, float] = {}
        self.errors: list[tuple[float, str, str]] = []
        self.charges: list[tuple[float, str, tuple]] = []
        self.unsure: set[str] = set()  # networks whose pause is over and none of whose servers answered since
        self._checked_at, self._ok = -math.inf, True

    def answered(self, queue: str) -> None:
        with self.lock:
            net = network_of(queue)
            self.last_answer[net] = self.clock()
            self.unsure.discard(net)

    def grace(self, network: str | None = None) -> None:
        """After a pause (of every network, or of one): the other network is assumed to work until it shows otherwise;
        the school's is unsure until one of its servers answers."""
        nets = NETWORKS if network is None else (network,)
        with self.lock:
            now = self.clock()
            for n in nets:
                if n == "school":
                    self.unsure.add(n)
                else:
                    self.last_answer[n] = now
            self.errors = [e for e in self.errors if e[1] not in nets]

    def on_probation(self, queue: str) -> bool:
        """Whether the queue's network is unsure (its pause is over, none of its servers answered since)."""
        with self.lock:
            return network_of(queue) in self.unsure

    def check(self, fresh: bool = False) -> bool:
        now = self.clock()
        if fresh or now - self._checked_at > NETCHECK_CACHE_S:
            self._ok, self._checked_at = bool(self._check()), now
        return self._ok

    def down_after(self, queue: str) -> tuple[frozenset[str], bool]:
        """Called after a connection error from queue: (the networks to pause, whether it is ours: the check failed).
        Empty: the server's own problem."""
        now, net = self.clock(), network_of(queue)
        with self.lock:
            self.errors = [e for e in self.errors if now - e[0] <= NET_WINDOW_S] + [(now, net, queue)]
            if now - self.last_answer.get(net, -math.inf) <= NET_WINDOW_S:
                return frozenset(), False
            many = len({q for _, n, q in self.errors if n == net}) >= NET_QUEUES
        if self.check(fresh=many):
            if net == "school" and many:
                return frozenset({"school"}), False
            return frozenset(), False
        with self.lock:
            return frozenset(n for n in NETWORKS if now - self.last_answer.get(n, -math.inf) > NET_WINDOW_S), True

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
    def __init__(self, root: Path, *, plan: round1.Plan | None = None, limit: int | None = None,
                 clock: Callable[[], float] = wall, sleep: Callable[[float], None] = time.sleep,
                 resolver: Callable[[str], tuple] = resolve,
                 client_factory: Callable[[WarcSink | None], Client] | None = None, rng: random.Random | None = None,
                 site_cap: int = SITE_CAP, variant_cap: int = VARIANT_CAP, file_cap: int = FILE_CAP,
                 image_cap: int = IMAGE_CAP, netcheck: Callable[[], bool] = netcheck,
                 disk_free: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
                 gap: tuple[float, float] = GAP, sites: frozenset[str] | None = None,
                 campus_url: str | None = CAMPUS_URL, local_address: Callable[[], str | None] = local_address,
                 decide: Callable[[str, str], str | None] | None = profiles.decision, end_wait_s: float = END_WAIT_S,
                 google_step: bool = True):
        """plan: the site list (round1.load() when not given). gap: the run's (check_gap). sites: only these sites'
        rows (`archive run --sites`). campus_url: the campus check (None: no check, campus stays unknown). decide:
        the platform file's decision for a site and a writing's key (profiles.decision; None: nothing decided, every
        writing unknown). end_wait_s: a run ends when what is left comes due later than this (C0, `archive run
        --end-wait`). google_step: Drive, Docs and Calendar addresses go to google_api's step (False: they wait as
        unhandled:google, B8)."""
        if end_wait_s <= 0:
            raise ValueError(f"end_wait_s is a time in seconds greater than 0 (got {end_wait_s:g})")
        self.root = root
        self.gap = check_gap(gap)
        self.plan = plan if plan is not None else round1.load()
        self.frontier = Frontier(root / "frontier.db")
        self.limit, self.clock, self.sleep, self.resolver = limit, clock, sleep, resolver
        self.client_factory = client_factory or self._new_client
        self.rng = rng or random.Random()
        self.site_cap, self.variant_cap, self.file_cap, self.image_cap = site_cap, variant_cap, file_cap, image_cap
        self.disk_free = disk_free
        self.sites = frozenset(sites) if sites else None
        self.net = _NetWatch(clock, netcheck)
        self.stop = threading.Event()
        self.end_reason: str | None = None
        self.run_id = int(time.time())
        self.campus_url, self.local_address = campus_url, local_address
        self.campus = "unknown"  # on, off or unknown: written on every request row (B4)
        self._campus_at, self._campus_addr = -math.inf, None
        self.decide = decide or (lambda _site, _key: None)
        self.end_wait_s = float(end_wait_s)
        # the Google step (design §7.6): a Drive, Docs or Calendar address becomes its API rows; None: B8's wait
        self.google: round1.GoogleStep | None = google_api.step(self.plan) if google_step else None
        # the platform chains (design §7.5): the presses planned from stored pages, and every list's walk
        self.chains = postback.Chains(self.frontier, root, clock=clock)
        self._real_net = client_factory is None  # the real network: run() needs the machine-wide lock
        self._fetched = 0
        self._count_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._campus_lock = threading.Lock()
        self._door_lock = threading.Lock()
        self._tl = threading.local()  # row: the url row a worker is handling now (a scheme move hands it another)
        self._last_kind: dict[str, str] = {}
        self._prefer: dict[str, str] = {}
        self._answered_off: set[str] = set()
        self._dns_timed: dict[str, float] = {}
        self._doors: dict[str, dict[tuple[str, int], float]] = {}  # queue -> (scheme, port) -> its last answer
        self._stuck = 0  # loops in a row with rows due that no queue takes
        self._probe_tries: dict[tuple[str, str], int] = {}  # (host, detail type) -> id probes that got no answer

    def _new_client(self, warc: WarcSink | None) -> Client:
        return archive_client(warc, resend_gap_s=self.gap[0], clock=self.clock, sleep=self.sleep)

    def halt(self, reason: str) -> None:
        """Stop the run (its in-flight requests finish); the first reason given is the one run_end says."""
        if self.end_reason is None:
            self.end_reason = reason
        self.stop.set()

    # ---- discovery
    def queue_of(self, host: str) -> str:
        row = self.frontier.host(host)
        if row is not None and row["queue"] not in ("?", "", None):
            return row["queue"]
        named = google_api.queue_for(host)  # Google's APIs and its forms' host: a named queue each (design §6.1)
        if named is not None:
            self.frontier.set_host(host, queue=named, dns_at=self.clock(), dns_fail=0)
            return named
        started = time.perf_counter()
        queue, ip, cname = self.resolver(host)
        self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_at=self.clock(),
                               dns_fail=int(queue == "dns-fail"), dns_ms=(time.perf_counter() - started) * 1000)
        self._dns_timed[queue] = self.clock()
        return queue

    def _dns_step(self, queue: str) -> bool:
        """§6.2: TCP's connect time includes the name lookup, so each host of a queue has its lookup timed again every
        DNS_TIME_EVERY_S (host.dns_ms), on the queue's own thread; the answer never moves the host (that is
        _reresolve's, for names that did not resolve). Whether it timed them now."""
        if self.clock() - self._dns_timed.get(queue, -math.inf) < DNS_TIME_EVERY_S:
            return False
        self._dns_timed[queue] = self.clock()
        for host in self.frontier.hosts_of(queue):
            started = time.perf_counter()
            self.resolver(host)
            self.frontier.set_host(host, dns_ms=(time.perf_counter() - started) * 1000)
        return True

    def admit(self, href: str, base: str | None = None, from_row: Mapping[str, Any] | None = None,
              how: str = "link") -> round1.Admission | None:
        return round1.admit(self.plan, href, base, from_row, how, google=self.google)

    def enqueue(self, href: str, *, base: str | None = None, from_row: Mapping[str, Any] | None = None,
                how: str = "link", depth: int = 0, found_via: str = "link", prio: int | None = None,
                kind0: str | None = None, old_ref: int | None = None) -> str:
        """Admit one address (round1.admit) and add its url row; returns the link table's verdict."""
        return self._enqueue(href, base=base, from_row=from_row, how=how, depth=depth, found_via=found_via, prio=prio,
                             kind0=kind0, old_ref=old_ref).verdict

    def seed_url(self, url: str, *, source: str = "seed", found_via: str = "seed", prio: int | None = PRIO_LIST,
                 kind0: str | None = None, depth: int = 0, old_ref: int | None = None,
                 from_row: Mapping[str, Any] | None = None) -> str:
        """A seed (design §5): admitted with no page behind it, its source noted (seed_src: a URL may have several)."""
        e = self._enqueue(url, from_row=from_row, depth=depth, found_via=found_via, prio=prio, kind0=kind0,
                          old_ref=old_ref)
        if e.url is not None:
            self.frontier.seed_src(e.url, source)
        return e.verdict

    def seed_login_check(self, url: str) -> str:
        """One of the listed login addresses inside round-1 sites (design §4.2): one plain GET (kind0 login_check), its
        own login rule set aside; what comes back says whether it is one."""
        canon = scope.canonical(url)
        if canon is None:
            return "bad_url"
        s = self.plan.site(scope.site_key(canon))
        owner, tier = (s.site, s.tier) if s is not None and s.round == "1" else (scope.site_key(canon), round1.EXTRA_TIER)
        e = self._enqueue(canon, placement=round1.Placement("r1", None, owner, tier, 0, "login_check"), url=canon,
                          found_via="seed", prio=PRIO_LIST, kind0="login_check")
        if e.url is not None:
            self.frontier.seed_src(e.url, "login_check")
        return e.verdict

    def _enqueue(self, href: str, *, base: str | None = None, from_row: Mapping[str, Any] | None = None,
                 how: str = "link", depth: int = 0, found_via: str = "link", prio: int | None = None,
                 kind0: str | None = None, old_ref: int | None = None, found_from: int | None = None,
                 placement: round1.Placement | None = None, url: str | None = None) -> Enqueued:
        if placement is None:
            adm = self.admit(href, base, from_row, how)
            if adm is None:
                return Enqueued("bad_url", None, None, None)
            url, placement = adm.url, adm.placement
        if placement.state is None:  # out:<rule> or unhandled:<why>: the link table only
            return Enqueued(placement.reason or "out", None, None, placement)
        if found_from is None and from_row is not None:
            found_from = from_row["id"]
        kind0 = kind0 or placement.kind0
        rewritten = False
        if placement.state == "pending":
            moved = self._scheme_rewrite(url)
            rewritten, url = moved != url, moved
        if prio is None and kind0 == "page" and profiles.page_kind(url) == "list":
            prio = PRIO_LIST  # a platform's list page (design §7.4 list_paths: prio 0)
        prio = _PRIO_OF0.get(kind0, 1) if prio is None else prio
        existing = self.frontier.row(url)
        if existing is not None:
            self._upgrade(existing, placement, prio, kind0, found_from)
            return Enqueued("scheme_switch" if rewritten else "dup", url, existing["id"], placement)
        host = urlsplit(url).hostname or ""
        kind = _KIND_OF0.get(kind0, "page")
        state, reason, next_at = placement.state, placement.reason, 0.0
        if state == "pending":
            cap = self._cap(url, placement.owner_site, kind, depth)
            if cap is not None:
                state, reason = "held", cap
            else:
                state, reason, next_at = self._due(url, host)
        owner = placement.owner_site
        s = self.plan.site(owner) if owner else None
        added = self.frontier.add(url, host=host, site=scope.site_key(url), unit=(s.name or None) if s else None,
                                  tier=placement.tier, kind=kind, depth=depth, found_via=found_via,
                                  found_from=found_from, state=state, reason=reason, next_at=next_at,
                                  owner_site=owner, prio=prio, leaf=placement.leaf, kind0=kind0,
                                  added_at=self.clock(), old_ref=old_ref)
        row = self.frontier.row(url)
        if not added:
            return Enqueued("scheme_switch" if rewritten else "dup", url, row["id"] if row else None, placement)
        verdict = {"pending": "queued", "held": reason, "later": reason, "failed": f"failed:{reason}"}[state]
        return Enqueued("scheme_switch" if rewritten else verdict, url, row["id"], placement)

    def _due(self, url: str, host: str) -> tuple[str, str | None, float]:
        """A row this round fetches: (state, reason, next_at) by its host's name lookup and its directory's refusals."""
        queue = self.queue_of(host)
        if queue == "dns-fail":  # waits for the name to resolve (run() tries again)
            h = self.frontier.host(host)
            state = "failed" if h["dns_fail"] >= DNS_GIVE_UP else "pending"
            return state, "dns", h["dns_at"] + _dns_wait(h["dns_fail"])
        if host == google_api.API_HOST and not google_api.key_set():
            return "pending", "no_api_key", 0.0  # §7.6: waits for a run with the key (hold_for_key), no failure
        if (until := self.frontier.deny_until(deny_dir(url))) > self.clock():
            return "pending", "denied_dir", until  # its directory is waiting out a streak of refusals
        return "pending", None, 0.0

    def _upgrade(self, row, placement: round1.Placement, prio: int, kind0: str, found_from: int | None) -> None:
        """An address known already. Round two's row that a round-1 page now uses becomes this round's (the page's
        resource, design §4.4); one this round's page links again comes forward when it only had an old source
        (prio, §6.7: an old link or Common Crawl adds 1)."""
        if row["state"] == "later" and placement.state == "pending":
            owner = placement.owner_site
            s = self.plan.site(owner) if owner else None
            state, reason, next_at = self._due(row["url"], row["host"])
            self.frontier.x("UPDATE url SET state=?, reason=?, next_at=?, tier=?, owner_site=?, leaf=?, kind0=?, "
                            "kind=?, prio=?, found_from=coalesce(?, found_from), unit=? WHERE id=? AND state='later'",
                            (state, reason, next_at, placement.tier, owner, placement.leaf, kind0,
                             _KIND_OF0.get(kind0, "page"), prio, found_from, (s.name or None) if s else None,
                             row["id"]))
        elif prio < row["prio"]:
            self.frontier.x("UPDATE url SET prio=? WHERE id=? AND prio>?", (prio, row["id"], prio))

    def _cap(self, url: str, owner: str | None, kind: str, depth: int) -> str | None:
        """The cap a new row of this round would pass (design §9): held, never skipped, and listed."""
        if depth > MAX_DEPTH:
            return "cap:depth"
        if owner is None:
            return None
        pk = profiles.page_kind(url)
        if pk == "detail" and kind == "page":
            return None  # a platform's detail pages are free of the caps (design §7.4 detail_paths: 免上限)
        if kind == "file" and self.frontier.count_site(owner, "file") >= self.file_cap:
            return "cap:file"
        if kind == "image" and self.frontier.count_site(owner, "image") >= self.image_cap:
            return "cap:image"
        if kind == "page" and self.frontier.count_site(owner, "page") >= self.site_cap:
            return "cap:site"
        if (kind in ("page", "file") and not scope.is_platform_path(url) and pk != "list"  # lists: no variant cap
                and self.frontier.count_path(owner, url.split("?", 1)[0]) >= self.variant_cap):
            return "cap:variant"
        return None

    def _scheme_rewrite(self, url: str) -> str:
        """An address of a host known to answer on one scheme only is written in that one (design §6.6)."""
        parts = urlsplit(url)
        h = self.frontier.host(parts.hostname or "")
        if h is None or not h["scheme_only"] or h["scheme_only"] == parts.scheme or parts.port is not None:
            return url
        return urlunsplit(parts._replace(scheme=h["scheme_only"]))

    def _reresolve(self, force: bool = False) -> None:
        """Names that did not resolve, tried again when their back-off is over (all of them when force). Every pending
        row of such a host waits for its name, whatever brought it (a row `retry` put back keeps no next_at of its
        own: second review 10-26), and its reason says so (dns); nothing was ever sent to a name that never resolved,
        so when it resolves they are all due."""
        now = self.clock()
        for h in self.frontier.q("SELECT host, dns_fail, dns_at FROM host WHERE queue='dns-fail' AND dns_fail < ?",
                                 (DNS_GIVE_UP,)):
            host = h["host"]
            if not force and now < h["dns_at"] + _dns_wait(h["dns_fail"]):
                continue
            queue, ip, cname = self.resolver(host)
            if queue != "dns-fail":
                self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_fail=0, dns_at=now)
                n = self.frontier.x("UPDATE url SET next_at=0, reason=CASE WHEN reason='dns' THEN NULL ELSE reason END "
                                    "WHERE host=? AND state='pending'", (host,))
                self.frontier.event("info", "dns_back", f"resolves now ({queue}): {n} URLs due", host)
                continue
            fails = h["dns_fail"] + (1 if self.net.check() else 0)  # an outage is not the name's fault
            self.frontier.set_host(host, dns_fail=fails, dns_at=now)
            if fails >= DNS_GIVE_UP:
                n = self.frontier.x("UPDATE url SET state='failed', reason='dns' WHERE host=? AND state='pending'",
                                    (host,))
                self.frontier.event("warn", "dns_gave_up", f"did not resolve {fails} times: {n} URLs failed "
                                    "(nknu archive retry dns tries again)", host)
            else:
                self.frontier.x("UPDATE url SET next_at=max(next_at, ?), reason='dns' WHERE host=? AND state='pending'",
                                (now + _dns_wait(fails), host))

    def _place_hosts(self) -> int:
        """The hosts of pending rows that nobody looked up (second review 10-26: `retry` brings back round two's rows
        and those a cap held, which never had their host looked up; the old archive's hosts are seeded with queue '?'):
        looked up now, before any is fetched, so that each goes out on its server's own queue. One that does not
        resolve waits like any other such name (reason dns, its back-off). Returns how many hosts were looked up."""
        hosts = [r[0] for r in self.frontier.q(
            "SELECT DISTINCT u.host FROM url u LEFT JOIN host h ON h.host = u.host "
            "WHERE u.state = 'pending' AND (h.host IS NULL OR h.queue IN ('?', ''))")]
        for host in hosts:
            if self.queue_of(host) == "dns-fail":
                h = self.frontier.host(host)
                self.frontier.x("UPDATE url SET next_at=max(next_at, ?), reason='dns' WHERE host=? AND state='pending'",
                                (h["dns_at"] + _dns_wait(h["dns_fail"]), host))
        if hosts:
            log.info("looked up %d hosts nobody had looked up", len(hosts))
        return len(hosts)

    # ---- running
    def run(self, poll_s: float = 5.0) -> None:
        """The caller holds run.lock (hold_run_lock) and the school lock: only then may rows left active be taken back.
        On the real network a run that does not hold the school lock is refused before any row changes
        (schoollock.SchoolLockNotHeld: second review 10-26). Ends when nothing is left, when what is left only waits past
        END_WAIT_S (B1), when rows are due that no queue can take (stuck), or when halted."""
        if self._real_net and not schoollock.held():
            raise schoollock.SchoolLockNotHeld(f"a crawl on the real network holds the machine-wide lock on school "
                                               f"requests ({schoollock.lock_path()}) for its whole run: none was taken")
        n = self.frontier.requeue_active()
        if n:
            log.info("requeued %d URLs left active by the last run", n)
        chains = self.chains.resume()  # §7.5 step 9: a chain left pressing starts again from its GET
        if chains:
            log.info("%d lists start their walk again from their GET (left pressing by the last run)", chains)
        google_api.hold_for_key(self.frontier, self.clock())  # §7.6: no key, the googleapis queue waits (no failure)
        sites = sorted(self.sites) if self.sites else None
        self.frontier.event("info", "run_start", json.dumps({"pid": os.getpid(), "gap": list(self.gap),
                                                             "sites": sites, "end_wait_s": self.end_wait_s,
                                                             "school_lock": str(schoollock.lock_path())}))
        workers: dict[str, threading.Thread] = {}
        try:
            self._reresolve(force=True)
            self._place_hosts()
            while not self.stop.is_set():
                if self._limit_reached():
                    self.halt("limit")
                    break
                self._reresolve()
                if not self._paused():
                    now = self.clock()
                    due = self.frontier.queues(now, self.sites)
                    for q in self.chains.due_queues(now, self.sites):  # a queue whose only work is a press
                        due.setdefault(q, [])
                    # the campus check goes on SSO's queue, and only when that queue may send: a worker started for a
                    # check its paused queue cannot make would idle, and keep the run from ever ending (second review)
                    if (self._campus_due() and (cq := self.campus_queue()) not in (None, "dns-fail")
                            and self.frontier.paused_until(cq) <= now):
                        due.setdefault(cq, [])
                    for queue in due:
                        if queue == "dns-fail":
                            continue
                        t = workers.get(queue)
                        if t is None or not t.is_alive():
                            t = threading.Thread(target=self._work, args=(queue,), name=f"q-{queue}", daemon=True)
                            workers[queue] = t
                            t.start()
                if not any(t.is_alive() for t in workers.values()) and self._should_end():
                    break
                self.stop.wait(poll_s)
        except sqlite3.DatabaseError:
            log.exception("the frontier database failed, stopping the run")
            self.halt("db_error")
        finally:
            self.stop.set()
            for t in workers.values():
                t.join(timeout=120)
            with suppress(sqlite3.Error):
                self.chains.sso_totals()  # §7.5 step 7: SSO 公告中心's categories add up to its whole list
            with suppress(sqlite3.Error):
                left, nxt = self.frontier.waiting(self.sites)
                presses, p_nxt, _active = self.chains.waiting(self.sites)
                nxt = min((t for t in (nxt, p_nxt) if t is not None), default=None)
                self.frontier.event("info", "run_end", json.dumps({"reason": self.end_reason or "stop", "sites": sites,
                                                                   "pending": left, "presses": presses,
                                                                   "next_due": nxt}))

    def _should_end(self) -> bool:
        """With no worker alive: whether the run ends now (and why, in end_reason). Nothing left: finished. Rows still
        active have no worker to hold them (a bug's): each is a crash, and the run looks again. The soonest row due
        later than END_WAIT_S: waiting (B1). Rows due now that no queue takes (none looked up, or one nobody can
        dispatch): the hosts are looked up once more, and if that is not it two loops in a row, the run ends (stuck),
        with an error event, instead of waiting for ever with both locks held."""
        left, nxt = self.frontier.waiting(self.sites)
        presses, p_nxt, active = self.chains.waiting(self.sites)
        if left == 0 and presses == 0:
            self.end_reason = self.end_reason or "finished"
            log.info("nothing left to fetch")
            return True
        if active:  # presses left active with no worker holding them (a bug's): sent or not, never sent again
            for list_id in sorted({a["list_id"] for a in active if a["kind"] != "click"}):
                self.chains.restart_chain(list_id, "crash:left_active")
            n = self.frontier.x("UPDATE action SET state='failed', reason='crash:left_active' WHERE kind='click' "
                                "AND state='active'")
            self.frontier.event("error", "left_active", f"{len(active)} presses were left active with no worker "
                                f"holding them (a crawler bug): their lists start again from their GET, {n} file "
                                "buttons failed as crash:left_active (nknu archive retry click puts them back)")
            return False
        if presses:  # what the presses wait for counts as the rows' waits do
            nxt = min((t for t in (nxt, p_nxt) if t is not None), default=None)
            if nxt is None:
                return False  # only active rows and presses being restarted: the run looks again
        if nxt is None:  # only active rows, and no worker holds them
            n = self.frontier.x("UPDATE url SET state='failed', reason='crash:left_active' WHERE state='active'")
            self.frontier.event("error", "left_active", f"{n} URLs were left active with no worker holding them (a "
                                "crawler bug): failed as crash:left_active (nknu archive retry crash tries again)")
            return False
        now = self.clock()
        if nxt - now > self.end_wait_s:
            self.end_reason = self.end_reason or "waiting"
            log.info("only rows that wait for later are left (%d, %d presses): left for the next run", left, presses)
            return True
        if (nxt <= now and not [q for q in self.frontier.queues(now, self.sites) if q != "dns-fail"]
                and not self.chains.due_queues(now, self.sites)):
            if self._place_hosts():
                self._stuck = 0
                return False
            self._stuck += 1
            if self._stuck >= 2:
                self.frontier.event("error", "stuck", f"{left} URLs are due that no queue takes (a crawler bug): the "
                                    "run ends instead of waiting for ever")
                self.end_reason = self.end_reason or "stuck"
                return True
            return False
        self._stuck = 0
        return False

    def _limit_reached(self) -> bool:
        with self._count_lock:
            return self.limit is not None and self._fetched >= self.limit

    def _count(self) -> None:
        with self._count_lock:
            self._fetched += 1
            if self.limit is not None and self._fetched >= self.limit:
                self.halt("limit")

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
                                    else "the school network's pause is over: its queues try again, and no connection "
                                    "failure is held against its servers until one of them answers"
                                    if network == "school" else f"the {network} network is back: its queues resume")
            return False

    def _next(self, queue: str) -> sqlite3.Row | None:
        """The queue's next row, as its worker takes it: the run's sites only, the host it is on first."""
        return self.frontier.next_for(self.frontier.hosts_of(queue), self.clock(), prefer_host=self._prefer.get(queue),
                                      sites=self.sites)

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
                if not paused and self._step(self._campus_step, queue, client, sink):
                    continue
                self._dns_step(queue)
                # a chain's next press goes before the queue's url rows (design §6.7, §7.5 step 4)
                press = None if paused else self.chains.next_action(queue, self.clock(), self.sites)
                if press is not None:
                    idle_since = None
                    self._prefer[queue] = press["host"]
                    self._press(queue, client, sink, press)
                    continue
                row = None if paused else self._next(queue)
                if row is None:
                    idle_since = idle_since or self.clock()
                    if self.clock() - idle_since > IDLE_EXIT_S:
                        return
                    due = None if paused else self.frontier.next_due(self.frontier.hosts_of(queue))
                    self.stop.wait(min(IDLE_POLL_S, max(0.05, due - self.clock())) if due else IDLE_POLL_S)
                    continue
                idle_since = None
                self._prefer[queue] = row["host"]
                self._handle(queue, client, sink, row)
        finally:
            with suppress(Exception):
                client.close()
            with suppress(Exception):
                sink.close()

    def _step(self, step, queue: str, client: Client, sink: WarcSink) -> bool:
        """A step of a worker that is no row's (the campus check): what stops the run stops it."""
        try:
            return bool(step(queue, client, sink))
        except (_Stopped, _Offline, _Paused):
            return False
        except (ArchiveWriteError, _DiskLow):
            self.halt("warc_write")
            return False
        except schoollock.SchoolLockNotHeld as exc:
            self._no_school_lock(exc, None)
            return False

    def _no_school_lock(self, exc: Exception, row) -> None:
        """A request refused before it was sent because this process does not hold the machine-wide lock: a program's
        bug, never the page's (second review 10-26): the run stops, the row waits as it was."""
        self.frontier.event("error", "no_school_lock", f"{exc} — the run stops (nknu archive run takes the lock)"[:500],
                            row["host"] if row else None, row["url"] if row else None)
        self.halt("no_school_lock")

    def _handle(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        """One row, whatever happens: the run stops for what no page can fix (archive, disk, database, the school
        lock). What happens is the row's the worker is handling at that moment: a scheme move hands it the row of the
        address in the other scheme (self._tl.row), whose crash is its own, and the row it came from stays moved
        (second review 10-26: it was left active, and the run never ended)."""
        self._tl.row = row["id"]
        try:
            self._process(queue, client, sink, row)
            self._walk_check(self._tl.row)
        except (_Stopped, _Paused):
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
        except _Offline:
            self.frontier.finish(self._tl.row, state="pending", reason="net_down", fetched_at=None)
            for h in self.frontier.hosts_of(queue):
                client.reset_host(h)
        except (ArchiveWriteError, _DiskLow) as exc:
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
            if isinstance(exc, ArchiveWriteError):
                self.frontier.event("error", "warc_write", f"{exc} — the run stops; fix the archive directory, then "
                                    "nknu archive run again"[:500], row["host"], row["url"])
            self.halt("warc_write" if isinstance(exc, ArchiveWriteError) else "disk_low")
        except sqlite3.DatabaseError:
            log.exception("queue %s: the frontier database failed, stopping the run", queue)
            self.halt("db_error")
        except schoollock.SchoolLockNotHeld as exc:
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
            self._no_school_lock(exc, row)
        except Exception as exc:  # one bad page never stops the queue
            current = self.frontier.row_by_id(self._tl.row) or row
            log.exception("queue %s: %s", queue, current["url"])
            self.frontier.finish(current["id"], state="failed", reason=f"crash:{type(exc).__name__}")
            self.frontier.event("error", "crash", f"{type(exc).__name__}: {exc}"[:500], current["host"],
                                current["url"])

    def _walk_check(self, uid: int) -> None:
        """A row some GET-paged list's pager links to has its answer, or none for good (failed, held, skipped, a file
        where a page was expected): that list's walk again (postback.get_walk_for), so that it never stays walking
        or claims an end its last page did not show. A row still waiting changes nothing."""
        r = self.frontier.row_by_id(uid)
        if r is not None and r["state"] not in ("pending", "active"):
            self.chains.get_walk_for(uid)

    # ---- a press (design §7.4 clicks, §7.5)
    def _press(self, queue: str, client: Client, sink: WarcSink, a) -> None:
        """One press of a chain or of a file button, whatever happens: a press that never went out (the run stops, the
        queue rests, the network is down) waits as pending; one that may have reached the server is never sent again
        (design §6.4: a POST is sent once). What stops the run stops it."""
        try:
            self._press_one(queue, client, sink, a)
        except (_Stopped, _Paused, _Offline) as exc:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            if isinstance(exc, _Offline):
                for h in self.frontier.hosts_of(queue):
                    client.reset_host(h)
        except _DiskLow:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            self.halt("disk_low")
        except ArchiveWriteError as exc:  # sent, its answer not kept: failed, never sent again
            self.chains.failed(a, "warc_write")
            self.frontier.event("error", "warc_write", f"{exc} — the run stops; fix the archive directory, then "
                                "nknu archive run again"[:500], a["host"], a["target"])
            self.halt("warc_write")
        except sqlite3.DatabaseError:
            log.exception("queue %s: the frontier database failed, stopping the run", queue)
            self.halt("db_error")
        except schoollock.SchoolLockNotHeld as exc:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            self._no_school_lock(exc, None)
        except Exception as exc:  # one bad press never stops the queue
            log.exception("queue %s: press %s", queue, a["id"])
            self._press_dead(a, f"crash:{type(exc).__name__}")
            self.frontier.event("error", "crash", f"press {a['id']}: {type(exc).__name__}: {exc}"[:500], a["host"],
                                a["target"])

    def _press_dead(self, a, reason: str) -> None:
        """A press that came to nothing the chain can go on from, and that no automatic start again would mend (an
        answer too large to keep, a crash): failed; a next page's ends its walk as post_failed (`nknu archive retry
        post_failed` walks it again once a person looked)."""
        self.frontier.x("UPDATE action SET state='failed', reason=?, done=? WHERE id=? AND state IN ('active',"
                        "'pending')", (reason, self.clock(), a["id"]))
        if a["kind"] == "pager":
            self.chains._walk_end(a, "post_failed", f"page {a['step']}: {reason}")

    def _press_one(self, queue: str, client: Client, sink: WarcSink, a) -> None:
        """Build the POST from the page it was planned on as archived (postback.target_for), send it once on the
        list's own server queue after the gap like any request, and hand the answer back (_press_answer)."""
        if self.disk_free(self.root) < MIN_FREE_BYTES:
            self.frontier.event("error", "disk_low", f"less than {MIN_FREE_BYTES // 1024**3} GB free under "
                                f"{self.root}: the run stops before the archive cannot be written", a["host"],
                                a["target"])
            raise _DiskLow
        list_row = self.frontier.row_by_id(a["list_id"])
        if list_row is None:
            self._press_dead(a, "no_list_row")
            return
        target = self.chains.target_for(a)
        if target is None:  # the press cannot be built from its page (mismatch, written by target_for)
            if a["kind"] == "pager":  # the walk cannot go on: never last_page
                reason = self.frontier.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0]
                self.chains._walk_end(a, "mismatch:page", f"page {a['step']}: no press could be built ({reason})")
            return
        host = urlsplit(target.url).hostname or a["host"]
        got = self._request(queue, client, target, "postback", scope_reason=self._admitted_as(list_row))
        if isinstance(got, HostCircuitOpen):  # nothing was sent
            self._cool_host(host)
            client.reset_host(host)
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            return
        self._count()
        if isinstance(got, RetryLater):  # the server asked us to wait: it may have read the POST, never sent again
            self._rest_queue(queue, client, host, target.url, got)
            self.chains.failed(a, "retry_after")
            return
        if isinstance(got, TooLarge):  # answered, too large to keep: nobody presses it again by itself
            self._press_dead(a, "media_type" if isinstance(got, UnwantedType) else "too_large")
            return
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind in CONN_KINDS:  # it never reached the server: the server's failure (B2), the press waits

                def back() -> None:
                    self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))

                self._conn_failed(queue, client, {"host": host, "id": None}, target.url, got, back=back,
                                  fail=lambda: self.chains.failed(a, kind))
                return
            self.chains.failed(a, kind)  # a timeout, a TLS failure, a 5xx: the chain starts again from its GET once
            return
        self._answered(host, target.url, got.status)
        self._press_answer(a, list_row, got, sink)

    def _press_answer(self, a, list_row, got: Snapshot, sink) -> None:
        """An answer to a press (design §7.5 step 6). A page that answers a detail or a next page is read as the list
        page's (its links and parts, _parts with the list row) before postback.answered() checks it; a redirect's
        Location is the list page's resource (link table: [postback]); a file button's address is the attachment of
        every page that shows the button (§7.4)."""
        warc = self._warc_path(sink)
        list_id = a["list_id"]
        ctype = got.headers.get("content-type", "")
        if got.status == 200 and a["kind"] in ("pager", "detail") and sniff.sniff(got.body, ctype) == "html":
            text = got.text if got.text is not None else _lenient(got.body, ctype)
            page = extract(text, got.url, profiles.content_css(got.url))
            entries, _n = self._parts(list_row, page, list_id, html=text, warc=warc, warc_offset=got.warc_offset)
            self.frontier.add_page_res(list_id, entries)
            google_api.resync(self.frontier, list_id)
        out = self.chains.answered(a, got, warc)
        if out.location is not None:
            e = self._resource_of(out.location, list_row, "redirect", list_row, [list_id])
            self.frontier.link(list_id, out.location, "[postback]", e.verdict)
        for f in out.files:
            pages = [r[0] for r in self.frontier.q("SELECT DISTINCT page_id FROM page_res WHERE action_id=?",
                                                   (a["id"],))] or [list_id]
            e = self._resource_of(f, list_row, "click", list_row, pages, kind0="file")
            self.frontier.link(list_id, f, "[click]", e.verdict)
            if e.id is not None:
                for pid in pages:
                    self.frontier.add_page_res(pid, [{"res_key": f, "how": "get", "role": "file", "need": "must",
                                                      "res_id": e.id}])

    def _resource_of(self, target: str, page_row, found_via: str, via, page_ids: list[int],
                     kind0: str | None = None) -> Enqueued:
        """An address a press led to, as the page's (design §7.5 step 6, §7.4): judged like a link of that page (a
        Drive or Docs address by the Google step, its entries on the pages; a short link asked where it leads);
        kind0 file: a file button's answer, the page's attachment unless a rule leaves the address out."""
        adm = self.admit(target, None, page_row, "link")
        if adm is None:
            return Enqueued("bad_url", None, None, None)
        if adm.step == "google" and self.google is not None:
            return self._google_users(adm, page_row, via, "link", found_via, page_ids)
        placement = adm.placement
        if kind0 == "file" and adm.step == "place" and placement.verdict in ("r1", "round2"):
            placement = self._transclude(adm.url, page_row, "file")  # the page's attachment, wherever it is
        return self._enqueue(target, placement=placement, url=adm.url, from_row=page_row,
                             depth=page_row["depth"] + 1, found_via=found_via, found_from=via["id"])

    # ---- the wait after each request (design §6.3)
    def _gap(self, host: str, took: float) -> None:
        """The run's gap after a response, on this queue, never cut short, and never less than what the answer took
        (at most SLOW_CAP_S). A longer Crawl-delay of the host (at most 300 s) is that host's own wait: it may not be
        asked again before not_before, the other hosts on the server may."""
        ended = self.clock()
        delay = max(self.rng.uniform(*self.gap), min(max(took, 0.0), SLOW_CAP_S))
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

    def _crawl_delay(self, host: str) -> None:
        """Before any request: a host still within its Crawl-delay is waited for (at most MAX_CRAWL_DELAY_S), whatever
        sends it (second review 10-26: robots.txt's redirects and its other scheme, a page's other scheme went out
        after the gap only). A row's worker hands its row back at the three places a wait is foreseen (_not_due); this
        is for the requests one row makes in a row."""
        h = self.frontier.host(host)
        wait = (h["not_before"] or 0) - self.clock() if h is not None else 0.0
        if wait > 0:
            self.sleep(min(wait, MAX_CRAWL_DELAY_S))

    def _send(self, queue: str, client: Client, target: Target, purpose: str, pass_statuses: frozenset[int] | None,
              scope_reason: str | None, robots_sha: str | None, max_bytes: int = MAX_BYTES) -> Snapshot | FetchError:
        """One fetch. Whatever follows a request that may have reached the server (an answer, a failure, an
        exception of any kind), each request it put on the wire is logged and the full gap is waited."""
        host = urlsplit(target.url).hostname or ""
        if self.stop.is_set():
            raise _Stopped
        if self.frontier.paused_until(queue) > self.clock():  # a rest began while this row was being handled
            raise _Paused
        self._crawl_delay(host)
        started, status, error, sent, got = self.clock(), None, None, True, None
        try:
            got = client.fetch(target, NO_RATE, max_bytes=max_bytes, pass_statuses=pass_statuses)
            status = got.status
            return got
        except HostCircuitOpen as exc:  # refused before sending: nothing reached the server, nothing to wait for
            sent = False
            return exc
        except FetchError as exc:
            got, error = exc, str(exc)[:300]
            return exc
        except ArchiveWriteError as exc:
            got, status, error = exc, exc.status, f"warc_write: {exc}"[:300]
            raise
        except schoollock.SchoolLockNotHeld:  # refused before sending: a program's bug, never a request
            sent = False
            raise
        except BaseException as exc:
            error = f"crash:{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            if sent:
                try:
                    self._log(queue, host, target.url, purpose, started, got, status, error, scope_reason, robots_sha,
                              client)
                finally:
                    self._gap(host, self.clock() - started)

    def _log(self, queue: str, host: str, url: str, purpose: str, started: float, got, status: int | None,
             error: str | None, scope_reason: str | None, robots_sha: str | None, client) -> None:
        """One request row per request on the wire (B7: fetch.Sent, a resend after an idle close included); a client
        that says nothing of its wire (a test's) gets one row."""
        wire = getattr(got, "sent", ()) or ()
        sink = getattr(client, "warc", None)
        warc = self._warc_path(sink) if sink is not None else None
        if not wire:
            self.frontier.request(queue, host, url, purpose, started, self.clock(), status, error, scope=scope_reason,
                                  robots_sha=robots_sha, campus=self.campus)
        for s in wire:
            self.frontier.request(queue, host, url, purpose, s.started, s.ended, s.status, s.error,
                                  scope=scope_reason, robots_sha=robots_sha, attempt=s.attempt, method=s.method,
                                  bytes=s.bytes, new_conn=s.new_conn, connect_ms=s.connect_ms, tls_ms=s.tls_ms,
                                  ip=s.ip, warc=warc if s.warc_offset is not None else None,
                                  warc_offset=s.warc_offset, campus=self.campus)
        answered = status is not None or any(s.status is not None for s in wire)
        if answered and self.campus != "on" and host not in self._answered_off:
            self._answered_off.add(host)
            self.frontier.set_host(host, answered_off=1)

    def _warc_path(self, sink) -> str | None:
        try:
            return str(Path(sink.path).relative_to(self.root))
        except (ValueError, AttributeError, TypeError):
            return str(getattr(sink, "path", "")) or None

    def _request(self, queue: str, client: Client, target: Target, purpose: str,
                 pass_statuses: frozenset[int] | None = None, scope_reason: str | None = None,
                 robots_sha: str | None = None, max_bytes: int = MAX_BYTES) -> Snapshot | FetchError:
        got = self._send(queue, client, target, purpose, pass_statuses, scope_reason, robots_sha, max_bytes)
        if isinstance(got, HostCircuitOpen):
            return got
        host = urlsplit(target.url).hostname or ""
        kind = error_kind(got) if isinstance(got, FetchError) else None
        if kind is not None:
            self._last_kind[host] = kind
        if kind is None or kind.startswith("http_") or kind == "retry_after":  # an answer, a 5xx too: it is there
            self._server_ok(queue)
            self._door_answered(queue, target.url)
        if _erred(kind):
            self._server_erred(queue, client, host, target.url, kind)
        elif kind is None:
            self.frontier.server_err_clear(queue)
        if kind not in NET_KINDS:
            self.net.answered(queue)  # something on that network answered (a 5xx is an answer too)
            return got
        networks, ours = self.net.down_after(queue)
        if networks:
            self._go_offline(host, networks, ours)
            raise _Offline
        return got

    def _server_ok(self, queue: str) -> None:
        if self.frontier.server_fails(queue):
            self.frontier.server_answered(queue, self.clock())

    def _go_offline(self, host: str, networks: frozenset[str], ours: bool) -> None:
        """Pause the networks that do not answer (every queue when none does) and undo what their connection errors
        of the last minutes cost. ours: the connectivity check failed (5 min, then checked again); else the school's
        network is down while ours works (B2: 15 min, then its servers are asked again)."""
        now = self.clock()
        every = networks >= frozenset(NETWORKS)
        keys = [ALL_QUEUES] if every else [net_key(n) for n in sorted(networks)]
        first = all(self.frontier.paused_until(k) <= now for k in keys)
        wait = NET_PAUSE_S if ours else CONN_PAUSE_S
        what = "every queue" if every else " and ".join(
            {"school": "the school network (140.127.*)", "other": "the other (off-campus) network"}[n]
            for n in sorted(networks)) + "'s queues"
        for key in keys:
            self.frontier.pause(key, now + wait, "the network is down" if every else
                                "this network does not answer and the connectivity check failed" if ours else
                                "the school's servers do not connect while the internet answers")
        undone = self._undo(self.net.take_charges(networks))
        if first:
            why = ("the network is down" if ours else
                   f"{NET_QUEUES} school servers do not connect while the internet works: the school's network is down")
            self.frontier.event("warn", "offline", f"{why}: {what} pause {wait / 60:.0f} min; "
                                f"{undone} connection failures are not held against URLs, hosts or servers", host)

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
            elif kind == "conn":
                queue, fails, until = args
                self.frontier.server_set(queue, fails)
                if until > 0:
                    self.frontier.x("UPDATE pause SET until=? WHERE queue=?", (until, queue))
                else:
                    self.frontier.unpause(queue)
        return len(charges)

    def _cool_host(self, host: str) -> float:
        """The client's breaker (three failures in a row on this name): the name cools down a fixed 15 min (second
        review 10-26: it doubled up to a day). The whole server's rest for its errors is _server_erred's."""
        wait = COOLDOWN[0]
        self.frontier.set_host(host, cooldown_until=self.clock() + wait, cooldown_s=wait)
        return wait

    def _server_erred(self, queue: str, client: Client, host: str, url: str, kind: str) -> None:
        """The server erred (_erred): one more in a row, whichever of its names. A 503 (「太忙」) rests the whole server
        queue at once, BREAKER_THRESHOLD in a row rest it too: a fixed SERVER_REST_S, never doubled (second review
        10-26: CLAUDE.md 「出错或回『太忙』整台休息」, design §6.3 「整台服务器先休息」, record 一·3 「15 分钟以上」).
        The request that erred costs its URL an attempt as before (_failed); the rows the rest sends back cost
        nothing."""
        n = self.frontier.server_erred(queue)
        if kind not in REST_NOW and n < BREAKER_THRESHOLD:
            return
        until = self.clock() + SERVER_REST_S
        self.frontier.pause(queue, until, f"{host}: {kind}" + ("" if kind in REST_NOW else f" ({n} errors in a row)"))
        self.frontier.server_err_clear(queue)
        for h in self.frontier.hosts_of(queue):
            client.reset_host(h)
        why = "said it is too busy (503)" if kind in REST_NOW else f"erred {n} times in a row (last: {kind})"
        self.frontier.event("warn", "server_rest", f"server queue {queue} {why}: it rests {SERVER_REST_S / 60:.0f} min, "
                            "every name on it (no URL loses an attempt for the rest)", host, url)

    # ---- doors: a server that answers on one scheme or port and refuses another is up
    def _door_answered(self, queue: str, url: str) -> None:
        with self._door_lock:
            self._doors.setdefault(queue, {})[_door(url)] = self.clock()

    def _closed_door(self, queue: str, url: str) -> bool:
        """Whether a connection refused at this address's door is that door's (closed: a port or a scheme the server
        does not serve) rather than the server's: its other doors answered within DOOR_WINDOW_S, this one did not."""
        now, door = self.clock(), _door(url)
        with self._door_lock:
            doors = dict(self._doors.get(queue, {}))
        if now - doors.get(door, -math.inf) <= DOOR_WINDOW_S:
            return False
        return any(now - t <= DOOR_WINDOW_S for d, t in doors.items() if d != door)

    def _rest_queue(self, queue: str, client: Client, host: str, url: str, exc: RetryLater) -> float:
        """The server asked us to come back later: every host on it waits that long (at least 15 min, at most a day)."""
        wait = min(max(exc.seconds, COOLDOWN[0]), COOLDOWN[1])
        until = self.clock() + wait
        self.frontier.pause(queue, until, f"Retry-After {exc.seconds:.0f} s from {host}")
        client.reset_host(host)  # the wait is kept here, in the frontier, not in the client's "skip this run"
        self.frontier.event("warn", "retry_after", f"{host} asked for {exc.seconds:.0f} s (Retry-After): server queue "
                            f"{queue} rests {wait / 60:.0f} min", host, url)
        return until

    # ---- a server that does not connect (B2)
    def _conn_failed(self, queue: str, client: Client, row, url: str, exc: FetchError | None, *,
                     back: Callable[[], None] | None = None, fail: Callable[[], None] | None = None) -> None:
        """No connection at this address. A door (scheme, port) of a server that answers on its other doors is closed:
        the URL's failure (_failed: an attempt, retried later), no server pause (second review 10-26: else one such
        address would pause its whole server each time it came up, for ever). Otherwise the server does not connect:
        its whole queue rests CONN_PAUSE_S (fixed) and the row waits, no URL ever losing an attempt for it (B2; second
        review: it used to cost every row of the server one attempt every CONN_GIVE_UP failures). The failure counts
        against the server only while its network is known to work: not during a pause of that network, nor after
        the school's pause until one of its servers answered (_NetWatch.unsure). At CONN_GIVE_UP in a row,
        _server_down. back, fail: what a press does instead of a row (a POST that never reached the server waits;
        one at a closed door fails, never sent again)."""
        host = row["host"]
        if self._closed_door(queue, url):
            if fail is not None:
                fail()
            else:
                self._failed(queue, row, exc, kind="connect")
            return
        now = self.clock()
        before, before_pause = self.frontier.server_fails(queue), self.frontier.pause_of(queue)
        net = network_of(queue)
        unsure = (self.net.on_probation(queue)
                  or max(self.frontier.pause_of(net_key(net)), self.frontier.pause_of(ALL_QUEUES)) > now)
        n = before if unsure else self.frontier.server_failed(queue, now)
        self.frontier.pause(queue, now + CONN_PAUSE_S, f"{host}: no connection" + ("" if unsure else
                                                                                   f" ({n} in a row)"))
        self.net.charge(queue, "conn", queue, before, before_pause)
        for h in self.frontier.hosts_of(queue):
            client.reset_host(h)
        if back is not None:
            back()
        else:
            self._back(row["id"], "conn_pause")
        if n == 1 and not unsure:
            self.frontier.event("warn", "conn_pause", f"{host} does not connect: server queue {queue} rests "
                                f"{CONN_PAUSE_S / 60:.0f} min (no URL loses an attempt)", host, url)
        if n >= CONN_GIVE_UP and not unsure:
            self._server_down(queue, n)

    def _give_up_hosts(self, hosts: list[str], kind: str) -> tuple[int, int]:
        """Hosts that did not answer for hours: a school host that never answered us off campus may be one only the
        school's network reaches (host_unreachable: the lab machine tries again, record 三·19); any other is down for
        now (B3), and each of its rows loses one attempt (reason kind), three in all. Returns (lab, charged)."""
        now, lab, charged = self.clock(), 0, 0
        for host in hosts:
            h = self.frontier.host(host)
            if schoollock.is_school_host(host) and not (h is not None and h["answered_off"]):
                lab += self.frontier.x("UPDATE url SET state='failed', reason='host_unreachable' WHERE host=? AND "
                                       "state IN ('pending','active')", (host,))
                continue
            for r in self.frontier.q("SELECT id, attempts FROM url WHERE host=? AND state IN ('pending','active')",
                                     (host,)):
                attempts = r["attempts"] + 1
                if attempts < MAX_ATTEMPTS:
                    self.frontier.finish(r["id"], state="pending", attempts=attempts, reason=kind, fetched_at=None,
                                         next_at=now + RETRY_AFTER_S * attempts + self.rng.uniform(0, RETRY_JITTER_S))
                else:
                    self.frontier.finish(r["id"], state="failed", attempts=attempts, reason=kind)
                charged += 1
        return lab, charged

    def _server_down(self, queue: str, n: int) -> None:
        """A server that did not connect n ≥ CONN_GIVE_UP times in a row (about two hours of pauses): a school host on
        it that never answered us off campus may be one only the school's network reaches (host_unreachable: the lab
        machine tries again, record 三·19); every other row keeps waiting with all its attempts (B2: no URL loses one
        while its server is down; second review 10-26). One warning when the streak reaches CONN_GIVE_UP (the
        dashboard shows it red); the streak goes on counting until the server answers."""
        lab = 0
        for host in self.frontier.hosts_of(queue):
            h = self.frontier.host(host)
            if schoollock.is_school_host(host) and not (h is not None and h["answered_off"]):
                lab += self.frontier.x("UPDATE url SET state='failed', reason='host_unreachable' WHERE host=? AND "
                                       "state IN ('pending','active')", (host,))
        if lab:
            self.frontier.event("warn", "host_unreachable", f"server queue {queue} did not connect {n} times in a row: "
                                f"{lab} URLs of school hosts that never answered off campus wait for the lab machine "
                                "(nknu archive retry host_unreachable)")
        if n == CONN_GIVE_UP:
            self.frontier.event("warn", "server_down", f"server queue {queue} did not connect {n} times in a row "
                                f"(about {n * CONN_PAUSE_S / 3600:.0f} h): its rows keep waiting, every one with all its "
                                "attempts (B2); a person should look whether it is down for good")

    # ---- robots.txt and the probe
    def _robots(self, queue: str, client: Client, row, admitted: str) -> Robots:
        """The host's robots.txt (design §6.8), read again after 24 h: never a block. Rules already known are never
        overwritten by a 5xx or by no answer (B3); a connection that cannot be made is the server's (Robots('conn')).
        Its requests say which row they were made for (admitted: the row's placement, _admitted_as), so that the scope
        audit finds robots.txt of a form's or a Drive file's host allowed (second review 10-26). When its scheme
        refuses, the other scheme's robots.txt is read instead; that teaches nothing of the host's scheme (a 404 or a
        redirect there is no proof that the pages answer on it: second review, an expired certificate whose http only
        sends the visitor back); the page's own try on the other scheme does (_switch_scheme)."""
        url = row["url"]
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        age = self.clock() - ((h["robots_at"] or 0) if h is not None else 0)
        if h is not None and h["robots_state"] in ("rules", "none") and age < FRESH_S:
            return Robots(h["robots_state"], h["robots_body"] or "")
        self._tl.ended = None
        answer, kind = self._read_robots(queue, client, host, f"{parts.scheme}://{parts.netloc}/robots.txt", admitted)
        if answer.state == "unreachable" and kind in SWITCH_KINDS and parts.port is None:
            # an http link to an https-only host (or the other way round) must not cost the host its robots.txt
            other = "https" if parts.scheme == "http" else "http"
            alt, alt_kind = self._read_robots(queue, client, host, f"{other}://{parts.netloc}/robots.txt", admitted)
            if alt.state != "unreachable" or alt_kind is None or alt_kind.startswith("http_"):
                answer, kind = alt, alt_kind
        if answer.state == "wait":
            return answer
        if answer.state == "unreachable" and (kind is None or kind.startswith("http_")):
            # a 5xx: an answer with no rules this time; what was known stays (B3), and it is asked again in an hour
            self.frontier.set_host(host, robots_state=(h["robots_state"] if h is not None and h["robots_state"]
                                                       in ("rules", "none") else "none"),
                                   robots_at=self.clock() - FRESH_S + RETRY_UNREACHABLE_S, robots_fail=0)
            self.frontier.event("warn", "robots_5xx", f"robots.txt answered {kind or 'a 5xx'}: read as no new rules; "
                                "what was known stays", host, url)
            h = self.frontier.host(host)
            return Robots(h["robots_state"], h["robots_body"] or "")
        if answer.state == "unreachable" and kind in CONN_KINDS:
            return Robots("conn")
        if answer.state == "unreachable" and h is not None and h["robots_state"] in ("rules", "none"):
            # keep the last good copy, try again in an hour
            self.frontier.set_host(host, robots_at=self.clock() - FRESH_S + RETRY_UNREACHABLE_S)
            return Robots(h["robots_state"], h["robots_body"] or "")
        before = {k: h[k] for k in ("robots_state", "robots_body", "robots_sha", "robots_at", "crawl_delay",
                                    "robots_fail", "cooldown_until", "not_before")} if h is not None else None
        if answer.state == "unreachable":
            fails = (h["robots_fail"] or 0) + 1 if h is not None else 1
            self.frontier.set_host(host, robots_at=self.clock(), robots_fail=fails)  # rules, body, delay: untouched
            mass = False
            if fails >= UNREACHABLE_TRIES:
                lab, charged = self._give_up_hosts([host], kind or "fetch")
                self.frontier.set_host(host, robots_fail=0 if not lab else fails)
                mass = bool(lab)
                self.frontier.event("warn", "host_unreachable", f"robots.txt unreachable {fails} times in a row: "
                                    f"{lab} URLs wait for a run from the lab machine (nknu archive retry "
                                    f"host_unreachable), {charged} lose an attempt (down for now)", host, url)
            else:
                self.frontier.event("warn", "robots_unreachable", "robots.txt unreachable: host waits an hour", host,
                                    url)
            if kind in NET_KINDS and before is not None:
                self.net.charge(queue, "host", host, before, mass)
            return Robots("unreachable")
        if answer.state == "rules":
            self.frontier.robots_version(answer.sha, host, answer.body, self.clock())
        delay, gap, ended = answer.crawl_delay(), getattr(self._tl, "gap", 0.0), getattr(self._tl, "ended", None)
        # the robots.txt just read rules the wait after it: the request after it is the host's next one
        not_before = ended + delay if delay and ended is not None and delay > gap else 0.0
        self.frontier.set_host(host, robots_state=answer.state, robots_body=answer.body[:MAX_BODY],
                               robots_sha=answer.sha, robots_at=self.clock(), crawl_delay=delay, robots_fail=0,
                               not_before=not_before)
        for sm in answer.sitemaps():  # design §5 ⑥: the sitemaps it names are read for their addresses
            self._enqueue(sm, from_row=row, depth=row["depth"], found_via="link", prio=PRIO_LIST, kind0="sitemap")
        return answer

    def _learn_scheme(self, host: str, scheme: str) -> None:
        h = self.frontier.host(host)
        if h is None or h["scheme_only"] != scheme:
            self.frontier.set_host(host, scheme_only=scheme)
            self.frontier.event("info", "scheme_switch", f"answers on {scheme} only: its addresses are written so",
                                host)

    def _unlearn_scheme(self, host: str, sent_to: str) -> None:
        """The host was taken to answer on one scheme only, and a page there sends the visitor to the other (sent_to):
        it is not so. Its addresses are written as they come again, and the row of sent_to, if it was moved, is
        fetched again (second review 10-26: else the two rows point at each other, and the content is never read)."""
        self.frontier.set_host(host, scheme_only=None)
        self.frontier.x("UPDATE url SET state='pending', reason=NULL, final_url=NULL, next_at=0 WHERE url=? "
                        "AND state='skipped' AND reason='moved_scheme'", (sent_to,))
        self.frontier.event("warn", "scheme_switch", f"a page sends the visitor to {urlsplit(sent_to).scheme}: the host "
                            "is no longer taken to answer on one scheme only", host, sent_to)

    def _read_robots(self, queue: str, client: Client, host: str, target: str,
                     admitted: str | None = None) -> tuple[Robots, str | None]:
        """One host's robots.txt, following redirects that stay on this queue: (answer, failure kind)."""
        for hop in range(ROBOTS_HOPS + 1):
            got = self._request(queue, client, Target(target), "robots", pass_statuses=ROBOTS_4XX,
                                scope_reason=admitted)
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
                return answer, (f"http_{got.status}" if got.status >= 500 else None)
            nxt = scope.canonical(got.headers.get("location", ""), target)
            why = None
            if hop >= ROBOTS_HOPS:
                why = f"more than {ROBOTS_HOPS} redirects"
            elif nxt is None:
                why = "a redirect without a usable Location"
            elif scope.judge(nxt).reason in scope.HARD_OUT:
                why = f"a redirect to {nxt} ({scope.judge(nxt).reason})"
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
        """Once per page host: what a page that does not exist looks like (status, digest, length, title). Returns
        None to go on with the page; the time the server queue rests until (Retry-After); or the failure, when the
        probe reached no server (a connection, a TLS handshake or a name that failed: the page would meet the same
        door, so it is not sent; the probe is tried again with a later page)."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        if h is not None and h["probe_status"] is not None:
            return None
        target = f"{parts.scheme}://{parts.netloc}/nknu-archive-probe-{secrets.token_hex(6)}.html"
        got = self._request(queue, client, Target(target), "probe", scope_reason=scope_reason, robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):
            return None  # nothing was sent; the page request meets the same breaker
        if isinstance(got, RetryLater):
            return self._rest_queue(queue, client, host, target, got)
        if isinstance(got, FetchError):
            if error_kind(got) in SWITCH_KINDS | {"dns"}:  # the server is not reachable at this door
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

    def _admitted_as(self, row) -> str:
        """What let a row in, for its requests' request.scope (B7; second review 10-26: it said admit:<kind0> for
        every row, so the audit could tell nothing): admit:<verdict>:<owner site>:<kind0>, verdict r1 (a page of its
        own round-1 site, or an extra page), resource (a part of a round-1 page, or a page of a site a seed moved to),
        or round2 (a round-two row `retry` brought back: its reason says why)."""
        kind0 = row["kind0"] or row["kind"]
        owner = row["owner_site"] or "-"
        if row["tier"] not in round1.TIER_OF_IMP.values():
            verdict = "round2"
        elif owner == scope.site_key(row["url"]) or self.plan.is_extra(row["url"]):
            verdict = "r1"
        else:
            verdict = "resource"
        return f"admit:{verdict}:{owner}:{kind0}"

    # ---- one row
    def _process(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        kind0 = row["kind0"] or row["kind"]
        # an API row is the Google step's own address (design §7.6): never judged again by scope (a listing's next
        # page carries its token and may pass 1,000 characters, which scope would read as too_long)
        api = google_api.is_api(row["url"])
        if kind0 != "login_check" and not api:  # the rules of today, for rows that entered under older ones
            verdict = scope.judge(row["url"])
            if verdict.reason in _HARD_SKIP and not (kind0 == "script" and verdict.reason == "media"):
                self.frontier.finish(row["id"], state="skipped", reason=f"out:{verdict.reason}", fetched_at=None)
                return
        if self.disk_free(self.root) < MIN_FREE_BYTES:
            self.frontier.event("error", "disk_low", f"less than {MIN_FREE_BYTES // 1024**3} GB free under "
                                f"{self.root}: the run stops before the archive cannot be written", row["host"],
                                row["url"])
            raise _DiskLow
        row = self._move_scheme(row)
        if row is None:
            return
        url, host, uid = row["url"], row["host"], row["id"]
        admitted = self._admitted_as(row)
        robots = self._robots(queue, client, row, admitted)
        if robots.state == "wait":
            self.frontier.finish(uid, state="pending", reason="robots_wait", fetched_at=None)
            return
        if robots.state == "conn":
            self._conn_failed(queue, client, row, url, None)
            return
        if robots.state == "unreachable":
            if self.frontier.row_by_id(uid)["state"] == "active":  # else it was given up with its host
                self.frontier.set_host(host, cooldown_until=self.clock() + RETRY_UNREACHABLE_S)
                self.frontier.finish(uid, state="pending", reason="robots_unreachable", fetched_at=None)
            return
        row = self._move_scheme(row)  # robots.txt may have shown the host answers on one scheme only
        if row is None:
            return
        url, host, uid = row["url"], row["host"], row["id"]
        if self._not_due(host):  # the Crawl-delay after robots.txt: the row comes back when the host is due
            self.frontier.finish(uid, state="pending", fetched_at=None)
            return
        if kind0 in ("page", "login_check"):  # only a page host's missing page says something (design §6.8)
            probed = self._probe(queue, client, url, robots, admitted)
            if isinstance(probed, float):  # the server asked to be left alone: no attempt is used
                self.frontier.finish(uid, state="pending", reason="retry_after", next_at=probed, fetched_at=None)
                return
            if isinstance(probed, FetchError):
                kind = error_kind(probed)
                if kind in SWITCH_KINDS:  # the page would meet the same door: its other scheme first (§6.6)
                    self._door_failed(queue, client, sink, row, probed, kind)
                else:
                    self._failed(queue, row, probed)
                return
            if self._not_due(host):  # the Crawl-delay after the probe
                self.frontier.finish(uid, state="pending", fetched_at=None)
                return
        if kind0 == "page":  # a platform's detail page: its host's id probe once per detail type (design §10.1)
            waited = self._id_probe(queue, client, row, robots, admitted)
            if waited is not None:
                self.frontier.finish(uid, state="pending", reason="retry_after", next_at=waited, fetched_at=None)
                return
            if self._not_due(host):
                self.frontier.finish(uid, state="pending", fetched_at=None)
                return
        if kind0 == "image" and self._decor_repeat(row):
            self.frontier.finish(uid, state="skipped", reason="decor:repeat", fetched_at=None)
            return
        target = google_api.target(self.frontier, url) if api else Target(url)
        got = self._request(queue, client, target, "page", scope_reason=admitted, robots_sha=robots.sha,
                            max_bytes=google_api.max_bytes(url) if api else MAX_BYTES)
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
            size = re.search(r"(\d+) bytes declared", str(got))
            self.frontier.finish(uid, state="skipped", reason="media_type" if isinstance(got, UnwantedType)
                                 else "too_large", size_declared=int(size[1]) if size else None)
            return
        if isinstance(got, DecodeError):
            self.frontier.finish(uid, state="failed", reason="decode")
            return
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind in SWITCH_KINDS:
                self._door_failed(queue, client, sink, row, got, kind)
                return
            self._failed(queue, row, got)
            return
        if api and got.status >= 400 and self._api_error(queue, row, got, sink):
            return
        self._answered(host, url, got.status)
        self._store(queue, row, got, sink)
        if api and got.status == 200 and kind0 in ("drive", "drive_list", "calendar"):
            stored = self.frontier.row_by_id(uid)
            if stored is not None and stored["state"] == "done":  # what the answer holds (google_api.read)
                google_api.read(self.frontier, stored, got, self._google_add)

    # ---- Google's APIs (design §7.6)
    def _api_error(self, queue: str, row, got: Snapshot, sink) -> bool:
        """A 4xx of Google's API, sorted by its reason (google_api.classify, design §7.6's table): kept as answered
        (api_error, login, too_large: _store, then the columns it says); a quota or rate answer rests the queue and
        retries the row as any GET; a key problem rests the queue for the run and puts the row back, no attempt used
        (the next run's hold_for_key lifts the rest). Never a refusal streak of a directory (_answered: ten 403s of
        quota would pause /drive/v3/files whole). Whether it handled the answer."""
        err = google_api.classify(got.status, got.body, got.headers)
        if err is None:
            return False
        now = self.clock()
        if err.action == "retry":
            self.frontier.pause(queue, now + (err.pause_s if err.pause_s is not None else google_api.RATE_PAUSE_S),
                                f"{row['host']}: {err.url_reason}")
            self._failed(queue, row, kind=err.url_reason)
            return True
        if err.action == "key":
            # the reason starts with api_key, which hold_for_key() reads as a key's rest when the next run starts
            self.frontier.pause(queue, now + (err.pause_s or google_api.KEY_PAUSE_S), f"api_key: {err.reason}")
            self.frontier.event("warn", "key_check", f"Google 不接受金钥（{err.reason}）：googleapis 这条队这次 run "
                                "暂停，下次 nknu archive run 开跑时再试；这一行放回待抓，不算失败", row["host"],
                                row["url"])
            self.frontier.finish(row["id"], state="pending", reason="api_key", fetched_at=None)
            return True
        self._store(queue, row, got, sink)
        cols = google_api.error_cols(err)
        if cols:
            if "flags" in cols:
                old = self.frontier.row_by_id(row["id"])["flags"]
                cols["flags"] = ",".join(dict.fromkeys(filter(None, [*(old or "").split(","), cols["flags"]])))
            self.frontier.finish(row["id"], **cols)
        return True

    def _google_add(self, url: str, placement: round1.Placement, from_row, found_via: str) -> tuple[int | None, bool]:
        """google_api.read()'s way to make a row (google_api.Add): a content row, a listing's next page, a child."""
        e = self._enqueue(url, placement=placement, url=url, from_row=from_row, found_via=found_via,
                          found_from=from_row["id"], depth=from_row["depth"])
        return e.id, e.verdict not in ("dup", "scheme_switch")

    def _google(self, adm: round1.Admission, *, from_row, how: str, depth: int, found_via: str,
                found_from: int | None = None, zone: str | None = None,
                text: str | None = None) -> tuple[Enqueued, list[dict]]:
        """An address the Google step took (design §7.6): every row it makes and the page_res entries of the page that
        uses it (google_api.uses: an embed showing three calendars makes three; attach: a folder read already brings
        all it holds). Returns the Enqueued of its first row (else of its first use) for the link table, and the
        entries."""
        first: Enqueued | None = None
        entries: list[dict] = []
        for u in google_api.uses(self.plan, adm.link, from_row, how):
            e = self._enqueue(u.url or u.link, placement=u.placement, url=u.url or u.link, from_row=from_row,
                              depth=depth, found_via=found_via, found_from=found_from)
            if first is None or (first.id is None and e.id is not None):
                first = e
            entries += google_api.attach(self.frontier, u, e.id if u.url is not None else None, zone=zone, text=text)
        return first or Enqueued(adm.placement.reason or "out", None, None, adm.placement), entries

    def _google_users(self, adm: round1.Admission, from_row, via, how: str, found_via: str,
                      page_ids: list[int]) -> Enqueued:
        """A resource row `via` (a short link, an attachment, a frame) that leads to a Drive, Docs or Calendar address:
        the pages that use it get the address's entries as if they had linked it (design §7.7), each brought up to
        date. from_row: the round-1 row its placement is by (the page, or the resource row, which has its owner and
        tier)."""
        e, entries = self._google(adm, from_row=from_row, how=how, depth=via["depth"], found_via=found_via,
                                  found_from=via["id"])
        for pid in page_ids:
            self.frontier.add_page_res(pid, entries)
            google_api.resync(self.frontier, pid)
        return e

    # ---- the id probe (design §10.1)
    def _id_probe(self, queue: str, client: Client, row, robots: Robots, admitted: str) -> float | None:
        """Once per platform host and detail type, before its first detail page: the address of an item that cannot
        exist (profiles.id_probe_url: SSO's UNID of 40 zeros, c.nknu's nid=999999999), its answer kept in id_probe
        (with whether it is an empty detail). A page is gone_id only when the probe answered 200 with an empty detail
        and the page is itself one (_page). Returns the time the server queue rests until (Retry-After), else None. A
        probe that got no answer is asked again with a later page (one that may have reached the server, a timeout or
        a broken answer, at most PROBE_TRIES times a run); any answer, an error too, is kept."""
        probe_url = profiles.id_probe_url(row["url"])
        if probe_url is None:
            return None
        host, ptype = row["host"], pagetypes.type_key(row["url"], row["site"])
        if self.frontier.q("SELECT 1 FROM id_probe WHERE host=? AND page_type=?", (host, ptype)):
            return None
        if self._probe_tries.get((host, ptype), 0) >= PROBE_TRIES:
            return None  # it timed out (or broke off) that often this run: the next run asks again
        got = self._request(queue, client, Target(probe_url), "id_probe", scope_reason=admitted,
                            robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):
            return None
        if isinstance(got, RetryLater):
            return self._rest_queue(queue, client, host, probe_url, got)
        cols: dict[str, Any] = {"url": probe_url, "at": self.clock()}
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind in TRANSPORT_KINDS or kind.startswith("net_"):
                if kind not in PROBE_UNSENT:  # a timeout or a broken answer may have reached the server (review
                    key = (host, ptype)       # 10-26 phase 3): not asked again before every detail page of this run
                    self._probe_tries[key] = self._probe_tries.get(key, 0) + 1
                return None
            cols["status"] = int(kind[5:]) if kind.startswith("http_") and kind[5:].isdigit() else 0
        else:
            text = None
            if got.status == 200 and sniff.sniff(got.body, got.headers.get("content-type", "")) == "html":
                text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            empty = profiles.empty_detail(probe_url, text) if text is not None else None
            cols.update(status=got.status, sha256=got.sha256, len=len(got.body),
                        final_url=scope.canonical(got.headers.get("location", ""), probe_url)
                        if got.status in REDIRECTS else None,
                        title=extract(text, probe_url).title if text else None,
                        empty=None if empty is None else int(empty))
        names = ["host", "page_type", *cols]
        self.frontier.x(f"INSERT OR REPLACE INTO id_probe({', '.join(names)}) VALUES ({','.join('?' * len(names))})",
                        (host, ptype, *cols.values()))
        return None

    def _door_failed(self, queue: str, client: Client, sink: WarcSink, row, exc: FetchError, kind: str) -> None:
        """The row's address refused a connection or a TLS handshake (its probe's, or its own): its other scheme once
        (design §6.6). Moved there: done. The other scheme answered without letting the page in (it sends the visitor
        back, or errs): the server is up and the address cannot be reached, the URL's failure (an attempt, retried
        later: an expired certificate ends as tls_cert, 连接失败). Neither answered: a TLS failure is the URL's, a
        connection the server's (_conn_failed)."""
        switched = self._switch_scheme(queue, client, sink, row)
        if switched in ("moved", "handled"):
            return
        if switched == "up" or kind in TLS_KINDS:
            self._failed(queue, row, exc, kind=kind)
            return
        self._conn_failed(queue, client, row, row["url"], exc)

    def _back(self, uid: int, reason: str) -> None:
        """The row goes back to pending, no attempt used, unless its server was given up meanwhile (_give_up_hosts)."""
        self.frontier.x("UPDATE url SET state='pending', reason=? WHERE id=? AND state='active'", (reason, uid))

    def _move_scheme(self, row):
        """A row on a host that answers on the other scheme only goes there (design §6.6): the row of the address in
        that scheme (made from this one when there is none, found_via scheme_switch) is fetched instead, active, and
        is the row the worker handles from now on (self._tl.row); this one is skipped (moved_scheme) with final_url
        pointing there. None when the other row was fetched already."""
        h = self.frontier.host(row["host"])
        other = _other_scheme(row["url"])
        if h is None or not h["scheme_only"] or other is None or not other.startswith(h["scheme_only"] + "://"):
            return row
        moved = self._moved_row(row, other)
        self.frontier.finish(row["id"], state="skipped", reason="moved_scheme", final_url=other)
        if moved is None or moved["state"] not in ("pending", "active"):
            return None
        self.frontier.x("UPDATE url SET state='active' WHERE id=?", (moved["id"],))
        self._tl.row = moved["id"]
        return self.frontier.row_by_id(moved["id"])

    def _moved_row(self, row, other: str):
        existing = self.frontier.row(other)
        if existing is not None:
            return existing
        self.frontier.add(other, host=row["host"], site=scope.site_key(other), unit=row["unit"], tier=row["tier"],
                          kind=row["kind"], depth=row["depth"], found_via="scheme_switch", found_from=row["id"],
                          owner_site=row["owner_site"], prio=row["prio"], leaf=row["leaf"], kind0=row["kind0"],
                          added_at=self.clock(), old_ref=row["old_ref"])
        moved = self.frontier.row(other)
        self.frontier.x("UPDATE page_res SET res_id=? WHERE res_id=?", (moved["id"], row["id"]))
        return moved

    def _switch_scheme(self, queue: str, client: Client, sink: WarcSink, row) -> str | None:
        """The row's scheme refused a connection or a TLS handshake: once, on the same queue after the gap, the other
        scheme (design §6.6). 'moved': it answered there, the row moved and the answer is stored on the other row; the
        host is taken to answer on that scheme only when the answer is a page or a redirect elsewhere (2xx, 3xx), never
        a 4xx or 5xx, which says nothing of its other pages (second review 10-26). 'up': it answered, but only to send
        the visitor back to the scheme that failed, or with an error: the server is there, the address cannot be
        reached (the caller's: the URL's failure, never a loop). 'handled': it asked us to come back later (the row
        waits, no attempt used). None: that scheme did not answer either, or there is none to try."""
        other = _other_scheme(row["url"])
        if other is None or row["found_via"] == "scheme_switch":
            return None
        got = self._request(queue, client, Target(other), "page", scope_reason=self._admitted_as(row))
        if isinstance(got, HostCircuitOpen):
            return None  # nothing was sent
        if isinstance(got, RetryLater):
            until = self._rest_queue(queue, client, row["host"], other, got)
            self.frontier.finish(row["id"], state="pending", reason="retry_after", next_at=until, fetched_at=None)
            return "handled"
        if isinstance(got, FetchError):
            return None if error_kind(got) in SWITCH_KINDS | {"dns"} else "up"
        if got.status in REDIRECTS and _sends_back(row["url"], other, got.headers.get("location")):
            return "up"
        moved = self._moved_row(row, other)
        self.frontier.finish(row["id"], state="skipped", reason="moved_scheme", final_url=other)
        if 200 <= got.status < 400:
            self._learn_scheme(row["host"], urlsplit(other).scheme)
        self._answered(row["host"], other, got.status)
        if moved["state"] in ("pending", "active"):
            self.frontier.x("UPDATE url SET state='active' WHERE id=?", (moved["id"],))
            self._tl.row = moved["id"]
            self._store(queue, self.frontier.row_by_id(moved["id"]), got, sink)
        return "moved"

    def _decor_repeat(self, row) -> bool:
        """A layout image (design §7.2): on 5 or more of its site's stored pages and 30% of them, never in a body."""
        got = self.frontier.q("SELECT count(DISTINCT page_id), coalesce(max(zone = 'body'), 0) FROM page_res "
                              "WHERE res_id=? AND role='img'", (row["id"],))[0]
        site_pages = self.frontier.q("SELECT count(*) FROM url WHERE owner_site=? AND kind='page' AND state='done'",
                                     (row["owner_site"],))[0][0]
        return links.decor_repeat(row["url"], got[0], site_pages, bool(got[1]))

    def _failed(self, queue: str, row, exc: FetchError | None = None, kind: str | None = None) -> None:
        """A request for this row reached no answer (or an error page): tried again later (30 min × attempts), three
        attempts in all."""
        uid, kind = row["id"], kind or error_kind(exc)
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
        if status in DENY_STATUSES and host != google_api.API_HOST:  # an API's 403 is its reason's (_api_error)
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

    # ---- B4: where the requests are made from
    def campus_queue(self) -> str | None:
        if not self.campus_url:
            return None
        return self.queue_of(urlsplit(self.campus_url).hostname or "")

    def _campus_system(self) -> str:
        """The host of the system the check page's cURL names (edocap.nknu.edu.tw), where it sends a visitor on
        campus."""
        for k, v in parse_qsl(urlsplit(self.campus_url or "").query):
            if k.lower() == "curl":
                return (urlsplit(v if "://" in v else f"https://{v}").hostname or "").lower()
        return ""

    def _campus_due(self) -> bool:
        """Whether the campus check is due: hourly, and at once when this machine's address changed, from which moment
        the requests say campus unknown until the check has been made (second review 10-26: they said what the last
        check found, on or off, for as long as SSO's queue was busy or paused)."""
        if not self.campus_url:
            return False
        if self.local_address() != self._campus_addr:
            if self._campus_addr is not None and self.campus != "unknown":
                self.campus = "unknown"
                self.frontier.event("info", "campus", "这台机器的地址变了：校内还是校外，等 SSO 的判断页再查一次之前记 unknown",
                                    None, self.campus_url)
            return True
        return self.clock() - self._campus_at >= CAMPUS_EVERY_S

    def _campus_step(self, queue: str, client: Client, sink: WarcSink) -> bool:
        """On the campus check page's own queue (SSO's), when due: one GET of SSO's check page. Off campus it says so
        (「您的IP 不在校內網路」); on campus it sends the visitor on (never followed). Every request row after it says
        where it was made from. Crawling goes on either way (the owner 10-26 「听你的」: 照抓、记下来、看板提醒)."""
        with self._campus_lock:
            if queue != self.campus_queue() or not self._campus_due():
                return False
            self._campus_addr, self._campus_at = self.local_address(), self.clock()
        got = self._request(queue, client, Target(self.campus_url), "campus", scope_reason="campus")
        if isinstance(got, Snapshot) and got.status in REDIRECTS:
            # on campus SSO sends the visitor on to the system cURL names; any other redirect (its error page, https)
            # says nothing of where we are (second review 10-26)
            to = scope.canonical(got.headers.get("location", ""), self.campus_url)
            state = "on" if to is not None and urlsplit(to).hostname == self._campus_system() else "unknown"
            if state == "unknown":
                self.frontier.event("warn", "campus", f"SSO 的校内判断页转去 {to or '（没有 Location）'}，不是 cURL 指的 "
                                    f"{self._campus_system()}：判断不出在校内还是校外", None, self.campus_url)
        elif isinstance(got, Snapshot) and got.status == 200:
            text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            state = "off" if _CAMPUS_OFF.search(text or "") else "unknown"
            if state == "unknown":
                self.frontier.event("warn", "campus", "SSO 的校内判断页样子变了，判断不出在校内还是校外", None,
                                    self.campus_url)
        else:
            state = "unknown"
        if state != self.campus:
            if state == "on":
                self.frontier.event("warn", "campus", "这台机器在校内网络：有些页面校内看得到全文、校外只看到标题；照抓，"
                                    "每个请求都记下在校内抓的", None, self.campus_url)
            else:
                self.frontier.event("info", "campus", f"campus: {state} (这台机器{'在校外网络' if state == 'off' else '在哪里判断不出'})",
                                    None, self.campus_url)
        self.campus = state
        return True

    # ---- what came back
    def _store(self, queue: str, row, snap: Snapshot, sink) -> None:
        uid, ctype = row["id"], snap.headers.get("content-type", "").lower()
        kind0 = row["kind0"] or row["kind"]
        cols: dict[str, Any] = {"state": "done", "reason": None, "status": snap.status, "final_url": snap.url,
                                "ctype": ctype[:100], "bytes": len(snap.body), "sha256": snap.sha256,
                                "warc": self._warc_path(sink), "warc_offset": snap.warc_offset,
                                "warc_len": snap.warc_len}
        if kind0 == outcome.SHORTLINK:
            self._shortlink(row, snap, cols)
            return
        if snap.status in REDIRECTS:
            self._redirect(queue, row, snap, cols)
            return
        if snap.status != 200:
            if snap.status >= 400:
                cols["reason"] = f"http_{snap.status}"
            self.frontier.finish(uid, **cols)
            return
        sniffed = sniff.sniff(snap.body, ctype)
        cols.update(sniff=sniffed, filename=sniff.filename(snap.raw_headers, snap.headers))
        flags: list[str] = []
        sig = outcome.error_signature(row["url"])
        if sig is not None and sig.result == outcome.GONE and sig.flag:
            flags.append(sig.flag)  # SSO's not-found page fetched directly (design §10.1)
        text = None
        if sniffed in ("html", "xml", "json", "text"):
            text = snap.text if snap.text is not None else _lenient(snap.body, ctype)
            if snap.target.meta.get("decode_error"):
                flags.append("decode")
        if kind0 in ("form", "embed") and sniffed == "html":
            cols["kind"] = "embed"  # the page's part, never a round-1 page itself (design §4.4, §7.6)
            if kind0 == "form" and ("closedform" in row["url"] or _FORM_CLOSED.search(text or "")):
                flags.append("form_closed")
            cols["flags"] = ",".join(flags) or None
            self.frontier.finish(uid, **cols)
            # what an embed box holds, and what a form's description holds (B12), count on the pages that use it
            self._part_answer(row, text or "", cols["warc"], snap.warc_offset, self._users_of(row))
            return
        if kind0 in outcome._EXPECTED_FILE:
            cols["kind"] = _KIND_OF0.get(kind0, "file") if sniffed == "html" else sniff.kind_of(sniffed)
            if sniffed == "html" and "<input" in (text or "").lower() and \
                    re.search(r"(?i)<input[^>]+type\s*=\s*['\"]?password", text or ""):
                flags.append("login")  # an attachment that sends a sign-in page (not_file otherwise: never followed)
            said = profiles.school_says(row["url"], text) if sniffed == "html" else None
            if said is not None:  # the school's own page says the file is missing, or its link broken (§10.2, B6)
                step = profiles.says_step(said, row["reason"], row["attempts"], self.clock())
                if "flags" in step:
                    flags.append(step.pop("flags"))
                cols.update(step)
                if cols["state"] == "pending":
                    cols["fetched_at"] = None  # looked at once more GONE_RECHECK_S later: the same twice is gone
            if kind0 == "image" and cols["kind"] == "file":  # a scan sent as a picture: the page's attachment
                self.frontier.x("UPDATE page_res SET role='file' WHERE res_id=? AND role='img'", (uid,))
            if kind0 == "sitemap" and sniffed != "html":
                self._sitemap(row, snap)
            content = None
            if kind0 == "click" and sniffed != "html":  # a tab pressed by its platform rule (§7.4): oia's JSON
                content = profiles.tab_content(text)
                if content is None:
                    flags.append("click_empty")  # success is not true, or the content is empty: 要人看 (thin)
            cols["flags"] = ",".join(dict.fromkeys(flags)) or None
            self.frontier.finish(uid, **cols)
            if content is not None:  # the tab's content is part of the pages that use it
                self._part_answer(row, content, cols["warc"], snap.warc_offset, self._users_of(row))
            return
        if sniffed != "html":  # a page address that answers a PDF, a picture, a JSON: a file (design §7.3)
            cols.update(kind=sniff.kind_of(sniffed), flags=",".join(flags) or None)
            self.frontier.finish(uid, **cols)
            return
        self._page(row, snap, text or "", cols, flags)

    def _shortlink(self, row, snap: Snapshot, cols: dict) -> None:
        """A short link's one GET (design §7.7, round1.short_answer): its target, read from a 3xx's Location or the
        service's 200 page, is admitted as if the page that names the short link had linked it directly."""
        text = None
        if snap.status == 200:
            cols["sniff"] = sniff.sniff(snap.body, cols.get("ctype") or "")
            if cols["sniff"] == "html":
                text = snap.text if snap.text is not None else _lenient(snap.body, cols.get("ctype") or "")
        answer = round1.short_answer(row["url"], snap.status, snap.headers.get("location"), text)
        cols["kind"] = outcome.SHORTLINK
        if snap.status >= 400 and snap.status not in (404, 410):
            cols["reason"] = f"http_{snap.status}"
        target = scope.canonical(answer.target) if answer.target else None
        if target is not None:
            page = self.frontier.row_by_id(row["found_from"])
            how = self._use_of(row)
            adm = self.admit(target, None, page, how)
            if adm is not None and adm.step == "google" and self.google is not None and page is not None:
                # a short link to Drive, Docs or a calendar: the pages that name it use what it leads to (§7.7)
                e = self._google_users(adm, page, row, how, "redirect", self._users_of(row))
            elif adm is not None:
                e = self._enqueue(target, placement=adm.placement, url=adm.url, from_row=page, depth=row["depth"],
                                  found_via="redirect", found_from=row["id"])
            else:
                e = Enqueued("bad_url", None, None, None)
            self.frontier.link(row["id"], target, "[shortlink]", e.verdict)
            cols["final_url"] = e.url or target
        self.frontier.finish(row["id"], **cols)

    def _use_of(self, row) -> str:
        """How the page uses this row (its page_res role): a frame, an image or a link."""
        got = self.frontier.q("SELECT role FROM page_res WHERE res_id=? LIMIT 1", (row["id"],))
        role = got[0][0] if got else None
        return {"iframe": "frame", "frame": "frame", "img": "image"}.get(role, "link")

    def _redirect(self, queue: str, row, snap: Snapshot, cols: dict) -> None:
        uid = row["id"]
        target = scope.canonical(snap.headers.get("location", ""), row["url"])
        if target is None:
            self.frontier.finish(uid, **cols)
            return
        sig = outcome.error_signature(target)
        if sig is not None:  # a school error page (design §10.1): never queued, it holds nothing of the school's
            self.frontier.link(uid, target, "[redirect]", outcome.ERROR_LINK)
            if sig.result == outcome.GONE:
                cols.update(flags=sig.flag, final_url=target)
                self.frontier.finish(uid, **cols)
            else:  # the server failed: retried as any GET, then server_error (the old archive's copy stands)
                self._failed(queue, row, kind=sig.reason or outcome.ERROR_LINK)
                self.frontier.x("UPDATE url SET status=?, final_url=? WHERE id=?", (snap.status, target, uid))
            return
        if target != row["url"] and self._scheme_rewrite(target) == row["url"]:
            self._unlearn_scheme(row["host"], target)  # it sends the visitor to the scheme it was moved away from
        google: list[dict] = []
        e = self._follow(row, target, "redirect", google)
        self.frontier.link(uid, target, "[redirect]", e.verdict)
        if e.verdict == "out:login" or urlsplit(target).hostname == "accounts.google.com":
            cols["flags"] = "login"  # the page sends visitors to a sign-in
        cols["final_url"] = e.url or target
        if google:  # a page sent to Drive, Docs or a calendar: what it leads to is all this reading of it uses
            self.frontier.set_page_res(uid, google)
            google_api.resync(self.frontier, uid)
        self.frontier.finish(uid, **cols)

    def _follow(self, row, target: str, found_via: str, entries: list[dict] | None = None) -> Enqueued:
        """Where a row sends the browser (a 3xx, a script or a refresh: design §8.3), no depth added. A resource's
        redirect stays that page's resource (Heritrix's TransclusionDecideRule), unless a rule leaves the address out;
        a page's is that page's link; a seed's redirect to another site is the same important site (it moved).

        A page sent to a Drive, Docs or Calendar address uses what it leads to as if it linked it (the Google step's
        rows, and their page_res entries added to `entries` for the caller to write on the page): its completeness is
        the item's, every file of a folder included, never the metadata row's 200 (review 10-26 phase 3, 【Claude 的执行
        办法】)."""
        kind0 = row["kind0"] or row["kind"]
        if kind0 in _TRANSCLUDE:
            how = {"image": "image", "embed": "frame"}.get(kind0, "link")
            adm = self.admit(target, None, row, how)
            if adm is not None and adm.step == "google" and self.google is not None:
                # an attachment, a frame or a picture that leads to Drive, Docs or a calendar: the pages that use the
                # row use what it leads to (its rows and their entries, design §7.6)
                return self._google_users(adm, row, row, how, found_via, self._users_of(row))
            if adm is not None and adm.step in ("shortlink", "google"):
                # the one way in (B7; second review 10-26): a short link is asked where it leads, a Google address
                # waits for the Google step, both as the page's resource (the resource row stands for the page)
                return self._enqueue(target, placement=adm.placement, url=adm.url, depth=row["depth"],
                                     found_via=found_via, found_from=row["id"])
            return self._enqueue(target, placement=self._transclude(target, row, kind0), url=target,
                                 depth=row["depth"], found_via=found_via, found_from=row["id"], kind0=kind0)
        if (row["depth"] == 0 and row["found_via"] in _SEED_VIAS and row["owner_site"]
                and scope.site_key(target) != scope.site_key(row["url"])
                and not self.plan.adopt(target, row["owner_site"], row["url"])):
            self.frontier.event("info", "seed_moved", f"a seed of {row['owner_site']} redirects to {target}, which "
                                "is not taken as the same site: judged as any link", row["host"], row["url"])
        adm = self.admit(target, None, row, "link")
        if adm is None:
            return Enqueued("bad_url", None, None, None)
        if adm.step == "google" and self.google is not None:
            e, got = self._google(adm, from_row=row, how="link", depth=row["depth"], found_via=found_via,
                                  found_from=row["id"])
            if entries is not None:
                entries += got
            return e
        return self._enqueue(target, placement=adm.placement, url=adm.url, from_row=row, depth=row["depth"],
                             found_via=found_via)

    def _transclude(self, target: str, row, kind0: str) -> round1.Placement:
        """Where a resource's redirect goes that admit() places by the rules (no short link, no Google address): the
        same resource of the same page, unless a rule leaves the address out."""
        v = scope.judge(target)
        if not v.ok and v.reason not in scope.SOFT_OUT and not (kind0 == "script" and v.reason == "media"):
            return round1.Placement("out", f"out:{v.reason}", None, None, 0, kind0)
        return round1.Placement("resource", None, row["owner_site"], row["tier"], 1, kind0)

    def _page(self, row, snap: Snapshot, text: str, cols: dict, flags: list[str]) -> None:
        """A web page of this round: its parts (page_res, _parts), its page type and the signs it reads (design §8.3:
        pagetypes.measure and observe, before finish), its platform's presses and walk (postback.page_stored, after
        finish), and what its embed boxes, forms, tabs and pressed details brought before (_replay_parts)."""
        uid, kind0 = row["id"], row["kind0"] or row["kind"]
        css = profiles.content_css(row["url"])  # the platform file's content area (design §7.2, §7.4)
        page = extract(text, snap.url, css)
        # a GET-walked list's pages its platform file's pager box names (WebIdx: sort=date, review 10-26 phase 3)
        page.pagers.extend(u for u in postback.profile_pagers(row["owner_site"] or row["site"], snap.url, text,
                                                              page.base) if u not in page.pagers)
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
        if kind0 == "login_check" and "login" not in flags and _LOGIN_WORDS.search(text):
            flags.append("login")  # design §4.2: 請登入, 無權限, 請連線 VPN
        if profiles.detail_of(row["url"]) is not None and profiles.empty_detail(row["url"], text):
            flags.append("empty_detail")  # design §10.1 nonempty: no body text, attachment, picture or related link
            probe = self.frontier.q("SELECT status, empty FROM id_probe WHERE host=? AND page_type=?",
                                    (row["host"], pagetypes.type_key(row["url"], row["site"])))
            if probe and profiles.gone_id(probe[0]["status"], bool(probe[0]["empty"]), True, True):
                flags.append("gone_id")  # the three at once: the probe's 200 empty detail, and this one empty too
        cols.update(kind="page", title=page.title, n_postbacks=len(page.buttons), text_chars=page.text_chars)
        redirect = page.js_redirect
        google: list[dict] = []  # what a script or a refresh sends the page to on Drive, Docs or a calendar
        if redirect is not None and (target := scope.canonical(redirect.href, page.base)) and target != row["url"]:
            e = self._follow(row, target, "js_redirect", google)
            self.frontier.link(uid, target, "[js_redirect]" if redirect.how == "script" else "[refresh]", e.verdict)
            flags.append("js_redirect")
            cols["final_url"] = e.url or target
            if e.verdict == "out:login" or urlsplit(target).hostname == "accounts.google.com":
                flags.append("login")
        cols["flags"] = ",".join(dict.fromkeys(flags)) or None
        if "login" in flags or "soft404" in flags:  # archived as it is; not a place to look for more pages
            cols["n_links"] = 0
            self.frontier.set_page_res(uid, [])
            self.frontier.finish(uid, **cols)
            return
        if page.pagers:
            cols["page_role"] = "list"  # a list by its GET paging (design §7.5 generic); its walk is listwalk's
        entries, n = self._parts(row, page, uid, html=text, warc=cols.get("warc"), warc_offset=snap.warc_offset)
        keys = {e["res_key"] for e in entries}
        self.frontier.set_page_res(uid, entries + [g for g in google if g["res_key"] not in keys])
        google_api.resync(self.frontier, uid)  # a folder or calendar read meanwhile brings what it holds
        # design §8.3: the page's type and its signs, after its page_res (a click it cannot press is read from it)
        # and before finish (observe reads the row's js_hint of its last fetch)
        m = pagetypes.measure(text, content_css=css, text_chars=page.text_chars)
        seen = pagetypes.observe(self.frontier, url_id=uid, url=row["url"], m=m, site=row["site"])
        flags += seen.flags
        cols["flags"] = ",".join(dict.fromkeys(flags)) or None
        cols["n_links"] = n
        self.frontier.finish(uid, **cols)
        # design §7.5: the platform's presses of this page as archived (actions), and how its list is walked
        stored = self.chains.page_stored(self.frontier.row_by_id(uid), text, cols.get("warc"), snap.warc_offset, page)
        if stored.page_role == "list" and cols.get("page_role") != "list":
            self.frontier.x("UPDATE url SET page_role='list' WHERE id=?", (uid,))
        if stored.unplanned:  # buttons the platform file says to press that no press of this page covers: 要人看
            self.frontier.add_page_res(uid, postback.unpressed_entries(stored.unplanned))
        self._replay_parts(uid)

    # ---- what a page's parts brought (design §4.4, §7.4, §7.5, §7.6)
    def _users_of(self, row) -> list[int]:
        """The pages that use a resource row (their page_res points at it), or the page it was found on. A row found
        from another resource (a redirect's target, what a short link leads to) is used by that resource's pages:
        page_res is a page's, never written on an attachment's or a short link's row (review 10-26 phase 3)."""
        cur, seen = row, {row["id"]}
        for _ in range(outcome.MAX_HOPS):
            ids = [r[0] for r in self.frontier.q("SELECT DISTINCT page_id FROM page_res WHERE res_id=? ORDER BY "
                                                 "page_id", (cur["id"],))]
            if ids:
                return [i for i in ids if i != row["id"]]
            up = cur["found_from"]
            if up is None or up in seen:
                return []
            seen.add(up)
            parent = self.frontier.row_by_id(up)
            if parent is None or parent["kind"] == "page":
                return [up]
            cur = parent
        return []

    def _part_answer(self, row, text: str, warc: str | None, warc_offset: int | None, page_ids: list[int]) -> None:
        """What a page's part brought counts on the pages that use it, their parts and their words (design §4.4): a
        Google Sites embed box (kind0 embed), a form's description (form: google_api.form_parts, B12: its attachments,
        Drive files and other forms, never its ordinary links; its words are the form's, not the page's) and a tab's
        content its platform rule pressed for (click: oia's load_content.php, read as part of the page, relative
        addresses by the page's own)."""
        kind0 = row["kind0"] or row["kind"]
        for pid in page_ids:
            parent = self.frontier.row_by_id(pid)
            if parent is None:
                continue
            if kind0 == "form":
                page = google_api.form_parts(text, row["url"])
            else:
                page = extract(text, parent["url"] if kind0 == "click" else row["url"])
            entries, _n = self._parts(parent, page, row["id"], html=text, warc=warc, warc_offset=warc_offset)
            self.frontier.add_page_res(pid, entries)
            google_api.resync(self.frontier, pid)
            if kind0 in ("embed", "click"):
                self.frontier.x("UPDATE url SET text_chars = coalesce(text_chars, 0) + ? WHERE id=?",
                                (page.text_chars, pid))

    def _replay_parts(self, page_id: int) -> None:
        """A page fetched again writes its page_res anew; what its parts brought when they were fetched (an embed box,
        a form, a tab, a file button's address, a detail pressed by POST: none of them is fetched or pressed again)
        is read back from the archive, so that it is never lost from the page unseen. Sends nothing."""
        page = self.frontier.row_by_id(page_id)
        if page is None:
            return
        for r in self.frontier.q(
                "SELECT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND u.state='done' "
                "AND u.status=200 AND u.kind0 IN ('embed', 'form', 'click') AND u.warc IS NOT NULL "
                "AND u.warc_offset IS NOT NULL", (page_id,)):
            text = self._archived_text(r["warc"], r["warc_offset"])
            if text is None:
                continue
            if (r["kind0"] or "") == "click":
                text = profiles.tab_content(text)
                if text is None:
                    continue
            elif r["sniff"] != "html":
                continue
            self._part_answer(r, text, r["warc"], r["warc_offset"], [page_id])
        for r in self.frontier.q(
                "SELECT DISTINCT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND "
                "u.state='done' AND u.final_url IS NOT NULL AND u.final_url <> u.url AND "
                "(u.status BETWEEN 300 AND 399 OR u.kind0='shortlink')", (page_id,)):
            self._replay_google(page, r)
        files = [{"res_key": a["location"], "how": "get", "role": "file", "need": "must", "res_id": u["id"]}
                 for a in self.frontier.q("SELECT a.location FROM page_res pr JOIN action a ON a.id = pr.action_id "
                                          "WHERE pr.page_id=? AND a.kind='click' AND a.state='done' "
                                          "AND a.location IS NOT NULL", (page_id,))
                 if (u := self.frontier.row(a["location"])) is not None]
        if files:
            self.frontier.add_page_res(page_id, files)
        for a in self.frontier.q("SELECT * FROM action WHERE list_id=? AND kind='detail' AND state='done' "
                                 "AND sniff='html' AND warc IS NOT NULL AND warc_offset IS NOT NULL", (page_id,)):
            text = self._archived_text(a["warc"], a["warc_offset"])
            if text is not None:
                got = extract(text, page["url"], profiles.content_css(page["url"]))
                entries, _n = self._parts(page, got, page_id, html=text, warc=a["warc"], warc_offset=a["warc_offset"])
                self.frontier.add_page_res(page_id, entries)
                google_api.resync(self.frontier, page_id)

    def _replay_google(self, page, row) -> None:
        """A resource of the page that redirects (a 3xx, a short link's target) and, hop by hop, ends on a Drive, Docs
        or Calendar address: that address's entries on the page again (the Google step's rows, all known already), as
        when the redirect was first read (_follow, _shortlink → _google_users), so that the page fetched again keeps
        them (review 10-26 phase 3). Sends nothing."""
        how = self._use_of(row)
        cur, seen = row, set()
        for _ in range(outcome.MAX_HOPS):
            if cur is None or cur["id"] in seen or cur["state"] != "done":
                return
            seen.add(cur["id"])
            to = cur["final_url"]
            if not to or to == cur["url"] or not (cur["status"] in REDIRECTS
                                                  or (cur["kind0"] or cur["kind"]) == outcome.SHORTLINK):
                return
            for link in self.frontier.q("SELECT dst FROM link WHERE src=?", (cur["id"],)):  # [redirect], [shortlink]
                adm = self.admit(link["dst"], None, page, how)
                if adm is not None and adm.step == "google" and self.google is not None and adm.url == to:
                    self._google_users(adm, page, cur, how, "redirect", [page["id"]])
                    return
            cur = self.frontier.row(to)

    def _archived_text(self, warc: str, offset: int) -> str | None:
        """A response record's body as text (postback.read_response), None when it cannot be read back."""
        try:
            _url, _status, headers, body = postback.read_response(self.root, warc, offset)
        except (OSError, ValueError, StopIteration):
            return None
        return postback.as_text(body, headers.get("content-type"))

    def _sitemap(self, row, snap: Snapshot) -> None:
        """A sitemap (sitemaps.org; design §5 ⑥): an index's sitemaps are sitemaps again, a list's addresses are
        queued like links (leaf 0). Read with a pattern, never an XML parser that would expand entities."""
        body = snap.body
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)[:MAX_BYTES]
            except (OSError, EOFError):
                return
        text = body.decode("utf-8", "replace")
        index = "<sitemapindex" in text[:2000].lower()
        for loc in _LOC.findall(text):
            href = htmllib.unescape(loc).strip()
            if index:
                e = self._enqueue(href, from_row=row, depth=row["depth"], found_via="link", prio=PRIO_LIST,
                                  kind0="sitemap")
            else:
                e = self._enqueue(href, from_row=row, depth=row["depth"] + 1, found_via="link")
            if (canon := scope.canonical(href)) is not None:
                self.frontier.link(row["id"], canon, "[sitemap]", e.verdict)

    def _parts(self, row, page: links.PageLinks, src_id: int, *, html: str | None = None, warc: str | None = None,
               warc_offset: int | None = None) -> tuple[list[dict], int]:
        """Admit what a page links and uses (design §4.4, §7.1, §7.2, §7.8): its links (the link table), and a
        page_res entry for each thing it uses, with its need and why. row: the page these count for (an embed box's
        parent); src_id: where they were read (the link table's src). html: what was read (a click rule reads the
        bound elements' values from it); warc, warc_offset: where it is archived (a file button's press keeps it as
        its evidence). A Drive, Docs or Calendar address the Google step takes makes its rows and entries
        (google_api.uses, attach: design §7.6); a click its platform rule presses is a GET row or a POST press
        (profiles.click_plan, design §7.4). Returns (page_res entries, links seen)."""
        base, url = page.base, row["url"]
        entries: dict[str, dict] = {}
        google_done: set[str] = set()

        def put(key: str, **e) -> None:
            entries.setdefault(key, {"res_key": key, **e})

        def admit(href: str, how: str, depth_: int, zone: str | None = None, text: str | None = None,
                  prio: int | None = None) -> Enqueued:
            adm = self.admit(href, base, row, how)
            if adm is None:
                return Enqueued("bad_url", None, None, None)
            if adm.step == "google" and self.google is not None:
                e, got = self._google(adm, from_row=row, how=how, depth=depth_, found_via="link", zone=zone,
                                      text=text)
                for x in got:
                    entries.setdefault(x["res_key"], x)
                google_done.add(adm.link)
                return e
            return self._enqueue(href, base=base, placement=adm.placement, url=adm.url, from_row=row, how=how,
                                 depth=depth_, found_via="link", prio=prio)

        def use(key: str, e: Enqueued, role: str, zone: str | None = None, text: str | None = None) -> None:
            pl = e.placement
            if pl is None:
                return
            if pl.verdict == "unhandled":
                put(f"google:{key}", how="unhandled", role="calendar" if "calendar" in key else "drive",
                    need="must", why=pl.reason, zone=zone, text=text)
            elif pl.verdict == "out":
                put(key, how="link_only", role=role, need="must", why=pl.reason, zone=zone, text=text)
            elif e.id is not None:
                put(key, how="get", role=role, need="round2" if pl.verdict == "round2" else "must", res_id=e.id,
                    zone=zone, text=text)

        depth = row["depth"] + 1
        for img in page.images:
            if img.src.startswith("data:"):
                put(img.src, how="get", role="img", need="no", why="data", zone=img.zone, text=img.alt)
                continue
            target = scope.canonical(img.src, base)
            if target is None:
                continue
            need, why = links.image_need(img, url, page.profiled)
            if need != "must":
                put(target, how="get", role="img", need="no", why=why, zone=img.zone, text=img.alt)
                continue
            e = admit(img.src, "image", depth, img.zone, img.alt)
            if target not in google_done:
                use(target, e, "img", img.zone, img.alt)
        frame_srcs = set()
        for fr in page.frames:
            target = scope.canonical(fr.src, base)
            if target is None:
                continue
            frame_srcs.add(fr.src)
            e = admit(fr.src, "hidden" if fr.hidden else "frame", depth, fr.zone, fr.title)
            if e.verdict != "out:internal_service":
                self.frontier.link(src_id, target, "[frame]" if fr.tag in ("iframe", "frame") else "[embed]",
                                   e.verdict)
            if target in google_done:
                continue
            role = "frame" if fr.tag == "frame" else "iframe"
            if e.placement is not None and e.placement.kind0 == outcome.SHORTLINK:
                role = "shortlink"
            use(target, e, role, fr.zone, fr.title)
        pagers, n = set(page.pagers), 0
        pictures = {scope.canonical(img.src, base) for img in page.images}
        for href, text in page.links:
            if text in ("[frame]", "[embed]") and href in frame_srcs:
                continue
            target = scope.canonical(href, base)
            if target is None:
                continue
            n += 1
            if target in pictures:
                continue  # an <a> straight to a picture: the images above, by its need
            pager = target in pagers
            e = admit(href, "link", row["depth"] if pager else depth, page.link_zone.get(href), text,
                      prio=PRIO_LIST if pager else None)
            if e.verdict == "out:internal_service":
                continue  # not even in the link table
            self.frontier.link(src_id, target, text, e.verdict)
            pl = e.placement
            if pl is not None and pl.reason in ("round2:offsite", "round2:offsite_file"):
                self.frontier.offsite(urlsplit(target).hostname or "", src_id, target[:300])
            if pl is None or target in google_done:
                continue
            if pl.verdict == "resource" or pl.kind0 in _USED_KINDS or (
                    pl.verdict == "unhandled" and pl.reason == round1.GOOGLE_UNHANDLED):
                use(target, e, _ROLE_OF0.get(pl.kind0, "file"), page.link_zone.get(href), text)
            elif pager and e.id is not None:
                put(target, how="get", role="pager", need="must", res_id=e.id, text=text)
        for src in page.scripts:
            target = scope.canonical(src, base)
            if target is None:
                continue
            e = self._enqueue(src, base=base, from_row=row, how="script", depth=depth, found_via="link")
            if e.id is not None:
                put(target, how="get", role="script", need="no", why="script", res_id=e.id)
        site = row["owner_site"] or ""
        for c in page.clicks:
            # the platform file's clicks rule reads the click's writing exactly (handle), else its decision (§7.8)
            decision = (profiles.click_decision(site, c, html, url) if self.decide is profiles.decision
                        else self.decide(site, c.key))
            plan = (profiles.click_plan(site, c, html, url)
                    if decision == "handle" and row["tier"] in round1.TIER_OF_IMP.values() else None)
            if plan is not None and plan.method == "GET":  # oia's tab: one GET per tab, the shown one too (§7.4)
                e = self._enqueue(plan.url, from_row=row, how="click", depth=depth, found_via="click")
                self.frontier.link(src_id, plan.url, "[click]", e.verdict)
                entry = postback.click_entry(plan, res_id=e.id)
                if e.id is None:  # a rule leaves the tab's address out: what it says is the tab's result
                    entry["why"] = (e.placement.reason if e.placement is not None else None) or "unhandled:unknown"
                entries.setdefault(plan.res_key, entry)
                continue
            if plan is not None:  # ee's file button: one POST per (k, number) of the site, a press (§7.4)
                aid = self.chains.click_action(row, plan, warc, warc_offset)
                entries.setdefault(plan.res_key, postback.click_entry(plan, action_id=aid))
                continue
            need, why = links.click_need(c, "round2" if decision == "handle" else decision)
            put(c.key, how="click", role="click", need=need, why=why or None, zone=c.zone, text=c.text)
        for u in page.unhandled:
            need, why = links.unhandled_need(self.decide(site, u.key))
            put(u.key, how="unhandled", role="click", need=need, why=why, zone=u.zone, text=u.text)
        for b in page.buttons:
            # with its argument: MasterGrid's Page$N is pressed, its Sort$x is not (profiles._button_key); with its
            # text: a rule that names one holds for that text only (SSO's bare ctlNN 「查詢」, review 10-26 phase 3)
            key = postback.button_key(b.target, b.argument)
            decision = (profiles.decision(site, key, b.text) if self.decide is profiles.decision
                        else self.decide(site, key)) or "unknown"
            self.frontier.postback(site, b.target, src_id, b.argument, b.text, b.form_id,
                                   decision if decision in ("press", "never", "ignore") else "unknown")
            if decision == "press":
                continue  # pressed by postback.py (page_stored plans it from the archived page), an action row
            need, why = links.unhandled_need(decision)
            old = entries.get(key)
            if old is None or (old["need"] == "no" and need != "no"):
                # one key, several buttons (ctlNN as ctl*): the one that still needs something is the one written
                entries[key] = {"res_key": key, "how": "unhandled", "role": "click", "need": need, "why": why,
                                "zone": b.zone, "text": b.text}
        for v in page.videos:
            self.frontier.video(row["id"], v)
        return list(entries.values()), n


def _utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)
