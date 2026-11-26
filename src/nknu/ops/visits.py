"""網站訪問量 on 總覽 (11-23 design, research/design_2026-11-23/scripts/visits.py, tune2.py, help.py): the public
service's calls (MCP, web pages, the JSON API; the log cannot tell students from our own tests), in two views on one
card of one height.

  即時   the requests of every second of the last five minutes as a sliding line (each point a 30-second weighted
         average), with 每秒請求 (that average now), 每分鐘請求 (the last 60 s) and 每秒峰值 (the busiest second of
         them). The page asks GET /api/usage/live every few seconds (live): the per-second counts read from the end of
         the query log (pipeline.lines_since), never a query's words.
  每日   the calls of each of the last 30 days as a line that zooms and pans (ECharts), and for the day picked on
         it: 負荷率 P95, 峰值 QPM, 最大並發, P90 延遲, 錯誤率 (month()). Every day the query log still covers is read
         from it; the nights' record (history.Store.usage_days) holds the days it rotated away. The page asks GET
         /api/usage/card again when 每日 is shown and every minute while it is, so today and the date follow the clock.

The design copy replayed 15 minutes of 11/22 for 即時 (its 「示意」 label) and drew 11 月 by hand; both are live here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from nknu.ops import pipeline as pl
from nknu.ops.look import e
from nknu.ops.pipeline import TAIPEI

WINDOW = 300  # seconds the live line shows
SMOOTH = 30  # seconds each point averages (weights 30..1, the newest heaviest)
POLL_MS = 3000  # how often the page asks again
BACK = 24 * 3600  # how far back 即時 can be dragged, in seconds (the log rotates at 20 MB: span() says where it began)
DAYS = 30  # the days 每日 draws, ending today
HELP = ("對外服務（MCP、網頁、接口）收到的查詢，分不出是學生還是我們在測試。即時：最近 5 分鐘每秒的請求數（30 秒平滑）；"
        "滾輪或 +、− 縮放，拖曳或 ←、→ 往回看（最多 24 小時），按「回到現在」回到即時。"
        "每日：每天的查詢次數，可以縮放、左右拖；點一天看那天的負荷率（P95）、峰值 QPM、最大並發、P90 延遲、錯誤率。")


def slots() -> int:
    """The searches the service runs at once (nknu.retrieval.public.MAX_SEARCHES, read by pipeline.search_slots)."""
    read = getattr(pl, "search_slots", None)
    try:
        return int(read()) if callable(read) else 4
    except (TypeError, ValueError, OSError):
        return 4


def live(public_log: Path | None, now: datetime | None = None) -> dict:
    """The requests of each second up to the last whole one: {'now', 'end' (that second, epoch), 'per_sec' (oldest
    first, WINDOW + SMOOTH seconds), 'read' (the log could be read: False draws no line, never a quiet one), 'last'
    (the latest call's time as the log has it, also when it is older than the window: a quiet window says when anyone
    last called; None with no call or no log)}."""
    now = now or pl.now_tpe()
    end = int(now.timestamp()) - 1
    span = WINDOW + SMOOTH
    counts = [0] * span
    path = Path(public_log) if public_log else None
    read = bool(path and path.exists())
    last = None
    if read:
        lines, _whole = pl.lines_since(path, now - timedelta(seconds=span + 1))
        last = _last_call(path, lines)
        first = end - span + 1
        for x in lines:
            at = x.get("_at")
            if not isinstance(at, datetime):
                continue
            second = int(at.timestamp())
            if first <= second <= end:
                counts[second - first] += 1
    return {"now": now.isoformat(timespec="seconds"), "end": end, "per_sec": counts, "read": read,
            "poll_ms": POLL_MS, "last": last}


def _last_call(path: Path, lines: list[dict]) -> str | None:
    """The latest call's 't': the window's last line, else the log's last line that has one."""
    if lines:
        return str(lines[-1].get("t") or "") or None
    for raw in reversed(pl.tail(path)):
        try:
            t = json.loads(raw).get("t")
        except (ValueError, AttributeError):
            continue
        if t:
            return str(t)
    return None


def span(public_log: Path | None, since: int, until: int, now: datetime | None = None) -> dict:
    """The calls of each second from `since` to `until` (epoch seconds, kept inside the last BACK seconds up to the
    last whole one), for 即時 dragged back or zoomed out (owner 11-24: 「可以滑动回看之前的走线以及放大缩小时间范围吗」):
    {'from', 'to', 'calls' ([second, n] for each second with calls, oldest first: a quiet stretch is long and sent as
    nothing), 'read' (the log could be read), 'whole' (the log reaches back past 'from'), 'began' (not whole: the
    log's first second, or 'to' + 1 when it holds no line at all; nothing before it is known, never a quiet stretch)}.
    Never a query's words."""
    now = now or pl.now_tpe()
    last = int(now.timestamp()) - 1
    lo, hi = max(int(since), last - BACK), min(int(until), last)
    path = Path(public_log) if public_log else None
    out = {"from": lo, "to": hi, "calls": [], "read": bool(path and path.exists()), "whole": False, "began": None}
    if not out["read"] or hi < lo:
        return out
    lines, whole = pl.lines_since(path, datetime.fromtimestamp(lo, TAIPEI))
    seconds = [int(x["_at"].timestamp()) for x in lines if isinstance(x.get("_at"), datetime)]
    counts: dict[int, int] = {}
    for s in seconds:
        if lo <= s <= hi:
            counts[s] = counts.get(s, 0) + 1
    out.update(calls=[[s, n] for s, n in sorted(counts.items())], whole=whole,
               began=None if whole else (seconds[0] if seconds else hi + 1))
    return out


def live_api(where, params=None, *, flow=None) -> dict:
    """GET /api/usage/live: live(), and 'serve' (serve_of: whether the public service answered its last check). With
    from= and to= (epoch seconds): span() of that stretch instead, what 即時 asks when it is dragged back or zoomed
    out past what it holds."""
    log = getattr(where, "public_log", None)
    if params is not None and params.get("from") is not None:
        try:
            since, until = int(params.get("from")), int(params.get("to") or 2**40)
        except (TypeError, ValueError):
            return {"error": "bad_args", "read": False, "calls": []}
        return span(log, since, until)
    return {**live(log), "serve": serve_of(flow)}


def serve_of(flow) -> dict | None:
    """The public service ('public' among 查詢服務's checked services) as the last reading of the steps found it:
    {'up', 'at' (when it was checked), 'words', 'name'}; None before any reading or with no such service. The last
    reading only, never a new one started (Pipeline.last)."""
    read = getattr(flow, "last", None)
    try:
        snap = read() if callable(read) else None
    except Exception:  # noqa: BLE001  a reading being replaced: nothing said this time
        snap = None
    step = next((x for x in (snap or {}).get("steps") or [] if isinstance(x, dict) and x.get("key") == "serve"), None)
    svc = next((x for x in ((step or {}).get("nums") or {}).get("services") or []
                if isinstance(x, dict) and x.get("kind") == "public"), None)
    if svc is None:
        return None
    return {"up": bool(svc.get("up")), "at": str(step.get("at") or snap.get("at") or ""),
            "words": str(svc.get("words") or ""), "name": str(svc.get("name") or "")}


def _day_of(row: dict) -> str:
    return str(row.get("day") or row.get("date") or "")


_read_cache: dict[tuple, tuple] = {}


def _log_days(public_log: Path, first: date, now: datetime) -> tuple[dict[str, dict], date | None, bool]:
    """The log read once from first's 00:00: ({day: usage_of's numbers}, the first day it covers (None: it holds no
    line), whether it reaches back past first). Kept while neither file changes (the card is asked every minute)."""
    path = Path(public_log)
    stamp = []
    for f in (path, path.with_name(path.name + ".1")):
        try:
            st = f.stat()
            stamp.append((st.st_size, st.st_mtime_ns))
        except OSError:
            stamp.append(None)
    key = (str(path), first.isoformat(), tuple(stamp))
    got = _read_cache.get(key)
    if got is None:
        start = datetime.combine(first, datetime.min.time(), TAIPEI)
        lines, whole = pl.lines_since(path, start)
        began = first if whole else (lines[0]["_at"].astimezone(TAIPEI).date() if lines else None)
        got = (pl.usage_of(lines), began, whole)
        _read_cache.clear()
        _read_cache[key] = got
    return got


def month(store=None, public_log: Path | None = None, *, now: datetime | None = None,
          recent: list[list] | None = None) -> dict:
    """The last DAYS days' calls a day and each day's load, ending today, as the card's data (vs-data and GET
    /api/usage/card): {'labels' (M/D), 'dates', 'calls' (None: no record), 'today', 'days' ({day: numbers, with
    'complete': False on a day the log began after the day's start}), 'pick', 'slots'}.

    Each day from the best source there is: the query log for every day it still covers (read once, so yesterday is
    whole the minute after midnight and the record's older reckoning of a day is not kept over the log's); the
    nights' record (store: history.Store.usage_days) for days the log no longer holds (it rotates at 20 MB); the
    24-hour reading's week (recent: pipeline.usage_days' [day, calls, searches]) for calls the two have not. A day
    the log covers with no call is a quiet day (0); a day nothing covers has no point (review 11-23: this calendar
    month before, so the 1st held today alone, and days to come were drawn empty)."""
    now = now or pl.now_tpe()
    today = (now.astimezone(TAIPEI) if now.tzinfo else now).date()
    first = today - timedelta(days=DAYS - 1)
    dates = [first + timedelta(days=i) for i in range(DAYS)]
    inside = {d.isoformat() for d in dates}
    days: dict[str, dict] = {}
    for row in recent or []:
        if len(row) >= 3 and row[1] is not None and row[0] in inside:
            days[row[0]] = {"calls": row[1], "search": row[2]}
    kept = getattr(store, "usage_days", None) if store is not None else None
    if callable(kept) and getattr(store, "enabled", True):
        try:
            rows = kept(first.isoformat(), (today - timedelta(days=1)).isoformat()) or []
        except (sqlite3.Error, OSError):  # a store from before the table: the log and the week still draw
            rows = []
        for row in rows:
            d = _day_of(row)
            if d in inside:
                days[d] = {**days.get(d, {}), **{k: v for k, v in row.items() if k not in ("day", "date")}}
    if public_log and Path(public_log).exists():
        try:
            fresh, began, whole = _log_days(Path(public_log), first, now)
        except (OSError, ValueError, TypeError, KeyError):  # an odd log: the record and the week still draw
            fresh, began, whole = {}, None, False
        for d in dates:
            key = d.isoformat()
            if d == today or (began is not None and d >= began):
                part = not whole and began is not None and d == began and d != today
                days[key] = {**(fresh.get(key) or pl.usage_empty()), "complete": not part}
    calls = [days.get(d.isoformat(), {}).get("calls") for d in dates]
    # the day the five numbers open on: the last one that is over (owner 11-23: a morning's few calls read as the
    # day's P90 and load), with its load measured if any is; today only when no day before it has calls
    before = {d: v for d, v in days.items() if d < today.isoformat()}
    pick = (max((d for d, v in before.items() if v.get("qpm_peak")), default="")
            or max((d for d, v in before.items() if v.get("calls")), default="")
            or max((d for d, v in days.items() if v.get("qpm_peak")), default="")
            or max((d for d, v in days.items() if v.get("calls") is not None), default=""))
    return {"labels": [f"{d.month}/{d.day}" for d in dates], "dates": [d.isoformat() for d in dates], "calls": calls,
            "today": today.isoformat(), "days": days, "pick": pick, "slots": slots()}


def card_api(store, where, params=None) -> dict:
    """GET /api/usage/card: month() now, for the card's 每日 view to redraw while the page stays open."""
    return month(store, getattr(where, "public_log", None))


def data_json(data: dict) -> str:
    """The card's data as inline JSON (</ never closes the script it sits in)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/")


def card_html(data: dict) -> str:
    """The card (section.vs, 即時 first); its drawing is the layer's (theme/design/home.js). The live numbers say
    「—」 until the first answer of /api/usage/live: never a 0 nobody measured."""
    n = data.get("slots") or slots()
    helpb = (f"<button type='button' class='hp' data-hp='vs' aria-label='這張卡片在說什麼' data-tip-h='網站訪問量' "
             f"data-tip='{e(HELP)}' data-tip-full>?</button>")
    load_tip = f"有查詢的每一分鐘裡，{n} 個檢索名額平均被占用幾成，取 P95。超過 100% 表示有請求在排隊。"
    return (
        "<section class='pn-kpi vs' id='usage' aria-label='網站訪問量' data-v='live'>"
        "<div class='vs-head'><div class='pn-khead'>網站訪問量</div><div class='vs-tools'>"
        "<div class='cal-switch vs-switch' role='group' aria-label='看即時或每天'><button type='button' data-v='live' "
        "aria-pressed='true'>即時</button><button type='button' data-v='day' aria-pressed='false'>每日</button></div>"
        f"{helpb}</div></div>"
        "<div class='vs-body vs-live'>"
        "<div class='vs-ecg'><canvas id='vs-ecg' tabindex='0' aria-label='最近 5 分鐘每秒請求數；滾輪或 +、− 縮放，"
        "拖曳或 ←、→ 往回看'></canvas>"
        "<button type='button' class='vs-now' id='vs-now' hidden>回到現在</button>"
        "<div class='vs-ecg-none' id='vs-ecg-none' hidden>讀不到查詢紀錄</div>"
        "<div class='vs-ecg-quiet' id='vs-ecg-quiet' hidden></div>"
        "<div class='vs-ecg-tip' id='vs-ecg-tip' role='tooltip' hidden></div></div>"
        "<div class='vs-load'>"
        "<div class='vs-main' tabindex='0' data-tip-h='每秒請求數' data-tip='每秒請求數（最近 30 秒加權平均）'>"
        "<span class='vs-sl'>每秒請求</span><span class='vs-pct'><b id='vs-rps'>—</b></span></div>"
        "<div class='vs-grid'>"
        "<div class='vs-stat' tabindex='0' data-tip-h='每分鐘請求' data-tip='最近 60 秒的請求數'><span class='vs-sl'>每分鐘請求"
        "</span><span class='vs-sv'><b id='vs-60'>—</b></span></div>"
        "<div class='vs-stat' tabindex='0' data-tip-h='每秒峰值' data-tip='最近 60 秒裡最忙那一秒的請求數'><span class='vs-sl'>"
        "每秒峰值</span><span class='vs-sv'><b id='vs-peak'>—</b></span></div>"
        "</div></div></div>"
        "<div class='vs-body vs-days'>"
        f"<div class='vs-chart' id='vs-chart' role='img' aria-label='最近 {DAYS} 天每天的查詢次數，可以放大縮小、左右拖'></div>"
        "<div class='vs-load'><div class='vs-day' id='vs-day'></div>"
        f"<div class='vs-main' tabindex='0' data-tip-h='負荷率（P95）' data-tip='{e(load_tip)}'>"
        "<span class='vs-sl'>負荷率 P95</span><span class='vs-pct'><b id='vs-load'>—</b>%</span>"
        "<span class='vs-meter'><i id='vs-meter'></i></span></div>"
        "<div class='vs-grid'>"
        "<div class='vs-stat' tabindex='0' data-tip-h='峰值 QPM' data-tip='最忙那一分鐘的請求數'><span class='vs-sl'>峰值 QPM</span>"
        "<span class='vs-sv'><b id='vs-qpm'>—</b></span><span class='vs-sn' id='vs-qpm-at'></span></div>"
        f"<div class='vs-stat' tabindex='0' data-tip-h='最大並發' data-tip='同一時間在跑或排隊的檢索最多幾個；同時跑的上限是 "
        f"{e(n)} 個（讀全文不占名額）'><span class='vs-sl'>最大並發</span><span class='vs-sv'><b id='vs-conc'>—</b></span></div>"
        "<div class='vs-stat' tabindex='0' id='vs-lat-card' data-tip-h='P90 延遲' data-tip=''><span class='vs-sl'>P90 延遲"
        "</span><span class='vs-sv'><b id='vs-p90'>—</b>s</span></div>"
        "<div class='vs-stat' tabindex='0' data-tip-h='錯誤率' data-tip='沒答成的請求比例：太忙、服務出錯（呼叫參數錯不算）'>"
        "<span class='vs-sl'>錯誤率</span><span class='vs-sv'><b id='vs-err'>—</b>%</span></div>"
        "</div></div></div>"
        f"<script type='application/json' id='vs-data'>{data_json(data)}</script>"
        "</section>"
    )
