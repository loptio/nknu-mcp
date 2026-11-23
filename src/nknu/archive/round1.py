"""Round 1's list and its verdicts (round-1 design §4.1–4.4, scope record 二·2): which URLs this round fetches, which
wait for round 2, and which site each row counts towards.

The list is archive_round1.json in the configuration directory (config/, or NKNU_CONFIG_DIR as settings.py reads it),
the only one (seed, crawler, dashboard and acceptance all read it): 107 sites
(全校重要 34, 本系重要 73; ksped by its /resource/ path, dataopen2search by its lost-and-found and course-table pages),
SSO's attachment service as a resource rather than a site, and the other keys for round 2: 5 JavaScript sites the owner
set aside (js_site), the school's own units on off-campus sites (offsite_unit: the record's 附表一 and the two centres
of record 四, by their paths or a parameter, or one page only: leaf), and the others (other_site). Next to it: the 4
pages the owner opened (7 URLs: extra seeds, tier 1), the 15 login URLs inside round-1 sites that get one plain GET
each (kind0='login_check'), and the off-campus pages round two fetches one page of (round2_pages, with their batch).

The verdict is layer 2 of three (§4.3): scope.judge() first (its out:<rule> goes to the link table only), then
verdict(): r1 (the key is a round-1 site, inside its paths), resource (a part of the round-1 page that uses it: an
attachment on any host, off campus too, the file only (the owner, 10-26, record 五·15), an image on any host, a school
frame, a frame of a school unit's own site off campus (offsite_unit: one layer, gap ⑫ of 11-08), a Google Sites embed
box, a Google form, anything of the attachment service, a school script it loads, a tab
its platform rule presses, a short link it names: one GET to learn where it leads, admit()), or round 2 (a url row
with state 'later' and reason round2:<why>: js_site, other_site, offsite_unit by the list, and off campus offsite or
offsite_file, one layer only: leaf=1). The list comes before the question whether a URL is the school's: a unit's site
off campus is its unit's, by the list. An off-campus frame the page hides (a tracker's) is a link only (out:not_content). Layer 3, a
seed that redirects to another site, is Plan.adopt(). owner_site: an r1 row's own site, a resource's using page's owner
(the first page found counts, as for tier and depth), a round-2 row's own key.

admit() is the one way in (review 10-26 A2): wrappers off, short links held for one GET (short_answer() reads what it
says) unless a rule leaves their own address out, Google's addresses to the Google step (unhandled:google while there
is none), then place(); whoever brings an address calls it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from nknu.archive import links, outcome, scope
from nknu.settings import load_settings

PLAN_NAME = "archive_round1.json"
TIER_OF_IMP = {"全校重要": 0, "本系重要": 1}
EXTRA_TIER = 1  # the pages the owner opened (design §4.2)
ROUND2_TIER = 2
# a round-two site a batch crawls now (Site.now) is as important as a department's own site unless its imp says
# (design 2026-11-17-round2-batch2 §三.1: 「判法上当成第一轮的「本系重要」网站」)
NOW_IMP = "本系重要"
CAP_KINDS = ("page", "file", "image")  # a site's own caps (Site.caps), in this order
ROUNDS = ("1", "resource", "2")
# offsite_unit: the school's own unit on an off-campus site (record 三·2, 附表一; record 四's two centres)
ROUND2_WHY = ("js_site", "other_site", "offsite_unit")
# jscheck_redirect: where the browser stopped when jscheck opened a page (design §8.4), for round two
ROUND2_REASONS = frozenset({"round2:js_site", "round2:other_site", "round2:offsite_unit", "round2:offsite",
                            "round2:offsite_file", "round2:jscheck_redirect"})
# how the page uses the URL: a link (<a>, <area>, refresh), an image, a frame/embed, a frame it hides
# (links.Frame.hidden: width or height 0, display:none …): a school one is a frame all the same, an off-campus one a
# tracker's, the link only (record 一·1); a script it loads (<script src>: a school site's .js is read for its clicks,
# design §4.4); a tab or button pressed by its platform rule (the GET it sends, design §7.4)
HOW = ("link", "image", "frame", "hidden", "script", "click")
# Google Sites' embedded boxes (HTML and gadgets) are the page's own content (design §4.4)
_GOOGLE_EMBED = ("-atari-embeds.googleusercontent.com",)
# never a place to look for links as a page: what a tab or an embed box brings counts on the page that uses it (§4.4)
_LEAF_KINDS = frozenset({"file", "image", "form", "script", "click", "embed"})
LEAVES = (0, 1)


@dataclass(frozen=True)
class Site:
    site: str  # the key (scope.site_key of its home)
    round: str  # "1", "resource" or "2"
    imp: str | None = None
    name: str = ""
    why: str = ""  # round 2: js_site, other_site or offsite_unit
    platform: str = ""
    home: str | None = None
    # the part of the key this entry is: under these paths (lowercase; whole segments), or with leaf=1 those pages
    # only; for round 1 the rest of the key is round 2, for offsite_unit it is not the unit's
    paths: tuple[str, ...] = ()
    params: tuple[tuple[str, str], ...] = ()  # (name lowercased, value): every one must match (cirn.moe's sid=1219)
    leaf: int = 0  # 1: those pages only (附表一's 「该页」「这两页」); round two never follows from them
    seeds: tuple[str, ...] = ()  # where round two starts: its home, or each address of a merged key
    # round two's batch that crawls this site now (design 2026-11-17-round2-batch2 §三.1: the batch's name, its date):
    # the site is placed as this round's (this_round, tier 本系重要 unless its imp says) while it carries one
    now: str = ""
    caps: tuple[int, int, int] | None = None  # its own caps (pages, files, images), in place of a round-one site's
    # paths of its own it does not follow (lowercase, by whole segments): an address under one is left out, out:skip_path
    # (11-17, 爬虫judge's choice B for the gender institute's old Wix blog: its tag lists, every post they list in the
    # site's own sitemap); only as the list names them for a site
    skip: tuple[str, ...] = ()
    # the name of its own a skip path carries, (path as in skip, name): the export writes that rule's rows as
    # out:skip_path:<name> (11-19: the content stage lets the documents of one named rule leave its library, the
    # teachers' works fetched before their rule came, and holds a swap over any other rule's)
    skip_names: tuple[tuple[str, str], ...] = ()
    # every address of the site with a query not followed either (11-17, GitHub: 「只抓社团首页和各仓库首页」, its tabs
    # and filters ?tab=, ?q= aside)
    skip_query: bool = False
    # links it does not follow by the page they stand on: pairs of paths (lowercase, whole segments, as skip), a link
    # on a page of the site under the first to an address under the second is left out, out:skip_path (11-18, course
    # schedules design §八: a course outline links the same department's outlines of the year before, whose pages link
    # the year before that; the outlines a class's table names are followed as before)
    skip_from: tuple[tuple[str, str], ...] = ()
    # pages of its own whose Google files (Drive, Docs) it does not fetch, (the page as a skip path writes one, a
    # name): a Drive file a page under the path names is left out, out:skip_path, exported under the name as a named
    # skip path's rows are (11-19: 工教系's seminar proceedings, a whole volume of papers a year on its 學術活動
    # pages, /?p=acadact; the page, its form, its calendar and the files on the site's own host are as before)
    skip_google: tuple[tuple[str, str], ...] = ()
    # the kinds (CAP_KINDS) whose rows past this site's own cap are not fetched: skipped, out:cap_<kind>, a rule,
    # where another site's are held for a person (11-19: 产业教育中心's Google Sites pages carry thousands of activity
    # photographs past its picture cap, which nobody waits for)
    cap_out: tuple[str, ...] = ()
    # how often its home and the pages its home links are read again once its crawl is done: weekly or monthly, on
    # the night update.REVISIT_WEEKDAY names (11-19: the off-campus unit sites, one layer, about 500 requests a week
    # where reading each whole would be 2,000; 性别所's old Wix site, moved to a new one, monthly). A site with none
    # is under the nightly update or read once
    revisit: str = ""

    def skips(self, url: str, found_on: str | None = None) -> bool:
        """Whether the URL is one the list says the site does not follow: its path one of skip or under one (whole
        segments, a trailing slash aside; a segment * stands for any one segment: /org/*/* is every page below a
        repository's home; a path written with a ? at its end only when the address carries a query: the page itself
        stays; with text after the ? only when the query starts with it, any case: /previewpdf.aspx?name=%5cfolder
        leaves that folder's files and follows the page's others), or, with skip_query, any query; or, found_on being
        the page the link stands on, a link skip_from names (skips_from)."""
        if found_on is not None and self.skips_from(url, found_on):
            return True
        if not self.skip and not self.skip_query:
            return False
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if self.skip_query and parts.query:
            return True
        return self._skip_path(parts) is not None

    def _skip_path(self, parts, rules: tuple[str, ...] | None = None) -> str | None:
        """The skip path (of these rules; the site's skip when none is given) that names the address (its parts), as
        the list writes it; None when none does."""
        segs = _segments(parts.path)
        for rule in self.skip if rules is None else rules:
            # a ? in the rule: only with a query (11-18: the course query's teacher and room tables, ?cDES=…), one
            # that starts with the rule's text after it (11-19: the teachers' works among previewpdf.aspx's files)
            b, mark, start = rule.partition("?")
            if mark and not (parts.query and parts.query.lower().startswith(start)):
                continue
            if _under(segs, b):
                return rule
        return None

    def skip_name(self, url: str) -> str:
        """The name of its own the skip path that names the address carries (skip_names); '' when it has none, or no
        skip path names the address."""
        try:
            rule = self._skip_path(urlsplit(url)) if self.skip_names else None
        except ValueError:
            return ""
        return dict(self.skip_names).get(rule, "") if rule is not None else ""

    def skip_google_name(self, found_on: str | None) -> str:
        """The name of the skip_google rule the page found_on falls under: its Google files are not fetched. '' for
        a page under none, or one that is not the site's own."""
        if not self.skip_google or not found_on:
            return ""
        try:
            if scope.site_key(found_on) != self.site:
                return ""
            rule = self._skip_path(urlsplit(found_on), tuple(p for p, _name in self.skip_google))
        except ValueError:
            return ""
        return dict(self.skip_google).get(rule, "") if rule is not None else ""

    def skips_from(self, url: str, found_on: str) -> bool:
        """Whether a link to url on the page found_on is one skip_from names: both the site's own addresses, the
        page's path under a pair's first path and the link's under its second."""
        if not self.skip_from:
            return False
        try:
            if scope.site_key(url) != self.site or scope.site_key(found_on) != self.site:
                return False
            to, on = _segments(urlsplit(url).path), _segments(urlsplit(found_on).path)
        except ValueError:
            return False
        return any(_under(on, a) and _under(to, b) for a, b in self.skip_from)

    @property
    def this_round(self) -> bool:
        """Crawled now: a round-one site, or a round-two site a batch carries (now)."""
        return self.round == "1" or (self.round == "2" and bool(self.now))

    @property
    def tier(self) -> int:
        if self.round == "1":
            return TIER_OF_IMP[self.imp]
        if self.this_round:
            return TIER_OF_IMP.get(self.imp, TIER_OF_IMP[NOW_IMP])
        return ROUND2_TIER

    def holds(self, url: str) -> bool:
        """Whether the URL is inside this entry's part of its key (design §15.1). Every parameter in params must be in
        the URL (its name in any case) with that value, each time it is written. Paths by whole segments, a trailing
        slash aside: with leaf=1 the page itself only, else the path or anything under it (/g/nknutc holds
        /g/nknutc/c/x, never /g/nknutc2). No paths: anywhere."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if self.params:
            got: dict[str, list[str]] = {}
            for k, v in parse_qsl(parts.query, keep_blank_values=True):
                got.setdefault(k.lower(), []).append(v.strip())
            if not all(got.get(name) and all(v == value for v in got[name]) for name, value in self.params):
                return False
        if not self.paths:
            return True
        path = parts.path.lower().rstrip("/")
        for p in self.paths:
            base = p.rstrip("/")
            if path == base or (not self.leaf and path.startswith(base + "/")):
                return True
        return False


@dataclass(frozen=True)
class Placement:
    # r1, resource, round2, out, or unhandled: an address nobody may yet say anything of (a Google address with no
    # Google step to read it, admit()): no url row, the page_res entry says unhandled:<why> (need must, 要人看)
    verdict: str
    reason: str | None  # round2:<why>, out:<rule> or unhandled:<why>; None for r1 and resource
    owner_site: str | None
    tier: int | None
    leaf: int  # 1: fetched, never looked into for links (round two: that page only)
    # page, file, image, form, script, click, embed, shortlink (one GET to learn where it leads, admit()), or what the
    # Google step makes of an address (drive, calendar): what the page expects to get
    kind0: str

    @property
    def state(self) -> str | None:
        """The url row's state: pending (this round), later (round 2), or no row at all (out:*, unhandled:*: the link
        table, and the page_res entry of the page that uses it)."""
        return {"r1": "pending", "resource": "pending", "round2": "later"}.get(self.verdict)

    @property
    def row(self) -> bool:
        return self.state is not None


class Plan:
    """The list as loaded, plus the sites seed redirects brought in (adopt): one per crawl, never shared."""

    def __init__(self, sites: Mapping[str, Site], extra: tuple[str, ...], login_checks: tuple[str, ...],
                 sha256: str, round2_pages: tuple[tuple[str, str], ...] = ()) -> None:
        self.sites = dict(sites)
        self.extra = extra
        self.login_checks = login_checks
        self.sha256 = sha256
        self.round2_pages = round2_pages  # (url, batch): off-campus pages round two fetches one page of (§4.1)
        self._extra = frozenset(_schemeless(u) for u in extra)
        self._owner: dict[str, Site] = {}  # adopted key -> the round-1 site it stands for

    def site(self, key: str) -> Site | None:
        return self._owner.get(key) or self.sites.get(key)

    def is_extra(self, url: str) -> bool:
        return _schemeless(url) in self._extra

    def adopt(self, url: str, owner: str, seed: str | None = None) -> bool:
        """A seed of round-1 site `owner` (seed: the URL that redirected; the site's home if not given) redirected to
        `url` on another site: that site has moved, so its key is the same important site from now on (design §4.3
        layer 3, crawler.py's seed-redirect rule). A school URL, or off campus the same unit's site only (_moved_to).
        Never a key the list already places in round 1, a JavaScript site (owner, 10-25 15:33), nor the attachment
        service. Whether it was adopted: when not, the crawler notes the redirect for a person to look at."""
        key, src = scope.site_key(url), self.sites.get(owner)
        if src is None or not src.this_round or key in self._owner:
            return False
        if not scope.school_url(url) and not _moved_to(seed or src.home or "", url, key):
            return False
        known = self.sites.get(key)
        if known is not None and (known.round != "2" or known.why == "js_site"):
            return False
        self._owner[key] = Site(site=src.site, round=src.round, imp=src.imp, name=src.name, platform=src.platform,
                                home=src.home, now=src.now, caps=src.caps)
        return True


def _moved_to(seed: str, url: str, key: str) -> bool:
    """Whether a seed's redirect off campus still leads to the same unit's site: one site on a shared host (a Google
    Site, a Wix site: its key is not its host), or a redirect that keeps the site (scope.redirect_keeps_site: a short
    link, the same name with or without www.), never a whole big platform, government or other school's host
    (scope.NOT_A_SITE, INSTITUTIONS). A vendor's home page or a parked domain is not the unit's site: the 10-24
    crawler adopted only what judge() let in, and an off-campus site would be crawled 4,000 pages deep."""
    host = (urlsplit(url).hostname or "").lower()
    if key != host:
        return True
    if scope.NOT_A_SITE.search(host) or scope.INSTITUTIONS.search(host):
        return False
    return scope.redirect_keeps_site(seed, url)


def _schemeless(url: str) -> str:
    """http and https are one page here: the crawler's scheme switch (design §6.6) moves a page between them."""
    return url.split("://", 1)[-1]


def plan_path() -> Path:
    """archive_round1.json in the configuration directory (NKNU_CONFIG_DIR, else the repository's config/)."""
    return load_settings().config_dir / PLAN_NAME


def load(path: Path | None = None) -> Plan:
    """The list, checked: one entry per key, a known round, round 1 only for important sites with a home."""
    path = path or plan_path()
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    sites: dict[str, Site] = {}
    for entry in data["sites"]:
        site = parse_site(entry)
        if site.site in sites:
            raise ValueError(f"{path.name}: {site.site} is listed twice")
        sites[site.site] = site
    extra = tuple(e["url"] for e in data.get("extra", ()))
    checks = tuple(e["url"] for e in data.get("login_check", ()))
    pages = []
    for e in data.get("round2_pages", ()):
        if not e.get("url") or not e.get("batch"):
            raise ValueError(f"{path.name}: a round-2 page has its url and its batch ({e!r})")
        pages.append((e["url"], e["batch"]))
    if len({u for u, _b in pages}) != len(pages):
        raise ValueError(f"{path.name}: a round-2 page is listed twice")
    return Plan(sites, extra, checks, hashlib.sha256(raw).hexdigest(), tuple(pages))


def _segments(path: str) -> list[str]:
    return [x for x in path.lower().split("/") if x]


def _under(segs: list[str], path: str) -> bool:
    """Whether a path's segments are the path of a skip rule or under it (whole segments; * is any one segment)."""
    want = [x for x in path.split("/") if x]
    return len(segs) >= len(want) and all(w in ("*", x) for w, x in zip(want, segs))


def _skip_names(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """An entry's skip_names: a mapping of skip paths of the entry (as it writes them) to a name of small letters
    and underscores."""
    got = entry.get("skip_names") or {}
    skip = {p.lower() for p in entry.get("skip") or ()}
    if not isinstance(got, Mapping) or not all(
            isinstance(k, str) and k.lower() in skip and isinstance(v, str) and re.fullmatch(r"[a-z][a-z_]*", v)
            for k, v in got.items()):
        raise ValueError(f"{entry.get('site')}: skip_names maps skip paths of the entry to names (a-z and _)")
    return tuple((k.lower(), v) for k, v in got.items())


def _skip_google(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """An entry's skip_google: a mapping of pages (a path as skip writes one) to a name of small letters and
    underscores."""
    got = entry.get("skip_google") or {}
    if not isinstance(got, Mapping) or not all(
            isinstance(k, str) and k.startswith("/") and isinstance(v, str) and re.fullmatch(r"[a-z][a-z_]*", v)
            for k, v in got.items()):
        raise ValueError(f"{entry.get('site')}: skip_google maps pages (paths) to names (a-z and _)")
    return tuple((k.lower(), v) for k, v in got.items())


def _cap_out(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """An entry's cap_out: a list of kinds (CAP_KINDS), each once."""
    got = entry.get("cap_out") or ()
    if isinstance(got, str) or not all(k in CAP_KINDS for k in got) or len(set(got)) != len(got):
        raise ValueError(f"{entry.get('site')}: cap_out is a list of kinds among {CAP_KINDS}, each once")
    return tuple(got)


REVISITS = ("weekly", "monthly")


def _skip_from(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """An entry's skip_from: a list of pairs of paths (the page a link stands on, the address it names)."""
    got = entry.get("skip_from") or ()
    if isinstance(got, str) or not all(
            isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) and x for x in p) for p in got):
        raise ValueError(f"{entry.get('site')}: skip_from is a list of pairs of paths (the page, the address linked)")
    return tuple((a.lower(), b.lower()) for a, b in got)


def parse_site(entry: Mapping[str, Any]) -> Site:
    """One entry of the list, checked: a known round, round 1 only for important sites with a home, round 2 with its
    why (an off-campus unit with its home), params as text, leaf 0 or 1, and leaf=1 only for pages its seeds are."""
    rnd = str(entry.get("round"))
    if rnd not in ROUNDS:
        raise ValueError(f"{entry.get('site')}: round {entry.get('round')!r} is not one of {ROUNDS}")
    params = entry.get("params") or {}
    if not isinstance(params, Mapping) or not all(isinstance(v, str) and v for v in params.values()):
        raise ValueError(f"{entry.get('site')}: params is a mapping of names to values written as text")
    home = entry.get("home")
    caps = entry.get("caps")
    if caps is not None:
        if not isinstance(caps, Mapping) or set(caps) != set(CAP_KINDS) or not all(
                isinstance(caps[k], int) and not isinstance(caps[k], bool) and caps[k] > 0 for k in CAP_KINDS):
            raise ValueError(f"{entry.get('site')}: caps gives {CAP_KINDS} each a whole number above 0")
        caps = tuple(caps[k] for k in CAP_KINDS)
    now = entry.get("now") or ""
    if now and (rnd != "2" or not isinstance(now, str)):
        raise ValueError(f"{entry.get('site')}: now names the round-two batch that crawls a round-two site")
    site = Site(site=entry["site"], round=rnd, imp=entry.get("imp"), name=entry.get("name") or "",
                why=entry.get("why") or "", platform=entry.get("platform") or "", home=home,
                paths=tuple(p.lower() for p in entry.get("paths") or ()),
                params=tuple((k.lower(), v.strip()) for k, v in params.items()), leaf=entry.get("leaf", 0),
                seeds=tuple(entry.get("seeds") or ((home,) if home else ())), now=now, caps=caps,
                skip=tuple(p.lower() for p in entry.get("skip") or ()), skip_query=bool(entry.get("skip_query")),
                skip_from=_skip_from(entry), cap_out=_cap_out(entry), revisit=entry.get("revisit") or "",
                skip_names=_skip_names(entry), skip_google=_skip_google(entry))
    if site.revisit and (site.revisit not in REVISITS or not (rnd == "2" and now)):
        raise ValueError(f"{site.site}: revisit is one of {REVISITS}, for a round-two site a batch crawled (now)")
    if rnd == "1" and (site.imp not in TIER_OF_IMP or not site.home):
        raise ValueError(f"{site.site}: a round-1 site is 全校重要 or 本系重要 and has a home")
    if rnd == "2" and site.why not in ROUND2_WHY:
        raise ValueError(f"{site.site}: round 2 says why ({ROUND2_WHY})")
    if site.why == "offsite_unit" and not site.seeds:
        raise ValueError(f"{site.site}: a unit's site off campus has its home, where round two starts")
    if site.leaf not in LEAVES:
        raise ValueError(f"{site.site}: leaf is 0 or 1, not {site.leaf!r}")
    if site.leaf:
        seed_paths = {urlsplit(u).path.lower().rstrip("/") for u in site.seeds}
        if not site.paths or any(p.rstrip("/") not in seed_paths for p in site.paths):
            raise ValueError(f"{site.site}: leaf=1 is those pages only: each path the whole path of one of its seeds")
    return site


def place(plan: Plan, url: str, from_row: Mapping[str, Any] | None = None, how: str = "link",
          directory_sites=()) -> Placement:
    """Layers 1 and 2 of §4.3 for one canonical URL: scope.judge(), then verdict(). An out:<rule> makes no url row
    (the link table records it); off campus (scope.SOFT_OUT) is verdict()'s to place.

    A script a page loads (how='script', <script src>): a .js on a school host is judged as the page it would be
    without its extension (so a login's, a system's or a back office's script stays out) and goes to verdict(), which
    fetches it as the page's resource; any other script (a library's site, a framework handler that is not a .js) is
    out:media, as is a .js a page only links to (scope.judge() is not changed: design §4.5)."""
    if how == "script":
        return _place_script(plan, url, from_row, directory_sites)
    v = scope.judge(url, directory_sites)
    if not v.ok and v.reason not in scope.SOFT_OUT:
        return Placement("out", f"out:{v.reason}", None, None, 0, _kind0(url, how))
    return verdict(plan, url, from_row, how)


@dataclass(frozen=True)
class Admission:
    """What admit() makes of one address a page (or a seed list, a redirect, rejudge, the scope audit) brings."""

    link: str  # the canonical address, wrappers taken off (Google's /url, Facebook's l.php, SSO's ExternalLink)
    url: str  # the address of the url row: the link itself, or what the Google step makes of it (an API address)
    placement: Placement
    step: str  # place (scope.judge, then verdict), shortlink (one GET to learn where it leads), google


# the Google step (google_api.parse_link, design §7.6; a later phase): what a Drive, Docs or Calendar address becomes,
# given the url row of the page that uses it and how; None for one it leaves to scope.judge (a form's page)
GoogleStep = Callable[[str, Mapping[str, Any] | None, str], Admission | None]
_SHORT_HOW = ("link", "frame", "image")  # how a page may use a short link (a script, a tab or a hidden frame never)
GOOGLE_UNHANDLED = "unhandled:google"  # a Google address with no Google step to read it (design §7.6)
# a school error page (outcome.ERROR_SIGNATURES, design §10.1 「错误页本身不入队」, §18.1 「/errorpage/... 那几行本身不
# 算内容」): never a url row, whoever brings it (a seed list, a page's link or part, a short link's answer). The 10-26
# rehearsal found the 10-24 archive's url table and Common Crawl bringing 12 of them in as round-1 pages (news.nknu's
# /errorpage/pagenotfound.aspx, c.nknu's filenotfound.htm?aspxerrorpath= and default.aspx?aspxerrorpath=, ksped's),
# counted incomplete or thin; only a 3xx to one was kept out (crawler._redirect, which writes outcome.ERROR_LINK and
# judges the page that redirected). What one holds is the error, never the school's content: a seed or an ordinary link
# to it is left out (规则不抓, the link table and the seed report list it); a part of a page whose address is one (a
# picture, a frame, a short link's target) is a broken link of the school's (outcome._kept_out: bad_link, 要人看)
ERROR_PAGE = outcome.ERROR_OUT


def admit(plan: Plan, href: str, base: str | None = None, from_row: Mapping[str, Any] | None = None,
          how: str = "link", directory_sites=(), google: GoogleStep | None = None) -> Admission | None:
    """The one way in (review 10-26 A2): every address the crawl may queue goes through here, whoever brings it (a
    page's links and parts, a 3xx, a script redirect, a short link's answer, a seed list, the old archive's link
    table, rejudge, the scope audit), so that all of them are judged alike. href as written, joined to base. None for
    what is no http(s) address.

    1 the wrappers come off without a request (scope.canonical: Google's /url, Facebook's l.php, SSO's ExternalLink);
    2 a short link (scope.is_shortlink; a map's is not one) that scope.judge() lets through or finds off campus (a rule
      that leaves it out comes first: a short link whose own address is a login's, a captcha's or too long is never
      asked, second look F2) is held for one GET to learn where it leads: a resource of the round-1 page that uses it
      (kind0 shortlink, leaf 1), else left for round two; what it answers is read by short_answer(), and its target
      comes back here with the same page and how, judged as if the page had linked it directly (a form is the page's
      form, an attachment its attachment, a Drive file goes to the Google step);
    3 a Drive, Docs or Calendar address goes to the Google step, before scope.judge() and its length limit (design
      §4.5); one it declines is judged as before. With no step given (until google_api.py is plugged in) such an
      address, a form's page aside, is unhandled:google (要人看): never out:never_host, which would read 规则不抓 while
      the file it stands for was never read (design §7.6: 认不出的 Google 网址不落到 never_host; second look F2);
    4 place(): scope.judge(), then verdict().
    Before all of them, a school error page (outcome.error_signature) is left out (ERROR_PAGE), whoever brings it.
    A script a page loads and a tab its platform rule presses are placed directly (never a short link nor Google's)."""
    url = scope.canonical(href, base)
    if url is None:
        return None
    if outcome.error_signature(url) is not None:  # 0 a school error page: never queued (ERROR_PAGE)
        return Admission(url, url, Placement("out", ERROR_PAGE, None, None, 0, _kind0(url, how)), "place")
    if how in _SHORT_HOW and scope.is_shortlink(url):
        v = scope.judge(url, directory_sites)
        if v.ok or v.reason in scope.SOFT_OUT:
            return Admission(url, url, _shortlink(url, from_row), "shortlink")
    if how not in ("script", "click") and scope.is_google_link(url):
        if google is not None:
            got = google(url, from_row, how)
            if got is not None:
                if got.placement.kind0 == "drive" and _leaves_google(plan, from_row):
                    # a Drive file on a page whose Google files the list leaves (Site.skip_google): no row, no request
                    return Admission(got.link, got.url, Placement("out", "out:skip_path", None, None, 0, "drive"),
                                     "place")
                return got
        elif not scope.is_google_form(url):
            return Admission(url, url, Placement("unhandled", GOOGLE_UNHANDLED, None, None, 0, _kind0(url, how)),
                             "google")
    return Admission(url, url, place(plan, url, from_row, how, directory_sites), "place")


def _leaves_google(plan: Plan, from_row: Mapping[str, Any] | None) -> bool:
    """Whether the page a link stands on is one whose Google files its site leaves (Site.skip_google)."""
    page = _url_of(from_row)
    if page is None:
        return False
    try:
        owner = from_row["owner_site"]
    except (KeyError, IndexError, TypeError):
        return False
    site = plan.site(owner) if owner else None
    return site is not None and bool(site.skip_google_name(page))


def _shortlink(url: str, from_row: Mapping[str, Any] | None) -> Placement:
    """A short link on a round-1 page is that page's resource: one GET, then its target is the page's (design §7.7).
    One only a round-2 page (or nobody: an old list) names waits for round two, one layer out like any off-campus link."""
    if _round1_page(from_row):
        return Placement("resource", None, from_row["owner_site"], from_row["tier"], 1, "shortlink")
    return Placement("round2", "round2:offsite", scope.site_key(url), ROUND2_TIER, 1, "shortlink")


@dataclass(frozen=True)
class ShortAnswer:
    target: str | None  # where the short link leads (absolute): to admit() with the page that linked the short link
    how: str | None  # location, refresh, rule:<service>
    # why there is none: short_dead (404, 410: a dead short link, never 学校那边不存在), unhandled:shortlink (a person
    # looks); None with a target, or for an answer the general rules judge (403, 5xx …)
    reason: str | None


SHORT_DEAD, SHORT_UNREAD = "short_dead", "unhandled:shortlink"


def short_answer(url: str, status: int, location: str | None, text: str | None) -> ShortAnswer:
    """What one GET of a short link (never following its redirect) says, in three steps (review 10-26 A2): a 3xx's
    Location; a 200 web page's meta refresh, then the service's own writing (scope.SHORTLINK_RULES); else
    unhandled:shortlink. text: the 200 answer decoded, None when it is no web page. A 404 or 410 is a dead short link."""
    if 300 <= status <= 399:
        target = urljoin(url, location.strip()) if location and location.strip() else None
        return ShortAnswer(target, "location", None) if target else ShortAnswer(None, None, SHORT_UNREAD)
    if status in (404, 410):
        return ShortAnswer(None, None, SHORT_DEAD)
    if status == 200:
        found = links.shortlink_target(text, url) if text else None
        return ShortAnswer(*found, None) if found else ShortAnswer(None, None, SHORT_UNREAD)
    return ShortAnswer(None, None, None)


def _place_script(plan: Plan, url: str, from_row: Mapping[str, Any] | None, directory_sites) -> Placement:
    def out(reason: str) -> Placement:
        return Placement("out", f"out:{reason}", None, None, 0, "script")

    v = scope.judge(url, directory_sites)
    if not v.ok and v.reason not in (*scope.SOFT_OUT, "media"):
        return out(v.reason)  # a tracker's (not_content), a login's …: what judge() says of it
    if scope.extension(url) != "js" or not scope.school_url(url):
        return out("media")
    parts = urlsplit(url)
    bare = scope.judge(urlunsplit(parts._replace(path=parts.path[:-3])), directory_sites)
    if not bare.ok:
        return out("media" if bare.reason in scope.SOFT_OUT else bare.reason)
    return verdict(plan, url, from_row, "script")


def verdict(plan: Plan, url: str, from_row: Mapping[str, Any] | None = None, how: str = "link") -> Placement:
    """Where a URL scope.judge() lets through belongs (design §4.3 layer 2, in the order §15.1 fixes): r1, resource or
    round 2; out only for an off-campus frame the page hides, or a script no round-1 page loads. from_row: the url row
    of the page that uses it (owner_site, tier), or None (an old archive's row, Common Crawl, a seed list).

    1 an extra seed; 2 a script or a tab a round-1 page uses is that page's resource, whatever site it is on (a tab
    pressed with no round-1 page behind it is a crawler bug: ValueError); 3 a round-1 site's part; 4 an off-campus
    frame the page hides; 5 a round-1 page's other resources (a page of a unit's site off campus that it frames among
    them, one layer: gap ⑫ of 11-08); 5b a round-two site a batch crawls now, inside its reach (this round's, tier
    本系重要 unless its imp says); 6 a unit's site off campus, by the list (its paths and
    params; leaf as the list says), before 8, which would make it one page; 7 a JavaScript site; 8 off campus, one
    layer; 9 another school site."""
    key, kind0 = scope.site_key(url), _kind0(url, how)
    site = plan.site(key)
    leaf = int(kind0 in _LEAF_KINDS)
    if plan.is_extra(url):
        return Placement("r1", None, key, EXTRA_TIER, leaf, kind0)
    if how in ("script", "click"):
        if _round1_page(from_row):
            return Placement("resource", None, from_row["owner_site"], from_row["tier"], 1, kind0)
        if how == "click":
            raise ValueError(f"a tab pressed with no round-1 page behind it: {url}")
        return Placement("out", "out:media", None, None, 0, kind0)
    if site is not None and site.this_round and site.holds(url) and site.skips(url, _url_of(from_row)):
        # a path the list says the site does not follow, or a link it does not follow from the page it stands on
        return Placement("out", "out:skip_path", None, None, 0, kind0)
    if site is not None and site.round == "1" and site.holds(url):
        return Placement("r1", None, site.site, site.tier, leaf, kind0)
    if how == "hidden" and not scope.school_url(url):  # a tracker's frame: never a part the page lacks, nor round 2
        return Placement("out", "out:not_content", None, None, 0, kind0)
    if _round1_page(from_row):
        got = _resource(url, site, kind0, how, from_row)
        if got is not None:
            return got
    # 5b a round-two site a batch crawls now (Site.now), inside its reach: this round's, after a round-1 page's own
    # parts (a unit's Wix page 性别所 frames stays that page's content, gap ⑫ of 11-08); design 2026-11-17-round2-batch2
    # §三.1 【Claude 的执行办法: the order】
    if site is not None and site.round == "2" and site.now and site.holds(url):
        return Placement("r1", None, site.site, site.tier, max(leaf, site.leaf), kind0)
    if site is not None and site.round == "2" and site.why == "offsite_unit" and site.holds(url):
        return Placement("round2", "round2:offsite_unit", key, ROUND2_TIER, max(leaf, site.leaf), kind0)
    if site is not None and site.round == "2" and site.why == "js_site":
        return Placement("round2", "round2:js_site", key, ROUND2_TIER, leaf, kind0)
    if not scope.school_url(url):  # one layer out (record 一·1): fetched in round 2, never followed
        why = "round2:offsite_file" if kind0 == "file" else "round2:offsite"
        return Placement("round2", why, key, ROUND2_TIER, 1, kind0)
    return Placement("round2", "round2:other_site", key, ROUND2_TIER, leaf, kind0)


def _url_of(row: Mapping[str, Any] | None) -> str | None:
    """The address of the page a link stands on, when its row says it (a url row does; a bare mapping of a test or of
    an old archive's row may not)."""
    if row is None:
        return None
    try:
        return row["url"]
    except (KeyError, IndexError):
        return None


def came_by_skipped_link(site: Site, row: Mapping[str, Any], row_by_id) -> bool:
    """Whether a url row of the site, got before a skip_from rule came, is one the rule would never have brought: the
    link that first found it (found_from: the row of the page it stood on), or the one that found that page, and so
    on up, is a link the rule names (the older outline an outline page linked, the page it redirects to, the files and
    the yet older outlines that page links). row_by_id(id) gives a row or None. A row first found another way (a
    class's table, a seed) is not, whatever else links it."""
    if not site.skip_from:
        return False
    seen: set[Any] = set()
    while row is not None and row["id"] not in seen:
        seen.add(row["id"])
        up = row_by_id(row["found_from"]) if row["found_from"] is not None else None
        if up is None:
            return False
        if site.skips_from(row["url"], up["url"]):
            return True
        row = up
    return False


def _round1_page(row: Mapping[str, Any] | None) -> bool:
    """A page this round fetches: its tier is one of the round's (round-2 rows are tier 2, never fetched now)."""
    return row is not None and row["tier"] in TIER_OF_IMP.values()


def _resource(url: str, site: Site | None, kind0: str, how: str, src: Mapping[str, Any]) -> Placement | None:
    """A part of the round-1 page src (design §4.4): its attachments (on any host: the owner, 10-26, record 五·15:
    「抓吧」, the file only, never followed), images, frames, forms, and the attachment service; None for an ordinary
    link. Off-campus frames are noted for round 2, one layer only, but for a page of a school unit's own site off
    campus (the list's offsite_unit, inside its paths and params) that a school page src frames: src's content,
    fetched with it, one layer (leaf 1: its links stay round two's; what it frames in turn too, since it is no school
    page: review of 11-09). Gap ⑫ of 11-08: 性别所's pages frame their Wix pages (an iframe a script loads,
    data-src), where the content is; a hidden frame never gets here (verdict) 【Claude 的执行办法】."""
    school = scope.school_url(url)

    def part(leaf: int) -> Placement:
        return Placement("resource", None, src["owner_site"], src["tier"], leaf, kind0)

    if kind0 in ("image", "form", "file"):
        return part(1)
    if how in ("frame", "hidden"):  # off campus, a hidden frame never gets here (verdict)
        if school:
            return part(0)  # a frameset's pages hold the content (design §4.4, review: frames are not leaves)
        if (urlsplit(url).hostname or "").lower().endswith(_GOOGLE_EMBED):
            return part(1)
        if how == "frame" and site is not None and site.round == "2" and site.why == "offsite_unit" \
                and site.holds(url) and _school_page(src):
            return part(1)  # a school unit's own page off campus, framed in: the page's content (gap ⑫ of 11-08)
        return Placement("round2", "round2:offsite", scope.site_key(url), ROUND2_TIER, 1, kind0)
    if site is not None and site.round == "resource":
        return part(1)
    return None


def _school_page(row: Mapping[str, Any]) -> bool:
    """Whether the page a URL was found on is on a school host (scope.school_url of its url row's address), as every
    round-1 site's page is; a page a round-1 page brought in from off campus as its part (a unit's Wix page framed in,
    gap ⑫ of 11-08) is not. A row that names no address (one made by hand: a test's) counts as a school page."""
    try:
        url = row["url"]
    except (KeyError, IndexError):  # a dict without it; sqlite3.Row says IndexError
        return True
    return not url or scope.school_url(url)


def _kind0(url: str, how: str) -> str:
    """What the page expects to get (url.kind0, design §12): an image, a school script, a tab's answer, a Google Sites
    embed box, a Google form, else by the URL (page, file, image)."""
    if how not in HOW:
        raise ValueError(f"how is one of {HOW}, not {how!r}")
    if how in ("image", "script", "click"):
        return how
    if how in ("frame", "hidden") and (urlsplit(url).hostname or "").lower().endswith(_GOOGLE_EMBED):
        return "embed"
    return "form" if scope.is_google_form(url) else scope.kind_of(url)
