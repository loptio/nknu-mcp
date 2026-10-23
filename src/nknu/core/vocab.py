"""Curated vocabulary (entities, hierarchy, aliases), source tiers, terms and TTL filling."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from nknu.core.ids import ENTITY_ID_RE
from nknu.core.textnorm import alias_norm
from nknu.core.timeutil import today_tpe

if TYPE_CHECKING:
    from nknu.core.records import FactRecord

CAMPUSES = ("heping", "yanchao")


def load_vocab(path: Path) -> dict:
    vocab = yaml.safe_load(Path(path).read_text())
    for e in vocab["entities"]:
        if not ENTITY_ID_RE.match(e["id"]):
            raise ValueError(f"bad entity id {e['id']!r}")
    return vocab


def _campus_of(entity_id: str) -> str | None:
    parts = entity_id.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in CAMPUSES else None


def seed(conn: sqlite3.Connection, vocab: dict) -> None:
    """Upsert the vocabulary. Entities are never deleted (facts reference them); relations, aliases and
    authorities are curated settings and are replaced wholesale."""
    with conn:
        for e in vocab["entities"]:
            conn.execute(
                """INSERT INTO entity(id, type, name_zh, name_en, campus, wikidata_qid, status)
                   VALUES (:id, :type, :name_zh, :name_en, :campus, :wikidata_qid, :status)
                   ON CONFLICT(id) DO UPDATE SET type=excluded.type, name_zh=excluded.name_zh, name_en=excluded.name_en,
                     campus=excluded.campus, wikidata_qid=excluded.wikidata_qid, status=excluded.status""",
                {"name_en": None, "wikidata_qid": None, "status": "active", **e, "campus": _campus_of(e["id"])},
            )
        conn.execute("DELETE FROM entity_relation")
        conn.executemany(
            "INSERT INTO entity_relation(subject, predicate, object, note) VALUES (:subject, :predicate, :object, :note)",
            [{"note": None, **r} for r in vocab.get("relations", [])],
        )
        conn.execute("DELETE FROM attribute_authority")
        conn.executemany(
            "INSERT INTO attribute_authority(attribute, entity_id, org_id) VALUES (:attribute, :entity_id, :org_id)",
            vocab.get("attribute_authority", []),
        )
        conn.execute("DELETE FROM entity_alias")
        aliases = vocab.get("aliases", [])
        owners: dict[str, set[str]] = {}
        for a in aliases:
            owners.setdefault(alias_norm(a["alias"]), set()).add(a["entity_id"])
        conn.executemany(
            "INSERT OR REPLACE INTO entity_alias(entity_id, alias, alias_norm, kind, ambiguous) VALUES (?,?,?,?,?)",
            [(a["entity_id"], a["alias"], alias_norm(a["alias"]), a["kind"], int(len(owners[alias_norm(a["alias"])]) > 1))
             for a in aliases],
        )


def ancestors(conn: sqlite3.Connection, entity_id: str, on: date | None = None) -> list[str]:
    """containedInPlace chain upwards (nearest first), at most 6 levels."""
    t = (on or today_tpe()).isoformat()
    out, cur = [], entity_id
    for _ in range(6):
        row = conn.execute(
            "SELECT object FROM entity_relation WHERE subject=? AND predicate='containedInPlace' AND valid_from<=? AND ?<valid_to",
            (cur, t, t),
        ).fetchone()
        if row is None:
            break
        cur = row[0]
        out.append(cur)
    return out


def managing_org(conn: sqlite3.Connection, entity_id: str, attribute: str, on: date | None = None) -> str | None:
    row = conn.execute("SELECT org_id FROM attribute_authority WHERE attribute=? AND entity_id=?", (attribute, entity_id)).fetchone()
    if row:
        return row[0]
    for e in [entity_id, *ancestors(conn, entity_id, on)]:
        row = conn.execute("SELECT object FROM entity_relation WHERE subject=? AND predicate='managedBy'", (e,)).fetchone()
        if row:
            return row[0]
    return None


def tier(conn: sqlite3.Connection, *, publisher: str, kind: str, entity_id: str, attribute: str) -> int:
    """1 managing unit's table/ICS, 2 managing unit's announcement, 3 another official unit, 4 maintainer on site."""
    if kind == "manual":
        return 4
    if publisher == managing_org(conn, entity_id, attribute):
        return 1 if kind in ("table_page", "ics") else 2
    return 3


def current_term(conn: sqlite3.Connection, on: date) -> tuple[date, date] | None:
    """(classes begin, next vacation begins) for the term in force on `on`, from the academic calendar."""
    live = "status NOT IN ('withdrawn','cancelled')"
    begin = conn.execute(
        f"SELECT during_from FROM calendar_event WHERE category='term_begin' AND {live} AND during_from<=? "
        "ORDER BY during_from DESC LIMIT 1", (on.isoformat(),)).fetchone()
    if begin is None:
        return None
    vac = conn.execute(
        f"SELECT during_from FROM calendar_event WHERE category='vacation_begin' AND {live} AND during_from>? "
        "ORDER BY during_from LIMIT 1", (begin[0],)).fetchone()
    if vac is None or on.isoformat() >= vac[0]:
        return None
    return date.fromisoformat(begin[0]), date.fromisoformat(vac[0])


def fill_validity(conn: sqlite3.Connection, rec: FactRecord, today: date) -> FactRecord | None:
    """TTL rule (spec §6.2): a term-based fact without dates is valid until the current term ends.
    Its key gains the term's first day, so each term's table is its own lineage (next term's never closes this one's)."""
    if rec.valid_from is not None and rec.valid_to is not None:
        return rec
    if rec.valid_basis != "term":
        return None
    term = current_term(conn, today)
    if term is None:
        return None
    return replace(rec, key=f"{rec.key}:{term[0]}", valid_from=term[0], valid_to=term[1])


def resolve_place(conn: sqlite3.Connection, place: str) -> list[str]:
    """Exact lookup: an entity id, or a name/alias equal after alias_norm. Several ids mean the name is ambiguous."""
    if conn.execute("SELECT 1 FROM entity WHERE id=?", (place,)).fetchone():
        return [place]
    key = alias_norm(place)
    if not key:
        return []
    ids = {r[0] for r in conn.execute("SELECT entity_id FROM entity_alias WHERE alias_norm=?", (key,))}
    for r in conn.execute("SELECT id, name_zh, name_en FROM entity"):
        if key in (alias_norm(r["name_zh"]), alias_norm(r["name_en"] or "")):
            ids.add(r["id"])
    return sorted(ids)
