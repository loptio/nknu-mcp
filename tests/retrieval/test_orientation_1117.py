"""Design 2026-11-16-mcp-interface-design §八: what an agent is told before its first call is built from the database's
sites table and unit tree (server.texts_for); a database without them keeps the fixed texts."""

from __future__ import annotations

from fastmcp import Client

from nknu.content import units as unit_tree
from nknu.retrieval import server
from tests.retrieval.test_respond import TODAY, Tiny, prose

UNITS = [  # unit_id, name, kind, parent_id, campus
    ("國立高雄師範大學", "國立高雄師範大學", "学校", None, None),
    ("教務處", "教務處", "行政单位", "國立高雄師範大學", None),
    ("註冊組", "註冊組", "行政组别", "教務處", None),
    ("課務組", "課務組", "行政组别", "教務處", None),
    ("主計室", "主計室", "行政单位", "國立高雄師範大學", None),
    ("教育學院", "教育學院", "学院", "國立高雄師範大學", "和平"),
    ("教育學系", "教育學系", "系所", "教育學院", "和平"),
    ("測試碩士班", "測試碩士班", "系所", "教育學系", "和平"),
    ("特殊教育中心", "特殊教育中心", "中心", "國立高雄師範大學", None),
    ("沒資料中心", "沒資料中心", "中心", "國立高雄師範大學", None),
]


def library(tmp_path, *, units=UNITS, sites=2, event=False):
    t = Tiny(tmp_path / "aca.db")
    t.conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)", units)
    t.conn.executemany("INSERT INTO sites(site, name, unit, data_as_of) VALUES (?, ?, ?, ?)",
                       [(f"site{i}", f"網站{i}", "教務處", "2026-11-16") for i in range(sites)])
    t.doc("d1", prose(300), unit="教務處", grp="註冊組")  # the group is named only in grp
    t.doc("d2", prose(300), unit="測試碩士班", grp="")  # a 碩士班 under its 學系: the college's
    t.doc("d3", prose(300), unit="教育學系", grp="")
    t.doc("d4", prose(300), unit="特殊教育中心", grp="")
    t.doc("d5", prose(300), unit="照網頁原文寫的單位", grp="")  # not in the tree: skipped, no error
    if event:
        t.doc("e1", "", kind_class="event", unit="教務處", grp="")
    t.conn.commit()
    t.conn.close()
    return tmp_path / "aca.db"


def test_the_texts_say_what_is_in_and_which_units(tmp_path):
    texts = server.texts_for(library(tmp_path))
    text = texts.instructions
    assert "整理自高師大 2 個網站的公開資料" in text and "不是學校的官方服務" in text
    assert "沒有收錄：要登入的系統" in text
    assert "教務處（註冊組）" in text  # a group with documents, named through grp
    assert "課務組" not in text and "主計室" not in text and "沒資料中心" not in text  # no documents: not named
    assert "教育學院（和平校區）：教育學系、測試碩士班" in text or "教育學院（和平校區）：測試碩士班、教育學系" in text
    assert "中心：特殊教育中心。" in text
    assert "照網頁原文寫的單位" not in text
    assert "換幾個說法再查" in text and "查無資料" in text
    assert "行事曆" not in text  # no events in this database


def test_the_search_description_counts_the_units(tmp_path):
    search = server.texts_for(library(tmp_path)).search
    assert search.startswith("搜尋高師大 2 個網站已收錄的公開網頁、公告、附件，")
    assert "收錄的單位有：教務處等行政單位，教育學院這 1 個學院的 2 個系所，以及 1 個中心；" in search
    assert server.SEARCH_HOW in search and search.endswith(server.SEARCH_FRESH)


def test_the_calendar_is_named_only_when_the_database_has_events(tmp_path):
    texts = server.texts_for(library(tmp_path, event=True))
    assert "與行事曆（2023 年起）" in texts.instructions and "與行事曆（2023 年起）" in texts.search


def test_a_database_without_sites_or_units_keeps_the_fixed_texts(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert server.texts_for(library(tmp_path / "a", units=[])) == server.FIXED
    assert server.texts_for(library(tmp_path / "b", sites=0)) == server.FIXED
    assert server.texts_for(tmp_path / "missing.db") == server.FIXED


async def test_the_handshake_and_the_tool_carry_the_built_texts(tmp_path):
    db = library(tmp_path)
    texts = server.texts_for(db)
    async with Client(server.create_mcp(db, today_fn=lambda: TODAY)) as c:
        assert c.instructions == texts.instructions
        tools = {t.name: t for t in await c.list_tools()}
    assert tools["search"].description == texts.search
    assert tools["fetch"].description == server.FETCH_DESCRIPTION


def test_the_kinds_are_the_unit_trees():
    assert {server.ADMIN, server.GROUP, server.COLLEGE, server.CENTRE} | server.DEPT_KINDS <= set(unit_tree.KINDS)
