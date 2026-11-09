"""The school's answers of no data (the crawler's fa81f41; the owner 11-09 「照这样做」 to the content session's
proposal, asked in the crawler session: such pages left out by the export's mark and counted, never by their address):
the course tables answer 「系所未提供相關資料」 in a pop-up the to-text step cannot read, so the page reads as an empty
form; the export marks such a page 「学校回答没有资料（系所未提供相關資料）」 in 缺什么, alone or after other reasons,
in every export listing it. The content side reads the mark as a substring into Record.area_mark (corpus.NO_DATA) and
leaves the page out: no document (documents._class), counted as <kind>(学校回答没有资料) when its kind alone would keep
it, never the notice kept for its address, never a notice the school deleted, keeping no image, never read again; a
corpus assembled before content read the mark is warned of, the one the item-15 program assembled too (its area marks
and no NO_DATA). Never by the page's address: the real tables would go too once the school publishes the year, and 16
real notices on 5 SSO unit sites carry the same 「#按钮=115學年度」. The names and words in the fixtures are made up; no
model loads."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from nknu.archive import export as archive_export
from nknu.content.corpus import AREA_EMPTY, AREA_MARKS, LIST_PAGER, NO_DATA, ROW_MARKS, export_info, load_corpus
from nknu.content.documents import DELETED, NO_DATA_SUFFIX, build_documents, deleted_notice
from tests.content import test_columns_1109 as tc
from tests.content import test_lists_1109 as tl
from tests.content import test_multisite_1108 as tm
from tests.content import test_under_read_1109 as tu

COURSES = "https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=200001"
PRESSED = COURSES + "#按钮=115學年度 日間-大學部 OO學系"  # a menu press's answer: kind detail:<profile>#按钮
MARKED = "学校回答没有资料（系所未提供相關資料）"
AFTER_REASONS = "要人看（1 个）、缺内嵌页或按钮（1 个）、待判定、" + MARKED  # 14 rows of 11-09 read so


def test_contents_copy_is_the_exports_word_and_read_before_the_area_marks():
    assert NO_DATA == archive_export.NO_DATA == "学校回答没有资料"
    assert ROW_MARKS == (NO_DATA, *AREA_MARKS)
    assert NO_DATA_SUFFIX == "(学校回答没有资料)"


def test_assemble_reads_the_mark_alone_or_after_other_reasons(tmp_path):
    extra = {1: {"缺什么": AFTER_REASONS}, 3: {"缺什么": MARKED}}  # the notice; the album page (made-up rows)
    records, _ = tc.assemble_with(tmp_path, tc.ta.base_rows(), tc.ta.base_results(), extra)
    got = {r["url"]: r["area_mark"] for r in records}
    assert (got[tc.ta.NOTICE], got[tc.ta.ALBUM]) == (NO_DATA, NO_DATA)
    assert {v for u, v in got.items() if u not in (tc.ta.NOTICE, tc.ta.ALBUM)} == {""}
    assert {r.url: r.area_mark for r in load_corpus(tmp_path / "out" / "corpus.json")} == got


def test_a_page_the_school_answered_with_no_data_is_left_out_and_counted_apart():
    for kind, url in (("detail:dataopen2search#按钮", PRESSED), ("page:sso_dataopen", COURSES)):
        page = tl.rec("p1", kind, url, area_mark=NO_DATA)
        assert build_documents([page], {}, []) == ([], {kind + NO_DATA_SUFFIX: 1}), kind
        assert not deleted_notice(page)  # never counted in sites.deleted_notices
        # unmarked (an export made before fa81f41): a document, as before
        assert len(build_documents([replace(page, area_mark="")], {}, [])[0]) == 1, kind
    # a kind the build leaves out anyway keeps its count under its kind alone
    assert build_documents([tl.rec("p1", "album_images", COURSES, area_mark=NO_DATA)], {}, []) == (
        [], {"album_images": 1})
    # a list's pager page with the mark too is counted as the school's answer (documents._left_out_as) 【Claude】
    both = tl.rec("p1", "page:sso_dataopen", COURSES, area_mark=NO_DATA, list_kind=LIST_PAGER)
    assert build_documents([both], {}, []) == ([], {"page:sso_dataopen" + NO_DATA_SUFFIX: 1})


def test_an_answer_of_no_data_is_never_the_notice_kept_for_its_address():
    # a made-up empty answer spelled without &amp; would otherwise be kept, and its namesake with words set aside
    empty = tl.rec("n1", "newsview", "https://tecs.nknu.edu.tw/newsview.aspx?nid=9&gi=1", area_mark=NO_DATA)
    real = tl.rec("n2", "newsview", "https://tecs.nknu.edu.tw/newsview.aspx?nid=9&amp;gi=1")
    docs, excluded = build_documents([empty, real], {}, [])
    assert ([d.doc_id for d in docs], excluded) == (["dn2"], {"newsview" + NO_DATA_SUFFIX: 1})


def test_an_image_only_such_a_page_shows_is_a_document_of_its_own(tmp_path):
    records, report = tc.assemble_with(tmp_path, tl.pager_rows(), tl.PAGER_RESULTS, {1: {"缺什么": MARKED}})
    image = {r["url"]: r for r in records}[tl.ACA + "uploadfile/a.jpg"]
    assert (image["kind"], report.images_inlined, report.image_docs) == ("附件:图片", 0, 1)


def test_such_a_page_is_never_read_again(tmp_path):
    alumni = f"{tl.ACA}people_detail.aspx?pid=1"  # tu.site_rows()'s row 6: its area holds almost nothing
    records, report, _, _ = tu.assembled(tmp_path, tu.site_rows(), {6: {"缺什么": MARKED}})
    assert (records[alumni]["area_mark"], records[alumni]["under_read"], report.under_read) == (NO_DATA, False, 0)


def test_export_info_counts_the_page_rows_marked(tmp_path):
    rows = [("pages/0001.html", "网页", AFTER_REASONS), ("pages/0002.html", "网页", MARKED),
            ("", "网页", MARKED),  # no file: no record of the corpus
            ("files/0001-a.pdf", "附件", MARKED), ("pages/0005.html", "网页", "内文区是空的（…）")]
    items = [{"序号": str(n), "类型": kind, "网址": f"{COURSES}&n={n}", "本地档案": file, "缺什么": mark}
             for n, (file, kind, mark) in enumerate(rows, start=1)]
    (tmp_path / "manifest.json").write_text(json.dumps({"网站": "sso.nknu.edu.tw/dataopen2search", "项目": items},
                                                       ensure_ascii=False), encoding="utf-8")
    assert export_info(tmp_path)["no_data_marked"] == 2


def marked_corpus(root: Path, area_marks: dict[int, str]) -> tuple[Path, Path]:
    """tl.categorized's corpus of three pages, with these records' area_mark (index -> mark), and its export, whose row
    0 says the notice page's areas are empty (item 15) and row 1 the school's answer of no data (fa81f41)."""
    corpus, export = tl.categorized(root, ["", "", ""])
    records = json.loads(corpus.read_text(encoding="utf-8"))
    for n, mark in area_marks.items():
        records[n]["area_mark"] = mark
    corpus.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    tl.with_columns(export, {0: {"缺什么": archive_export.EMPTY_AREA}, 1: {"缺什么": MARKED}})
    return corpus, export


def test_a_corpus_assembled_before_the_mark_is_warned_of(tmp_path):
    # review 11-09: the stale corpora that exist were assembled by the item-15 program (11-09, before fa81f41): an area
    # mark on a deleted notice, none of no data; any area_mark (the item-15 check) does not tell them apart
    paths = tm.tree_files(tmp_path / "tree")
    stale = marked_corpus(tmp_path / "stale", {0: AREA_EMPTY})
    report = tm.run(paths, tmp_path / "db" / "stale.db", inputs=[stale])
    assert not any("没有 area_mark" in w for w in report.warnings)  # the item-15 marks were read
    said = [w for w in report.warnings if "学校回答没有资料" in w]
    assert len(said) == 1 and "第 1 份输入" in said[0] and "1 行网页" in said[0] and "assemble 重拼" in said[0]
    # the deleted notice left out; the empty page stays, as before
    assert (report.counts["docs"]["total"], report.excluded.get("page:sso_clv2" + DELETED)) == (2, 1)
    assert not any(k.endswith(NO_DATA_SUFFIX) for k in report.excluded)
    # assembled since: the page left out and counted, no warning
    fresh = marked_corpus(tmp_path / "fresh", {0: AREA_EMPTY, 1: NO_DATA})
    report = tm.run(paths, tmp_path / "db" / "fresh.db", inputs=[fresh])
    assert not any("学校回答没有资料" in w for w in report.warnings)
    assert report.counts["docs"]["total"] == 1
    assert report.excluded.get("page:sso_clv2" + DELETED) == 1
    assert sum(n for k, n in report.excluded.items() if k.endswith(NO_DATA_SUFFIX)) == 1


def test_a_row_with_an_area_mark_too_is_read_as_no_data_and_never_kept(tmp_path):
    # made up (no row had both on 11-09): 找不到内文区 first would keep a page that has text (deleted_notice needs none)
    both = archive_export.NO_AREA + "、" + MARKED
    records, _ = tc.assemble_with(tmp_path, tl.pager_rows(), tl.PAGER_RESULTS, {1: {"缺什么": both}})
    assert {r["url"]: r["area_mark"] for r in records}[tl.PAGER] == NO_DATA
    docs, excluded = build_documents(load_corpus(tmp_path / "out" / "corpus.json"), {}, [])
    assert [d.url for d in docs if d.url == tl.PAGER] == []
    assert excluded.get("default#翻页/按钮" + NO_DATA_SUFFIX) == 1
