"""Which converter a file goes to (plan 2026-11-02 T3a): by its manifest row type first (网页 is a page, 资料 is
structured data the build reads itself), then by its magic bytes. A container's own signature at offset 0 (ZIP, RAR,
OLE, RTF, JPEG, PNG, GIF) wins over "%PDF" anywhere in the first kilobyte: a RAR or ZIP that stores a PDF uncompressed
shows that PDF's header early. "%PDF" after other bytes (a BOM, a server's debug line) is a PDF read from that offset.
OLE files (.doc, .xls, .ppt) are told apart by their extension; ZIPs by what they hold."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

ROUTES = ("page", "pdf", "office", "office_old", "image", "archive", "data", "unsupported")

HEAD_BYTES = 1024  # "%PDF" this far in still makes a PDF
OLE = bytes.fromhex("D0CF11E0A1B11AE1")
_OLE_KINDS = {".doc": "doc", ".dot": "doc", ".xls": "xls", ".xlt": "xls", ".ppt": "ppt", ".pps": "ppt", ".pot": "ppt"}
_OFFICE_PARTS = (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx"))
_ODF = "application/vnd.oasis.opendocument."
_ODF_KINDS = {"text": "odt", "spreadsheet": "ods", "presentation": "odp"}
_ZIP_BY_EXTENSION = {".docx": ("office", "docx"), ".xlsx": ("office", "xlsx"), ".pptx": ("office", "pptx"),
                     ".odt": ("office_old", "odt"), ".ods": ("office_old", "ods"), ".odp": ("office_old", "odp")}


@dataclass(frozen=True)
class Route:
    name: str  # one of ROUTES
    kind: str  # pdf docx xlsx pptx doc xls ppt odt ods odp rtf jpeg png gif zip rar html ''
    pdf_offset: int = 0  # where "%PDF" starts (bytes before it are dropped before converting)
    # page route, both set by accounting.input_route (plan 2026-11-08 T2): page_kind is pagetext.page_route_kind (the
    # page's kind by its 网址, how it is read, PAGE_VERSION), url where it was read (最后落地网址, else 网址), by which
    # page_text chooses what its text is read from
    page_kind: str = ""
    url: str = ""


def route_for(row_type: str, path: Path) -> Route:
    if row_type == "网页":
        return Route("page", "html")
    if row_type == "资料":
        return Route("data", "")
    path = Path(path)
    with path.open("rb") as f:
        head = f.read(HEAD_BYTES)
    if head.startswith(OLE):
        kind = _OLE_KINDS.get(path.suffix.lower())
        return Route("office_old", kind) if kind else Route("unsupported", "")
    if head.startswith(b"{\\rtf"):
        return Route("office_old", "rtf")
    if head.startswith(b"Rar!\x1a\x07"):
        return Route("archive", "rar")
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _zip_route(path)
    if head.startswith(b"\xff\xd8\xff"):
        return Route("image", "jpeg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Route("image", "png")
    if head.startswith((b"GIF87a", b"GIF89a")):
        return Route("image", "gif")
    if (at := head.find(b"%PDF")) >= 0:
        return Route("pdf", "pdf", pdf_offset=at)
    return Route("unsupported", "")


def _zip_route(path: Path) -> Route:
    """docx/xlsx/pptx (an OPC package with its main part), odt/ods/odp (an OpenDocument mimetype), else an archive of
    files. Another package (a drawing, an e-book, a Visio file) is unsupported, never unpacked as an archive. A ZIP the
    reader cannot open or read (a bad header, a damaged deflate stream: zlib.error, EOFError, …) goes by its
    extension, so the converter that fails on it says why; routing never raises for an exported file's content."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            mimetype = None
            if "mimetype" in names:
                with zf.open("mimetype") as f:
                    mimetype = f.read(200).decode("ascii", "replace").strip()
    except Exception:  # noqa: BLE001 - untrusted bytes: whatever zipfile or zlib raises, the extension decides
        return Route(*_ZIP_BY_EXTENSION.get(path.suffix.lower(), ("archive", "zip")))
    if mimetype is not None:
        sub = mimetype.removeprefix(_ODF).removesuffix("-template") if mimetype.startswith(_ODF) else ""
        return Route("office_old", _ODF_KINDS[sub]) if sub in _ODF_KINDS else Route("unsupported", "")
    if "[Content_Types].xml" in names:
        for prefix, kind in _OFFICE_PARTS:
            if any(n.startswith(prefix) for n in names):
                return Route("office", kind)
        return Route("unsupported", "")
    return Route("archive", "zip")
