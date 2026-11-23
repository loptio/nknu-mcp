"""Alias groups: words that mean the same thing on the site, the school's wording and the words students type (owner
tentative decision 10-31: an alias table; the seed is config/aliases.yaml). A query that names one member also searches
for the others, and in search a member counts as found when another member of its group is in the document.

The unit tree's names go one way (owner 11-08 10:16, 「让搜系所/处室简称时能100%匹配全称」; expand's names): a short
name in the query (事經系, 生輔組) brings the unit's name and its other short names; the name itself brings nothing
【Claude】: every document of a unit's own site carries its short names on its unit line already (build.unit_words),
and a name's short names are loose as bigrams (軟體系's 軟體 + 體系 would count 軟體工程與管理學系 as found in any
document with 軟體 and 體系 somewhere).

Plan 2026-11-14 T5 (direction 2026-11-13 第 5 件; search.RankConfig.topic_required) asks two more things of a query:
where it writes a unit's name (unit_spans), and which of the tokens expand brings are synonyms of what it asks about
(synonyms: the table's groups, never a unit's other names). expand itself is as it was."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

# where a unit's name ends, as units.detect reads one (系列, 組別, 所以 … are other words), so the unit names T5 takes
# out of a query's words are the units hybrid finds in it
from nknu.content.units import _edges_ok as _unit_edges
from nknu.core.textnorm import norm_text, tokenize

_ASCII = re.compile(r"[a-z0-9]")


def load_aliases(path: Path) -> list[list[str]]:
    """YAML {groups: [[停修, 棄選, 退選], ...]}; members stripped and deduped. A broken table stops the build: another
    shape raises TypeError, a group of fewer than two members or a member with nothing to search for ValueError."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list | None):
        raise TypeError(f"{path}: expected a mapping with a list under 'groups'")
    out: list[list[str]] = []
    for group in data.get("groups") or []:
        if not isinstance(group, list):
            raise TypeError(f"{path}: alias group {group!r} is not a list")
        members = list(dict.fromkeys("" if m is None else str(m).strip() for m in group))
        if len(members) < 2 or not all(tokenize(m) for m in members):
            raise ValueError(f"{path}: alias group {group!r} needs two or more members with words in them")
        out.append(members)
    return out


def _whole(t: str, i: int, j: int) -> bool:
    """An ASCII edge of a match must not run on into an ASCII word (sso is not a match inside lasso)."""
    if i > 0 and _ASCII.match(t[i]) and _ASCII.match(t[i - 1]):
        return False
    return not (j < len(t) and _ASCII.match(t[j - 1]) and _ASCII.match(t[j]))


def _spans(t: str, key: str) -> list[tuple[int, int]]:
    """Where normalised text t writes key (a member's norm_text form), an ASCII edge never inside an ASCII word."""
    out = []
    i = t.find(key) if key else -1
    while i != -1:
        if _whole(t, i, i + len(key)):
            out.append((i, i + len(key)))
        i = t.find(key, i + 1)
    return out


def _taken(t: str, groups: Sequence[Sequence[str]],
           names: Sequence[Sequence[str]]) -> list[tuple[int, int, int, str]]:
    """expand's matches in normalised text t, in text order: (start, end, group, member), the unit tree's groups
    numbered after the table's. Longest first and without overlaps; of names only the short names, none where a
    unit's name is written."""
    named_units = [span for group in names for span in _spans(t, norm_text(group[0]))]
    found = [(i, j, gi, m) for gi, group in enumerate(groups) for m in group for i, j in _spans(t, norm_text(m))]
    found += [(i, j, len(groups) + k, m) for k, group in enumerate(names) for m in group[1:]
              for i, j in _spans(t, norm_text(m)) if all(j <= a or i >= b for a, b in named_units)]
    taken: list[tuple[int, int, int, str]] = []
    for f in sorted(found, key=lambda f: (f[0] - f[1], f[0])):
        if all(f[1] <= g[0] or f[0] >= g[1] for g in taken):
            taken.append(f)
    taken.sort()
    return taken


def expand(query: str, groups: Sequence[Sequence[str]],
           names: Sequence[Sequence[str]] = ()) -> tuple[list[str], list[str]]:
    """(extra tokens, matched terms). Members are found in norm_text(query), longest first and without overlaps (加退選
    is one word, not 加 + 退選); matched terms are the members as the table writes them, in query order; extra tokens
    are the other members' tokens the query does not already have, group by group.

    names: the unit tree's groups, a unit's name first, then its short names (search.unit_names). Only a short name is
    matched and brings the others; a unit's name in the query brings nothing and is no matched term, and no short name
    it overlaps matches (生活輔導組 is not 輔導, another unit's short name), while an alias of the table's groups still
    does (成人教育研究所甄試's 研究所甄試: review of 11-08 10:16's change)."""
    taken = _taken(norm_text(query), groups, names)
    every = [*groups, *names]
    have = set(tokenize(query))
    extra: list[str] = []
    for gi in dict.fromkeys(f[2] for f in taken):
        for member in every[gi]:
            extra += [tok for tok in dict.fromkeys(tokenize(member)) if tok not in have and tok not in extra]
    return extra, list(dict.fromkeys(f[3] for f in taken))


def unit_spans(query: str, spellings: Iterable[str]) -> list[tuple[int, int]]:
    """Plan 2026-11-14 T5: where norm_text(query) writes a unit, by any of spellings (every name and short name of
    the unit tree, search.unit_spellings): (start, end), in text order, the longest name first and without overlaps
    (生活輔導組 is that unit, not the 輔導 inside it). A name is one as nknu.content.units reads it (_edges_ok): never
    the start of 系統, 系列, 組別, 所以 or 所有 (音樂系列講座 names no department), an ASCII edge never inside an ASCII
    word. Unlike units.detect, a name must be written without spaces inside: a query is typed, not read from a PDF."""
    t = norm_text(query)
    found = []
    for key in dict.fromkeys(norm_text(s) for s in spellings):
        found += [(i, j) for i, j in _spans(t, key) if _unit_edges(t, i, j, key)]
    taken: list[tuple[int, int]] = []
    for f in sorted(found, key=lambda f: (f[0] - f[1], f[0])):
        if all(f[1] <= g[0] or f[0] >= g[1] for g in taken):
            taken.append(f)
    return sorted(taken)


def synonyms(query: str, groups: Sequence[Sequence[str]], names: Sequence[Sequence[str]] = (),
             within: Sequence[tuple[int, int]] = ()) -> list[str]:
    """Plan 2026-11-14 T5 (「讲什么的词…加上别名表带出来的词」): the part of expand's extra tokens that the table's
    groups bring, group by group, each token once: other words for what the query asks about, which admit a document
    as the query's own words do. What a unit's short name brings (names) is the same unit said again, so it is left
    out. within: the query's unit names (unit_spans); a group all of whose matches lie inside one brings nothing
    【Claude】: in 學生輔導中心 the table's 輔導 is a part of the name, not what is asked."""
    taken = _taken(norm_text(query), groups, names)
    have = set(tokenize(query))
    out: list[str] = []
    for gi in dict.fromkeys(f[2] for f in taken):
        if gi >= len(groups) or all(any(a <= f[0] and f[1] <= b for a, b in within) for f in taken if f[2] == gi):
            continue
        for member in groups[gi]:
            out += [tok for tok in dict.fromkeys(tokenize(member)) if tok not in have and tok not in out]
    return out
