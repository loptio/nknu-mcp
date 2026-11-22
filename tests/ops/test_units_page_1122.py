"""單位 (nknu.ops.units_page): each unit's own documents, its units' added on the line above, the sites they came from,
the newest date, and the units with no document or none in a year marked. Offline: a small made-up library."""

import sqlite3
from datetime import date

from nknu.ops import units_page

TODAY = date(2026, 11, 22)


def library(with_units: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE docs (doc_id TEXT PRIMARY KEY, site TEXT);
        CREATE TABLE doc_scope (doc_id TEXT, relation TEXT, unit_id TEXT, campus TEXT, level TEXT, cohort_from INTEGER);
        CREATE TABLE dates (doc_id TEXT, kind TEXT, date TEXT, raw TEXT, source TEXT);
    """)
    if not with_units:
        return conn
    conn.execute("CREATE TABLE units (unit_id TEXT PRIMARY KEY, name TEXT, kind TEXT, parent_id TEXT, campus TEXT)")
    conn.executemany("INSERT INTO units VALUES (?, ?, ?, ?, ?)", [
        ("國立高雄師範大學", "國立高雄師範大學", "学校", None, None),
        ("教務處", "教務處", "行政单位", "國立高雄師範大學", None),
        ("主計室", "主計室", "行政单位", "國立高雄師範大學", None),
        ("理學院", "理學院", "学院", "國立高雄師範大學", "燕巢"),
        ("化學系", "化學系", "系所", "理學院", "燕巢"),
        ("物理學系", "物理學系", "系所", "理學院", "燕巢"),
    ])
    docs = [("d1", "c.nknu.edu.tw/aca", "教務處", "2026-11-01"), ("d2", "c.nknu.edu.tw/aca", "教務處", "2026-10-01"),
            ("d3", "sso.nknu.edu.tw/news", "教務處", None), ("d4", "chem.nknu.edu.tw", "化學系", "2025-03-01"),
            ("d5", "phys.nknu.edu.tw", "物理學系", "2026-12-30")]  # a date after today is not the newest
    for doc_id, site, unit, day in docs:
        conn.execute("INSERT INTO docs VALUES (?, ?)", (doc_id, site))
        conn.execute("INSERT INTO doc_scope VALUES (?, 'publisher', ?, NULL, NULL, NULL)", (doc_id, unit))
        conn.execute("INSERT INTO doc_scope VALUES (?, 'applies_to', '國立高雄師範大學', NULL, NULL, NULL)", (doc_id,))
        if day:
            conn.execute("INSERT INTO dates VALUES (?, 'published', ?, ?, '公告頁日期欄')", (doc_id, day, day))
    return conn


def by_name(tree):
    return {u.unit_id: u for u in tree.units}


def test_a_units_count_is_what_it_published_and_a_parent_adds_its_units():
    tree = units_page.load(library(), TODAY)
    u = by_name(tree)
    assert u["教務處"].docs == 3 and u["教務處"].sites == {"c.nknu.edu.tw/aca": 2, "sso.nknu.edu.tw/news": 1}
    assert u["理學院"].docs == 0 and u["理學院"].total == 2
    assert u["國立高雄師範大學"].total == 5  # applies_to rows are not publishing


def test_the_newest_date_is_published_or_inherited_and_not_after_today():
    u = by_name(units_page.load(library(), TODAY))
    assert u["教務處"].newest == "2026-11-01"
    assert u["物理學系"].newest is None  # its only date is after today
    assert u["理學院"].total_newest == "2025-03-01"


def test_units_with_no_document_and_units_a_year_old_are_found():
    tree = units_page.load(library(), TODAY)
    assert [x.unit_id for x in tree.empty()] == ["主計室"]
    assert [x.unit_id for x in tree.stale()] == ["化學系"]


def test_siblings_go_offices_first_then_colleges():
    tree = units_page.load(library(), TODAY)
    assert [c.unit_id for c in tree.root.children] == ["教務處", "主計室", "理學院"]
    assert [x.unit_id for x in tree.units][:2] == ["國立高雄師範大學", "教務處"]


def test_the_page_marks_what_is_missing_and_links_the_sites():
    html = units_page.body(library(), TODAY)
    assert "主計室" in html and "沒有文件" in html
    assert "/site?site=c.nknu.edu.tw/aca" in html
    assert "行政單位" in html  # the kinds the build writes in Simplified are shown in Traditional
    assert "<b>1</b>沒有文件" in html and "<b>1</b>一年沒更新" in html


def test_a_library_without_the_unit_tree_says_so():
    assert "沒有單位表" in units_page.body(library(with_units=False), TODAY)
