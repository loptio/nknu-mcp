"""Page types and JavaScript (round-1 design §8.1, §8.3): the one page-type key, the cheap signs a page reads that
its content needs a script, and what the crawl does with them. Nothing here sends anything or runs a script; the
browser check after the round (jscheck, §8.4) is not here. (The 10-25 browser test's results were brought into the
page_type table once, at the first seeding; that import, design §8.2, went with the judgment-layer cut of 11-19.)

- type_key(url): site|host|path template|parameter names, as the 10-25 test keyed its 1,093 types (page_types.json):
  the path lower-cased; when its last segment is a web page file (.html, .php, .aspx …) each run of digits in each
  segment is {n}, otherwise (a route: /news/page/2/, /9/458, an upload folder's file) the first segment stays and each
  later one is *; the names of the parameters that have a value, lower-cased and sorted (SSO's selpage among them, so
  WebIdx's home, list and item are three types). The site is scope.site_key's. This is click_scan.py's reading of the
  10-25 keys, which gives all 1,906 sample URLs their own key back (design §8.1 sketches the rule; where the sketch and
  the 1,906 samples differ, the samples win: no digits-only rule for routes, no selpage value in the key).
- measure(html): js_pages_scan.py's rules (lines 17-19, 53-54): strong signs (a 「載入中」 kind of text anywhere in the
  HTML, script strings too; an empty single-page-app root and its kin; a platform profile's content area that is there
  but holds no text, picture or attachment) and weak ones (under 150 characters with 3 or more scripts, or an ajax
  call with under 400 characters).
- observe(): design §8.3, for each 200 HTML page the crawl stores. A new type is todo, why=new. A strong sign, a click
  this round cannot press (need='round2', or a no-rule tab shown on load: click:shown_on_load), or weak signs on 5 or
  more pages and 30% of the type's pages, send an ok type (frame too, for a strong sign) back to todo (why hint, click,
  weak) when the type came from the import or the crawl; a type jscheck or a person judged keeps its verdict and the
  signs stay as the page's flag. The page with a strong sign carries js_hint whatever its type says (url_result
  holds it unchecked until jscheck opened that very page).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import parse_qsl, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import links, scope
from nknu.archive.frontier import Frontier, write_first

# ---------------------------------------------------------------- §8.1 the page-type key
# a last segment of these is a web page file: its path is a template with {n} for each run of digits; any other path is
# a route (or an upload folder's file) and is its first segment and one * per later segment
_PAGE_EXT = re.compile(r"\.(?:s?html?|php\d?|aspx?|jsp|cfm|xhtml|cgi|pl|txt|xml)$")


class TypeKey(NamedTuple):
    site: str
    host: str
    path_tpl: str
    params: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.site}|{self.host}|{self.path_tpl}|{self.params!r}"


def path_template(path: str) -> str:
    segs = [s for s in path.lower().strip("/").split("/") if s]
    if not segs:
        return ""
    if not _PAGE_EXT.search(segs[-1]):
        return "/".join([segs[0]] + ["*"] * (len(segs) - 1))
    return "/".join(re.sub(r"\d+", "{n}", s) for s in segs)


def type_parts(url: str, site: str | None = None) -> TypeKey:
    """The parts of a URL's page-type key; site: the row's site (scope.site_key(url) when not given)."""
    sp = urlsplit(url)
    names = tuple(sorted({k.lower() for k, _ in parse_qsl(sp.query)}))
    return TypeKey(scope.site_key(url) if site is None else site, (sp.hostname or "").lower(), path_template(sp.path),
                   names)


def type_key(url: str, site: str | None = None) -> str:
    """The page-type key of a page's URL (design §8.1), e.g. c.nknu.edu.tw/aca|c.nknu.edu.tw|aca/newsdetail.aspx|('gi',
    'nid')."""
    return type_parts(url, site).key


# ---------------------------------------------------------------- §8.3 the signs a page reads
# js_pages_scan.py:17-19, as the 10-25 scan read them (its 174 strong pages)
LOADING = re.compile(r"載入中|载入中|加載中|加载中|讀取中|读取中|Loading\.\.\.|loading\.\.\.")
SPA = re.compile(r'<div[^>]+id=["\'](?:app|root|__next|__nuxt)["\'][^>]*>\s*</div>|<app-root|ng-app=|v-cloak'
                 r"|\{\{\s*[\w.]+\s*\}\}|<nknu-[a-z-]+")
AJAX = re.compile(r"fetch\(\s*['\"`][^'\"`]+|\$\.(?:ajax|get|getJSON|post)\(|axios\.|XMLHttpRequest|/api/[\w./-]+")
WEAK_CHARS, WEAK_SCRIPTS, WEAK_AJAX_CHARS = 150, 3, 400  # js_pages_scan.py:53-54
WEAK_MIN_PAGES, WEAK_SHARE_TENTHS = 5, 3  # a type with weak signs on 5 or more pages and 30% of them (design §8.3)
_CONTENT_DROP = ("script", "style", "noscript", "template")
# what fills a content area without text: a picture, a player, a frame, a Google Sites embed-code block
_CONTENT_MEDIA = "img, picture, svg, video, audio, iframe, frame, embed, object, canvas, [data-code]"
_FILE_EXT = scope.ATTACHMENT_EXT | scope.IMAGE_EXT
_FILE_PATHS = scope.FILE_PATHS | scope.IMAGE_PATHS


@dataclass(frozen=True)
class Measure:
    text_chars: int  # the page's words (links.text_chars, or what extract() counted with embed-code blocks)
    scripts: int  # <script> elements, inline and external
    loading: bool  # a 「載入中」 kind of text anywhere in the HTML
    spa: bool  # an empty app root, an Angular or Vue template left unfilled, a custom element
    ajax: bool  # a script that asks for data: fetch('…'), $.ajax, axios, XMLHttpRequest, /api/
    content_found: bool = False  # the platform profile's content area is on the page
    content_empty: bool = False  # … and holds no text, picture or attachment

    @property
    def strong(self) -> bool:
        return self.loading or self.spa or self.content_empty

    @property
    def weak(self) -> bool:
        return ((self.text_chars < WEAK_CHARS and self.scripts >= WEAK_SCRIPTS)
                or (self.ajax and self.text_chars < WEAK_AJAX_CHARS))

    @property
    def signals(self) -> tuple[str, ...]:
        """For a report: which signs the page has."""
        out = [name for name, on in (("loading", self.loading), ("spa", self.spa),
                                     ("empty_content", self.content_empty)) if on]
        if self.text_chars < WEAK_CHARS and self.scripts >= WEAK_SCRIPTS:
            out.append("weak:scripts")
        if self.ajax and self.text_chars < WEAK_AJAX_CHARS:
            out.append("weak:ajax")
        return tuple(out)


def measure(html: str, *, content_css: str | None = None, text_chars: int | None = None) -> Measure:
    """The signs of one page's HTML (design §8.3). content_css: the platform profile's content area (None on a site
    without one: no empty-area sign); text_chars: links.extract()'s count when the caller has it (it adds the words of
    Google Sites embed-code blocks), otherwise counted here the same way (links.text_chars)."""
    tree = LexborHTMLParser(html)
    scripts = len(tree.css("script"))
    found = empty = False
    if content_css:
        areas = tree.css(content_css)
        found = bool(areas)
        empty = found and not any(_area_holds_content(node.html or "") for node in areas)
    if text_chars is None:
        text_chars = links.text_chars(tree)  # last: it takes script and layout out of the tree
    return Measure(text_chars=text_chars, scripts=scripts, loading=bool(LOADING.search(html)), spa=bool(SPA.search(html)),
                   ajax=bool(AJAX.search(html)), content_found=found, content_empty=empty)


def _area_holds_content(fragment: str) -> bool:
    """A content area holds something: words (not its scripts' or styles'), a picture or player, a background image,
    or a link to an attachment or a picture (design §8.3: 「正文区找得到、却没有字、也没有图片和附件」)."""
    sub = LexborHTMLParser(fragment)
    for tag in _CONTENT_DROP:
        for n in sub.css(tag):
            n.decompose()
    body = sub.body
    if body is None:
        return False
    if re.sub(r"\s+", "", body.text(separator=" ")):
        return True
    if body.css_first(_CONTENT_MEDIA) is not None:
        return True
    if any("url(" in (n.attributes.get("style") or "") for n in body.css("[style]")):
        return True
    return any(_file_like(n.attributes.get("href") or "") for n in body.css("a[href]"))


def _file_like(href: str) -> bool:
    try:
        sp = urlsplit(href.strip())
        host = (sp.hostname or "").lower()
    except ValueError:
        return False
    path = sp.path.lower()
    last = path.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1] if "." in last else ""
    return ext in _FILE_EXT or path in _FILE_PATHS or host in scope.GOOGLE_LINK_HOSTS


# ---------------------------------------------------------------- §8.3 what the crawl does with a page
CHANGEABLE_SOURCES = ("import", "crawl")  # a verdict the signs may send back to todo; jscheck's and a person's stay
HINT_FLAG = "js_hint"
_HAS_HINT = f"(flags IS NOT NULL AND instr(',' || flags || ',', ',{HINT_FLAG},') > 0)"
# a click this round cannot press (design §7.8, §8.3): the rule does not fit it (round2), or no rule and the tab shown
# on load; a tab pressed by its platform rule (must) and a tab with a plain link (click:plain_href) are not one
CLICK_HINT_SQL = ("SELECT 1 FROM page_res WHERE page_id=? AND how='click' "
                  "AND (need='round2' OR why='click:shown_on_load') LIMIT 1")


@dataclass(frozen=True)
class Observed:
    key: str  # url.page_type (observe() wrote it)
    new: bool  # the type first appeared on this page: todo, why=new, source crawl
    flags: tuple[str, ...]  # what the caller adds to url.flags: js_hint for a strong sign
    retyped: str | None  # why this page sent its type back to todo: hint, click, weak (None: unchanged)
    kept: tuple[str, ...]  # signs on a type jscheck or a person judged: its verdict stays, for the report


def click_hint(conn: sqlite3.Connection, page_id: int) -> bool:
    """The page has a click this round cannot press (its page_res rows, design §8.3)."""
    return conn.execute(CLICK_HINT_SQL, (page_id,)).fetchone() is not None


def observe(fr: Frontier, *, url_id: int, url: str, m: Measure, click: bool | None = None,
            site: str | None = None) -> Observed:
    """One 200 HTML page the crawl stored (design §8.3): writes url.page_type and the type's row (a new type: todo
    why=new, source crawl, this page its sample; a known one: its page count and signs, the sample when it has none
    this round), and sends an ok type back to todo when this page shows it needs a closer look. click: the page has a
    click this round cannot press; None reads it from the page's page_res rows (write them first). The same row seen
    again (a fetch again, rejudge) is not counted twice: its weak sign stays as first counted.

    The caller adds Observed.flags to url.flags; text_chars it writes from its own count (m.text_chars is the same)."""
    parts = type_parts(url, site)
    key = parts.key
    strong, weak = m.strong, m.weak
    with fr.lock, write_first(fr.conn, "pagetypes_observe"):  # it reads, then writes (11-07 05:45)
        conn = fr.conn
        prev = conn.execute(f"SELECT page_type, {_HAS_HINT} FROM url WHERE id=?", (url_id,)).fetchone()
        if prev is None:
            raise KeyError(f"url row {url_id} does not exist")
        again = prev[0] == key
        if prev[0] is not None and not again:  # counted under a key the rules no longer give: not there now
            conn.execute("UPDATE page_type SET n_pages=max(n_pages - 1, 0), n_strong=max(n_strong - ?, 0) "
                         "WHERE key=?", (int(bool(prev[1])), prev[0]))
        if click is None:
            click = click_hint(conn, url_id)
        row = conn.execute("SELECT * FROM page_type WHERE key=?", (key,)).fetchone()
        new = row is None
        retyped, kept = None, ()
        if new:
            conn.execute(
                "INSERT INTO page_type(key, site, host, path_tpl, params, verdict, why, sample_id, sample_url, "
                "n_pages, n_strong, n_weak, source) VALUES (?,?,?,?,?,'todo','new',?,?,1,?,?,'crawl')",
                (key, parts.site, parts.host, parts.path_tpl, json.dumps(list(parts.params)), url_id, url,
                 int(strong), int(weak)))
        else:
            n_pages = row["n_pages"] + (0 if again else 1)
            n_strong = max(0, row["n_strong"] + int(strong) - int(again and bool(prev[1])))
            n_weak = row["n_weak"] + (0 if again else int(weak))
            signs = _signs(row["verdict"], strong=strong, click=bool(click), n_pages=n_pages, n_weak=n_weak)
            verdict, why = row["verdict"], row["why"]
            if signs and row["source"] in CHANGEABLE_SOURCES:
                verdict, why, retyped = "todo", signs[0], signs[0]
            elif signs:
                kept = signs
            conn.execute(
                "UPDATE page_type SET n_pages=?, n_strong=?, n_weak=?, verdict=?, why=?, "
                "sample_id=coalesce(sample_id, ?), sample_url=coalesce(sample_url, ?) WHERE key=?",
                (n_pages, n_strong, n_weak, verdict, why, url_id, url, key))
        conn.execute("UPDATE url SET page_type=? WHERE id=?", (key, url_id))
    return Observed(key=key, new=new, flags=(HINT_FLAG,) if strong else (), retyped=retyped, kept=kept)


def _signs(verdict: str, *, strong: bool, click: bool, n_pages: int, n_weak: int) -> tuple[str, ...]:
    """Which of the §8.3 signs would send a type of this verdict back to todo, the strongest first."""
    out = []
    if strong and verdict in ("ok", "frame"):
        out.append("hint")
    if click and verdict == "ok":
        out.append("click")
    if verdict == "ok" and _weak_type(n_pages, n_weak):
        out.append("weak")
    return tuple(out)


def _weak_type(n_pages: int, n_weak: int) -> bool:
    return n_pages >= WEAK_MIN_PAGES and 10 * n_weak >= WEAK_SHARE_TENTHS * n_pages


def withdraw_files_only(fr: Frontier) -> list[str]:
    """Design §8.2, 工设系's uploads/*: a type imported as todo because its sample was a broken link
    (inconclusive:broken_sample), with no 200 HTML page of it this round, is withdrawn (note withdrawn:files_only: the
    folder holds files, which have no page type) rather than left waiting for a verdict. jscheck calls it."""
    out = []
    with fr.lock:
        for r in fr.conn.execute(
                "SELECT key, note FROM page_type WHERE verdict='todo' AND why='inconclusive:broken_sample'").fetchall():
            if fr.conn.execute("SELECT 1 FROM url WHERE page_type=? AND state='done' AND status=200 AND sniff='html' "
                               "LIMIT 1", (r["key"],)).fetchone() is not None:
                continue
            note = add_note(r["note"], "withdrawn:files_only")
            if note != r["note"]:
                fr.conn.execute("UPDATE page_type SET note=? WHERE key=?", (note, r["key"]))
            out.append(r["key"])
    return out


def add_note(note: str | None, *tokens: str) -> str | None:
    """page_type.note as comma-separated tokens (js_redirect, click_profiled …), each once, in the order added."""
    have = [t for t in (note or "").split(",") if t]
    for t in tokens:
        if t and t not in have:
            have.append(t)
    return ",".join(have) or None
