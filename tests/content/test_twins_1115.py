"""The owner, 11-14 23:55: 「请接着把"公告平台和单位网站各一份"的那 191 对合并做掉，再重建一次（约 25 分钟）并更新正式库。」

A notice on a shared platform (the announcement system) and a notice of the same day on a unit's own site are one
document when, page furniture, punctuation and list numbers aside, every line of one is found in the other
(build._platform_twins); when one says more, the fuller one is kept (build._join_twins, build._merge_copies); two that
each hold a line the other lacks stay two documents. --no-platform-twins builds as before. The copies' list says
which copy's words differ (wording_differs), and respond explains it once.
The sentences and names are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from nknu.content import build
from nknu.content.documents import build_documents
from nknu.retrieval import respond
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import search_docs
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_review_1114c as tr

DAY = "2026-06-20"
P_URL, U_URL, D_URL = tc.NEWS + "AB12", tc.WEB + "1", tc.CHAIR + "1"
L1 = "圖書館自七月一日起調整開放時間，週一至週五上午九時至下午五時開放。"
L2 = "借書期限順延至開學後第一週，逾期不另計罰款。"
L3 = "暑假期間自修室照常開放，請攜帶學生證入館。"
EXTRA = "另外，四樓視聽室因整修暫停開放至八月底。"
# the platform's page: numbered lines; the unit's page: its own date, the same words punctuated its own way with one
# line broken in two and no numbers (an <ol>), and the attachment list with sizes
ON_PLATFORM = f"一、{L1}\n二、{L2}\n三、{L3}\n開放時間表.pdf"
ON_UNIT = ("2026-06-20\n圖書館自七月一日起調整開放時間,\n週一至週五上午九時至下午五時開放\n"
           "借書期限順延至開學後第一週,逾期不另計罰款\n暑假期間自修室照常開放，請攜帶學生證入館\n開放時間表.pdf (152.5 Kb)")


def platform(text: str = ON_PLATFORM, title: str = "【公告】圖書館暑假開放時間", day: str = DAY, rid: str = "p1"):
    return tc.rec(rid, "detail:sso_news", P_URL, title, text, tc.NEWS_SITE, day)


def office(text: str = ON_UNIT, title: str = "圖書館暑假開放時間", day: str = DAY, rid: str = "p2"):
    return tc.rec(rid, "detail:sso_webidx", U_URL, title, text, tc.WEB_SITE, day)


def department(text: str = ON_UNIT, title: str = "轉知：圖書館暑假開放時間", day: str = DAY, rid: str = "p3"):
    return tc.rec(rid, "detail:sso_clv2", D_URL, title, text, tc.CHAIR_SITE, day)


def merged(records, twins: bool = True, units=tr.UNITS):
    """The ids kept, the copies under each and what the twins counted (build._merge_copies' seen)."""
    docs, excluded = build_documents(records, {}, [])
    seen: dict = {}
    kept, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, build.COPY_TEXT, True,
                                       units, twins, seen)
    return [r.doc.doc_id for r in kept], {k: [c.doc.doc_id for c in v] for k, v in copies.items()}, seen, excluded


# ---------------------------------------------------------------- what is compared

def test_twin_lines_keep_what_a_notice_says():
    day = date(2026, 6, 20)
    assert build._twin_lines(ON_PLATFORM, day) == build._twin_lines(f"{L1}\n{L2}\n{L3}", day)
    said = build._twin_lines("# 一、申請期限：115年6月30日（星期二）止。\n(二) 逾期不受理\n3. 請攜帶學生證", day)
    assert said == ["申请期限115年6月30日星期二止", "逾期不受理", "请携带学生证"]
    # the notice's own date on a line of its own is the page's, in the Republic's years too; any other date is said
    assert build._twin_lines("2026-06-20\n115年6月20日\n115/06/20 (六)\n2026-06-30\n受理至 6 月 30 日", day) == [
        "20260630", "受理至6月30日"]
    assert build._twin_lines("2026-06-20", None) == ["20260620"]  # an undated notice has no date of its own
    # a file's name is the attachment list's; a sentence that names a file is not
    assert build._twin_lines("報名表.docx\n簡章.PDF (1.2 Mb)\n請下載報名表.docx填寫後送出", day) == ["请下载报名表docx填写后送出"]
    # a figure or a 「一、二年級」 at the start of a line is no list number
    assert build._twin_lines("3.5 公里路跑\n一、二年級學生優先\n10月4日起", day) == ["35公里路跑", "一二年级学生优先", "10月4日起"]


# ---------------------------------------------------------------- which notices are one

def test_one_notice_on_the_platform_and_on_an_offices_site_is_one_document():
    kept, copies, seen, excluded = merged([platform(), office()])
    assert (kept, copies) == (["dp2"], {"dp2": ["dp1"]})  # the office's own site, as for copies of one text
    assert {k: seen[k] for k in ("same", "fuller", "groups", "removed", "apart", "department_longer")} == {
        "same": 1, "fuller": {"platform": 0, "unit": 0}, "groups": 1, "removed": 1, "apart": 0, "department_longer": 0}
    assert seen["linked"] == {"dp1", "dp2"} and excluded == {"detail:sso_news" + build.COPY: 1}
    # --no-platform-twins: two documents, as before (their texts differ, so the text rule leaves them)
    assert merged([platform(), office()], twins=False)[:3] == (["dp1", "dp2"], {}, {"names_carried": tc.NO_NAMES})


def test_a_changed_figure_keeps_two_notices_apart_and_is_listed():
    later = ON_UNIT.replace("七月一日", "七月二日")
    kept, copies, seen, _ = merged([platform(), office(later)])
    assert (kept, copies) == (["dp1", "dp2"], {}) and (seen["same"], seen["apart"]) == (0, 0)  # other titles: not listed
    kept, copies, seen, _ = merged([platform(title="圖書館暑假開放時間"), office(later)])
    assert (kept, copies, seen["apart"]) == (["dp1", "dp2"], {}, 1)
    assert seen["apart_shown"] == [{"date": DAY, "title": "圖書館暑假開放時間", "platform": "dp1", "unit": "dp2"}]


def test_the_fuller_copy_is_the_one_kept():
    # the platform's says one thing more: it stays, though a unit's own site comes first among equals
    kept, copies, seen, _ = merged([platform(ON_PLATFORM + "\n" + EXTRA), office()])
    assert (kept, copies) == (["dp1"], {"dp1": ["dp2"]}) and seen["fuller"] == {"platform": 1, "unit": 0}
    # the unit's says more: it stays
    kept, copies, seen, _ = merged([platform(), office(ON_UNIT + "\n" + EXTRA)])
    assert (kept, copies) == (["dp2"], {"dp2": ["dp1"]}) and seen["fuller"] == {"platform": 0, "unit": 1}
    assert (seen["same"], seen["groups"], seen["removed"]) == (0, 1, 1)


def test_another_day_another_kind_or_two_units_sites_are_no_twins():
    assert merged([platform(), office(day="2026-06-21")])[:2] == (["dp1", "dp2"], {})
    assert merged([platform(day=""), office(day="")])[:2] == (["dp1", "dp2"], {})  # undated: nothing says one posting
    assert merged([office(), department()])[:2] == (["dp2", "dp3"], {})  # no platform's copy between them
    file = tc.rec("p4", tc.PDF, tc.ACA + "uploadfile/news/1/開放時間.pdf", "開放時間.pdf", ON_UNIT, tc.ACA_SITE)
    kept, copies, _, _ = merged([platform(), file])
    assert copies == {} and len(kept) == 2  # a file is no notice


def test_a_notice_that_says_little_needs_the_same_title():
    brief = "詳如附件，請同學踴躍報名。"  # under TWIN_MIN_CHARS
    assert len(build._twin_lines(brief, None)[0]) < build.TWIN_MIN_CHARS
    title = "暑期游泳訓練班報名公告"
    assert merged([platform(brief, title), office(brief + "\n2026-06-20", title)])[:2] == (["dp2"], {"dp2": ["dp1"]})
    assert merged([platform(brief, title), office(brief, "暑期籃球營報名公告")])[:2] == (["dp1", "dp2"], {})
    # a title too short to be one is no evidence either
    assert merged([platform(brief, "公告"), office(brief, "公告")])[:2] == (["dp1", "dp2"], {})
    # an empty page under the same title is inside the full notice
    kept, copies, seen, _ = merged([platform(f"{L1}\n{L2}", title), office("公告連結", title)])
    assert (kept, copies) == (["dp1"], {"dp1": ["dp2"]}) or seen["apart"] == 1


def test_a_line_many_notices_of_a_site_carry_is_page_furniture():
    menu = "首頁 > 最新消息 > 生活輔導組公告"
    others = [tc.rec(f"q{i}", "detail:sso_webidx", tc.WEB + str(20 + i), f"第{i}則公告",
                     f"{menu}\n第{i}週宿舍檢查結果已公布，請各寢室長至服務台領取檢查表並於週五前繳回。", tc.WEB_SITE,
                     f"2026-05-{10 + i:02d}") for i in range(build.TWIN_TEMPLATE_MIN - 1)]
    ours = office(f"{menu}\n{ON_UNIT}")
    _, copies, seen, _ = merged([platform(), ours, *others])
    assert copies == {"dp2": ["dp1"]} and (seen["same"], seen["fuller"]["unit"]) == (1, 0)
    # carried by too few notices it is something the unit's copy says: the unit's is then the fuller one
    _, copies, seen, _ = merged([platform(), ours, *others[:2]])
    assert copies == {"dp2": ["dp1"]} and (seen["same"], seen["fuller"]["unit"]) == (0, 1)


OWN = "本系同學請向系辦登記暑假借用研究室的時段。"  # what a department adds for its own students


def test_a_departments_longer_version_is_left_alone():
    longer = department(f"{ON_UNIT}\n{OWN}")
    kept, copies, seen, _ = merged([platform(), longer])
    assert (kept, copies) == (["dp1", "dp3"], {})  # it would stand for the school's notice under the department's name
    assert (seen["department_longer"], seen["fuller"], seen["groups"]) == (1, {"platform": 0, "unit": 0}, 0)
    assert seen["department_longer_shown"] == [{"date": DAY, "title": "【公告】圖書館暑假開放時間", "platform": "dp1",
                                                 "unit": "dp3"}]
    # the same words on the department's site are the same notice, the department's kept as for copies of one text
    assert merged([platform(), department()])[:2] == (["dp3"], {"dp3": ["dp1"]})
    # the department's shorter copy goes under the platform's fuller one
    kept, copies, seen, _ = merged([platform(f"{ON_PLATFORM}\n{EXTRA}"), department()])
    assert (kept, copies, seen["department_longer"]) == (["dp1"], {"dp1": ["dp3"]}, 0)
    # without the unit tree nobody is known to be a department
    assert merged([platform(), longer], units=None)[:2] == (["dp3"], {"dp3": ["dp1"]})


def test_a_short_notice_inside_two_units_longer_ones_joins_one_of_them():
    # the office adds one line, the department another: the department's longer version is left alone
    at_office = office(f"{ON_UNIT}\n{EXTRA}")
    kept, copies, seen, _ = merged([platform(), department(f"{ON_UNIT}\n{OWN}"), at_office])
    assert (kept, copies) == (["dp3", "dp2"], {"dp2": ["dp1"]})
    assert (seen["fuller"], seen["department_longer"]) == ({"platform": 0, "unit": 1}, 1)
    # two offices' longer ones (教務處's site is no unit of this tree: no teaching unit either): joining all three
    # would drop what one of the two says, so the platform's goes under the nearest owner's and the other stays
    at_aca = tc.rec("p5", "newsdetail", tc.ACA + "newsdetail.aspx?nid=7&gi=2", "圖書館暑假開放時間",
                    f"{ON_UNIT}\n教務處服務台暑假期間照常受理各項申請。", tc.ACA_SITE, DAY)
    kept, copies, seen, _ = merged([platform(), at_aca, at_office])
    assert (kept, copies) == (["dp5", "dp2"], {"dp2": ["dp1"]})
    assert seen["fuller"] == {"platform": 0, "unit": 2} and (seen["groups"], seen["removed"]) == (1, 1)
    # the same notice at both: all three are one, the office's kept
    kept, copies, seen, _ = merged([platform(), department(), office()])
    assert (kept, copies) == (["dp2"], {"dp2": ["dp1", "dp3"]}) or (kept, copies) == (["dp2"], {"dp2": ["dp3", "dp1"]})
    assert seen["same"] == 2 and seen["removed"] == 2


def test_twins_join_the_copies_of_one_text():
    # the platform's notice word for word on a department's site (the text rule's copy, 200 characters and more) and
    # its twin on the office's: one group, counted as a twin's
    lines = [L1, L2, L3, EXTRA, L1, L2, L3, EXTRA]
    long_platform = "\n".join(f"{n}、{line}" for n, line in zip("一二三四五六七八", lines))
    long_unit = "\n".join(lines).replace("，", ",")
    assert len(build._bare(long_platform)) >= build.COPY_MIN_CHARS
    records = [platform(long_platform), office(long_unit), department(long_platform, day="2026-06-25")]
    docs, excluded = build_documents(records, {}, [])
    seen: dict = {}
    rows, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, build.COPY_TEXT, True,
                                       tr.UNITS, True, seen)
    assert [r.doc.doc_id for r in rows] == ["dp2"] and {c.doc.doc_id for c in copies["dp2"]} == {"dp1", "dp3"}
    assert (seen["groups"], seen["removed"]) == (1, 1)  # the department's copy the text rule removed already
    report = build._copies_report(rows, copies, build.COPY_TEXT, linked=seen["linked"])
    assert report["by_rule"] == {"text": {"groups": 0, "removed": 0}, "title": {"groups": 0, "removed": 0},
                                 "twin": {"groups": 1, "removed": 2}}
    listed = json.loads(build._copies_meta(rows, copies)["copies:dp2"])
    assert {c["id"]: c.get("wording_differs") for c in listed} == {"dp1": True, "dp3": True}
    # without twins the two of one text are a group of the text rule, and neither copy's words differ
    rows, copies = build._merge_copies(build._rows(build_documents(records, {}, [])[0], {}, {}), lambda _: None, {},
                                       build.COPY_TEXT, True, tr.UNITS, False)
    assert sorted(r.doc.doc_id for r in rows) == ["dp2", "dp3"] and [c.doc.doc_id for c in copies["dp3"]] == ["dp1"]
    assert "wording_differs" not in json.loads(build._copies_meta(rows, copies)["copies:dp3"])[0]


# ---------------------------------------------------------------- the build and the response

N_NOTICE, A_NOTICE = tc.NEWS + "AB12", tc.ACA + "newsdetail.aspx?nid=1&gi=2"
IDS = {"news": tc.doc_id(N_NOTICE), "aca": tc.doc_id(A_NOTICE)}


def twin_inputs(tmp_path):
    aca = tc.site_input(tmp_path / "aca", tc.ACA_SITE, "教务处", [
        {"kind": "newsdetail", "url": A_NOTICE, "title": "圖書館暑假開放時間", "text": ON_UNIT, **tc.dated(DAY)},
        {"kind": "page", "url": tc.ACA + "page.aspx?pn=50", "title": "社團博覽會", "text": tc.SHORT}])
    news = tc.site_input(tmp_path / "news", tc.NEWS_SITE, "全校公告中心", [
        {"kind": "detail:sso_news", "url": N_NOTICE, "title": "【公告】圖書館暑假開放時間", "text": ON_PLATFORM,
         **tc.dated(DAY)}])
    return [aca, news]


def test_the_build_merges_them_says_so_and_can_leave_them(tmp_path, capsys):
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=twin_inputs(tmp_path))
    twins = report.dedupe["twins"]
    assert twins == {"on": True, "groups": 1, "removed": 1, "same": 1, "fuller": {"platform": 0, "unit": 0},
                     "department_longer": 0, "department_longer_shown": [], "apart": 0, "apart_shown": []}
    assert report.dedupe["copies"]["by_rule"]["twin"] == {"groups": 1, "removed": 1}
    assert any("公告平台和单位网站各一份的公告合了 1 组、少 1 份" in part for part in build._dedupe_words(report.dedupe))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'platform_twins'").fetchone() == ("on",)
        assert [r[0] for r in conn.execute("SELECT doc_id FROM docs WHERE kind_class = 'notice'")] == [IDS["aca"]]
        assert conn.execute("SELECT doc_id, why FROM doc_alias WHERE alias = ?", (IDS["news"],)).fetchone() == (
            IDS["aca"], "copy")
        resp = build_response(conn, search_docs(conn, "圖書館暑假開放時間", today=tc.TODAY, aliases=[]),
                              today=tc.TODAY)
        first = resp["results"][0]
        assert first["id"] == IDS["aca"]
        assert first["copies"] == [{"id": IDS["news"], "url": N_NOTICE, "date": DAY, "date_type": "發布",
                                    "wording_differs": True}]
        assert respond.NOTE_COPIES in resp["notes"] and respond.NOTE_WORDING in resp["notes"]
    finally:
        conn.close()
    # --no-platform-twins: both stay, and the report says the switch is off
    left = tmp_path / "db" / "y.db"
    report = tm.run(tm.tree_files(tmp_path / "tree2"), left, inputs=twin_inputs(tmp_path / "again"),
                    platform_twins=False)
    assert report.dedupe["twins"] == {"on": False}
    assert "公告平台和单位网站各一份的公告没合并（--no-platform-twins）" in build._dedupe_words(report.dedupe)
    conn = sqlite3.connect(f"file:{left}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'platform_twins'").fetchone() == ("off",)
        assert conn.execute("SELECT COUNT(*) FROM docs WHERE kind_class = 'notice'").fetchone() == (2,)
    finally:
        conn.close()


def test_a_copy_of_one_text_does_not_say_its_words_differ():
    copies = [{"id": "d1", "url": "https://example.invalid/a", "date": "2026-06-20"}]
    assert respond._copies_notes(copies) == [respond.NOTE_COPIES]
    assert respond._copies_notes([{**copies[0], "wording_differs": True}]) == [respond.NOTE_COPIES, respond.NOTE_WORDING]
    assert "wording_differs" in respond.NOTE_WORDING and respond.NOTE_WORDING.startswith("【系統】")
