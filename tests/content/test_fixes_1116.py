"""The three small fixes batched for the rebuild after 11-16 (blueprint 第五节 item 25: another session's audit of the
dates, titles and units; the owner 11-16, 「听你的」 to the grouping that batches these with the next rebuild instead
of a rebuild of their own):

1. a title a document lost (plan 2026-11-14 T4: a site's name on every page, a file name without a Han character) is
   no longer kept among its link names, which the index weighs as a title; old_title keeps it, and the term, the
   cohort and the doc type it alone named are still read from there;
2. a file whose parent took a new title names the parent by that title;
3. a 組別 that names no unit by itself is read as its unit's own section (進修學院's 「教務組」 is 進修學院教務組 in the
   tree; the bare word means 教務處's two campus sections everywhere else).

The sentences and the units are made up; no model loads."""

from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from nknu.content import build
from nknu.content.build import publisher_of, unit_scope, unit_words
from nknu.content.documents import build_documents, own_titles
from nknu.content.units import Unit, resolve
from nknu.retrieval.search import search_docs
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_own_titles_1114 as tt

CHAIR, CHAIR_SITE, SOFT, SCHOOL = tt.CHAIR, tt.CHAIR_SITE, tt.SOFT, tt.SCHOOL
MAP3 = "114學年度第2學期課程地圖第3版"  # the title page 3 of chair_rows takes from its own text


# ---------------------------------------------------------------- 1. the old title and the link names

def _docs():
    pages = [tt.page(n, SOFT, f"課程地圖第{n}版\n本頁說明第 {n} 版的修課順序。") for n in range(1, 21)]
    docs, _ = build_documents([*pages, tt.file("a1", "643-79-8.odt", "## 學生請假辦法\n第一條 本辦法依學則訂定。")], {}, [])
    return docs


def test_a_replaced_title_is_kept_in_old_title_and_not_among_the_link_names():
    docs = _docs()
    report = own_titles(docs, SCHOOL)
    got = {d.doc_id: (d.title, d.linktext, d.old_title) for d in docs}
    assert got["dp1"] == ("課程地圖第1版", "", SOFT) and got["da1"] == ("學生請假辦法", "", "643-79-8.odt")
    assert report["shared"]["retitled"] == 20 and report["file"]["retitled"] == 1


# ---------------------------------------------------------------- the build

def rows() -> list[dict]:
    """test_own_titles_1114's site (20 pages titled by the site's name, four files on a notice) and one more file, on
    page 3: its parent's title is the site's name until the page takes its own."""
    return [*tt.chair_rows(), {"kind": tc.PDF, "url": f"{CHAIR}f/修課順序表.pdf", "title": "修課順序表.pdf",
                               "text": "修課順序表\n大一上學期必修科目。", "linked": CHAIR + "3"}]


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "db" / "on.db"
    report = tm.run(tm.tree_files(tmp_path / "tree-on"), out, inputs=[tt.site(tmp_path, rows(), name="on")])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn
    conn.close()


def _by_url(conn, columns: str) -> dict:
    return {r[0]: r[1:] for r in conn.execute(f"SELECT url, {columns} FROM docs")}


def test_the_build_drops_the_old_titles_from_the_link_names_and_says_so(built):
    report, conn = built
    got = _by_url(conn, "title, linktext, period_ay, period_sem")
    assert got[CHAIR + "1"] == ("114學年度第2學期課程地圖第1版", "", 114, 2)
    assert got[CHAIR + "f/643-79-8.odt"][:2] == ("113學年度第1次系務會議紀錄", "")
    # the term only the old file name said is still the document's: it is read from old_title, not from the link names
    assert got[CHAIR + "f/115-1a.pdf"] == ("選課時程表", "", 115, 1)
    assert report.titles["old_kept"] is False
    assert conn.execute("SELECT value FROM meta WHERE key = 'fixes_1116'").fetchone() == ("on",)
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key = 'titles_json'").fetchone()[0]) == report.titles


def test_the_new_title_and_the_units_name_still_find_the_document(built):
    _, conn = built
    file_id = conn.execute("SELECT doc_id FROM docs WHERE url = ?", (CHAIR + "f/643-79-8.odt",)).fetchone()[0]

    def found(query: str) -> bool:
        return file_id in [h.doc_id for h in search_docs(conn, query, today=tt.TODAY, aliases=[]).hits]

    assert found("系務會議紀錄") and not found("643-79-8.odt")  # a name of digits finds it no more
    page_id = conn.execute("SELECT doc_id FROM docs WHERE url = ?", (CHAIR + "7",)).fetchone()[0]
    tokens = "SELECT f.title_tokens, f.unit_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid WHERE c.doc_id = ? LIMIT 1"
    title, unit = (set(part.split()) for part in conn.execute(tokens, (page_id,)).fetchone())
    assert {"课程", "地图", "term114s2"} <= title and "软体" not in title
    assert "软体" in unit  # the site's name is the page's unit: 「軟體系 課程地圖」 finds the page by its unit words


# ---------------------------------------------------------------- 2. the parent's new title

def test_a_file_names_its_parent_by_the_title_the_parent_took(built):
    report, conn = built
    url, sql = f"{CHAIR}f/修課順序表.pdf", "SELECT parent_title, parent_id FROM docs WHERE url = ?"
    page_id = conn.execute("SELECT doc_id FROM docs WHERE url = ?", (CHAIR + "3",)).fetchone()[0]
    assert conn.execute(sql, (url,)).fetchone() == (MAP3, page_id)  # not the site's name, the page's old title
    # a file on a notice that kept its title is left alone
    assert conn.execute(sql, (CHAIR + "f/停修申請表.pdf",)).fetchone()[0] == "會議資料公告"
    assert report.titles["parent_titles"] == 1


def test_a_parent_title_that_was_not_the_parents_old_one_is_left():
    docs, _ = build_documents([tt.page(n, SOFT, f"課程地圖第{n}版\n本頁說明第 {n} 版的修課順序。") for n in range(1, 21)]
                              + [tt.file("a1", "修課順序表.pdf", "修課順序表\n必修科目。")], {}, [])
    by_id = {d.doc_id: d for d in docs}
    by_id["da1"].parent_id, by_id["da1"].parent_title = "dp3", "另一則公告的標題"  # a merged page's, say
    report = build._retitle(build._rows(docs, {}, {}))
    assert by_id["dp3"].title == "課程地圖第3版" and by_id["da1"].parent_title == "另一則公告的標題"
    assert report["parent_titles"] == 0


# ---------------------------------------------------------------- 3. a unit's own section

def _unit(name: str, kind: str, parent: str | None, unit_id: str | None = None) -> Unit:
    return Unit(unit_id or name, name, kind, parent, None, ())


EVENING = "進修學院"
TREE = {u.unit_id: u for u in (
    _unit(SCHOOL, "学校", None), _unit("教務處", "行政单位", SCHOOL), _unit("和平教務組", "行政组别", "教務處"),
    _unit(EVENING, "行政单位", SCHOOL), _unit(EVENING + "教務組", "行政组别", EVENING),  # the tree's name for its 教務組
    _unit("綜合服務組", "行政组别", EVENING))}


def test_a_bare_section_name_is_the_units_own_section():
    assert resolve("教務組", TREE) is None  # by itself it names no unit (units.yaml: on purpose)
    assert publisher_of(EVENING, "教務組", TREE) == EVENING + "教務組"
    assert publisher_of(EVENING, " 教務組 ", TREE) == EVENING + "教務組"
    assert publisher_of(EVENING, "綜合服務組", TREE) == "綜合服務組"  # a section the tree names plainly: as before


def test_only_the_units_own_section_is_read_so():
    assert publisher_of("教務處", "教務組", TREE) == "教務處"  # 教務處 has no section of that name
    assert publisher_of(EVENING, "和平教務組", TREE) == EVENING  # another office's section stays unplaced
    assert publisher_of(EVENING, "校史室", TREE) == EVENING and publisher_of(EVENING, "", TREE) == EVENING
    assert publisher_of("沒有的單位", "教務組", TREE) is None


def _notice(grp: str, unit: str = EVENING):
    (doc,), _ = build_documents([tc.rec("p1", "detail:cnknu", "https://c.nknu.edu.tw/ccee/newsdetail.aspx?nid=1&gi=2",
                                        "夜間班選課公告", "選課自九月一日起。", "c.nknu.edu.tw/ccee", "2026-09-01")], {}, [])
    return dataclasses.replace(doc, unit=unit, grp=grp)


def test_the_scope_names_the_section_as_the_publisher_and_counts_it_placed():
    doc = _notice("教務組")
    scope = unit_scope([doc], TREE)
    assert [(rel, unit_id) for _, rel, unit_id, *_ in scope.rows] == [("applies_to", SCHOOL), ("publisher", EVENING + "教務組")]
    assert scope.report["grp_unplaced"] == {} and scope.report["publishers"] == {EVENING + "教務組": 1}
    # what is unplaced for another reason still is, and a section named plainly is not counted
    assert unit_scope([_notice("和平教務組")], TREE).report["grp_unplaced"] == {"和平教務組": 1}
    assert unit_scope([_notice("綜合服務組")], TREE).report["grp_unplaced"] == {}


def test_the_unit_line_says_the_unit_and_the_group_as_written():
    # the section the 組別 names adds no word to the line: 「進修學院」 and 「教務組」 say it already
    words = unit_words(_notice("教務組"), TREE, SCHOOL)
    assert (words.name, words.group, words.group_aliases) == (EVENING, "教務組", ())
