"""Round one's gap ⑩ of 11-08 (docs/round1-gaps-2026-11-08.md), its second half: the owner 11-09 「现在补 115」. The
course tables (sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=<code>) open on an old academic year (113
is the page's default); the current year's table is what the page's form shows when its year menu is set to the
current academic year and 「顯示資料」 is pressed, once for each department or programme of its department menu. The
crawler session's design (profiles.Menu, profiles.DATAOPEN; how each piece is done where the design does not say,
【Claude 的执行办法】):

- a menu rule presses the form's submit button once per enabled option of the department menu, the year menu set to
  the academic year of today (1 August on, profiles.academic_year); the press is a detail of a special argument
  (MENU_ARG) whose row key is the list's, so the same department is one row whichever page or answer shows it;
- no year (a reading that presses nothing): no menu press; a form, menu, button or year missing: no press and an
  unpressed key (要人看), never a silent gap;
- its POST is the form's own fields as archived, the two menus set, and the submit button that sent it (no
  __EVENTTARGET); an answer is checked by what its form shows (the year and the department asked), never by a title;
- an answer that lists a department no press covers plans it from the answer's own form state; one pressed or planned
  already is never pressed twice, and a new GET reads the done answers again once each;
- `retry unpressed` puts back a stored course table with no press for this academic year (a rejudge before it reads
  the menus as needing nothing and presses nothing);
- the export says the page came of the menu.
The review of 11-09 (its findings fixed here; how, 【Claude 的执行办法】): a redirect answers no menu press; what a
mismatched answer showed stays with the press; `retry unpressed` judges each option (one tried twice is a person's, one
tried once is pressed again beside others done) and verify check 4 names the same pages; a rejudge keeps a menu its
page cannot send unpressed; a press line says the department in words; two options NFKC reads as one are two rows.
All offline: fake networks and clocks, made-up pages, made-up departments and courses (測試學系 …), no person's name."""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import export, links, monitor, postback, profiles, reconcile, scope
from nknu.archive.crawler import Crawler
from nknu.archive.frontier import Frontier
from nknu.archive.postback import UNPRESSED, Chains, Stored, read_page
from tests.archive.plans import OpenPlan
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_export_1027 import HTML, _action, _res, by_url, make_archive, rows_of
from tests.archive.test_pilot_fixes import SSO_Q, PressNet, work
from tests.archive.test_postback import World

TAIPEI = ZoneInfo("Asia/Taipei")
SITE = "sso.nknu.edu.tw/dataopen2search"
PAGE = "https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=999001"  # a made-up unit code
LOST = "https://sso.nknu.edu.tw/dataopen2search/lostandfound.aspx"
YEAR, DEPT, OK = "ctl00$phMain$uYear", "ctl00$phMain$uDepartment", "ctl00$phMain$uOK"
BARE = "postback:ctl*$phMain$uOK|@menu"  # the key of a menu nobody could press (要人看)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=TAIPEI).timestamp()  # the day the owner said 「现在补 115」
JULY = datetime(2026, 7, 1, 12, 0, tzinfo=TAIPEI).timestamp()  # academic year 114 still
# the department menu: (optgroup label, ((value, disabled), …)); one option the school disabled
DEPTS = (("日間-學士班", (("測試學系", False),)),
         ("日間-碩士班", (("測試碩士班", False), ("停招碩士班", True))),
         ("進修-碩士班", (("測試進修班", False),)))
TEXTS = ("115學年度 日間-學士班 測試學系", "115學年度 日間-碩士班 測試碩士班", "115學年度 進修-碩士班 測試進修班")


def _option(value: str, chosen: bool, off: bool = False) -> str:
    sel = ' selected="selected"' if chosen else ""
    dis = ' disabled="disabled"' if off else ""
    return f'<option{sel}{dis} value="{value}">{value}</option>'


def _depts(groups, chosen: str) -> str:
    out = []
    for label, options in groups:
        inner = "".join(_option(v, v == chosen, off) for v, off in options)
        out.append(f'<optgroup label="{label}">{inner}</optgroup>' if label else inner)
    return "".join(out)


def course_page(year: str = "113", dept: str = "測試學系", *, groups=DEPTS, depts: str | None = None,
                state: str = "VS-COURSE-PAGE", courses: tuple[str, ...] = ("測試課程甲", "測試課程乙"),
                form_id: str = "uForm", submit: bool = True, extra: str = "") -> str:
    """A course table as the school writes it (made up): its hidden state, the two menus, the 師培 radios, the submit
    button, the courses of the year and department shown; year and dept: what the page shows selected."""
    years = "".join(_option(y, y == year) for y in ("116", "115", "114", "113", "112"))
    button = f'<input type="submit" name="{OK}" value="顯示資料" id="ctl00_phMain_uOK" />' if submit else ""
    items = "".join(f'<li class="cssItemStyle">{c}</li>' for c in courses)
    return ('<!DOCTYPE html><html><head><title>開課課表（測試）</title></head><body>'
            f'<form method="post" action="./opencoursetablepure.aspx?unit=999001" id="{form_id}">'
            f'<div class="aspNetHidden"><input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="{state}" />'
            '</div><div class="aspNetHidden"><input type="hidden" name="__VIEWSTATEGENERATOR" '
            'id="__VIEWSTATEGENERATOR" value="0000TEST" /><input type="hidden" name="__VIEWSTATEENCRYPTED" '
            'id="__VIEWSTATEENCRYPTED" value="" /></div>'
            '<input type="hidden" name="ctl00$uPostBackFocus" id="ctl00_uPostBackFocus" />'
            '<input type="hidden" name="ctl00$UserTraceUseAjax" id="ctl00_UserTraceUseAjax" value="false" />'
            f'<span>學年度</span><select name="{YEAR}" id="ctl00_phMain_uYear">{years}</select>'
            f'<span>系所</span><select name="{DEPT}" id="ctl00_phMain_uDepartment">'
            f'{depts if depts is not None else _depts(groups, dept)}</select>'
            '<span>師培</span><input id="ctl00_phMain_uTeacherEducation_0" type="radio" '
            'name="ctl00$phMain$uTeacherEducation" value="false" checked="checked" />'
            '<label for="ctl00_phMain_uTeacherEducation_0">否</label><input id="ctl00_phMain_uTeacherEducation_1" '
            'type="radio" name="ctl00$phMain$uTeacherEducation" value="true" />'
            f'<label for="ctl00_phMain_uTeacherEducation_1">是</label>{button}'
            f'<fieldset><legend>系必修</legend><ul class="cssColumnStyle">{items}</ul></fieldset>'
            f"</form>{extra}</body></html>")


def answer_page(year: str, dept: str, *, groups=DEPTS) -> str:
    """What the school answers a press with: the same page, the year and the department asked selected."""
    return course_page(year, dept, groups=groups, state=f"VS-{year}-{dept}", courses=(f"{year}學年度測試課程",))


def key(value: str, year: int = 115, url: str = PAGE) -> str:
    """A menu press's row key: the list's, from the year and the value exactly as sent (quoted)."""
    return postback.row_key(SITE, url, f"{year} {quote(value, safe='')}", category="course_table")


def arg(value: str, year: int = 115) -> str:
    return profiles.menu_argument(profiles.COURSE_TABLE, year, value)


def unit(n: int) -> str:
    return PAGE.replace("999001", f"999{n:03d}")


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.now = NOW
    yield world
    world.sink.close()
    world.f.close()


def test_the_fixtures_are_what_the_tests_say():
    page = course_page()
    assert page.count("<option") == 9 and 'selected="selected" value="113"' in page
    assert 'disabled="disabled" value="停招碩士班"' in page and "__EVENTTARGET" not in page and "__doPostBack" not in page
    assert 'selected="selected" value="115"' in answer_page("115", "測試碩士班")


# ---------------------------------------------------------------- the academic year


@pytest.mark.parametrize(("day", "want"), [
    (datetime(2026, 7, 31), 114), (datetime(2026, 8, 1), 115), (datetime(2026, 11, 9), 115),
    (datetime(2027, 1, 15), 115), (datetime(2027, 8, 1), 116),
])
def test_academic_year(day, want):
    assert profiles.academic_year(day.date()) == want


def test_a_menu_argument_reads_back_as_it_was_written():
    got = arg("進修 碩士班&甲")
    assert got.startswith(profiles.MENU_ARG + "?") and "$" not in got  # urlencoded, the select names too
    assert profiles.menu_choice(got) == ((YEAR, "115"), (DEPT, "進修 碩士班&甲"))
    assert profiles.menu_year(profiles.COURSE_TABLE, got) == 115
    for other in ("", None, profiles.MENU_ARG, profiles.IMAGE_ARG, "Page$2"):
        assert profiles.menu_choice(other) is None and profiles.menu_year(profiles.COURSE_TABLE, other) is None


# ---------------------------------------------------------------- the platform file


def test_the_profile_claims_the_site_and_check_profiles_stays_empty():
    assert profiles.check_profiles() == []
    assert profiles.profile_for(PAGE) is profiles.DATAOPEN and profiles.profile_for(LOST) is profiles.DATAOPEN
    assert profiles.profile_for_site(SITE) is profiles.DATAOPEN and profiles.PROFILES[-1] is profiles.DATAOPEN
    assert profiles.DATAOPEN.menus == (profiles.COURSE_TABLE,) and "uForm" in profiles.DATAOPEN.forms
    assert profiles.page_kind(PAGE) is None and profiles.content_css(PAGE) is None  # read as before
    assert profiles.COURSE_TABLE.why.strip()


def test_check_profiles_names_a_menu_whose_form_or_paths_are_wrong(monkeypatch):
    bad = dataclasses.replace(profiles.COURSE_TABLE, paths="(", form="form9")
    monkeypatch.setattr(profiles, "PROFILES", tuple(dataclasses.replace(p, menus=(bad,)) if p is profiles.DATAOPEN
                                                    else p for p in profiles.PROFILES))
    got = profiles.check_profiles()
    assert len(got) == 2 and all(x.startswith("dataopen2search: ") for x in got), got
    assert any("form9" in x for x in got) and any(x.startswith("dataopen2search: (: ") for x in got)


def test_the_menus_need_nothing_the_submit_is_pressed_and_the_course_popup_stays_for_a_person():
    page = links.extract(course_page(), PAGE, profiles.content_css(PAGE))
    keys = {u.key for u in page.unhandled}
    assert {f"select:{YEAR}", f"select:{DEPT}"} <= keys  # the crawler writes select:<name>
    for k in (f"select:{YEAR}", f"select:{DEPT}"):
        assert profiles.decision(SITE, k) == "ignore"
        assert links.unhandled_need(profiles.decision(SITE, k)) == ("no", "unhandled:ignore")
    assert profiles.decision(SITE, BARE, "顯示資料") == "press"
    assert monitor.expected_unhandled(SITE, BARE, "顯示資料", UNPRESSED) == ("must", UNPRESSED)
    assert profiles.decision(SITE, "js_bind:click .cssItemStyle") == "unknown"  # a course's history: 要人看
    assert profiles.decision(SITE, "js_link:isFinite") == "ignore"
    assert profiles.decision(SITE, "postback:ctl*$uTopMainMenu$btnSwitch") == "unknown"  # lost and found: as before
    assert profiles.decision(SITE, "select:ctl00$phMain$uOther") == "unknown"


# ---------------------------------------------------------------- reading a page


def test_read_page_presses_each_department_of_this_year_once():
    pr = read_page(profiles.DATAOPEN, PAGE, course_page(), site=SITE, list_url=PAGE, year=115)
    assert [(p.kind, p.target, p.argument, p.row_key, p.text, p.step, p.form_id, p.rowset) for p in pr.presses] == [
        ("detail", OK, arg(v), key(v), t, 1, "uForm", "course_table")
        for v, t in zip(("測試學系", "測試碩士班", "測試進修班"), TEXTS, strict=True)]
    assert (pr.rowset, pr.walk, pr.unknown, pr.unplanned, pr.unplanned_at) == (None, "none", (), (), ())
    # the keys are the list's: the same from a detail's reading, and from an answer of another year shown
    again = read_page(profiles.DATAOPEN, PAGE, answer_page("115", "測試進修班"), site=SITE, list_url=PAGE,
                      parent_key="k0", year=115)
    assert [p.row_key for p in again.presses] == [p.row_key for p in pr.presses]
    other = read_page(profiles.DATAOPEN, unit(2), course_page(), site=SITE, list_url=unit(2), year=115)
    assert not {p.row_key for p in other.presses} & {p.row_key for p in pr.presses}  # another unit's table


def test_read_page_reads_the_options_as_a_browser_sends_them():
    """Disabled options (their own attribute, or their optgroup's) are never pressed; an option with no value
    attribute sends its text, white space collapsed; a value written twice is one press; no optgroup, no label."""
    depts = ('<option value="測試學系">測試學系</option>'
             '<optgroup label="日間-學士班"><option value="測試學系">測試學系（重複）</option></optgroup>'
             '<optgroup label="停招" disabled="disabled"><option value="停招學系">停招學系</option></optgroup>'
             '<optgroup label="學程"><option>  測試\n  學程 </option><option disabled value="x">x</option></optgroup>')
    pr = read_page(profiles.DATAOPEN, PAGE, course_page(depts=depts), site=SITE, list_url=PAGE, year=115)
    assert [(p.argument, p.text) for p in pr.presses] == [(arg("測試學系"), "115學年度 測試學系"),
                                                          (arg("測試 學程"), "115學年度 學程 測試 學程")]


def test_no_year_presses_no_menu():
    """A reading that names no year (rejudge, the export) presses no menu and says nothing of it."""
    pr = read_page(profiles.DATAOPEN, PAGE, course_page(), site=SITE, list_url=PAGE)
    assert (pr.presses, pr.unplanned, pr.unplanned_at, pr.texts) == ((), (), (), ())


@pytest.mark.parametrize(("html", "year", "text"), [
    (course_page(), 120, "顯示資料"),  # the year menu does not offer this year (yet)
    (course_page(form_id="form1"), 115, "顯示資料"),  # the form is another
    (course_page(submit=False), 115, ""),  # no submit button
    (course_page().replace(f'name="{DEPT}"', 'name="ctl00$phMain$uDept"'), 115, "顯示資料"),  # no department menu
    (course_page().replace(f'name="{YEAR}"', 'name="ctl00$phMain$uYr"'), 115, "顯示資料"),  # no year menu
    (course_page().replace(f'name="{YEAR}"', f'disabled name="{YEAR}"'), 115, "顯示資料"),  # a menu one cannot set
    (course_page(depts='<option disabled value="x">x</option>'), 115, "顯示資料"),  # no department to choose
    # the button outside the form sends no form (its text is still the one written)
    (course_page(submit=False, extra=f'<input type="submit" name="{OK}" value="顯示資料" />'), 115, "顯示資料"),
])
def test_a_menu_nobody_can_send_is_written_unpressed(html, year, text):
    pr = read_page(profiles.DATAOPEN, PAGE, html, site=SITE, list_url=PAGE, year=year)
    assert pr.presses == ()
    assert (pr.unplanned, pr.unplanned_at) == ((BARE,), ((OK, profiles.MENU_ARG),))
    assert dict(pr.texts)[BARE] == text


def test_a_lost_and_found_page_reads_as_before(w):
    html = ("<html><body><form method='post' action='./lostandfound.aspx' id='uForm'><input type='hidden' "
            "name='__VIEWSTATE' value='VS-LOST'><a href=\"javascript:__doPostBack('ctl00$uTopMainMenu$btnSwitch',''"
            ")\">切換</a><select name='ctl00$phMain$uPlace'><option>全部</option></select><img src='/x/1.jpg'>"
            "</form></body></html>")
    pr = read_page(profiles.DATAOPEN, LOST, html, site=SITE, list_url=LOST, year=115)
    assert (pr.presses, pr.unplanned, pr.unknown) == ((), (), ("postback:ctl*$uTopMainMenu$btnSwitch|",))
    row, got = w.stored(LOST, SITE, html)
    assert got == Stored(None, 0, None, ()) and not w.actions(row["id"])


# ---------------------------------------------------------------- the chain: planning and the POST


def test_a_stored_course_table_plans_one_press_per_department_for_this_year(w):
    row, got = w.stored(PAGE, SITE, course_page())
    assert (got.page_role, got.presses, got.ended_by, got.unplanned) == (None, 3, None, ())
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["argument"], a["row_key"], a["row_text"], a["step"], a["form_id"],
             a["profile"], a["state"]) for a in acts] == [
        ("detail", OK, arg(v), key(v), t, 1, "uForm", "dataopen2search/course_table", "pending")
        for v, t in zip(("測試學系", "測試碩士班", "測試進修班"), TEXTS, strict=True)]
    res = {r["res_key"]: (r["role"], r["need"], r["action_id"]) for r in w.f.q(
        "SELECT * FROM page_res WHERE page_id=? AND res_key LIKE 'pb:%'", (row["id"],))}
    assert res == {f"pb:detail:{a['row_key']}": ("detail", "must", a["id"]) for a in acts}
    assert w.walk(row["id"]) is None  # no list


def test_the_year_is_todays_academic_year(w):
    w.now = JULY
    row, _got = w.stored(PAGE, SITE, course_page())
    assert {profiles.menu_year(profiles.COURSE_TABLE, a["argument"]) for a in w.actions(row["id"])} == {114}


def test_the_academic_year_is_of_the_day_in_taipei(w):
    """1 August begins in Taipei eight hours before it does in UTC: the presses of then are of the new year."""
    w.now = datetime(2026, 7, 31, 16, 30, tzinfo=UTC).timestamp()  # 1 August 00:30 in Taipei, 31 July in UTC
    assert postback.this_year(w.now) == 115
    row, _got = w.stored(PAGE, SITE, course_page())
    assert {profiles.menu_year(profiles.COURSE_TABLE, a["argument"]) for a in w.actions(row["id"])} == {115}


def test_two_departments_nfkc_reads_as_one_are_two_rows(w):
    """A row key takes the value exactly as sent (review of 11-09): two options that differ only in the width of
    their brackets are two presses, the second never silently left out."""
    depts = '<option value="測試學系(甲)">測試學系(甲)</option><option value="測試學系（甲）">測試學系（甲）</option>'
    row, _got = w.stored(PAGE, SITE, course_page(depts=depts))
    acts = w.actions(row["id"])
    assert [dict(profiles.menu_choice(a["argument"]))[DEPT] for a in acts] == ["測試學系(甲)", "測試學系（甲）"]
    assert len({a["row_key"] for a in acts}) == 2 and acts[0]["row_key"] == key("測試學系(甲)")


def test_a_press_is_the_form_with_both_menus_set_and_its_submit_button(w):
    w.stored(PAGE, SITE, course_page())
    _a, target = w.press()
    assert (target.method, target.url) == ("POST", PAGE)  # the form's action, resolved
    # the form's own fields as archived (the page showed 113), the two menus set, then the button that sent it: a
    # browser's entry list (no __EVENTTARGET: the page has none and no __doPostBack); 師培 as the page has it (否)
    assert list(target.data) == [("__VIEWSTATE", "VS-COURSE-PAGE"), ("__VIEWSTATEGENERATOR", "0000TEST"),
                                 ("__VIEWSTATEENCRYPTED", ""), ("ctl00$uPostBackFocus", ""),
                                 ("ctl00$UserTraceUseAjax", "false"), (YEAR, "115"), (DEPT, "測試學系"),
                                 ("ctl00$phMain$uTeacherEducation", "false"), (OK, "顯示資料")]


SECOND_FORM = ('<form id="form9" method="post" action="./opencoursetablepure.aspx?unit=999001">'
               f'<input type="submit" name="{OK}" value="顯示資料" /></form>')


@pytest.mark.parametrize(("change", "html", "reason"), [
    (("argument", arg("不存在學系")), course_page(), f"option_missing:{DEPT}"),
    (("argument", arg("停招碩士班")), course_page(), f"option_missing:{DEPT}"),  # disabled on the page
    (("argument", arg("測試學系", 120)), course_page(), f"option_missing:{YEAR}"),
    (("target", "ctl00$phMain$uGo"), course_page(), "control_missing"),
    (("form_id", "form7"), course_page(), "no_form"),
    (("form_id", "form9"), course_page(extra=SECOND_FORM), "form_not_in_profile:form9"),
    (("form_id", "uForm"), course_page().replace('action="./opencoursetablepure.aspx?unit=999001"',  # another host
                                                 'action="https://elsewhere.nknu.edu.tw/x.aspx"'),
     "form_posts_to:elsewhere.nknu.edu.tw"),
])
def test_a_press_its_page_cannot_send_sends_nothing(w, change, html, reason):
    row, _got = w.stored(PAGE, SITE, html)
    first = w.actions(row["id"])[0]
    w.f.x(f"UPDATE action SET {change[0]}=? WHERE id=?", (change[1], first["id"]))
    a = w.chains.next_action(SSO_Q)
    assert a["id"] == first["id"] and w.chains.target_for(a) is None
    assert tuple(w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]) == ("mismatch", reason)


def test_menu_form_and_menu_post():
    page = course_page()
    assert postback.menu_form(page, "uForm", OK) == ('form[id="uForm"]', "")
    assert postback.menu_form(course_page(submit=False), "uForm", OK) == (None, "control_missing")
    assert postback.menu_form(page, "form7", OK) == (None, "no_form")
    assert postback.menu_form(page + page, "uForm", OK) == (None, "no_form")  # two forms of that id
    outside = course_page(submit=False, extra=f'<input type="submit" name="{OK}" value="顯示資料" />')
    assert postback.menu_form(outside, "uForm", OK) == (None, "no_form")  # the button sends no form of that id
    _url, fields = postback.webforms.form_state(page, PAGE, 'form[id="uForm"]')
    got, wrong = postback.menu_post(page, 'form[id="uForm"]', fields, ((YEAR, "116"), (DEPT, "測試進修班")), OK)
    assert wrong is None and got[5:] == [(YEAR, "116"), (DEPT, "測試進修班"),
                                         ("ctl00$phMain$uTeacherEducation", "false"), (OK, "顯示資料")]
    assert postback.menu_post(page, 'form[id="uForm"]', fields, ((YEAR, "115"), (DEPT, "x")), OK)[1] == DEPT


# ---------------------------------------------------------------- the chain: the answers


def test_an_answer_that_shows_the_year_and_department_asked_is_done(w):
    row, _got = w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    out = w.answer(a, target.url, answer_page("115", "測試學系"))
    assert not out.restart and out.note == ""
    assert tuple(w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]) == ("done", None)
    assert w.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"
    assert len(w.actions(row["id"])) == 3  # the answer's own menu names the same three: none pressed twice
    assert not w.f.q("SELECT 1 FROM page_res WHERE page_id=? AND why=?", (row["id"], UNPRESSED))


def test_an_answer_with_no_course_is_the_schools_answer_never_thin(w):
    w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    w.answer(a, target.url, course_page("115", "測試學系", state="VS-EMPTY", courses=()))
    assert w.f.q("SELECT result, reason FROM action_result WHERE id=?", (a["id"],))[0][:] == ("ok", None)


def test_an_answer_that_shows_the_default_year_is_a_mismatch_walked_again_once(w):
    row, _got = w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    out = w.answer(a, target.url, course_page("113", "測試學系"))
    said = "menu: asked 115/測試學系, shows 113/測試學系"  # what the answer showed, kept with the press
    assert tuple(w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]) == ("mismatch", said)
    assert out.restart and out.note == said
    back = w.f.row_by_id(row["id"])
    assert (back["state"], back["reason"], back["prio"]) == ("pending", "chain_restart", 0)
    assert w.f.q("SELECT 1 FROM event WHERE code='chain_restart' AND message LIKE '%asked 115/測試學系, shows 113%'")
    # the GET again: epoch 2, the three pressed again; the same answer again is left for a person
    row2, shot = w.page(PAGE, SITE, course_page())
    w.chains.page_stored(row2, course_page(), w.warc, shot.warc_offset)
    assert len(w.actions(row["id"], epoch=2)) == 3
    b, target = w.press()
    assert (b["epoch"], b["row_key"]) == (2, a["row_key"])
    out = w.answer(b, target.url, course_page("113", "測試學系"))
    assert not out.restart and w.f.q("SELECT result FROM action_result WHERE id=?", (b["id"],))[0][0] == "mismatch"
    # no restart (no event) this time: the press itself says what the school showed (review of 11-09)
    assert w.f.q("SELECT reason FROM action WHERE id=?", (b["id"],))[0][0] == said


@pytest.mark.parametrize(("html", "note"), [
    (answer_page("115", "測試碩士班"), "menu: asked 115/測試學系, shows 115/測試碩士班"),
    (course_page("115", "測試學系", form_id="form1"), "menu: asked 115/測試學系, shows no form uForm"),
    (course_page("115", depts=""), "menu: asked 115/測試學系, shows 115/?"),
])
def test_an_answer_that_shows_another_choice_is_a_mismatch(w, html, note):
    w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    out = w.answer(a, target.url, html)
    assert out.note == note and out.restart
    assert w.f.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0] == note


@pytest.mark.parametrize("location", [LOST, f"{PAGE}&year=115&dept=x"])
def test_a_menu_press_answered_with_a_redirect_failed_and_its_chain_walks_again_once(w, location):
    """A redirect shows neither the year nor the department asked, wherever it leads (another page of the site, the
    table's own address with a query; review of 11-09: done, the department's table missing and the menu taken as
    sent): the press failed (press_redirect), its chain starts again from its GET once, and the second time it is
    mismatch redirect, for a person. The Location is never queued."""
    row, _got = w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    out = w.answer(a, target.url, b"", status=302, headers={"location": location, "content-type": "text/html"})
    got = w.f.q("SELECT state, reason, location FROM action WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("failed", "press_redirect", scope.canonical(location)) and out.restart
    assert out.location is None and w.f.row_by_id(row["id"])["reason"] == "chain_restart"
    row2, shot = w.page(PAGE, SITE, course_page())
    w.chains.page_stored(row2, course_page(), w.warc, shot.warc_offset)
    b, target = w.press()
    assert (b["epoch"], b["row_key"]) == (2, a["row_key"])
    out = w.answer(b, target.url, b"", status=302, headers={"location": location, "content-type": "text/html"})
    assert not out.restart and out.location is None
    assert tuple(w.f.q("SELECT state, reason FROM action WHERE id=?", (b["id"],))[0]) == ("mismatch", "redirect")
    assert w.f.q("SELECT result FROM action_result WHERE id=?", (b["id"],))[0][0] == "mismatch"


def test_an_answer_at_another_address_keys_its_departments_to_the_list(w):
    """The form may post to another address than the table's (its action): the departments the answer lists are
    still the list's rows, none planned twice."""
    page = course_page().replace('action="./opencoursetablepure.aspx?unit=999001"',
                                 'action="./opencoursetablepure.aspx?unit=999001&amp;from=menu"')
    row, _got = w.stored(PAGE, SITE, page)
    a, target = w.press()
    assert target.url == f"{PAGE}&from=menu"
    w.answer(a, target.url, answer_page("115", "測試學系"))
    assert [x["row_key"] for x in w.actions(row["id"])] == [key(v) for v in ("測試學系", "測試碩士班", "測試進修班")]


def test_a_department_only_an_answer_lists_is_pressed_from_that_answers_form(w):
    """The year asked may list another department (a programme opened since the page's default year): its press is
    planned from the answer's own form state, and the departments both list are one row each."""
    row, _got = w.stored(PAGE, SITE, course_page())
    a, target = w.press()
    groups = (*DEPTS, ("日間-學士班", (("新設學程", False),)))
    shot = w.archive(target.url, answer_page("115", "測試學系", groups=groups))
    w.chains.answered(a, shot, w.warc)
    acts = w.actions(row["id"])
    assert len(acts) == 4 and len({x["row_key"] for x in acts}) == 4
    new = acts[-1]
    assert (new["row_key"], new["row_text"], new["argument"], new["state"]) == (
        key("新設學程"), "115學年度 日間-學士班 新設學程", arg("新設學程"), "pending")
    assert (new["form_src_warc"], new["form_src_offset"]) == (w.warc, shot.warc_offset)  # the answer's record
    got = w.chains.target_for(w.f.q("SELECT * FROM action WHERE id=?", (new["id"],))[0])
    assert dict(got.data)["__VIEWSTATE"] == "VS-115-測試學系"  # the answer's own state
    assert (dict(got.data)[YEAR], dict(got.data)[DEPT], got.data[-1]) == ("115", "新設學程", (OK, "顯示資料"))


def test_every_department_is_pressed_once_and_the_walk_ends(w):
    row, _got = w.stored(PAGE, SITE, course_page())
    sent = []
    while (a := w.chains.next_action(SSO_Q)) is not None:
        target = w.chains.target_for(a)
        data = dict(target.data)
        sent.append((data[YEAR], data[DEPT]))
        w.chains.answered(a, w.archive(target.url, answer_page(data[YEAR], data[DEPT])), w.warc)
    assert sent == [("115", "測試學系"), ("115", "測試碩士班"), ("115", "測試進修班")]
    assert [x["state"] for x in w.actions(row["id"])] == ["done"] * 3


def test_a_new_get_reads_the_done_answers_again_once_each_and_presses_what_they_list(w):
    """A department an answer listed and nobody pressed before the chain started again is planned again from that
    answer, read with the year it asked; the done answers name one another, and each is read once (no endless
    reading again)."""
    row, _got = w.stored(PAGE, SITE, course_page())
    groups = (*DEPTS, ("日間-學士班", (("新設學程", False),)))
    while (a := w.chains.next_action(SSO_Q)) is not None and a["row_key"] != key("新設學程"):
        target = w.chains.target_for(a)
        data = dict(target.data)
        w.chains.answered(a, w.archive(target.url, answer_page(data[YEAR], data[DEPT], groups=groups)), w.warc)
    assert a is not None  # the department only the answers list, planned and not pressed yet
    w.chains.restart_chain(row["id"], "a run stopped")
    row2, shot = w.page(PAGE, SITE, course_page())
    w.chains.page_stored(row2, course_page(), w.warc, shot.warc_offset)
    second = w.actions(row["id"], epoch=2)
    assert [(x["row_key"], x["state"]) for x in second] == [(key("新設學程"), "pending")]
    first = w.actions(row["id"], epoch=1, row_key=key("測試學系"))[0]
    assert (second[0]["form_src_warc"], second[0]["form_src_offset"]) == (first["warc"], first["warc_offset"])
    assert {r["res_key"] for r in w.f.q("SELECT res_key FROM page_res WHERE page_id=? AND res_key LIKE 'pb:%'",
                                        (row["id"],))} == {f"pb:detail:{key(v)}" for v in
                                                           ("測試學系", "測試碩士班", "測試進修班", "新設學程")}


# ---------------------------------------------------------------- retry unpressed


def test_retry_unpressed_puts_back_a_course_table_with_no_press_for_this_year(w):
    never, _shot = w.page(unit(1), SITE, course_page())  # stored before the platform file had the menu
    pending, _got = w.stored(unit(2), SITE, course_page())  # planned, not pressed yet: waits for the run
    done, _got = w.stored(unit(3), SITE, course_page())
    w.f.x("UPDATE action SET state='done', status=200 WHERE list_id=?", (done["id"],))
    failed, _got = w.stored(unit(4), SITE, course_page())
    w.f.x("UPDATE action SET state='mismatch', reason='menu' WHERE list_id=?", (failed["id"],))
    w.now = JULY
    last_year, _got = w.stored(unit(5), SITE, course_page())
    w.f.x("UPDATE action SET state='done', status=200 WHERE list_id=?", (last_year["id"],))
    w.now = NOW
    bare, _shot = w.page(unit(6), SITE, course_page(submit=False))  # the GET wrote its menu unpressed
    w.f.add_page_res(bare["id"], postback.unpressed_entries([BARE], [(BARE, "")]))
    lost, _shot = w.page(LOST, SITE, "<html><body>遺失物招領</body></html>")
    gone, _shot = w.page(unit(7), SITE, course_page())
    w.f.x("UPDATE url SET status=404 WHERE id=?", (gone["id"],))  # the school answered no page: no form to send
    assert w.chains.retry_unpressed(["another.nknu.edu.tw"]) == (0, 0)
    assert w.chains.retry_unpressed([SITE]) == (4, 4)  # one key each: menu:course_table
    for r in (never, failed, last_year, bare):
        back = w.f.row_by_id(r["id"])
        assert (back["state"], back["reason"], back["prio"], back["next_at"]) == ("pending", "chain_restart", 0, 0)
    for r in (pending, done, lost, gone):
        assert w.f.row_by_id(r["id"])["state"] == "done"
    assert w.f.q("SELECT 1 FROM event WHERE code='chain_restart' AND message='retry unpressed: 1'")


def test_retry_unpressed_goes_by_each_option_of_this_year(w):
    """Option by option, as the new GET would press (review of 11-09): a table whose departments were each tried twice
    is a person's and is not fetched again each time for nothing; one department tried once beside others done is put
    back, and its GET presses only that one."""
    tried, _got = w.stored(unit(1), SITE, course_page())  # each department mismatched in two epochs
    w.f.x("UPDATE action SET state='mismatch', reason='menu: x' WHERE list_id=?", (tried["id"],))
    again, shot = w.page(unit(1), SITE, course_page())
    w.chains.page_stored(again, course_page(), w.warc, shot.warc_offset)
    assert len(w.actions(tried["id"], epoch=2)) == 3
    w.f.x("UPDATE action SET state='mismatch', reason='menu: x' WHERE list_id=? AND epoch=2", (tried["id"],))
    one, _got = w.stored(unit(2), SITE, course_page())  # one department failed once, the other two done
    first, second, third = w.actions(one["id"])
    w.f.x("UPDATE action SET state='done', status=200 WHERE id IN (?, ?)", (first["id"], third["id"]))
    w.f.x("UPDATE action SET state='failed', reason='http_503' WHERE id=?", (second["id"],))
    lapsed, _got = w.stored(unit(3), SITE, course_page())  # planned, superseded, and its GET since could send nothing
    w.chains.restart_chain(lapsed["id"], "a run stopped")
    no_year = course_page().replace('<option value="115">115</option>', "")
    row3, shot = w.page(unit(3), SITE, no_year)
    assert w.chains.page_stored(row3, no_year, w.warc, shot.warc_offset).unplanned == (BARE,)
    assert [r["id"] for r, _m in postback.menus_not_sent(w.f, 115)] == [one["id"], lapsed["id"]]
    assert w.chains.retry_unpressed([SITE]) == (2, 2)
    assert (w.f.row_by_id(tried["id"])["state"], w.f.row_by_id(one["id"])["state"]) == ("done", "pending")
    row2, shot = w.page(unit(2), SITE, course_page())
    w.chains.page_stored(row2, course_page(), w.warc, shot.warc_offset)
    assert [(a["row_key"], a["state"]) for a in w.actions(one["id"], epoch=2)] == [(second["row_key"], "pending")]
    assert w.chains.retry_unpressed([SITE]) == (0, 0)  # pending: the run presses it


def test_verify_check_4_names_the_menus_not_sent_and_a_press_line_says_the_department(w):
    """verify names the pages `retry unpressed` puts back (review of 11-09: nothing else said so), and a menu press's
    problem line says what it chose in words, never its urlencoded argument."""
    never, _shot = w.page(unit(1), SITE, course_page())  # stored before the platform file had the menu
    one, _got = w.stored(unit(2), SITE, course_page())
    first, *rest = w.actions(one["id"])
    w.f.x("UPDATE action SET state='mismatch', status=200, reason='menu: asked 115/測試學系, shows 113/測試學系' "
          "WHERE id=?", (first["id"],))
    for a in rest:
        w.f.x("UPDATE action SET state='done', status=200, sniff='html' WHERE id=?", (a["id"],))
    done, _got = w.stored(unit(3), SITE, course_page())
    w.f.x("UPDATE action SET state='done', status=200, sniff='html' WHERE list_id=?", (done["id"],))
    four = {c.no: c for c in monitor.checks(w.f, now=NOW)}["4"]
    assert four.status == monitor.FAIL and len(four.lines) == 1, four.lines
    line = four.lines[0]
    assert line.startswith(f"{SITE}：平台规则 course_table 的选单这一学年度（115）还没送完的网页 2 个")
    assert f"例如 {never['url']}" in line  # the first of them
    assert f"nknu archive retry unpressed --sites {SITE}" in line and four.detail.endswith("的网页 2 个")
    presses = [i.line() for i in monitor.site_status(w.f, now=NOW)[SITE].issues if i.what == "press"]
    assert len(presses) == 1 and f"（选单「{TEXTS[0]}」）" in presses[0], presses
    assert "%" not in presses[0] and "asked 115/測試學系, shows 113/測試學系" in presses[0]


def test_a_rejudge_keeps_a_menu_its_page_cannot_send_unpressed(tmp_path):
    """A rejudge reads a menu as the crawler does (review of 11-09: read with no year, it dropped the mark): a table
    whose year menu does not offer this academic year stays unpressed (要人看), and so does the menu of a done answer
    that cannot be sent (read with the year it asked); a table whose menu can be sent gets no key per department."""
    w = World(tmp_path)
    w.now = NOW
    no_year = course_page().replace('<option value="115">115</option>', "")
    row, got = w.stored(unit(1), SITE, no_year)
    assert got.unplanned == (BARE,)
    w.f.add_page_res(row["id"], postback.unpressed_entries(got.unplanned, got.texts))  # as the crawler writes it
    other, _got = w.stored(unit(2), SITE, course_page())
    a, target = w.press()
    w.answer(a, target.url, course_page("115", "測試學系", submit=False))  # done; its own menu nobody can send
    assert w.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "done"
    assert w.f.q("SELECT 1 FROM page_res WHERE page_id=? AND res_key=?", (other["id"], BARE))
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [SITE], plan=make_plan(make_site(SITE, home=LOST)), clock=lambda: NOW)
    f = Frontier(tmp_path / "frontier.db")
    for page_id in (row["id"], other["id"]):
        entries = f.q("SELECT res_key, need, why FROM page_res WHERE page_id=? AND why=?", (page_id, UNPRESSED))
        assert [tuple(e) for e in entries] == [(BARE, "must", UNPRESSED)], page_id
    f.close()


def test_a_rejudge_presses_no_menu_and_retry_unpressed_then_puts_the_table_back(tmp_path):
    """The way to the current year on an archive whose course tables round one stored: a rejudge reads them with
    today's platform file (the menus need nothing, nothing pressed, nothing written unpressed: a menu its page can send
    is `retry unpressed`'s, and verify check 8 finds nothing stale), then `retry unpressed` puts each back for its GET
    to send the menu."""
    w = World(tmp_path)
    row, _shot = w.page(PAGE, SITE, course_page())
    w.f.add_page_res(row["id"], [{"res_key": f"select:{k}", "how": "unhandled", "role": "click", "need": "must",
                                  "why": "unhandled:unknown"} for k in (YEAR, DEPT)])  # as round one wrote them
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [SITE], plan=make_plan(make_site(SITE, home=LOST)), clock=lambda: NOW)
    f = Frontier(tmp_path / "frontier.db")
    entries = f.q("SELECT * FROM page_res WHERE page_id=? AND how='unhandled'", (row["id"],))
    assert {r["res_key"]: (r["need"], r["why"]) for r in entries if r["res_key"].startswith("select:")} == {
        f"select:{YEAR}": ("no", "unhandled:ignore"), f"select:{DEPT}": ("no", "unhandled:ignore")}
    assert not [r["res_key"] for r in entries if r["why"] == UNPRESSED]
    for r in entries:  # verify check 8's comparison: what is written is what today's platform file says
        assert monitor.expected_unhandled(SITE, r["res_key"], r["text"], r["why"]) == (r["need"], r["why"])
    assert not f.q("SELECT 1 FROM action")  # a rejudge presses nothing
    assert Chains(f, tmp_path).retry_unpressed([SITE]) == (1, 1)
    back = f.row_by_id(row["id"])
    assert (back["state"], back["reason"], back["prio"]) == ("pending", "chain_restart", 0)
    f.close()


def test_the_retry_unpressed_command_says_it_puts_back_the_menus_too(tmp_path):
    w = World(tmp_path)
    w.page(PAGE, SITE, course_page())
    w.sink.close()
    w.f.close()
    run = CliRunner()
    got = run.invoke(app, ["archive", "retry", "--help"])
    flat = re.sub(r"[\s│╭╮╰╯─]", "", got.output)  # the help as typer lays it out, its wrapping and boxes taken off
    assert got.exit_code == 0 and "这一学年度的选单还没送完" in flat and "menu:<规则名>" in flat, got.output
    got = run.invoke(app, ["archive", "retry", "unpressed", "--sites", SITE, "--dir", str(tmp_path)])
    assert got.exit_code == 0 and "1 个网页、1 种" in got.output and "选单" in got.output, got.output
    f = Frontier(tmp_path / "frontier.db")
    assert f.row(PAGE)["state"] == "pending"
    f.close()


# ---------------------------------------------------------------- the export


def test_the_export_says_a_menus_answer_came_of_the_menu(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    f = arch.f
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (SITE, "遗失物招领和开课课表", "全校重要", "1", "其他", LOST))
    sso = {"host": "sso.nknu.edu.tw", "site": SITE, "owner_site": SITE}
    pid = arch.page("table", PAGE, course_page(), title="開課課表（測試）", **sso)
    body = answer_page("115", "測試學系").encode()
    off, sha = arch.put("answer", PAGE, HTML, body)
    common = {"kind": "detail", "step": 1, "target": OK, "host": "sso.nknu.edu.tw", "site": SITE, "queue": SSO_Q,
              "profile": "dataopen2search/course_table", "form_id": "uForm", "sniff": "html", "ctype": HTML,
              "bytes": len(body), "sha256": sha, "warc": arch.rel, "warc_offset": off}
    menu = _action(f, pid, row_key=key("測試學系"), row_text=TEXTS[0], argument=arg("測試學系"), **common)
    plain = _action(f, pid, row_key="k-plain", row_text="另一個按鈕", argument="", **common)
    _res(f, pid, f"pb:detail:{key('測試學系')}", "detail", how="postback", action_id=menu, text=TEXTS[0])
    _res(f, pid, "pb:detail:k-plain", "detail", how="postback", action_id=plain, text="另一個按鈕")
    arch.close()
    out = tmp_path / "out"
    export.export_site(arch.root, SITE, out)
    rows = by_url(rows_of(out / "manifest.csv"))
    row = rows[f"{PAGE}#按钮={TEXTS[0]}"]
    assert (row["类型"], row["标题"], row["按钮编号"]) == ("网页", TEXTS[0], key("測試學系"))
    assert row["怎么来的"] == f"网页 {PAGE} 的选单选「{TEXTS[0]}」后按送出（回传）"
    assert row["本地档案"].startswith("pages/")
    assert rows[f"{PAGE}#按钮=另一個按鈕"]["怎么来的"] == f"列表 {PAGE} 第 1 页上的按钮「另一個按鈕」（回传）"


# ---------------------------------------------------------------- the whole walk, fake network


def test_the_crawler_gets_this_years_table_of_each_department(tmp_path):
    clock = Clock()
    clock.t = NOW

    def press(target):
        data = dict(target.data)
        return snap(target.url, html=answer_page(data[YEAR], data[DEPT]))

    net = PressNet(clock, {("GET", "/robots.txt"): snap("x", 404),
                           ("GET", "opencoursetablepure.aspx"): snap(PAGE, html=course_page()),
                           ("POST", "opencoursetablepure.aspx"): press})
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    seed(crawler, PAGE)
    work(crawler, net, SSO_Q, rounds=40)
    f = crawler.frontier
    page = f.row(PAGE)
    assert page["state"] == "done"
    posts = [u for m, u in net.seen if m == "POST"]
    assert posts == [PAGE] * 3
    acts = f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id ORDER BY a.id")
    assert [(a["row_text"], a["state"], a["result"]) for a in acts] == [(t, "done", "ok") for t in TEXTS]
    assert {a["list_id"] for a in acts} == {page["id"]}
    entries = {r["res_key"]: (r["need"], r["why"]) for r in f.q("SELECT * FROM page_res WHERE page_id=?",
                                                                  (page["id"],))}
    assert entries[f"select:{YEAR}"] == entries[f"select:{DEPT}"] == ("no", "unhandled:ignore")
    assert not [k for k, (need, why) in entries.items() if why == UNPRESSED]
    assert Chains(f, tmp_path, clock=clock).retry_unpressed() == (0, 0)  # this year's menu is sent: nothing put back
    f.close()
