"""One file to text by its route (plan 2026-11-02 T3b, T4, T5). A page goes through pagetext, by the URL its route
carries (plan 2026-11-08 T2); its result keeps the route's page_kind, which ends with the page rule's version. A PDF,
after dropping the bytes in front of "%PDF", has its pages analysed (ocr.analyze_pdf) and planned (ocr.ocr_plan): a
PDF whose every page is ours to OCR never reaches Docling (11-02 night: with no usable text layer it gave nothing,
and cost a second set of Docling models in memory plus layout and table inference on every page), any other goes
through the OCR-on converter (Docling's region OCR, a broken text layer re-read); then our whole-page OCR reads the
planned pages and replaces their text (addendum A). docx, xlsx and pptx go through the OCR-off Docling converter, a
docx after its Taiwanese list numbering is mapped to formats Docling renders; old Office, OpenDocument and RTF through
LibreOffice first, then the office path. An image is OCR'd whole. An archive is unpacked (archives.extract) and each
member converted as an 附件 of its own, into its own cache entry (by its sha256; one already there is reused, one
that crashed the process is not tried again); an archive inside the archive is not unpacked. The archive's own result
lists its members and has no text. Structured data and unrecognised formats are skipped with a reason. A conversion
that fails says why in Simplified Chinese, and one that ends partial with no text is failed; any other exception is
the caller's: convert_and_save, the run loop's step for one file (and for each archive member), turns it into a
failed result, so the run goes on, except a SetupError (a tool that cannot load), which stops the run with the file
still pending.

store() writes a result into the cache: text.md, Docling's document (docling.json.gz), the per-line OCR (ocr.json),
a page's whole reading (cache.FULL, full.md: blueprint item 22) and result.json, the record the accounting reads (the
Converted fields but text, doc_json, ocr and full, plus the route, the counts, the seconds and the tools)."""

from __future__ import annotations

import gzip
import json
import plistlib
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from importlib import metadata
from pathlib import Path
from xml.parsers.expat import ExpatError

from nknu.totext import archives, docling_convert, libreoffice, ocr, pagetext
from nknu.totext.accounting import join_notes, made_otherwise, member_crash_reason
from nknu.totext.archives import ArchiveError, Member
from nknu.totext.cache import FULL, PIPELINE_VERSION, Cache, sha256_file
from nknu.totext.docling_convert import DOCLING_ARTIFACTS, DoclingConverter, DoclingOut
from nknu.totext.libreoffice import ConvertError
from nknu.totext.ocr import LOW_SCORE, OcrEngine, OcrLine
from nknu.totext.route import Route, route_for

REASON_CHARS = 300
OCR_ERROR_CHARS = 100  # the first failed page's error, kept in the reason
BOM = b"\xef\xbb\xbf"
SKIP_REASONS = {"data": "结构化资料：行事历由建库直接读"}
EMPTY_REASONS = {"page": "网页内容区没有文字", "pdf": "PDF 转出来没有文字（OCR 也没认出字）",
                 "office": "文件转出来没有文字", "office_old": "文件转出来没有文字", "image": "OCR 没认出字"}
ANALYSIS_FAILED = "无法分析页面，没做整页 OCR"
NESTED_ARCHIVE = "压缩档里的压缩档，没有再解开"
EMPTY_ARCHIVE = "压缩档里没有档案"
TOOL_PACKAGES = ("docling", "docling-core", "docling-parse", "rapidocr", "onnxruntime", "pypdfium2")
_NOT_IN_RESULT = ("text", "doc_json", "ocr", "docling_ocr", "full")  # text.md and the extras hold these


@dataclass
class Converted:
    route: str
    status: str  # ok | partial | empty | failed | skipped
    reason: str  # '' for ok
    text: str
    pages: int = 0  # PDF pages
    page_starts: list[int] = field(default_factory=list)  # PDF: where page n starts in text (page_starts[n - 1])
    ocr_pages: list[dict] = field(default_factory=list)  # pages our OCR read: {"page", "why", "reason", "lines",
    #                                                      "mean_score"}
    failed_pages: list[int] = field(default_factory=list)  # pages whose OCR raised
    ocr: list[dict] = field(default_factory=list)  # per line our OCR read {"page", "text", "score", "box"}
    fixes: list[str] = field(default_factory=list)  # bom_prefix:N, pdf_prefix:N, docx_numbering:N, …
    page: dict | None = None  # page route: {"title", "album", "images", "links"} (links: PageText.links, of full)
    members: list[dict] = field(default_factory=list)  # archive route, one per member: {"index", "path", "sha256",
    #   "route", "kind", "pdf_offset", "size", "status", "reason", "note"}; sha256 and route '' when not extracted
    doc_json: dict | None = None  # DoclingDocument.export_to_dict()
    pictures: int = 0  # pictures Docling found (in a docx, xlsx or pptx nobody OCRs them)
    simplified_only: int = 0  # characters only Simplified Chinese writes, in the OCR'd text of the result (our OCR
    #                           and Docling's region OCR on the pages that keep Docling's text)
    docling_ocr_pages: list[int] = field(default_factory=list)  # PDF pages Docling's OCR put text on
    broken_pages: list[int] = field(default_factory=list)  # among them, pages Docling re-read for a broken layer
    ocr_short_lines: int = 0  # lines of that OCR'd text with at most ocr.SHORT_LINE_CHARS characters (noise gauge)
    docling_ocr: dict[int, list[str]] = field(default_factory=dict)  # per PDF page, the lines Docling's OCR read on
    #   the pages that keep Docling's text (docling_ocr.json): which text came from a picture, for later stages
    full: str = ""  # page route: the whole body's reading (PageText.full; cache.FULL, blueprint item 22)


class SetupError(RuntimeError):
    """A converter that cannot start (Docling's models missing, RapidOCR without OpenCV): never one file's failure.
    convert_and_save lets it through, so the run stops before it marks every such file failed, results a rerun would
    keep; str() is the reason (Simplified Chinese)."""


def _no_beat() -> None:
    return None


def _load_pipeline(converter, kind: str) -> bool:
    """Docling's pipeline for kind loaded into converter now; False for a converter without Docling's own
    DocumentConverter (a test's fake)."""
    build = getattr(converter, "_docling", None)  # DoclingConverter's lazily built DocumentConverter
    if build is None:
        return False
    from docling.datamodel.base_models import InputFormat

    build().initialize_pipeline(InputFormat(kind))
    return True


class Tools:
    """The converters one run uses, each built on first use (a test passes fakes): docling (OCR off: docx, xlsx and
    pptx), docling_ocr (Docling's region OCR on: PDFs) and ocr, the engine of our whole-page and image OCR. beat is the
    watchdog's feed, a no-op unless the run sets one; it is called after each Docling call, each OCR'd page or image,
    an archive's extraction and each archive member converted."""

    def __init__(self, docling: DoclingConverter | None = None, ocr: OcrEngine | None = None,
                 beat: Callable[[], None] | None = None, *, docling_ocr: DoclingConverter | None = None):
        self._docling = docling
        self._docling_ocr = docling_ocr
        self._ocr = ocr
        self.beat: Callable[[], None] = beat or _no_beat
        self._versions: dict | None = None
        self._warm: set[str] = set()

    @property
    def docling(self) -> DoclingConverter:
        if self._docling is None:
            self._docling = DoclingConverter()
        return self._docling

    @property
    def docling_ocr(self) -> DoclingConverter:
        if self._docling_ocr is None:
            self._docling_ocr = DoclingConverter(ocr=True)
        return self._docling_ocr

    @property
    def ocr(self) -> OcrEngine:
        if self._ocr is None:
            self._ocr = ocr.RapidEngine()  # the module: method bodies do not see the class's names
        return self._ocr

    def _load_ocr(self) -> bool:
        warm = getattr(self.ocr, "warm", None)
        if warm is None:
            return False
        warm()
        return True

    def warm_up(self, kind: str) -> float:
        """What converting kind (pdf, docx, xlsx, pptx, or 'ocr' for images) needs, loaded now, each part once, so no
        file's seconds hold that one-time load (the PDF models took 19.5 s cold on 11-02): for docx, xlsx and pptx the
        OCR-off converter's pipeline for it; for pdf the OCR-on converter's PDF pipeline and the OCR engine's models,
        since which of them a PDF needs shows only once its pages are analysed (an all-scanned one needs the engine
        alone; the OCR-off converter's PDF pipeline is never loaded, 11-02 night); for 'ocr' the OCR engine alone.
        Returns the seconds it took: 0.0 when loaded already, or for fakes. A load that fails (a model missing) raises
        SetupError."""
        if kind == "ocr":
            loads = [("rapidocr", self._load_ocr)]
        elif kind == "pdf":
            loads = [("pdf-ocr", lambda: _load_pipeline(self.docling_ocr, "pdf")), ("rapidocr", self._load_ocr)]
        else:
            loads = [(kind, lambda: _load_pipeline(self.docling, kind))]
        seconds = 0.0
        for key, load in loads:
            if key in self._warm:
                continue
            started = time.monotonic()
            try:
                loaded = load()
            except Exception as e:  # whatever a model load raises, the run cannot go on with it
                what = "OCR" if key == "rapidocr" else "Docling"
                raise SetupError(f"{what} 准备不了（{type(e).__name__}: {e}），没有再转下去") from e
            if loaded:
                seconds += time.monotonic() - started
            self._warm.add(key)
        return seconds

    def describe(self) -> dict:
        """What converts, for result.json and the report; read once, loading no model and starting no program."""
        if self._versions is None:
            self._versions = tool_versions(getattr(self._docling, "artifacts", DOCLING_ARTIFACTS))
        return self._versions


def tool_versions(artifacts: Path = DOCLING_ARTIFACTS) -> dict:
    out: dict = {"pipeline": PIPELINE_VERSION}
    for name in TOOL_PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = ""
    out["docling_models"] = docling_convert.artifact_commits(artifacts)
    out["soffice"] = libreoffice.SOFFICE
    out["libreoffice"] = libreoffice_version(libreoffice.SOFFICE)
    return out


def libreoffice_version(soffice: str) -> str:
    """LibreOffice's version without starting it: CFBundleShortVersionString of the macOS app that holds soffice
    (Homebrew's soffice is a wrapper script running /Applications/LibreOffice.app); '' elsewhere."""
    plists = [p / "Contents" / "Info.plist" for p in Path(soffice).resolve().parents if p.suffix == ".app"][:1]
    if sys.platform == "darwin":
        plists.append(Path("/Applications/LibreOffice.app/Contents/Info.plist"))
    for plist in plists:
        try:
            with plist.open("rb") as f:
                return str(plistlib.load(f).get("CFBundleShortVersionString", ""))
        except (OSError, ValueError, ExpatError):
            continue
    return ""


def failed(route: str, reason: str) -> Converted:
    """A failed conversion; the reason on one line, at most REASON_CHARS characters."""
    return Converted(route, "failed", " ".join(reason.split())[:REASON_CHARS] or "转换失败", "")


def text_chars(text: str) -> int:
    """Non-space characters outside [[img:…]] lines: the text a file gave of its own."""
    return sum(not c.isspace() for c in pagetext.IMG_LINE.sub("", text))


def join_pages(pages: list[str]) -> tuple[str, list[int]]:
    """Pages joined by a blank line, and where each starts."""
    starts, at = [], 0
    for page in pages:
        starts.append(at)
        at += len(page) + 2
    return "\n\n".join(pages), starts


def _finish(conv: Converted) -> Converted:
    """No text after a conversion: ok becomes empty (nothing to read), partial becomes failed with its reason (the
    errors cost everything, and T7a's fallback looks at failed files only)."""
    if not conv.text.strip():
        if conv.status == "ok":
            conv.status, conv.reason = "empty", EMPTY_REASONS.get(conv.route, "转出来没有文字")
        elif conv.status == "partial":
            conv.status = "failed"
    return conv


def _named(path: Path, kind: str, workdir: Path) -> Path:
    """path itself when its extension says kind, else a link workdir/source.<kind> to it: Docling tells formats
    apart by content and extension."""
    if path.suffix.lower() == f".{kind}":
        return path
    workdir.mkdir(parents=True, exist_ok=True)
    link = workdir / f"source.{kind}"
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(path.resolve())
    except OSError:  # no symlinks here: a copy
        shutil.copyfile(path, link)
    return link


def _page(path: Path, route: Route) -> Converted:
    try:
        got = pagetext.page_text(path.read_bytes(), route.url)  # read by where it was read (plan 2026-11-08 T2)
    except pagetext.PageDecodeError as e:
        return failed("page", str(e))
    meta = {"title": got.title, "album": got.album, "images": list(got.images), "links": list(got.links)}
    return _finish(Converted("page", "ok", "", got.text, page=meta, full=got.full))


def _ocr_rows(page: int, lines: list[OcrLine]) -> list[dict]:
    return [{"page": page, "text": line.text, "score": round(line.score, 4), "box": [round(v, 1) for v in line.box]}
            for line in lines]


def _mean(lines: list[OcrLine]) -> float | None:
    return round(sum(line.score for line in lines) / len(lines), 4) if lines else None


def _pdf(path: Path, route: Route, tools: Tools, workdir: Path) -> Converted:
    fixes = []
    if route.pdf_offset:
        workdir.mkdir(parents=True, exist_ok=True)
        source = workdir / "input.pdf"
        with path.open("rb") as f, source.open("wb") as g:
            prefix = f.read(route.pdf_offset)
            shutil.copyfileobj(f, g)
        fixes.append(f"{'bom' if prefix == BOM else 'pdf'}_prefix:{route.pdf_offset}")
    else:
        source = _named(path, "pdf", workdir)
    try:
        infos = ocr.analyze_pdf(source)
    except Exception:  # noqa: BLE001 - Docling may still read it: its text is kept and the reason says what is missing
        infos = None
    plan = {info.page: why for info in infos or () if (why := ocr.ocr_plan(info))}
    if infos and len(plan) == len(infos):
        # every page is ours to read: no text layer for Docling to give, so Docling's result is a blank page each
        # (a page our OCR cannot read stays blank) and no Docling model loads for it
        out = DoclingOut("ok", "", [""] * len(infos), None, 0)
    else:
        out = tools.docling_ocr.convert(source, "pdf")
        tools.beat()
    reasons = [out.reason] if out.reason else []
    if infos is None:
        reasons.append(ANALYSIS_FAILED)
    if out.status == "failed":  # the text-layer pages need Docling
        return Converted("pdf", "failed", "；".join(reasons) or "转换失败", "", fixes=fixes)
    read, errors = ocr.ocr_pdf_pages(source, plan, tools.ocr, beat=tools.beat) if plan else ({}, {})
    text, starts = ocr.merge_pages(out.pages, plan, read, count=len(infos or ()))
    if errors:
        first = min(errors)
        reasons.append(f"第 {'、'.join(map(str, sorted(errors)))} 页 OCR 失败（{errors[first][:OCR_ERROR_CHARS]}）")
    if out.status == "ok" and (infos is None or errors):
        status = "partial"  # _finish makes a partial without text failed
    else:
        status = out.status
    info_of = {info.page: info for info in infos or ()}
    ocr_pages = [{"page": n, "why": why, "reason": ocr.plan_reason(why, info_of[n]), "lines": len(read[n]),
                  "mean_score": _mean(read[n])} for n, why in sorted(plan.items()) if n in read]
    rows = [row for n in sorted(read) for row in _ocr_rows(n, read[n])]
    # the OCR'd lines the text holds: ours, and Docling's on the pages that keep Docling's text (merge_pages)
    kept = {n: list(lines) for n, lines in sorted(out.ocr_text.items()) if lines and not (n in plan and read.get(n))}
    ocr_lines = [row["text"] for row in rows] + [line for lines in kept.values() for line in lines]
    return _finish(Converted("pdf", status, "；".join(reasons), text, pages=len(starts), page_starts=starts,
                             ocr_pages=ocr_pages, failed_pages=sorted(errors), ocr=rows, fixes=fixes,
                             doc_json=out.doc_json, pictures=out.pictures,
                             simplified_only=ocr.simplified_only("\n".join(ocr_lines)),
                             docling_ocr_pages=list(out.ocr_pages), broken_pages=list(out.broken_pages),
                             ocr_short_lines=ocr.short_lines(ocr_lines), docling_ocr=kept))


def _image(path: Path, tools: Tools) -> Converted:
    tools.warm_up("ocr")  # loaded already in a run; a broken setup is a SetupError, not this image's failure
    text, lines, skipped = ocr.ocr_image(path, tools.ocr)
    tools.beat()
    if skipped:
        return Converted("image", "skipped", skipped, "")
    return _finish(Converted("image", "ok", "", text, ocr=_ocr_rows(1, lines),  # an image counts as page 1
                             simplified_only=ocr.simplified_only(text),
                             ocr_short_lines=ocr.short_lines(line.text for line in lines)))


def _office(path: Path, kind: str, route: str, tools: Tools, workdir: Path) -> Converted:
    fixes = []
    source = None
    if kind == "docx":
        workdir.mkdir(parents=True, exist_ok=True)
        fixed = workdir / "input.docx"
        try:
            remapped, unrendered = docling_convert.fix_docx_numbering(path, fixed)
        except Exception as e:  # noqa: BLE001 - a fix that cannot read the file never costs its text: Docling tries
            fixed.unlink(missing_ok=True)
            fixes.append(f"docx_numbering_skipped:{type(e).__name__}")
        else:
            fixes += [f"docx_numbering:{remapped}"] * bool(remapped)
            fixes += [f"docx_numbering_unsupported:{unrendered}"] * bool(unrendered)
            source = fixed
    out: DoclingOut = tools.docling.convert(source or _named(path, kind, workdir), kind)
    tools.beat()
    text, _starts = join_pages(out.pages)
    return _finish(Converted(route, out.status, out.reason, text, fixes=fixes, doc_json=out.doc_json,
                             pictures=out.pictures))


def _docling_kind(route: Route) -> str | None:
    """The format Docling reads for this route (an old Office file after LibreOffice), or None (run._docling_kind's
    rule; run imports this module)."""
    if route.name == "pdf":
        return "pdf"
    if route.name == "office":
        return route.kind
    if route.name == "office_old":
        return libreoffice.TARGET.get(route.kind)
    return None


def _member(m: Member, tools: Tools, workdir: Path, cache: Cache, key: str, file: str) -> dict:
    """One archive member's entry, converting it into its own cache entry when it has none yet."""
    entry = {"index": m.index, "path": m.path, "sha256": "", "route": "", "kind": "", "pdf_offset": 0,
             "size": m.size, "status": m.status, "reason": m.reason, "note": ""}
    if m.local is None:  # not extracted: the extraction's word stands
        return entry
    sha = sha256_file(m.local)
    entry.update(sha256=sha, note=m.reason)
    try:
        route = route_for("附件", m.local)
    except Exception as e:  # noqa: BLE001 - routing reads the member's head (a damaged zip goes by its extension
        #                                       there); anything else it raises is that member's loss alone
        why = " ".join(f"{type(e).__name__}: {e}".split())[:OCR_ERROR_CHARS]
        entry.update(status="failed", reason=join_notes(f"认不出格式（{why}）", m.reason))
        return entry
    entry.update(route=route.name, kind=route.kind, pdf_offset=route.pdf_offset)
    if route.name == "archive":
        status, reason = "skipped", NESTED_ARCHIVE
    elif (done := cache.done(sha)) is not None:  # the same bytes converted before, as a file or a member
        other = made_otherwise(done, route)
        status, reason = ("skipped", other) if other else (str(done.get("status") or ""),
                                                           str(done.get("reason") or ""))
    elif cache.crashed(sha):  # it crashed the process on an earlier run: retrying the archive must not loop on it
        status, reason = "failed", member_crash_reason(cache, sha)
    else:
        if (kind := _docling_kind(route)) is not None:
            tools.warm_up(kind)  # a model's one-time load is no member's time
        result = convert_and_save(cache, sha, m.local, route, tools, workdir / f"m{m.index:04d}",
                                  key=f"{key}!{m.path}", file=f"{file}!{m.path}", fmt=archives.extension(m.path))
        tools.beat()
        status, reason = result["status"], result["reason"]
    entry.update(status=status, reason=join_notes(reason, m.reason))
    return entry


def _archive(path: Path, route: Route, tools: Tools, workdir: Path, cache: Cache | None, key: str,
             file: str) -> Converted:
    if cache is None:
        raise ValueError("the archive route needs the cache: each member gets an entry of its own")
    try:
        members = archives.extract(path, route.kind, workdir / "members")
    except ArchiveError as e:
        return failed("archive", str(e))
    tools.beat()
    entries = [_member(m, tools, workdir, cache, key, file) for m in members]
    if not entries:
        return Converted("archive", "empty", EMPTY_ARCHIVE, "")
    return Converted("archive", "ok", f"解开 {len(entries)} 份成员", "", members=entries)


def convert_file(path: Path, route: Route, tools: Tools, workdir: Path, *, cache: Cache | None = None,
                 fmt: str = "", key: str = "", file: str = "") -> Converted:
    """path as its route says. workdir: this file's scratch folder (made when needed; the caller removes it). cache:
    where an archive's members get entries of their own (the archive route needs it). fmt: the manifest's 格式,
    named when the format is not recognised (else the file's extension). key, file: the input's, from which an
    archive member's are made ("<key>!<member path>")."""
    path, workdir = Path(path), Path(workdir)
    name = route.name
    if name == "page":
        return _page(path, route)
    if name == "pdf":
        return _pdf(path, route, tools, workdir)
    if name == "office":
        return _office(path, route.kind, name, tools, workdir)
    if name == "office_old":
        try:
            modern = libreoffice.to_modern(path, route.kind, workdir)
        except ConvertError as e:
            return failed(name, str(e))
        return _office(modern, libreoffice.TARGET[route.kind], name, tools, workdir)
    if name == "image":
        return _image(path, tools)
    if name == "archive":
        return _archive(path, route, tools, workdir, cache, key, file)
    if name == "unsupported":
        return Converted(name, "skipped", f"认不出的档案格式（{fmt or path.suffix.lower() or '没有扩展名'}）", "")
    if name in SKIP_REASONS:
        return Converted(name, "skipped", SKIP_REASONS[name], "")
    raise ValueError(f"unknown route {name!r}")


def result_of(conv: Converted, route: Route, *, seconds: float, tools: dict, key: str = "",
              file: str = "") -> tuple[dict, str, dict[str, bytes]]:
    """(result.json's record, the text, the extras) of one conversion. key, file: the input it was made for (the
    first one, when several rows share these bytes)."""
    result = {f.name: getattr(conv, f.name) for f in fields(conv) if f.name not in _NOT_IN_RESULT}
    scores = [float(line["score"]) for line in conv.ocr if "score" in line]
    result.update(version=PIPELINE_VERSION, kind=route.kind, pdf_offset=route.pdf_offset, page_kind=route.page_kind,
                  chars=text_chars(conv.text), ocr_lines=len(conv.ocr),
                  ocr_mean_score=round(sum(scores) / len(scores), 4) if scores else None,
                  ocr_low_lines=sum(score < LOW_SCORE for score in scores), seconds=round(seconds, 6), tools=tools,
                  first_key=key, first_file=file)
    extras = {}
    if conv.doc_json is not None:
        data = json.dumps(conv.doc_json, ensure_ascii=False, default=str).encode("utf-8")
        extras["docling.json.gz"] = gzip.compress(data, compresslevel=6)
    if conv.ocr:
        extras["ocr.json"] = json.dumps(conv.ocr, ensure_ascii=False).encode("utf-8")
    if conv.docling_ocr:
        extras["docling_ocr.json"] = json.dumps({str(n): lines for n, lines in conv.docling_ocr.items()},
                                                ensure_ascii=False).encode("utf-8")
    if conv.full:
        extras[FULL] = conv.full.encode("utf-8")
    return result, conv.text, extras


def store(cache: Cache, sha: str, conv: Converted, route: Route, *, seconds: float, tools: dict, key: str = "",
          file: str = "") -> dict:
    result, text, extras = result_of(conv, route, seconds=seconds, tools=tools, key=key, file=file)
    cache.save(sha, result, text, extras)
    return result


def _fresh(folder: Path) -> Path:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return folder


def convert_and_save(cache: Cache, sha: str, path: Path, route: Route, tools: Tools, workdir: Path, *,
                     key: str = "", file: str = "", fmt: str = "") -> dict:
    """begin, convert, store: one file of the run (and one archive member, T5). An exception becomes a failed result
    (f"{type}: {message}"); a SetupError (a tool that cannot load) and an interrupt drop the marker (no crash: the
    file is converted on the next run) and go on up. workdir is made fresh and removed afterwards. Returns the stored
    record. An archive's seconds include its members' conversions."""
    workdir = _fresh(Path(workdir))
    cache.begin(sha)
    started = time.monotonic()
    try:
        conv = convert_file(path, route, tools, workdir, cache=cache, fmt=fmt, key=key, file=file)
    except SetupError:
        cache.abandon(sha)
        raise
    except Exception as e:  # noqa: BLE001 - one file's converter error is that file's failure; the run goes on
        conv = failed(route.name, f"{type(e).__name__}: {e}")
    except BaseException:
        cache.abandon(sha)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return store(cache, sha, conv, route, seconds=time.monotonic() - started, tools=tools.describe(), key=key,
                 file=file)
