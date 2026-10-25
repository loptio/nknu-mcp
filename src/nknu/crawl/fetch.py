"""The only way the crawler talks to the network: polite, retrying, strictly decoding, archiving.

Every answer is written to WARC before anything is made of it (a 503 before it is retried, a page before it is decoded),
as it came (review 10-26 A1): the body as the wire carried it, still compressed, every header line in its own case and
order, the status line's own reason phrase; the program reads a decoded copy (Snapshot.body, Snapshot.headers).
Each fetch hands back what every request it sent did on the wire (Snapshot.sent, or FetchError.sent when it fails):
whether a connection was opened for it, how long TCP and TLS took, the server's address (design §6.2), so that the
archive crawl's request table has one row per request that went out."""

from __future__ import annotations

import copy
import email.utils
import hashlib
import os
import random
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

import httpx
import stamina

from nknu.crawl import schoollock
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.tls import default_ssl_context, is_nknu_host, nknu_ssl_context
from nknu.crawl.warc import WarcSink

USER_AGENT = "nknu-facts-bot/1.0 (+https://github.com/loptio/nknu-mcp)"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 120
# how long a 429 without a Retry-After asks for, when the last try gets one (RFC 6585 §4: it MAY say, and often does not)
RETRY_429_S = 60.0
BREAKER_THRESHOLD = 3
_TEXT_TYPES = ("text/", "application/xhtml+xml", "application/xml", "application/json")
_CHARSET = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)
# Target.credential -> (request header, environment variable, the hosts it may go to). The secret is read when the
# request is made and travels only in that header, over https, to those hosts (review 10-26: whatever a Target says):
# never in a URL, a log line, an error message, an exception's cause or the WARC archive (warc.py redacts the header).
CREDENTIALS = {"google_api_key": ("X-Goog-Api-Key", "GOOGLE_API_KEY", frozenset({"www.googleapis.com"}))}
_HEADER_SAFE = re.compile(r"[!-~]+")  # a secret a header can carry: printable ASCII, no space or control character
# The archive crawl's limits (design §6.2): one connection per server, kept open up to a minute between requests, so a
# server slow to connect to (c.nknu: about 3.6 s a connection) pays that once instead of on every request. With one
# connection allowed, httpcore closes the pool's idle connection before it opens one to another name of the server.
KEEPALIVE_LIMITS = httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=60)
# Each request's total time from its start (design §6.5): a page, and anything else (attachments, API answers)
PAGE_TIME_LIMIT_S = 120.0
FILE_TIME_LIMIT_S = 600.0
_PAGE_TYPES = ("text/html", "application/xhtml+xml")
# how a request fails on a reused connection the server had already closed, before any answer (design §6.4): a reset
# (ReadError, WriteError), or the end of the connection before a byte of answer, in httpcore's words (1.0.9,
# _sync/http11.py). Any other RemoteProtocolError is an answer HTTP cannot read: the server got the request.
_IDLE_CLOSED = (httpx.ReadError, httpx.WriteError)
_NO_ANSWER = "Server disconnected without sending a response."
# the least wait before that resend: whether the server had read the request it closed the connection on cannot be
# told (a reset or an end of connection looks the same either way), so the resend keeps the least gap between two
# requests to a server (CLAUDE.md 2–5 s; monitor.politeness counts less than 2 s as a violation). Review 10-26: design
# §6.4 said 「立刻」; 【Claude 的执行办法】
RESEND_GAP_S = 2.0


class FetchError(Exception):
    sent: tuple[Sent, ...] = ()  # the requests that went out for it (Client.fetch sets them; none: refused before)


class HostCircuitOpen(FetchError):
    pass


class TooLarge(FetchError):
    """The body is bigger than the caller's max_bytes: the download stops there and nothing is stored."""


class TooSlow(httpx.ReadTimeout):
    """The response took longer than its total time limit (Client time_limits): a timeout like a read timeout, so it is
    retried the way timeouts are (v1: in place; the archive crawl: through its frontier). Nothing is stored."""


class UnwantedType(TooLarge):
    """The response's content type is one the caller skips (the archive crawl: images, video, audio): the body is not
    read and nothing is stored."""


class DecodeError(FetchError):
    pass


class RetryLater(FetchError):
    """The server asked us to come back later, and the client will not wait in place: a Retry-After longer than a run
    waits (the host is skipped for the rest of this run), or the last try answered with a Retry-After or a 429. The
    archive crawl lets the whole server rest that many seconds (at least its own cool-down)."""

    def __init__(self, message: str, seconds: float):
        super().__init__(message)
        self.seconds = seconds


class ArchiveWriteError(Exception):
    """The response arrived but could not be written to the WARC archive (disk full, disk gone). Not a FetchError: the
    server answered, and nothing more should be fetched until the archive can be written again."""

    sent: tuple[Sent, ...] = ()

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RetryableStatus(Exception):
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after = status, retry_after

    def wait_asked(self) -> float | None:
        """How long the server asked us to stay away: its Retry-After, or RETRY_429_S for a 429 that does not say."""
        if self.retry_after:
            return self.retry_after
        return RETRY_429_S if self.status == 429 else None


@dataclass(frozen=True)
class Sent:
    """One request as it went out, for the archive crawl's request table (design §6.2, §6.4, §12). started and ended
    are on the client's clock. The connection facts come from httpcore's trace: they stay None on a transport without
    a connection pool (a test's MockTransport), or when the request never got a connection."""

    attempt: int  # 1, 2, ... within one fetch: a resend on a new connection, or a retry (v1), counts up
    method: str
    started: float
    ended: float
    status: int | None  # the answer's, once its headers arrived
    error: str | None  # why the request failed (any secret redacted); None when its answer came back whole
    bytes: int | None  # body bytes read off the wire (before any Content-Encoding is decoded)
    new_conn: bool | None  # True: a connection was opened for it; False: it reused one left open
    connect_ms: float | None  # the TCP connect, name lookup included
    tls_ms: float | None  # the TLS handshake
    ip: str | None  # the server's address (IPv4 or IPv6)
    idle_closed: bool = False  # it failed on a reused connection the server had closed: no answer, nothing reached it
    warc_offset: int | None = None  # where its answer's response record starts in the client's WARC file (a 503 too)
    warc_len: int | None = None


@dataclass(frozen=True)
class Snapshot:
    """One answer. headers and body are what the program reads: one lowercase name per header (a name sent twice
    joined by ", "), the body decoded from its Content-Encoding; sha256 is the decoded body's. The answer as it came is
    kept for the WARC archive (warc.py): raw_headers (each line's name and value as bytes, in order, a name sent twice
    twice), reason and http_version (the status line), raw_body (the body still compressed; None when it is body
    itself). A snapshot made by hand (tests, failed_snapshot) has no raw_headers, and warc.py writes what headers and
    body say."""

    target: Target
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    text: str | None
    fetched_at: datetime
    sha256: str
    sent: tuple[Sent, ...] = ()  # every request that went out for it, the answered one last
    warc_offset: int | None = None  # where its response record starts in the client's WARC file (WarcSink.path)
    warc_len: int | None = None
    raw_headers: tuple[tuple[bytes, bytes], ...] = ()
    reason: bytes | None = None  # the status line's reason phrase as the server wrote it (b"" when it wrote none)
    http_version: str = "HTTP/1.1"
    raw_body: bytes | None = None


def failed_snapshot(target: Target, error: str) -> Snapshot:
    """What a page that could not be fetched looks like to parse(): status 0, no body, the error in meta['error']."""
    return Snapshot(target=replace(target, meta={**target.meta, "error": error}), url=target.url, status=0, headers={},
                    body=b"", text=None, fetched_at=datetime.now(UTC), sha256=hashlib.sha256(b"").hexdigest())


def decode_body(body: bytes, content_type: str | None) -> str:
    """Strict decoding to str before any parser sees the page. HTML <meta charset> is ignored on purpose
    (c.nknu says big5 in meta but serves UTF-8)."""
    tried = []
    m = _CHARSET.search(content_type or "")
    if m:
        cs = m.group(1).lower()
        tried.append("cp950" if cs in ("big5", "big-5", "x-big5") else cs)
    tried += ["utf-8", "cp950"]
    for cs in dict.fromkeys(tried):
        try:
            return body.decode(cs).removeprefix("﻿")
        except (UnicodeError, LookupError):  # UnicodeError: a charset such as "undefined" that names no codec
            continue
    raise DecodeError(f"cannot decode body strictly as any of {list(dict.fromkeys(tried))}")


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    if not v:
        return None
    if v.strip().isdigit():
        return float(v)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(v).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (RetryableStatus, httpx.TransportError))


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _clean_cause(exc: BaseException, secret: str | None) -> BaseException:
    """exc as the cause of the FetchError raised for it (a traceback prints the cause, log.exception included): itself,
    unless its text or the chain behind it holds the secret; then a copy of the same type (crawler.error_kind reads
    the type) whose message is redacted and which has no chain of its own."""
    if not secret:
        return exc
    chain, seen = [], set()
    x: BaseException | None = exc
    while x is not None and id(x) not in seen:
        seen.add(id(x))
        chain.append(x)
        x = x.__cause__ or x.__context__
    if not any(secret in str(c) or secret in repr(c) for c in chain):
        return exc
    try:
        clean = copy.copy(exc)
        clean.args = (_redact(str(exc), secret),)
    except Exception:  # noqa: BLE001 - an exception that cannot be copied: a plain one, redacted
        clean = RuntimeError(_redact(f"{type(exc).__name__}: {exc}", secret))
    clean.__cause__ = clean.__context__ = None
    clean.__suppress_context__ = True
    return clean.with_traceback(None)


def _peer(stream) -> str | None:
    """The server's address on an httpcore network stream, when the stream can tell."""
    try:
        addr = stream.get_extra_info("server_addr") if stream is not None else None
    except OSError:  # a socket already closed
        return None
    return str(addr[0]) if isinstance(addr, tuple) and addr else None


class _Trace:
    """What one request did on the wire. httpcore calls it with its trace events (the request extension "trace":
    connection.connect_tcp.*, connection.start_tls.*, http11.send_request_headers.*, ...); _send adds the status and the
    body bytes read. A transport without a connection pool sends no events, and the connection facts stay None."""

    def __init__(self) -> None:
        self.new_conn: bool | None = None
        self.connect_ms: float | None = None
        self.tls_ms: float | None = None
        self.ip: str | None = None
        self.status: int | None = None
        self.bytes: int | None = None
        self._began: dict[str, float] = {}

    def __call__(self, event: str, info: dict) -> None:
        step, _, phase = event.rpartition(".")
        if phase == "started":
            self._began[step] = time.perf_counter()
            if step == "connection.connect_tcp":
                self.new_conn = True
            elif step.endswith(".send_request_headers") and self.new_conn is None:
                self.new_conn = False  # the pool handed it a connection that was already open
            return
        began = self._began.pop(step, None)
        if began is None:
            return
        ms = (time.perf_counter() - began) * 1000  # real time, whatever the client's clock; a failed connect is timed too
        if step == "connection.connect_tcp":
            self.connect_ms = ms
            self.ip = _peer(info.get("return_value")) or self.ip
        elif step == "connection.start_tls":
            self.tls_ms = ms

    def answered(self, resp: httpx.Response) -> None:
        self.status, self.bytes = resp.status_code, 0
        self.ip = self.ip or _peer(resp.extensions.get("network_stream"))  # a reused connection had no connect event

    def idle_closed(self, exc: Exception) -> bool:
        """The connection this request reused had been closed by the server, and no answer came: the request never
        reached it (RFC 9112 §9.3.1)."""
        if self.new_conn is not False or self.status is not None:
            return False
        return isinstance(exc, _IDLE_CLOSED) or (isinstance(exc, httpx.RemoteProtocolError) and str(exc) == _NO_ANSWER)

    def sent(self, attempt: int, method: str, started: float, ended: float, error: str | None,
             idle_closed: bool = False) -> Sent:
        return Sent(attempt=attempt, method=method, started=started, ended=ended, status=self.status, error=error,
                    bytes=self.bytes, new_conn=self.new_conn, connect_ms=self.connect_ms, tls_ms=self.tls_ms,
                    ip=self.ip, idle_closed=idle_closed)


def _close_connections(pool: httpx.Client) -> None:
    """Close every connection a client's pool holds and keep the client usable. httpx has no public call for this: its
    transport's close() empties the httpcore pool, which opens new connections as before (httpcore 1.0.9;
    tests/archive/test_fetch_keepalive.py notices if that changes). It is the transport every request goes through
    because a one-connection client has no proxy transports (trust_env=False)."""
    pool._transport.close()


class Client:
    def __init__(self, *, warc: WarcSink | None = None, sleep=time.sleep, clock=time.monotonic,
                 rng: random.Random | None = None, transport: httpx.BaseTransport | None = None,
                 follow_redirects: bool = True, pass_statuses: frozenset[int] = frozenset({404}),
                 strict_decode: bool = True, limits: httpx.Limits | None = None, skip_types: tuple[str, ...] = (),
                 attempts: int = 4, time_limits: tuple[float, float] = (PAGE_TIME_LIMIT_S, FILE_TIME_LIMIT_S),
                 resend_gap_s: float = RESEND_GAP_S):
        """follow_redirects=False hands a 3xx back as a snapshot (the archive crawler queues each hop by its own
        server); pass_statuses are the 4xx answers handed back as snapshots instead of raising. Every response is
        written to WARC before it is judged or decoded; strict_decode=False (the archive crawl) hands back a text it
        cannot decode strictly as text=None with meta["decode_error"] instead of raising DecodeError.

        limits: the connection pool's, for both pools. With max_connections=1 (the archive crawl: one client per server
        queue, used by that queue's thread alone) the two pools hold one connection between them: before the client
        uses one pool it closes the other's, so a server reached by host name and by bare IP never has two
        connections from it, idle ones included (design §6.2; KEEPALIVE_LIMITS keeps it open between requests). Such
        a client asks the server itself, never a proxy from the environment (or a .netrc login): a proxy is reached
        through a transport of its own, which closing the other pool would not reach.
        attempts: tries per fetch for a 408/429/5xx answer or a transport failure, with backoff (v1: 4; the archive
        crawl: 1, its frontier retries later); when the last one is asked to wait, RetryLater says how long.
        time_limits: each request's total time in seconds from its start, for a page and for anything else (§6.5).
        resend_gap_s: the least wait before a GET is sent again after the server closed the kept connection it went out
        on (the rate's own gap when that is longer; the archive crawl passes its GAP's least)."""
        timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
        common = {"timeout": timeout, "follow_redirects": True, "headers": {"User-Agent": USER_AGENT}}
        self._one_conn = limits is not None and limits.max_connections == 1
        if limits is not None:
            common["limits"] = limits
        if self._one_conn:
            common["trust_env"] = False
        # the real network: a request to a school host needs the machine-wide lock (schoollock.require, design §6.9,
        # review 10-26 C). Only a transport that reaches no server goes without: httpx's MockTransport (the tests'), or
        # one that says so (offline = True: the offline rehearsal's). Any other, httpx.HTTPTransport() included, may
        # reach the school (second review 10-26)
        self._real = not (isinstance(transport, httpx.MockTransport) or getattr(transport, "offline", False) is True)
        if transport is not None:
            self._nknu = self._other = httpx.Client(transport=transport, **common)
        else:
            self._nknu = httpx.Client(verify=nknu_ssl_context(), **common)
            self._other = httpx.Client(verify=default_ssl_context(), **common)
        self.warc = warc
        self._follow, self._pass, self._strict_decode = follow_redirects, pass_statuses, strict_decode
        self._skip_types = tuple(t.lower() for t in skip_types)  # content-type prefixes not downloaded (streamed only)
        self._attempts = attempts
        self._resend_gap_s = resend_gap_s
        self._page_s, self._file_s = time_limits
        self._in_use: httpx.Client | None = None  # the pool the last request went through
        self._sleep, self._clock, self._rng = sleep, clock, rng or random.Random()
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last: dict[str, float] = {}
        self._failures: defaultdict[str, int] = defaultdict(int)
        self._skip: dict[str, str] = {}  # host -> why it is left alone for the rest of this run

    def _wait_polite(self, host: str, rate: RateLimit) -> None:
        if host in self._last:
            due = self._last[host] + rate.min_interval_s + self._rng.uniform(0, rate.jitter_s)
            delay = due - self._clock()
            if delay > 0:
                self._sleep(delay)

    def _fail(self, host: str) -> None:
        self._failures[host] += 1

    def reset_host(self, host: str) -> None:
        """Let a host that opened the breaker (or sent a long Retry-After) be tried again, after the caller's cool-down."""
        self._failures.pop(host, None)
        self._skip.pop(host, None)

    def _pool(self, host: str) -> httpx.Client:
        """The school's certificate pool or the general one; a one-connection client closes the other pool first."""
        pool = self._nknu if is_nknu_host(host) else self._other
        if self._one_conn and self._in_use is not None and self._in_use is not pool:
            _close_connections(self._in_use)
        self._in_use = pool
        return pool

    def _headers(self, target: Target) -> tuple[dict[str, str], str | None]:
        """The request's own headers (the client adds its User-Agent), and the secret among them if there is one."""
        headers = dict(target.headers)
        if target.data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if target.credential is None:
            return headers, None
        header, env, hosts = CREDENTIALS[target.credential]
        parts = urlsplit(target.url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in hosts:
            raise FetchError(f"{target.url}: the credential {target.credential} goes only to {sorted(hosts)} over "
                             "https; not sent")
        if not (secret := os.environ.get(env, "").strip()):
            raise FetchError(f"{target.url}: {env} is not set (deploy/env.example)")
        if not _HEADER_SAFE.fullmatch(secret):  # the header library would quote the whole value in its error
            raise FetchError(f"{target.url}: {env} holds a character a header cannot carry; check the env file")
        headers[header] = secret
        return headers, secret

    def fetch(self, target: Target, rate: RateLimit, max_bytes: int | None = None,
              pass_statuses: frozenset[int] | None = None) -> Snapshot:
        """The answer for one target. The snapshot carries every request that went out for it (Snapshot.sent); so does
        a failure (FetchError.sent, ArchiveWriteError.sent), which has none when it was refused before sending."""
        sent: list[Sent] = []
        try:
            return self._fetch(target, rate, max_bytes, pass_statuses, sent)
        except (FetchError, ArchiveWriteError) as exc:
            exc.sent = tuple(sent)
            raise

    def _fetch(self, target: Target, rate: RateLimit, max_bytes: int | None, pass_statuses: frozenset[int] | None,
               sent: list[Sent]) -> Snapshot:
        host = urlsplit(target.url).hostname or ""
        if host in self._skip:
            raise HostCircuitOpen(f"{host}: {self._skip[host]}")
        if self._failures[host] >= BREAKER_THRESHOLD:
            raise HostCircuitOpen(f"{host}: {BREAKER_THRESHOLD} consecutive failures, skipping for this run")
        if self._real:
            schoollock.require(host)  # SchoolLockNotHeld before anything is sent (not a FetchError: a program's bug)
        headers, secret = self._headers(target)
        body = urlencode(target.data).encode() if target.data is not None else None
        client = self._pool(host)
        # a credential never follows a redirect: httpx drops Authorization on another host, but not X-Goog-Api-Key
        follow = secret is None and self._follow
        with self._locks[host]:  # per-host concurrency 1
            retry_at: float | None = None  # the server's Retry-After, waited before the next attempt (never after the last)
            try:
                for attempt in stamina.retry_context(on=_retryable, attempts=self._attempts, timeout=300,
                                                     wait_initial=2, wait_max=60):
                    with attempt:
                        if retry_at is not None and (delay := retry_at - self._clock()) > 0:
                            self._sleep(delay)
                        self._wait_polite(host, rate)
                        try:
                            resp, raw, content, broken = self._exchange(client, target, body, headers, follow,
                                                                        max_bytes, sent, secret, host, rate)
                        finally:
                            self._last[host] = self._clock()  # after the body: the interval counts from the response's end
                        snap = self._archive(target, resp, content, raw, body)  # every answer, before it is judged
                        sent[-1] = replace(sent[-1], warc_offset=snap.warc_offset, warc_len=snap.warc_len)
                        if resp.status_code in RETRY_STATUSES:
                            ra = _retry_after(resp)
                            if ra is not None and ra > MAX_RETRY_AFTER_S:
                                # asked to stay away longer than a run waits: give up on this host until the next run
                                why = f"HTTP {resp.status_code} with Retry-After {ra:.0f} s (> {MAX_RETRY_AFTER_S} s)"
                                self._skip[host] = f"{why}, skipping for this run"
                                raise RetryLater(f"{target.url}: {why}", ra)
                            retry_at = self._clock() + ra if ra else None
                            raise RetryableStatus(resp.status_code, ra)
                        if broken is not None:  # archived as it came; the program cannot read it (not retried)
                            raise broken
            except (RetryableStatus, httpx.HTTPError) as exc:
                self._fail(host)
                if isinstance(exc, RetryableStatus) and (wait := exc.wait_asked()) is not None:
                    # the last try was asked to wait: the caller hears how long (the archive crawl rests the server)
                    said = "with Retry-After" if exc.retry_after else "without Retry-After, taken as"
                    raise RetryLater(f"{target.url}: HTTP {exc.status} {said} {wait:.0f} s", wait) from _clean_cause(
                        exc, secret)
                raise FetchError(f"{target.url}: {_redact(str(exc), secret)}") from _clean_cause(exc, secret)
        if secret is not None and resp.is_redirect:
            self._fail(host)
            raise FetchError(f"{target.url}: HTTP {resp.status_code}, not followed: the request carries a credential")
        if resp.status_code >= 400 and resp.status_code not in (self._pass if pass_statuses is None else pass_statuses):
            self._fail(host)
            raise FetchError(f"{target.url}: HTTP {resp.status_code}")
        self._failures[host] = 0
        snap = replace(snap, sent=tuple(sent))
        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 200 and any(ctype.startswith(t) or t in ctype for t in _TEXT_TYPES):
            try:
                snap = replace(snap, text=decode_body(content, ctype))
            except DecodeError:
                if self._strict_decode:
                    raise
                snap = replace(snap, target=replace(target, meta={**target.meta, "decode_error": "1"}))
        return snap

    def _exchange(self, client: httpx.Client, target: Target, body: bytes | None, headers: dict, follow: bool,
                  max_bytes: int | None, sent: list[Sent], secret: str | None, host: str,
                  rate: RateLimit) -> tuple[httpx.Response, bytes, bytes, httpx.DecodingError | None]:
        """Send the request and note it in `sent`. A GET whose reused connection the server had closed before answering
        goes out once more on a new connection (RFC 9112 §9.3.1), after the polite gap and at least resend_gap_s (the
        server may have read the first): both are noted, and only the second can count against the host. A POST never
        goes out twice (RFC 9110 §9.2.2: only idempotent requests are resent)."""
        resend = target.method == "GET"
        while True:
            trace, started = _Trace(), self._clock()
            try:
                got = self._send(client, target, body, headers, follow, max_bytes, trace)
            except Exception as exc:
                idle = trace.idle_closed(exc)
                error = _redact(f"{type(exc).__name__}: {exc}", secret)
                sent.append(trace.sent(len(sent) + 1, target.method, started, self._clock(), error, idle))
                if idle and resend:
                    resend = False
                    self._last[host] = self._clock()
                    self._wait_polite(host, rate)
                    if (short := self._resend_gap_s - (self._clock() - self._last[host])) > 0:
                        self._sleep(short)
                    continue
                raise
            error = None if got[3] is None else _redact(f"{type(got[3]).__name__}: {got[3]}", secret)
            sent.append(trace.sent(len(sent) + 1, target.method, started, self._clock(), error))
            return got

    def _send(self, client: httpx.Client, target: Target, body: bytes | None, headers: dict, follow: bool,
              max_bytes: int | None, trace: _Trace) -> tuple[httpx.Response, bytes, bytes, httpx.DecodingError | None]:
        """One request, its body streamed and read in full as the wire carries it (raw: still compressed, which the
        WARC keeps) and decoded as it comes (content: what the program reads). max_bytes stops a download past the cap,
        on the wire or once decoded (a small gzip of a huge file); the total time limit stops one that is still coming
        (a page after 120 s, anything else after 600 s, counted from the start). A body its Content-Encoding cannot
        decode is read to its end all the same and comes back with the error, so that it is archived before it fails.

        The decoder is httpx's own for the answer's Content-Encoding (the one iter_bytes() uses; a private method as of
        httpx 0.28.1: tests/crawl/test_warc_raw.py notices if it changes). An answer built in memory (a test's
        MockTransport) was read and decoded by httpx when it was made: its stream gives the same bytes again, and its
        content is the decoded copy."""
        started = self._clock()
        req = client.build_request(target.method, target.url, content=body, headers=headers,
                                   extensions={"trace": trace})
        resp = client.send(req, stream=True, follow_redirects=follow)
        try:
            trace.answered(resp)
            ctype = resp.headers.get("content-type", "").lower().strip()
            if self._skip_types and resp.status_code == 200 and ctype.startswith(self._skip_types):
                raise UnwantedType(f"{target.url}: {ctype.split(';')[0]} not downloaded")
            declared = resp.headers.get("content-length", "")
            if max_bytes is not None and declared.isdigit() and int(declared) > max_bytes:
                raise TooLarge(f"{target.url}: {int(declared)} bytes declared (> {max_bytes})")
            limit = self._page_s if ctype.startswith(_PAGE_TYPES) else self._file_s
            built = resp.is_stream_consumed
            decoder = None if built else resp._get_content_decoder()
            raw: list[bytes] = []
            chunks: list[bytes] = []
            wire = size = 0
            broken: httpx.DecodingError | None = None

            def decoded(out: bytes) -> None:
                nonlocal size
                size += len(out)
                if max_bytes is not None and size > max_bytes:
                    raise TooLarge(f"{target.url}: more than {max_bytes} bytes once decoded")
                chunks.append(out)

            for chunk in (iter(resp.stream) if built else resp.iter_raw()):
                wire += len(chunk)
                trace.bytes = wire
                if max_bytes is not None and wire > max_bytes:
                    raise TooLarge(f"{target.url}: more than {max_bytes} bytes")
                if self._clock() - started > limit:
                    raise TooSlow(f"{target.url}: still coming after {limit:.0f} s", request=req)
                raw.append(chunk)
                if decoder is not None and broken is None:
                    try:
                        decoded(decoder.decode(chunk))
                    except httpx.DecodingError as exc:
                        broken = exc
            if decoder is None:
                decoded(resp.content)
            elif broken is None:
                try:
                    decoded(decoder.flush())
                except httpx.DecodingError as exc:
                    broken = exc
            return resp, b"".join(raw), b"".join(chunks), broken
        finally:
            resp.close()

    def _archive(self, target: Target, resp: httpx.Response, content: bytes, raw: bytes,
                 body: bytes | None) -> Snapshot:
        """The answer as a snapshot, written to WARC first, whatever comes of it: a 503 before it is retried, a 403
        before it is refused, a page before it is decoded. The WARC gets the answer as it came (raw, resp.headers.raw,
        the reason phrase) and the request as it was sent (its header lines in their own case; warc.py hides the key).
        The snapshot notes where its response record is."""
        reason = resp.extensions.get("reason_phrase")
        snap = Snapshot(
            target=target, url=str(resp.url), status=resp.status_code, headers=dict(resp.headers), body=content,
            text=None, fetched_at=datetime.now(UTC), sha256=hashlib.sha256(content).hexdigest(),
            raw_headers=tuple(resp.headers.raw),
            reason=reason if isinstance(reason, bytes) else resp.reason_phrase.encode("ascii", "replace"),
            http_version=resp.http_version, raw_body=None if raw == content else raw,
        )
        if self.warc is None:
            return snap
        sent_headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in resp.request.headers.raw]
        try:
            place = self.warc.write(snap, sent_headers, body or b"")
        except OSError as exc:
            raise ArchiveWriteError(f"{target.url}: the WARC archive could not be written: {exc}",
                                    resp.status_code) from exc
        offset, length = place or (None, None)
        return replace(snap, warc_offset=offset, warc_len=length)

    def close(self) -> None:
        self._nknu.close()
        if self._other is not self._nknu:
            self._other.close()
