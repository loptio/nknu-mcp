"""The owner, 11-15: 「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」.

The crawler's 2989f60 export carries the words of the announcement platform's 「公告單位：」 cell (公告单位). A notice
whose page states its publishing unit stands under that unit (a section of an office as the office with the section as
its 組別), the platform's files hanging on it follow, and the copy merge takes the statement for who published a text
among the copies of the nearest kind of owner (an office's own site still comes first): only that unit's copies stand
for the group. Units, sentences and names are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3

import pytest

from nknu.content import build, sites
from nknu.content.assemble import assemble
from nknu.content.corpus import load_corpus
from nknu.content.documents import build_documents, one_per_id
from nknu.content.units import Unit, name_unit
from tests.content import test_assemble as ta
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_review_1114c as tr
from tests.content import test_twins_1115 as tw

SCHOOL = tr.SCHOOL
AFFAIRS, LIFE, SOFT, COLLEGE = "學生事務處", tr.OFFICE, tr.DEPT, "科技學院"  # LIFE: 生活輔導組, a section of AFFAIRS
BY_LIFE, BY_SOFT = AFFAIRS + LIFE, COLLEGE + SOFT  # as the platform writes them: the names down the tree run together
CHEM_PAGE, CHEM_SITE = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=203012&page=", "sso.nknu.edu.tw/site/203012"
CHEM = sites.unit_of_site(CHEM_SITE)  # another department's site


def unit(name: str, kind: str, parent: str | None, aliases: tuple[str, ...] = ()) -> Unit:
    return Unit(name, name, kind, parent, None, aliases)


TREE = tr.UNITS | {u.unit_id: u for u in (
    unit("教務處", "行政单位", SCHOOL), unit("和平教務組", "行政组别", "教務處", ("和平校區教務組",)),
    unit("總務處", "行政单位", SCHOOL), unit("出納組", "行政组别", "總務處", ("總務處 出納組",)),
    unit("校長室", "行政单位", SCHOOL), unit("圖書資訊處", "行政单位", SCHOOL, ("圖資處",)),
    unit("校安中心", "行政单位", SCHOOL), unit("通識教育中心", "中心", SCHOOL),
    unit("理學院", "学院", SCHOOL), unit(CHEM, "系所", "理學院"))}


# ---------------------------------------------------------------- the name a page writes, placed in the tree

@pytest.mark.parametrize(("written", "placed"), [
    ("教務處", ("教務處", "")),  # the whole name
    ("總務處出納組", ("出納組", "")),  # an alias, written with a space in the list
    ("校安中心(原軍訓室)", ("校安中心", "")),  # a trailing note in brackets is no part of the name
    ("教務處和平教務組", ("和平教務組", "")),  # the names down the tree run together: the last one published it
    ("教务处 和平校区教务组", ("和平教務組", "")),  # Simplified, a space, an alias
    (BY_SOFT, (SOFT, "")),
    ("其他教學研究中心通識教育中心", ("通識教育中心", "")),  # after a heading of the platform's own that ends like a unit
    ("副校長室", (None, "副校長室")),  # 校長室 after 「副」 is another office: what stands before must read as a unit
    ("學生事務處學生宿舍組", (AFFAIRS, "學生宿舍組")),  # a part the tree lacks: the unit, and the rest as written
    ("校長室圖資處", ("校長室", "圖資處")),  # the unit at the end is not under the one before it: no path down the tree
    ("校長室圖資處測試用", ("校長室", "圖資處測試用")),
    ("其他教學研究中心師資培育中心", (None, "其他教學研究中心師資培育中心")),  # no unit of the tree at either end
    ("", (None, "")), (None, (None, "")), ("  \n ", (None, "")),
])
def test_a_written_name_is_placed_in_the_tree(written, placed):
    assert name_unit(written, TREE) == placed


# ---------------------------------------------------------------- from the export to the document

def test_assemble_carries_the_exports_column(tmp_path):
    rows = [{"type": "网页", "url": tc.NEWS + "AB12", "file": "pages/0001.html", "data": b"<html></html>",
             "title": "停修公告"},
            {"type": "网页", "url": tc.NEWS + "CD34", "file": "pages/0002.html", "data": b"<html></html>",
             "title": "加退選公告"}]
    export = ta.write_export(tmp_path / "export", rows, tc.NEWS_SITE, "全校公告中心")
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    manifest["项目"][0]["公告单位"] = " 教務處\n和平教務組 "  # the cell as the page writes it; the other row has none
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    totext = ta.fake_totext(tmp_path, export, {"1": (tm.PAGE, "停修公告\n請見附件"), "2": (tm.PAGE, "加退選公告\n請見附件")})
    out = tmp_path / "out" / "corpus.json"
    assemble(export, totext, out, fallback=None)
    assert [r["publisher"] for r in json.loads(out.read_text(encoding="utf-8"))] == ["教務處 和平教務組", ""]
    assert [r.publisher for r in load_corpus(out)] == ["教務處 和平教務組", ""]


def test_one_page_in_two_exports_keeps_the_statement_either_read():
    # 學務處's export brought the platform's page too; the record kept is the one fetched later, whichever states it
    old = tc.rec("p1", "detail:sso_news", tc.NEWS + "AB12", "公告", tc.LONG, tc.NEWS_SITE, publisher=BY_LIFE)
    new = tc.rec("p1", "detail:sso_news", tc.NEWS + "AB12", "公告", tc.LONG, tc.WEB_SITE,
                 fetched="2026-11-02T04:41:00+08:00")
    kept, _ = one_per_id([old, new])
    assert [(r.site, r.publisher) for r in kept] == [(tc.WEB_SITE, BY_LIFE)]
    docs, _ = build_documents(kept, {}, [])
    assert (docs[0].stated_unit, docs[0].unit, docs[0].unit_stated) == (BY_LIFE, LIFE, False)  # the build names it


# ---------------------------------------------------------------- the unit and the 組別 it gives

def notice(rid: str, publisher: str, text: str = tc.LONG, title: str = "公告", day: str = "2026-03-01"):
    return tc.rec(rid, "detail:sso_news", tc.NEWS + rid, title, text, tc.NEWS_SITE, day, publisher=publisher)


def stated(records, units=TREE):
    docs, _ = build_documents(records, {}, [])
    report = build._stated_units(docs, units)
    return {d.doc_id: (d.unit, d.grp, d.unit_stated) for d in docs}, report, docs


def test_a_stated_notice_stands_under_the_unit_its_page_names():
    got, report, docs = stated([notice("p1", BY_LIFE), notice("p2", BY_SOFT), notice("p3", "教務處"),
                                notice("p4", "學生事務處學生宿舍組"), notice("p5", "副校長室"), notice("p6", "")])
    assert got == {"dp1": (AFFAIRS, LIFE, True),  # a section of an office: the office, the section as its 組別
                   "dp2": (SOFT, "", True), "dp3": ("教務處", "", True),
                   "dp4": (AFFAIRS, "學生宿舍組", True),  # the part the tree lacks is the 組別 as written
                   "dp5": ("副校長室", "", True),  # no unit of the tree: as the page writes it
                   "dp6": (tr.PLATFORM, "", False)}  # no statement: the platform's placeholder, as before
    assert report == {"on": True, "stated": 5, "placed": 4, "as_written": {"副校長室": 1},
                      "parts_as_written": {"學生宿舍組": 1}, "changed": 5, "files": 0,
                      "units": {AFFAIRS: 2, SOFT: 1, "教務處": 1, "副校長室": 1}}
    # a short link another site writes for the platform's notice is a link document, and the page it lands on states
    # its publisher all the same (教务处's export, 11-15: two reurl.cc rows)
    short = tc.rec("p7", "外站:reurl.cc", "https://reurl.cc/x1", "公告", tc.SHORT, tc.ACA_SITE, publisher=BY_SOFT)
    plain = tc.rec("p8", "外站:reurl.cc", "https://reurl.cc/x2", "公告", tc.SHORT, tc.ACA_SITE)
    assert stated([short, plain])[0] == {"dp7": (SOFT, "", True), "dp8": ("教務處", "", False)}
    by_id = {d.doc_id: d for d in docs}
    # whose copy it is no longer stops at the platform's placeholder: an office's, a department's, an unknown unit's
    assert [build._owner_level(by_id[i], TREE) for i in ("dp1", "dp2", "dp5", "dp6")] == [0, 1, 2, 2]
    assert build.publisher_of(*got["dp1"][:2], TREE) == LIFE and build.publisher_of(*got["dp4"][:2], TREE) == AFFAIRS


def test_the_platforms_files_follow_their_notice_and_a_units_own_file_does_not():
    posted = notice("p1", BY_LIFE)
    on_platform = tc.rec("a1", tc.PDF, "https://sso.nknu.edu.tw/news/files/a1.pdf", "簡章.pdf", tc.OTHER, tc.NEWS_SITE,
                         linked=(posted.url,))
    no_site = tc.rec("a2", tc.PDF, "https://sso.nknu.edu.tw/webservices/a2.pdf", "報名表.pdf", tc.SHORT, tc.NEWS_SITE,
                     linked=(posted.url,))
    own = tc.rec("a3", tc.PDF, tc.ACA + "uploadfile/a3.pdf", "要點.pdf", tc.SHORT * 2, tc.NEWS_SITE,
                 linked=(posted.url,))
    loose = tc.rec("a4", tc.PDF, "https://sso.nknu.edu.tw/news/files/a4.pdf", "其他.pdf", tc.SHORT * 3, tc.NEWS_SITE)
    got, report, _ = stated([posted, on_platform, no_site, own, loose])
    assert got["da1"] == got["da2"] == (AFFAIRS, LIFE, True)
    assert got["da3"] == ("教務處", "", False)  # on 教务处's own site: its own unit's
    assert got["da4"] == (tr.PLATFORM, "", False) and report["files"] == 2  # hanging on no stated notice


def test_without_a_tree_nothing_changes_and_the_report_still_counts():
    got, report, _ = stated([notice("p1", BY_LIFE)], None)
    assert got == {"dp1": (tr.PLATFORM, "", False)}
    assert report == {"on": False, "stated": 1, "placed": 0, "as_written": {}, "parts_as_written": {},
                      "changed": 0, "files": 0, "units": {}}


# ---------------------------------------------------------------- whose copy stands for a text

def merged(records, units=TREE):
    docs, excluded = build_documents(records, {}, [])
    build._stated_units(docs, units)
    seen: dict = {}
    kept, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, units, seen)
    return [r.doc.doc_id for r in kept], {k: [c.doc.doc_id for c in v] for k, v in copies.items()}, seen


def office_copy(rid: str = "p2", day: str = "2026-03-01", text: str = tc.LONG):
    return tc.rec(rid, "detail:sso_webidx", tc.WEB + rid, "生輔組公告", text, tc.WEB_SITE, day)


def dept_copy(rid: str = "p3", day: str = "2026-02-01", text: str = tc.LONG):
    return tc.rec(rid, "detail:sso_clv2", tc.CHAIR + rid, "轉知", text, tc.CHAIR_SITE, day)


def test_the_platforms_notice_of_an_office_is_kept_before_a_departments_copy():
    # until 11-15 the department's copy stood for the text, the platform's unit being a placeholder
    assert merged([notice("p1", BY_LIFE), dept_copy()])[:2] == (["dp1"], {"dp1": ["dp3"]})
    assert merged([notice("p1", ""), dept_copy()])[:2] == (["dp3"], {"dp3": ["dp1"]})  # no statement: as before


def test_the_publishers_own_site_comes_before_the_platforms_notice():
    kept, copies, _ = merged([notice("p1", BY_LIFE), office_copy(), dept_copy()])
    assert kept == ["dp2"] and sorted(copies["dp2"]) == ["dp1", "dp3"]


def test_an_offices_own_copy_still_comes_before_a_department_that_posted_the_text():
    # the statement says who put that notice on the platform: a department posting an office's text there is no more
    # its source than one copying it to its own site, so the kind of owner decides first, as since 11-14
    assert merged([notice("p1", BY_SOFT), office_copy()])[:2] == (["dp2"], {"dp2": ["dp1"]})
    kept, copies, _ = merged([notice("p1", BY_SOFT), office_copy(), dept_copy()])
    assert kept == ["dp2"] and sorted(copies["dp2"]) == ["dp1", "dp3"]


def test_among_departments_the_one_the_platform_names_stands_for_the_text():
    other = tc.rec("p4", "detail:sso_clv2", CHEM_PAGE + "4", "轉知", tc.LONG, CHEM_SITE, "2026-02-01")
    # 化學系's own site holds the text and the platform says 軟體系 published it: the platform's notice, as 軟體系's
    assert merged([notice("p1", BY_SOFT), other])[:2] == (["dp1"], {"dp1": ["dp4"]})
    # without the statement the platform's copy is a placeholder's, and the department's own site stands for it
    assert merged([notice("p1", ""), other])[:2] == (["dp4"], {"dp4": ["dp1"]})
    # 軟體系's own site holds it too: that copy, at its own address
    kept, copies, _ = merged([notice("p1", BY_SOFT), other, dept_copy()])
    assert kept == ["dp3"] and sorted(copies["dp3"]) == ["dp1", "dp4"]


def test_a_department_and_its_college_are_two_houses_and_a_section_and_its_office_one():
    assert build._house(SOFT, TREE) == SOFT and build._house(COLLEGE, TREE) == COLLEGE
    assert build._house(LIFE, TREE) == build._house(AFFAIRS, TREE) == AFFAIRS
    assert build._house(" 副校長室 ", TREE) == "副校長室"  # as written when the tree does not have it


def test_a_departments_longer_version_of_its_own_notice_is_merged():
    longer = tw.ON_UNIT + "\n" + tw.EXTRA
    own = tc.rec("p1", "detail:sso_news", tw.P_URL, "【公告】圖書館暑假開放時間", tw.ON_PLATFORM, tc.NEWS_SITE, tw.DAY,
                 publisher=BY_SOFT)
    kept, copies, seen = merged([own, tw.department(longer)])
    assert (kept, copies) == (["dp3"], {"dp3": ["dp1"]})  # the fuller one, on the department's own site
    assert (seen["department_longer"], seen["fuller"]) == (0, {"platform": 0, "unit": 1})
    # stated to be an office's, or not stated: the department's longer page is its own version, left alone as before
    for publisher in (BY_LIFE, ""):
        other = tc.rec("p1", "detail:sso_news", tw.P_URL, "【公告】圖書館暑假開放時間", tw.ON_PLATFORM, tc.NEWS_SITE,
                       tw.DAY, publisher=publisher)
        kept, copies, seen = merged([other, tw.department(longer)])
        assert (kept, copies, seen["department_longer"]) == (["dp1", "dp3"], {}, 1)


# ---------------------------------------------------------------- the build

N_NOTICE, C_NOTICE = tc.NEWS + "AB12", tc.CHAIR + "1"


def inputs(root, publisher: str = BY_LIFE):
    news = tc.site_input(root / "news", tc.NEWS_SITE, "全校公告中心", [
        {"kind": "detail:sso_news", "url": N_NOTICE, "title": "宿舍熱水供應公告", "text": tc.OTHER,
         "publisher": publisher, **tc.dated("2026-03-01")}])
    chair = tc.site_input(root / "chair", tc.CHAIR_SITE, "软体系", [
        {"kind": "detail:sso_clv2", "url": C_NOTICE, "title": "轉知", "text": tc.OTHER, **tc.dated("2026-03-02")}])
    return [news, chair]


def test_the_build_names_the_publisher_and_says_so(tmp_path):
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=inputs(tmp_path))
    assert report.publishers == {"on": True, "stated": 1, "placed": 1, "as_written": {}, "parts_as_written": {},
                                 "changed": 1, "files": 0, "units": {AFFAIRS: 1}}
    assert any("照网页写的公告单位定发布单位 1 份（单位树上认得 1、照原文写 0" in part for part in build._v6_words(report))
    news, chair = tc.doc_id(N_NOTICE), tc.doc_id(C_NOTICE)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('stated_units', 'label_titles')")) == {
            "stated_units": "on", "label_titles": "off"}
        assert json.loads(conn.execute("SELECT value FROM meta WHERE key = ?",
                                       (build.PUBLISHERS_KEY,)).fetchone()[0])["stated"] == 1
        # the platform's notice stands for the text, under the office that published it, with its section
        assert conn.execute("SELECT doc_id, unit, grp FROM docs WHERE kind_class = 'notice'").fetchall() == [
            (news, AFFAIRS, LIFE)]
        assert conn.execute("SELECT doc_id, why FROM doc_alias WHERE alias = ?", (chair,)).fetchone() == (news, "copy")
        line = conn.execute("SELECT unit_line FROM docs WHERE doc_id = ?", (news,)).fetchone()[0]
        assert line.startswith(build.UNIT_LABEL + AFFAIRS) and LIFE in line
        assert conn.execute("SELECT unit_id FROM doc_scope WHERE doc_id = ? AND relation = 'publisher'",
                            (news,)).fetchone() == (LIFE,)
    finally:
        conn.close()
