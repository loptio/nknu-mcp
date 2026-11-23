"""Hybrid retrieval (plan 2026-11-02 T8, spec §3.4; addendum M, owner 11-08): the bigram keyword pool and the bge-m3
vector pool are fused per document by reciprocal rank fusion (RRF), the first primary and the first background
documents are reranked by bge-reranker-v2-m3 on at most chunks_per_doc chunks each, the ranking-v2 adjustments are
added, and the result is in search.py's tier order, each document on its own (no series since the owner's 11-03
decision, addendum L). Both models only return scores: the serving path never calls a text-generating model.

1. keyword_pool (search.py): the FTS match, each document scored as search_docs scores it and marked background or
   not (search.py step 3). Nothing to search for ("!!!", ""): no hits, and no model is asked for.
2. The audience (scope_factor; the unit tree and doc_scope of plan 2026-11-02 T7d): the keyword score of a document
   whose audience is not the asker's (off_audience) is multiplied by scope_factor before the keyword order is taken,
   so the school's documents reach the rerank head; a database without the unit tables changes nothing.
3. The keyword order (search_docs' own, the audience factor aside) with ranking "keyword", so the answer carries
   respond's NOTE_RANKING, and no model is asked for (main session 11-03, addendum J: the plan's Review Focus 1 over
   T8's pseudo-code), when
   - both models are off;
   - the database has no chunk_vecs (one built before this plan, such as the skeleton's; the reranker could not read
     its chunks either);
   - the dense path is on and the database has no usable vectors of the pinned model, the embedder's model and
     revision: vectors.index_for gives none (a build without --embed, partial vectors, another format) or cannot read
     them, or another model or revision wrote them.
   Dense off with rerank on (the server's --no-dense) is the explicit rerank-only mode: it reads no vectors and asks
   for the reranker whatever they are.
4. Dense: the query written the school's way (terms.school_spelling: 115-1, 115學年度第1學期 and 這學期 send one
   text) is embedded, and the index gives the dense_candidates nearest primary documents and the dense_candidates
   nearest background ones, each with its PER_DOC nearest chunks (VectorIndex.top_docs; one pool when the tier is off).
   Until 11-08 it was the 300 nearest chunks of the whole database, so background material (about 70 % of the
   chunks) and long documents filled it (the owner's review, plan addendum M7). Any failure (ModelUnavailable, a
   partial cache, an inference error such as MPS out of memory, a query vector of another width than the index's)
   leaves the dense pool empty; the reranker still reads the keyword candidates.
5. RRF within each tier, over its keyword document order and its dense document order (each document at its best
   chunk): fused score high to low, ties by keyword rank, then doc_id. With no dense hit this is the keyword order.
6. Rerank: the first rerank_docs primary and the first rerank_background background documents in RRF order (addendum
   M3: background material no longer takes the head's places), each on its first chunks_per_doc keyword chunks (by
   bm25) and its first chunks_per_doc dense chunks (by similarity), deduped, at most chunks_per_doc + 1 when it has
   both (_picks; until 11-08 the two lists were interleaved to chunks_per_doc, so a dense chunk pushed out the second
   keyword chunk), all in one reranker call. A document's relevance is its best chunk's score as it comes (addendum
   M4: an unsure head stays unsure), its score becomes the one below, and its chunks are ordered by the reranker's
   scores. In each tier the head is sorted as search_docs sorts and the other documents follow in RRF order. A
   failure leaves the RRF order and the keyword scores.
7. The two tiers in one order, then top_hits (search.py): the first limit documents, each its own hit. Reranked: the
   two heads by search.mixed (spec 2026-11-16 §四, background_factor), then the rest of each tier; else, and with
   background_factor 0, search.tiered's fixed places.

A reranked document's score (step 6; direction 2026-11-13 第一节: useful = on the topic x still valid x right
audience) is
    (w_rerank * relevance + adjustments) * valid * scope
- adjustments (search.adjustments): the period and cohort matches and ranking v2's date part.
- valid (valid_decay; plan 2026-11-14 T6): when the query names no term (pool.recent) and the database has
  docs.expires and docs.valid_ay, a document that goes out of date counts less the older it is (validity: a whole
  step of valid_decay for each academic year past when its title names its year, by its own age otherwise); else 1.
- scope (scope_factor; plan 2026-11-14 T7): scope_factor for a document whose audience is not the asker's
  (off_audience), else 1; the same factor as in step 2.

SearchResult.ranking is "hybrid" when dense hits or rerank scores shaped the order, else "keyword"; best_rerank is the
head's highest raw rerank score. No threshold (11-01): the dense path finds neighbours for any query with words.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date

from nknu.content.embed import chunk_passages
from nknu.content.terms import current_term, school_spelling
from nknu.content.units import TEACHING_KINDS, Scope, Unit, detect, relation
from nknu.core import models
from nknu.retrieval import unitfilter, vectors
from nknu.retrieval.search import (
    CANDIDATES,
    PER_DOC,
    TOP_N,
    W_BM25,
    W_COVERAGE,
    KeywordPool,
    RankConfig,
    _cohort_docs,
    _Doc,
    adjustments,
    by_tier,
    doc_rows,
    keyword_pool,
    mark_background,
    mixed,
    sort_docs,
    tiered,
    top_hits,
)
from nknu.retrieval.types import SearchResult
from nknu.retrieval.unitfilter import UnitFilter

log = logging.getLogger(__name__)

_IN_MAX = 500  # ids per IN (...) query


@dataclass(frozen=True)
class HybridConfig:
    """The fusion's settings (plan 2026-11-02 T8, spec §3.4). Starting values 【Claude】, none measured on our data
    before the first pipeline build: T10 compares them offline."""

    dense: bool = True
    rerank: bool = True
    rrf_k: int = 60  # spec §3.4 「常用 60」
    dense_candidates: int = CANDIDATES  # documents per tier from the vector path (300), PER_DOC chunks each
    rerank_docs: int = 50  # primary documents by RRF that the reranker reads (was 30 of any tier; addendum M3)
    rerank_background: int = 10  # background documents by RRF that it reads too (addendum M3)
    chunks_per_doc: int = 2  # each document's keyword chunks and dense chunks the reranker reads (_picks): at most
    #                            3 per document, 180 passages
    w_rerank: float = W_COVERAGE + W_BM25  # 3.0: rerank relevance (0-1) spans what keyword relevance spans
    # plan 2026-11-14 T6 and T7 (module docstring): both count by default (the report they were set after is
    # research/pipeline_2026-11-02/v6_1114/summary.md). The numbers themselves are judged, not measured (0.85: Li and
    # Croft 2003 gives 0.79-0.89 a year; the direction wrote 0.8 for the audience, the title self-search of 150
    # department notices made it 0.9).
    scope_factor: float = 0.9  # T7: times the scores of a document off the asker's audience (off_audience); 1: off
    valid_decay: float = 0.85  # T6: per year, times a reranked document that goes out of date (validity); 1: off
    # spec 2026-11-16 §四 (the owner 11-16, 「可以，先写程序」 on the design; the design's section 七 item 3 changes the
    # owner's 11-08 「8 份主料＋2 份背景」): a reranked background document's score times this stands among the primary
    # documents' scores (search.mixed), at most BACKGROUND_SLOTS of them among the first TOP_N. 0.8 【Claude: a
    # judgment, not fitted to the 33 questions; with it 2 of the 33 get a journal article or minutes among their first
    # three, and 72 of 100 background documents asked for by their own title pass the third primary one】. 0: the
    # fixed places of before (search.tiered), which keyword-only ranking keeps in any case
    background_factor: float = 0.8

    def __post_init__(self) -> None:
        # a discount: 85 for 0.85 would turn the order over
        for name, off in (("scope_factor", 1), ("valid_decay", 1), ("background_factor", 0)):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be from 0 to 1 ({off}: off), not {getattr(self, name)!r}")


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    """Reciprocal rank fusion: each id's sum over the rankings of 1 / (k + its rank), ranks counted from 1."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return fused


def _fused(kw_ids: list[str], dense_ids: list[str], k: int) -> list[str]:
    """Step 5 for one tier: RRF over its two orders, fused score high to low, ties by keyword rank, then doc_id."""
    fused = rrf([kw_ids, dense_ids], k)
    kw_rank = {d: i for i, d in enumerate(kw_ids)}
    return sorted(fused, key=lambda d: (-fused[d], kw_rank.get(d, len(kw_ids)), d))


def rerank_passages(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, str]:
    """chunk_id -> the text the reranker reads: embed.passage of its document's title, unit line (plan 2026-11-08 T4;
    none in a database built before), parent title and link texts, its section heading, the header of the table it
    starts inside and the chunk, exactly as bge-m3 embedded it."""
    return chunk_passages(conn, chunk_ids)


# ---------------------------------------------------------------- the unit tree

def scope_units(conn: sqlite3.Connection) -> dict[str, Unit]:
    """The unit tree the build stored (T7d: units, and unit_aliases holding each unit's name and aliases); {} for a
    database without it."""
    if not (vectors.has_table(conn, "units") and vectors.has_table(conn, "unit_aliases")):
        return {}
    aliases: dict[str, list[str]] = {}
    for alias, unit_id in conn.execute("SELECT alias, unit_id FROM unit_aliases"):
        aliases.setdefault(unit_id, []).append(alias)
    return {unit_id: Unit(unit_id, name, kind, parent_id, campus,
                          tuple(dict.fromkeys(a for a in aliases.get(unit_id, ()) if a and a != name)))
            for unit_id, name, kind, parent_id, campus in conn.execute(
                "SELECT unit_id, name, kind, parent_id, campus FROM units")}


def _keyword_order(pool: KeywordPool, off: set[str] | frozenset[str] = frozenset(), factor: float = 1.0) -> list[_Doc]:
    """pool.ordered; or, with the keyword documents of off (off_audience) multiplied by factor
    (HybridConfig.scope_factor; plan 2026-11-14 T7), the pool's documents ordered again the same way."""
    scaled = [d for d in off if d in pool.docs] if factor != 1.0 else []
    if not scaled:
        return pool.ordered
    for d in scaled:
        pool.docs[d].score *= factor
    return sort_docs(pool.docs.values())


# ---------------------------------------------------------------- useful = on topic x valid x audience (plan 2026-11-14)

def off_audience(conn: sqlite3.Connection, query: str, ids: list[str]) -> set[str]:
    """Plan 2026-11-14 T7 (direction 2026-11-13 第 7 件, 「对口」): the documents of ids whose audience is not the
    asker's; HybridConfig.scope_factor multiplies their scores. By the unit tree and doc_scope's rows; set() for a
    database without either.
    - The query names a unit below the school and units.relation(the document's scope, the query's) is -1 (「程序里
      现成的判断」): another department's document, and, as relation has it, another campus's or level's. The scope is
      the document's applies_to rows; a document without such rows is never one.
    - The query names no college, department or programme (units.TEACHING_KINDS) and the document's publisher is
      one: the direction's 「系所、学院、学程发的，只算它自己的；处室、组、中心发的，算全校适用」 (units.reach).
    Main session 11-14, after the first measurement: as first written the second read the applies_to rows (only
    colleges, departments or programmes named there), and those are the publisher's reach NARROWED by any such unit
    the title or the text's first lines name. On the 111-site database 5,790 of the 36,925 documents that counted
    were an office's, a section's or the school's that merely name a department (1,221 of 教務處's own 4,557; a page
    of prize winners, a forum post), and a search for one of them by its own title lost its place. The publisher is
    the direction's own wording and has no such guesswork: its rows come from the site table.
    【Claude】 on the second: the plan writes 「问题没点名单位」, the direction 「问题没点名系所时」. relation never gives
    -1 between an office and a department (not the same kind), so by the plan's words 「教務處 轉系」 would discount
    no department's document while 「轉系」 discounts them all; naming an office is therefore naming no department."""
    units = scope_units(conn)
    ids = list(dict.fromkeys(ids))
    if not units or not ids or not vectors.has_table(conn, "doc_scope"):
        return set()
    named = detect(query, units)
    below = {u for u in named.units if u in units and units[u].parent_id is not None}
    teaching = any(units[u].kind in TEACHING_KINDS for u in below)
    applies: dict[str, list[tuple]] = {}
    own: set[str] = set()  # published by a college, a department or a programme
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        for doc_id, rel, unit_id, campus, level, cohort in conn.execute(
                "SELECT doc_id, relation, unit_id, campus, level, cohort_from FROM doc_scope"
                f" WHERE relation IN ('applies_to', 'publisher') AND doc_id IN ({','.join('?' * len(part))})", part):
            if rel == "applies_to":
                applies.setdefault(doc_id, []).append((unit_id, campus, level, cohort))
            elif unit_id in units and units[unit_id].kind in TEACHING_KINDS:
                own.add(doc_id)
    out = set() if teaching else own
    if below:
        for doc_id, rows in applies.items():
            doc = Scope(frozenset(r[0] for r in rows if r[0]), frozenset(r[1] for r in rows if r[1]),
                        frozenset(r[2] for r in rows if r[2]),
                        min((r[3] for r in rows if r[3] is not None), default=None))
            if relation(doc, named, units) == -1:
                out.add(doc_id)
    return out


def _off(conn: sqlite3.Connection, query: str, ids: list[str], config: HybridConfig) -> set[str]:
    if config.scope_factor == 1.0:  # off: the unit tables are not read
        return set()
    try:
        return off_audience(conn, query, ids)
    except Exception:
        log.exception("读不了单位范围（units、doc_scope）：这次不按单位打折")
        return set()


def _one_cohort(conn: sqlite3.Connection, ids: list[str], years: int) -> set[str]:
    """The documents of ids that go out of date for their one entry cohort (spec 2026-11-16 §二, the build's
    'cohort_single', read back): they state a cohort and none onwards (applies_to: a cohort_exact row, no cohort_from
    row), their title names no term or year (docs.period_ay: that would be why they expire, and comes first in the
    build), and their year is the one that cohort gives, its last of years years (docs.valid_ay = cohort + years - 1).
    The last two keep out a notice of 「111學年度」 that names the 111 entrants and a file that expires with the notice
    it hangs on: found on the trial library of 11-16, where two notices of 2023 lost their discount for a question
    naming the 111 cohort and pushed the answer down two places (Q15)."""
    out: set[str] = set()
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        out.update(r[0] for r in conn.execute(
            "SELECT a.doc_id FROM applies_to a JOIN docs d ON d.doc_id = a.doc_id WHERE a.doc_id IN ("
            + ",".join("?" * len(part)) + ") AND d.period_ay IS NULL GROUP BY a.doc_id "
            "HAVING MAX(a.cohort_exact IS NOT NULL) = 1 AND MAX(a.cohort_from IS NOT NULL) = 0 "
            "AND d.valid_ay = MAX(a.cohort_exact) + ? - 1", (*part, years)))
    return out


def validity(conn: sqlite3.Connection, ids: list[str], today: date, decay: float, *,
             cohort_step: int = 0) -> dict[str, float] | None:
    """Plan 2026-11-14 T6 (direction 2026-11-13 第 6 件, 「还有效」): doc_id -> what its score is multiplied by, for
    the documents of ids that go out of date (docs.expires = 1) and have a year (docs.valid_ay). Every other document
    counts 1 and is left out: one that does not go out of date (a regulation, a form, a standing page, an undated
    file), one of no known year. None for a database without the two columns: the caller then discounts nothing.
    - A document whose title names its term or year (docs.period_ay is set; an event's always is) loses a whole step
      for each academic year past: decay ** (the academic years from its own, docs.valid_ay, to today's). The year
      turns on 1 August (terms.current_term). A title that says 114學年度第2學期 says which term the document is of,
      and when that year is over it is the old version.
    - Any other falls by its own age, decay ** (days since the date it ranks by / 365): docs.rank_date, the latest
      among it and its merged copies, else its identity date. So nothing drops on 1 August: by a step alone a notice
      of July would be last year's a month later. One with no such date steps by its year, as the first.
    - cohort_step (spec 2026-11-16 §二; _valid gives it for a library whose meta says cohort_expiry on: the years a
      cohort studies, meta cohort_years; 0: none): a document that expires for its one entry cohort (_one_cohort)
      loses the whole step too, from the year the build gave it, the last that cohort studies, and not by the age of
      its file: the table of 98 entrants was put up again in 2025."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
    if not {"expires", "valid_ay"} <= columns:
        return None
    now = current_term(today).ay
    dated = "COALESCE(rank_date, ident_date)" if "rank_date" in columns else "ident_date"
    out: dict[str, float] = {}
    ids = list(dict.fromkeys(ids))
    stepped = _one_cohort(conn, ids, cohort_step) if cohort_step else set()
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        for doc_id, ay, named, day in conn.execute(
                f"SELECT doc_id, valid_ay, period_ay, {dated} FROM docs WHERE expires = 1 AND valid_ay IS NOT NULL"
                f" AND doc_id IN ({','.join('?' * len(part))})", part):
            if named is None and doc_id not in stepped and day:  # by its own age
                if (days := (today - date.fromisoformat(day[:10])).days) > 0:
                    out[doc_id] = decay ** (days / 365)
            elif isinstance(ay, int) and ay < now:
                out[doc_id] = decay ** (now - ay)
    return out


def _valid(conn: sqlite3.Connection, head: list[str], pool: KeywordPool, today: date,
           config: HybridConfig) -> dict[str, float] | None:
    """validity of the rerank head when it applies to this query: valid_decay is set and the query names no term
    (pool.recent, where ranking v2's date part is added too; so RankConfig(recency=False) asks for neither
    【Claude】). None otherwise, and when the columns cannot be read: no document is discounted."""
    if config.valid_decay == 1.0 or not pool.recent:
        return None
    try:
        # spec 2026-11-16 §二: only a library built with the single-cohort rule (meta cohort_expiry on) has documents
        # that expire for their cohort; any other is read without it
        try:
            said = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('cohort_expiry', 'cohort_years')"))
        except sqlite3.Error:  # a database without a meta table
            said = {}
        step = int(said.get("cohort_years") or 0) if said.get("cohort_expiry") == "on" else 0
        out = validity(conn, head, today, config.valid_decay, cohort_step=step)
        if step and out and pool.cohorts:
            # the design: 「问题点名了届别时不打折」: the documents that expire for that cohort, and only those (a
            # notice of its year keeps the discount of its age whatever cohort it names)
            for doc_id in _cohort_docs(conn, sorted(_one_cohort(conn, list(out), step)), list(pool.cohorts)):
                out.pop(doc_id, None)
        return out
    except Exception:
        log.exception("读不了 docs.expires、docs.valid_ay：这次不按学年打折，照旧加新旧分")
        return None


# ---------------------------------------------------------------- dense and rerank

def _usable_index(conn: sqlite3.Connection, embedder, index) -> vectors.VectorIndex | None:
    """Step 3's check, before any model is asked for: index (tests, offline runs) or the process's
    vectors.index_for(conn), when the model and revision of the embedder in use (the pins for the shared one) wrote
    it; None, with a log line, when there is none, it cannot be read or another model or revision wrote it."""
    want = (embedder.model_id, embedder.revision) if embedder is not None else (models.EMBED_ID, models.EMBED_REVISION)
    try:
        index = index if index is not None else vectors.index_for(conn)
    except Exception:
        log.exception("读不了向量（chunk_vecs）：只用关键词排序，两个模型都不载入")
        return None
    if index is None:
        log.warning("这个资料库没有可用的向量（建库没加 --embed，或向量不完整）：只用关键词排序，两个模型都不载入")
        return None
    if (index.model, index.revision) != want:
        log.warning("向量是 %s@%s 算的，查询要用 %s@%s：只用关键词排序，两个模型都不载入", index.model,
                    index.revision[:7], want[0], want[1][:7])
        return None
    return index


def _dense(text: str, config: HybridConfig, embedder, index: vectors.VectorIndex,
           tier: bool, explain: dict | None = None, allow=None) -> list[tuple[int, float]]:
    """Step 4: (chunk_id, cosine), the primary documents' chunks then the background ones' (tier), each pool's
    documents by their nearest chunk, from index (_usable_index checked it); an index without its documents gives the
    dense_candidates nearest chunks as before 11-08. [] when dense_candidates is 0 or anything fails (a rerank then
    reads the keyword candidates alone); a failure is written into explain as 'dense_error' (plan 2026-11-15 T1,
    review: an empty vector pool must not read as 「the vectors found nothing」). allow: a bool per document of the
    index (unitfilter.allow_mask), None: all."""
    if config.dense_candidates <= 0:
        return []
    try:
        encoder = embedder if embedder is not None else models.shared_embedder()
        q = encoder.encode([text])[0]
        if index.doc_of is None:
            return index.top(q, config.dense_candidates)
        if not tier or index.background is None:
            return index.top_docs(q, PER_DOC, config.dense_candidates, allow)
        sims = index.similarities(q)
        primary, back = ~index.background, index.background
        if allow is not None:
            primary, back = primary & allow, back & allow
        return (index.top_docs(q, PER_DOC, config.dense_candidates, primary, sims=sims)
                + index.top_docs(q, PER_DOC, config.dense_candidates, back, sims=sims))
    except models.ModelUnavailable as exc:  # expected while a model is missing: one line, not a traceback per query
        log.warning("向量模型不能用，这次不用向量：%s", exc)
        if explain is not None:
            explain["dense_error"] = f"向量模型不能用：{exc}"
        return []
    except Exception as exc:
        log.exception("向量检索失败：这次不用向量（这次失败不代表查无资料）")
        if explain is not None:
            explain["dense_error"] = f"向量检索失败：{type(exc).__name__}: {exc}"
        return []


def _by_doc(conn: sqlite3.Connection, dense: list[tuple[int, float]]) -> dict[str, list[int]]:
    """doc_id -> its dense chunk ids by similarity; documents in the order of their best chunk. Chunks the database
    no longer has are left out."""
    ids = [c for c, _ in dense]
    owner: dict[int, str] = {}
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        owner.update({r[0]: r[1] for r in conn.execute(
            f"SELECT chunk_id, doc_id FROM chunks WHERE chunk_id IN ({','.join('?' * len(part))})", part)})
    out: dict[str, list[int]] = {}
    for c in ids:
        if c in owner:
            out.setdefault(owner[c], []).append(c)
    return out


def _keyword_chunks(doc: _Doc) -> list[int]:
    return [c for _, c in sorted(doc.chunks)]


def _picks(keyword: list[int], dense: list[int], n: int) -> list[int]:
    """The chunks the reranker reads of one document: its first n keyword chunks, then the first of its n dense chunks
    it lacks, at most n + 1 in all when it has both (a document with one keyword chunk gets two dense ones), so the
    dense path adds and never pushes out a keyword chunk (owner 11-08)."""
    out = list(dict.fromkeys(keyword[: max(n, 0)] + dense[: max(n, 0)]))
    return out[: n + 1] if keyword and dense else out[: max(n, 0)]


def _rerank(conn: sqlite3.Connection, text: str, head: list[str], docs: dict[str, _Doc], dense_of: dict[str, list[int]],
            pool: KeywordPool, rank: RankConfig, today: date, config: HybridConfig, reranker, *,
            off: set[str] | frozenset[str] = frozenset(), explain: dict | None = None) -> float | None:
    """Step 6: scores the head and only then writes each head document's score and chunk order. Returns the best raw
    score, or None when the reranker could not be used (the documents are left as they were). off: off_audience of
    the candidates, multiplied by config.scope_factor (plan 2026-11-14 T7; T6's validity is read here, for the head
    only). explain (plan 2026-11-15 T1): a dict that is given gets 'rerank', each head document's parts as they
    entered its score, or 'rerank_error'."""
    parts: dict[str, dict] | None = {} if explain is not None else None
    try:
        scorer = reranker if reranker is not None else models.shared_reranker()
        picks = {d: _picks(_keyword_chunks(docs[d]), dense_of.get(d, []), config.chunks_per_doc) for d in head}
        texts = rerank_passages(conn, [c for cs in picks.values() for c in cs])
        pairs = [(d, c) for d, cs in picks.items() for c in cs if c in texts]
        if not pairs:
            log.warning("前 %d 笔没有能给重排模型读的小段：这次不重排", len(head))
            if explain is not None:
                explain["rerank_error"] = f"前 {len(head)} 笔没有能给重排模型读的小段"
            return None
        scores = [float(s) for s in scorer.score(text, [texts[c] for _, c in pairs])]
        if len(scores) != len(pairs):
            raise RuntimeError(f"重排模型给了 {len(scores)} 个分数，应该是 {len(pairs)} 个")
        of_chunk = {c: s for (_, c), s in zip(pairs, scores)}
        rel: dict[str, float] = {}
        for (d, _), s in zip(pairs, scores):
            rel[d] = max(rel.get(d, s), s)
        best = max(rel.values())
        # plan 2026-11-14 T6: the adjustments keep ranking v2's date part, and the whole score is multiplied by the
        # document's validity. Left out of a reranked score, the date part no longer ordered the documents that do
        # not go out of date, which it alone orders
        valid = _valid(conn, head, pool, today, config)
        adj = adjustments(conn, {d: docs[d] for d in head}, pool, rank, today, parts=parts)
    except models.ModelUnavailable as exc:
        log.warning("重排模型不能用，这次用关键词和向量的排序：%s", exc)
        if explain is not None:
            explain["rerank_error"] = f"重排模型不能用：{exc}"
        return None
    except Exception as exc:
        log.exception("重排失败：这次用关键词和向量的排序（这次失败不代表查无资料）")
        if explain is not None:
            explain["rerank_error"] = f"重排失败：{type(exc).__name__}: {exc}"
        return None
    noted: dict[str, dict] = {}
    for d in head:
        doc = docs[d]
        r = rel.get(d, 0.0)  # the sigmoid as it comes: an unsure head stays unsure
        picked = sorted((c for c in picks[d] if c in of_chunk), key=lambda c: -of_chunk[c])
        doc.ranked = list(dict.fromkeys(picked + _keyword_chunks(doc) + dense_of.get(d, [])))
        score = config.w_rerank * r + adj[d]
        if valid:  # T6
            score *= valid.get(d, 1.0)
        if d in off:  # T7
            score *= config.scope_factor
        doc.score = score
        if explain is not None and parts is not None:
            noted[d] = {"rel": r, "adj": adj[d], "parts": parts.get(d),
                        "valid": valid.get(d, 1.0) if valid else None, "off": d in off,
                        "score": doc.score, "chunks": {c: of_chunk[c] for c in picks[d] if c in of_chunk}}
    if explain is not None:
        explain["rerank"] = {"best": best, "valid_applies": valid is not None, "docs": noted}
    return best


def hybrid_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]], limit: int = TOP_N,
                  rank: RankConfig | None = None, config: HybridConfig | None = None, embedder=None, reranker=None,
                  index: vectors.VectorIndex | None = None, explain: dict | None = None,
                  only: UnitFilter | None = None) -> SearchResult:
    """The module docstring's steps. embedder, reranker and index stand in for the process's shared ones (tests,
    offline runs). In step 3's cases no model, shared or given, is asked for; past them, the embedder encodes the
    query when there is a usable index, and the reranker scores the head when there is one.
    explain (plan 2026-11-15 T1, the inspection tools: nknu.ops.explain): a dict that is given is filled with what
    each step found: 'keyword' (keyword_pool), 'mode' (empty: nothing to search for; keyword: step 3; hybrid),
    'ranking', 'text' (what the models read), 'kw_order' and 'kw_scores' (the keyword order and scores after the
    audience factor), 'off', 'dense' ([chunk, similarity]), 'dense_docs', 'background',
    'tiers' and 'heads' (primary, background: the fused orders and the reranked heads), 'rerank' (_rerank; None when
    none ran), 'final' (every document in the final order), 'config' and 'rank'; and, only when it happened,
    'dense_error' (_dense) and 'rerank_error' (_rerank): why a model's part is missing. Without it nothing changes:
    the tests of tests/ops/test_recorder.py compare the two to the bit.
    only (search(unit=...), design 2026-11-16-mcp-interface-design §二): both candidate paths keep only the documents
    the unit filter keeps, before their limits (keyword_pool's query; the vector path's allow mask); None: as before."""
    config = config if config is not None else HybridConfig()
    rank = rank if rank is not None else RankConfig()
    limit = max(limit, 0)
    pool = keyword_pool(conn, query, today=today, aliases=aliases, rank=rank, explain=explain, only=only)
    if explain is not None:
        explain.update(mode="empty", ranking="keyword", text=None, kw_order=[], kw_scores={}, off=[],
                       dense=[], dense_docs={}, background=[], tiers=[[], []], heads=[[], []], rerank=None, final=[],
                       config=asdict(config), rank=asdict(rank))
    if not pool.tokens:
        return SearchResult(pool.query, pool.understood, [], 0)
    has_vecs = vectors.has_table(conn, "chunk_vecs")
    index = _usable_index(conn, embedder, index) if config.dense and has_vecs else None
    rerank_only = has_vecs and config.rerank and not config.dense
    if index is None and not rerank_only:  # step 3
        off = _off(conn, pool.query, list(pool.docs), config)
        by_keyword = _keyword_order(pool, off, config.scope_factor)
        ordered = by_tier(by_keyword)
        if explain is not None:
            explain.update(mode="keyword", kw_order=[d.doc_id for d in by_keyword],
                           kw_scores={d: doc.score for d, doc in pool.docs.items()}, off=sorted(off),
                           background=sorted(d for d, doc in pool.docs.items() if doc.background),
                           final=[d.doc_id for d in ordered])
        return SearchResult(pool.query, pool.understood, top_hits(ordered, limit), pool.rows)

    text = school_spelling(pool.query, today)  # the one text bge-m3 and the reranker read
    keep = unitfilter.allowed(conn, only) if only is not None and index is not None else None
    allow = unitfilter.allow_mask(index.doc_ids, keep) if keep is not None and index.doc_of is not None else None
    dense = _dense(text, config, embedder, index, pool.tier, explain, allow) if index is not None else []
    dense_of = _by_doc(conn, dense)
    if keep is not None:  # an index without its documents could not be masked
        dense_of = {d: cs for d, cs in dense_of.items() if d in keep}
    only_dense = doc_rows(conn, [d for d in dense_of if d not in pool.docs])
    if pool.tier:
        mark_background(conn, only_dense)
    docs = pool.docs | only_dense
    dense_of = {d: cs for d, cs in dense_of.items() if d in docs}
    off = _off(conn, pool.query, list(docs), config)
    kw_ids = [d.doc_id for d in _keyword_order(pool, off, config.scope_factor)]
    tiers = tuple(_fused([d for d in kw_ids if docs[d].background == back],
                         [d for d in dense_of if docs[d].background == back], config.rrf_k) for back in (False, True))
    heads = (tiers[0][: max(config.rerank_docs, 0)], tiers[1][: max(config.rerank_background, 0)])
    if explain is not None:  # before the rerank writes the head's scores over the keyword ones
        explain.update(mode="hybrid", text=text, kw_order=list(kw_ids),
                       kw_scores={d: pool.docs[d].score for d in kw_ids}, off=sorted(off),
                       dense=[[int(c), float(s)] for c, s in dense],
                       dense_docs={d: [int(c) for c in cs] for d, cs in dense_of.items()},
                       background=sorted(d for d, doc in docs.items() if doc.background),
                       tiers=[list(t) for t in tiers], heads=[list(h) for h in heads])
    best = None
    if config.rerank and (heads[0] or heads[1]):
        best = _rerank(conn, text, heads[0] + heads[1], docs, dense_of, pool, rank, today, config, reranker,
                       off=off, explain=explain)
    reranked = best is not None
    parts: list[list[_Doc]] = []
    for ids, head in zip(tiers, heads, strict=True):
        rest = ids[len(head):] if reranked else ids
        for d in rest:
            if dense_of.get(d):
                docs[d].ranked = list(dict.fromkeys(_keyword_chunks(docs[d]) + dense_of[d]))
        parts.append((sort_docs(docs[d] for d in head) if reranked else []) + [docs[d] for d in rest])
    if reranked and config.background_factor > 0:  # spec 2026-11-16 §四: the two reranked heads by one measure
        cut = [len(h) for h in heads]
        ordered = (mixed(parts[0][: cut[0]], parts[1][: cut[1]], config.background_factor)
                   + parts[0][cut[0]:] + parts[1][cut[1]:])
    else:
        ordered = tiered(*parts)
    ranking = "hybrid" if dense_of or reranked else "keyword"
    if explain is not None:
        explain.update(final=[d.doc_id for d in ordered], ranking=ranking)
    return SearchResult(pool.query, pool.understood, top_hits(ordered, limit), pool.rows + len(dense),
                        ranking=ranking, best_rerank=best)
