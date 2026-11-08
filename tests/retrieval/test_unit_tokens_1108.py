"""Owner 11-08 10:16 (c), N11 (「致理大樓 教務組」 1 -> 42 once the unit words were title tokens): the unit words are a
column of their own (chunk_fts.unit_tokens, pipeline-5) with their own bm25 weight (RankConfig.unit_weight), and a
query word only they have counts unit_cover in coverage, so the page that says 教務組 itself leads the unit's pages
that only mention 致理大樓, while 「某系 + 某事」 still puts the department's pages above the others. A database
before pipeline-5 searches as it did. No model loads; the names are made up."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from nknu.content.schema import create_schema
from nknu.core.textnorm import tokenize
from nknu.retrieval.search import has_unit_tokens
from tests.retrieval.test_search import BASE, Tiny, _hit, _ids

UNIT = " ".join(tokenize("軟體工程與管理學系\n軟體系\n軟工系"))  # 軟體系's unit words, a word at a time


def unit_doc(db: Tiny, doc_id: str, title: str, text: str) -> None:
    for chunk in db.doc(doc_id, title, text):
        db.conn.execute("UPDATE chunk_fts SET unit_tokens = ? WHERE rowid = ?", (UNIT, chunk))


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "t.db")
    unit_doc(t, "bus", "交通資訊", "致理大樓 搭乘公車")  # 軟體系's page that only mentions 致理大樓
    unit_doc(t, "office", "系辦公室", "軟體系系辦公室位於致理大樓三樓")  # the answer: says 軟體系 itself
    t.doc("other", "交通資訊", "致理大樓 搭乘公車")  # another unit's page
    yield t
    t.conn.close()


def test_a_word_only_the_unit_words_have_counts_half(db):
    result = db.search("致理大樓 軟體系")
    assert _ids(result) == ["office", "bus", "other"]
    assert [round(h.coverage, 2) for h in result.hits] == [1.0, 0.8, 0.6]  # 0.8: 致理 理大 大楼 + half 软体 体系


def test_counting_them_fully_ties_the_answer(db):
    full = db.search("致理大樓 軟體系", rank=dataclasses.replace(BASE, unit_cover=1.0, unit_weight=3.0))
    assert [round(_hit(full, d).coverage, 2) for d in ("office", "bus")] == [1.0, 1.0]  # what N11 met


def test_the_departments_page_still_leads_by_its_unit_words(db):
    result = db.search("軟體系 公車")  # 公车 in bus's text and other's; 軟體系 only in bus's unit words
    assert _ids(result).index("bus") < _ids(result).index("other")
    assert [round(_hit(result, d).coverage, 2) for d in ("bus", "other")] == [0.67, 0.33]


def test_a_database_before_pipeline_5_searches_as_before(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    create_schema(conn)
    conn.execute("DROP TABLE chunk_fts")
    conn.execute("CREATE VIRTUAL TABLE chunk_fts USING fts5(title_tokens, body_tokens,"
                 " tokenize='unicode61 remove_diacritics 0')")
    conn.commit()
    conn.close()
    t = Tiny.__new__(Tiny)
    t.conn = sqlite3.connect(tmp_path / "old.db")
    t.doc("a", "交通資訊", "致理大樓 搭乘公車")
    assert not has_unit_tokens(t.conn) and _ids(t.search("致理大樓")) == ["a"]
    assert has_unit_tokens(Tiny(tmp_path / "new.db").conn)
