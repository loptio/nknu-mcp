"""總覽's calendar: one cell a night, named by the day it runs (owner 11-23: the 01:00 run on 11-23 is 11/23, no 「夜」).

The owner settled it on the design copy step by step (research/design_2026-11-23/index.html, built by the scripts
draw_calendar.py, gauge.py, caltune.py, datefix.py and help.py beside it):

  a ring        five arcs clockwise from 12 o'clock: 爬取, 導出, 轉文字, 建庫, 換庫 (he refused merging the first two: an
                export failure would hide). Green done, the whole arc yellow stopped (暫停), red failed (出錯; the cell
                tinted red too), a dashed moving green arc running, empty not run. A step done with a warning stays
                green and puts a yellow dot at the cell's top right (留意).
  its centre    the time the new library went in; the first two characters of the running step; else 「—」.
  under it      +new −left of a night that swapped (violet, + deeper); a night that did not swap shows nothing.
  the dial      week cells only: a 270° arc around the ring, 01:00 lower left to 10:00 lower right, each step at its
                true time in the text colour (no state colours), a hair cut where one step meets the next.
  the card      only for a night that is not all green: the worst state on top, one line a step in the source's own
                words, nothing added (he cut 「學生搜到的還是前一晚的庫」).
  the head      the legend 完成 · 暫停 · 留意 · 出錯, two stacked numbers (when the library in use was crawled, and
                a countdown to the next 01:00), 週 / 月, a ?.

The nights come from two places: nightly.tonight's last seven readings (the live night among them) and the nights'
record (history.Store.nights: every finished night with its steps, swapped, new and left). A night's key there is the
evening before its run (nightly.night_of); here every cell is keyed by its run day, with the panels' one mapping
(nightly.run_day, and sources.night_of_run back). The week is the seven run days ending today, never tonight()'s
list, which moves on at noon.

One addition to the design's tone (an owner check): a step that stopped without ever starting (沒跑: the crawler
silent at 01:00) is drawn stopped (red when its source says 出錯), where the copy drew no arc at all for a step with no
start, which made such a night a grey 「還沒開始」 ring, or no night.
"""

from __future__ import annotations

import calendar as stdcal
import math
from datetime import date, datetime, timedelta

from nknu.ops.look import e
from nknu.ops.nightly import CELLS, run_day
from nknu.ops.pipeline import TAIPEI
from nknu.ops.sources import night_of_run

SITES = "/sites"  # 內容; ?date=<run day> opens a night there (the night view merged into 內容, owner 11-23)
STAGES = CELLS  # (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("swap", "換庫"))
SAID = {"stop": "暫停", "bad": "出錯", "warn": "留意", "run": "跑中"}  # a step's word when its source said nothing
WORST = {"bad": 0, "stop": 1, "run": 2, "warn": 3}
HEAD = {0: "出錯", 1: "暫停", 2: "跑中", 3: "留意"}
WEEKDAY = "一二三四五六日"
HEADS = "日一二三四五六"  # the month grid starts on Sunday
WEEK = 7
LEGEND = ("<div class='cal-key cal-key-top'><span><i class='k-done'></i>完成</span><span><i class='k-stop'></i>暫停</span>"
          "<span><i class='k-note'></i>留意</span><span><i class='k-bad'></i>出錯</span></div>")
HELP = ("一格一晚，從 01:00 起。圓環五段順時針：爬取、導出、轉文字、建庫、換庫。綠：完成；整段黃：暫停；紅：出錯；"
        "虛線在轉：正在跑。中間是換上新庫的時間，「—」是沒換庫。右上黃點：做完了但有警告。"
        "+／− 是這一晚新進和離開庫的文件數。週曆的外圈是 01:00–10:00，各步驟在跑的時段。點一晚看那一晚的細節。")
WAIT_RING = ("<span class='cal-ic'><svg class='ic-wait' viewBox='0 0 56 56' width='56' height='56' role='img' "
             "aria-label='還沒開始'><circle cx='28' cy='28' r='20'/></svg></span>")
NO_RECORD = "<span class='cal-dash' aria-label='沒紀錄'>—</span>"

# the ring (draw_calendar.ring): radius, centre, stroke, the gap between two arcs (radians)
R, C, SW, GAP = 20, 28, 6, 0.16
# the dial (gauge.py): radius and stroke outside the ring, 270° from lower left, clockwise
GR, GSW, A0, SPAN = 33, 5, -135, 270
FROM, HOURS = 60, 540  # 01:00, nine hours to 10:00, in minutes
SLIVER = 2.2  # a step at least this share of the dial (the tip has its times)


def tone(c: dict | None) -> str:
    """A step's tone on the ring: none (not run), run, bad, stop, warn (done with a warning), done. A step stopped
    with no start is still stopped (the crawler that never began, 沒跑: red, never 還沒開始)."""
    if not c or c.get("state") in (None, "", "wait"):
        return "none"
    if not c.get("since") and c.get("state") != "stop":
        return "none"
    if c.get("state") == "run":
        return "run"
    if c.get("sev") == "bad":
        return "bad"
    if c.get("state") == "stop":
        return "stop"
    if c.get("sev") == "warn":
        return "warn"
    return "done"


def hm(v: str | None) -> str:
    return (v or "")[11:16]


def from_store(step: dict) -> dict:
    """A step of the nights' record (history.STEP_COLS) in a reading's shape (nightly._cell's keys used here)."""
    return {"key": step.get("step") or "", "name": step.get("name") or "", "state": step.get("state") or "",
            "sev": step.get("sev") or "", "say": step.get("say") or "", "since": step.get("start") or "",
            "until": step.get("end") or ""}


# ------------------------------------------------------------------------------------------------ the nights


def nights(tonight: dict, records: list[dict | None], changes: dict | None = None) -> dict[str, dict]:
    """{run day: {'night', 'cells': {key: cell}, 'seen' (a reading or a record of it exists), 'swapped', 'new',
    'left'}}. A night tonight() read is drawn from that reading (the freshest), else from the record's steps; swapped,
    new and left are the record's, and for the night the record has not written yet, the reading's swap and the last
    swap's comparison (changes) when it is that night's."""
    out: dict[str, dict] = {}
    for rec in records or []:
        if not rec or not rec.get("night"):
            continue
        cells = {c["key"]: c for c in (from_store(s) for s in rec.get("steps") or []) if c["key"]}
        out[run_day(rec["night"])] = {"night": rec["night"], "cells": cells, "seen": True,
                                          "swapped": None if rec.get("swapped") is None else bool(rec["swapped"]),
                                          "new": rec.get("new"), "left": rec.get("left")}
    ready = (changes or {}).get("state") == "ready"
    for n in tonight.get("nights") or []:
        if not n.get("night"):
            continue
        day = run_day(n["night"])
        got = out.setdefault(day, {"night": n["night"], "swapped": None, "new": None, "left": None})
        cells = {c["key"]: c for c in n.get("cells") or [] if c.get("key")}
        # a night before the chain went live (還沒上線) or unseen (看不到) is no night: 「—」, never 還沒開始; the
        # record's steps, when it has them, stay
        seen = any(c.get("word") not in ("還沒上線", "看不到") for c in cells.values())
        if seen or not got.get("cells"):
            got["cells"] = cells
        got["seen"] = bool(got.get("seen") or seen)
        swap = got["cells"].get("swap") or {}
        if got["swapped"] is None and tone(swap) in ("done", "warn"):
            got["swapped"] = swap.get("say") != "庫沒換"  # done with nothing new: the library stayed
        if got["new"] is None and ready and changes.get("night") == n["night"]:
            totals = changes.get("totals") or {}
            got["new"], got["left"] = totals.get("new"), totals.get("left")
    return out


def card(cells: dict) -> tuple[str, str]:
    """The floating card of a night that is not all green: (the worst state, one line a step, the worst first, in the
    source's own words); ('', '') for a night that ran clean."""
    found = sorted(((WORST[tone(cells.get(k))], i, w, cells.get(k) or {}) for i, (k, w) in enumerate(STAGES)
                    if tone(cells.get(k)) in WORST), key=lambda x: (x[0], x[1]))
    if not found:
        return "", ""
    lines = []
    for _rank, _i, w, c in found:
        t = tone(c)
        lines.append(f"{w}：{hm(c.get('since'))} 開始，還在跑" if t == "run" else f"{w}：{c.get('say') or SAID[t]}")
    return HEAD[found[0][0]], "\n".join(lines)


# ------------------------------------------------------------------------------------------------ drawing


def ring(cells: dict, centre: str, *, dial: bool = False) -> str:
    """The ring of five arcs with its centre; dial: the week's clock around it (gauge)."""
    out = [f"<circle cx='{C}' cy='{C}' r='{R}' class='ring-bg'/>"]
    for i, (k, _w) in enumerate(STAGES):
        a0 = -math.pi / 2 + i * 2 * math.pi / 5 + GAP / 2
        a1 = -math.pi / 2 + (i + 1) * 2 * math.pi / 5 - GAP / 2
        x0, y0 = C + R * math.cos(a0), C + R * math.sin(a0)
        x1, y1 = C + R * math.cos(a1), C + R * math.sin(a1)
        t = tone(cells.get(k))
        out.append(f"<path d='M{x0:.2f} {y0:.2f} A{R} {R} 0 0 1 {x1:.2f} {y1:.2f}' class='a-{'done' if t == 'warn' else t}' "
                   f"stroke-width='{SW}' fill='none' stroke-linecap='round'/>")
    out.append(f"<text x='{C}' y='{C + 3.5}' text-anchor='middle'>{e(centre)}</text>")
    if dial:
        out.append(gauge(cells))
        return ("<svg class='ic-ring ic-gauge' viewBox='-12 -12 80 77' width='80' height='77' aria-hidden='true'>"
                f"{''.join(out)}</svg>")
    return f"<svg class='ic-ring' viewBox='0 0 56 56' width='56' height='56' aria-hidden='true'>{''.join(out)}</svg>"


def _pt(t: float, r: float = GR) -> tuple[float, float]:
    a = math.radians(A0 + SPAN * t / 100)
    return C + r * math.sin(a), C - r * math.cos(a)


def _arc(t0: float, t1: float, cls: str, extra: str = "") -> str:
    t1 = max(t1, t0 + 0.6)
    (x0, y0), (x1, y1) = _pt(t0), _pt(t1)
    large = 1 if SPAN * (t1 - t0) / 100 > 180 else 0
    return f"<path d='M{x0:.2f} {y0:.2f} A{GR} {GR} 0 {large} 1 {x1:.2f} {y1:.2f}' class='{cls}'{extra}/>"


def tpos(v: str) -> float:
    """A time of the night on the dial, 0 (01:00) to 100 (10:00)."""
    try:
        minutes = int(v[11:13]) * 60 + int(v[14:16])
    except (ValueError, IndexError):
        return 0.0
    return max(0.0, min(100.0, (minutes - FROM) / HOURS * 100))


def gauge(cells: dict) -> str:
    """The week's dial (gauge.py): the steps one after the other at their times, in the text colour."""
    out = [_arc(0, 100, "ga-bg")]
    for t in (0, 100 / 3, 200 / 3, 100):  # 01, 04, 07, 10
        (x0, y0), (x1, y1) = _pt(t, GR - 4.2), _pt(t, GR - 2.2)
        out.append(f"<line x1='{x0:.2f}' y1='{y0:.2f}' x2='{x1:.2f}' y2='{y1:.2f}' class='ga-tick'/>")
    end: float | None = None
    cuts = []
    for i, (k, w) in enumerate(STAGES):
        c = cells.get(k) or {}
        if tone(c) == "none" or not c.get("since"):
            continue
        t0 = tpos(c["since"])
        t1 = tpos(c["until"]) if c.get("until") else t0
        t0 = max(t0, end or 0)
        t1 = max(t1, t0 + SLIVER)
        if end is not None and t0 - end < 0.05:
            cuts.append(t0)
        tip = f"{hm(c['since'])}–{hm(c.get('until'))}"
        out.append(_arc(t0, t1, f"ga-s{i}", f" data-tip-h='{e(w)}' data-tip='{e(tip)}'"))
        end = max(end or 0, t1)
    for t in cuts:
        (x0, y0), (x1, y1) = _pt(t, GR - 3.2), _pt(t, GR + 3.2)
        out.append(f"<line x1='{x0:.2f}' y1='{y0:.2f}' x2='{x1:.2f}' y2='{y1:.2f}' class='ga-cut'/>")
    (lx, ly), (rx, ry) = _pt(0, GR + 1), _pt(100, GR + 1)
    out.append(f"<text x='{lx + 3:.1f}' y='{ly + 2:.1f}' class='ga-lab' text-anchor='middle'>01</text>"
               f"<text x='{rx - 3:.1f}' y='{ry + 2:.1f}' class='ga-lab' text-anchor='middle'>10</text>")
    return "<g class='ga'>" + "".join(out) + "</g>"


def ran(info: dict | None) -> bool:
    return bool(info) and any(tone(c) != "none" for c in (info.get("cells") or {}).values())


def night_cell(day: str, info: dict, cls: list[str], label: str, *, week: bool) -> str:
    """A night that ran (draw_calendar.night_cell): a link to its night on 內容."""
    cells = info.get("cells") or {}
    swap = cells.get("swap") or {}
    swapped = bool(info.get("swapped"))
    running = next((w for k, w in STAGES if tone(cells.get(k)) == "run"), "")
    centre = hm(swap.get("since")) if swapped and swap.get("since") else (running[:2] if running else "—")
    head, tip = card(cells)
    if any(tone(cells.get(k)) == "bad" for k, _w in STAGES):
        cls = [*cls, "is-bad"]
    if swapped and info.get("new") is not None:
        moved = (f"<span class='cal-m'><span class='m-in'>+{info['new']:,}</span>"
                 f"<span class='m-out'>−{info.get('left') or 0:,}</span></span>")
    else:
        moved = "<span class='cal-m'>&nbsp;</span>"  # not swapped: the ring's dash says it
    dot = ("<span class='cal-dot' aria-label='留意'></span>"
           if any(tone(cells.get(k)) == "warn" for k, _w in STAGES) else "")
    # the source's own words, read whole on hover (data-tip-full: never cut to a line's length)
    tip_attr = f" data-tip-h='{e(head)}' data-tip='{e(tip)}' data-tip-full" if head else ""
    return (f"<a class='{' '.join(cls)}' href='{SITES}?date={e(day)}'{tip_attr}>{label}{dot}"
            f"<span class='cal-ic'>{ring(cells, centre, dial=week)}</span>{moved}</a>")


def week_cell(d: date, today: date, by_day: dict[str, dict]) -> str:
    key = d.isoformat()
    info = by_day.get(key)
    label = f"<span class='wk-day'>週{WEEKDAY[d.weekday()]}<b>{d.month}/{d.day}</b></span>"
    today_cls = ["is-today"] if d == today else []
    if not ran(info):
        # tonight not started (or a night read but nothing begun): a dashed ring; a night before the record: a dash
        body = WAIT_RING if (info and info.get("seen")) or d == today else NO_RECORD
        return f"<div class='{' '.join(['wk-d', 'is-empty', *today_cls])}'>{label}{body}</div>"
    return night_cell(key, info, ["wk-d", *today_cls], label, week=True)


def month_cell(d: date, today: date, month: int, by_day: dict[str, dict]) -> str:
    if d.month != month:
        return "<div class='cal-d is-out'></div>"
    num = f"<span class='cal-n'>{d.day}</span>"
    if d > today:
        return f"<div class='cal-d is-future'>{num}</div>"
    info = by_day.get(d.isoformat())
    cls = ["cal-d", *(["is-today"] if d == today else [])]
    if not ran(info):
        body = WAIT_RING if (info and info.get("seen")) or d == today else NO_RECORD
        return f"<div class='{' '.join([*cls, 'is-empty'])}'>{num}{body}</div>"
    return night_cell(d.isoformat(), info, cls, num, week=False)


def crawl_times(day: str, crawl: dict) -> tuple[str, str]:
    """(「MM-DD HH:MM」 a crawl's end, its floating card) of the night run on day; a dash saying so when its end is not
    read."""
    if not crawl.get("until"):
        return "—", f"上次換庫的那一晚（{day[5:]}）讀不到抓取是什麼時候結束的"
    until, since = crawl["until"], crawl.get("since") or ""
    span = f"{since[5:16].replace('T', ' ')}–{hm(until)}" if since else until[5:16].replace("T", " ")
    return until[5:16].replace("T", " "), f"{span} 抓下來的"


def library_times(by_day: dict[str, dict], today: date, last_swap: dict | None = None) -> tuple[str, str]:
    """(「MM-DD HH:MM」 the crawl end of the night whose library is in use, its floating card). The last night that
    swapped, not the night the page opens on (that one may not have swapped: the design copy's slip, OV-18): among the
    calendar's nights, else the nights' record's last (last_swap: history.Store.last_swapped, the night it has when a
    week or more of nights did not swap, exactly when the number matters). A dash saying why when neither has one."""
    for day in sorted(by_day, reverse=True):
        info = by_day[day]
        if day > today.isoformat() or not info.get("swapped"):
            continue
        return crawl_times(day, (info.get("cells") or {}).get("crawl") or {})
    if last_swap and last_swap.get("night"):
        crawl = next((from_store(s) for s in last_swap.get("steps") or [] if s.get("step") == "crawl"), {})
        return crawl_times(run_day(last_swap["night"]), crawl)
    return "—", "檢視台的紀錄裡還沒有換過庫的一晚"


def today_of(tonight: dict, now: datetime | None = None) -> date:
    if now is None and tonight.get("now"):
        try:
            now = datetime.fromisoformat(tonight["now"])
        except ValueError:
            now = None
    now = now or datetime.now(TAIPEI)
    return (now.astimezone(TAIPEI) if now.tzinfo else now).date()


def span_for(today: date) -> tuple[str, str]:
    """The nights (stored keys, first and last) the calendar of today needs: its month and its week."""
    first = min(today.replace(day=1), today - timedelta(days=WEEK - 1))
    return night_of_run(first.isoformat()), night_of_run(today.isoformat())


def build(tonight: dict, records: list[dict | None], *, changes: dict | None = None,
          now: datetime | None = None, last_swap: dict | None = None) -> dict:
    """The calendar's model: {'today', 'week': [run day, …], 'month': (year, month), 'nights': {run day: info},
    'library': (crawl end, its card)}. last_swap: the nights' record's last night that swapped (library_times)."""
    today = today_of(tonight, now)
    by_day = nights(tonight, records, changes if changes is not None else tonight.get("changes"))
    week = [(today - timedelta(days=i)).isoformat() for i in range(WEEK - 1, -1, -1)]
    return {"today": today.isoformat(), "week": week, "month": (today.year, today.month), "nights": by_day,
            "library": library_times(by_day, today, last_swap)}


def card_html(model: dict) -> str:
    """The calendar card (section#calendar), week shown first; the 週 / 月 switch, the countdown, the hover cards
    and the ? are the layer's (theme/design/home.js)."""
    today = date.fromisoformat(model["today"])
    by_day = model["nights"]
    year, month = model["month"]
    weeks = stdcal.Calendar(firstweekday=6).monthdatescalendar(year, month)
    heads = "".join(f"<span>{w}</span>" for w in HEADS)
    grid = "".join(month_cell(d, today, month, by_day) for wk in weeks for d in wk)
    week = "".join(week_cell(date.fromisoformat(d), today, by_day) for d in model["week"])
    lib, lib_tip = model.get("library") or ("", "")
    lib_html = (f"<span class='ct' tabindex='0' data-tip-h='現在這份庫的資料' data-tip='{e(lib_tip)}'>{e(lib)}</span>"
                if lib else "")
    times = (f"<span class='cal-times'>{lib_html}<span class='ct ct-next' id='calNext' tabindex='0' "
             "data-tip-h='距離下一次每晚更新' data-tip='每天 01:00 開始'>--:--:--</span></span>")
    switch = ("<div class='cal-switch' role='group' aria-label='多長'><button type='button' data-k='view' data-v='week' "
              "aria-pressed='true'>週</button><button type='button' data-k='view' data-v='month' "
              "aria-pressed='false'>月</button></div>")
    helpb = (f"<button type='button' class='hp' data-hp='cal' aria-label='這張卡片在說什麼' data-tip-h='每晚流程' "
             f"data-tip='{e(HELP)}' data-tip-full>?</button>")
    return (f"<section class='pn-card cal' id='calendar' data-view='week' aria-label='每晚流程'>"
            f"<div class='pn-chd'><h2><span class='t-month'>{year} 年 {month} 月</span></h2>{LEGEND}"
            f"<div class='cal-tools'>{times}{switch}{helpb}</div></div>"
            f"<div class='v-runs v-month'><div class='cal-grid'><div class='cal-head'>{heads}</div>{grid}</div></div>"
            f"<div class='v-runs v-week'><div class='wk-grid'>{week}</div></div></section>")
