"""What a response is by its first bytes (round-1 design §7.3; WHATWG MIME Sniffing, OOXML ECMA-376, ODF ISO/IEC 26300),
whatever its Content-Type says, and the file name a Content-Disposition gives (filename*, else a bare name in UTF-8 or
Big5)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from nknu.archive import sniff


def _zip(names: dict[str, bytes], first: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        order = ([first] if first else []) + [n for n in names if n != first]
        for n in order:
            z.writestr(n, names[n])
    return buf.getvalue()


DOCX = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"})
XLSX = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})
PPTX = _zip({"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>"})
ODT = _zip({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b"<x/>"}, first="mimetype")
ODS = _zip({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet", "content.xml": b"<x/>"}, first="mimetype")
PLAIN_ZIP = _zip({"a.txt": b"hello"})


@pytest.mark.parametrize("body, ctype, want", [
    (b"%PDF-1.7\n...", "application/octet-stream", "pdf"),
    (b"\xef\xbb\xbf  %PDF-1.4", "", "text"),  # a PDF's signature is at the very start (WHATWG: no leading bytes)
    (DOCX, "application/octet-stream", "ooxml:docx"),
    (XLSX, "application/zip", "ooxml:xlsx"),
    (PPTX, "", "ooxml:pptx"),
    (ODT, "", "odf:odt"),
    (ODS, "", "odf:ods"),
    (PLAIN_ZIP, "application/zip", "zip"),
    (bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 100, "application/msword", "ole"),
    (b"{\\rtf1\\ansi ...}", "text/plain", "rtf"),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "image/jpeg", "png"),
    (b"\xff\xd8\xff\xe0" + b"\0" * 20, "", "jpeg"),
    (b"GIF89a" + b"\0" * 10, "", "gif"),
    (b"RIFF\x10\0\0\0WEBPVP8 ", "", "webp"),
    (b"II*\x00" + b"\0" * 10, "", "tiff"),
    (b"MM\x00*" + b"\0" * 10, "", "tiff"),
    (b"\xef\xbb\xbf\n  <!DOCTYPE html><html><body>x</body></html>", "application/octet-stream", "html"),
    (b"<html lang='zh'><body>x</body></html>", "text/plain", "html"),
    (b"  <HEAD><title>x</title></HEAD>", "", "html"),
    (b"<script>location.href='a.html'</script>", "", "html"),  # WHATWG's list of tags that start a web page
    (b"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>", "text/xml", "xml"),
    (b"<?xml version='1.0'?>\n<html xmlns='http://www.w3.org/1999/xhtml'><body/></html>", "", "html"),
    (json.dumps({"success": True, "content": "<p>x</p>"}).encode(), "text/html", "json"),
    (b"\xef\xbb\xbf  [1, 2, 3]", "", "json"),
    (b"{not json at all", "", "text"),  # looks like JSON, does not parse: plain text
    ("一般的文字檔，學年度行事曆".encode(), "text/plain", "text"),
    ("一般的文字檔".encode("cp950"), "", "text"),
    (b"\x00\x01\x02binary\xfe\xff" * 10, "", "other"),
    (b"", "text/html", "other"),
    (b"just words, no tags", "text/html", "html"),  # the server says a web page and nothing says otherwise
])
def test_sniff(body, ctype, want):
    assert sniff.sniff(body, ctype) == want


def test_image_and_file_kinds():
    assert all(sniff.kind_of(s) == "image" for s in ("png", "jpeg", "gif", "webp"))
    assert all(sniff.kind_of(s) == "file" for s in ("pdf", "ooxml:docx", "tiff", "json", "xml", "text", "other"))
    assert sniff.kind_of("html") == "page"


@pytest.mark.parametrize("raw, want", [
    ([(b"Content-Disposition", b"attachment; filename*=UTF-8''%E8%A1%8C%E4%BA%8B%E6%9B%86.pdf")], "行事曆.pdf"),
    ([(b"Content-Disposition", b'attachment; filename="report.pdf"; filename*=utf-8\'\'%E5%A0%B1%E5%91%8A.pdf')],
     "報告.pdf"),
    ([(b"content-disposition", 'attachment; filename="招生簡章.pdf"'.encode())], "招生簡章.pdf"),
    ([(b"Content-Disposition", 'attachment; filename=招生簡章.docx'.encode("cp950"))], "招生簡章.docx"),
    ([(b"Content-Disposition", b"inline")], None),
    ([(b"Content-Type", b"application/pdf")], None),
    ([], None),
])
def test_filename(raw, want):
    assert sniff.filename(raw, {}) == want


def test_filename_from_decoded_headers_when_no_raw_ones():
    assert sniff.filename((), {"content-disposition": 'attachment; filename="a b.pdf"'}) == "a b.pdf"
