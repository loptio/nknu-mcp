"""Adapter interface: one rule-based scraper per site, registered with @adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


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
