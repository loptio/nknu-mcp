import json
import os
import re
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pytest

from nknu.server import query
from nknu.server.envelope import NOTICE_LABEL
from nknu.server.query import get_calendar, get_fact, get_hours, parse_date_arg, today_info
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import CONFLICT_DAY, GYM, NOW, WR, tpe

YWR, HLIB = "nknu.yanchao.gym.weight-room", "nknu.heping.library.main"


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
    f = e["facts"][0]
    assert f["valid"] == ["2026-09-07", "2027-01-08"] and f["source"]["tier"] == 1 and f["source"]["url"].startswith("https://")
    assert e["next_change"]["from"] == "2026-10-24" and "暫停開放" in e["next_change"]["answer"]


def test_untrimmed_fact_carries_rules_and_source(db, monkeypatch):
    monkeypatch.setattr(query, "fit_budget", lambda env: env)  # the budget may drop rules: the answer states them
    f = get_hours(db, WR, "2026-10-22", now=NOW)["facts"][0]
    assert {"dows": [4], "opens": "18:00", "closes": "20:00"} in f["rules"] and f["source"]["title"] == "場地開放時間"


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


def test_stale_envelope_has_no_hours_values(db):
    e = get_hours(db, WR, "2027-02-01", now=NOW)
    body = {k: v for k, v in e.items() if k != "today"}
    assert e["status"] == "stale" and not re.search(r"\b\d{2}:\d{2}\b", json.dumps(body, ensure_ascii=False))
    assert e["last_known"] == {"valid_to_inclusive": "2027-01-08", "source_url": "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"}
    assert "facts" not in e and e["found"] is False and e["caveat"]


def test_beyond_window_runtime_resolve(db):
    e = get_hours(db, WR, "2027-06-01", now=NOW)
    assert e["status"] in {"stale", "no_data"} and "2027-06-01" in e["answer"]


def test_ambiguous_place_lists_candidates(db):
    e = get_hours(db, "重訓室", "2026-10-22", now=NOW)
    assert e["match"] == "ambiguous" and {f["entity_id"] for f in e["facts"]} == {WR, YWR}
    assert all(f["answer"] for f in e["facts"]) and e["caveat"]


def test_days_list(db):
    e = get_hours(db, "和平重訓室", "2026-10-22", days=7, now=NOW)  # bare 重訓室 is ambiguous (Task 5 ruling)
    assert len(e["days"]) == 7 and [d["date"] for d in e["days"]][:3] == ["2026-10-22", "2026-10-23", "2026-10-24"]
    assert e["days"][2]["open"] is False


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


def test_recent_notices_during_pending(world_with_pending_closure):
    db = world_with_pending_closure
    e = get_hours(db, GYM, "2026-10-22", now=NOW)
    assert e["recent_notices"] and all(n["label"] == NOTICE_LABEL for n in e["recent_notices"])
    assert "9100" in e["recent_notices"][0]["url"]
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
    old = get_fact(db, lineage, include_history=True, now=NOW)["history"][0]["id"]
    e = get_fact(db, old, now=NOW)
    assert e["status"] == "stale" and e["caveat"] and "history" not in e


def test_get_fact_calendar_event(db):
    uid = get_calendar(db, "2026-10-01", "2026-11-30", keyword="期中", now=NOW)["facts"][0]["id"]
    e = get_fact(db, uid, now=NOW)
    assert e["found"] is True and "期中" in e["answer"]


def test_get_fact_unknown(db):
    e = get_fact(db, "no-such-id", now=NOW)
    assert e["found"] is False and e["status"] == "no_data" and "請勿推測" in e["caveat"]


def test_conflict_lists_alternatives(conflict_world):
    e = get_hours(conflict_world, WR, CONFLICT_DAY, now=NOW)
    assert e["status"] == "conflict" and len(e["alternatives"]) == 3 and "facts" not in e


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
