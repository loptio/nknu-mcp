"""Blueprint item 22 (owner 11-09, 「照这个做」), the page reading's part: a drop-down <select> reads as the one option
it shows, and every page is also read whole with its link lines marked (PageText.full, links; convert stores full.md),
which assemble's under_read falls back on. The words are made up."""

from __future__ import annotations

import re

from nknu.totext.cache import FULL, Cache
from nknu.totext.convert import Tools, convert_file, store
from nknu.totext.pagetext import PAGE_VERSION, page_text
from nknu.totext.route import Route

ACA = "https://c.nknu.edu.tw/aca/"
PAGE = ACA + "page.aspx?pn=1"
STATIC = ACA + "uploadfile/userfiles/files/spaper/1.htm"


def _area(inner: str) -> str:
    return page_text(f'<html><body><div id="lbl_Contents">{inner}</div></body></html>'.encode(), PAGE).text


def test_a_drop_down_reads_as_the_option_it_shows():
    menu = '<select name="uYear"><option>115</option><option selected>113</option><option>112</option></select>'
    assert _area(f"<p>學年度：{menu}學期：<select><option>1</option><option>2</option></select></p>") == (
        "學年度： 113 學期： 1")
    # no selected option: the first one not disabled; the last selected one when two are; a label attribute wins
    assert _area('<select><option disabled>請選擇</option><option>甲班</option></select>') == "甲班"
    assert _area('<select><option selected>甲</option><option selected>乙</option></select>') == "乙"
    assert _area('<select><option label="一年級" selected>1</option></select>') == "一年級"
    assert _area('<p>學期<select></select></p>') == "學期"  # no option: nothing


def test_a_drop_down_holding_more_than_options_reads_as_before():
    # lexbor nests what follows an unclosed select inside it (11-09 review): one option of it would drop the notice
    unclosed = ('<div>學年度<select name="y"><option selected>113<option>112</div>'
                '<div><h2>獎學金申請公告</h2><p>申請期間自十一月一日起</p></div>')
    assert _area(unclosed) == "學年度\n113\n112\n獎學金申請公告\n申請期間自十一月一日起"
    assert _area('<p>學年<select><option>1</option><hr><option selected>2</option></select></p>') == "學年 2"


def test_a_disabled_optgroup_and_the_size_attribute_by_htmls_rules():
    disabled = '<p>學年<select><optgroup label="舊" disabled><option>100</option></optgroup><option>113</option></select></p>'
    assert _area(disabled) == "學年 113"
    assert _area('<select size="²"><option>甲</option><option>乙</option></select>') == "甲"  # no digits: a drop-down
    assert _area('<select size="3px"><option>甲</option><option>乙</option></select>') == "甲\n乙"  # 3: a list box
    assert _area('<select size=" +1"><option>甲</option><option>乙</option></select>') == "甲"


def test_a_list_box_still_shows_every_option():
    assert _area('<select multiple><option selected>甲</option><option>乙</option></select>') == "甲\n乙"
    assert _area('<select size="3"><option>甲</option><option>乙</option></select>') == "甲\n乙"


def test_a_drop_down_in_a_data_table_cell():
    table = ("<table><tr><th>學年</th><th>課名</th></tr><tr><td><select><option>114</option>"
             "<option selected>113</option></select></td><td>國文</td></tr></table>")
    assert _area(table) == "| 學年 | 課名 |\n| --- | --- |\n| 113 | 國文 |"


def test_every_page_is_also_read_whole_with_its_link_lines():
    html = ('<html><body><nav><ul><li><a href="/a">最新消息</a></li><li><a href="/b">系所簡介</a></li></ul></nav>'
            '<div id="lbl_Contents"><p>停修申請</p></div>'
            '<div class="side"><p><a href="/c">相關<b>連結</b></a></p><p>電話：<a href="tel:1">07-0000000</a></p></div>'
            '<footer>地址：高雄市</footer></body></html>').encode()
    got = page_text(html, PAGE)
    assert got.text == "停修申請"  # the content area, as before
    assert got.full.split("\n") == ["最新消息", "系所簡介", "停修申請", "相關連結", "電話：07-0000000", "地址：高雄市"]
    assert got.links == (0, 1, 3)  # a line of a link's words alone; 「電話：07-…」 holds more than the link


def test_a_page_without_a_body_has_no_whole_reading():
    got = page_text(b"<html><frameset><frame src='a.htm'></frameset></html>", PAGE)
    assert (got.text, got.full, got.links) == ("", "", ())


def test_the_whole_reading_is_stored_beside_the_text(tmp_path):
    path = tmp_path / "p.html"
    path.write_bytes('<html><body><a href="/">首頁</a><div id="lbl_Contents">停修申請</div></body></html>'.encode())
    route = Route("page", "html", url=PAGE, page_kind=f"page|cnknu:body|{PAGE_VERSION}")
    conv = convert_file(path, route, Tools(), tmp_path / "w")
    cache = Cache(tmp_path / "cache")
    sha = "a" * 64
    result = store(cache, sha, conv, route, seconds=0.1, tools={})
    assert cache.text(sha) == "停修申請"
    assert cache.extra(sha, FULL) == "首頁\n停修申請"
    assert result["page"]["links"] == [0] and "full" not in result
    assert cache.extra(sha, "missing.md") == ""


def test_the_page_rule_is_pages_4():
    assert re.fullmatch(r"pages-4:[0-9a-f]{10}", PAGE_VERSION)
