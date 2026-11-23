"""A site's page (/site, nknu.ops.site_page) says the 11-23 words (names.py) in its 流程 step panels, the same words
內容 (night_view) and the drawers (raw.py) say: the headings (names.TITLES, the long label in the hover), the crawl's
export results (the fold under 爬取), the to-text statuses (list and split-bar tips), the export's marks and the
build's rows (audit items C24, NV38, CR-11, site:step-panel-words of research/design_2026-11-23/
port_audit_open_1123.json). Made-up site, the export's own Simplified words as the manifest writes them."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.ops import pipeline, pipeline_page, site_page
from nknu.ops.look import s2t
from nknu.ops.names import LINKS, NAMES, TITLES
from nknu.ops.pipeline import OK

# the crawler's old words the live aca page showed in its step panels (the audit's evidence), none of which may stay
OLD = ("學校那邊已經不存在", "鏈接已失效：跳回學校首頁", "下一輪再抓", "被拒絕（401、403、451）", "要登錄，進不去",
       "Google API 讀不到", "檔案太大，沒抓", "讀不出文字", "照規則跳過", "轉文字失敗", "轉到別的網址（另外收）", "照規則不抓",
       "還沒抓到的（照爬蟲自己的分類）", "清單上註明缺東西的（一筆可能缺好幾樣）", "別處有一樣的、併成一份",
       "學校刪掉或轉走、沒收的公告", "連接失敗", "附件沒抓到")


def row_and_detail(*, old_cells: bool = False) -> tuple[dict, dict]:
    """aca's shape: the crawl cell's reasons (pipeline's words, or a reading from before the rename: old_cells), the
    export's results in the manifest's Simplified words, its marks, the to-text accounting, the build's merges."""
    lost = ([["gone", "學校那邊已經不存在", 67], ["failed", "連接失敗", 5], ["login", "要登入", 1]] if old_cells
            else [["gone", "校方已移除", 67], ["failed", "連線失敗", 5], ["login", "需要登入", 1]])
    row = {"key": "a.example", "name": "甲處", "worst": OK,
           "cells": {"crawl": {"n": 927, "state": OK, "whole": 1000, "lost": lost}, "export": {"n": 900, "state": OK},
                     "text": {"n": 850, "state": OK}, "docs": {"n": 315, "state": OK}},
           "why": [], "issues": []}
    missing = {"附件沒抓到": 6, "留到下一輪補抓": 2} if old_cells else {"附件未取得": 6, "延至下一輪": 5}
    missing_examples = {next(iter(missing)): [("https://a.example/f1", "表一")]}
    outcomes = {"抓到了": 900, "链接已失效：跳回学校首页": 133, "学校那边已经不存在": 108, "跳到别的网址（目标另算）": 20,
                "按规则不抓": 12, "下一轮再抓": 5, "被拒绝（401、403、451）": 4, "要登录，进不去": 3,
                "Google API 读不到（不公开或已删除，分不出）": 3, "档案太大，没抓": 2, "还没抓": 1}
    detail = {"site": {"site": "a.example", "name": "甲處", "docs": 315, "deleted_notices": 2},
              "export": {"rows": 1200, "files": 900, "missing": missing, "missing_examples": missing_examples,
                         "outcomes": outcomes, "folder": "a.example-20261123-010000",
                         "exported_at": "2026-11-23T01:00:00+08:00", "paging": 0, "refill": {}, "refills": [],
                         "examples": {"学校那边已经不存在": [("https://a.example/gone", "舊公告")]}},
              "text": {"status": {"ok": 850, "empty": 44, "skipped": 5, "failed": 1},
                       "bad": [("failed", "PDF 壞了", "files/x.pdf"), ("empty", "OCR 沒認出字", "files/y.png")]},
              "copies": {"merged": 7}, "errors": [], "flags": []}
    return row, detail


def panels(**kw) -> dict[str, LexborHTMLParser]:
    row, detail = row_and_detail(**kw)
    nodes = site_page.site_nodes(site_page.site_stages(row, detail), row, detail)
    return {x["key"]: LexborHTMLParser(site_page.step_panel(x, detail)) for x in nodes}


def test_the_step_headings_are_the_short_titles_with_the_long_label_in_the_hover():
    got = panels()
    heads = {k: p.css_first("h4") for k, p in got.items()}
    assert {k: h.text() for k, h in heads.items()} == {
        "crawl": "未取得", "export": "清單標註的缺漏", "totext": "未轉出", "build": "未成為文件"}
    # the same short names 內容 gives the same lists (pipeline's step labels through names.TITLES)
    assert heads["crawl"].text() == TITLES["還沒抓到的（照爬蟲自己的分類）"]
    assert heads["export"].text() == TITLES["清單上註明缺東西的（一筆可能缺好幾樣）"]
    assert heads["totext"].text() == TITLES["沒走到下一步的"]
    assert heads["build"].text() == TITLES["沒成為文件的（大多是合併掉的重複）"]
    assert heads["crawl"].attributes["data-tip"] == "還沒抓到的（照爬蟲自己的分類）"
    assert heads["build"].attributes["data-tip"] == "沒收成文件的（這個網站分得出的）"


def test_the_crawl_fold_says_the_export_results_in_the_new_words():
    crawl = panels()["crawl"]
    fold = crawl.css_first("details.st-fold")
    assert [x.text() for x in fold.css(".pl-why")] == [
        "連結失效（轉回首頁）", "校方已移除", "已轉址", "依規則不抓", "延至下一輪", "遭拒（401／403／451）", "需要登入",
        "雲端檔案無法讀取", "檔案過大", "尚未抓取"]
    assert [x.text() for x in fold.css(".num")][:2] == ["133", "108"]
    # a result's examples still open 網頁核查 on that page (looked up by the export's own word)
    gone = next(li for li in fold.css(".pl-reasons li") if li.css_first(".pl-why").text() == "校方已移除")
    assert gone.css_first("a.pl-eg1").attributes["href"] == "/check?ref=https%3A%2F%2Fa.example%2Fgone"


def test_the_to_text_list_and_its_split_bar_say_the_new_words():
    text = panels()["totext"]
    assert [x.text() for x in text.css(".rs-w")] == ["無文字可讀", "依規則略過", "轉換失敗"]
    tips = [i.attributes.get("data-tip") for i in text.css(".hm-split i")]
    assert tips[1:] == ["無文字可讀：44", "依規則略過：5", "轉換失敗：1"]
    assert site_page.STATUS_TEXT == pipeline.STATUS_WORDS  # one table: what 內容 and the drawers say


def test_the_build_rows_are_the_words_of_what_the_build_left_out():
    build = panels()["build"]
    assert [x.text() for x in build.css(".rs-w")] == ["重複內容，已合併", "已刪公告空殼"]
    # the same words 內容's 建庫 gives the build's own keys for a merged copy and a deleted notice's shell
    assert pipeline.left_out_words("notice(副本)") == "重複內容，已合併"
    assert pipeline.left_out_words("notice(学校删掉)") == "已刪公告空殼"


@pytest.mark.parametrize("old_cells", [False, True])
def test_no_old_word_is_left_in_the_step_panels(old_cells):
    for key, p in panels(old_cells=old_cells).items():
        shown = p.text() + "\n".join(i.attributes.get("data-tip") or "" for i in p.css(".hm-split i"))
        assert [w for w in OLD if w in shown] == [], key


def test_a_reading_from_before_the_rename_is_shown_in_the_new_words_its_examples_kept():
    got = panels(old_cells=True)
    assert [x.text() for x in got["crawl"].css(".rs-w")] == ["校方已移除", "連線失敗", "需要登入"]
    assert [i.attributes["data-tip"] for i in got["crawl"].css(".hm-split i")][1:] == [
        "校方已移除：67", "連線失敗：5", "需要登入：1"]
    export = got["export"]
    assert [x.text() for x in export.css(".rs-w")] == ["附件未取得", "延至下一輪"]
    att = next(li for li in export.css("ul.rsd > li") if li.css_first(".rs-w").text() == "附件未取得")
    assert att.css_first("details .rs-items a").attributes["href"] == "/check?ref=https%3A%2F%2Fa.example%2Ff1"


def test_every_crawler_result_names_py_renames_is_shown_renamed():
    outcome = pytest.importorskip("nknu.archive.outcome")
    for raw in outcome.RESULT_LABEL.values():
        old = s2t(raw)
        if old in NAMES:
            assert pipeline_page.outcome_word(raw) == NAMES[old], raw
        elif old in LINKS:
            assert pipeline_page.outcome_word(raw) == LINKS[old], raw
    assert pipeline_page.outcome_word(outcome.GONE_WHY_LABEL[outcome.GONE_HOME]) == "連結失效（轉回首頁）"


def test_two_results_that_now_share_a_word_are_one_row_with_both_examples():
    html = pipeline_page._bars({"下一轮再抓": 2, "留到下一輪補抓": 3, "学校那边已经不存在": 1}, pipeline_page.outcome_word,
                               {"下一轮再抓": [("https://a.example/1", "一")], "留到下一輪補抓": [("https://a.example/2", "二")]})
    rows = LexborHTMLParser(html).css(".pl-reasons li")
    assert [(r.css_first(".pl-why").text(), r.css_first(".num").text()) for r in rows] == [("延至下一輪", "5"),
                                                                                            ("校方已移除", "1")]
    assert [a.text() for a in rows[0].css("a.pl-eg1")] == ["一", "二"]


def test_the_whole_page_flow_side_has_no_old_word():
    row, detail = row_and_detail(old_cells=True)
    html = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=site_page.now_line(row, None, {}, read=True), detail=detail,
        changes={"state": "none", "words": "沒有前一份庫可以比"}, changes_title="", view=None, archive=False,
        kinds={}, tables="", row=row, hist={"enabled": False, "why": "x"})
    side = LexborHTMLParser(html).css_first("aside.hm-side")
    assert len(side.css("article.hm-panel")) == 4
    text = side.text() + "\n".join(i.attributes.get("data-tip") or "" for i in side.css(".hm-split i"))
    assert [w for w in OLD if w in text] == []
