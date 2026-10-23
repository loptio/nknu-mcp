"""Adapter interface: one rule-based scraper per site, registered with @adapter. One failing adapter never stops others."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from nknu.core.records import Record
    from nknu.crawl.fetch import Client, Snapshot


@dataclass(frozen=True)
class RateLimit:
    per_host_concurrency: int = 1
    min_interval_s: float = 2.0
    jitter_s: float = 3.0  # each request waits min_interval_s + uniform(0, jitter_s): 2-5 s


@dataclass(frozen=True)
class Target:
    url: str
    method: str = "GET"
    data: list[tuple[str, str]] | None = None  # form fields, order and duplicates kept (WebForms postbacks)
    meta: dict = field(default_factory=dict)


class ParseError(Exception):
    """The page no longer looks like what the adapter expects: this run is void, old values stay."""


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


@dataclass(frozen=True)
class RunHistory:
    counts_7d: list[int]
    last_count: int | None


@dataclass
class RunContext:
    client: Client
    full_scan: bool
    today: date
    conn: sqlite3.Connection  # facts.db, read only for adapters
    known_keys: set[str]
    rate: RateLimit

    def fetch(self, target: Target) -> Snapshot:
        return self.client.fetch(target, self.rate)


class Adapter:
    name: ClassVar[str] = ""
    hosts: ClassVar[list[str]] = []
    rate_limit: ClassVar[RateLimit] = RateLimit()
    required_selectors: ClassVar[list[str]] = []  # per-target override: Target.meta["selectors"]
    pages_are_html: ClassVar[bool] = True

    def discover(self, ctx: RunContext) -> list[Target]:
        return []

    def fetch_all(self, ctx: RunContext) -> list[Snapshot]:
        return [ctx.fetch(t) for t in self.discover(ctx)]

    def parse(self, snap: Snapshot) -> list[Record]:
        """Pure: same snapshot -> same records. No network, no clock."""
        raise NotImplementedError

    def invariants(self, records: list[Record], history: RunHistory, snaps: list[Snapshot]) -> list[Violation]:
        from nknu.crawl.invariants import standard

        return standard(self, records, history, snaps)

    def counts_missing(self, key: str, kind: str, span: tuple[date, date] | None, records: list[Record], today: date) -> bool:
        """C2: whether this full-scan run had the chance to produce `key` (kind fact/event/doc; span = its current dates),
        so that its absence counts towards retirement. `records` are this run's parsed records, detail pages unmerged.
        Facts printed on a table page never get here: they expire on their own date."""
        return True


REGISTRY: dict[str, type[Adapter]] = {}


def adapter(name: str, hosts: list[str], rate_limit: RateLimit | None = None):
    def register(cls: type[Adapter]) -> type[Adapter]:
        cls.name, cls.hosts, cls.rate_limit = name, list(hosts), rate_limit or RateLimit()
        REGISTRY[name] = cls
        return cls

    return register


def load_adapters() -> dict[str, type[Adapter]]:
    import importlib
    import pkgutil

    import nknu.crawl.adapters as pkg

    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{mod.name}")
    return REGISTRY
