"""Bigram search over the content database (plan 2026-11-01, Task A). No LLM.

1. One FTS5 MATCH ORs the query's words (textnorm bigrams), its terms and cohorts as canonical tokens (115-1,
   115學年度第1學期, 115學年度上學期 and 這學期 are all term115s1) and its alias expansions: the alias table's groups and,
   one way, the unit tree's short names (事經系 brings 事業經營學系; unit_names, RankConfig.unit_names). The term, cohort and
   relative-word spans are taken out of the words first, so how a term is spelled changes nothing; so are question
   words (什麼時候, 怎麼, 嗎 ...: they say that something is asked, not what; 請問 and 請教 only where a clause starts).
   When nothing else is left, the query's terms and aliases are searched alone, and the question words only when the
   query has neither.
   Each document keeps its best PER_DOC matched chunks by bm25, and the pool keeps the best POOL_DOCS documents by
   their best chunk (owner 11-08, plan addendum M1: a long document no longer fills the pool or gains coverage from
   many chunks).
   With RankConfig.topic_required (plan 2026-11-14 T5, direction 2026-11-13 第 5 件; the default), when the query
   has both topic words and other tokens (_topic), only the documents with a chunk that has a topic word are
   candidates (_FTS_TOPIC): one that has only a unit's name or only a term is none. The MATCH and so every chunk's
   bm25 are the ones of before, so a document on the topic keeps the chunks, the bm25 and the coverage it had: the
   unit's name and the term count for it as they did (the direction: 「进了候选的文件，这些词照样算进挑 300 份的那个
   分」). Steps 2 to 4 are as they are.
2. The kept chunks are grouped by document:
   score = W_COVERAGE*coverage + W_BM25*bm25_norm + W_PERIOD*period_match + W_COHORT*cohort_match,
   ties newest identity date first (no date last), then doc_id.
   coverage: share of the query's words in the document's kept chunks (its canonical tokens when the query is
   only terms); a word inside a matched alias counts when another member of the alias group is all there; a word
   only the document's unit words have (chunk_fts.unit_tokens, pipeline-5) counts RankConfig.unit_cover.
   bm25_norm: the document's best chunk bm25 over the best of all candidates.
   period_match: 1.0 when the query names the document's (學年度, 學期), 0.6 when only the year matches.
   cohort_match: 1.0 when a 入學 year the query names is one the document applies to.
   Ranking v2 (spec 「排序 v2」, 11-01; RankConfig) adds, when the query names no term or year (a cohort is not one):
   w_recent * exp(-days before today / tau_days) of the identity date, else of a calendar event's last day (a day
   after today counts as today), else no_date_factor * w_recent.
   The date a document ranks by (plan 2026-11-14 T2, completed the same day; RankConfig.copy_dates, on by default):
   for a document the build merged copies into, the latest identity date among it and them (docs.rank_date), in this
   date part, in the tie of equal scores above (sort_docs) and in adjustments; the date part is never less than its
   own date gives (_date_part). The build keeps the earliest copy and shows its day, and ranked by that day a gold
   document of the 教务处 control fell from keyword rank 4 to 26 (main session, 11-14). What a response shows is
   still the document's own date: respond reads docs.ident_date itself, never _Doc. Off, and in a database without
   the column, the date is the document's own.
3. By tier (owner 11-08, addendum M2; RankConfig.tier): archive members, journals, exams and (since 11-08 10:16)
   files of meeting minutes are background material
   (background_ids: docs.tier, which the build writes since plan 2026-11-08 T1; in an older database the 11-08 rule
   on the id's shape and doc_type). The first TOP_N - BACKGROUND_SLOTS primary documents come first, then the best
   BACKGROUND_SLOTS background ones, then the other primary documents and the other background ones, each part in
   score order; the order does not depend on limit. This replaced the journal and exam demotions (w_journal, w_exam)
   of ranking v2. RankConfig(recency=False, tier=False) ranks as before ranking v2, the pool cap aside.
4. The first limit documents in that order are the hits (top_hits), each on its own: no series (owner 11-03,
   「可以，这样砍」; plan addendum L). Two editions of one notice are two hits, and the one whose period the query names
   comes first through period_match and its term tokens, the newer one through ranking v2 when no term is named.
There is no threshold: whether the hits answer is the agent's call (owner decision 11-01: test not-found with real
agents first).

search_docs is keyword_pool (steps 1-2) plus by_tier and top_hits (steps 3-4): plan 2026-11-02 T8 took them out,
with adjustments (the ranking-v2 part of step 2's score), doc_rows and sort_docs, for nknu.retrieval.hybrid, which
fuses this keyword pool with a vector pool. The private helpers keep their names and signatures (the 11-02 research scripts call them),
except fold and its alias _fold, _open, _names, _Shown and _MEMBERS, which went with the series on 11-03.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from nknu.content.schema import BACKGROUND, EXAM, JOURNAL
from nknu.content.terms import Term, canonical_tokens, query_terms
from nknu.core.textnorm import CJK, fts_query, norm_text, tokenize
from nknu.retrieval.aliases import expand, synonyms, unit_spans
from nknu.retrieval.types import DocHit, SearchResult, Understood

TITLE_WEIGHT = 3.0
CANDIDATES = 300  # documents per tier the vector path brings (hybrid's dense_candidates), PER_DOC chunks each;
#                   until 11-08 the chunks of either path
POOL_DOCS = 300  # documents the keyword pool keeps, by their best chunk (addendum M1, 【Claude】)
PER_DOC = 3  # each one's best chunks (review 11-06: 「每份文件最多 2～3 段」)
TOP_N = 10
BACKGROUND_SLOTS = 2  # background documents placed after the first TOP_N - BACKGROUND_SLOTS (addendum M2, 【Claude】)
W_COVERAGE = 2.0
W_BM25 = 1.0
W_PERIOD = 0.5
W_COHORT = 0.3
MAX_QUERY_CHARS = 300
# Question words and particles, as norm_text writes them, longest first: v1's list (nknu.server.search._ZH_STOP)
# without 要, which this corpus's titles use in 要點 and 重要 (實施要點, 重要校聞), and without 請問 and 請教 (_OPENER);
# with 何時, 能否 and 幾 (fix round 3, 11-01). Fix round 2, 11-01: searched for, 什麼時候 made the long FAQ page that
# holds every question word outrank the documents that answer.
QUESTION_WORDS = tuple(sorted(dict.fromkeys(norm_text(w) for w in (
    "我想知道", "想知道", "我想", "我要", "知道", "如果", "的話", "什麼時候", "甚麼時候", "什麼", "甚麼", "怎麼", "怎樣",
    "如何", "何時", "哪裡", "哪裏", "哪些", "哪幾天", "哪一天", "哪天", "哪幾", "哪一", "哪個", "幾天", "是否", "是不是",
    "有沒有", "可不可以", "可以", "能不能", "能否", "會不會", "要不要", "還是", "還要", "到底", "自己", "那個", "這個",
    "那天", "之後", "之前", "幾號", "幾個", "幾次", "幾分", "幾科", "幾", "多少", "嗎", "呢", "吧", "啊", "呀", "喔", "的",
    "了", "誰", "是", "這", "那")), key=len, reverse=True))
# One pass, longest word first at each place. 何時 and 幾 inside other words stay (任何時間, 幾乎, 幾何). Each word
# leaves a gap, except 幾: it stands where a number would, inside the phrase it asks about, so the words around it close
# up (休幾學期 -> 休學期, which holds 休學).
_CLOSE_UP = norm_text("幾")
_ASKED = re.compile("|".join({norm_text("何時"): "(?<!任)何时", _CLOSE_UP: "几(?![乎何])"}.get(w, re.escape(w))
                             for w in QUESTION_WORDS))
# 請問 and 請教 open a question only where a clause starts, i.e. not right after a CJK character: inside 申請問題 and
# 申請教室 they are 申請 + 問題 / 教室; and 請教務處, 請教室 are 請 + 教務處 / 教室
_OPENER = re.compile(rf"(?<![{CJK}])(?:请问|请教(?![务室]))")

# step 1: every match scored once, each document's best PER_DOC chunks, the best POOL_DOCS documents by their best
# chunk (ties by doc_id); the tokens are read for the kept chunks only (_TOKENS)


def _fts_sql(only: str = "") -> str:
    """The keyword query: each document's best PER_DOC matched chunks, of the POOL_DOCS documents whose best chunk is
    best. only: a WHERE on the matched chunks' documents (c.doc_id), read before the two limits are counted."""
    return ("SELECT chunk_id, score FROM ("
            " SELECT chunk_id, score, DENSE_RANK() OVER (ORDER BY best, doc_id) AS d FROM ("
            "  SELECT m.chunk_id, m.score, c.doc_id,"
            "   ROW_NUMBER() OVER (PARTITION BY c.doc_id ORDER BY m.score, m.chunk_id) AS n,"
            "   MIN(m.score) OVER (PARTITION BY c.doc_id) AS best"
            "  FROM (SELECT rowid AS chunk_id, bm25(chunk_fts, ?, 1.0, ?) AS score FROM chunk_fts"
            "        WHERE chunk_fts MATCH ?) m"
            f"  JOIN chunks c ON c.chunk_id = m.chunk_id{only})"
            " WHERE n <= ?)"
            " WHERE d <= ? ORDER BY d, score")


_FTS = _fts_sql()
# plan 2026-11-14 T5 (RankConfig.topic_required): the same over the documents that have a chunk with a topic word
# (the second MATCH, which ranks nothing; its parameter follows the first MATCH's). Main session 11-14, after the
# first measurements: as first written the one MATCH was (topic) AND (every token). That asked the topic of each
# chunk and counted a topic word twice in bm25, so a document on the topic lost the chunks that only name the unit
# and went to the reranker with other chunks than before. The direction asks it of the document and leaves the
# score alone: 「讲什么的词至少对上一个，文件才进候选……进了候选的文件，这些词照样算进挑 300 份的那个分」.
_FTS_TOPIC = _fts_sql(" WHERE c.doc_id IN (SELECT t.doc_id FROM chunks t WHERE t.chunk_id IN"
                      " (SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ?))")
# the topic is asked of a chunk's own words, never of its unit words (chunk_fts.unit_tokens): 工程 would admit every
# page of 軟體工程與管理學系, the very unit-only match the setting leaves out (review 11-14). A column filter that names
# the two columns every database has; one before pipeline-5 keeps its unit words among the title tokens
_TOPIC_COLUMNS = "{{title_tokens body_tokens}} : ({})"
_TOKENS = "SELECT rowid, title_tokens, body_tokens{} FROM chunk_fts WHERE rowid IN ({})"  # {}: ', unit_tokens' or ''
# (a database before pipeline-5 has no such column: its unit words, if any, are title tokens)
# background material by source (addendum M2): docs.tier, which the build writes since plan 2026-11-08 T1 (archive
# members, journals and exams: build.tier_of). A database built before has no such column and keeps the 11-08 rule:
# archive members, which assemble wrote as a<序号>m<k> (T7a; doc_id da…m…), and the documents the build typed journal
# or exam. Never 「an attachment whose url has a #」: the files an SSO download button brought are exported at
# <list url>#按钮=… and are primary (review 11-08).
_BACKGROUND = "SELECT doc_id FROM docs WHERE doc_id IN ({}) AND tier = ?"
_BACKGROUND_OLD = ("SELECT doc_id FROM docs WHERE doc_id IN ({}) AND (doc_type IN (?, ?)"
                   " OR doc_id GLOB 'da[0-9]*m[0-9]*')")


@dataclass(frozen=True)
class RankConfig:
    """Ranking v2's date part (module docstring, step 2) and the tier (step 3). Date defaults chosen offline on 11-01
    (【Claude】, research/eval_2026-11-01/ranking_v2.md): most top-3 hits, fewest questions ranked lower, then the
    smallest weights. A date bonus should stay below what one missing query word costs (W_COVERAGE / number of
    words): 0.3 does up to 6 words. tier replaced type_prior, w_journal and w_exam on 11-08 (addendum M2).
    unit_names (owner 11-08 10:16): a unit's short name in the query brings its name and other short names (step 1).
    unit_weight and unit_cover (owner 11-08 10:16, N11; a database with chunk_fts.unit_tokens, pipeline-5): the bm25
    weight of the unit words' column (the owner: 「放进 body_tokens（1 倍权重）或者控制单位词在 BM25 里的权重」), and
    what a query word found only there counts in coverage 【Claude】 (1.0: as much as one in the document's own words;
    the unit words were title tokens before, weight TITLE_WEIGHT, counting fully). unit_names applies to any database
    with the unit tables, the 11-04 and 11-08 builds too: RankConfig(unit_names=False) ranks those as before.
    topic_required (plan 2026-11-14 T5; on since the owner's defaults of 11-14, 「找候选开」; False is the search of
    before): a candidate document must have one of the query's topic words; a unit's name and a term count for it as before but bring no document by
    themselves (module docstring, step 1; _topic, _FTS_TOPIC).
    copy_dates (plan 2026-11-14 T2, completed the same day; on by default): a document ranks by the latest identity
    date among it and the copies the build merged into it (docs.rank_date: module docstring, step 2), so merging
    ranks no document lower than before. False: by its own identity date, the search of before; a database without
    the column (built before, the first rebuild of 11-14 too) ranks so either way."""

    recency: bool = True
    w_recent: float = 0.3
    tau_days: float = 365
    no_date_factor: float = 0.5
    tier: bool = True
    unit_names: bool = True
    unit_weight: float = 1.0
    unit_cover: float = 0.5
    topic_required: bool = True
    copy_dates: bool = True


@dataclass
class _Doc:
    doc_id: str
    ay: int | None
    sem: int | None
    ident: str | None
    chunks: list[tuple[float, int]] = field(default_factory=list)  # (bm25, chunk_id)
    tokens: set[str] = field(default_factory=set)
    coverage: float = 0.0
    score: float = 0.0
    ranked: list[int] | None = None  # hybrid's chunk order (reranked picks first, then dense hits); None: by bm25
    background: bool = False  # step 3: an archive member, journal or exam (set when the tier applies)
    unit_tokens: set[str] = field(default_factory=set)  # its unit words' (chunk_fts.unit_tokens, pipeline-5)
    # plan 2026-11-14 T2: docs.rank_date, the latest identity date among it and the copies merged into it, when
    # RankConfig.copy_dates had it read (doc_rows); None: it ranks by ident, its own identity date (docs.ident_date),
    # as every document does with copy_dates off and in a database without the column
    rank_date: str | None = None
    # plan 2026-11-14 T2 (main session, the same day): docs.rank_undated, read with rank_date: the document is dated
    # but a copy merged into it has no date, so its date part is never under an undated document's (_date_part)
    rank_undated: bool = False

    @property
    def day(self) -> str | None:
        """The date it ranks by where the order alone asks (sort_docs' tie): rank_date when it has one, else its
        own."""
        return self.rank_date or self.ident

    @property
    def bm25(self) -> float:
        return min(self.chunks)[0] if self.chunks else 0.0

    @property
    def chunk_ids(self) -> list[int]:
        if self.ranked is not None:
            return list(self.ranked)
        return [c for _, c in sorted(self.chunks)]


@dataclass
class KeywordPool:
    """The keyword path's half of search_docs: what matched and how it scored (steps 1 and 2 of the module docstring).
    hybrid fuses it with the vector pool."""

    query: str  # the query as searched (cut to MAX_QUERY_CHARS)
    understood: Understood
    terms: list[Term]  # the terms it names, relative words resolved
    cohorts: list[int]  # the 入學 years it names
    recent: bool  # ranking v2's date part applies: rank.recency and no term named
    tokens: list[str]  # what the FTS query ORs; empty: nothing to search for ("!!!", "")
    docs: dict[str, _Doc]  # the matched documents, scored
    ordered: list[_Doc]  # docs in score order (sort_docs); search_docs' order is by_tier(ordered)
    rows: int  # kept chunks (SearchResult.candidates)
    tier: bool = True  # rank.tier: docs' background flags are set
    topic: list[str] = field(default_factory=list)  # plan 2026-11-14 T5: the tokens one of which a matched chunk had
    #                                                 to have; empty: none was asked for (the MATCH of before)


def _label(t: Term) -> str:
    return f"{t.ay}-{t.sem}" if t.sem else f"{t.ay}學年度"


def _words(rest: str, *, fallback: bool) -> list[str]:
    """The bigrams of the query's words (rest: norm_text, terms blanked) without its question words. When nothing else
    is left: the question words themselves if fallback (「什麼時候？」 alone), else none, and the query's terms and
    aliases are searched alone (fix round 3, 11-01: 「115-1 什麼時候？」 found the FAQ page and the school journal)."""
    asked = _OPENER.sub(" ", _ASKED.sub(lambda m: "" if m.group() == _CLOSE_UP else " ", rest))
    return list(dict.fromkeys(tokenize(asked) or (tokenize(rest) if fallback else [])))


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _period_match(terms: list[Term], ay: int | None, sem: int | None) -> float:
    if ay is None:
        return 0.0
    if any(t.ay == ay and t.sem == sem for t in terms):
        return 1.0
    return 0.6 if any(t.ay == ay for t in terms) else 0.0


def _coverage(words: list[str], tokens: set[str], alias_sets: list[tuple[set[str], list[set[str]]]],
              unit: set[str] | frozenset[str] = frozenset(), unit_cover: float = 1.0) -> float:
    """The share of words the document has (tokens; a word inside a matched alias when another member is all there);
    a word only its unit words have (unit, with tokens) counts unit_cover (RankConfig.unit_cover)."""
    if not words:
        return 0.0

    def found(w: str, have: set[str]) -> bool:
        return w in have or any(w in own and any(o and o <= have for o in others) for own, others in alias_sets)

    every = tokens | unit if unit else tokens
    return sum(1.0 if found(w, tokens) else unit_cover if unit and found(w, every) else 0.0 for w in words) / len(words)


def has_rank_date(conn: sqlite3.Connection) -> bool:
    """docs has rank_date (a database built since plan 2026-11-14 T2 was completed)."""
    return any(r[1] == "rank_date" for r in conn.execute("PRAGMA table_info(docs)"))


def doc_rows(conn: sqlite3.Connection, ids: list[str], *, copy_dates: bool = True) -> dict[str, _Doc]:
    """The documents of ids that exist, unscored and without chunks (hybrid: those only the vector path found), each
    with its own identity date and, with copy_dates (RankConfig.copy_dates; plan 2026-11-14 T2) in a database that
    has docs.rank_date, the date it ranks by. The default is RankConfig's: hybrid reads the documents only the
    vectors found with no RankConfig at hand, and adjustments, the first to read their dates, takes the rank dates
    away again when the setting is off."""
    if not ids:
        return {}
    columns = {r[1] for r in conn.execute("PRAGMA table_info(docs)")} if copy_dates else set()
    ranks_by = "rank_date" if "rank_date" in columns else "NULL"
    undated = "rank_undated" if "rank_undated" in columns else "0"  # with rank_date since the same day's fix
    return {r[0]: _Doc(r[0], r[1], r[2], r[3], rank_date=r[4], rank_undated=bool(r[5])) for r in conn.execute(
        f"SELECT doc_id, period_ay, period_sem, ident_date, {ranks_by}, {undated} FROM docs"
        f" WHERE doc_id IN ({_marks(len(ids))})", ids)}


def _candidates(conn: sqlite3.Connection, rows: list, copy_dates: bool = True) -> dict[str, _Doc]:
    """rows: (chunk_id, bm25) of the kept chunks; their documents (doc_rows, with copy_dates) with those chunks and
    their tokens."""
    ids = [r[0] for r in rows]
    owner = {r[0]: r[1] for r in conn.execute(
        f"SELECT chunk_id, doc_id FROM chunks WHERE chunk_id IN ({_marks(len(ids))})", ids)}
    unit = ", unit_tokens" if has_unit_tokens(conn) else ""
    tokens = {r[0]: r[1:] for r in conn.execute(_TOKENS.format(unit, _marks(len(ids))), ids)}
    docs = doc_rows(conn, list(dict.fromkeys(owner.values())), copy_dates=copy_dates)
    for rowid, score in rows:
        d = docs.get(owner.get(rowid, ""))
        if d is not None:
            d.chunks.append((score, rowid))
            title_tokens, body_tokens, *unit_tokens = tokens.get(rowid, ("", ""))
            d.tokens.update((title_tokens or "").split(), (body_tokens or "").split())
            d.unit_tokens.update(*((u or "").split() for u in unit_tokens))
    return docs


def has_unit_tokens(conn: sqlite3.Connection) -> bool:
    """chunk_fts has the unit words' column (a database built since pipeline-5)."""
    return any(r[1] == "unit_tokens" for r in conn.execute("PRAGMA table_info(chunk_fts)"))


def _event_ends(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    """Calendar events' last days (ISO); an event has no identity date."""
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT doc_id, max(date) FROM dates WHERE kind = 'event_end' AND doc_id IN ({_marks(len(ids))})"
        " GROUP BY doc_id", ids)}


def background_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """Step 3: the documents of ids that are background material (archive members, journals, exams, meeting minutes):
    docs.tier, or the 11-08 rule in a database without that column (no minutes there)."""
    tiered = any(r[1] == "tier" for r in conn.execute("PRAGMA table_info(docs)"))
    sql, args = (_BACKGROUND, [BACKGROUND]) if tiered else (_BACKGROUND_OLD, [JOURNAL, EXAM])
    out: set[str] = set()
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        out.update(r[0] for r in conn.execute(sql.format(_marks(len(part))), [*part, *args]))
    return out


def mark_background(conn: sqlite3.Connection, docs: dict[str, _Doc]) -> None:
    """Sets each document's background flag (step 3)."""
    found = background_ids(conn, list(docs))
    for d in docs.values():
        d.background = d.doc_id in found


def tiered(main: list[_Doc], background: list[_Doc]) -> list[_Doc]:
    """Step 3's order of two ordered parts: the first TOP_N - BACKGROUND_SLOTS of main, the first BACKGROUND_SLOTS of
    background, the rest of main, the rest of background."""
    k = TOP_N - BACKGROUND_SLOTS
    return main[:k] + background[:BACKGROUND_SLOTS] + main[k:] + background[BACKGROUND_SLOTS:]


def by_tier(ordered: list[_Doc]) -> list[_Doc]:
    """tiered over ordered's primary and background documents, each part keeping ordered's order."""
    return tiered([d for d in ordered if not d.background], [d for d in ordered if d.background])


def mixed(main: list[_Doc], background: list[_Doc], factor: float) -> list[_Doc]:
    """spec 2026-11-16 §四 (hybrid.HybridConfig.background_factor): the order of two reranked parts, each in score
    order, where a background document stands by its score times factor among the primary documents' scores (on equal
    values the primary one first; within a part the part's own order). At most BACKGROUND_SLOTS background documents
    stand among the first TOP_N: one past that waits behind them, ahead of whatever scored less. No document's own
    score changes. Why: by tiered, a background document asked for by name (an exam paper, a form inside an archive,
    an alumni page) came ninth although it scored above every primary document: 91 of 100 on the official library of
    11-15 (research/pipeline_2026-11-02/etl_review_1115/check.md)."""
    def weight(d):
        return d.score * factor if d.background else d.score

    merged = sorted([*main, *background], key=weight, reverse=True)  # stable: main first on equal values
    top: list[_Doc] = []
    later: list[_Doc] = []
    held = 0
    for d in merged:
        if len(top) < TOP_N and not (d.background and held >= BACKGROUND_SLOTS):
            top.append(d)
            held += bool(d.background)
        else:
            later.append(d)
    return top + later


def _recency(rank: RankConfig, day: str | None, today: date) -> float:
    if not day:
        return rank.w_recent * rank.no_date_factor
    return rank.w_recent * math.exp(-max(0, (today - date.fromisoformat(day)).days) / rank.tau_days)


def _date_part(rank: RankConfig, d: _Doc, end: str | None, today: date) -> float:
    """Ranking v2's date part of one document's score: _recency of its own identity date, else of end (a calendar
    event's last day), else of no date, as always. With a rank date (_Doc.rank_date: plan 2026-11-14 T2,
    RankConfig.copy_dates), the higher of that and what the rank date gives 【Claude: the higher, where the main
    session's brief of 11-14 says to read the rank date whenever there is one】. For a document dated itself that is
    the rank date's, which is never earlier than its own. For one kept without a date beside a dated copy (a primary
    file before an archive member) no date can give more than an old date does (no_date_factor), and it keeps that:
    the aim of the correction, that merging ranks no document lower than before, holds for it too. A document whose
    rank date is its own scores to the bit as before."""
    own = _recency(rank, d.ident or end, today)
    best = max(own, _recency(rank, d.rank_date, today)) if d.rank_date else own
    # main session, 11-14: a dated document with an undated copy merged into it (docs.rank_undated: a rule kept on a
    # standing page beside the notice that posted it) gets no less than that copy got alone, the no-date share
    return max(best, _recency(rank, None, today)) if d.rank_undated else best


def _cohort_docs(conn: sqlite3.Connection, ids: list[str], cohorts: list[int]) -> set[str]:
    """Documents that apply to one of the 入學 years: cohort_exact == X or cohort_from <= X."""
    if not cohorts or not ids:
        return set()
    return {r[0] for r in conn.execute(
        f"SELECT doc_id, cohort_from, cohort_exact FROM applies_to WHERE doc_id IN ({_marks(len(ids))})", ids)
        if any(r[2] == x or (r[1] is not None and r[1] <= x) for x in cohorts)}


def _hit(d: _Doc) -> DocHit:
    return DocHit(d.doc_id, d.score, d.coverage, d.bm25, d.chunk_ids, d.background)


def top_hits(ordered: list[_Doc], limit: int) -> list[DocHit]:
    """Step 4 of the module docstring: the first limit documents of ordered, each its own hit with its own score and
    chunks (no series since 11-03)."""
    return [_hit(d) for d in ordered[: max(limit, 0)]]


def sort_docs(docs: Iterable[_Doc]) -> list[_Doc]:
    """search_docs' order: score high to low, ties newest identity date first (no date last), then doc_id. The date
    is the one a document ranks by (_Doc.day: its rank date when RankConfig.copy_dates had one read, plan 2026-11-14
    T2; else its own, as before)."""
    ordered = sorted(docs, key=lambda d: d.doc_id)
    ordered.sort(key=lambda d: d.day or "", reverse=True)
    ordered.sort(key=lambda d: d.score, reverse=True)
    return ordered


def unit_names(conn: sqlite3.Connection) -> list[list[str]]:
    """The unit tree's groups for alias expansion (aliases.expand's names): each unit below the school that has short
    names, its name first, then those (the build's tables units and unit_aliases, plan 2026-11-02 T7d); [] without
    them. The school gives none 【Claude】, as on the unit line (build.unit_words): 高師大 would bring 國立高雄師範大學's
    大學, 高雄 ... to every query that names the school."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN"
                                       " ('units', 'unit_aliases')")}
    if have != {"units", "unit_aliases"}:
        return []
    short: dict[str, list[str]] = {}
    for alias, unit_id in conn.execute("SELECT alias, unit_id FROM unit_aliases ORDER BY rowid"):
        short.setdefault(unit_id, []).append(alias)
    out = []
    for unit_id, name in conn.execute("SELECT unit_id, name FROM units WHERE parent_id IS NOT NULL ORDER BY rowid"):
        group = list(dict.fromkeys([name, *(a for a in short.get(unit_id, ()) if a and tokenize(a))]))
        if len(group) > 1 and tokenize(name):
            out.append(group)
    return out


def unit_spellings(conn: sqlite3.Connection) -> list[str]:
    """Plan 2026-11-14 T5: every name the unit tree has for a unit, each once: the units' names and short names (the
    build's tables units and unit_aliases, plan 2026-11-02 T7d); [] without them. Not unit_names, which is what a
    short name brings and so leaves out the school and the units without a short name: here the question is what in a
    query is a unit's name, and 教務處 (no short name) and 高師大 (the school) say whose a thing is as 軟體系 does."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN"
                                       " ('units', 'unit_aliases')")}
    if have != {"units", "unit_aliases"}:
        return []
    names = [r[0] for r in conn.execute("SELECT name FROM units ORDER BY rowid")]
    names += [r[0] for r in conn.execute("SELECT alias FROM unit_aliases ORDER BY rowid")]
    return [n for n in dict.fromkeys(names) if n and tokenize(n)]


def _topic(rest: str, tokens: list[str], units: list[tuple[int, int]], others: list[str]) -> list[str]:
    """Plan 2026-11-14 T5 (direction 2026-11-13 第 5 件): the query's topic words, the tokens one of which a candidate
    must have, or [] when the query is searched as before. rest: norm_text(query) with its terms blanked
    (QueryTerms.rest); tokens: everything the query searches for; units: where it writes a unit's name
    (aliases.unit_spans); others: what the alias table brings (aliases.synonyms).

    Topic words are the bigrams and ASCII words of rest outside the unit names and the question words, and others (a
    synonym of a topic word is one). So a unit's name, what its short name brings, a term's canonical tokens and a
    bigram across a name's edge (軟體系學雜費's 系學) are no topic words: they are in tokens and count as before.
    【Claude】 A single character is no topic word (有 in 軟體系有哪些課程: in the index a lone character is one that
    stands apart, which says nothing of the topic), and no reason for the conjunction either.
    [] when there is no topic word (a unit's name alone, a term alone, question words alone) or nothing else of more
    than one character is searched for: asking for a topic word would then leave out no document for its unit or
    term, and the query of before is run."""
    kept = list(rest)
    for start, end in units:
        kept[start:end] = " " * (end - start)
    searched = set(tokens)
    words = [w for w in _words("".join(kept), fallback=False) if len(w) > 1]
    topic = [t for t in dict.fromkeys(words + others) if t in searched]
    apart = searched.difference(topic)  # a unit's, a term's, a name's edge; single characters
    return topic if topic and any(len(t) > 1 for t in apart) else []


def keyword_pool(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]],
                 rank: RankConfig | None = None, explain: dict | None = None) -> KeywordPool:
    """Steps 1 and 2: the FTS match and each document's score, added up exactly as search_docs always has (the float
    sums decide ties that the stable sorts and the 11-01 offline numbers rely on).
    explain (plan 2026-11-15 T1, the inspection tools: nknu.ops.explain): a dict that is given gets 'keyword': the
    tokens searched, the topic words, whether the date part applies, the best bm25, and each document's parts as they
    were added (coverage, bm25, bm25_norm, period, cohort, date or None, score). Without it nothing changes."""
    q = query[:MAX_QUERY_CHARS]
    rank = rank if rank is not None else RankConfig()
    named = query_terms(q, today)
    canon = canonical_tokens(q, today)
    names = unit_names(conn) if rank.unit_names else []
    extra, matched = expand(q, aliases, names)
    words = _words(named.rest, fallback=not (canon or matched))
    cohorts = list(dict.fromkeys(ay for ay, _ in named.cohorts))
    understood = Understood(words, canon, matched, [_label(t) for t in named.terms], cohorts)
    tokens = list(dict.fromkeys(words + canon + extra))
    recent = rank.recency and not named.terms
    topic: list[str] = []
    if rank.topic_required and tokens:  # plan 2026-11-14 T5; the unit tree is read whatever rank.unit_names says
        spans = unit_spans(q, unit_spellings(conn))
        topic = _topic(named.rest, tokens, spans, synonyms(q, aliases, names, spans))
    if not tokens:
        rows = []
    elif topic:
        rows = conn.execute(_FTS_TOPIC, (TITLE_WEIGHT, rank.unit_weight, fts_query(tokens),
                                         _TOPIC_COLUMNS.format(fts_query(topic)), PER_DOC, POOL_DOCS)).fetchall()
    else:
        rows = conn.execute(_FTS, (TITLE_WEIGHT, rank.unit_weight, fts_query(tokens), PER_DOC, POOL_DOCS)).fetchall()
    if not rows:
        if explain is not None:
            explain["keyword"] = {"tokens": tokens, "topic": topic, "recent": recent, "best_bm25": 0.0, "docs": {}}
        return KeywordPool(q, understood, named.terms, cohorts, recent, tokens, {}, [], 0, rank.tier, topic)

    docs = _candidates(conn, rows, rank.copy_dates)
    alias_sets = [(set(tokenize(m)), [set(tokenize(o)) for g in [*aliases, *names] if m in g for o in g if o != m])
                  for m in matched]
    applies = _cohort_docs(conn, list(docs), cohorts)
    ends = _event_ends(conn, list(docs)) if recent else {}
    if rank.tier:
        mark_background(conn, docs)
    best = min((d.bm25 for d in docs.values() if d.chunks), default=0.0)
    noted: dict[str, dict] = {}
    for d in docs.values():
        d.coverage = _coverage(words or canon, d.tokens, alias_sets, d.unit_tokens, rank.unit_cover)
        bm25_norm = min(1.0, max(0.0, d.bm25 / best)) if best < 0 else 0.0
        period, cohort = _period_match(named.terms, d.ay, d.sem), d.doc_id in applies
        d.score = (W_COVERAGE * d.coverage + W_BM25 * bm25_norm + W_PERIOD * period
                   + W_COHORT * cohort)
        dated = _date_part(rank, d, ends.get(d.doc_id), today) if recent else None
        if dated is not None:
            d.score += dated
        if explain is not None:
            noted[d.doc_id] = {"coverage": d.coverage, "bm25": d.bm25, "bm25_norm": bm25_norm, "period": period,
                               "cohort": cohort, "date": dated, "score": d.score}
    if explain is not None:
        explain["keyword"] = {"tokens": tokens, "topic": topic, "recent": recent, "best_bm25": best, "docs": noted}
    return KeywordPool(q, understood, named.terms, cohorts, recent, tokens, docs, sort_docs(docs.values()), len(rows),
                       rank.tier, topic)


def adjustments(conn: sqlite3.Connection, docs: dict[str, _Doc], pool: KeywordPool, rank: RankConfig,
                today: date, *, recency: bool = True, parts: dict | None = None) -> dict[str, float]:
    """doc_id -> the part of its score that is not keyword relevance: W_PERIOD*period_match + W_COHORT*cohort_match
    and the ranking-v2 date part when pool.recent (hybrid adds it to the reranker's relevance). recency=False leaves
    the date part out (plan 2026-11-14 T6: hybrid then multiplies by how valid the document still is, so the date
    is not counted twice).
    With rank.copy_dates off, each of docs loses its rank date here (plan 2026-11-14 T2): hybrid reads the documents
    only the vectors found with doc_rows' default, having no RankConfig to pass it, and this is the first place that
    reads their dates and the one hybrid hands the RankConfig, before it sorts the reranked head (sort_docs). So with
    the setting off the date part and that sort's tie read each document's own date, as before, whoever read the
    rows 【Claude: hybrid.py was not to be edited; passing copy_dates to its doc_rows call would do the same】.
    parts (plan 2026-11-15 T1, the inspection tools): a dict that is given gets each document's three addends as they
    were added: 'period', 'cohort' and 'date' (None when the date part is left out). Without it nothing changes."""
    if not rank.copy_dates:
        for d in docs.values():
            d.rank_date = None
            d.rank_undated = False
    dated = pool.recent and recency
    ids = list(docs)
    applies = _cohort_docs(conn, ids, pool.cohorts)
    ends = _event_ends(conn, ids) if dated and ids else {}
    out: dict[str, float] = {}
    for d in docs.values():
        period, cohort = W_PERIOD * _period_match(pool.terms, d.ay, d.sem), W_COHORT * (d.doc_id in applies)
        a = period + cohort
        day = _date_part(rank, d, ends.get(d.doc_id), today) if dated else None
        if day is not None:
            a += day
        if parts is not None:
            parts[d.doc_id] = {"period": period, "cohort": cohort, "date": day}
        out[d.doc_id] = a
    return out


def search_docs(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]],
                limit: int = TOP_N, rank: RankConfig | None = None) -> SearchResult:
    pool = keyword_pool(conn, query, today=today, aliases=aliases, rank=rank)
    if not pool.rows:
        return SearchResult(pool.query, pool.understood, [], 0)
    return SearchResult(pool.query, pool.understood, top_hits(by_tier(pool.ordered), limit), pool.rows)
