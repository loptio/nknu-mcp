import re
from dataclasses import replace
from datetime import UTC, date, datetime

import icalendar
import pytest

from nknu.core.pii import find_pii
from nknu.core.records import EventRecord, FactRecord
from nknu.core.vocab import current_term
from nknu.core.writer import upsert_event
from nknu.crawl.adapter import ParseError
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs, category, classify
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


def vevent(summary, description=None):
    e = icalendar.Event()
    e.add("SUMMARY", summary)
    e.add("DTSTART", date(2026, 11, 3))
    if description:
        e.add("DESCRIPTION", description)
    return e


# F-ics-staff-filter: shapes of the staff entries the public calendar carried in 2017-18 (names masked with O), which
# the leave/meeting vocabulary let through; there is no review any more, so the adapter drops them
@pytest.mark.parametrize("summary", ["林O去玩", "林O，王O，陳O去爬玉山", "王O教務處餐敘 18:00", "組內聚餐 18:00", "林O休",
                                     "王O，休大假", "林O，看牙", "週五統一排休", "陳O。公差(某大學)", "林O出國", "王O看牙",
                                     "林O、陳O，評鑑中心訪視"])
def test_name_led_personal_entries_dropped(summary):
    assert classify(vevent(summary)) == "drop"


# real academic entries of the same calendar (2017-2027) that start like a name or carry a personal-looking word
@pytest.mark.parametrize("summary", ["開學，開始上課 Classes begin", "補上課、上班(補10月2日) Make-up work/class date",
                                     "過年去 China New Year", "辦理休/退學退費2/3期限截止", "萬安40號防空演習", "補上班",
                                     "英聽考試", "學測", "美術術科", "放暑假了！", "期末考試，若教師另訂其他日期者，從其規定",
                                     "申辦棄選、研究生申報指導教授", "暑假開始、大學部學生宿舍閉舍", "颱風，停止上課",
                                     "元旦，放假", "大學部各學系新生、轉學生報到暨親師座談會", "學生出國交換申請截止",
                                     "恭喜發財過新年Chinese New Year", "課表公告", "畢業典禮"])
def test_academic_entries_kept(summary):
    assert classify(vevent(summary)) == "keep"
