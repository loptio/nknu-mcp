"""What one request is (Target) and how fast a host may be asked (RateLimit), as fetch.Client takes them."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RateLimit:
    per_host_concurrency: int = 1
    min_interval_s: float = 2.0
    jitter_s: float = 3.0  # each request waits min_interval_s + uniform(0, jitter_s): 2-5 s


# Headers a Target may not set: the bot's own identity (its User-Agent names the project), and the ones that carry a
# secret. A secret is named by Target.credential and read only when the request is made, so it never sits in a Target,
# whose repr can reach a log line or an error message.
RESERVED_HEADERS = frozenset({"user-agent", "x-goog-api-key", "authorization", "proxy-authorization"})


@dataclass(frozen=True)
class Target:
    url: str
    method: str = "GET"
    data: list[tuple[str, str]] | None = None  # form fields, order and duplicates kept (WebForms postbacks)
    meta: dict = field(default_factory=dict)
    credential: str | None = None  # the name of a secret the client sends as a header (fetch.CREDENTIALS), never the secret
    headers: dict[str, str] = field(default_factory=dict)  # extra request headers, e.g. Drive's X-Goog-Drive-Resource-Keys
    # a body sent as it is (its Content-Type in headers), instead of data's form fields: a page's own script's JSON
    # POST to its data address (archive round-1 design §23 C1)
    body: bytes | None = None

    def __post_init__(self) -> None:
        if reserved := sorted(k.lower() for k in self.headers if k.lower() in RESERVED_HEADERS):
            raise ValueError(f"{self.url}: a Target may not set the header {', '.join(reserved)}")
