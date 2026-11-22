"""網頁核查's way in without remembering anything (design docs/superpowers/specs/2026-11-16-page-check-design.md section
六; nknu.ops.web serves it at /check and /check?site=…): one box that finds sites and pages as one types, the sites
with their deciding numbers, and a site's page with its misses merged across its pages and its pages, the ones that
missed something first. It draws what nknu.ops.sweep returns and computes nothing.

Owner 11-16, on the first version's single address box: 「为什么要我输入网址或文件编号？我怎么会记得网址或者文件编号呀？」.
The layout and every word are 【Claude】's, by the owner's rules for these panels: plain nouns on the page and the detail
in the floating card (data-tip, look.TIP_JS); one word per concept, the page check's own (正文區, 附件區, 選單等, 沒抓到的,
要查的, 爬蟲可補, 第二輪…); states only in look.CSS's good / warn / bad and greys; a number column is just the number
(owner 11-15 of 資料總覽's site list: 「那个网站列表的可视化条形可以拿掉」); what is pressed is ink; a long choice is a list
to type into that carries the numbers that decide it; an empty list says why it is empty.

Redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md) 【Claude】: the deciding numbers are four tiles
(the number big, the plain noun under it, the sentence in the card), never a line of values joined by dots; numbers
of what is missing are violet (--amt), the state colours stay on the small marks that carry a word or a line icon; a
site's key floats in its name's card instead of trailing every row; a merged row opens its drawer and carries no
「核查」 of its own (the drawer has it).

Owner 11-22 (the lab's entry headlined 「正文區有漏 4,465 頁」, and its list's first row was the school's homepage, one
address linked from 7,859 pages, its third and fourth two toolbar buttons of a report viewer): each address is counted
once and each button once per site, what is missing is split into three classes (pagecheck.CLASSES: 爬蟲能補, 要確認,
學校那邊), each a tile and a tab of one list, files before pages before images before buttons; the menus' misses are a
small line, not a tile. 【Claude】 the layout and the words.

Owner 2026-11-22 (「改」: the sweep kept in ops.db, nknu.ops.sweep): the line says when the results shown were checked,
and under the tiles the school's last fourteen nights, a small bar chart per class (trend_html). 【Claude】 the drawing."""

from __future__ import annotations

from urllib.parse import unquote, urlencode

from nknu.ops.check_page import CHECK_CSS, CHECK_JS, item_name
from nknu.ops.look import e
from nknu.ops.pagecheck import BUCKET_NAME, CLASS_NAME, CLASS_TIP, CLASSES, KIND_NAME, live_url
from nknu.ops.sweep import CHIP_TIPS, CHIPS, SORTS

ZONE_SHORT = {"in": "正文區", "files": "附件區", "out": "選單等"}
MERGED_SHOWN = 300  # merged rows drawn per tab; the rest counted (sweep.TOP keeps as many of the school's) 【Claude】

# the deciding numbers (design section 六 1; owner 11-22: each address once, in three classes, so that a template link
# or a button on thousands of pages never makes the headline): the misses of the content and attachment areas per
# class (pagecheck.CLASSES, an address once school-wide, a button once per site), each with on how many pages they
# are; and 沒抓下來, the page itself (the review of 11-16: a site whose pages failed looked clean on the entry)
COUNT_TIP = "正文區、附件區（會進庫的地方）裡的；同一個網址在很多頁、很多網站上只算一次，按鈕每個網站算一次"
FIGURES = (*((key, name, "個網址" if key == "fix" else "個", f"{tip}。{COUNT_TIP}") for key, name, tip in CLASSES),
           ("nofetch", "沒抓下來", "頁", "這一頁本身沒抓下來：失敗、存檔讀不出來（學校已刪除的不算）"))
MENU_TIP = "選單、頁首、頁尾（不進庫的地方）裡帳本沒有或抓失敗的連結、圖片、按鈕；同一個網址只算一次，不算進上面的數字"
# the old site table's columns (/check/list, which no page draws since the structure design of 2026-11-17): pages
SITE_COLUMNS = (("gap", "正文區有漏", "頁", "正文區或附件區（會進庫的地方）有沒抓到的連結、圖片、按鈕的網頁"),
                ("menu_links", "選單沒抓到", "個連結", "選單、頁首、頁尾裡沒抓到的連結；同一個連結在很多頁上只算一次"),
                ("check", "要查", "頁", "有要查的（內容可疑、爬蟲還沒決定怎麼處理），或這一頁本身內容可疑、核查出錯的網頁"),
                ("nofetch", "沒抓下來", "頁", "這一頁本身沒抓下來：失敗、存檔讀不出來（學校已刪除的不算）"))

SITES_CSS = """
.sv{display:flex;flex-direction:column;gap:12px}
.sv-line{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;font-size:13px;color:var(--ink2)}
.sv-line .hd{font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink)}
.sv-key{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;font-size:12px;color:var(--ink3)}
.sv-key>span{display:inline-flex;align-items:center;gap:6px}
.ck-figs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.ck-fig{display:flex;flex-direction:column;min-width:0;padding:12px 14px 13px;border-radius:12px;background:var(--sunk);
border:1px solid var(--line);border-top:3px solid var(--line2)}
.ck-fig[data-k=fix],.ck-fig[data-k=nofetch]{border-top-color:var(--bad-s)}
.ck-fig[data-k=check]{border-top-color:var(--warn-s)}
.ck-fig[data-k=school]{border-top-color:var(--line2)}
.ck-v{display:flex;align-items:baseline;gap:5px;min-width:0}
.ck-v b{font-size:26px;line-height:1.15;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--amt)}
.ck-v small{font-size:12px;color:var(--ink3)}
.ck-w{margin-top:3px;font-size:12px;font-weight:600;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ck-s{margin-top:1px;font-size:12px;color:var(--ink3);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.ck-fig.z{border-top-color:var(--line)}
.ck-fig.z .ck-v b{color:var(--ink3);font-weight:500}
.ck-notes{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 18px;font-size:12px;color:var(--ink3)}
.ck-notes b{font-weight:650;font-variant-numeric:tabular-nums;color:var(--ink2)}
.ck-notes a{color:inherit}.ck-notes a:hover{color:var(--ink)}
.ck-tabs{margin-top:16px;min-width:0}
.ck-tabbar{display:flex;flex-wrap:wrap;gap:2px 20px;margin:0 0 8px;border-bottom:1px solid var(--line)}
.ck-tabbar [role=tab]{display:inline-flex;align-items:baseline;gap:6px;margin:0 0 -1px;padding:2px 2px 9px;border:0;
border-bottom:2px solid transparent;background:transparent;color:var(--ink2);font:inherit;font-size:14px;font-weight:650;
cursor:pointer}
.ck-tabbar [role=tab]:hover{color:var(--ink)}
.ck-tabbar [role=tab][aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
.ck-tabbar [role=tab]:focus-visible{outline:2px solid var(--act);outline-offset:2px;border-radius:4px}
.ck-tabbar [role=tab] b{font-size:13px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--amt)}
.ck-tabbar [role=tab] b.z{color:var(--ink3);font-weight:500}
.ck-pane[hidden]{display:none}
.ck-pane ul.probs,.ck-menu ul.probs{max-height:560px;overflow:auto;scrollbar-width:thin}
details.ck-menu summary{cursor:pointer;width:max-content;max-width:100%}
details.ck-menu summary:hover{color:var(--ink)}
details.ck-menu[open]{flex-basis:100%}details.ck-menu[open] .probs{margin-top:6px}
@media (max-width:640px){.ck-figs{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ck-fig{padding:9px 12px 10px}}
.prog{height:4px;border-radius:999px;background:var(--mark2);overflow:hidden}
.prog i{display:block;height:100%;background:var(--amt);border-radius:999px;transition:width .4s}
.prog.ind i{width:28%;animation:prog-ind 1.4s ease-in-out infinite}
@keyframes prog-ind{from{transform:translateX(-100%)}to{transform:translateX(360%)}}
@media (prefers-reduced-motion:reduce){.prog i{transition:none}.prog.ind i{animation:none;width:100%;opacity:.4}}
.sl,.plist{margin-top:16px;overflow:hidden}
.sl-head,.srow{display:grid;grid-template-columns:minmax(0,1fr) 64px 82px 82px 52px 70px;gap:0 12px;align-items:center}
.sl-head{padding:10px 18px 8px;border-bottom:1px solid var(--line);background:var(--sunk);font-size:12px;color:var(--ink3);font-weight:650}
.sl-head .r{text-align:right}.sl-head a{color:inherit;text-decoration:none}.sl-head a:hover{color:var(--ink)}
.sl-head a[aria-pressed=true]{color:var(--ink)}.sl-head a[aria-pressed=true]::after{content:' ▾'}
.sl ol,.plist ol{list-style:none;margin:0;padding:0}
#chk-rows ol{max-height:480px;overflow:auto;scrollbar-width:thin}
.sl li+li,.plist li+li{border-top:1px solid var(--line)}
a.srow{padding:9px 18px;text-decoration:none;color:inherit;min-width:0}a.srow:hover{background:var(--hover)}
.srow .nm{display:flex;align-items:baseline;gap:8px;min-width:0}
.srow .nm b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.srow .num{text-align:right;font-variant-numeric:tabular-nums}
.srow .num.amt{color:var(--amt);font-weight:650}
.srow .num.z{color:var(--ink3)}.srow .wip,.srow .wip.amt{color:var(--ink3);font-weight:500}
.srow .nothing{grid-column:2/-1;text-align:right;color:var(--ink3);font-size:13px}
.srow .waiting{grid-column:3/-1;text-align:right;color:var(--ink3);font-size:13px}
.srow.zero .nm b{color:var(--ink3);font-weight:500}
.plist h2{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--ink);font-weight:700;margin:0}
.plist h2 .faint{font-weight:600;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--sunk);color:var(--ink2)}
.pl-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;padding:13px 18px;border-bottom:1px solid var(--line);background:var(--sunk)}
a.prow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:9px 18px;
text-decoration:none;color:inherit}a.prow:hover{background:var(--hover)}
.prow .pn{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;font-size:13px}
.prow .pp{grid-column:1/-1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;
color:var(--ink3)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
a.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;border:1px solid var(--line2);
text-decoration:none;color:var(--ink2);font-size:12px;background:var(--surface)}a.chip:hover{background:var(--hover)}
a.chip b{font-variant-numeric:tabular-nums;font-weight:650;color:var(--ink)}a.chip.z{color:var(--ink3)}
a.chip.z b{color:var(--ink3);font-weight:500}
a.chip[aria-pressed=true]{background:var(--act);border-color:var(--act);color:var(--on-act)}
a.chip[aria-pressed=true] b{color:var(--on-act)}
form.filter{margin-left:auto;flex:0 1 260px;min-width:160px;display:flex}
form.filter input{width:100%;height:32px;padding:0 11px;border:1px solid var(--line2);border-radius:9px;
background:var(--surface);font-size:13px}form.filter input:focus{outline:2px solid var(--act);outline-offset:1px}
.more-n{padding:10px 18px;border-top:1px solid var(--line);background:var(--sunk);font-size:12px;color:var(--ink3)}
.why{padding:24px 16px;color:var(--ink2);font-size:13px;text-align:center}
a.prob{text-decoration:none;color:inherit}
a.prob.two{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:2px 10px;align-items:center;padding:8px 10px;border-radius:9px}
a.prob.two small{margin-left:0;min-width:0;font-variant-numeric:tabular-nums;font-size:12px;color:var(--ink3);white-space:nowrap}
a.prob.two .sub{grid-column:2/-1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;
color:var(--ink3)}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin:2px 2px 12px}
.drawer{position:fixed;inset:0;z-index:50;display:flex;justify-content:flex-end}.drawer[hidden]{display:none}
.dr-back{position:absolute;inset:0;background:rgba(0,0,0,.38)}
.dr-sheet{position:relative;width:min(860px,100vw);height:100%;overflow:auto;background:var(--bg);
border-left:1px solid var(--line);box-shadow:var(--pop);padding:20px 22px 24px;display:flex;flex-direction:column}
.dr-top{display:flex;align-items:flex-start;gap:12px}
.dr-top h2{flex:1;margin:0;font-size:16px;font-weight:700;line-height:1.4;overflow-wrap:anywhere}
.dr-x{border:0;background:transparent;color:var(--ink2);font-size:20px;line-height:1;padding:2px 8px;border-radius:8px;
cursor:pointer}.dr-x:hover{background:var(--hover);color:var(--ink)}
.dr-tags{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin-top:10px;font-size:13px;color:var(--ink2)}
.dr-facts{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-direction:column;gap:3px;font-size:13px;
color:var(--ink2)}
.dr-url{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-top:12px}
.dr-url code{font:12px var(--mono);color:var(--ink);overflow-wrap:anywhere;user-select:all}
.dr-go{flex:none;font-size:13px;color:var(--ink2);text-decoration:none;padding:4px 10px;border:1px solid var(--line2);
border-radius:8px;background:var(--surface);white-space:nowrap}.dr-go:hover{background:var(--hover);color:var(--ink)}
.dr-sheet h3{display:flex;align-items:baseline;gap:8px;margin:16px 0 6px;font-size:13px;color:var(--ink2)}
.dr-sheet h3 .faint{font-weight:400}
.dr-pages{list-style:none;margin:0;padding:0;max-height:172px;overflow:auto;border:1px solid var(--line);
border-radius:12px;background:var(--surface)}.dr-pages li+li{border-top:1px solid var(--line)}
.dr-pg{display:flex;align-items:baseline;gap:10px;width:100%;padding:7px 12px;border:0;background:transparent;
text-align:left;font-size:13px;color:var(--ink);cursor:pointer}.dr-pg:hover{background:var(--hover)}
.dr-pg .t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dr-pg small{flex:0 1 auto;min-width:0;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
max-width:55%;color:var(--ink3);font-size:12px}
.dr-pg[aria-pressed=true]{box-shadow:inset 3px 0 0 var(--act);font-weight:600;background:var(--sunk)}
.dr-prev{display:flex;flex-direction:column;margin-top:14px;border:1px solid var(--line);border-radius:12px;
overflow:hidden;background:var(--surface)}
.dr-bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;padding:8px 12px;border-bottom:1px solid var(--line);
background:var(--sunk);font-size:13px}.dr-bar .hd{margin-right:auto;color:var(--ink2);font-weight:600}
.chk-frame.dr-frame{height:max(420px,calc(100vh - 360px));min-height:0}
.dr-wait{padding:30px 0;color:var(--ink3);text-align:center;font-size:13px}
@media (max-width:560px){.dr-sheet{padding:14px 12px 18px}.dr-pg small{display:none}}
.ck-trend{margin-top:4px;padding-top:12px;border-top:1px solid var(--line)}
.ck-tr-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;font-size:12px;color:var(--ink3)}
.ck-tr-head b{font-size:13px;font-weight:650;color:var(--ink2)}
.ck-trs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 22px;margin-top:8px}
.ck-tr{min-width:0}
.ck-tr-h{display:flex;align-items:baseline;gap:6px;min-width:0;font-size:12px;color:var(--ink2);white-space:nowrap}
.ck-tr-h>span{display:inline-flex;align-items:center;gap:6px;min-width:0;overflow:hidden;text-overflow:ellipsis}
.ck-tr-h b{font-size:13px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--amt)}
.ck-mk{flex:none;width:8px;height:8px;border-radius:2px;background:var(--line2)}
.ck-mk[data-k=fix]{background:var(--bad-s)}.ck-mk[data-k=check]{background:var(--warn-s)}
.ck-bars{display:flex;align-items:flex-end;gap:2px;height:34px;margin-top:6px;border-bottom:1px solid var(--line)}
.ck-bars i{flex:1 1 0;min-width:2px;min-height:2px;border-radius:2px 2px 0 0;background:var(--amt);opacity:.55}
.ck-bars i.last{opacity:1}.ck-bars i.none{height:0;min-height:0;background:transparent}
.ck-bars i:hover{opacity:1}
.ck-tr-ax{display:flex;justify-content:space-between;margin-top:3px;font-size:11px;color:var(--ink3);
font-variant-numeric:tabular-nums}
details.ck-tr-t{margin-top:8px;font-size:12px;color:var(--ink3)}details.ck-tr-t summary{cursor:pointer;width:max-content}
details.ck-tr-t summary:hover{color:var(--ink)}
.ck-tr-wrap{overflow-x:auto;scrollbar-width:thin}
.ck-tr-tab{border-collapse:collapse;width:100%;margin-top:6px;font-size:12px}
.ck-tr-tab th,.ck-tr-tab td{padding:4px 0 4px 10px;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}
.ck-tr-tab th:first-child,.ck-tr-tab td:first-child{padding-left:0;text-align:left}
.ck-tr-tab th{font-weight:600;color:var(--ink2)}.ck-tr-tab td{color:var(--ink2)}
@media (max-width:560px){.ck-trs{gap:10px 14px}.ck-tr-h{flex-direction:column;align-items:flex-start;gap:1px}}
.sh h1{font-size:20px;line-height:1.3;overflow-wrap:anywhere}.sh code{font:12px var(--mono);color:var(--ink3)}
.sh .btn{margin-left:auto;align-self:center}
@media (max-width:560px){
.sl-head,.srow{grid-template-columns:minmax(0,1fr) 40px 40px 40px 34px 40px;gap:0 5px}
.sl-head{font-size:11px;padding:8px 12px 6px}.sl-head span{white-space:normal;line-height:1.25}
a.srow{padding:8px 12px}.srow .nm{display:block}.srow .nm b{display:block}
a.prow,.pl-head{padding-left:12px;padding-right:12px}form.filter{margin-left:0;flex:1 1 100%}
.ck-tabbar{gap:2px 14px}.ck-tabbar [role=tab]{font-size:13px}}
"""

# The entry's and the site page's script. The pages work without it (the box and the site filter are forms, the chips
# links); with it a list follows what is typed (one request a moment after the last key, the answer to the latest
# kept) and fills itself in while the sweep runs. A link to a page steers the live window through CHECK_JS's own
# delegated click (design section 三).
SITES_JS = """
(()=>{
const $=s=>document.querySelector(s);
const direct=v=>/^https?:\\/\\//i.test(v)||/^[a-z0-9-]+(\\.[a-z0-9-]+){2,}(:\\d+)?(\\/|$)/i.test(v);
// a list the pointer or the keyboard is in is not redrawn under it by a refresh of the sweep (it waits for the next)
const busy=el=>!!el&&(el.matches(':hover')||el.contains(document.activeElement));
const keep=root=>{const tops=[...root.querySelectorAll('ul.probs')].map(u=>u.scrollTop);
  return ()=>root.querySelectorAll('ul.probs').forEach((u,i)=>{if(tops[i])u.scrollTop=tops[i]})};
let seq=0,timer=null;
const get=(url,apply)=>{const my=++seq;fetch(url,{cache:'no-store'}).then(r=>r.ok?r.json():null)
  .then(j=>{if(j&&my===seq)apply(j)}).catch(()=>{})};
const list=$('#chk-list');
if(list){const box=$('#chk-ask input');let typed=false;
  const poll=()=>{clearTimeout(list._p);if(list.querySelector('[data-running]'))list._p=setTimeout(()=>load(false),4000)};
  const sort=list.dataset.sort||'';
  const load=asked=>{const q=box.value.trim();if(q&&direct(q))return;
    if(!asked&&busy(list)){poll();return}
    const p=new URLSearchParams({q});if(sort)p.set('sort',sort);
    get('/check/list?'+p,j=>{list.innerHTML=j.html;
      if(typed){const u=new URLSearchParams();if(q)u.set('url',q);if(sort)u.set('sort',sort);
        const t=String(u);history.replaceState(null,'','/check'+(t?'?'+t:''))}poll()})};
  box.addEventListener('input',()=>{typed=true;clearTimeout(timer);timer=setTimeout(()=>load(true),180)});poll()}
const site=$('#chk-site');
if(site){const f=$('#chk-filter');let only=site.dataset.only||'';
  const params=()=>{const p=new URLSearchParams({site:site.dataset.site});const q=f.value.trim();if(q)p.set('q',q);
    if(only)p.set('only',only);return p};
  const poll=()=>{clearTimeout(site._p);if(site.hasAttribute('data-running'))site._p=setTimeout(()=>load(false),3000)};
  const load=asked=>{if(!asked&&(busy($('#chk-rows'))||busy($('#chk-top .ck-tabs')))){poll();return}
    const p=params();get('/check/site?'+p,j=>{const back=keep(site);for(const k of ['top','chips','rows']){
      const el=$('#chk-'+k);if(el)el.innerHTML=j[k]}back();site.toggleAttribute('data-running',!!j.running);
      history.replaceState(null,'',(site.dataset.path||'/check')+'?'+p+location.hash);poll()})};
  f.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>load(true),180)});
  f.form.addEventListener('submit',ev=>{ev.preventDefault();load(true)});
  site.addEventListener('click',ev=>{const c=ev.target.closest('a.chip[data-only]');if(!c)return;ev.preventDefault();
    only=c.getAttribute('aria-pressed')==='true'?'':c.dataset.only;load(true)});poll()}
// the merged list's tabs (爬蟲能補 / 要確認 / 學校那邊) switch in place, the arrow keys too; the tab picked, and the menus'
// list opened, stay so when the sweep's refresh draws the list again (an observer puts them back)
const picked={},opened={};
const show=(box,k)=>{const tabs=[...box.querySelectorAll('[role=tab]')];if(!tabs.some(t=>t.dataset.tab===k))return;
  tabs.forEach(t=>{const on=t.dataset.tab===k;t.setAttribute('aria-selected',on?'true':'false');t.tabIndex=on?0:-1});
  box.querySelectorAll('[role=tabpanel]').forEach(p=>{p.hidden=p.dataset.tab!==k})};
document.addEventListener('click',ev=>{const t=ev.target.closest&&ev.target.closest('[data-tabs] [role=tab]');if(!t)return;
  const box=t.closest('[data-tabs]');picked[box.dataset.tabs]=t.dataset.tab;show(box,t.dataset.tab)});
document.addEventListener('keydown',ev=>{const t=ev.target.closest&&ev.target.closest('[data-tabs] [role=tab]');
  if(!t||!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key))return;
  const all=[...t.closest('[data-tabs]').querySelectorAll('[role=tab]')],i=all.indexOf(t);
  const j=ev.key==='Home'?0:ev.key==='End'?all.length-1:(i+(ev.key==='ArrowRight'?1:all.length-1))%all.length;
  ev.preventDefault();all[j].focus();all[j].click()});
document.addEventListener('toggle',ev=>{const d=ev.target;if(d.matches&&d.matches('details[data-keep]'))
  opened[d.dataset.keep]=d.open},true);
new MutationObserver(()=>{for(const box of document.querySelectorAll('[data-tabs]')){const k=picked[box.dataset.tabs];
    const on=box.querySelector('[role=tab][aria-selected=true]');if(k&&(!on||on.dataset.tab!==k))show(box,k)}
  for(const d of document.querySelectorAll('details[data-keep]')){const o=opened[d.dataset.keep];
    if(o!==undefined&&d.open!==o)d.open=o}}).observe(document.body,{childList:true,subtree:true});
// a merged row opens in place (the drawer); a click with a key held, or without the script, opens its page
const dr=$('#chk-drawer'),body=$('#chk-dr');
if(dr&&body){let back=null,asked=0;
  const close=()=>{dr.hidden=true;body.innerHTML='';document.body.style.overflow='';if(back)back.focus();back=null};
  const pick=b=>{const fr=body.querySelector('iframe.dr-frame');if(!fr)return;
    body.querySelectorAll('.dr-pg').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
    const url=b.dataset.page,n=b.dataset.n;fr.setAttribute('data-flash',n);
    fr.src='/check/raw?'+new URLSearchParams({url});
    const c=body.querySelector('.dr-check');if(c)c.href='/check?'+new URLSearchParams({url,n});
    const s=body.querySelector('.dr-school');if(s)s.href=url};
  const open=a=>{back=a;dr.hidden=false;document.body.style.overflow='hidden';
    document.querySelectorAll('.tip').forEach(t=>{t.hidden=true});  // the row's card would stay over the drawer
    body.innerHTML="<div class='dr-wait'>讀取中…</div>";
    const my=++asked;  // its own count: a refresh of the lists never drops the drawer's answer
    fetch('/check/item?'+new URLSearchParams({site:a.dataset.site,key:a.dataset.key}),{cache:'no-store'})
      .then(r=>r.ok?r.json():null).catch(()=>null).then(j=>{if(my!==asked||dr.hidden)return;
        body.innerHTML=j?j.html:"<header class='dr-top'><h2>這一條現在不在了</h2><button type='button' class='dr-x' "+
          "data-close aria-label='關閉'>×</button></header><div class='dr-wait'>帳本改過、重新核查以後沒有網頁有它；關掉再看清單</div>";
        const fr=body.querySelector('iframe.dr-frame');if(fr&&window.nkHook)window.nkHook(fr);
        const x=body.querySelector('.dr-x');if(x)x.focus()})};
  document.addEventListener('click',ev=>{const a=ev.target.closest&&ev.target.closest('a.prob[data-key]');
    if(a&&!(ev.metaKey||ev.ctrlKey||ev.shiftKey||ev.altKey||ev.button)){ev.preventDefault();open(a);return}
    if(dr.hidden)return;if(ev.target.closest('[data-close]')){close();return}
    const b=ev.target.closest('.dr-pg');if(b)pick(b)});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!dr.hidden)close()})}
})();
"""


def _n(value: int) -> str:
    return f"{value:,}"


def _check_href(url: str, n: int | None = None) -> str:
    params = {"url": url}
    if n:
        params["n"] = str(n)
    return "/check?" + urlencode(params)


HELD_TIP = "核查要把存檔裡的網頁讀一遍（約 17 分鐘），和每晚更新用同一顆硬碟；每晚更新抓完、導完以後，再打開這一頁就會重新核查"


def summary_line(progress: dict, sums: dict | None, *, unit_tip: str = "", menu_rows: str = "", notes: str = "") -> str:
    """The answer first (design section 六 1): how far the check has come in one short line (with its bar while the
    sweep runs), then the deciding numbers as tiles, each its number big, its plain noun under it and on how many
    pages small, the sentence in the card (redesign 2026-11-17: never a line of values joined by dots), and under them
    the small notes: the menus' misses, which no tile counts (menu_rows: their list, opened from the note on a site's
    page), and what the site's section adds (notes). 【Claude】"""
    if progress.get("phase") == "failed" and not progress.get("ready"):
        return (f"<div class='sv-line'><span class='pill bad' data-tip-h='讀不了帳本' data-tip='{e(progress['failure'])}'>"
                "讀不了帳本</span></div>")
    if progress.get("held") and (not progress.get("ready") or sums is None):
        # nothing checked yet and the nightly update is fetching: the check waits (sweep.Sweep.ensure's hold)
        return (f"<div class='sv-line'><span class='hd' data-tip='{e(HELD_TIP)}'>每晚更新正在抓網頁，核查等它跑完再開始"
                "</span></div>")
    if not progress.get("ready") or sums is None:
        # the ledger is being read: how long it takes is not known, so the bar moves rather than fills
        running = " data-running='1'" if progress.get("running") else ""
        return (f"<div class='sv-line'{running}><span class='hd'>正在讀帳本</span></div>"
                "<div class='prog ind'><i></i></div>")
    done, total = progress.get("done", 0), progress.get("total", 0)
    running = progress.get("running")
    if "site_total" in progress:
        done, total = progress["site_done"], progress["site_total"]
        running = progress.get("site_running")
    stopped = progress.get("phase") == "failed" and sums.get("checked", 0) < sums.get("pages", 0)
    rereading = progress.get("phase") == "reading"  # the ledger changed: its pages are read again before checking
    said = ("帳本改過，正在重讀" if rereading else
            # kept in ops.db (2026-11-22): a check again reads only the pages whose stored copy changed
            f"帳本改過，正在重新核查 {_n(done)} / {_n(total)} 頁（只重讀改過的網頁）"
            if running and progress.get("again") and progress.get("kept") else
            # a check just begun reads as stuck at 0 (the lab, 11-17): say it has begun and how long the whole takes
            (f"剛重啟，正在逐頁核查（全部約 17 分鐘）：{_n(done)} / {_n(total)} 頁" if not done else
             f"正在逐頁核查 {_n(done)} / {_n(total)} 頁（全部約 17 分鐘）") if running else
            f"核查到 {_n(sums.get('checked', 0))} / {_n(sums.get('pages', 0))} 頁" if stopped else
            f"已核查 {_n(sums.get('checked', 0))} 頁")
    head = (f"<span class='hd' data-tip='{e(unit_tip or '帳本裡抓下來的網頁，一頁一頁照網頁核查的同一套規則核查')}'>"
            f"{said}</span>")
    if not running and not rereading and progress.get("at"):  # when the results shown were checked (kept in ops.db)
        head += (f"<span class='faint' data-tip='這是 {e(progress['at'])} 核查完一遍的結果；帳本改過才重新核查，"
                 f"只重讀改過的網頁'>{e(progress['at'])} 核查完</span>")
    older = ""
    if progress.get("held"):
        older = (f"<span class='pill none' data-tip-h='先看上次的結果' data-tip='{e(HELD_TIP)}'>每晚更新在跑，先看上次的結果"
                 "</span>")
    elif progress.get("stale"):  # the ledger changed after these results: a check again waits for it to be quiet
        again = ("帳本在這次核查以後改過；等帳本靜下來幾分鐘，就重新核查（只重讀改過的網頁）" if progress.get("kept") else
                 "帳本在這次核查以後改過；等帳本靜下來幾分鐘，再打開這一頁就重新核查")
        older = f"<span class='pill none' data-tip-h='稍後重新核查' data-tip='{e(again)}'>稍後重新核查</span>"
    elif progress.get("older"):
        older = ("<span class='pill none' data-tip-h='有舊的結果' data-tip='帳本改過了，正在重新核查；"
                 f"還有 {_n(progress['older'])} 頁是改之前的結果，核查到就換掉'>有舊的結果</span>")
    if progress.get("phase") == "failed":
        older += (f"<span class='pill bad' data-tip-h='核查停了' data-tip='{e(progress.get('failure', ''))}'>"
                  "核查停了</span>")
    bar = ""
    if rereading:
        bar = "<div class='prog ind'><i></i></div>"
    elif running:
        share = round(100 * done / total) if total else 0
        bar = f"<div class='prog'><i style='width:{share}%'></i></div>"
    tiles = ""
    if sums.get("checked", 0):  # nothing checked yet: no tiles (a 0 there would read as 「nothing missed」)
        counts, pages = sums.get("cls") or {}, sums.get("cls_pages") or {}
        cells = []
        for key, name, unit, tip in FIGURES:
            n = sums.get(key, 0) if key == "nofetch" else counts.get(key, 0)
            under = f"出現在 {_n(pages.get(key, 0))} 頁" if key != "nofetch" and n else ""
            cells.append(f"<div class='ck-fig{'' if n else ' z'}' data-k='{e(key)}' data-tip-h='{e(name)}' data-tip='{e(tip)}'>"
                         f"<span class='ck-v'><b>{_n(n)}</b><small>{e(unit)}</small></span>"
                         f"<span class='ck-w'>{e(name)}</span>"
                         + (f"<span class='ck-s'>{e(under)}</span>" if under else "") + "</div>")
        menu = sums.get("menu", 0)
        said_menu = (f"<span data-tip-h='選單裡沒抓到' data-tip='{e(MENU_TIP)}'>選單裡沒抓到 <b>{_n(menu)}</b> 個連結</span>"
                     if menu else "")
        if menu and menu_rows:  # a site's: the note opens their list (the page's script keeps it open on a refresh)
            said_menu = (f"<details class='ck-menu' data-keep='menu'><summary>{said_menu}</summary>{menu_rows}"
                         "</details>")
        tiles = f"<div class='ck-figs'>{''.join(cells)}</div>"
        if said_menu or notes:
            tiles += f"<div class='ck-notes'>{said_menu}{notes}</div>"
    flag = " data-running='1'" if running else ""
    return f"<div class='sv-line'{flag}>{head}{older}</div>{bar}{tiles}"


def _sort_head(name: str, key: str, sort: str | None, tip: str, tip_h: str = "") -> str:
    """A header of the site list: a link that sorts by its number (sort None: the list of a search, not sorted);
    the one pressed again returns to the first sort."""
    hd = f" data-tip-h='{e(tip_h)}'" if tip_h else ""
    if sort is None:
        return f"<span class='r'{hd} data-tip='{e(tip)}'>{e(name)}</span>"
    pressed = sort == key
    go = "/check" if pressed or key == SORTS[0] else "/check?" + urlencode({"sort": key})
    return (f"<span class='r'><a href='{e(go)}' aria-pressed='{'true' if pressed else 'false'}'{hd} "
            f"data-tip='{e(tip + '（點一下照它排）')}'>{e(name)}</a></span>")


def site_rows(sites: list[dict], sort: str | None = None) -> str:
    """The sites, one row each: name, its pages, then its deciding numbers (numbers only, right-aligned); sort: the
    number the list is sorted by, its header pressed (None: a search's sites, headers not links)."""
    if not sites:
        return ""
    rows = []
    for s in sites:
        href = "/site?" + urlencode({"site": s["key"]}) + "#check"  # the site's page (structure design 2026-11-17)
        # the site's key in its name's card, not trailing the row (redesign 2026-11-17); pasted in the box it opens
        # the site (design section 六 6)
        name = f"<span class='nm' data-tip-h='{e(s['name'])}' data-tip='{e(s['key'])}'><b>{e(s['name'])}</b></span>"
        if not s["pages"]:
            rows.append(f"<li><a class='srow zero' href='{e(href)}'>{name}"
                        "<span class='nothing' data-tip='帳本裡這個網站沒有抓下來的網頁'>沒有抓到網頁</span></a></li>")
            continue
        wip = s["current"] < s["pages"] and s["checked"] < s["pages"]
        tip = f"核查中：已核查 {_n(s['checked'])} / {_n(s['pages'])} 頁" if wip else ""
        cells = [f"<span class='num'>{_n(s['pages'])}</span>"]
        if wip and not s["checked"]:  # not one page checked yet: nothing to count (design section 六 4)
            cells.append("<span class='waiting'>核查中</span>")
        for key, *_rest in SITE_COLUMNS if not (wip and not s["checked"]) else ():
            n = s[key]
            cls = ("wip " if wip else "") + ("amt" if n else "z")  # how many are missing: violet
            cells.append(f"<span class='num {cls}'>{_n(n) if n else '—'}</span>")
        attrs = f" data-tip-h='核查中' data-tip='{e(tip)}'" if tip else ""
        rows.append(f"<li><a class='srow' href='{e(href)}'{attrs}>{name}{''.join(cells)}</a></li>")
    head = ("<div class='sl-head'><span>網站</span>"
            + _sort_head("網頁", "pages", sort, "帳本裡抓下來的網頁（也算失敗、學校已刪除的）")
            + "".join(_sort_head(name, key, sort, tip, name) for key, name, _u, tip in SITE_COLUMNS) + "</div>")
    return f"<section class='card sl'>{head}<ol>{''.join(rows)}</ol></section>"


def page_rows(rows: list[dict], *, with_site: bool = False, stopped: bool = False) -> str:
    """Pages, one row each: its name, a pill for what it missed, its address faint (and its site's name). stopped:
    the sweep ended without checking every page, so a page with no result says 沒核查到, not 核查中."""
    out = []
    for r in rows:
        if stopped and r["state"] == "wait":
            r = {**r, "pill": "沒核查到"}
        # the address, faint, unless the page is named by it already
        bits = [r.get("site_name") or r["site"]] if with_site else []
        bits += [r["path"]] if r["path"] != r["name"] else []
        under = " · ".join(bits)
        tip = f" data-tip-h='{e(r['pill'])}' data-tip='{e(r['tip'])}'" if r["tip"] else ""
        out.append(f"<li><a class='prow' href='{e(_check_href(r['url']))}' "
                   f"data-live-href='{e(r['url'])}'{tip}><span class='pn'>{e(r['name'])}</span>"
                   f"<span class='pill {r['tone']}'>{e(r['pill'])}</span>"
                   + (f"<span class='pp'>{e(under)}</span>" if under else "") + "</a></li>")
    return f"<ol>{''.join(out)}</ol>"


BUCKET_KEY = ("missing", "failed", "review", "look")  # the rows' marks, in the legend under the tabs


def miss_tabs(rows: dict[str, list[dict]], counts: dict[str, int], pages: dict[str, int], *, group: str,
              empty: dict[str, str], school: bool = False, site: str = "") -> str:
    """The merged misses as one list with a tab per class (pagecheck.CLASSES, 爬蟲能補 first; owner 11-22), each tab
    with how many it holds, its pane the rows in sweep.list_order (MERGED_SHOWN of them, how many more said). The
    first tab is shown; SITES_JS switches them in place and keeps the one picked when the list is drawn again. group:
    names the list on its page (its ids); empty: per class, what an empty pane says; school, site: merged_rows'."""
    tabs, panes = [], []
    for i, (key, name, tip) in enumerate(CLASSES):
        n = counts.get(key, 0)
        on = i == 0
        tab_id, pane_id = f"ck-t-{group}-{key}", f"ck-p-{group}-{key}"
        hint = f"{tip}（{_n(n)} 個，出現在 {_n(pages.get(key, 0))} 頁）" if n else tip
        faint = "" if n else " class='z'"
        tabs.append(f"<button type='button' role='tab' id='{tab_id}' aria-controls='{pane_id}' data-tab='{key}' "
                    f"aria-selected='{'true' if on else 'false'}' tabindex='{0 if on else -1}' data-tip-h='{e(name)}' "
                    f"data-tip='{e(hint)}'>{e(name)}<b{faint}>{_n(n)}</b></button>")
        body = merged_rows(rows.get(key, []), empty.get(key, ""), school=school, site=site, total=n)
        panes.append(f"<div class='ck-pane' role='tabpanel' id='{pane_id}' aria-labelledby='{tab_id}' data-tab='{key}'"
                     f"{'' if on else ' hidden'}>{body}</div>")
    legend = "".join(f"<span><i class='sw' data-b='{b}'></i>{e(BUCKET_NAME[b])}</span>" for b in BUCKET_KEY)
    return (f"<section class='card pad ck-tabs' data-tabs='{e(group)}'><div class='ck-tabbar' role='tablist' "
            f"aria-label='沒抓到的，分三類'>{''.join(tabs)}</div>{''.join(panes)}"
            f"<div class='sv-key' aria-hidden='true' style='margin-top:10px'>{legend}</div></section>")


def school_lists(top: dict | None, progress: dict) -> str:
    """The entry's list under its tiles: the school's misses in the content and attachment areas merged across sites,
    in three tabs (sweep.school_problems): what the tiles' numbers are made of."""
    if not top:
        return ""
    wip = bool(progress.get("running"))
    empty = {"fix": "爬蟲這邊沒有要補的", "check": "沒有要確認的", "school": "沒有學校那邊的"}
    if wip:
        empty = dict.fromkeys(empty, "還在核查")
    return miss_tabs(top["rows"], top["n"], top["pages"], group="school", empty=empty, school=True)


def entry_list(view: dict, found: dict | None = None) -> str:
    """What the entry shows under its box: the verdict line, the school's misses merged and the sites (all, sorted
    by view['sort']), or the pages and sites found for what is typed."""
    progress = view["progress"]
    body = f"<section class='card pad sv'>{summary_line(progress, view.get('totals'))}</section>"
    sites = view["sites"]
    if found is not None and found.get("q"):
        q = found["q"]
        if found["sites"]:  # the sites first: there are few, and a site is what one usually looks for
            body += site_rows(found["sites"])
        if found["pages"]:
            more = found["pages_total"] - len(found["pages"])
            body += (f"<section class='card plist'><div class='pl-head'><h2>網頁<span class='faint'>"
                     f"{_n(found['pages_total'])}</span></h2></div>{page_rows(found['pages'], with_site=True)}"
                     + (f"<div class='more-n'>還有 {_n(more)} 頁，多打幾個字</div>" if more > 0 else "")
                     + "</section>")
        if not found["pages"] and not found["sites"]:
            why = (f"網站名、網頁名和網址裡都沒有「{q}」" if progress.get("ready")
                   else "帳本還在讀，讀好就找得到")
            body += f"<section class='card sl'><div class='why'>{e(why)}</div></section>"
        return f"<div>{body}</div>"
    body += school_lists(view.get("top"), progress)
    if sites:
        body += site_rows(sites, view.get("sort") or SORTS[0])
    elif progress.get("ready"):
        body += "<section class='card sl'><div class='why'>帳本裡沒有網站</div></section>"
    return f"<div>{body}</div>"


def merged_rows(rows: list[dict], empty: str, *, school: bool = False, site: str = "", total: int | None = None) -> str:
    """Misses merged across pages: one row per address (a button per site), its name and on how many pages it is,
    then what it is, its site (school: the entry's rows, merged across sites, 「等 K 個網站」) and why; MERGED_SHOWN of
    them, and how many more of total (default: len(rows)). A click opens the first page that has it and flashes it
    there (the drawer's 「核查它指的網址」 checks what it points to; the row carries no 「核查」 of its own since the
    redesign of 2026-11-17). With the page's script a click opens the row in place (the drawer: its facts, its pages,
    the archived page with it flashed) and never the school's window (11-16 evening, owner: 「…我还得回来看在俩个网页之间
    来回切换，这没办法在一个网页内解决吗？」); site: the site page's key."""
    if not rows:
        return f"<div class='soft small'>{e(empty)}</div>"
    out = []
    for r in rows[:MERGED_SHOWN]:
        name = item_name(r)
        # two lines (11-16 evening: one line squeezed a long reason over the name): the name and on how many pages,
        # then what it is, where and why
        bits = [KIND_NAME.get(r.get("kind", ""), "")]
        if school:
            bits.append(r["site_name"] + (f" 等 {r['sites']} 個網站" if r["sites"] > 1 else ""))
        if r["zone"] != "in":
            bits.append(ZONE_SHORT[r["zone"]])
        bits.append(r["reason"] or BUCKET_NAME[r["bucket"]])
        where = " · ".join(b for b in bits if b)
        # whose pages the drawer lists: an address's on every site from the entry, else the row's site
        at = site or ("" if r["target"] else r.get("site", ""))
        out.append(f"<li><a class='prob two' href='{e(_check_href(r['page'], r['n']))}' data-site='{e(at)}' "
                   f"data-key='{e(r['key'])}' data-tip-h='{e(BUCKET_NAME[r['bucket']])}' data-tip='{e(r['tip'])}'>"
                   f"<i class='sw' data-b='{e(r['bucket'])}'></i><span class='t'>{e(name)}</span>"
                   f"<small>出現在 {_n(r['pages'])} 頁</small><span class='sub'>{e(where)}</span></a></li>")
    more = (len(rows) if total is None else total) - min(len(rows), MERGED_SHOWN)
    tail = f"<div class='faint small' style='margin:6px 8px 0'>還有 {_n(more)} 條沒列出</div>" if more > 0 else ""
    return f"<ul class='probs'>{''.join(out)}</ul>{tail}"


TONES = {"missing": "bad", "failed": "bad", "review": "warn", "look": "warn"}
DRAWER = ("<aside class='drawer' id='chk-drawer' hidden><div class='dr-back' data-close></div>"
          "<section class='dr-sheet' role='dialog' aria-modal='true' aria-label='這一條'><div id='chk-dr'></div>"
          "</section></aside>")


def item_html(view: dict) -> str:
    """One merged row opened in place (Sweep.item_view): its name, what the ledger says, its class (the list's tab it
    is under) and what it is, its address, the pages that have it, and the first of them archived with it flashed; a
    page picked from the list replaces the preview. The school's pages open only from the two 「↗」 links, in a tab of
    their own."""
    it, pages = view["item"], view["pages"]
    cls = it.get("cls") or "check"
    who_name, who_tip = CLASS_NAME.get(cls, ""), CLASS_TIP.get(cls, "")
    target = live_url(it["target"])
    shown = unquote(it["target"])[:300] if it["target"] else ""
    facts = "".join(f"<li>{e(line)}</li>" for line in it["tip"].split("\n") if line and line != shown)
    tags = (f"<span class='pill {TONES.get(it['bucket'], 'none')}'>{e(BUCKET_NAME[it['bucket']])}</span>"
            f"<span class='plain' data-tip-h='{e(who_name)}' data-tip='{e(who_tip)}'>{e(who_name)}</span>"
            + (f"<span>{e(KIND_NAME[it['kind']])}</span>" if it.get("kind") in KIND_NAME else "")
            + f"<span>{e(ZONE_SHORT[it['zone']])}</span>"
            f"<span>{_n(view['total'])} 頁" + (f" · {view['sites']} 個網站" if view["sites"] > 1 else "") + "</span>")
    url = ""
    if it["target"]:
        go = ""
        if it["target"].startswith(("http://", "https://")):  # what the list's 「核查」 did before 2026-11-17
            go = (f"<a class='dr-go' href='{e(_check_href(it['target']))}' data-live-href='{e(it['target'])}' "
                  "data-tip='在這裡核查它指的那一頁'>核查它指的網址</a>")
        go += (f"<a class='dr-go' href='{e(target)}' target='_blank' rel='noopener noreferrer' "
               "data-tip='在新分頁開學校的這個網址（學校現在的樣子）'>開它指的網址 ↗</a>" if target else "")
        url = f"<div class='dr-url'><code>{e(unquote(it['target']))}</code>{go}</div>"
    rows = []
    for i, p in enumerate(pages):
        where = ([p["site_name"]] if not view["site"] else []) + [ZONE_SHORT[p["zone"]], unquote(p["path"])]
        rows.append(f"<li><button type='button' class='dr-pg' data-page='{e(p['url'])}' data-n='{p['n']}' "
                    f"aria-pressed='{'true' if i == 0 else 'false'}'><span class='t'>{e(p['name'])}</span>"
                    f"<small>{e(' · '.join(where))}</small></button></li>")
    more = view["total"] - len(pages)
    tail = f"<div class='faint small' style='margin:6px 4px 0'>還有 {_n(more)} 頁</div>" if more > 0 else ""
    first = pages[0]
    raw = "/check/raw?" + urlencode({"url": first["url"]})
    preview = ("<div class='dr-prev'><div class='dr-bar'><span class='hd'>存檔裡的這一頁</span>"
               f"<a class='dr-go dr-check' href='{e(_check_href(first['url'], first['n']))}' "
               "data-tip='在這裡打開這一頁的核查（左右對照、它上面所有的連結）'>打開這一頁的核查</a>"
               f"<a class='dr-go dr-school' href='{e(live_url(first['url']))}' target='_blank' rel='noopener noreferrer' "
               "data-tip='在新分頁開學校網站上的這一頁（學校現在的樣子）'>開學校網頁 ↗</a></div>"
               f"<iframe class='chk-frame dr-frame' sandbox=\"allow-same-origin\" src='{e(raw)}' data-flash='{first['n']}' "
               "title='存檔裡的這一頁'></iframe></div>")
    return (f"<header class='dr-top'><h2>{e(item_name(it))}</h2>"
            "<button type='button' class='dr-x' data-close aria-label='關閉'>×</button></header>"
            f"<div class='dr-tags'>{tags}</div>{url}<ul class='dr-facts'>{facts}</ul>"
            f"<h3>出現在<span class='faint'>{_n(view['total'])} 頁</span></h3><ol class='dr-pages'>{''.join(rows)}</ol>"
            f"{tail}{preview}")


def _site_href(key: str, *, q: str = "", only: str = "") -> str:
    params = {"site": key}
    if q:
        params["q"] = q
    if only:
        params["only"] = only
    return "/site?" + urlencode(params) + "#check"  # the site's page (structure design 2026-11-17 section 二)


def site_parts(view: dict) -> dict:
    """The parts of a site's page that change as the sweep goes on and as the list is filtered: top (the verdict line
    with the tiles, and the merged misses in their three tabs, counted as the school's entry counts them), chips,
    rows; running: whether the site is still being checked."""
    site, sums, progress = view["site"], view["sum"], view["progress"]
    notes = ""
    if view["errors"]:
        notes += (f"<a href='{e(_site_href(site['key'], only='check'))}' data-tip-h='核查出錯' "
                  "data-tip='核查這幾頁時程式出錯了；網頁清單裡停在它上面看錯在哪'>"
                  f"核查出錯 <b>{_n(view['errors'])}</b> 頁</a>")
    if view.get("more"):
        notes += ("<span data-tip-h='另有沒列出的' data-tip='一頁上超過 300 條沒抓到或要查的，多的沒有留下來；"
                  f"打開那幾頁看得到全部'>另有 <b>{_n(view['more'])}</b> 條沒列出</span>")
    menu = merged_rows(view["menu"], "", site=site["key"]) if view["menu"] else ""
    line = summary_line(progress, sums, unit_tip="這個網站抓下來的網頁", menu_rows=menu, notes=notes)
    top = f"<section class='card pad sv'>{line}</section>"
    wip = progress.get("site_running") or (progress.get("site_done", 0) < progress.get("site_total", 0)
                                           and progress.get("running"))
    still = f"還在核查（{_n(progress.get('site_done', 0))} / {_n(progress.get('site_total', 0))} 頁）"
    empty = (dict.fromkeys(CLASS_NAME, still) if wip else
             {"fix": "爬蟲這邊沒有要補的：都抓到了，或照規則不抓", "check": "沒有要確認的", "school": "沒有學校那邊的"})
    by = {k: [r for r in view["misses"] if r["cls"] == k] for k in CLASS_NAME}
    top += miss_tabs(by, sums.get("cls", {}), sums.get("cls_pages", {}), group="site", empty=empty, site=site["key"])
    chips = []
    for key, name in CHIPS:
        n = view["chips"].get(key, 0)
        pressed = view["only"] == key
        href = _site_href(site["key"], q=view["q"], only="" if pressed else key)
        chips.append(f"<a class='chip{'' if n else ' z'}' href='{e(href)}' data-only='{key}' "
                     f"aria-pressed='{'true' if pressed else 'false'}' data-tip-h='{e(name)}' "
                     f"data-tip='{e(CHIP_TIPS[key])}'>{e(name)} <b>{_n(n) if n else '—'}</b></a>")
    rows = view["rows"]
    if rows:
        more = view["matched"] - len(rows)
        stopped = progress.get("phase") == "failed"
        listed = page_rows(rows, stopped=stopped) + (f"<div class='more-n'>還有 {_n(more)} 頁，打字篩選</div>"
                                                    if more > 0 else "")
    else:
        if not sums["pages"]:
            why = "帳本裡這個網站沒有抓下來的網頁"
        elif view["q"]:
            why = f"名字和網址裡都沒有「{view['q']}」的網頁"
        else:
            why = f"沒有{dict(CHIPS).get(view['only'], '')}的網頁"
        if progress.get("site_running"):
            why += "（還在核查）"
        listed = f"<div class='why'>{e(why)}</div>"
    return {"top": top, "chips": "".join(chips), "rows": listed, "running": bool(progress.get("site_running"))}


def home_button(view: dict | None) -> str:
    """「從首頁開始」: the site's home page checked, to go on from its links ('' when the ledger knows no home)."""
    site = (view or {}).get("site") or {}
    if not site.get("home"):
        return ""
    return (f"<a class='btn' href='{e(_check_href(site['home']))}' data-live-href='{e(site['home'])}' "
            "data-tip='核查這個網站的首頁，再點上面的連結一頁一頁看'>從首頁開始</a>")


def site_section(view: dict | None, key: str, *, path: str = "/site") -> str:
    """網頁核查 of one site as a section of its page (design section 六 2; nknu.ops.site_page): the verdict line, its
    misses merged, its pages filtered by a chip and by what is typed, and the drawer a merged row opens in. path: the
    page it is on (its filter's form and the address its script keeps go there). Until the structure design of
    2026-11-17 (section 二) this was the page /check?site=…, under a crumb back to every site and the site's name."""
    style = f"<style>{CHECK_CSS}{SITES_CSS}</style>"
    if view is None:
        return style + ("<section class='card pad'><span class='pill none'>帳本裡沒有這個網站</span>"
                        f"<div class='soft small' style='margin-top:8px'>{e(key)}</div></section>")
    if not view.get("ready"):  # the ledger is being read: look again in a moment (not when it could not be read)
        again = ("<script>setTimeout(()=>location.reload(),3000)</script>" if view["progress"].get("running")
                 else "")
        return (style + f"<section class='card pad sv'>{summary_line(view['progress'], None)}</section>"
                f"<script>{CHECK_JS}</script>{again}")
    site = view["site"]
    parts = site_parts(view)
    running = " data-running" if parts["running"] else ""
    only = f" data-only='{e(view['only'])}'" if view["only"] else ""
    return (style + f"<div id='chk-site' data-site='{e(site['key'])}' data-path='{e(path)}'{only}{running}>"
            f"<div id='chk-top'>{parts['top']}</div>"
            "<section class='card plist'><div class='pl-head'>"
            f"<h2>網頁<span class='faint'>{_n(view['sum']['pages'])}</span></h2>"
            f"<nav class='chips' id='chk-chips'>{parts['chips']}</nav>"
            f"<form class='filter' action='{e(path)}' method='get'>"
            f"<input type='hidden' name='site' value='{e(site['key'])}'>"
            + (f"<input type='hidden' name='only' value='{e(view['only'])}'>" if view["only"] else "")
            + f"<input id='chk-filter' name='q' value='{e(view['q'])}' placeholder='打幾個字篩選網頁' "
            "aria-label='篩選網頁' autocomplete='off'></form></div>"
            f"<div id='chk-rows'>{parts['rows']}</div></section></div>{DRAWER}"
            f"<script>{CHECK_JS}</script><script>{SITES_JS}</script>")


def school_section(view: dict | None, *, archive: bool = True) -> str:
    """The school's 網頁核查 for 文件's page (/doc; nknu.ops.doc_page, which adds the style, the drawer its rows open in
    and the scripts once): the verdict line with the four deciding numbers, then the school's misses merged across
    sites in three tabs (爬蟲能補, 要確認, 學校那邊; owner 11-22). The sites' table it sat over until the structure
    design of 2026-11-17 (section 二) went to each site's page (/site); data-running asks the page to fetch it again
    while the sweep runs, and only then: a sweep that failed is not ready either, and the page asked every 4 s for good
    (review 2026-11-17)."""
    if not archive or view is None:
        return ("<section class='card pad'><span class='pill warn'>沒有掛上爬蟲存檔</span>"
                "<div class='soft small' style='margin-top:8px'>檢視台啟動時沒有給 --archive，或那個資料夾裡沒有帳本。"
                "</div></section>")
    progress = view["progress"]
    running = " data-running='1'" if progress.get("running") else ""
    return (f"<div id='chk-school'{running}><section class='card pad sv'>"
            f"{summary_line(progress, view.get('totals'))}{trend_html(view.get('history'))}</section>"
            f"{school_lists(view.get('top'), progress)}</div>")


TREND_TIP = ("每晚核查完一遍以後的全校總數（記在檢視台自己的紀錄檔）：正文區、附件區裡沒抓到的，同一個網址只算一次，"
             "按鈕每個網站算一次；一晚核查了幾次，留最後一次")
TREND_ROWS = (*((f"cls_{key}", name) for key, name, _t in CLASSES), ("nofetch", "沒抓下來"), ("menu", "選單裡沒抓到"))


def trend_html(history: dict | None) -> str:
    """The school's last nights under the tiles (Sweep.history, kept in ops.db; owner 2026-11-22): per class
    (pagecheck.CLASSES) the addresses missed, a bar a night, the last night's number beside the class's name; a night
    with no record is left empty, so the nights stay in their places. Amounts are violet, the class's state colour is
    only its small mark (the tiles' rule); each bar says its night in its card, and the numbers are a table folded
    under the bars. '' when the nights are not kept (no file)."""
    if not history or not history.get("enabled"):
        return ""
    days, nights = list(history.get("days") or []), list(history.get("nights") or [])
    kept = [(d, r) for d, r in zip(days, nights, strict=False) if r]
    if not kept:
        return ("<div class='ck-trend'><div class='ck-tr-head'><b>每晚的變化</b>"
                "<span>還沒有紀錄：每晚核查完一遍，就記下全校的總數</span></div></div>")
    last_day, last = kept[-1]
    panels = []
    for key, name, _tip in CLASSES:
        col = f"cls_{key}"
        top = max((r.get(col) or 0 for _d, r in kept), default=0)
        bars = []
        for day, rec in zip(days, nights, strict=False):
            label = f"{day[5:]} 夜"
            if not rec:
                bars.append(f"<i class='none' data-tip-h='{e(label)}' data-tip='這一晚沒有紀錄'></i>")
                continue
            v = rec.get(col) or 0
            done = str(rec.get("at") or "")[5:16].replace("T", " ")
            tip = f"{name} {_n(v)} 個，出現在 {_n(rec.get(f'pages_{key}') or 0)} 頁" + (f"\n{done} 核查完" if done else "")
            mark = " class='last'" if day == last_day else ""
            bars.append(f"<i{mark} style='height:{round(100 * v / top) if top else 0}%' data-tip-h='{e(label)}' "
                        f"data-tip='{e(tip)}'></i>")
        said = "、".join(f"{d[5:]} {_n(r.get(col) or 0)}" for d, r in kept)
        panels.append(f"<div class='ck-tr'><div class='ck-tr-h'><span><i class='ck-mk' data-k='{e(key)}'></i>{e(name)}"
                      f"</span><b>{_n(last.get(col) or 0)}</b></div>"
                      f"<div class='ck-bars' role='img' aria-label='{e(f'{name}，每晚的網址個數：{said}')}'>"
                      f"{''.join(bars)}</div><div class='ck-tr-ax'><span>{e(days[0][5:])}</span>"
                      f"<span>{e(days[-1][5:])}</span></div></div>")
    head = "".join(f"<th>{e(name)}</th>" for _c, name in TREND_ROWS)
    rows = "".join(f"<tr><td>{e(d[5:])} 夜</td>" + "".join(f"<td>{_n(r.get(c) or 0)}</td>" for c, _n2 in TREND_ROWS)
                   + "</tr>" for d, r in reversed(kept))
    return (f"<div class='ck-trend'><div class='ck-tr-head' data-tip='{e(TREND_TIP)}'><b>最近 {len(days)} 晚</b>"
            "<span>每晚核查完的網址個數</span></div>"
            f"<div class='ck-trs'>{''.join(panels)}</div><details class='ck-tr-t'><summary>看每晚的數字</summary>"
            f"<div class='ck-tr-wrap'><table class='ck-tr-tab'><thead><tr><th>晚上</th>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details></div>")
