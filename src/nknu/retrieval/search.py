"""Bigram search over the content database (plan 2026-11-01, Task A). No LLM.

1. One FTS5 MATCH ORs the query's words (textnorm bigrams), its terms and cohorts as canonical tokens (115-1,
   115學年度第1學期, 115學年度上學期 and 這學期 are all term115s1) and its alias expansions. The term, cohort and
   relative-word spans are taken out of the words first, so how a term is spelled changes nothing; so are question
   words (什麼時候, 怎麼, 嗎 ...: they say that something is asked, not what; 請問 and 請教 only where a clause starts).
   When nothing else is left, the query's terms and aliases are searched alone, and the question words only when the
   query has neither.
   Each document keeps its best PER_DOC matched chunks by bm25, and the pool keeps the best POOL_DOCS documents by
   their best chunk (owner 11-08, plan addendum M1: a long document no longer fills the pool or gains coverage from
   many chunks).
2. The kept chunks are grouped by document:
   score = W_COVERAGE*coverage + W_BM25*bm25_norm + W_PERIOD*period_match + W_COHORT*cohort_match,
   ties newest identity date first (no date last), then doc_id.
   coverage: share of the query's words in the document's kept chunks (its canonical tokens when the query is
   only terms); a word inside a matched alias counts when another member of the alias group is all there.
   bm25_norm: the document's best chunk bm25 over the best of all candidates.
   period_match: 1.0 when the query names the document's (學年度, 學期), 0.6 when only the year matches.
   cohort_match: 1.0 when a 入學 year the query names is one the document applies to.
   Ranking v2 (spec 「排序 v2」, 11-01; RankConfig) adds, when the query names no term or year (a cohort is not one):
   w_recent * exp(-days before today / tau_days) of the identity date, else of a calendar event's last day (a day
   after today counts as today), else no_date_factor * w_recent.
3. By tier (owner 11-08, addendum M2; RankConfig.tier): archive members, journals and exams are background material
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
from nknu.retrieval.aliases import expand
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
_FTS = ("SELECT chunk_id, score FROM ("
        " SELECT chunk_id, score, DENSE_RANK() OVER (ORDER BY best, doc_id) AS d FROM ("
        "  SELECT m.chunk_id, m.score, c.doc_id,"
        "   ROW_NUMBER() OVER (PARTITION BY c.doc_id ORDER BY m.score, m.chunk_id) AS n,"
        "   MIN(m.score) OVER (PARTITION BY c.doc_id) AS best"
        "  FROM (SELECT rowid AS chunk_id, bm25(chunk_fts, ?, 1.0) AS score FROM chunk_fts WHERE chunk_fts MATCH ?) m"
        "  JOIN chunks c ON c.chunk_id = m.chunk_id)"
        " WHERE n <= ?)"
        " WHERE d <= ? ORDER BY d, score")
_TOKENS = "SELECT rowid, title_tokens, body_tokens FROM chunk_fts WHERE rowid IN ({})"
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
    words): 0.3 does up to 6 words. tier replaced type_prior, w_journal and w_exam on 11-08 (addendum M2)."""

    recency: bool = True
    w_recent: float = 0.3
    tau_days: float = 365
    no_date_factor: float = 0.5
    tier: bool = True


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


def _coverage(words: list[str], tokens: set[str], alias_sets: list[tuple[set[str], list[set[str]]]]) -> float:
    if not words:
        return 0.0
    covered = sum(1 for w in words if w in tokens
                  or any(w in own and any(o and o <= tokens for o in others) for own, others in alias_sets))
    return covered / len(words)


def doc_rows(conn: sqlite3.Connection, ids: list[str]) -> dict[str, _Doc]:
    """The documents of ids that exist, unscored and without chunks (hybrid: those only the vector path found)."""
    if not ids:
        return {}
    return {r[0]: _Doc(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT doc_id, period_ay, period_sem, ident_date FROM docs"
        f" WHERE doc_id IN ({_marks(len(ids))})", ids)}


def _candidates(conn: sqlite3.Connection, rows: list) -> dict[str, _Doc]:
    """rows: (chunk_id, bm25) of the kept chunks; their documents with those chunks and their tokens."""
    ids = [r[0] for r in rows]
    owner = {r[0]: r[1] for r in conn.execute(
        f"SELECT chunk_id, doc_id FROM chunks WHERE chunk_id IN ({_marks(len(ids))})", ids)}
    tokens = {r[0]: (r[1], r[2]) for r in conn.execute(_TOKENS.format(_marks(len(ids))), ids)}
    docs = doc_rows(conn, list(dict.fromkeys(owner.values())))
    for rowid, score in rows:
        d = docs.get(owner.get(rowid, ""))
        if d is not None:
            d.chunks.append((score, rowid))
            title_tokens, body_tokens = tokens.get(rowid, ("", ""))
            d.tokens.update(title_tokens.split(), body_tokens.split())
    return docs


def _event_ends(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    """Calendar events' last days (ISO); an event has no identity date."""
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT doc_id, max(date) FROM dates WHERE kind = 'event_end' AND doc_id IN ({_marks(len(ids))})"
        " GROUP BY doc_id", ids)}


def background_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """Step 3: the documents of ids that are background material (archive members, journals, exams): docs.tier, or
    the 11-08 rule in a database without that column."""
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


def _recency(rank: RankConfig, day: str | None, today: date) -> float:
    if not day:
        return rank.w_recent * rank.no_date_factor
    return rank.w_recent * math.exp(-max(0, (today - date.fromisoformat(day)).days) / rank.tau_days)


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
    """search_docs' order: score high to low, ties newest identity date first (no date last), then doc_id."""
    ordered = sorted(docs, key=lambda d: d.doc_id)
    ordered.sort(key=lambda d: d.ident or "", reverse=True)
    ordered.sort(key=lambda d: d.score, reverse=True)
    return ordered


def keyword_pool(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]],
                 rank: RankConfig | None = None) -> KeywordPool:
    """Steps 1 and 2: the FTS match and each document's score, added up exactly as search_docs always has (the float
    sums decide ties that the stable sorts and the 11-01 offline numbers rely on)."""
    q = query[:MAX_QUERY_CHARS]
    named = query_terms(q, today)
    canon = canonical_tokens(q, today)
    extra, matched = expand(q, aliases)
    words = _words(named.rest, fallback=not (canon or matched))
    cohorts = list(dict.fromkeys(ay for ay, _ in named.cohorts))
    understood = Understood(words, canon, matched, [_label(t) for t in named.terms], cohorts)
    tokens = list(dict.fromkeys(words + canon + extra))
    rank = rank if rank is not None else RankConfig()
    recent = rank.recency and not named.terms
    rows = conn.execute(_FTS, (TITLE_WEIGHT, fts_query(tokens), PER_DOC, POOL_DOCS)).fetchall() if tokens else []
    if not rows:
        return KeywordPool(q, understood, named.terms, cohorts, recent, tokens, {}, [], 0, rank.tier)

    docs = _candidates(conn, rows)
    alias_sets = [(set(tokenize(m)), [set(tokenize(o)) for g in aliases if m in g for o in g if o != m])
                  for m in matched]
    applies = _cohort_docs(conn, list(docs), cohorts)
    ends = _event_ends(conn, list(docs)) if recent else {}
    if rank.tier:
        mark_background(conn, docs)
    best = min((d.bm25 for d in docs.values() if d.chunks), default=0.0)
    for d in docs.values():
        d.coverage = _coverage(words or canon, d.tokens, alias_sets)
        bm25_norm = min(1.0, max(0.0, d.bm25 / best)) if best < 0 else 0.0
        d.score = (W_COVERAGE * d.coverage + W_BM25 * bm25_norm + W_PERIOD * _period_match(named.terms, d.ay, d.sem)
                   + W_COHORT * (d.doc_id in applies))
        if recent:
            d.score += _recency(rank, d.ident or ends.get(d.doc_id), today)
    return KeywordPool(q, understood, named.terms, cohorts, recent, tokens, docs, sort_docs(docs.values()), len(rows),
                       rank.tier)


def adjustments(conn: sqlite3.Connection, docs: dict[str, _Doc], pool: KeywordPool, rank: RankConfig,
                today: date) -> dict[str, float]:
    """doc_id -> the part of its score that is not keyword relevance: W_PERIOD*period_match + W_COHORT*cohort_match
    and the ranking-v2 date part when pool.recent (hybrid adds it to the reranker's relevance)."""
    ids = list(docs)
    applies = _cohort_docs(conn, ids, pool.cohorts)
    ends = _event_ends(conn, ids) if pool.recent and ids else {}
    out: dict[str, float] = {}
    for d in docs.values():
        a = W_PERIOD * _period_match(pool.terms, d.ay, d.sem) + W_COHORT * (d.doc_id in applies)
        if pool.recent:
            a += _recency(rank, d.ident or ends.get(d.doc_id), today)
        out[d.doc_id] = a
    return out


def search_docs(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]],
                limit: int = TOP_N, rank: RankConfig | None = None) -> SearchResult:
    pool = keyword_pool(conn, query, today=today, aliases=aliases, rank=rank)
    if not pool.rows:
        return SearchResult(pool.query, pool.understood, [], 0)
    return SearchResult(pool.query, pool.understood, top_hits(by_tier(pool.ordered), limit), pool.rows)
