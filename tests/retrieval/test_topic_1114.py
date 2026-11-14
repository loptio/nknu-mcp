"""Plan 2026-11-14 T5 (direction 2026-11-13 第 5 件): with RankConfig.topic_required, a document becomes a keyword
candidate only when it has one of the query's topic words (the words outside the unit names, the terms and the
question words, and what the alias table brings for them). A unit's name and a term then only add to the score of a
document that is on the topic. Off by default: the search is then the one of before. No model loads; the names are
the made-up slice of the unit tree test_hybrid.py uses, the sentences are invented."""

from __future__ import annotations

import dataclasses

import pytest

from nknu.core.textnorm import tokenize
from nknu.retrieval import vectors
from nknu.retrieval.aliases import synonyms, unit_spans
from nknu.retrieval.hybrid import HybridConfig
from nknu.retrieval.search import RankConfig, keyword_pool, unit_spellings
from tests.retrieval.test_hybrid import UNITS, FakeEmbedder, Tiny
from tests.retrieval.test_search import BASE, TODAY, _hit, _ids

ON = dataclasses.replace(BASE, topic_required=True)
SOFTWARE = "軟體工程與管理學系\n軟體系"  # the unit words of 軟體系's own pages
FEES = [["學費", "學雜費"]]
COUNSEL = (*UNITS, ("學生輔導中心", "中心", "國立高雄師範大學", None, ()))


def unit_doc(db: Tiny, doc_id: str, title: str, *chunks: str, unit: str = "") -> list[int]:
    """A document whose chunks carry unit's words in chunk_fts.unit_tokens, as the build writes a unit's own pages."""
    ids = db.doc(doc_id, title, *chunks)
    db.conn.executemany("UPDATE chunk_fts SET unit_tokens = ? WHERE rowid = ?",
                        [(" ".join(tokenize(unit)), c) for c in ids])
    return ids


def snapshot(result):
    return [(h.doc_id, h.score, h.coverage, h.bm25, h.chunk_ids) for h in result.hits], result.candidates


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    t.units()
    yield t
    t.conn.close()


def _fees(db: Tiny) -> None:
    unit_doc(db, "own", "學雜費收費標準", "各學制學雜費一覽", unit=SOFTWARE)  # the named unit's page on the topic
    unit_doc(db, "chem", "學雜費收費標準", "各學制學雜費一覽", unit="化學系")  # another unit's page on the topic
    db.doc("school", "學雜費收費標準", "各學制學雜費一覽")  # the school's page on the topic
    db.doc("intro", "系所簡介", "軟體系成立於民國九十年")  # names the unit, not the topic
    unit_doc(db, "bus", "交通資訊", "搭乘公車路線", unit=SOFTWARE)  # the unit's page on something else
    db.doc("about", "關於本校", "高師大位於高雄市")  # names the school only
    db.doc("office", "處室簡介", "教務處負責註冊與課務")  # names an office only


def test_the_default_is_the_owners():
    # owner 11-14: 「开关默认值：照你的建议——找候选开、还有效开（0.85 定稿算法）、对口开、第一关名次分不开。」
    assert RankConfig().topic_required is True and BASE.topic_required is False and ON.topic_required is True


def test_a_unit_only_document_is_no_candidate_when_a_topic_word_is_there(db):
    _fees(db)
    q = "軟體系 學雜費"
    off, on = db.search(q), db.search(q, rank=ON)
    assert {"intro", "bus"} <= set(_ids(off))  # today: the unit's name alone brings them
    assert set(_ids(on)) == {"own", "chem", "school"} and on.candidates == 3
    pool = keyword_pool(db.conn, q, today=TODAY, aliases=[], rank=ON)
    assert pool.topic == ["学杂", "杂费"] and pool.tokens == keyword_pool(db.conn, q, today=TODAY, aliases=[],
                                                                           rank=BASE).tokens
    assert on.understood == off.understood  # what the answer says was understood does not change


def test_the_named_units_document_on_the_topic_comes_first(db):
    _fees(db)
    on = db.search("軟體系 學雜費", rank=ON)
    assert _ids(on)[0] == "own" and _hit(on, "own").coverage == pytest.approx(0.75)  # its unit words count half
    assert _hit(on, "chem").coverage == _hit(on, "school").coverage == 0.5
    assert _hit(on, "own").bm25 < min(_hit(on, "chem").bm25, _hit(on, "school").bm25)  # bm25 counts the unit's words


def test_written_without_a_space(db):
    _fees(db)
    assert set(_ids(db.search("軟體系學雜費", rank=ON))) == {"own", "chem", "school"}
    assert _ids(db.search("軟體系學雜費", rank=ON))[0] == "own"
    assert set(_ids(db.search("软体系学杂费", rank=ON))) == {"own", "chem", "school"}  # Simplified: the same names


def test_a_units_name_alone_is_searched_as_before(db):
    _fees(db)
    for q in ("軟體系", "軟體工程與管理學系", "教務處", "高師大", "化學系 軟體系", "軟體系 115-1", "請問軟體系？",
              "軟體系的課"):  # 的課: one character is no topic word
        assert snapshot(db.search(q, rank=ON)) == snapshot(db.search(q)), q
        assert keyword_pool(db.conn, q, today=TODAY, aliases=[], rank=ON).topic == [], q
    assert "intro" in _ids(db.search("軟體系", rank=ON))


def test_only_topic_words_is_searched_as_before(db):
    # nothing but topic words (and single characters): no unit's name and no term to keep apart, so the query is the
    # one of before, to the bit
    _fees(db)
    for q, aliases in (("學雜費", ()), ("學雜費 收費標準", ()), ("學費", FEES), ("公車路線怎麼搭", ())):
        assert snapshot(db.search(q, aliases, rank=ON)) == snapshot(db.search(q, aliases)), q
        assert keyword_pool(db.conn, q, today=TODAY, aliases=[list(g) for g in aliases], rank=ON).topic == [], q


def test_a_single_character_is_no_topic_word(db):
    # 有 is left between the unit's name and 哪些: alone in the index it is an isolated character, which says nothing
    # about what is asked
    _fees(db)
    db.doc("course", "課程地圖", "必修課程一覽")
    pool = keyword_pool(db.conn, "軟體系有哪些課程", today=TODAY, aliases=[], rank=ON)
    assert pool.topic == ["课程"] and "系有" in pool.tokens and set(pool.docs) == {"course"}
    pool = keyword_pool(db.conn, "軟體系 有 課程", today=TODAY, aliases=[], rank=ON)
    assert pool.topic == ["课程"] and "有" in pool.tokens and set(pool.docs) == {"course"}


def test_a_name_without_short_names_and_the_schools_name_are_unit_names(db):
    # 教務處 has no short name and the school is the tree's root: search.unit_names gives neither, T5 reads both
    _fees(db)
    assert "office" in _ids(db.search("教務處 學雜費")) and "about" in _ids(db.search("高師大 學雜費"))
    for q in ("教務處 學雜費", "高師大 學雜費", "國立高雄師範大學 學雜費"):
        assert set(_ids(db.search(q, rank=ON))) == {"own", "chem", "school"}, q
    assert set(unit_spellings(db.conn)) >= {"教務處", "高師大", "國立高雄師範大學", "軟體系", "軟體工程與管理學系"}


def test_an_alias_of_the_table_still_admits(db):
    _fees(db)
    q = "軟體系 學費"  # no page writes 學費; the table says 學雜費 is the same thing
    assert _ids(db.search(q, rank=ON)) == []  # without the table no document has the topic word
    on = db.search(q, FEES, rank=ON)
    assert set(_ids(on)) == {"own", "chem", "school"} and _ids(on)[0] == "own"
    assert keyword_pool(db.conn, q, today=TODAY, aliases=FEES, rank=ON).topic == ["学费", "学杂", "杂费"]
    assert {"intro", "bus"} <= set(_ids(db.search(q, FEES)))


def test_a_term_only_document_is_no_candidate_when_a_topic_word_is_there(db):
    db.doc("cal", "115學年度第一學期行事曆", "開學日", ay=115, sem=1)  # the term and nothing of the topic
    db.doc("pick", "115學年度第一學期選課公告", "選課時間表", ay=115, sem=1)
    db.doc("guide", "選課須知", "選課注意事項")
    for q in ("115-1 選課", "115學年度第1學期選課", "這學期 選課"):
        assert "cal" in _ids(db.search(q)), q
        on = db.search(q, rank=ON)
        assert _ids(on) == ["pick", "guide"] and _hit(on, "pick").score > _hit(on, "guide").score, q
    assert _hit(db.search("115-1 選課", rank=ON), "pick").bm25 < _hit(db.search("選課", rank=ON), "pick").bm25
    for q in ("115-1", "115學年度", "115-1 什麼時候？", "113學年度入學", "下學期呢？"):  # a term alone: as before
        assert snapshot(db.search(q, rank=ON)) == snapshot(db.search(q)), q


def test_the_unit_names_switch_does_not_decide_what_a_unit_name_is(db):
    # unit_names (11-08) is whether a short name brings the unit's name; a unit's name is no topic word either way
    _fees(db)
    on = db.search("軟體系 學雜費", rank=dataclasses.replace(ON, unit_names=False))
    assert set(_ids(on)) == {"own", "chem", "school"}


def test_a_database_without_the_unit_tree_knows_no_unit_names(db):
    _fees(db)
    db.conn.execute("DROP TABLE units")
    db.conn.execute("DROP TABLE unit_aliases")
    assert unit_spellings(db.conn) == []
    q = "軟體系 學雜費"  # every word is a topic word then: the search of before
    assert snapshot(db.search(q, rank=ON)) == snapshot(db.search(q))
    assert _ids(db.search("115-1 學雜費", rank=ON)) and "intro" not in _ids(db.search("115-1 學雜費", rank=ON))


def test_the_topic_is_asked_of_the_document(db):
    # main session 11-14: the direction's 「文件才进候选……进了候选的文件，这些词照样算」. The index's rows are chunks; a
    # document with the topic in one chunk and the unit's name in another is a candidate with both, as before
    first, second = db.doc("long", "系務公告", "軟體系聯絡方式", "學雜費繳費期限")
    db.doc("intro", "系所簡介", "軟體系成立於民國九十年")  # the unit's name and nothing of the topic
    off, on = db.search("軟體系 學雜費"), db.search("軟體系 學雜費", rank=ON)
    assert sorted(_hit(off, "long").chunk_ids) == sorted(_hit(on, "long").chunk_ids) == [first, second]
    assert "intro" in _ids(off) and _ids(on) == ["long"]


def test_a_document_on_the_topic_keeps_what_it_had(db):
    # only documents leave: the ones that stay have the chunks, the bm25 and the coverage of before (the score's
    # bm25 part is a share of the best candidate's, so it moves when the best one was a unit-only document)
    _fees(db)
    unit_doc(db, "split", "系務公告", "軟體系聯絡方式與分機", "學雜費繳費期限與方式", unit=SOFTWARE)
    db.doc("cal", "115學年度第一學期行事曆", "開學日", ay=115, sem=1)  # the term and nothing of the topic
    for q in ("軟體系 學雜費", "115-1 學雜費", "軟體系 115-1 學雜費 繳費"):
        off, on = db.search(q), db.search(q, rank=ON)
        kept = {h.doc_id: (h.coverage, h.bm25, h.chunk_ids) for h in on.hits}
        assert kept and kept == {h.doc_id: (h.coverage, h.bm25, h.chunk_ids) for h in off.hits if h.doc_id in kept}, q
        assert len(on.hits) < len(off.hits), q  # and something did leave


def test_an_alias_inside_a_units_name_brings_no_topic_word(db):
    # 輔導 is a word of the table and a part of 學生輔導中心: naming the unit asks for nothing about 諮商
    db.conn.execute("DELETE FROM units")
    db.conn.execute("DELETE FROM unit_aliases")
    db.units(COUNSEL)
    table = [["輔導", "諮商"]]
    db.doc("centre", "中心簡介", "學生輔導中心位於活動中心三樓")
    db.doc("book", "預約諮商", "線上預約諮商時段")
    q = "學生輔導中心"
    assert snapshot(db.search(q, table, rank=ON)) == snapshot(db.search(q, table))
    pool = keyword_pool(db.conn, "學生輔導中心 預約", today=TODAY, aliases=table, rank=ON)
    assert pool.topic == ["预约"] and set(pool.docs) == {"book"}
    pool = keyword_pool(db.conn, "課業輔導 預約", today=TODAY, aliases=table, rank=ON)  # 輔導 outside a unit's name
    assert pool.topic == []  # every token is a topic word: 课业 业辅 辅导 预约 and 咨商


def test_queries_that_break_nothing(db):
    _fees(db)
    for q in ('軟體系 AND OR NEAR * ^ "', "軟體系 !!!", "", '軟體系 title_tokens:學雜費', '軟體系 學雜費*', '"軟體系 學雜費',
              "NEAR(軟體系 學雜費)", "軟體系 NOT 學雜費", "'; DROP TABLE docs; --", "　", "\x00", "115-1 (學雜費)"):
        assert isinstance(db.search(q, rank=ON).hits, list), q
    distinct = "軟體系" + "".join(chr(c) for c in range(0x4E00, 0x4E00 + 5000))
    assert db.search(distinct, rank=ON).hits == []  # 300 characters of topic words no document has
    assert set(_ids(db.search("軟體系 115-1 " + "學雜費" * 2000, rank=ON))) == {"own", "chem", "school"}


def test_hybrid_takes_the_switch_through_rank(db):
    _fees(db)
    off = HybridConfig(dense=False, rerank=False)
    for q in ("軟體系 學雜費", "軟體系", "115-1 學雜費"):
        assert db.hybrid(q, rank=ON, config=off).hits == db.search(q, rank=ON).hits, q
    assert set(_ids(db.hybrid("軟體系 學雜費", rank=ON, config=off))) == {"own", "chem", "school"}


def test_the_vectors_still_bring_what_the_keywords_leave_out(db):
    # the switch is on the keyword pool only: no page writes 多益, so no keyword candidate is left, and the vector
    # path finds the page that writes TOEIC as it did before
    db.doc("gate", "系所簡介", "軟體系 TOEIC 門檻")
    db.vectors(FakeEmbedder())
    q = "軟體系 多益"
    assert _ids(db.search(q)) == ["gate"] and _ids(db.search(q, rank=ON)) == []
    r = db.hybrid(q, rank=ON, embedder=FakeEmbedder(), config=HybridConfig(rerank=False))
    assert _ids(r) == ["gate"] and r.ranking == "hybrid" and r.hits[0].coverage == 0.0


# ---------------------------------------------------------------- aliases.py's part

NAMES = ["國立高雄師範大學", "高師大", "教務處", "軟體工程與管理學系", "軟體系", "音樂學系", "音樂系", "招生企劃組", "招生組",
         "生活輔導組", "輔導"]


def test_unit_spans():
    assert unit_spans("軟體系 學雜費", NAMES) == [(0, 3)]
    assert unit_spans("高師大軟體工程與管理學系的學雜費", NAMES) == [(0, 3), (3, 12)]
    assert unit_spans("学杂费 软体系", NAMES) == [(4, 7)]  # Simplified
    assert unit_spans("學雜費", NAMES) == [] and unit_spans("", NAMES) == [] and unit_spans("軟體系", []) == []


def test_unit_spans_longest_name_first():
    assert unit_spans("生活輔導組 宿舍", NAMES) == [(0, 5)]  # not the 輔導 inside it
    assert unit_spans("輔導 宿舍", NAMES) == [(0, 2)]


def test_a_name_that_runs_on_into_another_word_is_no_unit():
    # as nknu.content.units reads a name: 系列, 系統, 組別, 所以 and 所有 are other words
    assert unit_spans("音樂系列講座", NAMES) == [] and unit_spans("招生組別", NAMES) == []
    assert unit_spans("音樂系 系列講座", NAMES) == [(0, 3)]


def test_synonyms_are_the_tables_part_of_what_expand_brings():
    table = [["停修", "棄選", "退選"], ["學費", "學雜費"]]
    names = [["軟體工程與管理學系", "軟體系"]]
    assert synonyms("軟體系 退選", table, names) == ["停修", "弃选"]  # not the unit's 软体工程…
    assert synonyms("軟體系 學費 退選", table, names) == ["学杂", "杂费", "停修", "弃选"]
    assert synonyms("軟體系", table, names) == [] and synonyms("選課", table, names) == []


def test_synonyms_of_a_member_inside_a_units_name_are_left_out():
    table = [["輔導", "諮商"]]
    assert synonyms("學生輔導中心 預約", table) == ["咨商"]
    assert synonyms("學生輔導中心 預約", table, within=[(0, 6)]) == []
    assert synonyms("學生輔導中心 課業輔導", table, within=[(0, 6)]) == ["咨商"]  # written once outside the name
    assert synonyms("成人教育研究所甄試", [["研究所甄試", "碩士班甄試"]], within=[(0, 7)]) == ["硕士", "士班", "班甄"]
