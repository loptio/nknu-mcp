"""robots.txt per RFC 9309 (design §3.3).

- A 4xx means no rules; a 5xx, a timeout or no answer means everything is disallowed until a later try.
- Redirects are followed up to five hops while they stay on the same queue. A redirect to another server could only be
  followed by opening a second connection to that server, so it counts as "unavailable", which RFC 9309 §2.3.1.2 and
  §2.3.1.3 let a crawler read as no rules; so does a sixth redirect. Either way an event says so.
- The body is read as UTF-8 (RFC 9309 §2.3), whatever the Content-Type says, bad bytes replaced; the directives are
  ASCII. A 200 body with no directive in it (an HTML error page) is logged and read as no rules.
- At least 512 KiB is kept (RFC 9309 §2.5); each body is parsed once (Protego, Scrapy's parser, BSD-3).
- Kept 24 hours; a failed refresh keeps the last good copy.
- On a case-insensitive (IIS) server, where canonical() lowercases the path, rules and URL are compared lowercased
  (ASCII): `Disallow: /ACA/Private/` then covers /aca/private/ too, as it does on that server. Robots.allows() is the
  one check the crawler and the monitor both use.
- Crawl-delay is honoured up to 300 s (Heritrix's default ceiling); a larger value is read as 300 s.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from protego import Protego

from nknu.archive.scope import is_iis

AGENT = "nknu-facts-bot"
FRESH_S = 24 * 3600
RETRY_UNREACHABLE_S = 3600
MAX_BODY = 512 * 1024
MAX_CRAWL_DELAY_S = 300.0
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_DIRECTIVE = re.compile(r"(?im)^\s*(?:user-agent|allow|disallow|crawl-delay|sitemap)\s*:")


@lru_cache(maxsize=512)
def _parsed(body: str) -> Protego:
    return Protego.parse(body)


@lru_cache(maxsize=512)
def _parsed_folded(body: str) -> Protego:
    """The rules for a case-insensitive server: every path lowercased (directive names and user agents are
    case-insensitive anyway, and the rest of the body does not take part in matching)."""
    return Protego.parse(body.translate(_ASCII_LOWER))


def _case_insensitive(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return is_iis((parts.hostname or "").lower(), parts.path)
    except ValueError:
        return False


@dataclass(frozen=True)
class Robots:
    # rules | none (4xx, or unavailable) | unreachable (5xx, timeout: disallows everything) | wait (nothing was asked:
    # the server said come back later, or the request never left; disallows everything and counts as no failure)
    state: str
    body: str = ""

    def allows(self, url: str) -> bool:
        if self.state == "none":
            return True
        if self.state != "rules":
            return False
        if _case_insensitive(url):
            return _parsed_folded(self.body).can_fetch(url.translate(_ASCII_LOWER), AGENT)
        return _parsed(self.body).can_fetch(url, AGENT)

    def crawl_delay(self) -> float | None:
        if self.state != "rules":
            return None
        d = _parsed(self.body).crawl_delay(AGENT)
        return min(float(d), MAX_CRAWL_DELAY_S) if d else None

    @property
    def sha(self) -> str:
        """What a request was allowed by, for the audit: the rules' digest, or the state when there are no rules."""
        return hashlib.sha256(self.body.encode()).hexdigest()[:16] if self.state == "rules" else self.state


def decode(body: bytes) -> str:
    return body[:MAX_BODY].decode("utf-8", errors="replace").removeprefix("﻿")


def unparsed(text: str | None) -> bool:
    """A non-empty body without a single directive: an HTML page answering for a missing robots.txt."""
    return bool(text and text.strip()) and not _DIRECTIVE.search(text)


def judge_answer(status: int | None, text: str | None) -> Robots:
    """What one final robots.txt answer means (status None: the request failed)."""
    if status is None or status >= 500:
        return Robots("unreachable")
    if 400 <= status < 500:
        return Robots("none")
    if 200 <= status < 300:
        return Robots("rules", (text or "")[:MAX_BODY])
    return Robots("none")  # a redirect not followed: unavailable, which RFC 9309 reads as no rules
