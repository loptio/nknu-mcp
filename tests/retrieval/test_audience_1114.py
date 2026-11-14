"""Plan 2026-11-14 T7 (direction 2026-11-13 第 7 件, 「对口」): with HybridConfig.scope_factor below 1, a document
whose audience is not the asker's is multiplied by it, in its keyword score before the keyword order is taken and in
its final score after reranking. A document a college, department or programme published when the query names none
of those (main session 11-14: by its publisher, as the direction words it; as first written, by its applies_to rows,
which also took an office's page that merely names a department); a document units.relation gives -1 when the query
names a unit. Off (1.0) by default. Fake models only; the unit tree is the made-up slice test_hybrid.py uses, the
sentences are invented."""

from __future__ import annotations

import logging

import pytest

from nknu.retrieval import vectors
from nknu.retrieval.hybrid import HybridConfig, off_audience
from tests.retrieval.test_hybrid import FakeReranker, Tiny
from tests.retrieval.test_search import _hit, _ids

F = 0.8  # the plan's number 【Claude】; the default is 1.0
KEYWORD = HybridConfig(dense=False, rerank=False, scope_factor=F)
RERANK = HybridConfig(dense=False, scope_factor=F)
SCHOOL = "國立高雄師範大學"
SOFTWARE = "軟體工程與管理學系"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    t.units()
    yield t
    t.conn.close()


def _scores(result):
    return {h.doc_id: h.score for h in result.hits}


OFFICE = "教務處"
OWN = {"own", "other", "owncollege", "othercollege", "two"}  # published by a department or a college


def _rules(db: Tiny) -> list[str]:
    """Documents alike in words, each with its own audience: (applies_to, publisher)."""
    scopes = {"own": ([SOFTWARE], SOFTWARE), "other": (["化學系"], "化學系"), "owncollege": (["科技學院"], "科技學院"),
              "othercollege": (["理學院"], "理學院"), "two": (["化學系", "國文學系"], "理學院"),
              "school": ([SCHOOL], OFFICE), "mixed": (["化學系", SCHOOL], OFFICE),
              "narrowed": (["化學系"], OFFICE),  # the office's page; its first lines name a department
              "none": None}
    for doc_id, scope in scopes.items():
        db.doc(doc_id, "畢業門檻", "學生須修畢 128 學分")
        if scope is not None:
            db.scope(doc_id, scope[0], publisher=scope[1])
    return list(scopes)


def test_the_default_is_the_owners():
    # owner 11-14: 「开关默认值：照你的建议——找候选开、还有效开（0.85 定稿算法）、对口开、第一关名次分不开。」 and 「对口折扣：用你建议的 0.9。」; 1.0 is off
    assert HybridConfig().scope_factor == 0.9


def test_no_unit_named_a_teaching_units_own_document_counts_less(db):
    ids = _rules(db)
    q = "畢業門檻"
    plain = _scores(db.search(q))
    assert off_audience(db.conn, q, ids) == OWN
    r = db.hybrid(q, config=KEYWORD)
    for doc_id in OWN:  # a department's, a college's, a college's about two departments
        assert _hit(r, doc_id).score == pytest.approx(plain[doc_id] * F), doc_id
    # the school's, one that names the school too, the office's that names a department, one with no scope rows
    for doc_id in ("school", "mixed", "narrowed", "none"):
        assert _hit(r, doc_id).score == plain[doc_id], doc_id
    assert set(_ids(r)[:4]) == {"school", "mixed", "narrowed", "none"} and r.ranking == "keyword"


def test_an_offices_document_that_names_a_department_is_the_schools(db):
    # main session 11-14: applies_to is the publisher's reach narrowed by what the title and first lines name, so a
    # page of 教務處 listing a prize winner's department had counted as that department's own
    db.doc("prize", "得獎公告", "化學系 王OO 同學獲獎")
    db.scope("prize", ["化學系"], publisher=OFFICE)
    db.doc("dept", "得獎公告", "化學系 王OO 同學獲獎")
    db.scope("dept", ["化學系"], publisher="化學系")
    assert off_audience(db.conn, "得獎公告", ["prize", "dept"]) == {"dept"}
    # with a department named, the scope the document was given still counts: it is another department's matter
    assert off_audience(db.conn, "軟體系 得獎公告", ["prize", "dept"]) == {"prize", "dept"}
    assert off_audience(db.conn, "化學系 得獎公告", ["prize", "dept"]) == set()


def test_a_publisher_alone_is_enough(db):
    db.doc("bare", "畢業門檻", "學生須修畢 128 學分")
    db.conn.execute("INSERT INTO doc_scope(doc_id, relation, unit_id) VALUES ('bare', 'publisher', '化學系')")
    assert off_audience(db.conn, "畢業門檻", ["bare"]) == {"bare"}
    assert off_audience(db.conn, "軟體系 畢業門檻", ["bare"]) == set()  # no applies_to rows: relation is not asked


def test_the_schools_name_a_term_or_a_campus_names_no_unit(db):
    ids = _rules(db)
    db.doc("peace", "畢業門檻", "學生須修畢 128 學分")
    db.scope("peace", campus=["和平"])  # the school's, written for one campus
    for q in ("高師大 畢業門檻", "115-1 畢業門檻", "燕巢校區 畢業門檻", "碩士班 畢業門檻"):
        assert off_audience(db.conn, q, [*ids, "peace"]) == OWN, q


def test_a_department_named_another_departments_document_counts_less(db):
    ids = _rules(db)
    q = "軟體系 畢業門檻"
    plain = _scores(db.search(q))
    # units.relation as it is: another department's is -1 (also when it names the school beside it: the school
    # counts as naming nothing there); the named one's, its college's and the school's are not, and another college's
    # is 0 (not the same kind as the department named)
    off = {"other", "two", "mixed", "narrowed"}
    assert off_audience(db.conn, q, ids) == off
    r = db.hybrid(q, config=KEYWORD)
    assert _scores(r) == pytest.approx({d: plain[d] * (F if d in off else 1.0) for d in ids})
    assert set(_ids(r)[-4:]) == off


def test_a_department_named_its_campus_counts_as_the_existing_judgment_has_it(db):
    db.doc("peace", "停車證申請", "汽車停車證請至總務處申請")
    db.doc("nest", "停車證申請", "汽車停車證請至總務處申請")
    db.scope("peace", campus=["和平"])
    db.scope("nest", campus=["燕巢"])
    assert off_audience(db.conn, "軟體系 停車證", ["peace", "nest"]) == {"peace"}  # 軟體系 is on 燕巢
    assert off_audience(db.conn, "燕巢校區 停車證", ["peace", "nest"]) == set()  # no unit named: relation is not asked


def test_an_office_named_is_no_department_named(db):
    # 【Claude】 the plan says 「问题点名了单位：relation 是 −1」; relation never gives -1 between an office and a
    # department, so with only that rule naming 教務處 would lift every department's document over not naming it
    ids = _rules(db)
    assert off_audience(db.conn, "教務處 畢業門檻", ids) == OWN
    assert off_audience(db.conn, "教務處 軟體系 畢業門檻", ids) == {"other", "two", "mixed", "narrowed"}  # one is named


def test_the_final_score_after_reranking(db):
    db.doc("dept", "畢業門檻", "學生須修畢 128 學分")
    db.doc("school", "畢業門檻", "學生須修畢 128 學分")
    db.scope("dept", ["化學系"], publisher="化學系")
    db.scope("school")
    same = {"畢業": 0.5}
    plain = db.hybrid("畢業門檻", reranker=FakeReranker(same), config=HybridConfig(dense=False, scope_factor=1.0))
    assert _ids(plain) == ["dept", "school"] and _scores(plain) == {"dept": 1.5, "school": 1.5}  # a tie, by doc_id
    r = db.hybrid("畢業門檻", reranker=FakeReranker(same), config=RERANK)
    assert _ids(r) == ["school", "dept"] and _scores(r) == pytest.approx({"school": 1.5, "dept": 1.5 * F})
    assert r.best_rerank == 0.5
    # it is a discount, not a filter: clearly more on the topic, the department's document still leads
    db.doc("deptbest", "畢業門檻", "化學系學生須修畢 128 學分")
    db.scope("deptbest", ["化學系"], publisher="化學系")
    r = db.hybrid("畢業門檻", reranker=FakeReranker({"畢業": 0.5, "化學系學生": 0.9}), config=RERANK)
    assert _ids(r)[0] == "deptbest" and _hit(r, "deptbest").score == pytest.approx(3 * 0.9 * F)


def test_the_keyword_order_decides_who_is_reranked(db):
    # direction 第 7 件: 「第一关的名次也要乘，不然全校的文件进不了重排」. One place in the head: da leads the tie by its id
    db.doc("da", "畢業門檻", "甲 學生須修畢 128 學分")
    db.doc("dz", "畢業門檻", "乙 學生須修畢 128 學分")
    db.scope("da", ["化學系"], publisher="化學系")
    db.scope("dz")
    rr = FakeReranker({"畢業": 0.5})
    one = HybridConfig(dense=False, rerank_docs=1, scope_factor=1.0)
    assert _ids(db.hybrid("畢業門檻", reranker=rr, config=one)) == ["da", "dz"]
    assert ["甲" in p for _, ps in rr.calls for p in ps] == [True]
    rr = FakeReranker({"畢業": 0.5})
    r = db.hybrid("畢業門檻", reranker=rr, config=HybridConfig(dense=False, rerank_docs=1, scope_factor=F))
    assert _ids(r) == ["dz", "da"] and ["乙" in p for _, ps in rr.calls for p in ps] == [True]


def test_with_the_additive_scope_of_addendum_f(db):
    # the 11-02 weights (0 since 11-08) are added as before, after the factor 【Claude】
    ids = _rules(db)
    q = "軟體系 畢業門檻"
    plain = _scores(db.search(q))
    config = HybridConfig(dense=False, rerank=False, scope_factor=F, w_scope=0.3)
    r = _scores(db.hybrid(q, config=config))
    assert r["other"] == pytest.approx(plain["other"] * F - 0.3) and r["own"] == pytest.approx(plain["own"] + 0.3)
    assert r["school"] == plain["school"] and len(r) == len(ids)


def test_a_database_without_the_unit_tree_or_the_scope_is_as_before(db):
    ids = _rules(db)
    q = "畢業門檻"
    expected = db.search(q).hits
    db.conn.execute("DROP TABLE doc_scope")
    assert off_audience(db.conn, q, ids) == set() and db.hybrid(q, config=KEYWORD).hits == expected
    db.scope("own", [SOFTWARE])  # the scope again, and no unit tree to read it with
    for table in ("units", "unit_aliases"):
        db.conn.execute(f"DROP TABLE {table}")
    assert off_audience(db.conn, q, ids) == set() and db.hybrid(q, config=KEYWORD).hits == expected
    r = db.hybrid(q, reranker=FakeReranker({"畢業": 0.5}), config=RERANK)
    assert set(_scores(r).values()) == {1.5}


def test_at_one_nothing_is_read(db, caplog):
    _rules(db)
    db.conn.execute("DROP TABLE doc_scope")
    db.conn.execute("CREATE TABLE doc_scope (broken TEXT)")  # reading it fails, and the failure is logged
    q = "畢業門檻"
    with caplog.at_level(logging.ERROR, logger="nknu.retrieval.hybrid"):
        assert db.hybrid(q, config=HybridConfig(dense=False, rerank=False, scope_factor=1.0)).hits == db.search(q).hits
        at_one = HybridConfig(dense=False, scope_factor=1.0)
        assert db.hybrid(q, reranker=FakeReranker({"畢業": 0.5}), config=at_one).best_rerank == 0.5
    assert caplog.records == []
    with caplog.at_level(logging.ERROR, logger="nknu.retrieval.hybrid"):
        assert db.hybrid(q, config=KEYWORD).hits == db.search(q).hits  # a scope that cannot be read: no discount
    assert len(caplog.records) == 1


def test_values_outside_zero_to_one_are_refused():
    for bad in (1.2, -0.1):
        with pytest.raises(ValueError):
            HybridConfig(scope_factor=bad)
