"""The export's mark of a school page that answers it has no data (the owner 11-09 「照这样做」 to the crawler session's
question 「要在导出把开课课表的空回答标「学校回答没有资料」，再重导那一站吗？」; the words are the content session's ask:
it leaves the rows whose 缺什么 holds 「学校回答没有资料」 out and counts them). The 11-09 crawl pressed the 115 academic
year of each department's course table and the school answered every one with an empty form and its pop-up
「系所未提供相關資料.」, a script call no reading of the page's text shows; leaving them out by address would leave out
the real tables once the school publishes 115. So a course table (sso.nknu.edu.tw/dataopen2search/
opencoursetablepure.aspx) whose HTML as archived carries the pop-up says 「学校回答没有资料（系所未提供相關資料）」 in
缺什么, in any site's export that lists it (by the page's own address, never the site exported), its 结果, 分类 and
完整度 as the archive judged them, and the README names each such row; the mark goes when the message does. Export
only: the crawler's own reading (school_says) and the content stage's page version stay as they were. Where neither
says how (【Claude 的执行办法】): every row whose file is such a page says it too, once (a redirect landing on it, a row
sharing its file, a page whose 缺什么 the export judged again). The review of 11-09 (its findings fixed here; how,
【Claude 的执行办法】): exported with the rule and without it, nothing but 缺什么 and 备注 differs, nor any file, and the
README and the command differ by their lines on it alone; only the school's no-data message marks a page, never another
pop-up; the words come last; the README's section and summary, the column help and the command quote the words as the
cells hold them. Made-up pages in the platforms' templates (the course tables of test_course_menu_1109), no school text
but the system message itself, no person's name."""

from __future__ import annotations

import codecs
import dataclasses
from pathlib import Path

import pytest

from nknu.archive import export, outcome, profiles
from tests.archive.test_course_menu_1109 import (
    LOST,
    NOW,
    OK,
    PAGE,
    SITE,
    TEXTS,
    answer_page,
    arg,
    course_page,
    key,
    unit,
)
from tests.archive.test_export_1027 import BASE, HTML, NOTICE_BLANK, _action, _add, _res, by_url, make_archive, rows_of
from tests.archive.test_export_1027 import SITE as ACA
from tests.archive.test_export_dates_1108 import SSO, WEB
from tests.archive.test_pilot_fixes import SSO_Q

WORDS = "学校回答没有资料（系所未提供相關資料）"  # the content session's words (its ask of 11-09)
SECTION = "## 学校回答没有资料的网页"
BULLET = "**学校回答没有资料**"  # the README's summary line on them
COMMAND = "学校回答没有资料的"  # how the command's line on them begins
# the school's pop-up as round one's course tables carry it, a script call just before </form> (the lab's exports,
# read only 11-09): the only school text in this file
NOTY = "<script type=\"text/javascript\">\n//<![CDATA[\nshowNoty('系所未提供相關資料.');//]]>\n</script>\n"
OTHER_NOTY = NOTY.replace("系所未提供相關資料.", "測試訊息.")  # another pop-up of the same call (made up)
SSO_ROW = {"host": "sso.nknu.edu.tw", "site": SITE, "owner_site": SITE}
UNIT_ROW = {"host": "sso.nknu.edu.tw", "site": WEB, "owner_site": WEB}
UNIT_PAGE = SSO + "592&selpage=Course"  # a made-up page of a unit's WebIdx site embedding course tables
CLV2_PAGE = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=999009"  # a made-up page of a cLv2 unit site
TITLE = "開課課表（測試）"
OLD = "https://sso.nknu.edu.tw/dataopen2search/opencoursetable.aspx?unit=999008"  # a made-up old address
PLAN = SSO + "592&selpage=Plan"  # a unit's page sent to the course table its other page embeds


def no_data_page(year: str = "115", dept: str = "測試學系") -> str:
    """A course table the school answered with no data: the year and department asked shown, no course, and the
    school's pop-up just before </form>."""
    return course_page(year, dept, state=f"VS-{year}-{dept}", courses=()).replace("</form>", NOTY + "</form>")


def other_pop_up_page(courses: tuple[str, ...] = ("測試課程甲",)) -> str:
    """A course table with another pop-up of the school's (made up) just before </form>: its courses shown, or none."""
    return course_page(courses=courses).replace("</form>", OTHER_NOTY + "</form>")


def lost_page() -> str:
    """The lost-and-found page (made up), here with the course tables' pop-up."""
    return ('<!DOCTYPE html><html><head><title>遺失物招領（測試）</title></head><body><form method="post" '
            f'action="./lostandfound.aspx" id="uForm"><ul><li>測試物品</li></ul>{NOTY}</form></body></html>')


def _press(arch, list_id: int, dept: str, text: str, html: str) -> int:
    """A press of PAGE's menus (year 115, department dept; its words text) answered with html, as the crawler keeps
    one (test_course_menu_1109)."""
    body = html.encode()
    off, sha = arch.put(f"answer:{dept}", PAGE, HTML, body)
    common = {"kind": "detail", "step": 1, "target": OK, "host": "sso.nknu.edu.tw", "site": SITE, "queue": SSO_Q,
              "profile": "dataopen2search/course_table", "form_id": "uForm", "sniff": "html", "ctype": HTML,
              "bytes": len(body), "sha256": sha, "warc": arch.rel, "warc_offset": off}
    aid = _action(arch.f, list_id, row_key=key(dept), row_text=text, argument=arg(dept), **common)
    _res(arch.f, list_id, f"pb:detail:{key(dept)}", "detail", how="postback", action_id=aid, text=text)
    return aid


def _course_tables(arch) -> None:
    """dataopen2search in the fixture archive: PAGE by GET (year 113, its courses shown) and two presses of its menus
    for 115, the school answering the first with no data and the second with a course; unit(2) by GET, the default
    year of its department with no data; the lost-and-found page with the same pop-up."""
    arch.f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
             (SITE, "遗失物招领和开课课表", "全校重要", "1", "其他", LOST))
    table = arch.page("table", PAGE, course_page(), title=TITLE, **SSO_ROW)
    _press(arch, table, "測試學系", TEXTS[0], no_data_page())
    _press(arch, table, "測試碩士班", TEXTS[1], answer_page("115", "測試碩士班"))
    arch.page("empty113", unit(2), no_data_page("113"), title=TITLE, **SSO_ROW)
    arch.page("lost", LOST, lost_page(), title="遺失物招領（測試）", **SSO_ROW)


def _unit_site(arch) -> None:
    """A unit's WebIdx site (made up) whose page embeds two course tables dataopen2search owns: unit(5) answered with
    no data, unit(6) with its courses."""
    arch.f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
             (WEB, "測試單位", "全校重要", "1", "sso_webidx", UNIT_PAGE))
    html = (f'<html><body><h2>課程資訊</h2><iframe src="{unit(5)}"></iframe><iframe src="{unit(6)}"></iframe>'
            "</body></html>")
    home = arch.page("unit", UNIT_PAGE, html, title="課程資訊", **UNIT_ROW)
    t5 = arch.page("t5", unit(5), no_data_page("113"), title=TITLE, **SSO_ROW)
    t6 = arch.page("t6", unit(6), course_page(), title=TITLE, **SSO_ROW)
    _res(arch.f, home, unit(5), "iframe", res_id=t5)
    _res(arch.f, home, unit(6), "iframe", res_id=t6)


def _moved(arch, url: str, to: str, **cols) -> int:
    """An address the server answered with a redirect to `to` (302), as the crawler keeps it."""
    off, sha = arch.put(url, url, "text/html", b"moved", headers={"location": to}, status=302)
    return _add(arch.f, url=url, final_url=to, status=302, bytes=5, sha256=sha, warc=arch.rel, warc_offset=off,
                ctype="text/html", sniff="html", page_type="t:ok", **cols)


def _redirects(arch) -> None:
    """unit(8), answered with no data, and OLD sent to it; PLAN, the unit's page, sent to unit(5) (with _unit_site)."""
    arch.page("t8", unit(8), no_data_page("113"), title=TITLE, **SSO_ROW)
    _moved(arch, OLD, unit(8), **SSO_ROW)
    _moved(arch, PLAN, unit(5), **UNIT_ROW)


def _mixed(arch) -> None:
    """unit(9) answered with no data, a page the crawler could not decode (a Big5 comment in a UTF-8 page): item ⑧."""
    body = codecs.BOM_UTF8 + no_data_page("113").encode().replace(
        b"<form", b"<!-- " + "選單".encode("cp950") + b" --><form", 1)
    arch.page("mixed", unit(9), body, ctype="text/html;charset=utf-8", flags="decode", title=TITLE, **SSO_ROW)


def _other_pop_ups(arch) -> None:
    """Two course tables with another pop-up: unit(10) showing its courses, unit(11) an empty form."""
    arch.page("other10", unit(10), other_pop_up_page(), title=TITLE, **SSO_ROW)
    arch.page("other11", unit(11), other_pop_up_page(courses=()), title=TITLE, **SSO_ROW)


def _archive(tmp_path, *builds):
    """The fixture archive (test_export_1027's 教务处) with these added, its frontier.db closed."""
    arch = make_archive(tmp_path / "archive").open()
    for build in builds:
        build(arch)
    arch.close()
    return arch


def _exported(arch, tmp_path, site: str = SITE, name: str = "out") -> tuple[dict[str, dict[str, str]], str]:
    out = tmp_path / name
    export.export_site(arch.root, site, out)
    return by_url(rows_of(out / "manifest.csv")), (out / "README.md").read_text(encoding="utf-8")


def _judged(row: dict[str, str]) -> tuple[str, str, str]:
    return row["结果"], row["分类"], row["完整度"]


def _section(readme: str) -> str:
    return readme.split(SECTION + "\n\n", 1)[1].split("\n## ", 1)[0]


# ---------------------------------------------------------------- the platform file


def test_the_rule_is_the_course_tables_and_goes_by_the_pages_own_address():
    rule = profiles.COURSE_NO_DATA
    assert profiles.DATAOPEN.no_data == (rule,) and rule.words == WORDS and rule.why.strip()
    # what the content stage leaves rows out by: every rule's words begin with it
    assert export.NO_DATA == "学校回答没有资料"
    assert all(n.words.startswith(export.NO_DATA) for p in profiles.PROFILES for n in p.no_data)
    html = no_data_page()
    # a press's answer has its list's address (a fragment is no path); the path is read in lowercase
    for url in (PAGE, unit(2), f"{PAGE}#按钮={TEXTS[0]}", PAGE.replace("opencoursetablepure", "OpenCourseTablePure")):
        assert profiles.school_no_data(url, html) is rule, url
    assert profiles.school_no_data(PAGE, course_page()) is None  # its courses shown, no pop-up
    # only the school's no-data message counts: another pop-up, on a table with its courses or with an empty form
    for page in (other_pop_up_page(), other_pop_up_page(courses=())):
        assert "showNoty(" in page and profiles.school_no_data(PAGE, page) is None
    # the lost-and-found page; pages of other platforms with the pop-up (a unit's WebIdx and cLv2 pages, 教务处's at a
    # path that reads the same)
    others = (UNIT_PAGE, CLV2_PAGE, BASE + "dataopen2search/opencoursetablepure.aspx")
    assert all(profiles.profile_for(url) not in (None, profiles.DATAOPEN) for url in others)
    for url in (LOST, LOST + "#/dataopen2search/opencoursetablepure.aspx", *others):
        assert profiles.school_no_data(url, html) is None, url
    assert profiles.school_no_data(PAGE, None) is None and profiles.school_no_data(PAGE, "") is None


def test_check_profiles_stays_empty_and_names_a_no_data_rule_that_does_not_compile(monkeypatch):
    assert profiles.check_profiles() == []
    bad = dataclasses.replace(profiles.COURSE_NO_DATA, paths="(", says="[")
    monkeypatch.setattr(profiles, "PROFILES", tuple(dataclasses.replace(p, no_data=(bad,)) if p is profiles.DATAOPEN
                                                    else p for p in profiles.PROFILES))
    got = profiles.check_profiles()
    assert len(got) == 2 and all(x.startswith("dataopen2search: ") for x in got), got


def test_the_crawlers_own_reading_of_these_pages_is_as_before():
    """Export only: what the crawler reads of a school page answering for a file (school_says, which changes the
    crawl's state and flags) says nothing of the pop-up, and the platform's sayings are what they were."""
    for url, html in ((PAGE, no_data_page()), (unit(2), no_data_page("113")), (LOST, lost_page())):
        assert profiles.school_says(url, html) is None, url
    assert profiles.DATAOPEN.says == () and profiles.file_says(no_data_page()) is None


def test_the_content_stages_page_version_does_not_read_the_rule():
    """PAGE_VERSION (nknu.totext.pagetext, read only here) digests what the content stage's page reading takes of the
    profiles; the new field is none of it, so no page is read again for it."""
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    without = tuple(dataclasses.replace(p, no_data=()) for p in profiles.PROFILES)
    assert pagetext.page_version(without) == pagetext.page_version(profiles.PROFILES) == pagetext.PAGE_VERSION


# ---------------------------------------------------------------- the export


def test_a_menu_press_the_school_answered_with_no_data_says_so_and_keeps_its_judgement(tmp_path):
    rows, _readme = _exported(_archive(tmp_path, _course_tables), tmp_path)
    empty, shown = rows[f"{PAGE}#按钮={TEXTS[0]}"], rows[f"{PAGE}#按钮={TEXTS[1]}"]
    assert empty["缺什么"] == WORDS and shown["缺什么"] == ""
    assert empty["本地档案"].startswith("pages/") and empty["按钮编号"] == key("測試學系")
    # 结果, 分类 and 完整度 as the archive judged them: those of the press the school answered with a course
    assert _judged(empty) == _judged(shown) == (outcome.RESULT_LABEL["ok"], "抓到了", "抓到了")
    assert empty["备注"].count(export.NO_DATA_NOTE) == 1 and export.NO_DATA_NOTE not in shown["备注"]


def test_a_course_table_got_by_get_with_the_pop_up_says_so_and_one_showing_courses_does_not(tmp_path):
    rows, _readme = _exported(_archive(tmp_path, _course_tables), tmp_path)
    empty, shown = rows[unit(2)], rows[PAGE]
    assert empty["缺什么"] == WORDS and shown["缺什么"] == ""
    assert _judged(empty) == _judged(shown) == ("抓到了", "抓到了", outcome.LEVEL_LABEL["complete"])
    assert export.NO_DATA_NOTE in empty["备注"] and export.NO_DATA_NOTE not in shown["备注"]


def test_the_lost_and_found_page_and_another_sites_page_with_the_pop_up_are_not_marked(tmp_path):
    def other(arch):  # 教务处's own page (c.nknu) at a path that reads the same, with the pop-up
        arch.page("noty", BASE + "dataopen2search/opencoursetablepure.aspx", no_data_page())

    arch = _archive(tmp_path, _course_tables, other)
    rows, _readme = _exported(arch, tmp_path)
    assert rows[LOST]["本地档案"] and rows[LOST]["缺什么"] == "" and export.NO_DATA_NOTE not in rows[LOST]["备注"]
    aca, readme = _exported(arch, tmp_path, ACA, "aca")
    page = aca[BASE + "dataopen2search/opencoursetablepure.aspx"]
    assert page["本地档案"] and export.NO_DATA not in page["缺什么"]
    assert not [r for r in aca.values() if export.NO_DATA in r["缺什么"]]
    assert SECTION not in readme and BULLET not in readme


def test_a_unit_sites_export_marks_the_course_table_it_lists(tmp_path):
    """9 SSO unit sites' exports list a course table the school answered with no data (each links one): the rule goes
    by the page's own address, whatever site is exported."""
    rows, readme = _exported(_archive(tmp_path, _unit_site), tmp_path, WEB)
    empty, shown, home = rows[unit(5)], rows[unit(6)], rows[UNIT_PAGE]
    assert (empty["类型"], empty["缺什么"]) == ("网页", WORDS) and empty["本地档案"].startswith("pages/")
    assert _judged(empty) == _judged(shown)
    assert export.NO_DATA not in shown["缺什么"] + home["缺什么"]
    assert SECTION in readme and f"| {unit(5)} | {empty['怎么来的']} |" in readme and f"| {unit(6)} |" not in readme


def test_the_readme_names_each_marked_row_with_its_address_and_how_it_came(tmp_path, monkeypatch):
    arch = _archive(tmp_path, _course_tables)
    got = export.export_site(arch.root, SITE, tmp_path / "out")
    rows = by_url(rows_of(got.out / "manifest.csv"))
    readme = (got.out / "README.md").read_text(encoding="utf-8")
    marked = [r for r in rows.values() if export.NO_DATA in r["缺什么"]]
    assert len(marked) == 2  # the press and unit(2)
    section = _section(readme)
    # the words as the cells hold them (the review of 11-09: not the bare NO_DATA they begin with)
    assert section.startswith("这 2 行") and f"「缺什么」栏写着「{WORDS}」" in section
    assert profiles.COURSE_NO_DATA.why in section and "| 网址 | 怎么来的 |" in section
    for r in marked:
        assert f"| {r['网址']} | {r['怎么来的']} |" in section
    assert sum(1 for line in section.splitlines() if line.startswith("| http")) == 2
    # the summary says how many and points to the section, as it does of the empty notice pages (the review of 11-09)
    bullet = next(line for line in readme.split(SECTION, 1)[0].splitlines() if BULLET in line)
    assert bullet.startswith(f"- 有 2 行的网页上{BULLET}") and f"「{WORDS}」" in bullet
    assert "「学校回答没有资料的网页」一节逐行列出" in bullet
    # 缺什么's help quotes the words as the cells hold them
    help_line = next(line for line in readme.splitlines() if line.startswith("- **缺什么**："))
    assert f"「{WORDS}」" in help_line
    # the command (what the export service's log keeps) says how many too
    said = [line for line in got.lines if line.startswith(COMMAND)]
    assert len(said) == 1 and said[0].startswith(f"{COMMAND} 2 行") and f"「{WORDS}」" in said[0]
    # no more rows than the empty notice pages' cap
    monkeypatch.setattr(export, "EMPTY_SHOWN", 1)
    _rows, capped = _exported(arch, tmp_path, name="capped")
    section = _section(capped)
    assert sum(1 for line in section.splitlines() if line.startswith("| http")) == 1
    assert f"…另有 1 行，manifest.csv「缺什么」栏有「{export.NO_DATA}」的就是。" in section


def test_the_words_come_once_after_what_the_page_lacks_besides(tmp_path):
    def broken(arch):  # unit(2) with an attachment that never connected
        _res(arch.f, arch.ids["empty113"], BASE + "uploadfile/broken.pdf", "file", res_id=arch.ids["broken"],
             text="附件二")

    rows, _readme = _exported(_archive(tmp_path, _course_tables, broken), tmp_path)
    lacks = rows[unit(2)]["缺什么"]
    assert lacks.count(export.NO_DATA) == 1 and lacks.endswith("、" + WORDS) and "连接失败（1 个）" in lacks
    assert rows[unit(2)]["完整度"] == outcome.LEVEL_LABEL["partial"]  # the archive's judgement, kept


def test_the_words_come_last_on_a_page_whose_notice_areas_are_empty_too(tmp_path, monkeypatch):
    """The review of 11-09 (a nit on Writer._page): were a page both a notice whose title and text areas are empty and
    the school's answer of no data (no platform's rules make one both today: the course tables have no notice areas),
    the words would still come last, after the empty-area words."""
    def gone(arch):
        arch.page("gone", BASE + "newsview.aspx?nid=4", NOTICE_BLANK, found_via="common_crawl")

    arch = _archive(tmp_path, gone)
    monkeypatch.setattr(profiles, "school_no_data",
                        lambda url, html: profiles.COURSE_NO_DATA if url.endswith("nid=4") else None)
    rows, _readme = _exported(arch, tmp_path, ACA)
    assert rows[BASE + "newsview.aspx?nid=4"]["缺什么"] == export.EMPTY_AREA + "、" + WORDS


def test_every_row_whose_file_is_such_a_page_says_so_once(tmp_path):
    """【Claude 的执行办法】: the mark is what the file says (as the dates a page states are, lists.md §4): a redirect that
    takes the page's file says it (as the empty-area words go with the landing page's 缺什么), and so does a row that
    shares its file (Writer._write gives it none of the first row's 缺什么), each once; the README counts them."""
    arch = _archive(tmp_path, _course_tables, _unit_site, _redirects)
    rows, _readme = _exported(arch, tmp_path)
    assert rows[OLD]["本地档案"] == rows[unit(8)]["本地档案"] != ""
    assert rows[OLD]["缺什么"].count(export.NO_DATA) == 1 and rows[OLD]["缺什么"].endswith(WORDS)
    unit_rows, readme = _exported(arch, tmp_path, WEB, "web")
    sent, table = unit_rows[PLAN], unit_rows[unit(5)]
    assert sent["本地档案"] == table["本地档案"] != "" and "和清单里另一行是同一个档" in sent["备注"] + table["备注"]
    for r in (sent, table):
        assert r["缺什么"].count(export.NO_DATA) == 1 and r["缺什么"].endswith(WORDS), r
        assert r["备注"].count(export.NO_DATA_NOTE) == 1
    assert sent["结果"] == outcome.RESULT_LABEL["redirect"]  # its own result, as before
    section = _section(readme)
    assert section.startswith("这 2 行") and f"| {PLAN} |" in section and f"| {unit(5)} |" in section


def test_a_page_judged_again_after_the_export_read_it_whole_keeps_the_words(tmp_path):
    """Item ⑧: a course table the crawler could not decode (a Big5 comment in a UTF-8 page), read whole by the export
    and judged again as the page it is (Writer._read_whole writes its 缺什么 anew), still says it."""
    rows, _readme = _exported(_archive(tmp_path, _course_tables, _mixed), tmp_path)
    row = rows[unit(9)]
    assert "看板上的结果是「编码有问题」：导出时读通了" in row["备注"] and row["结果"] == "抓到了"
    assert row["缺什么"] == WORDS and row["备注"].count(export.NO_DATA_NOTE) == 1


# ---------------------------------------------------------------- the mark changes nothing else (review of 11-09)


@dataclasses.dataclass
class _Out:
    rows: list[dict[str, str]]
    files: dict[str, bytes]
    missing: bytes
    readme: str
    lines: list[str]


def _export_at(arch, out: Path, site: str) -> _Out:
    """One export at a fixed time (NOW), as the comparison reads it: its rows, its files by path, missing.csv, the
    README and the command's lines (the folder's path in them made one)."""
    got = export.export_site(arch.root, site, out, now=NOW)
    files = {str(p.relative_to(got.out)): p.read_bytes() for d in export.FOLDERS.values()
             for p in sorted((got.out / d).rglob("*")) if p.is_file()}
    return _Out(rows_of(got.out / "manifest.csv"), files, (got.out / "missing.csv").read_bytes(),
                (got.out / "README.md").read_text(encoding="utf-8"),
                [line.replace(str(got.out), "<out>") for line in got.lines])


def _without_note(notes: str) -> str:
    """备注 with NO_DATA_NOTE taken out once (Item.row joins the notes by 「；」)."""
    for cut in ("；" + export.NO_DATA_NOTE, export.NO_DATA_NOTE + "；", export.NO_DATA_NOTE):
        if cut in notes:
            return notes.replace(cut, "", 1)
    return notes


def _without_mark(readme: str) -> list[str]:
    """The README's lines with its section on the marked rows and its summary line on them taken out."""
    head, rest = readme.split("\n\n" + SECTION + "\n", 1)
    return [line for line in (head + "\n\n## " + rest.split("\n\n## ", 1)[1]).splitlines() if BULLET not in line]


def test_the_mark_changes_nothing_but_what_the_rows_lack_and_their_notes(tmp_path, monkeypatch):
    """The review of 11-09 (mutants M5a–d, M20): the same archive exported with the rule and without it (DATAOPEN with
    no no-data rule). Only the rows on the course tables the school answered with no data are marked (no other pop-up,
    not the lost-and-found page), and each row is the same but for 缺什么 (the words added once, last) and 备注 (the
    note added once): its 结果, 分类 and 完整度 as the archive judged them, whichever way it was marked (in _page or
    by its file, Writer._no_data_rows). The files and missing.csv are the same; the README is the same but for its
    section and its summary line on them, the command the same but for its line."""
    arch = _archive(tmp_path, _course_tables, _unit_site, _redirects, _mixed, _other_pop_ups)
    sites = (SITE, WEB)
    on = {s: _export_at(arch, tmp_path / f"on{n}", s) for n, s in enumerate(sites)}
    monkeypatch.setattr(profiles, "PROFILES", tuple(dataclasses.replace(p, no_data=()) if p is profiles.DATAOPEN
                                                    else p for p in profiles.PROFILES))
    off = {s: _export_at(arch, tmp_path / f"off{n}", s) for n, s in enumerate(sites)}
    marked = {s: {r["网址"] for r in on[s].rows if export.NO_DATA in r["缺什么"]} for s in sites}
    assert marked == {SITE: {f"{PAGE}#按钮={TEXTS[0]}", unit(2), unit(5), unit(8), OLD, unit(9)},
                      WEB: {unit(5), PLAN}}
    for s in sites:
        a, b = on[s], off[s]
        assert not [r for r in b.rows if export.NO_DATA in r["缺什么"] or export.NO_DATA_NOTE in r["备注"]]
        assert SECTION not in b.readme and BULLET not in b.readme and not any(x.startswith(COMMAND) for x in b.lines)
        assert [r["网址"] for r in a.rows] == [r["网址"] for r in b.rows]
        for x, y in zip(a.rows, b.rows, strict=True):
            mark = x["网址"] in marked[s]
            assert {k: v for k, v in x.items() if k not in ("缺什么", "备注")} == {
                k: v for k, v in y.items() if k not in ("缺什么", "备注")}, x["网址"]
            assert x["缺什么"] == ("、".join(v for v in (y["缺什么"], WORDS) if v) if mark else y["缺什么"]), x["网址"]
            assert x["备注"].count(export.NO_DATA_NOTE) == mark and _without_note(x["备注"]) == y["备注"], x["网址"]
        assert a.files == b.files and a.missing == b.missing
        assert _without_mark(a.readme) == b.readme.splitlines()
        assert [x for x in a.lines if not x.startswith(COMMAND)] == b.lines and len(a.lines) == len(b.lines) + 1
