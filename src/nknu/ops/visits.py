"""網站訪問量 on 總覽 (11-23 design, research/design_2026-11-23/scripts/visits.py, tune2.py, help.py): the public
service's calls (MCP, web pages, the JSON API; the log cannot tell students from our own tests), in two views on one
card of one height.

  即時   the requests of every second of the last five minutes as a sliding line (each point a 30-second weighted
         average), with 每秒請求 (that average now), 每分鐘請求 (the last 60 s) and 每秒峰值 (the busiest second of
         them). The page asks GET /api/usage/live every few seconds (live): the per-second counts read from the end of
         the query log (pipeline.lines_since), never a query's words.
  每日   the calls of each day of this month as a line that zooms and pans (ECharts), and for the day picked on it:
         負荷率 P95, 峰值 QPM, 最大並發, P90 延遲, 錯誤率. Finished days come from the nights' record
         (history.Store.usage_days, written once a day is over: the log rotates at 20 MB); today is read now
         (pipeline.usage_day); the week the 24-hour reading counts fills the calls of days the record has not.

The design copy replayed 15 minutes of 11/22 for 即時 (its 「示意」 label) and drew 11 月 by hand; both are live here.
"""

from __future__ import annotations

import calendar as stdcal
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from nknu.ops import pipeline as pl
from nknu.ops.look import e
from nknu.ops.pipeline import TAIPEI

WINDOW = 300  # seconds the live line shows
SMOOTH = 30  # seconds each point averages (weights 30..1, the newest heaviest)
POLL_MS = 3000  # how often the page asks again
HELP = ("對外服務（MCP、網頁、接口）收到的查詢，分不出是學生還是我們在測試。即時：最近 5 分鐘每秒的請求數（30 秒平滑）。"
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
    first, WINDOW + SMOOTH seconds), 'read' (the log could be read: False draws no line, never a quiet one)}."""
    now = now or pl.now_tpe()
    end = int(now.timestamp()) - 1
    span = WINDOW + SMOOTH
    counts = [0] * span
    path = Path(public_log) if public_log else None
    read = bool(path and path.exists())
    if read:
        lines, _whole = pl.lines_since(path, now - timedelta(seconds=span + 1))
        first = end - span + 1
        for x in lines:
            at = x.get("_at")
            if not isinstance(at, datetime):
                continue
            second = int(at.timestamp())
            if first <= second <= end:
                counts[second - first] += 1
    return {"now": now.isoformat(timespec="seconds"), "end": end, "per_sec": counts, "read": read,
            "poll_ms": POLL_MS}


def live_api(where, params=None) -> dict:
    """GET /api/usage/live (ROUTES_TO_WIRE in nknu.ops.panorama)."""
    return live(getattr(where, "public_log", None))


def _day_of(row: dict) -> str:
    return str(row.get("day") or row.get("date") or "")


def month(store=None, public_log: Path | None = None, *, now: datetime | None = None,
          recent: list[list] | None = None) -> dict:
    """This month's calls a day and each day's load, as the card's data (vs-data): {'labels' (M/D), 'dates',
    'calls' (None: no record, or a day still to come), 'today', 'days' ({day: numbers}), 'pick' (the latest day with
    a load measured), 'slots', 'month'}. store: history.Store (its usage_days); public_log: today read now
    (pipeline.usage_day); recent: pipeline.usage_days' [day, calls, searches] of the last week."""
    now = now or pl.now_tpe()
    today = (now.astimezone(TAIPEI) if now.tzinfo else now).date()
    first = today.replace(day=1)
    dates = [first + timedelta(days=i) for i in range(stdcal.monthrange(today.year, today.month)[1])]
    inside = {d.isoformat() for d in dates}
    days: dict[str, dict] = {}
    for row in recent or []:
        if len(row) >= 3 and row[1] is not None and row[0] in inside:
            days[row[0]] = {"calls": row[1], "search": row[2]}
    kept = getattr(store, "usage_days", None) if store is not None else None
    if callable(kept) and getattr(store, "enabled", True) and today > first:
        try:
            rows = kept(first.isoformat(), (today - timedelta(days=1)).isoformat()) or []
        except (sqlite3.Error, OSError):  # a store from before the table: the week's calls still draw
            rows = []
        for row in rows:
            d = _day_of(row)
            if d in inside:
                days[d] = {**days.get(d, {}), **{k: v for k, v in row.items() if k not in ("day", "date")}}
    measure = getattr(pl, "usage_day", None)
    if public_log and callable(measure) and Path(public_log).exists():
        try:
            got = measure(Path(public_log), today.isoformat())
        except (OSError, ValueError, TypeError, KeyError):  # an odd log: the day stays a gap, the page draws
            got = None
        if got:
            days[today.isoformat()] = {**days.get(today.isoformat(), {}), **got}
    calls = [days.get(d.isoformat(), {}).get("calls") if d <= today else None for d in dates]
    pick = max((d for d, v in days.items() if v.get("qpm_peak")), default="") or max(
        (d for d, v in days.items() if v.get("calls") is not None), default="")
    return {"labels": [f"{d.month}/{d.day}" for d in dates], "dates": [d.isoformat() for d in dates], "calls": calls,
            "today": today.isoformat(), "days": days, "pick": pick, "slots": slots(), "month": today.month}


def data_json(data: dict) -> str:
    """The card's data as inline JSON (</ never closes the script it sits in)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/")


def card_html(data: dict) -> str:
    """The card (section.vs, 即時 first); its drawing is the layer's (theme/design/home.js). The live numbers say
    「—」 until the first answer of /api/usage/live: never a 0 nobody measured."""
    n = data.get("slots") or slots()
    m = data.get("month") or ""
    helpb = (f"<button type='button' class='hp' data-hp='vs' aria-label='這張卡片在說什麼' data-tip-h='網站訪問量' "
             f"data-tip='{e(HELP)}' data-tip-full>?</button>")
    load_tip = f"有查詢的每一分鐘裡，{n} 個搜尋名額平均被占用幾成，取 P95。超過 100% 表示有請求在排隊。"
    return (
        "<section class='pn-kpi vs' id='usage' aria-label='網站訪問量' data-v='live'>"
        "<div class='vs-head'><div class='pn-khead'>網站訪問量</div><div class='vs-tools'>"
        "<div class='cal-switch vs-switch' role='group' aria-label='看即時或每天'><button type='button' data-v='live' "
        "aria-pressed='true'>即時</button><button type='button' data-v='day' aria-pressed='false'>每日</button></div>"
        f"{helpb}</div></div>"
        "<div class='vs-body vs-live'>"
        "<div class='vs-ecg'><canvas id='vs-ecg' aria-label='最近 5 分鐘每秒請求數'></canvas>"
        "<div class='vs-ecg-none' id='vs-ecg-none' hidden>讀不到查詢紀錄</div></div>"
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
        f"<div class='vs-chart' id='vs-chart' role='img' aria-label='{e(m)} 月每天的查詢次數，可以放大縮小、左右拖'></div>"
        "<div class='vs-load'><div class='vs-day' id='vs-day'></div>"
        f"<div class='vs-main' tabindex='0' data-tip-h='負荷率（P95）' data-tip='{e(load_tip)}'>"
        "<span class='vs-sl'>負荷率 P95</span><span class='vs-pct'><b id='vs-load'>—</b>%</span>"
        "<span class='vs-meter'><i id='vs-meter'></i></span></div>"
        "<div class='vs-grid'>"
        "<div class='vs-stat' tabindex='0' data-tip-h='峰值 QPM' data-tip='最忙那一分鐘的請求數'><span class='vs-sl'>峰值 QPM</span>"
        "<span class='vs-sv'><b id='vs-qpm'>—</b></span><span class='vs-sn' id='vs-qpm-at'></span></div>"
        f"<div class='vs-stat' tabindex='0' data-tip-h='最大並發' data-tip='同一時間在處理或排隊的請求最多幾個；同時處理的上限是 "
        f"{e(n)} 個'><span class='vs-sl'>最大並發</span><span class='vs-sv'><b id='vs-conc'>—</b></span></div>"
        "<div class='vs-stat' tabindex='0' id='vs-lat-card' data-tip-h='P90 延遲' data-tip=''><span class='vs-sl'>P90 延遲"
        "</span><span class='vs-sv'><b id='vs-p90'>—</b>s</span></div>"
        "<div class='vs-stat' tabindex='0' data-tip-h='錯誤率' data-tip='沒答成的請求比例：太忙、服務出錯（呼叫參數錯不算）'>"
        "<span class='vs-sl'>錯誤率</span><span class='vs-sv'><b id='vs-err'>—</b>%</span></div>"
        "</div></div></div>"
        f"<script type='application/json' id='vs-data'>{data_json(data)}</script>"
        "</section>"
    )
