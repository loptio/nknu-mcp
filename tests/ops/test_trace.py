"""plan 2026-11-15 T3: one page or file followed through the pipeline. trace reads what each step left of it: the
export's manifest row (and with it what the crawler knew), the to-text cache entry, the assembled record, the library's
rows, and where a search for its own title finds it; for what the library does not hold, why."""

from __future__ import annotations

import json
import shutil

import pytest

from nknu.ops.trace import trace, trace_text
from tests.ops import world
from tests.ops.world import (
    AWAY_URL,
    COPY_URL,
    FORM_TEXT,
    FORM_URL,
    LOST_URL,
    MENU_URL,
    NOTICE_URL,
    PAGER_URL,
    SCAN_URL,
    doc_id,
)
from tests.retrieval.test_search import TODAY


@pytest.fixture
def w(tmp_path):
    made = world.build(tmp_path)
    yield made
    made.conn.close()


def _trace(w, ref, **kw):
    return trace(w.conn, ref, root=w.root, today=TODAY, **kw)


def test_a_file_with_a_text_layer(w):
    report = _trace(w, doc_id(FORM_URL))
    assert report["resolved"] == {"doc_id": doc_id(FORM_URL), "via": "id", "why": "", "alias": ""}
    assert report["export"]["site"] == "school.example" and report["export"]["folder"] == world.EXPORT
    assert report["export"]["row"]["标题"] == "停修申請表" and report["export"]["file"] == "files/0003-停修申請表.pdf"
    assert report["crawl"]["fetched"] == "2026-11-08T03:12:00+08:00" and report["crawl"]["archive_id"] == "u82"
    assert report["crawl"]["size"] == 13 and report["crawl"]["linked_from"] == NOTICE_URL
    assert report["totext"]["sha256"] == report["export"]["row"]["sha256"]  # a file is exported as it was archived
    t = report["totext"]
    assert (t["route"], t["status"], t["pages"], t["ocr_pages"], t["chars"]) == ("pdf", "ok", 2, 0, len(FORM_TEXT))
    assert t["text"] == FORM_TEXT and t["full"] is None
    assert report["record"]["kind"] == "附件:pdf:有文字层" and report["record"]["source"] == "totext"
    lib = report["library"]
    assert lib["doc"]["kind_class"] == "attachment" and lib["doc"]["parent_title"] == "115-1 停修公告"
    assert lib["doc"]["ident_date"] == "2026-10-21" and lib["doc"]["ident_kind"] == "inherited"
    assert lib["dates"] == [{"kind": "inherited", "date": "2026-10-21", "raw": "115.10.21", "source": "所屬公告"}]
    assert lib["refs"] == [{"ref_id": doc_id(NOTICE_URL), "zone": "attachments", "context": "申請表",
                            "title": "115-1 停修公告"}]
    assert lib["chunks_total"] == 1 and lib["chunks"][0]["text"] == FORM_TEXT
    assert "停修申請表" in lib["chunks"][0]["passage"] and "115-1 停修公告" in lib["chunks"][0]["passage"]
    assert report["search"] == {"query": "停修申請表", "rank": 1, "within": 50, "how": "keyword"}
    assert report["left_out"] is None and report["missing"] == []


def test_a_file_read_by_ocr(w):
    report = _trace(w, SCAN_URL)
    assert report["resolved"]["via"] == "url"
    t = report["totext"]
    assert (t["ocr_pages"], t["pages"], t["ocr_lines"], t["ocr_mean_score"]) == (2, 2, 2, 0.93)
    assert t["ocr_reasons"] == ["没有可用的文字层"]
    assert report["library"]["doc"]["has_text"] == 0
    assert "全部 2 页靠 OCR" in trace_text(report) and "没有可用的文字层" in trace_text(report)


def test_the_pages_read_by_ocr_are_counted_once_whoever_read_them(tmp_path):
    """result.json names the pages our OCR read (one entry each, with the reason) and, apart, the pages Docling's own
    OCR read (page numbers): a page in both is one page."""
    from nknu.ops.trace import _totext
    from nknu.totext.cache import Cache

    cache, sha = Cache(tmp_path), "ab" * 32
    ours = [{"page": 1, "why": "few_chars", "reason": "没有可用的文字层", "lines": 3, "mean_score": 0.9},
            {"page": 3, "why": "broken", "reason": "文字层是乱码", "lines": 5, "mean_score": 0.8}]
    cache.save(sha, {"route": "pdf", "status": "ok", "pages": 4, "ocr_pages": ours, "docling_ocr_pages": [3, 4],
                     "chars": 9}, "一二三四五六七八九", {})
    got = _totext(cache, sha, [])
    assert got["ocr_pages"] == 3 and got["ocr_reasons"] == ["没有可用的文字层", "文字层是乱码"]


def test_a_notice_with_its_date_unit_and_chunks(w):
    report = _trace(w, NOTICE_URL)
    assert report["record"]["under_read"] is False and report["record"]["date_source"] == "详细页的日期栏"
    assert report["totext"]["route"] == "page" and "正文区" in report["totext"]["reading"]
    # a page is re-saved on export: its cache entry is the exported file's, not the manifest's sha256
    assert report["totext"]["sha256"] != report["export"]["row"]["sha256"]
    assert report["totext"]["full_chars"] > report["totext"]["chars"] and report["totext"]["full"].startswith("首頁")
    lib = report["library"]
    doc = lib["doc"]
    assert (doc["unit"], doc["grp"], doc["unit_line"]) == ("教務處", "註冊組", "單位：教務處 註冊組")
    assert (doc["ident_date"], doc["ident_kind"], doc["period_ay"], doc["period_sem"]) == (
        "2026-10-21", "published", 115, 1)
    assert (doc["tier"], doc["expires"], doc["valid_ay"], doc["rank_date"]) == ("primary", 1, 115, "2026-10-21")
    assert "text" not in doc and doc["chars"] == len(world.NOTICE_TEXT)
    assert [(d["kind"], d["date"], d["source"]) for d in lib["dates"]] == [
        ("period_start", "2026-11-03", "正文期間"), ("published", "2026-10-21", "公告頁日期欄")]
    assert [(s["relation"], s["unit_id"]) for s in lib["scope"]] == [
        ("applies_to", "國立高雄師範大學"), ("publisher", "教務處")]
    assert lib["site"]["name"] == "範例處" and lib["site"]["data_as_of"] == "2026-11-08"
    assert [c["doc_id"] for c in lib["children"]] == [doc_id(FORM_URL)]
    assert lib["copies"] == [{"alias": doc_id(COPY_URL), "why": "copy", "url": COPY_URL}]
    assert [c["text"] for c in lib["chunks"]] == ["115學年度第1學期停修申請自11月3日起受理。", "申請人：王OO。\n逾期不予受理。"]
    assert all("單位：教務處 註冊組" in c["passage"] for c in lib["chunks"])
    assert lib["sections"] == [{"sec_id": 0, "heading": "", "start": 0, "end": len(world.NOTICE_TEXT)}]


def test_a_merged_copy_is_answered_with_the_document_kept_for_it(w):
    report = _trace(w, COPY_URL)
    kept = doc_id(NOTICE_URL)
    assert report["resolved"] == {"doc_id": kept, "via": "alias", "why": "copy", "alias": doc_id(COPY_URL)}
    assert report["library"]["doc"]["doc_id"] == kept
    # the copy's own row, on its own site: what the owner pasted (review 11-15: the kept document's row was shown)
    assert report["export"]["row"]["网址"] == COPY_URL and report["export"]["site"] == world.BOARD
    assert report["export"]["of"] == "reference" and report["totext"]["route"] == "page"
    assert report["record"]["kind"] == "page"
    assert f"并进了 {kept}" in trace_text(report) and "①～③ 是你给的那一份" in trace_text(report)


def test_a_copys_id_finds_its_own_row_by_the_address_the_library_kept_of_it(w):
    report = _trace(w, doc_id(COPY_URL))
    assert report["resolved"]["via"] == "alias"
    assert report["export"]["site"] == world.BOARD and report["export"]["of"] == "reference"
    assert report["export"]["row"]["网址"] == COPY_URL


def test_a_copy_whose_own_row_is_gone_shows_the_kept_documents_and_says_so(w):
    shutil.move(w.exports[world.BOARD], w.exports[world.BOARD].with_name("moved-away"))
    report = _trace(w, COPY_URL)
    assert report["export"]["site"] == world.SITE and report["export"]["of"] == "kept"
    assert report["export"]["row"]["网址"] == NOTICE_URL
    text = trace_text(report)
    assert "①～③ 是留下来的那一份" in text and "①～③ 是你给的那一份" not in text
    assert any("导出资料夹" in m for m in report["missing"])


def test_a_document_of_the_library_opens_its_own_sites_manifest_only(w, monkeypatch):
    from nknu.ops import trace as module

    opened: list[str] = []
    real = module._load_manifest
    monkeypatch.setattr(module, "_load_manifest", lambda path: opened.append(path.parent.name) or real(path))
    assert _trace(w, doc_id(FORM_URL))["export"]["file"] == "files/0003-停修申請表.pdf"
    assert opened == [world.EXPORT.rsplit("/", 1)[-1]]


def test_of_two_rows_of_one_id_the_documents_own_is_shown(w):
    """An address spelt twice is one id and two manifest rows: the document's is the row at its own address, which has
    the file (review 11-15: the last row won, here the one without a file)."""
    for ref in (NOTICE_URL, doc_id(NOTICE_URL)):
        report = _trace(w, ref)
        assert report["export"]["row"]["网址"] == NOTICE_URL and report["export"]["file"]
    assert _trace(w, world.TWIN_URL)["export"]["row"]["网址"] == world.TWIN_URL  # asked by the other spelling: that row


def test_a_lists_pager_page_is_left_out_and_the_reason_is_the_builds_own(w):
    report = _trace(w, PAGER_URL)
    assert report["resolved"]["doc_id"] is None and report["library"] is None and report["search"] is None
    assert report["export"]["row"]["页别"] == "列表翻页" and report["totext"]["route"] == "page"
    assert report["left_out"]["key"] == "default(列表翻页)" and "列表" in report["left_out"]["words"]
    assert "没进库" in trace_text(report)


def test_a_row_that_was_never_fetched_says_what_the_crawler_got(w):
    report = _trace(w, MENU_URL)
    assert report["export"]["row"]["结果"] == "没抓到" and report["totext"] is None and report["record"] is None
    assert report["left_out"]["key"] == "" and "没抓到" in report["left_out"]["words"]
    assert "HTTP 404" in report["left_out"]["words"]


@pytest.mark.parametrize("ref", ["https://school.example/nowhere.aspx", "https://other.example/x", "junk", ""])
def test_a_reference_found_nowhere_is_said_in_words(w, ref):
    report = _trace(w, ref)
    assert report["resolved"]["doc_id"] is None
    assert report["export"] is None and report["library"] is None and report["left_out"] is None
    assert report["missing"] and "库里找不到这一份" in trace_text(report)
    assert any("找不到" in m or "找过了" in m for m in report["missing"])


def test_a_cache_entry_that_is_gone_is_said(w):
    report = _trace(w, LOST_URL)
    assert report["totext"] is None and report["library"]["doc"]["title"] == "舊表單"
    assert any("转文字" in m for m in report["missing"])


def test_an_export_folder_that_is_gone_is_said(w):
    shutil.move(w.export, w.export.with_name("moved-away"))
    report = _trace(w, doc_id(FORM_URL))
    assert report["export"] is None and report["crawl"] is None and report["totext"] is None
    assert report["library"]["doc"]["title"] == "停修申請表"
    assert any("导出资料夹" in m for m in report["missing"])


def test_a_database_built_before_pipeline_8_still_traces(w):
    for column in ("tier", "site", "unit_line", "date_line", "expires", "valid_ay", "rank_date", "rank_undated"):
        w.conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    w.conn.executescript("DROP TABLE sites; DROP TABLE doc_alias; DROP TABLE doc_scope;")
    report = _trace(w, doc_id(NOTICE_URL))
    lib = report["library"]
    assert lib["doc"]["title"] == "115-1 停修公告" and lib["site"] is None and lib["scope"] == [] and lib["copies"] == []
    assert "expires" not in lib["doc"] and len(lib["chunks"]) == 2
    assert report["export"] is None and any("导出" in m for m in report["missing"])
    assert trace_text(report)


def test_the_own_title_search_can_be_given_and_may_fail(w):
    kept = doc_id(NOTICE_URL)
    report = _trace(w, kept, own_title_search=lambda conn, title: (["dz", kept], "hybrid"))
    assert report["search"] == {"query": "115-1 停修公告", "rank": 2, "within": 50, "how": "hybrid"}
    assert _trace(w, kept, own_title_search=lambda conn, title: (["dz"], "hybrid"))["search"]["rank"] is None

    def broken(conn, title):
        raise RuntimeError("models are away")

    report = _trace(w, kept, own_title_search=broken)
    assert report["search"] is None and any("models are away" in m for m in report["missing"])


def test_the_chunks_shown_are_capped_and_counted(w):
    lib = _trace(w, NOTICE_URL, max_chunks=1)["library"]
    assert len(lib["chunks"]) == 1 and lib["chunks_total"] == 2


def test_the_text_has_the_five_steps_in_order(w):
    text = trace_text(_trace(w, NOTICE_URL))
    marks = [text.index(m) for m in ("① 抓取", "② 导出", "③ 转文字", "④ 建库", "⑤ 检索")]
    assert marks == sorted(marks)
    for word in ("115-1 停修公告", "2026-10-21", "公告頁日期欄", "教務處", "註冊組", "u80", "主料", "第 1 名",
                 "115學年度第1學期停修申請自11月3日起受理。"):
        assert word in text, word


def test_the_report_is_json(w):
    for ref in (NOTICE_URL, PAGER_URL, "junk"):
        report = _trace(w, ref)
        assert json.loads(json.dumps(report, ensure_ascii=False))["ref"] == ref


def test_times_and_sizes_are_written_plainly():
    from nknu.ops.trace import size_words, when

    assert when("2026-11-08T03:12:00+08:00") == "2026-11-08 03:12" and when("") == "" and when("昨天") == "昨天"
    assert [size_words(n) for n in (None, 13, 2048, 5 * 1024 * 1024)] == ["", "13 字节", "2.0 KB", "5.0 MB"]


def test_the_text_writes_the_fetch_time_plainly(w):
    assert "2026-11-08 03:12" in trace_text(_trace(w, NOTICE_URL))


def test_a_file_on_another_host_is_found_in_the_manifest_of_the_site_that_links_it(w):
    """An attachment on a host that is no site of ours is exported with the site whose page links it: when the sites
    of its own host do not list it, every site's manifest is asked, once per process."""
    report = _trace(w, AWAY_URL)
    assert report["export"]["site"] == "school.example" and report["export"]["row"]["网址"] == AWAY_URL
    assert "链接已失效" in report["left_out"]["words"] and report["missing"] == []


def test_what_no_manifest_lists_says_how_far_it_looked(w):
    report = _trace(w, "https://files.example/never-linked.pdf")
    assert report["export"] is None and any("2 个网站的清单都找过了" in m for m in report["missing"])


def test_manifests_that_cannot_be_read_are_named_when_nothing_is_found(w):
    shutil.move(w.exports[world.BOARD], w.exports[world.BOARD].with_name("moved-away"))
    report = _trace(w, "https://files.example/never-linked.pdf")
    said = [m for m in report["missing"] if "找过了" in m]
    assert len(said) == 1 and "2 个网站" in said[0] and "读得到的 1 个" in said[0] and "school.example_board" in said[0]


def test_with_the_corpus_unreadable_the_reason_is_not_guessed(w):
    (w.root / world.CORPUS).unlink()
    report = _trace(w, PAGER_URL)
    assert report["record"] is None and report["left_out"]["key"] == ""
    assert "没有留下它" not in report["left_out"]["words"] and "说不出" in report["left_out"]["words"]
    assert sum("记录档" in m for m in report["missing"]) == 1


def test_a_corpus_assembled_again_since_the_build_is_said(w):
    corpus = w.root / world.CORPUS
    corpus.write_text(corpus.read_text(encoding="utf-8") + " ", encoding="utf-8")  # the same records, another sha256
    report = _trace(w, PAGER_URL)
    assert any("重新整理过" in m for m in report["missing"])
    assert report["left_out"]["key"] == "default(列表翻页)" and "不一定" in report["left_out"]["words"]


def test_a_row_a_button_brought_is_asked_about_under_its_own_key(w):
    report = _trace(w, world.PRESS_URL)
    assert report["export"]["row"]["按钮编号"] == "row-key-1"
    assert report["record"]["kind"] == "newslist#翻页/按钮" and report["left_out"]["key"] == "newslist#翻页/按钮"


def test_a_layout_the_passages_cannot_be_read_from_still_traces(w):
    """A library laid out before the table-header columns (the skeleton's): the chunks are shown without the passage
    the models read, and the report says so (review 11-15: a traceback)."""
    w.conn.execute("ALTER TABLE chunks DROP COLUMN head_end")
    w.conn.execute("ALTER TABLE chunks DROP COLUMN head_start")
    lib = _trace(w, NOTICE_URL)
    assert len(lib["library"]["chunks"]) == 2 and lib["library"]["chunks"][0]["passage"] == ""
    assert lib["library"]["chunks"][0]["text"] == "115學年度第1學期停修申請自11月3日起受理。"
    assert any("模型实际读到的字" in m for m in lib["missing"])


def test_the_cohort_a_document_applies_to_is_printed(w):
    report = _trace(w, NOTICE_URL)
    assert report["library"]["applies_to"] == [{"unit": "教務處", "cohort_from": 113, "cohort_exact": None,
                                                "raw": "113學年度起入學"}]
    assert "113 学年度起入学" in trace_text(report)


def test_an_address_without_its_scheme_is_an_address(w):
    report = _trace(w, PAGER_URL.removeprefix("https://"))
    assert report["export"]["row"]["页别"] == "列表翻页" and report["left_out"]["key"] == "default(列表翻页)"
