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
frame, a Google Sites embed box, a Google form, anything of the attachment service, a school script it loads, a tab
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from nknu.archive import links, scope
from nknu.settings import load_settings

PLAN_NAME = "archive_round1.json"
TIER_OF_IMP = {"全校重要": 0, "本系重要": 1}
EXTRA_TIER = 1  # the pages the owner opened (design §4.2)
ROUND2_TIER = 2
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

    @property
    def tier(self) -> int:
        return TIER_OF_IMP[self.imp] if self.round == "1" else ROUND2_TIER

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
        if src is None or src.round != "1" or key in self._owner:
            return False
        if not scope.school_url(url) and not _moved_to(seed or src.home or "", url, key):
            return False
        known = self.sites.get(key)
        if known is not None and (known.round != "2" or known.why == "js_site"):
            return False
        self._owner[key] = Site(site=src.site, round="1", imp=src.imp, name=src.name, platform=src.platform,
                                home=src.home)
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
    site = Site(site=entry["site"], round=rnd, imp=entry.get("imp"), name=entry.get("name") or "",
                why=entry.get("why") or "", platform=entry.get("platform") or "", home=home,
                paths=tuple(p.lower() for p in entry.get("paths") or ()),
                params=tuple((k.lower(), v.strip()) for k, v in params.items()), leaf=entry.get("leaf", 0),
                seeds=tuple(entry.get("seeds") or ((home,) if home else ())))
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
    A script a page loads and a tab its platform rule presses are placed directly (never a short link nor Google's)."""
    url = scope.canonical(href, base)
    if url is None:
        return None
    if how in _SHORT_HOW and scope.is_shortlink(url):
        v = scope.judge(url, directory_sites)
        if v.ok or v.reason in scope.SOFT_OUT:
            return Admission(url, url, _shortlink(url, from_row), "shortlink")
    if how not in ("script", "click") and scope.is_google_link(url):
        if google is not None:
            got = google(url, from_row, how)
            if got is not None:
                return got
        elif not scope.is_google_form(url):
            return Admission(url, url, Placement("unhandled", GOOGLE_UNHANDLED, None, None, 0, _kind0(url, how)),
                             "google")
    return Admission(url, url, place(plan, url, from_row, how, directory_sites), "place")


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
    frame the page hides; 5 a round-1 page's other resources; 6 a unit's site off campus, by the list (its paths and
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
    if site is not None and site.round == "1" and site.holds(url):
        return Placement("r1", None, site.site, site.tier, leaf, kind0)
    if how == "hidden" and not scope.school_url(url):  # a tracker's frame: never a part the page lacks, nor round 2
        return Placement("out", "out:not_content", None, None, 0, kind0)
    if _round1_page(from_row):
        got = _resource(url, site, kind0, how, from_row)
        if got is not None:
            return got
    if site is not None and site.round == "2" and site.why == "offsite_unit" and site.holds(url):
        return Placement("round2", "round2:offsite_unit", key, ROUND2_TIER, max(leaf, site.leaf), kind0)
    if site is not None and site.round == "2" and site.why == "js_site":
        return Placement("round2", "round2:js_site", key, ROUND2_TIER, leaf, kind0)
    if not scope.school_url(url):  # one layer out (record 一·1): fetched in round 2, never followed
        why = "round2:offsite_file" if kind0 == "file" else "round2:offsite"
        return Placement("round2", why, key, ROUND2_TIER, 1, kind0)
    return Placement("round2", "round2:other_site", key, ROUND2_TIER, leaf, kind0)


def _round1_page(row: Mapping[str, Any] | None) -> bool:
    """A page this round fetches: its tier is one of the round's (round-2 rows are tier 2, never fetched now)."""
    return row is not None and row["tier"] in TIER_OF_IMP.values()


def _resource(url: str, site: Site | None, kind0: str, how: str, src: Mapping[str, Any]) -> Placement | None:
    """A part of the round-1 page src (design §4.4): its attachments (on any host: the owner, 10-26, record 五·15:
    「抓吧」, the file only, never followed), images, frames, forms, and the attachment service; None for an ordinary
    link. Off-campus frames are noted for round 2, one layer only."""
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
        return Placement("round2", "round2:offsite", scope.site_key(url), ROUND2_TIER, 1, kind0)
    if site is not None and site.round == "resource":
        return part(1)
    return None


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
