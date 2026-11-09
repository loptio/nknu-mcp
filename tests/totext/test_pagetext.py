"""page_text and page_kind: an exported HTML page as lines of text, with tables as rows and images as markers in
place (plan 2026-11-02 T3a). The words are made up. Since plan 2026-11-08 T2 page_text takes the page's URL, not its
kind: the pages here are c.nknu's, read by its content area; the other platforms are in test_pages_1108."""

from __future__ import annotations

import pytest

from nknu.totext.pagetext import IMG_LINE, IMG_MARK, PageDecodeError, PageText, page_kind, page_text

ACA = "https://c.nknu.edu.tw/aca/"
NOTICE = ACA + "newsdetail.aspx?nid=1&gi=2"
NEWSVIEW = ACA + "newsview.aspx?nid=1"
ALBUM = ACA + "album_images.aspx?aid=1"
PAGE = ACA + "page.aspx?pn=1"
STATIC = ACA + "uploadfile/userfiles/files/spaper/1.htm"


def _text(inner: str, url: str = PAGE) -> str:
    return page_text(f'<html><body><div id="lbl_Contents">{inner}</div></body></html>'.encode(), url).text


def test_page_kind():
    assert page_kind("https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=1&gi=2") == "newsdetail"
    assert page_kind("https://c.nknu.edu.tw/aca/newslist.aspx#第2页") == "newslist#翻页/按钮"
    assert page_kind("https://c.nknu.edu.tw/aca/page.aspx?sn=3") == "page(sn)"
    assert page_kind("https://c.nknu.edu.tw/aca/uploadfile/userfiles/files/spaper/1.htm") == "静态:spaper"
    assert page_kind("https://forms.gle/x") == "外站:forms.gle"


def test_page_kind_other_forms():
    assert page_kind("https://c.nknu.edu.tw/aca/UploadFile/userfiles/files/nknupaper/50/a.htm") == "静态:nknupaper"
    assert page_kind("https://c.nknu.edu.tw/aca/uploadfile/userfiles/files/sinfo/x.html") == "静态:sinfo"
    assert page_kind("https://c.nknu.edu.tw/aca/Page.aspx?pn=1") == "page"
    assert page_kind("https://c.nknu.edu.tw/aca/page.aspx?sn=3#x") == "page(sn)#翻页/按钮"
    assert page_kind("https://c.nknu.edu.tw/aca/album_images.aspx?aid=1") == "album_images"
    assert page_kind("https://c.nknu.edu.tw/aca/") == "其他:"
    assert page_kind("https://c.nknu.edu.tw/aca/robots.txt") == "其他:robots.txt"


def test_style_and_script_inside_content_are_dropped():
    html = '<div id="lbl_Contents"><style>.x{color:red}</style><p>停修申請</p><script>var a=1</script></div>'
    assert page_text(html.encode(), NOTICE).text == "停修申請"


def test_noscript_and_template_are_dropped():
    assert _text("<noscript>請開啟 JavaScript</noscript><p>選課</p><template><p>樣板</p></template>") == "選課"


def test_dropped_elements_never_reach_the_text_however_nested():
    # lexbor parses with scripting off: a noscript holds elements, even another noscript
    html = ('<div id="lbl_Contents"><noscript><p>甲</p><noscript><b>乙</b></noscript></noscript><p>丙</p>'
            "<table><tr><th>欄</th></tr><tr><td>值<script>var x</script><style>.y{}</style></td></tr></table></div>"
            '<noscript><div id="lbl_PageSub">不該出現</div></noscript>')
    assert page_text(html.encode(), PAGE).text.splitlines() == ["丙", "| 欄 |", "| --- |", "| 值 |"]
    title = page_text('<span id="lbl_Title"><img src="t.gif">標題<script>x</script></span>'.encode(), PAGE)
    assert title.title == "標題" and title.text == "[[img:t.gif]]\n標題"


def test_tables_keep_rows():
    html = ('<div id="lbl_Contents"><table><tr><th>項目</th><th>金額</th></tr>'
            '<tr><td>學費</td><td>1,000</td></tr></table></div>')
    assert page_text(html.encode(), PAGE).text.splitlines() == ["| 項目 | 金額 |", "| --- | --- |", "| 學費 | 1,000 |"]


def test_table_without_header_cells_has_no_separator():
    html = ("<p>說明</p><table><tr><td>日期</td><td>地點</td></tr><tr><td>11月1日</td><td>行政大樓<br>二樓</td></tr>"
            "</table><p>以上</p>")
    assert _text(html).splitlines() == ["說明", "| 日期 | 地點 |", "| 11月1日 | 行政大樓 二樓 |", "以上"]


def test_cells_are_one_line_and_pipes_are_escaped():
    html = ("<table><tr><th>名稱</th><th>備註</th></tr><tr><td><p>甲</p><p>乙</p></td><td>A|B</td></tr>"
            "<tr><td></td><td></td></tr></table>")
    assert _text(html).splitlines() == ["| 名稱 | 備註 |", "| --- | --- |", "| 甲 乙 | A\\|B |"]


def test_images_become_markers_in_place():
    t = page_text('<div id="lbl_Contents"><p>前</p><img src="uploadfile/a.jpg"><p>後</p></div>'.encode(), PAGE)
    assert t.text == "前\n[[img:uploadfile/a.jpg]]\n後" and t.images == ("uploadfile/a.jpg",)
    assert IMG_LINE.findall(t.text) == ["uploadfile/a.jpg"] and IMG_MARK.format(src="x") == "[[img:x]]"


def test_images_in_a_table_follow_it():
    html = ('<table><tr><th>照片</th><th>說明</th></tr><tr><td><img src="a.jpg"></td><td>開幕<img src="b.png"></td>'
            "</tr></table><p>完</p>")
    t = page_text(f'<div id="lbl_Contents">{html}</div>'.encode(), PAGE)
    assert t.text.splitlines() == ["| 照片 | 說明 |", "| --- | --- |", "|  | 開幕 |", "[[img:a.jpg]]", "[[img:b.png]]",
                                   "完"]
    assert t.images == ("a.jpg", "b.png")


def test_image_without_src_is_no_marker():
    t = page_text(b'<div id="lbl_Contents"><img alt="x"><img src="  "><img src="c\n.jpg"></div>', PAGE)
    assert t.text == "[[img:c.jpg]]" and t.images == ("c.jpg",)


def test_layout_table_keeps_its_lines():
    # one column (or one row) is layout, not data: each cell keeps its own lines, images stay in place
    html = ('<table><tr><td><p>一、報名方式</p><p>（一）網路報名</p></td></tr><tr><td>二、繳費<img src="p.jpg">'
            "</td></tr></table>")
    assert _text(html).splitlines() == ["一、報名方式", "（一）網路報名", "二、繳費", "[[img:p.jpg]]"]
    assert _text("<table><tr><td>左欄文字</td><td>右欄<br>第二行</td></tr></table>").splitlines() == [
        "左欄文字", "右欄", "第二行"]


def test_table_holding_a_table_is_layout():
    inner = "<table><tr><th>項目</th><th>金額</th></tr><tr><td>學費</td><td>1,000</td></tr></table>"
    html = f"<table><tr><td>一、收費</td><td>{inner}</td></tr><tr><td>二、退費</td><td>見附件</td></tr></table>"
    assert _text(html).splitlines() == ["一、收費", "| 項目 | 金額 |", "| --- | --- |", "| 學費 | 1,000 |", "二、退費",
                                        "見附件"]


def test_header_cells_make_a_data_table_whatever_its_shape():
    # th, thead, caption: a data table even with one column; a caption is a line of its own above the rows
    assert _text("<table><tr><th>系所</th></tr><tr><td>教育系</td></tr></table>").splitlines() == [
        "| 系所 |", "| --- |", "| 教育系 |"]
    html = "<table><caption>收費標準</caption><tr><td>學費</td><td>1,000</td></tr><tr><td>雜費</td><td>500</td></tr></table>"
    assert _text(html).splitlines() == ["收費標準", "| 學費 | 1,000 |", "| 雜費 | 500 |"]
    assert _text('<table role="presentation"><tr><th>甲</th><th>乙</th></tr></table>').splitlines() == ["甲", "乙"]


def test_static_page_uses_the_whole_body():
    html = "<html><body><h1>校訊</h1><p>第一則</p><div>第二則</div><img src='p1.jpg'></body></html>".encode()
    assert page_text(html, STATIC).text == "校訊\n第一則\n第二則\n[[img:p1.jpg]]"
    # no content area (plan 2026-11-08 T2 changed this on purpose: every page without one read nothing): a
    # detail page still reads nothing (a deleted notice), any other page its body without header, nav, footer
    # and aside
    assert page_text(html, NOTICE).text == ""
    assert page_text(html, PAGE).text == "校訊\n第一則\n第二則\n[[img:p1.jpg]]"


def test_only_the_content_area_is_read():
    html = ('<html><body><div id="menu"><a href="x">首頁</a></div><span id="lbl_Title">停修公告</span>'
            '<div id="lbl_Contents"><p>申請期間</p></div><div id="footer">版權所有</div></body></html>')
    assert page_text(html.encode(), NOTICE).text == "停修公告\n申請期間"


def test_nested_content_nodes_once():
    html = '<div id="DataFormView"><p>標題列</p><span id="lbl_Contents">停修申請</span></div>'
    assert page_text(html.encode(), NEWSVIEW).text == "標題列\n停修申請"


def test_title_fields():
    html = ('<span id="lbl_Title">  115學年度\n<b>停修</b>公告 </span><span id="lbl_AlbumName">活動<br>相簿</span>'
            '<div id="lbl_Contents">內文</div>')
    t = page_text(html.encode(), ALBUM)
    assert (t.title, t.album) == ("115學年度 停修公告", "活動 相簿")
    assert page_text(b"<p>x</p>", NOTICE) == PageText("", (), "", "", "x")  # full: the whole body (item 22)


def test_line_breaks_and_blank_lines():
    assert _text("甲<br>乙<br><br><br><br>丙<p>&nbsp;</p><p>丁</p>") == "甲\n乙\n\n丙\n丁"
    assert _text("<span>停修</span><span>申請</span>  <b>截止</b>\n日") == "停修申請 截止 日"
    assert _text("<br><br><p>開頭</p><br><br>") == "開頭"


def test_bom_is_dropped():
    assert page_text('﻿<div id="lbl_Contents">選課</div>'.encode(), PAGE).text == "選課"


def test_not_utf8_is_an_error():
    with pytest.raises(PageDecodeError):
        page_text(b"\xff\xfe\xfa", PAGE)
    with pytest.raises(PageDecodeError, match="UTF-8"):
        page_text('<div id="lbl_Contents">選課</div>'.encode("big5"), PAGE)
