"""檢索 (/try) after the owner's 11-23 design copy (research/design_2026-11-23/try.html, why.html; search_design.py):
the search options one word each, the code name on hover; with nothing asked, 手動檢查: the 33 questions as one card
(each marked with what the cleaned answer key says of it, research/eval_2026-11-01/also_right_1122.json) beside the
real-data checks' last manual run (checks.md: 22 squares, the failures in their own words); on 為什麼這樣排 the formula
as chips drawn by the server (only when a rerank shaped the order), the rows that also count as right tagged, and the
verdict said as 「第 N 名就答對 · 前 10 名裡 N 份答對」."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from nknu.core.timeutil import TAIPEI
from nknu.ops import design, search_page
from nknu.ops.try_page import fields, gloss, name_html, param_html
from tests.ops.test_pages import _explained

SEARCH = {"type": "object", "required": ["query"], "properties": {
    "query": {"type": "string", "description": "學生的問題"},
    "detail": {"type": "string", "enum": ["snippet", "passage", "full"], "default": "passage",
               "description": "每筆給多少原文。passage（預設）：每筆約 500 字。snippet：約 200 字。full：全文。"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10, "description": "要幾筆，1～50，預設 10。"},
    "unit": {"type": "string", "description": "只看某個單位的資料：填單位名稱（例如「教務處」）。"},
    "sort": {"type": "string", "enum": ["relevance", "date"], "default": "relevance",
             "description": "relevance（預設）：最相關的在前。date：由新到舊。"}}}

QUESTIONS = [{"id": "Q01", "question": "115-1 什麼時候可以申請停修？", "gold": ["g1"]},
             {"id": "Q02", "question": "期中考完還能退選嗎？<b>", "gold": ["g2"]},
             {"id": "N02", "question": "學號", "gold": ["g3"]},
             {"id": "N05", "question": "停車", "gold": ["g4"]}]
KEY = {"day": "2026-11-22",
       "also_right": {"Q01": [{"doc_id": "a1", "why": "教务处的停修公告原件"}, {"doc_id": "a2", "why": "转贴"}],
                      "Q02": [{"doc_id": "a3", "why": "同一句答案"}]},
       "superseded": {"Q02": [{"doc_id": "g2", "by": "n2", "reason": "这是旧学期的公告"}]},
       "stale": {"N02": [{"doc_id": "g3", "reason": "那一届早已入学"}]}}

CHECKS = """# 教务处管线：真资料检查

- 资料库：`research/pipeline_2026-11-02/aca.db`（4505 MB，建于 2026-11-23T02:53:09+08:00，资料截至 2026-11-22）
- 结果：2 过、1 不过

| # | 检查 | 结果 | 数字 |
|---|---|---|---|
| 1 | 文件数（按种类）和排除数 | PASS | 89168 份；排除 43813 |
| 7 | 「退選」找得到停修公告 | FAIL | 前 10 名里停修公告 0 则；别名 ['退選'] |
| 8 | <script>x</script> | PASS | a & b |

## 1. 文件数（按种类）和排除数：PASS

| 9 | 细节里的表 | FAIL | 不算进上面那张表 |
"""
RAN = datetime(2026, 11, 23, 3, 14, tzinfo=TAIPEI)


# ---------------------------------------------------------------- the search options: one word each

def test_each_search_option_is_one_word_and_its_code_name_floats_over_it():
    by = {f.name: f for f in fields(SEARCH)}
    assert {n: gloss(f) for n, f in by.items() if n != "query"} == {
        "detail": "原文", "limit": "筆數", "unit": "單位", "sort": "排序"}
    assert name_html(by["limit"]) == "<span class='tr-g'>筆數</span>"  # no inline code name
    for name in ("detail", "limit", "unit", "sort"):
        drawn = param_html(by[name], None, "", "search")
        assert "<code>" not in drawn
        label = LexborHTMLParser(drawn).css_first(".tr-k")
        assert label.attributes["data-tip-h"].startswith(name + "　")  # the name the agent sends, on hover
    unknown = fields({"type": "object", "properties": {"made_up": {"type": "string", "description": "Whatever."}}})[0]
    assert name_html(unknown) == "<code>made_up</code>"  # a parameter with no word keeps its own name


# ---------------------------------------------------------------- 手動檢查: the 33 questions and the real-data checks

def test_the_manual_checks_are_two_cards_the_questions_with_their_marks_and_a_legend():
    board = search_page.tests_html(QUESTIONS, key=KEY, checks={"text": CHECKS, "ran": RAN})
    assert "<h2 class='sec' id='tests'>手動檢查</h2>" in board and "測試題" not in board
    tree = LexborHTMLParser(board)
    cards = tree.css(".sd-two > section.card")
    assert len(cards) == 2 and cards[0].css_first(".sd-h h3").text() == "4 題"
    assert cards[1].css_first(".sd-h h3").text() == "真資料檢查"
    marks = {a.css_first(".tq-id").text(): [(m.attributes["class"], m.text()) for m in a.css(".sd-m")]
             for a in cards[0].css("ul.tq li > a")}
    assert marks == {"Q01": [("sd-m also", "+2")], "Q02": [("sd-m gone", "被取代"), ("sd-m also", "+1")],
                     "N02": [("sd-m old", "舊版")], "N05": []}
    assert "另外 2 份也算對" in cards[0].css_first(".sd-m.also").attributes["data-tip"]
    assert "期中考完還能退選嗎？&lt;b&gt;" in board  # a question's words escaped
    legend = [s.text() for s in cards[0].css(".sd-legend .sd-m")]
    assert legend == ["+3", "舊版", "被取代"]
    assert "href='/try?tab=why&amp;qid=Q01'" in board


def test_without_the_answer_key_the_questions_have_no_marks_and_no_legend():
    board = search_page.tests_html(QUESTIONS)
    assert "<h3>4 題</h3>" in board and "class='sd-m" not in board and "sd-legend" not in board
    assert "還沒有真資料檢查的結果" in board  # the checks card says it has nothing, it is not dropped
    assert search_page.tests_html([]) == ""
    partly = search_page.tests_html(QUESTIONS, key={"also_right": {"Q01": [{"doc_id": "a1"}]}})
    assert [s.text() for s in LexborHTMLParser(partly).css(".sd-legend .sd-m")] == ["+3"]  # only the marks shown
    assert search_page.tests_html(QUESTIONS, key={"also_right": "broken"}).count("class='sd-m") == 0


def test_the_real_data_checks_card_is_the_last_runs_squares_and_its_failures_in_their_own_words():
    card = search_page.checks_html({"text": CHECKS, "ran": RAN})
    tree = LexborHTMLParser(card)
    assert tree.css_first(".sd-at").text() == "上次 11-23 03:14"
    assert "2026-11-23 02:53" in tree.css_first(".sd-at").attributes["data-tip"]  # which library it checked
    assert tree.css_first(".sd-n").text() == "2/3"
    squares = tree.css(".rc-grid i")
    assert [(i.attributes.get("class") or "", i.text()) for i in squares] == [("", ""), ("f", "7"), ("", "")]
    assert squares[1].attributes["data-tip-h"] == "第 7 項 · 沒過"
    assert squares[1].attributes["data-tip"] == "「退選」找得到停修公告\n前 10 名裡停修公告 0 則；別名 ['退選']"
    fails = tree.css(".rc-fail li")
    assert [(li.css_first("b").text(), li.css_first("span").text()) for li in fails] == [("7", "「退選」找得到停修公告")]
    assert fails[0].attributes["data-tip"] == "前 10 名裡停修公告 0 則；別名 ['退選']"
    assert "<script>x" not in card and "&lt;script&gt;x&lt;/script&gt;" in card
    assert "細節裡的表" not in card  # only the summary table
    clean = search_page.checks_html({"text": CHECKS.replace("| FAIL |", "| PASS |"), "ran": RAN})
    assert "rc-fail" not in clean and "都過了" in clean


def test_the_checks_and_the_answer_key_are_read_from_their_files(tmp_path):
    assert search_page.read_checks(tmp_path / "checks.md") is None
    path = tmp_path / "checks.md"
    path.write_text(CHECKS, encoding="utf-8")
    os.utime(path, (RAN.timestamp(), RAN.timestamp()))
    got = search_page.read_checks(path)
    assert got["text"] == CHECKS and got["ran"] == RAN and got["ran"].tzinfo is not None
    assert search_page.read_answer_key(tmp_path / "also_right_1122.json") is None
    (tmp_path / "also_right_1122.json").write_text(json.dumps(KEY, ensure_ascii=False), encoding="utf-8")
    assert search_page.read_answer_key(tmp_path / "also_right_1122.json") == KEY
    (tmp_path / "bad.json").write_text("[1, 2]", encoding="utf-8")
    assert search_page.read_answer_key(tmp_path / "bad.json") is None


def test_the_try_layer_styles_the_two_cards_and_the_marks():
    layer = design.layer("try")
    for selector in (".sd-two", ".sd-h", ".sd-m.also", ".sd-m.gone", ".sd-m.old", ".sd-legend", ".rc-grid", ".rc-fail"):
        assert selector in layer, selector


# ---------------------------------------------------------------- 為什麼這樣排: the formula, the rows that also count

def test_the_formula_is_chips_with_the_librarys_numbers_only_when_a_rerank_ordered(db):
    report = _explained(db, "停修")
    assert report["order"] == "reranked"
    report["weights"] = {**report["weights"], "rerank": 2.5, "valid_decay": 0.8}
    pop = LexborHTMLParser(search_page._help(report)).css_first(".pop")
    chips = [(i.attributes["class"], i.text()) for i in pop.css(".pd-f i")]
    assert chips == [("rel", "對題×2.5"), ("add", "學期"), ("add", "屆別"), ("add", "新舊"), ("cut", "還有效"),
                     ("cut", "對口"), ("add", "單位")]
    assert "每過一個學年 ×0.8" in pop.css_first(".pd-fn").text() and not pop.css("b")
    keyword = {**report, "order": "keyword", "mode": "keyword"}
    plain = LexborHTMLParser(search_page._help(keyword)).css_first(".pop")
    assert not plain.css(".pd-f") and plain.css_first("b").text().startswith("排序：只有關鍵字")
    assert ".pop>b" not in (design.DIR / "why.js").read_text(encoding="utf-8")  # the script no longer rewrites it


def test_rows_that_also_count_are_tagged_and_the_verdict_counts_every_right_one_in_the_top(db):
    report = _explained(db, "停修", follow=["d1"])  # d3 d2 d1 dx dept j1 dq
    question = {"id": "Q09", "question": "停修", "gold": ["d1"]}
    key = {"also_right": {"Q09": [{"doc_id": "d2", "why": "转贴的同一份公告"}, {"doc_id": "zz", "why": "不在库里"}]}}
    body = search_page.search_body(report, {"d1"}, question, key=key)
    tree = LexborHTMLParser(body)
    also = tree.css_first("details.row.also")
    assert also.attributes["id"] == "r-d2"
    chip = also.css_first(".ttl .sd-also")
    assert chip.text() == "也算對" and chip.attributes["data-tip"] == "也算對：轉貼的同一份公告"
    assert len(tree.css(".row.also")) == 1
    verdict = tree.css_first(".verdict .v-text")
    assert verdict.css_first("b").text() == "第 2 名就答對"
    said = verdict.css_first("span")
    assert said.text() == "前 10 名裡 2 份答對"
    assert said.attributes["data-tip"] == "登記的標準答案 1 份：第 3 名；另外 2 份也算對：第 2 名，1 份不在前 10 名"
    assert tree.css_first(".v-badge span").text().startswith("2")


def test_a_superseded_answer_does_not_count_and_a_stale_one_is_said(db):
    report = _explained(db, "停修", follow=["d3", "d1"])
    question = {"id": "Q09", "question": "停修", "gold": ["d3", "d1"]}
    key = {"also_right": {"Q09": [{"doc_id": "dx", "why": "新版"}]},
           "superseded": {"Q09": [{"doc_id": "d3", "by": "dx", "reason": "旧学期的公告"}]},
           "stale": {"Q09": [{"doc_id": "d1", "reason": "那一届早已入学"}]}}
    tree = LexborHTMLParser(search_page.search_body(report, {"d3", "d1"}, question, key=key))
    assert tree.css_first(".verdict .v-text b").text() == "第 3 名就答對"  # d3 at 1 is superseded; d1 at 3
    assert "其中 1 份已被新版取代，不算" in tree.css_first(".verdict .v-text span").attributes["data-tip"]
    gone = tree.css_first("#r-d3 .sd-also.gone")
    assert gone.text() == "被取代" and "舊學期的公告" in gone.attributes["data-tip"]
    assert tree.css_first("#r-d1 .sd-also.old").text() == "舊版"
    assert "row gold" in tree.css_first("#r-d3").attributes["class"]  # still the registered answer, marked as such
    nothing = {"superseded": {"Q09": [{"doc_id": "d3"}, {"doc_id": "d1"}]}}
    none = LexborHTMLParser(search_page.search_body(report, {"d3", "d1"}, question, key=nothing))
    assert none.css_first(".verdict .v-text b").text() == "前 10 名裡沒有答對的"


def test_a_registered_answer_out_of_the_top_or_not_in_the_library_is_still_counted_in_the_hover():
    follow = [{"doc_id": "g1", "ref": "g1", "verdict": "ranked", "row": {"rank": 24, "title": "甲"}, "words": ""},
              {"doc_id": None, "ref": "dp9", "verdict": "not_found", "row": None, "words": ""}]
    report = {"follow": follow, "limit": 10, "counts": {"final": 300}, "rows": [{"doc_id": "x", "rank": 1}]}
    tree = LexborHTMLParser(search_page.verdict_html(report, {"id": "Q09", "gold": ["g1", "dp9"]}, {
        "also": {}, "gone": {}, "old": {}}, {"g1"}))
    assert tree.css_first(".v-text b").text() == "前 10 名裡沒有答對的"
    said = tree.css_first(".v-text span")
    assert said.text() == "2 份標準答案：第 24 名"
    assert said.attributes["data-tip"] == "登記的標準答案 2 份：第 24 名、庫裡沒有 1 份"
    assert tree.css_first(".v-badge").attributes["class"] == "v-badge g-out"


def test_without_the_answer_key_the_verdict_keeps_its_words(db):
    report = _explained(db, "停修", follow=["d1"])
    question = {"id": "Q09", "question": "停修", "gold": ["d1"]}
    body = search_page.search_body(report, {"d1"}, question)
    assert "標準答案在第 3 名" in body and "row also" not in body and "sd-also" not in body
    typed = search_page.search_body(report, set(), None, key={"also_right": {"Q09": [{"doc_id": "d2"}]}})
    assert "指定的文件在第 3 名" in typed and "sd-also" not in typed  # a question typed by hand has no answer key


def test_the_why_layer_styles_the_tagged_rows():
    layer = design.layer("why")
    for selector in (".row.also", ".sd-also", ".sd-also.gone", ".sd-also.old", ".pd-f"):
        assert selector in layer, selector
    assert re.search(r"\.row\.also>summary\{", layer)
    # the formula sits in the legend's ? (.rhead .lg), whose own rule draws every <i> as a 9px swatch: the chips undo it
    assert ".rhead .lg .pd-f i.rel" in layer
