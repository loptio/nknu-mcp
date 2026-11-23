"""Readers for the content stage's inputs, all read-only: the corpus (one text per page or attachment: the 10-29
corpus, or the one nknu.content.assemble writes from the to-text run), the notice pages' header fields, the calendar
JSON and the manifest of one site's export (archive-export/<site>-<date>/).

A c.nknu notice's date comes from the page's own date field (newsdetail's #lbl_AddTime, newsview's
#DataFormView_AddTimeLabel: plan 2026-11-08 T3), never from the export manifest, whose dates were wrong for about 16%
of 教务处's notices. Since plan 2026-11-08 T3 a record carries its export's site, its row's 抓取时间 and the date the
export read on its page (网页上写的日期 and 日期原文), which assemble writes from the manifest: the documents a date
field does not date take that one, with its own source (documents.build_documents). HTML is decoded strictly as UTF-8
before parsing: the pages claim big5 but are UTF-8.

Plan 2026-11-08 T4 (one database from many exports): the build gives each record the export it read it with
(Record.export), where its file and its notice fields are read (read_metas), and each event carries the 抓取时间 of its
calendar file, by which the build keeps the newest of one event's copies (documents.one_event_per_id).
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

from opencc import OpenCC
from selectolax.lexbor import LexborHTMLParser

from nknu.core.timeutil import TAIPEI, today_tpe

EARLIEST = date(1990, 1, 1)
LATEST_DAYS = 400  # dates later than today + this many days are misreadings

_s2t = OpenCC("s2t")
_ADD_TIME = re.compile(r"(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})")
PAGES_SEP = " | "  # how a corpus record joins page URLs (linked, nav_linked), as the export joins them
CALENDAR_FILES = ("data/*行事历*.json", "data/*行事曆*.json")  # an export's calendar files (load_calendar)
# The leading words of archive.export's EMPTY_AREA (「内文区是空的（…多半是学校删掉的公告）」) and NO_AREA
# (「找不到内文区（…多半是学校删掉的公告只剩网站模板）」), content's own copy as dates.OWN_FIELD copies the export's
# 日期来源: a 缺什么 holding either (alone, or joined to other reasons by 、) says a notice page's own title and text
# areas are on the page and empty, or not on it (owner 11-09, blueprint item 15; Record.area_mark,
# documents.deleted_notice)
AREA_EMPTY, AREA_MISSING = "内文区是空的", "找不到内文区"
# blueprint item 22 (owner 11-09, 「照这个做」): a page's template is told from its own words only among at least this
# many files of its group (nknu.content.assemble.read_again); the build warns of a corpus none of whose pages was
# judged though it holds this many 【Claude】
TEMPLATE_PAGES = 5
AREA_MARKS = (AREA_EMPTY, AREA_MISSING)
# archive.export's 页别 values (blueprint item 22, part 3; the crawler's 53cf31b), content's own copy as AREA_EMPTY copies
# the export's words: a list's first page (LIST_FIRST) and a page a list's pager brought (LIST_PAGER: …#第N页, or a
# GET-paged list's page 2 on); '' on every other row (Record.list_kind; documents._class leaves a LIST_PAGER page out)
LIST_FIRST, LIST_PAGER = "列表", "列表翻页"
CATEGORY_SEP = " | "  # archive.export's joiner of 列表上的类别 (its list_category): Record.list_category
# archive.export's NO_DATA (the crawler's fa81f41), content's own copy as AREA_EMPTY copies the export's words: the
# school answered the page with no data (the course tables' pop-up 「系所未提供相關資料」, which the to-text step cannot
# read: such a page reads as an empty form), written 「学校回答没有资料（系所未提供相關資料）」 in 缺什么, alone or after
# other reasons, so read as a substring. The owner 11-09 「照这样做」 (in the crawler session) to the content session's
# proposal (documents' module docstring quotes it): documents._class leaves such a page out and the build counts it,
# never by its address, since the real tables would go too once the school publishes the year, and 16 real notices
# on 5 SSO unit sites carry the same 「#按钮=115學年度」
# (research/crawler_lab_2026-11-04/item22_1109/peer_notes.md 第六、七则). Record.area_mark holds it 【Claude】
NO_DATA = "学校回答没有资料"
# spec 2026-11-16 (nightly ingest) §五, agreed with the crawler session 11-16: a page or a file the school deleted, which
# the nightly update writes 「学校已删除」 for in 缺什么 once two fetches at least 24 hours apart both found it gone (the
# row keeps the last good copy). Read as a substring, like NO_DATA; documents._class leaves such a row out and the
# build counts it per site. A notice that only left its list is not marked and stays (the owner 10-24).
GONE = "学校已删除"
# the crawler's export batch of 11-16 (ef05cf1; its export.NOTICE_MOVED, 「学校把这则公告转到别的页（多半是删了）」, of which
# this is the part before the brackets): a notice's address the school now sends to another page of the same host (its
# home page, a list, an album), so the row's file and title are that page's, not a notice's (31 of c.nknu.edu.tw/uk's
# notices showed an album page's title on 11-16). documents._class leaves such a row out and the build counts it with
# the deleted ones: agreed with the crawler session 11-16 (「等你定了字样，照标记不收」) 【Claude】
MOVED = "学校把这则公告转到别的页"
# 11-19, agreed with the crawler session (its export of the course system, sso.nknu.edu.tw/stu): an outline page of a
# course several teachers give shows only 「本課程多名老師合開，請先選擇教師」 and the teachers' menu; what each choice
# brings is a row of its own (<the page>#按钮=<teacher>). The export writes 「内容在按钮行（…）」 at the start of such a
# page's 缺什么; read as a substring like the others, documents._class leaves the page out (122 pages on 11-18, 13
# documents of menu words once their copies were merged) and the build counts it under its own key.
IN_BUTTONS = "内容在按钮行"
# the 缺什么 marks a record's area_mark holds: the first of them, in this order, that the row's 缺什么 holds, so the
# school's answer of no data comes first when a row has an area mark too: a page so marked is never kept, while
# 找不到内文区 first would keep one with text (deleted_notice); no row had both on 11-09, the course tables' platform
# naming no notice areas 【Claude】
# GONE, MOVED first: such a row is never kept, whatever else it says; IN_BUTTONS next (11-19), never kept either
ROW_MARKS = (GONE, MOVED, IN_BUTTONS, NO_DATA, *AREA_MARKS)
# c.nknu's notice templates, by their page kind (pagetext.page_kind): newsdetail (教务处), and newsview (总务处, 体育室
# and 师培处, per the site probe; plan 2026-11-08 T3)
NOTICE_KINDS = ("newsdetail", "newsview")
# the header fields of each (CSS): 組別, title, date, file links; newsview names no 組別
_FIELDS = (("#lbl_GroupName", "#lbl_Title", "#lbl_AddTime", "#lbl_FileLink"),
           ("", "#DataFormView_TitleLabel", "#DataFormView_AddTimeLabel", "#DataFormView_FileLinkLabel"))


@dataclass(frozen=True)
class Record:
    id: str
    kind: str
    url: str
    file: str
    title: str
    text: str
    linktext: str
    linked: tuple[str, ...]  # URLs of the pages linking to it (attachments only): from their text or attachment area
    # the assembled corpus (plan 2026-11-02 T7a, addendum B); the 10-29 corpus has none of these
    source: str = ""  # where the text came from: totext | fallback-1029 | none
    nav_linked: tuple[str, ...] = ()  # pages linking to it from a menu only
    zones: dict[str, str] = field(default_factory=dict, hash=False)  # page URL -> content|attachments|unknown|nav
    contexts: dict[str, str] = field(default_factory=dict, hash=False)  # page URL -> what that page calls it
    rows: dict[str, str] = field(default_factory=dict, hash=False)  # page URL -> the row it sits on
    formats: tuple[dict, ...] = field(default=(), hash=False)  # format siblings folded into it: id, url, file,
    #                                                           format, kind
    key: str = ""  # its identity, whose hash id is (nknu.content.ids, plan 2026-11-08 T1); '' in the 10-29 corpus
    # plan 2026-11-08 T3, from its manifest row ('' in a corpus assembled before): the export's site key (网站), its
    # 抓取时间 as written, the date the export read on its page (网页上写的日期, an ISO date; pages only) and the words
    # it read it from (日期原文); sub-step 4 (spec 2026-11-08 §四), from the step-3 export's columns ('' in an export
    # made before 3c33a79, and in a corpus assembled before): which export rule found that date (日期来源:
    # dates.OWN_FIELD, LIST_ROW, PAGE_WORDS; documents._stated), the archived answer's Last-Modified as written
    # (最后修改时间: a file's server time, spec §二 3; documents.Document.server_date) and where the file came from
    # (来源: '' this round's archive, 「10-24 旧存档」 the 10-24 archive's copy)
    site: str = ""
    fetched: str = ""
    page_date: str = ""
    page_date_raw: str = ""
    date_source: str = ""
    modified: str = ""
    origin: str = ""
    # owner 11-09 (blueprint item 15, 「照建议定」): which of AREA_MARKS the row's 缺什么 holds, the notice's own areas
    # empty (AREA_EMPTY) or missing (AREA_MISSING), documents.deleted_notice reads it; since the crawler's fa81f41 also
    # NO_DATA, the school answered the page with no data (documents._class leaves it out); '' without, and in an
    # export or a corpus made before
    area_mark: str = ""
    # blueprint item 22 (owner 11-09, 「照这个做」): True for a page read again whole, its reading having given much less
    # than its own words (assemble's under_read), False for a page judged and kept as read, None when not judged (too
    # few files of its template, an older result, any record but a page, a corpus assembled before); the build counts
    # the True ones per site (sites.under_read)
    under_read: bool | None = None
    # blueprint item 22 (owner 11-09, 「照这个做」), the crawler's 53cf31b export: the row's 页别 (LIST_FIRST, LIST_PAGER:
    # documents._class leaves a LIST_PAGER page out) and 列表上的类别 (the categories the list rows linking the page state,
    # joined by CATEGORY_SEP: documents.Document.categories); '' without them, and in an export or a corpus made before
    list_kind: str = ""
    list_category: str = ""
    # the crawler's export batch of 11-16 (ef05cf1): the row's 组别, the words of a c.nknu notice page's own 組別 cell
    # (#lbl_GroupName; newsdetail only), as written; '' on any other row and in an export or a corpus made before.
    # The build reads it in place of its own reading of the page (build_sites' page_reread)
    group: str = ""
    # the owner, 11-15 (「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」), the crawler's 2989f60 export: the
    # row's 公告单位, the words of the announcement platform's 「公告單位：」 cell on the notice's page, as written
    # (「教務處註冊組」; not matched to the unit tree: build._stated_units does that); '' on every other row, on a
    # platform notice whose cell is empty, and in an export or a corpus made before
    publisher: str = ""
    # plan 2026-11-08 T4: the export folder the build read it with (its file, and a notice's header fields, are read
    # there); set by the build for each input, never written to corpus.json ('' when read alone)
    export: str = ""


def _pages(value) -> tuple[str, ...]:
    items = value if isinstance(value, list) else (value or "").split(PAGES_SEP)
    return tuple(u.strip() for u in items if isinstance(u, str) and u.strip())


def load_corpus(path: Path) -> list[Record]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Record(
            id=str(r["id"]), kind=r.get("kind") or "", url=r.get("url") or "", file=r.get("file") or "",
            title=r.get("title") or "", text=r.get("text") or "", linktext=r.get("linktext") or "",
            linked=_pages(r.get("linked")), source=r.get("source") or "", nav_linked=_pages(r.get("nav_linked")),
            zones=dict(r.get("zones") or {}), contexts=dict(r.get("contexts") or {}), rows=dict(r.get("rows") or {}),
            formats=tuple(dict(f) for f in r.get("formats") or () if isinstance(f, dict)), key=r.get("key") or "",
            site=r.get("site") or "", fetched=r.get("fetched") or "", page_date=r.get("page_date") or "",
            page_date_raw=r.get("page_date_raw") or "", date_source=r.get("date_source") or "",
            modified=r.get("modified") or "", origin=r.get("origin") or "", area_mark=r.get("area_mark") or "",
            under_read=None if r.get("under_read") is None else bool(r.get("under_read")),
            group=r.get("group") or "",
            list_kind=r.get("list_kind") or "", list_category=r.get("list_category") or "",
            publisher=r.get("publisher") or "",
        )
        for r in rows
    ]


def plausible(d: date) -> bool:
    return EARLIEST <= d <= today_tpe() + timedelta(days=LATEST_DAYS)


def parse_add_time(raw: str) -> date | None:
    """「2026/10/22 上午 10:49:00」-> 2026-10-22; None when there is no plausible date."""
    m = _ADD_TIME.search(raw or "")
    if not m:
        return None
    try:
        d = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return d if plausible(d) else None


@dataclass(frozen=True)
class PageMeta:
    group: str
    title: str
    added_raw: str
    added: date | None
    attachment_urls: tuple[str, ...]


@lru_cache(maxsize=8)
def _manifest(export_dir: str) -> dict:
    path = Path(export_dir) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@lru_cache(maxsize=8)
def _page_urls(export_dir: str) -> dict[str, str]:
    """本地档案 -> the URL its relative links resolve against (the landing URL after a redirect)."""
    return {it["本地档案"]: it.get("最后落地网址") or it.get("网址") or ""
            for it in _manifest(export_dir).get("项目", []) if it.get("本地档案")}


def _text(node) -> str:
    return " ".join(node.text().split()) if node is not None else ""


def read_page_meta(export_dir: Path, rel_file: str) -> PageMeta | None:
    """The header of a notice page in either c.nknu template (_FIELDS): newsdetail's #lbl_GroupName, #lbl_Title,
    #lbl_AddTime and #lbl_FileLink, or newsview's #DataFormView_TitleLabel, #DataFormView_AddTimeLabel and
    #DataFormView_FileLinkLabel (plan 2026-11-08 T3 asks the date; its title and file links are read as newsdetail's
    【Claude】; it names no 組別, and its #DataFormView_ClassLabel is a category);
    the file links' hrefs made absolute against the page's URL from the export manifest (left as written when the page
    is not listed there). None when the file is missing or has none of these fields."""
    path = Path(export_dir) / rel_file
    if not path.is_file():
        return None
    tree = LexborHTMLParser(path.read_bytes().decode("utf-8-sig"))
    for fields in _FIELDS:
        group, title, added = (tree.css_first(css) if css else None for css in fields[:3])
        if group is not None or title is not None or added is not None:
            break
    else:
        return None
    base = _page_urls(str(Path(export_dir).resolve())).get(rel_file, "")
    links = tree.css_first(fields[3])
    urls: list[str] = []
    for a in links.css("a") if links is not None else ():
        href = (a.attributes.get("href") or "").strip()
        if href and not href.lower().startswith(("javascript:", "#", "mailto:")):
            url = urljoin(base, href) if base else href
            if url not in urls:
                urls.append(url)
    added_raw = _text(added)
    return PageMeta(_text(group), _text(title), added_raw, parse_add_time(added_raw), tuple(urls))


def read_metas(export_dir: Path, records: list[Record]) -> dict[str, PageMeta]:
    """read_page_meta for every record of a c.nknu notice template (NOTICE_KINDS), keyed by Record.id (the key
    build_documents expects), each in its own export (Record.export: the build's input it came from, plan 2026-11-08
    T4), else in export_dir."""
    out: dict[str, PageMeta] = {}
    for r in records:
        if r.kind in NOTICE_KINDS and (meta := read_page_meta(Path(r.export or export_dir), r.file)) is not None:
            out[r.id] = meta
    return out


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: date
    end_inclusive: date
    url: str
    site: str = ""  # its export's site key (网站), whose unit it takes (plan 2026-11-08 T3); '' for one made by hand
    # the 抓取时间 of the export's row of its calendar file: the newest of one id's events stays (plan 2026-11-08 T4,
    # documents.one_event_per_id); '' for one made by hand or a file the manifest does not list
    fetched: str = ""
    # plan 2026-11-14 T2: the id of the recurring event this one is an instance of (the Calendar API's
    # recurringEventId; the export holds the instances, expanded with singleEvents=true, each with the id
    # <that id>_<its start>); '' for a single event
    series: str = ""
    # plan 2026-11-14 T2: only on the one event documents.one_event_per_series makes of a series' instances (its id is
    # the series id, start its first day and end_inclusive its last): every instance's (first day, last day), by day;
    # () on any other event
    days: tuple[tuple[date, date], ...] = ()


def _day(when: dict) -> tuple[date, bool]:
    """A Calendar API start/end -> (Asia/Taipei date, is_all_day)."""
    if when.get("date"):
        return date.fromisoformat(when["date"]), True
    return datetime.fromisoformat(when["dateTime"]).astimezone(TAIPEI).date(), False


def _midnight(when: dict) -> bool:
    dt = when.get("dateTime")
    return bool(dt) and datetime.fromisoformat(dt).astimezone(TAIPEI).time() == datetime.min.time()


def load_calendar(export_dir: Path) -> list[Event]:
    """Events of data/*行事历*.json (Google Calendar API events.list), cancelled ones left out, every year (the
    documents start at documents.CALENDAR_SINCE), each with its export's site (the manifest's 网站; '' without one) and
    its file's 抓取时间 (the manifest row whose 本地档案 it is; plan 2026-11-08 T4). An all-day event's end.date is the
    day after its last day, so end_inclusive = end.date - 1; a timed event ending exactly at midnight ends the day
    before. An instance of a recurring event keeps that event's id (recurringEventId: Event.series, plan 2026-11-14
    T2), which documents.one_event_per_series merges by."""
    export_dir = Path(export_dir)
    files = sorted({f for pattern in CALENDAR_FILES for f in export_dir.glob(pattern)})
    manifest = _manifest(str(export_dir.resolve())) if files else {}
    site = manifest.get("网站") or ""
    fetched = {it["本地档案"]: it.get("抓取时间") or "" for it in manifest.get("项目", []) if it.get("本地档案")}
    out: list[Event] = []
    for f in files:
        when = fetched.get(f.relative_to(export_dir).as_posix(), "")
        for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
            if it.get("status") == "cancelled" or not it.get("start") or not it.get("end"):
                continue
            start, _ = _day(it["start"])
            end, all_day = _day(it["end"])
            if (all_day or _midnight(it["end"])) and end > start:
                end -= timedelta(days=1)
            out.append(Event(str(it["id"]), (it.get("summary") or "").strip(), start, max(start, end),
                             it.get("htmlLink") or "", site, when, series=str(it.get("recurringEventId") or "").strip()))
    return out


def taipei_day(stamp: str) -> date | None:
    """An ISO date-time (抓取时间, 导出时间) as its Asia/Taipei day; None when it is not one."""
    try:
        return datetime.fromisoformat(stamp).astimezone(TAIPEI).date()
    except (TypeError, ValueError):
        return None


def export_info(export_dir: Path) -> dict:
    """{'site', 'name' (Traditional Chinese), 'data_as_of', 'missing_pages', 'missing_files', 'area_marked',
    'list_marked', 'no_data_marked'}:
    data_as_of is the site's crawl day, the latest 抓取时间 (Asia/Taipei day) among the 网页 rows with a 本地档案;
    without one, among all rows with a 本地档案; else the export day. An export re-reads an older crawl (T7a), and a crawl learns what
    the site holds through its pages: files fetched one by one later (the 4 off-site attachments of 11-02, nknu
    archive run --only) do not re-read the site, so they do not move the date (phase-2 review). The counts are the
    pages and attachments listed in missing.csv; area_marked the rows whose 缺什么 holds one of AREA_MARKS (owner 11-09,
    blueprint item 15: a corpus assembled before 11-09 carries no area_mark, which the build then warns of);
    list_marked the 网页 rows with a 本地档案 (each a page record of the corpus) marked LIST_PAGER (页别) or stating a
    列表上的类别 (blueprint item 22, the crawler's 53cf31b, review 11-09: a corpus assembled before content read them
    carries neither, which the build then warns of; the export sets a category on a row with no file too); 0 for an
    export made before 53cf31b; no_data_marked the 网页 rows with a 本地档案 whose 缺什么 holds NO_DATA (the crawler's
    fa81f41: a corpus assembled before content read it carries no such area_mark, which the build then warns of)."""
    m = _manifest(str(Path(export_dir).resolve()))
    fetched = [(it.get("类型") == "网页", d) for it in m.get("项目", [])
               if it.get("本地档案") and (d := taipei_day(it.get("抓取时间") or ""))]
    page_days = [d for is_page, d in fetched if is_page]
    data_as_of = max(page_days or [d for _, d in fetched], default=None) or taipei_day(m.get("导出时间") or "")
    data_as_of = data_as_of.isoformat() if data_as_of else ""
    pages = files = 0
    missing = Path(export_dir) / "missing.csv"
    if missing.is_file():
        with missing.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                pages += row.get("类型") == "网页"
                files += row.get("类型") == "附件"
    return {"site": m.get("网站") or "", "name": _s2t.convert(m.get("名称") or ""), "data_as_of": data_as_of,
            "missing_pages": pages, "missing_files": files,
            "area_marked": sum(any(a in (it.get("缺什么") or "") for a in AREA_MARKS) for it in m.get("项目", [])),
            "list_marked": sum(it.get("类型") == "网页" and bool(it.get("本地档案"))
                               and (it.get("页别") == LIST_PAGER or bool((it.get("列表上的类别") or "").strip()))
                               for it in m.get("项目", [])),
            "no_data_marked": sum(it.get("类型") == "网页" and bool(it.get("本地档案")) and NO_DATA in (it.get("缺什么") or "")
                                  for it in m.get("项目", []))}
