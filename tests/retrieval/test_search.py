import dataclasses
import sqlite3
from datetime import date

import pytest

from nknu.content.schema import BACKGROUND, EXAM, JOURNAL, PRIMARY, create_schema
from nknu.content.terms import canonical_tokens
from nknu.core.textnorm import tokenize
from nknu.retrieval.search import TOP_N, RankConfig, search_docs
from nknu.retrieval.types import DocHit

TODAY = date(2026, 11, 1)  # 115-1
WITHDRAW = ["停修", "棄選", "退選"]
# The ranking before v2, which every test in this file pins: with the date part and the tier off, it ranks as before
# (spec 2026-11-01 「排序 v2」), the pool cap aside (addendum M1). The date part is tested in test_rank_v2.py, the tier
# and the cap in test_tier_1108.py. Owner 11-03 (「可以，这样砍」): no series, so every document is its own hit, in
# score order.
BASE = RankConfig(recency=False, tier=False)
# the series part of the schema before 11-03 (pipeline-1 and the skeleton's skeleton-2), which a build no longer writes
OLD_SERIES_DDL = """
ALTER TABLE docs ADD COLUMN series_id TEXT;
CREATE TABLE series (series_id TEXT PRIMARY KEY, key TEXT NOT NULL, relation TEXT NOT NULL, newest_id TEXT NOT NULL,
                     size INTEGER NOT NULL);
CREATE TABLE series_members (series_id TEXT NOT NULL, doc_id TEXT NOT NULL, ord INTEGER NOT NULL,
                             PRIMARY KEY (series_id, doc_id));
"""


def _tokens(s: str) -> str:
    return " ".join(tokenize(s) + canonical_tokens(s))


class Tiny:
    """A content database filled the way nknu.content.build fills it: one FTS row per chunk, title tokens repeated."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        create_schema(self.conn)

    def doc(self, doc_id, title, *chunks, ay=None, sem=None, ident=None, kind="notice", doc_type="", tier=None):
        """tier: docs.tier; by default the build's (plan 2026-11-08 T1): background for a journal or an exam."""
        text = "\n".join(chunks)
        tier = tier or (BACKGROUND if doc_type in (JOURNAL, EXAM) else PRIMARY)
        self.conn.execute(
            "INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars, period_ay,"
            " period_sem, ident_date, doc_type, tier) VALUES (?, ?, 'newsdetail', ?, ?, '教務處', 1, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, kind, title, f"https://example.test/{doc_id}", text, len(text), ay, sem, ident, doc_type, tier))
        ids, start = [], 0
        for chunk in chunks or ("",):
            cur = self.conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, 0, ?, ?)",
                                    (doc_id, start, start + len(chunk)))
            self.conn.execute("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens) VALUES (?, ?, ?)",
                              (cur.lastrowid, _tokens(title), _tokens(chunk)))
            ids.append(cur.lastrowid)
            start += len(chunk) + 1
        return ids

    def old_series(self, series_id, members):
        """What a build before 11-03 wrote for one series (members newest first): docs.series_id and the series
        tables, as the skeleton's database still holds them. Search reads none of it now (owner 11-03)."""
        if "series_id" not in {r[1] for r in self.conn.execute("PRAGMA table_info(docs)")}:
            self.conn.executescript(OLD_SERIES_DDL)
        self.conn.execute("INSERT INTO series VALUES (?, ?, 'periodic', ?, ?)",
                          (series_id, series_id, members[0], len(members)))
        for i, doc_id in enumerate(members):
            self.conn.execute("INSERT INTO series_members VALUES (?, ?, ?)", (series_id, doc_id, i))
            self.conn.execute("UPDATE docs SET series_id = ? WHERE doc_id = ?", (series_id, doc_id))

    def applies(self, doc_id, cohort_from=None, cohort_exact=None):
        self.conn.execute("INSERT INTO applies_to VALUES (?, '教務處', ?, ?, '')", (doc_id, cohort_from, cohort_exact))

    def date(self, doc_id, kind, day, source="行事曆"):
        self.conn.execute("INSERT INTO dates VALUES (?, ?, ?, ?, ?)", (doc_id, kind, day, day, source))

    def search(self, q, aliases=(), rank=BASE, **kw):
        return search_docs(self.conn, q, today=TODAY, aliases=[list(g) for g in aliases], rank=rank, **kw)


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "t.db")
    yield t
    t.conn.close()


def _ids(result):
    return [h.doc_id for h in result.hits]


def _hit(result, doc_id):
    return next(h for h in result.hits if h.doc_id == doc_id)


def _withdrawals(db):
    db.doc("d3", "115學年度第一學期線上申請停修", "受理期間：115年11月3日起至11月16日止", ay=115, sem=1, ident="2026-10-21")
    db.doc("d2", "114學年度第二學期線上申請停修", "受理期間：115年4月7日起至4月20日止", ay=114, sem=2, ident="2026-03-30")
    db.doc("d1", "114學年度第一學期線上申請停修", "受理期間：114年11月4日起至11月17日止", ay=114, sem=1, ident="2025-10-20")
    db.doc("dx", "學生選課應行注意事項", "停修以一科為限，不予退費。")


def _yearly(db, best: str, old_tables: bool = False):
    """One 停修 notice a year (112, 113, 114 學年度 第1學期), alike in words; best is the one whose body says 停修
    four times more, so it matches 停修 best. old_tables: the database also holds the series an old build made of
    them (the skeleton's has such rows)."""
    for ay in (112, 113, 114):
        body = "停修 停修 停修，申請停修" if f"d{ay}" == best else "受理期間另行公告"
        db.doc(f"d{ay}", f"{ay}學年度第一學期線上申請停修", body, ay=ay, sem=1, ident=f"{ay + 1911}-10-20")
    if old_tables:
        db.old_series("s0123456789", ["d114", "d113", "d112"])


def test_fts_injection(db):
    db.doc("d1", "115學年度第一學期線上申請停修", "停修以一科為限")
    for q in ('AND OR NEAR * ^ "', "!!!", "", 'title_tokens:停修', '停修*', '"停修', "NEAR(停修 選課)", "^停修",
              "停修 NOT 選課", "'; DROP TABLE docs; --", "{body_tokens} : x", "　", "\x00"):
        result = db.search(q)
        assert isinstance(result.hits, list), q
    for q in ("!!!", "", "　"):
        assert db.search(q).hits == [] and db.search(q).candidates == 0
    assert _ids(db.search('"停修')) == ["d1"]


def test_long_query(db):
    db.doc("d1", "115學年度第一學期線上申請停修", "停修以一科為限")
    assert _ids(db.search("停修" * 20_000)) == ["d1"]
    distinct = "".join(chr(c) for c in range(0x4E00, 0x4E00 + 5000))
    assert db.search(distinct).hits == []
    assert _ids(db.search("115-1 " * 2000)) == ["d1"]  # one term, repeated: found by its canonical tokens


def test_hits_carry_no_series_fields():
    # owner 11-03: no series, so a hit is one document with its own score and chunks; nothing is folded under it
    assert [f.name for f in dataclasses.fields(DocHit)] == ["doc_id", "score", "coverage", "bm25", "chunk_ids",
                                                         "background"]  # addendum M2


@pytest.mark.parametrize("old_tables", [False, True])
def test_yearly_versions_are_separate_results_in_score_order(db, old_tables):
    # owner 11-03: the 112, 113 and 114 notices of one kind are three results, best match first; the two alike in
    # words tie, newest identity date first. Each keeps its own score and chunks, on an old build's database too
    _yearly(db, best="d112", old_tables=old_tables)
    result = db.search("停修")
    assert _ids(result) == ["d112", "d114", "d113"] and result.candidates == 3
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[1] == scores[2]
    assert all(h.chunk_ids and h.coverage == 1.0 for h in result.hits)


@pytest.mark.parametrize("old_tables", [False, True])
def test_a_query_naming_112_ranks_the_112_notice_first(db, old_tables):
    # owner 11-03: with no series to promote an older member, the term does it: 112 named (as a term or a year), the
    # 112 notice is first by term matching (its term tokens and the period bonus), although the 114 one matches 停修
    # best; the other years still come back, each on its own
    _yearly(db, best="d114", old_tables=old_tables)
    assert _ids(db.search("停修"))[0] == "d114"
    for q in ("112-1 停修", "112學年度第1學期 停修", "112學年度 停修"):
        result = db.search(q)
        assert _ids(result)[0] == "d112" and sorted(_ids(result)) == ["d112", "d113", "d114"], q


def test_period_named_ranks_its_notice_first(db):
    # was test_period_promotes_older (a series' older member shown in its newest's place); now the notice whose period
    # the query names is first by term matching and the others follow as their own results
    _withdrawals(db)
    for q in ("114-2 停修", "114學年度第2學期 停修", "114學年度下學期停修"):
        result = db.search(q)
        assert _ids(result)[0] == "d2" and sorted(_ids(result)) == ["d1", "d2", "d3", "dx"], q
    assert sorted(_ids(db.search("114學年度 停修"))[:2]) == ["d1", "d2"]  # a year: both its semesters first
    assert sorted(_ids(db.search("113-1 停修"))) == ["d1", "d2", "d3", "dx"]  # no notice of that term: all, as found


def test_relative_term_ranks_its_notice_first(db):
    # was test_relative_term_promotes: the same through a relative term
    _withdrawals(db)
    assert _ids(db.search("上學期停修"))[0] == "d2"  # 2026-11-01: 上學期 is 114-2


def test_alias_coverage(db):
    db.doc("d1", "線上申請停修", "停修以一科為限")
    db.doc("d2", "選課系統說明", "加退選期間請上網辦理")
    db.doc("d3", "棄選申請表", "棄選科目")
    assert _ids(db.search("退選")) == ["d2"]  # without the table 停修 is not found
    result = db.search("退選", aliases=[WITHDRAW])
    assert sorted(_ids(result)) == ["d1", "d2", "d3"]
    assert all(h.coverage == 1.0 for h in result.hits)
    assert result.understood.aliases == ["退選"]
    assert _hit(db.search("退選 截止", aliases=[WITHDRAW]), "d1").coverage == 0.5


def test_term_spellings_give_the_same_hits(db):
    _withdrawals(db)
    db.doc("d9", "115學年度第一學期選課公告", "加退選期間", ay=115, sem=1)

    def ranked(q):
        return [(h.doc_id, h.score) for h in db.search(q).hits]

    first = ranked("115-1 停修")
    assert first[0][0] == "d3"
    for q in ("115學年度第1學期 停修", "115學年度第一學期停修", "115學年度上學期 停修", "115上 停修", "這學期 停修"):
        assert ranked(q) == first, q


def test_period_match(db):
    for doc_id, ay, sem in (("da", 115, 1), ("db", 114, 2), ("dc", 114, 1), ("dd", None, None)):
        db.doc(doc_id, "線上申請停修", "停修以一科為限", ay=ay, sem=sem)
    assert _ids(db.search("114-2 停修"))[0] == "db"
    assert sorted(_ids(db.search("114學年度 停修"))[:2]) == ["db", "dc"]
    assert _ids(db.search("114學年度 停修"))[2:] == ["da", "dd"]  # tie: doc_id


def test_cohort_match(db):
    for doc_id in ("da", "db", "dc", "dd"):
        db.doc(doc_id, "學士班畢業門檻", "英文能力須達B2")
    db.applies("db", cohort_from=111)
    db.applies("dc", cohort_exact=113)
    db.applies("dd", cohort_exact=110)
    result = db.search("我是113學年度入學的 畢業門檻")
    assert sorted(_ids(result)[:2]) == ["db", "dc"] and _ids(result)[2:] == ["da", "dd"]
    assert result.understood.cohorts == [113]
    assert sorted(_ids(db.search("我是112入學的 畢業門檻"))[:1]) == ["db"]


def test_ties_newest_date_first_then_doc_id(db):
    for doc_id, ident in (("da", "2026-01-05"), ("db", "2026-05-01"), ("dc", None), ("dd", "2026-05-01")):
        db.doc(doc_id, "線上申請停修", "停修以一科為限", ident=ident)
    assert _ids(db.search("停修")) == ["db", "dd", "da", "dc"]


def test_terms_only_query_uses_canonical_coverage(db):
    db.doc("da", "115學年度第一學期行事曆", "開學")
    db.doc("db", "115學年度行事曆", "開學")
    result = db.search("115-1")
    assert result.understood.tokens == [] and result.understood.canonical == ["term115s1", "ay115"]
    assert _hit(result, "da").coverage == 1.0 and _hit(result, "db").coverage == 0.5
    assert _ids(result)[0] == "da"


def test_best_chunks_first(db):
    first, second = db.doc("d1", "選課須知", "加退選期間", "停修以一科為限，停修申請請上網")
    h = db.search("停修").hits[0]
    assert h.chunk_ids == [second] and h.bm25 < 0
    h = db.search("停修 加退選").hits[0]
    assert sorted(h.chunk_ids) == [first, second]


def test_limit_and_candidates(db):
    for i in range(12):
        db.doc(f"d{i:02}", f"停修公告{i}", "停修")
    result = db.search("停修")
    assert len(result.hits) == TOP_N and result.candidates == 12
    assert len(db.search("停修", limit=5).hits) == 5


def test_read_only_connection_with_row_factory(tmp_path):
    t = Tiny(tmp_path / "t.db")
    _withdrawals(t)
    t.conn.commit()
    expected = [(h.doc_id, h.score, h.chunk_ids) for h in t.search("115-1 停修").hits]
    t.conn.close()
    ro = sqlite3.connect(f"file:{tmp_path / 't.db'}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        result = search_docs(ro, "115-1 停修", today=TODAY, aliases=[], rank=BASE)
        assert [(h.doc_id, h.score, h.chunk_ids) for h in result.hits] == expected
    finally:
        ro.close()


def test_understood(db):
    db.doc("d1", "線上申請停修", "停修以一科為限")
    u = db.search("我是113學年度入學的，下學期要退選", aliases=[WITHDRAW]).understood
    assert u.terms == ["115-2"] and u.cohorts == [113] and u.aliases == ["退選"]
    assert u.canonical == ["entry113", "term115s2", "ay115"]
    assert u.tokens == ["我", "要退", "退选"]  # 是 and 的 are question filler; 要 stays (see below)
    assert db.search("116學年度甄試").understood.terms == ["116學年度"]


def test_question_words_not_searched(db):
    # fix round 2 (11-01): 什麼時候, 怎麼, 嗎 ... only say that something is asked; searched for, they made the long
    # FAQ page that holds them all outrank the document that answers
    db.doc("dq", "Q & A", "Q：什麼時候可以選課？A：請見行事曆。", "Q：怎麼停修？A：線上申請。")
    db.doc("de", "大學部網路選課", "大學部網路選課", ay=115, sem=2)
    result = db.search("下學期什麼時候選課？")
    assert result.understood.tokens == ["选课"] and result.understood.terms == ["115-2"]
    assert _hit(result, "de").coverage == 1.0
    assert _ids(result)[0] == "de"


def test_question_words_leave_terms_and_aliases_alone(db):
    db.doc("d1", "115學年度第一學期線上申請停修", "停修以一科為限", ay=115, sem=1)
    result = db.search("請問115-1可以退選嗎？", aliases=[WITHDRAW])
    assert result.understood.tokens == ["退选"]
    assert result.understood.terms == ["115-1"] and result.understood.aliases == ["退選"]
    assert _hit(result, "d1").coverage == 1.0


def test_only_question_words_keeps_them(db):
    db.doc("d1", "常見問題", "什麼時候")
    result = db.search("什麼時候？")
    assert result.understood.tokens == ["什么", "么时", "时候"] and _ids(result) == ["d1"]


def test_yao_is_not_a_question_word(db):
    # unlike v1's list: 要 is part of 要點 and 重要 in this corpus's titles (實施要點, 作業要點)
    db.doc("d1", "學生請假要點", "請假以一日為單位")
    result = db.search("請假要點")
    assert result.understood.tokens == ["请假", "假要", "要点"] and _hit(result, "d1").coverage == 1.0


def test_question_opener_only_where_a_clause_starts(db):
    # fix round 3 (11-01): 請問 and 請教 open a question only where a clause starts; cut anywhere, they took the 請 of
    # 申請 and the 問 / 教 of 問題 / 教室, and 「申請教室」 searched for 申 and 室
    for q in ("申請教室", "怎麼申請教室"):
        assert {"申请", "教室"} <= set(db.search(q).understood.tokens), q
    assert {"申请", "问题"} <= set(db.search("申請問題").understood.tokens)
    tokens = db.search("請問停修怎麼申請").understood.tokens
    assert {"停修", "申请"} <= set(tokens) and "请问" not in tokens
    assert db.search("我想請問，轉系").understood.tokens == ["转系"]  # a clause starts again after 我想
    assert db.search("SSO請問密碼").understood.tokens == ["sso", "密码"]  # only a CJK character can take its 請
    assert {"教务", "务处"} <= set(db.search("請教務處開在學證明").understood.tokens)  # 請 + 教務處


def test_more_question_words(db):
    # fix round 3: 何時, 能否 and 幾 are question words too. 幾 stands where a number would, inside the phrase it asks
    # about, so the words around it close up (休幾學期 -> 休學期); inside 任何時間 and 幾何 they are parts of other words
    db.doc("d1", "學生休學辦法", "休學以二學期為限")
    assert db.search("停修何時截止").understood.tokens == ["停修", "截止"]
    assert db.search("能否停修").understood.tokens == ["停修"]
    result = db.search("最多能休幾學期")
    assert "休学" in result.understood.tokens and not any("几" in t for t in result.understood.tokens)
    assert _ids(result) == ["d1"]
    assert {"任何", "时间"} <= set(db.search("任何時間都能申請嗎").understood.tokens)
    assert "几何" in db.search("幾何學停修").understood.tokens


def test_term_alone_when_only_question_words_are_left(db):
    # fix round 3: 「115-1 什麼時候？」 and 「下學期呢？」 name a term and ask nothing else, so the term alone is searched;
    # falling back to their question words found the FAQ page and the school journal that hold them
    db.doc("dq", "Q & A", "Q：什麼時候可以選課？A：請見行事曆。")
    db.doc("d1", "115學年度第一學期行事曆", "開學", ay=115, sem=1)
    db.doc("d2", "115學年度第二學期行事曆", "開學", ay=115, sem=2)
    result = db.search("115-1 什麼時候？")
    assert result.understood.tokens == [] and result.understood.canonical == ["term115s1", "ay115"]
    assert _ids(result) == ["d1", "d2"]
    result = db.search("下學期呢？")  # 2026-11-01: 115-2
    assert result.understood.tokens == [] and _ids(result) == ["d2", "d1"]
