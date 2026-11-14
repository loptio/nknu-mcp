"""Plan 2026-11-14 T2, completed the same day (the search's part): RankConfig.copy_dates, on by default.

A document the build merged copies into is the earliest of them, and docs.ident_date is its own day. Where the search
reads a date to rank by (ranking v2's date part, in the keyword score and in the reranked one, and the tie between
equal scores) it now reads docs.rank_date, the latest identity date among the document and its copies, so merging
ranks no document lower than before: on the 教务处 control a gold document's keyword rank had fallen from 4 to 26
(main session, 11-14). With copy_dates off, and in a database without the column, every score and order is the one of
before. What the response shows stays the document's own date: respond reads docs.ident_date itself. The sentences
are made up; fake models only."""

from __future__ import annotations

import dataclasses
import math
from datetime import date

import pytest

from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import RankConfig, adjustments, doc_rows, keyword_pool, sort_docs
from tests.retrieval import test_respond as trs
from tests.retrieval.test_hybrid import FakeEmbedder, FakeReranker, Tiny
from tests.retrieval.test_search import BASE, TODAY

ON = RankConfig(tier=False)  # ranking v2's date part and copy_dates on, as in the default
OFF = dataclasses.replace(ON, copy_dates=False)
SAME = {"停修": 0.5, "退選": 0.5}  # every document scores 0.5 with the reranker
OLD, NEW = "2023-03-01", "2026-10-01"  # the kept (earliest) copy's day, the latest copy's


def recency(day: str | None) -> float:
    """Ranking v2's date bonus at its defaults (search._recency)."""
    return 0.3 * (0.5 if day is None else math.exp(-max(0, (TODAY - date.fromisoformat(day)).days) / 365))


def rank_date(db: Tiny, doc_id: str, day: str | None) -> None:
    db.conn.execute("UPDATE docs SET rank_date = ? WHERE doc_id = ?", (day, doc_id))


def snapshot(result):
    return [(h.doc_id, h.score, h.coverage, h.bm25, h.chunk_ids) for h in result.hits]


def _ids(result):
    return [h.doc_id for h in result.hits]


def _scores(result):
    return {h.doc_id: h.score for h in result.hits}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _notices(db: Tiny) -> None:
    """A document merged from copies (its own day OLD, its latest copy's NEW), one of a year ago without copies, an
    undated one, and one the build gave its own date as its rank date."""
    db.doc("merged", "停修公告", "受理停修申請", ident=OLD)
    rank_date(db, "merged", NEW)
    db.doc("year", "停修公告", "受理停修申請", ident="2025-11-01")
    rank_date(db, "year", "2025-11-01")
    db.doc("none", "停修公告", "受理停修申請")
    db.doc("plain", "停修公告", "受理停修申請", ident="2024-11-01")  # rank_date NULL: a row no build wrote one for


def test_on_by_default():
    assert RankConfig().copy_dates is True and BASE.copy_dates is True


def test_a_merged_document_ranks_by_its_latest_copys_date(db):
    _notices(db)
    base = _scores(db.search("停修", rank=dataclasses.replace(ON, recency=False)))
    on, off = db.search("停修", rank=ON), db.search("停修", rank=OFF)
    assert _scores(on)["merged"] - base["merged"] == pytest.approx(recency(NEW))
    assert _scores(off)["merged"] - base["merged"] == pytest.approx(recency(OLD))
    # the four are alike in words: the date part orders them (no date counts as half of today's)
    assert _ids(on) == ["merged", "none", "year", "plain"] and _ids(off) == ["none", "year", "plain", "merged"]
    for doc_id, day in (("year", "2025-11-01"), ("none", None), ("plain", "2024-11-01")):  # the others: as before
        assert _scores(on)[doc_id] == _scores(off)[doc_id] and _scores(on)[doc_id] - base[doc_id] == pytest.approx(
            recency(day))


def test_off_and_a_database_without_the_column_rank_as_before(db, tmp_path):
    _notices(db)
    queries = ("停修", "115-1 停修", "113學年度入學 停修", "停修公告 申請")
    with_column = {(q, name): snapshot(db.search(q, rank=rank)) for q in queries for name, rank in (("on", ON), ("off", OFF))}
    with_hybrid = {name: snapshot(db.hybrid("停修", rank=rank, reranker=FakeReranker(SAME), config=HybridConfig(dense=False)))
                   for name, rank in (("on", ON), ("off", OFF))}
    db.conn.execute("ALTER TABLE docs DROP COLUMN rank_date")  # a database built before: pipeline-7, the first rebuild
    for q in queries:
        before = snapshot(db.search(q, rank=ON))
        assert before == snapshot(db.search(q, rank=OFF)) == with_column[q, "off"], q
    assert with_column["停修", "on"] != with_column["停修", "off"]
    # a term named: no date part, so the same scores, and only the tie between them reads the date
    named_on, named_off = with_column["115-1 停修", "on"], with_column["115-1 停修", "off"]
    assert sorted(named_on) == sorted(named_off) and len({score for _, score, *_ in named_on}) == 1
    assert [doc_id for doc_id, *_ in named_on] == ["merged", "year", "plain", "none"]
    assert [doc_id for doc_id, *_ in named_off] == ["year", "plain", "merged", "none"]
    before = snapshot(db.hybrid("停修", rank=ON, reranker=FakeReranker(SAME), config=HybridConfig(dense=False)))
    assert before == with_hybrid["off"] != with_hybrid["on"]
    assert before == snapshot(db.hybrid("停修", rank=OFF, reranker=FakeReranker(SAME), config=HybridConfig(dense=False)))


def test_a_copys_date_never_ranks_a_document_lower_than_its_own(db):
    # an undated document whose only dated copy is old: no date gives more than that date, and it keeps that share
    db.doc("kept", "停修公告", "受理停修申請")
    rank_date(db, "kept", "2019-11-01")
    db.doc("fresh", "停修公告", "受理停修申請")  # undated too, its copy of last month
    rank_date(db, "fresh", "2026-10-01")
    db.doc("odd", "停修公告", "受理停修申請", ident="2026-06-01")  # a rank date before its own: its own counts
    rank_date(db, "odd", "2020-01-01")
    base = _scores(db.search("停修", rank=dataclasses.replace(ON, recency=False)))
    on, off = _scores(db.search("停修", rank=ON)), _scores(db.search("停修", rank=OFF))
    assert on["kept"] == off["kept"] and on["kept"] - base["kept"] == pytest.approx(recency(None))
    assert on["fresh"] - base["fresh"] == pytest.approx(recency("2026-10-01")) and on["fresh"] > off["fresh"]
    assert on["odd"] == off["odd"] and on["odd"] - base["odd"] == pytest.approx(recency("2026-06-01"))
    assert all(on[d] >= off[d] for d in on)


def test_a_tie_goes_to_the_newest_date_it_ranks_by(db):
    # a term named: no date part, the scores are equal and the date only orders them
    for doc_id, ident, ranks in (("a", "2024-01-01", "2026-09-01"), ("b", "2025-06-01", "2025-06-01"),
                                 ("c", None, "2025-01-01"), ("d", None, None)):
        db.doc(doc_id, "115學年度第1學期停修公告", "受理停修申請", ay=115, sem=1, ident=ident)
        rank_date(db, doc_id, ranks)
    on, off = db.search("115-1 停修", rank=ON), db.search("115-1 停修", rank=OFF)
    assert len(set(_scores(on).values())) == 1 and _scores(on) == _scores(off)
    assert _ids(on) == ["a", "b", "c", "d"]  # by the date each ranks by; the undated one last
    assert _ids(off) == ["b", "a", "c", "d"]  # by its own date, as before; no date last, then by id


def test_the_helpers_read_the_column_only_when_asked(db):
    _notices(db)
    ids = ["merged", "year", "none", "plain", "missing"]
    assert {d: (doc.ident, doc.rank_date) for d, doc in doc_rows(db.conn, ids).items()} == {
        "merged": (OLD, NEW), "year": ("2025-11-01", "2025-11-01"), "none": (None, None), "plain": ("2024-11-01", None)}
    assert {doc.rank_date for doc in doc_rows(db.conn, ids, copy_dates=False).values()} == {None}
    assert [d.doc_id for d in sort_docs(doc_rows(db.conn, ids).values())] == ["merged", "year", "plain", "none"]
    assert [d.doc_id for d in sort_docs(doc_rows(db.conn, ids, copy_dates=False).values())] == [
        "year", "plain", "merged", "none"]
    pool = keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=OFF)
    assert {d.rank_date for d in pool.docs.values()} == {None}
    assert keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=ON).docs["merged"].rank_date == NEW


def test_the_reranked_score_reads_the_same_date(db):
    _notices(db)
    config = HybridConfig(dense=False)
    on = db.hybrid("停修", rank=ON, reranker=FakeReranker(SAME), config=config)
    off = db.hybrid("停修", rank=OFF, reranker=FakeReranker(SAME), config=config)
    assert _scores(on) == pytest.approx({"merged": 1.5 + recency(NEW), "year": 1.5 + recency("2025-11-01"),
                                         "none": 1.5 + recency(None), "plain": 1.5 + recency("2024-11-01")})
    assert _scores(off) == pytest.approx({**_scores(on), "merged": 1.5 + recency(OLD)})
    assert _ids(on)[0] == "merged" and _ids(off)[-1] == "merged"


def test_a_document_only_the_vectors_find_follows_the_setting_too(db):
    # hybrid reads such a document with doc_rows, which has no RankConfig: adjustments settles which date it ranks by
    db.doc("kw", "停修公告", "受理停修申請", ident="2025-11-01")
    db.doc("vec", "退選公告", "受理退選申請", ident=OLD)  # the fake embedder puts 退選 beside 停修; no keyword finds it
    rank_date(db, "vec", NEW)
    db.doc("vec2", "退選須知", "退選注意事項", ident="2024-11-01")
    rank_date(db, "vec2", "2024-11-01")
    emb = FakeEmbedder()
    db.vectors(emb)
    assert _ids(db.search("停修", rank=ON)) == ["kw"]
    on = db.hybrid("停修", rank=ON, embedder=emb, reranker=FakeReranker(SAME))
    off = db.hybrid("停修", rank=OFF, embedder=emb, reranker=FakeReranker(SAME))
    assert _scores(on) == pytest.approx({"kw": 1.5 + recency("2025-11-01"), "vec": 1.5 + recency(NEW),
                                         "vec2": 1.5 + recency("2024-11-01")})
    assert _scores(off) == pytest.approx({**_scores(on), "vec": 1.5 + recency(OLD)})
    assert _ids(on) == ["vec", "kw", "vec2"] and _ids(off) == ["kw", "vec2", "vec"]
    # a term named: the scores are equal, and the tie among the two the vectors found is settled the same way
    on = db.hybrid("115-1 停修", rank=ON, embedder=emb, reranker=FakeReranker(SAME))
    off = db.hybrid("115-1 停修", rank=OFF, embedder=emb, reranker=FakeReranker(SAME))
    assert set(_scores(on).values()) == {1.5} and _scores(on) == _scores(off)
    assert _ids(on) == ["vec", "kw", "vec2"] and _ids(off) == ["kw", "vec2", "vec"]
    db.conn.execute("ALTER TABLE docs DROP COLUMN rank_date")
    monkey = db.hybrid("115-1 停修", rank=ON, embedder=emb, reranker=FakeReranker(SAME))
    assert snapshot(monkey) == snapshot(off)  # a database without the column: as with the setting off


def test_adjustments_take_the_rank_dates_away_when_the_setting_is_off(db):
    _notices(db)
    pool = keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=ON)
    docs = doc_rows(db.conn, ["merged", "year"])  # as hybrid reads a document only the vectors found
    assert adjustments(db.conn, docs, pool, ON, TODAY) == pytest.approx({"merged": recency(NEW),
                                                                         "year": recency("2025-11-01")})
    assert docs["merged"].rank_date == NEW
    assert adjustments(db.conn, docs, pool, OFF, TODAY) == pytest.approx({"merged": recency(OLD),
                                                                          "year": recency("2025-11-01")})
    assert docs["merged"].rank_date is None and docs["merged"].ident == OLD  # the sort that follows ties by its own


def test_the_response_shows_the_documents_own_date(tmp_path):
    # respond reads docs.ident_date itself (never the date search ranked by): test_respond's database, one document
    t = trs.Tiny(tmp_path / "r.db")
    try:
        chunks = t.doc("merged", "受理停修申請，請於期限內上網辦理。", title="停修公告", ident=(OLD, "published"))
        t.date("merged", "published", OLD)
        t.conn.execute("UPDATE docs SET rank_date = ? WHERE doc_id = 'merged'", (NEW,))
        resp = build_response(t.conn, trs.result(trs.hit("merged", chunks)), today=TODAY)
    finally:
        t.conn.close()
    [shown] = resp["results"]
    assert OLD in shown["identity"] and [d["date"] for d in shown["dates"]] == [OLD]
    assert NEW not in trs.to_json(resp)
