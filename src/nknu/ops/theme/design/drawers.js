// 原始紀錄 and 檔案: two drawers on the right (owner 11-23: 「用右边出现一个侧边栏显示吧」), ported from the design copy's
// rawview.js and fileview.js. The rows and the files are read from the server a page at a time (GET /api/raw, GET
// /api/file: nknu.ops.raw, nknu.ops.lineage) instead of a sample baked into the page. Entry points as the design's:
// window.RAWSHOW(step, preset), window.RAWVIEW(box, step, night), window.FILEOPEN(key, step[, site]),
// window.FILEVIEW(box, step, night). A preset names {label (a key or a word), site (a site key), bucket, miss}.
(()=>{
if(window.RAWSHOW&&window.FILEOPEN)return;
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const enc=encodeURIComponent;
const NAMES={crawl:'爬取',export:'導出',totext:'轉文字',build:'建庫',swap:'換庫'},ORDER=['crawl','export','totext','build','swap'];
const OUT={crawl:'got',export:'file',totext:'ok',build:'doc:*',swap:'new'};
const VIA={link:'頁面連結',old_frontier:'舊帳本',commoncrawl:'Common Crawl 索引',drive:'Google 雲端硬碟',redirect:'跳轉',rejudge:'重新判定',v1_known:'第一版已知',scheme_switch:'http/https 互換',seed:'網站清單起點',click:'按按鈕',reconcile:'核對補的',js_redirect:'網頁腳本跳轉'};
const ST={done:'抓到了',failed:'失敗',skipped:'照規則不抓',later:'留到第二輪',held:'壓著',pending:'還在排'};
const how=r=>{if(r.how)return r.how;const p=new Set((r.req||[]).map(q=>q.purpose));if(r.kind==='drive'||String(r.reason||'').startsWith('api:'))return 'Google API';
  if(p.has('jsfetch'))return '瀏覽器開';if(p.has('postback'))return '按按鈕';if(p.has('page'))return '直接請求';return (r.req||[]).length?'檢查用':'沒發請求'};
const dec=u=>{try{return decodeURI(u)}catch(e){return u}};
const size=b=>b==null?'':b>=1e9?(b/1e9).toFixed(1)+' GB':b>=1e6?(b/1e6).toFixed(1)+' MB':b>=1e3?(b/1e3).toFixed(0)+' KB':b+' B';
const num=n=>(n||0).toLocaleString();
const svg=p=>`<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>${p}</svg>`;
const IC={網頁:"<rect x='3' y='2' width='10' height='12' rx='1.5'/><path d='M5.5 6h5M5.5 8.5h5M5.5 11h3'/>",
  圖片:"<rect x='2' y='3' width='12' height='10' rx='1.5'/><circle cx='6' cy='6.5' r='1.2'/><path d='M2.5 12l3.5-3.5 2.5 2.5 2-2 3 3'/>",
  檔案:"<path d='M4 2h5l3 3v9H4z'/><path d='M9 2v3h3'/>",'Google 雲端硬碟':"<path d='M6 2.5h4l4 7-2 3.5H4l-2-3.5z'/>",
  嵌入內容:"<path d='M6 4.5 2.5 8 6 11.5M10 4.5 13.5 8 10 11.5'/>",短網址:"<path d='M7 9a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-.6.6M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.6-.6'/>",
  直接請求:"<path d='M8 2.5v8M4.5 7.5 8 11l3.5-3.5M3 13.5h10'/>",按按鈕:"<path d='M4 3l8.5 4-3.6 1.1L7.8 12z'/>",
  瀏覽器開:"<rect x='2' y='3' width='12' height='10' rx='1.5'/><path d='M2 6h12'/>",'Google API':"<path d='M5 12a3 3 0 0 1 .4-6 4 4 0 0 1 7.6 1.6A2.2 2.2 0 0 1 12.5 12z'/>",
  檢查用:"<circle cx='7' cy='7' r='4'/><path d='M10 10l3.5 3.5'/>",沒發請求:"<circle cx='8' cy='8' r='5.5'/><path d='M5 8h6'/>",
  校外:"<circle cx='8' cy='8' r='5.5'/><path d='M2.5 8h11M8 2.5c2 2 2 9 0 11M8 2.5c-2 2-2 9 0 11'/>",
  原樣:"<path d='M2.5 4.5h11v9h-11zM2 2.5h12v2H2z'/><path d='M6.5 7.5h3'/>",學校:"<path d='M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5'/>",
  欄位:"<path d='M5.5 3C4 3 4 4 4 5.5S3 8 2.5 8C3 8 4 8.5 4 10.5S4 13 5.5 13M10.5 3C12 3 12 4 12 5.5S13 8 13.5 8c-.5 0-1.5.5-1.5 2.5S12 13 10.5 13'/>"};
IC.公告="<path d='M3 6.5h2l6-3v9l-6-3H3z'/><path d='M5 9.5l1 3.5'/>";
IC.行事曆="<rect x='2.5' y='3.5' width='11' height='10' rx='1.5'/><path d='M2.5 6.5h11M5.5 2v3M10.5 2v3'/>";IC.文件=IC.網頁;
const ico=(k,tip,cls='')=>`<i class='rv-ic ${cls}' data-tip='${esc(tip||k)}'>${svg(IC[k]||'')}</i>`;
const KIND={page:'網頁',image:'圖片',file:'檔案',drive:'Google 雲端硬碟',embed:'嵌入內容',shortlink:'短網址'};
// state colour of the dot: the panel's bucket
const BK={got:'ok',review:'warn',failed:'bad'};
const FL={soft404:['疑似假頁面','和這台主機「不存在的網頁」長得一樣'],login:['登入頁','要登入才看得到'],decode:['編碼有問題','無法嚴格解碼，原樣存檔'],
  thin:['幾乎是空的','正文太少'],no_area:['缺內容區','學校回的頁沒有內容區'],gone_redirect:['跳到不存在','轉址到「找不到」的頁'],
  gone_id:['編號不存在','網址裡的編號學校那邊沒有'],gone_page:['學校的找不到頁','學校回了自己的「找不到」頁'],empty_detail:['詳情是空的','詳情頁沒有內容'],
  js_hint:['可能要 JavaScript','內容看起來靠網頁腳本載入'],js_redirect:['腳本跳轉','用網頁腳本跳到別的網址'],error_page:['錯誤頁','學校回了錯誤頁'],form_closed:['表單已關閉','']};
// the ledger writes fetched_at in UTC; the server sends it in Taipei time too (r.at)
const tpe=s=>{const d=new Date(s);if(!s||isNaN(d))return s||'';const t=new Date(d.getTime()+8*36e5).toISOString();return t.slice(5,10)+' '+t.slice(11,16)};
const http=(s,err)=>s!=null?`<b class='rv-h h${String(s)[0]}'>${s}</b>`:err?`<b class='rv-h h5' data-tip='${esc(err)}'>錯</b>`:'';
// a school link written as UTF-8 bytes read as Latin-1 (「å¹´」): shown as the words it meant; the hover keeps the raw one
const unmangle=s=>{if(!/[Â-ô][\u0080-¿]/.test(s)||/[^\u0000-ÿ]/.test(s))return s;
  try{return new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(s,c=>c.charCodeAt(0)))}catch(e){return s}};
const short=u=>esc(unmangle(dec(u||'')).replace(/^https?:\/\//,''));
const pickedSite=()=>{try{return (window.APP&&window.APP.S&&window.APP.S.site)||''}catch(e){return ''}};
// the night a caller names, always as its run day (the day the 01:00 run started): n.run (the contract), n.run_day,
// n.day, n.date or a plain string are run days; the old key n.night (the day before, nightly.night_of) is turned into
// its run day, so a night is never compared across the two namings (RD-07)
const nextDay=d=>{const t=new Date(String(d).slice(0,10)+'T12:00:00Z');if(isNaN(t))return '';t.setUTCDate(t.getUTCDate()+1);return t.toISOString().slice(0,10)};
const nightOf=n=>{if(!n)return '';if(typeof n!=='object')return String(n);const d=n.run||n.run_day||n.day||n.date;return d?String(d):n.night?nextDay(n.night):''};
const getJSON=(url,signal)=>fetch(url,{signal,headers:{Accept:'application/json'}}).then(r=>r.json().then(j=>{if(!r.ok&&!j.state)throw new Error(j.error||r.status);return j}));

/* ---------------------------------------------------------------- 原始紀錄 */
let R=null,rows=[],stepNow='',title='',f={},open=null,night='',ctl=null,qTimer=0,waitTimer=0,tries=0,loading=false;
let rawOpen=false,moreOpen=false,labelsOpen=false;
const dr=document.createElement('aside');dr.className='rvd';dr.hidden=true;dr.setAttribute('aria-label','原始紀錄');document.body.appendChild(dr);
const shade=document.createElement('div');shade.className='rvd-shade';shade.hidden=true;document.body.appendChild(shade);
function close(){dr.hidden=true;shade.hidden=true;document.body.classList.remove('rvd-open');if(ctl)ctl.abort();clearTimeout(waitTimer)}
shade.onclick=close;addEventListener('keydown',e=>{if(e.key==='Escape'&&!dr.hidden)close()});
function chips(name,vals,lab){return vals.length<2&&!f[name]?'':`<div class='rv-f'><span>${lab}</span><div>`+vals.map(([v,w,c])=>`<button data-f='${name}' data-v='${esc(v)}' aria-pressed='${f[name]===v}'>${esc(w)}${c!=null?` <small>${num(c)}</small>`:''}</button>`).join('')+`</div></div>`}
const labelOn=r=>(R&&R.label_on||[]).includes(r.lk);
// one address (owner 11-23: 「让看起来好读一点…能用悬浮窗或者视觉化图标表示的用视觉化」): a state dot, the address and
// its time; under it the panel's word, then icons (how it was fetched, what it is) and the HTTP code. Opened: where it
// came from, what the page said of itself, its requests as bars, and the raw fields only on demand
function row(r,o){
  const word=r.label||ST[r.state]||r.state;
  const dotTip=`帳本：${ST[r.state]||r.state}${r.reason?' · '+r.reason:''}${r.result?' · '+r.result:''}`;
  const h=how(r),kind=KIND[r.kind]||r.kind,req=r.req||[];
  let s=`<div class='rv-i${o?' open':''}' data-id='${esc(r.id)}'><div class='rv-l1'><i class='rv-dot ${BK[r.bucket]||''}' data-tip='${esc(dotTip)}'></i>`+
    `<span class='rv-u' data-tip='${esc(r.url)}'>${short(r.url)}</span><time>${esc(r.at||tpe(r.fetched_at))}</time></div>`+
    `<div class='rv-m'>${labelOn(r)?'':`<span class='rv-lb'>${esc(word)}</span>`}${!r.label&&r.reason?`<code>${esc(r.reason)}</code>`:''}`+
    `${ico(h,'怎麼抓：'+h)}${ico(kind,'種類：'+kind)}${http(r.status)}${r.bytes!=null?`<span>${size(r.bytes)}</span>`:''}</div>`;
  if(!o)return s+`</div>`;
  const flags=String(r.flags||'').split(',').filter(Boolean);
  const from=r.from_url?`<a href='${esc(r.from_url)}' target='_blank' rel='noopener' data-tip='${esc(dec(r.from_url))}'>${short(r.from_url)}</a>`:r.found_from?`<span data-tip='帳本裡第 ${esc(r.found_from)} 號網址'>#${esc(r.found_from)}</span>`:'';
  const maxMs=Math.max(1,...req.map(q=>q.ms||0));
  s+=`<div class='rv-x'><dl class='rv-dl'>`+
    `<dt>來路</dt><dd>${esc(VIA[r.found_via]||r.found_via||'')}${from?' ← '+from:''}${r.depth!=null?` <small data-tip='從起點點了 ${esc(r.depth)} 次連結'>第 ${esc(r.depth)} 層</small>`:''}</dd>`+
    (r.final_url&&r.final_url!==r.url?`<dt>轉到</dt><dd><a href='${esc(r.final_url)}' target='_blank' rel='noopener'>${short(r.final_url)}</a></dd>`:'')+
    (r.title?`<dt>標題</dt><dd>${esc(r.title)}</dd>`:'')+
    (flags.length?`<dt>看出</dt><dd>${flags.map(x=>`<span class='rv-flag' data-tip='${esc((FL[x]||[])[1]||x)}'>${esc((FL[x]||[x])[0])}</span>`).join('')}</dd>`:'')+`</dl>`+
    (req.length?`<div class='rv-reqs'>`+req.map(q=>{const p={page:'直接請求',postback:'按按鈕',jsfetch:'瀏覽器開'}[q.purpose]||'檢查用';
      return `<div class='rv-q1'><time>${esc((q.at||'').slice(5,16))}</time>${ico(p,`${p}（${q.method||''}）`)}${http(q.status,q.error)}`+
        `<span class='rv-bar' data-tip='${q.ms!=null?q.ms+' ms':'沒有時間'}'><i style='width:${q.ms?Math.max(2,q.ms/maxMs*100):0}%'></i></span>`+
        `<span>${size(q.bytes)}</span>${q.campus==='off'?ico('校外','從校外連的'):''}</div>`}).join('')+`</div>`:`<p class='rv-none'>沒有請求紀錄</p>`)+
    `<div class='rv-go'>${r.warc?`<a href='/check?url=${enc(r.url)}' target='_blank' rel='noopener' data-tip='${esc(r.warc)}'>${svg(IC.原樣)}原樣</a>`:''}`+
    `<a href='${esc(r.url)}' target='_blank' rel='noopener'>${svg(IC.學校)}學校網頁</a>`+
    `<button class='rv-rawbtn' type='button' aria-pressed='${rawOpen}'>${svg(IC.欄位)}全部欄位</button></div>`+
    (rawOpen?`<div class='rv-kv'>${Object.entries(r).filter(([k,v])=>k!=='req'&&v!=null&&v!=='').map(([k,v])=>`<span>${esc(k)}</span><code>${esc(/url$/.test(k)?dec(v):v)}</code>`).join('')}</div>`:'')+`</div>`;
  return s+`</div>`}
// the other four steps (owner 11-23: 「剩下的四个步骤也跟上吧」): every record in one shape, drawn alike
const val=(v,o)=>{o=o||{};const t=o.tip?` data-tip='${esc(o.tip)}'`:'';
  if(o.chips)return o.chips.map(([w,tip])=>`<span class='${o.plain?'rv-chip':'rv-flag'}'${tip?` data-tip='${esc(tip)}'`:''}>${esc(w)}</span>`).join('');
  if(o.href)return `<a href='${esc(o.href)}' target='_blank' rel='noopener'${t||` data-tip='${esc(dec(o.href))}'`}>${short(v)}</a>`;
  if(o.doc)return `<a href='/trace?ref=${enc(o.doc)}' target='_blank' rel='noopener' data-tip='文件 ${esc(o.doc)}'>${esc(v)}</a>`;
  if(o.from)return `${esc(v)} ← <a href='${esc(o.from)}' target='_blank' rel='noopener' data-tip='${esc(o.tip||dec(o.from))}'>${short(o.from)}</a>`;
  if(o.quote)return `<q class='rv-quote'>${esc(v)}</q>`;
  return o.mono?`<code${t}>${esc(v)}</code>`:`<span${t}${o.tip?" class='rv-tipped'":''}>${esc(v)}</span>`};
function rowG(r,o){
  let s=`<div class='rv-i${o?' open':''}' data-id='${esc(r.id)}'><div class='rv-l1'><i class='rv-dot ${r.dot||''}'></i>`+
    `<span class='${r.mono?'rv-u':'rv-t'}'${r.tip?` data-tip='${esc(dec(r.tip))}'`:''}>${r.mono?short(r.main):esc(r.main)}</span><time>${esc(r.time||'')}</time></div>`+
    `<div class='rv-m'>${labelOn(r)?'':`<span class='rv-lb'>${esc(r.label)}</span>`}`+
    (r.meta||[]).map(([ic,t,tip,mono])=>ic?ico(ic,tip):mono?`<code${tip?` data-tip='${esc(tip)}'`:''}>${esc(t)}</code>`:`<span${tip?` data-tip='${esc(tip)}'`:''}>${esc(t)}</span>`).join('')+`</div>`;
  if(!o)return s+`</div>`;
  s+=`<div class='rv-x'><dl class='rv-dl'>`+(r.dl||[]).map(([k,v,op])=>`<dt>${esc(k)}</dt><dd>${val(v,op)}</dd>`).join('')+`</dl>`+
    `<div class='rv-go'>${(r.go||[]).map(([ic,t,h,tip])=>`<a href='${esc(h)}' target='_blank' rel='noopener'${tip?` data-tip='${esc(tip)}'`:''}>${svg(IC[ic]||IC.學校)}${esc(t)}</a>`).join('')}`+
    `<button class='rv-rawbtn' type='button' aria-pressed='${rawOpen}'>${svg(IC.欄位)}全部欄位</button></div>`+
    (rawOpen?`<div class='rv-kv'>${Object.entries(r.raw||{}).filter(([k,v])=>v!=null&&v!==''&&!(Array.isArray(v)&&!v.length)).map(([k,v])=>`<span>${esc(k)}</span><code>${esc(typeof v==='object'?JSON.stringify(v):/url$|网址|网页$/.test(k)?dec(v):v)}</code>`).join('')}</div>`:'')+`</div>`;
  return s+`</div>`}
// the head: which file the rows are read from and its place in the step (owner 11-23: 「为什么没显示定位在哪一个文件呀」),
// what the whole is, which rows of how many are shown
function head(){const src=(R&&R.src)||{},n=rows.length,m=(R&&R.matched)||0;
  const range=R&&R.state==='ready'?(m?`<span class='rv-range' data-tip='一次讀 ${R.per||50} 筆；到底按「更多」再讀'>第 1–${num(n)} 筆 / ${num(m)}</span>`:`<span class='rv-range'>0 筆</span>`):'';
  return `<div class='rvd-h'><div><b><button class='rv-file' type='button' data-tip='打開這個檔'>${esc(src.file||'')}</button> · ${esc(src.table||'')}</b>`+
    `<span>${esc(title)}的<i data-tip='${esc(src.tip||'')}'>${esc(src.side||'')}</i>${R&&R.head?' · '+esc(R.head):''}</span></div>${range}<button class='rvd-x' aria-label='關閉'>×</button></div>`}
function tools(){
  const allL=(R.labels||[]),cut=labelsOpen||allL.length<=11?allL:allL.filter((x,i)=>i<9||x[0]===f.label);
  const labelChips=allL.length?`<div class='rv-f'><span>結果</span><div>`+cut.map(([k,w,c,only])=>
    `<button data-f='label' data-v='${esc(k)}' aria-pressed='${f.label===k}'${only?` class='only' data-tip='只記了數量，沒有逐筆紀錄'`:''}>${esc(w)} <small>${num(c)}</small></button>`).join('')+
    (cut.length<allL.length?`<button class='rv-lmore' type='button'>其餘 ${allL.length-cut.length} 種 ▾</button>`:labelsOpen&&allL.length>11?`<button class='rv-lmore' type='button'>收起 ▴</button>`:'')+`</div></div>`:'';
  // the source's own fields fold away: 結果 is the panel's word for them (owner: 「能少的废话少掉」)
  const facets=(R.facets||[]).filter(([k])=>!(k==='site'&&f.site)),on=facets.filter(([k])=>f[k]).length,show=moreOpen||on;
  return `<div class='rvd-tools'><label class='rv-q'><svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6'><circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/></svg>`+
    `<input type='search' placeholder='${stepNow==='crawl'?'網址、標題':'網址、標題、檔名'}' value='${esc(f.q||'')}'></label>`+labelChips+
    (facets.length?`<button class='rv-more' type='button' aria-expanded='${!!show}'>篩選${on?` <small>${on}</small>`:''} <span>${show?'▴':'▾'}</span></button>`+
      (show?facets.map(([k,lab,vals])=>chips(k,vals,lab)).join(''):''):'')+`</div>`}
function oldNight(){if(!night||!R||R.state!=='ready'||!R.run_day||night===R.run_day)return '';
  return `<p class='rv-old'>${stepNow==='crawl'?`這一晚的逐筆紀錄沒留下，只有現在的帳本（${esc(R.as_of||'')} 的樣子）`:`這一晚的逐筆紀錄沒留下，下面是現在的（${esc(R.run_day||'')} 那一晚的）`}</p>`}
function listHtml(){
  if(!R||R.state!=='ready')return `<p class='rv-none rv-wait'>${esc((R&&R.words)||'讀取中…')}</p>`;
  const lab=(R.labels||[]).find(x=>x[0]===f.label);
  if(R.only||(lab&&lab[3]))return `<p class='rv-none'>${esc(R.note||`${title}只記了這一類的數量（${num(lab?lab[2]:0)} 筆），沒有留下逐筆紀錄。`)}</p>`;
  const draw=stepNow==='crawl'?row:rowG;
  return oldNight()+rows.map(r=>draw(r,open===String(r.id))).join('')+(rows.length?'':`<p class='rv-none'>沒有符合的</p>`)+
    (rows.length<(R.matched||0)?`<button class='rv-next' type='button'${loading?' disabled':''}>更多（再 ${num(Math.min(R.per||50,R.matched-rows.length))} 筆）</button>`:'')+
    (R.note?`<p class='rv-none rv-note'>${esc(R.note)}</p>`:'')}
function draw(){
  const list=dr.querySelector('.rvd-list'),y=list?list.scrollTop:0,qi=dr.querySelector('.rv-q input'),typing=qi&&document.activeElement===qi,caret=typing?qi.selectionStart:0;
  dr.innerHTML=head()+(R&&R.state==='ready'?tools():'')+`<div class='rvd-list'>${listHtml()}</div>`;
  wire();const l2=dr.querySelector('.rvd-list');if(l2)l2.scrollTop=y;
  if(typing){const n2=dr.querySelector('.rv-q input');if(n2){n2.focus();n2.setSelectionRange(caret,caret)}}}
function pills(){const list=dr.querySelector('.rvd-list');if(!list||!R)return;const P=[];
  if(f.site)P.push(['site','網站：'+(R.site_name||f.site)]);if(f.bucket)P.push(['bucket',R.bucket_word||f.bucket]);if(f.miss)P.push(['miss','只看沒通過的']);
  if(!P.length)return;const d=document.createElement('div');d.className='rv-pills';
  d.innerHTML=P.map(([k,w])=>`<button type='button' data-k='${k}' data-tip='拿掉這個篩選'>${esc(w)} <b>✕</b></button>`).join('');
  d.querySelectorAll('button').forEach(b=>b.onclick=()=>{f[b.dataset.k]=null;open=null;load()});list.before(d)}
function wire(){
  pills();
  dr.querySelector('.rvd-x').onclick=close;
  const src=(R&&R.src)||{};
  const fb=dr.querySelector('.rv-file');if(fb)fb.onclick=()=>{if(window.FILEOPEN&&src.key){close();window.FILEOPEN(src.key,stepNow,f.site||'')}};
  dr.querySelectorAll('.rv-f button[data-f]').forEach(b=>b.onclick=()=>{const k=b.dataset.f,v=b.dataset.v;f[k]=f[k]===v?null:v;if(k==='state')f.reason=null;if(k==='label')f.alt=null;open=null;load()});
  const mo=dr.querySelector('.rv-more');if(mo)mo.onclick=()=>{moreOpen=!moreOpen;draw()};
  const lm=dr.querySelector('.rv-lmore');if(lm)lm.onclick=()=>{labelsOpen=!labelsOpen;draw()};
  const nx=dr.querySelector('.rv-next');if(nx)nx.onclick=()=>load(true);
  dr.querySelectorAll('.rv-rawbtn').forEach(b=>b.onclick=()=>{rawOpen=!rawOpen;draw()});
  dr.querySelectorAll('.rv-i').forEach(el=>el.onclick=e=>{if(e.target.closest('a,.rv-x'))return;open=open===el.dataset.id?null:el.dataset.id;draw()});
  // the search asks the server a quarter second after the last key; a newer question cancels the one before
  const qi=dr.querySelector('.rv-q input');if(qi)qi.oninput=()=>{f.q=qi.value.trim();clearTimeout(qTimer);qTimer=setTimeout(()=>{open=null;load()},250)};
}
const OWN=['label','alt','site','bucket','q','miss'];
function query(page){const p=new URLSearchParams({step:stepNow,page:String(page||1)});
  for(const k of ['label','site','bucket','q'])if(f[k])p.set(k,f[k]);if(f.label&&f.alt)p.set('alt',f.alt);if(f.miss)p.set('miss','1');
  for(const [k,v] of Object.entries(f))if(v&&!OWN.includes(k))p.set('f_'+k,v);
  if(night)p.set('night',night);return '/api/raw?'+p}
// the server's reading of the filters: a bucket's word becomes its bucket; a label becomes its key only when it names
// exactly one, else the value sent stays (doc:*, a word two keys share), so 更多, a facet and the search box ask for the
// same set and no single kind's chip looks pressed (RD-02)
function settle(){const F=R.filters||{};
  if(F.bucket&&!f.bucket)f.bucket=F.bucket;if(f.label&&!F.label)f.label=null;
  if(f.label&&F.label&&F.label.length===1&&F.label[0]!==f.label){f.label=F.label[0];f.alt=null}
  if(F.site)f.site=F.site}
function load(more){
  if(ctl)ctl.abort();clearTimeout(waitTimer);ctl=new AbortController();const page=more&&R?(R.page||1)+1:1;loading=true;if(more)draw();
  getJSON(query(page),ctl.signal).then(j=>{loading=false;
    if(j.state==='computing'){R=j;rows=[];draw();if(tries++<80)waitTimer=setTimeout(()=>load(),1500);return}
    tries=0;R=j;if(j.state==='ready')settle();rows=more?rows.concat(j.rows||[]):(j.rows||[]);draw()
  }).catch(e=>{loading=false;if(e.name==='AbortError')return;R={state:'error',words:'讀不到：'+e.message,src:(R&&R.src)||{}};rows=[];draw()})}
// a preset's key (a lost row's data-key) is asked first, its words (label) only when the key finds nothing (RD-13)
function show(step,t,preset,nightKey){stepNow=step;title=t||NAMES[step]||step;night=nightOf(nightKey);f=Object.assign({},preset||{});
  if(f.key){if(f.label&&f.label!==f.key)f.alt=f.label;f.label=f.key}delete f.key;
  if(f.miss===0||f.miss==='0')f.miss=null;open=null;R=null;rows=[];tries=0;
  draw();dr.hidden=false;shade.hidden=false;document.body.classList.add('rvd-open');load()}
window.RAWSHOW=(step,preset,nightKey)=>{if(NAMES[step])show(step,NAMES[step],preset,nightKey)};
window.RAWVIEW=(box,step,n)=>{if(!NAMES[step]||!box)return;const name=(box.querySelector('.sd-h b')||{}).textContent||NAMES[step],nk=nightOf(n);
  const go=pre=>show(step,name,pre,nk);
  const x=box.querySelector('.sd-h .sd-x');if(x&&!box.querySelector('.rv-open')){const b=document.createElement('button');b.className='rv-open';b.type='button';b.textContent='原始紀錄 →';b.onclick=()=>go({});x.before(b)}
  box.querySelectorAll('.sd-io>div').forEach((d,i)=>{if(i===0){d.classList.add('rv-link');d.onclick=()=>go({})}
    if(i===2){d.classList.add('rv-link');d.onclick=()=>go({label:OUT[step]})}});
  // each kind of loss opens on its rows (with a site picked on the page, that site's): the labels the server knows,
  // looked up by the row's key first (data-key: the step's own key), then by its words (RD-13). A word two keys share
  // is sent as the word, so the drawer keeps asking for both
  getJSON(`/api/raw?step=${enc(step)}&per=0`).then(R0=>{const ready=R0.state==='ready',labs=new Map(),bks=new Map(),per=new Map();
    (R0.labels||[]).forEach(([k,w])=>{labs.set(k,{label:k});per.set(w,(per.get(w)||[]).concat(k))});
    per.forEach((ks,w)=>{if(!labs.has(w))labs.set(w,{label:ks.length===1?ks[0]:w})});
    Object.entries(R0.aliases||{}).forEach(([a,ks])=>{if(!labs.has(a)&&ks.length)labs.set(a,{label:ks.length===1?ks[0]:a})});
    (R0.buckets||[]).forEach(([k,w])=>{bks.set(k,{bucket:k});if(!bks.has(w))bks.set(w,{bucket:k})});
    box.querySelectorAll('.sd-lost li').forEach(li=>{const key=(li.dataset.key||'').trim(),word=((li.querySelector('span')||{}).textContent||'').trim();if(!key&&!word)return;
      const pre=ready?(key&&labs.get(key))||(word&&labs.get(word))||(key&&bks.get(key))||(word&&bks.get(word)):Object.assign({label:key||word},key&&word?{alt:word}:{});
      if(!pre)return;li.classList.add('rv-go-li');
      li.onclick=()=>{const s=pickedSite();go(Object.assign({},pre,s?{site:s}:{}))}})}).catch(()=>{});
};

/* ---------------------------------------------------------------- 檔案 */
const fdr=document.createElement('aside');fdr.className='rvd rvd-wide';fdr.hidden=true;fdr.setAttribute('aria-label','檔案');document.body.appendChild(fdr);
const fshade=document.createElement('div');fshade.className='rvd-shade';fshade.hidden=true;document.body.appendChild(fshade);
const fclose=()=>{fdr.hidden=true;fshade.hidden=true;clearTimeout(fTimer)};fshade.onclick=fclose;addEventListener('keydown',e=>{if(e.key==='Escape'&&!fdr.hidden)fclose()});
let fTimer=0,L=null;
const base=p=>String(p).replace(/\/$/,'').split('/').pop();
const cut=(v,n=90)=>{const t=typeof v==='string'?v:JSON.stringify(v);return t.length>n?t.slice(0,n)+'…':t};
const prim=v=>v===null||['string','number','boolean'].includes(typeof v);
function cell(v){if(v==null)return `<span class='jv-null'>—</span>`;if(prim(v)){const t=String(v);return /^https?:\/\//.test(t)?`<a href='${esc(t)}' target='_blank' rel='noopener'${t.length>66?` data-tip='${esc(t)}'`:''}>${esc(cut(t.replace(/^https?:\/\//,''),60))}</a>`:`<span${t.length>90?` data-tip='${esc(t.slice(0,1500))}'`:''}>${esc(cut(t))}</span>`}
  // a list or a group of fields inside a cell: written out short in the cell, the whole of it one hover away
  const d=t=>{try{return decodeURIComponent(t)}catch(e){return t}};
  const txt=Array.isArray(v)?(v.every(prim)?v.map(x=>d(String(x))).join('、'):v.length+' 項'):Object.entries(v).map(([a,b])=>`${a}: ${prim(b)?b:Array.isArray(b)?b.length+' 項':'…'}`).join('；');
  const full=JSON.stringify(v,null,1);
  return `<span class='jv-obj'${txt.length>70||full.length>txt.length+20?` data-tip='${esc(full.length>1500?full.slice(0,1500)+'…':full)}'`:''}>${esc(cut(txt,70))}</span>`}
let tid=0,curK='';const TB={};
// the columns shown first (the rest one click away in 欄位); a table not named here shows its first six
const PREF={'round1:sites':['site','name','imp','round','platform','home'],'round1_work:sites':['site','name','imp','round','platform','home'],
  'manifest:项目':['类型','标题','网址','结果','原因','完整度','网页上写的日期','大小'],'missing:':['类型','标题','网址','缺什么','原因','结果'],
  'build_report:sites':['site','name','unit','docs','data_as_of','missing_pages','missing_files'],'build_report:inputs':['site','records','events','export']};
const store={get(k){try{return JSON.parse(localStorage.getItem('fvcols:'+k)||'null')}catch(e){return null}},set(k,v){try{localStorage.setItem('fvcols:'+k,JSON.stringify(v))}catch(e){}}};
function table(arr,tkey,onRow){const cols=[];arr.slice(0,300).forEach(o=>Object.keys(o||{}).forEach(k=>{if(!cols.includes(k))cols.push(k)}));const id='jt'+(++tid);
  const saved=store.get(tkey),pref=PREF[tkey];const shown=(saved||pref||cols.slice(0,6)).filter(c=>cols.includes(c));
  TB[id]={arr,cols,shown:shown.length?shown:cols.slice(0,6),tkey,q:'',onRow};
  return `<div class='jv-tw' data-t='${id}'><div class='jv-bar'>${arr.length>12?`<input class='jv-f' placeholder='在 ${num(arr.length)} 筆裡找'>`:''}<span class='jv-n'></span>`+
    `<details class='jv-cols'><summary>欄位 <b></b></summary><div class='jv-menu'></div></details></div><div class='fv-scroll jv-scroll'><table class='jv-t'></table></div></div>`}
function renderTable(id){const T=TB[id],box=fdr.querySelector(`.jv-tw[data-t='${id}']`);if(!box)return;const C=T.shown;
  const shownRows=T.arr.map((o,i)=>[o,i]).filter(([o])=>!T.q||JSON.stringify(o).toLowerCase().includes(T.q));
  box.querySelector('.jv-t').innerHTML=`<tr>${C.map(c=>`<th>${esc(c)}</th>`).join('')}</tr>`+shownRows.slice(0,600).map(([o,i])=>`<tr${T.onRow?` class='jv-go' data-i='${i}'`:''}>${C.map(c=>`<td>${cell((o||{})[c])}</td>`).join('')}</tr>`).join('');
  box.querySelector('.jv-n').textContent=(T.q?num(shownRows.length)+' / ':'')+num(T.arr.length)+' 筆'+(shownRows.length>600?'（列前 600）':'');
  box.querySelector('.jv-cols b').textContent=`${C.length}/${T.cols.length}`;
  box.querySelector('.jv-menu').innerHTML=T.cols.map(c=>`<label><input type='checkbox' value='${esc(c)}'${C.includes(c)?' checked':''}>${esc(c)}</label>`).join('')+
    `<div class='jv-mb'><button data-a='main'>主要欄位</button><button data-a='all'>全部</button></div>`;
  if(T.onRow)box.querySelectorAll('tr.jv-go').forEach(tr=>tr.onclick=e=>{if(e.target.closest('a'))return;T.onRow(T.arr[+tr.dataset.i])})}
function mountTables(root){root.querySelectorAll('.jv-tw').forEach(box=>{const id=box.dataset.t,T=TB[id];renderTable(id);
  const q=box.querySelector('.jv-f');if(q)q.oninput=()=>{T.q=q.value.trim().toLowerCase();renderTable(id)};
  box.querySelector('.jv-menu').addEventListener('change',e=>{const c=e.target.value;T.shown=e.target.checked?T.cols.filter(x=>T.shown.includes(x)||x===c):T.shown.filter(x=>x!==c);store.set(T.tkey,T.shown);renderTable(id)});
  box.querySelector('.jv-menu').addEventListener('click',e=>{const a=e.target.dataset.a;if(!a)return;e.preventDefault();T.shown=a==='all'?T.cols.slice():(PREF[T.tkey]||T.cols.slice(0,6)).filter(c=>T.cols.includes(c));store.set(T.tkey,T.shown);renderTable(id)})})}
function jv(v,depth,path=''){
  if(prim(v))return typeof v==='string'&&v.length>120?`<p class='jv-p'>${esc(v)}</p>`:cell(v);
  if(Array.isArray(v)){if(!v.length)return `<span class='jv-null'>（空）</span>`;
    if(v.every(x=>x&&typeof x==='object'&&!Array.isArray(x)))return table(v,curK+':'+path);
    if(v.every(prim))return `<div class='jv-chips'>${v.slice(0,80).map(x=>`<span>${esc(cut(String(x),60))}</span>`).join('')}${v.length>80?`<span>…共 ${v.length}</span>`:''}</div>`;
    return `<pre class='fv-pre'>${esc(JSON.stringify(v,null,1).slice(0,4000))}</pre>`}
  const ks=Object.keys(v);
  return `<dl class='jv-dl'>${ks.map(k=>{const x=v[k],big=!prim(x)&&(Array.isArray(x)?x.length>3:Object.keys(x).length>4);
    const label=`${esc(k)}${Array.isArray(x)?` <small>${x.length}</small>`:''}`;
    return big&&depth>0?`<details class='jv-d'><summary>${label}</summary>${jv(x,depth+1,path?path+'.'+k:k)}</details>`:`<dt>${label}</dt><dd>${jv(x,depth+1,path?path+'.'+k:k)}</dd>`}).join('')}</dl>`}
function md(t){const lines=t.split('\n'),o=[];let tb=null,ul=false;const flush=()=>{if(tb){o.push(`<div class='fv-scroll'><table class='jv-t'>${tb.map((r,i)=>`<tr>${r.map(c=>i?`<td>${esc(c)}</td>`:`<th>${esc(c)}</th>`).join('')}</tr>`).join('')}</table></div>`);tb=null}if(ul){o.push('</ul>');ul=false}};
  for(const l of lines){if(/^\s*\|/.test(l)){if(/^\s*\|[\s:\-|]+\|\s*$/.test(l))continue;(tb=tb||[]).push(l.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim()));continue}
    if(/^\s*[-*] /.test(l)){if(tb)flush();if(!ul){o.push("<ul class='md-ul'>");ul=true}o.push(`<li>${esc(l.replace(/^\s*[-*] /,''))}</li>`);continue}
    flush();const h=l.match(/^(#{1,4}) (.*)/);if(h)o.push(`<h${Math.min(6,h[1].length+2)} class='md-h'>${esc(h[2])}</h${Math.min(6,h[1].length+2)}>`);else if(l.trim())o.push(`<p class='md-p'>${esc(l)}</p>`)}
  flush();return o.join('')}
// a text file read for the eye (owner 11-23: 「yaml文件很难看，其他文件也是一样」): its head comment folded into 說明,
// the rest as a table with search and a column picker, an empty list said in words
function view(v,k,onRow){
  const intro=(v.intro||[]).filter(Boolean);
  const hd=intro.length?`<details class='fv-intro'><summary><b>說明</b><span>${esc(cut(intro[0],80))}</span></summary>${intro.map(p=>`<p>${esc(p)}</p>`).join('')}</details>`:'';
  const top=v.top?`<dl class='jv-dl'>${Object.entries(v.top).map(([a,b])=>`<dt>${esc(a)}</dt><dd>${cell(b)}</dd>`).join('')}</dl>`:'';
  if(!v.rows||!v.rows.length)return hd+top+`<p class='fv-note'>${esc(v.empty||'沒有內容')}</p>`;
  if(v.cols&&!PREF[k+':'])PREF[k+':']=v.cols;
  if(!onRow&&v.rows.length<=12&&v.cols&&v.cols.length===1)return hd+top+`<div class='jv-chips'>${v.rows.map(r=>`<span>${esc(r[v.cols[0]])}</span>`).join('')}</div>`;
  return hd+top+table(v.rows,k+':',onRow)}
// a log's lines: the time dimmed, an error or a warning marked by its level as the 紀錄 viewer does
const LOGLV=[[/error|錯誤|错误|失败|失敗|traceback|exception|fatal/i,'bad'],[/warn|警告|留意|跳过|略過/i,'warn']];
function logLines(lines){return `<div class='fv-scroll fv-logv'>`+lines.map(l=>{const lv=(LOGLV.find(([re])=>re.test(l))||[])[1]||'';
  const m=l.match(/^(\[[^\]]{6,40}\]|\d{4}-\d\d-\d\d[ T][\d:.]+(?:[+-]\d\d:?\d\d)?|\d\d:\d\d:\d\d)\s*(.*)$/);
  return `<div class='fv-ll ${lv}'>${m?(()=>{const t=m[1].replace(/^\[|\]$/g,''),d=t.match(/^(?:[a-z]+ )?(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)/i);return d?`<time data-tip='${esc(t)}'>${d[2]}</time>`:`<time>${esc(t)}</time>`})()+`<span>${esc(m[2])}</span>`:`<span>${esc(l)}</span>`}</div>`}).join('')+`</div>`}
let onRowNow=null;
function look(F,k,step){curK=k;onRowNow=null;const X=F.look||{};
  if(F.secret||X.kind==='secret')return `<p class='fv-note'>金鑰：只在請求標頭裡用，不顯示內容</p>`;
  if(X.kind==='host')return `<p class='fv-note'>${esc(X.note||F.absent||'檢視台沒有掛上這個資料夾')}</p>`;
  if(X.kind==='absent'||(!F.exists&&X.kind!=='view'))return `<p class='fv-note'>這個檔現在不在（${esc(X.absent||F.absent||'這一晚沒用到')}）</p>`;
  if(X.kind==='dir')return `<h6>裡面 ${num(F.count)} 項${F.count>15?'，前 15 項':''}</h6><ul class='fv-ls'>${(F.list||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  // a count is kept a while (the ledger's for minutes while the crawl writes it): it says when it was made
  if(X.kind==='tables'){if(X.pending||!X.tables)return `<h6>資料表</h6><p class='fv-note'>正在數每個資料表有幾筆…</p>`;
    return `<h6>資料表${X.counted?` <small data-tip='數一次要幾秒到幾十秒，數好的留著用'>${esc(X.counted)} 數的</small>`:''}</h6><table class='jv-t'>${X.tables.map(([n,c,approx])=>`<tr><td>${esc(n)}</td><td class='num'>${c==null?'—':(approx?'約 ':'')+num(c)+' 筆'}</td></tr>`).join('')}</table>`}
  if(X.kind==='view'){const v=X.view||{};if(v.open==='site')onRowNow=r=>{if(r&&r['網站'])window.FILEOPEN(k,step,r['網站'])};return view(v,k,onRowNow)}
  if(X.kind==='json'){const c=X.cut||{},note=Object.entries(c).map(([a,n])=>`${a} 前 ${num(Math.min(300,n))} 筆（共 ${num(n)} 筆）`).join('；');return (note?`<h6>${esc(note)}</h6>`:'')+jv(X.json,0)}
  if(X.kind==='lines'){const rs=(X.lines||[]).filter(x=>x&&x.trim()).map(r=>r.split(X.sep||','));
    const hd=X.head,h=hd||(rs[0]||[]).map(s=>s.replace(/^\ufeff/,'')),body=hd?rs:rs.slice(1);
    return (body.length>=299?`<h6>前 ${body.length} 行</h6>`:'')+table(body.map(r=>Object.fromEntries(h.map((c,i)=>[c||('欄'+(i+1)),r[i]]))),k+':')}
  if(X.kind==='tail')return `<h6>最後 ${(X.tail||[]).length} 行</h6>`+logLines(X.tail||[]);
  if(X.kind==='md')return `<div class='md'>${md(X.text||'')}</div>`;
  if(X.kind==='text')return `<pre class='fv-pre'>${esc(X.text||'')}</pre>`;
  if(X.kind==='head')return `<h6>開頭 ${((X.head||'').length/1024).toFixed(0)} KB（共 ${size(X.size||F.size)}）</h6><pre class='fv-pre'>${esc(X.head||'')}</pre>`;
  return `<p class='fv-note'>壓縮檔或二進位檔，不預覽</p>`}
// from a file to its rows (FD-09): the ledger, a site's manifest, the to-text accounting, the library
function rawOf(k,step,site){if(k==='frontier')return ['crawl',{},'看帳本裡每個網址 →'];if(k==='manifest')return ['export',site?{site}:{},'看每一筆 →'];
  if(k==='acc_rows')return ['totext',{},'看每一筆 →'];if(k==='aca')return [step==='swap'?'swap':'build',{},'看每一筆 →'];if(k==='aca_prev')return ['swap',{},'看每一筆 →'];return null}
function fileHtml(F,k,step){const steps=(L&&L.steps)||{},order=(L&&L.order)||ORDER,m=(L&&L.files&&L.files[k])||{};
  const who=list=>esc((list||[]).join('、')||'—');
  const rd=F.readers||order.filter(s=>steps[s]&&steps[s].in.includes(k)).map(s=>steps[s].name),wr=F.writers||order.filter(s=>steps[s]&&steps[s].out.includes(k)).map(s=>steps[s].name);
  const raw=F.exists?rawOf(k,step,F.site):null;
  return `<div class='rvd-h'><div><b>${esc(F.name||m.name||base(F.path||k))}</b><span>${esc(F.what||m.what||'')}${F.site?' · '+esc(F.site):''}</span></div><button class='rvd-x' aria-label='關閉'>×</button></div>`+
    `<div class='fv-meta'><span>路徑</span><code>${esc(F.path||'')}</code>${F.exists?`<span>大小</span><code>${F.size!=null?size(F.size):'計算中'}${F.dir?' · '+num(F.count)+' 項':''}</code><span>最後改動</span><code>${esc(F.mtime||'')}</code>`:''}`+
    `<span>誰讀</span><code>${who(rd)}</code><span>誰寫</span><code>${who(wr)}</code></div>`+
    `<div class='fv-body'>${look(F,k,step)}${raw&&window.RAWSHOW?`<button class='rv-open fv-raw' type='button'>${esc(raw[2])}</button>`:''}</div>`}
function openFile(F,k,step){fdr.innerHTML=fileHtml(F,k,step);fdr.querySelector('.rvd-x').onclick=fclose;mountTables(fdr);
  const r=fdr.querySelector('.fv-raw'),raw=rawOf(k,step,F.site);if(r&&raw)r.onclick=()=>{fclose();window.RAWSHOW(raw[0],raw[1])};
  fdr.hidden=false;fshade.hidden=false}
function fileOpen(k,step,site,tries){clearTimeout(fTimer);const p=new URLSearchParams({key:k});if(step)p.set('step',step);if(site)p.set('site',site);
  if(!tries){fdr.innerHTML=`<div class='rvd-h'><div><b>${esc(((L&&L.files&&L.files[k])||{}).name||k)}</b><span>讀取中…</span></div><button class='rvd-x' aria-label='關閉'>×</button></div>`;fdr.querySelector('.rvd-x').onclick=fclose;fdr.hidden=false;fshade.hidden=false}
  const need=L?Promise.resolve(L):getJSON('/api/file').then(j=>{L=j;return j});
  need.then(()=>getJSON('/api/file?'+p)).then(F=>{if(fdr.hidden&&tries)return;openFile(F,k,step);
    if(F.look&&F.look.pending&&(tries||0)<40)fTimer=setTimeout(()=>fileOpen(k,step,site,(tries||0)+1),1500)})
    .catch(e=>{fdr.querySelector('.rvd-h span').textContent='讀不到：'+e.message})}
window.FILEOPEN=(k,step,site)=>fileOpen(k,step||'',site||'',0);
// a file's part in a step where its name alone misleads (owner 11-23, 換庫's chips): aca.db goes in as the library before
// the swap (today that file is aca.prev.db) and comes out new; aca.db.tmp is gone because it became aca.db
// shared with the neighbouring step: a chain link, the step named on hover (owner 11-23: icons; the arrows read wrong)
const SV=d=>`<svg viewBox='0 0 24 24' width='16' height='16'><path d='${d}'/></svg>`;
const IC_LINK=SV('M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 1 1 0 10h-2M8 12h8');
const ROLE={swap:{in:{aca_tmp:{tag:'新庫',sub:'已改名成 aca.db'},aca:{tag:'換前',from:'aca_prev'}},out:{aca:{tag:'新庫'},aca_prev:{tag:'留底'}}}};
function fileChips(M,step,nk){L=Object.assign({},L||{},{files:M.files,steps:M.steps,order:M.order});const S=M.steps[step],order=M.order||ORDER,i=order.indexOf(step),F=M.info||{};
  const prevOut=i>0?M.steps[order[i-1]].out:[],nextIn=i<order.length-1?M.steps[order[i+1]].in:[];
  const prevN=i>0?M.steps[order[i-1]].name:'',nextN=i<order.length-1?M.steps[order[i+1]].name:'';
  const chip=(k,side)=>{const R0=((ROLE[step]||{})[side]||{})[k]||{},fi=F[R0.from||k]||{},m=M.files[k]||{};
    const tag=R0.tag?`<em class='role'>${R0.tag}</em>`:side==='in'&&prevOut.includes(k)?`<em class='ic' data-tip='${prevN}寫的' aria-label='${prevN}寫的'>${IC_LINK}</em>`:side==='out'&&nextIn.includes(k)?`<em class='ic' data-tip='${nextN}接著讀' aria-label='${nextN}接著讀'>${IC_LINK}</em>`:'';
    // a file on the lab PC no container of the panel mounts (the D: backup) is not missing: its chip says where it is,
    // never struck through (FD-08: the backup must not make the night look failed)
    const host=fi.host&&!fi.secret;
    const sub=R0.sub||(fi.secret?'不顯示':host?(fi.chip||'檢視台沒有掛上'):fi.pick?'每站一份 · 點開選網站':fi.exists===false?'不在':(fi.size!=null?size(fi.size):'')+(fi.dir?(fi.size!=null?' · ':'')+num(fi.count)+' 項':'')+(fi.mtime?' · '+fi.mtime.slice(5):''));
    const tip=(m.what||'')+(host&&fi.absent?'\n'+fi.absent:fi.exists===false&&fi.absent&&!R0.sub&&!fi.pick?'\n不在：'+fi.absent:'');
    return `<button class='fv-c${host?' host':fi.exists===false&&!R0.sub&&!fi.pick?' gone':''}' type='button' data-k='${esc(R0.from||k)}' data-tip='${esc(tip)}'><b>${esc((R0.from?m.name:fi.name)||m.name||base(fi.path||k))}</b><span>${esc(sub)}</span>${tag}</button>`};
  const old=nk&&M.run_day&&nk!==M.run_day?`<p class='fv-now'>檔案是現在的樣子（${esc(M.run_day)} 那一晚以後）：看每個檔的時間，是不是這一晚寫的</p>`:'';
  return old+`<div class='fv-col'><h5>輸入檔</h5>${S.in.map(k=>chip(k,'in')).join('')}</div><div class='fv-mid'><i>→</i><b data-tip='${esc(S.prog||'')}'>${esc(S.name)}</b><i>→</i></div><div class='fv-col'><h5>輸出檔</h5>${S.out.map(k=>chip(k,'out')).join('')}</div>`}
window.FILEVIEW=(box,step,n)=>{if(!NAMES[step]||!box)return;const h=box.querySelector('.sd-h');if(!h)return;box.querySelector('.fv')?.remove();
  const el=document.createElement('div');el.className='fv';el.innerHTML=`<p class='fv-note fv-wait'>讀取這一步的檔案…</p>`;h.after(el);
  box.querySelector('.rv-open')?.remove();
  const site=pickedSite(),nk=nightOf(n);
  getJSON(`/api/file?step=${enc(step)}${site?'&site='+enc(site):''}`).then(M=>{if(!M.steps||!M.steps[step]){el.remove();return}
    el.innerHTML=fileChips(M,step,nk);el.querySelectorAll('.fv-c').forEach(b=>b.onclick=()=>window.FILEOPEN(b.dataset.k,step,site))})
    .catch(()=>{el.innerHTML=`<p class='fv-note'>讀不到這一步的檔案</p>`})};
})();
