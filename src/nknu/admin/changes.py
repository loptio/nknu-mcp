"""`nknu changes`: what was written to facts.db since a given day — by the crawler, review, manual entry or undo.

Derived from the append-only fact table (every version and when it was closed), calendar_event, doc and the undo
memory, so it needs no log of its own. Each change carries the id or key that `nknu undo` takes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from nknu.build.cards import names_of
from nknu.build.templates import dows_zh
from nknu.core.timeutil import TAIPEI

OPS = {"new": "新增", "update": "更新", "replaced": "被取代", "retired": "下架", "undone": "撤回"}


@dataclass(frozen=True)
class Change:
    at: str  # UTC timestamp
    kind: str  # fact | event | doc
    op: str  # a key of OPS
    adapter: str
    key: str  # record key (an event's uid)
    ref: str  # the fact version / event uid / doc key; for op new/update this is what `nknu undo` takes
    text: str


def since_utc(day: date) -> str:
    """Taipei midnight of `day` as a UTC timestamp."""
    return datetime(day.year, day.month, day.day, tzinfo=TAIPEI).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_day(valid_to: str) -> str:
    return (date.fromisoformat(valid_to) - timedelta(days=1)).isoformat()


def _fact_text(conn: sqlite3.Connection, f: sqlite3.Row, names: dict[str, str]) -> str:
    value = json.loads(f["value_json"])
    if f["attribute"] == "holiday":
        what = f"放假（{value.get('label', '')}）"
    elif value.get("closed"):
        what = f"不開放（{value.get('reason') or ''}）"
    else:
        rules = conn.execute("SELECT dows, opens, closes FROM hours_rule WHERE fact_id=? ORDER BY dows, opens", (f["id"],))
        what = "；".join(f"{dows_zh(json.loads(r[0]))} {r[1]}–{r[2]}" for r in rules) or "開放"
    span = f["valid_from"] if _last_day(f["valid_to"]) == f["valid_from"] else f"{f['valid_from']}～{_last_day(f['valid_to'])}"
    return f"{names.get(f['entity_id'], f['entity_id'])} {span} {what}"


_FACT = "SELECT f.*, s.adapter, s.url FROM fact f JOIN source_doc s ON s.id = f.source_doc"
# the fact that displaced f (C1): written in the same transaction, same source and slot, overlapping validity
_DISPLACER = """SELECT n.record_key FROM fact n JOIN source_doc ns ON ns.id = n.source_doc
                 WHERE n.recorded_from = :at AND n.lineage_id <> :lineage AND ns.url = :url AND n.entity_id = :entity
                   AND n.attribute = :attribute AND n.qualifier = :qualifier AND n.specificity = :specificity
                   AND n.valid_from < :valid_to AND :valid_from < n.valid_to LIMIT 1"""


def _undone(conn: sqlite3.Connection, kind: str, key: str, ref: str) -> bool:
    return conn.execute("SELECT 1 FROM suppressed WHERE kind=? AND record_key=? AND ref=? AND note='undo'",
                        (kind, key, ref)).fetchone() is not None


def _fact_changes(conn: sqlite3.Connection, since: str, names: dict[str, str]) -> list[Change]:
    out = []
    for f in conn.execute(f"{_FACT} WHERE f.recorded_from >= ?", (since,)):
        out.append(Change(f["recorded_from"], "fact", "update" if f["supersedes"] else "new", f["adapter"], f["record_key"],
                          f["id"], _fact_text(conn, f, names)))
    closed = conn.execute(f"{_FACT} WHERE f.recorded_to >= ? AND NOT EXISTS (SELECT 1 FROM fact n WHERE n.supersedes = f.id)",
                          (since,))
    for f in closed:  # closed without a successor of its own lineage
        text = _fact_text(conn, f, names)
        by = conn.execute(_DISPLACER, {"at": f["recorded_to"], "lineage": f["lineage_id"], "url": f["url"],
                                       "entity": f["entity_id"], "attribute": f["attribute"], "qualifier": f["qualifier"],
                                       "specificity": f["specificity"], "valid_from": f["valid_from"],
                                       "valid_to": f["valid_to"]}).fetchone()
        if by is not None:
            op, text = "replaced", f"{text}（由 {by[0]} 取代）"
        else:
            op = "undone" if _undone(conn, "fact", f["record_key"], f["id"]) else "retired"
        out.append(Change(f["recorded_to"], "fact", op, f["adapter"], f["record_key"], f["id"], text))
    return out


def _event_changes(conn: sqlite3.Connection, since: str) -> list[Change]:
    out = []
    rows = conn.execute("""SELECT e.*, s.adapter FROM calendar_event e JOIN source_doc s ON s.id = e.source_doc
                            WHERE e.last_seen >= ?""", (since,))
    for e in rows:
        if e["status"] == "withdrawn":
            op = "undone" if _undone(conn, "event", e["uid"], e["uid"]) else "retired"
        else:
            op = "new" if e["first_seen"] == e["last_seen"] else "update"
        last = _last_day(e["during_to"])
        span = e["during_from"] if last == e["during_from"] else f"{e['during_from']}～{last}"
        status = "" if e["status"] in ("confirmed", "withdrawn") else f"（{e['status']}）"
        out.append(Change(e["last_seen"], "event", op, e["adapter"], e["uid"], e["uid"], f"{span} {e['summary']}{status}"))
    return out


def _doc_changes(conn: sqlite3.Connection, since: str) -> list[Change]:
    out = []
    for d in conn.execute("SELECT * FROM doc WHERE first_seen >= ? OR withdrawn_at >= ?", (since, since)):
        key, text = f"{d['adapter']}:{d['doc_key']}", f"{d['published_on'] or ''} {d['title']}".strip()
        if d["first_seen"] >= since:
            out.append(Change(d["first_seen"], "doc", "new", d["adapter"], key, d["doc_key"], text))
        if d["withdrawn_at"] and d["withdrawn_at"] >= since:
            op = "undone" if _undone(conn, "doc", key, d["doc_key"]) else "retired"
            out.append(Change(d["withdrawn_at"], "doc", op, d["adapter"], key, d["doc_key"], text))
    return out


def list_changes(conn: sqlite3.Connection, since: str) -> list[Change]:
    """Every change at or after `since` (UTC timestamp), oldest first."""
    names = names_of(conn)
    changes = _fact_changes(conn, since, names) + _event_changes(conn, since) + _doc_changes(conn, since)
    return sorted(changes, key=lambda c: (c.at, c.kind, c.key, c.op))


def format_change(c: Change) -> str:
    at = datetime.strptime(c.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone(TAIPEI)
    handle = f"id {c.ref}  key {c.key}" if c.kind == "fact" and c.op in ("new", "update") else f"key {c.key}"
    return f"{at:%m-%d %H:%M} {OPS[c.op]} [{c.adapter}] {c.text}\n            {handle}"
