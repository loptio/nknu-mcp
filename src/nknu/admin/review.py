"""`nknu review`: high-impact changes wait here until the maintainer approves them on the lab machine."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from nknu.build.cards import contact_of, hours_sentence, names_of
from nknu.build.templates import Templates
from nknu.core.factsdb import bump_data_version
from nknu.core.records import EventRecord, FactRecord, Record, from_json
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.writer import apply_fact, retire_event, retire_fact, set_state, upsert_event

PREVIEW_DAYS = 14


@dataclass(frozen=True)
class PendingItem:
    id: int
    adapter: str
    record_key: str
    op: str
    record: Record | None
    content_hash: str
    diff: str
    reason: str
    created_at: str


def list_pending(conn: sqlite3.Connection) -> list[PendingItem]:
    rows = conn.execute("SELECT * FROM pending_change WHERE status='pending' ORDER BY id").fetchall()
    return [PendingItem(r["id"], r["adapter"], r["record_key"], r["op"],
                        from_json(r["record_json"]) if r["record_json"] != "{}" else None, r["content_hash"], r["diff"],
                        r["reason"], r["created_at"]) for r in rows]


def _affected(conn: sqlite3.Connection, rec: FactRecord) -> list[str]:
    if not rec.propagates:
        return [rec.entity_id]
    rows = conn.execute(
        """WITH RECURSIVE down(id) AS (SELECT ? UNION SELECT r.subject FROM entity_relation r JOIN down d ON r.object=d.id
                                      WHERE r.predicate='containedInPlace') SELECT id FROM down""", (rec.entity_id,)).fetchall()
    return [r[0] for r in rows]


def _sentences(conn, entity, tpl, today, names, contact) -> list[str]:
    be = SQLiteBackend(conn)
    out = []
    for i in range(PREVIEW_DAYS):
        d = today + timedelta(days=i)
        out.append(hours_sentence(entity, resolve(be, entity, "hours", d), names, contact, tpl, d, d + timedelta(days=1)))
    return out


def _apply(conn: sqlite3.Connection, item: PendingItem, now: str) -> None:
    rec = item.record
    if item.op == "upsert" and isinstance(rec, FactRecord):
        apply_fact(conn, rec, adapter=item.adapter, review="verified", now=now)
        set_state(conn, item.adapter, rec.key, "fact", item.content_hash, now)
    elif item.op == "upsert" and isinstance(rec, EventRecord):
        upsert_event(conn, rec, adapter=item.adapter, now=now)
        set_state(conn, item.adapter, rec.key, "event", item.content_hash, now)
    elif item.op == "retire":
        kind = conn.execute("SELECT kind FROM record_state WHERE adapter=? AND record_key=?",
                            (item.adapter, item.record_key)).fetchone()
        if kind and kind[0] == "event":
            retire_event(conn, item.record_key, now=now)
        else:
            retire_fact(conn, item.record_key, now=now)
        conn.execute("UPDATE record_state SET status='retired' WHERE adapter=? AND record_key=?", (item.adapter, item.record_key))
    else:
        raise ValueError(f"pending #{item.id}: cannot apply {item.op} {type(rec).__name__}")


def render_item(conn: sqlite3.Connection, item: PendingItem, today: date, tpl: Templates) -> str:
    rec = item.record
    lines = [f"#{item.id} [{item.adapter}] {item.op} ({item.reason}) {item.record_key}", item.diff]
    if isinstance(rec, (FactRecord, EventRecord)):
        lines.append(f"來源：{rec.source.title or ''} {rec.source.url}")
    if isinstance(rec, FactRecord):
        lines.append(f"引文：{rec.quote}")
    if isinstance(rec, FactRecord) or (item.op == "retire" and rec is None):
        names = names_of(conn)
        entities = _affected(conn, rec) if isinstance(rec, FactRecord) else []
        conn.execute("SAVEPOINT preview")
        try:
            before = {e: _sentences(conn, e, tpl, today, names, contact_of(conn, e, names)) for e in entities}
            _apply(conn, item, "9999-12-31T00:00:00Z")
            after = {e: _sentences(conn, e, tpl, today, names, contact_of(conn, e, names)) for e in entities}
        finally:
            conn.execute("ROLLBACK TO preview")
            conn.execute("RELEASE preview")
        for e in entities:
            changed = [(today + timedelta(days=i), b, a) for i, (b, a) in enumerate(zip(before[e], after[e], strict=True)) if b != a]
            lines.append(f"  {names.get(e, e)}：未來 {PREVIEW_DAYS} 天有 {len(changed)} 天的答案會改變")
            shown = set()
            for d, b, a in changed:
                if (b, a) in shown:
                    continue
                shown.add((b, a))
                lines += [f"    {d} 現在：{b}", f"    {d} 核准後：{a}"]
    return "\n".join(lines)


def decide(conn: sqlite3.Connection, item_id: int, decision: str, now: str) -> None:
    item = next((i for i in list_pending(conn) if i.id == item_id), None)
    if item is None:
        raise ValueError(f"no pending change #{item_id}")
    if decision == "approve":
        _apply(conn, item, now)
        bump_data_version(conn)
        status = "approved"
    elif decision == "reject":
        status = "rejected"
    else:
        raise ValueError(decision)
    conn.execute("UPDATE pending_change SET status=?, decided_at=? WHERE id=?", (status, now, item_id))
    conn.commit()
