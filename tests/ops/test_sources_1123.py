"""The per-night files the panels read (nknu.ops.sources, the 11-23 port of the owner's design copy): the crawler's
coverage.jsonl summed over the library's sites with round two's batch apart (the design's 抓取覆蓋 and its 7-night
trend), a missing night left a gap, an older night summed over its own library's sites; the nightly ingest's
nights/<run day>.json as written; run days against ops.db's night keys. Made-up lines in the writers' shapes
(nknu.archive.coverage, deploy/lab-pipeline/night_sites.py); the 11-22 totals are the design's (cov_labels.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nknu.ops import sources

# the design's cov_labels.json (the backup after run day 11-22), split over two sites of the library and one more
A = {"buckets": {"got": 100_000, "review": 800, "failed": 1_785, "gone": 3_000, "rule": 29_346, "next_round": 2_444,
                 "login": 110, "queued": 21},
     "review": {"api_error": 191, "thin": 498, "too_large": 52, "held": 59}}
B = {"buckets": {"got": 18_164, "review": 176, "gone": 529},
     "review": {"decode": 116, "held": 41, "parked": 18, "looks_gone": 1}}
OUTSIDE = {"buckets": {"got": 9_999, "failed": 5}, "review": {}}  # a site the library does not hold


def line(night: str, sites: dict, **more) -> dict:
    return {"night": night, "ledger": "live", "written_at": f"{night} 03:31:00", "round2_open": [], "sites": sites,
            "others": {"sites": 3, "buckets": {"got": 7}}, **more}


@pytest.fixture
def crawl(tmp_path) -> Path:
    root = tmp_path / "crawl"
    (root / "reports").mkdir(parents=True)
    return root


def write(crawl: Path, lines: list[dict], *, tail: str = "") -> None:
    text = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines) + tail
    sources.coverage_path(crawl).write_text(text, encoding="utf-8")


def test_the_coverage_file_sits_in_the_crawlers_reports():
    assert sources.coverage_path(Path("/crawl/archive")) == Path("/crawl/archive/reports/coverage.jsonl")


def test_a_night_is_summed_over_the_librarys_sites_as_the_design_counted_it(crawl):
    write(crawl, [line("2026-11-22", {"a.example": A, "b.example": B, "out.example": OUTSIDE})])
    [n] = sources.coverage_nights(crawl, {"a.example", "b.example"})
    assert (n["night"], n["got"], n["meant"]) == ("2026-11-22", 118_164, 124_585)  # meant: all − rule − next_round
    assert round(n["pct"], 2) == 94.85 and n["scope"] == 2
    assert n["ours"] == 976 + 1_785 + 21 and n["school"] == 3_529 + 110  # 要處理, 學校那邊
    # 待確認 by its causes, in the names.py words (the drawer's labels)
    assert n["review"]["待確認：內容過少"] == 498 and n["review"]["待確認：超過抓取上限"] == 100
    assert n["labels"]["已取得"] == {"bucket": "got", "n": 118_164}
    assert n["labels"]["連線失敗"] == {"bucket": "failed", "n": 1_785}
    assert n["labels"]["校方已移除"]["n"] == 3_529 and n["labels"]["需要登入"]["bucket"] == "login"
    assert n["labels"]["待確認：雲端檔案無法讀取"] == {"bucket": "review", "n": 191} and "待確認" not in n["labels"]
    assert next(iter(n["labels"])) == "已取得"  # largest first
    one = n["sites"]["b.example"]
    assert (one["got"], one["meant"], one["review"]["待確認：編碼異常"]) == (18_164, 18_869, 116)
    assert "out.example" not in n["sites"]
    every = sources.coverage_nights(crawl)[0]  # no scope given: every site the line names
    assert every["got"] == 118_164 + 9_999 and every["scope"] == 3


def test_round_twos_batch_is_kept_apart(crawl):
    batch = {**B, "batch": {"got": 40, "queued": 60}}
    write(crawl, [line("2026-11-22", {"a.example": A, "b.example": batch}, round2_open=["b.example"])])
    [n] = sources.coverage_nights(crawl, {"a.example", "b.example"})
    assert n["got"] == 118_164 and n["batch"] == {"got": 40, "queued": 60}
    assert n["sites"]["b.example"]["batch"] == {"got": 40, "queued": 60} and n["round2_open"] == ["b.example"]


def test_the_trend_keeps_seven_run_days_and_a_missing_night_is_a_gap(crawl):
    nights = ["2026-11-14", "2026-11-16", "2026-11-17", "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]
    write(crawl, [line(d, {"a.example": A}) for d in nights], tail='{"night": "2026-11-23", "sit')  # cut short
    got = sources.coverage_nights(crawl, {"a.example"})
    assert [n["night"] for n in got] == ["2026-11-16", "2026-11-17", "2026-11-19", "2026-11-20", "2026-11-21",
                                          "2026-11-22"]  # 11-18 absent: the page draws a gap there
    assert [n["night"] for n in sources.coverage_nights(crawl, {"a.example"}, 3, until="2026-11-20")] == \
        ["2026-11-19", "2026-11-20"]
    assert sources.coverage_nights(crawl, {"a.example"}, 0) == []


def test_no_file_is_quiet_and_a_changed_file_is_read_again(crawl, tmp_path):
    assert sources.coverage_nights(crawl, {"a.example"}) == [] and sources.coverage_nights(None) == []
    write(crawl, [line("2026-11-22", {"a.example": A})])
    assert sources.coverage_nights(crawl, {"a.example"})[0]["got"] == 100_000
    write(crawl, [line("2026-11-22", {"a.example": A}), line("2026-11-23", {"a.example": {"buckets": {"got": 5}}})])
    assert [n["got"] for n in sources.coverage_nights(crawl, {"a.example"})] == [100_000, 5]


def swap(started: str, sites: dict, **more) -> dict:
    return {"started_at": started, "finished_at": started, "how": "run", "backfilled": False, "reports": None,
            "built_at": started, "prev_built_at": None, "departures": None, "sites": sites, **more}


def site(**more) -> dict:
    return {"export": "archive-export/sites/a-1", "export_changed": True, "export_rows": 120, "records": 100,
            "totext": {"ok": 90, "empty": 3}, "docs": 95, "docs_before": 93, "unexplained_left": 0, **more}


@pytest.fixture
def library(tmp_path) -> Path:
    folder = tmp_path / "pipe"
    (folder / "nights").mkdir(parents=True)
    return folder


def write_night(library: Path, day: str, swaps: list[dict]) -> None:
    (library / "nights" / f"{day}.json").write_text(json.dumps({"day": day, "swaps": swaps}), encoding="utf-8")


def test_a_nights_record_is_read_as_written(library):
    write_night(library, "2026-11-22", [swap("2026-11-22T01:00:02+08:00", {"a.example": site()}),
                                        swap("2026-11-22T14:10:00+08:00", {"a.example": site(export_rows=None),
                                                                           "b.example": site()}, how="promote")])
    write_night(library, "2026-11-21", [swap("2026-11-21T01:00:02+08:00", {"a.example": site()})])
    (library / "nights" / "notes.json").write_text("{}", encoding="utf-8")
    rec = sources.night_record(library, "2026-11-22")
    assert rec["day"] == "2026-11-22" and len(rec["swaps"]) == 2
    assert sources.night_record(library / "aca.db", "2026-11-22") == rec  # the library file itself works as well
    assert sources.night_record_days(library) == ["2026-11-21", "2026-11-22"]
    last = sources.night_swap(library, "2026-11-22")
    assert last["how"] == "promote" and sources.night_sites(library, "2026-11-22") == {"a.example", "b.example"}
    # an export folder the crawler cleared is said so, never 0
    assert sources.export_rows_word(last["sites"]["a.example"]) == "舊導出資料夾已清掉"
    assert sources.export_rows_word(last["sites"]["b.example"]) == "120"
    # a night without a swap, a day that is no date, a broken file: None, never an error
    assert sources.night_record(library, "2026-11-20") is None and sources.night_swap(library, "2026-11-20") is None
    assert sources.night_record(library, "../../etc/passwd") is None and sources.night_record(library, "") is None
    (library / "nights" / "2026-11-19.json").write_text("{not json", encoding="utf-8")
    assert sources.night_record(library, "2026-11-19") is None


def test_an_older_night_is_summed_over_its_own_librarys_sites(crawl, library):
    write(crawl, [line("2026-11-21", {"a.example": A, "b.example": B}), line("2026-11-22", {"a.example": A,
                                                                                            "b.example": B})])
    write_night(library, "2026-11-21", [swap("2026-11-21T01:00:02+08:00", {"a.example": site()})])
    got = sources.coverage_nights(crawl, {"a.example", "b.example"}, library=library)
    assert [(n["night"], n["scope"], n["got"]) for n in got] == [("2026-11-21", 1, 100_000),
                                                                ("2026-11-22", 2, 118_164)]


def test_a_run_day_is_the_night_key_plus_one_by_one_mapping():
    from datetime import date

    from nknu.ops import nightly

    assert sources.run_day is nightly.run_day  # one helper (CR-03): sources only re-exports it
    assert sources.run_day("2026-11-22") == "2026-11-23" and sources.night_of_run("2026-11-23") == "2026-11-22"
    assert sources.run_day("2026-11-30T03:00:00+08:00") == "2026-12-01"
    assert sources.run_day(date(2026, 12, 31)) == "2027-01-01" and sources.night_of_run(date(2027, 1, 1)) == "2026-12-31"
    for day in ("2026-11-23", "2026-12-01", "2027-01-01"):
        assert sources.run_day(sources.night_of_run(day)) == day


def test_the_coverage_says_when_it_is_behind_the_night_it_should_have(crawl):
    from datetime import datetime

    from nknu.core.timeutil import TAIPEI

    write(crawl, [line(d, {"a.example": A}) for d in ("2026-11-20", "2026-11-21")])
    morning, noon = datetime(2026, 11, 22, 9, 0, tzinfo=TAIPEI), datetime(2026, 11, 22, 12, 0, tzinfo=TAIPEI)
    assert sources.expected_night(morning) == "2026-11-21" and sources.expected_night(noon) == "2026-11-22"
    fresh = sources.coverage_nights(crawl, {"a.example"}, now=morning)
    assert fresh[-1]["night"] == "2026-11-21" and fresh[-1]["stale"] is False and fresh[-1]["missing"] == 0
    # the step stopped writing: the newest line is not tonight's, and the card can name its night
    late = sources.coverage_nights(crawl, {"a.example"}, now=datetime(2026, 11, 24, 13, 0, tzinfo=TAIPEI))
    assert late[-1]["night"] == "2026-11-21" and late[-1]["stale"] is True and late[-1]["missing"] == 3
    assert late[-1]["expected"] == "2026-11-24" and all(n["stale"] for n in late)
    # asked to end at the night expected, the nights without a line are the window's trailing gaps
    assert sources.coverage_nights(crawl, {"a.example"}, 3, until="2026-11-24") == []  # all three nights missing
    gaps = sources.coverage_nights(crawl, {"a.example"}, 4, until="2026-11-24")
    assert [n["night"] for n in gaps] == ["2026-11-21"] and "stale" not in gaps[0]


def test_a_swap_comparison_is_filed_under_the_day_its_swap_record_is(library):
    write_night(library, "2026-11-23", [swap("2026-11-23T01:00:02+08:00", {"a.example": site()}),
                                        swap("2026-11-23T15:10:00+08:00", {"a.example": site()}, how="promote",
                                             built_at="2026-11-23T09:40:00+08:00")])
    write_night(library, "2026-11-22", [swap("2026-11-22T23:50:00+08:00", {"a.example": site()},
                                             built_at="2026-11-23T00:20:00+08:00")])

    def ch(built: str, swapped: str) -> dict:
        return {"state": "ready", "new": {"built_at": built, "swapped_at": swapped}}

    # the 01:00 run's swap and the afternoon's promote are both that day's (ops.db's night key would put the promote
    # under the next night: swapped − 12 h is 11-23, its run day 11-24)
    assert sources.swap_day(library, ch("2026-11-23T01:00:02+08:00", "2026-11-23T01:40:00+08:00")) == "2026-11-23"
    assert sources.swap_day(library, ch("2026-11-23T09:40:00+08:00", "2026-11-23T15:20:00+08:00")) == "2026-11-23"
    # a run by hand begun before midnight is the day it began, as its record is
    assert sources.swap_day(library, ch("2026-11-23T00:20:00+08:00", "2026-11-23T00:30:00+08:00")) == "2026-11-22"
    # no record (an older swap, no nights folder): the Taipei day it was swapped in
    assert sources.swap_day(library, ch("2026-11-10T03:00:00+08:00", "2026-11-10T16:00:00+08:00")) == "2026-11-10"
    assert sources.swap_day(None, ch("x", "2026-11-10T18:00:00+00:00")) == "2026-11-11"
    assert sources.swap_day(library, {"state": "ready"}) is None and sources.swap_day(library, None) is None


def test_the_library_folder_and_its_sites(tmp_path):
    from nknu.ops.pipeline import PIPE, Where
    from tests.ops.test_pipeline import _library

    db = _library(tmp_path / PIPE / "aca.db")
    where = Where(root=tmp_path, db=db)
    assert sources.library_dir(where) == tmp_path / PIPE
    assert sources.library_sites(db) == {"school.example"}
    assert sources.library_sites(tmp_path / "none.db") == set()


def test_the_warc_backup_has_one_reader_the_to_do_lists():
    """CR-04: whether the raw files' nightly copy is behind is decided in one place, todo.warc_rows (要處理's row and its
    problem key); this module keeps no second reading of warc-backup.json."""
    assert not hasattr(sources, "warc_backup")
