"""All writes to facts.db. Callers own the transaction (commit / rollback)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

from nknu.core.ids import lineage_id, uuid7
from nknu.core.notify import notify
from nknu.core.records import DocRecord, EventRecord, FactRecord, Record, SourceRef, to_json
from nknu.core.vocab import tier

log = logging.getLogger("nknu")


def _evidence_group(key: str | None) -> int | None:
    # a stable integer per evidence key; far above any source_doc id, so it never collides with coalesce(group, id)
    return None if key is None else int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) + (1 << 50)


def ensure_source(conn: sqlite3.Connection, src: SourceRef, *, adapter: str, fetched_at: str) -> int:
    row = conn.execute("SELECT id FROM source_doc WHERE url=? AND warc_sha256=?", (src.url, src.sha256)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        """INSERT INTO source_doc(url, title, publisher, kind, adapter, evidence_group, published_on, fetched_at, warc_sha256)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (src.url, src.title, src.publisher, src.kind, adapter, _evidence_group(src.evidence_key),
         src.published_on.isoformat() if src.published_on else None, fetched_at, src.sha256),
    ).lastrowid


def current_fact(conn: sqlite3.Connection, record_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM fact WHERE lineage_id=? AND recorded_to IS NULL", (lineage_id(record_key),)).fetchone()


def apply_fact(conn: sqlite3.Connection, rec: FactRecord, *, adapter: str, review: str, now: str) -> str:
    """Write a new current version of rec's lineage (closing the previous one). Returns the new fact id."""
    if rec.valid_from is None or rec.valid_to is None:
        raise ValueError(f"{rec.key}: validity must be filled before writing")
    sid = ensure_source(conn, rec.source, adapter=adapter, fetched_at=now)
    t = tier(conn, publisher=rec.source.publisher, kind=rec.source.kind, entity_id=rec.entity_id, attribute=rec.attribute)
    cur = current_fact(conn, rec.key)
    if cur is not None:
        conn.execute("UPDATE fact SET recorded_to=? WHERE id=?", (now, cur["id"]))
    fid = uuid7()
    conn.execute(
        """INSERT INTO fact(id, lineage_id, record_key, entity_id, attribute, qualifier, value_json, valid_from, valid_to,
                            valid_basis, specificity, propagates, source_doc, tier, quote, extraction, review, recorded_from,
                            supersedes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fid, lineage_id(rec.key), rec.key, rec.entity_id, rec.attribute, rec.qualifier,
         json.dumps(rec.value, ensure_ascii=False, sort_keys=True), rec.valid_from.isoformat(), rec.valid_to.isoformat(),
         rec.valid_basis, rec.specificity, int(rec.propagates), sid, t, rec.quote[:200], rec.extraction, review, now,
         cur["id"] if cur is not None else None),
    )
    conn.executemany(
        "INSERT INTO hours_rule(fact_id, dows, opens, closes) VALUES (?,?,?,?)",
        [(fid, json.dumps(sorted(r.dows)), r.opens, r.closes) for r in rec.rules],
    )
    return fid


def retire_fact(conn: sqlite3.Connection, record_key: str, *, now: str) -> str | None:
    cur = current_fact(conn, record_key)
    if cur is None:
        return None
    conn.execute("UPDATE fact SET recorded_to=? WHERE id=?", (now, cur["id"]))
    return cur["id"]


def upsert_event(conn: sqlite3.Connection, rec: EventRecord, *, adapter: str, now: str) -> None:
    sid = ensure_source(conn, rec.source, adapter=adapter, fetched_at=now)
    conn.execute(
        """INSERT INTO calendar_event(uid, summary, during_from, during_to, category, status, sequence, source_doc,
                                      last_modified, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET summary=excluded.summary, during_from=excluded.during_from,
             during_to=excluded.during_to, category=excluded.category, status=excluded.status, sequence=excluded.sequence,
             source_doc=excluded.source_doc, last_modified=excluded.last_modified, last_seen=excluded.last_seen""",
        (rec.key, rec.summary, rec.during_from.isoformat(), rec.during_to.isoformat(), rec.category, rec.status, rec.sequence,
         sid, rec.last_modified, now, now),
    )


def retire_event(conn: sqlite3.Connection, uid: str, *, now: str) -> None:
    conn.execute("UPDATE calendar_event SET status='withdrawn', last_seen=? WHERE uid=?", (now, uid))


def upsert_doc(conn: sqlite3.Connection, rec: DocRecord, *, adapter: str, now: str) -> None:
    attachments = json.dumps(list(rec.attachments), ensure_ascii=False)
    if rec.partial:
        conn.execute(
            """UPDATE doc SET excerpt=coalesce(?, excerpt),
                              attachments_json=CASE WHEN ?='[]' THEN attachments_json ELSE ? END, last_seen=?
               WHERE adapter=? AND doc_key=?""",
            (rec.excerpt, attachments, attachments, now, adapter, rec.doc_key),
        )
        return
    conn.execute(
        """INSERT INTO doc(adapter, doc_key, title, unit, category, published_on, announce_until, url, excerpt, attachments_json,
                           pii_level, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(adapter, doc_key) DO UPDATE SET title=excluded.title, unit=excluded.unit, category=excluded.category,
             published_on=excluded.published_on, announce_until=excluded.announce_until, url=excluded.url,
             excerpt=coalesce(excluded.excerpt, doc.excerpt),
             attachments_json=CASE WHEN excluded.attachments_json='[]' THEN doc.attachments_json ELSE excluded.attachments_json END,
             pii_level=excluded.pii_level, last_seen=excluded.last_seen, withdrawn_at=NULL""",
        (adapter, rec.doc_key, rec.title, rec.unit, rec.category, rec.published_on.isoformat() if rec.published_on else None,
         rec.announce_until.isoformat() if rec.announce_until else None, rec.url, rec.excerpt, attachments, rec.pii_level, now,
         now),
    )


def withdraw_doc(conn: sqlite3.Connection, adapter: str, doc_key: str, *, now: str) -> None:
    conn.execute("UPDATE doc SET withdrawn_at=? WHERE adapter=? AND doc_key=? AND withdrawn_at IS NULL", (now, adapter, doc_key))


def set_state(conn: sqlite3.Connection, adapter: str, rec_key: str, kind: str, content_hash: str, now: str) -> None:
    conn.execute(
        """INSERT INTO record_state(adapter, record_key, kind, content_hash, last_seen_at) VALUES (?,?,?,?,?)
           ON CONFLICT(adapter, record_key) DO UPDATE SET content_hash=excluded.content_hash, last_seen_at=excluded.last_seen_at,
             missing_full_scans=0, status='active'""",
        (adapter, rec_key, kind, content_hash, now),
    )


def add_pending(conn: sqlite3.Connection, *, adapter: str, record_key: str, op: str, record: Record | None, content_hash: str,
                diff: str, reason: str, now: str) -> int | None:
    """Queue a change for `nknu review`. Same content already pending or rejected -> None (never re-ask)."""
    dup = conn.execute(
        "SELECT 1 FROM pending_change WHERE adapter=? AND record_key=? AND content_hash=? AND status IN ('pending','rejected')",
        (adapter, record_key, content_hash)).fetchone()
    if dup:
        return None
    conn.execute("UPDATE pending_change SET status='superseded', decided_at=? WHERE adapter=? AND record_key=? AND status='pending'",
                 (now, adapter, record_key))
    return conn.execute(
        """INSERT INTO pending_change(adapter, record_key, op, record_json, content_hash, diff, reason, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (adapter, record_key, op, to_json(record) if record is not None else "{}", content_hash, diff, reason, now),
    ).lastrowid


def add_alert(conn: sqlite3.Connection, *, adapter: str | None, level: str, code: str, message: str,
              dedup_key: str | None = None, now: str, webhook: str | None = None) -> int | None:
    if dedup_key and conn.execute("SELECT 1 FROM alert WHERE dedup_key=? AND resolved_at IS NULL", (dedup_key,)).fetchone():
        return None
    aid = conn.execute(
        "INSERT INTO alert(created_at, adapter, level, code, message, dedup_key) VALUES (?,?,?,?,?,?)",
        (now, adapter, level, code, message, dedup_key),
    ).lastrowid
    if level in ("warn", "error"):
        notify(f"[{level}] {adapter or '-'} {code}: {message}", webhook)
    else:
        log.info("alert %s %s: %s", adapter, code, message)
    return aid
