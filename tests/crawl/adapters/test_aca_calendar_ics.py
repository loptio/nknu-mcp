import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from nknu.core.pii import find_pii
from nknu.core.records import EventRecord, FactRecord, content_hash
from nknu.core.vocab import current_term
from nknu.core.writer import upsert_event
from nknu.crawl.adapter import ParseError, RunContext
from nknu.crawl.adapters.aca_calendar_ics import API_URL, MAX_PAGES, PAGE_URL, AcaCalendar, category, classify
from tests.crawl.adapters.helpers import FIXTURES, FakeClient, snap
from tests.factories import T0, make_db

A = AcaCalendar()
# events.list of the public calendar (singleEvents=true, orderBy=startTime), names masked with O. It holds events of
# several years; parse() keeps the window around the fetch day, as the API's timeMin/timeMax would.
RAW = (FIXTURES / "aca_calendar_ics" / "events.json").read_text()


def api_snap(text=RAW, fetched_at=None):
    s = snap(API_URL, text)
    s = replace(s, headers={"content-type": "application/json; charset=UTF-8"})
    return replace(s, fetched_at=fetched_at) if fetched_at else s


def with_items(mutate):
    data = json.loads(RAW)
    mutate(data)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def recs():
    return A.parse(api_snap())


def events(recs):
    return [r for r in recs if isinstance(r, EventRecord)]


def test_discover_asks_the_api_for_the_window_with_the_key_named_not_held():
    ctx = RunContext(client=None, full_scan=True, today=date(2026, 10, 24), conn=None, known_keys=set(), rate=None)
    [t] = A.discover(ctx)
    q = parse_qs(urlsplit(t.url).query)
    assert t.url.startswith(API_URL) and t.credential == "google_api_key" and "key" not in q
    assert (q["singleEvents"], q["orderBy"], q["maxResults"]) == (["true"], ["startTime"], ["2500"])
    assert (q["timeMin"], q["timeMax"]) == (["2026-08-25T00:00:00+08:00"], ["2027-11-28T00:00:00+08:00"])


def test_source_is_the_schools_calendar_page(recs):
    assert {r.source.url for r in recs} == {PAGE_URL} and {r.source.kind for r in recs} == {"ics"}


def test_staff_entries_dropped(recs):
    assert not any(re.search(r"休假|寒休|組長|會議", e.summary) for e in events(recs))


def test_academic_kept_zero_false_drops(recs):
    cats = {e.category for e in events(recs)}
    assert cats >= {"term_begin", "vacation_begin", "midterm_exam", "course_withdrawal", "holiday", "final_exam"}
    assert len(events(recs)) == 12  # every kept academic event in the 2026-08..2027-11 window


def test_keys_are_the_ics_uids(recs):
    # the API's iCalUID is the UID the ICS feed carried: records stored before the switch keep their keys
    e = next(e for e in events(recs) if e.category == "course_withdrawal")
    assert e.key == e.uid == "10kt6s1i12424pqmif1bkmifcm@google.com"


def test_midterm_grades_not_midterm(recs):
    e = next(e for e in events(recs) if "送交期中考試成績" in e.summary)
    assert e.category == "other"


def test_holiday_fact_on_university(recs):
    h = [r for r in recs if isinstance(r, FactRecord)]
    assert {f.valid_from for f in h} == {date(2026, 10, 9), date(2026, 10, 24)}
    assert all(f.entity_id == "nknu" and f.attribute == "holiday" and not f.propagates and f.source.kind == "ics" for f in h)


def test_all_day_end_date_is_the_day_after_the_last_day(recs):
    # events.list gives 申辦停修 start.date 2026-11-03, end.date 2026-11-17: the last day is 11-16, and the half-open
    # span the store keeps is [11-03, 11-17)
    e = next(e for e in events(recs) if e.category == "course_withdrawal")
    assert (e.during_from, e.during_to) == (date(2026, 11, 3), date(2026, 11, 17))
    e = next(e for e in events(recs) if e.category == "vacation_begin")
    assert (e.during_from, e.during_to) == (date(2027, 1, 11), date(2027, 1, 12))  # one day


def test_timed_event_spans_its_taipei_day():
    def timed(data):
        data["items"] = [{"kind": "calendar#event", "iCalUID": "0timed0event0000000000000@google.com", "status": "confirmed", "summary": "新生座談會",
                          "start": {"dateTime": "2026-11-05T23:30:00Z"}, "end": {"dateTime": "2026-11-06T01:00:00Z"}}]
    [e] = events(A.parse(api_snap(with_items(timed))))
    assert (e.during_from, e.during_to) == (date(2026, 11, 6), date(2026, 11, 7))  # 07:30–09:00 Taipei on 11-06


def test_updated_kept_in_the_old_last_modified_form(recs):
    e = next(e for e in events(recs) if e.category == "course_withdrawal")
    assert e.last_modified == "2026-05-04T06:36:38+00:00"


def test_tentative_status():
    old = A.parse(api_snap(fetched_at=datetime(2017, 10, 1, tzinfo=UTC)))
    assert any(e.status == "tentative" and "雙十連假" in e.summary for e in events(old))


def test_timed_weekly_recurring_dropped():
    old = A.parse(api_snap(fetched_at=datetime(2017, 5, 1, tzinfo=UTC)))
    assert not any("上課" in e.summary for e in events(old))
    weekly = {"recurringEventId": "w", "summary": "英文會話", "start": {"dateTime": "2026-11-03T08:00:00+08:00"}}
    assert classify(weekly) == "drop"
    assert classify({**weekly, "start": {"date": "2026-11-03"}}) == "keep"  # an all-day series is the calendar's own


def test_category_rules():
    assert category("註冊及全校上課開始Classes begin") == "term_begin"
    assert category("寒假開始Winter vacation begins") == "vacation_begin"
    assert category("2/28 和平紀念日，3/1 補假") == "holiday"
    assert category("暑假結束") == "other"


@pytest.mark.parametrize("text", [(FIXTURES / "aca_calendar_ics" / "broken.json").read_text(),
                                  "<html><body>Service Unavailable</body></html>",
                                  json.dumps({"kind": "calendar#events", "items": []}),
                                  json.dumps({"kind": "calendar#calendarList", "items": []}),
                                  json.dumps({"kind": "calendar#events", "items": [{"summary": "缺少 iCalUID"}]})])
def test_unreadable_or_empty_response_raises_parse_error(text):
    with pytest.raises(ParseError):
        A.parse(api_snap(text))


def test_a_page_that_still_has_a_next_page_voids_the_run():
    # parse() only ever sees the merged pages; a lone page with a nextPageToken means pages were not read, and their
    # events would look deleted: the run is void and the old values stay
    with pytest.raises(ParseError, match="nextPageToken"):
        A.parse(api_snap(with_items(lambda d: d.update(nextPageToken="abc"))))


def ctx_for(route, today=date(2026, 10, 22)):
    return RunContext(client=FakeClient(route), full_scan=True, today=today, conn=None, known_keys=set(), rate=None)


def paged(sizes, tokens):
    """events.json split into pages of these sizes (0 = an empty page), each with its nextPageToken (None on the last)."""
    data = json.loads(RAW)
    items, pages, at = data["items"], {}, 0
    for i, (n, tok) in enumerate(zip(sizes, tokens, strict=True)):
        page = {**data, "items": items[at:at + n]}
        at += n
        if tok:
            page["nextPageToken"] = tok
        pages[None if i == 0 else tokens[i - 1]] = json.dumps(page, ensure_ascii=False)
    assert at == len(items)
    return pages


def route_pages(pages):
    def route(target):
        q = parse_qs(urlsplit(target.url).query)
        assert target.credential == "google_api_key"
        return pages[q.get("pageToken", [None])[0]]
    return route


def test_pages_are_followed_and_merged_into_one_snapshot():
    # Google may cut a window far below maxResults into pages, some of them empty: only nextPageToken says more remain
    n = len(json.loads(RAW)["items"])
    ctx = ctx_for(route_pages(paged([10, 0, n - 10], ["p2", "p3", None])))
    snaps = A.fetch_all(ctx)
    assert len(ctx.client.requests) == 3 and len(snaps) == 1
    assert [parse_qs(urlsplit(t.url).query).get("pageToken") for t in ctx.client.requests] == [None, ["p2"], ["p3"]]
    merged = replace(snaps[0], fetched_at=api_snap().fetched_at)
    assert [content_hash(r) for r in A.parse(merged)] == [content_hash(r) for r in A.parse(api_snap())]  # sha aside


def test_one_page_is_not_rewritten():
    ctx = ctx_for(lambda target: RAW)
    [s] = A.fetch_all(ctx)
    assert s.text == RAW and len(ctx.client.requests) == 1


def test_a_pager_that_never_ends_voids_the_run():
    page = with_items(lambda d: d.update(nextPageToken="again"))
    ctx = ctx_for(lambda target: page)
    with pytest.raises(ParseError, match="next page"):
        A.fetch_all(ctx)
    assert len(ctx.client.requests) == MAX_PAGES


def test_a_broken_later_page_voids_the_run():
    pages = paged([10, len(json.loads(RAW)["items"]) - 10], ["p2", None])
    pages["p2"] = "<html>Backend Error</html>"
    [s] = A.fetch_all(ctx_for(route_pages(pages)))
    with pytest.raises(ParseError):
        A.parse(s)


def test_repeated_instances_are_keyed_by_their_day():
    # the instances of an all-day series share one iCalUID: each is keyed uid@day, as in the ICS era
    def series(data):
        inst = {"kind": "calendar#event", "iCalUID": "0weekly0series00000000000@google.com", "status": "confirmed",
                "summary": "期中考試週", "recurringEventId": "0weekly0series00000000000"}
        data["items"] = [{**inst, "start": {"date": d0}, "end": {"date": d1}}
                         for d0, d1 in (("2026-11-03", "2026-11-04"), ("2026-11-10", "2026-11-11"))]
    keys = {e.key for e in events(A.parse(api_snap(with_items(series))))}
    assert keys == {"0weekly0series00000000000@google.com@2026-11-03", "0weekly0series00000000000@google.com@2026-11-10"}

    def single(data):
        series(data)
        data["items"] = data["items"][:1]
    [e] = events(A.parse(api_snap(with_items(single))))
    assert e.key == "0weekly0series00000000000@google.com"


def test_term_dates_feed_current_term(tmp_path, recs):
    c = make_db(tmp_path, calendar=False)
    for e in events(recs):
        upsert_event(c, e, adapter="aca_calendar_ics", now=T0)
    assert current_term(c, date(2026, 10, 22)) == (date(2026, 9, 7), date(2027, 1, 11))


def test_parse_is_pure():
    assert A.parse(api_snap()) == A.parse(api_snap())


def test_fixture_only_masked_names():
    assert all("O" in hit for hit in find_pii(RAW))


def test_absence_counts_only_inside_the_parse_window():
    # C2: an event that ended more than WINDOW_BEFORE ago is no longer returned; its absence is not a deletion
    today = date(2026, 10, 22)
    assert A.counts_missing("uid", "event", (date(2026, 10, 1), date(2026, 10, 2)), [], today)
    assert A.counts_missing("ics-holiday:uid", "fact", (date(2026, 10, 24), date(2026, 10, 27)), [], today)
    assert not A.counts_missing("uid", "event", (date(2026, 8, 1), date(2026, 8, 2)), [], today)
    assert not A.counts_missing("uid", "event", (date(2028, 1, 1), date(2028, 1, 2)), [], today)
    assert A.counts_missing("uid", "event", None, [], today)


def item(summary, description=None):
    e = {"summary": summary, "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}, "status": "confirmed"}
    return {**e, "description": description} if description else e


# F-ics-staff-filter: shapes of the staff entries the public calendar carried in 2017-18 (names masked with O), which
# the leave/meeting vocabulary let through; there is no review any more, so the adapter drops them
@pytest.mark.parametrize("summary", ["林O去玩", "林O，王O，陳O去爬玉山", "王O教務處餐敘 18:00", "組內聚餐 18:00", "林O休",
                                     "王O，休大假", "林O，看牙", "週五統一排休", "陳O。公差(某大學)", "林O出國", "王O看牙",
                                     "林O、陳O，評鑑中心訪視"])
def test_name_led_personal_entries_dropped(summary):
    assert classify(item(summary)) == "drop"


def test_description_counts_for_the_filter():
    assert classify(item("行程", "林O請假")) == "drop"


# real academic entries of the same calendar (2017-2027) that start like a name or carry a personal-looking word
@pytest.mark.parametrize("summary", ["開學，開始上課 Classes begin", "補上課、上班(補10月2日) Make-up work/class date",
                                     "過年去 China New Year", "辦理休/退學退費2/3期限截止", "萬安40號防空演習", "補上班",
                                     "英聽考試", "學測", "美術術科", "放暑假了！", "期末考試，若教師另訂其他日期者，從其規定",
                                     "申辦棄選、研究生申報指導教授", "暑假開始、大學部學生宿舍閉舍", "颱風，停止上課",
                                     "元旦，放假", "大學部各學系新生、轉學生報到暨親師座談會", "學生出國交換申請截止",
                                     "恭喜發財過新年Chinese New Year", "課表公告", "畢業典禮"])
def test_academic_entries_kept(summary):
    assert classify(item(summary)) == "keep"
