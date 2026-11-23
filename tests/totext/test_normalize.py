"""Text normalization, applied by nknu.content.assemble to every stored text (plan 2026-11-02 T5): compatibility
characters folded, letter-spaced CJK joined (outside PDF text also an isolated run of 2 or 3: addendum H1), and in PDF
text the space a line wrap left between two CJK characters dropped."""

from __future__ import annotations

from nknu.totext.normalize import (
    COMPAT_RANGES,
    MISMAPPED,
    fix_mismapped,
    fold_compat,
    join_spaced,
    join_wraps,
    normalize,
)


def test_fold_compat():
    assert fold_compat(chr(0xF900) + chr(0x2F00) + chr(0xFA0E)) == (chr(0x8C48) + chr(0x4E00) + chr(0xFA0E), 2)
    assert fold_compat("（一）：１，") == ("（一）：１，", 0)  # full-width punctuation untouched


def test_fold_compat_ranges():
    assert COMPAT_RANGES == ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))
    # a supplement compatibility ideograph and a radical supplement with an NFKC form fold; one without stays
    assert fold_compat(chr(0x2F800) + chr(0x2E9F) + chr(0x2E80)) == (chr(0x4E3D) + "母" + chr(0x2E80), 2)
    assert fold_compat("選課") == ("選課", 0)


def test_fix_mismapped():
    # review of phase 1: PDF text layers set in 標楷體 give 詴 for 試 and 帄 for 平 (measured on the 10-27 export:
    # 考詴, 面詴, 和帄校區, 公帄); 郍 is left alone: it shows only as 郍吒, in a font that writes 那 right beside it
    assert MISMAPPED == {"詴": "試", "帄": "平"}
    assert fix_mismapped("考詴時間在和帄校區") == ("考試時間在和平校區", 2)
    assert fix_mismapped("郍吒") == ("郍吒", 0) and fix_mismapped("") == ("", 0)


def test_join_spaced():
    # changed on purpose (addendum H1): an isolated run of 2 or 3 single CJK characters joins too, so 「姓 名」 and
    # 「申 請 人」 join now (they stayed before), and so do 「生 簡 章」 after two spaces (a run of 3 of its own) and
    # 「一 二」 after 、 (punctuation is no CJK character)
    assert join_spaced("招 生 簡 章\n姓 名") == ("招生簡章\n姓名", 2)
    assert join_spaced("申 請 人 與 招 生 簡 章") == ("申請人與招生簡章", 1)  # one run, however long
    assert join_spaced("申 請 人") == ("申請人", 1)
    assert join_spaced("招  生 簡 章") == ("招  生簡章", 1)  # two spaces end a run
    assert join_spaced("A B C D、一 二") == ("A B C D、一二", 1)  # Latin letters never join


def test_join_spaced_short_runs():
    # H1: labels on the staff and notice pages (「摘 要」「分 機」「備 註」 in 13% of the page texts) were never found
    # by a search for 分機; U+3000 between two CJK characters counts as the one space
    assert join_spaced("摘 要：本校115學年度") == ("摘要：本校115學年度", 1)
    assert join_spaced("分　機：1131") == ("分機：1131", 1)
    assert join_spaced("| 姓 名 | 備 註 |") == ("| 姓名 | 備註 |", 2)
    assert join_spaced("聯 絡 人：分機1131") == ("聯絡人：分機1131", 1)
    assert join_spaced("地 址 高雄市") == ("地址 高雄市", 1)  # the run ends where a longer word starts
    assert join_spaced("聯 絡 人 分機") == ("聯絡人分機", 1)  # 4 CJK characters one space apart: the kept rule
    assert join_spaced("招　生　簡　章") == ("招生簡章", 1)  # 4 or more with U+3000 too, never split


def test_join_spaced_leaves_what_is_no_isolated_run():
    # a Latin or digit neighbour, a run beside another CJK character, two spaces, a lone character
    for text in ("A 班", "第 1 次", "第1 次", "的姓 名", "姓 名字", "申請 人", "姓  名", "分　　機", "分 　機",
                 "姓 A 名", "一", "A　班", "甲 乙丙 丁"):
        assert join_spaced(text) == (text, 0), text


def test_join_spaced_in_pdf_text_keeps_the_old_rule():
    # PDF text keeps join_spaced's 4-or-more rule with ASCII spaces; join_wraps then joins its short runs as wraps
    assert join_spaced("姓 名\n申 請 人", pdf=True) == ("姓 名\n申 請 人", 0)
    assert join_spaced("招 生 簡 章\n分　機", pdf=True) == ("招生簡章\n分　機", 1)
    assert join_spaced("招　生　簡　章", pdf=True) == ("招　生　簡　章", 0)


def test_join_wraps():
    assert join_wraps("應於 加退選截止日前，並以一 次為限") == ("應於加退選截止日前，並以一次為限", 2)
    assert join_wraps("TOEIC 550 分") == ("TOEIC 550 分", 0) and join_wraps("教務處 TOEIC") == ("教務處 TOEIC", 0)
    # CJK punctuation (U+3001-303F) and full-width forms (U+FF01-FF60) count as CJK-side characters
    assert join_wraps("一、 選課（一） 期間 １ 月") == ("一、選課（一）期間１月", 4)
    assert join_wraps("選課  期間\n選課 期間") == ("選課  期間\n選課期間", 1)  # two spaces stay
    assert join_wraps("| 姓 名 | 備 註 |") == ("| 姓名 | 備註 |", 2)


def test_normalize_order_and_counts():
    # the counts gained "mismapped" (the phase-1 review's fold, PDF text only): the exact dicts below changed with it
    none = {"compat": 0, "mismapped": 0, "spaced": 0, "wraps": 0}
    assert normalize(chr(0x2F00) + " 二 三 四", pdf=False) == ("一二三四", none | {"compat": 1, "spaced": 1})
    assert normalize("並以一 次", pdf=True)[0] == "並以一次" and normalize("並以一 次", pdf=False)[0] == "並以一 次"
    assert normalize("", pdf=True) == ("", none)
    assert normalize("考 詴 時 間", pdf=True) == ("考試時間", none | {"mismapped": 1, "spaced": 1})
    assert normalize("考詴", pdf=False) == ("考詴", none)  # only a PDF's text layer maps these glyphs wrong


def test_normalize_joins_short_runs_outside_pdf_text():
    # H1: page, office, office_old and image text (pdf=False) join isolated runs of 2 or 3, counted as spaced; PDF
    # text keeps its steps, where join_wraps joins the same label (counted as a wrap) and leaves U+3000 as before
    none = {"compat": 0, "mismapped": 0, "spaced": 0, "wraps": 0}
    assert normalize("分 機 1131\n摘　要", pdf=False) == ("分機 1131\n摘要", none | {"spaced": 2})
    assert normalize("分 機 1131\n摘　要", pdf=True) == ("分機 1131\n摘　要", none | {"wraps": 1})
    assert normalize("並以一 次", pdf=False) == ("並以一 次", none)  # beside another CJK character: no isolated run


def test_normalize_leaves_image_markers():
    # assemble puts an image's OCR text in place of its [[img:src]] line by the src: the src must stay as written
    marker = "[[img:uploadfile/招 生 簡 章" + chr(0xF900) + ".jpg]]"
    text, counts = normalize(f"{marker}\n招 生 簡 章\n一 次", pdf=True)
    assert text == f"{marker}\n招生簡章\n一次" and counts == {"compat": 0, "mismapped": 0, "spaced": 1, "wraps": 1}
    assert normalize(marker, pdf=True) == (marker, {"compat": 0, "mismapped": 0, "spaced": 0, "wraps": 0})
