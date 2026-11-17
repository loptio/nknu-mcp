"""One site on one page (檢視台 /site?site=…): everything 檢視台 knows of a site, in the order a question about it runs
(structure design docs/superpowers/specs/2026-11-17-panels-structure-design.md section 二; owner: 「可以按你的建议走吧，不用
问我了」, after 「目前的分法都是我什么时候刚好需要什么dashboard 让agent去搞一个出来 完全没有想过其实某些页面的某些功能是可以在
已经在的某一页某一个片段中实现起来更好」):

1. its name and unit (its key and home address float over the name), and in one line whether it is fine now, and if
   not what to look at and who fixes it (the 流水線 row's verdict, plus the last swap's departures it cannot explain);
2. 處理流程: the site through the pipeline's steps, in → out and what was lost and why (pipeline_page.site_steps; the
   page /pipeline/site until 2026-11-17);
3. 昨晚變了什麼 (or 上次換庫變了什麼): its documents that came in, changed and left with their reasons (nknu.ops.changes;
   pipeline_page.one_site_changes), the night's word as on 今晚;
4. 網頁核查: its misses merged and its pages from the sweep (check_sites.site_section; the page /check?site= until
   2026-11-17);
5. 文件: how many documents of each kind it holds and how many have no date, and the 資料庫 queries that list them
   and say why one has no date (metadata.yaml's site_docs, site_kinds, site_undated_why), linked, not done again.

It draws what the other modules return; the one thing it counts itself is the documents per kind (Kinds, one pass over
the library in the background, kept per library file). The section names were tried on a cold reader (owner's rule
for new names, 11-16): 「每一步」 read as unclear and became 處理流程; 「最近的變動」 read as unclear and takes 今晚's
own words. 【Claude】 redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from nknu.ops.changes import file_key
from nknu.ops.check_sites import home_button, site_section
from nknu.ops.look import e, icon
from nknu.ops.pipeline import BAD, CONTENT, FIXERS, NONE, OK, STATE_WORDS, WARN, open_ro, worst
from nknu.ops.pipeline_page import CSS as PIPE_CSS
from nknu.ops.pipeline_page import PILL, SITE_CSS, STATE_ICON, one_site_changes, site_steps

log = logging.getLogger(__name__)

# a document's kinds in 資料總覽's order and words, with their colour's number (theme.css --k0…--k4)
KINDS = (("notice", "公告", 0), ("page", "一般網頁", 1), ("attachment", "附件", 2), ("event", "行事曆", 3),
         ("link", "校外連結", 4))
DATED = ("notice", "page", "attachment")  # the kinds a date is looked for on (metadata.yaml's undated queries)
TABLES_DB = "aca"  # the official library's name in 資料庫 (deploy/lab-inspect/datasette/plugins/guide.py DATABASE)


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


# ------------------------------------------------------------------------------------------------ the parts

def now_line(row: dict | None, mine: dict | None, reasons: dict, *, read: bool) -> tuple[str, str]:
    """(state, sentence): whether the site is fine now and, if not, what to look at and who fixes it. row: its line of
    流水線's table of sites (its worst state and 要留意的地方, each saying who fixes it); mine: its part of the last swap's
    comparison (the departures nobody can explain, and the ones that may be ours); read: whether 流水線 has a reading
    yet."""
    says: list[str] = list((row or {}).get("why") or [])
    state = (row or {}).get("worst") or (OK if read else NONE)
    if mine:
        if mine.get("unexplained"):
            says.append(f"上次換庫有 {mine['unexplained']:,} 份離開了、查不出原因（{FIXERS[CONTENT]}）")
            state = worst(state, BAD)
        if mine.get("look"):
            fixers = {(reasons.get(k) or {}).get("fix") for k in (mine.get("reasons") or {})
                      if (reasons.get(k) or {}).get("kind") == "look"}
            who = "、".join(FIXERS[f] for f in sorted(f for f in fixers if f in FIXERS))
            says.append(f"上次換庫有 {mine['look']:,} 份離開了、可能是我們漏的" + (f"（{who}）" if who else ""))
            state = worst(state, WARN)
    if says:
        return state, "；".join(says)
    if state == NONE:
        return NONE, "正在讀每一步的紀錄，幾秒後重新整理就有"
    return OK, "每一步都正常"


def kinds_html(key: str, counts: dict[str, list[int]] | None, tables: str) -> str:
    """文件: the site's documents per kind (its colour, its word, how many, how many have no date), then the 資料庫
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
        return (f"<section class='card pad st-docs' data-counting><span class='pill none'>計算中</span> "
                f"<span class='soft'>正在數這個網站各類型的文件</span>{links}</section>")
    total = sum(v[0] for v in counts.values())
    if not total:
        return f"<section class='card pad st-docs'><span class='soft'>庫裡沒有這個網站的文件</span>{links}</section>"
    rows = []
    for kind, word, k in KINDS:
        n, undated = counts.get(kind, [0, 0])
        if not n:
            continue
        none = (f"<span class='num' data-tip='{undated:,} 份沒有日期'>{undated:,}</span>" if kind in DATED
                else "<span class='num faint' data-tip='行事曆和校外連結不看日期'>—</span>")
        rows.append(f"<li><span class='st-k'><i style='background:var(--k{k})'></i>{e(word)}</span>"
                    f"<span class='num'>{n:,}</span>{none}</li>")
    undated = sum(counts.get(kind, [0, 0])[1] for kind in DATED)
    head = ("<li class='st-kh'><span>類型</span><span class='num'>份數</span>"
            "<span class='num' data-tip='公告、網頁、附件裡沒有日期的'>沒日期</span></li>")
    foot = (f"<li class='st-kt'><span>合計</span><span class='num'>{total:,}</span>"
            f"<span class='num'>{undated:,}</span></li>")
    return f"<section class='card pad st-docs'><ul class='st-kinds'>{head}{''.join(rows)}{foot}</ul>{links}</section>"


CSS = """
.st-head{display:flex;flex-direction:column;gap:10px;padding:16px 18px}
.st-name{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 14px}
.st-name h1{font-size:20px;line-height:1.3;overflow-wrap:anywhere}
.st-name .st-unit{color:var(--ink2);font-size:13px}
.st-now{display:flex;align-items:flex-start;gap:10px;font-size:14px;line-height:1.55}
.st-now .pill{flex:none;margin-top:1px}
.st-now .ic{flex:none}
.st-sec{scroll-margin-top:12px}
.st-sec .btn{margin-left:auto;font-weight:400}
.st-docs{display:flex;flex-direction:column;gap:12px}
.st-kinds{list-style:none;margin:0;padding:0;max-width:420px}
.st-kinds li{display:grid;grid-template-columns:minmax(0,1fr) 80px 80px;gap:8px;align-items:center;padding:5px 2px;
font-size:13px;border-bottom:1px solid var(--line)}
.st-kinds li:last-child{border-bottom:0}
.st-kinds .num{text-align:right;font-variant-numeric:tabular-nums}
.st-kh{color:var(--ink3);font-size:12px;font-weight:600}.st-kh span{font-size:12px}
.st-kt{font-weight:650}
.st-k{display:inline-flex;align-items:center;gap:8px}.st-k i{width:10px;height:10px;border-radius:3px;flex:none}
.st-links{display:flex;flex-wrap:wrap;gap:8px}
.st-links .btn{display:inline-flex;align-items:center;gap:5px}
"""


def site_body(key: str, *, name: str, unit: str, home: str, now: tuple[str, str], detail: dict | None,
              changes: dict, changes_title: str, view: dict | None, archive: bool,
              kinds: dict[str, list[int]] | None, tables: str) -> str:
    """The page under the top bar (the module docstring's five parts). detail: pipeline.site_detail (None: the library
    holds no such site); changes: the last swap's comparison (changes.Changes.snapshot); view: the sweep's site view
    (check_sites.site_section; None when the ledger has no such site); archive: whether the crawler's archive is
    mounted; kinds: this site's site_kinds (None: being counted); tables: 資料庫's origin."""
    state, said = now
    tip = "\n".join(x for x in (key, home) if x)
    head = (f"<header class='card st-head'><div class='st-name'><h1 data-tip-h='網站代號和首頁' data-tip='{e(tip)}'>"
            f"{e(name)}</h1>" + (f"<span class='st-unit'>{e(unit)}</span>" if unit and unit != name else "") + "</div>"
            f"<div class='st-now' aria-label='這個網站現在'><span class='pill {PILL.get(state, 'none')}'>"
            f"{icon(STATE_ICON.get(state, 'dash'), 12)}{e(STATE_WORDS.get(state, state))}</span>"
            f"<span>{e(said)}</span></div></header>")
    steps = site_steps(detail, style=False) if detail is not None else (
        "<section class='card pad'><span class='pill none'>庫裡沒有這個網站</span>"
        "<span class='soft'>　正式庫的網站表裡沒有它，沒有導出、轉文字和建庫的數字</span></section>")
    if archive:
        check = site_section(view, key)
        start = home_button(view)
    else:
        check = ("<section class='card pad'><span class='pill warn'>沒有掛上爬蟲存檔</span>"
                 "<div class='soft small' style='margin-top:8px'>檢視台啟動時沒有給 --archive，或那個資料夾裡沒有帳本。</div>"
                 "</section>")
        start = ""
    return (f"<style>{PIPE_CSS}{SITE_CSS}{CSS}</style>{head}"
            f"<h2 class='sec st-sec' id='steps'>處理流程</h2>{steps}"
            f"<h2 class='sec st-sec' id='changes'>{e(changes_title)}</h2>{one_site_changes(changes, key)}"
            f"<h2 class='sec st-sec' id='check'>網頁核查{start}</h2>{check}"
            f"<h2 class='sec st-sec' id='docs'>文件</h2>{kinds_html(key, kinds, tables)}"
            # the counts are made once per library file (a few seconds): the page asks again when they are not there yet
            + ("<script>setTimeout(()=>location.reload(),5000)</script>" if kinds is None else ""))
