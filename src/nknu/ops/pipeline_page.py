"""The 流水線 page (nknu.ops.pipeline's snapshot drawn), made to be read top down in three looks:

1. one card on top: the first line (whether every step is normal, and if not which and how many sites have something to
   look at), then the flow map: the seven steps in a row, each a status dot (colour with an icon, never colour alone),
   its name and the one number it put out. The flow map is the page's overview; a click on a step opens its row below
   and scrolls to it;
2. a row per step, all in one list: its state once (a small mark with an icon and a word), its name, a violet bar of
   what came out of what went in (one unit; violet for an amount, as on 資料總覽: owner 11-15 「其他地方普遍用紫色來表達
   多和少」), the number that came out and when it last ran. A normal step stays that one row; a step to look at
   (留意, 出錯) opens by itself on what is wrong, who fixes it and its largest reasons. The rest of a step (what it does,
   its notes, the full nouns of the numbers) floats beside the pointer (data-tip), nothing is printed twice;
3. every site with one number per cell, problems first, filtered as one types and sorted by a click on a header; a
   cell's second line (共 … 個網址, 讀不出字 …) is in its floating card, a site's name opens its own page (site_body):
   its steps as one line of numbers, then each step's reasons with examples that open 網頁核查 on that page.

Redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md): before it every step was a big card that said
its state three times (the dot, a coloured left border, a pill) and printed 「細節（滑過去看）」 seven times; owner 11-16:
the page is to 「帮助我监控和看见整个项目每个关键节点的输入输出 加工过程…出错的话是哪里错了 能帮助我精准定位」, and of
the panels 「頁面廢話太多了」. The words are Traditional Chinese, Taiwan usage (owner 11-15: 「可视化dashboard全部换繁体
字」); the layout, the colours' jobs and the names are 【Claude】 (owner 11-16: 「全部给你决定 只要让我看到最后的东西是好
用的就行」)."""

from __future__ import annotations

import re
from urllib.parse import quote

from nknu.ops.look import e, icon, s2t
from nknu.ops.pipeline import BAD, CONTENT, FIXERS, NONE, OK, STATE_WORDS, WARN, stamp

PILL = {OK: "good", WARN: "warn", BAD: "bad", NONE: "none"}
STATE_ICON = {OK: "ok", WARN: "warn", BAD: "no", NONE: "dash"}  # look's drawings: the state is never colour alone
SHOWN_REASONS = 4  # reasons an open step shows; the rest are in the floating card 【Claude】
OPEN = (WARN, BAD)  # the states whose row opens by itself (redesign 2026-11-17) 【Claude】
# a small "i" in a ring: the mark the notes of a step float from (look has no such drawing; currentColor only)
INFO = ("<svg class='ic' viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.5' "
        "stroke-linecap='round' aria-hidden='true'><circle cx='8' cy='8' r='6'/><path d='M8 7.2v3.6'/>"
        "<circle cx='8' cy='5' r='.5' fill='currentColor'/></svg>")

CSS = """
.pl-top{padding:14px 18px 6px}
.pl-pipe-head{display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap}
.pl-pipe-head .pl-big{font-size:16px;font-weight:700}
.pl-pipe-head .pl-when{margin-left:auto;color:var(--ink3);font-size:12px}
.pl-flowmap{display:flex;align-items:flex-start;margin:10px -8px 0;padding:8px 0 4px;overflow-x:auto}
.pl-flowmap .pl-node{flex:1 1 0;min-width:96px;display:flex;flex-direction:column;align-items:center;gap:2px;
padding:8px 4px 10px;border-radius:12px;text-decoration:none;color:inherit;text-align:center}
.pl-flowmap .pl-node:hover{background:var(--hover)}
.pl-flowmap .pl-dot{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--hover);
color:var(--ink2)}
.pl-flowmap .pl-node[data-state=ok] .pl-dot{background:var(--good);color:var(--on-good)}
.pl-flowmap .pl-node[data-state=warn] .pl-dot{background:var(--warn);color:var(--on-warn)}
.pl-flowmap .pl-node[data-state=bad] .pl-dot{background:var(--bad);color:var(--on-bad)}
.pl-flowmap .pl-nm{font-weight:650;font-size:14px;margin-top:6px}
.pl-flowmap .pl-fig{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.3}
.pl-flowmap .pl-unit{font-size:12px;color:var(--ink3)}
.pl-flowmap .pl-wire{flex:0 1 44px;min-width:12px;height:2px;background:var(--line2);margin-top:27px}
@media (max-width:860px){.pl-flowmap{flex-wrap:wrap;justify-content:center;margin:10px 0 0}
.pl-flowmap .pl-wire{display:none}.pl-flowmap .pl-node{flex:0 0 33%;min-width:0}
.pl-flowmap .pl-fig{font-size:16px}}

.pl-chain{list-style:none;margin:16px 0 0;padding:0;overflow:hidden}
.pl-chain>li+li{border-top:1px solid var(--line)}
.pl-stage{scroll-margin-top:12px}
.pl-stage>summary{display:grid;grid-template-columns:96px 104px minmax(0,1fr) minmax(150px,240px) 84px 46px;
grid-template-areas:"mark name flow out when aux";align-items:center;gap:6px 16px;padding:11px 18px;cursor:pointer;
list-style:none}
.pl-stage>summary::-webkit-details-marker{display:none}
.pl-stage>summary:hover{background:var(--hover)}
.pl-stage[open]>summary{background:var(--sunk)}
.pl-stage.pl-hit>summary{animation:pl-hit 1.6s ease-out}
@keyframes pl-hit{from{box-shadow:inset 0 0 0 2px var(--act)}to{box-shadow:inset 0 0 0 2px transparent}}
@media (prefers-reduced-motion:reduce){.pl-stage.pl-hit>summary{animation:none}}
.pl-mark{grid-area:mark;display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:650;color:var(--ink2);
white-space:nowrap}
.pl-mark .ic{width:18px;height:18px;padding:2px;border-radius:50%;background:var(--hover);color:var(--ink2)}
.pl-mark[data-state=ok]{color:var(--good-ink)}.pl-mark[data-state=ok] .ic{background:var(--good);color:var(--on-good)}
.pl-mark[data-state=warn]{color:var(--warn-ink)}.pl-mark[data-state=warn] .ic{background:var(--warn);color:var(--on-warn)}
.pl-mark[data-state=bad]{color:var(--bad-ink)}.pl-mark[data-state=bad] .ic{background:var(--bad);color:var(--on-bad)}
.pl-stage h3{grid-area:name;font-size:16px;white-space:nowrap}
.pl-flow{grid-area:flow;display:flex;align-items:center;gap:10px;min-width:0;font-size:13px;color:var(--ink2)}
.pl-flow .pl-in{flex:none;min-width:84px;font-variant-numeric:tabular-nums;white-space:nowrap}
.pl-track{flex:1 1 auto;min-width:40px;height:10px;border-radius:5px;background:var(--q0);overflow:hidden}
.pl-track i{display:block;height:100%;border-radius:5px;background:var(--amt)}
.pl-out{grid-area:out;font-size:13px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pl-out b{color:var(--ink);font-size:14px;font-variant-numeric:tabular-nums}
.pl-at{grid-area:when;font-size:12px;color:var(--ink3);font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.pl-aux{grid-area:aux;display:flex;align-items:center;justify-content:flex-end;gap:6px;color:var(--ink3)}
.pl-aux .pl-i{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;cursor:default}
.pl-aux .pl-i:hover{background:var(--hover);color:var(--ink)}
.pl-aux .pl-chev{transition:transform .15s}.pl-stage[open] .pl-chev{transform:rotate(90deg)}
@media (prefers-reduced-motion:reduce){.pl-aux .pl-chev{transition:none}}
.pl-body{padding:4px 18px 16px calc(18px + 96px + 16px);display:flex;flex-direction:column;gap:8px;
background:var(--sunk)}
.pl-say{margin:0;font-size:14px}
.pl-act{display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;font-size:13px}
.pl-act .pl-fix{font-weight:650}
.pl-act a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2)}
.pl-flowtext{font-size:13px;color:var(--ink2)}.pl-flowtext b{color:var(--ink);font-variant-numeric:tabular-nums}
.pl-lost{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px}
.pl-lost .pl-lab{color:var(--ink3);font-size:12px;margin-right:2px}
.pl-lost .tag{white-space:normal}.pl-lost .tag b{font-variant-numeric:tabular-nums;color:var(--ink);white-space:nowrap}
.pl-lost .pl-rest{cursor:default;border:1px dashed var(--line2);background:none}
@media (max-width:860px){
.pl-stage>summary{grid-template-columns:auto minmax(0,1fr) auto;grid-template-areas:"name name aux" "flow flow flow"
"mark out when";padding:11px 14px}
.pl-body{padding:4px 14px 14px}}

.sec .pl-all{margin-left:auto;font-size:13px;font-weight:600;color:var(--ink);text-decoration:none;padding:4px 10px;
border:1px solid var(--line2);border-radius:8px;background:var(--surface)}.sec .pl-all:hover{background:var(--hover)}
table.pl-sites{width:100%;border-collapse:collapse;font-size:13px}
table.pl-sites th{text-align:right;font-weight:600;color:var(--ink2);padding:6px 8px;border-bottom:1px solid var(--line);
white-space:nowrap;background:var(--surface);cursor:pointer;user-select:none}
table.pl-sites th:hover{color:var(--ink)}
table.pl-sites th[aria-sort]::after{content:" ▾";color:var(--ink3)}table.pl-sites th[aria-sort=ascending]::after{content:" ▴"}
table.pl-sites th:first-child,table.pl-sites td:first-child{text-align:left}
table.pl-sites td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;
white-space:nowrap}
table.pl-sites td:first-child{white-space:normal;min-width:160px}
table.pl-sites td:first-child a{text-decoration:none;font-weight:600}
table.pl-sites td:first-child a:hover{text-decoration:underline}
table.pl-sites tr:hover td{background:var(--hover)}
table.pl-sites td .pl-m{display:inline-grid;place-items:center;width:15px;height:15px;margin-right:5px;border-radius:50%;
vertical-align:-2px}
table.pl-sites td .pl-m .ic{width:11px;height:11px}
table.pl-sites td .pl-m.warn{background:var(--warn);color:var(--on-warn)}
table.pl-sites td .pl-m.bad{background:var(--bad);color:var(--on-bad)}
table.pl-sites td.pl-why{text-align:left;white-space:normal;color:var(--ink2);font-size:12px;min-width:200px}
table.pl-sites th.pl-why{text-align:left;cursor:default}
.pl-scroll{overflow-x:auto}
""" + """
/* 今晚 (redesign 2026-11-17, spec 2026-11-17-panels-structure-design.md 三.2-三.4): the first sentence, the night's
   five parts in a row, the last seven nights; then 昨晚變了什麼. State colours only on the small marks (an icon in
   each), amounts violet, what is picked ink */
.tn{padding:14px 18px 16px;margin:0 0 16px}
.tn-say{display:flex;align-items:center;gap:10px;font-size:16px;font-weight:700;line-height:1.4}
.tn-m{flex:none;display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--hover);
color:var(--ink2)}
.tn-say[data-state=ok] .tn-m{background:var(--good);color:var(--on-good)}
.tn-say[data-state=warn] .tn-m{background:var(--warn);color:var(--on-warn)}
.tn-say[data-state=bad] .tn-m{background:var(--bad);color:var(--on-bad)}
.tn-say[data-state=run] .tn-m{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 2px var(--ink)}
.tn-bar{display:flex;align-items:center;flex-wrap:wrap;gap:8px 16px;margin:14px 0 2px}
.tn-h{font-size:14px;font-weight:650;color:var(--ink2)}.tn-h .faint{font-weight:400;margin-left:6px}
.tn-nights{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
.tn-n{display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 5px;border-radius:9px;
text-decoration:none;color:var(--ink2);font-size:11px;font-variant-numeric:tabular-nums;min-width:40px}
.tn-n:hover{background:var(--hover)}
.tn-n[aria-current]{box-shadow:inset 0 0 0 1.5px var(--act);color:var(--ink);font-weight:650}
.tn-n.off{opacity:.55;pointer-events:none}
.tn-dot{display:grid;place-items:center;width:22px;height:22px;border-radius:7px;background:var(--hover);
color:var(--ink2)}
[data-state=ok]>.tn-dot{background:var(--good);color:var(--on-good)}
[data-state=warn]>.tn-dot{background:var(--warn);color:var(--on-warn)}
[data-state=bad]>.tn-dot,[data-state=missed]>.tn-dot{background:var(--bad);color:var(--on-bad)}
[data-state=run]>.tn-dot{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--ink)}
.tn-line{list-style:none;margin:12px 0 0;padding:0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0}
.tn-cell{position:relative;display:flex;gap:10px;align-items:flex-start;padding:10px 12px 10px 0;min-width:0}
.tn-cell+.tn-cell{padding-left:12px}
.tn-cell+.tn-cell::before{content:"";position:absolute;left:-10px;top:24px;width:16px;height:2px;
background:var(--line2)}
.tn-cell .tn-dot{width:30px;height:30px;border-radius:50%;flex:none}
.tn-cell[data-state=done][data-sev=ok]>.tn-dot{background:var(--good);color:var(--on-good)}
.tn-cell[data-state=done][data-sev=warn]>.tn-dot,.tn-cell[data-state=stop][data-sev=warn]>.tn-dot{background:var(--warn);
color:var(--on-warn)}
.tn-cell[data-state=stop][data-sev=bad]>.tn-dot{background:var(--bad);color:var(--on-bad)}
.tn-cell[data-state=run]>.tn-dot{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 2px var(--ink)}
.tn-body{min-width:0;display:flex;flex-direction:column;gap:2px}
.tn-nm{display:flex;align-items:baseline;gap:8px;white-space:nowrap}
.tn-nm b{font-size:14px}
.tn-word{font-size:12px;font-weight:650;color:var(--ink3)}
.tn-cell[data-state=done][data-sev=ok] .tn-word{color:var(--good-ink)}
.tn-cell[data-sev=warn] .tn-word{color:var(--warn-ink)}
.tn-cell[data-state=stop][data-sev=bad] .tn-word{color:var(--bad-ink)}
.tn-cell[data-state=run] .tn-word{color:var(--ink)}
.tn-cell[data-state=run][data-sev=warn]>.tn-dot{background:var(--warn);color:var(--on-warn);box-shadow:none}
.tn-cell[data-state=run][data-sev=warn] .tn-word{color:var(--warn-ink)}
.tn-sayc{font-size:13px;color:var(--ink);overflow-wrap:anywhere}
.tn-when{font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.tn-fix{font-size:12px;font-weight:650;color:var(--ink)}
@media (max-width:860px){.tn-line{grid-template-columns:minmax(0,1fr)}
.tn-cell,.tn-cell+.tn-cell{padding:8px 0}
.tn-cell+.tn-cell::before{left:14px;top:-6px;width:2px;height:12px}
.tn-nights{margin-left:0;width:100%;justify-content:space-between;flex-wrap:nowrap;gap:0}
.tn-n{min-width:0;padding:3px 2px}}
@media (prefers-reduced-motion:no-preference){[data-state=run]>.tn-dot .ic{animation:tn-turn 2.4s linear infinite}}
@keyframes tn-turn{to{transform:rotate(360deg)}}

.tn-chg{margin:0 0 22px}
.tn-chg .tn-tot{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 28px}
.tn-fig{display:flex;flex-direction:column}
.tn-fig span{font-size:12px;color:var(--ink2)}
.tn-fig b{font-size:26px;font-weight:700;color:var(--amt);font-variant-numeric:tabular-nums;line-height:1.15}
.tn-crawl{margin-left:auto;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;font-size:12px;color:var(--ink2)}
.tn-crawl b{color:var(--amt);font-size:13px;font-variant-numeric:tabular-nums}
.tn-flag{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:650;white-space:nowrap}
.tn-flag .ic{width:16px;height:16px;padding:2px;border-radius:50%}
.tn-flag.bad{color:var(--bad-ink)}.tn-flag.bad .ic{background:var(--bad);color:var(--on-bad)}
.tn-flag.warn{color:var(--warn-ink)}.tn-flag.warn .ic{background:var(--warn);color:var(--on-warn)}
.tn-unx{margin:14px 0 0;padding:10px 12px;border-radius:10px;background:var(--bad-bg)}
.tn-unx>.tn-row{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;font-size:13px}
.tn-unx>.tn-row b{font-variant-numeric:tabular-nums}
.tn-docs{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:2px;font-size:13px}
.tn-docs li{display:flex;align-items:baseline;gap:8px;min-width:0}
.tn-docs a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);overflow:hidden;
text-overflow:ellipsis;white-space:nowrap;min-width:0}
.tn-docs .tn-of{flex:none;color:var(--ink2);font-size:12px;text-decoration:none}
.tn-docs a.tn-of:hover{color:var(--ink);text-decoration:underline}
.tn-site>summary .tn-sn a{color:inherit;text-decoration:none}.tn-site>summary .tn-sn a:hover{text-decoration:underline}
.tn-one .tn-why{display:flex;flex-wrap:wrap;gap:4px;margin:12px 0 0}.tn-one .tn-why .tag b{color:var(--ink)}
.tn-one .tn-in{padding:12px 0 0}
.tn-docs .tag{flex:none}
.tn-more{font-size:12px;color:var(--ink3)}
.tn-sites{margin:14px 0 0;border-top:1px solid var(--line)}
.tn-sites .tn-head,.tn-site>summary{display:grid;grid-template-columns:minmax(140px,1.2fr) 64px 64px 64px minmax(0,2fr) 18px;
gap:4px 12px;align-items:center;padding:7px 4px}
.tn-sites .tn-head{font-size:12px;color:var(--ink2);font-weight:600;border-bottom:1px solid var(--line)}
.tn-sites .tn-head span:nth-child(n+2):nth-child(-n+4),.tn-site>summary .num{text-align:right}
.tn-site{border-bottom:1px solid var(--line)}
.tn-site>summary{cursor:pointer;list-style:none;font-size:13px}
.tn-site>summary::-webkit-details-marker{display:none}
.tn-site>summary:hover{background:var(--hover)}
.tn-site>summary .tn-sn{font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tn-site>summary .num.zero{color:var(--ink3)}
.tn-site .tn-why{display:flex;flex-wrap:wrap;gap:4px}
.tn-site .tn-why .tag b{color:var(--ink);font-variant-numeric:tabular-nums}
.tag.tn-r-unknown{color:var(--bad-ink);background:var(--bad-bg)}.tag.tn-r-look{color:var(--warn-ink);background:var(--warn-bg)}
.tag.tn-r-unknown .ic,.tag.tn-r-look .ic{width:13px;height:13px;padding:1px;border-radius:50%}
.tag.tn-r-unknown .ic{background:var(--bad);color:var(--on-bad)}.tag.tn-r-look .ic{background:var(--warn);color:var(--on-warn)}
.tn-site .pl-chev{color:var(--ink3);transition:transform .15s}.tn-site[open] .pl-chev{transform:rotate(90deg)}
@media (prefers-reduced-motion:reduce){.tn-site .pl-chev{transition:none}}
.tn-in{padding:4px 4px 14px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px 24px;
align-items:start}
.tn-rest>summary{cursor:pointer;padding:9px 4px;font-size:13px;color:var(--ink2)}
.tn-rest>summary:hover{color:var(--ink)}
.tn-in h4{margin:0;font-size:12px;font-weight:650;color:var(--ink2);display:flex;align-items:center;gap:6px}
@media (max-width:860px){.tn-sites .tn-head{display:none}
.tn-site>summary{grid-template-columns:minmax(0,1fr) auto auto auto 14px;grid-template-areas:none}
.tn-site>summary .tn-why{grid-column:1/-1}.tn-site>summary .pl-chev{grid-row:1;grid-column:5}
.tn-site>summary .num::before{content:attr(data-h) " ";color:var(--ink3);font-size:11px}
.tn-crawl{margin-left:0}}
"""


def n(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def pill(state: str, words: str = "") -> str:
    return f"<span class='pill {PILL.get(state, 'none')}'>{e(words or STATE_WORDS.get(state, state))}</span>"


def link(label: str, href: str) -> str:
    out = " target='_blank' rel='noopener'" if href.startswith("http") else ""
    return f"<a href='{e(href)}'{out}>{e(label)} →</a>"


def short(noun: str) -> str:
    """A noun without its trailing bracket (「個小段（搜尋時比對的單位）」 → 「個小段」): the row shows the short one, the
    floating card the whole."""
    return re.sub(r"（[^（）]*）$", "", noun or "").strip()


def flow_map(steps: list[dict]) -> str:
    """The seven steps in a row: a status dot (icon in it), the name, the one number the step put out; each a link to
    its row (PIPE_JS opens the row and scrolls to it; without the script the link still jumps there)."""
    nodes = []
    for step in steps:
        state = step.get("state", NONE)
        figure = step.get("figure") or STATE_WORDS.get(state, "")
        unit = step.get("unit") or ""
        nodes.append(
            f"<a class='pl-node' href='#step-{e(step.get('key'))}' data-state='{e(state)}' "
            f"data-tip-h='{e(step.get('name'))}：{e(STATE_WORDS.get(state, state))}' data-tip='{e(step.get('say'))}'>"
            f"<span class='pl-dot'>{icon(STATE_ICON.get(state, 'dash'), 20)}</span>"
            f"<span class='pl-nm'>{e(step.get('name'))}</span><span class='pl-fig'>{e(figure)}</span>"
            f"<span class='pl-unit'>{e(unit)}</span></a>")
    return "<nav class='pl-flowmap' aria-label='流水線的每一步'>" + "<span class='pl-wire'></span>".join(nodes) + "</nav>"


def flow_tip(step: dict) -> str:
    """Every number that went in and came out with its whole noun, for the floating card of a step's row."""
    lines = [f"進 {c:,} {noun}" for noun, c in step.get("into") or []]
    lines += [f"出 {c:,} {noun}" for noun, c in step.get("out") or []]
    bar = step.get("bar") or []
    if len(bar) == 2 and bar[1] and step.get("share", True):
        lines.append(f"{bar[0] / bar[1]:.0%} 走到了下一步")
    return "\n".join(lines)


def flow_html(step: dict) -> str:
    """The row's middle: how many went in and a violet bar of what came out of it when both are one unit
    (step['bar']); without a bar only what went in."""
    into, bar = step.get("into") or [], step.get("bar") or []
    tip = flow_tip(step)
    went_in = f"<span class='pl-in'>進 {n(into[0][1])}</span>" if into else ""
    track = ""
    if len(bar) == 2 and bar[1]:
        share = max(0.0, min(1.0, bar[0] / bar[1]))
        track = f"<span class='pl-track'><i style='width:{share * 100:.1f}%'></i></span>"
    return f"<span class='pl-flow' data-tip='{e(tip)}'>{went_in}{track}</span>"


def out_html(step: dict) -> str:
    """The row's number that came out (the first of step['out']) with its short noun and, when the bar is one unit,
    its share; a step that put out nothing countable shows its figure (暫停中 …)."""
    out, bar = step.get("out") or [], step.get("bar") or []
    tip = flow_tip(step)
    if out:
        noun, count = out[0]
        share = ""
        if len(bar) == 2 and bar[1] and step.get("share", True):
            share = f" <span class='faint'>{bar[0] / bar[1]:.0%}</span>"
        return f"<span class='pl-out' data-tip='{e(tip)}'><b>{n(count)}</b> {e(short(noun))}{share}</span>"
    figure = step.get("figure") or STATE_WORDS.get(step.get("state", NONE), "")
    return f"<span class='pl-out' data-tip='{e(tip)}'><b>{e(figure)}</b></span>"


def lost_html(step: dict) -> str:
    """The largest reasons of what did not come out (SHOWN_REASONS), the rest in one chip's floating card."""
    lost = [(why, c) for why, c in step.get("lost") or [] if c]
    if not lost:
        return ""
    shown = "".join(f"<span class='tag'>{e(why)} <b>{n(c)}</b></span>" for why, c in lost[:SHOWN_REASONS])
    rest = lost[SHOWN_REASONS:]
    more = ""
    if rest:
        tip = "\n".join(f"{why}：{c:,}" for why, c in rest)
        more = f"<span class='tag pl-rest' data-tip='{e(tip)}'>還有 {len(rest)} 種</span>"
    label = step.get("lost_label") or "沒走到下一步的"
    return f"<div class='pl-lost'><span class='pl-lab'>{e(label)}</span>{shown}{more}</div>"


def stage_html(step: dict, panels: dict[str, str]) -> str:
    """One step's row (step: an asdict'ed nknu.ops.pipeline.Step): a <details> whose summary is the one line (state
    mark, name, in → out, when) and whose body is the verdict, who fixes it, where to look further and the reasons;
    open by itself when the step is to look at (OPEN). look.JS opens a <details> the address's #step-… names."""
    state = step.get("state", NONE)
    when = stamp(step.get("at"))
    took = step.get("took") or ""
    when_tip = (f"上次 {when}" + (f"，花了 {took}" if took else "")) if when else "還沒跑過"
    notes = step.get("notes") or []
    info = (f"<span class='pl-i' tabindex='0' data-tip-h='{e(step.get('name'))}的細節' "
            f"data-tip='{e(chr(10).join(notes))}'>{INFO}</span>") if notes else ""
    fix = step.get("fix") or ""
    fix_html = f"<span class='pl-fix'>{e(FIXERS.get(fix, fix))}</span>" if fix and state != OK else ""
    more = [link(label, panels.get(href, href)) for label, href in step.get("more") or [] if panels.get(href, href)]
    act = f"<div class='pl-act'>{fix_html}{''.join(more)}</div>" if fix_html or more else ""
    words = f"<div class='pl-flowtext'>{e(step['flow_words'])}</div>" if step.get("flow_words") else ""
    word = STATE_WORDS.get(state, state)
    opened = " open" if state in OPEN else ""
    return (f"<li><details class='pl-stage' id='step-{e(step.get('key'))}' data-state='{e(state)}'{opened}>"
            f"<summary><span class='pl-mark' data-state='{e(state)}'>{icon(STATE_ICON.get(state, 'dash'), 14)}"
            f"{e(word)}</span><h3 data-tip='{e(step.get('does'))}'>{e(step.get('name'))}</h3>"
            f"{flow_html(step)}{out_html(step)}<span class='pl-at' data-tip='{e(when_tip)}'>{e(when or '—')}</span>"
            f"<span class='pl-aux'>{info}<span class='pl-chev'>{icon('right', 14)}</span></span></summary>"
            f"<div class='pl-body'><p class='pl-say'>{e(step.get('say'))}</p>{act}{words}{lost_html(step)}</div>"
            "</details></li>")


PIPE_JS = """(()=>{const go=id=>{const d=document.getElementById(id);if(!d)return false;if(d.tagName==='DETAILS')d.open=true;
const still=matchMedia('(prefers-reduced-motion: reduce)').matches;d.scrollIntoView({block:'start',behavior:still?'auto':'smooth'});
d.classList.remove('pl-hit');void d.offsetWidth;d.classList.add('pl-hit');return true};
document.querySelectorAll('.pl-node').forEach(a=>a.addEventListener('click',ev=>{const id=a.getAttribute('href').slice(1);
if(go(id)){ev.preventDefault();history.replaceState(null,'','#'+id)}}));})();"""


# look again at the same page (its ?night= kept), without asking for another reading (?fresh=1 dropped)
AGAIN = ("const u=new URL(location.href);u.searchParams.delete('fresh');u.hash='';"
         "setTimeout(()=>location.replace(u.toString()),{ms})")


def pipeline_body(snap: dict, panels: dict[str, str], sites_html: str = "", before: str = "") -> str:
    """The page: before (今晚 and 昨晚變了什麼: tonight_html), then the first line and the flow map in one card, the
    steps, the sites. panels: where the step links go by their short names ('overview', 'board', …); a link whose
    name panels does not hold is used as it is."""
    if not snap.get("steps"):
        return (f"<style>{CSS}</style>{before}<section class='card pad pl-pipe-head'><span class='pill none'>讀取中</span>"
                "<span class='pl-big'>正在讀每一步的紀錄，幾秒後自動重新整理</span></section>"
                f"<script>{AGAIN.replace('{ms}', '4000')}</script>")
    head = (f"<div class='pl-pipe-head'>{pill(snap['state'], STATE_WORDS.get(snap['state']))}"
            f"<span class='pl-big'>{e(snap['say'])}</span>"
            f"<span class='pl-when' data-tip='每 5 分鐘重新讀一次各步驟的紀錄；這次讀了 {e(snap.get('took'))}'>"
            f"讀於 {e(stamp(snap.get('at')))}"
            + ("（正在重新讀）" if snap.get("running") else " · <a href='/?fresh=1'>現在重讀</a>")
            + "</span></div>")
    # a reading under way: look again in a few seconds, without asking for another one
    again = f"<script>{AGAIN.replace('{ms}', '5000')}</script>" if snap.get("running") else ""
    top = f"<section class='card pl-top'>{head}{flow_map(snap['steps'])}</section>{again}"
    chain = "<ol class='pl-chain card'>" + "".join(stage_html(s, panels) for s in snap["steps"]) + "</ol>"
    return f"<style>{CSS}</style>{before}{top}{chain}{sites_html}<script>{PIPE_JS}</script>"


SITE_JS = """(()=>{const table=document.querySelector('table.pl-sites');
if(!table)return;const body=table.tBodies[0],rows=[...body.rows];
table.querySelectorAll('th[data-col]').forEach(th=>th.addEventListener('click',()=>{
const down=th.getAttribute('aria-sort')!=='descending';
table.querySelectorAll('th').forEach(h=>h.removeAttribute('aria-sort'));
th.setAttribute('aria-sort',down?'descending':'ascending');const col=th.cellIndex;
const key=r=>{const c=r.cells[col];return c.dataset.v!==undefined?Number(c.dataset.v):c.textContent.trim();};
rows.sort((a,b)=>{const x=key(a),y=key(b);const d=(typeof x==='number'&&typeof y==='number')?x-y:
String(x).localeCompare(String(y),'zh-Hant');return down?-d:d;});rows.forEach(r=>body.appendChild(r));}));})();"""


def site_cell(cell: dict) -> str:
    """One number of a site at one step; its second line (cell['sub']) and its detail (cell['tip']) in the floating
    card; a step to look at gets a small mark with an icon, the number stays ink (redesign 2026-11-17)."""
    tip = "\n".join(x for x in (cell.get("sub"), cell.get("tip")) if x)
    tip_attr = f" data-tip='{e(tip)}'" if tip else ""
    state = cell.get("state")
    mark = (f"<span class='pl-m {state}' aria-label='{e(STATE_WORDS[state])}'>{icon(STATE_ICON[state], 11)}</span>"
            if state in (WARN, BAD) else "")
    value = cell.get("n")
    return f"<td data-v='{value if value is not None else -1}'{tip_attr}>{mark}{n(value)}</td>"


def sites_html(snap: dict, all_href: str = "") -> str:
    """The sites with something to look at (留意 or 出錯) with their number at each step (snap['sites'],
    snap['site_cols']: (key, header, what it counts)) and what to look at; a click on a header sorts by it. A site's
    name opens its page (/site), its address is in the name's floating card. Every site is on 網站 (all_href: 資料總覽,
    「全部 N 個網站 →」): one list of sites, not one per page (structure design 2026-11-17 section 二)."""
    rows, cols = snap.get("sites") or [], snap.get("site_cols") or []
    if not rows or not cols:
        return ""
    every = (f"<a class='pl-all' href='{e(all_href)}' data-tip='「網站」：資料總覽的網站列表'>全部 {len(rows)} 個網站 →</a>"
             if all_href else "")
    marked = [r for r in rows if r.get("worst") in (WARN, BAD)]
    if not marked:
        return (f"<h2 class='sec' id='sites'>要留意的網站<span class='faint'>0</span>{every}</h2>"
                "<section class='card pad'><span class='soft'>沒有要留意的網站</span></section>")
    head = "".join(f"<th data-col='{e(key)}' data-tip='{e(tip)}'>{e(name)}</th>" for key, name, tip in cols)
    body = []
    for row in marked:
        cells = "".join(site_cell((row.get("cells") or {}).get(key) or {}) for key, _name, _tip in cols)
        why = "；".join(row.get("why") or [])
        key = e(row.get("key"))
        name = (f"<a href='{e(row['href'])}' data-tip='{key}'>{e(row.get('name'))}</a>" if row.get("href")
                else f"<span data-tip='{key}'>{e(row.get('name'))}</span>")
        body.append(f"<tr data-q='{e(row.get('q', ''))}'><td>{name}</td>{cells}<td class='pl-why'>{e(why)}</td></tr>")
    return (f"<h2 class='sec' id='sites'>要留意的網站<span class='faint'>{len(marked)}</span>{every}</h2>"
            "<section class='card pad'><div class='pl-scroll'>"
            f"<table class='pl-sites'><thead><tr><th data-col='name'>網站</th>{head}<th class='pl-why'>要留意的地方</th></tr>"
            f"</thead><tbody>{''.join(body)}</tbody></table></div></section><script>{SITE_JS}</script>")


# the crawl's 结果 of an export row in plain words (nknu.archive.export writes them; the ones not here show as written)
OUTCOME_WORDS = {"抓到了": "抓到了", "学校那边已经不存在": "學校那邊已經不存在",
                 "应该是档案，回来不是档案（网页、空白或错误说明）": "該是檔案，學校回的卻是網頁或錯誤說明",
                 "跳到别的网址（目标另算）": "轉到別的網址（另外收）", "看起来像不存在（程序比对的）": "看起來像不存在",
                 "按规则不抓": "照規則不抓", "还没抓": "還沒抓", "这一轮还没决定怎么处理的写法": "還沒決定怎麼處理"}


def _bars(counts: dict[str, int], words: dict[str, str] | None = None, examples: dict | None = None,
          to: str = "/check?ref=") -> str:
    """Reasons and their counts, the largest first, each with up to five examples that open 網頁核查 on that page."""
    rows = []
    for why, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if not why:
            continue
        shown = (words or {}).get(why, s2t(why))
        eg = ""
        some = ((examples or {}).get(why) or [])[:5]
        titles = [t for _u, t in some]
        for url, title in some:
            # a title shared by several examples (a site's own name on every page) says nothing: the address does
            label = s2t(title) if title and titles.count(title) == 1 else url
            eg += f"<a class='pl-eg1' href='{e(to + quote(url, safe=''))}' data-tip='{e(url)}'>{e(label)}</a>"
        rows.append(f"<li><span class='pl-why'>{e(shown)}</span><b class='num'>{count:,}</b>"
                    + (f"<div class='pl-egs'>{eg}</div>" if eg else "") + "</li>")
    return f"<ul class='pl-reasons'>{''.join(rows)}</ul>" if rows else "<p class='soft'>沒有。</p>"


SITE_CSS = """
.pl-reasons{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}
.pl-reasons li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;padding:6px 10px;border-radius:9px;
background:var(--sunk)}
.pl-reasons .pl-why{min-width:0}.pl-reasons .num{text-align:right}
.pl-reasons .pl-egs{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px 10px;font-size:12px}
.pl-reasons .pl-eg1{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);max-width:420px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}.pl-reasons .pl-eg1:hover{text-decoration:underline}
.pl-facts{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:13px;color:var(--ink2);margin:4px 0 0}
.pl-facts b{color:var(--ink);font-variant-numeric:tabular-nums}
.pl-facts a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2)}.pl-facts a:hover{text-decoration:underline}
.pl-funnel{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin:12px 0 2px}
.pl-funnel .pl-st{display:flex;flex-direction:column;padding:8px 14px;border-radius:12px;background:var(--sunk);min-width:110px}
.pl-funnel .pl-st b{font-size:20px;font-variant-numeric:tabular-nums}.pl-funnel .pl-st span{font-size:12px;color:var(--ink2)}
.pl-funnel .pl-ar{color:var(--ink3)}
.pl-site-step{margin-top:14px}.pl-site-step h3{font-size:16px;margin:0 0 4px}
.pl-more{margin-top:6px}
.pl-more>summary{display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:9px;background:var(--sunk);
font-size:13px;color:var(--ink);cursor:pointer;list-style:none}
.pl-more>summary::-webkit-details-marker{display:none}
.pl-more>summary .ic{color:var(--ink3);transition:transform .15s}.pl-more[open]>summary .ic{transform:rotate(90deg)}
.pl-more>summary:hover{background:var(--hover)}
"""


SHOWN_LOSS = 5  # rows of one kind of loss a step's section shows; the rest behind one fold row 【Claude, the lead's
#                brief 11-17: 教務處's 轉文字 printed 41 rows of 讀不出文字】
LOSS_ORDER = ("failed", "pending", "partial", "empty", "skipped")  # the to-text kinds, failures first


def folded(rows: list[str], total: int | None = None) -> str:
    """One kind of loss as rows (<li> of .pl-reasons): the first SHOWN_LOSS, the rest behind one fold row 「還有 N
    個」 that opens in place (a <details>). total: how many of the kind there are when the list holds fewer (the
    to-text accounting lists up to 30, the export's refills up to 300): said inside the fold."""
    total = max(total or 0, len(rows))
    head = f"<ul class='pl-reasons'>{''.join(rows[:SHOWN_LOSS])}</ul>"
    if total <= SHOWN_LOSS:
        return head
    unlisted = total - len(rows)
    return (head + f"<details class='pl-more'><summary>{icon('right', 12)}還有 {total - SHOWN_LOSS:,} 個</summary>"
            + (f"<ul class='pl-reasons'>{''.join(rows[SHOWN_LOSS:])}</ul>" if len(rows) > SHOWN_LOSS else "")
            + (f"<div class='faint small' style='margin-top:6px'>其餘 {unlisted:,} 個沒有列出來</div>" if unlisted else "")
            + "</details>")


STATUS_TEXT = {"ok": "轉成文字", "partial": "轉了一部分", "empty": "讀不出文字", "skipped": "照規則跳過", "failed": "轉文字失敗",
               "pending": "還沒轉"}


def funnel(detail: dict) -> str:
    """The site through the steps on one line: what its export lists, what the crawl got of it, what became text,
    what the library holds."""
    site, ex, text = detail["site"], detail.get("export") or {}, detail.get("text") or {}
    stops = []
    if ex:
        stops.append((ex.get("rows"), "導出清單的筆數", "每筆一個網頁、檔案或圖片"))
        stops.append((ex.get("outcomes", {}).get("抓到了"), "抓到了", "爬蟲抓到的（其餘看下面「爬取」）"))
    if text:
        stops.append(((text.get("status") or {}).get("ok"), "轉成文字", "壓縮檔裡的檔各算一個"))
    stops.append((site.get("docs"), "庫裡的文件", "合併重複、去掉列表翻頁以後"))
    arrow = f"<span class='pl-ar'>{icon('right', 16)}</span>"
    return "<div class='pl-funnel'>" + arrow.join(
        f"<div class='pl-st' data-tip='{e(tip)}'><b>{n(value)}</b><span>{e(label)}</span></div>"
        for value, label, tip in stops) + "</div>"


def site_steps(detail: dict, *, style: bool = True) -> str:
    """One site through the pipeline's steps (nknu.ops.pipeline.site_detail), the 處理流程 of its page (/site,
    nknu.ops.site_page): its steps as one line of numbers, then in the pipeline's order what the crawl got of what the
    export lists (by result, with examples that open 網頁核查 on that page), what the export says is missing, what the
    to-text step made of its files (with what failed or came out empty), and what the library holds of it. Until the
    structure design of 2026-11-17 this was the page /pipeline/site, with the site's name and links over it. style:
    whether the page's style comes with it (site_page puts it on the page once)."""
    site = detail["site"]
    parts = [f"<section class='card pad'>{funnel(detail)}</section>"]
    for words in detail.get("errors") or []:
        parts.append(f"<section class='card pad'><span class='pill warn'>讀不到</span> {e(words)}</section>")
    ex = detail.get("export")
    if ex:
        fetched = ex["outcomes"].get("抓到了", 0)
        parts.append(
            "<section class='card pad pl-site-step'><h3>爬取</h3>"
            f"<div class='pl-facts'><span>導出清單 <b>{n(ex['rows'])}</b> 筆</span><span>抓到了 <b>{n(fetched)}</b></span>"
            f"<span>其他結果 <b>{n(ex['rows'] - fetched)}</b>（點例子看那一頁）</span></div>"
            + _bars({k: v for k, v in ex["outcomes"].items() if k != "抓到了"}, OUTCOME_WORDS, ex.get("examples"))
            + "</section>")
        newer = (f"<span>爬蟲有更新的一份 <b class='mono'>{e(detail['newer'])}</b>，庫還沒用</span>"
                 if detail.get("newer") else "")
        parts.append(
            "<section class='card pad pl-site-step'><h3>導出</h3>"
            f"<div class='pl-facts'><span>導出於 <b>{e(stamp(ex.get('exported_at')))}</b></span>"
            f"<span>資料夾 <b class='mono'>{e(ex['folder'])}</b></span>"
            f"<span>公告列表的分頁（不進庫） <b>{n(ex.get('paging'))}</b></span>{newer}</div>"
            "<div class='faint small' style='margin-top:6px'>清單標出的缺漏（一筆可能缺好幾樣）：</div>"
            + _bars(ex.get("missing") or {}) + refill_html(ex) + "</section>")
    text = detail.get("text")
    if text:
        status = text.get("status") or {}
        kinds: dict[str, list[str]] = {}
        for st, r, f in text.get("bad") or []:
            kinds.setdefault(st, []).append(
                f"<li><span class='pl-why'>{e(STATUS_TEXT.get(st, st))}：{e(s2t(f))}"
                f"<span class='faint'>（{e(s2t(r) or '沒寫原因')}）</span></span><b class='num'></b></li>")
        order = {st: i for i, st in enumerate(LOSS_ORDER)}
        bad = "".join(folded(rows, status.get(st))
                      for st, rows in sorted(kinds.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])))
        parts.append(
            "<section class='card pad pl-site-step'><h3>轉文字</h3><div class='pl-facts'>"
            + "".join(f"<span>{e(STATUS_TEXT.get(st, st))} <b>{n(c)}</b></span>"
                      for st, c in sorted(status.items(), key=lambda kv: -kv[1]))
            + "</div>" + bad + "</section>")
    copies = detail.get("copies") or {}
    flags = detail.get("flags") or []
    parts.append(
        "<section class='card pad pl-site-step'><h3>建庫</h3><div class='pl-facts'>"
        f"<span>庫裡文件 <b>{n(site.get('docs'))}</b></span>"
        f"<span>資料截至 <b>{e(site.get('data_as_of') or '—')}</b></span>"
        f"<span>學校刪掉或轉走、沒收的公告 <b>{n(site.get('deleted_notices'))}</b></span>"
        f"<span>正文沒抓全、改讀整頁 <b>{n(site.get('under_read'))}</b></span>"
        + (f"<span>別處有一樣的、併成一份 <b>{n(copies.get('merged'))}</b></span>" if copies else "")
        + "</div>" + (f"<div class='pl-facts'><span>這個網站的檢查提醒：{e('、'.join(flags))}</span></div>" if flags else "")
        + "</section>")
    return (f"<style>{CSS}{SITE_CSS}</style>" if style else "") + "".join(parts)


REFILL_WORDS = {"10-24 旧存档": "10-24 舊存檔", "上一次抓到的": "每晚更新前一次抓到的"}


def refill_html(ex: dict) -> str:
    """The rows of the export filled from an older answer (its 来源), each with why (its 备注) and a link to 網頁核查 on
    that page: this round's answer was broken or empty."""
    rows = ex.get("refills") or []
    counts = ex.get("refill") or {}
    total = sum(counts.values())
    if not total:
        return "<div class='faint small' style='margin-top:10px'>用舊資料補的：沒有。</div>"
    kinds: dict[str, list[str]] = {}
    for url, title, origin, note in rows:
        kinds.setdefault(origin, []).append(
            f"<li><span class='pl-why'><a class='pl-eg1' href='/check?ref={e(quote(url, safe=''))}' data-tip='{e(url)}'>"
            f"{e(s2t(title) or url)}</a><span class='faint'>（{e(REFILL_WORDS.get(origin, origin))}）</span>"
            + (f"<div class='faint small'>{e(s2t(note))}</div>" if note else "") + "</span><b class='num'></b></li>")
    by = "、".join(f"{REFILL_WORDS.get(k, k)} {v:,}" for k, v in counts.items())
    items = "".join(folded(kinds[k], counts.get(k)) for k in sorted(kinds, key=lambda k: -counts.get(k, 0)))
    return (f"<div class='faint small' style='margin-top:10px'>抓壞或抓到空的、改用舊資料補上的 {total:,} 筆（{e(by)}）"
            f"：</div>{items}")


# ------------------------------------------------------------------------------------------------ 今晚 (nknu.ops.nightly)

# a clock: the mark of a part that runs (look has no such drawing; currentColor only) 【Claude】
CLOCK = ("<svg class='ic' viewBox='0 0 16 16' width='{s}' height='{s}' fill='none' stroke='currentColor' "
         "stroke-width='1.6' stroke-linecap='round' aria-hidden='true'><circle cx='8' cy='8' r='5.6'/>"
         "<path d='M8 4.8V8l2.2 1.4'/></svg>")
NIGHT_ICON = {"ok": "ok", "warn": "warn", "bad": "no", "missed": "no", "wait": "dash", "off": "dash"}
SAY_STATE = {"ok": "ok", "warn": "warn", "bad": "bad", "run": "run", "wait": "none"}
SHOWN_DOCS = 40  # documents a list shows on the page; the rest are in /api/tonight 【Claude】
SHOWN_SITES = 20  # sites shown before 還有 N 個網站有變動 (the ones with something to look at come first) 【Claude】


def _mark(state: str, size: int) -> str:
    return CLOCK.format(s=size) if state == "run" else icon(NIGHT_ICON.get(state, "dash"), size)


def cell_html(c: dict) -> str:
    """One part of the night: its mark (an icon in it), its name and state word, what it does or where it stopped,
    when (a running part's 已經 … ticks in the page: TONIGHT_JS), who fixes it when it stopped."""
    state, sev = c.get("state", "wait"), c.get("sev") or ""
    # a part still running that has been silent a while (可能卡住, nknu.ops.nightly.STUCK) wears the warning mark; its
    # minutes still tick and the page still asks (review 2026-11-17)
    mark = ("warn" if sev == "warn" else "run" if state == "run" else "bad" if state == "stop" and sev == "bad"
            else "ok" if state == "done" else "wait")
    when = e(c.get("when"))
    if state == "run" and c.get("since_ts"):
        when = f"<span data-since='{int(c['since_ts'])}'>{when}</span>"
    tip = "\n".join(x for x in (c.get("say"), c.get("tip"), f"從 {c['since'][11:16]}" if c.get("since") else "") if x)
    return (f"<li class='tn-cell' data-state='{e(state)}' data-sev='{e(sev)}' data-tip-h='{e(c.get('name'))}："
            f"{e(c.get('word'))}' data-tip='{e(tip)}'><span class='tn-dot'>{_mark(mark, 16)}</span>"
            f"<div class='tn-body'><div class='tn-nm'><b>{e(c.get('name'))}</b><span class='tn-word'>{e(c.get('word'))}"
            "</span></div>"
            + (f"<div class='tn-sayc'>{e(c.get('say'))}</div>" if c.get("say") else "")
            + (f"<div class='tn-when'>{when}</div>" if when else "")
            + (f"<div class='tn-fix'>{e(c.get('fixer'))}</div>" if c.get("fixer") else "")
            + (f"<div class='tn-when'>{icon('warn', 12)} 另外有一次建庫在跑</div>" if c.get("aside") else "")
            + "</div></li>")


def nights_html(nights: list[dict], shown: str) -> str:
    """The last seven nights, oldest first: a small mark each (an icon in it) over its date; the one shown is ringed
    in ink; a click shows that night (?night=)."""
    out = []
    for n in nights:
        state = n.get("state", "wait")
        current = " aria-current='page'" if n.get("night") == shown else ""
        off = " off" if state == "off" else ""
        tip_h = f"{n.get('date')}（{n.get('label')}）：{n.get('word')}" if n.get("label") in ("今晚", "昨晚") else \
            f"{n.get('date')} 那一晚：{n.get('word')}"
        out.append(f"<a class='tn-n{off}' href='{e(n.get('href'))}' data-state='{e(state)}'{current} "
                   f"data-tip-h='{e(tip_h)}'><span class='tn-dot'>{_mark(state, 13)}</span>{e(n.get('date'))}</a>")
    return "<nav class='tn-nights' aria-label='最近 7 晚'>" + "".join(out) + "</nav>"


TONIGHT_JS = """(()=>{const box=document.querySelector('[data-tonight]');if(!box)return;
const words=s=>s<90?Math.round(s)+' 秒':s<5400?Math.round(s/60)+' 分鐘':Math.floor(Math.round(s/60)/60)+' 小時 '+Math.round(s/60)%60+' 分';
const tick=()=>document.querySelectorAll('[data-since]').forEach(el=>{el.textContent='已經 '+words(Date.now()/1000-Number(el.dataset.since))});
const live=box.hasAttribute('data-live'),busy=!!document.querySelector('[data-computing]');
if(live)setInterval(tick,30000);if(!live&&!busy)return;
const poll=()=>fetch(box.getAttribute('data-tonight'),{cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.sig!==box.getAttribute('data-sig'))location.reload();else setTimeout(poll,busy?5000:60000)}).catch(()=>setTimeout(poll,60000));
setTimeout(poll,busy?5000:60000)})();"""


def tonight_html(t: dict, changes: dict) -> str:
    """The home page's top (t: nknu.ops.nightly.tonight; changes: nknu.ops.changes.Changes.snapshot): the first
    sentence (of the default night), the night shown as five parts in a row with the last seven nights beside it, and
    昨晚變了什麼 (changes_html). The page asks /api/tonight again while something runs or is computed, and reloads
    when what it shows has changed."""
    sentence, shown = t.get("sentence") or {}, t.get("shown") or {}
    say_state = SAY_STATE.get(sentence.get("state"), "none")
    mark = _mark("run" if say_state == "run" else {"ok": "ok", "warn": "warn", "bad": "bad"}.get(say_state, "wait"), 16)
    api = "/api/tonight?brief=1" + (f"&night={quote(shown['night'])}"
                                    if shown.get("night") and shown["night"] != t.get("default") else "")
    live = " data-live" if t.get("live") else ""
    said = e(sentence.get("text"))
    if sentence.get("since_ts") and sentence.get("lead"):  # the minutes tick in the page (TONIGHT_JS)
        rest = sentence["text"][len(sentence["lead"]):].lstrip("，")
        said = f"{e(sentence['lead'])}，<span data-since='{int(sentence['since_ts'])}'>{e(rest)}</span>"
    title = shown.get("label") or ""
    cells = "".join(cell_html(c) for c in shown.get("cells") or [])
    top = (f"<section class='card tn' data-tonight='{e(api)}' data-sig='{e(t.get('sig'))}'{live}>"
           f"<div class='tn-say' data-state='{e(say_state)}'><span class='tn-m'>{mark}</span>"
           f"<span>{said}</span></div>"
           f"<div class='tn-bar'><h2 class='tn-h'>{e(title)}<span class='faint'>{e(shown.get('night'))}</span></h2>"
           f"{nights_html(t.get('nights') or [], shown.get('night', ''))}</div>"
           f"<ol class='tn-line' aria-label='這一晚的五步'>{cells}</ol></section>")
    return top + changes_html(changes, t) + f"<script>{TONIGHT_JS}</script>"


def _doc_li(d: dict, *, site: str = "", left: bool = False) -> str:
    """One document of a list: its title, a link to 文件追蹤 (one in the library) or to 網頁核查's copy of the page (one
    that left), its address, id and note in the floating card."""
    title = d.get("title") or d.get("url") or d.get("id")
    href = f"/check?ref={quote(d.get('url') or '', safe='')}" if left else f"/trace?ref={quote(d.get('id') or '', safe='')}"
    tip = "\n".join(x for x in (d.get("url"), d.get("id"), d.get("note")) if x)
    what = "".join(f"<span class='tag'>{e(w)}</span>" for w in d.get("what") or [])
    key = d.get("site") or ""
    where = (f"<a class='tn-of' href='/site?site={e(quote(key, safe=''))}'>{e(site)}</a>" if site and key
             else f"<span class='tn-of'>{e(site)}</span>" if site else "")
    return f"<li>{where}<a href='{e(href)}' data-tip='{e(tip)}'>{e(title)}</a>{what}</li>"


def _docs(items: list[dict], total: int, *, left: bool = False, site_names: bool = False) -> str:
    shown = "".join(_doc_li(d, site=d.get("site_name", "") if site_names else "", left=left)
                    for d in items[:SHOWN_DOCS])
    more = total - min(len(items), SHOWN_DOCS)
    return (f"<ul class='tn-docs'>{shown}</ul>"
            + (f"<div class='tn-more'>還有 {more:,} 份（全部在 /api/tonight）</div>" if more > 0 else ""))


def _flag(kind: str, words: str) -> str:
    return f"<span class='tn-flag {kind}'>{icon('no' if kind == 'bad' else 'warn', 12)}{e(words)}</span>"


def _reason_tag(key: str, n: int, reasons: dict) -> str:
    r = reasons.get(key) or {"label": key, "tip": "", "kind": "ok"}
    tip = r.get("tip", "") + (f"（{FIXERS[r['fix']]}）" if r.get("fix") in FIXERS and r.get("kind") != "ok" else "")
    lead = ""
    if r.get("kind") == "unknown":
        lead = icon("no", 11)
    elif r.get("kind") == "look":
        lead = icon("warn", 11)
    cls = " tn-r-" + r.get("kind", "ok")
    return f"<span class='tag{cls}' data-tip-h='{e(r['label'])}' data-tip='{e(tip)}'>{lead}{e(r['label'])} <b>{n:,}</b></span>"


def _reason_keys(s: dict, reasons: dict) -> list[str]:
    """A site's reasons for what left, 查不出原因 first, then ours to look at, then by how many."""
    order = {"unknown": 0, "look": 1, "ok": 2}
    return sorted(s.get("reasons") or {}, key=lambda k: (order.get((reasons.get(k) or {}).get("kind"), 2),
                                                         -s["reasons"][k]))


def _change_lists(s: dict, reasons: dict, keys: list[str]) -> str:
    """A site's lists: what left by reason (keys' order), what came in, what changed and how."""
    parts = []
    lists = s.get("lists") or {}
    for k in keys:
        r = reasons.get(k) or {"label": k}
        parts.append(f"<div><h4>離開：{e(r['label'])}<span class='faint'>{s['reasons'][k]:,}</span></h4>"
                     + _docs((lists.get("left") or {}).get(k) or [], s["reasons"][k], left=True) + "</div>")
    if s.get("new"):
        parts.append(f"<div><h4>新進<span class='faint'>{s['new']:,}</span></h4>"
                     + _docs(lists.get("new") or [], s["new"]) + "</div>")
    if s.get("changed"):
        parts.append(f"<div><h4>改了<span class='faint'>{s['changed']:,}</span></h4>"
                     + _docs(lists.get("changed") or [], s["changed"]) + "</div>")
    return "".join(parts)


def site_change_html(s: dict, reasons: dict) -> str:
    """One site's changes: a summary row (its name, a link to its page; new, changed, left, the reasons the left ones
    have) that opens on the lists: what left by reason (查不出原因 and ours first), what came in, what changed and
    how."""
    keys = _reason_keys(s, reasons)
    why = "".join(_reason_tag(k, s["reasons"][k], reasons) for k in keys)

    def num(v: int, head: str) -> str:
        return f"<span class='num{' zero' if not v else ''}' data-h='{head}'>{v:,}</span>"

    held = " data-tip='這個網站留在舊的導出（ingest-hold.txt）'" if s.get("held") else ""
    # the name opens the site's page (every site name links there: structure design 2026-11-17); the rest of the
    # row opens the lists
    name = (f"<a href='/site?site={e(quote(s.get('site') or '', safe=''))}'>{e(s.get('name'))}</a>" if s.get("site")
            else e(s.get("name")))
    summary = (f"<summary><span class='tn-sn'{held}>{name}</span>{num(s['new'], '新進')}"
               f"{num(s['changed'], '改了')}{num(s['left'], '離開')}<span class='tn-why'>{why}</span>"
               f"<span class='pl-chev'>{icon('right', 14)}</span></summary>")
    return (f"<details class='tn-site' data-site='{e(s.get('site'))}'>{summary}"
            f"<div class='tn-in'>{_change_lists(s, reasons, keys)}</div></details>")


def one_site_changes(changes: dict, key: str) -> str:
    """One site's part of the last swap's comparison, for its page (/site): its new, changed and left as figures,
    the reasons the left ones have, its lists open; or why there is nothing to show (no library before, the same
    file, being computed, nothing of this site changed)."""
    state = changes.get("state")
    if state in ("none", "same", "error"):
        flag = _flag("warn", "比不了") + " " if state == "error" else ""
        return f"<section class='card pad tn-chg'>{flag}<span class='soft'>{e(changes.get('words'))}</span></section>"
    if state == "computing":
        return ("<section class='card pad tn-chg' data-computing><span class='pill none'>計算中</span> "
                "<span class='soft'>正在比對前一份庫和現在的庫（要一兩分鐘）</span></section>")
    prev, new = changes.get("prev") or {}, changes.get("new") or {}

    def when(x: dict) -> str:
        return (x.get("swapped_at") or x.get("built_at") or "")[5:16].replace("T", " ")

    pair = f"<span class='faint small'>{e(when(prev))} 換上的庫 → {e(when(new))} 換上的庫</span>"
    s = next((x for x in changes.get("sites") or [] if x.get("site") == key), None)
    if s is None:
        return f"<section class='card pad tn-chg'><span class='soft'>這個網站沒有變動</span>　{pair}</section>"
    reasons = changes.get("reasons") or {}
    keys = _reason_keys(s, reasons)
    figs = "".join(f"<div class='tn-fig'><span>{label}</span><b>{s.get(k, 0):,}</b></div>"
                   for k, label in (("new", "新進"), ("changed", "改了"), ("left", "離開")))
    why = "".join(_reason_tag(k, s["reasons"][k], reasons) for k in keys)
    return (f"<section class='card pad tn-chg tn-one'><div class='tn-tot'>{figs}<div class='tn-crawl'>{pair}</div></div>"
            + (f"<div class='tn-why'>{why}</div>" if why else "")
            + f"<div class='tn-in'>{_change_lists(s, reasons, keys)}</div></section>")


def changes_title(changes: dict, t: dict) -> str:
    """昨晚變了什麼 when the last swap was that night's, else 上次換庫變了什麼 with its day."""
    night = changes.get("night") or ""
    for n in t.get("nights") or []:
        if n.get("night") == night and n.get("label") in ("今晚", "昨晚"):
            return f"{n['label']}變了什麼"
    return "上次換庫變了什麼"


def changes_html(changes: dict, t: dict) -> str:
    """昨晚變了什麼 (nknu.ops.changes): the two libraries compared, what came in, changed and left in violet beside the
    crawler's own counts of its night; then 查不出原因 (red, open), then every site that changed, the ones with
    something unexplained or ours to look at first, each opening on its lists."""
    state = changes.get("state")
    if state in ("none", "same", "error"):
        flag = _flag("warn", "比不了") if state == "error" else ""
        return (f"<h2 class='sec' id='changes'>上次換庫變了什麼</h2><section class='card pad tn-chg'>{flag} "
                f"<span class='soft'>{e(changes.get('words'))}</span></section>")
    if state == "computing":
        return ("<h2 class='sec' id='changes'>上次換庫變了什麼</h2><section class='card pad tn-chg' data-computing>"
                "<span class='pill none'>計算中</span> <span class='soft'>正在比對前一份庫和現在的庫（兩份各約七萬份文件，"
                "要一兩分鐘）</span></section>")
    prev, new = changes.get("prev") or {}, changes.get("new") or {}

    def when(x: dict) -> str:
        return (x.get("swapped_at") or x.get("built_at") or "")[5:16].replace("T", " ")

    head = (f"<h2 class='sec' id='changes'>{e(changes_title(changes, t))}<span class='faint' data-tip='aca.prev.db"
            f"（{e(prev.get('docs'))} 份）→ aca.db（{e(new.get('docs'))} 份）；比對花了 {e(changes.get('took'))}'>"
            f"{e(when(prev))} 換上的庫 → {e(when(new))} 換上的庫</span></h2>")
    tot = changes.get("totals") or {}
    figs = "".join(f"<div class='tn-fig'><span>{label}</span><b>{tot.get(k, 0):,}</b></div>"
                   for k, label in (("new", "新進"), ("changed", "改了"), ("left", "離開")))
    crawler = t.get("crawler") or {}
    crawl = ""
    if crawler.get("counts"):
        when_c = (crawler.get("started") or "")[5:16].replace("T", " ")
        crawl = (f"<div class='tn-crawl' data-tip='爬蟲自己數的：那一晚看了哪些網址、和上一次比怎樣（網址，不是文件）'>"
                 f"<span>爬蟲 {e(when_c)} 那一晚看到</span>"
                 + "".join(f"<span>{e(k)} <b>{v:,}</b></span>" for k, v in crawler["counts"] if v)
                 + "</div>")
    flags = []
    if tot.get("unexplained"):
        flags.append(_flag("bad", f"查不出原因 {tot['unexplained']:,} 份"))
    if tot.get("look"):
        flags.append(_flag("warn", f"可能是我們漏的 {tot['look']:,} 份"))
    reasons = changes.get("reasons") or {}
    unx = ""
    if changes.get("unexplained"):
        unx = (f"<div class='tn-unx'><div class='tn-row'>{_flag('bad', '查不出原因')}<b>{tot['unexplained']:,}</b> 份"
               f"<span class='soft'>{e(FIXERS[CONTENT])}</span></div>"
               + _docs(changes["unexplained"], tot["unexplained"], left=True, site_names=True) + "</div>")
    sites = changes.get("sites") or []
    if sites:
        rows = "".join(site_change_html(s, reasons) for s in sites[:SHOWN_SITES])
        if len(sites) > SHOWN_SITES:
            rows += (f"<details class='tn-rest'><summary>還有 {len(sites) - SHOWN_SITES} 個網站有變動</summary>"
                     + "".join(site_change_html(s, reasons) for s in sites[SHOWN_SITES:]) + "</details>")
        table = ("<div class='tn-sites'><div class='tn-head'><span>網站</span><span>新進</span><span>改了</span>"
                 f"<span>離開</span><span>離開的原因</span><span></span></div>{rows}</div>")
    else:
        table = "<p class='soft'>兩份庫的文件一模一樣。</p>"
    return (f"{head}<section class='card pad tn-chg'><div class='tn-tot'>{figs}"
            f"<div class='tn-fig'>{''.join(flags)}</div>{crawl}</div>{unx}{table}</section>")
