"""Plan addendum M (owner 11-08, 「6 件事全部照你的建议定」 on the project review): the keyword pool keeps each
document's best PER_DOC chunks and the best POOL_DOCS documents (M1); archive members, journals and exams are
background, placed after the first TOP_N - BACKGROUND_SLOTS primary documents (M2); the rerank head takes primary and
background documents apart (M3); the response marks background results (M2)."""

from __future__ import annotations

import dataclasses

import pytest

from nknu.content.schema import BACKGROUND, PRIMARY
from nknu.retrieval import search, vectors
from nknu.retrieval.hybrid import HybridConfig
from nknu.retrieval.respond import NOTE_BACKGROUND, build_response
from nknu.retrieval.search import BACKGROUND_SLOTS, PER_DOC, TOP_N, RankConfig, background_ids, keyword_pool
from tests.retrieval import test_respond
from tests.retrieval.test_hybrid import FakeReranker, Tiny
from tests.retrieval.test_search import BASE, TODAY

TIER = RankConfig(recency=False)  # the tier on, the date part off: scores are keyword or rerank relevance only
K = TOP_N - BACKGROUND_SLOTS  # 8


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _ids(result):
    return [h.doc_id for h in result.hits]


def member(db, doc_id, title, *chunks, url=None):
    """An archive member as the build writes one: an attachment at <archive url>#<its path in the archive>, docs.tier
    background (plan 2026-11-08 T1: search reads the column; the doc_ids here keep the shape T7a gave them before)."""
    ids = db.doc(doc_id, title, *chunks, kind="attachment", tier=BACKGROUND)
    db.conn.execute("UPDATE docs SET url = ? WHERE doc_id = ?",
                    (url or f"https://example.test/a.zip#{doc_id}.pdf", doc_id))
    return ids


# ---------------------------------------------------------------- M1: the pool

def test_pool_keeps_the_best_chunks_of_each_document(db):
    ids = db.doc("dlong", "選課規定", "停修 停修 停修", *["停修 其他 說明 文字"] * 9)
    db.doc("dshort", "停修公告", "停修申請")
    pool = keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=BASE)
    assert len(pool.docs["dlong"].chunks) == PER_DOC == 3 and len(pool.docs["dshort"].chunks) == 1
    assert pool.rows == PER_DOC + 1 and pool.docs["dlong"].chunk_ids[0] == ids[0]  # its best chunk is kept, first


def test_a_long_document_no_longer_fills_the_pool(db):
    # before 11-08 the pool was the best 300 chunks: these 400 strong chunks of one document took every place
    db.doc("dlong", "停修規定彙編", *["停修 停修"] * 400)
    db.doc("dshort", "公告", "受理停修申請")
    pool = keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=BASE)
    assert set(pool.docs) == {"dlong", "dshort"} and pool.rows == PER_DOC + 1


def test_pool_keeps_the_best_documents(db, monkeypatch):
    monkeypatch.setattr(search, "POOL_DOCS", 2)
    db.doc("d1", "公告", "停修 停修 停修")
    db.doc("d2", "公告", "停修 停修")
    db.doc("d3", "公告", "停修 另有 許多 其他 不相干 的 說明 文字 與 規定 條文 內容")
    pool = keyword_pool(db.conn, "停修", today=TODAY, aliases=[], rank=BASE)
    assert set(pool.docs) == {"d1", "d2"}


# ---------------------------------------------------------------- M2: the tier

def _what_counts(db) -> list[str]:
    db.doc("da1", "附件", "停修", kind="attachment")  # an attachment at its own address
    member(db, "da2m1", "附件", "停修")
    db.doc("je", "考古題", "停修", doc_type="exam")
    db.doc("jj", "學報", "停修", doc_type="journal")
    db.doc("pn", "公告", "停修")
    # a file an SSO download button brought: exported at <list url>#按钮=…, a # but no archive member (review 11-08)
    db.doc("da3", "申請表", "停修", kind="attachment")
    db.conn.execute("UPDATE docs SET url = 'https://example.test/list.aspx#按钮=申請表' WHERE doc_id = 'da3'")
    # a member of an archive that a button brought: background however its address reads
    member(db, "da4m2", "會議紀錄.pdf", "停修", url="https://example.test/list.aspx#按钮=x#會議紀錄.pdf")
    return ["da1", "da2m1", "je", "jj", "pn", "da3", "da4m2", "nope"]


def test_what_counts_as_background(db):
    ids = _what_counts(db)
    assert background_ids(db.conn, ids) == {"da2m1", "je", "jj", "da4m2"}
    # changed on purpose (plan 2026-11-08 T1): the tier is the build's column, read as it is, whatever the doc_id's
    # shape or the doc_type say; until then search guessed it from them
    db.conn.execute("UPDATE docs SET tier = ? WHERE doc_id = 'jj'", (PRIMARY,))
    db.conn.execute("UPDATE docs SET tier = ? WHERE doc_id = 'pn'", (BACKGROUND,))
    assert background_ids(db.conn, ids) == {"da2m1", "je", "pn", "da4m2"}


def test_a_database_without_the_tier_column_keeps_the_rule_of_11_08(db):
    # the skeleton's and the 11-04 builds have no docs.tier: an archive member by its doc_id da<序号>m<k>, journals and
    # exams by doc_type (addendum M2 as it was on 11-08)
    ids = _what_counts(db)
    db.conn.execute("UPDATE docs SET tier = ? WHERE doc_id = 'pn'", (BACKGROUND,))  # gone with the column below
    db.conn.execute("ALTER TABLE docs DROP COLUMN tier")
    assert background_ids(db.conn, ids) == {"da2m1", "je", "jj", "da4m2"}


def _nine_and_three(db):
    """Nine primary notices alike, and three background documents that match better (停修 three times; no title of
    them names it, since a title counts three times)."""
    for i in range(9):
        db.doc(f"p{i}", f"公告{i}", "受理停修申請")
    member(db, "da1m1", "會議紀錄一", "停修 停修 停修")
    member(db, "da2m1", "會議紀錄二", "停修 停修 停修")
    db.doc("j1", "學報", "停修 停修 停修", doc_type="journal")


def test_background_comes_after_the_first_primary_documents(db):
    _nine_and_three(db)
    assert _ids(db.search("停修", rank=BASE, limit=20))[:3] == ["j1", "da1m1", "da2m1"]  # without the tier: first
    r = db.search("停修", rank=TIER, limit=20)
    assert _ids(r) == [f"p{i}" for i in range(K)] + ["j1", "da1m1"] + ["p8", "da2m1"]
    assert [h.background for h in r.hits] == [False] * K + [True, True, False, True]
    # the order does not depend on limit, so offline ranks are what the server shows
    assert _ids(db.search("停修", rank=TIER, limit=TOP_N)) == _ids(r)[:TOP_N]
    # the tier moves documents, it never changes their scores
    assert {h.doc_id: h.score for h in r.hits} == {h.doc_id: h.score for h in db.search("停修", rank=BASE,
                                                                                          limit=20).hits}


def test_few_primary_documents_then_all_background(db):
    db.doc("p1", "停修公告", "受理停修申請")
    member(db, "da1m1", "會議紀錄一", "停修 停修 停修")
    member(db, "da2m1", "會議紀錄二", "停修 停修 停修")
    member(db, "da3m1", "會議紀錄三", "停修 停修 停修")
    assert _ids(db.search("停修", rank=TIER)) == ["p1", "da1m1", "da2m1", "da3m1"]


def test_keyword_only_hybrid_uses_the_tier(db):
    _nine_and_three(db)
    off = HybridConfig(dense=False, rerank=False)
    assert _ids(db.hybrid("停修", rank=TIER, limit=20, config=off)) == _ids(db.search("停修", rank=TIER, limit=20))


# ---------------------------------------------------------------- M3: the rerank head

def test_rerank_head_takes_each_tier_apart(db):
    for i in range(60):
        db.doc(f"p{i:02}", f"停修公告{i:02}", "受理停修申請")
    for i in range(15):
        member(db, f"da{100 + i}m1", f"停修會議紀錄{i:02}", "停修 停修 停修")
    rr = FakeReranker({"停修會議紀錄07": 0.99, "停修公告41": 0.9, "停修會議紀錄12": 1.0})
    r = db.hybrid("停修", limit=75, reranker=rr, config=HybridConfig(dense=False), rank=TIER)
    ((_, passages),) = rr.calls
    titles = {p.split("\n")[0] for p in passages}
    assert len([t for t in titles if "公告" in t]) == 50 and len([t for t in titles if "會議紀錄" in t]) == 10
    # 會議紀錄12 is not among the first 10 background documents: never scored; p41's 0.9 outranks the other primary ones
    assert _ids(r)[:TOP_N] == ["p41", "p00", "p01", "p02", "p03", "p04", "p05", "p06", "da107m1", "da100m1"]
    assert r.hits[0].score == pytest.approx(3 * 0.9) and r.best_rerank == pytest.approx(0.99)


# ---------------------------------------------------------------- M2: the response

def test_response_marks_background_results(tmp_path):
    t = test_respond.Tiny(tmp_path / "aca.db")
    try:
        main = test_respond.hit("p1", t.doc("p1", "停修申請受理期間至十一月中旬。", title="停修公告"))
        back = dataclasses.replace(test_respond.hit("m1", t.doc("m1", "會議決議停修規定修正。", title="會議紀錄")),
                                   background=True)
        resp = build_response(t.conn, test_respond.result(main, back), today=test_respond.TODAY)
        assert [r.get("background") for r in resp["results"]] == [None, True] and NOTE_BACKGROUND in resp["notes"]
        plain = build_response(t.conn, test_respond.result(main), today=test_respond.TODAY)
        assert NOTE_BACKGROUND not in plain["notes"] and "background" not in plain["results"][0]
    finally:
        t.conn.close()
