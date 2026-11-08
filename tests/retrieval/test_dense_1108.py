"""Plan addendum M7 (owner 11-08, 「向量先保留……这两处先记着或者顺手修一下」): the dense path takes the nearest primary
documents and the nearest background documents apart, each with its PER_DOC nearest chunks (VectorIndex.top_docs),
instead of the 300 nearest chunks of the whole database; and the reranker reads a document's keyword chunks first,
then its best dense chunk, instead of the two lists interleaved (_picks)."""

from __future__ import annotations

import numpy as np
import pytest

from nknu.content.schema import BACKGROUND
from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig, _picks
from nknu.retrieval.search import PER_DOC, RankConfig
from nknu.retrieval.vectors import VectorIndex
from tests.retrieval.test_hybrid import FakeEmbedder, FakeReranker, Tiny
from tests.retrieval.test_search import BASE

TIER = RankConfig(recency=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _ids(result):
    return [h.doc_id for h in result.hits]


def member(db, doc_id, title, *chunks):
    """An archive member as the build writes one: an attachment at <archive url>#<its path in the archive>, docs.tier
    background (plan 2026-11-08 T1: search reads the column; the doc_ids here keep the shape T7a gave them before)."""
    ids = db.doc(doc_id, title, *chunks, kind="attachment", tier=BACKGROUND)
    db.conn.execute("UPDATE docs SET url = ? WHERE doc_id = ?", (f"https://example.test/a.zip#{doc_id}.pdf", doc_id))
    return ids


# ---------------------------------------------------------------- top_docs

def _index(seed: int = 7, rows: int = 60, docs: int = 11, dim: int = 8) -> VectorIndex:
    rng = np.random.default_rng(seed)
    matrix = rng.standard_normal((rows, dim)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return VectorIndex(np.arange(100, 100 + rows, dtype=np.int64), matrix, "fake/embed", "0" * 40,
                       tuple(f"d{i:02}" for i in range(docs)), rng.integers(0, docs, rows).astype(np.int64),
                       rng.random(docs) < 0.6)


def _reference(index: VectorIndex, q: np.ndarray, per_doc: int, docs: int, allow) -> list[tuple[int, float]]:
    """top_docs written the plain way: rows nearest first, documents by their nearest row, per_doc rows each."""
    sims = index.matrix @ (q / np.linalg.norm(q))
    rows = sorted((i for i in range(len(index.ids)) if allow is None or allow[index.doc_of[i]]),
                  key=lambda i: (-sims[i], index.ids[i]))
    by_doc: dict[int, list[int]] = {}
    for i in rows:
        by_doc.setdefault(int(index.doc_of[i]), []).append(i)
    return [(int(index.ids[i]), float(sims[i])) for d in list(by_doc)[:docs] for i in by_doc[d][:per_doc]]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_top_docs_is_the_plain_definition(seed):
    index = _index(seed)
    q = np.random.default_rng(seed + 100).standard_normal(8).astype(np.float32)
    for per_doc, docs in ((1, 3), (2, 5), (3, 11), (5, 2), (3, 50)):
        for allow in (None, index.background, ~index.background):
            got = index.top_docs(q, per_doc, docs, allow)
            want = _reference(index, q, per_doc, docs, allow)
            assert [c for c, _ in got] == [c for c, _ in want], (per_doc, docs)
            assert [s for _, s in got] == pytest.approx([s for _, s in want], abs=1e-6)


def test_top_docs_edges():
    index = _index()
    q = np.ones(8, dtype=np.float32)
    assert index.top_docs(q, 0, 5) == [] and index.top_docs(q, 3, 0) == []
    assert index.top_docs(q, 3, 5, np.zeros(len(index.doc_ids), dtype=bool)) == []
    bare = VectorIndex(index.ids, index.matrix, index.model, index.revision)  # an index without its documents
    assert bare.top_docs(q, 3, 5) == [] and len(bare.top(q, 5)) == 5
    with pytest.raises(ValueError):
        index.top_docs(np.ones(3, dtype=np.float32), 3, 5)


def test_load_index_knows_documents_and_tiers(db):
    ids = db.doc("dp", "選課須知", "選課 規定", "英檢 TOEIC 成績")
    member(db, "da5m1", "會議紀錄", "TOEIC 討論")
    db.doc("dj", "學報", "TOEIC 研究", doc_type="journal")
    db.vectors(FakeEmbedder())
    index = vectors.load_index(db.conn)
    assert index.doc_ids == ("da5m1", "dj", "dp")
    assert [index.doc_ids[i] for i in index.doc_of[np.searchsorted(index.ids, ids)]] == ["dp", "dp"]
    assert dict(zip(index.doc_ids, index.background.tolist(), strict=True)) == {"da5m1": True, "dj": True,
                                                                                "dp": False}


# ---------------------------------------------------------------- the dense pool in hybrid search

def _crowd(db, n: int = 5):
    """n archive members nearer to 多益 (cosine 1: TOEIC only) than the one primary document (TOEIC and 停修: 0.71);
    none of them holds the word 多益, so only the dense path can find any."""
    for i in range(n):
        member(db, f"da{i + 1}m1", f"會議紀錄{i}", "英語 TOEIC 檢定 討論")
    db.doc("dp", "外語畢業門檻", "英檢 TOEIC 成績 停修 說明")
    db.vectors(FakeEmbedder())


def test_primary_documents_reach_the_dense_pool_past_background(db):
    _crowd(db)
    config = HybridConfig(rerank=False, dense_candidates=3)
    r = db.hybrid("多益", embedder=FakeEmbedder(), config=config, rank=TIER)
    assert _ids(r)[0] == "dp" and r.ranking == "hybrid"
    assert _ids(r)[1:] == ["da1m1", "da2m1", "da3m1"]  # the background pool: its 3 nearest documents
    assert [h.background for h in r.hits] == [False, True, True, True]
    # with the tier off there is one pool, by nearest chunk: the members fill it
    assert "dp" not in _ids(db.hybrid("多益", embedder=FakeEmbedder(), config=config, rank=BASE))


def test_dense_only_document_is_reranked_on_its_dense_chunks(db):
    _crowd(db, 2)
    rr = FakeReranker({"外語畢業門檻": 0.9})
    r = db.hybrid("多益", embedder=FakeEmbedder(), reranker=rr, rank=TIER)
    ((_, passages),) = rr.calls
    assert sum(p.startswith("外語畢業門檻") for p in passages) == 1  # its one chunk, found by the vectors alone
    assert _ids(r)[0] == "dp" and r.hits[0].score == pytest.approx(3 * 0.9)


def test_documents_only_the_vectors_found_are_marked_background(db):
    # nine primary notices the keywords find, three archive members only the vectors find: unmarked, the members
    # would take primary places; marked, they sit at places 9, 10 and 12
    for i in range(9):
        db.doc(f"p{i}", f"公告{i}", "多益 說明")
    for i in range(3):
        member(db, f"da{i + 1}m1", f"會議紀錄{i}", "英語 TOEIC 討論")
    db.vectors(FakeEmbedder())
    r = db.hybrid("多益", embedder=FakeEmbedder(), config=HybridConfig(rerank=False), rank=TIER, limit=20)
    assert _ids(r) == [f"p{i}" for i in range(8)] + ["da1m1", "da2m1", "p8", "da3m1"]
    assert [h.background for h in r.hits] == [False] * 8 + [True, True, False, True]


def test_tier_off_dense_pool_is_still_per_document(db):
    # one document with 5 nearest chunks: the 3 nearest chunks would be all its own; 3 documents are 3 documents
    member(db, "da1m1", "會議紀錄", *["英語 TOEIC 討論"] * 5)
    for i in range(3):
        db.doc(f"q{i}", f"外語門檻{i}", "英檢 TOEIC 停修 說明")
    db.vectors(FakeEmbedder())
    r = db.hybrid("多益", embedder=FakeEmbedder(), config=HybridConfig(rerank=False, dense_candidates=3), rank=BASE)
    assert _ids(r) == ["da1m1", "q0", "q1"]


def test_dense_pool_keeps_per_doc_chunks(db):
    db.doc("dlong", "外語畢業門檻", *[f"英檢 TOEIC 第{i}條" for i in range(8)])
    db.vectors(FakeEmbedder())
    index = vectors.load_index(db.conn)
    got = index.top_docs(FakeEmbedder().encode(["多益"])[0], PER_DOC, 10)
    assert len(got) == PER_DOC == 3


# ---------------------------------------------------------------- the reranker's chunks of one document

def test_picks_keyword_first_then_the_best_dense_chunk():
    assert _picks([1, 2, 3], [4, 5], 2) == [1, 2, 4]
    assert _picks([1, 2], [2, 4], 2) == [1, 2, 4]  # a dense chunk the keywords already have adds the next one
    assert _picks([1, 2], [1, 2], 2) == [1, 2]
    assert _picks([1, 2, 3], [], 2) == [1, 2]  # keyword only, as with the vectors off
    assert _picks([], [4, 5, 6], 2) == [4, 5]  # found by the vectors alone
    assert _picks([1, 2], [3], 1) == [1, 3]
    assert _picks([1], [], 0) == [] and _picks([], [], 2) == []
