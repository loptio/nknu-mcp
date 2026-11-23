"""OCR for the to-text run (plan 2026-11-02 T4, as changed by addendum A of 11-02 evening). Docling's own OCR does
most of it: the OCR-on converter (docling_convert) reads, in reading order, the layout regions that overlap a bitmap,
hold no visible native text or hold outlined glyphs, and re-reads a junk text layer (control, U+FFFD, private-use
characters) with full-page OCR. Our route reads a whole PDF page with RapidOCR only where Docling is weaker:
- few_chars: fewer than FEW_CHARS characters in the text layer (a scan). On an image-only page Docling's region OCR
  turned 「一、選課期間」 into 「## 選課期間 一」 while a full-page read kept it (one sample); numbering is what the
  sections split on. (T4's checks on 11-02: a full-page read also drops some leading 一、/二、, 38 of 54 kept on
  synthetic pages; Docling's region crops dropped all 4 synthetic ones.)
- misread: a share of Han characters the recogniser does not know (媙, 䧲, 蔛: a font mapped to the wrong code
  points throughout, which is how our garbled PDFs look), which Docling's broken-layer check cannot see. The two a
  標楷體 text layer swaps one for one (詴 for 試, 帄 for 平) count as known: normalize fixes them on every page.
A PDF whose every page is ours never reaches Docling (main session 11-02 22:30); any other goes through the OCR-on
converter. Image files are
read whole, in tiles when long and narrow. RapidOCR runs the wheel's bundled PP-OCRv6 small models, given by path:
nothing is downloaded. pypdfium2, Pillow, onnxruntime, RapidOCR and OpenCC load on first use."""

from __future__ import annotations

import functools
import importlib.util
import itertools
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from nknu.totext.normalize import MISMAPPED

if TYPE_CHECKING:
    from PIL.Image import Image

FEW_CHARS = 30  # rule 1 (ours): fewer than 30 non-space characters on the page (29 -> OCR, 30 -> not)
MISREAD_MIN, MISREAD_SHARE = 3, 0.1  # rule 3 (ours): at least 3 Han characters outside the recogniser's charset and
#   10% of the page's Han characters 【Claude】. Addendum A started at 2%; on the 10-27 export, with normalize's
#   MISMAPPED counted as known, rare but real characters (names in lists, 丶 as a bullet, 郍) reach 3.5% at most and
#   the garbled layers start at 24%: 10% flags the same 25 pages as 2% there, and spares a list page with a few rare
#   name characters a whole-page OCR that would lose its table and misread those names anyway
RENDER_DPI = 200
RENDER_MAX_PIXELS = 25_000_000  # a page larger than this at RENDER_DPI (a poster) renders at a lower dpi
MIN_IMAGE_SIDE = 64
MIN_LINE_SCORE = 0.5  # RapidOCR lines below this are dropped
LOW_SCORE = 0.8  # an OCR line below this counts as low in the accounting
TILE_PX, TILE_OVERLAP = 2000, 100  # RapidOCR's Global.max_side_len shrinks a longer side to 2,000 px
ROW_GAP = 0.5  # boxes of one row closer than this many line heights join without a space
SHORT_LINE_CHARS = 2  # an OCR line this short is counted for the report (a diagram's labels, a seal, stray marks;
#                       some are real two-character headings): a noise gauge, nothing is dropped
TW_ALLOWED = frozenset("台里")  # common in Taiwanese text though OpenCC s2t rewrites them
RAPIDOCR_MODELS = {"det": "PP-OCRv6_det_small.onnx", "rec": "PP-OCRv6_rec_small.onnx",
                   "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}
REASONS = {"few_chars": "没有可用的文字层", "misread": "文字层有 {n} 个认不得的字"}
_HAN = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
        (0x20000, 0x323AF))  # radicals, Kangxi radicals, unified ideographs A and basic, compatibility, planes 2-3
_FOLDED = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))  # folded by NFKC per character


@dataclass(frozen=True)
class PageInfo:
    page: int  # 1-based
    chars: int  # non-space characters of the text layer
    misread: int  # Han characters the recogniser does not know
    misread_share: float  # misread among the page's Han characters
    images: tuple[tuple[float, float, float, float], ...]  # image objects' bounds (left, bottom, right, top), points
    size: tuple[float, float]  # width, height in points


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float
    box: tuple[float, float, float, float]  # left, top, right, bottom in pixels of the image read


class OcrEngine(Protocol):
    def read(self, image: Image) -> list[OcrLine]: ...


def _in(c: str, ranges) -> bool:
    o = ord(c)
    return any(lo <= o <= hi for lo, hi in ranges)


def rapidocr_model(task: str) -> Path:
    """The wheel's bundled model file for det, rec or cls, found without importing rapidocr (that loads OpenCV)."""
    spec = importlib.util.find_spec("rapidocr")
    if spec is None or not spec.submodule_search_locations:
        raise FileNotFoundError("没装 rapidocr（pipeline 可选组）")
    return Path(next(iter(spec.submodule_search_locations))) / "models" / RAPIDOCR_MODELS[task]


@functools.lru_cache(maxsize=1)
def ocr_charset() -> frozenset[str]:
    """The characters the recogniser writes (18,708 in rapidocr 3.10.0): the 'character' entry of the custom metadata
    of PP-OCRv6_rec_small.onnx; onnxruntime opens the model once, nothing is inferred."""
    import onnxruntime

    session = onnxruntime.InferenceSession(str(rapidocr_model("rec")), providers=["CPUExecutionProvider"])
    return frozenset(session.get_modelmeta().custom_metadata_map["character"].splitlines())


def _known(c: str, charset: frozenset[str]) -> bool:
    c = MISMAPPED.get(c, c)  # normalize fixes these in PDF text: the page shows a character the recogniser knows
    if c in charset:
        return True
    if not _in(c, _FOLDED):
        return False
    folded = unicodedata.normalize("NFKC", c)
    return folded != c and all(ch in charset for ch in folded)


def misread_count(text: str, charset: frozenset[str] | None = None) -> tuple[int, float]:
    """(Han characters outside the charset, their share of the text's Han characters), a character of normalize's
    MISMAPPED counting as the one it stands for, and a Kangxi radical, radical supplement or compatibility ideograph as
    the character NFKC folds it to. The charset (ocr_charset by default) is read only for a text that has Han
    characters."""
    han = [c for c in text if _in(c, _HAN)]
    if not han:
        return 0, 0.0
    known = ocr_charset() if charset is None else charset
    bad = sum(1 for c in han if not _known(c, known))
    return bad, bad / len(han)


def analyze_pdf(path: Path, offset: int = 0, charset: frozenset[str] | None = None) -> list[PageInfo]:
    """Each page's text layer and image objects, read with pypdfium2 from byte `offset` on. Raises what pypdfium2
    raises for a file it cannot open."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    data = Path(path).read_bytes()
    doc = pdfium.PdfDocument(data[offset:] if offset else data)
    try:
        infos = []
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
                images = tuple(tuple(obj.get_bounds()) for obj in
                               page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=15))
                size = tuple(page.get_size())
            finally:
                page.close()
            misread, share = misread_count(text, charset)
            infos.append(PageInfo(index + 1, sum(not c.isspace() for c in text), misread, share, images, size))
        return infos
    finally:
        doc.close()


def ocr_plan(info: PageInfo) -> str | None:
    """'few_chars', then 'misread', else None (Docling's own OCR is enough)."""
    if info.chars < FEW_CHARS:
        return "few_chars"
    if info.misread >= MISREAD_MIN and info.misread_share >= MISREAD_SHARE:
        return "misread"
    return None


def plan_reason(why: str, info: PageInfo) -> str:
    return REASONS[why].format(n=info.misread)


def rapid_params() -> dict:
    """RapidOCR's settings: the wheel's PP-OCRv6 small detector and recogniser and its line-direction classifier, by
    path (no model is looked up or downloaded), Traditional Chinese, lines below MIN_LINE_SCORE dropped."""
    return {"Global.log_level": "warning", "Global.text_score": MIN_LINE_SCORE,
            "Det.model_path": str(rapidocr_model("det")), "Cls.model_path": str(rapidocr_model("cls")),
            "Rec.model_path": str(rapidocr_model("rec")), "Rec.lang_type": "chinese_cht"}


def _rapidocr():
    from rapidocr import RapidOCR

    return RapidOCR(params=rapid_params())


def _lines_of(result) -> list[OcrLine]:
    """RapidOCR's output as lines: each box as the min/max of its four corners; blank or low lines dropped. An empty
    output (or one with only detections or only classes) is []."""
    boxes, txts, scores = (getattr(result, name, None) for name in ("boxes", "txts", "scores"))
    if boxes is None or txts is None or scores is None:
        return []
    lines = []
    for corners, text, score in zip(boxes.tolist() if hasattr(boxes, "tolist") else boxes, txts, scores):
        if float(score) < MIN_LINE_SCORE or not str(text).strip():
            continue
        xs, ys = [float(p[0]) for p in corners], [float(p[1]) for p in corners]
        lines.append(OcrLine(str(text).strip(), float(score), (min(xs), min(ys), max(xs), max(ys))))
    return lines


class RapidEngine:
    """The OcrEngine of a run: RapidOCR (rapid_params), built on first use; warm() loads its three models now."""

    def __init__(self):
        self._reader = None

    def _get(self):
        if self._reader is None:
            self._reader = _rapidocr()
        return self._reader

    def warm(self) -> None:
        reader = self._get()
        for load in ("_load_det_model", "_load_cls_model", "_load_rec_model"):  # rapidocr 3.10 loads them lazily
            getattr(reader, load, lambda: None)()

    def read(self, image: Image) -> list[OcrLine]:
        return _lines_of(self._get()(image))


def tiles(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Crop boxes (left, top, right, bottom): the whole image, unless its long side is over TILE_PX and at least twice
    the short side; then tiles of at most TILE_PX along the long side, overlapping by TILE_OVERLAP."""
    long, short = max(width, height), min(width, height)
    if long <= TILE_PX or long < 2 * short:
        return [(0, 0, width, height)]
    spans, start = [], 0
    while True:
        end = min(start + TILE_PX, long)
        spans.append((start, end))
        if end == long:
            break
        start += TILE_PX - TILE_OVERLAP
    if height >= width:
        return [(0, a, width, b) for a, b in spans]
    return [(a, 0, b, height) for a, b in spans]


def read_tiled(image: Image, engine: OcrEngine) -> list[OcrLine]:
    """The engine's lines over tiles(image): a tile keeps the lines whose box centre lies between the middles of its
    two overlaps (so a line read twice counts once), shifted into image coordinates."""
    boxes = tiles(*image.size)
    if len(boxes) == 1:
        return list(engine.read(image))
    vertical = image.size[1] >= image.size[0]
    a, b = (1, 3) if vertical else (0, 2)  # the long axis' start and end in a box
    lines = []
    for i, box in enumerate(boxes):
        low = (box[a] + boxes[i - 1][b]) / 2 if i else float("-inf")
        high = (boxes[i + 1][a] + box[b]) / 2 if i + 1 < len(boxes) else float("inf")
        for line in engine.read(image.crop(box)):
            x0, y0, x1, y1 = line.box
            shifted = (x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1])
            if low <= (shifted[a] + shifted[b]) / 2 < high:
                lines.append(OcrLine(line.text, line.score, shifted))
    return lines


def lines_text(lines: list[OcrLine]) -> str:
    """The lines as text, one row per line: boxes whose vertical centres lie within half a line height of the row's
    first box share its row and join left to right, without a space across a gap under ROW_GAP line heights (one
    phrase the detector split: 「一、」 「選課期間」), with one across a wider gap (a table's next cell)."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda line: (line.box[1], line.box[0])):
        height = line.box[3] - line.box[1]
        if rows:
            first = rows[-1][0]
            first_height = first.box[3] - first.box[1]
            centre, first_centre = (line.box[1] + line.box[3]) / 2, (first.box[1] + first.box[3]) / 2
            if abs(centre - first_centre) <= min(height, first_height) / 2:
                rows[-1].append(line)
                continue
        rows.append([line])
    out = []
    for row in rows:
        row.sort(key=lambda line: line.box[0])
        text = row[0].text
        for left, right in itertools.pairwise(row):
            gap = right.box[0] - left.box[2]
            tight = gap < ROW_GAP * min(left.box[3] - left.box[1], right.box[3] - right.box[1])
            text += ("" if tight else " ") + right.text
        out.append(text)
    return "\n".join(out)


def render_page(doc, page_no: int, dpi: int = RENDER_DPI) -> Image:
    """Page page_no (1-based) of an open pypdfium2 document as an RGB image at dpi, or at the dpi that keeps it
    within RENDER_MAX_PIXELS."""
    page = doc[page_no - 1]
    try:
        width, height = page.get_size()
        scale = dpi / 72
        pixels = width * height * scale * scale
        if pixels > RENDER_MAX_PIXELS:
            scale *= (RENDER_MAX_PIXELS / pixels) ** 0.5 * 0.99
        return page.render(scale=scale).to_pil().convert("RGB")
    finally:
        page.close()


def _no_beat() -> None:
    return None


def ocr_pdf_pages(path: Path, pages: Iterable[int], engine: OcrEngine, *, offset: int = 0, dpi: int = RENDER_DPI,
                  beat: Callable[[], None] = _no_beat) -> tuple[dict[int, list[OcrLine]], dict[int, str]]:
    """Each of these pages rendered whole and read by the engine: (lines per page read, f"{error}: {message}" per page
    that raised). beat() after each page."""
    import pypdfium2 as pdfium

    data = Path(path).read_bytes()
    doc = pdfium.PdfDocument(data[offset:] if offset else data)
    read: dict[int, list[OcrLine]] = {}
    failed: dict[int, str] = {}
    try:
        for page_no in sorted(pages):
            try:
                read[page_no] = read_tiled(render_page(doc, page_no, dpi), engine)
            except Exception as e:  # noqa: BLE001 - one page's failure costs that page, not the file
                failed[page_no] = f"{type(e).__name__}: {e}"
            beat()
    finally:
        doc.close()
    return read, failed


def merge_pages(pages_md: list[str], plan: dict[int, str], ocr: dict[int, list[OcrLine]], *,
                count: int = 0) -> tuple[str, list[int]]:
    """Pages 1..max(len(pages_md), count): a planned page the OCR read lines on becomes their text, any other page
    keeps Docling's (it holds Docling's region OCR; a page Docling did not return is ''); joined by a blank line.
    Returns (text, where each page starts)."""
    pages = []
    for page_no in range(1, max(len(pages_md), count, max(plan, default=0)) + 1):
        lines = ocr.get(page_no) if page_no in plan else None
        pages.append(lines_text(lines) if lines else (pages_md[page_no - 1] if page_no <= len(pages_md) else ""))
    starts, at = [], 0
    for page in pages:
        starts.append(at)
        at += len(page) + 2
    return "\n\n".join(pages), starts


def load_image(path: Path) -> Image:
    """An image file as RGB: a GIF's first frame, turned upright by its EXIF orientation, transparency on white."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    with PILImage.open(path) as im:
        im.seek(0)
        frame = ImageOps.exif_transpose(im)
        if frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info):
            rgba = frame.convert("RGBA")
            out = PILImage.new("RGB", rgba.size, "white")
            out.paste(rgba, mask=rgba.getchannel("A"))
            return out
        return frame.convert("RGB")


def ocr_image(path: Path, engine: OcrEngine) -> tuple[str, list[OcrLine], str | None]:
    """(text, lines, why it was skipped): an image whose short side is under MIN_IMAGE_SIDE is skipped."""
    image = load_image(path)
    short = min(image.size)
    if short < MIN_IMAGE_SIDE:
        return "", [], f"图太小（短边 {short} px < {MIN_IMAGE_SIDE} px）"
    lines = read_tiled(image, engine)
    return lines_text(lines), lines, None


@functools.lru_cache(maxsize=1)
def _opencc():
    from opencc import OpenCC

    return OpenCC("t2s"), OpenCC("s2t")


@functools.cache
def _simplified(c: str) -> bool:
    if c in TW_ALLOWED or not _in(c, _HAN):
        return False
    t2s, s2t = _opencc()
    return t2s.convert(c) == c != s2t.convert(c)


def simplified_only(text: str) -> int:
    """Characters only Simplified Chinese writes (t2s keeps them, s2t changes them), TW_ALLOWED aside: a flag for
    the report, never a fix."""
    return sum(1 for c in text if _simplified(c))


def short_lines(texts: Iterable[str]) -> int:
    """OCR lines with 1 to SHORT_LINE_CHARS non-space characters."""
    return sum(1 for text in texts if 0 < sum(not c.isspace() for c in text) <= SHORT_LINE_CHARS)
