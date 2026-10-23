"""One crawl run per adapter: fetch -> parse -> layout guard -> diff -> write.

Everything that passes the layout guard is written directly (U2): venue hours, closures, holidays, calendar events
(rescheduled, tentative and cancelled ones too) and announcements. The maintainer checks afterwards instead of before:
`nknu changes`, `nknu undo` and the summary each crawl logs and sends (C4, admin.changes.crawl_summary). A void run
writes nothing except its crawl_run row and alerts, so the previous values keep serving.

Disappearance (C2): only full scans count absences, and only for records the run had the chance to produce
(Adapter.counts_missing); three in a row retire the record without review. Facts printed on a table page (closure days,
term tables) are never retired for leaving the page: they carry their own end date and expire on it. An announcement
whose page answers 404 is withdrawn at once, together with the closures it stated.

Guard (invariants.standard): any failing list page, missing selector, unreadable 200, date-rate drop, collapse or
out-of-range count voids the run. Exception (C6): a lower count, with every other check passing, that the two runs
before counted exactly the same is the new normal: written, and the count baseline restarts there. Void alerts are
raised again once a day while the adapter stays void. A detail page that fails costs only its own record
(alert detail_failed) and is asked for after the others next time.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from nknu.core.factsdb import bump_data_version, connect, migrate
from nknu.core.ids import lineage_id
from nknu.core.pii import redact
from nknu.core.records import DocRecord, EntityHint, EventRecord, FactRecord, ParseIssue, Record, content_hash
from nknu.core.timeutil import TAIPEI, iso_now
from nknu.core.vocab import fill_validity
from nknu.core.writer import (
    add_alert,
    apply_fact,
    is_suppressed,
    retire_event,
    retire_fact,
    set_state,
    supersede_pending,
    suppress,
    upsert_doc,
    upsert_event,
    withdraw_doc,
)
from nknu.crawl.adapter import Adapter, ParseError, RunContext, RunHistory, Violation
from nknu.crawl.fetch import FetchError
from nknu.crawl.invariants import record_count
from nknu.crawl.warc import WarcSink, gc_warc
from nknu.settings import Settings

log = logging.getLogger("nknu")
ORDER = ["aca_calendar_ics", "pe_open_hours", "lib_open_time", "pe_news", "sso_news"]  # calendar first: term dates
RETIRE_AFTER_FULL_SCANS = 3
NEW_NORMAL_RUNS = 3  # C6: the same lower count this many runs in a row is accepted


@dataclass
class RunResult:
    adapter: str
    passed: bool
    n_records: int = 0
    n_written: int = 0  # facts, events and announcements written
    n_retired: int = 0  # taken down: missing from 3 full scans, or their page answered 404
    violations: list[Violation] = field(default_factory=list)
    error: str | None = None


def run_order(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER), n))


def _baseline_key(name: str, full_scan: bool) -> str:
    return f"count_baseline:{name}:{int(full_scan)}"


def _history(conn: sqlite3.Connection, name: str, full_scan: bool, now: str) -> RunHistory:
    """Record counts of the passed runs of the last 7 days (same scan mode), from the last accepted new normal on."""
    since = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = conn.execute("SELECT value FROM meta WHERE key=?", (_baseline_key(name, full_scan),)).fetchone()
    since = max(since, base[0]) if base else since
    rows = conn.execute("SELECT n_records FROM crawl_run WHERE adapter=? AND passed=1 AND full_scan=? AND started_at>=? "
                        "ORDER BY id", (name, int(full_scan), since)).fetchall()
    return RunHistory([r[0] for r in rows], rows[-1][0] if rows else None)


def _new_normal(conn, name: str, run_id: int, full_scan: bool, n: int, history: RunHistory,
                violations: list[Violation]) -> bool:
    """C6: the count is the only check that failed, it is below the 7-day range, and the two runs before this one (same
    scan mode) counted exactly the same: a source that really shrank, accepted instead of voided for a week."""
    if not violations or any(v.code != "count_out_of_band" for v in violations) or not history.counts_7d:
        return False
    if not 0 < n < statistics.median(history.counts_7d):
        return False
    before = conn.execute("SELECT n_records FROM crawl_run WHERE adapter=? AND full_scan=? AND id<? AND finished_at IS NOT "
                          "NULL ORDER BY id DESC LIMIT ?", (name, int(full_scan), run_id, NEW_NORMAL_RUNS - 1)).fetchall()
    return len(before) == NEW_NORMAL_RUNS - 1 and all(r[0] == n for r in before)


def _accept_new_normal(conn, name: str, full_scan: bool, n: int, history: RunHistory, now: str) -> None:
    med = statistics.median(history.counts_7d)
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (_baseline_key(name, full_scan), now))
    conn.execute("UPDATE alert SET resolved_at=? WHERE dedup_key=? AND resolved_at IS NULL",
                 (now, f"{name}:count_out_of_band"))
    add_alert(conn, adapter=name, level="info", code="count_new_normal",
              message=f"{'全扫' if full_scan else '部分扫描'}条数 {n} 连续 {NEW_NORMAL_RUNS} 轮相同（之前 7 天中位数 {med:g}），"
                      f"当成新常态接受并写入；之后以 {n} 为基准", now=now)


def _merge(records: list[Record]) -> list[Record]:
    """Detail-page partial DocRecords are folded into the list-page record with the same key; a detail page that answered
    404 marks its list-page record gone (the list fields stay, so its content hash is the list row's)."""
    full: dict[str, Record] = {}
    notes: list[Record] = []  # issues and hints are kept as they are: one keyed on an announcement must not replace it
    partials: list[DocRecord] = []
    gone: set[str] = set()
    for r in records:
        if isinstance(r, (ParseIssue, EntityHint)):
            notes.append(r)
        elif isinstance(r, DocRecord) and r.partial:
            partials.append(r)
        elif isinstance(r, DocRecord) and r.gone:
            gone.add(r.key)
            full.setdefault(r.key, r)
        else:
            full[r.key] = r
    for key in gone:
        if isinstance(full[key], DocRecord):
            full[key] = replace(full[key], gone=True)
    for p in partials:
        base = full.get(p.key)
        if isinstance(base, DocRecord):  # "" is an excerpt too: the page was read and had no text
            full[p.key] = replace(base, excerpt=p.excerpt if p.excerpt is not None else base.excerpt,
                                  attachments=p.attachments or base.attachments)
        else:
            full[p.key] = p  # existing doc row gets updated
    return list(full.values()) + notes


def _same_source_overlaps(facts: list[FactRecord]) -> dict[int, FactRecord]:
    """Records of one run that state a slot another record of the same run already states (same source URL, entity,
    attribute, qualifier, specificity, overlapping validity): id(record) -> the record kept instead. The earliest-starting
    record is kept, so the choice is the same on every run and the two never replace each other back and forth."""
    groups: dict[tuple, list[FactRecord]] = {}
    for f in facts:
        groups.setdefault((f.source.url, f.entity_id, f.attribute, f.qualifier, f.specificity), []).append(f)
    out: dict[int, FactRecord] = {}
    for group in groups.values():
        kept: list[FactRecord] = []
        for f in sorted(group, key=lambda f: (f.valid_from, f.valid_to, f.key)):
            hit = next((k for k in kept if k.valid_from < f.valid_to and f.valid_from < k.valid_to), None)
            if hit is None:
                kept.append(f)
            else:
                out[id(f)] = hit
    return out


def _state(conn: sqlite3.Connection, name: str, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM record_state WHERE adapter=? AND record_key=?", (name, key)).fetchone()


def _void(conn, name, run_id, violations, error, readonly, n_records: int = 0) -> RunResult:
    """A void run writes only its crawl_run row (n_records only when the count alone failed, for C6) and its alerts,
    raised again once a day while the adapter stays void (C6)."""
    now = iso_now()
    if not readonly:
        for v in violations:
            add_alert(conn, adapter=name, level="error" if v.code in ("fetch_failed", "parse_failed") else "warn", code=v.code,
                      message=redact(v.message), dedup_key=f"{name}:{v.code}", now=now, remind_daily=True)
        conn.execute("UPDATE crawl_run SET finished_at=?, passed=0, error=?, n_records=? WHERE id=?",
                     (now, redact("; ".join(f"{v.code}: {v.message}" for v in violations))[:500], n_records, run_id))
        conn.commit()
    return RunResult(name, False, violations=violations, error=error)


def run_adapter(conn: sqlite3.Connection, adapter: Adapter, client, *, today: date, full_scan: bool,
                readonly: bool) -> RunResult:
    name, now = adapter.name, iso_now()
    run_id = 0
    if not readonly:
        run_id = conn.execute("INSERT INTO crawl_run(adapter, started_at, full_scan) VALUES (?,?,?)",
                              (name, now, int(full_scan))).lastrowid
        conn.commit()
    known = {r[0] for r in conn.execute("SELECT record_key FROM record_state WHERE adapter=? AND status='active'", (name,))}
    # announcements the maintainer took down are never fetched again; those whose page answered 404 only on full scans,
    # so a page that answers again lifts the 'gone' memory (F-404-churn); both count as known
    marks = conn.execute("SELECT record_key, note FROM suppressed WHERE adapter=? AND kind='doc'", (name,)).fetchall()
    down = {k for k, note in marks if note != "gone"}
    gone = {k for k, note in marks if note == "gone"} - down
    # announcements whose detail page is stored ("" = read, no text); the others are the detail backlog (F-excerpt-backlog)
    detailed = {f"{name}:{r[0]}" for r in conn.execute("SELECT doc_key FROM doc WHERE adapter=? AND excerpt IS NOT NULL",
                                                         (name,))}
    # detail pages that failed and are still failing (open alert): asked for after the rest (F-detail-void-freeze)
    failed = {r[0][len(_detail_alert(name, "")):] for r in conn.execute(
        "SELECT dedup_key FROM alert WHERE adapter=? AND code='detail_failed' AND resolved_at IS NULL", (name,))}
    ctx = RunContext(client, full_scan, today, conn, known | down | gone, adapter.rate_limit, detailed, down, failed, gone)
    try:
        snaps = adapter.fetch_all(ctx)
    except FetchError as exc:
        return _void(conn, name, run_id, [Violation("fetch_failed", str(exc))], str(exc), readonly)
    except ParseError as exc:  # a list page the adapter could not follow (a row without its id, a pager that never ends)
        return _void(conn, name, run_id, [Violation("parse_failed", str(exc))], str(exc), readonly)
    try:
        parsed = [r for s in snaps for r in adapter.parse(s)]
        records = _merge(parsed)
    except Exception as exc:  # noqa: BLE001 - any parser error means the layout changed
        msg = f"{type(exc).__name__}: {exc}"
        return _void(conn, name, run_id, [Violation("parse_failed", msg)], msg, readonly)
    history = _history(conn, name, full_scan, now)
    violations = adapter.invariants(records, history, snaps)
    n = record_count(records)
    if not readonly and _new_normal(conn, name, run_id, full_scan, n, history, violations):
        _accept_new_normal(conn, name, full_scan, n, history, now)
        violations = []
    if violations:
        band_only = all(v.code == "count_out_of_band" for v in violations)
        return _void(conn, name, run_id, violations, violations[0].message, readonly, n if band_only else 0)

    res = RunResult(name, True, n_records=n)
    if readonly:
        return res
    try:
        read = {r.key for r in parsed if isinstance(r, DocRecord) and r.partial}  # detail pages that answered
        seen = _apply_all(conn, adapter, records, res, today, now, known, read)
        if full_scan:
            _missing(conn, adapter, known - seen, parsed, res, today, now)
        if res.n_written or res.n_retired:
            bump_data_version(conn)
        conn.execute("UPDATE crawl_run SET finished_at=?, n_snapshots=?, n_records=?, passed=1, n_written=? WHERE id=?",
                     (iso_now(), len(snaps), res.n_records, res.n_written + res.n_retired, run_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return res


def _seen(conn, name: str, key: str, now: str, seen: set[str]) -> None:
    seen.add(key)
    conn.execute("UPDATE record_state SET missing_full_scans=0, last_seen_at=? WHERE adapter=? AND record_key=?",
                 (now, name, key))


def _apply_all(conn, adapter, records, res, today, now, known: set[str], read: set[str]) -> set[str]:
    name, seen = adapter.name, set()
    ready: list[Record] = []
    for rec in records:
        if isinstance(rec, EntityHint):
            add_alert(conn, adapter=name, level="warn", code="new_entity", message=f"未知地点「{rec.name}」：{redact(rec.context)} {rec.url}",
                      dedup_key=f"hint:{rec.name}", now=now)
        elif isinstance(rec, ParseIssue) and rec.code == "detail":  # one announcement's detail page was not read
            add_alert(conn, adapter=name, level="warn", code="detail_failed", message=redact(f"{rec.key}: {rec.message}"),
                      dedup_key=_detail_alert(name, rec.key), now=now)
        elif isinstance(rec, ParseIssue):
            add_alert(conn, adapter=name, level="warn", code="parse_issue", message=redact(rec.message),
                      dedup_key=f"issue:{name}:{rec.key}", now=now)
        elif isinstance(rec, FactRecord):
            # dates first: a term table's record key carries its term's first day (fill_validity)
            filled = fill_validity(conn, rec, today)
            if filled is None:
                add_alert(conn, adapter=name, level="warn", code="no_term_dates",
                          message=f"{rec.key}: 行事历里找不到目前学期，无法补上有效期，这条先不写入",
                          dedup_key=f"no_term:{rec.key}", now=now)
                for k in [k for k in known if k == rec.key or k.startswith(rec.key + ":")]:
                    _seen(conn, name, k, now, seen)  # still on the page: its term lineages are not missing
                continue
            ready.append(filled)
        else:
            ready.append(rec)
    overlaps = _same_source_overlaps([r for r in ready if isinstance(r, FactRecord)])
    for rec in ready:
        _seen(conn, name, rec.key, now, seen)
        if isinstance(rec, FactRecord):
            kept = overlaps.get(id(rec))
            if kept is not None:
                add_alert(conn, adapter=name, level="warn" if rec.attribute == "hours" else "info", code="overlap",
                          message=redact(f"{rec.key} [{rec.valid_from}, {rec.valid_to}) 与同一来源的 {kept.key} "
                                         f"[{kept.valid_from}, {kept.valid_to}) 有效期重叠，只写入后者；请到来源页面确认"),
                          dedup_key=f"overlap:{name}:{rec.key}", now=now)
                continue
            _apply_fact(conn, name, rec, res, now)
        elif isinstance(rec, EventRecord):
            _apply_event(conn, name, rec, res, now)
        elif isinstance(rec, DocRecord):
            _apply_doc(conn, name, rec, res, now, read)
    return seen


def _unchanged_or_undone(conn, name: str, key: str, h: str) -> bool:
    """Same content as last written (and still in force), or content the maintainer took down with `nknu undo`."""
    state = _state(conn, name, key)
    if state is not None and state["content_hash"] == h and state["status"] == "active":
        return True
    return is_suppressed(conn, name, key, h)


def _apply_fact(conn, name, rec, res, now) -> None:
    """U2: written directly; C1 closes the facts it restates (apply_fact)."""
    h = content_hash(rec)
    if _unchanged_or_undone(conn, name, rec.key, h):
        return
    apply_fact(conn, rec, adapter=name, review="auto_ok", now=now)
    set_state(conn, name, rec.key, "fact", h, now)
    supersede_pending(conn, name, rec.key, now=now)
    res.n_written += 1


def _apply_event(conn, name, rec, res, now) -> None:
    """U2: new, rescheduled, tentative and cancelled events are all written directly; serve.db leaves cancelled ones out
    and the answers label tentative ones."""
    h = content_hash(rec)
    if _unchanged_or_undone(conn, name, rec.key, h):
        return
    upsert_event(conn, rec, adapter=name, now=now)
    set_state(conn, name, rec.key, "event", h, now)
    supersede_pending(conn, name, rec.key, now=now)
    res.n_written += 1


def _apply_doc(conn, name, rec, res, now, read: set[str]) -> None:
    # list-level identity: excerpt/attachments come from detail pages and are updated only when those are fetched
    h = content_hash(replace(rec, excerpt=None, attachments=(), gone=False))
    if rec.gone:
        _gone(conn, name, rec, h, res, now)
        return
    if rec.key in read:  # its detail page answered: a page that once answered 404 is back, one that failed works again
        conn.execute("DELETE FROM suppressed WHERE adapter=? AND record_key=? AND note='gone'", (name, rec.key))
        conn.execute("UPDATE alert SET resolved_at=? WHERE dedup_key=? AND resolved_at IS NULL",
                     (now, _detail_alert(name, rec.key)))
    st = _state(conn, name, rec.key)
    if rec.partial or (st is not None and st["status"] == "active" and st["content_hash"] == h):
        # the list row is as stored: only what its detail page brought this run can be new (the detail backlog of
        # rows read long ago, an edited body); written, and counted, only when it differs from what is stored
        if rec.key in read and _detail_differs(conn, name, rec):
            upsert_doc(conn, replace(rec, partial=True), adapter=name, now=now)
            res.n_written += 1
        return
    if is_suppressed(conn, name, rec.key, h):
        return
    upsert_doc(conn, rec, adapter=name, now=now)
    set_state(conn, name, rec.key, "doc", h, now)
    res.n_written += 1


def _detail_alert(name: str, key: str) -> str:
    return f"detail:{name}:{key}"


def _detail_differs(conn, name: str, rec: DocRecord) -> bool:
    row = conn.execute("SELECT excerpt, attachments_json FROM doc WHERE adapter=? AND doc_key=?",
                       (name, rec.doc_key)).fetchone()
    if row is None:
        return False
    return ((rec.excerpt is not None and rec.excerpt != row["excerpt"])
            or (bool(rec.attachments) and list(rec.attachments) != json.loads(row["attachments_json"])))


def _gone(conn, name, rec: DocRecord, h: str, res: RunResult, now: str) -> None:
    """The announcement's page answered 404: withdraw it now, with the facts it stated (their keys start with its key;
    C2: a dated closure is taken down when its page is gone). The list row that still shows it is remembered, so later
    runs do not write it back; an edited row is new content and is written as usual. Its page is asked for again on full
    scans only: when it answers, the memory is lifted and the announcement and its closures are written again."""
    res.n_retired += withdraw_doc(conn, name, rec.doc_key, now=now)
    derived = conn.execute("SELECT record_key FROM record_state WHERE adapter=? AND kind='fact' AND status='active' "
                           "AND substr(record_key, 1, ?) = ?", (name, len(rec.key) + 1, rec.key + ":")).fetchall()
    for (key,) in derived:
        res.n_retired += _retire(conn, name, key, "fact", now)
    conn.execute("UPDATE record_state SET status='retired' WHERE adapter=? AND record_key=?", (name, rec.key))
    suppress(conn, adapter=name, record_key=rec.key, content_hash=h, kind="doc", ref=rec.doc_key, note="gone", now=now)


def _retire(conn, name: str, key: str, kind: str, now: str) -> bool:
    if kind == "doc":
        done = withdraw_doc(conn, name, key.split(":", 1)[-1], now=now)
    elif kind == "event":
        done = retire_event(conn, key, now=now)
    else:
        done = retire_fact(conn, key, now=now) is not None
    conn.execute("UPDATE record_state SET status='retired' WHERE adapter=? AND record_key=?", (name, key))
    supersede_pending(conn, name, key, now=now)
    return done


def _countable(conn, kind: str, key: str) -> tuple[bool, tuple[date, date] | None]:
    """(whether an absence of this record can count at all, its current dates)."""
    if kind == "fact":
        f = conn.execute("""SELECT f.valid_from, f.valid_to, s.kind FROM fact f JOIN source_doc s ON s.id = f.source_doc
                             WHERE f.lineage_id=? AND f.recorded_to IS NULL""", (lineage_id(key),)).fetchone()
        if f is None or f["kind"] == "table_page":  # nothing in force, or a dated table entry that expires by itself
            return False, None
        return True, (date.fromisoformat(f["valid_from"]), date.fromisoformat(f["valid_to"]))
    if kind == "event":
        e = conn.execute("SELECT during_from, during_to FROM calendar_event WHERE uid=? AND status <> 'withdrawn'",
                         (key,)).fetchone()
        return (e is not None), ((date.fromisoformat(e[0]), date.fromisoformat(e[1])) if e else None)
    return True, None


def _missing(conn, adapter: Adapter, keys: set[str], parsed: list[Record], res: RunResult, today: date, now: str) -> None:
    """C2: a full scan counts an absence only for a record it had the chance to produce; three in a row retire it,
    without review."""
    name = adapter.name
    for key in sorted(keys):
        st = _state(conn, name, key)
        if st is None or st["status"] != "active":  # already taken down in this run (C1 displacement, a 404 page)
            continue
        countable, span = _countable(conn, st["kind"], key)
        if not countable or not adapter.counts_missing(key, st["kind"], span, parsed, today):
            continue
        conn.execute("UPDATE record_state SET missing_full_scans=missing_full_scans+1 WHERE adapter=? AND record_key=?",
                     (name, key))
        if _state(conn, name, key)["missing_full_scans"] >= RETIRE_AFTER_FULL_SCANS:
            res.n_retired += _retire(conn, name, key, st["kind"], now)


def _full_scan_due(conn: sqlite3.Connection, name: str, today: date) -> bool:
    start = datetime(today.year, today.month, today.day, tzinfo=TAIPEI).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return conn.execute("SELECT 1 FROM crawl_run WHERE adapter=? AND full_scan=1 AND passed=1 AND started_at>=?",
                        (name, start)).fetchone() is None


def run_all(settings: Settings, *, today: date, only: list[str] | None = None, full_scan: bool | None = None,
            adapters: list[Adapter] | None = None, client=None) -> list[RunResult]:
    from nknu.crawl.adapter import load_adapters
    from nknu.crawl.fetch import Client

    if adapters is None:
        registry = load_adapters()
        adapters = [registry[n]() for n in run_order(only or list(registry))]
    adapters = [a for a in adapters if a.name not in settings.disabled_sources]
    conn = connect(settings.facts_path)
    migrate(conn)
    own_client = client is None
    client = client or Client()
    results = []
    try:
        for a in adapters:
            fs = _full_scan_due(conn, a.name, today) if full_scan is None else full_scan
            sink = None
            if hasattr(client, "warc") and not settings.readonly:
                sink = client.warc = WarcSink(settings.warc_dir, a.name, int(time.time()), datetime.now(UTC))
            try:
                results.append(run_adapter(conn, a, client, today=today, full_scan=fs, readonly=settings.readonly))
            except Exception as exc:
                conn.rollback()
                log.exception("adapter %s crashed", a.name)
                if not settings.readonly:
                    add_alert(conn, adapter=a.name, level="error", code="crashed", message=redact(f"{type(exc).__name__}: {exc}"),
                              dedup_key=f"{a.name}:crashed", now=iso_now(), remind_daily=True)
                    conn.execute("UPDATE crawl_run SET finished_at=?, passed=0, error=? WHERE adapter=? AND finished_at IS NULL",
                                 (iso_now(), redact(str(exc))[:500], a.name))
                    conn.commit()
                results.append(RunResult(a.name, False, error=f"{type(exc).__name__}: {exc}"))
            finally:
                if sink is not None:
                    sink.close()
                    client.warc = None
        gc_warc(settings.warc_dir, today)
    finally:
        if own_client:
            client.close()
        conn.close()
    return results
