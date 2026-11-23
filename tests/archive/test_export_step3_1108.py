"""The export's step-3 additions (spec 2026-11-08 §四 row 3 and 附 导出; the owner 11-08 【用户】: 「先做导出，别让内容管线
等」; how each is done is Claude's). Four columns at the end of the manifest: 按钮编号 (a press's row_key), 最后修改时间
(the archived answer's Last-Modified, a Drive file's modified), 日期来源 and 来源; a page strict decoding refused read
again with another encoding (item ⑧ of the 11-08 gaps); whether a site can be exported now and its data time
(`nknu archive ready`, manifest.json's 可以导出 and 资料截至); --reuse, the attachments and pictures of a previous
export of the site hard-linked instead of read out of the WARC again; and --old-copies, the rows this round got nothing
good of filled from the 10-24 archive's copies (item ⑦; the notices' dates are test_export_dates_1108's). Built on the
small made-up archive of test_export_1027 (its one name is O-masked)."""

from __future__ import annotations

import codecs
import errno
import hashlib
import json
import os
import sqlite3

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import export, outcome
from nknu.archive.fetch import DecodeError, decode_body
from tests.archive.test_export_1027 import (
    BASE,
    DRIVE_ID,
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

OLD_COLUMNS = ("序号", "类型", "网址", "最后落地网址", "本地档案", "标题", "网页上写的日期", "日期原文", "完整度", "缺什么",
               "结果", "原因", "分类", "原因代码", "抓取时间", "大小", "sha256", "格式", "链到它的网页", "链接文字", "怎么来的",
               "备注", "存档编号")
# the fixture's latest fetch: its presses' done (1793070000.0), after its url rows' fetched_at (09:02:03 Taipei)
AS_OF = "2026-10-27T11:00:00+08:00"
AFFAIR = "c.nknu.edu.tw/affair"
LAST_MODIFIED = "Tue, 04 Mar 2025 02:20:30 GMT"
NOTE_PDF = b"%PDF-1.4 a made-up notice attachment\n%%EOF\n"


def _with(tmp_path, build):
    """The fixture archive with build(arch) added (its frontier.db and WARC open meanwhile), exported."""
    arch = make_archive(tmp_path / "archive").open()
    build(arch)
    arch.close()
    return arch, *_export(arch, tmp_path)


def _manifest(out) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the columns


def test_four_columns_are_added_at_the_end_and_the_others_stay_where_they_were(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, out, rows, _readme = _export(arch, tmp_path)
    assert export.COLUMNS[:len(OLD_COLUMNS)] == OLD_COLUMNS
    # item 22's two came after them (test_export_item22_1109)
    assert export.COLUMNS[len(OLD_COLUMNS):len(OLD_COLUMNS) + 4] == ("按钮编号", "最后修改时间", "日期来源", "来源")
    with open(out / "manifest.csv", encoding="utf-8-sig") as fh:
        assert tuple(fh.readline().rstrip("\r\n").split(",")) == export.COLUMNS
    data = _manifest(out)
    assert data["栏位"] == list(export.COLUMNS) and all(list(r) == list(export.COLUMNS) for r in data["项目"])
    assert {r["来源"] for r in rows.values()} == {""}  # this round's archive: the 10-24 one is the next step's
    assert "按钮编号" in (out / "README.md").read_text(encoding="utf-8")


def test_a_press_brought_row_carries_its_row_key_and_any_other_row_none(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[BASE + "newslist.aspx#第2页"]["按钮编号"] == "p2"  # the pager's
    assert rows[BASE + "newslist.aspx#按钮=寒假宿舍申請"]["按钮编号"] == "k9"  # a detail press
    assert rows[BASE + "newslist.aspx#按钮=成績單申請表"]["按钮编号"] == "k8"  # a download press
    for url in (BASE + "newslist.aspx", BASE + "newsview.aspx?nid=1", BASE + "uploadfile/rule.pdf",
                BASE + "images/banner.jpg", BASE + "uploadfile/broken.pdf"):
        assert rows[url]["按钮编号"] == "", url


def test_a_press_the_export_only_accounts_for_keeps_its_row_key_too(tmp_path, monkeypatch):
    arch = make_archive(tmp_path / "archive")
    monkeypatch.setattr(export.Builder, "_presses", lambda self: None)  # every press then listed by _account
    _got, out, _rows, _readme = _export(arch, tmp_path)
    gaps = {r["存档编号"]: r["按钮编号"] for r in rows_of(out / "manifest.csv") if "导出程式没处理到" in r["备注"]}
    assert gaps == {f"a{arch.ids['pager']}": "p2", f"a{arch.ids['detail']}": "k9", f"a{arch.ids['download']}": "k8"}


# ---------------------------------------------------------------- 最后修改时间


@pytest.mark.parametrize(("value", "want"), [
    (LAST_MODIFIED, "2025-03-04T10:20:30+08:00"),  # IMF-fixdate (RFC 7231)
    ("Tuesday, 04-Mar-25 02:20:30 GMT", "2025-03-04T10:20:30+08:00"),  # the obsolete RFC 850 form
    ("Tue Mar  4 02:20:30 2025", "2025-03-04T10:20:30+08:00"),  # asctime: no zone, UTC as every HTTP date
    ("Tue, 04 Mar 2025 02:20:30 -0000", "2025-03-04T10:20:30+08:00"),
    ("Tue, 04 Mar 2025 10:20:30 +0800", "2025-03-04T10:20:30+08:00"),
    ("2025-10-28T03:12:38.886Z", "2025-10-28T11:12:38+08:00"),  # a Drive item's modified (RFC 3339)
    ("Thu, 01 Jan 1970 00:00:00 GMT", ""),  # a server that does not know
    ("Mon, 32 Mar 2025 02:20:30 GMT", ""),
    ("yesterday", ""),
    ("", ""),
    (None, ""),
])
def test_changed_at(value, want):
    assert export.changed_at(value) == want


def test_the_archived_answers_last_modified_is_the_files_last_modified_time(tmp_path):
    def build(arch):
        for name, header in (("note", LAST_MODIFIED), ("odd", "yesterday")):
            arch.file(name, BASE + f"uploadfile/{name}.pdf", NOTE_PDF.replace(b"notice", name.encode()),
                      "application/pdf", headers={"last-modified": header})
            _res(arch.f, arch.ids["about"], BASE + f"uploadfile/{name}.pdf", "file", res_id=arch.ids[name], text=name)

    _arch, _got, _out, rows, readme = _with(tmp_path, build)
    assert rows[BASE + "uploadfile/note.pdf"]["最后修改时间"] == "2025-03-04T10:20:30+08:00"
    assert rows[BASE + "uploadfile/odd.pdf"]["最后修改时间"] == ""  # a header that cannot be read: none
    assert rows[BASE + "uploadfile/rule.pdf"]["最后修改时间"] == ""  # no header
    assert rows[BASE + "uploadfile/broken.pdf"]["最后修改时间"] == ""  # not got
    assert "Last-Modified" in readme


def test_a_drive_files_own_modified_comes_before_the_answers_header(tmp_path):
    other = "1ZyXwVuTsRqPoNmLk"

    def build(arch):
        arch.f.x("UPDATE drive_item SET modified=? WHERE file_id=?", ("2025-10-28T03:12:38.886Z", DRIVE_ID))
        meta = arch.file("meta2", f"https://www.googleapis.com/drive/v3/files/{other}?fields=id", b"{}",
                         "application/json", kind="drive", sniff="json", host="www.googleapis.com",
                         site="www.googleapis.com")
        content = arch.file("drive2", f"https://www.googleapis.com/drive/v3/files/{other}?alt=media", NOTE_PDF,
                            "application/pdf", host="www.googleapis.com", site="www.googleapis.com",
                            found_via="drive", headers={"last-modified": LAST_MODIFIED})
        arch.f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size, modified, content_id) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (meta, other, "報名表.pdf", "application/pdf", len(NOTE_PDF), "2024-01-02T03:04:05Z", content))
        _res(arch.f, arch.ids["about"], f"drive:{other}", "drive", how="drive_api", res_id=content, text="報名表")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    drive = rows[f"https://www.googleapis.com/drive/v3/files/{DRIVE_ID}?alt=media"]
    assert drive["最后修改时间"] == "2025-10-28T11:12:38+08:00" and drive["本地档案"].startswith("files/")
    assert rows[f"https://www.googleapis.com/drive/v3/files/{other}?alt=media"]["最后修改时间"] == \
        "2024-01-02T11:04:05+08:00"


# ---------------------------------------------------------------- 日期来源


RULES = ("<html><head><title>國立高雄師範大學教務處</title></head><body><h2>學則</h2><p>更新日期：2025/08/01</p>"
         "</body></html>")
# a second list of the site: the notice with its own date field, and a notice whose page states its date in its words
LIST2 = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>招生公告</h2><table id="MasterGrid">
<tr><th>日期</th><th>主題</th></tr>
<tr><td>2025/01/01</td><td><a href="newsdetail.aspx?nid=663&gi=4">115學年度碩士班考試放榜</a></td></tr>
<tr><td>2025/09/29</td><td><a href="newsview.aspx?nid=5">轉學考簡章</a></td></tr></table></body></html>"""
D5 = """<html><head><title>國立高雄師範大學教務處</title></head><body><div id="DataFormView">
<span id="DataFormView_TitleLabel">轉學考簡章</span><p>公告日期：2025年9月30日</p></div></body></html>"""


def test_the_date_says_which_rule_found_it(tmp_path):
    def build(arch):
        arch.page("notice", NOTICE_URL, NOTICE)  # c.nknu's newsdetail: its 公告日期 field (#lbl_AddTime)
        arch.page("rules", BASE + "rules.aspx", RULES)  # no list's row: its words
        moved = BASE + "news.aspx?id=1"  # a redirect landing on a dated page shares its file and its date's source
        off, sha = arch.put("moved", moved, "text/html", b"moved", headers={"location": BASE + "newsview.aspx?nid=1"},
                            status=302)
        _add(arch.f, url=moved, final_url=BASE + "newsview.aspx?nid=1", status=302, bytes=5, sha256=sha,
             warc=arch.rel, warc_offset=off, ctype="text/html", sniff="html", page_type="t:ok")

    _arch, _got, _out, rows, readme = _with(tmp_path, build)
    assert (rows[NOTICE_URL]["网页上写的日期"], rows[NOTICE_URL]["日期来源"]) == ("2026-10-22", export.OWN_FIELD)
    # the fixture's notices have no date field: the list states their dates (P75)
    assert (rows[BASE + "newsview.aspx?nid=1"]["网页上写的日期"], rows[BASE + "newsview.aspx?nid=1"]["日期来源"]) == \
        ("2025-10-01", export.LIST_ROW)
    assert (rows[BASE + "rules.aspx"]["网页上写的日期"], rows[BASE + "rules.aspx"]["日期来源"]) == \
        ("2025-08-01", export.PAGE_WORDS)
    assert rows[BASE + "news.aspx?id=1"]["日期来源"] == export.LIST_ROW  # the landing page's
    for url in (BASE + "about.aspx", BASE + "newslist.aspx", BASE + "uploadfile/rule.pdf"):  # no date
        assert rows[url]["网页上写的日期"] == rows[url]["日期来源"] == "", url
    assert "日期来源" in readme and export.LIST_ROW in readme and export.OWN_FIELD in readme


def test_a_list_rows_date_is_taken_after_the_pages_own_field_and_before_its_words(tmp_path):
    """P75: the date a notice's row states on its list, between its page's own date field and its words."""
    def build(arch):
        arch.page("notice", NOTICE_URL, NOTICE)
        arch.page("d5", BASE + "newsview.aspx?nid=5", D5)
        arch.page("list2", BASE + "newslist.aspx?pclass=2", LIST2, page_role="list")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    notice, d5 = rows[NOTICE_URL], rows[BASE + "newsview.aspx?nid=5"]
    assert (notice["网页上写的日期"], notice["日期原文"], notice["日期来源"]) == \
        ("2026-10-22", "公告日期：2026/10/22", export.OWN_FIELD)  # its own field first: its row says 2025/01/01
    assert (d5["网页上写的日期"], d5["日期原文"], d5["日期来源"]) == ("2025-09-29", "2025/09/29", export.LIST_ROW)


# ---------------------------------------------------------------- item ⑧: a page whose charset fails


# a page that names Big5-HKSCS (嘅 is in Big5-HKSCS, not in cp950), served as UTF-8
HKSCS = ('<html><head><meta charset="big5-hkscs"><title>系所公告</title></head><body><h2>嘅課程說明</h2>'
         "<p>發佈日期：2025/10/01</p><p>請洽系辦，王O明。</p></body></html>")
SJIS = ("<html><head><meta http-equiv='Content-Type' content='text/html; charset=Shift_JIS'><title>招生考試公告</title>"
        "</head><body><h2>研究所招生考試公告</h2><p>公告日期：114/10/01</p><p>報名請洽系所，王O明。</p></body></html>")
# UTF-8 with a byte order mark, and a comment in Big5 inside (英语系's English pages, 11-08)
MIXED = codecs.BOM_UTF8 + (b"<html><head><title>Curriculum</title></head><body><!-- "
                           + "選單".encode("cp950") + " --><h2>課程規劃</h2></body></html>".encode())
# a Big5 page whose server and meta say big5, with a character the school made (造字, in Big5's user-defined area
# 0xFA40-0xFEFE: no Big5 reads it; Big5-HKSCS reads a Hong Kong character there) in place of 王
EUDC_PAGE = ('<html><head><meta charset="big5"><title>系所公告</title></head><body><h2>招生說明會</h2>'
             "<p>發佈日期：2025/10/01</p><p>聯絡人：王O明。</p></body></html>")
EUDC = EUDC_PAGE.encode("cp950").replace("王".encode("cp950"), b"\xfe\xa1")
BIG5 = "text/html; charset=big5"


def test_the_fixtures_are_what_the_crawler_cannot_decode():
    for body, ctype in ((HKSCS.encode("big5hkscs"), HTML), (SJIS.encode("cp932"), "text/html"),
                        (MIXED, "text/html;charset=utf-8"), (EUDC, BIG5)):
        with pytest.raises(DecodeError):
            decode_body(body, ctype)


def test_redecode_reads_with_an_encoding_the_page_names():
    assert export.redecode(SJIS.encode("cp932")) == (SJIS, "cp932", "网页自己写的编码是 shift_jis", 0)
    assert export.redecode(HKSCS.encode("big5hkscs"), HTML) == (HKSCS, "big5hkscs", "网页自己写的编码是 big5-hkscs", 0)
    hk = HKSCS.replace('<meta charset="big5-hkscs">', "").replace("嘅", "課")  # Big5 bytes, the server says big5
    assert export.redecode(hk.encode("cp950"), BIG5) == (hk, "cp950", "服务器说的编码是 big5", 0)


def test_redecode_never_guesses_an_encoding_nothing_names():
    # a 造字 in a name: one � where it was, the rest of the page and the O after it as they were (review 11-08: gb18030
    # read the whole page into mojibake, Big5-HKSCS gave it another character)
    text, encoding, said, lost = export.redecode(EUDC, BIG5)
    assert (encoding, said, lost) == ("cp950", "网页自己写的编码是 big5", 1)
    assert text == EUDC_PAGE.replace("王", "�")
    assert EUDC.decode("cp950", "replace") != text  # Python's own replace takes the O with the 造字
    assert "�" not in EUDC.decode("gb18030") and "�" not in EUDC.decode("big5hkscs")  # what was never tried
    # a page that names nothing that reads it: the crawler's own Big5 or UTF-8, whichever loses fewer
    bare = HKSCS.replace('<meta charset="big5-hkscs">', "")
    text, encoding, said, lost = export.redecode(bare.encode("big5hkscs"), HTML)
    assert (text, encoding, said, lost) == (bare.replace("嘅", "�"), "cp950", "", 1)
    sjis = SJIS.encode("cp932").replace(b"charset=Shift_JIS", b"charset=x-unknown")
    assert export.redecode(sjis)[1] in export.DEFAULTS and export.redecode(sjis)[3] > 0  # no gb18030 guess
    # a byte order mark says UTF-8 whatever else: each broken sequence a �
    assert export.redecode(MIXED, BIG5) == (MIXED.decode("utf-8", "replace")[1:], "utf-8", "",
                                            MIXED.decode("utf-8", "replace").count("�"))
    assert export.redecode(b"<p>\x80\xff</p>") == ("<p>��</p>", "utf-8", "", 2)


def test_a_page_whose_charset_fails_is_read_again_and_the_readme_counts_both_kinds(tmp_path):
    def build(arch):
        arch.page("hk", BASE + "hk.aspx", HKSCS.encode("big5hkscs"), ctype=HTML, flags="decode")
        arch.page("sjis", BASE + "sjis.htm", SJIS.encode("cp932"), ctype="text/html", flags="decode")
        arch.page("eudc", BASE + "eudc.aspx", EUDC, ctype=BIG5, flags="decode")
        arch.page("mixed", BASE + "mixed.html", MIXED, ctype="text/html;charset=utf-8", flags="decode")

    _arch, _got, out, rows, readme = _with(tmp_path, build)
    hk, sjis = rows[BASE + "hk.aspx"], rows[BASE + "sjis.htm"]
    eudc, mixed = rows[BASE + "eudc.aspx"], rows[BASE + "mixed.html"]
    assert (out / hk["本地档案"]).read_bytes() == codecs.BOM_UTF8 + HKSCS.encode()  # UTF-8 with a BOM, word for word
    assert "原本的编码读不通，按繁体中文 Big5 加香港增补字（big5hkscs；网页自己写的编码是 big5-hkscs）重读" in hk["备注"]
    assert hk["标题"] == "嘅課程說明" and hk["网页上写的日期"] == "2025-10-01"
    assert (out / sjis["本地档案"]).read_text(encoding="utf-8-sig") == SJIS
    assert "原本的编码读不通，按日文 Shift_JIS（cp932；网页自己写的编码是 shift_jis）重读" in sjis["备注"]
    assert sjis["标题"] == "研究所招生考試公告" and sjis["网页上写的日期"] == "2025-10-01"
    # the 造字: its title and date are read, its file has one �, and 备注 says so in plain words
    assert (out / eudc["本地档案"]).read_text(encoding="utf-8-sig") == EUDC_PAGE.replace("王", "�")
    assert eudc["标题"] == "招生說明會" and eudc["网页上写的日期"] == "2025-10-01"
    assert ("原本的编码读不通，没有一种编码整页读得通：按繁体中文 Big5（cp950；网页自己写的编码是 big5）读，有 1 个字读不出来，"
            "用 � 代替") in eudc["备注"]
    # 英语系's case: the broken characters are all in a comment, which no reader sees
    bad = MIXED.decode("utf-8", "replace").count("�")
    assert bad and (out / mixed["本地档案"]).read_text(encoding="utf-8-sig") == MIXED.decode("utf-8", "replace")[1:]
    assert (f"原本的编码读不通，按UTF-8重读：看得到的文字都读通了，只有网页的注释、链接网址这类看不到的地方有 {bad} 个字读不出来，"
            "用 � 代替") in mixed["备注"]
    # read whole where a reader sees it (asked by the content session 11-08): the page the school sent, 抓到了, judged
    # as a page that came back; the dashboard still says 编码有问题, and 备注 says so
    for r in (hk, sjis, mixed):
        assert (r["结果"], r["分类"], r["完整度"], r["缺什么"]) == ("抓到了", "抓到了", "完整", ""), r
        assert r["原因代码"] == "decode" and "看板上的结果是「编码有问题」：导出时读通了" in r["备注"]
    # a � where a reader sees it (the 造字 in a name): the views' judgement stays
    assert (eudc["结果"], eudc["分类"], eudc["完整度"]) == ("编码有问题", "要人看", "不完整")
    assert "看板上" not in eudc["备注"]
    assert ("有 3 页原本的编码读不通（看板上的结果是「编码有问题」），按网页或服务器说的编码重读，看得到的文字都读通了："
            "繁体中文 Big5 加香港增补字（big5hkscs） 1 页、日文 Shift_JIS（cp932） 1 页、UTF-8 1 页（其中 1 页只在网页的注释、"
            "链接网址这类看不到的地方有读不出来的字，用 � 代替）。这些页的结果、分类、完整度照读通的网页算") in readme
    assert ("有 1 页原本的编码读不通，没有一种编码读得通看得到的文字：照读不出来的字最少的那一种读（繁体中文 Big5（cp950） 1 页），"
            "一共 1 个字读不出来，用 � 代替；结果照旧是「编码有问题」、分类「要人看」") in readme
    assert "gb18030" not in readme and "不会改用网页和服务器都没说的编码去猜" in readme
    assert rows[BASE + "about.aspx"]["备注"] == ""  # a page that decodes says nothing
    assert not (out / "missing.csv").read_text(encoding="utf-8-sig").count(BASE + "hk.aspx")


def test_a_page_read_whole_is_judged_as_a_page_that_came_back_with_what_it_lacks(tmp_path):
    """A page read whole after its charset failed still lacks what it lacks (a failed attachment: 部分完整, 缺附件), and
    a redirect landing on it, judged by the views as that page, is judged again with it."""
    def build(arch):
        sjis = arch.page("sjis", BASE + "sjis.htm", SJIS.encode("cp932"), ctype="text/html", flags="decode")
        _res(arch.f, sjis, BASE + "uploadfile/broken.pdf", "file", res_id=arch.ids["broken"], text="附件二")
        moved = BASE + "old/sjis.htm"
        off, sha = arch.put("moved", moved, "text/html", b"moved", headers={"location": BASE + "sjis.htm"}, status=301)
        arch.ids["moved"] = _add(arch.f, url=moved, final_url=BASE + "sjis.htm", status=301, bytes=5, sha256=sha,
                                 warc=arch.rel, warc_offset=off, ctype="text/html", sniff="html", page_type="t:ok")

    arch, _got, _out, rows, _readme = _with(tmp_path, build)
    sjis, moved = rows[BASE + "sjis.htm"], rows[BASE + "old/sjis.htm"]
    db = sqlite3.connect(f"{(arch.root / 'frontier.db').resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    view = {r["id"]: dict(r) for r in db.execute("SELECT * FROM page_completeness")}
    db.close()
    assert view[arch.ids["sjis"]]["own"] == "decode" and view[arch.ids["sjis"]]["level"] == "incomplete"
    assert (sjis["结果"], sjis["分类"], sjis["完整度"]) == ("抓到了", "抓到了", "部分完整")
    assert sjis["缺什么"] == "连接失败（1 个）、缺附件（1 个）"
    # the redirect: no row of its own in the view (its landing page is judged in its own right), so its category
    # and level are its landing page's, judged again with it; its 结果 stays its own
    assert arch.ids["moved"] not in view and moved["本地档案"] == sjis["本地档案"]
    assert (moved["结果"], moved["分类"], moved["完整度"], moved["缺什么"]) == \
        (outcome.RESULT_LABEL["redirect"], "抓到了", "部分完整", "连接失败（1 个）、缺附件（1 个）")
    assert "看板上落地那一页的结果是「编码有问题」：导出时读通了" in moved["备注"]


def test_judge_page_is_the_views_own_judgement_of_a_row():
    """outcome.judge_page works the view's level, label and reasons out of one of its rows: the same answer as the view
    for every page of the fixture, and with own changed, the answer for that."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        arch = make_archive(Path(tmp) / "archive").open()
        arch.page("sjis", BASE + "sjis.htm", SJIS.encode("cp932"), ctype="text/html", flags="decode")
        arch.close()
        db = sqlite3.connect(f"{(arch.root / 'frontier.db').resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        view = [dict(r) for r in db.execute("SELECT * FROM page_completeness")]
        db.close()
    assert len(view) >= 6
    for row in view:
        assert outcome.judge_page(row) == {k: row[k] for k in outcome.JUDGED}, row["id"]
    decode = next(r for r in view if r["own"] == "decode")
    about = next(r for r in view if r["own"] == "ok" and not r["reasons"].strip(","))
    assert outcome.judge_page(decode)["level"] == "incomplete"
    assert outcome.judge_page(decode, own="ok") == {k: about[k] for k in outcome.JUDGED}  # nothing else lacking


# ---------------------------------------------------------------- can it be exported now (archive ready)


def _second_site(arch, *, waiting: bool = True) -> None:
    """总务处 on the list too, with a row still waiting (a retry) when waiting."""
    arch.f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
             (AFFAIR, "总务处", "全校重要", "1", "c.nknu", "https://c.nknu.edu.tw/affair/newslist.aspx"))
    _add(arch.f, url="https://c.nknu.edu.tw/affair/newslist.aspx", site=AFFAIR, owner_site=AFFAIR,
         fetched_at="2026-10-28T02:00:00+00:00", page_type=None)
    if waiting:
        _add(arch.f, url="https://c.nknu.edu.tw/affair/newsview.aspx?nid=1", site=AFFAIR, owner_site=AFFAIR,
             state="pending", attempts=1, status=None, fetched_at=None)


def test_ready_says_which_sites_can_be_exported_and_their_data_time(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    _second_site(arch)
    arch.close()
    got, pid = export.ready(arch.root)
    assert pid is None and [r.site for r in got] == [SITE, AFFAIR]  # the dashboard's sites: the list's round 1
    aca, affair = got
    assert (aca.can, aca.urls, aca.presses, aca.as_of, aca.name) == (True, 0, 0, AS_OF, "教务处")
    assert (affair.can, affair.urls, affair.presses) == (False, 1, 0)  # a retry still waits
    assert affair.as_of == "2026-10-28T10:00:00+08:00"
    assert aca.state in export.progress.STATE_SHOW and aca.state != "unchecked"  # monitor.site_status's


def test_a_press_still_waiting_keeps_a_site_from_being_exported(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    _action(arch.f, arch.ids["list"], kind="detail", step=3, row_key="k77", row_text="還沒按的公告",
            state="pending", status=None, done=None)
    arch.close()
    (aca,), _pid = export.ready(arch.root)
    assert (aca.can, aca.urls, aca.presses) == (False, 0, 1)
    _got, out, _rows, readme = _export(arch, tmp_path)
    data = _manifest(out)
    assert data["可以导出"] is False and data["资料截至"] == AS_OF
    assert "可以导出：否（还有 0 个网址、1 个按钮在排队）" in readme


def test_the_manifest_says_whether_the_site_could_be_exported_and_its_data_time(tmp_path):
    arch = make_archive(tmp_path / "archive")
    got, out, _rows, readme = _export(arch, tmp_path)
    data = _manifest(out)
    assert data["可以导出"] is True and data["资料截至"] == AS_OF
    assert got.ready.can and f"资料截至：{AS_OF}" in readme and "可以导出：是" in readme
    assert any(line.startswith(f"资料截至 {AS_OF}；可以导出") for line in got.lines)


def test_an_archive_too_old_for_the_dashboard_is_exported_without_them(tmp_path, monkeypatch):
    """The 10-26 pilot's presses have no next_at, which progress._presses reads (export.NOT_READ)."""
    arch = make_archive(tmp_path / "archive")

    def old(*_a, **_kw):
        raise sqlite3.OperationalError("no such column: a.next_at")

    monkeypatch.setattr(export, "readiness", old)
    got, out, _rows, readme = _export(arch, tmp_path)
    data = _manifest(out)
    assert data["可以导出"] is False and data["资料截至"] == "" and got.ready is None
    assert "资料截至、可以导出：没算出来（OperationalError: no such column: a.next_at" in readme


def test_the_ready_command(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    _second_site(arch)
    arch.close()
    runner = CliRunner()
    r = runner.invoke(app, ["archive", "ready", "--dir", str(arch.root)])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0].startswith(f"✓ {SITE}（教务处）：可以导出；状态 ") and lines[0].endswith(f"资料截至 {AS_OF}")
    assert lines[1].startswith(f"… {AFFAIR}（总务处）：还没爬完，还有 1 个网址、0 个按钮在排队")
    assert lines[-1] == "共 2 个网站：可以导出 1，不能导出 1"
    r = runner.invoke(app, ["archive", "ready", "--dir", str(arch.root), "--json", "--sites",
                            f"{AFFAIR},https://c.nknu.edu.tw/aca/,c.nknu.edu.tw/nope"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["爬虫正在跑"] is False and [s["网站"] for s in data["网站"]] == [AFFAIR, SITE, "c.nknu.edu.tw/nope"]
    affair, aca, nope = data["网站"]
    assert aca == {"网站": SITE, "名称": "教务处", "可以导出": True, "状态": aca["状态"], "还在排队的网址": 0,
                   "还在排队的按钮": 0, "资料截至": AS_OF}
    assert affair["可以导出"] is False and affair["还在排队的网址"] == 1
    assert nope["可以导出"] is False and nope["状态"] == "存档里没有这个网站" and nope["资料截至"] == ""


def test_ready_reads_only(tmp_path):
    arch = make_archive(tmp_path / "archive")
    before = {p.name: p.read_bytes() for p in arch.root.iterdir() if p.is_file()}
    export.ready(arch.root)
    after = {p.name: p.read_bytes() for p in arch.root.iterdir() if p.is_file()}
    assert {k: after[k] for k in before} == before
    assert set(after) - set(before) <= {"frontier.db-wal", "frontier.db-shm"}


# ---------------------------------------------------------------- --reuse: hard links to the previous export


FILES = ("uploadfile/rule.pdf", "uploadfile/pic.jpg")


def _file_rows(rows: dict) -> dict:
    return {u: r for u, r in rows.items() if r["类型"] in ("附件", "图片") and r["本地档案"]}


def test_reuse_hard_links_the_same_attachments_and_pictures_and_writes_the_pages_again(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, rows1, _readme = _export(arch, tmp_path, "first")
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    second = tmp_path / "second"
    rows2 = by_url(rows_of(second / "manifest.csv"))
    linked = _file_rows(rows2)
    assert got.linked == len(linked) == 4  # the attachment, the Drive file, the download press's file, the picture
    for url, r in linked.items():
        new, old = os.stat(second / r["本地档案"]), os.stat(first / rows1[url]["本地档案"])
        assert new.st_ino == old.st_ino and new.st_nlink >= 2, url  # one file, two names
    for r in rows2.values():
        if r["类型"] in ("网页", "资料") and r["本地档案"]:  # pages and data are written again
            assert os.stat(second / r["本地档案"]).st_ino != os.stat(first / r["本地档案"]).st_ino
    assert _manifest(second)["项目"] == _manifest(first)["项目"]  # the same names, sizes, sha256, every column
    readme = (second / "README.md").read_text(encoding="utf-8")
    assert f"附件和图片有 4 个是从上一次导出（`{first.resolve()}`）硬链接过来的" in readme
    assert any("硬链接了 4 个附件和图片" in line for line in got.lines)


def test_reuse_keeps_the_last_modified_time_of_the_record(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    arch.file("note", BASE + "uploadfile/note.pdf", NOTE_PDF, "application/pdf",
              headers={"last-modified": LAST_MODIFIED,
                       "content-disposition": "attachment; filename*=UTF-8''%E5%A0%B1%E5%90%8D%E8%A1%A8.pdf"})
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/note.pdf", "file", res_id=arch.ids["note"], text="報名表")
    arch.close()
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    row = by_url(rows_of(tmp_path / "second" / "manifest.csv"))[BASE + "uploadfile/note.pdf"]
    assert row["最后修改时间"] == "2025-03-04T10:20:30+08:00" and row["本地档案"].endswith("-報名表.pdf")
    assert row["标题"] == "報名表.pdf"  # Content-Disposition's name, from the record's headers


def test_reuse_reads_from_the_archive_what_the_previous_export_does_not_have_as_it_was(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, rows1, _readme = _export(arch, tmp_path, "first")
    changed = first / rows1[BASE + "uploadfile/rule.pdf"]["本地档案"]
    changed.unlink()
    changed.write_bytes(b"%PDF-1.4 someone changed it\n")  # another size
    (first / rows1[BASE + "uploadfile/pic.jpg"]["本地档案"]).unlink()  # gone
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    rows2 = by_url(rows_of(tmp_path / "second" / "manifest.csv"))
    assert got.linked == 2
    for url, name in ((BASE + "uploadfile/rule.pdf", "rule"), (BASE + "uploadfile/pic.jpg", "pic")):
        path = tmp_path / "second" / rows2[url]["本地档案"]
        assert os.stat(path).st_nlink == 1 and hashlib.sha256(path.read_bytes()).hexdigest() == arch.sha[name]


def test_reuse_never_links_from_another_sites_export(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    data = _manifest(first)
    data["网站"] = AFFAIR
    (first / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    rows2 = by_url(rows_of(tmp_path / "second" / "manifest.csv"))
    assert got.linked == 0
    assert all(os.stat(tmp_path / "second" / r["本地档案"]).st_nlink == 1 for r in _file_rows(rows2).values())
    readme = (tmp_path / "second" / "README.md").read_text(encoding="utf-8")
    assert f"上一次导出（`{first.resolve()}`）没有用上：它是别的网站（{AFFAIR}）的导出，附件和图片都从存档读" in readme


def test_reuse_of_a_folder_with_no_manifest_reads_everything_from_the_archive(tmp_path):
    arch = make_archive(tmp_path / "archive")
    empty = tmp_path / "nothing"
    empty.mkdir()
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=empty)
    assert got.linked == 0 and any("读不到它的 manifest.json" in line for line in got.lines)
    assert len(list((tmp_path / "second" / "files").iterdir())) == 3


def test_reuse_falls_back_to_the_archive_when_the_file_system_refuses_a_link(tmp_path, monkeypatch):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")

    def refuse(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(export.os, "link", refuse)
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    assert got.linked == 0
    assert _manifest(tmp_path / "second")["项目"] == _manifest(first)["项目"]  # read from the archive, the same
    names = sorted(p.name for p in (tmp_path / "second" / "files").iterdir())
    assert names == sorted(p.name for p in (first / "files").iterdir())  # no number skipped


def test_the_export_command_takes_reuse(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    r = CliRunner().invoke(app, ["archive", "export", "--dir", str(arch.root), "--site", SITE, "--out",
                                 str(tmp_path / "second"), "--reuse", str(first)])
    assert r.exit_code == 0, r.output
    assert f"从上一次导出 {first.resolve()} 硬链接了 4 个附件和图片" in r.output


def test_the_archive_is_still_only_read(tmp_path):
    arch = make_archive(tmp_path / "archive")
    before = {str(p.relative_to(arch.root)): p.read_bytes() for p in arch.root.rglob("*") if p.is_file()}
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    after = {str(p.relative_to(arch.root)): p.read_bytes() for p in arch.root.rglob("*") if p.is_file()}
    assert {k: after[k] for k in before} == before
    assert set(after) - set(before) <= {"frontier.db-wal", "frontier.db-shm"}


# ---------------------------------------------------------------- item ⑦: the 10-24 archive's copies (--old-copies)


OLD_FETCHED = "2026-10-24T00:38:21+00:00"
OLD_PDF = b"%PDF-1.4 the second attachment as the 10-24 archive kept it\n%%EOF\n"
OLD_NOTICE = ("<html><head><title>國立高雄師範大學教務處</title></head><body><div id=\"DataFormView\">"
              "<span id=\"DataFormView_TitleLabel\">宿舍搬遷公告</span><p>更新日期：2025/08/01</p>"
              "<p>聯絡人：王O明。</p></div></body></html>")
PLIST = b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict/></plist>\n'
AS_NAMED = "attachment; filename*=UTF-8''%E9%99%84%E4%BB%B6%E4%BA%8C.pdf"  # 附件二.pdf


def _copies(folder, rows: dict, *, broken: tuple = ()) -> object:
    """An old-copies folder as research/crawler_lab_2026-11-04/old_copies_1108.py writes it: files/<sha256> and
    index.json, a row per this round's url id. broken: the ids whose file is left out."""
    (folder / "files").mkdir(parents=True)
    index = {}
    for uid, (body, cols) in rows.items():
        sha = hashlib.sha256(body).hexdigest()
        if uid not in broken:
            (folder / "files" / sha).write_bytes(body)
        index[str(uid)] = {"old_ref": uid, "url": "", "sha256": sha, "bytes": len(body), "ctype": "text/html",
                           "fetched_at": OLD_FETCHED, "last_modified": None, "content_disposition": None,
                           "file": f"files/{sha}", "this_round_result": "", "owner_site": SITE, **cols}
    (folder / "index.json").write_text(json.dumps({"made": OLD_FETCHED, "old_archive": "/x", "rows": index},
                                                  ensure_ascii=False), encoding="utf-8")
    return folder


def test_a_row_this_round_got_nothing_of_takes_the_old_copy_and_keeps_its_result(tmp_path):
    arch = make_archive(tmp_path / "archive")
    broken = BASE + "uploadfile/broken.pdf"  # an attachment that never connected
    old = _copies(tmp_path / "old", {arch.ids["broken"]: (OLD_PDF, {
        "url": broken, "ctype": "application/pdf", "last_modified": LAST_MODIFIED, "content_disposition": AS_NAMED,
        "this_round_result": "failed"})})
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    out = tmp_path / "out"
    rows = by_url(rows_of(out / "manifest.csv"))
    r = rows[broken]
    assert (out / r["本地档案"]).read_bytes() == OLD_PDF and r["本地档案"].startswith("files/")
    assert r["本地档案"].endswith("-附件二.pdf") and r["标题"] == "附件二.pdf"  # the old answer's Content-Disposition
    assert r["来源"] == export.ORIGIN_OLD
    assert (r["抓取时间"], r["最后修改时间"]) == ("2026-10-24T08:38:21+08:00", "2025-03-04T10:20:30+08:00")
    assert (r["sha256"], r["大小"], r["格式"]) == (hashlib.sha256(OLD_PDF).hexdigest(), str(len(OLD_PDF)), "pdf")
    # this round's result stays in sight
    assert (r["结果"], r["分类"], r["原因"]) == (outcome.RESULT_LABEL["failed"], "连接失败", "连不上这台服务器")
    assert f"这一轮：{outcome.RESULT_LABEL['failed']}；用 10-24 旧存档的副本补上" in r["备注"]
    assert broken not in {m["网址"] for m in rows_of(out / "missing.csv")}  # it has a file now
    assert {row["来源"] for url, row in rows.items() if url != broken} == {""}
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "有 1 行这一轮没抓到能用的内容" in readme and "连接失败 1" in readme
    assert "其中 1 行分类不是「抓到了」，算在上表「其中有档但还要处理」里" in readme
    assert got.old_copies == 1 and any(line.startswith("用 10-24 旧存档的副本补上 1 行（连接失败 1）") for line in got.lines)


def test_a_thin_page_a_redirect_that_tells_it_is_gone_and_one_copy_two_rows_take(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    thin = arch.page("thin", BASE + "intro.aspx", "<html><body><h2>系所簡介</h2></body></html>", flags="thin")
    cfg = arch.page("cfg", BASE + "wlan/profile.mobileconfig", "<html><body></body></html>", flags="thin")
    gone = BASE + "newsview.aspx?nid=9"  # sent to the site's about page: the notice is gone this round
    off, sha = arch.put("gone", gone, "text/html", b"moved", headers={"location": BASE + "about.aspx"}, status=302)
    moved = _add(arch.f, url=gone, final_url=BASE + "about.aspx", status=302, bytes=5, sha256=sha, warc=arch.rel,
                 warc_offset=off, ctype="text/html", sniff="html", page_type="t:ok")
    pair = []
    for scheme in ("http", "https"):  # one file under two addresses, both refused this round
        url = f"{scheme}://c.nknu.edu.tw/aca/uploadfile/form.pdf"
        pair.append(_add(arch.f, url=url, kind="file", kind0="file", tier=2, state="failed", reason="connect",
                         attempts=3, status=None, fetched_at=None))
        _res(arch.f, arch.ids["about"], url, "file", res_id=pair[-1], text="申請表")
    arch.close()
    old = _copies(tmp_path / "old", {
        thin: (HKSCS.encode("big5hkscs"), {"url": BASE + "intro.aspx", "ctype": "text/html; charset=utf-8"}),
        cfg: (PLIST, {"url": BASE + "wlan/profile.mobileconfig", "ctype": "application/octet-stream"}),
        moved: (OLD_NOTICE.encode(), {"url": gone, "ctype": "text/html; charset=utf-8"}),
        arch.ids["d2"]: (OLD_NOTICE.encode(), {"url": BASE + "newsview.aspx?nid=2"}),  # good this round: never taken
        pair[0]: (OLD_PDF, {"url": "http://c.nknu.edu.tw/aca/uploadfile/form.pdf", "ctype": "application/pdf"}),
        pair[1]: (OLD_PDF, {"url": "https://c.nknu.edu.tw/aca/uploadfile/form.pdf", "ctype": "application/pdf"})})
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    out = tmp_path / "out"
    rows = by_url(rows_of(out / "manifest.csv"))
    page = rows[BASE + "intro.aspx"]  # its thin page gives way, read again as the export reads pages
    assert (out / page["本地档案"]).read_bytes() == codecs.BOM_UTF8 + HKSCS.encode()
    assert "按繁体中文 Big5 加香港增补字（big5hkscs；网页自己写的编码是 big5-hkscs）重读" in page["备注"]
    assert page["标题"] == "嘅課程說明"
    assert (page["网页上写的日期"], page["日期来源"], page["来源"]) == ("2025-10-01", export.PAGE_WORDS, export.ORIGIN_OLD)
    assert (page["结果"], page["格式"]) == (outcome.RESULT_LABEL["thin"], "html")
    plist = rows[BASE + "wlan/profile.mobileconfig"]  # the copy is no web page: kept as the file it is
    assert plist["类型"] == "附件" and plist["本地档案"].startswith("files/") and plist["本地档案"].endswith("-profile.mobileconfig")
    assert (out / plist["本地档案"]).read_bytes() == PLIST and "10-24 的副本不是网页（xml），存成附件" in plist["备注"]
    notice = rows[gone]  # a file of its own, not the about page it lands on this round
    assert notice["本地档案"] != rows[BASE + "about.aspx"]["本地档案"]
    # its file is its own address's old answer: it landed nowhere (the content stage reads the file at 最后落地网址, else
    # at 网址: review 11-08), and 备注 keeps where this round's redirect went
    assert notice["最后落地网址"] == "" and f"这一轮跳到 {BASE}about.aspx；副本是这个网址自己的旧内容" in notice["备注"]
    assert (out / notice["本地档案"]).read_text(encoding="utf-8-sig") == OLD_NOTICE and notice["标题"] == "宿舍搬遷公告"
    assert (notice["来源"], notice["结果"]) == (export.ORIGIN_OLD, outcome.RESULT_LABEL["redirect"])
    assert "跳转：内容就是落地那一页的档" not in notice["备注"]
    d2 = rows[BASE + "newsview.aspx?nid=2"]
    assert d2["来源"] == "" and "旧存档" not in d2["备注"]
    first, second = (rows[f"{s}://c.nknu.edu.tw/aca/uploadfile/form.pdf"] for s in ("http", "https"))
    assert first["本地档案"] == second["本地档案"] and (out / first["本地档案"]).read_bytes() == OLD_PDF
    assert {first["来源"], second["来源"]} == {export.ORIGIN_OLD}
    assert "和清单里另一行是同一个档" in first["备注"] + second["备注"]
    assert len(list((out / "files").glob("*form.pdf"))) == 1
    assert got.old_copies == 5


def test_a_copy_that_cannot_be_used_is_said_and_the_row_stays_as_this_round_has_it(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    gone = arch.file("gone", BASE + "uploadfile/gone.pdf", b"<html>not found</html>", "text/html", sniff="html",
                     status=404)
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/gone.pdf", "file", res_id=gone, text="舊表格")
    page_for_file = arch.file("page", BASE + "uploadfile/odd.pdf", b"<html>a page</html>", "text/html", sniff="html")
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/odd.pdf", "file", res_id=page_for_file, text="表格")
    other = _add(arch.f, url=BASE + "uploadfile/other.pdf", kind="file", kind0="file", tier=2, state="failed",
                 reason="connect", attempts=3, status=None, fetched_at=None)
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/other.pdf", "file", res_id=other, text="另一個")
    arch.close()
    folder = _copies(tmp_path / "old", {
        arch.ids["broken"]: (OLD_PDF, {"url": BASE + "uploadfile/broken.pdf", "ctype": "application/pdf"}),
        gone: (OLD_PDF + b"x", {"url": BASE + "uploadfile/gone.pdf", "ctype": "application/pdf"}),
        page_for_file: (b"<html><body>still a page</body></html>", {"url": BASE + "uploadfile/odd.pdf"}),
        # an index made of another archive: its id names another address here
        other: (OLD_PDF + b"y", {"url": BASE + "uploadfile/not-this-one.pdf", "ctype": "application/pdf"})},
        broken=(gone,))
    (folder / "files" / hashlib.sha256(OLD_PDF).hexdigest()).write_bytes(b"%PDF-1.4 changed since\n")
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=folder)
    rows = by_url(rows_of(tmp_path / "out" / "manifest.csv"))
    missing = {m["网址"] for m in rows_of(tmp_path / "out" / "missing.csv")}
    for url, why in ((BASE + "uploadfile/broken.pdf", "档案的 sha256 和索引对不上"),
                     (BASE + "uploadfile/gone.pdf", "副本资料夹里没有它的档"),
                     (BASE + "uploadfile/odd.pdf", "该是档案，副本却是网页"),
                     (BASE + "uploadfile/other.pdf", "索引里的网址和存档里这一行的不一样")):
        r = rows[url]
        assert r["来源"] == "" and f"10-24 旧存档有它的副本，没用上：{why}" in r["备注"], url
        assert r["本地档案"] == "" and url in missing, url
    readme = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "10-24 旧存档有副本、却没用上的 4 行，照这一轮的导出" in readme and "副本资料夹里没有它的档 1" in readme
    assert "有 0 行" not in readme and "这个网站没有可以用它补的行" in readme
    assert got.old_copies == 0 and any(line.startswith("10-24 旧存档有副本却没用上的 4 行") for line in got.lines)


@pytest.mark.parametrize(("index", "why"), [
    ("{not json", "读不了它的 index.json（JSONDecodeError）"),
    (None, "读不了它的 index.json（FileNotFoundError）"),
    ('{"rows": []}', "它的 index.json 里没有 rows"),
])
def test_an_old_copies_folder_whose_index_cannot_be_read_is_not_used(tmp_path, index, why):
    arch = make_archive(tmp_path / "archive")
    folder = tmp_path / "old"
    folder.mkdir()
    if index is not None:
        (folder / "index.json").write_text(index, encoding="utf-8")
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=folder)
    plain = export.export_site(arch.root, SITE, tmp_path / "plain")
    assert _manifest(tmp_path / "out")["项目"] == _manifest(tmp_path / "plain")["项目"]  # nothing changed
    readme = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert f"10-24 旧存档的副本资料夹（`{folder.resolve()}`）没有用上：{why}" in readme
    assert any(why in line for line in got.lines) and not any("旧存档" in line for line in plain.lines)


def test_the_export_command_takes_old_copies(tmp_path):
    arch = make_archive(tmp_path / "archive")
    old = _copies(tmp_path / "old", {arch.ids["broken"]: (OLD_PDF, {"url": BASE + "uploadfile/broken.pdf",
                                                                     "ctype": "application/pdf"})})
    r = CliRunner().invoke(app, ["archive", "export", "--dir", str(arch.root), "--site", SITE, "--out",
                                 str(tmp_path / "out"), "--old-copies", str(old)])
    assert r.exit_code == 0, r.output
    assert "用 10-24 旧存档的副本补上 1 行（连接失败 1）" in r.output


def test_the_old_copies_folder_and_the_archive_are_only_read(tmp_path):
    arch = make_archive(tmp_path / "archive")
    old = _copies(tmp_path / "old", {arch.ids["broken"]: (OLD_PDF, {"url": BASE + "uploadfile/broken.pdf",
                                                                     "ctype": "application/pdf"})})

    def files(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    before, archived = files(old), files(arch.root)
    export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    assert files(old) == before
    after = files(arch.root)
    assert {k: after[k] for k in archived} == archived and set(after) - set(archived) <= {"frontier.db-wal",
                                                                                         "frontier.db-shm"}
