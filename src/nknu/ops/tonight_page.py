"""The data and the shared pieces of 每晚運行 (nknu.ops.home_page, the home of 檢視台) and of a site's page (nknu.ops.
site_page), read from nknu.ops.pipeline's snapshot, nknu.ops.nightly's night and nknu.ops.history's record:

  problems, handle_rows   every step or site that is not normal, once, the worse first; the sites to look at
  headline, mark_state    the one line on top in its fixed template and the one state (spec 2026-11-17-panels-v3-
                          questions.md 八.4)
  timeline_data           each night's parts as segments of the night's clock (every ingest run of the night)
  nightbars_data/_table   the fourteen nights of the record: what came in, changed and left
  funnel_data/_html, reason_rows, reconcile_words   a step's in and out on its own terms and its reasons as bars (a
                          site's page draws them)
  build_items             the build's own list of what left for no reason it could give

Until 2026-11-18 this module also drew the home as five sections (panels v3); the owner then handed the panels over
(「之前的session没有做好」) and the home is home_page's. Words are Traditional Chinese, Taiwan usage; nobody is named to
fix anything (spec 八.2)."""

from __future__ import annotations

import math
from datetime import date, datetime
from urllib.parse import quote

from nknu.ops import charts
from nknu.ops.history import hours_of
from nknu.ops.look import e, icon
from nknu.ops.nightly import DONE, NIGHT_STEP, RUN, STOP, clock, night_href
from nknu.ops.pipeline import BAD, NONE, OK, WARN, span, stamp, when_of
from nknu.ops.pipeline_page import strip_fixer

# the four steps of 流水線, each on its own terms (a cold reader of the lab's page, 11-17, read four bars of four units
# on one scale as one quantity flowing, and 導出 6,028 > 抓到 5,898 as a mistake): the step, what is said of what it
# did not put out, the unit of what went in, the word and unit of what came out 【Claude】
FUNNEL = (("crawl", "爬取", "沒抓到", "個網址", "出", "個網址"), ("export", "導出", "沒存檔", "筆", "有檔", "筆"),
          ("totext", "轉文字", "沒轉成文字", "個檔", "出", "個檔"), ("build", "建庫", "沒成為文件", "筆", "出", "份文件"))
PIPE_TIP = "這是到現在為止的總數（正式庫和爬蟲帳本），不是哪一晚的；每晚的變化看「每晚運行」的「每晚紀錄」"
STEP_OF_COL = {"crawl": "爬取", "check": "爬取", "export": "導出", "refill": "導出", "text": "轉文字", "docs": "建庫"}
STEP_OF_CELL = {"crawl": "crawl", "export": "export", "totext": "totext", "build": "build", "swap": "build"}
SHOWN_REASONS = 8  # reasons a drop lists before 其他 (spec 四.3)
SHOWN_NIGHT_SITES = 8  # sites listed for a night picked on the bars 【Claude】
SHOWN_UNEXPLAINED = 10  # documents of 查不出原因 listed 【Claude】
STATE_ICON = {OK: "ok", WARN: "warn", BAD: "no", NONE: "dash", "run": "dash"}
# a clock: the mark of what runs (look has no such drawing; currentColor only)
CLOCK = ("<svg class='ic' viewBox='0 0 16 16' width='{s}' height='{s}' fill='none' stroke='currentColor' "
         "stroke-width='1.6' stroke-linecap='round' aria-hidden='true'><circle cx='8' cy='8' r='5.6'/>"
         "<path d='M8 4.8V8l2.2 1.4'/></svg>")


def n(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def handle_rows(snap: dict, changes: dict | None) -> list[dict]:
    """The sites to look at, the most urgent first: every site whose row in 流水線's table has a cell to look at (its
    issues: the step, the source's own reason as the probe wrote it), and every site the last swap's comparison found
    documents leaving for no reason (or maybe ours). {key, name, href, sev, issues: [{sev, what, step, kind}],
    unexplained, docs}; kind: the cell's column, or unexplained / look of the swap (what the issue is from one night to
    the next: nknu.ops.standing). Nobody is named to fix anything (spec 2026-11-17-panels-v3-questions.md 八.2)."""
    rows: dict[str, dict] = {}
    for r in snap.get("sites") or []:
        issues = r.get("issues")
        if issues is None:  # a reading from before issues were kept: the marked cells, with the sentences in order
            whys = list(r.get("why") or [])
            issues = [{"col": col, "state": c.get("state"), "why": whys.pop(0) if whys else ""}
                      for col, c in (r.get("cells") or {}).items() if c.get("state") in (WARN, BAD)]
        for it in issues:
            step = STEP_OF_COL.get(it.get("col"), "")
            what = strip_fixer(it.get("why") or "") or f"{step}的數字不正常"
            row = rows.setdefault(r["key"], {"key": r["key"], "name": r.get("name") or r["key"],
                                             "href": r.get("href") or "/site?site=" + quote(r["key"], safe=""),
                                             "issues": [], "unexplained": 0,
                                             "docs": ((r.get("cells") or {}).get("docs") or {}).get("n") or 0})
            row["issues"].append({"sev": it.get("state") or WARN, "what": what, "step": step,
                                  "kind": it.get("col") or "cell"})
    if changes and changes.get("state") == "ready":
        for s in changes.get("sites") or []:
            found = []
            if s.get("unexplained"):
                found.append({"sev": BAD, "what": f"上次換庫說不出原因地離開 {s['unexplained']:,} 份", "step": "換庫",
                              "kind": "unexplained"})
            if s.get("look"):
                found.append({"sev": WARN, "what": f"上次換庫可能是我們這邊漏掉的 {s['look']:,} 份", "step": "換庫",
                              "kind": "look"})
            if not found:
                continue
            row = rows.setdefault(s["site"], {"key": s["site"], "name": s.get("name") or s["site"],
                                              "href": "/site?site=" + quote(s["site"], safe=""), "issues": [],
                                              "unexplained": 0, "docs": 0})
            row["issues"] = found + row["issues"]
            row["unexplained"] = int(s.get("unexplained") or 0)
    rank = {BAD: 0, WARN: 1}
    for row in rows.values():
        row["issues"].sort(key=lambda it: rank.get(it["sev"], 2))
        row["sev"] = row["issues"][0]["sev"] if row["issues"] else OK
    return sorted(rows.values(), key=lambda r: (rank.get(r["sev"], 2), -r["unexplained"], -r["docs"], r["name"]))


# a night's parts: their state in the fixed words (spec 2026-11-17-panels-v3-questions.md 八.1)
SHOWN_PROBLEMS = 12  # rows of 問題 before 還有 N 個 【Claude】
# the steps that are no part of a night, and where each shows once /tonight sends on to the night view (critic CR-05's
# proposal, 【Claude】 until the owner places them): 查詢服務 (the services and the machine) on 總覽's 網站訪問量
# (visits.py, #usage), 品質檢查 (the 33 by hand since 11-22, the real-data checks after an install) on 搜尋's 測試
ELSEWHERE = {"serve": "/#usage", "check": "/try#tests"}
PART_STATE = {RUN: "跑中", DONE: "完成", STOP: "停了"}


def part_state(c: dict) -> str:
    """A part's state in the fixed words: 沒跑, 跑中, 完成, 停了, 出錯 (and 沒見過 for a code nobody knows)."""
    if c.get("unknown"):
        return "沒見過"
    if c.get("word") in ("沒跑", "沒見過", "可能卡住"):
        return c["word"]
    if c.get("state") == STOP and c.get("sev") == BAD:
        return "出錯"
    return PART_STATE.get(c.get("state"), c.get("word") or "")


def problems(t: dict, snap: dict, handle: list[dict]) -> list[dict]:
    """Every step or site that is not normal, one row each ({sev, where, href, state, reason, code, unknown, when,
    issues: [{key, sev, what}]: what the row is from one night to the next, a site's row one for each of its issues),
    the worse first (spec 八.5): the night's parts that stopped or warn (their source's own reason: a status file's
    reason or error, a log line, as written; a code no dictionary knows marked 沒見過), every run of the night that
    did not end well, 流水線's steps that are not normal (what each probe says), and the sites to look at. Nothing
    here is written for one night: the rows are what the sources say. A part of the night, its run or a step of the
    chain links to the night view with that step open (nightly.night_href, 11-23: the night is 內容's 定時更新); a
    step of no night links where it shows (ELSEWHERE: 查詢服務 to 總覽's 網站訪問量, 品質檢查 to 搜尋's 測試, CR-05)."""
    out: list[dict] = []
    night = next((x for x in t.get("nights") or [] if x.get("night") == t.get("default")), None) or {}

    def here(step: str | None) -> str:
        key = night.get("night") or t.get("default")
        return night_href(key, step) if key else "#nights"

    covered: set[str] = set()
    for c in night.get("cells") or []:
        if c.get("word") in ("還沒上線", "看不到"):
            continue
        if c.get("state") == STOP or c.get("sev") in (WARN, BAD) or c.get("unknown"):
            covered.add(STEP_OF_CELL.get(c.get("key"), ""))
            sev = BAD if c.get("sev") == BAD else WARN
            reason = c.get("reason") or c.get("say") or ""
            key = f"part:{c.get('key')}" + (f":{c['code']}" if c.get("code") else "")
            out.append({"sev": sev, "where": c["name"], "href": here(c.get("key")), "state": part_state(c),
                        "reason": reason, "code": c.get("code") or "",
                        "unknown": bool(c.get("unknown")), "when": stamp(c.get("until") or c.get("since")),
                        "issues": [{"key": key, "sev": sev, "what": reason}]})
    for x in night.get("runs") or []:  # a run that did not end well, beside the one the parts are drawn from
        if x.get("shown") or x.get("outcome") in ("swapped", "nothing_new", "running"):
            continue
        sev = BAD if x.get("outcome") == "failed" else WARN
        out.append({"sev": sev, "where": f"進庫第 {x['run']} 次",
                    "href": here("build"), "state": "沒見過" if x.get("unknown") else "出錯" if x.get("outcome") == "failed"
                    else "停了", "reason": x.get("reason") or "", "code": x.get("code") or "",
                    "unknown": bool(x.get("unknown")), "when": stamp(x.get("until")),
                    # one run of one night: an event, never the same problem the night after
                    "issues": [{"key": f"run:{night.get('night')}:{x['run']}", "sev": sev, "what": x.get("reason") or ""}]})
    for s in snap.get("steps") or []:
        if s.get("key") == "nightly" or s.get("state") not in (WARN, BAD) or s.get("key") in covered:
            continue
        reason = strip_fixer(s.get("say") or "")
        out.append({"sev": s["state"], "where": s["name"],
                    "href": here(s["key"]) if s.get("key") in NIGHT_STEP else ELSEWHERE.get(s["key"], f"#step-{s['key']}"),
                    "state": "出錯" if s["state"] == BAD else "留意", "reason": reason,
                    "code": "", "unknown": False, "when": stamp(s.get("at")),
                    "issues": [{"key": f"step:{s['key']}", "sev": s["state"], "what": reason}]})
    for r in handle:
        it = r["issues"][0]
        issues: dict[str, dict] = {}  # two cells of one step (抓到, 漏抓的網頁) are two issues: the cell names it
        for x in r["issues"]:
            issues.setdefault(f"site:{r['key']}:{x.get('kind') or x.get('step') or ''}",
                              {"sev": x["sev"], "what": x["what"]})
        out.append({"sev": r["sev"], "where": r["name"], "href": r["href"], "state": "出錯" if r["sev"] == BAD else "留意",
                    "reason": it["what"] + (f"（還有 {len(r['issues']) - 1} 件）" if len(r["issues"]) > 1 else ""),
                    "code": "", "unknown": False, "when": "",
                    "issues": [{"key": k, **v} for k, v in issues.items()]})
    rank = {BAD: 0, WARN: 1}
    return sorted(out, key=lambda x: rank.get(x["sev"], 2))


def night_word(night: dict) -> tuple[str, str]:
    """(the night's last state in the fixed words, the part it is at) from its parts: 跑中, 沒跑, 出錯, 停了, 完成,
    還沒開始, 還沒上線."""
    live = [c for c in night.get("cells") or [] if c.get("word") not in ("還沒上線", "看不到")]
    if not live:
        return "還沒上線", ""
    run = next((c for c in live if c.get("state") == RUN), None)
    if run is not None:
        return "跑中", run["name"]
    if live[0].get("word") == "沒跑":
        return "沒跑", ""
    stops = [c for c in live if c.get("state") == STOP]
    bad = next((c for c in stops if c.get("sev") == BAD), None)
    if bad is not None:
        return "出錯", bad["name"]
    if stops:
        return ("沒見過" if any(c.get("unknown") for c in stops) else "停了"), stops[0]["name"]
    if all(c.get("state") == DONE for c in live):
        return "完成", ""
    if not any(c.get("state") == DONE for c in live):
        return "還沒開始", ""
    return "跑中", ""


def headline(t: dict, changes: dict | None, rows: list[dict]) -> str:
    """The one line on top, one template (spec 八.4): 「{哪一晚}：{最後的狀態}；新進 N 份、離開 M 份（說不出原因 K
    份）；有 P 個問題」; the counts only when that night's swap was compared (K: the build's own check when it made
    one)."""
    night = next((x for x in t.get("nights") or [] if x.get("night") == t.get("default")), None) or {}
    word, at = night_word(night)
    head = f"{night.get('label') or t.get('default') or ''}：{word}" + (f"（{at}）" if at else "")
    counts = ""
    if changes and changes.get("state") == "ready" and changes.get("night") == night.get("night"):
        tot = changes.get("totals") or {}
        dep = night.get("departures")
        unx = dep["unexplained"] if dep else int(tot.get("unexplained") or 0)
        counts = f"；新進 {int(tot.get('new') or 0):,} 份、離開 {int(tot.get('left') or 0):,} 份（說不出原因 {unx:,} 份）"
    return head + counts + (f"；有 {len(rows)} 個問題" if rows else "；沒有問題")


def mark_state(t: dict, rows: list[dict]) -> str:
    """The one state on top: 出錯 or 留意 when any problem is; else 跑中 while the night runs; else 正常."""
    sevs = {x["sev"] for x in rows}
    if BAD in sevs:
        return BAD
    if WARN in sevs:
        return WARN
    night = next((x for x in t.get("nights") or [] if x.get("night") == t.get("default")), None) or {}
    return "run" if night_word(night)[0] == "跑中" else OK


def problem_html(x: dict) -> str:
    code = (f"<span class='mono faint v3-code'>{e(x['code'])}</span>" if x.get("code") else "")
    seen = "<span class='v3-unseen' data-tip='這個代碼面板沒見過：照原樣列出，不當成正常'>沒見過</span>" if x.get("unknown") else ""
    where = f"<a href='{e(x['href'])}'>{e(x['where'])}</a>" if x.get("href") else e(x["where"])
    # since which night it has been listed (nknu.ops.standing), and whether it got worse since the night before
    age = (f"<span class='v3-age' data-tip='{e(x.get('age_tip') or '')}'>{e(x['age'])}</span>" if x.get("age") else "")
    worse = "<span class='v3-worse' data-tip='上一晚是留意，現在是出錯'>變嚴重</span>" if x.get("worse") else ""
    return (f"<li data-sev='{e(x['sev'])}'><span class='v3-m' data-state='{e(x['sev'])}'>"
            f"{icon(STATE_ICON.get(x['sev'], 'dash'), 11)}</span><span class='v3-pw'>{where}</span>"
            f"<span class='v3-ps'>{e(x['state'])}</span><span class='v3-pr'>{e(x['reason'])}{code}{seen}{worse}{age}</span>"
            f"<span class='v3-pt num'>{e(x['when'])}</span></li>")


def _tone(c: dict) -> str:
    sev = c.get("sev") or ""
    if sev == BAD:
        return "bad"
    if sev == WARN:
        return "warn"
    return "run" if c.get("state") == RUN else "done"



def nightly_words(outcome: str | None) -> str:
    """An ingest run's outcome in the fixed words of the parts."""
    return {"failed": "出錯", "swapped": "完成", "nothing_new": "完成", "running": "跑中"}.get(str(outcome or ""), "停了")


def minutes_words(seconds: float) -> str:
    """A step's time as its label says it: 4 分, 51 分, 1 小時 38 分."""
    m = max(0, round(seconds / 60))
    if m < 1:
        return "不到 1 分"
    return f"{m} 分" if m < 60 else f"{m // 60} 小時 {m % 60} 分"


def timeline_data(t: dict, now: datetime) -> dict:
    """The nights since the chain went live (at most seven), the newest first, each part of the chain that has a time
    as a segment in hours after the night's midnight (a part running is drawn to now), labelled with its name and how
    long it took. Every ingest run of the night is drawn (spec 2026-11-17-panels-v3-questions.md 八.3): the one the
    parts are read from as 轉文字, 建庫, 換庫, every other as one 進庫 segment in its own state, its run's own reason in
    the floating card; segments carry their run's number (run). A night with no segment says why (沒跑, 還沒開始).
    The clock runs 00:00 to 08:00, longer when a part ran later (at most 12:00)."""
    rows, top = [], 8.0
    for night_ in reversed(t.get("nights") or []):
        if night_.get("state") == "off":  # before the nightly chain existed: no row (owner 11-17, a cold reader)
            continue
        night = date.fromisoformat(night_["night"])
        segs = []
        for c in night_.get("cells") or []:
            since, until = when_of(c.get("since")), when_of(c.get("until"))
            if c.get("state") == RUN:
                until = now
            if since is None and until is None:
                continue
            start, end = since or until, until or since
            a, b = hours_of(night, start), hours_of(night, end)
            took = c.get("took") or (span((end - start).total_seconds()) if end and start and end > start else "")
            say = "\n".join(x for x in (c.get("say"), c.get("aside")) if x)  # a build by hand beside the night's
            dur = minutes_words((end - start).total_seconds()) if end > start else ""
            segs.append({"key": c["key"], "name": c["name"], "word": part_state(c), "say": say,
                         "reason": c.get("reason") or "", "code": c.get("code") or "",
                         "tone": _tone(c), "from": round(max(0.0, a), 3),
                         "to": round(max(0.0, a, b), 3), "label": f"{c['name']} {dur}" if dur else c["name"],
                         "dur": dur,
                         "when": f"{clock(start)}–{clock(end)}" + (f"（{took}）" if took else "")})
            top = max(top, b)
        runs = night_.get("runs") or []
        shown = next((x["run"] for x in runs if x.get("shown")), None)
        for seg in segs:
            if seg["key"] in ("totext", "build", "swap") and shown is not None:
                seg["run"] = shown
        for x in runs:
            if x.get("shown"):
                continue
            start, end = when_of(x["since"]), when_of(x["until"])
            a, b = hours_of(night, start), hours_of(night, end)
            outcome = x.get("outcome")
            tone = ("bad" if outcome == "failed" else "done" if outcome in ("swapped", "nothing_new") and not
                    x.get("unknown") else "run" if outcome == "running" else "warn")
            segs.append({"key": f"run{x['run']}", "name": "進庫", "run": x["run"],
                         "word": "沒見過" if x.get("unknown") else nightly_words(outcome),
                         "say": "\n".join(v for v in (x.get("reason"), x.get("code")) if v), "reason": x.get("reason") or "",
                         "code": x.get("code") or "", "tone": tone, "from": round(max(0.0, a), 3),
                         "to": round(max(0.0, a, b), 3), "dur": minutes_words((end - start).total_seconds()),
                         "label": f"進庫 {minutes_words((end - start).total_seconds())}",
                         "when": f"{clock(start)}–{clock(end)}"})
            top = max(top, b)
        segs.sort(key=lambda x: (x["from"], x["to"]))
        title = night_.get("label") or night_["date"]
        mark = ""
        if not segs:
            mark = {"missed": "✕ 沒跑", "off": "還沒上線", "wait": "還沒開始"}.get(night_.get("state"),
                                                                          night_.get("word") or "")
        rows.append({"night": night_["night"], "title": title, "tip": night_.get("tip") or "", "state": night_.get("state"),
                     "word": night_.get("word"), "segs": segs, "mark": mark,
                     "markTone": "bad" if night_.get("state") == "missed" else ""})
    top_h = min(12, max(8, math.ceil(top)))
    for row in rows:
        segs = row["segs"]
        for s in segs:
            s["from"], s["to"] = min(s["from"], top_h), min(s["to"], top_h)
        for k, s in enumerate(segs):
            s["gap_before"] = round(max(0.0, s["from"] - (segs[k - 1]["to"] if k else 0.0)), 3)
            s["gap_after"] = round(max(0.0, (segs[k + 1]["from"] if k + 1 < len(segs) else top_h) - s["to"]), 3)
    return {"nights": rows, "max": top_h}


def source_words(rec: dict) -> str:
    """Where a night's numbers come from, for the floating card: the unexplained from the build's own check (and
    what the panels' comparison counted when it differs), new and changed from the comparison or the ingest line."""
    by, other = rec.get("unexplained_by"), rec.get("unexplained_other")
    counts = {"changes": "新進、改了、離開：面板比對前後兩份庫", "ingest": "新進、離開：每晚進庫那一行"}.get(
        rec.get("counted_by") or "", "")
    unx = ""
    if by == "build":
        unx = "說不出原因的：建庫自己核對的" + (f"（面板比對的是 {other:,}）" if isinstance(other, int) else "")
    return "；".join(x for x in (counts, unx) if x)


def nightbars_data(hist: dict, labels: dict[str, str]) -> dict:
    """The fourteen nights of the record: what came in, changed and left, the unexplained, the documents; a night
    with no counts is drawn empty and says why; per night its sites with something new or gone."""
    nights = []
    for day, rec in zip(hist.get("days") or [], hist.get("nights") or []):
        counted = rec is not None and rec.get("counted_by") is not None and rec.get("new") is not None
        why = "沒有紀錄" if rec is None else "" if counted else "這一晚沒有算進出"
        # every night named by its evening (11-16 夜), never 今晚／昨晚 (owner 11-17)
        nights.append({"night": day, "date": day[5:], "label": "夜", "counted": counted, "why": why,
                       **{k: (rec or {}).get(k) for k in ("new", "changed", "left", "unexplained", "look", "docs")},
                       "state": (rec or {}).get("state"), "word": (rec or {}).get("word"),
                       "source": source_words(rec) if counted else ""})
    sites = {}
    for day, rows in (hist.get("sites") or {}).items():
        ordered = sorted(rows, key=lambda s: (-(s.get("unexplained") or 0), -((s.get("new") or 0) + (s.get("left") or 0)),
                                              s.get("name") or s["site"]))
        sites[day] = [{**s, "href": "/site?site=" + quote(s["site"], safe="")} for s in ordered]
    return {"nights": nights, "sites": sites, "shown": SHOWN_NIGHT_SITES}


def night_departures(t: dict | None) -> dict | None:
    """The build's own check of the night the page opens on (nightly.departures_of), None without one."""
    if not t:
        return None
    night = next((x for x in t.get("nights") or [] if x.get("night") == t.get("default")), None) or t.get("shown")
    return (night or {}).get("departures")


def build_items(dep: dict, site: str = "", *, names: dict[str, str] | None = None) -> str:
    """The build's own list of what left for no reason it could give (≤ 50, nightly_ingest's departures.items):
    the site, the title (its address when it has none) opening 文件追蹤 on that address, why in words, the detail in
    the floating card; site: only that site's ('' when it has none)."""
    items = [x for x in dep.get("items") or [] if not site or x.get("site") == site]
    if not items:
        return ""
    words = dep.get("words") or {}
    lis = []
    for x in items[:SHOWN_UNEXPLAINED * 5]:
        url = str(x.get("url") or "")
        title = str(x.get("title") or "") or url
        tip = "\n".join(v for v in (url, str(x.get("detail") or "")) if v)
        where = "" if site else (f"<a href='/site?site={e(quote(str(x.get('site') or ''), safe=''))}'>"
                                 f"{e((names or {}).get(x.get('site'), x.get('site') or ''))}</a>")
        why = words.get(x.get("why"), str(x.get("why") or ""))
        lis.append(f"<li>{where}<a class='v3-doc' href='/trace?ref={e(quote(url, safe=''))}' data-tip='{e(tip)}'>"
                   f"{e(title)}</a><span class='faint v3-why'>{e(why)}</span></li>")
    total = dep.get("unexplained") if not site else (dep.get("by_site") or {}).get(site, len(items))
    more = (total or 0) - len(lis)
    return (f"<div class='v3-panel v3-bad'><h3><span class='v3-flag'>✕ 說不出原因地離開</span> {total or len(items):,} 份"
            f"<span class='faint' data-tip='建庫換庫前自己逐份核對的（nightly_ingest 的 departures）'>建庫自己核對的</span>"
            f"</h3><ul class='v3-docs v3-items'>{''.join(lis)}</ul>"
            + (f"<div class='faint small'>還有 {more:,} 份沒列出來</div>" if more > 0 else "") + "</div>")


def _unexplained(changes: dict | None, t: dict | None = None) -> str:
    """What left for no reason: the build's own list when it made one (nightly_ingest's departures), else the
    panels' comparison of the two libraries (changes.py)."""
    dep = night_departures(t)
    if dep is not None:
        return build_items(dep)
    if not changes or changes.get("state") != "ready" or not changes.get("unexplained"):
        return ""
    tot = (changes.get("totals") or {}).get("unexplained") or len(changes["unexplained"])
    lis = []
    for d in changes["unexplained"][:SHOWN_UNEXPLAINED]:
        title = d.get("title") or d.get("url") or d.get("id")
        tip = "\n".join(x for x in (d.get("url"), d.get("id"), d.get("note")) if x)
        lis.append(f"<li><a href='/site?site={e(quote(d.get('site') or '', safe=''))}'>{e(d.get('site_name') or '')}"
                   f"</a><a href='/check?ref={e(quote(d.get('url') or '', safe=''))}' data-tip='{e(tip)}'>{e(title)}</a></li>")
    more = tot - min(len(changes["unexplained"]), SHOWN_UNEXPLAINED)
    return (f"<div class='v3-panel v3-bad'><h3><span class='v3-flag'>✕ 查不出原因</span> {tot:,} 份"
            f"</h3><ul class='v3-docs'>{''.join(lis)}</ul>"
            + (f"<div class='faint small'>還有 {more:,} 份（全部在 /api/tonight）</div>" if more > 0 else "") + "</div>")


def nightbars_table(data: dict) -> str:
    rows = []
    for x in reversed(data["nights"]):
        if not x["counted"]:
            rows.append([e(x["date"] + (" " + x["label"] if x["label"] else "")), "", "", "", "",
                         f"<span class='faint'>{e(x['why'])}</span>"])
            continue
        rows.append([e(x["date"] + (" " + x["label"] if x["label"] else "")), n(x["new"]), n(x["changed"]), n(x["left"]),
                     (f"<span class='v3-flag' data-tip='{e(x.get('source') or '')}'>✕ {x['unexplained']:,}</span>"
                      if x["unexplained"] else "0"),
                     n(x["docs"])])
    return charts.table(["晚上", "新進", "改了", "離開", "查不出原因", "庫裡文件"], rows, nums=(1, 2, 3, 4, 5))


def flow_words(into: int | None, value: int | None, unit_in: str, out_word: str, unit_out: str) -> str:
    """「進 114,643 個網址 → 出 105,916 個網址」: one step's in and out, each in its own unit."""
    out = f"{out_word} {n(value)} {unit_out}"
    return f"進 {n(into)} {unit_in} → {out}" if into is not None else out


def funnel_data(snap: dict) -> list[dict]:
    """The four steps from pipeline.py's steps, each on its own terms: what went in (into, the step's own) and what
    came out (value), the share kept (value ÷ into), what it did not put out (drop: into − value), in words with its
    units (flow), and its reasons (the step's lost, largest first, each with who can fix it)."""
    steps = {s.get("key"): s for s in snap.get("steps") or []}
    out = []
    for key, name, drop_word, unit_in, out_word, unit_out in FUNNEL:
        s = steps.get(key)
        base = {"key": key, "name": name, "drop_word": drop_word, "unit": unit_in, "unit_out": unit_out}
        if not s or not s.get("out"):
            out.append({**base, "into": None, "value": None, "share": None, "drop": None, "flow": "", "lost": [],
                        "lost_label": "", "tip": "讀不到這一步的紀錄", "state": (s or {}).get("state", NONE)})
            continue
        value = int(s["out"][0][1])
        into = int(s["into"][0][1]) if s.get("into") else None
        tip = "\n".join([f"進 {c:,} {noun}" for noun, c in s.get("into") or []]
                        + [f"出 {c:,} {noun}" for noun, c in s.get("out") or []]
                        + ([s["flow_words"]] if s.get("flow_words") else []))
        out.append({**base, "into": into, "value": value, "share": value / into if into else None,
                    "drop": into - value if into is not None else None,
                    "flow": flow_words(into, value, unit_in, out_word, unit_out),
                    "lost": [[w, int(c)] for w, c in s.get("lost") or [] if c],
                    "lost_label": s.get("lost_label") or "", "tip": tip, "state": s.get("state", NONE)})
    return out


def reconcile_words(stage: dict) -> str:
    """What a step's reasons add up to against what it did not put out: all of it, part of it (the rest has no
    reason), or, for the export, marks of which a row may carry several; a step with no count of what went in says
    only what its reasons hold (a site's 建庫). No silent mismatch (a cold reader of the lab's page, 11-17)."""
    unit, drop = stage.get("unit") or "", stage.get("drop")
    told = sum(r[1] for r in stage.get("lost") or [])
    if stage["key"] == "export":
        return (f"{stage['drop_word']} {n(drop)} {unit}；清單上註明缺東西的 {told:,} 處（一筆可能缺好幾樣）" if drop is not None
                else f"清單上註明缺東西的 {told:,} 處（一筆可能缺好幾樣）")
    if drop is None:
        return f"分得出的 {told:,} {stage.get('unit_out') or unit}（這一步比不出這個網站總共少多少）"
    if told == drop:
        return f"少 {drop:,} {unit}，都分了類"
    if told < drop:
        return f"少 {drop:,} {unit}：{told:,} 有分類，{drop - told:,} 沒分類"
    return f"少 {drop:,} {unit}；分類合計 {told:,}（有的算了不只一次）"


def reason_rows(rows: list[list], *, top: int = SHOWN_REASONS) -> str:
    """Reasons as sorted bars: (words, count) → a row each (its name, a violet bar, the count), the largest `top`,
    the rest as 其他 (their names and counts in its floating card)."""
    rows = sorted(rows, key=lambda r: -r[1])
    shown, rest = rows[:top], rows[top:]
    if rest:
        shown = [*shown, ["其他", sum(r[1] for r in rest), "\n".join(f"{r[0]}：{r[1]:,}" for r in rest)]]
    peak = max((r[1] for r in shown), default=0) or 1
    lis = []
    for r in shown:
        tip_attr = f" data-tip='{e(r[2])}'" if len(r) > 2 and r[2] else ""
        lis.append(f"<li{tip_attr}><span class='rs-w'>{e(r[0])}</span>"
                   f"<span class='rs-b'><i style='width:{max(1.5, r[1] / peak * 100):.1f}%'></i></span>"
                   f"<span class='rs-n'>{r[1]:,}</span></li>")
    return f"<ul class='rs'>{''.join(lis)}</ul>"


def funnel_html(stages: list[dict], *, site: bool = False) -> str:
    """One row a step, each on its own terms: its name, a bar of the share it kept (what came out of what went in:
    its own whole, never one scale for the four), the share, 「進 N 單位 → 出 M 單位」, and what it did not put out;
    a row with reasons is a <details> that opens on them, under what they add up to (reconcile_words)."""
    parts = []
    for s in stages:
        share = s.get("share")
        bar = (f"<span class='fn-bar' data-tip='{e(s['tip'])}'><span class='fn-fill' "
               f"style='width:{max(0.5, share * 100):.2f}%'></span></span>" if share is not None
               else f"<span class='fn-bar fn-none' data-tip='{e(s['tip'])}'></span>")
        pct = (f"<span class='fn-pct'>{share:.0%}</span>" if share is not None else
               f"<span class='fn-pct faint' data-tip='{e(s.get('no_share') or '這一步沒有進的數，比不出比例')}'>—</span>")
        has = bool(s["lost"])
        drop = ""
        if s["drop"] or has:
            # no count of what went in: the chip counts what its reasons hold, the same number as the list under it
            told = sum(r[1] for r in s["lost"]) if s["drop"] is None and s["key"] != "export" else 0
            count = f" <b>{s['drop']:,}</b>" if s["drop"] else f" <b>{told:,}</b>" if told else ""
            drop = (f"<span class='fn-drop' data-tip='{e(s['lost_label'])}'>{e(s['drop_word'])}{count}"
                    + (icon("right", 12) if has else "") + "</span>")
        flow = s.get("flow") or s.get("tip") or ""
        row = (f"<span class='fn-name'>{e(s['name'])}</span>{bar}{pct}<span class='fn-flow'>{e(flow)}</span>"
               f"{drop or '<span></span>'}")
        attrs = f" data-stage='{e(s['key'])}'" + (f" data-value='{s['value']}'" if s["value"] is not None else "") + (
            f" data-into='{s['into']}'" if s.get("into") is not None else "")
        if has:
            parts.append(f"<details class='fn-row'{attrs}><summary class='fn-sum'>{row}</summary>"
                         f"<div class='fn-why'><p class='rs-head'>{e(reconcile_words(s))}</p>{reason_rows(s['lost'])}"
                         "</div></details>")
        else:
            parts.append(f"<div class='fn-row'{attrs}><div class='fn-sum'>{row}</div></div>")
    cls = "fn fn-site" if site else "fn"
    return f"<div class='{cls}'>{''.join(parts)}</div>"


def funnel_table(stages: list[dict]) -> str:
    rows = [[e(s["name"]), n(s.get("into")), n(s["value"]),
             f"{s['share']:.0%}" if s.get("share") is not None else "—",
             n(s["drop"]) if s["drop"] is not None else "—", e(s.get("flow") or "")] for s in stages]
    return charts.table(["步驟", "進", "出", "留下", "少了", "單位"], rows, nums=(1, 2, 3, 4))


TONIGHT_JS = """(()=>{const box=document.querySelector('[data-tonight]');if(!box)return;
const words=s=>s<90?Math.round(s)+' 秒':s<5400?Math.round(s/60)+' 分鐘':Math.floor(Math.round(s/60)/60)+' 小時 '+Math.round(s/60)%60+' 分';
const tick=()=>document.querySelectorAll('[data-since]').forEach(el=>{el.textContent='已經 '+words(Date.now()/1000-Number(el.dataset.since))});
const live=box.hasAttribute('data-live'),busy=!!document.querySelector('[data-computing]');
if(live)setInterval(tick,30000);if(!live&&!busy)return;
const poll=()=>fetch(box.getAttribute('data-tonight'),{cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.sig!==box.getAttribute('data-sig'))location.reload();else setTimeout(poll,busy?5000:60000)}).catch(()=>setTimeout(poll,60000));
setTimeout(poll,busy?5000:60000)})();"""

CSS = """
.v3-q{scroll-margin-top:12px}
.v3-q:first-child .card{margin-top:0}
.v3-now{display:flex;gap:18px;align-items:flex-start}
.v3-state{flex:none;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:64px;padding-top:2px}
.v3-dot{display:grid;place-items:center;width:44px;height:44px;border-radius:50%;background:var(--hover);color:var(--ink2)}
.v3-state[data-state=ok] .v3-dot{background:var(--good);color:var(--on-good)}
.v3-state[data-state=warn] .v3-dot{background:var(--warn);color:var(--on-warn)}
.v3-state[data-state=bad] .v3-dot{background:var(--bad);color:var(--on-bad)}
.v3-state[data-state=run] .v3-dot{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 2px var(--ink)}
.v3-state b{font-size:13px}
.v3-state[data-state=ok] b{color:var(--good-ink)}.v3-state[data-state=warn] b{color:var(--warn-ink)}
.v3-state[data-state=bad] b{color:var(--bad-ink)}
.v3-main{min-width:0;flex:1}
.v3-say{margin:2px 0 0;font-size:16px;font-weight:700;line-height:1.45;overflow-wrap:anywhere}
.v3-problems-h{margin:14px 0 4px;font-size:13px;font-weight:650;color:var(--ink2)}
.v3-problems{list-style:none;margin:0;padding:0;display:grid;
grid-template-columns:16px minmax(0,max-content) max-content minmax(0,1fr) max-content;column-gap:12px}
.v3-problems li{grid-column:1/-1;display:grid;grid-template-columns:subgrid;row-gap:2px;align-items:baseline;
padding:7px 0;border-top:1px solid var(--line);font-size:14px}
.v3-problems li:first-child{border-top:0}
.v3-pw{font-weight:650;overflow-wrap:anywhere}.v3-pw a{color:inherit}
.v3-ps{font-size:13px;color:var(--ink2);white-space:nowrap}
li[data-sev=bad] .v3-ps{color:var(--bad-ink);font-weight:650}li[data-sev=warn] .v3-ps{color:var(--warn-ink);font-weight:650}
.v3-pr{min-width:0;overflow-wrap:anywhere;color:var(--ink)}
.v3-code{margin-left:8px;font-size:12px}
.v3-unseen{display:inline-block;margin-left:8px;font-size:11px;font-weight:650;color:var(--warn-ink);padding:0 7px;
border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.v3-pt{font-size:12px;color:var(--ink3);white-space:nowrap}
.v3-age,.v3-worse{display:inline-block;margin-left:8px;padding:0 6px;border-radius:4px;font-size:12px;white-space:nowrap;
  background:var(--sunk);color:var(--ink2)}
.v3-worse{background:var(--bad-bg);color:var(--bad-ink);font-weight:650}
.v3-more-p{margin:6px 0 0;font-size:13px}
.v3-none{margin:0;font-size:14px;color:var(--ink2)}
.v3-m{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;flex:none;vertical-align:-3px;
background:var(--hover);color:var(--ink2)}
.v3-m .ic{width:11px;height:11px}
.v3-m[data-state=warn]{background:var(--warn);color:var(--on-warn)}.v3-m[data-state=bad]{background:var(--bad);
color:var(--on-bad)}.v3-m[data-state=ok]{background:var(--good);color:var(--on-good)}
.v3-flag{color:var(--bad-ink);font-weight:650;font-size:12px;white-space:nowrap}
.v3-word[data-tone=bad]{color:var(--bad-ink);font-weight:650}.v3-word[data-tone=warn]{color:var(--warn-ink);font-weight:650}
.v3-link{text-decoration:none}

.v3-panel{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.v3-panel h3{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:0 0 6px;font-size:13px;font-weight:650;
color:var(--ink2)}
.v3-nsites,.v3-docs{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
gap:4px 24px;font-size:13px}
.v3-nsites li{display:flex;align-items:baseline;gap:10px;min-width:0}
.v3-nsites li a{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.v3-nsites .n{color:var(--ink2);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}
.v3-docs{grid-template-columns:minmax(0,1fr)}
.v3-docs li{display:flex;gap:10px;min-width:0;align-items:baseline}
.v3-docs li a:first-child:not(.v3-doc){flex:none;color:var(--ink2);font-size:12px}
.v3-docs li a:last-child,.v3-docs a.v3-doc{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.v3-docs .v3-why{flex:none;font-size:12px;margin-left:auto}

.fn{display:flex;flex-direction:column;gap:2px;max-width:1240px}
.fn-sum{display:grid;grid-template-columns:72px minmax(120px,1fr) 44px minmax(0,1.3fr) 170px;gap:14px;align-items:center;
padding:5px 6px;border-radius:8px;list-style:none}
.fn-sum::-webkit-details-marker{display:none}
details.fn-row>summary{cursor:pointer}
details.fn-row>summary:hover{background:var(--hover)}
details.fn-row[open]>summary{background:var(--sunk)}
.fn-name{font-weight:650;font-size:14px;white-space:nowrap}
.fn-bar{position:relative;height:14px;min-width:0;border-radius:0 4px 4px 0;background:var(--amt2)}
.fn-bar.fn-none{background:none;border-bottom:1px dashed var(--line2);height:0}
.fn-fill{position:absolute;left:0;top:0;bottom:0;border-radius:0 4px 4px 0;background:var(--amt)}
.fn-pct{text-align:right;font-variant-numeric:tabular-nums;font-size:13px;font-weight:650}
.fn-flow{font-size:13px;color:var(--ink2);font-variant-numeric:tabular-nums;min-width:0}
.rs-head{margin:2px 0 4px;font-size:13px;font-weight:650;color:var(--ink2)}
.fn-drop{justify-self:start;display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;font-size:12px;
color:var(--ink2);border:1px dashed var(--line2);white-space:nowrap}
.fn-drop b{color:var(--amt);font-variant-numeric:tabular-nums}
.fn-drop .ic{transition:transform .15s}details.fn-row[open] .fn-drop .ic{transform:rotate(90deg)}
.fn-why{padding:2px 6px 10px 92px}
@media (prefers-reduced-motion:reduce){.fn-drop .ic{transition:none}}
.v3-key{display:flex;flex-wrap:wrap;align-items:center;gap:4px 16px;margin-top:6px;font-size:12px;color:var(--ink2)}
.v3-key span{display:inline-flex;align-items:center;gap:6px}
.v3-key i{display:inline-grid;place-items:center;width:18px;height:12px;border-radius:3px;font-style:normal;font-size:11px;
font-weight:700;line-height:1}
.v3-key .k-done{background:var(--line2)}.v3-key .k-run{box-shadow:inset 0 0 0 1.5px var(--ink)}
.v3-key .k-warn{background:var(--warn);color:var(--on-warn)}.v3-key .k-bad{background:var(--bad);color:var(--on-bad)}
.v3-key .k-dot{width:10px;height:10px;border-radius:50%;background:var(--bad)}
.v3-key .k-new{background:var(--amt)}.v3-key .k-left{background:var(--q2)}
.v3-fig.v3-astable .v3-key{display:none}
.rs{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:5px;max-width:860px}
.rs li{display:grid;grid-template-columns:minmax(120px,260px) minmax(60px,1fr) 72px;gap:10px;align-items:center;
font-size:13px}
.rs-w{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rs-b{height:10px;min-width:0}.rs-b i{display:block;height:100%;border-radius:0 4px 4px 0;background:var(--amt)}
.rs-n{text-align:right;font-variant-numeric:tabular-nums}


@media (prefers-reduced-motion:no-preference){.v3-state[data-state=run] .v3-dot .ic{animation:v3-turn 2.4s linear infinite}}
@keyframes v3-turn{to{transform:rotate(360deg)}}

@media (max-width:640px){
.v3-now{gap:12px}.v3-state{min-width:48px}.v3-dot{width:36px;height:36px}
.v3-problems{display:block}
.v3-problems li{grid-template-columns:16px minmax(0,1fr) auto;grid-template-areas:"m w t" ". s s" ". r r";gap:2px 8px}
.v3-problems .v3-m{grid-area:m}.v3-pw{grid-area:w}.v3-pt{grid-area:t}.v3-ps{grid-area:s}.v3-pr{grid-area:r}
.fn-sum{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"name pct" "bar bar" "flow flow" "drop drop";
gap:4px 10px;padding:6px 2px}
.fn-name{grid-area:name}.fn-pct{grid-area:pct}.fn-bar{grid-area:bar}.fn-flow{grid-area:flow}.fn-sum>:last-child{grid-area:drop}
.fn-why{padding:2px 0 8px}
.rs li{grid-template-columns:minmax(0,1fr) 64px;grid-template-areas:"w n" "b b";gap:2px 10px}
.rs-w{grid-area:w}.rs-n{grid-area:n}.rs-b{grid-area:b}
}
"""


