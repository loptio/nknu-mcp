"""Reconciliation: the crawl's own records checked against what else knows the sites (round-1 design §18.2b, §17 step
12), the page that is almost empty (§10.1 thin), and the two ways a record changes without a request: rejudge
(§18.2a step 4) and a person's school_side mark (§10.2.1). Nothing here sends a request: R6, the one check that opens
pages, asks the spot checker its caller gives (SpotCheck: jscheck's browser, its proxy and its queue), and the
command runs it only when no run holds run.lock and this process holds the machine-wide lock on school requests.

Per site of a batch (reconcile_sites), in this order (the first two change what the result views say):
- thin: a 200 web page whose body (its platform's content area, else the whole page; a Google Sites embed-code
  block's words and what its pressed tabs and embed boxes brought counted in) has fewer than THIN_CHARS characters
  once the lines most pages of its kind repeat are taken out (its page type with TEMPLATE_MIN_PAGES distinct pages or
  more, else its site with as many, else nothing taken out; a line on TEMPLATE_SHARE of them or more; a page and its
  copies under other addresses count once), that needs no resource (page_res need must, the pager aside), whose
  type is no js or frame and that is no list walked with rows: url.flags thin, taken off again when that no longer
  holds (thin_pages; rejudge too). A page whose body is a picture is never thin (design §23 E3): one that needs a
  picture was not before either (a needed resource); one whose words alone would be thin and that holds, outside its
  layout areas, a picture left for round two or a data: picture of DATA_PICTURE_MIN bytes or more carries in_image
  instead (内容在图片里, for the text recognition of the organising stage; no gap), taken off the same way.
- R7: the school's own .js the page loads (its page_res script rows), read as text with the page's inline scripts by
  the same links.extract (jsread.bindings, selectors matched back to the page's elements): what clicking an element
  does that the page alone does not say is written on the page (page_res, zone r7:<zone>: a click that loads
  something, a writing that hands over to a function nobody read), and each writing nobody has decided about gets a
  decision to suggest (ignore: read and loads nothing; round2: loads something, or cannot be read); the targets only
  data-link, data-href or data-url name, and the Drive files a Google Sites embed names (data-embed-open-url), must
  have their rows.
- R1 every link of a page's menus (links.extract's chrome zone: nav, aside, the page's own header and footer; on a
  profiled site everything outside its content area) has a result; R2 a list's stated total (「共 N 筆」, listwalk.claimed;
  SSO 公告中心's categories against its whole list) is what was got; R3a, R4a every address Common Crawl (2xx) or the
  10-24 archive (done, 2xx) knows of this site has a row or a rule that leaves it out; R3b, R4b the discovery coverage
  (of those that answer well this round, the share a page of this round links to) and Lincoln–Petersen's estimate of
  what is still missed, a lower bound (both lists come from crawls that follow links too); R6 the spot check; V13
  (verify check 13) every page read again from the WARC with links.extract names nothing its link table and page_res
  rows do not; V14 (check 14) every response record reads back and matches its sha256, every WARC file reads through
  gzip to its last record. R5 (the 25 questions' answer pages) is no check since 10-26 (the owner agreed: the 25
  questions measure the answers of the whole project, not round 1's completeness; design §18.2b): an older
  reconciliation's R5 is read past (monitor.reconciled).
- reconcile_site: one row per site (its checks as JSON, the coverages, gates_ok when R1, R2, R3a, R4a, R6, R7, V13,
  V14 all pass), and an event reconcile. In the crawler's own process, as a site finishes (site_done), all of it
  but R6 (skip, so gates_ok 0): the run stops before R6 and a person's look (design §18.2a step 3).

What the checks find that has no row (R1, R3a, R4a: record 二·4 「对账发现爬虫没找到的网址」) is listed for a person, who
gives the ones to fetch to enqueue() (found_via reconcile). rejudge() reads the stored pages of some sites back from
the WARC and works out again, with today's rules, what the crawler would have written of them (links.extract,
round1.admit, pagetypes, the platform profiles, the presses already made: postback.Chains.rebuild), and the thin marks;
what it finds new is queued (found_via rejudge), nothing fetched is fetched again, no fetched_at changes.
review_mark() is Frontier.mark_school_side's command; review_menu_mark() marks a menu item that is the school's broken
link (R1 bad_link, design §10.2.1: the owner's 10-26 decision), which has no url row: an event menu_mark that holds
while the page it was seen on is not fetched again (monitor.menu_marks). Each command that writes refuses while a run
holds run.lock and holds it itself while it writes; and refuses a frontier.db no round-1 crawl made (the 10-24 archive
is only read).

Every way of doing this the design does not spell out is Claude's (【Claude 的执行办法】): which zone is a menu, the
discovery kinds (pages and files), the line split and the content-area fallback of thin, the r7: zone, the library
.js left unread, which R6 differences count, the V14 cache, where a menu item's mark is kept.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import random
import re
import sqlite3
import time
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import google_api, jsread, links, monitor, outcome, postback, profiles, round1, scope, seeds
from nknu.archive.frontier import ROUND, Frontier, round_of, utc_now, wall
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import DecodeError, Snapshot, decode_body

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"
# design §18.2b: R3b, R4b only warn. R5 (the 25 questions' answer pages) taken out 10-26, its number kept free: R6, R7
# keep theirs (the gates as monitor reads them: monitor.GATES, the same list)
CHECKS = ("R1", "R2", "R3a", "R3b", "R4a", "R4b", "R6", "R7", "V13", "V14")
GATES = monitor.GATES
CHECK_LABEL = {
    "R1": "网站选单", "R2": "列表写的总数", "R3a": "Common Crawl 导入齐了没有", "R3b": "Common Crawl 发现覆盖率",
    "R4a": "旧存档导入齐了没有", "R4b": "旧存档发现覆盖率", "R6": "浏览器抽查",
    "R7": "读学校自己的 .js", "V13": "资源没有漏抽（验收第 13 项）", "V14": "存档读得回来（验收第 14 项）",
}
STATUS_LABEL = {PASS: "✓ 过", FAIL: "✗ 没过", WARN: "⚠ 要看", SKIP: "没检查"}

# thin (design §10.1): the design's numbers, checked by the offline rehearsal (§18.1) on 10-26 (archive-rehearsal-2:
# 7,197 stored pages of 46 sites read back; the 25 questions' answer pages: 137 addresses, 59 of them stored pages
# there). None of the 59 is thin at any setting tried (THIN_CHARS 10–100, TEMPLATE_SHARE 0.3–0.9, TEMPLATE_MIN_PAGES
# 3–20), so they set no bound; what is left of the thin pages does. Pages counted page by page (before 10-26):
# 30/0.5/5 made 59 thin; 20 → 56, 40 → 145 (+86: album and tag pages with a title of 30–40 characters, a contact
# box); share 0.4 → 67, 0.6 → 49; min pages 3 → 76, 8 → 52. Every change of share or min pages moved only id.nknu's
# copies: its WordPress shows one listing under several addresses, so a listing's lines were on half of its kind and
# went out as layout (9 listings of 150–200 characters thin). Distinct pages counted once (_distinct): 50 thin, and
# share 0.4/0.5/0.6 → 50/50/49, min pages 3/5/8 → 53/50/48: the numbers no longer swing. What is thin then: school
# 404 pages answered 200 (「404: File not found」 16 characters, tecs's nodata.html 14), empty details and categories
# (0–8), album pages whose pictures are not fetched (ksped 8, tecs 29), one-post tag listings (17–20). Kept as the
# design has them 【Claude 的执行办法, 10-26】: 30 keeps every 404 page and empty page in with a margin (the highest is
# 16) and leaves out what has a title and a line of its own (the cluster at 30–40); 0.5 and 5 sit in the flat part.
THIN_CHARS = 30
TEMPLATE_SHARE = 0.5  # a line on half of the distinct pages or more is the layout's
TEMPLATE_MIN_PAGES = 5  # distinct pages (a page and its copies under other addresses are one)
# design §23 E3 (review of 830c1b7): a data: picture carrying this many bytes or more (its base64 decoded) is a body
# picture; a small icon or a 1×1 placeholder (a few dozen bytes) is not 【Claude 的执行办法】
DATA_PICTURE_MIN = 1024
_DATA_URI = re.compile(r"(?i)data:image/[^,\"'\s<>)]*,[^\"'\s<>)]*")
# R3b, R4b (design §18.2b): below this a warning; set again with the pilot's three sites (§18.2). One constant: the
# acceptance's (monitor.COVER_MIN), which the dashboard and verify also warn by
COVER_WARN = monitor.COVER_MIN
DISCOVERY_KINDS = ("page", "file")  # what the coverage counts: pages and attachments, as both lists hold them
# 「这一轮回 200（或跳转落地后是 ok）」: a row (or where its redirects land) that came back with its content, whatever
# the browser check says of its page type yet (outcome.PAGE_CAME_BACK: ok, thin, unchecked, js, click, unresolved)
ANSWERED = outcome.PAGE_CAME_BACK
# R6 (design §18.2b): the home page and this many more pages of each site, and jscheck's rule for text the browser
# shows that the archived page lacks
R6_PAGES = 4
R6_MORE_CHARS = 200
R6_RATIO = 1.3
ITEMS_KEPT = 20  # items per check in reconcile_site.checks; the report lists every one
R7_ZONE = "r7:"  # page_res.zone of the rows R7 writes: rewritten by each reconcile and rejudge, never the crawler's
# the school's copies of libraries that work by attributes or are no page's code: not read (they bind every element of
# a kind, and define nothing a page's writing names), listed in the report
LIBRARY_JS = re.compile(
    r"(?i)(?:^|[/._-])(?:jquery|bootstrap|popper|swiper|slick|owl\.carousel|camera|fancybox|lightbox|modernizr|"
    r"html5shiv|respond|wow|aos|gsap|moment|vue|react|angular|lodash|underscore|d3|chart|select2|datatables|"
    r"magnific|isotope|masonry|imagesloaded|superfish|hoverintent|easing|colorbox|flexslider|prettyphoto|jcarousel|"
    r"cycle|nivo|fullcalendar|sweetalert|toastr|ckeditor|tinymce|gtag|analytics|fontawesome|font-awesome|"
    r"my97datepicker|wdatepicker|core-js|polyfill)(?:[._-][\w.-]*)?\.js$")
# R6 (11-03, P52): a library's own helper page, which its script opens in a frame of the page the browser shows (My97
# DatePicker's My97DatePicker.htm, 教务处 10-27): the library's, never a menu item the round missed 【Claude 的执行办法】
LIBRARY_PAGE = re.compile(r"(?i)(?:^|/)my97datepicker\.html?$")
MAX_JS_BYTES = 512 * 1024  # a school script larger than this is listed, not read (a bundle, not a page's code)
_TEXT_DROP = ("script", "style", "noscript", "template")
_CODE_DEPTH = 2  # embed-code blocks inside embed-code blocks (links._DATA_CODE_DEPTH)
_ATTR_TEXTS = ("[data-link]", "[data-href]", "[data-url]")
_R7_KEYS = ("js_link:", "js_bind:", "js_deep:", "js_nav:")  # the writings R7 can read a decision for


# ---------------------------------------------------------------- what the checks say


@dataclass
class Check:
    """One check of one site: pass, fail, warn or skip (没检查: never 通过), its numbers and its items."""

    status: str
    counts: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, **self.counts}
        if self.note:
            out["note"] = self.note
        if self.items:
            out["items"] = self.items[:ITEMS_KEPT]
            if len(self.items) > ITEMS_KEPT:
                out["more"] = len(self.items) - ITEMS_KEPT
        return out


@dataclass(frozen=True)
class Found:
    """An address a check knows of that the url table holds no row for (R1, R3a, R4a): for a person, then
    enqueue()."""

    check: str
    url: str
    why: str


@dataclass(frozen=True)
class Suggestion:
    """R7's word on a writing nobody has decided about (page_res why unhandled:unknown), for the platform file."""

    site: str
    key: str
    decision: str  # ignore, round2, or unknown: a school script it needs was not got yet
    why: str
    pages: int
    sample: str


@dataclass
class SiteResult:
    site: str
    batch: str
    checks: dict[str, Check]
    cover_cc: float | None = None
    cover_old: float | None = None
    found: list[Found] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    thin_set: int = 0
    thin_cleared: int = 0
    r7_rows: int = 0

    @property
    def gates_ok(self) -> bool:
        return all(self.checks.get(g, Check(SKIP)).status == PASS for g in GATES)

    def uncompared(self) -> list[str]:
        """The gates that passed with nothing to compare (R1 with no menu, R2 with no total, R3a, R4a with no address
        of the site): 「没发现」, never 「没有」 (review 10-26). Whether that should keep a site from 爬完 is the
        user's to decide; for now it is said wherever the gate is shown."""
        return [g for g in GATES if (c := self.checks.get(g)) is not None and c.status == PASS
                and c.counts.get("compared") is False]


@dataclass
class Reconciled:
    batch: str
    at: str
    sites: list[SiteResult]
    r6: bool

    @property
    def found(self) -> list[tuple[str, Found]]:
        return [(s.site, f) for s in self.sites for f in s.found]

    def text(self) -> str:
        return report_text(self)


@dataclass(frozen=True)
class Sources:
    """What the checks compare with. old None: the old archive seed noted (meta old_archive), if any; a source that is
    not there makes its checks skip (没检查), never pass."""

    cc_dir: Path | None = seeds.CC_DIR
    old: Path | None = None


@dataclass(frozen=True)
class SpotView:
    """What the browser showed of one page (R6), as jscheck's spot checker gives it: the rendered DOM's HTML
    (document.documentElement.outerHTML), where the browser ended, the screenshot it saved (relative to the archive
    directory), or why it could not open the page."""

    url: str
    final_url: str | None = None
    html: str | None = None
    screenshot: str | None = None
    error: str | None = None
    incomplete: str | None = None  # why what the browser showed may be less than the page (jscheck.incomplete)
    data_posts: tuple[str, ...] = ()  # data addresses the page asked by a POST the round cannot make (jscheck.data_post)


# the spot checker (design §18.2b R6): opens one address through jscheck's browser, unreachable proxy and own queue
# (one connection per server, the run's gap; request rows purpose='spotcheck'), stores nothing in the url table
SpotCheck = Callable[[str], SpotView]


# ---------------------------------------------------------------- the archive, read back


class Archive:
    """Response records read back by their position (postback.read_response): bodies and their text."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def record(self, warc: str | None, offset: int | None) -> tuple[str, int, dict[str, str], bytes] | None:
        if not warc or offset is None or offset < 0:
            return None
        try:
            return postback.read_response(self.root, warc, offset)
        except (OSError, ValueError, StopIteration):
            return None

    def text(self, warc: str | None, offset: int | None) -> str | None:
        got = self.record(warc, offset)
        if got is None:
            return None
        return postback.as_text(got[3], got[2].get("content-type"))


_PAGES = ("SELECT * FROM url WHERE owner_site=? AND kind='page' AND state='done' AND status=200 AND tier IN (0, 1) "
          "AND warc IS NOT NULL AND warc_offset IS NOT NULL ORDER BY id")


def _flags(row) -> list[str]:
    return [f for f in (row["flags"] or "").split(",") if f]


def _other_scheme(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.port is not None or parts.scheme not in ("http", "https"):
        return None
    return urlunsplit(parts._replace(scheme="https" if parts.scheme == "http" else "http"))


def _row_of(fr: Frontier, url: str) -> sqlite3.Row | None:
    """The url row of an address as a page or a list writes it (made canonical), or of its other scheme (§6.6)."""
    canon = scope.canonical(url)
    if canon is None:
        return None
    for u in (canon, _other_scheme(canon)):
        if u and (row := fr.row(u)) is not None:
            return row
    return None


def _result(fr: Frontier, uid: int) -> str | None:
    got = fr.q("SELECT result FROM url_result WHERE id=?", (uid,))
    return got[0][0] if got else None


def _landing(fr: Frontier, row) -> tuple[Any, str | None]:
    """The row a redirecting row lands on (url.final_url, at most outcome.MAX_HOPS hops) and its result."""
    cur, res, seen = row, _result(fr, row["id"]), set()
    for _ in range(outcome.MAX_HOPS):
        if res != "redirect" or not cur["final_url"] or cur["id"] in seen:
            break
        seen.add(cur["id"])
        nxt = fr.row(cur["final_url"])
        if nxt is None:
            break
        cur, res = nxt, _result(fr, nxt["id"])
    return cur, res


def _left_out(plan: round1.Plan, url: str) -> str | None:
    """Why today's rules make no row of this address (round1.admit: out:<rule>, unhandled:<why>; a Google address
    is the Google step's API rows, never a row of its own), or None: it should have one."""
    canon = scope.canonical(url)
    if canon is None:
        return "bad_url"
    if scope.is_google_link(canon):
        return "google"
    adm = round1.admit(plan, canon)
    if adm is None:
        return "bad_url"
    return adm.placement.reason or "out" if adm.placement.state is None else None


# ---------------------------------------------------------------- thin (design §10.1)


def _code_texts(code: str, depth: int) -> list[str]:
    """The words a Google Sites embed-code block holds (the HTML in its data-code attribute), blocks in it too."""
    if depth > _CODE_DEPTH or not code.strip():
        return []
    tree = LexborHTMLParser(code)
    out = []
    for node in tree.css("[data-code]"):
        out += _code_texts(node.attributes.get("data-code") or "", depth + 1)
    for tag in _TEXT_DROP:
        for n in tree.css(tag):
            n.decompose()
    root = tree.body or tree.root
    if root is not None:
        out.append(root.text(separator="\n"))
    return out


def _html_texts(html: str, css: str | None) -> list[str]:
    """The words of a page's body, line by line as written: its platform's content area (every node it names), or
    the whole body when it has none or the page shows none of it; embed-code blocks inside counted in."""
    tree = LexborHTMLParser(html or "")
    roots = list(tree.css(css)) if css else []
    if not roots:
        roots = [tree.body] if tree.body is not None else []
    out: list[str] = []
    seen: set[int] = set()
    for node in roots:
        for n in [node, *node.css("[data-code]")]:
            if n.mem_id not in seen and "data-code" in n.attributes:
                seen.add(n.mem_id)
                out += _code_texts(n.attributes.get("data-code") or "", 1)
    for tag in _TEXT_DROP:
        for n in tree.css(tag):
            n.decompose()
    out += [node.text(separator="\n") for node in roots]
    return out


def lines_of(texts: Iterable[str]) -> dict[str, int]:
    """Each line of these texts (white space made one), with its characters that are not white space."""
    out: dict[str, int] = {}
    for t in texts:
        for line in t.splitlines():
            line = " ".join(line.split())
            if line:
                out[line] = len(line.replace(" ", ""))
    return out


def page_lines(html: str, url: str, parts: Iterable[str] = ()) -> dict[str, int]:
    """The body lines thin counts of one page (design §10.1): its content area (profiles.content_css), else the whole
    page; its embed-code blocks, and parts: the HTML its pressed tabs and its embed boxes brought."""
    texts = _html_texts(html, profiles.content_css(url))
    for p in parts:
        texts += _html_texts(p, None)
    return lines_of(texts)


def data_pictures(html: str) -> dict[str, int]:
    """The data: pictures a page writes (an <img>, a <picture>, a CSS background), by their first characters as
    links.extract keeps them (links.DATA_SRC_MAX: page_res.res_key): the bytes each carries (base64 decoded; the
    largest when several begin alike). Design §23 E3."""
    out: dict[str, int] = {}
    for m in _DATA_URI.finditer(html or ""):
        uri = m.group(0)
        head, _, data = uri.partition(",")
        n = len(data.rstrip("=")) * 3 // 4 if ";base64" in head.lower() else len(data)
        key = uri[:links.DATA_SRC_MAX]
        out[key] = max(out.get(key, 0), n)
    return out


def _distinct(pages: Sequence[Mapping[str, int]]) -> list[Mapping[str, int]]:
    """These pages with each set of lines once: a page and its copies under other addresses (a WordPress listing as
    category/x/page/2 and category/category/x …) are one page of the layout, never several votes for their own
    words (the 10-26 rehearsal; near-duplicates are taken out before template detection, as Bar-Yossef &
    Rajagopalan 2002 do with mirrors; 【Claude 的执行办法】)."""
    return list({frozenset(p): p for p in pages}.values())


def _template(pages: Sequence[Mapping[str, int]]) -> frozenset[str]:
    """The lines TEMPLATE_SHARE of these pages or more hold (each distinct page counted once per line), or none when
    there are fewer than TEMPLATE_MIN_PAGES distinct pages."""
    pages = _distinct(pages)
    if len(pages) < TEMPLATE_MIN_PAGES:
        return frozenset()
    seen = Counter(line for p in pages for line in p)
    least = TEMPLATE_SHARE * len(pages)
    return frozenset(line for line, n in seen.items() if n >= least)


def _part_htmls(fr: Frontier, arc: Archive, page_id: int) -> list[str]:
    """What a page's pressed tabs (their JSON's content) and its embed boxes brought, as HTML (design §4.4: their
    words are the page's)."""
    out = []
    for r in fr.q("SELECT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND "
                  "u.kind0 IN ('click', 'embed') AND u.state='done' AND u.status=200 AND u.warc IS NOT NULL",
                  (page_id,)):
        text = arc.text(r["warc"], r["warc_offset"])
        if text is None:
            continue
        if r["kind0"] == "click":
            text = profiles.tab_content(text)
        elif r["sniff"] != "html":
            text = None
        if text:
            out.append(text)
    return out


@dataclass
class ThinResult:
    judged: int = 0
    thin: list[int] = field(default_factory=list)
    set_: int = 0
    cleared: int = 0
    unread: list[int] = field(default_factory=list)
    in_image: list[int] = field(default_factory=list)  # pages whose words alone would be thin, their body a picture


IN_IMAGE = "in_image"  # outcome.FLAGS: 内容在图片里 (design §23 E3), never a gap


def _pictured(fr: Frontier, site: str, pics: Mapping[int, Mapping[str, int]]) -> set[int]:
    """The pages of a site whose body is a picture no needed resource stands for (design §23 E3): outside the layout
    areas (zone chrome), a picture left for round two (Google Sites' runtime pictures, §23 B3) or a data: picture of
    DATA_PICTURE_MIN bytes or more (pics: data_pictures of each page read). A picture the page needs is a needed
    resource: such a page was never thin and needs no mark (the 10-27 rehearsal: 277 pages with a short text and a
    needed picture, most of them a site's icons and banners, would all have said 内容在图片里) 【Claude 的执行办法】."""
    out = {r[0] for r in fr.q("SELECT DISTINCT pr.page_id FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE "
                              "u.owner_site=? AND pr.role='img' AND pr.need = 'round2' AND "
                              "coalesce(pr.zone, '') <> 'chrome'", (site,))}
    for pid, key in fr.q("SELECT pr.page_id, pr.res_key FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE "
                         "u.owner_site=? AND pr.role='img' AND pr.why='data' AND coalesce(pr.zone, '') <> 'chrome'",
                         (site,)):
        if pics.get(pid, {}).get(key, 0) >= DATA_PICTURE_MIN:
            out.add(pid)
    return out


def _set_flag(flags: list[str], flag: str, want: bool) -> tuple[list[str], bool]:
    """flags with flag in it (want) or not, and whether that changed anything."""
    if want == (flag in flags):
        return flags, False
    return ([*flags, flag] if want else [f for f in flags if f != flag]), True


def _decide_thin(fr: Frontier, site: str, rows, lines: Mapping[int, Mapping[str, int]], skip: set[int],
                 write: bool, pics: Mapping[int, Mapping[str, int]] | None = None) -> ThinResult:
    out = ThinResult(unread=[r["id"] for r in rows if r["id"] not in lines and r["id"] not in skip])
    by_type: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["id"] in lines:
            by_type[r["page_type"] or ""].append(r["id"])
    site_tpl = _template([lines[i] for i in lines])
    type_tpl = {t: _template([lines[i] for i in ids]) for t, ids in by_type.items()
                if len(_distinct([lines[i] for i in ids])) >= TEMPLATE_MIN_PAGES}
    must = {r[0] for r in fr.q("SELECT DISTINCT pr.page_id FROM page_res pr JOIN url u ON u.id = pr.page_id "
                               "WHERE u.owner_site=? AND pr.need='must' AND pr.role <> 'pager'", (site,))}
    scripted = {r[0] for r in fr.q(
        "SELECT u.id FROM url u LEFT JOIN page_type pt ON pt.key = u.page_type LEFT JOIN js_page jp ON "
        "jp.url_id = u.id AND jp.at >= u.fetched_at WHERE u.owner_site=? AND u.kind='page' AND "
        "(pt.verdict IN ('js', 'frame') OR jp.verdict IN ('js', 'frame'))", (site,))}
    walked = {r[0] for r in fr.q("SELECT lw.list_id FROM listwalk lw JOIN url u ON u.id = lw.list_id "
                                 "WHERE u.owner_site=? AND coalesce(lw.rows, lw.rows_unique, 0) > 0", (site,))}
    pictured = _pictured(fr, site, pics or {})
    for r in rows:
        pid = r["id"]
        if pid in skip:
            want = in_image = False
        elif pid in lines:
            out.judged += 1
            tpl = type_tpl.get(r["page_type"] or "", site_tpl)  # its kind with enough pages, else its site
            chars = sum(n for line, n in lines[pid].items() if line not in tpl)
            short = chars < THIN_CHARS and pid not in scripted and pid not in walked and pid not in must
            in_image = short and pid in pictured  # its words alone would be thin: its body is the picture (§23 E3)
            want = short and not in_image
        else:
            continue  # not read back: left as it is (V14 says why)
        if want:
            out.thin.append(pid)
        if in_image:
            out.in_image.append(pid)
        flags, thin_changed = _set_flag(_flags(r), "thin", want)
        flags, img_changed = _set_flag(flags, IN_IMAGE, in_image)
        if thin_changed:
            if want:
                out.set_ += 1
            else:
                out.cleared += 1
        if write and (thin_changed or img_changed):
            fr.x("UPDATE url SET flags=? WHERE id=?", (",".join(flags) or None, pid))
    return out


_NOT_READ = ("login", "soft404", "js_redirect")  # pages whose words are not theirs to judge (§8.3, §10.1)


def thin_pages(fr: Frontier, root: Path, sites: Iterable[str], *, write: bool = True) -> dict[str, ThinResult]:
    """Design §10.1 thin for every stored web page of these sites (reconcile and rejudge both call it): the flag set
    where it holds, taken off where it no longer does. Sends nothing."""
    arc = Archive(root)
    out = {}
    for site in sites:
        rows = fr.q(_PAGES, (site,))
        lines, skip, pics = {}, set(), {}
        for r in rows:
            if set(_flags(r)) & set(_NOT_READ):
                skip.add(r["id"])
                continue
            html = arc.text(r["warc"], r["warc_offset"])
            if html is not None:
                lines[r["id"]] = page_lines(html, r["url"], _part_htmls(fr, arc, r["id"]))
                pics[r["id"]] = data_pictures(html)
        out[site] = _decide_thin(fr, site, rows, lines, skip, write, pics)
    return out


# ---------------------------------------------------------------- R7: the school's own .js


@dataclass
class R7Page:
    entries: list[dict[str, Any]] = field(default_factory=list)  # what the .js says the page does (page_res rows)
    gone: set[str] = field(default_factory=set)  # writings the .js answers: read, and they load nothing
    loads: set[str] = field(default_factory=set)  # function names a new click calls (it loads something)
    bound_loads: bool = False  # a school script binds an element of the page to something that loads (a new click)
    still: set[str] = field(default_factory=set)  # writings the .js does not answer
    read: list[str] = field(default_factory=list)  # the school scripts read
    missing: list[str] = field(default_factory=list)  # the school scripts not got (yet)
    libraries: list[str] = field(default_factory=list)
    big: list[str] = field(default_factory=list)  # school scripts larger than MAX_JS_BYTES: not read
    other_js: bool = False  # the page loads a script nobody reads (from elsewhere, never downloaded; or too large)


class ScriptCache:
    """The school scripts' text by url row id, and what one set of them binds (jsread.bindings), read once."""

    def __init__(self, fr: Frontier, arc: Archive) -> None:
        self.fr, self.arc = fr, arc
        self.code: dict[int, str | None] = {}
        self.parsed: dict[tuple[int, ...], tuple[jsread.Script, bool]] = {}

    def text(self, row) -> str | None:
        if row["id"] not in self.code:
            self.code[row["id"]] = self.arc.text(row["warc"], row["warc_offset"])
        return self.code[row["id"]]

    def script(self, ids: tuple[int, ...], code: str) -> tuple[jsread.Script, bool]:
        """The joined scripts and whether they bind anything (a handler or an event fired on load)."""
        if ids not in self.parsed:
            sc = jsread.Script(code)
            bound, auto = jsread.bindings(sc)
            self.parsed[ids] = (sc, bool(bound or auto))
        return self.parsed[ids]


def _inject(html: str, code: str) -> str:
    """The page with the school's scripts as one more inline script at its end (never run: read as text)."""
    safe = code.replace("</script", "<\\/script")
    return f"{html}\n<script>\n{safe}\n</script>"


def r7_page(fr: Frontier, row, html: str, url: str, page: links.PageLinks, cache: ScriptCache) -> R7Page:
    """R7 for one stored page (design §7.8 「R7 怎么读下载下来的 .js」, §18.2b): its school scripts read with its inline
    ones by links.extract, selectors matched back to its elements; what that says beyond the page alone is a page_res
    row each (the same rules as the crawler's: links.click_need, links.unhandled_need with the platform file's
    decision; a click a clicks rule fits that only an outside script binds waits for the next round, since nothing
    this round presses it). Writes nothing (write_r7 does)."""
    out = R7Page()
    site = scope.site_key(row["url"])  # the platform the page is written on decides, as the crawler's (Crawler._parts)
    ids, codes = [], []
    for s in fr.q("SELECT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND pr.role='script' "
                  "ORDER BY u.id", (row["id"],)):
        if LIBRARY_JS.search(urlsplit(s["url"]).path):
            out.libraries.append(s["url"])
            continue
        if (s["bytes"] or 0) > MAX_JS_BYTES:
            out.big.append(s["url"])
            continue
        text = cache.text(s) if s["state"] == "done" and s["status"] == 200 and s["warc"] else None
        if text is None:
            out.missing.append(s["url"])
            continue
        ids.append(s["id"])
        codes.append(text)
        out.read.append(s["url"])
    known = {r[0] for r in fr.q("SELECT res_key FROM page_res WHERE page_id=? AND role='script'", (row["id"],))}
    base = page.base or url
    out.other_js = bool(out.big) or any(
        (t := scope.canonical(src, base)) not in known and not LIBRARY_JS.search(urlsplit(t or src).path)
        for src in page.scripts)
    base_unhandled = {u.key for u in page.unhandled}
    if not codes:
        out.still = base_unhandled
        return out
    code = "\n;\n".join(codes)
    sc, binds = cache.script(tuple(ids), code)
    names = {u.name for u in page.unhandled if u.kind == "js_link" and u.name}
    if not binds and not any(sc.defined(n) for n in names):
        out.still = base_unhandled
        return out
    combined = links.extract(_inject(html, code), url, profiles.content_css(row["url"]))
    base_clicks = {c.key for c in page.clicks}
    new_unhandled = [u for u in combined.unhandled if u.key not in base_unhandled]
    out.gone = base_unhandled - {u.key for u in combined.unhandled}
    out.still = base_unhandled - out.gone
    for c in combined.clicks:
        if c.key in base_clicks:
            continue
        out.loads.add(c.fn)
        out.bound_loads |= c.trigger.startswith("bind:")
        decision = profiles.click_decision(site, c, html, url)
        need, why = ("round2", "round2:click") if decision == "handle" else links.click_need(c, decision)
        out.entries.append({"res_key": c.key, "how": "click", "role": "click", "need": need, "why": why or None,
                            "zone": R7_ZONE + c.zone, "text": c.text})
    for u in new_unhandled:
        decision = profiles.decision(site, u.key)
        need, why = links.unhandled_need("round2" if decision == "handle" else decision)
        out.entries.append({"res_key": u.key, "how": "unhandled", "role": "click", "need": need, "why": why,
                            "zone": R7_ZONE + u.zone, "text": u.text})
    fired = {u.key for u in page.unhandled if u.auto}
    for u in combined.unhandled:  # a menu only an outside script fires on load: jscheck opens the page too (§8.4)
        if u.auto and u.key not in fired:
            out.entries.append({"res_key": outcome.AUTO_KEY + u.key, "how": outcome.AUTO_HOW, "role": "click",
                                "need": "no", "why": outcome.AUTO_WHY, "zone": R7_ZONE + u.zone, "text": u.text})
    return out


def write_r7(fr: Frontier, page_id: int, entries: Iterable[dict[str, Any]]) -> int:
    """The page's R7 rows anew (zone r7:…); a key the crawler wrote itself stays the crawler's."""
    entries = list(entries)
    with fr.lock:
        fr.conn.execute("DELETE FROM page_res WHERE page_id=? AND zone LIKE ?", (page_id, R7_ZONE + "%"))
        fr.add_page_res(page_id, entries)
    return len(entries)


def _suggest(site: str, key: str, per_page: list[tuple[str, R7Page]]) -> Suggestion | None:
    """R7's word on one writing nobody decided about, over the pages that have it (design §7.8 「R7 怎么下结论」): round2
    when any page says it loads something or cannot be read, else unknown while a school script it needs is not got,
    else ignore (read, and it loads nothing)."""
    if not key.startswith(_R7_KEYS):
        return None
    name = key.split(":", 1)[1]
    votes: list[tuple[str, str, str]] = []
    for url, r in per_page:
        js = "、".join(r.read[:2])
        if key in r.gone and ((name and name in r.loads) or (not name and r.bound_loads)):
            votes.append(("round2", f"学校的 .js 里读到它（{js}）：会发请求、载入内容；写得出规则就写 clicks", url))
        elif key in r.gone:
            votes.append(("ignore", f"读了学校的 .js 本体（{js}）：不发请求、不载入内容", url))
        elif r.missing:
            votes.append(("unknown", f"这一页的学校 .js 还没抓到（{r.missing[0]}）：抓到以后再对账", url))
        elif not r.read and not r.other_js:
            votes.append(("ignore", "这一页没有载入任何外面的 .js（套件除外），页面上也没有它的定义：点了什么都不做", url))
        else:
            votes.append(("round2", "学校的 .js 和 inline script 里都读不到它（或交给外面的 .js）：看不到它做什么，下一轮用浏览器点",
                          url))
    for d in ("round2", "unknown", "ignore"):
        hit = next((v for v in votes if v[0] == d), None)
        if hit is not None:
            return Suggestion(site, key, d, hit[1], len(per_page), hit[2])
    return None


# ---------------------------------------------------------------- V13: the pages read again (verify check 13)


def _page_read(row, html: str, url: str) -> links.PageLinks:
    """links.extract of a stored page as the crawler reads it (its platform's content area, and the pager links its
    platform's pager box names: Crawler._page)."""
    page = links.extract(html, url, profiles.content_css(row["url"]))
    page.pagers.extend(u for u in postback.profile_pagers(url, html, page.base) if u not in page.pagers)
    return page


def _menu_targets(page: links.PageLinks) -> list[str]:
    """What a stored page's menus link (links.extract's chrome zone: nav, aside, the page's own header and footer; on a
    profiled site everything outside its content area), as R1 reads them: canonical addresses."""
    return [t for href, _text in page.links
            if page.link_zone.get(href) == "chrome" and (t := scope.canonical(href, page.base)) is not None]


def _expected(plan: round1.Plan, row, html: str, url: str, page: links.PageLinks, fr: Frontier,
              src_id: int | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """What the crawler writes of one page (Crawler._parts): the link table's targets ({address: what}) and the
    page_res keys ({key: what}). A Google address's rows are the Google step's (drive:<id> …): its link only. A
    Google Sites runtime picture's key is where it sits (links.google_sites_keys, design §23 B2), read against src_id:
    the row its HTML was read from (the page's own, or an embed box's). The platform the page's own address is
    written on decides, as the crawler's (gaps ② ③ of 11-08)."""
    base, site = page.base, scope.site_key(row["url"])
    want_links: dict[str, str] = {}
    want_keys: dict[str, str] = {}
    pictures = {scope.canonical(img.src, base) for img in page.images}
    gsimg = links.google_sites_keys(page, src_id if src_id is not None else row["id"])
    for img in page.images:
        if img.src.startswith("data:"):
            want_keys[img.src] = "image"
            continue
        target = scope.canonical(img.src, base)
        if target is None:
            continue
        if img.src in gsimg:
            want_keys[gsimg[img.src]] = "image"
            continue
        need, _why = links.image_need(img, row["url"], page.profiled)
        if need == "must" and scope.is_google_link(target):
            continue
        want_keys[target] = "image"
    frame_srcs = set()
    for fr_ in page.frames:
        target = scope.canonical(fr_.src, base)
        if target is None:
            continue
        frame_srcs.add(fr_.src)
        adm = round1.admit(plan, fr_.src, base, row, "hidden" if fr_.hidden else "frame")
        if adm is not None and adm.placement.reason == "out:internal_service":
            continue
        want_links[target] = "frame"
    pagers = set(page.pagers)
    for href, text in page.links:
        if text in ("[frame]", "[embed]") and href in frame_srcs:
            continue
        target = scope.canonical(href, base)
        if target is None or target in pictures:
            continue
        adm = round1.admit(plan, href, base, row, "link")
        if adm is not None and adm.placement.reason == "out:internal_service":
            continue
        want_links[target] = text if text in _ATTR_TEXTS else "link"
        if adm is None or adm.step == "google" or scope.is_google_link(target):
            continue
        pl = adm.placement
        if pl.verdict == "resource" or pl.kind0 in ("file", "form", "shortlink", "image"):
            want_keys[target] = "resource"
        elif target in pagers and fr.row(adm.url) is not None:
            want_keys[target] = "pager"
    for src in page.scripts:
        target = scope.canonical(src, base)
        if target is not None and fr.row(target) is not None:
            want_keys[target] = "script"
    for c in page.clicks:
        decision = profiles.click_decision(site, c, html, url)
        plan_ = (profiles.click_plan(site, c, html, url)
                 if decision == "handle" and row["tier"] in round1.TIER_OF_IMP.values() else None)
        want_keys[plan_.res_key if plan_ is not None else c.key] = "click"
    for u in page.unhandled:
        want_keys[u.key] = "unhandled"
        if u.auto:
            want_keys[outcome.AUTO_KEY + u.key] = "auto"
    for b in page.buttons:
        key = postback.button_key(b.target, b.argument)
        if (profiles.decision(site, key, b.text) or "unknown") != "press":
            want_keys.setdefault(key, "button")
    return want_links, want_keys


def _v13_page(fr: Frontier, plan: round1.Plan, row, html: str, url: str, page: links.PageLinks,
              parts: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    """What the crawler should have written of one page and did not (its link table, its page_res keys), with what
    its embed boxes and pressed tabs brought (parts: (their row id, their HTML, the address their links are read
    against)). A Google address's own key is the Google step's: only its link is looked for."""
    want_links, want_keys = _expected(plan, row, html, url, page, fr)
    srcs = [row["id"]]
    for pid, phtml, purl in parts:
        sub = links.extract(phtml, purl)
        pl, pk = _expected(plan, row, phtml, purl, sub, fr, src_id=pid)
        for target, what in pl.items():
            want_links.setdefault(target, what + "@part")
        for key, what in pk.items():
            if what in ("image", "resource"):
                want_keys.setdefault(key, what + "@part")
        srcs.append(pid)
    have_links = {r[0] for r in fr.q(f"SELECT dst FROM link WHERE src IN ({','.join('?' * len(srcs))})", srcs)}
    have_keys = {r[0] for r in fr.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    out = []
    for target, what in want_links.items():
        if target not in have_links:
            out.append({"page": row["url"], "what": what, "missing": "link", "target": target})
    for key, what in want_keys.items():
        if key not in have_keys and f"google:{key}" not in have_keys:
            out.append({"page": row["url"], "what": what, "missing": "page_res", "target": key})
    return out


def _parts_of(fr: Frontier, arc: Archive, page_row) -> list[tuple[int, str, str]]:
    """The embed boxes and pressed tabs of a page, read back: (their row id, their HTML, the address their links are
    read against: a tab's the page's own, Crawler._part_answer)."""
    out = []
    for r in fr.q("SELECT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND "
                  "u.kind0 IN ('click', 'embed') AND u.state='done' AND u.status=200 AND u.warc IS NOT NULL",
                  (page_row["id"],)):
        text = arc.text(r["warc"], r["warc_offset"])
        if text is None:
            continue
        if r["kind0"] == "click":
            content = profiles.tab_content(text)
            if content:
                out.append((r["id"], content, page_row["url"]))
        elif r["sniff"] == "html":
            out.append((r["id"], text, r["url"]))
    return out


def verify_extract(fr: Frontier, root: Path, sites: Iterable[str], plan: round1.Plan | None = None
                   ) -> dict[str, Check]:
    """Verify check 13 for these sites (design §13): every stored web page read again from the WARC with the crawler's
    own links.extract names nothing its link table and page_res do not hold (embed boxes and pressed tabs too), and
    no resource of theirs is not_discovered. A difference is a crawler bug, or rules changed since and no rejudge:
    fail. Read only."""
    plan = plan if plan is not None else round1.load()
    arc, out = Archive(root), {}
    lost: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in fr.q("SELECT u.owner_site, u.url, pr.res_key FROM page_resource pr JOIN url u ON u.id = pr.page_id "
                  "WHERE pr.result = 'not_discovered'"):
        lost[r[0]].append({"page": r[1], "missing": "not_discovered", "target": r[2]})
    for site in sites:
        items, unread, n = [], 0, 0
        for row in fr.q(_PAGES, (site,)):
            if set(_flags(row)) & {"login", "soft404"}:
                continue  # read for nothing more (Crawler._page)
            rec = arc.record(row["warc"], row["warc_offset"])
            if rec is None:
                unread += 1
                continue
            n += 1
            url, html = rec[0] or row["url"], postback.as_text(rec[3], rec[2].get("content-type"))
            items += _v13_page(fr, plan, row, html, url, _page_read(row, html, url), _parts_of(fr, arc, row))
        items += lost.get(site, [])
        out[site] = Check(FAIL if items or unread else PASS,
                          {"pages": n, "unread": unread, "missing": len(items)}, items,
                          "读不回来的页见 V14；对不上的是爬虫的 bug，或规则改了还没 rejudge" if items or unread else "")
    return out


# ---------------------------------------------------------------- V14: the archive reads back (verify check 14)


class _Window(io.RawIOBase):
    """Bytes [start, end) of a file, for gzip to read through."""

    def __init__(self, fh, start: int, end: int) -> None:
        fh.seek(start)
        self.fh, self.left = fh, max(0, end - start)

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        n = min(len(b), self.left)
        if n <= 0:
            return 0
        data = self.fh.read(n)
        b[:len(data)] = data
        self.left -= len(data)
        return len(data)


def gzip_through(path: Path, start: int, end: int) -> str | None:
    """None when bytes [start, end) of a WARC file (each record its own gzip member, start at a member's start) read
    through Python's gzip to their end; else what broke. warcio's check says 0 after a gzip error (design §13)."""
    try:
        with open(path, "rb") as fh, gzip.GzipFile(fileobj=io.BufferedReader(_Window(fh, start, end))) as gz:
            while gz.read(1 << 20):
                pass
    except (OSError, EOFError, zlib.error) as exc:
        return f"{type(exc).__name__}: {exc}"[:200]
    return None


def _addresses(*urls: str | None) -> set[str]:
    """An address as written, canonical, and in the other scheme (a scheme switch, design §6.6)."""
    out = set()
    for u in urls:
        for v in (u, scope.canonical(u) if u else None):
            if v:
                out.add(v)
                if (o := _other_scheme(v)) is not None:
                    out.add(o)
    return out


def verify_archive(fr: Frontier, root: Path, sites: Iterable[str], *, cache: bool = False,
                   whole: bool = False, stop: Callable[[], None] | None = None) -> dict[str, Check]:
    """Verify check 14 for these sites (design §13), the one implementation (monitor.warc_readback, verify's item 14,
    reads through it): every response record of their rows and presses read back at its warc_offset with its sha256
    as recorded (a url row's record for its own address, its final address, or the other scheme's: other_url), a
    fetched row with an answer and no position failing too; and every WARC file they use read through gzip up to its
    last recorded record (records being written after that are not looked at: a run may be writing the file, as when
    the crawler reconciles a finished site). cache: the end verified of each file is kept in meta (v14:<file>), so the
    next call reads only what came after it (the reconcile command's). whole: each file read to its very end (verify,
    with no run going on)."""
    arc, root = Archive(root), Path(root)
    files: dict[str, str | None] = {}

    def file_ok(warc: str) -> str | None:
        if warc not in files:
            path = root / warc
            if not path.is_file():
                broke = "no such file"
            elif whole:
                broke = gzip_through(path, 0, path.stat().st_size)
            else:
                end = max(fr.q("SELECT max(warc_offset + warc_len) FROM url WHERE warc=? AND warc_len IS NOT NULL",
                               (warc,))[0][0] or 0,
                          fr.q("SELECT max(warc_offset + warc_len) FROM action WHERE warc=? AND warc_len IS NOT NULL",
                               (warc,))[0][0] or 0)
                key = f"v14:{warc}"
                start = int(fr.meta(key) or 0) if cache else 0
                start = start if start <= end else 0
                broke = gzip_through(path, start, end)
                if broke is None and cache:
                    fr.meta(key, str(end))
            files[warc] = broke
        return files[warc]

    out = {}
    for site in sites:
        items, n = [], 0
        rows = fr.q("SELECT id, url, final_url, warc, warc_offset, sha256, 'url' AS what FROM url WHERE owner_site=? "
                    "AND state='done' AND status IS NOT NULL", (site,))
        rows += fr.q("SELECT id, target AS url, NULL AS final_url, warc, warc_offset, sha256, 'action' AS what "
                     "FROM action WHERE site=? AND state IN ('done', 'mismatch') AND status IS NOT NULL", (site,))
        used = set()
        for i, r in enumerate(rows):
            if stop is not None and i % 200 == 0:
                stop()  # raises when the run stops (_Batch.check_stop)
            if not r["warc"] or r["warc_offset"] is None or r["warc_offset"] < 0:
                items.append({"what": r["what"], "id": r["id"], "url": r["url"], "problem": "no_position"})
                continue
            n += 1
            used.add(r["warc"])
            rec = arc.record(r["warc"], r["warc_offset"])
            if rec is None:
                items.append({"what": r["what"], "id": r["id"], "url": r["url"], "problem": "unreadable"})
            elif r["sha256"] and hashlib.sha256(rec[3]).hexdigest() != r["sha256"]:
                items.append({"what": r["what"], "id": r["id"], "url": r["url"], "problem": "sha256"})
            elif r["what"] == "url" and rec[0] and not _addresses(rec[0]) & _addresses(r["url"], r["final_url"]):
                items.append({"what": "url", "id": r["id"], "url": r["url"], "problem": "other_url",
                              "archived": rec[0]})
        for warc in sorted(used):
            broke = file_ok(warc)
            if broke is not None:
                items.append({"what": "file", "url": warc, "problem": broke})
        out[site] = Check(FAIL if items else PASS, {"records": n, "files": len(used), "problems": len(items)}, items)
    return out


# ---------------------------------------------------------------- R1, R2


def _menu_status(fr: Frontier, target: str) -> str:
    """has (a row with a result), waiting (a row still waiting: todo, retry), bad_link (a school error page's address,
    outcome.ERROR_OUT: the school's broken link, 要人看, never a rule: review 10-26), rule (the rules make no row: the
    link table or scope.judge says so; a Google address is its API rows), or missing."""
    row = _row_of(fr, target)
    if row is not None:
        return "waiting" if _result(fr, row["id"]) in ("todo", "retry") else "has"
    verdict = fr.q("SELECT verdict FROM link WHERE dst=? LIMIT 1", (target,))
    if verdict:
        v = verdict[0][0] or ""
        if v == outcome.ERROR_OUT:
            return "bad_link"
        if v.startswith(("out:", "unhandled:", "bad_url")):
            return "rule"
        return "has" if scope.is_google_link(target) else "missing"
    v = scope.judge(target)
    return "rule" if not v.ok and v.reason not in scope.SOFT_OUT else "missing"


# R1's items in the order they are kept (reconcile_site keeps the first ITEMS_KEPT): the school's broken links first,
# so that monitor can tell whether every one of them is marked since (monitor.reconciled), then what the crawl lacks
_R1_ORDER = {"bad_link": 0, outcome.SCHOOL_SIDE: 1, "missing": 2, "waiting": 3}
R1_STATUS_ZH = {"bad_link": "学校的链接坏了（要人看）", outcome.SCHOOL_SIDE: outcome.RESULT_LABEL[outcome.SCHOOL_SIDE],
                "missing": "没抓到（对账发现爬虫没找到的网址）", "waiting": "还在等"}


def _r1(fr: Frontier, menus: Mapping[str, str], marks: Mapping[str, Mapping] | None = None
        ) -> tuple[Check, list[Found]]:
    """menus: each menu target of the site's pages -> the first page it is on. marks: the site's menu items a person
    marked as the school's that still hold (monitor.menu_marks)."""
    marks = marks or {}
    counts = Counter()
    items, found = [], []
    for target, page in sorted(menus.items()):
        s = _menu_status(fr, target)
        if s == "bad_link" and target in marks:
            s = outcome.SCHOOL_SIDE
        counts[s] += 1
        if s in ("waiting", "missing", "bad_link"):
            items.append({"target": target, "page": page, "status": s})
        elif s == outcome.SCHOOL_SIDE:  # listed all the same, with the person's note (design §10.2.1)
            items.append({"target": target, "page": page, "status": s, "note": marks[target].get("note"),
                          "marked_at": marks[target].get("at")})
        if s == "missing":
            found.append(Found("R1", target, f"选单项目，在 {page}"))
    items.sort(key=lambda i: _R1_ORDER[i["status"]])
    # the school's broken menu link: nothing the crawler missed (no Found), but never a pass unseen (outcome: bad_link
    # is 要人看 and a page using one is never complete) 【Claude 的执行办法】; once a person marked it the school's, it
    # holds R1 back no more (the owner's 10-26 decision, design §10.2.1)
    status = FAIL if counts["missing"] or counts["waiting"] else WARN if counts["bad_link"] else PASS
    kinds = ("has", "rule", "waiting", "missing", "bad_link", outcome.SCHOOL_SIDE)
    out = Check(status, {"menu_links": len(menus), **{k: counts[k] for k in kinds}, "compared": bool(menus)}, items)
    if not menus:  # review 10-26: 「没发现」 is no 「没有」
        out.note = ("没认出选单（这个网站的网页没有 nav、header、footer、aside，也没有平台档案）：这一项没比到，"
                    "不等于选单都抓到了")
    notes = []
    if counts["bad_link"]:
        notes.append(f"选单上 {counts['bad_link']} 个网址是学校的错误页（学校的链接坏了）：人看过、确认是学校那边的问题的，用 "
                     "nknu archive review --mark school_side --menu <网址> --note … 标记")
    if counts[outcome.SCHOOL_SIDE]:
        notes.append(f"{counts[outcome.SCHOOL_SIDE]} 个已标成{R1_STATUS_ZH[outcome.SCHOOL_SIDE]}，照样列出")
    if notes:
        out.note = "；".join(notes)
    return out, found


def _r2(fr: Frontier, root: Path, site: str, *, write: bool = True) -> Check:
    """Each list's stated total (「共 N 筆」: listwalk.claimed) against the rows got; a walk with no end; SSO 公告中心's
    categories against its whole list (postback.Chains.sso_totals, which marks the whole list mismatch:total: only
    when this call may write); a site that ignores c.nknu's category menu with no 「全部」 list walked to its last page
    (monitor.class_all_missing, design §23 F2: problem class_all)."""
    items = []
    n = claimed = 0
    for lw in fr.q("SELECT lw.*, u.url FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE u.owner_site=?",
                   (site,)):
        n += 1
        if lw["claimed"] is not None:
            claimed += 1
            got = lw["rows_unique"] if lw["rows_unique"] is not None else lw["rows"]
            if got is None or got < lw["claimed"]:
                items.append({"list": lw["url"], "claimed": lw["claimed"], "got": got, "problem": "fewer"})
        if lw["ended_by"] == "mismatch:total":
            items.append({"list": lw["url"], "claimed": lw["claimed"], "problem": "mismatch:total",
                          "note": lw["note"]})
        elif lw["ended_by"] in (None, "walking"):
            items.append({"list": lw["url"], "problem": lw["ended_by"] or "no_end", "note": lw["note"]})
    for item in monitor.class_all_missing(fr, site):  # design §23 F2: the category menu ignored, 「全部」 not walked whole
        items.append({"list": f"{site} newslist.aspx（不带 class）", "problem": "class_all",
                      "note": monitor.class_all_words(item), "lists": item["lists"]})
    sso = None
    if write and fr.q("SELECT 1 FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE u.owner_site=? AND lw.profile=? "
                      "LIMIT 1", (site, profiles.SSO_NEWS.name)):
        sso = postback.Chains(fr, root).sso_totals()
        if sso[0] is not None and sso[1] is not None and sso[0] != sso[1]:
            items.append({"list": "SSO 公告中心", "categories": sso[0], "whole": sso[1], "problem": "categories"})
    compared = bool(claimed) or (sso is not None and None not in sso)
    out = Check(FAIL if items else PASS, {"lists": n, "claimed": claimed, "compared": compared,
                                          **({"sso": list(sso)} if sso is not None else {})}, items)
    if not compared:  # review 10-26: nothing stated is nothing compared
        out.note = ("这个网站没有翻过的列表：这一项没比到" if not n else
                    f"{n} 个列表都没写总数（「共 N 筆」「第 x / y 页」）：总数比不了，这一项没比到")
    return out


# ---------------------------------------------------------------- R3, R4: Common Crawl and the old archive


_SOURCES: dict[tuple, frozenset[str]] = {}  # a list read once per process while its files stay as they were


def _stamp_of(*paths: Path) -> tuple:
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def cc_addresses(cc_dir: Path) -> frozenset[str]:
    """The addresses Common Crawl's index (CDXJ lines) holds a 2xx answer of, canonical (design §18.2b R3)."""
    files = sorted(Path(cc_dir).glob("*.cdx"))
    key = ("cc", *_stamp_of(*files))
    if key not in _SOURCES:
        _SOURCES[key] = frozenset(_read_cc(files))
    return _SOURCES[key]


def _read_cc(files: list[Path]) -> set[str]:
    out = set()
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                i = line.find("{")
                if i < 0:
                    continue
                try:
                    rec = json.loads(line[i:])
                except ValueError:
                    continue
                if str(rec.get("status") or "").startswith("2") and (c := scope.canonical(rec.get("url") or "")):
                    out.add(c)
    return out


def old_addresses(old: Path) -> frozenset[str]:
    """The addresses the 10-24 archive fetched well (done, 2xx), canonical; its frontier.db read as immutable (never
    written, its -wal and -shm not even opened: seeds._open_old)."""
    db = seeds._old_db(Path(old))
    key = ("old", *_stamp_of(db))
    if key not in _SOURCES:
        conn = seeds._open_old(db)
        try:
            rows = conn.execute("SELECT url FROM url WHERE state='done' AND status BETWEEN 200 AND 299").fetchall()
        finally:
            conn.close()
        _SOURCES[key] = frozenset(c for (u,) in rows if (c := scope.canonical(u)))
    return _SOURCES[key]


def _imported(fr: Frontier, plan: round1.Plan, addrs: Iterable[str], check: str, source: str
              ) -> tuple[Check, list[Found]]:
    """R3a, R4a: every address of the list has a row, or today's rules make none."""
    n = ruled = 0
    items, found = [], []
    for u in sorted(addrs):
        n += 1
        if _row_of(fr, u) is not None:
            continue
        why = _left_out(plan, u)
        if why is not None:
            ruled += 1
            continue
        items.append({"url": u})
        found.append(Found(check, u, f"{source}记得，这一轮没有这一行，规则也没挡"))
    out = Check(FAIL if items else PASS, {"known": n, "ruled_out": ruled, "missing": len(items), "compared": bool(n)},
                items)
    if not n:  # review 10-26: the list knows nothing of this site, so nothing was compared
        out.note = f"{source}不认得这个网站的网址：没得比，这一项没比到"
    return out, found


def _discoverable(row) -> bool:
    """A row the coverage counts (DISCOVERY_KINDS): a page or an attachment as found, never what the crawl makes of
    one by itself (a school script, a pressed tab, a sitemap, a Google API answer)."""
    return (row["kind"] in DISCOVERY_KINDS and (row["kind0"] or row["kind"]) in ("page", "file", "login_check")
            and row["host"] not in outcome.API_HOSTS)


@dataclass
class _SiteRows:
    ok: set[int] = field(default_factory=set)  # landing rows of this site that came back (ANSWERED)
    linked: set[int] = field(default_factory=set)  # … that a page of this round links to (or to a redirect there)
    land: dict[int, tuple[int, str | None, bool]] = field(default_factory=dict)  # id -> (landing, result, counted)


def _land(fr: Frontier, row) -> tuple[int, str | None, bool]:
    land, res = _landing(fr, row)
    return land["id"], res, _discoverable(land)


def _site_rows(fr: Frontier, site: str) -> _SiteRows:
    out = _SiteRows()
    rows = fr.q("""SELECT u.id, u.url, u.final_url, u.kind, u.kind0, u.host, u.result,
                     EXISTS (SELECT 1 FROM link l JOIN url s ON s.id = l.src WHERE l.dst = u.url AND s.id <> u.id
                             AND s.kind = 'page' AND s.state = 'done' AND s.status = 200) AS linked
                   FROM url_result u WHERE u.owner_site = ?""", (site,))
    for r in rows:
        out.land[r["id"]] = _land(fr, r) if r["result"] == "redirect" else (r["id"], r["result"], _discoverable(r))
    for r in rows:
        lid, res, counted = out.land[r["id"]]
        if res in ANSWERED and counted:
            out.ok.add(lid)
            if r["linked"]:
                out.linked.add(lid)
    return out


def _coverage(fr: Frontier, sr: _SiteRows, addrs: Iterable[str], source: str) -> tuple[Check, float | None]:
    """R3b, R4b (design §18.2b): of the list's addresses that came to ok this round (M), how many a page of this round
    links to (m): coverage m / M; Lincoln–Petersen's estimate of the site's ok addresses, n·M/m (n: this round's ok
    addresses a page links to), and what is missed beyond this round's ok ones, a lower bound since both lists
    follow links. Only a warning."""
    sample, linked, only = set(), set(), []
    for u in sorted(addrs):
        row = _row_of(fr, u)
        if row is None:
            continue
        lid, res, counted = sr.land.get(row["id"]) or _land(fr, row)
        if res not in ANSWERED or not counted:
            continue
        sample.add(lid)
        if lid in sr.linked:
            linked.add(lid)
        else:
            only.append({"url": u})
    big_m, m, n, total = len(sample), len(linked), len(sr.linked), len(sr.ok)
    cover = m / big_m if big_m else None
    est = round(n * big_m / m) if m else None
    # 【Claude 的执行办法】 (review 10-26): an estimate no larger than what this round got says nothing of what it
    # missed (it is a lower bound): no number, never 「至少还漏约 0 个」
    missed = est - total if est is not None and est > total else None
    counts = {"known_ok": big_m, "linked": m, "site_linked_ok": n, "site_ok": total, "cover": cover,
              "estimate": est, "missed_at_least": missed}
    if cover is None:
        return Check(SKIP, counts, only, f"{source}记得的网址这一轮没有一个回 200：估不出"), None
    return Check(WARN if cover < COVER_WARN else PASS, counts, only, monitor.cover_words(source, counts)), cover



# ---------------------------------------------------------------- R6: the spot check


def page_text(html: str, parts: Iterable[str] = ()) -> str:
    """Every word of a page (its menus too), its embed-code blocks and what its tabs and embeds brought: the text R6
    keeps of what the browser showed."""
    texts = _html_texts(html, None)
    for p in parts:
        texts += _html_texts(p, None)
    return "\n".join(texts)


def _r6_pages(fr: Frontier, plan: round1.Plan, site: str, batch: str) -> list[sqlite3.Row]:
    came = ",".join("?" * len(ANSWERED))
    pages = fr.q(f"SELECT * FROM url_result WHERE owner_site=? AND kind='page' AND result IN ({came}) AND warc IS NOT "
                 "NULL AND warc_offset IS NOT NULL ORDER BY id", (site, *ANSWERED))
    out, home_id = [], None
    s = plan.site(site)
    if s is not None and s.home and (row := _row_of(fr, s.home)) is not None:
        land, _res = _landing(fr, row)
        if land["kind"] == "page" and land["warc"]:
            out.append(land)
            home_id = land["id"]
    others = [p for p in pages if p["id"] != home_id]
    rng = random.Random(f"{batch}|{site}")
    out += rng.sample(others, min(R6_PAGES, len(others)))
    return out


def _known_targets(fr: Frontier, row, stored: links.PageLinks) -> set[str]:
    base = stored.base
    known = {scope.canonical(h, base) for h, _t in stored.links}
    known |= {scope.canonical(f.src, base) for f in stored.frames}
    known |= {scope.canonical(i.src, base) for i in stored.images}
    known |= {r[0] for r in fr.q("SELECT dst FROM link WHERE src=?", (row["id"],))}
    known |= {r[0] for r in fr.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    return {k for k in known if k}


def _r6(fr: Frontier, arc: Archive, plan: round1.Plan, site: str, batch: str, spot: SpotCheck | None,
        out_dir: Path | None) -> Check:
    if spot is None:
        return Check(SKIP, note="浏览器抽查要等 run 停下、拿到全机锁，用 jscheck 的浏览器做（nknu archive reconcile）")
    pages = _r6_pages(fr, plan, site, batch)
    if not pages:
        return Check(FAIL, {"pages": 0}, note="这个网站没有回 200 的网页可以抽查")
    items, partial, posts = [], [], []
    for row in pages:
        view = spot(row["url"])
        if view.error or not view.html:
            items.append({"url": row["url"], "problem": "error", "error": (view.error or "no page")[:200]})
            continue
        if data_posts := _data_posts(row, getattr(view, "data_posts", ())):
            # the page reads data from an address of its own by a POST the round cannot make: what the browser
            # showed is compared as any page's, and the page's type is judged js (method data_post, the next round's
            # 读资料网址), so that no page of it ever shows complete. A data address the platform file reads this
            # round (cLv2's ajaxTimeline.aspx, design §23 C2) is none
            posts.append({"url": row["url"], "problem": "data_post", "targets": list(data_posts)[:5]})
            _type_data_post(fr, row, data_posts)
        html = arc.text(row["warc"], row["warc_offset"])
        if html is None:
            items.append({"url": row["url"], "problem": "unreadable"})
            continue
        css = profiles.content_css(row["url"])
        stored = links.extract(html, row["url"], css)
        shown_at = view.final_url or row["url"]
        shown = links.extract(view.html, shown_at, css)
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{row['id']}.txt").write_text(page_text(view.html), encoding="utf-8")
        diff: dict[str, Any] = {}
        if (shown.text_chars - stored.text_chars >= R6_MORE_CHARS
                and shown.text_chars >= R6_RATIO * stored.text_chars):
            diff["text"] = [stored.text_chars, shown.text_chars]
        known = _known_targets(fr, row, stored)
        missing = []
        candidates = [(h, shown.link_zone.get(h, "unknown"), "link") for h, _t in shown.links]
        candidates += [(f.src, f.zone, "frame") for f in shown.frames]
        # a Google Sites runtime picture has a new address each reading: it is the stored one where it sits in the
        # same place (its key, design §23 B2)
        gs_known = set(links.google_sites_keys(stored, row["id"]).values()) | known
        gs_shown = links.google_sites_keys(shown, row["id"])
        candidates += [(i.src, i.zone, "image") for i in shown.images
                       if links.image_need(i, row["url"], shown.profiled)[0] == "must"
                       and gs_shown.get(i.src) not in gs_known]
        for href, zone, how in candidates:
            target = scope.canonical(href, shown.base)
            if (target is None or target in known or target.split("#", 1)[0] == row["url"]
                    or LIBRARY_PAGE.search(urlsplit(target).path)):
                continue
            what = ("image" if how == "image" else "attachment" if scope.kind_of(target) == "file"
                    else "menu" if zone == "chrome" else None)
            if what is None:
                continue
            adm = round1.admit(plan, href, shown.base, row, "image" if how == "image" else how)
            if adm is None or adm.placement.state is None:
                continue
            missing.append({"what": what, "target": target})
        if missing:
            diff["missing"] = missing[:20]
        if diff:  # what the browser showed beyond the archive is a difference, whether or not it loaded whole
            items.append({"url": row["url"], "problem": "differs", **diff, "screenshot": view.screenshot})
        elif getattr(view, "incomplete", None):  # no difference where the page was not seen whole proves nothing
            partial.append({"url": row["url"], "problem": "incomplete", "why": view.incomplete[:200],
                            "screenshot": view.screenshot})
    counts = {"pages": len(pages), "differ": len(items), "incomplete": len(partial)}
    if posts:
        counts["data_post"] = len(posts)
    if items:
        return Check(FAIL, counts, items + partial + posts, "agent 再拿截图对照：正文、附件、图片、选单")
    if partial:  # 【Claude 的执行办法】 (review 10-26): no difference seen where nothing was seen whole is no pass
        return Check(SKIP, counts, partial + posts,
                     f"{len(partial)} 页浏览器没载入完整（页面要的脚本或资料没回应、太慢、一直没安静下来）：这几页等于没抽查，"
                     "过一阵再跑 reconcile")
    return Check(PASS, counts, posts, f"{len(posts)} 页用 POST 向自己的资料网址要资料（这一轮发不了）：网页类型记成 js、"
                                      "下一轮用读资料网址补；显示出来的部分没有差异" if posts else "")


def _data_posts(row, targets: Sequence[str]) -> tuple[str, ...]:
    """The data addresses a page asked by a POST (jscheck.data_post) that are no data address its platform file reads
    this round (profiles.known_data_api: cLv2's timeline, design §23 C2)."""
    return tuple(t for t in targets if not profiles.known_data_api(row["url"], t))


def _type_data_post(fr: Frontier, row, targets: Sequence[str]) -> None:
    """A page's type judged js by the browser spot check (R6): it loads data by a POST the round cannot make. Written
    only over a verdict that is not js already; the 10-25 import's verdict (source import) and a person's (manual)
    stand, any difference left to the reconciliation's report (design §23 C2)."""
    key = row["page_type"]
    if not key:
        return
    cur = fr.q("SELECT verdict, source FROM page_type WHERE key=?", (key,))
    if not cur or cur[0]["verdict"] == "js" or cur[0]["source"] in ("manual", "import"):
        return
    # a note token keeps no comma (page_type.note is comma-separated: jscheck.note_token's rule)
    note = "data_post:" + targets[0].replace(",", "%2C") if targets else "data_post"
    fr.x("UPDATE page_type SET verdict='js', why=NULL, method='data_post', source='spotcheck', tested_at=?, "
         "note=CASE WHEN coalesce(note, '') = '' THEN ? ELSE note || ',' || ? END WHERE key=?",
         (utc_now(), note, note, key))
    fr.event("info", "verdict_set", f"R6 浏览器抽查：{key} 用 POST 向 {targets[0] if targets else '?'} 要资料，这一轮发"
             "不了：类型记成 js（下一轮读资料网址）", row["host"], row["url"])


# ---------------------------------------------------------------- one batch


def fresh(fr: Frontier, site: str) -> bool:
    """Whether a site's reconciliation passed every gate and is newer than the last run and rejudge that covered it
    (design §10.5, verify check 18): the acceptance's own reading (monitor.reconciled: the event order says which came
    last, events within one second staying ordered), so that reconcile, verify and the dashboard never disagree."""
    rec = monitor.reconciled(fr, monitor.Rounds(fr)).get(site)
    return rec is not None and rec.fresh and rec.passed


def site_finished(fr: Frontier, site: str) -> bool:
    """A site has nothing left this run but rows waiting for a retry or for the lab (design §18.2a step 3, B1): no
    row of it to fetch yet (url_result todo) or being fetched, no press of it pending or active."""
    if fr.q("SELECT 1 FROM url WHERE owner_site=? AND (state='active' OR (state='pending' AND attempts=0 AND "
            "coalesce(reason, '') NOT IN ('dns', 'host_unreachable'))) LIMIT 1", (site,)):
        return False
    return not fr.q("SELECT 1 FROM action WHERE site=? AND state IN ('pending','active') LIMIT 1", (site,))


class _Batch:
    def __init__(self, fr: Frontier, root: Path, plan: round1.Plan, sources: Sources, batch: str,
                 spot: SpotCheck | None, write: bool, out_dir: Path | None,
                 stop: Callable[[], bool] | None = None) -> None:
        self.fr, self.root, self.plan, self.batch = fr, Path(root), plan, batch
        self.stop = stop
        self._many = False  # a batch of several sites reads page_resource once for all of them
        self.arc = Archive(root)
        self.spot, self.write, self.out_dir = spot, write, out_dir
        self.cc = self.old = None
        self.cc_note = self.old_note = ""
        if sources.cc_dir is not None and Path(sources.cc_dir).is_dir():
            self.cc = cc_addresses(Path(sources.cc_dir))
        else:
            self.cc_note = f"没有 Common Crawl 的索引（{sources.cc_dir}）"
        old = sources.old or (Path(m) if (m := fr.meta("old_archive")) else None)
        if old is not None and seeds._old_db(Path(old)).is_file():
            self.old = old_addresses(Path(old))
        else:
            self.old_note = f"没有旧存档（{old}）" if old else "没有旧存档：seed 没记下 old_archive，也没给 --old"
        self.scripts = ScriptCache(fr, self.arc)
        self._lost: dict[str, list[tuple[str, str]]] | None = None

    def _by_site(self, addrs: frozenset[str] | None, site: str) -> list[str]:
        return [u for u in addrs if scope.site_key(u) == site] if addrs else []

    def lost(self, site: str) -> list[tuple[str, str]]:
        """The resources page_resource finds not_discovered on this site's pages, after R7 wrote. One site (the
        crawler's, as a site finishes): asked of its own pages only (SQLite looks its pages up by owner_site and their
        page_res rows by page_id; review 10-26: the whole view took seconds, and the crawl's workers waited); a batch
        of several: the whole view once (page_resource is as costly as page_completeness)."""
        if not self._many:
            return [(r[0], r[1]) for r in self.fr.q(
                "SELECT u.url, pr.res_key FROM page_resource pr JOIN url u ON u.id = pr.page_id "
                "WHERE pr.result = 'not_discovered' AND u.owner_site = ?", (site,))]
        if self._lost is None:
            self._lost = defaultdict(list)
            for r in self.fr.q("SELECT u.owner_site, u.url, pr.res_key FROM page_resource pr JOIN url u ON "
                               "u.id = pr.page_id WHERE pr.result = 'not_discovered'"):
                self._lost[r[0]].append((r[1], r[2]))
        return self._lost.get(site, [])

    def check_stop(self) -> None:
        if self.stop is not None and self.stop():
            raise Stopped(f"batch {self.batch}: the run is stopping")

    def run(self, sites: Sequence[str]) -> list[SiteResult]:
        t0 = time.monotonic()
        self._many = len(sites) > 1
        results = [self._offline(site) for site in sites]
        self.check_stop()
        v14 = verify_archive(self.fr, self.root, sites, cache=self.write, stop=self.check_stop)
        for res in results:
            site = res.site
            self.check_stop()
            res.checks["R6"] = _r6(self.fr, self.arc, self.plan, site, self.batch, self.spot,
                                   self.out_dir / site.replace("/", "_") if self.out_dir is not None else None)
            res.checks["V14"] = v14[site]
            lost = self.lost(site)
            if lost:
                v13 = res.checks["V13"]
                v13.status = FAIL
                v13.items += [{"page": u, "missing": "not_discovered", "target": k} for u, k in lost]
                v13.counts["not_discovered"] = len(lost)
                v13.note = v13.note or "页面用到了、却没有取得的纪录：爬虫的 bug"
            res.checks = {c: res.checks[c] for c in CHECKS if c in res.checks}
            if self.write:
                self.fr.x("INSERT OR REPLACE INTO reconcile_site(site, batch, at, checks, cover_cc, cover_old, "
                          "gates_ok) VALUES (?,?,?,?,?,?,?)",
                          (site, self.batch, utc_now(),
                           json.dumps({c: k.as_json() for c, k in res.checks.items()}, ensure_ascii=False),
                           res.cover_cc, res.cover_old, int(res.gates_ok)))
        if self.write and results:
            self.fr.event("info", "reconcile", json.dumps(
                {"batch": self.batch, "sites": sorted(sites), "r6": self.spot is not None,
                 "gates_ok": sorted(r.site for r in results if r.gates_ok),
                 "seconds": round(time.monotonic() - t0, 1)}, ensure_ascii=False))
        return results

    def _offline(self, site: str) -> SiteResult:
        """What needs no browser and no page_completeness of other sites: thin, R7 (both write first), R1, R2, R3,
        R4, V13."""
        fr, arc = self.fr, self.arc
        res = SiteResult(site, self.batch, {})
        rows = fr.q(_PAGES, (site,))
        lines: dict[int, dict[str, int]] = {}
        pics: dict[int, dict[str, int]] = {}
        skip: set[int] = set()
        menus: dict[str, str] = {}
        r7: dict[str, list[tuple[str, R7Page]]] = defaultdict(list)
        r7_items: list[dict[str, Any]] = []
        v13: list[dict[str, Any]] = []
        unread = 0
        unknown = {(r[0], r[1]) for r in fr.q(
            "SELECT pr.page_id, pr.res_key FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE u.owner_site=? AND "
            "pr.how='unhandled' AND pr.why='unhandled:unknown'", (site,))}
        for row in rows:
            self.check_stop()  # between pages: a run that stops never waits for a large site's whole reconciliation
            rec = arc.record(row["warc"], row["warc_offset"])
            if rec is None:
                unread += 1
                continue
            url, html = rec[0] or row["url"], postback.as_text(rec[3], rec[2].get("content-type"))
            flags = set(_flags(row))
            parts = _parts_of(fr, arc, row)
            if flags & set(_NOT_READ):
                skip.add(row["id"])
            else:
                lines[row["id"]] = page_lines(html, row["url"], [p[1] for p in parts])
                pics[row["id"]] = data_pictures(html)
            if flags & {"login", "soft404"}:
                continue
            page = _page_read(row, html, url)
            for t in _menu_targets(page):
                menus.setdefault(t, row["url"])
            v13 += _v13_page(fr, self.plan, row, html, url, page, parts)
            got = r7_page(fr, row, html, url, page, self.scripts)
            if self.write:
                res.r7_rows += write_r7(fr, row["id"], got.entries)
            elif got.entries:
                r7_items.append({"page": row["url"], "problem": "not_written", "rows": len(got.entries)})
            for key in {k for pid, k in unknown if pid == row["id"]}:
                r7[key].append((row["url"], got))
            r7_items += self._r7_page_items(row, html, page)
        self.check_stop()
        thin = _decide_thin(fr, site, rows, lines, skip, self.write, pics)
        res.thin_set, res.thin_cleared = thin.set_, thin.cleared
        res.checks["R1"], found = _r1(fr, menus, monitor.menu_marks(fr, site))
        res.found += found
        res.checks["R2"] = _r2(fr, self.root, site, write=self.write)
        for name, addrs, note, check, label in (("cc", self.cc, self.cc_note, "R3", "Common Crawl "),
                                               ("old", self.old, self.old_note, "R4", "旧存档")):
            if addrs is None:
                res.checks[check + "a"] = Check(SKIP, note=note)
                res.checks[check + "b"] = Check(SKIP, note=note)
                continue
            mine = self._by_site(addrs, site)
            res.checks[check + "a"], found = _imported(fr, self.plan, mine, check + "a", label)
            res.found += found
            res.checks[check + "b"], cover = _coverage(fr, _site_rows(fr, site), mine, label)
            if name == "cc":
                res.cover_cc = cover
            else:
                res.cover_old = cover
        for key, per_page in sorted(r7.items()):
            if (s := _suggest(site, key, per_page)) is not None:
                res.suggestions.append(s)
        waiting = [s for s in res.suggestions if s.decision == "unknown"]
        undecided = fr.q("SELECT count(DISTINCT pr.res_key) FROM page_res pr JOIN url u ON u.id = pr.page_id "
                         "WHERE u.owner_site=? AND pr.how='unhandled' AND pr.why='unhandled:unknown'", (site,))[0][0]
        status = FAIL if r7_items else WARN if undecided or waiting else PASS
        res.checks["R7"] = Check(status, {"pages": len(rows) - unread, "rows": res.r7_rows, "undecided": undecided,
                                          "suggested": len(res.suggestions)},
                                 r7_items + [{"key": s.key, "suggest": s.decision, "why": s.why, "pages": s.pages}
                                             for s in res.suggestions],
                                 "还没决定的写法要在平台档案写决定（见建议），然后 rejudge" if undecided else "")
        res.checks["V13"] = Check(FAIL if v13 or unread else PASS, {"pages": len(rows) - unread, "unread": unread,
                                                                    "missing": len(v13)}, v13,
                                  "对不上的是爬虫的 bug，或规则改了还没 rejudge" if v13 else "")
        return res

    def _r7_page_items(self, row, html: str, page: links.PageLinks) -> list[dict[str, Any]]:
        """The targets only data-link, data-href, data-url name, and the Drive files a Google Sites embed names
        (data-embed-open-url), each with its row (design §18.2b R7)."""
        fr, out = self.fr, []
        have = {r[0] for r in fr.q("SELECT dst FROM link WHERE src=?", (row["id"],))}
        for href, text in page.links:
            if text not in _ATTR_TEXTS or (t := scope.canonical(href, page.base)) is None or t in have:
                continue
            adm = round1.admit(self.plan, href, page.base, row, "link")
            if adm is not None and adm.placement.reason == "out:internal_service":
                continue  # never in the link table (Crawler._parts)
            out.append({"page": row["url"], "problem": "attr_target", "target": t})
        keys = {r[0] for r in fr.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
        tree = LexborHTMLParser(html)
        for node in tree.css("[data-embed-open-url]"):
            v = scope.canonical(node.attributes.get("data-embed-open-url") or "", page.base)
            g = google_api.parse_link(v) if v else None
            if g is None or g.kind not in ("file", "folder") or not g.file_id:
                continue
            if f"drive:{g.file_id}" in keys or fr.q("SELECT 1 FROM drive_item WHERE file_id=? LIMIT 1", (g.file_id,)):
                continue
            out.append({"page": row["url"], "problem": "drive_embed", "target": f"drive:{g.file_id}"})
        return out


def reconcile_sites(fr: Frontier, root: Path, sites: Sequence[str], *, batch: str, plan: round1.Plan | None = None,
                    sources: Sources | None = None, spot: SpotCheck | None = None, write: bool = True,
                    out_dir: Path | None = None, stop: Callable[[], bool] | None = None) -> Reconciled:
    """Reconcile these sites (design §18.2b) on an open frontier, sending nothing but what `spot` sends (R6; None:
    R6 skip, gates_ok 0). write: the thin marks, R7's page_res rows, reconcile_site and the event (False: read only,
    what each check would say). out_dir: where R6 keeps the text the browser showed (one file per page). The caller
    decides whether it may write and send (reconcile(): the command; site_done(): the crawler's process). stop: asked
    between pages and sites; true, and Stopped is raised before anything of the batch is written as done."""
    work = _Batch(fr, root, plan if plan is not None else round1.load(), sources or Sources(), batch, spot, write,
                  out_dir, stop)
    return Reconciled(batch, utc_now(), work.run(list(dict.fromkeys(sites))), spot is not None)


def site_done(fr: Frontier, root: Path, site: str, *, plan: round1.Plan, batch: str = "run",
              sources: Sources | None = None, stop: Callable[[], bool] | None = None) -> SiteResult:
    """The offline part of one site's reconciliation, in the crawler's own process as the site finishes (design
    §18.2a step 3; site_finished says when): every check but R6 (skip), so gates_ok is 0 and the dashboard shows
    「⚠ 纪录齐了，还没对账」 with what failed. The crawler is the one writer still (on a thread and a connection of its
    own: Frontier.sibling); nothing is sent. stop: the run's; Stopped when it stops meanwhile."""
    return reconcile_sites(fr, root, [site], batch=batch, plan=plan, sources=sources, spot=None, stop=stop).sites[0]


# ---------------------------------------------------------------- the commands' guards


class Stopped(RuntimeError):
    """The run stopped while a site was being reconciled (site_done's stop): nothing of that site's reconciliation is
    written as done (no reconcile_site row, no event); R7's rows of the pages read so far are what any reconciliation
    writes, and the next one does it all again."""


class NotRound1(RuntimeError):
    """A frontier.db no round-1 crawl made (the 10-24 archive, or none): only read, never written here."""


def _round1_db(root: Path) -> Path:
    db = Path(root) / "frontier.db"
    if not db.is_file():
        raise NotRound1(f"{db} does not exist")
    if round_of(db) != ROUND:
        raise NotRound1(f"{db} was not made by a round-1 crawl (no meta round={ROUND}): it is read only")
    return db


@contextlib.contextmanager
def _run_lock(root: Path) -> Iterator[None]:
    """run.lock for the whole change (crawler.hold_run_lock: AlreadyRunning while a run holds it, design §6.9)."""
    from nknu.archive import crawler as crawl_mod

    fh = crawl_mod.hold_run_lock(Path(root))
    try:
        yield
    finally:
        fh.close()


def _stamp() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")


def _sites_of(fr: Frontier, plan: round1.Plan, sites: Sequence[str] | None) -> list[str]:
    if sites:
        return list(dict.fromkeys(sites))
    got = [r[0] for r in fr.q("SELECT site FROM site_plan WHERE round='1' ORDER BY site")]
    return got or sorted(s.site for s in plan.sites.values() if s.round == "1")


def reconcile(root: Path, *, batch: str, sites: Sequence[str] | None = None, plan: round1.Plan | None = None,
              sources: Sources | None = None, spot: SpotCheck | None = None,
              spot_factory: Callable[[Frontier], contextlib.AbstractContextManager[SpotCheck]] | None = None
              ) -> tuple[Reconciled, Path]:
    """`nknu archive reconcile --batch <批> [--sites …] [--no-r6]` (design §18.2b): refused while a run holds run.lock
    (it is held here throughout); R6 only while this process holds the machine-wide lock on school requests. The spot
    checker comes as spot, or is made by spot_factory from the open frontier once run.lock is held and entered for the
    whole batch (jscheck.SpotChecker(root, fr): it writes its request rows there); neither: R6 skip (--no-r6), so no
    site passes its gates. Writes reconcile_site, the event, reports/reconcile-<批>-<时间>.md and, when any check found
    addresses with no row, …-found.txt (one address a line, for enqueue). Returns the result and the report's path."""
    root = Path(root)
    db = _round1_db(root)
    if (spot is not None or spot_factory is not None) and not schoollock.held():
        raise schoollock.SchoolLockNotHeld("R6 opens school pages: take the machine-wide lock first (design §6.9)")
    plan = plan if plan is not None else round1.load()
    stamp = _stamp()
    safe = re.sub(r"[^\w.-]", "_", batch)
    reports = root / "reports"
    with _run_lock(root):
        fr = Frontier(db)
        try:
            made = spot_factory(fr) if spot_factory is not None else contextlib.nullcontext(spot)
            with made as checker:
                got = reconcile_sites(fr, root, _sites_of(fr, plan, sites), batch=batch, plan=plan, sources=sources,
                                      spot=checker,
                                      out_dir=reports / f"spotcheck-{safe}-{stamp}" if checker is not None else None)
        finally:
            fr.close()
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / f"reconcile-{safe}-{stamp}.md"
    out.write_text(got.text(), encoding="utf-8")
    if got.found:
        (reports / f"reconcile-{safe}-{stamp}-found.txt").write_text(
            "".join(f"{f.url}\t{f.check}\t{site}\n" for site, f in got.found), encoding="utf-8")
    return got, out


# ---------------------------------------------------------------- rejudge and enqueue: the crawler offline


class _QuietFrontier(Frontier):
    """A frontier that counts nothing twice when pages are read again (rejudge): an off-campus domain or a button seen
    once more keeps its count (the button's decision is today's)."""

    def offsite(self, domain: str, src: int, sample: str) -> None:
        self.x("INSERT OR IGNORE INTO offsite(domain, n, first_src, sample) VALUES (?,1,?,?)", (domain, src, sample))

    def postback(self, site: str, target: str, src: int, argument: str | None = None, text: str | None = None,
                 form_id: str | None = None, decision: str = "unknown") -> None:
        self.x("INSERT INTO postback(site, target, n, sample, argument, text, form_id, decision) VALUES (?,?,1,?,?,?,?,?) "
               "ON CONFLICT(site, target) DO UPDATE SET decision = excluded.decision",
               (site, target, src, argument, text, form_id, decision))


class _RejudgeChains(postback.Chains):
    """A stored list read again (rejudge): its press entries come back from the action table (rebuild), its rows'
    item entries from it and from the pages its presses brought, and the buttons to press that no press of any epoch
    covers are unpressed (要人看); no epoch is opened and nothing is planned. A GET-walked list's walk is worked out
    again as page_stored does."""

    def page_stored(self, row, html: str, warc: str | None, warc_offset: int | None,
                    page: links.PageLinks | None = None) -> postback.Stored:
        site = row["owner_site"] or scope.site_key(row["url"])  # the rows' keys and the walk's, as Chains'
        prof = profiles.profile_for(row["url"])  # the platform the page is written on (gaps ② ③ of 11-08)
        has_pagers = bool(page.pagers) if page is not None else row["page_role"] == "list"
        list_id = row["id"]
        if prof is None:
            return postback.Stored("list" if has_pagers else None, 0,
                                   self.get_walk(list_id) if has_pagers else None)
        pr = postback.read_page(prof, row["url"], html, site=site, list_url=row["url"], page=page)
        self.f.add_page_res(list_id, self._items(pr, row["url"]))
        self.rebuild(list_id)
        have = {(a[0], a[1]) for a in self.f.q("SELECT kind, row_key FROM action WHERE list_id=? AND kind NOT IN ('click', 'data')",
                                                (list_id,))}
        wanted = [*pr.presses, *([pr.next] if pr.next is not None and pr.walk == "postback" else [])]
        unplanned = [*pr.unplanned, *(postback.button_key(p.target, p.argument) for p in wanted
                                      if (p.kind, p.row_key) not in have)]
        for a in self.f.q("SELECT * FROM action WHERE list_id=? AND kind IN ('pager', 'detail') AND state='done' AND "
                          "sniff='html' AND warc IS NOT NULL ORDER BY epoch, step, id", (list_id,)):
            try:
                url, _status, headers, body = self.read(a["warc"], a["warc_offset"])
            except (OSError, ValueError, StopIteration):
                continue
            text = postback.as_text(body, headers.get("content-type"))
            if a["kind"] == "pager":
                got = postback.read_page(prof, url, text, site=site, list_url=row["url"], step=a["step"])
                self.f.add_page_res(list_id, self._items(got, url))
            else:
                a_prof, _rs = self._profile(a)
                got = postback.read_page(a_prof or prof, url, text, site=site, parent_key=a["row_key"],
                                         step=a["step"], walks=("none",))
            self._unpressed(list_id, got.unplanned_at, got.texts)
        ended = None
        if pr.walk in ("postback", "one_get"):
            lw = self.f.q("SELECT * FROM listwalk WHERE list_id=?", (list_id,))
            ended = lw[0]["ended_by"] if lw else None
            if ended == "mismatch:empty":  # the one end today's list_end may judge otherwise (FIX 3, 11-06)
                ended = self._ended_again(row, prof, site, pr, lw[0])
        elif pr.walk == "get" or has_pagers:
            ended = self.get_walk(list_id)
            if ended is None and pr.walk == "get" and not has_pagers:
                cands = postback.pager_candidates(row["url"], page) if page is not None else ()
                ended = postback.list_end(replace(pr, current=1, max_seen=1,
                                                  unknown_pagers=(*pr.unknown_pagers, *cands)),
                                          self._rows_unique(list_id))
                self._walk(list_id, site, prof.name, 1, pages=1, rows=len(pr.rows), claimed=pr.claimed,
                           ended=ended, note="one page")
        return postback.Stored("list" if pr.walk != "none" or has_pagers else None, 0, ended,
                               tuple(dict.fromkeys(unplanned)),
                               content=pr.content and pr.walk == "none" and not has_pagers, texts=pr.texts)

    def _ended_again(self, row, prof: profiles.Profile, site: str, pr: postback.PageRead, lw) -> str:
        """A walk that ended mismatch:empty, judged again with today's list_end on the page it ended on (FIX 3, 11-06:
        a table of content, PageRead.content, ends where its pager does): the answer of its epoch's last press of the
        pager (a 「下一頁」 that stayed on its page included), or the list page itself when its epoch pressed none. Only
        the end changes, with its ok and a note; a last page that cannot be read back, or that today's rules see a next
        page on (only a walk can say more), keeps its end. Nothing is pressed. 【Claude 的执行办法】"""
        last, stayed = pr, False
        got = self.f.q("SELECT * FROM action WHERE list_id=? AND epoch=? AND kind='pager' AND state='done' AND "
                       "sniff='html' AND warc IS NOT NULL AND warc_offset IS NOT NULL ORDER BY step DESC, id DESC "
                       "LIMIT 1", (row["id"], lw["epoch"]))
        if got:
            try:
                url, _status, headers, body = self.read(got[0]["warc"], got[0]["warc_offset"])
            except (OSError, ValueError, StopIteration):
                return lw["ended_by"]
            last = postback.read_page(prof, url, postback.as_text(body, headers.get("content-type")), site=site,
                                      list_url=row["url"], step=got[0]["step"])
            stayed = last.pager_how == "next" and last.current == got[0]["step"] - 1
        if last.next is not None and not stayed:
            return lw["ended_by"]
        end = postback.list_end(replace(last, next=None), self._rows_unique(row["id"]))
        if end != lw["ended_by"]:
            self.f.x("UPDATE listwalk SET ended_by=?, ok=?, note=? WHERE list_id=?",
                     (end, int(end in postback.END_OK), f"rejudge: was {lw['ended_by']}", row["id"]))
        return end

    def _items(self, pr: postback.PageRead, base: str) -> list[dict[str, Any]]:
        out = []
        for r in pr.rows:
            res_id = None
            if r.href and (canon := scope.canonical(r.href, base)) is not None and (hit := self.f.row(canon)):
                res_id = hit["id"]
            out.append({"res_key": f"item:{r.key}", "how": "get" if r.href else "postback", "role": "item",
                        "need": "no", "why": "item", "res_id": res_id, "text": r.title[:100]})
        return out


_OFFLINE: type | None = None


def _no_client(_warc):
    raise RuntimeError("rejudge and enqueue send nothing: no client is ever made")


def _no_lookup(host: str):
    raise RuntimeError(f"rejudge and enqueue look up no name ({host}): the next run does")


def _offline_class() -> type:
    """The crawler with no network (made when first needed: crawler.py may import this module)."""
    global _OFFLINE
    if _OFFLINE is not None:
        return _OFFLINE
    from nknu.archive import crawler as crawl_mod

    class OfflineCrawler(crawl_mod.Crawler):
        """The crawler's own reading of stored answers, with no client, no name lookup and no request: a host nobody
        looked up gets queue '?', which the next run looks up before it sends anything (Crawler._place_hosts); every
        new row is found_via `via` (rejudge, reconcile)."""

        def __init__(self, root: Path, *, plan: round1.Plan, via: str, clock: Callable[[], float] = wall,
                     decide=profiles.decision) -> None:
            super().__init__(root, plan=plan, clock=clock, client_factory=_no_client, resolver=_no_lookup,
                             netcheck=lambda: True, campus_url=None, local_address=lambda: None, decide=decide)
            self.via = via
            self.frontier.close()
            self.frontier = _QuietFrontier(Path(root) / "frontier.db")
            self.chains = _RejudgeChains(self.frontier, root, clock=clock)

        def queue_of(self, host: str) -> str:
            row = self.frontier.host(host)
            if row is not None and row["queue"] not in ("?", "", None):
                return row["queue"]
            named = google_api.queue_for(host)
            if named is not None:
                self.frontier.set_host(host, queue=named, dns_at=self.clock(), dns_fail=0)
                return named
            self.frontier.set_host(host, queue="?")
            return "?"

        def _enqueue(self, href: str, **kw):
            kw["found_via"] = self.via
            return super()._enqueue(href, **kw)

    _OFFLINE = OfflineCrawler
    return _OFFLINE


@dataclass
class Rejudged:
    sites: list[str]
    pages: int = 0
    shortlinks: int = 0
    unread: list[str] = field(default_factory=list)
    new_rows: int = 0
    r7_rows: int = 0
    thin_set: int = 0
    thin_cleared: int = 0


def _snapshot(row, rec: tuple[str, int, dict[str, str], bytes]) -> tuple[Snapshot, str | None, list[str]]:
    """A stored answer as the crawler's Snapshot again, its text (strict decoding first, as fetch does) and the flags
    _store reads before a page's own (decode; SSO's not-found page fetched directly)."""
    url, status, headers, body = rec
    ctype = headers.get("content-type", "").lower()
    flags: list[str] = []
    sig = outcome.error_signature(row["url"])
    if sig is not None and sig.result == outcome.GONE and sig.flag:
        flags.append(sig.flag)
    meta: dict[str, str] = {}
    try:
        text = decode_body(body, ctype)
    except DecodeError:
        from nknu.archive import crawler as crawl_mod

        text = crawl_mod._lenient(body, ctype)
        meta["decode_error"] = "1"
        flags.append("decode")
    snap = Snapshot(target=Target(url or row["url"], meta=meta), url=url or row["url"], status=status,
                    headers=headers, body=body, text=text, fetched_at=datetime.now(UTC),
                    sha256=row["sha256"] or hashlib.sha256(body).hexdigest(), warc_offset=row["warc_offset"],
                    warc_len=row["warc_len"])
    return snap, text, flags


def _kept(row) -> dict[str, Any]:
    """The columns of a stored row a reading again keeps as they are (fetched_at most of all: a person's mark and
    jscheck's look are about that fetch)."""
    return {"state": "done", "reason": None, "status": row["status"], "final_url": None, "ctype": row["ctype"],
            "bytes": row["bytes"], "sha256": row["sha256"], "warc": row["warc"], "warc_offset": row["warc_offset"],
            "warc_len": row["warc_len"], "fetched_at": row["fetched_at"]}


def rejudge(root: Path, sites: Sequence[str], *, plan: round1.Plan | None = None, clock: Callable[[], float] = wall,
            decide=profiles.decision) -> Rejudged:
    """`nknu archive rejudge --sites …` (design §18.2a step 4): every stored web page (and short link's page) of these
    sites read back from the WARC by its warc_offset and worked out again with today's rules (links.extract,
    round1.admit, pagetypes, the platform profiles and their decisions with each button's text, the presses made:
    _RejudgeChains), what its parts brought read again (Crawler._replay_parts), R7's rows from the school's .js, then
    the thin marks. New addresses are queued (found_via rejudge); nothing is fetched or pressed, no fetched_at
    changes. Refused while a run holds run.lock; writes an event rejudge with the sites it covers (design §10.5:
    a reconciliation before it is stale)."""
    root = Path(root)
    _round1_db(root)
    sites = list(dict.fromkeys(sites))
    with _run_lock(root):
        c = _offline_class()(root, plan=plan if plan is not None else round1.load(), via="rejudge", clock=clock,
                             decide=decide)
        try:
            return _rejudge(c, root, sites)
        finally:
            c.frontier.close()


def _rejudge(c, root: Path, sites: list[str]) -> Rejudged:
    fr, arc = c.frontier, Archive(root)
    out = Rejudged(sites)
    before = fr.q("SELECT count(*) FROM url WHERE found_via='rejudge'")[0][0]
    cache = ScriptCache(fr, arc)
    for site in sites:
        rows = fr.q("SELECT * FROM url WHERE owner_site=? AND state='done' AND status=200 AND warc IS NOT NULL AND "
                    "warc_offset IS NOT NULL AND (kind='page' OR (kind=? AND sniff='html')) ORDER BY id",
                    (site, outcome.SHORTLINK))
        for row in rows:
            rec = arc.record(row["warc"], row["warc_offset"])
            if rec is None:
                out.unread.append(row["url"])
                continue
            snap, text, flags = _snapshot(row, rec)
            cols = _kept(row)
            cols["final_url"] = snap.url
            if row["kind"] == outcome.SHORTLINK:
                c._shortlink(row, snap, cols)
                out.shortlinks += 1
                continue
            cols.update(sniff="html", filename=row["filename"])
            c._page(row, snap, text or "", cols, flags)
            c._walk_check(row["id"])
            out.pages += 1
            again = fr.row_by_id(row["id"])
            if again is not None and again["kind"] == "page" and not set(_flags(again)) & {"login", "soft404"}:
                page = _page_read(again, text or "", snap.url)
                out.r7_rows += write_r7(fr, row["id"], r7_page(fr, again, text or "", snap.url, page, cache).entries)
        thin = thin_pages(fr, root, [site])[site]
        out.thin_set += thin.set_
        out.thin_cleared += thin.cleared
    out.new_rows = fr.q("SELECT count(*) FROM url WHERE found_via='rejudge'")[0][0] - before
    fr.event("info", "rejudge", json.dumps({"sites": sorted(sites), "pages": out.pages, "shortlinks": out.shortlinks,
                                            "unread": len(out.unread), "new_rows": out.new_rows}, ensure_ascii=False))
    return out


def read_list(path: Path) -> list[str]:
    """A list of addresses (one a line; what follows a tab, and a line starting with #, is a person's note): the
    -found.txt a reconcile wrote, as a person kept it."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("\t", 1)[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def enqueue(root: Path, urls: Iterable[str], *, plan: round1.Plan | None = None) -> list[tuple[str, str]]:
    """`nknu archive reconcile --enqueue <清单>` (design §18.2b): the addresses a person confirmed, and only those,
    admitted by today's rules (round1.admit) and queued, found_via reconcile; the next run fetches them. Refused
    while a run holds run.lock. Returns (address, the link table's kind of verdict: queued, dup, round2:<why>,
    out:<rule> …)."""
    root = Path(root)
    _round1_db(root)
    urls = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))
    with _run_lock(root):
        c = _offline_class()(root, plan=plan if plan is not None else round1.load(), via="reconcile")
        try:
            got = [(u, c.enqueue(u, depth=1, found_via="reconcile")) for u in urls]
            c.frontier.event("info", "reconcile_enqueue", json.dumps(
                {"n": len(got), "queued": sum(v == "queued" for _u, v in got)}, ensure_ascii=False))
            return got
        finally:
            c.frontier.close()


# ---------------------------------------------------------------- a person's mark (design §10.2.1)

MARKS = (outcome.SCHOOL_SIDE,)


def review_mark(root: Path, url_id: int, *, note: str, mark: str = outcome.SCHOOL_SIDE) -> str:
    """`nknu archive review --mark school_side <id> --note …` (design §10.2.1): a person's word that a row's result
    is the school's to answer for (Frontier.mark_school_side: only a markable result, outcome.markable; only with a
    note). Refused while a run holds run.lock. Returns the result marked; ValueError for a mark, a row or a result
    that may not be marked."""
    if mark not in MARKS:
        raise ValueError(f"the only mark is {', '.join(MARKS)} (got {mark!r})")
    if not (note or "").strip():
        raise ValueError("a mark says why (--note)")
    root = Path(root)
    db = _round1_db(root)
    with _run_lock(root):
        fr = Frontier(db)
        try:
            result = fr.mark_school_side(int(url_id), note)
            row = fr.row_by_id(int(url_id))
            fr.event("info", "review_mark", json.dumps({"url_id": int(url_id), "mark": mark, "result": result},
                                                       ensure_ascii=False), row["host"] if row else None,
                     row["url"] if row else None)
            return result
        finally:
            fr.close()


def markable_rows(fr: Frontier, site: str | None = None) -> list[Mapping[str, Any]]:
    """The rows a person may mark as the school's now (outcome.markable), with their result, reason and status: what
    `review --mark` would take. A redirect is marked by what it leads to as a page's resource (page_resource); one no
    page uses, by what it comes to as a round-1 row of its own (monitor.orphans: an attachment sent to the home page is
    not_file), which this lists too."""
    only, args = ("AND owner_site=?", (site,)) if site else ("", ())
    rows = fr.q(f"SELECT id, url, owner_site, result, reason, status FROM url_result WHERE result IN "
                f"({','.join('?' * len(outcome.SCHOOL_SIDE_RESULTS))}) {only} ORDER BY id",
                (*outcome.SCHOOL_SIDE_RESULTS, *args))
    out: list[Mapping[str, Any]] = [r for r in rows if outcome.markable(r["result"], r["reason"])]
    for s, lone in sorted(monitor.orphans(fr).items()):
        if site and s != site:
            continue
        out += [{"id": o.id, "url": o.url, "owner_site": o.site, "result": o.result, "reason": o.reason,
                 "status": o.status} for o in lone if o.moved and outcome.markable(o.result, o.reason)]
    return sorted(out, key=lambda r: r["id"])


# ---------------------------------------------------------------- a menu item that is the school's broken link (O1)

MENU_MARK_EVENT = monitor.MENU_MARK_EVENT


@dataclass(frozen=True)
class MenuLink:
    """A menu item of a round-1 page that is the school's broken link (R1 bad_link: a school error page's address, no
    url row), with the first page (by id, the one R1 lists) whose menu has it, and that page's fetch."""

    site: str
    target: str
    page_id: int
    page: str
    fetched_at: str | None


def menu_bad_links(fr: Frontier, root: Path, *, site: str | None = None, target: str | None = None
                   ) -> list[MenuLink]:
    """The menu items R1 finds to be the school's broken links (design §10.2.1, §18.2b): every address a round-1 page
    links that the link table says is a school error page's (outcome.ERROR_OUT) and that has no row, with the first
    page of its site (by id, as R1 lists it) whose menu has it, read back from the WARC (only the pages that link such
    an address). site, target: only these."""
    arc = Archive(root)
    where, args = "", [outcome.ERROR_OUT]
    if site:
        where, args = where + " AND u.owner_site = ?", [*args, site]
    if target:
        where, args = where + " AND l.dst = ?", [*args, target]
    out = []
    for s, t in fr.q(f"SELECT DISTINCT u.owner_site, l.dst FROM link l JOIN url u ON u.id = l.src WHERE l.verdict = ? "
                     f"AND u.owner_site IS NOT NULL {where} ORDER BY u.owner_site, l.dst", args):
        if _menu_status(fr, t) != "bad_link":
            continue
        for row in fr.q(f"{_PAGES.replace(' ORDER BY id', '')} AND id IN (SELECT src FROM link WHERE dst = ? AND "
                        "verdict = ?) ORDER BY id", (s, t, outcome.ERROR_OUT)):
            if set(_flags(row)) & {"login", "soft404"} or (rec := arc.record(row["warc"], row["warc_offset"])) is None:
                continue
            html = postback.as_text(rec[3], rec[2].get("content-type"))
            if t in _menu_targets(_page_read(row, html, rec[0] or row["url"])):
                out.append(MenuLink(s, t, row["id"], row["url"], row["fetched_at"]))
                break
    return out


def review_menu_mark(root: Path, target: str, *, note: str, site: str | None = None,
                     mark: str = outcome.SCHOOL_SIDE) -> list[MenuLink]:
    """`nknu archive review --mark school_side --menu <网址> --note …` (design §10.2.1, the owner's 10-26 decision): a
    person's word that a menu item R1 lists as the school's broken link (bad_link) is the school's to answer for, the
    rules of a row's mark kept: only bad_link, only with a note, void once the page it was seen on is fetched again
    (its fetched_at kept: monitor.menu_marks), listed all the same. It has no url row, so the mark is an event
    (menu_mark), one for each site whose menu has it (site: only that one). Refused while a run holds run.lock.
    Returns what was marked; ValueError for a mark, an address or a note that may not be."""
    if mark not in MARKS:
        raise ValueError(f"the only mark is {', '.join(MARKS)} (got {mark!r})")
    if not (note or "").strip():
        raise ValueError("a mark says why (--note)")
    root = Path(root)
    db = _round1_db(root)
    canon = scope.canonical(target) or target
    with _run_lock(root):
        fr = Frontier(db)
        try:
            status = _menu_status(fr, canon)
            if status != "bad_link" or not outcome.markable("bad_link", None):
                raise ValueError(f"{canon} comes to {status} as a menu item: only the school's broken link (bad_link: "
                                 "a school error page's address, no row) may be marked")
            got = menu_bad_links(fr, root, site=site, target=canon)
            if not got:
                raise ValueError(f"{canon} is on no round-1 page's menu{' of ' + site if site else ''}: R1 reads "
                                 "menus only, so there is nothing of it to mark")
            for m in got:
                fr.event("info", MENU_MARK_EVENT, json.dumps(
                    {"site": m.site, "target": m.target, "page_id": m.page_id, "page": m.page,
                     "fetched_at": m.fetched_at, "mark": mark, "result": "bad_link", "note": note.strip()},
                    ensure_ascii=False), urlsplit(m.target).hostname, m.target)
            return got
        finally:
            fr.close()


# ---------------------------------------------------------------- the report


def _r1_line(i: Mapping[str, Any]) -> str:
    """One menu item R1 lists, in words: 学校的链接坏了（要人看）, 学校那边的问题（人看过） with the person's note, …."""
    st = i.get("status")
    line = f"- {R1_STATUS_ZH.get(st, st)}：{i.get('target')}（在 {i.get('page')} 的选单上）"
    if st == outcome.SCHOOL_SIDE:
        line += f"；理由：{i.get('note') or '—'}（{i.get('marked_at') or '—'} 标记）"
    elif st == "bad_link":
        line += "；人看过的用 nknu archive review --mark school_side --menu <网址> --note … 标记"
    return line


def report_text(got: Reconciled) -> str:
    """reports/reconcile-<批>-<时间>.md (design §18.2b), in plain words: each site's checks, then every item."""
    w = [f"# 对账 {got.batch}（{got.at}）", ""]
    ok = [s.site for s in got.sites if s.gates_ok]
    w.append(f"网站 {len(got.sites)} 个，关卡全过 {len(ok)} 个。" + ("" if got.r6 else
                                                               "这一次没有做浏览器抽查（R6），所以没有网站会是「✓ 爬完」。"))
    w.append(f"关卡：{'、'.join(GATES)} 全过才算过；R3b、R4b 只是估计，只提醒。")
    for s in got.sites:
        blind = s.uncompared()
        w += ["", f"## {s.site}：{'关卡全过' if s.gates_ok else '还有没过的关卡'}"
                  + (f"（没比到的：{'、'.join(blind)}，不等于没问题）" if blind else ""), ""]
        w += ["| 项目 | 结果 | 数字 | 说明 |", "|---|---|---|---|"]
        for name, ck in s.checks.items():
            nums = "、".join(f"{k} {v:.0%}" if k == "cover" and isinstance(v, float) else f"{k} {v}"
                            for k, v in ck.counts.items() if v is not None)
            label = STATUS_LABEL.get(ck.status, ck.status) + ("（没比到）" if name in blind else "")
            w.append(f"| {name} {CHECK_LABEL.get(name, '')} | {label} | {nums} | {ck.note} |")
        if s.thin_set or s.thin_cleared:
            w.append(f"\n「页面几乎是空的」记号：新加 {s.thin_set} 页，拿掉 {s.thin_cleared} 页。")
        if s.r7_rows:
            w.append(f"R7 读学校的 .js 写进这个网站各页的行：{s.r7_rows} 行（page_res.zone 以 r7: 开头）。")
        for name, ck in s.checks.items():
            if ck.items:
                w += ["", f"### {name} {CHECK_LABEL.get(name, '')}", ""]
                w += [_r1_line(i) if name == "R1" else f"- {json.dumps(i, ensure_ascii=False)}" for i in ck.items]
        if s.suggestions:
            w += ["", "### R7 给平台档案的决定建议", "", "| 写法 | 建议 | 理由 | 页数 | 例子 |", "|---|---|---|---|---|"]
            w += [f"| `{x.key}` | {x.decision} | {x.why} | {x.pages} | {x.sample} |" for x in s.suggestions]
        if s.found:
            w += ["", "### 对账发现爬虫没找到的网址（要人看）", "",
                  "要抓的留在 -found.txt 里，用 `nknu archive reconcile --enqueue <清单>` 写进待抓清单；不用抓的在这里写理由。", ""]
            w += [f"- [{f.check}] {f.url}（{f.why}）" for f in s.found]
    return "\n".join(w) + "\n"
