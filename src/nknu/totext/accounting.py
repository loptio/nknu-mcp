"""Per-file accounting of the to-text run (plan 2026-11-02 T3b; no silent gaps): every exported file, and every
member of an exported archive (T5), ends in exactly one status with a reason, read back from the cache: ok, partial,
empty, failed, skipped, or pending before its run. A file that crashed the process, or that the watchdog ended, is
failed with the marker's reason; a file missing from the export is failed too. Cache entries are found by the
exported file's own sha256, never the manifest's: that one is the archived original's, which differs for every page
the export re-saved as UTF-8 with a BOM. An archive's members follow its own record, keyed "<序号>!<member path>":
each takes its status from its own cache entry (by the member's sha256), or, when it never reached a converter
(not extracted, an archive inside the archive), from the archive's list of members. A page read by an older page rule
is pending (Cache.done, plan 2026-11-08 T2), and rows of one content share its result only when they are read alike
(made_otherwise: the page's reading, its kind aside).

write_report writes accounting.jsonl (one record per line), summary.json and summary.md (Simplified Chinese, for the
owner). Only `nknu.totext report` calls it, so two runs sharing one --out never overwrite each other's report."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from nknu.totext.cache import Cache, sha256_file, write_atomic
from nknu.totext.ocr import FEW_CHARS, LOW_SCORE, SHORT_LINE_CHARS
from nknu.totext.pagetext import page_route_kind, reading_of, reading_words
from nknu.totext.route import ROUTES, Route, route_for

STATUSES = ("ok", "partial", "empty", "failed", "skipped", "pending")
PENDING_REASON = "还没转"
CRASH_REASON = "上次转到一半程序崩溃"
RETRY_HINT = "，跳过（--retry-crashed 可重试）"
MEMBER_CRASH_HINT = "，重转压缩档时不再试这个成员"  # retrying the archive must not loop on the member that crashed it
MEMBER_GONE = "缓存里没有这个成员的结果（删掉压缩档的 result.json 再跑一次可重转）"
TIMED = ("ok", "partial", "empty", "failed")  # statuses whose seconds measure a conversion
OFFICE_ROUTES = ("office", "office_old")  # Docling reads their pictures as pictures: no OCR
TOP_REASONS, TOP_FILES = 20, 10
ROUTE_WORDS = {"page": "网页", "pdf": "PDF", "office": "docx/xlsx/pptx", "office_old": "doc/xls/ppt/odt/ods/odp/rtf",
               "image": "图片", "archive": "压缩档", "data": "结构化资料", "unsupported": "认不出的格式",
               "": "（没有档）"}
STATUS_WORDS = {"ok": "转好", "partial": "转了一部分", "empty": "没有文字", "failed": "失败", "skipped": "跳过",
                "pending": "还没转"}
# the OCR paths of a PDF page (addendum A): Account field, words for the owner
OCR_PATHS = (("docling_ocr", "docling_ocr_pages", "Docling 区域 OCR 出了字（图片、没有文字层的区块，含下一行）"),
             ("docling_broken", "broken_pages", "其中 Docling 判定文字层坏掉、整页重认"),
             ("few_chars", "few_chars_pages", f"整页 OCR：没有可用的文字层（不到 {FEW_CHARS} 个字）"),
             ("misread", "misread_pages", "整页 OCR：文字层有认不得的字"))


@dataclass(frozen=True)
class Input:
    row: int  # 序号
    type: str  # 类型: 网页, 附件, 图片 or 资料
    file: str  # 本地档案, relative to the export folder
    url: str  # 网址
    final_url: str  # 最后落地网址
    fmt: str  # 格式, the export's sniff
    title: str  # 标题
    linktext: str  # 链接文字
    linked: str  # 链到它的网页
    # no sha256: the manifest's is the archived original's, which differs for every page re-saved with a BOM

    @property
    def key(self) -> str:
        return str(self.row)


@dataclass
class Account:
    key: str  # 序号; an archive member: "<序号>!<its path in the archive>" ("#<index>" after a path that repeats)
    row: int  # an archive member: its archive's
    type: str
    file: str  # an archive member: its archive's
    member: str  # the path inside the archive (T5); '' for an exported file
    sha256: str  # sha256_file of the exported file (or member), the cache key; '' when there is no such file
    route: str
    status: str
    reason: str
    chars: int = 0  # non-space characters of the text, [[img:…]] lines aside
    pages: int = 0
    ocr_pages: int = 0  # PDF pages our whole-page OCR read
    few_chars_pages: int = 0  # among them, pages without a usable text layer
    misread_pages: int = 0  # among them, pages whose text layer holds characters the recogniser does not know
    docling_ocr_pages: int = 0  # PDF pages Docling's OCR put text on (layout regions, or a broken layer re-read)
    broken_pages: int = 0  # among them, pages Docling re-read whole for a broken text layer
    ocr_mean_score: float | None = None
    ocr_low_lines: int = 0
    ocr_short_lines: int = 0  # OCR'd lines (ours and Docling's region OCR) of at most ocr.SHORT_LINE_CHARS characters
    simplified_only: int = 0
    pictures: int = 0  # pictures Docling found; in a docx, xlsx or pptx (and an old Office file) nobody OCRs them
    fixes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if not self.reason and self.status != "ok":
            raise ValueError(f"a {self.status} record needs a reason")


def inputs(export_dir: Path) -> list[Input]:
    """Every manifest.json row with a 本地档案, in manifest order."""
    manifest = json.loads((Path(export_dir) / "manifest.json").read_text(encoding="utf-8"))
    return [Input(row=int(r["序号"]), type=r.get("类型") or "", file=r["本地档案"], url=r.get("网址") or "",
                  final_url=r.get("最后落地网址") or "", fmt=r.get("格式") or "", title=r.get("标题") or "",
                  linktext=r.get("链接文字") or "", linked=r.get("链到它的网页") or "")
            for r in manifest.get("项目", []) if r.get("本地档案")]


def locate(export_dir: Path, inp: Input) -> tuple[Path | None, str]:
    """(the exported file, '') or (None, why not): missing, or its path leads out of the export folder."""
    root = Path(export_dir).resolve()
    path = (root / inp.file).resolve()
    if root not in path.parents:
        return None, f"档案路径跑出导出资料夹（{inp.file}）"
    if not path.is_file():
        return None, f"导出资料夹里没有这个档（{inp.file}）"
    return path, ""


def input_route(inp: Input, path: Path) -> Route:
    """route_for, and for a page how it is read (plan 2026-11-08 T2): url where it was read (最后落地网址, else 网址:
    assemble reads its links there too, so text and zones agree 【Claude】), page_kind pagetext.page_route_kind (its
    kind by its 网址 alone, a label no result depends on: assemble adds the list grid the page shows, T2 review; the
    reading of where it was read; PAGE_VERSION)."""
    route = route_for(inp.type, path)
    if route.name != "page":
        return route
    where = inp.final_url or inp.url
    return replace(route, page_kind=page_route_kind(inp.url, where), url=where)


def crash_reason(cache: Cache, sha: str) -> str:
    return (cache.crash_note(sha) or CRASH_REASON) + RETRY_HINT


def member_crash_reason(cache: Cache, sha: str) -> str:
    return (cache.crash_note(sha) or CRASH_REASON) + MEMBER_CRASH_HINT


def join_notes(*parts: str) -> str:
    return "；".join(part for part in parts if part)


def _variant(name: str, kind: str, pdf_offset: int, page_kind: str) -> tuple:
    """What a conversion depends on besides the bytes: the route, and for a page how it is read with the page rule's
    version (pagetext.reading_of; its kind aside, so one page under two addresses read alike shares a result: plan
    2026-11-08 T2)."""
    return name, kind, pdf_offset, reading_of(page_kind)


def _how(variant: tuple) -> str:
    name, kind, _offset, reading = variant
    return (f"{name}/{kind}" if kind else name) + (f"，{reading_words(reading)}" if reading else "")


def made_otherwise(result: dict, route: Route) -> str:
    """'' when result was made reading its bytes the way route says; else why it does not count here: there is one
    cache entry per content, and another row (or archive member) read the same bytes another way."""
    made = _variant(result.get("route") or "", result.get("kind") or "", result.get("pdf_offset") or 0,
                    result.get("page_kind") or "")
    wanted = _variant(route.name, route.kind, route.pdf_offset, route.page_kind)
    if made == wanted:
        return ""
    return (f"和第 {result.get('first_key') or '?'} 行是同一个档，那一行按「{_how(made)}」转过；"
            f"这一行要按「{_how(wanted)}」读，没有另外转")


def _from_result(base: dict, sha: str, route: Route, result: dict) -> Account:
    if other := made_otherwise(result, route):
        return Account(**base, sha256=sha, route=route.name, status="skipped", reason=other)
    ocr_pages = [p for p in result.get("ocr_pages") or [] if isinstance(p, dict)]
    why = Counter(p.get("why") for p in ocr_pages)
    try:
        return Account(**base, sha256=sha, route=result.get("route") or route.name, status=result.get("status") or "",
                       reason=result.get("reason") or "", chars=int(result.get("chars") or 0),
                       pages=int(result.get("pages") or 0), ocr_pages=len(ocr_pages),
                       few_chars_pages=why["few_chars"], misread_pages=why["misread"],
                       docling_ocr_pages=len(result.get("docling_ocr_pages") or []),
                       broken_pages=len(result.get("broken_pages") or []),
                       ocr_mean_score=result.get("ocr_mean_score"), ocr_low_lines=int(result.get("ocr_low_lines") or 0),
                       ocr_short_lines=int(result.get("ocr_short_lines") or 0),
                       simplified_only=int(result.get("simplified_only") or 0),
                       pictures=int(result.get("pictures") or 0), fixes=list(result.get("fixes") or []),
                       seconds=float(result.get("seconds") or 0.0))
    except (TypeError, ValueError) as e:
        return Account(**base, sha256=sha, route=route.name, status="failed", reason=f"缓存里的结果不完整（{e}）")


def account_input(export_dir: Path, inp: Input, cache: Cache) -> Account:
    base = {"key": inp.key, "row": inp.row, "type": inp.type, "file": inp.file, "member": ""}
    path, why = locate(export_dir, inp)
    if path is None:
        return Account(**base, sha256="", route="", status="failed", reason=why)
    sha = sha256_file(path)
    route = input_route(inp, path)
    result = cache.done(sha)
    if result is not None:
        return _from_result(base, sha, route, result)
    if cache.crashed(sha):
        return Account(**base, sha256=sha, route=route.name, status="failed", reason=crash_reason(cache, sha))
    return Account(**base, sha256=sha, route=route.name, status="pending", reason=PENDING_REASON)


def _member_account(container: Account, entry: dict, key: str, cache: Cache) -> Account:
    base = {"key": key, "row": container.row, "type": container.type, "file": container.file,
            "member": str(entry.get("path") or "")}
    sha, route, note, kind = (str(entry.get(name) or "") for name in ("sha256", "route", "note", "kind"))
    if sha and route and route != "archive":  # it reached a converter
        result = cache.done(sha)
        if result is not None:
            offset = entry.get("pdf_offset")
            wanted = Route(route, kind, offset if isinstance(offset, int) else 0)
            made = _from_result(base, sha, wanted, result)
            return replace(made, reason=join_notes(made.reason, note))
        reason = member_crash_reason(cache, sha) if cache.crashed(sha) else MEMBER_GONE
        return Account(**base, sha256=sha, route=route, status="failed", reason=join_notes(reason, note))
    try:  # not extracted, or an archive inside the archive: the archive's own word on it
        return Account(**base, sha256=sha, route=route, status=str(entry.get("status") or ""),
                       reason=str(entry.get("reason") or ""))
    except ValueError as e:
        return Account(**base, sha256=sha, route=route, status="failed", reason=f"缓存里的结果不完整（{e}）")


def member_accounts(container: Account, cache: Cache) -> list[Account]:
    """One record per member of an archive whose extraction has a result, in its order; [] for anything else. A
    path that repeats in one archive gets "#<index>" after its key."""
    if container.route != "archive" or not container.sha256:
        return []
    result = cache.done(container.sha256)
    if result is None or result.get("route") != "archive":
        return []
    out, seen = [], set()
    for entry in result.get("members") or []:
        if not isinstance(entry, dict):
            continue
        key = f"{container.key}!{entry.get('path') or ''}"
        if key in seen:
            key += f"#{entry.get('index')}"
        seen.add(key)
        out.append(_member_account(container, entry, key, cache))
    return out


def account(export_dir: Path, cache: Cache) -> list[Account]:
    """One record per input, each archive's members right after it, in manifest order."""
    export_dir = Path(export_dir)
    out = []
    for inp in inputs(export_dir):
        container = account_input(export_dir, inp, cache)
        out.append(container)
        out += member_accounts(container, cache)
    return out


def _ordered(routes) -> list[str]:
    rank = {name: i for i, name in enumerate(ROUTES)}
    return sorted(set(routes), key=lambda r: (rank.get(r, len(rank)), r))


def _unique(accounts: list[Account]) -> list[Account]:
    """One record per content hash (the first): identical files convert once, so they are timed and counted once."""
    seen: set[str] = set()
    out = []
    for a in accounts:
        if a.sha256 and a.sha256 not in seen:
            seen.add(a.sha256)
            out.append(a)
    return out


def _brief(a: Account) -> dict:
    return {"key": a.key, "file": a.file, "member": a.member, "route": a.route}


def _projection(unique: list[Account], timing: dict, totals: dict) -> dict:
    """The seconds a whole run takes, and the pending part, from this cache's timings: PDFs per page, other routes per
    file. A route whose results are all skipped costs nothing; a route with no result has no sample."""
    files, left = totals.get("files_by_route", {}), totals.get("pending_by_route", {})
    statuses: dict[str, set[str]] = {}
    for a in unique:
        if a.status != "pending":
            statuses.setdefault(a.route, set()).add(a.status)
    rows = []
    for route in _ordered(files):
        t = timing.get(route)
        if route == "pdf" and t and t["per_page"]:
            basis, total, rest = "per_page", t["per_page"] * totals["pdf_pages"], t["per_page"] * totals[
                "pending_pdf_pages"]
        elif t:
            basis, total, rest = "per_file", t["per_file"] * files[route], t["per_file"] * left.get(route, 0)
        elif statuses.get(route) == {"skipped"}:
            basis, total, rest = "skipped", 0.0, 0.0
        else:
            basis, total, rest = "no_sample", None, None
        rows.append({"route": route, "files": files[route], "pending": left.get(route, 0), "basis": basis,
                     "seconds": None if total is None else round(total, 1),
                     "left_seconds": None if rest is None else round(rest, 1)})
    known = [r for r in rows if r["seconds"] is not None]
    return {"routes": rows, "total_seconds": round(sum(r["seconds"] for r in known), 1),
            "left_seconds": round(sum(r["left_seconds"] for r in known), 1),
            "no_sample": [r["route"] for r in rows if r["basis"] == "no_sample"]}


def summarize(accounts: list[Account], *, tools: dict, totals: dict | None = None) -> dict:
    by_status = Counter(a.status for a in accounts)
    by_route_status = {}
    for route in _ordered(a.route for a in accounts):
        counts = Counter(a.status for a in accounts if a.route == route)
        by_route_status[route] = {s: counts[s] for s in STATUSES if counts[s]}
    reasons = Counter((a.status, a.reason) for a in accounts if a.status != "ok")
    unique = _unique(accounts)
    timed = [a for a in unique if a.status in TIMED and a.seconds > 0]
    timing: dict[str, dict] = {}
    for route in _ordered(a.route for a in timed):
        mine = [a for a in timed if a.route == route]
        seconds, pages = sum(a.seconds for a in mine), sum(a.pages for a in mine)
        timing[route] = {"files": len(mine), "pages": pages, "seconds": round(seconds, 3),
                         "per_file": round(seconds / len(mine), 4), "per_page": round(seconds / pages, 4) if pages
                         else None}
    scores = [a.ocr_mean_score for a in unique if a.ocr_mean_score is not None]
    simplified = sorted((a for a in unique if a.simplified_only), key=lambda a: (-a.simplified_only, a.row))
    short = sorted((a for a in unique if a.ocr_short_lines), key=lambda a: (-a.ocr_short_lines, a.row))
    pictured = sorted((a for a in unique if a.pictures and a.route in OFFICE_ROUTES),
                      key=lambda a: (-a.pictures, a.row))
    return {
        "files": len(accounts), "unique_files": len(unique), "members": sum(1 for a in accounts if a.member),
        "pending": by_status["pending"],
        "by_status": {s: by_status[s] for s in STATUSES if by_status[s]},
        "by_route_status": by_route_status,
        "top_reasons": [{"status": s, "reason": r, "files": n} for (s, r), n in reasons.most_common(TOP_REASONS)],
        "pages_images_only": sum(1 for a in accounts if a.route == "page" and a.status == "ok" and a.chars == 0),
        "ocr": {"files": sum(1 for a in unique if a.ocr_pages or a.ocr_mean_score is not None),
                "pages": sum(a.ocr_pages for a in unique),
                "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
                "low_lines": sum(a.ocr_low_lines for a in unique),
                "paths": {name: {"pages": sum(getattr(a, attr) for a in unique),
                                 "files": sum(1 for a in unique if getattr(a, attr))} for name, attr, _ in OCR_PATHS}
                | {"image": {"files": sum(1 for a in unique if a.route == "image" and a.ocr_mean_score is not None)}},
                "short_lines": sum(a.ocr_short_lines for a in short),
                "short_top": [_brief(a) | {"count": a.ocr_short_lines} for a in short[:TOP_FILES]]},
        "simplified_only": {"files": len(simplified),
                            "top": [_brief(a) | {"count": a.simplified_only} for a in simplified[:TOP_REASONS]]},
        "office_pictures": {"files": len(pictured), "pictures": sum(a.pictures for a in pictured),
                            "top": [_brief(a) | {"count": a.pictures} for a in pictured[:TOP_REASONS]]},
        "slowest": [_brief(a) | {"seconds": a.seconds, "pages": a.pages}
                    for a in sorted(timed, key=lambda a: -a.seconds)[:TOP_FILES]],
        "timing": timing,
        "totals": totals,
        "projection": _projection(unique, timing, totals) if totals else None,
        "tools": tools,
    }


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} 小时" if seconds >= 360 else f"{seconds / 60:.1f} 分钟"


def summary_md(s: dict) -> str:
    counts = "、".join(f"{STATUS_WORDS[k]} {v}" for k, v in s["by_status"].items() if k != "pending") or "（没有）"
    members = f"其中压缩档成员 {s['members']} 个（键是「序号!成员路径」）。" if s.get("members") else ""
    out = ["# 转文字记账", "",
           (f"共 {s['files']} 个档（不同内容 {s['unique_files']} 个）：{counts}；还没转 {s['pending']} 个。{members}"
            "每个档一行，在 accounting.jsonl。"), "", "## 路线 × 状态", "",
           "| 路线 | 是什么 | " + " | ".join(STATUSES) + " | 合计 |", "|---|---|" + "---|" * (len(STATUSES) + 1)]
    for route, cell in s["by_route_status"].items():
        out.append(f"| {route or '-'} | {ROUTE_WORDS.get(route, route)} | "
                   + " | ".join(str(cell.get(st, "")) for st in STATUSES) + f" | {sum(cell.values())} |")
    out += ["", f"## 不是 ok 的原因（前 {TOP_REASONS} 种）", ""]
    if s["top_reasons"]:
        out += ["| 状态 | 原因 | 档数 |", "|---|---|---|"]
        out += [f"| {r['status']} | {_cell(r['reason'])} | {r['files']} |" for r in s["top_reasons"]]
    else:
        out.append("全部是 ok。")
    out += ["", "## 网页只有图片", "",
            (f"{s['pages_images_only']} 页网页只有图片标记、没有自己的文字（状态记 ok）：文字要等图片 OCR，"
             "再由 assemble 填进网页。"), "", "## OCR", ""]
    ocr = s["ocr"]
    paths = ocr["paths"]
    if ocr["files"] or any(paths[name]["pages"] for name, _, _ in OCR_PATHS):
        mean = "—" if ocr["mean_score"] is None else f"{ocr['mean_score']:.3f}"
        out += [(f"我们自己 OCR 的档 {ocr['files']} 个（PDF 整页 {ocr['pages']} 页，加上图片档）；"
                 f"各档平均分的平均 {mean}；分数低于 {LOW_SCORE} 的行 {ocr['low_lines']} 行。"
                 "各条 OCR 路径（同样内容的档只算一次）："), "",
                "| 路径 | 页数 | 档数 |", "|---|---|---|"]
        out += [f"| {words} | {paths[name]['pages']} | {paths[name]['files']} |" for name, _, words in OCR_PATHS]
        out.append(f"| 图片档 OCR 认出字 |  | {paths['image']['files']} |")
        out += ["", (f"OCR 读出的极短行（不超过 {SHORT_LINE_CHARS} 个字：多半是图里的标签、印章或杂讯，"
                     f"也有真的两字标题；包括 Docling 区域 OCR 读的）共 {ocr['short_lines']} 行，只计数、不删。")]
        if ocr["short_top"]:
            out += ["", f"极短行最多的 {TOP_FILES} 个档：", "", "| 序号 | 档案 | 路线 | 行数 |", "|---|---|---|---|"]
            out += [f"| {a['key']} | {_cell(a['file'])} | {a['route']} | {a['count']} |" for a in ocr["short_top"]]
    else:
        out.append("这一轮没有做 OCR 的档。")
    pictures = s["office_pictures"]
    out += ["", "## 文件里的图片（没有 OCR）", ""]
    if pictures["files"]:
        out += [(f"{pictures['files']} 个 docx/xlsx/pptx（含 LibreOffice 转过的旧格式）里有图片，"
                 f"共 {pictures['pictures']} 张：图片里的字没有 OCR（只有 PDF 和图片档做 OCR），"
                 "这些档的文字只来自文件本身。"), "",
                "| 序号 | 档案 | 路线 | 图片数 |", "|---|---|---|---|"]
        out += [f"| {a['key']} | {_cell(a['file'])} | {a['route']} | {a['count']} |" for a in pictures["top"]]
    else:
        out.append("没有。")
    out += ["", "## 有只见于简体字形的字的档", ""]
    if s["simplified_only"]["files"]:
        out += [(f"共 {s['simplified_only']['files']} 个（在 OCR 读出的字里找，包括 Docling 区域 OCR 读的；"
                 "只标出来，不改字）："), "", "| 序号 | 档案 | 路线 | 字数 |", "|---|---|---|---|"]
        out += [f"| {a['key']} | {_cell(a['file'])} | {a['route']} | {a['count']} |" for a in s["simplified_only"]["top"]]
    else:
        out.append("没有。")
    out += ["", f"## 最慢的 {TOP_FILES} 个档", ""]
    if s["slowest"]:
        out += ["| 序号 | 档案 | 路线 | 秒 | 页数 |", "|---|---|---|---|---|"]
        out += [f"| {a['key']} | {_cell(a['file'])} | {a['route']} | {a['seconds']:.2f} | {a['pages'] or ''} |"
                for a in s["slowest"]]
    else:
        out.append("还没有转过的档。")
    out += ["", "## 速度（同样内容的档只算一次）", ""]
    if s["timing"]:
        out += ["| 路线 | 档数 | 页数 | 秒 | 每个档（秒） | 每页（秒） |", "|---|---|---|---|---|---|"]
        out += [f"| {route} | {t['files']} | {t['pages'] or ''} | {t['seconds']:.1f} | {t['per_file']:.3f} | "
                f"{'' if t['per_page'] is None else format(t['per_page'], '.3f')} |" for route, t in s["timing"].items()]
    else:
        out.append("还没有转过的档。")
    if s["projection"]:
        p, totals = s["projection"], s["totals"]
        out += ["", "## 推算（report --project）", "",
                (f"全部 PDF {totals['pdf_files']} 个、{totals['pdf_pages']} 页（读不了页数的 {totals['pdf_unreadable']} "
                 f"个）。照这份缓存里的速度：全部转完约 {_hours(p['total_seconds'])}，"
                 f"还没转的约 {_hours(p['left_seconds'])}。"),
                "", "| 路线 | 档数 | 还没转 | 算法 | 全部（秒） | 还没转（秒） |", "|---|---|---|---|---|---|"]
        basis = {"per_page": "每页秒数 × 页数", "per_file": "每个档秒数 × 档数", "skipped": "全部跳过，不花时间",
                 "no_sample": "没有样本，没算进去"}
        out += [f"| {r['route']} | {r['files']} | {r['pending']} | {basis[r['basis']]} | "
                f"{'' if r['seconds'] is None else r['seconds']} | "
                f"{'' if r['left_seconds'] is None else r['left_seconds']} |" for r in p["routes"]]
        if p["no_sample"]:
            out += ["", f"没有样本的路线：{'、'.join(p['no_sample'])}（这些没算进上面的时间）。"]
    out += ["", "## 工具版本（写报告时）", ""]
    for name, value in s["tools"].items():
        if isinstance(value, dict):
            value = "、".join(f"{k} {v or '?'}" for k, v in value.items()) or "（没有）"
        out.append(f"- {name}：{value or '（不知道）'}")
    return "\n".join(out) + "\n"


def write_report(out: Path, accounts: list[Account], *, tools: dict, totals: dict | None = None) -> dict:
    """accounting.jsonl, summary.json and summary.md in out; returns the summary."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(accounts, tools=tools, totals=totals)
    lines = "".join(json.dumps(asdict(a), ensure_ascii=False) + "\n" for a in accounts)
    write_atomic(out / "accounting.jsonl", lines.encode("utf-8"))
    write_atomic(out / "summary.json", (json.dumps(summary, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    write_atomic(out / "summary.md", summary_md(summary).encode("utf-8"))
    return summary


def _pdf_pages(path: Path) -> int | None:
    import pypdfium2 as pdfium

    try:
        offset = route_for("附件", path).pdf_offset
        doc = pdfium.PdfDocument(path.read_bytes()[offset:] if offset else path)
    except (pdfium.PdfiumError, OSError, ValueError):
        return None
    try:
        return len(doc)
    finally:
        doc.close()


def project_totals(export_dir: Path, accounts: list[Account]) -> dict:
    """For report --project: the files per route (one per content hash; archive members aside) and the PDF pages,
    counted with pypdfium2, in all and among the files still pending."""
    unique = _unique([a for a in accounts if not a.member])  # members out first: one may share a later file's bytes
    files = Counter(a.route for a in unique)
    pending = Counter(a.route for a in unique if a.status == "pending")
    pages = pending_pages = unreadable = 0
    for a in unique:
        if a.route != "pdf":
            continue
        n = _pdf_pages(Path(export_dir) / a.file)
        if n is None:
            unreadable += 1
            continue
        pages += n
        pending_pages += n if a.status == "pending" else 0
    return {"files_by_route": {r: files[r] for r in _ordered(files)},
            "pending_by_route": {r: pending[r] for r in _ordered(pending)}, "pdf_files": files["pdf"],
            "pdf_pages": pages, "pending_pdf_pages": pending_pages, "pdf_unreadable": unreadable}
