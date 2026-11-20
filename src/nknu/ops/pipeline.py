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

import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from nknu.core.timeutil import TAIPEI

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
    figure: str = ""  # the step's one number on the flow map (with unit): what it put out, or its state in two words
    unit: str = ""
    bar: list[int] = field(default_factory=list)  # [what came out, what went in], one unit: the step's bar; [] none
    share: bool = True  # whether the bar says its percentage (not where the rest is by design, as the build's merges)
    flow_words: str = ""  # one sentence in place of the in/out, where they are not a loss (the export's rows)
    paused: bool = False  # the nightly ingest is switched off by hand (ingest.pause): the home says so on top


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
                        " ORDER BY rowid DESC LIMIT 2").fetchall()
    if not rows:
        return {"state": NONE, "words": "爬蟲還沒跑過", "at": "", "took": "", "running": False}
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


def crawl_buckets(conn: sqlite3.Connection, sites: set[str], batch_sites: list[str] = ()
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
    return out, batch


PLAIN_BUCKET = {"要人看": "待確認"}


def bucket_words() -> dict[str, str]:
    _outcome, progress = _crawler()
    # Taiwan usage; the crawler's 要人看 (fetched but maybe incomplete or wrong, or left alone by its own limits: a
    # dozen causes in one bucket) is 待確認 here: the owner (11-16) named 要人看 among the names that say nothing
    return {k: PLAIN_BUCKET.get(tw(v), tw(v)).replace("登錄", "登入")
            for k, v in getattr(progress, "BUCKET_ZH", {}).items()}


def crawl_step(where: Where) -> Step:
    step = Step("crawl", "爬取", "爬蟲照網站清單抓網頁和檔案，存進帳本和存檔；每晚再看一次各站的列表和首頁")
    step.more = [("看爬蟲進度", "crawl"), ("網頁核查", "check")]
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
        buckets, batch = crawl_buckets(conn, keys, open_sites)
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
    step.lost = [(words.get(b, b), n) for b, n in sorted(totals.items(), key=lambda bn: -bn[1])
                 if b != "got" and b not in NOT_MEANT and n]
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
    step.state, step.say = state, say
    step.fix = CRAWLER if state in (BAD, WARN) else ""
    return step


def page_check_cells(where: Where, step: Step) -> None:
    """網頁核查's sweep (in this process: nknu.ops.sweep) as numbers of the crawl step and of every site's row. Neutral
    on purpose 【Claude】: what it finds is handed to the crawler session in batches (the owner's rule of 11-16), so a
    long-known miss is a number to see, not an alarm every morning."""
    view = where.sweep() if where.sweep else None
    if not view or not view.get("totals"):
        phase = ((view or {}).get("progress") or {}).get("phase")
        if phase in ("reading", "checking"):
            step.notes.append("網頁核查正在逐頁核對，核完才有每站的漏抓數字")
        elif where.sweep:
            step.notes.append("網頁核查還沒跑過（打開「網頁核查」那一頁就會開始，約 17 分鐘）")
        return
    totals, progress = view["totals"], view.get("progress") or {}
    done = "核完" if progress.get("phase") == "done" else f"核了 {progress.get('done', 0):,}/{progress.get('total', 0):,} 頁"
    step.notes.append(f"網頁核查（{done}）：正文區有漏 {totals['gap']:,} 頁、沒抓下來 {totals['nofetch']:,} 頁、"
                      f"要查 {totals['check']:,} 頁")
    for site in view.get("sites") or []:
        lost = (site.get("gap") or 0) + (site.get("nofetch") or 0)
        tip = (f"網頁 {site.get('pages', 0):,} 頁；正文區有漏 {site.get('gap', 0):,}、沒抓下來 {site.get('nofetch', 0):,}、"
               f"要查 {site.get('check', 0):,}、選單沒抓到的連結 {site.get('menu_links', 0):,}")
        cell(where, site["key"], "check", lost, tip=tip, sub=f"{site.get('pages', 0):,} 頁裡" if lost else "",
             name=site.get("name") or "")


# ------------------------------------------------------------------------------------------------ 導出

FETCHED = "抓到了"  # the export's 结果 for a row the crawler has (nknu.archive.export)
MISSING_SHOWN = 20  # examples kept of each kind of 缺什么 (the site page's reason lists) 【Claude】
_manifest_sums: dict[tuple[str, int], dict] = {}  # (manifest path, its time of change) -> its summary


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
    paging = files = 0
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
        for part in missing_parts(str(r.get("缺什么") or "")):
            missing[part] = missing.get(part, 0) + 1
            if len(shown := missing_examples.setdefault(part, [])) < MISSING_SHOWN:
                shown.append((str(r.get("网址") or ""), str(r.get("标题") or "")))
        if str(r.get("页别") or "") == "列表翻页":
            paging += 1
        if (saved := r.get("本地档案")) and saved not in seen:  # one file of several rows (a redirect's landing page,
            seen.add(saved)  # a Google Sites picture several pages show, 11-19) is one file
            files += 1
        origin = str(r.get("来源") or "")
        if origin in REFILL:
            refill[origin] = refill.get(origin, 0) + 1
            if len(refills) < 300:
                refills.append((str(r.get("网址") or ""), str(r.get("标题") or ""), origin, str(r.get("备注") or "")))
    summary = {"site": manifest.get("网站") or "", "name": manifest.get("名称") or "",
               "exported_at": manifest.get("导出时间") or "", "as_of": manifest.get("资料截至") or "",
               "rows": len(rows), "files": files, "kinds": kinds, "outcomes": outcomes, "missing": missing,
               "paging": paging, "refill": refill, "refills": refills,
               "examples": examples, "missing_examples": missing_examples}
    _manifest_sums[key] = summary
    while len(_manifest_sums) > 400:  # about three exports a site at most are ever asked about
        _manifest_sums.pop(next(iter(_manifest_sums)))
    return summary


# what 缺什么 can say, by the words a part holds (nknu.archive.export: progress's reasons, then the export's own marks;
# a part can hold 「、」 itself, so it is never split on it), in the order they are looked for
MISSING_WORDS = (("学校回答没有资料", "學校回答「沒有資料」"), ("内文区是空的", "內文區是空的"),
                 ("找不到内文区", "找不到內文區"), ("列表没翻完", "列表沒翻完"), ("待判定", "待判定"),
                 ("缺内嵌页或按钮", "頁面裡的內嵌頁或按鈕沒抓到"), ("学校那边已不存在", "連到的資源學校已不存在或要登入"),
                 ("连接失败", "連線失敗"), ("缺附件", "附件沒抓到"), ("缺图片", "圖片沒抓到"), ("下一轮", "留到下一輪補抓"),
                 ("要人看", "待確認"))


# 来源 of a row whose file is an older answer than this round's (nknu.archive.export ORIGIN_OLD, ORIGIN_PREV): this
# round's answer was broken or empty, so the export gave the last good one and its 结果 stays this round's (owner 11-17:
# 「他说爬下来的坏了，目前是用旧的资料补上的，我要去哪个面板看到」)
REFILL = {"10-24 旧存档": "10-24 舊存檔", "上一次抓到的": "每晚更新前一次抓到的"}


# a page the rules leave out says so in 缺什么 (the crawler of 11-18: 「按规则不抓：整页跳到校外页（用户 11-17 决定不抓）」,
# outcome.OFFSITE_LANDED's label): that is a decision, not something missing
RULE_MARK = "按规则不抓"


def missing_parts(words: str) -> list[str]:
    """The kinds of what a row's 缺什么 names (MISSING_WORDS' names), each once; words naming none of them are one
    kind of their own."""
    words = words.strip()
    if not words:
        return []
    found = [name for mark, name in MISSING_WORDS if mark in words]
    if not found and RULE_MARK in words:  # left out by rule: nothing is missing (no kind of its own, no gap drawn)
        return []
    return found or [tw(words.split("（", 1)[0])]


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


def export_step(where: Where) -> Step:
    """The exports the library was built from (sites.export), each summed up from its manifest, and the crawler's
    newer exports the library has not taken yet (current.tsv against sites.export)."""
    step = Step("export", "導出", "爬蟲把每個網站抓到的網頁和檔案整理成一份清單和一個資料夾，給轉文字和建庫用")
    step.more = [("看每站的數字", "#sites")]
    facts = library_facts(where.library)
    newest = current_exports(where.root)
    night = night_export(where.archive)
    held, paused = export_held(night), export_paused(night)  # the crawler held these sites' new export (11-17)
    rows = fetched = with_files = 0
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
    step.flow_words = f"清單共 {rows:,} 筆（每筆一個網頁、檔案或圖片），其中 {with_files:,} 筆存下了檔案"
    step.out = [("筆存下了檔案", with_files)]
    step.lost = [(k, v) for k, v in sorted(missing.items(), key=lambda kv: -kv[1])][:8]
    step.lost_label = "清單上註明缺東西的（一筆可能缺好幾樣）"
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
    if unread:
        state, fix = WARN, CONTENT
        say = f"庫用的導出有 {len(unread)} 站讀不到了：" + "、".join(unread[:5])
    step.state, step.say, step.fix = state, say, fix
    return step


# ------------------------------------------------------------------------------------------------ 轉文字

STATUS_WORDS = {"ok": "轉成文字", "partial": "轉了一部分", "empty": "讀不出文字", "skipped": "照規則跳過", "failed": "轉文字失敗",
                "pending": "還沒轉"}
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
    step.out = [("個轉成文字", int(by_status.get("ok") or 0))]
    step.figure, step.unit = f"{int(by_status.get('ok') or 0):,}", "個轉成文字"
    step.bar = [int(by_status.get("ok") or 0), total] if total else []
    step.lost = [(STATUS_WORDS[s], int(by_status.get(s) or 0)) for s in ("empty", "skipped", "partial", "failed",
                                                                        "pending") if by_status.get(s)]
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
        cell(where, site["site"], "text", status.get("ok", 0), tip="\n".join(tip) or "全部轉成文字了",
             sub=f"讀不出字 {empty:,}" if empty else "", name=site.get("name") or "")
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
LEFT_OUT = ((lambda k: k.endswith("(重复系列)"), "每週重複的行事曆，合成一個系列"),
            (lambda k: k.endswith("(副本)"), "別處有一模一樣的一份，合併了"),
            (lambda k: k.endswith("(重复)"), "同一網址重複導出，只留一份"),
            (lambda k: "(列表翻页)" in k or "#翻页" in k, "公告列表的分頁（各則另外收了）"),
            (lambda k: "2023" in k, "2023 年以前的行事曆"),
            (lambda k: "学校回答没有资料" in k, "學校這頁回答「沒有資料」"),
            (lambda k: "选单" in k, "只在選單出現的圖片"),
            (lambda k: "学校删掉" in k or "学校已删除" in k, "學校刪掉的公告，只剩空殼"),
            (lambda k: k.endswith("(其他格式)"), "同一個檔的其他格式"),
            (lambda k: k.endswith("(空白)"), "沒標題也沒讀到字"),
            (lambda k: "(" not in k, "列表、相簿、網站地圖這類導覽頁（照種類不收）"))


def left_out_words(key: str) -> str:
    for test, words in LEFT_OUT:
        if test(key):
            return words
    return tw(key)


# site_report.FLAG_WORDS's keys, in the words 資料總覽 shows them (deploy/lab-overview/page.html's CK; one concept, one
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
    step.lost_label = "沒成為文件的（大多是合併掉的重複）"
    sites = len(facts["inputs"]) if isinstance(facts["inputs"], list) else len(facts["sites"])
    # what went in: the documents plus what the build left out or merged (an approximation the survey of 11-16 found
    # close: a record two exports both hold counts once)
    before = facts["docs"] + sum(left.values())
    step.into = [(f"筆整理前的網頁、檔案和行事曆（{sites} 站）", before)]
    step.out = [("份文件", facts["docs"]), ("個小段（搜尋時比對的單位）", facts["chunks"])]
    step.figure, step.unit, step.bar = f"{facts['docs']:,}", "份文件", [facts["docs"], before] if before else []
    step.share = False  # its share is mostly merged repeats: a percentage would read as a loss
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
    step = Step("check", "品質檢查", "建庫最後跑的兩種檢查：33 題測試題（標準答案排第幾）和 22 項真資料檢查")
    step.more = [("看 33 題", "board")]
    built_at = library_facts(where.library)["built_at"]
    off = offline_metrics(where.pipe / "offline_new_r3.json")
    real = realdata(where.pipe / "checks.md")
    if off is None and real is None:
        step.say = "還沒有建庫的檢查結果"
        return step
    parts = []
    stale = []
    if off is not None:
        q, a = off["question"], off["agent"]
        if off["built_at"] != built_at:
            stale.append("33 題")
        step.into = [("題測試題", int(q.get("n") or 33))]
        step.out = [("題答案排進前 10", int(q.get("top10") or 0)), ("題排第 1", int(q.get("top1") or 0))]
        step.figure, step.unit = f"{int(q.get('top10') or 0)}/{int(q.get('n') or 33)}", "題答案排進前 10"
        step.bar = [int(q.get("top10") or 0), int(q.get("n") or 33)]
        parts.append(f"33 題測試：答案排第 1 的 {q.get('top1')} 題、前 3 的 {q.get('top3')} 題、前 10 的 {q.get('top10')} 題")
        if a:
            step.notes.append(f"讓 AI 自己改寫問題再搜（agent 重放）：第 1 {a.get('top1')}、前 3 {a.get('top3')}、"
                              f"前 10 {a.get('top10')} 題")
    if real is not None:
        if real["built_at"] and real["built_at"] != built_at:
            stale.append("真資料檢查")
        if real["passed"] is not None:
            parts.append(f"真資料檢查 {real['passed'] + real['failed']} 項過了 {real['passed']} 項")
        step.lost = [(f"真資料檢查第 {k} 項：{tw(v)}", 1) for k, v in real["fails"].items()]
        step.lost_label = "沒通過的檢查"
    say = "；".join(parts)
    state, fix = OK, ""
    # 「33 题只当别退步的底线」(the owner's line of 11-08): an alarm only when this build did worse than the one before
    before = previous_reports(where.pipe / "lab_pc")
    if before is not None and not stale:
        old_off = offline_metrics(before / "offline_new_r3.json")
        old_real = realdata(before / "checks.md")
        worse = []
        if off is not None and old_off is not None:
            for k, name in (("top10", "前 10 名"), ("top3", "前 3 名")):
                now_n, then_n = off["question"].get(k), old_off["question"].get(k)
                if isinstance(now_n, int) and isinstance(then_n, int) and now_n < then_n:
                    worse.append(f"33 題進{name}的從 {then_n} 題掉到 {now_n} 題")
        if real is not None and old_real is not None:
            newly = sorted(set(real["fails"]) - set(old_real["fails"]), key=int)
            if newly:
                worse.append("真資料檢查新冒出沒過的：" + "、".join(f"第 {k} 項" for k in newly))
        if worse:
            state, fix = WARN, CONTENT
            say = "比上一次建庫退步了：" + "；".join(worse)
        step.notes.append(f"和上一次建庫比（{before.name}）" + ("：沒有退步" if not worse else ""))
    if stale:
        state = NONE
        say = "、".join(stale) + " 是上一份庫的結果（新庫的檢查還在跑，換庫後約 50 分鐘才跑完）"
    board = where.board() if where.board else None
    if board and board.get("total") and board.get("done") == board.get("total"):
        c = board["counts"]
        step.notes.append(f"檢視台今天用今天的日期重跑 33 題：第 1 名 {c.get('first', 0)}、前 3 名 "
                          f"{c.get('first', 0) + c.get('top3', 0)}、前 10 名 "
                          f"{c.get('first', 0) + c.get('top3', 0) + c.get('top', 0)} 題")
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
    overview: its /stamp (which library its numbers are of); http: any answer counts (the crawler's viewer computes
    every page, so it is never asked for one)."""
    try:
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


def serve_step(where: Where) -> Step:
    step = Step("serve", "查詢服務", "給學生的 AI 用的搜尋服務（MCP），和這幾個面板")
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
    if down:
        state = BAD
        fix = INTERFACE if any(a["name"].startswith("搜尋") for a in down) else PANEL
        say = "、".join(a["name"] for a in down) + " 沒有回應"
        step.lost = [(f"{a['name']}：{a['words']}", 1) for a in down]
        step.lost_label = "沒回應的服務"
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
                 every: float = EVERY) -> None:
        self.where = where
        self.probes = probes if probes is not None else PROBES
        self.every = every
        self._lock = threading.Lock()
        self._snap: dict | None = None
        self._read_at = 0.0
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        started = time.monotonic()
        self.where.sites = {}
        steps = [guarded(probe, self.where, key, name) for key, name, probe in self.probes]
        try:
            from nknu.ops.look import t2s
            sites = site_table(self.where, t2s)
        except Exception:  # the steps still show without the table
            log.exception("流水线：每站的表没做成")
            sites = []
        state, say = overall(steps, sites)
        snap = {"at": now_tpe().isoformat(timespec="seconds"), "took": span(time.monotonic() - started),
                "state": state, "say": say, "steps": [asdict(s) for s in steps], "sites": sites,
                "site_cols": [c for c in SITE_COLS if any(c[0] in r["cells"] for r in sites)]}
        with self._lock:
            self._snap, self._read_at = snap, time.monotonic()

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
            snap = dict(self._snap) if self._snap else {"steps": [], "state": NONE, "say": "", "at": ""}
        snap["running"] = running
        return snap

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
