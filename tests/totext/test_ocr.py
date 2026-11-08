"""OCR (plan 2026-11-02 T4, as changed by addendum A of 11-02 evening): the page rules, the recogniser's charset,
page analysis and rendering, tiles, merging pages, the image route, and the PDF route choosing between the OCR-on
Docling converter and, for a PDF whose every page is ours to read, our OCR alone (11-02 night: no Docling at all).
Fakes only, except the tests marked models (real RapidOCR, real Docling)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from nknu.totext import ocr
from nknu.totext.convert import Tools, convert_file
from nknu.totext.docling_convert import DOCLING_ARTIFACTS
from nknu.totext.ocr import (
    FEW_CHARS,
    MISREAD_MIN,
    MISREAD_SHARE,
    OcrLine,
    PageInfo,
    analyze_pdf,
    lines_text,
    merge_pages,
    misread_count,
    ocr_image,
    ocr_pdf_pages,
    ocr_plan,
    simplified_only,
    tiles,
)
from nknu.totext.route import Route
from tests.totext.fixtures import (
    FakeEngine,
    cjk_font,
    image_pdf,
    mixed_pdf,
    png,
    refuse_real_models,
    text_image,
    tiny_pdf,
)
from tests.totext.test_docling_convert import NUMBERING_LOST
from tests.totext.test_run import PDF_TEXT, FakeDocling

BOX = (0.0, 0.0, 1.0, 1.0)
PDF = Route("pdf", "pdf")
MIXED_LINE = "Course rules for the autumn term of the school"  # 38 non-space characters: no rule flags the page


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, request):
    refuse_real_models(monkeypatch, request)


def _info(page=1, chars=200, misread=0, share=0.0) -> PageInfo:
    return PageInfo(page=page, chars=chars, misread=misread, misread_share=share, images=(), size=(595.0, 842.0))


def _tools(off=None, on=None, engine=None, beat=None) -> Tools:
    return Tools(docling=off or FakeDocling(), docling_ocr=on or FakeDocling(), ocr=engine or FakeEngine(), beat=beat)


# --- rules ---

def test_ocr_plan():
    # MISREAD_SHARE was 0.02 (addendum A's starting value); the phase-1 review moved it to 0.1. Measured on the 10-27
    # export once 詴/帄 are folded: legitimate rare characters (names in lists, 丶 as a bullet, 郍) reach a share of
    # 0.0353 at most, the garbled syllabi start at 0.2377; 0.1 flags the same 25 pages there and keeps a list page
    # with a few rare name characters off whole-page OCR, which would lose its table and misread those names anyway
    assert (FEW_CHARS, MISREAD_MIN, MISREAD_SHARE) == (30, 3, 0.1)
    assert ocr_plan(_info(chars=12)) == "few_chars" and ocr_plan(_info(chars=0, misread=5, share=1.0)) == "few_chars"
    assert ocr_plan(_info(chars=29)) == "few_chars" and ocr_plan(_info(chars=30)) is None  # the spec's boundaries
    assert ocr_plan(_info(misread=3, share=0.1)) == "misread" and ocr_plan(_info(misread=80, share=0.24)) == "misread"
    assert ocr_plan(_info(misread=2, share=0.5)) is None and ocr_plan(_info(misread=40, share=0.099)) is None
    assert ocr_plan(_info(misread=3, share=0.0353)) is None


def test_plan_reasons():
    assert ocr.plan_reason("few_chars", _info(chars=3)) == "没有可用的文字层"
    assert ocr.plan_reason("misread", _info(misread=7, share=0.1)) == "文字层有 7 个认不得的字"


def test_misread_count():
    known = frozenset("一二選課期間")
    assert misread_count("一、選課期間", known) == (0, 0.0)
    assert misread_count("選課詴郍", known) == (2, 0.5)
    assert misread_count(chr(0x2F00) + "二", known) == (0, 0.0)  # the Kangxi radical ⼀ folds to 一
    assert misread_count(chr(0x2F01) + "二", known) == (1, 0.5)  # ⼁ folds to 丨, which this charset lacks
    assert misread_count(chr(0xF900) + "一", frozenset("豈一")) == (0, 0.0)  # a compatibility ideograph folds too
    assert misread_count(chr(0x2E80) + "一", known) == (1, 0.5)  # a radical NFKC leaves alone stays unknown
    assert misread_count("abc 123，。", known) == (0, 0.0)
    # no Han character: the charset is never read (the guard makes ocr_charset raise)
    assert misread_count("Hello course rules") == (0, 0.0)
    # 詴 and 帄 are 試 and 平 in a mismapped text layer (normalize folds them in PDF text): known, so a page whose only
    # odd characters they are keeps Docling's text and tables instead of whole-page OCR (review of phase 1)
    assert misread_count("考詴和帄", frozenset("考試和平")) == (0, 0.0)
    assert misread_count("考詴郍", frozenset("考試那")) == (1, 1 / 3)  # 郍 is not folded


def test_simplified_only():
    assert simplified_only("這是测试台") == 2 and simplified_only("臺灣台北市OO里") == 0


# --- page analysis and rendering ---

def test_analyze_image_only_pdf(tmp_path):
    [info] = analyze_pdf(image_pdf(tmp_path / "s.pdf"))
    assert (info.page, info.chars, info.misread, info.misread_share) == (1, 0, 0, 0.0)
    assert len(info.images) == 1 and info.size == pytest.approx((595.44, 842.04), abs=0.01)
    assert ocr_plan(info) == "few_chars"


def test_analyze_text_pdf(tmp_path):
    one, two = analyze_pdf(tiny_pdf(tmp_path / "t.pdf", [PDF_TEXT, None]))
    assert (one.page, one.chars, one.images, ocr_plan(one)) == (1, 48, (), None)  # non-space characters
    assert (two.page, two.chars, ocr_plan(two)) == (2, 0, "few_chars")


def test_analyze_reads_from_the_offset_and_passes_the_charset(tmp_path, monkeypatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"debug line\n" + tiny_pdf(tmp_path / "t.pdf", [PDF_TEXT]).read_bytes())
    seen = []
    monkeypatch.setattr(ocr, "misread_count", lambda text, charset=None: seen.append((text.strip(), charset)) or (0, 0))
    [info] = analyze_pdf(path, offset=11, charset=frozenset("一"))
    assert info.chars == 48 and seen == [(PDF_TEXT.strip(), frozenset("一"))]


def test_tiles():
    assert tiles(600, 5000) == [(0, 0, 600, 2000), (0, 1900, 600, 3900), (0, 3800, 600, 5000)]
    assert tiles(1654, 2339) == [(0, 0, 1654, 2339)]  # an A4 render at 200 dpi: RapidOCR's own resize is mild
    assert tiles(5000, 600) == [(0, 0, 2000, 600), (1900, 0, 3900, 600), (3800, 0, 5000, 600)]
    assert tiles(3000, 2000) == [(0, 0, 3000, 2000)]  # long side over 2,000 px but not twice the short side
    assert tiles(600, 2000) == [(0, 0, 600, 2000)]


def test_ocr_pdf_pages_renders_the_planned_pages_whole(tmp_path):
    path = tiny_pdf(tmp_path / "t.pdf", [PDF_TEXT, None, None])
    engine = FakeEngine([OcrLine("掃描", 0.9, BOX)])
    beats = []
    read, failed = ocr_pdf_pages(path, {2: "few_chars", 3: "few_chars"}, engine, beat=lambda: beats.append(1))
    assert read == {2: [OcrLine("掃描", 0.9, BOX)], 3: [OcrLine("掃描", 0.9, BOX)]} and failed == {}
    assert engine.read_images == [(1700, 2200, "RGB")] * 2 and beats == [1, 1]  # 612×792 points at 200 dpi


def test_ocr_pdf_pages_returns_each_failure(tmp_path):
    path = tiny_pdf(tmp_path / "t.pdf", [None, None])
    read, failed = ocr_pdf_pages(path, {1: "few_chars", 2: "few_chars"}, FakeEngine(raises=ValueError("bad")))
    assert read == {} and failed == {1: "ValueError: bad", 2: "ValueError: bad"}


def test_a_huge_page_is_rendered_within_the_pixel_cap(tmp_path):
    path = tiny_pdf(tmp_path / "poster.pdf", [None], size=(14400, 14400))  # 200 inches: 40,000 px a side at 200 dpi
    engine = FakeEngine()
    ocr_pdf_pages(path, {1: "few_chars"}, engine)
    [(width, height, _mode)] = engine.read_images
    assert width == height and width * height <= ocr.RENDER_MAX_PIXELS and width > 4000


# --- lines and pages ---

def test_lines_text_keeps_a_row_on_one_line():
    lines = [OcrLine("選課期間", 0.9, (130, 10, 400, 50)), OcrLine("一、", 0.9, (60, 12, 118, 48)),
             OcrLine("自九月一日起", 0.9, (60, 70, 300, 110)), OcrLine("項目", 0.9, (60, 130, 120, 170)),
             OcrLine("金額", 0.9, (400, 131, 460, 169))]
    # a tight gap (under half the line's height) joins without a space, a wide one (a table's next cell) with one
    assert lines_text(lines) == "一、選課期間\n自九月一日起\n項目 金額"
    assert lines_text([]) == ""


def test_merge_pages():
    text, starts = merge_pages(["p1", "", "p3 text"], {2: "few_chars"}, {2: [OcrLine("掃描文字", 0.99, BOX)]})
    assert text == "p1\n\n掃描文字\n\np3 text" and starts == [0, 4, 10]
    # a planned page OCR found nothing on keeps Docling's text; a page Docling did not return is ''
    text, starts = merge_pages(["p1", "header"], {2: "few_chars"}, {2: []}, count=3)
    assert text == "p1\n\nheader\n\n" and starts == [0, 4, 12]
    # a page whose OCR failed is missing from ocr: Docling's text stays
    assert merge_pages(["p1", "p2"], {2: "misread"}, {})[0] == "p1\n\np2"


# --- RapidOCR's output and the engine ---

def test_rapidocr_output_becomes_lines():
    result = SimpleNamespace(boxes=np.array([[[10, 20], [110, 22], [110, 60], [10, 58]],
                                             [[0, 0], [5, 0], [5, 5], [0, 5]],
                                             [[0, 70], [50, 70], [50, 90], [0, 90]]], dtype=float),
                             txts=(" 一、選課期間 ", "x", "  "), scores=(0.97, 0.3, 0.9))
    assert ocr._lines_of(result) == [OcrLine("一、選課期間", 0.97, (10.0, 20.0, 110.0, 60.0))]
    assert ocr._lines_of(SimpleNamespace(boxes=None, txts=None, scores=None)) == []
    assert ocr._lines_of(SimpleNamespace()) == []  # a detection-only or classifier-only output


def test_rapid_engine_is_lazy_and_offline(monkeypatch):
    params = ocr.rapid_params()
    assert params["Global.log_level"] == "warning" and params["Global.text_score"] == ocr.MIN_LINE_SCORE
    for key, name in (("Det.model_path", "PP-OCRv6_det_small.onnx"), ("Rec.model_path", "PP-OCRv6_rec_small.onnx"),
                      ("Cls.model_path", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")):
        assert params[key].endswith(name) and Path(params[key]).is_file()  # the wheel's own files, by path
    built, loaded = [], []

    class Reader:
        def __call__(self, image):
            return SimpleNamespace(boxes=np.array([[[0, 0], [9, 0], [9, 9], [0, 9]]], dtype=float), txts=("字",),
                                   scores=(0.99,))

        def _load_det_model(self):
            loaded.append("det")

        def _load_cls_model(self):
            loaded.append("cls")

        def _load_rec_model(self):
            loaded.append("rec")

    monkeypatch.setattr(ocr, "_rapidocr", lambda: built.append(1) or Reader())
    engine = ocr.RapidEngine()
    assert built == []
    engine.warm()
    assert built == [1] and loaded == ["det", "cls", "rec"]
    assert engine.read(object()) == [OcrLine("字", 0.99, (0.0, 0.0, 9.0, 9.0))] and built == [1]


# --- the image route ---

def test_ocr_image_skips_tiny(tmp_path):
    assert ocr_image(png(tmp_path, 40, 40), FakeEngine([])) == ("", [], "图太小（短边 40 px < 64 px）")


def test_image_route(tmp_path):
    path = png(tmp_path, 300, 120)
    line = OcrLine("公告", 0.93, (5.0, 6.0, 50.0, 30.0))
    c = convert_file(path, Route("image", "png"), Tools(ocr=FakeEngine([line])), tmp_path / "w")
    assert (c.route, c.status, c.reason, c.text, c.pages) == ("image", "ok", "", "公告", 0)
    assert c.ocr == [{"page": 1, "text": "公告", "score": 0.93, "box": [5.0, 6.0, 50.0, 30.0]}]
    assert c.simplified_only == 0 and c.ocr_short_lines == 1  # 公告: two characters
    c = convert_file(path, Route("image", "png"), Tools(ocr=FakeEngine([])), tmp_path / "w2")
    assert (c.status, c.reason, c.text, c.ocr) == ("empty", "OCR 没认出字", "", [])
    tiny = png(tmp_path, 40, 90, name="t.png")
    c = convert_file(tiny, Route("image", "png"), Tools(ocr=FakeEngine([line])), tmp_path / "w3")
    assert (c.status, c.reason) == ("skipped", "图太小（短边 40 px < 64 px）")


def test_tall_image_is_read_in_tiles(tmp_path):
    path = png(tmp_path, 600, 5000)
    engine = FakeEngine(by_call=[[], [OcrLine("中段", 0.9, (10, 100, 200, 140))], []])
    text, lines, skip = ocr_image(path, engine)
    assert [(w, h) for w, h, _mode in engine.read_images] == [(600, 2000), (600, 2000), (600, 1200)]
    assert lines == [OcrLine("中段", 0.9, (10, 2000, 200, 2040))] and text == "中段" and skip is None


def test_a_line_in_two_tiles_counts_once(tmp_path):
    # tile 1 holds rows 0-2000, tile 2 rows 1900-3900: a line at rows 1920-1940 is read by both, kept by the tile
    # whose share of the overlap (split at its middle, row 1950) holds the line's centre
    path = png(tmp_path, 600, 5000)
    engine = FakeEngine(by_call=[[OcrLine("跨界", 0.9, (10, 1920, 200, 1940))],
                                 [OcrLine("跨界", 0.9, (10, 20, 200, 40))], []])
    _text, lines, _skip = ocr_image(path, engine)
    assert lines == [OcrLine("跨界", 0.9, (10, 1920, 200, 1940))]


def test_gif_first_frame_and_transparency_read_as_rgb_on_white(tmp_path):
    from PIL import Image

    gif = tmp_path / "a.gif"
    Image.new("P", (100, 80)).save(gif, save_all=True, append_images=[Image.new("P", (100, 80), 1)])
    clear = png(tmp_path, 100, 80, name="c.png", mode="RGBA", color=(0, 0, 0, 0))
    seen = []

    class Probe:
        def read(self, image):
            seen.append((image.mode, image.getpixel((0, 0))))
            return []

    ocr_image(gif, Probe())
    ocr_image(clear, Probe())
    assert seen[0][0] == "RGB" and seen[1] == ("RGB", (255, 255, 255))  # a transparent pixel is white, not black


# --- the PDF route ---

def test_pdf_route_ocrs_flagged_pages(tmp_path):
    path = tiny_pdf(tmp_path / "a.pdf", [None, PDF_TEXT])  # page 1 blank: few_chars
    on = FakeDocling(pages=["", PDF_TEXT])
    engine = FakeEngine([OcrLine("一、選課期間", 0.99, BOX)])
    c = convert_file(path, PDF, _tools(on=on, engine=engine), tmp_path / "w")
    assert c.ocr_pages == [{"page": 1, "why": "few_chars", "reason": "没有可用的文字层", "lines": 1,
                            "mean_score": 0.99}]
    assert c.text.startswith("一、選課期間") and c.text == "一、選課期間\n\n" + PDF_TEXT and c.page_starts == [0, 8]
    assert c.ocr == [{"page": 1, "text": "一、選課期間", "score": 0.99, "box": [0.0, 0.0, 1.0, 1.0]}]
    assert (c.status, c.reason, c.pages, c.failed_pages) == ("ok", "", 2, [])
    assert engine.read_images == [(1700, 2200, "RGB")]  # page 1 rendered whole at 200 dpi


def test_mixed_pdf_uses_the_ocr_converter(tmp_path):
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])
    off, on = FakeDocling(), FakeDocling(pages=[PDF_TEXT, ""], ocr_pages=(1,))
    c = convert_file(path, PDF, _tools(off, on, FakeEngine([OcrLine("掃描頁", 0.9, BOX)])), tmp_path / "w")
    assert off.calls == [] and on.kinds() == ["pdf"]
    assert c.text == PDF_TEXT + "\n\n掃描頁" and (c.docling_ocr_pages, c.broken_pages) == ([1], [])


def test_text_pdf_uses_the_ocr_converter_and_reads_no_page_itself(tmp_path):
    # no page flagged: Docling's region OCR alone covers any images and outlined text
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, PDF_TEXT])
    off, on, engine = FakeDocling(), FakeDocling(ocr_pages=(2,), broken_pages=(2,)), FakeEngine()
    c = convert_file(path, PDF, _tools(off, on, engine), tmp_path / "w")
    assert off.calls == [] and on.kinds() == ["pdf"] and engine.read_images == []
    assert (c.text, c.ocr_pages, c.docling_ocr_pages, c.broken_pages) == ("page one\n\npage two", [], [2], [2])


def test_all_scanned_pdf_skips_docling(tmp_path):
    # changed 11-02 night (was test_all_scanned_pdf_skips_docling_ocr, the OCR-off converter read the scan): with no
    # usable text layer Docling gave a scan nothing but cost a second set of its models in memory and layout and
    # table inference on each page; now neither converter reads it and the Docling side of the result is empty
    path = tiny_pdf(tmp_path / "a.pdf", [None, None])
    off = FakeDocling(pages=["off one", "off two"], pictures=1)
    on = FakeDocling(pages=["on one", "on two"], ocr_pages=(1,), ocr_text={1: ["on"]})
    engine = FakeEngine([OcrLine("掃描", 0.9, BOX)])
    c = convert_file(path, PDF, _tools(off, on, engine), tmp_path / "w")
    assert off.calls == on.calls == [] and len(engine.read_images) == 2
    assert (c.status, c.reason, c.text, c.pages, c.page_starts) == ("ok", "", "掃描\n\n掃描", 2, [0, 4])
    assert [p["page"] for p in c.ocr_pages] == [1, 2] and c.failed_pages == []
    assert (c.doc_json, c.pictures, c.docling_ocr_pages, c.broken_pages, c.docling_ocr) == (None, 0, [], [], {})


def test_misread_page_is_replaced(tmp_path, monkeypatch):
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, PDF_TEXT])
    infos = [_info(1), _info(2, misread=9, share=0.3)]
    monkeypatch.setattr(ocr, "analyze_pdf", lambda path, offset=0, charset=None: infos)
    on = FakeDocling(pages=["第一頁", "詴郍媙"])
    c = convert_file(path, PDF, _tools(on=on, engine=FakeEngine([OcrLine("選课", 0.95, BOX)])), tmp_path / "w")
    assert c.text == "第一頁\n\n選课" and on.kinds() == ["pdf"]
    assert c.ocr_pages == [{"page": 2, "why": "misread", "reason": "文字层有 9 个认不得的字", "lines": 1,
                            "mean_score": 0.95}]
    assert c.simplified_only == 1  # 课 is counted over the OCR text: a flag, never a fix


def test_ocr_failure_on_a_page_is_partial(tmp_path):
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])
    on = FakeDocling(pages=[PDF_TEXT, "頁尾"])
    c = convert_file(path, PDF, _tools(on=on, engine=FakeEngine(raises=RuntimeError("boom"))), tmp_path / "w")
    assert (c.status, c.reason, c.failed_pages) == ("partial", "第 2 页 OCR 失败（RuntimeError: boom）", [2])
    assert c.text == PDF_TEXT + "\n\n頁尾" and c.ocr_pages == [] and c.ocr == []


def test_analysis_failure_keeps_docling_text(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("pdfium cannot read it")

    monkeypatch.setattr(ocr, "analyze_pdf", broken)
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, PDF_TEXT])
    off, on, engine = FakeDocling(), FakeDocling(pages=["p1", "p2"]), FakeEngine([OcrLine("x", 0.9, BOX)])
    c = convert_file(path, PDF, _tools(off, on, engine), tmp_path / "w")
    assert (c.status, c.reason, c.text) == ("partial", "无法分析页面，没做整页 OCR", "p1\n\np2")
    assert off.calls == [] and on.kinds() == ["pdf"] and engine.read_images == [] and c.ocr_pages == []
    timeout = FakeDocling(status="partial", reason="Docling 超时（900 秒），只转出前面几页", pages=["p1"])
    c = convert_file(path, PDF, _tools(on=timeout), tmp_path / "w2")
    assert (c.status, c.text) == ("partial", "p1")
    assert c.reason == "Docling 超时（900 秒），只转出前面几页；无法分析页面，没做整页 OCR"
    gone = FakeDocling(status="failed", reason="docling-parse could not load document", pages=[])
    c = convert_file(path, PDF, _tools(on=gone), tmp_path / "w3")
    assert (c.status, c.text) == ("failed", "")
    assert c.reason == "docling-parse could not load document；无法分析页面，没做整页 OCR"


def test_failed_docling_is_not_ocred(tmp_path):
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])
    gone = FakeDocling(status="failed", reason="docling-parse could not load document", pages=[])
    engine = FakeEngine([OcrLine("x", 0.9, BOX)])
    c = convert_file(path, PDF, _tools(on=gone, engine=engine), tmp_path / "w")
    assert (c.status, c.reason, c.text, c.doc_json) == ("failed", "docling-parse could not load document", "", None)
    assert engine.read_images == []


def test_scan_docling_cannot_read_never_reaches_docling(tmp_path):
    # replaces test_scan_docling_cannot_read_is_still_ocred (review of phase 1: docling-parse failing on a scan that
    # pypdfium2 reads cost no text, the file partial with the reason DOCLING_FAILED_OCR). Since 11-02 night neither
    # converter sees an all-scanned PDF: whatever Docling would make of it, the text is our OCR's and the file is ok
    path = tiny_pdf(tmp_path / "a.pdf", [None, None])
    off = FakeDocling(status="failed", reason="docling-parse could not load document", pages=[])
    on = FakeDocling(status="failed", reason="docling-parse could not load document", pages=[])
    engine = FakeEngine([OcrLine("掃描", 0.9, BOX)])
    c = convert_file(path, PDF, _tools(off, on, engine), tmp_path / "w")
    assert off.calls == on.calls == [] and len(engine.read_images) == 2
    assert (c.status, c.reason, c.text, c.pages, c.doc_json) == ("ok", "", "掃描\n\n掃描", 2, None)
    assert [p["page"] for p in c.ocr_pages] == [1, 2]


def test_scanned_page_our_ocr_cannot_read_is_blank(tmp_path):
    # 11-02 night: an all-scanned PDF has no Docling text to fall back on, so a page our OCR failed on or read no line
    # from is ''; a failure stays in failed_pages with the file partial, and a scan our OCR reads nothing on is empty
    path = tiny_pdf(tmp_path / "a.pdf", [None, None, None])
    off = FakeDocling(pages=["off one", "off two", "off three"])

    class FirstReadFails(FakeEngine):
        def read(self, image):
            lines = super().read(image)
            if len(self.read_images) == 1:
                raise RuntimeError("boom")
            return lines

    engine = FirstReadFails(by_call=[[], [], [OcrLine("掃描", 0.9, BOX)]])  # page 1 raises, page 2 reads no line
    c = convert_file(path, PDF, _tools(off=off, engine=engine), tmp_path / "w")
    assert off.calls == [] and len(engine.read_images) == 3
    assert (c.status, c.text, c.page_starts, c.failed_pages) == ("partial", "\n\n\n\n掃描", [0, 2, 4], [1])
    assert c.reason == "第 1 页 OCR 失败（RuntimeError: boom）"
    assert [(p["page"], p["lines"]) for p in c.ocr_pages] == [(2, 0), (3, 1)]
    c = convert_file(path, PDF, _tools(off=off, engine=FakeEngine([])), tmp_path / "w2")
    assert (c.status, c.reason, c.text, c.failed_pages) == ("empty", "PDF 转出来没有文字（OCR 也没认出字）", "\n\n\n\n", [])
    assert off.calls == []


def test_partial_without_text_is_failed(tmp_path):
    # review of phase 1: OCR raising on every page of a scan left a partial with no text, which T7a's fallback (for
    # failed files) never reached. 11-02 night: its second case (Docling failing on a scan OCR read nothing on, the
    # reason DOCLING_FAILED_OCR) cannot happen any more; test_scanned_page_our_ocr_cannot_read_is_blank has the rest
    path = tiny_pdf(tmp_path / "a.pdf", [None, None])
    broken = FakeEngine(raises=RuntimeError("boom"))
    c = convert_file(path, PDF, _tools(engine=broken), tmp_path / "w")
    assert (c.status, c.text, c.failed_pages) == ("failed", "\n\n", [1, 2])
    assert c.reason == "第 1、2 页 OCR 失败（RuntimeError: boom）"


def test_missing_docling_page_is_ocred(tmp_path):
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])
    on = FakeDocling(status="partial", reason="Docling 超时（900 秒），只转出前面几页", pages=[PDF_TEXT])  # page 2 lost
    c = convert_file(path, PDF, _tools(on=on, engine=FakeEngine([OcrLine("第二頁", 0.9, BOX)])), tmp_path / "w")
    assert c.pages == 2 and c.text == PDF_TEXT + "\n\n第二頁" and c.status == "partial"
    assert c.reason == "Docling 超时（900 秒），只转出前面几页"


def test_beat_after_each_ocr_page_and_image(tmp_path):
    beats = []
    tools = _tools(engine=FakeEngine([OcrLine("掃描", 0.9, BOX)]), beat=lambda: beats.append(1))
    convert_file(tiny_pdf(tmp_path / "a.pdf", [None, None]), PDF, tools, tmp_path / "w")
    assert len(beats) == 2  # two pages read; since 11-02 night an all-scanned PDF makes no Docling call (it made one)
    convert_file(png(tmp_path, 100, 100), Route("image", "png"), tools, tmp_path / "w2")
    assert len(beats) == 3


def test_region_ocr_text_is_flagged_and_its_short_lines_counted(tmp_path):
    # review of phase 1: the text Docling's region OCR read was never checked for Simplified forms, and its noise (a
    # diagram's labels, a seal) never showed in the report
    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])  # page 2: few_chars, replaced by our OCR
    on = FakeDocling(pages=[PDF_TEXT, ""], ocr_pages=(1, 2), ocr_text={1: ["教职员名冊", "Rf", "十"], 2: ["这"]})
    engine = FakeEngine([OcrLine("掃描", 0.9, BOX), OcrLine("x", 0.9, BOX)])
    c = convert_file(path, PDF, _tools(on=on, engine=engine), tmp_path / "w")
    # 职 and 员 on page 1; page 2 shows our OCR, so the 这 Docling read there is not in the text and not counted
    assert (c.simplified_only, c.ocr_short_lines) == (2, 4)  # short: Rf and 十 (Docling's), 掃描 and x (ours)
    from nknu.totext.convert import result_of

    assert result_of(c, PDF, seconds=1.0, tools={})[0]["ocr_short_lines"] == 4
    assert ocr.short_lines(["一、選課", "附件", " ", "x y"]) == 2 and ocr.SHORT_LINE_CHARS == 2


def test_result_json_carries_the_ocr_paths(tmp_path):
    from nknu.totext.convert import result_of

    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])
    on = FakeDocling(pages=[PDF_TEXT, ""], ocr_pages=(1, 2), broken_pages=(1,))
    c = convert_file(path, PDF, _tools(on=on, engine=FakeEngine([OcrLine("掃描", 0.9, BOX)])), tmp_path / "w")
    result, _text, extras = result_of(c, PDF, seconds=1.0, tools={})
    assert (result["docling_ocr_pages"], result["broken_pages"], result["failed_pages"]) == ([1, 2], [1], [])
    assert [(p["page"], p["why"], p["reason"]) for p in result["ocr_pages"]] == [(2, "few_chars", "没有可用的文字层")]
    assert (result["ocr_lines"], result["ocr_mean_score"]) == (1, 0.9) and "ocr.json" in extras


def test_docling_ocr_lines_are_kept_beside_the_result(tmp_path):
    # main session, before the full run: later stages must be able to tell text read off a picture from the text
    # layer without converting again
    from nknu.totext.convert import result_of

    path = tiny_pdf(tmp_path / "a.pdf", [PDF_TEXT, None])  # page 2: few_chars, replaced by our OCR
    on = FakeDocling(pages=[PDF_TEXT, ""], ocr_pages=(1, 2), ocr_text={1: ["一、選課期間", "Rf"], 2: ["這"]})
    c = convert_file(path, PDF, _tools(on=on, engine=FakeEngine([OcrLine("掃描", 0.9, BOX)])), tmp_path / "w")
    assert c.docling_ocr == {1: ["一、選課期間", "Rf"]}  # page 2 shows our OCR, so Docling's 這 is not in the text
    result, _text, extras = result_of(c, PDF, seconds=1.0, tools={})
    assert "docling_ocr" not in result
    assert json.loads(extras["docling_ocr.json"]) == {"1": ["一、選課期間", "Rf"]}
    plain = tiny_pdf(tmp_path / "b.pdf", [PDF_TEXT])
    c = convert_file(plain, PDF, _tools(on=FakeDocling(pages=[PDF_TEXT])), tmp_path / "w2")
    assert "docling_ocr.json" not in result_of(c, PDF, seconds=1.0, tools={})[2]


# --- real models ---

@pytest.mark.models
def test_real_ocr_charset():
    charset = ocr.ocr_charset()
    assert len(charset) == 18708 and {"選", "課", "臺", "灣"} <= charset and not {"詴", "郍", "媙"} & charset


@pytest.mark.models
def test_real_rapidocr_reads_traditional():
    font = cjk_font()
    if font is None:
        pytest.skip("no CJK font on this machine")
    lines = ocr.RapidEngine().read(text_image("一、選課期間", font))
    # the plan's assertion; the numbering is not asserted: on 11-02 PP-OCRv6 small dropped a leading 一、 on this
    # crop, and kept 38 of 54 numbering marks on synthetic A4 pages at 200 dpi (three fonts, 10.5-14 pt)
    assert "選課期間" in lines_text(lines) and all(line.score >= ocr.MIN_LINE_SCORE for line in lines)


@pytest.mark.models
def test_real_pdf_route_reads_both_layers(tmp_path):
    # a page with native text and an image of text: Docling's region OCR reads the image, no page is ours to read.
    # Changed after the phase-1 review, as test_real_docling_region_ocr: the lost 「一、」 shows as an expected failure
    if not (DOCLING_ARTIFACTS / "docling-project--docling-layout-heron").is_dir():
        pytest.skip(f"no Docling layout model under {DOCLING_ARTIFACTS}")
    font = cjk_font()
    if font is None:
        pytest.skip("no CJK font on this machine")
    path = mixed_pdf(tmp_path / "m.pdf", MIXED_LINE, text_image("一、選課期間", font))
    c = convert_file(path, PDF, Tools(), tmp_path / "w")
    assert c.status == "ok" and c.ocr_pages == [] and c.docling_ocr_pages == [1] and c.broken_pages == []
    assert MIXED_LINE in c.text and "選課期間" in c.text and c.simplified_only == 0
    if "一、選課期間" not in c.text:
        pytest.xfail(NUMBERING_LOST)
