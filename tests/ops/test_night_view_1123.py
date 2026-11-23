"""定時更新, one night inside 內容 (nknu.ops.night_view, theme/design/night.js and night.css; the owner's design copy
research/design_2026-11-23/sites.html and its scripts content_night.py, night.py, nm_site.py). Made-up nights in the
shapes the crawler's nightly script, the ingest, the crawler's coverage step and night_sites.py write, a made-up
reading of the steps and comparison of the libraries: no real data."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import design, history, logs, night_view, nightly, sources, tonight_page
from nknu.ops import pipeline as pl
from nknu.ops.pipeline import PIPE, Step, Where
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, library, run

NOW = at("2026-11-17T09:00:00")  # the night of 11-16 (its run day 11-17) has ended; it is the one 內容 opens on
SITES = [("a.example", "甲處", 40, "exp/a"), ("c.example/aca", "教務處", 10, "exp/c"),
         ("c.example/tcsl", "華語所", 5, "exp/t")]
KEYS = {k for k, *_ in SITES}


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db", sites=SITES)
    (tmp_path / "crawl").mkdir()
    w = Where(root=tmp_path, db=db, archive=tmp_path / "crawl")
    crawler_log(w, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(w)
    ingest(w, [run("swapped")], SAID)
    return w


@pytest.fixture
def store(tmp_path, where) -> history.Store:
    s = history.Store(tmp_path / "ops" / "ops.db")
    history.Recorder(s, where).tick(now=NOW)
    return s


def snapshot() -> dict:
    """A reading of the steps (pipeline.Pipeline.snapshot's shape), in the words before the 11-23 rename where the
    reading may still carry them."""
    steps = [
        Step("crawl", "爬取", "爬蟲抓", into=[("個這一輪該抓的網址（3 站）", 120)], out=[("個抓到了", 100)],
             lost=[("校方已移除", 12), ("待確認：內容過少", 8)], lost_keys=[["gone", "校方已移除", 12],
                                                                       ["review:thin", "待確認：內容過少", 8]],
             lost_label="還沒抓到的（照爬蟲自己的分類）", lost_title="未取得",
             sides=[["校外網站每週重看", "ok", "輪到 3 站"], ["要用瀏覽器開的網站", "warn", "沒開"]]),
        Step("export", "導出", "導出清單", into=[("筆（每筆一個網頁、檔案或圖片）", 200)], out=[("筆存下了檔案", 110)],
             lost=[("學校回答「沒有資料」", 3), ("頁面裡的內嵌頁或按鈕沒抓到", 9), ("學校回答沒資料", 2)],
             lost_label="清單上註明缺東西的（一筆可能缺好幾樣）",
             link=[["", "爬取抓到的網址", 100, ""], ["+", "第二輪這一批抓到的（爬取那一步另外算）", 10, ""],
                   ["=", "清單上「抓到了」的", 110, "抓到了：100\n這是照帳本裡的種類分的，沒有逐個到清單裡核對它記在哪一筆上；要逐個核對，請爬蟲跑它的核對腳本"]]),
        Step("totext", "轉文字", "轉", into=[("個檔和網頁", 110)], out=[("個轉成文字", 104)],
             lost=[("讀不出文字", 5), ("照規則跳過", 1)], sides=[["x", "ok", "not the crawl's"]]),
        Step("build", "建庫", "建", into=[("筆整理前的網頁、檔案和行事曆（3 站）", 104)],
             out=[("份文件", 70), ("個小段（搜尋時比對的單位）", 600)], share=False,
             lost=[("別處有一模一樣的一份，合併了", 30)], lost_label="沒成為文件的（大多是合併掉的重複）"),
        Step("nightly", "每晚進庫", "進", out=[("份文件（換前）", 68), ("份文件（換後）", 70)])]
    sites = [{"key": "a.example", "name": "甲處", "cells": {"crawl": {"n": 30, "whole": 33, "lost": [
        ["failed", "連接失敗", 2], ["gone", "學校那邊已經不存在", 1]]}, "export": {"n": 50}, "text": {"n": 44},
        "docs": {"n": 40}}}]
    return {"at": "2026-11-17T08:00:00+08:00", "steps": [asdict(s) for s in steps], "sites": sites}


def doc(i: int, site: str = "a.example") -> dict:
    return {"id": f"d{i}", "title": f"公告 {i}", "url": f"https://{site}/n/{i}", "kind": "notice", "date": None, "chars": 9}


def comparison(night: str = "2026-11-16") -> dict:
    """Changes.snapshot() of a swap (changes.compare's shape, ready)."""
    a = {"site": "a.example", "name": "甲處", "new": 15, "changed": 1, "left": 2, "unexplained": 1, "look": 0,
         "lists": {"new": [doc(i) for i in range(15)], "changed": [{**doc(99), "what": ["標題"]}],
                   "left": {"unknown": [doc(50)], "copy": [doc(51)]}}}
    return {"state": "ready", "night": night, "totals": {"new": 15, "changed": 1, "left": 2, "unexplained": 1, "look": 0},
            "sites": [a], "by_reason": {"copy": 1, "unknown": 1},
            "reasons": {"unknown": {"label": "查不出原因", "kind": "unknown"}, "copy": {"label": "併成副本", "kind": "ok"}},
            "prev": {"docs": 68}, "new": {"docs": 70}}


def coverage(where: Where, night: str, sites: dict) -> None:
    path = where.archive / "reports" / "coverage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"night": night, "ledger": "x", "written_at": f"{night}T03:30:00+08:00", "sites": sites})
                + "\n")


def night_file(where: Where, day: str, swaps: list[dict]) -> None:
    folder = where.library.parent / "nights"
    folder.mkdir(exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps({"day": day, "swaps": swaps}), encoding="utf-8")


SWAP_SITES = {"a.example": {"export_rows": 120, "totext": {"ok": 100, "empty": 5, "failed": 1}, "records": 100,
                            "docs": 40, "docs_before": 38},
              "c.example/aca": {"export_rows": None, "totext": {"ok": 10}, "records": 12, "docs": 10,
                                "docs_before": 10}}
COVER = {"a.example": {"buckets": {"got": 90, "failed": 4, "review": 6, "rule": 10}, "review": {"thin": 6}},
         "c.example/aca": {"buckets": {"got": 20, "gone": 2}},
         "elsewhere.example": {"buckets": {"got": 999}}}


# ---------------------------------------------------------------- one night: its name, head, steps and problems

def test_a_night_is_named_by_its_run_day_and_drawn_from_its_five_cells(where, store):
    got = night_view.data(where, store, None, now=NOW)
    n = got["n"]
    # 「名字按开跑那天」: the chain that ran on the morning of 11-17 is 11/17, a Tuesday; ops.db keys it 11-16
    assert (got["run"], got["night"], got["default"]) == ("2026-11-17", "2026-11-16", "2026-11-17")
    assert (n["label"], n["wd"]) == ("11/17", "二")
    assert list(n["cells"]) == ["crawl", "export", "totext", "build", "swap"]
    assert n["cells"]["export"]["say"] == "導了 35 站" and n["cells"]["swap"]["state"] == "done"
    # the span is the record's (ops.db: the first start, the last end)
    rec = store.get("2026-11-16")
    assert n["started"] == rec["started_at"] == "2026-11-17T01:00:02+08:00" and n["ended"] == rec["ended_at"]
    assert n["probs"] == [] and not n["live"]
    assert got["nav"] == [{"run": "2026-11-17", "night": "2026-11-16", "label": "11/17", "wd": "二"}]
    # nothing kept of its steps and no comparison: said, never a blank
    assert got["flow"] == {"kind": "none", "lacks": "這一晚的輸入、輸出沒留下", "steps": {}}
    assert got["changes"]["kind"] in ("none", "counts") and not got["latest"]
    brief = night_view.api(where, store, {"brief": "1"}, now=NOW)
    assert brief == {"run": "2026-11-17", "sig": got["sig"], "live": False, "reading": False}
    # ops.db keeps the nights and has this one: nothing to say under the head, no pause standing
    assert got["store"] == {"on": True, "why": ""} and got["note"] == "" and got["paused"] == []


def test_a_running_night_takes_its_span_from_its_cells_and_says_it_is_live(where, tmp_path):
    ingest(where, [run("running", finished=None, step="convert")], SAID[:2])
    store = history.Store(tmp_path / "o2" / "ops.db")  # a running night has no record (map NV05: no countdown)
    got = night_view.data(where, store, None, now=at("2026-11-17T02:20:00"))
    n = got["n"]
    assert n["started"] == "2026-11-17T01:00:02+08:00" and n["ended"] == "" and n["live"] and got["live"]
    assert n["cells"]["totext"]["state"] == nightly.RUN
    before = got["sig"]
    ingest(where, [run("swapped")], SAID)
    assert night_view.api(where, store, {"brief": "1"}, now=NOW)["sig"] != before  # the page's poll sees the change


def test_a_date_out_of_reach_opens_the_default_night_and_older_nights_are_stepped_to(where, store):
    far = night_view.data(where, store, "2026-01-01", now=NOW)
    assert far["run"] == "2026-11-17" and far["note"].startswith("沒有 1/1 這一晚的紀錄（可看 11/17–")  # never silently another
    odd = night_view.data(where, store, "nonsense", now=NOW)
    assert odd["run"] == "2026-11-17" and odd["note"].startswith("沒有 nonsense 這一晚的紀錄")
    assert night_view.data(where, store, "2026-11-17", now=NOW)["note"] == ""  # the night asked is the one shown
    later = at("2026-11-19T09:00:00")  # two nights later: 11-17's and 11-18's chains did not run (沒跑)
    got = night_view.data(where, store, "2026-11-17", now=later)
    assert got["run"] == "2026-11-17" and got["default"] == "2026-11-19"
    assert [x["run"] for x in got["nav"]] == ["2026-11-17", "2026-11-18", "2026-11-19"]
    missed = night_view.data(where, store, "2026-11-18", now=later)["n"]
    assert missed["cells"]["crawl"]["word"] == "沒跑" and missed["probs"][0]["sev"] == "bad"
    assert missed["started"] == ""  # not started: the head counts down to 01:00 (and shows — once it has passed)


def test_problems_are_the_sources_own_words_each_naming_its_sites():
    one = {"cells": [
        {"name": "導出", "sev": "warn", "state": "done", "say": "有 1 站用舊資料補：c.example/aca/news 變多", "reason": "",
         "code": ""},
        {"name": "建庫", "sev": "bad", "state": "stop", "say": "", "reason": "build_exit_2 停在第 5 步", "code": "build_exit_2"},
        {"name": "換庫", "sev": "", "state": "wait", "say": "", "unknown": False}],
        "runs": [{"run": 1, "shown": True, "outcome": "swapped"}, {"run": 2, "shown": False, "outcome": "failed",
                                                                   "reason": "建庫程式出錯", "code": "failed · x"},
                 {"run": 3, "shown": False, "outcome": "nothing_new"}],
        "departures": {"items": [{"site": "a.example", "title": "某份公告", "url": "https://a.example/1"}]}}
    rows = night_view.problems(one, KEYS)
    assert [(r["sev"], r["where"]) for r in rows] == [("warn", "導出"), ("bad", "建庫"), ("bad", "進庫第 2 次"),
                                                      ("warn", "說不出原因地離開")]
    assert rows[0]["what"].startswith("有 1 站用舊資料補") and rows[0]["sites"] == ["c.example/aca"]
    assert rows[1]["what"] == "build_exit_2 停在第 5 步" and rows[1]["code"] == "build_exit_2"
    assert rows[3]["what"] == "a.example：某份公告" and rows[3]["sites"] == ["a.example"]
    # a bare host several sites share names none of them (the design picked one at random)
    assert night_view.site_of("c.example", KEYS) is None and night_view.site_of("a.example/x/y", KEYS) == "a.example"
    assert night_view.site_of("c.example/aca/n", KEYS) == "c.example/aca"
    assert night_view.site_of("c.example/acad", KEYS) is None
    assert night_view.sites_named("站 c.example 的 c.example/tcsl/x 與 a.example", KEYS) == ["c.example/tcsl", "a.example"]


# ---------------------------------------------------------------- the changes: map and side card

def test_the_nights_changes_are_its_own_comparison_listed_twelve_a_kind(where, store):
    got = night_view.data(where, store, None, changes=comparison(), now=NOW)
    c = got["changes"]
    assert c["kind"] == "full" and c["night"] == "2026-11-16"
    a = c["sites"][0]
    assert (a["site"], a["new"], a["changed"], a["left"]) == ("a.example", 15, 1, 2)
    assert len(a["lists"]["new"]) == 12 and a["lists"]["new"][0] == {"t": "公告 0", "u": "https://a.example/n/0",
                                                                     "id": "d0"}
    assert {x["why"] for x in a["lists"]["left"]} == {"unknown", "copy"}  # a left one carries its reason's key
    assert not any("id" in x for x in a["lists"]["left"])  # and no id: it is not in the library to open
    # the reasons' labels in the 11-23 words (names.py)
    assert c["labels"] == {"unknown": "原因不明", "copy": "併為副本"} and c["by_reason"] == {"copy": 1, "unknown": 1}
    # a comparison of another night is not this one's
    other = night_view.data(where, store, None, changes=comparison("2026-11-15"), now=NOW)
    assert other["changes"]["kind"] != "full"


def test_an_older_night_shows_the_comparison_kept_for_it_else_its_per_site_counts(where, store):
    assert store.put_night_changes("2026-11-17", comparison())
    got = night_view.data(where, store, "2026-11-17", now=at("2026-11-18T09:00:00"))
    assert got["changes"]["kind"] == "full" and got["changes"]["sites"][0]["site"] == "a.example"
    # the head's numbers come from the comparison when ops.db has none
    rec = {"counted_by": "changes", "site_counts": 1,
           "sites": [{"site": "a.example", "name": "甲處", "new": 3, "left": 1},
                     {"site": "b.example", "name": "乙處", "new": 0, "left": 0}]}
    counts = night_view.counts_view(rec)
    assert counts["kind"] == "counts" and [s["site"] for s in counts["sites"]] == ["a.example"]
    assert counts["sites"][0]["changed"] is None and counts["sites"][0]["lists"] is None  # 改了 was not kept
    assert night_view.counts_view({"counted_by": None})["kind"] == "none"
    # an ingest-counted swap keeps some sites' rows (their unexplained and documents), never the night's split by
    # site: not drawn as the night's per-site changes (G8)
    part = {"counted_by": "ingest", "site_counts": 0, "sites": [{"site": "a.example", "new": 3, "left": 1}]}
    assert night_view.counts_view(part) == {"kind": "none", "sites": [], "by_reason": {}, "labels": {}}


# ---------------------------------------------------------------- the steps: the latest reading, kept, or records

def test_the_latest_night_draws_its_steps_from_the_reading_in_the_11_23_words(where, store):
    got = night_view.data(where, store, None, flow=snapshot(), changes=comparison(), now=NOW)
    assert got["latest"] and got["flow"]["kind"] == "live" and got["flow"]["lacks"] == ""
    st = got["flow"]["steps"]
    assert list(st) == ["crawl", "export", "totext", "build", "swap"]
    crawl = st["crawl"]
    assert crawl["into"] == [["個這一輪該抓的網址（3 站）", 120]] and crawl["out"] == [["個抓到了", 100]]
    # the heading's short name and its long label for the hover; each row with its stable key for the drawer
    assert (crawl["lost_label"], crawl["lost_tip"]) == ("未取得", "還沒抓到的（照爬蟲自己的分類）")
    assert crawl["lost"] == [["校方已移除", 12, "gone"], ["待確認：內容過少", 8, "review:thin"]]
    assert [s[0] for s in crawl["sides"]] == ["校外網站每週重看", "要用瀏覽器開的網站"] and st["totext"]["sides"] == []
    export = st["export"]
    # an old reading's words renamed, and two that became one summed (names.merge)
    assert export["lost"] == [["內嵌頁或按鈕未取得", 9, ""], ["頁面顯示查無資料", 5, ""]]
    assert export["lost_label"] == "清單標註的缺漏"
    assert [r[1] for r in export["link"]] == ["爬取已取得", "第二輪補抓", "清單已取得"]
    assert export["link"][2][3] == "已取得：100\n依帳本種類歸類，未逐筆核對清單"
    assert st["build"]["share"] is False and st["build"]["lost_label"] == "未成為文件"
    swap = st["swap"]
    assert swap["does"] == "建好的庫先把關（有網站少一半就不換），再換上"
    assert (swap["into"], swap["out"], swap["share"]) == ([["份文件（換前）", 68]], [["份文件（換後）", 70]], False)
    assert swap["link"] == [["", "換前", 68, ""], ["+", "新進", 15, ""], ["−", "離開", 2, "併為副本 1、原因不明 1"],
                            ["=", "換後", 70, "改了 1 份（份數不變）"]]
    assert swap["lost"] == [["併為副本", 1, "copy"], ["原因不明", 1, "unknown"]]
    # 都說得出原因 is not said when some could not be (map NV16)
    assert swap["lost_label"] == "離開庫的原因" and swap["lost_tip"] == "離開庫的（1 份說不出原因）"
    # the reading's per-site cells give a site's numbers until the night's own files exist
    assert got["sites_kind"] == "cells" and got["sites"]["a.example"]["crawl"] == [33, 30]
    assert got["sites"]["a.example"]["lost"] == [["failed", "連線失敗", 2], ["gone", "校方已移除", 1]]
    assert got["sites"]["a.example"]["labels"] is None  # 待確認's causes: the page asks the drawer's counts


def test_an_older_night_draws_its_steps_from_its_own_files_and_says_what_it_lacks(where, store):
    coverage(where, "2026-11-17", COVER)
    night_file(where, "2026-11-17", [{"how": "run", "sites": SWAP_SITES},
                                     {"how": "promote", "sites": {k: {**v, "docs": 1} for k, v in SWAP_SITES.items()}}])
    store.put_night_changes("2026-11-17", comparison())
    got = night_view.data(where, store, "2026-11-17", flow=snapshot(), now=at("2026-11-18T09:00:00"))
    assert not got["latest"] and got["flow"]["kind"] == "records"
    assert got["flow"]["lacks"] == "這一晚只留下每一步的輸入、輸出和逐站數字；對帳、檔案和逐筆紀錄只看得到最近一晚的"
    st = got["flow"]["steps"]
    # 爬取 from the night's coverage line, over the library's sites only (elsewhere.example is not one)
    assert st["crawl"]["into"] == [["個這一輪該抓的網址（2 站）", 122]] and st["crawl"]["out"] == [["個抓到了", 110]]
    words = {w: n for w, n, _k in st["crawl"]["lost"]}
    assert words == {pl.review_word("thin"): 6, "連線失敗": 4, "校方已移除": 2}
    # 導出: the rows of the exports the night used; a cleared folder is said, never counted as 0
    assert st["export"]["into"] == [["筆（每筆一個網頁、檔案或圖片）；1 站的舊導出資料夾已清掉，沒算進來", 120]]
    assert st["export"]["out"] == []
    assert st["totext"]["into"] == [["個檔和網頁", 116]] and st["totext"]["out"] == [["個轉成文字", 110]]
    assert [r[0] for r in st["totext"]["lost"]] == [pl.STATUS_WORDS["empty"], pl.STATUS_WORDS["failed"]]
    assert st["build"]["into"] == [["筆記錄", 112]] and st["build"]["out"] == [["份文件", 50]]
    # 換庫 from the night's own swap (not the hand promote after it), its 對帳 from the comparison kept for it
    assert st["swap"]["into"] == [["份文件（換前）", 48]] and st["swap"]["out"] == [["份文件（換後）", 50]]
    assert st["swap"]["link"][1] == ["+", "新進", 15, ""]
    # a site's own numbers of that night
    assert got["sites_kind"] == "records"
    a, c = got["sites"]["a.example"], got["sites"]["c.example/aca"]
    assert a["crawl"] == [100, 90] and a["export"] == 120 and a["text"] == 100 and a["docs"] == 40
    assert a["docs_before"] == 38 and a["name"] == "甲處"
    assert a["labels"] == {pl.review_word("thin"): 6, "連線失敗": 4}  # 待確認 split by its causes
    assert c["export"] is None and c["export_gone"] and c["crawl"] == [22, 20]
    assert "elsewhere.example" not in got["sites"]


def test_an_older_night_with_its_steps_kept_draws_them_and_says_its_drawers_are_todays(where, store):
    assert store.put_night_flow("2026-11-17", snapshot(), at="2026-11-17T08:00:00+08:00")
    got = night_view.data(where, store, "2026-11-17", now=at("2026-11-18T09:00:00"))
    assert got["flow"]["kind"] == "kept" and not got["latest"]
    assert got["flow"]["lacks"] == "這一晚的檔案和逐筆紀錄只看得到最近一晚的（它們是現在的樣子）"
    assert got["flow"]["steps"]["crawl"]["out"] == [["個抓到了", 100]]
    assert got["sites"]["a.example"]["crawl"] == [33, 30]  # the kept reading's per-site cells


def test_the_latest_night_while_its_steps_are_read_says_so_and_shows_what_was_kept_meanwhile(where, store):
    # a restart: the first reading of the steps has not ended (Pipeline.snapshot: {'steps': [], 'running': True})
    reading = {"steps": [], "running": True, "state": "none", "say": "", "at": ""}
    got = night_view.data(where, store, None, flow=reading, now=NOW)
    assert got["reading"] and not got["latest"] and got["flow"]["kind"] == "none"
    assert got["flow"]["lacks"] == night_view.READING  # never 「這一晚的輸入、輸出沒留下」 while it is being read
    assert night_view.api(where, store, {"brief": "1"}, flow=reading, now=NOW)["reading"] is True
    # the steps the Recorder kept for the latest night stand in meanwhile (G7)
    assert store.put_night_flow("2026-11-17", snapshot(), at="2026-11-17T08:00:00+08:00")
    got = night_view.data(where, store, None, flow=reading, now=NOW)
    assert got["reading"] and got["flow"]["kind"] == "kept" and got["flow"]["steps"]["crawl"]["out"] == [["個抓到了", 100]]
    assert got["flow"]["lacks"] == night_view.READING and got["sites"]["a.example"]["crawl"] == [33, 30]
    before = got["sig"]
    read = night_view.data(where, store, None, flow=snapshot(), now=NOW)  # the reading ended: the page's poll sees it
    assert not read["reading"] and read["latest"] and read["flow"]["kind"] == "live" and read["sig"] != before


def test_a_pause_put_by_hand_is_said_on_the_night_the_page_opens_on_and_after(where, store):
    assert night_view.data(where, store, None, now=NOW)["paused"] == []
    (where.pipe / "ingest.pause").write_text("", encoding="utf-8")
    (where.archive / "update.pause").write_text("", encoding="utf-8")
    later = at("2026-11-18T09:00:00")
    got = night_view.data(where, store, "2026-11-18", now=later)
    assert got["paused"] == [["ingest.pause", "進庫暫停中", "現在是暫停的（有 ingest.pause）：每晚只轉文字、不換庫"],
                             ["update.pause", "更新暫停中", "現在是暫停的（有 update.pause）：每晚更新不跑，這晚沒更新"]]
    # an older night: what stands now is not that night's
    assert night_view.data(where, store, "2026-11-17", now=later)["paused"] == []
    band = LexborHTMLParser(night_view.band_html())
    assert band.css_first(".nt-left #ntPause") is not None and "hidden" in band.css_first("#ntPause").attributes


def test_an_older_night_with_no_record_says_so_under_its_head(where, store):
    later = at("2026-11-19T09:00:00")
    assert night_view.data(where, store, "2026-11-17", now=later)["note"] == ""  # 11-16's record is kept
    # the night of 11-17 (run 11-18) did not run and has no record: nothing to say of numbers it never had
    missed = night_view.data(where, store, "2026-11-18", now=later)
    assert not missed["n"]["kept"] and missed["note"] == ""
    # a night that ran and ended with no record kept (ops.db came later): said
    empty = history.Store(where.root / "o3" / "ops.db")
    empty.put_night_flow("2026-11-01", snapshot())  # ops.db holds something, only not this night
    empty.put({"night": "2026-11-01", "state": "ok", "word": "正常", "source": "test", "steps": [], "sites": []})
    got = night_view.data(where, empty, "2026-11-17", now=later)
    assert not got["n"]["kept"] and got["note"].startswith("這一晚沒有存下紀錄")
    assert night_view.data(where, empty, None, now=NOW)["note"] == ""  # the night the page opens on: not yet


def test_a_nights_file_not_in_the_shape_written_is_drawn_from_what_it_gives_never_a_500(where, store):
    """A nights/<day>.json cut short or written by hand (a swap's sites a list, a site a string, its totext a list, a
    count that is no number): /api/night still answers, from what the file does give (review finding 10)."""
    later = at("2026-11-18T09:00:00")
    night_file(where, "2026-11-17", [{"sites": ["a"]}])  # the review's own case
    got = night_view.api(where, store, {"date": "2026-11-17"}, now=later)
    assert got["run"] == "2026-11-17" and got["sites"] == {} and "export" not in got["flow"]["steps"]
    assert night_view.api(where, store, {"date": "2026-11-17", "brief": "1"}, now=later)["run"] == "2026-11-17"
    night_file(where, "2026-11-18", [
        "not a swap", {"how": "run", "sites": {
            "a.example": "x",
            "c.example/aca": {"export_rows": 7, "totext": ["ok"], "records": 4, "docs": 3, "docs_before": 2},
            "c.example/tcsl": {"totext": {"ok": "many", "empty": 2}, "docs": "5"}}}])
    got = night_view.api(where, store, {"date": "2026-11-18"}, now=later)
    aca, tcsl = got["sites"]["c.example/aca"], got["sites"]["c.example/tcsl"]
    assert "a.example" not in got["sites"]  # a site that is no object is left out
    assert (aca["export"], aca["text"], aca["records"], aca["docs"], aca["docs_before"]) == (7, None, 4, 3, 2)
    assert tcsl["text"] is None and tcsl["docs"] is None and tcsl["export_gone"]  # never a number made up
    assert got["flow"]["steps"]["export"]["into"][0][1] == 7
    # the readers on their own take any shape too
    for bad in (None, [], "x", {"swaps": "x"}, {"swaps": [5, {"sites": 5}]}):
        swap = night_view.chain_swap(bad)
        assert swap is None or swap["sites"] == {}
        assert night_view.flow_of_records(None, swap, None, {}) == {}
    sites, kind = night_view.site_numbers({"sites": ["x"]}, {"sites": [1]}, None, KEYS, {})
    assert (sites, kind) == ({}, "none")
    cov = {"sites": {"a.example": {"buckets": ["got"], "review": 3}}}
    assert night_view.site_numbers(cov, None, None, KEYS, {})[0]["a.example"]["crawl"] == [0, 0]


def test_the_live_comparison_is_the_night_the_recorder_files_it_under(where, store):
    """A hand promote at 15:00 on 11-17 (its 'night' 11-17, whose run day is 11-18): history.Recorder files its
    comparison under 11-17, the day the swap's run started (nights/2026-11-17.json holds its built_at); the night view
    matches the live comparison by the same rule, so 11-17 shows it and 11-18, not yet run, does not (review finding
    13)."""
    later = at("2026-11-18T09:00:00")
    promote = {**comparison("2026-11-17"), "new": {"docs": 70, "built_at": "2026-11-17T14:40:00+08:00",
                                                    "swapped_at": "2026-11-17T15:00:00+08:00"}}
    night_file(where, "2026-11-17", [{"how": "run", "built_at": "2026-11-17T03:00:00+08:00", "sites": SWAP_SITES},
                                     {"how": "promote", "built_at": "2026-11-17T14:40:00+08:00", "sites": SWAP_SITES}])
    assert night_view.changes_day(where.library.parent, promote) == "2026-11-17"
    assert night_view.data(where, store, "2026-11-17", changes=promote, now=later)["changes"]["kind"] == "full"
    assert night_view.data(where, store, "2026-11-18", changes=promote, now=later)["changes"]["kind"] != "full"
    # ops.db's copy: under the same day
    assert history.Recorder(store, where).keep_changes(promote)
    assert store.night_changes("2026-11-17") is not None and store.night_changes("2026-11-18") is None
    # a comparison naming no build (before nights/ existed): its 'night' alone, as the Recorder does
    assert night_view.changes_day(where.library.parent, comparison("2026-11-16")) == "2026-11-17"
    assert night_view.changes_day(where.library.parent, {"state": "running", "night": "2026-11-16"}) is None
    assert night_view.changes_day(where.library.parent, {"state": "ready", "night": "no date"}) is None


# ---------------------------------------------------------------- a picked site's moments on the time line

def test_a_sites_moments_are_its_requests_from_the_ledger_and_the_log_lines_naming_it(where, store, monkeypatch):
    db = where.archive / "frontier.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE request(id INTEGER PRIMARY KEY, queue TEXT, host TEXT, url TEXT, purpose TEXT,"
                 " started REAL, ended REAL, status INTEGER)")
    t = at("2026-11-17T01:10:00").timestamp()
    conn.executemany("INSERT INTO request (host, url, purpose, started, ended, status) VALUES (?, ?, ?, ?, ?, ?)", [
        ("c.example", "https://c.example/aca/a", "fetch", t, t + 1, 200),
        ("c.example", "https://c.example/aca/b", "fetch", t + 30, t + 31, 404),  # the same 90-second bucket
        ("c.example", "https://c.example/acad/x", "fetch", t + 40, t + 41, 200),  # another site's
        ("c.example", "https://c.example/tcsl/y", "fetch", t + 50, t + 51, 200),
        ("c.example", "https://c.example/aca/c", "fetch", t + 600, t + 601, 200),
        ("c.example", "https://c.example/aca/d", "fetch", t - 7200, t - 7199, 200)])  # before the night
    conn.commit()
    conn.close()
    monkeypatch.setattr(pl, "open_ledger", lambda archive: sqlite3.connect(Path(archive) / "frontier.db"))
    asked = []

    def window(_where, **kw):
        asked.append(kw)
        rows = {"c.example/aca": [["2026-11-17T03:00:00", "info", "ingest", "站 c.example/aca 進庫"]],
                "c.example_aca": [["2026-11-17T01:55:00", "warn", "export", "导出 c.example_aca-20261117 有缺"],
                                  ["2026-11-17T01:20:00", "info", "crawl", "c.example_aca: not a step tick"]]}
        return {"rows": rows.get(kw["q"], []), "more": False}

    monkeypatch.setattr(logs, "window", window)
    got = night_view.ticks(where, store, {"date": "2026-11-17", "site": "c.example/aca"}, now=NOW)
    assert got["run"] == "2026-11-17" and got["site"] == "c.example/aca" and not got["cut"]
    by = [(x["st"], x["t"][11:16], x["n"]) for x in got["ticks"]]
    assert by == [("build", "03:00", 1), ("crawl", "01:10", 2), ("crawl", "01:20", 1), ("export", "01:55", 1)]
    first = next(x for x in got["ticks"] if x["st"] == "crawl")
    assert first["msg"] == "fetch 200 https://c.example/aca/a"
    # the night's own window (start − 10 min … last end + 10 min), every level, the export's folder form asked too
    assert {kw["q"] for kw in asked} == {"c.example/aca", "c.example_aca"}
    assert asked[0]["since"] == "2026-11-17T00:50:02+08:00" and asked[0]["until"] == "2026-11-17T03:50:10+08:00"
    assert asked[0]["levels"] == {"error", "warn", "info", "debug"}
    assert night_view.ticks(where, store, {"date": "2026-11-17", "site": ""}, now=NOW)["ticks"] == []


def test_a_host_sites_moments_leave_out_the_addresses_of_the_path_sites_on_its_host(tmp_path, monkeypatch):
    # sso.nknu.edu.tw beside sso.nknu.edu.tw/news …: a host key's requests are only the ones under no longer key (G10)
    db = library(tmp_path / PIPE / "aca.db", sites=[*SITES, ("c.example", "總站", 3, "exp/x")])
    (tmp_path / "crawl").mkdir()
    w = Where(root=tmp_path, db=db, archive=tmp_path / "crawl")
    crawler_log(w, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(w)
    ingest(w, [run("swapped")], SAID)
    conn = sqlite3.connect(w.archive / "frontier.db")
    conn.execute("CREATE TABLE request(id INTEGER PRIMARY KEY, queue TEXT, host TEXT, url TEXT, purpose TEXT,"
                 " started REAL, ended REAL, status INTEGER)")
    t = at("2026-11-17T01:10:00").timestamp()
    conn.executemany("INSERT INTO request (host, url, purpose, started, status) VALUES (?, ?, ?, ?, ?)", [
        ("c.example", "https://c.example/aca/a", "fetch", t, 200),  # 教務處's
        ("c.example", "https://c.example/ACA", "fetch", t + 1, 200),  # 教務處's (case and no slash)
        ("c.example", "https://c.example/tcsl?id=1", "fetch", t + 2, 200),  # 華語所's
        ("c.example", "https://c.example/acad/x", "fetch", t + 3, 404),  # the host's: /acad is not under /aca
        ("c.example", "https://c.example/", "fetch", t + 200, 200),  # the host's
        ("c.example", "https://c.example/a_b%/x", "fetch", t + 400, 200)])  # the host's (LIKE's wildcards in a url)
    conn.commit()
    conn.close()
    monkeypatch.setattr(pl, "open_ledger", lambda archive: sqlite3.connect(Path(archive) / "frontier.db"))
    monkeypatch.setattr(logs, "window", lambda _where, **kw: {"rows": [], "more": False})
    got = night_view.ticks(w, None, {"date": "2026-11-17", "site": "c.example"}, now=NOW)
    assert [(x["t"][11:19], x["n"], x["msg"]) for x in got["ticks"]] == [
        ("01:10:03", 1, "fetch 404 https://c.example/acad/x"), ("01:13:20", 1, "fetch 200 https://c.example/"),
        ("01:16:40", 1, "fetch 200 https://c.example/a_b%/x")]
    aca = night_view.ticks(w, None, {"date": "2026-11-17", "site": "c.example/aca"}, now=NOW)
    assert [x["n"] for x in aca["ticks"]] == [2]


def test_a_sites_moments_are_masked_as_every_log_row_is(where, store, monkeypatch):
    """The ledger's request addresses and the log lines a tick carries are masked with pipeline._SECRETS, as the
    drawers and 紀錄 mask them, before they are cut to 140 characters (review finding 2)."""
    conn = sqlite3.connect(where.archive / "frontier.db")
    conn.execute("CREATE TABLE request(id INTEGER PRIMARY KEY, queue TEXT, host TEXT, url TEXT, purpose TEXT,"
                 " started REAL, ended REAL, status INTEGER)")
    t = at("2026-11-17T01:10:00").timestamp()
    long_path = "seg/" * 25  # the key starts at character 128: cut first, 「AIza」 and 8 more would show
    conn.executemany("INSERT INTO request (host, url, purpose, started, status) VALUES (?, ?, ?, ?, ?)", [
        ("a.example", "https://a.example/api?access_token=tok123secret&x=1", "fetch", t, 200),
        ("a.example", f"https://a.example/{long_path}AIzaSyA1234567890abcdefghij", "fetch", t + 600, 200)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(pl, "open_ledger", lambda archive: sqlite3.connect(Path(archive) / "frontier.db"))
    monkeypatch.setattr(logs, "window", lambda _where, **kw: {"rows": [
        ["2026-11-17T02:00:00", "info", "build", "建 a.example key=x&token=abc999 完"]] if kw["q"] == "a.example" else [],
        "more": False})
    got = night_view.ticks(where, store, {"date": "2026-11-17", "site": "a.example"}, now=NOW)
    msgs = [x["msg"] for x in got["ticks"]]
    assert msgs[0] == "建 a.example key=x&token=（遮掉） 完"
    assert msgs[1] == "fetch 200 https://a.example/api?access_token=（遮掉）&x=1"
    assert "AIza" not in msgs[2] and "1234567890" not in msgs[2] and len(msgs[2]) <= 140
    assert not any("secret" in m or "abc999" in m for m in msgs)


def test_the_nights_window_runs_from_ten_minutes_before_its_start_to_ten_after_its_last_end():
    n = {"started": "2026-11-17T01:00:00+08:00", "ended": "2026-11-17T03:40:00+08:00",
         "runs": [{"until": "2026-11-17T05:00:00+08:00"}]}
    a, b = night_view.window_of(n)
    assert (a.strftime("%H:%M"), b.strftime("%H:%M")) == ("00:50", "05:10")
    assert night_view.window_of({"started": "", "runs": []}) is None
    a, b = night_view.window_of({"started": "2026-11-17T01:00:00+08:00", "ended": "", "runs": []})
    assert (b - a).total_seconds() == 12 * 3600 + 20 * 60  # running, no end yet: twelve hours on


# ---------------------------------------------------------------- the markup and the layer

def test_the_band_and_the_log_are_the_designs_markup_hidden_until_night_mode():
    band = LexborHTMLParser(night_view.band_html())
    sec = band.css_first("section#nightBand")
    assert sec.attributes.get("class") == "nt" and "hidden" in sec.attributes
    for sel in ("#ntBadge", "#ntSpan", "#ntPrev", "#ntNext", "#ntDay", "#ntWd", "#ntKpis", "#ntProbs", "#ntProbN",
                "#ntGantt", ".nt-left", ".nt-right", ".nt-nav"):
        assert band.css_first(sel) is not None, sel
    assert band.css_first("#ntPrev").attributes["aria-label"] == "前一晚" and band.css_first("#ntNext").text() == "›"
    assert band.css_first(".nt-probs h2").text() == "問題" and "hidden" in band.css_first(".nt-probs").attributes
    hp = band.css_first("button.hp")
    assert hp.attributes["aria-label"] == "定時更新怎麼看"
    assert hp.attributes["data-tip"].split("\n") == ["日期：開跑那天，01:00 起",
                                                     "網站圖：灰 沒變 · 紫 有變（越深越多）· 黃紅 被問題點名",
                                                     "點網站：右邊看它這一晚的文件", "← →：換一天"]
    cfg = json.loads(band.css_first("script#nt-cfg").text())
    assert cfg == {"api": "/api/night", "log": "/api/logs", "ticks": "/api/night/ticks"}
    log = LexborHTMLParser(night_view.log_html())
    card = log.css_first("section#nightLog")
    assert card.attributes["class"] == "card nt-log" and "hidden" in card.attributes
    assert card.css_first("h2").text() == "紀錄"
    assert log.css_first("#lv-q").attributes["placeholder"] == "搜尋紀錄（網址、站名、字詞）"
    assert log.css_first(".lv-max-btn").attributes["data-tip"] == "放大看紀錄（Esc 關閉）"
    assert all(log.css_first(s) is not None for s in ("#lv-lv", "#lv-src", "#lv-hist", "#lv-list"))
    assert not re.search(r"<script[^>]* src=", night_view.band_html() + night_view.log_html())


def test_the_night_layer_carries_the_designs_behaviour_and_colours():
    got = design.layer("night")
    css = (design.DIR / "night.css").read_text(encoding="utf-8")
    js = (design.DIR / "night.js").read_text(encoding="utf-8")
    assert css in got and js in got and got.startswith("<style id='design-night'>")
    # the app's hooks and the drawers', as the design named them
    for name in ("window.NM={apply,tile,after,set,items,sum,tip", "A.LENS.night={name:'這一晚的變動'", "window.APP",
                 "window.RAWVIEW(box,openStep,m)", "window.FILEVIEW(box,openStep,m)", "RAWSHOW(sk,",
                 "CFG.api", "CFG.log", "CFG.ticks", "&brief=1", "searchParams.get('date')", "qs.get('step')"):
        assert name in js, name
    # the words the owner settled: the badge on hover, 通過率, 對帳, the folded rest, the log's two empties
    for words in ("done:'完成',warn:'留意',stop:'暫停',bad:'出錯',run:'跑中',none:'還沒開始'", "通過率 <b>", "淨 <b>",
                  ">對帳${off}</h5>", "其餘 ${rest.length} 項 · ", "沒有錯誤或警告；全部 ", "這一晚沒有留下紀錄",
                  "'各網站變動'", "'文件變化'", "網站：<b>", "全部網站 ↓", "這一晚的問題沒有點名這個網站",
                  "上面的數字、問題、時間條都只算這個網站", "這一晚只有問題點名的網站，逐站變動沒留下", "✓ 沒有問題",
                  "校外週檢", "瀏覽器抓取", "開跑", "文件總數", "份說不出原因", "另一次進庫（第 ",
                  "還差 ${num(Math.abs(F.link_left))}"):
        assert words in js, words
    assert "new Set(['error','warn'])" in js  # 紀錄 opens on what went wrong only (owner 11-23)
    # 總覽's state colours on the time line, the violet in/out in both themes, the step's head row gone
    assert ".gt-bar:not(.extra)[data-s=done]{background-color:var(--good-s)!important}" in css
    assert ":root{--cal-in:#3d2c9e;--cal-out:#6a61c4}" in css
    assert "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--cal-in:#9a90ff" in css
    assert ":root[data-theme=dark]{--cal-in:#9a90ff;--cal-out:#7068c8}" in css
    assert "#ntStep .sd-h{display:none!important}" in css and "#nightBand .gt{grid-template-columns:150px" in css
    assert ".nm-sw" not in css and ".wf{" not in css  # the leftovers the port leaves out
    assert ".visually-hidden" not in css  # the page's own rule shows a focused skip link (G20)
    # the 紀錄 route is logs.py's /api/logs, also when the page carries no nt-cfg (G15)
    assert "log:'/api/logs'" in js and "/api/night/log" not in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_night_script_parses():
    js = design.DIR / "night.js"
    assert subprocess.run(["node", "--check", str(js)], check=False, capture_output=True, timeout=60).returncode == 0


# ---------------------------------------------------------------- links into a night

def test_a_night_is_linked_by_its_run_day_with_a_step_to_open():
    assert nightly.run_day("2026-11-22") == "2026-11-23"
    assert nightly.night_href("2026-11-22") == "/sites?date=2026-11-23"
    assert nightly.night_href("2026-11-22", "build") == "/sites?date=2026-11-23&step=build"
    assert nightly.night_href("2026-11-22", "nightly") == "/sites?date=2026-11-23&step=swap"
    assert nightly.night_href("2026-11-22", "serve") == "/sites?date=2026-11-23"
    # one mapping (CR-03): nightly's, re-exported by sources beside its inverse; the night view keeps no copy
    assert sources.run_day is nightly.run_day is night_view.run_day
    assert night_view.night_of_run is sources.night_of_run and sources.night_of_run("2026-11-23") == "2026-11-22"
    assert not hasattr(night_view, "night_key") and not hasattr(night_view, "_mod")


def test_the_problems_of_a_night_link_into_its_night_view_with_the_step_open(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("failed", error="build_exit_2", step="build")], SAID[:4])
    t = nightly.tonight(where, now=NOW)
    snap = {"steps": [{"key": "export", "name": "導出", "state": "warn", "say": "有缺", "at": ""},
                      {"key": "serve", "name": "查詢服務", "state": "bad", "say": "資料庫 沒有回應", "at": ""},
                      {"key": "nightly", "name": "每晚進庫", "state": "bad", "say": "x", "at": ""}]}
    rows = tonight_page.problems(t, snap, [])
    hrefs = {r["where"]: r["href"] for r in rows}
    assert hrefs["建庫"] == "/sites?date=2026-11-17&step=build"
    assert hrefs["導出"] == "/sites?date=2026-11-17&step=export"
    # no night's step: where it shows once /tonight sends on (CR-05): 總覽's 網站訪問量, 搜尋's 測試
    assert hrefs["查詢服務"] == "/#usage"
    check = tonight_page.problems(t, {"steps": [{"key": "check", "name": "品質檢查", "state": "warn", "say": "退步",
                                                 "at": ""}]}, [])
    assert next(r["href"] for r in check if r["where"] == "品質檢查") == "/try#tests"
    assert all(x["href"].startswith("/sites?date=2026-11-17") for x in t["nights"] if x["night"] == "2026-11-16")


# ---------------------------------------------------------------- night.js on a page, in this machine's Chrome
# The band and the log as web.py puts them in 內容, the night layer, a stand-in for 內容's app (window.APP: S, S0,
# render, byKey, LENS, as overview_page.html exposes it) and for the drawers (RAWSHOW records its calls), and the
# answers of /api/night, /api/logs, /api/raw and /api/night/ticks as the modules give them, served on a made-up host:
# the browser asks nothing outside it.

PAGE_HOST = "http://panel.test"
GROUPS10 = ["文學院", "教育學院", "理學院", "藝術學院", "科技學院", "管理學院", "行政處室", "中心", "全校共用", "第十組"]
S0 = [{"k": "a.example", "n": "甲處", "f": "甲處", "g": 0}, {"k": "c.example/aca", "n": "教務處", "f": "教務處", "g": 6},
      {"k": "c.example/tcsl", "n": "華語所", "f": "華語文教學研究所", "g": 9}]  # 華語所 in a tenth group (G19)
STAND_IN = ("<script>window.RAWCALLS=[];window.RAWSHOW=(step,pre)=>RAWCALLS.push([step,pre]);window.RAWVIEW=()=>{};"
            "window.FILEVIEW=()=>{};const S0=" + json.dumps(S0, ensure_ascii=False) + ",byKey={};"
            "S0.forEach(s=>byKey[s.k]=s);"
            # render() calls NM.after() twice, as 內容's does (renderMap's own call, then render's)
            "window.APP={S:{grp:null,site:null,lens:null},S0,byKey,LENS:{},render(){if(window.NM){NM.after();NM.after()}}};"
            "</script>")


def page_html() -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'>" + design.layer("night") + "</head><body>"
            "<div class='ct-band'></div>" + night_view.band_html() +
            "<h2 id='hMap'></h2><span id='hMapSub'></span><div id='tm'></div><div id='tmKey'></div>"
            "<div id='tmLegend'></div><aside id='insp'><h3 id='iT'></h3><span id='iS'></span></aside>"
            + night_view.log_html() +
            "<script type='application/json' id='data'>" + json.dumps({"groups": GROUPS10}, ensure_ascii=False)
            + "</script>" + STAND_IN + "</body></html>")


# raw.page's answer for 爬取 with a site (DRAWERS: labels [[key, word, n, only]], the key a bucket or review:<code>)
RAW_SITE = {"step": "crawl", "state": "ready", "labels": [["got", "已取得", 30, False], ["review:thin", "待確認：內容過少", 5, False],
                                                         ["gone", "校方已移除", 1, False]], "rows": [], "matched": 36}
# logs.window's answer for a night whose 爬取 lines are held only from 01:30 (the panel read the log from its end)
LOGS_SHORT = {"rows": [], "hist": [], "counts": {"levels": {}, "sources": {}}, "more": False, "next": None, "total": 0,
              "from": "2026-11-17T00:50:02", "to": "2026-11-17T03:50:10",
              "kept_from": {"crawl": "2026-11-17T01:30:00", "ingest": "2026-11-10T02:00:00"},
              "cut_from": {"crawl": "2026-11-17T01:30:00"}, "short": [["crawl", "2026-11-17T01:30:00"]]}


@pytest.fixture
def chrome():
    pw = pytest.importorskip("playwright.sync_api", reason="没装 Playwright（pyproject 的 extra browser）")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001  no Chrome on this machine
            pytest.skip(f"这台机器打不开 Chrome：{str(exc)[:200]}")
        try:
            yield browser
        finally:
            browser.close()


def open_night(browser, address: str, answers: dict, *, logs_answer: dict | None = None, at_time: str = "",
               clock: str = "", raw_answer: dict | None = None):
    """A page at PAGE_HOST + address with night.js; answers: /api/night's by ?date= ('' for the default), read at each
    request. at_time: Date.now() fixed there; clock: the page's clock installed there (page.clock.run_for moves it and
    its timers); raw_answer: /api/raw's (else RAW_SITE). Returns (page, asked: every /api/… address the page asked,
    errors)."""
    page = browser.new_page()
    if at_time:
        page.clock.set_fixed_time(at_time)
    if clock:
        page.clock.install(time=clock)
    asked, errors = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def answer(route):
        url = route.request.url
        path, _, query = url[len(PAGE_HOST):].partition("?")
        params = dict(x.partition("=")[::2] for x in query.split("&") if x)
        if path == "/sites":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page_html())
        asked.append(url[len(PAGE_HOST):])
        body = {"/api/night": lambda: answers.get(params.get("date", ""), answers[""]),
                "/api/logs": lambda: logs_answer or LOGS_SHORT, "/api/raw": lambda: raw_answer or RAW_SITE,
                "/api/night/ticks": lambda: {"run": params.get("date"), "site": params.get("site"), "ticks": [],
                                             "cut": False}}.get(path)
        if body is None:
            return route.fulfill(status=404, body="")
        got = body()
        if path == "/api/night" and params.get("brief") == "1":
            got = {k: got[k] for k in ("run", "sig", "live", "reading")}
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(got, ensure_ascii=False))

    page.route(f"{PAGE_HOST}/**", answer)
    page.goto(PAGE_HOST + address)
    page.wait_for_selector("#ntGantt .gt-l", state="attached", timeout=10000)
    return page, asked, errors


def until(page, cond, tries: int = 60) -> bool:
    """Lets the page answer its requests (a poll's fetch ends after the clock moved) until cond() holds."""
    for _ in range(tries):
        if cond():
            return True
        page.wait_for_timeout(50)
    return cond()


def texts(page, selector: str) -> list[str]:
    return page.eval_on_selector_all(selector, "xs=>xs.map(x=>x.textContent)")


@pytest.mark.browser
def test_night_js_picks_and_unpicks_a_site_and_recounts_by_the_drawers_labels(where, store, chrome):
    latest = night_view.data(where, store, None, flow=snapshot(), changes=comparison(), now=NOW)
    latest["n"]["probs"] = [{"sev": "warn", "where": "導出", "what": "c.example/aca 有缺", "code": "", "sites": ["c.example/aca"]},
                            {"sev": "bad", "where": "導出", "what": "c.example/tcsl 失敗", "code": "",
                             "sites": ["c.example/tcsl"]}]
    page, asked, errors = open_night(chrome, "/sites?date=2026-11-17&step=crawl", {"": latest, "2026-11-17": latest},
                                     at_time="2026-11-23T12:00:00+08:00")
    lost = "#ntStep .sd-lost li"
    assert texts(page, f"{lost} b") == ["12", "8"]
    widths = page.eval_on_selector_all(f"{lost} i>em", "xs=>xs.map(x=>x.style.width)")
    # a picked site: the causes recounted from the drawer's labels by their keys (G1: never NaN, never all 0)
    page.evaluate("APP.S.site='a.example';APP.render()")
    page.wait_for_function("document.querySelector('#ntStep .sd-lost li b').textContent==='1'", timeout=5000)
    assert texts(page, f"{lost} b") == ["1", "5"] and not page.query_selector(f"{lost}.nm-z")
    assert page.eval_on_selector_all(f"{lost} i>em", "xs=>xs.map(x=>x.style.width)") == ["20%", "100%"]
    assert any(a.startswith("/api/raw?step=crawl&site=a.example") for a in asked)
    assert "甲處" in page.text_content("#ntStep .nm-step") and page.text_content("#ntSite b") == "甲處"
    page.click(f"{lost} >> nth=0")
    assert page.evaluate("RAWCALLS.at(-1)") == ["crawl", {"label": "校方已移除", "key": "gone", "site": "a.example"}]
    # unpicked: the whole night's counts, bars and rows back, and a row opens every site's records (G3)
    page.evaluate("APP.S.site=null;APP.render()")
    assert texts(page, f"{lost} b") == ["12", "8"] and not page.query_selector(f"{lost}.nm-z")
    assert page.eval_on_selector_all(f"{lost} i>em", "xs=>xs.map(x=>x.style.width)") == widths
    assert not page.query_selector("#ntStep .nm-step") and not page.query_selector("#ntSite")
    page.click(f"{lost} >> nth=1")
    assert page.evaluate("RAWCALLS.at(-1)") == ["crawl", {"label": "待確認：內容過少", "key": "review:thin"}]
    # a site only a problem names is a tile with its label (G18); a site in a tenth group is listed under it (G19)
    assert page.evaluate("[APP.LENS.night.f(APP.byKey['c.example/aca']),APP.LENS.night.lab(APP.byKey['c.example/aca'])]"
                         ) == [0.5, "留意"]
    assert page.evaluate("APP.LENS.night.f({k:'nowhere.example'})") is None
    assert {t.split(" ")[0][1:] for t in texts(page, "#nmSite .nm-g span")} == {"文學院", "行政處室", "第十組"}
    # the log: no line held before 01:30 for 爬取 is said, never read as a quiet night (G5)
    assert page.text_content("#lv-list") == "11-17 01:30 以前的爬取紀錄沒有留在檢視台"
    assert not errors


@pytest.mark.browser
def test_night_js_on_older_nights_never_draws_nan_caches_only_older_nights_and_counts_down_only_ahead(
        where, store, chrome):
    later = at("2026-11-18T09:00:00")
    coverage(where, "2026-11-17", COVER)  # 11-17 has a coverage line and no nights/<day>.json: no swap numbers
    older = night_view.data(where, store, "2026-11-17", now=later)
    off = history.Store(None)  # ops.db not set: said under the head
    (where.pipe / "ingest.pause").write_text("", encoding="utf-8")  # a pause put by hand: said from the default on
    missed = night_view.data(where, off, "2026-11-18", now=later)
    assert older["sites"]["c.example/aca"].get("docs") is None and not missed["n"]["started"]
    answers = {"": missed, "2026-11-18": missed, "2026-11-17": older}
    page, asked, errors = open_night(chrome, "/sites?date=2026-11-17&step=swap", answers,
                                     at_time="2026-11-23T12:00:00+08:00")
    assert not page.is_visible("#ntPause")  # an older night: the pause standing now is not its
    page.evaluate("APP.S.site='c.example/aca';APP.render()")
    line = page.text_content("#ntStep .nm-step")
    assert "這個網站這一步的數字沒留下" in line and "NaN" not in line  # G2
    for step, word in (("export", "這個網站這一步的數字沒留下"), ("crawl", "22")):
        page.click(f"#ntGantt .gt-l[data-k={step}]")
        assert word in page.text_content("#ntStep .nm-step") and "NaN" not in page.text_content("#ntStep")
    # ‹ › : the night before the default is kept for the session, the default is asked again each time (G6)
    page.click("#ntNext")
    page.wait_for_function("document.getElementById('ntDay').textContent==='11/18'", timeout=5000)
    assert page.text_content("#ntNote") == "每晚的紀錄沒有存：沒有設定紀錄檔（--ops-db）；前幾晚的數字看不到"
    assert page.is_visible("#ntPause") and page.text_content("#ntPause") == "進庫暫停中"  # G13
    assert page.get_attribute("#ntPause", "data-tip") == "現在是暫停的（有 ingest.pause）：每晚只轉文字、不換庫"
    # a past night that never started: no countdown (G17); its counts were not kept: —, not 0 (G9)
    assert texts(page, "#ntKpis b") == ["沒跑"] and not page.query_selector("#ntCd")
    assert texts(page, "#nmSite .nm-ring li b") == ["—", "—", "—"]  # the site still picked: its counts not kept
    page.evaluate("APP.S.site=null;APP.render()")
    assert texts(page, "#nmSite .nm-ring li b") == ["—", "—", "—"]
    assert "這一晚的新進、改了、離開沒留下" in page.text_content("#nmSite")
    page.click("#ntPrev")
    page.wait_for_function("document.getElementById('ntDay').textContent==='11/17'", timeout=5000)
    page.click("#ntNext")
    page.wait_for_function("document.getElementById('ntDay').textContent==='11/18'", timeout=5000)
    nights = [a for a in asked if a.startswith("/api/night?")]
    assert nights.count("/api/night?date=2026-11-17") == 1 and nights.count("/api/night?date=2026-11-18") == 2
    # an unknown ?step= opens nothing and leaves the address (G16)
    page2, _asked, errors2 = open_night(chrome, "/sites?date=2026-11-17&step=serve", answers,
                                        at_time="2026-11-23T12:00:00+08:00")
    assert page2.evaluate("location.search") == "?date=2026-11-17" and not page2.is_visible("#ntStep")
    assert "undefined" not in page2.text_content("#nightBand")
    # the same night not started, seen before its 01:00: the countdown, and the page asks again (G6, G17)
    page3, _asked, errors3 = open_night(chrome, "/sites?date=2026-11-18", answers, at_time="2026-11-18T00:30:00+08:00")
    assert page3.text_content("#ntCd") == "00:30:00"
    assert not errors and not errors2 and not errors3


@pytest.mark.browser
def test_night_js_asks_again_while_a_night_can_change_and_reads_its_log_again(where, store, chrome):
    running = night_view.data(where, store, None, flow=snapshot(), changes=comparison(), now=NOW)
    running["live"] = running["n"]["live"] = True  # a night going on: the page asks every minute (NV37)
    answers = {"": running, "2026-11-17": running}
    page, asked, errors = open_night(chrome, "/sites?date=2026-11-17", answers, clock="2026-11-17T02:20:00+08:00")
    logs_before = sum(a.startswith("/api/logs?") for a in asked)
    page.clock.run_for(61_000)  # nothing changed: asked briefly, not drawn again
    assert until(page, lambda: any(a.endswith("&brief=1") for a in asked))
    assert sum(a.startswith("/api/logs?") for a in asked) == logs_before
    answers[""] = answers["2026-11-17"] = {**running, "sig": "changed"}
    page.clock.run_for(61_000)
    # its sig changed: the night drawn again from a fresh answer, and its 紀錄 read again (G6 c)
    assert until(page, lambda: sum(a.startswith("/api/logs?") for a in asked) > logs_before)
    assert asked.count("/api/night?date=2026-11-17") >= 2
    # a night still to come: the countdown, and at 01:00 the page asks once, not on every second after it (G6 a)
    later = at("2026-11-18T09:00:00")
    ahead = night_view.data(where, store, "2026-11-18", now=later)
    page2, asked2, errors2 = open_night(chrome, "/sites?date=2026-11-18", {"": ahead, "2026-11-18": ahead},
                                        clock="2026-11-18T00:59:57+08:00")
    assert re.fullmatch(r"00:00:0[1-3]", page2.text_content("#ntCd"))  # the installed clock moves on its own
    first = asked2.count("/api/night?date=2026-11-18")
    page2.clock.run_for(10_000)
    assert until(page2, lambda: asked2.count("/api/night?date=2026-11-18") > first)
    page2.clock.run_for(5_000)
    until(page2, lambda: False, tries=6)
    assert page2.text_content("#ntCd") == "—" and asked2.count("/api/night?date=2026-11-18") == first + 1
    assert not errors and not errors2


@pytest.mark.browser
def test_night_js_keeps_the_rest_of_the_address_and_asks_once_per_pick(where, store, chrome):
    """Review findings 7, 8 and 14: only ?date and &step are the night's (內容's ?ck and ?kind stay through a step
    opened and closed, a poll, leaving the night); a picked site's moments and the drawer's counts are asked once per
    pick, not on each of the app's redraws, and the counts not on the poll either (an index still being read is asked
    again at the next pick); a night going on asks its moments again only once they are TICK_FRESH old."""
    running = night_view.data(where, store, None, flow=snapshot(), changes=comparison(), now=NOW)
    running["live"] = running["n"]["live"] = True
    answers = {"": running, "2026-11-17": running}
    building = {"step": "crawl", "state": "building", "labels": [], "rows": []}
    page, asked, errors = open_night(chrome, "/sites?ck=none&date=2026-11-17&kind=2", answers,
                                     clock="2026-11-17T02:20:00+08:00", raw_answer=building)
    assert page.evaluate("location.search") == "?ck=none&date=2026-11-17&kind=2"
    page.click("#ntGantt .gt-l[data-k=crawl]")
    assert page.evaluate("location.search") == "?ck=none&date=2026-11-17&kind=2&step=crawl"
    page.click("#ntGantt .gt-l[data-k=export]")
    page.click("#ntGantt .gt-l[data-k=export]")
    assert page.evaluate("location.search") == "?ck=none&date=2026-11-17&kind=2"
    page.click("#ntGantt .gt-l[data-k=crawl]")
    n_ticks = lambda: sum(a.startswith("/api/night/ticks?") for a in asked)
    n_raw = lambda: sum(a.startswith("/api/raw?step=crawl&site=") for a in asked)
    page.evaluate("APP.S.site='a.example';APP.render();APP.render();APP.render()")
    assert until(page, lambda: n_ticks() >= 1 and n_raw() >= 1)
    page.evaluate("for(let i=0;i<5;i++)APP.render()")  # clicks, a resize: the same pick drawn again
    until(page, lambda: False, tries=4)
    assert (n_ticks(), n_raw()) == (1, 1)
    # the poll finds the night changed and draws it again: the moments, a minute old, asked again; the counts not
    answers[""] = answers["2026-11-17"] = {**running, "sig": "changed"}
    page.clock.run_for(61_000)
    assert until(page, lambda: n_ticks() == 2)
    until(page, lambda: False, tries=4)
    assert n_raw() == 1 and page.evaluate("location.search") == "?ck=none&date=2026-11-17&kind=2&step=crawl"
    # the site let go and picked again: a new pick asks the drawer's counts again (its index was still being read);
    # its moments, fresh, are drawn from what the page holds
    page.evaluate("APP.S.site=null;APP.render();APP.S.site='a.example';APP.render()")
    assert until(page, lambda: n_raw() == 2)
    assert n_ticks() == 2
    # leaving the night: its ?date and &step go, 內容's filters stay
    page.evaluate("NM.set(false)")
    assert page.evaluate("location.search") == "?ck=none&kind=2"
    assert not errors
