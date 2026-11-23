"""The pieces of 流水線 that 今晚 (nknu.ops.tonight_page) and a site's page (nknu.ops.site_page) share, drawn from
nknu.ops.pipeline's snapshot:

1. a row per step (stage_html), all in one list: its state once (a small mark with an icon and a word), its name, a
   violet bar of what came out of what went in (one unit; violet for an amount, as on 資料總覽: owner 11-15 「其他地方普遍
   用紫色來表達多和少」), the number that came out and when it last ran. A step to look at (留意, 出錯) opens by itself on
   what is wrong, who fixes it and its largest reasons; the rest floats beside the pointer (data-tip). Since panels v3
   (2026-11-17) these rows are 今晚's folded 「各步驟細節」;
2. one site through the steps (site_steps): each step's reasons with examples that open 網頁核查 on that page, now the
   folded 「各步驟細節」 of a site's page;
3. one site's part of the last swap's comparison (one_site_changes).

Redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md); panels v3 the same day (docs/superpowers/
specs/2026-11-17-panels-v3-questions.md) took the flow map, the per-site table and the night's five parts off the
home: 今晚 answers the owner's questions with charts (tonight_page). The words are Traditional Chinese, Taiwan usage
(owner 11-15: 「可视化dashboard全部换繁体字」); the layout, the colours' jobs and the names are 【Claude】 (owner 11-16:
「全部给你决定 只要让我看到最后的东西是好用的就行」)."""

from __future__ import annotations

import re
from urllib.parse import quote

from nknu.ops.look import e, icon, s2t
from nknu.ops.pipeline import BAD, NONE, OK, STATE_WORDS, WARN, stamp

PILL = {OK: "good", WARN: "warn", BAD: "bad", NONE: "none"}
STATE_ICON = {OK: "ok", WARN: "warn", BAD: "no", NONE: "dash"}  # look's drawings: the state is never colour alone
SHOWN_REASONS = 4  # reasons an open step shows; the rest are in the floating card 【Claude】
OPEN = (WARN, BAD)  # the states whose row opens by itself (redesign 2026-11-17) 【Claude】
# a small "i" in a ring: the mark the notes of a step float from (look has no such drawing; currentColor only)
INFO = ("<svg class='ic' viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.5' "
        "stroke-linecap='round' aria-hidden='true'><circle cx='8' cy='8' r='6'/><path d='M8 7.2v3.6'/>"
        "<circle cx='8' cy='5' r='.5' fill='currentColor'/></svg>")

CSS = """
.pl-chain{list-style:none;margin:16px 0 0;padding:0;overflow:hidden}
.pl-chain>li+li{border-top:1px solid var(--line)}
.pl-stage{scroll-margin-top:12px}
.pl-stage>summary{display:grid;grid-template-columns:96px 104px minmax(0,1fr) minmax(150px,240px) 84px 46px;
grid-template-areas:"mark name flow out when aux";align-items:center;gap:6px 16px;padding:11px 18px;cursor:pointer;
list-style:none}
.pl-stage>summary::-webkit-details-marker{display:none}
.pl-stage>summary:hover{background:var(--hover)}
.pl-stage[open]>summary{background:var(--sunk)}
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

/* one site's part of the last swap's comparison (one_site_changes): new, changed and left in violet, the reasons the
   left ones have, the lists; state colours only on the small marks (an icon in each) */
.tn-chg{margin:0 0 22px}
.tn-chg .tn-tot{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 28px}
.tn-fig{display:flex;flex-direction:column}
.tn-fig span{font-size:12px;color:var(--ink2)}
.tn-fig b{font-size:26px;font-weight:700;color:var(--amt);font-variant-numeric:tabular-nums;line-height:1.15}
.tn-crawl{margin-left:auto;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;font-size:12px;color:var(--ink2)}
.tn-flag{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:650;white-space:nowrap}
.tn-flag .ic{width:16px;height:16px;padding:2px;border-radius:50%}
.tn-flag.bad{color:var(--bad-ink)}.tn-flag.bad .ic{background:var(--bad);color:var(--on-bad)}
.tn-flag.warn{color:var(--warn-ink)}.tn-flag.warn .ic{background:var(--warn);color:var(--on-warn)}
.tn-docs{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:2px;font-size:13px}
.tn-docs li{display:flex;align-items:baseline;gap:8px;min-width:0}
.tn-docs a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);overflow:hidden;
text-overflow:ellipsis;white-space:nowrap;min-width:0}
.tn-docs .tn-of{flex:none;color:var(--ink2);font-size:12px;text-decoration:none}
.tn-docs a.tn-of:hover{color:var(--ink);text-decoration:underline}
.tn-one .tn-why{display:flex;flex-wrap:wrap;gap:4px;margin:12px 0 0}.tn-one .tn-why .tag b{color:var(--ink)}
.tn-one .tn-in{padding:12px 0 0}
.tn-docs .tag{flex:none}
.tn-more{font-size:12px;color:var(--ink3)}
.tag.tn-r-unknown{color:var(--bad-ink);background:var(--bad-bg)}.tag.tn-r-look{color:var(--warn-ink);background:var(--warn-bg)}
.tag.tn-r-unknown .ic,.tag.tn-r-look .ic{width:13px;height:13px;padding:1px;border-radius:50%}
.tag.tn-r-unknown .ic{background:var(--bad);color:var(--on-bad)}.tag.tn-r-look .ic{background:var(--warn);color:var(--on-warn)}
.tn-in{padding:4px 4px 14px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px 24px;
align-items:start}
.tn-in h4{margin:0;font-size:12px;font-weight:650;color:var(--ink2);display:flex;align-items:center;gap:6px}
@media (max-width:860px){.tn-crawl{margin-left:0}}
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


def strip_fixer(text: str) -> str:
    """A sentence without a 「交給… session」 a reading made before 11-17 evening may still hold: no page names who is
    to fix anything (spec 2026-11-17-panels-v3-questions.md 八.2)."""
    text = re.sub(r"[，；、]?\s*交給(?:[^，；（）()]|（[^（）]*）)*?session", "", text or "")
    text = re.sub(r"[，；]?\s*學校那邊的事，我們不用改", "", text)
    return re.sub(r"（\s*）|\(\s*\)", "", text).replace("（ ", "（").strip(" ，；")


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
    # no one named to fix it (spec 2026-11-17-panels-v3-questions.md 八.2): where to look further, only
    more = [link(label, panels.get(href, href)) for label, href in step.get("more") or [] if panels.get(href, href)]
    act = f"<div class='pl-act'>{''.join(more)}</div>" if more else ""
    words = f"<div class='pl-flowtext'>{e(step['flow_words'])}</div>" if step.get("flow_words") else ""
    word = STATE_WORDS.get(state, state)
    opened = " open" if state in OPEN else ""
    return (f"<li><details class='pl-stage' id='step-{e(step.get('key'))}' data-state='{e(state)}'{opened}>"
            f"<summary><span class='pl-mark' data-state='{e(state)}'>{icon(STATE_ICON.get(state, 'dash'), 14)}"
            f"{e(word)}</span><h3 data-tip='{e(step.get('does'))}'>{e(step.get('name'))}</h3>"
            f"{flow_html(step)}{out_html(step)}<span class='pl-at' data-tip='{e(when_tip)}'>{e(when or '—')}</span>"
            f"<span class='pl-aux'>{info}<span class='pl-chev'>{icon('right', 14)}</span></span></summary>"
            f"<div class='pl-body'><p class='pl-say'>{e(strip_fixer(step.get('say') or ''))}</p>{act}{words}{lost_html(step)}</div>"
            "</details></li>")


# look again at the same page (its ?night= kept), without asking for another reading (?fresh=1 dropped)
AGAIN = ("const u=new URL(location.href);u.searchParams.delete('fresh');u.hash='';"
         "setTimeout(()=>location.replace(u.toString()),{ms})")


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


def site_steps(detail: dict, *, style: bool = True) -> str:
    """One site through the pipeline's steps (nknu.ops.pipeline.site_detail), the folded 各步驟細節 of its page (/site,
    nknu.ops.site_page, whose funnel since panels v3 gives the numbers): in the pipeline's order what the crawl got of what the
    export lists (by result, with examples that open 網頁核查 on that page), what the export says is missing, what the
    to-text step made of its files (with what failed or came out empty), and what the library holds of it. Until the
    structure design of 2026-11-17 this was the page /pipeline/site, with the site's name and links over it. style:
    whether the page's style comes with it (site_page puts it on the page once)."""
    site = detail["site"]
    parts = []
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
            "<div class='faint small' style='margin-top:6px'>清單上註明缺東西的（一筆可能缺好幾樣）：</div>"
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
        + "</div>" + (f"<div class='pl-facts'><span>建庫查出這個網站的問題：{e('、'.join(flags))}</span></div>" if flags else "")
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
        return "<div class='faint small' style='margin-top:10px'>用舊存檔頂替的：沒有。</div>"
    kinds: dict[str, list[str]] = {}
    for url, title, origin, note in rows:
        kinds.setdefault(origin, []).append(
            f"<li><span class='pl-why'><a class='pl-eg1' href='/check?ref={e(quote(url, safe=''))}' data-tip='{e(url)}'>"
            f"{e(s2t(title) or url)}</a><span class='faint'>（{e(REFILL_WORDS.get(origin, origin))}）</span>"
            + (f"<div class='faint small'>{e(s2t(note))}</div>" if note else "") + "</span><b class='num'></b></li>")
    by = "、".join(f"{REFILL_WORDS.get(k, k)} {v:,}" for k, v in counts.items())
    items = "".join(folded(kinds[k], counts.get(k)) for k in sorted(kinds, key=lambda k: -counts.get(k, 0)))
    return (f"<div class='faint small' style='margin-top:10px'>這次沒抓好、用舊存檔頂替的 {total:,} 筆（{e(by)}）"
            f"：</div>{items}")


# ------------------------------------------------------------------------------------------------ the last swap, one site

SHOWN_DOCS = 40  # documents a list shows on the page; the rest are in /api/tonight 【Claude】


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
    tip = r.get("tip", "")
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
        if n.get("night") == night and n.get("label"):
            return f"{n['label']}變了什麼"
    return "上次換庫變了什麼"
