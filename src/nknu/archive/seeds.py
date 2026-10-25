"""Where the archive crawl starts.

Round 1 (round-1 design §5; review 10-26 B3, B7, B9): seed_round1(), below. Its sources, each noted in seed_src (a URL
may have several): ① the list's round-1 sites (their homes), its extra pages and login re-checks; ⑧ the SSO
announcement centre's lists, 榮譽典範 and all categories included; ⑤ each c.nknu-template site's SiteMap.aspx; ⑥ the
WordPress sites' wp-sitemap.xml (the sitemaps robots.txt names are queued by the crawler as it reads them); ⑦ v1's
announcements, honour notices included (record 三·3); ② every row of the old archive's url table and ③ the targets of
its link table, judged again by today's rules through round1.admit() (an attachment counts for the page that linked
it; one step back in the queue for an old link: prio + 1); ④ Common Crawl's answered addresses (prio + 1); ⑨ round
two's starting points, each batch with the reason and leaf round1.verdict gives (the list's round-2 sites, its
round2_pages, the 52 「校务系统」 seeds that are no round-1 site's). The old archive is read only (mode=ro, immutable),
never changed; its hosts' Crawl-delay and robots.txt come along, and which of them answered it from off campus (B3).
The check: no site of the old archive starts with fewer rows than it had there, less what today's rules leave out
(record 二·8): exit code 2, with the rows that are missing. ⑩ (the 10-25 page-type test results) is pagetypes.py's,
a later step (design §17 step 8).

The 10-24 crawler's seeds (below the round-1 part, kept for its tests and the viewer), in the order the owner asked
for: by publishing institution.

Tier 0: the school's own directory pages (and the club roster 社團一覽, design §2): the off-campus sites they link to
directly are crawled too. Those pages carry an explicit marker (Seed.directory, url.directory in the frontier): being
tier 0 or a seed does not make a page a directory page, and a directory page lends neither its tier nor its name to the
pages it links to. Tier 1: administrative offices and their sections (教務、學務、總務、圖書館、
體育、國際…). Tier 2: colleges and departments. Tier 3: centres, programmes, labs, and the announcements v1 already
knows. Tier 4: everything else (student groups, systems, other). Inside a server's queue the lower tier goes first;
different servers run side by side.

Sources: the site probe (research/site_probe_2026-10-23/data.json, classes "direct" and "render": a JS page still has
links in its raw HTML), each c.nknu unit's SiteMap.aspx, the SSO announcement centre's seven list pages, and the
announcement URLs in the maintainer's v1 facts.db (read only).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from nknu.archive import scope
from nknu.settings import REPO_ROOT

if TYPE_CHECKING:
    from nknu.archive.crawler import Crawler

PROBE = REPO_ROOT / "research" / "site_probe_2026-10-23" / "data.json"
DIRECTORY = ["https://w3.nknu.edu.tw/", "https://w3.nknu.edu.tw/admin.html", "https://w3.nknu.edu.tw/edu_list.html",
             "https://w3.nknu.edu.tw/dept_website_list.html", "https://w3.nknu.edu.tw/center.html",
             "https://w3.nknu.edu.tw/sitemap.html", "https://w3.nknu.edu.tw/services.html"]
# 課外活動指導組's public club roster (design §2 「官方目录当起点」); the probe only has it as roster entries
CLUB_DIRECTORY = ("https://sso.nknu.edu.tw/OSA/StuClub.aspx", "課外活動指導組 社團一覽")
# design §5 ⑧: the seven categories, 榮譽典範 (GOOD) among them since record 三·3 (the owner 10-25: honour lists are
# collected whole), and the list of all categories, whose 「共 N 筆」 the categories' sum is checked against
SSO_LISTS = [f"https://sso.nknu.edu.tw/News/SubjectList.aspx?cat={c}"
             for c in ("SCHOOLADMIN", "ENTRANCEEXAM", "CAMPUS", "Lights", "LEARNING", "JOB", "GOOD")]
SSO_LISTS.append("https://sso.nknu.edu.tw/News/SubjectList.aspx")
TIER_OF_KIND = {"学校": 1, "行政单位": 1, "行政组别": 1, "学院": 2, "系所": 2, "中心": 3, "学程/计划": 3, "实验室": 3}


@dataclass(frozen=True)
class Seed:
    url: str
    unit: str
    tier: int
    via: str = "seed"
    directory: bool = False  # an official directory page (design §2)


def probe_seeds(path: Path = PROBE) -> list[Seed]:
    rows = json.loads(path.read_text(encoding="utf-8"))["sites"]
    out = []
    for r in rows:
        if r.get("c") not in ("direct", "render") or not r.get("u"):
            continue
        url = scope.canonical(r["u"])
        if url and scope.judge(url).ok:
            out.append(Seed(url, r.get("n") or "", TIER_OF_KIND.get(r.get("k") or "", 4)))
    return out


def sitemap_seeds(seeds: list[Seed]) -> list[Seed]:
    """Each c.nknu unit's SiteMap.aspx: the template's own full menu (research/site_inventory g1 §1), spelt from the
    seed's own first directory (the site key is lowercased) and then made canonical like every other URL."""
    out, seen = [], set()
    for s in seeds:
        key = scope.site_key(s.url)
        if key.startswith("c.nknu.edu.tw/") and key not in seen:
            seen.add(key)
            segment = next(x for x in scope.urlsplit(s.url).path.split("/") if x)
            url = scope.canonical(f"https://c.nknu.edu.tw/{segment}/SiteMap.aspx")
            if url:
                out.append(Seed(url, s.unit, s.tier))
    return out


def v1_known(facts_db: Path, judged: bool = True) -> list[Seed]:
    """Announcement pages v1 found through list paging (read only). Seeded so the archive holds them; whether link
    following also found them is the coverage check of the monitor. judged=False: every one, for round 1's seeds,
    which admit() judges and whose report lists what it left out (second review 10-26: they were left out unseen)."""
    if not facts_db.exists():
        return []
    conn = sqlite3.connect(f"file:{facts_db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT url, unit, title, category FROM doc WHERE withdrawn_at IS NULL").fetchall()
    finally:
        conn.close()
    out = []
    for url, unit, _title, _category in rows:  # honour notices too (record 三·3)
        c = scope.canonical(url)
        if c and (not judged or scope.judge(c).ok):
            out.append(Seed(c, unit or "", 3, "v1_known"))
    return out


def directory_seeds() -> list[Seed]:
    """The official directory pages (tier 0, marked as directory pages) and the SSO announcement lists (tier 1: lists of
    announcements, not of units), in their canonical spelling."""
    pages = [(u, "學校目錄", 0, True) for u in DIRECTORY] + [(CLUB_DIRECTORY[0], CLUB_DIRECTORY[1], 0, True)]
    pages += [(u, "SSO 公告中心", 1, False) for u in SSO_LISTS]
    return [Seed(scope.canonical(u) or u, unit, tier, directory=d) for u, unit, tier, d in pages]


def directory_pages() -> frozenset[str]:
    """The canonical URLs of the official directory pages: the rows the frontier marks as such."""
    return frozenset(s.url for s in directory_seeds() if s.directory)


def all_seeds(facts_db: Path) -> list[Seed]:
    probe = probe_seeds()
    return directory_seeds() + probe + sitemap_seeds(probe) + v1_known(facts_db)


# ================================================================ round 1 (design §5)

CC_DIR = REPO_ROOT / "research" / "crawl_seed_sources_2026-10-24" / "commoncrawl"
SYSTEMS_KIND = "校务系统"  # the 52 「校务系统」 seeds of the 10-24 choices page (research/site_probe_2026-10-23)
# ⑤: besides the c.nknu template's sites (platform c.nknu…), ksped's /resource/ runs the same system (design §5 ⑤)
SITEMAP_ALSO = frozenset({"ksped.nknu.edu.tw"})
CC_STATUS = ("2", "3", "5")  # Common Crawl's answers taken (a 404 is counted only: design §5 ④)


@dataclass(frozen=True)
class Blocked:
    source: str
    url: str
    reason: str  # out:<rule> (the rules leave it out), or unhandled:<why> (it waits: the Google step, 要人看)
    site: str | None = None

    @property
    def waits(self) -> bool:
        """Not left out by a rule: it waits for a step not plugged in yet (unhandled:google, B8: never 规则不抓)."""
        return self.reason.startswith("unhandled:")


@dataclass(frozen=True)
class Missing:
    site: str
    url: str
    why: str


@dataclass
class SeedReport:
    """What seeding brought, per source and per site, what today's rules left out, and the check (design §5)."""

    verdicts: Counter = field(default_factory=Counter)  # (source, verdict kind) -> n
    by_site: dict = field(default_factory=lambda: defaultdict(Counter))  # owner site -> source -> n
    sites: dict = field(default_factory=dict)  # old site -> {"old", "blocked", "waits" (the Google step), "seeded"}
    blocked: list[Blocked] = field(default_factory=list)  # left out by a rule, or waiting (Blocked.waits)
    blocked_links: Counter = field(default_factory=Counter)  # ③ by reason (out:* and unhandled:*)
    missing: list[Missing] = field(default_factory=list)
    images_not_imported: int = 0
    cc_404: int = 0
    cc_other: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 2 if self.missing else 0

    def note(self, source: str, verdict: str, owner: str | None) -> None:
        kind = verdict.split(":", 1)[0] if verdict.startswith(("out:", "unhandled:", "cap:", "round2:", "failed:")) \
            else verdict
        self.verdicts[(source, kind)] += 1
        if owner:
            self.by_site[owner][source] += 1

    def text(self) -> str:
        w = [f"# 第一轮起点 {datetime.now(UTC).astimezone():%Y-%m-%d %H:%M}", ""]
        w.append("退出码：" + ("2（有网站的起点比旧存档少，见下面「少了的网址」）" if self.missing else "0（每个网站都不少）"))
        for n in self.notes:
            w.append(f"- {n}")
        w += ["", "## 各来源", "", "| 来源 | 结果 | 数量 |", "|---|---|---|"]
        for (src, kind), n in sorted(self.verdicts.items()):
            w.append(f"| {src} | {kind} | {n} |")
        w.append(f"\n旧链接表里的图片没导入、只计数：{self.images_not_imported} 个（那一页重抓时会再找到）")
        w.append(f"Common Crawl 当时回 404 的只计数：{self.cc_404} 个；其他状态（4xx 等）不收：{self.cc_other} 个")
        ruled = [(k, v) for k, v in self.blocked_links.most_common() if not k.startswith("unhandled:")]
        waiting = [(k, v) for k, v in self.blocked_links.most_common() if k.startswith("unhandled:")]
        if ruled:
            w.append("旧链接表被新规则挡掉的目标（只计数）：" + "、".join(f"{k} {v}" for k, v in ruled))
        if waiting:  # B8: waiting for the Google step is 要人看, never 规则不抓 (second review 10-26)
            w.append("旧链接表里等 Google 那一步的目标（要人看，只计数）：" + "、".join(f"{k} {v}" for k, v in waiting))
        w += ["", "## 每个网站：旧存档的行数和这次的起点", "",
              "| 网站 | 旧存档行数 | 被新规则挡掉 | 等 Google 那一步 | 带过来 |", "|---|---|---|---|---|"]
        for s, c in sorted(self.sites.items()):
            w.append(f"| {s} | {c['old']} | {c['blocked']} | {c.get('waits', 0)} | {c['seeded']} |")
        w += ["", "## 每个网站的起点（按来源）", ""]
        for s, c in sorted(self.by_site.items()):
            w.append(f"- {s}：" + "、".join(f"{k} {v}" for k, v in sorted(c.items())))
        w += ["", "## 被新规则挡掉的网址（逐条）", ""]
        for b in self.blocked:
            if b.waits:
                continue
            tag = "（后台：新规则挡掉，原本不在 A1 要登录清单）" if scope.ADMIN_PATH.search(scope.urlsplit(b.url).path) else ""
            w.append(f"- [{b.source}] {b.url} — {b.reason}{tag}")
        w += ["", "## 等 Google 那一步（要人看，逐条）", "",
              "Drive、Docs、日历的网址：Google 那一步（第 7.6 节）接上前不抓，也不是规则不抓。", ""]
        w += [f"- [{b.source}] {b.url} — {b.reason}" for b in self.blocked if b.waits]
        if self.missing:
            w += ["", "## 少了的网址（旧存档有、这次却没有起点，也不是被规则挡掉的）", ""]
            w += [f"- {m.site}：{m.url}（{m.why}）" for m in self.missing]
        return "\n".join(w) + "\n"


def _old_db(old: Path) -> Path:
    old = Path(old)
    return old if old.is_file() else old / "frontier.db"


def _open_old(db: Path) -> sqlite3.Connection:
    """The old archive's frontier.db, read only and as immutable: SQLite neither writes nor locks it (its -wal and
    -shm files are not even opened)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sitemap_aspx(site) -> str | None:
    """⑤ A c.nknu-template site's SiteMap.aspx: its first directory on c.nknu.edu.tw, its path on a host of its own."""
    if not ((site.platform or "").startswith("c.nknu") or site.site in SITEMAP_ALSO) or not site.home:
        return None
    parts = scope.urlsplit(site.home)
    if parts.hostname == "c.nknu.edu.tw":
        seg = next((x for x in parts.path.split("/") if x), None)
        base = f"/{seg}/" if seg else "/"
    else:
        base = site.paths[0] if site.paths else "/"
        base = base if base.endswith("/") else base + "/"
    return scope.canonical(f"{parts.scheme}://{parts.netloc}{base}SiteMap.aspx")


def _systems(probe: Path | None) -> list[str]:
    if probe is None or not Path(probe).exists():
        return []
    rows = json.loads(Path(probe).read_text(encoding="utf-8"))["sites"]
    return [r["u"] for r in rows if r.get("k") == SYSTEMS_KIND and r.get("c") in ("direct", "render") and r.get("u")]


def seed_round1(crawler: Crawler, *, old: Path | None, facts_db: Path | None, cc_dir: Path | None = CC_DIR,
                probe: Path | None = PROBE) -> SeedReport:
    """Seed a round-1 frontier (design §5 ①–⑨): crawler.frontier is written, the old archive only read. Safe to run
    again: what is there already is noted as a source (seed_src) and nothing is added twice."""
    from nknu.archive import crawler as crawl_mod

    report = SeedReport()
    plan, f = crawler.plan, crawler.frontier
    old_conn = _open_old(_old_db(old)) if old is not None else None
    if old_conn is not None:
        wal = Path(f"{_old_db(old)}-wal")
        if wal.exists() and wal.stat().st_size > 0:
            report.notes.append(f"旧库有没合并的 WAL（{wal}，{wal.stat().st_size} 字节）：immutable 方式读不到其中的改动")
    try:
        _site_plan(f, plan, old_conn)
        if old_conn is not None:
            _old_hosts(f, old_conn)

        def seed(url, source, **kw):
            e = crawler._enqueue(url, **kw)
            if e.url is not None:
                f.seed_src(e.url, source)
            report.note(source, e.verdict, e.placement.owner_site if e.placement else None)
            if e.placement is not None and e.placement.state is None:
                report.blocked.append(Blocked(source, scope.canonical(url) or url, e.placement.reason or "out"))
            return e

        # ① the list
        for s in plan.sites.values():
            if s.round == "1":
                for u in s.seeds or ((s.home,) if s.home else ()):
                    seed(u, "site_home", found_via="seed", prio=crawl_mod.PRIO_LIST)
        for u in plan.extra:
            seed(u, "extra", found_via="seed", prio=crawl_mod.PRIO_LIST)
        for u in plan.login_checks:
            verdict = crawler.seed_login_check(u)
            report.note("login_check", verdict, None)
        # ⑧ SSO's lists, ⑤ SiteMap.aspx, ⑥ WordPress sitemaps
        for u in SSO_LISTS:
            seed(u, "sso_lists", found_via="seed", prio=crawl_mod.PRIO_LIST)
        for s in plan.sites.values():
            if s.round != "1":
                continue
            if (u := _sitemap_aspx(s)) is not None:
                seed(u, "sitemap_aspx", found_via="seed", prio=crawl_mod.PRIO_LIST)
            if (s.platform or "").lower().startswith("wordpress") and s.home:
                parts = scope.urlsplit(s.home)
                seed(f"{parts.scheme}://{parts.netloc}/wp-sitemap.xml", "wp_sitemap", found_via="seed",
                     prio=crawl_mod.PRIO_LIST, kind0="sitemap")
        # ⑦ v1's announcements (read only), each judged by admit(): what it leaves out is listed (second review)
        if facts_db is not None:
            for v in v1_known(Path(facts_db), judged=False):
                seed(v.url, "v1_known", found_via="v1_known", prio=1)
        # ② ③ the old archive
        if old_conn is not None:
            _old_rows(crawler, old_conn, report, seed)
        # ④ Common Crawl
        if cc_dir is not None and Path(cc_dir).exists():
            _common_crawl(crawler, Path(cc_dir), report, seed)
        # ⑨ round two's starting points
        for s in plan.sites.values():
            if s.round == "2":
                for u in s.seeds or ((s.home,) if s.home else ()):
                    seed(u, f"round2:{s.why}", found_via="seed")
        for u, batch in plan.round2_pages:
            seed(u, batch, found_via="seed")
        for u in _systems(probe):
            seed(u, "systems", found_via="seed")
        f.meta("plan_sha256", plan.sha256)
        if old is not None:
            f.meta("old_archive", str(Path(old).resolve().parent if Path(old).is_file() else Path(old).resolve()))
        f.meta("seeded_at", datetime.now(UTC).isoformat(timespec="seconds"))
    finally:
        if old_conn is not None:
            old_conn.close()
    return report


def _site_plan(f, plan, old_conn) -> None:
    counts = dict(old_conn.execute("SELECT site, count(*) FROM url GROUP BY site").fetchall()) if old_conn else {}
    for s in plan.sites.values():
        f.x("INSERT OR REPLACE INTO site_plan(site, name, imp, round, why, platform, home, paths, params, leaf, base_urls) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s.site, s.name, s.imp, s.round, s.why or None, s.platform or None, s.home,
             json.dumps(list(s.paths)) if s.paths else None, json.dumps(dict(s.params)) if s.params else None, s.leaf,
             counts.get(s.site)))


def _old_hosts(f, old_conn) -> None:
    """B3: the old hosts' Crawl-delay and robots.txt (fine-arts asks for 20 s; a robots.txt the new crawl cannot read
    must not lose it), and whether they answered the 10-24 crawl, made from the maintainer's Mac off campus. Their
    addresses are looked up again (queue '?'), never taken from the old archive."""
    answered = {r[0] for r in old_conn.execute("SELECT DISTINCT host FROM request WHERE status IS NOT NULL")}
    for h in old_conn.execute("SELECT host, robots_state, robots_body, robots_sha, robots_at, crawl_delay FROM host"):
        cols = {"answered_off": int(h["host"] in answered)}
        if h["robots_state"] in ("rules", "none"):
            cols.update(robots_state=h["robots_state"], robots_body=h["robots_body"], robots_sha=h["robots_sha"],
                        robots_at=h["robots_at"] or 0.0)
        if h["crawl_delay"]:
            cols["crawl_delay"] = h["crawl_delay"]
        known = f.host(h["host"])
        if known is not None and known["robots_state"] in ("rules", "none"):  # read by this archive: newer
            cols = {"answered_off": max(cols["answered_off"], known["answered_off"] or 0)}
        f.set_host(h["host"], **cols)
    for host in answered:
        if f.host(host) is None:
            f.set_host(host, answered_off=1)


def _old_rows(crawler, old_conn, report: SeedReport, seed) -> None:
    """② every old url row and ③ the old link table's targets that are no url row, judged again (round1.admit). An
    attachment counts for the page the old crawl found it on, judged again too: a round-1 page's, or round two's."""
    from nknu.archive import crawler as crawl_mod

    parents: dict[int, dict] = {}
    depths: dict[int, int] = {}
    for r in old_conn.execute("SELECT id, url, site, depth, found_from FROM url ORDER BY id"):
        site = r["site"]
        c = report.sites.setdefault(site, {"old": 0, "blocked": 0, "waits": 0, "seeded": 0})
        c["old"] += 1
        depth = min(r["depth"], crawl_mod.MAX_DEPTH)
        depths[r["id"]] = r["depth"]
        e = seed(r["url"], "old_url", from_row=parents.get(r["found_from"]), depth=depth, found_via="old_frontier",
                 old_ref=r["id"])
        if e.placement is not None and e.placement.verdict in ("out", "unhandled"):
            # left out by a rule, or waiting for the Google step (listed apart: 要人看): neither is missing
            c["blocked" if e.placement.verdict == "out" else "waits"] += 1
            report.blocked[-1] = Blocked("old_url", report.blocked[-1].url, report.blocked[-1].reason, site)
            continue
        if e.url is not None and crawler.frontier.row(e.url) is not None:
            c["seeded"] += 1
            if e.placement is not None and e.id is not None:
                parents[r["id"]] = {"id": e.id, "owner_site": e.placement.owner_site, "tier": e.placement.tier}
        else:
            report.missing.append(Missing(site, r["url"], e.verdict))
    for r in old_conn.execute("SELECT dst, min(src) AS src FROM link WHERE dst NOT IN (SELECT url FROM url) "
                              "GROUP BY dst ORDER BY dst"):
        dst = r["dst"]
        if scope.kind_of(dst) == "image":
            report.images_not_imported += 1  # a page's part: that page finds it again when it is fetched
            continue
        parent = parents.get(r["src"])
        adm = crawler.admit(dst, None, parent)
        if adm is None:
            continue
        pl = adm.placement
        if pl.state is None:
            report.blocked_links[pl.reason or "out"] += 1
            continue
        kind0 = pl.kind0
        prio = crawl_mod._PRIO_OF0.get(kind0, 1) + 1  # §6.7: only an old link names it, until a page links it
        e = crawler._enqueue(dst, placement=pl, url=adm.url, from_row=parent,
                             depth=min(depths.get(r["src"], 0) + 1, crawl_mod.MAX_DEPTH), found_via="old_frontier",
                             prio=prio)
        if e.url is not None:
            crawler.frontier.seed_src(e.url, "old_link")
        report.note("old_link", e.verdict, pl.owner_site)


def _common_crawl(crawler, cc_dir: Path, report: SeedReport, seed) -> None:
    """④ Common Crawl's index lines (CDXJ: key, time, JSON): the addresses it got an answer for then (2xx, 3xx, 5xx),
    one step back in the queue (prio + 1)."""
    from nknu.archive import crawler as crawl_mod

    for path in sorted(cc_dir.glob("*.cdx")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                i = line.find("{")
                if i < 0:
                    continue
                try:
                    rec = json.loads(line[i:])
                except ValueError:
                    continue
                url, status = rec.get("url"), str(rec.get("status") or "")
                if not url:
                    continue
                if status == "404":
                    report.cc_404 += 1
                    continue
                if not status.startswith(CC_STATUS):
                    report.cc_other += 1
                    continue
                adm = crawler.admit(url)
                prio = crawl_mod._PRIO_OF0.get(adm.placement.kind0, 1) + 1 if adm is not None else None
                seed(url, "commoncrawl", depth=1, found_via="commoncrawl", prio=prio)
