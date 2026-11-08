"""Owner 11-08 10:16 (a): a unit's short name in the query brings the unit's name and its other short names
(search.unit_names, aliases.expand's names), so 「事經系 實習」 finds what 事業經營學系 wrote; the name brings nothing
(one way, 【Claude】), a short name inside another unit's name is not one, and the school brings none. No model
loads; the names are made up."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from nknu.retrieval.aliases import expand
from nknu.retrieval.search import unit_names
from tests.retrieval.test_search import BASE, WITHDRAW, Tiny, _hit, _ids

ROOT = "國立高雄師範大學"
UNITS = [  # unit_id (= name), kind, parent, short names
    (ROOT, "学校", None, ["高師大"]),
    ("學生事務處", "行政单位", ROOT, ["學務處"]),
    ("生活輔導組", "行政组别", "學生事務處", ["生輔組"]),
    ("事業經營學系", "系所", ROOT, ["事經系"]),
    ("學生輔導中心", "中心", ROOT, ["輔導"]),  # a made-up short name that is inside 生活輔導組
    ("教務處", "行政单位", ROOT, []),
]
NAMES = [["學生事務處", "學務處"], ["生活輔導組", "生輔組"], ["事業經營學系", "事經系"], ["學生輔導中心", "輔導"]]


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "t.db")
    for name, kind, parent, short in UNITS:
        t.conn.execute("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, NULL)",
                       (name, name, kind, parent))
        t.conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)",
                           [(n, name) for n in [name, *short]])  # the build's order: the name first
    yield t
    t.conn.close()


def test_unit_names_reads_the_tree_without_the_school(db):
    assert unit_names(db.conn) == NAMES  # the school (高師大) and a unit with no short name (教務處) give none
    assert unit_names(sqlite3.connect(":memory:")) == []  # no unit tables: an older or a hand-made database


def test_a_short_name_brings_the_name():
    assert expand("事經系 實習", [], NAMES) == (["事业", "业经", "经营", "营学", "学系"], ["事經系"])


def test_the_name_brings_nothing():
    assert expand("事業經營學系 實習", [], NAMES) == ([], [])


def test_a_short_name_inside_another_units_name_is_not_one():
    assert expand("生活輔導組 宿舍", [], NAMES) == ([], [])  # not 學生輔導中心's 輔導
    assert expand("輔導 宿舍", [], NAMES)[1] == ["輔導"]


def test_a_units_name_does_not_block_an_alias_of_the_table():
    # review of 11-08: 成人教育研究所 brings nothing, and 研究所甄試 inside the query still brings the table's members
    groups = [["研究所甄試", "碩士班甄試"]]
    assert expand("成人教育研究所甄試 日期", groups, [["成人教育研究所", "成教所"]]) == (["硕士", "士班", "班甄"],
                                                                                   ["研究所甄試"])


def test_alias_groups_and_names_together():
    assert expand("退選 生輔組", [WITHDRAW], NAMES) == (["停修", "弃选", "生活", "活辅", "辅导", "导组"],
                                                       ["退選", "生輔組"])


def test_search_finds_the_name_by_its_short_name(db):
    db.doc("d1", "實習辦法", "事業經營學系 學生校外實習辦法")
    db.doc("d2", "實習公告", "暑期實習說明會")
    result = db.search("事經系 實習")
    assert _ids(result)[0] == "d1" and _hit(result, "d1").coverage == 1.0
    assert result.understood.aliases == ["事經系"]
    off = db.search("事經系 實習", rank=dataclasses.replace(BASE, unit_names=False))
    assert _hit(off, "d1").coverage == pytest.approx(1 / 3) and off.understood.aliases == []


def test_the_school_brings_nothing(db):
    db.doc("d1", "停車", "國立高雄師範大學 停車證申請")
    assert db.search("高師大 停車").understood.aliases == []
