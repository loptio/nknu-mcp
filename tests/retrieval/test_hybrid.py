"""T8 (plan 2026-11-02) and addendum F's T8 addition: hybrid retrieval (bigram pool + bge-m3 pool -> document RRF ->
per-document chunk cap -> rerank -> ranking-v2 adjustments and the unit scope -> the first documents by score; no series
since the owner's 11-03 decision), on tiny databases with a fake embedder and a fake reranker. tests/conftest.py's guard
makes any real model load raise here, so a test that leaves reranker None gets no rerank. One real-model test, marked
models (deselected by default)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from nknu.content import embed
from nknu.core import models
from nknu.retrieval import vectors
from nknu.retrieval.hybrid import W_MENTION, W_SCOPE, HybridConfig, hybrid_search, rerank_passages, rrf
from nknu.retrieval.search import (
    CANDIDATES,
    W_BM25,
    W_COVERAGE,
    W_PERIOD,
    RankConfig,
    adjustments,
    by_tier,
    keyword_pool,
    search_docs,
    top_hits,
)
from nknu.retrieval.vectors import index_for, load_index
from tests.retrieval import test_search
from tests.retrieval.test_search import BASE, TODAY, _withdrawals

KEYS = {"多益": 0, "TOEIC": 0, "停修": 1, "退選": 1}  # words the fake embedder treats as one meaning, by axis
# a made-up slice of the unit tree (addendum F; no research data): kinds as nknu.content.units.KINDS writes them
UNITS = (
    ("國立高雄師範大學", "学校", None, None, ("高師大",)),
    ("教務處", "行政单位", "國立高雄師範大學", None, ()),
    ("科技學院", "学院", "國立高雄師範大學", "燕巢", ()),
    ("理學院", "学院", "國立高雄師範大學", "燕巢", ()),
    ("文學院", "学院", "國立高雄師範大學", "和平", ()),
    ("軟體工程與管理學系", "系所", "科技學院", "燕巢", ("軟體系",)),
    ("化學系", "系所", "理學院", "燕巢", ()),
    ("國文學系", "系所", "文學院", "和平", ()),
)
# the scope tables of addendum F (T7d adds them to the schema; IF NOT EXISTS keeps these tests working either way)
SCOPE_DDL = """
CREATE TABLE IF NOT EXISTS units (unit_id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, parent_id TEXT,
                                  campus TEXT);
CREATE TABLE IF NOT EXISTS unit_aliases (alias TEXT NOT NULL, unit_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doc_scope (doc_id TEXT NOT NULL, relation TEXT NOT NULL, unit_id TEXT, campus TEXT,
                                      level TEXT, cohort_from INTEGER);
"""
# scope_factor and valid_decay at 1: the settings of before plan 2026-11-14, under which these tests were written
# (the owner's defaults of 11-14 are 0.9 and 0.85: tests/retrieval/test_audience_1114.py, test_valid_1114.py)
OFF = HybridConfig(dense=False, rerank=False, scope_factor=1.0, valid_decay=1.0)


class FakeEmbedder:
    """model_id fake/embed, revision "0" * 40, dim 16. KEYS found in a text add their axis (summed, then normalised);
    a text with none gets a hash vector orthogonal to those axes. Records the texts it encodes."""

    def __init__(self, model_id: str = "fake/embed", revision: str = "0" * 40, dim: int = 16) -> None:
        self.model_id, self.revision, self.dim = model_id, revision, dim
        self.texts: list[str] = []
        self.calls = 0

    def vector(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for key, axis in KEYS.items():
            if key in text:
                v[axis] += 1.0
        if not v.any():
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
            v[: max(KEYS.values()) + 1] = 0.0
        return v / np.linalg.norm(v)

    def encode(self, texts: list[str], *, batch_size: int = 16) -> np.ndarray:
        texts = list(texts)
        self.calls += 1
        self.texts += texts
        return np.stack([self.vector(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class FakeReranker:
    """Each passage scores the highest value of the table's substrings it holds, else 0.0; records (query, passages)."""

    model_id, revision = "fake/rerank", "0" * 40

    def __init__(self, table: dict[str, float]) -> None:
        self.table = dict(table)
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str], *, batch_size: int = 16) -> list[float]:
        passages = list(passages)
        self.calls.append((query, passages))
        return [max((v for k, v in self.table.items() if k in p), default=0.0) for p in passages]


class Tiny(test_search.Tiny):
    def vectors(self, embedder, model: tuple[str, str] | None = None) -> None:
        """chunk_vecs and the vec_* meta, written by the build's own writer; model overrides vec_model/vec_revision."""
        embed.embed_chunks(self.conn, embedder)
        if model is not None:
            self.conn.executemany("UPDATE meta SET value = ? WHERE key = ?",
                                  [(model[0], "vec_model"), (model[1], "vec_revision")])

    def units(self, rows=UNITS) -> None:
        """units and unit_aliases as T7d writes them: the unit's name and its aliases in unit_aliases."""
        self.conn.executescript(SCOPE_DDL)
        for unit_id, kind, parent_id, campus, aliases in rows:
            self.conn.execute("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)",
                              (unit_id, unit_id, kind, parent_id, campus))
            self.conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)",
                                  [(a, unit_id) for a in (unit_id, *aliases)])

    def scope(self, doc_id, applies=("國立高雄師範大學",), *, campus=(), levels=(), cohort=None, mentions=(),
              publisher="教務處") -> None:
        """doc_scope rows: an applies_to row per unit x campus x level (NULL when none), the publisher, the mentions."""
        self.conn.executescript(SCOPE_DDL)
        rows = [(doc_id, "applies_to", u, c, lv, cohort) for u in applies for c in (campus or (None,))
                for lv in (levels or (None,))]
        rows.append((doc_id, "publisher", publisher, None, None, None))
        rows += [(doc_id, "mentions", u, None, None, None) for u in mentions]
        self.conn.executemany("INSERT INTO doc_scope(doc_id, relation, unit_id, campus, level, cohort_from)"
                              " VALUES (?, ?, ?, ?, ?, ?)", rows)

    def hybrid(self, q, aliases=(), rank=BASE, **kw):
        return hybrid_search(self.conn, q, today=TODAY, aliases=[list(g) for g in aliases], rank=rank, **kw)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})  # one index per database file and process: none from another test
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _ids(result):
    return [h.doc_id for h in result.hits]


def _hit(result, doc_id):
    return next(h for h in result.hits if h.doc_id == doc_id)


def _scores(result):
    return {h.doc_id: h.score for h in result.hits}


# ---------------------------------------------------------------- config, helpers

def test_hybrid_defaults():
    # addendum M (owner 11-08): the head split by tier and 50 + 10 documents, raw rerank scores, no unit weights
    assert HybridConfig() == HybridConfig(dense=True, rerank=True, rrf_k=60, dense_candidates=300, rerank_docs=50,
                                          rerank_background=10, chunks_per_doc=2, w_rerank=3.0, rerank_norm="raw")
    assert HybridConfig().dense_candidates == CANDIDATES and HybridConfig().w_rerank == W_COVERAGE + W_BM25
    assert (W_SCOPE, W_MENTION) == (0.0, 0.0) and (HybridConfig().w_scope, HybridConfig().w_mention) == (0.0, 0.0)


def test_rrf():
    assert rrf([["a", "b", "c"], ["c", "a"]], 60) == pytest.approx({"a": 1/61 + 1/62, "b": 1/62, "c": 1/63 + 1/61})
    assert rrf([], 60) == {} and rrf([["a"], []], 1) == {"a": 0.5}


def test_extracted_helpers_are_search_docs(db):
    # search_docs is keyword_pool + its first documents in tier order (by_tier, top_hits; the series folding went
    # 11-03), and adjustments is the ranking-v2 part of each keyword score
    _withdrawals(db)
    db.doc("j1", "學報論文", "停修制度之研究", ident="2023-01-01", doc_type="journal")
    cases = (("停修", RankConfig()), ("115-1 停修", RankConfig()), ("停修", BASE), ("我是113學年度入學 停修", BASE))
    for q, rank in cases:
        pool = keyword_pool(db.conn, q, today=TODAY, aliases=[], rank=rank)
        expected = search_docs(db.conn, q, today=TODAY, aliases=[], rank=rank)
        assert top_hits(by_tier(pool.ordered), 10) == expected.hits, q
        assert [h.doc_id for h in expected.hits] == [d.doc_id for d in by_tier(pool.ordered)][:10], q
        assert pool.rows == expected.candidates and pool.query == q, q
        adj = adjustments(db.conn, pool.docs, pool, rank, TODAY)
        best = min(d.bm25 for d in pool.docs.values())
        for d in pool.docs.values():
            assert d.score == pytest.approx(W_COVERAGE * d.coverage + W_BM25 * d.bm25 / best + adj[d.doc_id]), q


def test_both_off_equals_search_docs(db):
    _withdrawals(db)
    for q in ("停修", "115-1 停修", "114學年度第二學期停修", "選課"):
        off = db.hybrid(q, config=OFF)
        assert off.hits == db.search(q).hits and off.ranking == "keyword" and off.best_rerank is None, q
        assert off.candidates == db.search(q).candidates, q


# ---------------------------------------------------------------- dense path

def test_dense_finds_what_keywords_miss(db):
    (cid,) = db.doc("d1", "英文畢業門檻", "TOEIC 550 分以上")
    db.vectors(FakeEmbedder())
    assert _ids(db.search("多益")) == []
    r = db.hybrid("多益", embedder=FakeEmbedder(), reranker=None, config=HybridConfig(rerank=False))
    assert _ids(r) == ["d1"] and r.ranking == "hybrid" and r.candidates == 1 and r.best_rerank is None
    assert r.hits[0].chunk_ids == [cid] and r.hits[0].bm25 == 0.0 and r.hits[0].coverage == 0.0


def test_load_index(db):
    ids = [*db.doc("d1", "線上申請停修", "停修以一科為限", "退選"), *db.doc("d2", "英文畢業門檻", "TOEIC 550")]
    assert load_index(db.conn) is None  # an empty chunk_vecs: a build without --embed
    db.vectors(FakeEmbedder())
    index = load_index(db.conn)
    assert index.model == "fake/embed" and index.revision == "0" * 40 and sorted(index.ids.tolist()) == sorted(ids)
    assert index.matrix.dtype == np.float32 and np.allclose(np.linalg.norm(index.matrix, axis=1), 1.0)
    top = index.top(FakeEmbedder().vector("多益"), 2)
    assert len(top) == 2 and top[0][0] == ids[-1] and top[0][1] == pytest.approx(1.0, abs=1e-3)
    assert len(index.top(FakeEmbedder().vector("多益"), 10)) == len(ids) and index.top(np.ones(16), 0) == []
    # a partial set (vec_count says more than the table holds) is not used
    db.conn.execute("DELETE FROM chunk_vecs WHERE chunk_id = ?", (ids[0],))
    assert load_index(db.conn) is None


def test_index_reloads_after_rebuild(db):
    db.doc("d1", "線上申請停修", "停修以一科為限")
    db.vectors(FakeEmbedder())
    first = index_for(db.conn)
    assert first is not None and index_for(db.conn) is first
    db.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('built_at', '2026-11-03T08:00:00+08:00')")
    second = index_for(db.conn)
    assert second is not None and second is not first and index_for(db.conn) is second


def test_vectors_from_another_model_are_not_used(db, monkeypatch):
    _withdrawals(db)
    db.vectors(FakeEmbedder(), model=("other/model", "1" * 40))
    emb = FakeEmbedder()
    r = db.hybrid("停修", embedder=emb, config=HybridConfig(rerank=False))
    assert emb.calls == 0 and r.ranking == "keyword" and r.hits == db.search("停修").hits
    asked = []
    monkeypatch.setattr(models, "shared_embedder", lambda: asked.append("e") or FakeEmbedder())
    r = db.hybrid("停修", config=HybridConfig(rerank=False))
    assert asked == [] and r.ranking == "keyword" and r.hits == db.search("停修").hits
    # the same model at another revision is another model
    db.conn.execute("UPDATE meta SET value = 'fake/embed' WHERE key = 'vec_model'")
    r = db.hybrid("停修", embedder=emb, config=HybridConfig(rerank=False))
    assert emb.calls == 0 and r.ranking == "keyword"


def test_db_without_vectors_is_keyword_search(db, monkeypatch):
    _withdrawals(db)
    db.conn.execute("DROP TABLE chunk_vecs")  # a skeleton-2 database
    asked = []
    monkeypatch.setattr(models, "shared_embedder", lambda: asked.append("embedder"))
    monkeypatch.setattr(models, "shared_reranker", lambda: asked.append("reranker"))
    for q in ("停修", "115-1 停修", "選課"):
        r = db.hybrid(q)
        assert r.hits == db.search(q).hits and r.ranking == "keyword" and r.best_rerank is None, q
    assert asked == []


def _record_models(monkeypatch, table: dict[str, float] | None = None) -> list[str]:
    """The shared models replaced by fakes that note each time one is asked for; returns that list."""
    asked: list[str] = []
    monkeypatch.setattr(models, "shared_embedder", lambda: asked.append("embedder") or FakeEmbedder())
    monkeypatch.setattr(models, "shared_reranker", lambda: asked.append("reranker") or FakeReranker(table or {}))
    return asked


def test_empty_vectors_ask_for_no_model(db, monkeypatch):
    # a build without --embed: chunk_vecs is there but empty. The dense path is on and has no usable vectors, so
    # neither model is asked for, not even the reranker (main session 11-03, addendum J; the plan's Review Focus 1),
    # and the answer gets the keyword note (ranking "keyword")
    _withdrawals(db)
    assert db.conn.execute("SELECT count(*) FROM chunk_vecs").fetchone() == (0,)
    asked = _record_models(monkeypatch, {"停修": 0.9})
    for q in ("停修", "115-1 停修", "選課"):
        r = db.hybrid(q)
        assert r.hits == db.search(q).hits and r.ranking == "keyword" and r.best_rerank is None, q
    assert asked == []
    emb, rr = FakeEmbedder(), FakeReranker({"停修": 0.9})  # given models are not asked either
    r = db.hybrid("停修", embedder=emb, reranker=rr)
    assert (emb.calls, rr.calls, r.ranking) == (0, [], "keyword") and r.hits == db.search("停修").hits


def test_vectors_of_another_model_ask_for_no_model(db, monkeypatch):
    # vectors written by another model (or revision) than the pinned one: as if there were none, with rerank on too
    _withdrawals(db)
    db.vectors(FakeEmbedder(), model=("other/model", "1" * 40))
    asked = _record_models(monkeypatch, {"停修": 0.9})
    r = db.hybrid("停修")  # the pins against other/model
    assert asked == [] and r.ranking == "keyword" and r.best_rerank is None and r.hits == db.search("停修").hits
    emb, rr = FakeEmbedder(), FakeReranker({"停修": 0.9})  # fake/embed against other/model
    r = db.hybrid("停修", embedder=emb, reranker=rr)
    assert (emb.calls, rr.calls, r.ranking) == (0, [], "keyword") and r.hits == db.search("停修").hits
    db.conn.execute("UPDATE meta SET value = 'fake/embed' WHERE key = 'vec_model'")  # the same model, another revision
    r = db.hybrid("停修", embedder=emb, reranker=rr)
    assert (emb.calls, rr.calls, r.ranking) == (0, [], "keyword") and asked == []


def test_no_dense_is_the_rerank_only_mode_whatever_the_vectors(db, monkeypatch):
    # HybridConfig(dense=False), the server's --no-dense: the reranker is asked for on an empty chunk_vecs, and on
    # vectors of another model; the embedder never is
    db.doc("d1", "停修規定", "停修 停修 一般說明文字", "停修以一科為限")
    db.doc("d2", "停修公告", "停修申請 停修 停修 停修")
    assert _ids(db.search("停修"))[0] == "d2"
    asked = _record_models(monkeypatch, {"一科為限": 0.9})
    r = db.hybrid("停修", config=HybridConfig(dense=False))
    assert asked == ["reranker"] and _ids(r)[0] == "d1" and r.ranking == "hybrid" and r.best_rerank == 0.9
    db.vectors(FakeEmbedder(), model=("other/model", "1" * 40))
    r = db.hybrid("停修", config=HybridConfig(dense=False))
    assert asked == ["reranker", "reranker"] and _ids(r)[0] == "d1" and r.ranking == "hybrid"


def test_models_unavailable_falls_back_to_keywords(db, monkeypatch):
    _withdrawals(db)
    db.vectors(FakeEmbedder(), model=(models.EMBED_ID, models.EMBED_REVISION))  # so the shared embedder is asked for
    asked = []

    def unavailable(kind):
        def load():
            asked.append(kind)
            raise models.ModelUnavailable(f"{kind} is not in this machine's cache")
        return load

    monkeypatch.setattr(models, "shared_embedder", unavailable("embedder"))
    monkeypatch.setattr(models, "shared_reranker", unavailable("reranker"))
    r = db.hybrid("停修")
    assert r.hits == db.search("停修").hits and r.ranking == "keyword" and r.best_rerank is None
    assert asked == ["embedder", "reranker"]


def test_inference_error_falls_back_to_keywords(db):
    _withdrawals(db)
    db.vectors(FakeEmbedder())

    class Broken(FakeEmbedder):
        def encode(self, texts, *, batch_size=16):
            raise RuntimeError("MPS backend out of memory")

    class BrokenReranker(FakeReranker):
        def score(self, query, passages, *, batch_size=16):
            raise RuntimeError("MPS backend out of memory")

    r = db.hybrid("停修", embedder=Broken(), reranker=BrokenReranker({}))
    assert r.hits == db.search("停修").hits and r.ranking == "keyword" and r.best_rerank is None


def test_no_tokens_no_dense(db):
    db.doc("d1", "線上申請停修", "停修以一科為限")
    db.vectors(FakeEmbedder())
    emb, rr = FakeEmbedder(), FakeReranker({})
    for q in ("!!!", "", "　"):
        r = db.hybrid(q, embedder=emb, reranker=rr)
        assert r.hits == [] and r.candidates == 0 and r.ranking == "keyword", q
    assert emb.calls == 0 and rr.calls == []


def test_nothing_found_asks_for_no_model(db, monkeypatch):
    # words to search for, no keyword match and no vectors: nothing to rerank, so no model is asked for
    db.doc("d1", "線上申請停修", "停修以一科為限")
    asked = []
    monkeypatch.setattr(models, "shared_embedder", lambda: asked.append("embedder"))
    monkeypatch.setattr(models, "shared_reranker", lambda: asked.append("reranker"))
    r = db.hybrid("畢業門檻")
    assert r.hits == [] and r.candidates == 0 and asked == []


# ---------------------------------------------------------------- rerank

def test_rerank_reorders_and_puts_its_best_chunk_first(db):
    ids = db.doc("d1", "停修規定", "停修 停修 一般說明文字", "停修以一科為限")
    db.doc("d2", "停修公告", "停修申請 停修 停修 停修")
    keyword = db.search("停修")
    assert _ids(keyword)[0] == "d2" and _hit(keyword, "d1").chunk_ids[0] == ids[0]  # keywords alone
    r = db.hybrid("停修", reranker=FakeReranker({"一科為限": 0.9}), config=HybridConfig(dense=False))
    assert _ids(r)[0] == "d1" and _hit(r, "d1").chunk_ids == [ids[1], ids[0]]
    assert r.ranking == "hybrid" and r.best_rerank == 0.9


def test_rerank_scores_are_raw_unless_normalised(db):
    # addendum M4 (11-08): raw by default, so an unsure head stays unsure; "max" is the 11-04 build's setting
    db.doc("d1", "停修公告", "甲 停修")
    db.doc("d2", "停修公告", "乙 停修")
    table = {"甲": 0.6, "乙": 0.3}
    r = db.hybrid("停修", reranker=FakeReranker(table), config=HybridConfig(dense=False))
    assert [(h.doc_id, h.score) for h in r.hits] == [("d1", pytest.approx(1.8)), ("d2", pytest.approx(0.9))]
    assert r.best_rerank == 0.6
    norm = db.hybrid("停修", reranker=FakeReranker(table), config=HybridConfig(dense=False, rerank_norm="max"))
    assert [(h.doc_id, h.score) for h in norm.hits] == [("d1", pytest.approx(3.0)), ("d2", pytest.approx(1.5))]


def test_adjustments_survive_rerank(db):
    # equal rerank scores: the document whose (學年度, 學期) the query names comes first
    db.doc("da", "線上申請停修", "停修以一科為限", ay=114, sem=2)
    db.doc("dz", "線上申請停修", "停修以一科為限", ay=115, sem=1)
    r = db.hybrid("停修", reranker=FakeReranker({"停修": 0.5}), config=HybridConfig(dense=False))
    assert _ids(r) == ["da", "dz"]  # no term named: a tie, by doc_id
    r = db.hybrid("115-1 停修", reranker=FakeReranker({"停修": 0.5}), config=HybridConfig(dense=False))
    assert _ids(r) == ["dz", "da"] and _hit(r, "dz").score == pytest.approx(3.0 * 0.5 + W_PERIOD)


def test_term_spellings_send_one_model_query(db):
    _withdrawals(db)
    db.vectors(FakeEmbedder())
    tops = []
    for q in ("115-1 停修", "115學年度第1學期 停修", "這學期 停修"):  # TODAY is in 115-1
        emb, rr = FakeEmbedder(), FakeReranker({"115年11月3日": 0.8})
        r = db.hybrid(q, embedder=emb, reranker=rr)
        assert emb.texts == ["115學年度第1學期 停修"], q
        assert [query for query, _ in rr.calls] == ["115學年度第1學期 停修"], q
        assert r.ranking == "hybrid", q
        tops.append(r.hits[:3])
    assert tops[0] == tops[1] == tops[2]


def test_per_document_cap(db):
    db.doc("d1", "停修規定", *[f"停修第{i}款" for i in range(10)])
    db.doc("d2", "停修公告", "停修申請")
    rr = FakeReranker({})
    db.hybrid("停修", reranker=rr, config=HybridConfig(dense=False))
    ((_, passages),) = rr.calls
    assert sum(p.startswith("停修規定") for p in passages) == 2 and len(passages) == 3


def test_rerank_docs_cap(db):
    for i in range(60):
        db.doc(f"d{i:02}", f"停修公告{i}", "停修")
    keyword = _ids(db.search("停修", limit=60))
    assert keyword == [f"d{i:02}" for i in range(60)]
    rr = FakeReranker({"停修公告29": 0.9, "停修公告55": 0.95})
    r = db.hybrid("停修", limit=60, reranker=rr, config=HybridConfig(dense=False))
    ((_, passages),) = rr.calls
    assert len({p.split("\n")[0] for p in passages}) == 50
    # the 50 by RRF are scored and sorted; the other 10 follow in RRF order, d55 never scored
    assert _ids(r)[0] == "d29" and _ids(r)[50:] == keyword[50:] and r.best_rerank == 0.9


def test_dense_chunks_reach_the_reranker(db):
    # owner 11-08: the two best keyword chunks and then the best dense chunk (at most chunks_per_doc + 1); until then
    # the two lists were interleaved to 2, so the dense chunk pushed out the second keyword chunk
    ids = db.doc("d1", "選課須知", "選課 選課 選課", "選課規定", "英檢 TOEIC 成績")
    db.vectors(FakeEmbedder())
    rr = FakeReranker({})
    r = db.hybrid("選課 多益", embedder=FakeEmbedder(), reranker=rr)
    ((query, passages),) = rr.calls
    texts = rerank_passages(db.conn, ids)
    assert query == "選課 多益" and passages == [texts[ids[0]], texts[ids[1]], texts[ids[2]]]
    assert r.ranking == "hybrid" and _hit(r, "d1").chunk_ids == [ids[0], ids[1], ids[2]]


def test_each_version_is_ranked_on_its_own_after_rerank(db):
    # was test_series_folding_after_rerank (d1 reranked best, its series' newest member shown with d1's score). Owner
    # 11-03: no series, so the reranker's pick is first under its own id and score, and the other versions follow as
    # results of their own: score ties newest identity date first, no date last
    _withdrawals(db)  # d3 (115-1), d2 (114-2), d1 (114-1), dx (no date)
    r = db.hybrid("停修", reranker=FakeReranker({"114年11月4日": 0.9}), config=HybridConfig(dense=False))
    assert _ids(r) == ["d1", "d3", "d2", "dx"] and r.ranking == "hybrid"
    assert r.hits[0].score == pytest.approx(3.0 * 0.9) and {h.score for h in r.hits[1:]} == {0.0}


def test_rerank_passage_has_titles_heading_and_table_header(db):
    text = "第二條 收費標準\n| 項目 | 金額 |\n| --- | --- |\n| 學費 | 1,000 |\n| 雜費 | 500 |"
    head_start, head_end, start = text.index("| 項目"), text.index("| 學費"), text.index("| 雜費")
    db.conn.execute(
        "INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, parent_title, linktext, has_text, text,"
        " chars) VALUES ('da1', 'attachment', '附件:pdf', '收費標準.pdf', 'https://example.test/da1', '教務處',"
        " '115學年度收費公告', '收費標準 | 收費標準.pdf', 1, ?, ?)", (text, len(text)))
    db.conn.execute("INSERT INTO sections(doc_id, sec_id, heading, start, end)"
                    " VALUES ('da1', 0, '第二條 收費標準', 0, ?)", (len(text),))
    cid = db.conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end, head_start, head_end)"
                          " VALUES ('da1', 0, ?, ?, ?, ?)", (start, len(text), head_start, head_end)).lastrowid
    want = embed.passage("收費標準.pdf", "115學年度收費公告", "收費標準 | 收費標準.pdf", "第二條 收費標準",
                         text[head_start:head_end], text[start:])
    assert rerank_passages(db.conn, [cid]) == {cid: want}
    assert all(part in want for part in ("115學年度收費公告", "第二條 收費標準", "| 項目 | 金額 |", "| 雜費 | 500 |"))


# ---------------------------------------------------------------- the unit scope (addendum F)

# The scope's weights are 0 since 11-08 (addendum M5); the mechanism stays, so these tests give it addendum F's
# starting values back.
S_SCOPE, S_MENTION = 0.3, 0.1
SCOPED_OFF = HybridConfig(dense=False, rerank=False, w_scope=S_SCOPE, w_mention=S_MENTION, scope_factor=1.0)
SCOPED_RERANK = HybridConfig(dense=False, w_scope=S_SCOPE, w_mention=S_MENTION, scope_factor=1.0)


def test_scope_has_no_weight_by_default(db):
    db.units()
    db.doc("da", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    db.doc("dz", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    db.scope("da", ["化學系"], levels=["碩士"])
    db.scope("dz", ["軟體工程與管理學系"], levels=["碩士"], mentions=["軟體工程與管理學系"])
    q = "軟體系 碩士班 畢業門檻"
    assert db.hybrid(q, config=OFF).hits == db.search(q).hits
    assert _ids(db.hybrid(q, config=SCOPED_OFF)) == ["dz", "da"]  # the fixture does reach the scope

def test_scope_boosts_the_named_department(db):
    db.units()
    db.doc("da", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    db.doc("dz", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    db.scope("da", ["化學系"], levels=["碩士"])
    db.scope("dz", ["軟體工程與管理學系"], levels=["碩士"])
    q = "軟體系 碩士班 畢業門檻"
    plain = db.search(q)
    assert _ids(plain) == ["da", "dz"]  # keywords alone: a tie, by doc_id
    r = db.hybrid(q, config=SCOPED_OFF)
    assert _ids(r) == ["dz", "da"] and r.ranking == "keyword"
    assert _scores(r) == pytest.approx({"dz": _scores(plain)["dz"] + S_SCOPE, "da": _scores(plain)["da"] - S_SCOPE})
    # the rerank path: equal rerank scores, the scope decides
    r = db.hybrid(q, reranker=FakeReranker({"畢業": 0.5}), config=SCOPED_RERANK)
    assert _ids(r) == ["dz", "da"] and _hit(r, "dz").score - _hit(r, "da").score == pytest.approx(2 * S_SCOPE)
    # both weights 0 (T10's comparison from one build): as if the build had no scope
    assert db.hybrid(q, config=OFF).hits == plain.hits  # OFF: w_scope and w_mention are 0 by default


def test_scope_neutral_for_school_wide(db):
    db.units()
    for doc_id in ("da", "db", "dc"):
        db.doc(doc_id, "畢業門檻", "學生須修畢 128 學分")
    db.scope("da", ["化學系"])  # 化學系's rule
    db.scope("db")  # the school's (an office's reach)
    q = "軟體系 畢業門檻"  # dc has no scope rows at all
    plain, r = _scores(db.search(q)), _scores(db.hybrid(q, config=SCOPED_OFF))
    assert r["db"] == plain["db"] and r["dc"] == plain["dc"] and r["da"] == pytest.approx(plain["da"] - S_SCOPE)
    # a query that names nothing, or only the school, changes nothing
    for q in ("畢業門檻", "115-1 畢業門檻", "高師大 畢業門檻"):
        assert db.hybrid(q, config=SCOPED_OFF).hits == db.search(q).hits, q


def test_campus_named_in_query(db):
    db.units()
    db.doc("da", "停車證申請", "汽車停車證請至總務處申請")
    db.doc("dz", "停車證申請", "汽車停車證請至總務處申請")
    db.scope("da", campus=["和平"])
    db.scope("dz", campus=["燕巢"])
    assert _ids(db.search("燕巢校區 停車證")) == ["da", "dz"]
    assert _ids(db.hybrid("燕巢校區 停車證", config=SCOPED_OFF)) == ["dz", "da"]
    assert _ids(db.hybrid("和平校區 停車證", config=SCOPED_OFF)) == ["da", "dz"]
    assert _ids(db.hybrid("軟體系 停車證", config=SCOPED_OFF)) == ["dz", "da"]  # the unit's campus counts for the query
    both = "和平與燕巢校區 停車證"  # every campus: the whole school, so nothing
    assert db.hybrid(both, config=SCOPED_OFF).hits == db.search(both).hits


def test_mention_adds_a_little(db):
    db.units()
    db.doc("da", "停修公告", "停修申請請洽各系辦公室")
    db.doc("dz", "停修公告", "停修申請請洽各系辦公室")
    db.scope("da")
    db.scope("dz", mentions=["軟體工程與管理學系"])
    q = "軟體系 停修"
    plain, r = db.search(q), db.hybrid(q, config=SCOPED_OFF)
    assert _ids(plain) == ["da", "dz"] and _ids(r) == ["dz", "da"]
    assert _scores(r) == pytest.approx({"dz": _scores(plain)["dz"] + S_MENTION, "da": _scores(plain)["da"]})


def test_db_without_scope_is_neutral(db):
    db.doc("da", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    db.doc("dz", "碩士班畢業門檻", "碩士班學生須修畢 30 學分")
    q = "軟體系 碩士班 畢業門檻"
    for table in ("doc_scope", "units", "unit_aliases"):  # a skeleton or pre-T7d database
        db.conn.execute(f"DROP TABLE IF EXISTS {table}")
    assert db.hybrid(q, config=SCOPED_OFF).hits == db.search(q).hits
    # scope rows but no unit tree to read the query with
    db.scope("dz", ["軟體工程與管理學系"])
    assert db.hybrid(q, config=SCOPED_OFF).hits == db.search(q).hits
    # the unit tree without the scope table
    db.units()
    db.conn.execute("DROP TABLE doc_scope")
    assert db.hybrid(q, config=SCOPED_OFF).hits == db.search(q).hits
    r = db.hybrid(q, reranker=FakeReranker({"畢業": 0.5}), config=SCOPED_RERANK)
    assert _ids(r) == ["da", "dz"] and r.hits[0].score == r.hits[1].score


# ---------------------------------------------------------------- real models (deselected by default)

@pytest.mark.models
def test_real_models_bridge_vocabulary(tmp_path):
    pytest.importorskip("sentence_transformers")
    if not all(models.is_cached(m, r) for m, r in models.PINS):
        pytest.skip("bge-m3 or bge-reranker-v2-m3 is not in this machine's cache")
    db = Tiny(tmp_path / "t.db")
    db.doc("d1", "英文畢業門檻", "學士班學生須通過 TOEIC 550 分以上，始得畢業。")
    db.doc("d2", "線上申請停修", "停修以一科為限，不予退費。")
    db.doc("d3", "教室借用辦法", "借用教室請於三日前上網申請。")
    db.doc("d4", "學雜費繳費", "學雜費請於開學前繳納。")
    embedder, reranker = models.load_embedder(), models.load_reranker()
    db.vectors(embedder)
    assert _ids(db.search("多益")) == []
    r = hybrid_search(db.conn, "多益", today=TODAY, aliases=[], embedder=embedder, reranker=reranker,
                      index=load_index(db.conn))
    assert _ids(r)[0] == "d1" and r.ranking == "hybrid" and r.best_rerank is not None
    db.conn.close()
