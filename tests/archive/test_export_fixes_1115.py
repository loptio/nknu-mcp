"""Five things the content session asked of the export after its build of the 111 sites, and the pages of the same
kinds the look for them turned up (the owner 11-15: 「我们选"先做这 5 件小修"。请你现在去实验室电脑只读看一下这 5 种网页的
实际结构（不发校网请求、先不改代码），把这 5 件事的具体做法…一起列给我，我点头后你再动手改代码。」; shown the design
docs/superpowers/specs/2026-11-15-export-fixes-design.md: 「这 5 件全部照你的设计做。」「你顺带加的「同类」四条错误页标记，
以及地理系第四条标题规则，全部保留一起做。」「可以开工了」). Measured 11-15 on the lab's exports, read only
(research/crawler_lab_2026-11-04/fixes_1115/):

1. titles (profiles.PageTitle, the export's alone, as on 11-14): 成教所's 心得發表 (1,030 pages, 916 titles) and
   學生園地 (21) in #DataFormView_TitleLabel; a teacher's vita page on SSO's 资料开放查询 (47 pages of round one, each
   titled 「Report Viewer Configuration Error」 by the h2 of a message the report control keeps hidden) in
   #ctl00_phMain_uName; 地理系's member pages (48) in .teaminfo .name, its other pages (lists: 175 of 180) in
   .page-header;
2. the school's answers of no data (profiles.NoData, the export's alone, as on 11-09): 师培处's nodata.html (131 rows,
   130 of them old notice addresses the school sends there), c.nknu's 「404: File not found」 (79), 语文教学中心's
   404.php (20), the notice platform's unit lists of no row (53), 电机系's English notices with no title, text or
   attachment (482);
3. a notice's posting unit (profiles.Detail.unit, the export's alone): 公告单位, a 30th column, the SSO 公告中心's
   #ctl00_phMain_lbUnitName (1,953 of 2,004 notice pages hold one);
4. content areas (profiles.content_css): 电机系's right column without its path line, the vita page's card.

How each is done is 【Claude 的执行办法】. Made-up pages in the templates' shapes, names O-masked, no school text (the two
headings of the report control's hidden messages are its maker's words)."""

from __future__ import annotations

import dataclasses

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import export, links, outcome, pagetypes, profiles
from tests.archive.test_export_1027 import BASE, SITE, make_archive
from tests.archive.test_export_no_data_1109 import (
    SECTION,
    _archive,
    _export_at,
    _exported,
    _moved,
    _section,
    _without_note,
)

TG, GEO, DATAOPEN, ENGLISH = "c.nknu.edu.tw/tg", "geo.nknu.edu.tw", "sso.nknu.edu.tw/dataopen2search", "english.nknu.edu.tw"
EXPERIENCE_URL = "https://c.nknu.edu.tw/tg/experience_view.aspx?pid=31"
CLUB_URL = "https://c.nknu.edu.tw/tg/club_view.aspx?pid=4"
VITA_URL = "https://sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=0A1B2C"
MEMBER_URL = "https://geo.nknu.edu.tw/index.php?option=module&lang=cht&task=pageinfo&id=12&index=3"
LIST_URL = "https://geo.nknu.edu.tw/index.php?option=module&lang=cht&task=showlist&id=5&index=1"
TITLE_CELL, NAME_CELL, MEMBER_CELL, HEADER_CELL = ("#DataFormView_TitleLabel", "#ctl00_phMain_uName", ".teaminfo .name",
                                                   ".page-header")
RV_ERROR = "Report Viewer Configuration Error"  # the heading of the report control's first hidden message
GEO_SITE = "測試學系"  # the logo (h1) of every made-up 地理系 page
MEMBER = '<div class="teaminfo"><div class="teamleft"><div class="name">王O明</div><div>教授</div></div></div>'
MEMBERS = ('<ul><li><a href="#"><div class="info"><div class="name">李O華</div></div></a></li>'
           '<li><a href="#"><div class="info"><div class="name">陳O安</div></div></a></li></ul>')
GEO_NOTICE = '<dl class="newslist2"><dt>發布日期 2026-03-01</dt><dd class="title">地理週活動公告</dd></dl><p>歡迎參加。</p>'


def experience(title: str = "參訪心得分享", section: str = "心得發表") -> str:
    """A 心得發表 page of the c.nknu template (made up, in the shape of 成教所's: the section's name in the h2 every
    such page carries, the entry's own title after 「心得標題：」 in DataFormView_TitleLabel)."""
    return (f'<html><head><title>測試研究所</title></head><body><form method="post" id="form1"><h2 id="MainContent">'
            f'{section}</h2><div id="DataFormView"><h3>心得標題： <span id="DataFormView_TitleLabel">{title}</span></h3>'
            '<div class="post-info">發表日期： 2026/03/02 發表人： 王O明</div>'
            '<span id="DataFormView_ContentsLabel">這次參訪收穫很多。</span></div></form></body></html>')


def vita(name: str = "王O明") -> str:
    """A teacher's vita page of SSO's 资料开放查询 (made up, in the shape of round one's 47: no h1, the teacher's card,
    then the report control with its two messages hidden by style, each under an h2)."""
    return (f'<html><head><title>測試大學 - {name} Wang O-Ming</title></head><body><form method="post" id="uForm">'
            '<div class="container-fluid"><div id="ctl00_phMain_divVita" class="card-body"><div class="form-group">'
            f'<span id="ctl00_phMain_uName" class="text-primary">{name}</span></div>'
            '<a id="ctl00_phMain_cEmail" href="#">電子信箱（測試）</a>'
            '<div id="ctl00_phMain_panelEdu" class="mypanel"><table id="ctl00_phMain_tb7"><tr><td>博士</td>'
            '<td>測試大學教育學系</td></tr></table></div></div>'
            '<div id="ctl00_phMain_ReportViewer1_ReportViewer"><div id="ctl00_phMain_ReportViewer1" class="MSRS-RVC">'
            '<div id="ctl00_phMain_ReportViewer1_HttpHandlerMissingErrorMessage" style="border-color:Red;display:none;">'
            f'<h2>{RV_ERROR}</h2><p>The handler is not registered in this application.</p></div>'
            '<div id="ctl00_phMain_ReportViewer1_BrowserNotSupportedErrorMessage" style="display:none;">'
            '<h2>Browser not supported</h2><p>Use another browser to see the report.</p></div></div></div></div>'
            '</form></body></html>')


def geo_page(header: str = "專任師資", inner: str = MEMBER) -> str:
    """A page of 地理系's own site (made up, in its shape: the site's name in the logo's h1 on every page, the page's
    own name in .page-header, what the page holds in .pbox1)."""
    return (f'<html><head><title>{header}-{GEO_SITE}</title></head><body><div id="page"><header id="header">'
            f'<h1 class="logo"><a class="clearfix" href="/">{GEO_SITE}</a></h1></header><div id="pbanner"><h3>Faculty 系所成員'
            f'</h3></div><div id="pcontent"><div class="container"><div class="pageright"><div class="page-header">{header}'
            f'</div><div class="pbox1 edit">{inner}</div></div></div></div></div></body></html>')


def _title(html: str, url: str, site: str) -> str:
    return export.page_title(LexborHTMLParser(html), url, site)


# ---------------------------------------------------------------- 1. titles


@pytest.mark.parametrize(("url", "cells"), [
    (EXPERIENCE_URL, [TITLE_CELL]),
    (CLUB_URL, [TITLE_CELL]),
    ("https://tecs.nknu.edu.tw/Experience_View.aspx?pid=1", [TITLE_CELL]),  # the template's other sites, any case
    (VITA_URL, [NAME_CELL]),
    (MEMBER_URL, [MEMBER_CELL, HEADER_CELL]),
    (LIST_URL, [MEMBER_CELL, HEADER_CELL]),
])
def test_the_title_rules_of_a_page_are_those_of_its_own_address_in_the_tables_order(url, cells):
    assert [t.title for t in profiles.page_title_rules(url)] == cells
    assert profiles.page_title_rule(url).title == cells[0]  # the first, as 11-14's callers read it


@pytest.mark.parametrize("url", [
    "https://c.nknu.edu.tw/tg/experience.aspx?pn=1",  # the list of the entries
    "https://c.nknu.edu.tw/tg/experience_view.aspx.bak",
    "https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=1",  # the same site's course tables
    "https://sso.nknu.edu.tw/services/vita.aspx?unid=1",  # another module of SSO
    "https://geo.nknu.edu.tw/highschool/index.php?temp=1",  # the department's other folder, another template
    "https://geo.nknu.edu.tw/",
    "https://elec.nknu.edu.tw/index.php",  # another department's own site
])
def test_no_other_address_has_a_title_rule(url):
    assert profiles.page_title_rules(url) == () and profiles.page_title_rule(url) is None


@pytest.mark.parametrize(("url", "site", "html", "want", "before"), [
    pytest.param(EXPERIENCE_URL, TG, experience(), "參訪心得分享", "心得發表", id="an entry of 心得發表"),
    pytest.param(CLUB_URL, TG, experience("社團成果展", "學生園地"), "社團成果展", "學生園地", id="an entry of 學生園地"),
    pytest.param(VITA_URL, DATAOPEN, vita(), "王O明", RV_ERROR, id="a vita page"),
    pytest.param(VITA_URL, ENGLISH, vita("李O華"), "李O華", RV_ERROR, id="a vita page in the export of a site that links it"),
    pytest.param(MEMBER_URL, GEO, geo_page(), "王O明", GEO_SITE, id="a member's page"),
    pytest.param(LIST_URL, GEO, geo_page("招生考試", MEMBERS), "招生考試", GEO_SITE, id="a list, never a row's name"),
])
def test_a_page_of_these_kinds_is_titled_by_its_own_cell(url, site, html, want, before, monkeypatch):
    assert _title(html, url, site) == want
    monkeypatch.setattr(profiles, "PAGE_TITLES", ())
    assert _title(html, url, site) == before  # what round one's export called it


def test_a_cell_with_no_words_leaves_the_title_to_the_next_rule_and_then_to_the_old_reading():
    no_name = MEMBER.replace("王O明", " ")
    assert _title(geo_page("專任師資", no_name), MEMBER_URL, GEO) == "專任師資"
    assert _title(geo_page(" ", no_name), MEMBER_URL, GEO) == GEO_SITE
    assert _title(geo_page("", "<p>系所簡介</p>"), MEMBER_URL, GEO) == GEO_SITE
    assert _title(experience(""), EXPERIENCE_URL, TG) == "心得發表"
    assert _title(vita(""), VITA_URL, DATAOPEN) == RV_ERROR  # a card with no name: as before, the first heading


def test_a_notices_own_title_field_still_comes_before_the_title_rules():
    """地理系's notices are its platform's detail pages (task=pageinfo with the notice's title cell): theirs first,
    though the page carries .page-header as every page does."""
    url = MEMBER_URL.replace("id=12", "id=77")
    assert _title(geo_page("最新消息", GEO_NOTICE), url, GEO) == "地理週活動公告"
    assert profiles.check_profiles() == []


# ---------------------------------------------------------------- 2. the school's answers of no data

NODATA_URL = "https://tecs.nknu.edu.tw/nodata.html"
NOT_FOUND = "很抱歉，找不到您所查詢的頁面"  # all that 师培处's nodata.html holds (the rule's own words)
FNF = ('<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=big5">\n'
       "<title>404: File not found</title>\n</head>\n<body>\n404: File not found\n</body>\n</html>\n")
CLCTS_404 = ('<html><head><title>404 ｜ 測試中心</title></head><body><header><a href="/">回首頁</a></header>'
             "<p>SCROLL DOWN</p></body></html>")
NOT_FOUND_WORDS = "学校回答没有资料（找不到您所查詢的頁面）"
FNF_WORDS = "学校回答没有资料（404: File not found）"
CLCTS_WORDS = "学校回答没有资料（404）"
ZERO_WORDS = "学校回答没有资料（搜尋結果：共0筆）"
EN_WORDS = "学校回答没有资料（英文版這一則沒有內容）"
UNIT_LIST = "https://news.nknu.edu.tw/nknu_news/lastNewsList_Dept.aspx?place=1&catid=2"
EN_NOTICE = "https://elec.nknu.edu.tw/en/news_ii.html?type=&id=7"
ZH_NOTICE = "https://elec.nknu.edu.tw/news_ii.html?type=&id=7"


def unit_list(n: int = 0) -> str:
    """A unit's list of the notice platform (made up, in its shape: the count after 「搜尋結果：」, the rows' grid)."""
    rows = "".join(f'<tr><td><a id="gvFull_ctl0{i}_linkSubject_short" href="#">測試公告 {i}</a></td></tr>'
                   for i in range(2, 2 + n))
    return ('<html><head><title>單位公告</title></head><body><form method="post" id="uForm"><div class="row">'
            f'<span>搜尋結果：共{n}筆</span></div><table id="gvFull">{rows}</table></form></body></html>')


def elec_page(block: str, path: str = "Home / Announcement / ALL / Article") -> str:
    """A page of 电机系's own site (made up, in its shape: the menus in #top and .mobile, the section's menu in #left,
    in #right the path line and then the page's own blocks, a footer)."""
    return ('<html><head><title>Announcement - 測試學系</title></head><body><div id="top"><div id="btn"><ul class="navigation">'
            '<li><a href="/news.html">系所公告選單</a></li><li><a href="/about.html">系所資訊選單</a></li></ul></div></div>'
            '<div class="mobile"><ul class="m_nav"><li><a href="/news.html">手機版選單</a></li></ul></div>'
            '<div id="content_bg"><div id="content"><div id="left"><div id="left_menu"><a href="/news.html?type=1">'
            f'獲獎公告選單</a></div></div><div id="right"><p id="path"><a href="/">{path}</a></p>{block}</div></div></div>'
            '<div id="footer_b_bg">測試學系版權所有</div></body></html>')


def elec_notice(title: str = "", text: str = " ", downloads: bool = False) -> str:
    """A notice of 电机系 (made up, in its shape: the title, the date and the views, the text, and a download block
    only when the notice has attachments). The English mirror leaves title and text empty on most."""
    files = ('<div id="ne_download"><p id="ne_download_title">Downloads</p><div id="ne_download_c"><ul><li>'
             '<p id="ne_downlaod_c_i"></p><p id="ne_download_c_t"><a href="https://elec.nknu.edu.tw/upload/item9/a.pdf">'
             "a.pdf</a></p></li></ul></div></div>") if downloads else ""
    return elec_page(f'<div id="ne_content"><p id="ne_title">{title}</p><p id="ne_list_d" style="margin-bottom:0px;">'
                     '2026/03/01</p><p id="ne_list_d" style="margin-bottom:10px;">Views:12</p>'
                     f'<div id="ne_c" class="f14bb_17">{text}</div>{files}</div>')


@pytest.mark.parametrize(("url", "html", "words"), [
    pytest.param(NODATA_URL, NOT_FOUND, NOT_FOUND_WORDS, id="师培处's nodata.html"),
    pytest.param("https://c.nknu.edu.tw/uk/nodata.html", "\ufeff" + NOT_FOUND, NOT_FOUND_WORDS, id="the template's other sites"),
    pytest.param("https://c.nknu.edu.tw/tg/wccafpsfs/album/activity/page.aspx?pn=3", FNF, FNF_WORDS, id="404 at any address"),
    pytest.param("https://c.nknu.edu.tw/tcsl/newsview.aspx?nid=9", FNF, FNF_WORDS, id="404 at a notice's address"),
    pytest.param("https://clcts.nknu.edu.tw/404.php?id=12", CLCTS_404, CLCTS_WORDS, id="语文教学中心's 404.php"),
    pytest.param(UNIT_LIST, unit_list(0), ZERO_WORDS, id="a unit's list of no row"),
    pytest.param("https://sso.nknu.edu.tw/news/lastNewsList.aspx", unit_list(0), ZERO_WORDS, id="the list at SSO's address"),
    pytest.param(EN_NOTICE, elec_notice(), EN_WORDS, id="an English notice with nothing"),
    pytest.param(EN_NOTICE, elec_notice("", "\n\t "), EN_WORDS, id="an English notice with blanks alone"),
])
def test_a_page_on_which_the_school_says_there_is_nothing_names_its_rule(url, html, words):
    rule = profiles.school_no_data(url, html)
    assert rule is not None and rule.words == words and words.startswith(export.NO_DATA)


@pytest.mark.parametrize(("url", "html"), [
    pytest.param("https://tecs.nknu.edu.tw/newsview.aspx?nid=3", f"<html><body><p>若出現「{NOT_FOUND}」請重新整理。</p></body></html>",
                 id="a notice that quotes the sentence"),
    pytest.param(NODATA_URL, "<html><body>系統維護中</body></html>", id="nodata.html saying something else"),
    pytest.param("https://c.nknu.edu.tw/tg/page.aspx?pn=3", "<html><head><title>404 教室借用說明</title></head><body>借用須知"
                 "</body></html>", id="a title that only begins with 404"),
    pytest.param("https://c.nknu.edu.tw/tg/page.aspx?pn=3", "<html><head><title>測試研究所</title></head><body>"
                 "404: File not found</body></html>", id="the words in the body alone"),
    pytest.param("https://clcts.nknu.edu.tw/news_page.php?id=4", CLCTS_404, id="语文教学中心's other addresses"),
    pytest.param("https://clcts.nknu.edu.tw/404.php", "<html><head><title>測試中心</title></head><body></body></html>",
                 id="404.php under another title"),
    pytest.param(UNIT_LIST, unit_list(3), id="a list with rows"),
    pytest.param(UNIT_LIST, unit_list(10), id="a list of ten rows (共10筆)"),
    pytest.param("https://news.nknu.edu.tw/nknu_news/lastNews.aspx?unid=1", unit_list(0), id="the count at a notice's address"),
    pytest.param(EN_NOTICE, elec_notice(downloads=True), id="an English notice that lists attachments"),
    pytest.param(EN_NOTICE, elec_notice("Seminar"), id="an English notice with a title"),
    pytest.param(EN_NOTICE, elec_notice("", "<p>Welcome.</p>"), id="an English notice with a text"),
    pytest.param(EN_NOTICE, elec_notice("", '<img src="/upload/p.jpg">'), id="an English notice with a picture"),
    pytest.param(ZH_NOTICE, elec_notice(), id="a Chinese notice (its empty areas are the detail rule's to say)"),
    pytest.param("https://elec.nknu.edu.tw/en/lab_ii.html?type=1&id=2", elec_notice(), id="another English page"),
])
def test_a_page_with_something_of_its_own_names_no_rule(url, html):
    assert profiles.school_no_data(url, html) is None


def _gone(arch) -> None:
    """On 教务处's fixture (a c.nknu-template site): the template's 「找不到」 page, two old notice addresses the school
    sends there, and a page that is the server's 404 text."""
    arch.page("nodata", BASE + "nodata.html", NOT_FOUND)
    _moved(arch, BASE + "newsview.aspx?nid=71", BASE + "nodata.html")
    _moved(arch, BASE + "newsview.aspx?nid=72", BASE + "nodata.html")
    arch.page("fnf", BASE + "wccafpsfs/page.aspx?pn=3", FNF)


def test_the_rows_of_such_pages_say_so_in_what_they_lack_and_keep_their_judgement(tmp_path):
    arch = _archive(tmp_path, _gone)
    rows, readme = _exported(arch, tmp_path, SITE)
    page, sent = rows[BASE + "nodata.html"], [rows[BASE + f"newsview.aspx?nid={n}"] for n in (71, 72)]
    for r in (page, *sent):
        assert r["缺什么"].count(export.NO_DATA) == 1 and r["缺什么"].endswith(NOT_FOUND_WORDS), r
        assert r["备注"].count(export.NO_DATA_NOTE) == 1
    assert all(r["结果"] == outcome.RESULT_LABEL["redirect"] and r["本地档案"] == page["本地档案"] != "" for r in sent)
    fnf = rows[BASE + "wccafpsfs/page.aspx?pn=3"]
    assert fnf["缺什么"].count(export.NO_DATA) == 1 and fnf["缺什么"].endswith(FNF_WORDS)
    section = _section(readme)
    assert section.startswith("这 4 行") and all(f"| {url} |" in section for url in rows if export.NO_DATA in rows[url]["缺什么"])
    assert "找不到您所查詢的頁面" in section and "404: File not found" in section


def test_the_marks_change_nothing_but_what_the_rows_lack_and_their_notes(tmp_path, monkeypatch):
    """The same archive exported with the template's two rules and without them: the four rows differ in 缺什么 (the
    words added once, last) and 备注 (the note once) alone; every other row, the files and missing.csv are the same."""
    arch = _archive(tmp_path, _gone)
    on = _export_at(arch, tmp_path / "on", SITE)
    monkeypatch.setattr(profiles, "PROFILES", tuple(dataclasses.replace(p, no_data=()) if p is profiles.CNKNU else p
                                                    for p in profiles.PROFILES))
    off = _export_at(arch, tmp_path / "off", SITE)
    marked = {r["网址"]: r["缺什么"].rsplit("、", 1)[-1] for r in on.rows if export.NO_DATA in r["缺什么"]}
    assert marked == {BASE + "nodata.html": NOT_FOUND_WORDS, BASE + "newsview.aspx?nid=71": NOT_FOUND_WORDS,
                      BASE + "newsview.aspx?nid=72": NOT_FOUND_WORDS, BASE + "wccafpsfs/page.aspx?pn=3": FNF_WORDS}
    assert not [r for r in off.rows if export.NO_DATA in r["缺什么"] or export.NO_DATA_NOTE in r["备注"]]
    assert SECTION in on.readme and SECTION not in off.readme
    assert [r["网址"] for r in on.rows] == [r["网址"] for r in off.rows]
    for x, y in zip(on.rows, off.rows, strict=True):
        words = marked.get(x["网址"])
        assert {k: v for k, v in x.items() if k not in ("缺什么", "备注")} == {
            k: v for k, v in y.items() if k not in ("缺什么", "备注")}, x["网址"]
        assert x["缺什么"] == ("、".join(v for v in (y["缺什么"], words) if v) if words else y["缺什么"]), x["网址"]
        assert x["备注"].count(export.NO_DATA_NOTE) == bool(words) and _without_note(x["备注"]) == y["备注"], x["网址"]
    assert on.files == off.files and on.missing == off.missing


# ---------------------------------------------------------------- 3. 公告单位

NEWS = "sso.nknu.edu.tw/news"
NOTICE_URL = "https://sso.nknu.edu.tw/news/lastNews.aspx?UNID=" + "A1" * 20
BLANK_URL = "https://sso.nknu.edu.tw/news/lastNews.aspx?UNID=" + "B2" * 20
SENT_URL = BASE + "newsdetail.aspx?nid=900"  # a unit site's notice address the school sends to the platform's notice
SENT_AGAIN = BASE + "newsdetail.aspx?nid=901"  # a second address of that site sent to the same notice
OLD_NOTICE = NOTICE_URL + "&from=old"  # an address of the platform's own sent to the notice
UNIT_CELL = "#ctl00_phMain_lbUnitName"
NEWS_ROW = {"host": "sso.nknu.edu.tw", "site": NEWS, "owner_site": NEWS, "title": "公告中心（測試）"}


def notice(unit: str = "教務處註冊組", subject: str = "選課公告") -> str:
    """A notice of the SSO 公告中心 (made up, in its shape: the subject in the card's header, 公告單位 and 公告時間 each a
    label and a span, the text and the attachments' cell)."""
    return ('<html><head><title>公告中心（測試）</title></head><body><form method="post" id="uForm"><div class="card">'
            f'<div class="card-header py-2"><span id="ctl00_phMain_lbSubject" class="h4 font-weight-bold">{subject}</span>'
            '</div><div class="card-body border-bottom"><div class="row no-gutters mb-2"><div class="col-xs-12 col-sm-6">'
            f'<span>公告單位：</span><span id="ctl00_phMain_lbUnitName">{unit}</span></div><div class="col-xs-12 col-sm-6">'
            '<span>公告時間：</span><span id="ctl00_phMain_lbStartEndDate">2026-03-01 ~ 2026-03-31</span></div></div></div>'
            '<div class="card-body"><span id="ctl00_phMain_lbContent">請同學留意選課時程。</span>'
            '<span id="ctl00_phMain_lbFileLink"></span></div></div></form></body></html>')


def _unit(html: str, url: str, site: str) -> str:
    return export.own_unit(LexborHTMLParser(html), url, site)


@pytest.mark.parametrize(("url", "site", "html", "want"), [
    pytest.param(NOTICE_URL, NEWS, notice(), "教務處註冊組", id="a notice of the 公告中心"),
    pytest.param("https://news.nknu.edu.tw/nknu_news/lastNews.aspx?catid=1&unid=" + "C3" * 20 + "&place=2", "news.nknu.edu.tw",
                 notice("秘書室"), "秘書室", id="the same notice at 新闻网's address"),
    pytest.param("https://sso.nknu.edu.tw/news/lightNews.aspx?unid=" + "D4" * 20, NEWS, notice(" 體育室\n"), "體育室",
                 id="校園亮點, the cell's words on one line"),
    pytest.param(NOTICE_URL, SITE, notice(), "教務處註冊組", id="in the export of a site whose address lands on it"),
    pytest.param(NOTICE_URL, NEWS, notice(""), "", id="an empty cell"),
    pytest.param(NOTICE_URL, NEWS, notice("&nbsp; "), "", id="a cell of blanks"),
    pytest.param("https://sso.nknu.edu.tw/news/lastNewsList.aspx", NEWS, notice(), "", id="the cell at a list's address"),
    pytest.param(BASE + "newsdetail.aspx?nid=3", SITE, notice(), "", id="the cell on another platform's notice"),
    pytest.param("https://elecnas.nknu.edu.tw/lastNews.aspx?unid=1", "elecnas.nknu.edu.tw", notice(), "",
                 id="a site no platform describes"),
])
def test_a_notices_posting_unit_is_its_platforms_unit_cell_by_the_pages_own_address(url, site, html, want):
    assert _unit(html, url, site) == want


def test_the_manifest_has_a_thirtieth_column_for_it_explained_as_the_others_are():
    assert export.COLUMNS[-1] == "公告单位" and len(export.COLUMNS) == 30 == len(set(export.COLUMNS))
    assert export.COLUMNS[:29][-2:] == ("页别", "列表上的类别")  # the 29 of 11-09 in their places
    assert "公告單位" in export.COLUMN_HELP["公告单位"]
    assert [d.unit for d in profiles.SSO_NEWS.details] == [UNIT_CELL]
    assert {d.unit for p in profiles.PROFILES if p is not profiles.SSO_NEWS for d in p.details} == {""}


def _notices(arch) -> None:
    """The platform's site: a notice, a notice whose unit cell is empty, and an address of the platform's own the
    school sends (302) to the first; on 教务处's site two addresses sent to the first."""
    arch.page("posted", NOTICE_URL, notice(), **NEWS_ROW)
    arch.page("blank", BLANK_URL, notice("", "停水通知"), **NEWS_ROW)
    _moved(arch, OLD_NOTICE, NOTICE_URL, **NEWS_ROW)
    _moved(arch, SENT_URL, NOTICE_URL)
    _moved(arch, SENT_AGAIN, NOTICE_URL)


def test_the_export_states_the_unit_on_the_notice_and_on_a_row_that_takes_its_file(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    _notices(arch)
    arch.close()
    news, aca = _export_at(arch, tmp_path / "news", NEWS), _export_at(arch, tmp_path / "aca", SITE)
    for out in (news, aca):
        assert all(tuple(r) == export.COLUMNS for r in out.rows)
    by_url = {r["网址"]: r for r in news.rows}
    assert by_url[NOTICE_URL]["公告单位"] == "教務處註冊組" and by_url[BLANK_URL]["公告单位"] == ""
    # a redirect to a notice that is a row of the same export takes the notice's file, and its unit by the file
    # (Writer._unit_rows: the review of 11-15 found the call untested)
    old = by_url[OLD_NOTICE]
    assert old["本地档案"] == by_url[NOTICE_URL]["本地档案"] != "" and old["公告单位"] == "教務處註冊組"
    assert sorted(url for url, r in by_url.items() if r["公告单位"]) == sorted([NOTICE_URL, OLD_NOTICE])
    rows = {r["网址"]: r for r in aca.rows}
    sent, again = rows[SENT_URL], rows[SENT_AGAIN]
    # two addresses of another site sent to the notice: the first reads the page itself, the second shares its file
    assert sent["本地档案"] == again["本地档案"] != "" and "和清单里另一行是同一个档" in sent["备注"] + again["备注"]
    for r in (sent, again):
        assert r["公告单位"] == "教務處註冊組" and r["结果"] == outcome.RESULT_LABEL["redirect"], r["网址"]
    assert sorted(r["网址"] for r in aca.rows if r["公告单位"]) == sorted([SENT_URL, SENT_AGAIN])
    assert "- **公告单位**：" in news.readme and "- **公告单位**：" in aca.readme  # the README explains every column


def test_the_content_stages_page_version_does_not_read_the_unit_cell():
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    without = tuple(dataclasses.replace(p, details=tuple(dataclasses.replace(d, unit="") for d in p.details))
                    for p in profiles.PROFILES)
    assert pagetext.page_version(without) == pagetext.page_version(profiles.PROFILES)


# ---------------------------------------------------------------- 4. content areas

ELEC_AREA, VITA_AREA = "#right > :not(#path)", "#ctl00_phMain_divVita"
OLD_PAGE_VERSION = "pages-4:a3e5112274"  # the content stage's page version from 11-09 until these two areas
COURSE_TABLE = "https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=1"
ELEC_HOME = '<html><body><div id="top"><a href="/news.html">系所公告選單</a></div><div class="row index_news_box">最新公告</div></body></html>'
MENUS = ("系所公告選單", "系所資訊選單", "手機版選單", "獲獎公告選單", "Home / Announcement", "版權所有")


def test_the_two_platforms_name_their_content_area():
    assert profiles.content_css(ZH_NOTICE) == profiles.content_css(EN_NOTICE) == ELEC_AREA
    assert profiles.content_css(VITA_URL) == profiles.content_css(COURSE_TABLE) == VITA_AREA
    assert profiles.check_profiles() == []


def test_the_area_of_a_page_of_dianji_is_its_right_column_without_the_path_line():
    tree = LexborHTMLParser(elec_notice("研討會公告", "<p>歡迎參加。</p>", downloads=True))
    got = " ".join(n.text(separator=" ", strip=True) for n in tree.css(ELEC_AREA))
    assert all(x in got for x in ("研討會公告", "歡迎參加。", "2026/03/01", "a.pdf")) and not [m for m in MENUS if m in got]
    other = LexborHTMLParser(elec_page('<div id="la_content">實驗室簡介</div><div class="manu">設備清單</div><p>備註</p>'))
    assert [n.text(strip=True) for n in other.css(ELEC_AREA)] == ["實驗室簡介", "設備清單", "備註"]  # each block of the column
    assert LexborHTMLParser(ELEC_HOME).css(ELEC_AREA) == []  # the home page has no right column: no area


def test_the_content_stage_reads_the_area_and_neither_the_menus_nor_the_hidden_messages():
    """nknu.totext.pagetext, read only here: what it reads a page's text from (page_roots) once the profiles name the
    areas. A page of the two sites with no such area reads as before, its body."""
    pagetext = pytest.importorskip("nknu.totext.pagetext")

    def words(html: str, url: str) -> str:
        return " ".join(n.text(separator=" ", strip=True) for n in pagetext.page_roots(LexborHTMLParser(html), url))

    got = words(elec_notice("研討會公告", "<p>歡迎參加。</p>"), ZH_NOTICE)
    assert "研討會公告" in got and "歡迎參加。" in got and not [m for m in MENUS if m in got]
    got = words(vita(), VITA_URL)
    assert "王O明" in got and "測試大學教育學系" in got and RV_ERROR not in got and "Browser not supported" not in got
    table = '<html><body><form id="uForm"><fieldset><legend>系必修</legend>測試課程甲</fieldset></form></body></html>'
    assert "測試課程甲" in words(table, COURSE_TABLE)
    assert "最新公告" in words(ELEC_HOME, "https://elec.nknu.edu.tw/")


def test_the_crawlers_own_measure_finds_the_area_and_calls_no_english_notice_empty():
    """What the crawler will read once its code is the new one (the lab's is 87f770b still; nothing is judged again
    now): an English notice with no title and no text still holds its date in the area, so the empty-area sign (页面
    几乎是空的, 要人看) is not raised for the 574 of them; the links of the area are the body's, the menus' chrome."""
    css = profiles.content_css(EN_NOTICE)
    m = pagetypes.measure(elec_notice(), content_css=css)
    assert m.content_found and not m.content_empty
    assert not pagetypes.measure(ELEC_HOME, content_css=css).content_found
    zones = links.extract(elec_notice("研討會公告", "<p>歡迎參加。</p>", downloads=True), ZH_NOTICE, css).link_zone
    assert zones == {"https://elec.nknu.edu.tw/upload/item9/a.pdf": "body", "/news.html": "chrome", "/about.html": "chrome",
                     "/news.html?type=1": "chrome", "/": "chrome"}  # by the href as written; the path line's link is chrome


def test_the_page_version_changes_by_these_two_areas_alone():
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    before = tuple(dataclasses.replace(p, content_css=None) if p is profiles.ELEC or p is profiles.DATAOPEN else p
                   for p in profiles.PROFILES)
    assert pagetext.page_version(before) == OLD_PAGE_VERSION
    assert pagetext.page_version(profiles.PROFILES) == pagetext.PAGE_VERSION != OLD_PAGE_VERSION
