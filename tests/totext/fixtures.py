"""Small files the to-text tests convert (plan 2026-11-02 T3a, T4), and the fake OCR engine. Helpers only, no
assertions; every file helper returns its path. Nothing here comes from research/ or archive*/: the words are made
up."""

from __future__ import annotations

import zipfile
import zlib
from pathlib import Path

# the first of these a machine has draws the CJK test images (macOS, then Linux's Noto CJK, then Windows)
CJK_FONTS = ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
             "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/Supplemental/Songti.ttc",
             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msjh.ttc")


def _pdf_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(path: Path, objects: list[bytes]) -> Path:
    """objects[i] is object i + 1; object 1 the catalog."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path = Path(path)
    path.write_bytes(bytes(out))
    return path


def tiny_pdf(path: Path, pages: list[str | None], size: tuple[int, int] = (612, 792)) -> Path:
    """A hand-written PDF 1.4: one Helvetica text object per page (ASCII text), or a blank page for None; every page
    `size` points."""
    box = f"/MediaBox [0 0 {size[0]} {size[1]}]"
    objects: list[bytes] = []  # objects[i] is object i + 1
    kids = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # the page tree, written once the page numbers are known
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in pages:
        page_no = len(objects) + 1
        kids.append(f"{page_no} 0 R")
        if text is None:
            objects.append(f"<< /Type /Page /Parent 2 0 R {box} >>".encode())
            continue
        stream = f"BT /F1 12 Tf 72 720 Td ({_pdf_string(text)}) Tj ET".encode("latin-1")
        objects.append(f"<< /Type /Page /Parent 2 0 R {box} /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {page_no + 1} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    return _write_pdf(path, objects)


def mixed_pdf(path: Path, line: str, image) -> Path:
    """One 612×792 page: the ASCII line in Helvetica near the top, and the PIL image below it as an embedded bitmap
    (500 points wide at most): a page with native text and text only an OCR can read."""
    rgb = image.convert("RGB")
    w_px, h_px = rgb.size
    width = min(w_px, 500)
    height = h_px * width / w_px
    data = zlib.compress(rgb.tobytes())
    stream = (f"BT /F1 12 Tf 72 720 Td ({_pdf_string(line)}) Tj ET\n"
              f"q {width:.2f} 0 0 {height:.2f} 72 {680 - height:.2f} cm /Im1 Do Q").encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> "
                b"/XObject << /Im1 6 0 R >> >> /Contents 5 0 R >>"),
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                b"/Filter /FlateDecode /Length %d >>\nstream\n") % (w_px, h_px, len(data)) + data + b"\nendstream"]
    return _write_pdf(path, objects)


def cjk_font() -> Path | None:
    """The first font of CJK_FONTS this machine has, or None."""
    return next((Path(p) for p in CJK_FONTS if Path(p).is_file()), None)


def text_image(text: str, font: Path, size: int = 64):
    """A PIL image: text in black on white, `size` px high, with a margin of `size` px around it."""
    from PIL import Image, ImageDraw, ImageFont

    face = ImageFont.truetype(str(font), size)
    left, top, right, bottom = face.getbbox(text)
    image = Image.new("RGB", (right - left + 2 * size, bottom - top + 2 * size), "white")
    ImageDraw.Draw(image).text((size - left, size - top), text, font=face, fill="black")
    return image


def png(folder: Path, width: int, height: int, name: str = "i.png", mode: str = "RGB", color="white") -> Path:
    """A plain PNG in folder."""
    from PIL import Image

    path = Path(folder) / name
    Image.new(mode, (width, height), color).save(path, "PNG")
    return path


class FakeEngine:
    """An OcrEngine (nknu.totext.ocr) for unit tests: each read returns `lines`, or the next entry of `by_call` ([]
    once they run out), or raises `raises`; every image read is recorded as (width, height, mode)."""

    def __init__(self, lines=(), *, by_call=None, raises: BaseException | None = None):
        self.lines = list(lines)
        self.by_call = [list(call) for call in by_call] if by_call is not None else None
        self.raises = raises
        self.read_images: list[tuple[int, int, str]] = []

    def read(self, image):
        self.read_images.append((image.width, image.height, image.mode))
        if self.raises is not None:
            raise self.raises
        if self.by_call is not None:
            n = len(self.read_images)
            return list(self.by_call[n - 1]) if n <= len(self.by_call) else []
        return list(self.lines)


def refuse_real_models(monkeypatch, request) -> None:
    """For an autouse fixture: unless the test is marked models, building Docling's DocumentConverter, building
    RapidOCR or reading the recogniser's charset raises, so a test that forgot a fake fails instead of loading
    models."""
    if request.node.get_closest_marker("models"):
        return
    from nknu.totext import docling_convert, ocr

    def refuse(*_args, **_kwargs):
        raise AssertionError("unit tests pass fakes: no real Docling, RapidOCR or OCR charset")

    monkeypatch.setattr(docling_convert, "build_converter", refuse)
    monkeypatch.setattr(ocr, "_rapidocr", refuse)
    monkeypatch.setattr(ocr, "ocr_charset", refuse)


def image_pdf(path: Path, size: tuple[int, int] = (1654, 2339)) -> Path:
    """An image-only PDF: PIL draws a white page with a black box (A4 at 200 dpi by default)."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    w, h = size
    ImageDraw.Draw(image).rectangle((w // 5, h // 5, w * 4 // 5, h * 2 // 5), fill="black")
    path = Path(path)
    image.save(path, "PDF", resolution=200)
    return path


def docx_with_table(path: Path) -> Path:
    """python-docx: the paragraph 「選課注意事項」 and a 2×2 table 項目/金額/學費/1,000."""
    import docx

    document = docx.Document()
    document.add_paragraph("選課注意事項")
    table = document.add_table(rows=2, cols=2)
    for (row, col), text in {(0, 0): "項目", (0, 1): "金額", (1, 0): "學費", (1, 1): "1,000"}.items():
        table.cell(row, col).text = text
    path = Path(path)
    document.save(path)
    return path


def zip_of(path: Path, entries: dict[str, bytes]) -> Path:
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path
