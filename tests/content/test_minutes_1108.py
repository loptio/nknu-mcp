"""Owner 11-08 10:16 (addendum M9): a file of meeting minutes is background. 會議紀錄 / 會議記錄 / 議事錄 in a file's
title or its page's title makes it minutes (build.doc_type), unless that title, or the page's, names a rule or a blank
form; a page or a notice is never minutes (the owner named uploaded files). No model loads; the names are made up."""

from __future__ import annotations

import sqlite3

import pytest

from nknu.content import build
from nknu.content.schema import BACKGROUND, MINUTES, PRIMARY
from tests.content import test_build as tb

SSO = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=595&menuid=507"

MINUTES_TYPES = [  # file or page title, the page's title, kind_class, doc_type
    ("113-2燕巢宿委會會議紀錄.pdf", "宿舍服務", "attachment", MINUTES),
    ("114學年度第一學期燕巢校區幹部座談會會議紀錄.docx", "幹部座談會", "attachment", MINUTES),
    ("會議紀錄 113學年第2學期第2次", "校課程委員會議記錄 - 校課程委員會議紀錄", "attachment", MINUTES),
    ("第3次系務會議議事錄.pdf", "", "attachment", MINUTES),
    ("11311教務會議附件.pdf", "會議紀錄", "attachment", MINUTES),  # the page says so
    ("會議紀錄總檔.pdf", "會議紀錄", "attachment", MINUTES),
    # a rule or a blank form, by the file's title
    ("學生宿舍委員會會議紀錄格式.docx", "宿舍服務", "attachment", ""),
    ("會議紀錄範本.doc", "表單下載", "attachment", ""),
    ("會議紀錄表.docx", "", "attachment", ""),
    ("宿舍委員會設置辦法.pdf", "會議紀錄", "attachment", ""),
    # a page that holds rules and minutes: only the file's own title can say it is minutes
    ("宿舍收費標準.pdf", "相關法規及會議紀錄", "attachment", ""),
    ("113學年度宿舍床位分配結果.pdf", "相關法規及會議紀錄", "attachment", ""),
    ("113-1宿委會會議紀錄.pdf", "相關法規及會議紀錄", "attachment", MINUTES),
    # not a file: the page that lists them, a notice
    ("會議紀錄", "", "page", ""),
    ("113學年度第1次宿委會會議紀錄", "", "notice", ""),
    # review of the change (11-08): a rule before 會議紀錄, or in a note, is what the meeting was about; one after it,
    # or a form anywhere, makes a rule or a form; a title that says it only in a note goes by the rest of the title
    ("法規委員會會議紀錄.pdf", "", "attachment", MINUTES),
    ("研議「學生請假規則」修正案會議紀錄.pdf", "", "attachment", MINUTES),
    ("會議紀錄（修正學生請假辦法）.pdf", "", "attachment", MINUTES),
    ("會議紀錄及簽到表.pdf", "", "attachment", MINUTES),
    ("會議紀錄撰寫要點.pdf", "", "attachment", ""),
    ("空白會議紀錄.doc", "", "attachment", ""),
    ("學生請假規則（依113學年度第1次會議紀錄修正）.pdf", "", "attachment", ""),
    ("第3次會議（會議紀錄）.pdf", "", "attachment", MINUTES),
    # a file that does not say so (the owner: 「明确写着」)
    ("111學年度第1學期燕巢校區社團暨班級幹部建議事項執行情形.pdf", "幹部座談會", "attachment", ""),
    # an exam stays an exam
    ("歷屆考題.pdf", "會議紀錄", "attachment", "exam"),
]


@pytest.mark.parametrize(("title", "parent", "kind_class", "want"), MINUTES_TYPES)
def test_minutes_by_title_or_page(title, parent, kind_class, want):
    assert build.doc_type(title, parent, SSO, kind_class) == want


def test_minutes_are_background_in_the_built_db(tmp_path):
    pages = [("p90", "page", f"{tb.ACA}page.aspx?pn=90&gi=2", "會議紀錄", "會議紀錄\n113學年度")]
    attachments = [
        ("a90", "附件:pdf:有文字层", tb.attachment_url("a90", "11311教務會議記錄.pdf"), "11311教務會議記錄.pdf",
         "教務會議 停車 討論事項", ["p90"]),
        ("a91", "附件:pdf:有文字层", tb.attachment_url("a91", "會議紀錄範本.doc"), "會議紀錄範本.doc",
         "會議紀錄範本 主席", ["p90"]),
        ("a92", "附件:pdf:有文字层", tb.attachment_url("a92", "宿委會會議紀錄.pdf"), "宿委會會議紀錄.pdf",
         "宿委會 停車", ["p15"]),
    ]
    paths = tb.make_inputs(tmp_path, pages=pages, attachments=attachments)
    report = tb.run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        got = {r[0]: r[1:] for r in conn.execute(
            "SELECT doc_id, doc_type, tier FROM docs WHERE doc_id IN ('da90', 'da91', 'da92', 'dp90')")}
    finally:
        conn.close()
    assert got == {"da90": (MINUTES, BACKGROUND), "da91": ("", PRIMARY), "da92": (MINUTES, BACKGROUND),
                   "dp90": ("", PRIMARY)}
    assert report.counts["doc_types"]["minutes"] == 2
