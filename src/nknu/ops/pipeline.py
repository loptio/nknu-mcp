"""流水線: the whole pipeline on one page, each step with what went in, what came out, what was lost on the way and
why, whether it is normal, and who fixes it when it is not (owner 11-16: 「这些dashboard存在的意义是帮助我监控和看见
整个项目每个关键节点的输入输出 加工过程，让我知道整个项目是否正常运作，出错的话是哪里错了 能帮助我精准定位」).

Every step is read from what that step itself left behind, read-only, never computed again:

  爬取     the crawler's ledger (event run_start/run_end, meta run:alive_at, url_result) and its nightly status file
  導出     the export folders the library was built from (manifest.json) and the crawler's newest list (current.tsv)
  轉文字   the build's to-text accounting (totext/summary.json, accounting.jsonl) and the three lanes' marks and logs
  建庫     the library's meta and sites table, build.status, a held library, the per-site checks (site_checks.json)
  檢查     the build's own checks (offline_new_r3.json, checks.md) against the reports of the build before
  服務     the services' own answers (the MCP service, Datasette, 資料總覽, the crawler's viewer)
  每晚進庫  the nightly ingest's status file and history (content session 9's; fields agreed 11-16)

where everything is and what it costs was measured on the lab PC on 11-16 19:00–19:40 (two read-only surveys). The
probes run in one background thread (Pipeline), every EVERY seconds at most, so a page never waits for a slow one; a
probe that fails says so on its own step (檢視台讀不到這一步的紀錄) and never hides the others. Nothing here takes a lock
another program uses (run.lock, the lanes' and the build's): a shared flock at the wrong moment stops a night (the
crawler session's warning, 11-16).

A step's verdict is one of OK (正常), WARN (要看: something is off but nothing is broken), BAD (出錯: a run failed, a
service does not answer) and NONE (還沒有: the step is not set up yet, or there is nothing to judge), with one sentence
saying what is true and, when it is not OK, who fixes it (FIXERS; the owner's rule of 11-16, relayed by 爬虫judge:
problems of the crawler are written up for the crawler session, the panels never judge for it, so the crawl step shows
the crawler's own verdicts). Long-standing numbers (round one's 待判定, the build's standing reminders, known failed
conversions) are shown, never raised as alarms: an alarm is for what went wrong or got worse. The rules that turn
numbers into verdicts are 【Claude】; each sits beside the probe that uses it."""

from __future__ import annotations

import functools
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from nknu.core.timeutil import TAIPEI
from nknu.ops.names import TITLES, link_name, link_tip, rename

log = logging.getLogger(__name__)

OK, WARN, BAD, NONE = "ok", "warn", "bad", "none"
WORST = (BAD, WARN, OK, NONE)  # the overall verdict is the first of these any step has; NONE alone is not a problem
STATE_WORDS = {OK: "正常", WARN: "留意", BAD: "出錯", NONE: "還沒結果"}  # names tried on a cold reader 11-16
# who fixes what (the sessions as the owner knows them, by their job); 學校那邊 for what only the school can change
CRAWLER, CONTENT, INTERFACE, PANEL, SCHOOL = "爬蟲", "建庫", "對外服務", "面板", "學校那邊"
FIXERS = {CRAWLER: "交給爬蟲 session", CONTENT: "交給內容（建庫）session", INTERFACE: "交給 mcp session",
          PANEL: "交給面板 session", SCHOOL: "學校那邊的事，我們不用改"}
EVERY = 300.0  # seconds a reading is kept before the probes run again 【Claude】
PIPE = Path("research/pipeline_2026-11-02")  # under root: the libraries, the lists, the reports, the nightly files
EXPORTS = Path("archive-export/sites")  # under root: the crawler's export folders and its current.tsv
SEARCH_EVERY = 3600.0  # a real search through the MCP service at most this often 【Claude】: it loads both models
CRAWL_LONG = 6 * 3600  # a crawl run longer than this is worth a look 【Claude】: a whole night took about 1.5 h
BUILD_LONG = 3 * 3600  # a build with its checks took 68.5 min on 11-16 【Claude】
INGEST_LONG = 4 * 3600  # the lanes' 2 h limit plus a build with its checks 【Claude】
LATE_EXPORT = 26 * 3600  # a newer export the library has not taken after this long 【Claude】: the night should have


@dataclass
class Step:
    """One step of the pipeline as the page shows it. into, out: (noun, count) of what came in and what came out;
    lost: (why, count) of what went in and did not come out (the step's own reasons, in plain words); notes: more
    sentences (the first shows, the rest float over it); more: (label, where) of the pages that show the step in
    detail (a short name the page resolves, or an address)."""

    key: str
    name: str
    does: str
    state: str = NONE
    say: str = ""
    fix: str = ""
    at: str = ""
    took: str = ""
    into: list[tuple[str, int]] = field(default_factory=list)
    out: list[tuple[str, int]] = field(default_factory=list)
    lost: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    more: list[tuple[str, str]] = field(default_factory=list)
    lost_label: str = "沒走到下一步的"  # what lost is, in this step's own words
    # 11-23 (the design's port): lost is every kind, never cut (a page cuts when it draws: 「其餘 N 項 · total」), in the
    # names.py words. lost_keys: [key, word, n] a row of lost, in its order, with a key that stays when a word is
    # renamed (crawl: the bucket or review:<result code>; export: the 缺什么 mark; totext: the status; build:
    # build_left_keys: alias:<why> or left:<the build's first key with that word>, the raw drawer's own labels), what a
    # drawer filters by. lost_buckets (crawl only): [bucket, word, n]
    # a row, the crawler's category of it (review, failed, queued: 要處理; gone, login: 學校那邊). lost_title: the
    # heading's short name (names.TITLES; lost_label stays the long one, for its hover)
    lost_keys: list[list] = field(default_factory=list)
    lost_buckets: list[list] = field(default_factory=list)
    lost_title: str = ""
    figure: str = ""  # the step's one number on the flow map (with unit): what it put out, or its state in two words
    unit: str = ""
    bar: list[int] = field(default_factory=list)  # [what came out, what went in], one unit: the step's bar; [] none
    share: bool = True  # whether the bar says its percentage (not where the rest is by design, as the build's merges)
    flow_words: str = ""  # one sentence in place of the in/out, where they are not a loss (the export's rows)
    paused: bool = False  # the nightly ingest is switched off by hand (ingest.pause): the home says so on top
    # how what came in follows from what the step before put out, a line a term: [sign ('' the start, '+', '−', '='),
    # words, count, more words floating over it]; link_left: what the terms leave unaccounted (0: they add up; None:
    # nothing to add up). 2026-11-21, owner: 「上一步出的和下一步进的对不上」: the steps counted in different units
    # (addresses, rows, files, records) and a stage between two of them (assemble) was on no panel 【Claude】. Since
    # 11-23 (NV38) guarded() gives every row the names.py words (link_name; its hover through link_tip), so each page
    # that draws a link shows the design's words without renaming it again
    link: list[list] = field(default_factory=list)
    link_left: int | None = None
    # what runs inside the step on its own schedule: [name, tone (ok, warn, bad, none), words] (the crawl's weekly
    # revisit of the outside sites, its browser-opened sites, its course-term check)
    sides: list[list] = field(default_factory=list)
    sides_label: str = ""  # what the sides are, where they are no runs (the services' step: 這台電腦)
    # the public service's use (the services' step: its query log of the last day): lines as sides, and the last
    # searches that found nothing [time, interface, the searched words as logged (masked by the service)]
    usage: list[list] = field(default_factory=list)
    usage_label: str = ""
    usage_missed: list[list] = field(default_factory=list)
    # the step's numbers as numbers, for a page that draws them (總覽, nknu.ops.panorama): the sentences above say the
    # same in words, and a page that read its numbers back out of them broke on the first rewording (2026-11-22)
    nums: dict = field(default_factory=dict)


@dataclass
class Where:
    """What the probes read: root (the folder the library's relative paths start from, /work on the lab PC), the
    library, the crawler's archive folder (frontier.db; None: not mounted), the services to ask whether they answer
    ((name, address, how): how is mcp, datasette, overview or http), the scoreboard's snapshot and the page check's
    overview (both from the app that runs this; None: none)."""

    root: Path
    db: Path
    archive: Path | None = None
    services: tuple[tuple[str, str, str], ...] = ()
    disks: tuple[tuple[str, str], ...] = ()  # (name, a folder on it): the drives whose free space the page says
    public_log: Path | None = None  # the public service's query log (queries.jsonl), read only; None: not mounted
    board: Callable[[], dict | None] | None = None
    sweep: Callable[[], dict | None] | None = None
    today: Callable[[], date] = field(default=lambda: datetime.now(TAIPEI).date())
    sites: dict[str, dict] = field(default_factory=dict)  # one reading's numbers per site, filled by the probes (cell)

    @property
    def library(self) -> Path:
        return self.db if self.db.is_absolute() else self.root / self.db

    @property
    def pipe(self) -> Path:
        return self.root / PIPE


# the columns of the table of sites, in the pipeline's order: (key, header, what it counts: the header's floating card)
SITE_COLS = (("crawl", "抓到", "爬蟲帳本裡這個網站抓到的網址（還沒抓到的、照規則不抓的另算，滑過去看）"),
             ("export", "導出", "庫用的那份導出清單有幾筆（每筆一個網頁、檔案或圖片）"),
             ("refill", "舊存檔頂替的", "這一次沒抓好（抓壞或抓到空的）、只能用以前抓到的那一份頂替的筆數（10-24 舊存檔，或每晚更新前一次抓到的）"),
             ("text", "轉成文字", "建庫時這個網站的檔案和網頁有幾個轉成了文字（壓縮檔裡的檔各算一個，可能比導出的行數多）"),
             ("docs", "庫裡文件", "正式庫裡屬於這個網站的文件"),
             ("check", "漏抓的網頁", "網頁核查找到正文區有漏、或整頁沒抓下來的網頁（點網站名，在它那一頁的「網頁核查」看是哪幾頁）"))


def cell(where: Where, site: str, col: str, n: int | None, *, state: str = OK, tip: str = "", sub: str = "",
         why: str = "", name: str = "", lost: list[tuple[str, str, int]] | None = None,
         whole: int | None = None) -> None:
    """One number of one site's row; why: a sentence for the row's 要看的地方 when the cell is not normal (it says who
    fixes it); lost: (key, words, count) of what did not come out at this step, and whole: what this step was to put
    out, for the site's page (its funnel and its reasons, panels v3 2026-11-17 四.2-四.3). A cell to look at (WARN,
    BAD) is also one of the row's issues: (col, state, why), so a page can say at which step it is."""
    row = where.sites.setdefault(site, {"key": site, "name": "", "cells": {}, "why": [], "worst": OK, "issues": []})
    if name and not row["name"]:
        row["name"] = name
    row["cells"][col] = {"n": n, "state": state, "tip": tip, "sub": sub}
    if lost is not None:
        row["cells"][col]["lost"] = [list(x) for x in lost]
    if whole is not None:
        row["cells"][col]["whole"] = whole
    if why:
        row["why"].append(why)
    if state in (BAD, WARN):
        row["issues"].append({"col": col, "state": state, "why": why})
    if state in (BAD, WARN) and WORST.index(state) < WORST.index(row["worst"]):
        row["worst"] = state


def site_table(where: Where, t2s: Callable[[str], str] = lambda s: s) -> list[dict]:
    """The rows of the table of sites: the ones with problems first (出錯, then 要看), then by how many documents."""
    rows = []
    for site, row in where.sites.items():
        row = dict(row)
        row["name"] = row["name"] or site
        row["q"] = " ".join({site.lower(), row["name"].lower(), t2s(row["name"]).lower()})
        row["href"] = "/site?site=" + quote(site, safe="")  # one site's page (structure design 2026-11-17 section 二)
        rows.append(row)
    rank = {BAD: 0, WARN: 1, OK: 2, NONE: 3}
    rows.sort(key=lambda r: (rank.get(r["worst"], 3), -((r["cells"].get("docs") or {}).get("n") or 0), r["name"]))
    return rows


def worst(*states: str) -> str:
    """The worst of states (出錯 over 要看 over 正常 over 還沒有)."""
    found = [s for s in states if s]
    return min(found, key=WORST.index) if found else NONE


# ------------------------------------------------------------------------------------------------ small helpers


def now_tpe() -> datetime:
    return datetime.now(TAIPEI)


def when_of(value: str | float | None) -> datetime | None:
    """An ISO time (with or without its zone: none is Taipei) or a POSIX time, as a Taipei time; None if unreadable."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, TAIPEI)
        at = datetime.fromisoformat(str(value).strip())
    except (ValueError, OSError, OverflowError):
        return None
    return at.astimezone(TAIPEI) if at.tzinfo else at.replace(tzinfo=TAIPEI)


def stamp(value: str | float | None) -> str:
    """A time as the pages write it: 11-16 14:56 (Asia/Taipei), '' for none."""
    at = when_of(value)
    if at is None:
        return str(value or "")[:16]
    return at.strftime("%m-%d %H:%M")


def span(seconds: float | None) -> str:
    """A duration in words: 45 秒, 13 分鐘, 2 小時 5 分."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 90:
        return f"{seconds:.0f} 秒"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} 分鐘"
    return f"{minutes // 60} 小時 {minutes % 60} 分"


def ago(value: str | float | None, at: datetime | None = None) -> float | None:
    """Seconds from a time (ISO or POSIX) until at (default now), None when it cannot be read."""
    then = when_of(value)
    return None if then is None else ((at or now_tpe()) - then).total_seconds()


def read_json(path: Path) -> object | None:
    """A JSON file, None when it is not there or cannot be read (a file being renamed into place)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_lines(path: Path) -> list[dict]:
    """A JSON-lines file's records, the unreadable lines left out."""
    out: list[dict] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    got = json.loads(line)
                except ValueError:
                    continue
                if isinstance(got, dict):
                    out.append(got)
    except OSError:
        return []
    return out


def tail(path: Path, size: int = 16384) -> list[str]:
    """The last lines of a log that only grows (its last size bytes), [] when it cannot be read."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if end > size else lines  # the first may be cut


# the logs whose end a page can show, by a short name (never a path from a request). Owner 11-21, of an error that
# said 「細節在存檔的 update.log」: the page read the log to judge the night and left the owner to ssh for its words
LOG_FILES = {"update": "update.log", "export": "export.log", "laneA": "laneA.log", "laneB": "laneB.log",
             "laneC": "laneC.log", "build": "build.log", "ingest": "ingest.log"}
# nothing a log should hold, and nothing a page may show if one does: a Google key, a key or token in an address,
# a header that carries one
_SECRETS = ((re.compile(r"AIza[0-9A-Za-z_\-]{10,}"), "（遮掉的金鑰）"),
            (re.compile(r"(?i)([?&](?:key|api_key|access_token|token)=)[^&\s\"']+"), r"\1（遮掉）"),
            (re.compile(r"(?i)((?:authorization|x-goog-api-key|cookie)\s*[:=]\s*)\S.*"), r"\1（遮掉）"))


def log_tail(where: Where, name: str, lines: int = 50) -> dict | None:
    """The last lines of one of LOG_FILES as it stands ({name, file, at, lines}), None for a name that is none of
    them or a log this service has no folder of; lines that cannot be read are an empty list."""
    if name not in LOG_FILES:
        return None
    kit = where.pipe / "lab_pc"
    folder = (where.archive if name == "update" else where.root / EXPORTS if name == "export" else kit)
    if folder is None:
        return None
    path = folder / LOG_FILES[name]
    got = tail(path, 96 * 1024)[-max(1, min(int(lines), 200)):]
    shown = []
    for line in got:
        for pattern, word in _SECRETS:
            line = pattern.sub(word, line)
        shown.append(line[:1500])
    try:
        at = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI).isoformat(timespec="seconds")
    except OSError:
        at = ""
    return {"name": name, "file": path.name, "at": at, "lines": shown}


def open_ro(path: Path) -> sqlite3.Connection:
    """A database opened read-only (the libraries hold students' names; nothing here writes)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def tw(text: str) -> str:
    """Words our own programs write in Simplified Chinese (the crawler's, the build's), as the panels show them
    (owner 11-15: 「可视化dashboard全部换繁体字」); only for showing, never for matching."""
    try:
        from nknu.ops.look import s2t
    except ImportError:  # the converter's package is missing: show the words as written
        return text
    return s2t(text)


# ------------------------------------------------------------------------------------------------ the library


def library_facts(db: Path) -> dict:
    """What the library says of its own build: when, from how many sites, its documents and chunks, what the build
    left out by reason, its merging per site, and per site its documents and export folder (meta and the sites table:
    no query over docs, whose columns after its text cost a scan of the whole file)."""
    conn = open_ro(db)
    try:
        meta = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('built_at', 'vec_built_at', 'schema_version', 'counts_json',"
            " 'excluded_json', 'inputs_json', 'dedupe_json', 'date_spikes_json')")}
        sites = [dict(r) for r in conn.execute("SELECT * FROM sites")]
    finally:
        conn.close()

    def loaded(key: str) -> dict | list:
        try:
            got = json.loads(meta.get(key) or "null")
        except ValueError:
            return {}
        return got if isinstance(got, (dict, list)) else {}

    counts = loaded("counts_json")
    counts = counts if isinstance(counts, dict) else {}
    docs = counts.get("docs") if isinstance(counts.get("docs"), dict) else {}
    return {"built_at": meta.get("built_at"), "swapped_at": meta.get("vec_built_at"),
            "schema": meta.get("schema_version"), "docs": int(docs.get("total") or 0),
            "chunks": int(counts.get("chunks") or 0), "kinds": {k: v for k, v in docs.items() if k != "total"},
            "excluded": loaded("excluded_json"), "inputs": loaded("inputs_json"), "dedupe": loaded("dedupe_json"),
            "spikes": loaded("date_spikes_json"), "sites": sites}


def folder_of(root: Path, written: str) -> Path:
    """An export folder as a library's sites.export, exports.txt or current.tsv writes it: relative to root, or an
    absolute path inside the lab's containers (/work/...), which is under root wherever this runs."""
    path = Path(written.strip())
    if path.is_absolute():
        if str(path).startswith("/work/") and not path.exists():
            return root / str(path)[len("/work/"):]
        return path
    return root / path


# ------------------------------------------------------------------------------------------------ 爬取


def open_ledger(archive: Path) -> sqlite3.Connection:
    """The crawler's ledger read-only, with its result views (網頁核查's way: nknu.ops.pagecheck.open_ledger)."""
    from nknu.ops import pagecheck
    return pagecheck.open_ledger(archive)


def _crawler():
    from nknu.archive import outcome, progress
    return outcome, progress


def _message(raw: object) -> dict:
    try:
        got = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def run_state(conn: sqlite3.Connection, now: datetime) -> dict:
    """The crawler's last run as its ledger records it ({state, words, at, took, running}), never touching run.lock:
    a run is going on when its run_start has no run_end after it and its heartbeat (meta run:alive_at, every 60 s) is
    fresher than the crawler's own limit (progress.ALIVE_LATE_S); a run_start with neither is a run that stopped
    without ending (killed, the power off). How a run ended is the crawler's own words (progress.RUN_END_ZH)."""
    _outcome, progress = _crawler()
    rows = conn.execute("SELECT rowid, at, code, message FROM event WHERE code IN ('run_start', 'run_end')"
                        " ORDER BY rowid DESC LIMIT 8").fetchall()
    if not rows:
        return {"state": NONE, "words": "爬蟲還沒跑過", "at": "", "took": "", "running": False}
    if rows[0][2] == "run_end":
        # the nightly script runs the browser-opened sites again after the night's own run (nightly_update.sh 5b): a
        # run of those sites alone is theirs (crawl_sides says it), and 上一輪 is the run before it. 2026-11-21: every
        # morning the page said 上一輪 … 結束（5 站）
        js = js_sites(conn)
        for i in range(0, len(rows) - 1, 2):
            if rows[i][2] != "run_end" or rows[i + 1][2] != "run_start":
                break
            if not js or not set(_message(rows[i + 1][3]).get("sites") or []) <= js:
                rows = rows[i:]
                break
    last = rows[0]
    if last[2] == "run_end":
        start = rows[1] if len(rows) > 1 and rows[1][2] == "run_start" else None
        reason = str(_message(last[3]).get("reason") or "")
        words = RUN_END_WORDS.get(reason) or tw(getattr(progress, "RUN_END_ZH", {}).get(reason, reason) or "結束了")
        bad = reason in getattr(progress, "RUN_END_BAD", ())
        sites = len(_message(start[3]).get("sites") or []) if start else 0
        took = ago(start[1], when_of(last[1])) if start else None
        return {"state": BAD if bad else OK, "at": start[1] if start else last[1], "took": span(took),
                "running": False, "sites": sites,
                "words": f"上一輪 {stamp(last[1])} 結束（{sites} 站）：{words}"}
    started, sites = last[1], len(_message(last[3]).get("sites") or [])
    beat = conn.execute("SELECT value FROM meta WHERE key = 'run:alive_at'").fetchone()
    try:
        alive = float(beat[0]) if beat else None
    except (TypeError, ValueError):
        alive = None
    late = float(getattr(progress, "ALIVE_LATE_S", 180.0))
    running_for = ago(started, now)
    if alive is not None and now.timestamp() - alive < late:
        state, words = OK, f"在跑：{stamp(started)} 開始、{sites} 站、跑了 {span(running_for)}"
        if running_for is not None and running_for > CRAWL_LONG:
            state, words = WARN, words + "，比平常久很多"
        return {"state": state, "words": words, "at": started, "took": "", "running": True, "sites": sites}
    return {"state": BAD, "at": started, "took": "", "running": False, "sites": sites,
            "words": f"{stamp(started)} 開始的那一輪沒有收尾，心跳也停了（爬蟲可能被中斷）"}


# the nightly update's counts (its status file's keys) in plain words
# how a run ended, where the crawler's own words name a file or a signal (progress.RUN_END_ZH keeps the rest)
RUN_END_WORDS = {"finished": "抓完了", "waiting": "先停下（剩下的伺服器在休息或連不上，下一晚再試）",
                 "stop_file": "有人手動停了它", "sigterm": "有人手動停了它", "stop": "停了"}
# update-status.json's failed_step, matched by how it starts, in this order (export-list before export); the last
# three are the nightly export's (the crawler's export batch, eb2b831)
NIGHT_STEP_WORDS = (("preflight", "檢查環境"), ("backup", "備份帳本"), ("put-back", "排今晚要看的網址"),
                    ("sites", "網站清單"), ("run", "抓網頁"), ("review", "算有哪些變動"),
                    ("export-list", "列出要導出的網站"), ("export", "導出"), ("seen", "記下各站看過的時間"))
OWED = "export-owed.json"  # the crawler's sites that changed and are not exported yet (update.OWED): next night's
NOT_MEANT = ("rule", "next_round")  # the crawler's categories for what this round is not to fetch: no loss
NIGHT_COUNTS = {"new": "新的", "changed": "改了", "same": "沒變", "no_answer": "沒回話", "not_seen": "沒看到",
                "not_got": "沒抓到", "deleted": "學校刪了", "still_gone": "還是不在", "back": "又回來了"}
NIGHT_COLORS = {"red": BAD, "yellow": WARN, "green": OK, "none": OK}


def crawler_night(conn: sqlite3.Connection, archive: Path, now: datetime) -> dict | None:
    """The crawler's nightly update as the crawler itself judges it (progress.update_state, 735ca62): None before
    go-live. The copy the tools run may hold an older crawler without that function: then the same rules are read
    here from the same three meta keys and the status file (reports/update-status.json)."""
    _outcome, progress = _crawler()
    judge = getattr(progress, "update_state", None)
    if judge is not None:
        got = judge(conn, archive, now.timestamp())
        if got is None:
            return None
        # the night's export as the crawler marks it (progress.export_mark: a held site red, and first) is read here
        # by the panels' rule instead: held is no failure (11-17, the lead's brief)
        export = night_export(archive)
        mark = getattr(progress, "export_mark", None)
        if export is not None and mark is not None and got == mark(export):
            said = export_night(export)
            if said is None:  # only manual holds: the night went as it should (the crawler's mark is red for them)
                began = _night_started(conn)
                said = {"state": OK, "words": f"每晚更新 {stamp(began)} 那一次跑完了" if began else "每晚更新跑完了",
                        "tip": ""}
            said["tip"] = "；".join(x for x in (said["tip"], held_words(export_paused(export), inline=True,
                                                                        paused=True)) if x)
            return said
        words = str(got.get("words") or "")
        state = NONE if "还没跑过" in words else NIGHT_COLORS.get(str(got.get("color")), OK)
        return {"state": state, "words": tw(words).replace("定期更新", "每晚更新"), "tip": tw(str(got.get("tip") or ""))}
    meta = {r[0]: r[1] for r in conn.execute(
        "SELECT key, value FROM meta WHERE key IN ('update:sites', 'update:night_started', 'update:night_reviewed')")}
    if "update:sites" not in meta:
        return None
    status = read_json(archive / "reports" / "update-status.json")
    status = status if isinstance(status, dict) else {}

    def num(key: str) -> float | None:
        try:
            return float(meta[key]) if meta.get(key) else None
        except ValueError:
            return None

    night, reviewed = num("update:night_started"), num("update:night_reviewed")
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    tip = "、".join(f"{NIGHT_COUNTS.get(k, k)} {v:,}" for k, v in counts.items() if isinstance(v, int))
    alarms = status.get("alarms") if isinstance(status.get("alarms"), dict) else {}
    if status.get("failed_step"):
        where_ = str(status["failed_step"])
        stopped = next((w for k, w in NIGHT_STEP_WORDS if where_.startswith(k)), tw(where_))
        return {"state": BAD, "words": f"每晚更新沒跑完（停在「{stopped}」這一步）", "tip": tip}
    if alarms:
        return {"state": BAD, "words": f"每晚更新有 {len(alarms)} 站警報",
                "tip": "\n".join(f"{k}：{tw(str(v))}" for k, v in alarms.items())}
    if night is None:
        return {"state": NONE, "words": "每晚更新還沒跑過", "tip": ""}
    if now.timestamp() - night > 26 * 3600:
        return {"state": WARN, "words": f"每晚更新 {int((now.timestamp() - night) // 3600)} 小時沒跑了", "tip": tip}
    if reviewed != night:
        return {"state": OK, "words": f"每晚更新在跑（{stamp(night)} 開始）", "tip": tip}
    # the nightly export (the crawler's export batch, eb2b831): its review writes "export": null, its seen step the
    # export's record; a status file of before it holds a sentence there, or nothing (the same rules as update_state)
    export = status.get("export")
    if "export" in status and export is None:
        return {"state": OK, "words": f"每晚更新在跑（{stamp(night)} 開始，變動算好了、還在導出）", "tip": tip}
    if isinstance(export, dict):
        paused = held_words(export_paused(export), inline=True, paused=True)
        if (said := export_night(export)) is not None:
            said["tip"] = "；".join(x for x in (said["tip"], paused) if x)
            return said
        tip = "；".join(x for x in (tip, f"導了 {len(export.get('exported') or [])} 站", paused) if x)
    return {"state": OK, "words": f"每晚更新 {stamp(night)} 那一次跑完了", "tip": tip}


def _night_started(conn: sqlite3.Connection) -> float | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'update:night_started'").fetchone()
    try:
        return float(row[0]) if row and row[0] else None
    except (TypeError, ValueError):
        return None


def night_export(archive: Path | None) -> dict | None:
    """The last night's export as the crawler's status file records it (reports/update-status.json "export"), None
    when there is none."""
    status = read_json(archive / "reports" / "update-status.json") if archive is not None else None
    export = status.get("export") if isinstance(status, dict) else None
    return export if isinstance(export, dict) else None


MANUAL_HOLD = "人工暂缓："  # export_sites.sh: a site export-hold.txt names, its reason after it (11-17)


def _holds(export: dict | None, manual: bool) -> dict[str, str]:
    held = (export or {}).get("held")
    if not isinstance(held, dict):
        return {}
    out = {}
    for k, v in held.items():
        v = str(v or "")
        if v.startswith(MANUAL_HOLD) == manual:
            out[str(k)] = tw(v.removeprefix(MANUAL_HOLD))
    return out


def export_held(export: dict | None) -> dict[str, str]:
    """The sites whose new export the crawler's copy guard held back, and why (update-status.json export.held, 11-17:
    the export in use filled a row from the 10-24 archive's copy that the new one did not fill, and it is neither
    deleted nor fetched this round): their old export stays in use, they stay owed, and they are listed in
    export.failed too. The manual holds (export_paused) are not among them."""
    return _holds(export, False)


def export_paused(export: dict | None) -> dict[str, str]:
    """The sites a person put in export-hold.txt (the crawler's export folder; the reason the crawler records after
    人工暂缓：, 11-17): not exported on purpose, held and listed in failed as the copy guard's are, but nothing to fix:
    said as news, never a warning (the lead's brief, 11-17). Their reasons without the prefix, in Traditional."""
    return _holds(export, True)


def held_words(held: dict[str, str], *, inline: bool = False, paused: bool = False) -> str:
    """The held (or, paused, the manually held) sites and why, for a floating card (a line each) or a note (inline)."""
    if not held:
        return ""
    if inline:
        head = "人工暫緩的站：" if paused else "攔下的站照舊用前一份導出："
        return head + "、".join(f"{k}（{v}）" if v else k for k, v in list(held.items())[:12])
    head = (f"人工暫緩 {len(held)} 站（export-hold.txt 寫著，這次不導、照舊用前一份導出）：" if paused
            else f"攔下 {len(held)} 站，新導出沒換上、照舊用前一份導出：")
    return "\n".join([head] + [f"{k}：{v}" if v else k for k, v in list(held.items())[:12]])


def export_night(export: dict) -> dict | None:
    """The night's export when a site did not go out: a held site is a warning (the crawler session looks at what
    dropped; the old export stays in use), a failure that is not a held one stays red (review 2026-11-17 and the
    lead's brief of 11-17); None when every site went out."""
    held, paused = export_held(export), export_paused(export)
    failed = [str(x) for x in export.get("failed") or [] if str(x) not in held and str(x) not in paused]
    if failed:
        return {"state": BAD, "words": f"每晚更新有 {len(failed)} 站導出失敗（下一晚再導）"
                + (f"，另攔下 {len(held)} 站" if held else ""),
                "tip": "；".join(x for x in ("、".join(failed[:12]), held_words(held, inline=True)) if x)}
    if held:
        return {"state": WARN, "words": f"每晚更新導出攔下 {len(held)} 站", "tip": held_words(held, inline=True)}
    return None


def owed_sites(archive: Path) -> list[str]:
    """The sites the crawler saw change and has not exported yet (reports/export-owed.json): the next night exports
    them."""
    got = read_json(archive / "reports" / OWED)
    return [x for x in got if isinstance(x, str)] if isinstance(got, list) else []


ROUND2_OPEN = "round2:open"  # nknu.archive.batch.OPEN_KEY: meta {"sites": [...]}, the sites of round two's open batch


def round2_open(conn: sqlite3.Connection) -> list[str]:
    """The sites of round two's batch being crawled (meta round2:open, written by the crawler's batch.promote and
    cleared by its close); [] when none."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (ROUND2_OPEN,)).fetchone()
        got = json.loads(row[0]) if row and row[0] else None
    except (sqlite3.Error, TypeError, ValueError):
        return []
    sites = got.get("sites") if isinstance(got, dict) else got
    return sorted({s for s in sites if isinstance(s, str)}) if isinstance(sites, list) else []


def crawl_buckets(conn: sqlite3.Connection, sites: set[str], batch_sites: list[str] = (),
                  review: dict[str, int] | None = None
                  ) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]] | None]:
    """Per site, its addresses by the crawler's own category of their result (outcome.user_bucket: got, failed,
    review, pending_check, …): the ledger's url_result, the rules 看板, the export and 網頁核查 all use; and, apart
    from them, round two's open batch per site: the rows it took from 下一輪 (round2_promoted) and what its sites
    gained since it began (what those pages led to), so the batch is never read as round one falling behind (the
    lead's brief, 11-17; batch.promote marks only the first, the second by url.added_at 【Claude】). The batch is
    None when the ledger cannot tell them apart (no round2_promoted: an older crawler); {} when no batch is open."""
    outcome, _progress = _crawler()
    out: dict[str, dict[str, int]] = {}
    batch: dict[str, dict[str, int]] | None = {}
    rows = None
    if batch_sites:
        qs = ",".join("?" * len(batch_sites))
        try:
            start = conn.execute(f"SELECT min(k.at) FROM round2_promoted k JOIN url u ON u.id = k.url_id"
                                 f" WHERE u.owner_site IN ({qs})", tuple(batch_sites)).fetchone()[0]
            rows = conn.execute(
                f"SELECT owner_site, result, owner_site IN ({qs}) AND (id IN (SELECT url_id FROM round2_promoted)"
                " OR added_at >= ?), count(*) FROM url_result GROUP BY 1, 2, 3",
                (*batch_sites, start if start is not None else float("inf"))).fetchall()
        except sqlite3.Error:
            batch = None
    if rows is None:
        rows = [(s, r, 0, n) for s, r, n in conn.execute(
            "SELECT owner_site, result, count(*) FROM url_result GROUP BY 1, 2")]
    for site, result, apart, n in rows:
        if apart and batch is not None:
            per = batch.setdefault(site, {})
        elif site in sites:
            per = out.setdefault(site, {})
        else:
            continue
        bucket = outcome.user_bucket(result)
        per[bucket] = per.get(bucket, 0) + n
        if review is not None and bucket == "review" and per is out.get(site):
            review[result] = review.get(result, 0) + n  # the bucket's results, each by its own code
    return out, batch


def js_sites(conn: sqlite3.Connection) -> set[str]:
    """The sites the browser opens (the last jsfetch_start's), {} where the ledger has none."""
    try:
        row = conn.execute("SELECT message FROM event WHERE code = 'jsfetch_start' ORDER BY rowid DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return set()
    return {s for s in (_message(row[0]).get("sites") or []) if isinstance(s, str)} if row else set()


def _utc(value: str | float | None) -> str:
    """A time as the ledger writes its own (UTC, ISO), '' when it cannot be read."""
    at = when_of(value)
    return at.astimezone(UTC).isoformat() if at else ""


def crawl_sides(conn: sqlite3.Connection, archive: Path, now: datetime) -> list[list]:
    """What the nightly crawl does beside its own run, each from the file the crawler writes of it: the weekly
    revisit of the outside sites (update-status.json revisit_tonight, put_back.revisit; the night of Saturday 01:00),
    the sites a browser opens (the ledger's jsfetch_end events of this night), the course-term check
    (reports/schedule-term.json). Until 2026-11-21 none of the three was on a panel: whether they ran could not be
    seen 【Claude】."""
    out: list[list] = []
    status = read_json(archive / "reports" / "update-status.json")
    status = status if isinstance(status, dict) else {}
    started = _utc(status.get("night_started"))
    night = stamp(status.get("night_started"))
    # 1 the outside sites
    tonight = [s for s in status.get("revisit_tonight") or [] if isinstance(s, str)]
    idle = [s for s in status.get("revisit_idle") or [] if isinstance(s, str)]
    put = (status.get("put_back") or {}).get("revisit") if isinstance(status.get("put_back"), dict) else None
    name = "校外網站每週重看"
    if "revisit_tonight" not in status:
        out.append([name, NONE, "爬蟲的狀態檔沒有這一項（還沒跑過，或讀不到）"])
    elif not tonight:
        out.append([name, NONE, f"{night} 這一晚沒輪到（每週六凌晨那一晚才看，共 {len(idle)} 站）"])
    else:
        states: dict[str, int] = {}
        try:
            qs = ",".join("?" * len(tonight))
            for state, n in conn.execute(f"SELECT state, count(*) FROM url_result WHERE owner_site IN ({qs})"
                                         " AND fetched_at >= ? GROUP BY 1", (*tonight, started)):
                states[str(state)] = n
            left = conn.execute(f"SELECT count(*) FROM url_result WHERE owner_site IN ({qs})"
                                " AND state = 'pending' AND reason = 'update_revisit'", tuple(tonight)).fetchone()[0]
        except sqlite3.Error:
            states, left = {}, None
        words = f"{night} 這一晚輪到 {len(tonight)} 站，排了 {int(put or 0):,} 頁（首頁和首頁連到的頁）"
        if states:
            words += f"：抓了 {states.get('done', 0):,} 個網址" + (f"、失敗 {states['failed']:,}" if states.get("failed")
                                                               else "")
        if left:
            words += f"；還有 {left:,} 頁沒輪到抓"
        out.append([name, WARN if left else OK, words + (f"；沒輪到的 {len(idle)} 站" if idle else "")])
    # 2 the sites a browser opens
    name = "要用瀏覽器開的網站"
    try:
        events = conn.execute("SELECT at, level, code, message FROM event WHERE code IN ('jsfetch_start',"
                              " 'jsfetch_end') ORDER BY rowid DESC LIMIT 8").fetchall()
    except sqlite3.Error:
        events = []
    mine = [ev for ev in events if started and str(ev[0]) >= started]
    if not events:
        out.append([name, NONE, "帳本裡沒有瀏覽器開網頁的紀錄"])
    elif not mine:
        out.append([name, WARN, f"{night} 這一晚沒開（爬取或檢查沒過就不開）；上一次是 {stamp(events[0][0])}"])
    elif mine[0][2] == "jsfetch_start":
        out.append([name, OK, f"在開：{stamp(mine[0][0])} 開始，{len(_message(mine[0][3]).get('sites') or [])} 站"])
    else:
        ends = [(ev[1], _message(ev[3])) for ev in mine if ev[2] == "jsfetch_end"]
        total: dict[str, int] = {}
        sites: set[str] = set()
        for _level, msg in ends:
            for site, per in (msg.get("sites") or {}).items():
                sites.add(site)
                for k in ("opened", "incomplete", "requests", "skipped"):
                    total[k] = total.get(k, 0) + int((per or {}).get(k) or 0)
        how = {"finished": "都開完了", "stop": "有人要它停", "error": "出錯"}
        last_end = str(ends[0][1].get("end") or "")
        bad = any(level == "error" or str(msg.get("end")) == "error" for level, msg in ends)
        words = (f"{night} 這一晚開了 {len(ends)} 趟、{len(sites)} 站：共開 {total.get('opened', 0):,} 頁、送了 "
                 f"{total.get('requests', 0):,} 個請求")
        if total.get("incomplete"):
            words += f"、{total['incomplete']:,} 頁沒開完整"
        if total.get("skipped"):
            words += f"、到上限沒開 {total['skipped']:,} 頁"
        words += f"；{stamp(mine[0][0])} 結束：{how.get(last_end, tw(last_end) or '沒說怎麼結束')}"
        out.append([name, BAD if bad else WARN if last_end not in ("finished", "") else OK, words])
    # 3 the course term
    name = "課表學期"
    term = read_json(archive / "reports" / "schedule-term.json")
    if not isinstance(term, dict):
        out.append([name, NONE, "還沒有課表學期的紀錄（reports/schedule-term.json）"])
    else:
        at, since = stamp(term.get("at")), ago(term.get("at"), now)
        if term.get("changed") is None:
            tone, words = WARN, f"{at} 讀不到課程查詢頁存下的那一份，分不出學期換了沒"
        elif not term.get("changed"):
            tone, words = OK, f"沒變：{tw(str(term.get('page_term') or ''))}（{at} 看的，沒送請求）"
        elif term.get("walk") == "done":
            tone, words = OK, (f"{at} 發現換成 {tw(str(term.get('page_term') or ''))}"
                               f"（原來 {tw(str(term.get('ledger_term') or ''))}），課表重走完了")
        else:
            tone, words = WARN, f"{at} 發現換成 {tw(str(term.get('page_term') or ''))}，課表沒走完，下一晚再走"
        if term.get("open"):
            words += (f"；{tw(str(term['open']))} 的課表還在進：這一晚放行 {int(term.get('let_go') or 0):,} 行、還壓著 "
                      f"{int(term.get('held_left') or 0):,} 行")
        if since is not None and since > 36 * 3600:
            tone, words = WARN, words + f"；已經 {span(since)} 沒再看（每晚都該看一次）"
        out.append([name, tone, words])
    return out


PLAIN_BUCKET = {"要人看": "待確認"}


def bucket_words() -> dict[str, str]:
    _outcome, progress = _crawler()
    # Taiwan usage; the crawler's 要人看 (fetched but maybe incomplete or wrong, or left alone by its own limits: a
    # dozen causes in one bucket) is 待確認 here: the owner (11-16) named 要人看 among the names that say nothing; then
    # the 11-23 words (names.py: 已取得, 連線失敗, 校方已移除 …), so every page and drawer says one word
    return {k: rename(PLAIN_BUCKET.get(tw(v), tw(v)).replace("登錄", "登入"))
            for k, v in getattr(progress, "BUCKET_ZH", {}).items()}


@functools.lru_cache(maxsize=512)
def review_word(code: str) -> str:
    """One 待確認 cause (a review result code) in the panel's words: 「待確認：」 and the crawler's label of it
    (outcome.RESULT_LABEL, its first clause), renamed (names.py: 待確認：內容過少 …). The crawl step's lost list, the
    coverage per night (nknu.ops.sources) and the drawer's labels all use it."""
    labels = getattr(_crawler()[0], "RESULT_LABEL", {})
    return rename(f"待確認：{tw(str(labels.get(code) or code)).split('；')[0]}")


def crawl_step(where: Where) -> Step:
    step = Step("crawl", "爬取", "爬蟲照網站清單抓網頁和檔案，存進帳本和存檔；每晚再看一次各站的列表和首頁")
    step.more = [("網頁核查", "check")]  # 看爬蟲進度 (the crawler's own board) until the panels became one, 2026-11-21
    archive = where.archive
    if archive is None or not (archive / "frontier.db").exists():
        step.say = "檢視台沒有掛上爬蟲的帳本"
        page_check_cells(where, step)
        return step
    now = now_tpe()
    keys = {s.get("site") for s in library_facts(where.library)["sites"] if s.get("site")}
    conn = open_ledger(archive)
    try:
        run = run_state(conn, now)
        night = crawler_night(conn, archive, now)
        open_sites = round2_open(conn)
        review: dict[str, int] = {}
        buckets, batch = crawl_buckets(conn, keys, open_sites, review)
        try:
            step.sides = crawl_sides(conn, archive, now)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            step.sides = [["爬取裡另外跑的三件事", NONE, "這一次讀不到（爬蟲正在寫帳本時會這樣，下一次再讀）"]]
    finally:
        conn.close()
    words = bucket_words()
    totals: dict[str, int] = {}
    for site, per in buckets.items():
        for bucket, n in per.items():
            totals[bucket] = totals.get(bucket, 0) + n
        others = sorted(((b, n) for b, n in per.items() if b != "got"), key=lambda bn: -bn[1])
        tip = "\n".join(f"{words.get(b, b)}：{n:,}" for b, n in others) or "全部抓到了"
        if (mine := (batch or {}).get(site)) is not None:
            tip += f"\n第二輪這一批另外抓到 {mine.get('got', 0):,} / {sum(mine.values()):,} 個網址（不算在這裡）"
        lost = [(b, words.get(b, b), n) for b, n in others if b not in NOT_MEANT and n]
        cell(where, site, "crawl", per.get("got", 0), tip=tip, sub=f"共 {sum(per.values()):,} 個網址" if others else "",
             lost=lost, whole=sum(n for b, n in per.items() if b not in NOT_MEANT))
    # the whole of the bar: the addresses this round meant to fetch; what the rules leave out or keep for round two is
    # no loss and is said beside it 【Claude】 (a 63% that was mostly by design misled more than it told)
    left_aside = {b: totals.get(b, 0) for b in NOT_MEANT if totals.get(b)}
    meant = sum(totals.values()) - sum(left_aside.values())
    step.into = [(f"個這一輪該抓的網址（{len(buckets)} 站）", meant)]
    step.out = [("個抓到了", totals.get("got", 0))]
    step.figure, step.unit = f"{totals.get('got', 0):,}", "個抓到了"
    step.bar = [totals.get("got", 0), meant] if meant else []
    # every row keeps its bucket (lost_buckets) and a stable key (lost_keys: the bucket, or review:<result code>), so a
    # page splits 要處理 from 學校那邊 and a drawer filters by key, not by a word a later rename would change (11-23)
    rows = [(b, b, words.get(b, b), n) for b, n in sorted(totals.items(), key=lambda bn: -bn[1])
            if b != "got" and b not in NOT_MEANT and n]
    # 待確認 (the crawler's 要人看) is a dozen causes in one bucket: each cause on its own line, in the crawler's own
    # words of the result (owner 11-21: a bucket that cannot say why says nothing)
    if review and sum(review.values()) == totals.get("review"):
        split = [(f"review:{code}", "review", review_word(code), n)
                 for code, n in sorted(review.items(), key=lambda kv: -kv[1])]
        rows = sorted([r for r in rows if r[0] != "review"] + split, key=lambda r: -r[3])
    step.lost = [(w, n) for _k, _b, w, n in rows]
    step.lost_buckets = [[b, w, n] for _k, b, w, n in rows]
    step.lost_keys = [[k, w, n] for k, _b, w, n in rows]
    step.lost_label = "還沒抓到的（照爬蟲自己的分類）"
    if left_aside:
        step.notes.insert(0, "不算在內：" + "、".join(f"{words.get(b, b)} {n:,}" for b, n in left_aside.items()))
    step.at, step.took = run.get("at", ""), run.get("took", "")
    state = worst(run["state"], night["state"] if night else NONE)
    say = run["words"]
    if night:
        say += "；" + night["words"]
        if night.get("tip"):
            step.notes.append(night["tip"])
    else:
        step.notes.append("每晚更新還沒上線")
    owed = owed_sites(archive)
    if owed:
        step.notes.insert(0, f"變動了、還沒導出的網站 {len(owed)} 個（下一晚接著導）：" + "、".join(owed[:8]))
    # the export batch of 11-16 (the crawler session) wrote most of round one's 待判定 and 要人看 off: what is left is
    # shown, and the light follows only whether the crawler ran and ended as it should
    step.notes.append("「還沒抓到的」只顯示數字、不亮燈；亮燈只看爬蟲有沒有正常跑完、每晚更新有沒有警報")
    if open_sites:  # round two's batch crawled in the daytime (11-17): said, and kept out of round one's whole
        head = f"第二輪這一批在抓（{len(open_sites)} 站）"
        say += "；" + head
        if batch is None:
            step.notes.insert(0, f"{head}：帳本分不出哪些網址是這一批的，上面的比例含這幾站還沒抓的")
        else:
            got = sum(per.get("got", 0) for per in batch.values())
            whole = sum(sum(per.values()) for per in batch.values())
            step.notes.insert(0, f"{head}：抓到 {got:,} / {whole:,} 個網址，不算在上面的比例裡")
    page_check_cells(where, step)
    for name, tone, words in step.sides:  # one of the side runs went wrong: the step is lit and says which
        if tone == BAD:
            state = worst(state, WARN)
            say += f"；{name}：{words}"
    step.state, step.say = state, say
    step.fix = CRAWLER if state in (BAD, WARN) else ""
    return step


def page_check_cells(where: Where, step: Step) -> None:
    """網頁核查's results (nknu.ops.sweep, kept in ops.db since 2026-11-22 night) as numbers of the crawl step and of
    every site's row: addresses counted once, in the three classes of 網頁核查 (爬蟲能補, 要確認, 學校那邊; owner 11-22:
    count each address once). A site's cell is what the crawler can fetch and the pages it could not get itself.
    Neutral on purpose 【Claude】: what it finds is handed to the crawler session in batches (the owner's rule of
    11-16), so a long-known miss is a number to see, not an alarm every morning."""
    view = where.sweep() if where.sweep else None
    if not view or not view.get("totals"):
        phase = ((view or {}).get("progress") or {}).get("phase")
        if phase in ("reading", "checking"):
            step.notes.append("網頁核查正在核對，核完才有每站的漏抓數字")
        elif where.sweep:
            step.notes.append("網頁核查還沒跑過（打開「網頁核查」那一頁就會開始）")
        return
    totals, progress = view["totals"], view.get("progress") or {}
    cls = totals.get("cls") or {}
    done = "核完" if progress.get("phase") == "done" else f"核了 {progress.get('done', 0):,}/{progress.get('total', 0):,} 頁"
    step.notes.append(f"網頁核查（{done}）：爬蟲能補 {cls.get('fix', 0):,} 個網址、要確認 {cls.get('check', 0):,} 個、"
                      f"學校那邊 {cls.get('school', 0):,} 個；沒抓下來的網頁 {totals.get('nofetch', 0):,} 頁")
    for site in view.get("sites") or []:
        c = site.get("cls") or {}
        lost = (c.get("fix") or 0) + (site.get("nofetch") or 0)
        tip = (f"網頁 {site.get('pages', 0):,} 頁；爬蟲能補 {c.get('fix', 0):,} 個網址、要確認 {c.get('check', 0):,} 個、"
               f"學校那邊 {c.get('school', 0):,} 個；沒抓下來的網頁 {site.get('nofetch', 0):,} 頁")
        cell(where, site["key"], "check", lost, tip=tip, sub="個網址、頁" if lost else "", name=site.get("name") or "")


# ------------------------------------------------------------------------------------------------ 導出

FETCHED = "抓到了"  # the export's 结果 for a row the crawler has (nknu.archive.export)
MISSING_SHOWN = 20  # examples kept of each kind of 缺什么 (the site page's reason lists) 【Claude】
_manifest_sums: dict[tuple[str, int], dict] = {}  # (manifest path, its time of change) -> its summary
_manifest_ids: dict[tuple[str, int], tuple[frozenset[int], dict[int, str]]] = {}  # the same key -> manifest_ids


def manifest_summary(folder: Path) -> dict:
    """One export folder's manifest.json summed up: when it was exported, its rows by 类型 and by the crawl's 结果
    (with up to 20 examples of each result but 抓到了), what 缺什么 names and how often (a part counted by the words it
    holds: it can hold 「、」 itself), the rows that are a list's paging. Kept per file and time (an export folder is
    written once)."""
    path = folder / "manifest.json"
    key = (str(path), path.stat().st_mtime_ns)
    if key in _manifest_sums:
        return _manifest_sums[key]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in manifest.get("项目", []) if isinstance(r, dict)]
    kinds: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    missing: dict[str, int] = {}
    missing_examples: dict[str, list[tuple[str, str]]] = {}  # up to MISSING_SHOWN of each (the site page's lists)
    examples: dict[str, list[tuple[str, str]]] = {}
    paging = files = file_rows = got_buttons = both = got_no_file = 0
    file_else: dict[str, int] = {}  # rows with a file whose 结果 is not 抓到了 (an older answer filled in, …)
    got_ids: set[int] = set()
    else_ids: dict[int, str] = {}
    seen: set[str] = set()
    refill: dict[str, int] = {}
    refills: list[tuple[str, str, str, str]] = []
    for r in rows:
        kind = str(r.get("类型") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
        result = str(r.get("结果") or "")
        outcomes[result] = outcomes.get(result, 0) + 1
        if result != FETCHED and len(examples.setdefault(result, [])) < 20:
            examples[result].append((str(r.get("网址") or ""), str(r.get("标题") or "")))
        parts = missing_parts(str(r.get("缺什么") or ""))
        both += REVIEW_LINKED in parts and EMBED_MISSING in parts
        ref = str(r.get("存档编号") or "")
        if ref[:1] == "u" and ref[1:].isdigit():  # a row of an address (the ledger's url.id); a… is a button's
            if result == FETCHED:
                got_ids.add(int(ref[1:]))
            else:
                else_ids[int(ref[1:])] = result
        elif result == FETCHED:
            got_buttons += 1
        for part in parts:
            missing[part] = missing.get(part, 0) + 1
            if len(shown := missing_examples.setdefault(part, [])) < MISSING_SHOWN:
                shown.append((str(r.get("网址") or ""), str(r.get("标题") or "")))
        if str(r.get("页别") or "") == "列表翻页":
            paging += 1
        if result == FETCHED and not r.get("本地档案"):
            got_no_file += 1
        if saved := r.get("本地档案"):
            file_rows += 1  # what the to-text run takes: a row, even one whose file another row has too
            if result != FETCHED:
                file_else[result] = file_else.get(result, 0) + 1
            if saved not in seen:  # one file of several rows (a redirect's landing page, a Google Sites picture
                seen.add(saved)  # several pages show, 11-19) is one file
                files += 1
        origin = str(r.get("来源") or "")
        if origin in REFILL:
            refill[origin] = refill.get(origin, 0) + 1
            if len(refills) < 300:
                refills.append((str(r.get("网址") or ""), str(r.get("标题") or ""), origin, str(r.get("备注") or "")))
    summary = {"site": manifest.get("网站") or "", "name": manifest.get("名称") or "",
               "exported_at": manifest.get("导出时间") or "", "as_of": manifest.get("资料截至") or "",
               "rows": len(rows), "files": files, "file_rows": file_rows, "got_buttons": got_buttons,
               "both": both, "got_no_file": got_no_file, "file_else": file_else,
               "kinds": kinds, "outcomes": outcomes, "missing": missing,
               "paging": paging, "refill": refill, "refills": refills,
               "examples": examples, "missing_examples": missing_examples}
    _manifest_sums[key] = summary
    _manifest_ids[key] = (frozenset(got_ids), else_ids)
    while len(_manifest_sums) > 400:  # about three exports a site at most are ever asked about
        _manifest_ids.pop(old := next(iter(_manifest_sums)), None)
        _manifest_sums.pop(old)
    return summary


def manifest_ids(folder: Path) -> tuple[frozenset[int], dict[int, str]]:
    """The ledger ids (url.id) of an export's address rows: those it says were fetched, and the others with their 结果.
    Apart from the summary, which goes out as JSON."""
    path = folder / "manifest.json"
    manifest_summary(folder)
    return _manifest_ids.get((str(path), path.stat().st_mtime_ns), (frozenset(), {}))


# what 缺什么 can say, by the words a part holds (nknu.archive.export: progress's reasons, then the export's own marks;
# a part can hold 「、」 itself, so it is never split on it), in the order they are looked for
# (the names are the 11-23 words, names.py: the old sentence each replaced is renamed here once)
MISSING_WORDS = tuple((mark, rename(name)) for mark, name in (
    ("学校回答没有资料", "學校回答「沒有資料」"), ("内文区是空的", "內文區是空的"),
    ("找不到内文区", "找不到內文區"), ("列表没翻完", "列表沒翻完"), ("待判定", "待判定"),
    ("缺内嵌页或按钮", "頁面裡的內嵌頁或按鈕沒抓到"), ("学校那边已不存在", "連到的資源學校已不存在或要登入"),
    ("连接失败", "連線失敗"), ("缺附件", "附件沒抓到"), ("缺图片", "圖片沒抓到"), ("下一轮", "留到下一輪補抓"),
    ("要人看：页面几乎是空的", "這一頁幾乎是空的"),
    ("要人看（", "頁面連到的東西裡有待確認的"), ("要人看", "待確認")))
MISSING_KEYS = {name: mark for mark, name in MISSING_WORDS}  # a kind's word -> its mark in 缺什么 (its stable key)
EMBED_MISSING, REVIEW_LINKED, REVIEW_PLAIN = rename("頁面裡的內嵌頁或按鈕沒抓到"), rename("頁面連到的東西裡有待確認的"), "待確認"
REVIEW_SAID = (rename("這一頁幾乎是空的"), REVIEW_LINKED)  # 要人看 with what it is about: the bare word says nothing beside them


# 来源 of a row whose file is an older answer than this round's (nknu.archive.export ORIGIN_OLD, ORIGIN_PREV): this
# round's answer was broken or empty, so the export gave the last good one and its 结果 stays this round's (owner 11-17:
# 「他说爬下来的坏了，目前是用旧的资料补上的，我要去哪个面板看到」)
REFILL = {"10-24 旧存档": "10-24 舊存檔", "上一次抓到的": "每晚更新前一次抓到的"}


# a page the rules leave out says so in 缺什么 (the crawler of 11-18: 「按规则不抓：整页跳到校外页（用户 11-17 决定不抓）」,
# outcome.OFFSITE_LANDED's label): that is a decision, not something missing
RULE_MARK = "按规则不抓"


_REVIEW_SAID = re.compile(r"要人看(?:：页面几乎是空的|（)")


def missing_parts(words: str) -> list[str]:
    """The kinds of what a row's 缺什么 names (MISSING_WORDS' names), each once; words naming none of them are one
    kind of their own."""
    words = words.strip()
    if not words:
        return []
    found = [name for mark, name in MISSING_WORDS if mark in words]
    if any(name in found for name in REVIEW_SAID) and "要人看" not in _REVIEW_SAID.sub("", words):
        found.remove(REVIEW_PLAIN)  # 「要人看：…」 and 「要人看（N 个）」 are each one kind, not 待確認 as well
    if not found and RULE_MARK in words:  # left out by rule: nothing is missing (no kind of its own, no gap drawn)
        return []
    return found or [rename(tw(words.split("（", 1)[0]))]


def current_exports(root: Path) -> dict[str, Path]:
    """The crawler's newest export of every site (current.tsv: site, folder), as paths under root."""
    out: dict[str, Path] = {}
    try:
        text = (root / EXPORTS / "current.tsv").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        site, _, folder = line.partition("\t")
        if site.strip() and folder.strip():
            out[site.strip()] = folder_of(root, folder)
    return out


_STAMPED = re.compile(r"-(\d{8})-(\d{6})$")


def folder_time(folder: Path) -> datetime | None:
    """When an export folder was made, from its name (<site>-YYYYMMDD-HHMMSS, Taipei)."""
    m = _STAMPED.search(folder.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=TAIPEI)
    except ValueError:
        return None


def refill_words(before: int, now: int) -> str:
    """A site's export filling more rows from an older answer than the one in use, as data: the two counts (spec
    2026-11-17-panels-v3-questions.md 八.1: the numbers, no sentence written for one incident, no one named to fix it)."""
    return f"用舊存檔頂替的筆數：庫用的導出 {before:,}，新導出 {now:,}"


def export_link(where: Where, step: Step, fetched: int, got_ids: set[int], else_ids: dict[int, str],
                got_buttons: int) -> int:
    """How the export's fetched rows follow from the addresses the crawl fetched, by the ledger's ids on the rows
    (存档编号 u<url.id>): fetched addresses on no export in use (by what they are), on one under another 结果, the rows
    a button brought (no address of their own), and rows the exports call fetched that the ledger now does not (an
    export older than tonight's crawl, an address another site owns).

    A fetched address the export gives no row of its own is one of three kinds by design (the crawler session,
    2026-11-21, which placed all 6,133 of that night: research/crawler_design_2026-11-19/day/off_manifest_1121.out):
    a site's script (.js, kept as a part of its pages: kind0 script, while kind says file), a Drive record (the request
    that asked a file's name and size; the file itself has its row), the landing of a redirect (the row is the first
    address's, the landing in its 最后落地网址). Any other is returned (0: none) and is the crawler's to look at: the
    kinds are told by the ledger's own columns, not by looking each landing up in the manifests 【Claude】."""
    archive = where.archive
    if archive is None or not (archive / "frontier.db").exists() or not fetched:
        return 0
    keys = {s.get("site") for s in library_facts(where.library)["sites"] if s.get("site")}
    crawled = on_got = 0
    off: dict[str, int] = {}
    stray: dict[str, int] = {}
    other: dict[str, int] = {}
    try:
        conn = open_ledger(archive)
        try:
            for uid, site, kind, via in conn.execute(
                    "SELECT id, owner_site, coalesce(kind0, kind), found_via FROM url_result WHERE result = 'ok'"):
                if site not in keys:
                    continue
                crawled += 1
                if uid in got_ids:
                    on_got += 1
                elif uid in else_ids:
                    word = tw(else_ids[uid]).split("（")[0] or "沒寫結果"
                    other[word] = other.get(word, 0) + 1
                elif str(kind) in OFF_BY_DESIGN:
                    off[OFF_BY_DESIGN[str(kind)]] = off.get(OFF_BY_DESIGN[str(kind)], 0) + 1
                elif via in ("redirect", "scheme_switch"):
                    off[OFF_LANDING] = off.get(OFF_LANDING, 0) + 1
                else:
                    word = OFF_KIND.get(str(kind), str(kind))
                    stray[word] = stray.get(word, 0) + 1
        finally:
            conn.close()
    except sqlite3.Error:
        step.notes.append("這一次沒和爬取對帳：爬蟲正在寫帳本，下一次再對")
        return 0

    def said(parts: dict[str, int]) -> str:
        return "\n".join(f"{k}：{v:,}" for k, v in sorted(parts.items(), key=lambda kv: -kv[1]))

    gone = len(got_ids) - on_got
    twice = fetched - got_buttons - len(got_ids)  # an address two exports hold (a file two sites link) is two rows
    # the crawl step's own figure leaves out round two's open batch (crawl_step: its cells hold what it counted):
    # the link starts at that figure and says the batch as a term, so the two steps show one number
    shown = sum(int((row["cells"].get("crawl") or {}).get("n") or 0) for row in where.sites.values())
    start = [["", "爬取抓到的網址", crawled, ""]]
    if 0 < shown < crawled:
        start = [["", "爬取抓到的網址", shown, ""],
                 ["+", "第二輪這一批抓到的（爬取那一步另外算）", crawled - shown, ""]]
    step.link = [*start,
                 ["−", "抓到了、照設計不另開一筆的（不是內容，或記在別的筆上）", sum(off.values()),
                  said(off) + OFF_LIMIT if off else ""],
                 ["−", "抓到了、清單上沒有，也說不出原因的", sum(stray.values()), said(stray)],
                 ["−", "清單上有，記的是別的結果", sum(other.values()), said(other)],
                 ["+", "清單記「抓到了」、帳本現在不是（清單比今晚的爬取舊，或網址歸別的網站）", gone, ""],
                 ["+", "同一個網址在清單上不只一筆（幾個網站都連到它）", twice, ""],
                 ["+", "按鈕按出來的內容（清單一筆，爬蟲不算網址）", got_buttons, ""],
                 ["=", "清單上「抓到了」的", fetched, ""]]
    step.link_left = fetched - (crawled - sum(off.values()) - sum(stray.values()) - sum(other.values()) + gone + twice
                                + got_buttons)
    return sum(stray.values())


# a fetched address no export in use holds: the kinds that have no row by design (the ledger's coalesce(kind0, kind)),
# the landing of a redirect, and the names of the other kinds
# (the 11-23 words, names.LINKS / NOTES: 網站程式檔（.js）, 雲端硬碟檔案資訊, 轉址落點; the long sentences they replace
# are the keys there: 網站的程式檔是網頁的零件不是內容, 檔案本身那一筆在清單上, 清單記在原來那個網址上)
_DRIVE = link_name("Google 雲端硬碟的檔案資訊和資料夾清單（檔案本身那一筆在清單上）")
OFF_BY_DESIGN = {"script": link_name("網站的程式檔（.js：是網頁的零件，不是內容）"), "drive": _DRIVE, "drive_list": _DRIVE}
OFF_LANDING = link_name("跳轉後的落點（清單記在原來那個網址上）")
# what the count does not prove (the crawler session, 11-21): the kinds are the ledger's, so a landing whose first
# address has no row, or a Drive record whose file has none, would still be counted here. Its own scripts look each
# one up in the manifests (research/crawler_design_2026-11-19/day/off_manifest_1121.py, off_manifest_drive_1121.py)
OFF_LIMIT = "\n" + link_tip("這是照帳本裡的種類分的，沒有逐個到清單裡核對它記在哪一筆上；要逐個核對，請爬蟲跑它的核對腳本")
OFF_KIND = {"file": "檔案", "image": "圖片", "embed": "內嵌頁", "page": "網頁"}


def export_step(where: Where) -> Step:
    """The exports the library was built from (sites.export), each summed up from its manifest, and the crawler's
    newer exports the library has not taken yet (current.tsv against sites.export)."""
    step = Step("export", "導出", "爬蟲把每個網站抓到的網頁和檔案整理成一份清單和一個資料夾，給轉文字和建庫用")
    step.more = [("看每站的數字", "#sites")]
    facts = library_facts(where.library)
    newest = current_exports(where.root)
    night = night_export(where.archive)
    held, paused = export_held(night), export_paused(night)  # the crawler held these sites' new export (11-17)
    rows = fetched = with_files = file_rows = got_buttons = both = got_no_file = 0
    file_else: dict[str, int] = {}
    got_ids: set[int] = set()
    else_ids: dict[int, str] = {}
    missing: dict[str, int] = {}
    refill: dict[str, int] = {}
    more_refill: list[str] = []
    unread: list[str] = []
    waiting: list[tuple[str, datetime | None]] = []
    late: list[str] = []
    latest = ""
    now = now_tpe()
    for site in facts["sites"]:
        key, written = site.get("site") or "", site.get("export") or ""
        if not key or not written:
            continue
        name = site.get("name") or ""
        folder = folder_of(where.root, written)
        try:
            summary = manifest_summary(folder)
        except (OSError, ValueError):
            unread.append(key)
            cell(where, key, "export", None, state=WARN, tip=f"讀不到 {folder.name} 的清單", name=name,
                 why=f"讀不到庫用的導出清單：{folder.name}")
            continue
        rows += summary["rows"]
        fetched += summary["outcomes"].get(FETCHED, 0)
        with_files += summary.get("files", 0)
        file_rows += summary.get("file_rows", 0)
        got_buttons += summary.get("got_buttons", 0)
        both += summary.get("both", 0)
        got_no_file += summary.get("got_no_file", 0)
        for k, v in (summary.get("file_else") or {}).items():
            file_else[k] = file_else.get(k, 0) + v
        try:
            ids = manifest_ids(folder)
        except OSError:
            ids = (frozenset(), {})
        got_ids |= ids[0]
        else_ids.update(ids[1])
        latest = max(latest, str(summary["exported_at"] or ""))
        for k, v in summary["missing"].items():
            missing[k] = missing.get(k, 0) + v
        for k, v in summary["refill"].items():
            refill[k] = refill.get(k, 0) + v
        filled = sum(summary["refill"].values())
        tip = [f"導出於 {stamp(summary['exported_at'])}（{folder.name}）"]
        if summary["missing"]:
            tip.append("缺漏：" + "、".join(f"{k} {v:,}" for k, v in
                                           sorted(summary["missing"].items(), key=lambda kv: -kv[1])[:6]))
        state, why, sub = OK, "", ""
        new = newest.get(key)
        if new is not None and new.name != folder.name:
            made = folder_time(new)
            waiting.append((key, made))
            age = (now - made).total_seconds() if made else None
            sub = f"有 {stamp(made.isoformat()) if made else ''} 的新資料，還沒進庫"
            tip.append(f"爬蟲有更新的導出：{new.name}，庫還沒用它")
            if age is not None and age > LATE_EXPORT:
                state, why = WARN, f"新導出放了 {span(age)} 還沒進庫"
                late.append(key)
        if key in held:
            state = WARN
            said = f"新導出被攔下：{held[key]}"
            why = "；".join(x for x in (why, said) if x)
            tip.append(f"新導出被攔下：{held[key]}")
        if key in paused:  # news, not a problem: the row's state stays as it is
            why = "；".join(x for x in (why, f"人工暫緩：{paused[key]}（這一次不導，照舊用前一份導出）") if x)
            tip.append(f"人工暫緩：{paused[key]}")
        cell(where, key, "export", summary["rows"], state=state, tip="\n".join(tip), sub=sub, why=why, name=name)
        # rows filled from an older answer; more of them in the crawler's newer export than in the one in use means the
        # newest crawl broke more pages (a light 【Claude】; the export in use itself is a number to see)
        mark, why2, tip2 = OK, "", "\n".join(f"{REFILL[k]}：{v:,}" for k, v in summary["refill"].items())
        if new is not None and new.name != folder.name:
            try:
                newer = sum(manifest_summary(new)["refill"].values())
            except (OSError, ValueError):
                newer = filled
            if newer > filled:
                mark, why2 = WARN, refill_words(filled, newer)
                more_refill.append(key)
        cell(where, key, "refill", filled, state=mark, why=why2, tip=tip2 or "沒有要用舊存檔頂替的", name=name)
    outside = sorted(set(newest) - {s.get("site") for s in facts["sites"]})
    failed = []
    try:
        failed = [ln for ln in (where.root / EXPORTS / "failed.tsv").read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
    except OSError:
        pass
    step.at = latest
    step.into = [("筆（每筆一個網頁、檔案或圖片）", rows)]
    step.figure, step.unit = f"{rows:,}", "筆"
    step.flow_words = f"清單共 {rows:,} 筆（每筆一個網頁、檔案或圖片），其中 {file_rows:,} 筆存下了檔案"
    if file_rows > with_files:
        step.flow_words += f"（{file_rows - with_files:,} 筆和別筆共用同一個檔，不同的檔 {with_files:,} 個）"
    step.out = [("筆存下了檔案", file_rows)]
    # every kind (the critic's CR-10, 11-23: cut to 8 here, the night's 「其餘 N 項 · total」 understated and the kinds
    # past the eighth could not reach the drawer); a page cuts when it draws
    step.lost = [(k, v) for k, v in sorted(missing.items(), key=lambda kv: -kv[1])]
    step.lost_keys = [[MISSING_KEYS.get(k, k), k, v] for k, v in step.lost]
    step.lost_label = "清單上註明缺東西的（一筆可能缺好幾樣）"
    if both:
        step.notes.append(f"「{REVIEW_LINKED}」和「{EMBED_MISSING}」有 {both:,} 筆是同一批網頁：一筆算了兩樣")
    stray = export_link(where, step, fetched, got_ids, else_ids, got_buttons)
    if step.link:  # on from the fetched rows to the rows with a file, which is what the to-text run is given
        kept = "\n".join(f"{tw(k).split('（')[0] or '沒寫結果'}：{v:,}" for k, v in sorted(file_else.items(), key=lambda kv: -kv[1]))
        step.link += [["−", "抓到了、沒有存下檔案的", got_no_file, ""],
                      ["+", "沒記「抓到了」、但清單上有檔的（這次沒抓好用舊存檔頂替的也在這裡）", sum(file_else.values()), kept],
                      ["=", "存下了檔案的", file_rows, ""]]
        step.link_left = abs(step.link_left or 0) + abs(file_rows - (fetched - got_no_file + sum(file_else.values())))
    if refill:
        step.notes.insert(0, f"這次沒抓好、用舊存檔頂替的 {sum(refill.values()):,} 筆："
                          + "、".join(f"{REFILL[k]} {v:,}" for k, v in refill.items()))
        step.flow_words += f"；其中 {sum(refill.values()):,} 筆這次沒抓好、用舊存檔頂替"
    if outside:
        step.notes.append(f"爬蟲導出了、庫裡還沒有的網站 {len(outside)} 個：" + "、".join(outside[:6]))
    batch = [ln for ln in tail(where.root / EXPORTS / "export.log", 8192) if "这一批结束" in ln]
    if batch:
        step.notes.append("最後一批導出：" + tw(batch[-1].split("这一批结束", 1)[-1].strip("：: ")))
    state, say, fix = OK, f"庫用的是 {len(facts['sites'])} 站的導出，最新一份 {stamp(latest)} 導出", ""
    if waiting:
        say += f"；另有 {len(waiting)} 站的新導出等著進庫"
    if more_refill:
        state, fix = WARN, CRAWLER
        say = f"有 {len(more_refill)} 站這次沒抓好、要用舊存檔頂替的變多了：" + "、".join(more_refill[:5])
    if paused:
        say += f"；人工暫緩 {len(paused)} 站"
        step.notes.insert(0, held_words(paused, inline=True, paused=True))
    if held:
        state, fix = WARN, CRAWLER
        say = f"每晚更新攔下 {len(held)} 站的新導出，照舊用前一份：" + "、".join(list(held)[:5])
        step.notes.insert(0, held_words(held, inline=True))
    if late:
        state, fix = WARN, CONTENT
        say = f"有 {len(late)} 站的新導出放超過一天還沒進庫：" + "、".join(late[:5])
    if failed:
        state, fix = WARN, CRAWLER
        say = f"導出失敗 {len(failed)} 站：" + "、".join(ln.split("\t")[0] for ln in failed[:5])
    if stray and state == OK:  # fetched, on no export, and of no kind that is left off by design: content may be missing
        state, fix = WARN, CRAWLER
        say = f"有 {stray:,} 個抓到的網址不在任何一份導出清單上，也說不出原因（細節在「和上一步對帳」）"
    if unread:
        state, fix = WARN, CONTENT
        say = f"庫用的導出有 {len(unread)} 站讀不到了：" + "、".join(unread[:5])
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 轉文字

STATUS_WORDS = {k: rename(v) for k, v in {  # the 11-23 words (names.py: 已轉文字, 無文字可讀, 依規則略過, 轉換失敗)
    "ok": "轉成文字", "partial": "轉了一部分", "empty": "讀不出文字", "skipped": "照規則跳過", "failed": "轉文字失敗",
    "pending": "還沒轉"}.items()}
LANES = (("A", "轉 PDF 和 Office 檔的程序"), ("B", "轉網頁和圖片的程序"), ("C", "轉壓縮檔的程序"))
_accounts: dict[tuple[str, int], dict] = {}


def accounting(path: Path) -> dict[str, dict]:
    """The build's to-text accounting (totext/accounting.jsonl, one line a file or archive member, key <site with / as
    _>:<row>) per site prefix: {prefix: {'status': {status: n}, 'bad': [(status, reason, file), …]}} (up to 30 of
    what did not come out ok: failed first). Kept per file and time."""
    key = (str(path), path.stat().st_mtime_ns)
    if key in _accounts:
        return _accounts[key]
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            prefix = str(r.get("key") or "").split(":", 1)[0]
            per = out.setdefault(prefix, {"status": {}, "bad": []})
            status = str(r.get("status") or "")
            per["status"][status] = per["status"].get(status, 0) + 1
            if status in ("failed", "partial", "pending") or (status == "empty" and len(per["bad"]) < 30):
                per["bad"].append((status, str(r.get("reason") or ""), str(r.get("member") or r.get("file") or "")))
    for per in out.values():
        per["bad"].sort(key=lambda b: (b[0] != "failed", b[0] != "pending", b[0]))
        del per["bad"][30:]
    _accounts.clear()
    _accounts[key] = out
    return out


def lane_states(kit: Path, now: datetime) -> list[dict]:
    """The three lanes as their files show them (no docker in here; their locks are never touched, a lane that finds
    its lock taken does not start): lane<X>.inflight while a round runs, lane<X>.log's last lines."""
    out = []
    for lane, what in LANES:
        log_path, mark = kit / f"lane{lane}.log", kit / f"lane{lane}.inflight"
        lines = tail(log_path, 8192)
        try:
            quiet = now.timestamp() - log_path.stat().st_mtime
        except OSError:
            quiet = None
        done = next((ln for ln in reversed(lines) if "] done" in ln), "")
        state = "running" if mark.exists() else "stopped" if done else "unknown"
        if state == "running" and quiet is not None and quiet > 65 * 60:
            state = "stale"  # a round that was killed leaves its mark: the log has not moved for over an hour
        why, at = "", ""
        if done:
            i = lines.index(done)
            before = lines[i - 1].split("]", 1)[-1].strip() if i > 0 else ""
            # how the lane stopped, in the words lanes.sh writes: through its list, stopped on request, a bad setting
            why = ("走完了" if "走过一遍" in before else "被要求停下" if "按要求停" in before
                   else "設定錯誤停了" if "设定错误" in before else tw(before.split("（", 1)[0])[:40])
            m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", done)
            at = m.group(1).replace("T", " ") if m else ""
        out.append({"lane": lane, "what": what, "state": state, "quiet": quiet, "why": why, "at": at})
    return out


def lane_state_words(lane: dict) -> str:
    return {"running": "在轉", "stopped": "停了", "stale": "被中斷"}.get(lane["state"], "看不到")


def exported_file_rows(where: Where) -> int | None:
    """The rows with a file of the exports the library was built from (what the to-text run was given), None when
    one of them cannot be read."""
    rows = 0
    for site in library_facts(where.library)["sites"]:
        if not site.get("site") or not site.get("export"):
            continue
        try:
            rows += manifest_summary(folder_of(where.root, site["export"])).get("file_rows", 0)
        except (OSError, ValueError):
            return None
    return rows


def totext_step(where: Where) -> Step:
    step = Step("totext", "轉文字",
                "把導出的網頁、PDF、Office、圖片（掃描的用 OCR 認字）、壓縮檔讀成文字；三條線分工轉，同一個檔只轉一次")
    pipe = where.pipe
    summary = read_json(pipe / "totext" / "summary.json")
    summary = summary if isinstance(summary, dict) else {}
    by_status = summary.get("by_status") if isinstance(summary.get("by_status"), dict) else {}
    now = now_tpe()
    lanes = lane_states(pipe / "lab_pc", now)
    total = int(summary.get("files") or sum(v for v in by_status.values() if isinstance(v, int)))
    step.into = [("個檔和網頁", total)]
    members = summary.get("members")
    file_rows = exported_file_rows(where)
    if isinstance(members, int) and file_rows is not None:
        step.link = [["", "導出存下檔案的（清單上一筆算一個）", file_rows, ""],
                     ["+", "壓縮檔裡拆出來的檔", members, "一個壓縮檔是清單上的一筆，裡面的檔各轉各的"],
                     ["=", "轉文字經手的", total, ""]]
        step.link_left = total - file_rows - members
    step.out = [("個轉成文字", int(by_status.get("ok") or 0))]
    step.figure, step.unit = f"{int(by_status.get('ok') or 0):,}", "個轉成文字"
    step.bar = [int(by_status.get("ok") or 0), total] if total else []
    shown = [s for s in ("empty", "skipped", "partial", "failed", "pending") if by_status.get(s)]
    step.lost = [(STATUS_WORDS[s], int(by_status.get(s) or 0)) for s in shown]
    step.lost_keys = [[s, STATUS_WORDS[s], int(by_status.get(s) or 0)] for s in shown]
    for item in summary.get("top_reasons") or []:
        if isinstance(item, dict) and item.get("status") in ("failed", "empty"):
            step.notes.append(f"{STATUS_WORDS.get(item['status'], item['status'])}：{tw(str(item.get('reason')))} "
                              f"{item.get('files', 0):,} 個")
    try:
        step.at = datetime.fromtimestamp((pipe / "totext" / "summary.json").stat().st_mtime, TAIPEI).isoformat()
    except OSError:
        pass
    # per site: the accounting's prefix is the site's key with / as _
    facts = library_facts(where.library)
    prefixes = {s["site"].replace("/", "_"): s for s in facts["sites"] if s.get("site")}
    try:
        per_site = accounting(pipe / "totext" / "accounting.jsonl")
    except OSError:
        per_site = {}
        step.notes.append("讀不到建庫時的轉文字記帳（totext/accounting.jsonl）")
    for prefix, per in per_site.items():
        site = prefixes.get(prefix)
        if site is None:
            continue
        status = per["status"]
        others = [(s, n) for s, n in status.items() if s != "ok" and n]
        tip = [f"{STATUS_WORDS.get(s, s)}：{n:,}" for s, n in sorted(others, key=lambda sn: -sn[1])]
        tip += [f"{STATUS_WORDS.get(s, s)}：{f}（{tw(r)}）" for s, r, f in per["bad"][:5] if s != "empty"]
        empty = status.get("empty", 0)
        cell(where, site["site"], "text", status.get("ok", 0), tip="\n".join(tip) or f"全部{STATUS_WORDS['ok']}",
             sub=f"{STATUS_WORDS['empty']} {empty:,}" if empty else "", name=site.get("name") or "")
    running = [ln for ln in lanes if ln["state"] == "running"]
    stale = [ln for ln in lanes if ln["state"] == "stale"]
    pending = int(by_status.get("pending") or summary.get("pending") or 0)
    lane_words = "、".join(f"{ln['what']}{lane_state_words(ln)}" for ln in lanes)
    step.notes[:0] = ["轉文字的三支程序：" + "、".join(
        ln["what"] + (f" {stamp(ln['at'])} {ln['why']}" if ln["why"] else lane_state_words(ln)) for ln in lanes)]
    state, say, fix = OK, "建庫時文字都已轉完", ""
    if pending:
        say = f"建庫時還有 {pending:,} 個沒轉（{lane_words}）"
        state, fix = (OK, "") if running else (WARN, CONTENT)
    elif running:
        say = "正在轉新的檔：" + "、".join(ln["what"] for ln in running)
    if stale:
        state, fix = WARN, CONTENT
        say = "、".join(ln["what"] for ln in stale) + "上一次被中斷了（一小時沒有動靜）"
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 建庫

# the build's keys for what it left out (documents.build_documents, build._merge_copies, the events' series), grouped as
# the survey of 11-16 found them, in the order they are tried: a series of events before a plain repeat
LEFT_OUT = tuple((test, rename(words)) for test, words in (  # the 11-23 words (names.py)
    (lambda k: k.endswith("(重复系列)"), "每週重複的行事曆，合成一個系列"),
    (lambda k: k.endswith("(副本)"), "別處有一模一樣的一份，合併了"),
    (lambda k: k.endswith("(重复)"), "同一網址重複導出，只留一份"),
    (lambda k: "(列表翻页)" in k or "#翻页" in k, "公告列表的分頁（各則另外收了）"),
    (lambda k: "2023" in k, "2023 年以前的行事曆"),
    (lambda k: "学校回答没有资料" in k, "學校這頁回答「沒有資料」"),
    (lambda k: "选单" in k, "只在選單出現的圖片"),
    (lambda k: "学校删掉" in k or "学校已删除" in k, "學校刪掉的公告，只剩空殼"),
    (lambda k: k.endswith("(其他格式)"), "同一個檔的其他格式"),
    (lambda k: k.endswith("(空白)"), "沒標題也沒讀到字"),
    (lambda k: "(" not in k, "列表、相簿、網站地圖這類導覽頁（照種類不收）")))
# a merge the library lists one by one (its doc_alias table, by why) as the build's own key for it
ALIAS_SAID = {"copy": "x(副本)", "format": "x(其他格式)", "duplicate": "x(重复)"}
_whys: dict[tuple, frozenset] = {}


def left_out_words(key: str) -> str:
    for test, words in LEFT_OUT:
        if test(key):
            return words
    return rename(tw(key))


def alias_word(why: str) -> str:
    """A doc_alias why in the words of what the build left out (重複內容，已合併 …); the why itself for another."""
    return rename(left_out_words(ALIAS_SAID[why])) if why in ALIAS_SAID else why


def alias_whys(db: Path) -> frozenset[str]:
    """The whys the library's doc_alias table holds (a merge it lists one by one), read once per version of the file;
    empty for a library without the table."""
    try:
        st = db.stat()
    except OSError:
        return frozenset()
    key = (str(db), st.st_ino, st.st_size, st.st_mtime_ns)
    got = _whys.get(key)
    if got is None:
        try:
            conn = open_ro(db)
            try:
                got = frozenset(str(r[0]) for r in conn.execute("SELECT DISTINCT why FROM doc_alias") if r[0])
            finally:
                conn.close()
        except sqlite3.Error:  # a library built before doc_alias
            got = frozenset()
        _whys.clear()
        _whys[key] = got
    return got


def build_left_keys(excluded: object, whys: frozenset[str] | set[str] = frozenset()) -> dict[str, str]:
    """{word: key}: each word of what the build left out with the key the raw drawer labels it by (nknu.ops.raw
    _build_build, critic RD-02: one scheme on both sides, so a preset passing the step's key finds its rows): a merge
    the library lists one by one is alias:<why>; any other is left:<the first of the build's own keys with that word>,
    in the order the library's excluded_json lists them (the build's key stays when a word is renamed)."""
    out: dict[str, str] = {}
    for why in sorted(whys):
        if why in ALIAS_SAID:
            out.setdefault(alias_word(why), f"alias:{why}")
    for key, n in (excluded if isinstance(excluded, dict) else {}).items():
        if isinstance(n, int) and n:
            out.setdefault(rename(left_out_words(key)), f"left:{key}")
    return out


# site_report.FLAG_WORDS's keys, in the words 資料總覽 shows them (src/nknu/ops/overview_page.html's CK; one concept, one
# word on every panel: redesign 2026-11-17)
FLAG_WORDS = {"none": "沒有文件進庫", "self": "用標題找不回文件", "empty": "網頁沒文字", "no_date": "沒日期",
              "same_title": "多份文件同一標題", "same_opening": "選單混進正文", "fragments": "附件一字一行"}


def site_flags(checks: object, built_at: str | None) -> tuple[dict[str, list[str]], list[tuple[str, str]], bool]:
    """site_checks.json (site_report.py): per site its reminders (flags), the ones new since the build before (new),
    and whether it is this library's (its built_at; written about 35 minutes after a swap)."""
    if not isinstance(checks, dict):
        return {}, [], False
    sites = checks.get("sites") if isinstance(checks.get("sites"), dict) else {}
    flags = {s: [str(f) for f in (e.get("flags") or [])] for s, e in sites.items() if isinstance(e, dict)}
    new = [(str(p[0]), str(p[1])) for p in checks.get("new") or [] if isinstance(p, (list, tuple)) and len(p) >= 2]
    return {s: f for s, f in flags.items() if f}, new, checks.get("built_at") == built_at


def build_status(kit: Path) -> dict:
    """build.status (build.sh writes it whole, one line, a trial build too): {state: running|failed|ok|'', words,
    since, step, code, line}."""
    try:
        line = (kit / "build.status").read_text(encoding="utf-8").strip()
    except OSError:
        return {"state": "", "line": ""}
    at, _, rest = line[:19], "", line[20:]
    m = re.match(r"running step (\S+) since (.+)", rest)
    if m:
        return {"state": "running", "step": m.group(1), "since": m.group(2).strip(), "at": at, "line": line}
    m = re.match(r"failed step (\S+) \(exit (\d+)", rest)
    if m:
        return {"state": "failed", "step": m.group(1), "code": int(m.group(2)), "at": at, "line": line}
    return {"state": "ok" if rest.startswith("ok") else "", "at": at, "line": line}


_assembled: dict[tuple[str, int], dict] = {}  # (report path, its time of change) -> its counts
# what assemble takes of the to-text run's files and gives no record of its own (AssembleReport), in the 11-23 words
# (names.LINKS: 圖片文字併入網頁 …; the hover's sentence through names.NOTES)
ASSEMBLE_OUT = tuple((k, link_name(words), link_tip(tip)) for k, words, tip in (
    ("images_inlined", "圖的字併進了顯示它的網頁", "網頁上的圖讀出來的字放進那一頁，圖自己不另成一筆"),
    ("images_no_text", "圖沒讀出夠多的字，也沒有收進庫的網頁顯示它", ""),
    ("containers_dropped", "壓縮檔本身（裡面的檔各成一筆）", ""),
    ("data_rows", "不用轉文字的資料（行事曆由建庫直接讀、雲端硬碟資料夾的清單）", ""),
    ("format_merged", "同一份文件的另一種格式（併成一筆）", "同一份文件有 PDF 也有 Word：留字多的那份")))


def assembled(where: Where, inputs: object) -> dict[str, int] | None:
    """The per-site assemble reports (<corpus>.assemble_report.json beside each corpus the library was built from)
    summed: records, and what took no record by ASSEMBLE_OUT's keys. None when the library names no inputs or a
    report cannot be read (a library built before the reports were kept)."""
    total: dict[str, int] = {}
    if not isinstance(inputs, list) or not inputs:
        return None
    for item in inputs:
        corpus = str((item or {}).get("corpus") or "") if isinstance(item, dict) else ""
        if not corpus.endswith(".json"):
            return None
        path = folder_of(where.root, corpus[:-len(".json")] + ".assemble_report.json")
        try:
            key = (str(path), path.stat().st_mtime_ns)
            if key not in _assembled:
                got = json.loads(path.read_text(encoding="utf-8"))
                _assembled[key] = {"records": int(got.get("records") or 0),
                                   **{k: len(v) if isinstance(v := got.get(k), dict) else int(v or 0)
                                      for k, _w, _t in ASSEMBLE_OUT}}
                while len(_assembled) > 600:
                    _assembled.pop(next(iter(_assembled)))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        for k, v in _assembled[key].items():
            total[k] = total.get(k, 0) + v
    return total


def build_link(where: Where, step: Step, facts: dict, left: dict[str, int]) -> None:
    """How the build's documents follow from the to-text run: the files it handled, less what assemble folds away
    (ASSEMBLE_OUT), are the records; with the calendar events the build reads itself they are what the build
    sorts into documents and what it leaves out. The events are counted from the build's own tallies (its event
    documents, the series it folded, the ones before 2023): the build keeps no count of what it read."""
    summary = read_json(where.pipe / "totext" / "summary.json")
    handled = int(summary.get("files") or 0) if isinstance(summary, dict) else 0
    sums = assembled(where, facts["inputs"])
    if not handled or sums is None:
        return
    taken = [[k, words, sums.get(k, 0), tip] for k, words, tip in ASSEMBLE_OUT if sums.get(k)]
    records = sums["records"]
    events = int((facts["kinds"] or {}).get("event") or 0) + sum(left.get(w, 0) for w in EVENT_LEFT)
    formats = left.get(FORMAT_LEFT, 0)  # assemble folded them: the build counts them among what it left out
    whole = facts["docs"] + sum(left.values())
    step.into = [("筆記錄（轉文字的結果整理成的）", records), ("個行事曆事件（建庫自己讀的）", events)]
    step.link = ([["", "轉文字經手的（讀不出字的也往下走，文件只剩標題）", handled, ""]]
                 + [["−", words, n, tip] for _k, words, n, tip in taken]
                 + [["=", "整理成的記錄", records, ""],
                    ["+", "行事曆事件（建庫直接從行事曆讀）", events, "照建庫的數字算的：進庫的事件、合成系列的、2023 年以前的"],
                    ["+", "整理時已經併掉、建庫也記了一筆的另一種格式", formats, ""],
                    ["=", "成為文件的，加上沒成為文件的", whole, f"文件 {facts['docs']:,} + 沒成為文件的 {sum(left.values()):,}"]])
    step.link_left = abs(records - (handled - sum(row[2] for row in taken))) + abs(whole - (records + events + formats))


EVENT_LEFT = (rename("每週重複的行事曆，合成一個系列"), rename("2023 年以前的行事曆"))  # LEFT_OUT's words of the events left out
FORMAT_LEFT = rename("同一個檔的其他格式")


def build_step(where: Where) -> Step:
    step = Step("build", "建庫",
                "把轉好的文字整理成文件：合併副本、定日期和單位、切小段、算向量，建好先把關（有網站少一半就不換）再換上")
    db = where.library
    facts = library_facts(db)
    step.at = facts["built_at"] or ""
    left: dict[str, int] = {}
    for key, n in (facts["excluded"] if isinstance(facts["excluded"], dict) else {}).items():
        if isinstance(n, int) and n:
            words = left_out_words(key)
            left[words] = left.get(words, 0) + n
    step.lost = sorted(left.items(), key=lambda kv: -kv[1])
    keys = build_left_keys(facts["excluded"], alias_whys(db))
    step.lost_keys = [[keys.get(rename(w), f"left:{w}"), w, n] for w, n in step.lost]
    step.lost_label = "沒成為文件的（大多是合併掉的重複）"
    sites = len(facts["inputs"]) if isinstance(facts["inputs"], list) else len(facts["sites"])
    # what went in: the documents plus what the build left out or merged (an approximation the survey of 11-16 found
    # close: a record two exports both hold counts once)
    before = facts["docs"] + sum(left.values())
    step.into = [(f"筆整理前的網頁、檔案和行事曆（{sites} 站）", before)]
    step.out = [("份文件", facts["docs"]), ("個小段（搜尋時比對的單位）", facts["chunks"])]
    step.figure, step.unit, step.bar = f"{facts['docs']:,}", "份文件", [facts["docs"], before] if before else []
    step.share = False  # its share is mostly merged repeats: a percentage would read as a loss
    build_link(where, step, facts, left)
    step.more = [("看庫裡有什麼", "overview"), ("看每站的數字", "#sites")]
    swapped = facts.get("swapped_at") or facts["built_at"]
    state, say, fix = OK, f"正式庫 {stamp(swapped)} 換上、正在用（{facts['docs']:,} 份文件）", ""
    status = build_status(where.pipe / "lab_pc")
    now = now_tpe()
    if status["state"] == "running":
        since = ago(status.get("since"), now)
        say = f"正在建庫：步驟 {status.get('step')}（{stamp(status.get('since'))} 開始）；正在用的是 {stamp(swapped)} 那份"
        if since is not None and since > BUILD_LONG:
            state, fix = WARN, CONTENT
            say = (f"建庫的狀態檔停在步驟 {status.get('step')}：{stamp(status.get('since'))} 寫的，之後 {span(since)} "
                   "沒有更新（可能被中斷）")
    elif status["state"] == "failed" and status.get("code") != 3:
        state, fix = WARN, CONTENT
        say = (f"上一次建庫（{stamp(status.get('at'))}，可能是試建）停在步驟 {status.get('step')}、退出碼 "
               f"{status.get('code')}；正在用的還是 {stamp(swapped)} 那份")
    held = db.with_name(f"{db.stem}.held{db.suffix}")
    if held.exists() and held.stat().st_mtime > db.stat().st_mtime:
        state, fix = WARN, CONTENT
        say = f"{stamp(held.stat().st_mtime)} 建好的新庫被擋下（有網站的文件數掉到一半以下），正在用的還是舊的那份"
    flags, new, current = site_flags(read_json(db.with_name("site_checks.json")), facts["built_at"])
    if flags:
        kinds: dict[str, int] = {}
        for found in flags.values():
            for f in found:
                kinds[f] = kinds.get(f, 0) + 1
        step.notes.append(f"建庫自己查出問題的網站 {len(flags)} 個（長期的，不亮燈）："
                          + "、".join(f"{FLAG_WORDS.get(k, k)} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
    if not current and flags:
        step.notes.append("每站報告是上一份庫的（換庫後約 35 分鐘才寫好）")
    if current and new:
        state = WARN if state == OK else state
        fix = fix or CONTENT
        say += "；這次建庫查出新問題：" + "、".join(f"{s} {FLAG_WORDS.get(k, k)}" for s, k in new[:5])
    new_sites = {s for s, _k in new} if current else set()
    for row in facts["sites"]:
        site, docs = row.get("site") or "", row.get("docs")
        if not site:
            continue
        tip = [f"資料截至 {row.get('data_as_of') or '—'}"]
        if row.get("deleted_notices"):
            tip.append(f"學校刪掉或轉走、沒收的公告 {row['deleted_notices']} 則")
        if row.get("under_read"):
            tip.append(f"正文區讀漏、改讀整頁 {row['under_read']} 份")
        found = flags.get(site) or []
        if found:
            tip.append("建庫查出這個網站的問題：" + "、".join(FLAG_WORDS.get(f, f) for f in found))
        mark, why = OK, ""
        if not docs:
            mark, why = WARN, "庫裡 0 份文件"
        elif site in new_sites:
            mark, why = WARN, "這次建庫查出新問題：" + "、".join(FLAG_WORDS.get(k, k) for s, k in new if s == site)
        cell(where, site, "docs", docs, state=mark, tip="\n".join(tip), why=why, name=row.get("name") or "",
             sub=f"建庫查出的問題 {len(found)} 種" if found else "")
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 檢查


def offline_metrics(path: Path) -> dict | None:
    """offline_eval.py's result (offline_new_r3.json): {built_at, question, agent}, each {n, top1, top3, top10, mrr}
    counting copies (the numbers the content session reports), None when unreadable."""
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}

    def of(mode: str) -> dict:
        got = (metrics.get(mode) or {}).get("all") if isinstance(metrics.get(mode), dict) else None
        return got if isinstance(got, dict) else {}

    return {"built_at": data.get("db_built_at"), "question": of("question_copies") or of("question"),
            "agent": of("agent_copies") or of("agent")}


_RESULT = re.compile(r"结果[:：]\s*(\d+)\s*过[、,，]\s*(\d+)\s*不过")
_FAIL = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*FAIL\s*\|")


def realdata(path: Path) -> dict | None:
    """check_realdata.py's report (checks.md): {built_at, passed, failed, fails: {number: name}}, None if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _RESULT.search(text)
    built = re.search(r"建于\s*([0-9T:+\-]+)", text)
    fails = {}
    for line in text.splitlines():
        f = _FAIL.match(line.strip())
        if f:
            fails[f.group(1)] = f.group(2)
    return {"built_at": built.group(1) if built else None, "passed": int(m.group(1)) if m else None,
            "failed": int(m.group(2)) if m else None, "fails": fails}


def previous_reports(kit: Path) -> Path | None:
    """The reports of the official build before the last one (build.sh copies them to lab_pc/previous/<time>/ before
    a run overwrites them; a trial's folder has its name after the time)."""
    try:
        folders = sorted(p for p in (kit / "previous").iterdir() if p.is_dir() and re.fullmatch(r"\d{8}-\d{6}", p.name))
    except OSError:
        return None
    return folders[-1] if folders else None


def check_step(where: Where) -> Step:
    """品質檢查: the build's own real-data checks (checks.md, build.sh step 8). The 33 questions run only by hand since
    2026-11-22 (build.sh --q33), and the question bank checks the whole project now and then, never the night (owner,
    2026-11-22 night: 「拿这些题目和答案做质量检查其实没什么意义……删了这个流程吧」): the 33 are tried on 搜尋. A
    warning when a check failed that passed in the build before."""
    step = Step("check", "品質檢查", "建庫最後跑的 22 項真資料檢查；33 題在「搜尋」頁手動測")
    step.more = [("手動測 33 題", "board")]
    built_at = library_facts(where.library)["built_at"]
    real = realdata(where.pipe / "checks.md")
    if real is None:
        step.say = "還沒有建庫的檢查結果"
        return step
    say, state, fix = "", OK, ""
    stale = bool(real["built_at"] and real["built_at"] != built_at)
    if real["passed"] is not None:
        total = real["passed"] + real["failed"]
        say = f"真資料檢查 {total} 項過了 {real['passed']} 項"
        step.into, step.out = [("項檢查", total)], [("項過了", real["passed"])]
        step.figure, step.unit, step.bar = f"{real['passed']}/{total}", "項過了", [real["passed"], total]
        step.nums.update(real_passed=real["passed"], real_total=total)
    step.lost = [(f"真資料檢查第 {k} 項：{tw(v)}", 1) for k, v in real["fails"].items()]
    step.lost_label = "沒通過的檢查"
    before = previous_reports(where.pipe / "lab_pc")
    old_real = realdata(before / "checks.md") if before is not None else None
    if old_real is not None and not stale:
        newly = sorted(set(real["fails"]) - set(old_real["fails"]), key=int)
        if newly:
            state, fix = WARN, CONTENT
            say = "比上一次建庫退步了：真資料檢查新冒出沒過的：" + "、".join(f"第 {k} 項" for k in newly)
        step.notes.append(f"和上一次建庫比（{before.name}）" + ("：沒有退步" if not newly else ""))
    step.nums["stale"] = stale
    if stale:
        state = NONE
        say = "真資料檢查是上一份庫的結果（新庫的檢查還在跑）"
    step.at = built_at or ""
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 服務

MCP_HELLO = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "nknu-pipeline-panel", "version": "1"}}}
MCP_SEARCH = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "search", "arguments": {"query": "停修申請"}}}  # a question every library answers
_searched: dict[str, dict] = {}  # url -> the last real search's {'at': monotonic, 'answer': ...}


def _local_host(url: str) -> str:
    """The Host every one of these services answers (each refuses any name but this machine's)."""
    parts = urlsplit(url)
    return f"127.0.0.1:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _ask(url: str, *, body: dict | None = None, timeout: float = 10.0, local: bool = True) -> tuple[int, bytes, float]:
    """One request: (status, body, milliseconds). local: under this machine's name, the only one the services answer
    in full; without it they refuse at once (403), which is the cheap way to see that one is up."""
    headers = {"Host": _local_host(url)} if local else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    started = time.monotonic()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2_000_000), (time.monotonic() - started) * 1000
    except urllib.error.HTTPError as exc:  # it answered: a server runs there
        return exc.code, b"", (time.monotonic() - started) * 1000


def ask_service(name: str, url: str, how: str) -> dict:
    """Whether a service answers, and what it says of itself: {'name', 'up', 'ms', 'words', 'built'}.
    mcp: the MCP handshake (no model loaded), and once an hour a real search through it (SEARCH_EVERY);
    datasette: the built_at of the library it has open (it keeps the file it opened until restarted);
    overview: its /stamp (which library its numbers are of); public: the public service's /health (alive, its
    library's age; never a search: that would go into its query log and load its models); http: any answer counts
    (the crawler's viewer computes every page, so it is never asked for one)."""
    try:
        if how == "public":
            code, raw, ms = _ask(url)
            info = json.loads(raw or b"{}") if code == 200 else {}
            if code != 200:
                return {"name": name, "up": False, "ms": ms, "interface": True,
                        "words": "回 503：它讀不到庫" if code == 503 else f"連線回 {code}"}
            words = f"有回應（{ms:.0f} 毫秒）"
            if info.get("built_at"):
                words += f"；讀的庫 {stamp(info['built_at'])} 建的" + (
                    f"、{info['docs']:,} 份" if isinstance(info.get("docs"), int) else "")
            stale = bool(info.get("stale"))
            if stale:
                words += f"；它說庫已經 {info.get('age_hours', '?')} 小時沒換"
            return {"name": name, "up": True, "ms": ms, "words": words, "stale": stale, "interface": True}
        if how == "mcp":
            code, raw, ms = _ask(url, body=MCP_HELLO)
            if code != 200:
                return {"name": name, "up": False, "ms": ms, "words": f"連線回 {code}"}
            last = _searched.get(url)
            if last is None or time.monotonic() - last["at"] > SEARCH_EVERY:
                code, raw, sms = _ask(url, body=MCP_SEARCH, timeout=60.0)
                answer = {"ok": False, "ms": sms, "n": None}
                if code == 200:
                    result = (json.loads(raw or b"{}").get("result") or {})
                    text = next((c.get("text") for c in result.get("content") or [] if c.get("type") == "text"), "")
                    try:
                        found = json.loads(text) if text else {}
                    except ValueError:
                        found = {}
                    answer = {"ok": not result.get("isError"), "ms": sms,
                              "n": len(found.get("results") or []) if isinstance(found, dict) else None}
                last = _searched[url] = {"at": time.monotonic(), "answer": answer, "when": now_tpe().isoformat()}
            got = last["answer"]
            if not got["ok"]:
                return {"name": name, "up": False, "ms": ms, "words": f"連得上，但 {stamp(last['when'])} 那次試搜出錯"}
            words = f"{stamp(last['when'])} 試搜一次 {got['ms'] / 1000:.1f} 秒" + (
                f"、回 {got['n']} 筆" if got["n"] is not None else "") + f"（連線 {ms:.0f} 毫秒）"
            return {"name": name, "up": True, "ms": ms, "words": words}
        if how == "datasette":
            q = "select value from meta where key='built_at'"
            code, raw, ms = _ask(url.rstrip("/") + "/aca.json?_shape=array&sql=" + quote(q))
            built = None
            if code == 200:
                rows = json.loads(raw or b"[]")
                built = rows[0].get("value") if rows and isinstance(rows[0], dict) else None
            return {"name": name, "up": code < 500, "ms": ms, "words": f"有回應（{code}）", "built": built}
        if how == "overview":
            code, raw, ms = _ask(url.rstrip("/") + "/stamp")
            info = json.loads(raw or b"{}") if code == 200 else {}
            return {"name": name, "up": code < 500, "ms": ms, "words": f"有回應（{code}）", "built": info.get("built"),
                    "state": info.get("state"), "error": info.get("error")}
        code, _raw, ms = _ask(url, timeout=5.0, local=False)  # its fast refusal: the viewer computes every page
        return {"name": name, "up": True, "ms": ms, "words": "有回應"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"name": name, "up": False, "ms": None, "words": f"連不上（{type(exc).__name__}）"}


GB = 2 ** 30
DISK_WARN, DISK_BAD = (0.15, 50 * GB), (0.05, 15 * GB)  # a drive is said when its free share or its free bytes fall under


def gpu_memory() -> tuple[int, int] | None:
    """(used, total) MiB of the first graphics card as nvidia-smi says it, None without one or without an answer."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, check=False).stdout
        used, total = (int(x) for x in out.strip().splitlines()[0].split(","))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return (used, total) if total else None


MEMINFO = Path("/proc/meminfo")
MEM_WARN = 0.10  # Docker's memory with less than this share available: worth a look 【Claude】


def memory(path: Path = MEMINFO) -> tuple[int, int, int] | None:
    """(total, available, swap used) in bytes of the memory this container sees: on the lab PC the Docker Desktop
    machine's (Windows gives it half of its 128 GB; Windows' own memory and page file are out of a container's reach),
    where the builds, the lanes and the services all run. None where there is no /proc/meminfo (a Mac)."""
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    kb = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            kb[name] = int(parts[0]) * 1024
    if not kb.get("MemTotal") or "MemAvailable" not in kb:
        return None
    return kb["MemTotal"], kb["MemAvailable"], max(0, kb.get("SwapTotal", 0) - kb.get("SwapFree", 0))


def machine_read(where: Where) -> list[dict]:
    """The machine everything runs on, one dict a line: a drive each (where.disks: free space, said in amber and red
    as it runs out), the memory Docker has (memory), the graphics card's. {name, tone, words, used, total (GB; None
    where unreadable), swap (GB, memory only)}. Until 2026-11-21 no panel said any, and this one machine has been near
    these limits before (C: went from 36 to 23 GB free within a day while builds ran, the page file the likely cause:
    docs/project-blueprint.md)."""
    rows: list[dict] = []
    for name, path in where.disks:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            rows.append({"name": name, "tone": NONE, "words": "讀不到（這一槽沒有掛給檢視台）", "used": None, "total": None})
            continue
        share = usage.free / usage.total if usage.total else 0.0
        tone = (BAD if share < DISK_BAD[0] or usage.free < DISK_BAD[1]
                else WARN if share < DISK_WARN[0] or usage.free < DISK_WARN[1] else OK)
        rows.append({"name": name, "tone": tone,
                     "words": f"還剩 {usage.free / GB:,.0f} GB（共 {usage.total / GB:,.0f} GB，用了 {1 - share:.0%}）",
                     "used": round((usage.total - usage.free) / GB, 1), "total": round(usage.total / GB, 1)})
    if mem := memory():
        total, free, swap = mem
        words = f"用了 {(total - free) / GB:.1f} GB（共 {total / GB:.1f} GB，Docker 分到的那一份）"
        if swap >= GB / 10:  # the Docker machine's swap file is on C: (a few GB is usual after a build)
            words += f"；交換區用了 {swap / GB:.1f} GB"
        rows.append({"name": "記憶體", "tone": WARN if free / total < MEM_WARN else OK, "words": words,
                     "used": round((total - free) / GB, 1), "total": round(total / GB, 1), "swap": round(swap / GB, 1)})
    if gpu := gpu_memory():
        used, total = gpu
        rows.append({"name": "顯示卡記憶體", "tone": WARN if used / total > 0.92 else OK,
                     "words": f"用了 {used / 1024:.1f} GB（共 {total / 1024:.1f} GB）",
                     "used": round(used / 1024, 1), "total": round(total / 1024, 1)})
    return rows


def machine(where: Where) -> list[list]:
    """machine_read's lines as the page says them: [name, tone, words]."""
    return [[m["name"], m["tone"], m["words"]] for m in machine_read(where)]


PUBLIC_HOURS = 24  # the public service's use is said over the last this many hours 【Claude】
# 總覽 draws the calls of each of the last this many days, read from the same log (it keeps every call; 2026-11-22: a
# day's use alone said nothing about whether students come back) 【Claude】
PUBLIC_DAYS = 7
PUBLIC_MISSED = 10  # searches that found nothing listed, the latest first 【Claude】
PUBLIC_SLOW = 10_000  # ms: the slowest tenth over this is worth a look (a search waits at most 20 s, then fails) 【Claude】
# searches timed before a middle and a slowest tenth are said (and can light the step): the first search after the
# service starts loads its models, about 17 s (lab, 11-21 23:02; the 接口 session's note), and alone would light a
# day, so the step is lit only by two or more searches over PUBLIC_SLOW 【Claude】
PUBLIC_TIMED = 5
LOG_CHUNK = 256 * 1024  # bytes read at a time from the end of the query log (it grows to 20 MB before it rotates)
VIA_WORDS = {"mcp": "MCP", "web": "網頁", "api": "接口"}
TOOL_WORDS = {"search": "搜尋", "fetch": "讀全文"}
# what a failed call's 'error' says (nknu.retrieval.public, asked for 2026-11-21); bad_args is the caller's own
FAIL_WORDS = {"busy": "排隊太久", "unavailable": "庫讀不到", "internal": "程式出錯", "bad_args": "參數不對"}


def _query_lines(path: Path, since: datetime) -> tuple[list[dict], bool]:
    """The query log's lines written since `since`, oldest first, read from the end back; and whether a line older
    than since was reached (False: the file began after it, or could not be read). A line that is no JSON or has no
    time is skipped."""
    out: list[dict] = []
    try:
        f = path.open("rb")
    except OSError:
        return out, False
    with f:
        f.seek(0, 2)
        pos, rest = f.tell(), b""
        while pos > 0:
            size = min(LOG_CHUNK, pos)
            pos -= size
            f.seek(pos)
            lines = (f.read(size) + rest).split(b"\n")
            rest = lines.pop(0) if pos > 0 else b""  # the first may be cut: kept for the next read
            for raw in reversed(lines):
                try:
                    line = json.loads(raw)
                    at = datetime.fromisoformat(line["t"])
                except (ValueError, KeyError, TypeError):
                    continue
                if at < since:
                    out.reverse()
                    return out, True
                out.append({**line, "_at": at})
    out.reverse()
    return out, False


def lines_since(path: Path, since: datetime) -> tuple[list[dict], bool]:
    """The lines since `since` of the query log and, when the current file began after it, of its last rotated copy
    (queries.jsonl.1); and whether the two reach back past since (False: the log began after it)."""
    lines, whole = _query_lines(path, since)
    if not whole:
        older, whole = _query_lines(path.with_name(path.name + ".1"), since)
        lines = older + lines
    return lines, whole


def query_lines(path: Path, since: datetime) -> list[dict]:
    """lines_since's lines."""
    return lines_since(path, since)[0]


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def usage_days(lines: list[dict], now: datetime, kept_from: date | None = None) -> list[list]:
    """[day 'YYYY-MM-DD', calls, searches] for each of the last PUBLIC_DAYS days (Taipei), today last. A day with no
    line is a 0, so a quiet day shows as one; a day before kept_from (the log began that day) is None, None: no record
    is no quiet day."""
    today = now.astimezone(TAIPEI).date()
    days = {(today - timedelta(days=i)).isoformat(): [0, 0] for i in range(PUBLIC_DAYS - 1, -1, -1)}
    for x in lines:
        got = days.get(x["_at"].astimezone(TAIPEI).date().isoformat())
        if got is not None:
            got[0] += 1
            got[1] += int(x.get("tool") == "search" and not x.get("error"))
    return [[d, *((None, None) if kept_from and d < kept_from.isoformat() else (c, s))] for d, (c, s) in days.items()]


def public_usage(path: Path, now: datetime | None = None,
                 nums: dict | None = None) -> tuple[list[list], list[list]]:
    """The public service's last PUBLIC_HOURS from its query log: ([name, tone, words] lines, the last searches that
    found nothing). How long a call took and the calls that failed are said only once the log writes them ('ms',
    'error'); before that the lines say the log does not keep them. nums, when given, gets the same as numbers, and
    the calls of each of the last PUBLIC_DAYS days ('days', usage_days). 2026-11-21: a review of the panels found the
    service students use on no panel; the owner: 「8770那个可以动了」."""
    now = now or now_tpe()
    nums = {} if nums is None else nums
    if not path.exists():
        return [["查詢紀錄", NONE, "讀不到（紀錄檔沒有掛給檢視台，或服務還沒寫過）"]], []
    since = now - timedelta(hours=PUBLIC_HOURS)
    first_day = datetime.combine((now.astimezone(TAIPEI) - timedelta(days=PUBLIC_DAYS - 1)).date(), datetime.min.time(),
                                 TAIPEI)
    week, reached = lines_since(path, min(since, first_day))
    lines = [x for x in week if x["_at"] >= since]
    kept_from = None if reached else week[0]["_at"].astimezone(TAIPEI).date() if week else now.astimezone(TAIPEI).date()
    nums.update(hours=PUBLIC_HOURS, calls=len(lines), days=usage_days(week, now, kept_from))
    if not lines:
        last = next((x for x in reversed(tail(path)) if x.strip()), "")
        try:
            when = stamp(json.loads(last)["t"])
        except (ValueError, KeyError, TypeError):
            when = ""
        return [["查詢", NONE, "沒有人查" + (f"（最後一次 {when}）" if when else "")]], []
    failed = [x for x in lines if x.get("error")]
    done = [x for x in lines if not x.get("error")]
    tools = {k: sum(1 for x in done if x.get("tool") == k) for k in TOOL_WORDS}
    vias = {k: sum(1 for x in lines if x.get("via") == k) for k in VIA_WORDS}
    parts = "、".join(f"{TOOL_WORDS[k]} {n:,}" for k, n in tools.items() if n)
    where_from = "、".join(f"{VIA_WORDS[k]} {n:,}" for k, n in vias.items() if n)
    rows = [["查詢", OK, f"{len(lines):,} 次" + (f"：{parts}" if parts else "") + (f"（{where_from}）" if where_from else "")
             + f"；最後一次 {stamp(lines[-1]['t'])}"]]
    searches = [x for x in done if x.get("tool") == "search"]
    missed = [x for x in searches if not x.get("found")]
    if searches:
        rows.append(["查無資料", OK, f"{len(missed):,} 次，占搜尋的 {_share(len(missed), len(searches))}"])
    new_log = any("ms" in x for x in lines)  # the log writes how long and what failed (2026-11-21 on)
    # how long a search took that answered: a refused argument or a missing document answers in milliseconds and
    # would pull the middle down (the lab's first lines with times, 11-21 23:02: the middle read 0.0 s)
    times = sorted(int(x["ms"]) for x in searches if isinstance(x.get("ms"), (int, float)))
    if 0 < len(times) < PUBLIC_TIMED:  # too few for a middle or a slowest tenth: each said, nothing lit
        rows.append(["多慢", OK, f"答完的 {len(times)} 次各花 " + "、".join(f"{ms / 1000:.1f}" for ms in times) + " 秒"])
    elif times:
        mid, slow = times[len(times) // 2], times[max(0, -(-len(times) * 9 // 10) - 1)]  # nearest-rank tenth
        over = sum(1 for ms in times if ms > PUBLIC_SLOW)
        words = f"一半在 {mid / 1000:.1f} 秒內答完，最慢的一成要 {slow / 1000:.1f} 秒以上"
        if over == 1:  # most likely the first search after a restart, which loads the models
            words += f"；超過 {PUBLIC_SLOW // 1000} 秒的只有 1 次（服務重啟後第一次搜尋要先載入模型，約 17 秒）"
        rows.append(["多慢", WARN if slow > PUBLIC_SLOW and over >= 2 else OK, words])
    else:
        rows.append(["多慢", NONE, "沒有答完的搜尋可以算" if new_log else "紀錄還沒記每次花多久"])
    if failed:
        kinds: dict[str, int] = {}
        for x in failed:
            kinds[str(x["error"])] = kinds.get(str(x["error"]), 0) + 1
        ours = sum(n for k, n in kinds.items() if k != "bad_args")
        rows.append(["沒答成", WARN if ours else OK, f"{len(failed):,} 次：" + "、".join(
            f"{FAIL_WORDS.get(k, k)} {n:,}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))])
    elif new_log:
        rows.append(["沒答成", OK, "沒有"])
    else:
        rows.append(["沒答成", NONE, "紀錄還沒記沒答成的那幾次"])
    shown = [[stamp(x["t"]), VIA_WORDS.get(x.get("via"), x.get("via") or ""), str(x.get("query") or "")]
             for x in reversed(missed[-PUBLIC_MISSED:])]
    errors: dict[str, int] = {}
    for x in failed:
        errors[str(x["error"])] = errors.get(str(x["error"]), 0) + 1
    enough = len(times) >= PUBLIC_TIMED
    nums.update(tools=tools, via=vias, searches=len(searches), missed=len(missed), timed=len(times),
                mid_ms=times[len(times) // 2] if enough else None,
                slow_ms=times[max(0, -(-len(times) * 9 // 10) - 1)] if enough else None,
                over_ms=sum(1 for ms in times if ms > PUBLIC_SLOW), failed=len(failed),
                failed_ours=sum(n for k, n in errors.items() if k != "bad_args"), errors=errors,
                kept_failures=new_log, last=lines[-1]["t"])
    return rows, shown


def search_slots() -> int:
    """How many searches the public service runs at once (nknu.retrieval.public.MAX_SEARCHES, a BoundedSemaphore only
    searches take): read from the module when it is loaded, else from its source (importing it would load the service's
    whole stack), else 4."""
    import sys

    mod = sys.modules.get("nknu.retrieval.public")
    if mod is not None and isinstance(getattr(mod, "MAX_SEARCHES", None), int):
        return int(mod.MAX_SEARCHES)
    try:
        import importlib.util

        spec = importlib.util.find_spec("nknu.retrieval.public")
        text = Path(spec.origin).read_text(encoding="utf-8") if spec and spec.origin else ""
    except (ImportError, OSError, ValueError):
        text = ""
    m = re.search(r"^MAX_SEARCHES\s*=\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else 4


def _pick(sorted_values: list, q: float):
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * q))] if sorted_values else None


def usage_of(lines: list[dict], slots: int | None = None) -> dict[str, dict]:
    """The public service's use per Taipei day, from its query log's lines (lines_since's, with '_at': the call's end;
    'ms' how long it took): the design's 網站訪問量 · 每日 (research/design_2026-11-23/scripts/load2.py), with the
    critic's correction (CR-12, 11-23): how busy the service was and how many ran at once are over the searches only,
    the calls that hold one of the `slots` (search_slots()). Per day:

      calls, search        every call; the searches among them
      qpm_peak, _at        the busiest minute's calls and its HH:MM
      conc_peak            the most searches running at once (their spans t − ms .. t, swept)
      load_p95, load_max   per minute with a search running: the share of the slots its searches held (busy seconds
                           / (60 × slots)), the 95th percentile and the most (over 1: searches waited); active_min:
                           the minutes counted
      p50_ms, p90_ms, p99_ms   how long a call took, every call with a time (waiting included)
      errors, err_rate     the calls that failed for the service's reasons (busy, unavailable, internal: not
                           bad_args, the caller's own), over every call
    """
    slots = slots or search_slots()
    days: dict[str, list[tuple[datetime, object, object, object]]] = {}
    for x in lines:
        at = x.get("_at")
        if not isinstance(at, datetime):
            at = when_of(x.get("t"))
            if at is None:
                continue
        at = at.astimezone(TAIPEI)
        days.setdefault(at.date().isoformat(), []).append((at, x.get("ms"), x.get("error"), x.get("tool")))
    out: dict[str, dict] = {}
    for day, rs in sorted(days.items()):
        per_min: dict[datetime, int] = {}
        busy: dict[datetime, float] = {}
        spans: list[tuple[datetime, datetime]] = []
        for at, ms, _err, tool in rs:
            minute = at.replace(second=0, microsecond=0)
            per_min[minute] = per_min.get(minute, 0) + 1
            if tool != "search" or not isinstance(ms, (int, float)) or ms < 0:
                continue
            a, b = at - timedelta(milliseconds=ms), at
            spans.append((a, b))
            m = a.replace(second=0, microsecond=0)
            while m < b:
                n = m + timedelta(minutes=1)
                busy[m] = busy.get(m, 0.0) + (min(b, n) - max(a, m)).total_seconds()
                m = n
        loads = sorted(v / (60 * slots) for v in busy.values())
        events = sorted([(a, 1) for a, _b in spans] + [(b, -1) for _a, b in spans])
        cur = top = 0
        for _t, k in events:
            cur += k
            top = max(top, cur)
        ms_all = sorted(r[1] for r in rs if isinstance(r[1], (int, float)))
        errors = sum(1 for r in rs if r[2] and r[2] != "bad_args")
        peak_m, peak_n = max(per_min.items(), key=lambda kv: (kv[1], -kv[0].timestamp())) if per_min else (None, 0)
        out[day] = {"calls": len(rs), "search": sum(1 for r in rs if r[3] == "search"), "qpm_peak": peak_n,
                    "qpm_peak_at": peak_m.strftime("%H:%M") if peak_m else "", "conc_peak": top,
                    "load_p95": round(_pick(loads, 0.95) or 0, 3), "load_max": round(loads[-1], 3) if loads else 0,
                    "active_min": len(busy), "p50_ms": _pick(ms_all, 0.5), "p90_ms": _pick(ms_all, 0.9),
                    "p99_ms": _pick(ms_all, 0.99), "errors": errors,
                    "err_rate": round(errors / len(rs), 4) if rs else 0, "slots": slots}
    return out


def usage_empty() -> dict:
    """usage_of's numbers of a day nobody called."""
    return {"calls": 0, "search": 0, "qpm_peak": 0, "qpm_peak_at": "", "conc_peak": 0, "load_p95": 0, "load_max": 0,
            "active_min": 0, "p50_ms": None, "p90_ms": None, "p99_ms": None, "errors": 0, "err_rate": 0,
            "slots": search_slots()}


def usage_day(public_log: Path | None, day: str) -> dict:
    """One Taipei day's use of the public service (usage_of's numbers; zeros for a day nobody called), and whether the
    log reaches back to the day's start ('complete': False when it began, or was rotated away, after it)."""
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), TAIPEI)
    empty = usage_empty()
    if public_log is None:
        return {"day": day, **empty, "complete": False}
    lines, whole = lines_since(public_log, start)
    end = start + timedelta(days=1)
    mine = [x for x in lines if start <= x["_at"] < end]
    return {"day": day, **(usage_of(mine).get(day) or empty), "complete": bool(whole)}


def serve_step(where: Where) -> Step:
    step = Step("serve", "查詢服務", "給學生和他們的 AI 用的對外服務（MCP、網頁、接口），檢視台「測試」用的 MCP，和資料庫")
    box = machine_read(where)
    step.sides, step.sides_label = [[m["name"], m["tone"], m["words"]] for m in box], "這台電腦"
    step.nums["machine"] = [{k: v for k, v in m.items() if k != "words"} for m in box]
    if where.public_log is not None:
        step.nums["usage"] = {}
        step.usage, step.usage_missed = public_usage(where.public_log, nums=step.nums["usage"])
        step.usage_label = f"對外服務最近 {PUBLIC_HOURS} 小時"
    low = [(name, tone, words) for name, tone, words in step.sides if tone in (WARN, BAD)]
    if not where.services:
        step.say = "沒設定要看哪些服務"
        return step
    built_at = library_facts(where.library)["built_at"] or ""
    answers = [ask_service(*s) for s in where.services]
    down = [a for a in answers if not a["up"]]
    step.into = [("個服務", len(answers))]
    step.out = [("個有回應", len(answers) - len(down))]
    step.figure, step.unit = f"{len(answers) - len(down)}/{len(answers)}", "個服務有回應"
    step.bar = [len(answers) - len(down), len(answers)]
    step.notes = [f"{a['name']}：{a['words']}" for a in answers]
    late = []
    for a in answers:
        if a.get("built") and a["name"] and a.get("built") != built_at and not built_at.startswith(
                str(a["built"]).replace(" ", "T")):
            late.append(a)
    state, fix = OK, ""
    say = "都有回應" + "".join(f"；{a['words']}" for a in answers if a["name"].startswith("搜尋"))
    if late:
        state, fix = WARN, CONTENT
        say = "、".join(f"{a['name']}還開著舊庫（{stamp(a['built'])} 那份）" for a in late) + "：換庫後要重啟它"
    stale = [a for a in answers if a.get("stale")]
    if stale:  # the public service reads the library in use on every query: an old one means the nights stopped
        state, fix = WARN, CONTENT
        say = "；".join(f"{a['name']}{a['words'].rsplit('；', 1)[-1]}" for a in stale)
    if down:
        state = BAD
        fix = INTERFACE if any(a["name"].startswith("搜尋") or a.get("interface") for a in down) else PANEL
        say = "、".join(a["name"] for a in down) + " 沒有回應"
        step.lost = [(f"{a['name']}：{a['words']}", 1) for a in down]
        step.lost_label = "沒回應的服務"
    for name, tone, words in low:  # a drive running out, or the card's memory nearly full: the step is lit for it
        state = worst(state, tone)
        say += f"；{name}{words}"
    for name, tone, words in step.usage:  # the public service failing to answer, or slow
        if tone in (WARN, BAD):
            state = worst(state, tone)
            say += f"；對外服務{name}：{words}"
    step.at = now_tpe().isoformat(timespec="seconds")
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 每晚進庫

NIGHT_WORDS = {"running": "還在跑", "swapped": "換上了新庫", "held": "把關沒過，沒換庫", "nothing_new": "沒有新資料，沒重建",
               "paused": "暫停中：只轉文字，沒建庫", "deferred": "轉文字兩小時內沒走完，這晚沒建庫",
               "failed": "失敗，庫沒動", "rolled_back": "退回了前一份庫", "busy": "前一晚還在跑，這晚沒開"}
NIGHT_STEPS = {"plan": "比清單", "repoint": "改清單", "convert": "轉文字", "keep": "留舊庫", "build": "建庫和檢查",
               "done": "收尾"}
NIGHT_FIGURE = {"running": "在跑", "swapped": "換了新庫", "held": "沒換庫", "nothing_new": "沒新資料", "paused": "暫停中",
                "deferred": "沒建庫", "failed": "失敗", "rolled_back": "退回了", "busy": "沒開"}
NIGHT_STATE = {"swapped": OK, "nothing_new": OK, "running": OK, "held": WARN, "paused": WARN, "deferred": WARN,
               "rolled_back": WARN, "busy": WARN, "failed": BAD}


def nightly_step(where: Where) -> Step:
    """The nightly ingest (spec docs/superpowers/specs/2026-11-16-nightly-ingest-design.md; content session 9's
    nightly_ingest.py; its status files' fields agreed with the dashboard session 11-16). It runs at night only once
    the crawler's nightly script calls it (its last line, due about 11-18): until a night run is in its history, a
    missed night is not an alarm 【Claude】."""
    step = Step("nightly", "每晚進庫", "每晚接在爬蟲後面：比清單 → 改清單 → 轉文字 → 留舊庫 → 建庫和檢查 → 換庫")
    pipe = where.pipe
    status = read_json(pipe / "ingest-status.json")
    history = read_lines(pipe / "ingest-history.jsonl")
    if not isinstance(status, dict):
        step.say = "每晚進庫還沒跑過（還沒有它的狀態檔）"
        return step
    outcome = str(status.get("outcome") or "")
    night = str(status.get("night") or "")
    step.at = status.get("started_at") or ""
    finished = when_of(status.get("finished_at"))
    step.took = span(ago(status.get("started_at"), finished)) if finished else ""
    words = NIGHT_WORDS.get(outcome, outcome)
    if outcome == "running":
        words += f"（在「{NIGHT_STEPS.get(status.get('step'), status.get('step'))}」這一步）"
    new = status.get("sites_new") or []
    lib = status.get("library") or {}
    before, after = lib.get("before") or {}, lib.get("after") or {}
    step.into = [("個網站有新資料", len(new))]
    if before and after:
        step.out = [("份文件（換前）", int(before.get("docs") or 0)), ("份文件（換後）", int(after.get("docs") or 0))]
    held = (status.get("held") or {}).get("detail") or []
    step.lost = [(f"{h.get('site')}：{h.get('before'):,} → {h.get('after'):,} 份", 1) for h in held
                 if isinstance(h.get("before"), int) and isinstance(h.get("after"), int)]
    step.lost_label = "文件數掉到一半以下的網站"
    for key, label in (("sites_unlisted", "清單外的新網站（沒自動加進庫）"), ("sites_not_ready", "新資料夾還沒導完")):
        if status.get(key):
            step.notes.append(f"{label}：" + "、".join(map(str, status[key][:8])))
    if status.get("new_reminders"):
        step.notes.append("建庫查出的新問題：" + "、".join(f"{r.get('site')} {FLAG_WORDS.get(r.get('kind'), r.get('kind'))}"
                                                for r in status["new_reminders"][:8] if isinstance(r, dict)))
    if (pipe / "ingest.pause").exists():
        step.paused = True
        step.notes.append("現在是暫停的（有 ingest.pause）：每晚只轉文字、不換庫")
    state = NIGHT_STATE.get(outcome, WARN)
    fix = CONTENT if state != OK else ""
    say = f"{night} 那一晚：{words}" + (f"；原因：{status['error']}" if status.get("error") else "")
    step.figure, step.unit = NIGHT_FIGURE.get(outcome, words[:6]), night[5:] if night else ""
    now = now_tpe()
    if outcome == "running":
        running_for = ago(status.get("started_at"), now)
        if running_for is not None and running_for > INGEST_LONG:
            state, fix = WARN, CONTENT
            say = f"{night} 那一晚跑了 {span(running_for)} 還沒完（在「{NIGHT_STEPS.get(status.get('step'), '')}」）"
    nights: dict[str, str] = {}
    at_night = False
    for line in history:
        if line.get("night"):
            nights[str(line["night"])] = str(line.get("outcome") or "")
        started = when_of(line.get("started_at"))
        at_night = at_night or (started is not None and started.hour < 7)
    if at_night:
        # a night that should have run and did not: the crawler starts at 01:00 and the ingest ends near 04:00, so
        # from 07:00 on, last night's line must be there 【Claude】
        due = (where.today() - timedelta(days=1 if now.hour >= 7 else 2)).isoformat()
        if night and night < due and outcome != "running":
            state, fix = BAD, CONTENT
            say = f"最後一次是 {night} 那一晚（{words}），之後沒有再跑"
    else:
        step.notes.insert(0, "還沒在夜裡跑過：等爬蟲把它接在每晚更新的最後（預計 11-18～19）")
        if state == OK:
            state = NONE
    if len(nights) > 1:
        recent = sorted(nights.items())[-7:]
        step.notes.append("最近幾晚：" + "、".join(f"{n[5:]} {NIGHT_WORDS.get(o, o)}" for n, o in recent))
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ one site 一個網站


def site_detail(where: Where, key: str) -> dict | None:
    """One site through the pipeline, for its own page: what the library holds of it (with its merging and its
    reminders), its export as the library read it (the crawl's results and what the export says is missing, by
    reason, with examples), its to-text accounting and the crawler's newer export, if any. None when the library
    holds no such site."""
    facts = library_facts(where.library)
    site = next((s for s in facts["sites"] if s.get("site") == key), None)
    if site is None:
        return None
    out: dict = {"site": site, "export": None, "text": None, "newer": None, "errors": [],
                 "copies": ((facts["dedupe"].get("copies") or {}).get("by_site") or {}).get(key)
                 if isinstance(facts["dedupe"], dict) else None}
    if site.get("export"):
        folder = folder_of(where.root, site["export"])
        try:
            out["export"] = {"folder": folder.name, **manifest_summary(folder)}
        except (OSError, ValueError) as exc:
            out["errors"].append(f"讀不到這個網站的導出清單（{folder.name}：{type(exc).__name__}）")
        newer = current_exports(where.root).get(key)
        if newer is not None and newer.name != folder.name:
            out["newer"] = newer.name
    try:
        out["text"] = accounting(where.pipe / "totext" / "accounting.jsonl").get(key.replace("/", "_"))
    except OSError:
        out["errors"].append("讀不到建庫時的轉文字記帳")
    flags, _new, current = site_flags(read_json(where.library.with_name("site_checks.json")), facts["built_at"])
    out["flags"] = [FLAG_WORDS.get(f, f) for f in flags.get(key, [])] if current else []
    return out


# ------------------------------------------------------------------------------------------------ the reading


def link_words(rows: list[list]) -> list[list]:
    """對帳 rows in the 11-23 words (NV38, the design's content_night.py: [sign, link_name(words), n,
    link_tip(hover)]): 爬取已取得, 依設計不單列, 清單已取得 …; each 「name：n」 of a hover renamed, an explaining
    sentence shortened (names.NOTES). Renaming twice changes nothing."""
    out = []
    for row in rows or []:
        sign, words, n, tip = (list(row) + ["", "", 0, ""])[:4]
        out.append([sign, link_name(str(words)), n, link_tip(str(tip or ""))])
    return out


def guarded(probe: Callable[[Where], Step], where: Where, key: str, name: str) -> Step:
    """probe's step, or a step saying the panel could not read it (the panel's own problem, never the step's)."""
    started = time.monotonic()
    try:
        step = probe(where)
    except Exception as exc:
        log.exception("流水线：读不到「%s」的纪录", name)
        step = Step(key, name, "", state=WARN, fix=PANEL,
                    say=f"檢視台讀不到這一步的紀錄（{type(exc).__name__}: {exc}）")
    step.notes.append(f"讀這一步花了 {span(time.monotonic() - started)}")
    step.link = link_words(step.link)
    if not step.lost_title:
        step.lost_title = TITLES.get(step.lost_label, step.lost_label.split("（")[0])
    if step.lost and not step.lost_keys:  # a step that names no keys: its words are its keys
        step.lost_keys = [[w, w, n] for w, n in step.lost]
    return step


def overall(steps: list[Step], sites: list[dict] | None = None) -> tuple[str, str]:
    """The page's first line: the worst verdict any step has and which steps have it, and how many sites have
    something to look at (a site's problem does not make its step's verdict: the step ran as it should)."""
    marked = sum(1 for r in sites or [] if r.get("worst") in (BAD, WARN))
    tail = f"；{marked} 個網站要留意" if marked else ""
    for state in (BAD, WARN):
        hit = [s for s in steps if s.state == state]
        if hit:
            names = "、".join(s.name for s in hit)
            return state, (f"有 {len(hit)} 處出錯：{names}" if state == BAD else f"有 {len(hit)} 處要留意：{names}") + tail
    return (WARN if marked else OK), "每一步都正常" + tail


PROBES: list[tuple[str, str, Callable[[Where], Step]]] = [
    ("crawl", "爬取", crawl_step), ("export", "導出", export_step), ("totext", "轉文字", totext_step),
    ("build", "建庫", build_step), ("check", "品質檢查", check_step), ("serve", "查詢服務", serve_step),
    ("nightly", "每晚進庫", nightly_step)]


class Pipeline:
    """The steps as last read, read again in a background thread when older than every seconds (or when asked)."""

    def __init__(self, where: Where, probes: list[tuple[str, str, Callable[[Where], Step]]] | None = None,
                 every: float = EVERY, kept: object | None = None) -> None:
        self.where = where
        self.probes = probes if probes is not None else PROBES
        self.every = every
        self._lock = threading.Lock()
        self._snap: dict | None = None
        self._read_at = 0.0
        self._thread: threading.Thread | None = None
        # nknu.ops.keep.Kept: the last reading before a restart, shown (said so) until the first new one ends; never
        # what last() gives, so the nights' record is kept from a reading of this process only (2026-11-22 night)
        self.kept = kept
        before = kept.get("pipeline", "last") if kept is not None else None
        self._before: dict | None = {**before, "kept": True} if isinstance(before, dict) and before.get("steps") else None

    def _run(self) -> None:
        started = time.monotonic()
        began = now_tpe().isoformat(timespec="seconds")
        self.where.sites = {}
        steps = [guarded(probe, self.where, key, name) for key, name, probe in self.probes]
        try:
            from nknu.ops.look import t2s
            sites = site_table(self.where, t2s)
        except Exception:  # the steps still show without the table
            log.exception("流水线：每站的表没做成")
            sites = []
        state, say = overall(steps, sites)
        snap = {"at": now_tpe().isoformat(timespec="seconds"), "began": began, "took": span(time.monotonic() - started),
                "state": state, "say": say, "steps": [asdict(s) for s in steps], "sites": sites,
                "site_cols": [c for c in SITE_COLS if any(c[0] in r["cells"] for r in sites)]}
        with self._lock:
            self._snap, self._read_at = snap, time.monotonic()
        if self.kept is not None:
            self.kept.put("pipeline", "last", snap)

    def snapshot(self, fresh: bool = False) -> dict:
        """The last reading ({'at', 'took', 'state', 'say', 'steps', 'sites', 'site_cols'}, plus 'running': a reading
        is under way), a new one started when it is older than every (or fresh). Before the first reading ends:
        {'steps': [], 'running': True}."""
        with self._lock:
            stale = self._snap is None or fresh or time.monotonic() - self._read_at > self.every
            running = self._thread is not None and self._thread.is_alive()
            if stale and not running:
                self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
                self._thread.start()
                running = True
            snap = dict(self._snap) if self._snap else dict(self._before) if self._before else {
                "steps": [], "state": NONE, "say": "", "at": ""}
        snap["running"] = running
        return snap

    def last(self) -> dict | None:
        """The last reading as it is, however old, and no new one started (None before the first has ended)."""
        with self._lock:
            return dict(self._snap) if self._snap else None

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
