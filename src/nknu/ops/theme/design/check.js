(()=>{const LEG={};document.querySelectorAll('.sv-key span').forEach(s=>{const i=s.querySelector('.sw');if(i)LEG[i.dataset.b]=s.textContent.trim()});
window.prettyRows=root=>{root.querySelectorAll('.prob').forEach(a=>{if(a.dataset.pr)return;a.dataset.pr='1';const sub=a.querySelector('.sub');if(!sub)return;
  const parts=sub.textContent.split(' · ').map(x=>x.trim()).filter(Boolean);if(!parts.length)return;let ic='';
  if(window.kic&&window.KIC&&KIC.W[parts[0]]){ic=kic(parts[0],13)||'';parts.shift()}const why=parts.length?parts.pop():'';
  sub.innerHTML=ic+(parts.length?`<span class='pr-u'>${parts.join(' · ')}</span>`:'')+(why?`<em class='pr-why'>${why}</em>`:'')});
  root.querySelectorAll('.sw').forEach(i=>{if(LEG[i.dataset.b]&&!i.dataset.tip)i.dataset.tip=LEG[i.dataset.b]})};
prettyRows(document)})();
(()=>{const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const q=new URLSearchParams(location.search),site=q.get('site'),F={};
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
const top=$('.dc-school')||$('main');
// the head line: when it was checked, what had no tile of its own; the four tiles go
const sv=$('.sv');const line=document.createElement('p');line.className='pd-line';
// 「已核查 N 頁」 → 「N 頁」, 「… 核查完」 → a clock with the time; the words on hover
const stamp=x=>/核查完/.test(x.textContent)&&window.kclock?kclock(esc(x.textContent.replace('核查完','').trim()),esc('核查完一遍的時間。'+(x.dataset.tip||''))):`<span data-tip='${esc(x.dataset.tip)}'>${esc(x.textContent.replace('已核查','').trim())}</span>`;
const head=sv&&sv.querySelector('.sv-line');let bits=head?[...head.children].map(stamp):[];
const nf=sv&&sv.querySelector(".ck-fig[data-k='nofetch']");if(nf)bits.push(`<span data-tip='${esc(nf.dataset.tip)}'>沒抓下來 <b>${esc((nf.querySelector('.ck-v b')||{}).textContent)}</b> 頁</span>`);
const notes=$('.ck-notes');if(notes)bits.push(...[...notes.children].map(x=>`<span data-tip='${esc(x.dataset.tip)}'>${x.innerHTML}</span>`));
line.innerHTML=bits.join('');
// each tab carries its own nights (the bars of 最近 14 晚)
const trs=$$('.ck-tr');const order=['fix','check','school'];
$$('.ck-tabbar button').forEach(b=>{if(b.querySelector('b.z'))b.classList.add('pd-zero');const tr=trs[order.indexOf(b.dataset.tab)];if(!tr)return;
  const bars=[...tr.querySelectorAll('.ck-bars i, .ck-bars span, .ck-bars b')].filter(x=>x.style.height);if(bars.length<3)return;
  const sp=document.createElement('span');sp.className='pd-spark';sp.dataset.tip=(tr.querySelector('.ck-bars')||{}).getAttribute?.('aria-label')||'';
  sp.innerHTML=bars.map(x=>`<i style='height:${x.style.height}'></i>`).join('');b.append(sp)});
if(sv){sv.before(line);sv.remove()}const trend=$('.ck-trend');if(trend)(trend.closest('details')||trend).remove();
$('.ck-tr-wrap')?.closest('details')?.remove();
// tabs that work on whatever is in the page (the site's lists are put in after load)
const tabs=box=>{box.querySelectorAll('.ck-tabbar button').forEach(b=>b.onclick=()=>{box.querySelectorAll('.ck-tabbar button').forEach(x=>x.setAttribute('aria-selected',String(x===b)));
  box.querySelectorAll('.ck-pane').forEach(p=>p.hidden=p.dataset.tab!==b.dataset.tab)})};
const pick=(box,t)=>{const b=t&&box.querySelector(`.ck-tabbar button[data-tab='${t}']`)||box.querySelector('.ck-tabbar button:not(.pd-zero)');if(b)b.click()};
const ONLY={gap:['正文區有漏'],menu:['只漏選單'],check:['要查','內容可疑','核查出錯'],nofetch:['這一頁沒抓下來','失敗']};
const pages=pl=>{if(!pl)return;let only=q.get('only')||'',words='';const inp=pl.querySelector('input');
  // a row's state: its hover's head, else its pill (a page that failed has only the pill)
  const stOf=a=>a?(a.dataset.tipH||(a.querySelector('.pill')||{}).textContent||'').trim():'';
  const note=document.createElement('p');note.className='pd-cut';pl.querySelector('#chk-chips')?.closest('.pl-head')?.after(note);
  // 要查 also takes a page whose own state is another but which holds addresses to look at (its hover: 「要查 N 個」), as the
  // panel's own count does
  const hit=(a,k)=>{const st=stOf(a);return (ONLY[k]||[]).includes(st)||(k==='check'&&/要查 \d/.test(a&&a.dataset.tip||''))};
  const run=()=>{let shown=0;pl.querySelectorAll('#chk-rows li').forEach(li=>{const a=li.querySelector('.prow');
      li.hidden=(only&&!hit(a,only))||(words&&!li.textContent.toLowerCase().includes(words));if(!li.hidden)shown++});
    pl.querySelectorAll('#chk-chips .chip').forEach(c=>c.setAttribute('aria-pressed',String(c.dataset.only===only)));
    // the copy carries the site page's first rows only; say so when a kind has more than are here (the panel pages through all)
    const c=only&&pl.querySelector(`#chk-chips .chip[data-only='${only}'] b`),n=c?+c.textContent.replace(/,/g,''):0;
    note.hidden=!(n>shown);note.textContent=n>shown?`這裡列出 ${shown.toLocaleString()} 頁，共 ${n.toLocaleString()} 頁：打字篩選看其他的`:''};
  pl.querySelectorAll('#chk-chips .chip').forEach(c=>c.onclick=e=>{e.preventDefault();only=only===c.dataset.only?'':c.dataset.only;run()});
  if(inp){inp.closest('form').onsubmit=e=>e.preventDefault();inp.oninput=()=>{words=inp.value.trim().toLowerCase();run()}}
  run();if(only)pl.scrollIntoView({block:'start'})};
const school=$('.ck-tabs');if(school)tabs(school);
if(!site){if(school)pick(school,q.get('tab'));return}
const chip=document.createElement('span');chip.className='pd-site';

fetch('/site?site='+encodeURIComponent(site)+(q.get('only')?'&only='+encodeURIComponent(q.get('only')):'')).then(r=>r.text()).then(t=>{const doc=new DOMParser().parseFromString(t,'text/html');const cs=doc.querySelector('#chk-site');if(!cs)return;
  const name=(doc.querySelector('#now h1')||{}).textContent||site;chip.innerHTML=`網站：<b>${esc(name)}</b><a href='/check' data-tip='看全校'>✕</a>`;line.prepend(chip);
  const sl=cs.querySelector('.sv-line');if(sl)[...line.children].forEach(x=>{if(x!==chip)x.remove()}),line.insertAdjacentHTML('beforeend',[...sl.children].map(stamp).join(''));
  const nt=cs.querySelector('.ck-tabs'),pl=cs.querySelector('.plist');
  if(nt&&school){nt.querySelectorAll('.ck-tabbar button').forEach(b=>{if(b.querySelector('b.z'))b.classList.add('pd-zero')});school.replaceWith(nt);tabs(nt);pick(nt,q.get('tab'));
    if(q.get('tab')){nt.classList.add('pd-hit');setTimeout(()=>nt.scrollIntoView({block:'start'}),0)}}
  if(pl){(nt||line).after(pl);pages(pl)}
  if(window.prettyRows)prettyRows(document);
  document.title=name+' · 網頁核查'})})();
