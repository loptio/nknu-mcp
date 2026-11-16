"""11-16 (the owner: 「碎字pdf你现在去查原因和调查 来决定要用什么工具」): what the investigation found, and the rule that
came of it. Of the 1,031 attachments whose text is mostly one-character lines, hardly any is a file the converter
could not read. Over half are official letters whose body reads well and whose left margin carries the dotted binding
line (「…裝…訂…線…」), each dot a line of its own; the charts and forms (the table of required courses) have labels
set vertically, which come out one character a line, in the right order. So no other tool: a run of one-character
lines loses its leader marks and, where it holds two or more Han characters, becomes one line (documents.
join_single_lines; the build's --no-single-lines keeps the text as converted). On a trial build of the 111 sites of
11-16: 1,031 such files before, 24 after (those left are posters and exam sheets whose pictures the OCR read).
Sentences here are made up."""
from __future__ import annotations

import json
import sqlite3

from nknu.content.documents import build_documents, join_single_lines
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content.test_validity_1114 import ACA, ACA_SITE, PDF


def lines(*parts: str) -> str:
    return "\n\n".join(parts)


def test_the_dotted_binding_line_of_a_letter_goes_and_its_three_characters_join():
    margin = [".", ".", ".", "裝", ".", ".", ".", "訂", ".", ".", "線", ".", "."]
    text = lines("檔號：保存年限：", *margin, "## 某某部函", "主旨：請查照並轉知所屬。")
    got, dropped, joined = join_single_lines(text)
    assert got == lines("檔號：保存年限：", "裝訂線", "## 某某部函", "主旨：請查照並轉知所屬。")
    assert (dropped, joined) == (10, 2)


def test_a_label_set_vertically_becomes_one_line():
    text = lines("校定共同必修科目", "28 學分", "進", "階", "英", "文", "2", "學", "分", "## 說明：", "- 一、經教務會議通過。")
    got, dropped, joined = join_single_lines(text)
    assert got == lines("校定共同必修科目", "28 學分", "進階英文2學分", "## 說明：", "- 一、經教務會議通過。")
    assert (dropped, joined) == (0, 6)


def test_lines_without_a_blank_between_them_join_too_and_keep_what_follows_apart():
    assert join_single_lines("表頭\n學\n分\n合計 28")[0] == "表頭\n學分\n合計 28"


def test_what_is_no_vertical_text_stays_as_it_was():
    for text in (lines("第一題", "1", "2", "3", "第二題"),  # page numbers, options: no Han character
                 lines("請選出正確答案", "A", "B", "C", "D"),
                 lines("校", "本校訂於下週舉行座談。"),  # one character alone is no run
                 "| 系 | 所 | 學分 |\n| - | - | - |\n| 甲 | 乙 | 3 |",  # table rows are no one-character lines
                 lines("## 壹", "- 一", "內文")):
        assert join_single_lines(text) == (text, 0, 0)


def test_a_few_dots_are_kept_and_a_run_of_nothing_but_marks_goes():
    text = lines("單價", ".", "元", "合計")
    assert join_single_lines(text) == (text, 0, 0)  # one mark between two characters that are no run of Han: kept
    got, dropped, joined = join_single_lines(lines("第一段。", "●", "●", "●", "第二段。"))
    assert got == lines("第一段。", "第二段。") and (dropped, joined) == (3, 0)


def test_joining_again_changes_nothing():
    text = lines("通", "識", "科", "目", "10 學分", ".", ".", ".", ".", ".", "裝")
    once = join_single_lines(text)[0]
    assert once == lines("通識科目", "10 學分", "裝") and join_single_lines(once) == (once, 0, 0)


def test_the_build_joins_them_and_the_switch_keeps_the_converted_text():
    text = lines("選課流程圖", "加", "退", "選", "期", "間", "受理停修申請。")
    rec = tc.rec("a1", PDF, ACA + "f/1/流程圖.pdf", "流程圖.pdf", text, ACA_SITE)
    (doc,), _ = build_documents([rec], {}, [])
    assert doc.text == lines("選課流程圖", "加退選期間", "受理停修申請。") and (doc.lines_joined, doc.marks_dropped) == (4, 0)
    (plain,), _ = build_documents([rec], {}, [], single_lines=False)
    assert plain.text == text and (plain.lines_joined, plain.marks_dropped) == (0, 0)


def test_the_library_says_how_much_it_joined(tmp_path):
    text = lines("選課流程圖", ".", ".", ".", ".", ".", "加", "退", "選", "受理停修申請。")
    aca = tc.site_input(tmp_path / "aca", ACA_SITE, "教务处", [
        {"kind": PDF, "url": ACA + "f/1/流程圖.pdf", "title": "流程圖.pdf", "text": text},
        {"kind": PDF, "url": ACA + "f/2/辦法.pdf", "title": "辦法.pdf", "text": "第一條 依本校學則訂定。"}])
    paths = tm.tree_files(tmp_path / "tree")
    report = tm.run(paths, tmp_path / "db" / "on.db", inputs=[aca])
    assert report.single_lines == {"on": True, "documents": 1, "joined": 2, "dropped": 5}
    with sqlite3.connect(tmp_path / "db" / "on.db") as conn:
        got = dict(conn.execute("SELECT title, text FROM docs"))
        said = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'single_lines_json'").fetchone()[0])
    assert got["流程圖.pdf"] == lines("選課流程圖", "加退選", "受理停修申請。") and said == report.single_lines
    off = tm.run(paths, tmp_path / "db" / "off.db", inputs=[aca], single_lines=False)
    assert off.single_lines == {"on": False, "documents": 0, "joined": 0, "dropped": 0}
    with sqlite3.connect(tmp_path / "db" / "off.db") as conn:
        assert dict(conn.execute("SELECT title, text FROM docs"))["流程圖.pdf"] == text
