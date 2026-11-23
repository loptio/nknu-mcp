"""每晚紀錄 (nknu.ops.history; panels v3, spec docs/superpowers/specs/2026-11-17-panels-v3-questions.md 一): the store's
schema, an upsert that writes a night once however often it is put, what a later reading may and may not replace,
the backfill on the first look, and a store that cannot be written. Made-up night files in the shapes the crawler's
nightly script and the nightly ingest write (tests.ops.test_nightly's helpers), made-up sites: no real data."""

from __future__ import annotations

import os
import sqlite3
from datetime import date

import pytest

from nknu.ops import history, nightly
from nknu.ops.pipeline import PIPE, Where
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, library, run


class Fixed:
    """A stand-in for the app's Changes and Kinds: its snapshot is what it was given."""

    def __init__(self, value):
        self.value = value

    def snapshot(self):
        return self.value if self.value is not None else {"state": "none"}

    def get(self):
        return self.value


def record(night: str = "2026-11-16", **more) -> dict:
    rec = {"night": night, "state": "ok", "word": "正常", "started_at": f"{night}T17:00:02+00:00",
           "ended_at": f"{night}T19:40:10+00:00", "swapped": 1, "counted_by": None, "site_counts": 0,
           "new": None, "changed": None, "left": None, "unexplained": None, "look": None, "docs": None,
           "source": "night",
           "steps": [{"step": "crawl", "name": "爬取", "state": "done", "word": "完成", "sev": "ok", "say": None,
                      "fixer": None, "start": "2026-11-17T01:00:02+08:00", "end": "2026-11-17T01:51:00+08:00"}],
           "sites": []}
    rec.update(more)
    return rec


@pytest.fixture
def store(tmp_path_factory) -> history.Store:
    return history.Store(tmp_path_factory.mktemp("ops") / "ops.db")


def test_the_store_makes_its_tables_and_says_its_schema(store):
    assert store.enabled and store.error == ""
    conn = sqlite3.connect(store.path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    # problem_lists, problems: 2026-11-21; night_changes, night_flow, usage_days: 11-23 (the design's port)
    assert names == {"meta", "nights", "steps", "sites", "problem_lists", "problems", "night_changes", "night_flow",
                     "usage_days"}
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == history.SCHEMA
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nights)")}
    assert {"night", "state", "counted_by", "new", "changed", "left", "unexplained", "docs", "digest"} <= cols
    conn.close()


def test_a_night_put_twice_is_written_once_and_read_back_whole(store):
    rec = record(counted_by="changes", site_counts=1, new=3, changed=1, left=2, unexplained=1, look=0, docs=70,
                 sites=[{"site": "a.example", "name": "甲處", "new": 3, "left": 2, "unexplained": 1}])
    assert store.put(rec) is True
    assert store.put(rec) is False  # the same night again: an upsert, nothing written
    assert store.count() == 1
    got = store.get("2026-11-16")
    assert (got["new"], got["left"], got["unexplained"], got["docs"]) == (3, 2, 1, 70)
    assert got["steps"][0]["name"] == "爬取" and got["sites"][0]["site"] == "a.example"
    assert store.put({**rec, "state": "warn", "word": "停了"}) is True and store.count() == 1  # it replaces itself
    assert store.get("2026-11-16")["state"] == "warn"


def test_what_a_later_reading_lacks_never_wipes_what_is_stored(store):
    store.put(record(counted_by="changes", site_counts=1, new=3, changed=1, left=2, unexplained=0, look=0, docs=70,
                     sites=[{"site": "a.example", "new": 3, "left": 2, "unexplained": 0, "docs": 9, "undated": 1}]))
    # a later look without the comparison (the library was swapped again) and without the library's counts
    store.put(record(state="warn", word="停了"))
    got = store.get("2026-11-16")
    assert got["state"] == "warn" and (got["new"], got["docs"], got["counted_by"]) == (3, 70, "changes")
    assert got["sites"] == [{"site": "a.example", "name": None, "docs": 9, "undated": 1, "new": 3, "left": 2,
                             "unexplained": 0}]
    # the ingest's coarser counts never replace the comparison's
    store.put(record(counted_by="ingest", new=99, left=99, unexplained=5, look=None, changed=None))
    assert store.get("2026-11-16")["new"] == 3
    # but the comparison replaces the ingest's
    store.put(record("2026-11-15", counted_by="ingest", new=10, left=4, unexplained=1,
                     sites=[{"site": "b.example", "unexplained": 1}]))
    store.put(record("2026-11-15", counted_by="changes", site_counts=1, new=11, changed=2, left=4, unexplained=0,
                     look=0, sites=[{"site": "c.example", "new": 11, "left": 4, "unexplained": 0}]))
    got = store.get("2026-11-15")
    assert (got["new"], got["unexplained"], got["site_counts"]) == (11, 0, 1)
    assert [s["site"] for s in got["sites"]] == ["c.example"]  # b's count was the ingest's: gone with it


def test_a_store_not_set_or_not_writable_is_off_and_says_why(tmp_path, tmp_path_factory):
    off = history.Store(None)
    assert not off.enabled and "--ops-db" in off.error and off.put(record()) is False and off.count() == 0
    inside = history.Store(tmp_path / "ops.db", forbidden=(tmp_path,))  # never under /work or /crawl
    assert not inside.enabled and "不能放在" in inside.error and not (tmp_path / "ops.db").exists()
    locked = tmp_path_factory.mktemp("locked")
    os.chmod(locked, 0o500)
    try:
        cannot = history.Store(locked / "sub" / "ops.db")
        assert not cannot.enabled and "寫不進去" in cannot.error
    finally:
        os.chmod(locked, 0o700)
    page = history.home_history(off, date(2026, 11, 16))
    assert page["enabled"] is False and page["why"] == off.error and len(page["days"]) == history.SHOWN


# ---------------------------------------------------------------- the recorder over made-up night files


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db", docs=[
        ("d1", "notice", "甲", "https://a.example/1", "內文", "2026-11-01", "a.example"),
        ("d2", "attachment", "乙", "https://a.example/2", "內文", None, "a.example"),
        ("d3", "event", "丙", "https://b.example/3", "內文", None, "b.example")],
        sites=[("a.example", "甲處", 2, "exp/a"), ("b.example", "乙處", 1, "exp/b")])
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def two_nights(where: Where) -> None:
    """11-15: swapped, its counts in the ingest's own line; 11-16: swapped (the library in use)."""
    lines = [(s.replace("2026-11-17", "2026-11-16"), w) for s, w in NIGHT] + NIGHT
    crawler_log(where, lines, at("2026-11-17T02:06:00"))
    crawler_status(where)
    before = run("swapped", night="2026-11-15", started="2026-11-16T02:06:30+08:00",
                 finished="2026-11-16T03:40:10+08:00",
                 library={"before": {"docs": 100, "sites": {}}, "after": {"docs": 104, "sites": {"a.example": 2}}},
                 departures={"checked": True, "left": 6, "unexplained_total": 1, "by_site": {"a.example": 1}})
    said = [(s.replace("2026-11-17", "2026-11-16"), w) for s, w in SAID] + SAID
    ingest(where, [before, run("swapped")], said)


def test_the_first_look_backfills_every_finished_night_and_a_second_writes_nothing(where, tmp_path_factory):
    two_nights(where)
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    changes = {"state": "ready", "night": "2026-11-16",
               "totals": {"new": 2, "changed": 1, "left": 1, "unexplained": 0, "look": 1},
               "sites": [{"site": "a.example", "name": "甲處", "new": 2, "left": 1, "unexplained": 0}]}
    kinds = {"a.example": {"notice": [1, 0], "attachment": [1, 1]}, "b.example": {"event": [1, 1]}}
    rec = history.Recorder(store, where, changes=Fixed(changes), kinds=Fixed(kinds))
    assert rec.tick(now=at("2026-11-17T09:00:00")) == 2 and rec.backfilled
    older, last = store.get("2026-11-15"), store.get("2026-11-16")
    assert older["source"] == last["source"] == "backfill"
    # 11-15 from the ingest's own line: new = after − before + left
    assert (older["counted_by"], older["new"], older["left"], older["unexplained"], older["docs"]) == \
        ("ingest", 10, 6, 1, 104)
    assert {s["site"]: (s["unexplained"], s["docs"]) for s in older["sites"]} == {"a.example": (1, 2)}
    # 11-16 from the last swap's comparison and the library it left in use
    assert (last["counted_by"], last["new"], last["changed"], last["left"], last["look"], last["docs"]) == \
        ("changes", 2, 1, 1, 1, 3)
    assert {s["site"]: (s["docs"], s["undated"], s["new"], s["left"]) for s in last["sites"]} == \
        {"a.example": (2, 1, 2, 1), "b.example": (1, 0, None, None)}  # 行事曆 is not counted as undated
    assert [x["step"] for x in last["steps"]] == ["crawl", "export", "totext", "build", "swap"]
    assert last["steps"][0]["start"] == "2026-11-17T01:00:02+08:00" and last["swapped"] == 1
    assert rec.tick(now=at("2026-11-17T09:05:00")) == 0  # nothing new: nothing written
    # one site's nights: exact where the comparison counted, 0 for a site it did not list
    nights = store.site_nights("b.example", "2026-11-15", "2026-11-16")
    assert [(x["night"], x["counted"], x["new"]) for x in nights] == [("2026-11-15", False, None),
                                                                       ("2026-11-16", True, 0)]


def test_a_night_still_running_is_not_recorded_until_it_ends(where, tmp_path_factory):
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    rec = history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None))
    assert rec.tick(now=at("2026-11-17T01:40:00")) == 0 and store.count() == 0
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("nothing_new", step="plan", finished="2026-11-17T02:06:40+08:00")])
    assert rec.tick(now=at("2026-11-17T09:00:00")) == 1
    got = store.get("2026-11-16")
    assert got["source"] == "night" and got["swapped"] == 0  # the second look is not the backfill
    # nothing swapped: nothing came in or left, exactly
    assert (got["counted_by"], got["new"], got["left"], got["site_counts"]) == ("ingest", 0, 0, 1)


def test_a_missed_night_is_recorded_as_missed_with_nothing_changed(where, tmp_path_factory):
    crawler_log(where, [(s.replace("2026-11-17", "2026-11-16"), w) for s, w in NIGHT], at("2026-11-16T02:06:00"))
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    history.Recorder(store, where).tick(now=at("2026-11-17T09:00:00"))
    got = store.get("2026-11-16")
    assert got["state"] == "missed" and got["swapped"] == 0 and got["new"] == 0
    assert nightly.NIGHT_WORDS["missed"] == got["word"]


def test_the_pages_get_fourteen_nights_with_a_gap_where_none_is_recorded(store):
    store.put(record("2026-11-15", counted_by="ingest", new=4, left=1, unexplained=0, changed=None, look=None,
                     sites=[{"site": "a.example", "new": 4, "left": 1, "unexplained": 0}]))
    page = history.home_history(store, date(2026, 11, 16))
    assert page["enabled"] and len(page["nights"]) == 14 and page["days"][-1] == "2026-11-16"
    assert page["nights"][-1] is None and page["nights"][-2]["new"] == 4
    assert page["sites"] == {"2026-11-15": [{"site": "a.example", "name": None, "new": 4, "left": 1,
                                             "unexplained": 0}]}
    one = history.site_history(store, "a.example", date(2026, 11, 16))
    assert one["nights"][-2]["counted"] is False  # the ingest's counts are not exact per site: no bar, not a 0


# ---------------------------------------------------------------- 11-23: a night's comparison, its steps, the use per day


def comparison(night: str = "2026-11-22", **more) -> dict:
    return {"state": "ready", "night": night, "totals": {"new": 2, "changed": 1, "left": 1, "unexplained": 0,
                                                          "look": 0},
            "by_reason": {"copy": 1}, "reasons": {"copy": {"label": "併為副本", "tip": "", "kind": "ok", "fix": ""}},
            "sites": [{"site": "a.example", "name": "甲處", "new": 2, "changed": 1, "left": 1, "unexplained": 0,
                       "lists": {"new": [{"id": "d1", "title": "新的", "url": "https://a.example/1"}], "changed": [],
                                 "left": {"copy": [{"id": "d9", "title": "舊的", "url": "https://a.example/9"}]}}}],
            "unexplained": [], **more}


def test_a_nights_comparison_is_kept_by_its_run_day(store):
    assert store.night_changes("2026-11-23") is None and store.night_changes_days() == []
    assert store.put_night_changes("2026-11-23", comparison()) is True
    assert store.put_night_changes("2026-11-23", comparison()) is False  # the same comparison: written once
    got = store.night_changes("2026-11-23")
    assert got == comparison() and got["sites"][0]["lists"]["left"]["copy"][0]["id"] == "d9"
    assert store.put_night_changes("2026-11-23", comparison(by_reason={"copy": 2})) is True  # a later swap replaces it
    assert store.night_changes("2026-11-23")["by_reason"] == {"copy": 2}
    assert store.put_night_changes("2026-11-24", {"state": "computing"}) is False  # never one not ready
    assert store.put_night_changes("../x", comparison()) is False and store.night_changes("../x") is None
    assert store.night_changes_days() == ["2026-11-23"]
    assert history.Store(None).put_night_changes("2026-11-23", comparison()) is False


def swapped(built: str, at_: str, **more) -> dict:
    return comparison(new={"built_at": built, "swapped_at": at_, "docs": 9}, **more)


def test_a_days_later_swap_is_the_row_and_the_earlier_ones_stay_beside_it(store):
    """A hand promote in the afternoon after the 01:00 run's swap: both are the day's (nights/<day>.json lists both);
    the promote's comparison is the row and the run's stays in 'earlier', never lost to it."""
    run_ = swapped("2026-11-23T01:20:00+08:00", "2026-11-23T01:40:00+08:00")
    promote = swapped("2026-11-23T09:40:00+08:00", "2026-11-23T15:20:00+08:00", by_reason={"copy": 3})
    assert store.put_night_changes("2026-11-23", run_) is True
    assert store.put_night_changes("2026-11-23", promote) is True
    got = store.night_changes("2026-11-23")
    assert got["by_reason"] == {"copy": 3} and got["earlier"] == [run_]
    assert store.put_night_changes("2026-11-23", promote) is False  # the same again: nothing written
    # the same swap's comparison made again (a restart) replaces itself and keeps what was before it
    assert store.put_night_changes("2026-11-23", {**promote, "took": "3 秒"}) is True
    assert store.night_changes("2026-11-23")["earlier"] == [run_]
    for i in range(6):  # at most EARLIER_KEEP kept, the latest of them
        store.put_night_changes("2026-11-23", swapped(f"b{i}", f"2026-11-23T{16 + i}:00:00+08:00"))
    kept = store.night_changes("2026-11-23")["earlier"]
    assert len(kept) == history.EARLIER_KEEP and kept[-1]["new"]["built_at"] == "b4"
    assert all("earlier" not in e for e in kept)


def test_the_recorder_files_a_comparison_under_the_day_its_swap_record_is(where, tmp_path_factory):
    """keep_changes uses the day nights/<day>.json files the swap (sources.swap_day), so an afternoon promote is the
    day's own, not the next night's (critic CR-03: the two per-night stores agree)."""
    import json

    two_nights(where)
    folder = where.library.parent / "nights"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-11-17.json").write_text(json.dumps({"day": "2026-11-17", "swaps": [
        {"started_at": "2026-11-17T14:50:00+08:00", "how": "promote", "built_at": "2026-11-17T09:00:00+08:00",
         "sites": {}}]}), encoding="utf-8")
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    promote = {**comparison("2026-11-17"), "new": {"built_at": "2026-11-17T09:00:00+08:00",
                                                   "swapped_at": "2026-11-17T15:00:00+08:00"}}
    rec = history.Recorder(store, where, changes=Fixed(promote), kinds=Fixed(None))
    assert rec.keep_changes(promote) is True
    assert store.night_changes("2026-11-17") == promote and store.night_changes("2026-11-18") is None


def test_a_nights_steps_are_kept_without_the_services_use(store):
    snap = {"at": "2026-11-23T04:06:00+08:00", "began": "x",
            "steps": [{"key": "crawl", "lost": [["連線失敗", 2]], "lost_keys": [["failed", "連線失敗", 2]]},
                      {"key": "serve", "usage": [["查詢", "ok", "3 次"]], "usage_missed": [["1", "MCP", "誰"]],
                       "usage_label": "x", "say": "正常"}],
            "sites": [{"key": "a.example", "name": "甲處", "cells": {"crawl": {"n": 5}}, "worst": "ok", "q": "a"}]}
    flow = history.flow_of(snap)
    assert flow["steps"][1] == {"key": "serve", "say": "正常"} and flow["sites"][0] == {
        "key": "a.example", "name": "甲處", "cells": {"crawl": {"n": 5}}, "worst": "ok"}
    assert store.put_night_flow("2026-11-23", flow) is True and store.put_night_flow("2026-11-23", flow) is False
    got = store.night_flow("2026-11-23")
    assert got["steps"][0]["lost_keys"] == [["failed", "連線失敗", 2]] and got["at"] == "2026-11-23T04:06:00+08:00"
    assert store.night_flow_days() == ["2026-11-23"] and store.put_night_flow("2026-11-24", {"steps": []}) is False


def test_a_days_use_is_kept_and_a_day_not_kept_is_a_gap(store):
    assert store.put_usage_day("2026-11-20", {"calls": 3, "search": 2, "complete": True}) is True
    assert store.put_usage_day("2026-11-20", {"day": "2026-11-20", "calls": 3, "search": 2, "complete": True}) is False
    store.put_usage_day("2026-11-22", {"calls": 0, "search": 0, "complete": True})
    got = store.usage_days("2026-11-01", "2026-11-30")
    assert got == [{"day": "2026-11-20", "calls": 3, "search": 2, "complete": True},
                   {"day": "2026-11-22", "calls": 0, "search": 0, "complete": True}]  # 11-21: absent, not 0
    assert store.put_usage_day("someday", {"calls": 1}) is False


def test_the_recorder_keeps_the_comparison_under_its_nights_run_day(where, tmp_path_factory):
    two_nights(where)
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    ready = comparison("2026-11-16")
    rec = history.Recorder(store, where, changes=Fixed(ready), kinds=Fixed(None))
    rec.tick(now=at("2026-11-17T09:00:00"))
    assert store.night_changes("2026-11-17") == ready and rec.last["changes"] is True  # night 11-16 runs on 11-17
    assert store.night_changes("2026-11-16") is None
    rec.tick(now=at("2026-11-17T09:05:00"))
    assert rec.last["changes"] is False  # the same comparison: nothing written again
    busy = history.Recorder(store, where, changes=Fixed({"state": "computing", "night": "2026-11-17"}),
                            kinds=Fixed(None))
    busy.tick(now=at("2026-11-18T09:00:00"))
    assert store.night_changes("2026-11-18") is None


def test_the_recorder_keeps_each_finished_day_of_the_services_use(where, tmp_path_factory, monkeypatch):
    import json

    from nknu.ops import pipeline as pl

    monkeypatch.setattr(pl, "search_slots", lambda: 4)
    log = where.root / "queries.jsonl"
    lines = [{"t": "2026-11-20T15:00:00+08:00", "tool": "search", "ms": 900},
             {"t": "2026-11-20T15:00:30+08:00", "tool": "fetch", "ms": 100},
             {"t": "2026-11-22T10:00:00+08:00", "tool": "search", "ms": 1200, "error": "busy"},
             {"t": "2026-11-23T08:00:00+08:00", "tool": "search", "ms": 700}]  # today: not over, not kept
    log.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    where.public_log = log
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    rec = history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None))
    assert rec.keep_usage(at("2026-11-23T09:00:00")) == 3
    days = {d["day"]: d for d in store.usage_days("2026-10-01", "2026-11-30")}
    assert sorted(days) == ["2026-11-20", "2026-11-21", "2026-11-22"]  # before 11-20 the log does not reach: unknown
    assert days["2026-11-20"]["calls"] == 2 and days["2026-11-20"]["search"] == 1
    assert days["2026-11-20"]["complete"] is False  # the log began that day
    assert days["2026-11-21"]["calls"] == 0 and days["2026-11-21"]["complete"] is True  # a quiet day, known
    assert days["2026-11-22"]["errors"] == 1 and days["2026-11-22"]["slots"] == 4
    assert rec.keep_usage(at("2026-11-23T09:05:00")) == 0  # kept: not read again
    where.public_log = None
    assert history.Recorder(store, where).keep_usage(at("2026-11-24T09:00:00")) == 0  # no log mounted: nothing


def test_the_recorder_keeps_a_finished_nights_steps_with_its_problems(where, tmp_path_factory):
    from tests.ops.test_standing import Flow, reading

    two_nights(where)  # 11-16's chain ended 03:40
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    flow = Flow(reading("2026-11-17T03:00:00+08:00"))  # read while the chain ran: not the night's
    keeper = history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None), flow=flow)
    keeper.tick(now=at("2026-11-17T03:05:00"))
    assert store.night_flow("2026-11-17") is None
    flow.snap = reading("2026-11-17T04:06:00+08:00")
    keeper.tick(now=at("2026-11-17T04:10:00"))
    got = store.night_flow("2026-11-17")  # night 11-16, its run day 11-17
    assert got["at"] == "2026-11-17T04:06:00+08:00" and got["steps"][0]["key"] == "serve"


def test_a_known_marks_last_sighting_is_read_from_the_kept_lists(store):
    def rec(key: str) -> dict:
        return {"key": key, "sev": "warn", "place": "甲處", "state": "warn", "reason": f"{key} 的原因", "href": "/x"}

    store.put_problems("2026-11-19", [rec("step:build"), rec("site:a.example:docs")], at="x")
    store.put_problems("2026-11-20", [rec("step:build"), rec("site:a.example:docs"), rec("site:b_x.example:docs")],
                       at="x")
    store.put_problems("2026-11-21", [rec("site:a.example:export")], at="x")
    got = store.problem_sightings(["step:build", "site:a.example", "site:b_x.example", "machine:gpu", "site:a"])
    assert got["step:build"]["night"] == "2026-11-20" and got["step:build"]["run_day"] == "2026-11-21"
    assert got["step:build"]["gone_run_day"] == "2026-11-22"  # night 11-21's list did not hold it: 11-22 起沒再出現
    # a mark on a site covers every key under it; still in the latest list: not gone
    assert got["site:a.example"]["key"] == "site:a.example:export" and got["site:a.example"]["gone_run_day"] is None
    assert got["site:b_x.example"]["reason"] == "site:b_x.example:docs 的原因"
    assert "machine:gpu" not in got and "site:a" not in got  # never listed; a prefix is only a whole part


def test_the_last_swapped_night_is_found_with_its_steps(store):
    store.put(record("2026-11-20", swapped=1))
    store.put(record("2026-11-21", swapped=0))
    got = store.last_swapped()
    assert got["night"] == "2026-11-20" and got["steps"][0]["name"] == "爬取"
    assert store.last_swapped(before="2026-11-19") is None and history.Store(None).last_swapped() is None


def test_the_use_per_day_api_gives_the_kept_days_and_today_live(store, tmp_path):
    import json as _json

    from nknu.ops import pipeline as pl

    log = tmp_path / "queries.jsonl"
    log.write_text(_json.dumps({"t": "2026-11-23T08:00:00+08:00", "tool": "search", "ms": 700}) + "\n", encoding="utf-8")
    store.put_usage_day("2026-11-21", {"calls": 3, "complete": True})
    store.put_usage_day("2026-11-23", {"calls": 99, "complete": True})  # today kept by mistake: today is read live
    where = Where(root=tmp_path, db=tmp_path / "aca.db", public_log=log)
    got = history.usage_api(store, where, {}, now=at("2026-11-23T09:00:00"))
    assert (got["first"], got["last"]) == ("2026-11-01", "2026-11-23") and got["slots"] == pl.search_slots()
    assert [(d["day"], d["calls"], d.get("today")) for d in got["days"]] == [("2026-11-21", 3, None),
                                                                            ("2026-11-23", 1, True)]
    past = history.usage_api(store, where, {"first": "2026-11-01", "last": "2026-11-22"}, now=at("2026-11-23T09:00:00"))
    assert [d["day"] for d in past["days"]] == ["2026-11-21"]
