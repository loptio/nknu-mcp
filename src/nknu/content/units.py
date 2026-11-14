"""The unit tree: school units with their aliases and campus, and the scope a text names (addendum 2026-11-02 F, task
T6c). No model, no network.

Model (the owner's 10-31 design: knowledge hangs on a tree of school units through typed relations; details settled by
Claude 11-02, content-layer design §4): org units form the tree (school → colleges → departments; school → offices →
sections; centres and programmes under their parents). Campus (和平, 燕巢), degree level (學士, 碩士, 博士, 進修) and
the 入學 cohort are qualifiers on units and on relations, never tree levels. applies_to is inherited downwards: a
document for a college covers its departments, one for the school everyone.

Sources. The site probe's list (research/site_probe_2026-10-23/data.json, input data read at build time) gives each
unit's name (n), aliases (a), parent (p) and kind (k). An entry is a unit when its kind is one of KINDS (the list's
学程/计划 is 学程), it is not a round-2 entry (those are other presences of units: Facebook, Instagram, YouTube and
Google Sites pages, camps, conferences) and its name, without a trailing note in brackets
(「國文學系（系所博覽連結）」), ends like a unit's name (處, 組, 學院, 系, 所, 學程, 中心 ...); entries with one name
are one unit. A list alias is kept
only when it ends like a unit's name too and names no other unit; an entry naming itself as its parent (研究發展處)
lists its parts there, so its aliases are left out. A parent the list does not have, or none, is the school. The
list's units without a URL ("nourl") are not read: they carry no aliases and include renamed units the list also has
under their new names. config/units.yaml adds what the list lacks (campus, short names, a parent, a name, a unit),
every entry with its evidence. A unit without a campus of its own takes its nearest ancestor's.

Matching runs on textnorm.norm_text (NFKC, lowercase, Traditional->Simplified), so Simplified forms match the same
names; spaces between the characters of a name are allowed (PDF text), and a name never matches as part of 系統,
系列, 組別, 所以 or 所有.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from nknu.content.terms import find_cohorts
from nknu.core.textnorm import CJK, norm_text, tokenize

LEVELS = ("學士", "碩士", "博士", "進修")
CAMPUSES = ("和平", "燕巢")
KINDS = ("学校", "行政单位", "行政组别", "学院", "系所", "中心", "学程")  # the site list's kinds that are units
TEACHING_KINDS = frozenset({"学院", "系所", "学程"})  # what a document applies to; such a publisher reaches itself
LIST_GROUPS = (frozenset({"系所", "学程"}), frozenset({"学院"}))  # LIST_MIN of one group named make a list
LIST_MIN = 3  # this many departments (or colleges) named in a title and header: a list (名單, 一覽表), so mentions
SKIPPED_ROUND = "第二轮"
CONFIG_FIELDS = frozenset({"campus", "aliases", "parent", "kind", "name", "evidence"})

_KIND_OF = {"学程/计划": "学程"}
_NOTE = re.compile(r"\s*[（(][^（）()]*[）)]\s*$")  # a trailing note in brackets
_UNIT_END = re.compile("(?:大学|学院|处|室|组|队|中心|系|所|学程|专班|班|基地)$")  # on norm_text output
_SPACE = re.compile(r"\s+")
_ASCII = re.compile("[a-z0-9]")
_NOT_AFTER = {"系": "统列", "组": "别", "所": "以有"}  # a name's last character before these is another word's


@dataclass(frozen=True)
class Unit:
    unit_id: str  # the name the unit goes by here (the list's name, or the config's)
    name: str
    kind: str  # one of KINDS
    parent_id: str | None  # None for the school only
    campus: str | None  # its own, else its nearest ancestor's; None: both campuses, or not known
    aliases: tuple[str, ...]  # other names as written, the unit's own name not among them


@dataclass(frozen=True)
class Scope:
    units: frozenset[str]  # unit ids
    campus: frozenset[str]  # of CAMPUSES
    levels: frozenset[str]  # of LEVELS
    cohort_from: int | None  # 「111學年度起入學」 -> 111 (terms.find_cohorts, the first 起 cohort)


@dataclass
class _Draft:
    name: str  # the name used here
    listed: str  # the name in the site list ('' for a unit the config adds)
    kind: str
    parent: str  # a name as written; '' for none
    parent_from_config: bool = False
    campus: str | None = None
    aliases: list[str] = field(default_factory=list)
    config_aliases: list[str] = field(default_factory=list)


def _clean(s: object) -> str:
    """One line, without trailing notes in brackets: 「國立高雄師範大學（校首頁）」 -> 「國立高雄師範大學」."""
    s = " ".join(str(s).split())
    while m := _NOTE.search(s):
        s = s[: m.start()]
    return s.strip()


def _key(s: str) -> str:
    """What a name is matched as: norm_text without spaces."""
    return _SPACE.sub("", norm_text(s))


def _unit_like(name: str) -> bool:
    return bool(_UNIT_END.search(_key(name)))


# --- loading ----------------------------------------------------------------------------------------------------------

def _read_list(path: Path) -> dict[str, _Draft]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, list):
        raise TypeError(f"{path}: expected a mapping with a list under 'sites'")
    drafts: dict[str, _Draft] = {}  # by the key of the listed name
    for entry in sites:
        kind = str(entry.get("k") or "")
        kind = _KIND_OF.get(kind, kind)
        name = _clean(entry.get("n") or "")
        if kind not in KINDS or entry.get("s") == SKIPPED_ROUND or not _unit_like(name):
            continue
        parent = _clean(entry.get("p") or "")
        names_itself = _key(parent) == _key(name)
        aliases = [] if names_itself else [a for a in map(_clean, entry.get("a") or []) if _unit_like(a)]
        draft = drafts.get(_key(name))
        if draft is None:
            drafts[_key(name)] = _Draft(name, name, kind, "" if names_itself else parent, aliases=aliases)
            continue
        if not draft.parent and not names_itself:
            draft.parent = parent
        draft.aliases += [a for a in aliases if a not in draft.aliases]
    return drafts


def _text(path: Path, name: str, value: object, what: str) -> str:
    if not isinstance(value, str) or not tokenize(value):
        raise ValueError(f"{path}: {name}: {what} {value!r} has nothing to match")
    return value.strip()


def read_config(path: Path) -> dict[str, dict]:
    """config/units.yaml, checked: {units: {name in the site list: {campus, aliases, parent, kind, name, evidence}}}.
    Every entry needs evidence; campus is one of CAMPUSES, kind one of KINDS. A broken table stops the build: another
    shape raises TypeError, a bad value ValueError. Returns {cleaned name: entry with every field (None or [])}."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict | None):
        raise TypeError(f"{path}: expected a mapping with a mapping under 'units'")
    out: dict[str, dict] = {}
    for raw, entry in (data.get("units") or {}).items():
        name = _clean(raw)
        if not isinstance(entry, dict):
            raise TypeError(f"{path}: {name}: expected a mapping, got {entry!r}")
        if unknown := sorted(set(entry) - CONFIG_FIELDS):
            raise ValueError(f"{path}: {name}: unknown fields {unknown}")
        if not tokenize(name) or name in out:
            raise ValueError(f"{path}: {name!r} is empty or written twice")
        evidence = entry.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"{path}: {name}: every entry needs its evidence")
        campus = entry.get("campus")
        if campus is not None and campus not in CAMPUSES:
            raise ValueError(f"{path}: {name}: campus {campus!r} is not one of {CAMPUSES}")
        kind = entry.get("kind")
        if kind is not None and kind not in KINDS:
            raise ValueError(f"{path}: {name}: kind {kind!r} is not one of {KINDS}")
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            raise TypeError(f"{path}: {name}: aliases must be a list")
        out[name] = {
            "campus": campus, "kind": kind, "evidence": evidence.strip(),
            "aliases": list(dict.fromkeys(_text(path, name, a, "alias") for a in aliases)),
            "parent": None if entry.get("parent") is None else _text(path, name, entry["parent"], "parent"),
            "name": None if entry.get("name") is None else _clean(_text(path, name, entry["name"], "name")),
        }
    return out


def _apply(drafts: dict[str, _Draft], config: dict[str, dict], path: Path) -> None:
    for name, entry in config.items():
        draft = drafts.get(_key(name))
        if draft is None:
            if entry["kind"] is None or entry["parent"] is None:
                raise ValueError(f"{path}: {name} is not in the site list; give its kind and parent to add it")
            draft = drafts[_key(name)] = _Draft(name, "", entry["kind"], "")
        if entry["kind"] is not None:
            draft.kind = entry["kind"]
        if entry["parent"] is not None:
            draft.parent, draft.parent_from_config = entry["parent"], True
        if entry["campus"] is not None:
            draft.campus = entry["campus"]
        if entry["name"] is not None:
            draft.name = entry["name"]
        draft.config_aliases += entry["aliases"]


def _granted(drafts: list[_Draft], path: Path) -> dict[str, _Draft]:
    """Alias key -> the unit it names. Names win: a list alias that is a unit's name is dropped, a config alias that
    is another unit's name stops the load. A key two units claim goes to the one the config gives it (two in the
    config stop the load); two list claims cancel out."""
    names = {_key(d.name): d for d in drafts} | {_key(d.listed): d for d in drafts if d.listed}
    claims: dict[str, dict[int, tuple[_Draft, bool]]] = {}
    for d in drafts:
        for alias, from_config in [(a, False) for a in d.aliases] + [(a, True) for a in d.config_aliases]:
            k = _key(alias)
            seen = claims.setdefault(k, {}).get(id(d))
            claims[k][id(d)] = (d, from_config or (seen is not None and seen[1]))
    granted: dict[str, _Draft] = {}
    for k, by_unit in claims.items():
        configs = [d for d, from_config in by_unit.values() if from_config]
        owner = names.get(k)
        if owner is not None:
            if any(d is not owner for d in configs):
                raise ValueError(f"{path}: alias {k!r} is the name of {owner.name}")
            continue
        if len(configs) > 1:
            raise ValueError(f"{path}: alias {k!r} is given to {', '.join(d.name for d in configs)}")
        if configs:
            granted[k] = configs[0]
        elif len(by_unit) == 1:
            granted[k] = next(iter(by_unit.values()))[0]
    return granted


def load_units(site_list: Path, config: Path) -> dict[str, Unit]:
    """{unit_id: Unit}, the school first, then in list order, then the units the config adds."""
    drafts_by_key = _read_list(site_list)
    _apply(drafts_by_key, read_config(config), config)
    drafts = list(drafts_by_key.values())
    by_name: dict[str, _Draft] = {}
    for d in drafts:
        if _key(d.name) in by_name:
            raise ValueError(f"{config}: two units are called {d.name}")
        by_name[_key(d.name)] = d
    schools = [d for d in drafts if d.kind == "学校" and not d.parent]
    if len(schools) != 1:
        raise ValueError(f"{site_list}: expected one school without a parent, found {[d.name for d in schools]}")
    school = schools[0]
    granted = _granted(drafts, config)
    lookup = {**granted, **{_key(d.listed): d for d in drafts if d.listed}, **by_name}

    parent: dict[int, _Draft | None] = {id(school): None}
    for d in drafts:
        if d is school:
            continue
        p = lookup.get(_key(d.parent)) if d.parent else None
        if p is None or p is d:
            if d.parent_from_config:
                raise ValueError(f"{config}: {d.name}: parent {d.parent!r} is not a unit")
            p = school
        parent[id(d)] = p
    for d in drafts:
        seen, up = {id(d)}, parent[id(d)]
        while up is not None:
            if id(up) in seen:
                raise ValueError(f"{config}: {d.name} is its own ancestor")
            seen.add(id(up))
            up = parent[id(up)]

    campus: dict[int, str | None] = {}

    def campus_of(d: _Draft) -> str | None:
        if id(d) not in campus:
            p = parent[id(d)]
            campus[id(d)] = d.campus or (campus_of(p) if p is not None else None)
        return campus[id(d)]

    out: dict[str, Unit] = {}
    for d in [school] + [d for d in drafts if d is not school]:
        aliases: dict[str, str] = {}  # key -> the first spelling
        for a in d.aliases + d.config_aliases:
            if granted.get(_key(a)) is d:
                aliases.setdefault(_key(a), a)
        p = parent[id(d)]
        out[d.name] = Unit(d.name, d.name, d.kind, None if p is None else p.name, campus_of(d),
                           tuple(aliases.values()))
    return out


# --- the tree ---------------------------------------------------------------------------------------------------------

def ancestors(units: Mapping[str, Unit], unit_id: str) -> list[str]:
    """The unit itself first, then its parents up to the school. KeyError for an id that is not a unit."""
    if unit_id not in units:
        raise KeyError(unit_id)
    out: list[str] = []
    cur: str | None = unit_id
    while cur is not None and cur in units and cur not in out:
        out.append(cur)
        cur = units[cur].parent_id
    return out


def _roots(units: Mapping[str, Unit]) -> set[str]:
    return {k for k, u in units.items() if u.parent_id is None}


def _school(units: Mapping[str, Unit]) -> str | None:
    roots = [k for k, u in units.items() if u.parent_id is None]
    return next((k for k in roots if units[k].kind == "学校"), roots[0] if roots else None)


@lru_cache(maxsize=8)
def _tables(items: tuple[tuple[str, Unit], ...]) -> tuple[dict[str, tuple[tuple[str, str], ...]], dict[str, str]]:
    """((key, unit id) pairs by the key's first character, key -> unit id): names first, then aliases; the first unit
    keeps a key."""
    keys: dict[str, str] = {}
    for words in ([(k, u.name) for k, u in items], [(k, a) for k, u in items for a in u.aliases]):
        for unit_id, word in words:
            if (k := _key(word)) and k not in keys:
                keys[k] = unit_id
    first: dict[str, list[tuple[str, str]]] = {}
    for k, unit_id in keys.items():
        first.setdefault(k[0], []).append((k, unit_id))
    return {c: tuple(v) for c, v in first.items()}, keys


def resolve(name: str | None, units: Mapping[str, Unit]) -> str | None:
    """The unit an id, a name or an alias names (any script: norm_text); None for nothing or no unit."""
    if not name:
        return None
    if name in units:
        return name
    return _tables(tuple(units.items()))[1].get(_key(_clean(name)))


def name_unit(name: str | None, units: Mapping[str, Unit]) -> tuple[str | None, str]:
    """A unit's name as a page writes it, placed in the tree: (the unit id, what is left of the name as written);
    (None, the name on one line) when it names no unit. For the announcement platform's 公告單位 (the crawler's
    2989f60: the export's 公告单位), which runs the names down the tree together, 「教務處和平教務組」,
    「科技學院電子工程學系」, at times after a heading of its own, 「其他教學研究中心通識教育中心」, and names parts
    the tree does not have, 「國際事務處國際合作組」 (the 82 spellings of the 11-15 exports:
    research/pipeline_2026-11-02/fixes_1115/publishers_1115.txt) 【Claude】. The first that holds, each on the name
    without a trailing note in brackets (「校安中心(原軍訓室)」) and matched as resolve matches:
    - the whole name is a unit's (a name or an alias): that unit, nothing left;
    - it ends with a unit's name, the longest such, and what stands before reads as a unit's name too (it ends as one:
      處, 學院, 中心 …, so 「副校長室」 is not 校長室); when that part is itself a unit of the tree it must be one the
      unit is under: that unit, nothing left;
    - it starts with a unit's name, the longest such: that unit, and the rest as written (a part of it the tree lacks:
      what a notice's 組別 holds)."""
    line = _clean(name or "")
    if not line:
        return None, ""
    _, keys = _tables(tuple(units.items()))
    key = _key(line)
    if key in keys:
        return keys[key], ""
    for cut in range(1, len(key)):  # the longest ending first
        unit_id = keys.get(key[cut:])
        if unit_id is None or not _UNIT_END.search(key[:cut]):
            continue
        above = keys.get(key[:cut])
        if above is None or above in ancestors(units, unit_id):
            return unit_id, ""
    compact = "".join(line.split())
    for cut in range(len(compact) - 1, 0, -1):  # the longest beginning first
        if (unit_id := keys.get(_key(compact[:cut]))) is not None:
            return unit_id, compact[cut:]
    return None, line


# --- detection --------------------------------------------------------------------------------------------------------

def _w(word: str) -> str:
    """A Traditional word as a pattern on norm_text output, spaces allowed between its characters."""
    return r"\s*".join(map(re.escape, norm_text(word)))


_CAMPUS_KEY = {norm_text(c): c for c in CAMPUSES}
_EVERY_CAMPUS = frozenset(CAMPUSES)
_C = "|".join(map(re.escape, _CAMPUS_KEY))
# 燕巢校區, 和平與燕巢校區, 和平、燕巢兩校區; 和平 or 燕巢 alone is no campus (二二八和平紀念日, 和平一路)
_CAMPUS = re.compile(rf"({_C})(?:\s*[与及和、/]\s*({_C}))?\s*(?:两\s*)?{_w('校區')}")
# written forms (addendum F): 大學部/學士班/日間學士班 -> 學士; 碩士班/研究所/碩士在職專班 -> 碩士, and 進修 too for
# 在職專班 (and 在職學位學程, its newer name); 博士班 -> 博士; 進修部 -> 進修. The bare 學士/碩士/博士 also count
# (碩士學位考試), except 學士後, 博士後 and 名譽博士; 碩博士, 博碩士 and 碩、博士 name both.
_LEVEL_FORMS = tuple((re.compile(p), frozenset(levels)) for p, levels in (
    (rf"{_w('碩')}\s*[、/及与和]?\s*{_w('博')}|{_w('博')}\s*[、/及与和]?\s*{_w('碩')}", ("碩士", "博士")),
    (rf"{_w('學士')}(?!\s*{_w('後')})|{_w('大學部')}", ("學士",)),
    (rf"{_w('碩士')}|{_w('研究所')}", ("碩士",)),
    (rf"(?<!{norm_text('名譽')}){_w('博士')}(?!\s*{_w('後')})", ("博士",)),
    (rf"{_w('在職')}\s*(?:{_w('專班')}|{_w('學位學程')})|{_w('進修部')}", ("進修",)),
))


def _edges_ok(t: str, start: int, end: int, key: str) -> bool:
    """An ASCII edge must not run on into an ASCII word; a name ending in 系, 組 or 所 must not run on into 系統, 系列,
    組別, 所以 or 所有 (the character right after it, spaces counting as a break)."""
    if _ASCII.match(key[0]) and start > 0 and _ASCII.match(t[start - 1]):
        return False
    after = t[end] if end < len(t) else ""
    if _ASCII.match(key[-1]) and _ASCII.match(after):
        return False
    return not (after and after in _NOT_AFTER.get(key[-1], ""))


def _find_units(t: str, units: Mapping[str, Unit]) -> list[str]:
    """Unit ids named in normalised text t, in text order: every name and alias found, longest first, no overlaps.
    Names are looked for in t without its spaces (a PDF writes 「軟 體 系」), their positions mapped back to t."""
    first, _ = _tables(tuple(units.items()))
    at = [i for i, ch in enumerate(t) if not ch.isspace()]  # position in t of each character of the compact text
    compact = "".join(t[i] for i in at)
    found: list[tuple[int, int, int, str]] = []
    for c in set(compact):
        for key, unit_id in first.get(c, ()):
            i = compact.find(key)
            while i != -1:
                start, end = at[i], at[i + len(key) - 1] + 1
                if _edges_ok(t, start, end, key):
                    found.append((start, end, len(key), unit_id))
                i = compact.find(key, i + 1)
    taken: list[tuple[int, int, int, str]] = []
    for f in sorted(found, key=lambda f: (-f[2], f[0])):
        if all(f[1] <= g[0] or f[0] >= g[1] for g in taken):
            taken.append(f)
    return list(dict.fromkeys(f[3] for f in sorted(taken)))


def detect(text: str, units: Mapping[str, Unit]) -> Scope:
    """What a text names: units by name or alias (longest match first), campus words, level words and the first
    「X學年度起入學」 cohort (terms.find_cohorts; one cohort without 起 is applies_to's cohort_exact, not repeated)."""
    t = norm_text(text or "")
    campus = {_CAMPUS_KEY[g] for m in _CAMPUS.finditer(t) for g in m.groups() if g}
    levels = {lv for pattern, named in _LEVEL_FORMS if pattern.search(t) for lv in named}
    cohort = next((ay for ay, onwards in find_cohorts(text or "") if onwards), None)
    return Scope(frozenset(_find_units(t, units)), frozenset(campus), frozenset(levels), cohort)


# --- a document's scope -----------------------------------------------------------------------------------------------

def _scope(units: Iterable[str]) -> Scope:
    return Scope(frozenset(units), frozenset(), frozenset(), None)


def reach(publisher: str | None, units: Mapping[str, Unit]) -> Scope:
    """A publisher's own reach, a document's default applies_to: a college, department or programme reaches itself;
    an office, section or centre, and an unknown or missing publisher, the school. A section's campus is not its
    documents' reach (departure from addendum F, 11-02): both of 教務處's campus sections publish school-wide
    procedures (all 43 燕巢教務組 notices of the export are 轉系, 輔系, 離校手續 or 經濟不利學生獎學金; 和平教務組
    posts the fee page, the 學則 and the 前三名 lists), so a campus default would mark them as the other campus's. A
    campus the title or header names narrows a document instead."""
    unit_id = resolve(publisher, units)
    if unit_id is not None and units[unit_id].kind in TEACHING_KINDS:
        return _scope([unit_id])
    school = _school(units)
    return _scope([school] if school else [])


# 「各學系」「各系所」「各班」「各學院」: a title or header about every department, class or college is a list,
# however few of them its first characters name (a fee table, a 前三名 list)
_EACH = re.compile(rf"各\s*(?:{_w('學系')}|{_w('系所')}|{_w('學院')}|{_w('學程')}|系(?![统列])|班|院)")

# 「數學、物理、光通、電子、軟工等系」: LIST_MIN or more items between 、 before 等 (a count may follow: 等三系) and a
# teaching unit's ending are a list, however few of the items are names the tree has (phase-2 review: the bare 軟工 of
# addendum H5, the one name the tree has there, narrowed three past exams of five departments to 軟體系). Each ending
# (norm_text form) with the endings its items' names may take, in order.
_LIST_ENDS = {"系所": ("系", "学系", "所", "研究所"), "学系": ("学系", "系"), "研究所": ("研究所", "所"),
              "学院": ("学院",), "学程": ("学程", "学位学程"), "系": ("系", "学系"), "所": ("所", "研究所")}
_LIST_END = re.compile(rf"等\s*(?:[一二三四五六七八九十两\d]+\s*)?"
                       rf"({_w('系所')}|{_w('學系')}|{_w('研究所')}|{_w('學院')}|{_w('學程')}|系(?![统列])|所(?![以有]))")
_ITEM = rf"(?!等)[{CJK}](?:\s?(?!等)[{CJK}]){{0,15}}"  # a name or a stem, the same read from either end
# the items before an ending, matched on the reversed text from the 等: anchored there, so a long 、 list without an
# ending (a name list) costs one pass, where a forward search would retry it from every character
_ITEMS_BEFORE = re.compile(rf"\s*({_ITEM}(?:\s*、\s*{_ITEM}){{{LIST_MIN - 1},}})")
_LIST_SEP = re.compile(r"\s*、\s*")


def _in_lists(t: str, units: Mapping[str, Unit]) -> set[str]:
    """The units the lists in normalised text t name (LIST_MIN or more items between 、 before 等 and an ending,
    _LIST_END). An item names a unit when it is the unit's name or an alias, alone or with an ending the list's stands
    for (數學 + 系 -> 數學系, 物理 + 學系 -> 物理學系); words may run into the first item (歡迎軟工), so there the
    longest name that ends where the item ends counts."""
    out: set[str] = set()
    if "等" not in t or "、" not in t:  # no list can be there (most texts): skip the search
        return out
    reverse, keys = t[::-1], {}
    for end in _LIST_END.finditer(t):
        if (m := _ITEMS_BEFORE.match(reverse, len(t) - end.start())) is None:
            continue
        keys = keys or _tables(tuple(units.items()))[1]
        ends = _LIST_ENDS[_SPACE.sub("", end.group(1))]
        items = [_SPACE.sub("", it)[::-1] for it in reversed(_LIST_SEP.split(m.group(1)))]  # in the text's order
        for n, item in enumerate(items):
            for stem in (item[i:] for i in range(max(1, len(item) - 1))) if n == 0 else (item,):
                if found := next((keys[k] for k in (stem, *(stem + e for e in ends)) if k in keys), None):
                    out.add(found)
                    break
    return out


def doc_scope(title: str, header: str, publisher: str | None, units: Mapping[str, Unit], *,
              body: str = "") -> tuple[Scope, frozenset[str]]:
    """(applies_to, mentions). applies_to is the publisher's reach, narrowed by what the title and header name: the
    colleges, departments and programmes they name, their campus words, level words and cohort, each replacing the
    default when present. The publisher, its parents and the school restate the publisher and count for nothing.
    Offices, sections and centres named go to mentions (a contact, a forwarder or a co-organiser, not the audience),
    and so do the teaching units of a list: LIST_MIN or more of one LIST_GROUPS group, or every one when the title or
    header says 各學系, 各系所, 各班 or 各學院; and the units a written list names (「數學、物理、光通、電子、軟工等系」,
    _in_lists), however few of its items the tree knows, except a unit the title names outside a list (the
    document's subject: 「軟體系碩士班招生簡章」 stays 軟體系's whatever eligible departments it lists). body (optional,
    e.g. the text's first 3,000 characters) only tells a list the header cuts off: when it names LIST_MIN or more of
    a group, that group's units named in the header (not the title) are a list too, and so are the header's units
    that a written list in it names."""
    text = f"{title or ''} | {header or ''}"
    t = norm_text(text)
    named = detect(text, units)
    pub = resolve(publisher, units)
    own = (set(ancestors(units, pub)) if pub else set()) | _roots(units)
    others = named.units - own
    teaching = {u for u in others if units[u].kind in TEACHING_KINDS}
    title_t, body_t = norm_text(title or ""), norm_text(body) if body else ""
    in_title = set(_find_units(title_t, units)) - _in_lists(title_t, units)  # the subject: named outside a list
    in_lists = _in_lists(t, units) | (_in_lists(body_t, units) & teaching if body_t and teaching else set())
    listed = in_lists - own - in_title
    if _EACH.search(t):
        listed |= teaching
    else:
        in_body = set(_find_units(body_t, units)) - own if body_t else set()
        for group in LIST_GROUPS:
            if len(hit := {u for u in teaching if units[u].kind in group}) >= LIST_MIN:
                listed |= hit
            elif hit and len(hit | {u for u in in_body if units[u].kind in group}) >= LIST_MIN:
                listed |= hit - in_title
    applies = frozenset(teaching - listed) or reach(pub, units).units
    return Scope(applies, named.campus, named.levels, named.cohort_from), frozenset((others - teaching) | listed)


# --- relation ---------------------------------------------------------------------------------------------------------

def _units_verdict(doc: set[str], query: set[str], units: Mapping[str, Unit]) -> int:
    if any(d in ancestors(units, q) for d in doc for q in query):
        return 1
    if any(units[d].kind == units[q].kind and q not in ancestors(units, d) for d in doc for q in query):
        return -1
    return 0


def relation(doc: Scope, query: Scope, units: Mapping[str, Unit]) -> int:
    """+1 when the document covers what the query names, -1 when it names something else of the same kind, else 0.
    Units: the document names the query's unit or one of its ancestors (+1), else a different unit of the same kind
    that is not one of its descendants (-1, another department); the school counts as naming nothing. Campus: the
    query's campus words and its units' campus against the document's campus words; naming every campus is naming the
    whole school, so nothing. Levels only lower (main session 11-03, addendum J; addendum F gave +1 for a shared
    level): a document naming none of the query's levels is -1, one naming one of them 0. So a level named alone never
    raises a score, and a unit named beside it still gives the unit's +1. Why: a query saying only 大學部 or 學士班
    lifted every document that writes 學士 above the school-wide rules that answer it, which name no level (T10,
    keyword-only on a partial corpus: Q08's gold from rank 7 to 18, Q18's from 3 to 10). Each side must name
    something of that kind for it to count; one -1 makes the result -1. Cohort follows the existing cohort rule
    (search's cohort_match) and is not repeated here."""
    roots = _roots(units)
    q_units = {u for u in query.units if u in units} - roots
    d_units = {u for u in doc.units if u in units} - roots
    verdicts = []
    if q_units and d_units:
        verdicts.append(_units_verdict(d_units, q_units, units))
    q_campus = set(query.campus) | {c for u in q_units if (c := units[u].campus)}
    if q_campus and doc.campus and not (q_campus >= _EVERY_CAMPUS or doc.campus >= _EVERY_CAMPUS):
        verdicts.append(1 if q_campus & doc.campus else -1)
    if query.levels and doc.levels:
        verdicts.append(0 if query.levels & doc.levels else -1)  # a shared level adds nothing (addendum J)
    return -1 if -1 in verdicts else 1 if 1 in verdicts else 0
