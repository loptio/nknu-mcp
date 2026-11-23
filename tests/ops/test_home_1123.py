"""總覽 as the owner designed it on 11-23 (research/design_2026-11-23/index.html; nknu.ops.panorama, calendar, visits,
todo, theme/design/home.*): the calendar of nights under the day each runs, 網站訪問量 live and by day, 待處理 | 日誌 |
已知, the frame of 內容's chart with this machine under its donut. Made-up nights, logs and marks only."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import calendar, design, history, logs, nightly, panorama, sources, standing, todo, visits
from nknu.ops import pipeline as pl
from nknu.ops.pipeline import BAD, TAIPEI, WARN

LAYER = Path(design.DIR)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TAIPEI)


def c(key: str, state: str = "done", since: str = "", until: str = "", *, sev: str = "", say: str = "",
      word: str = "") -> dict:
    """A part of a night as nightly._cell makes it (the keys the calendar reads)."""
    day = "2026-11-22"
    return {"key": key, "state": state, "sev": sev or ("ok" if state == "done" else ""), "say": say,
            "word": word or {"done": "完成", "run": "跑中", "stop": "停了", "wait": "還沒開始"}[state],
            "since": f"{day}T{since}:00+08:00" if since else "", "until": f"{day}T{until}:00+08:00" if until else ""}


def clean(night: str, swap_at: str = "03:09") -> dict:
    """A night that ran clean and swapped (the run of the day after it)."""
    return {"night": night, "cells": [c("crawl", since="01:00", until="02:22"), c("export", since="02:22", until="02:48"),
                                      c("totext", since="02:48", until="02:52"), c("build", since="02:52", until=swap_at),
                                      c("swap", since=swap_at, until="03:40")]}


def waiting(night: str) -> dict:
    return {"night": night, "cells": [c(k, "wait") for k in ("crawl", "export", "totext", "build", "swap")]}


def tonight(now: str = "2026-11-23T00:30:00", nights: list[dict] | None = None, **more) -> dict:
    """nightly.tonight's answer, the seven nights ending at night_of(now)."""
    return {"now": at(now).isoformat(), "default": "2026-11-21", "sig": "abc", "nights": nights or [], **more}


def record(night: str, *, swapped: int = 1, new: int | None = 5, left: int | None = 2, steps: list[dict] | None = None,
           state: str = "ok") -> dict:
    """A night of history.Store.nights (its steps in STEP_COLS)."""
    steps = steps if steps is not None else [
        {"step": k, "name": w, "state": "done", "word": "完成", "sev": "ok", "say": None, "fixer": None,
         "start": f"{night}T{a}:00+08:00", "end": f"{night}T{b}:00+08:00"}
        for (k, w), (a, b) in zip(calendar.STAGES, (("01:00", "02:00"), ("02:00", "02:30"), ("02:30", "02:40"),
                                                    ("02:40", "03:30"), ("03:30", "03:31")), strict=True)]
    return {"night": night, "state": state, "word": "正常", "swapped": swapped, "new": new, "left": left, "steps": steps,
            "source": "night", "sites": []}


def cal_tree(model: dict) -> LexborHTMLParser:
    return LexborHTMLParser(calendar.card_html(model))


# ---------------------------------------------------------------- the calendar


def test_a_night_is_drawn_under_the_day_it_runs_and_the_week_ends_today_even_after_noon():
    # 00:30 on 11-23: the run of 11-23 (night 11-22) has not begun; the week is 11-17 … 11-23
    early = calendar.build(tonight(nights=[clean("2026-11-21"), waiting("2026-11-22")]), [])
    assert early["today"] == "2026-11-23" and early["week"][0] == "2026-11-17" and early["week"][-1] == "2026-11-23"
    cells = cal_tree(early).css(".wk-grid > *")
    assert [x.css_first(".wk-day").text() for x in cells][-2:] == ["週日11/22", "週一11/23"]
    ran, today = cells[-2], cells[-1]
    assert ran.tag == "a" and ran.attributes["href"] == "/sites?date=2026-11-22"  # night 11-21 ran on 11-22
    assert today.tag == "div" and "is-today" in today.attributes["class"] and today.css_first(".ic-wait")
    assert today.css_first("[aria-label='還沒開始']") is not None
    assert cells[0].css_first(".cal-dash").text() == "—"  # no record that far back
    # 13:00 on 11-23: tonight() now lists night 11-23 (runs tomorrow) and no longer 11-16; the week stays 11-17 … 11-23
    late = calendar.build(tonight("2026-11-23T13:00:00", nights=[clean("2026-11-22"), waiting("2026-11-23")]),
                          [record("2026-11-16")])
    assert late["week"] == early["week"] and "2026-11-24" not in [x for x in late["week"]]
    first = cal_tree(late).css(".wk-grid > *")[0]
    assert first.tag == "a" and first.attributes["href"] == "/sites?date=2026-11-17"  # from the nights' record
    # one mapping night key <-> run day for every page (CR-03): nightly's, never a copy of it here
    assert calendar.run_day is nightly.run_day and calendar.night_of_run is sources.night_of_run
    assert nightly.run_day("2026-11-30") == "2026-12-01" and sources.night_of_run("2026-12-01") == "2026-11-30"


def test_the_ring_says_each_step_and_its_centre_the_swap_time_the_running_step_or_a_dash():
    swapped = calendar.build(tonight(nights=[clean("2026-11-21")]), [record("2026-11-21", new=129, left=22)])
    cell = cal_tree(swapped).css_first("a.wk-d")
    arcs = [p.attributes["class"] for p in cell.css("svg.ic-ring > path")]
    assert arcs == ["a-done"] * 5 and cell.css_first("svg.ic-ring > text").text() == "03:09"
    assert cell.css_first(".m-in").text() == "+129" and cell.css_first(".m-out").text() == "−22"
    assert "data-tip" not in cell.attributes and cell.css_first(".cal-dot") is None  # all green: no card, no dot
    running = {"night": "2026-11-22", "cells": [c("crawl", since="01:00", until="02:20"), c("export", "run", "02:20"),
                                                c("totext", "wait"), c("build", "wait"), c("swap", "wait")]}
    run = cal_tree(calendar.build(tonight("2026-11-23T02:30:00", nights=[running]), []))
    live = run.css_first("a.wk-d.is-today")
    assert [p.attributes["class"] for p in live.css("svg.ic-ring > path")] == ["a-done", "a-run", "a-none", "a-none", "a-none"]
    assert live.css_first("svg.ic-ring > text").text() == "導出" and live.css_first(".m-in") is None
    assert live.attributes["data-tip-h"] == "跑中" and live.attributes["data-tip"] == "導出：02:20 開始，還在跑"
    # done with nothing new: the library stayed, so no swap time and no +/−
    calm = clean("2026-11-21")
    calm["cells"][-1]["say"] = "庫沒換"
    still = cal_tree(calendar.build(tonight(nights=[calm]), [])).css_first("a.wk-d")
    assert still.css_first("svg.ic-ring > text").text() == "—" and still.css_first(".m-in") is None


def test_a_night_not_all_green_says_its_worst_state_and_each_step_in_its_own_words():
    paused = {"night": "2026-11-17", "cells": [
        c("crawl", since="01:00", until="04:14"), c("export", since="04:14", until="04:44"),
        c("totext", since="04:44", until="06:25", sev=WARN, say="2 個網站到點還沒轉完"),
        c("build", "stop", "06:25", sev=WARN, say="暫停中（有 ingest.pause），只轉文字、沒建庫"), c("swap", "wait")]}
    failed = {"night": "2026-11-18", "cells": [
        c("crawl", since="01:00", until="02:20"), c("export", since="02:20", until="02:48"),
        c("totext", since="02:48", until="02:55"), c("build", "stop", "02:55", "03:20", sev=BAD), c("swap", "wait")]}
    model = calendar.build(tonight(nights=[paused, failed]), [])
    cells = {x.attributes["href"]: x for x in cal_tree(model).css("a.wk-d")}
    stop = cells["/sites?date=2026-11-18"]
    assert [p.attributes["class"] for p in stop.css("svg.ic-ring > path")] == ["a-done", "a-done", "a-done", "a-stop", "a-none"]
    assert stop.attributes["data-tip-h"] == "暫停" and stop.css_first(".cal-dot") is not None  # 轉文字 warned: the dot
    assert stop.attributes["data-tip"] == "建庫：暫停中（有 ingest.pause），只轉文字、沒建庫\n轉文字：2 個網站到點還沒轉完"
    bad = cells["/sites?date=2026-11-19"]
    assert "is-bad" in bad.attributes["class"] and bad.attributes["data-tip"] == "建庫：出錯"  # no say: the word
    # the crawler that never began (沒跑: stopped, bad, no start) is red, never 還沒開始
    missed = {"night": "2026-11-20", "cells": [c("crawl", "stop", sev=BAD, say="01:00 該開始，沒有動靜", word="沒跑"),
                                               c("export", "wait"), c("totext", "wait"), c("build", "wait"),
                                               c("swap", "wait")]}
    cell = cal_tree(calendar.build(tonight(nights=[missed]), [])).css_first("a.wk-d")
    assert "is-bad" in cell.attributes["class"] and cell.attributes["data-tip"] == "爬取：01:00 該開始，沒有動靜"


def test_the_week_has_the_nights_clock_around_the_ring_and_the_month_does_not():
    model = calendar.build(tonight(nights=[clean("2026-11-21")]), [])
    tree = cal_tree(model)
    week = tree.css_first("a.wk-d svg")
    assert "ic-gauge" in week.attributes["class"] and week.attributes["viewBox"] == "-12 -12 80 77"
    steps = week.css(".ga path[data-tip]")
    assert [(p.attributes["data-tip-h"], p.attributes["data-tip"]) for p in steps] == [
        ("爬取", "01:00–02:22"), ("導出", "02:22–02:48"), ("轉文字", "02:48–02:52"), ("建庫", "02:52–03:09"),
        ("換庫", "03:09–03:40")]
    assert [t.text() for t in week.css(".ga-lab")] == ["01", "10"] and len(week.css(".ga-tick")) == 4
    assert week.css(".ga-cut")  # where one step meets the next
    assert all("good-s" not in (p.attributes.get("class") or "") for p in steps)  # the dial has no state colours
    month = tree.css_first("a.cal-d svg")
    assert month.attributes["viewBox"] == "0 0 56 56" and month.css_first(".ga") is None


def test_the_month_starts_on_sunday_with_days_to_come_dashed():
    model = calendar.build(tonight(nights=[clean("2026-11-21"), waiting("2026-11-22")]), [record("2026-11-16")])
    tree = cal_tree(model)
    assert [s.text() for s in tree.css(".cal-head span")] == list("日一二三四五六")
    assert "2026 年 11 月" in tree.css_first(".t-month").text()
    cells = tree.css(".cal-grid > .cal-d")
    days = [x.css_first(".cal-n").text() if x.css_first(".cal-n") else "" for x in cells]
    assert days[0] == "1" and days.index("30") == 29  # 11-01 is a Sunday
    by = dict(zip(days, cells, strict=True))
    assert by["17"].tag == "a" and by["17"].attributes["href"] == "/sites?date=2026-11-17"  # night 11-16's record
    assert by["16"].css_first(".cal-dash") and by["23"].css_first(".ic-wait") and "is-today" in by["23"].attributes["class"]
    assert "is-future" in by["24"].attributes["class"] and by["24"].css_first("a, svg") is None
    assert all("is-out" in x.attributes["class"] for x in cells[30:])


def test_the_head_says_when_the_library_in_use_was_crawled_and_counts_down_to_the_next_run():
    later = clean("2026-11-22")
    later["cells"][-1]["say"] = "庫沒換"  # the last night did not swap: the library is still the night before's
    model = calendar.build(tonight("2026-11-23T09:00:00", nights=[clean("2026-11-21"), later]), [])
    times = cal_tree(model).css_first(".cal-times")
    lib = times.css_first(".ct:not(.ct-next)")
    assert lib.text() == "11-22 02:22" and lib.attributes["data-tip-h"] == "現在這份庫的資料"
    assert lib.attributes["data-tip"] == "11-22 01:00–02:22 抓下來的"
    nxt = times.css_first("#calNext")
    assert nxt.attributes["data-tip-h"] == "距離下一次每晚更新" and nxt.attributes["data-tip"] == "每天 01:00 開始"
    assert [s.text() for s in cal_tree(model).css(".cal-key-top span")] == ["完成", "暫停", "留意", "出錯"]
    helpb = cal_tree(model).css_first("button.hp[data-hp=cal]")
    assert "外圈是 01:00–10:00" in helpb.attributes["data-tip"] and "底下長條" not in helpb.attributes["data-tip"]
    assert "data-tip-full" in helpb.attributes  # read whole on hover: the reworded sentence sits past 90 characters
    # no night that swapped at all: a dash that says why, never a number gone without a word
    assert calendar.build(tonight(nights=[waiting("2026-11-22")]), [])["library"] == ("—", "檢視台的紀錄裡還沒有換過庫的一晚")


def test_the_library_time_comes_from_the_record_when_no_night_of_the_month_or_week_swapped():
    """A week or more of nights that did not swap (exactly when the number matters): the record's last night that
    swapped (history.Store.last_swapped) gives it; a swapped night without a crawl end says so (OV-18)."""
    stuck = [record(f"2026-11-{d:02d}", swapped=0) for d in range(1, 21)]
    old = record("2026-10-20", steps=[{"step": "crawl", "name": "爬取", "state": "done", "sev": "ok",
                                       "start": "2026-10-21T01:00:00+08:00", "end": "2026-10-21T03:15:00+08:00"}])
    model = calendar.build(tonight("2026-11-23T09:00:00"), stuck, last_swap=old)
    assert model["library"] == ("10-21 03:15", "10-21 01:00–03:15 抓下來的")
    lib = cal_tree(model).css_first(".cal-times .ct:not(.ct-next)")
    assert lib.text() == "10-21 03:15" and lib.attributes["data-tip-h"] == "現在這份庫的資料"
    # the calendar's own swapped night comes first, the record only when none is there
    assert calendar.build(tonight("2026-11-23T09:00:00", nights=[clean("2026-11-21")]), stuck,
                          last_swap=old)["library"][0] == "11-22 02:22"
    blind = record("2026-10-20", steps=[])
    assert calendar.build(tonight("2026-11-23T09:00:00"), stuck, last_swap=blind)["library"] == (
        "—", "上次換庫的那一晚（10-21）讀不到抓取是什麼時候結束的")


def test_a_recorded_night_gets_its_counts_and_the_latest_the_comparison_s_until_recorded():
    records = [record("2026-11-20", new=104, left=22)]
    changes = {"state": "ready", "night": "2026-11-21", "totals": {"new": 129, "left": 22}}
    model = calendar.build(tonight(nights=[clean("2026-11-21")], changes=changes), records)
    assert model["nights"]["2026-11-21"]["new"] == 104 and model["nights"]["2026-11-22"]["new"] == 129
    assert model["nights"]["2026-11-21"]["cells"]["swap"]["since"].endswith("03:30:00+08:00")  # the record's steps
    # a night before the chain went live is no night, never 還沒開始
    off = {"night": "2026-11-20", "cells": [{**c(k, "wait"), "word": "還沒上線"} for k, _w in calendar.STAGES]}
    cell = cal_tree(calendar.build(tonight(nights=[off]), [])).css(".wk-grid > *")[-3]
    assert cell.css_first(".cal-dash") is not None and cell.css_first(".ic-wait") is None


# ---------------------------------------------------------------- 網站訪問量


def write_log(path: Path, lines: list[dict]) -> Path:
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


def test_the_live_line_counts_each_second_from_the_query_log(tmp_path):
    now = at("2026-11-23T10:00:00")
    t = lambda s: (now - timedelta(seconds=s)).isoformat()
    log = write_log(tmp_path / "queries.jsonl", [
        {"t": t(400), "tool": "search", "ms": 900}, {"t": t(5), "tool": "search", "ms": 1200},
        {"t": t(5), "tool": "fetch", "ms": 30}, {"t": t(2), "tool": "search", "error": "busy"}])
    got = visits.live(log, now)
    assert got["read"] and got["end"] == int(now.timestamp()) - 1
    assert len(got["per_sec"]) == visits.WINDOW + visits.SMOOTH and sum(got["per_sec"]) == 3  # 400 s ago is out
    assert got["per_sec"][-5] == 2 and got["per_sec"][-2] == 1
    none = visits.live(tmp_path / "missing.jsonl", now)
    assert none["read"] is False and sum(none["per_sec"]) == 0
    assert visits.live_api(SimpleNamespace(public_log=log))["read"] is True


class Days:
    """A store with finished days (history.Store.usage_days' shape)."""

    enabled = True

    def __init__(self, rows: list[dict]) -> None:
        self.rows, self.asked = rows, None

    def usage_days(self, first: str, last: str) -> list[dict]:
        self.asked = (first, last)
        return [r for r in self.rows if first <= r["day"] <= last]


def call(t: str, ms: int = 1500, tool: str = "search", **more) -> dict:
    return {"t": t, "tool": tool, "ms": ms, "via": "mcp", **more}


def test_the_days_are_the_last_thirty_ending_today_and_the_record_fills_what_the_log_no_longer_holds(tmp_path):
    """Review 11-23: 每日 was this calendar month, so on the 1st it held today alone and opened on today's few morning
    calls (yesterday, in the record, was cut off with the month), and the days after today were drawn empty. It is the
    last 30 days ending today; days the log no longer covers come from the record (history.Store.usage_days)."""
    store = Days([{"day": "2026-11-30", "calls": 1800, "search": 1410, "qpm_peak": 34, "conc_peak": 2,
                   "load_p95": 0.08, "p90_ms": 3818, "complete": True}])
    log = write_log(tmp_path / "q.jsonl", [call("2026-12-01T07:59:00+08:00")])  # the log began today
    data = visits.month(store, log, now=at("2026-12-01T08:00:00"))
    assert data["dates"][0] == "2026-11-02" and data["dates"][-1] == "2026-12-01" and len(data["dates"]) == 30
    assert data["labels"][-1] == "12/1" and data["today"] == "2026-12-01"
    calls = dict(zip(data["dates"], data["calls"], strict=True))
    assert calls["2026-11-30"] == 1800 and calls["2026-12-01"] == 1 and calls["2026-11-29"] is None
    assert data["pick"] == "2026-11-30"  # the last day over, though it is last month's


def test_every_day_the_log_still_covers_is_read_from_it_so_yesterday_is_whole_right_after_midnight(tmp_path):
    """Review 11-23: from 00:00 until the Recorder's next round (up to 5 minutes) yesterday came from the 24-hour
    reading taken before midnight: fewer calls and no five numbers, and the card opened on the day before. Every day
    the log covers is read from the log (once, cached while it does not change): the record and the week's counts only
    fill days it no longer holds; the day the log began is marked not whole."""
    lines = [call("2026-11-21T23:30:00+08:00"), call("2026-11-22T10:00:00+08:00"),
             *[call(f"2026-11-23T23:58:{s:02d}+08:00", ms=300) for s in range(30)],
             call("2026-11-23T09:00:00+08:00"), call("2026-11-23T09:01:00+08:00"), call("2026-11-23T09:02:00+08:00")]
    log = write_log(tmp_path / "q.jsonl", sorted(lines, key=lambda x: x["t"]))
    store = Days([{"day": "2026-11-22", "calls": 99, "conc_peak": 5, "complete": True}])  # an older reckoning
    data = visits.month(store, log, now=at("2026-11-24T00:02:00"), recent=[["2026-11-23", 3, 3]])
    days = data["days"]
    assert days["2026-11-23"]["calls"] == 33 and days["2026-11-23"]["qpm_peak"] == 30 and days["2026-11-23"]["complete"]
    assert days["2026-11-22"]["calls"] == 1 and days["2026-11-22"]["conc_peak"] == 1  # the log's, not the record's
    assert days["2026-11-21"]["complete"] is False and "2026-11-20" not in days  # the log began on the 21st
    assert days["2026-11-24"]["calls"] == 0 and data["pick"] == "2026-11-23"


def test_two_searches_count_as_running_at_once_only_when_they_surely_overlap_at_the_logs_precision():
    """Review 11-23: the log writes its end time to the second (the fraction cut), so a search that ended at
    10:00:00.9 and the next that began at 10:00:00.95 looked overlapping. At one-second precision only an overlap that
    holds whatever the fractions were counts; a time with its fraction is taken as it is."""
    back_to_back = [call("2026-11-23T10:00:00+08:00", ms=1200), call("2026-11-23T10:00:01+08:00", ms=1900)]
    assert pl.usage_of(back_to_back, slots=4)["2026-11-23"]["conc_peak"] == 1
    together = [call("2026-11-23T10:05:00+08:00", ms=4000), call("2026-11-23T10:05:01+08:00", ms=4000)]
    assert pl.usage_of(together, slots=4)["2026-11-23"]["conc_peak"] == 2
    exact = [call("2026-11-23T10:00:00.900+08:00", ms=1200), call("2026-11-23T10:00:01.950+08:00", ms=1000)]
    assert pl.usage_of(exact, slots=4)["2026-11-23"]["conc_peak"] == 1  # 10:00:00.950 starts after 10:00:00.900
    overlap = [call("2026-11-23T10:00:00.900+08:00", ms=1200), call("2026-11-23T10:00:01.500+08:00", ms=1000)]
    assert pl.usage_of(overlap, slots=4)["2026-11-23"]["conc_peak"] == 2


class Flow:
    """The app's Pipeline, as visits.live_api reads it: the last reading, never a new one started."""

    def __init__(self, services: list[dict] | None) -> None:
        self.services = services

    def last(self) -> dict | None:
        if self.services is None:
            return None
        return {"at": "2026-11-23T13:40:05+08:00", "steps": [
            {"key": "serve", "at": "2026-11-23T13:40:00+08:00", "nums": {"services": self.services}}]}


def test_the_live_view_says_the_public_service_is_down_not_that_nobody_called(tmp_path):
    """Review 11-23: with the public service down the log stops, and 即時 said 「最近 5 分鐘沒有人查」. The last check
    of the services (查詢服務's reading) says whether it answered; down, the card says so and the numbers are 「—」."""
    log = write_log(tmp_path / "queries.jsonl", [call("2026-11-23T05:30:38+08:00", tool="fetch", ms=9)])
    down = [{"name": "對外服務（8770）", "kind": "public", "up": False, "words": "連不上"},
            {"name": "資料庫", "kind": "datasette", "up": True, "words": "有回應"}]
    where = SimpleNamespace(public_log=log)
    got = visits.live_api(where, {}, flow=Flow(down))
    assert got["serve"] == {"up": False, "at": "2026-11-23T13:40:00+08:00", "words": "連不上", "name": "對外服務（8770）"}
    assert visits.live_api(where, {}, flow=Flow(None))["serve"] is None  # no reading yet: nothing said
    assert visits.live_api(where, {})["serve"] is None
    ids = ["vs-data", "vs-day", "vs-ecg", "vs-ecg-none", "vs-ecg-quiet", "vs-rps", "vs-60", "vs-peak"]
    data = {"days": {}, "pick": "", "dates": [], "calls": [], "labels": [], "today": "2026-11-23", "slots": 4}
    out = run_layer(tmp_path, ids=ids, vsData=data, answers={"/api/usage/live": {**got, "per_sec": [0] * 330}})
    assert out["quiet"] == {"hidden": False, "text": "對外服務沒有回應（11/23 13:40 檢查）：連不上"}
    assert out["live"] == ["—", "—", "—"]


def test_the_days_view_asks_again_when_shown_so_today_and_the_date_follow_the_clock(tmp_path):
    """Review 11-23: 每日 was drawn once from the page's own data, so today's point and its numbers stayed as they were
    at load and, past midnight, yesterday was still 「今天」. Shown, it asks GET /api/usage/card (visits.month now) and
    redraws; a day not whole says so."""
    old = {"days": {"2026-11-23": {"calls": 9}}, "pick": "2026-11-23", "dates": ["2026-11-22", "2026-11-23"],
           "labels": ["11/22", "11/23"], "calls": [None, 9], "today": "2026-11-23", "slots": 4}
    new = {"days": {"2026-11-21": {"calls": 31, "qpm_peak": 10, "complete": False}, "2026-11-24": {"calls": 2}},
           "pick": "2026-11-21", "dates": ["2026-11-21", "2026-11-24"], "labels": ["11/21", "11/24"], "calls": [31, 2],
           "today": "2026-11-24", "slots": 4}
    ids = ["vs-data", "vs-day", "vs-chart", "vs-load", "vs-meter", "vs-qpm", "vs-qpm-at", "vs-conc", "vs-p90",
           "vs-lat-card", "vs-err"]
    out = run_layer(tmp_path, ids=ids, vsData=old, echarts=True, tipAt=[0, 1], answers={"/api/usage/card": new})
    assert "/api/usage/card" in out["asked"]
    assert out["day"] == "11/23（今天，還沒過完） · 9 次" and out["dayNow"] == "11/21（這天的紀錄不是整天） · 31 次"
    assert "31 次" in out["dayTips"][0] and "這天的紀錄不是整天" in out["dayTips"][0] and "今天還沒過完" in out["dayTips"][1]


def test_the_visits_card_is_the_designs_with_its_renamed_words():
    data = visits.month(None, None, now=at("2026-11-23T15:00:00"), recent=[["2026-11-23", 7, 5]])
    tree = LexborHTMLParser(visits.card_html(data))
    card = tree.css_first("section.vs")
    assert card.attributes["data-v"] == "live" and card.css_first(".pn-khead").text() == "網站訪問量"
    assert [b.text() for b in card.css(".vs-switch button")] == ["即時", "每日"]
    labels = [x.text() for x in card.css(".vs-sl")]
    assert labels[:3] == ["每秒請求", "每分鐘請求", "每秒峰值"]
    assert labels[3:] == ["負荷率 P95", "峰值 QPM", "最大並發", "P90 延遲", "錯誤率"]
    assert "示意" not in tree.html and "含測試流量" not in tree.html and "RPS" not in tree.html
    assert "每日：每天的查詢次數" in card.css_first("button.hp").attributes["data-tip"]
    assert "4 個檢索名額" in card.css_first(".vs-days .vs-main").attributes["data-tip"]
    assert card.css_first("#vs-chart").attributes["aria-label"] == "最近 30 天每天的查詢次數，可以放大縮小、左右拖"
    assert json.loads(card.css_first("#vs-data").text())["calls"][-1] == 7
    assert "</script><b>" not in visits.card_html({**data, "pick": "</script><b>"})  # the data never closes its tag


# ---------------------------------------------------------------- 待處理 | 日誌 | 已知


def row(where: str, sev: str, *keys: str, href: str = "", reason: str = "", **more) -> dict:
    return {"sev": sev, "where": where, "href": href, "state": "", "reason": reason, "code": "", "unknown": False,
            **more, "issues": [{"key": k, "sev": sev, "what": reason} for k in keys]}


A = row("甲處", WARN, "site:a.example:crawl", href="/site?site=a.example", reason="有 3 個網址抓不到", tag="≥2 天",
        age_tip="從開始記問題清單的那一晚就在；更早有沒有，檢視台沒有紀錄")
B = row("乙處", BAD, "site:b.example:export", "site:b.example:docs", href="/site?site=b.example", reason="導出失敗",
        tag="新出現")


def card(open_: list[dict], *, known: list[dict] | None = None, reading: bool = False) -> LexborHTMLParser:
    rows = [{**x, "kind": todo.kind_of(x)} for x in open_]
    return LexborHTMLParser(panorama.todo_card({"open": rows, "known": []}, reading, known))


def test_the_card_has_three_tabs_and_every_alert_on_one_line():
    tree = card([B, A, *[row(f"站{i}", WARN, f"site:s{i}:docs", reason="少了") for i in range(10)]])
    tabs = tree.css("#todo .lg-tabs [role=tab]")
    assert [t.text() for t in tabs] == ["待處理 12", "日誌", "已知 0"] and tabs[0].css_first("b").text() == "12"
    assert [t.attributes["aria-controls"] for t in tabs] == ["pane-todo", "pane-log", "pane-known"]
    assert tree.css_first("#todo").attributes["data-tab"] == "todo"
    head = tree.css_first("#todo .lg-head")
    assert head.css_first("#lv-q").attributes["placeholder"] == "搜尋紀錄（網址、站名、字詞）"
    assert head.css_first(".lv-max-btn").attributes["aria-label"] == "放大紀錄"
    assert head.css_first(".hp").attributes["data-tip-h"] == "待處理 · 日誌 · 已知"
    items = tree.css("#pane-todo ul.td > li")
    assert len(items) == 12 and tree.css_first(".pn-tmore") is None  # every row, the pane scrolls (no fold at 8)
    first = items[0]
    assert first.css_first(".td-mark.td-bad") and first.css_first(".td-kind").text() == "網站"
    assert first.css_first(".td-what a").attributes["href"] == "/site?site=b.example"
    assert first.css_first(".td-issue").text() == "導出失敗" and first.css_first(".td-age.td-new").text() == "新出現"
    age = items[1].css_first(".td-age")
    assert age.text() == "≥2 天" and "從開始記問題清單" in age.attributes["data-tip"]
    for gone in ("新出現：和", "之前就有", "什麼算要處理", "pn-tsub", "pn-pill"):
        assert gone not in tree.html


def test_mark_known_is_always_there_with_six_reasons_and_one_field_for_every_key():
    li = card([B]).css_first("ul.td > li")
    form = li.css_first("details.td-known form")
    assert li.css_first("details.td-known summary").text() == "標成已知" and form.attributes["action"] == "/todo/known"
    assert [b.text() for b in form.css(".tk-opts button")] == list(panorama.REASONS)
    assert all(b.attributes["type"] == "button" for b in form.css(".tk-opts button"))  # a pick never posts
    keys = form.css("input[name=keys]")
    assert len(keys) == 1 and keys[0].attributes["value"] == "site:b.example:export site:b.example:docs"
    assert form.css_first("input[name=note]").attributes["placeholder"] == "為什麼不用管（可不寫）"
    css = (LAYER / "home.css").read_text(encoding="utf-8")
    assert ".td .td-known:not([open]){opacity:1}" in css and "tk-fixed" in css  # shown, and never cut by the pane


def test_the_sources_own_code_and_an_unknown_one_stay_and_everything_is_escaped():
    odd = row("建庫", WARN, "part:build:x", href="/tonight#nights", code="exploded · mystery_code_7", unknown=True)
    li = card([odd, row("<b>壞</b>", WARN, "step:x", reason="<i>")]).css("ul.td > li")
    assert li[0].css_first(".td-code").text() == "exploded · mystery_code_7" and "沒見過" in li[0].text()
    assert li[0].attributes["data-sev"] == WARN and li[0].css_first(".td-kind").text() == "每晚流程"
    html = panorama.todo_card({"open": [row("<b>壞</b>", WARN, "step:x", reason="<i>")]}, False)
    assert "&lt;b&gt;壞&lt;/b&gt;" in html and "<b>壞</b>" not in html and "&lt;i&gt;" in html


def test_reading_waits_and_nothing_to_handle_is_said_in_one_line():
    waiting_card = card([A], reading=True)
    assert waiting_card.css_first("#pane-todo").text() == "正在讀各步驟的紀錄" and "甲處" not in waiting_card.html
    assert waiting_card.css_first("#tab-todo b") is None
    calm = card([])
    assert calm.css_first("#pane-todo .td-none").text() == "沒有要處理的" and calm.css_first("#tab-todo b") is None


def test_the_log_tab_is_a_viewer_over_the_api_never_lines_baked_in():
    tree = card([A])
    pane = tree.css_first("#pane-log")
    assert "hidden" in pane.attributes and pane.css_first(".lv").attributes["data-src"] == "/api/logs"
    assert pane.css_first("#lv-list").attributes["role"] == "log" and pane.css_first("#lv-more").text() == "再顯示 500 行"
    assert "lv-data" not in tree.html


def test_a_long_reason_is_cut_on_its_line_and_marked_to_be_read_whole():
    """OV-41: a reason in the source's own words is one line cut with …, its hover card the whole of it (data-tip-full:
    the layer never runs RICHTIP's 90-character cut over it); the card's ? too (its 「日誌：…」 part starts at 98)."""
    long = "原檔備份沒做完：" + "c.nknu.edu.tw/aca 的原檔還沒拷到備份碟，" * 5
    li = card([row("原檔備份", WARN, "part:warc_backup:copy_failed", reason=long)]).css_first("ul.td > li")
    issue = li.css_first(".td-issue")
    assert issue.attributes["data-tip"] == long and "data-tip-full" in issue.attributes and len(long) > 90
    helpb = card([A]).css_first("#todo .hp")
    assert "data-tip-full" in helpb.attributes and "日誌：" in helpb.attributes["data-tip"]
    vis = LexborHTMLParser(visits.card_html(visits.month(None, None, now=at("2026-11-23T15:00:00"))))
    assert "data-tip-full" in vis.css_first(".vs .hp").attributes
    paused = {"night": "2026-11-17", "cells": [c("crawl", "stop", "01:00", sev=BAD, say=long), c("export", "wait"),
                                               c("totext", "wait"), c("build", "wait"), c("swap", "wait")]}
    night = cal_tree(calendar.build(tonight(nights=[paused]), [])).css_first("a.wk-d[data-tip]")
    assert night.attributes["data-tip"] == f"爬取：{long}" and "data-tip-full" in night.attributes


def kept(path: Path, night: str, rows: list[dict]) -> None:
    """A night's list kept as the nights' record keeps it (history.Store.put_problems of standing.records)."""
    history.Store(path).put_problems(night, standing.records(rows), at=f"{night}T12:00:00+08:00")


@pytest.fixture
def ops_db(tmp_path) -> Path:
    path = tmp_path / "ops.db"
    store = history.Store(path)
    store.put_problems("2026-11-14", [{"key": "site:c.example:docs", "sev": WARN, "place": "丙處", "state": "",
                                       "reason": "庫裡 0 份文件", "href": "/site?site=c.example"}], at="x")
    store.put_problems("2026-11-15", [{"key": "step:serve", "sev": BAD, "place": "查詢服務", "state": "",
                                       "reason": "MCP 沒有回應", "href": "#step-serve"}], at="x")
    return path


def test_every_mark_is_kept_and_seen_through_the_records_sightings_each_marks_own(ops_db):
    """已知's sightings are the nights' record's (Store.problem_sightings, OV-45): the run day of the first kept list
    without the problem, across a night that kept no list; a site's mark covers its site's keys, any other mark only
    its own key (part:crawl is not part:crawl:dns)."""
    known = todo.Known(ops_db)
    known.mark(["site:c.example"], "網站改版中")
    known.mark(["step:serve"], "")
    known.mark(["machine:記憶體"], "在跑實驗")
    marks = known.marks()
    assert {m["key"] for m in marks} == {"site:c.example", "step:serve", "machine:記憶體"}
    assert all(m["at"] for m in marks)
    # 11-16 and 11-17 kept no list; 11-18 kept one without either
    kept(ops_db, "2026-11-18", [{"where": "乙處", "sev": WARN, "issues": [{"key": "site:b.example:docs"}]}])
    seen = todo.sightings(history.Store(ops_db), [m["key"] for m in marks])
    assert seen["site:c.example"]["night"] == "2026-11-14" and seen["site:c.example"]["place"] == "丙處"  # its site
    assert seen["site:c.example"]["gone_run_day"] == "2026-11-16"  # the run of night 11-15, the next list kept
    assert seen["step:serve"]["gone_run_day"] == "2026-11-19"  # no list on 11-16, 11-17: the first one without it
    assert "machine:記憶體" not in seen  # never on a kept list
    rows = todo.known_rows(marks, {"open": [], "known": []}, seen, "2026-11-18")
    by = {r["key"]: r for r in rows}
    assert by["step:serve"]["seen"] == "11-19 起沒再出現" and by["site:c.example"]["seen"] == "11-16 起沒再出現"
    assert by["step:serve"]["href"] == "/#usage"  # an old list's #step-serve leads to 網站訪問量 (CR-05)
    assert by["machine:記憶體"]["seen"] == "沒再出現"
    # another key under a mark that is not a site's is another problem: never its sighting
    kept(ops_db, "2026-11-19", [{"where": "爬取", "sev": BAD, "issues": [{"key": "part:crawl:dns_gave_up"}]}])
    assert "part:crawl" not in todo.sightings(history.Store(ops_db), ["part:crawl"])
    assert todo.sightings(history.Store(ops_db), ["part:crawl:dns_gave_up"])["part:crawl:dns_gave_up"][
        "gone_run_day"] is None  # the latest list still has it
    gone_now = todo.known_rows([{"key": "part:crawl:dns_gave_up", "note": "", "at": ""}], {"open": []},
                               todo.sightings(history.Store(ops_db), ["part:crawl:dns_gave_up"]))
    assert gone_now[0]["seen"] == "11-20 後沒再出現"  # the list of the 11-20 run had it; the list now does not
    assert todo.sightings(None, ["x"]) == {} and todo.sightings(history.Store(ops_db), []) == {}
    assert todo.Known(None).marks() == []


def test_known_rows_still_listed_first_then_the_day_each_stopped_showing():
    marks = [{"key": "site:c.example", "note": "網站改版中", "at": "2026-11-20T10:00:00+08:00"},
             {"key": "step:serve", "note": "", "at": "2026-11-19T10:00:00+08:00"},
             {"key": "site:a.example", "note": "外校", "at": "2026-11-18T10:00:00+08:00"},
             {"key": "machine:記憶體", "note": "", "at": "2026-11-17T10:00:00+08:00"}]
    listed = {"open": [], "known": [{**A, "kind": "網站"}]}
    seen = {"site:c.example": {"key": "site:c.example:docs", "night": "2026-11-14", "run_day": "2026-11-15",
                               "gone_run_day": "2026-11-17", "place": "丙處", "reason": "庫裡 0 份文件", "href": ""},
            "step:serve": {"key": "step:serve", "night": "2026-11-21", "run_day": "2026-11-22", "gone_run_day": None,
                           "place": "查詢服務", "reason": "MCP 沒有回應", "href": "#step-serve"}}
    rows = todo.known_rows(marks, listed, seen, "2026-11-21")
    assert [(r["name"], r["here"], r["seen"]) for r in rows] == [
        ("甲處", True, "今晚還在"),
        ("丙處", False, "11-17 起沒再出現"),  # the record's day: the first kept list without it (a night between kept none)
        ("查詢服務", False, "11-22 後沒再出現"),  # the latest list (the 11-22 run) had it, the list now does not
        ("記憶體", False, "沒再出現")]
    assert rows[0]["at"] == "11-18" and rows[0]["issue"] == "有 3 個網址抓不到" and rows[3]["kind"] == "這台電腦"
    assert todo.covers("site:a.example", "site:a.example:crawl") and not todo.covers("site:a.example", "site:a.example2:x")
    pane = LexborHTMLParser(panorama.todo_card({"open": []}, False, rows))
    assert pane.css_first("#tab-known .kn-n").text() == "4"
    assert [h.text() for h in pane.css("#pane-known .kn-h")] == ["還在出現 1", "沒再出現 3"]
    gone = pane.css("#pane-known li.kn-row[data-gone]")
    assert len(gone) == 3 and gone[0].css_first(".kn-seen").text() == "11-17 起沒再出現"
    first = pane.css_first("#pane-known li.kn-row")
    assert first.css_first(".kn-note").text() == "外校" and first.css_first(".kn-when").text().startswith("11-18 標的")
    off = first.css_first("form.kn-offf")
    assert off.attributes["action"] == "/todo/known" and off.css_first("input[name=off]").attributes["value"] == "1"
    assert off.css_first("input[name=keys]").attributes["value"] == "site:a.example"
    assert off.css_first("button.kn-off").text() == "取消已知"
    assert "沒寫原因" in gone[1].text()  # an empty note says so
    assert "kn-demo" not in pane.html and "示意" not in pane.html


# ---------------------------------------------------------------- the page


def snap(**more) -> dict:
    machine = [{"name": "E 槽", "tone": "ok", "words": "", "used": 487.0, "total": 1863.0},
               {"name": "記憶體", "tone": WARN, "words": "", "used": 57.0, "total": 62.6, "swap": 0.4},
               {"name": "顯示卡記憶體", "tone": "ok", "words": "", "used": 3.7, "total": 20.0},
               {"name": "C 槽", "tone": "none", "words": "讀不到", "used": None, "total": None}]
    return {"steps": [{"key": "serve", "nums": {"machine": machine, "usage": {"days": [["2026-11-23", 7, 5]]}}}],
            "sites": [], **more}


def front(sites: dict | None = None, s: dict | None = None, **kw) -> str:
    t = tonight("2026-11-23T09:00:00", nights=[clean("2026-11-22")])
    return panorama.page(s or snap(), t, sites if sites is not None else {"sites": [{"k": "a"}]}, None,
                         built="2026-11-23T03:09:00", todo={"open": [{**A, "kind": "網站"}], "known": []}, **kw)


def test_the_page_is_the_calendar_then_visits_beside_the_three_tabs_then_the_frame():
    page = front()
    tree = LexborHTMLParser(page)
    h1 = tree.css_first("h1#main-title")
    assert "visually-hidden" in h1.attributes["class"] and h1.text() == "總覽"
    main = tree.css_first("main")
    order = [x.attributes.get("id") or x.attributes.get("class") for x in main.iter() if x.tag != "h1"][:3]
    assert order[0] == "calendar" and order[1] == "mid-row" and order[2] == "sxFrame"
    assert [x.attributes.get("id") for x in tree.css(".mid-row > section")] == ["usage", "todo"]
    frame = tree.css_first("iframe#sxFrame")
    assert frame.attributes["src"] == "/sites?embed=1" and frame.attributes["title"] == "文件日期分佈"
    assert frame.attributes["loading"] == "lazy" and tree.css_first("template#mcBlock") is not None
    assert main.attributes["data-sig"] == "abc" and "<title>總覽 · 檢視台</title>" in page
    shown = main.html  # what the page draws
    for gone in ("全景", "學生搜到的庫", "AI 實測", "文件年份分佈", "文件從哪裡來", "最近 7 晚", "pn-say", "要處理 1 件",
                 "收錄網站", "該抓的抓到", "pn-kpis", "pn-divider"):
        assert gone not in shown, gone
    # nor do its styles carry the removed cards' rules (OV-85): only the card shell 內容's band and the night use too
    for dead in ("pn-gantt", "pn-gseg", "pn-kpi-link", "pn-ustats", "pn-mgrid", "pn-tlist", "pn-pop", "pn-divider"):
        assert dead not in page and dead not in panorama.CSS, dead
    for shell in (".pn-card{", ".pn-chd{", ".pn-chd h2{", ".pn-empty{", ".pn-bento{", ".pn-kpi{", "main{"):
        assert shell in panorama.CSS, shell
    assert "location.replace" not in page  # read: no blind reload (the layer asks for the night's signature)
    assert "id='design-richtip-home-js'" in page or "id='design-home-js'" in page
    assert "/api/usage/live" in page and "echarts" in page


def test_while_reading_the_page_reloads_soon_and_while_counting_it_says_so_with_the_machine_alone():
    reading = front(s=snap(steps=[]))
    assert "location.replace" in reading and "正在讀各步驟的紀錄" in reading
    waiting_page = LexborHTMLParser(front(sites={}))
    assert "網站的統計還在算" in waiting_page.css_first(".pn-wait").text() and waiting_page.css_first("iframe") is None
    assert "全景" not in waiting_page.css_first(".pn-wait").text()
    alone = waiting_page.css_first(".mc-alone")
    assert alone.css_first(".side-mc h2").text() == "這台電腦"  # this machine still shown, its title with it
    assert "pn-card" not in alone.attributes["class"]  # placed, never a card in a card (OV-70)
    assert "pn-card" not in LexborHTMLParser(front()).css_first("#mcHere").attributes["class"]


def test_the_machine_is_three_pictures_filling_like_water_the_projects_part_in_violet():
    block = panorama.machine_block(snap())
    tree = LexborHTMLParser(block)
    tiles = tree.css(".mc")
    assert [t.css_first(".mc-name").text() for t in tiles] == ["E 槽", "記憶體", "顯示卡", "C 槽"]
    disk, mem, gpu, unread = tiles
    assert disk.css_first(".mc-pct").text() == "26%" and disk.css_first(".mc-det").text() == "剩 1,376 / 1,863 GB"
    assert disk.css_first(".mc-water.mc-o") and disk.css_first(".mc-legend").text() == ""  # no split of a drive measured
    assert "本專案、Strata 和其他各占多少" in disk.attributes["data-tip"]
    # the Docker machine's memory is what it is, never 本專案: other containers run in it too (OV-70/72)
    assert mem.css_first(".mc-water.mc-p") and mem.css_first(".mc-legend").text() == "Docker 虛擬機 57.0 GB"
    assert mem.attributes["data-tip-h"] == "記憶體 · 用了 91%" and mem.attributes["data-tone"] == WARN
    assert "不只本專案的" in mem.attributes["data-tip"] and "本專案：" not in mem.attributes["data-tip"]
    assert gpu.attributes["data-tip-h"] == "顯示卡 · 用了 18%，本專案 18%"
    # each line of a tile's tip stays short: 內容's hover card in the frame cuts a line past 90 characters
    assert all(len(line) <= 90 for x in tiles for line in x.attributes["data-tip"].split("\n"))
    assert "交換區 0.4 GB" in mem.attributes["data-tip"]
    assert gpu.css_first(".mc-det").text() == "剩 16.3 / 20.0 GB" and "Strata 載入時會拿走約 14 GB" in gpu.attributes["data-tip"]
    assert unread.css_first(".mc-pct").text() == "—%" and unread.css_first(".mc-water") is None
    assert "<style id='mc-css'>" in block and "side-mc" in block  # carries its styles into the frame
    assert panorama.machine_block({"steps": []}) == ""


def probe_file(tmp_path: Path, at_: str, *, nknu: float = 68, docker: float = 180.6, strata: float = 153.8) -> Path:
    """The lab's measurement (deploy/lab-inspect/machine/probe.ps1) as it writes it: UTF-8 with a BOM, bytes."""
    gb = 1024 ** 3
    data = {"at": at_, "drives": {"E": {"total": 1863 * gb, "free": 1376 * gb,
                                        "parts": {"nknu": nknu * gb, "docker": docker * gb, "strata": strata * gb}}}}
    path = tmp_path / "machine.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode())
    return path


def test_a_drive_is_split_into_the_projects_stratas_and_the_rest_from_the_labs_own_measure(tmp_path, monkeypatch):
    """OV-70/71/84: how much of E: is the project's (the crawler's raw files E:\\nknu, Docker's data disk E:\\DockerData),
    the test model's (E:\\Strata) and the rest cannot be seen from inside Docker: the lab measures it every 30 minutes
    (machine/probe.ps1) and the panel draws it while the measure is under 3 hours old; older, the drive is one grey
    layer again and the tip says when it was last measured."""
    monkeypatch.setattr(pl, "memory", lambda: None)
    monkeypatch.setattr(pl, "gpu_memory", lambda: None)
    gb = 1024 ** 3
    monkeypatch.setattr(pl.shutil, "disk_usage", lambda p: SimpleNamespace(total=1863 * gb, free=1376 * gb, used=487 * gb))
    now = datetime(2026, 11, 23, 14, 0, tzinfo=TAIPEI)
    where = pl.Where(root=tmp_path, db=tmp_path / "aca.db", disks=(("E 槽", str(tmp_path)),),
                     machine=probe_file(tmp_path, "2026-11-23T13:30:00+08:00"))
    (row,) = pl.machine_read(where, now=now)
    assert row["parts"] == [["本專案", 248.6, "p"], ["Strata", 153.8, "s"], ["其他", 84.6, "o"]]
    assert row["probe"] == {"at": "2026-11-23T13:30:00+08:00", "nknu": 68.0, "docker": 180.6, "strata": 153.8}
    tile = LexborHTMLParser(panorama.machine_block({"steps": [{"key": "serve", "nums": {"machine": [row]}}]}))
    disk = tile.css_first(".mc")
    assert disk.attributes["data-tip-h"] == "E 槽 · 用了 26%，本專案 13%"
    assert disk.css_first(".mc-legend").text() == "本專案 249 GBStrata 154 GB"
    tip = disk.attributes["data-tip"]
    assert "爬蟲原檔 68 GB" in tip and "Docker 的磁碟 181 GB" in tip and "11-23 13:30 在實驗室電腦上量的" in tip
    assert all(len(line) <= 90 for line in tip.split("\n"))
    old = pl.Where(root=tmp_path, db=tmp_path / "aca.db", disks=(("E 槽", str(tmp_path)),),
                   machine=probe_file(tmp_path, "2026-11-23T09:00:00+08:00"))
    (stale,) = pl.machine_read(old, now=now)
    assert "parts" not in stale and stale["probe_old"] == "2026-11-23T09:00:00+08:00"
    tile = LexborHTMLParser(panorama.machine_block({"steps": [{"key": "serve", "nums": {"machine": [stale]}}]}))
    assert "上次量是 11-23 09:00，太久沒量了" in tile.css_first(".mc").attributes["data-tip"]
    nothing = pl.Where(root=tmp_path, db=tmp_path / "aca.db", disks=(("E 槽", str(tmp_path)),),
                       machine=tmp_path / "missing.json")
    assert "parts" not in pl.machine_read(nothing, now=now)[0]


def test_gather_reads_the_months_nights_the_days_and_every_mark(tmp_path, ops_db):
    store = history.Store(ops_db)
    store.put(record("2026-11-05"), at=at("2026-11-06T05:00:00"))
    kept(ops_db, "2026-11-20", [])  # a list kept without step:serve, two nights after the last one with it
    known = todo.Known(ops_db)
    known.mark(["step:serve"], "")
    t = tonight("2026-11-23T09:00:00", nights=[clean("2026-11-22")])
    more = panorama.gather(store, known, SimpleNamespace(public_log=None), t, snap(), {"open": [], "known": []})
    assert "2026-11-06" in more["calendar"]["nights"]  # the month reaches back past the seven readings
    assert more["visits"]["calls"][-1] == 7 and more["known"][0]["seen"] == "11-21 起沒再出現"  # today, last
    assert more["calendar"]["library"][0] == "11-22 02:22"  # the reading's night that swapped
    page = LexborHTMLParser(front(more=more))
    assert page.css_first("a.cal-d[href='/sites?date=2026-11-06']") is not None
    assert page.css_first("#tab-known .kn-n").text() == "1"
    assert calendar.span_for(at("2026-11-03T09:00:00").date()) == ("2026-10-27", "2026-11-02")


# ---------------------------------------------------------------- the layer


def test_the_layer_does_what_the_designs_scripts_did_with_live_data_and_none_of_the_copys_props():
    js = (LAYER / "home.js").read_text(encoding="utf-8")
    css = (LAYER / "home.css").read_text(encoding="utf-8")
    for live in ("/api/usage/live", "/api/logs", "/api/tonight", "Asia/Taipei", "RICHTIP", "tk-opts", "lv-maxed",
                 "sxFrame", "mcBlock", "dataZoom", "aria-selected", "ArrowRight", "#known".strip("#")):
        assert live in js, live
    for prop in ("persec", "lv-data", "onsubmit", "示意", "#design-copy", "data-style"):
        assert prop not in js and prop not in css, prop
    for gone in ("ga-key", "ic-pips", "tb-crawl", "uw-grid", "cal-demo", "kn-demo", "vs-ecg-demo", ".lg li"):
        assert gone not in css, gone
    assert "--cal-in:#3d2c9e" in css and "prefers-reduced-motion" in css
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(LAYER / "home.js")], capture_output=True, check=False).returncode == 0


def test_the_live_route_and_the_one_logs_route_answer_through_a_small_app_as_web_py_will_wire_them(tmp_path):
    """ROUTES_TO_WIRE's handler behind starlette, the way web.py adds it (JSON, no-store), and GET /api/logs wired to
    DATA's one handler (logs.api_window; this module has none of its own): asked with the names 日誌 here sends
    (date, lv, src, q, n, before), it filters by them."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from nknu.ops.pipeline import Where

    (tmp_path / "crawl").mkdir()
    (tmp_path / "crawl" / "update.log").write_text(
        "2026-11-23 01:00:02 定期更新开始\n2026-11-23 01:00:03,100 ERROR 抓取失败 3\n", encoding="utf-8")
    now = datetime.now(TAIPEI)
    log = write_log(tmp_path / "queries.jsonl", [{"t": (now - timedelta(seconds=3)).isoformat(), "tool": "search"}])
    where = Where(root=tmp_path, db=tmp_path / "aca.db", archive=tmp_path / "crawl", public_log=log)
    logs.reset()
    handlers = {"nknu.ops.visits.live_api": lambda r: visits.live_api(where),
                "nknu.ops.logs.api_window": lambda r: logs.api_window(where, r.query_params)}
    assert {r["handler"] for r in panorama.ROUTES_TO_WIRE} == {"nknu.ops.visits.live_api"}
    assert "/api/logs" not in {r["path"] for r in panorama.ROUTES_TO_WIRE} and not hasattr(panorama, "logs_api")
    assert any(r["path"] == "/api/logs" and r["handler"] == "nknu.ops.logs.api_window" for r in logs.ROUTES_TO_WIRE)

    def endpoint(handler):
        return lambda req: JSONResponse(handler(req), headers={"Cache-Control": "no-store"})

    routes = [Route("/api/usage/live", endpoint(handlers["nknu.ops.visits.live_api"])),
              Route("/api/logs", endpoint(handlers["nknu.ops.logs.api_window"]))]
    window = {"since": "2026-11-23T00:00:00", "until": "2026-11-23T02:00:00", "src": "crawl", "q": "", "n": "500"}
    with TestClient(Starlette(routes=routes)) as client:
        live = client.get("/api/usage/live").json()
        assert live["read"] and sum(live["per_sec"]) == 1
        page = client.get("/api/logs", params={**window, "lv": "error,warn,info"}).json()
        assert set(page) >= {"rows", "hist", "counts", "more"}
        assert any("抓取失败 3" in r[3] for r in page["rows"])  # the source's own words, never converted
        errors = client.get("/api/logs", params={**window, "lv": "error"}).json()
        assert errors["rows"] and {r[1] for r in errors["rows"]} == {"error"}  # lv= is read: the chips filter
    logs.reset()


def test_the_warc_backup_behind_is_a_row_to_handle_never_a_mark_on_the_night(tmp_path):
    """The raw files' nightly copy (nknu.archive.warcbackup, last in the night from the 11-24 run on): behind, it is
    one row of 待處理 in the status's own words, aged and markable as any (part:warc_backup:<why>); the calendar
    never reads it (owner 11-23: it must not make the night look failed)."""
    reports = tmp_path / "reports"
    reports.mkdir()
    status = reports / "warc-backup.json"

    def said(**kw) -> list[dict]:
        status.write_text(json.dumps({"state": "done", "started_at": "2026-11-24 04:00:00", "ended_at":
                                      "2026-11-24 04:20:00", "ok": True, "why": None, "message": "", **kw}), "utf-8")
        return todo.warc_rows(tmp_path, at("2026-11-24T15:00:00"))

    assert todo.warc_rows(tmp_path, at("2026-11-24T15:00:00")) == []  # before its first run: nothing to say
    assert said() == []  # done today, whole
    failed = said(ok=False, why="copy_failed", message="原档备份没做完：该拷 3 个，拷成 1 个；备份落后 2 个档")
    assert len(failed) == 1 and failed[0]["where"] == "原檔備份" and failed[0]["reason"].startswith("原檔備份沒做完")
    assert failed[0]["issues"][0]["key"] == "part:warc_backup:copy_failed" and not failed[0]["unknown"]
    assert todo.kind_of(failed[0]) == "每晚流程"
    stuck = said(state="running", ok=None, started_at="2026-11-24 04:00:00")
    assert "還說在拷" in stuck[0]["reason"] and stuck[0]["issues"][0]["key"] == "part:warc_backup:running"
    status.write_text(json.dumps({"state": "running", "started_at": "2026-11-24 14:30:00"}), "utf-8")
    assert todo.warc_rows(tmp_path, at("2026-11-24T15:00:00")) == []  # running now: not behind
    old = said(ended_at="2026-11-23 04:20:00")
    assert old[0]["reason"] == "上次備份完是 11-23 04:20，這一晚的還沒有" and old[0]["code"] == "stale"
    assert todo.warc_rows(tmp_path, at("2026-11-24T09:00:00")) == []  # before noon yesterday's copy is the last due
    odd = said(ok=False, why="disk_on_fire", message="x")
    assert odd[0]["unknown"] is True  # a code nobody here knows: listed as written, never read as normal
    row = todo.collect(None, failed, [], {})["open"][0]
    tree = LexborHTMLParser(panorama.todo_card({"open": [row]}, False))
    assert tree.css_first(".td-what b").text() == "原檔備份" and tree.css_first(".td-code").text() == "copy_failed"
    assert "warc" not in calendar.card_html(calendar.build(tonight(nights=[clean("2026-11-21")]), []))


def test_the_warc_row_is_on_the_one_list_kept_a_night_so_it_ages_and_its_mark_is_seen(tmp_path, monkeypatch):
    """CR-04: todo.night_rows is the list 待處理 shows and the nights' record keeps (tonight_page.problems, then the
    backup's row). Kept one night, the backup still behind the next is 第 2 天, not 新出現 every day; marked known,
    its sightings get a day once it is gone."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "warc-backup.json").write_text(json.dumps(
        {"state": "done", "ok": False, "why": "copy_failed", "message": "原档备份没做完", "started_at": "2026-11-23 04:00:00",
         "ended_at": "2026-11-23 04:20:00"}), "utf-8")
    seen = {}

    def problems(t, s, handle):
        seen["asked"] = (t, s, handle)
        return [{"sev": WARN, "where": "建庫", "href": "#nights", "issues": [{"key": "part:build:held", "sev": WARN}]}]

    monkeypatch.setattr(todo.tonight_page, "problems", problems)
    rows = todo.night_rows({"default": "2026-11-22"}, {"steps": []}, ["h"], tmp_path, at("2026-11-23T15:00:00"))
    assert seen["asked"] == ({"default": "2026-11-22"}, {"steps": []}, ["h"])
    assert [r["where"] for r in rows] == ["建庫", "原檔備份"]
    assert rows[1]["issues"][0]["key"] == "part:warc_backup:copy_failed"
    assert todo.night_rows({}, {}, [], None) == rows[:1]  # no crawl volume: the night's problems alone
    db = tmp_path / "ops.db"
    store = history.Store(db)
    store.put_problems("2026-11-22", standing.records(rows), at="2026-11-23T12:00:00+08:00")  # as the Recorder keeps it
    parts = standing.split(rows, store.problem_base("2026-11-23"))  # the next night, the backup still behind
    got = todo.collect(parts, rows, [], {}, night="2026-11-23")
    warc = next(x for x in got["open"] if x["where"] == "原檔備份")
    assert warc["tag"] == "≥2 天" and warc["tag"] != "新出現"  # aged: the list goes no further back than 11-22
    store.put_problems("2026-11-23", standing.records(rows), at="2026-11-24T12:00:00+08:00")
    third = standing.split(rows, store.problem_base("2026-11-24"))
    assert next(x for x in third["old"] if x["where"] == "原檔備份")["age"] == "≥3 天"
    todo.Known(db).mark(["part:warc_backup:copy_failed"], "備份碟換新")
    store.put_problems("2026-11-24", standing.records(rows[:1]), at="2026-11-25T12:00:00+08:00")  # copied again
    marks = todo.Known(db).marks()
    sight = todo.sightings(store, [m["key"] for m in marks])
    gone = todo.known_rows(marks, {"open": []}, sight)
    assert gone[0]["name"] == "原檔備份" and gone[0]["seen"] == "11-25 起沒再出現"


# ---------------------------------------------------------------- the layer, run (node, a stand-in page)

HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const [rt, hj, cfgJson] = process.argv.slice(2);
const cfg = JSON.parse(cfgJson);
const on = {}, frames = [], asked = [], els = {};
const texts = [];  // what the live canvas wrote (its axis labels)
const ctx2d = new Proxy({fillText: t => { texts.push(String(t)); }}, {get: (t, p) => (p in t ? t[p] : () => ({addColorStop() {}}))});
const el = (id, extra = {}) => {
  if (els[id]) return els[id];
  const attrs = {};
  const e = {id, hidden: true, textContent: '', innerHTML: '', dataset: {}, style: {}, clientWidth: 300, clientHeight: 200,
    width: 0, height: 0, attrs, setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    hasAttribute(k) { return k in attrs; }, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
    closest() { return e; }, getContext: () => ctx2d, ...extra};
  els[id] = e; return e;
};
const tip = el('tip', {hidden: false, offsetWidth: 100, offsetHeight: 40, querySelector() { return /rt-/.test(this.innerHTML) ? {} : null; }});
const ids = new Set(cfg.ids || []);
if (cfg.vsData) el('vs-data').textContent = JSON.stringify(cfg.vsData);
const button = v => el('btn-' + v, {dataset: {v}, addEventListener(t, f) { (this.on ||= {})[t] = f; }});
const vs = cfg.vsData ? el('vs', {dataset: {v: 'live'}, querySelector: () => el('vs-main'),
  querySelectorAll: s => (s === '.vs-switch button' ? [button('live'), button('day')] : [])}) : null;
let option = null;  // what 每日 gave ECharts (cfg.echarts: a stand-in that keeps it)
const document = {hidden: false, documentElement: {}, activeElement: null,
  body: {classList: {toggle() {}, contains: () => false}},
  getElementById: id => (ids.has(id) ? el(id) : null),
  querySelector: sel => (sel === 'body > .tip' ? tip : sel === '.vs' ? vs : null),
  querySelectorAll: () => [], addEventListener() {}};
const g = {document, URLSearchParams, console, setImmediate,
  addEventListener: (t, f) => { (on[t] ||= []).push(f); }, dispatchEvent() {},
  getComputedStyle: () => ({getPropertyValue: () => ''}),
  sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  location: {hash: '', reload() {}}, innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
  requestAnimationFrame: f => { frames.push(f); }, setTimeout: () => 1, setInterval: () => 1, clearTimeout() {},
  performance: {now: () => 1000}, MutationObserver: class { observe() {} }, ResizeObserver: class { observe() {} },
  Event: class { constructor(t) { this.type = t; } },
  fetch: async url => {
    asked.push(String(url));
    const r = (cfg.answers || {})[String(url).split('?')[0]];
    if (r === 'throw') throw new Error('unreachable');
    return r ? {ok: true, status: 200, json: async () => r} : {ok: false, status: 404, json: async () => ({})};
  }};
if (cfg.echarts) g.echarts = {init: () => ({setOption(o) { option = o; }, getZr: () => ({off() {}, on() {}}), resize() {}}),
  graphic: {LinearGradient: class {}}};
g.window = g;
vm.createContext(g);
vm.runInContext(fs.readFileSync(rt, 'utf8'), g);
vm.runInContext(fs.readFileSync(hj, 'utf8'), g);
const ticks = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
(async () => {
  await ticks();
  const out = {};
  out.tips = (cfg.tips || []).map((attrs, i) => {
    const e = el('t' + i); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    (on.pointermove || []).forEach(f => f({target: e, clientX: 10, clientY: 10}));
    return tip.innerHTML;
  });
  if (vs) {
    frames.slice().forEach(f => f(2000));
    out.live = ['vs-rps', 'vs-60', 'vs-peak'].map(id => String(el(id).textContent));
    out.none = el('vs-ecg-none').hidden;
    out.texts = texts.slice();
    out.quiet = {hidden: el('vs-ecg-quiet').hidden, text: String(el('vs-ecg-quiet').textContent)};
    out.day = String(el('vs-day').textContent);
    if (cfg.echarts) {
      els['btn-day'].on.click(); await ticks();
      out.dayTips = option ? (cfg.tipAt || []).map(i => option.tooltip.formatter([{dataIndex: i}])) : null;
      out.dayNow = String(el('vs-day').textContent);
    }
  }
  if (cfg.log) {
    const root = el('lvroot', {dataset: {src: '/api/logs', page: '500'}, querySelector: s => el('lv' + s)});
    g.LOGVIEW(root, {params: {date: '2026-11-22'}}).start();
    await ticks();
  }
  out.asked = asked;
  console.log(JSON.stringify(out));
})();
"""


def run_layer(tmp_path: Path, **cfg) -> dict:
    """home.js (after 內容's richtip.js, as design.layer loads them) on a stand-in page in node: what it did."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    got = subprocess.run([node, str(harness), str(LAYER / "richtip.js"), str(LAYER / "home.js"), json.dumps(cfg)],
                         capture_output=True, text=True, timeout=60, check=False)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout.strip().splitlines()[-1])


def test_a_prose_tip_is_shown_whole_and_a_number_tip_still_as_rows(tmp_path):
    """OV-41/20/30/48: RICHTIP cuts a prose line at 90 characters; a tip marked data-tip-full (a ? help, a reason,
    a night's card) is drawn whole in the same card's style, every other tip as RICHTIP draws it."""
    reason = "原檔備份沒做完：" + "c.nknu.edu.tw/aca 的原檔還沒拷到備份碟，" * 4 + "完"
    cal = calendar.HELP
    assert len(reason) > 90 and len(cal) > 116
    out = run_layer(tmp_path, tips=[{"data-tip": reason, "data-tip-full": ""},
                                    {"data-tip-h": "每晚流程", "data-tip": cal, "data-tip-full": ""},
                                    {"data-tip": reason},
                                    {"data-tip-h": "抓到", "data-tip": "抓到 1,234、沒抓到 56"}])
    whole, helped, cut, rows = out["tips"]
    assert reason in whole and "…" not in whole and "rt-p" in whole
    assert "週曆的外圈是 01:00–10:00" in helped and "<b class='rt-h'>每晚流程</b>" in helped
    assert reason not in cut and "…" in cut  # unmarked: RICHTIP's own drawing, cut as before
    assert "rt-r" in rows and "1,234" in rows


def test_the_live_line_says_unread_when_the_address_fails_never_zero(tmp_path):
    """OV-31: /api/usage/live not wired (404), unreachable, or the log unread: the card says 讀不到查詢紀錄 and the
    three numbers say 「—」; only a real answer draws the line and its numbers."""
    ids = ["vs-data", "vs-day", "vs-ecg", "vs-ecg-none", "vs-rps", "vs-60", "vs-peak"]
    data = {"days": {}, "pick": "", "dates": [], "calls": [], "labels": [], "today": "2026-11-23", "slots": 4}
    for answers in ({}, {"/api/usage/live": "throw"}, {"/api/usage/live": {"read": False, "end": 1000, "per_sec": []}}):
        out = run_layer(tmp_path, ids=ids, vsData=data, answers=answers)
        assert out["none"] is False and out["live"] == ["—", "—", "—"], answers
        assert out["asked"][0] == "/api/usage/live"
    good = {"/api/usage/live": {"read": True, "end": 1700000000, "per_sec": [2] * 330, "poll_ms": 3000}}
    out = run_layer(tmp_path, ids=ids, vsData=data, answers=good)
    assert out["none"] is True and out["live"] == ["2.0", "120", "2"]
    tree = LexborHTMLParser(visits.card_html(data))
    assert [tree.css_first(f"#{i}").text() for i in ("vs-rps", "vs-60", "vs-peak")] == ["—", "—", "—"]  # before it


def test_the_day_shown_first_is_the_last_one_over_not_todays_few_calls(tmp_path):
    """Live 11-23 (owner: 「網站訪問量那個卡片應該有bug」): 每日 opened on today, 9 calls by 05:30, and gave as the day's
    numbers P90 19.5 s (the first search after a restart loads the models) and 負荷率 23%. The day shown first is the
    last one that is over; today stays one click away, and is shown first only when no day before it has calls."""
    log = write_log(tmp_path / "q.jsonl", [call(f"2026-11-23T05:30:{s:02d}+08:00", ms=19503 if s == 0 else 7)
                                           for s in range(9)])
    store = Days([{"day": "2026-11-21", "calls": 31, "qpm_peak": 10}, {"day": "2026-11-22", "calls": 1800,
                  "search": 1410, "qpm_peak": 34, "conc_peak": 2, "load_p95": 0.082, "p90_ms": 3818}])
    data = visits.month(store, log, now=at("2026-11-23T13:00:00"))
    assert data["pick"] == "2026-11-22" and data["days"]["2026-11-23"]["calls"] == 9  # today still drawn and pickable
    assert visits.month(Days([]), log, now=at("2026-11-23T13:00:00"))["pick"] == "2026-11-23"  # nothing before it


def test_the_live_axis_says_its_steps_as_they_are_and_a_quiet_window_says_when_the_last_call_was(tmp_path):
    """Live 11-23: with nobody calling the axis read 0.0 / 0.3 / 0.5 (0.25 rounded to one place), and the card was a
    flat line with no word of when anyone last called. The steps are said as they are, and a window with no call says
    so with the last call's time (live(): 'last', from the log's end, also when it is older than the window)."""
    now = at("2026-11-23T13:00:00")
    log = write_log(tmp_path / "queries.jsonl", [{"t": "2026-11-23T05:30:38+08:00", "tool": "fetch", "ms": 9}])
    got = visits.live(log, now)
    assert sum(got["per_sec"]) == 0 and got["last"] == "2026-11-23T05:30:38+08:00"
    assert visits.live(tmp_path / "none.jsonl", now)["last"] is None
    ids = ["vs-data", "vs-day", "vs-ecg", "vs-ecg-none", "vs-ecg-quiet", "vs-rps", "vs-60", "vs-peak"]
    data = {"days": {}, "pick": "", "dates": [], "calls": [], "labels": [], "today": "2026-11-23", "slots": 4}
    quiet = {"/api/usage/live": {**got, "per_sec": [0] * 330}}
    out = run_layer(tmp_path, ids=ids, vsData=data, answers=quiet)
    assert [t for t in out["texts"] if t[0].isdigit()] == ["0", "0.25", "0.5"]
    assert out["quiet"] == {"hidden": False, "text": "最近 5 分鐘沒有人查 · 最後一次 11/23 05:30"}
    busy = {"/api/usage/live": {**got, "per_sec": [2] * 330}}
    out = run_layer(tmp_path, ids=ids, vsData=data, answers=busy)
    assert out["quiet"]["hidden"] is True and [t for t in out["texts"] if t[0].isdigit()] == ["0", "1", "2"]


def test_a_day_still_to_come_says_so_not_no_record(tmp_path):
    """每日's hover on a day after today said 「沒有紀錄」 as on a day before the log began; it says 還沒到."""
    dates = [f"2026-11-{d:02d}" for d in range(20, 26)]
    data = {"days": {"2026-11-22": {"calls": 1800}}, "pick": "2026-11-22", "dates": dates,
            "labels": [f"11/{d}" for d in range(20, 26)], "calls": [None, None, 1800, 9, None, None],
            "today": "2026-11-23", "slots": 4}
    ids = ["vs-data", "vs-day", "vs-chart", "vs-load", "vs-meter", "vs-qpm", "vs-qpm-at", "vs-conc", "vs-p90",
           "vs-lat-card", "vs-err"]
    out = run_layer(tmp_path, ids=ids, vsData=data, echarts=True, tipAt=[0, 2, 3, 4])
    before, full, today, ahead = out["dayTips"]
    assert "沒有紀錄" in before and "1,800 次" in full and "今天還沒過完" in today
    assert "還沒到" in ahead and "沒有紀錄" not in ahead


def test_the_log_viewer_asks_with_the_one_handlers_names_and_the_reload_check_asks_brief(tmp_path):
    """OV-50: 日誌 sends logs.api_window's names (date, lv, src, q, n), the ones 內容's night log sends; OV-82: the
    60-second check asks /api/tonight?brief=1 (the signature, not the comparison's lists)."""
    from urllib.parse import parse_qs, urlsplit

    out = run_layer(tmp_path, log=True, answers={"/api/logs": {"rows": [], "hist": [], "counts": {}, "more": False}})
    url = next(u for u in out["asked"] if u.startswith("/api/logs"))
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    assert set(q) == {"date", "lv", "src", "q", "n"} and q["date"] == "2026-11-22" and q["n"] == "500"
    assert q["lv"] == "error,warn,info" and q["src"] == "crawl,export,totext,build,ingest"
    js = (LAYER / "home.js").read_text(encoding="utf-8")
    assert "'/api/tonight?brief=1'" in js and "levels:" not in js and "limit:" not in js
