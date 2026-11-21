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
    assert names == {"meta", "nights", "steps", "sites", "problem_lists", "problems"}  # the last two: 2026-11-21
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
