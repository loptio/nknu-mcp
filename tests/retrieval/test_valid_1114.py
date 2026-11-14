"""Plan 2026-11-14 T6, the query's part (direction 2026-11-13 第 6 件, 「还有效」): with HybridConfig.valid_decay below 1
and no term named in the query, a reranked document that goes out of date (docs.expires = 1) is multiplied by
valid_decay for each academic year since its own (docs.valid_ay), and the additive date bonus of ranking v2 is left
out of the final score (it stays in the first stage's keyword score). Off (1.0) by default, and off for a database
without the two columns, which a later build writes: these tests add them. Fake models only; the sentences are
invented."""

from __future__ import annotations

import math
import sqlite3
from datetime import date

import pytest

from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig, hybrid_search, validity
from nknu.retrieval.search import W_COHORT, RankConfig
from tests.retrieval.test_hybrid import FakeReranker, Tiny
from tests.retrieval.test_search import BASE, TODAY, _hit, _ids

D = 0.85  # the plan's number 【Claude】; the default is 1.0
RANK = RankConfig(tier=False)  # ranking v2's date part on, as in the default
# The discount as the plan first wrote it, which this file tests: a step each academic year, ranking v2's date bonus
# left out of a reranked score. The three settings added the same evening (and on by default since the owner's word
# of 11-14) are off here, and so is the audience factor: tests/retrieval/test_valid_keep_1114c.py and
# test_valid_smooth_1114c.py have them.
FIRST = {"valid_keeps_recency": False, "valid_smooth": False, "valid_named_step": False, "scope_factor": 1.0}
RERANK = HybridConfig(dense=False, valid_decay=1.0, scope_factor=1.0)
DECAY = HybridConfig(dense=False, valid_decay=D, **FIRST)
SAME = {"停修": 0.5}  # every document scores 0.5 with the reranker
COLUMNS = ("expires", "valid_ay")


def columns(db: Tiny) -> set[str]:
    return {r[1] for r in db.conn.execute("PRAGMA table_info(docs)")} & set(COLUMNS)


def valid(db: Tiny, doc_id: str, expires: int | None, valid_ay: int | None) -> None:
    """docs.expires and docs.valid_ay of one document; the columns are added when the schema has none yet. In a schema
    whose expires cannot be NULL, None is written as 0 (what an unset value means to the search)."""
    for column in COLUMNS:
        if column not in columns(db):
            db.conn.execute(f"ALTER TABLE docs ADD COLUMN {column} INTEGER")
    try:
        db.conn.execute("UPDATE docs SET expires = ?, valid_ay = ? WHERE doc_id = ?", (expires, valid_ay, doc_id))
    except sqlite3.IntegrityError:
        db.conn.execute("UPDATE docs SET expires = 0, valid_ay = ? WHERE doc_id = ?", (valid_ay, doc_id))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _scores(result):
    return {h.doc_id: h.score for h in result.hits}


def recency(day: str | None) -> float:
    """Ranking v2's date bonus at its defaults (search._recency)."""
    return 0.3 * (0.5 if day is None else math.exp(-max(0, (TODAY - date.fromisoformat(day)).days) / 365))


DOCS = {  # doc_id: (identity date, expires, valid_ay); TODAY is in 115
    "now": ("2026-10-01", 1, 115), "last": ("2025-10-01", 1, 114), "old": ("2023-10-01", 1, 112),
    "rule": ("2022-05-01", 0, 110),  # a regulation: it does not go out of date, whatever its year
    "undated": (None, 0, None), "noyear": ("2024-03-01", 1, None),  # goes out of date, but of no known year
    "next": ("2026-10-30", 1, 116),  # next year's: never more than 1
    "unset": ("2021-01-01", None, 109),  # expires not filled in: as 0
}


def _notices(db: Tiny) -> None:
    for doc_id, (ident, expires, valid_ay) in DOCS.items():
        db.doc(doc_id, "停修公告", "受理停修申請", ident=ident)
        valid(db, doc_id, expires, valid_ay)


def test_the_default_is_the_owners():
    # owner 11-14: 「开关默认值：照你的建议——找候选开、还有效开（0.85 定稿算法）、对口开、第一关名次分不开。」
    config = HybridConfig()
    assert config.valid_decay == 0.85 and (config.valid_keeps_recency, config.valid_smooth, config.valid_named_step) == (
        True, True, True)


def test_validity_reads_the_two_columns(db):
    _notices(db)
    got = validity(db.conn, list(DOCS), TODAY, D)
    assert got == pytest.approx({"last": D, "old": D ** 3}) and got["old"] == D ** 3
    assert validity(db.conn, list(DOCS), date(2027, 7, 31), D) == pytest.approx({"last": D, "old": D ** 3})
    assert validity(db.conn, list(DOCS), date(2027, 8, 1), D) == pytest.approx({"now": D, "last": D ** 2, "old": D ** 4})
    assert validity(db.conn, [], TODAY, D) == {} and validity(db.conn, ["nope"], TODAY, D) == {}


def test_off_the_date_bonus_is_added_as_before(db):
    _notices(db)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=RERANK)
    assert _scores(r) == pytest.approx({d: 1.5 + recency(DOCS[d][0]) for d in DOCS})
    assert _ids(r)[0] == "next" and _ids(r).index("rule") > _ids(r).index("last")  # newest first


def test_a_document_that_goes_out_of_date_loses_a_share_each_academic_year(db):
    _notices(db)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    expected = {d: 1.5 for d in DOCS} | {"last": 1.5 * D, "old": 1.5 * D ** 3}
    assert _scores(r) == pytest.approx(expected) and r.best_rerank == 0.5
    assert _ids(r)[-2:] == ["last", "old"]
    # the regulation of 2022 is level with this year's notice: no date bonus is added at this stage any more
    assert _hit(r, "rule").score == _hit(r, "now").score == 1.5


def test_a_term_named_no_decay(db):
    _notices(db)
    for q in ("115-1 停修", "114學年度 停修", "上學期 停修"):
        on = db.hybrid(q, rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
        assert on.hits == db.hybrid(q, rank=RANK, reranker=FakeReranker(SAME), config=RERANK).hits, q
        assert set(_scores(on).values()) == {1.5}, q


def test_a_cohort_is_no_term_and_its_bonus_is_inside(db):
    # (w_rerank * relevance + the cohort bonus) * valid
    _notices(db)
    db.applies("old", cohort_from=111)
    r = db.hybrid("113學年度入學 停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    assert _hit(r, "old").score == pytest.approx((1.5 + W_COHORT) * D ** 3)
    assert _hit(r, "last").score == pytest.approx(1.5 * D) and _hit(r, "now").score == 1.5


def test_the_academic_year_turns_in_august(db):
    db.doc("d1", "停修公告", "受理停修申請", ident="2026-06-01")
    valid(db, "d1", 1, 114)

    def score(today: date) -> float:
        r = hybrid_search(db.conn, "停修", today=today, aliases=[], rank=RANK, reranker=FakeReranker(SAME),
                          config=DECAY)
        return r.hits[0].score

    assert score(date(2026, 7, 31)) == 1.5 and score(date(2026, 8, 1)) == pytest.approx(1.5 * D)
    assert score(date(2027, 1, 15)) == pytest.approx(1.5 * D) and score(date(2027, 8, 1)) == pytest.approx(1.5 * D * D)


def test_a_database_without_the_columns_is_as_before(db):
    for doc_id, (ident, _expires, _ay) in DOCS.items():
        db.doc(doc_id, "停修公告", "受理停修申請", ident=ident)
    for column in columns(db):  # a schema that already has them: taken out again, as in a database built before
        db.conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    assert validity(db.conn, list(DOCS), TODAY, D) is None
    on = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    assert on.hits == db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=RERANK).hits
    assert _scores(on) == pytest.approx({d: 1.5 + recency(DOCS[d][0]) for d in DOCS})
    db.conn.execute("ALTER TABLE docs ADD COLUMN expires INTEGER")  # one of the two is not enough
    assert validity(db.conn, list(DOCS), TODAY, D) is None


def test_without_ranking_v2s_date_part_there_is_no_decay(db):
    # 【Claude】 the decay stands where the date bonus stood (pool.recent): RankConfig(recency=False) asks for neither
    _notices(db)
    on = db.hybrid("停修", rank=BASE, reranker=FakeReranker(SAME), config=DECAY)
    assert on.hits == db.hybrid("停修", rank=BASE, reranker=FakeReranker(SAME), config=RERANK).hits
    assert set(_scores(on).values()) == {1.5}


def test_the_first_stage_keeps_its_date_bonus(db):
    # one place in the head: the other documents keep their keyword scores, date bonus included, and the head is the
    # same document with the decay or without
    _notices(db)
    keyword = _scores(db.search("停修", rank=RANK))
    assert keyword["next"] - keyword["undated"] == pytest.approx(recency("2026-10-30") - recency(None))
    one = {"dense": False, "rerank_docs": 1}
    off = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=HybridConfig(**one, valid_decay=1.0))
    on = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=HybridConfig(**one, valid_decay=D, **FIRST))
    assert _ids(on) == _ids(off) and _ids(on)[0] == "next"
    assert {d: s for d, s in _scores(on).items() if d != "next"} == {d: keyword[d] for d in DOCS if d != "next"}
    assert _hit(off, "next").score == pytest.approx(1.5 + recency("2026-10-30")) and _hit(on, "next").score == 1.5
    kw = HybridConfig(dense=False, rerank=False, valid_decay=D, **FIRST)  # no rerank: nothing to multiply
    assert db.hybrid("停修", rank=RANK, config=kw).hits == db.search("停修", rank=RANK).hits


def test_useful_is_on_topic_times_still_valid_times_right_audience(db):
    # direction 第一节, the three new parts together: (w_rerank * relevance + adjustments + the first stage's place)
    # * valid * scope
    db.units()
    for doc_id, n, applies in (("d1", 3, ["化學系"]), ("d2", 2, None), ("d3", 1, ["化學系"])):
        db.doc(doc_id, "公告", "停修 " * n, ident="2024-10-01")
        valid(db, doc_id, 1, {"d1": 115, "d2": 113, "d3": 113}[doc_id])
        if applies:
            db.scope(doc_id, applies, publisher=applies[0])  # the department's own (T7 reads the publisher)
    assert _ids(db.search("停修", rank=RANK)) == ["d1", "d2", "d3"]
    config = HybridConfig(dense=False, w_first=0.3, valid_decay=D, **(FIRST | {"scope_factor": 0.8}))
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=config)
    # the keyword order with the discount: d2 (the school's) first, then d1, d3
    assert _scores(r) == pytest.approx({"d2": (1.5 + 0.3) * D ** 2, "d1": (1.5 + 0.15) * 0.8,
                                        "d3": (1.5 + 0.0) * D ** 2 * 0.8})


def test_values_outside_zero_to_one_are_refused():
    for bad in (1.5, -0.2):
        with pytest.raises(ValueError):
            HybridConfig(valid_decay=bad)
