"""Writes config/archive_round1.json, round 1's only site list (round-1 design §4.1, §4.2), from the 10-25 site map
(research/project_audit_2026-10-25/site_map_rows.json, 373 rows) and its login list (rule_lists_full.json, A1).

    .venv/bin/python scripts/build_archive_round1.py

research/ is not in git, so the file it writes is committed; tests/archive/test_round1_scope.py checks that running
this again makes the same file (wherever research/ is there).

The rules (scope record 二·2): a row's key is scope.site_key(home) (373 rows, 368 keys). 全校重要 and 本系重要 are
round 1, except the 5 sites that need JavaScript (w3, ord, ite, gauss, see: round 2, js_site; owner 10-25 15:33) and
SSO's attachment service (webservices: round "resource", fetched as the parts of the pages that link it). Every other
key is round 2 (other_site). A key several rows share keeps its important row's name and imp and a path prefix: ksped
/resource/, dataopen2search's lost-and-found and course-table pages (its home, demo.aspx, is a round-2 page, so the
round-1 home is the lost-and-found page). Extra seeds: the 4 pages the owner opened (record 三·4, 三·5), 7 URLs. Login
re-check: the A1 URLs inside round-1 sites (design §4.2: 15).

Round two's seeds (design §4.1, §5 ⑨; record 三·2, 四, 附表一): the school's own units on off-campus sites are
why=offsite_unit. Of 附表一's 35 URLs, 8 are in the site map already (their why becomes offsite_unit, the rest of
their row stays); the other 27 are written below (UNITS), one entry per key (the two Google Groups forums and the two
TED pages share theirs: 25 keys), with what 附表一's 「抓的范围」 says: a whole site has no paths; 「该站路径」
「该组织路径」「该分会路径」「该论坛」 are paths (Wix's and Google Sites' keys already hold the site's name: no paths);
「该页」「这两页」 are those pages only (leaf 1). The two centres on the Ministry's platforms (record 四, 【Claude】: as
附表一's units, by their path or their sid). round2_pages: off-campus pages round two fetches one page of, with the
batch they come from (record 四: the 5 OpenStreetMap shops and the bus timetable the owner agreed to; claude: the
affiliated senior high school and the teacher qualification site, 【Claude】). This script is the one place the reach
of each is written; 附表一's column is checked against it (tests/archive/test_round1_scope.py).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from nknu.archive import round1, scope

REPO = Path(__file__).resolve().parents[1]
ROWS = REPO / "research" / "project_audit_2026-10-25" / "site_map_rows.json"
RULES = REPO / "research" / "project_audit_2026-10-25" / "rule_lists_full.json"
OUT = REPO / "config" / "archive_round1.json"

IMPORTANT = ("全校重要", "本系重要")
JS_SITES = ("w3.nknu.edu.tw", "ord.nknu.edu.tw", "ite.nknu.edu.tw", "gauss.nknu.edu.tw", "see.nknu.edu.tw")
RESOURCE = "sso.nknu.edu.tw/webservices"
PATHS = {"ksped.nknu.edu.tw": ["/resource/"],
         "sso.nknu.edu.tw/dataopen2search": ["/dataopen2search/lostandfound.aspx",
                                             "/dataopen2search/opencoursetablepure.aspx"]}
HOMES = {"sso.nknu.edu.tw/dataopen2search": "https://sso.nknu.edu.tw/dataopen2search/lostandfound.aspx"}
EXTRA = [
    ("https://sso.nknu.edu.tw/recruit/index.aspx", "招生报名首页", "三·5"),
    ("https://sso.nknu.edu.tw/recruit/index.aspx?classtype=M", "招生报名（在职专班）", "三·5"),
    ("https://sso.nknu.edu.tw/recruit/index.aspx?classtype=T", "招生报名（在职教学硕士学位班）", "三·5"),
    ("http://sso.nknu.edu.tw/recruit/index.aspx", "招生报名首页（http 版）", "三·5"),
    ("https://sso.nknu.edu.tw/staff/courseenrollinfo/courseenrollinfo.aspx", "课务查询首页", "三·5"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=19", "专案小组会议纪录", "三·5"),
    ("https://sso.nknu.edu.tw/studentprofile/stud_mails.aspx", "学生信件招领", "三·4"),
]
# the owner could not open these (record 三·5): never checked, whatever a list says
OWNER_SHUT = ("cmeetingtype=4", "cmeetingtype=8", "cmeetingtype=9", "/staff/staffleavestate.aspx")
ABOUT = ("第一轮爬虫唯一的网站名单（第一轮设计 §4.1、§4.2）。由 scripts/build_archive_round1.py 从 "
         "research/project_audit_2026-10-25/site_map_rows.json 和 rule_lists_full.json 的 A1，加上抓取范围决定的附表一和"
         "记录四产生；要改名单，先改脚本再重新产生。round：1 第一轮，resource 当附属资源，2 第二轮（why 写原因："
         "js_site 整站靠 JavaScript、other_site 其他网站、offsite_unit 校外的本校单位网站）。paths：只算这些路径（整段比）；"
         "params：参数值相同才算；leaf 1：只抓那一页；seeds：第二轮的起点；round2_pages：第二轮只抓那一页的校外网页。")
# 附表一 (record 三·2): the 8 the site map already has, by their key
UNITS_IN_SITE_MAP = ("sites.google.com/iec-nknu.page/iec", "sites.google.com/view/taiwanology",
                     "pairnknu.wixstudio.io/pair-center", "www.nknu-vd.com", "sites.google.com/view/nknu-edu-teach1",
                     "mandy900316.wixsite.com/website", "ocsnknu.wixsite.com/index", "nknubwy2.wixsite.com/nknu-bwy")
# 附表一's other 27 URLs (record 三·2), in its order: (name, seeds, paths, leaf). groups.google.com's ujujnknu is
# written as the forum's own address: canonical() drops the #!forum/ujujnknu of the record's URL (design §4.1)
UNITS = (
    ("SHINE Center 南区社会情绪学习与教育创新中心", ["https://sites.google.com/view/nknu-shine-center"], [], 0),
    ("身心障碍者职业重建服务资源中心", ["https://kppvrrc.bur.tw/index.php"], [], 0),
    ("国文学系", ["https://nknuchinese.tw/"], [], 0),
    ("客家文化研究所（Wix）", ["https://ul2974.wixsite.com/nknuhakka"], [], 0),
    ("性别教育研究所（Wix 旧站）", ["https://nknugender.wixsite.com/nknugender"], [], 0),
    ("高龄产业经营学士原住民专班 AIM-I", ["https://2060631web-repository.github.io/nknu-aim-i/"], ["/nknu-aim-i"], 0),
    ("教育学刊（教育学系）", ["https://sites.google.com/view/nknu-educational-review/"], [], 0),
    ("教育学系高中生专区（Canva）", ["https://nknu-edu.my.canva.site/"], [], 0),
    ("教育学系高中生专区（Webflow）", ["https://eduhighschool.webflow.io/"], [], 0),
    ("软件工程与管理学系高中生专区", ["https://semfreshman.sdpmlab.org/"], [], 0),
    ("华语学程论坛、华语所论坛（Google 网路论坛）",
     ["https://groups.google.com/g/ujujnknu", "https://groups.google.com/g/nknutc"], ["/g/ujujnknu", "/g/nknutc"], 0),
    ("性别教育研究所 Podcast", ["https://solink.soundon.fm/gigepodcast"], ["/gigepodcast"], 1),
    ("生活科技科教师增能推动总召暨开班计划", ["https://sites.google.com/site/empowerteacher/home"], [], 0),
    ("第二十七届全国语言学论文研讨会 2026NCL", ["https://ix5961.wixstudio.com/2026nclcknu"], [], 0),
    ("电机系 114 学年度实作专题成果发表展", ["https://114-yt3qscn.gamma.site/"], [], 0),
    ("2019 第 15 届科技教育研究与发展学术研讨会", ["https://sites.google.com/view/nknuite-forum2019"], [], 0),
    ("2022 第 18 届科技教育研究与发展学术研讨会", ["https://sites.google.com/view/nknuite2022-18"], [], 0),
    ("119 级新生定向共识营", ["https://lit.link/zh-tw/119freshmancamp_NKNU"], ["/zh-tw/119freshmancamp_nknu"], 1),
    ("TEDxNKNU 2023、2024（TED.com 活动页）",
     ["https://www.ted.com/tedx/events/53158", "https://www.ted.com/tedx/events/56115"],
     ["/tedx/events/53158", "/tedx/events/56115"], 1),
    ("NKNU-FABLAB 文件站", ["https://nknufablab.github.io/"], [], 0),
    ("NKNU 马达感测器（自造者基地卫星基地计划）", ["https://sites.google.com/view/nknuparking/"], [], 0),
    ("街头物理 Adept 实验室", ["http://streetphysics.blogspot.com/"], [], 0),
    ("教育学系生命教育研究学会（Wix）", ["https://lifedunknu.wixsite.com/life-education-nknu"], [], 0),
    ("GDG on Campus NKNU（GitHub 组织）", ["https://github.com/GDG-on-Campus-NKNU"], ["/gdg-on-campus-nknu"], 0),
    ("GDG on Campus NKNU（gdg.community.dev）",
     ["https://gdg.community.dev/gdg-on-campus-national-kaohsiung-normal-university-kaohsiung-city-taiwan/"],
     ["/gdg-on-campus-national-kaohsiung-normal-university-kaohsiung-city-taiwan/"], 0),
    # record 四: the two centres on the Ministry's platforms, as 附表一's units (【Claude】)
    ("国中小自造及科技辅导中心", ["https://cirn.k12ea.gov.tw/web1240/"], ["/web1240/"], 0),
    ("综合活动领域教学研究中心", ["https://cirn.moe.edu.tw/WebContent/index.aspx?sid=1219&mid=15043"], [], 0),
)
UNIT_PARAMS = {"cirn.moe.edu.tw": {"sid": "1219"}}  # the unit is in its parameter, not its path (record 四)
ROUND2_PAGES = (  # (url, batch)
    *((f"https://www.openstreetmap.org/{p}", "record4") for p in (
        "way/179222556", "node/6801079464", "node/1895719326", "way/381732645", "node/12312253801")),
    ("https://www.edabus.com.tw/Timetable.aspx?pathid=120", "record4"),
    ("https://www.nknush.kh.edu.tw/", "claude"),
    ("https://tqa.rcpet.edu.tw/", "claude"),
)


def build(rows_path: Path = ROWS, rules_path: Path = RULES) -> dict:
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(scope.site_key(scope.canonical(r["home"]) or r["home"]), []).append(r)
    sites = [_entry(key, group) for key, group in by_key.items()]
    for s in sites:
        if s["site"] in UNITS_IN_SITE_MAP:
            if s.get("why") != "other_site":
                raise ValueError(f"{s['site']}: 附表一 lists it, the site map does not have it as another site")
            s["why"] = "offsite_unit"
    sites += [_unit(name, seeds, paths, leaf) for name, seeds, paths, leaf in UNITS]
    sites = sorted(sites, key=_order)
    if len({s["site"] for s in sites}) != len(sites):
        raise ValueError("a key is written twice: merge its entries into one")
    extra = tuple(scope.canonical(u) for u, *_ in EXTRA)
    plan = round1.Plan({s["site"]: round1.parse_site(s) for s in sites}, extra, (), "")
    a1 = json.loads(rules_path.read_text(encoding="utf-8"))["A1"]["rows"]
    checks = sorted({u for r in a1 if (u := scope.canonical(r["key"])) and not any(s in u for s in OWNER_SHUT)
                     and round1.verdict(plan, u).verdict == "r1"})
    return {
        "about": ABOUT,
        "made_from": {"site_map_rows": str(rows_path.relative_to(REPO)), "site_map_sha256": _sha(rows_path),
                      "rule_lists": str(rules_path.relative_to(REPO)), "rule_lists_sha256": _sha(rules_path)},
        "sites": sites,
        "extra": [{"url": scope.canonical(u), "name": n, "why": f"用户实测进得去（记录{rec}）"} for u, n, rec in EXTRA],
        "login_check": [{"url": u, "site": round1.verdict(plan, u).owner_site} for u in checks],
        "round2_pages": [{"url": scope.canonical(u), "batch": b} for u, b in ROUND2_PAGES],
    }


def _unit(name: str, seeds: list[str], paths: list[str], leaf: int) -> dict:
    """One entry of 附表一 the site map lacks (or a centre of record 四): round 2, offsite_unit, its seeds."""
    seeds = [scope.canonical(u) for u in seeds]
    keys = {scope.site_key(u) for u in seeds}
    if len(keys) != 1 or None in seeds:
        raise ValueError(f"{name}: its seeds are one key's ({keys})")
    key = keys.pop()
    out = {"site": key, "name": name, "round": 2, "why": "offsite_unit", "home": seeds[0]}
    if len(seeds) > 1:
        out["seeds"] = seeds
    if paths:
        out["paths"] = paths
    if key in UNIT_PARAMS:
        out["params"] = UNIT_PARAMS[key]
    if leaf:
        out["leaf"] = leaf
    return out


def _entry(key: str, group: list[dict]) -> dict:
    """One key's entry: the important row speaks for it (the first, in the site map's order), else the first row."""
    main = next((r for r in group if r["imp"] in IMPORTANT), group[0])
    out = {"site": key, "name": main["name"], "imp": main["imp"]}
    if key == RESOURCE:
        out = {"site": key, "name": "SSO 附件接口（各单位网站挂的附件）", "round": "resource", "why": "附件接口，不是网站",
               "platform": main["platform"]}
        return out
    if main["imp"] in IMPORTANT and key not in JS_SITES:
        out.update({"round": 1, "platform": main["platform"], "home": HOMES.get(key, main["home"])})
        if key in PATHS:
            out["paths"] = PATHS[key]
        return out
    out.update({"round": 2, "why": "js_site" if key in JS_SITES else "other_site", "platform": main["platform"],
                "home": main["home"]})
    return out


def _order(entry: dict) -> tuple:
    rank = {1: 0, "resource": 1, 2: 2}[entry["round"]]
    imp = IMPORTANT.index(entry["imp"]) if entry.get("imp") in IMPORTANT else len(IMPORTANT)
    why = {"js_site": 0, "offsite_unit": 1}.get(entry.get("why"), 2)
    return rank, why, imp, entry["site"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(plan: dict) -> str:
    """Pretty, with one site per line, so that a change to the list is a one-line diff."""
    lines = ["{"]
    for i, (key, value) in enumerate(plan.items()):
        comma = "," if i < len(plan) - 1 else ""
        if isinstance(value, list):
            lines.append(f"  {json.dumps(key)}: [")
            lines += [f"    {json.dumps(v, ensure_ascii=False)}{',' if j < len(value) - 1 else ''}"
                      for j, v in enumerate(value)]
            lines.append(f"  ]{comma}")
        else:
            lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    return "\n".join(lines) + "\n}\n"


def main() -> int:
    plan = build()
    OUT.write_text(dump(plan), encoding="utf-8")
    rounds = [s["round"] for s in plan["sites"]]
    units = sum(s.get("why") == "offsite_unit" for s in plan["sites"])
    print(f"{OUT.relative_to(REPO)}: {len(plan['sites'])} keys, round 1 {rounds.count(1)}, "
          f"resource {rounds.count('resource')}, round 2 {rounds.count(2)} (offsite_unit {units}); "
          f"{len(plan['extra'])} extra seeds, {len(plan['login_check'])} login checks, "
          f"{len(plan['round2_pages'])} round-2 pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
