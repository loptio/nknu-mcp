import re
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from nknu.core.pii import find_pii
from nknu.core.records import EventRecord, FactRecord
from nknu.core.vocab import current_term
from nknu.core.writer import upsert_event
from nknu.crawl.adapter import ParseError
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs, category
from tests.crawl.adapters.helpers import FIXTURES, snap
from tests.factories import T0, make_db

A = AcaCalendarIcs()
RAW = (FIXTURES / "aca_calendar_ics" / "calendar.ics").read_bytes()


def ics_snap(body=RAW, fetched_at=None):
    s = snap(ICS_URL, body.decode(errors="ignore"), body=body)
    return replace(s, fetched_at=fetched_at) if fetched_at else s


@pytest.fixture
def recs():
    return A.parse(ics_snap())


def events(recs):
    return [r for r in recs if isinstance(r, EventRecord)]


def test_staff_entries_dropped(recs):
    assert not any(re.search(r"休假|寒休|組長|會議", e.summary) for e in events(recs))


def test_academic_kept_zero_false_drops(recs):
    cats = {e.category for e in events(recs)}
    assert cats >= {"term_begin", "vacation_begin", "midterm_exam", "course_withdrawal", "holiday", "final_exam"}
    assert len(events(recs)) == 12  # every kept academic event in the 2026-08..2027-11 window


def test_midterm_grades_not_midterm(recs):
    e = next(e for e in events(recs) if "送交期中考試成績" in e.summary)
    assert e.category == "other"


def test_holiday_fact_on_university(recs):
    h = [r for r in recs if isinstance(r, FactRecord)]
    assert {f.valid_from for f in h} == {date(2026, 10, 9), date(2026, 10, 24)}
    assert all(f.entity_id == "nknu" and f.attribute == "holiday" and not f.propagates and f.source.kind == "ics" for f in h)


def test_dates_half_open(recs):
    e = next(e for e in events(recs) if e.category == "vacation_begin")
    assert (e.during_from, e.during_to) == (date(2027, 1, 11), date(2027, 1, 12))


def test_tentative_status():
    old = A.parse(ics_snap(fetched_at=datetime(2017, 10, 1, tzinfo=UTC)))
    assert any(e.status == "tentative" and "雙十連假" in e.summary for e in events(old))


def test_timed_weekly_recurring_dropped():
    old = A.parse(ics_snap(fetched_at=datetime(2017, 5, 1, tzinfo=UTC)))
    assert not any("上課" in e.summary for e in events(old))


def test_category_rules():
    assert category("註冊及全校上課開始Classes begin") == "term_begin"
    assert category("寒假開始Winter vacation begins") == "vacation_begin"
    assert category("2/28 和平紀念日，3/1 補假") == "holiday"
    assert category("暑假結束") == "other"


def test_broken_ics_raises_parse_error():
    with pytest.raises(ParseError):
        A.parse(ics_snap((FIXTURES / "aca_calendar_ics" / "broken.ics").read_bytes()))


def test_term_dates_feed_current_term(tmp_path, recs):
    c = make_db(tmp_path, calendar=False)
    for e in events(recs):
        upsert_event(c, e, adapter="aca_calendar_ics", now=T0)
    assert current_term(c, date(2026, 10, 22)) == (date(2026, 9, 7), date(2027, 1, 11))


def test_parse_is_pure():
    assert A.parse(ics_snap()) == A.parse(ics_snap())


def test_fixture_only_masked_names():
    assert all("O" in hit for hit in find_pii(RAW.decode()))


def test_absence_counts_only_inside_the_parse_window():
    # C2: an event that ended more than WINDOW_BEFORE ago is no longer parsed; its absence is not a deletion
    today = date(2026, 10, 22)
    assert A.counts_missing("uid", "event", (date(2026, 10, 1), date(2026, 10, 2)), [], today)
    assert A.counts_missing("ics-holiday:uid", "fact", (date(2026, 10, 24), date(2026, 10, 27)), [], today)
    assert not A.counts_missing("uid", "event", (date(2026, 8, 1), date(2026, 8, 2)), [], today)
    assert not A.counts_missing("uid", "event", (date(2028, 1, 1), date(2028, 1, 2)), [], today)
    assert A.counts_missing("uid", "event", None, [], today)
