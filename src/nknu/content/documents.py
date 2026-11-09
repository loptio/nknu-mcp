"""Corpus records -> documents: one per notice, page, attachment, outside link and calendar event (skeleton spec:
「一个网页算一份，一个附件也算一份」). List pages and albums are left out and counted, as are images only menus carry
(MENU_IMAGE, decided by assemble: I4), duplicate notices (one site and nid: ids.location_key, plan 2026-11-08 T1),
records with neither title nor text and calendar events that start before CALENDAR_SINCE, so nothing goes missing
silently. Records of one identity have one id (plan 2026-11-08 T1): one_per_id keeps one of them before this.

The school's deleted notices (owner 11-09, blueprint item 15, 「照建议定」): a notice the school deleted leaves only the
site's template, which went in as a notice with neither text nor date (教务处 27, 体育室 27); 「建议不收、在每站报告里记
个数（学校删掉的公告不该被搜到），照导出标的『内文区是空的』做」. A record the export marked so (Record.area_mark,
corpus.AREA_MARKS) is no document (deleted_notice): counted as <kind>(学校删掉), never kept for its address nor counted
as (重复), and the build writes each site's count to sites.deleted_notices. AREA_EMPTY (the platform's own title and
text areas on the page, and empty) always; AREA_MISSING (neither area on the page) only with no text read either,
since a template the export's profile does not know yet looks the same (review 11-09) 【Claude】.

Every platform (plan 2026-11-08 T3; kind_class): a notice is a page of c.nknu's notice templates (newsdetail, newsview)
or a detail page of any profiled platform (detail:<profile>, a press-made one too); a list read from its page
(list:<profile>, pagetext.list_grid) and a list's pager row (…#翻页) are left out like c.nknu's lists. A document's unit
is the site table's (nknu.content.sites.unit_for: its own URL's site, else its export's), an event's its export's.
Dates: a c.nknu notice takes its date field (corpus.read_metas: 公告頁日期欄); any other notice, and a page, takes the
date the export read on its page (网页上写的日期, source 網頁上寫的日期: _stated) when it read it after a label of
putting the page up or changing it (公告、發佈、更新 …, never a bare 日期: _OWN_LABEL), a page only as its own date.

Sub-step 4 (spec 2026-11-08 §四), the step-3 export's columns ('' in an export made before 3c33a79, which then dates
and classes everything as above): a date the export read in the platform's own date field (日期来源 详细页的日期栏,
source 公告頁日期欄) or on the notice's row of its list (列表上的日期, source 列表上的日期) needs no label, the
crawler's platform rule having chosen the field (spec §二 2); and a page it read such a field on is a notice (_class:
a detail page whose address no detail path names, GEO, ID, WordPress profiles: 2,253 pages on 11-09) 【Claude】. Every
document carries the server's time of its file (最后修改时间, Document.server_date, spec §二 3), which the build dates
only an attachment by.

An attachment hangs on the latest-dated page linking to it (a notice's date; undated notices, then pages, after;
corpus order on ties) and takes that page's date (addendum 2026-11-02 B; 11-01: with several pages, the latest). The
assembled corpus (T7a) keeps apart the pages that link it only from a menu (nav_linked): they are references, never
its parent. One linked only from a left-out list page (album_images) has no parent but keeps that page's title as
parent_title, so the index still knows its context. Its title is what its latest-dated linking page calls it
(Record.contexts; the most frequent name among pages of that date), the file name otherwise; a title taken from a page
keeps the file name in linktext. refs: every page document linking it, with the zone and the name.
組別: a notice's own group field; a page's from the gi URL parameter, mapped by what the notices of its own site with
that gi say (gi 2 is 和平教務組 on 教務處's site, another group elsewhere: plan 2026-11-08 T1); an attachment's from its
parent when the parent is of its own unit; else (no parent, or another unit's page: another site's later re-post of the
file) from the latest-dated page of its own unit linking it that has one, then from the gi of the pages of its own unit
linking to it (menus included) (plan 2026-11-08 T4's pilot check: 體育室's PDF a 教務處 notice links took that
notice's 課務組; its review: 教務處's file a 生輔組 re-post links later lost its 和平教務組) 【Claude】.

Many exports (plan 2026-11-08 T4): the build reads several, so one identity can come from each (a file two sites link,
the SSO news mirror, one calendar two sites show). one_per_id keeps the record of the newest 抓取时间 (an &amp;
spelling still gives way first; on a tie, _rank: the export of the address's own site unless that site is a shared
platform, then any other export, the one that brought it, a shared platform's own export last; then the first in input
order) with what all of them say; one_event_per_id keeps one event per id the same way. A document's site is the
export its record or event came from (docs.site), which the sites table dates.
"""

from __future__ import annotations

import dataclasses
import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from nknu.archive import profiles, scope
from nknu.content import ids, sites
from nknu.content.corpus import AREA_EMPTY, AREA_MARKS, NOTICE_KINDS, Event, PageMeta, Record, plausible, taipei_day
from nknu.content.dates import FIELD_SOURCE, LIST_ROW, LIST_SOURCE, OWN_FIELD, PAGE_SOURCE, kept_range
from nknu.core.timeutil import TAIPEI

EVENT_KIND = "行事历"  # kind_raw of calendar events (the export's data file name)
# c.nknu's list pages by their 10-29 names (its pages keep them: pagetext.page_kind)
EXCLUDED_KINDS = frozenset({"newslist", "newslist#翻页/按钮", "album", "album#翻页/按钮", "album_images", "sitemap",
                            "linkslist"})
# page_kind's names on the other profiled platforms (plan 2026-11-08 T2): a detail page (a notice), a list read from
# its page and a list's pager row (left out)
DETAIL, LIST, PAGER = "detail:", "list:", "#翻页"
# the kind assemble gives an image every page referencing it carries only as a picture outside its content area (a
# unit banner, whose OCR text is the unit's name): no document, counted under this name (main session 11-02, I4); a
# file linked only from menus stays a document (a standing file of its unit)
MENU_IMAGE = "只在选单出现的图片"
# prefixes: scanned PDFs, mostly scanned PDFs with a few words (the words are not the document; review 11-01), zip,
# xlsx, ole ...: never shown as 全文; whatever text they have is still indexed and shown as a few words
NO_TEXT_KINDS = ("附件:pdf:无文字层", "附件:pdf:文字很少", "附件:无文字")
BLANK = "(空白)"  # excluded-count suffix: neither title nor text
# excluded-count suffix: a second record of one notice (one site and nid), or of one identity (one id: one_per_id)
DUPLICATE = "(重复)"
# The calendar starts here: the owner's decision of 11-01, a fixed date that never rolls forward with today
# (「不需要滚动算法，就直接从23年开始就好了」). An event whose first day (Asia/Taipei) is earlier is no document; it stays
# in the export and is counted as 行事历(2023年以前).
CALENDAR_SINCE = date(2023, 1, 1)
BEFORE = f"({CALENDAR_SINCE.year}年以前)"  # excluded-count suffix: an event before CALENDAR_SINCE (a 1 January)
FORMAT = "(其他格式)"  # excluded-count suffix: a format sibling assemble folded into another record (addendum B)
DELETED = "(学校删掉)"  # excluded-count suffix: a notice the school deleted, only the template left (deleted_notice)
UNKNOWN_ZONE = "unknown"  # a linking page whose link to the file was not found (a button), or the 10-29 corpus
ZONE_ORDER = {"attachments": 0, "content": 1, UNKNOWN_ZONE: 2, "nav": 3}  # the zone a page's reference is shown with

_HSPACE = re.compile(r"[ \t\v\xa0\u1680\u2000-\u200a\u202f\u205f\u3000]+")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_BLANK_LINES = re.compile(r"\n{3,}")
# the size the site writes after each attachment link, 「115-1停修公告.pdf (619775 Kb)」: no word of the document
_FILE_SIZE = re.compile(r"\s*[(（]\s*\d+(?:\.\d+)?\s*(?:[KkMmGg]?[Bb]|bytes?)\s*[)）]$")
LINK_SEP = " | "  # how the export joins the texts of all the links to one file (archive.export._joined)
# The export's labels of putting a page up or changing it (archive.export._STRONG: 發佈、发布、公告、更新、張貼、刊登, with
# 日期 or 時間 after them or not; Posted, Published, Updated). Its 日期原文 starts at the label it read the date after,
# so one starting with these is a date the page gives itself. One after a bare 日期 (the export's weaker label) is no
# date of the page: the label is the tail of a word that names another day, 校慶日期, 申請日期 (a deadline), 修正日期
# (a rule's); 教务处's three such dates were all of that kind (plan 2026-11-08 T3 review) 【Claude】
_OWN_LABEL = re.compile(r"(?i)^\s*(?:發\s*[佈布]|发\s*布|公\s*告|更\s*新|張\s*貼|张\s*贴|刊\s*登|posted|published|updated?)")
# the export's 日期来源 that need no label (spec 2026-11-08 §二 2: the crawler's platform rule chose the field), with the
# source each date gets
_TRUSTED = {OWN_FIELD: FIELD_SOURCE, LIST_ROW: LIST_SOURCE}
# A 最后修改时间 this close to the row's 抓取时间 is the server's clock answering a page it made on the fly, not the
# time of a file: 825 pages of geo.nknu.edu.tw, 2 附件 and 2 图片 elsewhere in the 111 exports of 11-09 (sub-step 4,
# spec 2026-11-08 §四) 【Claude】; so is one after it (a server clock running fast: a file is never changed after the
# answer that carried it, and HTTP forbids a Last-Modified later than the answer's Date; none in the local crawls)
CLOCK_SECONDS = 120


@dataclass
class Document:
    doc_id: str
    kind_class: str  # notice | page | attachment | event | link
    kind_raw: str
    title: str
    url: str
    unit: str
    grp: str
    parent_id: str | None
    parent_title: str
    linktext: str
    has_text: bool
    text: str
    linked_from: tuple[str, ...] = ()  # doc_ids of linking docs, corpus order
    added: date | None = None  # notices only: the date the notice states, read where added_source says
    added_raw: str = ""
    # dates.FIELD_SOURCE (a notice's date field), dates.LIST_SOURCE (its list row's: sub-step 4), dates.PAGE_SOURCE
    # (plan 2026-11-08 T3)
    added_source: str = ""
    # pages only (plan 2026-11-08 T3): the date the export read on the page (dates.PAGE_SOURCE), the page's own date;
    # never a parent's date for the files it links, nor what orders the pages naming a file (only a notice's date does,
    # as before: 教务处's files keep their parents, titles and dates 【Claude】)
    page_date: date | None = None
    page_date_raw: str = ""
    page_date_source: str = ""  # the source of page_date (_stated): dates.PAGE_SOURCE, or LIST_SOURCE (sub-step 4)
    # every document (sub-step 4, spec 2026-11-08 §二 3): the server's time of its file (_server_day: the export's
    # 最后修改时间), which dates an attachment only (owner 11-01: a file inherits its page's date, else the server's
    # time; a page is never dated by it); None without one
    server_date: date | None = None
    source: str = ""  # where the text came from (assembled corpus): totext | fallback-1029 | none
    refs: tuple[tuple[str, str, str], ...] = ()  # attachments: (doc_id, zone, context) of each page document linking it
    title_from_context: bool = False  # the title is what a page calls it (addendum B), not its file name
    file_title: str = ""  # the file name, when the title came from a page
    formats: tuple[dict, ...] = ()  # format siblings assemble folded into it (shown as its copies)
    duplicates: tuple[str, ...] = ()  # doc_ids of the notices left out as (重复) of it (the build's doc_alias)
    site: str = ""  # the site key of the export its record or event came from (plan 2026-11-08 T4: docs.site)


def clean_text(s: str) -> str:
    """Whitespace only, every other character kept: CR and form feed become newlines, runs of spaces and tabs one space,
    lines trimmed, at most one blank line in a row. The 10-29 text is often more whitespace than words."""
    s = _ZERO_WIDTH.sub("", s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n"))
    s = "\n".join(_HSPACE.sub(" ", line).strip() for line in s.split("\n"))
    return _BLANK_LINES.sub("\n\n", s).strip()


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _link_text(s: str) -> str:
    """One line, without the file size the site writes at the end of each link's text (fix round 2, 11-01); nothing
    else changes."""
    parts = (_FILE_SIZE.sub("", p) for p in _one_line(s).split(LINK_SEP))
    return LINK_SEP.join(p for p in parts if p)


def kind_class(kind: str) -> str | None:
    """notice, page, attachment or link for a record kind (pagetext.page_kind's for a page), None for a left-out one
    (plan 2026-11-08 T3): notice for c.nknu's notice templates (newsview too, the template of 总务处, 体育室 and 师培处)
    and every profiled platform's detail page (detail:<profile>, detail:<profile>#按钮); left out a list read from its
    page and a list's pager row (list:<profile>, …#翻页), c.nknu's list names (EXCLUDED_KINDS) and an image only menus
    carry (MENU_IMAGE); attachment for 附件…; link for 外站:<host>; page for the rest (page:<profile>, page, c.nknu's
    other names)."""
    if kind in EXCLUDED_KINDS or kind == MENU_IMAGE or kind.startswith(LIST) or kind.endswith(PAGER):
        return None
    if kind in NOTICE_KINDS or kind.startswith(DETAIL):
        return "notice"
    if kind.startswith("附件"):
        return "attachment"
    if kind.startswith("外站"):
        return "link"
    return "page"


def _stated(r: Record) -> tuple[date, str, str] | None:
    """(the date the export read on the record's page, 网页上写的日期, with the words it read it from, 日期原文, and the
    source it is shown with) when that is the page's own date (plan 2026-11-08 T3), else None, rather none than a
    wrong one:
    - read in the platform's own date field (日期来源 OWN_FIELD: source FIELD_SOURCE) or on the notice's row of its
      list (LIST_ROW: LIST_SOURCE), whatever its words (the bare 「2024-05-31」 of a date field): the crawler's platform
      rule chose it (sub-step 4, spec 2026-11-08 §二 2); any other date (PAGE_WORDS, or '' from an export made before
      3c33a79) only after a label of putting the page up or changing it (_OWN_LABEL: 「更新時間： 2025.9.17」, never
      「日期113.4.24」 cut from 「校慶日期113.4.24」) 【Claude】, source PAGE_SOURCE;
    and, for all of them: a plausible date (corpus.plausible) no later than the day the page was fetched (a later one
    is a day the page names ahead, a deadline: 「榜單公告115年12月30日」 【Claude】); and not on a c.nknu list page
    (default.aspx, download.aspx, laws.aspx: the profile's list paths), whose date is one of its rows' (教务处's home
    pages show their first notice's, 「公告 2026.10.21」) 【Claude】, nor on a site's root address (site_root: the home
    page there, on any platform, shows its first notice's too; blueprint item 22, owner 11-09 「照这个做」: 13 sites'
    roots took it on 11-09; the rule's form 【Claude】)."""
    raw = " ".join(r.page_date_raw.split())
    source = _TRUSTED.get(r.date_source)
    if source is None:
        if not _OWN_LABEL.match(raw):
            return None
        source = PAGE_SOURCE
    try:
        day = date.fromisoformat(r.page_date) if r.page_date else None
    except ValueError:
        return None
    if day is None or not plausible(day):
        return None
    if (fetched := taipei_day(r.fetched)) is not None and day > fetched:
        return None
    if profiles.CNKNU.covers(scope.site_key(r.url)) and profiles.page_kind(r.url) == "list":
        return None
    if site_root(r.url, r.site):
        return None
    return day, raw, source


def site_root(url: str, site: str) -> bool:
    """Whether url is the root address of site (a site key): https://c.nknu.edu.tw/tcsl, …/tcsl/,
    https://clcts.nknu.edu.tw/ (the site's home page; its fragment aside, a pager row #第2页 too), its host compared
    as scope.site_key reads it (no port). Never with a query: /?p=news&id=1 is a page a site serves at its root path
    (the round-2 sites gauss, ite, ord; 11-09 review). A short link or another site's page in the export is none (its
    address is not the site key) 【Claude】."""
    try:
        parts = urlsplit(url.partition("#")[0])
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return bool(site) and not parts.query and (host + parts.path).rstrip("/").lower() == site.lower()


def _class(r: Record) -> str | None:
    """A record's document class: kind_class of its kind, except that a page the export read the platform's own date
    field on (日期来源 OWN_FIELD) is a notice: a detail page whose address no detail path names (GEO, ID, WordPress
    profiles: 2,253 pages on 11-09), dated by that field (sub-step 4, spec 2026-11-08 §四) 【Claude】. Its kind_raw stays.
    One helper for both places that ask (the notices kept at one address, and the documents), so they never disagree."""
    cls = kind_class(r.kind)
    return "notice" if cls == "page" and r.date_source == OWN_FIELD else cls


def deleted_notice(r: Record) -> bool:
    """A notice the school deleted (owner 11-09, blueprint item 15, 「照建议定」), by the export's mark on its page
    (Record.area_mark), whatever its class (a record the build leaves out by its kind aside):
    - AREA_EMPTY: the platform's own title and text areas are on the page and hold nothing (no words, picture, link,
      frame or file): one, whatever the content side read there (a platform read as a whole body gives its menus:
      电机系's 3 of 11-09, page:elec, whose bodies hold 887 to 891 characters of menu);
    - AREA_MISSING: neither area is on the page: one only when the to-text step read no text either, since a template
      the export's profile does not know yet looks the same (the 11-08 export marked 156 real tecs notices so, before
      3c33a79 gave CNKNU tecs's newsview areas; on 11-09 it marks tecs's &amp;-spelled address, which shows a list).
    review 11-09 【Claude】."""
    if _class(r) is None or r.area_mark not in AREA_MARKS:
        return False
    return r.area_mark == AREA_EMPTY or not clean_text(r.text)


def _server_day(r: Record) -> date | None:
    """The Asia/Taipei day of the record's 最后修改时间 (Record.modified; spec 2026-11-08 §二 3); None when it is blank or
    unreadable, outside dates.kept_range(), or at most CLOCK_SECONDS before its 抓取时间 or after it (the server's
    clock answering a page made on the fly, or running fast; not a file's time) 【Claude】. Without an 抓取时间 (a
    corpus assembled before plan 2026-11-08 T3) there is nothing to compare it with."""
    day = taipei_day(r.modified) if r.modified else None
    if day is None:
        return None
    earliest, latest = kept_range()
    if not earliest <= day <= latest:
        return None
    fetched = fetched_at(r.fetched)
    if fetched != float("-inf") and fetched_at(r.modified) >= fetched - CLOCK_SECONDS:
        return None
    return day


def _param(url: str, name: str) -> str | None:
    values = parse_qs(urlsplit(html.unescape(url)).query).get(name)
    return values[0] if values else None


def _gi(url: str) -> tuple[str, str] | None:
    """(the URL's site, its gi), the key a gi's group is voted by (plan 2026-11-08 T1); None without a gi."""
    gi = _param(url, "gi")
    return (scope.site_key(html.unescape(url)), gi) if gi else None


def _link_key(url: str) -> str:
    """The identity of an address (ids.location_key, plan 2026-11-08 T1): a c.nknu notice is one page per site and
    nid whatever its gi or &amp; spelling (aca's nid 578 is not ccee's), http and https are one; links.page_links
    spells its targets with it, so they compare with a manifest 网址 key for key."""
    return ids.location_key(url)


def _joined(keep: Record, others: list[Record]) -> Record:
    """keep with what the other records of its identity say of it: their pages (a page some record links from its text
    or attachment area is a text reference, else a menu one), the best zone of each page (ZONE_ORDER), the first
    context and row each page gives, their link texts and their formats."""
    linked, nav = list(keep.linked), list(keep.nav_linked)
    zones, contexts, rows = dict(keep.zones), dict(keep.contexts), dict(keep.rows)
    texts, formats = [t for t in keep.linktext.split(LINK_SEP) if t], list(keep.formats)
    for other in others:
        for u in other.linked:
            if u not in linked:
                linked.append(u)
        for u in other.nav_linked:
            if u not in nav:
                nav.append(u)
        for u, zone in other.zones.items():
            if ZONE_ORDER.get(zone, 9) < ZONE_ORDER.get(zones.get(u, ""), 9):
                zones[u] = zone
        for mine, theirs in ((contexts, other.contexts), (rows, other.rows)):
            for u, value in theirs.items():
                mine.setdefault(u, value)
        texts += [t for t in other.linktext.split(LINK_SEP) if t and t not in texts]
        formats += [f for f in other.formats if f.get("id") not in {g.get("id") for g in formats}]
    return dataclasses.replace(keep, linked=tuple(linked), nav_linked=tuple(u for u in nav if u not in linked),
                               zones=zones, contexts=contexts, rows=rows, linktext=LINK_SEP.join(texts),
                               formats=tuple(formats))


def fetched_at(stamp: str) -> float:
    """An 抓取时间 (ISO date-time) as a POSIX timestamp, a naive one read as Asia/Taipei time 【Claude】; -inf when it is
    missing or none (a corpus assembled before plan 2026-11-08 T3, a hand-made event): the oldest."""
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return float("-inf")
    return (when if when.tzinfo is not None else when.replace(tzinfo=TAIPEI)).timestamp()


def _rank(r: Record) -> int:
    """Which export's record of one identity stays on a tie of 抓取时间 (one_per_id; plan 2026-11-08 T4 review)
    【Claude】, the lower first:
    - 0: the export of the site its own address belongs to (scope.site_key, as sites.unit_for reads it), whose 资料截至
      then dates it, when that site is no shared platform;
    - 1: any other export, the one that brought it (教务处's notice files on the announcement platform);
    - 2: a shared platform's own export (sites: shared, the announcement system and SSO's common parts). Its record
      would take the platform's placeholder unit (秘書室, or the school, which gives no unit words) and lose the 組別
      and the 资料截至 of the unit that brought it, against T3's rule (sites.unit_for: what another site's export
      brought from a shared platform is that site's unit's)."""
    if not r.site or scope.site_key(r.url) != r.site:
        return 1
    row = sites.table().rows.get(r.site)
    return 2 if row is not None and row.shared else 0


def one_per_id(records: list[Record]) -> tuple[list[Record], dict[str, int]]:
    """One record per id (plan 2026-11-08 T1): an id is its record's identity, so rows of one identity (an &amp;
    spelling, http and https, the SSO news mirror, a file two sites' exports share) are one document. One stays, in its
    own place, joined with what the others say (_joined); the others are counted as <kind>(重复). A corpus with ids
    of its own (the 10-29 one) has none of them. Which one stays (plan 2026-11-08 T4: across the build's inputs, the
    newest 抓取时间), in this order 【Claude】:
    - one written without &amp; (T1's rule: the address shown; 教务处's export has 4 such pairs, the &amp; one fetched
      later in each, and keeps its plain spellings as before);
    - the newest 抓取时间 (fetched_at: a record without one is the oldest);
    - on a tie (one archive's row exported by two sites: the lab PC's normal case), _rank: its own site's export, then
      the export that brought it, a shared platform's own export last (T4 review: 教务处's 35 files on the
      announcement platform stay 教務處's, with their 組別, once the platform's export is in the build too);
    - the first in the records' order (the inputs' order, then corpus order: T1's rule).
    A newer row of a shared platform's own export still wins, with the platform's unit (exports of one archive at one
    time never differ in 抓取时间; exports made on different days only come with the daily updates, deferred)."""
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(r.id, []).append(r)
    if len(groups) == len(records):
        return records, {}
    place = {id(r): i for i, r in enumerate(records)}
    keep = {key: min(group, key=lambda g: ("&amp;" in g.url, -fetched_at(g.fetched), _rank(g), place[id(g)]))
            for key, group in groups.items() if len(group) > 1}
    excluded: Counter[str] = Counter()
    out = []
    for r in records:
        kept = keep.get(r.id, r)
        if r is kept:
            out.append(_joined(r, [g for g in groups[r.id] if g is not r]) if r.id in keep else r)
        else:
            excluded[r.kind + DUPLICATE] += 1
    return out, dict(excluded)


def one_event_per_id(events: list[Event]) -> tuple[list[Event], dict[str, int]]:
    """One event per id (plan 2026-11-08 T4; spec 2026-11-08 附「行事历按事件去重」): every export holding one calendar
    (a site that shows 教务处's) brings each of its events. The one of the newest fetch of its calendar file stays
    (Event.fetched; fetched_at), the first in the events' order on a tie (one archive's row in two exports) 【Claude】;
    the others are counted as 行事历(重复). The kept ones keep their order."""
    best: dict[str, int] = {}
    for i, ev in enumerate(events):
        j = best.get(ev.id)
        if j is None or fetched_at(ev.fetched) > fetched_at(events[j].fetched):
            best[ev.id] = i
    kept = [ev for i, ev in enumerate(events) if best[ev.id] == i]
    left = len(events) - len(kept)
    return kept, ({EVENT_KIND + DUPLICATE: left} if left else {})


def _context_title(named: list[tuple[date | None, str]]) -> str:
    """The name the latest-dated pages give (undated pages last); among pages of that date the most frequent, the
    first named on ties. named: (page date, name) of the pages that name the file."""
    if not named:
        return ""
    top = max(day or date.min for day, _ in named)
    return Counter(name for day, name in named if (day or date.min) == top).most_common(1)[0][0]


def build_documents(records: list[Record], metas: dict[str, PageMeta], events: list[Event],
                    site: str = "") -> tuple[list[Document], dict[str, int]]:
    """Documents in corpus order, then events; and the count of left-out records by kind. records: one per id
    (one_per_id); metas: the notice pages' header fields keyed by Record.id (corpus.read_metas). A notice the school
    deleted (deleted_notice) is left out and counted as <kind>(学校删掉). Of the other notices at one address
    (_link_key: one site and nid), the first written without &amp; is kept; the others are (重复) and their doc_ids go
    into its duplicates. site: the export's site key, for a record or an event that names none of its own
    (a corpus assembled before plan 2026-11-08 T3, an event made by hand); with neither, the unit is the school's."""
    excluded: Counter[str] = Counter()
    keep: dict[str, str] = {}  # link key -> the notice kept: the first one whose URL is written without &amp;
    # a notice the school deleted is never the one kept for its address (owner 11-09, blueprint item 15)
    notices = (r for r in records if _class(r) == "notice" and not deleted_notice(r))
    for r in sorted(notices, key=lambda r: "&amp;" in r.url):
        keep.setdefault(_link_key(r.url), r.id)

    docs: list[Document] = []
    doc_of: dict[str, Document] = {}  # record id -> its document
    duplicate_of: dict[str, str] = {}  # record id of a (重复) notice -> the record id kept for it
    order = {r.id: i for i, r in enumerate(records)}
    linkable: dict[str, list[Record]] = {}  # link key -> every record at that address, kept or not
    for r in records:
        key = _link_key(r.url)
        linkable.setdefault(key, []).append(r)
        cls = _class(r)
        if cls is None:
            excluded[r.kind] += 1
            continue
        if deleted_notice(r):  # before the duplicate check: a shell is counted as deleted, never as (重复)
            excluded[r.kind + DELETED] += 1
            continue
        if cls == "notice" and keep.get(key, r.id) != r.id:
            excluded[r.kind + DUPLICATE] += 1
            duplicate_of[r.id] = keep[key]
            continue
        m = metas.get(r.id) if cls == "notice" else None
        title = _one_line(m.title if m and m.title else r.title)
        text = clean_text(r.text)
        if not title and not text:
            excluded[r.kind + BLANK] += 1
            continue
        stated = _stated(r) if cls in ("notice", "page") else None
        added, added_raw, added_source = None, m.added_raw if m else "", ""
        if m and m.added:  # c.nknu's date field first: the export's dates misdated 16% of 教务处's notices (11-02)
            added, added_source = m.added, FIELD_SOURCE
        elif cls == "notice" and stated:
            added, added_raw, added_source = stated
        page_date, page_raw, page_source = stated if cls == "page" and stated else (None, "", "")
        d = Document(doc_id="d" + r.id, kind_class=cls, kind_raw=r.kind, title=title, url=r.url,
                     unit=sites.unit_for(r.url, r.site or site), grp=m.group if m else "", parent_id=None,
                     parent_title="", linktext=_link_text(r.linktext),
                     has_text=bool(text) and not r.kind.startswith(NO_TEXT_KINDS), text=text, added=added,
                     added_raw=added_raw, added_source=added_source, page_date=page_date, page_date_raw=page_raw,
                     page_date_source=page_source, server_date=_server_day(r), source=r.source, formats=r.formats,
                     site=r.site or site)
        docs.append(d)
        doc_of[r.id] = d
        for f in r.formats:
            excluded[(f.get("kind") or r.kind) + FORMAT] += 1
    for dup, kept in duplicate_of.items():
        if (d := doc_of.get(kept)) is not None:
            d.duplicates += ("d" + dup,)

    votes: dict[tuple[str, str], Counter[str]] = {}
    for d in docs:
        if d.kind_class == "notice" and d.grp and (gi := _gi(d.url)):
            votes.setdefault(gi, Counter())[d.grp] += 1
    gi_group = {gi: c.most_common(1)[0][0] for gi, c in votes.items()}
    for d in docs:
        if d.kind_class in ("page", "link"):
            d.grp = gi_group.get(_gi(d.url), "")

    def unit_of(lr: Record) -> str:
        """The unit of a linking page: its document's, else (a page left out: a list) the site table's."""
        return doc_of[lr.id].unit if lr.id in doc_of else sites.unit_for(lr.url, lr.site or site)

    def at(urls) -> list[Record]:
        """Every record at these addresses, kept or not, in corpus order."""
        return sorted({lr.id: lr for u in urls for lr in linkable.get(_link_key(u), [])}.values(),
                      key=lambda lr: order[lr.id])

    def latest(k: Document) -> tuple:
        """The order a file's linking pages are taken in (addendum 2026-11-02 B; 11-01: the latest): a notice's date,
        an undated notice before a page, the first in corpus order on ties."""
        return k.added or date.min, k.kind_class == "notice", -order[k.doc_id[1:]]

    for r in records:
        d = doc_of.get(r.id)
        if d is None or d.kind_class != "attachment":
            continue
        linking = at(r.linked)
        kept = [doc_of[lr.id] for lr in linking if lr.id in doc_of]
        d.linked_from = tuple(k.doc_id for k in kept)
        pages = [k for k in kept if k.kind_class in ("notice", "page")]
        parent = max(pages, key=latest, default=None)
        if parent is not None:
            d.parent_id, d.parent_title = parent.doc_id, parent.title
        else:
            d.parent_title = next((_one_line(lr.title) for lr in linking if lr.title.strip()), "")
        if own_gi := gi_group.get(_gi(r.url), ""):
            d.grp = own_gi
        elif parent is not None and parent.unit == d.unit:
            d.grp = parent.grp
        else:  # no parent, or another unit's (another site's notice linking this file): its own unit's pages only
            mine = max((k for k in pages if k.unit == d.unit and k.grp), key=latest, default=None)
            d.grp = mine.grp if mine is not None else next(
                (gi_group[g] for lr in [*linking, *at(r.nav_linked)]
                 if (g := _gi(lr.url)) in gi_group and unit_of(lr) == d.unit), "")
        named = []  # (the page's date, what it calls the file): pages linking it from their text, never menus
        for u in r.linked:
            if name := _one_line(r.contexts.get(u, "")):
                days = [doc_of[lr.id].added for lr in linkable.get(_link_key(u), []) if lr.id in doc_of]
                named.append((max((x for x in days if x), default=None), name))
        if (title := _context_title(named)) and title != d.title:
            d.file_title, d.title, d.title_from_context = d.title, title, True
            if d.file_title not in d.linktext.split(LINK_SEP):
                d.linktext = LINK_SEP.join(p for p in (d.linktext, d.file_title) if p)
        refs: dict[str, tuple[str, str]] = {}  # doc_id -> (zone, context): the best zone a page shows it in
        for u, zone in [(u, r.zones.get(u) or UNKNOWN_ZONE) for u in r.linked] + [(u, "nav") for u in r.nav_linked]:
            for lr in linkable.get(_link_key(u), []):
                k = doc_of.get(lr.id)
                if k is None or k is d:
                    continue
                if k.doc_id not in refs or ZONE_ORDER.get(zone, 9) < ZONE_ORDER.get(refs[k.doc_id][0], 9):
                    refs[k.doc_id] = (zone, _one_line(r.contexts.get(u, "")))
        d.refs = tuple((doc_id, zone, context) for doc_id, (zone, context) in refs.items())

    for ev in events:
        if ev.start < CALENDAR_SINCE:
            excluded[EVENT_KIND + BEFORE] += 1
            continue
        title = _one_line(ev.title)
        if not title:
            excluded[EVENT_KIND + BLANK] += 1
            continue
        # an event's whole text is its title: has_text, so it is shown as text and never as a scanned file; its unit is
        # its export's site's (plan 2026-11-08 T3)
        docs.append(Document(doc_id="e" + ev.id, kind_class="event", kind_raw=EVENT_KIND, title=title, url=ev.url,
                             unit=sites.unit_of_site(ev.site or site), grp="", parent_id=None, parent_title="",
                             linktext="", has_text=True, text=title, site=ev.site or site))
    return docs, dict(excluded)
