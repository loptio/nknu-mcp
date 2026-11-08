"""Academic terms (學年度, 學期) and entry cohorts named in text, and the canonical tokens index and query share.

Every pattern runs on textnorm.norm_text (NFKC, lowercase, Traditional->Simplified). 115-1, 115學年度第1學期 and
115學年度上學期 all give term115s1 plus ay115, so the spelling a page or a query uses stops mattering (owner decision
11-01: one semester spelling in the index). Whitespace is allowed inside a term because PDF text writes
「115 學年度第 1 學期」. A cohort (113學年度起入學) says whom a document applies to, not which period it is about: it
gives entry113 and no ay113. Digits glued to a longer digit run are never read, and years outside AY_MIN..AY_MAX are
dropped (「1 12 學年度」 is a PDF split of 112, not year 12).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from nknu.core.textnorm import norm_text

AY_MIN, AY_MAX = 40, 130
DASH_AY_MIN = 90  # 115-1 and the query-only 115上 / 112入學 forms: 3 digits, 90..AY_MAX (201-1 is a room)


@dataclass(frozen=True)
class Term:
    ay: int
    sem: int | None


@dataclass(frozen=True)
class QueryTerms:
    terms: list[Term]                # in order of appearance, relative words resolved
    cohorts: list[tuple[int, bool]]  # (ay, onwards)
    rest: str                        # norm_text(query) with every term, cohort and relative word blanked out


_SEM_NUM = {"一": 1, "1": 1, "上": 1, "二": 2, "2": 2, "下": 2}
_NUM = r"(?<!\d)(\d{2,3})\s*"
_AY = r"学年度?\s*"
_DASH = re.compile(r"(?<![\d/.\-])(\d{3})[-－]([12])(?![\d/.\-])(?:\s*学期)?")
_SEMESTER = (re.compile(_NUM + _AY + r"第?\s*([一二12])\s*学期"), re.compile(_NUM + _AY + r"([上下])\s*学期"), _DASH)
_SEMESTER_QUERY = re.compile(r"(?<!\d)(\d{3})([上下])(?!午)(?:\s*学期)?")
_YEAR = re.compile(_NUM + r"学年度?")
_ONWARDS = r"(起|以后|（含）\s*以后|\(含\)\s*以后)?\s*(入学|新生)"
_COHORT = re.compile(_NUM + _AY + _ONWARDS)
_COHORT_QUERY = re.compile(r"(?<!\d)(\d{3})\s*" + _ONWARDS)
_RELATIVE = re.compile(r"这学期|本学期|(?<!以)[上下]学期|[本上下]学年度?|(?<!过)去年")
# what an event title can say instead of a whole term (event_term)
_SEMESTER_WORD = re.compile(r"第\s*([一二12])\s*学期")  # a semester without its year
_SEASON = re.compile(r"春季|秋季|(?<![a-z])(?:spring|fall)(?=\s*semester|\b)")  # the second, the first semester
_NEXT_WORD = re.compile(r"[新次]\s*学期|(?<!以)下\s*学期")
_LAST_WORD = re.compile(r"(?<!以)上\s*学期")


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    term: Term | None = None
    cohort: tuple[int, bool] | None = None


def current_term(today: date) -> Term:
    """Aug-Dec: first semester of ROC year y-1911; Jan: still that first semester; Feb-Jul: its second semester."""
    if today.month >= 8:
        return Term(today.year - 1911, 1)
    return Term(today.year - 1912, 1 if today.month == 1 else 2)


def _relative(word: str, today: date) -> Term:
    cur = current_term(today)
    if word in ("这学期", "本学期"):
        return cur
    if word == "下学期":
        return Term(cur.ay, 2) if cur.sem == 1 else Term(cur.ay + 1, 1)
    if word == "上学期":
        return Term(cur.ay - 1, 2) if cur.sem == 1 else Term(cur.ay, 1)
    if word.startswith("本"):
        return Term(cur.ay, None)
    if word.startswith("下"):
        return Term(cur.ay + 1, None)
    return Term(cur.ay - 1, None)  # 上學年, 去年 (the previous academic year)


def _scan(t: str, *, query: bool, today: date | None) -> list[_Hit]:
    """Every term and cohort in normalised text t, without overlaps: semesters first, then cohorts, then year-only
    mentions, then (query and today given) relative words."""
    hits: list[_Hit] = []

    def take(m: re.Match, lo: int, **kw) -> None:
        if lo <= int(m.group(1)) <= AY_MAX and all(m.end() <= h.start or m.start() >= h.end for h in hits):
            hits.append(_Hit(m.start(), m.end(), **kw))

    for p in _SEMESTER + ((_SEMESTER_QUERY,) if query else ()):
        lo = DASH_AY_MIN if p in (_DASH, _SEMESTER_QUERY) else AY_MIN
        for m in p.finditer(t):
            take(m, lo, term=Term(int(m.group(1)), _SEM_NUM[m.group(2)]))
    for p in (_COHORT,) + ((_COHORT_QUERY,) if query else ()):
        lo = DASH_AY_MIN if p is _COHORT_QUERY else AY_MIN
        for m in p.finditer(t):
            take(m, lo, cohort=(int(m.group(1)), m.group(2) is not None))
    for m in _YEAR.finditer(t):
        take(m, AY_MIN, term=Term(int(m.group(1)), None))
    if query and today is not None:
        for m in _RELATIVE.finditer(t):
            if all(m.end() <= h.start or m.start() >= h.end for h in hits):
                hits.append(_Hit(m.start(), m.end(), term=_relative(m.group(), today)))
    return sorted(hits, key=lambda h: h.start)


def _coming(day: date, sem: int) -> Term:
    """Semester sem of the 學年度 under way on day if it has not ended yet, else of the next 學年度."""
    cur = current_term(day)
    return cur if cur.sem == sem else Term(cur.ay, 2) if sem == 2 else Term(cur.ay + 1, 1)


def event_term(title: str, start: date) -> Term:
    """The term of a calendar event, whose title seldom names one (fix round 2, 11-01). A whole term in the title is
    taken as written, also when the event falls in another 學年度 (next term's timetable is published in July, 109-2's
    deadlines ran into September 2021). At a semester's turn the calendar posts what the coming semester needs (fix
    round 3, 11-01), so a semester the title names without its year (第1學期 / 第一學期, 第2學期; Spring / 春季 is the
    second, Fall / 秋季 the first) is that semester while it is under way on the first day, else the coming one (a
    July 第1學期 is next 學年度's); 新學期, 下學期, 次學期 are the semester after the first day's, 上學期 the one
    before it. Else its first day's semester (current_term: August to January is the first). A year alone in an event
    title is an admission year or a cohort (114學年度大學美術術科考試 is held in 113-2), so it is not the event's term."""
    for t in find_terms(title):
        if t.sem is not None:
            return t
    t = norm_text(title)
    if m := _SEMESTER_WORD.search(t):
        return _coming(start, _SEM_NUM[m.group(1)])
    if m := _SEASON.search(t):
        return _coming(start, 2 if m.group() in ("春季", "spring") else 1)
    if _NEXT_WORD.search(t):
        return _relative("下学期", start)
    if _LAST_WORD.search(t):
        return _relative("上学期", start)
    return current_term(start)


def find_terms(text: str) -> list[Term]:
    """Terms a document names, in order of appearance, deduped; year-only mentions have sem=None."""
    return list(dict.fromkeys(h.term for h in _scan(norm_text(text), query=False, today=None) if h.term))


def find_cohorts(text: str) -> list[tuple[int, bool]]:
    """(ay, onwards): 「113學年度起入學」 -> (113, True); 「113學年度入學」 -> (113, False)."""
    return list(dict.fromkeys(h.cohort for h in _scan(norm_text(text), query=False, today=None) if h.cohort))


def query_terms(text: str, today: date | None = None) -> QueryTerms:
    """What a query names: the document patterns plus the query-only 115上 / 115下 / 112入學 and, when today is
    given, the relative words. rest is what is left to search for as words."""
    t = norm_text(text)
    hits = _scan(t, query=True, today=today)
    rest = list(t)
    for h in hits:
        rest[h.start : h.end] = " " * (h.end - h.start)
    return QueryTerms(list(dict.fromkeys(h.term for h in hits if h.term)),
                      list(dict.fromkeys(h.cohort for h in hits if h.cohort)), "".join(rest))


def _spelled(h: _Hit) -> str:
    """A term, cohort or resolved relative word as the school writes it."""
    if h.term is not None:
        return f"{h.term.ay}學年度第{h.term.sem}學期" if h.term.sem else f"{h.term.ay}學年度"
    ay, onwards = h.cohort
    return f"{ay}學年度{'起' if onwards else ''}入學"


def school_spelling(text: str, today: date) -> str:
    """The query as bge-m3 and the reranker read it (plan 2026-11-02 T8; 11-01: one semester spelling): NFKC(text) with
    each term, cohort and relative word query_terms finds written the school's way (115學年度第1學期, 115學年度,
    113學年度起入學, 113學年度入學; 這學期, 下學期 ... resolved from today). Spans are found on norm_text(text) and
    replaced in NFKC(text) when both have the same length; else the spellings go in front of NFKC(text). A template
    only: no word is added or dropped otherwise."""
    plain = unicodedata.normalize("NFKC", text)
    t = norm_text(text)
    hits = _scan(t, query=True, today=today)
    if not hits:
        return plain
    if len(t) != len(plain):
        return " ".join(dict.fromkeys(_spelled(h) for h in hits)) + " " + plain
    parts, pos = [], 0
    for h in hits:
        parts += [plain[pos : h.start], _spelled(h)]
        pos = h.end
    return "".join(parts) + plain[pos:]


def term_tokens(t: Term) -> list[str]:
    """'term115s1' and 'ay115' for a semester, 'ay115' for a year: what the index and the query share."""
    return [f"term{t.ay}s{t.sem}", f"ay{t.ay}"] if t.sem else [f"ay{t.ay}"]


def canonical_tokens(text: str, today: date | None = None) -> list[str]:
    """'term115s1' for a semester (also emits 'ay115'), 'ay115' for a year, 'entry113' for a cohort. today marks a
    query: the query-only spellings and the relative words (這學期, 下學期, 本學年, 去年 ...) are read only then."""
    out: list[str] = []
    for h in _scan(norm_text(text), query=today is not None, today=today):
        if h.term:
            out += term_tokens(h.term)
        elif h.cohort:
            out.append(f"entry{h.cohort[0]}")
    return list(dict.fromkeys(out))
