"""總覽 (/home), the draft of 檢視台's front page (2026-11-22; the owner picked 「项目全景」: scale, where the documents
come from, how students use it, how right the search is, the nights and the crawler, as charts with few words).

Every number is read as a number: the pipeline's steps carry them (Step.nums: the services' use and the machine, the 33
questions), the sites' data and the nights' record are numbers already. Nothing is read back out of a sentence.
"""

from __future__ import annotations

import datetime as dt
import math
from urllib.parse import quote

from nknu.ops.look import CSS as LOOK_CSS
from nknu.ops.look import TIP_JS, e, icon
from nknu.ops.nightly import TONIGHT
from nknu.ops.pipeline_page import AGAIN
from nknu.ops.theme import bar as themebar

TIP_CSS = LOOK_CSS[LOOK_CSS.index(".tip{") :]

EPOCH = dt.date(1970, 1, 1)
KINDS = (
    ("notice", "公告", "var(--k0)"),
    ("page", "一般網頁", "var(--k1)"),
    ("attachment", "附件", "var(--k2)"),
    ("event", "行事曆", "var(--k3)"),
    ("link", "校外連結", "var(--k4)"),
)
# The nights' steps: the violet ramp, dark and light taking turns so two steps side by side never look alike, and the
# build neutral; not the kinds' colours (a document's kind on the same page) nor the states' (green is "fine")
GANTT_STEPS = (
    ("crawl", "爬取", "var(--q5)"),
    ("export", "導出", "var(--q2)"),
    ("totext", "轉文字", "var(--q4)"),
    ("build", "建庫", "var(--mute-s)"),
    ("swap", "換庫＋檢查", "var(--q3)"),
)
VIA = (("mcp", "MCP", "var(--q5)"), ("api", "接口", "var(--q3)"), ("web", "網頁", "var(--q1)"))
VERDICTS = (("right", "答對", "var(--good-s)"), ("partly", "部分對", "var(--warn-s)"), ("wrong", "答錯", "var(--bad-s)"),
            ("none", "沒答", "var(--mute-s)"))
STEP_MAP = {k: (w, c) for k, w, c in GANTT_STEPS}
MARK = {"ok": "ok", "done": "ok", "warn": "warn", "stop": "warn", "bad": "no", "none": "dash", "wait": "dash"}
SEV = {"done": "ok", "stop": "warn", "wait": "none"}
FIRST_YEAR = 2004
NIGHT_FROM, NIGHT_HOURS = 1, 9
NIGHTS = 6


def n(value: float) -> str:
    return f"{value:,}"


def pcts(values: list[int]) -> list[int]:
    """Integer percentages summing to 100 via largest-remainder rounding."""
    total = sum(values)
    if total <= 0:
        return [0] * len(values)
    exact = [v * 100 / total for v in values]
    floors = [int(x) for x in exact]
    rem = 100 - sum(floors)
    order = sorted(range(len(values)), key=lambda i: (-(exact[i] - floors[i]), -values[i]))
    for i in order[:rem]:
        floors[i] += 1
    return floors


def steps(snap: dict) -> dict:
    return {s["key"]: s for s in snap.get("steps") or []}


def mark(state: str, word: str = "") -> str:
    sev = SEV.get(state, state)
    return f"<span class='pn-mark pn-{e(sev)}'>{icon(MARK.get(state, 'dash'), 13)}{e(word)}</span>"


# -------------------------------------------------------------------------------------------- data shaping


def nums_of(snap: dict, key: str) -> dict:
    """A step's numbers (pipeline's Step.nums), {} when the step or its numbers are missing."""
    return (steps(snap).get(key) or {}).get("nums") or {}


def machine_meters(snap: dict) -> list[dict]:
    """The machine's drives and memories as meters: {label, tone, pct, detail, sub, tip}; one unread is said so."""
    serve = steps(snap).get("serve") or {}
    words = {r[0]: r[2] for r in serve.get("sides") or [] if len(r) >= 3}
    short = {"D 槽（爬蟲存檔）": "D 槽", "顯示卡記憶體": "顯示卡"}
    out = []
    for m in nums_of(snap, "serve").get("machine") or []:
        used, total = m.get("used"), m.get("total")
        disk = "槽" in m["name"]
        if used is None or not total:
            detail, pct = "讀不到", 0.0
        elif disk:
            detail, pct = f"還剩 {total - used:,.0f} GB，共 {total:,.0f}", used / total * 100
        else:
            detail, pct = f"用了 {used:.1f} GB，共 {total:.1f}", used / total * 100
        sub = f"交換區 {m['swap']:.1f} GB" if m.get("swap") else ""
        out.append({"label": short.get(m["name"], m["name"]), "tone": m.get("tone") or "none",
                    "pct": min(100.0, max(0.0, pct)), "detail": detail, "sub": sub,
                    "tip": f"{m['name']}：{words.get(m['name'], '')}"})
    return out


def warn_sites(snap: dict) -> list[dict]:
    """Sites marked warn or bad in the latest pipeline snapshot."""
    out = []
    for r in snap.get("sites") or []:
        if r.get("worst") in ("warn", "bad"):
            why = "；".join(r.get("why") or []) or "要留意"
            key = r.get("key") or ""
            out.append({
                "key": key,
                "name": r.get("name") or key,
                "state": r["worst"],
                "why": why,
                "href": r.get("href") or f"/site?site={quote(key, safe='')}",
            })
    return out


def by_group(sites: dict) -> tuple[list[dict], list[int]]:
    """Documents per school group (largest first) and overall 5-kind totals."""
    groups = sites.get("groups") or []
    out = {g: {"name": g, "kinds": [0] * len(KINDS), "sites": 0} for g in groups}
    totals = [0] * len(KINDS)
    for s in sites.get("sites") or []:
        g = out[groups[s["g"]]]
        g["sites"] += 1
        for i, v in enumerate(s.get("t") or []):
            if i < len(KINDS):
                g["kinds"][i] += v
                totals[i] += v
    rows = [dict(g, total=sum(g["kinds"])) for g in out.values()]
    return sorted((r for r in rows if r["total"]), key=lambda r: -r["total"]), totals


def by_year(sites: dict) -> dict:
    """Announcements, pages, and attachments by year (excluding calendar events)."""
    today = dt.date.fromisoformat(sites.get("today") or dt.date.today().isoformat())
    years = {y: [0, 0, 0] for y in range(FIRST_YEAR, today.year + 1)}
    since = today.replace(year=today.year - 1)
    older = later = recent = 0
    for rows in (sites.get("daily") or {}).values():
        for r in rows:
            got = r[1:4]
            if not any(got):
                continue
            day = EPOCH + dt.timedelta(days=r[0])
            if day > today:
                later += sum(got)
                continue
            if day > since:
                recent += sum(got)
            if day.year in years:
                years[day.year] = [a + b for a, b in zip(years[day.year], got)]
            else:
                older += sum(got)
    undated = sum(s.get("ud") or 0 for s in sites.get("sites") or [])
    dated = sum(sum(v) for v in years.values()) + older
    return {"years": list(years.items()), "older": older, "later": later, "undated": undated, "recent": recent,
            "dated": dated, "today": today}


def night_rows(tonight: dict, history: dict) -> list[dict]:
    """Recent nightly runs with normalized Gantt coordinates and accurate completion labels."""
    record = {r["night"]: r for r in history.get("nights") or [] if r}
    rows = []
    for night in tonight.get("nights") or []:
        cells = [c for c in night.get("cells") or [] if c.get("since")]
        if not cells:
            continue
        first = dt.datetime.fromisoformat(cells[0]["since"])
        start = first.replace(hour=NIGHT_FROM, minute=0, second=0, microsecond=0)
        span = NIGHT_HOURS * 60
        parts, end, swap_at = [], None, ""
        for c in cells:
            a = dt.datetime.fromisoformat(c["since"])
            b = dt.datetime.fromisoformat(c["until"]) if c.get("until") else a
            end = max(end or b, b)
            if c["key"] == "swap" and c["state"] != "stop":
                swap_at = a.strftime("%H:%M")
            left = max(0.0, (a - start).total_seconds() / 60)
            width = max(0.0, (b - a).total_seconds() / 60)
            label, colour = STEP_MAP.get(c["key"], (c.get("name") or c["key"], "var(--mute-s)"))
            parts.append({
                "key": c["key"],
                "name": label,
                "colour": colour,
                "state": c["state"],
                "left": min(left, span) / span * 100,
                "width": min(width, span - min(left, span)) / span * 100,
                "since": a.strftime("%H:%M"),
                "until": b.strftime("%H:%M"),
                "took": c.get("took") or "",
                "say": c.get("say") or "",
            })
        rec = record.get(night["night"]) or {}
        rows.append({
            "night": night["night"],
            "label": night.get("label") or night["night"],
            "state": night["state"],
            "word": night.get("word") or "",
            "parts": parts,
            "end": end.strftime("%H:%M") if end else "",
            "swap_at": swap_at,
            "swapped": bool(rec.get("swapped")),
            "new": rec.get("new"),
            "left": rec.get("left"),
            "changed": rec.get("changed"),
            "docs": rec.get("docs"),
            "tip": night.get("tip") or "",
        })
    return list(reversed(rows))[:NIGHTS]


def last_swap(history: dict) -> dict:
    """The nights' record of the last night that swapped the library ({} when none)."""
    return next((r for r in reversed(history.get("nights") or []) if r and r.get("swapped")), {})


def short_day(day: str) -> str:
    return f"{int(day[5:7])}/{int(day[8:10])}" if len(day) >= 10 else day


def split_bar(parts: list[tuple[str, int, str]], unit: str, cls: str = "pn-split") -> str:
    """One bar cut into its parts (name, count, colour), a 2px gap between, each part's count floating over it."""
    whole = sum(v for _, v, _ in parts) or 1
    segs = "".join(
        f"<i style='flex-grow:{v};background:{c}' tabindex='0' data-tip-h='{e(w)}' "
        f"data-tip='{n(v)} {e(unit)}（{v / whole:.0%}）'></i>"
        for w, v, c in parts if v
    )
    return f"<div class='{cls}'>{segs}</div>"


def key_line(parts: list[tuple[str, int, str]], *, share: bool = True) -> str:
    """The parts' names, counts and shares under a split bar (identity never by colour alone)."""
    shares = pcts([v for _, v, _ in parts])
    return "<div class='pn-ckeys'>" + "".join(
        f"<span><i style='background:{c}'></i>{e(w)} <b>{n(v)}</b>{f'<em>{p}%</em>' if share else ''}</span>"
        for (w, v, c), p in zip(parts, shares)
    ) + "</div>"


def day_columns(days: list[list], *, width: int = 520, height: int = 104, labels: bool = True) -> str:
    """The calls of each day as columns, today last; a day before the log began is a dashed stub (no record is no
    quiet day). labels: the count over each column and the day under it (the tile's small copy has none)."""
    if not days:
        return ""
    top = max((d[1] or 0 for d in days), default=0) or 1
    pt, pb = (16, 18) if labels else (2, 2)
    ph = height - pt - pb
    slot = width / len(days)
    bw = min(34.0, slot * 0.56)
    out = []
    for i, (day, calls, searches) in enumerate(days):
        x = i * slot + (slot - bw) / 2
        cx = x + bw / 2
        last = i == len(days) - 1
        name = "今天" if last else short_day(day)
        if calls is None:
            out.append(f"<rect x='{x:.1f}' y='{pt + ph - 3:.1f}' width='{bw:.1f}' height='3' rx='1.5' "
                       f"fill='none' stroke='var(--line2)' stroke-dasharray='3 2'/>"
                       f"<rect class='pn-hit' x='{i * slot:.1f}' y='0' width='{slot:.1f}' height='{height}' "
                       f"fill='transparent' tabindex='0' data-tip-h='{e(day)}' data-tip='查詢紀錄從這天以後才有'/>")
        else:
            h = max(2.0, ph * calls / top) if calls else 0.0
            y = pt + ph - h
            bar = (f"<rect x='{x:.1f}' y='{y:.1f}' width='{bw:.1f}' height='{h:.1f}' rx='3' fill='var(--amt)'/>"
                   if calls else f"<rect x='{x:.1f}' y='{pt + ph - 1:.1f}' width='{bw:.1f}' height='1' fill='var(--line2)'/>")
            val = (f"<text x='{cx:.1f}' y='{y - 5:.1f}' class='pn-peak' text-anchor='middle'>{n(calls)}</text>"
                   if labels else "")
            out.append(bar + val + f"<rect class='pn-hit' x='{i * slot:.1f}' y='0' width='{slot:.1f}' "
                       f"height='{height}' fill='transparent' tabindex='0' data-tip-h='{e(day)}{'（到現在）' if last else ''}' "
                       f"data-tip='{n(calls)} 次查詢，其中搜尋 {n(searches or 0)} 次'/>")
        if labels:
            out.append(f"<text x='{cx:.1f}' y='{height - 4}' class='pn-ax' text-anchor='middle'>{e(name)}</text>")
    base = f"<line x1='0' y1='{pt + ph:.1f}' x2='{width}' y2='{pt + ph:.1f}' stroke='var(--line)'/>" if labels else ""
    return (f"<svg class='pn-svg pn-days' viewBox='0 0 {width} {height}' role='img' "
            f"aria-label='最近 {len(days)} 天每天的查詢次數'>{base}{''.join(out)}</svg>")


# -------------------------------------------------------------------------------------------- SVG primitives


def svg_sparkline(points: list[tuple[str, int]], *, width: int = 92, height: int = 34, label: str = "") -> str:
    """Smooth area sparkline SVG for KPI trend."""
    if len(points) < 2:
        return ""
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pad_x, pad_y = 3.0, 4.0
    usable_w = width - pad_x * 2
    usable_h = height - pad_y * 2
    coords = []
    for i, (_d, v) in enumerate(points):
        x = pad_x + usable_w * i / (len(points) - 1)
        y = pad_y + usable_h * (1 - (v - lo) / span)
        coords.append((x, y))
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    area = f"{line} L{coords[-1][0]:.1f},{height:.1f} L{coords[0][0]:.1f},{height:.1f} Z"
    lx, ly = coords[-1]
    return (f"<svg class='pn-spark' width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
            f"role='img' aria-label='{e(label)}'><title>{e(label)}</title>"
            f"<defs><linearGradient id='pn-sp-g' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0%' stop-color='var(--amt)' stop-opacity='.28'/>"
            f"<stop offset='100%' stop-color='var(--amt)' stop-opacity='0'/></linearGradient></defs>"
            f"<path d='{area}' fill='url(#pn-sp-g)'/>"
            f"<path d='{line}' fill='none' stroke='var(--amt)' stroke-width='2' stroke-linecap='round' "
            f"stroke-linejoin='round'/>"
            f"<circle cx='{lx:.1f}' cy='{ly:.1f}' r='3' fill='var(--amt)'/></svg>")


def toggletip(tip_id: str, aria_label: str, body_html: str) -> str:
    """Accessible native toggletip button + hint popover (WCAG 1.4.13 compliant)."""
    return (f"<span class='pn-tt'>"
            f"<button type='button' class='pn-ibtn' interestfor='{e(tip_id)}' popovertarget='{e(tip_id)}' "
            f"popovertargetaction='show' aria-label='{e(aria_label)}'>i</button>"
            f"<div id='{e(tip_id)}' popover='hint' class='pn-pop pn-hint'>{body_html}</div></span>")


# -------------------------------------------------------------------------------------------- page sections


def header_bar(snap: dict, tonight: dict) -> str:
    st = steps(snap)
    sent = tonight.get("sentence") or {}
    serve = st.get("serve") or {}
    ws = warn_sites(snap)
    night_state = sent.get("state") or "none"
    night_short = (sent.get("text") or "尚無紀錄").split("：", 1)[0]
    srv_state = serve.get("state") or "none"
    srv_fig = serve.get("figure") or "—"
    srv_tip = serve.get("say") or ""

    warn_ctrl = ""
    if ws:
        items = "".join(
            f"<li><a href='{e(x['href'])}'><b>{e(x['name'])}</b><span>{e(x['why'])}</span></a></li>" for x in ws
        )
        warn_ctrl = (
            f"<button type='button' class='pn-pill pn-pill-warn' popovertarget='pn-warn-pop' aria-expanded='false'>"
            f"{icon('warn', 13)}<span>{len(ws)} 站待留意</span></button>"
            f"<div id='pn-warn-pop' popover='auto' class='pn-pop pn-warnlist'>"
            f"<div class='pn-pophd'>待留意網站（{len(ws)}）</div><ul>{items}</ul>"
            f"<a class='pn-popfoot' href='{TONIGHT}'>前往每晚運行處理 →</a></div>"
        )
    elif not snap.get("steps"):  # just restarted: nothing read of the steps yet, so nothing to call normal
        warn_ctrl = f"<span class='pn-pill'>{icon('dash', 13)}正在讀各步驟</span>"
    else:
        warn_ctrl = f"<span class='pn-pill pn-pill-ok'>{icon('ok', 13)}網站都正常</span>"

    return (
        f"<header class='pn-top'>"
        f"<div class='pn-title'><h1 id='main-title' tabindex='-1'>總覽</h1></div>"
        f"<div class='pn-status'>"
        f"<a class='pn-pill pn-pill-{e(SEV.get(night_state, night_state))}' href='{TONIGHT}' "
        f"data-tip='{e(sent.get('text') or '')}'>{icon(MARK.get(night_state, 'dash'), 13)}<span>{e(night_short)}</span></a>"
        f"<span class='pn-pill pn-pill-{e(SEV.get(srv_state, srv_state))}' tabindex='0' "
        f"data-tip='{e(srv_tip)}'>{icon(MARK.get(srv_state, 'dash'), 13)}<span>服務 {e(srv_fig)}</span></span>"
        f"{warn_ctrl}</div></header>"
    )


def hero_kpis(snap: dict, tonight: dict, sites: dict, history: dict,
              usage: dict | None, aitest: dict | None) -> str:
    st = steps(snap)
    changes = tonight.get("changes") or {}
    docs = ((changes.get("new") or {}).get("docs")) or next(
        (v for w, v in (st.get("build") or {}).get("out") or [] if w == "份文件"), 0
    )
    before = (changes.get("prev") or {}).get("docs")
    delta = f"{docs - before:+,}" if before else ""
    rec = last_swap(history)
    moved = f"昨晚新進 {n(rec['new'])} · 離開 {n(rec.get('left') or 0)}" if rec.get("new") is not None else ""
    hist_pts = [(r["night"][5:], int(r["docs"])) for r in history.get("nights") or [] if r and r.get("docs")]
    spark = svg_sparkline(hist_pts[-7:], label="近幾晚庫裡的文件數")
    spark_tip = "近幾晚庫裡的文件數：\n" + "\n".join(f"{d} 夜 {n(v)}" for d, v in hist_pts[-7:])

    site_n = len(sites.get("sites") or [])
    units = len({s.get("u") for s in sites.get("sites") or []})

    cards = [
        ("收錄文件", n(docs), f"<span class='pn-delta'>{e(delta)}</span>" if delta else "", moved, spark,
         spark_tip, "/sites"),
        ("收錄網站", n(site_n), "<span class='pn-unit'>站</span>", f"分屬 {units} 個單位", "",
         "學校和校外單位的網站，每晚更新", "/sites"),
    ]

    if usage:
        tools = usage.get("tools") or {}
        days = usage.get("days") or []
        cards.append((
            "對外查詢 · 24 小時",
            n(usage.get("calls") or 0),
            "<span class='pn-unit'>次</span>",
            f"搜尋 {n(tools.get('search', 0))} · 讀全文 {n(tools.get('fetch', 0))}",
            day_columns(days, width=84, height=36, labels=False) if days else "",
            "最近 7 天：\n" + "\n".join(f"{d} {'沒紀錄' if c is None else f'{n(c)} 次'}" for d, c, _ in days)
            if days else "",
            "",
        ))

    if aitest and aitest.get("n"):
        verdicts = [(w, aitest.get(k) or 0, c) for k, w, c in VERDICTS]
        cards.append((
            "AI 實測答對",
            f"{aitest['right']}/{aitest['n']}",
            "<span class='pn-unit'>題</span>",
            " · ".join(f"{w} {v}" for w, v, _ in verdicts[1:]) + f" · {(aitest.get('date') or '')[-5:]}",
            split_bar(verdicts, "題", "pn-split pn-tsplit"),
            (f"{aitest.get('date', '')} 實驗室的 AI 只用我們的 search 和 fetch，答學生角度的 {aitest['n']} 題\n"
             f"出處 {aitest.get('source', '')}"),
            "",
        ))

    html = []
    for title, big, suffix, sub, viz, tip, href in cards:
        inner = (
            f"<div class='pn-khead'>{e(title)}</div>"
            f"<div class='pn-kbody'><div class='pn-knum'><div class='pn-big'>{e(big)}{suffix}</div>"
            f"<div class='pn-ksub'>{e(sub)}</div></div><div class='pn-kviz'>{viz}</div></div>"
        )
        if href:
            html.append(f"<a class='pn-kpi pn-kpi-link' href='{e(href)}' aria-label='{e(title)} {e(big)}' "
                        f"data-tip='{e(tip)}'>{inner}</a>")
        else:
            html.append(f"<div class='pn-kpi' tabindex='0' data-tip='{e(tip)}'>{inner}</div>")
    return f"<section class='pn-kpis' aria-label='核心指標'>{''.join(html)}</section>"


def freshness_card(sites: dict) -> str:
    """SVG stacked vertical column chart of document years (2004-today) with gridlines."""
    d = by_year(sites)
    years = d["years"]
    max_y = max((sum(v) for _, v in years), default=1) or 1
    # Clean rounded top gridline (nearest 2,000)
    step_y = 2000 if max_y > 4000 else 1000 if max_y > 1500 else 500
    ceil_y = max(step_y, int(math.ceil(max_y / step_y) * step_y))

    w, h = 620, 290
    pl, pr, pt, pb = 38, 12, 22, 26
    pw, ph = w - pl - pr, h - pt - pb

    grid = []
    for val in range(0, ceil_y + 1, step_y):
        y = pt + ph * (1 - val / ceil_y)
        lbl = f"{val // 1000}k" if val >= 1000 else "0"
        dash = "" if val == 0 else " stroke-dasharray='3 3'"
        grid.append(
            f"<line x1='{pl}' y1='{y:.1f}' x2='{w - pr}' y2='{y:.1f}' stroke='var(--line)'{dash}/>"
            f"<text x='{pl - 7}' y='{y:.1f}' class='pn-ax' text-anchor='end' dominant-baseline='central'>{lbl}</text>"
        )

    bar_slot = pw / max(1, len(years))
    bw = max(6.0, bar_slot * 0.68)
    cols = []
    last_yr = years[-1][0] if years else 0
    peak_val = max((sum(v) for _, v in years), default=0)
    kind_colors = ("var(--k0)", "var(--k1)", "var(--k2)")

    for i, (yr, vals) in enumerate(years):
        tot = sum(vals)
        x = pl + i * bar_slot + (bar_slot - bw) / 2
        cx = x + bw / 2
        tip = f"公告 {n(vals[0])} · 一般網頁 {n(vals[1])} · 附件 {n(vals[2])}"
        if yr == last_yr:
            tip += f"\n截至 {d['today']:%m-%d}"

        y_cur = pt + ph
        rects = []
        for seg_v, col in zip(vals, kind_colors):
            if seg_v <= 0:
                continue
            sh = max(1.2, ph * seg_v / ceil_y)
            y_cur -= sh
            rects.append(f"<rect x='{x:.1f}' y='{y_cur:.1f}' width='{bw:.1f}' height='{sh:.1f}' rx='1.5' fill='{col}'/>")
        # Invisible full-column hit zone for effortless hovering & focus
        hit = (f"<rect class='pn-hit' x='{pl + i * bar_slot:.1f}' y='{pt}' width='{bar_slot:.1f}' height='{ph}' "
               f"fill='transparent' tabindex='0' data-tip-h='{yr} 年 · {n(tot)} 份' data-tip='{e(tip)}'/>")
        peak_lbl = ""
        if tot == peak_val and tot > 0:
            peak_lbl = (f"<text x='{cx:.1f}' y='{max(10.0, y_cur - 6):.1f}' class='pn-peak' "
                        f"text-anchor='middle'>{n(tot)}</text>")
        tick = ""
        if yr % 4 == 0 or yr == last_yr:
            tick = f"<text x='{cx:.1f}' y='{h - 8}' class='pn-ax' text-anchor='middle'>{yr}</text>"
        cols.append(f"<g class='pn-col'>{''.join(rects)}{peak_lbl}{hit}{tick}</g>")

    share = f"{d['recent'] / d['dated'] * 100:.0f}%" if d["dated"] else "—"
    leg = "".join(f"<span><i style='background:{c}'></i>{w_}</span>" for _, w_, c in KINDS[:3])
    chips = [f"近 12 月 <b>{n(d['recent'])}</b>（{share}）", f"無日期 <b>{n(d['undated'])}</b>",
             f"2004 前 <b>{n(d['older'])}</b>"]
    if d["later"]:
        chips.append(f"未來日期 <b>{n(d['later'])}</b>")
    foot = "".join(f"<span class='pn-mchip'>{c}</span>" for c in chips)
    svg = (f"<svg class='pn-svg' viewBox='0 0 {w} {h}' role='img' aria-label='各年份文件分佈'>"
           f"<title>2004 至 {last_yr} 年文件分佈</title>{''.join(grid)}{''.join(cols)}</svg>")
    return (
        f"<section class='pn-card pn-span7'>"
        f"<div class='pn-chd'><h2>文件年份分佈</h2><div class='pn-leg'>{leg}</div></div>"
        f"{svg}<div class='pn-mchips'>{foot}</div></section>"
    )


def sources_card(sites: dict) -> str:
    """Donut chart of overall 5 document kinds + compact ranked org stacked bars."""
    rows, totals = by_group(sites)
    kinds = [(w, totals[i], c) for i, (_, w, c) in enumerate(KINDS) if totals[i]]

    top = max((r["total"] for r in rows), default=1)
    bars = []
    for r in rows:
        bsegs = "".join(
            f"<i style='flex-grow:{v};background:{c}' data-tip-h='{e(r['name'])} · {e(w)}' data-tip='{n(v)} 份'></i>"
            for v, (_, w, c) in zip(r["kinds"], KINDS) if v
        )
        tip = "\n".join(f"{w} {n(v)}" for v, (_, w, _) in zip(r["kinds"], KINDS) if v)
        bars.append(
            f"<div class='pn-orow'>"
            f"<span class='pn-oname' tabindex='0' data-tip-h='{e(r['name'])}（{r['sites']} 站）' data-tip='{e(tip)}'>"
            f"{e(r['name'])}</span>"
            f"<span class='pn-otrack'><span class='pn-ostack' style='width:{r['total'] / top * 100:.1f}%'>{bsegs}"
            f"</span></span>"
            f"<span class='pn-oval'>{n(r['total'])}</span></div>"
        )
    return (
        f"<section class='pn-card pn-span5'>"
        f"<div class='pn-chd'><h2>文件從哪裡來</h2><a class='pn-link' href='/sites'>全部網站 →</a></div>"
        f"{split_bar(kinds, '份')}{key_line(kinds)}"
        f"<div class='pn-obars'>{''.join(bars)}</div></section>"
    )


def quality_card(snap: dict, newset: dict | None) -> str:
    """Where the right document comes in a search, as nested bars (first, top 3, top 10, out of the questions): the
    students' own words first (the yardstick), then an AI's rewording, then the nightly 33. The number said is the
    share at first place: a student's AI that reads only the top few sees that one (the top 10 hid how few were
    first: 83% in the top 10, 35% first, for the students' words on 11-19)."""
    ladders = []
    b = next((g for g in (newset or {}).get("groups") or [] if g.get("key") == "B"), None)
    if b and b.get("n"):
        when = (newset.get("measured_at") or "")[5:10]
        ladders.append(("學生原話", f"{b['n']} 題 · {when}", b["q"], b["n"],
                        "學生問的那句話原樣送進搜尋，標準答案的文件排第幾"))
        ladders.append(("AI 換說法", f"{b['n']} 題 · {when}", b["a"], b["n"],
                        "錄下來的 AI 自己改寫、多搜幾次，最好的一次排第幾"))
    c = nums_of(snap, "check")
    if c.get("n") and c.get("top1") is not None:
        ladders.append(("每晚 33 題", "每次建庫後量" + ("（上一份庫的）" if c.get("stale") else ""),
                        [c["top1"], c["top3"], c["top10"]], c["n"], "只當別退步的底線：比上一次少了才亮"))

    rows_html = []
    for label, sub, tops, of, tip in ladders:
        p1, p3, p10 = [t / of * 100 for t in tops]
        rows_html.append(
            f"<div class='pn-lrow' tabindex='0' data-tip-h='{e(label)}（{e(sub)}）' "
            f"data-tip='排第 1：{tops[0]} 題（{p1:.0f}%）\n前 3：{tops[1]} 題（{p3:.0f}%）\n"
            f"前 10：{tops[2]} 題（{p10:.0f}%）\n{e(tip)}'>"
            f"<div class='pn-llbl'><b>{e(label)}</b><span>{e(sub)}</span></div>"
            f"<div class='pn-ltrack'>"
            f"<i class='pn-l10' style='width:{p10:.1f}%'></i>"
            f"<i class='pn-l3' style='width:{p3:.1f}%'></i>"
            f"<i class='pn-l1' style='width:{p1:.1f}%'></i></div>"
            f"<div class='pn-lnum'><b>{p1:.0f}%</b><span>排第 1</span></div></div>"
        )

    real_chip = ""
    if c.get("real_total"):
        real_chip = f"<span class='pn-mchip'>真資料檢查過了 <b>{c['real_passed']}/{c['real_total']}</b> 項</span>"

    leg = (
        "<div class='pn-leg pn-leg-sm'>"
        "<span><i style='background:var(--q5)'></i>排第 1</span>"
        "<span><i style='background:var(--q3)'></i>前 3</span>"
        "<span><i style='background:var(--q1)'></i>前 10</span></div>"
    )
    body = (f"<div class='pn-lhead'><span>標準答案的文件排第幾</span>{leg}</div>"
            f"<div class='pn-ladders'>{''.join(rows_html)}</div>") if rows_html else "<p class='pn-empty'>還沒有量過</p>"
    return (
        f"<section class='pn-card'>"
        f"<div class='pn-chd'><h2>搜尋排得準不準</h2><a class='pn-link' href='/try'>測試頁 →</a></div>"
        f"{body}<div class='pn-mchips'>{real_chip}</div></section>"
    )


def usage_card(snap: dict, usage: dict | None) -> str:
    """The public service: its calls a day over the last week, where the last day's came from and how they went."""
    if usage and usage.get("calls") is not None:
        days = usage.get("days") or []
        via = [(w, (usage.get("via") or {}).get(k, 0), c) for k, w, c in VIA]
        mid, slow = usage.get("mid_ms"), usage.get("slow_ms")
        speed = (f"<b>{mid / 1000:.1f}</b> 秒" if mid is not None else "<b>—</b>")
        speed_tip = (f"一半在 {mid / 1000:.1f} 秒內答完，最慢的一成要 {slow / 1000:.1f} 秒以上"
                     if mid is not None and slow is not None else "答完的搜尋太少，還算不出")
        ours, theirs = usage.get("failed_ours") or 0, (usage.get("failed") or 0) - (usage.get("failed_ours") or 0)
        fail_tip = (f"排隊太久、庫讀不到或程式出錯：{ours} 次"
                    + (f"\n另有 {theirs} 次是呼叫的參數不對（對方的錯，不算我們的）" if theirs else ""))
        fail_mark = mark("warn" if ours else "ok", f"{ours} 次")
        last = (usage.get("last") or "")[5:16].replace("T", " ")
        stats = (
            f"<div class='pn-ustats'>"
            f"<div class='pn-ustat' tabindex='0' data-tip='{e(speed_tip)}'><span>一半在幾秒內答完</span>"
            f"<b>{speed}</b></div>"
            f"<div class='pn-ustat'><span>搜了沒找到</span><b>{n(usage.get('missed') or 0)} 次</b></div>"
            f"<div class='pn-ustat' tabindex='0' data-tip='{e(fail_tip)}'>"
            f"<span>我們沒答成</span><b>{fail_mark}</b></div>"
            f"<div class='pn-ustat'><span>最後一次</span><b>{e(last or '—')}</b></div>"
            f"</div>"
        )
        week = (f"<div class='pn-subhd'><span>最近 {len(days)} 天，每天幾次</span></div>{day_columns(days)}"
                if days else "")
        usage_html = (
            f"{week}<div class='pn-subhd{' pn-subhd-sep' if days else ''}'><span>最近 24 小時從哪裡來</span></div>"
            f"{split_bar(via, '次')}{key_line(via)}{stats}"
        )
    else:
        usage_html = "<p class='pn-empty'>讀不到對外服務的查詢紀錄</p>"

    who = toggletip("pn-tip-who", "這些查詢是誰查的",
                    "紀錄分不出是學生還是我們自己在測試（跑題庫、AI 實測都走同一個服務），"
                    "有測試的那幾天會特別高。")
    return (
        f"<section class='pn-card pn-span6'>"
        f"<div class='pn-chd'><h2>對外查詢</h2>{who}</div>"
        f"{usage_html}</section>"
    )


def machine_card(snap: dict) -> str:
    """The machine everything runs on: its drives, Docker's memory, the graphics card's, as meters."""
    m_html = []
    for m in machine_meters(snap):
        bar_col = "var(--bad-s)" if m["tone"] == "bad" else "var(--warn-s)" if m["tone"] == "warn" else "var(--amt)"
        state = mark(m["tone"]) if m["tone"] in ("warn", "bad") else ""
        sub = f" · {m['sub']}" if m["sub"] else ""
        m_html.append(
            f"<div class='pn-meterbox' tabindex='0' data-tip='{e(m['tip'])}'>"
            f"<div class='pn-mtop'><b>{e(m['label'])}{state}</b><span>{m['pct']:.0f}%</span></div>"
            f"<div class='pn-mtrack'><i style='width:{max(2.0, m['pct']):.1f}%;background:{bar_col}'></i></div>"
            f"<div class='pn-mbot'>{e(m['detail'])}{e(sub)}</div></div>"
        )
    if not m_html:
        return ""
    return (f"<section class='pn-card'><div class='pn-chd'><h2>這台電腦</h2></div>"
            f"<div class='pn-mgrid'>{''.join(m_html)}</div></section>")


def nights_card(tonight: dict, history: dict) -> str:
    """Color-coded 5-stage nightly Gantt timeline with clean stop markers and consistent finish times."""
    rows = night_rows(tonight, history)
    if not rows:
        return "<section class='pn-card pn-span7'><h2>每晚運行</h2><p class='pn-empty'>尚無紀錄</p></section>"

    leg = "".join(f"<span><i style='background:{c}'></i>{e(w)}</span>" for _, w, c in GANTT_STEPS)
    hours = "".join(
        f"<span style='left:{h / NIGHT_HOURS * 100:.2f}%'>{NIGHT_FROM + h:02d}:00</span>"
        for h in range(0, NIGHT_HOURS, 2)
    )

    body = []
    for r in rows:
        segs = []
        for p in r["parts"]:
            if p["state"] == "stop":
                # Place stop icon cleanly AFTER the last completed bar (no overlap!)
                segs.append(
                    f"<b class='pn-stop' style='left:calc({p['left']:.2f}% + 9px)' tabindex='0' "
                    f"data-tip-h='{e(p['name'])}停在 {e(p['since'])}' data-tip='{e(p['say'])}'>"
                    f"{icon('warn', 11)}</b>"
                )
                continue
            tip_body = f"{p['since']}–{p['until']}" + (f"（{p['took']}）" if p["took"] else "")
            if p["say"]:
                tip_body += f"\n{p['say']}"
            segs.append(
                f"<i class='pn-gseg' style='left:{p['left']:.2f}%;width:{max(0.5, p['width']):.2f}%;"
                f"background:{p['colour']}' tabindex='0' data-tip-h='{e(p['name'])}' data-tip='{e(tip_body)}'></i>"
            )
        if r["swapped"] and r["swap_at"]:
            end_lbl, end_tip = f"{r['swap_at']} 換上", f"學生從 {r['swap_at']} 起搜到新庫；換庫後的檢查到 {r['end']} 跑完"
        else:
            end_lbl, end_tip = f"{r['end']} 停", "這一晚沒有換庫，學生搜到的還是前一晚的庫"
        end = f"<span class='pn-gend' tabindex='0' data-tip='{e(end_tip)}'>{e(end_lbl)}</span>"
        if r["new"] is None:
            moved = "<span class='pn-gdelta'>—</span>"
        elif not r["swapped"]:
            moved = f"<span class='pn-gdelta pn-gmuted' data-tip='庫維持 {n(r['docs'] or 0)} 份'>沒換庫</span>"
        else:
            moved = (
                f"<span class='pn-gdelta' tabindex='0' data-tip='新進 {r['new']} · 異動 {r['changed'] or 0} · "
                f"離開 {r['left'] or 0}\n庫共 {n(r['docs'] or 0)} 份'>"
                f"<b class='pn-pos'>+{n(r['new'])}</b> <span class='pn-neg'>−{n(r['left'] or 0)}</span></span>"
            )
        body.append(
            f"<div class='pn-grow'><span class='pn-glbl' data-tip='{e(r['tip'])}'>{e(r['label'])}</span>"
            f"{mark(r['state'], r['word'])}<span class='pn-gtrack'>{''.join(segs)}</span>{end}{moved}</div>"
        )

    return (
        f"<section class='pn-card pn-span7'>"
        f"<div class='pn-chd'><h2>每晚運行</h2><a class='pn-link' href='{TONIGHT}'>完整時間軸 →</a></div>"
        f"<div class='pn-leg pn-gleg'>{leg}</div>"
        f"<div class='pn-gantt'>"
        f"<div class='pn-grow pn-ghours'><span></span><span></span>"
        f"<span class='pn-gtrack'>{hours}</span><span></span><span></span></div>"
        f"{''.join(body)}</div></section>"
    )


def crawler_card(snap: dict, tonight: dict) -> str:
    """Compact crawler pulse: nightly delta bar + coverage breakdown + 3 branch status dots."""
    crawl = steps(snap).get("crawl") or {}
    cr = tonight.get("crawler") or {}
    counts = dict(cr.get("counts") or [])
    order = (
        ("新的", "var(--q5)"),
        ("改了", "var(--q3)"),
        ("沒變", "var(--line2)"),
        ("沒抓到", "var(--bad-s)"),
        ("沒看到", "var(--mute-s)"),
    )
    parts = [(w, counts.get(w, 0), c) for w, c in order if w in counts]
    seen = sum(v for _, v, _ in parts)
    night = (cr.get("night") or "")[5:]

    nightly_html = ""
    if parts:
        segs = "".join(
            f"<i style='flex-grow:{v};background:{c}' data-tip-h='{e(w)}' data-tip='{n(v)} 個網址'></i>"
            for w, v, c in parts if v
        )
        p_vals = pcts([v for _, v, _ in parts])
        keys = "".join(
            f"<span><i style='background:{c}'></i>{e(w)} <b>{n(v)}</b><em>{p}%</em></span>"
            for (w, v, c), p in zip(parts, p_vals)
        )
        nightly_html = (
            f"<div class='pn-subhd'><span>{e(night)} 夜的每晚更新：看了 {n(seen)} 個網址，{cr.get('sites_changed', 0)} 個網站有變</span></div>"
            f"<div class='pn-cbar'>{segs}</div><div class='pn-ckeys'>{keys}</div>"
        )

    got = crawl.get("bar") or [0, 0]
    lost = crawl.get("lost") or []
    top_v = max((v for _, v in lost[:4]), default=1) or 1
    reasons = []
    for w, v in lost[:4]:
        short_w = (w.replace("學校那邊已經不存在", "校方已刪除 / 404")
                   .replace("待確認：Google API 讀不到（不公開或已刪除，分不出）", "Google 權限/已刪")
                   .replace("待確認：", ""))
        is_ext = "不存在" in w or "連接失敗" in w or "Google" in w
        tag = "<em class='pn-rtag'>校方</em>" if is_ext else "<em class='pn-rtag pn-rtag-w'>待確認</em>"
        reasons.append(
            f"<div class='pn-rrow'><span class='pn-rname' data-tip='{e(w)}'>{tag}{e(short_w)}</span>"
            f"<span class='pn-rtrack'><i style='width:{v / top_v * 100:.1f}%'></i></span>"
            f"<span class='pn-rval'>{n(v)}</span></div>"
        )
    rest = sum(v for _, v in lost[4:])
    cov_pct = (got[0] / got[1] * 100) if len(got) == 2 and got[1] else 0.0
    cover_html = ""
    if len(got) == 2 and got[1]:
        cov_tip = toggletip("pn-tip-cov", "這些沒抓到的是什麼",
                            "這一輪該抓的網址裡沒抓到的，大多是學校那邊刪了頁、連不上或不公開，"
                            "不是昨晚才壞的；「待確認」的要人看一眼。")
        more = f"<div class='pn-rmore'>其餘 {len(lost) - 4} 種原因共 {n(rest)}</div>" if rest else ""
        cover_html = (
            f"<div class='pn-subhd pn-subhd-sep'><span>這一輪該抓的網址抓到 {cov_pct:.1f}%"
            f"（{n(got[0])} / {n(got[1])}），沒抓到的原因</span>{cov_tip}</div>"
            f"<div class='pn-rbars'>{''.join(reasons)}</div>{more}"
        )

    sides = "".join(
        f"<div class='pn-sidepill' tabindex='0' data-tip='{e(t)}'>{mark(s)}<span>{e(w)}</span></div>"
        for w, s, t in crawl.get("sides") or []
    )
    return (
        f"<section class='pn-card pn-span5'>"
        f"<div class='pn-chd'><h2>爬蟲狀態</h2><a class='pn-link' href='/check'>網頁核查 →</a></div>"
        f"{nightly_html}{cover_html}"
        f"<div class='pn-sidepills'>{sides}</div></section>"
    )


# -------------------------------------------------------------------------------------------- styles & script


CSS = """
:root{color-scheme:light dark}
.visually-hidden:where(:not(:focus-within,:active)){position:absolute!important;clip-path:inset(50%)!important;
overflow:hidden!important;width:1px!important;height:1px!important;margin:-1px!important;padding:0!important;
border:0!important;white-space:nowrap!important}
.pn-skip:focus{position:fixed;top:10px;left:10px;z-index:100;padding:8px 14px;border-radius:10px;
background:var(--act);color:var(--on-act);text-decoration:none;font-weight:650}

/* Main canvas */
main{max-width:var(--page);margin:0 auto;padding:18px var(--gutter) 44px}
.pn-wait{margin:0 0 14px;padding:9px 13px;border-radius:10px;background:var(--sunk);color:var(--ink2);font-size:13px}

/* Top header strip */
.pn-top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 16px;margin:0 0 18px}
.pn-title{display:flex;align-items:baseline;gap:12px}
.pn-title h1{margin:0;font-size:20px;font-weight:700;letter-spacing:-.02em}
.pn-status{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.pn-pill{display:inline-flex;align-items:center;gap:5px;min-block-size:30px;padding:4px 11px;border-radius:999px;
border:1px solid var(--line);background:var(--surface);color:var(--ink2);font:600 12px/1 var(--font);
text-decoration:none;cursor:pointer;white-space:nowrap;transition:border-color .12s,background .12s}
.pn-pill:hover{border-color:var(--line2);color:var(--ink)}
.pn-pill-ok{background:var(--good-bg);border-color:transparent;color:var(--good-ink)}
.pn-pill-warn{background:var(--warn-bg);border-color:transparent;color:var(--warn-ink)}
.pn-pill-bad{background:var(--bad-bg);border-color:transparent;color:var(--bad-ink)}

/* Hero KPI row: 4-up desktop, clean 2x2 medium, 1-up small (no 3+1 orphan!) */
.pn-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:16px}
@media (max-width:1040px){.pn-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){.pn-kpis{grid-template-columns:minmax(0,1fr)}}
.pn-kpi{display:flex;flex-direction:column;justify-content:space-between;gap:8px;padding:16px 18px;
border:1px solid var(--line);border-radius:16px;background:var(--surface);box-shadow:var(--shadow);
text-decoration:none;color:inherit;transition:border-color .15s,transform .15s}
.pn-kpi-link:hover{border-color:var(--line2);transform:translateY(-1px)}
.pn-khead{font-size:12px;font-weight:600;color:var(--ink3);letter-spacing:.01em}
.pn-kbody{display:flex;align-items:flex-end;justify-content:space-between;gap:10px}
.pn-knum{min-width:0}
.pn-big{font-size:26px;font-weight:700;line-height:1.1;letter-spacing:-.02em;font-variant-numeric:tabular-nums;
display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.pn-unit{font-size:13px;font-weight:500;color:var(--ink3)}
.pn-delta{font-size:12px;font-weight:650;padding:2px 6px;border-radius:999px;background:var(--good-bg);
color:var(--good-ink)}
.pn-ksub{margin-top:6px;font-size:12px;line-height:1.4;color:var(--ink3);font-variant-numeric:tabular-nums}
.pn-kviz{flex:none;display:flex;align-items:center}

/* Bento grid (12 columns on desktop) */
.pn-bento{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}
.pn-span7{grid-column:span 7}.pn-span5{grid-column:span 5}.pn-span6{grid-column:span 6}
.pn-stack{display:flex;flex-direction:column;gap:16px;min-width:0}
@media (max-width:1100px){.pn-span7,.pn-span5,.pn-span6{grid-column:1/-1}}

/* Card shell */
.pn-card{container-type:inline-size;min-width:0;padding:18px 20px;border:1px solid var(--line);border-radius:16px;
background:var(--surface);box-shadow:var(--shadow);display:flex;flex-direction:column}
.pn-chd{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;margin:0 0 14px}
.pn-chd h2{margin:0;font-size:16px;font-weight:700;letter-spacing:-.01em}
.pn-link{font-size:12px;font-weight:600;color:var(--ink3);text-decoration:none;min-block-size:24px;
display:inline-flex;align-items:center}
.pn-link:hover{color:var(--ink)}
.pn-leg{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font-size:11px;color:var(--ink2)}
.pn-leg span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.pn-leg i{width:8px;height:8px;border-radius:2px;flex:none}
.pn-subhd{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:650;color:var(--ink2);margin:0 0 10px}
.pn-subhd-sep{margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.pn-empty{margin:4px 0;font-size:13px;color:var(--ink3)}

/* Freshness SVG stacked column chart */
.pn-svg{width:100%;height:auto;display:block;overflow:visible}
.pn-ax{font-size:11px;fill:var(--ink3);font-variant-numeric:tabular-nums}
.pn-peak{font-size:11px;font-weight:700;fill:var(--ink);font-variant-numeric:tabular-nums}
.pn-col rect:not(.pn-hit){transition:opacity .12s}
.pn-svg:has(.pn-col:hover) .pn-col:not(:hover) rect:not(.pn-hit){opacity:.42}
.pn-hit{cursor:pointer}
.pn-mchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto;padding-top:14px}
.pn-mchip{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:999px;background:var(--sunk);
font-size:11px;color:var(--ink2);font-variant-numeric:tabular-nums}
.pn-mchip b{color:var(--ink);font-weight:650}

/* where the documents come from: groups as stacked bars */
.pn-obars{display:flex;flex-direction:column;gap:6px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.pn-orow{display:grid;grid-template-columns:64px minmax(0,1fr) 50px;align-items:center;gap:8px;font-size:12px}
.pn-oname{color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:default}
.pn-otrack{display:block;height:10px;border-radius:4px;background:var(--sunk);overflow:hidden}
.pn-ostack{display:flex;gap:1.5px;height:100%;min-width:3px}
.pn-ostack i{display:block;min-width:2px;height:100%}
.pn-ostack i:first-child{border-radius:4px 0 0 4px}.pn-ostack i:last-child{border-radius:0 4px 4px 0}
.pn-oval{text-align:right;color:var(--ink3);font-variant-numeric:tabular-nums}

/* Quality card: AI ring + unified percentage ladders */
.pn-lhead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px;font-size:12px;
font-weight:650;color:var(--ink2);margin-bottom:10px}
.pn-ladders{display:flex;flex-direction:column;gap:11px}
.pn-lrow{display:grid;grid-template-columns:118px minmax(0,1fr) 54px;align-items:center;gap:10px;cursor:default}
.pn-llbl{display:flex;flex-direction:column;line-height:1.25}
.pn-llbl b{font-size:13px;font-weight:650;color:var(--ink)}
.pn-llbl span{font-size:11px;color:var(--ink3)}
.pn-ltrack{position:relative;height:16px;border-radius:6px;background:var(--qx);overflow:hidden}
.pn-ltrack i{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.pn-l10{background:var(--q1)}.pn-l3{background:var(--q3)}.pn-l1{background:var(--q5)}
.pn-lnum{text-align:right;font-variant-numeric:tabular-nums;line-height:1.2}
.pn-lnum b{display:block;font-size:13px;color:var(--ink)}
.pn-lnum span{font-size:11px;color:var(--ink3)}

/* Usage + Host 2x2 meters */
.pn-ustats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:12px}
.pn-ustat{padding:7px 10px;border-radius:10px;background:var(--sunk);display:flex;flex-direction:column;gap:2px}
.pn-ustat span{font-size:11px;color:var(--ink3);white-space:nowrap}
.pn-ustat b{font-size:13px;color:var(--ink);font-variant-numeric:tabular-nums;white-space:nowrap}
.pn-mgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
@container (max-width:440px){.pn-ustats{grid-template-columns:repeat(2,minmax(0,1fr))}}
@container (max-width:360px){.pn-mgrid{grid-template-columns:minmax(0,1fr)}}
.pn-meterbox{padding:10px 12px;border-radius:11px;background:var(--sunk);display:flex;flex-direction:column;gap:6px}
.pn-mtop{display:flex;justify-content:space-between;align-items:baseline;font-size:12px}
.pn-mtop b{color:var(--ink);font-weight:650}.pn-mtop span{color:var(--ink2);font-weight:650;font-variant-numeric:tabular-nums}
.pn-mtrack{height:7px;border-radius:999px;background:var(--line);overflow:hidden}
.pn-mtrack i{display:block;height:100%;border-radius:999px}
.pn-mbot{font-size:11px;color:var(--ink3);font-variant-numeric:tabular-nums}

/* Nightly color-coded Gantt */
.pn-gleg{margin-bottom:12px}
.pn-gantt{display:flex;flex-direction:column;gap:10px}
.pn-grow{display:grid;grid-template-columns:56px 56px minmax(0,1fr) 64px 78px;align-items:center;gap:10px;font-size:12px}
.pn-glbl{color:var(--ink);font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.pn-gtrack{position:relative;height:18px;border-radius:5px;background:repeating-linear-gradient(90deg,transparent 0,
transparent calc(22.222% - 1px),var(--line) calc(22.222% - 1px),var(--line) 22.222%)}
.pn-ghours .pn-gtrack{height:14px;background:none}
.pn-ghours .pn-gtrack span{position:absolute;transform:translateX(-50%);font-size:11px;color:var(--ink3);
font-variant-numeric:tabular-nums;white-space:nowrap}
.pn-ghours .pn-gtrack span:first-child{transform:none}
.pn-gseg{position:absolute;top:2px;bottom:2px;min-width:4px;border-radius:4px;box-shadow:0 0 0 1px var(--surface);
cursor:pointer;transition:filter .12s,transform .12s}
.pn-gseg:hover{filter:brightness(1.12);transform:scaleY(1.18);z-index:2}
.pn-stop{position:absolute;top:1px;z-index:3;display:flex;align-items:center;justify-content:center;
width:16px;height:16px;border-radius:50%;background:var(--warn-bg);color:var(--warn-ink);
box-shadow:0 0 0 2px var(--surface);cursor:pointer}
.pn-gend{color:var(--ink2);font-variant-numeric:tabular-nums;white-space:nowrap}
.pn-gdelta{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pn-pos{color:var(--ink);font-weight:650}.pn-neg{color:var(--ink3)}.pn-gmuted{color:var(--ink3)}
@container (max-width:560px){
  .pn-grow{grid-template-columns:auto auto minmax(0,1fr) auto;row-gap:4px}
  .pn-grow>.pn-gtrack{grid-column:1/-1;grid-row:2}
  .pn-ghours>span:not(.pn-gtrack){display:none}.pn-ghours>.pn-gtrack{grid-row:1}
}

/* a bar cut into its parts; the days' columns */
.pn-split{display:flex;gap:2px;height:12px;margin:0 0 8px}
.pn-split i{display:block;min-width:3px;height:100%;cursor:pointer;transition:opacity .12s}
.pn-split i:first-child{border-radius:4px 0 0 4px}.pn-split i:last-child{border-radius:0 4px 4px 0}
.pn-split i:only-child{border-radius:4px}
.pn-split:has(i:hover) i:not(:hover){opacity:.45}
.pn-tsplit{width:84px;height:8px;margin:0 0 4px}
.pn-kviz .pn-days{width:84px;height:36px}
.pn-days .pn-hit{cursor:pointer}
.pn-ckeys{margin-bottom:2px}

/* Crawler pulse */
.pn-cbar{display:flex;gap:2px;height:12px;margin-bottom:8px}
.pn-cbar i{display:block;min-width:3px;height:100%}
.pn-cbar i:first-child{border-radius:4px 0 0 4px}.pn-cbar i:last-child{border-radius:0 4px 4px 0}
.pn-ckeys{display:flex;flex-wrap:wrap;gap:5px 12px;font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.pn-ckeys span{display:inline-flex;align-items:center;gap:5px}
.pn-ckeys i{width:8px;height:8px;border-radius:2px;flex:none}
.pn-ckeys b{color:var(--ink);font-weight:650}.pn-ckeys em{font-style:normal;color:var(--ink3);font-size:11px}
.pn-rbars{display:flex;flex-direction:column;gap:7px}
.pn-rrow{display:grid;grid-template-columns:minmax(0,148px) minmax(0,1fr) 44px;align-items:center;gap:8px;font-size:12px}
.pn-rname{display:inline-flex;align-items:center;gap:5px;color:var(--ink2);white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.pn-rtag{font-style:normal;font-size:11px;padding:1px 5px;border-radius:4px;background:var(--sunk);color:var(--ink3);
flex:none}
.pn-rtag-w{background:var(--warn-bg);color:var(--warn-ink)}
.pn-rtrack{display:block;height:8px;border-radius:4px;background:var(--sunk);overflow:hidden}
.pn-rtrack i{display:block;height:100%;border-radius:4px;background:var(--line2)}
.pn-rval{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums}
.pn-rmore{margin-top:6px;font-size:11px;color:var(--ink3)}
.pn-sidepills{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.pn-sidepill{display:inline-flex;align-items:center;gap:5px;padding:4px 9px;border:1px solid var(--line);
border-radius:999px;background:var(--sunk);font-size:11px;color:var(--ink2);cursor:default}

/* Status marks */
.pn-mark{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:650;white-space:nowrap}
.pn-mark .ic{flex:none;border-radius:50%;padding:1px}
.pn-ok{color:var(--good-ink)}.pn-ok .ic{background:var(--good-bg)}
.pn-warn{color:var(--warn-ink)}.pn-warn .ic{background:var(--warn-bg)}
.pn-bad{color:var(--bad-ink)}.pn-bad .ic{background:var(--bad-bg)}
.pn-none{color:var(--ink3)}

/* Native Popovers & Toggletips (anchor positioning gated by @supports) */
.pn-tt{display:inline-flex;align-items:center}
.pn-ibtn{display:inline-grid;place-items:center;min-inline-size:24px;min-block-size:24px;padding:0;
border:1px solid var(--line2);border-radius:50%;background:var(--surface);color:var(--ink3);
font:700 11px/1 var(--font);cursor:pointer}
.pn-ibtn:hover{border-color:var(--ink3);color:var(--ink)}
[popover].pn-pop{border:1px solid var(--line2);border-radius:12px;background:var(--surface);color:var(--ink);
box-shadow:var(--pop);padding:10px 12px;max-width:min(320px,90vw);font-size:12px;line-height:1.5}
.pn-hint{color:var(--ink2)}
.pn-warnlist{padding:12px 14px;width:min(340px,92vw)}
.pn-pophd{font-size:12px;font-weight:700;color:var(--ink);margin-bottom:8px}
.pn-warnlist ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.pn-warnlist li a{display:flex;flex-direction:column;gap:1px;padding:6px 8px;border-radius:8px;text-decoration:none}
.pn-warnlist li a:hover{background:var(--hover)}
.pn-warnlist li b{font-size:13px;color:var(--ink);font-weight:600}
.pn-warnlist li span{font-size:11px;color:var(--ink3)}
.pn-popfoot{display:inline-block;margin-top:8px;padding-top:8px;border-top:1px solid var(--line);width:100%;
font-size:12px;font-weight:600;color:var(--amt);text-decoration:none}
.pn-popfoot:hover{text-decoration:underline}
@supports (position-area:block-end){
  [popover].pn-pop{inset:auto;margin:6px 0 0;position-area:block-end span-inline-end;
  position-try-fallbacks:flip-block,flip-inline,flip-block flip-inline}
}

@media (prefers-reduced-motion:reduce){
  .pn-kpi,.pn-gseg,.pn-col rect,.pn-split i{transition:none}
}
"""

PAGE_JS = r"""
(() => {
  // the toggletips (interestfor / popover="hint"): their ARIA, and a plain popover where hint is not known (Safari)
  const owned = new Set();
  for (const trig of document.querySelectorAll('[interestfor]')) {
    const target = document.getElementById(trig.getAttribute('interestfor'));
    if (!target || target.getAttribute('popover') !== 'hint') continue;
    if (!target.hasAttribute('role')) target.setAttribute('role', 'tooltip');
    if (trig.hasAttribute('popovertarget') && !trig.hasAttribute('aria-expanded')) {
      trig.setAttribute('aria-expanded', target.matches(':popover-open') ? 'true' : 'false');
      owned.add(trig);
    }
  }
  document.addEventListener('toggle', (ev) => {
    const id = ev.target && ev.target.id;
    if (!id) return;
    const isOpen = ev.newState === 'open' ? 'true' : 'false';
    for (const trig of owned) {
      if (trig.getAttribute('interestfor') === id) trig.setAttribute('aria-expanded', isOpen);
    }
    for (const btn of document.querySelectorAll(`[popovertarget="${CSS.escape(id)}"][aria-expanded]`)) {
      btn.setAttribute('aria-expanded', isOpen);
    }
  }, true);
  const probe = document.createElement('div');
  probe.popover = 'hint';
  if (probe.popover !== 'hint') {
    document.addEventListener('beforetoggle', ({ target, newState }) => {
      if (newState === 'open' && target.getAttribute('popover') === 'hint'
          && document.querySelector(`[popovertarget="${CSS.escape(target.id)}"]`)) {
        target.popover = 'auto';
      }
    }, true);
  }
})();
"""


def page(snap: dict, tonight: dict, sites: dict, history: dict, *, newset: dict | None = None,
         aitest: dict | None = None, built: str = "") -> str:
    """總覽 as a whole page: the bar every page has, the state in one line, four figures, then the cards. built: when
    the library in use was built (the bar's right end, as on every page)."""
    usage = nums_of(snap, "serve").get("usage")
    # read again in a few seconds while the steps are being read (as 每晚運行 does after a restart)
    again = AGAIN.replace("{ms}", "5000") if not snap.get("steps") or snap.get("running") else ""
    stamp = built[:16].replace("T", " ")
    meta = f"<span data-tip='正式庫 {e(stamp)} 建好'>庫建於 {e(stamp[5:])}</span>" if stamp else ""
    waiting = "" if sites.get("sites") else (
        "<p class='pn-wait'>網站的統計還在算（檢視台剛重啟時要幾分鐘），文件的兩張圖等它算好再出現。</p>")

    content = (
        f"{header_bar(snap, tonight)}{waiting}"
        f"{hero_kpis(snap, tonight, sites, history, usage, aitest)}"
        f"<div class='pn-bento'>"
        f"{freshness_card(sites)}"
        f"{sources_card(sites)}"
        f"<div class='pn-stack pn-span6'>{quality_card(snap, newset)}{machine_card(snap)}</div>"
        f"{usage_card(snap, usage)}"
        f"{nights_card(tonight, history)}"
        f"{crawler_card(snap, tonight)}"
        f"</div>"
    )
    return (
        "<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        "<title>總覽 · 檢視台</title>"
        f"<style>{themebar.theme_css()}{TIP_CSS}{CSS}</style></head>"
        f"<body><a href='#main-title' class='visually-hidden pn-skip'>跳至主要內容</a>"
        f"{themebar.bar('home', 'ops', meta)}<main id='main'>{content}</main>"
        f"<script>{TIP_JS}{PAGE_JS}{again}</script></body></html>"
    )
