"""Blueprint item 22 (owner 11-09, 「照这个做」), the content side:

- a kept page whose reading gave much less than its own words is read again from its whole reading without the site's
  template (assemble's under_read: 「对上的区块几乎没字、整页却有很多字，就改读整页（去掉选单、页首、页尾），并记下来」),
  and the build writes each site's count to sites.under_read (「每站报告加『读到的字远少于整页』的页数」);
- a site's root address (its home page) takes no date from its words: it shows its first notice's.

After the 11-09 review: the template share is 10% of a group's files (a second template on a quarter of them is no
page's own words); a home page, a c.nknu list and a page the export marked as deleted are never read again; a page of
another platform or site in the export is judged with its own group; a menu run, images outside the page's own words
and extra copies of what the area read stay out; a site none of whose pages could be judged has no count.

The pages are read by the real page rule (pagetext through convert and the cache), so the whole reading and its link
lines are what the lanes store. The names and words in the fixtures are made up (O masks); no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from nknu.archive.export import NO_AREA
from nknu.content import build
from nknu.content.assemble import (
    MENU_RUN,
    OWN_CHARS,
    TEMPLATE_PAGES,
    TEMPLATE_SHARE,
    Again,
    Whole,
    assemble,
    read_again,
)
from nknu.content.corpus import load_corpus
from nknu.content.dates import OWN_FIELD, PAGE_SOURCE, PAGE_WORDS
from nknu.content.documents import _stated
from nknu.totext.accounting import input_route, inputs
from nknu.totext.cache import Cache, sha256_file
from nknu.totext.convert import Tools, convert_file, store
from tests.content import test_assemble as ta
from tests.content import test_items_1109 as ti
from tests.content import test_multisite_1108 as tm

ACA = "https://c.nknu.edu.tw/aca/"
MENU = "".join(f'<li><a href="page.aspx?pn={n}">{w}</a></li>'
               for n, w in enumerate(("最新消息", "系所簡介", "師資陣容", "招生資訊", "所友名錄"), start=1))
FOOT = '<div class="foot"><p>地址：高雄市苓雅區和平一路116號</p><p>電話：07-0000000 分機 1234</p></div>'
LOGO = '<img src="images/logo.png">'
NORMAL = ("本系辦理大學部轉系考試，報名期間自十一月一日起至十一月十日止，請於期限內完成線上報名並繳交相關資料。",
          "碩士班招生說明會將於本週五下午舉行，地點在行政大樓三樓會議室，歡迎有興趣的同學踴躍參加並提出問題。",
          "系學會舉辦期末聯誼活動，報名截止日為十二月五日，名額有限額滿為止，請同學把握機會儘早完成報名手續。",
          "本學期導師時間調整至每週三中午，請各班導師與同學留意新的時間安排，若有任何問題請洽系辦公室詢問。",
          "實習課程說明會資料已上傳至系網，請修課同學下載閱讀並於下週一前回覆實習意願調查表以利後續安排。")
ALUMNI = [("姓名", "王O明"), ("屆別", "第十二屆碩士在職專班"), ("電子郵件", "alumnus@example.com"),
          ("論文題目", "成人學習者參與社區大學終身學習課程之動機與學習成效關係研究：以高雄市三所社區大學為例"),
          ("指導教授", "陳O丙 教授"), ("現職", "高雄市立某某國民中學教務主任兼任社區教育推廣中心執行秘書"),
          ("研究摘要", ("本研究以問卷調查與深度訪談探討成人學習者的參與動機，發現自我成長與人際互動是主要動機，"
                        "且動機強度與學習成效呈正相關，建議社區大學規劃課程時兼顧實用與社群經營。"))]


def page_html(area: str, outside: str = "") -> bytes:
    """A c.nknu page: a logo and a menu of links, the content area (#MainContent), what sits outside it, a footer."""
    return (f'<html><body>{LOGO}<div class="menu"><ul>{MENU}</ul></div><div id="MainContent">{area}</div>'
            f'{outside}{FOOT}</body></html>').encode()


# The page the content area misses is a made-up kind (people_detail.aspx, a table of class profile): the crawler
# session fixes the real ones' areas (student_detail's table.EditArea and others), which would read these fixtures whole.
def alumni_html() -> bytes:
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in ALUMNI)
    return page_html("所友", f'<table class="profile">{rows}</table>')  # the area holds two characters


def site_rows(pages: int = len(NORMAL)) -> list[dict]:
    rows = [{"type": "网页", "url": f"{ACA}page.aspx?pn={n}", "file": f"pages/{n:04d}.html", "title": "公告",
             "data": page_html(f"<p>{NORMAL[n - 1]}</p>")} for n in range(1, pages + 1)]
    rows.append({"type": "网页", "url": f"{ACA}people_detail.aspx?pid=1", "file": "pages/0100.html",
                 "title": "所友", "data": alumni_html()})
    return rows


def read_pages(tmp_path: Path, export: Path) -> Path:
    """The to-text cache of the export's pages as the lanes write it: each page read by the page rule (convert_file)
    and stored under its file's sha256 (store), its whole reading beside its text."""
    totext = tmp_path / "totext"
    cache = Cache(totext / "cache")
    for inp in inputs(export):
        path = export / inp.file
        route = input_route(inp, path)
        conv = convert_file(path, route, Tools(), tmp_path / "work")
        store(cache, sha256_file(path), conv, route, seconds=0.0, tools={}, key=inp.key, file=inp.file)
    return totext


def assembled(tmp_path: Path, rows: list[dict], extra: dict[int, dict] | None = None
              ) -> tuple[dict[str, dict], dict, Path, Path]:
    """({url: record}, the assemble report, the corpus, the export) of 教务处's made-up export, with the columns of
    extra (序号 -> {column: value}) added to its rows."""
    export = ta.write_export(tmp_path / "export", rows)
    if extra:
        manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["项目"]:
            item.update(extra.get(int(item["序号"]), {}))
        (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    totext = read_pages(tmp_path, export)
    out = tmp_path / "out" / "corpus.json"
    report = assemble(export, totext, out, fallback=None)
    records = json.loads(out.read_text(encoding="utf-8"))
    return {r["url"]: r for r in records}, report, out, export


def test_a_page_whose_area_holds_almost_nothing_is_read_again_without_the_sites_template(tmp_path):
    records, report, out, _ = assembled(tmp_path, site_rows())
    alumni = records[f"{ACA}people_detail.aspx?pid=1"]
    assert alumni["under_read"] is True and report.under_read == 1
    lines = alumni["text"].split("\n")
    assert lines[0] == "所友"  # what its reading gave stays, where it sits
    assert lines[1:] == [f"| {k} | {v} |" for k, v in ALUMNI]  # the page's own words, in its order
    for gone in ("最新消息", "所友名錄", "地址：", "電話：", "[[img:"):  # menus, footer, logo: the site's template
        assert gone not in alumni["text"]
    # the other pages read well: untouched
    for n, words in enumerate(NORMAL, start=1):
        record = records[f"{ACA}page.aspx?pn={n}"]
        assert (record["text"], record["under_read"]) == (words, False)
    assert {r.url: r.under_read for r in load_corpus(out)}[f"{ACA}people_detail.aspx?pid=1"] is True


def test_short_words_of_its_own_are_no_reason_to_read_again(tmp_path):
    rows = site_rows()
    short = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in ALUMNI[:2])  # well under OWN_CHARS
    rows[-1]["data"] = page_html("所友", f'<table class="profile">{short}</table>')
    records, report, _, _ = assembled(tmp_path, rows)
    assert records[f"{ACA}people_detail.aspx?pid=1"]["text"] == "所友" and report.under_read == 0
    assert OWN_CHARS == 200


def test_a_left_out_list_page_is_never_read_again(tmp_path):
    rows = site_rows()
    rows[-1]["url"] = f"{ACA}newslist.aspx?nc=1"  # c.nknu's list: the build leaves it out
    records, report, _, _ = assembled(tmp_path, rows)
    assert records[f"{ACA}newslist.aspx?nc=1"]["under_read"] is False and report.under_read == 0


def test_a_site_of_too_few_pages_tells_no_template_and_is_left_as_read(tmp_path):
    rows = site_rows(TEMPLATE_PAGES - 2)  # with the alumni page: one page short of TEMPLATE_PAGES
    records, report, _, _ = assembled(tmp_path, rows)
    assert len(records) == TEMPLATE_PAGES - 1
    alumni = records[f"{ACA}people_detail.aspx?pid=1"]
    assert (alumni["text"], alumni["under_read"], report.under_read) == ("所友", None, 0)  # None: not judged
    assert report.under_read_unjudged == TEMPLATE_PAGES - 1


def test_a_second_template_on_a_quarter_of_the_files_is_no_pages_own_words(tmp_path):
    # news.nknu.edu.tw on 11-09: its notice system's chrome on 24-26% of the export's files; at a share of 0.3 its 99
    # correctly read notices were read again into that chrome
    assert TEMPLATE_SHARE == 0.1
    rows = [{"type": "网页", "url": f"{ACA}page.aspx?pn={n}", "file": f"pages/{n:04d}.html", "title": "公告",
             "data": page_html(f"<p>{NORMAL[n % 5]}（第 {n} 則）</p>")} for n in range(1, 16)]  # 15 files, one template
    chrome = ('<div class="dialog"><p>系統訊息：</p><p>是確定否取消</p><p>繁中 English</p></div><div class="links">'
              '<p>相關連結</p><p>相關附檔</p></div><div class="contact"><p>聯絡資訊：(07)717-2930 和平校區：80201高雄市'
              '苓雅區和平一路116號 燕巢校區：82446高雄市燕巢區深中路62號 服務時間：週一至週五上午八時至下午五時</p>'
              '<p>本網站著作權屬於國立高雄師範大學所有，轉載請註明出處，網站維護：圖書資訊處系統組</p>'
              '<p>本系統建議使用最新版瀏覽器瀏覽，解析度一九二零乘一零八零以上，若有任何問題請洽系統管理員協助處理</p></div>')
    for n, words in enumerate(NORMAL, start=1):
        rows.append({"type": "网页", "url": f"{ACA}newsdetail.aspx?nid={n}", "file": f"pages/{100 + n:04d}.html",
                     "title": "公告", "data": (f'<html><body><span id="lbl_Title">公告{n}</span>'
                                             f'<div id="lbl_Contents">{words[:30]}</div>{chrome}</body></html>').encode()})
    records, report, _, _ = assembled(tmp_path, rows)
    notices = [records[f"{ACA}newsdetail.aspx?nid={n}"] for n in range(1, 6)]
    assert [(r["under_read"], r["text"].count("系統訊息")) for r in notices] == [(False, 0)] * 5
    assert report.under_read == 0


def test_a_home_page_a_cnknu_list_and_a_page_marked_deleted_are_never_read_again(tmp_path):
    teasers = "".join(f"<p>{words}</p>" for words in NORMAL)  # the notices' words again: none of them template
    rows = site_rows()
    rows += [{"type": "网页", "url": url, "file": f"pages/{200 + n:04d}.html", "title": "首頁",
              "data": page_html("最新消息", f'<div class="news">{teasers}</div>')}
             for n, url in enumerate((ACA, f"{ACA}default.aspx?gi=1"))]
    rows.append({"type": "网页", "url": f"{ACA}newsdetail.aspx?nid=9", "file": "pages/0300.html", "title": "",
                 "data": f'<html><body>{LOGO}<ul>{MENU}</ul><div class="news">{teasers}</div>{FOOT}</body></html>'
                 .encode()})  # no notice areas on it: the export marks it, and the page rule reads nothing
    records, report, _, _ = assembled(tmp_path, rows, {len(rows): {"缺什么": NO_AREA}})
    for url in (ACA, f"{ACA}default.aspx?gi=1"):
        assert (records[url]["text"], records[url]["under_read"]) == ("最新消息", False), url
    marked = records[f"{ACA}newsdetail.aspx?nid=9"]
    assert (marked["text"], marked["under_read"], marked["area_mark"]) == ("", False, "找不到内文区")
    assert report.under_read == 1  # the alumni page alone


def test_each_sites_documents_read_again_are_in_the_sites_table(tmp_path):
    _, _, corpus, export = assembled(tmp_path, site_rows())
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "db" / "c.db"
    report = build.build_sites([(corpus, export)], out, server_dates=None, aliases=paths["aliases"],
                               today=ti.TODAY)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        rows = dict(conn.execute("SELECT site, under_read FROM sites"))
        text = conn.execute("SELECT text FROM docs WHERE url = ?", (f"{ACA}people_detail.aspx?pid=1",)).fetchone()
    finally:
        conn.close()
    assert rows == {"c.nknu.edu.tw/aca": 1} and [s["under_read"] for s in report.sites] == [1]
    assert "王O明" in text[0] and "最新消息" not in text[0]
    assert "under_read" in build.SITE_COLUMNS


# ---------------------------------------------------------------- a site's root address takes no date (item 22)

def test_a_sites_home_page_takes_no_date_from_its_words():
    def stated(url: str, site: str = "c.nknu.edu.tw/tcsl", source: str = PAGE_WORDS):
        return _stated(ti.rec("p1", "其他:", url, page_date="2026-11-02", page_date_raw="公告日期：2026-11-02",
                              date_source=source, site=site))

    for root in ("https://c.nknu.edu.tw/tcsl", "https://c.nknu.edu.tw/tcsl/", "https://c.nknu.edu.tw/TCSL/",
                 "https://c.nknu.edu.tw:443/tcsl/", "https://c.nknu.edu.tw/tcsl/#第2页"):
        assert stated(root) is None, root
    assert stated("https://clcts.nknu.edu.tw/", "clcts.nknu.edu.tw") is None  # another platform's home too
    assert stated("https://clcts.nknu.edu.tw/", "clcts.nknu.edu.tw", OWN_FIELD) is None  # whatever found the date
    # a page under the root, a short link the export holds and a record without its site keep theirs
    day = (date(2026, 11, 2), "公告日期：2026-11-02", PAGE_SOURCE)
    assert stated("https://c.nknu.edu.tw/tcsl/page.aspx?pn=1") == day
    assert stated("https://reurl.cc/k9eean", "sso.nknu.edu.tw/osa") == day
    assert stated("https://c.nknu.edu.tw/tcsl/", "") == day
    # a page a site serves at its root path with a query is no home page (the round-2 sites gauss, ite, ord)
    assert stated("https://gauss.nknu.edu.tw/?p=news&id=123", "gauss.nknu.edu.tw") == day


# ---------------------------------------------------------------- read_again on made-up whole readings (11-09 review)

GROUP = "cnknu|c.nknu.edu.tw/tg"
SITE_MENU = ["最新消息", "系所簡介", "所友"]  # links on every page: template
FOOTER = "地址：高雄市苓雅區和平一路116號 電話：07-0000000"
SECTION = ["[[img:images/section_banner.png]]", "專任教師", "兼任教師", "行政人員"]  # a section's banner and menu
ROWS = [f"| {k} | {v} |" for k, v in ALUMNI]


def whole_of(body: list[str], links: tuple[str, ...] = (), group: str = GROUP) -> Whole:
    lines = ["[[img:images/logo.png]]", *SITE_MENU, *body, FOOTER]
    marked = set(SITE_MENU) | set(links)
    return Whole(lines, frozenset(n for n, line in enumerate(lines) if line in marked), group)


def section_site() -> dict[str, Whole]:
    """Six files of one group: four notices, one with the section's banner and menu, and the alumni page with them."""
    wholes = {f"n{n}": whole_of([words]) for n, words in enumerate(NORMAL[:4])}
    wholes["s"] = whole_of([*SECTION, NORMAL[4]], tuple(SECTION[1:]))
    wholes["a"] = whole_of([*SECTION, "所友", *ROWS[:3], "[[img:people/a.jpg]]", "個人網頁", *ROWS[3:]],
                           (*SECTION[1:], "個人網頁"))
    return wholes


def test_a_menu_run_images_outside_the_pages_words_and_extra_copies_of_the_read_lines_stay_out():
    assert MENU_RUN == 3
    [again] = read_again(section_site(), [("a", "所友", "student_detail", True)])
    assert again.judged and again.text is not None
    assert again.text.split("\n") == ["所友", *ROWS[:3], "[[img:people/a.jpg]]", "個人網頁", *ROWS[3:]]
    # gone: the site's logo, menu and footer (template), the section's banner (before the page's own words) and its
    # menu (three links in a row); kept: the area's 「所友」 once, where it stands by the rows (the menu's copy goes),
    # the photo among the rows and a link alone


def test_read_again_judges_a_group_only_with_enough_files_and_a_page_only_when_it_may():
    wholes = section_site()
    wholes["x"] = whole_of(["所友", *ROWS], group="sso_news|sso.nknu.edu.tw/news")  # another site's page in the export
    got = read_again(wholes, [("x", "所友", "detail:sso_news", True), ("a", "所友", "student_detail", False),
                              ("a", "所友", "newslist", True), ("n0", NORMAL[0], "page", True),
                              ("gone", "", "page", True)])
    assert got == [Again(False, None), Again(True, None), Again(True, None), Again(True, None), Again(False, None)]


def test_a_site_none_of_whose_pages_was_judged_has_no_count_and_an_old_corpus_is_named(tmp_path):
    paths = tm.tree_files(tmp_path / "tree")
    _, _, small, small_export = assembled(tmp_path / "small", site_rows(TEMPLATE_PAGES - 2))
    report = build.build_sites([(small, small_export)], tmp_path / "db" / "small.db", server_dates=None,
                               aliases=paths["aliases"], today=ti.TODAY)
    assert [s["under_read"] for s in report.sites] == [None]
    assert not any("判断过正文读漏" in w for w in report.warnings)  # too few pages: nothing to warn of
    conn = sqlite3.connect(f"file:{tmp_path / 'db' / 'small.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT under_read FROM sites").fetchall() == [(None,)]
    finally:
        conn.close()
    # a corpus assembled before item 22 carries no under_read: the build says so
    _, _, corpus, export = assembled(tmp_path / "old", site_rows())
    records = json.loads(corpus.read_text(encoding="utf-8"))
    corpus.write_text(json.dumps([{k: v for k, v in r.items() if k != "under_read"} for r in records],
                                 ensure_ascii=False), encoding="utf-8")
    report = build.build_sites([(corpus, export)], tmp_path / "db" / "old.db", server_dates=None,
                               aliases=paths["aliases"], today=ti.TODAY)
    assert [s["under_read"] for s in report.sites] == [None]
    assert any("没有一页判断过正文读漏" in w for w in report.warnings)
