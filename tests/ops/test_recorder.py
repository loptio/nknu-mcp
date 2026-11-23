"""plan 2026-11-15 T1: the recorder inside the real search. hybrid_search, keyword_pool and adjustments take an optional
dict and write what each step found into it (nknu.ops.explain shapes it for the owner). Two things are pinned here:
without the dict, and with it, every result is the same to the bit; and what is written adds up to the scores, in the
code's own order of adding, so the inspection tools never show a number the search did not use."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import W_BM25, W_COHORT, W_COVERAGE, W_PERIOD, RankConfig
from tests.ops.library import SETTINGS
from tests.ops.library import search as _search
from tests.retrieval.test_hybrid import FakeEmbedder
from tests.retrieval.test_search import TODAY

QUERIES = ("停修", "115-1 停修", "線上申請停修", "化學系 停修", "113學年度入學 停修", "退選", "注意事項 退費", "學報 停修")


@pytest.mark.parametrize("setting", SETTINGS)
@pytest.mark.parametrize("query", QUERIES)
def test_the_result_is_the_same_to_the_bit_with_the_recorder(db, query, setting):
    assert asdict(_search(db, query, setting, {})) == asdict(_search(db, query, setting))


@pytest.mark.parametrize("setting", SETTINGS)
@pytest.mark.parametrize("query", QUERIES)
def test_the_keyword_parts_add_up_to_the_keyword_score(db, query, setting):
    explain: dict = {}
    _search(db, query, setting, explain)
    found = explain["keyword"]["docs"]
    assert found or query == "學報 停修" or not explain["kw_order"]
    for parts in found.values():
        score = (W_COVERAGE * parts["coverage"] + W_BM25 * parts["bm25_norm"] + W_PERIOD * parts["period"]
                 + W_COHORT * parts["cohort"])
        if parts["date"] is not None:
            score += parts["date"]
        assert score == parts["score"]
        assert (parts["date"] is not None) == explain["keyword"]["recent"]


@pytest.mark.parametrize("setting", [s for s, (config, _) in SETTINGS.items() if config.rerank])
@pytest.mark.parametrize("query", QUERIES)
def test_the_rerank_parts_add_up_to_the_final_score(db, query, setting):
    config, _ = SETTINGS[setting]
    explain: dict = {}
    result = _search(db, query, setting, explain, limit=50)
    final = {h.doc_id: h.score for h in result.hits}
    head = explain["rerank"]["docs"]
    assert set(head) == set(explain["heads"][0]) | set(explain["heads"][1]) and head
    for doc_id, r in head.items():
        adj = r["parts"]["period"] + r["parts"]["cohort"]
        if r["parts"]["date"] is not None:
            adj += r["parts"]["date"]
        assert adj == r["adj"]
        score = config.w_rerank * r["rel"] + r["adj"]
        if r["valid"] is not None:
            score *= r["valid"]
        if r["off"]:
            score *= config.scope_factor
        assert score == r["score"] == final[doc_id]
        assert r["rel"] == max(r["chunks"].values()) and r["chunks"]


def test_what_each_step_found_is_written(db):
    explain: dict = {}
    result = _search(db, "停修", "the owner's defaults", explain, limit=50)
    assert explain["mode"] == "hybrid" and explain["ranking"] == result.ranking == "hybrid"
    assert explain["text"] == "停修" and explain["keyword"]["tokens"] == ["停修"]
    assert explain["final"] == [h.doc_id for h in result.hits]
    assert set(explain["kw_order"]) == set(explain["keyword"]["docs"]) == {"d1", "d2", "d3", "dx", "dept", "j1"}
    assert "dq" in explain["dense_docs"] and "dq" not in explain["keyword"]["docs"]  # the vectors alone found it
    assert explain["background"] == ["j1"] and explain["tiers"][1] == ["j1"] and explain["heads"][1] == ["j1"]
    assert explain["off"] == ["dept"]  # a department's own document, no department named
    assert explain["rerank"]["valid_applies"] is True
    assert explain["rerank"]["docs"]["d1"]["valid"] < 1.0 and explain["rerank"]["docs"]["dx"]["valid"] == 1.0
    assert explain["config"] == asdict(HybridConfig()) and explain["rank"] == asdict(RankConfig())
    assert explain["kw_scores"]["dept"] == explain["keyword"]["docs"]["dept"]["score"] * 0.9


def test_the_keyword_order_is_written_when_no_model_shapes_it(db):
    explain: dict = {}
    result = _search(db, "停修", "keywords only", explain, limit=50)
    assert explain["mode"] == "keyword" and explain["ranking"] == result.ranking == "keyword"
    assert explain["rerank"] is None and explain["dense"] == [] and explain["text"] is None
    assert explain["final"] == [h.doc_id for h in result.hits]


def test_nothing_to_search_for_is_written_as_empty(db):
    explain: dict = {}
    result = _search(db, "!!!", "the owner's defaults", explain)
    assert result.hits == [] and explain["mode"] == "empty" and explain["final"] == []
    assert explain["keyword"] == {"tokens": [], "topic": [], "recent": True, "best_bm25": 0.0, "docs": {}}


def test_a_reranker_that_fails_leaves_no_rerank_and_says_so(db):
    class Broken:
        model_id, revision = "fake/rerank", "0" * 40

        def score(self, query, passages, *, batch_size=16):
            raise RuntimeError("out of memory")

    explain: dict = {}
    config, rank = SETTINGS["the owner's defaults"]
    result = hybrid_search(db.conn, "停修", today=TODAY, aliases=[], rank=rank, config=config,
                           embedder=FakeEmbedder(), reranker=Broken(), explain=explain, limit=50)
    assert explain["rerank"] is None and "out of memory" in explain["rerank_error"]
    assert explain["final"] == [h.doc_id for h in result.hits]


@pytest.mark.parametrize("setting", SETTINGS)
def test_what_is_written_is_json(db, setting):
    explain: dict = {}
    _search(db, "115-1 停修", setting, explain)
    assert json.loads(json.dumps(explain, ensure_ascii=False))["mode"] in ("hybrid", "keyword")


class NoMemory(FakeEmbedder):
    """An embedder that fails on every text, as a model that ran out of memory does."""

    def encode(self, texts, *, batch_size=16):
        raise RuntimeError("no memory")


def test_a_vector_search_that_fails_is_written_and_changes_nothing(db):
    """review 11-15: nothing said the vectors had failed, so a page told a document the vectors had passed it over."""
    from tests.ops.library import TABLE
    from tests.retrieval.test_hybrid import FakeReranker

    config, rank = SETTINGS["the owner's defaults"]
    kw = {"today": TODAY, "aliases": [], "rank": rank, "config": config, "limit": 50}
    explain: dict = {}
    recorded = hybrid_search(db.conn, "停修", embedder=NoMemory(), reranker=FakeReranker(TABLE), explain=explain, **kw)
    plain = hybrid_search(db.conn, "停修", embedder=NoMemory(), reranker=FakeReranker(TABLE), **kw)
    assert asdict(recorded) == asdict(plain)
    assert "no memory" in explain["dense_error"] and explain["dense"] == [] and explain["mode"] == "hybrid"


def test_nothing_for_the_reranker_to_read_is_written(db, monkeypatch):
    from nknu.retrieval import hybrid

    monkeypatch.setattr(hybrid, "rerank_passages", lambda conn, chunk_ids: {})
    explain: dict = {}
    result = _search(db, "停修", "the owner's defaults", explain, limit=50)
    assert explain["rerank"] is None and "小段" in explain["rerank_error"]
    assert explain["final"] == [h.doc_id for h in result.hits]


def test_a_search_without_trouble_writes_no_error(db):
    explain: dict = {}
    _search(db, "停修", "the owner's defaults", explain)
    assert "dense_error" not in explain and "rerank_error" not in explain
