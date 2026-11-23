"""網頁核查's whole sweep (design docs/superpowers/specs/2026-11-16-page-check-design.md section 六): every page of the
crawler's ledger checked once by the very function the panel uses for one page (nknu.ops.pagecheck.check), so the
list of sites, a site's list of pages and the page opened from them never disagree. Of each check only what the lists
need is kept (its state, how many it missed where, the misses themselves).

Kept on disk since 2026-11-22 (owner: 「改」; until then in memory only, so every restart of the panel checked the
whole archive again, about 17 minutes of 「正在讀帳本」, and every change of the ledger did the same): in the panel's
own writable file (ops.db, nknu.ops.sweep_store), the results, the listing they were drawn with, each stored page as
read (pagecheck.Read: its links, images and buttons and the lookup each needs) and one record a night of the school's
totals. A restart draws the kept results at once, said with when they were checked, and checks again only if the
ledger or the rules changed. A changed ledger is looked up again page by page and only the pages whose record changed
are read again: a page's verdict is its own stored copy (the read, keyed by the record: pagecheck.record_key) and the
ledger's rows of the addresses it links (looked up every time, by the same code a whole check uses: pagecheck.look_up),
so the result is what a whole check gives. Measured on the Mac on the 1,855 stored pages of an old ledger (archive/,
2026-11-02) and their real WARCs: a whole check 4.3 ms a page, the sweep's check reading the page 3.3 ms (no safe page,
no caveats), looked up from its kept read 0.7 ms (the ledger's lookups are about 16% of a check), 0.44 ms with the url
rows shared by the pages of a chunk (their menus link the same addresses: 27% of the lookups are distinct); the same
results on every page, also after 8% of the rows and 5% of the records were changed (92 pages read again). So on the
lab (about 17 ms a page, 58,707 pages) a night's update is the ledger read again (a minute or two) plus about two
minutes of lookups plus the pages the crawl fetched again; a restart over an unchanged ledger is nothing. Without the
file (no --ops-db) everything stays in memory, as before. 【Claude】 the design.

Owner 11-16, on the first version's one address box: 「为什么要我输入网址或文件编号？我怎么会记得网址或者文件编号呀？」.
Every way of doing it below is 【Claude】's (design section 六 says so).

How it runs (design section 六 4): one thread of the panel's process reads the ledger's pages, the sites and the
pages' names, then checks the pages a few at a time with its own read-only ledger connection, the site the owner opened
last first. One thread is enough: a check takes about 14 ms (the lab's ledger, 11-16), so the 48,000 pages take about
eleven minutes and a site opened is done within a minute or two (the design's three worker processes were dropped
11-16, with the owner's 「你太夸张了」 about the machinery around this change: a pool, its crashes and its hangs cost far
more code than the minutes they save 【Claude】). The first sweep starts with the first request of 網頁核查; with the
file, a watcher then keeps it current by itself. It sweeps again when the ledger has changed and has been quiet for
QUIET_SECONDS (or at most RESWEEP_SECONDS after the last began, while it keeps changing), never while the nightly
update fetches or exports (the hold), and pauses between its chunks while the ledger is being written, so the crawler
keeps the disk; the older results stay on the pages, said to be older, until each is replaced. A request never waits
on it: it copies what is there under a short lock and draws from the copy."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import zlib
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

import nknu.archive
import nknu.settings
from nknu.archive import scope
from nknu.ops import pagecheck
from nknu.ops.history import window
from nknu.ops.look import t2s, tw
from nknu.ops.nightly import night_of
from nknu.ops.sweep_store import SweepStore
from nknu.totext import pagetext

log = logging.getLogger(__name__)

CHUNK = 20  # pages checked between two looks at which site was asked for: well under a second 【Claude】
# a changed ledger is checked again once it has been quiet this long (its files' last change, so a restart knows it
# too), or at most this long after the last sweep began while it keeps changing (a crawl run by hand in the day);
# a re-check over kept reads takes minutes, so once in half an hour leaves the disk to the crawler 【Claude】
QUIET_SECONDS = 300.0
RESWEEP_SECONDS = 1800.0
WATCH_SECONDS = 60.0  # the watcher's look at the ledger and the night 【Claude】
FLUSH = 200  # results and reads written to the file together, at most this many pages apart 【Claude】
RETRY_SECONDS = 30.0  # a sweep that could not read the ledger is tried again after this 【Claude】
NIGHTS_SHOWN = 14  # the school's trend: as many nights as the other panels draw (history.SHOWN)
KEEP = 300  # misses kept per page, as many as the page's own lists show (check_page.PROBLEMS_SHOWN) 【Claude】
ROUND_ONE = "1"  # site_plan.round of the first round's sites
NOFETCH = ("failed", "gone")  # a page's own bucket that says the page itself is not there
# a page's own buckets listed though no copy of it is stored: its failure, its deletion, its doubtful content is the
# finding. Not listed: an address that redirects (its landing is a page of its own), one a rule keeps out, one left to
# the next round with nothing stored. 【Claude】
UNSTORED_LISTED = ("failed", "gone", "look", "review")
NOT_LISTED = ("rule", "album")
PASSING = ("busy", "error")  # a check's states that say nothing of the page itself: an older result outlives them
# the entry's list of the school's misses (11-16 evening: 1,361 of the 1,406 pages whose content area misses something
# miss one and the same link, which the sites' numbers alone never said): the content and attachment areas only (a
# menu's miss is on every page of its site and would bury them), merged across sites, each address once and a button
# once per site, in three classes (pagecheck.CLASSES, owner 11-22); per class the first TOP rows are kept, as many as
# the page draws (check_sites.MERGED_SHOWN) 【Claude】
# 100 since 11-22 evening: the entry is sent again every few seconds while the sweep runs, through a tunnel, and
# each site's page holds its whole list 【Claude】
TOP = 100
TOP_ZONES = ("in", "files")
CLASS_KEYS = tuple(k for k, _n, _t in pagecheck.CLASSES)
KIND_KEYS = tuple(k for k, _n in pagecheck.KINDS)
# the entry's site list sorts by one of its numbers (a header pressed), 正文區有漏 when none is asked for 【Claude】
SORTS = ("gap", "pages", "menu_links", "check", "nofetch")

# A page's state in the lists, its words and tone (look.CSS's pills; states only good / warn / bad and greys), and its
# place in a site's list: the content area's misses first, then pages that are not there, then what to look at, the
# menus' misses, pages not yet checked, and the pages with nothing to say. 【Claude】
PILLS = {
    "gap": ("正文區有漏", "bad", 0),
    "files": ("附件區有漏", "bad", 0),  # only the attachment area misses: the opened page says 附件區 too
    "failed": (pagecheck.BUCKET_NAME["failed"], "bad", 1),
    "not_fetched": ("沒抓下來", "bad", 1),
    "error": ("核查出錯", "bad", 2),
    "unreadable": ("存檔讀不出來", "bad", 2),
    "gone": (pagecheck.BUCKET_NAME["gone"], "none", 2),
    "look": (pagecheck.BUCKET_NAME["look"], "warn", 3),
    "review": (pagecheck.BUCKET_NAME["review"], "warn", 3),
    "check": ("要查", "warn", 3),
    "menu": ("只漏選單", "warn", 4),
    "wait": ("核查中", "none", 5),
    "busy": ("帳本正在寫入", "none", 5),
    "ok": ("沒有漏抓", "good", 6),
    "not_html": ("存的不是網頁", "none", 6),
}
# the chips of a site's page list (design section 六 2), in their order: what each page falls under
CHIPS = (("gap", "正文區有漏"), ("menu", "只漏選單"), ("check", "要查"), ("nofetch", "這一頁沒抓下來"))
CHIP_TIPS = {"gap": "正文區或附件區（會進庫的地方）有沒抓到的連結、圖片、按鈕",
             "menu": "只有選單、頁首、頁尾這些不進庫的地方有沒抓到的",
             "check": "有要查的（內容可疑、爬蟲還沒決定怎麼處理），或這一頁本身內容可疑、核查出錯",
             "nofetch": "這一頁本身沒抓下來：失敗、存檔讀不出來（學校已刪除的不算，照樣列在清單裡）"}
# a page under the nofetch chip: the crawler can do something about it; a page the school deleted is listed, grey
NOFETCH_STATES = ("failed", "not_fetched", "unreadable")


# ---------------------------------------------------------------- what a check keeps

class Problem(NamedTuple):
    """One miss of a page, as its check listed it (pagecheck.Item.as_dict, only what the lists draw)."""

    target: str
    text: str
    zone: str  # in | files | out
    bucket: str  # one of pagecheck.PROBLEMS
    reason: str
    what: str  # link | image | button
    n: int  # its number on the page: the page opened from a list flashes it
    tip: str
    cls: str = ""  # whose move it is (pagecheck.CLASSES' key)


@dataclass(slots=True)
class Found:
    """What one check of a page left for the lists (made in a worker, sent back pickled)."""

    url: str
    state: str  # pagecheck.STATES' key, or 'error': the check itself failed
    fix: tuple[int, int, int] = (0, 0, 0)  # 爬蟲可補 in the content area, the attachment area, the menus
    check: int = 0  # 要查
    next: int = 0  # 第二輪, counted only
    problems: tuple[Problem, ...] = ()
    more: int = 0  # misses beyond KEEP
    error: str = ""
    gen: int = 0  # the sweep that made it
    at: float = 0.0  # when, seconds since 1970


def compact(url: str, summary: dict) -> Found:
    """A check's summary as the lists keep it."""
    v = summary.get("verdict") or {}
    fix = v.get("fix") or {}
    found = summary.get("problems") or []
    kept = tuple(Problem(p.get("target") or "", p.get("text") or "", p.get("zone") or "out", p["bucket"],
                         p.get("reason") or result_words(p.get("result")), p.get("what") or "link",
                         int(p.get("n") or 0), p.get("tip") or "",
                         p.get("cls") or pagecheck.miss_class(p["bucket"], p.get("what") or "link", p.get("result"),
                                                              p.get("code")))
                 for p in found[:KEEP])
    return Found(url, summary.get("state") or "error", (fix.get("in", 0), fix.get("files", 0), fix.get("out", 0)),
                 int(v.get("check", 0)), int(v.get("next", 0)), kept, max(0, len(found) - KEEP))


def result_words(result: str | None) -> str:
    """A ledger result in words for a miss whose reason says nothing (the school's homepage taken for a file: 應該是
    檔案，回來不是, not the bucket's 失敗); '' for none."""
    if not result:
        return ""
    said = pagecheck.RESULT_WORDS.get(result) or pagecheck.reason_words(result)
    return "" if said == result else said


def errored(url: str, words: str) -> Found:
    return Found(url, "error", error=words)


def check_pages(urls: Iterable[str], archive: Path, cache: pagecheck.LedgerCache | None,
                checker: Callable = pagecheck.check_verdict, stop: threading.Event | None = None,
                reads: dict[str, tuple[str, pagecheck.Read]] | None = None) -> list[Found]:
    """Each page checked as the panel checks it (pagecheck.check_verdict: the same verdict, without the safe page;
    reads: the pages read before, by address, and where the pages read now are put; the url rows looked up shared by
    these pages, a few of one site), a failure kept as that page's error instead of ending the rest."""
    out = []
    shared: dict = {}
    for url in urls:
        if stop is not None and stop.is_set():
            break
        try:
            out.append(compact(url, checker(archive, url, ledgers=cache, reads=reads, shared=shared).summary))
        except Exception as exc:  # one odd page never stops the sweep: it is listed under 要查
            log.exception("核查出错：%s", url)
            out.append(errored(url, f"{type(exc).__name__}: {exc}"[:300]))
    return out


# ---------------------------------------------------------------- what is kept on disk (nknu.ops.sweep_store)

def _source(*paths: Path) -> str:
    """A digest of these files' bytes, by their folder and name (the same code reads the same wherever it is)."""
    h = hashlib.sha256()
    for path in sorted(set(paths), key=lambda p: (p.parent.name, p.name)):
        h.update(f"{path.parent.name}/{path.name}\0".encode())
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"\0missing")
    return h.hexdigest()[:16]


@functools.cache
def read_rules() -> str:
    """What reads a stored page (pagecheck.read_record): the page check, the crawler's own readers it calls
    (nknu.archive: links, profiles, postback, scope…), the library's reading of the content area (pagetext) and the
    config files that reading loads. A kept read made by other rules is read again. 【Claude】: every module of
    nknu.archive, not only those imported today, so a reader added there counts too."""
    files = [Path(pagecheck.__file__), Path(pagetext.__file__), Path(nknu.settings.__file__),
             *Path(nknu.archive.__file__).parent.glob("*.py")]
    try:
        config = nknu.settings.load_settings().config_dir
        files += [config / pagetext.ROUND1_LIST, config / pagetext.SITE_TABLE]
    except Exception:  # noqa: BLE001 - no settings: the files are left out, the code still counts
        log.warning("网页核查：读不到设定档的位置，规则只看程序")
    return _source(*files)


@functools.cache
def rules() -> str:
    """What checks a page and keeps its result: read_rules and this module (KEEP, compact…). Results kept by other
    rules are drawn, said to be older, and checked again."""
    return f"{read_rules()}-{_source(Path(__file__))}"


def _pack(data: object) -> bytes:
    return zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)


def _unpack(blob: bytes) -> object:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def pack_found(f: Found) -> bytes:
    return _pack([f.state, list(f.fix), f.check, f.next, [list(p) for p in f.problems], f.more, f.error])


def unpack_found(url: str, blob: bytes, gen: int, at: float) -> Found:
    state, fix, check, nxt, problems, more, error = _unpack(blob)  # type: ignore[misc]
    return Found(url, state, tuple(fix), check, nxt, tuple(Problem(*p) for p in problems), more, error, gen, at)


def pack_steps(read: pagecheck.Read) -> bytes:
    """A read: its state and its steps, each without its href as written (a check's verdict never reads it)."""
    return _pack([read.state, [[s.what, s.target, s.text, s.zone, s.look, s.key, s.argument,
                                list(s.press) if s.press else None] for s in read.steps]])


def unpack_steps(blob: bytes) -> pagecheck.Read:
    state, steps = _unpack(blob)  # type: ignore[misc]
    return pagecheck.Read(state, tuple(pagecheck.Step(what, "", target, text, zone, look, key, argument,
                                                      tuple(press) if press else None)
                                       for what, target, text, zone, look, key, argument, press in steps))


def pack_listing(listing: Listing) -> bytes:
    return _pack({"pages": [[p.url, p.site, p.own, p.stored, p.path, listing.names.get(p.url, ""),
                             *listing.keys.get(p.url, ("", ""))] for p in listing.pages.values()],
                  "sites": [[s.key, s.name, s.home, s.round1, list(s.keys)] for s in listing.sites.values()],
                  "order": listing.order})


def unpack_listing(blob: bytes) -> Listing:
    data = _unpack(blob)
    out = Listing()
    for url, site, own, stored, path, name, key_name, key_url in data["pages"]:  # type: ignore[index]
        out.pages[url] = Page(url, site, own, stored, path)
        out.site_pages.setdefault(site, []).append(url)
        out.names[url] = name
        out.keys[url] = (key_name, key_url)
    for key, name, home, round1, keys in data["sites"]:  # type: ignore[index]
        out.sites[key] = Site(key, name, home, round1, tuple(keys))
    out.order = list(data["order"])  # type: ignore[index]
    return out


def _stamp_of(text: str | None) -> tuple | None:
    """A kept LedgerCache.stamped (JSON) as stamped returns it, None for none."""
    if not text:
        return None
    return tuple(tuple(x) if x is not None else None for x in json.loads(text))


def when(at: float | None) -> str:
    """A time of the sweep for the pages: MM-DD HH:MM, Taipei ('' for none)."""
    return datetime.fromtimestamp(at, pagecheck.TAIPEI).strftime("%m-%d %H:%M") if at else ""


# ---------------------------------------------------------------- the pages, the sites and their names

@dataclass(slots=True)
class Page:
    url: str
    site: str  # coalesce(owner_site, site): the library's sites.site and docs.site
    own: str  # the page's own bucket (pagecheck.bucket_of its url row)
    stored: bool  # a copy of it is in the archive
    path: str  # its address without the host, readable


@dataclass(slots=True)
class Site:
    key: str
    name: str  # Traditional: the library's sites.name, else the crawler's site table's name in Traditional
    home: str  # the address its 「從首頁開始」 checks ('' when none is known)
    round1: bool
    keys: tuple[str, ...]  # what it is found by, normalized (find)


@dataclass
class Listing:
    """What a sweep reads once at its start."""

    pages: dict[str, Page] = field(default_factory=dict)  # by address, a site's pages together
    sites: dict[str, Site] = field(default_factory=dict)
    site_pages: dict[str, list[str]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)  # a page's name (name_of's order)
    keys: dict[str, tuple[str, str]] = field(default_factory=dict)  # a page's name and address, normalized (find)
    order: list[str] = field(default_factory=list)  # the order the sites are swept in


def norm(text: str | None) -> str:
    """text as find compares it: Simplified characters (look.t2s, on what is typed and on what it is compared with
    alike: the owner's 台湾 finds the school's 台灣), lower case, no white space."""
    return re.sub(r"\s+", "", t2s(text or "")).lower()


def bare(text: str | None) -> str:
    """text without white space, lower case: how the generic words below are compared (as the school writes them)."""
    return re.sub(r"\s+", "", text or "").lower()


def path_of(url: str) -> str:
    """An address without its scheme and host, readable (a percent-encoded path decoded)."""
    parts = urlsplit(url)
    return unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")


SCHOOL_NAMES = ("國立高雄師範大學", "國立高師大", "高雄師範大學", "高師大", "NKNU", "National Kaohsiung Normal University")
# a link's words that name no page (a list's 「更多」, a banner's 「前往連結」…): the next most used words name it 【Claude】
# (the lab's ledger, 11-16: a list's pages are linked most as 「上一頁」, an admin page as its footer's visitor counter)
GENERIC_ANCHORS = frozenset({"更多", "more", "more...", "more»", "詳全文", "詳細內容", "閱讀更多", "readmore", "前往連結",
                             "回首頁", "點此", "按此", "點我", "連結", "link", "here", "clickhere", "下載", "download",
                             "...", "…", "»", "›", "»more", "+more", "查看更多", "了解更多", "上一頁", "下一頁", "上頁",
                             "下頁", "回上頁", "回上一頁", "第一頁", "最後一頁", "最末頁", "prev", "next", "back", "返回",
                             "回列表", "回上層", "top", "回頂端", "列印", "print", "分享", "share"})
_COUNTER = re.compile(r"(?i)(人次|次數|瀏覽|點閱|visitors?|views?)\s*[:：]?\s*[\d,]+$")
GENERIC_TITLES = frozenset({"首頁", "home", "index", "default", "首页"})
_TITLE_SPLIT = re.compile(r"\s*(?:[｜|│]|\s[-–—>»›:]{1,2}\s|::)\s*")
_URLISH = re.compile(r"(?i)^(?:https?://|www\.)|^[a-z0-9.-]+\.[a-z]{2,}(?:/|$)")
_JUNK = re.compile(r"^[\W\d_]+$")


def anchor_words(text: str | None) -> str:
    """A link's words as a page's name, or '' for none that names it: a crawler's marker ([redirect], [onclick]…),
    an address, a number or a sign, a generic 「更多」."""
    words = " ".join((text or "").split())
    if not words or pagecheck._MARKER.fullmatch(words) or (words.startswith("[") and words.endswith("]")):
        return ""
    if _URLISH.search(words) or _JUNK.match(words) or len(words) < 2 or bare(words) in GENERIC_ANCHORS \
            or _COUNTER.search(words):
        return ""
    return words[:80]


def title_words(title: str | None, own_names: Iterable[str]) -> str:
    """A stored page's <title> without the school's and the site's own names (「國立高雄師範大學 教務處｜首頁｜首頁」 is
    「首頁」), '' when nothing else is left."""
    text = " ".join((title or "").split())
    names = sorted({n for n in (*SCHOOL_NAMES, *own_names) if n and len(n) > 1}, key=len, reverse=True)
    if names:  # NKNU, nknu: the Latin names in any case
        text = re.sub("|".join(map(re.escape, names)), " | ", text, flags=re.IGNORECASE)
    parts: list[str] = []
    for part in _TITLE_SPLIT.split(text):
        part = part.strip(" -–—_:：|｜>»›·.")
        if part and (not parts or parts[-1] != part) and part not in parts:
            parts.append(part)
    return " · ".join(parts)[:80]


SHARED = 5  # a name this many pages of one site share names none of them 【Claude】


def name_of(url: str, *, library: dict[str, str], anchors: dict[str, str], title: str, own_names: Iterable[str],
            home: bool = False, shared: Callable[[str], bool] = lambda _name: False) -> str:
    """A page's name (design section 六 2): 「首頁」 for the site's home (other sites link it by their own words: 「本校
    教務處網頁連結」), the library's title of its address, else the words other pages link it with most, else its
    <title> without the site's name, else its address; a title or words that are only the school's and the site's own
    names (「國立高雄師範大學 總務處」: 335 pages of the lab's ledger) name nothing and give way, and so does one that
    SHARED pages of the site share (shared: the SSO platform's own 「國立高雄師範大學 單一登入 網路資訊平台」 on every
    page of a site, seen on the lab 11-16) unless nothing else is left. 【Claude】"""
    if home:
        return "首頁"
    own_names = tuple(own_names)
    kept = []
    for got in ((library.get(url) or "").strip(), anchors.get(url, "")):
        if got and title_words(got, own_names):
            kept.append(got)
    got = title_words(title, own_names)
    if got and bare(got) not in GENERIC_TITLES:
        kept.append(got)
    return next((k for k in kept if not shared(k)), kept[0] if kept else path_of(url))


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _anchors(conn: sqlite3.Connection, wanted: set[str]) -> dict[str, str]:
    """For each stored page, the words other pages link it with most (anchor_words; ties: the longer words). Counted
    here, not by a GROUP BY: a sort that large would spill into SQLite's temporary files, and nothing is written."""
    counts: dict[str, dict[str, int]] = {}
    for dst, text in conn.execute("SELECT l.dst, l.text FROM link l JOIN url u ON u.url = l.dst "
                                  "WHERE u.kind = 'page' AND u.warc IS NOT NULL"):
        if dst not in wanted or not (words := anchor_words(text)):
            continue
        mine = counts.setdefault(dst, {})
        mine[words] = mine.get(words, 0) + 1
    return {dst: max(mine.items(), key=lambda kv: (kv[1], len(kv[0])))[0] for dst, mine in counts.items()}


def read_listing(conn: sqlite3.Connection, library: sqlite3.Connection | None) -> Listing:
    """The pages a sweep checks, the sites listed, and the pages' names: the ledger (read-only, its result views
    built: pagecheck.open_ledger) and the library when there is one (names only)."""
    rows = sorted(conn.execute("SELECT id, url, coalesce(owner_site, site), result, reason, warc IS NOT NULL, title "
                               "FROM url_result WHERE kind = 'page'"), key=lambda r: (r[2] or "", r[1]))
    out = Listing()
    titles: dict[str, str] = {}
    for _id, url, site, result, reason, stored, title in rows:
        own = pagecheck.bucket_of(result, reason)
        if result == "redirect" or own in NOT_LISTED or not (stored or own in UNSTORED_LISTED):
            continue
        out.pages[url] = Page(url, site or "", own, bool(stored), path_of(url))
        out.site_pages.setdefault(site or "", []).append(url)
        if title:
            titles[url] = title
    plan = {}
    if _has(conn, "site_plan"):
        plan = {r[0]: (r[1] or "", r[2] or "", str(r[3] or "")) for r in
                conn.execute("SELECT site, name, home, round FROM site_plan")}
    lib_sites: dict[str, tuple[str, str]] = {}
    aliases: dict[str, list[str]] = {}
    lib_titles: dict[str, str] = {}
    if library is not None:
        if _has(library, "sites"):
            lib_sites = {r[0]: (r[1] or "", r[2] or "") for r in library.execute("SELECT site, name, unit FROM sites")}
        if _has(library, "unit_aliases"):
            for alias, unit in library.execute("SELECT alias, unit_id FROM unit_aliases"):
                aliases.setdefault(unit, []).append(alias)
        for url, title in library.execute("SELECT url, title FROM docs WHERE title <> '' ORDER BY doc_id"):
            if url in out.pages:
                lib_titles.setdefault(url, title)
    keys = {k for k, (_n, _h, rnd) in plan.items() if rnd == ROUND_ONE} | set(out.site_pages) | set(lib_sites)
    keys.discard("")
    planned: set[str] = set()  # the homes the crawler's site table names: only these are called 首頁
    for key in sorted(keys):
        plan_name, home, rnd = plan.get(key, ("", "", ""))
        lib_name, unit = lib_sites.get(key, ("", ""))
        name = lib_name or tw(plan_name) or key
        home = (scope.canonical(home) or home) if home else ""
        if home:
            planned.add(home)
        elif out.site_pages.get(key):  # no home known: 「從首頁開始」 opens the site's shortest stored address
            home = min(out.site_pages[key], key=lambda u: (len(u), u))
        found_by = (name, plan_name, tw(plan_name), key, unit, *aliases.get(unit, ()), unquote(home))
        out.sites[key] = Site(key, name, home, rnd == ROUND_ONE, tuple(dict.fromkeys(norm(k) for k in found_by if k)))
    own_names: dict[str, tuple[str, ...]] = {}
    for key, site in out.sites.items():
        plan_name = plan.get(key, ("", "", ""))[0]
        own_names[key] = (site.name, plan_name, tw(plan_name), lib_sites.get(key, ("", ""))[1])
    anchors = _anchors(conn, set(out.pages))
    uses: Counter = Counter()  # (site, a candidate name): on how many of the site's pages it is a candidate
    for url, page in out.pages.items():
        names = own_names.get(page.site, ())
        cands = {lib_titles.get(url, "").strip(), anchors.get(url, ""), title_words(titles.get(url, ""), names)}
        uses.update((page.site, c) for c in cands if c)
    for url, page in out.pages.items():
        site = out.sites.get(page.site)
        name = name_of(url, library=lib_titles, anchors=anchors, title=titles.get(url, ""),
                       own_names=own_names.get(page.site, ()), home=site is not None and site.home == url and url in planned,
                       shared=lambda n, k=page.site: uses[(k, n)] >= SHARED)
        out.names[url] = name
        out.keys[url] = (norm(name), norm(unquote(url)))
    # the first round's sites before the others, the small ones first: most sites are done in the first minutes
    out.order = sorted(out.site_pages, key=lambda k: (k not in out.sites or not out.sites[k].round1,
                                                      len(out.site_pages[k]), k))
    return out


# ---------------------------------------------------------------- what the lists say of a page and of a site

def state_of(page: Page, found: Found | None) -> str:
    """A page's state in the lists (PILLS' key)."""
    if found is None:
        return "wait"
    if page.own in NOFETCH:
        return page.own
    if found.state == "error":
        return "error"
    if found.state in ("unreadable", "not_fetched", "busy", "not_html"):
        return found.state
    if found.state != "ok":  # not_in_ledger / no_archive: the page's address no longer reads (it was listed)
        return "error"
    if found.fix[0]:
        return "gap"
    if found.fix[1]:
        return "files"
    if page.own in ("look", "review"):
        return page.own
    if found.check:
        return "check"
    if found.fix[2]:
        return "menu"
    return "ok"


def chips_of(page: Page, found: Found | None, state: str) -> tuple[str, ...]:
    """The chips a page falls under (CHIPS): it can be under more than one."""
    if found is None:
        return ()
    out = []
    ok = found.state == "ok" and page.own not in NOFETCH
    if ok and found.fix[0] + found.fix[1]:
        out.append("gap")
    elif ok and found.fix[2]:
        out.append("menu")
    if state in ("error", "look", "review", "check") or (ok and (found.check or page.own in ("look", "review"))):
        out.append("check")  # a page that is itself doubtful is one to look at, whatever else it misses
    if state in NOFETCH_STATES:
        out.append("nofetch")
    return tuple(out)


def verdict_words(found: Found | None) -> str:
    """A page's misses in one line, the page check's words (check_page.verdict_html)."""
    if found is None:
        return ""
    lost = [f"{word} {n} 個" for word, n in zip(("正文區", "附件區", "選單等"), found.fix, strict=True) if n]
    bits = ["沒抓到：" + " · ".join(lost)] if lost else []
    if found.check:
        bits.append(f"要查 {found.check} 個")
    if found.next:
        bits.append(f"第二輪 {found.next} 個")
    return "\n".join(bits)


def row_of(listing: Listing, found: dict[str, Found], gen: int, url: str) -> dict:
    """One page of a site's list, as the page draws it."""
    page = listing.pages[url]
    f = found.get(url)
    state = state_of(page, f)
    words, tone, rank = PILLS[state]
    tip = verdict_words(f) if state not in ("error", "wait") else ""
    if state == "error" and f is not None:
        tip = f.error or pagecheck.STATES.get(f.state, f.state)
    older = f is not None and f.gen < gen
    if older:
        tip = (tip + "\n" if tip else "") + "帳本改過，這是改之前的結果" + (f"（{when(f.at)} 核查的）" if f.at else "")
    return {"url": url, "name": listing.names.get(url) or page.path, "path": page.path, "state": state,
            "pill": words, "tone": tone, "rank": rank, "chips": chips_of(page, f, state), "tip": tip, "older": older,
            "lost": (f.fix[0] + f.fix[1]) if f is not None else 0, "site": page.site}


def _row_order(row: dict) -> tuple:
    return (row["rank"], -row["lost"], row["path"])


@dataclass
class SiteData:
    """A site's numbers, its merged misses and its pages in their order (cached per site until it changes)."""

    pages: int = 0
    checked: int = 0  # pages with a result, older ones too
    current: int = 0  # pages checked by the sweep now running (or the last)
    older: int = 0
    gap: int = 0
    menu_links: int = 0  # the menus' addresses and buttons not got (爬蟲可補 in the menus), each once
    check: int = 0  # 要查 pages
    nofetch: int = 0
    chips: dict[str, int] = field(default_factory=dict)
    misses: list[dict] = field(default_factory=list)  # the content and attachment areas' merged misses, list_order
    menu: list[dict] = field(default_factory=list)  # the menus' misses not got (menu_links of them), list_order
    cls: dict[str, int] = field(default_factory=dict)  # misses per class (pagecheck.CLASSES)
    cls_pages: dict[str, int] = field(default_factory=dict)  # per class: on how many of its pages they are
    errors: int = 0
    more: int = 0  # misses beyond KEEP on its pages: not in the merged lists, said under them
    rows: list[dict] = field(default_factory=list)


def problem_key(url: str, p: Problem) -> str:
    """What makes two misses one row of a merged list: an address is one thing wherever it is; a button by its words
    on its site; one with neither (a download button that is only an icon) is its own (11-16 evening: 「按鈕」 merged
    nine sites' downloads)."""
    return p.target or (f"{p.what}|{p.text}" if p.text else f"{p.what}|{url}#{p.n}")


def list_order(row: dict) -> tuple:
    """A merged list's order inside a class (owner 11-22): links to files first, then links to pages, images, and
    buttons last; within each, on more sites first, then on more pages (a template link on every page of one site
    never goes before a file missed on three sites)."""
    return (KIND_KEYS.index(row["kind"]), -row.get("sites", 1), -row["pages"], row["text"], row["key"])


def merge_problems(listing: Listing, found: dict[str, Found], urls: Iterable[str]) -> tuple[list[dict], dict[str, int]]:
    """A site's misses merged across its pages (design section 六 2): one row per target, in the best zone any page
    has it in (the content area first), the first page that has it there and its number on that page, its class and
    what it is, and on how many pages it is; (the rows in list_order, per class on how many pages a row of the content
    or attachment area is). A page that is itself not there (failed, deleted) and one not checked as a page add none."""
    acc: dict[str, dict] = {}
    merged: list[tuple[str, Found]] = []
    for url in urls:
        page, f = listing.pages[url], found.get(url)
        if f is None or f.state != "ok" or page.own in NOFETCH:
            continue
        merged.append((url, f))
        for p in f.problems:
            key = problem_key(url, p)
            row = acc.get(key)
            if row is None:
                acc[key] = row = {"key": key, "target": p.target, "text": p.text, "zone": p.zone, "bucket": p.bucket,
                                  "reason": p.reason, "what": p.what, "tip": p.tip, "page": url, "n": p.n, "pages": 0,
                                  "last": "", "cls": p.cls or pagecheck.miss_class(p.bucket, p.what)}
            elif pagecheck.ZONE_ORDER.index(p.zone) < pagecheck.ZONE_ORDER.index(row["zone"]):
                row.update(zone=p.zone, bucket=p.bucket, reason=p.reason, what=p.what, tip=p.tip, page=url, n=p.n,
                           cls=p.cls or pagecheck.miss_class(p.bucket, p.what))
            if not row["text"] and p.text:
                row["text"] = p.text
            if row["last"] != url:
                row["pages"] += 1
                row["last"] = url
    for row in acc.values():
        row["kind"] = pagecheck.miss_kind(row["what"], row["target"])
    pages: Counter = Counter()  # a page counts once per class whose row in the content or attachment area it has
    for url, f in merged:
        pages.update({row["cls"] for p in f.problems
                      if (row := acc[problem_key(url, p)])["zone"] in TOP_ZONES})
    return sorted(acc.values(), key=list_order), {k: pages[k] for k in CLASS_KEYS}


def site_data(listing: Listing, found: dict[str, Found], gen: int, key: str) -> SiteData:
    urls = listing.site_pages.get(key, [])
    rows = sorted((row_of(listing, found, gen, u) for u in urls), key=_row_order)
    out = SiteData(pages=len(urls), rows=rows)
    out.chips = {k: 0 for k, _ in CHIPS}
    for row in rows:
        f = found.get(row["url"])
        if f is not None:
            out.checked += 1
            out.current += f.gen == gen
            out.older += f.gen < gen
            out.errors += f.state == "error"
            out.more += f.more
        for chip in row["chips"]:
            out.chips[chip] += 1
    out.gap, out.check, out.nofetch = out.chips["gap"], out.chips["check"], out.chips["nofetch"]
    merged, out.cls_pages = merge_problems(listing, found, urls)
    out.misses = [r for r in merged if r["zone"] in TOP_ZONES]
    out.menu = [r for r in merged if r["zone"] not in TOP_ZONES and pagecheck.WHO.get(r["bucket"]) == "fix"]
    out.menu_links = len(out.menu)
    out.cls = {k: sum(r["cls"] == k for r in out.misses) for k in CLASS_KEYS}
    return out


def school_problems(sites: Iterable[tuple[Site, SiteData]], limit: int = TOP) -> dict:
    """The school's misses in the content and attachment areas merged across its sites (the entry's list, owner
    11-22): one row per address school-wide and per button on its site, with on how many pages and sites it is,
    opening a page of the site that has it most; per class (pagecheck.CLASSES) the first limit rows in list_order
    (rows), how many there are (n) and on how many pages (pages: each page once per class); and how many of the menus'
    addresses and buttons are not got, each once (menu)."""
    acc: dict[str, dict] = {}
    pages: Counter = Counter()
    menu: set[str] = set()
    for site, data in sites:
        pages.update(data.cls_pages)
        menu.update(r["target"] or f"{site.key}|{r['key']}" for r in data.menu)
        for r in data.misses:
            key = r["target"] or f"{site.key}|{r['key']}"  # the same words on two sites are two buttons
            row = acc.get(key)
            if row is None:
                acc[key] = row = {**r, "pages": 0, "sites": 0, "most": 0}
            row["pages"] += r["pages"]
            row["sites"] += 1
            if r["pages"] > row["most"]:
                row.update(page=r["page"], n=r["n"], zone=r["zone"], bucket=r["bucket"], reason=r["reason"],
                           what=r["what"], kind=r["kind"], cls=r["cls"], tip=r["tip"], site=site.key,
                           site_name=site.name, most=r["pages"])
            if not row["text"] and r["text"]:
                row["text"] = r["text"]
    by: dict[str, list[dict]] = {k: [] for k in CLASS_KEYS}
    for row in sorted(acc.values(), key=list_order):
        by[row["cls"] if row["cls"] in by else "check"].append(row)
    return {"rows": {k: v[:limit] for k, v in by.items()}, "n": {k: len(v) for k, v in by.items()},
            "pages": {k: pages[k] for k in CLASS_KEYS}, "menu": len(menu)}


def query_forms(q: str) -> list[str]:
    """What is typed, as norm compares it (Simplified on both sides: the owner types Simplified, the school's names
    are Traditional)."""
    return [f] if (f := norm(q)) else []


def _in_order(q: str, text: str) -> bool:
    """Whether text holds q's characters in q's order (軟體系 in 軟體工程與管理學系: a short name)."""
    it = iter(text)
    return all(ch in it for ch in q)


def site_score(site: Site, forms: list[str]) -> int:
    """2: a key holds what is typed; 1: a key holds its characters in order (Chinese only); 0: no match."""
    if any(f in k for f in forms for k in site.keys):
        return 2
    if any(not f.isascii() and len(f) > 1 and _in_order(f, k) for f in forms for k in site.keys):
        return 1
    return 0


# ---------------------------------------------------------------- the sweep

class Sweep:
    """The sweep of one archive, its results and the views the pages draw from them.

    archive: the crawler's archive folder (pagecheck.LEDGER in it); library: the content library's file, read for the
    pages' and the sites' names only (None: none); checker stands in for pagecheck.check in tests."""

    def __init__(self, archive: Path, library: Path | None = None, *, checker: Callable | None = None,
                 ops_db: Path | None = None, hold: Callable[[], bool] | None = None,
                 resweep_seconds: float = RESWEEP_SECONDS, quiet_seconds: float = QUIET_SECONDS,
                 watch_seconds: float = WATCH_SECONDS, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        """ops_db: the panel's writable file (nknu.ops.sweep_store; None: everything in memory, nothing kept across a
        restart); hold: whether the nightly update is fetching or exporting now (the watcher starts nothing then);
        wall: the time of day (the nights, when a page was checked)."""
        self.archive = Path(archive)
        self.library = Path(library) if library is not None else None
        self.checker = checker or pagecheck.check_verdict
        self.resweep_seconds = resweep_seconds
        self.quiet_seconds = quiet_seconds
        self.watch_seconds = watch_seconds
        self.clock = clock
        self.wall = wall
        self.hold = hold
        self.store = SweepStore(ops_db, forbidden=(self.archive,)) if ops_db is not None else None
        self.finished_at: float | None = None  # when the last sweep that checked every page ended (wall)
        self.watcher: threading.Thread | None = None
        self._buf_pages: list[tuple[str, int, float, bytes]] = []  # results not yet written (the sweep's thread's)
        self._buf_reads: list[tuple[str, str, str, bytes]] = []
        self.lock = threading.Lock()
        self.cache = pagecheck.LedgerCache(self.archive)  # the sweep's own, never the requests'
        self.listing: Listing | None = None
        self.found: dict[str, Found] = {}
        self.gen = 0
        self.stamp: tuple | None = None
        self.started = 0.0
        self.phase = "idle"  # idle | reading | checking | done | failed | closed
        self.failure = ""
        self.pending: dict[str, deque[str]] = {}
        self.order: list[str] = []
        self.first: list[str] = []  # the sites asked for, the latest first
        self.done: Counter = Counter()  # pages checked by this sweep, per site
        self.carried = 0  # listed pages whose result is an older sweep's
        self.ver: Counter = Counter()  # per site: changes whenever what the site's lists show changes
        self.cached: dict[str, tuple[int, int, SiteData]] = {}
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self.finished = threading.Event()
        self.held = False  # asked for while the nightly update was fetching: not started, the last results stay
        self.again = False  # the sweep running began with results to replace (a check again, not the first)
        self._load()

    @property
    def kept(self) -> SweepStore | None:
        """The file the sweep is kept in, None when there is none (or it cannot be written)."""
        return self.store if self.store is not None and self.store.enabled else None

    def _load(self) -> None:
        """What the file kept: the last sweep's listing and results, drawn at once (said to be older when that sweep
        did not end, or ended over another ledger or other rules, until ensure has them checked again). A file that
        cannot be read is said in the log and left: the sweep starts from nothing, as without one."""
        store = self.kept
        if store is None:
            return
        try:
            run, got = store.last_run(), store.listing()
            if run is None:
                return
            if got is None:  # the runs' numbers go on
                self.gen = int(run["run"])
                return
            dropped = store.forget_reads(read_rules())
            if dropped:
                log.info("网页核查：核查的程序改过，%d 页的读法作废，重读", dropped)
            listing = unpack_listing(got[1])
            found = {url: unpack_found(url, blob, gen, at) for url, gen, at, blob in store.pages()
                     if url in listing.pages}
            whole = store.last_finished()
        except Exception:  # a kept file that does not read never stops the panel
            log.exception("网页核查：纪录档读不了，从头核查")
            return
        with self.lock:
            self.listing, self.order, self.found, self.gen = listing, listing.order, found, int(run["run"])
            self.ver.update(listing.sites.keys() | listing.site_pages.keys())
            self.started = self.clock()
            self.finished_at = whole["finished"] if whole is not None else None
            if run["finished"] is not None:
                self.phase = "done"
                if run["rules"] == rules():  # else checked again (stamp None)
                    self.stamp = _stamp_of(run["stamp"])
            self.carried = sum(1 for f in found.values() if f.gen < self.gen)
        self.finished.set()
        log.info("网页核查：从纪录档读回 %d 页的结果（第 %d 次核查）", len(found), self.gen)
        self._watch()

    # ------------------------------------------------ running

    def _quiet(self, stamp: tuple) -> bool:
        """Whether the ledger's files have not changed for quiet_seconds (their own times: a restart knows it too)."""
        last = max((s[1] for s in stamp if s is not None), default=0) / 1e9
        return self.wall() - last >= self.quiet_seconds

    def _due_locked(self, now: float) -> bool:
        """Whether a sweep is to start now: the first ever at once; after one that could not read the ledger, a while
        later; after one over a ledger that has changed since (or by other rules: stamp None), once the ledger has
        been quiet for quiet_seconds, or resweep_seconds after the last began while it keeps changing."""
        if self.phase == "failed":
            return now - self.started >= RETRY_SECONDS
        if self.listing is None:
            return True
        stamp = self.cache.stamped()
        if self.phase == "done" and stamp == self.stamp:
            return False
        return self._quiet(stamp) or now - self.started >= self.resweep_seconds

    def ensure(self, hold: bool = False) -> None:
        """Start the sweep the first time 網頁核查 is asked for; sweep again when the ledger has changed since the
        last one read it, as _due_locked says (never while one runs); try again a while after a sweep that could not
        read the ledger. hold: the nightly update is fetching or exporting, so no sweep is started now (a sweep read
        the whole archive for about 17 minutes on the disk the crawler writes: opening a page at night must not start
        that; 2026-11-18, found when a look at 找文件 at 03:32 began one); one already running goes on, the results of
        the last one stay, and the pages say so (progress['held'])."""
        with self.lock:
            if self.phase in ("reading", "checking", "closed"):
                self.held = False
                return
            self.held = hold
            if hold:
                return
            now = self.clock()
            if not self._due_locked(now):
                return
            self.gen += 1
            self.phase, self.started, self.failure = "reading", now, ""
            self.finished.clear()
            self.thread = threading.Thread(target=self._run, args=(self.gen,), name="nknu-page-sweep", daemon=True)
            self.thread.start()

    def prioritize(self, site: str) -> None:
        """The site the owner opened is checked next (design section 六 4)."""
        if not site:
            return
        with self.lock:
            if site in self.first:
                self.first.remove(site)
            self.first.insert(0, site)
            del self.first[8:]

    def close(self, timeout: float = 30.0) -> None:
        """Stop sweeping (the app stopping): the thread ends after the page it is on, writing what it checked."""
        with self.lock:
            self.phase = "closed"
        self.stopping.set()
        for thread in (self.thread, self.watcher):
            if thread is not None and thread.is_alive():
                thread.join(timeout)
        self.cache.close_now()

    # ------------------------------------------------ the watcher (with a file only)

    def _watch(self) -> None:
        """Start the watcher once there is something to keep current (a listing) and a file to keep it in."""
        if self.kept is None or self.watch_seconds <= 0:
            return
        with self.lock:
            if self.watcher is not None or self.phase == "closed":
                return
            self.watcher = threading.Thread(target=self._watching, name="nknu-page-sweep-watch", daemon=True)
            self.watcher.start()

    def _watching(self) -> None:
        while not self.stopping.wait(self.watch_seconds):
            try:
                self.tick()
            except Exception:  # the watcher looks again next time
                log.exception("网页核查：看账本变了没有时出错")

    def tick(self) -> None:
        """One look of the watcher (every watch_seconds): a sweep started when it is due and the nightly update is not
        fetching (hold); tonight's totals kept when the results are whole and current and tonight has none yet (a
        night the ledger did not change keeps its record too). Never the first sweep: that waits for 網頁核查."""
        if self.listing is None or not (self.archive / pagecheck.LEDGER).exists():
            return  # nothing kept yet, or no ledger mounted: nothing to keep current (the pages say so)
        hold = False
        if self.hold is not None:
            try:
                hold = bool(self.hold())
            except Exception:  # the night cannot be read: as if not fetching (web.nightly_fetching)
                log.exception("网页核查：读不了每晚更新在不在跑")
        self.ensure(hold=hold)
        store = self.kept
        with self.lock:
            current = self.phase == "done" and self.stamp is not None and self.stamp == self.cache.stamped()
            finished_at = self.finished_at
        if store is not None and current and finished_at is not None and store.night(self.night()) is None:
            self._record_night(finished_at)

    # ------------------------------------------------ the nights

    def night(self) -> str:
        """The night it is now (nightly.night_of: the Taipei day twelve hours before), YYYY-MM-DD."""
        return night_of(datetime.fromtimestamp(self.wall(), pagecheck.TAIPEI)).isoformat()

    def _record_night(self, finished_at: float) -> bool:
        """Tonight's record of the school's totals (overview's: the three classes counted per address, on how many
        pages, the menus', the pages not got), from a sweep that checked every page; the night's last one stays."""
        store = self.kept
        if store is None:
            return False
        totals = self.overview()["totals"]
        if not totals or totals["checked"] < totals["pages"]:
            return False
        cls, pages = totals["cls"], totals["cls_pages"]
        rec = {"night": self.night(), "run": self.gen, "pages": totals["pages"], "checked": totals["checked"],
               "at": datetime.fromtimestamp(finished_at, pagecheck.TAIPEI).isoformat(timespec="seconds"),
               **{f"cls_{k}": cls.get(k, 0) for k in CLASS_KEYS}, **{f"pages_{k}": pages.get(k, 0) for k in CLASS_KEYS},
               "menu": totals["menu"], "nofetch": totals["nofetch"]}
        try:
            store.put_night(rec)
        except (OSError, sqlite3.Error):
            log.exception("网页核查：今晚的总数写不进纪录档")
            return False
        return True

    def history(self, days: int = NIGHTS_SHOWN) -> dict:
        """The school's totals of the last nights, for the entry's trend: {enabled, why, days (YYYY-MM-DD, oldest
        first), nights: one a day, None where none is kept}."""
        span = window(datetime.fromisoformat(self.night()).date(), days)
        store = self.kept
        if store is None:
            why = self.store.error if self.store is not None else "沒有設定紀錄檔（--ops-db）"
            return {"enabled": False, "why": why, "days": span, "nights": []}
        try:
            got = {r["night"]: r for r in store.nights(span[0], span[-1])}
        except (OSError, sqlite3.Error):
            log.exception("网页核查：读不了纪录档里的每晚总数")
            got = {}
        return {"enabled": True, "why": "", "days": span, "nights": [got.get(d) for d in span]}

    def wait(self, timeout: float | None = None) -> bool:
        """Until the sweep running ends (tests)."""
        return self.finished.wait(timeout)

    def _library(self) -> sqlite3.Connection | None:
        if self.library is None or not self.library.is_file():
            return None
        from nknu.retrieval.server import connect  # read-only, as every request opens it
        return connect(self.library)

    def _run(self, gen: int) -> None:
        try:
            library = self._library()
            try:
                with self.cache.use() as conn:
                    listing = read_listing(conn, library)
            finally:
                if library is not None:
                    library.close()
            # taken after the read: opening a ledger in WAL mode read-only can itself make its empty -wal
            stamp = self.cache.stamped()
        except Exception as exc:  # said on the page; tried again on a later request
            log.exception("网页核查的整站核查读不了账本")
            with self.lock:
                if self.phase != "closed":
                    self.phase, self.failure = "failed", f"{type(exc).__name__}: {exc}"[:300]
            self.finished.set()
            return
        with self.lock:
            if self.phase == "closed":
                self.finished.set()
                return
            self.stamp, self.listing, self.order = stamp, listing, listing.order
            self.pending = {k: deque(urls) for k, urls in listing.site_pages.items() if urls}
            self.done = Counter()
            self.carried = sum(1 for u in listing.pages if u in self.found)
            self.again = self.carried > 0
            self.ver.update(listing.sites.keys() | listing.site_pages.keys())
            self.cached.clear()
            self.phase = "checking"
        store = self.kept
        if store is not None:
            try:
                store.begin(gen, self.wall(), json.dumps(stamp), rules(), len(listing.pages), pack_listing(listing))
            except (OSError, sqlite3.Error):
                log.exception("网页核查：纪录档写不进去，这一次的结果只留在内存")
                store = None
        self._watch()
        try:
            while not self.stopping.is_set() and (chunk := self._next_chunk()):
                began = time.monotonic()
                reads = self._reads(store, chunk)
                before = {u: key for u, (key, _read) in reads.items()}
                kept = self._merge(gen, check_pages(chunk, self.archive, self.cache, self.checker, self.stopping,
                                                    reads))
                if store is not None:
                    self._keep(store, gen, kept, {u: kr for u, kr in reads.items() if before.get(u) != kr[0]})
                if self.cache.stamped() != stamp:  # the crawler is writing: it gets the disk half the time
                    self.stopping.wait(time.monotonic() - began)
        except Exception as exc:  # what was checked stays; said on the page
            log.exception("网页核查的整站核查停了")
            with self.lock:
                if self.phase != "closed":
                    self.phase, self.failure = "failed", f"{type(exc).__name__}: {exc}"[:300]
            self._flush(store)
            self.finished.set()
            return
        self._flush(store)
        with self.lock:
            whole = self.phase != "closed" and not self.stopping.is_set()
            if whole:
                self.found = {u: f for u, f in self.found.items() if u in listing.pages}
                self.carried = 0
                self.phase = "done"
                self.cached.clear()
                self.finished_at = self.wall()
            finished_at = self.finished_at
        if whole and store is not None and finished_at is not None:
            try:
                store.finish(gen, finished_at, set(listing.pages))
            except (OSError, sqlite3.Error):
                log.exception("网页核查：纪录档写不进这一次的结束")
            self._record_night(finished_at)
        self.finished.set()

    def _reads(self, store: SweepStore | None, urls: list[str]) -> dict[str, tuple[str, pagecheck.Read]]:
        """The kept reads of these pages ({} without a file: every page is read)."""
        if store is None:
            return {}
        try:
            return {u: (key, unpack_steps(blob)) for u, (key, blob) in store.reads(urls, read_rules()).items()}
        except Exception:  # a kept read that does not unpack: the pages are read again
            log.exception("网页核查：纪录档里的读法读不回来，这几页重读")
            return {}

    def _keep(self, store: SweepStore, gen: int, found: list[Found],
              reads: dict[str, tuple[str, pagecheck.Read]]) -> None:
        """Results and new reads to write, written FLUSH pages at a time (the sweep's thread only)."""
        self._buf_pages += [(f.url, gen, f.at, pack_found(f)) for f in found]
        self._buf_reads += [(u, key, read_rules(), pack_steps(read)) for u, (key, read) in reads.items()]
        if len(self._buf_pages) + len(self._buf_reads) >= FLUSH:
            self._flush(store)

    def _flush(self, store: SweepStore | None) -> None:
        pages, reads = self._buf_pages, self._buf_reads
        self._buf_pages, self._buf_reads = [], []
        if store is None:
            return
        try:
            store.put(pages, reads)
        except (OSError, sqlite3.Error):
            log.exception("网页核查：%d 页的结果写不进纪录档", len(pages))

    def _next_chunk(self) -> list[str]:
        """The next pages to check: of the site asked for last that has pages left, else in the sweep's order."""
        with self.lock:
            for key in (*self.first, *self.order):
                q = self.pending.get(key)
                if q:
                    chunk = [q.popleft() for _ in range(min(CHUNK, len(q)))]
                    if not q:
                        del self.pending[key]
                    return chunk
        return []

    def _merge(self, gen: int, results: list[Found]) -> list[Found]:
        """The results of a chunk in place of the older ones; the ones taken (to be written)."""
        taken = []
        at = self.wall()
        with self.lock:
            if gen != self.gen or self.listing is None:
                return taken
            for f in results:
                f.gen, f.at = gen, at
                old = self.found.get(f.url)
                if old is not None and old.gen < gen and old.state not in PASSING and f.state in PASSING:
                    pass  # a ledger busy for a moment (the crawler writing) never hides an older real result
                else:
                    if old is not None and old.gen < gen:
                        self.carried -= 1
                    self.found[f.url] = f
                    taken.append(f)
                page = self.listing.pages.get(f.url)
                if page is not None:
                    self.done[page.site] += 1
                    self.ver[page.site] += 1
        return taken

    # ------------------------------------------------ what the pages draw

    def _snap(self) -> tuple[Listing | None, dict[str, Found], int, dict]:
        """What the views draw from, copied at one moment: the listing, the results, the sweep's number, the
        progress (with each site's version, so a site's data is cached for the results it was made from)."""
        with self.lock:
            progress = self._progress_locked()
            progress["ver"] = dict(self.ver)
            return self.listing, dict(self.found), self.gen, progress

    def _progress_locked(self, site: str = "") -> dict:
        listing = self.listing
        total = len(listing.pages) if listing is not None else 0
        running = self.phase in ("reading", "checking")
        whole = self.phase == "done"  # a sweep that ended: every page has its result
        # stale: the results shown are of a ledger (or rules) that has changed since, and no sweep runs yet
        stale = (not running and listing is not None and self.phase in ("done", "idle")
                 and (self.stamp is None or self.stamp != self.cache.stamped()))
        out = {"phase": self.phase, "running": running, "done": total if whole else sum(self.done.values()),
               "total": total, "older": self.carried, "failure": self.failure, "ready": listing is not None,
               "held": self.held, "at": when(self.finished_at), "again": self.again and running,
               "kept": self.kept is not None, "stale": stale}
        if site:
            pages = len(listing.site_pages.get(site, [])) if listing is not None else 0
            out["site_total"] = pages
            out["site_done"] = pages if whole else min(self.done[site], pages)
            out["site_running"] = running and out["site_done"] < pages
        return out

    def progress(self, site: str = "") -> dict:
        """How far the sweep has come (overall; and of one site), whether it runs, whether older results show."""
        with self.lock:
            return self._progress_locked(site)

    def _site(self, listing: Listing, found: dict[str, Found], gen: int, key: str, ver: dict) -> SiteData:
        """A site's data for the snapshot (ver: the sites' versions when it was taken), made again only when the
        site's results changed since."""
        mine = ver.get(key, 0)
        with self.lock:
            hit = self.cached.get(key)
        if hit is not None and hit[0] == mine and hit[1] == gen:
            return hit[2]
        data = site_data(listing, found, gen, key)
        with self.lock:
            self.cached[key] = (mine, gen, data)
        return data

    @staticmethod
    def _sum(site: Site, data: SiteData) -> dict:
        """A site's numbers: its pages', and its misses' per class (cls, cls_pages, menu: what the tiles draw)."""
        return {"key": site.key, "name": site.name, "round1": site.round1, "pages": data.pages,
                "checked": data.checked, "current": data.current, "older": data.older, "gap": data.gap,
                "menu_links": data.menu_links, "check": data.check, "nofetch": data.nofetch, "cls": dict(data.cls),
                "cls_pages": dict(data.cls_pages), "menu": data.menu_links}

    def overview(self, sort: str = "") -> dict:
        """The entry's view (design section 六 1): the progress, the totals, the school's misses merged (TOP), every
        site with its deciding numbers, by sort (one of SORTS; else the content area's misses first), then the
        larger sites. The totals keep the pages' sums the other panels read (gap, check, nofetch…) and the school's
        misses counted once each (cls, cls_pages, menu: school_problems')."""
        listing, found, gen, progress = self._snap()
        sort = sort if sort in SORTS else "gap"
        if listing is None:
            return {"progress": progress, "totals": None, "sites": [], "top": None, "sort": sort,
                    "history": self.history()}
        ver = progress.pop("ver")
        pairs = [(s, self._site(listing, found, gen, k, ver)) for k, s in listing.sites.items()]
        sites = [self._sum(s, d) for s, d in pairs]
        sites.sort(key=lambda s: (-s[sort], -s["gap"], -s["pages"], s["name"]))
        totals = {k: sum(s[k] for s in sites) for k in ("pages", "checked", "gap", "menu_links", "check", "nofetch")}
        top = school_problems(pairs)
        totals.update(cls=top["n"], cls_pages=top["pages"], menu=top["menu"])
        return {"progress": progress, "totals": totals, "sites": sites, "top": top, "sort": sort,
                "history": self.history()}

    def find(self, q: str, limit: int = 30) -> dict:
        """Sites and pages for what is typed in the entry's box: a site by its names, its unit's short names, its
        address; a page by its name or a piece of its address; Simplified and Traditional alike."""
        forms = query_forms(q)
        listing, found, gen, progress = self._snap()
        ver = progress.pop("ver")
        out = {"q": q, "sites": [], "pages": [], "pages_total": 0, "progress": progress}
        if not forms or listing is None:
            return out
        scored = [(site_score(s, forms), s) for s in listing.sites.values()]
        sums = [(n, self._sum(s, self._site(listing, found, gen, s.key, ver))) for n, s in scored if n]
        sums.sort(key=lambda ns: (-ns[0], -ns[1]["gap"], -ns[1]["pages"], ns[1]["name"]))
        out["sites"] = [s for _n, s in sums]
        pages: list[tuple] = []
        for url, (name, addr) in listing.keys.items():
            score = 3 if any(name.startswith(f) for f in forms) else 2 if any(f in name for f in forms) else \
                1 if any(f in addr for f in forms) else 0
            if score:  # the better match first, then the page with problems, then the shorter name
                pages.append((-score, PILLS[state_of(listing.pages[url], found.get(url))][2], len(name), url))
        out["pages_total"] = len(pages)
        pages.sort()
        out["pages"] = [row_of(listing, found, gen, p[-1]) for p in pages[:limit]]
        names = {k: s.name for k, s in listing.sites.items()}
        for r in out["pages"]:
            r["site_name"] = names.get(r["site"], r["site"])
        return out

    def site_view(self, key: str, *, q: str = "", only: str = "", limit: int = 300) -> dict | None:
        """A site's view (design section 六 2): its numbers, its merged misses, its pages (problems first) filtered
        by a chip and by what is typed, the first limit of them; None for a site the listing does not have."""
        listing, found, gen, snap = self._snap()
        progress = self.progress(key)
        if listing is None:
            return {"site": None, "progress": progress, "ready": False}
        site = listing.sites.get(key)
        if site is None and key not in listing.site_pages:
            return None
        if site is None:
            site = Site(key, key, min(listing.site_pages[key], key=lambda u: (len(u), u)), False, (norm(key),))
        data = self._site(listing, found, gen, key, snap["ver"])
        progress["older"] = data.older  # this site's own older results, not the whole sweep's
        rows = data.rows
        if only in dict(CHIPS):
            rows = [r for r in rows if only in r["chips"]]
        forms = query_forms(q)
        if forms:
            rows = [r for r in rows if any(f in k for f in forms for k in listing.keys.get(r["url"], ()))]
        return {"site": {"key": site.key, "name": site.name, "home": site.home, "round1": site.round1},
                "sum": self._sum(site, data), "misses": data.misses, "menu": data.menu, "errors": data.errors,
                "more": data.more,
                "chips": data.chips, "rows": rows[:limit], "matched": len(rows), "q": q,
                "only": only if only in dict(CHIPS) else "", "progress": progress, "ready": True}

    def item_view(self, site: str, key: str, limit: int = 40) -> dict | None:
        """One row of a merged list opened in place (11-16 evening; owner: 「我点开 没抓到的 问题想要知道的时候他直接新开
        一个网页链到目标网站…这没办法在一个网页内解决吗？」): the miss as the first page that has it in its best zone lists
        it, and the pages that have it (site: that site's; '' for an address: every site's), the first limit of
        them with their number on the page; None when no checked page has it (any more)."""
        listing, found, _gen, _snap = self._snap()
        if listing is None or not key:
            return None
        keys = [site] if site else list(listing.site_pages)
        names = {k: s.name for k, s in listing.sites.items()}
        pages, best = [], None
        for k in keys:
            for url in listing.site_pages.get(k, []):
                page, f = listing.pages[url], found.get(url)
                if f is None or f.state != "ok" or page.own in NOFETCH:
                    continue
                p = next((p for p in f.problems if problem_key(url, p) == key), None)
                if p is None:
                    continue
                pages.append({"url": url, "name": listing.names.get(url) or page.path, "path": page.path, "n": p.n,
                              "zone": p.zone, "site": k, "site_name": names.get(k, k)})
                if best is None or pagecheck.ZONE_ORDER.index(p.zone) < pagecheck.ZONE_ORDER.index(best.zone):
                    best = p
        if best is None:
            return None
        pages.sort(key=lambda r: (pagecheck.ZONE_ORDER.index(r["zone"]), r["site_name"], r["path"]))
        item = {"key": key, "target": best.target, "text": best.text, "zone": best.zone, "bucket": best.bucket,
                "reason": best.reason, "what": best.what, "tip": best.tip,
                "cls": best.cls or pagecheck.miss_class(best.bucket, best.what),
                "kind": pagecheck.miss_kind(best.what, best.target)}
        return {"item": item, "pages": pages[:limit], "total": len(pages), "sites": len({r["site"] for r in pages}),
                "site": site}

    def site_key(self, ref: str) -> str:
        """The listed site whose key is what the box was given (a site row shows its key: pasted back, it opens the
        site's page, not a check of an address the ledger may not have), else ''."""
        listing = self.listing
        if listing is None:
            return ""
        key = re.sub(r"^https?://", "", (ref or "").strip(), flags=re.IGNORECASE).rstrip("/")
        return key if key in listing.sites else ""

    def crumb(self, site: str, url: str) -> dict | None:
        """The checked page's top line (design section 六 3): its site and name, and the next page of the site whose
        content area misses something (None: not a page the sweep has read yet, or of a site it does not list, such as
        a link of the next round followed from the frame: the line would point to a site page that has nothing)."""
        listing, found, gen, snap = self._snap()
        if listing is None or not site or (site not in listing.sites and site not in listing.site_pages):
            return None
        known = listing.sites.get(site)
        out = {"site": site, "site_name": known.name if known is not None else site,
               "name": listing.names.get(url, ""), "next": "", "next_name": ""}
        if site not in listing.site_pages:
            return out
        gaps = [r for r in self._site(listing, found, gen, site, snap["ver"]).rows if "gap" in r["chips"]]
        at = next((i for i, r in enumerate(gaps) if r["url"] == url), -1)
        if at + 1 < len(gaps):
            out["next"], out["next_name"] = gaps[at + 1]["url"], gaps[at + 1]["name"]
        return out
