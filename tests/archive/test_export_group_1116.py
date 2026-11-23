"""The export batch, T3 (plan docs/superpowers/plans/2026-11-16-export-batch.md; the content session 11-16): a 31st
column 组别, the words of c.nknu's notice detail page's group field (#lbl_GroupName) as the page has them, '' for every
other page (newsview has no such field); never folded into 公告单位 (the content stage rewrites a file's unit by that
one). Made-up pages only."""

from __future__ import annotations

from nknu.archive import export
from tests.archive.test_export_1027 import BASE
from tests.archive.test_export_step3_1108 import _with

DETAIL = BASE + "newsdetail.aspx?nid=11"
PAGE = """<html><head><title>國立高雄師範大學教務處</title></head><body>
<h5>發佈人員：OOO｜公告日期：<span id="lbl_AddTime">2026/11/10 上午 10:00:00</span>｜
發佈單位：<span id="lbl_GroupName"> 招生 企劃組 </span></h5>
<span id="lbl_Title">轉學考放榜</span><span id="lbl_Contents">榜單已公告於網站。</span></body></html>"""


def test_the_group_column_is_the_last_and_the_others_stay_where_they_were():
    assert export.COLUMNS[-1] == "组别" and export.COLUMNS[-2] == "公告单位" and len(export.COLUMNS) == 31


def test_a_notice_detail_page_states_its_group_as_the_page_has_it(tmp_path):
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: arch.page("d11", DETAIL, PAGE))
    assert rows[DETAIL]["组别"] == "招生 企劃組"
    assert rows[DETAIL]["公告单位"] == ""


def test_other_pages_state_no_group(tmp_path):
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: None)
    assert {r["组别"] for r in rows.values()} == {""}
