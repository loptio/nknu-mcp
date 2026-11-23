(()=>{const q=new URLSearchParams(location.search),ck=q.get('ck'),box=document.getElementById('quality');if(!box)return;const pick=k=>{box.querySelectorAll('.sq-tabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.k===k)));box.querySelectorAll('.sq-p').forEach(p=>p.hidden=p.dataset.k!==k)};box.querySelectorAll('.sq-tabs button').forEach(b=>b.onclick=()=>pick(b.dataset.k));const N={"none": "無文件", "self": "標題搜不到", "empty": "網頁無文字", "no_date": "無日期", "same_title": "標題重複", "same_opening": "選單混入正文", "fragments": "附件逐字斷行", "spike": "檔案時間雷同"},known=ck&&Object.prototype.hasOwnProperty.call(N,ck);if(known&&box.querySelector(`.sq-p[data-k='${ck}']`)){pick(ck);box.scrollIntoView({block:'start'})}const back=document.getElementById('sqBack');if(back&&known){back.href='/sites?ck='+encodeURIComponent(ck);back.textContent='‹ 內容 · ';const b=document.createElement('b');b.textContent=N[ck];back.append(b)}})();
// 品質提醒: a list longer than the card draws has 「再列 N 份」 under it, which asks /site/quality for the next part (rows
// as the card draws them, escaped on the server) and adds them; the note says how many are drawn of how many
(()=>{const box=document.getElementById('quality');if(!box)return;
  box.addEventListener('click',ev=>{const b=ev.target.closest&&ev.target.closest('.sq-more-b');if(!b||b.disabled)return;
    const list=b.closest('.sq-list'),tb=list&&list.querySelector('tbody');if(!tb)return;const at=+b.dataset.at||0,of=+b.dataset.of||0;
    const site=box.dataset.site||new URLSearchParams(location.search).get('site')||'';b.disabled=true;
    fetch('/site/quality?'+new URLSearchParams({site,k:b.dataset.k||'',g:b.dataset.g||'0',at:String(at)}),{cache:'no-store'})
      .then(r=>r.ok?r.json():null).catch(()=>null).then(j=>{b.disabled=false;
        if(!j||typeof j.rows!=='string'||!(j.at>at)){b.textContent='讀不到，再試一次';return}
        tb.insertAdjacentHTML('beforeend',j.rows);const now=j.at,step=now-at;b.dataset.at=String(now);
        const note=list.querySelector('.sq-more'),cut=note&&note.querySelector('.sq-at'),n=note?+note.dataset.n||of:of;
        if(cut)cut.textContent=now<n?`，這裡列前 ${now.toLocaleString()} 份`:'';
        if(now>=of)b.remove();else b.textContent=`再列 ${Math.min(step,of-now).toLocaleString()} 份`})})})();
(()=>{const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const EXT=`<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M6.5 3.5H12.5V9.5M12.5 3.5 4 12'/></svg>`,DBI=`<svg viewBox='0 0 16 16' width='13' height='13' fill='none' stroke='currentColor' stroke-width='1.4' aria-hidden='true'><ellipse cx='8' cy='3.8' rx='5' ry='1.8'/><path d='M3 3.8v8.4c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V3.8M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8'/></svg>`,q=new URLSearchParams(location.search);
const st=$('#now .v3-state'),say=$('#now .st-say');if(st&&say){st.dataset.tip=say.textContent.trim();say.remove()}
const fr=$('#now .hm-fresh');if(fr){const nums=[...fr.querySelectorAll('b.num')].map(b=>b.textContent),a=fr.querySelector('a[target=_blank]'),code=(fr.querySelector('.mono')||{}).textContent||'';
  fr.innerHTML=`<span><b class='num'>${nums[0]||''}</b> 份文件</span><span class='hm-sep'>·</span><span data-tip='庫裡這個網站最新一份文件的日期'>資料截至 <b class='num'>${(nums[1]||'').slice(5).replace('-','/')}</b></span>`
   +(a?`<span class='hm-sep'>·</span><a class='sd-addr' href='${a.href}' target='_blank' rel='noopener' data-tip='打開學校的這個網站'>${code}${EXT}</a>`:'')}
const main=$('#now .v3-main');
const docs=$('#docs');if(docs&&main){const kb=docs.querySelector('.st-kbar'),lg=docs.querySelector('.st-legend'),ln=[...docs.querySelectorAll('.st-links a')];
  const box=document.createElement('div');box.className='sd-kinds';if(kb)box.append(kb);if(lg)box.append(lg);
  if(ln.length){const w=document.createElement('span');w.className='sd-db';w.innerHTML=`<i data-tip='資料庫裡看'>${DBI}</i>`;
    ln.forEach(x=>{x.className='sd-dbl';x.querySelector('svg')?.remove();x.textContent=({'沒日期的原因':'沒日期原因','各類型份數':'各類型'})[x.textContent.trim()]||x.textContent.trim();w.append(x)});box.append(w)}
  main.append(box);docs.remove()}
const ch=$('#changes');if(ch&&main){const when=((ch.querySelector('.faint')||{}).textContent||'').trim();let el;
  const figs=[...ch.querySelectorAll('.tn-fig')].map(f=>[f.querySelector('span').textContent,f.querySelector('b').textContent]);
  if(!figs.length){el=document.createElement('p');el.className='sd-chg0';el.innerHTML=`<span data-tip='${when}'>上次換庫：沒有變動</span>`}
  else{el=document.createElement('details');el.className='sd-chg';
    el.innerHTML=`<summary data-tip='${when}'>上次換庫 ${figs.map(([k,v])=>`<span class='${v==='0'?'z':''}'>${k} <b>${v}</b></span>`).join('')}</summary>`;
    const lst=ch.querySelector('.tn-in');if(lst)el.append(lst)}
  main.append(el);ch.remove()}
$$('.hm-panel').forEach(p=>{const pill=p.querySelector('header .pill.good');if(pill)pill.remove();p.querySelector('.hm-split')?.remove();
  const io=p.querySelector('dl.hm-io');if(!io)return;const dd=[...io.querySelectorAll('dd')];if(dd.length!==2)return;
  const v=dd.map(d=>{const b=d.querySelector('b');return [b?b.textContent:'',d.textContent.replace(b?b.textContent:'','').trim()]});
  const a=+v[0][0].replace(/,/g,''),b=+v[1][0].replace(/,/g,''),r=a?b/a*100:0;
  const div=document.createElement('div');div.className='sd-io';
  div.innerHTML=`<div class='sd-n'><b>${v[0][0]}</b><small>${v[0][1]}</small></div><div class='sd-bar' data-tip='輸出 ÷ 輸入'><i style='width:${Math.min(100,r).toFixed(1)}%'></i><span>通過率 <b>${r.toFixed(1)}%</b></span></div><div class='sd-n r'><b>${v[1][0]}</b><small>${v[1][1]}</small></div>`;
  io.replaceWith(div)});
// 網頁核查 keeps its counts only (owner 11-23, the cut list): the lists of addresses and of pages live on the check
// page, filtered to this site (/check?site=…&tab=… / &only=…); the card sits beside 品質提醒
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
const ck=$('#check'),cs=$('#chk-site');if(ck&&cs){const site=cs.dataset.site||q.get('site')||'',u=k=>'/check?lists=1&site='+encodeURIComponent(site)+k;
  // 「已核查 N 頁 · 11-23 05:57 核查完」 → 「N 頁 ⏱ 11-23 05:57」, the words on hover
  const line=cs.querySelector('.sv-line'),lc=line?[...line.children]:[];const pg=lc[0]?lc[0].textContent.replace('已核查','').trim():'',tm=lc[1]?lc[1].textContent.replace('核查完','').trim():'';
  const when=(pg?`<span data-tip='${esc(lc[0].dataset.tip)}'>${esc(pg)}</span>`:'')+(tm&&window.kclock?' '+kclock(esc(tm),esc('核查完一遍的時間。'+(lc[1].dataset.tip||''))):'');
  const figs=[...cs.querySelectorAll('.ck-fig')].map(f=>({k:f.dataset.k,n:(f.querySelector('.ck-w')||{}).textContent||'',v:(f.querySelector('.ck-v b')||{}).textContent||'0',
    unit:(f.querySelector('.ck-v small')||{}).textContent||'',s:(f.querySelector('.ck-s')||{}).textContent||'',tip:f.dataset.tip||''}));
  const chips=[...cs.querySelectorAll('#chk-chips .chip')].filter(c=>!c.classList.contains('z')).map(c=>({k:c.dataset.only,n:c.childNodes[0].textContent.trim(),v:(c.querySelector('b')||{}).textContent||'',tip:c.dataset.tip||''}));
  const total=(cs.querySelector('.pl-head h2 .faint')||{}).textContent||'';
  const row=(href,n,v,tip,sub,z)=>`<li class='${z?'z':''}'><a href='${href}' data-tip-h='${esc(n)}' data-tip='${esc(tip)}'><span>${esc(n)}${sub?`<small>${esc(sub)}</small>`:''}</span><b>${esc(v)}</b></a></li>`;
  const sec=document.createElement('section');sec.className='v3-q sd-ck';sec.id='ckSum';sec.setAttribute('aria-label','網頁核查');
  sec.innerHTML=`<div class='v3-h'><h2>網頁核查</h2><span class='sd-ckwhen'>${when}</span></div><div class='card hm-card'><div class='hm-pad'>`
    // 沒抓到的 as one split bar (爬蟲能補 / 要確認 are ours: warn; 學校那邊 grey; 沒抓下來 bad), its legend under it; a
    // segment or a legend entry opens that kind on the check page
    +(()=>{const COL={fix:'var(--warn-s)',check:'color-mix(in srgb,var(--warn-s) 55%,transparent)',school:'var(--ink3)',nofetch:'var(--bad-s,#e5484d)'};
      const go=f=>f.k==='nofetch'?u('&only=nofetch'):u('&tab='+f.k),num=f=>+String(f.v).replace(/,/g,'')||0,tot=figs.reduce((a,f)=>a+num(f),0);
      const bar=tot?`<div class='sd-split'>${figs.filter(f=>num(f)).map(f=>`<a href='${go(f)}' style='flex-grow:${num(f)};background:${COL[f.k]||'var(--ink3)'}' data-tip-h='${esc(f.n)} ${esc(f.v)}' data-tip='${esc((f.s?f.s+'。':'')+f.tip)}'></a>`).join('')}</div>`:`<p class='sd-none'>都抓到了</p>`;
      return `<h5>沒抓到的<small>${tot.toLocaleString()} 個</small></h5>${bar}<ul class='sd-leg'>${figs.map(f=>`<li class='${num(f)?'':'z'}'><a href='${go(f)}' data-tip-h='${esc(f.n)}' data-tip='${esc((f.s?f.s+'。':'')+f.tip)}'><i style='background:${COL[f.k]||'var(--ink3)'}'></i>${esc(f.n)}<b>${esc(f.v)}</b></a></li>`).join('')}</ul>`})()
    +(chips.length?`<h5>有問題的網頁<small>共 ${esc(total)} 頁</small></h5><ul class='sd-ckl'>${chips.map(c=>row(u('&only='+c.k),c.n,c.v,c.tip,'',false)).join('')}</ul>`:'')
    +`<a class='sd-all' href='${u('')}'>看這個網站的全部核查 ›</a></div></div>`;
  const grid=document.createElement('div');grid.className='sd-grid';const qy=$('#quality');
  if(qy){qy.before(grid);grid.append(qy,sec)}else{ck.before(sec)}
  const hm=$('.hm.st');if(hm&&grid.parentNode)grid.before(hm);  // 流程 under the head, then 品質提醒 | 網頁核查
  ck.remove();cs.remove()}
// the head's kinds as 總覽's 全部網站 card draws them (owner 11-23: 「这俩个卡片的可视化可以再优化，不是我喜欢的风格」): a donut
// with the total inside and a plain legend beside it; the 「N 份文件」 words go (the donut says it)
const kd=$('.sd-kinds');if(kd){const items=[...kd.querySelectorAll('.st-legend li')].map(li=>({n:li.querySelector('span').textContent,v:+li.querySelector('b').textContent.replace(/,/g,''),
    c:li.querySelector('i').style.background,tip:li.dataset.tip||''}));const tot=items.reduce((a,x)=>a+x.v,0);
  const R=38,C=2*Math.PI*R;let off=0;const segs=items.map(x=>{const len=tot?x.v/tot*C:0,g=items.length>1?Math.min(2,len/3):0;
    const s=`<circle r='${R}' cx='50' cy='50' fill='none' stroke='${x.c}' stroke-width='12' stroke-dasharray='${Math.max(0,len-g).toFixed(2)} ${C.toFixed(2)}' stroke-dashoffset='${(-off).toFixed(2)}' data-tip-h='${esc(x.n)}' data-tip='${esc(x.tip)}'/>`;off+=len;return s}).join('');
  const db=kd.querySelector('.sd-db');
  const box=document.createElement('div');box.className='sd-kd';
  box.innerHTML=`<svg viewBox='0 0 100 100' width='108' height='108' role='img' aria-label='各類型文件，共 ${tot.toLocaleString()} 份'><g transform='rotate(-90 50 50)'>${segs}</g>`
    +`<text x='50' y='50' class='t'>${tot.toLocaleString()}</text><text x='50' y='64' class='u'>份文件</text></svg>`
    +`<ul>${items.map(x=>`<li data-tip-h='${esc(x.n)}' data-tip='${esc(x.tip)}'><i style='background:${x.c}'></i><span>${esc(x.n)}</span><b>${x.v.toLocaleString()}</b></li>`).join('')}</ul>`;
  $('#now .hm-now').append(box);if(db)$('#now .v3-main').append(db);kd.remove();
  const fr2=$('#now .hm-fresh');if(fr2){const f=fr2.firstElementChild;if(f&&/份文件/.test(f.textContent)){f.nextElementSibling?.remove();f.remove()}}}
// 流程 as rings (總覽's dials): each step's 通過率 (out ÷ in) as a ring in its state's colour, the number it put out beside
// it, what came in under; violet arrows between the steps (in → out)
const TONE={ok:'var(--good-s)',warn:'var(--warn-s)',bad:'var(--bad-s, var(--warn-s))'};
$$('.hm-node').forEach(li=>{const a=li.querySelector('a');if(!a)return;const g=s=>(a.querySelector(s)||{}).textContent||'';
  const name=g('.hm-nm b'),fig=g('.hm-fig'),unit=g('.hm-unit'),line=g('.hm-line').trim();const m=line.match(/^(該抓|清單|進)\s*([\d,]+)/);
  const out=+fig.replace(/,/g,''),inn=m?+m[2].replace(/,/g,''):0,p=inn?Math.min(100,out/inn*100):100,col=TONE[li.dataset.tone]||TONE.ok;
  const R=19,C=2*Math.PI*R;
  a.innerHTML=`<svg class='sd-ring' viewBox='0 0 48 48' width='48' height='48' data-tip='${m?'通過率：'+fig+' ÷ '+m[2]:esc(line)}'><circle cx='24' cy='24' r='${R}' class='trk'/>`
    +`<circle cx='24' cy='24' r='${R}' class='arc' stroke='${col}' stroke-dasharray='${(p/100*C).toFixed(2)} ${C.toFixed(2)}' transform='rotate(-90 24 24)'/>`
    +`<text x='24' y='27.5'>${m?(p>=99.95?'100':p.toFixed(p<10?1:0))+'%':'✓'}</text></svg>`
    +`<span class='sd-st'><b>${esc(name)}</b><span class='sd-fig'>${esc(fig)}<small>${esc(unit.replace(/^(個|筆|份)/,''))}</small></span><small class='sd-in'>${esc(line)}</small></span>`});
// 流程: the tiles only; a step's panel opens under them on a click and closes on a second (as 定時更新's Gantt does)
const hm=$('.hm.st');if(hm){let cur=null;hm.addEventListener('click',e=>{const a=e.target.closest('[data-pick]');if(!a)return;
  const k=a.dataset.pick;const open=!(cur===k&&hm.classList.contains('sd-open'));cur=k;hm.classList.toggle('sd-open',open);
  $$('.hm-node').forEach(n=>n.classList.toggle('sd-sel',open&&n.contains(a)))})}
})();
