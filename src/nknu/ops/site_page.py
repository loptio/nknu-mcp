"""One site on one page (檢視台 /site?site=…), panels v3 (docs/superpowers/specs/2026-11-17-panels-v3-questions.md 四;
owner 11-17: 「你再按照安装的插件和发现的问题再优化dashboard吧，我看一下」): five sections, each answering one question
in the form the Dashboard Design plugin's data-display table gives it, then the folded detail and 網頁核查:

  1 這個網站正常嗎、哪裡不正常？   its name, a state with its icon and word, one sentence (its sources' reasons)
  2 每一步進出多少、丟在哪？       the same rows as 每晚運行's 整體流程 (抓到 → 導出有檔 → 轉成文字 → 庫裡文件), this site only
  3 丟掉的原因各多少、誰能補？     the reasons as sorted bars (8 and 其他), each tagged 爬蟲可補 / 建庫可補 / 學校那邊 /
                                照規則; a click opens its items (the export's examples open 網頁核查 on that page)
  4 庫裡有哪些文件、多少沒日期？   one bar split by kind (the kinds' own colours) and the share without a date, with the
                                資料庫 queries that list them
  5 最近幾晚這個網站變了什麼？     fourteen nights of the nights' record (nknu.ops.history) as small bars, then the last
                                swap's list (pipeline_page.one_site_changes)

then 各步驟細節 (pipeline_page.site_steps, folded) and 網頁核查 (check_sites.site_section, unchanged, at the bottom).

It draws what the other modules return; the one thing it counts itself is the documents per kind (Kinds, one pass over
the library in the background, kept per library file). The names and the forms are 【Claude】 (redesign 2026-11-17,
research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from nknu.ops import charts
from nknu.ops.changes import file_key
from nknu.ops.check_sites import home_button, site_section
from nknu.ops.look import e, icon, s2t
from nknu.ops.pipeline import BAD, NONE, OK, STATE_WORDS, WARN, open_ro, worst
from nknu.ops.pipeline_page import CSS as PIPE_CSS
from nknu.ops.pipeline_page import PILL, SITE_CSS, STATE_ICON, STATUS_TEXT, one_site_changes, site_steps
from nknu.ops.tonight_page import CSS as TONIGHT_CSS
from nknu.ops.tonight_page import (
    PIPE_TIP,
    SHOWN_REASONS,
    build_items,
    flow_words,
    funnel_html,
    funnel_table,
    nightbars_table,
    reconcile_words,
    strip_fixer,
)

log = logging.getLogger(__name__)

# a document's kinds in 資料總覽's order and words, with their colour's number (theme.css --k0…--k4)
KINDS = (("notice", "公告", 0), ("page", "一般網頁", 1), ("attachment", "附件", 2), ("event", "行事曆", 3),
         ("link", "校外連結", 4))
DATED = ("notice", "page", "attachment")  # the kinds a date is looked for on (metadata.yaml's undated queries)
TABLES_DB = "aca"  # the official library's name in 資料庫 (deploy/lab-inspect/datasette/plugins/guide.py DATABASE)
SHOWN_ITEMS = 12  # items a reason lists when opened 【Claude】
SITE_NIGHTS_KEY = ("<div class='v3-key'><span><i class='k-new'></i>新進</span><span><i class='k-left'></i>離開</span>"
                   "<span><i class='k-dot'></i>查不出原因的離開</span><span>日期淡色：那一晚沒有這個網站的紀錄</span></div>")


def site_kinds(db: Path) -> dict[str, dict[str, list[int]]]:
    """{site: {kind: [documents, documents without a date]}}: one pass over the library's docs (each row's text is
    read past to its site, so it runs in the background: Kinds)."""
    conn = open_ro(db)
    try:
        out: dict[str, dict[str, list[int]]] = {}
        for site, kind, n, undated in conn.execute(
                "SELECT site, kind_class, count(*), sum(ident_date IS NULL) FROM docs GROUP BY site, kind_class"):
            out.setdefault(site or "", {})[kind or ""] = [int(n or 0), int(undated or 0)]
        return out
    finally:
        conn.close()


class Kinds:
    """site_kinds of one library file, made in a background thread and kept until the file changes (file_key: its
    identity, size and time of change). get() is the counts, or None while they are being made."""

    def __init__(self, db: Path, count: Callable[[Path], dict] = site_kinds) -> None:
        self.db, self.count = db, count
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._done: dict | None = None
        self._thread: threading.Thread | None = None

    def _run(self, key: tuple) -> None:
        try:
            got = self.count(self.db)
        except Exception:  # the section says it is not there; the log has the whole
            log.exception("单站页：数不了各类型的文件")
            got = {}
        with self._lock:
            self._key, self._done = key, got

    def get(self) -> dict | None:
        key = file_key(self.db)
        if key is None:
            return {}
        with self._lock:
            if self._key == key:
                return self._done
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, args=(key,), name="site-kinds", daemon=True)
                self._thread.start()
        return None

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


# ------------------------------------------------------------------------------------------------ 1 the site now

def now_line(row: dict | None, mine: dict | None, reasons: dict, *, read: bool) -> tuple[str, str]:
    """(state, sentence): whether the site is fine now and, if not, what to look at and who fixes it. row: its line of
    流水線's table of sites (its worst state and 要留意的地方, each saying who fixes it); mine: its part of the last swap's
    comparison (the departures nobody can explain, and the ones that may be ours); read: whether 流水線 has a reading
    yet."""
    says: list[str] = list((row or {}).get("why") or [])
    state = (row or {}).get("worst") or (OK if read else NONE)
    if mine:
        if mine.get("unexplained"):
            says.append(f"上次換庫說不出原因地離開 {mine['unexplained']:,} 份")
            state = worst(state, BAD)
        if mine.get("look"):
            says.append(f"上次換庫可能是我們這邊漏掉的 {mine['look']:,} 份")
            state = worst(state, WARN)
    if says:
        return state, "；".join(says)
    if state == NONE:
        return NONE, "正在讀每一步的紀錄，幾秒後重新整理就有"
    if state in (WARN, BAD):  # a mark that is not 正常 never sits beside 「每一步都正常」 (the lab, 11-17)
        return state, "有一步的數字要看（看下面的流水線）"
    return OK, "每一步都正常"


def head_html(key: str, *, name: str, unit: str, home: str, now: tuple[str, str]) -> str:
    state, said = now
    tip = "\n".join(x for x in (key, home) if x)
    said = strip_fixer(said)  # a reading from before: no one is named on the page (spec 八.2)
    return (f"<header class='card st-head v3-q' data-q='1' data-question='這個網站正常嗎、要誰做什麼？'>"
            f"<div class='st-name'><h1 data-tip-h='網站代號和首頁' data-tip='{e(tip)}'>{e(name)}</h1>"
            + (f"<span class='st-unit'>{e(unit)}</span>" if unit and unit != name else "") + "</div>"
            f"<div class='st-now' aria-label='這個網站現在'><span class='pill {PILL.get(state, 'none')}'>"
            f"{icon(STATE_ICON.get(state, 'dash'), 12)}{e(STATE_WORDS.get(state, state))}</span>"
            f"<span class='st-say'>{e(said)}</span></div></header>")


# ------------------------------------------------------------------------------------------------ 2, 3 the funnel


def site_stages(row: dict | None, detail: dict | None) -> list[dict]:
    """The site's four steps in tonight_page.funnel_data's shape (each on its own terms: what went in, what came out,
    the share kept), with what each step's reasons hold when opened (items: (text, href) pairs; '' href for text
    only). 建庫 has no count of what went in for one site: its row says what came out and the reasons it can tell."""
    cells = (row or {}).get("cells") or {}
    detail = detail or {}
    crawl = cells.get("crawl") or {}
    ex = detail.get("export") or {}
    text = detail.get("text") or {}
    site = detail.get("site") or {}

    def step(key: str, into: int | None, value: int | None, **more) -> dict:
        unit, out_word, unit_out = more.pop("units")
        return {"key": key, "into": into, "value": value,
                "share": value / into if into and value is not None else None,
                "drop": into - value if into is not None and value is not None else None,
                "flow": flow_words(into, value, unit, out_word, unit_out) if value is not None else "",
                "unit": unit, "unit_out": unit_out, **more}

    got, whole = crawl.get("n"), crawl.get("whole")
    files, rows = ex.get("files"), ex.get("rows")
    examples = ex.get("missing_examples") or {}
    status = text.get("status") or {}
    ok = status.get("ok") if status else None
    total = sum(status.values()) if status else None
    bad: dict[str, list] = {}
    for st, reason, f in text.get("bad") or []:
        bad.setdefault(STATUS_TEXT.get(st, st), []).append((f"{s2t(f)}（{s2t(reason) or '沒寫原因'}）", ""))
    copies = (detail.get("copies") or {}).get("merged") if isinstance(detail.get("copies"), dict) else None
    built = [x for x in (["別處有一樣的、併成一份", int(copies or 0)],
                         ["學校刪掉或轉走、沒收的公告", int(site.get("deleted_notices") or 0)]) if x[1]]
    stages = [
        step("crawl", whole, got, name="爬取", drop_word="沒抓到", units=("個網址", "出", "個網址"),
             lost=[[w, int(c)] for _code, w, c in crawl.get("lost") or [] if c],
             lost_label="還沒抓到的（照爬蟲自己的分類）", tip=crawl.get("tip") or "", items={}),
        step("export", rows, files, name="導出", drop_word="沒存檔", units=("筆", "有檔", "筆"),
             lost=[[w, int(c)] for w, c in (ex.get("missing") or {}).items() if c],
             lost_label="清單上註明缺東西的（一筆可能缺好幾樣）",
             tip=(f"導出清單 {rows:,} 筆，有檔的 {files:,} 筆；沒檔的多半照規則本來就不存檔"
                  if rows is not None and files is not None else ""),
             items={w: [(s2t(t) or u, f"/check?ref={quote(u, safe='')}") for u, t in examples.get(w) or []]
                    for w in ex.get("missing") or {}}),
        step("totext", total, ok, name="轉文字", drop_word="沒轉成文字", units=("個檔", "出", "個檔"),
             lost=[[STATUS_TEXT.get(st, st), int(c)] for st, c in status.items() if st != "ok" and c],
             lost_label="沒轉成文字的", tip="壓縮檔裡的檔各算一個", items=bad),
        step("build", None, site.get("docs") if site else None, name="建庫", drop_word="建庫沒收的",
             units=("筆", "出", "份文件"), lost=built, lost_label="建庫沒收成文件的（這個網站分得出的）",
             no_share="這一步比不出這個網站總共該有多少",
             tip="合併重複、去掉列表翻頁以後；這個網站進建庫的筆數沒有分開記", items={}),
    ]
    for s in stages:
        if s["value"] is None:
            s["tip"] = s["tip"] or "讀不到這一步的數字"
    return stages


def reasons_html(stages: list[dict]) -> tuple[str, str, int]:
    """(the reasons, their table, how many): grouped by step, each group under what its reasons add up to against
    what the step did not put out (tonight_page.reconcile_words: no silent mismatch), its reasons as bars (the
    largest SHOWN_REASONS of the step and 其他), each a <details> whose summary is the bar and whose body its items."""
    groups, rows = [], []
    for s in stages:
        mine = sorted(((w, c, s["items"].get(w) or []) for w, c in s["lost"]), key=lambda r: -r[1])
        if not mine:
            continue
        rows += [(s["name"], w, c) for w, c, _ in mine]
        shown, rest = mine[:SHOWN_REASONS], mine[SHOWN_REASONS:]
        if rest:
            shown.append(("其他", sum(r[1] for r in rest), [(f"{r[0]}：{r[1]:,}", "") for r in rest]))
        peak = max(r[1] for r in shown) or 1
        lis = []
        for words, count, items in shown:
            if items:
                body = "".join(f"<li><a href='{e(href)}' data-tip='{e(t)}'>{e(t)}</a></li>" if href else f"<li>{e(t)}</li>"
                               for t, href in items[:SHOWN_ITEMS])
                more = len(items) - SHOWN_ITEMS
                body = (f"<ul class='rs-items'>{body}</ul>"
                        + (f"<div class='faint small'>還有 {more} 個</div>" if more > 0 else ""))
            else:
                body = "<p class='faint small rs-none'>這一類沒有逐筆清單；每一頁抓到什麼，看最下面的「網頁核查」</p>"
            lis.append(f"<li><details><summary><span class='rs-w'>{e(words)}</span>"
                       f"<span class='rs-b'><i style='width:{max(1.5, count / peak * 100):.1f}%'></i></span>"
                       f"<span class='rs-n'>{count:,}</span></summary>"
                       f"{body}</details></li>")
        groups.append(f"<div class='rs-group' data-stage='{e(s['key'])}'><p class='rs-head'>{e(s['name'])}　"
                      f"{e(reconcile_words(s))}</p><ul class='rs rsd'>{''.join(lis)}</ul></div>")
    bars = "".join(groups) or "<p class='v3-empty'>沒有丟掉的</p>"
    table = charts.table(["原因", "在哪一步", "份數"], [[e(w), e(st), f"{c:,}"] for st, w, c in rows], nums=(2,))
    return bars, table, len(rows)


# ------------------------------------------------------------------------------------------------ 4 the documents


def kinds_html(key: str, counts: dict[str, list[int]] | None, tables: str) -> tuple[str, str]:
    """(the drawing, its table): the site's documents as one bar split by kind (the kinds' own colours, the names,
    counts and shares beside it), then how many of 公告、網頁、附件 have no date as a violet meter, then the 資料庫
    queries that list them (tables: 資料庫's origin; '' leaves the links out). counts None: still being counted."""
    q = quote(key, safe="")
    links = ""
    if tables:
        base = f"{tables.rstrip('/')}/{TABLES_DB}"
        links = ("<div class='st-links'>"
                 f"<a class='btn' href='{e(base)}/site_docs?site={q}' data-tip='資料庫：這個網站的每一份文件，最新的在前'>"
                 f"全部文件{icon('out', 11)}</a>"
                 f"<a class='btn' href='{e(base)}/site_undated_why?site={q}' data-tip='資料庫：公告、網頁、附件沒有日期的，"
                 f"照原因分；每個原因寫了怎麼辦、點進去是那幾份'>沒日期的原因{icon('out', 11)}</a>"
                 f"<a class='btn' href='{e(base)}/site_kinds?site={q}' data-tip='資料庫：各類型的份數、字數、主料和背景各幾份'>"
                 f"各類型份數{icon('out', 11)}</a></div>")
    if counts is None:
        counting = ("<div data-counting><span class='pill none'>計算中</span> <span class='soft'>正在數這個網站各類型的文件"
                    f"</span>{links}</div>")
        return counting, ""
    total = sum(v[0] for v in counts.values())
    if not total:
        return f"<p class='v3-empty'>庫裡沒有這個網站的文件</p>{links}", ""
    blocks, legend, rows = [], [], []
    for kind, word, k in KINDS:
        n, undated = counts.get(kind, [0, 0])
        if not n:
            continue
        share = n / total
        blocks.append(f"<i style='flex-grow:{n};background:var(--k{k})' data-tip-h='{e(word)}' "
                      f"data-tip='{n:,} 份（{share:.0%}）'></i>")
        legend.append(f"<li><i style='background:var(--k{k})'></i><span>{e(word)}</span><b>{n:,}</b>"
                      f"<span class='faint'>{share:.0%}</span></li>")
        rows.append([e(word), f"{n:,}", f"{undated:,}" if kind in DATED else "—"])
    dated = sum(counts.get(kind, [0, 0])[0] for kind in DATED)
    undated = sum(counts.get(kind, [0, 0])[1] for kind in DATED)
    rows.append(["合計", f"{total:,}", f"{undated:,}"])
    meter = ""
    if dated:
        share = undated / dated
        meter = (f"<div class='st-undated' data-tip='公告、網頁、附件 {dated:,} 份裡沒有日期的'><span>沒日期</span>"
                 f"<span class='st-track'><i style='width:{max(0.5, share * 100):.1f}%'></i></span>"
                 f"<span><b>{undated:,}</b> 份 <span class='faint'>{share:.0%}</span></span></div>")
    plot = (f"<div class='st-kbar' role='img' aria-label='各類型文件的份數'>{''.join(blocks)}</div>"
            f"<ul class='st-legend'>{''.join(legend)}</ul>{meter}")
    table = charts.table(["類型", "份數", "沒日期"], rows, nums=(1, 2)).replace("class='v3-t'", "class='v3-t st-kinds'")
    return plot + links, table


# ------------------------------------------------------------------------------------------------ 5 the nights


def nights_data(hist: dict, labels: dict[str, str]) -> dict:
    """The site's fourteen nights in charts' nightbars shape (no per-site 改了: the record keeps new, left and the
    unexplained per site)."""
    nights = []
    for day, rec in zip(hist.get("days") or [], hist.get("nights") or []):
        counted = bool(rec and rec.get("counted"))
        why = "沒有紀錄" if rec is None else "" if counted else "這一晚沒有逐站算進出"
        # every night named by its evening (11-16 夜), never 今晚／昨晚 (owner 11-17)
        nights.append({"night": day, "date": day[5:], "label": "夜", "counted": counted, "why": why,
                       "new": (rec or {}).get("new"), "changed": None, "left": (rec or {}).get("left"),
                       "unexplained": (rec or {}).get("unexplained"), "look": None, "docs": (rec or {}).get("docs")})
    return {"nights": nights, "sites": {}, "shown": 0}


def unexplained_items(dep: dict | None, key: str) -> str:
    """This site's part of the build's own list of what left for no reason (nightly.departures_of; '' when none)."""
    return build_items(dep, key) if dep else ""


def nights_section(hist: dict | None, changes: dict, key: str, labels: dict[str, str],
                   title: str = "上次換庫變了什麼", departures: dict | None = None) -> tuple[str, str]:
    """(the section's body, its toggle): the fourteen nights, then what the build found leaving this site for no
    reason (its own list), then the last swap's list under its own name."""
    # named for what they are: the last swap's numbers (a cold reader read 「新進 1 改了 2 離開 1」 as last night's)
    tip = f"前一份庫和現在用的這份庫比：這個網站新進、改了、離開各幾份（{title}）"
    last = (unexplained_items(departures, key)
            + f"<h3 class='st-lt' data-tip='{e(tip)}'>上次換庫的變動</h3>{one_site_changes(changes, key)}")
    if not hist or not hist.get("enabled"):
        why = (hist or {}).get("why") or "沒有紀錄"
        return (f"<p class='v3-empty'><span class='v3-m' data-state='warn'>{icon('warn', 11)}</span> 沒有在記每晚的紀錄："
                f"{e(why)}</p>{last}"), ""
    data = nights_data(hist, labels)
    if not any(x["counted"] for x in data["nights"]):
        return f"<p class='v3-empty'>還沒有這個網站的每晚紀錄：第一晚跑完以後就有</p>{last}", ""
    fig = charts.figure("fig-site-nights", chart="nightbars", src="data-site-nights", table=nightbars_table(data),
                        label="這個網站最近 14 晚新進和離開的文件", height=190, key=SITE_NIGHTS_KEY)
    return (charts.data_tag("data-site-nights", data) + fig + f"<div class='st-last'>{last}</div>",
            charts.toggle("fig-site-nights"))


# ------------------------------------------------------------------------------------------------ the page


CSS = """
.st-head{display:flex;flex-direction:column;gap:10px;padding:16px 18px}
.st-name{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 14px}
.st-name h1{font-size:20px;line-height:1.3;overflow-wrap:anywhere}
.st-name .st-unit{color:var(--ink2);font-size:13px}
.st-now{display:flex;align-items:flex-start;flex-wrap:wrap;gap:8px 10px;font-size:14px;line-height:1.55}
.st-now .pill{flex:none;margin-top:1px}
.st-now .st-say{flex:1 1 320px;min-width:0;overflow-wrap:anywhere}
.st-now .ic{flex:none}
.st-sec{scroll-margin-top:12px}
.st-sec .btn{margin-left:auto;font-weight:400}
.st-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.st-links .btn{display:inline-flex;align-items:center;gap:5px}
.st-kbar{display:flex;gap:2px;height:16px;max-width:860px}
.st-kbar i{display:block;min-width:3px;border-radius:4px}
.st-legend{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px 22px;font-size:13px}
.st-legend li{display:inline-flex;align-items:center;gap:6px}
.st-legend li i{width:10px;height:10px;border-radius:3px;flex:none}
.st-legend b{font-variant-numeric:tabular-nums}
.st-undated{display:grid;grid-template-columns:auto minmax(80px,360px) auto;justify-content:start;gap:10px;
align-items:center;margin-top:14px;font-size:13px}
.st-track{height:10px;border-radius:5px;background:var(--q0);overflow:hidden}
.st-track i{display:block;height:100%;border-radius:0 4px 4px 0;background:var(--amt)}
.st-undated b{font-variant-numeric:tabular-nums}
.st-last{margin-top:16px}.st-lt{margin:0 0 8px;font-size:13px;font-weight:650;color:var(--ink2)}
.st-q .tn-chg{margin:0;border:0;padding:0;box-shadow:none;background:none}
.rsd>li{display:block}
.rs-group+.rs-group{margin-top:16px}
.rsd details>summary{display:grid;grid-template-columns:minmax(120px,300px) minmax(60px,1fr) 72px;gap:10px;
align-items:center;font-size:13px;cursor:pointer;list-style:none;padding:3px 4px;border-radius:7px}
.rsd details>summary::-webkit-details-marker{display:none}
.rsd details>summary:hover{background:var(--hover)}
.rsd details[open]>summary{background:var(--sunk)}
.rsd .rs-w .faint{font-size:11px;margin-left:6px}
.rs-items{list-style:none;margin:4px 0 8px 14px;padding:0;display:flex;flex-direction:column;gap:2px;font-size:12px}
.rs-items a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);display:block;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap;max-width:640px}
.rs-none{margin:4px 0 8px 14px}
.st-more{margin-top:26px}
@media (max-width:640px){
.rsd details>summary{grid-template-columns:minmax(0,1fr) 64px;grid-template-areas:"w n" "b b";gap:2px 10px}
.rsd details>summary .rs-w{grid-area:w}.rsd details>summary .rs-n{grid-area:n}.rsd details>summary .rs-b{grid-area:b}
.st-undated{grid-template-columns:auto minmax(0,1fr) auto;justify-content:stretch}
}
"""


def _section(q: int, sec_id: str, title: str, question: str, body: str, aside: str = "", tip: str = "") -> str:
    tip_attr = f" data-tip='{e(tip)}'" if tip else ""
    return (f"<section class='v3-q st-q' id='{e(sec_id)}' data-q='{q}' data-question='{e(question)}' "
            f"aria-label='{e(title)}'><div class='v3-h'><h2 class='st-sec'{tip_attr}>{e(title)}</h2>{aside}</div>"
            f"<div class='card pad'>{body}</div></section>")


def site_body(key: str, *, name: str, unit: str, home: str, now: tuple[str, str], detail: dict | None,
              changes: dict, changes_title: str, view: dict | None, archive: bool,
              kinds: dict[str, list[int]] | None, tables: str, row: dict | None = None,
              hist: dict | None = None, labels: dict[str, str] | None = None, departures: dict | None = None) -> str:
    """The page under the top bar (the module docstring's sections). detail: pipeline.site_detail (None: the library
    holds no such site); changes: the last swap's comparison (changes.Changes.snapshot); changes_title: its name
    (昨晚變了什麼 …, said over its list); view: the sweep's site view (check_sites.site_section; None when the ledger
    has no such site); archive: whether the crawler's archive is mounted; kinds: this site's site_kinds (None: being
    counted); tables: 資料庫's origin; row: the site's row of 流水線's table (its cells: the funnel's 抓到 and its
    reasons); hist: history.site_history; labels: each night's name
    (11-16 夜); departures: the build's own check of the night (nightly.departures_of), its items of this site listed."""
    head = head_html(key, name=name, unit=unit, home=home, now=now)
    stages = site_stages(row, detail)
    has_any = any(s["value"] is not None for s in stages)
    if has_any:
        fig = charts.figure("fig-site-funnel", plot=funnel_html(stages, site=True), table=funnel_table(stages),
                            label="這個網站每一步出來多少")
        flow = _section(2, "flow", "整體流程（累計）", "每一步進出多少、丟在哪？", fig, charts.toggle("fig-site-funnel"),
                        PIPE_TIP)
        bars, table, count = reasons_html(stages)
        fig2 = charts.figure("fig-site-reasons", plot=bars, table=table, label="丟掉的原因")
        why = _section(3, "reasons", "丟掉的原因", "丟掉的原因各多少、誰能補？", fig2,
                       charts.toggle("fig-site-reasons") if count else "")
    else:
        note = ("<span class='pill none'>沒有數字</span><span class='soft'>　正式庫的網站表裡沒有它，帳本裡也沒有它抓到的網址</span>")
        flow = _section(2, "flow", "整體流程（累計）", "每一步進出多少、丟在哪？", note, tip=PIPE_TIP)
        why = _section(3, "reasons", "丟掉的原因", "丟掉的原因各多少、誰能補？", "<p class='v3-empty'>沒有數字</p>")
    plot, table = kinds_html(key, kinds, tables)
    docs_body = (charts.figure("fig-site-kinds", plot=plot, table=table, label="這個網站的文件") if table else plot)
    docs = _section(4, "docs", "文件", "庫裡有哪些文件、多少沒日期？", docs_body,
                    charts.toggle("fig-site-kinds") if table else "")
    body5, aside5 = nights_section(hist, changes, key, labels or {}, changes_title, departures)
    nights = _section(5, "changes", "最近幾晚", "最近幾晚這個網站變了什麼？", body5, aside5)
    steps = ""
    if detail is not None:
        steps = (f"<details class='card v3-more st-more' id='steps'><summary>{icon('right', 14)}<span>各步驟細節</span>"
                 f"</summary><div class='pad'>{site_steps(detail, style=False)}</div></details>")
    if archive:
        check = site_section(view, key)
        start = home_button(view)
    else:
        check = ("<section class='card pad'><span class='pill warn'>沒有掛上爬蟲存檔</span>"
                 "<div class='soft small' style='margin-top:8px'>檢視台啟動時沒有給 --archive，或那個資料夾裡沒有帳本。</div>"
                 "</section>")
        start = ""
    return (f"<style>{PIPE_CSS}{SITE_CSS}{charts.CSS}{TONIGHT_CSS}{CSS}</style>{head}{flow}{why}{docs}{nights}{steps}"
            f"<h2 class='sec st-sec' id='check'>網頁核查{start}</h2>{check}"
            + charts.scripts()
            # the counts are made once per library file (a few seconds): the page asks again when they are not there yet
            + ("<script>setTimeout(()=>location.reload(),5000)</script>" if kinds is None else ""))
