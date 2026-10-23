"""`nknu undo`: take down the current version of a fact, calendar event or announcement, and remember its content so the
crawler never writes the identical content back (C3). Changed content is written again as usual; `--cancel` forgets."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from nknu.core.factsdb import bump_data_version
from nknu.core.writer import current_fact, retire_event, retire_fact, suppress, withdraw_doc


class UndoError(ValueError):
    pass


@dataclass(frozen=True)
class Undone:
    kind: str  # fact | event | doc
    key: str  # record key (an event's uid)
    ref: str  # the fact version, event uid or doc key taken down
    remembered: bool  # its content is now suppressed (False: nothing produced it automatically, e.g. a test fixture)


def _fact_key(conn: sqlite3.Connection, target: str) -> str | None:
    row = conn.execute("SELECT record_key, recorded_to, lineage_id FROM fact WHERE id=?", (target,)).fetchone()
    if row is not None:
        if row["recorded_to"] is None:
            return row["record_key"]
        cur = conn.execute("SELECT id FROM fact WHERE lineage_id=? AND recorded_to IS NULL", (row["lineage_id"],)).fetchone()
        raise UndoError(f"{target} 已经不是现行版本" + (f"（现行版本是 {cur['id']}）" if cur else "（这条资料目前没有现行版本）"))
    row = conn.execute("SELECT record_key FROM fact WHERE lineage_id=? AND recorded_to IS NULL", (target,)).fetchone()
    if row is not None:
        return row["record_key"]
    return target if current_fact(conn, target) is not None else None


def _target(conn: sqlite3.Connection, target: str) -> tuple[str, str]:
    """(kind, record key) of the current item that `target` names: a fact id, a lineage id or a record key."""
    key = _fact_key(conn, target)
    if key is not None:
        return "fact", key
    if conn.execute("SELECT 1 FROM calendar_event WHERE uid=? AND status <> 'withdrawn'", (target,)).fetchone():
        return "event", target
    st = conn.execute("SELECT adapter FROM record_state WHERE record_key=? AND kind='doc'", (target,)).fetchone()
    if st and conn.execute("SELECT 1 FROM doc WHERE adapter=? AND doc_key=? AND withdrawn_at IS NULL",
                           (st["adapter"], target.split(":", 1)[-1])).fetchone():
        return "doc", target
    raise UndoError(f"找不到现行的「{target}」：请用 nknu changes 列出的 id 或 key")


def undo(conn: sqlite3.Connection, target: str, *, now: str) -> Undone:
    kind, key = _target(conn, target)
    state = conn.execute("SELECT adapter, content_hash FROM record_state WHERE record_key=? AND kind=?", (key, kind)).fetchone()
    try:
        if kind == "fact":
            ref = retire_fact(conn, key, now=now)
        elif kind == "event":
            retire_event(conn, key, now=now)
            ref = key
        else:
            ref = key.split(":", 1)[-1]
            withdraw_doc(conn, state["adapter"], ref, now=now)
        if state is not None:
            suppress(conn, adapter=state["adapter"], record_key=key, content_hash=state["content_hash"], kind=kind, ref=ref,
                     note="undo", now=now)
            conn.execute("UPDATE record_state SET status='retired' WHERE adapter=? AND record_key=?", (state["adapter"], key))
            # the same content waiting in review would bring it back
            conn.execute("UPDATE pending_change SET status='superseded', decided_at=? WHERE adapter=? AND record_key=? "
                         "AND content_hash=? AND status='pending'", (now, state["adapter"], key, state["content_hash"]))
        bump_data_version(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return Undone(kind, key, ref, state is not None)


def cancel_undo(conn: sqlite3.Connection, target: str) -> int:
    """Forget the remembered content of `target` (record key, or the id `nknu undo` took down). Returns rows removed."""
    n = conn.execute("DELETE FROM suppressed WHERE record_key=? OR ref=?", (target, target)).rowcount
    conn.commit()
    return n
