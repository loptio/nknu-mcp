"""route_for: which converter a file goes to, by its row type, then by its magic bytes (plan 2026-11-02 T3a)."""

from __future__ import annotations

import zipfile

import pytest

from nknu.totext.route import ROUTES, Route, route_for
from tests.totext.fixtures import zip_of

OLE = bytes.fromhex("D0CF11E0A1B11AE1")


@pytest.mark.parametrize("head,name,row_type,want", [
    (b"%PDF-1.4\n", "a.pdf", "附件", ("pdf", "pdf", 0)),
    (b"\xef\xbb\xbf%PDF-1.4\n", "b.pdf", "附件", ("pdf", "pdf", 3)),
    (OLE + b"\0" * 8, "c.doc", "附件", ("office_old", "doc", 0)), (OLE, "c.xls", "附件", ("office_old", "xls", 0)),
    (OLE, "c.bin", "附件", ("unsupported", "", 0)), (b"{\\rtf1\\ansi", "d.rtf", "附件", ("office_old", "rtf", 0)),
    (b"Rar!\x1a\x07\x00", "e.rar", "附件", ("archive", "rar", 0)), (b"\xff\xd8\xff\xe0", "f.jpg", "图片", ("image", "jpeg", 0)),
    (b"\x89PNG\r\n\x1a\n", "g.png", "图片", ("image", "png", 0)), (b"GIF89a", "h.gif", "图片", ("image", "gif", 0)),
    (b"\xef\xbb\xbf<html>", "i.html", "网页", ("page", "html", 0)), (b"{}", "j.json", "资料", ("data", "", 0)),
    (b"\x00\x01", "k.bin", "附件", ("unsupported", "", 0))])
def test_route_by_magic(tmp_path, head, name, row_type, want):
    path = tmp_path / name
    path.write_bytes(head)
    r = route_for(row_type, path)
    assert (r.name, r.kind, r.pdf_offset) == want
    assert r.name in ROUTES and r.page_kind == ""


def test_zip_kinds(tmp_path):
    ct = {"[Content_Types].xml": b"<Types/>"}
    for folder, member, kind in (("word", "document.xml", "docx"), ("xl", "workbook.xml", "xlsx"),
                                 ("ppt", "presentation.xml", "pptx")):
        path = zip_of(tmp_path / f"x.{kind}", ct | {f"{folder}/{member}": b"<x/>"})
        assert route_for("附件", path) == Route("office", kind)
    for sub, kind in (("text", "odt"), ("spreadsheet", "ods"), ("presentation", "odp")):
        path = zip_of(tmp_path / f"y.{kind}", {"mimetype": f"application/vnd.oasis.opendocument.{sub}".encode(),
                                               "content.xml": b"<x/>"})
        assert route_for("附件", path) == Route("office_old", kind)
    assert route_for("附件", zip_of(tmp_path / "z.zip", {"a.pdf": b"%PDF-1.4\n"})) == Route("archive", "zip")


def test_a_container_wins_over_a_pdf_stored_inside(tmp_path):
    # a RAR or ZIP that stores a PDF uncompressed shows "%PDF" in its first kilobyte: still an archive
    rar = tmp_path / "s.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00" + b"\x01" * 150 + b"%PDF-1.4\n")
    assert route_for("附件", rar) == Route("archive", "rar")
    stored = tmp_path / "s.zip"
    with zipfile.ZipFile(stored, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.pdf", b"%PDF-1.4\n")
    assert route_for("附件", stored) == Route("archive", "zip")


def test_pdf_after_other_bytes(tmp_path):
    # a server's debug line written before the PDF: the offset is where "%PDF" starts
    head = b"\xef\xbb\xbfdebug line<br>\n"
    path = tmp_path / "p.pdf"
    path.write_bytes(head + b"%PDF-1.4\n" + b"x" * 2000)
    assert route_for("附件", path) == Route("pdf", "pdf", pdf_offset=len(head))
    late = tmp_path / "late.pdf"
    late.write_bytes(b"x" * 1100 + b"%PDF-1.4\n")
    assert route_for("附件", late) == Route("unsupported", "")


def test_unreadable_zip_falls_back_to_its_extension(tmp_path):
    for name, want in (("bad.docx", Route("office", "docx")), ("bad.odt", Route("office_old", "odt")),
                       ("bad.zip", Route("archive", "zip"))):
        path = tmp_path / name
        path.write_bytes(b"PK\x03\x04" + b"\0" * 40)
        assert route_for("附件", path) == want


def _damaged_mimetype(path) -> bytes:
    """An OpenDocument zip whose deflated 'mimetype' entry has a broken deflate stream (zlib raises on reading it)."""
    import zlib

    mimetype = b"application/vnd.oasis.opendocument.text" * 20
    data = bytearray(zip_of(path, {"mimetype": mimetype, "content.xml": b"<x/>"}).read_bytes())
    deflate = zlib.compressobj(-1, zlib.DEFLATED, -15)
    at = data.index((deflate.compress(mimetype) + deflate.flush())[:6])
    data[at:at + 6] = b"\xff" * 6
    return bytes(data)


def test_a_zip_the_reader_chokes_on_goes_by_its_extension(tmp_path, monkeypatch):
    # review of phase 1: zlib.error (and EOFError) from a damaged entry escaped route_for and stopped the whole run in
    # select() and the report in account(); any error reading the zip routes by extension, as a BadZipFile does
    bad = _damaged_mimetype(tmp_path / "made.odt")
    for name, want in (("bad.odt", Route("office_old", "odt")), ("bad.docx", Route("office", "docx")),
                       ("bad.zip", Route("archive", "zip"))):
        path = tmp_path / name
        path.write_bytes(bad)
        assert route_for("附件", path) == want
    truncated = zip_of(tmp_path / "t.odt", {"mimetype": b"application/vnd.oasis.opendocument.text"})

    def ends_early(*_args, **_kwargs):
        raise EOFError("Compressed file ended before the end-of-stream marker was reached")

    monkeypatch.setattr(zipfile.ZipFile, "open", ends_early)
    assert route_for("附件", truncated) == Route("office_old", "odt")


def test_other_packages_are_unsupported(tmp_path):
    # a ZIP with a mimetype entry that is no OpenDocument text, sheet or slides (a drawing, an e-book) is no archive
    odg = zip_of(tmp_path / "d.odg", {"mimetype": b"application/vnd.oasis.opendocument.graphics", "content.xml": b"<x/>"})
    assert route_for("附件", odg) == Route("unsupported", "")
    assert route_for("附件", zip_of(tmp_path / "v.vsdx", {"[Content_Types].xml": b"<Types/>", "visio/a.xml": b"<x/>"})) \
        == Route("unsupported", "")


def test_row_type_comes_first(tmp_path):
    path = tmp_path / "p.html"
    path.write_bytes(b"%PDF-1.4\n")
    assert route_for("网页", path) == Route("page", "html")
    assert route_for("资料", path) == Route("data", "")
