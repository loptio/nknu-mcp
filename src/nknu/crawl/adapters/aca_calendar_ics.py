"""教務處教學行事曆 (public Google Calendar embedded on c.nknu.edu.tw/aca/Page.aspx?PN=28&GI=2).

The calendar also carries staff-internal entries (personal leave, 組長 schedules, meetings). Those are dropped here and
never stored or logged (design §3). Holidays additionally become university-wide `holiday` facts; they never imply
that a venue is closed.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import ClassVar, Literal

import icalendar
import recurring_ical_events

from nknu.core.records import EventRecord, FactRecord, Record, SourceRef
from nknu.core.timeutil import TAIPEI
from nknu.crawl.adapter import Adapter, ParseError, Target, adapter
from nknu.crawl.fetch import Snapshot

ICS_URL = "https://calendar.google.com/calendar/ical/nknubf%40gmail.com/public/basic.ics"
PAGE_URL = "https://c.nknu.edu.tw/aca/Page.aspx?PN=28&GI=2"
TITLE = "教學行事曆"
WINDOW_BEFORE, WINDOW_AFTER = timedelta(days=60), timedelta(days=400)

# filter rules ported from prototypes/acquisition/10_ics.py (0 false drops on the 115 academic year)
_MASK = "OＯ○"
_LEAVE = re.compile(r"請假|休假|公假|補休|事假|病假|特休|寒休|暑休|出差|喪假|婚假|產假|休。")
_ACADEMIC_EXC = re.compile(r"補考|學生|新生|選課|註冊|成績|考試|停修|教師送交")
_STAFF = re.compile(r"組長|(^|[^學])上課[:：]|開會|值班")
_MEETING = re.compile(r"會議|會議室|\d+F\d*")
_PUBLIC_HOLIDAY = re.compile(r"放假|補假|國定|連假|調整放假|彈性放假")
_NAME_PREFIX_LEAVE = re.compile(rf"^\s*[一-鿿{_MASK}]{{2,4}}\s*[，,。.．_ ]?\s*(上午|下午)?\s*(請假|休假|出差|寒休|暑休|補休|公假)")


def classify(e) -> Literal["keep", "drop", "review"]:
    s = f"{e.get('SUMMARY') or ''} {e.get('DESCRIPTION') or ''}"
    if _NAME_PREFIX_LEAVE.search(s) and not _PUBLIC_HOLIDAY.search(s):
        return "drop"
    if _LEAVE.search(s) and not _ACADEMIC_EXC.search(s) and not _PUBLIC_HOLIDAY.search(s):
        return "drop"
    if _STAFF.search(s) or _MEETING.search(s):
        return "drop"
    if e.get("RRULE") and isinstance(e.decoded("DTSTART"), datetime):
        return "drop"  # timed weekly entries are staff schedules
    if not str(e.get("SUMMARY") or "").strip():
        return "drop"
    if str(e.get("STATUS")) == "TENTATIVE":
        return "review"
    return "keep"


def category(summary: str) -> str:
    s = summary
    if "期中考試" in s and "成績" not in s:
        return "midterm_exam"
    if "期末考試" in s and "成績" not in s:
        return "final_exam"
    if "停修" in s:
        return "course_withdrawal"
    if re.search(r"放假|補假|National holiday", s):
        return "holiday"
    if re.search(r"上課開始|開始上課|Classes begin|開學", s):
        return "term_begin"
    if re.search(r"寒假開始|暑假開始|winter vacation begins|Summer vacation begins", s, re.IGNORECASE):
        return "vacation_begin"
    return "other"


def _as_date(v) -> date:
    return v.astimezone(TAIPEI).date() if isinstance(v, datetime) else v


def _span(e) -> tuple[date, date]:
    start = e.decoded("DTSTART")
    d0 = _as_date(start)
    end = e.decoded("DTEND") if e.get("DTEND") else None
    if end is None:
        return d0, d0 + timedelta(days=1)
    if isinstance(end, datetime):
        end_t = end.astimezone(TAIPEI)
        d1 = end_t.date() + timedelta(days=0 if end_t.time() == datetime.min.time() else 1)
    else:
        d1 = end
    return d0, max(d1, d0 + timedelta(days=1))


@adapter("aca_calendar_ics", hosts=["calendar.google.com"])
class AcaCalendarIcs(Adapter):
    pages_are_html: ClassVar[bool] = False

    def discover(self, ctx) -> list[Target]:
        return [Target(ICS_URL)]

    def counts_missing(self, key: str, kind: str, span: tuple[date, date] | None, records: list[Record],
                       today: date) -> bool:
        """Only events (and holiday facts) inside the window parse() reads count: one that ended more than WINDOW_BEFORE
        ago simply stopped being parsed. A day of margin at each edge covers a fetch just after midnight."""
        if span is None:
            return True
        lo, hi = span
        return hi > today - WINDOW_BEFORE + timedelta(days=1) and lo < today + WINDOW_AFTER - timedelta(days=1)

    def parse(self, snap: Snapshot) -> list[Record]:
        if snap.status != 200:
            return []
        try:
            cal = icalendar.Calendar.from_ical(snap.body)
            anchor = snap.fetched_at.astimezone(TAIPEI).date()
            occurrences = recurring_ical_events.of(cal).between(anchor - WINDOW_BEFORE, anchor + WINDOW_AFTER)
        except Exception as exc:
            raise ParseError(f"ICS not readable: {type(exc).__name__}") from exc
        if not list(cal.walk("VEVENT")):
            raise ParseError("ICS has no VEVENT")
        src = SourceRef(ICS_URL, TITLE, "nknu.org.aca", "ics", snap.sha256)
        kept = [(e, classify(e)) for e in occurrences]
        kept = [(e, c) for e, c in kept if c != "drop"]  # dropped entries are never stored, counted or logged
        uid_count: dict[str, int] = {}
        for e, _ in kept:
            uid_count[str(e["UID"])] = uid_count.get(str(e["UID"]), 0) + 1
        out: list[Record] = []
        for e, c in kept:
            uid, summary = str(e["UID"]), str(e.get("SUMMARY")).strip()
            d0, d1 = _span(e)
            key = uid if uid_count[uid] == 1 else f"{uid}@{d0}"
            status = "tentative" if c == "review" else ("cancelled" if str(e.get("STATUS")) == "CANCELLED" else "confirmed")
            lm = e.decoded("LAST-MODIFIED") if e.get("LAST-MODIFIED") else None
            cat = category(summary)
            out.append(EventRecord(key, uid, summary, d0, d1, cat, status, int(e.get("SEQUENCE") or 0),
                                   lm.isoformat() if lm else None, src))
            if cat == "holiday" and status == "confirmed":
                out.append(FactRecord(f"ics-holiday:{key}", "nknu", "holiday", {"label": summary[:60]}, d0, d1, "explicit",
                                      2, False, src, summary[:200], "ics"))
        return out
