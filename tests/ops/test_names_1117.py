"""The names the owner asked to be better (11-17: 「你先改再说吧，然后把名字什么的都优化一下，一个页面叫"今晚"你觉得合理吗」),
picked with a cold reader: the bar's entries, the home's section titles, a night named by its evening, a few sentences
said the way a person would, the night clock without nights before the chain existed and with labels in time order,
and a site's 建庫 row that says why it has no share. Made-up data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import charts, home_page, look, nightly, pipeline, site_page, tonight_page
from nknu.ops.pipeline import PIPE, Where
from nknu.ops.theme import bar
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, library, run
from tests.ops.test_panels_v3 import hist, row_and_detail, snap, tonight

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db")
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def test_the_bar_names_each_page_for_what_it_answers():
    pages = json.loads((REPO / "src/nknu/ops/theme/panels.json").read_text(encoding="utf-8"))["pages"]
    # 總覽 first since 11-22; since that evening three entries by the question each answers (每晚運行 is reached from
    # 總覽, 單位 from 內容, and 文件搜尋 is the bar's box)
    assert [p["name"] for p in pages] == ["總覽", "內容", "檢索"]
    assert all(p["tip"] and "\n" not in p["tip"] and "今晚" not in p["tip"] for p in pages)
    html = bar.bar("tonight", "ops")
    assert "aria-current='true'>總覽</a>" in html and "每晚運行" not in html  # a night marks the entry it is reached from
    assert "<title>每晚運行 · 檢視台</title>" in look.shell("每晚運行 · 檢視台", "", {}).body.decode()


def test_the_home_sections_have_their_new_titles_and_no_tonight_or_last_night():
    html = home_page.home_body(snap(), tonight(), None, hist(), {})
    titles = [h.text() for h in LexborHTMLParser(html).css("section.v3-q .v3-h h2")]
    assert titles == ["流程", "每晚紀錄", "上次換庫的變動"]  # 問題 left for 總覽's 要處理 (2026-11-22 evening)
    assert "今晚" not in html and "昨晚" not in html and "每晚紀錄" in tonight_page.PIPE_TIP


def test_a_night_is_named_by_its_evening_and_the_sentence_starts_with_it(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("swapped")], SAID)
    for now in ("2026-11-17T05:00:00", "2026-11-17T09:00:00", "2026-11-18T09:00:00"):  # no 06:00 switch any more
        assert nightly.label_of(nightly.date(2026, 11, 16), at(now)) == "11-16 夜"
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert t["sentence"]["text"] == "11-16 夜正常：換了新庫"
    data = tonight_page.timeline_data(t, at("2026-11-17T09:00:00"))
    assert data["nights"][0]["title"] == "11-16 夜" and data["nights"][0]["tip"] == "11-17 01:00 開跑"
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    running = nightly.tonight(where, now=at("2026-11-17T01:40:00"))
    assert running["sentence"]["text"] == "11-16 夜正在跑：爬取（抓網頁），已經 40 分鐘"


def test_the_night_clock_has_no_rows_before_the_chain_and_puts_a_short_steps_label_after_it(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    data = tonight_page.timeline_data(t, at("2026-11-17T09:00:00"))
    assert [r["title"] for r in data["nights"]] == ["11-16 夜"]  # the six nights before it went live: no rows
    # a label too long for its step goes after it in time order: on its row's line, else on the line under it
    assert "below" in charts.JS and "x-4,cy,'right'" not in charts.JS


def test_the_export_warning_is_the_counts_and_names_no_one(tmp_path, monkeypatch):
    from nknu.retrieval import vectors

    monkeypatch.setattr(vectors, "_CACHE", {})
    words = pipeline.refill_words(0, 3)
    assert words == "用舊存檔頂替的筆數：庫用的導出 0，新導出 3"  # data, no sentence for one night (spec 八.1)
    row = {"key": "e.example", "name": "英語學系", "worst": "warn", "cells": {"refill": {"n": 3, "state": "warn"}},
           "why": [words], "issues": [{"col": "refill", "state": "warn", "why": words}]}
    rows = tonight_page.handle_rows({"sites": [row]}, None)
    items = tonight_page.problems(tonight(), {"steps": []}, rows)
    assert [(x["where"], x["reason"]) for x in items] == [("英語學系", "用舊存檔頂替的筆數：庫用的導出 0，新導出 3")]
    src = (REPO / "src/nknu/ops/pipeline.py").read_text(encoding="utf-8")
    assert "舊資料補的" not in src and "檢查提醒" not in src


def test_a_sites_build_row_says_why_it_has_no_share_and_its_chip_matches_its_reasons():
    row, detail = row_and_detail()
    nodes = site_page.site_nodes(site_page.site_stages(row, detail), row, detail)
    html = site_page.step_panel(nodes[3], detail)  # 建庫: no count of what went in for one site
    build = LexborHTMLParser(html)
    assert [d.text() for d in build.css(".hm-io dt")] == ["出"] and not build.css(".hm-split")
    said = [p.text() for p in build.css(".hm-words")]
    assert said[0].startswith("分得出的 9 份文件") and "只有「出」" in said[1]
    assert [r.text() for r in build.css(".rs-n")] == ["7", "2"]  # 7 merged + 2 the school took away: the reasons
    assert "建庫拿掉的" not in html and "標出的缺漏" not in html and "%" not in build.text()
