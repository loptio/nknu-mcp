"""品質提醒 on a site's page: each reminder the build raised for the site, with the documents behind it (owner 11-23:
「品质提醒是用了干嘛的，如果我没办法追踪这个有问题的文件来改进和排错的话」, then the design copy's site page).

The lists are the build's own: site_check_docs.json, written by deploy/lab-pipeline/site_report.py at build step 5 beside
the library. They are read only while their built_at is the library's own (an older list would name documents the
library no longer has). A document's title, kind, date and address come from the library. A row opens 文件頁 with the
reminder that listed it (/trace?ref=…&ck=…&site=…&why=…); its hover says every reminder the document is under.

A tab per reminder the site has a number for (flagged ones marked), the chosen tab's documents under it; ?ck= on the
page opens that reminder's tab (the design layer's script)."""
import json
import re
import sqlite3
from html import escape
from pathlib import Path
from urllib.parse import quote

NAME = {"none": "無文件", "self": "標題搜不到", "empty": "網頁無文字", "no_date": "無日期", "same_title": "標題重複",
        "same_opening": "選單混入正文", "fragments": "附件逐字斷行", "spike": "檔案時間雷同"}
WHAT = {"none": "一份文件都沒收進庫", "self": "抽 20 份文件、用自己的標題去搜，排進前 10 的不到七成就提醒",
        "empty": "網頁轉成文字後是空的；超過兩成才提醒", "no_date": "公告、網頁、附件裡沒有日期；超過一半才提醒",
        "same_title": "15 份以上的文件用同一個標題，檢索結果分不出哪份是哪份",
        "same_opening": "三成以上的網頁開頭 80 個字一樣，多半是選單被當成正文讀了進來",
        "fragments": "附件轉出來一行只有一個字（多半是圖表或轉向的表格），裡面的字搜不太到",
        "spike": "同一天有二十個以上的檔共用同一個伺服器時間（搬家或整批上傳），這些檔不拿這個時間當日期"}
ORDER = ["none", "self", "same_title", "same_opening", "no_date", "empty", "fragments", "spike"]
SHOW = 300  # rows drawn a list; the count above says how many there are
KIND_WORD = {"notice": "公告", "page": "一般網頁", "attachment": "附件", "event": "行事曆", "link": "校外連結"}
KIND_PATH = {"notice": "M2.5 6.5v3h2l4.5 3v-9l-4.5 3zM11.5 6a2.5 2.5 0 0 1 0 4", "page": "M2 3.5h12v9H2zM2 6h12",
             "attachment": "M10.5 5.5 6 10a1.4 1.4 0 0 0 2 2l5-5a2.9 2.9 0 0 0-4.1-4.1l-5 5a4.3 4.3 0 0 0 6.1 6.1l3.5-3.5",
             "event": "M2.5 3.5h11v10h-11zM2.5 6.5h11M5.5 2v3M10.5 2v3", "link": "M7 3H3v10h10V9M9.5 2.5h4v4M13.5 2.5 7.5 8.5"}
KIND_COLOUR = {"notice": "var(--k0)", "page": "var(--k1)", "attachment": "var(--k2)", "event": "var(--k3)", "link": "var(--k4)"}
CLOCK = "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zM8 4.8V8l2.2 1.4"


def E(s) -> str:
    return escape(str(s if s is not None else ""), quote=True)


def _svg(path: str, size: int) -> str:
    return (f"<svg class='ki-s' viewBox='0 0 16 16' width='{size}' height='{size}' fill='none' stroke='currentColor' "
            f"stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='{path}'/></svg>")


def kind_icon(kind: str) -> str:
    if kind not in KIND_PATH:
        return E(kind)
    w = KIND_WORD[kind]
    return f"<span class='ki' data-tip='{w}' aria-label='{w}' style='color:{KIND_COLOUR[kind]}'>{_svg(KIND_PATH[kind], 14)}</span>"


def load(folder: Path, built_at: str) -> dict | None:
    """The build's lists, when they belong to this library; None otherwise."""
    path = Path(folder) / "site_check_docs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data if built_at and data.get("built_at") == built_at else None


def _ids(x: dict) -> list[str]:
    out = [m["doc_id"] for m in (x.get("self") or {}).get("misses", [])]
    for g in (x.get("same_title") or {}).get("groups", []):
        out += g["docs"][:SHOW]
    for k in ("same_opening", "no_date", "empty", "fragments"):
        out += (x.get(k) or {}).get("docs", [])[:SHOW]
    for d in (x.get("spike_dates") or {}).get("days", []):
        out += d["docs"][:SHOW]
    return list(dict.fromkeys(out))


def lookup(conn: sqlite3.Connection, ids: list[str]) -> dict:
    """A document's title, kind, date, address, the date words it had (why it has none) and its first lines."""
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = ("SELECT doc_id, kind_class, title, url, ident_date, date_line, ident_kind, substr(text, 1, 600) FROM docs "
             f"WHERE doc_id IN ({','.join('?' * len(part))})")
        for r in conn.execute(q, part):
            lines = [x.strip() for x in (r[7] or "").split("\n") if x.strip()]
            out[r[0]] = {"k": r[1], "t": r[2], "u": r[3], "d": r[4], "why": r[5] or r[6] or "",
                         "s": " / ".join(lines[:14])[:200]}
    return out


class _Card:
    def __init__(self, site: str, x: dict, meta: dict):
        self.site, self.x, self.meta = site, x, meta
        self.k, self.why = "", ""
        self.dock = self._kinds()

    def _kinds(self) -> dict:
        x, out = self.x, {}

        def add(ids, k, why):
            for i in ids:
                out.setdefault(i, []).append((NAME[k], why(i) if callable(why) else why))
        for m in (x.get("self") or {}).get("misses", []):
            out.setdefault(m["doc_id"], []).append((NAME["self"], f"排第 {m['place']} 名" if m.get("place") else "前 50 名都沒有"))
        for g in (x.get("same_title") or {}).get("groups", []):
            add(g["docs"], "same_title", f"「{g['title']}」共 {g['total']:,} 份")
        if (x.get("same_opening") or {}).get("docs"):
            add(x["same_opening"]["docs"], "same_opening", f"開頭和另外 {x['same_opening']['total'] - 1:,} 頁一樣")
        add((x.get("no_date") or {}).get("docs", []), "no_date", lambda i: self.meta.get(i, {}).get("why") or "沒讀到日期")
        add((x.get("empty") or {}).get("docs", []), "empty", "轉成文字後是空的")
        add((x.get("fragments") or {}).get("docs", []), "fragments", "一行只有一個字")
        for d in (x.get("spike_dates") or {}).get("days", []):
            add(d["docs"], "spike", f"{d['day']} 共 {d['total']:,} 份同一時間")
        return out

    def go(self, i: str, why: str = "") -> str:
        m = self.meta.get(i, {})
        q = {"ref": i, "ck": self.k, "site": self.site, "why": why or self.why, "t": m.get("t") or ""}
        return "/trace?" + "&amp;".join(f"{k}={quote(str(v), safe='')}" for k, v in q.items() if v)

    def tip(self, i: str) -> tuple[str, str]:
        m = self.meta.get(i, {})
        ks = sorted(self.dock.get(i, []), key=lambda kw: kw[0] != NAME.get(self.k))
        return E(m.get("u") or i), E("\n".join(f"{n}：{w}" for n, w in ks))

    def plus(self, i: str) -> str:
        more = len(self.dock.get(i, [])) - 1
        th, tb = self.tip(i)
        return f"<span class='sq-plus' data-tip-h='{th}' data-tip='{tb}'>+{more}</span>" if more > 0 else ""

    def row(self, i: str, extra: str = "", by_url: bool = False) -> str:
        m = self.meta.get(i, {})
        t = E(re.sub(r"^https?://", "", m.get("u") or "") if by_url and m.get("u") else m.get("t") or i)
        go = f"<a href='{E(m['u'])}' target='_blank' rel='noopener' data-tip='學校原網頁'>↗</a>" if m.get("u") else ""
        why = {"no_date": "日期原文：" + m["why"] if m.get("why") else "整份讀不到日期", "empty": "轉成文字後一個字都沒有",
               "fragments": "轉出來的字：" + (m.get("s") or "")[:60]}.get(self.k, "")
        th, tb = self.tip(i)
        return (f"<tr><td><a class='sq-t' href='{self.go(i, why)}' data-tip-h='{th}' data-tip='{tb}'>{t}</a>{self.plus(i)}</td>"
                f"<td class='sq-k'>{kind_icon(m.get('k') or '')}</td>{extra}<td class='sq-d'>{E(m.get('d') or '—')}</td>"
                f"<td class='sq-go'>{go}</td></tr>")

    def tables(self, k: str) -> list[dict]:
        """A reminder's lists of documents, as the card draws them and the rest of them are read (api): per list its
        documents (ids), the reminder's words for them (why), what is drawn over it (before), its own column (head,
        cell), whether a row is named by its address (by_url), how many there are (total) and whether the build cut
        it (truncated). One list per title of 標題重複 and per day of 檔案時間雷同; none for 標題搜不到 (a sample of
        20) and 無文件."""
        x = self.x
        v = x.get(k) if k != "spike" else x.get("spike_dates")
        if not v or k in ("self", "none"):
            return []
        if k == "same_title":
            return [{"ids": g["docs"], "why": f"「{g['title']}」這個標題共 {g['total']:,} 份", "by_url": True,
                     "before": f"<h6 class='sq-g'>「{E(g['title'])}」<b>{g['total']:,} 份</b></h6>",
                     "total": g["total"], "truncated": g.get("truncated")} for g in v["groups"]]
        if k == "spike":
            return [{"ids": d["docs"], "why": f"{d['day']} 這一天 {d['total']:,} 份共用伺服器時間，不拿它當日期",
                     "before": f"<h6 class='sq-g'>{E(d['day'])}<b>{d['total']:,} 份共用這一天的伺服器時間</b></h6>",
                     "total": d["total"], "truncated": d.get("truncated")} for d in v.get("days", [])]
        one = {"ids": v.get("docs", []), "total": v.get("total"), "truncated": v.get("truncated")}
        if k == "same_opening":
            one["why"] = f"開頭和另外 {v['total'] - 1:,} 頁一模一樣：{v['opening'][:40]}…"
        elif k == "fragments":
            one["head"] = "<th>轉出來的字</th>"
            one["cell"] = (lambda i: f"<td class='sq-x'><span data-tip='{E(self.meta.get(i, {}).get('s') or '')}'>"
                                     f"{E((self.meta.get(i, {}).get('s') or '')[:40])}…</span></td>")
        elif k == "no_date":
            one["head"] = "<th>日期原文</th>"
            one["cell"] = lambda i: f"<td class='sq-x'>{E(self.meta.get(i, {}).get('why') or '')}</td>"
        return [one]

    def rows(self, t: dict, start: int, count: int) -> str:
        """The <tr>s of one list from start, count of them (the card's first part, or a part api reads)."""
        self.why = t.get("why", "")
        cell = t.get("cell") or (lambda i: "")
        return "".join(self.row(i, cell(i), t.get("by_url", False)) for i in t["ids"][start:start + count])

    def table(self, t: dict, g: int) -> str:
        """One list: its first SHOW rows; when the build listed more, 「再列」 asks for the next SHOW (site.js reads
        data-k, data-g and data-at, api answers) and the note under it says how many are drawn of how many."""
        ids = t["ids"]
        shown = min(len(ids), SHOW)
        n = t["total"] if t.get("total") is not None else len(ids)
        cut = "；建庫只存到上限" if t.get("truncated") else ""
        more = (f"<p class='sq-more' data-n='{n}'>共 {n:,} 份<span class='sq-at'>"
                + (f"，這裡列前 {shown:,} 份" if n > shown else "") + f"</span>{cut}</p>") if n > shown or cut else ""
        if len(ids) > shown:
            more += (f"<button type='button' class='sq-more-b' data-k='{E(self.k)}' data-g='{g}' data-at='{shown}' "
                     f"data-of='{len(ids)}'>再列 {min(SHOW, len(ids) - shown):,} 份</button>")
        return (f"<div class='sq-list'><table class='sq-tb'><thead><tr><th>{'網址' if t.get('by_url') else '文件'}</th>"
                f"<th></th>{t.get('head', '')}<th>日期</th><th></th></tr></thead><tbody>{self.rows(t, 0, SHOW)}</tbody>"
                f"</table>{more}</div>")

    def panel(self, k: str) -> str:
        x = self.x
        v = x.get(k) if k != "spike" else x.get("spike_dates")
        self.k, self.why = k, ""
        if k == "self":
            rows = ""
            for m in v["misses"]:
                res = "第 " + str(m["place"]) + " 名" if m.get("place") else "前 " + str(v["deep"]) + " 名都沒有"
                th, tb = self.tip(m["doc_id"])
                rows += (f"<tr><td><a class='sq-t' href='{self.go(m['doc_id'], '用自己的標題去搜，' + res.replace('第 ', '排第 '))}' "
                         f"data-tip-h='{th}' data-tip='{tb}'>{E(m['title'])}</a>{self.plus(m['doc_id'])}</td>"
                         f"<td class='sq-x' colspan='3'>{res}</td><td></td></tr>")
            return (f"<p class='sq-note'>抽 {v['sampled']} 份、用自己的標題去搜：前 {v['top']} 名找到 {v['found']} 份；下面是沒找到的</p>"
                    f"<table class='sq-tb'><thead><tr><th>用這個標題去搜</th><th colspan='3'>結果</th><th></th></tr></thead><tbody>{rows}</tbody></table>")
        lists = "".join(t.get("before", "") + self.table(t, g) for g, t in enumerate(self.tables(k)))
        if k == "same_opening":
            return (f"<div class='sq-quote' data-tip='這幾頁開頭 80 個字一模一樣（去掉空白）'>{E(v['opening'])}</div>"
                    f"<p class='sq-note'>有開頭的網頁 {v['of']:,} 頁，{v['total']:,} 頁是這一個</p>" + lists)
        if k == "spike":
            if v.get("note"):
                return f"<p class='sq-note'>{E(v['note'])}</p>"
            gap = v["counted"] - v["listed"]
            return (f"<p class='sq-note'>建庫記了 {v['counted']} 份，列得出 {v['listed']} 份"
                    + (f"；差的 {gap} 份是併進其他文件的副本，庫裡沒有它們自己的編號" if gap else "") + "</p>" + lists)
        if k in ("same_title", "fragments", "no_date", "empty"):
            return lists
        return "<p class='sq-note'>這個網站一份文件都沒收進庫；看下面「流程」哪一步沒有往下走。</p>"


def counts(x: dict) -> dict:
    def g(k):
        return (x.get(k) or {}).get("total", 0)
    sp = x.get("spike_dates") or {}
    return {"none": 1 if "none" in (x.get("flags") or []) else 0,
            "self": (x.get("self") or {}).get("sampled", 0) - (x.get("self") or {}).get("found", 0),
            "same_title": g("same_title"), "same_opening": g("same_opening"), "no_date": g("no_date"), "empty": g("empty"),
            "fragments": g("fragments"), "spike": sp.get("counted", 0)}


def card(site: str, data: dict | None, conn: sqlite3.Connection, *, broken: bool = False) -> str:
    """The 品質提醒 section of one site's page; broken: the build's list was there but could not be read."""
    head = f"<section class='v3-q sq' id='quality' data-site='{E(site)}' aria-label='品質提醒'><div class='v3-h'><h2>品質提醒</h2>"
    if broken:
        return (head + "</div><div class='card hm-card'><div class='hm-pad'><p class='sq-note'>這一版庫的提醒清單讀不了"
                "（格式和面板對不上）；網站的其他部分照常</p></div></div></section>")
    if data is None:
        return (head + "</div><div class='card hm-card'><div class='hm-pad'><p class='sq-note'>這一版庫的提醒清單還沒寫出來"
                "（建庫第 5 步寫；和庫的建庫時間對得上才用）</p></div></div></section>")
    x = (data.get("sites") or {}).get(site)
    if not x:
        return (head + "</div><div class='card hm-card'><div class='hm-pad'><p class='sq-note'>這個網站沒有品質提醒</p>"
                "</div></div></section>")
    c = _Card(site, x, lookup(conn, _ids(x)))
    flags = set(x.get("flags") or []) | ({"spike"} if (x.get("spike_dates") or {}).get("counted") else set())
    n = counts(x)
    tabs = [k for k in ORDER if n[k]]
    first = next((k for k in tabs if k in flags), tabs[0] if tabs else "")
    btn = "".join(f"<button type='button' data-k='{k}' class='{'flag' if k in flags else ''}' data-tip='{E(WHAT[k])}' "
                  f"aria-pressed='{str(k == first).lower()}'>{NAME[k]}<b>{n[k]:,}</b></button>" for k in tabs)
    panes = "".join(f"<div class='sq-p' data-k='{k}'{'' if k == first else ' hidden'}><div class='sq-scroll'>{c.panel(k)}</div></div>"
                    for k in tabs)
    when = E((data.get("built_at") or "")[5:16].replace("T", " "))
    clock = (f"<span class='ki-t' data-tip='這一次建庫時查的；每次建庫後自動檢查每個網站，只提醒，不擋建庫'>"
             f"{_svg(CLOCK, 12)}{when}</span>")
    return (head + clock + "</div><div class='card hm-card'><div class='hm-pad'>"
            + (f"<div class='sq-tabs' role='tablist'>{btn}</div>{panes}" if tabs else "<p class='sq-note'>這個網站沒有品質提醒</p>")
            + "</div></div></section>")


def _whole(value) -> int:
    """A query string's whole number; anything else is 0."""
    text = str(value if value is not None else "")
    return int(text) if text.isdecimal() else 0


def api(db_path: Path, params) -> tuple[int, dict]:
    """GET /site/quality?site=&k=&g=&at=: the next SHOW rows of one of a reminder's lists on a site's page (its
    「再列」 button; port audit 11-23: a list over SHOW could not be read past its first part). k: the reminder, g: which
    of its lists (a title of 標題重複, a day of 檔案時間雷同; 0 for the others), at: where this part starts. (status,
    {rows: the <tr>s as the card draws them, at: where the next part starts, of: how many the build listed}); 404 when
    the library has no such list (the build's lists are read only while they are this library's, as card's are)."""
    site, k = str(params.get("site") or "").strip(), str(params.get("k") or "")
    g, at = _whole(params.get("g")), _whole(params.get("at"))
    none = 404, {"error": "這一版庫沒有這份清單"}
    if not site or k not in NAME:
        return none
    conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
    try:
        built = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        x = ((load(Path(db_path).parent, built[0] if built else "") or {}).get("sites") or {}).get(site)
        lists = _Card(site, x, {}).tables(k) if x else []
        if g >= len(lists):
            return none
        part = lists[g]["ids"][at:at + SHOW]
        c = _Card(site, x, lookup(conn, part))
        c.k = k
        return 200, {"rows": c.rows(c.tables(k)[g], at, SHOW), "at": at + len(part), "of": len(lists[g]["ids"])}
    finally:
        conn.close()


def lay_out(body: str, quality: str) -> str:
    """The site page's body with 品質提醒 put in (before the steps), 「‹ 內容」 above the head, 每晚紀錄 gone (the nights
    are on 內容) and 問題 gone when it says there are none."""
    body = body.replace("<header class='v3-q st-head'", "<a class='sq-back' id='sqBack' href='/sites'>‹ 內容</a>"
                        "<header class='v3-q st-head'", 1)
    body = body.replace("<div class='hm st' data-home", quality + "<div class='hm st' data-home", 1)
    body = re.sub(r"<section class='v3-q st-q' id='nights'.*?</section>", "", body, count=1, flags=re.DOTALL)
    return re.sub(r"<section class='v3-q st-q' id='problems'[^>]*>(?:(?!</section>).)*沒有問題(?:(?!</section>).)*</section>",
                  "", body, count=1, flags=re.DOTALL)
