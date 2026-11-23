"""The made-up library the tests of tests/ops share (plan 2026-11-15): the yearly 停修 notices of
tests.retrieval.test_search, a page the vectors alone find, a department's own page, a journal paper (background), the
unit tree, who published what, which documents go out of date, and vectors by the fake embedder. No real data."""

from __future__ import annotations

from nknu.content.schema import JOURNAL
from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import RankConfig
from tests.retrieval.test_hybrid import FakeEmbedder, FakeReranker, Tiny
from tests.retrieval.test_search import TODAY, _withdrawals
from tests.retrieval.test_valid_1114 import valid

TABLE = {"線上申請": 0.9, "注意事項": 0.4, "停修": 0.5, "退選": 0.3}  # what the fake reranker gives a passage
SETTINGS = {
    "the owner's defaults": (HybridConfig(), RankConfig()),
    "before 11-14": (HybridConfig(valid_decay=1.0, scope_factor=1.0), RankConfig(topic_required=False)),
    "rerank only": (HybridConfig(dense=False), RankConfig()),
    "dense only": (HybridConfig(rerank=False), RankConfig()),
    "keywords only": (HybridConfig(dense=False, rerank=False), RankConfig()),
    "no date part": (HybridConfig(), RankConfig(recency=False, tier=False)),
}


def fill(t: Tiny) -> Tiny:
    _withdrawals(t)  # d3 (115-1), d2 (114-2), d1 (114-1): the yearly notices; dx: a standing page
    t.doc("dq", "退選與棄修說明", "退選後不得再加選")  # 退選 and 停修 are one meaning to the fake embedder
    t.doc("dept", "化學系停修注意事項", "化學系學生申請停修另須系主任簽章", ident="2026-10-01")
    t.doc("j1", "停修制度之研究", "本文探討停修制度", doc_type=JOURNAL)
    t.units()
    for doc_id in ("d1", "d2", "d3", "dx", "dq", "j1"):
        t.scope(doc_id)
    t.scope("dept", ["化學系"], publisher="化學系", mentions=["化學系"])
    for doc_id, ay in (("d1", 114), ("d2", 114), ("d3", 115)):
        valid(t, doc_id, 1, ay)
    t.applies("dx", cohort_from=113)
    t.vectors(FakeEmbedder())
    return t


def models() -> dict:
    """The fake models, as keyword arguments of hybrid_search and of what calls it."""
    return {"embedder": FakeEmbedder(), "reranker": FakeReranker(TABLE)}


def search(t: Tiny, query: str, setting: str = "the owner's defaults", explain: dict | None = None, **kw):
    config, rank = SETTINGS[setting]
    return hybrid_search(t.conn, query, today=TODAY, aliases=[], rank=rank, config=config, explain=explain,
                         **models(), **kw)
