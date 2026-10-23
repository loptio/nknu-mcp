import hashlib
from datetime import UTC, datetime
from pathlib import Path

from nknu.crawl.adapter import RunHistory, Target
from nknu.crawl.fetch import Snapshot

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FETCHED_AT = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)


def read(adapter: str, name: str) -> str:
    return (FIXTURES / adapter / name).read_text()


def snap(url: str, text: str | None, status: int = 200, meta: dict | None = None, body: bytes | None = None) -> Snapshot:
    body = body if body is not None else (text or "").encode()
    return Snapshot(Target(url, meta=meta or {}), url, status, {"content-type": "text/html; charset=utf-8"}, body, text,
                    FETCHED_AT, hashlib.sha256(body).hexdigest())


NO_HISTORY = RunHistory([], None)


class FakeClient:
    """Stands in for fetch.Client: route(target) returns the page text (or a Snapshot, or an exception to raise)."""

    def __init__(self, route):
        self.route, self.requests = route, []

    def fetch(self, target, rate):
        self.requests.append(target)
        out = self.route(target)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, Snapshot):
            return out
        return Snapshot(target, target.url, 200, {"content-type": "text/html; charset=utf-8"}, out.encode(), out,
                        FETCHED_AT, hashlib.sha256(out.encode()).hexdigest())
