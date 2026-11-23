"""A notice's date per platform (P75 of docs/crawler-plan.md; the step-3 design §四 row 3, the owner 11-08 【用户】; how
it is done is Claude's): the export reads a notice's own date field first (profiles.Detail.date, the platform chosen
by the page's own address, the date read alone when no label stands before it, the label's words kept when the field's
parent, or the element above, states the same day), then the date the notice's row states on its list (the rows of the
site's list pages and of the pages their pager brought, read as the crawler read them: a press's row by its row_key, a
GET notice by its page_res item entry or by the row's plain link), then the first labelled date in its words. Made-up
pages of each platform's template (11-08: read on round one's pages, no school text copied; the one name is O-masked)."""

from __future__ import annotations

import dataclasses

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import export, postback, profiles
from tests.archive.test_export_1027 import (
    BASE,
    HTML,
    SITE,
    _action,
    _add,
    _export,
    _res,
    by_url,
    make_archive,
    rows_of,
)
from tests.archive.test_export_1102 import NOTICE, NOTICE_URL

WEB = "sso.nknu.edu.tw/web/592"
SSO = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx="
MORE = SSO + "592&selpage=MoreNews"


def _date(html: str, url: str, site: str):
    return export.own_date(LexborHTMLParser(html), url, site)


# ---------------------------------------------------------------- the platforms' date fields


def webidx_detail(meta: str = "2026-05-21", title: str = "【公告】宿舍申請說明",
                  body: str = "<p>說明會日期：115年6月9日，王O明同學主持。</p>") -> str:
    """An SSO WebIdx notice (GET newsidx= and a row's press bring the same template): the date after a calendar icon,
    the views after an eye."""
    return (f'<html><body><div class="news-detail-view"><div class="detail-header"><h3>{title}</h3>'
            '<div class="detail-meta"><span><i class="fa-regular fa-calendar"></i>\n'
            f'                        {meta}</span><span><i class="fa-regular fa-eye"></i> 4817</span></div></div>'
            f'<hr><div class="detail-body">{body}</div></div></body></html>')


def test_a_webidx_notice_is_dated_by_the_date_beside_its_calendar_icon():
    url = MORE + "&newsidx=1264"
    assert _date(webidx_detail(), url, WEB) == ("2026-05-21", "2026-05-21")
    # its words would have given the meeting's day, after the bare label 日期
    words = export.page_texts(LexborHTMLParser(webidx_detail()), WEB)
    assert next(got for got in map(export.find_date, words) if got)[0] == "2026-06-09"
    # a press brings the same page at its list's address, which no detail path names: dated by its row instead
    assert _date(webidx_detail(), MORE + "&sort=date&pagenum=3", WEB) is None
    # a notice the school deleted: its fields empty
    assert _date(webidx_detail(meta="", title="", body=""), SSO + "31&selpage=MoreNews&newsidx=1281",
                 "sso.nknu.edu.tw/web/31") is None


def test_a_page_is_read_with_the_platform_its_own_address_names():
    """图资处's notices are SSO WebIdx pages (lis's own profile is its old Joomla site's), and so are the /site/ keys'."""
    url = SSO + "53&selpage=MoreNews&newsidx=2783"
    for owner in ("lis.nknu.edu.tw", "sso.nknu.edu.tw/site/100061"):
        tree = LexborHTMLParser(webidx_detail(meta="2026-11-03", title="資料庫試用公告"))
        # the title above the date ends in 公告: no label of the date's (the meta holds the views too)
        assert export.own_date(tree, url, owner) == ("2026-11-03", "2026-11-03"), owner
        assert export.page_title(tree, url, owner) == "資料庫試用公告"  # the notice's h3, not the page's first h2
        assert export.empty_detail_area(LexborHTMLParser(webidx_detail(meta="", title="", body="")), url, owner) == \
            export.EMPTY_AREA


SSO_NEWS_PAGE = """<html><body><div class="row no-gutters mb-2">
<div class="col-xs-12 col-sm-6"><span>公告單位：
                        </span><span>
                            <span id="ctl00_phMain_lbUnitName">教務處</span></span></div>
<div class="col-xs-12 col-sm-6"><span>公告時間：
                        </span>
                        <span>
                            <span id="ctl00_phMain_lbStartEndDate">2026-09-08 ~ 2026-12-31</span>
                        </span></div></div>
<span id="ctl00_phMain_lbSubject">寒假宿舍申請</span>
<span id="ctl00_phMain_lbContent">日期： 民國115年12月19日起受理。</span></body></html>"""


@pytest.mark.parametrize("url", [
    "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=9B449AA47D86DF9A4C8C5E6A951C0246AC386A72",
    "https://sso.nknu.edu.tw/news/lightnews.aspx?unid=8645814E2EA48B438FFC8348798003691BC5DC21",  # 校園亮點
    "https://news.nknu.edu.tw/nknu_news/lastnews.aspx?catid=SCHOOLADMIN&unid=47764F578B&place=nknu",  # the mirror
])
def test_an_sso_news_notice_is_dated_by_the_start_of_its_display_period(url):
    # the label 公告時間 is two levels above the field, about 25 white-space characters before the date
    site = "news.nknu.edu.tw" if "news.nknu" in url else "sso.nknu.edu.tw/news"
    assert _date(SSO_NEWS_PAGE, url, site) == ("2026-09-08", "公告時間： 2026-09-08")
    assert export.page_title(LexborHTMLParser(SSO_NEWS_PAGE), url, site) == "寒假宿舍申請"


AFFAIR = """<html><body><div align="left"><div class="post-info">
<strong><span id="DataFormView_AddTimeLabel">2026/09/07</span><b>&nbsp;</b></strong>
<h2><span id="DataFormView_TitleLabel">【公告】交通車時刻表（適用日期：115年9月5日起）</span></h2></div>
<span id="DataFormView_ContentsLabel">交通車時刻如附件。</span></div></body></html>"""
TECS = """<html><body><div class="post-info"><h2><span id="TitleLabel">碩士班榜單公告115年12月30日</span></h2>
<span id="DeptLabel">地方教育輔導組</span> 公告日期：<span id="AddTimeLabel">2026/11/2</span></div>
<span id="ContentsLabel">榜單如附件。</span><span id="FileLinkLabel"><a href="/upload/a.pdf">榜單</a></span>
</body></html>"""
REALITY = """<html><body><div id="DataFormView"><table><tr><td>日　　期</td><td>
<span id="DataFormView_AddTimeLabel">2026/10/2 上午 12:00:00</span></td></tr></table>
<span id="DataFormView_TitleLabel">實境課程說明</span><span id="DataFormView_ContentsLabel">課程說明。</span></div>
</body></html>"""


def test_a_cnknu_newsview_notice_is_dated_by_its_field_alone_or_with_its_label():
    view = "https://c.nknu.edu.tw/affair/newsview.aspx?nid=3010"
    # 总务处: the date alone; the label above it (適用日期 in the title) states another day: the field's own words
    assert _date(AFFAIR, view, "c.nknu.edu.tw/affair") == ("2026-09-07", "2026/09/07")
    # tecs: no DataFormView_ prefix; its parent holds the title's 「榜單公告115年12月30日」 first, then the field's label
    tecs = "https://tecs.nknu.edu.tw/newsview.aspx?kind=id&nid=7555"
    assert _date(TECS, tecs, "tecs.nknu.edu.tw") == ("2026-11-02", "公告日期：2026/11/2")
    assert export.page_title(LexborHTMLParser(TECS), tecs, "tecs.nknu.edu.tw") == "碩士班榜單公告115年12月30日"
    assert export.empty_detail_area(LexborHTMLParser(TECS), tecs, "tecs.nknu.edu.tw") == ""  # was NO_AREA
    # reality: the label 「日　　期」 in the cell beside the field's
    assert _date(REALITY, "https://c.nknu.edu.tw/reality/newsview.aspx?nid=88", "c.nknu.edu.tw/reality") == \
        ("2026-10-02", "日 期 2026/10/2")


def test_the_newsdetail_field_reads_as_before():
    assert export.own_date(LexborHTMLParser(NOTICE), NOTICE_URL, SITE) == ("2026-10-22", "公告日期：2026/10/22")


OIA = """<html><body><div class="page-content"><div class="d-flex align-items-center"><span class="badge">
<i class="fa-solid fa-hashtag"></i>校園新聞</span><div class="post-date align-items-center">2026-09-01   </div></div>
<h2 class="post-title">交換學生說明會</h2><div class="post-content"><p>日期： 2026 年 11 月 17 日</p></div></div>
</body></html>"""
EE = """<html><body><div id="ab_c"><p id="ne_date">2023-09-01</p><p id="ne_title">恭賀本系同學獲獎</p>
<div id="ne_c"><p>得獎同學：王O明。</p></div></div></body></html>"""
ELEC = """<html><body><div id="ne_content"><p id="ne_title">加退選說明</p>
<p id="ne_list_d" style="margin-bottom:20px;">2026/05/08 </p><p id="ne_list_d">觀看次數:655</p>
<div id="ne_c"><p>日期：115 年 5 月18日 前完成。</p></div><div id="ne_download_c"><a href="/up/a.pdf">附件</a></div>
</div></body></html>"""
ENGLISH = """<html><body><div class="news_t01"><p>2026-05-12</p><h3>試教日期公告</h3></div>
<div class="edit01"><p>日期：115年11月19日</p></div></body></html>"""
CLCTS = """<html><body><div class="page_contain"><div class="badge news-type-badge">華語</div>
<div class="badge news-badge">公告</div>
<span class="list-date">2026.10.14</span>
<h1 class="settitle">停課公告</h1><p>因颱風停課一天。</p></div></body></html>"""
WORDPRESS = """<html><body><div class="blog_single blog_holder"><article><div class="post_text_inner">
<h2 itemprop="name" class="entry_title"><span itemprop="dateCreated" class="date entry_date updated">30 6 月<meta
itemprop="interactionCount" content="UserComments: 0"></span> 【學士班公告】轉學考面試時間表</h2>
<p>面試日期：115年7月6日</p></div></article></div></body></html>"""
GEO = """<html><body><div class="newslist2"><dl><dt><span class="newsitem item2">學術活動</span>2026-10-13</dt>
<dd class="title">【工作坊】地理資訊系統研習</dd></dl></div><div class="pbox1"><p>日期：114年3月29日</p></div>
</body></html>"""
ID = """<html><body><div class="title_block"><h1>證照獎勵申請</h1><ul>
<li class="d-none"><a href="category/link1LP95JGZ"><i class="far fa-folder"></i> 行政</a></li>
<li><i class="far fa-clock"></i> 2026年10月01日</li></ul></div>
<div id="article"><div class="blog_content"><p>日期：2026/04/11 截止</p></div></div></body></html>"""


@pytest.mark.parametrize(("html", "url", "site", "want"), [
    (OIA, "https://oia.nknu.edu.tw/news-detail.php?id=473", "oia.nknu.edu.tw", ("2026-09-01", "2026-09-01")),
    (EE, "https://ee.nknu.edu.tw/news_ii.html?getId=34", "ee.nknu.edu.tw", ("2023-09-01", "2023-09-01")),
    # the first of the two fields; the parent's labelled date (the body's deadline) is another day's
    (ELEC, "https://elec.nknu.edu.tw/news_ii.html?type=&id=523", "elec.nknu.edu.tw", ("2026-05-08", "2026/05/08")),
    (ENGLISH, "https://english.nknu.edu.tw/tw/news_ii.html?nID=236", "english.nknu.edu.tw",
     ("2026-05-12", "2026-05-12")),
    # the type badge 公告 right before the date: the label's words kept
    (CLCTS, "https://clcts.nknu.edu.tw/news_page.php?id=1s31-ebYuJ4rf4MifXHnZQ", "clcts.nknu.edu.tw",
     ("2026-10-14", "公告 2026.10.14")),
    # the day and the month in the field, the year in the address
    (WORDPRESS, "https://vd.nknu.edu.tw/2026/06/30/%E3%80%90%E5%AD%B8%E5%A3%AB%E7%8F%AD/", "vd.nknu.edu.tw",
     ("2026-06-30", "30 6 月（网址 /2026/06/30/）")),
    (GEO, "https://geo.nknu.edu.tw/index.php?option=module&lang=cht&task=pageinfo&id=990&index=1",
     "geo.nknu.edu.tw", ("2026-10-13", "2026-10-13")),
    (ID, "https://id.nknu.edu.tw/linkpw4imkw5/11510-2-11-30", "id.nknu.edu.tw", ("2026-10-01", "2026年10月01日")),
])
def test_the_self_built_sites_notices_are_dated_by_their_own_fields(html, url, site, want):
    assert _date(html, url, site) == want
    assert export.empty_detail_area(LexborHTMLParser(html), url, site) == ""


@pytest.mark.parametrize(("html", "url", "site"), [
    # the field and the address disagree: no date from them
    (WORDPRESS.replace("30 6 月", "29 6 月"), "https://vd.nknu.edu.tw/2026/06/30/abc/", "vd.nknu.edu.tw"),
    (WORDPRESS, "https://vd.nknu.edu.tw/news/page/2/", "vd.nknu.edu.tw"),  # a list, no post
    (ID, "https://id.nknu.edu.tw/linkpw4imkw5/page/2", "id.nknu.edu.tw"),  # a list
    (ELEC, "https://elec.nknu.edu.tw/en/news_ii.html?type=&id=523", "elec.nknu.edu.tw"),  # the English mirror
    (ELEC.replace("2026/05/08", "0000/00/00"), "https://elec.nknu.edu.tw/news_ii.html?id=101", "elec.nknu.edu.tw"),
    (OIA, "https://oia.nknu.edu.tw/gallery-detail.php?id=12", "oia.nknu.edu.tw"),  # an album
])
def test_no_date_where_the_field_states_none(html, url, site):
    assert _date(html, url, site) is None


def test_the_platforms_declare_their_date_fields():
    dated = {p.name: [d for d in p.details if d.date] for p in profiles.PROFILES}
    for name in ("sso_news", "sso_webidx", "cnknu", "oia", "ee", "elec", "english", "clcts", "wordpress", "geo", "id"):
        assert dated[name], name
    assert not dated["sso_clv2"] and not dated["lis"]  # no notice page of theirs in round one
    wp, = profiles.WORDPRESS.details
    assert wp.date_path and not wp.title  # the h2 holds the date too
    # geo and id call no page a detail for the content stage: their address is a static page's too
    assert not profiles.GEO.detail_paths and not profiles.ID.detail_paths
    assert profiles.profile_for("https://geo.nknu.edu.tw/index.php") is profiles.GEO
    assert profiles.profile_for("https://id.nknu.edu.tw/linkpw4imkw5/x") is profiles.ID
    assert profiles.check_profiles() == []


def test_a_date_path_without_its_groups_is_a_mistake_in_the_profiles(monkeypatch):
    bad = dataclasses.replace(profiles.WORDPRESS, details=(dataclasses.replace(
        profiles.WORDPRESS.details[0], date_path=r"^/(\d{4})/(\d{2})/(\d{2})/"),))
    monkeypatch.setattr(profiles, "PROFILES", tuple(bad if p is profiles.WORDPRESS else p for p in profiles.PROFILES))
    assert profiles.check_profiles() == [f"wordpress: {bad.details[0].date_path}: a date_path names its groups y, m and d"]


@pytest.mark.parametrize(("text", "want"), [
    ("2024-08-15", ("2024-08-15", "2024-08-15")),
    ("最新消息2026-03-10", ("2026-03-10", "2026-03-10")),
    ("2026-10-22 ~ 2026-11-06", ("2026-10-22", "2026-10-22")),  # the first
    ("115年01月01日 - 115年12月18日", ("2026-01-01", "115年01月01日")),  # 民国
    ("2026.10.22", ("2026-10-22", "2026.10.22")),
    ("2026 / 10 / 2 上午 12:00:00", ("2026-10-02", "2026 / 10 / 2")),
    ("民國114年10月1日", ("2025-10-01", "民國114年10月1日")),
    ("２０２５／１０／０１", ("2025-10-01", "２０２５／１０／０１")),
    ("0000/00/00", None),  # a date that does not exist
    ("1900-01-01", None),  # outside YEARS
    ("2025/2/30", None),
    ("2025/10-01", None),  # two marks
    ("12026-05-21", None),  # inside a longer number
    ("觀看次數:655", None),
    ("30 6 月", None),  # no year
    ("", None),
])
def test_bare_date(text, want):
    assert export.bare_date(text) == want


# ---------------------------------------------------------------- the list rows' dates


def _row(key: str, date: str) -> postback.Row:
    return postback.Row(key, "標題", date, "", None)


def test_list_date_takes_the_date_the_rows_agree_on_else_the_most_else_the_earliest():
    assert export.list_date([(1, _row("k1", "2026.10.22")), (2, _row("k9", "2026/10/22"))]) == \
        ("2026-10-22", "2026.10.22", "")
    # a list's row on each of its pages (a pinned row) is one row
    assert export.list_date([(1, _row("k1", "2025-01-05")), (1, _row("k1", "2025-01-05")),
                             (2, _row("k2", "2025-01-02"))])[0] == "2025-01-02"  # one each: the earliest
    day, raw, note = export.list_date([(1, _row("k1", "2025-01-05")), (2, _row("k2", "2025-01-05")),
                                       (3, _row("k3", "2025-01-02"))])
    assert (day, raw) == ("2025-01-05", "2025-01-05")
    assert note == "列表上有 3 行指到它，写的日期不一样：2025-01-02（1 行）、2025-01-05（2 行），取多数的"
    _day, _raw, note = export.list_date([(1, _row("k1", "2025-01-05")), (2, _row("k2", "2025-01-02"))])
    assert note.endswith("几个日期一样多，取最早的")
    # a row with no date, or none that can be read, gives none
    assert export.list_date([(1, _row("k1", "")), (2, _row("k2", "置頂"))]) is None
    assert export.list_date([(1, _row("k1", "")), (2, _row("k2", "2011-09-27 12:00 ~ 2011-09-27 01:30"))]) == \
        ("2011-09-27", "2011-09-27 12:00 ~ 2011-09-27 01:30", "")


def _web_list(rows: list[tuple[str, str, str]]) -> str:
    """A WebIdx MoreNews page: (date, title, how the title links: a postback control or a plain href)."""
    out = []
    for date, title, link in rows:
        href = f"javascript:__doPostBack('{link}','')" if link.startswith("ucNews") else link
        out.append(f'<div class="news-row"><div class="col-date">{date}</div><div class="col-cat">公告</div>'
                   f'<div class="col-title"><a href="{href}">{title}</a></div></div>')
    return ('<html><body><form id="form1" method="post"><div class="news-list-wrapper">' + "".join(out)
            + "</div></form></body></html>")


LIST_ROWS = [("2026-05-21", "宿舍申請說明", "ucNews$rptNews$ctl00$lnkShowDetail"),
             ("2026-04-01", "獎學金公告", "ucNews$rptNews$ctl01$lnkShowDetail"),
             ("2026-03-03", "交換生說明會", "default.aspx?webidx=592&selpage=MoreNews&newsidx=1300")]


def _webidx_site(arch) -> dict:
    """生活輔導組's WebIdx site in the fixture archive: its MoreNews list, two notices its rows' presses brought (the
    answers at the list's address) and one got by GET from a row's plain link (its page_res item entry), each
    answer's own date field one day off its row's but the first's."""
    f = arch.f
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (WEB, "生活輔導組", "全校重要", "1", "sso_webidx", MORE))
    sso = {"host": "sso.nknu.edu.tw", "site": WEB, "owner_site": WEB}
    page = _web_list(LIST_ROWS)
    lst = arch.page("web_list", MORE, page, page_role="list", **sso)
    pr = postback.read_page(profiles.SSO_WEBIDX, MORE, page, site=WEB, list_url=MORE)
    keys = {r.title: r.key for r in pr.rows}
    assert [p.row_key for p in pr.presses] == [keys["宿舍申請說明"], keys["獎學金公告"]]  # the crawler's own keys
    for title, meta in (("宿舍申請說明", "2026-05-21"), ("獎學金公告", "")):  # the second's field is empty
        body = webidx_detail(meta=meta, title=title).encode()
        off, sha = arch.put(f"press:{title}", MORE, HTML, body)
        arch.ids[title] = _action(f, lst, kind="detail", step=1, row_key=keys[title], row_text=title, host=sso["host"],
                                  site=WEB, target="ucNews$rptNews$ctl00$lnkShowDetail", profile="sso_webidx/webidx_news",
                                  sniff="html", ctype=HTML, bytes=len(body), sha256=sha, warc=arch.rel,
                                  warc_offset=off)
        _res(f, lst, f"pb:detail:{keys[title]}", "detail", how="postback", action_id=arch.ids[title], text=title)
    got = arch.page("web_get", MORE + "&newsidx=1300", webidx_detail(meta="2026-03-02", title="交換生說明會"), **sso)
    _res(f, lst, f"item:{keys['交換生說明會']}", "item", need="no", why="item", res_id=got)
    return keys


def test_a_webidx_notice_a_rows_press_brought_is_dated_by_its_row(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    keys = _webidx_site(arch)
    arch.close()
    export.export_site(arch.root, WEB, tmp_path / "web")
    rows = by_url(rows_of(tmp_path / "web" / "manifest.csv"))
    first, second = rows[MORE + "#按钮=宿舍申請說明"], rows[MORE + "#按钮=獎學金公告"]
    assert (first["网页上写的日期"], first["日期原文"], first["日期来源"]) == ("2026-05-21", "2026-05-21", export.LIST_ROW)
    assert first["按钮编号"] == keys["宿舍申請說明"]
    assert (second["网页上写的日期"], second["日期来源"]) == ("2026-04-01", export.LIST_ROW)  # its field is empty
    got = rows[MORE + "&newsidx=1300"]
    assert (got["网页上写的日期"], got["日期来源"]) == ("2026-03-02", export.OWN_FIELD)  # its field before its row
    assert got["标题"] == "交換生說明會"
    assert rows[MORE]["网页上写的日期"] == ""  # the list itself
    readme = (tmp_path / "web" / "README.md").read_text(encoding="utf-8")
    assert export.LIST_ROW in readme and "#按钮=" in readme


# ---------------------------------------------------------------- c.nknu: a deleted notice, rows that disagree, a link item


BLANK_VIEW = """<html><head><title>國立高雄師範大學教務處</title></head><body><div id="DataFormView">
<strong><span id="DataFormView_AddTimeLabel"></span></strong><span id="DataFormView_TitleLabel"></span>
<span id="DataFormView_ContentsLabel"></span></div></body></html>"""


def _cnknu_list(rows: list[tuple[str, str, str]]) -> str:
    """A c.nknu MasterGrid list: (date, link, title) each row; no date selector, read by the row's first date."""
    trs = "".join(f'<tr><td>{d}</td><td><a href="{href}">{t}</a></td></tr>' for d, href, t in rows)
    return ('<html><head><title>國立高雄師範大學教務處</title></head><body><h2>最新消息</h2><table id="MasterGrid">'
            f"<tr><th>日期</th><th>主題</th></tr>{trs}</table></body></html>")


def _lists(arch, *dates: str, link: str = "newsview.aspx?nid=7") -> None:
    for i, d in enumerate(dates):
        arch.page(f"list{i}", BASE + f"newslist.aspx?pclass={i + 10}", _cnknu_list([(d, link, "交通車時刻表")]),
                  page_role="list")


def test_a_notice_the_school_deleted_takes_its_rows_date(tmp_path):
    """总务处's 21 newsview notices of round one: the template with empty fields, the notice still on its list."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("blank", BASE + "newsview.aspx?nid=7", BLANK_VIEW)
    _lists(arch, "2025.05.08")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    row = rows[BASE + "newsview.aspx?nid=7"]
    assert (row["网页上写的日期"], row["日期原文"], row["日期来源"]) == ("2025-05-08", "2025.05.08", export.LIST_ROW)
    assert "写的日期不一样" not in row["备注"]


@pytest.mark.parametrize(("dates", "day", "how"), [
    (("2025/05/09", "2025/05/08"), "2025-05-08", "几个日期一样多，取最早的"),
    (("2025/05/09", "2025/05/08", "2025/05/09"), "2025-05-09", "取多数的"),
])
def test_rows_that_disagree_give_the_most_dates_else_the_earliest_and_say_so(tmp_path, dates, day, how):
    arch = make_archive(tmp_path / "archive").open()
    arch.page("blank", BASE + "newsview.aspx?nid=7", BLANK_VIEW)
    _lists(arch, *dates)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    row = rows[BASE + "newsview.aspx?nid=7"]
    assert (row["网页上写的日期"], row["日期来源"]) == (day, export.LIST_ROW)
    assert f"列表上有 {len(dates)} 行指到它，写的日期不一样" in row["备注"] and how in row["备注"]


def _redirect(arch, url: str, to: str) -> int:
    off, sha = arch.put(url, url, "text/html", b"moved", headers={"location": to}, status=302)
    return _add(arch.f, url=url, final_url=to, status=302, bytes=5, sha256=sha, warc=arch.rel, warc_offset=off,
                ctype="text/html", sniff="html", page_type="t:ok")


def test_a_listed_link_item_takes_its_own_rows_date_unless_its_landing_page_has_a_date_field(tmp_path):
    """c.nknu's link items (a newsdetail that redirects): the landing page's date field first, else the date the
    redirect's own row states, never another page's words or row."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("notice", NOTICE_URL, NOTICE)  # its own field: 2026-10-22
    _redirect(arch, BASE + "newsdetail.aspx?nid=20", BASE + "about.aspx")  # about.aspx states no date
    _redirect(arch, BASE + "newsdetail.aspx?nid=21", NOTICE_URL)
    arch.page("links", BASE + "newslist.aspx?pclass=20", _cnknu_list(
        [("2025/03/01", "newsdetail.aspx?nid=20", "校外連結"), ("2025/03/02", "newsdetail.aspx?nid=21", "放榜")]),
        page_role="list")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    link, onto = rows[BASE + "newsdetail.aspx?nid=20"], rows[BASE + "newsdetail.aspx?nid=21"]
    assert (link["网页上写的日期"], link["日期来源"]) == ("2025-03-01", export.LIST_ROW)
    assert link["本地档案"] == rows[BASE + "about.aspx"]["本地档案"] and rows[BASE + "about.aspx"]["网页上写的日期"] == ""
    assert (onto["网页上写的日期"], onto["日期来源"]) == ("2026-10-22", export.OWN_FIELD)  # the landing page's field


def test_a_page_no_list_row_points_to_is_dated_by_its_words(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    arch.page("blank", BASE + "newsview.aspx?nid=7", BLANK_VIEW.replace(
        '<span id="DataFormView_ContentsLabel"></span>', '<span id="DataFormView_ContentsLabel">公告日期：114/05/08</span>'))
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    row = rows[BASE + "newsview.aspx?nid=7"]
    assert (row["网页上写的日期"], row["日期原文"], row["日期来源"]) == ("2025-05-08", "公告日期：114/05/08",
                                                                    export.PAGE_WORDS)


def test_a_list_page_that_cannot_be_read_stops_nothing(tmp_path, monkeypatch):
    arch = make_archive(tmp_path / "archive").open()
    arch.page("blank", BASE + "newsview.aspx?nid=7", BLANK_VIEW)
    _lists(arch, "2025.05.08")
    arch.close()

    def broken(*_a, **_kw):
        raise ValueError("a page no reader expected")

    monkeypatch.setattr(export.postback, "read_page", broken)
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[BASE + "newsview.aspx?nid=7"]["网页上写的日期"] == ""
    assert "读这一页列表上的行出错（ValueError）" in rows[BASE + "newslist.aspx?pclass=10"]["备注"]
