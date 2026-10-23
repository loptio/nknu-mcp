"""Free-text search behind nknu_search (spec §6.5). No LLM anywhere.

1. Entity linking first: a place named in the query plus an hours question (or kind=hours), or a query that is nothing
   but a place name or id, answers with that place's card for on_date in get_hours's envelope (match=entity: next_change,
   open_now, recent notices); several places are listed, never picked (match=ambiguous), each with its next_change.
2. Otherwise bigram full text over the answer cards (card_fts) and the notices (doc_fts), aliases expanded. Expired rows
   are filtered out before ranking: an hours card must cover on_date or start within HORIZON_DAYS of it, a calendar
   event must not have ended, a notice must be current. A card that starts after on_date (a coming closure or
   extension) is listed only when the query fits it better than that place's card for on_date. Each index gives its
   best CANDIDATES by bm25; the merged list is ordered by coverage, then on_date's cards first, then bm25 (bm25 of two
   different indexes is not on one scale; coverage is).
3. Coverage = the share of the query's bigrams found in a hit; the first hit has the highest. Below T_LOW nothing is
   returned (found=false, 「請勿推測」); between T_LOW and T_HIGH the hits come back as match=weak.

Coverage ignores what says nothing about the topic: campus names (they only filter), English stop words and Chinese
question words; a question with several clauses counts its best-covered clause. An alias counts as covered when its
canonical term is in the hit (退課 is covered by 停修)."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from nknu.build.templates import Templates
from nknu.core.textnorm import fts_query, norm_text, tokenize
from nknu.server.envelope import NOTICE_LABEL, fit_budget, make_envelope
from nknu.server.linking import (
    CAMPUS_WORDS,
    alias_pairs,
    detect_intent,
    exact_places,
    expand,
    link_entities,
    query_campus,
)
from nknu.server.query import (
    CAVEAT_AMBIGUOUS,
    CAVEATS,
    CURRENT_DOC_SQL,
    current_doc_params,
    hours_envelope,
    parse_date_arg,
    place_candidates,
    today_info,
    worst_status,
)
from nknu.server.servedb_reader import ServeDB

# Bigram-coverage thresholds, grid-searched (step 0.05) on tests/search/calibration.yaml:
# `uv run python -m tests.search.evaluate --calibrate`.
T_LOW = 0.35
T_HIGH = 0.4

KINDS = ("any", "hours", "calendar", "announcement", "place")
CAMPUSES = ("heping", "yanchao")
MAX_LIMIT = 10
CANDIDATES = 20  # per index, by bm25, before the coverage re-ranking
HORIZON_DAYS = 14  # an hours card starting this many days after on_date can still answer (a coming closure, extension)
CAVEAT_NOT_FOUND = "查無相關資料，請勿推測"
CAVEAT_WEAK = "只有部分字詞相符，請先確認內容是否回答問題，請勿推測"

Strength = Literal["none", "weak", "text"]

_EN_STOP = frozenset((
    "a", "an", "the", "is", "are", "was", "were", "be", "do", "does", "did", "can", "could", "will", "would", "should",
    "of", "to", "in", "on", "at", "for", "from", "by", "with", "and", "or", "what", "when", "where", "which", "who",
    "whom", "why", "how", "i", "me", "my", "we", "our", "you", "your", "it", "its", "this", "that", "these", "there",
    "any", "some"))
# Question words and particles, removed before tokenizing so they neither rank hits nor dilute the coverage.
_ZH_STOP = tuple(sorted((norm_text(w) for w in (
    "請問", "請教", "我想知道", "想知道", "我想", "我要", "知道", "如果", "的話", "什麼時候", "甚麼時候", "什麼", "甚麼",
    "怎麼", "怎樣", "如何", "哪裡", "哪裏", "哪些", "哪幾天", "哪一天", "哪天", "哪幾", "哪一", "哪個", "幾天", "是否",
    "是不是", "有沒有", "可不可以", "可以", "能不能", "會不會", "要不要", "還是", "還要", "到底", "自己", "那個", "這個",
    "那天", "之後", "之前", "幾號", "幾個", "幾次", "幾分", "幾科", "多少", "嗎", "呢", "吧", "啊", "呀", "喔", "的", "了",
    "誰", "是", "要", "這", "那")),
    key=len, reverse=True))
_CAMPUS_TEXT = tuple(sorted({norm_text(w) for words in CAMPUS_WORDS.values() for w in words}
                            | {norm_text("和平校區"), norm_text("燕巢校區")}, key=len, reverse=True))
_CLAUSE = re.compile(r"[?!;,。？！；，]+")


def strength(coverage: float, t_low: float = T_LOW, t_high: float = T_HIGH) -> Strength:
    return "none" if coverage < t_low else "weak" if coverage < t_high else "text"


def _strip(text: str, words: tuple[str, ...]) -> str:
    for w in words:
        text = text.replace(w, " ")
    return text


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(t for t in tokenize(text) if t not in _EN_STOP))


def _cleaned(query: str) -> str:
    return _strip(norm_text(query), _ZH_STOP)


@dataclass
class _Hit:
    score: float  # bm25: lower is better
    order: int  # tie-break between the two indexes
    tokens: frozenset[str]
    view: dict
    entity_id: str | None = None
    coverage: float = 0.0
    upcoming: bool = False  # an hours card that starts after on_date
    day_from: str = ""


@dataclass
class Retrieval:
    """What a query found before thresholds and budget: match is entity / ambiguous / text; coverage is None for an
    entity match (its strength does not depend on wording)."""
    match: str
    facts: list[dict]
    coverage: float | None = None
    status: str = "current"
    entity_ids: list[str] = field(default_factory=list)


def _incl(hi: str) -> str:
    return (dt.date.fromisoformat(hi) - dt.timedelta(days=1)).isoformat()


def _hours_hits(conn: sqlite3.Connection, match: str, on: dt.date, campus: str | None) -> list[_Hit]:
    """Hours cards covering on_date or starting within HORIZON_DAYS of it. No bm25 cut: a place has a handful of cards
    in that span, and the cards for on_date must all be there for _fitting() to compare against."""
    rows = conn.execute(
        """SELECT c.entity_id, c.status, c.answer_zh, c.valid_from, f.tokens, bm25(card_fts) AS score
             FROM card_fts f JOIN answer_card c ON c.id = f.card_id JOIN entity e ON e.id = c.entity_id
            WHERE card_fts MATCH :q AND c.intent = 'hours' AND c.valid_from <= :last AND :d < c.valid_to
              AND (:campus IS NULL OR e.campus = :campus)
            ORDER BY score""",
        {"q": match, "d": on.isoformat(), "last": (on + dt.timedelta(days=HORIZON_DAYS)).isoformat(), "campus": campus})
    out = []
    for r in rows:
        view = {"entity_id": r["entity_id"], "answer": r["answer_zh"]}
        if r["status"] != "current":
            view["status"] = r["status"]
        out.append(_Hit(r["score"], 0, frozenset(r["tokens"].split()), view, r["entity_id"],
                        upcoming=r["valid_from"] > on.isoformat(), day_from=r["valid_from"]))
    return out


def _fitting(hits: list[_Hit]) -> list[_Hit]:
    """Cards that start after on_date only where the query fits them better than the place's card for on_date (閉館
    fits the closure card; 重訓 fits the closure no better than today's card), each sentence once (the earliest)."""
    today = {h.entity_id: h.coverage for h in hits if h.entity_id and not h.upcoming}
    seen: set[tuple[str | None, str]] = set()
    out = []
    for h in sorted(hits, key=lambda h: h.day_from):
        if h.upcoming:
            key = (h.entity_id, h.view["answer"])
            if h.coverage <= today.get(h.entity_id, 0.0) or key in seen:
                continue
            seen.add(key)
        out.append(h)
    return out


def _calendar_hits(conn: sqlite3.Connection, match: str, on: dt.date, n: int) -> list[_Hit]:
    rows = conn.execute(
        """SELECT c.payload_json, f.tokens, bm25(card_fts) AS score
             FROM card_fts f JOIN answer_card c ON c.id = f.card_id
            WHERE card_fts MATCH :q AND c.intent = 'calendar' AND c.valid_to > :d
            ORDER BY score LIMIT :n""", {"q": match, "d": on.isoformat(), "n": n})
    out = []
    for r in rows:
        p = json.loads(r["payload_json"])
        view = {"id": p["uid"], "summary": p["summary"], "valid": [p["valid"][0], _incl(p["valid"][1])],
                "category": p["category"]}
        if p.get("status", "confirmed") != "confirmed":
            view["status"] = p["status"]
        out.append(_Hit(r["score"], 1, frozenset(r["tokens"].split()), view))
    return out


def _doc_hits(conn: sqlite3.Connection, match: str, today: dt.date, n: int) -> list[_Hit]:
    rows = conn.execute(
        f"""SELECT d.title, d.unit, d.published_on, d.url, f.tokens, bm25(doc_fts) AS score
              FROM doc_fts f JOIN doc d ON d.id = f.doc_id
             WHERE doc_fts MATCH :q AND {CURRENT_DOC_SQL}
             ORDER BY score LIMIT :n""", {"q": match, "n": n, **current_doc_params(today)})
    out = []
    for r in rows:
        view = {"title": r["title"], "unit": r["unit"], "published_on": r["published_on"], "url": r["url"],
                "label": NOTICE_LABEL}
        out.append(_Hit(r["score"], 2, frozenset(r["tokens"].split()), {k: v for k, v in view.items() if v}))
    return out


@dataclass(frozen=True)
class _Clause:
    tokens: frozenset[str]  # topic bigrams of one clause
    aliases: tuple[tuple[frozenset[str], tuple[frozenset[str], ...]], ...]  # (alias bigrams, each canonical term's)


def _clauses(query: str) -> list[_Clause]:
    out = []
    for clause in _CLAUSE.split(_cleaned(query)):
        toks = frozenset(_tokens(_strip(clause, _CAMPUS_TEXT)) or _tokens(clause))
        if toks:
            aliases = tuple((frozenset(tokenize(key)) & toks, tuple(frozenset(tokenize(t)) for t in terms))
                            for key, terms in alias_pairs(clause))
            out.append(_Clause(toks, aliases))
    return out


def _coverage(clauses: list[_Clause], hit: frozenset[str]) -> float:
    """Share of the query's topic bigrams found in the hit (an alias counts when one of its canonical terms is wholly
    there), taken over the best-covered clause when a question has several (期末考是哪幾天？考完之後還要上課嗎？)."""
    best = 0.0
    for c in clauses:
        covered = c.tokens & hit
        for key, terms in c.aliases:
            if any(t <= hit for t in terms):
                covered |= key
        best = max(best, len(covered) / len(c.tokens))
    return best


def _full_text(conn: sqlite3.Connection, query: str, *, kind: str, on: dt.date, today: dt.date, campus: str | None,
               limit: int) -> tuple[list[_Hit], float]:
    tokens = _tokens(expand(_cleaned(query)))
    if not tokens:
        return [], 0.0
    match = fts_query(tokens)
    hits: list[_Hit] = []
    if kind in ("any", "hours", "place"):
        hits += _hours_hits(conn, match, on, campus)
    if kind in ("any", "calendar"):
        hits += _calendar_hits(conn, match, on, CANDIDATES)
    if kind in ("any", "announcement"):
        hits += _doc_hits(conn, match, today, CANDIDATES)
    clauses = _clauses(query)
    for h in hits:
        h.coverage = _coverage(clauses, h.tokens)
    hits = _fitting(hits)
    hits.sort(key=lambda h: (-h.coverage, h.upcoming, h.score, h.order))
    return hits[:limit], (hits[0].coverage if hits else 0.0)


def retrieve(conn: sqlite3.Connection, tpl: Templates, query: str, *, kind: str, on: dt.date, today: dt.date,
             campus: str | None, limit: int) -> Retrieval:
    """Ranked facts for a query, before thresholds and the token budget (search() and the calibration use it)."""
    exact = exact_places(conn, query, campus)  # a bare place name or id: its card is the answer
    ids = exact or link_entities(conn, query, campus)
    campus = campus or query_campus(query)
    if kind == "place":
        if ids:
            return Retrieval("entity", _place_views(conn, ids[:limit]), entity_ids=ids)
        hits, cov = _full_text(conn, query, kind="place", on=on, today=today, campus=campus, limit=MAX_LIMIT)
        ids = list(dict.fromkeys(h.entity_id for h in hits if h.entity_id))
        return Retrieval("text", _place_views(conn, ids[:limit]), cov, entity_ids=ids)
    if ids and (kind == "hours" or (kind == "any" and (exact or detect_intent(query) == "hours"))):
        cands = place_candidates(conn, tpl, ids, on)
        status = worst_status({c["status"] for c in cands})
        return Retrieval("entity" if len(cands) == 1 else "ambiguous", cands[:limit], status=status, entity_ids=ids)
    hits, cov = _full_text(conn, query, kind=kind, on=on, today=today, campus=campus, limit=limit)
    return Retrieval("text", [h.view for h in hits[:limit]], cov)


def _place_views(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT id, name_zh, campus FROM entity WHERE id IN ({','.join('?' * len(ids))})", ids)}
    return [{"id": e, "name": rows[e]["name_zh"], "campus": rows[e]["campus"]} for e in ids if e in rows]


def search(db: ServeDB, query: str, kind: str = "any", on_date: str | None = None, campus: str | None = None,
           limit: int = 5, now: dt.datetime | None = None) -> dict:
    if not query or not query.strip():
        raise ValueError("query 不可為空")
    if kind not in KINDS:
        raise ValueError(f"kind 應為 {'|'.join(KINDS)}")
    if campus is not None and campus not in CAMPUSES:
        raise ValueError(f"campus 應為 {'|'.join(CAMPUSES)}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 應為 1–{MAX_LIMIT}")
    info = today_info(now)
    today = dt.date.fromisoformat(info["date"])
    on = parse_date_arg(on_date) if on_date else today
    base = {"kb": db.kb, "today": info}
    conn = db.conn()
    r = retrieve(conn, db.templates(), query, kind=kind, on=on, today=today, campus=campus, limit=limit)
    more = len(r.entity_ids) - len(r.facts) if r.entity_ids else 0
    if r.match == "entity" and kind != "place":  # the same fields get_hours answers (next_change, open_now, notices)
        f = r.facts[0]
        env = hours_envelope(conn, db.templates(), f["entity_id"], on, info)
        env["facts"] = [{"entity_id": f["entity_id"], "name": f["name"]}]
        return fit_budget(make_envelope(**base, **env))
    if r.match == "ambiguous" and kind != "place":
        found = any(f["status"] in ("current", "conflict") for f in r.facts)
        return fit_budget(make_envelope(**base, found=found, status=r.status, match="ambiguous", facts=r.facts,
                                        caveat=CAVEAT_AMBIGUOUS, more=more or None))
    s = "text" if r.coverage is None else strength(r.coverage)
    if s == "none" or not r.facts:
        return fit_budget(make_envelope(**base, found=False, status="no_data", caveat=CAVEAT_NOT_FOUND))
    match = "entity" if r.match == "entity" else s
    # The hours cards set the status, as on the ambiguous path (the most severe one); events and notices count as found.
    status = worst_status({f.get("status", "current") for f in r.facts if f.get("entity_id")})
    found = any(f.get("status", "current") not in ("stale", "no_data") for f in r.facts)
    return fit_budget(make_envelope(**base, found=found, status=status, match=match, facts=r.facts,
                                    caveat=_caveat(match, status), more=more or None))


def _caveat(match: str, status: str) -> str | None:
    """A weak match and a non-current status each need saying; both are kept when both apply."""
    return "；".join(c for c in (CAVEAT_WEAK if match == "weak" else None, CAVEATS.get(status)) if c) or None
