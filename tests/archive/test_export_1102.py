"""The export's two fixes of 11-02 (plan 2026-11-02-pipeline T1; spec 2026-11-02-project-architecture §3.1 item 1,
技术总览 §7). A: a c.nknu notice states its own date in a field of the platform (公告日期, #lbl_AddTime), outside the
notice's text area; the export looked in the text first, where an exam's date or 「榜單公告115年12月30日」 came before
it (79 of the 501 教务处 notices whose field holds a date). B: a file another site owns, linked from the site's pages
and not in the archive yet (the 43 the 教务处 notices link to), was a bare 还在排队 row; it now says whose it is. It is
never fetched: the export reads the archive only. The notices are made up; the one name is O-masked."""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import export, profiles, progress
from tests.archive.test_export_1027 import BASE, SITE, _add, _export, _res, make_archive, rows_of

H5 = '<h5>發佈人員：教務處｜公告日期：<span id="lbl_AddTime">2026/10/22 上午 10:49:00</span></h5>'
NOTICE = ('<html><body><h2>最新消息</h2><span id="lbl_Title">115學年度碩士班考試放榜</span>' + H5
          + '<div id="lbl_Contents">考試日期：116年01月11日<br>榜單公告115年12月30日，王O明同學請洽教務處。</div>'
          '</body></html>')
NOTICE_URL = BASE + "newsdetail.aspx?nid=663&gi=4"
NEWS = "news.nknu.edu.tw"
NEWS_PDF = f"https://{NEWS}/uploadfile/news/ab12/list.pdf"
LIST_PDF = b"%PDF-1.4 a list of admitted students\n%%EOF\n"


def _notice_export(tmp_path, body: str):
    arch = make_archive(tmp_path / "archive").open()
    arch.page("notice", NOTICE_URL, body)
    arch.close()
    return _export(arch, tmp_path)


# ---------------------------------------------------------------- A: the notice's own date field first


def test_a_cnknu_notice_takes_its_date_field_before_dates_in_its_text(tmp_path):
    _got, _out, rows, readme = _notice_export(tmp_path, NOTICE)
    row = rows[NOTICE_URL]
    assert row["网页上写的日期"] == "2026-10-22" and row["日期原文"].startswith("公告日期")
    assert row["日期原文"] == "公告日期：2026/10/22"  # the words as the page writes them, label included
    assert row["标题"] == "115學年度碩士班考試放榜"
    # the README says where a notice's date comes from, in plain words (review 11-08: no CSS ids for the owner)
    assert "c.nknu 公告页上「公告日期」那一栏" in readme and "#lbl_AddTime" not in readme


def test_a_notice_without_its_date_field_falls_back_to_its_text(tmp_path):
    # the old rule: a 公告 label first (「榜單公告115年12月30日」), before a bare 日期 (考試日期)
    _got, _out, rows, _readme = _notice_export(tmp_path, NOTICE.replace(H5, ""))
    assert rows[NOTICE_URL]["网页上写的日期"] == "2026-12-30"
    assert rows[NOTICE_URL]["日期原文"] == "公告115年12月30日"


def test_a_notice_whose_date_field_is_empty_falls_back_to_its_text(tmp_path):
    # the 27 deleted notices of the 教务处 archive: <h5>發佈人員：｜公告日期：<span id="lbl_AddTime"></span></h5>
    blank = NOTICE.replace(H5, '<h5>發佈人員：<span id="lbl_Editor"></span>｜公告日期：'
                                '<span id="lbl_AddTime"></span></h5>')
    assert export.own_date(LexborHTMLParser(blank), NOTICE_URL, SITE) is None
    _got, _out, rows, _readme = _notice_export(tmp_path, blank)
    assert rows[NOTICE_URL]["网页上写的日期"] == "2026-12-30"


def test_own_date_only_for_the_platforms_detail_pages():
    tree = LexborHTMLParser(NOTICE)
    assert export.own_date(tree, BASE + "newsdetail.aspx?nid=663", SITE) == ("2026-10-22", "公告日期：2026/10/22")
    assert export.own_date(tree, BASE + "NewsDetail.aspx?Nid=663", SITE) == ("2026-10-22", "公告日期：2026/10/22")
    assert export.own_date(LexborHTMLParser(NOTICE), BASE + "page.aspx?pn=28", SITE) is None
    assert export.own_date(LexborHTMLParser(NOTICE), BASE + "newsview.aspx?nid=1", SITE) is None  # no date field
    assert export.own_date(LexborHTMLParser(NOTICE), "https://www.example.test/newsdetail.aspx",
                           "www.example.test") is None


def test_the_platforms_date_field_is_declared_on_its_detail_page():
    news, view = profiles.CNKNU.details
    # newsview's field too since 11-08 (总务处、体育室 …; tecs writes it with no DataFormView_ prefix)
    assert news.date == "#lbl_AddTime" and view.date == "#DataFormView_AddTimeLabel, #AddTimeLabel"
    assert profiles.Detail(r"/x\.aspx", text="#body").date == ""  # no date field unless a profile names one


# ---------------------------------------------------------------- B: a file another site owns


def test_b_an_off_site_file_in_the_archive_is_exported(tmp_path):
    # characterization (passes before the change too): bug B's note never touches a file the archive holds
    arch = make_archive(tmp_path / "archive").open()
    uid = arch.file("news", NEWS_PDF, LIST_PDF, "application/pdf", host=NEWS, site=NEWS, owner_site=NEWS)
    _res(arch.f, arch.ids["d1"], NEWS_PDF, "file", res_id=uid, text="錄取名單")
    arch.close()
    _got, out, rows, _readme = _export(arch, tmp_path)
    row = rows[NEWS_PDF]
    assert row["本地档案"].startswith("files/") and (out / row["本地档案"]).read_bytes() == LIST_PDF
    assert row["分类"] == progress.BUCKET_ZH["got"] and "归属别的网站" not in row["备注"]
    assert NEWS_PDF not in {r["网址"] for r in rows_of(out / "missing.csv")}


def test_b_an_off_site_file_not_in_the_archive_is_listed_with_its_owner(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    uid = _add(arch.f, url=NEWS_PDF, host=NEWS, site=NEWS, owner_site=NEWS, kind="file", kind0="file", tier=2,
               state="pending", status=None, fetched_at=None)
    _res(arch.f, arch.ids["d1"], NEWS_PDF, "file", res_id=uid, text="錄取名單")
    _add(arch.f, url=BASE + "todo.aspx", state="pending", status=None, fetched_at=None)  # the site's own: no note
    arch.close()
    got, out, rows, _readme = _export(arch, tmp_path)
    row = rows[NEWS_PDF]
    assert row["本地档案"] == "" and row["分类"] == progress.BUCKET_ZH["queued"]
    assert "归属别的网站（news.nknu.edu.tw）" in row["备注"]
    assert row["备注"] == "归属别的网站（news.nknu.edu.tw），存档里还没有它：只爬教务处时不抓，跟着那个网站的进度抓"
    missing = {r["网址"]: r for r in rows_of(out / "missing.csv")}
    assert NEWS_PDF in missing and missing[NEWS_PDF]["备注"] == row["备注"]
    assert rows[BASE + "todo.aspx"]["分类"] == progress.BUCKET_ZH["queued"]
    assert "归属别的网站" not in rows[BASE + "todo.aspx"]["备注"]
    assert got.left_other == 1
