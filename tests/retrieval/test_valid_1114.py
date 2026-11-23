"""Plan 2026-11-14 T6, the query's part (direction 2026-11-13 第 6 件, 「还有效」): with HybridConfig.valid_decay below 1
and no term named in the query, a reranked document that goes out of date (docs.expires = 1) counts less the older it
is. One whose title names its term or year (docs.period_ay) loses a whole step of valid_decay for each academic year
since its own (docs.valid_ay); any other falls by its own age, valid_decay ** (days since the date it ranks by / 365),
so nothing drops on 1 August. Ranking v2's date bonus stays in the score, and the whole is multiplied. A database
without the two columns is not discounted: these tests add them where the schema has none. Fake models only; the
sentences are invented."""

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

D = 0.85  # the default
RANK = RankConfig(tier=False)  # ranking v2's date part on, as in the default
# the audience factor is off in both: tests/retrieval/test_audience_1114.py has it
RERANK = HybridConfig(dense=False, valid_decay=1.0, scope_factor=1.0)
DECAY = HybridConfig(dense=False, valid_decay=D, scope_factor=1.0)
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


def recency(day: str | None, today: date = TODAY) -> float:
    """Ranking v2's date bonus at its defaults (search._recency)."""
    return 0.3 * (0.5 if day is None else math.exp(-max(0, (today - date.fromisoformat(day)).days) / 365))


def aged(day: str, today: date = TODAY) -> float:
    """What a document that names no year is multiplied by: its own age."""
    return D ** ((today - date.fromisoformat(day)).days / 365)


DOCS = {  # doc_id: (identity date, expires, valid_ay); TODAY is in 115. No title names a term or a year
    "now": ("2026-10-01", 1, 115), "last": ("2025-10-01", 1, 114), "old": ("2023-10-01", 1, 112),
    "rule": ("2022-05-01", 0, 110),  # a regulation: it does not go out of date, whatever its year
    "undated": (None, 0, None), "noyear": ("2024-03-01", 1, None),  # goes out of date, but of no known year
    "next": ("2026-10-30", 1, 116),
    "unset": ("2021-01-01", None, 109),  # expires not filled in: as 0
}
AGING = ("now", "last", "old", "next")  # the ones that go out of date and have a year: each by its own age


def _notices(db: Tiny) -> None:
    for doc_id, (ident, expires, valid_ay) in DOCS.items():
        db.doc(doc_id, "停修公告", "受理停修申請", ident=ident)
        valid(db, doc_id, expires, valid_ay)


def _named(db: Tiny) -> None:
    """Notices: one whose title names the 114 academic year (period_ay), one dated in it, one dated this year."""
    db.doc("named", "114學年度第2學期停修公告", "受理停修申請", ident="2026-03-01", ay=114, sem=2)
    db.doc("dated", "停修公告", "受理停修申請", ident="2026-03-01")
    db.doc("fresh", "停修通知", "受理停修申請", ident="2026-10-01")
    valid(db, "named", 1, 114)
    valid(db, "dated", 1, 114)
    valid(db, "fresh", 1, 115)


def test_the_default_is_the_owners():
    # going out of date counts by default: 0.85 a year
    assert HybridConfig().valid_decay == 0.85


def test_validity_reads_the_two_columns(db):
    _notices(db)
    got = validity(db.conn, list(DOCS), TODAY, D)
    assert got == pytest.approx({d: aged(DOCS[d][0]) for d in AGING})
    assert D ** 4 < got["old"] < D ** 3 < got["last"] < D < got["now"] < got["next"] < 1.0
    assert validity(db.conn, [], TODAY, D) == {} and validity(db.conn, ["nope"], TODAY, D) == {}


def test_a_title_that_names_its_year_steps_and_any_other_falls_by_its_age(db):
    _named(db)
    ids = ["named", "dated", "fresh"]
    got = validity(db.conn, ids, TODAY, D)
    assert got == pytest.approx({"named": D, "dated": aged("2026-03-01"), "fresh": aged("2026-10-01")})
    assert D < got["dated"] < got["fresh"] < 1.0  # eight months, one month
    # nothing drops on 1 August for a document that only has its date
    before = validity(db.conn, ["dated"], date(2026, 7, 31), D)["dated"]
    after = validity(db.conn, ["dated"], date(2026, 8, 1), D)["dated"]
    assert before - after < 0.001


def test_for_a_title_that_names_its_year_the_academic_year_turns_in_august(db):
    _named(db)
    assert validity(db.conn, ["named"], date(2026, 7, 31), D) == {}  # still its own year
    assert validity(db.conn, ["named"], date(2026, 8, 1), D) == {"named": D}
    assert validity(db.conn, ["named"], date(2027, 1, 15), D) == {"named": D}
    assert validity(db.conn, ["named"], date(2027, 8, 1), D) == pytest.approx({"named": D * D})


def test_a_merged_document_ages_by_its_latest_copy(db):
    _named(db)
    db.conn.execute("UPDATE docs SET rank_date = '2026-09-01' WHERE doc_id = 'dated'")  # the schema has the column
    got = validity(db.conn, ["dated", "fresh"], TODAY, D)
    assert got == pytest.approx({"dated": aged("2026-09-01"), "fresh": aged("2026-10-01")})


def test_off_the_date_bonus_is_added_as_before(db):
    _notices(db)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=RERANK)
    assert _scores(r) == pytest.approx({d: 1.5 + recency(DOCS[d][0]) for d in DOCS})
    assert _ids(r)[0] == "next" and _ids(r).index("rule") > _ids(r).index("last")  # newest first


def test_the_bonus_stays_and_the_whole_is_multiplied(db):
    _notices(db)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    with_bonus = {d: 1.5 + recency(DOCS[d][0]) for d in DOCS}
    expected = with_bonus | {d: with_bonus[d] * aged(DOCS[d][0]) for d in AGING}
    assert _scores(r) == pytest.approx(expected) and r.best_rerank == 0.5
    # among the documents that do not go out of date the bonus orders as before: the undated one above the 2022 rule
    assert _hit(r, "undated").score > _hit(r, "rule").score
    assert _ids(r)[-1] == "old"


def test_the_search_steps_a_title_that_names_its_year(db):
    _named(db)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    assert _scores(r) == pytest.approx({"named": (1.5 + recency("2026-03-01")) * D,
                                        "dated": (1.5 + recency("2026-03-01")) * aged("2026-03-01"),
                                        "fresh": (1.5 + recency("2026-10-01")) * aged("2026-10-01")})


def test_a_term_named_no_decay(db):
    _notices(db)
    for q in ("115-1 停修", "114學年度 停修", "上學期 停修"):
        on = db.hybrid(q, rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
        assert on.hits == db.hybrid(q, rank=RANK, reranker=FakeReranker(SAME), config=RERANK).hits, q
        assert set(_scores(on).values()) == {1.5}, q


def test_a_cohort_is_no_term_and_its_bonus_is_inside(db):
    # (w_rerank * relevance + the cohort bonus + the date bonus) * valid
    _notices(db)
    db.applies("old", cohort_from=111)
    r = db.hybrid("113學年度入學 停修", rank=RANK, reranker=FakeReranker(SAME), config=DECAY)
    assert _hit(r, "old").score == pytest.approx((1.5 + W_COHORT + recency("2023-10-01")) * aged("2023-10-01"))
    assert _hit(r, "last").score == pytest.approx((1.5 + recency("2025-10-01")) * aged("2025-10-01"))
    assert _hit(r, "rule").score == pytest.approx(1.5 + recency("2022-05-01"))  # it does not go out of date


def test_the_score_follows_the_day_it_is_asked(db):
    db.doc("d1", "114學年度第2學期停修公告", "受理停修申請", ident="2026-06-01", ay=114, sem=2)
    valid(db, "d1", 1, 114)

    def score(today: date) -> float:
        r = hybrid_search(db.conn, "停修", today=today, aliases=[], rank=RANK, reranker=FakeReranker(SAME),
                          config=DECAY)
        return r.hits[0].score

    def bonus(today: date) -> float:
        return 1.5 + recency("2026-06-01", today)

    assert score(date(2026, 7, 31)) == pytest.approx(bonus(date(2026, 7, 31)))
    assert score(date(2026, 8, 1)) == pytest.approx(bonus(date(2026, 8, 1)) * D)
    assert score(date(2027, 8, 1)) == pytest.approx(bonus(date(2027, 8, 1)) * D * D)


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
    # the decay stands where the date bonus stands (pool.recent): RankConfig(recency=False) asks for neither
    _notices(db)
    on = db.hybrid("停修", rank=BASE, reranker=FakeReranker(SAME), config=DECAY)
    assert on.hits == db.hybrid("停修", rank=BASE, reranker=FakeReranker(SAME), config=RERANK).hits
    assert set(_scores(on).values()) == {1.5}


def test_only_the_reranked_head_is_multiplied(db):
    # one place in the head: the other documents keep their keyword scores, and the head is the same document with
    # the decay or without
    _notices(db)
    keyword = _scores(db.search("停修", rank=RANK))
    assert keyword["next"] - keyword["undated"] == pytest.approx(recency("2026-10-30") - recency(None))
    one = {"dense": False, "rerank_docs": 1, "scope_factor": 1.0}
    off = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=HybridConfig(**one, valid_decay=1.0))
    on = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=HybridConfig(**one, valid_decay=D))
    assert _ids(on) == _ids(off) and _ids(on)[0] == "next"
    assert {d: s for d, s in _scores(on).items() if d != "next"} == {d: keyword[d] for d in DOCS if d != "next"}
    assert _hit(off, "next").score == pytest.approx(1.5 + recency("2026-10-30"))
    assert _hit(on, "next").score == pytest.approx((1.5 + recency("2026-10-30")) * aged("2026-10-30"))
    kw = HybridConfig(dense=False, rerank=False, valid_decay=D, scope_factor=1.0)  # no rerank: nothing to multiply
    assert db.hybrid("停修", rank=RANK, config=kw).hits == db.search("停修", rank=RANK).hits


def test_useful_is_on_topic_times_still_valid_times_right_audience(db):
    # direction 第一节, the three parts together: (w_rerank * relevance + adjustments) * valid * scope
    db.units()
    days = {"d1": "2026-10-01", "d2": "2024-10-01", "d3": "2024-10-01"}
    for doc_id, n, applies in (("d1", 3, ["化學系"]), ("d2", 2, None), ("d3", 1, ["化學系"])):
        db.doc(doc_id, "公告", "停修 " * n, ident=days[doc_id])
        valid(db, doc_id, 1, {"d1": 115, "d2": 113, "d3": 113}[doc_id])
        if applies:
            db.scope(doc_id, applies, publisher=applies[0])  # the department's own (T7 reads the publisher)
    assert _ids(db.search("停修", rank=RANK)) == ["d1", "d2", "d3"]
    config = HybridConfig(dense=False, valid_decay=D, scope_factor=0.8)
    r = db.hybrid("停修", rank=RANK, reranker=FakeReranker(SAME), config=config)
    assert _scores(r) == pytest.approx({d: (1.5 + recency(day)) * aged(day) * (1.0 if d == "d2" else 0.8)
                                        for d, day in days.items()})


def test_values_outside_zero_to_one_are_refused():
    for bad in (1.5, -0.2):
        with pytest.raises(ValueError):
            HybridConfig(valid_decay=bad)
