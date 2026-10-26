"""What a response is by its own first bytes (round-1 design §7.3), not by its Content-Type: SSO's ~1,240 download
programs have no file extension, and the 10-24 archive holds 25 'attachments' that came back as web pages.

sniff() follows the WHATWG MIME Sniffing Standard's signatures (§7.1 binary, §7.2 web page tags) for what a server
sends: PDF, the two ZIP containers (OOXML, ECMA-376: [Content_Types].xml; ODF, ISO/IEC 26300: a first entry named
mimetype), the old Office compound file (OLE), RTF, PNG, JPEG, GIF, WebP, TIFF; then a web page (a WHATWG tag after the
byte order mark and white space), XML, JSON (the whole body parses), plain text, else other. A body nothing marks, of a
Content-Type that says text/html, is a web page: the server says so and nothing says otherwise. Whether a document
asks for a password is the sorting stage's (design §7.3: the file is kept whole).

filename(): Content-Disposition's filename* (RFC 6266, RFC 5987), else its filename, as UTF-8 or else Big5 (cp950):
school servers send bare Big5 names."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from urllib.parse import unquote_to_bytes

HEAD = 4096
IMAGES = ("png", "jpeg", "gif", "webp")
_BOM = b"\xef\xbb\xbf"
_WS = b" \t\n\r\x0c"
# WHATWG MIME Sniffing §7.2 "the HTML signatures": the tag, then a space or ">" (tag-terminating byte)
_HTML_TAGS = (b"<!doctype html", b"<html", b"<head", b"<script", b"<iframe", b"<h1", b"<div", b"<font", b"<table",
              b"<a", b"<style", b"<title", b"<b", b"<body", b"<br", b"<p")
_TERMINATORS = b" >\t\n\r\x0c"
_OOXML = (("word/", "ooxml:docx"), ("xl/", "ooxml:xlsx"), ("ppt/", "ooxml:pptx"))
_ODF = {"application/vnd.oasis.opendocument.text": "odf:odt", "application/vnd.oasis.opendocument.spreadsheet": "odf:ods",
        "application/vnd.oasis.opendocument.presentation": "odf:odp",
        "application/vnd.oasis.opendocument.graphics": "odf:odg"}
_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1a\x1c-\x1f]")


def sniff(body: bytes, ctype: str = "") -> str:
    """html/pdf/ooxml:docx|xlsx|pptx/odf:odt|ods|odp|odg/zip/ole/rtf/png/jpeg/gif/webp/tiff/xml/json/text/other."""
    head = body[:HEAD]
    if not head:
        return "other"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(body)
    if head.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "ole"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    text = head.removeprefix(_BOM).lstrip(_WS)
    low = text[:64].lower()
    if any(low.startswith(tag) and len(low) > len(tag) and low[len(tag)] in _TERMINATORS for tag in _HTML_TAGS) \
            or low.startswith(b"<!--"):
        return "html"
    if low.startswith(b"<?xml"):
        return "html" if re.search(rb"(?i)<html[\s>]|xhtml", head) else "xml"
    if text[:1] in (b"{", b"["):
        try:
            json.loads(_decode(body.removeprefix(_BOM)))
            return "json"
        except (ValueError, UnicodeError):
            pass
    if "html" in (ctype or "").lower() and not _CONTROL.search(head):
        return "html"
    if not _CONTROL.search(head):
        try:
            _decode(head)
            return "text"
        except UnicodeError:
            pass
    return "other"


# what sniff() calls a file by its own first bytes (design §23 A1): a document, an archive, a picture. html, xml, json,
# text and other are not (a page, an error message, a script's answer, nothing); audio and video have no signature here
# (round 1 downloads neither, record 一·1)
FILE_FORMATS = frozenset({"pdf", "ooxml", "zip", "ole", "rtf", "png", "jpeg", "gif", "webp", "tiff"})


def is_file_format(sniffed: str | None) -> bool:
    """Whether sniff() said a file format: FILE_FORMATS, or one of its ooxml:* and odf:* kinds."""
    return bool(sniffed) and (sniffed in FILE_FORMATS or sniffed.startswith(("ooxml:", "odf:")))


# the file name extensions that say a format sniff() knows by its first bytes (design §23.9 A1 ④, 【Claude 的执行办法】):
# a name ending so whose answer sniffs as none of FILE_FORMATS is no such file. Others (.txt, .csv, .htm, .7z, .rar …)
# say nothing sniff can check. A .doc that is a docx or RTF, an .xlsx that is an old OLE file: files all the same (the
# 10-24 archive's 830 named files, through archive-rehearsal-4: no name and sniff disagreed but those)
NAMED_FORMATS = frozenset({"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "odg", "zip", "rtf",
                           "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff"})
PDF_LEEWAY = 1024  # a PDF reader finds %PDF- within its first 1,024 bytes (a line break or two a server put first)


def name_ext(name: str | None) -> str:
    """A file name's extension, lowercase ('' for none)."""
    base = (name or "").strip().rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[1].strip().lower() if "." in base else ""


def not_file(body: bytes, sniffed: str | None, name: str | None) -> str | None:
    """Why what a download button brought back is no file (design §23 A1 ①②, §23.9 A1 ④, 【Claude 的执行办法】):
    empty_body (nothing at all), no_name:<sniff> (no Content-Disposition file name, and its first bytes are no file
    format: a page, a line of text, a JSON answer), name_mismatch:<extension>/<sniff> (a name whose extension says a
    format sniff knows, NAMED_FORMATS, on an answer that is none: 計畫.pdf holding 「檔案不存在」 or an error page); None
    when it is a file. The school's own words (③) are profiles.file_says()'."""
    if not body:
        return "empty_body"
    if not name and not is_file_format(sniffed):
        return f"no_name:{sniffed or 'other'}"
    ext = name_ext(name)
    if ext in NAMED_FORMATS and not is_file_format(sniffed) \
            and not (ext == "pdf" and b"%PDF-" in body[:PDF_LEEWAY]):
        return f"name_mismatch:{ext}/{sniffed or 'other'}"
    return None


def kind_of(sniffed: str | None) -> str:
    """The url.kind a fetched answer is, by what it is: a web page, an image, or else a file (design §10.2: what is not
    HTML is never a page)."""
    if sniffed == "html":
        return "page"
    return "image" if sniffed in IMAGES else "file"


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp950")


def _zip_kind(body: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if infos and infos[0].filename == "mimetype":
                kind = _ODF.get(z.read("mimetype").decode("ascii", "replace").strip())
                if kind:
                    return kind
            if "[Content_Types].xml" in names:
                for prefix, kind in _OOXML:
                    if any(n.startswith(prefix) for n in names):
                        return kind
                return "ooxml"
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, EOFError, NotImplementedError):
        pass
    return "zip"


_PARAM = re.compile(rb"""(?i)(?:^|;)\s*(filename\*?)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)""")


def filename(raw_headers: Sequence[tuple[bytes, bytes]], headers: Mapping[str, str]) -> str | None:
    """The file name Content-Disposition gives (raw_headers: the header lines as bytes, as the server sent them; else
    the decoded headers): filename* first, then filename (UTF-8, else Big5); None when it gives none."""
    value = next((v for k, v in raw_headers if k.strip().lower() == b"content-disposition"), None)
    if value is None:
        text = next((v for k, v in headers.items() if k.lower() == "content-disposition"), None)
        if text is None:
            return None
        value = text.encode("utf-8", "replace")
    star = plain = None
    for m in _PARAM.finditer(value):
        name, v = m.group(1).lower(), m.group(2).strip()
        if v.startswith(b'"') and v.endswith(b'"') and len(v) >= 2:
            v = re.sub(rb"\\(.)", rb"\1", v[1:-1])
        if name == b"filename*":
            star = v
        else:
            plain = v
    if star:
        charset, _, rest = star.partition(b"'")
        _lang, _, encoded = rest.partition(b"'")
        data = unquote_to_bytes(encoded if encoded or rest else star)
        try:
            return data.decode((charset or b"utf-8").decode("ascii", "replace") or "utf-8").strip() or None
        except (LookupError, UnicodeError):
            pass
    if plain:
        data = unquote_to_bytes(plain) if b"%" in plain and plain.isascii() else plain
        try:
            return _decode(data).strip() or None
        except UnicodeError:
            return data.decode("latin-1").strip() or None
    return None
