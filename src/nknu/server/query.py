"""Query functions behind the MCP tools (hours, academic calendar, single facts). Answers are the answer cards built by
`nknu build`; a date outside the card window goes through the same resolve() and template on serve.db. No LLM anywhere.

Every function takes the ServeDB first and an optional `now` (tests freeze the clock with it). Dates are Taipei days."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from nknu.build.cards import contact_of, hours_sentence, names_of
from nknu.build.templates import DOWZH, Templates, span_zh
from nknu.core.resolve import SQLiteBackend, day_hours, resolve
from nknu.core.textnorm import alias_norm, norm_text
from nknu.core.timeutil import TAIPEI, now_utc
from nknu.core.vocab import ancestors, managing_org, resolve_place
from nknu.server.envelope import NOTICE_LABEL, echo, fit_budget, make_envelope
from nknu.server.linking import PLACE_TYPES, link_entities
from nknu.server.servedb_reader import ServeDB

MAX_DAYS = 14
MAX_CALENDAR_LIMIT = 50
MAX_CALENDAR_SPAN = 366
NOTICE_RECENT_DAYS = 14
NOTICE_MAX = 3
ALTERNATIVES_MAX = 3

# A notice counts as current while not withdrawn and either inside its posting period, or (no period given) under 180 days
# old. Search (Task 19) uses the same condition. Parameters: :today, :ttl_from (today - 180 days).
CURRENT_DOC_SQL = ("withdrawn_at IS NULL AND (announce_until >= :today "
                   "OR (announce_until IS NULL AND published_on >= :ttl_from))")
NOTICE_TTL_DAYS = 180

CAVEAT_NO_PLACE = "查無此地點，請勿推測"
CAVEAT_AMBIGUOUS = "有多個地點符合，請確認校區"
CAVEATS = {
    "stale": "最近一筆資料已過期，請勿推測開放時間",
    "conflict": "官方來源不一致，請勿推測，請以現場公告為準",
    "no_data": "查無開放時間資料，請勿推測",
}
_SEVERITY = ("conflict", "stale", "no_data")


def today_info(now: dt.datetime | None = None) -> dict:
    t = (now or now_utc()).astimezone(TAIPEI)
    return {"date": t.date().isoformat(), "weekday": weekday_zh(t.date()), "time": t.strftime("%H:%M")}


def weekday_zh(d: dt.date) -> str:
    return "週" + DOWZH[d.isoweekday() - 1]


_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_ROC = re.compile(r"(\d{2,3})([-./])(\d{1,2})\2(\d{1,2})")


def parse_date_arg(s: str) -> dt.date:
    """YYYY-MM-DD, or an ROC date 115-10-24 / 115.10.24 / 115/10/24. Anything else raises ValueError."""
    s = s.strip()
    if m := _ISO.fullmatch(s):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := _ROC.fullmatch(s):
        y, mo, d = int(m[1]) + 1911, int(m[3]), int(m[4])
    else:
        raise ValueError(f"日期格式應為 YYYY-MM-DD 或民國 115-10-24：{echo(s)}")
    try:
        return dt.date(y, mo, d)
    except ValueError:
        raise ValueError(f"不是有效的日期：{echo(s)}") from None


def _incl(hi: str) -> str:
    """Half-open end -> the inclusive last day."""
    return (dt.date.fromisoformat(hi) - timedelta(days=1)).isoformat()


def _fact_view(b: dict) -> dict:
    """A resolve() brief (winner or alternative) as an envelope fact."""
    out = {"id": b["fact_id"], "valid": [b["valid"][0], _incl(b["valid"][1])],
           "rules": [{"dows": r["dows"], "opens": r["opens"], "closes": r["closes"]} for r in b.get("rules") or []]}
    if b["value"].get("closed"):
        out["closed"] = True
    src = {"title": b["source"].get("title"), "url": b["source"]["url"], "tier": b.get("tier"),
           "published_on": b.get("published_on")}
    out["source"] = {k: v for k, v in src.items() if v is not None}
    return out


@dataclass(frozen=True)
class _Day:
    day: dt.date
    res: dict  # resolve()-shaped: status, winner | alternatives | last_known
    answer: str
    card_to: dt.date | None  # exclusive end of the answer card it came from; None = resolved at query time


class _Ctx:
    """Per-request lookups: answer cards first, runtime resolve() for days outside the card window."""

    def __init__(self, conn: sqlite3.Connection, tpl: Templates):
        self.conn, self.tpl = conn, tpl
        self.names = names_of(conn)
        self._be = SQLiteBackend(conn)
        self._days: dict[tuple[str, dt.date], _Day] = {}

    def day(self, entity: str, d: dt.date) -> _Day:
        key = (entity, d)
        if key not in self._days:
            self._days[key] = self._load(entity, d)
        return self._days[key]

    def _load(self, entity: str, d: dt.date) -> _Day:
        row = self.conn.execute(
            "SELECT valid_to, answer_zh, payload_json FROM answer_card WHERE entity_id=? AND intent='hours' "
            "AND valid_from<=? AND ?<valid_to LIMIT 1", (entity, d.isoformat(), d.isoformat())).fetchone()
        if row is not None:
            return _Day(d, json.loads(row["payload_json"]), row["answer_zh"], dt.date.fromisoformat(row["valid_to"]))
        res = resolve(self._be, entity, "hours", d)
        contact = contact_of(self.conn, entity, self.names)
        return _Day(d, res, hours_sentence(entity, res, self.names, contact, self.tpl, d, d + timedelta(days=1)), None)

    def next_change(self, entity: str, first: _Day, *, with_answer: bool) -> dict | None:
        if first.card_to is None:
            return None
        row = self.conn.execute(
            "SELECT valid_from, answer_zh FROM answer_card WHERE entity_id=? AND intent='hours' AND valid_from>=? "
            "ORDER BY valid_from LIMIT 1", (entity, first.card_to.isoformat())).fetchone()
        if row is None:
            return None
        return {"from": row["valid_from"], "answer": row["answer_zh"]} if with_answer else {"from": row["valid_from"]}


def _segments(d: _Day) -> list[dict] | None:
    """Today's segments of a current result; None when the hours are unknown (stale, conflict, no times stated)."""
    if d.res["status"] != "current":
        return None
    h = day_hours(d.res["winner"], d.day)
    if h["open"] is None:
        return None
    return h.get("segments") or []


def _open_now(today: _Day, yesterday: _Day, hm: str) -> bool | None:
    """Open at hh:mm: today's segments, plus yesterday's segment that runs past midnight and has not ended yet."""
    for s in _segments(yesterday) or []:
        if s["closes"] < s["opens"] and hm < s["closes"]:
            return True
    segs = _segments(today)
    if segs is None:
        return None
    return any((s["opens"] <= hm < s["closes"]) if s["opens"] < s["closes"] else hm >= s["opens"] for s in segs)


def _day_view(d: _Day, *, values: bool = True) -> dict:
    """One day of the days list; values=False (a stale envelope) leaves only date, weekday and status."""
    out = {"date": d.day.isoformat(), "weekday": weekday_zh(d.day), "status": d.res["status"]}
    if d.res["status"] != "current" or not values:
        return out
    h = day_hours(d.res["winner"], d.day)
    if h["open"] is True:
        out["open"] = True
        out["segments"] = [f"{s['opens']}–{s['closes']}" for s in h["segments"]]
    elif h["open"] is False:
        out["open"] = False
        reason = d.res["winner"]["value"].get("reason") if d.res["winner"]["value"].get("closed") else None
        if reason:
            out["reason"] = reason
    return out


def _labels(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """alias_norm of every name and alias of the given entities."""
    out: set[str] = set()
    for e in ids:
        row = conn.execute("SELECT name_zh, name_en FROM entity WHERE id=?", (e,)).fetchone()
        if row:
            out |= {alias_norm(row["name_zh"]), alias_norm(row["name_en"] or "")}
        out |= {r[0] for r in conn.execute("SELECT alias_norm FROM entity_alias WHERE entity_id=?", (e,))}
    return out - {""}


def current_doc_params(today: dt.date) -> dict:
    return {"today": today.isoformat(), "ttl_from": (today - timedelta(days=NOTICE_TTL_DAYS)).isoformat()}


def _recent_notices(conn: sqlite3.Connection, entity: str, today: dt.date) -> list[dict]:
    """Current notices from the managing unit, at most 14 days old, that name the place or one of its ancestors."""
    org = managing_org(conn, entity, "hours", today)
    if org is None:
        return []
    units = _labels(conn, [org])
    keys = _labels(conn, [entity, *ancestors(conn, entity, today)])
    rows = conn.execute(
        f"SELECT title, excerpt, unit, published_on, url FROM doc WHERE {CURRENT_DOC_SQL} AND published_on >= :recent "
        "ORDER BY published_on DESC, id DESC",
        {**current_doc_params(today), "recent": (today - timedelta(days=NOTICE_RECENT_DAYS)).isoformat()})
    out = []
    for r in rows:
        unit = alias_norm(r["unit"] or "")
        if not any(unit.startswith(u) for u in units):
            continue
        text = alias_norm(f"{r['title']} {r['excerpt'] or ''}")
        if any(k in text for k in keys):
            out.append({"title": r["title"], "published_on": r["published_on"], "url": r["url"], "label": NOTICE_LABEL})
            if len(out) == NOTICE_MAX:
                break
    return out


# Clock times in free text: 09:00, 9：30, 9時, 9點半, 9時30分.
_TIME_RE = re.compile(r"(?<!\d)\d{1,2}\s*(?:[:：]\s*\d{2}|[時點点](?:\s*\d{1,2}\s*分|半)?)")
TIME_MASK = "--:--"


def mask_times(text: str) -> str:
    return _TIME_RE.sub(TIME_MASK, text)


def _similar(conn: sqlite3.Connection, place: str) -> list[str]:
    """Up to 3 place names that look like the query (shown with no_data; never used as an answer)."""
    q = alias_norm(place)
    if not q:
        return []
    marks = ",".join("?" * len(PLACE_TYPES))
    best: dict[str, tuple[float, str]] = {}
    for r in conn.execute(f"SELECT e.id, e.name_zh, a.alias FROM entity e LEFT JOIN entity_alias a ON a.entity_id = e.id "
                          f"WHERE e.type IN ({marks})", PLACE_TYPES):
        for label in (r["name_zh"], r["alias"]):
            if label:
                score = difflib.SequenceMatcher(None, q, alias_norm(label)).ratio()
                if score > best.get(r["id"], (0.0, ""))[0]:
                    best[r["id"]] = (score, r["name_zh"])
    ranked = sorted(((s, eid, name) for eid, (s, name) in best.items() if s >= 0.2), key=lambda x: (-x[0], x[1]))
    return [name for _, _, name in ranked[:3]]


def _find_places(conn: sqlite3.Connection, place: str) -> list[str]:
    """Exact id/name/alias lookup; when that finds nothing, longest-match entity linking on the free text."""
    return resolve_place(conn, place) or link_entities(conn, place)


def worst_status(statuses: set[str]) -> str:
    """The most severe status of several places (conflict, stale, no_data, else current)."""
    return next((s for s in _SEVERITY if s in statuses), "current")


def _candidate(ctx: _Ctx, entity: str, d: dt.date) -> dict:
    """One place of an ambiguous list; its next_change says what comes next (no sentence when stale: no values)."""
    day = ctx.day(entity, d)
    out = {"entity_id": entity, "name": ctx.names.get(entity, entity), "status": day.res["status"], "answer": day.answer}
    nxt = ctx.next_change(entity, day, with_answer=day.res["status"] != "stale")
    return {**out, "next_change": nxt} if nxt else out


def place_candidates(conn: sqlite3.Connection, tpl: Templates, ids: list[str], d: dt.date) -> list[dict]:
    """{entity_id, name, status, answer, next_change?} per place on day d: the answer card, or resolve() outside the
    card window."""
    ctx = _Ctx(conn, tpl)
    return [_candidate(ctx, e, d) for e in ids]


def hours_envelope(conn: sqlite3.Connection, tpl: Templates, entity: str, d0: dt.date, today: dict,
                   days: int = 1) -> dict:
    """The envelope fields (no kb/today, not yet budgeted) get_hours answers for one place from day d0; `today` is
    today_info(). nknu_search answers an entity match with the same fields."""
    return _entity_fields(_Ctx(conn, tpl), entity, d0, dt.date.fromisoformat(today["date"]), today["time"], days)


def _entity_fields(ctx: _Ctx, entity: str, d0: dt.date, today: dt.date, hm: str, days: int) -> dict:
    first = ctx.day(entity, d0)
    res, status = first.res, first.res["status"]
    env: dict = {"found": status in ("current", "conflict"), "status": status, "match": "entity", "answer": first.answer}
    if status == "current":
        env["facts"] = [_fact_view(res["winner"])]
    elif status == "conflict":
        env["alternatives"] = [_fact_view(a) for a in res["alternatives"][:ALTERNATIVES_MAX]]
    elif status == "stale" and res.get("last_known"):  # never an expired value: only when it ended and where it came from
        lk = res["last_known"]
        env["last_known"] = {"valid_to_inclusive": _incl(lk["valid"][1]), "source_url": lk["source"]["url"]}
    if d0 == today:
        env["open_now"] = _open_now(first, ctx.day(entity, d0 - timedelta(days=1)), hm)
    stale = status == "stale"  # no hours anywhere: not the next card's, not a later day's, not in a notice title
    env["next_change"] = ctx.next_change(entity, first, with_answer=not stale)
    if days > 1:
        env["days"] = [_day_view(ctx.day(entity, d0 + timedelta(days=i)), values=not stale) for i in range(days)]
    notices = _recent_notices(ctx.conn, entity, today)
    env["recent_notices"] = [{**n, "title": mask_times(n["title"])} for n in notices] if stale else notices
    env["caveat"] = CAVEATS.get(status)
    return env


def _base(db: ServeDB, now: dt.datetime | None) -> tuple[dict, dt.date]:
    info = today_info(now)
    return {"kb": db.kb, "today": info}, dt.date.fromisoformat(info["date"])


def get_hours(db: ServeDB, place: str, date: str | None = None, days: int = 1, now: dt.datetime | None = None) -> dict:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days 應為 1–{MAX_DAYS}")
    now = now or now_utc()
    base, today = _base(db, now)
    d0 = parse_date_arg(date) if date else today
    conn = db.conn()
    ids = _find_places(conn, place)
    if not ids:
        return fit_budget(make_envelope(**base, found=False, status="no_data", caveat=CAVEAT_NO_PLACE,
                                        similar=_similar(conn, place)))
    ctx = _Ctx(conn, db.templates())
    if len(ids) > 1:
        cands = [_candidate(ctx, e, d0) for e in ids]
        statuses = {c["status"] for c in cands}
        status = worst_status(statuses)
        return fit_budget(make_envelope(**base, found=any(s in ("current", "conflict") for s in statuses), status=status,
                                        match="ambiguous", facts=cands, caveat=CAVEAT_AMBIGUOUS))
    return fit_budget(make_envelope(**base, **_entity_fields(ctx, ids[0], d0, today, base["today"]["time"], days)))


def get_calendar(db: ServeDB, from_: str | None = None, to: str | None = None, keyword: str | None = None, limit: int = 10,
                 now: dt.datetime | None = None) -> dict:
    """Academic-calendar events overlapping [from_, to] (inclusive), optionally filtered by a keyword in the summary."""
    if not 1 <= limit <= MAX_CALENDAR_LIMIT:
        raise ValueError(f"limit 應為 1–{MAX_CALENDAR_LIMIT}")
    base, today = _base(db, now)
    lo = parse_date_arg(from_) if from_ else today
    hi = parse_date_arg(to) if to else lo + timedelta(days=180 if keyword else 30)
    if hi < lo:
        raise ValueError("to 早於 from")
    if (hi - lo).days > MAX_CALENDAR_SPAN:
        raise ValueError(f"查詢區間最長 {MAX_CALENDAR_SPAN} 天")
    conn, tpl = db.conn(), db.templates()
    names = names_of(conn)
    rows = conn.execute(
        """SELECT e.uid, e.summary, e.during_from, e.during_to, e.category, e.status, s.url, s.title, s.publisher
             FROM calendar_event e JOIN source_doc s ON s.id = e.source_doc
            WHERE e.during_to > ? AND e.during_from <= ? ORDER BY e.during_from, e.uid""",
        (lo.isoformat(), hi.isoformat())).fetchall()
    kw = norm_text(keyword.strip()) if keyword and keyword.strip() else None
    hits = [r for r in rows if kw is None or kw in norm_text(r["summary"])]
    span = span_zh(lo, hi + timedelta(days=1))
    kw_zh = f"符合「{keyword.strip()}」的" if kw else ""
    if not hits:
        org = managing_org(conn, "nknu", "calendar")
        answer = tpl.render("calendar_none", span=span, kw=kw_zh, contact=names.get(org, "學校相關單位") if org else "學校相關單位")
        return fit_budget(make_envelope(**base, found=False, status="no_data", answer=answer,
                                        caveat="查無符合的行事曆事件，請勿推測"))
    sources = list(dict.fromkeys((names.get(r["publisher"], r["publisher"]), r["title"] or "教學行事曆", r["url"]) for r in hits))
    answer = tpl.render("calendar_list", span=span, kw=kw_zh, n=len(hits),
                        sources="；".join(f"{p}《{t}》" for p, t, _ in sources))
    facts = [_event_view(r) for r in hits[:limit]]
    return fit_budget(make_envelope(**base, found=True, status="current", answer=answer, facts=facts,
                                    sources=[{"title": t, "url": u} for _, t, u in sources],
                                    more=len(hits) - len(facts) or None))


def _event_view(r: sqlite3.Row) -> dict:
    out = {"id": r["uid"], "summary": r["summary"], "valid": [r["during_from"], _incl(r["during_to"])]}
    if r["category"] != "other":
        out["category"] = r["category"]
    if r["status"] != "confirmed":
        out["status"] = r["status"]
    return out


_FACT_SQL = """SELECT f.*, s.url, s.title, s.publisher, s.published_on FROM fact f JOIN source_doc s ON s.id = f.source_doc"""


def _rules(conn: sqlite3.Connection, fact_id: str) -> list[dict]:
    return [{"dows": json.loads(r["dows"]), "opens": r["opens"], "closes": r["closes"]}
            for r in conn.execute("SELECT dows, opens, closes FROM hours_rule WHERE fact_id=? ORDER BY dows, opens", (fact_id,))]


def _brief_of(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {"fact_id": row["id"], "valid": [row["valid_from"], row["valid_to"]], "value": json.loads(row["value_json"]),
            "rules": _rules(conn, row["id"]), "tier": row["tier"], "published_on": row["published_on"],
            "source": {"url": row["url"], "title": row["title"], "publisher": row["publisher"]}}


def get_fact(db: ServeDB, id: str, include_history: bool = False, now: dt.datetime | None = None) -> dict:
    """A fact version (by version id or lineage id; a lineage gives its current version) or a calendar event (by uid)."""
    base, today = _base(db, now)
    conn, tpl = db.conn(), db.templates()
    row = conn.execute(f"{_FACT_SQL} WHERE f.id=?", (id,)).fetchone() or conn.execute(
        f"{_FACT_SQL} WHERE f.lineage_id=? ORDER BY f.recorded_to IS NULL DESC, f.recorded_from DESC LIMIT 1", (id,)).fetchone()
    if row is None:
        return _event_fact(conn, tpl, base, id)
    names = names_of(conn)
    brief = _brief_of(conn, row)
    lo, hi = dt.date.fromisoformat(row["valid_from"]), dt.date.fromisoformat(row["valid_to"])
    publisher, title = names.get(row["publisher"], row["publisher"]), row["title"] or row["url"]
    current_row = conn.execute("SELECT id FROM fact WHERE lineage_id=? AND recorded_to IS NULL", (row["lineage_id"],)).fetchone()
    if row["recorded_to"] is not None:
        why = "已被新版本取代"
    elif row["review"] == "rejected" or row["rank"] == "deprecated":
        why = "未通過審核"
    elif hi <= today:
        why = "已過有效期"
    else:
        why = None
    view = _fact_view(brief)
    fact = {"id": view.pop("id"), "lineage": row["lineage_id"], "entity_id": row["entity_id"], **view,
            "recorded_from": row["recorded_from"]}
    if why is None:
        if row["attribute"] == "hours":
            res = {"status": "current", "winner": brief}
            answer = hours_sentence(row["entity_id"], res, names, contact_of(conn, row["entity_id"], names), tpl, lo, hi)
        else:
            answer = tpl.render("calendar_event", span=span_zh(lo, hi), summary=brief["value"].get("label") or row["quote"],
                                publisher=publisher, title=title)
        env = {"found": True, "status": "current", "answer": answer, "facts": [fact]}
    else:  # a version that is not in force: no values outside the explicitly requested history
        fact.pop("rules", None)
        fact.pop("closed", None)
        if row["recorded_to"]:
            fact["recorded_to"] = row["recorded_to"]
        if current_row and current_row["id"] != row["id"]:
            fact["superseded_by"] = current_row["id"]
        answer = tpl.render("fact_stale", span=span_zh(lo, hi), publisher=publisher, title=title, why=why)
        env = {"found": True, "status": "stale", "answer": answer, "facts": [fact], "caveat": f"此筆資料{why}，請勿當作現行資料"}
    if include_history:  # the versions carry the values (and every id of the lineage); the fact keeps its source
        env["history"] = [_version(conn, v) for v in conn.execute(
            f"{_FACT_SQL} WHERE f.lineage_id=? ORDER BY f.recorded_from, f.id", (row["lineage_id"],))]
        env["facts"] = [{k: fact[k] for k in ("id", "source", "superseded_by") if k in fact}]
    return fit_budget(make_envelope(**base, **env))


def _version(conn: sqlite3.Connection, v: sqlite3.Row) -> dict:
    out = {"id": v["id"], "recorded_from": v["recorded_from"], "valid": [v["valid_from"], _incl(v["valid_to"])],
           "rules": _rules(conn, v["id"])}
    if json.loads(v["value_json"]).get("closed"):
        out["closed"] = True
    if v["recorded_to"]:
        out["recorded_to"] = v["recorded_to"]
    return out


def _event_fact(conn: sqlite3.Connection, tpl: Templates, base: dict, uid: str) -> dict:
    r = conn.execute(
        """SELECT e.uid, e.summary, e.during_from, e.during_to, e.category, e.status, s.url, s.title, s.publisher
             FROM calendar_event e JOIN source_doc s ON s.id = e.source_doc WHERE e.uid=?""", (uid,)).fetchone()
    if r is None:
        return fit_budget(make_envelope(**base, found=False, status="no_data", caveat="查無此 id，請勿推測"))
    names = names_of(conn)
    lo, hi = dt.date.fromisoformat(r["during_from"]), dt.date.fromisoformat(r["during_to"])
    answer = tpl.render("calendar_event", span=span_zh(lo, hi), summary=r["summary"],
                        publisher=names.get(r["publisher"], r["publisher"]), title=r["title"] or "教學行事曆")
    fact = {**_event_view(r), "source": {"title": r["title"], "url": r["url"]}}
    return fit_budget(make_envelope(**base, found=True, status="current", answer=answer, facts=[fact]))
