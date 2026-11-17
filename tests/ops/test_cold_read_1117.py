"""What a cold reader of the real-data screenshots (lab, 11-17 evening) could not read, fixed: the night clock says how
long each step took and draws the scheduled attempt that stopped short, the pipeline is four steps each on its own
terms (no flow of one quantity between rows) and says it is a standing total, a site's reasons add up to the losses
they explain, two times are named for what they are, a stopped scheduled run that was made good says so, the small
bars count whole documents, and a fresh page check says it has just begun. Made-up data only."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import charts, check_sites, look, nightly, site_page, tonight_page
from nknu.ops.pipeline import OK, PIPE, Where
from tests.ops.test_nightly import MANUAL_SAID, NIGHT, at, crawler_log, ingest, library, run
from tests.ops.test_panels_v3 import hist, row_and_detail, snap, tonight


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db")
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def test_the_night_clock_says_each_steps_time_and_draws_every_run_of_the_night(where):
    crawler_log(where, NIGHT[:6] + [("2026-11-17 02:07:00", "定期更新结束（导出 35 个网站；seen.tsv 写好了）")],
                at("2026-11-17T02:07:00"))
    paused = run("paused", step="convert", started="2026-11-17T02:41:57+08:00", finished="2026-11-17T02:47:00+08:00")
    manual = run("swapped", started="2026-11-17T03:51:00+08:00", finished="2026-11-17T04:30:00+08:00")
    ingest(where, [paused, manual], MANUAL_SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    segs = tonight_page.timeline_data(t, at("2026-11-17T09:00:00"))["nights"][0]["segs"]
    by = {s["key"]: s for s in segs}
    assert by["crawl"]["label"] == "爬取 51 分" and by["totext"]["label"] == "轉文字 4 分"
    # the paused run is a run like any other (spec 八.3): one 進庫 segment in its own state, nothing said of why
    first = by["run1"]
    assert (first["name"], first["word"], first["tone"], first["run"]) == ("進庫", "停了", "warn", 1)
    assert first["from"] == pytest.approx(2 + 42 / 60, abs=0.02) and first["to"] == pytest.approx(2 + 47 / 60, abs=0.02)
    assert [s["key"] for s in segs] == ["crawl", "export", "run1", "totext", "build", "swap"]  # in time order
    assert by["totext"]["run"] == 2
    # a short step's label goes outside it, where its row is free: the gap before and after it, in hours
    tt = by["totext"]
    assert tt["gap_before"] == pytest.approx(3 + 51 / 60 - (2 + 47 / 60), abs=0.02) and tt["gap_after"] == pytest.approx(0, abs=0.02)
    assert "minW" in charts.JS and "p.below" in charts.JS  # the drawing keeps a width and labels in time order


def test_the_pipeline_is_four_steps_each_its_own_share_and_says_it_is_a_standing_total():
    stages = tonight_page.funnel_data(snap())
    assert [(s["into"], s["value"]) for s in stages] == [(120, 100), (200, 110), (110, 104), (104, 70)]
    assert [round(s["share"], 3) for s in stages] == [0.833, 0.55, 0.945, 0.673]
    assert stages[0]["flow"] == "進 120 個網址 → 出 100 個網址" and stages[1]["flow"] == "進 200 筆 → 有檔 110 筆"
    assert stages[3]["flow"] == "進 104 筆 → 出 70 份文件"
    html = tonight_page.home_body(snap(), tonight(), None, hist(), {})
    tree = LexborHTMLParser(html)
    sec = tree.css_first("section[data-q='4']")
    assert sec.css_first(".v3-h h2").text() == "整體流程（累計）" and "不是哪一晚" in sec.css_first(".v3-h h2").attributes["data-tip"]
    fills = [r.css_first(".fn-fill").attributes["style"] for r in sec.css(".fn-row")]
    assert fills[0].startswith("width:83.3") and fills[1].startswith("width:55.0")  # the share, not one scale
    assert "fn-whole" not in html and sec.css_first(".fn-row .fn-flow").text() == stages[0]["flow"]


def test_a_sites_reasons_add_up_to_the_losses_they_explain():
    row, detail = row_and_detail()
    bars, _table, _count = site_page.reasons_html(site_page.site_stages(row, detail))
    heads = [h.text() for h in LexborHTMLParser(bars).css(".rs-head")]
    assert heads == ["爬取　少 10 個網址，都分了類", "導出　沒存檔 25 筆；清單上註明缺東西的 6 處（一筆可能缺好幾樣）",
                     "轉文字　少 7 個檔，都分了類", "建庫　分得出的 9 份文件（這一步比不出這個網站總共少多少）"]


def test_two_times_are_named_for_what_they_are():
    page = look.shell("x", "", {"built_at": "2026-11-17T04:02:00+08:00"}).body.decode()
    assert "庫建於 11-17 04:02</span>" in page and "資料截至 2026" not in page
    html = tonight_page.home_body(snap(), tonight(), None, hist(), {})
    assert "讀於" not in html and "頁面更新 11-17 08:00" in html


def test_no_sentence_is_written_about_a_run_that_was_made_good():
    assert not hasattr(nightly, "first_note")  # no narrative of one night (spec 八.3); its runs are drawn instead


def test_the_small_bars_count_whole_documents_and_the_last_swap_is_named():
    assert "minInterval:1" in charts.JS
    row, detail = row_and_detail()
    html = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=(OK, "每一步都正常"), detail=detail,
        changes={"state": "none", "words": "沒有前一份庫可以比"}, changes_title="昨晚變了什麼", view=None,
        archive=False, kinds={}, tables="", row=row, hist={"enabled": False, "why": "x"})
    lt = LexborHTMLParser(html).css_first(".st-lt")
    assert lt.text().startswith("上次換庫的變動") and "前一份庫" in lt.attributes["data-tip"]


def test_a_page_check_just_begun_says_so_and_how_long_it_takes():
    fresh = check_sites.summary_line({"phase": "checking", "running": True, "ready": True, "done": 0, "total": 1861},
                                     {"pages": 1861, "checked": 0})
    assert "剛重啟，正在逐頁核查（全部約 17 分鐘）：0 / 1,861 頁" in fresh
    later = check_sites.summary_line({"phase": "checking", "running": True, "ready": True, "done": 40, "total": 1861},
                                     {"pages": 1861, "checked": 40})
    assert "正在逐頁核查 40 / 1,861 頁（全部約 17 分鐘）" in later

