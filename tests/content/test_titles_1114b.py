"""Plan 2026-11-14 T4, after the first rebuild: a size is not a title.

The rebuilt 111-site database had 9 attachments whose new title held a file size (「… 146 KB」, 「pdf 23kb」): the
first line of their text is a download list's link text, which says how big a file is and not what the document is.
documents.title_flaw refuses a candidate that holds a file size, a number followed by a unit of bytes (kb, mb, gb, KB,
MB, bytes, 位元組 …) 【Claude】, with the reason 'size'; such a document keeps the title it has, and the build's
report counts it. The sentences are made up; no model loads."""

from __future__ import annotations

import pytest

from nknu.content.documents import title_flaw
from tests.content import test_own_titles_1114 as to

OLD = "舊標題"
NAMES = (to.SCHOOL, to.SOFT)


@pytest.mark.parametrize("candidate", [
    "學生轉系辦法 pdf 146 KB", "pdf 23kb", "001 特殊需求學生學習協助 pdf 146 KB", "獎學金申請表 (1.2 MB)",
    "活動簡章 ＰＤＦ ５１２ＫＢ",  # full-width letters and digits
    "課程地圖 odt 45 kB", "報名表.docx 1,024 Kb", "招生簡章 2GB", "成果影片 1.5 tb", "會議資料 3 MiB",
    "下載 3072 bytes", "附件大小 1 byte", "表單 20 KBytes", "申請書 512 位元組", "申請書 512 千位元組", "說明文件 88 字节",
    "選課須知㎅ 30㎅",  # the one-character units NFKC spells out
])
def test_a_line_that_holds_a_file_size_is_no_title(candidate):
    assert title_flaw(candidate, OLD, NAMES) == "size"


@pytest.mark.parametrize("candidate", [
    "學生轉系辦法", "KB 國民銀行獎學金公告",  # no number before the unit
    "3MBA 招生說明會", "校園網路 100 kbps 頻寬說明", "2 bytecode 課程講義",  # a longer word that only starts like a unit
    "5G 行動通訊講座", "113B 教室借用辦法", "TB 防治宣導", "115 學年度 mb 班課表",  # a bare letter, no number before the unit
    "第 3 位元 加法器實驗", "檔案大小限制說明",
])
def test_a_line_without_a_size_is_still_a_title(candidate):
    assert title_flaw(candidate, OLD, NAMES) == ""


def test_the_other_reasons_keep_their_order():
    # what was refused before is refused for the reason of before; a size is asked after those and before the shape
    assert title_flaw("", OLD, NAMES) == "blank"
    assert title_flaw("23kb", OLD, NAMES) == "size" and title_flaw("3 kb", OLD, NAMES) == "size"
    assert title_flaw("9 mb", OLD, NAMES) == "size" and title_flaw("1", OLD, NAMES) == "short"
    assert title_flaw("長" * 78 + " 3 kb", OLD, NAMES) == "long"  # 83 characters
    assert title_flaw("附件一", OLD, NAMES) == "nothing" and title_flaw("附件 20 KBytes", OLD, NAMES) == "nothing"
    assert title_flaw("| 申請表 | pdf | 23kb |", OLD, NAMES) == "size"  # a table's row that holds one: the size
    assert title_flaw("| 申請表 | pdf |", OLD, NAMES) == "no_title"
    assert title_flaw("檔案大小：146 KB", OLD, NAMES) == "size" and title_flaw("檔案大小：", OLD, NAMES) == "no_title"
    assert title_flaw("申請表 146 KB", "申請表 146 KB", NAMES) == "size"  # before 'same'


def test_a_file_whose_first_line_holds_a_size_keeps_its_file_name():
    files = [to.file("a1", "file1_718131228.pdf", "學生轉系辦法 pdf 146 KB\n第一條 本辦法依學則訂定。"),
             to.file("a2", "0627.odt", "## 請假規定 odt 23kb\n一、依據"),  # a heading with one
             to.file("a3", "643-79-8.odt", "## 學生請假辦法\n檔案大小 146 KB")]  # further down: the title says none
    got, report = to.retitled(files)
    assert got["da1"] == ("file1_718131228.pdf", "") and got["da2"] == ("0627.odt", "")
    assert got["da3"] == ("學生請假辦法", "")
    assert to.counts(report["file"]) == {"documents": 3, "retitled": 1, "kept": 2, "why": {"size": 2}}
    assert report["by_site"] == {to.ACA_SITE: {"file": {"retitled": 1, "kept": 2}}}


def test_pages_that_share_a_title_keep_it_when_their_first_line_holds_a_size():
    pages = [to.page(n, to.SOFT, f"表單下載第{n}項 pdf {20 + n} KB\n請點選下載。") for n in range(1, 4)]
    pages += [to.page(n, to.SOFT, f"課程地圖第{n}版\n修課順序。") for n in range(4, 21)]
    got, report = to.retitled(pages)
    assert [got[f"dp{n}"] for n in (1, 2, 3)] == [(to.SOFT, "")] * 3 and got["dp4"] == ("課程地圖第4版", "")
    assert report["shared"] == {"titles": 1, "documents": 20, "retitled": 17, "kept": 3, "why": {"size": 3}}
