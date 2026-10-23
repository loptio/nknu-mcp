"""Entity linking, intent rules and alias expansion for free-text queries. No LLM: an alias table, a longest match and
keyword lists (spec §6.5).

link_entities() matches every name and alias (after alias_norm) inside the query, longest first and without overlaps,
so 燕巢健身房 is one place and not 燕巢 + 健身房. Only places come back (gyms, grounds, libraries); a campus name
(和平/燕巢) narrows the places to that campus instead, and a university or unit name is not a place. link_query() also
says which places the query names on any campus, which school and unit names it uses (高師大, NKNU, 教務處, and every
office a notice comes from: 教務處和平教務組 gives 教務處 and 教務組), and what the query asks once all those names
are taken out."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from nknu.core.textnorm import CJK, norm_text
from nknu.core.vocab import resolve_place

PLACE_TYPES = ("ExerciseGym", "Place", "Library")
NAME_TYPES = ("CollegeOrUniversity", "Organization")  # whose question it is, never what it asks (Linked.schools, units)
UNIT = "unit:"  # id prefix of an office named only as a notice's unit (doc.unit), not in the vocabulary
CAMPUS_WORDS = {"heping": ("和平", "heping"), "yanchao": ("燕巢", "yanchao")}

_KEEP = re.compile(rf"[{CJK}a-z0-9]")


def _keyed(s: str) -> tuple[str, set[int], list[int]]:
    """alias_norm(s), the offsets in it where a removed character (space, punctuation) used to be, and the offset in
    norm_text(s) of each character it kept."""
    out: list[str] = []
    cuts: set[int] = set()
    pos: list[int] = []
    for i, ch in enumerate(norm_text(s).replace("臺", "台")):
        if _KEEP.match(ch):
            out.append(ch)
            pos.append(i)
        else:
            cuts.add(len(out))
    return "".join(out), cuts, pos


def _word(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _bounded(q: str, cuts: set[int], lo: int, hi: int) -> bool:
    """An ASCII edge of a match must not run on into an ASCII word (gym is not a match inside gymnasium)."""
    if lo > 0 and lo not in cuts and _word(q[lo]) and _word(q[lo - 1]):
        return False
    return not (hi < len(q) and hi not in cuts and _word(q[hi - 1]) and _word(q[hi]))


# An office inside a unit string: 學生事務處生活輔導組 -> 學生事務處, 生活輔導組.
_OFFICE = re.compile(r".+?(?:處|室|組|中心|學院|學系|研究所|碩士班)")
_ASIDE = re.compile(r"[（(][^）)]*[）)]")  # 校安中心(原軍訓室)
_CAMPUS_PREFIX = re.compile(r"^(?:和平|燕巢)")


def _unit_names(unit: str) -> list[str]:
    """The offices a notice's unit names, each at least 3 characters, also without a campus prefix (和平教務組 ->
    教務組)."""
    out = []
    for m in _OFFICE.finditer(_ASIDE.sub("", unit)):
        for name in (m.group(), _CAMPUS_PREFIX.sub("", m.group())):
            if len(name) >= 3 and name not in out:
                out.append(name)
    return out


def _alias_table(conn: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, tuple[str, str | None]]]:
    """alias_norm key -> entity ids, and entity id -> (type, campus). Offices named only by notices (doc.unit) are
    there too, as UNIT ids of type Organization."""
    keys: dict[str, set[str]] = {}
    kinds: dict[str, tuple[str, str | None]] = {}
    for r in conn.execute("SELECT id, type, campus, name_zh, coalesce(name_en, '') FROM entity"):
        kinds[r[0]] = (r[1], r[2])
        for name in (r[3], r[4]):
            if key := _keyed(name)[0]:
                keys.setdefault(key, set()).add(r[0])
    for alias_key, entity_id in conn.execute("SELECT alias_norm, entity_id FROM entity_alias"):
        if alias_key:
            keys.setdefault(alias_key, set()).add(entity_id)
    for (unit,) in conn.execute("SELECT DISTINCT unit FROM doc WHERE unit IS NOT NULL AND unit <> ''"):
        for name in _unit_names(unit):
            if (key := _keyed(name)[0]) and key not in keys:
                keys[key] = {UNIT + name}
                kinds[UNIT + name] = ("Organization", None)
    return keys, kinds


def _longest_matches(q: str, cuts: set[int], keys: dict[str, set[str]]) -> list[tuple[int, int, str]]:
    """(start, end, key) of the keys found in q, longest first (then leftmost), never overlapping; in query order."""
    found = []
    for key in keys:
        at = q.find(key)
        while at != -1:
            if _bounded(q, cuts, at, at + len(key)):
                found.append((at, at + len(key), key))
            at = q.find(key, at + 1)
    found.sort(key=lambda m: (m[0] - m[1], m[0]))
    taken = [False] * len(q)
    chosen = []
    for lo, hi, key in found:
        if not any(taken[lo:hi]):
            taken[lo:hi] = [True] * (hi - lo)
            chosen.append((lo, hi, key))
    return sorted(chosen)


def query_campus(query: str) -> str | None:
    """The campus a query names (和平/燕巢, Heping/Yanchao); None when it names neither or both."""
    q = _keyed(query)[0]
    named = [c for c, words in CAMPUS_WORDS.items() if any(w in q for w in words)]
    return named[0] if len(named) == 1 else None


@dataclass(frozen=True)
class Linked:
    ids: list[str]  # places named, on the campus asked (the `campus` argument, else a campus the query names)
    named: list[str]  # places named, any campus
    rest: str  # norm_text(query) with every place, school and unit name blanked out: what the query asks
    mentions: list[tuple[str, list[str]]]  # (the name as written, after norm_text; the places it names)
    schools: list[str] = field(default_factory=list)  # the university's names as written, after norm_text
    units: list[str] = field(default_factory=list)  # offices (圖資處, 教務組) as written, after norm_text


def link_query(conn: sqlite3.Connection, query: str, campus: str | None = None) -> Linked:
    q, cuts, pos = _keyed(query)
    text = list(norm_text(query))
    if not q:
        return Linked([], [], "".join(text), [])
    keys, kinds = _alias_table(conn)
    campus = campus or query_campus(query)
    ids: list[str] = []
    named: list[str] = []
    mentions: list[tuple[str, list[str]]] = []
    names: dict[str, list[str]] = {t: [] for t in NAME_TYPES}
    for lo, hi, key in _longest_matches(q, cuts, keys):
        places = [e for e in sorted(keys[key]) if kinds.get(e, ("", None))[0] in PLACE_TYPES]
        a, b = pos[lo], pos[hi - 1] + 1
        if not places:
            if found := [t for t in NAME_TYPES if any(kinds.get(e, ("", None))[0] == t for e in keys[key])]:
                names[found[0]].append("".join(text[a:b]))
                text[a:b] = [" "] * (b - a)
            continue
        mentions.append(("".join(text[a:b]), places))
        text[a:b] = [" "] * (b - a)
        named += [e for e in places if e not in named]
        ids += [e for e in places if (campus is None or kinds[e][1] == campus) and e not in ids]
    return Linked(ids, named, "".join(text), mentions, names["CollegeOrUniversity"], names["Organization"])


def link_entities(conn: sqlite3.Connection, query: str, campus: str | None = None) -> list[str]:
    """Places named in free text, in query order. `campus` (or a campus named in the query) keeps that campus only.
    Several ids mean the text is ambiguous: the caller lists them, never picks one."""
    return link_query(conn, query, campus).ids


def exact_places(conn: sqlite3.Connection, query: str, campus: str | None = None) -> list[str]:
    """The places the whole query names exactly (an entity id, or a name/alias equal after alias_norm)."""
    ids = resolve_place(conn, query.strip())
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, type, campus FROM entity WHERE id IN ({marks})", ids).fetchall()
    return [r[0] for r in rows if r[1] in PLACE_TYPES and (campus is None or r[2] == campus)]


# ---------------------------------------------------------------- intent

Intent = Literal["hours", "calendar", "doc"]
# Asking whether a place is closed asks for its hours too (體育館關了嗎); a bare 關 is not one (相關, 關於, 機關).
_HOURS = tuple(norm_text(w) for w in ("開", "開放", "幾點", "時間", "營業", "open", "hours", "關門", "關了", "關嗎",
                                      "關閉", "休息", "休館", "可以去", "close", "closing"))
_NOT_HOURS = tuple(norm_text(w) for w in ("可以去哪",))  # where to go, not whether it is open
_CALENDAR = tuple(norm_text(w) for w in ("行事曆", "考試", "期中", "期末", "截止", "停修", "放假", "開學", "deadline", "exam"))
_DOC = tuple(norm_text(w) for w in ("公告", "消息", "通知", "news"))


def detect_intent(query: str) -> Intent | None:
    """Keyword rules on norm_text. Calendar words are taken out before the hours check, so the 開 inside 開學 does not
    make 下學期什麼時候開學 an hours question (期中考 圖書館 開放時間 still is one)."""
    q = norm_text(query)
    rest = q
    for w in _CALENDAR + _NOT_HOURS:
        rest = rest.replace(w, " ")
    if any(w in rest for w in _HOURS):
        return "hours"
    if any(w in q for w in _CALENDAR):
        return "calendar"
    if any(w in q for w in _DOC):
        return "doc"
    return None


# Words that only say what kind of answer is wanted (opening hours, a notice) or when, not which place or what about
# it: every hours card has 開放 (its template) and 時間 (its source title), so search leaves them out of the coverage;
# a lone 開 or 關 is dropped as a token. 下學期 says when, like 明天: no calendar entry is worded 下學期開學.
KIND_WORDS = tuple(sorted((norm_text(w) for w in (
    "開放", "時間", "時段", "幾點", "營業", "開門", "開到", "有開", "關門", "關閉", "休息", "週末", "平日", "今天",
    "明天", "今晚", "晚上", "早上", "上午", "下午", "中午", "這學期", "本學期", "下學期", "上學期", "公告", "消息",
    "通知")), key=len, reverse=True))
KIND_TOKENS = frozenset((norm_text("開"), norm_text("關"), "open", "opens", "opening", "close", "closes", "closed",
                         "closing", "time", "hour", "hours", "weekend", "weekends", "today", "tonight", "tomorrow",
                         "news"))


# ---------------------------------------------------------------- alias expansion (full-text path only)

# User wording -> the terms the official text uses. From prototypes/search/common.py, plus the closure words (a closure
# card says 暫停開放) and English words that the bilingual calendar or Chinese notices spell otherwise.
ALIASES = {
    "健身房": ["重量訓練室"], "重訓": ["重量訓練室"], "游泳館": ["游泳池"], "泳池": ["游泳池"],
    "圖書館": ["圖資處", "圖書資訊處"], "圖資處": ["圖書館"], "接駁車": ["校車", "交通車"], "校車": ["交通車"],
    "交通車": ["校車"], "機車通行證": ["停車證"], "通行證": ["停車證"], "地址": ["校址"], "入住": ["開舍"],
    "運動會": ["校慶"], "K書中心": ["愛閱館"], "自習室": ["愛閱館"], "退課": ["停修"], "高師大": ["國立高雄師範大學"],
    "閉館": ["暫停開放"], "休館": ["暫停開放"], "開學": ["上課開始"], "期限": ["截止"],
    "midterm": ["mid-term", "期中"], "withdrawal": ["停修"], "withdraw": ["停修"], "typhoon": ["颱風"],
}
_ALIAS_N = [(norm_text(k), [(v, norm_text(v)) for v in vs]) for k, vs in ALIASES.items()]


def alias_pairs(query: str) -> list[tuple[str, list[str]]]:
    """(alias as normalised, canonical terms not already in the query) for every alias found in the query."""
    nq = norm_text(query)
    out = []
    for k, vs in _ALIAS_N:
        if k in nq and (extra := [v for v, vn in vs if vn not in nq]):
            out.append((k, extra))
    return out


def expand(query: str) -> str:
    """Append the canonical terms for any alias found in the query (used for the full-text path only)."""
    extra = [v for _, vs in alias_pairs(query) for v in vs]
    return query + (" " + " ".join(dict.fromkeys(extra)) if extra else "")
