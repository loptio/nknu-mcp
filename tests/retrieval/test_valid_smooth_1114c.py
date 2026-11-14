"""Plan 2026-11-14 T6 (main session, the same day): HybridConfig.valid_smooth.

By the plan's step a document of the academic year before today's is multiplied by valid_decay from 1 August on,
whether it is of last September or of last July. With the setting on, the discount falls evenly from the day the
document's academic year ended: valid_decay ** (days since that 31 July / 365), so a document keeps its full weight
to the end of its own academic year and is at valid_decay one year later. Off by default: the scores of
test_valid_1114 stand. No model loads; the sentences are made up."""

from __future__ import annotations

from datetime import date

import pytest

from nknu.retrieval.hybrid import HybridConfig, validity
from tests.retrieval import test_valid_1114 as tv
from tests.retrieval.test_hybrid import FakeReranker
from tests.retrieval.test_search import TODAY

db = tv.db  # the fixture
D = tv.D
SMOOTH = HybridConfig(dense=False, valid_decay=D, **(tv.FIRST | {"valid_smooth": True}))


def share(ay: int, today: date = TODAY) -> float:
    return D ** ((today - date(1912 + ay, 7, 31)).days / 365)


def test_on_by_default_since_the_owners_word():
    assert HybridConfig().valid_smooth is True  # owner 11-14: 「还有效开（0.85 定稿算法）」


def test_evenly_from_the_end_of_the_documents_academic_year(db):
    tv._notices(db)
    got = validity(db.conn, list(tv.DOCS), TODAY, D, smooth=True)
    assert got == pytest.approx({"last": share(114), "old": share(112)})  # the same documents as the step's
    assert D < got["last"] < 1.0  # three months after its year ended: well above a whole step
    assert validity(db.conn, list(tv.DOCS), TODAY, D) == pytest.approx({"last": D, "old": D ** 3})  # the step stands


def test_nothing_drops_on_the_first_of_august(db):
    tv._notices(db)
    before = validity(db.conn, list(tv.DOCS), date(2027, 7, 31), D, smooth=True)
    after = validity(db.conn, list(tv.DOCS), date(2027, 8, 1), D, smooth=True)
    assert "now" not in before and after["now"] == pytest.approx(D ** (1 / 365))  # one day past its year: a hair
    assert before["last"] == pytest.approx(D) and after["last"] == pytest.approx(D ** (366 / 365))
    # by the step the same night costs a whole share
    assert validity(db.conn, list(tv.DOCS), date(2027, 8, 1), D)["now"] == pytest.approx(D)


def test_one_year_after_its_year_ended_it_is_at_the_plans_number(db):
    tv._notices(db)
    assert validity(db.conn, ["last"], date(2027, 7, 31), D, smooth=True) == pytest.approx({"last": D})
    assert validity(db.conn, ["last"], date(2028, 7, 30), D, smooth=True) == pytest.approx({"last": D ** 2})


def test_the_search_multiplies_by_it(db):
    tv._notices(db)
    r = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=SMOOTH)
    expected = {d: 1.5 for d in tv.DOCS} | {"last": 1.5 * share(114), "old": 1.5 * share(112)}
    assert tv._scores(r) == pytest.approx(expected)


def test_with_the_date_bonus_kept(db):
    tv._notices(db)
    config = HybridConfig(dense=False, valid_decay=D, **(tv.FIRST | {"valid_smooth": True, "valid_keeps_recency": True}))
    r = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=config)
    with_bonus = {d: 1.5 + tv.recency(tv.DOCS[d][0]) for d in tv.DOCS}
    expected = with_bonus | {"last": with_bonus["last"] * share(114), "old": with_bonus["old"] * share(112)}
    assert tv._scores(r) == pytest.approx(expected)


def test_without_valid_decay_the_setting_changes_nothing(db):
    tv._notices(db)
    plain = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=tv.RERANK)
    on = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME),
                   config=HybridConfig(dense=False, valid_decay=1.0, valid_smooth=True, scope_factor=1.0))
    assert tv._scores(plain) == tv._scores(on)


# ---------------------------------------------------------------- valid_named_step (late 11-14)

def aged(day: str, today: date = TODAY) -> float:
    return D ** ((today - date.fromisoformat(day)).days / 365)


def _named(db) -> None:
    """Notices: one whose title names the 114 academic year (period_ay), one dated in it, one dated this year."""
    db.doc("named", "114學年度第2學期停修公告", "受理停修申請", ident="2026-03-01", ay=114, sem=2)
    db.doc("dated", "停修公告", "受理停修申請", ident="2026-03-01")
    db.doc("fresh", "停修通知", "受理停修申請", ident="2026-10-01")
    tv.valid(db, "named", 1, 114)
    tv.valid(db, "dated", 1, 114)
    tv.valid(db, "fresh", 1, 115)


def test_named_step_on_by_default_since_the_owners_word():
    assert HybridConfig().valid_named_step is True  # owner 11-14: 「还有效开（0.85 定稿算法）」


def test_a_title_that_names_its_year_steps_and_any_other_falls_by_its_age(db):
    _named(db)
    ids = ["named", "dated", "fresh"]
    even = validity(db.conn, ids, TODAY, D, smooth=True)
    assert even == pytest.approx({"named": share(114), "dated": share(114)})  # from the end of the academic year
    mixed = validity(db.conn, ids, TODAY, D, smooth=True, named_step=True)
    assert mixed == pytest.approx({"named": D, "dated": aged("2026-03-01"), "fresh": aged("2026-10-01")})
    assert D < mixed["dated"] < mixed["fresh"] < 1.0  # eight months, one month
    # nothing drops on 1 August for a document that only has its date
    before = validity(db.conn, ["dated"], date(2026, 7, 31), D, smooth=True, named_step=True)["dated"]
    after = validity(db.conn, ["dated"], date(2026, 8, 1), D, smooth=True, named_step=True)["dated"]
    assert before - after < 0.001
    # without smooth every document steps, whatever named_step says
    assert validity(db.conn, ids, TODAY, D, named_step=True) == pytest.approx({"named": D, "dated": D})


def test_a_merged_document_ages_by_its_latest_copy(db):
    _named(db)
    db.conn.execute("UPDATE docs SET rank_date = '2026-09-01' WHERE doc_id = 'dated'")  # the schema has the column
    got = validity(db.conn, ["dated", "fresh"], TODAY, D, smooth=True, named_step=True)
    assert got == pytest.approx({"dated": aged("2026-09-01"), "fresh": aged("2026-10-01")})


def test_the_search_takes_named_step_from_the_config(db):
    _named(db)
    config = HybridConfig(dense=False, valid_decay=D,
                          **(tv.FIRST | {"valid_smooth": True, "valid_named_step": True}))
    r = db.hybrid("停修", rank=tv.RANK, reranker=FakeReranker(tv.SAME), config=config)
    assert tv._scores(r) == pytest.approx({"named": 1.5 * D, "dated": 1.5 * aged("2026-03-01"),
                                           "fresh": 1.5 * aged("2026-10-01")})
