"""What search hands to respond. Kept apart so the two can be written and tested separately. Since the owner's 11-03
decision (plan addendum L) there are no series: a hit is one document, never a group."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocHit:
    doc_id: str                 # the document
    score: float                # higher is better
    coverage: float             # share of the query's own tokens (alias-equivalent counts) found in the matched chunks
    bm25: float                 # best FTS5 bm25 of its matched chunks (lower is better); 0.0 when it matched nothing
    chunk_ids: list[int]        # its matched chunks, best first (hybrid: the reranker's order); empty: none matched
    background: bool = False    # an archive member, journal or exam, ranked after the primary documents (addendum M2)


@dataclass
class Understood:
    tokens: list[str]           # the query's own tokens (textnorm.tokenize)
    canonical: list[str]        # term115s1, ay115, entry113 ...
    aliases: list[str]          # alias terms the query matched
    terms: list[str]            # human-readable: '115-1', '115學年度'
    cohorts: list[int]          # 入學 years the query names


@dataclass
class SearchResult:
    query: str
    understood: Understood
    hits: list[DocHit]
    candidates: int             # chunks found (keyword chunks kept, ≤ PER_DOC a document, + dense chunks); 0: none
    ranking: str = "keyword"    # "hybrid" once dense hits or rerank scores shaped the order (plan 2026-11-02 T8)
    best_rerank: float | None = None  # the query's highest raw reranker score; None when no rerank ran
