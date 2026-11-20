"""11-18 (the JS sites' handoff item 7, after round two's first batch crawled them 11-17): 研发处 (ord), 工教系 (ite)
and 数学系 (gauss) share one vendor's template, whose content a script loads. Read on the rendered pages the archive
kept (research/crawler_design_2026-11-18/prep/, read only): a notice of 工教系 sits in div.billboardInfo beside the
whole list of the other notices (div.billboardList, about 10,000 characters on every notice page); a news item of
数学系 in div.newsInfo beside the news list; a teacher's page of 工教系 in section.teacherstaffInfo beside every
teacher; every other page (研发处's notices and lists, activities, downloads, plans) in div.mainwrap. A page with none
of them (a home page, 数学系's staff list) reads as before, its whole body. Made-up pages."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import export, links, profiles
from nknu.totext import pagetext

MENU = "<header><nav><a href='./?p=download&type=studentlaw'>學生法規</a> 選單甲 選單乙</nav></header>"
FOOT = "<footer>地址 電話 版權所有</footer>"


def _page(main: str) -> str:
    return f"<html><head><title>系網</title></head><body>{MENU}<div class='page'>{main}</div>{FOOT}</body></html>"


def _text(html: str, url: str) -> str:
    tree = LexborHTMLParser(html)
    return " ".join(" ".join((n.text(separator=" ") or "").split()) for n in pagetext.page_roots(tree, url))


@pytest.mark.parametrize("site", ["ord.nknu.edu.tw", "ite.nknu.edu.tw", "gauss.nknu.edu.tw"])
def test_the_three_sites_of_the_vendor_have_one_profile(site):
    p = profiles.profile_for_site(site)
    assert p is profiles.JS_VENDOR and p.content_css


@pytest.mark.parametrize("site", ["w3.nknu.edu.tw", "see.nknu.edu.tw"])
def test_the_static_js_sites_keep_no_profile(site):
    assert profiles.profile_for_site(site) is None


def test_a_notice_of_ite_reads_without_the_list_of_the_other_notices():
    html = _page("<section class='billboard'><div class='billboardList'><a href='./?p=billboard&id=2'>別則公告標題</a>"
                 "</div><div class='billboardInfo'><h1 class='maintitle'>這則公告</h1><article>公告內文第一段</article>"
                 "<a href='./api/dl.php?file=a1'>附件一</a></div></section>")
    text = _text(html, "https://ite.nknu.edu.tw/?p=billboard&id=1")
    assert "公告內文第一段" in text and "附件一" in text
    assert "別則公告標題" not in text and "選單甲" not in text and "版權所有" not in text


def test_a_news_item_of_gauss_reads_without_the_news_list():
    html = _page("<div class='newsList'><a href='./?p=news&id=5'>別則新聞</a></div>"
                 "<div class='newsInfo'><h1 class='maintitle'>這則新聞</h1><article>新聞內文</article></div>")
    text = _text(html, "https://gauss.nknu.edu.tw/?p=news&id=4")
    assert "新聞內文" in text and "別則新聞" not in text


def test_a_notice_of_ord_reads_its_main_wrap_with_title_and_date():
    html = _page("<div class='mainwrap'><h2 class='maintitle'>研發處公告</h2><div class='inform'>2026-11-01</div>"
                 "<section class='info'><article name='data_content'>計畫徵件說明</article></section></div>")
    text = _text(html, "https://ord.nknu.edu.tw/?p=page&category=1&id=9")
    assert "研發處公告" in text and "2026-11-01" in text and "計畫徵件說明" in text and "選單乙" not in text


def test_a_page_with_no_area_reads_its_whole_body_as_before():
    html = _page("<div class='banner'><div class='content'>首頁輪播文字</div></div>")
    text = _text(html, "https://gauss.nknu.edu.tw/")
    assert "首頁輪播文字" in text


def test_a_picture_outside_the_area_is_the_menus_and_one_inside_the_pages():
    html = _page("<div class='side'><img src='/img/side.png' width='300' height='300'></div>"
                 "<div class='mainwrap'><img src='/upload/poster.jpg' width='800' height='600'></div>")
    got = links.extract(html, "https://ite.nknu.edu.tw/?p=acadact&id=3",
                        profiles.content_css("https://ite.nknu.edu.tw/?p=acadact&id=3"))
    zones = {i.src.rsplit("/", 1)[-1]: i.zone for i in got.images}
    assert zones == {"side.png": "chrome", "poster.jpg": "body"}


def test_the_export_marks_no_notice_of_these_sites_as_lacking_its_area():
    """No Detail rule: the export never calls a page of them a deleted notice (找不到内文区) by the area."""
    tree = LexborHTMLParser(_page("<p>沒有容器的頁</p>"))
    assert export.empty_detail_area(tree, "https://ite.nknu.edu.tw/?p=billboard&id=1", "ite.nknu.edu.tw") == ""


@pytest.mark.parametrize("url, box", [
    ("https://ite.nknu.edu.tw/?p=billboard&type=enroll",
     "<div class='billboardInfo'><h1 class='maintitle'></h1><div class='date'>公告時間：</div><article></article></div>"),
    ("https://gauss.nknu.edu.tw/?p=news&type=academic",
     "<div class='newsInfo'><h1 class='maintitle'></h1><div class='date'>公告時間：</div><article></article></div>"),
    ("https://ite.nknu.edu.tw/?p=teacherstaff&type=1",
     "<section class='teacherstaffInfo'><div class='content'><div class='name'><!-- 王OO --></div></div></section>"),
])
def test_a_list_page_with_the_box_unfilled_reads_its_list(url, box):
    """A list page of notices, news or teachers holds the detail's box unfilled, its list outside: read whole."""
    html = _page(f"<div class='billboardList'><a href='./?id=7'>列表上的一則</a></div>{box}")
    assert "列表上的一則" in _text(html, url)
