"""search(unit=...) (design 2026-11-16-mcp-interface-design §二 `unit`): a unit's name becomes a condition on the
documents' unit and grp that both candidate paths apply before their limits (nknu.retrieval.unitfilter)."""

from __future__ import annotations

import pytest

from nknu.retrieval import search
from nknu.retrieval.hybrid import HybridConfig, scope_units
from nknu.retrieval.unitfilter import allowed, nearest, unit_filter
from tests.retrieval import test_hybrid
from tests.retrieval.test_hybrid import FakeEmbedder
from tests.retrieval.test_search import TODAY

UNITS = (  # made up (no research data); kinds as nknu.content.units.KINDS writes them
    ("國立高雄師範大學", "学校", None, None, ("高師大",)),
    ("教務處", "行政单位", "國立高雄師範大學", None, ()),
    ("註冊組", "行政组别", "教務處", None, ()),
    ("教育學院", "学院", "國立高雄師範大學", "和平", ()),
    ("教育學系", "系所", "教育學院", "和平", ("教育系",)),
    ("特殊教育學系", "系所", "教育學院", "和平", ("特教系",)),
    ("特殊教育中心", "中心", "國立高雄師範大學", None, ()),
)
DOCS = {  # doc_id: (unit, grp)
    "reg": ("教務處", "註冊組"),
    "course": ("教務處", "課務組"),
    "edu": ("教育學系", ""),
    "sped": ("特殊教育學系", ""),
    "college": ("教育學院", ""),
    "centre": ("特殊教育中心", ""),
    "loose": ("照網頁原文寫的單位", ""),
}


@pytest.fixture
def db(tmp_path):
    t = test_hybrid.Tiny(tmp_path / "t.db")
    t.units(UNITS)
    for doc_id, (unit, grp) in DOCS.items():
        t.doc(doc_id, f"{doc_id} 停修公告", "停修申請期間公告")
        t.conn.execute("UPDATE docs SET unit = ?, grp = ? WHERE doc_id = ?", (unit, grp, doc_id))
    yield t
    t.conn.close()


def kept(db, name: str) -> set[str] | None:
    f, _ = unit_filter(scope_units(db.conn), name)
    return None if f is None else allowed(db.conn, f)


def test_a_department_keeps_its_own_its_college_and_the_school_wide(db):
    assert kept(db, "教育學系") == {"reg", "course", "edu", "college", "centre", "loose"}  # not 特殊教育學系


def test_an_office_keeps_itself_and_its_groups(db):
    assert kept(db, "教務處") == {"reg", "course"}


def test_a_group_keeps_its_own_and_its_offices_that_name_it(db):
    assert kept(db, "註冊組") == {"reg"}


def test_a_centre_keeps_its_own(db):
    assert kept(db, "特殊教育中心") == {"centre"}


def test_the_name_is_read_in_either_script_and_by_alias(db):
    assert kept(db, "教务处") == {"reg", "course"} and kept(db, "特教系") == kept(db, "特殊教育學系")


def test_the_school_and_an_unknown_name_filter_nothing(db):
    units = scope_units(db.conn)
    f, said = unit_filter(units, "高師大")
    assert f is None and said == {"input": "高師大", "applied": True, "unit": "國立高雄師範大學"}
    f, said = unit_filter(units, "教育研究所")
    assert f is None and said["applied"] is False and said["near"][0] in ("教育學系", "教育學院")
    assert nearest("火星", units) == []


def test_the_keyword_pool_fills_from_the_unit_before_its_limit(db, monkeypatch):
    monkeypatch.setattr(search, "POOL_DOCS", 1)
    f, _ = unit_filter(scope_units(db.conn), "特殊教育中心")
    pool = search.keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=test_hybrid.BASE, only=f)
    assert list(pool.docs) == ["centre"]  # the one place goes to the unit's document


def test_the_vector_path_keeps_only_the_unit(db):
    db.vectors(FakeEmbedder())
    f, _ = unit_filter(scope_units(db.conn), "教務處")
    r = db.hybrid("退選", embedder=FakeEmbedder(), reranker=None, config=HybridConfig(rerank=False), only=f)
    assert r.hits and {h.doc_id for h in r.hits} <= {"reg", "course"}
