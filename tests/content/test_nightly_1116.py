"""What the build reads of the crawler's nightly update (spec 2026-11-16-nightly-ingest-design §五; the owner 11-16,
「照这样做」 to its five points; the marks and files agreed with the crawler and the interface sessions the same day):

- a row the school deleted, marked 「学校已删除」 in the export's 缺什么 (corpus.GONE), a page or a file, is left out
  and counted, in the report under <kind>(学校已删除) and per site in sites.deleted_notices;
- the crawler's seen.tsv gives each site's last-seen day, stored as meta site_checked_json.

The sentences are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3

from nknu.archive import export as archive_export
from nknu.content import build
from nknu.content.build import read_site_checked
from nknu.content.corpus import GONE, MOVED, NO_DATA, ROW_MARKS
from nknu.content.documents import GONE_FILE_SUFFIX, GONE_SUFFIX, MOVED_SUFFIX, build_documents
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

CHAIR, CHAIR_SITE = tc.CHAIR, tc.CHAIR_SITE
WORDS = "本頁說明修課順序與先修科目，請同學依年級選課。"


# ---------------------------------------------------------------- 学校已删除

def test_the_mark_is_read_before_the_other_marks():
    assert GONE == "学校已删除" and ROW_MARKS[0] == GONE and NO_DATA in ROW_MARKS
    row = {"缺什么": f"要人看：页面几乎是空的、{NO_DATA}（404）、{GONE}"}
    assert next(m for m in ROW_MARKS if m in row["缺什么"]) == GONE  # assemble's rule for Record.area_mark


def test_a_deleted_page_and_a_deleted_file_are_left_out_and_counted():
    page = tc.rec("p1", "page:sso_clv2", CHAIR + "1", "課程地圖", WORDS, CHAIR_SITE)
    gone_page = tc.rec("p2", "page:sso_clv2", CHAIR + "2", "舊的實習辦法", WORDS, CHAIR_SITE, area_mark=GONE)
    gone_file = tc.rec("a1", tc.PDF, CHAIR + "f/old.pdf", "舊表單.pdf", "舊的申請表\n學號 姓名", CHAIR_SITE,
                       area_mark=GONE, linked=(page.url,))
    kept_file = tc.rec("a2", tc.PDF, CHAIR + "f/new.pdf", "新表單.pdf", "新的申請表\n學號 姓名", CHAIR_SITE, linked=(page.url,))
    docs, excluded = build_documents([page, gone_page, gone_file, kept_file], {}, [])
    assert sorted(d.doc_id for d in docs) == ["da2", "dp1"]
    assert excluded["page:sso_clv2" + GONE_SUFFIX] == 1 and excluded[tc.PDF + GONE_SUFFIX] == 1


def test_the_build_counts_them_per_site(tmp_path):
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + "1", "title": "課程地圖", "text": WORDS},
            {"kind": "page:sso_clv2", "url": CHAIR + "2", "title": "舊的實習辦法", "text": WORDS, "area_mark": GONE},
            {"kind": tc.PDF, "url": CHAIR + "f/old.pdf", "title": "舊表單.pdf", "text": "舊的申請表\n學號 姓名",
             "area_mark": GONE, "linked": CHAIR + "1"}]
    out = tmp_path / "db" / "a.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[tc.site_input(tmp_path / "chair", CHAIR_SITE, "系所", rows)])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert [r[0] for r in conn.execute("SELECT url FROM docs")] == [CHAIR + "1"]
        assert conn.execute("SELECT deleted_notices FROM sites WHERE site = ?", (CHAIR_SITE,)).fetchone() == (2,)
    finally:
        conn.close()
    assert report.excluded["page:sso_clv2" + GONE_SUFFIX] == 1 and report.excluded[tc.PDF + GONE_SUFFIX] == 1


def _with_files(*files):
    """A notice the school deleted, a page still there, and the files given: ({doc ids kept}, excluded)."""
    gone = tc.rec("p1", "detail:sso_clv2", CHAIR + "1", "獎學金公告", WORDS, CHAIR_SITE, "2026-03-01", area_mark=GONE)
    live = tc.rec("p2", "page:sso_clv2", CHAIR + "2", "表單下載", WORDS, CHAIR_SITE)
    docs, excluded = build_documents([gone, live, *files], {}, [])
    return {d.doc_id: d for d in docs}, excluded


def _file(rid: str, **more):
    return tc.rec(rid, tc.PDF, f"{CHAIR}f/{rid}.pdf", f"{rid}.pdf", "獎學金申請表\n學號 姓名", CHAIR_SITE, **more)


def test_a_file_only_a_deleted_notice_links_goes_with_it():
    kept, excluded = _with_files(_file("a1", linked=(CHAIR + "1",)))
    assert sorted(kept) == ["dp2"]  # no file left behind without its notice
    assert excluded[tc.PDF + GONE_FILE_SUFFIX] == 1 and excluded["detail:sso_clv2" + GONE_SUFFIX] == 1


def test_a_file_another_page_still_links_stays():
    kept, excluded = _with_files(_file("a1", linked=(CHAIR + "1", CHAIR + "2")),  # the page's text links it too
                                 _file("a2", linked=(CHAIR + "1",), nav_linked=(CHAIR + "2",)),  # a menu still does
                                 _file("a3"))  # no page links it: it was never the deleted notice's alone
    assert sorted(kept) == ["da1", "da2", "da3", "dp2"] and tc.PDF + GONE_FILE_SUFFIX not in excluded
    assert kept["da1"].parent_id == "dp2" and kept["da1"].linked_from == ("dp2",)  # the deleted notice is no parent


# ---------------------------------------------------------------- the crawler's words, a moved notice, 组别

def test_contents_copies_are_the_exports_words():
    assert GONE == archive_export.SCHOOL_DELETED
    assert archive_export.NOTICE_MOVED.startswith(MOVED) and MOVED == "学校把这则公告转到别的页"
    lacks = f"要人看：页面几乎是空的、{archive_export.NOTICE_MOVED}"
    assert next(m for m in ROW_MARKS if m in lacks) == MOVED
    assert next(m for m in ROW_MARKS if m in f"{archive_export.SCHOOL_DELETED}（2026-11-18 起说不存在）") == GONE


def test_a_notice_the_school_sent_elsewhere_is_left_out_and_counted(tmp_path):
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + "1", "title": "課程地圖", "text": WORDS},
            {"kind": "detail:sso_clv2", "url": CHAIR + "2", "title": "活動相簿", "text": WORDS, "area_mark": MOVED}]
    out = tmp_path / "db" / "a.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[tc.site_input(tmp_path / "chair", CHAIR_SITE, "系所", rows)])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert [r[0] for r in conn.execute("SELECT url FROM docs")] == [CHAIR + "1"]
        assert conn.execute("SELECT deleted_notices FROM sites WHERE site = ?", (CHAIR_SITE,)).fetchone() == (1,)
    finally:
        conn.close()
    assert report.excluded["detail:sso_clv2" + MOVED_SUFFIX] == 1


def test_a_file_a_moved_notice_and_nothing_else_links_stays():
    # only a page the school deleted takes its files with it: a moved notice's row holds another page's links
    moved = tc.rec("p1", "detail:sso_clv2", CHAIR + "1", "活動相簿", WORDS, CHAIR_SITE, area_mark=MOVED)
    docs, excluded = build_documents([moved, _file("a1", linked=(CHAIR + "1",))], {}, [])
    assert [d.doc_id for d in docs] == ["da1"] and tc.PDF + GONE_FILE_SUFFIX not in excluded


def test_the_exports_group_is_the_notices_when_its_page_is_not_read_again():
    notice = tc.rec("p1", "detail:sso_clv2", CHAIR + "1", "夜間班選課公告", WORDS, CHAIR_SITE, "2026-09-01", group="教務組")
    (doc,), _ = build_documents([notice], {}, [])
    assert doc.grp == "教務組"


# ---------------------------------------------------------------- seen.tsv

def test_the_last_seen_days_are_read_whatever_else_the_line_holds(tmp_path):
    seen = tmp_path / "seen.tsv"
    seen.write_text("﻿网站\t最后一次看过\t今晚\n"
                    f"{CHAIR_SITE}\t2026-11-18T01:42:10+08:00\t导了\n"
                    "c.nknu.edu.tw/aca\t2026-11-17 01:20\t没变动\n"
                    "c.nknu.edu.tw/never\t\t连不上\n"
                    "\n"
                    "一行没有分栏的字\n", encoding="utf-8")
    assert read_site_checked(seen) == {CHAIR_SITE: "2026-11-18", "c.nknu.edu.tw/aca": "2026-11-17"}
    assert read_site_checked(tmp_path / "none.tsv") == {}


def test_the_build_stores_them_for_the_sites_it_built(tmp_path):
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + "1", "title": "課程地圖", "text": WORDS}]
    paths = tm.tree_files(tmp_path / "tree")
    inputs = [tc.site_input(tmp_path / "chair", CHAIR_SITE, "系所", rows)]

    def meta(name: str, **kw) -> str:
        out = tmp_path / "db" / f"{name}.db"
        tm.run(paths, out, inputs=inputs, **kw)
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT value FROM meta WHERE key = ?", (build.SITE_CHECKED_KEY,)).fetchone()[0]
        finally:
            conn.close()

    assert build.SITE_CHECKED_KEY == "site_checked_json"
    given = {CHAIR_SITE: "2026-11-18", "c.nknu.edu.tw/elsewhere": "2026-11-18"}
    assert json.loads(meta("with", site_checked=given)) == {CHAIR_SITE: "2026-11-18"}
    assert json.loads(meta("without")) == {}
