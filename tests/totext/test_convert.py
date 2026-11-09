"""convert_file per route, and storing a result (plan 2026-11-02 T3b; T4 for the OCR parts), with fakes: no Docling
model, no LibreOffice, no OCR model. The OCR routes themselves are in test_ocr."""

from __future__ import annotations

import gzip
import hashlib
import json
import zipfile

import pytest

from nknu.totext import libreoffice, ocr
from nknu.totext.cache import PIPELINE_VERSION, Cache
from nknu.totext.convert import Converted, Tools, convert_and_save, convert_file, result_of, store, text_chars
from nknu.totext.docling_convert import DOCLING_ARTIFACTS, DoclingConverter
from nknu.totext.libreoffice import ConvertError
from nknu.totext.route import Route, route_for
from tests.totext.fixtures import FakeEngine, docx_with_table, refuse_real_models, tiny_pdf, zip_of
from tests.totext.test_run import PDF_TEXT, FakeDocling, fake_tools

SHA = hashlib.sha256(b"x").hexdigest()
NOTICE = "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=1&gi=2"


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, request):
    refuse_real_models(monkeypatch, request)


def _pdf_bytes(tmp_path, pages=(PDF_TEXT, PDF_TEXT)) -> bytes:
    return tiny_pdf(tmp_path / "made.pdf", list(pages)).read_bytes()


def test_bom_pdf_is_converted_without_its_prefix(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"\xef\xbb\xbf" + _pdf_bytes(tmp_path))
    route = route_for("附件", path)
    fake = FakeDocling()
    c = convert_file(path, route, fake_tools(fake), tmp_path / "w")
    [(given, kind, head)] = fake.calls
    assert route.pdf_offset == 3 and kind == "pdf" and head.startswith(b"%PDF") and given.parent == tmp_path / "w"
    assert c.fixes == ["bom_prefix:3"] and (c.route, c.status, c.reason) == ("pdf", "ok", "")


def test_other_bytes_before_the_pdf_are_named_as_such(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"Notice: debug line\n" + _pdf_bytes(tmp_path))
    c = convert_file(path, route_for("附件", path), fake_tools(FakeDocling()), tmp_path / "w")
    assert c.fixes == ["pdf_prefix:19"]


def test_pdf_pages_are_joined_with_their_starts(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    fake = FakeDocling(pages=["page one", "", "page three"])
    c = convert_file(path, Route("pdf", "pdf"), fake_tools(fake), tmp_path / "w")
    assert c.text == "page one\n\n\n\npage three" and c.page_starts == [0, 10, 12] and c.pages == 3
    assert c.text[c.page_starts[2]:] == "page three" and c.doc_json == {"k": 1} and c.fixes == []
    assert fake.calls[0][0] == path  # a .pdf goes to Docling as it is


def test_a_pdf_under_another_extension_goes_in_as_pdf(tmp_path):
    path = tmp_path / "a.ai"
    path.write_bytes(_pdf_bytes(tmp_path))
    fake = FakeDocling()
    convert_file(path, route_for("附件", path), fake_tools(fake), tmp_path / "w")
    [(given, _kind, head)] = fake.calls
    assert given.suffix == ".pdf" and head.startswith(b"%PDF")


def test_empty_text_after_success_is_empty(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    c = convert_file(path, Route("pdf", "pdf"), fake_tools(FakeDocling(pages=["", " \n "])), tmp_path / "w")
    assert c.status == "empty" and c.reason and c.pages == 2


def test_docling_partial_and_failure_keep_its_reason(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    partial = FakeDocling(status="partial", reason="Docling 超时（900 秒），只转出前面几页", pages=["p1"])
    c = convert_file(path, Route("pdf", "pdf"), fake_tools(partial), tmp_path / "w")
    # T4 changed this deliberately: pages follow the PDF's page count (the larger of Docling's and the analysis'),
    # so the page Docling did not return (a timeout) is '' and page_starts keeps page n at page_starts[n - 1]
    assert (c.status, c.reason, c.text, c.pages, c.page_starts) == (
        "partial", "Docling 超时（900 秒），只转出前面几页", "p1\n\n", 2, [0, 4])
    broken = FakeDocling(status="failed", reason="docling-parse could not load document", pages=[])
    c = convert_file(path, Route("pdf", "pdf"), fake_tools(broken), tmp_path / "w2")
    assert (c.status, c.reason, c.text, c.doc_json) == ("failed", "docling-parse could not load document", "", None)


def test_partial_without_text_is_failed_on_every_route(tmp_path):
    # review of phase 1: a partial that gave no text is a failure (T7a's fallback looks at failed files only)
    docx = docx_with_table(tmp_path / "a.docx")
    partial = FakeDocling(status="partial", reason="Docling 只转出一部分", pages=[" \n"])
    c = convert_file(docx, Route("office", "docx"), fake_tools(partial), tmp_path / "w")
    assert (c.status, c.reason) == ("failed", "Docling 只转出一部分")
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    timeout = FakeDocling(status="partial", reason="Docling 超时（900 秒），只转出前面几页", pages=[])
    c = convert_file(path, Route("pdf", "pdf"), fake_tools(timeout), tmp_path / "w2")
    assert (c.status, c.reason, c.pages) == ("failed", "Docling 超时（900 秒），只转出前面几页", 2)


def _numbered_docx(path, *formats: str):
    levels = "".join(f'<w:lvl w:ilvl="{i}"><w:numFmt w:val="{fmt}"/></w:lvl>' for i, fmt in enumerate(formats))
    numbering = (f'<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 f"<w:abstractNum>{levels}</w:abstractNum></w:numbering>").encode()
    return zip_of(path, {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>",
                         "word/numbering.xml": numbering})


def test_docx_numbering_is_fixed_before_docling(tmp_path):
    path = _numbered_docx(tmp_path / "a.docx", "taiwaneseCountingThousand", "ideographLegalTraditional")
    fake = FakeDocling(pages=["一、選課"])
    c = convert_file(path, Route("office", "docx"), fake_tools(fake), tmp_path / "w")
    [(given, kind, _head)] = fake.calls
    assert kind == "docx" and given == tmp_path / "w" / "input.docx"
    with zipfile.ZipFile(given) as zf:
        assert b"chineseCountingThousand" in zf.read("word/numbering.xml")
    assert c.fixes == ["docx_numbering:1", "docx_numbering_unsupported:1"] and c.text == "一、選課"
    assert (c.route, c.status, c.pages, c.page_starts) == ("office", "ok", 0, [])


def test_docx_the_fix_cannot_read_goes_to_docling_as_it_is(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK\x03\x04 broken")
    fake = FakeDocling(status="failed", reason="Package not found", pages=[])
    c = convert_file(path, Route("office", "docx"), fake_tools(fake), tmp_path / "w")
    assert fake.calls[0][0] == path and c.fixes == ["docx_numbering_skipped:BadZipFile"]
    assert (c.status, c.reason) == ("failed", "Package not found")


def test_xlsx_goes_to_docling_without_a_fix(tmp_path):
    path = zip_of(tmp_path / "a.xlsx", {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})
    fake = FakeDocling(pages=["| 項目 | 金額 |"])
    c = convert_file(path, Route("office", "xlsx"), fake_tools(fake), tmp_path / "w")
    assert fake.calls[0][:2] == (path, "xlsx") and c.fixes == [] and c.text == "| 項目 | 金額 |"


def _ole() -> bytes:
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 56


def test_office_old_goes_through_libreoffice(tmp_path, monkeypatch):
    seen = []

    def fake_to_modern(src, kind, workdir, **_):
        seen.append((src, kind))
        (workdir / "out").mkdir(parents=True)
        return docx_with_table(workdir / "out" / "input.docx")

    monkeypatch.setattr(libreoffice, "to_modern", fake_to_modern)
    path = tmp_path / "a.doc"
    path.write_bytes(_ole())
    fake = FakeDocling(pages=["選課注意事項"])
    c = convert_file(path, Route("office_old", "doc"), fake_tools(fake), tmp_path / "w")
    assert seen == [(path, "doc")] and fake.kinds() == ["docx"]
    assert fake.calls[0][0] == tmp_path / "w" / "input.docx"  # the numbering fix ran on LibreOffice's docx
    assert (c.route, c.status, c.text) == ("office_old", "ok", "選課注意事項")


def test_old_spreadsheet_becomes_xlsx(tmp_path, monkeypatch):
    def fake_to_modern(src, kind, workdir, **_):
        (workdir / "out").mkdir(parents=True)
        return zip_of(workdir / "out" / "input.xlsx", {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b""})

    monkeypatch.setattr(libreoffice, "to_modern", fake_to_modern)
    path = tmp_path / "a.xls"
    path.write_bytes(_ole())
    fake = FakeDocling(pages=["| a |"])
    convert_file(path, Route("office_old", "xls"), fake_tools(fake), tmp_path / "w")
    assert fake.kinds() == ["xlsx"]


def test_libreoffice_failure_is_failed(tmp_path, monkeypatch):
    def fake_to_modern(src, kind, workdir, **_):
        raise ConvertError("LibreOffice 超时（120 秒）")

    monkeypatch.setattr(libreoffice, "to_modern", fake_to_modern)
    path = tmp_path / "a.doc"
    path.write_bytes(_ole())
    fake = FakeDocling()
    c = convert_file(path, Route("office_old", "doc"), fake_tools(fake), tmp_path / "w")
    assert (c.route, c.status, c.reason, c.text) == ("office_old", "failed", "LibreOffice 超时（120 秒）", "")
    assert fake.calls == []


def test_page_route(tmp_path):
    path = tmp_path / "p.html"
    path.write_bytes('﻿<div id="lbl_Title">停修公告</div><div id="lbl_Contents"><p>停修申請</p>'
                     '<img src="a.jpg"></div>'.encode())
    c = convert_file(path, Route("page", "html", url=NOTICE), Tools(), tmp_path / "w")  # T2: read by its URL
    assert (c.route, c.status, c.reason) == ("page", "ok", "")
    assert c.text == "停修公告\n停修申請\n[[img:a.jpg]]"
    assert c.page == {"title": "停修公告", "album": "", "images": ["a.jpg"], "links": []}  # links: item 22, 11-09
    assert c.full == c.text  # the whole body's reading: no chrome here to keep


def test_page_that_is_not_utf8_is_failed(tmp_path):
    path = tmp_path / "p.html"
    path.write_bytes(b"\xff\xfe\xfa")
    c = convert_file(path, Route("page", "html", url=NOTICE), Tools(), tmp_path / "w")
    assert c.status == "failed" and "UTF-8" in c.reason


def test_page_without_text_is_empty(tmp_path):
    path = tmp_path / "p.html"
    path.write_bytes(b'<div id="lbl_Contents"> </div>')
    c = convert_file(path, Route("page", "html", url=NOTICE), Tools(), tmp_path / "w")
    assert (c.status, c.text) == ("empty", "") and c.reason


@pytest.mark.parametrize("route,reason", [  # T4 wired the image route (test_ocr), T5 the archive route (test_archives)
    (Route("data", ""), "结构化资料：行事历由建库直接读"),
    (Route("unsupported", ""), "认不出的档案格式（.bin）")])
def test_skipped_routes(tmp_path, route, reason):
    path = tmp_path / "f.bin"
    path.write_bytes(b"\x00\x01")
    fake, engine = FakeDocling(), FakeEngine()
    c = convert_file(path, route, fake_tools(fake, ocr=engine), tmp_path / "w")
    assert (c.route, c.status, c.reason, c.text) == (route.name, "skipped", reason, "") and fake.calls == []
    assert engine.read_images == []


def test_unsupported_names_the_manifest_format(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"\x00")
    assert convert_file(path, Route("unsupported", ""), Tools(), tmp_path / "w", fmt="ole").reason == \
        "认不出的档案格式（ole）"
    assert convert_file(path, Route("unsupported", ""), Tools(), tmp_path / "w").reason == "认不出的档案格式（没有扩展名）"


def test_unknown_route_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        convert_file(tmp_path / "f", Route("video", ""), Tools(), tmp_path / "w")


def test_beat_after_each_docling_call(tmp_path):
    beats = []
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    tools = fake_tools(beat=lambda: beats.append(1))
    convert_file(path, Route("pdf", "pdf"), tools, tmp_path / "w")
    convert_file(docx_with_table(tmp_path / "a.docx"), Route("office", "docx"), tools, tmp_path / "w2")
    assert beats == [1, 1]


def test_tools_build_docling_on_first_use_and_describe_without_loading():
    tools = Tools()
    assert tools._docling is None and tools._docling_ocr is None and tools._ocr is None
    described = tools.describe()
    assert tools._docling is None and described is tools.describe()
    assert described["pipeline"] == PIPELINE_VERSION and described["docling"] == "2.145.0"
    assert {"rapidocr", "onnxruntime", "pypdfium2", "docling_models", "libreoffice", "soffice"} <= set(described)
    converter = tools.docling
    assert isinstance(converter, DoclingConverter) and tools.docling is converter
    assert converter.artifacts == DOCLING_ARTIFACTS and converter._converter is None  # no model loaded yet
    assert converter.ocr is False
    # T4: the OCR-on converter and the OCR engine are built the same lazy way, loading nothing yet
    with_ocr = tools.docling_ocr
    assert isinstance(with_ocr, DoclingConverter) and with_ocr.ocr is True and tools.docling_ocr is with_ocr
    assert with_ocr is not converter and with_ocr._converter is None
    engine = tools.ocr
    assert isinstance(engine, ocr.RapidEngine) and tools.ocr is engine and engine._reader is None
    fake, fake_ocr, fake_engine = FakeDocling(), FakeDocling(), FakeEngine()
    given = Tools(docling=fake, docling_ocr=fake_ocr, ocr=fake_engine)
    assert (given.docling, given.docling_ocr, given.ocr) == (fake, fake_ocr, fake_engine)
    tools.beat()  # no watchdog: a no-op


def test_text_chars_skip_image_markers():
    assert text_chars("停修\n[[img:a.jpg]]\n 申請 ") == 4 and text_chars("[[img:a.jpg]]") == 0


def test_result_of_and_store(tmp_path):
    ocr = [{"page": 1, "text": "一、選課", "score": 0.95, "box": [0, 0, 1, 1]},
           {"page": 1, "text": "期間", "score": 0.5, "box": [0, 1, 1, 2]}]
    conv = Converted("pdf", "ok", "", "p1\n\np2", pages=2, page_starts=[0, 4], ocr=ocr, fixes=["bom_prefix:3"],
                     doc_json={"name": "文件"}, pictures=1)
    route = Route("pdf", "pdf", pdf_offset=3)
    result, text, extras = result_of(conv, route, seconds=1.25, tools={"pipeline": "x"}, key="2",
                                     file="files/a.pdf")
    assert text == "p1\n\np2" and not {"text", "doc_json", "ocr"} & set(result)
    assert (result["version"], result["route"], result["kind"], result["pdf_offset"], result["page_kind"]) == (
        PIPELINE_VERSION, "pdf", "pdf", 3, "")
    assert (result["chars"], result["pages"], result["page_starts"], result["pictures"]) == (4, 2, [0, 4], 1)
    assert (result["ocr_lines"], result["ocr_mean_score"], result["ocr_low_lines"]) == (2, 0.725, 1)
    assert (result["seconds"], result["tools"], result["first_key"], result["first_file"]) == (
        1.25, {"pipeline": "x"}, "2", "files/a.pdf")
    assert json.loads(gzip.decompress(extras["docling.json.gz"])) == {"name": "文件"}
    assert json.loads(extras["ocr.json"]) == ocr
    cache = Cache(tmp_path / "cache")
    stored = store(cache, SHA, conv, route, seconds=1.25, tools={"pipeline": "x"}, key="2", file="files/a.pdf")
    assert cache.done(SHA) == stored == result and cache.text(SHA) == text
    assert sorted(p.name for p in cache.dir(SHA).iterdir()) == ["docling.json.gz", "ocr.json", "result.json",
                                                               "text.md"]
    plain = result_of(Converted("data", "skipped", "r", ""), Route("data", ""), seconds=0, tools={})
    assert plain[2] == {} and plain[0]["ocr_mean_score"] is None and plain[0]["chars"] == 0


def test_convert_and_save_turns_an_exception_into_failed(tmp_path):
    cache = Cache(tmp_path / "cache")
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    result = convert_and_save(cache, SHA, path, Route("pdf", "pdf"),
                              fake_tools(FakeDocling(raises={"pdf": RuntimeError("x" * 400)})),
                              tmp_path / "w", key="2", file="files/a.pdf")
    assert result["status"] == "failed" and result["reason"].startswith("RuntimeError: x")
    assert len(result["reason"]) == 300 and cache.done(SHA) == result and not cache.crashed(SHA)
    assert not (tmp_path / "w").exists()


def test_convert_and_save_interrupted_leaves_no_marker(tmp_path):
    cache = Cache(tmp_path / "cache")
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_bytes(tmp_path))
    with pytest.raises(KeyboardInterrupt):
        convert_and_save(cache, SHA, path, Route("pdf", "pdf"),
                         fake_tools(FakeDocling(raises={"pdf": KeyboardInterrupt()})), tmp_path / "w")
    assert cache.done(SHA) is None and not cache.crashed(SHA) and not (tmp_path / "w").exists()
