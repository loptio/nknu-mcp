"""Where the archive crawl starts, in the order the owner asked for: by publishing institution.

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
from dataclasses import dataclass
from pathlib import Path

from nknu.archive import scope
from nknu.core.pii import is_honor_title
from nknu.settings import REPO_ROOT

PROBE = REPO_ROOT / "research" / "site_probe_2026-10-23" / "data.json"
DIRECTORY = ["https://w3.nknu.edu.tw/", "https://w3.nknu.edu.tw/admin.html", "https://w3.nknu.edu.tw/edu_list.html",
             "https://w3.nknu.edu.tw/dept_website_list.html", "https://w3.nknu.edu.tw/center.html",
             "https://w3.nknu.edu.tw/sitemap.html", "https://w3.nknu.edu.tw/services.html"]
# 課外活動指導組's public club roster (design §2 「官方目录当起点」); the probe only has it as roster entries
CLUB_DIRECTORY = ("https://sso.nknu.edu.tw/OSA/StuClub.aspx", "課外活動指導組 社團一覽")
SSO_LISTS = [f"https://sso.nknu.edu.tw/News/SubjectList.aspx?cat={c}"
             for c in ("SCHOOLADMIN", "ENTRANCEEXAM", "CAMPUS", "Lights", "LEARNING", "JOB")]
# not GOOD (榮譽典範): honour notices keep only their masked title, link and date, never the body (owner, 2026-10-24)
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


def v1_known(facts_db: Path) -> list[Seed]:
    """Announcement pages v1 found through list paging (read only). Seeded so the archive holds them; whether link
    following also found them is the coverage check of the monitor."""
    if not facts_db.exists():
        return []
    conn = sqlite3.connect(f"file:{facts_db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT url, unit, title, category FROM doc WHERE withdrawn_at IS NULL").fetchall()
    finally:
        conn.close()
    out = []
    for url, unit, title, category in rows:
        if category == "榮譽典範" or is_honor_title(title or ""):
            continue  # honour notices: never their body
        c = scope.canonical(url)
        if c and scope.judge(c).ok:
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
