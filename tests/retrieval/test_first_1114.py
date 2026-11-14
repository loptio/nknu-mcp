"""Plan 2026-11-14 T8 (direction 2026-11-13 第 8 件, a trial): with HybridConfig.w_first, a reranked document's score
also holds its place in the first stage's fused (RRF) order: w_first for the first of its tier's reranked documents,
down to 0 for the last. Off (0.0) by default: the score is then the one of before. Fake models only; the sentences
are invented."""

from __future__ import annotations

import math
from datetime import date

import pytest

from nknu.content.schema import BACKGROUND
from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig, first_share
from nknu.retrieval.search import W_COHORT, W_PERIOD, RankConfig
from tests.retrieval.test_hybrid import FakeEmbedder, FakeReranker, Tiny
from tests.retrieval.test_search import TODAY, _hit, _ids

W = 0.3  # the plan's number for the trial 【Claude】; the default is 0.0
RERANK = HybridConfig(dense=False)
FIRST = HybridConfig(dense=False, w_first=W)
TIER = RankConfig(recency=False)  # the tier on, the date part off


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _scores(result):
    return {h.doc_id: h.score for h in result.hits}


def _three(db: Tiny) -> None:
    """Three notices the keywords rank d1, d2, d3 (停修 three times, twice, once); 甲, 乙, 丙 tell the reranker which."""
    db.doc("d1", "公告", "甲 停修 停修 停修")
    db.doc("d2", "公告", "乙 停修 停修")
    db.doc("d3", "公告", "丙 停修")
    assert _ids(db.search("停修")) == ["d1", "d2", "d3"]


def test_off_by_default():
    assert HybridConfig().w_first == 0.0


def test_first_share():
    assert first_share([["a", "b", "c"], ["x", "y"]]) == {"a": 1.0, "b": 0.5, "c": 0.0, "x": 1.0, "y": 0.0}
    assert first_share([["a"], []]) == {"a": 1.0}  # a head of one: the whole of it
    assert first_share([[], []]) == {}
    five = first_share([list("abcde")])
    assert [five[d] for d in "abcde"] == [1.0, 0.75, 0.5, 0.25, 0.0]


def test_the_first_stages_place_is_added(db):
    _three(db)
    same = {"停修": 0.5}
    assert _scores(db.hybrid("停修", reranker=FakeReranker(same), config=RERANK)) == {"d1": 1.5, "d2": 1.5, "d3": 1.5}
    r = db.hybrid("停修", reranker=FakeReranker(same), config=FIRST)
    assert _ids(r) == ["d1", "d2", "d3"] and r.best_rerank == 0.5  # the rerank score itself is not touched
    assert _scores(r) == pytest.approx({"d1": 1.5 + W, "d2": 1.5 + W / 2, "d3": 1.5})


def test_a_small_rerank_lead_gives_way_and_a_clear_one_holds(db):
    # w_first 0.3 is a rerank lead of 0.1 (w_rerank 3.0): d3 is last in the first stage and first for the reranker
    _three(db)
    close = {"甲": 0.50, "乙": 0.20, "丙": 0.55}
    assert _ids(db.hybrid("停修", reranker=FakeReranker(close), config=RERANK)) == ["d3", "d1", "d2"]
    assert _ids(db.hybrid("停修", reranker=FakeReranker(close), config=FIRST)) == ["d1", "d3", "d2"]
    clear = {"甲": 0.50, "乙": 0.20, "丙": 0.65}
    r = db.hybrid("停修", reranker=FakeReranker(clear), config=FIRST)
    assert _ids(r) == ["d3", "d1", "d2"] and _hit(r, "d3").score == pytest.approx(3 * 0.65)


def test_one_reranked_document_gets_all_of_it(db):
    db.doc("d1", "公告", "停修")
    r = db.hybrid("停修", reranker=FakeReranker({"停修": 0.5}), config=FIRST)
    assert _scores(r) == pytest.approx({"d1": 1.5 + W})


def test_only_the_reranked_head_counts(db):
    # rerank_docs 2: d3 is not reranked, keeps its keyword score and is no part of n
    _three(db)
    keyword = _scores(db.search("停修"))
    config = HybridConfig(dense=False, w_first=W, rerank_docs=2)
    r = db.hybrid("停修", reranker=FakeReranker({"停修": 0.5}), config=config)
    assert _scores(r) == pytest.approx({"d1": 1.5 + W, "d2": 1.5, "d3": keyword["d3"]})
    assert _ids(r) == ["d1", "d2", "d3"]


def test_each_tier_has_its_own_places(db):
    for i, n in enumerate((3, 2), start=1):
        db.doc(f"p{i}", "公告", "停修 " * n)
    for i, n in enumerate((4, 3, 2), start=1):
        db.doc(f"b{i}", "會議紀錄", "停修 " * n, tier=BACKGROUND)
    r = db.hybrid("停修", rank=TIER, reranker=FakeReranker({"停修": 0.5}), config=FIRST)
    assert _scores(r) == pytest.approx({"p1": 1.5 + W, "p2": 1.5, "b1": 1.5 + W, "b2": 1.5 + W / 2, "b3": 1.5})
    assert _ids(r) == ["p1", "p2", "b1", "b2", "b3"]


def test_the_place_is_the_fused_one_and_a_document_only_the_vectors_found_has_one(db):
    # keywords: da, db. Vectors: db (TOEIC, the query's 多益), dv, da. Fused: db, da, dv, and dv is no keyword match
    db.doc("da", "選課須知", "選課 選課 選課")
    db.doc("db", "選課與英檢", "選課 TOEIC 成績")
    db.doc("dv", "英文門檻", "TOEIC 分數 退選")
    db.vectors(FakeEmbedder())
    assert _ids(db.search("選課 多益")) == ["da", "db"]
    every = {"": 0.5}  # each passage scores 0.5
    plain = db.hybrid("選課 多益", embedder=FakeEmbedder(), reranker=FakeReranker(every), config=HybridConfig())
    assert _scores(plain) == {"da": 1.5, "db": 1.5, "dv": 1.5}
    r = db.hybrid("選課 多益", embedder=FakeEmbedder(), reranker=FakeReranker(every), config=HybridConfig(w_first=W))
    assert _ids(r) == ["db", "da", "dv"]
    assert _scores(r) == pytest.approx({"db": 1.5 + W, "da": 1.5 + W / 2, "dv": 1.5})


def test_with_the_scores_over_the_heads_best(db):
    # rerank_norm "max" (the 11-04 setting) changes the relevance only; the place is added the same way
    _three(db)
    config = HybridConfig(dense=False, w_first=W, rerank_norm="max")
    r = db.hybrid("停修", reranker=FakeReranker({"甲": 0.3, "乙": 0.6, "丙": 0.15}), config=config)
    assert _scores(r) == pytest.approx({"d1": 3 * 0.5 + W, "d2": 3 * 1.0 + W / 2, "d3": 3 * 0.25})


def test_a_failed_rerank_adds_nothing(db):
    _three(db)

    class Broken(FakeReranker):
        def score(self, query, passages, *, batch_size=16):
            raise RuntimeError("out of memory")

    r = db.hybrid("停修", reranker=Broken({}), config=FIRST)
    assert r.hits == db.search("停修").hits and r.best_rerank is None


def test_the_keyword_only_path_is_not_touched(db):
    _three(db)
    off = HybridConfig(dense=False, rerank=False, w_first=W)
    assert db.hybrid("停修", config=off).hits == db.search("停修").hits


def test_at_the_defaults_the_score_is_the_expression_of_before_to_the_bit(db):
    # w_rerank * relevance + adjustments + scope, in that order: 0.1 and 0.7 are not exact in binary, so any other
    # order of the same sums, or one more term, would show in the last bits
    db.doc("d1", "115學年度第一學期停修公告", "甲 停修", ay=115, sem=1, ident="2026-10-21")
    db.doc("d2", "停修公告", "乙 停修", ident="2025-03-09")
    table = {"甲": 0.1, "乙": 0.7}
    rank = RankConfig(tier=False)
    r = db.hybrid("停修", rank=rank, reranker=FakeReranker(table), config=RERANK)
    for doc_id, rel, day in (("d1", 0.1, date(2026, 10, 21)), ("d2", 0.7, date(2025, 3, 9))):
        adj = W_PERIOD * 0.0 + W_COHORT * False
        adj += rank.w_recent * math.exp(-max(0, (TODAY - day).days) / rank.tau_days)
        assert _hit(r, doc_id).score == 3.0 * rel + adj + 0.0, doc_id
    r = db.hybrid("115-1 停修", rank=rank, reranker=FakeReranker(table), config=RERANK)
    assert _hit(r, "d1").score == 3.0 * 0.1 + (W_PERIOD * 1.0 + W_COHORT * False) + 0.0
    assert _hit(r, "d2").score == 3.0 * 0.7 + (W_PERIOD * 0.0 + W_COHORT * False) + 0.0
    for q in ("停修", "115-1 停修"):  # the switches named at their off values: the same path
        named = HybridConfig(dense=False, w_first=0.0, scope_factor=1.0, valid_decay=1.0)
        assert db.hybrid(q, rank=rank, reranker=FakeReranker(table), config=named).hits == db.hybrid(
            q, rank=rank, reranker=FakeReranker(table), config=RERANK).hits, q
