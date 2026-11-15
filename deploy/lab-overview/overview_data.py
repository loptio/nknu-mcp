"""The data of the 资料总览 page, read from what the build left on the lab PC.

Owner 11-15: 「下一步你先帮我把数据总览接上真实的数据」; the way, approved the same day (「照这样做」): a read-only service of its
own on the lab PC that recomputes when the library changes, and a page that refreshes itself (see server.py).

Everything is read, nothing is written:
- the library (aca.db): documents per site and kind, text volume, dates, the school's list categories, page kinds, the unit
  tree (tables units, sites.unit) that groups the sites the way the school is organised, and the copies merged away;
- each site's export (manifest.csv in the folder sites.export names): what the crawl did with every page, file and image;
- the build report (build_report.json): what was fetched but left out of the library, and why;
- config/archive_round1.json: names, importance and home pages of the round-1 sites, and the sites left for round 2.

This replaces the 11-11 one-off scripts in research/ops_2026-11-11/ (extract_atlas.py, extract_daily.py, extract_v2.py,
make_groups.py, build_atlas.py) with the same numbers; the grouping now reads the unit tree the library carries (checked
11-15: the same group and unit for all 111 sites as make_groups.py gave). The folding of crawl results into six outcomes,
of page kinds into sections and of left-out reasons into a few kinds is Claude's.

The page is in Traditional Chinese (owner 11-15: 「可视化dashboard全部换繁体字」): the school's own text (the library's
site and unit names, the colleges, the list categories and groups) is shown as the school writes it; the labels here
are Traditional; the owner's site list, which writes most names, importance, platform and why in Simplified, is
converted for display only (OpenCC s2tw, see _tw). The site finder still matches a name typed in Simplified (see
finder_names).
"""

from __future__ import annotations

import collections
import csv
import datetime
import functools
import json
import re
import sqlite3
from pathlib import Path

csv.field_size_limit(1 << 30)

DB = "research/pipeline_2026-11-02/aca.db"
REPORT = "research/pipeline_2026-11-02/build_report.json"
# spec 2026-11-16-data-fixes §五: the build's own checks of each site (research/pipeline_2026-11-02/lab_pc/site_report.py
# --checks, written by build.sh beside the official library after its checks run): its reminders, for this page to show
CHECKS = "research/pipeline_2026-11-02/site_checks.json"
ROUND1 = "config/archive_round1.json"
KINDS = ("notice", "page", "attachment", "event", "link")
BASE = ("notice", "page", "attachment")
EPOCH = datetime.date(1970, 1, 1)
# the groups in the order the page shows them; a college's name is the unit tree's own, the rest are labels (all Traditional)
GROUP_ORDER = ["文學院", "教育學院", "理學院", "藝術學院", "科技學院", "管理學院", "行政處室", "中心", "全校共用"]
GROUP_TIP = {"行政處室": "教務處、學務處、總務處、圖資處等行政單位的網站",
             "中心": "語文教學、特殊教育、學生輔導、通識教育中心的網站",
             "全校共用": "學校首頁和 SSO 平台上全校共用的頁面：活動、課程地圖、開課課表等"}
COLLEGE_TIP = "學院和它底下各系所的網站"
# the four keys the round-1 list carries as single extra pages, named after what the pages are (archive_round1.json "extra")
EXTRA_NAME = {"sso.nknu.edu.tw/recruit": "招生報名", "sso.nknu.edu.tw/staff": "課務查詢",
              "sso.nknu.edu.tw/unit": "專案小組會議紀錄", "sso.nknu.edu.tw/studentprofile": "學生信件招領"}
# page kinds that are a section of their own on the school's platforms (labels from the pages' own titles, sampled 11-11);
# the keys are the library's kind_raw values
SECTION = {"student_detail": "所友名錄", "experience_view": "心得發表", "case_detail": "活動花絮", "case": "活動花絮",
           "forumsview": "討論區", "teachercorner": "師資陣容", "teacher": "師資陣容", "cardprint": "明信片列印",
           "filesdownload": "檔案下載頁", "files": "檔案下載頁", "documentsdownload": "檔案下載頁", "member": "編制與業務",
           "recordview": "場地借用", "qaview": "問與答", "publishview": "出版品", "publish": "出版品", "club_view": "學生園地",
           "静态:spaper": "舊校刊", "静态:nknupaper": "校刊", "静态:sinfo": "校訊"}
OUTCOMES = ("got", "next", "gone", "fail", "login", "todo")
# the export's 结果 values (the crawler's words, matched as they are)
GOT = {"抓到了", "这类网页还没判定", "页面几乎是空的"}
GONE = {"学校那边已经不存在", "链接已失效：跳回学校首页", "看起来像不存在（程序比对的）", "短网址已失效", "学校的链接坏了"}
WHY = {"js_site": "整站靠 JavaScript", "other_site": "其他網站", "offsite_unit": "校外的本校單位網站"}
# the round-1 list's own importance words, in its order: sorted on as they are, shown converted (see _tw)
IMP_ORDER = ["全校重要", "本系重要", "偶尔有用", "看不出来", "基本没用", "不是资讯网站", ""]
DATE_SLOT = {"published": 0, "inherited": 1, "server": 2}  # anything else (a period written in the text, ...) is slot 3
# a file dated by its own row on a file table (the library of 11-16: spec 2026-11-16-data-fixes §二, file_row_dates) is
# slot 4, apart from a page's or notice's own date (both are the ident_kind 「published」)
ROW_SLOT = 4
# the reminders of the build's checks this page shows (site_report.FLAG_WORDS's kinds; 「no_date」 is the page's own
# 無日期 meter already) 【Claude】
CHECK_KINDS = ("none", "self", "empty", "same_title", "same_opening", "fragments")

CATS = re.compile(r"類別：(.*)$")
COPIES = "重複或副本（同樣的內容只留一份）"  # one label for the copies the report excludes and the ones dedupe removed


@functools.cache
def _t2s():
    """OpenCC t2s, never for what the page shows: it tells whether a value already holds Traditional characters (_tw),
    and gives the site finder a name's Simplified form (finder_names)."""
    try:
        from opencc import OpenCC
    except ImportError:
        return lambda s: s
    return OpenCC("t2s").convert


@functools.cache
def _tw():
    """OpenCC s2tw for the owner's Simplified text the page shows: the round-1 list's names, importance, platform and why
    (owner 11-15: 「可视化dashboard全部换繁体字」). For display only: this module sorts and matches on the list's own words.

    Two departures from plain s2tw 【Claude】, so that a name reads the way the school writes it (and the department site
    named like its unit, under which the page hangs the 品保 site, still carries the unit's name):
    - 台 stays 台 where s2tw only swaps it for its variant 臺: the school's own names write 台 far more often than 臺 (平台,
      台灣歷史文化及語言研究所, 超現代台灣學研究中心); 檯 and 颱 still come through.
    - A value that already holds Traditional characters is the school's own name, sometimes with the owner's note in
      Simplified (「化學系（短网址）」): only its Simplified characters change, and every character Traditional writes too
      is left as the school wrote it. Which those are is OpenCC's own table: a Simplified character listed among its own
      Traditional forms (台: 臺 檯 颱 台).
    """
    try:
        import opencc
    except ImportError:  # the page still works, with the list's own characters
        return lambda s: s
    s2tw, t2s = opencc.OpenCC("s2tw").convert, _t2s()
    both = {"台"}
    try:
        table = Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt"
        for line in table.read_text(encoding="utf-8").splitlines():
            key, _, forms = line.partition("\t")
            if key in forms.split():
                both.add(key)
    except OSError:  # without the table only 台 is left as the school wrote it
        pass

    def tw(text: str) -> str:
        out = s2tw(text)
        if out == text or len(out) != len(text):
            return out
        school = t2s(text) != text  # it holds Traditional characters
        out = "".join(a if (a in both if school else (a, b) == ("台", "臺")) else b for a, b in zip(text, out))
        for mainland, taiwan in TW_TERMS:
            out = out.replace(mainland, taiwan)
        return out

    return tw


# Words of the owner's Simplified notes that read as mainland usage once only their characters change (the page shows
# Taiwan usage: owner 11-15). OpenCC's own phrase step (s2twp) is not used: on the round-1 list it also turns 高級 into
# 高階, 社區 into 社群 and 文件 into 檔案, which breaks the school's names. These five do not occur in them 【Claude】.
TW_TERMS = (("模塊", "模組"), ("服務器", "伺服器"), ("接口", "介面"), ("軟件", "軟體"), ("鏈接", "連結"))


def finder_names(shown: str, *more: str) -> str:
    """The "o" of a site or of a round-2 row, never shown: what the page's site finder matches besides the shown name and
    the key, that is the library's own name and the shown name in Simplified, so that a query typed in Simplified (the
    owner's keyboard) still finds the site, as it did while the page showed Simplified names 【Claude】."""
    return " ".join(dict.fromkeys(x for x in (*more, _t2s()(shown)) if x and x != shown))


def outcome(result: str, cat: str) -> str:
    """One row of an export's manifest.csv folded into got / next / gone / fail / login / todo (or redirect / rule)."""
    if result in GOT:
        return "got"
    if result == "下一轮再抓":
        return "next"
    if result in GONE:
        return "gone"
    if result.startswith("跳到别的网址"):
        return "redirect"
    if result == "按规则不抓":
        return "rule"
    if result.startswith("要登录"):
        return "login"
    return "fail" if cat == "连接失败" else "todo"


def left_out(reason: str) -> str:
    """A reason of the build report's 「excluded」 (the build's words, matched as they are) folded into what a reader
    recognises."""
    if "翻页" in reason or reason.startswith(("list:", "newslist", "album#")):
        return "列表頁和翻頁頁"
    if "重复" in reason or "副本" in reason:
        return COPIES
    if reason.startswith("行事历"):
        return "2023 年以前的行事曆"
    if reason.startswith("album"):
        return "相簿照片"
    if "学校删掉" in reason:
        return "學校已刪除、只剩模板的公告"
    if "学校回答没有资料" in reason:
        return "學校回答「沒有資料」的網頁"
    if "只在选单出现" in reason:
        return "只在選單裡出現的圖片"
    return "其他"


def short(key: str) -> str:
    return key.replace("sites.google.com/mail.nknu.edu.tw/", "sites.google/").replace(".nknu.edu.tw", "")


def strip_note(name: str) -> str:
    n = re.sub(r"[（(].*?[）)]", "", name).strip()
    return n[:-2] if n.endswith(("網站", "网站")) and "品保" not in n else n


def fmt_slot(kind_raw: str) -> int:
    return 0 if "pdf:有文字层" in kind_raw else 1 if "pdf:含OCR" in kind_raw else 2 if "office" in kind_raw else \
        3 if "图片" in kind_raw else 4


def group_of(unit_id: str, units: dict[str, tuple[str, str, str]]) -> str:
    """The college a unit sits under; else 行政處室, 中心, or 全校共用 when the unit is the university itself (the unit
    tree's kinds 学院, 学校 and 中心 are the library's words, matched as they are)."""
    chain, cur, seen = [], unit_id, set()
    while cur and cur in units and cur not in seen:
        seen.add(cur)
        chain.append(units[cur])
        cur = units[cur][2]
    for name, kind, _ in chain:
        if kind == "学院":
            return name
    if not chain or chain[0][1] == "学校":
        return "全校共用"
    return "中心" if chain[0][1] == "中心" else "行政處室"


def day_of(text: str | None) -> int | None:
    try:
        return (datetime.date.fromisoformat((text or "")[:10]) - EPOCH).days
    except ValueError:
        return None


def owns(site: str, url: str) -> bool:
    """Whether a URL lies on a site key such as 'c.nknu.edu.tw/aca' or 'elec.nknu.edu.tw'."""
    rest = url.split("://", 1)[-1]
    return rest == site or (rest.startswith(site) and rest[len(site)] in "/?#")


def _blank() -> dict:
    """One site's tallies. Every amount the page shows is kept twice, in documents and in characters, so the page's
    文件數 / 字數 switch reaches every chart (owner 11-15: 「切換成字數的時候文件日期分佈那個圖還是以文件數量計算的」):
    a day holds [notice, page, attachment, event] counts then the same four in characters."""
    return {"kinds": collections.Counter(), "raw": collections.Counter(), "raw_c": collections.Counter(), "bg": 0,
            "no_text": collections.Counter(), "chars": collections.Counter(), "undated": collections.Counter(),
            "undated_c": collections.Counter(), "min_date": "", "event_min": "",
            "days": collections.defaultdict(lambda: [0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0]), "dsrc": [0, 0, 0, 0, 0],
            "dsrc_c": [0, 0, 0, 0, 0], "cats": collections.Counter(), "cats_c": collections.Counter(),
            "grp": collections.Counter(), "grp_c": collections.Counter()}


def _pairs(n: collections.Counter, c: collections.Counter) -> list[list]:
    """[name, documents, characters], most documents first, then by name."""
    return [[k, v, round(c.get(k, 0))] for k, v in sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))]


def _crawl(root: Path, export: str | None) -> tuple[dict[str, collections.Counter], int]:
    """Outcomes by 类型 (网页, 附件, 图片, 按钮, 资料) from the export the library was built from, and its pager pages."""
    bag: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    pager = 0
    path = root / (export or "-") / "manifest.csv"
    if export and path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                o = outcome(r.get("结果", ""), r.get("分类", ""))
                bag[r.get("类型", "")][o] += 1
                if o == "got" and r.get("页别") == "列表翻页":
                    pager += 1
    return bag, pager


def site_checks(path: Path, built_at: str | None) -> tuple[dict[str, dict], str]:
    """Each site's reminders from the build's checks file, as the page reads them, and the build they are of; nothing
    when the file is missing, unreadable or of another build than the library on show (the checks run after a swap,
    so for a while the file is the last library's) 【Claude】. A site's entry: f the kinds flagged (CHECK_KINDS order),
    nw those new against the build before, st same_title documents, so [same-opening pages, pages with text], fr
    fragment files, em [empty pages, pages], se [found, sampled], sp files whose shared server day was not taken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, ""
    if not isinstance(data, dict) or not built_at or data.get("built_at") != built_at:
        return {}, ""
    new: dict[str, set] = collections.defaultdict(set)
    for pair in data.get("new") or []:
        if isinstance(pair, list) and len(pair) == 2:
            new[pair[0]].add(pair[1])
    out = {}
    for site, c in (data.get("sites") or {}).items():
        if not isinstance(c, dict):
            continue
        flags = [k for k in CHECK_KINDS if k in (c.get("flags") or [])]
        spikes = c.get("spike_dates") or 0
        if not flags and not spikes:
            continue
        out[site] = {"f": flags, "nw": [k for k in flags if k in new[site]], "st": c.get("same_title") or 0,
                     "so": c.get("same_opening") or [0, 0], "fr": c.get("fragments") or 0,
                     "em": c.get("empty_pages") or [0, 0], "se": c.get("self") or [0, 0], "sp": spikes}
    return out, built_at


def collect(root: Path, today: datetime.date | None = None, db: str = DB, report: str = REPORT,
            round1: str = ROUND1, checks: str = CHECKS) -> dict:
    """The page's data object (what research/ops_2026-11-11/build_atlas.py wrote into the page, now computed live)."""
    root = Path(root)
    today = today or datetime.date.today()
    tw = _tw()
    lib = sqlite3.connect(f"file:{root / db}?mode=ro", uri=True)
    try:
        lib.execute("PRAGMA query_only=1")
        meta = dict(lib.execute("select key, value from meta where key in ('built_at', 'schema_version')"))
        sites: dict[str, dict] = {}
        for site, name, unit, export, deleted, under in lib.execute(
                "select site, name, unit, export, deleted_notices, under_read from sites"):
            sites[site] = {"name": name or "", "unit": unit or "", "export": export, "del": deleted or 0,
                           "ur": under or 0, **_blank()}
        n_docs = 0
        site_of: dict[str, str] = {}
        event_chars: dict[str, int] = {}
        for doc_id, site, kc, kraw, has_text, chars, idate, ikind, tier, uline, grp in lib.execute(
                "select doc_id, site, kind_class, kind_raw, has_text, chars, ident_date, ident_kind, tier, unit_line, "
                "grp from docs"):
            n_docs += 1
            site_of[doc_id] = site
            n_chars = chars or 0
            s = sites.setdefault(site, {"name": "", "unit": "", "export": None, "del": 0, "ur": 0, **_blank()})
            s["kinds"][kc] += 1
            s["raw"][kraw or ""] += 1
            s["raw_c"][kraw or ""] += n_chars
            s["chars"][kc] += n_chars
            if tier == "background":
                s["bg"] += 1
            if not has_text:
                s["no_text"][kc] += 1
            m = CATS.search(uline or "")
            if m:
                for c in re.split(r"[、,，]", m.group(1)):
                    if c.strip():
                        s["cats"][c.strip()] += 1
                        s["cats_c"][c.strip()] += n_chars
            if grp:
                s["grp"][grp] += 1
                s["grp_c"][grp] += n_chars
            if kc == "event":
                event_chars[doc_id] = n_chars
            if not idate:
                s["undated"][kc] += 1
                s["undated_c"][kc] += n_chars
                continue
            if kc != "event" and (not s["min_date"] or idate < s["min_date"]):
                s["min_date"] = idate
            if kc in BASE:
                d = day_of(idate)
                if d is not None:
                    s["days"][d][BASE.index(kc)] += 1
                    s["days"][d][4 + BASE.index(kc)] += n_chars
                    slot = ROW_SLOT if kc == "attachment" and ikind == "published" else DATE_SLOT.get(ikind or "", 3)
                    s["dsrc"][slot] += 1
                    s["dsrc_c"][slot] += n_chars
        # an event has no identity date by design: its calendar days are the 'event_start' rows of the dates table. A
        # series merged from weekly repeats is one document on many days; its characters are shared out over those days,
        # so a site's days add up to its own characters 【Claude】
        days_of: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        for doc_id, site, date in lib.execute(
                "select x.doc_id, d.site, x.date from dates x join docs d on d.doc_id = x.doc_id "
                "where x.kind = 'event_start'"):
            days_of[doc_id].append((site, date))
        for doc_id, hits in days_of.items():
            share = event_chars.get(doc_id, 0) / len(hits)
            for site, date in hits:
                s, d = sites[site], day_of(date)
                if d is not None:
                    s["days"][d][3] += 1
                    s["days"][d][7] += share
                    if not s["event_min"] or date < s["event_min"]:
                        s["event_min"] = date
        units = {uid: (name, kind, parent) for uid, name, kind, parent in
                 lib.execute("select unit_id, name, kind, parent_id from units")}
        copies = [(keep, c.get("url", "")) for key, value in
                  lib.execute("select key, value from meta where key like 'copies:%'")
                  for keep in [key.split(":", 1)[1]] for c in json.loads(value or "[]")]
    finally:
        lib.close()

    unit_by_name = {name: uid for uid, (name, _, _) in units.items()}
    r1_list = json.loads((root / round1).read_text(encoding="utf-8"))["sites"]
    r1 = {s["site"]: s for s in r1_list}
    rep = json.loads((root / report).read_text(encoding="utf-8")) if (root / report).is_file() else {}

    def full_name(key: str) -> str:
        if key in r1:
            return tw(r1[key]["name"])
        return EXTRA_NAME.get(key) or sites[key]["name"] or key

    total = {k: sum(s["kinds"].values()) for k, s in sites.items()}
    order = sorted(sites, key=lambda k: (-total[k], k))
    fulls = {k: full_name(k) for k in order}
    shorts = {k: strip_note(fulls[k]) for k in order}
    dup = {n for n, c in collections.Counter(shorts.values()).items() if c > 1}
    groups_seen, unit_of = [], {}
    for k in order:
        uid = unit_by_name.get(sites[k]["unit"]) or (sites[k]["unit"] if sites[k]["unit"] in units else "")
        g = group_of(uid, units)
        unit_of[k] = (g, units[uid][0] if uid in units else sites[k]["unit"])
        groups_seen.append(g)
    groups = GROUP_ORDER + sorted(set(groups_seen) - set(GROUP_ORDER))

    horizon = datetime.date(today.year + 1, 12, 31)
    h_day, t_day = (horizon - EPOCH).days, (today - EPOCH).days
    records, daily, redirected = [], {}, {}
    checked, checked_at = site_checks(root / checks, meta.get("built_at"))
    for i, k in enumerate(order):
        s = sites[k]
        fm, fmc = [0] * 5, [0] * 5
        sec: collections.Counter = collections.Counter()
        sec_c: collections.Counter = collections.Counter()
        for rk, n in s["raw"].items():
            if rk.startswith("附件"):
                fm[fmt_slot(rk)] += n
                fmc[fmt_slot(rk)] += s["raw_c"][rk]
            elif rk in SECTION:
                sec[SECTION[rk]] += n
                sec_c[SECTION[rk]] += s["raw_c"][rk]
        bag, pager = _crawl(root, s["export"])
        redirected[k] = sum(c.get("redirect", 0) for c in bag.values())

        def out(*types: str, bag: dict = bag) -> list[int]:
            return [sum(bag.get(t, {}).get(o, 0) for t in types) for o in OUTCOMES]

        cats = _pairs(s["cats"], s["cats_c"])
        grp = _pairs(s["grp"], s["grp_c"])
        firsts = [d[:4] for d in (s["min_date"], s["event_min"]) if d]
        meta_r1 = r1.get(k, {})
        rows, fut = [], 0
        for day in sorted(s["days"]):
            n, p, a, e, cn, cp, ca, ce = s["days"][day]
            if day > h_day:  # far-future events (a weekly class repeated for years) are counted, not drawn
                fut += e
                e, ce = 0, 0
                if not (n or p or a):
                    continue
            rows.append([day, n, p, a, e, round(cn), round(cp), round(ca), round(ce)])
        seen = [r[0] for r in rows if (r[1] or r[2] or r[3]) and r[0] <= t_day]
        if rows:
            daily[str(i)] = rows
        records.append({
            "k": k, "s": short(k), "n": fulls[k] if shorts[k] in dup else shorts[k], "f": fulls[k],
            "o": finder_names(fulls[k], s["name"] if s["name"] != k else ""),
            "url": meta_r1.get("home") or "https://" + k,
            "imp": tw(meta_r1.get("imp") or ""), "g": groups.index(unit_of[k][0]), "u": unit_of[k][1],
            "t": [s["kinds"].get(x, 0) for x in KINDS], "c": [s["chars"].get(x, 0) for x in KINDS],
            "b": sum(s["kinds"].get(x, 0) for x in BASE), "ud": sum(s["undated"].get(x, 0) for x in BASE),
            "nt": sum(s["no_text"].get(x, 0) for x in BASE), "u3": [s["undated"].get(x, 0) for x in BASE],
            "uc": [s["undated_c"].get(x, 0) for x in BASE],
            "first": min(firsts) if firsts else "",
            "last": (EPOCH + datetime.timedelta(days=max(seen))).isoformat() if seen else "",
            "fm": fm, "fmc": fmc, "ds": s["dsrc"], "dsc": s["dsrc_c"], "pk": _pairs(sec, sec_c),
            "ct": cats if sum(r[1] for r in cats) >= 20 else [],
            "gr": grp, "cr": {"p": out("网页", "按钮"), "f": out("附件"), "i": out("图片")}, "pg": pager,
            "bg": s["bg"], "del": s["del"], "ur": s["ur"], "fut": fut, "note": "", "ck": checked.get(k),
        })
    # a site that kept no document: say where its pages went, the copies the build merged into another site's document,
    # or addresses that all redirect elsewhere (11-15: the three 品保 sites left empty send every address to the SSO
    # platform's own pages, which count under that site)
    index = {r["k"]: r for r in records}
    for r in records:
        if sum(r["t"]):
            continue
        went = collections.Counter(site_of[keep] for keep, url in copies if keep in site_of and owns(r["k"], url))
        other = next((index[o]["n"] for o, _ in went.most_common() if o in index and o != r["k"]), "")
        got = r["cr"]["p"][0] + r["cr"]["f"][0]
        r["note"] = (f"這個網站的網頁和「{other}」網站的重複，已併到那邊" if other
                     else "這個網站的網址都會跳到別的網站，內容算在那邊" if redirected[r["k"]] and not got
                     else "這個網站沒有收進庫的文件")

    # sorted on the list's own words (its importance, then its name), shown converted
    round2 = sorted((s for s in r1_list if s.get("round") == 2),
                    key=lambda s: (IMP_ORDER.index(s.get("imp") or "") if (s.get("imp") or "") in IMP_ORDER else 9,
                                   s["name"]))
    later = [{"n": n, "o": finder_names(n), "h": s.get("home", ""), "w": WHY.get(s.get("why", "")) or tw(s.get("why", "")),
              "m": tw(s.get("imp") or ""), "p": tw(s.get("platform") or "")} for s in round2 for n in [tw(s["name"])]]
    excluded: collections.Counter = collections.Counter()
    for reason, n in (rep.get("excluded") or {}).items():
        if isinstance(n, int):
            excluded[left_out(reason)] += n
    # documents the build merged into another one after assembly (pipeline-8): copies (its by_rule already counts the
    # platform twins) and the weekly instances of a recurring calendar event folded into one series
    dedupe = rep.get("dedupe") or {}
    for part, label in (("copies", COPIES), ("recurring", "每週重複的行事曆（合成一個系列）")):
        n = (dedupe.get(part) or {}).get("removed", 0) if isinstance(dedupe.get(part), dict) else 0
        if isinstance(n, int) and n:
            excluded[label] += n
    if sum(sum(r["t"]) for r in records) != n_docs:  # the page shows this text (through /stamp) when it happens
        raise ValueError("各網站的文件數加起來和庫裡的不一樣")
    days = [row[0] for rows in daily.values() for row in rows]
    return {
        "built": (meta.get("built_at") or "")[:16].replace("T", " "), "schema": meta.get("schema_version", ""),
        "today": today.isoformat(), "groups": groups,
        "groupTip": [GROUP_TIP.get(g, COLLEGE_TIP) for g in groups],
        "sites": records, "daily": daily, "dayMin": min(days) if days else t_day, "dayMax": h_day,
        "later": later, "listed": len(records) + len(later), "excluded": excluded.most_common(),
        "checked": checked_at[:16].replace("T", " "),
    }
