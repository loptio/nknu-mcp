"""night_sites.py (11-23): each swap's numbers by site, one file a night, written after a swap and backfilled from the
history and the reports the builds left. Light: stand-in libraries, reports and exports in a temporary repository."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parents[2] / "deploy" / "lab-pipeline"
P = "research/pipeline_2026-11-02"
ACA, CHAIR = "c.nknu.edu.tw/aca", "sso.nknu.edu.tw/site/207016"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ns = _load("night_sites", KIT / "night_sites.py")


def _library(path: Path, built_at: str, sites: dict[str, tuple[int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                       "CREATE TABLE sites (site TEXT PRIMARY KEY, docs INTEGER, export TEXT);")
    conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (built_at,))
    conn.executemany("INSERT INTO sites VALUES (?, ?, ?)", [(s, n, e) for s, (n, e) in sites.items()])
    conn.commit()
    conn.close()


def _export(repo: Path, folder: str, rows: int) -> str:
    d = repo / "archive-export" / "sites" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"项目": [{"n": i} for i in range(rows)]}, ensure_ascii=False),
                                     encoding="utf-8")
    return f"archive-export/sites/{folder}"


def _reports(folder: Path, exports: dict[str, tuple[str, int, int]], files: dict[str, list[str]]) -> None:
    """build_report.json (inputs: corpus name and records; sites: documents and export) and totext/accounting.jsonl
    (files: corpus name -> each file's status)."""
    folder.mkdir(parents=True, exist_ok=True)
    report = {"inputs": [{"corpus": f"{P}/corpus/{name}.json", "export": e, "site": site, "records": records}
                         for site, (e, records, _docs) in exports.items() for name in [site.replace("/", "_")]],
              "sites": [{"site": site, "docs": docs, "export": e} for site, (e, _records, docs) in exports.items()]}
    (folder / "build_report.json").write_text(json.dumps(report), encoding="utf-8")
    (folder / "totext").mkdir(exist_ok=True)
    lines = [json.dumps({"key": f"{name}:{i + 1}", "status": status}) for name, statuses in files.items()
             for i, status in enumerate(statuses)]
    (folder / "totext" / "accounting.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _at(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def test_the_swap_in_place_is_written_by_site_with_what_it_replaced(tmp_path):
    repo, pipe = tmp_path, tmp_path / P
    old_aca, new_aca, chair = _export(repo, "aca-1122", 50), _export(repo, "aca-1123", 52), _export(repo, "chair-1115", 9)
    _library(pipe / "aca.prev.db", "2026-11-22T03:01:46+08:00", {ACA: (40, old_aca), CHAIR: (7, chair)})
    _library(pipe / "aca.db", "2026-11-23T02:53:09+08:00", {ACA: (44, new_aca), CHAIR: (7, chair)})
    _reports(pipe, {ACA: (new_aca, 49, 44), CHAIR: (chair, 8, 7)},
             {"c.nknu.edu.tw_aca": ["ok"] * 40 + ["empty", "failed"], "sso.nknu.edu.tw_site_207016": ["ok"] * 8})
    (pipe / "aca.swap.json").write_text(json.dumps({"departures": {
        "checked": True, "left": 14, "explained": {"merged": 11, "school_no_data": 1}, "unexplained": {"kind_not_kept": 2},
        "unexplained_total": 2, "by_site": {ACA: 2}}}), encoding="utf-8")
    out = ns.record_now(repo, started_at="2026-11-23T02:39:12+08:00", finished_at="2026-11-23T03:14:34+08:00",
                        how="run")
    assert out == pipe / "nights" / "2026-11-23.json"
    night = json.loads(out.read_text(encoding="utf-8"))
    (swap,) = night["swaps"]
    assert night["day"] == "2026-11-23" and (swap["how"], swap["backfilled"], swap["reports"]) == ("run", False, P)
    assert (swap["built_at"], swap["prev_built_at"]) == ("2026-11-23T02:53:09+08:00", "2026-11-22T03:01:46+08:00")
    assert swap["departures"] == {"left": 14, "explained": {"merged": 11, "school_no_data": 1},
                                  "unexplained": {"kind_not_kept": 2}}
    assert swap["sites"][ACA] == {"export": new_aca, "export_changed": True, "export_rows": 52, "records": 49,
                                  "totext": {"empty": 1, "failed": 1, "ok": 40}, "docs": 44, "docs_before": 40,
                                  "unexplained_left": 2}
    c = swap["sites"][CHAIR]
    assert (c["export_changed"], c["export_rows"], c["totext"], c["unexplained_left"]) == (False, 9, {"ok": 8}, 0)
    # the same run written again replaces its own; another swap of that day follows it
    ns.record_now(repo, started_at="2026-11-23T02:39:12+08:00", finished_at="2026-11-23T03:14:34+08:00", how="run")
    ns.record_now(repo, started_at="2026-11-23T14:02:00+08:00", finished_at="2026-11-23T14:30:00+08:00", how="promote")
    swaps = json.loads(out.read_text(encoding="utf-8"))["swaps"]
    assert [s["started_at"][11:16] for s in swaps] == ["02:39", "14:02"] and swaps[1]["how"] == "promote"


def test_a_cleared_export_folder_and_a_missing_accounting_are_null_not_zero(tmp_path):
    repo, pipe = tmp_path, tmp_path / P
    _library(pipe / "aca.db", "2026-11-23T02:53:09+08:00", {ACA: (44, "archive-export/sites/aca-gone")})
    out = ns.record_now(repo, started_at="2026-11-23T02:39:12+08:00", finished_at=None, how="run")
    (swap,) = json.loads(out.read_text(encoding="utf-8"))["swaps"]
    a = swap["sites"][ACA]
    assert (a["export_rows"], a["records"], a["totext"], a["docs_before"], a["export_changed"]) == (None,) * 5
    assert a["unexplained_left"] is None and swap["departures"] is None and swap["prev_built_at"] is None


def test_backfill_gives_each_swap_the_reports_its_build_wrote(tmp_path):
    repo, pipe = tmp_path, tmp_path / P
    aca1, aca2, chair = _export(repo, "aca-1122", 50), _export(repo, "aca-1123", 52), "archive-export/sites/chair-gone"
    kept = pipe / "lab_pc" / "previous"
    # the 11-22 run's build reports, kept when the 11-23 build began (build.sh copies them with their times)
    _reports(kept / "20261123-024414", {ACA: (aca1, 47, 40), CHAIR: (chair, 8, 7)}, {"c.nknu.edu.tw_aca": ["ok"] * 45})
    os.utime(kept / "20261123-024414" / "build_report.json", (_at("2026-11-22T03:01:00+08:00"),) * 2)
    # a trial's own kept reports are never a swap's
    _reports(kept / "20261122-100000-trial", {ACA: (aca1, 1, 1)}, {})
    # the 11-23 run's, still in the pipeline folder
    _reports(pipe, {ACA: (aca2, 49, 44), CHAIR: (chair, 8, 7)}, {"c.nknu.edu.tw_aca": ["ok"] * 46})
    os.utime(pipe / "build_report.json", (_at("2026-11-23T02:53:00+08:00"),) * 2)
    _library(pipe / "aca.prev.db", "2026-11-22T03:01:46+08:00", {ACA: (40, aca1), CHAIR: (7, chair)})
    history = [
        {"started_at": "2026-11-21T03:39:33+08:00", "finished_at": "2026-11-21T04:41:40+08:00", "outcome": "swapped",
         "library": {"before": {"built_at": "a"}, "after": {"built_at": "2026-11-21T04:00:00+08:00"}}},
        {"started_at": "2026-11-22T02:48:04+08:00", "finished_at": "2026-11-22T03:40:32+08:00", "outcome": "swapped",
         "library": {"before": {"built_at": "2026-11-21T04:00:00+08:00"}, "after": {"built_at": "2026-11-22T03:01:46+08:00"}},
         "departures": {"checked": True, "left": 22, "explained": {"merged": 22}, "unexplained": {}, "by_site": {}}},
        {"started_at": "2026-11-22T14:00:00+08:00", "finished_at": "2026-11-22T14:01:00+08:00", "outcome": "paused"},
        {"started_at": "2026-11-23T02:39:12+08:00", "finished_at": "2026-11-23T03:14:34+08:00", "outcome": "swapped",
         "library": {"before": {"built_at": "2026-11-22T03:01:46+08:00"}, "after": {"built_at": "2026-11-23T02:53:09+08:00"}},
         "departures": {"checked": True, "left": 14, "explained": {"merged": 11}, "unexplained": {"kind_not_kept": 2},
                        "by_site": {ACA: 2}}, "promoted": None},
    ]
    (pipe / "ingest-history.jsonl").write_text("\n".join(json.dumps(h) for h in history) + "\n", encoding="utf-8")
    written = ns.backfill(repo)
    assert sorted({p.name for p in written}) == ["2026-11-21.json", "2026-11-22.json", "2026-11-23.json"]
    read = {p.stem: json.loads(p.read_text(encoding="utf-8"))["swaps"] for p in set(written)}
    (first,) = read["2026-11-21"]  # its reports are gone: the swap is kept without sites
    assert first["sites"] == {} and first["reports"] is None and first["backfilled"] is True
    (second,) = read["2026-11-22"]
    assert second["reports"] == f"{P}/lab_pc/previous/20261123-024414"
    assert second["sites"][ACA]["docs"] == 40 and second["sites"][ACA]["docs_before"] is None  # nothing known before
    assert second["sites"][ACA]["totext"] == {"ok": 45} and second["sites"][ACA]["records"] == 47
    assert second["sites"][CHAIR]["export_rows"] is None  # the crawler cleared that folder
    (third,) = read["2026-11-23"]
    assert third["reports"] == P and third["sites"][ACA]["docs_before"] == 40 and third["sites"][ACA]["docs"] == 44
    assert third["sites"][ACA]["export_changed"] is True and third["sites"][ACA]["unexplained_left"] == 2
    assert third["prev_built_at"] == "2026-11-22T03:01:46+08:00"
