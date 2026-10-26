"""Links and parts out of an archived HTML page (round-1 design §7.1, §7.2, §7.8, §8.3, §9). Nothing here sends
anything, and no script is run (jsread reads them as text).

- links: <a>/<area> hrefs, frames, embeds and objects, meta refresh, and URLs written into event handlers and
  javascript: links (window.open, location.href … with one literal address: one built from pieces is no link, it is
  read as what it builds), the addresses only data-link, data-href or data-url write (an outside script follows them
  on a click), an attachment written as a CSS background ([background]), and where an inline script sends a short
  page by itself ([js_redirect]); as written, with their text: the link table records each one.
- redirects: the meta refresh of every page and a short page's own script redirect (design §8.3): js_redirect is the
  one the crawler treats as a redirect, on a page of fewer than JS_REDIRECT_MAX_CHARS characters.
- frames: iframes (a lazy loader's data-src or data-lazy-src when src is blank), frames, embeds and objects again, as
  parts of the page (round1: a school frame is a resource the crawler looks into; design §4.4), with their size and
  whether the page hides them (round1: an off-campus hidden frame is a tracker's, a link only).
- images: every way a page writes an image (src, the lazy loaders' attributes, the largest srcset entry,
  <picture><source>, background images, an <a> straight to an image file) with its alt text, size and zone;
  image_need() says whether the round downloads it and, when not, why (each one has its reason, design §7.2).
- buttons: ASP.NET postbacks (__doPostBack, WebForm_PostBackOptions) in hrefs, event handlers and inline scripts, with
  target, argument, text, form and trigger: recorded, never sent here (postback.py presses the allowed ones).
- clicks: elements whose click handler (an attribute, Vue's or Alpine's @click, or a handler an inline script binds)
  makes a network call, directly or through the page's own functions three levels down (design §7.8): the function
  and its arguments, the request it builds, the element's values, whether it is the tab shown on load or holds a
  plain link elsewhere. click_need() says what each needs, given the platform file's decision.
- scripts: the <script src> addresses (round1.place(how='script') fetches a school site's .js as the page's
  resource, read for its clicks, never content: design §4.4).
- pagers: GET links to the next pages of this same list, which add no depth (design §9).
- videos: each YouTube video the page links or embeds (watch, youtu.be, embed, the old /v/ Flash embed, shorts, live),
  shows a thumbnail of (i.ytimg.com, img.youtube.com) or names in a script or a handler, and each playlist and live
  stream it embeds or links (keyed list:<id>, live:<channel>), with the title the school's page gives it (an embed's
  title, a link's text, a thumbnail's alt): never fetched (scope.SOCIAL), noted for the sorting stage (owner 10-26,
  scope record 五·15: 「不用api 先记下youtube链接和标题」).
- unhandled: what a crawler of plain GETs cannot follow and nobody has decided about yet: javascript: links and click
  handlers by the function they call when no inline script defines it (an external .js: obj.method() too, and a
  handler that hands over to one), navigation to an address a handler builds, a network call bound to nothing on the
  page, postbacks fired by onchange, setTimeout or a script, <select> menus (a jump menu with how it builds its
  address, its options and whether the page fires it itself: select:auto), a checkbox that rebuilds the address, GET
  forms. page_res lists each (how='unhandled', need and why by unhandled_need() from the platform file's decision), so
  none is dropped unseen (design §7.8); the round presses no menu and sends no form.
- text_chars: the page's words, as the 10-25 scan counted them (script, style and the page's own header, nav,
  footer and aside left out; a Google Sites embed-code block counted in).
- A Google Sites embed-code block (div[data-code]) holds HTML in an attribute: it is read as part of the page (its
  links, images, frames, its words), and its loader's data-url is never a link (design §4.4).

Zones (design §7.2): body (inside the platform profile's content area, or a region every school template uses for
content: #HonorRollArea), chrome (nav, aside, and the page's own header and footer; on a profiled site, everything
outside its content area), unknown (anything else; it counts as body: 「分不清的一律当正文」). A header or footer inside
an article, main or section is that part's own (HTML-AAM: banner and contentinfo only outside sectioning content), not
the page's: WordPress puts a post's title and poster in <article><header>.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from nknu.archive import jsread, scope
from nknu.archive.jsread import NetCall

_REFRESH = re.compile(r"(?i)url\s*=\s*['\"]?([^'\";]+)")
_Q = r"""\\?['"]"""  # a quote, escaped or not: onchange="setTimeout('__doPostBack(\'ddl\',\'\')', 0)"
_POSTBACK = re.compile(rf"""__doPostBack\(\s*{_Q}([^'"\\]*){_Q}\s*,\s*{_Q}([^'"\\]*){_Q}""")
_POSTBACK_OPTIONS = re.compile(rf"""WebForm_PostBackOptions\(\s*{_Q}([^'"\\]*){_Q}\s*,\s*{_Q}([^'"\\]*){_Q}""")
_JS_CALL = re.compile(r"^\s*(?:javascript:)?\s*(?:return\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")
_BG_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""")
_PX = re.compile(r"(?i)^\s*(\d+)\s*(?:px)?\s*$")

CHROME_TAGS = frozenset({"header", "nav", "footer", "aside"})
PAGE_EDGE_TAGS = frozenset({"header", "footer"})  # layout only as the page's own (not inside SECTION_TAGS)
SECTION_TAGS = frozenset({"article", "main", "section"})
ALWAYS_BODY = ("#HonorRollArea",)  # EditPlus's honour posters (record 第八节第 6 点): content wherever they sit
_ZONE_RANK = {"chrome": 0, "unknown": 1, "body": 2}
_LAZY = ("data-src", "data-original", "data-lazy-src")
_BLANK_SRC = ("about:", "javascript:")  # a lazy loader's placeholder: the real address is in one of _LAZY
_HIDDEN_STYLE = re.compile(r"(?i)(?:^|[;\s])(?:display\s*:\s*none|visibility\s*:\s*hidden)")
_DATA_SRC_MAX = 60  # a data: image is listed by its first characters only
# design §8.3: a page of fewer words than this that sends the browser on is a redirect; a script redirect is read only
# on such a page (a long page's scripts write redirects for its menus)
JS_REDIRECT_MAX_CHARS = 150
_TEXT_DROP = ("script", "style", "noscript", "header", "nav", "footer", "aside")  # js_pages_scan.py's measure
_ATTR_LINKS = ("data-link", "data-href", "data-url")  # an outside script follows these on a click (design §7.8)
_MEDIA_TAGS = frozenset({"img", "iframe", "frame", "script", "link", "source", "video", "audio", "meta", "embed",
                         "object", "track", "picture"})
_ATARI = "atari-embeds.googleusercontent.com"  # a Google Sites embed's loader: never a link (design §7.8)
URLISH = re.compile(r"(?i)(?:https?://|\.(?:s?html?|php|aspx?|jsp|pdf|docx?|xlsx?|pptx?|odt)\b|^/|^\.{1,2}/|\?\w+=)")
_FRAMEWORK_ATTR = re.compile(r"(?:@|v-on:|x-on:)(?:click|change)(?:\.[\w.-]+)?")
_JS_TYPE = re.compile(r"(?i)javascript|ecmascript|module|^text/babel$")
_DATA_CODE_DEPTH = 2  # embed-code blocks inside embed-code blocks, at most this deep

# photo albums (record 三·7: not downloaded); c.nknu/aca's Album pages are its news list and items (record 第八节第 6 点)
ALBUM = re.compile(r"(?i)(album|gallery|photo|movieview|case_detail|活動花絮|相簿)")
ALBUM_NOT = frozenset({("c.nknu.edu.tw", "/aca/album.aspx"), ("c.nknu.edu.tw", "/aca/album_images.aspx")})
DECOR_FILE = re.compile(r"(?i)(logo|icon|banner|arrow|btn|button|bg[_.-]|spacer|blank|bullet|qrcode|loading|sprite)")
# uploads are content, never a template, however often they repeat: editors' upload folders, Joomla's dated images
_NEVER_TEMPLATE = re.compile(r"(?i)/(?:uploadfile|userfiles)/|/images/\d{4}/")
MIN_SIZE = 80  # a width or height under this is an icon or a tracking pixel
# the repeat rule (design §7.2 版型图): on 5 pages and 30% of a site's stored pages, never in a body. Checked by the
# offline rehearsal on 10-26 (archive-rehearsal-2, 7,437 pictures round-1 pages need, counted at the end of the run):
# 64 are layout by the rule, the same 64 the run skipped. The page count hardly matters (2 → 64, 10 → 61, 20 → 59);
# the share does: 0.5 → 54 (it loses elec's zh and en layout, each on 48–50% of a bilingual site's pages: logo, icons,
# header background), 0.2 → 76 (+12: clcts's section icons on 21%, layout too), 0.1 → 97 (adds the 10–20% band, where
# english's and ee's banners under /upload/ and Google Sites pictures are not plainly layout). The 64 are logos, menu
# buttons, icons and header pictures (where they sit: div#top, WUC_Flash_Header, a.logo); uploads (_NEVER_TEMPLATE)
# never count. Kept 【Claude 的执行办法, 10-26】: a skipped picture is never fetched, so a lower share risks content for
# a few requests saved (each picture is fetched once however many pages use it)
REPEAT_PAGES, REPEAT_SHARE = 5, 0.3
IMAGE_WHY = ("captcha", "album", "data", "tiny", "decor_name", "chrome", "decor:repeat")

# GET paging (design §9): one parameter of these names changes, or WordPress's /page/N/; the link reads as a number
# or "next". c.nknu's pn is a page's number, not paging.
PAGER_PARAMS = frozenset({"page", "pagenum", "pageno", "p", "pg", "paged", "start", "offset"})
NEXT_WORDS = ("下一頁", "下頁", "後頁", "next", "›", "»")
_WP_PAGE = re.compile(r"/page/\d+/?$")


@dataclass(frozen=True)
class Image:
    src: str  # as written (a data: image cut short)
    how: str  # src, data-src, data-original, data-lazy-src, srcset, picture, bg, data-bg, background, link
    zone: str
    alt: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class Frame:
    src: str
    tag: str  # iframe, frame, embed, object
    zone: str
    title: str = ""
    width: int | None = None
    height: int | None = None
    hidden: bool = False  # width or height 0, display:none, visibility:hidden or hidden, and no script sizes it


@dataclass(frozen=True)
class Postback:
    target: str
    argument: str
    text: str
    form_id: str  # the id (or name) of the <form> around it; "" outside any
    trigger: str  # href, an event attribute (onclick, onchange …), or script
    zone: str


@dataclass(frozen=True)
class Unhandled:
    # js_link (a function no inline script defines), js_nav (an address a handler builds), js_bind (a network call
    # bound to nothing on the page), js_postback, select, input (a checkbox that rebuilds the address), get_form
    kind: str
    name: str  # the function, the address built, the postback target, the select's name, the form's action and fields
    detail: str
    text: str
    form_id: str
    zone: str
    n: int = 1  # how many times the page writes it
    args: str = ""  # a function's arguments as written
    cls: str = ""  # the element's class
    options: tuple[tuple[str, str], ...] = ()  # a menu's options: (value, text)
    template: str = ""  # the address a jump menu or a checkbox builds (jsread.summarize)
    auto: bool = False  # the page's own script fires the menu when it loads (select:auto, design §7.8)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Click:
    """An element whose click (or change) handler makes a network call (design §7.8): one row of page_res
    (how='click', role='click', res_key=key)."""

    key: str  # click:<function>(<arguments>), or click:<selector>[<i>] for the elements a script binds
    group: str  # one set of tabs or buttons: the function, or the binding's selector
    fn: str
    args: str
    params: tuple[str, ...]  # the function's parameters, which a platform rule maps the arguments to
    trigger: str  # href, onclick, onchange, @click …, or bind:<event>
    text: str
    cls: str
    zone: str
    calls: tuple[NetCall, ...]
    values: tuple[str, ...] = ()  # hidden inputs' values and data-* attributes (ee's file numbers)
    shown_on_load: bool = False  # the one tab of its group the page marks as shown (click:shown_on_load)
    plain_href: str | None = None  # a plain address elsewhere the element is, holds or sits in (click:plain_href)
    n: int = 1


@dataclass(frozen=True)
class Video:
    """A YouTube video, playlist or live stream on the page (scope record 五·15): never fetched; the frontier's video
    table keeps it."""

    video_id: str  # a video's 11-character id, list:<playlist id>, or live:<channel id> (youtube_id)
    url: str  # the address as the page wrote it, canonical (wrappers taken off)
    title: str  # the school's page's own words for it: an embed's title (or aria-label), a link's text (or title, or
    # its picture's alt), a thumbnail's alt; "" when the page gives none
    how: str  # embed, link or image (a thumbnail only); the first of these the page does
    zone: str


# YouTube's hosts (scope.SOCIAL holds them: never fetched), its thumbnails' hosts, and a video's id: 11 of these
# characters. /embed/ and /live/ also take words that are no video (second look F7): /embed/videoseries?list= is a
# playlist, /embed/live_stream?channel= a channel's live stream, each kept under its own key so that two playlists on
# one page stay two, with their own titles
_YT_HOSTS = re.compile(r"(?i)(?:^|\.)(?:youtube\.com|youtube-nocookie\.com)$")
_YT_THUMB_HOSTS = re.compile(r"(?i)(?:^|\.)ytimg\.com$|^img\.youtube\.com$")
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_KEY = re.compile(r"[A-Za-z0-9_-]{2,64}")  # a playlist's or a channel's id
_YT_PATHS = ("embed", "v", "shorts", "live")  # /embed/ID, the old Flash /v/ID, /shorts/ID, /live/ID
_YT_THUMB_PATHS = ("vi", "vi_webp")  # i.ytimg.com/vi/ID/hqdefault.jpg
_YT_NOT_IDS = frozenset({"videoseries", "live_stream"})


def youtube_id(url: str) -> str | None:
    """The video a YouTube address shows (youtube.com/watch?v=, youtu.be/ID, /embed/ID, /v/ID, /shorts/ID, /live/ID, a
    thumbnail i.ytimg.com/vi/ID/…), list:<id> for a playlist (/playlist?list=, /embed/videoseries?list=,
    /embed?listType=playlist&list=), live:<channel> for a channel's live stream (/embed/live_stream?channel=), or None:
    a channel's page, a search, another site, a broken id."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    segs = [p for p in parts.path.split("/") if p]
    query = parse_qsl(parts.query)

    def param(name: str) -> str | None:
        return next((v for k, v in query if k == name), None)

    def key(prefix: str, value: str | None) -> str | None:
        return f"{prefix}:{value}" if value and _YT_KEY.fullmatch(value) else None

    vid = None
    if host == "youtu.be" and segs:
        vid = segs[0]
    elif _YT_THUMB_HOSTS.search(host):
        if len(segs) >= 2 and segs[0] in _YT_THUMB_PATHS:
            vid = segs[1]
    elif _YT_HOSTS.search(host):
        if segs == ["watch"]:
            vid = param("v")
        elif segs == ["playlist"] or (segs in (["embed"], ["embed", "videoseries"]) and param("list")):
            return key("list", param("list"))
        elif segs == ["embed", "live_stream"]:
            return key("live", param("channel"))
        elif len(segs) >= 2 and segs[0] in _YT_PATHS:
            vid = segs[1].split("&", 1)[0]  # /v/ID&hl=zh_TW: the Flash embed wrote its parameters into the path
    return vid if vid and vid not in _YT_NOT_IDS and _YT_ID.fullmatch(vid) else None


@dataclass(frozen=True)
class Redirect:
    """Where the page sends the browser by itself (design §8.3)."""

    href: str  # as written (a script's escapes undone)
    how: str  # refresh, script
    to_self: bool = False  # a refresh of the page itself (a board that reloads): no redirect


@dataclass
class PageLinks:
    base: str
    url: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (href as written, link text)
    link_zone: dict[str, str] = field(default_factory=dict)  # href -> its best zone (body > unknown > chrome)
    frames: list[Frame] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    buttons: list[Postback] = field(default_factory=list)
    pagers: list[str] = field(default_factory=list)  # canonical URLs
    unhandled: list[Unhandled] = field(default_factory=list)
    clicks: list[Click] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)  # YouTube videos linked or embedded, one per video
    scripts: list[str] = field(default_factory=list)  # <script src> as written
    redirects: list[Redirect] = field(default_factory=list)
    text_chars: int = 0
    login_form: bool = False
    title: str = ""
    profiled: bool = False  # zones come from a platform profile's content area

    @property
    def postbacks(self) -> list[str]:
        """"target|argument" per button, as the 10-24 crawler counts and records them."""
        return [f"{b.target}|{b.argument}" for b in self.buttons]

    @property
    def js_redirect(self) -> Redirect | None:
        """The redirect the crawler treats as one (flag js_redirect, final_url; design §8.3): the first that leaves
        the page, on a page of fewer than JS_REDIRECT_MAX_CHARS characters. A longer page is judged by its own
        content, its refresh's target queued as any link."""
        if self.text_chars >= JS_REDIRECT_MAX_CHARS:
            return None
        return next((r for r in self.redirects if not r.to_self), None)


def _base(url: str, href: str | None) -> str:
    """The page's <base href>, or the page itself when the base is written wrong (an unusable base is not a crash)."""
    if not href:
        return url
    try:
        joined = urljoin(url, href)
        urlsplit(joined).port  # noqa: B018 - raises for a port that is not a number
        return joined
    except ValueError:
        return url


class _Zones:
    """The zone of a node: the nearest body root above it wins, then a chrome tag (nav or aside anywhere, a header or
    footer only when no article, main or section holds it)."""

    def __init__(self, tree: LexborHTMLParser, content_css: str | None) -> None:
        self.profiled = content_css is not None
        roots = [*ALWAYS_BODY, *([content_css] if content_css else [])]
        self.body = {n.mem_id for css in roots for n in tree.css(css)}

    def of(self, node: LexborNode | None) -> str:
        chrome = edge = False  # edge: inside a header or footer no sectioning element above has claimed yet
        while node is not None:
            if node.mem_id in self.body:
                return "body"
            if node.tag in PAGE_EDGE_TAGS:
                edge = True
            elif node.tag in SECTION_TAGS:
                edge = False  # the header or footer below is this article's (or main's, section's) own
            elif node.tag in CHROME_TAGS:
                chrome = True
            node = node.parent
        return "chrome" if chrome or edge or self.profiled else "unknown"


def _best(a: str, b: str) -> str:
    return a if _ZONE_RANK[a] >= _ZONE_RANK[b] else b


def _largest(a: int | None, b: int | None) -> int | None:
    """One side of an image written twice, as least likely to make it tiny: unwritten once is unwritten."""
    return None if a is None or b is None else max(a, b)


def _merge(old: Image, new: Image) -> Image:
    """An image the page writes twice (a sidebar thumbnail, then the picture in the body): the best zone, each size
    the least likely to be tiny, the first alt text written. How it was first written stays."""
    return replace(old, zone=_best(old.zone, new.zone), alt=old.alt or new.alt,
                   width=_largest(old.width, new.width), height=_largest(old.height, new.height))


def _form_id(node: LexborNode | None) -> str:
    while node is not None:
        if node.tag == "form":
            return node.attributes.get("id") or node.attributes.get("name") or ""
        node = node.parent
    return ""


def _label(node: LexborNode) -> str:
    """What a reader sees on a control: its text, else its value, title or alt (a menu's options are not its label)."""
    a = node.attributes
    text = "" if node.tag in ("select", "script") else node.text(strip=True)
    return (text or a.get("value") or a.get("title") or a.get("alt") or "").strip()[:100]


def _handlers(node: LexborNode) -> list[tuple[str, str]]:
    return [(k, v) for k, v in node.attributes.items() if k.startswith("on") and v]


def _calls(code: str) -> tuple[list[str], list[tuple[str, str]]]:
    """The literal URLs and the postbacks (target, argument) a piece of script names."""
    urls = [jsread.unescape(u) for _q, u in jsread.URL_CALL.findall(code)]
    return urls, _POSTBACK.findall(code) + _POSTBACK_OPTIONS.findall(code)


class _Collect:
    """One page's parts as they are found: images and unhandled writings once each, the best zone winning."""

    def __init__(self, out: PageLinks, zones: _Zones) -> None:
        self.out, self.zones = out, zones
        self.images: dict[str, Image] = {}
        self.unhandled: dict[str, Unhandled] = {}
        self.clicks: dict[str, Click] = {}
        self.marked: dict[str, set[str]] = {}  # group -> the keys of its elements marked as shown on load
        self.jumps: dict[int, tuple[str, str]] = {}  # a menu's node -> (the address it builds, the binding's selector)
        self.fired: set[int] = set()  # the menus the page's own script fires when it loads
        self.videos: dict[str, Video] = {}

    def link(self, href: str, text: str, zone: str) -> None:
        self.out.links.append((href, text))
        self.out.link_zone[href] = _best(self.out.link_zone.get(href, zone), zone)
        # a video however the page names it (a javascript: link, a handler, an attribute, a refresh: second look F7);
        # a link's own words are its title, never the [onclick] this module writes for one it has none for
        self.video(href, "" if text.startswith("[") and text.endswith("]") else text, "link", zone)

    def image(self, src: str, how: str, node: LexborNode, alt: str = "") -> None:
        src = (src or "").strip()
        if not src:
            return
        if src.startswith("data:"):
            src = src[:_DATA_SRC_MAX]
        a = node.attributes
        new = Image(src, how, self.zones.of(node), (alt or a.get("alt") or a.get("title") or "").strip()[:100],
                    _size(node, "width"), _size(node, "height"))
        old = self.images.get(src)
        self.images[src] = new if old is None else _merge(old, new)
        self.video(src, new.alt, "image", new.zone)  # a YouTube thumbnail stands for its video (second look F7)

    def video(self, href: str, title: str, how: str, zone: str) -> None:
        """A YouTube video (or playlist, or live stream), once per page: embed when the page embeds it anywhere, else
        link when it links it, else image (a thumbnail only); the title an embed gives it before the one a link gives
        it before a thumbnail's alt, else the first one written; the best zone."""
        low = (href or "").lower()
        if "youtu" not in low and "ytimg" not in low:  # most links: no need to make the address canonical
            return
        url = scope.canonical(href, self.out.base)
        vid = youtube_id(url) if url else None
        if vid is None:
            return
        new = Video(vid, url, title.strip()[:200], how, zone)
        old = self.videos.get(vid)
        if old is not None:
            rank = {("embed", True): 3, ("link", True): 2, ("image", True): 1}
            better = rank.get((new.how, bool(new.title)), 0) > rank.get((old.how, bool(old.title)), 0)
            hows = (old.how, how)
            new = replace(old, how=next(h for h in ("embed", "link", "image") if h in hows),
                          zone=_best(old.zone, zone), title=new.title if better else old.title)
        self.videos[vid] = new

    def button(self, target: str, argument: str, node: LexborNode, trigger: str) -> None:
        self.out.buttons.append(Postback(target, argument, _label(node), _form_id(node), trigger,
                                         self.zones.of(node)))

    def unhandle(self, kind: str, name: str, node: LexborNode | None, detail: str = "", text: str = "",
                 **more) -> None:
        zone, key = self.zones.of(node), f"{kind}:{name}"
        old = self.unhandled.get(key)
        if old is None:
            self.unhandled[key] = Unhandled(kind, name, detail or name, text, _form_id(node), zone, **more)
        elif _ZONE_RANK[zone] > _ZONE_RANK[old.zone]:  # a sample from the body says more than one from a menu
            self.unhandled[key] = replace(old, zone=zone, text=text or old.text, n=old.n + 1,
                                          auto=old.auto or more.get("auto", False))
        else:
            self.unhandled[key] = replace(old, n=old.n + 1, auto=old.auto or more.get("auto", False))

    def click(self, key: str, group: str, node: LexborNode | None, **fields) -> None:
        a = node.attributes if node is not None else {}
        new = Click(key=key, group=group, text=_label(node) if node is not None else "", cls=(a.get("class") or "")[:100],
                    zone=self.zones.of(node), **fields)
        if node is not None and jsread.shown_on_load(node):
            self.marked.setdefault(group, set()).add(key)
        old = self.clicks.get(key)
        if old is None:
            self.clicks[key] = new
        else:  # the same tab twice (two menus): one resource, a plain link on either is enough
            self.clicks[key] = replace(old, n=old.n + 1, plain_href=old.plain_href or new.plain_href,
                                       zone=_best(old.zone, new.zone))

    def finish_clicks(self) -> list[Click]:
        """The tab shown on load, where its group tells it (design §7.8): exactly one of the group's elements marked;
        a group none or several of whose elements are marked has none (宁可多报)."""
        out = []
        for c in self.clicks.values():
            marked = self.marked.get(c.group, set())
            out.append(replace(c, shown_on_load=len(marked) == 1 and c.key in marked))
        return out


def extract(html: str, url: str, content_css: str | None = None, _depth: int = 0) -> PageLinks:
    """content_css: the platform profile's content area (design §7.4); None on a site without a profile."""
    tree = LexborHTMLParser(html)
    base_node = tree.css_first("base[href]")
    zones = _Zones(tree, content_css)
    out = PageLinks(base=_base(url, base_node.attributes.get("href") if base_node else None), url=url,
                    profiled=zones.profiled)
    title = tree.css_first("title")
    out.title = title.text(strip=True)[:200] if title else ""
    out.login_form = tree.css_first("input[type=password]") is not None
    got = _Collect(out, zones)
    page = scope.canonical(url) or url
    inline = [node.text() or "" for node in tree.css("script") if not node.attributes.get("src")
              and (not (typ := (node.attributes.get("type") or "").strip()) or _JS_TYPE.search(typ))]
    sc = jsread.Script("\n;\n".join(inline))
    covered = _guarded(_bound, set(), tree, got, sc, url)
    for node in tree.css("a[href], area[href]"):
        _anchor(node, got, page)
    for node in tree.css("iframe, frame, embed, object"):
        _frame(node, got)
    for node in tree.css("meta[http-equiv]"):
        if (node.attributes.get("http-equiv") or "").lower() == "refresh":
            m = _REFRESH.search(node.attributes.get("content") or "")
            if m:
                href = m.group(1).strip()
                got.link(href, "[refresh]", zones.of(node))
                out.redirects.append(Redirect(href, "refresh", _same(href, out.base, page)))
    for node in tree.css("*"):
        for attr, code in _handlers(node):
            _handler(node, attr, code, got)
    for node in _click_nodes(tree, html):
        _guarded(_click_node, None, node, got, sc, url, covered)
    for node in tree.css("script"):
        src = (node.attributes.get("src") or "").strip()
        if src:
            if src not in out.scripts:
                out.scripts.append(src)
            continue
        for target, arg in _calls(node.text() or "")[1]:
            got.button(target, arg, node, "script")
            got.unhandle("js_postback", target, node)
    _attr_links(tree, got)
    _images(tree, got)
    _selects(tree, got)
    _get_forms(tree, got)
    extra = _data_code(tree, got, url, _depth)
    out.images = list(got.images.values())
    out.videos = list(got.videos.values())
    out.clicks = got.finish_clicks()
    out.unhandled = list(got.unhandled.values())
    out.text_chars = text_chars(tree) + extra  # last: it takes the page's script and layout out of the tree
    if out.text_chars < JS_REDIRECT_MAX_CHARS:
        for code in inline:
            for href in jsread.script_redirects(code):
                got.link(href, "[js_redirect]", "unknown")
                out.redirects.append(Redirect(href, "script", _same(href, out.base, page)))
    return out


def _guarded(read, failed, *args):
    """A script this module cannot read is never a crash of its page, nor dropped unseen: it is a js_unread writing
    (要人看 until someone decides, like any other unhandled one)."""
    try:
        return read(*args)
    except Exception as exc:  # noqa: BLE001 - one script written oddly never fails the page it is on
        got = next(a for a in args if isinstance(a, _Collect))
        got.unhandle("js_unread", read.__name__.strip("_"), None, detail=f"{type(exc).__name__}: {exc}"[:200])
        return failed


def _same(href: str, base: str, page: str) -> bool:
    return scope.canonical(href, base) == page


def text_chars(tree: LexborHTMLParser) -> int:
    """The page's words as the 10-25 JavaScript scan counted them (js_pages_scan.py): the characters of the body that
    are not white space, script, style, noscript or the page's header, nav, footer and aside. It takes those out of
    the tree."""
    for tag in _TEXT_DROP:
        for n in tree.css(tag):
            n.decompose()
    body = tree.body
    return len(re.sub(r"\s+", "", body.text(separator=" "))) if body is not None else 0


def _anchor(node: LexborNode, got: _Collect, page: str) -> None:
    href = (node.attributes.get("href") or "").strip()
    text = node.text(strip=True)[:100] if node.tag == "a" else (node.attributes.get("alt") or "")[:100]
    zone = got.zones.of(node)
    if href.lower().startswith("javascript:"):
        urls, posts = _calls(href)
        for target, arg in posts:
            got.button(target, arg, node, "href")
        for u in urls:
            got.link(u.strip(), text, zone)
        return
    got.link(href, text, zone)
    if not href or href.startswith("#"):
        return
    target = scope.canonical(href, got.out.base)
    if target is None:
        return
    if youtube_id(target):
        picture = node.css_first("img[alt]")
        got.video(href, text or node.attributes.get("title") or (picture.attributes.get("alt") if picture else "")
                  or "", "link", zone)
    if scope.kind_of(target) == "image":
        got.image(href, "link", node, alt=text)
    if node.tag == "a" and is_pager(page, target, text) and target not in got.out.pagers:
        got.out.pagers.append(target)


# ---------------------------------------------------------------- clicks (design §7.8)


def _click_nodes(tree: LexborHTMLParser, html: str) -> list[LexborNode]:
    """Every element a click or a change acts on: an onclick or onchange, a javascript: or empty link, Vue's and
    Alpine's @click / v-on:click / x-on:click; each once (lexbor returns a node once per selector it matches)."""
    nodes = list(tree.css("[onclick], [onchange], a[href], area[href]"))
    if re.search(r"(?:@|v-on:|x-on:)(?:click|change)\b", html):
        nodes += [n for n in tree.css("*") if any(_FRAMEWORK_ATTR.fullmatch(k) for k in n.attributes)]
    seen, out = set(), []
    for n in nodes:
        if n.mem_id not in seen:
            seen.add(n.mem_id)
            out.append(n)
    return out


def _codes(node: LexborNode) -> list[tuple[str, str]]:
    a = node.attributes
    out = []
    href = (a.get("href") or "").strip()
    if node.tag in ("a", "area") and href.lower().startswith("javascript:"):
        out.append(("href", unquote(href[11:])))
    for ev in ("onclick", "onchange"):
        if a.get(ev):
            out.append((ev, a[ev]))
    out += [(k, v) for k, v in a.items() if v and _FRAMEWORK_ATTR.fullmatch(k)]
    return out


def _click_node(node: LexborNode, got: _Collect, sc: jsread.Script, url: str, covered: set[int]) -> None:
    """What clicking this element (or changing it) does, by its own attributes: a press (postback, _handler has it),
    a literal address (a link), a network call (a Click), a function no inline script defines (js_link), an address
    built (js_nav, or a jump menu's template). A link to nowhere a GET can follow whose handlers say nothing of the
    kind is recorded by the function it names (design §7.8), unless an inline script binds it (_bound has it)."""
    codes = _codes(node)
    href = (node.attributes.get("href") or "").strip()
    dead = node.tag in ("a", "area") and not jsread.real_href(href)
    if not codes:
        return
    plain = jsread.plain_href(node, url)
    read = handled = False
    for trig, code in codes:
        urls, posts = _calls(code)
        handled |= bool(urls or posts)
        if posts or any(p in code for p in jsread.POSTBACK_CALLS):
            continue
        read |= _attr_code(node, trig, code, got, sc, plain)
    if dead and not (read or handled) and node.mem_id not in covered:
        _dead_link(node, href if href.lower().startswith("javascript:") else None, got)


def _attr_code(node: LexborNode, trig: str, code: str, got: _Collect, sc: jsread.Script, plain: str | None) -> bool:
    """One handler written in an attribute; whether it was read to an end (something recorded, or the page's own
    function read and found to load nothing)."""
    read = False
    direct = jsread.net_calls(code, sc, depth=0)
    if direct:
        got.click(f"click:({trig}){code.strip()[:80]}", f"({trig})", node, fn=f"({trig})", args="", params=(),
                  trigger=trig, calls=tuple(direct), plain_href=plain)
        read = True
    navs = jsread.nav_computed(code, sc, depth=0)
    if navs:
        read |= _nav_built(node, navs[0], got, plain, trig)
    for m in jsread.CALL.finditer(code):
        name = re.sub(r"\s+", "", m.group(1))
        short = name.split(".")[-1]
        if short in jsread.KEYWORDS or name in jsread.NAV_CALLS or name.startswith(jsread.POSTBACK_CALLS):
            continue
        if name in jsread.NOT_CONTENT or short in jsread.NOT_CONTENT:
            # a browser's own call: never the page's function (popUp.focus()), but a javascript: link that is only one
            # (javascript:window.print(), history.back()) is recorded by it, for the global decision (design §7.8)
            if trig == "href" and ("." not in name.removeprefix("window.")
                                   or name.startswith(("history.", "window.history."))):
                got.unhandle("js_link", name, node, text=_text(node))
                read = True
            continue
        if name.startswith(jsread.DOM_PREFIXES) or jsread.NEW_BEFORE.search(code[max(0, m.start() - 8):m.start()]):
            continue
        p = m.end() - 1
        args = code[p + 1:jsread.match_close(code, p)].strip()
        fname = name.removeprefix("window.")
        key = sc.defined(name)
        if key is not None:
            params, body, _start = sc.defs[key]
            # the attribute is the handler (level 0); the function it calls is level 1 of the three read (§7.8)
            net = jsread.net_calls(body, sc, depth=jsread.LEVELS - 1, seen=frozenset({key}))
            read = True
            if net:
                got.click(f"click:{fname}({args[:80]})", fname, node, fn=fname, args=args, params=tuple(params),
                          trigger=trig, calls=tuple(net), plain_href=plain)
                continue
            built = jsread.nav_computed(body, sc, seen=frozenset({key}))
            if built:
                _nav_built(node, built[0], got, plain, trig, via=fname)
                continue
            if plain is None:
                for ext in jsread.undefined_calls(body, sc, params, seen=frozenset({key})):
                    got.unhandle("js_link", ext, node, detail=f"via {fname}", text=_text(node),
                                 cls=(node.attributes.get("class") or "")[:100])
                _deep(body, sc, fname, node, got, frozenset({key}))
            continue  # the page's own function, read: it loads nothing
        if plain is not None:
            read = True
            continue  # its plain link is followed; what an outside script adds on the way is not a gap of the page
        got.unhandle("js_link", fname, node, text=_text(node), args=args[:100],
                     cls=(node.attributes.get("class") or "")[:100])
        read = True
    return read


def _nav_built(node: LexborNode, template: str, got: _Collect, plain: str | None, trig: str, via: str = "") -> bool:
    """A handler that sends the browser to an address it builds: a menu's or a checkbox's (a jump menu), else a
    js_nav writing, unless the element holds a plain link already."""
    if node.tag in ("select", "input") and trig in ("onchange", "onclick"):
        got.jumps.setdefault(node.mem_id, (template, ""))
        return True
    if plain is not None:
        return True
    got.unhandle("js_nav", template, node, detail=f"via {via}" if via else "", text=_text(node))
    return True


def _text(node: LexborNode) -> str:
    return (_label(node) or (node.attributes.get("title") or "").strip())[:100]


def _dead_link(node: LexborNode, js: str | None, got: _Collect) -> None:
    """A link to nowhere a GET can follow (a javascript: link, or an empty or fragment href with handlers) whose
    handlers name no function this module could read: recorded by what it names (javascript:void(0) as void, a bare
    javascript: as nothing), so that it is never dropped unseen (design §7.8)."""
    handlers = [code for _attr, code in _handlers(node)]
    if js is None and not handlers:
        return
    names = [_js_name(code) for code in ([js] if js else []) + handlers]
    name = next((n for n in names if n not in ("", "void")), "void" if "void" in names else "")
    got.unhandle("js_link", name, node, text=_text(node), cls=(node.attributes.get("class") or "")[:100])


def _values(node: LexborNode) -> tuple[str, ...]:
    """What tells one bound button from another: its hidden inputs' values and its data-* attributes."""
    vals: list[str] = []
    for inp in node.css("input"):
        v = inp.attributes.get("value")
        if (inp.attributes.get("type") or "").lower() == "hidden" and v and v not in vals:
            vals.append(v)
    for k, v in node.attributes.items():
        if k.startswith("data-") and v and f"{k}={v}" not in vals:
            vals.append(f"{k}={v}")
    return tuple(vals[:40])


def _bound(tree: LexborHTMLParser, got: _Collect, sc: jsread.Script, url: str) -> set[int]:
    """The handlers the page's inline scripts bind (design §7.8): a network call is a Click per element bound (one
    for a document's delegated handler that names no element; a js_bind writing when it binds to nothing on the
    page), an address built is a jump menu's or a checkbox's template (or a js_nav writing), a handler that hands
    over to a function no inline script defines (or is one) is that function's js_link. The menus the scripts fire
    themselves on load are marked (select:auto). Returns the elements any binding covers."""
    bl, auto = jsread.bindings(sc)
    covered: set[int] = set()
    for fire in auto:
        if fire.event == "change":
            for n in jsread.css_safe(tree, fire.selector) or ():
                got.fired.add(n.mem_id)
    for b in bl:
        nodes = jsread.css_safe(tree, b.selector)
        nodes = list(nodes) if nodes else []
        covered.update(n.mem_id for n in nodes)
        if b.net:
            if b.selector == "<delegated>":
                got.click(f"click:<delegated>:{b.event}", "<delegated>", None, fn="<delegated>", args="", params=(),
                          trigger=f"bind:{b.event}", calls=b.net)
            elif not nodes:
                got.unhandle("js_bind", f"{b.event} {b.selector}", None,
                             detail=" ".join(f"{c.method} {c.target}" for c in b.net)[:200])
            for i, n in enumerate(nodes):
                got.click(f"click:{b.selector}[{i}]", b.selector, n, fn=b.selector, args="", params=(),
                          trigger=f"bind:{b.event}", calls=b.net, values=_values(n),
                          plain_href=jsread.plain_href(n, url))
            continue
        if b.nav:
            for n in nodes:
                if n.tag in ("select", "input"):
                    got.jumps[n.mem_id] = (b.nav[0], b.selector)
                elif jsread.plain_href(n, url) is None:
                    got.unhandle("js_nav", b.nav[0], n, detail=f"via bind {b.selector}", text=_text(n))
            continue
        if b.body and nodes:
            _deep(b.body, sc, b.selector, nodes[0], got)
        external = jsread.undefined_calls(b.body, sc, b.params) if b.body else []
        if not b.body and b.how.startswith("unknown:"):  # handed a function no inline script defines
            name = re.match(r"[\w$.]*", b.how.split(":", 1)[1]).group(0)
            external = [name] if name and name not in jsread.KEYWORDS else []
        for ext in external:
            for n in nodes[:1]:
                got.unhandle("js_link", ext, n, detail=f"via bind {b.selector}", text=_text(n))
    return covered


def _deep(body: str, sc: jsread.Script, name: str, node: LexborNode, got: _Collect,
          seen: frozenset = frozenset()) -> None:
    """A handler with no network call within three of the page's own functions (what design §7.8 reads) that makes one
    further down: listed (js_deep), never taken for a handler that loads nothing."""
    deeper = jsread.net_calls(body, sc, depth=jsread.DEEP_LEVELS, seen=seen)
    if deeper:
        got.unhandle("js_deep", name, node, text=_text(node),
                     detail=f"network call {len(deeper[0].via)} functions down: {deeper[0].method} {deeper[0].target}"[:200])


def click_need(click: Click, decision: str | None = None) -> tuple[str, str]:
    """What page_res needs of a click (design §7.8), given the platform file's decision for it (profiles.decision()
    passed as it is): 'handle' (a clicks rule presses it: must, every tab, the one shown on load too), 'no_content'
    (a person saw it only counts: no, click:no_content), 'never' (a sign-in, a captcha, a form sent, a search: never
    pressed, by this round or the next one's browser, design §7.4: no, click:never), 'round2' (the rule no longer
    fits: round2), None or 'unknown' (no rule; profiles.decision() says unknown when the file says nothing, §15): a
    plain link elsewhere on it is enough (no, click:plain_href), the tab shown on load is in the page already (no,
    click:shown_on_load), any other waits for the next round's browser (round2, round2:click); so does a decision
    this version does not know. A platform file never ignores a click that loads something: 'ignore' is refused
    (ValueError). R7 calls this too (design §18.2b). never and unknown here are Claude's way of reading the design
    (【Claude 的执行办法】, review 10-26)."""
    if decision == "ignore":
        raise ValueError(f"{click.key}: a click that loads something is never ignored (design §7.8)")
    if decision == "handle":
        return "must", ""
    if decision == "no_content":
        return "no", "click:no_content"
    if decision == "never":
        return "no", "click:never"
    if decision in (None, "unknown"):
        if click.plain_href:
            return "no", "click:plain_href"
        if click.shown_on_load:
            return "no", "click:shown_on_load"
    return "round2", "round2:click"


UNHANDLED_DECISIONS = ("ignore", "never", "no_content", "round2", "unknown")


def unhandled_need(decision: str | None) -> tuple[str, str]:
    """need and why of an unhandled writing's page_res row, from the platform file's decision (design §7.8): ignore,
    never and no_content need nothing; round2 waits for the next round; anything else (nobody has decided, or a
    decision this version does not know) is must, unhandled:unknown, so the page is never complete. A writing the
    platform file turned into a rule ('handle') is a click or a button, never unhandled (ValueError)."""
    if decision == "handle":
        raise ValueError("a writing with a rule is pressed as a click or a button, never unhandled")
    if decision in ("ignore", "never", "no_content"):
        return "no", f"unhandled:{decision}"
    if decision == "round2":
        return "round2", "round2:unhandled"
    return "must", "unhandled:unknown"


# ---------------------------------------------------------------- addresses in attributes, embed-code blocks


def _attr_links(tree: LexborHTMLParser, got: _Collect) -> None:
    """data-link, data-href and data-url on an element that is no media element: an address an outside script follows
    on a click is a plain link (design §7.8). A Google Sites embed-code block's data-url, and any data-url of a Google
    Sites embed's loader, is never one."""
    for node in tree.css(", ".join(f"[{a}]" for a in _ATTR_LINKS)):
        if node.tag in _MEDIA_TAGS or "data-code" in node.attributes:
            continue
        for attr in _ATTR_LINKS:
            v = (node.attributes.get(attr) or "").strip()
            if v and _ATARI not in v and URLISH.search(v):
                got.link(v, f"[{attr}]", got.zones.of(node))


def _data_code(tree: LexborHTMLParser, got: _Collect, url: str, depth: int) -> int:
    """Google Sites' embed-code blocks (div[data-code]): the HTML the attribute holds is read as part of the page, its
    links, images and frames counted on it in the block's zone; returns the characters of its words."""
    if depth >= _DATA_CODE_DEPTH:
        return 0
    chars = 0
    for node in tree.css("[data-code]"):
        code = node.attributes.get("data-code") or ""
        if not code.strip():
            continue
        zone = got.zones.of(node)
        sub = extract(code, url, None, depth + 1)
        for href, text in sub.links:
            got.link(href, text, zone)
        got.out.frames += [replace(f, zone=zone) for f in sub.frames]
        for img in sub.images:
            old = got.images.get(img.src)
            img = replace(img, zone=zone)
            got.images[img.src] = img if old is None else _merge(old, img)
        got.out.buttons += sub.buttons
        for u in sub.unhandled:
            got.unhandled.setdefault(u.key, replace(u, zone=zone))
        for c in sub.clicks:
            got.clicks.setdefault(c.key, replace(c, zone=zone))
        for v in sub.videos:
            got.video(v.url, v.title, v.how, zone)
        chars += sub.text_chars
    return chars


def _js_name(code: str) -> str:
    m = _JS_CALL.match(code)
    return m.group(1) if m else ""


def _handler(node: LexborNode, attr: str, code: str, got: _Collect) -> None:
    urls, posts = _calls(code)
    for u in urls:
        got.link(u.strip(), f"[{attr}]", got.zones.of(node))
        got.video(u.strip(), _label(node), "link", got.zones.of(node))  # a video it opens: the element's words
    for target, arg in posts:
        got.button(target, arg, node, attr)
        if attr != "onclick" or "settimeout" in code.lower():  # fired by a change or a timer, not a press
            got.unhandle("js_postback", target, node)


def _frame(node: LexborNode, got: _Collect) -> None:
    a = node.attributes
    src = _frame_src(node)
    if not src:
        return
    zone = got.zones.of(node)
    got.out.frames.append(Frame(src, node.tag, zone, (a.get("title") or a.get("name") or "").strip()[:100],
                                _size(node, "width"), _size(node, "height"), _hidden(node)))
    got.link(src, "[frame]" if node.tag in ("iframe", "frame") else "[embed]", zone)
    got.video(src, a.get("title") or a.get("aria-label") or "", "embed", zone)


def _frame_src(node: LexborNode) -> str:
    """Where a frame's page is: an object's data; else src, unless a lazy loader left it blank (empty, about:blank,
    javascript:) and keeps the address in data-src, data-original or data-lazy-src (WP Rocket). A frame left blank with
    nothing else is listed as written."""
    a = node.attributes
    if node.tag == "object":
        return (a.get("data") or "").strip()
    src = (a.get("src") or "").strip()
    if src and not src.lower().startswith(_BLANK_SRC):
        return src
    return next((lazy for attr in _LAZY if (lazy := (a.get(attr) or "").strip())), src)


def _hidden(node: LexborNode) -> bool:
    """A frame nobody sees (a tracker's): width or height written as 0, display:none or visibility:hidden in its style,
    or the hidden attribute. Never one a script sizes when it loads (onload: an auto-height frame shows its page)."""
    a = node.attributes
    if a.get("onload"):
        return False
    return (0 in (_size(node, "width"), _size(node, "height")) or "hidden" in a
            or bool(_HIDDEN_STYLE.search(a.get("style") or "")))


def _images(tree: LexborHTMLParser, got: _Collect) -> None:
    for node in tree.css("img, picture source"):
        a = node.attributes
        if node.tag == "img":
            got.image(a.get("src") or "", "src", node)
            for attr in _LAZY:
                got.image(a.get(attr) or "", attr, node)
        how = "picture" if node.tag == "source" else "srcset"
        for attr in ("srcset", "data-srcset"):
            got.image(largest_in_srcset(a.get(attr) or "") or "", how, node)
    for node in tree.css("[style]"):
        style = node.attributes.get("style") or ""
        if "background" in style.lower():  # background-image, and the shorthand background: url(…)
            for _q, src in _BG_URL.findall(style):
                _background(src, "bg", node, got)
    for node in tree.css("[data-bg], [data-background]"):
        _background(node.attributes.get("data-bg") or node.attributes.get("data-background") or "", "data-bg", node,
                    got)
    for node in tree.css("body[background], table[background], td[background], th[background], tr[background]"):
        _background(node.attributes.get("background") or "", "background", node, got)


def _background(src: str, how: str, node: LexborNode, got: _Collect) -> None:
    """A background is an image, unless what it names is a document (scope.ATTACHMENT_EXT): then it is the page's
    attachment, a plain link to fetch as one (design §7.2: elec's news list), whatever its zone or name."""
    src = (src or "").strip()
    if src and scope.extension(src) in scope.ATTACHMENT_EXT:
        got.link(src, "[background]", got.zones.of(node))
        return
    got.image(src, how, node)


def largest_in_srcset(srcset: str) -> str | None:
    """The candidate with the largest width (w) or density (x) descriptor, as the WHATWG parser splits a srcset: a URL
    is a run of non-space characters (a comma inside it stays), a trailing comma ends it; no descriptor counts as 1x."""
    best, best_v, i, n = None, -1.0, 0, len(srcset)
    while i < n:
        while i < n and (srcset[i].isspace() or srcset[i] == ","):
            i += 1
        start = i
        while i < n and not srcset[i].isspace():
            i += 1
        url, desc = srcset[start:i], ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            end = srcset.find(",", i)
            end = n if end < 0 else end
            desc, i = srcset[i:end].strip(), end
        if not url:
            continue
        m = re.match(r"^(\d+(?:\.\d+)?)[wx]$", desc.split()[0]) if desc else None
        value = float(m.group(1)) if m else 1.0
        if value >= best_v:
            best, best_v = url, value
    return best


def _size(node: LexborNode, dim: str) -> int | None:
    if (m := _PX.match(node.attributes.get(dim) or "")) is not None:
        return int(m.group(1))
    m = re.search(rf"(?i)(?:^|[;\s]){dim}\s*:\s*(\d+)\s*px", node.attributes.get("style") or "")
    return int(m.group(1)) if m else None


def _selects(tree: LexborHTMLParser, got: _Collect) -> None:
    """Every <select>; a jump menu (a script sends the browser to an address built from its value) also keeps that
    template, each option's value and text, and whether the page's script fires it when it loads (select:auto).
    A checkbox whose change rebuilds the address is listed as input:<selector> the same way."""
    for node in tree.css("select"):
        template, selector = got.jumps.get(node.mem_id, ("", ""))
        name = node.attributes.get("name") or node.attributes.get("id") or selector or ""
        options = tuple(((o.attributes.get("value") or "").strip(), o.text(strip=True)[:60]) for o in node.css("option"))
        detail = f"{name} options={len(options)}"
        if n_urls := sum(v.startswith(("http://", "https://", "/")) for v, _t in options):
            detail += f" url_options={n_urls}"  # a jump menu: each option is a link
        got.unhandle("select", name, node, detail=detail, options=options if template else (), template=template,
                     auto=bool(template) and node.mem_id in got.fired)
    for node in tree.css("input"):
        if node.mem_id not in got.jumps:
            continue
        template, selector = got.jumps[node.mem_id]
        name = selector or (f"#{node.attributes['id']}" if node.attributes.get("id") else
                            node.attributes.get("name") or "")
        got.unhandle("input", name, node, detail=f"{name} builds {template}", text=_text(node), template=template,
                     auto=node.mem_id in got.fired)


def _get_forms(tree: LexborHTMLParser, got: _Collect) -> None:
    for node in tree.css("form"):
        if (node.attributes.get("method") or "get").strip().lower() != "get":
            continue
        if node.css_first("input[type=password]") is not None:
            continue
        fields = []
        for el in node.css("input[name], select[name], textarea[name]"):
            kind = (el.attributes.get("type") or "").lower()
            name = el.attributes.get("name") or ""
            if kind not in ("submit", "button", "reset", "image") and name not in fields:
                fields.append(name)
        action = (node.attributes.get("action") or "").strip()
        got.unhandle("get_form", f"{action} {','.join(fields)}", node)


# ---------------------------------------------------------------- images: needed or not, and why (design §7.2)


def album_page(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if ((parts.hostname or "").lower(), parts.path.lower()) in ALBUM_NOT:
        return False
    return bool(ALBUM.search(unquote(f"{parts.path}?{parts.query}")))


def image_need(img: Image, page_url: str, profiled: bool = False) -> tuple[str, str]:
    """("must", "") or ("no", why), every why in IMAGE_WHY: a captcha's picture (scope.CAPTCHA: never fetched, and
    never content, so never a picture the page lacks; 【Claude 的执行办法】, review 10-26), a data: image, a size under
    80, an album page, a decoration file name outside the body, a layout area on a site without a profile.
    decor:repeat is the crawler's, once it has seen the site's pages (decor_repeat)."""
    if scope.CAPTCHA.search(unquote(img.src.split("?", 1)[0])):
        return "no", "captcha"
    if img.src.startswith("data:"):
        return "no", "data"
    if any(x is not None and x < MIN_SIZE for x in (img.width, img.height)):
        return "no", "tiny"
    if album_page(page_url):
        return "no", "album"
    if img.zone == "chrome":
        name = img.src.split("?", 1)[0].rsplit("/", 1)[-1]
        if DECOR_FILE.search(name):
            return "no", "decor_name"
        if not profiled:
            return "no", "chrome"
    return "must", ""


def never_template(url: str) -> bool:
    """Uploads, which no repeat makes a template: editors' uploadfile and userfiles folders, Joomla's /images/<year>/."""
    try:
        return bool(_NEVER_TEMPLATE.search(urlsplit(url).path))
    except ValueError:
        return False


def decor_repeat(url: str, pages_with: int, site_pages: int, ever_body: bool) -> bool:
    """A layout image by how often it repeats (Bar-Yossef & Rajagopalan 2002, template detection): on 5 or more of the
    site's stored pages, 30% of them or more, and never in a body. A folder name (/images/, /img/) alone is no sign."""
    return (not ever_body and not never_template(url) and pages_with >= REPEAT_PAGES
            and pages_with >= REPEAT_SHARE * site_pages)


# ---------------------------------------------------------------- a short link's own page (design §7.7)


def shortlink_target(html: str, url: str) -> tuple[str, str] | None:
    """Where a short-link service's 200 page sends the browser (review 10-26 A2), as (absolute address, how): its meta
    refresh first ("refresh"), then the service's own writing in scope.SHORTLINK_RULES ("rule:<service>": reurl.cc's
    input#target). None when neither says an http(s) address: nothing is guessed (round1.short_answer makes it
    unhandled:shortlink)."""
    tree = LexborHTMLParser(html or "")
    for node in tree.css("meta[http-equiv]"):
        if (node.attributes.get("http-equiv") or "").lower() == "refresh":
            m = _REFRESH.search(node.attributes.get("content") or "")
            target = _absolute(m.group(1).strip(), url) if m else None
            if target:
                return target, "refresh"
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    rule = scope.SHORTLINK_RULES.get(host)
    if rule is not None:
        css, attr = rule
        for node in tree.css(css):
            target = _absolute((node.attributes.get(attr) or "").strip(), url)
            if target:
                return target, f"rule:{host}"
    return None


def _absolute(href: str, base: str) -> str | None:
    """href joined to base, when that is an http(s) address."""
    if not href:
        return None
    try:
        joined = urljoin(base, href)
        ok = urlsplit(joined).scheme.lower() in ("http", "https")
    except ValueError:
        return None
    return joined if ok else None


# ---------------------------------------------------------------- GET paging (design §9)


def is_pager(page: str, target: str, text: str) -> bool:
    """Whether a link from page (canonical) to target (canonical) is the next page of the same list."""
    if not _pager_text(text) or target == page:
        return False
    a, b = urlsplit(page), urlsplit(target)
    if (a.scheme, a.netloc) != (b.scheme, b.netloc):
        return False
    if a.path == b.path:
        return _one_pager_param(a.query, b.query)
    wa, wb = _WP_PAGE.sub("/", a.path), _WP_PAGE.sub("/", b.path)
    return wa == wb and a.query == b.query and (wa != a.path or wb != b.path)


def _pager_text(text: str) -> bool:
    t = unicodedata.normalize("NFKC", text or "").strip().lower()
    return t.strip("[]() ").isdigit() or any(w in t for w in NEXT_WORDS)


def _one_pager_param(qa: str, qb: str) -> bool:
    def params(q: str) -> dict[str, tuple[str, ...]]:
        out: dict[str, tuple[str, ...]] = {}
        for k, v in parse_qsl(q, keep_blank_values=True):
            out[k] = (*out.get(k, ()), v)
        return out

    pa, pb = params(qa), params(qb)
    diff = [k for k in pa.keys() | pb.keys() if pa.get(k) != pb.get(k)]
    return len(diff) == 1 and diff[0].lower() in PAGER_PARAMS
