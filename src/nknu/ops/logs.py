"""紀錄: every step's log as one list, each line with its time, level and source, for 總覽's 日誌 and a night's 紀錄 on
內容 (the 11-23 design copy: research/design_2026-11-23/scripts/logview.py, lvfix.py, tune2.py, content_night.py; owner
11-23: 「看不全…失去了log的作用」). The design baked the last few thousand lines into its page; here the logs are read
where they are, parsed once and kept (only what was added since is read again), and a page asks for one time window,
one page of lines, the counts of its chips and its histogram (window()).

Where the lines come from (the folders pipeline.log_tail reads; nothing from a request names a path):

  爬取 crawl    the crawler's nightly log (<archive>/update.log) and its ledger's event table (frontier.db event: at,
                level, code, host, url, message; the crawler's own warnings and errors with their real level, critic
                CR-07); the HTTP requests are in update.log at INFO, the q-ip: and 「HTTP Request: 」 prefixes cut
  導出 export   the export's own log (archive-export/sites/export.log). Its script tees every line it says to that
                file and to the nightly log too (export_sites.sh say(), nightly_update.sh >> update.log), so a line of
                update.log that export.log also holds (the same time and words) is that same line again and is left
                out, whatever it says (critic CR-06: never twice). An update.log line in the export's words (EXPORT)
                that export.log does not hold (export.log missing, or not reaching back that far) is shown as 導出
  轉文字 totext the three lanes' logs (lab_pc/laneA, laneB, laneC.log): a line's time is the stamp at its end, or the
                last stamp before it
  建庫 build    lab_pc/build.log ([time] lines; the file listings it prints are left out)
  進庫 ingest   lab_pc/ingest.log ([ingest time] lines)

A line without a time of its own takes the last one before it (a traceback's lines, a report's). Levels as the design
guesses them: the logger's INFO / WARNING / ERROR / DEBUG word when there is one, then the words of failure (ERR: 失败,
出错, Traceback, exit 1 … error; 拦下 … warn) and of trouble (WARN: 暂停中, 重试, 没抓到 … warn). Lines are shown as
written: the programs write Simplified Chinese and the logs carry school titles and names, which a conversion would
change (張岳印 → 張嶽印), so nothing is converted (the design ran tw() over every line; not ported). Every line is
masked as pipeline.log_tail masks (a key, a token in an address, an authorization header) before it is kept; the
Google key file is never read. A trailing time at the end of a message is cut (the design's _LOG_B): the row has it.

How much is kept: the last FIRST_READ bytes of each log the first time (the older part is not read), then whatever is
added; at most KEEP_ROWS lines a log. A log that was replaced or cut (rotated) is read again from its end. A window
says it per source: kept_from (the earliest line held of each source), cut_from (from when every log of a source is
held, for a source one of whose logs was not read whole) and short (the sources cut after the window's start: their
lines before that time are not held, so a page says so instead of showing a quiet night). The numbers are
【Claude】."""

from __future__ import annotations

import bisect
import logging
import re
import sqlite3
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from nknu.core.timeutil import TAIPEI
from nknu.ops import pipeline as pl
from nknu.ops.pipeline import Where
from nknu.ops.sources import night_of_run

log = logging.getLogger(__name__)

# The routes this data layer needs in web.py (the integrator wires them; this module never edits web.py). Arguments are
# create_app's locals: where (the Where), store (history.Store); params is request.query_params. Both answer JSON
# (JSONResponse of the returned dict), behind the same guard as /api/log (guarded(handler, True)).
ROUTES_TO_WIRE = [
    {"path": "/api/logs", "method": "GET", "handler": "nknu.ops.logs.api_window", "args": "where, request.query_params",
     "returns": "logs.window()'s dict: rows [[time, level, source, message]] newest first, hist (72 columns), counts "
                "{levels, sources}, more, next (the cursor: send it back as before=), total, matched, first, last, "
                "from, to, kept_from {source: time}, cut_from {source: time}, short [[source, time]], levels, sources. "
                "Query (both pages' spellings: 內容's night.js and 總覽's home.js): date= or run_day= (YYYY-MM-DD, a "
                "night by its run day) or since=/until= (ISO, Taipei), q=, lv= or levels= (error,warn,…), src= or "
                "sources= (crawl,export,…), n= or limit= (≤2000, default 500), before=<cursor>. The one handler of "
                "/api/logs: wire this one and drop panorama.logs_api (HOME's ROUTES_TO_WIRE names the same path)"},
    {"path": "/api/usage/days", "method": "GET", "handler": "nknu.ops.history.usage_api", "args": "store, where, "
     "request.query_params", "returns": "{'days': [{'day', calls, search, qpm_peak, qpm_peak_at, conc_peak, load_p95, "
                                        "load_max, active_min, p50_ms, p90_ms, p99_ms, errors, err_rate, slots, "
                                        "complete, 'today'?}], 'first', 'last', 'slots'}: the kept finished days of "
                                        "first..last (default: this month) and today read live from the query log. "
                                        "Query: first=, last= (YYYY-MM-DD)"},
]

SOURCES = (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("ingest", "進庫"))
LEVELS = (("error", "錯誤"), ("warn", "警告"), ("info", "資訊"), ("debug", "除錯"))
DEFAULT_LEVELS = frozenset({"error", "warn", "info"})  # 除錯 off until asked (the design's chips)
BINS = 72  # the histogram's columns
PAD = timedelta(minutes=10)  # a night's window: its first start − PAD to its last end + PAD (the design's night.py)
SPAN = timedelta(days=7)  # the window when none is given: the last week (the design's 總覽 log covered 11-16 to 11-23)
FIRST_READ = 4 * 2 ** 20  # bytes read from a log's end the first time (a few nights of each) 【Claude】
KEEP_ROWS = 100_000  # lines kept a log 【Claude】
EVENTS_FIRST = 20_000  # ledger events read the first time (the latest) 【Claude】
MSG_MAX = 2000  # characters of a message kept 【Claude】
LIMIT_MAX = 2000

ISO = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:[,.](\d{3}))?\s*(.*)$")
INGEST = re.compile(r"^\[ingest (\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)\]()\s*(.*)$")
# build.sh stamps "[build 2026-11-23 02:43:00] …" (live 11-23: every line was missed without the word)
BUILD = re.compile(r"^\[?(?:build\s+)?(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)\]?()\s*(.*)$")
LANE_STAMP = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*$")
# the export's words (export_sites.sh say()): an update.log line export.log does not hold is shown as 導出 by these
EXPORT = re.compile(r"^(开始 \S+ →|好了 |删掉这个网站|没删这个网站|这一批结束|导出好了|\S+ 没有变动，跳过|写了 .*seen\.tsv"
                    r"|这一批 \d+ 个网站|暂缓 \S+：|\S+ 还没爬完，跳过|拦下 \S+：.*现用的还是|失败 \S+：资料夹 |清掉上次残留的 "
                    r"|另一个导出在用 |找不到内容那边的导出清单|看不到哪些网站爬完了|清单 \S+ 里没有网站|OLD_COPIES=)")
# (the design's words, and the export's own line for one site it failed or held: 「失败 <site>：资料夹…」 is an error,
# 「拦下 <site>：…」 a warning, so a night's 紀錄, errors and warnings by default, shows them)
ERR = re.compile(r"失败 [1-9]|出错|错误|Traceback|exit [1-9]|ERROR|CRITICAL|拦下 [1-9]|拦下的是|^失败 \S+：|^拦下 \S+：")
WARN = re.compile(r"WARNING|暂停中|超过上限|没转完|说不出原因|重试|没抓到")
LEVEL_WORD = re.compile(r"(INFO|WARNING|ERROR|DEBUG|CRITICAL)\s+")
LEVEL_OF = {"WARNING": "warn", "ERROR": "error", "CRITICAL": "error", "DEBUG": "debug", "INFO": "info"}
TRAILING = re.compile(r"\s+\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # a tool's terminal colours (RapidOCR's): never shown as text
# a library's own logger line inside ours: "[WARNING] 2026-11-23 02:42:25,405 [RapidOCR] …" (its time is the row's)
BRACKETED = re.compile(r"\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]\s+(?:\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+\s+)?")
LISTING = ("-rw", "drwx", "total ")  # build.log's file listings (ls -l)


def level_of(msg: str, level: str = "info") -> tuple[str, str]:
    """(level, message without its logger word): the design's guess (logview.py add())."""
    msg = ANSI.sub("", msg)
    m = BRACKETED.match(msg) or LEVEL_WORD.match(msg)
    if m:
        msg = msg[m.end():]
        level = LEVEL_OF[m.group(1)]
    if ERR.search(msg):
        level = "error" if any(w in msg for w in ("失败", "出错", "错误", "Traceback", "exit")) else "warn"
    elif level == "info" and WARN.search(msg):
        level = "warn"
    return level, msg


def mask(text: str) -> str:
    for pattern, word in pl._SECRETS:
        text = pattern.sub(word, text)
    return text


def shown(msg: str) -> str:
    """A message as a row shows it: masked, its trailing time cut, at most MSG_MAX characters."""
    msg = TRAILING.sub("", mask(ANSI.sub("", msg)).rstrip())
    return msg if len(msg) <= MSG_MAX else msg[:MSG_MAX] + "…"


def _epoch(t: str) -> float:
    try:
        return datetime.fromisoformat(t).replace(tzinfo=TAIPEI).timestamp()
    except ValueError:
        return 0.0


class LogFile:
    """One log, parsed as it grows: rows (time ISO, epoch, level, source, message, in the export's words). cut: the
    log was not read whole (begun FIRST_READ from its end, or its oldest lines let go past KEEP_ROWS): it holds no
    line before kept_from."""

    def __init__(self, path: Path, source: str, kind: str) -> None:
        self.path, self.source, self.kind = path, source, kind
        self.rows: list[tuple] = []
        self.version = 0
        self.kept_from = ""
        self.cut = False
        self._lock = threading.Lock()
        self._ino: int | None = None
        self._offset = 0
        self._partial = b""
        self._last = ""  # the last time a line had (lines without one take it)
        self._stamp = ""  # a lane log's last end-of-line stamp
        self._cut = False  # the first line read is a part of one (a read begun inside the file): dropped

    def _reset(self, ino: int, size: int) -> None:
        self.rows, self._ino, self._partial, self._last, self._stamp = [], ino, b"", "", ""
        self._offset = max(0, size - FIRST_READ)
        self._cut = self._offset > 0  # the first line read may be cut: dropped
        self.cut = self._offset > 0
        self.version += 1

    def refresh(self) -> None:
        with self._lock:
            try:
                st = self.path.stat()
            except OSError:
                if self.rows or self._ino is not None:
                    self.rows, self._ino, self._offset, self.version = [], None, 0, self.version + 1
                    self.kept_from, self.cut = "", False
                return
            if self._ino != st.st_ino or st.st_size < self._offset:
                self._reset(st.st_ino, st.st_size)
            if st.st_size == self._offset:
                return
            try:
                with self.path.open("rb") as f:
                    f.seek(self._offset)
                    data = f.read(st.st_size - self._offset)
            except OSError:
                return
            self._offset += len(data)
            lines = (self._partial + data).split(b"\n")
            self._partial = lines.pop()
            if self._cut and lines:
                lines.pop(0)
                self._cut = False
            for raw in lines:
                row = self.parse(raw.decode("utf-8", errors="replace").rstrip("\r"))
                if row is not None:
                    self.rows.append(row)
            if len(self.rows) > KEEP_ROWS:
                del self.rows[:len(self.rows) - KEEP_ROWS]
                self.cut = True
            self.kept_from = self.rows[0][0] if self.rows else ""
            self.version += 1

    def parse(self, raw: str) -> tuple | None:
        if not raw.strip():
            return None
        if self.kind == "build" and raw.startswith(LISTING):
            return None
        if self.kind == "lane":  # the time at a line's end, or the last one before it (logview.py)
            m = LANE_STAMP.search(raw)
            if m:
                self._stamp = m.group(1)
            if self._stamp and not ISO.match(raw):
                raw = f"{self._stamp} {raw}"
        pattern = INGEST if self.kind == "ingest" else BUILD if self.kind == "build" else ISO
        m = pattern.match(raw)
        if m:
            t = f"{m.group(1)}T{m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
            msg = m.group(4)
            self._last = t
        else:
            t, msg = self._last, raw
        if not t:
            return None
        level, msg = level_of(msg)
        src, copy = self.source, False
        if self.source == "crawl" and EXPORT.match(msg):
            src, copy = "export", True  # the export's line as the nightly log has it (merged(): once, CR-06)
        msg = re.sub(r"^q-ip:\S+\s+", "", msg)
        msg = re.sub(r"^HTTP Request: ", "", msg)
        return (t, _epoch(t), level, src, shown(msg), copy)


class LedgerEvents:
    """The crawler ledger's event table, read by rowid as it grows (the latest EVENTS_FIRST the first time)."""

    source = "crawl"

    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.rows: list[tuple] = []
        self.version = 0
        self.kept_from = ""
        self.cut = False  # the older events were not read (EVENTS_FIRST) or were let go (KEEP_ROWS)
        self._last_id = 0
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            if not (self.archive / "frontier.db").exists():
                return
            conn = _ledger(self.archive)
            if conn is None:
                return
            try:
                if not self._last_id:
                    top = conn.execute("SELECT max(rowid) FROM event").fetchone()[0] or 0
                    self._last_id = max(0, top - EVENTS_FIRST)
                    self.cut = self._last_id > 0
                got = conn.execute("SELECT rowid, at, level, code, host, url, message FROM event WHERE rowid > ?"
                                   " ORDER BY rowid", (self._last_id,)).fetchall()
            except sqlite3.Error:
                return
            finally:
                conn.close()
            for rowid, at, level, code, host, url, message in got:
                self._last_id = max(self._last_id, int(rowid))
                when = pl.when_of(at)
                if when is None:
                    continue
                t = when.astimezone(TAIPEI).replace(tzinfo=None).isoformat(timespec="seconds")
                msg = str(message or "")
                words = " ".join(x for x in (f"[{code}]", str(host or ""), msg,
                                             str(url) if url and str(url) not in msg else "") if x)
                lv = {"warn": "warn", "warning": "warn", "error": "error", "debug": "debug"}.get(str(level), "info")
                self.rows.append((t, _epoch(t), lv, "crawl", shown(words), False))
            if got:
                self.rows.sort(key=lambda r: r[1])
                if len(self.rows) > KEEP_ROWS:
                    del self.rows[:len(self.rows) - KEEP_ROWS]
                    self.cut = True
                self.kept_from = self.rows[0][0] if self.rows else ""
                self.version += 1


def _ledger(archive: Path) -> sqlite3.Connection | None:
    """The ledger read-only as the panels open it (pagecheck.open_ledger: its read-only mount's ways), else plainly
    read-only (the event table is all this reads); None when neither opens."""
    try:
        return pl.open_ledger(archive)
    except Exception as exc:  # noqa: BLE001 - busy, an older ledger (OutdatedFrontier), a mount it cannot read
        log.debug("纪录：账本照面板的方式打不开（%s），改成只读直接打开", exc)
    try:
        return sqlite3.connect(f"file:{archive / 'frontier.db'}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


_sources: dict[tuple, object] = {}
_merged: dict[tuple, tuple] = {}
_reg = threading.Lock()


def logs_of(where: Where) -> list:
    """The logs of this install (LogFile, LedgerEvents), kept between requests."""
    kit = where.pipe / "lab_pc"
    want: list[tuple] = []
    if where.archive is not None:
        want += [("file", str(where.archive / "update.log"), "crawl", "iso"), ("events", str(where.archive))]
    want += [("file", str(where.root / pl.EXPORTS / "export.log"), "export", "iso"),
             ("file", str(kit / "ingest.log"), "ingest", "ingest"),
             ("file", str(kit / "build.log"), "build", "build")]
    want += [("file", str(kit / f"lane{x}.log"), "totext", "lane") for x in "ABC"]
    out = []
    with _reg:
        for key in want:
            got = _sources.get(key)
            if got is None:
                got = LedgerEvents(Path(key[1])) if key[0] == "events" else LogFile(Path(key[1]), key[2], key[3])
                _sources[key] = got
            out.append(got)
    return out


def _is_nightly(one: object) -> bool:
    """The crawler's nightly log (update.log), into which the export's script tees what it says."""
    return isinstance(one, LogFile) and one.source == "crawl" and one.kind == "iso"


def held_from(logs: list) -> dict[str, dict[str, str]]:
    """{'kept_from': {source: the earliest line held}, 'cut_from': {source: from when every log of it is held}} — a
    source is in cut_from only when one of its logs was not read whole (LogFile.cut, LedgerEvents.cut): from that time
    on it is whole, before it some of its lines are not held."""
    kept: dict[str, str] = {}
    cut: dict[str, str] = {}
    for one in logs:
        src = one.source
        if one.kept_from and (src not in kept or one.kept_from < kept[src]):
            kept[src] = one.kept_from
        if one.cut and one.kept_from and one.kept_from > cut.get(src, ""):
            cut[src] = one.kept_from
    return {"kept_from": kept, "cut_from": cut}


def merged(where: Where) -> tuple[list[tuple], list[float], dict]:
    """Every log's rows refreshed and merged by time, their epochs (for bisect), and from when each source is held
    (held_from). A line of update.log that export.log also holds (the same second and words: export_sites.sh tees
    every line it says into both) is left out, so each export line is there once (CR-06); one export.log does not
    hold keeps its 導出 tag when it is in the export's words."""
    logs = logs_of(where)
    for one in logs:
        try:
            one.refresh()
        except Exception:  # one log that cannot be read never hides the others
            log.exception("纪录：读不到 %s", getattr(one, "path", "账本"))
    key = tuple((id(one), one.version) for one in logs)
    with _reg:
        got = _merged.get(key)
    if got is not None:
        return got
    said: Counter = Counter()
    for one in logs:
        if isinstance(one, LogFile) and one.source == "export":
            with one._lock:
                said.update((r[0][:19], r[4]) for r in one.rows)
    rows = []
    for one in logs:
        with one._lock:
            if said and _is_nightly(one):
                for r in one.rows:
                    k = (r[0][:19], r[4])
                    if said[k] > 0:
                        said[k] -= 1  # export.log's own line stands for it
                        continue
                    rows.append(r)
            else:
                rows += one.rows
    rows.sort(key=lambda r: r[1])
    got = (rows, [r[1] for r in rows], held_from(logs))
    with _reg:
        _merged.clear()
        _merged[key] = got
    return got


def _naive(value: str | None) -> datetime | None:
    at = pl.when_of(value)
    return at.astimezone(TAIPEI).replace(tzinfo=None) if at else None


FINAL = ("ok", "warn", "bad", "missed")  # a night in one of these has ended (nknu.ops.history.FINAL)


def night_bounds(where: Where, day: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """A night's window by its run day: its first start − PAD to the last end of its parts and runs + PAD (the design's
    night.py); None when it has not started (no lines: 「這一晚沒有留下紀錄」). A night still going (not FINAL), or one
    with a part that began and has no end (running, or stopped without one), reaches to now + PAD, never past the next
    night's start (its run day + 1 at 00:50): the running part's lines, its errors first, are the night's. When the
    night cannot be read, the run day from 00:50 to the next 00:50."""
    from nknu.ops import nightly  # here: nightly reads this package's pipeline as well

    try:  # a day that looks like one but is not (2026-02-30) is no night
        start_of = datetime.combine(date.fromisoformat(day), datetime.min.time()) + timedelta(minutes=50)
    except ValueError:
        return None
    cap = start_of + timedelta(days=1)  # the next night's chain starts at 01:00
    now_naive = (now or pl.now_tpe()).astimezone(TAIPEI).replace(tzinfo=None)
    try:
        n = nightly.one_night(nightly.Reading(where, now or pl.now_tpe()), date.fromisoformat(night_of_run(day)))
    except Exception:
        log.exception("纪录：读不到 %s 这一晚的时段", day)
        return start_of, cap
    cells = n.get("cells") or []
    starts = [x for c in cells if (x := _naive(c.get("since")))]
    if not starts:
        return None
    ends = [x for c in cells if (x := _naive(c.get("until")))]
    ends += [x for r in n.get("runs") or [] if (x := _naive(r.get("until")))]
    open_ = n.get("state") not in FINAL or any(c.get("since") and not c.get("until") for c in cells)
    if open_:
        ends.append(max(min(now_naive, cap), min(starts)))
    a = min(starts) - PAD
    b = (max(ends) if ends else min(starts) + timedelta(hours=12)) + PAD
    return a, b


def _bounds(where: Where, run_day: str | None, since: str | None, until: str | None,
            now: datetime | None) -> tuple[datetime, datetime] | None:
    now_naive = (now or pl.now_tpe()).astimezone(TAIPEI).replace(tzinfo=None)
    a, b = _naive(since), _naive(until)
    if a or b:
        b = b or now_naive
        return (a or b - SPAN), b
    if run_day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_day):
        return night_bounds(where, run_day, now)
    return now_naive - SPAN, now_naive


def window(where: Where, *, run_day: str | None = None, since: str | None = None, until: str | None = None,
           q: str = "", levels: set | None = None, sources: set | None = None, limit: int = 500,
           before: str | None = None, now: datetime | None = None) -> dict:
    """One page of the merged log ({'rows': [[time ISO, level, source, message]], newest first; 'hist': BINS columns
    [{'t', 'error', 'warn', 'info', 'debug'}] of the matching lines over the window's lines' span; 'counts': {'levels':
    {level: n}, 'sources': {source: n}} as the design's chips count them (a level's under the source and words chosen,
    a source's under the levels and words); 'more', 'next' (the cursor for the next page: pass it as before), 'total'
    (the window's lines), 'matched', 'first', 'last', 'from', 'to', 'kept_from' ({source: its earliest line held}),
    'cut_from' ({source: from when all its logs are held}, only the sources a log of which was not read whole),
    'short' ([[source, time]]: the sources chosen whose lines before that time, after the window's start, are not
    held: the page says 「MM-DD HH:MM 以前的爬取紀錄沒有留在檢視台」 instead of a quiet night), 'levels', 'sources'
    (key, word)}.

    The window: since/until (ISO, Taipei when no zone; one alone reaches SPAN from the other), else run_day (a night by
    the day its run starts: night_bounds), else the last SPAN. levels: default error, warn, info; sources: default all;
    q: words looked for in the message (case does not matter); before: a cursor 'time#n' from the page before."""
    levels = set(levels) & {k for k, _w in LEVELS} if levels else set(DEFAULT_LEVELS)
    sources = set(sources) & {k for k, _w in SOURCES} if sources else {k for k, _w in SOURCES}
    limit = max(1, min(int(limit or 500), LIMIT_MAX))
    ql = (q or "").strip().lower()
    out = {"rows": [], "hist": [], "counts": {"levels": {k: 0 for k, _w in LEVELS},
                                              "sources": {k: 0 for k, _w in SOURCES}},
           "more": False, "next": None, "total": 0, "matched": 0, "first": None, "last": None, "from": None,
           "to": None, "kept_from": {}, "cut_from": {}, "short": [], "levels": [list(x) for x in LEVELS],
           "sources": [list(x) for x in SOURCES]}
    rows, epochs, held = merged(where)
    out["kept_from"], out["cut_from"] = dict(held["kept_from"]), dict(held["cut_from"])
    bounds = _bounds(where, run_day, since, until, now)
    if bounds is None:
        return out
    a, b = bounds
    out["from"], out["to"] = a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds")
    # the sources chosen whose held lines begin after the window's start: before that time they are not held
    out["short"] = [[k, held["cut_from"][k]] for k, _w in SOURCES
                    if k in sources and held["cut_from"].get(k, "") > out["from"]]
    lo = bisect.bisect_left(epochs, a.replace(tzinfo=TAIPEI).timestamp())
    hi = bisect.bisect_right(epochs, b.replace(tzinfo=TAIPEI).timestamp())
    win = rows[lo:hi]
    out["total"] = len(win)
    if not win:
        return out
    out["first"], out["last"] = win[0][0], win[-1][0]
    said = [(r, not ql or ql in r[4].lower()) for r in win]
    lv, src = out["counts"]["levels"], out["counts"]["sources"]
    matched = []
    for r, hit in said:
        if not hit:
            continue
        if r[3] in sources:
            lv[r[2]] = lv.get(r[2], 0) + 1
        if r[2] in levels:
            src[r[3]] = src.get(r[3], 0) + 1
            if r[3] in sources:
                matched.append(r)
    out["matched"] = len(matched)
    t0, t1 = win[0][1], win[-1][1]
    w = (t1 - t0) / BINS or 0.001
    bins = [{"t": "", "error": 0, "warn": 0, "info": 0, "debug": 0} for _ in range(BINS)]
    for i, col in enumerate(bins):
        col["t"] = datetime.fromtimestamp(t0 + i * w, TAIPEI).replace(tzinfo=None).isoformat(timespec="seconds")
    for r in matched:
        bins[min(BINS - 1, int((r[1] - t0) / w))][r[2]] += 1
    out["hist"] = bins
    desc = matched[::-1]
    i = 0
    tb, k = "", 0
    if before:
        tb, _, kk = str(before).partition("#")
        k = int(kk) if kk.isascii() and kk.isdigit() else 0
        while i < len(desc) and desc[i][0] > tb:
            i += 1
        j = 0
        while i < len(desc) and desc[i][0] == tb and j < k:
            i, j = i + 1, j + 1
    page = desc[i:i + limit]
    out["rows"] = [[r[0], r[2], r[3], r[4]] for r in page]
    out["more"] = i + limit < len(desc)
    if out["more"] and page:
        lt = page[-1][0]
        c = sum(1 for r in page if r[0] == lt) + (k if before and lt == tb else 0)
        out["next"] = f"{lt}#{c}"
    return out


def reset() -> None:
    """Forget every log read (tests)."""
    with _reg:
        _sources.clear()
        _merged.clear()


def _set(value: str | None) -> set[str] | None:
    got = {x.strip() for x in str(value or "").split(",") if x.strip()}
    return got or None


def _param(params, *names: str) -> str:
    """The first of names the query gives (the two pages spell them differently: night.js lv/src/n, home.js
    levels/sources/limit)."""
    for name in names:
        value = params.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def api_window(where: Where, params) -> dict:
    """GET /api/logs (ROUTES_TO_WIRE), the one handler for both pages: window() from a request's query — date or
    run_day, since, until, q (at most 200 characters), lv or levels, src or sources, n or limit, before."""
    day = _param(params, "date", "run_day")
    try:
        limit = int(_param(params, "n", "limit") or 500)
    except ValueError:
        limit = 500
    return window(where, run_day=day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None,
                  since=_param(params, "since") or None, until=_param(params, "until") or None,
                  q=_param(params, "q")[:200], levels=_set(_param(params, "lv", "levels")),
                  sources=_set(_param(params, "src", "sources")), limit=limit, before=_param(params, "before") or None)
