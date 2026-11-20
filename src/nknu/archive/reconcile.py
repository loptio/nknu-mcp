"""What a record says without a request (round-1 design §10.1, §18.2a step 4, §10.2.1): the page that is almost
empty (thin), what a page's own scripts say a click does (R7), rejudge, a person's list of addresses to fetch, and a
person's school_side mark. Nothing here sends a request. (The reconciliation batch this file was named for, R1–R6,
V13, V14 with reconcile_site and its report, went with the judgment-layer cut of 11-19; the table stays in old
ledgers, unread.)

- thin: a 200 web page whose body (its platform's content area, else the whole page; a Google Sites embed-code
  block's words and what its pressed tabs and embed boxes brought counted in) has fewer than THIN_CHARS characters
  once the lines most pages of its kind repeat are taken out (its page type with TEMPLATE_MIN_PAGES distinct pages or
  more, else its site with as many, else nothing taken out; a line on TEMPLATE_SHARE of them or more; a page and its
  copies under other addresses count once), that needs no resource (page_res need must, the pager aside), whose
  type is no js or frame and that is no list walked with rows: url.flags thin, taken off again when that no longer
  holds (thin_pages, at a rejudge; Frontier keeps the mark over a fetch that did not grow the page's words). A page
  whose body is a picture is never thin (design §23 E3): one that needs a picture was not before either (a needed
  resource); one whose words alone would be thin and that holds, outside its layout areas, a picture left for round
  two or a data: picture of DATA_PICTURE_MIN bytes or more carries in_image instead (内容在图片里, for the text
  recognition of the organising stage; no gap), taken off the same way.
- R7: the school's own .js the page loads (its page_res script rows), read as text with the page's inline scripts by
  the same links.extract (jsread.bindings, selectors matched back to the page's elements): what clicking an element
  does that the page alone does not say is written on the page (page_res, zone r7:<zone>: a click that loads
  something, a writing that hands over to a function nobody read). A rejudge writes it.
- rejudge() reads the stored pages of some sites back from the WARC and works out again, with today's rules, what the
  crawler would have written of them (links.extract, round1.admit, pagetypes, the platform profiles, the presses
  already made: postback.Chains.rebuild), and the thin marks; what it finds new is queued (found_via rejudge), nothing
  fetched is fetched again, no fetched_at changes.
- enqueue() takes a person's list of addresses to fetch (found_via reconcile, the name the first lists came under);
  review_mark() is Frontier.mark_school_side's command.

Each command that writes refuses while a run holds run.lock and holds it itself while it writes; and refuses a
frontier.db no round-1 crawl made (the 10-24 archive is only read).

Every way of doing this the design does not spell out is Claude's (【Claude 的执行办法】): the line split and the
content-area fallback of thin, the r7: zone, the library .js left unread.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import google_api, jsread, links, monitor, outcome, postback, profiles, round1, scope
from nknu.archive.frontier import ROUND, Frontier, round_of, wall
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import DecodeError, Snapshot, decode_body

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
R7_ZONE = "r7:"  # page_res.zone of the rows R7 writes: rewritten by each reconcile and rejudge, never the crawler's
# the school's copies of libraries that work by attributes or are no page's code: not read (they bind every element of
# a kind, and define nothing a page's writing names), listed in the report
LIBRARY_JS = re.compile(
    r"(?i)(?:^|[/._-])(?:jquery|bootstrap|popper|swiper|slick|owl\.carousel|camera|fancybox|lightbox|modernizr|"
    r"html5shiv|respond|wow|aos|gsap|moment|vue|react|angular|lodash|underscore|d3|chart|select2|datatables|"
    r"magnific|isotope|masonry|imagesloaded|superfish|hoverintent|easing|colorbox|flexslider|prettyphoto|jcarousel|"
    r"cycle|nivo|fullcalendar|sweetalert|toastr|ckeditor|tinymce|gtag|analytics|fontawesome|font-awesome|"
    r"my97datepicker|wdatepicker|core-js|polyfill)(?:[._-][\w.-]*)?\.js$")
MAX_JS_BYTES = 512 * 1024  # a school script larger than this is listed, not read (a bundle, not a page's code)
_TEXT_DROP = ("script", "style", "noscript", "template")
_CODE_DEPTH = 2  # embed-code blocks inside embed-code blocks (links._DATA_CODE_DEPTH)


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
        if pid in skip or set(_flags(r)) & set(outcome.SAYS_NOTHING):  # the school says it holds nothing (T5 ③)
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


# ---------------------------------------------------------------- a stored page read as the crawler reads it


def _page_read(row, html: str, url: str) -> links.PageLinks:
    """links.extract of a stored page as the crawler reads it (its platform's content area, and the pager links its
    platform's pager box names: Crawler._page)."""
    page = links.extract(html, url, profiles.content_css(row["url"]))
    page.pagers.extend(u for u in postback.profile_pagers(url, html, page.base) if u not in page.pagers)
    return page


# ---------------------------------------------------------------- the commands' guards


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
    again as page_stored does. A menu (profiles.Menu, gap ⑩ of 11-08) is read with today's academic year as the
    crawler reads it, so that a menu its page cannot send stays unpressed (要人看; review of 11-09: read with no year,
    a rejudge dropped that mark): its options' presses are not named one by one (a menu not sent for this year is
    `retry unpressed`'s and verify check 4's, postback.menus_not_sent), and a menu's done answer is read with the year
    it asked and the list's row keys, as Chains._detail_files reads it 【Claude 的执行办法】."""

    def page_stored(self, row, html: str, warc: str | None, warc_offset: int | None,
                    page: links.PageLinks | None = None) -> postback.Stored:
        site = row["owner_site"] or scope.site_key(row["url"])  # the rows' keys and the walk's, as Chains'
        prof = profiles.profile_for(row["url"])  # the platform the page is written on (gaps ② ③ of 11-08)
        has_pagers = bool(page.pagers) if page is not None else row["page_role"] == "list"
        list_id = row["id"]
        if prof is None:
            return postback.Stored("list" if has_pagers else None, 0,
                                   self.get_walk(list_id) if has_pagers else None)
        pr = postback.read_page(prof, row["url"], html, site=site, list_url=row["url"], page=page, year=self._year())
        self.f.add_page_res(list_id, self._items(pr, row["url"]))
        self.rebuild(list_id)
        have = {(a[0], a[1]) for a in self.f.q("SELECT kind, row_key FROM action WHERE list_id=? AND kind NOT IN ('click', 'data')",
                                                (list_id,))}
        wanted = [p for p in (*pr.presses, *([pr.next] if pr.next is not None and pr.walk == "postback" else []))
                  if profiles.menu_choice(p.argument) is None]
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
                year = self._asked_year(a, a_prof or prof)  # a menu's answer: the year it asked (else None)
                got = postback.read_page(a_prof or prof, url, text, site=site,
                                         list_url=row["url"] if year is not None else None, parent_key=a["row_key"],
                                         step=a["step"], walks=("none",), year=year)
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
    """`nknu archive enqueue <清单>` (design §18.2b): the addresses a person confirmed, and only those,
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
