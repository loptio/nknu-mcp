"""An exported HTML page as lines of text (plan 2026-11-02 T3a; plan 2026-11-08 T2 for every platform). Strict UTF-8
first (the school's pages claim big5 and are UTF-8; bytes handed to a parser come out empty). script, style, noscript
and template are dropped. What a page's text is read from is decided per page by where it was read (page_roots, T2):
the content area of its platform profile (profiles.content_css) when that matches on the page, each outermost match
once, in document order; else nothing on a profiled detail page (c.nknu's deleted notice is another template); else
the whole body under the profile's download paths or 教务处's static folders; else the body. A content area and the
body are read without their chrome (_chrome: header, nav, footer and aside, the crawler's own text measure, and their
ARIA roles; T2 review: WebIdx's area holds its breadcrumb nav), an area root itself whatever it is; a whole body keeps
everything. Block elements and <br> break lines; text runs are whitespace-squashed; at most one
blank line in a row. An image is a line of its own, [[img:src]], where it sits, so the image's OCR text can later
stand in its place. A drop-down <select> reads as the one option it shows, in its line (blueprint item 22, owner
11-09 「照这个做」: the course tables' 学年 menu reads 113, not every year it offers); a list box (multiple, or a size
over 1) shows its options, one line each.

Every page is also read whole, its chrome kept (PageText.full), with the lines that are the words of one link on the
page marked (PageText.links: a menu's items): assemble reads a page again from them when what its reading gave holds
much less than the page's own words (blueprint item 22; content.assemble's under_read).

Tables: a data table is one line per row, | c1 | c2 |, each cell squashed to one line (images inside go on the lines
after the table); a first row holding <th> is followed by | --- | … |. A layout table is read like any other block,
cell by cell, keeping its lines and images in place. Which is which follows Mozilla Readability's _markDataTables,
read on the table's own rows (a nested table's are its own): role=presentation is layout; th, thead, tfoot, caption,
col, colgroup or a summary make a data table; a nested table, one row or one column make a layout table; anything
else is data (Readability's size rules would call a 3×3 table of short cells layout). Static pages wrap their text in
one-column tables (926 of their 999 tables in the 10-27 export): read as rows, every such page would be one line.

page_kind names a page by its URL and, on a profiled platform other than the c.nknu template, by the list grid its
read content shows (list_grid, read from the page by the caller: T2 review, a 书表 list and a prose page of WebIdx have
one URL shape). The to-text accounting names a page by its URL alone: no result depends on the kind (its variant is
the reading). PAGE_VERSION is the page rule's own version (T2): the accounting's page route carries it
(page_route_kind), and the cache reads a page result of another version as not done (read_now), so pages, and only
pages, are read again when the rule or the platform profiles change, while PIPELINE_VERSION, and every PDF and OCR
result with it, stays."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import SplitResult, parse_qs, urlsplit

import yaml
from selectolax.lexbor import LexborHTMLParser, LexborNode

from nknu.archive import profiles, scope
from nknu.archive.profiles import CNKNU
from nknu.settings import load_settings

IMG_MARK = "[[img:{src}]]"
IMG_LINE = re.compile(r"^\[\[img:(.+)\]\]$", re.MULTILINE)
DROP_TAGS = frozenset({"script", "style", "noscript", "template"})  # skipped with all they hold
# what a page read as its body or by its content area leaves out besides DROP_TAGS, wherever it sits below what is
# read (_chrome): the crawler's own text measure (archive.links._TEXT_DROP, url.text_chars) leaves out the same tags
# (plan 2026-11-08 T2)
CHROME_TAGS = frozenset({"header", "nav", "footer", "aside"})
# and elements of these roles, the ARIA names of those four (banner, navigation, contentinfo, complementary) and of a
# site's search box and menus (T2 review: Google Sites' 「Search this site」 banner and 「Report abuse」 menu, Google
# Forms' menu; read only, on 94 hosts of the 10-24 archive read by the body rule they hold menus, breadcrumbs and search
# forms, and none sits in a content area of the 教务处 export or the four pilot exports). Not aria-hidden="true", which
# the review proposed too: Wix galleries keep their photos under it (290 pictures on 40 pages of the round-2 site
# mandy900316.wixsite.com), and a picture left out of a read page becomes a menu image (I4) 【Claude】
CHROME_ROLES = frozenset({"banner", "navigation", "contentinfo", "complementary", "search", "menu"})
_CHROME_CSS = ", ".join([*sorted(CHROME_TAGS), "[role]"])
BLOCK_TAGS = frozenset({
    # the plan's list
    "p", "div", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
    "footer", "dd", "dt", "blockquote", "pre", "hr", "form", "center",
    # the rest of HTML's block elements, and a layout table's parts
    "html", "body", "main", "nav", "aside", "address", "dl", "figure", "figcaption", "fieldset", "legend", "details",
    "summary", "option", "caption", "thead", "tbody", "tfoot", "tr", "td", "th",
})
DATA_TABLE_TAGS = frozenset({"th", "thead", "tfoot", "caption", "col", "colgroup"})
CELL_TAGS = ("td", "th")
STATIC = "静态"  # page_kind prefix of a static page under uploadfile/userfiles/files/
_STATIC_DIRS = ("spaper", "nknupaper", "sinfo")  # 教务处's: its school paper, its journal, its static pages
OTHER, OFFSITE = "其他", "外站"  # page_kind prefixes: a c.nknu-template address that is no .aspx page; not the school's
PRESS, PAGER = "按钮=", "第"  # the export's fragment of a row a press brought: #按钮=<row> (detail), #第N页 (pager)
ROUND1_LIST = "archive_round1.json"  # the crawler's site list, in the configuration directory (read only)
SITE_TABLE = "site_units.yaml"  # the content side's site table (nknu.content.sites reads it): every site it builds
_URL_JUNK = re.compile(r"[\t\n\r]")  # browsers drop these from a URL (WHATWG URL)
_SIZE = re.compile(r"[\t\n\f\r ]*\+?([0-9]+)")  # a select's size: HTML's rules for parsing non-negative integers
_SELECT_PARTS = frozenset({"option", "optgroup", "hr"})  # what a select holds when it is closed

# How page_roots reads a page when the content area of its profile does not match: nothing (a profiled detail page),
# the whole body (a download path, a static folder), the body without its chrome. page_reading names them.
NOTHING, WHOLE, BODY = "nothing", "whole", "body"
_AREA = "area"  # page_roots took the profile's content area
_READ_WORDS = {NOTHING: "不读", WHOLE: "整页读", BODY: "读去掉页首、选单、页尾的整页"}
# The page rule's own version (plan 2026-11-08 T2): bumped by hand on every change to how a page is read (page_roots,
# _chrome, _STATIC_DIRS, the walk). PAGE_VERSION adds what the rule takes from the crawler's platform profiles.
# pages-3 (T2 review): chrome is left out below a content area too, and it takes in the ARIA roles; a content area
# matched by two selectors is read once. pages-4 (blueprint item 22, owner 11-09): a drop-down reads as the option it
# shows; every page's whole reading and its link lines (PageText.full, links).
PAGE_RULE = "pages-4"


@dataclass(frozen=True)
class PageText:
    text: str
    images: tuple[str, ...]  # each [[img:src]] marker's src, in text order
    title: str  # #lbl_Title, one line
    album: str  # #lbl_AlbumName, one line
    full: str = ""  # the whole body read with its chrome, whatever the page's reading (blueprint item 22)
    links: tuple[int, ...] = ()  # the lines of full (0-based) that are the words of one link on the page


class PageDecodeError(ValueError):
    """A page that is not UTF-8; str() is the accounting reason."""


def page_version(profs: Iterable[profiles.Profile] = profiles.PROFILES) -> str:
    """PAGE_RULE, ':' and the first 10 hex characters of the sha256 of PAGE_RULE and what page_roots takes from each
    platform profile: its content area, its download and detail paths, and its name and sites (which pages it reads:
    a site the crawler session moves to another profile is read the new way) 【Claude: name and sites】."""
    taken = [[p.name, p.sites, p.content_css or "", p.download_paths, p.detail_paths] for p in profs]
    digest = hashlib.sha256((PAGE_RULE + json.dumps(taken, ensure_ascii=False)).encode("utf-8")).hexdigest()
    return f"{PAGE_RULE}:{digest[:10]}"


PAGE_VERSION = page_version()


@lru_cache(maxsize=1)
def _round1_sites() -> frozenset[str]:
    """The round-1 site keys of the crawler's list (config/archive_round1.json, NKNU_CONFIG_DIR as the crawler reads
    it); a missing list stops the caller, never makes the school's sites 外站 quietly."""
    data = json.loads((load_settings().config_dir / ROUND1_LIST).read_text(encoding="utf-8"))
    return frozenset(str(s.get("site") or "") for s in data.get("sites", ()) if str(s.get("round")) == "1")


@lru_cache(maxsize=1)
def _table_sites() -> frozenset[str]:
    """The site keys of the content side's site table (config/site_units.yaml, read as plain YAML so the to-text step
    needs nothing of nknu.content): since 11-17 it holds the round-two sites the crawler handed over, the units' own
    sites off the school's domain (a Wix site, a GitHub page, a Google Site under /view/), whose pages are the
    school's as much as a round-1 site's (11-17: they read 外部連結 in answers). A missing table stops the caller, as
    a missing round-1 list does. Which pages are read how does not change (page_reading never asks the kind; the
    to-text cache keys a page without it: accounting), so no page is read again."""
    data = yaml.safe_load((load_settings().config_dir / SITE_TABLE).read_text(encoding="utf-8")) or {}
    return frozenset(str(k) for k in (data.get("sites") or {}))


def _static_folder(parts: SplitResult) -> str:
    """The static folder of 教务处's a path is under ('' for none)."""
    path = parts.path.lower()
    return next((f for f in _STATIC_DIRS if f"/uploadfile/userfiles/files/{f}/" in path), "")


def page_kind(url: str, grid: str | None = None) -> str:
    """The kind of a page (plan 2026-11-08 T2) by its URL and, on a profiled platform other than the c.nknu template,
    by the list grid its read content shows (grid: list_grid of the page where it was read; None when the page was not
    read, the URL alone):
    - a c.nknu-template host (profiles.CNKNU's sites, its own domains too): the 10-29 corpus's names, by the URL alone,
      which documents.EXCLUDED_KINDS uses: 静态:<folder> for 教务处's static folders, the leaf without .aspx (page(sn)
      for page.aspx?sn=), 其他:<leaf> for a leaf that is no .aspx, #翻页/按钮 after a row a press brought;
    - another profiled platform: detail:<profile> for its detail paths; a row a detail press brought (#按钮=: the
      export writes a pager press #第N页, and a download press is a file) detail:<profile>#按钮 whatever its list's
      own kind 【Claude】; a pager press's row list:<profile>#翻页 (the export writes #第N页 for a list's pages only);
      list:<profile> for a page whose read content shows one of the profile's list grids (grid), else page:<profile>.
      A list is read from the page, not the URL (T2 review): a 书表 list (WebIdx's menuid=N, cLv2's page=N) and a
      prose page have one URL shape, and the crawler's Rows.paths and Rows.query say where it walks a list, so
      WebIdx's ^webidx=N$ would make the home spelt cwebidx=N another kind; the URL alone says page:<profile>;
    - a school site no profile describes: page. That is a host under nknu.edu.tw or a round-1 site key such as a
      Google Site (the plan's two), or what else the crawler counts as the school's (scope.school_url: its 140.127.*
      addresses, its accounts' Google Sites in round 2) 【Claude: school_url】, or a site key of the content side's
      site table (_table_sites: the round-two units' own sites, 11-18);
    - anything else: 外站:<host>."""
    bare, _, frag = url.partition("#")
    try:
        parts = urlsplit(bare)
    except ValueError:
        return f"{OTHER}:"
    site = scope.site_key(bare)
    if CNKNU.covers(site):
        if folder := _static_folder(parts):
            return f"{STATIC}:{folder}"
        leaf = parts.path.lower().rsplit("/", 1)[-1]
        if not leaf.endswith(".aspx"):
            return f"{OTHER}:{leaf}"
        kind = leaf[:-5]
        if kind == "page" and "sn" in parse_qs(parts.query):
            kind = "page(sn)"
        return kind + "#翻页/按钮" if "#" in url else kind
    profile = profiles.profile_for_site(site)
    if profile is not None:
        if frag.startswith(PRESS):
            return f"detail:{profile.name}#按钮"
        if frag.startswith(PAGER):
            return f"list:{profile.name}#翻页"
        if profiles.page_kind(bare) == "detail":
            return f"detail:{profile.name}"
        return f"{'list' if grid else 'page'}:{profile.name}"
    if scope.school_url(bare) or site in _round1_sites() or site in _table_sites():  # school_url: nknu.edu.tw, more
        return "page"
    return f"{OFFSITE}:{parts.netloc}"


def _list_rows(profile: profiles.Profile) -> list[profiles.Rows]:
    """The profile's row sets that walk a list (a detail's attachments, walk none, are no list)."""
    return [rs for rs in profile.rows if rs.walk != "none"]


def list_grid(tree: LexborHTMLParser, url: str) -> str:
    """The name of the list grid (profiles.Rows that walk a list, in the profile's order) a page's read content shows,
    '' for none (T2 review: list:<profile> in page_kind, which Task 3 leaves out as a list page). A grid counts when it
    sits in what page_roots reads (so neither in a menu or a side card, its chrome, nor in a dropped element) and holds
    a row with the row set's title (with no title, a link), as the crawler reads a list's rows (postback.read_page), or
    the platform's words for an empty list (Rows.empty); a grid with neither shows no list here. Rows.paths and
    Rows.query are not asked (page_kind says why) 【Claude】. url: where the page was read. Never on a c.nknu-template
    host (its pages keep their 10-29 names) or a site no profile describes."""
    p = profiles.profile_for(url)
    if p is None or p.name == CNKNU.name or not (lists := _list_rows(p)):
        return ""
    read = {n.mem_id for n in page_roots(tree, url)}
    for rs in lists:
        for grid in tree.css(rs.grid):
            if not _read_in(grid, read):
                continue
            if any(row.css_first(rs.title or "a") is not None for row in grid.css(rs.row)):
                return rs.name
            text = " ".join((grid.text(separator=" ") or "").split())
            if any(w in text for w in rs.empty):
                return rs.name
    return ""


def _read_in(node: LexborNode, read: set[int]) -> bool:
    """Whether node is inside (or is) one of the parts read (their mem_ids), with no dropped element between."""
    while node is not None:
        if node.mem_id in read:
            return True
        if node.tag in DROP_TAGS:
            return False
        node = node.parent
    return False


def _whole(url: str) -> bool:
    """Read whole when no content area matches: under the profile's download paths, or 教务处's static folders (on a
    c.nknu-template host, as page_kind names them)."""
    if profiles.page_kind(url) == "download":
        return True
    try:
        parts = urlsplit(url.partition("#")[0])
    except ValueError:
        return False
    return CNKNU.covers(scope.site_key(url)) and bool(_static_folder(parts))


def page_reading(url: str) -> str:
    """How page_roots reads a page at url, its bytes aside: '<profile>:' when that platform's content area is tried
    first, then what stands when it does not match: nothing (a profiled detail page), whole (the whole body) or body
    (the body without its chrome). The accounting shares one result among rows of one content only when they are read
    alike."""
    p = profiles.profile_for(url)
    area = p.name if p is not None and p.content_css else ""
    rest = NOTHING if area and profiles.page_kind(url) == "detail" else WHOLE if _whole(url) else BODY
    return f"{area}:{rest}" if area else rest


def page_route_kind(url: str, where: str = "") -> str:
    """A page route's page_kind (accounting.input_route; result.json keeps it): the page's kind by its 网址, how it is
    read where it was read (page_reading of where, 最后落地网址 when it was redirected, else url) and PAGE_VERSION, joined
    by |."""
    return "|".join((page_kind(url), page_reading(where or url), PAGE_VERSION))


def reading_of(route_page_kind: str) -> str:
    """What a page's result depends on besides its bytes, from its route's page_kind: how it is read and the page
    rule's version, 'reading|version' ('' for any other route's)."""
    parts = route_page_kind.rsplit("|", 2)
    return "|".join(parts[1:]) if len(parts) == 3 else ""


def read_now(route_page_kind: str) -> bool:
    """Whether a page result was read by this page rule: PAGE_VERSION ends its page_kind (a result of the 11-02 run has
    none, an older rule's another)."""
    parts = route_page_kind.rsplit("|", 2)
    return len(parts) == 3 and parts[2] == PAGE_VERSION


def reading_words(reading: str) -> str:
    """A reading (reading_of) in words, for the accounting's reasons."""
    how = reading.split("|", 1)[0]
    area, _, rest = how.rpartition(":")
    words = _READ_WORDS.get(rest, rest)
    return f"先读 {area} 的正文区，对不上就{words}" if area else words


class _Lines:
    """Lines being written: text runs gather into the current line until a break."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.run: list[str] = []
        self.images: list[str] = []

    def text(self, s: str) -> None:
        self.run.append(s)

    def _flush(self) -> bool:
        line = " ".join("".join(self.run).split())
        self.run = []
        if line:
            self.lines.append(line)
        return bool(line)

    def block(self) -> None:
        """End the current line, if it holds any text."""
        self._flush()

    def br(self) -> None:
        """End the current line; an empty one becomes a blank line."""
        if not self._flush():
            self.lines.append("")

    def line(self, s: str) -> None:
        self._flush()
        self.lines.append(s)

    def image(self, src: str) -> None:
        self.images.append(src)
        self.line(IMG_MARK.format(src=src))

    def result(self) -> list[str]:
        self._flush()
        out: list[str] = []
        for line in self.lines:
            if line or (out and out[-1]):
                out.append(line)
        while out and not out[-1]:
            out.pop()
        return out


def _src(img: LexborNode) -> str:
    src = _URL_JUNK.sub("", img.attributes.get("src") or "").strip()
    if src.startswith("data:"):  # an inline image: its media type only, never kilobytes of base64
        src = src.split(",", 1)[0].split(";", 1)[0]
    return src


def _own(table: LexborNode, tags: frozenset[str] | set[str], skip: Collection[int] = frozenset()) -> list[LexborNode]:
    """table's descendants with these tags, in document order, not looking inside a nested table, a dropped element
    or an element of skip (mem_ids: the chrome a reading leaves out)."""
    found: list[LexborNode] = []
    stack = list(reversed(list(table.iter())))
    while stack:
        node = stack.pop()
        if node.tag in DROP_TAGS or node.mem_id in skip:
            continue
        if node.tag in tags:
            found.append(node)
        if node.tag != "table":
            stack.extend(reversed(list(node.iter())))
    return found


def _is_layout(table: LexborNode) -> bool:
    attrs = table.attributes
    if (attrs.get("role") or "").strip().lower() in ("presentation", "none"):
        return True
    if (attrs.get("summary") or "").strip() or _own(table, DATA_TABLE_TAGS):
        return False
    rows = _own(table, {"tr"})
    if len(rows) <= 1 or _own(table, {"table"}):
        return True
    return max(len([c for c in row.iter() if c.tag in CELL_TAGS]) for row in rows) <= 1


def _disabled(option: LexborNode) -> bool:
    """An option HTML calls disabled: its own disabled attribute, or its optgroup's."""
    group = option.parent
    return "disabled" in option.attributes or (group is not None and group.tag == "optgroup"
                                               and "disabled" in group.attributes)


def _shown(select: LexborNode) -> str | None:
    """The words a drop-down <select> shows (blueprint item 22, owner 11-09 「照这个做」), by HTML's selectedness: the
    last option with the selected attribute, else the first option not disabled (nor in a disabled optgroup), else the
    first; an option's label attribute, else its text; '' with no option. None, read as before (every option a line
    of its own), for a list box (multiple, or a size over 1 by HTML's rules for non-negative integers: 「3px」 is 3)
    and for a select that holds more than options, optgroups and hr, or an option holding an element: lexbor nests
    what follows an unclosed select inside it (to the next cell end, input or select), and reading one option of it
    would drop that (11-09 review) 【Claude】."""
    attrs = select.attributes
    if "multiple" in attrs:
        return None
    if (size := _SIZE.match(attrs.get("size") or "")) and int(size.group(1)) > 1:
        return None
    for node in select.css("*"):  # the select itself, then its descendants
        if node.mem_id == select.mem_id:
            continue
        if node.tag not in _SELECT_PARTS or (node.tag == "option" and any(True for _ in node.iter())):
            return None
    options = select.css("option")
    if not options:
        return ""
    chosen = [o for o in options if "selected" in o.attributes]
    pick = chosen[-1] if chosen else next((o for o in options if not _disabled(o)), options[0])
    return " ".join(((pick.attributes.get("label") or "").strip() or pick.text(deep=True) or "").split())


def _cell_text(cell: LexborNode, images: list[str], skip: Collection[int] = frozenset()) -> str:
    """Everything in a cell (nested tables too) as one squashed line, but the elements of skip (mem_ids: the chrome a
    reading leaves out; a cell of it reads '', so its row keeps its columns); its images go to images, in order."""
    parts: list[str] = []
    stack: list[tuple[LexborNode, bool]] = [(cell, False)]
    while stack:
        node, leaving = stack.pop()
        if leaving or node.tag == "br":
            parts.append(" ")
        elif node.tag == "-text":
            parts.append(node.text(deep=False) or "")
        elif node.tag == "img":
            if src := _src(node):
                images.append(src)
        elif node.is_element_node and node.tag not in DROP_TAGS and node.mem_id not in skip:
            if node.tag == "select" and (shown := _shown(node)) is not None:
                parts.append(f" {shown} ")
                continue
            if node.tag in BLOCK_TAGS:  # a space on the way in and on the way out: 「<p>甲</p><p>乙</p>」 is 甲 乙
                parts.append(" ")
                stack.append((node, True))
            stack.extend((child, False) for child in reversed(list(node.iter(include_text=True))))
    return " ".join("".join(parts).split())


def _data_table(table: LexborNode, out: _Lines, skip: Collection[int]) -> None:
    images: list[str] = []
    out.block()
    for caption in _own(table, {"caption"}, skip):
        if text := _cell_text(caption, images, skip):
            out.line(text)
    first = True
    for row in _own(table, {"tr"}, skip):
        cells = [c for c in row.iter() if c.tag in CELL_TAGS]
        texts = [_cell_text(c, images, skip).replace("|", "\\|") for c in cells]
        if not any(texts):
            continue
        out.line("| " + " | ".join(texts) + " |")
        if first and any(c.tag == "th" for c in cells):
            out.line("| " + " | ".join("---" for _ in cells) + " |")
        first = False
    for src in images:
        out.image(src)


def _walk(root: LexborNode, out: _Lines, skip: Collection[int] = frozenset()) -> None:
    """root's content in document order, but the elements of skip (mem_ids: the chrome a reading leaves out, never
    root itself) and what DROP_TAGS drops; block elements break lines on the way in and on the way out."""
    stack: list[tuple[LexborNode, bool]] = [(root, False)]
    while stack:
        node, leaving = stack.pop()
        if leaving:
            out.block()
            continue
        tag = node.tag
        if tag == "-text":
            out.text(node.text(deep=False) or "")
            continue
        if not node.is_element_node or tag in DROP_TAGS or node.mem_id in skip:
            continue
        if tag == "select" and (shown := _shown(node)) is not None:
            out.text(f" {shown} ")  # in its line, as it shows: 「學年度： 113 學期： 1」
            continue
        if tag == "br":
            out.br()
            continue
        if tag == "img":
            if src := _src(node):
                out.image(src)
            continue
        if tag == "table" and not _is_layout(node):
            _data_table(node, out, skip)
            continue
        if tag in BLOCK_TAGS:
            out.block()
            stack.append((node, True))
        stack.extend((child, False) for child in reversed(list(node.iter(include_text=True))))


def _outermost(found: list[LexborNode]) -> list[LexborNode]:
    """The matches no other match holds and no dropped element holds, in document order, each once (a selector list
    gives an element once per selector it matches, next to each other: 0 pages of the 教务处 export and the four pilot
    exports, read only, T2 review)."""
    ids = {n.mem_id for n in found}
    roots: list[LexborNode] = []
    seen: set[int] = set()
    for node in found:
        if node.mem_id in seen:
            continue
        seen.add(node.mem_id)
        up = node.parent
        while up is not None and up.mem_id not in ids and up.tag not in DROP_TAGS:
            up = up.parent
        if up is None:  # outermost, and in no dropped element
            roots.append(node)
    return roots


def _parts(tree: LexborHTMLParser, url: str) -> tuple[str, list[LexborNode]]:
    """(how, what) page_roots reads, in its order: _AREA and the content area's outermost matches; NOTHING and [] on a
    profiled detail page whose area does not match (a profile with no area has nothing to miss: such a page reads on
    by the rules after 【Claude】); WHOLE or BODY and [the body]."""
    css = profiles.content_css(url)
    if css:
        if found := _outermost(tree.css(css)):
            return _AREA, found
        if profiles.page_kind(url) == "detail":
            return NOTHING, []
    if tree.body is None:  # a frameset: no body to read
        return NOTHING, []
    return (WHOLE if _whole(url) else BODY), [tree.body]


def _chrome(node: LexborNode) -> bool:
    """Whether an element is chrome: a header, nav, footer or aside, or an element with one of CHROME_ROLES among its
    roles (any case; a role attribute holds a list, the first a browser knows applies, any of these marks chrome)."""
    if node.tag in CHROME_TAGS:
        return True
    role = node.attributes.get("role")
    return bool(role) and not CHROME_ROLES.isdisjoint(role.lower().split())


def _chrome_below(root: LexborNode) -> dict[int, LexborNode]:
    """The chrome elements below root, by mem_id; root itself is read whatever it is (an area root that is a nav)."""
    return {n.mem_id: n for n in root.css(_CHROME_CSS) if n.mem_id != root.mem_id and _chrome(n)}


def _without_chrome(root: LexborNode) -> list[LexborNode]:
    """root as the outermost parts that hold no chrome element below it, in document order: root itself when it
    holds none, else each child of a part that holds one, but a chrome or dropped element itself (nothing of it is
    read) and a blank text."""
    chrome = _chrome_below(root)
    holders: set[int] = set()  # root and the elements between it and a chrome element
    for node in chrome.values():
        up = node.parent
        while up is not None and up.mem_id not in holders:
            holders.add(up.mem_id)
            up = None if up.mem_id == root.mem_id else up.parent
    parts: list[LexborNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.mem_id not in holders:
            parts.append(node)
            continue
        stack.extend(child for child in reversed(list(node.iter(include_text=True)))
                     if (child.is_element_node and child.mem_id not in chrome and child.tag not in DROP_TAGS)
                     or (child.tag == "-text" and (child.text(deep=False) or "").strip()))
    return parts


def page_roots(tree: LexborHTMLParser, url: str) -> list[LexborNode]:
    """What a page's text is read from (plan 2026-11-08 T2), each outermost and in document order, decided per page
    in this order: (1) the outermost matches of its platform profile's content area (profiles.content_css) when that
    matches on this page outside a dropped element; (2) else nothing on a profiled detail page (c.nknu's deleted notice
    is another template); (3) else the whole body under the profile's download paths or 教务处's static folders; (4) else
    the body. An area and the body are taken without their chrome (_chrome below each: header, nav, footer, aside, the
    crawler's own text measure, and their ARIA roles; T2 review) and what DROP_TAGS drops, as the parts of them that
    hold none; a whole body keeps everything. url: where the page was read. links.content_roots is this
    function, so the zones of a page's links agree with its text."""
    how, roots = _parts(tree, url)
    if how in (_AREA, BODY):
        return [part for root in roots for part in _without_chrome(root)]
    return roots


def _one_line(node: LexborNode | None) -> str:
    if node is None:
        return ""
    out = _Lines()
    _walk(node, out)
    return " ".join(line for line in out.result() if line and not IMG_LINE.fullmatch(line))


def _link_words(tree: LexborHTMLParser) -> set[str]:
    """The words of each link on the page (<a>), squashed as a line is: a line that is one of them is a link line."""
    return {words for a in tree.css("a") if (words := " ".join((a.text(deep=True) or "").split()))}


def page_text(html: bytes, url: str) -> PageText:
    """The page's text, its images and its title fields (#lbl_Title, #lbl_AlbumName, read as before T2). url: where the
    page was read (最后落地网址 when it was redirected, else 网址), which says what its text is read from (page_roots; an
    area or a body is walked whole with its chrome skipped, the same nodes page_roots gives, without breaking lines
    between them). full: the whole body walked with nothing skipped but what DROP_TAGS drops, and links its lines
    that are the words of one link on the page (blueprint item 22: assemble's under_read reads them)."""
    try:
        source = html.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise PageDecodeError(f"网页不是 UTF-8 编码（第 {e.start} 个字节起读不了）") from e
    tree = LexborHTMLParser(source)  # never changed: the dropped elements are skipped while reading (lexbor parses
    # with scripting off, so a noscript can hold another one, and removing both would touch freed memory)
    how, roots = _parts(tree, url)
    skip: set[int] = set()
    if how in (_AREA, BODY):
        for root in roots:  # outermost: no root is below another, so none is skipped
            skip.update(_chrome_below(root))
    out = _Lines()
    for root in roots:
        out.block()
        _walk(root, out, skip)
        out.block()
    lines = out.result()
    whole = _Lines()
    if tree.body is not None:
        _walk(tree.body, whole)
    full = whole.result()
    words = _link_words(tree)
    return PageText("\n".join(lines), tuple(out.images), _one_line(tree.css_first("#lbl_Title")),
                    _one_line(tree.css_first("#lbl_AlbumName")), "\n".join(full),
                    tuple(n for n, line in enumerate(full) if line in words))
