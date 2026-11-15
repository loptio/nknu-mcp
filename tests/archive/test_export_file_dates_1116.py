"""A file a list row's button brought states that row's date and category in the export (the owner 11-16: 「请导出给
按钮带来的档案行填同样的栏：网页上写的日期、日期原文、日期来源＝列表上的日期，以及 列表上的类别，栏名和写法跟网页的完全
一样。」; the content session, relayed by the owner: 「建库现在只给公告和网页读「网页上写的日期／日期原文／日期来源」，
附件不读，内容这边会补」). An SSO unit site's file tables (法令規章, 表單下載, 會議紀錄 …) write each file as a row: its
category, its name, a download button, a count, a date. The platform's rows read the date and the category
(profiles: webidx_items), but the export gave them to pages alone (Writer._list_dates since 11-09: a notice a row's
press brought, any page a row links), and the school's server states no Last-Modified for such a download, so the file
came out with no date at all: 966 rows of 36 sites in the exports of 11-15 (820 of /web/ sites, 146 of /site/ sites;
read only on the lab, research/crawler_design_2026-11-15/file_dates3_1115.out). How it is done 【Claude 的执行办法】: by
the press's row key, as a pressed notice goes; a file a page links plainly is not this (not asked). Made-up pages in
the template's shape, no school text."""

from __future__ import annotations

from nknu.archive import export, postback, profiles
from tests.archive.test_export_1027 import _action, _res, by_url, make_archive, rows_of

UNIT = "sso.nknu.edu.tw/web/592"
LIST_URL = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=592&menuid=14"
ROW = {"host": "sso.nknu.edu.tw", "site": UNIT, "owner_site": UNIT}
PDF = b"%PDF-1.4 made up\n"


def table(rows: list[tuple[str, str, str]], extra: str = "") -> str:
    """A unit site's file table (made up, in the template's shape: a row is its category, its name, a download
    button that posts back, a count and a date): (category, name, date) each row."""
    out = "".join(
        f'<div class="items-row"><div class="col-cat">{cat}</div><div class="col-title">{name}</div>'
        f'<div class="col-dowload"><a id="ucAllTypeList_rptItems_ctl{n:02d}_lnkDownloadFile" '
        f"href=\"javascript:__doPostBack('ucAllTypeList$rptItems$ctl{n:02d}$lnkDownloadFile','')\"></a></div>"
        f'<div class="col-hits">12</div><div class="col-date">{day}</div></div>'
        for n, (cat, name, day) in enumerate(rows, start=1))
    return ('<html><head><title>測試學系</title></head><body><form id="form1" method="post" action="default.aspx">'
            '<input type="hidden" name="__VIEWSTATE" value="x"><div class="items-list-wrapper">' + out + "</div>"
            + extra + "</form></body></html>")


def _list(arch, name: str, url: str, html: str) -> tuple[int, list[postback.Press]]:
    """The list page as the crawler archived it, and its rows' download buttons as the crawler read them."""
    lid = arch.page(name, url, html, page_role="list", **ROW)
    pr = postback.read_page(profiles.SSO_WEBIDX, url, html, site=UNIT, list_url=url)
    return lid, [p for p in pr.presses if p.kind == "download"]


def _pressed(arch, lid: int, url: str, press: postback.Press, filename: str, **cols) -> int:
    """The file a press of that button brought, as the crawler keeps it: an action row of the list with the row's
    key, its answer archived at the list's address."""
    off, sha = arch.put(f"file-{press.row_key}", url, "application/pdf", PDF + filename.encode())
    aid = _action(arch.f, lid, kind="download", row_key=press.row_key, row_text=press.text, target=press.target,
                  argument=press.argument, sniff="pdf", ctype="application/pdf", bytes=len(PDF) + len(filename.encode()),
                  sha256=sha, warc=arch.rel, warc_offset=off, filename=filename, host="sso.nknu.edu.tw", site=UNIT,
                  profile="sso_webidx/webidx_items", **cols)
    _res(arch.f, lid, f"pb:download:{press.row_key}", "download", how="postback", action_id=aid, text=press.text)
    return aid


def _export(arch, tmp_path, site: str = UNIT) -> dict[str, dict[str, str]]:
    out = tmp_path / "out"
    export.export_site(arch.root, site, out)
    return by_url(rows_of(out / "manifest.csv"))


def _stated(row: dict[str, str]) -> tuple[str, str, str, str]:
    return row["网页上写的日期"], row["日期原文"], row["日期来源"], row["列表上的类别"]


def test_the_buttons_of_the_fixture_are_download_presses_keyed_by_their_rows():
    """The fixture is read as the crawler reads the template: one download press a row, each with its row's key."""
    html = table([("法令規章", "學生請假辦法", "2026-02-26"), ("表單下載", "請假單", "2025-09-01")])
    pr = postback.read_page(profiles.SSO_WEBIDX, LIST_URL, html, site=UNIT, list_url=LIST_URL)
    presses = [p for p in pr.presses if p.kind == "download"]
    assert len(presses) == 2 == len(pr.rows) and [p.row_key for p in presses] == [r.key for r in pr.rows]
    assert [(r.date, r.category) for r in pr.rows] == [("2026-02-26", "法令規章"), ("2025-09-01", "表單下載")]


def test_a_file_a_rows_button_brought_states_the_rows_date_and_category(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    html = table([("法令規章", "學生請假辦法", "2026-02-26"), ("表單下載", "請假單", "2025-09-01")])
    lid, (first, second) = _list(arch, "table", LIST_URL, html)
    _pressed(arch, lid, LIST_URL, first, "學生請假辦法.pdf")
    _pressed(arch, lid, LIST_URL, second, "請假單.pdf")
    arch.close()
    rows = _export(arch, tmp_path)
    files = [r for r in rows.values() if r["类型"] == "附件"]
    assert len(files) == 2 and all(r["本地档案"] and r["按钮编号"] for r in files)
    by_name = {r["标题"]: r for r in files}
    # the same four cells, written as a page's are (the owner: 「栏名和写法跟网页的完全一样」)
    assert _stated(by_name["學生請假辦法.pdf"]) == ("2026-02-26", "2026-02-26", export.LIST_ROW, "法令規章")
    assert _stated(by_name["請假單.pdf"]) == ("2025-09-01", "2025-09-01", export.LIST_ROW, "表單下載")
    assert export.LIST_ROW == "列表上的日期"
    # the list page itself states none of its rows' dates, as before
    assert _stated(rows[LIST_URL])[:3] == ("", "", "")


def test_a_row_with_no_date_gives_its_file_the_category_alone(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    lid, (press,) = _list(arch, "table", LIST_URL, table([("會議紀錄", "系務會議紀錄", "")]))
    _pressed(arch, lid, LIST_URL, press, "系務會議紀錄.pdf")
    arch.close()
    (file,) = [r for r in _export(arch, tmp_path).values() if r["类型"] == "附件"]
    assert _stated(file) == ("", "", "", "會議紀錄")


def test_each_file_takes_its_own_rows_date_on_every_page_of_the_table(tmp_path):
    """The table's second page is another address of its own (a get walk: &pagenum=2), its rows' buttons pressed
    there: a file is dated by the row of the page its button stood on, never by a row of another page."""
    arch = make_archive(tmp_path / "archive").open()
    lid, (one,) = _list(arch, "p1", LIST_URL, table([("招生專區", "招生簡章", "2026-01-05")]))
    url2 = LIST_URL + "&pagenum=2"
    lid2, (two,) = _list(arch, "p2", url2, table([("招生專區", "報名表", "2024-03-27")]))
    _pressed(arch, lid, LIST_URL, one, "招生簡章.pdf")
    _pressed(arch, lid2, url2, two, "報名表.pdf")
    arch.close()
    by_name = {r["标题"]: r for r in _export(arch, tmp_path).values() if r["类型"] == "附件"}
    assert _stated(by_name["招生簡章.pdf"])[0] == "2026-01-05" and _stated(by_name["報名表.pdf"])[0] == "2024-03-27"
    assert by_name["招生簡章.pdf"]["网址"].startswith(LIST_URL + "#按钮=")
    assert by_name["報名表.pdf"]["网址"].startswith(url2 + "#按钮=")


def test_a_file_whose_row_the_page_no_longer_shows_and_a_file_never_written_state_no_date(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    lid, (press,) = _list(arch, "table", LIST_URL, table([("法令規章", "學生請假辦法", "2026-02-26")]))
    gone = postback.Press("download", press.target.replace("ctl01", "ctl09"), "", "no-such-row", "舊檔", 1)
    _pressed(arch, lid, LIST_URL, gone, "舊檔.pdf")
    # a press that failed: the row is listed with no file
    aid = _action(arch.f, lid, kind="download", row_key=press.row_key, row_text=press.text, target=press.target,
                  state="failed", status=500, host="sso.nknu.edu.tw", site=UNIT, profile="sso_webidx/webidx_items")
    _res(arch.f, lid, f"pb:download:{press.row_key}", "download", how="postback", action_id=aid, text=press.text)
    arch.close()
    rows = [r for r in _export(arch, tmp_path).values() if r["类型"] == "附件"]
    old = next(r for r in rows if r["标题"] == "舊檔.pdf")
    assert _stated(old) == ("", "", "", "")
    failed = next(r for r in rows if not r["本地档案"])
    assert _stated(failed)[:3] == ("", "", "") and failed["列表上的类别"] == "法令規章"  # as a page with no file


def test_a_file_a_page_links_plainly_is_as_before(tmp_path):
    """Not asked: only a file a row's button brought. A file a table's row links by a plain address keeps its cells
    blank (the content stage dates it by the page that links it or by the server's Last-Modified, as before)."""
    arch = make_archive(tmp_path / "archive").open()
    link = "https://sso.nknu.edu.tw/userfiles/592/form.pdf"
    html = table([("表單下載", '<a href="' + link + '">申請表</a>', "2026-02-26")])
    lid = arch.page("table", LIST_URL, html, page_role="list", **ROW)
    fid = arch.file("form", link, PDF, "application/pdf", **ROW)
    _res(arch.f, lid, link, "file", res_id=fid, text="申請表")
    arch.close()
    (file,) = [r for r in _export(arch, tmp_path).values() if r["类型"] == "附件"]
    assert file["本地档案"] and _stated(file) == ("", "", "", "")
