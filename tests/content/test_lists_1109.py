"""Blueprint item 22, parts 2 and 3 (owner 11-09, 「照这个做」), the content side of the crawler's 53cf31b export, whose
manifest rows end in two new columns:

- 页别 (part 3): a page the export marks 列表翻页 (a page a list's pager brought, …#第N页, or a GET-paged list's page 2
  on) is no document whatever its kind, counted as <kind>(列表翻页) when its kind alone would keep it, else under its
  kind as before; it keeps no image, is never read again, never counts as a notice the school deleted, and a file only
  it links hangs on no page. The 11-09 check (research/pipeline_2026-11-02/reading_gaps_1109/lists_1109g.log to
  lists_1109j.log) found every row's page kept (but 17 notices the school deleted) with its title and date, and all
  but 1,453 of 31,582 cells on the linked pages (tecs's category cells, iu's card summaries, tecs's activity-list
  fields, pager widgets; left out with the pages 【Claude】). A page marked 列表 (a list's first page: a home page, a
  teacher's file grid) stays a page;
- 列表上的类别 (part 2): the categories the list rows linking a page state join its unit line as 類別：…, after 單位
  and 適用, so the keyword index's unit_tokens and the passage the reranker reads have them, the vectors not; the other
  records of its identity and its copies add theirs (review 11-09);
- 列表上的日期 (part 2): now the row of any list, this site's or another's, linking any page; a page or a notice it
  dates keeps the date, as before.
An export made before 53cf31b has neither column, and everything stays as it was; a corpus assembled before content read
them, built with an export that has them, is warned of (review 11-09). The names and words in the fixtures are made up
(O masks); no model loads."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from nknu.archive import export as archive_export
from nknu.archive import postback
from nknu.content import build, ids
from nknu.content.build import APPLIES_LABEL, CATEGORY_LABEL, UNIT_LABEL, UnitWords, unit_words
from nknu.content.corpus import (
    AREA_EMPTY,
    CATEGORY_SEP,
    LIST_FIRST,
    LIST_PAGER,
    Event,
    Record,
    export_info,
    load_corpus,
)
from nknu.content.dates import LIST_ROW, LIST_SOURCE, OWN_FIELD, PAGE_WORDS, DateFact
from nknu.content.documents import (
    DELETED,
    DUPLICATE,
    PAGER_SUFFIX,
    Document,
    build_documents,
    deleted_notice,
    one_per_id,
)
from nknu.content.embed import chunk_passages
from nknu.content.units import load_units
from nknu.totext.pagetext import page_kind
from tests.content import test_assemble as ta
from tests.content import test_build as tb
from tests.content import test_columns_1109 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_under_read_1109 as tu

ACA = "https://c.nknu.edu.tw/aca/"
PAGER = ACA + "default.aspx?kind=dd#第2页"  # a page a c.nknu list's pager brought: kind default#翻页/按钮, kept before
FILE = ACA + "uploadfile/news/1/form.pdf"
SERVICES = "https://sso.nknu.edu.tw/services/activity.aspx?id=1"  # a page of the school's activity system (made up)
NEWS = "https://news.nknu.edu.tw/news/detail.aspx?id=1"  # a notice of the announcement system (made up)
FETCHED = "2026-11-09T07:00:00+08:00"


def rec(rid: str, kind: str, url: str, title: str = "公告", linked=(), **more) -> Record:
    more.setdefault("fetched", FETCHED)
    return Record(rid, kind, url, f"pages/{rid}.html", title, title, "", tuple(linked), **more)


def by_id(docs) -> dict[str, Document]:
    return {d.doc_id: d for d in docs}


# ---------------------------------------------------------------- the two columns reach the corpus (53cf31b)

def test_contents_copies_are_the_exports_words():
    assert (LIST_FIRST, LIST_PAGER) == (archive_export.LIST_FIRST, archive_export.LIST_PAGER) == ("列表", "列表翻页")
    rows = [(1, postback.Row(f"k{n}", "x", "", category, None)) for n, category in enumerate(("招生公告", "活動公告"))]
    joined, _ = archive_export.list_category(rows)
    assert joined.split(CATEGORY_SEP) == ["招生公告", "活動公告"]
    assert PAGER_SUFFIX == "(列表翻页)"


def test_assemble_carries_the_columns_into_the_corpus(tmp_path):
    extra = {1: {"列表上的类别": "招生公告 | 活動公告"}, 3: {"页别": LIST_PAGER}}  # the notice; the album page
    records, _ = tc.assemble_with(tmp_path, ta.base_rows(), ta.base_results(), extra)
    got = {r["url"]: (r["list_kind"], r["list_category"]) for r in records}
    assert (got[ta.NOTICE], got[ta.ALBUM]) == (("", "招生公告 | 活動公告"), (LIST_PAGER, ""))
    assert {v for u, v in got.items() if u not in (ta.NOTICE, ta.ALBUM)} == {("", "")}  # files, members, an image
    loaded = {r.url: (r.list_kind, r.list_category) for r in load_corpus(tmp_path / "out" / "corpus.json")}
    assert loaded == got


def test_an_export_or_a_corpus_made_before_reads_blank(tmp_path):
    records, _ = tc.assemble_with(tmp_path, ta.base_rows(), ta.base_results(), {})
    assert {(r["list_kind"], r["list_category"]) for r in records} == {("", "")}
    older = tmp_path / "older.json"  # a corpus assembled before: no such fields
    older.write_text(json.dumps([{"id": "p1", "kind": "default#翻页/按钮", "url": PAGER}]), encoding="utf-8")
    r = load_corpus(older)[0]
    assert (r.list_kind, r.list_category) == ("", "")


# ---------------------------------------------------------------- 页别: a list's pager pages are left out (part 3)

def test_a_pager_page_its_kind_would_keep_is_left_out_and_counted_apart():
    pages = {"default#翻页/按钮": PAGER, "student#翻页/按钮": ACA + "student.aspx#第4页",
             "其他:": "https://c.nknu.edu.tw/affair/#第3页",  # the pager of a list at a site's root
             "page:sso_clv2": "https://sso.nknu.edu.tw/site/200001/index.aspx?page=7&pageNum=2"}  # any platform's
    for kind, url in pages.items():
        page = rec("p1", kind, url, list_kind=LIST_PAGER)
        assert build_documents([page], {}, []) == ([], {kind + PAGER_SUFFIX: 1}), kind
        # the same page unmarked (an export made before 53cf31b): a page, as before
        assert [d.kind_class for d in build_documents([replace(page, list_kind="")], {}, [])[0]] == ["page"], kind


def test_a_pager_page_its_kind_leaves_out_is_counted_under_its_kind_alone():
    for kind, url in (("newslist#翻页/按钮", ACA + "newslist.aspx?nc=1#第2页"),
                      ("list:sso_webidx#翻页", tm.WEB + "&selpage=MoreNews#第2页")):
        assert build_documents([rec("p1", kind, url, list_kind=LIST_PAGER)], {}, []) == ([], {kind: 1}), kind


def test_the_mark_comes_before_a_notices_class_and_its_deleted_mark():
    # tecs's &amp;-spelled newsview shows a list and its pager rows are marked (lists.md §3.3 item 5), of the kind
    # pagetext gives them: never one the school deleted, so sites.deleted_notices (deleted_notice) never counts it
    url = "https://tecs.nknu.edu.tw/newsview.aspx?nid=9&amp;gi=1#第2页"
    kind = page_kind(url)
    assert kind == "newsview#翻页/按钮"
    shell = rec("n1", kind, url, area_mark=AREA_EMPTY)
    assert deleted_notice(shell)  # unmarked, as item 15 had it
    assert build_documents([shell], {}, []) == ([], {kind + DELETED: 1})
    marked = replace(shell, list_kind=LIST_PAGER)
    assert not deleted_notice(marked)
    assert build_documents([marked], {}, []) == ([], {kind + PAGER_SUFFIX: 1})
    # nor a notice: a page whose platform's date field the export read (sub-step 4) ...
    dated = rec("p1", "page:geo", "https://geo.nknu.edu.tw/news/list?page=2", page_date="2026-09-11",
                page_date_raw="2026-09-11", date_source=OWN_FIELD, list_kind=LIST_PAGER)
    assert build_documents([dated], {}, []) == ([], {"page:geo" + PAGER_SUFFIX: 1})
    # ... nor the notice kept for its address: a made-up marked row of c.nknu's notice template, spelled without
    # &amp;, would otherwise be kept and its unmarked namesake, spelled with it, set aside as (重复)
    notice = rec("n2", "newsview", "https://tecs.nknu.edu.tw/newsview.aspx?nid=9&amp;gi=1")
    pager = rec("n3", "newsview", "https://tecs.nknu.edu.tw/newsview.aspx?nid=9&gi=1", list_kind=LIST_PAGER)
    docs, excluded = build_documents([pager, notice], {}, [])
    assert ([d.doc_id for d in docs], excluded) == (["dn2"], {"newsview" + PAGER_SUFFIX: 1})


def test_a_lists_first_page_stays_a_page_and_no_row_dates_it():
    # a list's first page holds words of its own (lists.md §3.3 item 7); a c.nknu list path and a site's root take no
    # date, so its first row's never dates it (documents._stated)
    first = rec("f1", "default", ACA + "default.aspx?gi=1", list_kind=LIST_FIRST, page_date="2026-10-21",
                page_date_raw="公告 2026.10.21", date_source=PAGE_WORDS)
    home = replace(first, id="f2", kind="其他:", url="https://c.nknu.edu.tw/tg/", site="c.nknu.edu.tw/tg")
    grid = rec("f3", "teachercorner", ACA + "teachercorner.aspx?tid=1", list_kind=LIST_FIRST)
    docs, excluded = build_documents([first, home, grid], {}, [])
    assert [(d.doc_id, d.kind_class, d.page_date) for d in docs] == [("df1", "page", None), ("df2", "page", None),
                                                                     ("df3", "page", None)]
    assert excluded == {}


def test_a_file_only_a_pager_page_links_hangs_on_no_page():
    page = rec("p1", "default#翻页/按钮", PAGER, "最新消息", list_kind=LIST_PAGER)
    file = rec("a1", "附件:pdf:有文字层", FILE, "form.pdf", linked=(PAGER,))
    a = by_id(build_documents([page, file], {}, [])[0])["da1"]
    assert (a.parent_id, a.parent_title, a.linked_from) == (None, "最新消息", ())  # like a file only a list links
    a = by_id(build_documents([replace(page, list_kind=""), file], {}, [])[0])["da1"]
    assert (a.parent_id, a.linked_from) == ("dp1", ("dp1",))  # unmarked: the page it hangs on


def pager_rows() -> list[dict]:
    """A c.nknu list's second page showing an image in its text, and the image."""
    return [{"type": "网页", "url": PAGER, "file": "pages/0001.html", "title": "最新消息", "fmt": "html",
             "data": ta.notice_html(body='<p>最新消息</p><img src="uploadfile/a.jpg">', title="最新消息")},
            {"type": "图片", "url": ACA + "uploadfile/a.jpg", "file": "images/0001-a.jpg", "data": ta.JPEG + b"a",
             "title": "a.jpg", "fmt": "jpeg", "linked": [PAGER]}]


PAGER_RESULTS = {"1": ({"page": {"title": "最新消息", "album": "", "images": ["uploadfile/a.jpg"]}},
                       "最新消息\n[[img:uploadfile/a.jpg]]"),
                 "2": ({}, "115學年度第1學期停修申請")}


def test_an_image_only_a_pager_page_shows_is_a_document_of_its_own(tmp_path):
    # unmarked: the page is kept, and the image's words are in its text
    records, report = tc.assemble_with(tmp_path / "before", pager_rows(), PAGER_RESULTS, {})
    assert [(r["url"], r["text"]) for r in records] == [(PAGER, "最新消息\n115學年度第1學期停修申請")]
    assert report.images_inlined == 1
    # marked 列表翻页: the build leaves the page out, so the image is no kept page's and becomes a record
    records, report = tc.assemble_with(tmp_path / "after", pager_rows(), PAGER_RESULTS, {1: {"页别": LIST_PAGER}})
    image = {r["url"]: r for r in records}[ACA + "uploadfile/a.jpg"]
    assert (image["kind"], image["linked"], image["zones"]) == ("附件:图片", PAGER, {PAGER: "content"})
    assert (report.images_inlined, report.image_docs) == (0, 1)
    docs, excluded = build_documents(load_corpus(tmp_path / "after" / "out" / "corpus.json"), {}, [])
    assert excluded == {"default#翻页/按钮" + PAGER_SUFFIX: 1}
    assert [(d.kind_class, d.parent_id, d.parent_title) for d in docs] == [("attachment", None, "最新消息")]
    # with fewer than IMAGE_DOC_CHARS characters of OCR text it is no record either, only counted (image_record)
    short = {**PAGER_RESULTS, "2": ({}, "最新消息")}
    records, report = tc.assemble_with(tmp_path / "short", pager_rows(), short, {1: {"页别": LIST_PAGER}})
    assert [r["url"] for r in records] == [PAGER]
    assert (report.images_inlined, report.image_docs, report.images_no_text) == (0, 0, 1)


def test_a_pager_page_is_never_read_again(tmp_path):
    alumni = f"{ACA}people_detail.aspx?pid=1"  # tu.site_rows()'s row 6: its area holds almost nothing
    records, _, _, _ = tu.assembled(tmp_path / "before", tu.site_rows())
    assert records[alumni]["under_read"] is True  # test_under_read_1109's case
    records, report, _, _ = tu.assembled(tmp_path / "after", tu.site_rows(), {6: {"页别": LIST_PAGER}})
    # judged with its group (its file still counts toward the template), never read again nor counted
    assert (records[alumni]["text"], records[alumni]["under_read"], report.under_read) == ("所友", False, 0)


# ---------------------------------------------------------------- 列表上的类别: on the unit line (part 2)

def test_a_documents_categories_are_its_list_rows_each_once():
    page = rec("p1", "page", SERVICES, list_category="招生公告 | 活動公告 | 招生公告")
    spaced = rec("p2", "page", SERVICES.replace("id=1", "id=2"), list_category=" 招生公告 |  | 活動公告 ")
    file = rec("a1", "附件:pdf:有文字层", FILE, "form.pdf")  # a file is no list row's page: none
    event = Event("ev1", "宿舍開放", date(2026, 9, 1), date(2026, 9, 1), "https://example.invalid/ev1")
    docs = build_documents([page, spaced, file], {}, [event])[0]
    assert [d.categories for d in docs] == [("招生公告", "活動公告"), ("招生公告", "活動公告"), (), ()]


def test_the_unit_line_names_the_categories_after_the_unit_and_what_it_applies_to():
    words = UnitWords(name=tb.SOFT, aliases=("軟體系",), applies=(("文學院", ()),), categories=("徵才就業", "活動快報"))
    assert words.line == f"{UNIT_LABEL}{tb.SOFT}（軟體系） {APPLIES_LABEL}文學院 {CATEGORY_LABEL}徵才就業、活動快報"
    assert words.words == (tb.SOFT, "軟體系", "文學院", "徵才就業", "活動快報")
    assert UnitWords(name="教務處", categories=("徵才就業",)).line == f"{UNIT_LABEL}教務處 {CATEGORY_LABEL}徵才就業"
    assert UnitWords(categories=("社團活動",)).line == f"{CATEGORY_LABEL}社團活動"  # no unit words: the line alone
    assert UnitWords(name="教務處").line == f"{UNIT_LABEL}教務處"  # none: no 類別
    assert UnitWords(name="教務處", categories=("教務處", "招生公告")).words == ("教務處", "招生公告")  # each once
    assert CATEGORY_LABEL == "類別："


def doc(unit: str, categories: tuple[str, ...] = ()) -> Document:
    return Document(doc_id="dp1", kind_class="page", kind_raw="page", title="公告", url=ACA + "page.aspx?pn=1",
                    unit=unit, grp="", parent_id=None, parent_title="", linktext="", has_text=True, text="公告",
                    categories=categories)


def test_unit_words_take_the_categories_the_words_do_not_name(tmp_path):
    paths = tm.tree_files(tmp_path / "tree")
    units = load_units(paths["sites"], paths["units"])
    soft = unit_words(doc(tb.SOFT, ("軟體系", "徵才就業")), units, tb.SCHOOL)
    assert soft.line == f"{UNIT_LABEL}{tb.SOFT}（軟體系、軟工系） {CATEGORY_LABEL}徵才就業"  # its short name: not again
    applied = unit_words(doc("教務處", ("國文學系", "招生公告")), units, tb.SCHOOL, ["國文學系"])
    assert applied.line == f"{UNIT_LABEL}教務處 {APPLIES_LABEL}國文學系 {CATEGORY_LABEL}招生公告"
    school = unit_words(doc(tb.SCHOOL, ("專題演講或個人展演",)), units, tb.SCHOOL)  # the school gives no words
    assert (school.line, school.words) == (f"{CATEGORY_LABEL}專題演講或個人展演", ("專題演講或個人展演",))
    assert unit_words(doc("教務處"), units, tb.SCHOOL).line == f"{UNIT_LABEL}教務處"
    assert unit_words(doc("教務處", ("招生公告",)), None, tb.SCHOOL).line == (  # no tree: the unit as written
        f"{UNIT_LABEL}教務處 {CATEGORY_LABEL}招生公告")


PAGES = [("招生說明會", "碩士班招生說明會將於本週五下午舉行"), ("教務處公告", "請同學留意選課時間"),
         ("停修", "停修申請至十一月十日止")]
CATEGORIES = ["招生公告 | 活動公告", "教務處", ""]  # the second's is its unit's name: said once, so no 類別


def categorized(root: Path, categories: list[str]) -> tuple[Path, Path]:
    """tm.hand_input's corpus of 教务处's PAGES, each record with its 列表上的类别 from categories, and its export."""
    pages = [(ACA + f"page.aspx?pn={40 + n}", title, text) for n, (title, text) in enumerate(PAGES)]
    corpus, export = tm.hand_input(root, "c.nknu.edu.tw/aca", "教务处", pages)
    records = json.loads(corpus.read_text(encoding="utf-8"))
    for r, category in zip(records, categories, strict=True):
        r["list_category"] = category
    corpus.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return corpus, export


def test_the_build_puts_the_categories_on_the_unit_line_and_counts_them(tmp_path):
    from tests.content.test_embed import FakeEmbedder

    paths = tm.tree_files(tmp_path / "tree")
    inputs = [categorized(tmp_path / "aca", CATEGORIES)]
    embedder = FakeEmbedder()
    out = tmp_path / "db" / "c.db"
    report = tm.run(paths, out, inputs=inputs, embed=True, embedder=embedder)
    assert report.scope["unit_line_categories"] == {"docs": 1}
    first = "d" + ids.record_id(ids.location_key(ACA + "page.aspx?pn=40"))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        lines = [line for (line,) in conn.execute("SELECT unit_line FROM docs ORDER BY url")]
        chunk = conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (first,)).fetchone()[0]
        matched = {d for (d,) in conn.execute("SELECT c.doc_id FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
                                              " WHERE chunk_fts MATCH 'unit_tokens:活动'")}
        passage = chunk_passages(conn, [chunk])[chunk].split("\n")
    finally:
        conn.close()
    assert lines == [f"{UNIT_LABEL}教務處 {CATEGORY_LABEL}招生公告、活動公告", f"{UNIT_LABEL}教務處", f"{UNIT_LABEL}教務處"]
    assert matched == {first}  # the keyword index: a word only its category has
    assert passage[1] == lines[0]  # the reranker reads the line
    assert embedder.texts and not any("活動公告" in t or CATEGORY_LABEL in t for t in embedder.texts)  # the vectors not
    # --no-unit-words leaves the whole unit line out, the categories too
    off = tm.run(paths, tmp_path / "db" / "off.db", inputs=inputs, unit_words=False)
    assert off.scope["unit_line_categories"] == {"docs": 0}


def test_main_says_how_many_unit_lines_name_categories(tmp_path, capsys):
    paths = tm.tree_files(tmp_path / "tree")
    common = ["--aliases", str(paths["aliases"]), "--server-dates", str(paths["server"]), "--site-list",
              str(paths["sites"]), "--units", str(paths["units"])]
    corpus, export = categorized(tmp_path / "aca", CATEGORIES)
    assert build.main(["--input", f"{corpus}::{export}", "--out", str(tmp_path / "o" / "c.db"), *common]) == 0
    assert "单位行写了列表类别的 1 份" in capsys.readouterr().out
    report = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert report["scope"]["unit_line_categories"] == {"docs": 1}
    corpus, export = categorized(tmp_path / "plain", ["", "", ""])  # an export made before 53cf31b: none
    assert build.main(["--input", f"{corpus}::{export}", "--out", str(tmp_path / "p" / "c.db"), *common]) == 0
    assert "列表类别" not in capsys.readouterr().out


def test_the_report_and_the_help_say_the_unit_line_holds_the_categories(capsys):
    # review 11-09: a school's page can have a line of 類別 alone, and --no-unit-words leaves them out with the rest
    assert "「類別：" in build.PASSAGE_NOTE and "只有「適用：…」或「類別：…」" in build.PASSAGE_NOTE
    assert "适用单位和列表类别" in build.PASSAGE_NOTE_OFF
    with pytest.raises(SystemExit):
        build.main(["--help"])
    assert "简称、适用单位、列表类别）" in "".join(capsys.readouterr().out.split())  # the help's lines joined


# ---------------------------------------------------------------- the merges keep every category (review 11-09)

SSO_NEWS = "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=OOO1"  # an announcement centre notice (made-up UNID)
MIRROR = "https://news.nknu.edu.tw/nknu_news/lastnews.aspx?catid=CAMPUS&unid=OOO1"  # its mirror: one identity (ids)


def test_one_identitys_records_join_their_categories():
    # the mirror, fetched later, is kept (one_per_id); the announcement centre's list and an &amp; spelling's gave the
    # other categories: its own first, then each other once
    assert ids.location_key(MIRROR) == ids.location_key(SSO_NEWS)
    rid = ids.record_id(ids.location_key(SSO_NEWS))
    sso = rec(rid, "detail:sso_news", SSO_NEWS, list_category="活動快報 | 行政資訊", fetched="2026-11-07T03:47:00+08:00",
              site="sso.nknu.edu.tw/news")
    mirror = rec(rid, "detail:sso_news", MIRROR, list_category="校園情報", fetched="2026-11-08T03:30:00+08:00",
                 site="news.nknu.edu.tw")
    amp = replace(mirror, url=MIRROR.replace("&", "&amp;"), list_category="行政事務 | 活動快報")
    kept, merged = one_per_id([sso, mirror, amp])
    assert ([r.url for r in kept], merged) == ([MIRROR], {"detail:sso_news" + DUPLICATE: 2})
    assert kept[0].list_category == CATEGORY_SEP.join(("校園情報", "活動快報", "行政資訊", "行政事務"))
    assert [d.categories for d in build_documents(kept, {}, [])[0]] == [("校園情報", "活動快報", "行政資訊", "行政事務")]
    # the kept one without any (the 11-09 case): the others' alone; none anywhere: none, as before
    kept, _ = one_per_id([sso, replace(mirror, list_category="")])
    assert kept[0].list_category == "活動快報 | 行政資訊"
    kept, _ = one_per_id([replace(sso, list_category=""), replace(mirror, list_category="")])
    assert kept[0].list_category == ""


def test_a_copys_categories_join_the_document_kept(tmp_path):
    # two addresses of one page (one title and text): the first is kept (build._merge_copies) and the copy's list row
    # gave a category the unit line, written after the merge, would lose
    paths = tm.tree_files(tmp_path / "tree")
    pages = [(ACA + f"page.aspx?pn={n}", "社團博覽會", "社團博覽會於操場舉行") for n in (50, 51)]
    corpus, export = tm.hand_input(tmp_path / "aca", "c.nknu.edu.tw/aca", "教务处", pages)
    records = json.loads(corpus.read_text(encoding="utf-8"))
    records[0]["list_category"], records[1]["list_category"] = "活動公告", "社團活動 | 活動公告"
    corpus.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "db" / "c.db"
    report = tm.run(paths, out, inputs=[(corpus, export)])
    assert report.excluded.get("page:sso_clv2" + build.COPY) == 1
    assert report.scope["unit_line_categories"] == {"docs": 1}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        lines = [line for (line,) in conn.execute("SELECT unit_line FROM docs")]
    finally:
        conn.close()
    assert lines == [f"{UNIT_LABEL}教務處 {CATEGORY_LABEL}活動公告、社團活動"]


# ---------------------------------------------------------------- a corpus assembled before (review 11-09)

def test_export_info_counts_the_page_rows_marked_that_are_records(tmp_path):
    rows = [(PAGER, "pages/0001.html", {"页别": LIST_PAGER}), (SERVICES, "pages/0002.html", {"列表上的类别": "社團活動"}),
            (ACA + "default.aspx", "pages/0003.html", {"页别": LIST_FIRST}),  # a list's first page changes nothing
            (NEWS, "", {"列表上的类别": "招生公告"}),  # the export sets a category on a row with no file: no record
            (ACA + "page.aspx?pn=1", "pages/0005.html", {"页别": "", "列表上的类别": " "})]
    items = [{"序号": str(n), "类型": "网页", "网址": url, "本地档案": file, **more}
             for n, (url, file, more) in enumerate(rows, start=1)]
    (tmp_path / "manifest.json").write_text(json.dumps({"网站": "c.nknu.edu.tw/aca", "项目": items}, ensure_ascii=False),
                                            encoding="utf-8")
    assert export_info(tmp_path)["list_marked"] == 2


def with_columns(export: Path, columns: dict[int, dict[str, str]]) -> None:
    """53cf31b's columns on rows of tm.hand_input's manifest (the row's index -> {column: value}), before any read."""
    path = export / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for n, values in columns.items():
        manifest["项目"][n].update(values)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_a_corpus_assembled_before_the_columns_is_warned_of(tmp_path):
    paths = tm.tree_files(tmp_path / "tree")
    marks = {0: {"列表上的类别": "招生公告"}, 2: {"页别": LIST_PAGER}}
    stale = categorized(tmp_path / "stale", ["", "", ""])  # no record carries a list field
    with_columns(stale[1], marks)
    report = tm.run(paths, tmp_path / "db" / "stale.db", inputs=[stale])
    said = [w for w in report.warnings if "没有页别和列表上的类别" in w]
    assert len(said) == 1 and "第 1 份输入" in said[0] and "2 行网页" in said[0] and "assemble 重拼" in said[0]
    assert (report.counts["docs"]["total"], report.scope["unit_line_categories"]) == (3, {"docs": 0})  # as before
    # assembled since: the pager page left out, the category on the line, no warning
    fresh = categorized(tmp_path / "fresh", ["招生公告", "", ""])
    records = json.loads(fresh[0].read_text(encoding="utf-8"))
    records[2]["list_kind"] = LIST_PAGER
    fresh[0].write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    with_columns(fresh[1], marks)
    report = tm.run(paths, tmp_path / "db" / "fresh.db", inputs=[fresh])
    assert not any("列表上的类别" in w for w in report.warnings)
    assert report.excluded.get("page:sso_clv2" + PAGER_SUFFIX) == 1
    assert report.scope["unit_line_categories"] == {"docs": 1}
    # an export made before 53cf31b, with a corpus of no list field: nothing to warn of
    plain = categorized(tmp_path / "plain", ["", "", ""])
    report = tm.run(paths, tmp_path / "db" / "plain.db", inputs=[plain])
    assert not any("列表上的类别" in w for w in report.warnings)


# ---------------------------------------------------------------- 列表上的日期: any list's row, trusted as before

def test_a_page_or_a_notice_its_list_row_dates_keeps_the_date():
    # a c.nknu activity page its own site's list dates, the activity system's page a unit site's 書表 list dates (another
    # site's list) and a notice of the announcement system another site's list links: all three as before 53cf31b
    records = [rec(rid, kind, url, page_date="2026-09-11", page_date_raw="2026-09-11", date_source=LIST_ROW)
               for rid, kind, url in (("p1", "case_detail", "https://c.nknu.edu.tw/tg/case_detail.aspx?hid=12"),
                                      ("p2", "page", SERVICES), ("n1", "detail:sso_news", NEWS))]
    docs = by_id(build_documents(records, {}, [])[0])
    day = date(2026, 9, 11)
    for doc_id in ("dp1", "dp2"):
        d = docs[doc_id]
        assert (d.kind_class, d.page_date, d.page_date_source) == ("page", day, LIST_SOURCE), doc_id
        assert build._facts(d, None, None, {}) == [DateFact("published", day, "2026-09-11", LIST_SOURCE)]
    assert (docs["dn1"].kind_class, docs["dn1"].added, docs["dn1"].added_source) == ("notice", day, LIST_SOURCE)
