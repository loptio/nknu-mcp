import httpx
import pytest
from starlette.responses import JSONResponse

from nknu.server.ratelimit import RateLimiter, RateLimitMiddleware, client_ip


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


def test_ipv6_clients_share_their_64(fake):
    # an IPv6 user gets a whole /64 and can pick a new address per request: the /64 is the client
    rl = RateLimiter(per_ip=2, clock=fake)
    assert rl.allow("2001:db8:0:1::5")[0] and rl.allow("2001:db8:0:1:ffff:ffff:ffff:9")[0]
    assert not rl.allow("2001:db8:0:1::abcd")[0]
    assert rl.allow("2001:db8:0:2::5")[0]  # the next /64 is another client


def test_ipv4_mapped_ipv6_is_the_ipv4_client(fake):
    rl = RateLimiter(per_ip=1, clock=fake)
    assert rl.allow("::ffff:1.2.3.4")[0] and not rl.allow("1.2.3.4")[0]
    rl = RateLimiter(clock=fake)
    assert sum(rl.allow("::ffff:160.79.104.9")[0] for _ in range(100)) == 100  # still Anthropic's shared budget


def test_shared_loopback_budget(fake):
    rl = RateLimiter(per_ip=1, shared=3, clock=fake, shared_loopback=True)
    assert [rl.allow(ip)[0] for ip in ("127.0.0.1", "::1", "127.0.0.2", "127.0.0.1")] == [True, True, True, False]
    assert rl.allow("1.2.3.4")[0] and not rl.allow("1.2.3.4")[0]  # other peers keep their per-IP budget


async def _ok(scope, receive, send):
    await JSONResponse({"ok": True})(scope, receive, send)


async def _statuses(peer: tuple[str, int], cf_ips: list[str | None], limiter: RateLimiter,
                    trust_cf_header: bool = True) -> list[httpx.Response]:
    app = RateLimitMiddleware(_ok, limiter, trust_cf_header=trust_cf_header)
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


async def test_cf_ip_ignored_when_not_trusted(fake):
    # Tailscale Funnel: every request arrives from loopback and nobody sets CF-Connecting-IP, so a value there was
    # written by the client: it is ignored, and all loopback traffic shares one budget of the shared size
    limiter = RateLimiter(per_ip=2, shared=3, clock=fake, shared_loopback=True)
    rs = await _statuses(("127.0.0.1", 5000), ["1.1.1.1", "160.79.104.1", None, "2.2.2.2"], limiter, trust_cf_header=False)
    assert [r.status_code for r in rs] == [200, 200, 200, 429]
    scope = {"type": "http", "client": ("127.0.0.1", 5000), "headers": [(b"cf-connecting-ip", b"1.1.1.1")]}
    assert client_ip(scope) == "1.1.1.1" and client_ip(scope, trust_cf_header=False) == "127.0.0.1"


async def test_loopback_without_header_or_with_garbage_is_the_peer(fake):
    rs = await _statuses(("127.0.0.1", 5000), [None, "not-an-ip", None], RateLimiter(per_ip=2, clock=fake))
    assert [r.status_code for r in rs] == [200, 200, 429]


async def test_429_body_and_header(fake):
    rs = await _statuses(("203.0.113.7", 5000), [None, None], RateLimiter(per_ip=1, clock=fake))
    r = rs[-1]
    assert r.status_code == 429 and r.json() == {"error": "rate_limited", "retry_after": 60}
    assert r.headers["retry-after"] == "60"
