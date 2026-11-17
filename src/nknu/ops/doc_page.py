"""文件 (檢視台 /doc): one box to find a document of the library or a page of the crawler's archive, by a few words of
its title, its address or its id, typed in Simplified or Traditional; and, with nothing typed, the school's 網頁核查
(the four deciding numbers and the misses merged across sites, which are about pages).

Structure design docs/superpowers/specs/2026-11-17-panels-structure-design.md section 二 (owner: 「可以按你的建议走吧，不用
问我了」): 文件追蹤 and 網頁核查's entry were two boxes that each found half (a document's title; a page's name or a site),
so the owner had to know which one a thing was in. Now one box finds both and says which is which: 庫裡的文件 open their
trace (/trace?ref=…), 抓到的網頁 their check (/check?url=…), and the sites it names their page (/site). The trace and
the single-page check stay as they were. /trace with nothing or with words, and /check with nothing or with words,
come here (nknu.ops.web).

The page draws what nknu.ops.web finds (its find_titles, lookup.resolve, the sweep's find) and computes nothing. The
group names were tried on a cold reader twice (owner's rule for new names, 11-16): 「文件／網頁」 alone read as unclear,
and so did 「庫裡的文件／存檔的網頁」, so the pages say the step they reached, 抓到的網頁, against the documents in the
library (庫裡的文件, the word 處理流程's numbers use); 「網頁核查」 on this page read as out of place, so it says 全校. 【Claude】 redesign
2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from nknu.ops.check_page import CHECK_CSS, CHECK_JS
from nknu.ops.check_sites import DRAWER, SITES_CSS, SITES_JS, page_rows, school_section
from nknu.ops.look import KIND_ICON, e, icon
from nknu.ops.trace import KINDS

DOCS_SHOWN = 40  # documents listed for what is typed; the rest counted 【Claude】

CSS = """
form.ask.dc-ask{margin:0 0 14px}
.dc-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
@media (max-width:900px){.dc-two{grid-template-columns:minmax(0,1fr)}}
.dc-two .plist{margin-top:0}
.dc-sites{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 14px;font-size:13px}
.dc-sites .lab{color:var(--ink3);font-size:12px;margin-right:2px}
.dc-sites a{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--line2);border-radius:8px;
background:var(--surface);color:var(--ink);text-decoration:none}.dc-sites a:hover{background:var(--hover)}
.dc-sites a .ic{color:var(--ink3)}
a.prow .pn .ic{margin-right:6px;color:var(--ink3);vertical-align:-2px}
.dc-school h2.sec{margin-top:18px}
"""

# As one types, the list follows (one request a moment after the last key, the answer to the latest kept), and while the
# sweep is still reading or checking it fills itself in, never under the pointer or the keyboard. The page works without
# it (the box is a form).
JS = """
(()=>{const box=document.querySelector('#doc-ask input'),list=document.getElementById('doc-list');if(!box||!list)return;
let seq=0,timer=null,poller=null;
const busy=()=>list.matches(':hover')||list.contains(document.activeElement);
const poll=()=>{clearTimeout(poller);if(list.querySelector('[data-running]'))poller=setTimeout(()=>load(false),4000)};
const load=asked=>{const q=box.value.trim();if(!asked&&busy()){poll();return}const my=++seq;
  fetch('/doc/find?'+new URLSearchParams({q}),{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{
    if(!j||my!==seq)return;list.innerHTML=j.html;
    if(asked)history.replaceState(null,'','/doc'+(q?'?'+new URLSearchParams({q}):''));poll()}).catch(()=>{})};
box.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>load(true),200)});poll()})();
"""


def doc_form(q: str, *, focus: bool = True) -> str:
    return (f"<form class='ask dc-ask' id='doc-ask' action='/doc' method='get'><label class='box'>{icon('search', 16)}"
            f"<input name='q' value='{e(q)}' placeholder='標題裡的字、網址或文件編號' aria-label='要找的文件或網頁'"
            f" autocomplete='off'{' autofocus' if focus else ''}></label><button>找</button></form>")


def _doc_rows(docs: list[dict]) -> str:
    out = []
    for x in docs[:DOCS_SHOWN]:
        kind = KINDS.get(x.get("kind"), x.get("kind") or "")
        under = " · ".join(y for y in (x.get("name") or x.get("site"), kind, x.get("date") or "無日期") if y)
        out.append(f"<li><a class='prow' href='/trace?{urlencode({'ref': x['doc_id']})}' "
                   f"data-tip-h='{e(x['doc_id'])}' data-tip='看它從抓取到搜尋的每一步'>"
                   f"<span class='pn'>{icon(KIND_ICON.get(x.get('kind'), 'page'), 13)}{e(x.get('title') or x['doc_id'])}"
                   f"</span><span class='pp'>{e(under)}</span></a></li>")
    return f"<ol>{''.join(out)}</ol>"


def results_html(q: str, docs: list[dict], found: dict | None, *, page_ref: str = "", archive: bool = True) -> str:
    """What the box found for q: the sites it names (their page), then 庫裡的文件 (their trace) beside 抓到的網頁 (their
    check). docs: the library's documents (the one q names first, then the ones whose titles hold it, newest first;
    one more than DOCS_SHOWN at most says there are more); found: the sweep's find (None: no archive); page_ref: q
    when it is an address or an id, offered as a page to check even when the ledger lists no page by it."""
    sites = (found or {}).get("sites") or []
    out = ""
    if sites:
        out += ("<nav class='dc-sites' aria-label='網站'><span class='lab'>網站</span>"
                + "".join(f"<a href='/site?site={e(quote(s['key'], safe=''))}' data-tip='{e(s['key'])}'>{e(s['name'])}"
                          f"{icon('right', 12)}</a>" for s in sites[:12]) + "</nav>")
    more = "<div class='more-n'>還有更多，多打幾個字</div>" if len(docs) > DOCS_SHOWN else ""
    shown = min(len(docs), DOCS_SHOWN)
    lib = (f"<section class='card plist'><div class='pl-head'><h2>庫裡的文件<span class='faint'>"
           f"{shown}{'+' if more else ''}</span></h2></div>"
           + (_doc_rows(docs) + more if docs else f"<div class='why'>標題裡沒有「{e(q)}」的文件，它也不是文件編號或網址</div>")
           + "</section>")
    pages = (found or {}).get("pages") or []
    total = (found or {}).get("pages_total", 0)
    progress = (found or {}).get("progress") or {}
    listed = {p.get("url") for p in pages}
    direct = ""
    if page_ref and page_ref not in listed:
        direct = (f"<ol><li><a class='prow' href='/check?{urlencode({'url': page_ref})}' data-live-href='{e(page_ref)}'>"
                  f"<span class='pn'>{icon('search', 13)}核查這一頁</span><span class='pp'>{e(page_ref)}</span></a></li></ol>")
    if not archive:
        body = direct + "<div class='why'>沒有掛上爬蟲存檔</div>"
    elif pages:
        rest = total - len(pages)
        body = direct + page_rows(pages, with_site=True) + (
            f"<div class='more-n'>還有 {rest:,} 頁，多打幾個字</div>" if rest > 0 else "")
    else:
        ready = progress.get("ready", True)
        why = (f"網頁名和網址裡都沒有「{q}」" if ready else "讀不了帳本" if progress.get("phase") == "failed"
               else "帳本還在讀，讀好就找得到")
        body = direct + f"<div class='why'>{e(why)}</div>"
    # asked again only while the sweep reads the ledger: a sweep that failed is not ready either, and the page asked
    # every 4 s for good (review 2026-11-17)
    running = " data-running='1'" if progress.get("running") and not progress.get("ready", True) else ""
    web = (f"<section class='card plist'{running}><div class='pl-head'><h2>抓到的網頁<span class='faint'>{total:,}</span>"
           f"</h2></div>{body}</section>")
    return f"{out}<div class='dc-two'>{lib}{web}</div>"


def school_html(view: dict | None, *, archive: bool = True) -> str:
    """With nothing typed: the school's 網頁核查 (check_sites.school_section)."""
    return (f"<div class='dc-school'><h2 class='sec'>全校網頁核查</h2>"
            f"{school_section(view, archive=archive)}</div>")


def doc_body(q: str, inner: str) -> str:
    """The page: the box, the list under it (inner: results_html or school_html, the part /doc/find sends again),
    the drawer a merged row of the school's lists opens in, and the scripts."""
    return (f"<style>{CHECK_CSS}{SITES_CSS}{CSS}</style>{doc_form(q, focus=not q)}<div id='doc-list'>{inner}</div>"
            f"{DRAWER}<script>{CHECK_JS}</script><script>{SITES_JS}</script><script>{JS}</script>")
