"""總覽 (/), 檢視台's front page, as the owner designed it on 11-23 (research/design_2026-11-23/index.html, 「照这个改」;
the scripts beside it say how each part came about):

  1. the calendar (nknu.ops.calendar): one cell a night under the day it runs, the week first, each opening its night
     on 內容 (/sites?date=<run day>)
  2. 網站訪問量 (nknu.ops.visits) on the left, beside one card of three tabs: 待處理 (nknu.ops.todo: one alert a line
     in the source's own words, the new first, each with its age and 標成已知 with six reasons to pick), 日誌 (every
     line of the night's logs: nknu.ops.logs, read a page at a time through GET /api/logs, logs.api_window, the one
     handler 內容's night log asks too) and 已知 (every mark, kept until taken off, with where its problem stands)
  3. one card: 內容's 文件日期分佈 and 全部網站 (the page of every site in a frame, /sites?embed=1, drawn by 內容) with
     這台電腦 under the donut (machine_block, put there by web.py through overview.fill's embed side; the page puts
     it there itself when the frame came without it)

Gone with the design: the visible title (kept for screen readers), the night's sentence and the 要處理 pill, the three
figures on top (學生搜到的庫, the old 對外查詢 card, 該抓的抓到: moved to 內容), 最近 7 晚 (the calendar has every
night), 全景 with 收錄網站 and AI 實測, 文件年份分佈 and 文件從哪裡來 (the frame). The browser's side (the switches,
the countdown, the live line and the day chart, the tabs, the log viewer, the reasons to pick, the hover cards read
for the eye) is the layer theme/design/home.js and home.css.
"""

from __future__ import annotations

from datetime import datetime

from nknu.ops import calendar, design, visits
from nknu.ops import pipeline as pl
from nknu.ops.charts import ECHARTS
from nknu.ops.look import CSS as LOOK_CSS
from nknu.ops.look import TIP_JS, e
from nknu.ops.pipeline_page import AGAIN
from nknu.ops.sources import night_of_run
from nknu.ops.theme import bar as themebar
from nknu.ops.todo import known_rows, sightings

# What web.py wires for 總覽 (the integration adds them; this module and visits draw):
# - GET /: front() keeps what it reads and passes it whole:
#     todo_res = todo_now(snap, night, changes)
#     panorama.page(snap, {**night, "changes": changes}, data or {}, hist, built=built, todo=todo_res,
#                   more=panorama.gather(store, known, where, {**night, "changes": changes}, snap, todo_res))
#   (aitest is no longer read: AI 實測 left 總覽.)
# - todo_now: one list with the nights' record (todo.night_rows: the night's problems and the WARC backup's row), and
#   the night the rows are of, so a row's anchor leads to its place (todo.link):
#     rows = todo.night_rows(night, snap, [] if reading else handle_rows(snap, changes), where.archive)
#     ... todo.collect(parts, rows, panorama.machine_meters(snap), known.load(), night=night["default"])
#   history.Recorder.keep_problems keeps the same list (DATA's file), or the backup's row never ages and its mark is
#   never seen on 已知:  rows = todo.night_rows(t, snap, tonight_page.handle_rows(snap, changes), self.where.archive, now)
# - GET /api/logs: nknu.ops.logs.api_window (DATA's ROUTES_TO_WIRE; 日誌 here sends its names: date, lv, src, q, n,
#   before). This module has no handler of its own for it.
# - GET /sites?embed=1 (總覽's frame): overview.fill(page, embed_side=panorama.machine_block(flow.snapshot())), the
#   machine's tiles under the donut (home.js puts the same block there itself when the frame came without it).
# - POST /todo/known: back to "/#known" when off=1 (a mark taken off on the 已知 tab), else "/#todo"; the layer opens
#   the tab the address names.
ROUTES_TO_WIRE = [
    {"path": "/api/usage/live", "method": "GET", "handler": "nknu.ops.visits.live_api", "args": "where",
     "returns": "JSON {now, end (epoch second), per_sec (330 counts, oldest first), read, poll_ms}; "
                "Cache-Control: no-store (網站訪問量 · 即時 asks every 3 s)"},
]

TIP_CSS = LOOK_CSS[LOOK_CSS.index(".tip{") :]
LAYERS = ("richtip", "home")  # 內容's hover card read for the eye, then this page's own
LOG_PAGE = 500  # log lines asked for at a time (the design's 「再顯示 500 行」)
# 標成已知's reasons to pick (owner 11-23: 「加一些比较笼统通用的选项」): a pick fills the note, which can still be typed
REASONS = ("校外網站，不歸我們", "學校本來就沒放內容", "網站已停用", "要登入才看得到", "照規則不收", "暫時的，過幾天再看")
TODO_HELP = ("待處理：每晚流程停了或出錯、某個網站的數字不對、硬碟或記憶體過了警戒線，一件一行，照來源寫的原文。"
             "「新出現」是前一晚沒有的。清單每 5 分鐘重算，好了就消失。標成已知的移到「已知」，按取消才回來。"
             "日誌：爬取、導出、轉文字、建庫、進庫的每一行紀錄，可以搜尋，按等級和來源篩選。")
WAITING = "網站的統計還在算（檢視台剛重啟時要幾分鐘），文件日期分佈和全部網站等它算好再出現。"
TD_SVG = {"warn": "<path d='M8 3.2v5.6'/><circle cx='8' cy='11.8' r='.5' fill='currentColor'/>",
          "bad": "<path d='M5 5l6 6M11 5l-6 6'/>"}
FIND = ("<label class='lv-find'><svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' "
        "stroke-width='1.6' aria-hidden='true'><circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/></svg>"
        "<input id='lv-q' type='search' placeholder='搜尋紀錄（網址、站名、字詞）' aria-label='搜尋紀錄' autocomplete='off'>"
        "</label>")
MAX_BTN = ("<button type='button' class='lv-max-btn' aria-label='放大紀錄' data-tip='放大看紀錄（Esc 關閉）'>"
           "<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6' "
           "stroke-linecap='round' aria-hidden='true'><path d='M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9'/>"
           "</svg></button>")


def steps(snap: dict) -> dict:
    return {s["key"]: s for s in snap.get("steps") or []}


def nums_of(snap: dict, key: str) -> dict:
    """A step's numbers (pipeline's Step.nums), {} when the step or its numbers are missing."""
    return (steps(snap).get(key) or {}).get("nums") or {}


def machine_meters(snap: dict) -> list[dict]:
    """The machine's drives and memories as meters: {label, tone, pct, detail, sub, tip}; one unread is said so
    (todo.machine_rows makes a 待處理 row of one past its warning line)."""
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


# -------------------------------------------------------------------------------------------- 這台電腦


# the three pictures (research/design_2026-11-23/scripts/machine.py): an outline, what is drawn inside, the band the
# water fills (top, bottom), the view box and the width
SHAPES = {
    "disk": ("M14 10 h92 a8 8 0 0 1 8 8 v44 a8 8 0 0 1 -8 8 h-92 a8 8 0 0 1 -8 -8 v-44 a8 8 0 0 1 8 -8 Z", ""),
    "ram": ("M4 22 h112 v34 h-3 v6 h-48 v-5 h-6 v5 h-55 v-6 h0 Z",
            "".join(f"<rect x='{12 + i * 26}' y='28' width='18' height='20' rx='2' class='mc-chip'/>" for i in range(4))),
    "gpu": ("M8 14 h102 a5 5 0 0 1 5 5 v38 a5 5 0 0 1 -5 5 h-102 Z",
            ("<circle cx='38' cy='38' r='15' class='mc-fan'/><circle cx='84' cy='38' r='15' class='mc-fan'/>"
             "<rect x='4' y='10' width='4' height='58' rx='1' class='mc-bracket'/>")),
}
INSIDE = {"disk": (10, 70), "ram": (22, 56), "gpu": (14, 62)}
VIEW = {"disk": "4 8 112 64", "ram": "2 20 116 44", "gpu": "2 8 115 62"}
WIDTH = {"disk": 54, "ram": 84, "gpu": 76}
WAVE = "M-120 0 q15 -4 30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 V80 H-120 Z"
MC_CSS = """<style id='mc-css'>
.mc-card .pn-chd{display:flex;align-items:center;justify-content:space-between}
.mc-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:6px}
.mc{display:grid;grid-template-columns:84px minmax(0,1fr);align-items:center;gap:12px;padding:7px 12px;border-radius:10px;
background:var(--sunk);border:1px solid var(--line);cursor:help;min-width:0}
.mc[data-tone=warn]{border-color:var(--warn-s)}.mc[data-tone=bad]{border-color:var(--bad-s)}
.mc-svg{height:auto;justify-self:center}
.mc-body{fill:var(--surface)}
.mc-edge{fill:none;stroke:var(--line2);stroke-width:2.5}
.mc-water{transition:transform .8s ease}
.mc-water path{animation:mc-flow 5s linear infinite}
.mc-p path{fill:var(--q4)}.mc-s path{fill:var(--q2);animation-duration:7s}
.mc-o path{fill:var(--line2);animation-duration:6s;animation-direction:reverse}
@keyframes mc-flow{to{transform:translateX(60px)}}
@media (prefers-reduced-motion:reduce){.mc-water path{animation:none}}
.mc-chip,.mc-fan{fill:none;stroke:var(--line2);stroke-width:2}.mc-fan{stroke-dasharray:5 4}.mc-bracket{fill:var(--line2)}
.mc-txt{display:flex;flex-direction:column;gap:1px;min-width:0}
.mc-line{display:flex;align-items:baseline;gap:6px}
.mc-name{font-size:12px;color:var(--ink2);font-weight:650;white-space:nowrap}
.mc-pct{font-size:17px;font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums;line-height:1.15}
.mc-pct small{font-size:11px;color:var(--ink3)}
.mc-legend{display:flex;flex-wrap:wrap;gap:0 8px;font-size:11px;color:var(--ink2);font-variant-numeric:tabular-nums}
.mc-k::before{content:'';display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:4px}
.mc-lp::before{background:var(--q4)}.mc-ls::before{background:var(--q2)}
.mc-det{font-size:11px;color:var(--ink3);font-variant-numeric:tabular-nums;white-space:nowrap}
</style>"""


def shape_of(name: str) -> str:
    return "disk" if "槽" in name else "gpu" if "顯示卡" in name else "ram"


def machine_tiles(snap: dict) -> list[dict]:
    """The machine's parts as pictures: {name, kind, total, parts [(word, GB, class)], tip, tone, read}. What the
    panel can measure is drawn as what it is: the graphics card's whole use (Windows cannot split it per program: the
    design's 本專案); the memory of the Docker machine the containers run in (named so, never 本專案: qdrant, which is
    not ours, runs there too, and the machine's own limit is the whole, not Windows' 127.6 GB, OV-72); a drive one grey
    layer (how much of it is the project's, Strata's or another's is measured on the lab PC by hand, OV-71: a part list
    from the reading, when it gives one, is drawn as it is). Each tip line stays short: 內容's hover card in the frame
    cuts a long one."""
    out = []
    for m in nums_of(snap, "serve").get("machine") or []:
        name = {"顯示卡記憶體": "顯示卡", "D 槽（爬蟲存檔）": "D 槽"}.get(m.get("name") or "", m.get("name") or "")
        kind, used, total = shape_of(name), m.get("used"), m.get("total")
        read = used is not None and bool(total)
        parts = [tuple(p) for p in m.get("parts") or [] if len(p) == 3]
        if not parts and read:
            parts = {"disk": [("用了", float(used), "o")], "ram": [("Docker 虛擬機", float(used), "p")]}.get(
                kind, [("本專案", float(used), "p")])
        if kind == "gpu":
            tip = ("本專案：檢視台、對外服務、共用的檢索模型；Windows 讀不到每個程式各用多少，這裡是全部用量；"
                   "Strata 載入時會拿走約 14 GB")
        elif kind == "ram":
            tip = (f"Docker 虛擬機用了 {used:.1f} GB（它最多能用 {total:.1f} GB，百分比照這個算），裡面的容器都算，不只本專案的\n"
                   "Windows 整台的記憶體檢視台讀不到；Strata 載入時另外再用"
                   if read else "Docker 虛擬機的記憶體讀不到")
            if m.get("swap"):
                tip += f"\n交換區 {m['swap']:.1f} GB"
        elif read and isinstance(m.get("probe"), dict):  # the lab's own measure (pipeline.drive_parts)
            pr = m["probe"]
            tip = (f"本專案：爬蟲原檔 {pr['nknu']:,.0f} GB、Docker 的磁碟 {pr['docker']:,.0f} GB（裡面也有不是我們的 qdrant）\n"
                   f"Strata：測試用的本地模型 {pr['strata']:,.0f} GB\n其他：{m.get('name')}上別的程式和檔案\n"
                   f"{pl.stamp(pr['at'])} 在實驗室電腦上量的，每 30 分鐘量一次")
        elif read and m.get("probe_old"):
            tip = (f"{m.get('name')}：用了 {used:,.0f} GB；本專案、Strata 和其他各占多少要在實驗室電腦上量，\n"
                   f"上次量是 {pl.stamp(m['probe_old'])}，太久沒量了（每 30 分鐘量一次），先不分")
        else:
            tip = (f"{m.get('name')}：用了 {used:,.0f} GB；本專案、Strata 和其他各占多少，檢視台讀不到（要在實驗室電腦上量）"
                   if read else f"{m.get('name')}：讀不到（這一槽沒有掛給檢視台）")
        out.append({"name": name, "kind": kind, "used": used, "total": total, "parts": parts, "tip": tip,
                    "tone": m.get("tone") or "", "read": read})
    return out


def machine_tile(i: int, t: dict) -> str:
    kind, total = t["kind"], t["total"] or 0
    outline, deco = SHAPES[kind]
    top, bot = INSIDE[kind]
    disk = kind == "disk"
    fmt = (lambda g: f"{g:,.0f}") if disk else (lambda g: f"{g:.1f}")
    layers = []
    if t["read"]:
        acc = sum(g for _w, g, _c in t["parts"])
        for _w, g, cls in t["parts"][::-1]:  # the top layer first, so the project's own part is drawn last, at the bottom
            if acc > 0:
                level = bot - (bot - top) * min(acc, total) / total
                layers.append(f"<g class='mc-water mc-{e(cls)}' style='transform:translateY({level:.1f}px)'>"
                              f"<path d='{WAVE}'/></g>")
            acc -= g
        used = t["used"]
        pct = f"{used / total * 100:.0f}"
        own = next((g for w, g, c in t["parts"] if c == "p" and w == "本專案"), None)  # only a part said to be ours
        head = f"{t['name']} · 用了 {pct}%" + (f"，本專案 {own / total * 100:.0f}%" if own is not None else "")
        legend = "".join(f"<span class='mc-k mc-l{e(c)}'>{e(w)} {fmt(g)} GB</span>" for w, g, c in t["parts"]
                         if g > 0 and c != "o")
        rest = f"剩 {fmt(total - used)} / {fmt(total)} GB"
    else:
        pct, head, legend, rest = "—", f"{t['name']} · 讀不到", "", "讀不到"
    tone = f" data-tone='{e(t['tone'])}'" if t["tone"] in ("warn", "bad") else ""
    return (f"<div class='mc' tabindex='0'{tone} data-tip-h='{e(head)}' data-tip='{e(t['tip'])}'>"
            f"<svg viewBox='{VIEW[kind]}' class='mc-svg' style='width:{WIDTH[kind]}px;flex-basis:{WIDTH[kind]}px' "
            f"aria-hidden='true'><defs><clipPath id='mc-c{i}'><path d='{outline}'/></clipPath></defs>"
            f"<path d='{outline}' class='mc-body'/><g clip-path='url(#mc-c{i})'>{''.join(layers)}</g>"
            f"{deco}<path d='{outline}' class='mc-edge'/></svg>"
            f"<div class='mc-txt'><div class='mc-line'><span class='mc-name'>{e(t['name'])}</span>"
            f"<span class='mc-pct'>{pct}<small>%</small></span></div>"
            f"<div class='mc-legend'>{legend}</div><div class='mc-det'>{e(rest)}</div></div></div>")


def machine_block(snap: dict) -> str:
    """這台電腦 as the design puts it under the donut (sitemap.py, machine_block.html): its own styles with it, so the
    frame's page (/sites?embed=1, overview.fill's embed side) draws it as 總覽 does; '' when nothing is read."""
    tiles = machine_tiles(snap)
    if not tiles:
        return ""
    return (f"{MC_CSS}<section class='pn-card mc-card side-mc' aria-label='這台電腦'><div class='pn-chd'><h2>這台電腦</h2>"
            f"</div><div class='mc-grid'>{''.join(machine_tile(i, t) for i, t in enumerate(tiles))}</div></section>")


# -------------------------------------------------------------------------------------------- 待處理 | 日誌 | 已知


def td_mark(sev: str) -> str:
    sev = "bad" if sev == "bad" else "warn"
    word = "出錯" if sev == "bad" else "留意"
    return (f"<span class='td-mark td-{sev}' role='img' aria-label='{word}'><svg viewBox='0 0 16 16' width='12' "
            f"height='12' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' aria-hidden='true'>"
            f"{TD_SVG[sev]}</svg></span>")


def known_form(keys: str) -> str:
    """標成已知 (always shown, owner 11-23: 「这个隐身了」): the six reasons to pick, then the note and 確定. One field
    holds every key of the row, space-joined (web.mark_known reads it so)."""
    chips = "".join(f"<button type='button' aria-pressed='false'>{e(r)}</button>" for r in REASONS)
    return (f"<details class='td-known'><summary>標成已知</summary><form method='post' action='/todo/known'>"
            f"<div class='tk-opts' role='group' aria-label='常見的原因'>{chips}</div>"
            f"<input type='hidden' name='keys' value='{e(keys)}'><input name='note' maxlength='200' "
            f"placeholder='為什麼不用管（可不寫）' aria-label='為什麼不用管'><button>確定</button></form></details>")


def todo_row(x: dict) -> str:
    """One alert, one line, in the words it was raised with; nothing added (owner: 「原样提醒…不要出错 文字要干练」).
    The source's own code and 沒見過 for a code no dictionary knows stay (spec 2026-11-17-panels-v3 八: never read as
    normal)."""
    where = (f"<a href='{e(x['href'])}'>{e(x.get('where'))}</a>" if x.get("href") else f"<b>{e(x.get('where'))}</b>")
    reason = x.get("reason") or ""
    # cut with … on its line, read whole on hover (data-tip-full: the hover card never cuts the source's words)
    issue = f"<span class='td-issue' data-tip='{e(reason)}' data-tip-full>{e(reason)}</span>" if reason else ""
    code = f"<span class='td-code'>{e(x['code'])}</span>" if x.get("code") else ""
    unknown = ("<span class='td-unk' tabindex='0' data-tip='這個代碼面板沒見過：照原樣列出，不當成正常'>沒見過</span>"
               if x.get("unknown") else "")
    tag = x.get("tag") or ""
    if tag == "新出現":
        age = "<span class='td-age td-new'>新出現</span>"
    elif tag:
        age = f"<span class='td-age' tabindex='0' data-tip='{e(x.get('age_tip') or '')}'>{e(tag)}</span>"
    else:
        age = "<span></span>"
    keys = " ".join(it.get("key") or "" for it in x.get("issues") or [] if it.get("key"))
    act = known_form(keys) if keys else "<span></span>"
    return (f"<li data-sev='{e(x.get('sev') or '')}'>{td_mark(x.get('sev') or '')}"
            f"<span class='td-kind'>{e(x.get('kind') or '')}</span>"
            f"<span class='td-what'>{where}{issue}{code}{unknown}</span>{age}{act}</li>")


def marks_of(todo: dict) -> list[dict]:
    """已知's rows from what collect folded (when the page was given no marks: an older web.py): each still listed."""
    return [{"key": " ".join(it.get("key") or "" for it in x.get("issues") or []), "kind": x.get("kind") or "",
             "name": x.get("where") or "", "issue": x.get("reason") or "", "note": x.get("note") or "", "at": "",
             "here": True, "seen": "今晚還在"} for x in todo.get("known") or []]


def known_row(k: dict) -> str:
    note = (f"<span class='kn-note'>{e(k['note'])}</span>" if k.get("note")
            else "<span class='kn-note kn-empty'>沒寫原因</span>")
    issue = f"<span>{e(k['issue'])}</span>" if k.get("issue") else ""
    when = f"<span>{e(k['at'])} 標的</span>" if k.get("at") else ""
    off = (f"<form class='kn-offf' method='post' action='/todo/known'><input type='hidden' name='keys' "
           f"value='{e(k['key'])}'><input type='hidden' name='off' value='1'>"
           f"<button class='kn-off'>取消已知</button></form>")
    return (f"<li class='kn-row'{'' if k.get('here') else ' data-gone'}><span class='kn-kind'>{e(k.get('kind'))}</span>"
            f"<span class='kn-what'><b>{e(k.get('name'))}</b>{issue}{note}</span>"
            f"<span class='kn-when'>{when}<span class='kn-seen'>{e(k.get('seen'))}</span></span>{off}</li>")


def known_pane(marks: list[dict]) -> str:
    """已知 (owner 11-23: every mark, kept until 取消, no automatic clearing): 還在出現 first, then 沒再出現 greyed."""
    if not marks:
        return "<p class='td-none kn-none'>沒有標成已知的</p>"
    here = [k for k in marks if k.get("here")]
    gone = [k for k in marks if not k.get("here")]
    out = []
    for title, rows in (("還在出現", here), ("沒再出現", gone)):
        if rows:
            out.append(f"<h3 class='kn-h'>{title} <span>{len(rows)}</span></h3>"
                       f"<ul class='kn-list'>{''.join(known_row(k) for k in rows)}</ul>")
    return f"<div class='kn'>{''.join(out)}</div>"


LOG_PANE = ("<div class='lv' id='lv' data-src='/api/logs' data-page='" + str(LOG_PAGE) + "'>"
            "<div class='lv-bar'><div class='lv-chips' id='lv-lv' role='group' aria-label='等級'></div>"
            "<div class='lv-chips' id='lv-src' role='group' aria-label='來源'></div></div>"
            "<div class='lv-hist' id='lv-hist'></div><div class='lv-meta' id='lv-meta'></div>"
            "<div class='lv-list' id='lv-list' role='log' tabindex='0' aria-label='日誌'></div>"
            f"<button type='button' class='lv-more' id='lv-more' hidden>再顯示 {LOG_PAGE} 行</button></div>")


def todo_card(todo: dict | None, reading: bool, known: list[dict] | None = None) -> str:
    """待處理 | 日誌 | 已知 (section#todo): the tabs in the head (待處理 with its count, 已知 with every mark's), the log's
    search and 放大 there too (shown on 日誌), a ?; under it the three panes, each scrolling inside the card. reading:
    the steps are being read (just restarted): nothing is called normal yet. known: 已知's rows (todo.known_rows)."""
    todo = todo or {}
    open_ = todo.get("open") or []
    marks = known if known is not None else marks_of(todo)
    count = f" <b>{len(open_)}</b>" if open_ and not reading else ""
    tabs = (
        "<div class='cal-switch lg-tabs' role='tablist' aria-label='待處理、日誌、已知'>"
        f"<button type='button' role='tab' id='tab-todo' data-p='todo' aria-selected='true' aria-controls='pane-todo'>"
        f"待處理{count}</button>"
        "<button type='button' role='tab' id='tab-log' data-p='log' aria-selected='false' aria-controls='pane-log' "
        "tabindex='-1'>日誌</button>"
        "<button type='button' role='tab' id='tab-known' data-p='known' aria-selected='false' aria-controls='pane-known' "
        f"tabindex='-1'>已知 <i class='kn-n'>{len(marks)}</i></button></div>")
    helpb = (f"<button type='button' class='hp' data-hp='td' aria-label='這張卡片在說什麼' data-tip-h='待處理 · 日誌 · 已知' "
             f"data-tip='{e(TODO_HELP)}' data-tip-full>?</button>")
    if reading:
        body = "<p class='td-none td-wait'>正在讀各步驟的紀錄</p>"
    elif open_:
        body = f"<ul class='td'>{''.join(todo_row(x) for x in open_)}</ul>"
    else:
        body = "<p class='td-none'>沒有要處理的</p>"
    return (f"<section class='pn-card' id='todo' data-tab='todo'>"
            f"<div class='pn-chd lg-head'>{tabs}{FIND}{MAX_BTN}{helpb}</div>"
            f"<div class='lg-pane' data-p='todo' id='pane-todo' role='tabpanel' aria-labelledby='tab-todo'>{body}</div>"
            f"<div class='lg-pane' data-p='known' id='pane-known' role='tabpanel' aria-labelledby='tab-known' hidden>"
            f"{known_pane(marks)}</div>"
            f"<div class='lg-pane' data-p='log' id='pane-log' role='tabpanel' aria-labelledby='tab-log' hidden>"
            f"{LOG_PANE}</div></section>")


# -------------------------------------------------------------------------------------------- the page


def now_of(tonight: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(tonight["now"]) if tonight.get("now") else None
    except ValueError:
        return None


def gather(store, known, where, tonight: dict, snap: dict, todo: dict | None, now: datetime | None = None) -> dict:
    """What 總覽 reads besides the reading and 待處理 (web.py's front passes it as page(more=…)): {'calendar' (its
    month and week of nights: the nights' record and tonight's readings), 'visits' (this month a day: the record's
    days, today read now, the week's calls), 'known' (every mark with where its problem stands: the store's
    sightings)}. store: history.Store; known: todo.Known; where: the pipeline's Where (its public_log)."""
    now = now or now_of(tonight)
    today = calendar.today_of(tonight, now)
    first, last = calendar.span_for(today)
    enabled = store is not None and getattr(store, "enabled", False)
    records = store.nights(first, last) if enabled else []
    # the library's crawl time when no night of the month or the week swapped (OV-18)
    reader = getattr(store, "last_swapped", None) if enabled else None
    last_swap = reader(night_of_run(today)) if callable(reader) else None
    usage = nums_of(snap, "serve").get("usage") or {}
    marks = known.marks() if known is not None else []
    seen = sightings(store if enabled else None, [m["key"] for m in marks])
    return {"calendar": calendar.build(tonight, records, now=now, last_swap=last_swap),
            "visits": visits.month(store, getattr(where, "public_log", None), now=now, recent=usage.get("days")),
            "known": known_rows(marks, todo, seen, tonight.get("default") or "")}


def sites_part(sites: dict, snap: dict) -> str:
    """文件日期分佈 + 全部網站 as one card (內容's page in a frame) with 這台電腦 under the donut; while the sites'
    numbers are still being counted, a line saying so and the machine's tiles alone. Alone, the block is its own card
    with its title 這台電腦 (no donut beside it to say what it is); the wrapper only places it (no card in a card)."""
    block = machine_block(snap)
    if not sites.get("sites"):
        alone = f"<div class='mc-alone'>{block}</div>" if block else ""
        return f"<p class='pn-wait'>{WAITING}</p>{alone}"
    return ("<iframe class='sx-frame' id='sxFrame' src='/sites?embed=1' title='文件日期分佈' loading='lazy'></iframe>"
            f"<template id='mcBlock'>{block}</template><div class='mc-alone' id='mcHere' hidden></div>")


def page(snap: dict, tonight: dict, sites: dict, history: dict | None = None, *, aitest: dict | None = None,
         built: str = "", todo: dict | None = None, more: dict | None = None) -> str:
    """總覽 as a whole page. tonight: nightly.tonight with the last swap's comparison as 'changes'; sites: 內容's data
    (only whether it is ready: the frame draws it); history: home_history (the calendar's nights when more has none);
    built: when the library in use was built (the bar's right end); todo: todo.collect; more: gather's answer.
    aitest is no longer drawn (AI 實測 left 總覽 with 全景)."""
    more = more or {}
    reading = not snap.get("steps")
    model = more.get("calendar") or calendar.build(tonight, (history or {}).get("nights") or [])
    usage = nums_of(snap, "serve").get("usage") or {}
    vis = more.get("visits") or visits.month(None, None, now=now_of(tonight), recent=usage.get("days"))
    # read again in a few seconds while the steps are being read; after that the layer asks for the night's
    # signature and reloads only when it changed and nothing is being done on the page (OV-82)
    again = f"<script>{AGAIN.replace('{ms}', '5000')}</script>" if reading else ""
    stamp = built[:16].replace("T", " ")
    meta = f"<span data-tip='正式庫 {e(stamp)} 建好'>庫建於 {e(stamp[5:])}</span>" if stamp else ""
    content = (
        "<h1 id='main-title' tabindex='-1' class='visually-hidden'>總覽</h1>"
        f"{calendar.card_html(model)}"
        f"<div class='mid-row'>{visits.card_html(vis)}{todo_card(todo, reading, more.get('known'))}</div>"
        f"{sites_part(sites, snap)}"
    )
    return (
        "<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        "<title>總覽 · 檢視台</title>"
        f"<style>{themebar.theme_css()}{TIP_CSS}{CSS}</style></head>"
        f"<body><a href='#main-title' class='visually-hidden pn-skip'>跳至主要內容</a>"
        f"{themebar.bar('home', 'ops', meta)}<main id='main' data-sig='{e(tonight.get('sig') or '')}'>{content}</main>"
        f"<script>{TIP_JS}</script>{again}"
        f"<script id='echarts-lib' src='{ECHARTS}' async crossorigin='anonymous' referrerpolicy='no-referrer'></script>"
        f"{design.layer(*LAYERS)}</body></html>"
    )


# -------------------------------------------------------------------------------------------- styles

# The card shell, all that is left of 總覽's styles before the 11-23 design (its first style block in the copy): the
# page's own frame (the skip link, main, the waiting line), a card and its head, the tile 網站訪問量 sits in. 內容's
# band and the night view use the same shell (content_band reads CSS). The 11-23 parts are theme/design/home.css;
# the parts of the removed cards (the Gantt, the KPI row, the meters, the old list, the popovers) went with them.
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

/* a tile (網站訪問量) */
.pn-kpi{display:flex;flex-direction:column;justify-content:space-between;gap:8px;padding:16px 18px;
border:1px solid var(--line);border-radius:16px;background:var(--surface);box-shadow:var(--shadow);
text-decoration:none;color:inherit}
.pn-khead{font-size:12px;font-weight:600;color:var(--ink3);letter-spacing:.01em}

/* Bento grid (內容's band) */
.pn-bento{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}

/* Card shell */
.pn-card{container-type:inline-size;min-width:0;padding:18px 20px;border:1px solid var(--line);border-radius:16px;
background:var(--surface);box-shadow:var(--shadow);display:flex;flex-direction:column}
.pn-chd{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;margin:0 0 14px}
.pn-chd h2{margin:0;font-size:16px;font-weight:700;letter-spacing:-.01em}
.pn-empty{margin:4px 0;font-size:13px;color:var(--ink3)}
"""
