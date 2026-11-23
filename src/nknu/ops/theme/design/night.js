// 定時更新: one night inside 內容 at /sites?date=<run day> (nknu.ops.night_view; the owner's design copy
// research/design_2026-11-23/sites.html: night.py's nt-js as content_night.py fitted it, its nm-js, nm_site.py's
// nm-site-js and lvfix_parts.py's maximize). The night's data is fetched one night at a time from /api/night (never
// baked into the page), its 紀錄 from nknu.ops.logs' /api/logs, a picked site's moments from /api/night/ticks. 內容's
// app (overview_page.html) exposes window.APP = {S, S0, render, byKey, LENS} and calls window.NM's hooks (tile, items,
// sum, tip, after); this file defines window.NM once the app is there.
(()=>{
'use strict';
let tries=0;
const boot=()=>{if(window.NM)return;if(window.APP&&document.getElementById('nightBand')){init(window.APP);return}
  if(++tries<200)setTimeout(boot,50)};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();

function init(A){
const $=id=>document.getElementById(id),$q=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const CFG=Object.assign({api:'/api/night',log:'/api/logs',ticks:'/api/night/ticks'},
  (()=>{try{return JSON.parse($('nt-cfg').textContent)}catch(_){return {}}})());
const STEPS=[['crawl','爬取'],['export','導出'],['totext','轉文字'],['build','建庫'],['swap','換庫']];
const stepOk=k=>STEPS.some(s=>s[0]===k)?k:null;  // ?step= from the address: one of the five, else none
// a night not started that is still to come: its 01:00 not an hour past (nightly.MISSED: nothing by 02:00 is 沒跑)
const upcoming=n=>!n.started&&Date.now()<Date.parse(n.run+'T02:00:00+08:00');
// a step's state in 總覽's colours; a step that stopped (or failed to start) is one even with no start time
const tone=c=>!c||c.state==='wait'?'none':c.state==='run'?'run':c.sev==='bad'?'bad':c.state==='stop'?'stop':!c.since?'none':c.sev==='warn'?'warn':'done';
const WORD={done:'完成',warn:'留意',stop:'暫停',bad:'出錯',run:'跑中',none:'還沒開始'};
const hm=v=>v?v.slice(11,16):'',num=v=>v==null?'—':Number(v).toLocaleString();
const mins=(a,b)=>Math.round((Date.parse(b)-Date.parse(a))/60000),dur=m=>m<1?'<1分':m<60?`${m}分`:`${Math.floor(m/60)}時${m%60?m%60+'分':''}`;
let G=null,openStep=null,AX=null,kpiAll='',poll=null,loading=0,askedAt=null;
const cache=new Map();

// ---------------------------------------------------------------- the night's data
async function getJSON(u){const r=await fetch(u,{headers:{Accept:'application/json'},cache:'no-store'});
  if(!r.ok)throw new Error(`${r.status}`);return r.json()}
// only a night before the one 內容 opens on, ended, is kept for the session: the latest, a running one, one still being
// read and one still to come change while the page is open (the countdown reaching 01:00, the steps read after a restart)
async function load(d,fresh){const k=d||'';if(!fresh&&k&&cache.has(k))return cache.get(k);
  const got=await getJSON(`${CFG.api}?date=${encodeURIComponent(k)}`);
  if(!got.live&&!got.reading&&got.run<got.default&&!upcoming(got.n))cache.set(got.run,got);return got}
function nightState(n){const t=STEPS.map(([k])=>tone(n.cells[k]));
  if(t.includes('bad'))return'bad';if(t.includes('stop'))return'stop';if(t.includes('run'))return'run';if(t.every(x=>x==='none'))return'none';
  if(t.includes('warn')||n.probs.length)return'warn';return'done'}
const flowOf=k=>G&&G.flow&&G.flow.steps?G.flow.steps[k]||null:null;

// ---------------------------------------------------------------- head: the state, the span, ‹ day ›, 文件總數
function head(n){
  $('ntDay').textContent=n.label;$('ntWd').textContent='週'+n.wd;
  const s=nightState(n),b=$('ntBadge');b.dataset.s=s;b.dataset.tip=WORD[s];b.setAttribute('aria-label',WORD[s]);b.innerHTML=`<svg viewBox='0 0 16 16' width='18' height='18' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='${({done:'m3.5 8.4 3 3 6-6.6',warn:'M8 3.5v5.5M8 12.2v.3',bad:'M4.5 4.5l7 7M11.5 4.5l-7 7',stop:'M6 4v8M10 4v8',run:'M8 2.5a5.5 5.5 0 1 1-5.5 5.5',none:'M8 8h.01'})[s]||''}'/></svg>`;
  const N=G.nav||[],i=N.findIndex(x=>x.run===n.run),p=$('ntPrev'),q=$('ntNext');
  p.setAttribute('aria-disabled',i<=0);q.setAttribute('aria-disabled',i<0||i>=N.length-1);
  p.href=i>0?`?date=${N[i-1].run}`:'#';q.href=i>=0&&i<N.length-1?`?date=${N[i+1].run}`:'#';
  // what stands now, put by hand (ingest.pause, update.pause), and what of the night's numbers was not kept
  const pz=$('ntPause'),P=G.paused||[];if(pz){pz.hidden=!P.length;pz.textContent=P.map(x=>x[1]).join('、');pz.dataset.tip=P.map(x=>x[2]).join('\n')}
  const nt=$('ntNote');if(nt){nt.hidden=!G.note;nt.textContent=G.note||''}
  clearInterval(window.ntTick);
  if(!n.started&&upcoming(n)){const el=()=>{const x=Math.max(0,Math.floor((Date.parse(n.run+'T01:00:00+08:00')-Date.now())/1000)),p=v=>String(v).padStart(2,'0');
      // at 01:00 the page asks once at once (the poll goes on every minute until the night has started)
      if(!x&&askedAt!==n.run&&G&&G.run===n.run){askedAt=n.run;refresh()}
      return x?`${p(Math.floor(x/3600))}:${p(Math.floor(x%3600/60))}:${p(x%60)}`:'—'};
    kpiAll=`<div class='nt-k' data-tip-h='距離開跑' data-tip='${n.run.slice(5)} 01:00'><small>開跑</small><b id='ntCd'>${el()}</b></div>`;$('ntKpis').innerHTML=kpiAll;$('ntKpis').classList.remove('nm-one');window.ntTick=setInterval(()=>{const e=$('ntCd');if(e)e.textContent=el()},1000);return}
  // a past night that never started: no countdown (it would mean nothing), the night says it did not run
  if(!n.started){kpiAll=`<div class='nt-k' data-tip='${n.run.slice(5)} 01:00 沒有開跑'><small>開跑</small><b>沒跑</b></div>`;$('ntKpis').innerHTML=kpiAll;$('ntKpis').classList.remove('nm-one');return}
  const sw=n.cells.swap,swapped=tone(sw)==='done'||tone(sw)==='warn',C=G.changes||{},lab=C.labels||{},full=C.kind==='full';
  const rows=[`新進：${num(n.new)}`,`離開：${num(n.left)}`].concat(full?Object.entries(C.by_reason||{}).map(([k,v])=>`　${lab[k]||k}：${v}`):[]).join('\n');
  kpiAll=(swapped&&n.unexplained?`<span class='nt-unx' data-tip='離開庫卻說不出原因'>${num(n.unexplained)} 份說不出原因</span>`:'')+`<div class='nt-k nt-lib'${swapped?` data-tip-h='這一晚' data-tip='${esc(rows)}'`:''}><small>文件總數</small><span class='nt-lr'><b>${num(n.docs)}</b>${swapped&&(n.new!=null||n.left!=null)?`<span class='nt-d'><em class='in'>+${num(n.new)}</em><em class='out'>−${num(n.left)}</em></span>`:''}</span></div>`;
  $('ntKpis').innerHTML=kpiAll;$('ntKpis').classList.remove('nm-one')}

// ---------------------------------------------------------------- the five steps on a time line
function ioOf(F){if(!F)return'';const a=F.into&&F.into[0],b=F.out&&F.out[0];return a&&b?`${num(a[1])} → ${num(b[1])}`:a?num(a[1]):b?num(b[1]):''}
function gantt(n){
  const base=Date.parse(n.night+'T00:00:00+08:00')+86400000,h=x=>(Date.parse(x)-base)/3600000;
  let end=10;STEPS.forEach(([k])=>{const c=n.cells[k];if(c&&c.until)end=Math.max(end,Math.ceil(h(c.until)))});
  n.runs.forEach(r=>{if(r.until)end=Math.max(end,Math.ceil(h(r.until)))});
  AX={base,end};
  const x=v=>((h(v)-1)/(end-1)*100),ticks=[];for(let t=1;t<=end;t+=end>10?2:1)ticks.push(t);
  const grid=`<div class='gt-grid'>${ticks.map(t=>`<i style='left:${(t-1)/(end-1)*100}%'></i>`).join('')}</div>`;
  const rows=STEPS.map(([k,w])=>{const c=n.cells[k]||{},t=tone(c);let bars='';
    if(c.since){const a=x(c.since),b=c.until?x(c.until):a;const tip=`${hm(c.since)}–${hm(c.until)||'…'} · ${dur(mins(c.since,c.until||c.since))}`+(c.tip?`\n${c.tip}`:c.say?`\n${c.say}`:'');
      bars+=`<i class='gt-bar' data-s='${t}' style='left:${a}%;width:${Math.max(b-a,.4)}%' data-tip-h='${w} · ${WORD[t]}' data-tip='${esc(tip)}'></i>`}
    if(k==='build')n.runs.filter(r=>!r.shown&&r.since).forEach(r=>{const a=x(r.since),b=r.until?x(r.until):a,s=r.outcome==='swapped'?'done':r.outcome==='failed'?'bad':'stop';
      bars+=`<i class='gt-bar extra' data-s='${s}' style='left:${a}%;width:${Math.max(b-a,.4)}%' data-tip-h='另一次進庫（第 ${r.run} 次）' data-tip='${esc(`${hm(r.since)}–${hm(r.until)} · ${r.reason||''}`)}'></i>`});
    const sub=(c.say==='換上了'?'':String(c.say||'').replace(/^導了 (\d+) 站$/,'$1 站'))||(t==='none'?'沒跑到':'');
    const F=flowOf(k),io=ioOf(F),sel=openStep===k?' sel':'';return `<div class='gt-l${sel}' data-k='${k}'><b${F&&F.does?` data-tip='${esc(F.does)}'`:''}>${w}</b><span title='${esc(sub)}'>${esc(sub)}</span></div><div class='gt-track${sel}' data-k='${k}'>${grid}${bars}</div><div class='gt-t${sel}' data-k='${k}'>${c.since&&c.until?dur(mins(c.since,c.until)):''}</div><div class='gt-io${sel}' data-k='${k}' title='輸入 → 輸出'>${io}</div>`}).join('');
  $('ntGantt').innerHTML=`<div class='gt'>${rows}<div class='gt-axis'>${ticks.map(t=>`<span style='left:${(t-1)/(end-1)*100}%'>${String(t).padStart(2,'0')}</span>`).join('')}</div></div>`;
  $('ntGantt').querySelectorAll('[data-k]').forEach(el=>el.onclick=e=>{if(e.target.closest('.gt-tick'))return;openStep=openStep===el.dataset.k?null:el.dataset.k;url();gantt(n)});
  stepDetail(n);
  $('ntSpan').textContent=n.started?`${hm(n.started)}–${n.ended?hm(n.ended):'…'}`:'';if(n.started)$('ntSpan').dataset.tip=n.ended?'共 '+dur(mins(n.started,n.ended)):'還在跑';else delete $('ntSpan').dataset.tip;
  if(on)siteGantt()}
const short=v=>v>=1e3?(v/1e3).toFixed(v>=1e5?0:1)+'k':Number(v).toLocaleString();

// ---------------------------------------------------------------- one step's detail
function stepDetail(n){let box=$('ntStep');if(!box){box=document.createElement('div');box.id='ntStep';$('ntGantt').after(box)}
  if(!openStep){box.hidden=true;box.innerHTML='';return}box.hidden=false;const F=flowOf(openStep),w=Object.fromEntries(STEPS)[openStep];
  if(!F){box.innerHTML=`<div class='sd-h'><b>${w}</b></div><p class='sd-none'>${esc(G.flow&&G.flow.kind==='none'?(G.flow.lacks||'這一晚的輸入、輸出沒留下'):'這一晚這一步的輸入、輸出沒留下')}</p>`;return}
  const i0=F.into[0]?F.into[0][1]:null,o0=F.out[0]?F.out[0][1]:null;
  const io=`<div class='sd-io'><div${F.into.length>1||(F.into[0]&&!F.out[0])?` data-tip='${esc(F.into.map(x=>num(x[1])+' '+x[0]).join('\n'))}'`:''}><small>輸入</small><b>${num(i0)}</b></div>`+
    `${F.share&&i0&&o0!=null?`<div class='io-bar' data-tip='輸出 ÷ 輸入'><i style='width:${Math.min(100,o0/i0*100)}%'></i><span>通過率 <b>${(o0/i0*100).toFixed(1)}%</b></span></div>`:openStep==='swap'&&i0!=null&&o0!=null?`<div class='io-net'><span>淨 <b>${o0>=i0?'+':'−'}${num(Math.abs(o0-i0))}</b></span></div>`:`<div class='io-line'></div>`}<div${F.out.length>1?` data-tip='${esc(F.out.map(x=>num(x[1])+' '+x[0]).join('\n'))}'`:''}><small>輸出</small><b>${num(o0)}</b></div>`+
    `</div>`;
  // 對帳: a bar a row on one axis, each starting where the last ended; the source's words on hover
  let wf='';if(F.link&&F.link.length){let run=0;const cols=F.link.map(([sg,lb,v,tp])=>{let a,b,k;
      if(sg==='+'){a=run;b=run+v;run=b;k='up'}else if(sg==='−'){a=run-v;b=run;run=a;k='dn'}else{a=0;b=v;run=v;k=sg==='='?'eq':'st'}return {sg,lb,v,tp,a,b,k}});
    const top=Math.max(...cols.map(c=>c.b)),vals=cols.flatMap(c=>c.k==='st'||c.k==='eq'?[c.b]:[c.a,c.b]),mn=Math.min(...vals),lo0=mn-(top-mn)*.6,lo=lo0>top*.3?lo0:0;
    const X=v=>top>lo?Math.max(0,v-lo)/(top-lo)*100:0;const brs=cols.filter(c=>c.v||c.k==='st'||c.k==='eq').map(c=>{const a=X(c.a),b=X(c.b);return `<div class='br-r br-${c.k}'${c.tp?` data-tip-h='${esc(c.lb)}' data-tip='${esc(c.tp)}'`:''}><span class='br-l'>${esc(c.lb)}</span><span class='br-t'><i style='left:${a}%;width:${Math.max(.6,b-a)}%'></i></span><b>${(c.sg==='+'?'+':c.sg==='−'?'−':'')+num(c.v)}</b></div>`}).join('');
    // a reconciliation that does not add up is marked on its heading (the design dropped live's 「還差 N 對不上」)
    const off=F.link_left?`<em class='br-off' data-tip='這幾項加減完，和這一步的輸入還差這麼多（對不上）'>還差 ${num(Math.abs(F.link_left))}</em>`:'';
    wf=`<div class='sd-wf sd-br'><h5 data-tip='上一步的輸出，逐項加減成這一步的輸入'>對帳${off}</h5>${brs}</div>`}
  // what did not go on: the six largest, the rest folded
  const L=F.lost||[],mx=Math.max(1,...L.map(x=>x[1])),hd=(F.lost_label||'沒走到下一步的').split('（')[0];
  const li=x=>`<li${x[2]?` data-key='${esc(x[2])}'`:''}><span title='${esc(x[0])}'>${esc(x[0])}</span><i><em style='width:${Math.max(1.5,x[1]/mx*100)}%'></em></i><b>${num(x[1])}</b></li>`;
  const rest=L.slice(6),lost=L.length?`<div class='sd-lostbox'><h5 data-tip='${esc(F.lost_tip||F.lost_label||'')}'>${esc(hd)}</h5><ul class='sd-lost'>${L.slice(0,6).map(li).join('')}</ul>`+
    (rest.length?`<details class='sd-more'><summary>其餘 ${rest.length} 項 · ${num(rest.reduce((t,x)=>t+x[1],0))}</summary><ul class='sd-lost'>${rest.map(li).join('')}</ul></details>`:'')+`</div>`:'';
  const lacks=G.flow&&G.flow.kind!=='live'&&G.flow.lacks?`<p class='sd-none sd-lacks'>${esc(G.flow.lacks)}</p>`:'';
  box.innerHTML=`<div class='sd-h'><b data-tip='${esc(F.does||'')}'>${w}</b><button class='sd-x' aria-label='收起'>×</button></div>${io}${(F.sides||[]).length?`<div class='sd-sides'>${F.sides.map(([nm,st,tp])=>`<span class='sd-side s-${esc(st)}' data-tip-h='${esc(nm)}' data-tip='${esc(tp)}'>${esc(({'校外網站每週重看':'校外週檢','要用瀏覽器開的網站':'瀏覽器抓取'})[nm]||nm)}</span>`).join('')}</div>`:''}${wf||lost?`<div class='sd-cols'>${wf}${lost}</div>`:''}${lacks}`;
  box.querySelector('.sd-x').onclick=()=>{openStep=null;url();gantt(n)};
  // the records and files behind a step are the lab's as they are now: only the night the steps were read for
  if(G.latest){const m=Object.assign({},n,{latest:true});if(window.RAWVIEW)window.RAWVIEW(box,openStep,m);if(window.FILEVIEW)window.FILEVIEW(box,openStep,m)}}

// ---------------------------------------------------------------- 問題
function probs(n){
  $('ntProbN').textContent=n.probs.length?n.probs.length+' 件':'';
  const mk=s=>`<span class='td-mark td-${s}'><svg viewBox='0 0 16 16' width='12' height='12' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round'>${s==='bad'?"<path d='M5 5l6 6M11 5l-6 6'/>":"<path d='M8 3.2v5.6'/><circle cx='8' cy='11.8' r='.5' fill='currentColor'/>"}</svg></span>`;
  $('ntProbs').innerHTML=n.probs.length?n.probs.map(p=>`<li>${mk(p.sev)}<b>${esc(p.where)}</b><span>${esc(p.what)}${p.code?`<code>${esc(p.code)}</code>`:''}</span></li>`).join('')
    :`<li class='nt-none' style='display:flex;border:0'>✓ 沒有問題</li>`}

// ---------------------------------------------------------------- 紀錄: the night's lines, a page at a time from nknu.ops.logs
// (/api/logs?date=<run day>: the night's window, newest first; its counts are the design's chips: a level's under the
// sources and words chosen, a source's under the levels and words)
const LV=[['error','錯誤','var(--bad-s)'],['warn','警告','var(--warn-s)'],['info','資訊','var(--q4)'],['debug','除錯','var(--line2)']];
const SRC=[['crawl','爬取'],['export','導出'],['totext','轉文字'],['build','建庫'],['ingest','進庫']];
const lvOn={lv:new Set(['error','warn']),src:new Set(SRC.map(s=>s[0]))};
const L_EMPTY={rows:[],hist:[],levels:{},sources:{},all:0,more:false,next:null,ok:true};
let L0=Object.assign({},L_EMPTY),q='',lvSeq=0,lvBusy=false;
function chips(id,list,set,count){const box=$(id);box.innerHTML=list.map(([k,w,c])=>`<button data-k='${k}' aria-pressed='${set.has(k)}'>${c?`<i style='background:${c}'></i>`:''}${w} <small>${count(k)}</small></button>`).join('');
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>{set.has(b.dataset.k)?set.delete(b.dataset.k):set.add(b.dataset.k);lvLoad()})}
// nothing chosen in a row of chips lists nothing (the log's own default would list every level or source)
function lvURL(before){const p=new URLSearchParams({date:G.run,lv:[...lvOn.lv].join(',')||'error',src:[...lvOn.src].join(',')||'crawl',q,n:'500'});if(before)p.set('before',before);return `${CFG.log}?${p}`}
// how far back the panel holds each log (logs.py: FIRST_READ of a log the first time, at most KEEP_ROWS lines): a
// source chosen whose lines are held only from after the window's start (its cut_from, else its earliest line held)
// is said, never read as a quiet night (map NV28: retention)
function lvShort(got){const from=String(got.from||'');if(!from)return'';const kf=got.kept_from||{},cf=got.cut_from||{},SW=Object.fromEntries(SRC);
  return SRC.filter(([k])=>lvOn.src.has(k)).map(([k])=>[k,cf[k]||kf[k]||'']).filter(([,t])=>t&&t>from)
    .map(([k,t])=>`${t.slice(5,10)} ${t.slice(11,16)} 以前的${SW[k]}紀錄沒有留在檢視台`).join('；')}
function lvTake(got){const none=!lvOn.lv.size||!lvOn.src.size,c=got.counts||{};
  return {rows:none?[]:(got.rows||[]),hist:none?[]:(got.hist||[]),levels:lvOn.src.size?(c.levels||{}):{},sources:lvOn.lv.size?(c.sources||{}):{},
    all:got.total||0,more:!none&&!!got.more,next:got.next||null,ok:true,short:lvShort(got)}}
async function lvLoad(keep){if(!G)return;const seq=++lvSeq;if(!G.n.started){L0=Object.assign({},L_EMPTY);lvDraw();return}
  try{const got=await getJSON(lvURL());if(seq!==lvSeq)return;L0=lvTake(got)}
  catch(e){if(seq!==lvSeq)return;L0=Object.assign({},L_EMPTY,{ok:false,why:`讀不到這一晚的紀錄（${e.message}）`})}
  lvDraw(keep===true)}
async function lvMore(){if(lvBusy||!L0.more||!L0.next)return;lvBusy=true;const seq=lvSeq;
  try{const got=await getJSON(lvURL(L0.next));if(seq!==lvSeq)return;const more=lvTake(got);
    L0.rows=L0.rows.concat(more.rows);L0.more=more.more;L0.next=more.next;lvDraw(true)}
  catch(_){L0.more=false}finally{lvBusy=false}}
function lvDraw(keep){
  chips('lv-lv',LV,lvOn.lv,k=>Number(L0.levels[k]||0).toLocaleString());
  chips('lv-src',SRC,lvOn.src,k=>Number(L0.sources[k]||0).toLocaleString());
  const list=$('lv-list'),y=list.scrollTop;
  if(L0.ok===false){$('lv-hist').innerHTML='';list.innerHTML=`<div class='lv-empty'>${esc(L0.why||'讀不到這一晚的紀錄')}</div>`;return}
  const short=L0.short?`<div class='lv-empty lv-short'>${esc(L0.short)}</div>`:'';
  if(!L0.all&&!L0.rows.length){$('lv-hist').innerHTML='';list.innerHTML=short||`<div class='lv-empty'>這一晚沒有留下紀錄</div>`;return}
  const H=L0.hist||[],top=Math.max(1,...H.map(x=>(x.error||0)+(x.warn||0)+(x.info||0)+(x.debug||0)));
  const tOf=t=>typeof t==='number'?new Date(t*(t<1e12?1000:1)).toTimeString().slice(0,5):String(t||'').length>=16?String(t).slice(11,16):String(t||'');
  $('lv-hist').innerHTML=H.map(x=>`<div data-tip-h='${esc(tOf(x.t))}' data-tip='錯誤 ${x.error||0} · 警告 ${x.warn||0} · 資訊 ${x.info||0}'>`+LV.map(([k,,c])=>x[k]?`<i style='height:${x[k]/top*100}%;background:${c}'></i>`:'').join('')+'</div>').join('');
  const rows=L0.rows;
  if(!rows.length){const quiet=!q&&[...lvOn.lv].every(k=>k==='error'||k==='warn');
    list.innerHTML=short+`<div class='lv-empty'>${quiet?`沒有錯誤或警告；全部 ${Number(L0.all||0).toLocaleString()} 行在上面「資訊」`:'沒有符合的紀錄'}</div>`;return}
  const LW={error:'ERROR',warn:'WARN',info:'INFO',debug:'DEBUG'},SW=Object.fromEntries(SRC);
  let lastD='';list.innerHTML=short+rows.map(r=>{const d=r[0].slice(5,10),sep=d!==lastD?`<div class='lv-day'>${d}</div>`:'';lastD=d;return sep+`<div class='lv-row' data-l='${esc(r[1])}'><span class='lv-t'>${esc(r[0].slice(11,19))}</span><span class='lv-s'>${esc(SW[r[2]]||r[2])}</span><span class='lv-m'>${r[1]==='error'||r[1]==='warn'?`<b class='lv-lw'>${LW[r[1]]}</b>`:''}${esc(String(r[3]).replace(/\s+\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$/,''))}</span></div>`}).join('')+(L0.more?`<div class='lv-empty lv-wait'>往下捲看更早的</div>`:'');
  list.querySelectorAll('.lv-row').forEach(x=>x.onclick=()=>x.classList.toggle('open'));
  if(keep)list.scrollTop=y;else list.scrollTop=0}
let qT=null;$('lv-q').oninput=e=>{clearTimeout(qT);qT=setTimeout(()=>{q=e.target.value.trim();lvLoad()},250)};
$('lv-list').addEventListener('scroll',e=>{const l=e.target;if(l.scrollTop+l.clientHeight>l.scrollHeight-40)lvMore()});
// the log over the whole window (lvfix_parts.py): Esc or a click outside closes it
(()=>{const card=()=>$('nightLog');
  const set=v=>{const c=card();if(!c)return;c.classList.toggle('lv-maxed',v);document.body.classList.toggle('lv-open',v);
    const b=c.querySelector('.lv-max-btn');if(b){b.dataset.icon=b.dataset.icon||b.innerHTML;b.setAttribute('aria-label',v?'收回紀錄':'放大紀錄');b.innerHTML=v?"<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round'><path d='M4 4l8 8M12 4l-8 8'/></svg>":b.dataset.icon}};
  document.addEventListener('click',e=>{const b=e.target.closest('#nightLog .lv-max-btn');if(b){set(!card().classList.contains('lv-maxed'))}
    else if(document.body.classList.contains('lv-open')&&!e.target.closest('.lv-maxed'))set(false)},true);
  addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('lv-open'))set(false)})})();

// ---------------------------------------------------------------- show a night (‹ › and ← → redraw in place)
// the address keeps the night and its open step: a reload (內容 reloads itself when the library changes) comes back here;
// only ?date and &step are the night's, the rest of the address (內容's ?ck, ?kind) stays as it is
function url(){if(!G)return;addr(u=>{u.searchParams.set('date',G.run);if(openStep)u.searchParams.set('step',openStep);else u.searchParams.delete('step')})}
function addr(edit){try{const u=new URL(location.href);edit(u);if(u.search!==location.search)history.replaceState(history.state,'',u.pathname+u.search+u.hash)}
  catch(_){/* an address that cannot be rewritten: the night still shows */}}
function fail(e){$('ntGantt').innerHTML=`<p class='sd-none'>讀不到這一晚（${esc(e&&e.message||e)}）；過一會兒再試</p>`}
async function show(d,opt){opt=opt||{};const seq=++loading;let got;
  if(!G)$('ntGantt').innerHTML=`<p class='sd-none'>讀取中…</p>`;
  try{got=await load(d,opt.fresh)}catch(e){if(seq===loading)fail(e);return}
  if(seq!==loading||!on)return;
  const same=G&&G.run===got.run,want=stepOk(opt.step);G=got;if(!opt.keep&&!same)openStep=want;else if(want)openStep=want;
  window.FLOW={night:G.latest?G.night:null,run:G.run,latest:!!G.latest,steps:G.latest?G.flow.steps:null};
  url();
  const n=G.n;head(n);gantt(n);probs(n);apply(n);
  // a redraw of the same night (the poll) reads the log again, keeping where it was scrolled to (NV37)
  if(!opt.keep||!same)lvLoad();else lvLoad(true);
  watch()}
function refresh(){if(G&&on)show(G.run,{fresh:true,keep:true})}
// the page asks again while the night can still change: going on (every minute), its steps being read after a restart
// (every five seconds), still to come (every minute, and once at 01:00: head)
function watch(){clearInterval(poll);poll=null;if(!G||!on)return;const every=G.reading?5000:G.live||upcoming(G.n)?60000:0;if(!every)return;
  poll=setInterval(async()=>{if(document.hidden||!G)return;try{const b=await getJSON(`${CFG.api}?date=${G.run}&brief=1`);if(b.sig!==G.sig)refresh()}catch(_){}},every)}
document.addEventListener('click',e=>{const a=e.target.closest('#ntPrev,#ntNext');if(!a)return;e.preventDefault();if(a.getAttribute('aria-disabled')==='true')return;
  const k=new URL(a.href,location.href).searchParams.get('date');if(k)show(k)});
addEventListener('keydown',e=>{if(!document.body.classList.contains('night-on')||e.target.closest('input,textarea,select')||e.metaKey||e.ctrlKey||e.altKey)return;const a=e.key==='ArrowLeft'?$('ntPrev'):e.key==='ArrowRight'?$('ntNext'):null;if(a&&a.getAttribute('aria-disabled')!=='true'){e.preventDefault();a.click()}});

// ---------------------------------------------------------------- the map: this night's changes (nm-js)
let on=false,cur=null,ch={},pr={},sortBy='all',openG=new Set();
// 內容's groups (overview_data: GROUP_ORDER, then any other group its sites are in): the app's own list when it gives
// one (APP.GROUPS), else the page's data, read once and only when a site sits past the nine everyone knows
const G9=['文學院','教育學院','理學院','藝術學院','科技學院','管理學院','行政處室','中心','全校共用'];
let GX=null;
function gnames(need){if(Array.isArray(A.GROUPS))return A.GROUPS;if(need<G9.length)return G9;
  if(!GX){try{GX=JSON.parse($('data').textContent).groups}catch(_){GX=null}if(!Array.isArray(GX))GX=G9}return GX}
const gname=g=>gnames(g)[g]||'其他';
const NKEY="<h4>這一晚的變動</h4><div class='key-r'><span class='kb'><i style='width:10px;height:10px'></i><i style='width:18px;height:18px'></i><i style='width:28px;height:28px'></i></span><span>只畫這一晚有變動的網站；方塊越大，新進＋改了＋離開越多</span></div>"+
"<div class='key-r'><span><span class='ramp' id='keyRamp'><i style='background:var(--q1)'></i><i style='background:var(--q3)'></i><i style='background:var(--q5)'></i></span></span><span>紫色越深變得越多；黃、紅是這一晚的問題點名到的網站</span></div><div class='key-r'><span></span><span>點方塊看這個網站這一晚的變動</span></div>";
const amt=c=>c?(c.new||0)+(c.changed||0)+(c.left||0):0,step=a=>a<=0?-1:Math.min(5,1+Math.floor(Math.log2(a)/1.6));
// a site only a problem names has a value too: the app draws a tile's label (出錯 / 留意) only for one (map NV25)
A.LENS.night={name:'這一晚的變動',lo:'',hi:'',tip:'',nil:'',f:s=>amt(ch[s.k])>0||pr[s.k]?.5:null,
  lab:s=>pr[s.k]?(pr[s.k].sev==='bad'?'出錯':'留意'):ch[s.k]&&amt(ch[s.k])?[ch[s.k].new?'+'+ch[s.k].new:'',ch[s.k].changed?'改 '+ch[s.k].changed:'',ch[s.k].left?'−'+ch[s.k].left:''].filter(Boolean).join(' '):'',bin:()=>''};
function items(list){return list.filter(s=>amt(ch[s.k])>0||pr[s.k]).map(s=>{const v=Math.max(amt(ch[s.k]),pr[s.k]?2:1);return {s,p:[v,0,0,0,0],v}})}
const sum=v=>v.toLocaleString()+' 份';
function tip(s){const c=ch[s.k],p=pr[s.k],a=amt(c);
  const bar=(w,v,col)=>`<div class='tt-r'><span>${w}</span><span class='tt-b'>${v?`<i style='width:${Math.max(4,v/(a||1)*100)}%;background:${col}'></i>`:''}</span><b>${v==null?'—':v}</b></div>`;
  return `<div class='tt'><div class='tt-top'><div><h4>${esc(s.f)}</h4><p class='tt-s'>${esc(s.g!=null?gname(s.g):'')}</p></div>`+
    (c?`<div class='tt-n'><b>${a} 份</b><span>${cur?cur.label:''}</span></div>`:'')+`</div>`+
    (c?bar('新進',c.new,'var(--cal-in)')+bar('改了',c.changed,'var(--ink3)')+bar('離開',c.left,'var(--cal-out)'):'')+
    (p?(c?"<div class='tt-d'></div>":'')+p.rows.map(r=>`<p class='tt-p ${r.sev}'><b>${esc(r.where)}</b> ${esc(r.what)}</p>`).join(''):'')+`</div>`}
function apply(n){cur=n;ch={};pr={};const box=$q('#nightBand .nt-probs');if(box)box.hidden=!n.probs.length;
  ((G.changes||{}).sites||[]).forEach(x=>{ch[x.site]=x});
  n.probs.forEach(p=>(p.sites||[]).forEach(k=>{if(!A.byKey[k])return;const o=pr[k]||(pr[k]={sev:'warn',rows:[]});if(p.sev==='bad')o.sev='bad';o.rows.push(p)}));
  A.S.lens='night';A.render();
  // a problem that names a site: a click picks that site on the map (and its row in the side card)
  document.querySelectorAll('#ntProbs>li').forEach((li,i)=>{const p=n.probs[i];if(!p)return;const k=(p.sites||[]).find(x=>A.byKey[x]);if(!k)return;
    li.classList.add('nm-jump');li.title='看這個網站';li.onclick=()=>{A.S.site=k;A.render();const tm=$('tm');if(tm)tm.scrollIntoView({behavior:'smooth',block:'center'})}})}
function tile(s,el,st){const p=pr[s.k],a=amt(ch[s.k]),k=step(a);
  if(p){st.setProperty('--fill',p.sev==='bad'?'var(--bad-s)':'var(--warn-s)');el.classList.toggle('inv',p.sev==='bad');return}
  st.setProperty('--fill',k<0?'var(--mark2)':'var(--q'+k+')');el.classList.toggle('inv',k>=4)}
function ring(tot){const T=(tot.new||0)+(tot.changed||0)+(tot.left||0);
  const P=[['新進',tot.new,'var(--cal-in)'],['改了',tot.changed,'var(--ink3)'],['離開',tot.left,'var(--cal-out)']];let a0=-Math.PI/2;
  const arcs=T?P.map(([,v,c])=>{if(!v)return'';const a1=a0+v/T*Math.PI*2-(v<T?.04:0),r=60,L=a1-a0>Math.PI?1:0,x0=75+r*Math.cos(a0),y0=75+r*Math.sin(a0),x1=75+r*Math.cos(a1),y1=75+r*Math.sin(a1);
    const out=v===T?`<circle cx='75' cy='75' r='${r}' fill='none' stroke='${c}' stroke-width='22'/>`:`<path d='M${x0} ${y0}A${r} ${r} 0 ${L} 1 ${x1} ${y1}' fill='none' stroke='${c}' stroke-width='22'/>`;a0+=v/T*Math.PI*2;return out}).join(''):'';
  return `<div class='nm-ring'><svg viewBox='0 0 150 150' width='150' height='150'><circle cx='75' cy='75' r='60' fill='none' stroke='var(--mark2)' stroke-width='22'/>${arcs}`+
    `<text x='75' y='77' text-anchor='middle' class='nm-rv'>${T?T.toLocaleString():'—'}</text><text x='75' y='94' text-anchor='middle' class='nm-rl'>份</text></svg>`+
    `<ul>${P.map(([w,v,c])=>`<li><i style='background:${c}'></i><span>${w}</span><b>${v==null?'—':Number(v).toLocaleString()}</b></li>`).join('')}</ul></div>`}
function side(){const box=$('insp');if(!box)return;let d=$('nmSite');if(!d){d=document.createElement('div');d.id='nmSite';box.appendChild(d)}
  if(!on||!G){d.hidden=true;return}d.hidden=false;const k=A.S.site,C=G.changes||{},kind=C.kind||'none',labels=C.labels||{};
  // the night's own counts, — where they were not kept (a night with no record and no comparison: never 0, G9)
  const v=x=>x==null?null:x;
  const tot=k?(ch[k]||(kind==='none'?{new:null,changed:null,left:null}:{new:0,changed:kind==='counts'?null:0,left:0}))
    :{new:v(cur&&cur.new),changed:v(cur&&cur.changed),left:v(cur&&cur.left)};
  const unknown=!k&&tot.new==null&&tot.left==null?`<p class='nm-muted'>這一晚的新進、改了、離開沒留下</p>`:'';
  const list=(w,xs,cls,n)=>xs&&xs.length?`<h5>${w} <span>${n}</span></h5><ul>${xs.map(x=>`<li class='${cls}'>${x.id?`<a href='/trace?ref=${encodeURIComponent(x.id)}&t=${encodeURIComponent(x.t||'')}' data-tip='${esc(x.u)}'>${esc(x.t||x.u)}</a><a class='nm-ext' href='${esc(x.u)}' target='_blank' rel='noopener' data-tip='學校原網頁'>↗</a>`:`<a href='${esc(x.u)}' target='_blank' rel='noopener' data-tip='${esc(x.u)}'>${esc(x.t||x.u)}</a>`}${x.why?`<small>${esc(labels[x.why]||x.why)}</small>`:''}</li>`).join('')}${n>xs.length?`<li class='nm-more'>還有 ${n-xs.length} 份</li>`:''}</ul>`:'';
  if(!k){const rows=A.S0.filter(s=>amt(ch[s.k])>0||pr[s.k]).map(s=>({k:s.k,n:s.n,g:s.g,c:ch[s.k]||{new:0,changed:0,left:0},p:pr[s.k]}));
    const key={name:r=>r.n,new:r=>r.c.new||0,changed:r=>r.c.changed||0,left:r=>r.c.left||0,all:r=>(r.p?1e6:0)+amt(r.c)};
    const cmp=(x,y)=>sortBy==='name'?String(x.n).localeCompare(y.n,'zh-Hant'):key[sortBy](y)-key[sortBy](x);
    // the groups of 內容 (colleges, offices, centres), each the sum of its sites; open one to see its sites
    const gs=[...new Set(rows.map(r=>r.g))].filter(g=>g!=null).sort((a,b)=>a-b);
    const groups=gs.map(g=>{const n=gname(g),rs=rows.filter(r=>r.g===g).sort(cmp);const c={new:0,changed:0,left:0};rs.forEach(r=>{c.new+=r.c.new||0;c.changed+=r.c.changed||0;c.left+=r.c.left||0});
      const sev=rs.some(r=>r.p&&r.p.sev==='bad')?'bad':rs.some(r=>r.p)?'warn':'';return {g,n,rs,c,p:sev?{sev}:null}}).filter(x=>x.rs.length).sort(cmp);
    const z=v=>v?'':' z',hd=(v,w)=>`<button data-s='${v}' aria-pressed='${sortBy===v}'>${w}</button>`;
    const nums=c=>`<b class='in${z(c.new)}'>${c.new?'+'+c.new:0}</b><b class='${z(c.changed)}'>${kind==='counts'?'—':c.changed||0}</b><b class='out${z(c.left)}'>${c.left?'−'+c.left:0}</b>`;
    const dot=p=>p?`<i class='pm ${p.sev}'></i>`:'';
    d.innerHTML=ring(tot)+unknown+(rows.length?`<div class='nm-list'><div class='nm-lh'>${hd('name','網站 '+rows.length)}${hd('new','新進')}${hd('changed','改了')}${hd('left','離開')}</div><ul class='nm-lb'>`+
      groups.map(x=>{const op=openG.has(x.g);return `<li class='nm-g' data-g='${x.g}' aria-expanded='${op}'><span><em>${op?'▾':'▸'}</em>${dot(x.p)}${esc(x.n)} <small>${x.rs.length}</small></span>${nums(x.c)}</li>`+
        (op?x.rs.map(r=>`<li class='nm-s' data-k='${esc(r.k)}' title='${esc(r.k)}'><span>${dot(r.p)}${esc(r.n)}</span>${nums(r.c)}</li>`).join(''):'')}).join('')+`</ul></div>`
      :`<p class='nm-muted'>這一晚沒有網站有變動</p>`)+(kind==='none'&&rows.length?`<p class='nm-muted'>這一晚的逐站份數沒留下，只列問題點名的網站</p>`:kind==='counts'&&rows.length?`<p class='nm-muted'>這一晚只留下逐站的新進和離開；改了沒留下</p>`:'');
    d.querySelectorAll('.nm-g').forEach(li=>li.onclick=()=>{const g=+li.dataset.g;openG.has(g)?openG.delete(g):openG.add(g);side()});
    d.querySelectorAll('.nm-s').forEach(li=>li.onclick=()=>{A.S.site=li.dataset.k;A.render()});
    d.querySelectorAll('.nm-lh button').forEach(bt=>bt.onclick=()=>{sortBy=bt.dataset.s===sortBy?'all':bt.dataset.s;side()});return}
  const c=ch[k],p=pr[k];
  d.innerHTML=ring(tot)+unknown+(p?`<ul class='td nm-p'>${p.rows.map(r=>`<li><span class='td-mark td-${r.sev}'>!</span><b>${esc(r.where)}</b><span>${esc(r.what)}</span></li>`).join('')}</ul>`:'')+
    (c&&c.lists?list('新進',c.lists.new,'in',c.new)+list('改了',c.lists.changed,'',c.changed)+list('離開',c.lists.left,'out',c.left)
      :`<p class='nm-muted'>${kind==='full'?'這一晚沒有變動':c?'這一晚的逐站清單沒留下（只留了新進和離開的份數）':'這一晚的逐站變動沒留下'}</p>`)}
function after(){
  document.body.classList.toggle('night-on',on);$('nightBand').hidden=!on;$('nightLog').hidden=!on;
  document.querySelectorAll('.nm-sw button').forEach(b=>b.setAttribute('aria-pressed',(b.dataset.v==='night')===on));
  const key=$('tmKey');
  const txt=(id,v)=>{const el=$(id);if(el)el.textContent=v};
  if(on){const n=A.S0.filter(s=>amt(ch[s.k])>0||pr[s.k]).length;
    if(A.S.grp===null){txt('hMap','各網站變動');txt('hMapSub',n)}
    if(!A.S.site){txt('iT','文件變化');txt('iS','')}
    if(key){if(!key.dataset.all)key.dataset.all=key.innerHTML;key.innerHTML=NKEY}
    const kind=G&&G.changes?G.changes.kind:'none';
    const lg=$('tmLegend');if(lg)lg.innerHTML=`<span class='nm-key' data-tip='方塊大小：新進＋改了＋離開'><span><i style='background:var(--q2)'></i><i style='background:var(--q4)'></i>越深越多</span><span><i style='background:var(--warn-s)'></i>留意</span><span><i style='background:var(--bad-s)'></i>出錯</span>${kind==='full'?'':kind==='counts'?"<em>這一晚只留下逐站的新進和離開；改了沒留下</em>":"<em>這一晚只有問題點名的網站，逐站變動沒留下</em>"}</span>`}
  else if(key&&key.dataset.all)key.innerHTML=key.dataset.all;
  side();focus()}
function set(night,date,stepKey){
  if(!night){on=false;G=null;pickKey=null;clearInterval(poll);clearInterval(window.ntTick);A.S.lens=null;addr(u=>{u.searchParams.delete('date');u.searchParams.delete('step')});A.render();return}
  on=true;document.body.classList.add('night-on');$('nightBand').hidden=false;$('nightLog').hidden=false;
  return show(date||'',{step:stepOk(stepKey)})}
document.querySelector('.nm-sw')?.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;set(b.dataset.v==='night')});

// ---------------------------------------------------------------- a site picked narrows the band (nm-site-js)
const PS=()=>(G&&G.sites)||{};
const forms=k=>[k,k.replace(/\//g,'_')];
const LC={gone:'var(--ink3)',login:'var(--ink3)',review:'var(--warn-s)',failed:'var(--bad-s,#e5484d)',queued:'color-mix(in srgb,var(--warn-s) 50%,transparent)'};
const OUT={crawl:'已取得',export:'已存檔',totext:'已轉文字',swap:'新進'};  // the drawer's label of what a step put out
const tickCache=new Map(),tickAsk=new Map(),rawCounts=new Map();
// a site's moments are asked once per (night, site): an ended night's kept for the session, a night going on's for
// TICK_FRESH (its poll redraws the time line, which draws them again from here and asks once they are older); a
// request already out is waited on, never sent twice (a pick redraws the app twice, a resize on every step)
const TICK_FRESH=30000,TICK_KEEP=64;
let pickKey=null,labelsFor=null,labelsAsk=null;
const xAt=t=>AX?((Date.parse(t)-AX.base)/3600000-1)/(AX.end-1)*100:-1;
function drawTicks(k,got){document.querySelectorAll('#ntGantt .gt-tick').forEach(x=>x.remove());if(!k||!got||!G||got.run!==G.run||got.site!==k)return;
  got.ticks.forEach(o=>{const tr=$q(`#ntGantt .gt-track[data-k='${o.st}']`),x=xAt(o.t);if(!tr||x<0||x>100)return;
    const i=document.createElement('i');i.className='gt-tick';i.style.left=x+'%';i.dataset.tipH=hm(o.t)+(o.n>1?` · ${o.n} 行`:'');i.dataset.tip=String(o.msg||'').slice(0,140);tr.append(i)})}
function ticks(k){if(!k||!G||!G.n.started){drawTicks(null);return}const memo=G.run+'|'+k,hit=tickCache.get(memo);
  drawTicks(k,hit&&hit.got);  // what is known now (or nothing: another site's moments never stay up)
  if(hit&&(hit.kept||Date.now()-hit.at<TICK_FRESH)||tickAsk.has(memo))return;
  const run=G.run,kept=!G.live;
  tickAsk.set(memo,getJSON(`${CFG.ticks}?date=${encodeURIComponent(run)}&site=${encodeURIComponent(k)}`)
    .then(got=>{tickCache.delete(memo);tickCache.set(memo,{got,at:Date.now(),kept});
      while(tickCache.size>TICK_KEEP)tickCache.delete(tickCache.keys().next().value);
      if(A.S.site===k&&G&&G.run===run)drawTicks(k,got)})
    .catch(()=>{}).finally(()=>tickAsk.delete(memo)))}
function io(k){const p=k&&PS()[k],c=ch[k];
  document.querySelectorAll('#ntGantt .gt-io').forEach(el=>{if(el.dataset.all===undefined)el.dataset.all=el.textContent;
    if(!p){el.textContent=el.dataset.all;el.classList.remove('nm-one');return}
    const s=el.dataset.k,prev=p.docs_before!=null?p.docs_before:p.docs!=null?p.docs-(c?c.new||0:0)+(c?c.left||0:0):null;
    const v={crawl:p.crawl?`${num(p.crawl[0])} → ${num(p.crawl[1])}`:'',export:p.export_gone?'舊導出資料夾已清掉':p.export!=null?`${num(p.export)} 筆`:'',totext:p.text!=null?`${num(p.text)} 個`:'',
      build:p.docs!=null?`${num(p.text)} → ${num(p.docs)}`:'',swap:p.docs!=null?`${num(prev)} → ${num(p.docs)}`:''}[s];
    el.textContent=v||el.dataset.all;el.classList.toggle('nm-one',!!v)})}
function kpi(k){const box=$('ntKpis');if(!box||!G)return;const p=k&&PS()[k],c=ch[k];
  if(!p||p.docs==null||!G.n.started){if(box.classList.contains('nm-one')){box.innerHTML=kpiAll;box.classList.remove('nm-one')}return}
  box.innerHTML=`<div class='kp-docs' data-tip='這個網站在庫裡的文件；+ 新進、− 離開'><span>文件</span><b>${num(p.docs)}</b><span class='kp-pm'><i class='in'>+${num(c?c.new||0:0)}</i><i class='out'>−${num(c?c.left||0:0)}</i></span></div>`;box.classList.add('nm-one')}
function probsOf(k){const box=$q('#nightBand .nt-probs');if(!box||!cur)return;const lis=[...box.querySelectorAll('#ntProbs>li')];let hit=0;const nm=(PS()[k]||{}).name||(A.byKey[k]||{}).n;
  lis.forEach((li,i)=>{const p=cur.probs[i];const yes=!k||(p&&(p.sites||[]).includes(k))||forms(k).some(f=>li.textContent.includes(f))||(nm&&li.textContent.includes(nm));li.hidden=!yes;if(yes)hit++});
  let n=box.querySelector('.nm-none');if(!n){n=document.createElement('p');n.className='nm-none';box.append(n)}n.hidden=!(k&&!hit&&cur.probs.length);n.textContent='這一晚的問題沒有點名這個網站'}
function chip(k){let c=$('ntSite');const left=$q('#nightBand .nt-left');if(!left)return;
  if(!k){if(c)c.remove();return}if(!c){c=document.createElement('span');c.id='ntSite';c.className='nt-site';left.append(c)}
  const has=!!PS()[k],name=(PS()[k]&&PS()[k].name)||(A.byKey[k]||{}).n||k;
  c.innerHTML=`<span data-tip='${has?'上面的數字、問題、時間條都只算這個網站':'這一晚沒留下逐站的數字：只有問題和時間條跟著這個網站'}'>網站：<b>${esc(name)}</b></span><button type='button' aria-label='看全部網站' data-tip='看全部網站'>✕</button>`;
  c.querySelector('button').onclick=()=>{A.S.site=null;A.render()}}
// the open step's line for the site (owner 11-23: 「字太多了」): only that step's numbers, drawn as the band draws them
function stepLine(k){const box=$('ntStep');if(!box||box.hidden)return;box.querySelector('.nm-step')?.remove();const p=k&&PS()[k];if(!p)return;
  const sk=openStep||'crawl',c=ch[k],raw=!!(G&&G.latest);
  const pass=(a,b,w)=>`<span class='nm-io'><b>${num(a)}</b><span class='nm-pb' data-tip='${w}：${num(b)} ÷ ${num(a)}'><i style='width:${a?Math.min(100,b/a*100):0}%'></i><em>${a?(b/a*100).toFixed(1):0}%</em></span><b>${num(b)}</b></span>`;
  // a step the site has no number of this night (a night with a coverage line and no swap: no 換庫, 建庫 …) says so,
  // never NaN or a 0 (G2)
  const none=`<span class='nm-io'><small>這個網站這一步的數字沒留下</small></span>`;
  let body='';
  if(sk==='crawl'){if(!p.crawl)body=none;else{const L=p.lost||[],T=L.reduce((s,x)=>s+x[2],0);
    body=pass(p.crawl[0],p.crawl[1],'抓到 ÷ 該抓')+(T?`<span class='nm-lost' data-tip='未取得 ${num(T)}'>${L.map(([b,w,n])=>`<i style='flex-grow:${n};background:${LC[b]||'var(--ink3)'}' data-tip-h='${esc(w)}' data-tip='${num(n)} 個'></i>`).join('')}</span><small>未取得 ${num(T)}</small>`:'')}}
  else if(sk==='export')body=p.export_gone?`<span class='nm-io'><small>舊導出資料夾已清掉</small></span>`:p.export!=null?`<span class='nm-io'><b>${num(p.export)}</b><small>筆</small></span>`:none;
  else if(sk==='totext')body=p.text!=null?`<span class='nm-io'><b>${num(p.text)}</b><small>個轉成文字</small></span>`:none;
  else if(sk==='build')body=p.text!=null&&p.docs!=null?pass(p.text,p.docs,'文件 ÷ 轉成文字'):p.docs!=null?`<span class='nm-io'><b>${num(p.docs)}</b><small>份文件</small></span>`:none;
  else if(sk==='swap'&&p.docs==null)body=none;
  else if(sk==='swap'){const prev=p.docs_before!=null?p.docs_before:p.docs-(c?c.new||0:0)+(c?c.left||0:0);body=`<span class='nm-io'><b>${num(prev)}</b><span class='nm-arrow'>→</span><b>${num(p.docs)}</b></span><span class='nm-pm'><i class='in'>+${num(c?c.new||0:0)}</i><i class='mid'>改 ${c&&c.changed!=null?num(c.changed):'—'}</i><i class='out'>−${num(c?c.left||0:0)}</i></span>`}
  const d=document.createElement('div');d.className='nm-step';
  d.innerHTML=`<b class='nm-nm'>${esc(p.name)}</b>${body}<span class='nm-all' data-tip='下面的檔案、通過率和未取得是全部網站的'>全部網站 ↓</span>`;box.prepend(d);
  if(!raw)return;  // the records behind a mark are the lab's as they are now: only on the night the steps were read for
  // every mark opens the records behind it, already filtered (owner 11-23: 「我看到了有問題，但我看不到細節」)
  const go=pre=>{if(window.RAWSHOW)RAWSHOW(sk,Object.assign({site:k},pre))};
  d.querySelectorAll('.nm-pb').forEach(x=>{x.classList.add('nm-go');x.dataset.tip+='；點開看沒通過的每一筆';x.onclick=()=>go({miss:1})});
  d.querySelectorAll('.nm-lost i').forEach((x,i)=>{const b=p.lost[i]&&p.lost[i][0];x.classList.add('nm-go');x.dataset.tip+='；點開看這些網址';x.onclick=e=>{e.stopPropagation();go({bucket:b})}});
  d.querySelectorAll('.nm-lost+small').forEach(x=>{x.classList.add('nm-go');x.onclick=()=>go({miss:1})});
  d.querySelectorAll('.nm-io b').forEach((x,i)=>{x.classList.add('nm-go');x.dataset.tip=i?'點開看通過的每一筆':'點開看這個網站的每一筆';x.onclick=()=>go(i?(OUT[sk]?{label:OUT[sk]}:{}):{})})}
// the whole night's 通過率 bar and its causes open the records too; with a site picked, only that site's
// a site's addresses by the drawer's labels (raw.page: labels [[key, word, n, only]]: the key a bucket or
// review:<result code>, the word names.py's), by key and by word; null while the drawer's index is being read (asked
// again at the next pick, never kept). Asked only on the latest night (wireAll: when the night's coverage line gives
// no counts of the site) and once per pick of the site on that night: every redraw of the pick (the poll, a click, a
// resize, the app's render) waits on that one answer, so the drawer's index is never asked for on a timer
function siteLabels(k){if(!G||!G.latest)return Promise.resolve(null);const memo=G.run+'|'+k;
  if(rawCounts.has(memo))return Promise.resolve(rawCounts.get(memo));if(labelsFor===memo)return labelsAsk;
  labelsFor=memo;labelsAsk=(async()=>{try{const j=await getJSON(`/api/raw?step=crawl&site=${encodeURIComponent(k)}&per=0`);if(j.state!=='ready')return null;
    const out={key:{},word:{}};(j.labels||[]).forEach(x=>{if(!Array.isArray(x)||x.length<3)return;const n=Number(x[2])||0;
      out.key[x[0]]=(out.key[x[0]]||0)+n;out.word[x[1]]=(out.word[x[1]]||0)+n});
    rawCounts.set(memo,out);return out}catch(_){return null}})();
  return labelsAsk}
// a lost row's count for the site: by its key (data-key) when the counts know it, else by its words
const lostN=(li,cnt)=>{const key=li.dataset.key||'',w=(li.querySelector('span')||{}).textContent||'';return Number(key&&key in cnt.key?cnt.key[key]:cnt.word[w]||0)};
function recount(box,cnt){const lis=[...box.querySelectorAll('.sd-lost li')],ns=lis.map(li=>lostN(li,cnt)),mx=Math.max(1,...ns);
  lis.forEach((li,i)=>{const n=ns[i],nb=li.querySelector('b'),bar=li.querySelector('i>em');
    if(nb){if(nb.dataset.all===undefined)nb.dataset.all=nb.textContent;nb.textContent=n.toLocaleString()}li.classList.toggle('nm-z',!n);
    if(bar){if(bar.dataset.all===undefined)bar.dataset.all=bar.style.width;bar.style.width=(n?Math.max(1.5,n/mx*100):0)+'%'}})}
// the whole night's counts back (a site unpicked, or another picked before its own counts come; G3)
function restore(box){box.querySelectorAll('.sd-lost li').forEach(li=>{const nb=li.querySelector('b'),bar=li.querySelector('i>em');
  if(nb&&nb.dataset.all!==undefined)nb.textContent=nb.dataset.all;if(bar&&bar.dataset.all!==undefined)bar.style.width=bar.dataset.all;li.classList.remove('nm-z')})}
function wireAll(){const box=$('ntStep');if(!box||box.hidden||!openStep)return;const sk=openStep,k=A.S.site||null,raw=!!(G&&G.latest);
  const bar=box.querySelector('.sd-io .io-bar');if(raw&&bar&&!bar.dataset.go){bar.dataset.go='1';bar.classList.add('nm-go');bar.dataset.tip='輸出 ÷ 輸入；點開看沒通過的每一筆';
    bar.onclick=()=>{if(window.RAWSHOW)RAWSHOW(sk,Object.assign({miss:1},A.S.site?{site:A.S.site}:{}))}}
  // a cause's row opens its records under the site picked when it is clicked (none picked by then: every site's)
  if(raw&&k)box.querySelectorAll('.sd-lost li').forEach(li=>{const lab=(li.querySelector('span')||{}).textContent;if(!lab)return;
    li.onclick=()=>{const s=A.S.site;if(window.RAWSHOW)RAWSHOW(sk,Object.assign({label:lab},li.dataset.key?{key:li.dataset.key}:{},s?{site:s}:{}))}});
  // with a site picked, the causes list counts that site's addresses (from the night's coverage line, else the drawer's)
  if(sk!=='crawl')return;restore(box);if(!k)return;const p=PS()[k];
  if(p&&p.labels){recount(box,{key:{},word:p.labels});return}
  siteLabels(k).then(cnt=>{if(cnt&&A.S.site===k&&openStep==='crawl'&&$('ntStep')===box)recount(box,cnt)})}
// the night's 紀錄 follows too: its search box gets the site's address (cleared again with the pick)
let lastK=null;function logFollow(k){if(k===lastK)return;const box=$('lv-q');if(!box)return;if(k||box.value===lastK){box.value=k||'';q=box.value.trim();if(G)lvLoad()}lastK=k}
function siteGantt(){const k=A.S.site||null;io(k);ticks(k);stepLine(k);wireAll()}
// a new pick (another site, none, another night) asks for its moments, and the drawer's counts may be asked once more;
// the app's redraws of the same pick (every render, a resize) ask for nothing (the time line keeps what it drew)
function focus(){if(!on||!G)return;const k=A.S.site||null,m=G.run+'|'+(k||'');chip(k);io(k);kpi(k);probsOf(k);
  if(m!==pickKey){pickKey=m;if(labelsFor!==m)labelsFor=null;ticks(k)}stepLine(k);wireAll();logFollow(k)}

window.NM={apply,tile,after,set,items,sum,tip,focus,get on(){return on},get night(){return G?G.run:null}};
// a link with ?date= opens the night (總覽's calendar, the old /tonight); &step= opens that step's detail
const qs=new URLSearchParams(location.search),d0=qs.get('date');
if(d0!==null&&qs.get('embed')!=='1')set(true,d0,qs.get('step'));
}
})();
