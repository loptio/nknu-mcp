"""All writes to facts.db. Callers own the transaction (commit / rollback)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta

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


def displaced_by(conn: sqlite3.Connection, rec: FactRecord) -> list[sqlite3.Row]:
    """Identity rule (C1): the current facts of other lineages that rec restates — same source URL, entity, attribute,
    qualifier and specificity, with overlapping validity. Writing rec closes them (e.g. a closure whose first day moved,
    a calendar holiday re-created under a new UID, a corrected manual entry)."""
    if rec.valid_from is None or rec.valid_to is None:
        return []
    return conn.execute(
        """SELECT f.* FROM fact f JOIN source_doc s ON s.id = f.source_doc
            WHERE f.recorded_to IS NULL AND f.lineage_id <> ? AND s.url = ? AND f.entity_id = ? AND f.attribute = ?
              AND f.qualifier = ? AND f.specificity = ? AND f.valid_from < ? AND ? < f.valid_to
            ORDER BY f.valid_from, f.record_key""",
        (lineage_id(rec.key), rec.source.url, rec.entity_id, rec.attribute, rec.qualifier, rec.specificity,
         rec.valid_to.isoformat(), rec.valid_from.isoformat())).fetchall()


def apply_fact(conn: sqlite3.Connection, rec: FactRecord, *, adapter: str, review: str, now: str) -> str:
    """Write a new current version of rec's lineage, closing the previous version and every fact rec displaces
    (displaced_by) in the same transaction. Returns the new fact id."""
    if rec.valid_from is None or rec.valid_to is None:
        raise ValueError(f"{rec.key}: validity must be filled before writing")
    sid = ensure_source(conn, rec.source, adapter=adapter, fetched_at=now)
    t = tier(conn, publisher=rec.source.publisher, kind=rec.source.kind, entity_id=rec.entity_id, attribute=rec.attribute)
    for old in displaced_by(conn, rec):
        conn.execute("UPDATE fact SET recorded_to=? WHERE id=?", (now, old["id"]))
        # not 'unchanged' any more: if the source states the old content again, it is written again
        conn.execute("UPDATE record_state SET status='retired' WHERE record_key=? AND kind='fact'", (old["record_key"],))
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


def retire_event(conn: sqlite3.Connection, uid: str, *, now: str) -> bool:
    """Withdraw an event; False when it was already withdrawn (or unknown)."""
    return conn.execute("UPDATE calendar_event SET status='withdrawn', last_seen=? WHERE uid=? AND status <> 'withdrawn'",
                        (now, uid)).rowcount > 0


def upsert_doc(conn: sqlite3.Connection, rec: DocRecord, *, adapter: str, now: str) -> None:
    """A list row never blanks a stored unit, category or date (a cell that moved reads as None: keep the old value);
    a detail page (partial) updates only the excerpt and attachments. Honor-roll rows (pii_level 'personal') keep no
    body text: a row that turns personal drops what was stored, and a detail page never adds any (design §3)."""
    personal = rec.pii_level == "personal"
    attachments = "[]" if personal else json.dumps(list(rec.attachments), ensure_ascii=False)
    if rec.partial:
        conn.execute(
            """UPDATE doc SET excerpt=coalesce(?, excerpt),
                              attachments_json=CASE WHEN ?='[]' THEN attachments_json ELSE ? END, last_seen=?
               WHERE adapter=? AND doc_key=? AND pii_level <> 'personal'""",
            (rec.excerpt, attachments, attachments, now, adapter, rec.doc_key),
        )
        return
    conn.execute(
        """INSERT INTO doc(adapter, doc_key, title, unit, category, published_on, announce_until, url, excerpt, attachments_json,
                           pii_level, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(adapter, doc_key) DO UPDATE SET title=excluded.title, unit=coalesce(excluded.unit, doc.unit),
             category=coalesce(excluded.category, doc.category),
             published_on=coalesce(excluded.published_on, doc.published_on),
             announce_until=coalesce(excluded.announce_until, doc.announce_until), url=excluded.url,
             excerpt=CASE WHEN excluded.pii_level='personal' THEN NULL ELSE coalesce(excluded.excerpt, doc.excerpt) END,
             attachments_json=CASE WHEN excluded.pii_level='personal' THEN '[]'
                                   WHEN excluded.attachments_json='[]' THEN doc.attachments_json
                                   ELSE excluded.attachments_json END,
             pii_level=excluded.pii_level, last_seen=excluded.last_seen, withdrawn_at=NULL""",
        (adapter, rec.doc_key, rec.title, rec.unit, rec.category, rec.published_on.isoformat() if rec.published_on else None,
         rec.announce_until.isoformat() if rec.announce_until else None, rec.url, None if personal else rec.excerpt,
         attachments, rec.pii_level, now,
         now),
    )


def withdraw_doc(conn: sqlite3.Connection, adapter: str, doc_key: str, *, now: str) -> bool:
    """Withdraw an announcement; False when it was already withdrawn (or unknown)."""
    return conn.execute("UPDATE doc SET withdrawn_at=? WHERE adapter=? AND doc_key=? AND withdrawn_at IS NULL",
                        (now, adapter, doc_key)).rowcount > 0


def set_state(conn: sqlite3.Connection, adapter: str, rec_key: str, kind: str, content_hash: str, now: str) -> None:
    conn.execute(
        """INSERT INTO record_state(adapter, record_key, kind, content_hash, last_seen_at) VALUES (?,?,?,?,?)
           ON CONFLICT(adapter, record_key) DO UPDATE SET content_hash=excluded.content_hash, last_seen_at=excluded.last_seen_at,
             missing_full_scans=0, status='active'""",
        (adapter, rec_key, kind, content_hash, now),
    )


def suppress(conn: sqlite3.Connection, *, adapter: str, record_key: str, content_hash: str, kind: str, ref: str | None,
             note: str, now: str) -> None:
    """Remember content that the maintainer took down: the crawler never writes this exact content again."""
    conn.execute("""INSERT INTO suppressed(adapter, record_key, content_hash, kind, ref, note, created_at) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING""", (adapter, record_key, content_hash, kind, ref, note, now))


def is_suppressed(conn: sqlite3.Connection, adapter: str, record_key: str, content_hash: str) -> bool:
    return conn.execute("SELECT 1 FROM suppressed WHERE adapter=? AND record_key=? AND content_hash=?",
                        (adapter, record_key, content_hash)).fetchone() is not None


def add_pending(conn: sqlite3.Connection, *, adapter: str, record_key: str, op: str, record: Record | None, content_hash: str,
                diff: str, reason: str, now: str) -> int | None:
    """Queue a change for `nknu review`. Same content already pending or rejected -> None (never re-ask).
    Since U2 the crawler writes directly and queues nothing; the queue only holds items left by an older version."""
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


def supersede_pending(conn: sqlite3.Connection, adapter: str, record_key: str, *, now: str) -> int:
    """The crawler wrote (or retired) record_key itself: an item still waiting in review for that key is older content,
    and approving it later would put that back."""
    return conn.execute("UPDATE pending_change SET status='superseded', decided_at=? WHERE adapter=? AND record_key=? "
                        "AND status='pending'", (now, adapter, record_key)).rowcount


REMIND_AFTER = timedelta(days=1)


def add_alert(conn: sqlite3.Connection, *, adapter: str | None, level: str, code: str, message: str,
              dedup_key: str | None = None, now: str, webhook: str | None = None, remind_daily: bool = False) -> int | None:
    """Raise an alert; None when the same alert (dedup_key) is still open. With remind_daily (a condition that keeps an
    adapter from updating), an open alert last raised a day or more ago is raised again: its created_at becomes now and
    its message the latest one, so the crawl summary shows it once more (C6: at most once a day)."""
    if dedup_key:
        open_ = conn.execute("SELECT id, created_at FROM alert WHERE dedup_key=? AND resolved_at IS NULL",
                             (dedup_key,)).fetchone()
        if open_ is not None:
            if not remind_daily or _ts(now) - _ts(open_[1]) < REMIND_AFTER:
                return None
            conn.execute("UPDATE alert SET created_at=?, level=?, message=? WHERE id=?", (now, level, message, open_[0]))
            _announce(adapter, level, code, message, webhook)
            return open_[0]
    aid = conn.execute(
        "INSERT INTO alert(created_at, adapter, level, code, message, dedup_key) VALUES (?,?,?,?,?,?)",
        (now, adapter, level, code, message, dedup_key),
    ).lastrowid
    _announce(adapter, level, code, message, webhook)
    return aid


def _ts(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")


def _announce(adapter: str | None, level: str, code: str, message: str, webhook: str | None) -> None:
    if level in ("warn", "error"):
        notify(f"[{level}] {adapter or '-'} {code}: {message}", webhook)
    else:
        log.info("alert %s %s: %s", adapter, code, message)
