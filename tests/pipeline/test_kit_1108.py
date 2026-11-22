"""Tests of the lab-PC kit for many exports (plan 2026-11-08 T5): the export list and the job lists, ready and the
accounting over every export (build_helpers.py), the progress count (totext_progress.py), the per-site report
(site_report.py), lanes.sh driven by a stand-in interpreter and build.sh --dry-run. Light: no model, no network, no
conversion; nothing outside tmp_path is written (build.sh --dry-run only prints).

    uv run --no-sync pytest research/pipeline_2026-11-02/lab_pc/test_kit_1108.py -q -p no:cacheprovider
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from nknu.content.schema import create_schema
from nknu.core.textnorm import tokenize
from nknu.totext.cache import Cache, sha256_file
from nknu.totext.pagetext import page_route_kind

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "deploy" / "lab-pipeline"  # the programs (in the repository since 11-19)
P = "research/pipeline_2026-11-02"
PIPE = REPO / P  # the pipeline's own folder: data and state, in no commit
SITE_A, SITE_B = "c.nknu.edu.tw/aca", "sso.nknu.edu.tw/web/595"
NAME_A, NAME_B = "c.nknu.edu.tw_aca", "sso.nknu.edu.tw_web_595"
OLD_PAGE_KIND = "page|body|pages-1:0000000000"  # a page result an older page rule read (plan 2026-11-08 T2)


def _load(name: str, path: Path):
    """The kit's script at path as module name, registered so the scripts that import it share this copy."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bh = _load("build_helpers", KIT / "build_helpers.py")
site_report = _load("site_report", KIT / "site_report.py")
progress = _load("totext_progress", KIT / "totext_progress.py")


def _export(root: Path, folder: str, site: str, tag: str = "") -> Path:
    """An export of one page, one PDF and one PNG at root/folder, its bytes its own (site, tag)."""
    d = root / folder
    for sub in ("pages", "files", "images"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    base = f"https://{site}/"
    rows = [{"序号": 1, "类型": "网页", "网址": base + "p.aspx", "本地档案": "pages/0001-p.html", "标题": "p"},
            {"序号": 2, "类型": "附件", "网址": base + "f.pdf", "本地档案": "files/0002-f.pdf", "标题": "f"},
            {"序号": 3, "类型": "图片", "网址": base + "i.png", "本地档案": "images/0003-i.png", "标题": ""}]
    (d / "pages" / "0001-p.html").write_text(f"<html><body><p>{site} {tag}</p></body></html>", encoding="utf-8")
    (d / "files" / "0002-f.pdf").write_bytes(f"%PDF-1.4\n{site} {tag}".encode())
    (d / "images" / "0003-i.png").write_bytes(b"\x89PNG\r\n\x1a\n" + f"{site} {tag}".encode())
    (d / "manifest.json").write_text(json.dumps({"网站": site, "项目": rows}, ensure_ascii=False), encoding="utf-8")
    return d


def _list(tmp_path: Path, lines: list[str], name: str = "exports.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _done(cache_root: Path, path: Path, route: str, kind: str, page_kind: str = "") -> None:
    """A finished conversion of path in the cache, as nknu.totext writes it."""
    result = {"route": route, "kind": kind, "status": "ok"} | ({"page_kind": page_kind} if page_kind else {})
    Cache(cache_root).save(sha256_file(path), result, "text", {})


def _all_done(cache_root: Path, export: Path, site: str, page_kind: str | None = None) -> None:
    _done(cache_root, export / "files" / "0002-f.pdf", "pdf", "pdf")
    _done(cache_root, export / "images" / "0003-i.png", "image", "png")
    url = f"https://{site}/p.aspx"
    _done(cache_root, export / "pages" / "0001-p.html", "page", "html", page_kind or page_route_kind(url, url))


# ---- the export list ----------------------------------------------------------------------------------------------


def test_no_list_is_the_aca_export_alone(tmp_path):
    _export(tmp_path, bh.ACA_EXPORT, SITE_A)
    assert bh.exports_file({}, default=tmp_path / "exports.txt", repo=tmp_path) is None
    (export,) = bh.read_exports(None, repo=tmp_path)
    assert (export.folder, export.site, export.name) == (bh.ACA_EXPORT, SITE_A, NAME_A)


def test_the_list_file_is_exports_else_the_default_file(tmp_path):
    default = tmp_path / "exports.txt"
    pilot = bh.exports_file({"EXPORTS": "lists/pilot.txt"}, default=default, repo=tmp_path)
    assert pilot == tmp_path / "lists" / "pilot.txt"
    assert bh.exports_file({"EXPORTS": "/abs/x.txt"}, default=default, repo=tmp_path) == Path("/abs/x.txt")
    default.write_text("x\n", encoding="utf-8")
    assert bh.exports_file({"EXPORTS": "  "}, default=default, repo=tmp_path) == default  # compose passes "" unset


def test_a_list_skips_comments_and_blank_lines_and_reads_a_windows_editors_file(tmp_path):
    _export(tmp_path, "archive-export/a", SITE_A)
    b = _export(tmp_path, "scratch/b", SITE_B)
    path = tmp_path / "exports.txt"
    path.write_bytes("﻿# 第一批\r\n\r\narchive-export\\a/\r\n  # later\r\n".encode() + f"{b}\r\n".encode())
    exports = bh.read_exports(path, repo=tmp_path)
    assert [(e.folder, e.name) for e in exports] == [("archive-export/a", NAME_A), (str(b), NAME_B)]
    assert exports[0].path == tmp_path / "archive-export" / "a"


@pytest.mark.parametrize(("lines", "words"), [
    (["# nothing"], "是空的"),
    (["exports/a", "exports/none"], "exports/none：没有 manifest.json"),
    (["exports/a", "exports/a/"], "写了两次"),
    (["exports/a", "exports/a2"], "是同一个网站"),
    (["exports/a", "exports/odd"], "档名一样"),
    (["exports/nosite"], "没写是哪个网站"),
    (["exports/dot"], "做不成档名"),
])
def test_a_list_it_cannot_use_stops_the_reader(tmp_path, lines, words):
    _export(tmp_path, "exports/a", SITE_A)
    _export(tmp_path, "exports/a2", SITE_A, "again")
    _export(tmp_path, "exports/odd", "c.nknu.edu.tw_aca")  # another key, the same name as c.nknu.edu.tw/aca
    _export(tmp_path, "exports/dot", "../x")
    nosite = _export(tmp_path, "exports/nosite", SITE_B)
    (nosite / "manifest.json").write_text(json.dumps({"项目": []}), encoding="utf-8")
    with pytest.raises(bh.ExportsError, match=words):
        bh.read_exports(_list(tmp_path, lines), repo=tmp_path)


def test_exports_prints_one_tab_separated_line_per_export(tmp_path, monkeypatch, capsys):
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    monkeypatch.setenv("EXPORTS", str(_list(tmp_path, [str(a), str(b)])))
    assert bh.main(["exports"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [f"{a}\t{NAME_A}\t{SITE_A}", f"{b}\t{NAME_B}\t{SITE_B}"]
    assert "2 份导出" in out.err
    monkeypatch.setenv("EXPORTS", str(_list(tmp_path, [str(a), str(tmp_path / "gone")], "bad.txt")))
    assert bh.main(["exports"]) == 2
    assert "没有 manifest.json" in capsys.readouterr().err


# ---- job lists, ready, account ------------------------------------------------------------------------------------


def test_a_job_list_is_kept_per_export_and_made_again_for_another_manifest(tmp_path, monkeypatch):
    from nknu.totext import run

    a = _export(tmp_path, "a", SITE_A)
    (export,) = bh.read_exports(_list(tmp_path, [str(a)]))
    jobs_dir = tmp_path / "jobs"
    rows = bh.export_jobs(export, frozenset(), jobs_dir)
    assert [(j["key"], j["route"], j["deferred"]) for j in rows] == [("1", "page", False), ("2", "pdf", False),
                                                                    ("3", "image", False)]
    assert rows[1]["sha"] == sha256_file(a / "files" / "0002-f.pdf")
    assert (jobs_dir / f"{NAME_A}.json").is_file()
    real = run.select
    calls = []
    monkeypatch.setattr(run, "select", lambda *args, **kw: calls.append(1) or real(*args, **kw))
    again = bh.export_jobs(export, frozenset({f"https://{SITE_A}/f.pdf"}), jobs_dir)
    assert calls == [] and [j["deferred"] for j in again] == [False, True, False]  # the kept list, deferred now
    manifest = json.loads((a / "manifest.json").read_text(encoding="utf-8"))
    (a / "manifest.json").write_text(json.dumps({**manifest, "导出时间": "later"}, ensure_ascii=False), encoding="utf-8")
    bh.export_jobs(export, frozenset(), jobs_dir)
    assert calls == [1]  # another manifest: made again


def test_all_jobs_name_each_row_by_its_export(tmp_path):
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    rows = bh.all_jobs(bh.read_exports(_list(tmp_path, [str(a), str(b)])), frozenset(), tmp_path / "jobs")
    assert [(r["key"], r["export"]) for r in rows] == [(f"{NAME_A}:{n}", NAME_A) for n in "123"] + [
        (f"{NAME_B}:{n}", NAME_B) for n in "123"]


def test_the_forced_stop_repair_gives_each_lane_its_own_files_of_every_export(tmp_path):
    fix = _load("retry_cut_members", KIT / "retry_cut_members.py")
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    rows = bh.all_jobs(bh.read_exports(_list(tmp_path, [str(a), str(b)])), frozenset(), tmp_path / "jobs")
    cache = Cache(tmp_path / "cache")
    since = time.time() - 60
    page, pdf, member = sha256_file(b / "pages" / "0001-p.html"), sha256_file(b / "files" / "0002-f.pdf"), "f" * 64
    for sha in (page, pdf, member):  # what a forced stop cut: export b's page and PDF, an archive's member
        cache.begin(sha)
    for lane, sha in (("B", page), ("A", pdf), ("C", member)):
        actions, _notes = fix.plan_after_stop(cache.root, lane, rows, since)
        assert [path.parent.name for _what, path, _new in actions] == [sha]
    assert fix.main(["--lane", "B", "--cache", str(cache.root), "--allow-mac"]) == 2  # B: after a forced stop only


def test_ready_waits_for_every_export_and_for_pages_an_older_rule_read(tmp_path, capsys):
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    cache = tmp_path / "cache"
    cache.mkdir()
    kw = {"exports": bh.read_exports(_list(tmp_path, [str(a), str(b)])), "cache_root": cache,
          "defer_file": tmp_path / "none.txt", "locks": {}, "jobs_dir": tmp_path / "jobs"}
    _all_done(cache, a, SITE_A)
    assert bh.ready(False, False, **kw) == 10
    assert f"{NAME_B}：还没转 3" in capsys.readouterr().out
    _all_done(cache, b, SITE_B, OLD_PAGE_KIND)  # b's page read by an older rule: read again (plan 2026-11-08 T2)
    assert bh.ready(False, False, **kw) == 10
    assert f"{NAME_B}：还没转 1" in capsys.readouterr().out
    _all_done(cache, b, SITE_B)
    assert bh.ready(False, False, **kw) == 0


def test_ready_first_builds_while_the_deferred_files_wait(tmp_path):
    a = _export(tmp_path, "a", SITE_A)
    cache = tmp_path / "cache"
    cache.mkdir()
    _done(cache, a / "images" / "0003-i.png", "image", "png")
    url = f"https://{SITE_A}/p.aspx"
    _done(cache, a / "pages" / "0001-p.html", "page", "html", page_route_kind(url, url))
    defer = _list(tmp_path, ["# 学报", f"https://{SITE_A}/f.pdf"], "defer.txt")
    kw = {"exports": bh.read_exports(_list(tmp_path, [str(a)])), "cache_root": cache, "defer_file": defer,
          "locks": {}, "jobs_dir": tmp_path / "jobs"}
    assert bh.ready(False, False, **kw) == 10
    assert bh.ready(True, False, **kw) == 0


def test_account_writes_one_report_for_every_export(tmp_path):
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    exports = bh.read_exports(_list(tmp_path, [str(a), str(b)]))
    cache, out = tmp_path / "cache", tmp_path / "acc"
    cache.mkdir()
    _all_done(cache, a, SITE_A)
    assert bh.account(exports, out, cache_root=cache, tools={}) == 1  # b is still to convert
    lines = [json.loads(x) for x in (out / "accounting.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(x["key"], x["status"]) for x in lines] == [(f"{NAME_A}:{n}", "ok") for n in "123"] + [
        (f"{NAME_B}:{n}", "pending") for n in "123"]
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["pending"] == 3
    assert (out / "summary.md").is_file()
    _all_done(cache, b, SITE_B)
    assert bh.account(exports, out, cache_root=cache, tools={}) == 0
    with pytest.raises(ValueError):
        bh.account(exports, a / "acc", cache_root=cache, tools={})  # never inside an export


# ---- totext_progress.py -------------------------------------------------------------------------------------------


def test_progress_counts_every_export_and_a_page_an_older_rule_read_as_left(tmp_path, monkeypatch, capsys):
    a, b = _export(tmp_path, "a", SITE_A), _export(tmp_path, "b", SITE_B)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("EXPORTS", str(_list(tmp_path, [str(a), str(b)])))
    monkeypatch.setattr(bh, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(progress, "CACHE", cache)
    monkeypatch.setattr(progress, "DEFER", tmp_path / "none.txt")
    _all_done(cache, a, SITE_A)
    _all_done(cache, b, SITE_B, OLD_PAGE_KIND)  # result.json is there, but the page is to be read again
    assert progress.main() == 1
    out = capsys.readouterr().out
    assert "线 A 其余：完成 2，还剩 0" in out and "线 B 其余：完成 3，还剩 1" in out
    assert f"{NAME_B}：线 A 还剩 0（学报考题 0），线 B 还剩 1" in out and f"{NAME_A}：" not in out
    assert out.rstrip().endswith("还没到建库的时候")
    _all_done(cache, b, SITE_B)
    assert progress.main() == 0
    assert capsys.readouterr().out.rstrip().endswith("可以建第一版库")


def test_progress_says_why_a_list_cannot_be_used(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXPORTS", str(_list(tmp_path, [str(tmp_path / "gone")])))
    assert progress.main() == 2
    assert "没有 manifest.json" in capsys.readouterr().out


# ---- site_report.py -----------------------------------------------------------------------------------------------

SITES_DDL = ("CREATE TABLE sites (site TEXT PRIMARY KEY, name TEXT, unit TEXT, data_as_of TEXT,"
             " missing_pages INTEGER, missing_files INTEGER, docs INTEGER, export TEXT)")  # plan 2026-11-08 T4
SITE_ROWS = [(SITE_A, "教務處", "教務處", "2026-11-02", 3, 43, 10, "archive-export/aca-20261102"),
             (SITE_B, "生活輔導組", "生活輔導組", "2026-11-08", 0, 1, 6, "exports/595"),
             ("sites.google.com/x", "空網站", "國立高雄師範大學", "2026-11-08", 0, 0, 0, "exports/x")]
# (doc_id, site, kind_class, title, text, identity date, tier); a document with text is found by its title
DOCS = [
    ("dn1", SITE_A, "notice", "招生簡章", "招生簡章的內容", "2026-10-01", "primary"),
    ("dn2", SITE_A, "notice", "獎學金申請", "獎學金申請辦法", "2026-09-01", "primary"),
    ("dp1", SITE_A, "page", "宿舍床位", "宿舍床位分配", None, "primary"),
    ("dp2", SITE_A, "page", "請假規定", "", None, "primary"),
    ("dp3", SITE_A, "page", "畢業門檻", "畢業門檻說明", "2026-08-01", "primary"),
    ("da1", SITE_A, "attachment", "選課須知", "選課須知全文", "2026-07-01", "primary"),
    ("da2", SITE_A, "attachment", "兵役緩徵", "兵役緩徵表", None, "background"),
    ("da3", SITE_A, "attachment", "校園導覽", "校園導覽圖", "2026-06-01", "primary"),
    ("de1", SITE_A, "event", "開學典禮", "開學典禮", None, "primary"),
    ("dl1", SITE_A, "link", "外部網站", "", None, "primary"),
    ("dq1", SITE_B, "page", "心理諮商", "心理諮商預約", "2026-10-02", "primary"),
    ("dq2", SITE_B, "page", "生涯輔導", "生涯輔導講座", "2026-10-03", "primary"),
    ("dq3", SITE_B, "page", "性別平等", "性別平等委員會", None, "primary"),
    ("dq4", SITE_B, "page", "急難救助", "", None, "primary"),
    ("dq5", SITE_B, "page", "失物招領", "", None, "primary"),
    ("dq6", SITE_B, "page", "租屋資訊", "", None, "primary"),
]


def _report_db(path: Path, docs: list[tuple], *, sites: list[tuple] | None = SITE_ROWS, site_column: bool = True,
               meta_site: str = SITE_A) -> Path:
    """A content database of nknu.content.schema holding docs (each with text gets one chunk whose title and text are
    in chunk_fts, so a search finds it by its title); with docs.site and the sites table of plan 2026-11-08 T4, or
    without both, as a database built before it."""
    conn = sqlite3.connect(path)
    create_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
    if site_column and "site" not in cols:
        conn.execute("ALTER TABLE docs ADD COLUMN site TEXT NOT NULL DEFAULT ''")
    if not site_column and "site" in cols:
        for index in [r[1] for r in conn.execute("PRAGMA index_list(docs)")]:
            if any(r[2] == "site" for r in conn.execute(f"PRAGMA index_info('{index}')")):
                conn.execute(f"DROP INDEX '{index}'")
        conn.execute("ALTER TABLE docs DROP COLUMN site")
    has_sites = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sites'").fetchone()
    if sites is not None and not has_sites:
        conn.execute(SITES_DDL)
    if sites is None and has_sites:
        conn.execute("DROP TABLE sites")
    for doc_id, site, kind, title, text, ident, tier in docs:
        names = ["doc_id", "kind_class", "kind_raw", "title", "url", "unit", "has_text", "text", "chars",
                 "ident_date", "tier"] + (["site"] if site_column else [])
        values = [doc_id, kind, kind, title, f"https://x.test/{doc_id}", "", int(bool(text)), text, len(text), ident,
                  tier] + ([site] if site_column else [])
        conn.execute(f"INSERT INTO docs({', '.join(names)}) VALUES ({', '.join('?' * len(values))})", values)
        if text:
            chunk = conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, 0, 0, ?)",
                                 (doc_id, len(text))).lastrowid
            conn.execute("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens) VALUES (?, ?, ?)",
                         (chunk, " ".join(tokenize(title)), " ".join(tokenize(text))))
    if sites:
        conn.executemany("INSERT INTO sites(site, name, unit, data_as_of, missing_pages, missing_files, docs, export)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", sites)
    conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                     [("aliases_json", "[]"), ("built_at", "2026-11-08T10:00:00+08:00"), ("site", meta_site),
                      ("data_as_of", "2026-11-02"), ("missing_pages", "3"), ("missing_files", "43")])
    conn.commit()
    conn.close()
    return path


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def test_site_report_counts_kinds_pages_without_text_and_documents_without_a_date(tmp_path):
    with _ro(_report_db(tmp_path / "c.db", DOCS)) as conn:
        rows = site_report.site_rows(conn, today=date(2026, 11, 8))
    by = {r.site: r for r in rows}
    assert list(by) == [SITE_A, "sites.google.com/x", SITE_B]  # by site key
    a = by[SITE_A]
    assert (a.name, a.data_as_of, a.missing_pages, a.missing_files) == ("教務處", "2026-11-02", 3, 43)
    assert a.kinds == {"notice": 2, "page": 3, "attachment": 3, "event": 1, "link": 1} and a.background == 1
    assert (a.pages, a.empty_pages, a.dated, a.no_date) == (5, 1, 8, 3)
    assert (a.sampled, a.found) == (8, 7) and [m[0] for m in a.misses] == ["dp2"]
    assert a.flags == []
    b = by[SITE_B]
    assert (b.pages, b.empty_pages, b.dated, b.no_date, b.sampled, b.found) == (6, 3, 6, 4, 6, 3)
    assert len(b.flags) == 3  # self-search 3/6, pages without text 3/6, no date 4/6
    assert by["sites.google.com/x"].flags == ["没有文件"]


def test_site_report_samples_at_most_the_sample_and_the_same_ones_every_time(tmp_path):
    words = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    docs = [(f"dz{i:02d}", SITE_B, "page", f"{w}{w}公告", f"{w}{w}公告內容", "2026-10-01", "primary")
            for i, w in enumerate(words)]
    docs.append(("dz99", SITE_B, "page", "!!", "", None, "primary"))  # no word to search for: never sampled
    with _ro(_report_db(tmp_path / "c.db", docs)) as conn:
        first = site_report.site_rows(conn, today=date(2026, 11, 8))
        again = site_report.site_rows(conn, today=date(2026, 11, 8))
        few = site_report.site_rows(conn, today=date(2026, 11, 8), sample=5)
    row = next(r for r in first if r.site == SITE_B)
    assert row.sampled == site_report.SAMPLE == 20 and row.picked == next(r for r in again if r.site == SITE_B).picked
    assert "dz99" not in row.picked and next(r for r in few if r.site == SITE_B).sampled == 5


def test_site_report_reads_a_database_without_sites_as_one_site(tmp_path):
    with _ro(_report_db(tmp_path / "c.db", DOCS[:10], sites=None, site_column=False)) as conn:
        (row,) = site_report.site_rows(conn, today=date(2026, 11, 8))
    assert (row.site, row.missing_pages, row.missing_files, row.data_as_of) == (SITE_A, 3, 43, "2026-11-02")
    assert sum(row.kinds.values()) == 10 and (row.sampled, row.found) == (8, 7)


def test_site_report_writes_markdown_and_never_fails_on_what_it_flags(tmp_path):
    db, out = _report_db(tmp_path / "c.db", DOCS), tmp_path / "site_report.md"
    assert site_report.main(["--db", str(db), "--out", str(out), "--today", "2026-11-08"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| 網站 |" not in text and "| 网站 |" in text  # Simplified Chinese, like the rest of the kit's reports
    line_b = next(x for x in text.splitlines() if x.startswith(f"| {SITE_B} "))
    assert "⚠" in line_b and "3 / 6" in line_b
    assert "dp2「請假規定」" in text and "dq4「急難救助」" in text
    assert site_report.main(["--db", str(tmp_path / "none.db"), "--out", str(out)]) == 2


def test_site_report_is_the_same_whatever_the_number_of_jobs(tmp_path):
    # 11-19: the title searches are shared among processes of the script; what is drawn and found does not move
    db = _report_db(tmp_path / "c.db", DOCS)
    one, four = tmp_path / "one.md", tmp_path / "four.md"
    assert site_report.main(["--db", str(db), "--out", str(one), "--today", "2026-11-08", "--jobs", "1"]) == 0
    assert site_report.main(["--db", str(db), "--out", str(four), "--today", "2026-11-08", "--jobs", "4",
                             "--checks", str(tmp_path / "four.json")]) == 0
    assert one.read_text(encoding="utf-8") == four.read_text(encoding="utf-8")
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        here = site_report.site_rows(conn, today=date(2026, 11, 8))
        there = site_report.site_rows(conn, today=date(2026, 11, 8), places=lambda picks: site_report.places_by_workers(
            db, picks, today=date(2026, 11, 8), top=site_report.TOP, jobs=3))
    assert [(r.site, r.picked, r.found, r.misses) for r in here] == [(r.site, r.picked, r.found, r.misses) for r in there]
    with pytest.raises(SystemExit):
        site_report.main(["--db", str(db), "--today", "2026-11-08"])


def test_site_report_shows_each_sites_deleted_notices_and_never_flags_them(tmp_path):
    # owner 11-09 (blueprint item 15, 「照建议定」): the school's deleted notices the build left out, per site
    db, out = _report_db(tmp_path / "c.db", DOCS), tmp_path / "site_report.md"
    with sqlite3.connect(db) as conn:
        conn.executemany("UPDATE sites SET deleted_notices = ? WHERE site = ?", [(27, SITE_A), (0, SITE_B)])
    with _ro(db) as conn:
        by = {r.site: r for r in site_report.site_rows(conn, today=date(2026, 11, 8))}
    assert (by[SITE_A].deleted_notices, by[SITE_B].deleted_notices, by["sites.google.com/x"].deleted_notices) == (
        27, 0, None)
    assert by[SITE_A].flags == []  # only reported, never a ⚠
    assert site_report.main(["--db", str(db), "--out", str(out), "--today", "2026-11-08"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "学校删掉的公告（不收）" in text
    head = next(x for x in text.splitlines() if x.startswith("| 网站 |")).split(" | ")
    column = head.index("学校删掉的公告（不收）")
    cells = {x.split(" | ")[0][2:]: x.split(" | ")[column] for x in text.splitlines() if x.startswith("| ") and
             " | " in x and not x.startswith(("| 网站 |", "|---"))}
    assert (cells[SITE_A], cells[SITE_B], cells["sites.google.com/x"]) == ("27", "0", "—")
    # a database built before pipeline-6 has no such column: nothing to show
    old = _report_db(tmp_path / "old.db", DOCS)
    with sqlite3.connect(old) as conn:
        conn.execute("DROP TABLE sites")
        conn.execute(SITES_DDL)
        conn.executemany("INSERT INTO sites VALUES (?, ?, ?, ?, ?, ?, ?, ?)", SITE_ROWS)
    with _ro(old) as conn:
        assert {r.deleted_notices for r in site_report.site_rows(conn, today=date(2026, 11, 8))} == {None}


def test_site_report_shows_each_sites_pages_read_again_whole_and_never_flags_them(tmp_path):
    # owner 11-09 (blueprint item 22, 「照这个做」): 「每站报告加『读到的字远少于整页』的页数」
    db, out = _report_db(tmp_path / "c.db", DOCS), tmp_path / "site_report.md"
    with sqlite3.connect(db) as conn:
        conn.executemany("UPDATE sites SET under_read = ? WHERE site = ?", [(12, SITE_A), (0, SITE_B)])
    with _ro(db) as conn:
        by = {r.site: r for r in site_report.site_rows(conn, today=date(2026, 11, 8))}
    assert (by[SITE_A].under_read, by[SITE_B].under_read, by["sites.google.com/x"].under_read) == (12, 0, None)
    assert by[SITE_A].flags == []  # only reported, never a ⚠
    assert site_report.main(["--db", str(db), "--out", str(out), "--today", "2026-11-08"]) == 0
    text = out.read_text(encoding="utf-8")
    head = next(x for x in text.splitlines() if x.startswith("| 网站 |")).split(" | ")
    column = head.index("读到的字远少于整页（改读整页）")
    cells = {x.split(" | ")[0][2:]: x.split(" | ")[column] for x in text.splitlines() if x.startswith("| ") and
             " | " in x and not x.startswith(("| 网站 |", "|---"))}
    assert (cells[SITE_A], cells[SITE_B], cells["sites.google.com/x"]) == ("12", "0", "—")
    assert len({len(x.split(" | ")) for x in text.splitlines() if x.startswith("| ")}) == 1  # one width, every row
    # a database built before pipeline-7 has no such column: nothing to show
    old = _report_db(tmp_path / "old.db", DOCS)
    with sqlite3.connect(old) as conn:
        conn.execute("DROP TABLE sites")
        conn.execute(SITES_DDL)
        conn.executemany("INSERT INTO sites VALUES (?, ?, ?, ?, ?, ?, ?, ?)", SITE_ROWS)
    with _ro(old) as conn:
        assert {r.under_read for r in site_report.site_rows(conn, today=date(2026, 11, 8))} == {None}

def test_site_report_never_writes_where_the_kit_must_not(tmp_path):
    db = _report_db(tmp_path / "c.db", DOCS)
    out = REPO / "research" / "skeleton_2026-11-01" / "site_report.md"
    assert site_report.main(["--db", str(db), "--out", str(out)]) == 2
    assert not out.exists()


# ---- lanes.sh, with a stand-in interpreter ------------------------------------------------------------------------

FAKE_PY = r"""#!/usr/bin/env bash
# lanes.sh's interpreter in these tests: converter runs and retry_cut_members.py are recorded and answered from
# $FAKE_DIR (codes.<export folder name>: one exit code a line, then 0; hook.<name>: a script run once during a
# conversion of that export); the GPU check answers $FAKE_GPU_RC; anything else (build_helpers.py) runs for real.
log=$FAKE_DIR/calls.log
if [[ ${1:-} == -m && ${2:-} == nknu.totext ]]; then
  printf 'convert %s\n' "${*:3}" >> "$log"
  args=("$@")
  export_dir=""
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ ${args[i]} == --export ]]; then export_dir=${args[i + 1]}; fi
  done
  name=$(basename "$export_dir")
  if [[ -e $FAKE_DIR/hook.$name ]]; then bash "$FAKE_DIR/hook.$name"; rm -f "$FAKE_DIR/hook.$name"; fi
  codes=$FAKE_DIR/codes.$name
  code=0
  if [[ -s $codes ]]; then
    code=$(head -n 1 "$codes")
    tail -n +2 "$codes" > "$codes.tmp"
    mv "$codes.tmp" "$codes"
  fi
  exit "$code"
fi
if [[ ${1:-} == *retry_cut_members.py ]]; then
  printf 'fix %s\n' "${*:2}" >> "$log"
  args=("$@")
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ ${args[i]} == --jobs ]]; then cp "${args[i + 1]}" "$FAKE_DIR/jobs.json"; fi
  done
  exit 0
fi
if [[ ${1:-} == - ]]; then
  cat > /dev/null
  exit "${FAKE_GPU_RC:-0}"
fi
exec "$REAL_PY" "$@"
"""


def _work(tmp_path: Path, exports: dict[str, str]) -> tuple[Path, Path, dict]:
    """A /work of its own for lanes.sh: the state folder, the converter's path, the defer list, the exports and
    their list; the stand-in interpreter. The helper and the repair script are the ones beside lanes.sh (11-19: the
    programs are in the repository) and take this folder for the project by NKNU_LANE_WORK."""
    work = tmp_path / "work"
    kit = work / P / "lab_pc"
    kit.mkdir(parents=True)
    (work / "src" / "nknu" / "totext").mkdir(parents=True)
    (work / "src" / "nknu" / "totext" / "run.py").write_text("", encoding="utf-8")
    (work / P / "totext_defer_urls.txt").write_text("# none\n", encoding="utf-8")
    for folder, site in exports.items():
        _export(work, folder, site)
    _list(work, list(exports))
    fake = tmp_path / "fake"
    fake.mkdir()
    py = tmp_path / "fakepy"
    py.write_text(FAKE_PY, encoding="utf-8")
    py.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("RETRY_FIRST", "NKNU_ALLOW_CPU", "EXPORTS")}
    env |= {"NKNU_LANE_WORK": str(work), "NKNU_LANE_PYTHON": str(py), "REAL_PY": sys.executable,
            "FAKE_DIR": str(fake), "EXPORTS": "exports.txt", "PYTHONPATH": str(REPO / "src")}
    return work, fake, env


def _lane(lane: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(KIT / "lanes.sh"), lane], check=False, env=env, capture_output=True, text=True,
                          timeout=120)


def _calls(fake: Path, what: str) -> list[str]:
    path = fake / "calls.log"
    return [c for c in path.read_text(encoding="utf-8").splitlines() if c.startswith(what)] if path.exists() else []


def _converted(fake: Path) -> list[str]:
    return _calls(fake, "convert ")


def _fixes(fake: Path) -> list[str]:
    return _calls(fake, "fix ")


def _exported(calls: list[str]) -> list[str]:
    return [c.split("--export ", 1)[1].split()[0] for c in calls]


TWO = {"exports/e1": "a.nknu.edu.tw", "exports/e2": "b.nknu.edu.tw"}


def test_lane_walks_every_export_and_runs_an_unfinished_one_again(tmp_path):
    work, fake, env = _work(tmp_path, TWO)
    (fake / "codes.e1").write_text("4\n0\n", encoding="utf-8")  # 40 minutes ran out once, then done
    before = time.time()
    r = _lane("B", {**env, "RETRY_FIRST": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _converted(fake)
    assert _exported(calls) == ["exports/e1", "exports/e1", "exports/e2"]
    # was --retry-crashed in the first run of each export; changed on purpose (review of plan 2026-11-08 T5): every
    # run retries the crashes from before the lane started, one time for all, so a file several exports share is
    # tried once and a crash past the first run's 40 minutes is reached too
    starts = {c.split("--retry-crashed-before ", 1)[1].split()[0] for c in calls if "--retry-crashed-before " in c}
    assert len(starts) == 1 and int(before) <= float(starts.pop()) <= time.time()  # whole seconds without EPOCHREALTIME
    assert all("--retry-crashed-before " in c and "--retry-crashed " not in c for c in calls)
    assert all(f"--out {P}/totext --routes page,image,data,unsupported" in c and "--max-minutes 40" in c
               and "--defer" not in c for c in calls)
    log = (work / P / "lab_pc" / "laneB.log").read_text(encoding="utf-8")
    assert "这条线的档都走过一遍了" in log and log.rstrip().splitlines()[-1].startswith("[lane B] done")
    assert "重转这次开线以前被打断的档（各一次）：是" in log


def test_lane_reads_the_list_again_when_it_is_through(tmp_path):
    work, fake, env = _work(tmp_path, TWO)
    _export(work, "exports/e3", "c.nknu.edu.tw")
    (fake / "hook.e2").write_text(f'echo exports/e3 >> "{work}/exports.txt"\n', encoding="utf-8")
    r = _lane("B", env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _exported(_converted(fake)) == ["exports/e1", "exports/e2", "exports/e3"]
    assert not any("--retry-crashed" in c for c in _converted(fake))  # no RETRY_FIRST: a crash marker is kept
    assert "清单里多了 1 份导出（现在共 3 份）" in (work / P / "lab_pc" / "laneB.log").read_text(encoding="utf-8")


def test_lane_stops_at_a_setting_error(tmp_path):
    _work_dir, fake, env = _work(tmp_path, TWO)
    (fake / "codes.e1").write_text("2\n", encoding="utf-8")
    r = _lane("B", env)
    assert r.returncode == 2
    assert _exported(_converted(fake)) == ["exports/e1"]


def test_lane_refuses_a_list_it_cannot_use(tmp_path):
    work, fake, env = _work(tmp_path, TWO)
    _list(work, ["exports/e1", "exports/missing"])
    r = _lane("B", env)
    assert r.returncode == 2 and _converted(fake) == []
    assert "exports/missing" in (work / P / "lab_pc" / "laneB.log").read_text(encoding="utf-8")


def test_lane_b_puts_back_what_a_forced_stop_cut_knowing_every_exports_files(tmp_path):
    work, fake, env = _work(tmp_path, TWO)
    inflight = work / P / "lab_pc" / "laneB.inflight"
    inflight.write_text("1762560000.5\n", encoding="utf-8")
    r = _lane("B", env)
    assert r.returncode == 0, r.stdout + r.stderr
    (fix,) = _fixes(fake)
    assert f"--lane B --cache {P}/totext/cache --jobs " in fix and fix.endswith("--after-stop 1762560000.5")
    jobs = json.loads((fake / "jobs.json").read_text(encoding="utf-8"))
    assert {j["key"] for j in jobs} == {f"{s}:{n}" for s in TWO.values() for n in "123"}
    assert not inflight.exists() and len(_converted(fake)) == 2


def test_lane_a_defers_the_journals_and_needs_a_gpu(tmp_path):
    _work_dir, fake, env = _work(tmp_path, TWO)
    r = _lane("A", env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _converted(fake)
    assert _exported(calls) == ["exports/e1", "exports/e2"]
    assert all(f"--routes pdf,office,office_old --defer {P}/totext_defer_urls.txt --max-minutes 40" in c
               for c in calls)
    assert [f.split()[1:3] for f in _fixes(fake)] == [["--lane", "A"]]  # the one-time fix, before the first run
    shutil.rmtree(fake)
    fake.mkdir()
    assert _lane("A", {**env, "FAKE_GPU_RC": "1"}).returncode == 2 and _converted(fake) == []


# ---- build.sh --dry-run -------------------------------------------------------------------------------------------


def _dry(tmp_path: Path, args: list[str], exports: dict[str, str]) -> tuple[subprocess.CompletedProcess, list[str]]:
    folders = [str(_export(tmp_path / "x", folder, site)) for folder, site in exports.items()]
    env = {k: v for k, v in os.environ.items() if k != "BUILD_OPTS"}
    env |= {"EXPORTS": str(_list(tmp_path, folders)), "PATH": f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"}
    r = subprocess.run(["bash", str(KIT / "build.sh"), "--dry-run", *args], check=False, env=env, capture_output=True,
                       text=True, timeout=120)
    return r, folders


def test_build_assembles_each_export_and_builds_one_database_from_every_pair(tmp_path):
    r, (aca, other) = _dry(tmp_path, [], {"aca": SITE_A, "web595": SITE_B})
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert f"build_helpers.py account --out {P}/totext" in out
    for folder, name in ((aca, NAME_A), (other, NAME_B)):
        assert f"nknu.content.assemble --export {folder} --totext {P}/totext --out {P}/corpus/{name}.json" in out
        assert f"{P}/corpus/assemble_report.json {P}/corpus/{name}.assemble_report.json" in out
    assert (f"nknu.content.build --input {P}/corpus/{NAME_A}.json::{aca} --input {P}/corpus/{NAME_B}.json::{other}"
            f" --out {P}/aca.db --report {P}/build_report.json") in out
    assert f"--vec-cache {P}/vec_cache.sqlite" in out
    assert f"site_report.py --db {P}/aca.db --out {P}/site_report.md" in out
    # 11-22: the night measures no questions: the 33 old ones (steps 6 and 7) only with --q33, the question bank never
    assert "offline_eval.py" not in out and "student_eval" not in out
    # 11-23: nor the real-data checks: step 8 only with --realdata, by hand after an install
    assert "check-realdata" not in out and "步骤 8" not in out


def test_the_33_old_questions_come_back_with_q33(tmp_path):
    r, _ = _dry(tmp_path, ["--q33"], {"aca": SITE_A})
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert f"offline_eval.py gold --db {P}/aca.db --corpus {P}/corpus/{NAME_A}.json --out {P}/gold_docs.json" in out
    assert f"offline_eval.py run --db {P}/aca.db --gold {P}/gold_docs.json" in out
    assert out.index("步骤 5 ") < out.index("步骤 6") < out.index("步骤 7")
    assert "student_eval" not in out and "check-realdata" not in out


@pytest.mark.parametrize("args", [["--realdata"], ["--from", "8"]])
def test_the_real_data_checks_run_with_realdata_or_from_8(tmp_path, args):
    r, _ = _dry(tmp_path, args, {"aca": SITE_A})
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert f"check-realdata --db {P}/aca.db --out {P}/checks.md" in out
    assert "offline_eval.py" not in out  # the 33 questions stay with --q33
    if args == ["--from", "8"]:
        assert "nknu.content.build" not in out  # from step 8 on: no build
    else:
        assert out.index("步骤 5 ") < out.index("步骤 8")


def test_build_gives_the_aca_pair_first_whatever_the_list_order(tmp_path):
    # the build's meta corpus and export_dir are its first input's, which step 8 (check_realdata) reads (plan T4)
    r, (other, aca) = _dry(tmp_path, [], {"web595": SITE_B, "aca": SITE_A})
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"nknu.content.build --input {P}/corpus/{NAME_A}.json::{aca} --input {P}/corpus/{NAME_B}.json::{other}" \
        in r.stdout


def test_build_puts_a_test_database_and_all_it_writes_in_a_folder_of_its_own(tmp_path):
    r, (other,) = _dry(tmp_path, ["--db", f"{P}/aca-kit.db"], {"web595": SITE_B})
    assert r.returncode == 0, r.stdout + r.stderr
    out, t = r.stdout, f"{P}/aca-kit"
    assert f"build_helpers.py account --out {t}/totext" in out
    assert f"--totext {P}/totext --out {t}/corpus/{NAME_B}.json" in out
    assert f"--input {t}/corpus/{NAME_B}.json::{other} --out {P}/aca-kit.db --report {t}/build_report.json" in out
    assert f"--vec-cache {P}/vec_cache.sqlite" in out  # the shared cache: a test build reuses the vectors
    assert f"site_report.py --db {P}/aca-kit.db --out {t}/site_report.md" in out
    assert "offline_eval.py" not in out and "check-realdata" not in out  # no 教务处 in the list: steps 6-9 skipped
    assert "步骤 6" in out


@pytest.mark.parametrize("db", [
    "research/skeleton_2026-11-01/aca.db", "archive-export/aca.db", "data/x.db", f"{P}/corpus.db", f"{P}/totext.db",
    f"{P}/aca.sqlite",
    # review of plan 2026-11-08 T5: their folders are the skeleton's, the question set's (step 6 wrote its gold over
    # research/eval_2026-11-01/gold_docs.json) and ./data; a test database sits directly in
    # research/pipeline_2026-11-02/, under a plain name whose folder is none of the default build's
    "research/skeleton_2026-11-01.db", "research/eval_2026-11-01.db", "data.db", f"{P}/sub/x.db",
    f"{P}/../eval_2026-11-01.db", f"{P}/.db", f"{P}/lab_pc.db", "/tmp/x.db"])
def test_build_refuses_a_database_where_the_kit_must_not_write(tmp_path, db):
    r, _ = _dry(tmp_path, ["--db", db], {"aca": SITE_A})
    assert r.returncode == 2 and "--db" in r.stderr
    assert "nknu.content" not in r.stdout  # refused before any command


def test_build_from_a_check_step_needs_the_aca_export(tmp_path):
    r, _ = _dry(tmp_path, ["--from", "7"], {"web595": SITE_B})
    assert r.returncode == 2 and "教务处" in r.stdout


# ---- site_report.py: four more reminders, what is new this build (spec 2026-11-16 §五, plan 2026-11-16 T4) ---------------

SITE_C = "sso.nknu.edu.tw/web/700"
FRAGMENT = "\n".join(["一", "二", "三", "四", "五", "六", "七", "八", "九"] + [f"第 {i} 行的文字比較長" for i in range(11)])
STEADY = "\n".join(f"第 {i} 行的文字比較長，是一段完整的句子。" for i in range(24))


def _many(site: str = SITE_C) -> list[tuple]:
    """A site with the four things the report now looks for: 15 files of one title, 21 pages of 30 opening alike,
    5 files read as scattered characters; SITE_A beside it with none of them."""
    menu = "系統訊息：是確定否取消。" + "法規彙編系統平台秘書室首頁行政部門學術單位研究及服務中心" * 3
    docs = [(f"dt{i:02}", site, "attachment", "相關檔案", f"第 {i} 份檔案的內容。", "2026-01-01", "primary") for i in range(15)]
    docs += [(f"do{i:02}", site, "page", f"法規第 {i} 條", f"{menu}第 {i} 條的條文。", "2026-01-01", "primary") for i in range(21)]
    docs += [(f"dx{i:02}", site, "page", f"公告第 {i} 則", f"第 {i} 則公告，內容各不相同，寫得夠長才算得上一個開頭，所以再多寫幾個字湊滿四十個字。",
              "2026-01-01", "primary") for i in range(9)]
    docs += [(f"df{i:02}", site, "attachment", f"流程圖{i}", FRAGMENT, "2026-01-01", "primary") for i in range(5)]
    docs += [("dg01", site, "attachment", "完整的檔", STEADY, "2026-01-01", "primary")]
    return docs


def _many_db(tmp_path, name="m.db", docs=None, spikes=None):
    sites = [*SITE_ROWS, (SITE_C, "法規網", "秘書室", "2026-11-15", 0, 0, 51, "exports/700")]
    db = _report_db(tmp_path / name, DOCS + (_many() if docs is None else docs), sites=sites)
    if spikes is not None:
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('date_spikes_json', ?)", (json.dumps(spikes),))
    return db


def test_site_report_flags_shared_titles_openings_and_scattered_text(tmp_path):
    db = _many_db(tmp_path, spikes={"on": True, "min": 20, "days": 1, "documents": 26, "by_site": {SITE_C: 26}})
    with _ro(db) as conn:
        by = {r.site: r for r in site_report.site_rows(conn, today=date(2026, 11, 16))}
    c = by[SITE_C]
    assert (c.same_title, c.same_title_top) == (15, ("相關檔案", 15))
    assert c.opening == (21, 30)  # of the 30 pages with an opening, 21 open alike: over three in ten
    assert c.fragments == 5 and c.spike_dates == 26
    assert {"same_title", "same_opening", "fragments"} <= set(c.flag_kinds)
    assert "同名标题 15 份（最多的「相關檔案」15 份）" in c.flags and "开头相同 21 / 30" in c.flags and "碎字附件 5 份" in c.flags
    a = by[SITE_A]
    assert (a.same_title, a.opening, a.fragments, a.spike_dates) == (0, None, 0, 0) and a.flag_kinds == []


def test_site_report_reminders_have_floors(tmp_path):
    fewer = [d for d in _many() if d[0] not in ("dt14", "do20", "df04")]  # 14 of a title, 20 of 29, 4 scattered
    with _ro(_many_db(tmp_path, docs=fewer)) as conn:
        c = next(r for r in site_report.site_rows(conn, today=date(2026, 11, 16)) if r.site == SITE_C)
    assert (c.same_title, c.fragments, c.spike_dates) == (0, 4, None)  # a database without the spike count: None
    assert c.opening == (20, 29) and "same_opening" in c.flag_kinds  # 20 of 29 is still over three in ten
    assert "same_title" not in c.flag_kinds and "fragments" not in c.flag_kinds
    assert (site_report.SAME_TITLE, site_report.FRAGMENT_MIN, site_report.OPENING_PAGES) == (15, 5, 20)


def test_site_report_writes_the_checks_file_and_names_what_is_new(tmp_path):
    out, checks = tmp_path / "site_report.md", tmp_path / "site_checks.json"
    plain = _many_db(tmp_path, "plain.db", docs=[])
    assert site_report.main(["--db", str(plain), "--out", str(out), "--checks", str(checks), "--today", "2026-11-16"]) == 0
    before = json.loads(checks.read_text(encoding="utf-8"))
    assert before["sites"][SITE_B]["flags"] == ["self", "empty", "no_date"] and before["sites"][SITE_C]["flags"] == ["none"]
    assert "这次新出现的提醒" not in out.read_text(encoding="utf-8")  # nothing to compare with
    prev = tmp_path / "prev.json"
    prev.write_text(checks.read_text(encoding="utf-8"), encoding="utf-8")
    assert site_report.main(["--db", str(_many_db(tmp_path)), "--out", str(out), "--checks", str(checks), "--prev", str(prev),
                             "--today", "2026-11-16"]) == 0
    text = out.read_text(encoding="utf-8")
    new = text.split("## 这次新出现的提醒")[1].split("\n## ")[0]
    assert SITE_C in new and "同名标题" in new and "开头相同" in new and "碎字附件" in new
    assert SITE_B not in new  # its three reminders were there before
    now = json.loads(checks.read_text(encoding="utf-8"))
    c = now["sites"][SITE_C]
    assert {"same_title", "same_opening", "fragments"} <= set(c["flags"]) and c["self"][1] == 20
    assert {k: c[k] for k in ("docs", "same_title", "same_opening", "fragments", "spike_dates", "no_date", "empty_pages")} == {
        "docs": 51, "same_title": 15, "same_opening": [21, 30], "fragments": 5, "spike_dates": None, "no_date": [0, 51],
        "empty_pages": [0, 30]}
    assert [SITE_C, "same_title"] in now["new"] and all(site == SITE_C for site, _ in now["new"])
    # a previous file that cannot be read is no reason to fail: the report says so and lists nothing as new
    prev.write_text("{", encoding="utf-8")
    assert site_report.main(["--db", str(_many_db(tmp_path, "again.db")), "--out", str(out), "--prev", str(prev)]) == 0
    assert "读不了上一次的检查" in out.read_text(encoding="utf-8")
