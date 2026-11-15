"""Periodic updates, stage 2 (design docs/superpowers/specs/2026-11-16-periodic-update-design.md §7.4, §7.5; plan
docs/superpowers/plans/2026-11-16-update-stage-2.md): what a night's run changed.

- fingerprint(html, url): a page's own text, as two readings of an unchanged page give it: the platform's content
  area (profiles.content_css), else the body without its header, nav, footer and aside; scripts and styles out; NFKC;
  white space taken out of each line (a space more or less is no change); a line that holds only a number dropped (a read or download counter: on the lab's 508 pages
  answered twice, 503 read the same and the 4 that differ are lists with new notices: research/crawler_design_2026-
  11-16/fingerprint2_1116.out) 【Claude, from the evidence】. Digits inside a line are never masked: a deadline moved
  is digits only.
- night(frontier, root, since, now): each row fetched since the night started (since: put-back's time) against the
  answer it held before (the first url_version copy kept tonight, stage 0a): new (a first answer), changed or same
  (a page by its fingerprint, a file by its sha256), not_seen (no good answer this time: kept, never a deletion),
  deleted (gone, in the school's words or a 404/410, twice at least 24 h apart: design §7.5; a word in the report,
  the archive keeps every answer), back (a good answer after a bad one). page_watch keeps each page's last
  fingerprint, when it last changed and how many nights in a row, and since when it answers gone. A site whose
  re-fetched rows turned bad by ALARM_SHARE or more (ALARM_MIN rows at least), or whose changed pages answer one
  identical body on SAME_BODY_MIN pages or more (a maintenance page), is an alarm: its export waits for a person
  (design §7.5) 【Claude's thresholds】.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import postback, profiles
from nknu.archive.frontier import Frontier

_DROP = "script, style, noscript, template"
_TEMPLATE = "header, nav, footer, aside"
_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"[\d,.\s]+")


def text_lines(html: str, url: str) -> list[str]:
    """The lines of a page's own text (the module's docstring)."""
    tree = LexborHTMLParser(html)
    for node in tree.css(_DROP):
        node.decompose()
    css = profiles.content_css(url)
    nodes = tree.css(css) if css else []
    if not nodes:
        for node in tree.css(_TEMPLATE):
            node.decompose()
        nodes = [tree.body] if tree.body is not None else []
    out: list[str] = []
    for node in nodes:
        for raw in (node.text(separator="\n") or "").split("\n"):
            line = _WS.sub("", unicodedata.normalize("NFKC", raw))
            if line and not _NUMBER.fullmatch(line):
                out.append(line)
    return out


def fingerprint(html: str, url: str) -> str:
    return hashlib.sha256("\n".join(text_lines(html, url)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the night's review

BAD_FLAGS = frozenset({"login", "soft404", "gone_page", "gone_id", "gone_redirect", "error_page", "empty_detail",
                       "bad_link"})
GONE_FLAGS = frozenset({"soft404", "gone_page", "gone_id", "gone_redirect"})
GONE_AGAIN_S = 24 * 3600  # a second gone answer at least this long after the first is the school's deletion
ALARM_SHARE, ALARM_MIN, SAME_BODY_MIN = 0.3, 5, 5
CHANGES = ("new", "changed", "same", "not_seen", "not_got", "deleted", "back")


@dataclass(frozen=True)
class Change:
    url_id: int
    site: str
    url: str
    kind: str
    change: str  # one of CHANGES
    previous_at: str | None  # when the answer it is judged against was fetched


@dataclass
class Night:
    rows: list[Change] = field(default_factory=list)
    alarms: dict[str, str] = field(default_factory=dict)  # site -> why
    sites_changed: list[str] = field(default_factory=list)  # sites with news for the export, alarms left out

    def counts(self) -> Counter:
        return Counter(c.change for c in self.rows)


def _flags(r) -> set[str]:
    return set((r["flags"] or "").split(",")) - {""}


def good(r) -> bool:
    """An answer stored well: 200, no sign-in, not-found or error page, a page that is HTML and a file that is not."""
    if r["status"] != 200 or r["warc"] is None or r["warc_offset"] is None or _flags(r) & BAD_FLAGS:
        return False
    return (r["sniff"] == "html") if r["kind"] == "page" else (r["sniff"] != "html")


def _gone(r) -> bool:
    return r["status"] in (404, 410) or bool(_flags(r) & GONE_FLAGS)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def _print(root: Path, r) -> str | None:
    """A page's fingerprint read back from the WARC (a file's sha256)."""
    if r["kind"] != "page":
        return r["sha256"]
    try:
        url, _status, headers, body = postback.read_response(root, r["warc"], r["warc_offset"])
    except (OSError, ValueError):
        return None
    return fingerprint(postback.as_text(body, headers.get("content-type")), url)


def night(frontier: Frontier, root: Path, since: float, *, now: float) -> Night:
    """The night's review (the module's docstring)."""
    since_iso = _iso(since)
    out = Night()
    stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "bad": 0, "bodies": Counter()})
    for r in frontier.q("SELECT * FROM url WHERE fetched_at >= ? AND state IN ('done', 'failed') ORDER BY id",
                        (since_iso,)):
        got = frontier.q("SELECT * FROM url_version WHERE url_id=? AND kept_at >= ? ORDER BY kept_at, id LIMIT 1",
                         (r["id"], since))
        prev = got[0] if got else None
        seen = frontier.q("SELECT * FROM page_watch WHERE url_id=?", (r["id"],))
        w = seen[0] if seen else None
        site = r["owner_site"] or r["site"]
        now_good = r["state"] == "done" and good(r)
        fp_now = _print(root, r) if now_good else None
        gone_now = r["state"] == "done" and not now_good and _gone(r)
        gone = (w["gone"] if w else 0) + 1 if gone_now else 0
        gone_at = ((w["gone_at"] if w and w["gone_at"] else now) if gone_now else None)
        if prev is None:
            change = "new" if now_good else "not_got"
        elif now_good:
            if not good(prev):
                change = "back"
            else:
                change = "same" if fp_now == _print(root, prev) else "changed"
        elif gone_now and gone >= 2 and now - gone_at >= GONE_AGAIN_S:
            change = "deleted"
        else:
            change = "not_seen"
        if prev is not None and good(prev):
            s = stats[site]
            s["n"] += 1
            s["bad"] += int(not now_good)
            if change == "changed" and r["kind"] == "page":
                s["bodies"][r["sha256"]] += 1
        changes = ((w["changes"] if w else 0) + 1) if change == "changed" else (0 if change == "same" else
                                                                                 (w["changes"] if w else 0))
        frontier.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes, gone_at, gone) VALUES "
                   "(?,?,?,?,?,?,?) ON CONFLICT(url_id) DO UPDATE SET fp=coalesce(excluded.fp, fp), "
                   "checked_at=excluded.checked_at, changed_at=coalesce(excluded.changed_at, changed_at), "
                   "changes=excluded.changes, gone_at=excluded.gone_at, gone=excluded.gone",
                   (r["id"], fp_now, now, now if change in ("new", "changed", "back") else None, changes, gone_at,
                    gone))
        out.rows.append(Change(r["id"], site, r["url"], r["kind"], change, prev["fetched_at"] if prev else None))
    for site, s in stats.items():
        if s["n"] >= ALARM_MIN and s["bad"] / s["n"] >= ALARM_SHARE:
            out.alarms[site] = f"{s['bad']} of {s['n']} pages read again did not come back well"
        elif s["bodies"] and max(s["bodies"].values()) >= SAME_BODY_MIN:
            out.alarms[site] = f"{max(s['bodies'].values())} pages changed to one identical body"
    out.sites_changed = sorted({c.site for c in out.rows if c.change in ("new", "changed", "deleted", "back")}
                               - set(out.alarms))
    return out
