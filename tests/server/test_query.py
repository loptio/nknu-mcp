import os
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pytest

from nknu.server.envelope import NOTICE_LABEL
from nknu.server.query import CAVEATS, get_calendar, get_fact, get_hours, parse_date_arg, today_info
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import CONFLICT_DAY, GYM, MIDTERM_UID, NOW, WITHDRAWAL_UID, WR, tpe

YWR, HLIB, YLIB = "nknu.yanchao.gym.weight-room", "nknu.heping.library.main", "nknu.yanchao.library.main"


@pytest.fixture
def db(world):
    return ServeDB(world[1])


def test_today_uses_taipei_date(db):
    assert get_hours(db, "重訓室", now=datetime(2026, 10, 22, 16, 30, tzinfo=UTC))["today"]["date"] == "2026-10-23"


def test_today_info():
    assert today_info(NOW) == {"date": "2026-10-22", "weekday": "週四", "time": "14:05"}
    assert today_info(datetime(2026, 10, 22, 16, 30, tzinfo=UTC)) == {"date": "2026-10-23", "weekday": "週五", "time": "00:30"}


def test_hours_current(db):
    e = get_hours(db, WR, "2026-10-22", now=NOW)
    assert e["status"] == "current" and e["match"] == "entity" and "來源" in e["answer"]
    assert e["found"] is True and e["kb"] == db.kb and "週四 18:00–20:00" in e["answer"]
    assert e["facts"][0]["id"] and e["facts"][0]["source"]["url"].startswith("https://")
    assert e["next_change"]["from"] == "2026-10-24" and "暫停開放" in e["next_change"]["answer"]


def test_fact_carries_rules_validity_and_source(db):
    """U1: nothing is trimmed, so the fact keeps what its answer sentence also states (rules, validity, source)."""
    f = get_hours(db, WR, "2026-10-22", now=NOW)["facts"][0]
    assert {"dows": [4], "opens": "18:00", "closes": "20:00"} in f["rules"] and f["source"]["title"] == "場地開放時間"
    assert f["valid"] == ["2026-09-07", "2027-01-08"] and f["source"]["tier"] == 1 and f["source"]["url"].startswith("https://")


def test_closure_inherited(db):
    e = get_hours(db, WR, "2026-10-25", now=NOW)
    assert e["facts"][0]["rules"] == [] and "暫停開放" in e["answer"]


def test_school_holiday_is_not_library_closure(db):
    e = get_hours(db, HLIB, "2026-10-24", now=NOW)  # the calendar says holiday; the library extends its hours
    assert e["status"] == "current" and "延長開館" in e["answer"] and "08:15–21:00" in e["answer"]


def test_open_now_cross_midnight(cross_midnight_world):
    db = cross_midnight_world  # test-only entity; Fridays 22:00–02:00, asked Saturday 00:30
    assert get_hours(db, "nknu.heping.gym.night-test", now=tpe(2026, 10, 24, 0, 30))["open_now"] is True
    assert get_hours(db, "nknu.heping.gym.night-test", now=tpe(2026, 10, 24, 2, 30))["open_now"] is False


def test_open_now_only_for_today(db):
    assert get_hours(db, WR, "2026-10-22", now=tpe(2026, 10, 22, 19, 30))["open_now"] is True
    assert get_hours(db, WR, "2026-10-22", now=tpe(2026, 10, 22, 21, 30))["open_now"] is False
    assert "open_now" not in get_hours(db, WR, "2026-10-23", now=tpe(2026, 10, 22, 19, 30))


PE_TABLE_URL = "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"


def test_stale_envelope_gives_the_last_known_hours_with_dates(db):
    """U3: no current data -> the last known values, their validity and source, and 可能已變動; status stays stale."""
    e = get_hours(db, WR, "2027-02-01", now=NOW)
    assert e["status"] == "stale" and e["found"] is False and "facts" not in e and "open_now" not in e
    a = e["answer"]
    assert "最近一筆官方資料" in a and "體育室《場地開放時間》" in a and "2026-09-07～2027-01-08" in a
    assert "週一、二、三、五 19:00–21:00" in a and "週四 18:00–20:00" in a and "可能已變動" in a and "可洽體育室" in a
    lk = e["last_known"]
    assert lk["valid"] == ["2026-09-07", "2027-01-08"] and lk["source"]["url"] == PE_TABLE_URL and lk["id"]
    assert {"dows": [4], "opens": "18:00", "closes": "20:00"} in lk["rules"] and lk["source"]["title"] == "場地開放時間"
    assert e["caveat"] == CAVEATS["stale"] and "可能已變動" in e["caveat"]


def test_beyond_window_runtime_resolve(db):
    e = get_hours(db, WR, "2027-06-01", now=NOW)  # past the card window (2027-04-20): resolve() at query time
    assert e["status"] == "stale" and "next_change" not in e and e["last_known"]["valid"][1] == "2027-01-08"
    assert "2027-01-09 起的時段尚未公布，可能已變動" in e["answer"]


def test_ambiguous_place_lists_candidates(db):
    e = get_hours(db, "重訓室", "2026-10-22", now=NOW)
    assert e["match"] == "ambiguous" and {f["entity_id"] for f in e["facts"]} == {WR, YWR}
    assert all(f["answer"] for f in e["facts"]) and e["caveat"] and all("days" not in f for f in e["facts"])


def test_ambiguous_candidates_carry_days_and_full_next_change(db):
    """F-ambiguous-ignores-days: no campus named, days=3 from 10/24 -> each library lists its 3 days (both are closed
    10/25-26) and its next change as a whole sentence, 附註 and 來源 included."""
    e = get_hours(db, "圖書館", "2026-10-24", days=3, now=NOW)
    assert e["match"] == "ambiguous" and [f["entity_id"] for f in e["facts"]] == [HLIB, YLIB]
    for f in e["facts"]:
        days = f["days"]
        assert [d["date"] for d in days] == ["2026-10-24", "2026-10-25", "2026-10-26"] and days[0]["open"] is True
        assert all(d["open"] is False and "臺灣光復" in d["reason"] for d in days[1:])
        assert f["next_change"]["from"] == "2026-10-25" and "暫停開放" in f["next_change"]["answer"]
        assert "來源：圖書資訊處《詳細開館時間》" in f["next_change"]["answer"] and "延長開館" in f["answer"]


def test_days_list_has_one_full_entry_per_day(db):
    e = get_hours(db, "和平重訓室", "2026-10-22", days=7, now=NOW)  # bare 重訓室 is ambiguous (Task 5 ruling)
    assert [d["date"] for d in e["days"]] == [f"2026-10-{d}" for d in range(22, 29)]
    assert e["days"][0] == {"date": "2026-10-22", "weekday": "週四", "status": "current", "open": True,
                            "segments": ["18:00–20:00"]}
    closed = {"weekday": "週六", "status": "current", "open": False, "reason": "臺灣光復紀念日連假"}
    assert e["days"][2] == {"date": "2026-10-24", **closed}  # no merged runs, no dropped reasons
    assert PE_NOTE in e["answer"] and "暫停開放" in e["next_change"]["answer"]


def test_days_out_of_range(db):
    with pytest.raises(ValueError):
        get_hours(db, "重訓室", days=15)
    with pytest.raises(ValueError):
        get_hours(db, "重訓室", days=0)


def test_roc_date_arg():
    assert parse_date_arg("115.10.24") == date(2026, 10, 24)
    assert parse_date_arg("115-10-24") == parse_date_arg("115/10/24") == parse_date_arg("2026-10-24") == date(2026, 10, 24)
    for bad in ("2026/10/24", "10/24", "tomorrow", "115.13.01", "2026-10-24T00:00"):
        with pytest.raises(ValueError):
            parse_date_arg(bad)


def test_bad_date_arg_raises(db):
    with pytest.raises(ValueError):
        get_hours(db, WR, "下週一")


def test_bad_date_arg_echo_is_cut():
    with pytest.raises(ValueError, match="「2026-13-45」"):
        parse_date_arg("2026-13-45")
    with pytest.raises(ValueError) as long:
        parse_date_arg("下週一" * 200)
    assert "下週一下週一下週一下週一" not in str(long.value) and len(str(long.value)) < 60


def test_recent_notices_during_pending(world_with_pending_closure):
    db = world_with_pending_closure
    e = get_hours(db, GYM, "2026-10-22", now=NOW)
    assert e["recent_notices"] and all(n["label"] == NOTICE_LABEL for n in e["recent_notices"])
    assert [n["url"][-4:] for n in e["recent_notices"]] == ["9100", "9103", "9102", "9101"]  # all of them, newest first
    assert "暫停開放" not in get_hours(db, GYM, "2026-11-07", now=NOW)["answer"]  # still pending: not a fact


def test_recent_notices_only_from_managing_unit_and_recent(world_with_pending_closure):
    db = world_with_pending_closure
    assert "recent_notices" not in get_hours(db, GYM, "2026-10-22", now=tpe(2026, 11, 10, 9, 0))  # > 14 days old
    assert "recent_notices" not in get_hours(db, HLIB, "2026-10-22", now=NOW)  # 圖資處 manages the library


def test_no_data_caveat(db):
    e = get_hours(db, "不存在的館", now=NOW)
    assert e["found"] is False and "請勿推測" in e["caveat"] and e["status"] == "no_data"
    assert 0 < len(e["similar"]) <= 3


def test_known_place_without_hours_is_no_data(db):
    e = get_hours(db, "nknu.heping", "2026-10-22", now=NOW)
    assert e["status"] == "no_data" and e["found"] is False and "請勿推測" in e["answer"]


def test_calendar_range_keyword(db):
    e = get_calendar(db, "2026-10-01", "2026-11-30", keyword="期中", now=NOW)
    assert e["facts"] and all("期中" in f["summary"] for f in e["facts"])
    assert e["facts"][0]["valid"] == ["2026-10-27", "2026-10-30"] and "來源" in e["answer"]


def test_tentative_event_is_labelled(world_calendar_status):
    # written without review since U2: the answer must not present a tentative date as settled
    db = world_calendar_status
    e = get_calendar(db, "2026-10-01", "2026-11-30", keyword="期中", now=NOW)
    midterm = next(f for f in e["facts"] if f["id"] == MIDTERM_UID)
    assert midterm["status"] == "tentative" and "暫定" in e["caveat"]
    one = get_fact(db, MIDTERM_UID, now=NOW)
    assert "（暫定" in one["answer"] and one["facts"][0]["status"] == "tentative" and "暫定" in one["caveat"]


def test_cancelled_event_is_not_listed(world_calendar_status, db):
    assert any(f["id"] == WITHDRAWAL_UID for f in get_calendar(db, "2026-11-01", "2026-11-30", keyword="停修",
                                                               now=NOW)["facts"])
    e = get_calendar(world_calendar_status, "2026-11-01", "2026-11-30", keyword="停修", now=NOW)
    assert e["found"] is False and not e.get("facts")
    assert get_fact(world_calendar_status, WITHDRAWAL_UID, now=NOW)["found"] is False


def test_confirmed_events_carry_no_tentative_caveat(db):
    e = get_calendar(db, "2026-10-01", "2026-11-30", keyword="期中", now=NOW)
    assert "暫定" not in (e.get("caveat") or "") and all("status" not in f for f in e["facts"])


def test_calendar_english_keyword_uses_the_aliases(db):
    """F-calendar-en-keyword: midterm finds 期中考試Mid-term exam like mid-term and 期中 do."""
    e = get_calendar(db, "2026-10-01", "2026-11-30", keyword="midterm", now=NOW)
    assert e["found"] is True and any(f["id"] == MIDTERM_UID for f in e["facts"]) and "midterm" in e["answer"]
    assert [f["id"] for f in e["facts"]] == [f["id"] for f in get_calendar(db, "2026-10-01", "2026-11-30",
                                                                            keyword="期中", now=NOW)["facts"]]


def test_calendar_no_match(db):
    e = get_calendar(db, "2026-10-01", "2026-11-30", keyword="不存在的活動", now=NOW)
    assert e["found"] is False and e["status"] == "no_data" and "請勿推測" in e["caveat"]


def test_calendar_bad_range(db):
    with pytest.raises(ValueError):
        get_calendar(db, "2026-11-30", "2026-10-01")
    with pytest.raises(ValueError):
        get_calendar(db, limit=0)


def test_get_fact_history(world_superseded):
    db, lineage = world_superseded
    e = get_fact(db, lineage, include_history=True, now=NOW)
    assert len(e["history"]) == 2 and e["status"] == "current"
    assert "10:00–12:00" in e["answer"]


def test_get_fact_by_version_id(world_superseded):
    db, lineage = world_superseded
    versions = get_fact(db, lineage, include_history=True, now=NOW)["history"]
    e = get_fact(db, versions[0]["id"], now=NOW)
    assert e["status"] == "stale" and e["caveat"] and "history" not in e
    f = e["facts"][0]  # U3: an old version keeps its values; the answer and caveat say it is not current
    assert f["rules"] == [{"dows": [6], "opens": "09:00", "closes": "12:00"}] and f["superseded_by"] == versions[1]["id"]
    assert "已被新版本取代" in e["answer"]


def test_get_fact_names_the_fact_that_displaced_it(world_displaced):
    """C1 closes a fact of another lineage (the closure moved to 10/24): get_fact points at the replacement; a fact
    withdrawn by nknu undo has none and says it was taken down."""
    db, ids = world_displaced
    e = get_fact(db, ids["replaced"], now=NOW)
    assert e["status"] == "stale" and e["facts"][0]["superseded_by"] == ids["replacement"] and "已被新版本取代" in e["answer"]
    e = get_fact(db, ids["withdrawn"], now=NOW)
    assert e["status"] == "stale" and "superseded_by" not in e["facts"][0] and "已下架" in e["answer"]


def test_get_fact_calendar_event(db):
    uid = get_calendar(db, "2026-10-01", "2026-11-30", keyword="期中", now=NOW)["facts"][0]["id"]
    e = get_fact(db, uid, now=NOW)
    assert e["found"] is True and "期中" in e["answer"]


def test_get_fact_unknown(db):
    e = get_fact(db, "no-such-id", now=NOW)
    assert e["found"] is False and e["status"] == "no_data" and "請勿推測" in e["caveat"]


def test_conflict_lists_every_alternative(conflict_world):
    e = get_hours(conflict_world, WR, CONFLICT_DAY, now=NOW)
    assert e["status"] == "conflict" and len(e["alternatives"]) == 4 and "facts" not in e
    assert all(a["id"] and a["valid"] and a["source"]["tier"] for a in e["alternatives"])


def test_serve_db_reopened_after_atomic_swap(world, tmp_path):
    path = tmp_path / "serve.db"
    shutil.copy(world[1], path)
    db = ServeDB(path)
    first = db.meta()["build_id"]
    tmp = tmp_path / "serve.db.tmp"
    shutil.copy(world[1], tmp)
    c = sqlite3.connect(tmp)
    c.execute("UPDATE meta SET value='swapped' WHERE key='build_id'")
    c.commit()
    c.close()
    os.replace(tmp, path)
    assert first != "swapped" and db.meta()["build_id"] == "swapped"


def test_serve_db_is_read_only(db):
    with pytest.raises(sqlite3.OperationalError):
        db.conn().execute("DELETE FROM meta")


def test_serve_db_connection_per_thread(db):
    """FastMCP runs sync tools in worker threads; a sqlite3 connection must never be shared between them."""
    got: dict[int, list] = {}

    def grab(i):
        got[i] = [db.conn(), db.conn()]

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got[0][0] is got[0][1] and got[1][0] is got[1][1]  # reused inside a thread
    assert got[0][0] is not got[1][0] and db.conn() is not got[0][0]


def test_concurrent_queries_match_single_threaded(db):
    places = [WR, YWR, HLIB]
    expected = {p: get_hours(db, p, "2026-10-22", now=NOW) for p in places}

    def ask(i):
        p = places[i % len(places)]
        return p, get_hours(db, p, "2026-10-22", now=NOW)

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(ask, range(600)))
    assert [p for p, e in results if e != expected[p]] == []


PE_NOTE = "附註：1.國定及連續假日、運動會停課、招生考試,各運動場地不予開放。"


@pytest.mark.parametrize("place", [WR, GYM, "nknu.heping.gym.outdoor"])
@pytest.mark.parametrize("day", ["2026-10-22", "2026-11-06"])  # 11-06: 校慶運動會停課; the note is the only signal
def test_answer_keeps_its_notes(db, place, day):
    assert PE_NOTE in get_hours(db, place, day, days=14, now=NOW)["answer"]


def test_one_day_answers_keep_their_notes(db):
    """Every answer whose card carries 附註 is served with it, whatever else the envelope holds (U1: no trimming)."""
    conn = db.conn()
    lost = []
    for place, lo, card in conn.execute("SELECT entity_id, valid_from, answer_zh FROM answer_card WHERE intent='hours' "
                                        "AND answer_zh LIKE '%附註：%' ORDER BY entity_id, valid_from"):
        if lo >= "2026-10-15":
            e = get_hours(db, place, lo, days=14, now=NOW)
            if e["answer"] != card:
                lost.append((place, lo))
    assert lost == []


@pytest.mark.parametrize("days", [1, 7, 14])
def test_conflict_with_days_keeps_every_day_and_alternative(conflict_world, days):
    e = get_hours(conflict_world, WR, CONFLICT_DAY, days=days, now=NOW)
    assert e["status"] == "conflict" and e["caveat"] and len(e["alternatives"]) == 4 and "more" not in e
    if days > 1:
        assert len(e["days"]) == days and "more_days" not in e
        assert e["days"][0] == {"date": CONFLICT_DAY, "weekday": "週六", "status": "conflict"}


def test_stale_notice_titles_are_verbatim(world_next_term):
    """U3 lifts the old rule that a stale envelope carries no clock time anywhere: notice titles are the school's text."""
    e = get_hours(world_next_term, WR, "2027-02-01", now=NOW)
    assert e["status"] == "stale" and "9300" in e["recent_notices"][0]["url"]
    assert e["recent_notices"][0]["title"] == "測試公告：重量訓練室11月14日上午09:00至12:00開放"


def test_current_notice_titles_keep_their_times(world_next_term):
    e = get_hours(world_next_term, WR, "2026-10-22", now=NOW)
    assert e["status"] == "current" and "09:00" in e["recent_notices"][0]["title"]


def test_stale_days_list_says_stale_and_next_change_is_a_sentence(world_next_term):
    e = get_hours(world_next_term, WR, "2027-02-15", days=14, now=NOW)
    assert e["status"] == "stale" and len(e["days"]) == 14 and "19:00–21:00" in e["answer"]  # the last known table
    assert e["next_change"]["from"] == "2027-02-22" and "19:00–21:00" in e["next_change"]["answer"]
    stale = [d for d in e["days"] if d["status"] == "stale"]
    assert [d["date"] for d in stale] == [f"2027-02-{d}" for d in range(15, 22)]
    assert all(set(d) == {"date", "weekday", "status"} for d in stale)  # a day with no current data has no hours
    assert e["days"][7] == {"date": "2027-02-22", "weekday": "週一", "status": "current", "open": True,
                            "segments": ["19:00–21:00"]}
