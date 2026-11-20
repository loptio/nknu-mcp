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
# round two, batch two (design 2026-11-17-round2-batch2 §三.1): ksped is round one whole (its path /resource/ is gone),
# the teachers' CV pages join dataopen2search's round-one part
PATHS = {"sso.nknu.edu.tw/dataopen2search": ["/dataopen2search/lostandfound.aspx",
                                             "/dataopen2search/opencoursetablepure.aspx",
                                             "/dataopen2search/vita.aspx"]}
# the batch that crawls the off-campus unit sites now, but the ones the site map calls 基本没用 (the owner 11-16: 「35 站里
# 的 5 个旧站也排除」), with each one's own caps 【Claude: design §四, ten times the 14 rows a unit has on average】
NOW_BATCH = "2026-11-17"
NOW_CAPS = {"page": 300, "file": 300, "image": 600}
# caps widened by the design's §四 way (a new cap here, the list made again, the site promoted again): 11-17 the gender
# institute's old Wix blog filled its 300 pages with about 84 tag lists and held 52 posts (an admission notice among
# them) found nowhere else, and holds about 980 pictures; 爬虫judge under the owner's delegation: 「同意照二做：网页 450、
# 图片 1,200、附件不变」
# 11-17 again (爬虫judge, choice B, after its own sitemap was counted: 567 posts, 24 pages, 17 categories; 「网页上限 900、
# 图片 2,600」): the posts seeded from the sitemap, the tag lists not followed (SKIP_PATHS)
WIDER_CAPS = {"nknugender.wixsite.com/nknugender": {"page": 900, "file": 300, "image": 2600}}
SKIP_PATHS = {"nknugender.wixsite.com/nknugender": ["/nknugender/blog/hashtags"],
              # 11-17 (爬虫judge: 「只抓社团首页和各仓库首页（README）」): every page below a repository's home, and every
              # address with a query (the org page's tabs, the code search)
              "github.com": ["/gdg-on-campus-nknu/*/*"],
              # 11-18 (course schedules design §五): the course query's table of a teacher and of a room are the same
              # courses as the classes' tables, which are fetched one a class (schedules.seed): never followed. Only
              # with a query (the trailing ?): the two query pages themselves are in the archive since round one
              "sso.nknu.edu.tw/stu": ["/stu/scheduleteacher.aspx?", "/stu/scheduleroom.aspx?"],
              # 11-19: SSO's previewpdf.aspx serves two folders by one parameter, the regulations (\\Regulations\\,
              # followed) and the papers and books the teachers' vita pages link (\\NknuTeacher\\: about 1,450
              # full texts of research, whose titles the vita pages already give): the works are not fetched
              "sso.nknu.edu.tw": ["/previewpdf.aspx?cpathandfilename=%5cnknuteacher%5c"],
              # 11-19: 国中小自造及科技辅导中心's download pages (teaching material for school teachers, some two
              # hundred files of 6–15 MB behind pagers the first revisit lap reached): not fetched; its menu
              # (List.aspx, which sends a folder to its page), its notices and its other pages stay
              "cirn.k12ea.gov.tw": ["/web1240/dl_page.aspx"]}
# 11-19: a skip path with a name of its own (round1.Site.skip_names): the export writes its rows out:skip_path:<name>,
# the one code the content stage lets documents already in its library leave by
SKIP_NAMES = {"sso.nknu.edu.tw": {"/previewpdf.aspx?cpathandfilename=%5cnknuteacher%5c": "teacher_fulltext"},
              "cirn.k12ea.gov.tw": {"/web1240/dl_page.aspx": "teaching_materials"}}
# 11-19: pages whose Google files a site does not fetch, with the rule's name (round1.Site.skip_google). 工教系's
# 學術活動 pages each carry a seminar's proceedings on Drive, a whole volume of papers (17–51 MB, eleven years and a
# journal issue): not collected, as the teachers' works are not; the pages themselves and their own files stay
SKIP_GOOGLE = {"ite.nknu.edu.tw": {"/?p=acadact": "proceedings"}}
SKIP_QUERY = {"github.com"}
# 11-18 evening (course schedules design §八): links a site does not follow by the page they stand on. A course
# outline's page links the same department's outlines of the year before (「114-1該系所日/夜相關課綱」), whose pages
# link the year before that: never followed, whatever the term (the outlines a class's table names are)
SKIP_FROM = {"sso.nknu.edu.tw/stu": [["/stu/18weekssyllabusforenro.aspx", "/stu/syllabusforenro.aspx"]]}
# round two's first batch, the five JavaScript sites (js爬虫's handoff 11-17, the owner 「现在交接」; plan
# 2026-11-17-jsfetch-integration task 2): crawled now, with a round-one site's caps (they are school sites of their own)
# 11-19: a site whose rows of a kind past its own cap are not fetched (round1.Site.cap_out). 产业教育中心's Google Sites
# pages carry about 2,700 pictures past its cap of 600, activity photographs
CAP_OUT = {"sites.google.com/iec-nknu.page/iec": ["image"]}
# 11-19: the off-campus unit sites of the batch are read again one layer deep (their home and the pages it links),
# weekly; 性别所's old Wix site, which moved to a new one, monthly (round1.Site.revisit, update.REVISIT_WEEKDAY)
MONTHLY = {"nknugender.wixsite.com/nknugender"}
JS_NOW = {"w3.nknu.edu.tw", "ord.nknu.edu.tw", "ite.nknu.edu.tw", "gauss.nknu.edu.tw", "see.nknu.edu.tw"}
LOW_IMP = "基本没用"
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
         "params：参数值相同才算；leaf 1：只抓那一页；seeds：第二轮的起点；round2_pages：第二轮只抓那一页的校外网页；"
         "now：第二轮这次就抓的批次（当成第一轮的网站抓），caps：这一站自己的上限（网页、附件、图片）。"
         "cap_out：这一站超过自己上限就按规则不抓的种类。revisit：抓完以后每周（weekly）或每月（monthly）再看一次首页和首页"
         "链到的页。")
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
    for s in sites:
        if s.get("why") == "offsite_unit" and s.get("imp") != LOW_IMP:
            s.update(now=NOW_BATCH, caps=dict(WIDER_CAPS.get(s["site"], NOW_CAPS)),
                     revisit="monthly" if s["site"] in MONTHLY else "weekly")
            if s["site"] in CAP_OUT:
                s["cap_out"] = list(CAP_OUT[s["site"]])
            if s["site"] in SKIP_PATHS:
                s["skip"] = list(SKIP_PATHS[s["site"]])
            if s["site"] in SKIP_QUERY:
                s["skip_query"] = True
        if s.get("why") == "js_site" and s["site"] in JS_NOW:
            s.update(now=NOW_BATCH)
        if s.get("round") == 1 and s["site"] in SKIP_PATHS:  # a round-one site's own skip paths (11-18)
            s["skip"] = list(SKIP_PATHS[s["site"]])
        if s["site"] in SKIP_NAMES:
            s["skip_names"] = dict(SKIP_NAMES[s["site"]])
        if s["site"] in SKIP_FROM:
            s["skip_from"] = [list(p) for p in SKIP_FROM[s["site"]]]
        if s["site"] in SKIP_GOOGLE:
            s["skip_google"] = dict(SKIP_GOOGLE[s["site"]])
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
