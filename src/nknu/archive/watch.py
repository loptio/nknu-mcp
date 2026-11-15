"""Periodic updates, stage 2 (design docs/superpowers/specs/2026-11-16-periodic-update-design.md §7.4, §7.5; plan
docs/superpowers/plans/2026-11-16-update-stage-2.md; the plan check wf_2249ba4d-a90 and its fix pass): what a night's
run changed.

- fingerprint(html, url): a page's own text, as two readings of an unchanged page give it: the platform's content
  area (profiles.content_css), else the body without its header, nav, footer and aside; scripts and styles out; the
  counters the page marks out (an eye icon's number, an element whose id or class names a counter, a number in the
  cell after a counter word, a line that is a counter word and a number); NFKC; white space taken out of each line.
  Every other number stays, alone in its cell or not: a deadline 115.11.20 moved, a fee or a quota changed (design
  §7.4: digits are never masked; the first rule, number-only lines dropped, lost dates written with dots: plan check)
  【Claude: the marks from the lab's 508 pages answered twice, research/crawler_design_2026-11-16/fingerprint*_1116】.
- night(frontier, root, since, now): each row fetched since the review's window opened (since: the start of the
  oldest night not reviewed) against the answer it held then: the first url_version copy kept since, else (its fetch
  then kept none: the answer was copied on a night that got no answer) its newest copy. A row whose pointer is still
  that answer was taken but not answered (no_answer: its page_watch left as it was). Otherwise new (a first answer),
  changed or same (a page by its fingerprint, a redirect by where it leads, a file by its sha256, a list page by its
  rows: list_row read first or changed since, never its text), not_seen (no good answer this time: kept, never a
  deletion), deleted (gone in outcome's sense, a 404/410 or a gone or looks-gone flag, twice at least 24 h apart:
  design §7.5; a word in the report, the archive keeps every answer), back (a good answer after a bad one). The
  presses of new rows done since (a detail or a download: a row's done press is never pressed again) are new rows of
  their site. page_watch keeps each page's last fingerprint, when it last changed and how many checks in a row, and
  since when (and how many times in a row) it answered gone. A site is an alarm when ALARM_MIN or more of its rows
  that were good turned bad and they are ALARM_SHARE or more of its re-fetched rows, or when SAME_BODY_MIN of its
  changed pages answer one body (a maintenance page; the host's known not-found page aside): its export waits, its
  page_watch is not written, and its re-fetched rows go back the next night (Night.again) 【Claude's thresholds】.
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

from nknu.archive import postback, profiles, scope
from nknu.archive.frontier import Frontier

_DROP = "script, style, noscript, template"
_TEMPLATE = "header, nav, footer, aside"
_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"[\d,.\s]+")


_COUNTER_ATTR = re.compile(r"counter|(?:read|click|hit|view|visit|browse|download)s?_?(?:count|num|times)", re.IGNORECASE)
_COUNTER_WORD = r"(?:觀看|瀏覽|點閱|點擊|點選|閱讀|人氣|下載)(?:次數|人次|數)?"
_COUNTER_LINE = re.compile(rf"{_COUNTER_WORD}[:]?\d[\d,]*(?:次|人次)?")
_COUNTER_LABEL = re.compile(rf"{_COUNTER_WORD}[:]?")


def _plain(text: str | None) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def _prev_element(node):
    at = node.prev
    while at is not None and at.tag in ("-text", "-comment"):
        at = at.prev
    return at


def _drop_counters(tree) -> None:
    """The counters a page marks (the module's docstring), each only when it holds a number alone."""
    for icon in tree.css("i"):
        if "fa-eye" in (icon.attributes.get("class") or "") and icon.parent is not None \
                and _NUMBER.fullmatch(_plain(icon.parent.text()) or "-"):
            icon.parent.decompose()
    for node in tree.css("[id], [class]"):
        names = f"{node.attributes.get('id') or ''} {node.attributes.get('class') or ''}"
        if _COUNTER_ATTR.search(names) and _NUMBER.fullmatch(_plain(node.text()) or "-"):
            node.decompose()
    for node in tree.css("td, th, span, dd, div"):
        if node.parent is None or not _NUMBER.fullmatch(_plain(node.text(deep=True)) or "-"):
            continue
        before = _prev_element(node)
        if before is not None and _COUNTER_LABEL.fullmatch(_plain(before.text())):
            node.decompose()


def text_lines(html: str, url: str) -> list[str]:
    """The lines of a page's own text (the module's docstring)."""
    tree = LexborHTMLParser(html)
    for node in tree.css(_DROP):
        node.decompose()
    _drop_counters(tree)
    css = profiles.content_css(url)
    nodes = tree.css(css) if css else []
    if not nodes:
        for node in tree.css(_TEMPLATE):
            node.decompose()
        nodes = [tree.body] if tree.body is not None else []
    out: list[str] = []
    for node in nodes:
        for raw in (node.text(separator="\n") or "").split("\n"):
            line = _plain(raw)
            if line and not _COUNTER_LINE.fullmatch(line):
                out.append(line)
    return out


def content_links(html: str, url: str) -> list[str]:
    """The links of a page's own text (the area text_lines reads), as absolute addresses (speed-up rule 1: what a
    changed notice points to, not its menu)."""
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
        for a in node.css("a[href]"):
            got = scope.canonical(a.attributes.get("href") or "", url)
            if got:
                out.append(got)
    return list(dict.fromkeys(out))


def fingerprint(html: str, url: str) -> str:
    return hashlib.sha256("\n".join(text_lines(html, url)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the night's review

BAD_FLAGS = frozenset({"login", "soft404", "gone_page", "gone_id", "gone_redirect", "error_page", "empty_detail",
                       "bad_link"})
GONE_FLAGS = frozenset({"soft404", "gone_page", "gone_id", "gone_redirect"})
GONE_AGAIN_S = 24 * 3600  # a second gone answer at least this long after the first is the school's deletion
ALARM_SHARE, ALARM_MIN, SAME_BODY_MIN = 0.3, 5, 5
CHANGES = ("new", "changed", "same", "no_answer", "not_seen", "not_got", "deleted", "back")


@dataclass(frozen=True)
class Change:
    url_id: int  # a press's: minus its action id
    site: str
    url: str
    kind: str  # page, file, image; press:detail, press:download
    change: str  # one of CHANGES
    previous_at: str | None  # when the answer it is judged against was fetched


@dataclass
class Night:
    rows: list[Change] = field(default_factory=list)
    alarms: dict[str, str] = field(default_factory=dict)  # site -> why
    sites_changed: list[str] = field(default_factory=list)  # sites with news for the export, alarms left out
    again: list[int] = field(default_factory=list)  # an alarmed site's re-fetched rows: back the next night

    def counts(self) -> Counter:
        return Counter(c.change for c in self.rows)


def _flags(r) -> set[str]:
    return set((r["flags"] or "").split(",")) - {""}


def _redirect(r) -> bool:
    return (r["status"] is not None and 300 <= r["status"] < 400 and bool(r["final_url"])
            and not _flags(r) & BAD_FLAGS)


def good(r) -> bool:
    """An answer stored well: a redirect to somewhere (judged by where), or 200 with no sign-in, not-found or error
    page, a page that is HTML and a file that is not."""
    if r["warc"] is None or r["warc_offset"] is None or _flags(r) & BAD_FLAGS:
        return False
    if _redirect(r):
        return True
    if r["status"] != 200:
        return False
    return (r["sniff"] == "html") if r["kind"] == "page" else (r["sniff"] != "html")


def _gone(r) -> bool:
    return r["status"] in (404, 410) or bool(_flags(r) & GONE_FLAGS)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def _print(root: Path, r) -> str | None:
    """A page's fingerprint read back from the WARC (a redirect's target; a file's sha256)."""
    if _redirect(r):
        return f"redirect:{r['final_url']}"
    if r["kind"] != "page":
        return r["sha256"]
    try:
        url, _status, headers, body = postback.read_response(root, r["warc"], r["warc_offset"])
    except (OSError, ValueError):
        return None
    return fingerprint(postback.as_text(body, headers.get("content-type")), url)


def _before(frontier: Frontier, uid: int, since: float):
    """The answer a row held when the window opened (the module's docstring)."""
    got = frontier.q("SELECT * FROM url_version WHERE url_id=? AND kept_at >= ? ORDER BY kept_at, id LIMIT 1",
                     (uid, since))
    if not got:
        got = frontier.q("SELECT * FROM url_version WHERE url_id=? ORDER BY kept_at DESC, id DESC LIMIT 1", (uid,))
    return got[0] if got else None


def list_pages(frontier: Frontier) -> set[int]:
    """Every list page: walked by button (listwalk), read as a list (page_role), or in a GET pager group."""
    ids = {r[0] for r in frontier.q("SELECT list_id FROM listwalk")}
    ids |= {r[0] for r in frontier.q("SELECT id FROM url WHERE page_role = 'list'")}
    for r in frontier.q("SELECT page_id, res_id FROM page_res WHERE role = 'pager' AND how = 'get' AND res_id IS NOT "
                        "NULL"):
        ids.update((r[0], r[1]))
    return ids


def night(frontier: Frontier, root: Path, since: float, *, now: float) -> Night:
    """The night's review (the module's docstring)."""
    since_iso = _iso(since)
    out = Night()
    stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "bad": 0, "bodies": Counter()})
    lists = list_pages(frontier)
    probe = {r[0]: r[1] for r in frontier.q("SELECT host, probe_sha FROM host WHERE probe_sha IS NOT NULL")}
    watched: list[tuple] = []  # (site, prev, the page_watch values)
    for r in frontier.q("SELECT * FROM url WHERE fetched_at >= ? AND state IN ('done', 'failed') ORDER BY id",
                        (since_iso,)):
        prev = _before(frontier, r["id"], since)
        site = r["owner_site"] or r["site"]
        if prev is not None and (prev["warc"], prev["warc_offset"]) == (r["warc"], r["warc_offset"]):
            out.rows.append(Change(r["id"], site, r["url"], r["kind"], "no_answer", prev["fetched_at"]))
            if good(prev):
                stats[site]["n"] += 1
                stats[site]["bad"] += 1
            continue
        seen = frontier.q("SELECT * FROM page_watch WHERE url_id=?", (r["id"],))
        w = seen[0] if seen else None
        now_good = r["state"] == "done" and good(r)
        is_list = r["id"] in lists and r["kind"] == "page" and not _redirect(r)
        fp_now = None if is_list or not now_good else _print(root, r)
        gone_now = r["state"] == "done" and not now_good and _gone(r)
        gone = (w["gone"] if w else 0) + 1 if gone_now else 0
        gone_at = ((w["gone_at"] if w and w["gone_at"] else now) if gone_now else None)
        if prev is None:
            change = "new" if now_good else "not_got"
        elif now_good:
            if not good(prev):
                change = "back"
            elif is_list:
                news = frontier.q("SELECT count(*) FROM list_row WHERE list_id=? AND (first_seen >= ? OR changed_at "
                                  ">= ?)", (r["id"], since, since))[0][0]
                change = "changed" if news else "same"
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
            if change == "changed" and r["kind"] == "page" and not is_list and r["sha256"] != probe.get(r["host"]):
                s["bodies"][r["sha256"]] += 1
        changes = ((w["changes"] if w else 0) + 1) if change == "changed" else (0 if change == "same" else
                                                                                 (w["changes"] if w else 0))
        watched.append((site, prev, (r["id"], fp_now, now, now if change in ("new", "changed", "back") else None,
                                     changes, gone_at, gone)))
        out.rows.append(Change(r["id"], site, r["url"], r["kind"], change, prev["fetched_at"] if prev else None))
    for a in frontier.q("SELECT a.id, a.kind, a.row_key, u.url, coalesce(u.owner_site, u.site) AS site FROM action a "
                        "JOIN url u ON u.id = a.list_id WHERE a.state = 'done' AND a.kind IN ('detail', 'download') "
                        "AND a.done >= ? ORDER BY a.id", (since,)):
        out.rows.append(Change(-a["id"], a["site"], f"{a['url']}#{a['kind']}:{a['row_key']}", f"press:{a['kind']}",
                               "new", None))
    for site, s in stats.items():
        if s["bad"] >= ALARM_MIN and s["bad"] / s["n"] >= ALARM_SHARE:
            out.alarms[site] = f"{s['bad']} of {s['n']} pages read again did not come back well"
        elif s["bodies"] and max(s["bodies"].values()) >= SAME_BODY_MIN:
            out.alarms[site] = f"{max(s['bodies'].values())} pages changed to one identical body"
    for site, prev, values in watched:
        if site in out.alarms:
            if prev is not None:
                out.again.append(values[0])
            continue
        frontier.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes, gone_at, gone) VALUES "
                   "(?,?,?,?,?,?,?) ON CONFLICT(url_id) DO UPDATE SET fp=coalesce(excluded.fp, fp), "
                   "checked_at=excluded.checked_at, changed_at=coalesce(excluded.changed_at, changed_at), "
                   "changes=excluded.changes, gone_at=excluded.gone_at, gone=excluded.gone", values)
    out.again += [c.url_id for c in out.rows if c.change == "no_answer" and c.site in out.alarms]
    out.sites_changed = sorted({c.site for c in out.rows if c.change in ("new", "changed", "deleted", "back")}
                               - set(out.alarms))
    return out
