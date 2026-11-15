"""spec 2026-11-16 §四 (plan 2026-11-16 T2; the owner 11-16: 「可以，先写程序」 on the design): a reranked background
document no longer waits at places 9 and 10 whatever it scored. Its score times HybridConfig.background_factor is
compared with the primary documents' scores, and at most BACKGROUND_SLOTS background documents stand among the first
TOP_N. Measured on the official library of 11-15: asked for by its own title, a background document scored above the
first primary one in 91 of 100 cases and still came ninth. Without a reranked score (keyword only) the fixed places of
addendum M2 stay: keyword scores of the two tiers were never meant to be compared."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig
from nknu.retrieval.respond import NOTE_BACKGROUND
from nknu.retrieval.search import BACKGROUND_SLOTS, TOP_N, RankConfig, mixed
from tests.retrieval.test_hybrid import FakeReranker, Tiny
from tests.retrieval.test_tier_1108 import member

TIER = RankConfig(recency=False)


def _doc(name: str, score: float, background: bool = False) -> SimpleNamespace:
    return SimpleNamespace(doc_id=name, score=score, background=background)


def _names(docs) -> list[str]:
    return [d.doc_id for d in docs]


def test_a_background_document_stands_where_its_discounted_score_puts_it():
    main = [_doc("m1", 3.0), _doc("m2", 2.0), _doc("m3", 1.0)]
    back = [_doc("b1", 3.5, True), _doc("b2", 1.2, True)]
    assert _names(mixed(main, back, 0.8)) == ["m1", "b1", "m2", "m3", "b2"]  # 3.5 x 0.8 = 2.8, 1.2 x 0.8 = 0.96
    assert [d.score for d in back] == [3.5, 1.2]  # the order moves, a document's own score never does
    assert _names(mixed(main, back, 1.0)) == ["b1", "m1", "m2", "b2", "m3"]


def test_on_equal_scores_the_primary_document_comes_first():
    assert _names(mixed([_doc("m1", 2.0)], [_doc("b1", 2.5, True)], 0.8)) == ["m1", "b1"]


def test_no_more_than_the_two_places_among_the_first_ten():
    main = [_doc(f"m{i}", 1.0 - i / 100) for i in range(10)]
    back = [_doc("b0", 5.0, True), _doc("b1", 4.0, True), _doc("b2", 3.0, True)]
    got = _names(mixed(main, back, 0.8))
    assert (BACKGROUND_SLOTS, TOP_N) == (2, 10)
    assert got[:TOP_N] == ["b0", "b1"] + [f"m{i}" for i in range(8)]
    assert got[TOP_N:] == ["b2", "m8", "m9"]  # the third waits behind the ten, ahead of what scored less


def test_one_tier_alone_keeps_its_order():
    main = [_doc("m1", 3.0), _doc("m2", 2.0)]
    assert _names(mixed(main, [], 0.8)) == ["m1", "m2"]
    back = [_doc(f"b{i}", 3.0 - i, True) for i in range(3)]
    assert _names(mixed([], back, 0.8)) == ["b0", "b1", "b2"]  # nothing else to show: all of them, as before


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _nine_and_two(db):
    for i in range(9):
        db.doc(f"p{i}", f"停修公告{i}", "受理停修申請")
    member(db, "da1m1", "停修考古題", "停修 停修 停修")
    member(db, "da2m1", "停修會議紀錄", "停修 停修 停修")


def _ids(result) -> list[str]:
    return [h.doc_id for h in result.hits]


RR = {"停修考古題": 1.0, "停修公告5": 0.7, "停修公告3": 0.5}  # the reranker's scores; every other passage 0


def test_a_background_document_the_reranker_puts_first_is_first(db):
    _nine_and_two(db)
    r = db.hybrid("停修", limit=20, reranker=FakeReranker(RR), config=HybridConfig(dense=False), rank=TIER)
    assert HybridConfig().background_factor == 0.8
    # 3 x 1.0 x 0.8 = 2.4 against 3 x 0.7 = 2.1 and 3 x 0.5; the other background document, at 0, behind the primary
    assert _ids(r) == ["da1m1", "p5", "p3", "p0", "p1", "p2", "p4", "p6", "p7", "p8", "da2m1"]
    assert r.hits[0].background and r.hits[0].score == pytest.approx(3.0)  # its own score, not the discounted one


def test_factor_zero_is_the_fixed_places_of_before(db):
    _nine_and_two(db)
    off = HybridConfig(dense=False, background_factor=0.0)
    r = db.hybrid("停修", limit=20, reranker=FakeReranker(RR), config=off, rank=TIER)
    assert _ids(r) == ["p5", "p3", "p0", "p1", "p2", "p4", "p6", "p7", "da1m1", "da2m1", "p8"]


def test_keyword_only_keeps_the_fixed_places(db):
    _nine_and_two(db)
    off = HybridConfig(dense=False, rerank=False)
    assert _ids(db.hybrid("停修", rank=TIER, limit=20, config=off)) == _ids(db.search("停修", rank=TIER, limit=20))
    assert _ids(db.search("停修", rank=TIER, limit=20))[8:10] == ["da1m1", "da2m1"]


def test_the_factor_is_a_discount():
    for bad in (1.5, -0.1, 80):
        with pytest.raises(ValueError, match="background_factor"):
            HybridConfig(background_factor=bad)


def test_the_note_says_what_background_is_and_no_longer_where_it_stands():
    assert NOTE_BACKGROUND == "【系統】background 為 true 的是背景資料（會議紀錄、壓縮檔裡的文件、學報、舊校刊和校訊、歷屆考題）。"
