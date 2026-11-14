"""Plan 2026-11-14 T6 (main session, the same day, after the first rebuild's numbers): HybridConfig.valid_keeps_recency.

As first written, valid_decay left ranking v2's date bonus out of a reranked score (so the date would not count
twice). On the rebuilt databases that reordered the documents that do not go out of date, which only the bonus
ordered (the control's question MRR went from 0.623 to 0.593 and one gold from rank 1 to 19). With the setting on, the
bonus stays and the whole score is multiplied by the document's validity. Off by default: the scores of
test_valid_1114 stand. No model loads; the sentences are made up."""

from __future__ import annotations

import pytest

from nknu.retrieval.hybrid import HybridConfig
from tests.retrieval import test_valid_1114 as tv
from tests.retrieval.test_hybrid import FakeReranker
from tests.retrieval.test_search import _hit, _ids

db = tv.db  # the fixture
KEEP = HybridConfig(dense=False, valid_decay=tv.D, **(tv.FIRST | {"valid_keeps_recency": True}))


def test_on_by_default_since_the_owners_word():
    assert HybridConfig().valid_keeps_recency is True  # owner 11-14: 「还有效开（0.85 定稿算法）」


def test_the_bonus_stays_and_the_whole_is_multiplied(db):
    tv._notices(db)
    r = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=KEEP)
    with_bonus = {d: 1.5 + tv.recency(tv.DOCS[d][0]) for d in tv.DOCS}
    expected = with_bonus | {"last": with_bonus["last"] * tv.D, "old": with_bonus["old"] * tv.D ** 3}
    assert tv._scores(r) == pytest.approx(expected)
    # among the documents that do not go out of date the bonus orders as before: the undated one above the 2022 rule
    assert _hit(r, "undated").score > _hit(r, "rule").score
    assert _ids(r)[-1] == "old"


def test_without_valid_decay_the_setting_changes_nothing(db):
    tv._notices(db)
    plain = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=tv.RERANK)
    kept = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME),
                     config=HybridConfig(dense=False, valid_decay=1.0, valid_keeps_recency=True, scope_factor=1.0))
    assert tv._scores(plain) == tv._scores(kept)
