"""The nightly ingest's own check of what left the library (content session, 11-17; research/pipeline_2026-11-02/
guard_1117/note_for_panels_1117.md), read by 今晚, the nights' record and a site's page: unexplained_left lights a night
that swapped, a held night says why in plain words, the new error values read as words, the build's own count of
the unexplained wins over the panels' own comparison, and its items are listed where the unexplained are. Made-up
status lines in the ingest's shape: no real data."""

from __future__ import annotations

from datetime import date

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import history, home_page, nightly, site_page, tonight_page
from nknu.ops.pipeline import BAD, CONTENT, OK, PIPE, WARN, Where
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, library, run

ITEMS = [{"site": "a.example", "title": "第一份<b>", "url": "https://a.example/1", "why": "no_file", "detail": "x｜y"},
         {"site": "a.example", "title": "", "url": "https://a.example/2", "why": "emptied", "detail": "900 → 10 字"},
         {"site": "b.example", "title": "第三份", "url": "https://b.example/3", "why": "no_row", "detail": ""}]
WORDS = {"no_file": "导出那一行没有档了", "emptied": "内容被掏空", "no_row": "导出里没有这一行了", "merged": "并进另一份"}


def departures(n: int = 3, by_site: dict | None = None, items: list | None = None) -> dict:
    return {"checked": True, "left": 9, "explained": {"merged": 6}, "unexplained": {"no_file": 1, "emptied": 1,
                                                                                     "no_row": 1},
            "unexplained_total": n, "by_site": by_site if by_site is not None else {"a.example": 2, "b.example": 1},
            "items": ITEMS if items is None else items, "waves": {}, "limits": {"total": 30, "site": 15},
            "words": WORDS}


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db", sites=[("a.example", "甲處", 2, "exp/a"), ("b.example", "乙處", 1, "exp/b")])
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def cells(t: dict) -> dict:
    return {c["key"]: c for c in t["shown"]["cells"]}


def test_the_three_new_error_values_read_as_words():
    assert nightly.error_words("disk_low_12GB") == "C 盤只剩 12 GB，沒建庫"
    assert nightly.error_words("build_timeout") == "建庫超過 3 小時停掉"
    assert nightly.error_words("checks_timeout") == "換庫後的檢查超時（庫已換上）"


def test_a_swapped_night_with_unexplained_departures_is_lit_and_names_its_sites(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("swapped", unexplained_left=3, departures=departures())], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    swap = cells(t)["swap"]
    assert swap["state"] == nightly.DONE and swap["sev"] == WARN and swap["fix"] == CONTENT
    assert "但有 3 份說不出原因地離開" in swap["say"] and "a.example 2" in swap["say"]
    assert t["shown"]["state"] == WARN
    s = t["sentence"]
    assert s["state"] == WARN and s["fix"] == CONTENT
    assert s["text"].startswith("11-16 夜換了新庫，但有 3 份說不出原因地離開") and "交給" not in s["text"]
    # the night carries the build's own list for the pages (section 3, a site's page)
    got = t["shown"]["departures"]
    assert got["unexplained"] == 3 and got["by_site"] == {"a.example": 2, "b.example": 1} and len(got["items"]) == 3
    # 問題: the swap's row, the ingest line's own words (spec 八.5)
    rows = tonight_page.problems(t, {"steps": []}, [])
    assert [(x["where"], x["state"]) for x in rows] == [("換庫", "完成")]
    assert rows[0]["reason"] == "換上了；說不出原因地離開 3 份（a.example 2、b.example 1）" and rows[0]["code"] == "swapped"
    # nothing unexplained: a normal night as before
    ingest(where, [run("swapped", unexplained_left=0, departures=departures(0, {}, []))], SAID)
    calm = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert calm["sentence"]["state"] == OK and cells(calm)["swap"]["sev"] == OK


def test_the_build_s_count_beats_the_panels_comparison_in_the_sentence(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped", unexplained_left=2, departures=departures(2, {"a.example": 2}))], SAID)
    changes = {"state": "ready", "night": "2026-11-16",
               "totals": {"new": 4, "changed": 1, "left": 9, "unexplained": 5, "look": 0, "look_fix": ""}}
    s = nightly.tonight(where, changes=changes, now=at("2026-11-17T09:00:00"))["sentence"]
    assert s["state"] == WARN and "新進 4 份、離開 9 份" in s["text"] and "但有 2 份說不出原因地離開" in s["text"]


@pytest.mark.parametrize("reasons, said", [
    (["unexplained_departures"], "說不出原因的離開太多（3 份）"),
    (["school_wave"], "某站一次被標成學校刪掉太多（a.example）"),
    (["site_collapsed"], "某站一次少了一半以上"),
    (["site_collapsed", "unexplained_departures"], "某站一次少了一半以上、說不出原因的離開太多（3 份）"),
])
def test_a_held_night_says_which_guard_held_it(where, reasons, said):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    dep = {**departures(), "waves": {"a.example": [40, 120]}}
    held = {"reason": reasons[0], "reasons": reasons, "db": "aca.held.db",
            "detail": [{"site": "c.example", "before": 100, "after": 30}] if "site_collapsed" in reasons else []}
    ingest(where, [run("held", step="build", unexplained_left=3, departures=dep, held=held)], SAID[:5])
    swap = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["swap"]
    assert swap["state"] == nightly.STOP and swap["say"] == f"把關沒過，新庫沒換上：{said}"
    if "site_collapsed" in reasons:
        assert "c.example：100 → 30 份" in swap["tip"]
    if "unexplained_departures" in reasons:
        assert "a.example 2" in swap["tip"]


def test_a_low_disk_stops_before_the_build_and_says_so(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("failed", step="convert", error="disk_low_12GB", finished="2026-11-17T02:31:00+08:00")],
           SAID[:3])
    c = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))
    assert c["totext"]["state"] == nightly.DONE
    assert c["build"]["state"] == nightly.STOP and c["build"]["say"] == "C 盤只剩 12 GB，沒建庫"
    assert c["build"]["sev"] == BAD


# ---------------------------------------------------------------- the nights' record


def test_the_record_takes_the_build_s_unexplained_and_keeps_the_comparisons_new_and_changed(where, tmp_path_factory):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("swapped", unexplained_left=2, departures=departures(2, {"a.example": 2}),
                       library={"before": {"docs": 10}, "after": {"docs": 5}})], SAID)
    changes = {"state": "ready", "night": "2026-11-16",
               "totals": {"new": 4, "changed": 1, "left": 9, "unexplained": 5, "look": 0},
               "sites": [{"site": "a.example", "name": "甲處", "new": 4, "left": 6, "unexplained": 3},
                         {"site": "b.example", "name": "乙處", "new": 0, "left": 3, "unexplained": 2}]}
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")

    class Fixed:
        def snapshot(self):
            return changes

    history.Recorder(store, where, changes=Fixed()).tick(now=at("2026-11-17T09:00:00"))
    got = store.get("2026-11-16")
    assert (got["new"], got["changed"], got["left"]) == (4, 1, 9)  # the comparison's
    assert got["unexplained"] == 2 and got["unexplained_by"] == "build" and got["unexplained_other"] == 5
    per = {s["site"]: (s["new"], s["left"], s["unexplained"]) for s in got["sites"]}
    assert per == {"a.example": (4, 6, 2), "b.example": (0, 3, 0)}  # unexplained per site: the build's by_site
    page = tonight_page.nightbars_data(history.home_history(store, date(2026, 11, 16)), {})
    last = page["nights"][-1]
    assert last["unexplained"] == 2 and "建庫自己核對的" in last["source"] and "面板比對的是 5" in last["source"]


# ---------------------------------------------------------------- the pages list the build's items


def test_the_home_and_a_site_list_the_build_s_unexplained_items_in_words(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped", unexplained_left=3, departures=departures())], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    html = home_page.changes_section({"state": "none"}, t)
    tree = LexborHTMLParser(html)
    rows = tree.css(".v3-bad li")
    assert len(rows) == 3 and "說不出原因" in tree.css_first(".v3-bad h3").text()
    first = rows[0]
    assert first.css_first("a.v3-doc").attributes["href"] == "/trace?ref=https%3A%2F%2Fa.example%2F1"
    assert first.css_first("a.v3-doc").text() == "第一份<b>" and "<b>" not in html.replace("&lt;b&gt;", "")
    assert "導出那一行沒有檔了" in first.text()  # the build's Simplified words, shown Traditional
    assert rows[1].css_first("a.v3-doc").text() == "https://a.example/2"  # no title: its address
    none = {"state": "none", "words": "沒有前一份庫可以比"}
    one = site_page.changes_html(none, "a.example", t["shown"]["departures"])  # a site's page: its own items only
    assert len(LexborHTMLParser(one).css(".v3-bad li")) == 2 and "b.example" not in one and "/trace?ref=" in one
    assert "v3-bad" not in site_page.changes_html(none, "c.example", t["shown"]["departures"])


def test_a_held_night_changed_nothing_in_the_library_whatever_the_held_one_lost(where, tmp_path_factory):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    held = {"reason": "unexplained_departures", "reasons": ["unexplained_departures"], "detail": []}
    ingest(where, [run("held", step="build", unexplained_left=40, departures=departures(40), held=held)], SAID[:5])
    store = history.Store(tmp_path_factory.mktemp("ops") / "ops.db")
    history.Recorder(store, where).tick(now=at("2026-11-17T09:00:00"))
    got = store.get("2026-11-16")
    assert got["swapped"] == 0 and got["unexplained"] == 0 and got["new"] == 0 and got["unexplained_by"] == "ingest"
