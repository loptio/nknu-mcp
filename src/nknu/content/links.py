"""Every link of an exported page with its zone, the title its context gives and its row (addendum 2026-11-02 B, task
T6b). T7a uses them to keep an attachment's real parents (pages linking it from their text or their attachment area,
never from a menu), to title a file its page names only by a format or a file name, and to group the formats of one
form (one row of one page).

- target: the crawler's spelling of the address (scope.canonical, joined to the page's <base href> or URL as the crawler
  joined it) under the key documents.py joins linked pages by (_link_key). Compare match_key(target) with
  match_key(_link_key(网址)): the crawler wrote the addresses of one-scheme hosts in that scheme. Links that are no
  http(s) address (javascript:, mailto:, a bare #fragment, an empty href) and links inside script, style, noscript or
  template are left out.
- zone: attachments when a detail-files selector of the page's profile matches the <a> (#lbl_FileLink a, …); content
  inside the parts the page's text is read from (content_roots, pagetext.page_roots: the platform profile's content
  area when it matches, nothing on a profiled detail page that does not match, a download path's whole body, else the
  body; an area and a body without their chrome, header, nav, footer, aside and their ARIA roles, so a breadcrumb
  inside WebIdx's area is nav; plan 2026-11-08 T2 and its review); else nav.
- context: a named anchor without its file size and extension; for an anchor that names nothing (GENERIC) what its
  nearest line or table row says (context_title); else ''.
- row: page_url#<DOM path of the nearest tr, li or p>, '' when none.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode

from nknu.archive import profiles, scope
from nknu.archive.links import _base as link_base  # the crawler's <base href>, so targets match the manifest's 网址
from nknu.content.documents import _FILE_SIZE, _link_key
from nknu.totext.pagetext import BLOCK_TAGS, CELL_TAGS, DROP_TAGS, PageDecodeError
from nknu.totext.pagetext import page_roots as content_roots  # what the text is read from, shared (plan 11-08 T2)

ZONES = ("content", "attachments", "nav")
_EXT = sorted(scope.ATTACHMENT_EXT | scope.IMAGE_EXT, key=lambda e: (-len(e), e))
_FORMATS = "|".join(_EXT)
_WORDS = "下載|下载|點擊|点击|這裡|这里|此處|附件|附檔|檔案|档案|download|here|click|link|file|word|excel"
_CHARS = r"\W_\d〇一二三四五六七八九十百點点按請请此我檔档及和與与或"  # with punctuation and numbers: no name
# anchors that name nothing (used with fullmatch): formats, download words, numbers and punctuation in any mix, or a
# bare file name (printable ASCII without a space, with a file extension: u112.pdf)
GENERIC = re.compile(rf"(?i)(?:[{_CHARS}]|{_WORDS}|{_FORMATS})*|[\x21-\x7e]+\.(?:{_FORMATS})")
_EXT_TAIL = re.compile(rf"(?i)(?:\s*\.(?:{_FORMATS}))+$")  # 選課說明.docx.pdf: a converted file keeps its old one
_EMPTY_BRACKETS = re.compile(r"[(（\[【「『〔][\s、，,;；/／|｜]*[)）\]】」』〕]")  # what a removed link text leaves
# the ends of a title: spaces, separators, symbols and stray brackets go; a word character stays, and so does a bracket
# that opens (at the start) or closes (at the end) a part of the title
_LEAD = re.compile(r"^(?:[^\w(（\[【「『〔]|_)+")
_TRAIL = re.compile(r"(?:[^\w)）\]】」』〕]|_)+$")
_ATTACHMENT_N = re.compile(r"^附件\s*(?:[(（]?\s*[0-9０-９〇一二三四五六七八九十]+\s*[)）]?\s*[：:、.．]?|[：:])\s*")
_NUMBER = r"(?:\d{1,3}|[〇一二三四五六七八九十]{1,3})"
# a leading 序号: (一) (1) 一、 1. but not the 1. of 1.5
_LEAD_SERIAL = re.compile(rf"^(?:[(（]\s*{_NUMBER}\s*[)）]|{_NUMBER}\s*[、.．](?!\d))\s*")
_BARE_SERIAL = re.compile(_NUMBER)  # fullmatch: a first cell that only numbers its row
_OPENERS, _CLOSERS, _SEPARATORS = "(（[【「『〔", ")）]】」』〕", "、，,;；/／|｜"


@dataclass(frozen=True)
class PageLink:
    page_url: str
    target: str
    anchor: str  # the link text, one line, as written
    zone: str  # one of ZONES
    context: str
    row: str


def _ancestor(node: LexborNode, tags: Collection[str]) -> LexborNode | None:
    node = node.parent
    while node is not None and node.is_element_node:
        if node.tag in tags:
            return node
        node = node.parent
    return None


def _inside(node: LexborNode, ids: set[int]) -> bool:
    while node is not None:
        if node.mem_id in ids:
            return True
        node = node.parent
    return False


def _dom_path(node: LexborNode) -> str:
    """/html[1]/body[1]/…/tr[3]: each element with its place among its parent's elements of its tag."""
    steps = []
    while node is not None and node.is_element_node:
        place, sib = 1, node.prev
        while sib is not None:
            place += int(sib.is_element_node and sib.tag == node.tag)
            sib = sib.prev
        steps.append(f"{node.tag}[{place}]")
        node = node.parent
    return "/" + "/".join(reversed(steps))


def _clean(s: str) -> str:
    """One line; brackets a removed link text left empty are dropped."""
    return " ".join(_EMPTY_BRACKETS.sub(" ", " ".join(s.split())).split())


def _trim(s: str) -> str:
    """The ends of a title: spaces, separators, symbols and stray brackets at both ends, a leading 附件N： and a
    leading 序号 dropped."""
    before = None
    while s != before:
        before = s
        s = _ATTACHMENT_N.sub("", _LEAD_SERIAL.sub("", _LEAD.sub("", s)))
    return _TRAIL.sub("", s)


def _text_without_links(node: LexborNode) -> str:
    """node's text without link text, script, style, noscript or template; block elements and <br> as spaces."""
    parts: list[str] = []
    stack: list[tuple[LexborNode, bool]] = [(node, False)]
    while stack:
        n, leaving = stack.pop()
        if leaving or n.tag == "br":
            parts.append(" ")
        elif n.tag == "-text":
            parts.append(n.text(deep=False) or "")
        elif n.is_element_node and n.tag not in DROP_TAGS and n.tag != "a":
            if n.tag in BLOCK_TAGS:
                parts.append(" ")
                stack.append((n, True))
            stack.extend((child, False) for child in reversed(list(n.iter(include_text=True))))
    return "".join(parts)


def _row_cells(tr: LexborNode) -> list[LexborNode]:
    return [c for c in tr.iter() if c.tag in CELL_TAGS]


def _row_context(tr: LexborNode) -> str:
    """The row's cell texts without any link text, joined by a space; a first cell that only numbers the row is
    left out."""
    cells = [t for c in _row_cells(tr) if (t := _trim(_clean(_text_without_links(c))))]
    if cells and _BARE_SERIAL.fullmatch(cells[0]):
        cells = cells[1:]
    return _trim(" ".join(cells))


def _segments(box: LexborNode, a: LexborNode) -> list[tuple[LexborNode | None, str]]:
    """a's line before it inside box, cut at the other links: (the link before the text, None at the line start; the
    text up to the next link or a). Lines break at <br> and block elements; link text is left out."""
    start: tuple[LexborNode | None, str] = (None, "")
    segments = [start]
    stack: list[tuple[LexborNode, bool]] = [(box, False)]
    while stack:
        n, leaving = stack.pop()
        if leaving:
            segments = [start]
            continue
        if n.mem_id == a.mem_id:
            return segments
        if n.tag == "-text":
            link, text = segments[-1]
            segments[-1] = (link, text + (n.text(deep=False) or ""))
        elif n.tag == "br":
            segments = [start]
        elif n.tag == "a":
            segments.append((n, ""))
        elif n.is_element_node and n.tag not in DROP_TAGS:
            if n.tag in BLOCK_TAGS:
                segments = [start]
                stack.append((n, True))
            stack.extend((child, False) for child in reversed(list(n.iter(include_text=True))))
    return [start]


def _suffix_end(s: str) -> int:
    """Where the text written right after a link ends: its first separator outside brackets."""
    depth = 0
    for i, ch in enumerate(s):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        elif depth == 0 and (ch.isspace() or ch in _SEPARATORS):
            return i
    return len(s)


def _named(s: str) -> str:
    """s when it names something, else ''."""
    return "" if GENERIC.fullmatch(s) else s


def _anchor(a: LexborNode) -> str:
    """The link text, one line, without the file size the site writes after it."""
    return _FILE_SIZE.sub("", " ".join((a.text() or "").split()))


def _line_title(box: LexborNode, a: LexborNode) -> str:
    """The text before a on its line, read back from a. The text after another link is the next item's title only
    when it starts after a space, a separator or a closing bracket (「（pdf）、附件二：乙表（」); text written
    right after a link is that link's own up to its first separator (「31期(2019年3月)、」), and then the line
    names nothing for a. Text that names nothing (「、」, 「及」) passes the title before it on to the next link
    (「甲表（pdf、doc）」), but never past a link whose own text names its file (「乙表.pdf」)."""
    for link, s in reversed(_segments(box, a)):
        s = s.rstrip()  # spaces before a link are layout
        head, own = "", s
        if link is not None and s and not (s[0].isspace() or s[0] in _SEPARATORS or s[0] in _CLOSERS):
            cut = _suffix_end(s)
            head, own = s[:cut], s[cut:]
        if title := _named(_trim(_clean(own))):
            return title
        if _named(_trim(_clean(head))) or (link is not None and _named(_anchor(link))):
            return ""
    return ""


def context_title(a: LexborNode) -> str:
    """The title the page gives the file a links to: the anchor without its file size and extension, unless the anchor
    names nothing (GENERIC). Then the nearest of: an <li>, <p> or <dd> (the text before a on its line, without a leading
    附件N： or 序号 and a trailing colon or opening bracket), going on up when it gives nothing; a table cell, where the
    search ends: in a row of two or more cells the row's cell texts without any link text, a leading 序号 dropped, in a
    row of one cell (a static page's wrapper table) the cell's line. Else ''."""
    anchor = _anchor(a)
    if not GENERIC.fullmatch(anchor):
        return _EXT_TAIL.sub("", anchor)
    node = a.parent
    while node is not None and node.is_element_node:
        if node.tag in ("li", "p", "dd"):
            if title := _line_title(node, a):
                return title
        elif node.tag in CELL_TAGS:
            row = node.parent
            if row is not None and row.tag == "tr" and len(_row_cells(row)) >= 2:
                return _named(_row_context(row))
            return _line_title(node, a)
        node = node.parent
    return ""


def match_key(key: str) -> str:
    """A target, or the _link_key of a manifest 网址, without its scheme, to compare the two: the crawler writes the
    addresses of a host that answers on one scheme only in that scheme (crawler._scheme_rewrite, design §6.6), so a
    page's http:// link can name the manifest's https:// file (77 of the 10-27 export's 2,914 page links to its
    attachments). A key of ids.location_key (plan 2026-11-08 T1) carries no scheme already and is left as it is."""
    return key.split(":", 1)[1] if key.startswith(("http://", "https://")) else key


def _file_areas(tree: LexborHTMLParser, url: str) -> set[int]:
    """The <a> nodes the detail-files selectors of the page's profile match (any of its detail pages' and rows')."""
    p = profiles.profile_for(url)
    if p is None:
        return set()
    css = [d.files for d in p.details if d.files] + [r.detail_files for r in p.rows if r.detail_files]
    return {n.mem_id for sel in css for n in tree.css(sel)}


def page_links(html: bytes, page_url: str) -> list[PageLink]:
    """Every <a> of an exported page that leads to an http(s) address, in document order, but those inside script,
    style, noscript or template (never shown; T3a's page text leaves them out too). page_url: where the page was read
    (最后落地网址 when it was redirected); hrefs are joined to its <base href> when it has one, as the crawler joined
    them. A page that is not UTF-8 raises PageDecodeError (the caller accounts it); pages claim big5 and are UTF-8."""
    try:
        source = html.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise PageDecodeError(f"网页不是 UTF-8 编码（第 {e.start} 个字节起读不了）") from e
    tree = LexborHTMLParser(source)
    base_node = tree.css_first("base[href]")
    base = link_base(page_url, base_node.attributes.get("href") if base_node else None)
    roots = {n.mem_id for n in content_roots(tree, page_url)}
    files = _file_areas(tree, page_url)
    links = []
    for a in tree.css("a[href]"):
        url = scope.canonical(a.attributes.get("href") or "", base)
        if url is None or _ancestor(a, DROP_TAGS) is not None:
            continue
        zone = "attachments" if a.mem_id in files else "content" if _inside(a, roots) else "nav"
        row = _ancestor(a, ("tr", "li", "p"))
        links.append(PageLink(page_url, _link_key(url), " ".join((a.text() or "").split()), zone, context_title(a),
                              f"{page_url}#{_dom_path(row)}" if row is not None else ""))
    return links
