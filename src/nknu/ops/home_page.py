"""每晚運行, the home of 檢視台, as rebuilt on 2026-11-18 (the owner handed the panels over: 「之前的session没有做好，
你去看一下，然后直接动手改吧」; reviewed on the lab's real data with the Dashboard Design plugin's dashboard-review).

What the review found on the page of 11-17 (tonight_page's five sections): the line on top did not say what students
search right now, nor that the nightly ingest was switched off by hand; two charts drew axes for fourteen nights with
one or two nights of data; 整體流程's percentages (導出 54%, 建庫 65%) read as losses where they are merges and rows
kept by rule; the same sites were listed twice (問題, 要處理的網站); the seven steps were folded away at the bottom.

The page now is the pipeline itself, as the owner asked on 11-16 (「帮助我监控和看见整个项目每个关键节点的输入输出 加工
过程，让我知道整个项目是否正常运作，出错的话是哪里错了 能帮助我精准定位」), in the selection model of 資料總覽 (one thing
picked, its detail beside it, nothing to scroll for):

  1 現況       one state, one sentence, which library students search and how old it is, the ingest's pause if any
  2 流程       seven nodes in a row (爬取 → 導出 → 轉文字 → 建庫 → 品質檢查 → 換庫 → 查詢服務): each its state, the number
               it holds now and its last run; a node picked shows beside the row what it does, this night's run, what
               went in and came out as one split bar (what came out, then each reason of what did not), its notes
  3 問題       every step or site that is not normal, once (tonight_page.problems)
  4 每晚紀錄    a row a night, a cell a step (its time as a bar on one scale, its state when it is not done), what came
               in, changed and left; a night picked shows beside it each part's hours and that night's sites. A table
               with bars, not a chart of axes: it reads the same with two nights as with fourteen
  5 上次換庫    what the last swap brought in, changed and took out, by site

Nothing here needs a script to be read (the panels all show, one under another, without it); with it, a pick shows one
panel and the address keeps it (#step-build, #night-2026-11-16). No chart library on this page. Words are Traditional
Chinese, Taiwan usage; a source's own reason is shown as written, nobody is named to fix anything, an unknown code is
shown as it is (spec 2026-11-17-panels-v3-questions.md 八 still holds). The data functions are tonight_page's."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from nknu.ops import charts, standing
from nknu.ops.look import e, icon
from nknu.ops.nightly import DONE, RUN, STOP, clock
from nknu.ops.pipeline import BAD, LANES, LOG_FILES, NONE, OK, WARN, span, stamp, when_of
from nknu.ops.pipeline_page import AGAIN, link, strip_fixer
from nknu.ops.tonight_page import (
    CLOCK,
    SHOWN_PROBLEMS,
    STATE_ICON,
    TONIGHT_JS,
    _unexplained,
    handle_rows,
    headline,
    mark_state,
    n,
    night_word,
    part_state,
    problem_html,
    problems,
    reason_rows,
    timeline_data,
)
from nknu.ops.tonight_page import CSS as SHARED_CSS

# the nodes of 流程, in the order things flow; a node's facts are the snapshot's step of the same key, 換庫's are the
# nightly ingest's (the step that swaps)
NODES = (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("check", "品質檢查"),
         ("swap", "換庫"), ("serve", "查詢服務"))
STEP_OF_NODE = {"swap": "nightly"}
NIGHT_PARTS = (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("swap", "換庫"))
QUESTIONS = {1: "現在正不正常？學生搜到的是哪一份庫？", 2: "每一步現在什麼狀態、進多少、出多少？", 3: "有什麼不正常的？",
             4: "每晚各步花多久？進來多少、離開多少？", 5: "上次換庫，哪些網站變了？"}
TONE_WORDS = {"ok": "正常", "warn": "留意", "bad": "出錯", "run": "跑中", "none": "還沒結果"}
TONE_PILL = {"ok": "good", "warn": "warn", "bad": "bad", "run": "none", "none": "none"}
NOT_LIVE = ("還沒上線", "看不到")  # a part's word on a night the chain did not exist or cannot be read
# a probe's notes about the panel itself (how long the reading took, what lights and what does not): not the step's
NOISE = ("讀這一步花了", "「還沒抓到的」只顯示數字")
SHOWN_CHANGED = 8  # sites of the last swap listed before 還有 N 個
SPLIT_PARTS = 6  # parts of a step's split bar beside what came out: the largest reasons, the rest as one
SHOWN_NIGHT_SITES = 8
SHOWN_OLD = 5  # rows of 之前就有 in sight at the least, however many are new 【Claude】
STALE_HOURS = 30  # a library swapped in longer ago than a night and a morning is said in the warning colour


def mark(tone: str, size: int = 11) -> str:
    """A state's small round mark: an icon in the state's colour (never colour alone); a clock for what runs."""
    drawing = CLOCK.format(s=size) if tone == "run" else icon(STATE_ICON.get(tone, "dash"), size)
    return f"<span class='v3-m' data-state='{e(tone)}'>{drawing}</span>"


def section(q: int, sec_id: str, title: str, body: str, *, aside: str = "", card: bool = True) -> str:
    head = f"<div class='v3-h'><h2>{e(title)}</h2>{aside}</div>" if title else ""
    inner = f"<div class='card hm-card'>{body}</div>" if card else body
    return (f"<section class='v3-q' id='{e(sec_id)}' data-q='{q}' data-question='{e(QUESTIONS.get(q, ''))}' "
            f"aria-label='{e(title or QUESTIONS.get(q, ''))}'>{head}{inner}</section>")


def default_night(t: dict) -> dict:
    return next((x for x in t.get("nights") or [] if x.get("night") == t.get("default")), None) or {}


# ------------------------------------------------------------------------------------------------ 1 現況


def library_now(snap: dict, changes: dict | None) -> dict:
    """The library students search: {swapped (when it was swapped in, else when it was built), docs}."""
    build = next((s for s in snap.get("steps") or [] if s.get("key") == "build"), None) or {}
    new = (changes or {}).get("new") or {}
    docs = int(build["out"][0][1]) if build.get("out") else new.get("docs")
    return {"swapped": new.get("swapped_at") or build.get("at") or "", "docs": docs}


def age_words(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 1:
        return f"{max(1, round(seconds / 60))} 分鐘前"
    return f"{round(hours)} 小時前" if hours < 48 else f"{round(hours / 24)} 天前"


def paused(snap: dict) -> bool:
    return any(s.get("paused") for s in snap.get("steps") or [] if s.get("key") == "nightly")


def stuck(t: dict) -> dict | None:
    """The part of the default night that runs but has been silent (nightly's 可能卡住), None when none is."""
    return next((c for c in default_night(t).get("cells") or [] if c.get("state") == RUN and c.get("word") == "可能卡住"),
                None)


def said(t: dict, changes: dict | None, rows: list[dict], reading: bool = False, fresh: int | None = None) -> str:
    """The sentence on top: tonight_page.headline's one template; a part that runs but went silent is named in it;
    while the steps are still being read it does not say 沒有問題 (nothing is known of them yet). fresh: how many of
    the problems the night before did not have (None: no night to compare with), said after their count."""
    text = headline(t, changes, rows)
    if fresh is not None and rows and not reading and text.endswith("個問題"):
        text += f"，其中新出現 {fresh} 個" if fresh else "，都是之前就有的"
    if reading and not rows and text.endswith("；沒有問題"):
        text = text[: -len("沒有問題")] + "正在讀每一步的紀錄"
    c = stuck(t)
    if c is not None:
        text = text.replace(f"跑中（{c['name']}）", f"{c['name']}可能卡住（{c.get('say') or ''}）".replace("（）", ""), 1)
    return text


def status_section(snap: dict, t: dict, changes: dict | None, rows: list[dict], state: str, api: str,
                   now: datetime, new: int | None = None) -> str:
    reading = not snap.get("steps")
    if reading and state == OK:  # nothing read of the steps yet: no green mark for what is not known
        state = NONE
    night = default_night(t)
    word = night_word(night)[0] if night else TONE_WORDS.get(state, state)
    if stuck(t) is not None:
        word = "可能卡住"
    drawing = CLOCK.format(s=20) if state == "run" else icon(STATE_ICON.get(state, "dash"), 20)
    lib = library_now(snap, changes)
    fresh = ""
    at = when_of(lib["swapped"])
    if at is not None:
        old = (now - at).total_seconds()
        tone = " data-tone='warn'" if old > STALE_HOURS * 3600 else ""
        docs = f"<span class='hm-sep'>·</span><b class='num'>{n(lib['docs'])}</b> 份文件" if lib["docs"] else ""
        fresh = (f"<p class='hm-fresh'><span class='hm-lab'>學生搜到的庫</span><b class='num'>{e(stamp(lib['swapped']))}</b> 換上"
                 f"<span class='hm-age'{tone}>{e(age_words(old))}</span>{docs}</p>")
    pause = (f"<p class='hm-paused'>{mark('warn')}進庫暫停中：每晚只轉文字，不建庫、不換庫"
             "<span class='faint mono'>ingest.pause</span></p>") if paused(snap) else ""
    live = " data-live" if t.get("live") else ""
    body = (f"<div class='hm-now' data-tonight='{e(api)}' data-sig='{e(t.get('sig'))}'{live}>"
            f"<div class='v3-state' data-state='{e(state)}'><span class='v3-dot'>{drawing}</span><b>{e(word)}</b></div>"
            f"<div class='v3-main'><p class='v3-say'>{e(said(t, changes, rows, reading, new))}</p>{fresh}{pause}</div></div>")
    return section(1, "now", "", f"<div class='hm-pad'>{body}</div>")


# ------------------------------------------------------------------------------------------------ 2 流程


def node_tone(step: dict | None, cell: dict | None) -> str:
    """A node's state now: the worse of what its step's probe says and how its part of the latest night went; 跑中
    while that part runs; 還沒結果 when neither says anything."""
    sevs = {(step or {}).get("state"), (cell or {}).get("sev")}
    if BAD in sevs:
        return "bad"
    if WARN in sevs:
        return "warn"
    if cell and cell.get("state") == RUN:
        return "run"
    if (step or {}).get("state") == OK or (cell or {}).get("state") == DONE:
        return "ok"
    return "none"


def node_line(step: dict | None, cell: dict | None) -> tuple[str, str]:
    """A node's last line (already escaped) and its tone: its part of the latest night (when it ended and how long
    it took, how long it has been running, why it stopped), else when its step was last read. The tone is a state's
    only when the line itself says what is wrong (a part that stopped or went silent): 還沒開始 under a node whose
    step has a standing remark stays grey."""
    if cell and cell.get("word") not in NOT_LIVE:
        state = cell.get("state")
        tone = "bad" if cell.get("sev") == BAD else "warn" if cell.get("sev") == WARN else ""
        if state == RUN:
            word = part_state(cell)
            if word == "可能卡住":
                return e(f"{cell.get('say') or word}"), tone or "warn"
            since = f" data-since='{cell['since_ts']:.0f}'" if cell.get("since_ts") else ""
            return f"{e(word)} · <span{since}>{e(cell.get('when') or '')}</span>", tone or "run"
        if state == DONE:
            since, until = when_of(cell.get("since")), when_of(cell.get("until"))
            hours = f"{clock(since)}–{clock(until)}" if since and until and until > since else clock(until or since)
            return e(hours or cell.get("when") or "完成"), ""
        if state == STOP:
            return e(part_state(cell)), tone or "warn"
        return e(cell.get("word") or ""), ""
    return e(stamp((step or {}).get("at"))), ""


def chain_nodes(snap: dict, t: dict, changes: dict | None) -> list[dict]:
    """The nodes: {key, name, id, tone, figure, unit, line, line_tone, step, cell}. The seven of NODES, then any step
    of the snapshot the page has no node for (a probe added later is shown, never left out)."""
    steps = {s.get("key"): s for s in snap.get("steps") or []}
    cells = {c.get("key"): c for c in default_night(t).get("cells") or []}
    known = {STEP_OF_NODE.get(k, k) for k, _name in NODES}
    extra = [(s["key"], s.get("name") or s["key"]) for s in snap.get("steps") or [] if s.get("key") not in known]
    out = []
    for key, name in (*NODES, *extra):
        step, cell = steps.get(STEP_OF_NODE.get(key, key)), cells.get(key)
        if cell is not None and cell.get("word") in NOT_LIVE:
            cell = None
        figure, unit = (step or {}).get("figure") or "", (step or {}).get("unit") or ""
        line, line_tone = node_line(step, cell)
        if key == "swap":  # the node holds when the library in use was swapped in, not the last run's outcome
            swapped = when_of(((changes or {}).get("new") or {}).get("swapped_at"))
            built = when_of((steps.get("build") or {}).get("at"))
            if swapped is not None:
                figure, unit = clock(swapped), f"{swapped:%m-%d} 換上"
            elif built is not None:
                figure, unit = clock(built), f"{built:%m-%d} 建好"
            else:
                figure, unit = "", ""
            if (step or {}).get("paused") and not (cell and cell.get("state") in (RUN, STOP)):
                line, line_tone = "暫停中", "warn"
        out.append({"key": key, "name": name, "id": f"step-{key}", "tone": node_tone(step, cell), "figure": figure,
                    "unit": unit, "line": line, "line_tone": line_tone, "step": step, "cell": cell})
    return out


def chain_html(nodes: list[dict]) -> str:
    lis = []
    for x in nodes:
        word = TONE_WORDS.get(x["tone"], "")
        figure = (f"<span class='hm-fig num'>{e(x['figure'])}</span><span class='hm-unit'>{e(x['unit'])}</span>"
                  if x["figure"] else "<span class='hm-fig faint'>—</span><span class='hm-unit'></span>")
        lis.append(f"<li class='hm-node' data-tone='{e(x['tone'])}'><a href='#{e(x['id'])}' data-pick='{e(x['id'])}' "
                   f"aria-label='{e(x['name'])}：{e(word)}'><span class='hm-nm'>{mark(x['tone'])}<b>{e(x['name'])}</b></span>"
                   f"{figure}<span class='hm-line' data-tone='{e(x['line_tone'])}'>{x['line']}</span></a></li>")
    cols = f" style='grid-template-columns:repeat({len(nodes)},minmax(0,1fr))'" if len(nodes) != len(NODES) else ""
    return f"<ol class='hm-nodes'{cols}>{''.join(lis)}</ol>"


def split_bar(step: dict) -> str:
    """What went into a step as one bar: what came out (solid), then each reason of what did not, the largest first
    (lighter), every part with its name and count in its floating card. Only where the step's own numbers add up
    (out + reasons = in); a step whose reasons are marks, not a split (the export), has no bar."""
    bar = step.get("bar") or []
    if len(bar) != 2 or not bar[1]:
        return ""
    out, whole = int(bar[0]), int(bar[1])
    lost = sorted(((w, int(c)) for w, c in step.get("lost") or [] if c), key=lambda r: -r[1])
    if sum(c for _w, c in lost) != whole - out:
        lost = [("沒走到下一步", whole - out)] if whole > out else []
    if len(lost) > SPLIT_PARTS:
        rest = lost[SPLIT_PARTS - 1:]
        lost = [*lost[:SPLIT_PARTS - 1], (f"其他 {len(rest)} 種", sum(c for _w, c in rest))]
    noun = (step.get("out") or [["", 0]])[0][0]
    parts = [f"<i class='hm-out' style='flex:{out} 0 0' data-tip='{e(f'出 {out:,} {noun}')}'></i>"]
    parts += [f"<i style='flex:{c} 0 0' data-tip='{e(f'{w}：{c:,}')}'></i>" for w, c in lost]
    return f"<div class='hm-split' role='img' aria-label='{e(f'進 {whole:,}，出 {out:,}')}'>{''.join(parts)}</div>"


def io_html(step: dict) -> str:
    rows = [("進", c, noun) for noun, c in step.get("into") or []] + [("出", c, noun) for noun, c in step.get("out") or []]
    if not rows:
        return ""
    return ("<dl class='hm-io'>" + "".join(f"<div><dt>{e(w)}</dt><dd><b class='num'>{c:,}</b> {e(noun)}</dd></div>"
                                           for w, c, noun in rows) + "</dl>")


def link_html(step: dict) -> str:
    """和上一步對帳: how what came into the step follows from what the step before put out, a term a line (the step's
    link), and whether the terms add up (link_left). A term of nothing is not drawn."""
    rows = [r for r in step.get("link") or [] if len(r) >= 3 and (r[0] in ("", "=") or r[2])]
    if not rows:
        return ""
    left = step.get("link_left")
    verdict = ("" if left is None else "<span class='pill good'>對得上</span>" if not left
               else f"<span class='pill warn'>還差 {abs(int(left)):,} 對不上</span>")
    total = " class='hm-sum'"
    items = "".join(
        f"<li{total if r[0] == '=' else ''}{tip_attr(r[3] if len(r) > 3 else '')}>"
        f"<span class='hm-sg'>{e(r[0])}</span><span>{e(r[1])}</span><b class='num'>{int(r[2]):,}</b></li>" for r in rows)
    return f"<div class='hm-link'><h4>和上一步對帳{verdict}</h4><ul>{items}</ul></div>"


def tip_attr(tip: str) -> str:
    return f" data-tip='{e(tip)}'" if tip else ""


def sides_html(step: dict) -> str:
    """What runs inside the step on its own schedule (the step's sides): a line each, marked when it needs a look."""
    rows = [r for r in step.get("sides") or [] if len(r) >= 3]
    if not rows:
        return ""
    items = "".join(
        f"<li><b>{e(name)}</b>"
        + (f"<span class='pill {TONE_PILL[tone]}'>{e(TONE_WORDS[tone])}</span>" if tone in ("warn", "bad") else "")
        + f"<span>{e(words)}</span></li>" for name, tone, words in (r[:3] for r in rows))
    return f"<div class='hm-sides'><h4>{e(step.get('sides_label') or '這一步裡另外跑的')}</h4><ul>{items}</ul></div>"


def usage_html(step: dict) -> str:
    """The public service's use (pipeline.public_usage): a line each as the sides are, and, folded, the last searches
    that found nothing, in the words the service logged (it masks phone numbers, student numbers and addresses)."""
    rows = [r for r in step.get("usage") or [] if len(r) >= 3]
    if not rows:
        return ""
    items = "".join(
        f"<li><b>{e(name)}</b>"
        + (f"<span class='pill {TONE_PILL[tone]}'>{e(TONE_WORDS[tone])}</span>" if tone in ("warn", "bad") else "")
        + f"<span>{e(words)}</span></li>" for name, tone, words in (r[:3] for r in rows))
    missed = [m for m in step.get("usage_missed") or [] if len(m) >= 3]
    fold = ""
    if missed:
        lis = "".join(f"<li><span class='num faint'>{e(at)}</span><span class='faint'>{e(via)}</span><span>{e(q)}</span></li>"
                      for at, via, q in missed)
        fold = (f"<details class='hm-log hm-missed'><summary>{icon('right', 12)}最近查無資料的 {len(missed)} 次"
                "<span class='faint'>照服務記下的原樣，電話、學號、信箱已遮掉</span></summary>"
                f"<ul>{lis}</ul></details>")
    return (f"<div class='hm-sides hm-usage'><h4>{e(step.get('usage_label') or '對外服務')}</h4><ul>{items}</ul>"
            f"{fold}</div>")


# the logs a node's panel can show the end of (pipeline.LOG_FILES' names): asked for when the fold is opened
NODE_LOGS = {"crawl": (("update", "每晚更新"),), "export": (("export", "導出"), ("update", "每晚更新")),
             # the three lanes named as the step itself names them (pipeline.LANES, from lanes.sh's routes): written
             # here by hand at first, C was called 認圖片的字 (it converts archives; images are B's) 2026-11-21
             "totext": tuple((f"lane{k}", words.removesuffix("的程序")) for k, words in LANES),
             "build": (("build", "建庫"),), "swap": (("ingest", "每晚進庫"),),
             # the 33 questions and the real-data checks are the build's last two steps (build.sh 7, 8): its log
             "check": (("build", "建庫（兩種檢查是它最後兩步）"),)}


def logs_html(key: str) -> str:
    """The end of a node's logs, each a fold that asks for its last 50 lines when opened (HOME_JS). Until
    2026-11-21 an error said 「細節在存檔的 update.log」 and the owner had to ssh for it."""
    folds = "".join(
        f"<details class='hm-log' data-log='{e(name)}'><summary>{icon('right', 12)}紀錄最後 50 行"
        f"<span class='faint'>{e(words)} · {e(LOG_FILES[name])}</span><span class='faint' data-when></span></summary>"
        "<pre class='hm-logpre'></pre></details>" for name, words in NODE_LOGS.get(key, ()))
    return folds


def run_html(cell: dict | None) -> str:
    """A node's part of the latest night: its hours and how long, the source's own words of it."""
    if cell is None:
        return ""
    since, until = when_of(cell.get("since")), when_of(cell.get("until"))
    state = cell.get("state")
    if state == RUN and since:
        hours = f"{clock(since)} 開始，{cell.get('when') or ''}"
    elif since and until and until > since:
        hours = f"{clock(since)}–{clock(until)} · {cell.get('took') or span((until - since).total_seconds())}"
    else:
        hours = clock(until or since)
    words = [x for x in (cell.get("say"), cell.get("tip"), cell.get("aside")) if x]
    seen: list[str] = []
    for w in "\n".join(words).splitlines():  # a tip often repeats the sentence: each line once
        if w and w not in seen and not any(w in s for s in seen):
            seen.append(w)
    lines = "".join(f"<span>{e(w)}</span>" for w in seen)
    word = part_state(cell)
    return (f"<div class='hm-run'><div class='hm-run-h'><span class='hm-lab'>最近一晚</span><b>{e(word)}</b>"
            f"<span class='num'>{e(hours)}</span></div>{lines}</div>")


def node_panel(x: dict, panels: dict[str, str]) -> str:
    step, cell = x["step"] or {}, x["cell"]
    tone = x["tone"]
    head = (f"<header><h3>{e(x['name'])}</h3><span class='pill {TONE_PILL.get(tone, 'none')}'>{e(TONE_WORDS.get(tone, ''))}"
            "</span></header>")
    does = f"<p class='hm-does'>{e(step.get('does'))}</p>" if step.get("does") else ""
    say = f"<p class='hm-said'>{e(strip_fixer(step.get('say') or ''))}</p>" if step.get("say") else ""
    words = f"<p class='hm-words'>{e(step['flow_words'])}</p>" if step.get("flow_words") else ""
    lost = [[w, int(c)] for w, c in step.get("lost") or [] if c]
    reasons = (f"<h4>{e(step.get('lost_label') or '沒走到下一步的')}</h4>{reason_rows(lost)}" if lost else "")
    notes = [x_ for x_ in step.get("notes") or [] if not x_.startswith(NOISE)]
    more = (f"<details class='hm-notes'><summary>{icon('right', 12)}細節 <span class='faint'>{len(notes)}</span></summary>"
            f"<ul>{''.join(f'<li>{e(x_)}</li>' for x_ in notes)}</ul></details>") if notes else ""
    links = [link(label, panels.get(href, href)) for label, href in step.get("more") or [] if panels.get(href, href)]
    act = f"<p class='hm-links'>{''.join(links)}</p>" if links else ""
    empty = "" if step else "<p class='v3-empty'>讀不到這一步的紀錄</p>"
    return (f"<article class='hm-panel' data-panel='{e(x['id'])}'>{head}{does}{say}{run_html(cell)}"
            f"{sides_html(step)}{usage_html(step)}{io_html(step)}{link_html(step)}{split_bar(step)}{words}{reasons}{more}"
            f"{logs_html(x['key'])}{act}{empty}</article>")


# ------------------------------------------------------------------------------------------------ 4 每晚紀錄


def night_rows(t: dict, hist: dict, now: datetime) -> list[dict]:
    """A row a night, the newest first: the nights the chain ran (tonight_page.timeline_data: the last seven, each
    part's hours) and the older ones of the nights' record (nknu.ops.history: up to fourteen). {night, title, tip,
    tone, word, cells: {part: {secs, dur, tone, word, when, say}}, runs: [other ingest runs], new, changed, left,
    unexplained, docs (None where the night was not counted), sites, mark}."""
    live = {r["night"]: r for r in timeline_data(t, now)["nights"]}
    recs = {}
    if hist.get("enabled"):
        recs = {d: r for d, r in zip(hist.get("days") or [], hist.get("nights") or []) if r}
    sites = hist.get("sites") or {}
    rows = []
    for day in sorted(set(live) | set(recs), reverse=True):
        row, rec = live.get(day), recs.get(day) or {}
        cells: dict[str, dict] = {}
        runs: list[dict] = []
        for s in (row or {}).get("segs") or []:
            item = {"secs": max(0.0, (s["to"] - s["from"]) * 3600), "dur": s.get("dur") or "", "tone": s["tone"],
                    "word": s["word"], "when": s["when"], "say": s.get("say") or s.get("reason") or "", "name": s["name"]}
            if s["key"].startswith("run"):
                runs.append(item)
            else:
                cells[s["key"]] = item
        if row is None:  # an older night: the record's own steps
            for s in rec.get("steps") or []:
                a, b = when_of(s.get("start")), when_of(s.get("end"))
                secs = (b - a).total_seconds() if a and b and b > a else 0.0
                tone = "bad" if s.get("sev") == BAD else "warn" if s.get("sev") == WARN else "done"
                cells[s.get("step")] = {"secs": secs, "dur": span(secs) if secs else "", "tone": tone,
                                        "word": s.get("word") or "", "say": s.get("say") or "", "name": s.get("name") or "",
                                        "when": f"{clock(a)}–{clock(b)}" if a and b else clock(a or b)}
        state = (row or {}).get("state") or rec.get("state") or ""
        tone = {"ok": "ok", WARN: "warn", BAD: "bad", "run": "run", "missed": "bad"}.get(state, "none")
        counted = rec.get("counted_by") is not None and rec.get("new") is not None
        rows.append({"night": day, "title": (row or {}).get("title") or f"{day[5:]} 夜", "tip": (row or {}).get("tip") or "",
                     "tone": tone, "word": (row or {}).get("word") or rec.get("word") or "", "cells": cells, "runs": runs,
                     "mark": (row or {}).get("mark") or "", "counted": counted,
                     **{k: (rec.get(k) if counted else None) for k in ("new", "changed", "left", "unexplained")},
                     "docs": rec.get("docs"), "sites": sites.get(day) or []})
    return rows


def _dur_cell(c: dict | None, peak: float) -> str:
    if c is None:
        return "<td class='hm-na'>—</td>"
    tone = c["tone"]
    width = max(3.0, c["secs"] / peak * 100) if peak else 3.0
    label = c["dur"] or c["word"]
    lead = ""
    if tone != "done":  # what is not simply done says so, with its mark
        lead = mark(tone)
        label = c["word"] + (f" {c['dur']}" if c["dur"] else "")
    tip = "\n".join(x for x in (f"{c['name']} {c['when']}".strip(), c["say"]) if x)
    return (f"<td data-tone='{e(tone)}' data-tip='{e(tip)}'><span class='hm-dur'><span class='hm-tr'>"
            f"<i style='width:{width:.1f}%'></i></span><span class='hm-dl'>{lead}{e(label)}</span></span></td>")


def nights_html(rows: list[dict]) -> str:
    if not rows:
        return "<p class='v3-empty hm-pad'>還沒有紀錄：第一晚跑完以後就有</p>"
    peak = max((c["secs"] for r in rows for c in [*r["cells"].values(), *r["runs"]]), default=0.0)
    head = ("<th>夜</th>" + "".join(f"<th>{e(name)}</th>" for _k, name in NIGHT_PARTS)
            + "<th class='n'>新進</th><th class='n'>離開</th><th class='n' data-tip='離開了、建庫和面板都說不出原因的"
              "文件'>說不出原因</th>")
    trs = []
    for r in rows:
        pid = f"night-{r['night']}"
        more = ""
        if r["runs"]:
            tip = "\n".join(f"{x['name']} {x['when']}：{x['word']}" + (f"（{x['say']}）" if x["say"] else "") for x in r["runs"])
            more = f"<span class='tag' data-tip='{e(tip)}'>另 {len(r['runs'])} 次進庫</span>"
        first = (f"<th scope='row'><a href='#{e(pid)}' data-pick='{e(pid)}' data-tip='{e(r['tip'])}'>{mark(r['tone'])}"
                 f"{e(r['title'])}</a>{more}</th>")
        if not r["cells"] and not r["runs"]:
            parts = f"<td colspan='{len(NIGHT_PARTS)}' class='hm-na'>{e(r['mark'] or r['word'])}</td>"
        else:
            parts = "".join(_dur_cell(r["cells"].get(k), peak) for k, _name in NIGHT_PARTS)
        unx = r["unexplained"]
        flag = (f"<span class='v3-flag'>✕ {unx:,}</span>" if unx else "0" if unx == 0 else "—")
        nums = "".join(f"<td class='n'>{n(r[k])}</td>" for k in ("new", "left"))
        trs.append(f"<tr data-row='{e(pid)}'>{first}{parts}{nums}<td class='n'>{flag}</td></tr>")
    return (f"<div class='hm-scroll'><table class='hm-nights'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table></div>")


def record_note(hist: dict) -> str:
    """Under the table when the nights' record is not kept: why, and what that leaves (the last seven nights' steps,
    no counts of what came in and left)."""
    if hist.get("enabled"):
        return ""
    return (f"<p class='hm-note'>{mark('warn')}沒有在記每晚的紀錄：{e(hist.get('why') or '')}"
            "<span class='faint'>只看得到最近 7 晚各步的時間，沒有新進和離開的數</span></p>")


def night_panel(r: dict) -> str:
    pid = f"night-{r['night']}"
    head = (f"<header><h3>{e(r['title'])}</h3><span class='pill {TONE_PILL.get(r['tone'], 'none')}'>"
            f"{e(r['word'] or TONE_WORDS.get(r['tone'], ''))}</span></header>")
    does = f"<p class='hm-does'>{e(r['tip'])}</p>" if r["tip"] else ""
    parts = []
    for key, name in NIGHT_PARTS:
        c = r["cells"].get(key)
        if c is None:
            continue
        word = "" if c["tone"] == "done" else f"<span class='v3-word' data-tone='{e(c['tone'])}'>{e(c['word'])}</span>"
        say = f"<span class='hm-part-say'>{e(c['say'])}</span>" if c["say"] else ""
        parts.append(f"<li><b>{e(name)}</b><span class='num'>{e(c['when'])}</span>{word}{say}</li>")
    for x in r["runs"]:
        say = f"<span class='hm-part-say'>{e(x['say'])}</span>" if x["say"] else ""
        parts.append(f"<li><b>{e(x['name'])}</b><span class='num'>{e(x['when'])}</span>"
                     f"<span class='v3-word' data-tone='{e(x['tone'])}'>{e(x['word'])}</span>{say}</li>")
    steps = f"<ul class='hm-parts'>{''.join(parts)}</ul>" if parts else f"<p class='v3-empty'>{e(r['mark'] or r['word'])}</p>"
    counts = ""
    if r["counted"]:
        unx = r["unexplained"] or 0
        counts = ("<dl class='hm-io hm-counts'>"
                  + "".join(f"<div><dt>{e(w)}</dt><dd><b class='num'>{n(r[k])}</b> 份</dd></div>"
                            for w, k in (("新進", "new"), ("改了", "changed"), ("離開", "left")))
                  + (f"<div><dt>說不出原因</dt><dd><b class='num v3-flag'>✕ {unx:,}</b> 份</dd></div>" if unx else "")
                  + (f"<div><dt>庫裡文件</dt><dd><b class='num'>{n(r['docs'])}</b> 份</dd></div>" if r["docs"] else "")
                  + "</dl>")
    sites = ""
    if r["sites"]:
        order = sorted(r["sites"], key=lambda s: (-(s.get("unexplained") or 0),
                                                  -((s.get("new") or 0) + (s.get("left") or 0)), s.get("name") or s["site"]))
        lis = []
        for s in order[:SHOWN_NIGHT_SITES]:
            flag = f"<span class='v3-flag'>✕ {s['unexplained']:,}</span>" if s.get("unexplained") else ""
            lis.append(f"<li><a href='/site?site={e(quote(s['site'], safe=''))}'>{e(s.get('name') or s['site'])}</a>"
                       f"<span class='num'>+{n(s.get('new') or 0)}</span><span class='num'>−{n(s.get('left') or 0)}</span>"
                       f"{flag}</li>")
        rest = len(order) - SHOWN_NIGHT_SITES
        sites = (f"<h4>有變動的網站 <span class='faint'>{len(order)}</span></h4><ul class='hm-nsites'>{''.join(lis)}</ul>"
                 + (f"<p class='faint small'>還有 {rest} 個</p>" if rest > 0 else ""))
    return f"<article class='hm-panel' data-panel='{e(pid)}'>{head}{does}{steps}{counts}{sites}</article>"


# ------------------------------------------------------------------------------------------------ 5 上次換庫


def changes_section(changes: dict | None, t: dict) -> str:
    title = "上次換庫的變動"
    if not changes or changes.get("state") != "ready":
        words = (changes or {}).get("words") or "沒有前一份庫可以比"
        if (changes or {}).get("state") == "computing":
            words = "正在比對前後兩份庫"
        busy = " data-computing" if (changes or {}).get("state") == "computing" else ""
        return section(5, "changes", title, f"<p class='v3-empty hm-pad'{busy}>{e(words)}</p>" + _unexplained(changes, t))
    tot = changes.get("totals") or {}
    prev, new = changes.get("prev") or {}, changes.get("new") or {}
    aside = (f"<span class='faint num'>{e(stamp(prev.get('swapped_at') or prev.get('built_at')))} 的庫 → "
             f"{e(stamp(new.get('swapped_at') or new.get('built_at')))} 的庫</span>")
    unx, look = int(tot.get("unexplained") or 0), int(tot.get("look") or 0)
    figs = "".join(f"<div class='hm-figure'><span>{e(w)}</span><b class='num'>{n(int(tot.get(k) or 0))}</b></div>"
                   for w, k in (("新進", "new"), ("改了", "changed"), ("離開", "left")))
    if unx:
        figs += f"<div class='hm-figure hm-bad'><span>說不出原因</span><b class='num'>{unx:,}</b></div>"
    flagged = bool(unx or look)
    order = sorted(changes.get("sites") or [], key=lambda s: (
        -(s.get("unexplained") or 0), -((s.get("new") or 0) + (s.get("changed") or 0) + (s.get("left") or 0)),
        s.get("name") or s["site"]))

    def row(s: dict) -> str:
        flag = ""
        if flagged:
            got = [f"<span class='v3-flag'>✕ {s['unexplained']:,}</span>"] if s.get("unexplained") else []
            got += [f"<span class='v3-word' data-tone='warn'>要看 {s['look']:,}</span>"] if s.get("look") else []
            flag = f"<td class='n'>{' '.join(got)}</td>"
        return (f"<tr><td><a href='/site?site={e(quote(s['site'], safe=''))}' data-tip='{e(s['site'])}'>"
                f"{e(s.get('name') or s['site'])}</a></td>"
                + "".join(f"<td class='n'>{n(s.get(k) or 0)}</td>" for k in ("new", "changed", "left")) + flag + "</tr>")

    head = ("<tr><th>網站</th><th class='n'>新進</th><th class='n'>改了</th><th class='n'>離開</th>"
            + ("<th class='n'>說不出原因</th>" if flagged else "") + "</tr>")
    top, rest = order[:SHOWN_CHANGED], order[SHOWN_CHANGED:]
    table = f"<table class='hm-t'><thead>{head}</thead><tbody>{''.join(row(s) for s in top)}</tbody></table>"
    more = ""
    if rest:
        more = (f"<details class='hm-rest'><summary>{icon('right', 12)}還有 {len(rest)} 個網站</summary>"
                f"<table class='hm-t'><tbody>{''.join(row(s) for s in rest)}</tbody></table></details>")
    empty = "" if order else "<p class='v3-empty'>沒有網站有變動</p>"
    body = (f"<div class='hm-pad'><div class='hm-figs'>{figs}</div>{table if order else ''}{more}{empty}"
            f"{_unexplained(changes, t)}</div>")
    return section(5, "changes", title, body, aside=aside)


# ------------------------------------------------------------------------------------------------ 3 問題


def _listed(rows: list[dict], shown: int) -> str:
    """Rows of 問題, the first `shown` in sight and the rest folded (never dropped)."""
    lis = "".join(problem_html(x) for x in rows[:shown])
    rest = ""
    if len(rows) > shown:
        rest = (f"<details class='hm-rest'><summary>{icon('right', 12)}還有 {len(rows) - shown} 個</summary>"
                f"<ul class='v3-problems'>{''.join(problem_html(x) for x in rows[shown:])}</ul></details>")
    return f"<ul class='v3-problems'>{lis}</ul>{rest}"


def night_name(night: str) -> str:
    """A night as the pages name it (nightly.label_of): 「11-20 夜」."""
    return f"{night[5:]} 夜"


def split_html(parts: dict, at: str = "") -> str:
    """問題 against the night before (nknu.ops.standing.split): what is new first, then what was already listed (each
    with the nights it has been), then, folded, what the night before listed and now is gone."""
    new, old, gone = parts["new"], parts["old"], parts["gone"]
    kept = f" data-tip='那一晚的問題清單是 {e(stamp(at))} 記下的'" if at else ""
    head = (f"<h3 class='v3-problems-h hm-ph'>新出現<span class='hm-count num'{' data-new' if new else ''}>{len(new)}</span>"
            f"<span class='hm-ph-sub'{kept}>和 {e(night_name(parts['night']))}比</span></h3>")
    out = head + (_listed(new, SHOWN_PROBLEMS) if new else f"<p class='v3-none'>{mark('ok')} 沒有新出現的</p>")
    if old:
        out += (f"<h3 class='v3-problems-h hm-ph'>之前就有<span class='hm-count num'>{len(old)}</span></h3>"
                + _listed(old, max(SHOWN_OLD, SHOWN_PROBLEMS - len(new))))
    if gone:
        rows = [{"sev": OK, "where": g.get("place") or "", "href": g.get("href") or "", "state": "好了",
                 "reason": f"上一晚：{g.get('reason') or g.get('state') or ''}", "code": "", "unknown": False, "when": ""}
                for g in gone]
        out += (f"<details class='hm-rest hm-gone'><summary>{icon('right', 12)}好了的 {len(gone)} 個"
                f"<span class='hm-ph-sub'>上一晚有、現在沒有</span></summary>"
                f"<ul class='v3-problems'>{''.join(problem_html(x) for x in rows)}</ul></details>")
    return out


def problems_section(rows: list[dict], reading: bool, parts: dict | None = None, at: str = "", note: str = "") -> str:
    """問題. parts: the rows against the night before (standing.split; None: no night to compare with, the rows are
    listed as they are, and note says why); at: when that night's list was kept."""
    wait = "<p class='v3-empty hm-pad'>正在讀每一步的紀錄</p>" if reading else ""
    tail = f"<p class='hm-pnote'>{e(note)}</p>" if note else ""
    if parts is not None and (rows or parts["gone"]):
        body = f"<div class='hm-pad'>{split_html(parts, at)}</div>{wait}"
    elif not rows:  # the night's own problems are listed at once; the steps' are added when their reading is in
        body = wait or f"<div class='hm-pad'><p class='v3-none'>{mark('ok')} 沒有問題</p>{tail}</div>"
    else:
        lis = "".join(problem_html(x) for x in rows[:SHOWN_PROBLEMS])
        more = len(rows) - SHOWN_PROBLEMS
        rest = ""
        if more > 0:
            rest = (f"<details class='hm-rest'><summary>{icon('right', 12)}還有 {more} 個</summary>"
                    f"<ul class='v3-problems'>{''.join(problem_html(x) for x in rows[SHOWN_PROBLEMS:])}</ul></details>")
        body = f"<div class='hm-pad'><ul class='v3-problems'>{lis}</ul>{rest}{tail}</div>{wait}"
    count = f"<span class='hm-count num'>{len(rows)}</span>" if rows else ""
    return section(3, "problems", "問題", body, aside=count)


# ------------------------------------------------------------------------------------------------ the page


def first_pick(nodes: list[dict], nights: list[dict]) -> str:
    """What the side shows before anything is picked: the node to look at (出錯 before 留意 before the one running),
    else the latest night."""
    for tone in ("bad", "warn", "run"):
        hit = next((x for x in nodes if x["tone"] == tone and x["step"]), None)
        if hit is not None:
            return hit["id"]
    return f"night-{nights[0]['night']}" if nights else (nodes[0]["id"] if nodes else "")


# a pick shows its panel beside the row (the address keeps it: #step-build; a row of 問題 picks by its link); on a
# narrow page, where the panel sits under the row, a pick brings it into view. Without the script every panel shows.
HOME_JS = """(()=>{const root=document.querySelector('[data-home]');if(!root)return;root.classList.add('hm-js');
const panels=[...root.querySelectorAll('[data-panel]')],side=root.querySelector('.hm-side');
const show=id=>{if(!panels.some(p=>p.dataset.panel===id))return false;
panels.forEach(p=>{p.hidden=p.dataset.panel!==id});
root.querySelectorAll('[data-pick]').forEach(a=>{const on=a.dataset.pick===id;a.classList.toggle('on',on);
if(on)a.setAttribute('aria-current','true');else a.removeAttribute('aria-current')});
root.querySelectorAll('[data-row]').forEach(r=>r.classList.toggle('on',r.dataset.row===id));return true};
const near=()=>{if(side&&matchMedia('(max-width:1100px)').matches)side.scrollIntoView({block:'start',behavior:'smooth'})};
const fromHash=()=>{let id='';try{id=decodeURIComponent(location.hash.slice(1))}catch(_){}return !!id&&show(id)};
if(!fromHash())show(root.dataset.first);
addEventListener('hashchange',()=>{if(fromHash())near()});
root.addEventListener('click',ev=>{const row=ev.target.closest('[data-row]');
const a=ev.target.closest('[data-pick]')||(row&&!ev.target.closest('a,button,summary')?row.querySelector('[data-pick]'):null);
if(!a)return;ev.preventDefault();const id=a.dataset.pick;history.replaceState(null,'','#'+id);if(show(id))near()});
root.addEventListener('toggle',ev=>{const d=ev.target;if(!d.matches||!d.matches('details[data-log]')||!d.open)return;
const pre=d.querySelector('pre'),when=d.querySelector('[data-when]');pre.textContent='讀取中…';
fetch('/api/log?name='+encodeURIComponent(d.dataset.log),{cache:'no-store'}).then(r=>r.json()).then(j=>{
pre.textContent=j.lines&&j.lines.length?j.lines.join('\\n'):'這份紀錄是空的，或讀不到';pre.scrollTop=pre.scrollHeight;
if(when)when.textContent=j.at?'最後寫入 '+j.at.slice(5,16).replace('T',' '):''}).catch(()=>{pre.textContent='讀不到這份紀錄'})},true)})();"""

CSS = """
.hm{display:grid;grid-template-columns:minmax(0,1fr) 384px;gap:0 20px;align-items:start;
grid-template-areas:"chain side" "problems side" "nights side" "changes side"}
.hm>#flow{grid-area:chain}.hm>#problems{grid-area:problems}.hm>#nights{grid-area:nights}.hm>#changes{grid-area:changes}
.hm>.hm-side{grid-area:side;position:sticky;top:12px;margin-top:26px;max-height:calc(100vh - 24px);overflow:auto;
overscroll-behavior:contain}
.hm-card{overflow:hidden}
.hm-pad{padding:14px 18px}
.hm-ph{display:flex;align-items:center;gap:8px;margin:14px 0 6px}.hm-ph:first-child{margin-top:0}
.hm-ph-sub{font-size:12px;font-weight:400;color:var(--ink3)}
.hm-count[data-new]{background:var(--ink);color:var(--bg)}
.hm-gone summary .hm-ph-sub{margin-left:4px}.hm-gone li{color:var(--ink2)}.hm-gone .v3-pr{color:var(--ink2)}
.hm-pnote{margin:10px 0 0;font-size:12px;color:var(--ink3)}
.hm-count{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 7px;border-radius:999px;
background:var(--hover);color:var(--ink2);font-size:12px;font-weight:650}
.v3-h .faint.num{margin-left:auto}

.hm-now{display:flex;gap:18px;align-items:center}
.hm-fresh{margin:6px 0 0;font-size:14px;color:var(--ink2);display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px}
.hm-fresh b{color:var(--ink);font-weight:650}
.hm-lab{font-size:12px;color:var(--ink3);margin-right:2px}
.hm-age{font-size:13px;padding:0 8px;border-radius:999px;background:var(--hover);color:var(--ink2);white-space:nowrap}
.hm-age[data-tone=warn]{background:var(--warn-bg);color:var(--warn-ink);font-weight:650}
.hm-sep{color:var(--ink3)}
.hm-paused{display:inline-flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 0;padding:5px 12px;border-radius:9px;
background:var(--warn-bg);color:var(--warn-ink);font-size:13px;font-weight:650}
.hm-paused .faint{font-weight:400}

.hm-nodes{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.hm-node{position:relative;min-width:0}
.hm-node+.hm-node{border-left:1px solid var(--line)}
.hm-node+.hm-node::before{content:"";position:absolute;z-index:1;left:-5px;top:19px;width:8px;height:8px;
background:var(--surface);border-top:1.5px solid var(--line2);border-right:1.5px solid var(--line2);transform:rotate(45deg)}
.hm-node>a{display:flex;flex-direction:column;gap:1px;height:100%;padding:13px 14px 12px;text-decoration:none;
color:inherit;min-width:0;outline-offset:-2px}
.hm-node>a:hover{background:var(--hover)}
.hm-node>a.on{background:var(--sunk);box-shadow:inset 0 -3px 0 var(--act)}
.hm-nm{display:flex;align-items:center;gap:7px;font-size:14px;white-space:nowrap}
.hm-nm b{font-weight:650;overflow:hidden;text-overflow:ellipsis}
.hm-fig{margin-top:8px;font-size:20px;font-weight:700;line-height:1.2;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.hm-unit{font-size:12px;color:var(--ink2);min-height:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hm-line{margin-top:7px;font-size:12px;color:var(--ink3);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.hm-line[data-tone=warn]{color:var(--warn-ink)}.hm-line[data-tone=bad]{color:var(--bad-ink)}
.hm-line[data-tone=run]{color:var(--ink)}
.v3-m[data-state=run]{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--ink)}
.v3-m[data-state=done]{background:var(--good);color:var(--on-good)}

.hm-panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);
padding:16px 18px;display:flex;flex-direction:column;gap:12px;min-width:0;scroll-margin-top:12px}
.hm-panel[hidden]{display:none}
.hm-side>.hm-panel+.hm-panel{margin-top:12px}.hm-js .hm-side>.hm-panel{margin-top:0}
.hm-panel header{display:flex;align-items:center;gap:10px}
.hm-panel h3{font-size:16px;font-weight:650}
.hm-panel h4{margin:2px 0 -6px;font-size:12px;font-weight:650;color:var(--ink2)}
.hm-panel p{margin:0}
.hm-does{font-size:13px;color:var(--ink2);line-height:1.55}
.hm-said{font-size:14px;line-height:1.55;overflow-wrap:anywhere}
.hm-words{font-size:13px;color:var(--ink2);line-height:1.55}
.hm-run{display:flex;flex-direction:column;gap:3px;padding:9px 12px;border-radius:10px;background:var(--sunk);font-size:13px;
color:var(--ink2);overflow-wrap:anywhere}
.hm-run-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;color:var(--ink)}
.hm-io{margin:0;display:flex;flex-direction:column;gap:4px;font-size:13px}
.hm-io>div{display:grid;grid-template-columns:22px minmax(0,1fr);gap:8px;align-items:baseline}
.hm-io dt{color:var(--ink3);font-size:12px}.hm-io dd{margin:0;color:var(--ink2)}
.hm-io dd b{color:var(--ink);font-size:14px;font-weight:700}
.hm-counts>div{grid-template-columns:76px minmax(0,1fr)}
.hm-link h4,.hm-sides h4{display:flex;align-items:center;gap:8px;margin:0 0 6px}
.hm-link ul,.hm-sides ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;font-size:13px}
.hm-link ul{padding:8px 12px;border-radius:10px;background:var(--sunk);gap:3px}
.hm-link li{display:grid;grid-template-columns:12px minmax(0,1fr) max-content;gap:8px;align-items:baseline;color:var(--ink2);
line-height:1.45}
.hm-link li .hm-sg{color:var(--ink3);text-align:center}.hm-link li b{color:var(--ink);font-weight:600}
.hm-link li.hm-sum{margin-top:3px;padding-top:4px;border-top:1px solid var(--line2);color:var(--ink)}
.hm-link li.hm-sum b{font-weight:700}.hm-link li[data-tip]>span:nth-child(2){text-decoration:underline dotted var(--ink3)}
.hm-sides ul{gap:7px}
.hm-usage{margin-top:12px}.hm-missed{margin-top:8px}
.hm-missed ul{gap:4px;margin-top:6px}.hm-missed li{display:grid;grid-template-columns:auto auto minmax(0,1fr);gap:8px;
  font-size:13px;overflow-wrap:anywhere}
.hm-log summary{flex-wrap:wrap}.hm-log summary .faint{font-size:12px}
.hm-logpre{max-height:340px;overflow:auto;margin:6px 0 0;padding:10px 12px;border-radius:10px;background:var(--sunk);font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink2)}
.hm-sides li{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 8px;line-height:1.5;overflow-wrap:anywhere}
.hm-sides li>b{font-weight:650;white-space:nowrap}.hm-sides li>span:last-child{flex:1 1 100%;color:var(--ink2)}
.hm-split{display:flex;gap:2px;height:14px;border-radius:4px;overflow:hidden}
.hm-split i{min-width:2px;background:var(--amt2)}.hm-split i.hm-out{background:var(--amt)}
.hm-split i:nth-child(odd):not(.hm-out){background:var(--amt3)}
.hm-panel .rs{margin:0;max-width:none}
.hm-panel .rs li{grid-template-columns:minmax(0,1.7fr) minmax(40px,1fr) 56px;align-items:baseline}
.hm-panel .rs-w{white-space:normal;overflow:visible;line-height:1.4}
.hm-notes summary,.hm-rest summary,.hm-log summary{display:inline-flex;align-items:center;gap:6px;cursor:pointer;list-style:none;font-size:13px;
color:var(--ink2);padding:2px 0}
.hm-notes summary::-webkit-details-marker,.hm-rest summary::-webkit-details-marker,.hm-log summary::-webkit-details-marker{display:none}
.hm-notes summary .ic,.hm-rest summary .ic,.hm-log summary .ic{transition:transform .15s}
.hm-notes[open] summary .ic,.hm-rest[open] summary .ic,.hm-log[open] summary .ic{transform:rotate(90deg)}
.hm-notes ul{margin:6px 0 0;padding:0 0 0 18px;display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--ink2);
line-height:1.5;overflow-wrap:anywhere}
.hm-links{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px}
.hm-parts{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:max-content max-content minmax(0,1fr);
gap:6px 12px;font-size:13px;align-items:baseline}
.hm-parts li{display:contents}
.hm-parts li>b{font-weight:650}.hm-parts li>.num{color:var(--ink2)}
.hm-parts li>.num:last-child{grid-column:2/-1}
.hm-part-say{grid-column:2/-1;margin-top:-4px;font-size:12px;color:var(--ink3);overflow-wrap:anywhere}
.hm-nsites{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px;font-size:13px}
.hm-nsites li{display:flex;align-items:baseline;gap:10px;min-width:0}
.hm-nsites li a{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hm-nsites .num{color:var(--ink2);font-size:12px;white-space:nowrap}

.hm-scroll{overflow-x:auto}
table.hm-nights,table.hm-t{border-collapse:collapse;width:100%;font-size:13px}
.hm-nights th,.hm-nights td{padding:9px 8px;text-align:left;white-space:nowrap;border-top:1px solid var(--line);
font-weight:400}
.hm-nights thead th,.hm-t thead th{border-top:0;font-size:12px;font-weight:600;color:var(--ink2)}
.hm-nights th:first-child{padding-left:18px}.hm-nights tr>:last-child{padding-right:18px}
.hm-nights .n,.hm-t .n{text-align:right;font-variant-numeric:tabular-nums}
.hm-nights tbody tr{cursor:pointer}
.hm-nights tbody tr:hover>*{background:var(--hover)}
.hm-nights tbody tr.on>*{background:var(--sunk)}
.hm-nights tbody tr.on>th{box-shadow:inset 3px 0 0 var(--act)}
.hm-nights tbody th a{display:inline-flex;align-items:center;gap:8px;font-weight:650;text-decoration:none}
.hm-nights tbody th .tag{margin-left:8px}
.hm-na{color:var(--ink3)}
.hm-dur{display:inline-grid;grid-template-columns:44px auto;gap:7px;align-items:center}
.hm-tr{display:block;height:8px;border-radius:2px;background:var(--qx)}
.hm-tr i{display:block;height:100%;border-radius:2px;background:var(--amt);min-width:2px}
td[data-tone=run] .hm-tr i{background:none;box-shadow:inset 0 0 0 1.5px var(--ink)}
td[data-tone=warn] .hm-tr i{background:var(--warn)}td[data-tone=bad] .hm-tr i{background:var(--bad)}
.hm-dl{display:inline-flex;align-items:center;gap:5px;font-variant-numeric:tabular-nums}
td[data-tone=warn] .hm-dl{color:var(--warn-ink);font-weight:650}td[data-tone=bad] .hm-dl{color:var(--bad-ink);font-weight:650}

.hm-figs{display:flex;flex-wrap:wrap;gap:8px 36px;margin:0 0 12px}
.hm-figure{display:flex;flex-direction:column;gap:1px}
.hm-figure span{font-size:12px;color:var(--ink2)}
.hm-figure b{font-size:20px;font-weight:700;line-height:1.25}
.hm-figure.hm-bad b{color:var(--bad-ink)}
table.hm-t{max-width:640px}
.hm-t th,.hm-t td{padding:7px 0 7px 16px;text-align:left;border-top:1px solid var(--line);white-space:nowrap}
.hm-t .n{width:64px;min-width:64px}
.hm-t tr>:first-child{padding-left:0;white-space:normal}
.hm-t td a{text-decoration:none;font-weight:600}.hm-t td a:hover{text-decoration:underline}
.hm-t tbody tr:hover td{background:var(--hover)}
.hm-rest{margin-top:8px}.hm-rest table{margin-top:4px}.hm-rest .hm-t .n{min-width:44px}
.hm-foot{margin:18px 2px 0;font-size:12px;color:var(--ink3)}
.hm-note{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;margin:0;padding:10px 18px;border-top:1px solid var(--line);
font-size:13px}
.v3-none{display:flex;align-items:center;gap:8px}
@media (prefers-reduced-motion:reduce){.hm-notes summary .ic,.hm-rest summary .ic{transition:none}}

@media (max-width:1100px){
.hm{grid-template-columns:minmax(0,1fr);grid-template-areas:"chain" "side" "problems" "nights" "changes"}
.hm>.hm-side{position:static;max-height:none;margin-top:12px;overflow:visible;scroll-margin-top:12px}
}
@media (max-width:900px){
.hm-nodes{grid-template-columns:minmax(0,1fr)!important}
.hm-node+.hm-node{border-left:0;border-top:1px solid var(--line)}
.hm-node+.hm-node::before{left:22px;top:-5px;transform:rotate(135deg)}
.hm-node>a{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"nm fig" "line unit";gap:2px 12px;
padding:11px 16px}
.hm-nm{grid-area:nm}.hm-fig{grid-area:fig;margin:0;font-size:16px;text-align:right}
.hm-unit{grid-area:unit;text-align:right;min-height:0}.hm-line{grid-area:line;margin:0}
.hm-node>a.on{box-shadow:inset 3px 0 0 var(--act)}
.hm-now{align-items:flex-start;gap:12px}
.hm-pad{padding:12px 14px}
.hm-nights th:first-child{position:sticky;left:0;background:var(--surface);padding-left:14px;z-index:1}
}
"""


def home_body(snap: dict, t: dict, changes: dict | None, hist: dict, panels: dict[str, str], all_href: str = "",
              now: datetime | None = None, base: dict | None = None, base_note: str = "") -> str:
    """The page under the top bar (the module docstring). snap: pipeline.Pipeline.snapshot; t: nightly.tonight;
    changes: changes.Changes.snapshot; hist: history.home_history; panels: where a step's links go by their short
    names; all_href: 網站 (every site); base: the list of problems kept for the night before the one the page opens
    on (history.Store.problem_base; None: none kept, and base_note says so under 問題)."""
    if now is None:
        now = datetime.fromisoformat(t["now"]) if t.get("now") else datetime.now().astimezone()
    reading = not snap.get("steps")
    handle = [] if reading else handle_rows(snap, changes)
    rows = problems(t, snap, handle)
    state = mark_state(t, rows)
    parts = standing.split(rows, base, reading=reading, swap_known=(changes or {}).get("state") != "computing")
    where = {**panels, "#sites": all_href}  # 「看每站的數字」: every site is listed on 網站
    nodes = chain_nodes(snap, t, changes)
    nights = night_rows(t, hist, now)
    side = "".join(node_panel(x, where) for x in nodes) + "".join(night_panel(r) for r in nights)
    flow = section(2, "flow", "流程", chain_html(nodes))
    when = ""
    if snap.get("at"):
        when = (f"<p class='hm-foot'>各步驟的紀錄每 5 分鐘讀一次，這次 {e(stamp(snap.get('at')))}"
                + ("（正在重新讀）" if snap.get("running") else " · <a href='/?fresh=1'>現在重讀</a>") + "</p>")
    again = f"<script>{AGAIN.replace('{ms}', '5000')}</script>" if reading or snap.get("running") else ""
    return (f"<style>{charts.CSS}{SHARED_CSS}{CSS}</style>"
            + status_section(snap, t, changes, rows, state, "/api/tonight?brief=1", now,
                             len(parts["new"]) if parts is not None else None)
            + f"<div class='hm' data-home data-first='{e(first_pick(nodes, nights))}'>{flow}"
            + f"<aside class='hm-side' aria-label='選中的步驟或夜晚'>{side}</aside>"
            + problems_section(rows, reading, parts, (base or {}).get("at") or "", base_note)
            + section(4, "nights", "每晚紀錄", nights_html(nights) + record_note(hist))
            + changes_section(changes, t) + "</div>" + when
            + again + f"<script>{TONIGHT_JS}</script><script>{HOME_JS}</script>")
