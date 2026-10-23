"""Answer cards: resolve() precomputed over a date window, adjacent days with the same result merged into one card."""

from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from nknu.build.templates import Templates, dows_zh, event_note, span_zh
from nknu.core.ids import uuid7
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.vocab import managing_org

WINDOW_BEFORE = 7
WINDOW_AFTER = 180
OLD_SOURCE_DAYS = 365  # U3: a source published longer ago than this is dated in the answer


@dataclass(frozen=True)
class CardRow:
    id: str
    entity_id: str
    intent: str
    qualifier: str
    valid_from: str
    valid_to: str
    status: str
    decided_by: str
    answer_zh: str
    payload_json: str
    fact_ids_json: str
    template_ver: int


def hours_entities(conn: sqlite3.Connection) -> list[str]:
    """Entities with current hours facts, plus every place below an entity whose facts propagate."""
    rows = conn.execute(
        """WITH RECURSIVE down(id) AS (
             SELECT DISTINCT entity_id FROM fact WHERE attribute='hours' AND recorded_to IS NULL AND propagates=1
             UNION SELECT r.subject FROM entity_relation r JOIN down d ON r.object = d.id WHERE r.predicate='containedInPlace')
           SELECT id FROM down
           UNION SELECT DISTINCT entity_id FROM fact WHERE attribute='hours' AND recorded_to IS NULL""").fetchall()
    return sorted(r[0] for r in rows)


def _sig(res: dict) -> tuple:
    return (res["status"], (res.get("winner") or {}).get("fact_id"), tuple(a["fact_id"] for a in res.get("alternatives", [])),
            json.dumps(res.get("last_known"), sort_keys=True))


def hours_segments(backend: SQLiteBackend, entity: str, lo: date, hi: date) -> list[tuple[date, date, dict]]:
    cands = backend.candidates(entity, "hours", "", lo, None)
    cuts = sorted({lo, hi} | {d for c in cands for d in (c["lo"], c["hi"]) if lo < d < hi})
    segs: list[list] = []
    for a, b in itertools.pairwise(cuts):
        res = resolve(backend, entity, "hours", a)
        if segs and _sig(segs[-1][2]) == _sig(res):
            segs[-1][1] = b
        else:
            segs.append([a, b, res])
    return [(a, b, r) for a, b, r in segs]


def _source_label(src: dict, names: dict[str, str]) -> tuple[str, str]:
    return names.get(src["publisher"], src["publisher"]), src["title"] or src["url"]


def _weekdays_in(lo: date, hi: date) -> set[int]:
    return {(lo + timedelta(days=i)).isoweekday() for i in range(min((hi - lo).days, 7))}


def _schedule(w: dict) -> tuple[str, str]:
    """A brief's weekly rules as printed, and the weekdays of its validity it leaves closed (；週六、日不開放)."""
    rules = w.get("rules") or []
    schedule = "；".join(f"{dows_zh(r['dows'])} {r['opens']}–{r['closes']}" for r in rules) or "時段未載明"
    open_days = {d for r in rules for d in r["dows"]}
    lo, hi = date.fromisoformat(w["valid"][0]), date.fromisoformat(w["valid"][1])
    closed = sorted(d for d in _weekdays_in(lo, hi) if d not in open_days) if rules else []
    return schedule, f"；{dows_zh(closed)}不開放" if closed else ""


def _stated(w: dict) -> str:
    """What a (last known) brief says, for the stale sentence: 暫停開放（reason）, or its hours."""
    value = w.get("value") or {}
    if value.get("closed"):
        return f"暫停開放（{value.get('reason') or '公告停開'}）"
    schedule, closed_days = _schedule(w)
    return schedule + closed_days + (f"（{value['reason']}）" if value.get("reason") else "")


def _old_source(w: dict, tpl: Templates, today: date | None) -> str:
    """U3: a current answer from a source published more than OLD_SOURCE_DAYS before today says that date."""
    published = w.get("published_on")
    if today is None or not published or date.fromisoformat(published) >= today - timedelta(days=OLD_SOURCE_DAYS):
        return ""
    return tpl.render("old_source", published_on=published)


def hours_sentence(entity: str, result: dict, names: dict[str, str], contact: str, tpl: Templates, seg_lo: date,
                   seg_hi: date, today: date | None = None) -> str:
    """The answer for one place over [seg_lo, seg_hi): a current sentence (plus the source date when it is over a year
    old), a conflict, the last known statement when stale, or no_data; the stated fact's 附註 follow."""
    sentence = _hours_sentence(entity, result, names, contact, tpl, seg_lo, seg_hi)
    stated = {"current": result.get("winner"), "stale": result.get("last_known")}.get(result["status"]) or {}
    if result["status"] == "current":
        sentence += _old_source(stated, tpl, today)
    notes = (stated.get("value") or {}).get("notes")
    return sentence + ("附註：" + "".join(notes) if notes else "")


def _hours_sentence(entity: str, result: dict, names: dict[str, str], contact: str, tpl: Templates, seg_lo: date,
                    seg_hi: date) -> str:
    # seg_lo/seg_hi only date a conflict; a current or stale sentence states its source's own validity
    name = names.get(entity, entity)
    status = result["status"]
    if status == "current":
        w = result["winner"]
        span = span_zh(date.fromisoformat(w["valid"][0]), date.fromisoformat(w["valid"][1]))  # the source's validity
        publisher, title = _source_label(w["source"], names)
        if w["value"].get("closed"):
            return tpl.render("hours_closed", name=name, span=span, reason=w["value"].get("reason") or "公告停開",
                              publisher=publisher, title=title)
        schedule, closed_days = _schedule(w)
        reason = w["value"].get("reason")
        return tpl.render("hours_open", name=name, span=span, note=f"（{reason}）" if reason else "", schedule=schedule,
                          closed_days=closed_days, publisher=publisher, title=title)
    span = span_zh(seg_lo, seg_hi)
    if status == "conflict":
        sources = "；".join("{}《{}》".format(*_source_label(a["source"], names)) for a in result["alternatives"])
        return tpl.render("hours_conflict", name=name, span=span, n=len(result["alternatives"]), sources=sources)
    lk = result.get("last_known")
    if status == "stale" and lk:  # U3: the last known statement, dated, and said to be possibly out of date
        publisher, title = _source_label(lk["source"], names)
        lk_span = span_zh(date.fromisoformat(lk["valid"][0]), date.fromisoformat(lk["valid"][1]))
        return tpl.render("hours_stale", name=name, publisher=publisher, title=title, span=lk_span, value=_stated(lk),
                          from_=lk["valid"][1], contact=contact)  # the first day the last statement no longer covers
    return tpl.render("hours_no_data", name=name, contact=contact)


def _payload(res: dict) -> dict:
    out = {k: res[k] for k in ("status", "decided_by", "winner", "supporting", "last_known") if k in res}
    if "alternatives" in res:
        out["alternatives"] = [{k: v for k, v in a.items() if k != "today"} for a in res["alternatives"]]
    return out


def names_of(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, name_zh FROM entity")}


def contact_of(conn: sqlite3.Connection, entity: str, names: dict[str, str]) -> str:
    org = managing_org(conn, entity, "hours")
    return names.get(org, "學校相關單位") if org else "學校相關單位"


def hours_card_rows(conn: sqlite3.Connection, tpl: Templates, today: date) -> list[CardRow]:
    be, names = SQLiteBackend(conn), names_of(conn)
    lo, hi = today - timedelta(days=WINDOW_BEFORE), today + timedelta(days=WINDOW_AFTER)
    out = []
    for entity in hours_entities(conn):
        contact = contact_of(conn, entity, names)
        for a, b, res in hours_segments(be, entity, lo, hi):
            fids = [x for x in [(res.get("winner") or {}).get("fact_id"), *(x["fact_id"] for x in res.get("alternatives", []))]
                    if x]
            out.append(CardRow(uuid7(), entity, "hours", "", a.isoformat(), b.isoformat(), res["status"], res["decided_by"],
                               hours_sentence(entity, res, names, contact, tpl, a, b, today),
                               json.dumps(_payload(res), ensure_ascii=False), json.dumps(fids), tpl.ver))
    return out


def calendar_card_rows(conn: sqlite3.Connection, tpl: Templates, today: date) -> list[CardRow]:
    names = names_of(conn)
    lo, hi = today - timedelta(days=WINDOW_BEFORE), today + timedelta(days=WINDOW_AFTER)
    rows = conn.execute(
        """SELECT e.uid, e.summary, e.during_from, e.during_to, e.category, e.status, s.url, s.title, s.publisher
             FROM calendar_event e JOIN source_doc s ON s.id = e.source_doc
            WHERE e.during_to > ? AND e.during_from < ? ORDER BY e.during_from""", (lo.isoformat(), hi.isoformat())).fetchall()
    out = []
    for r in rows:
        d0, d1 = date.fromisoformat(r["during_from"]), date.fromisoformat(r["during_to"])
        answer = tpl.render("calendar_event", span=span_zh(d0, d1), summary=r["summary"], note=event_note(r["status"]),
                            publisher=names.get(r["publisher"], r["publisher"]), title=r["title"] or "教學行事曆")
        payload = {"uid": r["uid"], "summary": r["summary"], "category": r["category"], "status": r["status"],
                   "valid": [r["during_from"], r["during_to"]], "source": {"url": r["url"], "title": r["title"]}}
        out.append(CardRow(uuid7(), "nknu", "calendar", r["uid"], r["during_from"], r["during_to"], "current", "ics", answer,
                           json.dumps(payload, ensure_ascii=False), "[]", tpl.ver))
    return out
