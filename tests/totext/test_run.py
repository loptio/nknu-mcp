"""The to-text run, its accounting and its report (plan 2026-11-02 T3b, T4) on a small export of real files, with a
fake Docling and a fake OCR engine: no model loads, nothing reaches the network. mini_export, FakeDocling and
fake_tools are shared with test_convert and test_ocr."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from nknu.totext import run as run_module
from nknu.totext.accounting import Account, account, inputs
from nknu.totext.cache import FULL, PIPELINE_VERSION, Cache, sha256_file
from nknu.totext.convert import Tools
from nknu.totext.docling_convert import DoclingOut
from nknu.totext.ocr import OcrLine
from nknu.totext.pagetext import PAGE_VERSION
from nknu.totext.run import SetupError, Watchdog, main, run
from tests.totext.fixtures import FakeEngine, docx_with_table, png, refuse_real_models, tiny_pdf

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE = "https://c.nknu.edu.tw/aca/"
PAGE_URL = SITE + "newsdetail.aspx?nid=1"
PAGE_HTML = '﻿<html><body><div id="lbl_Contents"><p>選課公告</p><p>一、選課期間</p></div></body></html>'.encode()
PDF_TEXT = "Hello course rules " * 3  # 57 characters a page: T4's rules flag no page
CRASHED = "上次转到一半程序崩溃，跳过（--retry-crashed 可重试）"
OCR_LINE = OcrLine("圖片文字", 0.9, (0.0, 0.0, 10.0, 10.0))  # what fake_tools' engine reads in any image


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, request):
    refuse_real_models(monkeypatch, request)


class FakeDocling:
    """Returns the tiny PDF's two pages for any file (or raises for the kinds in `raises`); records each call as
    (path, kind, the first bytes of the file it was given). ocr_pages, broken_pages and ocr_text: what Docling's OCR
    says it did, as the OCR-on converter reports it; pictures: the pictures it found."""

    def __init__(self, pages=("page one", "page two"), raises: dict[str, BaseException] | None = None,
                 status: str = "ok", reason: str = "", ocr_pages: tuple[int, ...] = (),
                 broken_pages: tuple[int, ...] = (), ocr_text: dict[int, list[str]] | None = None, pictures: int = 0):
        self.pages, self.raises, self.status, self.reason = list(pages), raises or {}, status, reason
        self.ocr_pages, self.broken_pages = tuple(ocr_pages), tuple(broken_pages)
        self.ocr_text, self.pictures = dict(ocr_text or {}), pictures
        self.calls: list[tuple[Path, str, bytes]] = []

    def convert(self, path, kind):
        self.calls.append((Path(path), kind, Path(path).read_bytes()[:8]))
        if kind in self.raises:
            raise self.raises[kind]
        doc_json = {"k": 1} if self.status != "failed" else None
        return DoclingOut(self.status, self.reason, list(self.pages), doc_json, self.pictures, self.ocr_pages,
                          self.broken_pages, dict(self.ocr_text))

    def kinds(self) -> list[str]:
        return [kind for _path, kind, _head in self.calls]


def fake_tools(docling=None, *, docling_ocr=None, ocr=None, beat=None) -> Tools:
    """Tools made of fakes: the OCR-on converter is the docling fake unless given (one fake then sees every file
    Docling reads: the Office files and every PDF but an all-scanned one, which only the engine reads), and the
    engine reads OCR_LINE in any image."""
    docling = docling if docling is not None else FakeDocling()
    return Tools(docling=docling, docling_ocr=docling_ocr if docling_ocr is not None else docling,
                 ocr=ocr if ocr is not None else FakeEngine([OCR_LINE]), beat=beat)


def write_export(root: Path, items: list[dict]) -> Path:
    """An export folder: manifest.json with one row per item (序号 from 1); an item's bytes go to its 本地档案."""
    rows = []
    for n, item in enumerate(items, start=1):
        rel = item.get("file", "")
        if rel and item.get("bytes") is not None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(item["bytes"])
        rows.append({"序号": str(n), "类型": item["type"], "网址": item.get("url", ""), "最后落地网址": "",
                     "本地档案": rel, "标题": item.get("title", ""), "格式": item.get("fmt", ""),
                     "链到它的网页": item.get("linked", ""), "链接文字": item.get("linktext", "")})
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"网站": "c.nknu.edu.tw/aca", "名称": "测试站", "导出时间": "2026-11-02T10:00:00+08:00", "项目": rows}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return root


def _jpeg(path: Path, size: tuple[int, int]) -> bytes:
    from PIL import Image

    Image.new("RGB", size, "white").save(path, "JPEG")
    return path.read_bytes()


def mini_export(tmp_path: Path) -> Path:
    """Eight rows, every file real: a page, a two-page PDF twice (the same bytes), a docx, a JPEG, calendar data, an
    unknown format, and an attachment with no file."""
    made = tmp_path / "made"
    made.mkdir(parents=True, exist_ok=True)
    pdf = tiny_pdf(made / "t.pdf", [PDF_TEXT, PDF_TEXT]).read_bytes()
    docx = docx_with_table(made / "t.docx").read_bytes()
    return write_export(tmp_path / "export", [
        {"type": "网页", "url": PAGE_URL, "file": "pages/0001-a.html", "bytes": PAGE_HTML, "fmt": "html",
         "title": "選課公告"},
        {"type": "附件", "url": SITE + "uploadfile/a.pdf", "file": "files/0001-a.pdf", "bytes": pdf, "fmt": "pdf",
         "linked": PAGE_URL, "linktext": "選課須知"},
        {"type": "附件", "url": SITE + "uploadfile/b.pdf", "file": "files/0002-a.pdf", "bytes": pdf, "fmt": "pdf"},
        {"type": "附件", "url": SITE + "uploadfile/c.docx", "file": "files/0003-c.docx", "bytes": docx,
         "fmt": "ooxml:docx"},
        {"type": "图片", "url": SITE + "uploadfile/x.jpg", "file": "images/0001-x.jpg",
         "bytes": _jpeg(made / "x.jpg", (200, 100)), "fmt": "jpeg"},
        {"type": "资料", "url": "https://www.googleapis.com/calendar/v3/x", "file": "data/0001-cal.json",
         "bytes": b'{"items": []}', "fmt": "json"},
        {"type": "附件", "url": SITE + "uploadfile/d.bin", "file": "files/0004-d.bin",
         "bytes": b"\x00\x01 no known format", "fmt": "other"},
        {"type": "附件", "url": SITE + "uploadfile/e.pdf", "file": ""},
    ])


def _accounts(export: Path, out: Path) -> dict[str, Account]:
    return {a.key: a for a in account(export, Cache(out / "cache"))}


def _sha(export: Path, rel: str) -> str:
    return sha256_file(export / rel)


def test_inputs_are_the_rows_with_a_file(tmp_path):
    export = mini_export(tmp_path)
    got = inputs(export)
    assert [i.key for i in got] == ["1", "2", "3", "4", "5", "6", "7"]
    first, second = got[0], got[1]
    assert (first.row, first.type, first.file, first.url, first.fmt, first.title) == (
        1, "网页", "pages/0001-a.html", PAGE_URL, "html", "選課公告")
    assert (second.linked, second.linktext, second.final_url) == (PAGE_URL, "選課須知", "")


def test_one_account_per_input_file(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, tools=fake_tools())
    accounts = account(export, Cache(out / "cache"))
    assert [a.key for a in accounts] == ["1", "2", "3", "4", "5", "6", "7"]  # row 8 has no file
    assert {a.key: (a.route, a.status) for a in accounts} == {
        "1": ("page", "ok"), "2": ("pdf", "ok"), "3": ("pdf", "ok"), "4": ("office", "ok"), "5": ("image", "ok"),
        "6": ("data", "skipped"), "7": ("unsupported", "skipped")}
    by_key = {a.key: a for a in accounts}
    # T4 wired the image route: the JPEG is OCR'd (the fake engine reads OCR_LINE)
    assert (by_key["5"].reason, by_key["5"].chars) == ("", len(OCR_LINE.text))
    assert by_key["6"].reason == "结构化资料：行事历由建库直接读"
    assert by_key["7"].reason == "认不出的档案格式（other）"
    pdf = by_key["2"]
    assert (pdf.sha256, pdf.pages, pdf.chars, pdf.member, pdf.fixes) == (_sha(export, "files/0001-a.pdf"), 2, 14, "",
                                                                         [])
    assert pdf.seconds > 0 and by_key["1"].chars == len("選課公告一、選課期間")


def test_identical_files_convert_once(tmp_path):
    export, out, fake = mini_export(tmp_path), tmp_path / "out", FakeDocling()
    summary = run(export, out, tools=fake_tools(fake))
    assert fake.kinds().count("pdf") == 1 and fake.kinds().count("docx") == 1
    assert summary["converted"] == 6 and summary["cached"] == 1  # row 3 found row 2's result
    accounts = _accounts(export, out)
    assert accounts["2"].sha256 == accounts["3"].sha256 and accounts["3"].status == "ok"


def test_resume_converts_nothing_new(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, tools=fake_tools())
    again = FakeDocling()
    summary = run(export, out, tools=fake_tools(again))
    assert again.calls == [] and summary["converted"] == 0 and summary["cached"] == 7


def test_crash_marker_is_failed_not_retried(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    Cache(out / "cache").begin(_sha(export, "files/0001-a.pdf"))  # a crash on row 2: a marker and no result
    fake = FakeDocling()
    summary = run(export, out, tools=fake_tools(fake))
    assert "pdf" not in fake.kinds() and summary["crashed"] == 2  # rows 2 and 3: the same bytes
    accounts = _accounts(export, out)
    assert (accounts["2"].status, accounts["2"].reason) == ("failed", CRASHED)
    assert (accounts["3"].status, accounts["3"].reason) == ("failed", CRASHED)
    assert accounts["4"].status == "ok"
    run(export, out, retry_crashed=True, tools=fake_tools(fake))
    assert fake.kinds().count("pdf") == 1 and _accounts(export, out)["2"].status == "ok"


def test_retry_crashed_before_tries_only_a_crash_older_than_the_lane(tmp_path, monkeypatch):
    # review of plan 2026-11-08 T5: lanes.sh's RETRY_FIRST gave every export's first run --retry-crashed, and the cache
    # is shared, so a file several exports hold that really crashes (or hangs for HANG_S) was tried once per export.
    # --retry-crashed-before T tries a file only when its marker is older than T (the lane's start): one that crashes
    # again in the lane gets a newer marker, and every later run skips it
    export, out = mini_export(tmp_path), tmp_path / "out"
    cache = Cache(out / "cache")
    sha = _sha(export, "files/0001-a.pdf")  # rows 2 and 3: the same bytes
    cache.begin(sha)
    os.utime(cache.dir(sha) / "started", (1_000_000.0, 1_000_000.0))  # the crash of an earlier lane start
    fake = FakeDocling()
    summary = run(export, out, retry_crashed_before=999_999.5, tools=fake_tools(fake))  # crashed after T: skipped
    assert "pdf" not in fake.kinds() and summary["crashed"] == 2
    summary = run(export, out, retry_crashed_before=1_000_000.5, tools=fake_tools(fake))
    assert fake.kinds().count("pdf") == 1 and summary["crashed"] == 0 and _accounts(export, out)["2"].status == "ok"
    seen = {}
    monkeypatch.setattr(run_module, "run", lambda *a, **k: seen.update(k) or {})
    args = ["run", "--export", str(export), "--out", str(tmp_path / "o2")]
    assert main([*args, "--retry-crashed-before", "1762560000,25"]) == 0  # bash's EPOCHREALTIME in a comma locale
    assert seen["retry_crashed_before"] == 1762560000.25 and seen["retry_crashed"] is False
    assert main(args) == 0 and seen["retry_crashed_before"] is None
    for bad in ("soon", "nan", "inf"):
        with pytest.raises(SystemExit) as stop:
            main([*args, "--retry-crashed-before", bad])
        assert stop.value.code == 2


def test_hang_ends_the_process_and_is_failed_on_rerun(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    code = ("import sys, time\n"
            "from pathlib import Path\n"
            "import nknu.totext.run as r\n"
            "from nknu.totext.convert import Tools\n"
            "class Sleepy:\n"
            "    def convert(self, path, kind):\n"
            "        time.sleep(30)\n"
            "class Blind:\n"
            "    def read(self, image):\n"
            "        return []\n"
            "r.HANG_S = 1\n"
            "r.run(Path(sys.argv[1]), Path(sys.argv[2]),\n"
            "      tools=Tools(docling=Sleepy(), docling_ocr=Sleepy(), ocr=Blind()))\n")
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", code, str(export), str(out)], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=60, check=False)
    assert proc.returncode == 3, proc.stderr
    assert time.monotonic() - started < 15
    assert "看门狗：files/0001-a.pdf 超过 1 秒没有进展，结束程序" in proc.stderr
    fake = FakeDocling()
    run(export, out, tools=fake_tools(fake))
    accounts = _accounts(export, out)
    reason = "转换超过 1 秒没有进展，看门狗结束了程序，跳过（--retry-crashed 可重试）"
    assert (accounts["2"].status, accounts["2"].reason) == ("failed", reason)
    assert accounts["1"].status == accounts["4"].status == "ok" and "pdf" not in fake.kinds()


def test_converter_exception_is_failed_and_the_run_goes_on(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, tools=fake_tools(FakeDocling(raises={"docx": RuntimeError("boom")})))
    accounts = _accounts(export, out)
    assert accounts["4"].status == "failed" and "RuntimeError: boom" in accounts["4"].reason
    assert accounts["2"].status == accounts["3"].status == accounts["5"].status == "ok"
    assert accounts["7"].status == "skipped"


def test_interrupt_leaves_the_file_pending(tmp_path):
    # Ctrl-C is no crash: the file in hand keeps no marker and is converted on the next run; its workdir is gone
    export, out = mini_export(tmp_path), tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        run(export, out, tools=fake_tools(FakeDocling(raises={"pdf": KeyboardInterrupt()})))
    accounts = _accounts(export, out)
    assert accounts["1"].status == "ok" and accounts["2"].status == "pending" and accounts["2"].reason == "还没转"
    assert list((out / "work").iterdir()) == []


def test_static_page_keeps_its_image(tmp_path):
    url = SITE + "uploadfile/userfiles/files/spaper/1.htm"
    export = write_export(tmp_path / "export", [
        {"type": "网页", "url": url, "file": "pages/0001-s.html",
         "bytes": b"<html><body><img src='p1.jpg'></body></html>"}])
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    sha = _sha(export, "pages/0001-s.html")
    cache = Cache(out / "cache")
    assert cache.text(sha) == "[[img:p1.jpg]]"
    result = cache.done(sha)
    # plan 2026-11-08 T2: a page's result names how it was read and the page rule's version after its kind
    assert result["page_kind"] == f"静态:spaper|cnknu:whole|{PAGE_VERSION}"
    assert result["page"] == {"title": "", "album": "", "images": ["p1.jpg"], "links": []}
    assert cache.extra(sha, FULL) == "[[img:p1.jpg]]"  # the whole reading beside it (blueprint item 22)
    [only] = account(export, cache)
    assert (only.status, only.chars) == ("ok", 0)  # its text is the image's, after OCR (T4) and assemble (T7a)


def test_routes_filter_and_pending(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    fake = FakeDocling()
    summary = run(export, out, routes={"page"}, tools=fake_tools(fake))
    assert summary["selected"] == ["1"] and fake.calls == []
    assert main(["report", "--export", str(export), "--out", str(out)]) == 1
    accounts = _accounts(export, out)
    assert accounts["1"].status == "ok"
    assert all((a.status, a.reason) == ("pending", "还没转") for k, a in accounts.items() if k != "1")
    assert accounts["7"].route == "unsupported" and accounts["5"].route == "image"  # routes known before the run


def test_unknown_route_is_refused(tmp_path):
    export = mini_export(tmp_path)
    with pytest.raises(ValueError, match="pdfs"):
        run(export, tmp_path / "out", routes={"pdfs"}, tools=fake_tools())


def test_reason_required_unless_ok():
    with pytest.raises(ValueError):
        Account(key="1", row=1, type="附件", file="f", member="", sha256="x", route="pdf", status="failed", reason="")
    with pytest.raises(ValueError):
        Account(key="1", row=1, type="附件", file="f", member="", sha256="x", route="pdf", status="done", reason="r")
    Account(key="1", row=1, type="附件", file="f", member="", sha256="x", route="pdf", status="ok", reason="")


def test_report_files(tmp_path, capsys):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, tools=fake_tools(FakeDocling(raises={"docx": RuntimeError("boom")})))
    assert not (out / "accounting.jsonl").exists() and not (out / "summary.json").exists()
    assert main(["report", "--export", str(export), "--out", str(out)]) == 0
    lines = (out / "accounting.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7 and json.loads(lines[1])["key"] == "2"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["by_route_status"]["pdf"]["ok"] == 2 and summary["by_route_status"]["office"]["failed"] == 1
    assert summary["pending"] == 0 and summary["files"] == 7 and summary["unique_files"] == 6
    assert summary["timing"]["pdf"]["pages"] == 2  # the shared PDF counts once
    assert any("RuntimeError: boom" in r["reason"] for r in summary["top_reasons"])
    assert summary["tools"]["pipeline"] == PIPELINE_VERSION and summary["totals"] is None  # was "totext-1" (bumped)
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert md.startswith("# 转文字记账") and "RuntimeError: boom" in md and "| pdf |" in md
    assert "7 个档" in capsys.readouterr().out


def test_report_project_counts_pdf_pages(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, routes={"page"}, tools=fake_tools())
    assert main(["report", "--export", str(export), "--out", str(out), "--project"]) == 1  # still pending
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    totals = summary["totals"]
    assert (totals["pdf_files"], totals["pdf_pages"], totals["pending_pdf_pages"], totals["pdf_unreadable"]) == (
        1, 2, 2, 0)
    assert totals["files_by_route"] == {"page": 1, "pdf": 1, "office": 1, "image": 1, "data": 1, "unsupported": 1}
    projection = {r["route"]: r for r in summary["projection"]["routes"]}
    assert projection["page"]["basis"] == "per_file" and projection["pdf"]["basis"] == "no_sample"
    assert "推算" in (out / "summary.md").read_text(encoding="utf-8")


def test_out_inside_the_export_or_archive_is_refused(tmp_path):
    export = mini_export(tmp_path)
    for out in (export / "x", REPO_ROOT / "archive" / "x", REPO_ROOT / "archive-export" / "y",
                REPO_ROOT / "research" / "skeleton_2026-11-01" / "x", REPO_ROOT / "data" / "x"):
        with pytest.raises(ValueError):
            run(export, out, tools=fake_tools())
        assert not out.exists()
    assert main(["run", "--export", str(export), "--out", str(export / "x"), "--routes", "page"]) == 2


def test_sample_is_seeded_and_covers_each_route(tmp_path):
    export = mini_export(tmp_path)
    first = run(export, tmp_path / "o1", sample=4, seed=1, tools=fake_tools())
    second = run(export, tmp_path / "o2", sample=4, seed=1, tools=fake_tools())
    assert first["selected"] == second["selected"]
    one, pdf, *rest = first["selected"]
    assert (one, rest) == ("1", ["4", "5"]) and pdf in ("2", "3")  # page, pdf, office, image
    accounts = _accounts(export, tmp_path / "o1")
    assert accounts["6"].status == accounts["7"].status == "pending"  # data and unsupported are never sampled


def test_deferred_urls_go_last_in_export_order(tmp_path, monkeypatch):
    # main session 11-02 22:55: on the memory-starved Mac the full run takes about a day; journals and past exam
    # papers go last so the documents students ask about are done first
    export = mini_export(tmp_path)
    defer = tmp_path / "defer.txt"
    defer.write_text(f"# journals\n{SITE}uploadfile/a.pdf\n\n{SITE}uploadfile/c.docx\n", encoding="utf-8")
    summary = run(export, tmp_path / "out", defer=run_module.read_defer(defer), tools=fake_tools())
    assert summary["selected"] == ["1", "3", "5", "6", "7", "2", "4"]
    summary = run(export, tmp_path / "o1", routes={"pdf"}, limit=1, defer=run_module.read_defer(defer),
                  tools=fake_tools())
    assert summary["selected"] == ["3"]  # row 2 deferred: row 3 is the first pdf
    assert run_module.read_defer(None) == frozenset()
    seen = {}
    monkeypatch.setattr(run_module, "run", lambda *a, **k: seen.update(k) or {})
    assert main(["run", "--export", str(export), "--out", str(tmp_path / "o2"), "--defer", str(defer)]) == 0
    assert seen["defer"] == frozenset({f"{SITE}uploadfile/a.pdf", f"{SITE}uploadfile/c.docx"})


def test_max_minutes_stops_between_files_with_exit_code_4(tmp_path, monkeypatch):
    # main session 11-03 03:40: one lane's process grew to 6.3 GB in a few hours of big archives on the 16 GiB Mac;
    # the lane script restarts the process when it stops on time, which gives the memory back
    export, out = mini_export(tmp_path), tmp_path / "out"
    summary = run(export, out, max_minutes=0, tools=fake_tools())
    assert summary["converted"] == 1 and summary["stopped_early"] is True
    summary = run(export, out, tools=fake_tools())
    assert summary["stopped_early"] is False and summary["cached"] == 2  # row 1, and row 3 shares row 2's bytes
    seen = {}
    monkeypatch.setattr(run_module, "run", lambda *a, **k: seen.update(k) or {"stopped_early": True})
    assert main(["run", "--export", str(export), "--out", str(tmp_path / "o2"), "--max-minutes", "40"]) == 4
    assert seen["max_minutes"] == 40.0 and run_module.EXIT_MORE == 4
    monkeypatch.setattr(run_module, "run", lambda *a, **k: {"stopped_early": False})
    assert main(["run", "--export", str(export), "--out", str(tmp_path / "o2")]) == 0


def test_limit_takes_the_first_inputs(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    assert run(export, out, limit=2, tools=fake_tools())["selected"] == ["1", "2"]
    accounts = _accounts(export, out)
    assert accounts["3"].status == "ok" and accounts["4"].status == "pending"  # row 3 shares row 2's bytes


def test_missing_exported_file_is_failed(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    (export / "files/0004-d.bin").unlink()
    run(export, out, tools=fake_tools())
    accounts = _accounts(export, out)
    assert (accounts["7"].status, accounts["7"].reason) == ("failed", "导出资料夹里没有这个档（files/0004-d.bin）")
    assert accounts["7"].sha256 == "" and accounts["4"].status == "ok"


def test_a_damaged_zip_neither_stops_the_run_nor_the_report(tmp_path, monkeypatch):
    # review of phase 1: a docx/odt/zip whose deflated 'mimetype' is damaged made select() and account() raise
    from nknu.totext import libreoffice
    from nknu.totext.libreoffice import ConvertError
    from tests.totext.test_route import _damaged_mimetype

    def broken_to_modern(src, kind, workdir, **_):
        raise ConvertError("LibreOffice 没产出可用的档（退出码 0）")

    monkeypatch.setattr(libreoffice, "to_modern", broken_to_modern)
    export = write_export(tmp_path / "export", [
        {"type": "附件", "url": SITE + "uploadfile/b.odt", "file": "files/0001-b.odt",
         "bytes": _damaged_mimetype(tmp_path / "made.odt"), "fmt": "zip"},
        {"type": "网页", "url": PAGE_URL, "file": "pages/0001-a.html", "bytes": PAGE_HTML}])
    out = tmp_path / "out"
    assert run(export, out, tools=fake_tools())["converted"] == 2
    accounts = _accounts(export, out)
    assert (accounts["1"].route, accounts["1"].status, accounts["1"].reason) == (
        "office_old", "failed", "LibreOffice 没产出可用的档（退出码 0）")
    assert accounts["2"].status == "ok" and main(["report", "--export", str(export), "--out", str(out)]) == 0


def test_same_bytes_read_another_way_is_said(tmp_path):
    # one cache entry per content hash: a row that would read those bytes another way is accounted, not hidden
    export = write_export(tmp_path / "export", [
        {"type": "网页", "url": PAGE_URL, "file": "pages/0001-a.html", "bytes": PAGE_HTML},
        {"type": "附件", "url": SITE + "uploadfile/a.htm", "file": "files/0001-a.htm", "bytes": PAGE_HTML}])
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    accounts = _accounts(export, out)
    assert accounts["1"].status == "ok"
    assert accounts["2"].status == "skipped" and accounts["2"].route == "unsupported"
    assert "和第 1 行是同一个档" in accounts["2"].reason


def test_workdirs_are_removed(tmp_path):
    export, out = mini_export(tmp_path), tmp_path / "out"
    run(export, out, tools=fake_tools())
    assert list((out / "work").iterdir()) == []


def test_run_restores_the_tools_beat(tmp_path):
    export, beats = mini_export(tmp_path), []
    tools = fake_tools(beat=lambda: beats.append(1))
    original = tools.beat
    run(export, tmp_path / "out", tools=tools)
    assert tools.beat is original and len(beats) == 3  # one Docling call each for the pdf and the docx, one image


def test_cli_run_prints_one_summary_line(tmp_path, capsys):
    export, out = mini_export(tmp_path), tmp_path / "out"
    assert main(["run", "--export", str(export), "--out", str(out), "--routes", "page,data"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1 and printed[0].startswith("转文字：")
    accounts = _accounts(export, out)
    assert accounts["1"].status == "ok" and accounts["6"].status == "skipped" and accounts["2"].status == "pending"


class WarmDocling(FakeDocling):
    """A fake that holds Docling's own converter (DoclingConverter._docling()): loading a format's pipeline takes
    0.3 s, or raises `fail`."""

    def __init__(self, fail: Exception | None = None):
        super().__init__()
        self.warmed: list[str] = []
        outer = self

        class Converter:
            def initialize_pipeline(self, fmt):
                outer.warmed.append(fmt.value)
                if fail is not None:
                    raise fail
                time.sleep(0.3)

        self._converter = Converter()

    def _docling(self):
        return self._converter


class WarmEngine(FakeEngine):
    """A fake engine whose models load on warm() (0.2 s)."""

    def __init__(self):
        super().__init__([OCR_LINE])
        self.warmed = 0

    def warm(self):
        self.warmed += 1
        time.sleep(0.2)


def test_docling_loads_once_per_format_before_its_first_file(tmp_path):
    # before the first PDF: the OCR-on converter's PDF pipeline and the OCR engine (which of them a PDF needs shows
    # once its pages are analysed); the docx pipeline before the first docx. Changed 11-02 night: the OCR-off
    # converter's PDF pipeline is never loaded, since an all-scanned PDF skips Docling (only Office files use it)
    export, out, fake, fake_ocr, engine = mini_export(tmp_path), tmp_path / "out", WarmDocling(), WarmDocling(), \
        WarmEngine()
    summary = run(export, out, tools=Tools(docling=fake, docling_ocr=fake_ocr, ocr=engine))
    assert fake.warmed == ["docx"] and fake_ocr.warmed == ["pdf"] and engine.warmed == 1
    assert summary["warm_up_seconds"] >= 0.75  # the fakes' loads: 0.3 s (pdf-ocr) + 0.2 s (OCR engine) + 0.3 s (docx)
    accounts = _accounts(export, out)
    assert accounts["2"].seconds < 0.2 and accounts["4"].seconds < 0.2  # the one-time loads are no file's time
    again, again_ocr, again_engine = WarmDocling(), WarmDocling(), WarmEngine()
    run(export, out, tools=Tools(docling=again, docling_ocr=again_ocr, ocr=again_engine))
    assert again.warmed == again_ocr.warmed == [] and again_engine.warmed == 0  # nothing to convert: nothing loaded


def test_docling_that_cannot_load_stops_the_run(tmp_path, monkeypatch, capsys):
    # a broken setup (a model missing) would otherwise mark every file of that format failed, and a rerun would keep
    # those results. Changed 11-02 night: a PDF needs the OCR-on converter only, so the OCR-off one failing to load
    # stops the run before the first docx, the PDF converted already (it stopped before the first PDF)
    export, out = mini_export(tmp_path), tmp_path / "out"
    with pytest.raises(SetupError, match="FileNotFoundError: layout model missing"):
        run(export, out, tools=fake_tools(WarmDocling(fail=FileNotFoundError("layout model missing")),
                                          docling_ocr=WarmDocling()))
    accounts = _accounts(export, out)
    assert accounts["1"].status == accounts["2"].status == "ok" and accounts["4"].status == "pending"
    broken_ocr, other = WarmDocling(fail=RuntimeError("no OCR converter")), tmp_path / "other"
    with pytest.raises(SetupError, match="RuntimeError: no OCR converter"):  # the OCR-on converter: before any PDF
        run(export, other, tools=fake_tools(WarmDocling(), docling_ocr=broken_ocr))
    accounts = _accounts(export, other)
    assert accounts["1"].status == "ok" and accounts["2"].status == "pending"
    monkeypatch.setattr(run_module, "Tools", lambda: fake_tools(WarmDocling(fail=RuntimeError("no torch"))))
    assert main(["run", "--export", str(export), "--out", str(out)]) == 2
    assert "Docling 准备不了" in capsys.readouterr().err


class BrokenEngine(FakeEngine):
    """An OCR engine whose models cannot load (RapidOCR without OpenCV's libGL in a slim image, say)."""

    def warm(self):
        raise ImportError("libGL.so.1: cannot open shared object file")

    def read(self, image):
        self.warm()


def test_ocr_engine_that_cannot_load_stops_the_run(tmp_path, monkeypatch, capsys):
    # review of phase 1: an image lane (--routes image) built RapidOCR inside the first image's conversion, so a
    # broken setup marked every image failed, results a rerun keeps
    export, out = mini_export(tmp_path), tmp_path / "out"
    with pytest.raises(SetupError, match="OCR 准备不了（ImportError: libGL.so.1"):
        run(export, out, routes={"image"}, tools=fake_tools(ocr=BrokenEngine()))
    assert _accounts(export, out)["5"].status == "pending"
    monkeypatch.setattr(run_module, "Tools", lambda: fake_tools(ocr=BrokenEngine()))
    assert main(["run", "--export", str(export), "--out", str(out), "--routes", "image"]) == 2
    assert "OCR 准备不了" in capsys.readouterr().err


def test_setup_error_is_never_one_files_failure(tmp_path):
    # wherever a tool fails to load inside a conversion, the file keeps no result and no crash marker
    from nknu.totext.convert import convert_and_save
    from nknu.totext.route import Route

    cache, path = Cache(tmp_path / "cache"), png(tmp_path, 120, 90)
    sha = sha256_file(path)
    with pytest.raises(SetupError, match="libGL"):
        convert_and_save(cache, sha, path, Route("image", "png"), fake_tools(ocr=BrokenEngine()), tmp_path / "w")
    assert cache.done(sha) is None and not cache.crashed(sha) and not (tmp_path / "w").exists()


def _archive_export(tmp_path: Path) -> Path:
    from tests.totext.fixtures import zip_of

    made = tmp_path / "made"
    made.mkdir(parents=True, exist_ok=True)
    zipped = zip_of(made / "m.zip", {"a.pdf": tiny_pdf(made / "a.pdf", [PDF_TEXT]).read_bytes()}).read_bytes()
    return write_export(tmp_path / "export", [
        {"type": "附件", "url": SITE + "uploadfile/m.zip", "file": "files/0001-m.zip", "bytes": zipped, "fmt": "zip"}])


def test_archive_lane_loads_every_format_before_its_first_archive(tmp_path):
    # review of phase 1: an archive lane (--routes archive) loaded Docling inside the archive's conversion, so a model
    # missing marked every archive with a PDF or Office member failed; now each format a member may need, and the OCR
    # engine, load before the first archive, and a load that fails stops the run with the archive still pending.
    # Changed 11-02 night: a PDF's formats are the OCR-on converter's and the OCR engine's, the OCR-off converter
    # loads the Office formats only
    export, out = _archive_export(tmp_path), tmp_path / "out"
    fake, fake_ocr, engine = WarmDocling(), WarmDocling(), WarmEngine()
    run(export, out, routes={"archive"}, tools=Tools(docling=fake, docling_ocr=fake_ocr, ocr=engine))
    assert fake.warmed == ["docx", "xlsx", "pptx"] and fake_ocr.warmed == ["pdf"] and engine.warmed == 1
    assert [(a.key, a.status) for a in account(export, Cache(out / "cache"))] == [("1", "ok"), ("1!a.pdf", "ok")]
    other = tmp_path / "other"
    with pytest.raises(SetupError, match="FileNotFoundError: layout model missing"):
        run(export, other, routes={"archive"},
            tools=fake_tools(WarmDocling(fail=FileNotFoundError("layout model missing")), docling_ocr=WarmDocling()))
    [only] = account(export, Cache(other / "cache"))
    assert (only.key, only.status) == ("1", "pending")


def test_report_totals_pages_per_ocr_path(tmp_path):
    # addendum A: the report totals the pages of each OCR path (Docling's region OCR, a broken layer Docling re-read,
    # our whole-page OCR per reason) and the images read
    made = tmp_path / "made"
    made.mkdir()
    export = write_export(tmp_path / "export", [
        {"type": "附件", "url": SITE + "uploadfile/m.pdf", "file": "files/0001-m.pdf", "fmt": "pdf",
         "bytes": tiny_pdf(made / "m.pdf", [PDF_TEXT, None]).read_bytes()},
        {"type": "附件", "url": SITE + "uploadfile/s.pdf", "file": "files/0002-s.pdf", "fmt": "pdf",
         "bytes": tiny_pdf(made / "s.pdf", [None, None, None]).read_bytes()},
        {"type": "图片", "url": SITE + "uploadfile/x.png", "file": "images/0001-x.png", "fmt": "png",
         "bytes": png(made, 120, 90).read_bytes()}])
    on = FakeDocling(pages=[PDF_TEXT, ""], ocr_pages=(1, 2), broken_pages=(1,))
    out = tmp_path / "out"
    run(export, out, tools=fake_tools(docling_ocr=on))  # the scan reaches no Docling converter (11-02 night)
    assert main(["report", "--export", str(export), "--out", str(out)]) == 0
    accounts = _accounts(export, out)
    mixed, scanned = accounts["1"], accounts["2"]
    assert (mixed.docling_ocr_pages, mixed.broken_pages, mixed.few_chars_pages, mixed.ocr_pages) == (2, 1, 1, 1)
    assert (scanned.docling_ocr_pages, scanned.few_chars_pages, scanned.misread_pages) == (0, 3, 0)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["ocr"]["paths"] == {
        "docling_ocr": {"pages": 2, "files": 1}, "docling_broken": {"pages": 1, "files": 1},
        "few_chars": {"pages": 4, "files": 2}, "misread": {"pages": 0, "files": 0}, "image": {"files": 1}}
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "| 整页 OCR：没有可用的文字层（不到 30 个字） | 4 | 2 |" in md
    assert "| 其中 Docling 判定文字层坏掉、整页重认 | 1 | 1 |" in md


def test_report_shows_ocr_noise_and_office_pictures(tmp_path):
    # review of phase 1: short OCR lines (a diagram's labels, a seal) and the pictures inside Office files, which
    # nobody OCRs, were in no report
    made = tmp_path / "made"
    made.mkdir()
    export = write_export(tmp_path / "export", [
        {"type": "附件", "url": SITE + "uploadfile/m.pdf", "file": "files/0001-m.pdf", "fmt": "pdf",
         "bytes": tiny_pdf(made / "m.pdf", [PDF_TEXT]).read_bytes()},
        {"type": "附件", "url": SITE + "uploadfile/c.docx", "file": "files/0002-c.docx", "fmt": "ooxml:docx",
         "bytes": docx_with_table(made / "c.docx").read_bytes()}])
    region = FakeDocling(pages=[PDF_TEXT], ocr_pages=(1,), ocr_text={1: ["Rf", "十", "电路图说明"]})
    out = tmp_path / "out"
    run(export, out, tools=fake_tools(FakeDocling(pages=["選課注意事項"], pictures=2), docling_ocr=region))
    assert main(["report", "--export", str(export), "--out", str(out)]) == 0
    accounts = _accounts(export, out)
    assert (accounts["1"].ocr_short_lines, accounts["1"].simplified_only) == (2, 3)  # 电 图 说
    assert (accounts["2"].route, accounts["2"].pictures) == ("office", 2)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["ocr"]["short_lines"] == 2 and [a["key"] for a in summary["ocr"]["short_top"]] == ["1"]
    assert summary["office_pictures"] == {"files": 1, "pictures": 2, "top": [
        {"key": "2", "file": "files/0002-c.docx", "member": "", "route": "office", "count": 2}]}
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "OCR 读出的极短行" in md and "| 1 | files/0001-m.pdf | pdf | 2 |" in md
    assert "## 文件里的图片（没有 OCR）" in md and "| 2 | files/0002-c.docx | office | 2 |" in md
    assert "包括 Docling 区域 OCR 读的；只标出来，不改字" in md and "| 1 | files/0001-m.pdf | pdf | 3 |" in md


def test_watchdog_fires_without_feed_and_never_after_stop():
    fired = threading.Event()
    Watchdog(0.2, fired.set)
    assert fired.wait(5)
    calls: list[int] = []
    fed = Watchdog(1.0, lambda: calls.append(1))
    for _ in range(6):  # 0.6 s of feeding, each well inside the second
        time.sleep(0.1)
        fed.feed()
    fed.stop()
    time.sleep(1.2)  # past the last deadline: a stopped watchdog never fires
    assert calls == []


def test_work_folder_is_per_process(tmp_path):
    # two runs sharing one --out never touch each other's work folders
    export, out, fake = mini_export(tmp_path), tmp_path / "out", FakeDocling()
    other = out / "work" / "999999-0123456789abcdef"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("x", encoding="utf-8")
    run(export, out, tools=fake_tools(fake))
    [docx] = [path for path, kind, _head in fake.calls if kind == "docx"]  # the numbering-fixed copy in its workdir
    assert docx.parent.name == f"{os.getpid()}-{_sha(export, 'files/0003-c.docx')[:16]}"
    assert (other / "keep.txt").exists() and [p.name for p in (out / "work").iterdir()] == [other.name]
