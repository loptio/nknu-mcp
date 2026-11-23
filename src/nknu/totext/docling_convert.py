"""PDF, docx, xlsx and pptx to Markdown with Docling 2.145.0 (plan 2026-11-02 T3a; spec 2026-11-02 §0 「11-02 下午
更新」: Docling replaces pdfplumber). PDFs go through the standard pipeline with layout analysis and TableFormer
(accurate). Models load from DOCLING_ARTIFACTS only (the package __init__ set HF_HUB_OFFLINE before anything loaded
huggingface_hub). Markdown comes per PDF page (pages[n - 1] is page n), once for the other formats. Docling itself is
imported lazily: importing this module stays cheap (no torch).

Every DocumentConverter comes from build_converter(PDF pipeline options). Two exist (T4, addendum A): OCR off, for
Word, Excel and PowerPoint (a PDF whose every page our own whole-page OCR reads skips Docling since 11-02 22:30), and
OCR on, Docling's layout-region OCR with
RapidOCR (regions overlapping a bitmap, without visible native text, or holding outlined glyphs; OCR cells over
native text dropped), a junk text layer re-read with full-page OCR. The OCR-on converter takes the wheel's PP-OCRv6
small models by path, so nothing is looked for under <artifacts>/RapidOcr/. Its result names the pages Docling's OCR
put text on and those it re-read for a broken layer, from the per-page confidence report."""

from __future__ import annotations

import gc
import math
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docling_core.types.doc import ContentLayer

from nknu.totext.ocr import rapidocr_model

if TYPE_CHECKING:
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter

DOCLING_ARTIFACTS: Path = Path(os.path.expanduser(os.environ.get("NKNU_DOCLING_ARTIFACTS")
                                                  or "~/.cache/docling/models"))
DOC_TIMEOUT_S = 3600  # 11-02 23:59: 900 s cut 100+ page PDFs short on the memory-starved 16 GiB Mac (G8)
MD_OPTIONS = {"escape_html": False, "escape_underscores": False, "image_placeholder": "", "compact_tables": True,
              "traverse_pictures": True, "included_content_layers": {ContentLayer.BODY, ContentLayer.FURNITURE}}
KINDS = ("pdf", "docx", "xlsx", "pptx")
ERROR_CHARS = 200  # a Docling error message kept as the reason
OCR_LANG = ("iso:zh-Hant",)  # RapidOCR's chinese_cht: the multilingual PP-OCRv6 recogniser

# Word list numbering Docling 2.145 cannot render, mapped to the format it renders the same way (ECMA-376 §17.18.59);
# Docling writes any other format it lacks as a plain decimal counter (1、 for 壹、): such values, ideographLegalTraditional
# first among them (its 壹貳參 has no twin: chineseLegalSimplified writes 贰), are counted and left as they are
NUMFMT_REMAP = {"taiwaneseCountingThousand": "chineseCountingThousand", "taiwaneseCounting": "chineseCounting",
                "taiwaneseDigital": "ideographDigital"}
# what Docling 2.145's Word reader renders (msword_backend._VISIBLE_NUMBERING_FORMATS), plus bullets and no marker
NUMFMT_RENDERED = frozenset({"decimal", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter", "decimalZero",
                             "chineseCounting", "chineseCountingThousand", "chineseLegalSimplified", "ideographDigital",
                             "ideographTraditional", "ideographZodiac", "japaneseCounting", "decimalFullWidth",
                             "decimalEnclosedCircle", "bullet", "none"})
_NUMFMT = re.compile(rb'(<(?:\w+:)?numFmt\b[^>]*?\b(?:\w+:)?val=")(\w+)(")')


@dataclass
class DoclingOut:
    status: str  # ok | partial | failed
    reason: str  # '' for ok
    pages: list[str]  # Markdown per PDF page (1-based page n = pages[n-1]); one entry for docx/xlsx/pptx
    doc_json: dict | None  # DoclingDocument.export_to_dict(); None when the conversion failed
    pictures: int
    ocr_pages: tuple[int, ...] = ()  # PDF pages Docling's OCR put text on (the OCR-on converter)
    broken_pages: tuple[int, ...] = ()  # among them, pages re-read whole for a broken text layer
    ocr_text: dict[int, list[str]] = field(default_factory=dict)  # per PDF page, each text line Docling's OCR read


def pdf_options(*, device: str, artifacts: Path, timeout_s: float = DOC_TIMEOUT_S,
                ocr: bool = False) -> PdfPipelineOptions:
    """The PDF pipeline: layout analysis and TableFormer (accurate) from the local artifacts; with ocr, Docling's
    layout-region OCR (OcrMode.DEFAULT) with RapidOCR on the wheel's own model files, a broken text layer re-read."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions

    extra = {}
    if ocr:
        from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions

        extra["ocr_options"] = RapidOcrOptions(
            lang=list(OCR_LANG), det_model_path=str(rapidocr_model("det")), rec_model_path=str(rapidocr_model("rec")),
            cls_model_path=str(rapidocr_model("cls")), mode=OcrMode.DEFAULT, replace_broken_text_layer=True)
    return PdfPipelineOptions(do_ocr=ocr, do_table_structure=True,
                              table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE),
                              artifacts_path=artifacts, document_timeout=timeout_s,
                              accelerator_options=AcceleratorOptions(device=device), **extra)


def build_converter(pdf: PdfPipelineOptions) -> DocumentConverter:
    """One DocumentConverter for KINDS: PDFs with these pipeline options, Word, Excel and PowerPoint with Docling's
    own readers (no model)."""
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    return DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.XLSX, InputFormat.PPTX],
                             format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf)})


def _status(res: Any, timeout_s: float = DOC_TIMEOUT_S) -> tuple[str, str]:
    """A ConversionResult's status as (status, reason). Docling's enums are str enums: compared by value, so this
    module never imports docling.datamodel (it pulls in torch)."""
    errors = list(getattr(res, "errors", None) or [])
    first = next((e.error_message for e in errors if getattr(e, "error_message", "")), "")[:ERROR_CHARS]
    if res.status == "success":
        return "ok", ""
    if res.status == "partial_success":
        if any(getattr(e, "category", None) == "timeout" for e in errors):
            return "partial", f"Docling 超时（{timeout_s:g} 秒），只转出前面几页"
        return "partial", first or "Docling 只转出一部分"
    if res.status == "failure":
        return "failed", first or "Docling 转换失败"
    return "failed", first or f"Docling 没有转换（{getattr(res.status, 'value', res.status)}）"


def _ocr_pages(res: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(pages Docling's OCR put text on, pages among them re-read for a broken text layer), from the per-page
    confidence report: Docling sets ocr_score only when OCR cells reached the page's text, and a broken layer forces
    parse_score to 0 (an ordinary low parse score without OCR text is no re-read). Empty without a report."""
    pages = getattr(getattr(res, "confidence", None), "pages", None) or {}

    def score(page: int, name: str) -> float:
        value = getattr(pages[page], name, None)
        return float(value) if value is not None else math.nan

    read = tuple(sorted(n for n in pages if math.isfinite(score(n, "ocr_score"))))
    return read, tuple(n for n in read if score(n, "parse_score") == 0)


def _ocr_text(res: Any) -> dict[int, list[str]]:
    """Per PDF page, the text of each line Docling's OCR read: the cells marked from_ocr in the page's layout
    clusters and their children (the page keeps its layout predictions after the conversion; its parsed cells are
    released), each cell once. A page without OCR text is left out; {} when the result keeps no pages."""
    out: dict[int, list[str]] = {}
    for page in getattr(res, "pages", None) or ():
        layout = getattr(getattr(page, "predictions", None), "layout", None)
        clusters = list(getattr(layout, "clusters", None) or ())
        seen: set[int] = set()
        texts = []
        while clusters:
            cluster = clusters.pop(0)
            clusters.extend(getattr(cluster, "children", None) or ())
            for cell in getattr(cluster, "cells", None) or ():
                if getattr(cell, "from_ocr", False) and id(cell) not in seen:
                    seen.add(id(cell))
                    texts.append(str(getattr(cell, "text", "") or ""))
        if texts:
            out[int(page.page_no)] = texts
    return out


class DoclingConverter:
    """One lazily built DocumentConverter per instance; with ocr, Docling's layout-region OCR (pdf_options). Docling
    reports a bad file in its result (raises_on_error off), which becomes a failed DoclingOut; an exception means a
    broken setup (a missing model) or a Docling bug, and the caller accounts it as failed."""

    def __init__(self, *, device: str | None = None, artifacts: Path = DOCLING_ARTIFACTS,
                 timeout_s: float = DOC_TIMEOUT_S, ocr: bool = False):
        self.device = device or "auto"  # Docling's own order: cuda, then mps (TableFormer stays on the CPU), then cpu
        self.artifacts = Path(artifacts)
        self.timeout_s = timeout_s
        self.ocr = ocr
        self._converter: DocumentConverter | None = None

    def options(self) -> PdfPipelineOptions:
        return pdf_options(device=self.device, artifacts=self.artifacts, timeout_s=self.timeout_s, ocr=self.ocr)

    def _docling(self) -> DocumentConverter:
        if self._converter is None:
            self._converter = build_converter(self.options())
        return self._converter

    def convert(self, path: Path, kind: str) -> DoclingOut:
        """path read as kind (pdf | docx | xlsx | pptx); Docling tells the format from the file itself. The memory the
        conversion used goes back afterwards (release_memory), a failed one's too."""
        if kind not in KINDS:
            raise ValueError(f"Docling reads {', '.join(KINDS)}, not {kind!r}")
        try:
            return self._convert(Path(path), kind)
        finally:
            release_memory()

    def _convert(self, path: Path, kind: str) -> DoclingOut:
        res = self._docling().convert(path, raises_on_error=False)
        status, reason = _status(res, self.timeout_s)
        doc = getattr(res, "document", None)
        if status == "failed" or doc is None:
            return DoclingOut(status, reason, [], None, 0)
        if kind != "pdf":
            return DoclingOut(status, reason, [doc.export_to_markdown(**MD_OPTIONS)], doc.export_to_dict(),
                              len(doc.pictures))
        # pages Docling could not finish stay in doc.pages, empty: max() keeps the numbering when one is missing
        last = max(doc.pages, default=0)
        pages = [doc.export_to_markdown(page_no=n, **MD_OPTIONS) for n in range(1, last + 1)]
        ocr_pages, broken = _ocr_pages(res)
        return DoclingOut(status, reason, pages, doc.export_to_dict(), len(doc.pictures), ocr_pages, broken,
                          _ocr_text(res))


def release_memory() -> None:
    """Garbage collection, then the MPS allocator's cached blocks back to the system when torch runs on MPS. Measured
    11-02 on the 16 GiB M2: one run's process held 2.4 GB of Metal memory after 17 PDFs while the machine swapped,
    and conversions slowed from about 2 to 20-30 s a page. A process that never imported torch has nothing to free."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        torch.mps.empty_cache()


def fix_docx_numbering(src: Path, dst: Path) -> tuple[int, int]:
    """dst = src with word/numbering.xml's Taiwanese numFmt values mapped by NUMFMT_REMAP; every other entry is
    copied unchanged. Returns (numFmts remapped, numFmts Docling cannot render). Raises zipfile.BadZipFile for a file
    that is no ZIP."""
    remapped = unrendered = 0

    def swap(m: re.Match) -> bytes:
        nonlocal remapped, unrendered
        value = m.group(2).decode("ascii")
        if value not in NUMFMT_REMAP:
            if value not in NUMFMT_RENDERED:
                unrendered += 1
            return m.group(0)
        remapped += 1
        return m.group(1) + NUMFMT_REMAP[value].encode("ascii") + m.group(3)

    with zipfile.ZipFile(src) as zin:
        if "word/numbering.xml" not in zin.namelist():
            shutil.copyfile(src, dst)
            return 0, 0
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info)
                if info.filename == "word/numbering.xml":
                    data = _NUMFMT.sub(swap, data)
                zout.writestr(info, data)
    return remapped, unrendered


def artifact_commits(artifacts: Path = DOCLING_ARTIFACTS) -> dict[str, str]:
    """Each model folder under artifacts -> the commit its files were downloaded at (the first line of the Hugging
    Face .metadata files under <folder>/.cache/huggingface/download/); '' when none says, several commits joined by
    ','."""
    artifacts = Path(artifacts)
    if not artifacts.is_dir():
        return {}
    out: dict[str, str] = {}
    for folder in sorted(p for p in artifacts.iterdir() if p.is_dir()):
        commits: Counter[str] = Counter()
        for meta in (folder / ".cache" / "huggingface" / "download").rglob("*.metadata"):
            try:
                first = meta.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            except (OSError, UnicodeDecodeError):
                continue
            if re.fullmatch(r"[0-9a-f]{40}", first):
                commits[first] += 1
        out[folder.name] = ",".join(sorted(commits))
    return out
