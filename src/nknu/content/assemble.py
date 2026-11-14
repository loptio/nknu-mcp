"""Assemble the corpus (plan 2026-11-02 T7a; addendum B as rewritten after the lane-B review, G2-G4): one site's export
and the to-text cache become one corpus.json of the 10-29 shape, which nknu.content.build reads as before. Only the
export (manifest.json; every file, hashed to find its cache entry as the to-text accounting does; the pages again for
their links), the to-text cache and the 10-29 corpus are read; the output is written whole (a temporary file, then
os.replace). A file converting while assemble runs has a marker and no result: it counts as failed (the accounting's
crash rule), so run assemble after the to-text run.

Ids (plan 2026-11-08 T1): a record's id is ids.record_id of its row's key (ids.manifest_keys: the identity of its
address, or of the button that brought it), 'u…', and an archive member's 'm…' (ids.member_key); the key is written
beside it. Rows of one identity (an &amp; spelling, http and https) get one id; the build keeps one record of them.
The 10-29 fallback is still looked up by the old serial ids (p<序号>, a<序号>), which stay inside assemble.

Plan 2026-11-08 T3: every record also carries its export's site (the manifest's 网站), its row's 抓取时间 (fetched) and
the date the export read on its page (page_date, page_date_raw: 网页上写的日期, 日期原文), which the build dates
pages and notices by. Sub-step 4 (spec 2026-11-08 §四): also the step-3 export's 日期来源 (date_source: which export
rule found that date, documents._stated), 最后修改时间 (modified: the server's time of a file, §二 3) and 来源 (origin),
each '' when the export (one made before 3c33a79) has no such column; an archive member carries its archive's row's.
Owner 11-09 (blueprint item 15, 「照建议定」): area_mark, which of corpus.AREA_MARKS the row's 缺什么 holds (the export
found the notice page's own areas empty, or not on it), which documents.deleted_notice reads; since the crawler's
fa81f41, corpus.NO_DATA: the school answered the page with no data, which documents._class leaves out (the owner 11-09
「照这样做」: documents' module docstring); an image only such pages show is no kept page's (inline), and the no-data
mark comes first when a row holds an area mark too (corpus.ROW_MARKS) 【Claude】; '' without one.
Blueprint item 22 (owner 11-09, 「照这个做」): a kept page whose reading gave much less than its own words is read
again from its whole reading without the site's template and menus, and marked under_read (_Assembly.under_read,
read_again: True, False when judged and kept as read, None when it could not be judged); never a home page, a c.nknu
list, a page whose 缺什么 holds a mark (area_mark: its areas, or the school's answer of no data) or one the export
marks 列表翻页. Its parts 2 and 3 (the crawler's 53cf31b): every record carries its row's 页别 (list_kind:
corpus.LIST_FIRST, LIST_PAGER) and 列表上的类别 (list_category: the categories of the list rows linking the page,
joined by corpus.CATEGORY_SEP), each '' in an export made before 53cf31b; a page marked LIST_PAGER is left out by the
build (documents._class), so an image only such pages show is no kept page's (inline).
A page's kind is pagetext.page_kind of its 网址 and the list grid the exported page shows where it was read
(pagetext.list_grid of 最后落地网址, else 网址; plan 2026-11-08 progress notes): an SSO or cLv2 list is
list:<profile> and left out, its content pages stay page:<profile>. The 10-29 fallback is 教务处's corpus: it is read
only for 教务处's export (FALLBACK_SITE), never for another site's, whose old serial ids name other files.

Records ({id, key, kind, url, file, title, text, linktext, linked, source, totext_status, site, fetched, page_date,
page_date_raw, date_source, modified, origin, area_mark, list_kind, list_category, publisher}, a page's also
under_read, in manifest order; publisher: the row's 公告单位, the publishing unit an announcement platform's notice
page states, since the crawler's 2989f60, '' else):
- a page: kind as above; title its album name, #lbl_Title or 标题; its to-text text with
  each [[img:src]] line replaced by the OCR text of the exported image the src names, or removed. The src is joined
  to the page's address (最后落地网址 or 网址, or its <base href>) and spelt as the crawler spells addresses
  (scope.canonical, scheme aside), so a CJK, %-encoded or mixed-case src finds the manifest's 图片 row.
- an attachment: url 最后落地网址 or 网址, title 标题, linktext 链接文字. An archive whose members were
  extracted gives way to its members (keyed by the archive's key and their paths), url <archive url>#<quoted path>,
  titled by their file names, with the archive's file, link text and pages.
- an image with at least IMAGE_DOC_CHARS characters of OCR text that no kept page (a kind documents.kind_class does not
  leave out, not marked 列表翻页, blueprint item 22, nor answered with no data, corpus.NO_DATA) shows: kind 附件:图片;
  its pages as an attachment's (below), and a page whose text shows it is a content page of it, while a page that
  neither shows nor links it in its read content area carries it outside that area (a header banner, a menu): nav.
  When every reference is nav and each of those pages holds an <img> of it (a unit banner), its kind is
  documents.MENU_IMAGE, which the build leaves out and counts (I4); a photo a page album links or a file of a folder
  the page links (no <img> of it there) stays 附件:图片.
- 资料 rows (data/: structured data) take no record; the report's data_rows gives each its reason (data_reason): the
  calendar the build reads itself, a Drive folder's listing not needed (its files are rows of their own), else unread.
Every text is normalized (nknu.totext.normalize; pdf=True for every PDF text, archive members included: G3; every
other route, pages, office, office_old and images, also joins isolated runs of 2 or 3 letter-spaced characters: H1).

source: totext (the to-text result: ok, partial or empty), fallback-1029, none. A file whose to-text failed (or, with
allow_pending, has no result yet) takes the 10-29 text and kind of the record with its old serial id only when that
record names the same file and has text; when it names another file (the re-export moved the numbers), the fallback
is refused and listed by that old id.

Links (addendum B): the pages an attachment's row lists (链到它的网页) are read with links.page_links. linked keeps the
pages that link it from their text or their attachment area (zones content, attachments) and those where no link to
it is found (zone unknown: a button download, a page not exported); pages that link it only from a menu go to
nav_linked, and so does a site map (MENU_PAGES: the menu written out as a page), whatever part of it holds the link. zones, contexts (what each page calls it) and rows (the table row, list item or paragraph it sits in) are
keyed by the page URLs as the row lists them. The build titles and dates an attachment by these pages.

Format siblings (G2): links to attachments on one row of one page make one record only when the row is not '', their
contexts are equal, their extensions differ (document formats: FORMAT_EXT), no anchor or file-name stem differs from
the other's in a number (a year grid's row holds editions, not formats), and either their file names match without
the extension or both anchors are format words. A group holding one format twice stays apart. The one with the most
text stays (PDF on ties) and lists the others in its formats, with their pages; they take no record of their own.

    python -m nknu.content.assemble --export DIR --totext DIR --out corpus.json [--fallback PATH|--no-fallback]
        [--allow-pending]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import profiles, scope
from nknu.archive.links import _base as link_base  # the crawler's <base href> rule, as links.page_links uses it
from nknu.content import build, ids
from nknu.content.corpus import CALENDAR_FILES, LIST_PAGER, NO_DATA, PAGES_SEP, ROW_MARKS, TEMPLATE_PAGES
from nknu.content.documents import _FILE_SIZE, MENU_IMAGE, UNKNOWN_ZONE, _link_key, kind_class, site_root
from nknu.content.links import PageLink, match_key, page_links
from nknu.totext.accounting import Account, account
from nknu.totext.cache import FULL, Cache, write_atomic
from nknu.totext.normalize import normalize
from nknu.totext.ocr import FEW_CHARS
from nknu.totext.pagetext import IMG_LINE, PageDecodeError, list_grid, page_kind
from nknu.totext.pagetext import _src as img_src  # the src pagetext writes into [[img:…]], so the two agree

FALLBACK = build.CORPUS  # the 10-29 corpus.json: per-file fallback only
FALLBACK_SITE = "c.nknu.edu.tw/aca"  # the one export the 10-29 corpus is of: 教务处's (plan 2026-11-08 T3)
REPORT = "assemble_report.json"  # written beside the corpus
IMAGE_DOC_CHARS = 10  # an image no kept page shows is a document of its own with this much OCR text
TEXT_STATUSES = ("ok", "partial", "empty")  # to-text results whose text is the file's
SOURCES = ("totext", "fallback-1029", "none")
FORMAT_EXT = frozenset({"pdf", "doc", "docx", "odt", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "rtf", "txt"})
_FORMATS = "|".join(sorted(FORMAT_EXT, key=lambda e: (-len(e), e)))
# a format word (fullmatch): pdf, PDF檔, (odt), 【doc版】, docx 格式
_FORMAT_WORD = re.compile(rf"(?i)[\s(（\[【「]*\.?(?:{_FORMATS})\s*(?:檔|档|版|格式)?[\s)）\]】」]*")
_NUMBER = re.compile(r"\d+")
_EXTENSION = re.compile(r"[a-z0-9]{1,5}")
_SEPARATOR = re.compile(r"[/\\]")  # an archive member's path separators
CONTEXT_ZONES = {"content": 0, "attachments": 1}  # whose name a page's reference takes first: its text's, then its
#                                                    attachment area's (often the uploaded file name)
# Page kinds (pagetext.page_kind) whose links are a menu's wherever they stand on the page 【Claude, 11-15, for the
# owner's 「请看一下附件标题取自链接文字还是档名的规则对不对」】: c.nknu's sitemap.aspx writes the site's menu out as a
# list in its content area, so its links read as links from a page's text, and the build, which never names a file by
# a menu, named 4 files of the 111 sites by it (教务处's 普通教室管理及使用要點1130607.pdf was 「7 , 普通教室借用申請單」,
# the menu entry that opens it with the site map's numbering:
# research/pipeline_2026-11-02/fixes_1115/title_case_1115.txt, title_rule_1115.json)
MENU_PAGES = frozenset({"sitemap"})
# 资料 rows (structured data the export keeps under data/) become no record; the report gives each its reason
# (phase-2 review: to-text has one skip reason for all of them, which fits the calendar only)
DATA_CALENDAR = "行事历：建库直接读成行事历事件（corpus.load_calendar）"
DATA_DRIVE_LIST = "云端硬盘资料夹的清单：不读，资料夹里的每个档在导出里各有自己的一行（附件、图片）"
DATA_UNREAD = "建库不读这种结构化资料：内容没有进资料库"
_DRIVE_LIST = re.compile("云端硬盘资料夹.*的清单")  # export._data_title's title (and file name) of a folder's listing
# blueprint item 22 (owner 11-09, 「照这个做」): when and how a page is read again whole (read_again). OWN_CHARS and
# READ_SHARE are the 11-09 sweep's, which found the pages a content area misses; TEMPLATE_SHARE was measured on every
# page of the 111 exports (research/pipeline_2026-11-02/reading_gaps_1109/cut_1109d.log): at 0.3 a site's second
# template (news.nknu.edu.tw's notice system on 24-26% of its files) passed for its pages' own words and 99 correctly
# read notices were re-read into chrome, at 0.05 sparse alumni pages stop being re-read 【Claude】
OWN_CHARS = 200  # a page's own words come to at least this many non-space characters
READ_SHARE = 0.3  # and what its reading gave to less than this share of them
TEMPLATE_SHARE = 0.1  # a line on this share of a group's files, TEMPLATE_PAGES (corpus) at least, is template
MENU_RUN = 3  # this many link lines in a row (template, blank and image lines between them aside) are a menu


class Whole(NamedTuple):
    """A page file's whole reading (cache.FULL, normalized as its text is), for read_again."""
    lines: list[str]
    links: frozenset[int]  # its lines that are one link's words alone (pagetext.PageText.links)
    group: str  # the files judged together: the platform profile and site key of where it was read


class Again(NamedTuple):
    """read_again's answer for one page."""
    judged: bool  # its group holds TEMPLATE_PAGES files or more: its template could be told from its own words
    text: str | None  # its text read again whole; None when it stays as read


def data_reason(row: dict) -> str:
    """Why a 资料 row's content is in the database or not: the calendar (corpus.CALENDAR_FILES) is read by the
    build, a Drive folder's listing is not needed, anything else is not read."""
    file = row.get("本地档案") or ""
    if any(PurePosixPath(file).match(pattern) for pattern in CALENDAR_FILES):
        return DATA_CALENDAR
    if _DRIVE_LIST.search(row.get("标题") or "") or _DRIVE_LIST.search(PurePosixPath(file).name):
        return DATA_DRIVE_LIST
    return DATA_UNREAD


class PendingInputs(ValueError):
    """The to-text run has not finished: inputs without a result (account keys, manifest order)."""

    def __init__(self, keys: list[str]):
        super().__init__(f"还有 {len(keys)} 个档没转完")
        self.keys = keys


@dataclass
class AssembleReport:
    records: int = 0
    by_kind: dict = field(default_factory=dict)
    by_source: dict = field(default_factory=dict)  # totext | fallback-1029 | none
    images_inlined: int = 0  # images a kept page shows: their text is in that page
    image_docs: int = 0  # images no kept page shows, with text enough: records the build keeps
    images_menu_only: int = 0  # such images only menus carry (I4): records of the kind MENU_IMAGE, left out
    images_no_text: int = 0  # images no kept page shows, with too little text: no record
    members: int = 0  # archive members that became records
    containers_dropped: int = 0  # archives whose members took their place
    fallback_refused: list[str] = field(default_factory=list)  # old serial ids whose 10-29 record names another file
    # plan 2026-11-08 T3: a 10-29 corpus was given, and the export is not 教务处's (FALLBACK_SITE): it was not read
    fallback_off: bool = False
    # plan 2026-11-08 T1: rows a button brought whose list another such row shares the words with: their ids take
    # the row's 存档编号 (internal to one frontier.db), so they may change between exports
    unstable_ids: int = 0
    pending: list[str] = field(default_factory=list)  # inputs without a to-text result (allow_pending only)
    # addendum B
    with_context: int = 0  # attachments some page names from its text or attachment area: the build titles them so
    nav_only: int = 0  # attachments only menus link: no parent (the build dates them by the server's time)
    button_downloads: int = 0  # attachments no linking page holds a link to: their pages stay, zone unknown
    unknown_refs: int = 0  # page references without a link to the file (all of a button download's among them)
    format_merged: int = 0  # records folded into a format sibling (listed in its formats)
    format_refused: int = 0  # sibling groups left apart: one format twice
    pages_unreadable: list[str] = field(default_factory=list)  # pages page_links could not read (序号)
    markers_unmatched: int = 0  # [[img:…]] lines naming no exported image: removed
    normalized: dict = field(default_factory=lambda: {"compat": 0, "mismapped": 0, "spaced": 0, "wraps": 0})
    data_rows: dict = field(default_factory=dict)  # 资料 rows, no records: 序号 -> data_reason (read by the build or not)
    under_read: int = 0  # pages read again whole: their reading gave much less than their own words (item 22)
    under_read_unjudged: int = 0  # pages read_again could not judge: too few files of their template, an older result


def _chars(text: str) -> int:
    """Non-space characters outside [[img:…]] lines."""
    return sum(not c.isspace() for c in IMG_LINE.sub("", text))


def _ocr_added(result: dict) -> bool:
    """Some page's text came from OCR: our whole-page OCR read lines, or Docling's region OCR put text on it."""
    ours = any(isinstance(p, dict) and p.get("lines") for p in result.get("ocr_pages") or [])
    return ours or bool(result.get("docling_ocr_pages"))


def kind_of(route: str, result: dict, chars_nonspace: int, fmt: str) -> str:
    """The corpus kind of an attachment, archive member or image by its route and to-text result (status ok when the
    result names none). pdf: 附件:pdf:无文字层 (< FEW_CHARS characters), 附件:pdf:含OCR (an OCR page added text,
    ours or Docling's region OCR), 附件:pdf:有文字层; office: 附件:office or 附件:无文字(office); image: 附件:图片
    (≥ IMAGE_DOC_CHARS characters) or 附件:无文字(图片); an archive kept as a record (nothing extracted):
    附件:无文字(压缩档); a failure, a skip, no result yet: 附件:无文字(<fmt or route>)."""
    status = result.get("status") or "ok"
    if route == "archive":
        return "附件:无文字(压缩档)"
    if route == "image":
        return "附件:图片" if chars_nonspace >= IMAGE_DOC_CHARS else "附件:无文字(图片)"
    if status in TEXT_STATUSES:
        if route == "pdf":
            if chars_nonspace < FEW_CHARS:
                return "附件:pdf:无文字层"
            return "附件:pdf:含OCR" if _ocr_added(result) else "附件:pdf:有文字层"
        if route in ("office", "office_old"):
            return "附件:office" if chars_nonspace else "附件:无文字(office)"
    return f"附件:无文字({fmt or route or '未知'})"


def _url_key(url: str, base: str | None = None) -> str:
    """An address as the crawler spells it (scope.canonical: joined to base, escapes normalised, an IIS path in lower
    case), scheme aside (links.match_key); the plan's spelling when canonical gives up (html.unescape, no fragment,
    scheme and host lowercased, path quote(unquote(path)))."""
    raw = html.unescape(url or "").strip()
    canon = scope.canonical(raw, base)
    if canon is None:
        try:
            parts = urlsplit(urljoin(base, raw) if base else raw)
        except ValueError:
            return raw
        canon = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), quote(unquote(parts.path)), parts.query, ""))
    return match_key(canon)


def _page_key(url: str) -> str:
    """A linking page's key, as documents.py joins linked pages: its identity (ids.location_key, scheme aside; a notice
    is one per site and nid, plan 2026-11-08 T1)."""
    return match_key(_link_key(url))


def _where(row: dict) -> str:
    """Where a row's file was read: its landing address after a redirect, else its address."""
    return row.get("最后落地网址") or row.get("网址") or ""


def _split(value: str) -> list[str]:
    return list(dict.fromkeys(u.strip() for u in (value or "").split(PAGES_SEP) if u.strip()))


def _name(path: str) -> str:
    return _SEPARATOR.split(path)[-1]


def _extension(name: str, url: str = "", route_kind: str = "") -> str:
    """The file's extension, lower case without its dot: its name's, else its address's, else the route's kind."""
    for candidate in (name.rsplit(".", 1)[-1].lower() if "." in name else "", scope.extension(url), route_kind):
        if candidate and _EXTENSION.fullmatch(candidate):
            return candidate
    return ""


def _numbers(s: str) -> list[str]:
    return _NUMBER.findall(unicodedata.normalize("NFKC", s))


@dataclass
class _Refs:
    """What the pages an attachment's row lists say of it."""
    linked: list[str] = field(default_factory=list)
    nav: list[str] = field(default_factory=list)
    zones: dict[str, str] = field(default_factory=dict)
    contexts: dict[str, str] = field(default_factory=dict)
    rows: dict[str, str] = field(default_factory=dict)
    links: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # page -> (row, context, anchor) of
    #                                                                            each link to it there (siblings)

    def fields(self, *, named: bool = True) -> dict:
        """The record's fields; named=False (an archive member): the page names the archive, not the member."""
        return {"linked": PAGES_SEP.join(self.linked), "nav_linked": PAGES_SEP.join(self.nav), "zones": self.zones,
                "contexts": self.contexts if named else {}, "rows": self.rows if named else {}}

    def take(self, other: _Refs) -> None:
        """other's pages join these (a format sibling folded in): a page new here comes with what it says of other,
        and a page linking only from its menu here but from its text there becomes a text reference."""
        for u in [*other.linked, *other.nav]:
            if u in self.zones and not (self.zones[u] == "nav" and other.zones.get(u) != "nav"):
                continue
            if u in self.nav:
                self.nav.remove(u)
            self.zones[u] = other.zones.get(u, UNKNOWN_ZONE)
            (self.nav if self.zones[u] == "nav" else self.linked).append(u)
            for mine, theirs in ((self.contexts, other.contexts), (self.rows, other.rows)):
                if u in theirs:
                    mine[u] = theirs[u]


@dataclass
class _Attachment:
    record: dict
    row: int
    ext: str
    stem: str
    chars: int
    refs: _Refs
    dropped: bool = False


@dataclass
class _Image:
    row: dict
    account: Account
    text: str
    source: str
    shown_by: list[str] = field(default_factory=list)  # 网址 of the pages whose text shows it
    kept: bool = False  # a kept page shows it


@dataclass
class _Look:
    """What the pages a row lists say of its file: their references; whether there were pages to look at and none
    holds a link to it (a button download); how many listed pages hold no link to it."""
    refs: _Refs
    button: bool
    unknown: int


class _Pages:
    """The exported pages by address (网址 and 最后落地网址, as _page_key spells them); each page's links (by their
    target, scheme aside) and <base> read once, on first use."""

    def __init__(self, export_dir: Path, rows: list[dict], report: AssembleReport):
        self.root = export_dir.resolve()
        self.report = report
        self.by_key: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("类型") == "网页" and row.get("本地档案"):
                for key in dict.fromkeys(_page_key(u) for u in (row.get("网址"), row.get("最后落地网址")) if u):
                    self.by_key.setdefault(key, []).append(row)
        self._links: dict[str, dict[str, list[PageLink]]] = {}
        self._pictures: dict[str, set[str]] = {}

    def kind(self, row: dict) -> str:
        """A page's kind (plan 2026-11-08 T3, as the T2 progress notes ask): pagetext.page_kind of its 网址 and the list
        grid (pagetext.list_grid) the exported page shows where it was read, 最后落地网址 else 网址; by its 网址 alone
        when the page is missing or not UTF-8."""
        data = self._bytes(row)
        try:
            tree = LexborHTMLParser(data.decode("utf-8-sig")) if data is not None else None
        except UnicodeDecodeError:
            tree = None
        return page_kind(row.get("网址") or "", list_grid(tree, _where(row)) if tree is not None else None)

    def _bytes(self, row: dict) -> bytes | None:
        path = (self.root / row["本地档案"]).resolve()
        if self.root not in path.parents or not path.is_file():
            return None
        return path.read_bytes()

    def at(self, url: str) -> list[dict]:
        return self.by_key.get(_page_key(url), [])

    def links_to(self, row: dict, target: str) -> list[PageLink]:
        """The page's links to target (a match_key), in document order."""
        key = str(row["序号"])
        if key not in self._links:
            data = self._bytes(row)
            try:
                links = page_links(data, _where(row)) if data is not None else []
            except PageDecodeError:
                links = []
                self.report.pages_unreadable.append(key)
            by_target: dict[str, list[PageLink]] = {}
            for link in links:
                by_target.setdefault(match_key(link.target), []).append(link)
            self._links[key] = by_target
        return self._links[key].get(target, [])

    def pictures(self, row: dict) -> set[str]:
        """The _url_key of every <img> src on the page, wherever it sits (header, menu, text); read once. A page that
        is missing or not UTF-8 has none."""
        key = str(row["序号"])
        if key not in self._pictures:
            data = self._bytes(row)
            try:
                tree = LexborHTMLParser(data.decode("utf-8-sig")) if data is not None else None
            except UnicodeDecodeError:
                tree = None
            base = self.base(row) if tree is not None else ""
            self._pictures[key] = {_url_key(src, base) for node in (tree.css("img") if tree else ())
                                   if (src := img_src(node))}
        return self._pictures[key]

    def base(self, row: dict) -> str:
        """What the page's relative addresses are joined to: its <base href>, else where it was read."""
        where = _where(row)
        data = self._bytes(row)
        if data is None or b"<base" not in data.lower():
            return where
        try:
            node = LexborHTMLParser(data.decode("utf-8-sig")).css_first("base[href]")
        except UnicodeDecodeError:
            return where
        return link_base(where, node.attributes.get("href") if node is not None else None)


class _Assembly:
    def __init__(self, export_dir: Path, rows: dict[int, dict], cache: Cache, old: dict[str, dict], site: str = ""):
        self.rows, self.cache, self.old, self.site = rows, cache, old, site
        self.report = AssembleReport()
        self.pages = _Pages(export_dir, list(rows.values()), self.report)
        self.images: dict[str, _Image] = {}
        self.attachments: list[_Attachment] = []
        self.read: set[str] = set()  # 序号 of the pages whose content area the to-text run read (their text is known)
        self.keys = ids.manifest_keys(list(rows.values()))  # 序号 -> the row's key (plan 2026-11-08 T1)
        # (page record, its file's sha256, its link lines, its Whole.group) of each page to-text read (item 22)
        self.whole: list[tuple[dict, str, object, str]] = []

    def ident(self, row: int) -> tuple[str, str]:
        """(id, key) of a row's record."""
        return ids.record_id(self.keys[row]), self.keys[row]

    def from_row(self, row: dict) -> dict:
        """What every record carries from its manifest row (plan 2026-11-08 T3): the export's site, 抓取时间 and the
        date the export read on the page, with its words; since sub-step 4 (spec 2026-11-08 §四) the rule that found
        that date (日期来源), the archived answer's Last-Modified (最后修改时间) and 来源, '' in an export without
        them (an archive member: its archive's row's); since 11-09 (owner, blueprint item 15) area_mark, the first of
        corpus.ROW_MARKS its 缺什么 holds (alone or joined to other reasons by 、: AREA_MARKS, and since the crawler's
        fa81f41 NO_DATA), else ''; and (blueprint item 22, the crawler's 53cf31b) list_kind, its 页别, and
        list_category, its 列表上的类别, '' in an export without them; since 11-15 (the owner: 「在你的程序里读第 30 栏
        「公告单位」」; the crawler's 2989f60) publisher, its 公告单位 on one line, '' in an export without the column."""
        return {"site": self.site, "fetched": row.get("抓取时间") or "", "page_date": row.get("网页上写的日期") or "",
                "page_date_raw": row.get("日期原文") or "", "date_source": row.get("日期来源") or "",
                "modified": row.get("最后修改时间") or "", "origin": row.get("来源") or "",
                "area_mark": next((m for m in ROW_MARKS if m in (row.get("缺什么") or "")), ""),
                "list_kind": row.get("页别") or "", "list_category": row.get("列表上的类别") or "",
                "publisher": " ".join((row.get("公告单位") or "").split())}

    # ------------------------------------------------------------ texts

    def _normalized(self, text: str, route: str) -> str:
        text, counts = normalize(text, pdf=route == "pdf")
        for name, n in counts.items():
            self.report.normalized[name] = self.report.normalized.get(name, 0) + n
        return text

    def text(self, a: Account, route: str, old_id: str = "") -> tuple[str, dict, str, str]:
        """(normalized text, the result or {"status": …}, source, the 10-29 kind when the fallback gave the text).
        old_id: the record's serial id of the 10-29 corpus (p<序号>, a<序号>), the fallback's key."""
        if a.status in TEXT_STATUSES and a.sha256 and (result := self.cache.done(a.sha256)) is not None:
            return self._normalized(self.cache.text(a.sha256), route), result, "totext", ""
        if old_id and a.status in ("failed", "pending"):
            old = self.old.get(old_id)
            if old is not None and (old.get("text") or "").strip():
                if old.get("file") == a.file:
                    return self._normalized(old["text"], route), {"status": a.status}, "fallback-1029", old.get(
                        "kind") or ""
                self.report.fallback_refused.append(old_id)
        return "", {"status": a.status}, "none", ""

    # ------------------------------------------------------------ records

    def page(self, a: Account, row: dict) -> dict:
        text, result, source, old_kind = self.text(a, "page", f"p{a.row}")
        if source == "totext":
            self.read.add(str(row.get("序号")))
        meta = result.get("page") or {}
        rid, key = self.ident(a.row)
        record = {"id": rid, "key": key, "kind": old_kind or self.pages.kind(row), "url": row.get("网址") or "",
                  "file": a.file, "title": meta.get("album") or meta.get("title") or row.get("标题") or "",
                  "text": text, "linktext": "", "linked": "", "source": source, "totext_status": a.status,
                  **self.from_row(row), "under_read": None}
        if source == "totext":
            where = _where(row)
            profile = profiles.profile_for(where)
            group = f"{profile.name if profile is not None else ''}|{scope.site_key(where)}"
            self.whole.append((record, a.sha256, meta.get("links"), group))
        return record

    def _record(self, ident: tuple[str, str], kind: str, url: str, file: str, title: str, text: str, row: dict,
                source: str, status: str) -> dict:
        """An attachment-like record in the 10-29 order (ident: its id and key); its pages are filled in at the end."""
        return {"id": ident[0], "key": ident[1], "kind": kind, "url": url, "file": file, "title": title, "text": text,
                "linktext": row.get("链接文字") or "", "linked": "", "source": source, "totext_status": status,
                **self.from_row(row)}

    def image(self, a: Account, row: dict) -> _Image:
        text, _result, source, _ = self.text(a, a.route)
        img = _Image(row, a, text, source)
        for u in (row.get("网址"), row.get("最后落地网址")):
            if u:
                self.images.setdefault(_url_key(u), img)
        return img

    def look(self, row: dict) -> _Look:
        """Each page the row lists, by what its links to the file say (addendum B); no page holding one: unknown."""
        pages = _split(row.get("链到它的网页") or "")
        want = match_key(_link_key(row.get("网址") or ""))
        found = {u: [link for page in self.pages.at(u) for link in self.pages.links_to(page, want)] for u in pages}
        refs = _Refs()
        for u in pages:
            hits = found[u]
            if page_kind(u) in MENU_PAGES:  # the menu written out as a page: every link of it is a menu's
                hits = [replace(h, zone="nav") for h in hits]
            refs.links[u] = [(h.row, h.context, h.anchor) for h in hits if h.zone != "nav"]
            text_links = [h for h in hits if h.zone != "nav"]
            if not hits:  # no link to it on that page: a button, a script, a page not exported
                refs.linked.append(u)
                refs.zones[u] = UNKNOWN_ZONE
                continue
            pick = min(text_links or hits, key=lambda h: (not h.context, CONTEXT_ZONES.get(h.zone, 9)))
            (refs.nav if pick.zone == "nav" else refs.linked).append(u)
            refs.zones[u] = pick.zone
            if pick.context:
                refs.contexts[u] = pick.context
            if pick.row:
                refs.rows[u] = pick.row
        button = any(self.pages.at(u) for u in pages) and not any(found.values())  # pages to look at, no link on any
        return _Look(refs, button, sum(not hits for hits in found.values()))

    def attachment(self, a: Account, row: dict, members: list[Account]) -> list[dict]:
        url = _where(row)
        look = self.look(row)
        refs = look.refs
        self.report.button_downloads += look.button
        self.report.unknown_refs += look.unknown
        if a.route == "archive" and members:
            self.report.containers_dropped += 1
            entries = [e for e in (self.cache.done(a.sha256) or {}).get("members") or [] if isinstance(e, dict)]
            out = []
            seen: Counter[str] = Counter()  # each path's occurrences so far: a path twice in one archive is two members
            for entry, m in zip(entries, members, strict=False):
                path = str(entry.get("path") or m.member)
                route = str(entry.get("route") or "")
                text, result, source, _ = self.text(m, route)
                kind = kind_of(route, result, _chars(text), _extension(_name(path)))
                nfc = unicodedata.normalize("NFC", path)
                key = ids.member_key(self.keys[a.row], path, seen[nfc])
                seen[nfc] += 1
                record = self._record((ids.record_id(key, member=True), key), kind, f"{url}#{quote(path)}", a.file,
                                      _name(path), text, row, source, m.status)
                out.append(record | refs.fields(named=False))
            self.report.members += len(out)
            return out
        route = a.route
        text, result, source, old_kind = self.text(a, route, f"a{a.row}")
        name = row.get("标题") or _name(unquote(urlsplit(url).path))
        kind = old_kind or kind_of(route, result, _chars(text), row.get("格式") or "")
        record = self._record(self.ident(a.row), kind, url, a.file, row.get("标题") or "", text, row, source, a.status)
        ext = _extension(name, row.get("网址") or "", str(result.get("kind") or ""))
        stem = name[: -len(ext) - 1] if ext and name.lower().endswith("." + ext) else name
        self.attachments.append(_Attachment(record, a.row, ext, stem, _chars(text), refs))
        return [record]

    # ------------------------------------------------------------ passes after every row

    def under_read(self) -> None:
        """Blueprint item 22 (owner 11-09, 「照这个做」): each page to-text read takes read_again's answer: under_read
        True with its text read again whole, False when it stays as read, None (and as read) when it could not be
        judged; a result of an older page rule has no link lines and is not judged. A page may be read again unless
        its 缺什么 holds a mark (area_mark: the export marked its areas, and item 15 decides on the page rule's own
        reading; or the school answered it with no data, corpus.NO_DATA, which the build leaves out, documents._class;
        the crawler's fa81f41), it is its site's home page (site_root) or a c.nknu list (default.aspx, download.aspx,
        laws.aspx: teasers and menus of what is elsewhere) 【Claude, 11-09 review】, or the crawler marks it 列表翻页
        (LIST_PAGER: the build leaves it out, documents._class; item 22, part 3), so a page the build leaves out is
        never read again nor counted in under_read; those still count toward the template."""
        wholes: dict[str, Whole] = {}
        for _record, sha, links, group in self.whole:
            if sha not in wholes and isinstance(links, list) and (text := self.cache.extra(sha, FULL)):
                wholes[sha] = Whole(normalize(text, pdf=False)[0].split("\n"), frozenset(links), group)
        pages = [(sha, record["text"], record["kind"], self._may_read_again(record))
                 for record, sha, _links, _group in self.whole]
        for (record, *_), again in zip(self.whole, read_again(wholes, pages), strict=True):
            if not again.judged:
                self.report.under_read_unjudged += 1
                continue
            record["under_read"] = again.text is not None
            if again.text is not None:
                record["text"] = again.text
                self.report.under_read += 1

    def _may_read_again(self, record: dict) -> bool:
        url = record["url"]
        listed = profiles.CNKNU.covers(scope.site_key(url)) and profiles.page_kind(url) == "list"
        return (not record["area_mark"] and record["list_kind"] != LIST_PAGER and not site_root(url, self.site)
                and not listed)

    def inline(self, pages: list[tuple[dict, dict]]) -> None:
        """Each [[img:src]] line of a page's text becomes the OCR text of the image it names, or goes. A page the build
        leaves out (a list kind; one the crawler marks 列表翻页, blueprint item 22; one the school answered with no
        data, NO_DATA, as such a page 【Claude】) keeps no image: an image only such pages show is no kept page's, and
        image_record makes it a record of its own when its OCR text reaches IMAGE_DOC_CHARS (else it is counted in
        images_no_text)."""
        for record, row in pages:
            if "[[img:" not in record["text"]:
                continue
            base = self.pages.base(row)
            # a list page of any platform is left out (T3), and so is a page the crawler marks 列表翻页 (item 22) or
            # one the school answered with no data (NO_DATA, the crawler's fa81f41)
            kept_page = (kind_class(record["kind"]) is not None and record["list_kind"] != LIST_PAGER
                         and record["area_mark"] != NO_DATA)
            lines = []
            for line in record["text"].split("\n"):
                m = IMG_LINE.fullmatch(line)
                if m is None:
                    lines.append(line)
                    continue
                img = self.images.get(_url_key(m.group(1), base))
                if img is None:
                    self.report.markers_unmatched += 1
                    continue
                if (page := row.get("网址") or "") and page not in img.shown_by:
                    img.shown_by.append(page)
                img.kept = img.kept or kept_page
                if img.text:
                    lines.append(img.text)
            record["text"] = "\n".join(lines)

    def image_record(self, img: _Image) -> dict | None:
        if img.kept:
            self.report.images_inlined += 1
            return None
        if _chars(img.text) < IMAGE_DOC_CHARS:
            self.report.images_no_text += 1
            return None
        row = img.row
        refs = self.look(row).refs
        shown = {_page_key(u) for u in img.shown_by}
        for u in [u for u in refs.linked if refs.zones[u] == UNKNOWN_ZONE]:  # no link to it there: shown, or chrome
            on = self.pages.at(u)
            if _page_key(u) in shown:
                refs.zones[u] = "content"
            elif on and all(str(page.get("序号")) in self.read for page in on):  # read, and its content has it not
                refs.linked.remove(u)
                refs.nav.append(u)
                refs.zones[u] = "nav"
        listed = {_page_key(u) for u in refs.zones}
        for u in img.shown_by:  # a page showing it that the row does not list
            if _page_key(u) not in listed:
                refs.linked.append(u)
                refs.zones[u] = "content"
        menu = self.menu_only(row, refs)
        if menu:
            self.report.images_menu_only += 1
        else:
            self.report.image_docs += 1
        record = self._record(self.ident(img.account.row), MENU_IMAGE if menu else "附件:图片", _where(row),
                              img.account.file, row.get("标题") or "", img.text, row, img.source, img.account.status)
        return record | refs.fields()

    def menu_only(self, row: dict, refs: _Refs) -> bool:
        """I4: every page referencing the image carries it only as a picture outside its content area (a unit
        banner): no reference from text, attachment area or an unknown place, at least one menu reference, and each
        such page holds an <img> of it. A photo a page album links from outside the content area, or a file of a
        folder the page links, has no <img> there: a reference no menu carries, so the image stays a document."""
        if refs.linked or not refs.nav:
            return False
        keys = {_url_key(u) for u in (row.get("网址"), row.get("最后落地网址")) if u}
        return all(any(keys & self.pages.pictures(page) for page in self.pages.at(u)) for u in refs.nav)

    def siblings(self) -> None:
        """Format siblings (G2), folded into the one with the most text (PDF on ties)."""
        candidates = [att for att in self.attachments if att.ext in FORMAT_EXT]
        buckets: dict[str, list[tuple[_Attachment, str, str]]] = {}
        for att in candidates:
            for links in att.refs.links.values():
                for row, context, anchor in links:
                    if row:
                        buckets.setdefault(row, []).append((att, context, _FILE_SIZE.sub("", anchor)))
        parent = {id(att): att for att in candidates}

        def find(att: _Attachment) -> _Attachment:
            while parent[id(att)] is not att:
                parent[id(att)] = parent[id(parent[id(att)])]
                att = parent[id(att)]
            return att

        for entries in buckets.values():
            for i, (x, cx, ax) in enumerate(entries):
                for y, cy, ay in entries[i + 1:]:
                    if x is not y and _siblings(x, cx, ax, y, cy, ay):
                        parent[id(find(x))] = find(y)
        groups: dict[int, list[_Attachment]] = {}
        for att in candidates:
            groups.setdefault(id(find(att)), []).append(att)
        for group in groups.values():
            if len(group) < 2:
                continue
            if len({att.ext for att in group}) < len(group):
                self.report.format_refused += 1
                continue
            keep = max(group, key=lambda att: (att.chars, att.ext == "pdf", -att.row))
            for other in sorted((att for att in group if att is not keep), key=lambda att: att.row):
                keep.record.setdefault("formats", []).append(
                    {"id": other.record["id"], "url": other.record["url"], "file": other.record["file"],
                     "format": other.ext, "kind": other.record["kind"]})
                keep.refs.take(other.refs)
                other.dropped = True
                self.report.format_merged += 1


def _siblings(x: _Attachment, cx: str, ax: str, y: _Attachment, cy: str, ay: str) -> bool:
    """Two links on one row are one document's formats: equal contexts, different extensions, no number told apart
    (anchors or file-name stems), and the same file name or two format words."""
    if cx != cy or x.ext == y.ext:
        return False
    if _numbers(ax) != _numbers(ay) or _numbers(x.stem) != _numbers(y.stem):
        return False
    return x.stem.casefold() == y.stem.casefold() or bool(_FORMAT_WORD.fullmatch(ax) and _FORMAT_WORD.fullmatch(ay))


def read_again(wholes: Mapping[str, Whole], pages: Sequence[tuple[str, str, str, bool]]) -> list[Again]:
    """Blueprint item 22 (owner 11-09, 「照这个做」: 「对上的区块几乎没字、整页却有很多字，就改读整页（去掉选单、页首、
    页尾），并记下来」), for one export: each page's answer (Again). pages: (its key in wholes, its read text, its kind,
    whether it may be read again); wholes: per file, its whole reading (Whole: pagetext.PageText.full, the body with
    its chrome) and its group. A group of fewer than TEMPLATE_PAGES files cannot tell its template from a page's own
    words: its pages are not judged. In a group, a line on TEMPLATE_SHARE of its files is template: the site's
    header, menus, footer. A page's own words are its other lines but link lines (a menu's items: 教务处's groups
    show their side menus only on their own pages) and images. A kept page (a notice or a page, documents.kind_class)
    that may be read again is read again when its own words come to OWN_CHARS and its read text gave under
    READ_SHARE of them (_reread) 【Claude: the template from the site's pages and the link lines, after the 11-09
    sweep and measures (research/pipeline_2026-11-02/reading_gaps_1109/); a short notice and an image-only page
    measure like a missed one on their page alone, 4,341 pages of the 111 exports (fallback_1109d.log)】."""
    groups: dict[str, list[str]] = defaultdict(list)
    for key, whole in wholes.items():
        groups[whole.group].append(key)
    seen: dict[str, Counter[str]] = {}
    cuts: dict[str, float] = {}
    for group, keys in groups.items():
        if len(keys) >= TEMPLATE_PAGES:
            seen[group] = Counter(line for key in keys for line in set(wholes[key].lines) if line)
            cuts[group] = max(TEMPLATE_PAGES, TEMPLATE_SHARE * len(keys))
    out: list[Again] = []
    for key, text, kind, may in pages:
        whole = wholes.get(key)
        if whole is None or whole.group not in seen:
            out.append(Again(False, None))
        elif not may or kind_class(kind) not in ("notice", "page"):
            out.append(Again(True, None))
        else:
            out.append(Again(True, _reread(whole, text, seen[whole.group], cuts[whole.group])))
    return out


def _reread(whole: Whole, text: str, seen: Counter[str], cut: float) -> str | None:
    """A page's text read again from its whole reading (read_again), or None when it stays as read. Kept, in the
    page's order, one blank line at most in a row: the page's own lines; each line the reading gave, as many times as
    it gave it, where it stands nearest the page's own lines (a template line the area holds, 「所友」, is a menu item
    too: only the area's copy comes back); a link line, but in a run of MENU_RUN or more (template, blank and image
    lines between them aside: a menu, which goes with the images inside it); an image only between the first and the
    last of those kept lines (not in the chrome before or after them). Template lines go 【Claude, 11-09 review】."""
    lines, links = whole.lines, whole.links
    template = [bool(line) and seen[line] >= cut for line in lines]
    image = [bool(IMG_LINE.fullmatch(line)) for line in lines]
    link = [n in links and not template[n] and not image[n] for n in range(len(lines))]
    own = [bool(line) and not (template[n] or link[n] or image[n]) for n, line in enumerate(lines)]
    words = sum(_chars(line) for n, line in enumerate(lines) if own[n])
    if words < OWN_CHARS or _chars(text) >= READ_SHARE * words:
        return None
    anchors = [n for n in range(len(lines)) if own[n]]
    keep = set(anchors)
    for line, times in Counter(x for x in text.split("\n") if x).items():
        spots = sorted((n for n, x in enumerate(lines) if x == line and not own[n]),
                       key=lambda n: min(abs(n - a) for a in anchors))
        keep.update(spots[:times])
    first, last = min(keep), max(keep)
    menu: set[int] = set()
    run: list[int] = []
    for n in range(len(lines) + 1):
        if n < len(lines) and n not in keep and (link[n] or template[n] or image[n] or not lines[n]):
            if link[n]:
                run.append(n)
            continue
        if len(run) >= MENU_RUN:
            menu.update(range(run[0], run[-1] + 1))
        run = []
    kept: list[str] = []
    for n, line in enumerate(lines):
        if not line:
            ok = bool(kept and kept[-1])
        elif n in keep:
            ok = True
        elif template[n] or n in menu:
            ok = False
        else:  # a link line alone, or an image
            ok = link[n] or first <= n <= last
        if ok:
            kept.append(line)
    return "\n".join(kept).strip("\n")


def _load_fallback(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    return {str(r["id"]): r for r in json.loads(Path(path).read_text(encoding="utf-8")) if isinstance(r, dict)}


def assemble(export_dir: Path, totext_dir: Path, out: Path, *, fallback: Path | None = FALLBACK,
             allow_pending: bool = False) -> AssembleReport:
    """corpus.json at out (written whole) from the export and <totext_dir>/cache; returns the report. Raises
    PendingInputs when inputs have no to-text result yet (unless allow_pending: they then take the fallback rule of a
    failed file), and ValueError for an out inside the export, ./data, archive*/ or the skeleton's folder. The 10-29
    fallback is read for 教务处's export only (FALLBACK_SITE; plan 2026-11-08 T3): for another site's it is off, and
    the report says so (fallback_off)."""
    export_dir, totext_dir, out = Path(export_dir), Path(totext_dir), Path(out)
    if build._protected(out, export_dir):
        raise ValueError(f"不能写到导出资料夹、./data、archive*/ 或 {build.SKELETON.name}/ 里面：{out}")
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = {int(r["序号"]): r for r in manifest.get("项目", [])}
    site = manifest.get("网站") or ""
    cache = Cache(totext_dir / "cache")
    accounts = account(export_dir, cache)
    pending = [a.key for a in accounts if a.status == "pending"]
    if pending and not allow_pending:
        raise PendingInputs(pending)
    work = _Assembly(export_dir, rows, cache, _load_fallback(fallback) if site == FALLBACK_SITE else {}, site)
    work.report.fallback_off = fallback is not None and site != FALLBACK_SITE
    work.report.pending = pending
    work.report.unstable_ids = len(ids.dup_rows(list(rows.values())) & {a.row for a in accounts if not a.member})
    members: dict[int, list[Account]] = {}
    for a in accounts:
        if a.member:
            members.setdefault(a.row, []).append(a)
    slots: list[dict | _Image] = []
    pages: list[tuple[dict, dict]] = []
    for a in accounts:
        if a.member:
            continue
        row = rows[a.row]
        kind = row.get("类型")
        if kind == "网页":
            record = work.page(a, row)
            slots.append(record)
            pages.append((record, row))
        elif kind == "图片":
            slots.append(work.image(a, row))
        elif kind == "附件":
            slots += work.attachment(a, row, members.get(a.row, []))
        elif kind == "资料":
            work.report.data_rows[str(a.row)] = data_reason(row)
    work.under_read()  # before inline: the image lines it brings back are read like the others
    work.inline(pages)
    work.siblings()
    kept = [att.record for att in work.attachments if not att.dropped]
    for att in work.attachments:
        att.record.update(att.refs.fields())
    dropped = {id(att.record) for att in work.attachments if att.dropped}
    records = []
    for slot in slots:
        record = work.image_record(slot) if isinstance(slot, _Image) else slot
        if record is not None and id(record) not in dropped:
            records.append(record)
    report = work.report
    report.with_context = sum(any(r["zones"].get(u) != "nav" for u in r["contexts"]) for r in kept)
    report.nav_only = sum(not r["linked"] and bool(r["nav_linked"]) for r in kept)
    report.records = len(records)
    report.by_kind = dict(Counter(r["kind"] for r in records).most_common())
    report.by_source = {s: n for s in SOURCES if (n := sum(r["source"] == s for r in records))}
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, json.dumps(records, ensure_ascii=False).encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.content.assemble",
                                 description="把导出资料夹和转文字的结果拼成 corpus.json（输入只读）")
    ap.add_argument("--export", type=Path, required=True, help="导出资料夹（archive-export/<网站>-<日期>）")
    ap.add_argument("--totext", type=Path, required=True, help="转文字的输出资料夹（缓存在 <totext>/cache）")
    ap.add_argument("--out", type=Path, required=True, help=f"写出的 corpus.json；报告写在同一个资料夹的 {REPORT}")
    fb = ap.add_mutually_exclusive_group()
    fb.add_argument("--fallback", type=Path, default=FALLBACK, help="转文字失败时，按档补用的 10-29 corpus.json")
    fb.add_argument("--no-fallback", action="store_true", help="不用 10-29 的文字")
    ap.add_argument("--allow-pending", action="store_true",
                    help="转文字还没跑完也拼：没有结果的档照失败处理（同一个档才补用 10-29 的文字）")
    args = ap.parse_args(argv)
    if not (args.export / "manifest.json").is_file():
        print(f"找不到导出资料夹的 manifest.json：{args.export}", file=sys.stderr)
        return 2
    try:
        report = assemble(args.export, args.totext, args.out, fallback=None if args.no_fallback else args.fallback,
                          allow_pending=args.allow_pending)
    except PendingInputs as e:
        print(f"还有 {len(e.keys)} 个档没转完（序号 {'、'.join(e.keys[:5])}{' 等' if len(e.keys) > 5 else ''}）："
              "等转文字跑完再拼，或加 --allow-pending", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    path = args.out.with_name(REPORT)
    write_atomic(path, (json.dumps(asdict(report), ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    sources = "、".join(f"{s} {n}" for s, n in report.by_source.items())
    fallback = ("10-29 补用：不是教务处的导出，没用" if report.fallback_off
                else f"10-29 补用被拒 {len(report.fallback_refused)}")
    print(f"{args.out}：{report.records} 笔（文字来源 {sources or '（没有）'}）；压缩档成员 {report.members}、"
          f"图片文件 {report.image_docs}（只在选单出现的图片 {report.images_menu_only}，建库时不算文件）；"
          f"格式合并 {report.format_merged}、按钮下载 {report.button_downloads}、"
          f"只在选单 {report.nav_only}；没转完 {len(report.pending)}、{fallback}；"
          f"编号可能会变的按钮行 {report.unstable_ids}；读到的字远少于整页、改读整页 {report.under_read}；结构化资料 {len(report.data_rows)} 份不成文件（其中建库不读的 "
          f"{sum(r == DATA_UNREAD for r in report.data_rows.values())} 份，原因见报告 data_rows）；报告 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
