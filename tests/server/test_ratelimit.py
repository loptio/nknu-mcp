import httpx
import pytest
from starlette.responses import JSONResponse

from nknu.server.ratelimit import RateLimiter, RateLimitMiddleware


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def fake():
    return FakeClock()


def test_rate_limit_per_ip(fake):
    rl = RateLimiter(clock=fake)
    assert all(rl.allow("1.2.3.4")[0] for _ in range(60)) and not rl.allow("1.2.3.4")[0]
    assert rl.allow("5.6.7.8")[0]  # another client has its own budget


def test_rate_limit_shared_anthropic(fake):
    rl = RateLimiter(clock=fake)
    ok = sum(rl.allow(f"160.79.{104 + i % 8}.{i % 250}")[0] for i in range(700))
    assert ok == 600
    assert rl.allow("160.79.103.1")[0] and rl.allow("160.79.112.1")[0]  # just outside the /21: per-IP budgets


def test_shared_range_is_one_budget_not_per_ip(fake):
    rl = RateLimiter(clock=fake)
    assert sum(rl.allow("160.79.104.1")[0] for _ in range(100)) == 100  # one egress address may exceed the per-IP 60


def test_window_slides_and_retry_after(fake):
    rl = RateLimiter(per_ip=2, clock=fake)
    assert rl.allow("1.2.3.4") == (True, 0)
    fake.t += 10
    assert rl.allow("1.2.3.4") == (True, 0)
    fake.t += 5
    assert rl.allow("1.2.3.4") == (False, 45)  # the first hit leaves the window 60 s after it was made
    fake.t += 45
    assert rl.allow("1.2.3.4")[0]


def test_retry_after_at_least_one_second(fake):
    rl = RateLimiter(per_ip=1, clock=fake)
    rl.allow("1.2.3.4")
    fake.t += 59.9
    assert rl.allow("1.2.3.4") == (False, 1)


def test_idle_clients_are_forgotten(fake):
    rl = RateLimiter(clock=fake)
    for i in range(500):
        rl.allow(f"10.0.{i // 250}.{i % 250}")
    fake.t += 61
    rl.allow("1.2.3.4")
    assert len(rl._hits) == 1


def test_ipv6_and_odd_addresses(fake):
    rl = RateLimiter(per_ip=1, clock=fake)
    assert rl.allow("2001:db8::1")[0] and not rl.allow("2001:0db8:0000::1")[0]  # same address, two spellings
    assert rl.allow("unknown")[0] and not rl.allow("unknown")[0]


async def _ok(scope, receive, send):
    await JSONResponse({"ok": True})(scope, receive, send)


async def _statuses(peer: tuple[str, int], cf_ips: list[str | None], limiter: RateLimiter) -> list[httpx.Response]:
    app = RateLimitMiddleware(_ok, limiter)
    transport = httpx.ASGITransport(app=app, client=peer)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        return [await c.get("/", headers={"cf-connecting-ip": ip} if ip else {}) for ip in cf_ips]


async def test_cf_ip_trusted_only_from_loopback(fake):
    # a peer that is not loopback is its own client, whatever CF-Connecting-IP it forges
    rs = await _statuses(("203.0.113.7", 5000), ["1.1.1.1", "2.2.2.2", "3.3.3.3"], RateLimiter(per_ip=2, clock=fake))
    assert [r.status_code for r in rs] == [200, 200, 429]
    # cloudflared connects from loopback: each CF-Connecting-IP is a client of its own
    rs = await _statuses(("127.0.0.1", 5000), ["1.1.1.1", "2.2.2.2", "3.3.3.3"], RateLimiter(per_ip=2, clock=fake))
    assert [r.status_code for r in rs] == [200, 200, 200]
    rs = await _statuses(("::1", 5000), ["1.1.1.1"] * 3, RateLimiter(per_ip=2, clock=fake))
    assert [r.status_code for r in rs] == [200, 200, 429]


async def test_loopback_without_header_or_with_garbage_is_the_peer(fake):
    rs = await _statuses(("127.0.0.1", 5000), [None, "not-an-ip", None], RateLimiter(per_ip=2, clock=fake))
    assert [r.status_code for r in rs] == [200, 200, 429]


async def test_429_body_and_header(fake):
    rs = await _statuses(("203.0.113.7", 5000), [None, None], RateLimiter(per_ip=1, clock=fake))
    r = rs[-1]
    assert r.status_code == 429 and r.json() == {"error": "rate_limited", "retry_after": 60}
    assert r.headers["retry-after"] == "60"
