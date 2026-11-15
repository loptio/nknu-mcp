"""網頁核查's page (nknu.ops.web serves it at /check; design docs/superpowers/specs/2026-11-16-page-check-design.md
sections 一 to 三): the address box with 「開真實網頁」, the page's own facts in one line, the archived page on the left
(/check/raw in a frame that runs no script), and on the right how many links, images and buttons are in each state, the
ones to look at one by one, and the warnings of section 四 that hold for this page. It draws what nknu.ops.pagecheck
returns and computes nothing.

Colours (design section 二): only look.CSS's validated good / warn / bad and its greys; never --acc (blue names 公告 and
一般網頁) nor violet (amounts), owner 11-15. Names are plain nouns and their detail floats in the panels' card (owner 11-15:
「很多名字看着不直觉…」); the layout and every word are 【Claude】's.

The live page (design section 三): the school's servers refuse to be framed, so the real page opens in a window of its
own named nknu_live, and a browser lets a page open or steer a window only inside a click; so 「開真實網頁」 opens it
(on the right half of the screen) and remembers in sessionStorage that it is open, and from then on a click on a link
of the archived page, on 「核查」 of a row, or the address box's submit, steers that same window as the panel moves on.
Every steer passes the same placement, so a window the owner closed comes back on the right half, not as a tab over
the panel; a document id or an address without its scheme steers through our own /check/go, which answers with the
school address it resolves to (the browser lets a click steer the window, not a later answer). The window keeps its
opener: Chrome finds a named window again only through it (the review of 11-16 tried cutting it: the next steer opened
a third window), so the panel tab's own risk from the school page is accepted (design section 三)."""

from __future__ import annotations

from urllib.parse import unquote, urlencode

from nknu.ops.look import e, icon, tw
from nknu.ops.pagecheck import BUCKET_NAME, BUCKET_TIP, BUCKETS, STATES, WHATS, WHO_WORDS, live_url

PROBLEMS_SHOWN = 300  # rows listed at most; the rest counted 【Claude】
WHAT_WORDS = dict(WHATS)
# the warnings of design section 四, by pagecheck.caveats' keys: (a plain name, what it means)
CAVEATS = {
    "snapshot": ("帳本用唯讀快照讀", ("帳本的 -wal 不在、目錄又是唯讀的，所以整份帳本照主檔讀（immutable）。爬蟲沒在跑時這樣讀是對的；"
                                    "爬蟲在跑時，最新的紀錄可能還沒寫進主檔，這一頁會比爬蟲舊一點。")),
    "press_answer": ("按鈕按出來的頁", ("你要的這一份（{press}）沒有自己的網址，是在列表上按按鈕（或翻頁）按出來的："
                                     "下面是那次按鍵存下的回答。它上面的連結和圖記在列表名下。")),
    "press_unfound": ("按鍵找不到", ("你要的這一份（{press}）是按鈕按出來的，帳本裡卻找不到那次按鍵："
                                   "下面只是它的列表第 1 頁，不是它本身。")),
    "spelled": ("帳本寫法不同", "你給的網址帳本裡沒有這個寫法，帳本記的是 {spelled}；下面是那一頁。"),
    "redirected": ("這個網址會轉址", "帳本裡的這個網址轉到別的網址，下面是轉到的那一頁。"),
    "pressed": ("還有按出來的頁", ("列表第 2 頁起、按出來的公告沒有自己的網址；它們的連結和圖也記在這一頁名下。"
                                  "按網址只看得到第 1 頁。")),
    "older_links": ("帳本多的連結", ("帳本只指最近一次抓的存檔，連結表卻是歷次（和按鈕按出來的頁）累積的："
                                    "有 {n} 個連結不在這份存檔上。")),
    "js": ("要 JavaScript", "存的是伺服器原始的 HTML，腳本生出來的內容不在裡面；和真實網頁並排時顯得空是對的，不是漏。"),
    "zone_changed": ("正文區改過", ("電機系、資料開放查詢的正文區是爬完以後才加的；框框照現在的規則畫，"
                                  "爬蟲當時記的區塊是舊的。")),
    "nothing_read": ("建庫不讀", "平台規則的正文區在這一頁對不上，建庫照規則不讀它的字。"),
    "frameset": ("框架頁", "內容在框架指的另一個網址：點框框核查那一頁。"),
}
READING = {"area": "讀平台設定的正文區", "nothing": "正文區對不上，不讀", "whole": "讀整頁",
           "body": "讀整頁，去掉頁首、選單、頁尾"}
ZONE_SHORT = {"in": "正文區", "files": "附件區", "out": "選單等"}

CHECK_CSS = """
main{max-width:1680px}
.chk-ask{align-items:stretch}form.ask.chk-ask button{background:var(--ink);color:var(--surface)}
form.ask.chk-ask input:focus{outline-color:var(--ink2)}
form.ask.chk-ask .live{background:var(--surface);color:var(--ink);border:1px solid var(--line2)}
form.ask.chk-ask .live:hover{background:var(--hover);opacity:1}form.ask.chk-ask .live[disabled]{opacity:.45;cursor:default}
.chk-facts{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin:12px 2px 0;font-size:13px;color:var(--ink2)}
.chk-facts .url{min-width:0;max-width:min(100%,640px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
color:var(--ink3);font-size:12px}
.chk{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:14px;margin-top:12px;align-items:start}
@media (max-width:1000px){.chk{grid-template-columns:minmax(0,1fr)}}
.chk-page{overflow:hidden;padding:0}
.chk-frame{display:block;width:100%;height:calc(100vh - 190px);min-height:460px;border:0;background:#fff}
.chk-side{display:flex;flex-direction:column;gap:12px;min-width:0}
@media (min-width:1001px){.chk-side{position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto}}
.chk-side h2{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--ink2);font-weight:650;margin:0 0 8px}
.chk-side h2 .faint{font-weight:400;margin-left:auto}
[data-b]{--c:var(--ink3)}[data-b=got],[data-b=look]{--c:var(--good)}[data-b=next],[data-b=review]{--c:var(--warn)}
[data-b=failed],[data-b=missing]{--c:var(--bad)}
.sw{position:relative;display:inline-block;flex:none;width:22px;height:12px;border-bottom:2.5px solid var(--c)}
.sw::after{content:"";position:absolute;right:-2px;bottom:-5px;width:7px;height:7px;border-radius:50%;background:var(--c)}
.sw[data-b=look]::after{background:var(--warn)}.sw[data-b=review],.sw[data-b=missing]{border-bottom-style:dashed}
.sw[data-b=gone]{border-bottom:0;background:linear-gradient(var(--c),var(--c)) center/100% 2px no-repeat}
.sw[data-b=notlink]{border-bottom:1px solid var(--line2)}.sw[data-b=notlink]::after{display:none}
table.legend2{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
table.legend2 th{font-size:11.5px;color:var(--ink3);font-weight:600;text-align:right;padding:0 0 5px 8px}
table.legend2 th:first-child{text-align:left;padding-left:0}
table.legend2 td{padding:4px 0 4px 8px;text-align:right;border-top:1px solid var(--line)}
table.legend2 td:first-child{text-align:left;padding-left:0}
table.legend2 td .nm{display:inline-flex;align-items:center;gap:9px}
table.legend2 td.z{color:var(--ink3)}table.legend2 tr.sum td{font-weight:650;border-top:1px solid var(--line2)}
.probs{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
.probs li{display:flex;align-items:center;gap:6px;min-width:0}
.prob{flex:1 1 auto;display:flex;align-items:center;gap:9px;min-width:0;padding:6px 8px;border:0;border-radius:8px;
background:transparent;text-align:left;cursor:pointer;font-size:13px}.prob:hover{background:var(--hover)}
.prob .t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prob small{margin-left:auto;padding-left:8px;color:var(--ink3);white-space:nowrap;font-size:11.5px}
.prob.lost{opacity:.5}
.probs .go{flex:none;font-size:11.5px;color:var(--ink2);text-decoration:none;padding:3px 7px;border-radius:7px}
.probs .go:hover{background:var(--hover);color:var(--ink)}
.cav{display:flex;flex-wrap:wrap;gap:6px}
.cav span{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:999px;background:var(--warn-bg);
color:var(--warn-ink);font-size:12px;font-weight:600}
.cav span.plain{background:var(--hover);color:var(--ink2);font-weight:500}
.verdict .pill{font-size:13.5px;padding:4px 12px}
.chk-empty{margin-top:14px}
"""

# The panel's script (design section 三). The archived page runs no script of its own (its frame's sandbox has no
# allow-scripts); this page's script, of the same origin, handles a click and a hover there, and draws the region
# labels and the flash of a row. The floating card is the panels' (look.TIP_JS's .tip), placed over the frame. A click
# on a picture inside a link follows the link (a banner, a download icon), not the picture.
CHECK_JS = """
(()=>{
const NAME='nknu_live';
const isWeb=u=>/^https?:\\/\\//i.test(u||'');
const remembered=()=>{try{return sessionStorage.getItem('nk-live')==='1'}catch(_){return false}};
const feats=()=>{const w=Math.floor(screen.availWidth/2),h=screen.availHeight,l=(screen.availLeft||0)+w,
  t=screen.availTop||0;return 'popup,width='+w+',height='+h+',left='+l+',top='+t};
const live=(ref,first)=>{if(!ref||!(first||remembered()))return;
  const url=isWeb(ref)?ref:'/check/go?'+new URLSearchParams({ref:ref});
  window.open(url,NAME,feats());
  if(first)try{sessionStorage.setItem('nk-live','1')}catch(_){}};
const go=url=>{location.href='/check?'+new URLSearchParams({url:url})};
const btn=document.querySelector('button[data-live]');
if(btn)btn.addEventListener('click',()=>live(btn.getAttribute('data-live'),true));
const form=document.getElementById('chk-ask');
if(form)form.addEventListener('submit',()=>live(form.querySelector('input').value.trim(),false));
document.querySelectorAll('a[data-live-href]').forEach(a=>a.addEventListener('click',()=>live(a.getAttribute('data-live-href'),false)));
const frame=document.querySelector('iframe.chk-frame');
if(!frame)return;
const tip=document.createElement('div');tip.className='tip';tip.hidden=true;tip.setAttribute('role','tooltip');
document.body.append(tip);let cur=null;
const fill=el=>{tip.textContent='';const h=el.getAttribute('data-tip-h'),t=el.getAttribute('data-tip');
  if(h){const b=document.createElement('b');b.textContent=h;tip.append(b)}
  if(t){const p=document.createElement('span');p.textContent=t;tip.append(p)}};
const place=(x,y)=>{const w=tip.offsetWidth,h=tip.offsetHeight;let left=x+14,top=y+16;
  if(left+w>innerWidth-8)left=Math.max(8,x-w-12);if(top+h>innerHeight-8)top=Math.max(8,y-h-12);
  tip.style.left=left+'px';tip.style.top=top+'px'};
const hide=()=>{tip.hidden=true;cur=null};
const labels=doc=>{doc.querySelectorAll('.nk-label').forEach(x=>x.remove());
  const win=doc.defaultView;doc.querySelectorAll('[data-nk-label]').forEach(el=>{const r=el.getBoundingClientRect();
    if(!r.width&&!r.height)return;const s=doc.createElement('span');s.className='nk-label';
    s.textContent=el.getAttribute('data-nk-label');s.style.left=(r.left+win.scrollX)+'px';
    s.style.top=Math.max(0,r.top+win.scrollY-19)+'px';doc.body.append(s)})};
const wire=()=>{const doc=frame.contentDocument;if(!doc||!doc.body||doc.body.dataset.nkWired)return;
  doc.body.dataset.nkWired='1';labels(doc);
  doc.defaultView.addEventListener('resize',()=>labels(doc));
  doc.addEventListener('click',e=>{e.preventDefault();
    const el=e.target.closest('a[data-nk-href],area[data-nk-href]')||e.target.closest('[data-nk-href]');if(!el)return;
    const href=el.getAttribute('data-nk-href');if(href&&isWeb(href)){live(href,false);go(href)}},true);
  doc.addEventListener('submit',e=>e.preventDefault(),true);
  doc.addEventListener('pointermove',e=>{const el=e.target.closest('[data-tip],[data-tip-h]');if(!el){hide();return}
    if(el!==cur){fill(el);cur=el}tip.hidden=!tip.childNodes.length;const r=frame.getBoundingClientRect();
    place(e.clientX+r.left,e.clientY+r.top)});
  doc.addEventListener('pointerleave',hide);doc.addEventListener('scroll',hide,true)};
frame.addEventListener('load',wire);
if(frame.contentDocument&&frame.contentDocument.readyState==='complete')wire();
document.querySelectorAll('button.prob[data-n]').forEach(b=>b.addEventListener('click',()=>{
  const doc=frame.contentDocument,el=doc&&doc.querySelector('[data-nk="'+b.getAttribute('data-n')+'"]');
  if(!el||!el.getClientRects().length){b.classList.add('lost');return}
  el.scrollIntoView({block:'center',behavior:'smooth'});el.classList.remove('nk-flash');void el.offsetWidth;
  el.classList.add('nk-flash');setTimeout(()=>el.classList.remove('nk-flash'),1800)}));
})();
"""


def check_form(value: str, url: str, *, focus: bool = True) -> str:
    """The address box, its button and 「開真實網頁」 (disabled when there is no school address to open)."""
    target = live_url(url)
    live = (f"<button type='button' class='live' data-live='{e(target)}' data-tip='在螢幕右半邊開學校網站上的這一頁；"
            "之後點存檔上的連結，它會跟著跳。那是學校現在的網頁，和存檔抓的時候可能不一樣'>"
            f"{icon('out', 14)} 開真實網頁</button>" if target else
            "<button type='button' class='live' disabled data-tip='先給一個網址'>開真實網頁</button>")
    return (f"<form class='ask chk-ask' id='chk-ask' action='/check' method='get'><label class='box'>{icon('search', 16)}"
            f"<input name='url' value='{e(value)}' placeholder='網址或文件編號' aria-label='要核查的網頁'"
            f"{' autofocus' if focus else ''}></label><button>核查</button>{live}</form>")


def _when(text: str | None) -> str:
    return (text or "")[:16].replace("T", " ")


def _facts(s: dict) -> str:
    """The page's own facts in one line: its state, HTTP, when it was fetched, its platform rule, the library."""
    page, doc = s.get("page"), s.get("doc")
    bits = []
    if page:
        b = page["bucket"]
        said = page["result_words"] + (f"（{page['reason']}）" if page.get("reason") else "")
        bits.append(f"<span class='pill {_tone(b)}' data-tip-h='帳本' data-tip='{e(said)}'>{e(BUCKET_NAME[b])}</span>")
        if page.get("status"):
            bits.append(f"<span data-tip='伺服器回的狀態碼'>HTTP {e(page['status'])}</span>")
        if page.get("fetched_at"):
            bits.append(f"<span data-tip='這份存檔是什麼時候抓的（台北時間）'>抓取 {e(_when(page['fetched_at']))}</span>")
        rule = tw(page.get("platform")) or "沒有平台規則"
        how = READING.get((s.get("regions") or {}).get("how", ""), "")
        bits.append(f"<span data-tip-h='平台規則' data-tip='{e(('建庫' + how) if how else '這一頁沒有讀')}'>{e(rule)}</span>")
    if doc:
        bits.append(f"<a class='btn' href='/trace?{urlencode({'ref': doc['doc_id']})}' data-tip-h='進庫了' "
                    f"data-tip='{e(doc.get('title') or doc['doc_id'])}'>{icon('trace', 12)}文件追蹤</a>")
    elif page or s.get("state") == "not_in_ledger":
        bits.append("<span class='tag' data-tip='庫裡沒有這個網址的文件'>沒進庫</span>")
    shown = (page or {}).get("url") or s.get("url") or ""
    if shown:
        press = (s.get("press") or {}).get("label", "")
        whole = unquote(shown) + (f"  #{press}" if press else "")
        bits.append(f"<span class='url mono' data-tip='{e(whole)}'>{e(whole)}</span>")
    return f"<div class='chk-facts'>{''.join(bits)}</div>" if bits else ""


def _tone(bucket: str) -> str:
    return {"got": "good", "look": "good", "next": "warn", "review": "warn", "failed": "bad",
            "missing": "bad"}.get(bucket, "none")


def legend_html(counts: dict) -> str:
    """How many links, images and buttons are in each state, the states in BUCKETS' order, those with none left out."""
    rows = []
    totals = {w: 0 for w, _ in WHATS}
    for key, name, _ in BUCKETS:
        cells = [counts.get(w, {}).get(key, 0) for w, _ in WHATS]
        if not any(cells):
            continue
        for (w, _), n in zip(WHATS, cells, strict=True):
            totals[w] += n
        nums = "".join(f"<td class='num{' z' if not n else ''}'>{n or '—'}</td>" for n in cells)
        rows.append(f"<tr><td><span class='nm' data-tip-h='{e(name)}' data-tip='{e(BUCKET_TIP[key])}'>"
                    f"<i class='sw' data-b='{key}'></i>{e(name)}</span></td>{nums}</tr>")
    if not rows:
        return "<div class='soft small'>這一頁沒有連結、圖片和按鈕</div>"
    head = "".join(f"<th>{e(word)}</th>" for _, word in WHATS)
    total = "".join(f"<td class='num'>{totals[w]}</td>" for w, _ in WHATS)
    return (f"<table class='legend2'><tr><th></th>{head}</tr>{''.join(rows)}"
            f"<tr class='sum'><td>合計</td>{total}</tr></table>")


def verdict_html(s: dict) -> str:
    """One line that answers the owner's question first: what this page missed, where, and who acts on it (the review
    of 11-16: the no-date page's 爬蟲可補 / 要查; the next round's links counted here, never listed). 【Claude】"""
    v = s.get("verdict") or {"fix": {}, "check": 0, "next": 0}
    fix = v.get("fix") or {}
    lost = [(z, fix.get(z, 0)) for z in ("in", "files", "out") if fix.get(z, 0)]
    if lost:
        head = "沒抓到：" + " · ".join(f"{ZONE_SHORT[z]} {n} 個" for z, n in lost)
        tone = "bad" if any(z in ("in", "files") for z, _ in lost) else "warn"
    else:
        head, tone = "沒有漏抓", "good"
    chips = []
    for key, n in (("fix", sum(fix.values())), ("check", v.get("check", 0))):
        if n:
            name, tip = WHO_WORDS[key]
            chips.append(f"<span data-tip-h='{e(name)}' data-tip='{e(tip)}'>{e(name)} {n}</span>")
    if v.get("next"):
        chips.append(f"<span class='plain' data-tip-h='第二輪' data-tip='校外、別的網站、要 JavaScript 的連結，"
                     f"已排到第二輪，不逐條列'>第二輪 {v['next']}</span>")
    return (f"<div class='verdict'><span class='pill {tone}'>{e(head)}</span></div>"
            + (f"<div class='cav' style='margin-top:8px'>{''.join(chips)}</div>" if chips else ""))


def problems_html(problems: list[dict], empty: str) -> str:
    """The links, images and buttons of one kind of problem, the content area's first; a click shows where it is,
    「核查」 checks it."""
    if not problems:
        return f"<div class='soft small'>{e(empty)}</div>"
    rows = []
    for p in problems[:PROBLEMS_SHOWN]:
        name = p["text"] or ("圖片" if p["what"] == "image" else "按鈕" if p["what"] == "button" else unquote(p["target"]))
        side = ZONE_SHORT[p["zone"]] + " · " + (p["reason"] or BUCKET_NAME[p["bucket"]])
        go = ""
        if live_url(p["target"]):
            go = (f"<a class='go' href='/check?{urlencode({'url': p['target']})}' data-live-href='{e(p['target'])}' "
                  "data-tip='核查它指的那一頁'>核查</a>")
        rows.append(f"<li><button type='button' class='prob' data-n='{p['n']}' data-tip-h='{e(BUCKET_NAME[p['bucket']])}' "
                    f"data-tip='{e(p['tip'])}'><i class='sw' data-b='{p['bucket']}'></i>"
                    f"<span class='t'>{e(name)}</span><small>{e(side)}</small></button>{go}</li>")
    more = len(problems) - PROBLEMS_SHOWN
    tail = f"<div class='faint small' style='margin:6px 8px 0'>還有 {more} 條</div>" if more > 0 else ""
    return f"<ul class='probs'>{''.join(rows)}</ul>{tail}"


def caveats_html(s: dict) -> str:
    pills = []
    press = (s.get("press") or {}).get("label", "")
    for key in s.get("caveats", []):
        name, said = CAVEATS[key]
        pills.append(f"<span data-tip-h='{e(name)}' "
                     f"data-tip='{e(said.format(n=s.get('older_links', 0), press=press, spelled=s.get('spelled', '')))}'>"
                     f"{e(name)}</span>")
    return f"<div class='cav'>{''.join(pills)}</div>" if pills else ""


def check_body(s: dict | None, *, value: str = "") -> str:
    """The page under the top bar: the box, then (for a page asked) its facts and the archived page with its column,
    or what stands in its way said plainly."""
    page_url = ((s or {}).get("page") or {}).get("url") or (s or {}).get("url", "")
    body = f"<style>{CHECK_CSS}</style>" + check_form(value, page_url, focus=s is None)
    if s is None:
        return body + f"<script>{CHECK_JS}</script>"
    body += _facts(s)
    if s["state"] != "ok":
        words = STATES[s["state"]]
        page = s.get("page") or {}
        more = {
            "busy": "帳本這一刻讀不了（爬蟲正在寫入，或正在整理檔案），過一下再試；完整的經過在檢視台的紀錄裡。",
            "no_archive": "檢視台啟動時沒有給 --archive，或那個資料夾裡沒有帳本。",
            "not_in_ledger": "爬蟲沒發現過這個網址（或寫法不同）；它要是學校網頁上的連結，就是漏抓。",
            "not_fetched": f"帳本：{page.get('result_words', '')}" + (f"（{page['reason']}）" if page.get("reason") else ""),
            "not_html": f"存的是 {page.get('stored_type', '')}，不是網頁，沒有東西可以上色。",
            "unreadable": "帳本指的那一份存檔讀不出來；完整的經過在檢視台的紀錄裡。",
            "empty": "",
        }[s["state"]]
        tone = "bad" if s["state"] in ("not_in_ledger", "unreadable") else "warn" if s["state"] == "busy" else "none"
        return (body + f"<section class='card pad chk-empty'><span class='pill {tone}'>{e(words)}</span>"
                f"<div class='soft small' style='margin-top:8px'>{e(more)}</div>{caveats_html(s)}</section>"
                f"<script>{CHECK_JS}</script>")
    raw = "/check/raw?" + urlencode({"url": s.get("raw_ref") or s["page"]["url"]})
    fix = [p for p in s["problems"] if p.get("who") == "fix"]
    look = [p for p in s["problems"] if p.get("who") == "check"]
    side = (f"<section class='card pad'><h2>結論</h2>{verdict_html(s)}</section>"
            f"<section class='card pad'><h2>連結、圖片、按鈕</h2>{legend_html(s['counts'])}</section>"
            f"<section class='card pad'><h2>沒抓到的<span class='faint'>{len(fix)}</span></h2>"
            f"{problems_html(fix, '都抓到了，或照規則不抓')}</section>")
    if look:
        side += (f"<section class='card pad'><h2>要查的<span class='faint'>{len(look)}</span></h2>"
                 f"{problems_html(look, '')}</section>")
    if cav := caveats_html(s):
        side += f"<section class='card pad'><h2>要注意</h2>{cav}</section>"
    return (body + "<div class='chk'><section class='card chk-page'>"
            f"<iframe class='chk-frame' sandbox=\"allow-same-origin\" src='{e(raw)}' "
            f"title='存檔裡的這一頁'></iframe></section><aside class='chk-side'>{side}</aside></div>"
            f"<script>{CHECK_JS}</script>")


# what /check/raw serves for a page that has nothing to show (the frame is only drawn for one that has)
EMPTY_RAW = ("<!doctype html><html><head><meta charset='utf-8'><style>body{font:14px system-ui,sans-serif;color:#5c5b57;"
             "margin:24px}</style></head><body>{words}</body></html>")


def empty_raw(state_words: str) -> str:
    return EMPTY_RAW.replace("{words}", e(state_words or "沒有東西可以顯示"))

