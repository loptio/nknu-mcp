"""檔案: each nightly step's input files and output files, and a look inside one (owner 11-23: 「爬取…输入是
archive_round1.json, js_sites.txt…输出成warc.gz和frontier.db」, then of the YAML: 「我现在打开yaml文件很难看，其他文件也是
一样做一下优化」). The design copy (its scratchpad fileview.js, fullview.py, full.py and content_night.py's LINEAGE)
probed the lab once and baked every file's size, time and contents into the page; here each is read when asked:

  files(where, step)       the map of every step's files (name, one line of what it is, who reads and who writes it)
                           and, for one step, each of its files as it stands now: exists, size, time, a folder's count
  file(where, key, params) one file by its key (never a path from a request): the same, and a look inside it, parsed
                           for the eye (YAML and list files as tables, a log's last lines, JSON as fields, a table's
                           first 300 lines, a database's tables)

Where a file is follows from the service's own places (where.root /work, where.archive /crawl/archive and the crawl
volume above it, the library's folder), never from a hard-coded lab path, so a test's folders work the same. The map
was surveyed by hand from nightly_update.sh, export_sites.sh, nightly_ingest.py and build.sh (the design copy, 11-23)
and brought up to date with what they write since (coverage.jsonl, warc-backup.json, nights/<day>.json,
site_checks.json; checks.md left the night: it is run by hand since 11-23 06:21).

The Google key is a name only: never statted, never opened (the hard rule); any path holding 'secrets' or ending in
.env is treated so too. Every string served (a log's line, a JSON value, a file's head, a YAML or list file read as a
table, a folder's names) has pipeline._SECRETS masked. A file only the lab PC holds (the D: backup) is not missing:
it is said to be where no container of the panel looks.
Folder sizes and a database's table counts cost seconds: they are worked out in a background thread, one a file at a
time, and kept by the file's identity (the ledger's, which changes all night, for pipeline.EVERY seconds), the last
count served with its time meanwhile; until there is one a chip shows what is cheap. The ledger is never counted row
by row (LIVE_DBS): its tables are said by their last rowid, read in a few page reads beside the crawl."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import yaml

from nknu.core.timeutil import TAIPEI
from nknu.ops import pipeline
from nknu.ops.pipeline import EXPORTS, Where, current_exports, folder_of, library_facts, open_ro, read_json, when_of
from nknu.ops.raw import mask, mask_all

# GET /api/file for web.py to wire (port contract 11-23): ?step= gives the map and that step's files, ?key= one file
# (&site= a site key for the per-site export files). The handler returns (status, body) for a JSONResponse.
ROUTES_TO_WIRE = [
    {"path": "/api/file", "method": "GET", "handler": "api", "args": "where, request.query_params",
     "returns": "(status: int, body: dict) -> JSONResponse(body, status_code=status, headers={'Cache-Control': "
                "'no-store'}); ?step=<step>[&site=] -> files(); ?key=<key>[&site=] -> file(); 404 {'error'} for a key "
                "or step that is not in the map"},
]

log = logging.getLogger(__name__)

# every file the steps read or write: key -> (name, what it is in one line, where: (base, path under it)). Bases:
# crawl the crawler's volume (/crawl), archive its archive folder (/crawl/archive), exports root/archive-export/sites,
# pipe root/research/pipeline_2026-11-02, kit pipe/lab_pc, lib the library's folder, config root/config, host a
# file of the lab PC no container of the panel mounts (its name only)
FILES: dict[str, tuple[str, str, str, str]] = {
    "round1": ("archive_round1.json", "網站清單：每個網站抓哪些、第幾輪、多久重看", "crawl", "code-update/config/archive_round1.json"),
    "js_sites": ("js_sites.txt", "要用瀏覽器開的網站", "crawl", "code-update/lab/js_sites.txt"),
    "albums": ("album_folders.json", "Google 雲端相簿的資料夾", "crawl", "code-update/config/album_folders.json"),
    "round1_work": ("archive_round1.json（/work）", "網站清單：轉文字、建庫和面板讀這份；11-23 起和爬蟲那份一樣", "config",
                    "archive_round1.json"),
    "google_key": ("google.env", "Google 金鑰", "host", r"C:\nknu-crawl\secrets\google.env"),
    "frontier": ("frontier.db", "帳本：每個網址的狀態，邊抓邊改", "archive", "frontier.db"),
    "warc_day": ("warc/<日期>/", "原檔 .warc.gz：這一晚抓到的原始回應（資料夾照 UTC 日期）", "archive", "warc"),
    "backup_nightly": ("crawler-nightly/", "帳本每晚的備份，留 7 份（D 槽）", "host", r"D:\nknu-backup\crawler-nightly"),
    "update_status": ("update-status.json", "這一晚爬取的結果、數字和警報", "archive", "reports/update-status.json"),
    "update_pause": ("update.pause", "放了它，每晚更新就停", "archive", "update.pause"),
    "changes": ("changes-*.csv", "這一晚哪些網址新的、改了、不見了", "archive", "reports"),
    "export_owed": ("export-owed.json", "變了但還沒導出的網站", "archive", "reports/export-owed.json"),
    "schedule_term": ("schedule-term.json", "課表現在是哪個學期", "archive", "reports/schedule-term.json"),
    "update_log": ("update.log", "爬取和導出的紀錄", "archive", "update.log"),
    "coverage": ("coverage.jsonl", "每晚一行：每個網站該抓的抓到多少（抓取覆蓋）", "archive", "reports/coverage.jsonl"),
    "warc_backup": ("warc-backup.json", "原檔每晚抄一份到 D 槽的結果", "archive", "reports/warc-backup.json"),
    "warc_backup_log": ("warc-backup.log", "原檔備份的紀錄", "archive", "reports/warc-backup.log"),
    "export_sites": ("export-sites.txt", "這一晚要導出的網站", "archive", "reports/export-sites.txt"),
    "export_hold": ("export-hold.txt", "手動壓著不導的網站", "exports", "export-hold.txt"),
    "old_copies": ("copies-1108/", "10-24 舊存檔：這一次沒抓好時拿來頂替", "crawl", "old-archive-2026-10-24/copies-1108"),
    "export_folder": ("<網站>-<時間>/", "每站一個導出資料夾：網頁、檔案、圖片、清單", "exports", ""),
    "manifest": ("manifest.json", "導出資料夾的清單：每一筆網頁、檔案、圖片", "exports", "manifest.json"),
    "missing": ("missing.csv", "導出清單上缺東西的", "exports", "missing.csv"),
    "current": ("current.tsv", "每個網站現在用的導出資料夾", "exports", "current.tsv"),
    "seen": ("seen.tsv", "每個網站最後一次看到的日期", "exports", "seen.tsv"),
    "failed": ("failed.tsv", "這一晚導出失敗的網站", "exports", "failed.tsv"),
    "export_results": ("export-results.tsv", "每站導出的結果：成功、沒變、失敗、壓著", "archive", "reports/export-results.tsv"),
    "export_log": ("export.log", "導出的紀錄", "exports", "export.log"),
    "exports_txt": ("exports.txt", "庫要用的導出資料夾清單", "pipe", "exports.txt"),
    "ingest_hold": ("ingest-hold.txt", "先不進庫的網站", "pipe", "ingest-hold.txt"),
    "ingest_refused": ("ingest-refused.txt", "之前被擋回的網站", "pipe", "ingest-refused.txt"),
    "defer": ("totext_defer_urls.txt", "期刊和考古題：最後才轉", "pipe", "totext_defer_urls.txt"),
    "cache": ("totext/cache/", "轉好的文字，照內容的雜湊存，同一個檔只轉一次", "pipe", "totext/cache"),
    "laneA": ("laneA.log", "轉文字（PDF、Office）的紀錄", "kit", "laneA.log"),
    "ingest_log": ("ingest.log", "每晚進庫的紀錄", "kit", "ingest.log"),
    "acc_rows": ("accounting.jsonl", "轉文字的逐檔記帳：每個檔怎麼轉、轉出多少字", "pipe", "totext/accounting.jsonl"),
    "accounting": ("totext/summary.json", "轉文字的記帳：每站轉了多少、讀不出多少", "pipe", "totext/summary.json"),
    "corpus": ("corpus/", "每站整理好的文件（建庫的中間產物）", "pipe", "corpus"),
    "units": ("units.yaml", "學校的單位樹", "config", "units.yaml"),
    "site_units": ("site_units.yaml", "網站 → 單位", "config", "site_units.yaml"),
    "aliases": ("aliases.yaml", "同義詞", "config", "aliases.yaml"),
    "server_dates": ("server_dates.json", "伺服器給的檔案日期", "pipe", "server_dates.json"),
    "vec_cache": ("vec_cache.sqlite", "向量快取：算過的不再算", "pipe", "vec_cache.sqlite"),
    "build_report": ("build_report.json", "建庫報告：每一關收了多少、為什麼不收", "pipe", "build_report.json"),
    "build_log": ("build.log", "建庫的紀錄", "kit", "build.log"),
    "site_checks": ("site_checks.json", "建庫查出的每站提醒（品質提醒讀它）", "lib", "site_checks.json"),
    "site_check_docs": ("site_check_docs.json", "每項提醒命中哪些文件", "lib", "site_check_docs.json"),
    "aca_tmp": ("aca.db.tmp", "剛建好的新庫（換上前的名字）", "lib", "{lib}.tmp"),
    "aca": ("aca.db", "正式庫：學生搜的就是它", "lib", "{lib}"),
    "aca_prev": ("aca.prev.db", "上一份庫，出事時退回去用", "lib", "{stem}.prev{suffix}"),
    "aca_held": ("aca.held.db", "把關沒過、被擋下的新庫", "lib", "{stem}.held{suffix}"),
    "swap_json": ("aca.swap.json", "換庫時拿掉了什麼、為什麼", "lib", "{stem}.swap.json"),
    "site_report": ("site_report.md", "換上以後的逐站報告", "lib", "site_report.md"),
    "checks_md": ("checks.md", "22 項真資料檢查的結果（11-23 起裝新程式那天手動跑，不是每晚）", "lib", "checks.md"),
    "exports_official": ("exports-official.txt", "現用的庫用到的導出資料夾（保護不被刪）", "pipe", "exports-official.txt"),
    "ingest_status": ("ingest-status.json", "這一晚進庫的狀態", "pipe", "ingest-status.json"),
    "ingest_history": ("ingest-history.jsonl", "每晚一行的進庫歷史", "pipe", "ingest-history.jsonl"),
    "nights_day": ("nights/<日期>.json", "這一晚每站從導出到換庫的數字（換了庫才有）", "lib", "nights"),
}
# what each step runs, and its files in and out (CR-14: coverage, the WARC backup, the per-night file and the site
# checks added; checks.md is no nightly output since 11-23)
STEPS: dict[str, dict] = {
    "crawl": {"name": "爬取", "prog": "nightly_update.cmd → nightly_update.sh → nknu archive run（進庫之後再跑 coverage、warc-backup）",
              "in": ["round1", "js_sites", "albums", "google_key", "frontier"],
              "out": ["frontier", "warc_day", "backup_nightly", "update_status", "changes", "export_owed", "schedule_term",
                      "update_log", "coverage", "warc_backup", "warc_backup_log"]},
    "export": {"name": "導出", "prog": "export_sites.sh → nknu archive export",
               "in": ["update_status", "export_owed", "frontier", "warc_day", "old_copies", "export_hold"],
               "out": ["export_sites", "export_folder", "manifest", "missing", "current", "seen", "export_results",
                       "export_log"]},
    "totext": {"name": "轉文字", "prog": "nightly_ingest.py → lanes.sh → nknu.totext",
               "in": ["current", "exports_txt", "ingest_hold", "ingest_refused", "manifest", "round1_work", "defer"],
               "out": ["exports_txt", "cache", "acc_rows", "laneA", "ingest_log"]},
    "build": {"name": "建庫", "prog": "build.sh → nknu.content.build",
              "in": ["cache", "exports_txt", "units", "site_units", "aliases", "round1_work", "server_dates", "seen",
                     "vec_cache"],
              "out": ["accounting", "corpus", "vec_cache", "build_report", "aca_tmp", "build_log", "site_checks",
                      "site_check_docs"]},
    "swap": {"name": "換庫", "prog": "nknu.content.build（把關）→ nightly_ingest done",
             "in": ["aca_tmp", "aca"],
             "out": ["aca", "aca_prev", "swap_json", "site_report", "exports_official", "ingest_status", "ingest_history",
                     "nights_day"]},
}
ORDER = ("crawl", "export", "totext", "build", "swap")
# a file's part in a step where its name alone misleads (the design's ROLE): 換庫's aca.db goes in as the library before
# the swap (now that file is aca.prev.db)
ROLE_FROM = {("swap", "in", "aca"): "aca_prev"}
# why a file is not there, where that is normal
ABSENT = {"aca_tmp": "建好、換上以後就改名成 aca.db 了", "aca_held": "沒有被擋下的新庫",
          "failed": "這一晚沒有導出失敗的網站", "update_pause": "沒有暫停：每晚更新照常跑",
          "warc_day": "這一晚的原檔資料夾不在（還沒開跑，或沒抓到東西）",
          "nights_day": "這一晚沒有換庫，就沒有這一晚的檔", "backup_nightly": "檢視台沒掛 D 槽：備份在實驗室電腦的 D 槽",
          "changes": "還沒有這一晚的網址變動清單"}
SITE_FILES = ("export_folder", "manifest", "missing")
LOGS = {"update_log": "update", "export_log": "export", "laneA": "laneA", "build_log": "build", "ingest_log": "ingest"}
LISTS = ("js_sites", "export_sites", "export_hold", "exports_txt", "ingest_hold", "ingest_refused", "exports_official")
# a headerless table's columns (the design's HEAD)
HEADS = {"current": ["網站", "導出資料夾"], "seen": ["網站", "最後看到", "這一晚"], "export_results": ["網站", "結果"],
         "failed": ["網站", "原因"]}
JSON_WHOLE, TEXT_WHOLE, HEAD_BYTES, TABLE_LINES = 200_000, 80_000, 4096, 300  # full.py's limits
TAIL_BYTES, TAIL_LINES = 40_000, 120
ROWS_SHOWN = 300  # a manifest's rows shown in the drawer 【Claude】
COUNT_BUDGET = 20.0  # seconds spent counting a database's tables; the rest by max(rowid) 【Claude】
_BIG = re.compile(r"(?:_content|_data|_idx|_docsize|_config|_chunks|_rowids|_vector_chunks\d*|_info|_auxiliary\d*)$")
_FOLDER = re.compile(r"(?:^|/)([^/]+)-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}/?$")


def _secret(path: str) -> bool:
    return "secrets" in path.lower() or path.lower().endswith(".env")


_mask_all = mask_all  # every string inside a JSON value masked (pipeline._SECRETS)


def _tpe(ts: float) -> str:
    return datetime.fromtimestamp(ts, TAIPEI).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------------------------------------ where a file is


def _bases(where: Where) -> dict[str, Path | None]:
    archive = where.archive
    lib = where.library
    return {"crawl": archive.parent if archive is not None else None, "archive": archive,
            "exports": where.root / EXPORTS, "pipe": where.pipe, "kit": where.pipe / "lab_pc", "lib": lib.parent,
            "config": where.root / "config"}


def run_day(where: Where) -> str:
    """The run day of the library in use (the day the 01:00 run that swapped it started; a hand promote its own day)."""
    try:
        facts = library_facts(where.library)
    except (OSError, sqlite3.Error):
        return ""
    at = when_of(str(facts.get("swapped_at") or facts.get("built_at") or ""))
    return at.date().isoformat() if at else ""


def _warc_day(archive: Path) -> Path | None:
    """The WARC folder of the night's crawl: its start in UTC (update-status.json night_started); else the newest."""
    status = read_json(archive / "reports" / "update-status.json")
    at = when_of(str(status.get("night_started") or "")) if isinstance(status, dict) else None
    if at is not None:
        return archive / "warc" / at.astimezone(UTC).strftime("%Y/%m/%d")
    days = sorted(p for p in (archive / "warc").glob("*/*/*") if p.is_dir())
    return days[-1] if days else None


def site_folder(where: Where, site: str, step: str = "") -> Path | None:
    """A site's export folder: for 導出 the crawler's newest (current.tsv), else the one the library was built from
    (sites.export); the other when the first has none."""
    library = {}
    try:
        library = {s["site"]: s.get("export") or "" for s in library_facts(where.library)["sites"] if s.get("site")}
    except (OSError, sqlite3.Error):
        pass
    newest = current_exports(where.root)
    first = [newest.get(site)] if step == "export" else []
    built = folder_of(where.root, library[site]) if library.get(site) else None
    for folder in (*first, built, newest.get(site)):
        if folder is not None:
            return folder
    return None


def path_of(where: Where, key: str, site: str = "", step: str = "") -> Path | None:
    """Where a file of the map is (None: not on any mount of the panel, or not known)."""
    _name, _what, base, rel = FILES[key]
    if base == "host":
        return None
    root = _bases(where).get(base)
    if root is None:
        return None
    lib = where.library
    if key == "warc_day":
        return _warc_day(where.archive) if where.archive is not None else None
    if key == "changes":
        found = sorted((root / rel).glob("changes-*.csv"))
        return found[-1] if found else root / rel / "changes-*.csv"
    if key == "nights_day":
        found = sorted((root / rel).glob("*.json"))
        return found[-1] if found else root / rel / f"{run_day(where) or '日期'}.json"
    if key in SITE_FILES:
        if not site:
            return None
        folder = site_folder(where, site, step)
        return None if folder is None else folder if key == "export_folder" else folder / rel
    if rel.startswith("{"):
        return root / rel.format(lib=lib.name, stem=lib.stem, suffix=lib.suffix)
    return root / rel


# ------------------------------------------------------------------------------------------------ what a file is now

_sizes: dict[str, tuple[int, int | None]] = {}  # folder -> (its time of change, its files' total size)
_sizes_lock = threading.Lock()
_walking: set[str] = set()


def _walk(path: Path, mtime: int) -> None:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    finally:
        with _sizes_lock:
            _sizes[str(path)] = (mtime, total)
            _walking.discard(str(path))


def folder_size(path: Path, mtime: int, *, wait: bool = False) -> int | None:
    """A folder's files' total size, walked in the background and kept by the folder's time of change; None until
    then."""
    key = str(path)
    with _sizes_lock:
        got = _sizes.get(key)
        if got and got[0] == mtime:
            return got[1]
        if key in _walking:
            thread = None
        else:
            _walking.add(key)
            thread = threading.Thread(target=_walk, args=(path, mtime), name="folder-size", daemon=True)
            thread.start()
    if wait and thread is not None:
        thread.join(30)
        with _sizes_lock:
            got = _sizes.get(key)
        return got[1] if got and got[0] == mtime else None
    return None


def info(where: Where, key: str, site: str = "", step: str = "", *, wait: bool = False) -> dict:
    """A file of the map as it stands now: {key, name, what, path, exists, absent, size, mtime, dir, count, secret,
    host}. The Google key and any secrets path is never statted."""
    name, what, base, rel = FILES[key]
    out: dict = {"key": key, "name": name, "what": what}
    if key == "google_key" or _secret(rel):
        return {**out, "path": rel, "secret": True, "host": True}
    if base == "host":  # not missing: on the lab PC where no container of the panel looks (FD-08); the chip says so
        said = ABSENT.get(key, "檢視台沒有掛上這個資料夾")
        return {**out, "path": rel, "host": True, "absent": said, "chip": said.split("：", 1)[0]}
    path = path_of(where, key, site, step)
    if path is None:
        why = ("先在上面選一個網站，或打開看每個網站的資料夾" if key in SITE_FILES
               else "檢視台沒有掛上爬蟲的資料夾" if base in ("crawl", "archive") else "找不到它在哪")
        return {**out, "path": "", "exists": False, "absent": why, "pick": key in SITE_FILES}
    if _secret(str(path)):
        return {**out, "path": str(path), "secret": True}
    out["path"] = str(path)
    if key == "warc_day":
        out["name"] = "warc/" + "/".join(path.parts[-3:]) + "/"
    elif key == "changes" or key == "nights_day":
        out["name"] = path.name if path.exists() else name
    elif key == "export_folder":
        out["name"] = path.name + "/"
    try:
        st = path.stat()
    except OSError:
        return {**out, "exists": False, "absent": ABSENT.get(key, "這一晚沒用到")}
    out.update(exists=True, mtime=_tpe(st.st_mtime))
    if path.is_dir():
        try:
            names = sorted(p.name for p in os.scandir(path))
        except OSError:
            names = []
        out.update(dir=True, count=len(names), list=names[:15], size=folder_size(path, st.st_mtime_ns, wait=wait))
    else:
        out["size"] = st.st_size
    if key == "changes":
        out["all"] = [p.name for p in sorted(path.parent.glob("changes-*.csv"))[-8:]]
    return out


def readers(key: str) -> list[str]:
    return [STEPS[s]["name"] for s in ORDER if key in STEPS[s]["in"]]


def writers(key: str) -> list[str]:
    return [STEPS[s]["name"] for s in ORDER if key in STEPS[s]["out"]]


def lineage_map() -> dict:
    """The map the drawer draws from: {files: {key: {name, what}}, steps: {step: {name, prog, in, out}}, order}."""
    return {"files": {k: {"name": n, "what": w} for k, (n, w, _b, _r) in FILES.items()}, "steps": STEPS,
            "order": list(ORDER)}


def files(where: Where, step: str = "", site: str = "") -> dict:
    """The map, and one step's files (in, out, and the files a role reads instead) as they stand now; site: the
    site picked on the page (its export folder, manifest and missing list)."""
    keys: list[str] = []
    if step in STEPS:
        for side in ("in", "out"):
            for k in STEPS[step][side]:
                keys.append(ROLE_FROM.get((step, side, k), k))
                keys.append(k)
    seen = dict.fromkeys(keys)
    return {**lineage_map(), "step": step, "site": site, "run_day": run_day(where),
            "info": _mask_all({k: info(where, k, site, step) for k in seen})}


# ------------------------------------------------------------------------------------------------ a look inside


def _split(text: str) -> tuple[list[str], str]:
    """(intro, body): a file's comment lines at its head as paragraphs (a line ending in 。 ends one), and the rest
    (fullview.split)."""
    lines, intro, i = text.split("\n"), [], 0
    while i < len(lines) and (lines[i].lstrip().startswith("#") or not lines[i].strip()):
        intro.append(lines[i].lstrip()[1:].strip() if lines[i].lstrip().startswith("#") else "")
        i += 1
    paras, cur = [], []
    for ln in intro:
        if ln:
            cur.append(ln)
            if ln.endswith("。"):
                paras.append(" ".join(cur))
                cur = []
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return paras, "\n".join(lines[i:])


def _stems(where: Where) -> dict[str, str]:
    """An export folder's name before its time -> the site it is of (current.tsv)."""
    out = {}
    for site, folder in current_exports(where.root).items():
        m = _FOLDER.search(str(folder))
        if m:
            out[m.group(1)] = site
    return out


def _names(where: Where) -> dict[str, str]:
    try:
        return {s["site"]: s.get("name") or "" for s in library_facts(where.library)["sites"] if s.get("site")}
    except (OSError, sqlite3.Error):
        return {}


def list_rows(body: str, stems: Mapping[str, str], names: Mapping[str, str]) -> list[dict]:
    """A list file's lines as rows: an export folder split into its site, when it was exported and the folder
    (fullview.rows_of), a site's name beside its key."""
    rows = []
    for item in (ln.strip() for ln in body.split("\n")):
        if not item or item.startswith("#"):
            continue
        first = item.split("\t", 1)[0].split()[0] if item.split() else item
        m = _FOLDER.search(first)
        if m:
            site = stems.get(m.group(1), m.group(1))
            rows.append({"網站": site, "名稱": names.get(site, ""), "導出於": f"{m.group(3)}-{m.group(4)} {m.group(5)}:"
                         f"{m.group(6)}", "資料夾": first})
        else:
            rest = item[len(first):].strip().lstrip("#").strip()
            rows.append({"網站": first, "名稱": names.get(first, ""), **({"說明": rest} if rest else {})})
    return rows


def _cols(rows: list[dict]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols and any(x.get(k) for x in rows):
                cols.append(k)
    return cols


def yaml_view(key: str, text: str) -> dict:
    """units.yaml, site_units.yaml and aliases.yaml as tables (fullview.py)."""
    intro, body = _split(text)
    if key == "aliases":
        rows = []
        for ln in body.split("\n"):
            m = re.match(r"\s*-\s*\[(.*?)\]\s*(?:#\s*(.*))?$", ln)
            if m:
                rows.append({"同一個意思的詞": [w.strip() for w in m.group(1).split(",")], "說明": (m.group(2) or "").strip()})
        return {"intro": intro, "rows": rows, "cols": ["同一個意思的詞", "說明"]}
    got = yaml.safe_load(body) or {}
    if key == "units":
        units = got.get("units") or {}
        rows = [{"單位": k, "別名": (v or {}).get("aliases") or [], "上級": (v or {}).get("parent") or "",
                 "校區": (v or {}).get("campus") or "", "正式名稱": (v or {}).get("name") or "",
                 "依據": " ".join(str((v or {}).get("evidence") or "").split())} for k, v in units.items()]
        return {"intro": intro, "rows": rows, "cols": ["單位", "別名", "上級", "校區", "依據"]}
    rows = []
    for k, v in (got.get("sites") or {}).items():
        kind, _, why = str((v or {}).get("how") or "").partition(":")
        rows.append({"網站": k, "單位": (v or {}).get("unit") or "",
                     "怎麼定": {"auto": "程式對上", "hand": "手填"}.get(kind.strip(), kind.strip()),
                     "共用平台": "是" if (v or {}).get("shared") else "", "理由": why.strip()})
    return {"intro": intro, "top": {"全校": got.get("school")}, "rows": rows,
            "cols": ["網站", "單位", "怎麼定", "共用平台", "理由"]}


def _read_text(path: Path, limit: int | None = None) -> str:
    with path.open("rb") as f:
        data = f.read(limit) if limit else f.read()
    return data.decode("utf-8-sig", errors="replace")


def _tail(path: Path) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        f.seek(max(0, end - TAIL_BYTES))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    if end > TAIL_BYTES:
        lines = lines[1:]  # the first may be cut
    return [str(mask(ln))[:1500] for ln in lines[-TAIL_LINES:]]


def _head_lines(path: Path) -> list[str]:
    out = []
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for _ in range(TABLE_LINES):
            line = f.readline()
            if not line:
                break
            out.append(str(mask(line.rstrip("\n"))))
    return out


# a database's path -> (its identity when counted, when (monotonic), when (Taipei), [[table, rows, approximate]]): one
# entry a file, a newer count replacing the one before
_tables: dict[str, tuple[tuple, float, str, list]] = {}
_tables_lock = threading.Lock()
_counting: set[str] = set()  # the files being counted: one thread a file at a time
# databases written all night (the ledger changes every few seconds while the crawl runs): never counted row by row (a
# count(*) of the ledger's link table alone is seconds of one read beside the crawl); each rowid table is said by its
# last rowid (約), a table without rowids by sqlite_stat1 when the file has it, else not at all (「—」, a line says
# why). Their last reading is served for pipeline.EVERY seconds whatever their identity, then read again (FD-06)
LIVE_DBS = ("frontier",)
UNREAD = ["（這一次讀不到）", None, False]
NO_ROWID_NOTE = ["（「—」：沒有編號的表，數一次要把整個表讀過；帳本邊抓邊改，不數）", None, False]
_TAIL = re.compile(r"\bWITHOUT\s+ROWID\b", re.IGNORECASE)


def without_rowid(sql: str | None) -> bool:
    """Whether a CREATE TABLE statement makes a table without rowids (its options after the last bracket: a comment
    inside the columns never counts)."""
    return bool(_TAIL.search(str(sql or "").rstrip().rsplit(")", 1)[-1]))


def _estimate(conn: sqlite3.Connection, name: str, no_rowid: bool) -> tuple[int | None, bool]:
    """(rows, approximate) of a table without counting it: its last rowid; for a table without rowids sqlite_stat1's
    row count when ANALYZE left one, else (None, False)."""
    if not no_rowid:
        # max(rowid) only says how many rows there are when the rowid is the table's own sequence: an implicit rowid or
        # an INTEGER PRIMARY KEY named id. A key that is another table's id (round2_promoted.url_id, list_look.list_id…)
        # says only the largest id it mentions (review 11-23): no estimate then
        pk = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")') if r[5]]
        if pk and pk != ["id"]:
            return None, False
        n = conn.execute(f'SELECT max(rowid) FROM "{name}"').fetchone()[0]
        return int(n or 0), True
    try:
        stats = conn.execute("SELECT stat FROM sqlite_stat1 WHERE tbl = ?", (name,)).fetchall()
    except sqlite3.Error:  # no ANALYZE ever ran: no sqlite_stat1
        return None, False
    got = [int(s[0].split()[0]) for s in stats if s[0] and s[0].split()[0].isdigit()]
    return (max(got), True) if got else (None, False)


def _count(key: str, path: Path, where: Where, ident: tuple) -> None:
    out: list[list] = []
    try:
        if key == "frontier" and where.archive is not None:
            from nknu.ops import pagecheck
            conn = pagecheck.open_ledger(where.archive)
        else:
            conn = open_ro(path)
        try:
            known: dict[str, int] = {}
            if key in ("aca", "aca_prev"):
                try:
                    counts = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'counts_json'").fetchone()[0])
                    docs = counts.get("docs") or {}
                    known = {"docs": int(docs.get("total") or 0), "chunks": int(counts.get("chunks") or 0)}
                except (sqlite3.Error, TypeError, ValueError, AttributeError):
                    known = {}
            started = time.monotonic()
            live = key in LIVE_DBS
            # read the list whole first: each count is then a read of its own, and no read stays open across them all
            # (on the live ledger a long read holds back the WAL's checkpoints)
            listed = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
            skipped = False
            for name, sql in listed:
                if name.startswith("sqlite_"):
                    continue
                virtual = str(sql or "").upper().startswith("CREATE VIRTUAL")
                if name in known:
                    out.append([name, known[name], False])
                    continue
                late = time.monotonic() - started > COUNT_BUDGET
                try:
                    if live or virtual or _BIG.search(name) or late or name in ("chunk_vecs", "vecs"):
                        n, approx = _estimate(conn, name, without_rowid(sql) and not virtual)
                        skipped = skipped or n is None
                        out.append([name, n, approx])
                    else:
                        out.append([name, int(conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]), False])
                except sqlite3.Error:
                    out.append([name, None, False])
            if live and skipped:
                out.append(NO_ROWID_NOTE)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001  an older ledger, a busy file: the drawer says so, the next ask tries again
        log.info("数不了 %s 的资料表：%s", path, exc)
        out = [UNREAD]
    finally:  # always, in one step: a file left in _counting would say 正在數 for ever
        with _tables_lock:
            _tables[str(path)] = (ident, time.monotonic(), datetime.now(TAIPEI).strftime("%m-%d %H:%M"), out)
            _counting.discard(str(path))


def tables(where: Where, key: str, path: Path, *, wait: bool = False) -> tuple[list | None, str]:
    """(a database's tables with their rows, approximate for the big index tables and every table of a database in
    LIVE_DBS, which is never counted row by row; when they were counted), counted in a background thread, one a file
    at a time. A count is kept until the file changes; a database in LIVE_DBS keeps it pipeline.EVERY seconds whatever
    its changes. Meanwhile the last count is served with its time; (None, '') until there is one."""
    try:
        st = path.stat()
    except OSError:
        return None, ""
    ident, name, now = (st.st_size, st.st_mtime_ns), str(path), time.monotonic()
    with _tables_lock:
        got = _tables.get(name)
        failed = got is not None and got[3][:1] == [UNREAD]  # tried again after a while, changed or not
        fresh = got is not None and ((got[0] == ident and not failed)
                                     or ((key in LIVE_DBS or failed) and now - got[1] < pipeline.EVERY))
        thread = None
        if not fresh and name not in _counting:
            _counting.add(name)
            thread = threading.Thread(target=_count, args=(key, path, where, ident), name="db-tables", daemon=True)
            thread.start()
    if wait and thread is not None:
        thread.join(60)
        with _tables_lock:
            got = _tables.get(name)
    return (got[3], got[2]) if got is not None else (None, "")


def look(where: Where, key: str, meta: dict, site: str = "", step: str = "", *, wait: bool = False) -> dict:
    """What the drawer shows of a file: {kind: secret|host|absent|dir|tables|view|json|lines|tail|text|md|head|none,
    …}."""
    if meta.get("secret"):
        return {"kind": "secret"}
    if meta.get("host"):
        return {"kind": "host", "note": meta.get("absent") or ""}
    if not meta.get("exists"):
        if key in SITE_FILES and not site:
            return {"kind": "view", "view": export_list(where, step)}
        return {"kind": "absent", "absent": meta.get("absent") or "這一晚沒用到"}
    if meta.get("dir"):
        return {"kind": "dir"}
    path = Path(meta["path"])
    suffix = path.suffix.lower()
    if suffix in (".db", ".sqlite") or key in ("aca", "aca_prev", "aca_tmp", "aca_held"):
        got, counted = tables(where, key, path, wait=wait)
        return {"kind": "tables", "tables": got, "pending": got is None, "counted": counted}
    if suffix in (".gz", ".tar", ".zip"):
        return {"kind": "none"}
    size = meta.get("size") or 0
    if key in ("units", "site_units", "aliases"):
        try:
            return {"kind": "view", "view": yaml_view(key, _read_text(path))}
        except (yaml.YAMLError, OSError, AttributeError, TypeError) as exc:
            log.info("读不成表格 %s：%s", path, exc)
    if key in LISTS:
        intro, body = _split(_read_text(path, TEXT_WHOLE))
        rows = list_rows(body, _stems(where), _names(where))
        return {"kind": "view", "view": {"intro": intro, "rows": rows, "cols": _cols(rows),
                                         "empty": "" if rows else "空的：現在沒有網站在這裡"}}
    if key == "ingest_history":
        rows = []
        for ln in _tail(path):
            try:
                got = json.loads(ln)
            except ValueError:
                continue  # the tail's first line may be cut
            if isinstance(got, dict):
                rows.append(_mask_all(got))
        cols = [c for c in ("night", "outcome", "started_at", "finished_at", "step", "sites") if any(c in r for r in rows)]
        return {"kind": "view", "view": {"intro": [], "rows": rows, "cols": cols}}
    if key in LOGS:
        got = pipeline.log_tail(where, LOGS[key], TAIL_LINES)
        if got is not None:
            return {"kind": "tail", "tail": got["lines"]}
    if suffix in (".log", ".jsonl"):
        return {"kind": "tail", "tail": _tail(path)}
    if key == "manifest":
        return manifest_look(path)
    if suffix == ".json":
        if size <= JSON_WHOLE:
            try:
                return {"kind": "json", "json": _mask_all(json.loads(_read_text(path)))}
            except ValueError:
                pass
        return _head(path, size)
    if suffix in (".tsv", ".csv"):
        return {"kind": "lines", "lines": _head_lines(path), "sep": "\t" if suffix == ".tsv" else ",",
                "head": HEADS.get(key)}
    if suffix in (".md", ".txt", ".yaml", ".yml") and size <= TEXT_WHOLE:
        return {"kind": "md" if suffix == ".md" else "text", "text": str(mask(_read_text(path)))}
    return _head(path, size)


def _head(path: Path, size: int) -> dict:
    with path.open("rb") as f:
        data = f.read(HEAD_BYTES)
    if b"\x00" in data:
        return {"kind": "none"}
    return {"kind": "head", "head": str(mask(data.decode("utf-8", errors="replace"))), "size": size}


def manifest_look(path: Path) -> dict:
    """A manifest (megabytes): its own fields and its first ROWS_SHOWN rows, the count of all."""
    got = json.loads(_read_text(path))
    rows = got.get("项目") if isinstance(got, dict) else None
    if not isinstance(rows, list):
        return {"kind": "json", "json": _mask_all(got)}
    top = {k: v for k, v in got.items() if k != "项目"}
    return {"kind": "json", "json": _mask_all({**top, "项目": rows[:ROWS_SHOWN]}), "cut": {"项目": len(rows)}}


def export_list(where: Where, step: str = "") -> dict:
    """The export folders as rows to open one by (no site picked): the crawler's newest (current.tsv) for 導出, the
    library's (sites.export) otherwise."""
    names = _names(where)
    rows = []
    if step == "export":
        for site, folder in sorted(current_exports(where.root).items()):
            rows.append({"網站": site, "名稱": names.get(site, ""), "資料夾": folder.name})
    if not rows:
        try:
            for s in library_facts(where.library)["sites"]:
                if s.get("site") and s.get("export"):
                    rows.append({"網站": s["site"], "名稱": s.get("name") or "",
                                 "資料夾": Path(str(s["export"]).rstrip("/")).name})
        except (OSError, sqlite3.Error):
            pass
    return {"intro": ["每個網站一個導出資料夾；點一列看那個網站的。"], "rows": rows, "cols": ["網站", "名稱", "資料夾"],
            "open": "site", "empty": "沒有導出資料夾"}


def file(where: Where, key: str, params: Mapping[str, str], *, wait: bool = False) -> dict:
    """One file of the map by its key: what files() says of it, who reads and writes it, and a look inside."""
    site = str(params.get("site") or "").strip()
    step = str(params.get("step") or "").strip()
    meta = info(where, key, site, step, wait=wait)
    body = {**meta, "readers": readers(key), "writers": writers(key), "site": site, "step": step,
            "run_day": run_day(where)}
    try:
        body["look"] = look(where, key, meta, site, step, wait=wait)
    except (OSError, ValueError, UnicodeError) as exc:
        log.info("看不了 %s：%s", key, exc)
        body["look"] = {"kind": "absent", "absent": f"這一次讀不到（{type(exc).__name__}）"}
    return _mask_all(body)  # every look and view: YAML and list tables, folder names, a manifest's fields (FD-05)


def api(where: Where, params: Mapping[str, str]) -> tuple[int, dict]:
    """GET /api/file: ?key= one file, ?step= the map and a step's files; 404 for a key or step not in the map (a
    path is never taken from a request)."""
    key, step = str(params.get("key") or "").strip(), str(params.get("step") or "").strip()
    if key:
        if key not in FILES:
            return 404, {"error": "沒有這個檔"}
        return 200, file(where, key, params)
    if step and step not in STEPS:
        return 404, {"error": "沒有這一步"}
    return 200, files(where, step, str(params.get("site") or "").strip())
