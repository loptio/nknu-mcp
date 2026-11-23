"""The public pages (design 2026-11-21-outward-interfaces-design §四, §六): the home page, the search results page, the
document page and llms.txt, each a pure function from what search and fetch answer (nknu.retrieval.server) to text.

No page calls a model or writes a sentence of its own about a document: a result shows the school's text, its identity
line, its address at the school and the day its site was last read, as the MCP answer does. Every value is escaped.
The pages work without JavaScript (the one script copies the MCP address). A result's text is in the HTML whole (a
passage, about 500 characters) and the stylesheet folds it to a few lines: a person sees the short form, an agent that
opens the address reads the passage.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

NAME = "高師大校園資訊（非官方）"
NOT_OFFICIAL = "這不是學校的官方服務。資料整理自高師大各單位的公開網站，請以學校原頁為準。"
SCHOOL_TEXT = "以下都是學校原文，不是 AI 寫的；每筆附出處和資料截至日，請以學校原頁為準。"
NOT_FOUND = "查不到。查不到不代表沒有：請換個說法再查（同義詞、全稱或簡稱、學校公告的寫法）。"
OTHER_UNITS_HEAD = "其他單位也有這些相關資料"
CONVERTED = "文字由程式從學校的網頁或檔案轉出，可能有錯字或版面走樣，請以學校原頁為準。"
LOGGED = ("我們記錄的東西：查了什麼字、什麼時候查、回了哪幾份文件，用來看哪些問題查不到。"
          "不記錄 IP 位址，也不記錄你是誰。")
EXAMPLES = ("停修怎麼申請", "學雜費繳費期限", "交通車時刻表", "畢業門檻")
SORTS = (("relevance", "最相關"), ("date", "最新"))

CSS = """
:root{--bg:#fafaf7;--fg:#1c1c1a;--mut:#666660;--line:#deded6;--acc:#1a5fb4;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#eaeae4;--mut:#a0a098;--line:#33332e;--acc:#8ab4f8;
--card:#1f1f1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 system-ui,"PingFang TC","Noto Sans TC",sans-serif}
main{max-width:46rem;margin:0 auto;padding:1rem}
a{color:var(--acc)}
h1{font-size:1.35rem;margin:.6rem 0}h1 a{color:inherit;text-decoration:none}
h2{font-size:1.1rem;margin:1.6rem 0 .4rem}
.note{color:var(--mut);font-size:.9rem}
form.q{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
form.q input[type=search]{flex:1 1 14rem;min-width:0;padding:.6rem .8rem;font:inherit;border:1px solid var(--line);
border-radius:.5rem;background:var(--card);color:var(--fg)}
form.q input[type=text],form.q select{padding:.45rem .6rem;font:inherit;font-size:.9rem;border:1px solid var(--line);
border-radius:.5rem;background:var(--card);color:var(--fg)}
form.q button,button.copy{padding:.6rem 1.1rem;font:inherit;border:0;border-radius:.5rem;background:var(--acc);
color:#fff;cursor:pointer}
@media (prefers-color-scheme:dark){form.q button,button.copy{color:#111}}
.opts{flex-basis:100%;display:flex;gap:.5rem;flex-wrap:wrap}
.ex a{display:inline-block;margin:0 .5rem .3rem 0;font-size:.92rem}
article{background:var(--card);border:1px solid var(--line);border-radius:.6rem;padding:.8rem 1rem;margin:.8rem 0}
article h3{font-size:1.02rem;margin:0 0 .2rem}
.id{color:var(--mut);font-size:.86rem;margin:.1rem 0}
.txt{white-space:pre-wrap;overflow-wrap:anywhere;margin:.5rem 0}
.fold{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:5;line-clamp:5;overflow:hidden}
input.more{display:none}
input.more:checked~.fold{display:block;-webkit-line-clamp:unset;line-clamp:unset}
label.more{color:var(--acc);font-size:.88rem;cursor:pointer}
input.more:checked~label.more{display:none}
.src{font-size:.88rem;display:flex;gap:1rem;flex-wrap:wrap}
code,.url{font-family:ui-monospace,Menlo,monospace;font-size:.9rem;overflow-wrap:anywhere}
.box{border:1px solid var(--line);border-radius:.6rem;padding:.7rem 1rem;background:var(--card);margin:.6rem 0}
ul.toc{padding-left:1.2rem}
footer{margin:2rem 0 1rem;color:var(--mut);font-size:.85rem}
"""

COPY_JS = ("document.querySelectorAll('button.copy').forEach(b=>b.addEventListener('click',()=>{"
           "navigator.clipboard.writeText(b.dataset.text).then(()=>{b.textContent='已複製'})}))")


def doc_href(doc_id: str, part: str = "") -> str:
    """The document page's address: the id as one path segment; part in the query."""
    return f"/doc/{quote(doc_id, safe='')}" + (f"?{urlencode({'part': part})}" if part else "")


def search_href(q: str, *, unit: str = "", sort: str = "relevance", limit: int | None = None) -> str:
    """The results page's address; only what is not at its default is written."""
    args = {"q": q, **({"unit": unit} if unit else {}), **({"sort": sort} if sort != "relevance" else {}),
            **({"limit": str(limit)} if limit else {})}
    return f"/search?{urlencode(args)}"


def page(title: str, body: str, *, noindex: bool = False, script: str = "") -> str:
    """One whole HTML page. noindex: a page that shows the school's text is kept out of search engines (design §四), so
    our copy never ranks above the school's own page."""
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    return ("<!doctype html><html lang=\"zh-Hant-TW\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"{robots}<title>{escape(title)}</title><style>{CSS}</style></head><body><main>"
            f"<h1><a href=\"/\">{escape(NAME)}</a></h1>{body}"
            f"<footer>{escape(NOT_OFFICIAL)}</footer></main>"
            + (f"<script>{script}</script>" if script else "") + "</body></html>")


def search_form(q: str = "", *, unit: str = "", sort: str = "relevance", options: bool = False) -> str:
    """The search box: a plain GET form, so a search is an address that can be passed on."""
    opts = ""
    if options:
        picks = "".join(f'<option value="{v}"{" selected" if v == sort else ""}>{escape(name)}</option>'
                        for v, name in SORTS)
        opts = ('<div class="opts"><input type="text" name="unit" placeholder="只看某個單位（可不填）" '
                f'value="{escape(unit)}" aria-label="單位"><select name="sort" aria-label="排序">{picks}</select></div>')
    return ('<form class="q" action="/search" method="get"><input type="search" name="q" required maxlength="300" '
            f'placeholder="想查什麼？例如：停修怎麼申請" value="{escape(q)}" aria-label="搜尋">'
            f"<button>搜尋</button>{opts}</form>")


def _lines(text: str) -> str:
    return "".join(f"<p>{escape(line)}</p>" for line in text.splitlines() if line.strip())


def home_html(instructions: str, *, mcp_url: str, base: str, docs: int | None, as_of: str | None) -> str:
    """design §四: what this is, the search box, how to connect an AI, what is collected (the handshake instructions,
    the same text an agent gets: server.texts_for), what is logged."""
    examples = "".join(f'<a href="{escape(search_href(e))}">{escape(e)}</a>' for e in EXAMPLES)
    link = f"{base}/search?q=停修"
    size = "".join(f"<p class=\"note\">{escape(s)}</p>" for s in (
        f"目前收錄 {docs:,} 份文件。" if docs else "", f"資料截至 {as_of}。" if as_of else "") if s)
    body = (
        "<p>查高師大各單位公開網站上的公告、規定、表單和行事曆。回給你的是學校原文，附出處和日期，不含 AI 寫的句子。</p>"
        f"<p class=\"note\">{escape(NOT_OFFICIAL)}</p>"
        f"{search_form()}<p class=\"ex note\">試試看：{examples}</p>"
        "<h2>接到你的 AI</h2>"
        "<p>把下面這個網址加進你的 AI，它就能自己來查，不用登入。</p>"
        f"<div class=\"box\"><span class=\"url\">{escape(mcp_url)}</span> "
        f"<button class=\"copy\" data-text=\"{escape(mcp_url)}\">複製</button></div>"
        "<p><b>Claude</b>：設定 → 連接器 → 新增自訂連接器，貼上網址。免費版可以加一個。</p>"
        "<p><b>ChatGPT</b>：設定裡找「應用程式」或「連接器」，新增自訂 MCP 伺服器，貼上網址。"
        "能不能加要看你的方案。</p>"
        "<p><b>Gemini</b>：設定 → Connected Apps → 自訂應用程式，貼上網址。目前不是每個帳號都有這個選項。</p>"
        "<p><b>加不上的 AI</b>：只要它會上網，直接請它打開這樣的連結來查："
        f"<span class=\"url\">{escape(link)}</span></p>"
        f"<h2>收了什麼</h2>{size}{_lines(instructions)}"
        f"<h2>隱私</h2><p>{escape(LOGGED)}</p>")
    return page(NAME, body, script=COPY_JS)


def _result(row: dict) -> str:
    """One result: title (to the document page), identity line, the school's text, the school's page, the day the
    site was last read. The text is whole in the HTML and folded by the stylesheet."""
    doc_id = str(row.get("id", ""))
    rank = row.get("rank", "")
    title = row.get("title") or doc_id
    parts = [f'<h3><a href="{escape(doc_href(doc_id))}">{escape(str(title))}</a></h3>']
    if identity := row.get("identity"):
        parts.append(f'<p class="id">{escape(str(identity))}</p>')
    if status := row.get("status"):
        parts.append(f'<p class="id">{escape(str(status))}</p>')
    text = "\n".join(str(row[k]) for k in ("header", "content") if row.get(k))
    if text:
        fold = len(text) > 220
        if fold:
            parts.append(f'<input class="more" type="checkbox" id="m{rank}">')
        parts.append(f'<div class="txt{" fold" if fold else ""}">{escape(text)}</div>')
        if fold:
            parts.append(f'<label class="more" for="m{rank}">展開</label>')
    src = [f'<a href="{escape(doc_href(doc_id, str(row.get("part") or "")))}">讀全文</a>']
    if url := row.get("url"):
        src.append(f'<a href="{escape(str(url))}" rel="noopener">看學校原頁</a>')
    if as_of := row.get("data_as_of"):
        src.append(f'<span class="note">資料截至 {escape(str(as_of))}</span>')
    parts.append(f'<p class="src">{"".join(src)}</p>')
    return f"<article>{''.join(parts)}</article>"


def _applied(resp: dict) -> str:
    """What the answer says of a unit that was asked for: the unit it took, or that it took none and the near names."""
    unit = (resp.get("applied") or {}).get("unit")
    if not isinstance(unit, dict):
        return ""
    if unit.get("applied") is False:
        near = "、".join(str(n) for n in unit.get("near") or [])  # nknu.retrieval.unitfilter: the nearest names
        return ("<p class=\"note\">認不出這個單位，這次查的是全部單位。"
                + (f"相近的名稱：{escape(near)}。" if near else "") + "</p>")
    return f"<p class=\"note\">只看：{escape(str(unit.get('unit', '')))}</p>" if unit.get("unit") else ""


def _others(resp: dict) -> str:
    """A search with a unit: the best documents of the other units (server.OTHER_UNITS, 11-22), after the results."""
    rows = resp.get("other_units") or []
    if not rows:
        return ""
    return f"<h2>{escape(OTHER_UNITS_HEAD)}</h2>" + "".join(_result(r) for r in rows)


def search_html(resp: dict | None, q: str, *, unit: str = "", sort: str = "relevance", message: str = "") -> str:
    """design §四: the results page. resp None with a message: a refused argument or a failure, said in our own
    words (a failure is no proof of 查無資料). A search with a unit shows the other units' best documents after its
    results, and after the not-found line when the unit had none."""
    form = search_form(q, unit=unit, sort=sort, options=True)
    if resp is None:
        return page(f"{q}｜{NAME}", f"{form}<p>{escape(message)}</p>", noindex=True)
    if not resp.get("found"):
        body = f"{form}{_applied(resp)}<p>{escape(NOT_FOUND)}</p>{_others(resp)}"
        return page(f"{q}｜{NAME}", body, noindex=True)
    results = "".join(_result(r) for r in resp.get("results", []))
    coverage = f"<p class=\"note\">{escape(str(resp['coverage']))}</p>" if resp.get("coverage") else ""
    body = f"{form}<p class=\"note\">{escape(SCHOOL_TEXT)}</p>{_applied(resp)}{results}{_others(resp)}{coverage}"
    return page(f"{q}｜{NAME}", body, noindex=True)


def _toc(doc_id: str, toc: list[dict]) -> str:
    items = "".join(f'<li><a href="{escape(doc_href(doc_id, str(t.get("part", ""))))}">'
                    f'{escape(str(t.get("heading") or "第 " + str(t.get("part", "")) + " 節"))}</a></li>' for t in toc)
    return f"<h2>目錄</h2><ul class=\"toc\">{items}</ul>" if items else ""


def doc_html(resp: dict, doc_id: str, part: str = "") -> str:
    """design §四: fetch for a reader. The school's page first; then the text, the table of contents of a long
    document (each entry a part of this page), the next page of a long section."""
    if not resp.get("found"):
        body = f"{search_form()}<p>找不到這份文件。它可能已經從資料裡移除，請重新搜尋。</p>"
        return page(f"找不到文件｜{NAME}", body, noindex=True)
    doc_id = str(resp.get("id") or doc_id)
    title = str(resp.get("title") or doc_id)
    head = [f"<h2>{escape(title)}</h2>"]
    if identity := resp.get("identity"):
        head.append(f'<p class="id">{escape(str(identity))}</p>')
    if status := resp.get("status"):
        head.append(f'<p class="id">{escape(str(status))}</p>')
    src = []
    if url := resp.get("url"):
        src.append(f'<a href="{escape(str(url))}" rel="noopener"><b>看學校原頁</b></a>')
    if as_of := resp.get("data_as_of"):
        src.append(f'<span class="note">資料截至 {escape(str(as_of))}</span>')
    if isinstance(parent := resp.get("parent"), dict) and parent.get("id"):
        src.append(f'<a href="{escape(doc_href(str(parent["id"])))}">'
                   f'所屬公告：{escape(str(parent.get("title") or parent["id"]))}</a>')
    head.append(f'<p class="src">{"".join(src)}</p>')
    text = "\n".join(str(resp[k]) for k in ("header", "content") if resp.get(k))
    body = [*head, f'<p class="note">{escape(CONVERTED)}</p>']
    if text:
        body.append(f'<article><div class="txt">{escape(text)}</div></article>')
    if nxt := resp.get("next"):
        body.append(f'<p><a href="{escape(doc_href(doc_id, str(nxt)))}">接著讀下一頁</a></p>')
    if part:
        body.append(f'<p><a href="{escape(doc_href(doc_id))}">回到這份文件的開頭</a></p>')
    body.append(_toc(doc_id, resp.get("toc") or []))
    return page(f"{title}｜{NAME}", f"{search_form()}{''.join(body)}", noindex=True)


def llms_txt(instructions: str, *, base: str) -> str:
    """design §六: for an agent that lands on the site: the handshake instructions, then how to reach the data."""
    return (f"# {NAME}\n\n{instructions}\n\n"
            "## 怎麼查\n\n"
            f"- 有 MCP：連 {base}/mcp（免登入，工具 search 和 fetch）。\n"
            f"- 沒有 MCP：打開 {base}/search?q=關鍵字 （可加 &unit=單位名稱、&sort=date、&limit=1～50）。\n"
            f"- 讀一份文件：打開 {base}/doc/文件編號 （搜尋結果裡每筆「讀全文」的連結）。\n"
            f"- 要 JSON：{base}/api/search?q=關鍵字 、{base}/api/fetch?id=文件編號 。\n\n"
            "## 回答使用者時\n\n"
            "- 引用學校原文，附上出處網址和日期。\n"
            "- 查不到就照實說查無資料，不要推測。\n"
            f"- {NOT_OFFICIAL}\n")
