"""Docling for PDF, docx, xlsx and pptx: options, status mapping, the Word numbering fix, offline loading (plan
2026-11-02 T3a). Only the test marked models loads Docling's real models."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docling_core.types.doc import ContentLayer

from nknu.totext import docling_convert
from nknu.totext.docling_convert import (
    DOC_TIMEOUT_S,
    DOCLING_ARTIFACTS,
    MD_OPTIONS,
    DoclingConverter,
    DoclingOut,
    artifact_commits,
    build_converter,
    fix_docx_numbering,
    pdf_options,
)
from tests.totext.fixtures import cjk_font, docx_with_table, mixed_pdf, text_image, tiny_pdf, zip_of

REPO_ROOT = Path(__file__).resolve().parents[2]
RAPIDOCR_FILES = {"PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}


def test_md_options():
    assert MD_OPTIONS == {"escape_html": False, "escape_underscores": False, "image_placeholder": "",
                          "compact_tables": True, "traverse_pictures": True,
                          "included_content_layers": {ContentLayer.BODY, ContentLayer.FURNITURE}}


def test_pdf_options(tmp_path):
    from docling.datamodel.pipeline_options import TableFormerMode

    o = pdf_options(device="cpu", artifacts=tmp_path)
    assert (o.do_ocr, o.do_table_structure, o.table_structure_options.mode) == (False, True, TableFormerMode.ACCURATE)
    assert o.artifacts_path == tmp_path and o.document_timeout == 3600 and str(o.accelerator_options.device).endswith("cpu")
    assert DOC_TIMEOUT_S == 3600  # 11-02 23:59: 100+ page PDFs passed 900 s on the memory-starved Mac and came out partial
    assert pdf_options(device="auto", artifacts=tmp_path, timeout_s=5).document_timeout == 5


def test_pdf_options_ocr(tmp_path):
    # addendum A: Docling's layout-region OCR with RapidOCR, a junk text layer re-read whole; the wheel's own model
    # files by path, so nothing is looked for under <artifacts>/RapidOcr/
    from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions, TableFormerMode

    o = pdf_options(device="cpu", artifacts=tmp_path, ocr=True)
    r = o.ocr_options
    assert o.do_ocr is True and isinstance(r, RapidOcrOptions)
    assert (r.mode, r.replace_broken_text_layer, r.lang, r.backend) == (OcrMode.DEFAULT, True, ["iso:zh-Hant"],
                                                                         "onnxruntime")
    paths = [Path(p) for p in (r.det_model_path, r.rec_model_path, r.cls_model_path)]
    assert all(p.is_file() for p in paths) and {p.name for p in paths} == RAPIDOCR_FILES
    assert [p.name for p in paths] == ["PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx",
                                       "ch_ppocr_mobile_v2.0_cls_mobile.onnx"]
    # the rest as without OCR
    assert (o.do_table_structure, o.table_structure_options.mode) == (True, TableFormerMode.ACCURATE)
    assert o.artifacts_path == tmp_path and o.document_timeout == 3600
    assert str(o.accelerator_options.device).endswith("cpu")
    assert pdf_options(device="cpu", artifacts=tmp_path).do_ocr is False


def test_ocr_converter_is_lazy_and_takes_the_ocr_options(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(docling_convert, "build_converter", lambda options: built.append(options) or "converter")
    c = DoclingConverter(artifacts=tmp_path, ocr=True)
    assert built == [] and c.ocr is True and DoclingConverter().ocr is False
    assert c._docling() == "converter" and c._docling() == "converter"
    [options] = built
    assert options.do_ocr is True and options.ocr_options.replace_broken_text_layer is True


def test_build_converter_takes_the_pdf_options(tmp_path):
    # one function builds every converter: a later OCR converter passes other PDF options to it
    from docling.datamodel.base_models import InputFormat

    options = pdf_options(device="cpu", artifacts=tmp_path)
    converter = build_converter(options)
    assert set(converter.allowed_formats) == {InputFormat.PDF, InputFormat.DOCX, InputFormat.XLSX, InputFormat.PPTX}
    assert converter.format_to_options[InputFormat.PDF].pipeline_options is options


def test_converter_is_lazy_and_device_defaults_to_auto(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(docling_convert, "build_converter", lambda options: built.append(options) or "converter")
    c = DoclingConverter(artifacts=tmp_path, timeout_s=7)
    assert built == [] and c.device == "auto"
    assert c._docling() == "converter" and c._docling() == "converter"
    [options] = built
    assert str(options.accelerator_options.device).endswith("auto") and options.document_timeout == 7
    assert options.artifacts_path == tmp_path


def _err(message: str, category: str = "unknown"):
    from docling.datamodel.base_models import DoclingComponentType, ErrorItem

    return ErrorItem(component_type=DoclingComponentType.PIPELINE, module_name="m", error_message=message,
                     category=category)


def test_status_mapping():
    from docling.datamodel.base_models import ConversionStatus

    status = docling_convert._status
    assert status(SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[])) == ("ok", "")
    timeout = _err("Pipeline stage timeout: processed 3/9 pages", "timeout")
    assert status(SimpleNamespace(status=ConversionStatus.PARTIAL_SUCCESS, errors=[timeout])) == (
        "partial", "Docling 超时（3600 秒），只转出前面几页")
    assert status(SimpleNamespace(status=ConversionStatus.PARTIAL_SUCCESS, errors=[timeout]), 60) == (
        "partial", "Docling 超时（60 秒），只转出前面几页")
    long = "x" * 300
    assert status(SimpleNamespace(status=ConversionStatus.FAILURE, errors=[_err(long), _err("second")])) == (
        "failed", long[:200])
    assert status(SimpleNamespace(status=ConversionStatus.PARTIAL_SUCCESS, errors=[_err("page 2 failed")])) == (
        "partial", "page 2 failed")
    assert status(SimpleNamespace(status=ConversionStatus.FAILURE, errors=[]))[0] == "failed"
    assert status(SimpleNamespace(status=ConversionStatus.SKIPPED, errors=[_err("File format not allowed")])) == (
        "failed", "File format not allowed")


class _FakeDoc:
    def __init__(self, pages: dict[int, str], whole: str = "", pictures: int = 0):
        self.pages = dict.fromkeys(pages)
        self._md = pages
        self._whole = whole
        self.pictures = [object()] * pictures

    def export_to_markdown(self, page_no=None, **kwargs):
        assert kwargs == MD_OPTIONS
        return self._whole if page_no is None else self._md.get(page_no, "")

    def export_to_dict(self):
        return {"name": "fake"}


class _FakeConverter:
    def __init__(self, result):
        self.result, self.calls = result, []

    def convert(self, source, raises_on_error=True):
        self.calls.append((source, raises_on_error))
        return self.result


def _with(monkeypatch, result) -> tuple[DoclingConverter, _FakeConverter]:
    fake = _FakeConverter(result)
    monkeypatch.setattr(docling_convert, "build_converter", lambda options: fake)
    return DoclingConverter(), fake


def test_pdf_pages_one_per_page(monkeypatch, tmp_path):
    from docling.datamodel.base_models import ConversionStatus

    doc = _FakeDoc({1: "page one", 2: "", 3: "page three"}, pictures=2)
    c, fake = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc))
    out = c.convert(tmp_path / "a.pdf", "pdf")
    assert out == DoclingOut("ok", "", ["page one", "", "page three"], {"name": "fake"}, 2)
    assert fake.calls == [(tmp_path / "a.pdf", False)]


def test_memory_is_handed_back_after_every_file(monkeypatch, tmp_path):
    # main session 11-02 21:55: one run's process held 2.4 GB of Metal memory after 17 PDFs on a 16 GiB machine that
    # was already swapping; the MPS allocator's cache goes back after each file, a failed one too
    from docling.datamodel.base_models import ConversionStatus

    calls = []
    mps = SimpleNamespace(is_available=lambda: True)
    fake_torch = SimpleNamespace(backends=SimpleNamespace(mps=mps), mps=SimpleNamespace(empty_cache=lambda: calls.append("mps")))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(docling_convert.gc, "collect", lambda: calls.append("gc"))
    doc = _FakeDoc({1: "page one"})
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc))
    c.convert(tmp_path / "a.pdf", "pdf")
    assert calls == ["gc", "mps"]
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.FAILURE, errors=[], document=None))
    c.convert(tmp_path / "b.pdf", "pdf")
    assert calls == ["gc", "mps", "gc", "mps"]
    mps.is_available = lambda: False  # no MPS (CPU only): only the garbage collection
    c.convert(tmp_path / "c.pdf", "pdf")
    assert calls[-1] == "gc" and calls.count("mps") == 2
    monkeypatch.delitem(sys.modules, "torch")  # torch never imported (a test's fake converter): nothing to free
    docling_convert.release_memory()
    assert calls[-1] == "gc"


def test_ocr_and_broken_pages_from_the_confidence_report(monkeypatch, tmp_path):
    # ocr_score is set only when OCR cells reached a page's text; a broken layer forces parse_score to 0
    from docling.datamodel.base_models import ConfidenceReport, ConversionStatus, PageConfidenceScores

    confidence = ConfidenceReport()
    confidence.pages[1] = PageConfidenceScores(parse_score=1.0, ocr_score=0.91)  # region OCR on a page with text
    confidence.pages[2] = PageConfidenceScores(parse_score=0.0, ocr_score=0.95)  # a broken layer read whole
    confidence.pages[3] = PageConfidenceScores(parse_score=0.9)  # no OCR text
    confidence.pages[4] = PageConfidenceScores(parse_score=0.0)  # a low parse score alone, no OCR text
    doc = _FakeDoc({1: "a", 2: "b", 3: "c", 4: ""})
    result = SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc, confidence=confidence)
    c, _ = _with(monkeypatch, result)
    out = c.convert(tmp_path / "a.pdf", "pdf")
    assert (out.ocr_pages, out.broken_pages) == ((1, 2), (2,)) and out.pages == ["a", "b", "c", ""]
    # no confidence report (OCR off, or another reader) and the office formats: none
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc))
    assert (c.convert(tmp_path / "a.pdf", "pdf").ocr_pages, c.convert(tmp_path / "a.pdf", "pdf").broken_pages) == (
        (), ())
    office = SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=_FakeDoc({}, whole="x"),
                             confidence=confidence)
    c, _ = _with(monkeypatch, office)
    assert (c.convert(tmp_path / "a.docx", "docx").ocr_pages, c.convert(tmp_path / "a.docx", "docx").broken_pages) == (
        (), ())


def test_ocr_text_from_the_layout_cells(monkeypatch, tmp_path):
    # review of phase 1: what Docling's region OCR read must be countable on its own (Simplified forms, short noise
    # such as a diagram's labels): the layout clusters keep their cells, OCR cells marked from_ocr
    from docling.datamodel.base_models import ConversionStatus

    def cell(text, from_ocr):
        return SimpleNamespace(text=text, from_ocr=from_ocr)

    shared = cell("員", True)
    picture = SimpleNamespace(cells=[cell("Rf", True), shared], children=[SimpleNamespace(cells=[shared], children=[])])
    text = SimpleNamespace(cells=[cell("Course rules", False)], children=[])
    pages = [SimpleNamespace(page_no=1, predictions=SimpleNamespace(layout=SimpleNamespace(clusters=[text, picture]))),
             SimpleNamespace(page_no=2, predictions=SimpleNamespace(layout=None)),
             SimpleNamespace(page_no=3, predictions=SimpleNamespace(layout=SimpleNamespace(clusters=[text])))]
    doc = _FakeDoc({1: "a", 2: "b", 3: "c"})
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc, pages=pages))
    assert c.convert(tmp_path / "a.pdf", "pdf").ocr_text == {1: ["Rf", "員"]}  # each cell once, native text out
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc))
    assert c.convert(tmp_path / "a.pdf", "pdf").ocr_text == {}  # a result that keeps no pages


def test_office_formats_export_once(monkeypatch, tmp_path):
    from docling.datamodel.base_models import ConversionStatus

    doc = _FakeDoc({}, whole="| 項目 | 金額 |")
    c, _ = _with(monkeypatch, SimpleNamespace(status=ConversionStatus.SUCCESS, errors=[], document=doc))
    assert c.convert(tmp_path / "a.docx", "docx").pages == ["| 項目 | 金額 |"]


def test_failure_keeps_no_text(monkeypatch, tmp_path):
    from docling.datamodel.base_models import ConversionStatus

    result = SimpleNamespace(status=ConversionStatus.FAILURE, errors=[_err("broken file")], document=_FakeDoc({1: "x"}))
    c, _ = _with(monkeypatch, result)
    assert c.convert(tmp_path / "a.pdf", "pdf") == DoclingOut("failed", "broken file", [], None, 0)


def test_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        DoclingConverter().convert(tmp_path / "a.doc", "doc")


def _docx_parts(numbering: bytes | None) -> dict[str, bytes]:
    parts = {"[Content_Types].xml": b"<Types/>", "word/document.xml": "<w:document>選課</w:document>".encode()}
    if numbering is not None:
        parts["word/numbering.xml"] = numbering
    parts["word/media/image1.png"] = bytes(range(256))
    return parts


def _numbering(*formats: str) -> bytes:
    levels = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="%1、"/>'
                     f"</w:lvl>" for i, fmt in enumerate(formats))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="http://schemas.openxml'
            f'formats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="0">{levels}</w:abstractNum>'
            "</w:numbering>").encode()


def test_fix_docx_numbering(tmp_path):
    src = zip_of(tmp_path / "src.docx", _docx_parts(_numbering("taiwaneseCountingThousand", "ideographLegalTraditional")))
    assert fix_docx_numbering(src, tmp_path / "dst.docx") == (1, 1)
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(tmp_path / "dst.docx") as b:
        assert a.namelist() == b.namelist()
        fixed = b.read("word/numbering.xml").decode()
        assert 'w:val="chineseCountingThousand"' in fixed and "taiwanese" not in fixed
        assert 'w:val="ideographLegalTraditional"' in fixed
        assert fixed == a.read("word/numbering.xml").decode().replace("taiwaneseCountingThousand",
                                                                       "chineseCountingThousand")
        for name in a.namelist():
            if name != "word/numbering.xml":
                assert a.read(name) == b.read(name)


def test_fix_docx_numbering_maps_each_taiwanese_format(tmp_path):
    src = zip_of(tmp_path / "src.docx", _docx_parts(_numbering("taiwaneseCounting", "taiwaneseDigital",
                                                               "taiwaneseCountingThousand", "decimal")))
    assert fix_docx_numbering(src, tmp_path / "dst.docx") == (3, 0)
    with zipfile.ZipFile(tmp_path / "dst.docx") as b:
        fixed = b.read("word/numbering.xml").decode()
    for fmt in ("chineseCounting", "ideographDigital", "chineseCountingThousand", "decimal"):
        assert f'w:numFmt w:val="{fmt}"' in fixed


def test_fix_docx_numbering_counts_every_format_docling_lacks(tmp_path):
    src = zip_of(tmp_path / "src.docx", _docx_parts(_numbering("japaneseLegal", "ideographLegalTraditional", "bullet",
                                                               "none", "ideographTraditional")))
    assert fix_docx_numbering(src, tmp_path / "dst.docx") == (0, 2)


def test_rendered_formats_are_doclings_own():
    from docling.backend.msword_backend import _VISIBLE_NUMBERING_FORMATS

    assert docling_convert.NUMFMT_RENDERED - {"bullet", "none"} == _VISIBLE_NUMBERING_FORMATS
    assert not set(docling_convert.NUMFMT_REMAP) & _VISIBLE_NUMBERING_FORMATS
    assert set(docling_convert.NUMFMT_REMAP.values()) <= _VISIBLE_NUMBERING_FORMATS


def test_fix_docx_numbering_without_numbering_copies(tmp_path):
    src = zip_of(tmp_path / "src.docx", _docx_parts(None))
    assert fix_docx_numbering(src, tmp_path / "dst.docx") == (0, 0)
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(tmp_path / "dst.docx") as b:
        assert [(n, a.read(n)) for n in a.namelist()] == [(n, b.read(n)) for n in b.namelist()]


def test_artifact_commits(tmp_path):
    for folder, commit in (("docling-project--docling-layout-heron", "a" * 40), ("docling-project--docling-models",
                                                                                 "b" * 40)):
        meta = tmp_path / folder / ".cache" / "huggingface" / "download"
        (meta / "sub").mkdir(parents=True)
        (meta / "config.json.metadata").write_text(f"{commit}\n{'e' * 40}\n1793602692.18\n")
        (meta / "sub" / "model.safetensors.metadata").write_text(f"{commit}\n{'f' * 64}\n1793602692.18\n")
    (tmp_path / "no-metadata").mkdir()
    (tmp_path / "a-file").write_text("x")
    assert artifact_commits(tmp_path) == {"docling-project--docling-layout-heron": "a" * 40,
                                          "docling-project--docling-models": "b" * 40, "no-metadata": ""}
    assert artifact_commits(tmp_path / "absent") == {}


def test_offline_flag_set_before_docling_loads():
    # a fresh interpreter, HF_HUB_OFFLINE absent from its environment: importing the module sets it, and Docling
    # (loaded when the options are built) finds it set when huggingface_hub reads it
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "DISABLE_SAFETENSORS_CONVERSION")}
    code = ("import nknu.totext.docling_convert as d, sys, os; d.pdf_options(device='cpu', artifacts=d.DOCLING_ARTIFACTS); "
            "import huggingface_hub.constants as c; "
            "print(c.HF_HUB_OFFLINE, 'docling' in sys.modules, os.environ['DISABLE_SAFETENSORS_CONVERSION'])")
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=REPO_ROOT, capture_output=True, text=True,
                         timeout=120, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["True", "True", "1"]


@pytest.mark.models
def test_real_pdf_and_docx(tmp_path):
    if not (DOCLING_ARTIFACTS / "docling-project--docling-layout-heron").is_dir():
        pytest.skip(f"no Docling layout model under {DOCLING_ARTIFACTS}")
    out = DoclingConverter(device="cpu").convert(tiny_pdf(tmp_path / "t.pdf", ["Hello course rules " * 3, None]), "pdf")
    assert out.status == "ok" and len(out.pages) == 2 and "Hello" in out.pages[0]
    assert out.doc_json is not None and out.pictures == 0
    assert "| 項目 | 金額 |" in DoclingConverter(device="cpu").convert(docx_with_table(tmp_path / "t.docx"), "docx").pages[0]


NUMBERING_LOST = ("Docling's region OCR drops or moves the leading 一、 (phase-1 review, 11-02: 4 of 4 synthetic "
                  "marks); T6 splits sections on these marks; the main session picks the remedy")


@pytest.mark.models
def test_real_docling_region_ocr(tmp_path):
    # a page with one native text line and an embedded image of 「一、選課期間」: the OCR-on converter keeps the
    # native line and reads the image, from the wheel's RapidOCR models (no <artifacts>/RapidOcr/ folder needed).
    # Changed after the phase-1 review: this test asserted 「選課期間」 alone and so hid that the numbering is lost
    # (Docling gave 「## 選課期間」); the loss now shows as an expected failure until a remedy lands (a post-fix of
    # OCR'd numbering, the medium PP-OCRv6 models, or accepting it)
    if not (DOCLING_ARTIFACTS / "docling-project--docling-layout-heron").is_dir():
        pytest.skip(f"no Docling layout model under {DOCLING_ARTIFACTS}")
    font = cjk_font()
    if font is None:
        pytest.skip("no CJK font on this machine")
    line = "Course rules for the autumn term of the school"
    path = mixed_pdf(tmp_path / "m.pdf", line, text_image("一、選課期間", font))
    out = DoclingConverter(device="cpu", ocr=True).convert(path, "pdf")
    assert out.status == "ok" and len(out.pages) == 1 and (out.ocr_pages, out.broken_pages) == ((1,), ())
    assert line in out.pages[0] and "選課期間" in out.pages[0]
    # the lines Docling's OCR read, apart from the native one (the report counts their noise from these)
    assert any("選課期間" in text for text in out.ocr_text[1]) and not any(line in text for text in out.ocr_text[1])
    if "一、選課期間" not in out.pages[0]:
        pytest.xfail(NUMBERING_LOST)
