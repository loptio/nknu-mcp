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
const TABS=['fix','check','school'];  // only a known tab key goes into a selector (the address is never trusted)
const pick=(box,t)=>{const b=TABS.includes(t)&&box.querySelector(`.ck-tabbar button[data-tab='${t}']`)||box.querySelector('.ck-tabbar button:not(.pd-zero)');if(b)b.click()};
// a site's pages (port audit 11-23: 「要精准跳转…需要帮我输入好筛选好」): the server filters and parts them
// (/check/site?site=&only=&q=&offset=), so a number's link lands on exactly the pages it counts, all of them. A chip
// asks again, typing asks again (a moment after the last key), 「再列 N 頁」 asks for the next part; the chips' numbers
// come back with the rows (one answer, one count). The latest answer wins; ?only= and ?q= keep what is picked.
const KINDS=['gap','menu','check','nofetch'];
const pages=pl=>{if(!pl)return;const rows=pl.querySelector('#chk-rows'),chips=pl.querySelector('#chk-chips');if(!rows||!chips)return;
  const inp=pl.querySelector('#chk-filter'),form=inp&&inp.closest('form');let only=KINDS.includes(q.get('only'))?q.get('only'):'';
  if(inp&&q.get('q'))inp.value=q.get('q');
  const note=document.createElement('p');note.className='pd-cut';note.hidden=true;chips.closest('.pl-head')?.after(note);
  const more=document.createElement('button');more.type='button';more.className='pd-more';more.hidden=true;rows.after(more);
  let seq=0,timer=null,matched=null,paged=false,step=0;
  const listed=()=>rows.querySelectorAll('ol>li').length,words=()=>inp?inp.value.trim():'';
  const say=()=>{const n=listed(),m=matched==null?n:matched;
    chips.querySelectorAll('.chip').forEach(c=>c.setAttribute('aria-pressed',String(c.dataset.only===only)));
    // the server counts the pages this filter keeps (matched); when they are not all here, say so and offer the rest
    note.hidden=!(m>n);note.textContent=m>n?`列出 ${n.toLocaleString()} 頁，共 ${m.toLocaleString()} 頁`+(paged?'':'：打字找其他的'):'';
    more.hidden=!(paged&&m>n);more.textContent=`再列 ${Math.min(step||m-n,m-n).toLocaleString()} 頁`};
  const keep=()=>{const u=new URLSearchParams(location.search);if(only)u.set('only',only);else u.delete('only');
    if(words())u.set('q',words());else u.delete('q');history.replaceState(null,'','?'+u+location.hash)};
  const ask=add=>{const my=++seq,at=add?listed():0,p=new URLSearchParams({site});if(only)p.set('only',only);
    if(words())p.set('q',words());if(at)p.set('offset',String(at));more.disabled=true;
    fetch('/check/site?'+p,{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null).then(j=>{if(my!==seq)return;more.disabled=false;
      if(!j||typeof j.rows!=='string'||!j.rows){say();return}  // the sweep is not ready, or no answer: keep what is here
      const got=new DOMParser().parseFromString(`<div id='got'>${j.rows}</div>`,'text/html').getElementById('got');
      // 「還有 N 頁」 under the server's rows counts from its first page; the note here says it for the whole list
      const tail=got.querySelector('.more-n'),rest=tail?+((tail.textContent.match(/[\d,]+/)||['0'])[0].replace(/,/g,'')):0;if(tail)tail.remove();
      paged=typeof j.offset==='number'&&typeof j.matched==='number';
      if(add){const ol=rows.querySelector('ol');if(!paged||j.offset!==at||!ol){paged=false;say();return}
        got.querySelectorAll('ol>li').forEach(li=>ol.append(li))}
      else{rows.replaceChildren(...got.childNodes);if(typeof j.chips==='string'&&j.chips)chips.innerHTML=j.chips;
        step=listed()}
      matched=paged?j.matched:listed()+rest;say()})};
  chips.addEventListener('click',e=>{const c=e.target.closest('.chip[data-only]');if(!c)return;e.preventDefault();
    only=only===c.dataset.only?'':c.dataset.only;matched=null;say();ask(false);keep()});
  more.onclick=()=>ask(true);
  if(form)form.addEventListener('submit',e=>{e.preventDefault();clearTimeout(timer);ask(false);keep()});
  // while the words wait to be asked, 「再列」 would add the next part of the list they are about to replace
  if(inp)inp.addEventListener('input',()=>{clearTimeout(timer);more.disabled=true;timer=setTimeout(()=>{ask(false);keep()},180)});
  say();ask(false);if(only)pl.scrollIntoView({block:'start'})};
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
