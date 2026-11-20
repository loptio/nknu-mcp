"""11-18 (design docs/superpowers/specs/2026-11-18-course-schedules-design.md §七): a course several teachers give
shows no outline at its outline's address (sso.nknu.edu.tw/stu/18weekssyllabusforenro.aspx?syllabusno=…), only a
notice (「本課程多名老師合開，請先選擇教師」), a teacher menu whose prompt (「請選擇Select」, value select) is the one
chosen, and a 「確定 OK」 submit button: 86 of the first 1,676 outlines fetched on 11-18, each about 3,500 characters of
the site's menus. A teacher's outline is what the form answers when that teacher is chosen and the button pressed. The
menu rule of the course tables (profiles.Menu, gap ⑩ of 11-08) does it, with two things a Menu may now say:

- no year menu (Menu.year ""): a press's argument names the choice alone, its row key and text too; nothing of such a
  menu goes stale with the academic year, so `retry unpressed` never puts its pages back;
- a prompt (Menu.prompt, matched in full against an option's value): an option that is no choice is never sent, and
  the menu is sent only from a page that shows the prompt chosen. A page that shows a teacher chosen (a course with one
  teacher: 1,590 of the 1,676; a press's answer) is that teacher's outline already: no press, and no gap written.

A page that asks and cannot be sent (no button, no teacher to choose) is written unpressed (要人看), never a silent gap.
The export titles a teacher's outline by its course's name and the teacher. Made-up courses and teachers only."""

from __future__ import annotations

from urllib.parse import quote, urlencode

import pytest

from nknu.archive import export, postback, profiles
from nknu.archive.crawler import Crawler
from nknu.archive.postback import UNPRESSED, Chains, Stored, read_page
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_course_menu_1109 import NOW
from tests.archive.test_export_1027 import HTML, _action, _res, by_url, make_archive, rows_of
from tests.archive.test_pilot_fixes import SSO_Q, PressNet, work
from tests.archive.test_postback import World

SITE = "sso.nknu.edu.tw/stu"
PAGE = "https://sso.nknu.edu.tw/stu/18weekssyllabusforenro.aspx?syllabusno=1150000001"
TEA, OK = "ctl00$phMain$ddlTea", "ctl00$phMain$btnOk"
BARE = "postback:ctl*$phMain$btnOk|@menu"  # the key of a menu nobody could press (要人看)
PROMPT = "select"
# a teacher's value as the school writes it (padded with spaces, sent as written) and the name shown
TEACHERS = (("115|1|ZZ101|19008     |ZZZZ306|T0001     |0001", "師甲O"),
            ("115|1|ZZ101|19011     |ZZZZ306|T0002     |0002", "師乙O"))
RULE = "outline_teachers"


def outline_page(chosen: str = PROMPT, *, teachers=TEACHERS, submit: bool | None = None, form_id: str = "uForm",
                 state: str = "VS-OUTLINE", extra: str = "") -> str:
    """An outline's page as the school writes it (made up): the teacher menu with `chosen` selected; the prompt chosen
    shows the notice and the submit button (submit: None as the school does, a button only with the prompt chosen), a
    teacher chosen shows the print link and the course's cells."""
    opts = "".join(f'<option{" selected=" + chr(34) + "selected" + chr(34) if v == chosen else ""} value="{v}">{t}'
                   "</option>" for v, t in ((PROMPT, "請選擇Select"), *teachers))
    asks = chosen == PROMPT
    button = (f'<input type="submit" name="{OK}" value="確定 OK" id="ctl00_phMain_btnOk" />'
              if (asks if submit is None else submit) else "")
    name = dict(teachers).get(chosen, "")
    body = ('<div role="alert">本課程多名老師合開，請先選擇教師</div>' if asks else
            """<a id="ctl00_phMain_uPrintFriendly" href="javascript:printFriendly('divData', null)">友善列印 Print</a>"""
            '<div id="divData"><h4>課程資料 Course Information</h4><div id="ctl00_phMain_plCourseBasicData"><table>'
            "<tr><td>開課學年</td><td>Year</td><td>115</td></tr>"
            "<tr><td>科目名稱</td><td>Course</td><td>甲課概論 Introduction to A</td></tr>"
            f"<tr><td>授課教師</td><td>Teacher</td><td>{name}</td></tr></table></div>"
            f"<p>第一週 {name} 的課程說明</p></div>")
    return ('<!DOCTYPE html><html><head><title>校務平台（測試）</title></head><body>'
            f'<form method="post" action="./18weekssyllabusforenro.aspx?syllabusno=1150000001" id="{form_id}">'
            f'<div class="aspNetHidden"><input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="{state}" />'
            '<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="0000TEST" /></div>'
            f'<h2>教學大綱 Syllabus</h2><label>授課教師Teacher：</label><select name="{TEA}" id="ctl00_phMain_ddlTea">'
            f"{opts}</select>{button}{body}</form>{extra}</body></html>")


def key(value: str, url: str = PAGE) -> str:
    """A press's row key: the page's, from the value exactly as sent (quoted); no year in it."""
    return postback.row_key(SITE, url, quote(value, safe=""), category=RULE)


def arg(value: str) -> str:
    return profiles.menu_argument(profiles.OUTLINE_TEACHERS, 115, value)


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.now = NOW
    yield world
    world.sink.close()
    world.f.close()


def test_the_course_query_s_profile_holds_the_rule_and_check_profiles_stays_empty():
    m = profiles.OUTLINE_TEACHERS
    assert profiles.STU.menus == (m,) and profiles.STU.forms == ("uForm",)
    assert (m.name, m.form, m.submit, m.year, m.each, m.prompt) == (RULE, "uForm", OK, "", TEA, PROMPT)
    assert profiles.check_profiles() == []
    assert profiles.decision(SITE, f"select:{TEA}") == "ignore"  # the rule sends it: the menu itself needs nothing
    assert profiles.decision(SITE, BARE) == "press"  # one its page could not send reads as unpressed (要人看)


def test_a_menu_argument_without_a_year_names_the_choice_alone():
    v = TEACHERS[0][0]
    assert arg(v) == profiles.MENU_ARG + "?" + urlencode([(TEA, v)])
    assert profiles.menu_choice(arg(v)) == ((TEA, v),)
    assert profiles.menu_year(profiles.OUTLINE_TEACHERS, arg(v)) is None
    # the course tables' as before
    assert profiles.menu_year(profiles.COURSE_TABLE, profiles.menu_argument(profiles.COURSE_TABLE, 115, "x")) == 115


def test_read_page_presses_each_teacher_of_a_page_that_asks_for_one():
    pr = read_page(profiles.STU, PAGE, outline_page(), site=SITE, list_url=PAGE, year=115)
    assert [(p.kind, p.target, p.argument, p.row_key, p.text, p.step, p.form_id, p.rowset) for p in pr.presses] == [
        ("detail", OK, arg(v), key(v), t, 1, "uForm", RULE) for v, t in TEACHERS]  # never the prompt
    assert (pr.rowset, pr.walk, pr.unknown, pr.unplanned, pr.unplanned_at) == (None, "none", (), (), ())
    other = read_page(profiles.STU, PAGE.replace("0001", "0002"), outline_page(), site=SITE,
                      list_url=PAGE.replace("0001", "0002"), year=115)
    assert not {p.row_key for p in other.presses} & {p.row_key for p in pr.presses}  # another course's outline


@pytest.mark.parametrize("html", [
    outline_page(TEACHERS[0][0], teachers=TEACHERS[:1]),  # one teacher: the school shows the outline at once
    outline_page(TEACHERS[1][0]),  # a press's answer: that teacher's outline
    outline_page(TEACHERS[1][0], submit=True),  # the same, were the button still there
])
def test_a_page_that_shows_a_teacher_s_outline_presses_nothing_and_is_no_gap(html):
    pr = read_page(profiles.STU, PAGE, html, site=SITE, list_url=PAGE, year=115)
    assert (pr.presses, pr.unplanned, pr.unplanned_at, pr.unknown) == ((), (), (), ())
    assert BARE not in dict(pr.texts)


def test_no_year_presses_no_menu():
    """A reading that names no year (rejudge, the export) presses no menu and says nothing of it, as for the course
    tables."""
    pr = read_page(profiles.STU, PAGE, outline_page(), site=SITE, list_url=PAGE)
    assert (pr.presses, pr.unplanned, pr.unplanned_at) == ((), (), ())


@pytest.mark.parametrize(("html", "text"), [
    (outline_page(submit=False), ""),  # it asks, and has no button
    (outline_page(form_id="form1"), "確定 OK"),  # the form is another
    (outline_page().replace(f'name="{TEA}"', 'name="ctl00$phMain$ddlTeacher"'), "確定 OK"),  # no teacher menu
    (outline_page(submit=False, extra=f'<input type="submit" name="{OK}" value="確定 OK" />'), "確定 OK"),
])
def test_a_page_that_asks_and_cannot_be_sent_is_written_unpressed(html, text):
    pr = read_page(profiles.STU, PAGE, html, site=SITE, list_url=PAGE, year=115)
    assert pr.presses == ()
    assert (pr.unplanned, pr.unplanned_at) == ((BARE,), ((OK, profiles.MENU_ARG),))
    assert dict(pr.texts)[BARE] == text


def test_the_course_tables_presses_are_as_they_were():
    """The year menu's rule is untouched: every option offered, the year in its argument, key and text."""
    from tests.archive import test_course_menu_1109 as old

    pr = read_page(profiles.DATAOPEN, old.PAGE, old.course_page(), site=old.SITE, list_url=old.PAGE, year=115)
    assert [(p.argument, p.row_key, p.text) for p in pr.presses] == [
        (old.arg(v), old.key(v), t) for v, t in zip(("測試學系", "測試碩士班", "測試進修班"), old.TEXTS, strict=True)]


# ---------------------------------------------------------------- the chain: planning, the POST, the answer


def test_a_stored_page_plans_one_press_per_teacher(w):
    row, got = w.stored(PAGE, SITE, outline_page())
    assert (got.page_role, got.presses, got.ended_by, got.unplanned) == (None, 2, None, ())
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["argument"], a["row_key"], a["row_text"], a["form_id"], a["profile"],
             a["state"]) for a in acts] == [
        ("detail", OK, arg(v), key(v), t, "uForm", f"stu_courses/{RULE}", "pending") for v, t in TEACHERS]
    one, got = w.stored(PAGE.replace("0001", "0003"), SITE, outline_page(TEACHERS[0][0], teachers=TEACHERS[:1]))
    assert got == Stored(None, 0, None, ()) and not w.actions(one["id"])  # one teacher: nothing to press


def test_a_press_is_the_form_with_the_teacher_chosen_and_its_submit_button(w):
    w.stored(PAGE, SITE, outline_page())
    _a, target = w.press()
    assert (target.method, target.url) == ("POST", PAGE)
    assert list(target.data) == [("__VIEWSTATE", "VS-OUTLINE"), ("__VIEWSTATEGENERATOR", "0000TEST"),
                                 (TEA, TEACHERS[0][0]), (OK, "確定 OK")]  # the value as written, its spaces kept


def test_an_answer_that_shows_the_teacher_asked_is_done_and_asks_for_nothing_more(w):
    row, _got = w.stored(PAGE, SITE, outline_page())
    a, target = w.press()
    out = w.answer(a, target.url, outline_page(TEACHERS[0][0], state="VS-ANSWER"))
    assert not out.restart and out.note == ""
    assert tuple(w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]) == ("done", None)
    assert w.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"
    assert len(w.actions(row["id"])) == 2  # the answer's own menu plans no teacher again
    assert not w.f.q("SELECT 1 FROM page_res WHERE page_id=? AND why=?", (row["id"], UNPRESSED))


def test_an_answer_that_shows_the_prompt_again_is_a_mismatch(w):
    w.stored(PAGE, SITE, outline_page())
    a, target = w.press()
    out = w.answer(a, target.url, outline_page(state="VS-AGAIN"))
    assert out.note == f"menu: asked {TEACHERS[0][0]}, shows {PROMPT}"
    assert w.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "mismatch"


def test_retry_unpressed_puts_back_no_outline_page(w):
    """A menu with no year has nothing to send again when the academic year turns, and a page that shows a teacher's
    outline never had: neither is a page whose menu was not sent."""
    w.stored(PAGE, SITE, outline_page())
    w.stored(PAGE.replace("0001", "0003"), SITE, outline_page(TEACHERS[0][0], teachers=TEACHERS[:1]))
    assert postback.menus_not_sent(w.f, 115) == [] and postback.menus_not_sent(w.f, 116) == []
    assert w.chains.retry_unpressed() == (0, 0)


# ---------------------------------------------------------------- a course with no teacher: the school has no outline


def no_teacher_page() -> str:
    """An outline's page of a course the school lists no teacher for, as it writes it (11-18: 268 of the term's 2,309
    outlines; the classes' tables leave those courses' teacher cell empty): the menu holds the prompt alone, the
    button is there, nothing of the course is."""
    return ('<!DOCTYPE html><html><head><title>校務平台（測試）</title></head><body><form method="post" id="uForm">'
            '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS" /><fieldset><legend>請先選擇授課教師 '
            'Select Teacher</legend><div class="form-inline"><label>授課教師Teacher：</label>\r\n        '
            f'<select name="{TEA}" id="ctl00_phMain_ddlTea" class="custom-select mr-1">\r\n\t<option selected="selected" '
            'value="select">請選擇Select</option>\r\n\r\n</select>\r\n        '
            f'<input type="submit" name="{OK}" value="確定 OK" id="ctl00_phMain_btnOk" /></div></fieldset></form>'
            "</body></html>")


@pytest.mark.parametrize("html", [no_teacher_page(), outline_page(teachers=())], ids=["as the school writes", "bare"])
def test_a_page_whose_menu_offers_no_teacher_has_nothing_to_press_and_is_no_gap(html):
    pr = read_page(profiles.STU, PAGE, html, site=SITE, list_url=PAGE, year=115)
    assert (pr.presses, pr.unplanned, pr.unplanned_at) == ((), (), ())
    assert BARE not in dict(pr.texts)


def test_the_school_s_answer_of_no_teacher_is_an_answer_of_no_data():
    rule = profiles.OUTLINE_NO_TEACHER
    assert rule in profiles.STU.no_data and rule.confirm and rule.words.startswith("学校回答没有资料")
    assert profiles.school_no_data(PAGE, no_teacher_page()) is rule
    assert profiles.school_no_data(PAGE, outline_page(teachers=())) is rule
    assert profiles.school_no_data(PAGE, outline_page()) is None  # it asks, teachers to choose
    assert profiles.school_no_data(PAGE, outline_page(TEACHERS[0][0])) is None  # a teacher's outline
    one = outline_page(TEACHERS[0][0], teachers=TEACHERS[:1])
    assert profiles.school_no_data(PAGE, one) is None  # one teacher, shown by the school at once
    assert profiles.school_no_data("https://sso.nknu.edu.tw/stu/scheduledepartment.aspx?cdes=a", no_teacher_page()) is None
    assert profiles.check_profiles() == []


def test_the_crawler_flags_an_outline_with_no_teacher_as_a_page_that_holds_nothing(tmp_path):
    """Read as the school's answer of no data, the page is flagged no_rows as it is read (outcome.SAYS_NOTHING: got,
    and the thin step never marks it 页面几乎是空的: 爬虫judge 11-18 「没有老师可选，也就没有东西可按，算不上缺口」)."""
    clock = Clock()
    clock.t = NOW
    net = PressNet(clock, {("GET", "/robots.txt"): snap("x", 404),
                           ("GET", "18weekssyllabusforenro.aspx"): snap(PAGE, html=no_teacher_page())})
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    seed(crawler, PAGE)
    work(crawler, net, SSO_Q, rounds=10)
    f = crawler.frontier
    row = f.row(PAGE)
    assert row["state"] == "done" and "no_rows" in (row["flags"] or "").split(",")
    assert [m for m, _u in net.seen if m == "POST"] == []
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == "ok"
    f.close()


def test_an_outline_that_asks_or_shows_a_teacher_is_not_flagged(tmp_path):
    clock = Clock()
    clock.t = NOW
    net = PressNet(clock, {("GET", "/robots.txt"): snap("x", 404),
                           ("GET", "18weekssyllabusforenro.aspx"): snap(PAGE, html=outline_page(TEACHERS[0][0]))})
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    seed(crawler, PAGE)
    work(crawler, net, SSO_Q, rounds=10)
    assert "no_rows" not in (crawler.frontier.row(PAGE)["flags"] or "").split(",")
    crawler.frontier.close()


# ---------------------------------------------------------------- the whole walk, fake network; the export


def test_the_crawler_gets_each_teacher_s_outline(tmp_path):
    clock = Clock()
    clock.t = NOW

    def press(target):
        return snap(target.url, html=outline_page(dict(target.data)[TEA], state="VS-ANSWER"))

    net = PressNet(clock, {("GET", "/robots.txt"): snap("x", 404),
                           ("GET", "18weekssyllabusforenro.aspx"): snap(PAGE, html=outline_page()),
                           ("POST", "18weekssyllabusforenro.aspx"): press})
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    seed(crawler, PAGE)
    work(crawler, net, SSO_Q, rounds=40)
    f = crawler.frontier
    page = f.row(PAGE)
    assert page["state"] == "done"
    assert [u for m, u in net.seen if m == "POST"] == [PAGE] * 2
    acts = f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id ORDER BY a.id")
    assert [(a["row_text"], a["state"], a["result"]) for a in acts] == [(t, "done", "ok") for _v, t in TEACHERS]
    entries = {r["res_key"]: (r["need"], r["why"]) for r in f.q("SELECT * FROM page_res WHERE page_id=?",
                                                                  (page["id"],))}
    assert entries[f"select:{TEA}"] == ("no", "unhandled:ignore")
    assert not [k for k, (_need, why) in entries.items() if why == UNPRESSED]
    assert Chains(f, tmp_path, clock=clock).retry_unpressed() == (0, 0)
    f.close()


def test_the_export_titles_a_teacher_s_outline_by_its_course_and_the_teacher(tmp_path):
    """A press's row is titled by the press's words (the teacher's name here, the year and department of a course
    table): an outline named by a teacher alone says nothing of the course. Where the answer is a kind of page with a
    title cell of its own (profiles.PageTitle) and that cell has words, the title is the cell's, the choice after it in
    brackets; the prompt's own page keeps its row and its file."""
    arch = make_archive(tmp_path / "archive").open()
    f = arch.f
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (SITE, "课程查询", "全校重要", "1", "其他", "https://sso.nknu.edu.tw/stu/"))
    sso = {"host": "sso.nknu.edu.tw", "site": SITE, "owner_site": SITE}
    pid = arch.page("outline", PAGE, outline_page(), title="教學大綱 Syllabus", **sso)
    value, name = TEACHERS[0]
    body = outline_page(value, state="VS-ANSWER").encode()
    off, sha = arch.put("answer", PAGE, HTML, body)
    act = _action(f, pid, kind="detail", step=1, target=OK, host="sso.nknu.edu.tw", site=SITE, queue=SSO_Q,
                  profile=f"stu_courses/{RULE}", form_id="uForm", sniff="html", ctype=HTML, bytes=len(body),
                  sha256=sha, warc=arch.rel, warc_offset=off, row_key=key(value), row_text=name, argument=arg(value))
    _res(f, pid, f"pb:detail:{key(value)}", "detail", how="postback", action_id=act, text=name)
    arch.close()
    out = tmp_path / "out"
    export.export_site(arch.root, SITE, out)
    rows = by_url(rows_of(out / "manifest.csv"))
    row = rows[f"{PAGE}#按钮={name}"]
    assert (row["类型"], row["标题"], row["按钮编号"]) == ("网页", f"甲課概論 Introduction to A（{name}）", key(value))
    assert row["怎么来的"] == f"网页 {PAGE} 的选单选「{name}」后按送出（回传）"
    assert row["本地档案"].startswith("pages/")
    assert rows[PAGE]["本地档案"].startswith("pages/")  # the page that asks stays a row with its file


def test_the_export_says_a_page_that_only_asks_holds_its_content_in_its_press_rows(tmp_path):
    """11-19 (the content stage's ask): the outline page of a course several teachers give is a row with a file that
    holds the prompt and the menu alone; what it is about stands in the rows its presses brought. The export says so
    in 缺什么 with words that begin as export.ASKS_FIRST does, by which the content stage leaves the page out. A
    teacher's own answer, a page that shows an outline at once, and a page with no teacher to choose do not say it."""
    arch = make_archive(tmp_path / "archive").open()
    f = arch.f
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (SITE, "课程查询", "全校重要", "1", "其他", "https://sso.nknu.edu.tw/stu/"))
    sso = {"host": "sso.nknu.edu.tw", "site": SITE, "owner_site": SITE}
    pid = arch.page("outline", PAGE, outline_page(), title="教學大綱 Syllabus", **sso)
    value, name = TEACHERS[0]
    body = outline_page(value, state="VS-ANSWER").encode()
    off, sha = arch.put("answer", PAGE, HTML, body)
    act = _action(f, pid, kind="detail", step=1, target=OK, host="sso.nknu.edu.tw", site=SITE, queue=SSO_Q,
                  profile=f"stu_courses/{RULE}", form_id="uForm", sniff="html", ctype=HTML, bytes=len(body),
                  sha256=sha, warc=arch.rel, warc_offset=off, row_key=key(value), row_text=name, argument=arg(value))
    _res(f, pid, f"pb:detail:{key(value)}", "detail", how="postback", action_id=act, text=name)
    one = PAGE.replace("1150000001", "1150000002") if "1150000001" in PAGE else PAGE + "&x=2"
    arch.page("one", one, outline_page(TEACHERS[0][0], teachers=TEACHERS[:1]), title="教學大綱 Syllabus", **sso)
    none = PAGE.replace("1150000001", "1150000003") if "1150000001" in PAGE else PAGE + "&x=3"
    arch.page("none", none, no_teacher_page(), title="教學大綱 Syllabus", **sso)
    arch.close()
    out = tmp_path / "out"
    export.export_site(arch.root, SITE, out)
    rows = by_url(rows_of(out / "manifest.csv"))
    assert export.ASKS_FIRST == "内容在按钮行"
    assert rows[PAGE]["缺什么"].startswith(export.ASKS_FIRST) and rows[PAGE]["本地档案"].startswith("pages/")
    assert export.ASKS_FIRST not in rows[f"{PAGE}#按钮={name}"]["缺什么"]
    assert export.ASKS_FIRST not in rows[one]["缺什么"]
    assert export.ASKS_FIRST not in rows[none]["缺什么"]
