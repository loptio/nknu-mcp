"""The only way the crawler talks to the network: polite, retrying, strictly decoding, archiving."""

from __future__ import annotations

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

from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.tls import default_ssl_context, is_nknu_host, nknu_ssl_context
from nknu.crawl.warc import WarcSink

USER_AGENT = "nknu-facts-bot/1.0 (+https://github.com/loptio/nknu-mcp)"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 120
BREAKER_THRESHOLD = 3
_TEXT_TYPES = ("text/", "application/xhtml+xml", "application/xml", "application/json")
_CHARSET = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)
# Target.credential -> (request header, environment variable). The secret is read when the request is made and travels
# only in that header: never in a URL, a log line, an error message or the WARC archive (warc.py redacts the header).
CREDENTIALS = {"google_api_key": ("X-Goog-Api-Key", "GOOGLE_API_KEY")}
_HEADER_SAFE = re.compile(r"[!-~]+")  # a secret a header can carry: printable ASCII, no space or control character


class FetchError(Exception):
    pass


class HostCircuitOpen(FetchError):
    pass


class TooLarge(FetchError):
    """The body is bigger than the caller's max_bytes: the download stops there and nothing is stored."""


class UnwantedType(TooLarge):
    """The response's content type is one the caller skips (the archive crawl: images, video, audio): the body is not
    read and nothing is stored."""


class DecodeError(FetchError):
    pass


class RetryLater(FetchError):
    """429/503 with a Retry-After longer than a run waits in place: the host is skipped for the rest of this run (v1),
    and the archive crawl lets the whole server rest that many seconds."""

    def __init__(self, message: str, seconds: float):
        super().__init__(message)
        self.seconds = seconds


class ArchiveWriteError(Exception):
    """The response arrived but could not be written to the WARC archive (disk full, disk gone). Not a FetchError: the
    server answered, and nothing more should be fetched until the archive can be written again."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RetryableStatus(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


@dataclass(frozen=True)
class Snapshot:
    target: Target
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    text: str | None
    fetched_at: datetime
    sha256: str


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


class Client:
    def __init__(self, *, warc: WarcSink | None = None, sleep=time.sleep, clock=time.monotonic,
                 rng: random.Random | None = None, transport: httpx.BaseTransport | None = None,
                 follow_redirects: bool = True, pass_statuses: frozenset[int] = frozenset({404}),
                 strict_decode: bool = True, limits: httpx.Limits | None = None, skip_types: tuple[str, ...] = ()):
        """follow_redirects=False hands a 3xx back as a snapshot (the archive crawler queues each hop by its own
        server); pass_statuses are the 4xx answers handed back as snapshots instead of raising. Every response is
        written to WARC before it is decoded; strict_decode=False (the archive crawl) hands back a text it cannot
        decode strictly as text=None with meta["decode_error"] instead of raising DecodeError. limits: the connection
        pool's, for both pools (the archive crawl keeps no connection open between requests)."""
        timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
        common = {"timeout": timeout, "follow_redirects": True, "headers": {"User-Agent": USER_AGENT}}
        if limits is not None:
            common["limits"] = limits
        if transport is not None:
            self._nknu = self._other = httpx.Client(transport=transport, **common)
        else:
            self._nknu = httpx.Client(verify=nknu_ssl_context(), **common)
            self._other = httpx.Client(verify=default_ssl_context(), **common)
        self.warc = warc
        self._follow, self._pass, self._strict_decode = follow_redirects, pass_statuses, strict_decode
        self._skip_types = tuple(t.lower() for t in skip_types)  # content-type prefixes not downloaded (streamed only)
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

    def fetch(self, target: Target, rate: RateLimit, max_bytes: int | None = None,
              pass_statuses: frozenset[int] | None = None) -> Snapshot:
        host = urlsplit(target.url).hostname or ""
        if host in self._skip:
            raise HostCircuitOpen(f"{host}: {self._skip[host]}")
        if self._failures[host] >= BREAKER_THRESHOLD:
            raise HostCircuitOpen(f"{host}: {BREAKER_THRESHOLD} consecutive failures, skipping for this run")
        client = self._nknu if is_nknu_host(host) else self._other
        body, headers = None, {}
        if target.data is not None:
            body = urlencode(target.data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        secret = None
        if target.credential is not None:
            header, env = CREDENTIALS[target.credential]
            if not (secret := os.environ.get(env, "").strip()):
                raise FetchError(f"{target.url}: {env} is not set (deploy/env.example)")
            if not _HEADER_SAFE.fullmatch(secret):  # the header library would quote the whole value in its error
                raise FetchError(f"{target.url}: {env} holds a character a header cannot carry; check the env file")
            headers[header] = secret
        with self._locks[host]:  # per-host concurrency 1
            retry_at: float | None = None  # the server's Retry-After, waited before the next attempt (never after the last)
            try:
                for attempt in stamina.retry_context(on=_retryable, attempts=4, timeout=300, wait_initial=2, wait_max=60):
                    with attempt:
                        if retry_at is not None and (delay := retry_at - self._clock()) > 0:
                            self._sleep(delay)
                        self._wait_polite(host, rate)
                        try:
                            # a credential never follows a redirect: httpx drops Authorization on another host, not ours
                            resp, content = self._send(client, target, body, headers, secret is None and self._follow,
                                                       max_bytes)
                        finally:
                            self._last[host] = self._clock()  # after the body: the interval counts from the response's end
                        if resp.status_code in RETRY_STATUSES:
                            ra = _retry_after(resp)
                            if ra is not None and ra > MAX_RETRY_AFTER_S:
                                # asked to stay away longer than a run waits: give up on this host until the next run
                                why = f"HTTP {resp.status_code} with Retry-After {ra:.0f} s (> {MAX_RETRY_AFTER_S} s)"
                                self._skip[host] = f"{why}, skipping for this run"
                                raise RetryLater(f"{target.url}: {why}", ra)
                            retry_at = self._clock() + ra if ra else None
                            raise RetryableStatus(resp.status_code)
            except (RetryableStatus, httpx.HTTPError) as exc:
                self._fail(host)
                why = str(exc).replace(secret, "[redacted]") if secret else str(exc)
                raise FetchError(f"{target.url}: {why}") from exc
        if secret is not None and resp.is_redirect:
            self._fail(host)
            raise FetchError(f"{target.url}: HTTP {resp.status_code}, not followed: the request carries a credential")
        if resp.status_code >= 400 and resp.status_code not in (self._pass if pass_statuses is None else pass_statuses):
            self._fail(host)
            raise FetchError(f"{target.url}: HTTP {resp.status_code}")
        self._failures[host] = 0
        ctype = resp.headers.get("content-type", "")
        is_text = resp.status_code == 200 and any(ctype.startswith(t) or t in ctype for t in _TEXT_TYPES)
        snap = Snapshot(
            target=target, url=str(resp.url), status=resp.status_code, headers=dict(resp.headers), body=content,
            text=None, fetched_at=datetime.now(UTC), sha256=hashlib.sha256(content).hexdigest(),
        )
        if self.warc is not None:  # the raw response first, whatever decoding makes of it
            try:
                self.warc.write(snap, dict(resp.request.headers), body or b"")
            except OSError as exc:
                raise ArchiveWriteError(f"{target.url}: the WARC archive could not be written: {exc}",
                                        resp.status_code) from exc
        if is_text:
            try:
                snap = replace(snap, text=decode_body(content, ctype))
            except DecodeError:
                if self._strict_decode:
                    raise
                snap = replace(snap, target=replace(target, meta={**target.meta, "decode_error": "1"}))
        return snap

    def _send(self, client: httpx.Client, target: Target, body: bytes | None, headers: dict, follow: bool,
              max_bytes: int | None) -> tuple[httpx.Response, bytes]:
        """One request, its body read in full; with max_bytes the body streams and the download stops past the cap."""
        if max_bytes is None:
            resp = client.request(target.method, target.url, content=body, headers=headers, follow_redirects=follow)
            return resp, resp.content
        req = client.build_request(target.method, target.url, content=body, headers=headers)
        resp = client.send(req, stream=True, follow_redirects=follow)
        try:
            ctype = resp.headers.get("content-type", "").lower().strip()
            if self._skip_types and resp.status_code == 200 and ctype.startswith(self._skip_types):
                raise UnwantedType(f"{target.url}: {ctype.split(';')[0]} not downloaded")
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise TooLarge(f"{target.url}: {int(declared)} bytes declared (> {max_bytes})")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(f"{target.url}: more than {max_bytes} bytes")
                chunks.append(chunk)
            return resp, b"".join(chunks)
        finally:
            resp.close()

    def close(self) -> None:
        self._nknu.close()
        if self._other is not self._nknu:
            self._other.close()
