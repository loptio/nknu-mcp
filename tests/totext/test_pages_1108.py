"""Page text for every platform (plan 2026-11-08 T2). page_roots decides per page what a page's text is read from: the
platform profile's content area when it matches; nothing on a profiled detail page that does not match; the whole body
under a download path or a static folder; else the body. An area and a body are read without their chrome (header,
nav, footer, aside and their ARIA roles: T2 review). page_kind names a page by its URL on every
platform and, off the c.nknu template, by the list grid its read content shows (list_grid, T2 review); PAGE_VERSION
has the to-text run read pages, and only pages, again. The pages are made up in the shape of each template (the four
pilot sites' 11-08 exports and the 10-24 archive, read only); the people in them are O masks."""

from __future__ import annotations

import dataclasses
import re

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import profiles
from nknu.totext import pagetext
from nknu.totext.accounting import Input, account, input_route
from nknu.totext.cache import PIPELINE_VERSION, Cache, sha256_file
from nknu.totext.pagetext import CHROME_TAGS, PAGE_VERSION, list_grid, page_kind, page_roots, page_text
from nknu.totext.run import run
from tests.totext.fixtures import refuse_real_models, tiny_pdf
from tests.totext.test_run import PDF_TEXT, FakeDocling, fake_tools, write_export

ACA = "https://c.nknu.edu.tw/aca/"
NOTICE = ACA + "newsdetail.aspx?nid=578&gi=2"
WEB = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=595"
CLV2 = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=100061"
GSITE = "https://sites.google.com/mail.nknu.edu.tw/counseling-center"
GSITE_PAGE = GSITE + "/%E4%B8%AD%E5%BF%83%E6%B2%BF%E9%9D%A9"  # 中心沿革


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, request):
    refuse_real_models(monkeypatch, request)


def lines(html: bytes, url: str) -> list[str]:
    return page_text(html, url).text.splitlines()


# ---------------------------------------------------------------- the templates

def webidx_page(main: str, *, area: bool = True) -> bytes:
    """An SSO unit site page (WebIdx, sso.nknu.edu.tw/web/N) as exported: the top bar, the menu bar (nav), the side
    card (aside), the content (article.main-content with its breadcrumb; area=False: main as it is, the home page has
    no such area) and the footer."""
    article = (f'<article id="mainContent" class="main-content"><nav id="breadcrumb"><ol class="breadcrumb">'
               f'<li><a href="default.aspx?webidx=595">首頁</a></li><li>關於生輔組</li></ol></nav>{main}</article>'
               if area else main)
    return ('<html><head><meta charset="utf-8"><title>生活輔導組</title></head><body>'
            '<div class="top-bar"><div class="language-switcher"><a id="btnLangTC" href="#">繁中</a> / '
            '<a id="btnLangEN" href="#">EN</a></div></div>'
            '<form id="form1"><nav class="navbar"><div class="logo"><a href="default.aspx?webidx=595">生活輔導組</a></div>'
            '<ul class="nav-links"><li><a href="default.aspx?webidx=595&amp;menuid=505">關於生輔組</a></li>'
            '<li><a href="default.aspx?webidx=595&amp;menuid=506">膳食服務</a></li></ul></nav>'
            '<main id="mainArea" class="container main-layout"><aside id="sidebar" class="sidebar">'
            '<div class="card-body"><a href="default.aspx?webidx=595&amp;menuid=518">生輔組成員</a>'
            '<img src="/images/side.png"></div></aside>'
            f'{article}</main>'
            '<footer id="footer" class="footer"><p>學生事務處生活輔導組 版權所有</p></footer>'
            '<script>var webIdx = 595;</script></form></body></html>').encode()


STAFF = ('<div class="content-header"><div class="content-title">生輔組成員</div></div><section>'
         '<p>生活輔導組組長 O組長</p><p>業務職掌：承學務長指導綜理學生生活輔導業務。</p>'
         '<img src="/uploads/595/staff.jpg"><p>分機：1230</p></section>')
HOME_NEWS = ('<section class="news-section"><h2>最新消息</h2><div class="news-row"><span class="col-date">11 9月</span> '
             '<span class="col-title"><a href="default.aspx?webidx=595&amp;newsidx=1">兵籍調查線上申報作業</a></span>'
             '</div></section>')


def clv2_page(content: str) -> bytes:
    """An SSO unit site page (cLv2, sso.nknu.edu.tw/site/N) as exported: the header with its menu, the content row
    (#divContent on a content page; the home page has none) and the footer."""
    return ('<html><body><form id="form1"><div id="preloader-active"><div class="preloader"></div></div>'
            '<header><div class="header-area"><a href="index.aspx?clv2=100061">校安中心(原軍訓室)</a>'
            '<nav><ul id="navigation"><li><a href="index.aspx?clv2=100061&amp;page=603">同仁介紹</a></li>'
            '<li><a href="index.aspx?clv2=100061&amp;page=607">折抵役期</a></li></ul></nav></div></header>'
            f'<main><div class="our-info-area"><div class="container"><div class="row">{content}</div></div></div>'
            '</main><footer><div id="footerArea">高師大校安中心</div></footer></form></body></html>').encode()


CLV2_STAFF = ('<div class="col-12"><h3 class="title">同仁介紹</h3></div><div id="divContent" class="divContent">'
              '<p>主任教官 O教官</p><p>分機：1234</p><img src="/Uploads/100061/a.jpg"></div>')
CLV2_HOME = ('<div class="col"><a href="index.aspx?clv2=100061&amp;page=607"><img src="/Uploads/100061/b.jpg">'
             '<span>折抵役期</span></a></div>')


def gsite_page(content: str) -> bytes:
    """A Google Sites page as exported: the site's header (banner and menu), a second menu, the content in plain
    divs, no platform profile."""
    return ('<html><body><div class="site"><header><div role="banner">學生輔導中心</div><nav>'
            f'<a href="{GSITE}/%E9%A6%96%E9%A0%81">首頁</a><a href="{GSITE_PAGE}">中心沿革</a></nav></header>'
            f'<nav><a href="{GSITE}/%E8%81%AF%E7%B5%A1%E6%88%91%E5%80%91">聯絡我們</a></nav>'
            f'<div class="page">{content}</div></div></body></html>').encode()


GSITE_TEXT = ('<section><p>民國59年11月在訓導處下設立「學生心理輔導室」。</p><p>歷任主管：O主任</p></section>'
              '<section><img src="https://lh7-rt.googleusercontent.com/docsz/AD_4nXa"></section>')

# a c.nknu notice as the 11-02 export holds one, test_assemble.notice_html's template with more in it: its header
# fields, a side menu outside the content area, the text in #lbl_Contents, the attachments in #lbl_FileLink, an album
# and a footer after it
NOTICE_HTML = (
    '﻿<html><head><meta charset="big5"><title>國立高雄師範大學 教務處</title></head><body><form id="form1">'
    '<div class="urbangreymenu"><ul id="navmenu-v"><li><a href="Default.aspx?GI=2">首頁</a></li>'
    '<li><a href="uploadfile/c.pdf">借用要點</a><img src="images/menu_icon.png"></li></ul></div>'
    '<span id="lbl_GroupName">和平教務組</span><span id="lbl_Title">115學年度第1學期停修公告</span>'
    '<h5>發佈人員：O小姐｜公告日期：<span id="lbl_AddTime">2026/10/21 上午 08:56:00</span></h5>'
    '<div id="lbl_Contents"><p>一、申請期間：115年11月2日至11月13日。</p><p>二、申請方式：<br>線上申請<br><br><br>逾期不受理</p>'
    '<img src="uploadfile/news/1/a.jpg">'
    '<table><tr><th>項目</th><th>日期</th></tr><tr><td>停修</td><td>11/13</td></tr></table>'
    '<table><tr><td><p>附件一：停修申請表</p></td></tr></table></div>'
    '<div class="panel-body"><span id="lbl_FileLink"><a href="uploadfile/news/1/a6.pdf">停修申請表.pdf&nbsp;(12 Kb)</a>'
    '<br><a href="uploadfile/news/1/m.zip">會議資料.zip</a></span></div>'
    '<div class="album"><img src="uploadfile/album/p1.jpg"><span>活動照片</span></div>'
    '<div id="footer">版權所有 國立高雄師範大學</div>'
    '</form></body></html>').encode()
# what page_text(NOTICE_HTML, "newsdetail") gave before T2 (pinned 11-08 with the 11-02 code)
NOTICE_TEXT = ("115學年度第1學期停修公告\n一、申請期間：115年11月2日至11月13日。\n二、申請方式：\n線上申請\n\n逾期不受理\n"
               "[[img:uploadfile/news/1/a.jpg]]\n| 項目 | 日期 |\n| --- | --- |\n| 停修 | 11/13 |\n附件一：停修申請表\n"
               "停修申請表.pdf (12 Kb)\n會議資料.zip")
# a notice c.nknu no longer has: the template of a page with none of the content area's fields
DELETED = ('<html><body><form id="form1"><div class="urbangreymenu"><ul><li><a href="Default.aspx">首頁</a></li></ul>'
           '</div><div class="alert">查無此公告</div></form></body></html>').encode()
STATIC_HTML = ('<html><body><header>高師大校訊</header><nav><a href="2.htm">第二版</a></nav><table><tr><td>'
               '<p>第一則</p><p>第二則</p></td></tr></table><img src="p1.jpg"></body></html>').encode()


# ---------------------------------------------------------------- page_text and page_roots

def test_sso_web_page_reads_its_content_area_and_no_menu():
    page = page_text(webidx_page(STAFF), WEB + "&menuid=518")
    # T2 review (deliberate): the breadcrumb nav inside the area (首頁 / 關於生輔組 on all 64 area pages of the
    # pilot's web/595) is chrome, left out below an area as below a body
    assert page.text.splitlines() == ["生輔組成員", "生活輔導組組長 O組長", "業務職掌：承學務長指導綜理學生生活輔導業務。",
                                      "[[img:/uploads/595/staff.jpg]]", "分機：1230"]
    assert page.images == ("/uploads/595/staff.jpg",) and (page.title, page.album) == ("", "")


def test_sso_web_home_without_a_content_area_reads_its_body_without_the_menus():
    # the pilot's webidx=595 home matches none of the profile's areas: its body, but the nav, the aside and the footer
    page = page_text(webidx_page(HOME_NEWS, area=False), WEB)
    assert page.text.splitlines() == ["繁中 / EN", "最新消息", "11 9月 兵籍調查線上申報作業"]
    assert page.images == ()  # the side card's picture is in the aside: a menu picture, not the page's


def test_clv2_page_reads_its_content_area():
    page = page_text(clv2_page(CLV2_STAFF), CLV2 + "&page=603")
    assert page.text.splitlines() == ["主任教官 O教官", "分機：1234", "[[img:/Uploads/100061/a.jpg]]"]


def test_clv2_home_without_a_content_area_reads_its_body_without_header_and_footer():
    assert lines(clv2_page(CLV2_HOME), CLV2) == ["[[img:/Uploads/100061/b.jpg]]", "折抵役期"]


def test_google_sites_page_reads_its_body_and_keeps_its_pictures():
    page = page_text(gsite_page(GSITE_TEXT), GSITE_PAGE)
    picture = "https://lh7-rt.googleusercontent.com/docsz/AD_4nXa"
    assert page.text.splitlines() == ["民國59年11月在訓導處下設立「學生心理輔導室」。", "歷任主管：O主任", f"[[img:{picture}]]"]
    assert page.images == (picture,)


# Google's interface as the pilot's Google Sites pages hold it outside header, nav, footer and aside (T2 review): the
# banner's search, the footer bar's menu (under an aria-hidden div), the skip link (a button: read, as the review
# expected)
GSITE_UI = ('<div role="banner"><div>Search this site</div><div>Embedded Files</div></div>'
            '<div role="button">Skip to main content</div>'
            '<div aria-hidden="true"><div role="menu"><span role="menuitem">Report abuse</span>'
            '<span role="menuitem">Page details</span></div></div><span role="contentinfo"></span>')
WIX_PHOTO = "https://static.wixstatic.com/media/5d4fa6_ff46.jpg"


def test_chrome_roles_are_left_out_of_a_body_and_aria_hidden_is_read():
    # T2 review: the ARIA names of header, nav, footer and aside, a search box and a menu (any case, one role among
    # several) are chrome; role=main and a button are read. What aria-hidden="true" hides is read (deliberate, against
    # the review's proposal): a Wix gallery keeps its photos there
    html = gsite_page(GSITE_UI + '<form role="search"><p>關鍵字查詢</p></form><div role="Navigation">你目前位置：首頁</div>'
                      '<div role="complementary">側欄</div><ul role="Menu menubar"><li>選單</li></ul>'
                      f'<div class="gallery-item-container" aria-hidden="true"><p>活動照片</p><img src="{WIX_PHOTO}">'
                      '</div><div role="main"><p>正文</p><img src="a.jpg"></div>')
    page = page_text(html, GSITE_PAGE)
    assert page.text.splitlines() == ["Skip to main content", "活動照片", f"[[img:{WIX_PHOTO}]]", "正文", "[[img:a.jpg]]"]
    assert page.images == (WIX_PHOTO, "a.jpg")


def test_chrome_roles_in_a_data_table_keep_the_columns():
    html = gsite_page('<table><tr><th>項目</th><th>說明</th></tr><tr role="navigation"><td>第 1 頁</td><td>下一頁</td>'
                      '</tr><tr><td><span role="navigation">首頁 ›</span>停修</td><td role="search">搜尋</td></tr></table>')
    assert lines(html, GSITE_PAGE) == ["| 項目 | 說明 |", "| --- | --- |", "| 停修 |  |"]


def test_chrome_below_a_content_area_is_left_out_but_the_area_itself_is_read():
    # T2 review: the area's own chrome (WebIdx's breadcrumb) goes the way a body's does; an area root is read whatever
    # it is (a content area matched on a <nav> would be read, not dropped)
    assert lines(webidx_page('<nav class="crumbs"><a href="x">首頁</a></nav><p>本文</p>'), WEB + "&menuid=518") == ["本文"]
    html = ('<html><body><nav id="lbl_Contents"><p>甲</p><nav>選單</nav><p>乙</p></nav>'
            '<div id="menu">側選單</div></body></html>').encode()
    assert lines(html, ACA + "page.aspx?pn=1") == ["甲", "乙"]
    tree = LexborHTMLParser(html.decode())
    assert [n.text().strip() for n in page_roots(tree, ACA + "page.aspx?pn=1")] == ["甲", "乙"]


def test_a_content_area_matched_twice_is_read_once():
    # a selector list gives an element once per selector it matches (lexbor): #lbl_Contents and .news_content
    html = '<html><body><div id="lbl_Contents" class="news_content"><p>停修申請</p></div></body></html>'.encode()
    assert lines(html, ACA + "page.aspx?pn=1") == ["停修申請"]
    assert len(page_roots(LexborHTMLParser(html.decode()), ACA + "page.aspx?pn=1")) == 1


def test_cnknu_notice_gives_exactly_the_text_it_gave_before():
    page = page_text(NOTICE_HTML, NOTICE)
    assert page.text == NOTICE_TEXT
    assert (page.images, page.title, page.album) == (("uploadfile/news/1/a.jpg",), "115學年度第1學期停修公告", "")
    # the same notice on another c.nknu-template host (tecs has its own domain): the same content area
    assert page_text(NOTICE_HTML, "https://tecs.nknu.edu.tw/newsdetail.aspx?nid=578&gi=2").text == NOTICE_TEXT


def test_deleted_notice_template_gives_nothing():
    assert page_roots(LexborHTMLParser(DELETED.decode()), NOTICE) == [] and page_text(DELETED, NOTICE).text == ""
    # an area that is there but empty is the profile's area all the same: nothing either (the pilot's reality
    # newsview pages that no longer exist hold an empty table#DataFormView)
    empty = DELETED.replace(b'<div class="alert">', b'<table id="DataFormView"></table><div class="alert">')
    assert page_text(empty, ACA + "newsview.aspx?nid=1").text == ""
    # only a detail page stays empty: another page of that template is read as its body
    assert lines(DELETED, ACA + "page.aspx?pn=64") == ["首頁", "查無此公告"]


def test_static_page_is_read_whole():
    want = ["高師大校訊", "第二版", "第一則", "第二則", "[[img:p1.jpg]]"]
    assert lines(STATIC_HTML, ACA + "uploadfile/userfiles/files/spaper/89/01.html") == want
    # another site's uploaded page (the profile's download paths), never by its template's content area
    assert lines(STATIC_HTML, "https://c.nknu.edu.tw/affair/uploadfile/files/a.htm") == want


def test_a_content_area_inside_a_dropped_element_is_no_match():
    html = ('<html><body><p>正文</p><noscript><div id="lbl_Contents">請開啟 JavaScript</div></noscript>'
            '<nav>選單</nav></body></html>').encode()
    assert lines(html, ACA + "page.aspx?pn=1") == ["正文"]


def test_chrome_and_dropped_elements_are_left_out_wherever_they_sit():
    html = ('<html><body><div><p>甲</p><div><nav>選單</nav><p>乙</p></div><noscript><header>頁首</header>丙</noscript>'
            '<table><tr><th>欄</th><th>值</th></tr><tr><td>丁<aside>側欄</aside></td><td>戊</td></tr></table>'
            '<footer>頁尾</footer></div></body></html>').encode()
    assert lines(html, GSITE_PAGE) == ["甲", "乙", "| 欄 | 值 |", "| --- | --- |", "| 丁 | 戊 |"]


def test_roots_of_a_body_are_outermost_hold_no_chrome_and_keep_document_order():
    tree = LexborHTMLParser(gsite_page(GSITE_TEXT).decode())
    roots = page_roots(tree, GSITE_PAGE)
    ids = {r.mem_id for r in roots}
    for root in roots:
        assert root.tag not in CHROME_TAGS and (not root.is_element_node or root.css_first("header, nav, footer, aside")
                                                is None)
        up = root.parent
        while up is not None:
            assert up.mem_id not in ids
            up = up.parent
    assert [r.attributes.get("class") for r in roots if r.is_element_node] == ["page"]
    # no chrome in the body: the body is the one root
    tree = LexborHTMLParser("<html><body><p>甲</p><p>乙</p></body></html>")
    assert [r.tag for r in page_roots(tree, GSITE_PAGE)] == ["body"]


def test_roots_follow_the_rule_of_each_page():
    notice = LexborHTMLParser(NOTICE_HTML.decode("utf-8-sig"))
    assert [n.attributes.get("id") for n in page_roots(notice, NOTICE)] == ["lbl_Title", "lbl_Contents",
                                                                              "lbl_FileLink"]
    static = LexborHTMLParser(STATIC_HTML.decode())
    assert [n.tag for n in page_roots(static, ACA + "uploadfile/userfiles/files/spaper/1.htm")] == ["body"]


# ---------------------------------------------------------------- page_kind

@pytest.mark.parametrize("url,kind", [
    # c.nknu-template hosts keep the 10-29 names, on their own domains too
    (NOTICE, "newsdetail"), (ACA + "newsview.aspx?nid=1", "newsview"), (ACA + "newslist.aspx#第2页", "newslist#翻页/按钮"),
    (ACA + "page.aspx?sn=3", "page(sn)"), (ACA + "uploadfile/userfiles/files/spaper/1.htm", "静态:spaper"),
    ("https://tecs.nknu.edu.tw/newsdetail.aspx?nid=12&gi=1", "newsdetail"),
    ("https://c.nknu.edu.tw/reality/newsview.aspx?nid=882", "newsview"),
    # SSO unit sites (WebIdx): a detail, a detail a row's press brought, a pager press's row; by the URL alone any other
    # page is page:<profile>, MoreNews and the home too: a list is read from the page (T2 review, deliberate; see
    # test_a_list_is_read_from_the_page_whatever_its_spelling)
    (WEB + "&selpage=MoreNews&newsIdx=1326", "detail:sso_webidx"), (WEB + "&selpage=MoreNews", "page:sso_webidx"),
    (WEB, "page:sso_webidx"), (WEB.replace("webidx", "cwebidx"), "page:sso_webidx"),
    (WEB + "&menuid=518", "page:sso_webidx"),
    (WEB + "&selpage=MoreNews#按钮=兵籍調查線上申報作業", "detail:sso_webidx#按钮"),
    (WEB + "&selpage=MoreNews#第2页", "list:sso_webidx#翻页"),
    # cLv2
    (CLV2 + "&page=603", "page:sso_clv2"), (CLV2, "page:sso_clv2"), (CLV2 + "#按钮=交通安全宣導", "detail:sso_clv2#按钮"),
    # the SSO news centre
    ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=ABC", "detail:sso_news"),
    ("https://news.nknu.edu.tw/nknu_news/lastnewslist_dept.aspx?place=CB1", "page:sso_news"),
    ("https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=1#第3页", "list:sso_news#翻页"),
    ("https://sso.nknu.edu.tw/News/AttachedFile.aspx?cEncrptedFilePath=x", "page:sso_news"),
    # a platform with a profile and no paths
    ("https://lis.nknu.edu.tw/index.php/zh/", "page:lis"),
    # SSO systems with profiles of their own since the crawler's b1f8a99: 学务处系统, 法规汇编, 校园授权软件
    ("https://sso.nknu.edu.tw/osa/index.aspx", "page:sso_osa"),
    ("https://sso.nknu.edu.tw/regulations/index.aspx", "page:sso_regulations"),
    ("https://sso.nknu.edu.tw/software/campussoftware.aspx?csoftid=3", "detail:sso_software"),
    # unprofiled school sites: a round-1 Google Site, an nknu.edu.tw host, a school address, a school Google Site
    (GSITE_PAGE, "page"), ("https://www.nknu.edu.tw/~abc/x.htm", "page"), ("http://140.127.40.31/x.htm", "page"),
    ("https://sites.google.com/mail.nknu.edu.tw/111/a", "page"),
    # everything else
    ("https://forms.gle/x", "外站:forms.gle"), ("https://docs.google.com/forms/d/e/1/viewform", "外站:docs.google.com"),
    ("https://sites.google.com/view/another-club", "外站:sites.google.com"), ("https://reurl.cc/52Lly6", "外站:reurl.cc"),
    # a round-two unit's own site off the school's domain, in the content side's site table since 11-17
    ("https://sites.google.com/view/nknuparking", "page"), ("https://ul2974.wixsite.com/nknuhakka/about", "page"),
])
def test_page_kind(url, kind):
    assert page_kind(url) == kind == page_kind(url, "")  # the URL alone, or a page that shows no list grid


def test_page_kind_without_the_page_is_the_urls_alone():
    # the accounting names a page's route by its 网址 alone (no result depends on the kind); assemble adds the page
    assert page_kind(WEB + "&menuid=518") == page_kind(WEB + "&menuid=518")
    assert page_kind("not a url at all") == "外站:" and page_kind("http://[::1") == "其他:"


# ---------------------------------------------------------------- lists, read from the page (T2 review)

def items_list(*titles: str) -> str:
    """WebIdx's 书表 (menuid=N) as the pilot's web/595 holds it: rows of category, title, a download press, the hits
    and the date, in the area's items view."""
    rows = "".join(f'<div class="items-row"><div class="col-cat">宿舍</div><div class="col-title">{t}</div>'
                   f'<div class="col-dowload"><a id="ucAllTypeList_rptItems_ctl0{i}_lnkDownloadFile" href="javascript:'
                   f"__doPostBack('ucAllTypeList$rptItems$ctl0{i}$lnkDownloadFile','')\">下載</a></div>"
                   f'<div class="col-hits">71</div><div class="col-date">2025-09-01</div></div>'
                   for i, t in enumerate(titles))
    return f'<div class="file-items items-view-container"><div class="items-list-wrapper">{rows}</div></div>'


# a WebIdx home whose news area is MoreNews's first page (the 10-24 archive's webidx=3 and 35, read only)
NEWS_VIEW = ('<section id="moreNewsSection" class="container news-section"><div class="news-view-container">'
             '<div class="news-list-wrapper"><div class="news-row"><span class="col-date">2026-10-01</span>'
             '<span class="col-title"><a href="default.aspx?webidx=595&amp;newsidx=1">兵籍調查線上申報作業</a></span>'
             '</div></div></div></section>')
CLV2_FILES = ('<div id="uDataContentPos2_UpdatePanel2"><table id="uDataContentPos2_uList" class="table w-100">'
              '<tr><th>類別</th><th>名稱</th><th>下載</th></tr><tr><td>兵役</td><td><span id="uDataContentPos2_conLB0">'
              "折抵役期申請表</span></td><td><a id=\"uDataContentPos2_downloadBtn0\" href=\"javascript:__doPostBack("
              "'uDataContentPos2$downloadBtn0','')\">下載</a></td></tr></table></div>")
CLV2_NEWS = ('<div id="uSSONews_PanelList"><table id="uSSONews_gvList0"><tr><td>'
             '<a id="uSSONews_gvList0_ctl02_gv_lbSel" title="交通安全宣導" '
             "href=\"javascript:__doPostBack('uSSONews$gvList0$ctl02$gv_lbSel','')\">交通安全宣導</a>"
             "</td></tr></table></div>")
GVFULL = ('<html><body><form id="uForm"><table id="gvFull"><tr><td><span id="gvFull_ctl02_StartDate">2026/10/01</span>'
          '<a id="gvFull_ctl02_linkSubject_short" href="lastnews.aspx?unid=ABC">停水通知</a></td></tr></table></form>'
          '</body></html>').encode()
WEB_ITEMS = WEB + "&menuid=506"


def kind_read(html: bytes, url: str, where: str = "") -> str:
    """page_kind as assemble is to name a page (Task 3): by its 网址, with the list grid of the page where it was
    read (最后落地网址, else 网址)."""
    return page_kind(url, list_grid(LexborHTMLParser(html.decode()), where or url))


def test_a_list_is_read_from_the_page_whatever_its_spelling():
    # a 书表 list and a prose page have one URL shape (menuid=N); the list's pages and the copies the pilot's web/595
    # also exports (cwebidx=595&cmenuid=N, pagenum=K: 40 of its 66 pages hold the 书表 grid) are lists all, as their
    # content says
    books = webidx_page(items_list("住宿申請表", "請假單"))
    copy = WEB_ITEMS.replace("webidx", "cwebidx").replace("menuid", "cmenuid")
    for url in (WEB_ITEMS, WEB_ITEMS + "&pagenum=2", copy):
        assert kind_read(books, url) == "list:sso_webidx", url
    assert list_grid(LexborHTMLParser(books.decode()), WEB_ITEMS) == "webidx_items"
    assert kind_read(webidx_page(STAFF), WEB + "&menuid=518") == "page:sso_webidx"
    # the home: a list when its news area is MoreNews's first page, a page when it is not (the pilot's webidx=595),
    # the same on both spellings of its address
    for url in (WEB, WEB.replace("webidx", "cwebidx")):
        assert kind_read(webidx_page(NEWS_VIEW, area=False), url) == "list:sso_webidx", url
        assert kind_read(webidx_page(HOME_NEWS, area=False), url) == "page:sso_webidx", url
    assert kind_read(webidx_page(NEWS_VIEW, area=False), WEB + "&selpage=MoreNews") == "list:sso_webidx"


def test_lists_of_clv2_and_the_news_centre():
    assert kind_read(clv2_page(CLV2_FILES), CLV2 + "&page=4") == "list:sso_clv2"  # its 书表 (the pilot's page=4)
    assert kind_read(clv2_page(CLV2_NEWS + CLV2_HOME), CLV2) == "list:sso_clv2"  # a home with the unit's notices
    assert kind_read(clv2_page(CLV2_STAFF), CLV2 + "&page=603") == "page:sso_clv2"
    assert kind_read(GVFULL, "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=1") == "list:sso_news"


def test_a_list_grid_counts_where_it_is_read_with_rows_or_its_words_for_none():
    rows = ('<div class="news-list-wrapper"><div class="news-row"><span class="col-title"><a href="x">甲</a></span>'
            '</div></div>')
    # in a side card (chrome), in a nav inside the area, in a dropped element: the page shows no list
    assert kind_read(webidx_page(HOME_NEWS + f"<aside>{rows}</aside>", area=False), WEB) == "page:sso_webidx"
    assert kind_read(webidx_page(STAFF + f"<nav>{rows}</nav>"), WEB + "&menuid=518") == "page:sso_webidx"
    assert kind_read(webidx_page(STAFF + f"<noscript>{rows}</noscript>"), WEB + "&menuid=518") == "page:sso_webidx"
    # no row: the platform's words for an empty list make it one; a grid with neither is none
    empty = items_list().replace("</div></div>", "<p>查無資料</p></div></div>")
    assert kind_read(webidx_page(empty), WEB_ITEMS) == "list:sso_webidx"
    assert kind_read(webidx_page(items_list()), WEB_ITEMS) == "page:sso_webidx"
    untitled = '<div class="items-list-wrapper"><div class="items-row">無標題</div></div>'
    assert kind_read(webidx_page(untitled), WEB_ITEMS) == "page:sso_webidx"  # no title: no row of the row set
    # a detail stays a detail; the c.nknu template keeps its 10-29 names (EXCLUDED_KINDS), its lists unread
    assert kind_read(webidx_page(NEWS_VIEW, area=False), WEB + "&selpage=MoreNews&newsIdx=1326") == "detail:sso_webidx"
    grid = ('<html><body><table id="MasterGrid"><tr><td><a href="newsdetail.aspx?nid=1">停修公告</a></td></tr></table>'
            '</body></html>').encode()
    assert list_grid(LexborHTMLParser(grid.decode()), ACA + "newslist.aspx") == ""
    assert kind_read(grid, ACA + "newslist.aspx") == "newslist" and kind_read(grid, ACA + "page.aspx?pn=1") == "page"
    assert kind_read(gsite_page(NEWS_VIEW), GSITE_PAGE) == "page"  # no profile, no list grid
    # the grid is read where the page was read (最后落地网址); the kind is named by its 网址
    books = webidx_page(items_list("請假單"))
    assert list_grid(LexborHTMLParser(books.decode()), WEB_ITEMS) == "webidx_items"
    assert kind_read(books, "https://reurl.cc/x", WEB_ITEMS) == "外站:reurl.cc"
    assert kind_read(books, WEB + "&menuid=507", WEB_ITEMS) == "list:sso_webidx"


# ---------------------------------------------------------------- PAGE_VERSION

def test_page_version_names_the_rule_and_follows_the_profiles():
    assert re.fullmatch(r"pages-4:[0-9a-f]{10}", PAGE_VERSION)  # pages-3: the T2 review's chrome; pages-4: item 22
    assert pagetext.page_version(profiles.PROFILES) == PAGE_VERSION
    for change in ({"content_css": "#elsewhere"}, {"download_paths": "/files/"}, {"detail_paths": "/x\\.aspx$"},
                   {"sites": "sso\\.nknu\\.edu\\.tw/site/\\d+|sso\\.nknu\\.edu\\.tw/new"}):
        changed = [dataclasses.replace(p, **change) if p.name == "sso_clv2" else p for p in profiles.PROFILES]
        assert pagetext.page_version(changed) != PAGE_VERSION, change
    # what page_roots does not read leaves it alone
    calm = [dataclasses.replace(p, list_paths="/other$") if p.name == "sso_clv2" else p for p in profiles.PROFILES]
    assert pagetext.page_version(calm) == PAGE_VERSION


def _old_page_result(page_kind_then: str) -> dict:
    return {"route": "page", "kind": "html", "pdf_offset": 0, "page_kind": page_kind_then, "status": "empty",
            "reason": "网页内容区没有文字", "page": {"title": "", "album": "", "images": []}, "first_key": "1"}


@pytest.mark.parametrize("then", ["page", f"page|body|pages-1:{'0' * 10}",  # the 11-02 run's, and older rules'
                                  f"page|body|pages-2:{'0' * 10}"])
def test_pages_read_by_an_older_rule_are_read_again_and_a_pdf_result_stays(tmp_path, then):
    pdf = tiny_pdf(tmp_path / "t.pdf", [PDF_TEXT]).read_bytes()
    export = write_export(tmp_path / "export", [
        {"type": "网页", "url": GSITE_PAGE, "file": "pages/0001-a.html", "bytes": gsite_page(GSITE_TEXT)},
        {"type": "附件", "url": GSITE + "/a.pdf", "file": "files/0001-a.pdf", "bytes": pdf, "fmt": "pdf"}])
    out = tmp_path / "out"
    cache = Cache(out / "cache")
    page_sha, pdf_sha = sha256_file(export / "pages/0001-a.html"), sha256_file(export / "files/0001-a.pdf")
    cache.save(page_sha, _old_page_result(then), "", {})
    cache.save(pdf_sha, {"route": "pdf", "kind": "pdf", "pdf_offset": 0, "page_kind": "", "status": "ok", "reason": "",
                         "pages": 1, "chars": 9, "first_key": "2"}, "PDF 的文字", {"ocr.json": b"[]"})
    assert cache.done(page_sha) is None and cache.done(pdf_sha)["version"] == PIPELINE_VERSION
    before = {a.key: (a.status, a.reason) for a in account(export, cache)}
    assert before == {"1": ("pending", "还没转"), "2": ("ok", "")}
    fake = FakeDocling()
    summary = run(export, out, tools=fake_tools(fake))
    assert (summary["converted"], summary["cached"], fake.calls) == (1, 1, [])
    page = cache.done(page_sha)
    assert page["page_kind"] == f"page|body|{PAGE_VERSION}" and page["status"] == "ok"
    assert cache.text(page_sha).startswith("民國59年11月") and cache.text(pdf_sha) == "PDF 的文字"
    assert sorted(p.name for p in cache.dir(pdf_sha).iterdir()) == ["ocr.json", "result.json", "text.md"]
    assert {a.key: a.status for a in account(export, cache)} == {"1": "ok", "2": "ok"}


# ---------------------------------------------------------------- the accounting's page route

def _input(url: str, final: str = "") -> Input:
    return Input(row=1, type="网页", file="pages/0001-a.html", url=url, final_url=final, fmt="html", title="",
                 linktext="", linked="")


def test_a_page_route_carries_its_kind_its_reading_and_the_page_version(tmp_path):
    path = tmp_path / "a.html"
    path.write_bytes(NOTICE_HTML)
    route = input_route(_input(NOTICE), path)
    assert (route.name, route.kind, route.url) == ("page", "html", NOTICE)
    assert route.page_kind == f"newsdetail|cnknu:nothing|{PAGE_VERSION}"
    assert input_route(_input(WEB + "&menuid=518"), path).page_kind == f"page:sso_webidx|sso_webidx:body|{PAGE_VERSION}"
    assert input_route(_input(ACA + "uploadfile/userfiles/files/spaper/1.htm"), path).page_kind == (
        f"静态:spaper|cnknu:whole|{PAGE_VERSION}")
    assert input_route(_input(GSITE_PAGE), path).page_kind == f"page|body|{PAGE_VERSION}"
    # a short link is read where it landed (links.page_links reads its links there too), named by its own address
    landed = input_route(_input("https://reurl.cc/52Lly6", ACA + "page.aspx?pn=64&gi=4"), path)
    assert (landed.page_kind, landed.url) == (f"外站:reurl.cc|cnknu:body|{PAGE_VERSION}", ACA + "page.aspx?pn=64&gi=4")


def test_rows_of_one_content_share_a_result_only_when_read_alike(tmp_path):
    export = write_export(tmp_path / "export", [
        {"type": "网页", "url": NOTICE, "file": "pages/0001-a.html", "bytes": NOTICE_HTML},
        {"type": "网页", "url": ACA + "newsdetail.aspx?nid=578", "file": "pages/0002-a.html", "bytes": NOTICE_HTML},
        {"type": "网页", "url": GSITE_PAGE, "file": "pages/0003-a.html", "bytes": NOTICE_HTML}])
    out = tmp_path / "out"
    summary = run(export, out, tools=fake_tools())
    assert (summary["converted"], summary["cached"]) == (1, 2)
    accounts = {a.key: a for a in account(export, Cache(out / "cache"))}
    assert accounts["1"].status == accounts["2"].status == "ok"  # a notice with and without gi: read alike
    assert accounts["3"].status == "skipped"
    assert accounts["3"].reason == ("和第 1 行是同一个档，那一行按「page/html，先读 cnknu 的正文区，对不上就不读」转过；"
                                    "这一行要按「page/html，读去掉页首、选单、页尾的整页」读，没有另外转")
