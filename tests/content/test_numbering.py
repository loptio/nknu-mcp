"""Task T6 (plan 2026-11-02): numbering marks, adapted from RAGFlow's bullet groups for Traditional Chinese."""

from nknu.content.numbering import (
    CONTENTS_LINE,
    HEADING_CHARS,
    MD_LEVEL,
    QA_LEVEL,
    Mark,
    marks,
    pick_group,
    strip_decor,
)


def test_pick_group():
    assert pick_group(["第一條 總則", "第二條 範圍", "一、申請", "二、資格"]) == 0
    assert pick_group(["壹、目的", "一、說明", "（一）網路報名"]) == 2
    assert pick_group(["1. 說明", "1.1 範圍", "1.2 對象"]) == 1 and pick_group(["沒有編號"]) is None
    assert pick_group([]) is None


def test_traditional_variants():
    assert [m.heading for m in marks("第二編 總則\n第三節 細則\n第五條之一 補充\n本文")] == [
        "第二編 總則", "第三節 細則", "第五條之一 補充"]


def test_qa_markers():
    ms = marks("問題：可以停修幾科？\n答：一科。\nQ3：何時公告？\n問題不是標題")
    assert [(m.level, m.heading) for m in ms] == [(QA_LEVEL, "問題：可以停修幾科？"), (QA_LEVEL, "Q3：何時公告？")]


def test_markdown_and_decoration():
    ms = marks("## 一、報名方式\n**（一）網路報名**\n- 二、 選課注意事項\n## 附表")
    assert [m.heading for m in ms] == ["一、報名方式", "（一）網路報名", "二、 選課注意事項", "附表"]
    assert ms[-1].level == MD_LEVEL + 2 and ms[0].level < QA_LEVEL


def test_not_bullet_and_contents():
    assert marks("0 元\n3 個工作天\n1.. 見附件\n1.2.3的規定\n壹、報考資格 ........ 1") == []


def test_lines_without_a_letter_are_never_marks():
    # review 11-02: table cells and form fields (a decimal, a date, a phone number) were marks that cut sections
    # headed 「8.5」, and they voted: a column of decimals made the numeric group beat 壹、 and 一、
    assert marks("8.5\n39.72±1.23\n20.00\n97.03.24\n(07)7172930\n１２.５\n本文") == []
    text = "壹、目的\n一、說明\n8.5\n20.00\n39.72\n11.5\n貳、方式\n二、步驟"
    assert [m.heading for m in marks(text)] == ["壹、目的", "一、說明", "貳、方式", "二、步驟"]


def test_a_list_number_split_from_its_text_stays_a_mark():
    # a layout table or a PDF text layer puts 「1.」 on one line and its text on the next: numbering all the same; a
    # column of row numbers (3. 4. （5）) is not, since no text follows its numbers
    text = "說明\n1.\n請填寫報名表\n\n(2)\n繳交報名費\n3.\n4.\n（５）\n"
    assert [m.heading for m in marks(text)] == ["1.", "(2)"]


def test_strip_decor():
    assert strip_decor("  ## 一、報名方式 ") == ("一、報名方式", 2)
    assert strip_decor("- 二、 選課注意事項") == ("二、 選課注意事項", 0)
    assert strip_decor("* **（一）網路報名**") == ("（一）網路報名", 0)
    assert strip_decor("__附表__") == ("附表", 0) and strip_decor("**一、報名**：見下") == ("一、報名：見下", 0)
    assert strip_decor("##") == ("", 2) and strip_decor("") == ("", 0)
    assert strip_decor("#1 不是 Markdown 標題") == ("#1 不是 Markdown 標題", 0)  # no space after #: plain text
    assert strip_decor("####### 七個#") == ("####### 七個#", 0)  # Markdown headings stop at six


def test_levels_follow_the_group():
    # zh_law: 編 0, 章 1, 節 2, 條 3, 款 一、 4, 目 （一） 5; full-width digits read like ASCII ones
    text = "第一編 總則\n第一章 通則\n第１節 範圍\n第 3 條 定義\n一、學生\n（一）大學部\n本文"
    assert [m.level for m in marks(text)] == [0, 1, 2, 3, 4, 5]
    # zh_num: 壹、 0, 一、 3, （一） 4, 1. 5, (1) 6
    text = "壹、目的\n一、說明\n（一）網路報名\n1. 登入\n(1) 帳號\n貳、方式\n二、步驟\n（二）現場報名"
    assert [m.level for m in marks(text)] == [0, 3, 4, 5, 6, 0, 3, 4]
    # numeric: 1. 2, 1.1 3, 1.1.1 4, 1.1.1.1 5; a dated line 1.5% is no heading
    text = "1. 說明\n1.1 範圍\n1.1.1 對象\n1.1.1.1 例外\n2. 方式\n1.5% 的學生"
    assert [m.level for m in marks(text)] == [2, 3, 4, 5, 2]


def test_only_the_picked_groups_patterns_count():
    # 壹、 makes the zh_num group win, and only its patterns make headings: 第X條 has none there (RAGFlow's rule)
    text = "壹、目的\n貳、範圍\n一、說明\n第三條 依本校學則"
    assert [m.heading for m in marks(text)] == ["壹、目的", "貳、範圍", "一、說明"]


def test_marks_are_line_starts_and_cut_headings():
    text = "前言\n\n  第一條 " + "字" * 60 + "\n本文"
    (m,) = marks(text)
    assert m == Mark(text.index("  第一條"), 3, ("第一條 " + "字" * 60)[:HEADING_CHARS])


def test_contents_line():
    assert CONTENTS_LINE.search("壹、報考資格 ........................ 1")
    assert CONTENTS_LINE.search("肆、面試 ··········· 7") and CONTENTS_LINE.search("第一章 總則．．．．．．12")
    assert not CONTENTS_LINE.search("一、自傳(含申請動機、志趣...)。")
