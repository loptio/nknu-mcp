"""The export batch, T2 (plan docs/superpowers/plans/2026-11-16-export-batch.md; design 2026-11-16-periodic-update-
design.md §五 row 「导出那一批」, §7.2 item 3): 列表上的日期 and 列表上的类别 also read the rows the crawler keeps of each
list (list_row), so that a notice pushed off the pages a nightly look reads again (today's first page) and the pages
kept from the last whole walk (its later pages) keeps its row's date and category. An archive with no list_row exports
as before. Made-up rows only."""

from __future__ import annotations

import sqlite3

from nknu.archive import export
from tests.archive.test_export_1027 import BASE, SITE, make_archive
from tests.archive.test_export_step3_1108 import _with

NOTICE7 = BASE + "newsview.aspx?nid=7"
PAGE7 = """<html><head><title>國立高雄師範大學教務處</title></head><body><div id="DataFormView">
<span id="DataFormView_TitleLabel">轉學考簡章</span><p>簡章請至網站下載。</p></div></body></html>"""


def _list_row(arch, *, list_id: int, href: str | None, key: str, date: str, category: str, title: str = "") -> None:
    arch.f.x("INSERT INTO list_row(list_id, ident, row_key, href, title, date, category, first_seen, last_seen) "
             "VALUES (?,?,?,?,?,?,?,?,?)", (list_id, href or f"key:{key}", key, href, title, date, category, 1.0, 2.0))


def test_a_notice_off_the_stored_list_pages_keeps_its_rows_date_and_category(tmp_path):
    def build(arch):
        arch.page("d7", NOTICE7, PAGE7)
        _list_row(arch, list_id=arch.ids["list"], href=NOTICE7, key="k7", date="2026/11/10", category="招生",
                  title="轉學考簡章")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[NOTICE7]
    assert (r["网页上写的日期"], r["日期来源"], r["列表上的类别"]) == ("2026-11-10", export.LIST_ROW, "招生")


def test_a_pressed_notice_off_the_stored_pages_keeps_its_rows_date(tmp_path):
    """The notice the list's detail button brought (row key k9): its row now read only from list_row."""
    def build(arch):
        _list_row(arch, list_id=arch.ids["list"], href=None, key="k9", date="2025/01/05", category="住宿",
                  title="寒假宿舍申請")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = next(x for x in rows.values() if x["按钮编号"] == "k9")
    assert r["列表上的类别"] == "住宿"


def test_a_row_the_stored_pages_and_list_row_both_hold_says_one_date(tmp_path):
    """nid=1 is on the stored first page (2025/10/01) and in list_row with the same date: no disagreement noted."""
    def build(arch):
        _list_row(arch, list_id=arch.ids["list"], href=BASE + "newsview.aspx?nid=1", key="k1", date="2025/10/01",
                  category="")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[BASE + "newsview.aspx?nid=1"]
    assert "列表" not in r["备注"] or "不一样" not in r["备注"]


def test_an_archive_without_list_row_exports_as_before(tmp_path):
    arch = make_archive(tmp_path / "a")
    export.export_site(arch.root, SITE, tmp_path / "with")
    db = sqlite3.connect(arch.root / "frontier.db")
    db.execute("DROP TABLE list_row")
    db.commit()
    db.close()
    export.export_site(arch.root, SITE, tmp_path / "without")
    assert (tmp_path / "with" / "manifest.csv").read_bytes() == (tmp_path / "without" / "manifest.csv").read_bytes()
