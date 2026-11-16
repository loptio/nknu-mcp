"""search(unit=...) (design 2026-11-16-mcp-interface-design §二 `unit`; direction 2026-11-13 第 9 件): a unit's name, as
an agent writes it, turned into a condition on docs.unit and docs.grp that keyword_pool and hybrid_search apply before
their candidate limits, so a narrow unit's documents are not crowded out first.

- a department, college or programme (units.TEACHING_KINDS): its own documents and those of the units below and above
  it, and every document a non-teaching unit published (offices, groups, centres, the school, and names the tree does
  not hold): 「填系所：只留这个系所的文件和全校适用的文件」;
- an office or a centre: its documents and those of the units below it (most of an office's documents name the office
  as unit and the group as grp, 11-15); 「填处室：只留这个处室和它底下各组发的文件」;
- a group: its own documents and its office's documents whose grp names it;
- the school, or a name the tree does not know: no condition; an unknown name comes back with the nearest names.

The name is read the way units.resolve reads any name (its name or an alias, in either script)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from nknu.content.units import TEACHING_KINDS, Unit, ancestors, resolve
from nknu.core.textnorm import norm_text

GROUP = "行政组别"
NEAR = 3  # names an unknown unit comes back with 【Claude】


@dataclass(frozen=True)
class UnitFilter:
    unit_id: str
    kind: str
    sql: str            # a condition on a docs row (its unit and grp columns) that keeps the document
    params: tuple[str, ...]

    def where(self) -> str:
        """The condition on a chunk's document as keyword_pool's query reads it (c is chunks)."""
        return f"c.doc_id IN (SELECT doc_id FROM docs WHERE {self.sql})"


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _below(units: Mapping[str, Unit], top: str) -> set[str]:
    """top and every unit under it."""
    kids: dict[str | None, list[str]] = {}
    for k, u in units.items():
        kids.setdefault(u.parent_id, []).append(k)
    out, todo = set(), [top]
    while todo:
        k = todo.pop()
        if k not in out:
            out.add(k)
            todo += kids.get(k, [])
    return out


def _bigrams(s: str) -> set[str]:
    t = norm_text(s)
    return {t[i : i + 2] for i in range(len(t) - 1)} or set(t)


def nearest(text: str, units: Mapping[str, Unit], n: int = NEAR) -> list[str]:
    """The names of the n units whose name or an alias shares the most character pairs with text (in either script),
    best first, ties by name; none shared, none returned."""
    want = _bigrams(text)
    scored = []
    for u in units.values():
        best = max(len(want & _bigrams(w)) for w in (u.name, *u.aliases))
        if best and u.parent_id is not None:
            scored.append((-best, u.name))
    return [name for _, name in sorted(scored)[:n]]


def unit_filter(units: Mapping[str, Unit], text: str) -> tuple[UnitFilter | None, dict]:
    """(the condition, what the answer says of it: applied's unit). No condition for the school or an unknown name;
    the latter says applied false and the nearest names."""
    unit_id = resolve(text, units) if units and text.strip() else None
    if unit_id is None:
        return None, {"input": text, "applied": False, "near": nearest(text, units) if units else []}
    u = units[unit_id]
    said = {"input": text, "applied": True, "unit": u.name}
    if u.parent_id is None:
        return None, said
    below = _below(units, unit_id)
    if u.kind in TEACHING_KINDS:
        keep = below | set(ancestors(units, unit_id))
        others = tuple(sorted(k for k, v in units.items() if v.kind in TEACHING_KINDS and k not in keep))
        if not others:
            return None, said
        return UnitFilter(unit_id, u.kind, f"(unit IS NULL OR unit NOT IN ({_marks(len(others))}))", others), said
    if u.kind == GROUP and u.parent_id is not None:
        own = tuple(sorted(below))
        return UnitFilter(unit_id, u.kind, f"(unit IN ({_marks(len(own))}) OR (unit = ? AND grp = ?))",
                          (*own, u.parent_id, unit_id)), said
    own = tuple(sorted(below))
    return UnitFilter(unit_id, u.kind, f"(unit IN ({_marks(len(own))}))", own), said


def allowed(conn: sqlite3.Connection, f: UnitFilter) -> set[str]:
    """The documents the condition keeps."""
    return {r[0] for r in conn.execute(f"SELECT doc_id FROM docs WHERE {f.sql}", f.params)}


def allow_mask(doc_ids: tuple[str, ...], keep: set[str]) -> np.ndarray:
    """A bool per document of a vector index (its doc_ids order): kept or not."""
    return np.fromiter((d in keep for d in doc_ids), dtype=bool, count=len(doc_ids))
