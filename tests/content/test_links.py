"""page_links and context_title: every link of an exported page with its zone, the title its context gives and its
row (addendum 2026-11-02 B, task T6b). The pages are made up; their layout follows the c.nknu template."""

from __future__ import annotations

import dataclasses

import pytest

from nknu.content.documents import _link_key
from nknu.content.links import GENERIC, ZONES, PageLink, match_key, page_links
from nknu.totext.pagetext import PageDecodeError

ACA = "https://c.nknu.edu.tw/aca/"
NOTICE = f"{ACA}newsdetail.aspx?nid=662&gi=2"
FORMS = f"{ACA}filesdownload.aspx?pn=111&pclass=A2004&gi=2"
STATIC = f"{ACA}uploadfile/userfiles/files/spaper/1.htm"


def page(body: str = "", files: str = "", menu: str = "", head: str = '<meta charset="big5">') -> bytes:
    """A c.nknu page as exported: a side menu outside the content area, the text in #lbl_Contents and the
    attachments in #lbl_FileLink (both content areas of profiles.CNKNU). It claims big5 and is UTF-8."""
    return (f'<html><head>{head}</head><body><form id="form1">'
            f'<div class="urbangreymenu"><ul id="navmenu-v">{menu}</ul></div>'
            f'<div id="lbl_Contents">{body}</div>'
            f'<div class="panel-body"><span id="lbl_FileLink">{files}</span></div>'
            "</form></body></html>").encode()


def contexts(html: bytes, url: str = FORMS) -> list[tuple[str, str]]:
    return [(link.anchor, link.context) for link in page_links(html, url)]


def test_interface():
    assert ZONES == ("content", "attachments", "nav")
    assert [f.name for f in dataclasses.fields(PageLink)] == ["page_url", "target", "anchor", "zone", "context", "row"]
    link = PageLink(NOTICE, "t", "a", "nav", "", "")
    with pytest.raises(dataclasses.FrozenInstanceError):
        link.zone = "content"  # type: ignore[misc]


def test_row_of_format_links_takes_the_rows_title():
    html = page('<table><tr><td>1</td><td>學生退學申請書</td><td><a href="f/a.pdf">pdf</a> <a href="f/a.doc">doc</a> '
                '<a href="f/a.odt">odt</a></td></tr></table>')
    links = page_links(html, FORMS)
    assert [(link.anchor, link.context) for link in links] == [
        ("pdf", "學生退學申請書"), ("doc", "學生退學申請書"), ("odt", "學生退學申請書")]
    assert {link.row for link in links} == {FORMS + "#/html[1]/body[1]/form[1]/div[2]/table[1]/tbody[1]/tr[1]"}
    assert {link.zone for link in links} == {"content"}
    assert [link.target for link in links] == [_link_key(f"{ACA}f/a.{x}") for x in ("pdf", "doc", "odt")]


def test_named_anchor_is_its_title_without_the_extension():
    html = page('<p><a href="f/b.docx">轉學考簡章.DOCX</a></p>',
                files='<a href="f/a.pdf">114學年度新生手冊.pdf&nbsp;(1234 Kb)</a><br>'
                      '<a href="f/c.pdf">選課說明＿甲班.docx.pdf&nbsp;(89.73 Kb)</a><br>')
    assert contexts(html, NOTICE) == [("轉學考簡章.DOCX", "轉學考簡章"),
                                      ("114學年度新生手冊.pdf (1234 Kb)", "114學年度新生手冊"),
                                      ("選課說明＿甲班.docx.pdf (89.73 Kb)", "選課說明＿甲班")]


def test_zones():
    html = page('<p>詳見<a href="UploadFile/a.pdf">實施要點</a></p>', files='<a href="UploadFile/b.pdf">說明.pdf</a>',
                menu='<li><a href="UploadFile/c.pdf">借用要點</a></li><li><a href="Default.aspx?GI=2">首頁</a></li>')
    zones = {link.anchor: link.zone for link in page_links(html, NOTICE)}
    assert zones == {"實施要點": "content", "說明.pdf": "attachments", "借用要點": "nav", "首頁": "nav"}


def test_other_detail_file_areas_of_the_profile_are_attachments():
    html = page(body='<div id="DataFormView"><span id="DataFormView_FileLinkLabel"><a href="f/a.pdf">a.pdf</a></span>'
                     "</div>")
    assert [link.zone for link in page_links(html, f"{ACA}newsview.aspx?nid=1")] == ["attachments"]


def test_static_page_is_read_whole():
    html = '<html><body><table><tr><td><p>本期<a href="2.htm">第二版</a></p></td></tr></table></body></html>'
    (link,) = page_links(html.encode(), STATIC)
    assert (link.zone, link.target) == ("content", _link_key(f"{ACA}uploadfile/userfiles/files/spaper/2.htm"))


def test_page_of_an_unprofiled_site_is_read_as_its_body():
    # plan 2026-11-08 T2 changed this on purpose: a page no profile describes (a Google form here) was all nav, read by
    # the c.nknu content area it never has; it is read as its body without header, nav, footer and aside now (the
    # crawler's own text measure), so the links of its text are content. A profiled detail page that does not match
    # its content area is all nav (test_links_1108).
    html = ('<html><body><nav><a href="https://forms.gle/y">其他表單</a></nav><p><a href="https://forms.gle/x">報名表單</a>'
            "</p></body></html>")
    assert [(link.anchor, link.zone) for link in page_links(html.encode(),
                                                             "https://docs.google.com/forms/d/e/1/viewform")] == [
        ("其他表單", "nav"), ("報名表單", "content")]


def test_line_before_the_link_in_a_paragraph():
    html = page('<p>附件一：停修申請表（<a href="f/s.pdf">pdf</a>）</p>')
    assert contexts(html) == [("pdf", "停修申請表")]


def test_line_before_the_link_in_a_list_item_and_a_definition():
    html = page('<ul><li>附件2:加選申請表(<a href="f/a.pdf">下載</a>)</li></ul>'
                '<dl><dt>表單</dt><dd>抵免申請表：<a href="f/b.pdf">點此下載</a></dd></dl>')
    assert contexts(html) == [("下載", "加選申請表"), ("點此下載", "抵免申請表")]


def test_only_the_line_of_the_link_counts():
    html = page('<p>一、說明文字甲<br>停修申請表 <a href="f/s.pdf">PDF</a><br>二、說明文字乙</p>')
    assert contexts(html) == [("PDF", "停修申請表")]


def test_generic_anchor_without_row_or_line_has_no_context():
    html = page(files='<a href="f/u112.pdf">u112.pdf&nbsp;(31 Kb)</a><br><a href="f/a.pdf">下載</a>')
    assert contexts(html, NOTICE) == [("u112.pdf (31 Kb)", ""), ("下載", "")]


def test_row_drops_a_leading_serial():
    html = page('<table><tr><th>序號</th><th>名稱</th><th>檔案</th></tr>'
                '<tr><td>(一)</td><td>停修申請表</td><td><a href="f/1.pdf">pdf</a></td></tr>'
                '<tr><td>二、</td><td>加選申請表</td><td><a href="f/2.pdf">pdf</a></td></tr>'
                '<tr><td>3.抵免申請表</td><td><a href="f/3.pdf">pdf</a></td></tr>'
                '<tr><td>114學年度招生簡章</td><td><a href="f/4.pdf">pdf</a></td></tr></table>')
    assert [c for _a, c in contexts(html)] == ["停修申請表", "加選申請表", "抵免申請表", "114學年度招生簡章"]


def test_row_leaves_out_every_link_text_of_the_row():
    html = page('<table><tr><td>2</td><td>休學申請書<br>(<a href="f/x.pdf">說明.pdf</a>)</td>'
                '<td><a href="f/a.pdf">PDF</a>／<a href="f/a.odt">ODT</a></td></tr></table>')
    assert contexts(html) == [("說明.pdf", "說明"), ("PDF", "休學申請書"), ("ODT", "休學申請書")]


def test_row_is_the_nearest_tr_li_or_p():
    html = page('<p>甲表 <a href="f/1.pdf">pdf</a> <a href="f/1.doc">doc</a></p>'
                '<ul><li>乙表 <a href="f/2.pdf">pdf</a></li><li>丙表 <a href="f/3.pdf">pdf</a></li></ul>',
                files='<a href="f/4.pdf">4.pdf</a>')
    rows = [link.row for link in page_links(html, NOTICE)]
    base = NOTICE + "#/html[1]/body[1]/form[1]/div[2]/"
    assert rows == [base + "p[1]", base + "p[1]", base + "ul[1]/li[1]", base + "ul[1]/li[2]", ""]


def test_target_is_the_documents_key_of_the_crawlers_address():
    html = page('<p><a href="UploadFile/News/20260101/停修說明.pdf">停修說明</a>'
                '<a href="NewsDetail.aspx?Nid=700&amp;GI=4">另一則公告</a>'
                '<a href="https://sso.nknu.edu.tw/News/ExternalLink.aspx?url=https%3A%2F%2Fc.nknu.edu.tw%2Faca%2Fx.pdf">'
                "外部連結</a></p>")
    # the key's own shape is documents.py's (ids.location_key since plan 2026-11-08 T1): what is checked here is that
    # the address is the crawler's spelling (path lower case, %-escaped, unwrapped) under that key
    assert [link.target for link in page_links(html, NOTICE)] == [
        _link_key(f"{ACA}uploadfile/news/20260101/%E5%81%9C%E4%BF%AE%E8%AA%AA%E6%98%8E.pdf"),
        _link_key(f"{ACA}newsdetail.aspx?nid=700&gi=4"), _link_key(f"{ACA}x.pdf")]


def test_links_that_lead_nowhere_are_left_out():
    html = page('<p><a href="javascript:void(0)">展開</a><a href="mailto:aca@example.edu">信箱</a>'
                '<a href="#top">回頂端</a><a href="">空</a><a>無連結</a><a href="f/a.pdf">a</a></p>')
    assert [link.anchor for link in page_links(html, FORMS)] == ["a"]


def test_page_claiming_big5_is_read_as_utf8():
    html = page('<p>附件一：停修申請表（<a href="f/s.pdf">pdf</a>）</p>', head='<meta http-equiv="Content-Type" '
                'content="text/html; charset=big5">')
    assert contexts(html) == [("pdf", "停修申請表")]
    assert contexts(b"\xef\xbb\xbf" + html) == [("pdf", "停修申請表")]


def test_page_that_is_not_utf8_raises():
    html = '<html><body><div id="lbl_Contents"><a href="a.pdf">停修申請表</a></div></body></html>'.encode("big5")
    with pytest.raises(PageDecodeError, match="UTF-8"):
        page_links(html, FORMS)


def test_a_row_of_one_cell_is_read_as_lines():
    # static pages wrap a whole page in a one-column table: its one cell is lines of text, not the fields of a record
    html = ('<html><body><table><tr><td>本期目錄<br>一、校務消息<br>停修申請表 <a href="s.pdf">PDF</a><br>'
            "二、榮譽榜 王O明</td></tr></table></body></html>")
    assert contexts(html.encode(), STATIC) == [("PDF", "停修申請表")]


def test_the_nearest_list_item_or_paragraph_comes_before_the_row():
    html = page('<table><tr><td>註冊組</td><td><ul><li>甲表 <a href="f/1.pdf">pdf</a></li>'
                '<li>乙表 <a href="f/2.pdf">pdf</a></li></ul></td></tr>'
                '<tr><td>學生退學申請書</td><td><p><a href="f/3.pdf">pdf</a></p></td></tr></table>')
    assert [c for _a, c in contexts(html)] == ["甲表", "乙表", "學生退學申請書"]


def test_links_after_another_on_one_line():
    html = page('<p>停修申請表（<a href="f/1.pdf">pdf</a>、<a href="f/1.doc">doc</a>）</p>'
                '<p>附件一：甲表(114學年度)（<a href="f/2.pdf">pdf</a>）、'
                '附件二：乙表(114學年度)（<a href="f/3.pdf">pdf</a>）</p>'
                '<p>丙表 <a href="f/4.pdf">pdf</a> 丁表 <a href="f/5.pdf">pdf</a> <a href="f/5.odt">odt</a></p>'
                '<p>戊表（<a href="f/6.pdf">pdf</a>）及（<a href="f/6.odt">odt</a>）</p>')
    assert [c for _a, c in contexts(html)] == [
        "停修申請表", "停修申請表", "甲表(114學年度)", "乙表(114學年度)", "丙表", "丁表", "丁表", "戊表", "戊表"]


def test_text_right_after_a_link_is_that_links_own():
    # 「31期(2019年3月)、32期…」: what follows a link up to the next separator belongs to it, never to the next link
    html = page('<p><a href="f/31.pdf">31</a>期(2019年3月)、<a href="f/32.pdf">32</a>期(2019年9月)、'
                '<a href="f/33.pdf">33</a>期(2020年3月, 春季號)、己表（<a href="f/7.pdf">pdf</a>）</p>')
    assert [c for _a, c in contexts(html)] == ["", "", "", "己表"]


def test_a_title_never_passes_a_link_that_names_its_own_file():
    # 「甲表 <乙表.pdf><下載>」: the 下載 beside 乙表.pdf is not 甲表's
    html = page('<p>甲表 <a href="f/1.pdf">pdf</a> <a href="f/2.pdf">乙表.pdf</a><a href="f/2.odt">下載</a></p>')
    assert [c for _a, c in contexts(html)] == ["甲表", "乙表", ""]


def test_symbols_at_the_ends_of_a_title_are_trimmed():
    html = page('<p>▲ 報名須知：👉<a href="f/a.pdf">下載</a></p>'
                '<table><tr><td>(僑生專用) 抵免申請表(限當學期)</td><td><a href="f/b.pdf">pdf</a></td></tr></table>')
    assert [c for _a, c in contexts(html)] == ["報名須知", "(僑生專用) 抵免申請表(限當學期)"]


def test_match_key_drops_the_scheme_the_crawler_may_have_rewritten():
    # the crawler writes a host that answers on one scheme only in that scheme (design §6.6): a page's http:// link
    # names the manifest's https:// file
    html = page('<p><a href="http://www.nknu.edu.tw/~unit/a.pdf">甲表</a></p>')
    (link,) = page_links(html, NOTICE)
    assert link.target == _link_key("http://www.nknu.edu.tw/~unit/a.pdf")
    assert match_key(link.target) == match_key(_link_key("https://www.nknu.edu.tw/~unit/a.pdf"))
    assert match_key("http://www.nknu.edu.tw/~unit/a.pdf") == match_key("https://www.nknu.edu.tw/~unit/a.pdf") == (
        "//www.nknu.edu.tw/~unit/a.pdf")
    assert match_key("nid:700") == "nid:700"


def test_links_the_page_never_shows_are_left_out():
    html = page('<p><noscript><a href="f/a.pdf">a</a></noscript><a href="f/b.pdf">b</a></p>'
                '<template><a href="f/c.pdf">c</a></template>')
    assert [link.anchor for link in page_links(html, FORMS)] == ["b"]


def test_targets_are_joined_to_the_pages_base():
    html = page('<p><a href="x.pdf">說明</a></p>', head=f'<base href="{ACA}sub/">')
    assert [link.target for link in page_links(html, NOTICE)] == [_link_key(f"{ACA}sub/x.pdf")]


@pytest.mark.parametrize("anchor", [
    "pdf", "PDF", "(pdf)", "[DOC]", "odt", "docx", "ods", "xlsx", "pptx", "zip", "rar", "jpg",
    "下載", "下载", "附件", "附件1", "附件(一)", "檔案", "檔案下載", "點此", "點我", "按此", "這裡", "點此下載",
    "點我下載", "請點此下載", "請點擊", "按這裡", "PDF檔", "word檔下載", "pdf及doc", "download", "Download", "here",
    "click here",
    "", "→", "(1)", "108", "115.3.15", "u112.pdf", "IMG_0031.JPG", "A1_form-2.docx",
])
def test_generic(anchor):
    assert GENERIC.fullmatch(anchor)


@pytest.mark.parametrize("anchor", [
    "學生退學申請書", "114學年度新生手冊.pdf", "新生攻略", "簡章", "停修申請表 pdf", "Course Guide.pdf", "第一頁",
    "和平校區", "公文檔案", "簡報下載", "u112", "profile",
])
def test_named(anchor):
    assert not GENERIC.fullmatch(anchor)
