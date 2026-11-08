"""Hybrid retrieval (plan 2026-11-02 T8, spec §3.4; addendum F's unit scope; addendum M, owner 11-08): the bigram
keyword pool and the bge-m3 vector pool are fused per document by reciprocal rank fusion (RRF), the first primary and
the first background documents are reranked by bge-reranker-v2-m3 on at most chunks_per_doc chunks each, the
ranking-v2 adjustments (and the unit scope, while it has a weight) are added, and the result is in search.py's tier
order, each document on its own (no series since the owner's 11-03 decision, addendum L). Both models only return
scores: the serving path never calls a text-generating model.

1. keyword_pool (search.py): the FTS match, each document scored as search_docs scores it and marked background or
   not (search.py step 3). Nothing to search for ("!!!", ""): no hits, and no model is asked for.
2. The unit scope (addendum F; T7d's tables units, unit_aliases and doc_scope): when the query names a unit below the
   school, a campus or a level (units.detect), each candidate gets w_scope * units.relation(its applies_to, the
   query's scope), plus w_mention when it mentions a unit the query names. It goes where the ranking-v2 adjustments
   are: into each keyword score before the keyword order is taken, and next to them in a reranked score. Both
   weights are 0 since 11-08 (addendum M5: 10 questions that name a unit moved 2 by 1-5 ranks on 11-04), so nothing
   reads the unit tables at query time; a database without them, or a query that names nothing, changes nothing.
3. The keyword order (search_docs' own, the scope aside) with ranking "keyword", so the answer carries respond's
   NOTE_RANKING, and no model is asked for (main session 11-03, addendum J: the plan's Review Focus 1 over T8's
   pseudo-code), when
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
   keyword chunk), all in one reranker call. A document's relevance is
   its best chunk's score; as it comes (rerank_norm "raw", addendum M4: an unsure head stays unsure) or over the
   head's best ("max", the 11-04 build's setting), its score becomes w_rerank * relevance + adjustments + scope, and
   its chunks are ordered by the reranker's scores. In each tier the head is sorted as search_docs sorts and the other
   documents follow in RRF order. A failure leaves the RRF order and the keyword scores.
7. search.tiered over the two tiers, then top_hits (search.py): the first limit documents, each its own hit.

SearchResult.ranking is "hybrid" when dense hits or rerank scores shaped the order, else "keyword"; best_rerank is the
head's highest raw rerank score. No threshold (11-01): the dense path finds neighbours for any query with words.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

from nknu.content.embed import chunk_passages
from nknu.content.terms import school_spelling
from nknu.content.units import Scope, Unit, detect, relation
from nknu.core import models
from nknu.retrieval import vectors
from nknu.retrieval.search import (
    CANDIDATES,
    PER_DOC,
    TOP_N,
    W_BM25,
    W_COVERAGE,
    KeywordPool,
    RankConfig,
    _Doc,
    adjustments,
    by_tier,
    doc_rows,
    keyword_pool,
    mark_background,
    sort_docs,
    tiered,
    top_hits,
)
from nknu.retrieval.types import SearchResult

log = logging.getLogger(__name__)

# addendum F's starting values were 0.3 and 0.1 【Claude】; 0 since the owner's 11-08 decision (project review §六 1,
# addendum M5): on 11-04 they moved 2 of 10 unit-naming questions by 1-5 ranks, and one site cannot show what they do
W_SCOPE = 0.0
W_MENTION = 0.0
NORMS = ("max", "raw")
_IN_MAX = 500  # ids per IN (...) query


@dataclass(frozen=True)
class HybridConfig:
    """The fusion's settings (plan 2026-11-02 T8, spec §3.4; addendum F). Starting values 【Claude】, none measured on
    our data before the first pipeline build: T10 compares them offline."""

    dense: bool = True
    rerank: bool = True
    rrf_k: int = 60  # spec §3.4 「常用 60」
    dense_candidates: int = CANDIDATES  # documents per tier from the vector path (300), PER_DOC chunks each
    rerank_docs: int = 50  # primary documents by RRF that the reranker reads (was 30 of any tier; addendum M3)
    rerank_background: int = 10  # background documents by RRF that it reads too (addendum M3)
    chunks_per_doc: int = 2  # each document's keyword chunks and dense chunks the reranker reads (_picks): at most
    #                            3 per document, 180 passages
    w_rerank: float = W_COVERAGE + W_BM25  # 3.0: rerank relevance (0-1) spans what keyword relevance spans
    rerank_norm: str = "raw"  # "raw": the sigmoid as it comes (addendum M4, 11-08); "max": each document's best score
    #                           over the head's best, as bm25_norm (spec §3.4, Onyx: one range first; until 11-08)
    w_scope: float = W_SCOPE  # times units.relation(the document's applies_to, the query's scope): +1, 0 or -1
    w_mention: float = W_MENTION  # when the document mentions a unit the query names

    def __post_init__(self) -> None:
        if self.rerank_norm not in NORMS:
            raise ValueError(f"rerank_norm must be one of {NORMS}, not {self.rerank_norm!r}")


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


# ---------------------------------------------------------------- the unit scope (addendum F)

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


def query_scope(conn: sqlite3.Connection, query: str) -> tuple[Scope, dict[str, Unit]] | None:
    """(what the query names, the unit tree), or None when the database has no unit tree or no doc_scope, or the
    query names no unit below the school, no campus and no level (it then changes nothing)."""
    units = scope_units(conn)
    if not units or not vectors.has_table(conn, "doc_scope"):
        return None
    named = detect(query, units)
    roots = {k for k, u in units.items() if u.parent_id is None}
    if not (named.units - roots or named.campus or named.levels):
        return None
    return named, units


def scope_bonus(conn: sqlite3.Connection, ids: list[str], named: tuple[Scope, dict[str, Unit]] | None,
                config: HybridConfig) -> dict[str, float]:
    """doc_id -> w_scope * units.relation(its applies_to rows as one Scope, the query's) + w_mention when one of its
    mentions rows is a unit the query names; documents without doc_scope rows, and zero sums, are left out."""
    if named is None or not ids or not (config.w_scope or config.w_mention):
        return {}
    query, units = named
    applies: dict[str, list[tuple]] = {}
    mentions: dict[str, set[str]] = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        for doc_id, rel, unit_id, campus, level, cohort in conn.execute(
                "SELECT doc_id, relation, unit_id, campus, level, cohort_from FROM doc_scope"
                f" WHERE doc_id IN ({','.join('?' * len(part))})", part):
            if rel == "applies_to":
                applies.setdefault(doc_id, []).append((unit_id, campus, level, cohort))
            elif rel == "mentions" and unit_id:
                mentions.setdefault(doc_id, set()).add(unit_id)
    out: dict[str, float] = {}
    for doc_id in ids:
        if doc_id not in applies and doc_id not in mentions:
            continue
        rows = applies.get(doc_id, [])
        doc = Scope(frozenset(r[0] for r in rows if r[0]), frozenset(r[1] for r in rows if r[1]),
                    frozenset(r[2] for r in rows if r[2]), min((r[3] for r in rows if r[3] is not None), default=None))
        bonus = config.w_scope * relation(doc, query, units)
        if mentions.get(doc_id, set()) & query.units:
            bonus += config.w_mention
        if bonus:
            out[doc_id] = bonus
    return out


def _named(conn: sqlite3.Connection, query: str, config: HybridConfig) -> tuple[Scope, dict[str, Unit]] | None:
    if not (config.w_scope or config.w_mention):
        return None
    try:
        return query_scope(conn, query)
    except Exception:
        log.exception("读不了单位范围（units、doc_scope）：这次不按单位加减分")
        return None


def _bonus(conn: sqlite3.Connection, ids: list[str], named: tuple[Scope, dict[str, Unit]] | None,
           config: HybridConfig) -> dict[str, float]:
    try:
        return scope_bonus(conn, ids, named, config)
    except Exception:
        log.exception("读不了单位范围（doc_scope）：这次不按单位加减分")
        return {}


def _keyword_order(pool: KeywordPool, bonus: dict[str, float]) -> list[_Doc]:
    """pool.ordered; or, with each keyword document's scope bonus added to its score (which already holds the
    ranking-v2 adjustments), those documents ordered again the same way."""
    moved = [d for d in bonus if d in pool.docs]
    if not moved:
        return pool.ordered
    for d in moved:
        pool.docs[d].score += bonus[d]
    return sort_docs(pool.docs.values())


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
           tier: bool) -> list[tuple[int, float]]:
    """Step 4: (chunk_id, cosine), the primary documents' chunks then the background ones' (tier), each pool's
    documents by their nearest chunk, from index (_usable_index checked it); an index without its documents gives the
    dense_candidates nearest chunks as before 11-08. [] when dense_candidates is 0 or anything fails (a rerank then
    reads the keyword candidates alone)."""
    if config.dense_candidates <= 0:
        return []
    try:
        encoder = embedder if embedder is not None else models.shared_embedder()
        q = encoder.encode([text])[0]
        if index.doc_of is None:
            return index.top(q, config.dense_candidates)
        if not tier or index.background is None:
            return index.top_docs(q, PER_DOC, config.dense_candidates)
        sims = index.similarities(q)
        return (index.top_docs(q, PER_DOC, config.dense_candidates, ~index.background, sims=sims)
                + index.top_docs(q, PER_DOC, config.dense_candidates, index.background, sims=sims))
    except models.ModelUnavailable as exc:  # expected while a model is missing: one line, not a traceback per query
        log.warning("向量模型不能用，这次不用向量：%s", exc)
        return []
    except Exception:
        log.exception("向量检索失败：这次不用向量（这次失败不代表查无资料）")
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
            pool: KeywordPool, rank: RankConfig, today: date, config: HybridConfig, bonus: dict[str, float],
            reranker) -> float | None:
    """Step 6: scores the head and only then writes each head document's score and chunk order. Returns the best raw
    score, or None when the reranker could not be used (the documents are left as they were)."""
    try:
        scorer = reranker if reranker is not None else models.shared_reranker()
        picks = {d: _picks(_keyword_chunks(docs[d]), dense_of.get(d, []), config.chunks_per_doc) for d in head}
        texts = rerank_passages(conn, [c for cs in picks.values() for c in cs])
        pairs = [(d, c) for d, cs in picks.items() for c in cs if c in texts]
        if not pairs:
            log.warning("前 %d 笔没有能给重排模型读的小段：这次不重排", len(head))
            return None
        scores = [float(s) for s in scorer.score(text, [texts[c] for _, c in pairs])]
        if len(scores) != len(pairs):
            raise RuntimeError(f"重排模型给了 {len(scores)} 个分数，应该是 {len(pairs)} 个")
        of_chunk = {c: s for (_, c), s in zip(pairs, scores)}
        rel: dict[str, float] = {}
        for (d, _), s in zip(pairs, scores):
            rel[d] = max(rel.get(d, s), s)
        best = max(rel.values())
        adj = adjustments(conn, {d: docs[d] for d in head}, pool, rank, today)
    except models.ModelUnavailable as exc:
        log.warning("重排模型不能用，这次用关键词和向量的排序：%s", exc)
        return None
    except Exception:
        log.exception("重排失败：这次用关键词和向量的排序（这次失败不代表查无资料）")
        return None
    for d in head:
        doc = docs[d]
        r = rel.get(d, 0.0)
        norm = r / best if config.rerank_norm == "max" and best > 0 else r
        picked = sorted((c for c in picks[d] if c in of_chunk), key=lambda c: -of_chunk[c])
        doc.ranked = list(dict.fromkeys(picked + _keyword_chunks(doc) + dense_of.get(d, [])))
        doc.score = config.w_rerank * norm + adj[d] + bonus.get(d, 0.0)
    return best


def hybrid_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]], limit: int = TOP_N,
                  rank: RankConfig | None = None, config: HybridConfig | None = None, embedder=None, reranker=None,
                  index: vectors.VectorIndex | None = None) -> SearchResult:
    """The module docstring's steps. embedder, reranker and index stand in for the process's shared ones (tests,
    offline runs). In step 3's cases no model, shared or given, is asked for; past them, the embedder encodes the
    query when there is a usable index, and the reranker scores the head when there is one."""
    config = config if config is not None else HybridConfig()
    rank = rank if rank is not None else RankConfig()
    limit = max(limit, 0)
    pool = keyword_pool(conn, query, today=today, aliases=aliases, rank=rank)
    if not pool.tokens:
        return SearchResult(pool.query, pool.understood, [], 0)
    named = _named(conn, pool.query, config)
    has_vecs = vectors.has_table(conn, "chunk_vecs")
    index = _usable_index(conn, embedder, index) if config.dense and has_vecs else None
    rerank_only = has_vecs and config.rerank and not config.dense
    if index is None and not rerank_only:  # step 3
        ordered = _keyword_order(pool, _bonus(conn, list(pool.docs), named, config))
        return SearchResult(pool.query, pool.understood, top_hits(by_tier(ordered), limit), pool.rows)

    text = school_spelling(pool.query, today)  # the one text bge-m3 and the reranker read
    dense = _dense(text, config, embedder, index, pool.tier) if index is not None else []
    dense_of = _by_doc(conn, dense)
    only_dense = doc_rows(conn, [d for d in dense_of if d not in pool.docs])
    if pool.tier:
        mark_background(conn, only_dense)
    docs = pool.docs | only_dense
    dense_of = {d: cs for d, cs in dense_of.items() if d in docs}
    bonus = _bonus(conn, list(docs), named, config)
    kw_ids = [d.doc_id for d in _keyword_order(pool, bonus)]
    for d, b in bonus.items():
        if d not in pool.docs:  # a document only the vectors found: its score is its scope
            docs[d].score += b
    tiers = tuple(_fused([d for d in kw_ids if docs[d].background == back],
                         [d for d in dense_of if docs[d].background == back], config.rrf_k) for back in (False, True))
    heads = (tiers[0][: max(config.rerank_docs, 0)], tiers[1][: max(config.rerank_background, 0)])
    best = None
    if config.rerank and (heads[0] or heads[1]):
        best = _rerank(conn, text, heads[0] + heads[1], docs, dense_of, pool, rank, today, config, bonus, reranker)
    reranked = best is not None
    parts: list[list[_Doc]] = []
    for ids, head in zip(tiers, heads, strict=True):
        rest = ids[len(head):] if reranked else ids
        for d in rest:
            if dense_of.get(d):
                docs[d].ranked = list(dict.fromkeys(_keyword_chunks(docs[d]) + dense_of[d]))
        parts.append((sort_docs(docs[d] for d in head) if reranked else []) + [docs[d] for d in rest])
    return SearchResult(pool.query, pool.understood, top_hits(tiered(*parts), limit), pool.rows + len(dense),
                        ranking="hybrid" if dense_of or reranked else "keyword", best_rerank=best)
