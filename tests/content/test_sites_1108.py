"""Plan 2026-11-08 T3 (spec 2026-11-08 §二 2 and 6): notices, lists, dates and units per site. A notice is a profiled
detail page of any platform, and c.nknu's newsdetail and newsview; list pages and pager rows are left out and counted;
a notice no date field dates, and a page, take the date the export read on the page (网页上写的日期) with that source;
every document's unit comes from the site table (config/site_units.yaml) by its own URL's site, else its export's.
The pages are made up in the shape of each template (tests/totext/test_pages_1108.py's, from the four pilot sites'
11-08 exports, read only); the people in them are O masks. No model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nknu.content import ids, sites
from nknu.content.build import build_db, build_sites
from nknu.content.corpus import Event, PageMeta, Record, load_calendar, read_metas, read_page_meta
from nknu.content.documents import build_documents, kind_class
from nknu.content.units import load_units
from nknu.retrieval.respond import read_response
from nknu.settings import REPO_ROOT
from tests.content import test_assemble as ta
from tests.content import test_build as tb
from tests.totext import test_pages_1108 as tp

ACA = "https://c.nknu.edu.tw/aca/"
CCEE = "https://c.nknu.edu.tw/ccee/"
REALITY = "https://c.nknu.edu.tw/reality/"
WEB = tp.WEB  # sso.nknu.edu.tw/web/595, 生活輔導組
CLV2 = tp.CLV2  # sso.nknu.edu.tw/site/100061, 校安中心
SCHOOL = "國立高雄師範大學"
TODAY = date(2026, 11, 8)
# an SSO file service's address in a generic form (site key sso.nknu.edu.tw/files, not in the table, like the real
# service's): the crawler's spelling (an IIS host: lower case) and how a page writes it
SSO_FILE = "https://sso.nknu.edu.tw/files/download.aspx?path=O"
SSO_FILE_HREF = "/Files/Download.aspx?path=O"
# the dates the export read on a page (网页上写的日期, 日期原文), as reality's 16 recordview pages show theirs
UPDATED, UPDATED_RAW = "2025-09-17", "更新時間： 2025.9.17"


def rec(rid: str, kind: str, url: str, title: str = "", text: str = "", linked=(), **more) -> Record:
    return Record(rid, kind, url, f"pages/{rid}.html", title, text or title, "", tuple(linked), **more)


def by_id(docs) -> dict:
    return {d.doc_id: d for d in docs}


# ---------------------------------------------------------------- kinds

@pytest.mark.parametrize(("kind", "cls"), [
    # notices: c.nknu's two templates, and the detail pages of every profiled platform, a press-made one too
    ("newsdetail", "notice"), ("newsview", "notice"), ("detail:sso_webidx", "notice"), ("detail:sso_news", "notice"),
    ("detail:sso_webidx#按钮", "notice"), ("detail:sso_clv2#按钮", "notice"),
    # left out: lists read from the page, pager rows, c.nknu's names as before, menu-only images
    ("list:sso_webidx", None), ("list:sso_clv2", None), ("list:sso_news", None), ("list:sso_webidx#翻页", None),
    ("list:sso_news#翻页", None), ("newslist", None), ("album#翻页/按钮", None), ("album_images", None),
    ("只在选单出现的图片", None),
    # pages: profiled pages that show no list, unprofiled school pages, c.nknu's other names
    ("page:sso_webidx", "page"), ("page:sso_clv2", "page"), ("page:lis", "page"), ("page", "page"),
    ("page_single", "page"), ("default", "page"), ("静态:spaper", "page"), ("recordview", "page"),
    ("附件:pdf:有文字层", "attachment"), ("附件:图片", "attachment"), ("外站:forms.gle", "link"),
])
def test_kind_classes_of_every_platform(kind, cls):
    assert kind_class(kind) == cls


# ---------------------------------------------------------------- the site table

SITE_PROBE = REPO_ROOT / "research" / "site_probe_2026-10-23" / "data.json"  # the unit tree's list (git-ignored)


def test_the_site_table_has_a_row_per_round_one_site_key():
    table = sites.table()
    round1 = json.loads((REPO_ROOT / "config" / "archive_round1.json").read_text(encoding="utf-8"))
    keys = [s["site"] for s in round1["sites"] if str(s.get("round")) == "1"]
    # archive_round1.json's extra seeds (7 SSO pages of round 1, on 4 site keys the crawl's 111 keys hold)
    extra = {"sso.nknu.edu.tw/recruit", "sso.nknu.edu.tw/staff", "sso.nknu.edu.tw/unit", "sso.nknu.edu.tw/studentprofile"}
    # 11-17: and the round-two sites the crawler handed over (research/step3_2026-11-08/site_units.py ROUND2_IN), each
    # a site of round two in the list
    round2 = {s["site"] for s in round1["sites"] if str(s.get("round")) == "2"}
    later = set(table.rows) - set(keys) - extra
    assert len(keys) == 107 and set(keys) | extra <= set(table.rows) and table.school == SCHOOL
    assert later <= round2 and len(later) == 35
    hows = [r.how.split(":", 1)[0] for k, r in table.rows.items() if k not in later]
    assert hows.count("auto") == 83 and hows.count("hand") == 24 + len(extra)  # the research's 83, by hand 24 (+4)
    # shared: the platforms many units publish on, and nothing else
    assert {k for k, r in table.rows.items() if r.shared} == {"news.nknu.edu.tw", "sso.nknu.edu.tw/news",
                                                              "sso.nknu.edu.tw", "sso.nknu.edu.tw/services"}


@pytest.mark.skipif(not SITE_PROBE.is_file(), reason="no site probe list here (research/ is not in git)")
def test_every_unit_of_the_site_table_is_a_unit_of_the_tree():
    units = load_units(SITE_PROBE, REPO_ROOT / "config" / "units.yaml")
    table = sites.table()
    assert units[table.school].parent_id is None  # the school is the tree's root
    assert {r.unit for r in table.rows.values()} <= set(units)


@pytest.mark.parametrize(("site", "unit"), [
    ("c.nknu.edu.tw/aca", "教務處"), ("c.nknu.edu.tw/ccee", "進修學院"), ("c.nknu.edu.tw/reality", "體育室"),
    ("tecs.nknu.edu.tw", "師資培育與就業輔導處"), ("sso.nknu.edu.tw/web/595", "生活輔導組"),
    ("sso.nknu.edu.tw/site/100061", "校安中心"), ("sites.google.com/mail.nknu.edu.tw/counseling-center", "學生輔導中心"),
    ("sso.nknu.edu.tw/site/207016", "軟體工程與管理學系"),  # a 品保網站: its department
    # SSO system parts: the unit that runs them (plan 2026-11-08 T3); unsure: the school
    ("sso.nknu.edu.tw/news", "秘書室"), ("news.nknu.edu.tw", "秘書室"), ("sso.nknu.edu.tw/osa", "學生事務處"),
    ("sso.nknu.edu.tw/coursemap", SCHOOL),
    # not in the table: the school
    ("sso.nknu.edu.tw/webservices", SCHOOL), ("www.googleapis.com", SCHOOL), ("example.invalid", SCHOOL), ("", SCHOOL),
])
def test_unit_of_site(site, unit):
    assert sites.unit_of_site(site) == unit


def test_unit_for_a_url_is_its_own_sites_else_its_exports():
    pdf = "https://c.nknu.edu.tw/affair/uploadfile/news/1/x.pdf"
    # the file services by a generic address of the same site key behaviour (CLAUDE.md: no school-internal interface
    # paths in tests): news.nknu.edu.tw's (a shared platform's row), and an SSO one on a key the table does not have
    news = "https://news.nknu.edu.tw/nknu_News/File.aspx?p=O"
    service = SSO_FILE
    drive = "https://www.googleapis.com/drive/v3/files/O1?alt=media"
    # a file another site shares: its owner's unit, whichever export brought it
    assert sites.unit_for(pdf, "c.nknu.edu.tw/aca") == sites.unit_for(pdf, "sso.nknu.edu.tw/web/595") == "總務處"
    # a host the table does not have (an outside host, SSO's file service) and a shared platform: the export's unit
    assert [sites.unit_for(u, "sso.nknu.edu.tw/web/595") for u in (news, service, drive)] == ["生活輔導組"] * 3
    assert sites.unit_for(news, "c.nknu.edu.tw/aca") == "教務處"
    assert sites.unit_for(news, "news.nknu.edu.tw") == "秘書室"  # the platform's own export: its row
    assert sites.unit_for(news) == "秘書室" and sites.unit_for(drive) == SCHOOL  # no export known
    assert sites.unit_for(WEB + "&menuid=514#按钮=住宿申請表", "") == "生活輔導組"  # a press-made row: its list's site


def test_a_broken_table_stops_whoever_reads_it(tmp_path):
    good = 'school: 國立高雄師範大學\nsites:\n  c.nknu.edu.tw/aca: {unit: 教務處, how: "auto: 第一轮名称"}\n'
    path = tmp_path / sites.SITE_UNITS
    assert sites.read_site_units_text(good, path).rows["c.nknu.edu.tw/aca"] == sites.SiteUnit(
        "c.nknu.edu.tw/aca", "教務處", "auto: 第一轮名称", False)
    for bad, why in [
        (good.replace("school: 國立高雄師範大學\n", ""), "school"),
        (good.replace("how: \"auto: 第一轮名称\"", "how: \"\""), "how"),
        (good.replace("how: \"auto: 第一轮名称\"", "how: \"第一轮名称\""), "auto: or hand:"),
        (good.replace("unit: 教務處", "unit: \"\""), "unit"),
        (good.replace("}", ", shared: yes please}"), "shared"),
        (good.replace("}", ", tier: background}"), "tier"),
        ("school: 國立高雄師範大學\nsites: [c.nknu.edu.tw/aca]\n", "sites"),
        # a site written twice: PyYAML alone would keep the last row and lose the first without a word
        (good + '  c.nknu.edu.tw/aca: {unit: 總務處, how: "hand: x"}\n', "twice"),
    ]:
        with pytest.raises((TypeError, ValueError), match=why):
            sites.read_site_units_text(bad, path)


# ---------------------------------------------------------------- documents: units and dates

def test_documents_take_the_unit_of_their_own_site_else_their_exports():
    notice = rec("n1", "detail:sso_webidx", WEB + "&selpage=MoreNews&newsIdx=1326", "兵籍調查", site="sso.nknu.edu.tw/web/595")
    records = [
        notice,
        rec("p1", "page:sso_webidx", WEB + "&menuid=518", "生輔組成員", site="sso.nknu.edu.tw/web/595"),
        rec("a1", "附件:pdf:有文字层", "https://c.nknu.edu.tw/aca/uploadfile/news/1/a.pdf", "a.pdf", "簡章",
            linked=(notice.url,), site="sso.nknu.edu.tw/web/595"),  # 教務處's file that 生活輔導組's page links
        rec("a2", "附件:pdf:有文字层", SSO_FILE, "b.pdf", "表", linked=(notice.url,), site="sso.nknu.edu.tw/web/595"),
        rec("a3", "附件:pdf:有文字层", "https://news.nknu.edu.tw/nknu_News/File.aspx?p=O", "c.pdf", "表",
            linked=(notice.url,), site="sso.nknu.edu.tw/web/595"),
        rec("l1", "外站:forms.gle", "https://forms.gle/O", "報名表單", site="sso.nknu.edu.tw/web/595"),
        rec("x1", "附件:pdf:有文字层", "https://www.googleapis.com/drive/v3/files/O?alt=media", "d.pdf", "表"),  # no site
    ]
    events = [Event("ev1", "宿舍開放", date(2026, 9, 1), date(2026, 9, 1), "https://www.google.com/calendar/event?eid=1",
                    site="sso.nknu.edu.tw/web/595"),
              Event("ev2", "期末考", date(2026, 12, 21), date(2026, 12, 25), "https://www.google.com/calendar/event?eid=2")]
    docs = by_id(build_documents(records, {}, events, site="c.nknu.edu.tw/aca")[0])
    assert {k: d.unit for k, d in docs.items()} == {
        "dn1": "生活輔導組", "dp1": "生活輔導組", "da1": "教務處", "da2": "生活輔導組", "da3": "生活輔導組",
        "dl1": "生活輔導組", "dx1": "教務處",  # no site of its own: the build's export (a corpus before T3)
        "eev1": "生活輔導組", "eev2": "教務處"}
    alone = by_id(build_documents([records[-1]], {}, events[1:])[0])  # nothing says which export
    assert (alone["dx1"].unit, alone["eev2"].unit) == (SCHOOL, SCHOOL)


def test_a_detail_page_is_a_notice_dated_by_the_date_its_page_shows():
    detail = rec("n1", "detail:sso_webidx", WEB + "&selpage=MoreNews&newsIdx=1326", "兵籍調查線上申報作業",
                 page_date="2026-09-11", page_date_raw="發布日期：2026-09-11", fetched="2026-10-27T04:41:00+08:00",
                 site="sso.nknu.edu.tw/web/595")
    file = rec("a1", "附件:pdf:有文字层", SSO_FILE, "說明.pdf", "說明", linked=(detail.url,), site="sso.nknu.edu.tw/web/595")
    docs = by_id(build_documents([detail, file], {}, [])[0])
    d = docs["dn1"]
    assert (d.kind_class, d.added, d.added_raw, d.added_source) == (
        "notice", date(2026, 9, 11), "發布日期：2026-09-11", "網頁上寫的日期")
    assert (docs["da1"].parent_id, docs["da1"].parent_title) == ("dn1", "兵籍調查線上申報作業")  # a dated notice
    # c.nknu's date field wins over the export's date (the 11-02 manifest misdated 16% of 教务处's notices)
    notice = rec("p32", "newsdetail", ACA + "newsdetail.aspx?nid=662&gi=2", "停修", page_date="2026-10-20",
                 page_date_raw="公告 2026.10.20")
    meta = PageMeta("和平教務組", "停修", "2026/10/21 上午 08:56:00", date(2026, 10, 21), ())
    n = build_documents([notice], {"p32": meta}, [])[0][0]
    assert (n.added, n.added_raw, n.added_source, n.page_date) == (date(2026, 10, 21), "2026/10/21 上午 08:56:00",
                                                                   "公告頁日期欄", None)
    undated = build_documents([rec("p33", "newsdetail", ACA + "newsdetail.aspx?nid=663", "x", page_date="2026-10-20",
                                   page_date_raw="公告 2026.10.20")], {}, [])[0][0]
    assert (undated.added, undated.added_source) == (date(2026, 10, 20), "網頁上寫的日期")  # no field: the export's
    # plan 2026-11-08 T3 review 【Claude】: a date the export read after a bare 日期 dates no notice either; here the tail
    # of 「申請日期】 115年8月27日（星期四）前提出申請」, a deadline before the day the page was fetched
    weak = build_documents([rec("n2", "detail:sso_webidx", WEB + "&selpage=MoreNews&newsIdx=1327", "宿舍申請",
                                page_date="2026-08-27", page_date_raw="日期】 115年8月27日",
                                fetched="2026-10-27T04:41:00+08:00")], {}, [])[0][0]
    assert (weak.kind_class, weak.added, weak.added_raw, weak.added_source) == ("notice", None, "", "")


def test_a_page_takes_the_date_its_page_shows_but_never_passes_it_on():
    page = rec("p2", "recordview", REALITY + "recordview.aspx?nid=18", "校隊成績", page_date=UPDATED,
               page_date_raw=UPDATED_RAW, fetched="2026-10-27T04:48:49+08:00")
    file = rec("a1", "附件:图片", REALITY + "uploadfile/record/18.jpg", "18.jpg", "成績", linked=(page.url,))
    docs = by_id(build_documents([page, file], {}, [])[0])
    p2 = docs["dp2"]
    assert (p2.page_date, p2.page_date_raw, p2.added) == (date(2025, 9, 17), UPDATED_RAW, None)
    a1 = docs["da1"]  # on the page, but none of its dates: those of a document are the build's (_facts)
    assert (a1.parent_id, a1.added, a1.page_date, a1.page_date_raw) == ("dp2", None, None, "")
    # 【Claude】: no date when it is no date of the page. A c.nknu list page's is one of its rows' (教务处's home pages
    # show their first notice's, 「公告 2026.10.01」); one after the day the page was fetched is a day it names ahead
    # (「榜單公告115年12月30日」); one the export wrote wrong is none. Nor, since the plan 2026-11-08 T3 review, is one
    # read after a bare 日期, the tail of a word naming another day: p6-p8 are the real ones of 教务处's export
    # (archive-export/aca-20261102 rows 16 and 224: 「修正校慶日期113.4.24」, 「【申請日期】 115年8月27日（星期四）前提出
    # 申請」) and of the cLv2 pilot's page=607 (「修正日期： 民國 113 年 01 月 25 日」, a rule's)
    cases = [rec("p1", "default", ACA + "default.aspx?gi=4", "首頁", page_date="2026-10-01",
                 page_date_raw="公告 2026.10.01"),
             rec("p3", "page:sso_clv2", CLV2 + "&page=4", "交通安全", page_date="2026-12-30",
                 page_date_raw="公告115年12月30日", fetched="2026-10-27T04:41:00+08:00"),
             rec("p4", "page", "https://sites.google.com/mail.nknu.edu.tw/counseling-center/x", "x",
                 page_date="2026-02-30", page_date_raw="更新時間： 2026.2.30"),
             rec("p5", "page", "https://sites.google.com/mail.nknu.edu.tw/counseling-center/y", "y",
                 page_date="1911-01-01", page_date_raw="更新時間： 1911.1.1"),
             rec("p6", "page", ACA + "page.aspx?pn=28&gi=2", "行事曆", page_date="2024-04-24",
                 page_date_raw="日期113.4.24", fetched="2026-10-27T06:25:09+08:00"),
             rec("p7", "page(sn)", ACA + "page.aspx?pn=208&sn=45&gi=6", "微學分", page_date="2026-08-27",
                 page_date_raw="日期】 115年8月27日", fetched="2026-10-27T06:36:53+08:00"),
             rec("p8", "page:sso_clv2", CLV2 + "&page=607", "折抵役期", page_date="2024-01-25",
                 page_date_raw="日期： 民國 113 年 01 月 25 日", fetched="2026-10-27T04:42:11+08:00"),
             rec("p9", "page", "https://sites.google.com/mail.nknu.edu.tw/counseling-center/z", "z",
                 page_date=UPDATED)]  # no words it was read from: no label either
    assert [d.page_date for d in build_documents(cases, {}, [])[0]] == [None] * len(cases)


def test_a_pages_date_never_orders_the_pages_naming_a_file():
    # plan 2026-11-08 T3: only a notice's date orders the pages linking a file (as before T3, so 教务处's files keep
    # their parents, titles and dates); an undated notice linking it first stays its parent and names it, before a
    # page with a date of its own 【Claude】
    notice = rec("n1", "detail:sso_webidx", WEB + "&selpage=MoreNews&newsIdx=1326", "兵籍調查線上申報作業")
    page = rec("p2", "page:sso_webidx", WEB + "&menuid=518", "生輔組成員", page_date=UPDATED, page_date_raw=UPDATED_RAW,
               fetched="2026-10-27T04:41:00+08:00")
    file = rec("a1", "附件:pdf:有文字层", SSO_FILE, "x.pdf", "表", linked=(notice.url, page.url),
               contexts={notice.url: "申報說明", page.url: "申報表格"})
    docs = by_id(build_documents([notice, page, file], {}, [])[0])
    assert (docs["dn1"].added, docs["dp2"].page_date) == (None, date(2025, 9, 17))
    a1 = docs["da1"]
    assert (a1.parent_id, a1.parent_title, a1.title, a1.title_from_context, a1.file_title) == (
        "dn1", "兵籍調查線上申報作業", "申報說明", True, "x.pdf")


def test_list_pages_and_pager_rows_are_left_out_and_counted():
    records = [rec("p1", "list:sso_webidx", WEB + "&menuid=514", "表單下載"),
               rec("p2", "list:sso_webidx#翻页", WEB + "&menuid=514#第2页", "表單下載"),
               rec("p3", "list:sso_clv2", CLV2 + "&page=4", "書表下載"),
               rec("p4", "page:sso_webidx", WEB + "&menuid=518", "生輔組成員"),
               rec("a1", "附件:pdf:有文字层", WEB + "&menuid=514#按钮=住宿申請表", "住宿申請表.pdf", "表",
                   linked=(WEB + "&menuid=514",))]
    docs, excluded = build_documents(records, {}, [])
    assert [d.doc_id for d in docs] == ["dp4", "da1"]
    assert excluded == {"list:sso_webidx": 1, "list:sso_webidx#翻页": 1, "list:sso_clv2": 1}
    a1 = by_id(docs)["da1"]  # on a list only: no parent, the list's title for its context (as on an album)
    assert (a1.parent_id, a1.parent_title) == (None, "表單下載")


# ---------------------------------------------------------------- corpus: newsview and the calendar's site

NEWSVIEW = ('﻿<html><head><meta charset="big5"></head><body><form id="form1"><div class="menu"><a href="x">首頁</a></div>'
            '<table id="DataFormView"><tr><td>日 期</td><td><span id="DataFormView_AddTimeLabel">2026/10/2 上午 12:00:00'
            '</span></td></tr><tr><td>類 別</td><td><span id="DataFormView_ClassLabel">一般公告</span></td></tr>'
            '<tr><td>主 題</td><td><span id="DataFormView_TitleLabel">敬邀全校同仁學生參加校園趣味競賽</span></td></tr>'
            '<tr><td colspan="2"><span id="DataFormView_ContentsLabel"><p>報名請洽O先生。</p></span></td></tr>'
            '<tr><td colspan="2"><span id="DataFormView_FileLinkLabel"><a href="uploadfile/news/1/a.pdf">報名表.pdf</a>'
            '</span></td></tr></table></form></body></html>')


def test_read_page_meta_reads_a_newsview_page(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "0002.html").write_text(NEWSVIEW, encoding="utf-8")
    url = REALITY + "newsview.aspx?nid=882"
    manifest = {"网站": "c.nknu.edu.tw/reality", "名称": "体育室",
                "项目": [{"类型": "网页", "网址": url, "最后落地网址": "", "本地档案": "pages/0002.html"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    meta = read_page_meta(tmp_path, "pages/0002.html")
    assert meta == PageMeta("", "敬邀全校同仁學生參加校園趣味競賽", "2026/10/2 上午 12:00:00", date(2026, 10, 2),
                            (REALITY + "uploadfile/news/1/a.pdf",))  # 類別 is a category, never a 組別
    view = Record("n1", "newsview", url, "pages/0002.html", "敬邀", "", "", ())
    assert read_metas(tmp_path, [view]) == {"n1": meta}


def test_calendar_events_carry_their_exports_site(tmp_path):
    tb_paths = tb.make_inputs(tmp_path)
    assert {ev.site for ev in load_calendar(tb_paths["export"])} == {"c.nknu.edu.tw/aca"}


# ---------------------------------------------------------------- assemble: kinds from the page, fields, fallback

DETAIL = WEB + "&selpage=MoreNews&newsidx=1326"  # as the crawler spells it (an IIS host: parameter names in lower case)
DETAIL_HTML = ('<div class="news-detail-view"><div class="detail-header"><h3>兵籍調查線上申報作業</h3></div>'
               '<div class="detail-body"><p>請於期限內申報。承辦人O先生。</p></div><ul class="detail-attachments">'
               f'<li class="file-item"><a href="{SSO_FILE_HREF}"><span class="file-text">'
               '申報說明</span></a></li></ul></div>')
FILE_URL = SSO_FILE  # an SSO file service (generic form), crawler-spelt


def sso_rows() -> list[dict]:
    """An export of sso.nknu.edu.tw/web/595 as the pilot's holds one: a prose page, a 书表 list and its second page (a
    pager press), a notice's detail page with the date the export read on it, a file the detail links and one the
    list's download press brought."""
    page = {"type": "网页", "fetched": "2026-10-27T04:41:00+08:00"}
    return [
        page | {"url": WEB + "&menuid=518", "file": "pages/0001.html", "title": "生輔組成員",
                "data": tp.webidx_page(tp.STAFF)},
        page | {"url": WEB + "&menuid=514", "file": "pages/0002.html", "title": "表單下載",
                "data": tp.webidx_page(tp.items_list("住宿申請表", "請假單"))},
        page | {"url": WEB + "&menuid=514#第2页", "file": "pages/0003.html", "title": "表單下載",
                "data": tp.webidx_page(tp.items_list("退宿申請表"))},
        page | {"url": DETAIL, "file": "pages/0004.html", "title": "兵籍調查線上申報作業",
                "data": tp.webidx_page(DETAIL_HTML), "date": "2026-09-11", "date_raw": "發布日期：2026-09-11"},
        {"type": "附件", "url": FILE_URL, "file": "files/0005-說明.pdf", "data": ta.PDF + b"five", "title": "申報說明.pdf",
         "fmt": "pdf", "linked": [DETAIL], "linktext": "申報說明", "fetched": "2026-10-27T04:42:00+08:00"},
        {"type": "附件", "url": WEB + "&menuid=514#按钮=住宿申請表", "file": "files/0006-住宿申請表.pdf",
         "data": ta.PDF + b"six", "title": "住宿申請表.pdf", "fmt": "pdf", "linked": [WEB + "&menuid=514"],
         "linktext": "住宿申請表", "fetched": "2026-10-27T04:43:00+08:00"},
    ]


def sso_results() -> dict:
    def page(text: str) -> tuple[dict, str]:
        return {"page": {"title": "", "album": "", "images": []}}, text

    return {"1": page("生輔組成員\n生活輔導組組長 O組長\n分機：1230"), "2": page("住宿申請表\n請假單"),
            "3": page("退宿申請表"), "4": page("兵籍調查線上申報作業\n請於期限內申報。承辦人O先生。"),
            "5": ({"pages": 1, "page_starts": [0]}, "兵籍調查申報說明\n" + ta.BODY),
            "6": ({"pages": 1, "page_starts": [0]}, "住宿申請表\n" + ta.BODY)}


def assemble_sso(tmp_path: Path, **kw) -> tuple[dict[str, dict], object]:
    records, report = ta.run_assemble(tmp_path, sso_rows(), sso_results(), site="sso.nknu.edu.tw/web/595",
                                      name="学务处生活辅导组", **kw)
    return ta.by_id(records), report


def test_assemble_names_pages_by_the_list_grid_their_page_shows(tmp_path):
    r, _ = assemble_sso(tmp_path, old=None)
    # the kind of each page is read from the exported page (Task 2's list_grid where it was read): a 书表 list and its
    # pager row are lists, the prose page and the detail stay
    assert {k: r[k]["kind"] for k in ("p1", "p2", "p3", "p4")} == {
        "p1": "page:sso_webidx", "p2": "list:sso_webidx", "p3": "list:sso_webidx#翻页", "p4": "detail:sso_webidx"}
    # every record carries its export's site, its row's 抓取时间 and the date the export read on its page
    assert {k: (r[k]["site"], r[k]["fetched"], r[k]["page_date"], r[k]["page_date_raw"]) for k in ("p4", "a5")} == {
        "p4": ("sso.nknu.edu.tw/web/595", "2026-10-27T04:41:00+08:00", "2026-09-11", "發布日期：2026-09-11"),
        "a5": ("sso.nknu.edu.tw/web/595", "2026-10-27T04:42:00+08:00", "", "")}


def test_the_1029_fallback_is_off_for_another_sites_export(tmp_path):
    results = sso_results()
    results["6"] = ({"status": "failed", "reason": "Docling 讀不了這個檔"}, "")
    old = [{"id": "a6", "kind": "附件:pdf:有文字层", "url": ACA + "x.pdf", "file": "files/0006-住宿申請表.pdf",
            "title": "x.pdf", "text": "教務處 10-29 的文字"}]  # same old serial id, same file name: it would match
    records, report = ta.run_assemble(tmp_path / "sso", sso_rows(), results, old=old, site="sso.nknu.edu.tw/web/595")
    a6 = ta.by_id(records)["a6"]
    assert (a6["source"], a6["text"], report.fallback_refused, report.fallback_off) == ("none", "", [], True)
    on, report = ta.run_assemble(tmp_path / "aca", sso_rows(), results, old=old)  # 教务处's export: still read
    assert (ta.by_id(on)["a6"]["source"], report.fallback_off) == ("fallback-1029", False)


# ---------------------------------------------------------------- the build: an SSO site end to end

@pytest.fixture
def sso_db(tmp_path):
    assemble_sso(tmp_path, old=None)
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "x.db"
    report = build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", out, server_dates=None,
                      aliases=tmp_path / "aliases.yaml")
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    d = {name: "d" + rid for name, rid in ta.row_ids(tmp_path / "export").items()}
    yield report, conn, d
    conn.close()


def test_an_sso_sites_notices_lists_dates_and_units_build(sso_db):
    report, conn, d = sso_db
    docs = {r[0]: r[1:] for r in conn.execute(
        "SELECT doc_id, kind_class, unit, parent_id, ident_date, ident_kind FROM docs")}
    assert docs == {
        d["p1"]: ("page", "生活輔導組", None, None, None),
        d["p4"]: ("notice", "生活輔導組", None, "2026-09-11", "published"),
        d["a5"]: ("attachment", "生活輔導組", d["p4"], "2026-09-11", "inherited"),  # SSO's file service: the export's
        d["a6"]: ("attachment", "生活輔導組", None, None, None)}  # on the 书表 list only: no parent, no date
    assert report.excluded == {"list:sso_webidx": 1, "list:sso_webidx#翻页": 1}
    dates = conn.execute("SELECT doc_id, kind, date, raw, source FROM dates ORDER BY doc_id, kind").fetchall()
    # the file inherits its notice's date with the source the notice's has (plan 2026-11-08 T3 review): read on the
    # notice's page, never the 所屬公告 of a notice's own date field (was 所屬公告 before the review)
    assert dates == sorted([(d["p4"], "published", "2026-09-11", "發布日期：2026-09-11", "網頁上寫的日期"),
                            (d["a5"], "inherited", "2026-09-11", "發布日期：2026-09-11", "網頁上寫的日期")])
    assert report.date_sources["notice"] == {"published": 1}


def test_respond_says_where_the_date_of_a_detail_page_came_from(sso_db):
    _, conn, d = sso_db
    resp = read_response(conn, d["p4"], "", today=TODAY)
    assert resp["identity"] == "生活輔導組｜公告｜發布 2026-09-11（網頁上寫的日期）"
    assert resp["dates"][0] == {"type": "發布", "date": "2026-09-11", "source": "網頁上寫的日期",
                                "raw": "發布日期：2026-09-11"}
    # one step down too: its file's date is the notice's, read on the notice's page
    file = read_response(conn, d["a5"], "", today=TODAY)
    assert file["identity"] == "生活輔導組｜附件（PDF）｜所屬公告發布 2026-09-11（網頁上寫的日期）"
    assert file["dates"][0] == {"type": "所屬公告發布", "date": "2026-09-11", "source": "網頁上寫的日期",
                                "raw": "發布日期：2026-09-11"}


def test_the_source_of_a_published_date_without_its_row_follows_the_kind(tmp_path):
    from tests.retrieval.test_respond import Tiny

    t = Tiny(tmp_path / "x.db")
    try:
        t.doc("dp1", "折抵役期說明", kind_class="page", kind_raw="page:sso_clv2", unit="校安中心", grp="",
              ident=("2024-01-25", "published"))
        t.doc("dn1", "停修公告", ident=("2026-10-21", "published"))
        identity = {i: read_response(t.conn, i, "", today=TODAY)["identity"] for i in ("dp1", "dn1")}
    finally:
        t.conn.close()
    assert identity == {"dp1": "校安中心｜網頁｜發布 2024-01-25（網頁上寫的日期）",
                        "dn1": "教務處｜註冊組｜公告｜發布 2026-10-21（公告頁日期欄）"}


CLV2_FORM = ('<div class="col-12"><h3 class="title">折抵役期</h3></div><div id="divContent" class="divContent">'
             f'<p>役男申請折抵役期，請填申請表。</p><p><a href="{SSO_FILE_HREF}">折抵役期申請表</a></p></div>')


def test_a_page_with_a_date_on_it_is_dated_and_shown_so(tmp_path):
    # dated by 「更新時間：」, a label of changing the page; the pilot's own page=607 writes 「修正日期：」 (a rule's), whose
    # bare 日期 dates nothing since the plan 2026-11-08 T3 review (this test used it before)
    page = {"type": "网页", "fetched": "2026-10-27T04:41:00+08:00"}
    rows = [page | {"url": CLV2 + "&page=607", "file": "pages/0001.html", "title": "折抵役期 - 校安中心(原軍訓室)",
                    "data": tp.clv2_page(CLV2_FORM), "date": UPDATED, "date_raw": UPDATED_RAW},
            page | {"url": CLV2 + "&page=4", "file": "pages/0002.html", "title": "書表下載",
                    "data": tp.clv2_page(tp.CLV2_FILES)},
            {"type": "附件", "url": SSO_FILE, "file": "files/0003-折抵役期申請表.pdf", "data": ta.PDF + b"form",
             "title": "折抵役期申請表.pdf", "fmt": "pdf", "linked": [CLV2 + "&page=607"], "linktext": "折抵役期申請表",
             "fetched": "2026-10-27T04:42:00+08:00"}]
    results = {"1": ({"page": {"title": "", "album": "", "images": []}}, "役男申請折抵役期，請填申請表。\n折抵役期申請表"),
               "2": ({"page": {"title": "", "album": "", "images": []}}, "折抵役期申請表"),
               "3": ({"pages": 1, "page_starts": [0]}, "折抵役期申請表\n" + ta.BODY)}
    ta.run_assemble(tmp_path, rows, results, old=None, site="sso.nknu.edu.tw/site/100061", name="校安中心（原军训室）")
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "x.db"
    report = build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", out, server_dates=None,
                      aliases=tmp_path / "aliases.yaml")
    d = {name: "d" + rid for name, rid in ta.row_ids(tmp_path / "export").items()}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        resp = read_response(conn, d["p1"], "", today=TODAY)
        file = read_response(conn, d["a3"], "", today=TODAY)
        kinds = dict(conn.execute("SELECT doc_id, kind_raw FROM docs"))
        attached = conn.execute("SELECT parent_id, ident_date, ident_kind FROM docs WHERE doc_id = ?",
                                (d["a3"],)).fetchone()
        file_dates = conn.execute("SELECT count(*) FROM dates WHERE doc_id = ?", (d["a3"],)).fetchone()[0]
    finally:
        conn.close()
    assert resp["identity"] == "校安中心｜網頁｜發布 2025-09-17（網頁上寫的日期）"
    assert kinds == {d["p1"]: "page:sso_clv2", d["a3"]: "附件:pdf:有文字层"} and report.excluded == {"list:sso_clv2": 1}
    assert report.date_sources["page"] == {"published": 1}
    # the page's date is its own (plan 2026-11-08 T3): the file it links hangs on it, takes no date from it and shows
    # none (no notice above it, no server time here)
    assert attached == (d["p1"], None, None) and file_dates == 0
    assert report.date_sources["attachment"] == {"none": 1}
    assert file["identity"] == "校安中心｜附件（PDF）｜無日期"


# ---------------------------------------------------------------- the build: two c.nknu sites, one nid

def test_two_cnknu_sites_with_one_nid_build_two_notices_with_their_files_groups_and_units(tmp_path):
    # Review Focus 1 through the build: the notice pages' own fields (組別, date), each site's gi, each site's unit.
    # plan 2026-11-08 T4 (deliberate): each site's records come with their own export, two inputs of one build; this
    # was one corpus whose records named two sites beside one export, which now stops the build (build._read_input:
    # its files would be read in another site's folder). The records carry their keys (ids.location_key), as an
    # assembled corpus's do, which two inputs need (a corpus with ids of its own builds alone)
    pages = {"aca": (ACA, "和平教務組", "2026/10/1 上午 09:00:00", "學分學程申請"),
             "ccee": (CCEE, "教務組", "2026/9/1 上午 09:00:00", "專題演講")}
    inputs = []
    for name, (base, group, added, title) in pages.items():
        export = tmp_path / name / "export"
        (export / "pages").mkdir(parents=True)
        url, file = base + "newsdetail.aspx?nid=578&gi=2", f"pages/{name}578.html"
        (export / file).write_text(tb.NOTICE_HTML.format(group=group, title=title, added=added), encoding="utf-8")
        items = [{"类型": "网页", "网址": url, "最后落地网址": "", "本地档案": file}]
        site = base.removeprefix("https://").rstrip("/")
        page, pdf = base + "page.aspx?pn=1&gi=2", base + "uploadfile/news/578/a.pdf"
        records = [{"id": f"n{name}", "key": ids.location_key(url), "kind": "newsdetail", "url": url, "file": file,
                    "title": title, "text": title, "site": site},
                   {"id": f"p{name}", "key": ids.location_key(page), "kind": "page", "url": page,
                    "file": f"pages/p{name}.html", "title": f"{name} 頁", "text": "業務", "site": site},
                   {"id": f"a{name}", "key": ids.location_key(pdf), "kind": "附件:pdf:有文字层", "url": pdf,
                    "file": f"files/{name}.pdf", "title": "a.pdf", "text": f"{title}附件", "linked": url, "site": site}]
        (export / "manifest.json").write_text(json.dumps({"网站": site, "名称": name, "项目": items}, ensure_ascii=False),
                                              encoding="utf-8")
        (tmp_path / name / "corpus.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        inputs.append((tmp_path / name / "corpus.json", export))
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    build_sites(inputs, tmp_path / "db" / "x.db", server_dates=None, aliases=tmp_path / "aliases.yaml")
    conn = sqlite3.connect(f"file:{tmp_path / 'db' / 'x.db'}?mode=ro", uri=True)
    try:
        docs = {r[0]: r[1:] for r in conn.execute("SELECT doc_id, kind_class, unit, grp, parent_id, ident_date FROM docs")}
    finally:
        conn.close()
    assert docs == {
        "dnaca": ("notice", "教務處", "和平教務組", None, "2026-10-01"),
        "dpaca": ("page", "教務處", "和平教務組", None, None),
        "daaca": ("attachment", "教務處", "和平教務組", "dnaca", "2026-10-01"),
        "dnccee": ("notice", "進修學院", "教務組", None, "2026-09-01"),
        "dpccee": ("page", "進修學院", "教務組", None, None),  # gi 2 is voted per site
        "daccee": ("attachment", "進修學院", "教務組", "dnccee", "2026-09-01")}


def test_a_newsview_notice_is_dated_by_its_own_field(tmp_path):
    url = REALITY + "newsview.aspx?nid=882"
    rows = [{"type": "网页", "url": url, "file": "pages/0001.html", "title": "敬邀全校同仁學生參加校園趣味競賽",
             "data": NEWSVIEW.encode(), "date": "2026-10-02", "date_raw": "日 期 2026/10/2"},
            {"type": "附件", "url": REALITY + "uploadfile/news/1/a.pdf", "file": "files/0002-a.pdf", "data": ta.PDF + b"a",
             "title": "a.pdf", "fmt": "pdf", "linked": [url], "linktext": "報名表.pdf"}]
    results = {"1": ({"page": {"title": "", "album": "", "images": []}}, "報名請洽O先生。"),
               "2": ({"pages": 1, "page_starts": [0]}, "報名表\n" + ta.BODY)}
    ta.run_assemble(tmp_path, rows, results, old=None, site="c.nknu.edu.tw/reality", name="体育室")
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "x.db"
    build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", out, server_dates=None,
             aliases=tmp_path / "aliases.yaml")
    d = {name: "d" + rid for name, rid in ta.row_ids(tmp_path / "export").items()}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        docs = {r[0]: r[1:] for r in conn.execute("SELECT doc_id, kind_class, unit, parent_id, ident_date FROM docs")}
        source = conn.execute("SELECT source FROM dates WHERE doc_id = ? AND kind = 'published'", (d["p1"],)).fetchone()
    finally:
        conn.close()
    assert docs == {d["p1"]: ("notice", "體育室", None, "2026-10-02"),
                    d["a2"]: ("attachment", "體育室", d["p1"], "2026-10-02")}
    assert source == ("公告頁日期欄",)  # #DataFormView_AddTimeLabel, the page's own field
