"""11-18 (design docs/superpowers/specs/2026-11-18-course-schedules-design.md §五, after the probe): the course query of
sso.nknu.edu.tw/stu refuses a blank course name, and a department's 「全年級選課用」 class is not the sum of its year
classes, so the form is walked class by class (by day and by night, each kind of programme, each department, each
class: one search a class). The walk keeps no answer: every row of a search links the class's own address
(scheduleDepartment.aspx?cDES=…, the same table by a plain GET), and those addresses are handed to the crawler as
pages of the site (seed), read as any page from then on. A teacher's and a room's table are the same courses again:
never followed. The form's own controls are no gap of a class page. Made-up departments, classes and courses only."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import crawler as crawl_mod
from nknu.archive import export, links, profiles, round1, schedules, scope
from nknu.archive.frontier import Frontier

BASE = "https://sso.nknu.edu.tw/stu/"
SITE = "sso.nknu.edu.tw/stu"
P = "ctl00$phMain$"

# the school as the fake server knows it: by day or night, by kind of programme, each department's classes and each
# class's courses (a class with none answers no row)
SCHOOL = {
    ("D", "1"): {"201": ("甲學系", {"A000": ("甲系全年級選課用", ["A001"]), "A101": ("甲系一年級", ["A101", "A102"])}),
                 "202": ("乙學系", {"B101": ("乙系一年級", ["B101"]), "B201": ("乙系二年級", [])})},
    ("D", "2"): {"301": ("甲學系碩士班", {"M101": ("甲碩一", ["M101"])})},
    ("N", "1"): {"401": ("丙學系進修班", {"N101": ("丙進一", ["N101", "N102"])})},
    ("N", "2"): {},
}
FORMS = (("1", "大學部"), ("2", "碩士班"))


def _token(cls: str) -> str:
    return f"TOK{cls}xYz0"


def _page(dn: str, form: str, dept: str | None, cls: str | None, shown: str | None) -> str:
    """The query page as the school writes it: the two radio groups, the department and class menus, the search
    button, and, when a class was searched, its course rows (each with the class's own address, its teacher's, its
    room's and the 選課名單 picture)."""
    depts = SCHOOL[(dn, form)]
    dept = dept if dept in depts else next(iter(depts), None)
    classes = depts[dept][1] if dept else {}
    cls = cls if cls in classes else next(iter(classes), None)

    def radio(name: str, i: int, value: str, on: bool, words: str = "") -> str:
        return (f'<input id="x{name}{i}" type="radio" name="{P}{name}" value="{value}"{" checked" if on else ""} '
                f"onclick=\"javascript:setTimeout('__doPostBack(\\'{P}{name}${i}\\',\\'\\')', 0)\" />"
                f'<label for="x{name}{i}">{words}</label>')

    rows = ""
    if shown and dept and shown in classes:
        for code in classes[shown][1]:
            rows += (f'<tr><td>{code}<a href="syllabusForEnro.aspx?SyllabusNo=115{code}">課{code}</a></td>'
                     f'<td><a href="scheduleDepartment.aspx?cDES={_token(shown)}">{classes[shown][0]}</a></td>'
                     f'<td><a href="scheduleTeacher.aspx?cDES=T{code}">師O</a></td>'
                     f'<td><a href="scheduleRoom.aspx?cDES=R{code}">室{code}</a></td>'
                     f"<td><img alt=\"選課名單\" onclick=\"showSelectList('S{code}')\" /></td></tr>")
    return f"""<html><head><title>校務平台</title></head><body><form method="post" action="./schedule.aspx" id="uForm">
<input type="hidden" name="__VIEWSTATE" value="vs-{dn}-{form}-{dept}" />
{radio("uDN", 0, "D", dn == "D", "日間")}{radio("uDN", 1, "N", dn == "N", "進修")}
<select name="{P}uYearSemester$uYear"><option value="114">114</option><option selected="selected" value="115">115</option></select>
<select name="{P}uYearSemester$uSemester"><option selected="selected" value="1">第一學期</option></select>
{"".join(radio("uDeformType", i, v, v == form, t) for i, (v, t) in enumerate(FORMS))}
<select name="{P}uDepartment" id="ctl00_phMain_uDepartment">{"".join(
        f'<option{" selected=" + chr(34) + "selected" + chr(34) if k == dept else ""} value="{k}">{v[0]}</option>'
        for k, v in depts.items())}</select>
<select name="{P}uClass" id="ctl00_phMain_uClass">{"".join(
        f'<option{" selected=" + chr(34) + "selected" + chr(34) if k == cls else ""} value="{k}">{v[0]}</option>'
        for k, v in classes.items())}</select>
<button onclick="__doPostBack('{P}uSearch','')" id="ctl00_phMain_uSearch" type="button">查詢</button>
<div id="ctl00_phMain_upShow"><script type="text/javascript">
function showSelectList(cDES) {{ var oData = {{ cDES: cDES }}; jSelectList.load("scheduleSelectList.aspx", oData, function () {{ }}); }}
</script>{'<h4 id="ctl00_phMain_uScheduleList_uTitle">課表</h4>' if rows else ""}
<table>{rows}</table></div></form></body></html>"""


class School:
    """The fake server: answers a GET and each postback as ASP.NET does (a department chosen brings its classes; a
    class the form does not offer falls back to the first), and counts what it was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def __call__(self, url: str, data: list[tuple[str, str]] | None) -> str:
        if data is None:
            self.asked.append(("GET", ""))
            return _page("D", "1", None, None, None)
        got = dict(data)
        target = got.get("__EVENTTARGET", "")
        self.asked.append(("POST", target))
        dn, form = got[P + "uDN"], got[P + "uDeformType"]
        state = got["__VIEWSTATE"].split("-")  # the lists the form held when it was sent
        held_dept = got[P + "uDepartment"] if (state[1], state[2]) == (dn, form) else None
        if target == P + "uSearch":
            classes = SCHOOL[(dn, form)].get(held_dept, ("", {}))[1] if held_dept == state[3] else {}
            cls = got.get(P + "uClass")
            return _page(dn, form, held_dept, cls, cls if cls in classes else next(iter(classes), None))
        return _page(dn, form, held_dept, None, None)


def test_the_walk_searches_every_class_once_and_keeps_each_class_s_own_address():
    school = School()
    got = schedules.walk(school)
    assert got.complete and got.term == "115 第一學期"
    assert [(c.dn, c.form, c.dept, c.cls, c.courses) for c in got.classes] == [
        ("D", "1", "201", "A000", 1), ("D", "1", "201", "A101", 2), ("D", "1", "202", "B101", 1),
        ("D", "1", "202", "B201", 0), ("D", "2", "301", "M101", 1), ("N", "1", "401", "N101", 2)]
    assert got.classes[1].cls_name == "甲系一年級" and got.classes[4].form_name == "碩士班"
    assert sorted(got.urls) == sorted(BASE + "scheduleDepartment.aspx?cDES=" + _token(c)
                                      for c in ("A000", "A101", "B101", "M101", "N101"))  # B201 has no course: no address
    assert got.outlines == {"115" + c for c in ("A001", "A101", "A102", "B101", "M101", "N101", "N102")}  # one a course
    searches = [t for m, t in school.asked if t == P + "uSearch"]
    assert len(searches) == 6 and got.requests == len(school.asked)
    # one GET, then: 乙學系 chosen, 碩士班, 進修 (which lands on 碩士班: no department there), its 大學部, and the six
    # searches; the kind of programme a switch lands on is walked first, never asked for twice
    assert len(school.asked) == 1 + 4 + 6


def test_a_walk_stopped_by_its_limit_says_it_is_not_complete():
    got = schedules.walk(School(), limit=4)
    assert not got.complete and got.requests == 4 and "limit" in got.note


def test_an_answer_with_no_form_stops_the_walk():
    def broken(url, data):
        return "<html><body>系統維護中</body></html>"

    got = schedules.walk(broken)
    assert not got.complete and got.classes == [] and "form" in got.note


def test_the_form_is_sent_as_a_browser_sends_it():
    page = _page("D", "1", None, None, None)
    sent = schedules.post(schedules.form_fields(page), P + "uDepartment", **{P + "uDepartment": "202"})
    assert sent[:2] == [("__EVENTTARGET", P + "uDepartment"), ("__EVENTARGUMENT", "")]
    assert dict(sent)[P + "uDepartment"] == "202" and dict(sent)[P + "uDN"] == "D"
    assert dict(sent)[P + "uYearSemester$uYear"] == "115"  # the page's own term, never one worked out here
    assert [k for k, _v in sent].count(P + "uDepartment") == 1


# ---------------------------------------------------------------- the classes handed to the crawler

@pytest.fixture
def root(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    f.close()
    return tmp_path


def _report(root, n: int = 3) -> None:
    urls = {BASE + f"scheduleDepartment.aspx?cDES=Tok{i:03d}x": [f"班{i}"] for i in range(n)}
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / schedules.REPORT).write_text(json.dumps({"complete": True, "urls": urls, "classes": []}),
                                                     encoding="utf-8")


def test_seed_adds_each_class_page_as_a_page_of_the_site_one_step_in(root):
    _report(root)
    assert schedules.main(["seed", "--dir", str(root), "--write"]) == 0
    f = Frontier(root / "frontier.db", readonly=True)
    rows = f.q("SELECT url, state, depth, kind, owner_site, found_via FROM url WHERE owner_site=? ORDER BY url", (SITE,))
    assert len(rows) == 3
    assert rows[0]["url"] == "https://sso.nknu.edu.tw/stu/scheduledepartment.aspx?cdes=Tok000x"  # the token as written
    # depth 1: a standing page the weekly look compares (update._pages takes depth > 0), never a home
    assert {(r["state"], r["depth"], r["kind"], r["found_via"]) for r in rows} == {("pending", 1, "page", "seed")}
    assert {r[0] for r in f.q("SELECT source FROM seed_src")} == {schedules.SEED_SOURCE}
    assert schedules.SEED_SOURCE not in __import__("nknu.archive.update", fromlist=["x"]).HOME_SOURCES


def test_seed_without_write_changes_nothing_and_a_second_seed_adds_nothing(root, capsys):
    _report(root)
    assert schedules.main(["seed", "--dir", str(root)]) == 0
    f = Frontier(root / "frontier.db", readonly=True)
    assert f.q("SELECT count(*) FROM url")[0][0] == 0
    f.close()
    schedules.main(["seed", "--dir", str(root), "--write"])
    schedules.main(["seed", "--dir", str(root), "--write"])
    f = Frontier(root / "frontier.db", readonly=True)
    assert f.q("SELECT count(*) FROM url")[0][0] == 3


def test_seed_lets_go_of_the_classes_the_nightly_growth_cap_would_hold(root, monkeypatch):
    """A site under periodic update holds what one run adds past its cap (cap:run_page) for a person: the classes are
    what a person decided to add, all of them."""
    monkeypatch.setattr(crawl_mod, "RUN_CAPS", crawl_mod.RunCaps(pages=2, files=2, images=2))
    _report(root, n=5)
    assert schedules.main(["seed", "--dir", str(root), "--write"]) == 0
    f = Frontier(root / "frontier.db", readonly=True)
    assert f.q("SELECT state, count(*) FROM url GROUP BY 1")[0][:] == ("pending", 5)


def test_seed_refuses_a_walk_that_did_not_end(root, capsys):
    _report(root)
    path = root / "reports" / schedules.REPORT
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "complete": False}), encoding="utf-8")
    assert schedules.main(["seed", "--dir", str(root), "--write"]) == 1
    assert "没走完" in capsys.readouterr().err


# ---------------------------------------------------------------- how a class page is read

CLASS_URL = BASE + "scheduleDepartment.aspx?cDES=" + _token("A101")


def test_a_teacher_s_and_a_room_s_table_are_not_followed_and_the_bare_query_pages_stay():
    plan = round1.load()
    site = plan.site(SITE)
    assert site.skips(BASE + "scheduleTeacher.aspx?cDES=Tabc")
    assert site.skips("https://sso.nknu.edu.tw/stu/scheduleroom.aspx?cdes=Rabc")
    assert not site.skips(BASE + "scheduleTeacher.aspx")  # the query page itself: in the archive since round one
    assert not site.skips(CLASS_URL) and not site.skips(BASE + "syllabusForEnro.aspx?SyllabusNo=1151001206")
    assert round1.verdict(plan, scope.canonical(BASE + "scheduleRoom.aspx?cDES=Rabc"), None, "link").reason == "out:skip_path"
    assert round1.verdict(plan, scope.canonical(CLASS_URL), None, "link").verdict == "r1"


def test_a_skip_path_ending_in_a_question_mark_holds_only_with_a_query():
    s = round1.Site(site="x.nknu.edu.tw", round="1", skip=("/a/list.aspx?", "/b"))
    assert s.skips("https://x.nknu.edu.tw/a/list.aspx?id=1") and not s.skips("https://x.nknu.edu.tw/a/list.aspx")
    assert s.skips("https://x.nknu.edu.tw/b") and s.skips("https://x.nknu.edu.tw/b/c?x=1")


def test_the_query_form_s_own_controls_are_no_gap_of_a_class_page():
    page = links.extract(_page("D", "1", "201", "A101", "A101"), CLASS_URL)
    keys = ([f"postback:{b.target}|{b.argument}" for b in page.buttons] + [u.key for u in page.unhandled]
            + [c.key for c in page.clicks])
    assert any("uDeformType" in k for k in keys) and any(k.startswith("click:showSelectList") for k in keys)
    said = {k: profiles.decision(SITE, k) for k in keys}
    assert "unknown" not in said.values(), {k: v for k, v in said.items() if v == "unknown"}
    assert {v for k, v in said.items() if k.startswith("click:showSelectList")} == {"never"}  # the students enrolled


def test_a_class_page_s_title_is_its_class_and_the_query_page_keeps_its_own():
    rules = profiles.page_title_rules(scope.canonical(CLASS_URL))
    assert len(rules) == 4  # the class, the table's heading; 11-19: the form's caption, the page's h3
    shown = LexborHTMLParser(_page("D", "1", "201", "A101", "A101")).css_first(rules[0].title)
    assert shown is not None and shown.text(strip=True) == "甲系一年級"
    for rule in rules[:2]:  # nothing searched: neither cell
        assert LexborHTMLParser(_page("D", "1", None, None, None)).css_first(rule.title) is None
    assert export.page_title(LexborHTMLParser(_page("D", "1", "201", "A101", "A101")), CLASS_URL, SITE) == "甲系一年級"
    assert export.page_title(LexborHTMLParser(_page("D", "1", None, None, None)), CLASS_URL, SITE) == "校務平台"


def test_a_class_page_with_no_class_is_titled_by_its_table_s_heading():
    """General education, PE and the teacher programmes have no class: the class menu holds the one choice 「--」 (no
    value), 27 of the 273 class pages of 11-18, all titled 「--」 by the first rule alone. The table's own heading
    (the department and 「課表」) is the title then."""
    page = _page("D", "1", "201", "A101", "A101").replace(
        '<option selected="selected" value="A101">甲系一年級</option>', "").replace(
        '<option value="A000">甲系全年級選課用</option>', '<option selected="selected" value="">--</option>').replace(
        '<h4 id="ctl00_phMain_uScheduleList_uTitle">課表</h4>', '<h4 id="ctl00_phMain_uScheduleList_uTitle">甲學程　課表</h4>')
    assert 'value="">--</option>' in page and "甲系一年級</option>" not in page
    assert export.page_title(LexborHTMLParser(page), CLASS_URL, SITE) == "甲學程 課表"


OUTLINE_URL = BASE + "18weekssyllabusforenro.aspx?syllabusno=1150000001"


def _outline(teachers: tuple[str, ...] = ("師O",), chosen: bool = True) -> str:
    """A course outline's page as the school writes it: the teacher menu (its prompt chosen when the course has several
    teachers: nothing of the outline is shown then, only the notice and the 「確定」 button), the print link and the
    course's cells."""
    menu = "".join(f'<option{" selected=" + chr(34) + "selected" + chr(34) if chosen and i == 0 else ""} '
                   f'value="115|1|X{i}">{t}</option>' for i, t in enumerate(teachers))
    prompt = f'<option{"" if chosen else " selected=" + chr(34) + "selected" + chr(34)} value="select">請選擇Select</option>'
    body = ("""<a id="ctl00_phMain_uPrintFriendly" href="javascript:printFriendly('divData', null)">友善列印 Print</a>
<div id="divData"><h4>課程資料 Course Information</h4><div id="ctl00_phMain_plCourseBasicData"><table>
<tr><td>開課學年</td><td>Year</td><td>115</td></tr><tr><td>開課學期</td><td>Semester</td><td>1</td></tr>
<tr><td>科目名稱</td><td>Course</td><td>甲課概論 Introduction to A</td></tr>
<tr><td>科目代碼</td><td>Course No</td><td>AA101</td></tr></table></div></div>""" if chosen else
            '<div role="alert">本課程多名老師合開，請先選擇教師</div>')
    return f"""<html><head><title>校務平台</title></head><body><form method="post" action="./18weekssyllabusforenro.aspx?syllabusno=1150000001" id="uForm">
<input type="hidden" name="__VIEWSTATE" value="vs" /><h3>教學大綱 Syllabus</h3>
<select name="{P}ddlTea" id="ctl00_phMain_ddlTea">{prompt}{menu}</select>
{'' if chosen else f'<input type="submit" name="{P}btnOk" value="確定 OK" id="ctl00_phMain_btnOk" />'}
{body}</form></body></html>"""


def test_an_outline_page_s_title_is_its_course_s_name():
    """Every outline page had one title in the export of 11-18 (「教學大綱 Syllabus」, 1,676 pages): the course's name is
    the third cell of the 科目名稱 row. A page that shows no outline yet (several teachers, none chosen) keeps the
    reading by headings. 11-19: the term the page's own cells write goes after the name (test_rules_1119: with the
    class too where the page names one)."""
    assert export.page_title(LexborHTMLParser(_outline()), OUTLINE_URL, SITE) == (
        "甲課概論 Introduction to A（115學年度第1學期）")
    assert export.page_title(LexborHTMLParser(_outline(("師O", "師OO"), chosen=False)), OUTLINE_URL, SITE) == "校務平台"


def test_an_outline_page_s_print_link_is_no_gap_of_it():
    page = links.extract(_outline(), OUTLINE_URL)
    keys = [u.key for u in page.unhandled]
    assert "js_link:printFriendly" in keys
    assert profiles.decision(SITE, "js_link:printFriendly") == "no_content"


def test_the_walk_s_argument_parsing_and_report(tmp_path, monkeypatch):
    """`walk` writes what it found beside the ledger's reports; the school is asked through the function given."""
    Frontier(tmp_path / "frontier.db").close()
    monkeypatch.setattr(schedules, "_school", lambda gap: (School(), lambda: None))
    assert schedules.main(["walk", "--dir", str(tmp_path)]) == 0
    got = json.loads((tmp_path / "reports" / schedules.REPORT).read_text(encoding="utf-8"))
    assert got["complete"] and len(got["urls"]) == 5 and len(got["classes"]) == 6 and got["requests"] == 11
    assert dict(parse_qsl(next(iter(got["urls"])).split("?", 1)[1]))["cDES"].startswith("TOK")


def test_the_course_query_s_profile_names_no_content_area_and_changes_the_page_version_once():
    """The content stage's page version is a digest of the profiles: one more profile is one more version (its lane B
    reads every page again once), from the JS sites' of this morning to this one."""
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    assert profiles.STU.content_css is None and profiles.profile_for(CLASS_URL) is profiles.STU
    assert pagetext.page_version(tuple(p for p in profiles.PROFILES if p is not profiles.STU)) == "pages-4:f932d12de9"
    assert pagetext.page_version(profiles.PROFILES) == "pages-4:c2a2af5ed9"
