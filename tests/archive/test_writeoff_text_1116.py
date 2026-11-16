"""The export batch, T5 ③ (judge.md §4.5 (ii), the two kinds only a page's words tell; the owner 11-16 「销账照表做」): a
c.nknu page whose whole answer is 「404: File not found」 (profiles.CNKNU_FNF) is the school's not-found page, flagged
gone_page (gone); a notice list that says 「搜尋結果：共0筆」 (profiles.NEWS_NO_ROWS) has no notice to hold, flagged
no_rows and never thin (got). The crawler flags a page as it reads it; the thin step never marks either thin.
Made-up pages only."""

from __future__ import annotations

import pytest

from nknu.archive import outcome
from tests.archive.test_archive import seed, snap
from tests.archive.test_pilot_fixes import make, work
from tests.archive.test_reconcile import Arch, _thin, _tpl
from tests.archive.test_thin_in_image_1027 import flags, layout_pages, stored

FNF_URL = "https://c.nknu.edu.tw/aca/oldpage.aspx"
FNF = "<html><head><title>404: File not found</title></head><body>404: File not found</body></html>"
EMPTY_LIST_URL = "https://news.nknu.edu.tw/nknu_news/lastnewslist_dept.aspx?place=WB&catid=JOB"
EMPTY_LIST = """<html><head><title>最新消息</title></head><body><form id="uForm"><div>搜尋結果：共0筆</div>
</form></body></html>"""


@pytest.fixture
def arch(tmp_path):
    a = Arch(tmp_path)
    yield a
    a.close()


def test_no_rows_is_a_known_flag_and_never_a_gap():
    assert "no_rows" in outcome.FLAGS and "no_rows" not in outcome.PAGE_FLAGS


@pytest.mark.parametrize("mark", ["no_rows", "gone_page"])
def test_the_thin_step_never_marks_a_page_the_school_says_holds_nothing(arch, mark):
    layout_pages(arch)
    empty = stored(arch, "/empty.html", _tpl("<p>共0筆</p>"))
    arch.f.x("UPDATE url SET flags=? WHERE id=?", (mark, empty))
    got = _thin(arch)
    assert empty not in got.thin and "thin" not in flags(arch, empty)


def test_the_crawler_flags_the_schools_file_not_found_page_and_it_is_gone(tmp_path):
    crawler, net, _clock = make(tmp_path, {("GET", "/aca/oldpage.aspx"): snap(FNF_URL, 200, html=FNF)})
    seed(crawler, FNF_URL)
    work(crawler, net, "ip:c.nknu.edu.tw")
    row = crawler.frontier.row(FNF_URL)
    assert "gone_page" in (row["flags"] or "").split(",")
    assert crawler.frontier.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == "gone"


def test_the_crawler_flags_a_list_that_says_it_has_no_notice(tmp_path):
    crawler, net, _clock = make(tmp_path, {("GET", "/nknu_news/lastnewslist_dept.aspx"):
                                           snap(EMPTY_LIST_URL, 200, html=EMPTY_LIST)})
    seed(crawler, EMPTY_LIST_URL)
    work(crawler, net, "ip:news.nknu.edu.tw")
    row = crawler.frontier.row(EMPTY_LIST_URL)
    assert "no_rows" in (row["flags"] or "").split(",")
