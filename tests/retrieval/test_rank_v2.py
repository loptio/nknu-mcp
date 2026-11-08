"""Ranking v2 (spec 2026-11-01 「排序 v2」): newer first when the query names no term; journal and exam documents a
little lower until 11-08, after the primary documents since (the tier, addendum M2; test_tier_1108.py has the rest).
RankConfig(recency=False, tier=False) ranks as before: test_search.py pins that ranking, and
test_both_off_ignores_dates_and_types shows the new inputs cannot reach it."""

import math
import sqlite3

import pytest

from nknu.retrieval.search import W_COVERAGE, RankConfig, search_docs
from tests.retrieval.test_search import BASE, TODAY, Tiny

# the spec's values, written out so these tests do not move when the defaults are tuned
R = RankConfig(recency=True, w_recent=0.6, tau_days=365, no_date_factor=0.5, tier=True)
NO_RECENCY = RankConfig(recency=False, tier=True)
OLD = 0.6 * math.exp(-2557 / 365)  # 2019-11-01 is 2557 days before 2026-11-01
YEAR = 0.6 * math.exp(-1)  # 365 days before


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _ids(result):
    return [h.doc_id for h in result.hits]


def snapshot(result):
    return [(h.doc_id, h.score, h.coverage, h.bm25, h.chunk_ids) for h in result.hits]


def score(db, q, doc_id, rank):
    return next(h.score for h in db.search(q, rank=rank).hits if h.doc_id == doc_id)


def bonus(db, q, doc_id, rank=R):
    """What rank adds to a document's score: its score with rank minus its score with both parts off."""
    return score(db, q, doc_id, rank) - score(db, q, doc_id, BASE)


def _old_and_new(db):
    db.doc("dold", "線上申請停修", "停修 停修 停修，申請停修", ident="2019-11-01")  # matches better
    db.doc("dnew", "線上申請停修", "停修以一科為限", ident="2026-10-21")


def test_newer_first_only_when_no_term_is_named(db):
    _old_and_new(db)
    assert _ids(db.search("停修")) == ["dold", "dnew"]  # before v2: the better match first
    assert _ids(db.search("停修", rank=R)) == ["dnew", "dold"]
    assert bonus(db, "停修", "dnew") == pytest.approx(0.6 * math.exp(-11 / 365))
    assert bonus(db, "停修", "dold") == pytest.approx(OLD)
    # a semester, a year or a relative term named: the query says which period it wants, so no date bonus
    for q in ("115-1 停修", "115學年度 停修", "這學期 停修", "上學期 停修"):
        assert _ids(db.search(q, rank=R)) == ["dold", "dnew"], q
        assert bonus(db, q, "dnew") == 0.0 and bonus(db, q, "dold") == 0.0, q
    # an entry cohort says whom a rule applies to, not which period: newer first still
    assert _ids(db.search("113學年度入學 停修", rank=R)) == ["dnew", "dold"]


def test_yearly_versions_without_a_term_get_recency(db):
    # owner 11-03: no series folds an older version away, so when the query names no term, ranking v2's date part (the
    # default RankConfig) is what puts the newer of two versions alike in words first; a term named turns it off
    db.doc("d112", "112學年度第一學期線上申請停修", "受理期間另行公告。申請停修請上網", ay=112, sem=1, ident="2023-10-20")
    db.doc("d115", "115學年度第一學期線上申請停修", "受理期間另行公告", ay=115, sem=1, ident="2026-10-21")
    assert _ids(db.search("停修")) == ["d112", "d115"]  # before v2: the better match first
    assert _ids(db.search("停修", rank=RankConfig())) == ["d115", "d112"]
    assert bonus(db, "停修", "d115", RankConfig()) == pytest.approx(0.3 * math.exp(-11 / 365))
    assert bonus(db, "停修", "d112", RankConfig()) == pytest.approx(0.3 * math.exp(-1108 / 365))
    assert bonus(db, "112-1 停修", "d115", RankConfig()) == 0.0
    assert _ids(db.search("112-1 停修", rank=RankConfig())) == ["d112", "d115"]


def test_recency_decays_from_today(db):
    for doc_id, ident in (("dtoday", "2026-11-01"), ("dyear", "2025-11-01"), ("dlater", "2026-12-01"),
                          ("dmonth", "2026-10-02")):
        db.doc(doc_id, "線上申請停修", "停修以一科為限", ident=ident)
    assert bonus(db, "停修", "dtoday") == pytest.approx(0.6)
    assert bonus(db, "停修", "dyear") == pytest.approx(YEAR)
    assert bonus(db, "停修", "dlater") == pytest.approx(0.6)  # a day after today counts as today, never more
    fast = RankConfig(w_recent=1.0, tau_days=30)
    assert bonus(db, "停修", "dmonth", fast) == pytest.approx(math.exp(-1))
    assert bonus(db, "停修", "dtoday", fast) == pytest.approx(1.0)


def test_event_counts_by_its_last_day(db):
    # a calendar event has no identity date: its last day decides, and one still to come counts in full
    for doc_id, start, end in (("ecoming", "2026-10-20", "2026-11-16"), ("etoday", "2026-10-28", "2026-11-01"),
                               ("epast", "2025-10-25", "2025-11-01")):
        db.doc(doc_id, "申辦停修", "申辦停修", kind="event")
        db.date(doc_id, "event_start", start)
        db.date(doc_id, "event_end", end)
    assert bonus(db, "停修", "ecoming") == pytest.approx(0.6)
    assert bonus(db, "停修", "etoday") == pytest.approx(0.6)
    assert bonus(db, "停修", "epast") == pytest.approx(YEAR)


def test_no_date_gets_its_share(db):
    db.doc("dnone", "停修申請表", "停修申請表 學號 科目")
    db.doc("dperiod", "線上申請停修", "受理期間至115年12月1日止", ident="2019-11-01")
    db.date("dperiod", "period_end", "2026-12-01", "正文期間")  # a period in the body never dates the document
    assert bonus(db, "停修", "dnone") == pytest.approx(0.3)
    assert bonus(db, "停修", "dnone", RankConfig(w_recent=0.6, no_date_factor=0.25)) == pytest.approx(0.15)
    assert bonus(db, "停修", "dperiod") == pytest.approx(OLD)


def test_journal_and_exam_come_after_primary_documents(db):
    # was test_journal_and_exam_a_little_lower (w_journal, w_exam): since 11-08 they are background (addendum M2), which
    # moves them in the order and leaves their scores alone
    for doc_id, doc_type in (("dplain", ""), ("djournal", "journal"), ("dexam", "exam")):
        db.doc(doc_id, "線上申請停修", "停修以一科為限", ident="2026-10-21", doc_type=doc_type)
    assert _ids(db.search("停修")) == ["dexam", "djournal", "dplain"]  # before v2: a tie, by doc_id
    assert _ids(db.search("停修", rank=NO_RECENCY)) == ["dplain", "dexam", "djournal"]
    assert [h.background for h in db.search("停修", rank=NO_RECENCY).hits] == [False, True, True]
    for doc_id in ("dplain", "djournal", "dexam"):
        assert bonus(db, "停修", doc_id, NO_RECENCY) == 0.0
    # with the date part: only the date adds, and a term in the query turns it off
    assert bonus(db, "停修", "djournal") == pytest.approx(0.6 * math.exp(-11 / 365))
    assert bonus(db, "115-1 停修", "dexam") == 0.0


def _mixed(db):
    db.doc("d1", "115學年度第一學期線上申請停修", "受理期間：115年11月3日起至11月16日止，停修以一科為限", ay=115, sem=1,
           ident="2026-10-21")
    db.doc("d0", "114學年度第一學期線上申請停修", "受理期間：114年11月4日起至11月17日止", ay=114, sem=1, ident="2025-10-20")
    db.doc("d2", "線上申請停修", "停修 停修 停修，申請停修", ident="2019-11-01")
    db.doc("d3", "停修申請表", "停修申請表 學號 科目")
    db.doc("e1", "申辦停修", "申辦停修", kind="event")
    db.date("e1", "event_start", "2026-11-03")
    db.date("e1", "event_end", "2026-11-16")
    db.doc("j1", "學報論文", "停修制度之研究：停修與學習成效", ident="2023-01-01", doc_type="journal")
    db.doc("x1", "104a.pdf", "停修 試題", ident="2021-09-09", doc_type="exam")


QUERIES = ("停修", "115-1 停修", "申請停修", "停修申請表 學號", "114學年度 停修")


def test_both_off_ignores_dates_and_types(db):
    # with both parts off nothing new is read: clearing the types and the events' last days changes no hit, score,
    # coverage or chunk, so the ranking is the one test_search.py pins
    _mixed(db)
    before = {q: snapshot(db.search(q, rank=BASE)) for q in QUERIES}
    assert snapshot(db.search("停修", rank=R)) != before["停修"]  # the fixture does reach v2
    db.conn.execute("UPDATE docs SET doc_type = ''")
    db.conn.execute("DELETE FROM dates WHERE kind = 'event_end'")
    assert {q: snapshot(db.search(q, rank=BASE)) for q in QUERIES} == before


def test_default_weights_are_the_ones_chosen_offline(db):
    # research/eval_2026-11-01/ranking_v2.md: of the settings with the most top-3 hits and the fewest questions ranked
    # lower, the smallest weights; the whole date bonus stays below one missing word for queries of up to 6 words
    assert RankConfig() == RankConfig(recency=True, w_recent=0.3, tau_days=365, no_date_factor=0.5, tier=True)
    for doc_id, doc_type in (("dtoday", ""), ("djournal", "journal"), ("dexam", "exam")):
        db.doc(doc_id, "線上申請停修", "停修以一科為限", ident="2026-11-01", doc_type=doc_type)
    date_bonus = bonus(db, "停修", "dtoday", RankConfig())
    assert date_bonus == pytest.approx(0.3) and date_bonus < W_COVERAGE / 6
    assert bonus(db, "停修", "djournal", RankConfig()) == pytest.approx(0.3)  # the date; the tier moves, never scores
    assert bonus(db, "115-1 停修", "dexam", RankConfig()) == 0.0


def test_default_is_rank_config(db):
    _mixed(db)
    for q in QUERIES:
        default = search_docs(db.conn, q, today=TODAY, aliases=[])
        assert snapshot(default) == snapshot(db.search(q, rank=RankConfig())), q
    assert snapshot(search_docs(db.conn, "停修", today=TODAY, aliases=[])) != snapshot(db.search("停修", rank=BASE))


def test_read_only_connection_with_row_factory(tmp_path):
    t = Tiny(tmp_path / "t.db")
    _mixed(t)
    t.conn.commit()
    expected = {q: snapshot(t.search(q, rank=R)) for q in QUERIES}
    t.conn.close()
    ro = sqlite3.connect(f"file:{tmp_path / 't.db'}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        for q in QUERIES:
            assert snapshot(search_docs(ro, q, today=TODAY, aliases=[], rank=R)) == expected[q], q
    finally:
        ro.close()
