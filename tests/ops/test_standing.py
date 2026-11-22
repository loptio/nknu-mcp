"""新出現, 之前就有, 好了 (nknu.ops.standing, 2026-11-21): the home's problems against the list kept for the night
before. A review of the panels as an ops panel found a site down for two weeks and a site that broke last night lit
alike. What these tests keep: what a problem is from one night to the next, the three piles, how long one has been
listed (and that the count says when it cannot know), that a list is kept only of a finished night from a reading made
after it, and that it then stays what the night left. Made-up sites and night files: no real data."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import history, home_page, panorama, standing, todo, tonight_page
from nknu.ops.pipeline import BAD, PIPE, WARN, Where
from tests.ops.test_history import Fixed, two_nights
from tests.ops.test_nightly import NIGHT, at, crawler_log, library

TONIGHT = {"now": "2026-11-17T09:00:00+08:00", "default": "2026-11-16", "live": False, "sig": "x",
           "nights": [{"night": "2026-11-16", "label": "11-16 夜", "state": "warn", "runs": [
               {"run": 2, "outcome": "failed", "reason": "建庫出錯", "until": "2026-11-17T05:00:00+08:00"}],
               "cells": [{"key": "export", "name": "導出", "state": "stop", "sev": "warn", "word": "停了",
                          "reason": "有 2 站沒導出", "code": "export_held", "until": "2026-11-17T02:00:00+08:00"}]}]}


def site(key: str, name: str, *issues: tuple[str, str, str]) -> dict:
    """A row of 流水線's table with cells to look at: (column, state, why)."""
    return {"key": key, "name": name, "href": f"/site?site={key}", "worst": WARN, "why": [],
            "cells": {"docs": {"n": 5}}, "issues": [{"col": c, "state": s, "why": w} for c, s, w in issues]}


def rows_of(snap: dict, changes: dict | None = None, t: dict = TONIGHT) -> list[dict]:
    return tonight_page.problems(t, snap, tonight_page.handle_rows(snap, changes))


SNAP = {"steps": [{"key": "serve", "name": "查詢服務", "state": BAD, "say": "MCP 沒有回應", "at": ""}],
        "sites": [site("a.example", "甲處", ("crawl", WARN, "有 3 個網址抓不到"), ("docs", WARN, "庫裡一份也沒有")),
                  site("b.example", "乙處", ("export", BAD, "導出失敗"))]}


def keys(rows: list[dict]) -> list[str]:
    return [it["key"] for x in rows for it in x["issues"]]


def test_every_row_says_what_it_is_from_one_night_to_the_next():
    changes = {"state": "ready", "sites": [{"site": "a.example", "name": "甲處", "unexplained": 4, "look": 0}]}
    got = keys(rows_of(SNAP, changes))
    assert set(got) == {"part:export:export_held", "run:2026-11-16:2", "step:serve", "site:a.example:unexplained",
                        "site:a.example:crawl", "site:a.example:docs", "site:b.example:export"}
    assert len(got) == len(set(got))
    recs = {r["key"]: r for r in standing.records(rows_of(SNAP, changes))}
    assert recs["site:a.example:crawl"] == {"key": "site:a.example:crawl", "sev": WARN, "place": "甲處", "state": "出錯",
                                            "reason": "有 3 個網址抓不到", "href": "/site?site=a.example"}
    assert recs["step:serve"]["reason"] == "MCP 沒有回應" and recs["part:export:export_held"]["place"] == "導出"


def base_of(rows: list[dict], night: str = "2026-11-15", runs: dict | None = None) -> dict:
    recs = {r["key"]: r for r in standing.records(rows)}
    return {"night": night, "at": "2026-11-16T05:00:00+08:00", "rows": recs,
            "runs": runs if runs is not None else {k: (1, True) for k in recs}}


def test_what_the_night_before_did_not_list_is_new_and_the_rest_says_how_long():
    before = {"steps": [], "sites": [site("a.example", "甲處", ("crawl", WARN, "有 3 個網址抓不到")),
                                     site("c.example", "丙處", ("docs", WARN, "庫裡一份也沒有"))]}
    base = base_of(rows_of(before, t={**TONIGHT, "nights": []}),
                   runs={"site:a.example:crawl": (3, False), "site:c.example:docs": (1, True)})
    parts = standing.split(rows_of(SNAP), base)
    assert parts["night"] == "2026-11-15"
    assert [x["where"] for x in parts["new"]] == ["進庫第 2 次", "查詢服務", "乙處", "導出", "甲處"]
    a = next(x for x in parts["new"] if x["where"] == "甲處")
    # 甲處 was listed for its crawl; what is new of it (its documents) leads its row, the old one is counted
    assert a["reason"] == "庫裡一份也沒有（還有 1 件）" and not a["worse"] and "age" not in a
    assert parts["old"] == []
    assert [g["place"] for g in parts["gone"]] == ["丙處"]  # listed the night before, not there now
    # nothing new of 甲處: it is old, with its nights (three before, this one the fourth)
    same = standing.split(rows_of({"steps": [], "sites": before["sites"][:1]}, t={**TONIGHT, "nights": []}), base)
    assert same["new"] == [] and same["old"][0]["age"] == "第 4 晚" and "連續 4 晚" in same["old"][0]["age_tip"]
    assert standing.age_words(2, True)[0] == "至少 2 晚"  # the lists go no further back: never a made-up first night


def test_a_problem_that_went_from_warn_to_bad_is_new_again_and_marked():
    before = {"steps": [{"key": "serve", "name": "查詢服務", "state": WARN, "say": "回得慢", "at": ""}], "sites": []}
    now = {"steps": [{"key": "serve", "name": "查詢服務", "state": BAD, "say": "MCP 沒有回應", "at": ""}], "sites": []}
    quiet = {**TONIGHT, "nights": []}
    parts = standing.split(rows_of(now, t=quiet), base_of(rows_of(before, t=quiet)))
    assert [x["where"] for x in parts["new"]] == ["查詢服務"] and parts["new"][0]["worse"] and parts["gone"] == []
    assert "變嚴重" in tonight_page.problem_html(parts["new"][0])
    back = standing.split(rows_of(before, t=quiet), base_of(rows_of(now, t=quiet)))  # bad to warn: not new
    assert back["new"] == [] and back["old"][0]["age"] == "至少 2 晚"


def test_nothing_is_said_gone_while_the_steps_are_read_and_a_run_is_never_gone():
    base = base_of(rows_of(SNAP))
    quiet = {**TONIGHT, "nights": []}
    assert "run:2026-11-16:2" in base["rows"]
    reading = standing.split(rows_of({"steps": []}, t=quiet), base, reading=True)
    assert reading["gone"] == []
    done = standing.split(rows_of({"steps": [], "sites": []}, t=quiet), base)
    assert sorted(g["key"] for g in done["gone"]) == ["part:export:export_held", "site:a.example:crawl",
                                                     "site:a.example:docs", "site:b.example:export", "step:serve"]
    assert done["gone"][0]["sev"] == BAD  # the worse first
    assert standing.split(rows_of(SNAP), None) is None and standing.split(rows_of(SNAP), {}) is None


def test_what_the_swaps_comparison_found_waits_for_the_comparison():
    found = {"state": "ready", "sites": [{"site": "a.example", "name": "甲處", "unexplained": 4, "look": 2}]}
    quiet = {**TONIGHT, "nights": []}
    empty = {"steps": [], "sites": []}
    base = base_of(rows_of(empty, found, quiet))
    # while tonight's comparison is being made its findings are not in the rows: not gone
    assert standing.split(rows_of(empty, t=quiet), base, swap_known=False)["gone"] == []
    assert len(standing.split(rows_of(empty, t=quiet), base)["gone"]) == 2


# ---------------------------------------------------------------- the lists a night


@pytest.fixture
def store(tmp_path_factory) -> history.Store:
    return history.Store(tmp_path_factory.mktemp("ops") / "ops.db")


def rec(key: str, sev: str = WARN) -> dict:
    return {"key": key, "sev": sev, "place": key, "state": "留意", "reason": "x", "href": ""}


def test_a_nights_list_is_kept_whole_and_the_night_before_is_what_the_home_compares_with(store):
    assert store.problem_base("2026-11-16") is None and store.problems_kept("2026-11-15") is None
    assert store.put_problems("2026-11-13", [rec("a"), rec("b")], at="2026-11-14T05:00:00+08:00") is True
    assert store.put_problems("2026-11-14", [rec("a")], at="2026-11-15T05:00:00+08:00") is True
    assert store.put_problems("2026-11-15", [rec("a"), rec("c", BAD)], at="2026-11-16T05:00:00+08:00") is True
    assert store.put_problems("2026-11-15", [rec("c", BAD), rec("a")], at="2026-11-16T05:10:00+08:00") is False
    assert store.problems_kept("2026-11-15") == "2026-11-16T05:00:00+08:00"  # unchanged: not written again
    base = store.problem_base("2026-11-16")
    assert base["night"] == "2026-11-15" and base["at"] == "2026-11-16T05:00:00+08:00"
    assert set(base["rows"]) == {"a", "c"} and base["rows"]["c"]["sev"] == BAD
    # a: three nights and the lists go no further back (open); c: this night only, the night before did not have it
    assert base["runs"] == {"a": (3, True), "c": (1, False)}
    assert store.problem_base("2026-11-15")["night"] == "2026-11-14"  # never a night's own list
    # an empty list is a list: nothing was wrong that night, and what it had the night before is gone
    assert store.put_problems("2026-11-16", [], at="2026-11-17T05:00:00+08:00") is True
    assert store.problem_base("2026-11-17") == {"night": "2026-11-16", "at": "2026-11-17T05:00:00+08:00",
                                                 "rows": {}, "runs": {}}


def test_a_night_with_no_list_between_two_ends_the_count_as_open(store):
    store.put_problems("2026-11-12", [rec("a")], at="x")
    store.put_problems("2026-11-14", [rec("a")], at="x")  # 11-13: 檢視台 was not running, nothing is known of it
    store.put_problems("2026-11-15", [rec("a")], at="x")
    assert store.problem_base("2026-11-16")["runs"] == {"a": (2, True)}


def test_old_lists_are_dropped_and_a_store_that_cannot_write_keeps_none(store):
    store.put_problems("2026-06-01", [rec("a")], at="x")
    store.put_problems("2026-11-15", [rec("a")], at="x")
    assert store.problems_kept("2026-06-01") is None and store.problems_kept("2026-11-15") == "x"
    off = history.Store(None)
    assert off.put_problems("2026-11-15", [rec("a")], at="x") is False
    assert off.problem_base("2026-11-16") is None and off.problems_kept("2026-11-15") is None


# ---------------------------------------------------------------- the recorder keeps a finished night's list


class Flow:
    """A stand-in for the app's Pipeline: the reading it was given, and how often a new one was asked for."""

    def __init__(self, snap: dict | None = None) -> None:
        self.snap, self.asked = snap, 0

    def last(self) -> dict | None:
        return self.snap

    def snapshot(self, fresh: bool = False) -> dict:
        self.asked += 1
        return self.snap or {"steps": []}


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db", docs=[("d1", "notice", "甲", "https://a.example/1", "內文", "2026-11-01",
                                                    "a.example")], sites=[("a.example", "甲處", 1, "exp/a")])
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def reading(began: str, **more) -> dict:
    return {"at": began, "began": began, "steps": [{"key": "serve", "name": "查詢服務", "state": WARN, "say": "回得慢",
                                                    "at": ""}], "sites": [], **more}


def test_a_list_is_kept_of_a_finished_night_from_a_reading_made_after_it(where, store):
    two_nights(where)  # 11-16's chain ended 03:40
    flow = Flow()
    keeper = history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None), flow=flow)
    keeper.tick(now=at("2026-11-17T04:00:00"))
    assert store.problems_kept("2026-11-16") is None and flow.asked == 1  # no reading yet: one is asked for
    flow.snap = reading("2026-11-17T03:00:00+08:00")  # read while the chain ran: the run's, not the night's
    keeper.tick(now=at("2026-11-17T04:05:00"))
    assert store.problems_kept("2026-11-16") is None and flow.asked == 2
    flow.snap = reading("2026-11-17T04:06:00+08:00")
    keeper.tick(now=at("2026-11-17T04:10:00"))
    assert store.problems_kept("2026-11-16") == "2026-11-17T04:06:00+08:00" and keeper.last["problems"] is True
    assert set(store.problem_base("2026-11-17")["rows"]) == {"step:serve"}
    # within the hour after the night ended a later reading still reaches it (a follow-up, the swap's comparison)
    flow.snap = reading("2026-11-17T04:30:00+08:00", steps=[])
    keeper.tick(now=at("2026-11-17T04:31:00"))
    assert store.problems_kept("2026-11-16") == "2026-11-17T04:06:00+08:00"  # a reading with no steps is no reading
    flow.snap = reading("2026-11-17T04:35:00+08:00", sites=[site("a.example", "甲處", ("docs", WARN, "少了"))])
    keeper.tick(now=at("2026-11-17T04:36:00"))
    assert set(store.problem_base("2026-11-17")["rows"]) == {"step:serve", "site:a.example:docs"}
    # after that it stays what the night left: what breaks in the afternoon is new the morning after, not old
    flow.snap = reading("2026-11-17T15:00:00+08:00", steps=[{"key": "build", "name": "建庫", "state": BAD, "say": "壞了"}])
    keeper.tick(now=at("2026-11-17T15:01:00"))
    assert set(store.problem_base("2026-11-17")["rows"]) == {"step:serve", "site:a.example:docs"}


def test_no_list_is_kept_while_a_night_runs_or_the_comparison_is_being_made(where, store):
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    flow = Flow(reading("2026-11-17T01:35:00+08:00"))
    keeper = history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None), flow=flow)
    keeper.tick(now=at("2026-11-17T01:40:00"))
    assert store.problems_kept("2026-11-16") is None and flow.asked == 0
    two_nights(where)
    flow.snap = reading("2026-11-17T04:06:00+08:00")
    busy = history.Recorder(store, where, changes=Fixed({"state": "computing"}), kinds=Fixed(None), flow=flow)
    busy.tick(now=at("2026-11-17T04:10:00"))
    assert store.problems_kept("2026-11-16") is None
    history.Recorder(store, where, changes=Fixed(None), kinds=Fixed(None)).tick(now=at("2026-11-17T04:10:00"))
    assert store.problems_kept("2026-11-16") is None  # no pipeline given (a test's app): nothing kept, nothing broken


# ---------------------------------------------------------------- the page


def front(snap: dict, t: dict, base: dict | None) -> LexborHTMLParser:
    """總覽's 要處理 of a reading against the list kept for the night before (where the home's list went on
    2026-11-22 evening; 每晚運行 keeps the counts in its sentence)."""
    rows = rows_of(snap, t=t)
    parts = standing.split(rows, base)
    return LexborHTMLParser(panorama.todo_card(todo.collect(parts, rows, [], {}), False))


def test_the_home_lists_what_is_new_first_and_says_so_on_top():
    before = {"steps": [], "sites": [site("a.example", "甲處", ("crawl", WARN, "有 3 個網址抓不到")),
                                     site("c.example", "丙處", ("docs", WARN, "庫裡一份也沒有"))]}
    quiet = {**TONIGHT, "nights": [{**TONIGHT["nights"][0], "cells": [], "runs": []}]}
    base = base_of(rows_of(before, t=quiet), runs={"site:a.example:crawl": (2, False), "site:c.example:docs": (1, True)})
    snap = {"steps": SNAP["steps"], "sites": [site("a.example", "甲處", ("crawl", WARN, "有 3 個網址抓不到")),
                                              SNAP["sites"][1]]}
    tree = front(snap, quiet, base)
    items = tree.css("#todo > .pn-tlist li")
    assert [li.css_first(".pn-twhat a").text() for li in items] == ["查詢服務", "乙處", "甲處"]  # the new first
    assert [li.css_first(".pn-tnew, .pn-tage").text() for li in items] == ["新出現", "新出現", "第 3 晚"]
    assert "和 11-15 夜比" in tree.css_first(".pn-tsub").text() and tree.css_first(".pn-tcount").text() == "3"
    # what the night before listed and is not there now (好了) is nothing to handle: 總覽 does not list it
    assert "丙處" not in tree.html
    page = home_page.home_body(snap, quiet, None, {"enabled": False}, {}, base=base)
    assert "；有 3 個問題，其中新出現 2 個" in page and "id='problems'" not in page
    # nothing new: no row marked so, and said on top
    fine = [{"key": "serve", "name": "查詢服務", "state": "ok", "say": "", "at": ""}]
    calm = {"steps": fine, "sites": snap["sites"][:1]}
    assert front(calm, quiet, base).css(".pn-tnew") == [] and len(front(calm, quiet, base).css(".pn-tage")) == 1
    assert "；有 1 個問題，都是之前就有的" in home_page.home_body(calm, quiet, None, {"enabled": False}, {}, base=base)
    # no night to compare with: the rows as they were, nothing called new (the note why was 問題's; the section left)
    plain = front(snap, quiet, None)
    assert plain.css(".pn-tnew, .pn-tage") == [] and plain.css_first(".pn-tsub") is None
    said = home_page.home_body(snap, quiet, None, {"enabled": False}, {}, base_note="問題清單從這一晚開始記")
    assert "；有 3 個問題" in said and "其中新出現" not in said
