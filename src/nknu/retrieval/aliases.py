"""Alias groups: words that mean the same thing on the site, the school's wording and the words students type (owner
tentative decision 10-31: an alias table; the seed is config/aliases.yaml). A query that names one member also searches
for the others, and in search a member counts as found when another member of its group is in the document.

The unit tree's names go one way (owner 11-08 10:16, 「让搜系所/处室简称时能100%匹配全称」; expand's names): a short
name in the query (事經系, 生輔組) brings the unit's name and its other short names; the name itself brings nothing
【Claude】: every document of a unit's own site carries its short names on its unit line already (build.unit_words),
and a name's short names are loose as bigrams (軟體系's 軟體 + 體系 would count 軟體工程與管理學系 as found in any
document with 軟體 and 體系 somewhere)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import yaml

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


def expand(query: str, groups: Sequence[Sequence[str]],
           names: Sequence[Sequence[str]] = ()) -> tuple[list[str], list[str]]:
    """(extra tokens, matched terms). Members are found in norm_text(query), longest first and without overlaps (加退選
    is one word, not 加 + 退選); matched terms are the members as the table writes them, in query order; extra tokens
    are the other members' tokens the query does not already have, group by group.

    names: the unit tree's groups, a unit's name first, then its short names (search.unit_names). Only a short name is
    matched and brings the others; a unit's name in the query brings nothing and is no matched term, and no short name
    it overlaps matches (生活輔導組 is not 輔導, another unit's short name), while an alias of the table's groups still
    does (成人教育研究所甄試's 研究所甄試: review of 11-08 10:16's change)."""
    t = norm_text(query)

    def spans(member: str) -> list[tuple[int, int]]:
        key, out = norm_text(member), []
        i = t.find(key) if key else -1
        while i != -1:
            if _whole(t, i, i + len(key)):
                out.append((i, i + len(key)))
            i = t.find(key, i + 1)
        return out

    every = [*groups, *names]
    named_units = [span for group in names for span in spans(group[0])]
    found = [(i, j, gi, m) for gi, group in enumerate(groups) for m in group for i, j in spans(m)]
    found += [(i, j, len(groups) + k, m) for k, group in enumerate(names) for m in group[1:] for i, j in spans(m)
              if all(j <= a or i >= b for a, b in named_units)]
    taken: list[tuple[int, int, int, str]] = []
    for f in sorted(found, key=lambda f: (f[0] - f[1], f[0])):
        if all(f[1] <= g[0] or f[0] >= g[1] for g in taken):
            taken.append(f)
    taken.sort()
    have = set(tokenize(query))
    extra: list[str] = []
    for gi in dict.fromkeys(f[2] for f in taken):
        for member in every[gi]:
            extra += [tok for tok in dict.fromkeys(tokenize(member)) if tok not in have and tok not in extra]
    return extra, list(dict.fromkeys(f[3] for f in taken))
