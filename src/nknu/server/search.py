"""Free-text search behind nknu_search (spec §6.5). No LLM anywhere.

1. Entity linking first: a place named in the query plus an hours question (or kind=hours), or a query that is nothing
   but a place name or id, answers with that place's card for on_date in get_hours's envelope (match=entity: next_change,
   open_now, recent notices); several places are listed, never picked (match=ambiguous), each with its next_change.
2. Otherwise bigram full text over the answer cards (card_fts) and the notices (doc_fts), aliases expanded. Expired rows
   are filtered out before ranking: an hours card must cover on_date or start within HORIZON_DAYS of it, a calendar
   event must not have ended, a notice must be current. A card that starts after on_date (a coming closure or
   extension) is listed only when the query fits it better than that place's card for on_date. Each index gives its
   best CANDIDATES by bm25; the merged list is ordered by coverage (below), then on_date's cards first, then bm25 (bm25
   of two different indexes is not on one scale; coverage is), except that equally covered calendar events take their
   places soonest first (the midterm week before a later one-day entry).
3. Coverage = the share of the query's topic bigrams found in a hit; a hit with none of them is dropped. The first hit's
   coverage decides: below T_LOW nothing is returned (found=false, 「請勿推測」); between T_LOW and T_HIGH the hits come
   back as match=weak.
4. A place the query names only on another campus (燕巢 游泳池) is not there: found=false, with the places of that name.
5. Old notices (C5: posted over a year ago, or past their posting period) are never current. When nothing current is
   returned to a notice search, or to a kind=any question with no hours or calendar intent, the notices are searched
   again among the old ones; hits that clear the same thresholds come back labelled with their date
   (old_notice_label), newest first among equally covered, with status=stale, found=false and CAVEAT_OLD_NOTICES.
   Envelopes are never trimmed (U1): `limit` is the only cut.

The topic leaves out what says nothing about what is asked: the places the query names (a place's name is in its own
cards; the name only picks which cards), the school and unit names (高師大, NKNU, 教務組: every notice is the school's,
and they are not searched for either), words that only say what kind of answer is wanted or when (開放 and 時間 are in
every hours card; 公告; 下學期), campus names (they only filter), English stop words and Chinese question words, and the
bigrams that only join two words (修截 in 停修截止: in no candidate hit while the bigrams on both sides are), so a
question typed without spaces reads as the spaced one. A query with no topic left falls back to all its words but the
school's names. A question with several clauses counts its best-covered clause. An alias counts as covered when its
canonical term is in the hit (退課 is covered by 停修). Hits are ordered by how much of the whole question they cover,
the named places and units included (an hours card is credited with a place's name only when it is that place's card,
with a unit's when it names the unit), then by topic coverage."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from itertools import groupby
from typing import Literal

from nknu.build.templates import Templates
from nknu.core.textnorm import CJK, fts_query, norm_text, tokenize
from nknu.server.envelope import NOTICE_LABEL, make_envelope
from nknu.server.linking import (
    CAMPUS_WORDS,
    KIND_TOKENS,
    KIND_WORDS,
    Linked,
    alias_pairs,
    detect_intent,
    exact_places,
    expand,
    link_query,
    query_campus,
)
from nknu.server.query import (
    CAVEAT_AMBIGUOUS,
    CAVEAT_TENTATIVE,
    CAVEATS,
    CURRENT_DOC_SQL,
    OLD_DOC_SQL,
    current_doc_params,
    hours_envelope,
    old_notice_label,
    parse_date_arg,
    place_candidates,
    today_info,
    worst_status,
)
from nknu.server.servedb_reader import ServeDB

# Bigram-coverage thresholds, grid-searched (step 0.05) on tests/search/calibration.yaml, the lowest point of the best
# plateau (U4): `uv run python -m tests.search.evaluate --calibrate`.
T_LOW = 0.35
T_HIGH = 0.4

KINDS = ("any", "hours", "calendar", "announcement", "place")
CAMPUSES = ("heping", "yanchao")
MAX_LIMIT = 10
CANDIDATES = 20  # per index, by bm25, before the coverage re-ranking
HORIZON_DAYS = 14  # an hours card starting this many days after on_date can still answer (a coming closure, extension)
CAVEAT_NOT_FOUND = "查無相關資料，請勿推測"
CAVEAT_NOT_ON_CAMPUS = "{campus}查無此地點，請勿推測"
CAMPUS_ZH = {"heping": "和平校區", "yanchao": "燕巢校區"}
CAVEAT_WEAK = "只有部分字詞相符，請先確認內容是否回答問題，請勿推測"
CAVEAT_OLD_NOTICES = "沒有現行資料符合；以下是一年以上或已過公告期的舊公告，內容可能已變動，請勿當作現行資料，並以學校最新公告為準"

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
_CJK_RUN = re.compile(f"[{CJK}]+")


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
    coverage: float = 0.0  # of the query's topic
    whole: float = 0.0  # of the topic and the named places together (the ranking)
    upcoming: bool = False  # an hours card that starts after on_date
    day_from: str = ""


@dataclass
class Retrieval:
    """What a query found before thresholds and budget: match is entity / ambiguous / text, or absent (the places named
    are all on another campus; entity_ids lists them); coverage is None for an entity match (its strength does not
    depend on wording)."""
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
        out.append(_Hit(r["score"], 1, frozenset(r["tokens"].split()), view, day_from=p["valid"][0]))
    return out


def _soonest_events_first(hits: list[_Hit]) -> list[_Hit]:
    """Among equally covered hits the calendar events keep their places but take them in date order: the soonest (or
    ongoing) first. bm25 favours a short summary, and an agent that reads only the first answers with it."""
    out = list(hits)
    for _, group in groupby(range(len(out)), key=lambda i: (out[i].whole, out[i].coverage, out[i].upcoming)):
        at = [i for i in group if out[i].order == 1]  # the calendar events
        for i, h in zip(at, sorted((out[i] for i in at), key=lambda h: h.day_from), strict=True):
            out[i] = h
    return out


def _doc_hits(conn: sqlite3.Connection, match: str, today: dt.date, n: int, *, old: bool = False) -> list[_Hit]:
    """Current notices (C5), or with old=True the others, each labelled as such."""
    rows = conn.execute(
        f"""SELECT d.title, d.unit, d.published_on, d.url, f.tokens, bm25(doc_fts) AS score
              FROM doc_fts f JOIN doc d ON d.id = f.doc_id
             WHERE doc_fts MATCH :q AND {OLD_DOC_SQL if old else CURRENT_DOC_SQL}
             ORDER BY score LIMIT :n""", {"q": match, "n": n, **current_doc_params(today)})
    out = []
    for r in rows:
        view = {"title": r["title"], "unit": r["unit"], "published_on": r["published_on"], "url": r["url"],
                "label": old_notice_label(r["published_on"]) if old else NOTICE_LABEL}
        out.append(_Hit(r["score"], 2, frozenset(r["tokens"].split()), {k: v for k, v in view.items() if v},
                        day_from=(r["published_on"] or "") if old else ""))  # old notices: newest first on ties
    return out


@dataclass(frozen=True)
class _Clause:
    tokens: frozenset[str]  # topic bigrams of one clause
    aliases: tuple[tuple[frozenset[str], tuple[frozenset[str], ...]], ...]  # (alias bigrams, each canonical term's)


def _joints(text: str, seen: frozenset[str]) -> set[str]:
    """Bigrams that only join two words: inside a run of CJK characters, in none of the candidate hits, between two
    bigrams that are (修截 in 停修截止, 風停 in 颱風停課). A word no hit has (學餐 in 學餐菜單) is not between two found
    bigrams, so it still counts against the hits."""
    out = set()
    for run in _CJK_RUN.findall(norm_text(text)):
        grams = [run[i:i + 2] for i in range(len(run) - 1)]
        out |= {g for a, g, b in zip(grams, grams[1:], grams[2:]) if g not in seen and a in seen and b in seen}
    return out


def _clauses(text: str, *, topic: bool, seen: frozenset[str] | None = None) -> list[_Clause]:
    """The clauses of a query and their bigrams. topic=True also leaves out the words that only say what kind of answer
    is wanted (開放時間, 公告), and a clause with nothing left is skipped; topic=False keeps a clause's campus name when
    it is all the clause has (燕巢). With the candidate hits' bigrams (seen), the bigrams that only join two words are
    left out too (_joints); that never empties a clause."""
    out = []
    for clause in _CLAUSE.split(_cleaned(text)):
        body = _strip(clause, _CAMPUS_TEXT)
        if topic:
            body = _strip(body, KIND_WORDS)
            toks = frozenset(t for t in _tokens(body) if t not in KIND_TOKENS)
        else:
            toks = frozenset(_tokens(body) or _tokens(clause))
        pairs = alias_pairs(body if topic else clause)
        if seen is not None:  # an alias is found when one of its canonical terms is (退課 by 停修)
            found = seen.union(*(tokenize(key) for key, terms in pairs if any(set(tokenize(t)) <= seen for t in terms)))
            toks -= _joints(body if topic else clause, found)
        if toks:
            aliases = tuple((frozenset(tokenize(key)) & toks, tuple(frozenset(tokenize(t)) for t in terms))
                            for key, terms in pairs)
            out.append(_Clause(toks, aliases))
    return out


def _covered(c: _Clause, hit: frozenset[str]) -> frozenset[str]:
    covered = c.tokens & hit
    for key, terms in c.aliases:
        if any(t <= hit for t in terms):
            covered |= key
    return covered


def _coverage(clauses: list[_Clause], hit: frozenset[str]) -> float:
    """Share of the query's topic bigrams found in the hit (an alias counts when one of its canonical terms is wholly
    there), taken over the best-covered clause when a question has several (期末考是哪幾天？考完之後還要上課嗎？)."""
    return max((len(_covered(c, hit)) / len(c.tokens) for c in clauses), default=0.0)


@dataclass(frozen=True)
class _Place:
    tokens: frozenset[str]  # bigrams of the name as the query wrote it
    ids: frozenset[str]
    canon: tuple[frozenset[str], ...]  # bigrams of the alias's canonical terms (圖書館 -> 圖資處)


def _places(linked: Linked) -> list[_Place]:
    """The places named, and the units (no ids): neither is the topic, both say whose facts are meant."""
    named = [(text, frozenset(ids)) for text, ids in linked.mentions] + [(u, frozenset()) for u in linked.units]
    return [_Place(frozenset(_tokens(text)), ids,
                   tuple(frozenset(tokenize(t)) for _, terms in alias_pairs(text) for t in terms))
            for text, ids in named]


def _credit(places: list[_Place], h: _Hit) -> frozenset[str]:
    """The named places' bigrams a hit is credited with: an hours card only for its own place (every library card
    says 圖書資訊處, the canonical term of 圖書館), a notice or event for the name or its canonical term; a unit by any
    hit with its name or canonical term (圖資處 by the library cards, 體育室 by its own cards and notices)."""
    out: set[str] = set()
    for p in places:
        if h.entity_id is not None and p.ids:
            out |= p.tokens if h.entity_id in p.ids else set()
        else:
            out |= p.tokens if any(t <= h.tokens for t in p.canon) else p.tokens & h.tokens
    return frozenset(out)


def _whole(topic: list[_Clause], places: list[_Place], h: _Hit) -> float:
    names = frozenset().union(*(p.tokens for p in places))
    credit = len(_credit(places, h))
    return max(((len(_covered(c, h.tokens)) + credit) / (len(c.tokens) + len(names)) for c in topic), default=0.0)


def _full_text(conn: sqlite3.Connection, query: str, linked: Linked, *, kind: str, on: dt.date, today: dt.date,
               campus: str | None, limit: int, old: bool = False) -> tuple[list[_Hit], float]:
    """Ranked hits and the first one's coverage; old=True searches only the old notices (C5). The school's names are
    never searched for (every notice is the school's), nor are unit names while a topic is left: notices that only name
    them would fill the candidates."""
    has_topic = bool(_clauses(linked.rest, topic=True))
    text = _strip(norm_text(query), tuple(linked.schools + linked.units if has_topic else linked.schools))
    tokens = _tokens(expand(_cleaned(text)))
    if not tokens:
        return [], 0.0
    match = fts_query(tokens)
    hits: list[_Hit] = []
    if kind in ("any", "hours", "place") and not old:
        hits += _hours_hits(conn, match, on, campus)
    if kind in ("any", "calendar") and not old:
        hits += _calendar_hits(conn, match, on, CANDIDATES)
    if kind in ("any", "announcement"):
        hits += _doc_hits(conn, match, today, CANDIDATES, old=old)
    seen = frozenset().union(*(h.tokens for h in hits))
    topic = _clauses(linked.rest, topic=True, seen=seen)
    places = _places(linked) if topic else []  # no topic left: every word of the query counts, names included
    topic = topic or _clauses(text, topic=False, seen=seen)
    for h in hits:
        h.coverage = _coverage(topic, h.tokens)
        h.whole = _whole(topic, places, h)
    hits = _fitting([h for h in hits if h.coverage > 0])
    if old:  # among equally covered old notices the newest says the most
        hits.sort(key=lambda h: h.day_from, reverse=True)
        hits.sort(key=lambda h: (-h.whole, -h.coverage))
    else:
        hits.sort(key=lambda h: (-h.whole, -h.coverage, h.upcoming, h.score, h.order))
        hits = _soonest_events_first(hits)
    return hits[:limit], (hits[0].coverage if hits else 0.0)


def retrieve(conn: sqlite3.Connection, tpl: Templates, query: str, *, kind: str, on: dt.date, today: dt.date,
             campus: str | None, limit: int) -> Retrieval:
    """Ranked facts for a query, before thresholds and the token budget (search() and the calibration use it)."""
    exact = exact_places(conn, query, campus)  # a bare place name or id: its card is the answer
    linked = link_query(conn, query, campus)
    ids = exact or linked.ids
    named = exact_places(conn, query) or linked.named  # on any campus
    campus = campus or query_campus(query)
    if named and not ids and kind in ("any", "hours", "place"):  # 燕巢 游泳池: the pool is on the other campus
        return Retrieval("absent", [], 0.0, status="no_data", entity_ids=named)
    if kind == "place":
        if ids:
            return Retrieval("entity", _place_views(conn, ids[:limit]), entity_ids=ids)
        hits, cov = _full_text(conn, query, linked, kind="place", on=on, today=today, campus=campus, limit=MAX_LIMIT)
        ids = list(dict.fromkeys(h.entity_id for h in hits if h.entity_id))
        return Retrieval("text", _place_views(conn, ids[:limit]), cov, entity_ids=ids)
    intent = detect_intent(query)
    place_only = not _clauses(linked.rest, topic=True)  # nothing but place names, campus, question and when words
    if ids and (kind == "hours" or (kind == "any" and (exact or intent == "hours" or (place_only and intent is None)))):
        cands = place_candidates(conn, tpl, ids, on, today)
        status = worst_status({c["status"] for c in cands})
        return Retrieval("entity" if len(cands) == 1 else "ambiguous", cands[:limit], status=status, entity_ids=ids)
    hits, cov = _full_text(conn, query, linked, kind=kind, on=on, today=today, campus=campus, limit=limit)
    return Retrieval("text", [h.view for h in hits[:limit]], cov)


def retrieve_old(conn: sqlite3.Connection, query: str, *, today: dt.date, campus: str | None, limit: int) -> Retrieval:
    """The old notices for a query (C5), ranked like the current ones; used only when nothing current is returned."""
    linked = link_query(conn, query, campus)
    hits, cov = _full_text(conn, query, linked, kind="announcement", on=today, today=today, campus=campus, limit=limit,
                           old=True)
    return Retrieval("text", [h.view for h in hits], cov, status="stale")


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
    if r.match == "absent":
        where = CAMPUS_ZH.get(campus or query_campus(query) or "", "此校區")
        return make_envelope(**base, found=False, status="no_data", caveat=CAVEAT_NOT_ON_CAMPUS.format(campus=where),
                             similar=[v["name"] for v in _place_views(conn, r.entity_ids)])
    more = len(r.entity_ids) - len(r.facts) if r.entity_ids else 0
    if r.match == "entity" and kind != "place":  # the same fields get_hours answers (next_change, open_now, notices)
        f = r.facts[0]
        env = hours_envelope(conn, db.templates(), f["entity_id"], on, info)
        env["facts"] = [{"entity_id": f["entity_id"], "name": f["name"]}]
        return make_envelope(**base, **env)
    if r.match == "ambiguous" and kind != "place":
        found = any(f["status"] in ("current", "conflict") for f in r.facts)
        return make_envelope(**base, found=found, status=r.status, match="ambiguous", facts=r.facts,
                             caveat=CAVEAT_AMBIGUOUS, more=more or None)
    s = "text" if r.coverage is None else strength(r.coverage)
    if s == "none" or not r.facts:
        if r.match == "text" and _asks_notices(query, kind):  # C5: old notices only when nothing current answers
            old = retrieve_old(conn, query, today=today, campus=campus, limit=limit)
            if old.facts and (s := strength(old.coverage or 0.0)) != "none":
                caveat = "；".join(c for c in (CAVEAT_WEAK if s == "weak" else None, CAVEAT_OLD_NOTICES) if c)
                return make_envelope(**base, found=False, status="stale", match=s, facts=old.facts, caveat=caveat)
        return make_envelope(**base, found=False, status="no_data", caveat=CAVEAT_NOT_FOUND)
    match = "entity" if r.match == "entity" else s
    # The hours cards set the status, as on the ambiguous path (the most severe one); events and notices count as found.
    status = worst_status({f.get("status", "current") for f in r.facts if f.get("entity_id")})
    found = any(f.get("status", "current") not in ("stale", "no_data") for f in r.facts)
    return make_envelope(**base, found=found, status=status, match=match, facts=r.facts,
                         caveat=_caveat(match, status, r.facts), more=more or None)


def _asks_notices(query: str, kind: str) -> bool:
    """Old notices may answer a notice search, or a question that is not about hours or calendar dates: a years-old
    notice only states past hours and past dates, which the cards and the calendar answer (or say they cannot)."""
    return kind == "announcement" or (kind == "any" and detect_intent(query) in (None, "doc"))


def _caveat(match: str, status: str, facts: list[dict] = ()) -> str | None:
    """A weak match, a non-current status and a tentative calendar event each need saying; all are kept together."""
    tentative = CAVEAT_TENTATIVE if any(f.get("status") == "tentative" for f in facts) else None
    return "；".join(c for c in (CAVEAT_WEAK if match == "weak" else None, CAVEATS.get(status), tentative) if c) or None
