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
  rows: list_row read first or changed since, and by its own text, list_print: outside its grid, or whole when its
  rows say nothing; never the rows' own text), not_seen (no good answer this time: kept, never a
  deletion), deleted (gone in outcome's sense, a 404/410 or a gone or looks-gone flag, twice at least 24 h apart:
  design §7.5; a word in the report, the archive keeps every answer), back (a good answer after a bad one). The
  presses of new rows done since (a detail or a download: a row's done press is never pressed again) are new rows of
  their site. page_watch keeps each page's last fingerprint, when it last changed and how many checks in a row, and
  since when (and how many times in a row) it answered gone. A site is an alarm when ALARM_MIN or more of its rows
  that were good turned bad and they are ALARM_SHARE or more of its re-fetched rows, or when SAME_BODY_MIN of its
  changed pages that answered different bodies before answer one body now (a maintenance page; the host's known
  not-found page aside; one page at several addresses changing together counts once, live 11-25): its export waits, its
  page_watch is not written, and its re-fetched rows go back the next night (Night.again) 【Claude's thresholds】.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import outcome, postback, profiles, scope
from nknu.archive.frontier import Frontier

_DROP = "script, style, noscript, template"
_TEMPLATE = "header, nav, footer, aside"
_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"[\d,.\s]+")


_COUNTER_ATTR = re.compile(r"counter|(?:read|click|hit|view|visit|browse|download)s?_?(?:count|num|times)", re.IGNORECASE)
_COUNTER_WORD = r"(?:(?:觀看|瀏覽|點閱|點擊|點選|閱讀|人氣|下載)(?:次數|人次|數)?|(?i:views?|hits?|clicks?|reads?))"
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


def list_print(html: str, url: str, site: str | None = None) -> str | None:
    """A list page's own text as a fingerprint (the first real night, 11-16: a teacher's page, teachercorner.aspx, is a
    list of one file row with the teacher's fields outside its grid, and a teacher list's rows hold no title, date or
    category; neither could say it changed, wf_9c6e1448-2cc; the owner: 「先修这三件」): the content area without the
    platform's grid (its rows are list_row's to judge: their counters and their order never count), or with it when
    every row the platform reads says nothing (no title, date or category). None when no platform reads its rows: it
    is judged by its rows alone. Measured on the lab's 194 list pages answered twice: rows the same, text the same on
    every one; on all 2,969 list pages no counter left in the text (research/crawler_lab_2026-11-04/night_1116/
    listprint_1116.out, listcensus_1116.out)."""
    prof = profiles.profile_for(url)
    if prof is None:
        return None
    try:
        pr = postback.read_page(prof, url, html, site=site or scope.site_key(url), list_url=url)
    except Exception:  # noqa: BLE001 - a page its reader cannot take is judged by its rows, never the night's end
        return None
    rows = next((r for r in prof.rows if r.name == pr.rowset), None)
    if rows is None:
        return None
    if pr.rows and all(not (r.title or r.date or r.category) for r in pr.rows):
        lines = text_lines(html, url)
    else:
        tree = LexborHTMLParser(html)
        for node in tree.css(rows.grid):
            node.decompose()
        lines = text_lines(tree.html or "", url)
    return "list:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the night's review

BAD_FLAGS = frozenset({"login", "soft404", "gone_page", "gone_id", "gone_redirect", "error_page", "empty_detail",
                       "bad_link"})
# an emptied notice (empty_detail: how c.nknu shows one the school deleted, the rule of 11-09) is gone too, so that
# twice a day apart it is the school's deletion (the export batch's review, F4)
GONE_FLAGS = frozenset({"soft404", "gone_page", "gone_id", "gone_redirect", "empty_detail"})
GONE_AGAIN_S = 24 * 3600  # a second gone answer at least this long after the first is the school's deletion
ALARM_SHARE, ALARM_MIN, SAME_BODY_MIN = 0.3, 5, 5
CHANGES = ("new", "changed", "same", "no_answer", "not_seen", "not_got", "deleted", "still_gone", "back")
HELD_KEY = "update:held"  # an alarmed site's rows: {"since": the alarm night's window start, "ids": [...]}


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
    """An answer that sends the reader on: a 3xx, or a page moving by script or refresh (js_redirect), to an address."""
    moves = (r["status"] is not None and 300 <= r["status"] < 400) or "js_redirect" in _flags(r)
    # a url_version copy holds no address of its own (sqlite3.Row: `in` would look at the values)
    own = r["url"] if "url" in r.keys() else None  # noqa: SIM118
    return moves and bool(r["final_url"]) and r["final_url"] != own and not _flags(r) & BAD_FLAGS


def _dead_end(r) -> bool:
    """An answer that sends the reader to its host's root page or to a school not-found page (outcome.not_found_url):
    how schools delete a notice (tecs: 130 rows to nodata.html in round one), never a page moved. A root page sent to
    a root page (http to https) is no such answer (the export batch's review, F1)."""
    moves = (r["status"] is not None and 300 <= r["status"] < 400) or "js_redirect" in _flags(r)
    own = r["url"] if "url" in r.keys() else None  # noqa: SIM118
    to = r["final_url"]
    return (moves and bool(to) and to != own and not outcome.school_home(own)
            and (outcome.school_home(to) or outcome.not_found_url(to)))


def good(r) -> bool:
    """An answer stored well: a redirect to somewhere (judged by where), or 200 with no sign-in, not-found or error
    page, a page that is HTML and a file that is not."""
    if r["warc"] is None or r["warc_offset"] is None or _flags(r) & BAD_FLAGS:
        return False
    if _redirect(r):
        return not _dead_end(r)
    if r["status"] != 200:
        return False
    return (r["sniff"] == "html") if r["kind"] == "page" else (r["sniff"] != "html")


def _gone(r) -> bool:
    return r["status"] in (404, 410) or bool(_flags(r) & GONE_FLAGS) or _dead_end(r)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def _print(root: Path, r, *, as_list: bool = False, site: str | None = None) -> str | None:
    """A page's fingerprint read back from the WARC (a redirect's target; a file's sha256); as_list: a list page's
    (list_print)."""
    if _redirect(r):
        return f"redirect:{r['final_url']}"
    if r["kind"] != "page":
        return r["sha256"]
    try:
        url, _status, headers, body = postback.read_response(root, r["warc"], r["warc_offset"])
    except (OSError, ValueError):
        return None
    html = postback.as_text(body, headers.get("content-type"))
    return list_print(html, url, site) if as_list else fingerprint(html, url)


def _before(frontier: Frontier, uid: int, since: float):
    """The answer a row held when the window opened (the module's docstring); None when its first answer came inside
    the window (a copy of an answer fetched in a later second than the window's start is the window's own)."""
    got = frontier.q("SELECT * FROM url_version WHERE url_id=? AND kept_at >= ? ORDER BY kept_at, id LIMIT 1",
                     (uid, since))
    if not got:
        got = frontier.q("SELECT * FROM url_version WHERE url_id=? ORDER BY kept_at DESC, id DESC LIMIT 1", (uid,))
    if got and got[0]["fetched_at"] is not None and got[0]["fetched_at"] > _iso(since):
        got = frontier.q("SELECT * FROM url_version WHERE url_id=? AND fetched_at <= ? ORDER BY fetched_at DESC, "
                         "kept_at DESC, id DESC LIMIT 1", (uid, _iso(since)))
    return got[0] if got else None


def _good_once(frontier: Frontier, uid: int, w) -> bool:
    """The row had a good answer once: a fingerprint watched, or a good copy."""
    if w is not None and w["fp"] is not None:
        return True
    return any(good(c) for c in frontier.q("SELECT * FROM url_version WHERE url_id=?", (uid,)))


def list_pages(frontier: Frontier) -> set[int]:
    """Every list page: walked by button (listwalk), read as a list (page_role), or in a GET pager group."""
    ids = {r[0] for r in frontier.q("SELECT list_id FROM listwalk")}
    ids |= {r[0] for r in frontier.q("SELECT id FROM url WHERE page_role = 'list'")}
    for r in frontier.q("SELECT page_id, res_id FROM page_res WHERE role = 'pager' AND how = 'get' AND res_id IS NOT "
                        "NULL"):
        ids.update((r[0], r[1]))
    return ids


def _news(frontier: Frontier, group: list[int], uid: int, since: float) -> int:
    """A list page's news since: its rows read first (and on no other page of its list before) or changed."""
    marks = ",".join("?" * len(group))
    return frontier.q(f"SELECT count(*) FROM list_row r WHERE r.list_id = ? AND (r.changed_at >= ? OR (r.first_seen >= "
                      f"? AND NOT EXISTS (SELECT 1 FROM list_row o WHERE o.ident = r.ident AND o.list_id IN ({marks}) "
                      f"AND o.first_seen < ?)))", (uid, since, since, *group, since))[0][0]


def night(frontier: Frontier, root: Path, since: float, *, now: float) -> Night:
    """The night's review (the module's docstring)."""
    since_iso = _iso(since)
    out = Night()
    stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "bad": 0, "bodies": defaultdict(set)})
    lists = list_pages(frontier)
    chains = postback.Chains(frontier, root)
    probe = {r[0]: r[1] for r in frontier.q("SELECT host, probe_sha FROM host WHERE probe_sha IS NOT NULL")}
    held_raw = json.loads(frontier.meta(HELD_KEY) or "null")
    held = set(held_raw["ids"]) if held_raw else set()
    held_since = held_raw["since"] if held_raw else since
    watched: list[tuple] = []  # (site, the page_watch values)
    reviewed: dict[int, str] = {}  # url id -> site
    rows = frontier.q("SELECT * FROM url WHERE (fetched_at >= ? AND state IN ('done', 'failed')) OR (state IN "
                      "('pending', 'active') AND id IN (SELECT url_id FROM url_version WHERE kept_at >= ?)) ORDER BY id",
                      (since_iso, since))
    for r in rows:
        site = r["owner_site"] or r["site"]
        reviewed[r["id"]] = site
        # a row an alarm held is judged against the answer before that alarm's night
        prev = _before(frontier, r["id"], held_since if r["id"] in held else since)
        if prev is not None and (prev["warc"], prev["warc_offset"]) == (r["warc"], r["warc_offset"]):
            out.rows.append(Change(r["id"], site, r["url"], r["kind"], "no_answer", prev["fetched_at"]))
            if good(prev):
                stats[site]["n"] += 1
                stats[site]["bad"] += 1
            continue
        seen = frontier.q("SELECT * FROM page_watch WHERE url_id=?", (r["id"],))
        w = seen[0] if seen else None
        answered = r["state"] != "failed"
        now_good = answered and good(r)
        group = sorted(chains._group(r["id"])[0]) if r["id"] in lists else [r["id"]]
        is_list = (r["id"] in lists and r["kind"] == "page" and not _redirect(r)
                   and bool(frontier.q(f"SELECT 1 FROM list_row WHERE list_id IN ({','.join('?' * len(group))}) "
                                       f"LIMIT 1", group)))  # a list whose rows no platform reads: by its text
        fp_now = _print(root, r, as_list=is_list, site=site) if now_good else None
        gone_now = answered and not now_good and _gone(r)
        gone = (w["gone"] if w else 0) + 1 if gone_now else 0
        gone_at = ((w["gone_at"] if w and w["gone_at"] else now) if gone_now else None)
        if prev is None:
            change = "new" if now_good else "not_got"
        elif now_good:
            if not good(prev):
                change = "back"
            elif is_list:
                # by its text only when both answers read as a list (an answer its reader could not take: rows alone)
                fp_prev = _print(root, prev, as_list=True, site=site) if fp_now is not None else None
                text_moved = fp_prev is not None and fp_now != fp_prev
                change = "changed" if _news(frontier, group, r["id"], since) or text_moved else "same"
            else:
                change = "same" if fp_now == _print(root, prev) else "changed"
        elif gone_now and gone >= 2 and now - gone_at >= GONE_AGAIN_S:
            said = w is not None and w["gone"] >= 2 and (w["checked_at"] or 0) - (w["gone_at"] or 0) >= GONE_AGAIN_S
            change = ("still_gone" if said else "deleted") if _good_once(frontier, r["id"], w) else "not_seen"
        else:
            change = "not_seen"
        if prev is not None and good(prev):
            s = stats[site]
            s["n"] += 1
            s["bad"] += int(not now_good)
            # one body over many pages that answered anew (a maintenance page), lists too; the host's not-found aside.
            # A body counts the different answers its pages held before (live 11-25: one page at six nested addresses
            # of id.nknu.edu.tw changed once, six addresses together): a maintenance page turns different pages into
            # one body, addresses that answered one body already did not turn into it
            if (now_good and r["kind"] == "page" and not _redirect(r) and r["sha256"] != prev["sha256"]
                    and r["sha256"] != probe.get(r["host"])):
                s["bodies"][r["sha256"]].add(prev["sha256"])
        changes = ((w["changes"] if w else 0) + 1) if change == "changed" else (0 if change == "same" else
                                                                                 (w["changes"] if w else 0))
        watched.append((site, (r["id"], fp_now, now, now if change in ("new", "changed", "back") else None,
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
        elif s["bodies"] and max(map(len, s["bodies"].values())) >= SAME_BODY_MIN:
            out.alarms[site] = f"{max(map(len, s['bodies'].values()))} pages answered one identical body"
    for site, values in watched:
        if site not in out.alarms:
            frontier.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes, gone_at, gone) VALUES "
                       "(?,?,?,?,?,?,?) ON CONFLICT(url_id) DO UPDATE SET fp=coalesce(excluded.fp, fp), "
                       "checked_at=excluded.checked_at, changed_at=coalesce(excluded.changed_at, changed_at), "
                       "changes=excluded.changes, gone_at=excluded.gone_at, gone=excluded.gone", values)
    # an alarmed site's rows, new ones too, are held: back the next night, judged against the answer before the alarm;
    # held rows not read tonight stay held; the rest are let go
    out.again = sorted({i for i, site in reviewed.items() if site in out.alarms} | (held - set(reviewed)))
    if out.again:
        frontier.meta(HELD_KEY, json.dumps({"since": min(held_since, since), "ids": out.again}))
    else:
        frontier.x("DELETE FROM meta WHERE key = ?", (HELD_KEY,))
    out.sites_changed = sorted({c.site for c in out.rows if c.change in ("new", "changed", "deleted", "back")}
                               - set(out.alarms))
    return out
