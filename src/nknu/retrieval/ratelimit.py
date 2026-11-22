"""Request limits for the public endpoint (spec §8): 60 requests a minute per client, and one budget of 600 a minute
shared by Anthropic's egress range (160.79.104.0/21), whose many users arrive from few addresses. An IPv4 address is a
client; for IPv6 the client is the address's /64 (one user's network, in which any address can be picked).

With a Cloudflare tunnel (NKNU_TRUST_CF_HEADER=1, the default) the client IP is cloudflared's CF-Connecting-IP header,
trusted only when the TCP peer is loopback (cloudflared runs on this machine and connects from 127.0.0.1). Any other
peer is its own client, so a header forged from outside changes nothing. uvicorn runs with proxy_headers=False for the
same reason: the peer must stay the real TCP peer.

Behind Tailscale Funnel (NKNU_TRUST_CF_HEADER=0) every request arrives from loopback and nobody sets that header, so a
value there was written by the client: it is ignored, and all loopback traffic shares one budget of the shared size."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from ipaddress import IPv6Network, ip_address, ip_network

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CF_HEADER = "cf-connecting-ip"
PER_IP, SHARED = 60, 600
ANTHROPIC = ("160.79.104.0/21",)
V6_PREFIX = 64


def _addr(s: str):
    try:
        return ip_address(s.strip())
    except ValueError:
        return None


class RateLimiter:
    """Sliding window: a key keeps the times of its hits inside the last window_s seconds."""

    def __init__(self, per_ip: int = PER_IP, shared: int = SHARED, window_s: float = 60.0,
                 shared_cidrs: Sequence[str] = ANTHROPIC, clock: Callable[[], float] = time.monotonic,
                 shared_loopback: bool = False):
        """shared_loopback: every loopback client shares one budget of `shared` (Tailscale Funnel: the whole school
        arrives from 127.0.0.1)."""
        self.per_ip, self.shared, self.window_s, self.clock = per_ip, shared, window_s, clock
        self.shared_loopback = shared_loopback
        self._nets = tuple(ip_network(c) for c in shared_cidrs)
        self._hits: dict[str, deque[float]] = {}
        self._swept = clock()
        self._lock = threading.Lock()

    def _key(self, ip: str) -> tuple[str, int]:
        addr = _addr(ip)
        if addr is None:
            return ip, self.per_ip
        if addr.version == 6 and addr.ipv4_mapped is not None:  # ::ffff:1.2.3.4 is the IPv4 client 1.2.3.4
            addr = addr.ipv4_mapped
        if self.shared_loopback and addr.is_loopback:
            return "shared:loopback", self.shared
        for net in self._nets:
            if addr.version == net.version and addr in net:
                return f"shared:{net}", self.shared
        if addr.version == 6:
            return str(IPv6Network((int(addr) >> (128 - V6_PREFIX) << (128 - V6_PREFIX), V6_PREFIX))), self.per_ip
        return addr.compressed, self.per_ip

    def _sweep(self, now: float) -> None:
        """Forget clients whose last hit has left the window (memory stays bounded by the clients of one window)."""
        horizon = now - self.window_s
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= horizon]:
            del self._hits[key]
        self._swept = now

    def allow(self, ip: str) -> tuple[bool, int]:
        """(allowed, retry_after seconds; 0 when allowed). A refused request is not counted."""
        now = self.clock()
        key, limit = self._key(ip)
        with self._lock:
            if now - self._swept >= self.window_s:
                self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, math.ceil(hits[0] + self.window_s - now))
            hits.append(now)
            return True, 0


def client_ip(scope: Scope, *, trust_cf_header: bool = True) -> str:
    """CF-Connecting-IP when it is trusted, the peer is loopback (cloudflared) and the header holds an address; else
    the peer."""
    peer = (scope.get("client") or ("unknown", 0))[0]
    addr = _addr(peer)
    if trust_cf_header and addr is not None and addr.is_loopback:
        cf = Headers(scope=scope).get(CF_HEADER, "")
        if _addr(cf) is not None:
            return cf.strip()
    return peer


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: RateLimiter, *, trust_cf_header: bool = True):
        self.app, self.limiter, self.trust_cf_header = app, limiter, trust_cf_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ok, retry_after = self.limiter.allow(client_ip(scope, trust_cf_header=self.trust_cf_header))
        if not ok:
            response = JSONResponse({"error": "rate_limited", "retry_after": retry_after}, status_code=429,
                                    headers={"Retry-After": str(retry_after)})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
