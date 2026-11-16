"""網頁核查's whole sweep (design docs/superpowers/specs/2026-11-16-page-check-design.md section 六): every page of the
crawler's ledger checked once by the very function the panel uses for one page (nknu.ops.pagecheck.check), so the
list of sites, a site's list of pages and the page opened from them never disagree. Of each check only what the lists
need is kept (its state, how many it missed where, the misses themselves), in memory: every mount of the panel's
container is read-only, so nothing is written anywhere.

Owner 11-16, on the first version's one address box: 「为什么要我输入网址或文件编号？我怎么会记得网址或者文件编号呀？」.
Every way of doing it below is 【Claude】's (design section 六 says so).

How it runs (design section 六 4): one thread of the panel's process reads the ledger's pages, the sites and the
pages' names, then checks the pages a few at a time with its own read-only ledger connection, the site the owner opened
last first. One thread is enough: a check takes about 14 ms (the lab's ledger, 11-16), so the 48,000 pages take about
eleven minutes and a site opened is done within a minute or two (the design's three worker processes were dropped
11-16, with the owner's 「你太夸张了」 about the machinery around this change: a pool, its crashes and its hangs cost far
more code than the minutes they save 【Claude】). It starts with the first request of 網頁核查 and sweeps again when the
ledger has changed; the older results stay on the pages, said to be older, until each is replaced. A request never
waits on it: it copies what is there under a short lock and draws from the copy."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

from nknu.archive import scope
from nknu.ops import pagecheck
from nknu.ops.look import t2s, tw

log = logging.getLogger(__name__)

CHUNK = 20  # pages checked between two looks at which site was asked for: well under a second 【Claude】
RESWEEP_SECONDS = 600.0  # a ledger that keeps changing (the crawler writing) is swept again at most this often 【Claude】
RETRY_SECONDS = 30.0  # a sweep that could not read the ledger is tried again after this 【Claude】
KEEP = 300  # misses kept per page, as many as the page's own lists show (check_page.PROBLEMS_SHOWN) 【Claude】
ROUND_ONE = "1"  # site_plan.round of the first round's sites
NOFETCH = ("failed", "gone")  # a page's own bucket that says the page itself is not there
# a page's own buckets listed though no copy of it is stored: its failure, its deletion, its doubtful content is the
# finding. Not listed: an address that redirects (its landing is a page of its own), one a rule keeps out, one left to
# the next round with nothing stored. 【Claude】
UNSTORED_LISTED = ("failed", "gone", "look", "review")
NOT_LISTED = ("rule", "album")
PASSING = ("busy", "error")  # a check's states that say nothing of the page itself: an older result outlives them
# the entry's two lists of the school's misses (11-16 evening: 1,361 of the 1,406 pages whose content area misses
# something miss one and the same link, which the sites' numbers alone never said): the content and attachment areas
# only (a menu's miss is on every page of its site and would bury them), merged across sites, the most pages first 【Claude】
TOP = 8
TOP_ZONES = ("in", "files")
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


def compact(url: str, summary: dict) -> Found:
    """A check's summary as the lists keep it."""
    v = summary.get("verdict") or {}
    fix = v.get("fix") or {}
    found = summary.get("problems") or []
    kept = tuple(Problem(p.get("target") or "", p.get("text") or "", p.get("zone") or "out", p["bucket"],
                         p.get("reason") or "", p.get("what") or "link", int(p.get("n") or 0), p.get("tip") or "")
                 for p in found[:KEEP])
    return Found(url, summary.get("state") or "error", (fix.get("in", 0), fix.get("files", 0), fix.get("out", 0)),
                 int(v.get("check", 0)), int(v.get("next", 0)), kept, max(0, len(found) - KEEP))


def errored(url: str, words: str) -> Found:
    return Found(url, "error", error=words)


def check_pages(urls: Iterable[str], archive: Path, cache: pagecheck.LedgerCache | None,
                checker: Callable = pagecheck.check, stop: threading.Event | None = None) -> list[Found]:
    """Each page checked as the panel checks it (no library: a check of an address needs none), a failure kept as
    that page's error instead of ending the rest."""
    out = []
    for url in urls:
        if stop is not None and stop.is_set():
            break
        try:
            out.append(compact(url, checker(archive, url, library=None, ledgers=cache).summary))
        except Exception as exc:  # one odd page never stops the sweep: it is listed under 要查
            log.exception("核查出错：%s", url)
            out.append(errored(url, f"{type(exc).__name__}: {exc}"[:300]))
    return out


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
        tip = (tip + "\n" if tip else "") + "帳本改過，這是改之前的結果"
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
    menu_links: int = 0
    check: int = 0  # 要查 pages
    nofetch: int = 0
    chips: dict[str, int] = field(default_factory=dict)
    fix: list[dict] = field(default_factory=list)
    look: list[dict] = field(default_factory=list)
    errors: int = 0
    more: int = 0  # misses beyond KEEP on its pages: not in the merged lists, said under them
    rows: list[dict] = field(default_factory=list)


def problem_key(url: str, p: Problem) -> str:
    """What makes two misses one row of a merged list: an address is one thing wherever it is; a button by its words
    on its site; one with neither (a download button that is only an icon) is its own (11-16 evening: 「按鈕」 merged
    nine sites' downloads)."""
    return p.target or (f"{p.what}|{p.text}" if p.text else f"{p.what}|{url}#{p.n}")


def merge_problems(listing: Listing, found: dict[str, Found], urls: Iterable[str]) -> tuple[list[dict], list[dict]]:
    """A site's misses merged across its pages (design section 六 2): one row per target, in the best zone any page
    has it in (the content area first), the first page that has it there and its number on that page, and on how many
    pages it is; (爬蟲可補, 要查), each in the content area first, then by how many pages. A page that is itself not
    there (failed, deleted) and one not checked as a page add none."""
    acc: dict[str, dict] = {}
    for url in urls:
        page, f = listing.pages[url], found.get(url)
        if f is None or f.state != "ok" or page.own in NOFETCH:
            continue
        for p in f.problems:
            key = problem_key(url, p)
            row = acc.get(key)
            if row is None:
                acc[key] = row = {"key": key, "target": p.target, "text": p.text, "zone": p.zone, "bucket": p.bucket,
                                  "reason": p.reason, "what": p.what, "tip": p.tip, "page": url, "n": p.n, "pages": 0,
                                  "last": ""}
            elif pagecheck.ZONE_ORDER.index(p.zone) < pagecheck.ZONE_ORDER.index(row["zone"]):
                row.update(zone=p.zone, bucket=p.bucket, reason=p.reason, tip=p.tip, page=url, n=p.n)
            if not row["text"] and p.text:
                row["text"] = p.text
            if row["last"] != url:
                row["pages"] += 1
                row["last"] = url

    def order(r: dict) -> tuple:
        return (pagecheck.ZONE_ORDER.index(r["zone"]), pagecheck.PROBLEMS.index(r["bucket"]), -r["pages"], r["text"])

    fix = sorted((r for r in acc.values() if pagecheck.WHO.get(r["bucket"]) == "fix"), key=order)
    look = sorted((r for r in acc.values() if pagecheck.WHO.get(r["bucket"]) == "check"), key=order)
    return fix, look


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
    out.fix, out.look = merge_problems(listing, found, urls)
    out.menu_links = sum(r["zone"] == "out" for r in out.fix)
    return out


def school_problems(sites: Iterable[tuple[Site, SiteData]], limit: int = TOP) -> dict:
    """The school's misses in the content and attachment areas merged across its sites (the entry's two lists): one
    row per target with on how many pages and sites it is, opening a page of the site that has it most; the most
    pages first, the first limit of them, and how many there are in all (n_fix, n_look)."""
    out: dict = {}
    for who in ("fix", "look"):
        acc: dict[str, dict] = {}
        for site, data in sites:
            for r in getattr(data, who):
                if r["zone"] not in TOP_ZONES:
                    continue
                key = r["target"] or f"{site.key}|{r['key']}"  # the same words on two sites are two buttons
                row = acc.get(key)
                if row is None:
                    acc[key] = row = {**r, "pages": 0, "sites": 0, "most": 0}
                row["pages"] += r["pages"]
                row["sites"] += 1
                if r["pages"] > row["most"]:
                    row.update(page=r["page"], n=r["n"], zone=r["zone"], bucket=r["bucket"], reason=r["reason"],
                               tip=r["tip"], site=site.key, site_name=site.name, most=r["pages"])
                if not row["text"] and r["text"]:
                    row["text"] = r["text"]
        rows = sorted(acc.values(), key=lambda r: (-r["pages"], pagecheck.ZONE_ORDER.index(r["zone"]), r["text"]))
        out[who], out["n_" + who] = rows[:limit], len(rows)
    return out


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
                 resweep_seconds: float = RESWEEP_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.archive = Path(archive)
        self.library = Path(library) if library is not None else None
        self.checker = checker or pagecheck.check
        self.resweep_seconds = resweep_seconds
        self.clock = clock
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

    # ------------------------------------------------ running

    def ensure(self) -> None:
        """Start the sweep the first time 網頁核查 is asked for; sweep again when the ledger has changed since the
        last one started (at most every resweep_seconds, never while one runs); try again a while after a sweep
        that could not read the ledger."""
        with self.lock:
            if self.phase in ("reading", "checking", "closed"):
                return
            now = self.clock()
            if self.phase == "failed" and now - self.started < RETRY_SECONDS:
                return
            if self.phase == "done" and (now - self.started < self.resweep_seconds
                                         or self.cache.stamped() == self.stamp):
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
        """Stop sweeping (the app stopping): the thread ends after the page it is on."""
        with self.lock:
            self.phase = "closed"
        self.stopping.set()
        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self.cache.close_now()

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
            self.ver.update(listing.sites.keys() | listing.site_pages.keys())
            self.cached.clear()
            self.phase = "checking"
        try:
            while not self.stopping.is_set() and (chunk := self._next_chunk()):
                self._merge(gen, check_pages(chunk, self.archive, self.cache, self.checker, self.stopping))
        except Exception as exc:  # what was checked stays; said on the page
            log.exception("网页核查的整站核查停了")
            with self.lock:
                if self.phase != "closed":
                    self.phase, self.failure = "failed", f"{type(exc).__name__}: {exc}"[:300]
            self.finished.set()
            return
        with self.lock:
            if self.phase != "closed" and not self.stopping.is_set():
                self.found = {u: f for u, f in self.found.items() if u in listing.pages}
                self.carried = 0
                self.phase = "done"
                self.cached.clear()
        self.finished.set()

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

    def _merge(self, gen: int, results: list[Found]) -> None:
        with self.lock:
            if gen != self.gen or self.listing is None:
                return
            for f in results:
                f.gen = gen
                old = self.found.get(f.url)
                if old is not None and old.gen < gen and old.state not in PASSING and f.state in PASSING:
                    pass  # a ledger busy for a moment (the crawler writing) never hides an older real result
                else:
                    if old is not None and old.gen < gen:
                        self.carried -= 1
                    self.found[f.url] = f
                page = self.listing.pages.get(f.url)
                if page is not None:
                    self.done[page.site] += 1
                    self.ver[page.site] += 1

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
        out = {"phase": self.phase, "running": running, "done": total if whole else sum(self.done.values()),
               "total": total, "older": self.carried, "failure": self.failure, "ready": listing is not None}
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
        return {"key": site.key, "name": site.name, "round1": site.round1, "pages": data.pages,
                "checked": data.checked, "current": data.current, "older": data.older, "gap": data.gap,
                "menu_links": data.menu_links, "check": data.check, "nofetch": data.nofetch}

    def overview(self, sort: str = "") -> dict:
        """The entry's view (design section 六 1): the progress, the totals, the school's misses merged (TOP), every
        site with its deciding numbers, by sort (one of SORTS; else the content area's misses first), then the
        larger sites."""
        listing, found, gen, progress = self._snap()
        sort = sort if sort in SORTS else "gap"
        if listing is None:
            return {"progress": progress, "totals": None, "sites": [], "top": None, "sort": sort}
        ver = progress.pop("ver")
        pairs = [(s, self._site(listing, found, gen, k, ver)) for k, s in listing.sites.items()]
        sites = [self._sum(s, d) for s, d in pairs]
        sites.sort(key=lambda s: (-s[sort], -s["gap"], -s["pages"], s["name"]))
        totals = {k: sum(s[k] for s in sites) for k in ("pages", "checked", "gap", "menu_links", "check", "nofetch")}
        return {"progress": progress, "totals": totals, "sites": sites, "top": school_problems(pairs), "sort": sort}

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
                "sum": self._sum(site, data), "fix": data.fix, "look": data.look, "errors": data.errors,
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
                "reason": best.reason, "what": best.what, "tip": best.tip}
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
