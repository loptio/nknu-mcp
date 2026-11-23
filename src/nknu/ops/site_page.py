"""One site on one page (檢視台 /site?site=…), rebuilt 2026-11-18 the way the home was (home_page; spec
docs/superpowers/specs/2026-11-17-panels-v3-questions.md 十): the site's own pipeline is the page, a picked step's
detail sits beside it, nothing is drawn as a percentage and no chart library is loaded.

  1 現況        這個網站正常嗎？庫裡有它多少文件？   its name, a state with its mark and word, one sentence, what the
                                                    library holds of it and as of when
  2 流程        每一步進多少、出多少？               four nodes, 爬取 → 導出 → 轉文字 → 建庫, each with what came out of it
                                                    for this site; a picked one shows beside the row what went in and
                                                    out, one split bar where the step's numbers add up (out + every
                                                    reason = in), its reasons the largest first (one with a list opens
                                                    on it: its examples open 網頁核查 on that page) and its facts
  3 問題        有什麼不正常的？                     the site's remarks, one row each, each naming its step
  4 文件        庫裡有哪些文件、多少沒日期？         one bar split by kind (the kinds' own colours) with the counts, how
                                                    many of 公告、網頁、附件 have no date, the 資料庫 queries listing them
  5 每晚紀錄    每晚進來多少、離開多少？             a row a night the record has (nknu.ops.history), then the last
                                                    swap's own lists (pipeline_page.one_site_changes)

then 網頁核查 (check_sites.site_section, unchanged, across the page's width).

It draws what the other modules return; the one thing it counts itself is the documents per kind (Kinds, one pass over
the library in the background, kept per library file). The page before (five sections with the steps as percentage
bars, the reasons as one long list, a fourteen-night chart of mostly empty nights, the steps' facts folded at the
bottom) is in git: 78e94a3."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from nknu.ops import charts
from nknu.ops.changes import file_key
from nknu.ops.check_sites import home_button, site_section
from nknu.ops.home_page import CSS as HOME_CSS
from nknu.ops.home_page import HOME_JS, TONE_PILL, TONE_WORDS, chain_html, mark, split_bar
from nknu.ops.look import e, icon, s2t
from nknu.ops.names import TITLES, merge, rename
from nknu.ops.pipeline import BAD, NONE, OK, WARN, left_out_words, open_ro, stamp, worst
from nknu.ops.pipeline_page import CSS as PIPE_CSS
from nknu.ops.pipeline_page import (
    SITE_CSS,
    STATE_ICON,
    STATUS_TEXT,
    _bars,
    one_site_changes,
    outcome_word,
    refill_html,
)
from nknu.ops.tonight_page import CSS as TONIGHT_CSS
from nknu.ops.tonight_page import build_items, n, problem_html, reconcile_words, strip_fixer

log = logging.getLogger(__name__)

# a document's kinds in 資料總覽's order and words, with their colour's number (theme.css --k0…--k4)
KINDS = (("notice", "公告", 0), ("page", "一般網頁", 1), ("attachment", "附件", 2), ("event", "行事曆", 3),
         ("link", "校外連結", 4))
DATED = ("notice", "page", "attachment")  # the kinds a date is looked for on (metadata.yaml's undated queries)
TABLES_DB = "aca"  # the official library's name in 資料庫 (deploy/lab-inspect/datasette/plugins/guide.py DATABASE)
SHOWN_ITEMS = 12  # items a reason lists when opened 【Claude】


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


# ------------------------------------------------------------------------------------------------ 1 現況, 3 問題

QUESTIONS = {1: "這個網站正常嗎？庫裡有它多少文件？", 2: "每一步進多少、出多少？", 3: "有什麼不正常的？",
             4: "庫裡有哪些文件、多少沒日期？", 5: "每晚進來多少、離開多少？", 6: "上次換庫，它變了什麼？"}
# the site's four steps, and the columns of 流水線's table of sites (pipeline.py's cells) each one answers for
NODES = (("crawl", "爬取", ("crawl",)), ("export", "導出", ("export", "refill")), ("totext", "轉文字", ("text",)),
         ("build", "建庫", ("docs",)))
NODE_OF_COL = {col: key for key, _name, cols in NODES for col in cols}
NODE_NAME = {key: name for key, name, _cols in NODES}
DOES = {"crawl": "爬蟲照這個網站的清單抓網頁和檔案",
        "export": "把抓到的整理成一份清單和一個資料夾，給轉文字和建庫用",
        "totext": "把網頁、PDF、Office 檔、圖片轉成文字",
        "build": "把轉好的文字整理成文件：合併副本、定日期和單位、切小段"}
SEV_WORDS = {WARN: "留意", BAD: "出錯"}
SHOWN_REASONS = 8  # reasons a step lists before 其他
SHOWN_SITE_NIGHTS = 14


def site_problems(row: dict | None, mine: dict | None) -> list[dict]:
    """What is not normal about the site, a row each in tonight_page.problem_html's shape: the remarks of its row of
    流水線's table of sites (each on the column, so the step, it is about), then what the last swap's comparison
    could not explain of it. The worst first."""
    out = []
    issues = list((row or {}).get("issues") or [])
    told = {x.get("why") for x in issues}
    if (row or {}).get("worst") in (WARN, BAD):  # a remark no column carries (a reading from before they were kept)
        issues += [{"col": "", "state": row["worst"], "why": w} for w in row.get("why") or [] if w not in told]
    for x in issues:
        node = NODE_OF_COL.get(x.get("col") or "")
        sev = x.get("state") if x.get("state") in (WARN, BAD) else WARN
        out.append({"sev": sev, "where": NODE_NAME.get(node, "這個網站"), "href": f"#step-{node}" if node else "",
                    "state": SEV_WORDS[sev], "reason": strip_fixer(x.get("why") or ""), "when": "", "node": node})
    if mine and mine.get("unexplained"):
        out.append({"sev": BAD, "where": "上次換庫", "href": "#changes", "state": "出錯",
                    "reason": f"說不出原因地離開 {mine['unexplained']:,} 份", "when": "", "node": None})
    if mine and mine.get("look"):
        out.append({"sev": WARN, "where": "上次換庫", "href": "#changes", "state": "留意",
                    "reason": f"可能是我們這邊漏掉的 {mine['look']:,} 份", "when": "", "node": None})
    return sorted(out, key=lambda x: x["sev"] != BAD)


def now_line(row: dict | None, mine: dict | None, reasons: dict, *, read: bool) -> tuple[str, str]:
    """(state, sentence) of the page's top: the site's worst state now and one short sentence (the one problem in
    its own words, or how many there are: the rows are in 問題; a normal site's news, such as an export held by
    hand, in its source's words). row: its line of 流水線's table of sites; mine: its part of the last swap's
    comparison; read: whether 流水線 has a reading yet."""
    rows = site_problems(row, mine)
    state = (row or {}).get("worst") or (OK if read else NONE)
    for x in rows:
        state = worst(state, x["sev"])
    if len(rows) == 1:
        return state, rows[0]["reason"]
    if rows:
        return state, f"有 {len(rows)} 個問題"
    news = [strip_fixer(w) for w in (row or {}).get("why") or []]
    if news:
        return state, "；".join(news)
    if row is None and read:  # nothing of it in 流水線's table: no green mark for what is not known (a site the
        return NONE, "正式庫還沒有這個網站；爬蟲抓到的在下面的網頁核查"  # library has not taken in yet)
    if state == NONE:
        return NONE, "正在讀每一步的紀錄，幾秒後重新整理就有"
    if state in (WARN, BAD):  # a mark that is not 正常 never sits beside 「每一步都正常」 (the lab, 11-17)
        return state, "有一步的數字要看"
    return OK, "每一步都正常"


def _section(q: int, sec_id: str, title: str, body: str, *, aside: str = "", card: bool = True) -> str:
    head = f"<div class='v3-h'><h2>{e(title)}</h2>{aside}</div>" if title else ""
    inner = f"<div class='card hm-card'>{body}</div>" if card else body
    return (f"<section class='v3-q st-q' id='{e(sec_id)}' data-q='{q}' data-question='{e(QUESTIONS[q])}' "
            f"aria-label='{e(title or QUESTIONS[q])}'>{head}{inner}</section>")


def head_html(key: str, *, name: str, unit: str, home: str, now: tuple[str, str], site: dict | None) -> str:
    """The page's top: the state as a mark with its word, the site's name, the sentence, then what the library holds
    of it (site: its row of the library's table of sites, None when the library has none)."""
    state, said = now
    tone = state if state in (OK, WARN, BAD) else "none"
    said = strip_fixer(said)  # a reading from before: no one is named on the page (spec 八.2)
    bits = []
    if site:
        bits.append(f"<span><span class='hm-lab'>庫裡</span><b class='num'>{n(site.get('docs'))}</b> 份文件</span>")
        if site.get("data_as_of"):
            bits.append(f"<span><span class='hm-lab'>資料截至</span><b class='num'>{e(site['data_as_of'])}</b></span>")
    else:
        bits.append("<span>正式庫裡沒有這個網站</span>")
    bits.append(f"<span class='mono faint'>{e(key)}</span>")
    if home:
        bits.append(f"<a href='{e(home)}' target='_blank' rel='noopener' data-tip='{e(home)}'>學校的這個網站{icon('out', 11)}</a>")
    sep = "<span class='hm-sep'>·</span>"
    tip = "\n".join(x for x in (key, home) if x)
    return (f"<header class='v3-q st-head' id='now' data-q='1' data-question='{e(QUESTIONS[1])}'><div class='card hm-card'>"
            f"<div class='hm-pad'><div class='hm-now'><div class='v3-state' data-state='{e(tone)}'><span class='v3-dot'>"
            f"{icon(STATE_ICON.get(state, 'dash'), 20)}</span><b>{e(TONE_WORDS.get(tone, ''))}</b></div>"
            f"<div class='v3-main'><div class='st-name'><h1 data-tip-h='網站代號和首頁' data-tip='{e(tip)}'>{e(name)}</h1>"
            + (f"<span class='st-unit'>{e(unit)}</span>" if unit and unit != name else "") + "</div>"
            f"<p class='v3-say st-say'>{e(said)}</p><p class='hm-fresh'>{sep.join(bits)}</p></div></div></div></div></header>")


def problems_section(rows: list[dict], reading: bool) -> str:
    if rows:
        body = f"<div class='hm-pad'><ul class='v3-problems'>{''.join(problem_html(x) for x in rows)}</ul></div>"
    elif reading:
        body = "<p class='v3-empty hm-pad'>正在讀每一步的紀錄</p>"
    else:
        body = f"<p class='v3-none hm-pad'>{mark('ok')} 沒有問題</p>"
    count = f"<span class='hm-count num'>{len(rows)}</span>" if rows else ""
    return _section(3, "problems", "問題", body, aside=count)


# ------------------------------------------------------------------------------------------------ 2 流程


def export_marks(ex: dict) -> tuple[list[list], dict[str, list]]:
    """The export's 缺什么 marks of the site as ([[words, count]], {words: [(text, href)]}): each word in the 11-23
    words (names.rename; the reading gives them so, a reading from before is renamed), two old words that are one new
    word one row, their examples together, each opening 網頁核查 on that page."""
    examples = ex.get("missing_examples") or {}
    counts: dict[str, int] = {}
    items: dict[str, list] = {}
    for w, c in (ex.get("missing") or {}).items():
        word = rename(w)
        counts[word] = counts.get(word, 0) + int(c or 0)
        items.setdefault(word, []).extend((s2t(t) or u, f"/check?ref={quote(u, safe='')}") for u, t in examples.get(w) or [])
    return [[w, c] for w, c in counts.items() if c], items


# what the build left out of one site, in 內容's 建庫 words: the build's own keys for a merged copy and for a deleted
# notice's shell (nknu.content.documents: (副本), (学校删掉)) through pipeline.left_out_words
COPY_WORD, DELETED_WORD = left_out_words("notice(副本)"), left_out_words("notice(学校删掉)")


def site_stages(row: dict | None, detail: dict | None) -> list[dict]:
    """The site's four steps, each on its own terms: {key, name, into, value, drop (into − value), unit, unit_out,
    in_words, out_words, drop_word, lost: [[words, count]], lost_label (the long one, for the hover), lost_title (its
    short name, names.TITLES), items: {words: [(text, href)]}, tip}. into and value are None where a step has no such
    count (建庫 has no count of what went in for one site; a site the library does not hold has only the crawl's).
    Every reason is in the 11-23 words (names.py), the ones 內容 and the drawers say."""
    cells = (row or {}).get("cells") or {}
    detail = detail or {}
    crawl = cells.get("crawl") or {}
    ex = detail.get("export") or {}
    text = detail.get("text") or {}
    site = detail.get("site") or {}

    def step(key: str, into: int | None, value: int | None, **more) -> dict:
        label = more.get("lost_label") or ""
        return {"key": key, "name": NODE_NAME[key], "into": into, "value": value,
                "drop": into - value if into is not None and value is not None else None,
                "lost_title": TITLES.get(label, label), **more}

    got, whole = crawl.get("n"), crawl.get("whole")
    files, rows = ex.get("files"), ex.get("rows")
    marks, marked = export_marks(ex)
    status = text.get("status") or {}
    ok = status.get("ok") if status else None
    total = sum(status.values()) if status else None
    bad: dict[str, list] = {}
    for st, reason, f in text.get("bad") or []:
        bad.setdefault(STATUS_TEXT.get(st, st), []).append((f"{s2t(f)}（{s2t(reason) or '沒寫原因'}）", ""))
    copies = (detail.get("copies") or {}).get("merged") if isinstance(detail.get("copies"), dict) else None
    built = [x for x in ([COPY_WORD, int(copies or 0)], [DELETED_WORD, int(site.get("deleted_notices") or 0)]) if x[1]]
    return [
        step("crawl", whole, got, unit="個網址", unit_out="個網址", drop_word="沒抓到",
             in_words="個這一輪該抓的網址", out_words="個抓到了",
             # the reading's words (pipeline.bucket_words), renamed once more for a reading from before 11-23
             lost=merge([(w, int(c)) for _code, w, c in crawl.get("lost") or [] if c]),
             lost_label="還沒抓到的（照爬蟲自己的分類）", tip=crawl.get("tip") or "", items={}),
        step("export", rows, files, unit="筆", unit_out="筆", drop_word="沒存檔",
             in_words="筆在導出清單上（每一個網頁、檔案或圖片一筆）", out_words="筆存下了檔案",
             lost=marks, lost_label="清單上註明缺東西的（一筆可能缺好幾樣）",
             tip="沒存下檔案的多半照規則本來就不存", items=marked),
        step("totext", total, ok, unit="個檔", unit_out="個檔", drop_word="沒轉成文字",
             in_words="個檔（壓縮檔裡的檔各算一個）", out_words="個轉成文字",
             lost=[[STATUS_TEXT.get(st, st), int(c)] for st, c in status.items() if st != "ok" and c],
             lost_label="沒轉成文字的", tip="", items=bad),
        step("build", None, site.get("docs") if site else None, unit="筆", unit_out="份文件", drop_word="建庫沒收的",
             in_words="", out_words="份文件",
             lost=built, lost_label="沒收成文件的（這個網站分得出的）",
             tip="這個網站進建庫的筆數沒有分開記，所以這一步只有「出」", items={}),
    ]


def _tone(states: list[str | None]) -> str:
    got = [s for s in states if s]
    if BAD in got:
        return "bad"
    if WARN in got:
        return "warn"
    return "ok" if OK in got else "none"


def site_nodes(stages: list[dict], row: dict | None, detail: dict | None) -> list[dict]:
    """The four nodes in home_page.chain_html's shape ({key, name, id, tone, figure, unit, line, line_tone}), each
    with its stage and the remarks of its columns (says). A node's last line is what went into the step (what the
    figure is to be read against); the export's says when the crawler has a newer one the library has not taken."""
    cells = (row or {}).get("cells") or {}
    site = (detail or {}).get("site") or {}
    issues = (row or {}).get("issues") or []
    out = []
    for stage, (key, name, cols) in zip(stages, NODES):
        says = [strip_fixer(x.get("why") or "") for x in issues if x.get("col") in cols]
        value, into = stage["value"], stage["into"]
        if value is None and key != "crawl":  # the library has nothing of the site: the table's own count, if any
            value = (cells.get(cols[0]) or {}).get("n")
        line, line_tone = "", ""
        if key == "crawl" and into is not None:
            line = f"該抓 {into:,} 個"
        elif key == "export":
            newer = (cells.get("export") or {}).get("sub") or ""
            line = newer or (f"清單 {into:,} 筆" if into is not None else "")
        elif key == "totext" and into is not None:
            line = f"進 {into:,} 個檔"
        elif key == "build" and site.get("data_as_of"):
            line = f"資料截至 {site['data_as_of']}"
        out.append({"key": key, "name": name, "id": f"step-{key}",
                    "tone": _tone([(cells.get(c) or {}).get("state") for c in cols]),
                    "figure": n(value) if value is not None else "", "unit": stage["out_words"] if value is not None else "",
                    "line": e(line), "line_tone": line_tone, "stage": stage, "says": says})
    return out


def reason_list(lost: list[list], items: dict[str, list]) -> str:
    """A step's reasons as sorted bars (the largest SHOWN_REASONS and 其他), one scale within the step. A reason with
    a list opens on it in place (each item a link when it has somewhere to go); one without is a plain row."""
    mine = sorted(((w, int(c), items.get(w) or []) for w, c in lost if c), key=lambda r: -r[1])
    if not mine:
        return ""
    shown, rest = mine[:SHOWN_REASONS], mine[SHOWN_REASONS:]
    if len(rest) == 1:  # one more is its own row, not 其他 1 種
        shown, rest = mine, []
    if rest:
        shown.append((f"其他 {len(rest)} 種", sum(r[1] for r in rest), [(f"{r[0]}：{r[1]:,}", "") for r in rest]))
    peak = max(r[1] for r in shown) or 1
    lis = []
    for words, count, its in shown:
        row = (f"<span class='rs-w'>{e(words)}</span><span class='rs-b'><i style='width:{max(1.5, count / peak * 100):.1f}%'>"
               f"</i></span><span class='rs-n'>{count:,}</span>")
        if not its:
            lis.append(f"<li><div class='rs-row'>{row}</div></li>")
            continue
        body = "".join(f"<li><a href='{e(href)}' data-tip='{e(t)}'>{e(t)}</a></li>" if href else f"<li>{e(t)}</li>"
                       for t, href in its[:SHOWN_ITEMS])
        more = min(len(its), count) - SHOWN_ITEMS
        listed = f"<p class='faint small rs-none'>列出 {min(len(its), SHOWN_ITEMS)} 個例子</p>" if count > len(its) else ""
        lis.append(f"<li><details><summary>{row}</summary><ul class='rs-items'>{body}</ul>"
                   + (f"<p class='faint small rs-none'>還有 {more:,} 個</p>" if more > 0 else listed) + "</details></li>")
    return f"<ul class='rs rsd'>{''.join(lis)}</ul>"


def _facts(pairs: list[tuple[str, str]]) -> str:
    """Small facts of a step as a list of name and value (value already escaped; '' leaves the pair out)."""
    rows = "".join(f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>" for k, v in pairs if v)
    return f"<dl class='st-facts'>{rows}</dl>" if rows else ""


def _fold(title: str, body: str, count: str = "") -> str:
    tail = f" <span class='faint'>{e(count)}</span>" if count else ""
    return f"<details class='hm-notes st-fold'><summary>{icon('right', 12)}{e(title)}{tail}</summary>{body}</details>"


def step_more(key: str, detail: dict | None) -> str:
    """What a step's panel adds under its numbers, from pipeline.site_detail: the export's own view of the crawl (each
    row's result, with examples that open 網頁核查 on that page), where and when the export was made and what it
    filled from an older answer, what the library notes of the site."""
    detail = detail or {}
    ex, site = detail.get("export") or {}, detail.get("site") or {}
    if key == "crawl" and ex:
        others = {k: v for k, v in (ex.get("outcomes") or {}).items() if k != "抓到了"}
        if not others:
            return ""
        return _fold("庫用的這份導出清單上，沒抓到的各種結果", _bars(others, outcome_word, ex.get("examples")),
                     f"{sum(others.values()):,}")
    if key == "export" and ex:
        facts = _facts([
            ("導出於", f"<b class='num'>{e(stamp(ex.get('exported_at')))}</b>"),
            ("資料夾", f"<span class='mono'>{e(ex.get('folder') or '')}</span>" if ex.get("folder") else ""),
            ("公告列表的分頁（不進庫）", f"<b class='num'>{n(ex.get('paging'))}</b>" if ex.get("paging") else ""),
            ("爬蟲有更新的一份，庫還沒用", f"<span class='mono'>{e(detail['newer'])}</span>" if detail.get("newer") else "")])
        refilled = sum((ex.get("refill") or {}).values())
        refill = _fold("這次沒抓好、用舊存檔頂替的", refill_html(ex), f"{refilled:,}") if refilled else ""
        return facts + refill
    if key == "build" and site:
        flags = "、".join(detail.get("flags") or [])
        return _facts([
            ("資料截至", f"<b class='num'>{e(site.get('data_as_of') or '')}</b>" if site.get("data_as_of") else ""),
            ("正文沒抓全、改讀整頁", f"<b class='num'>{n(site.get('under_read'))}</b> 頁" if site.get("under_read") else ""),
            ("建庫查出這個網站的問題", e(flags))])
    return ""


def step_panel(x: dict, detail: dict | None) -> str:
    """A picked step's detail: what it does (behind an info button), in and out, one split bar where out + reasons = in
    (the crawl and the to-text step; the export's reasons are marks, 建庫 has no count of what went in), the remarks on
    it, what the reasons add up to, the reasons, the step's facts."""
    stage, tone = x["stage"], x["tone"]
    does = DOES.get(stage["key"], "")
    info = (f"<button type='button' class='hm-info' aria-label='{e(x['name'])}說明' "
            f"data-tip='{e(does)}'>?</button>") if does else ""
    head = (f"<header><h3>{e(x['name'])}</h3>{info}"
            f"<span class='pill {TONE_PILL.get(tone, 'none')}'>{e(TONE_WORDS.get(tone, ''))}</span></header>")
    says = "".join(f"<p class='hm-said'>{e(w)}</p>" for w in x["says"])
    rows = []
    if stage["into"] is not None:
        rows.append(("進", stage["into"], stage["in_words"]))
    if stage["value"] is not None:
        rows.append(("出", stage["value"], stage["out_words"]))
    io = ("<dl class='hm-io'>" + "".join(f"<div><dt>{w}</dt><dd><b class='num'>{c:,}</b> {e(noun)}</dd></div>"
                                         for w, c, noun in rows) + "</dl>") if rows else ""
    bar = ""
    if stage["key"] in ("crawl", "totext") and stage["into"] and stage["value"] is not None:
        bar = split_bar({"bar": [stage["value"], stage["into"]], "lost": stage["lost"],
                         "out": [[stage["out_words"], stage["value"]]]})
    words = ""
    if stage["lost"] or stage["drop"]:
        words = f"<p class='hm-words'>{e(reconcile_words(stage))}</p>"
    tip = f"<p class='hm-words'>{e(stage['tip'])}</p>" if stage["tip"] and stage["key"] != "crawl" else ""
    reasons = reason_list(stage["lost"], stage["items"])
    # the list's short name (names.TITLES, as 內容 heads the same list), its long label in the hover
    title = stage.get("lost_title") or stage["lost_label"]
    long_ = f" data-tip='{e(stage['lost_label'])}'" if stage["lost_label"] and stage["lost_label"] != title else ""
    reasons = f"<h4{long_}>{e(title)}</h4>{reasons}" if reasons else ""
    empty = "" if rows else "<p class='v3-empty'>讀不到這一步的數字</p>"
    return (f"<article class='hm-panel' data-panel='{e(x['id'])}'>{head}{io}{bar}{says}{words}{tip}"
            f"{reasons}{step_more(stage['key'], detail)}{empty}</article>")


def first_pick(nodes: list[dict]) -> str:
    """The step the side shows before one is picked: the one to look at (出錯 before 留意), else the first."""
    for tone in ("bad", "warn"):
        hit = next((x for x in nodes if x["tone"] == tone), None)
        if hit is not None:
            return hit["id"]
    return nodes[0]["id"] if nodes else ""


# ------------------------------------------------------------------------------------------------ 4 文件


def kinds_html(key: str, counts: dict[str, list[int]] | None, tables: str) -> str:
    """The site's documents as one bar split by kind (the kinds' own colours; the names and counts under it), then how
    many of 公告、網頁、附件 have no date as one violet meter, then the 資料庫 queries that list them (tables: 資料庫's
    origin; '' leaves the links out). counts None: still being counted."""
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
        return ("<div data-counting><span class='pill none'>計算中</span> <span class='soft'>正在數這個網站各類型的文件"
                f"</span>{links}</div>")
    total = sum(v[0] for v in counts.values())
    if not total:
        return "<p class='v3-empty'>庫裡沒有這個網站的文件</p>"
    blocks, legend = [], []
    for kind, word, k in KINDS:
        count, undated = counts.get(kind, [0, 0])
        if not count:
            continue
        tip = f"{count:,} 份" + (f"，其中沒日期 {undated:,} 份" if kind in DATED else "")
        blocks.append(f"<i style='flex-grow:{count};background:var(--k{k})' data-tip-h='{e(word)}' data-tip='{e(tip)}'></i>")
        legend.append(f"<li data-tip-h='{e(word)}' data-tip='{e(tip)}'><i style='background:var(--k{k})'></i>"
                      f"<span>{e(word)}</span><b>{count:,}</b></li>")
    dated = sum(counts.get(kind, [0, 0])[0] for kind in DATED)
    undated = sum(counts.get(kind, [0, 0])[1] for kind in DATED)
    meter = ""
    if dated:
        meter = (f"<div class='st-undated'><span>沒日期</span><span class='st-track' role='img' "
                 f"aria-label='公告、網頁、附件 {dated:,} 份裡 {undated:,} 份沒有日期'>"
                 f"<i style='width:{max(0.5, undated / dated * 100):.1f}%'></i></span>"
                 f"<span><b>{undated:,}</b> 份<span class='faint'>　公告、網頁、附件共 {dated:,} 份</span></span></div>")
    return (f"<div class='st-kbar' role='img' aria-label='各類型文件的份數，共 {total:,} 份'>{''.join(blocks)}</div>"
            f"<ul class='st-legend'>{''.join(legend)}</ul>{meter}{links}")


# ------------------------------------------------------------------------------------------------ 5 每晚紀錄


def nights_html(hist: dict | None) -> str:
    """A row a night the record holds, the newest first: what came into the library of this site and what left, what
    left with no reason, the site's documents and how many have no date after that night. A night that was not
    counted site by site says so in place of the counts."""
    if not hist or not hist.get("enabled"):
        return (f"<p class='hm-note st-first'>{mark('warn')}沒有在記每晚的紀錄：{e((hist or {}).get('why') or '沒有紀錄')}</p>")
    recs = [r for r in hist.get("nights") or [] if r][-SHOWN_SITE_NIGHTS:]
    if not recs:
        return "<p class='v3-empty hm-pad'>還沒有每晚的紀錄：第一晚跑完以後就有</p>"
    if not any(r.get("docs") is not None or r.get("new") or r.get("left") or r.get("unexplained") for r in recs):
        return "<p class='v3-empty hm-pad'>每晚的紀錄裡沒有這個網站：庫裡沒有它的文件</p>"
    trs = []
    for r in reversed(recs):
        # no mark beside the night: the record's state is the whole night's, not this site's (it is in the card)
        first = (f"<th scope='row'><span class='st-night' data-tip='這一晚整體：{e(r.get('word') or '')}'>"
                 f"{e(r['night'][5:])} 夜</span></th>")
        if not r.get("counted"):
            cells = "<td colspan='3' class='hm-na'>這一晚沒有逐站算進出</td>"
        else:
            unx = r.get("unexplained")
            flag = f"<span class='v3-flag'>✕ {unx:,}</span>" if unx else "0" if unx == 0 else "—"
            cells = f"<td class='n'>{n(r.get('new'))}</td><td class='n'>{n(r.get('left'))}</td><td class='n'>{flag}</td>"
        trs.append(f"<tr>{first}{cells}<td class='n'>{n(r.get('docs'))}</td><td class='n'>{n(r.get('undated'))}</td></tr>")
    head = ("<tr><th scope='col'>夜</th><th scope='col' class='n'>新進</th><th scope='col' class='n'>離開</th>"
            "<th scope='col' class='n' data-tip='離開了、建庫和面板都說不出原因的文件'>說不出原因</th>"
            "<th scope='col' class='n' data-tip='那一晚以後庫裡這個網站的文件'>庫裡文件</th>"
            "<th scope='col' class='n' data-tip='公告、網頁、附件裡沒有日期的'>沒日期</th></tr>")
    return ("<div class='hm-scroll' tabindex='0' role='region' aria-label='每晚紀錄'>"
            f"<table class='hm-nights st-nights'><thead>{head}</thead>"
            f"<tbody>{''.join(trs)}</tbody></table></div>")


def changes_html(changes: dict, key: str, departures: dict | None) -> str:
    """What the build found leaving this site for no reason (its own list, nightly.departures_of), then the last
    swap's comparison of this site (its figures, the reasons of what left, its lists)."""
    return (build_items(departures, key) if departures else "") + one_site_changes(changes, key)


# ------------------------------------------------------------------------------------------------ the page


CSS = """
.hm.st{grid-template-areas:"chain side" "problems side" "docs side" "nights side" "changes side"}
.hm.st>#docs{grid-area:docs}
.st-name{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px}
.st-name h1{font-size:20px;font-weight:700;line-height:1.3;letter-spacing:-.01em;overflow-wrap:anywhere}
.st-name .st-unit{padding:1px 8px;border-radius:999px;background:var(--sunk);border:1px solid var(--line);
color:var(--ink2);font-size:12px}
.st-say{font-size:13px;font-weight:500;color:var(--ink2);margin-top:4px}
.hm-fresh a{display:inline-flex;align-items:center;gap:4px;font-weight:600;text-decoration:none}
.hm-fresh a:hover{text-decoration:underline}
.st-pad{padding:18px 20px}
.st-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.st-links .btn{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600}
.st-kbar{display:flex;gap:2px;height:12px;border-radius:999px;overflow:hidden;background:var(--qx)}
.st-kbar i{display:block;min-width:3px;border-radius:2px}
.st-legend{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px 8px;font-size:12px}
.st-legend li{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:9px;background:var(--sunk);
border:1px solid var(--line)}
.st-legend li i{width:9px;height:9px;border-radius:3px;flex:none}
.st-legend b{font-variant-numeric:tabular-nums;color:var(--ink)}
.st-undated{display:grid;grid-template-columns:auto minmax(80px,260px) auto;justify-content:start;gap:12px;
align-items:center;margin-top:14px;padding:10px 14px;border-radius:11px;background:var(--sunk);
border:1px solid var(--line);font-size:13px}
.st-track{height:7px;border-radius:999px;background:var(--qx);overflow:hidden}
.st-track i{display:block;height:100%;border-radius:999px;background:var(--amt)}
.st-undated b{font-variant-numeric:tabular-nums}
table.st-nights{width:100%;min-width:min(100%,540px)}
.st-nights th,.st-nights td{padding-left:18px;padding-right:18px}
.st-nights tbody tr{cursor:default}
.st-night{font-weight:650}
.st-first{border-top:0}
.st-q .tn-chg{margin:0;border:0;border-radius:0;padding:18px 20px;box-shadow:none;background:none}
.st-q .tn-figs{gap:10px}
.st-q .tn-fig{padding:10px 14px;border-radius:12px;background:var(--sunk);border:1px solid var(--line);min-width:96px}
.st-q .v3-panel{margin:16px 20px 0}
.st-q .tn-fig b{font-size:20px;color:var(--ink)}
.st-facts{margin:0;display:flex;flex-direction:column;gap:6px;padding:10px 12px;border-radius:11px;
background:var(--sunk);border:1px solid var(--line);font-size:12px}
.st-facts>div{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 10px}
.st-facts dt{color:var(--ink3);font-size:12px}.st-facts dd{margin:0;min-width:0;overflow-wrap:anywhere;color:var(--ink)}
.st-fold>summary .faint{font-variant-numeric:tabular-nums}
.st-fold .pl-reasons{margin-top:6px}
.st-fold .pl-reasons .pl-eg1{max-width:100%}
.rsd>li{display:block}
.rsd .rs-row,.rsd details>summary{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(40px,1fr) 56px;gap:10px;
align-items:center;font-size:12px;padding:4px 6px;border-radius:8px}
.rsd details>summary{cursor:pointer;list-style:none}
.rsd details>summary::-webkit-details-marker{display:none}
.rsd details>summary .rs-w{text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:3px}
.rsd details>summary:hover{background:var(--hover)}
.rsd details[open]>summary{background:var(--sunk)}
.rs-items{list-style:none;margin:4px 0 8px 10px;padding:0;display:flex;flex-direction:column;gap:3px;font-size:12px}
.rs-items li{overflow-wrap:anywhere}
.rs-items a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);display:block;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.rs-none{margin:4px 0 8px 10px}
.st-check{margin-top:28px}
@media (max-width:1100px){
.hm.st{grid-template-areas:"chain" "side" "problems" "docs" "nights" "changes"}
}
@media (max-width:640px){
.rsd .rs-row,.rsd details>summary{grid-template-columns:minmax(0,1fr) 56px;grid-template-areas:"w n" "b b";gap:2px 10px}
.st-nights th,.st-nights td{padding-left:8px;padding-right:8px}
.st-nights th:first-child{padding-left:14px}
.st-undated{grid-template-columns:auto minmax(0,1fr);justify-content:stretch}
.st-undated>span:last-child{grid-column:1/-1}
}
"""


def site_body(key: str, *, name: str, unit: str, home: str, now: tuple[str, str], detail: dict | None,
              changes: dict, changes_title: str, view: dict | None, archive: bool,
              kinds: dict[str, list[int]] | None, tables: str, row: dict | None = None,
              hist: dict | None = None, labels: dict[str, str] | None = None, departures: dict | None = None,
              read: bool = True) -> str:
    """The page under the top bar (the module docstring's sections). read: whether 流水線 has a reading yet (while it
    has none the page says so and looks again by itself; a site the reading simply does not hold is not waited for). detail: pipeline.site_detail (None: the library
    holds no such site); now: now_line's (state, sentence); changes: the last swap's comparison
    (changes.Changes.snapshot); view: the sweep's site view (check_sites.site_section; None when the ledger has no
    such site); archive: whether the crawler's archive is mounted; kinds: this site's site_kinds (None: being
    counted); tables: 資料庫's origin; row: the site's row of 流水線's table (its cells and remarks); hist:
    history.site_history; departures: the build's own check of the night (nightly.departures_of), its items of this
    site listed. changes_title and labels are no longer drawn (the sections are named for what they are)."""
    reading = not read
    mine = next((x for x in (changes or {}).get("sites") or [] if x.get("site") == key), None)
    rows = site_problems(row, mine)
    stages = site_stages(row, detail)
    nodes = site_nodes(stages, row, detail)
    side = "".join(step_panel(x, detail) for x in nodes)
    if archive:
        check = site_section(view, key)
        start = home_button(view)
    else:
        check = ("<section class='card pad'><span class='pill warn'>沒有掛上爬蟲存檔</span>"
                 "<div class='soft small' style='margin-top:8px'>檢視台啟動時沒有給 --archive，或那個資料夾裡沒有帳本。</div>"
                 "</section>")
        start = ""
    return (f"<style>{charts.CSS}{PIPE_CSS}{SITE_CSS}{TONIGHT_CSS}{HOME_CSS}{CSS}</style>"
            + head_html(key, name=name, unit=unit, home=home, now=now, site=(detail or {}).get("site"))
            + f"<div class='hm st' data-home data-first='{e(first_pick(nodes))}'>"
            + _section(2, "flow", "流程", chain_html(nodes))
            + f"<aside class='hm-side' aria-label='選中的步驟'>{side}</aside>"
            + problems_section(rows, reading)
            + _section(4, "docs", "文件", f"<div class='st-pad'>{kinds_html(key, kinds, tables)}</div>")
            + _section(5, "nights", "每晚紀錄", nights_html(hist))
            + _section(6, "changes", "上次換庫的變動", changes_html(changes or {}, key, departures))
            + "</div>"
            + f"<h2 class='sec st-sec st-check' id='check'>網頁核查{start}</h2>{check}"
            + f"<script>{HOME_JS}</script>"
            # the documents per kind are counted once per library file (a few seconds), the steps read every five
            # minutes: the page looks again when either is not there yet (the picked step stays: the hash is kept)
            + ("<script>setTimeout(()=>location.reload(),5000)</script>" if kinds is None or reading else ""))
