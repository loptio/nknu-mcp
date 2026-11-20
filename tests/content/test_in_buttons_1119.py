"""11-19: an outline page of a course several teachers give shows only 「請先選擇教師」 and the teachers' menu; what
each choice brings is a row of its own. The crawler's export writes 「内容在按钮行（…）」 at the start of such a page's
缺什么 (agreed with the crawler session 11-18 night). The content side reads the mark as a substring into
Record.area_mark (corpus.IN_BUTTONS) and leaves the page out, counted under <kind>(内容在按钮行); the departure guard
takes such a leaving as explained (not the school's side). The button rows themselves carry no mark and stay. The
words in the fixtures are made up; no model loads."""
from __future__ import annotations

from dataclasses import replace

from nknu.content import build
from nknu.content import documents as marks
from nknu.content.corpus import GONE, IN_BUTTONS, MOVED, NO_DATA, ROW_MARKS
from nknu.content.documents import IN_BUTTONS_SUFFIX, build_documents, deleted_notice
from tests.content import test_columns_1109 as tc
from tests.content import test_lists_1109 as tl

OUTLINE = "https://sso.nknu.edu.tw/stu/syllabusforenro.aspx?syllabusno=1151000001"
MARKED = "内容在按钮行（这一页只有选单的提示，每个选项按出来的内容各是一行）"
KIND = "page:stu_courses"


def test_the_mark_and_where_it_is_read():
    assert IN_BUTTONS == "内容在按钮行" and IN_BUTTONS_SUFFIX == "(内容在按钮行)"
    assert ROW_MARKS[:4] == (GONE, MOVED, IN_BUTTONS, NO_DATA)  # before the school's answer and the area marks


def test_assemble_reads_the_mark_alone_or_before_other_reasons(tmp_path):
    extra = {1: {"缺什么": MARKED}, 3: {"缺什么": MARKED + "、缺附件（1 个）"}}  # made-up rows
    records, _ = tc.assemble_with(tmp_path, tc.ta.base_rows(), tc.ta.base_results(), extra)
    got = {r["url"]: r["area_mark"] for r in records}
    assert (got[tc.ta.NOTICE], got[tc.ta.ALBUM]) == (IN_BUTTONS, IN_BUTTONS)
    assert {v for u, v in got.items() if u not in (tc.ta.NOTICE, tc.ta.ALBUM)} == {""}


def test_a_page_of_a_menu_only_is_left_out_and_its_button_rows_stay():
    page = tl.rec("p1", KIND, OUTLINE, area_mark=IN_BUTTONS)
    assert build_documents([page], {}, []) == ([], {KIND + IN_BUTTONS_SUFFIX: 1})
    assert not deleted_notice(page)  # never counted with what the school deleted
    row = tl.rec("p2", "detail:stu_courses#按钮", OUTLINE + "#按钮=王OO")  # one teacher's outline: no mark
    docs, left = build_documents([page, row], {}, [])
    assert [d.doc_id for d in docs] == ["dp2"] and left == {KIND + IN_BUTTONS_SUFFIX: 1}
    assert len(build_documents([replace(page, area_mark="")], {}, [])[0]) == 1  # unmarked (an export before): kept


def test_the_guard_takes_such_a_leaving_as_explained():
    assert build._why_left(KIND + marks.IN_BUTTONS_SUFFIX) == ("in_button_rows", True)
    assert "in_button_rows" in build.LEFT_WORDS and "in_button_rows" not in build.SCHOOL_SIDE
