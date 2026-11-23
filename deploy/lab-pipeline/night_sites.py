"""Each swap's numbers by site, one file a night (11-23, asked by the panels' session: a site's numbers of a night are
gone once the next build writes its reports over them), in research/pipeline_2026-11-02/nights/<the day the run
started>.json (a run is named by the day it starts, 11-23):

    {"day": "2026-11-24", "swaps": [swap, ...]}      one swap a night as a rule; a day's swaps by hand follow in order
    swap: {"started_at", "finished_at", "how": "run" | "promote", "backfilled", "reports", "built_at",
           "prev_built_at", "departures": {"left", "explained", "unexplained"} | null,
           "sites": {site: {"export", "export_changed", "export_rows", "records", "totext", "docs", "docs_before",
                            "unexplained_left"}}}

Read, by site:
- export and docs: the new library's sites table (backfilled: the build report of the build that made it); docs_before
  and export_changed against the library it replaced (aca.prev.db; backfilled: the swap before's), null when unknown;
- export_rows: the rows (项目) of that export's manifest.json, null once the crawler has cleared the folder;
- records: build_report.json inputs[].records, what assemble made of that export;
- totext: the files of that export in totext/accounting.jsonl (key <corpus name>:<row>) by status, null without it;
- unexplained_left: the swap guard's departures by_site (aca.swap.json; backfilled: the history line's), whose
  explained and unexplained counts for the whole library are the swap's departures.
how: run, the ingest's run (the night's, or one by hand in the day); promote, a checked trial put in place.
reports names the folder the build reports were read in. What came in, changed and left, and why, are the panels'
own (nknu.ops.changes reads both libraries for them).

nightly_ingest writes a swap's file once the swap is told (a failure is only said: the swap stands). By hand:
    python deploy/lab-pipeline/night_sites.py now        the swap in place now (aca.db against aca.prev.db), its times
                                                          from ingest-status.json
    python deploy/lab-pipeline/night_sites.py backfill   every swap of ingest-history.jsonl, each with the reports its
                                                          build left (lab_pc/previous/<time>/, kept by build.sh)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PIPE = "research/pipeline_2026-11-02"  # as nightly_ingest.PIPE
NIGHTS = "nights"
KEPT = re.compile(r"\d{8}-\d{6}")  # lab_pc/previous/<time>: an official build's earlier reports (a trial's add -<name>)


def library(db: Path) -> dict | None:
    """A library's built_at and, by site, its documents and the export folder it read; None without one."""
    if not db.is_file():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        built = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        sites = {s: {"docs": n or 0, "export": e or ""} for s, n, e in conn.execute("SELECT site, docs, export FROM sites")}
        return {"built_at": built[0] if built else "", "sites": sites}
    finally:
        conn.close()


def from_report(report: dict, built_at: str | None) -> dict:
    """The library a build report describes, as library() gives it (backfill)."""
    sites = {s["site"]: {"docs": s.get("docs") or 0, "export": s.get("export") or ""}
             for s in report.get("sites") or [] if isinstance(s, dict) and s.get("site")}
    return {"built_at": built_at or "", "sites": sites}


def _inputs(report: dict) -> dict[str, dict]:
    """build_report.json inputs by site: records, and the corpus name the to-text accounting keys its files by."""
    return {i["site"]: {"records": i.get("records"), "name": Path(str(i.get("corpus") or "")).stem}
            for i in report.get("inputs") or [] if isinstance(i, dict) and i.get("site")}


def to_text(accounting: Path) -> dict[str, dict[str, int]] | None:
    """totext/accounting.jsonl's files by export (the corpus name before ':' in its key) and status; None without it."""
    if not accounting.is_file():
        return None
    out: dict[str, Counter] = {}
    with accounting.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if name := str(row.get("key") or "").split(":", 1)[0]:
                out.setdefault(name, Counter())[str(row.get("status") or "?")] += 1
    return {name: dict(sorted(seen.items())) for name, seen in out.items()}


def export_rows(repo: Path, export: str) -> int | None:
    """The rows (项目) of an export folder's manifest.json; None when the folder is gone or unreadable."""
    if not export:
        return None
    try:
        items = json.loads((repo / export / "manifest.json").read_text(encoding="utf-8")).get("项目")
    except (OSError, ValueError, AttributeError):
        return None
    return len(items) if isinstance(items, list) else None


def swap_record(repo: Path, *, started_at: str, finished_at: str | None, how: str, after: dict, before: dict | None,
                report: dict | None, accounting: Path | None, departures: dict | None, reports: str | None,
                backfilled: bool = False) -> dict:
    inputs = _inputs(report or {})
    text = to_text(accounting) if accounting is not None else None
    checked = bool(departures and departures.get("checked"))
    by_site = (departures or {}).get("by_site") or {}
    sites = {}
    for site, now in sorted(after["sites"].items()):
        was = (before or {}).get("sites", {}).get(site) if before is not None else None
        inp = inputs.get(site, {})
        sites[site] = {"export": now["export"],
                       "export_changed": None if was is None else Path(was["export"]).name != Path(now["export"]).name,
                       "export_rows": export_rows(repo, now["export"]), "records": inp.get("records"),
                       "totext": None if text is None else text.get(inp.get("name") or "", {}),
                       "docs": now["docs"], "docs_before": None if was is None else was["docs"],
                       "unexplained_left": int(by_site.get(site, 0)) if checked else None}
    return {"started_at": started_at, "finished_at": finished_at, "how": how, "backfilled": backfilled,
            "reports": reports, "built_at": after.get("built_at") or None,
            "prev_built_at": (before or {}).get("built_at") or None,
            "departures": {k: departures.get(k) for k in ("left", "explained", "unexplained")} if checked else None,
            "sites": sites}


def write_swap(pipe: Path, record: dict) -> Path:
    """The swap into nights/<the day its run started>.json, beside that day's other swaps (one of the same start is
    replaced), whole and renamed into place."""
    day = str(record["started_at"])[:10]
    out = pipe / NIGHTS / f"{day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    swaps: list[dict] = []
    if out.is_file():
        try:
            swaps = [s for s in json.loads(out.read_text(encoding="utf-8")).get("swaps") or [] if isinstance(s, dict)]
        except (OSError, ValueError, AttributeError):
            swaps = []
    swaps = sorted([s for s in swaps if s.get("started_at") != record["started_at"]] + [record],
                   key=lambda s: str(s.get("started_at") or ""))
    tmp = out.with_name(out.name + ".new")
    tmp.write_text(json.dumps({"day": day, "swaps": swaps}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return out


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_now(repo: Path, *, started_at: str, finished_at: str | None, how: str) -> Path:
    """The swap in place now: aca.db against aca.prev.db (the library it replaced), the reports beside them and the
    swap guard's file (nightly_ingest, after a swap and after a promote)."""
    pipe = repo / PIPE
    after = library(pipe / "aca.db")
    if after is None:
        raise FileNotFoundError(pipe / "aca.db")
    before = library(pipe / "aca.prev.db")
    if before is not None and before["built_at"] == after["built_at"]:
        before = None  # the same library under both names: nothing was replaced
    told = _read(pipe / "aca.swap.json") or {}
    return write_swap(pipe, swap_record(repo, started_at=started_at, finished_at=finished_at, how=how, after=after,
                                        before=before, report=_read(pipe / "build_report.json"),
                                        accounting=pipe / "totext" / "accounting.jsonl",
                                        departures=told.get("departures"), reports=PIPE))


def _when(stamp: object) -> float:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except ValueError:
        return float("nan")


def backfill(repo: Path) -> list[Path]:
    """A file for every swap ingest-history.jsonl holds. A swap's reports are the ones its build wrote: of the kept
    folders (lab_pc/previous/<time>, an official build's: build.sh copies with their times) and the pipeline folder
    itself, the latest build_report.json written before the swap ended and after the swap before it ended. Without
    any, the swap is written without sites. The last swap's library before it is aca.prev.db when that is it."""
    pipe = repo / PIPE
    history = pipe / "ingest-history.jsonl"
    lines = [json.loads(x) for x in history.read_text(encoding="utf-8").splitlines() if x.strip()]
    swaps = [h for h in lines if h.get("outcome") == "swapped"]
    kept = pipe / "lab_pc" / "previous"
    folders = [d for d in sorted(kept.iterdir()) if d.is_dir() and KEPT.fullmatch(d.name)] if kept.is_dir() else []
    sets = sorted(((d / "build_report.json").stat().st_mtime, d) for d in [*folders, pipe]
                  if (d / "build_report.json").is_file())
    prior = library(pipe / "aca.prev.db")
    written: list[Path] = []
    last_end, previous = float("-inf"), None
    for n, h in enumerate(swaps):
        end = _when(h.get("finished_at"))
        lib = h.get("library") or {}
        built = (lib.get("after") or {}).get("built_at")
        was_built = (lib.get("before") or {}).get("built_at")
        chosen = [d for t, d in sets if last_end < t <= end]
        last_end = end
        folder = chosen[-1] if chosen else None
        report = _read(folder / "build_report.json") if folder is not None else None
        after = from_report(report, built) if report is not None else {"built_at": built or "", "sites": {}}
        before = previous if previous is not None and previous.get("built_at") == was_built else None
        if n == len(swaps) - 1 and prior is not None and prior["built_at"] == was_built:
            before = prior
        record = swap_record(repo, started_at=h["started_at"], finished_at=h.get("finished_at"),
                             how="promote" if h.get("promoted") else "run", after=after, before=before,
                             report=report, accounting=None if folder is None else folder / "totext" / "accounting.jsonl",
                             departures=h.get("departures"),
                             reports=None if folder is None else str(folder.relative_to(repo)), backfilled=True)
        written.append(write_swap(pipe, record))
        previous = after if report is not None else None
    return written


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_helpers as bh

    repo = Path(os.environ.get("NKNU_LANE_WORK") or bh.REPO)
    command = argv[0] if argv else ""
    if command == "backfill":
        written = backfill(repo)
        print(f"补了 {len(written)} 次换库，写在 {len(set(written))} 个档：{repo / PIPE / NIGHTS}")
        return 0
    if command == "now":
        status = _read(repo / PIPE / "ingest-status.json") or {}
        after = library(repo / PIPE / "aca.db")
        if status.get("outcome") != "swapped" or after is None or \
                ((status.get("library") or {}).get("after") or {}).get("built_at") != after["built_at"]:
            print("ingest-status.json 写的不是现在这份库的换库，不写", file=sys.stderr)
            return 1
        out = record_now(repo, started_at=status["started_at"], finished_at=status.get("finished_at"),
                         how="promote" if status.get("promoted") else "run")
        print(f"写在 {out}")
        return 0
    print("用法：night_sites.py now｜backfill", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
