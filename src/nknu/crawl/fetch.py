"""The only way the crawler talks to the network: polite, retrying, strictly decoding, archiving."""

from __future__ import annotations

import email.utils
import hashlib
import random
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
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


class FetchError(Exception):
    pass


class HostCircuitOpen(FetchError):
    pass


class DecodeError(FetchError):
    pass


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
        except (UnicodeDecodeError, LookupError):
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
                 rng: random.Random | None = None, transport: httpx.BaseTransport | None = None):
        timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
        common = {"timeout": timeout, "follow_redirects": True, "headers": {"User-Agent": USER_AGENT}}
        if transport is not None:
            self._nknu = self._other = httpx.Client(transport=transport, **common)
        else:
            self._nknu = httpx.Client(verify=nknu_ssl_context(), **common)
            self._other = httpx.Client(verify=default_ssl_context(), **common)
        self.warc = warc
        self._sleep, self._clock, self._rng = sleep, clock, rng or random.Random()
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last: dict[str, float] = {}
        self._failures: defaultdict[str, int] = defaultdict(int)

    def _wait_polite(self, host: str, rate: RateLimit) -> None:
        if host in self._last:
            due = self._last[host] + rate.min_interval_s + self._rng.uniform(0, rate.jitter_s)
            delay = due - self._clock()
            if delay > 0:
                self._sleep(delay)

    def _fail(self, host: str) -> None:
        self._failures[host] += 1

    def fetch(self, target: Target, rate: RateLimit) -> Snapshot:
        host = urlsplit(target.url).hostname or ""
        if self._failures[host] >= BREAKER_THRESHOLD:
            raise HostCircuitOpen(f"{host}: {BREAKER_THRESHOLD} consecutive failures, skipping for this run")
        client = self._nknu if is_nknu_host(host) else self._other
        body, headers = None, {}
        if target.data is not None:
            body = urlencode(target.data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        with self._locks[host]:  # per-host concurrency 1
            try:
                for attempt in stamina.retry_context(on=_retryable, attempts=4, timeout=300, wait_initial=2, wait_max=60):
                    with attempt:
                        self._wait_polite(host, rate)
                        try:
                            resp = client.request(target.method, target.url, content=body, headers=headers)
                        finally:
                            self._last[host] = self._clock()
                        if resp.status_code in RETRY_STATUSES:
                            ra = _retry_after(resp)
                            if ra:
                                self._sleep(min(ra, MAX_RETRY_AFTER_S))
                            raise RetryableStatus(resp.status_code)
            except (RetryableStatus, httpx.HTTPError) as exc:
                self._fail(host)
                raise FetchError(f"{target.url}: {exc}") from exc
        if resp.status_code >= 400 and resp.status_code != 404:
            self._fail(host)
            raise FetchError(f"{target.url}: HTTP {resp.status_code}")
        self._failures[host] = 0
        ctype = resp.headers.get("content-type", "")
        is_text = resp.status_code == 200 and any(ctype.startswith(t) or t in ctype for t in _TEXT_TYPES)
        snap = Snapshot(
            target=target, url=str(resp.url), status=resp.status_code, headers=dict(resp.headers), body=resp.content,
            text=decode_body(resp.content, ctype) if is_text else None, fetched_at=datetime.now(UTC),
            sha256=hashlib.sha256(resp.content).hexdigest(),
        )
        if self.warc is not None:
            self.warc.write(snap, dict(resp.request.headers), body or b"")
        return snap

    def close(self) -> None:
        self._nknu.close()
        if self._other is not self._nknu:
            self._other.close()
