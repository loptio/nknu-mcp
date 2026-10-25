"""教務處教學行事曆 (the public Google Calendar embedded on c.nknu.edu.tw/aca/Page.aspx?PN=28&GI=2), read through the
Google Calendar API: events.list with singleEvents=true and orderBy=startTime, usually one request a run.

calendar.google.com's robots.txt disallows everything but its root page, so the public ICS feed is no longer read
(2026-10-24); the API with the project's key is the way Google offers to read a public calendar. The key is the
GOOGLE_API_KEY environment variable (~/.config/nknu/google.env on the maintainer's machine, /etc/nknu/env on the lab
machine) and travels only in a request header (Target.credential): never in a URL, a log line or the WARC archive.

The adapter keeps the name aca_calendar_ics from when it read the ICS feed, and the record keys stay the ICS UIDs (the
API's iCalUID), so the events, holiday facts and retirement counts stored under that name carry over. The cited source
changed from the ICS address to the school's calendar page, and a record's content hash covers its source: the first run
after the switch rewrites every record once, and an `nknu undo` made before the switch no longer matches (the
maintainer's data held none on 2026-10-24).

The calendar also carries staff-internal entries (personal leave, 組長 schedules, meetings, and a staff member's name
followed by a private errand: 林O，看牙 / 林O去玩). Those are dropped here and never stored or logged (design §3); there is
no review any more, so a name-led entry is dropped unless it speaks of the academic calendar (F-ics-staff-filter).
Holidays additionally become university-wide `holiday` facts; they never imply that a venue is closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import ClassVar, Literal
from urllib.parse import urlencode

from nknu.core.records import EventRecord, FactRecord, Record, SourceRef
from nknu.core.timeutil import TAIPEI
from nknu.crawl.adapter import Adapter, ParseError, Target, adapter
from nknu.crawl.fetch import Snapshot

API_URL = "https://www.googleapis.com/calendar/v3/calendars/nknubf%40gmail.com/events"
PAGE_URL = "https://c.nknu.edu.tw/aca/Page.aspx?PN=28&GI=2"  # the school's page showing the calendar: the cited source
TITLE = "教學行事曆"
WINDOW_BEFORE, WINDOW_AFTER = timedelta(days=60), timedelta(days=400)
MAX_RESULTS = 2500  # the API's largest page size
# events.list may split even a window far below MAX_RESULTS into pages, some short or empty: only nextPageToken says more
# remain (developers.google.com/workspace/calendar/api/v3/reference/events/list, maxResults). fetch_all follows it.
MAX_PAGES = 10

# filter rules ported from prototypes/acquisition/10_ics.py (0 false drops on the 115 academic year)
_MASK = "OＯ○"
_LEAVE = re.compile(r"請假|休假|公假|補休|事假|病假|特休|寒休|暑休|排休|出差|喪假|婚假|產假|休。")
_ACADEMIC_EXC = re.compile(r"補考|學生|新生|選課|註冊|成績|考試|停修|教師送交")
_STAFF = re.compile(r"組長|(^|[^學])上課[:：]|開會|值班")
_MEETING = re.compile(r"會議|會議室|\d+F\d*")
_PUBLIC_HOLIDAY = re.compile(r"放假|補假|國定|連假|調整放假|彈性放假")
_NAME_PREFIX_LEAVE = re.compile(rf"^\s*[一-鿿{_MASK}]{{2,4}}\s*[，,。.．_ ]?\s*(上午|下午)?\s*(請假|休假|出差|寒休|暑休|補休|公假)")
# a name tag: 2-3 characters (or several joined by 、，) at the start, then a separator (林O，… / 陳O。公差) or straight
# into a private errand (林O去玩, 林O休, 林O出國)
_TAG = rf"^\s*(?P<names>[一-鿿{_MASK}]{{2,3}}(?:\s*[、，,]\s*[一-鿿{_MASK}]{{2,3}})*)"
_TAG_SEP = re.compile(_TAG + r"\s*[，,。.．_、:：]")
_TAG_ERRAND = re.compile(_TAG + r"\s*(?:去|休|看牙|看診|看病|植牙|出國|出差|公差|公假|餐敘|聚餐|返鄉|回診|就醫|體檢|健檢|旅遊|爬山)")
_ERRAND = re.compile(r"餐敘|聚餐|看牙|植牙|看診|回診|就醫|去玩|爬山|旅遊|環島|喜宴|婚宴")
# what an academic entry speaks of: a name-led or errand-looking entry that says one of these is kept
_CALENDAR = re.compile(r"上課|停課|補課|調課|放假|補假|連假|假期|國定|紀念日|節|開學|註冊|選課|加退選|停修|棄選|考試|補考|測驗|學測|"
                       r"成績|畢業|典禮|校慶|休學|退學|退費|學期|學年|課表|新生|學生|宿舍|論文|口試|抵免|招生|放榜|寒假|暑假|上班|"
                       r"補班|彈性教學")
# characters of calendar words that are not given names: 開學，… / 過年去 / 補上課、上班 are not name tags
_NOT_A_TAG = set("學課考試假節年週期寒暑典註冊選測慶校")


def _staff_tag(summary: str) -> bool:
    m = _TAG_SEP.match(summary) or _TAG_ERRAND.match(summary)
    return m is not None and not any(c in _NOT_A_TAG for c in m.group("names"))


def classify(e: dict) -> Literal["keep", "drop", "review"]:
    """e is one item of events.list."""
    summary = e.get("summary") or ""
    s = f"{summary} {e.get('description') or ''}"
    if _NAME_PREFIX_LEAVE.search(s) and not _PUBLIC_HOLIDAY.search(s):
        return "drop"
    if _LEAVE.search(s) and not _ACADEMIC_EXC.search(s) and not _PUBLIC_HOLIDAY.search(s):
        return "drop"
    if _STAFF.search(s) or _MEETING.search(s):
        return "drop"
    if (_staff_tag(summary) or _ERRAND.search(s)) and not _CALENDAR.search(s):
        return "drop"
    if e.get("recurringEventId") and "dateTime" in (e.get("start") or {}):
        return "drop"  # timed weekly entries are staff schedules
    if not summary.strip():
        return "drop"
    if e.get("status") == "tentative":
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


def _day(when: dict, *, end: bool) -> date:
    """A start or end of events.list as a Taipei day. An all-day event's end.date is the day after its last day, which is
    exactly the exclusive bound of the half-open [from, to) the fact store uses: it is kept as is, and the last day
    shown to students (templates.span_zh) is end.date minus one. A timed end at midnight ends the day before; any other
    timed end counts its own day."""
    if "date" in when:
        return date.fromisoformat(when["date"])
    t = datetime.fromisoformat(when["dateTime"]).astimezone(TAIPEI)
    return t.date() + timedelta(days=1 if end and t.time() != time.min else 0)


def _span(e: dict) -> tuple[date, date]:
    d0 = _day(e["start"], end=False)
    d1 = _day(e["end"], end=True) if e.get("end") else d0 + timedelta(days=1)
    return d0, max(d1, d0 + timedelta(days=1))


def _body(snap: Snapshot) -> dict:
    return json.loads(snap.text if snap.text is not None else snap.body)


def _merged(pages: list[Snapshot]) -> Snapshot:
    """The pages of one events.list as one snapshot, so that parse() sees the whole window (the instances of a series
    can sit on two pages and share one iCalUID). Each page was fetched and archived on its own."""
    data = [_body(p) for p in pages]
    merged = {**data[0], "items": [e for d in data for e in d.get("items", [])]}
    merged.pop("nextPageToken", None)
    body = json.dumps(merged, ensure_ascii=False).encode()
    return replace(pages[0], body=body, text=body.decode(), sha256=hashlib.sha256(body).hexdigest())


def _seconds(stamp: str | None) -> str | None:
    """events.list `updated` (milliseconds, Z) in the ICS LAST-MODIFIED form stored before: whole seconds, +00:00."""
    return datetime.fromisoformat(stamp).astimezone(UTC).replace(microsecond=0).isoformat() if stamp else None


@adapter("aca_calendar_ics", hosts=["www.googleapis.com"])
class AcaCalendar(Adapter):
    pages_are_html: ClassVar[bool] = False

    def discover(self, ctx) -> list[Target]:
        lo, hi = ctx.today - WINDOW_BEFORE, ctx.today + WINDOW_AFTER
        q = urlencode({"singleEvents": "true", "orderBy": "startTime", "maxResults": MAX_RESULTS, "timeZone": "Asia/Taipei",
                       "timeMin": f"{lo.isoformat()}T00:00:00+08:00", "timeMax": f"{hi.isoformat()}T00:00:00+08:00"})
        return [Target(f"{API_URL}?{q}", credential="google_api_key")]

    def fetch_all(self, ctx) -> list[Snapshot]:
        """Every page of the window, merged into one snapshot. A page that does not answer 200 or is not JSON comes back
        alone, and parse() voids the run; still a next page after MAX_PAGES voids it too."""
        [first] = self.discover(ctx)
        pages: list[Snapshot] = []
        token = None
        while True:
            target = first if token is None else replace(first, url=f"{first.url}&{urlencode({'pageToken': token})}")
            snap = ctx.fetch(target)
            if snap.status != 200:
                return [snap]
            try:
                token = _body(snap).get("nextPageToken")
            except (ValueError, AttributeError):
                return [snap]
            pages.append(snap)
            if not token:
                return [_merged(pages)] if len(pages) > 1 else pages
            if len(pages) == MAX_PAGES:
                raise ParseError(f"events.list still had a next page after {MAX_PAGES} pages: the unread pages would "
                                 "look deleted")

    def counts_missing(self, key: str, kind: str, span: tuple[date, date] | None, records: list[Record],
                       today: date) -> bool:
        """Only events (and holiday facts) inside the window asked for count: one that ended more than WINDOW_BEFORE
        ago simply stopped being returned. A day of margin at each edge covers a fetch just after midnight."""
        if span is None:
            return True
        lo, hi = span
        return hi > today - WINDOW_BEFORE + timedelta(days=1) and lo < today + WINDOW_AFTER - timedelta(days=1)

    def parse(self, snap: Snapshot) -> list[Record]:
        """The events of the window around the fetch day (the API's timeMin/timeMax already ask for it; parse keeps
        the same window, so a replayed snapshot gives the same records)."""
        if snap.status != 200:
            return []
        try:
            data = json.loads(snap.text if snap.text is not None else snap.body)
            items = data["items"]
            anchor = snap.fetched_at.astimezone(TAIPEI).date()
            lo, hi = anchor - WINDOW_BEFORE, anchor + WINDOW_AFTER
            spans = [_span(e) for e in items]
            uids = [str(e["iCalUID"]) for e in items]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ParseError(f"Calendar API response not readable: {type(exc).__name__}") from exc
        if data.get("kind") != "calendar#events" or not isinstance(items, list):
            raise ParseError("not an events.list response")
        if data.get("nextPageToken"):
            raise ParseError("an events.list page with a nextPageToken: the unread pages would look deleted")
        if not items:
            raise ParseError("the calendar returned no events")
        src = SourceRef(PAGE_URL, TITLE, "nknu.org.aca", "ics", snap.sha256)
        occurrences = [(e, uid, span) for e, uid, span in zip(items, uids, spans, strict=True)
                       if span[1] > lo and span[0] < hi]
        kept = [(e, uid, span, c) for e, uid, span in occurrences if (c := classify(e)) != "drop"]
        # dropped entries are never stored, counted or logged
        uid_count: dict[str, int] = {}
        for _, uid, _, _ in kept:
            uid_count[uid] = uid_count.get(uid, 0) + 1
        out: list[Record] = []
        for e, uid, (d0, d1), c in kept:
            summary = str(e.get("summary")).strip()
            key = uid if uid_count[uid] == 1 else f"{uid}@{d0}"
            status = "tentative" if c == "review" else ("cancelled" if e.get("status") == "cancelled" else "confirmed")
            cat = category(summary)
            out.append(EventRecord(key, uid, summary, d0, d1, cat, status, int(e.get("sequence") or 0),
                                   _seconds(e.get("updated")), src))
            if cat == "holiday" and status == "confirmed":
                out.append(FactRecord(f"ics-holiday:{key}", "nknu", "holiday", {"label": summary[:60]}, d0, d1, "explicit",
                                      2, False, src, summary[:200], "ics"))
        return out
