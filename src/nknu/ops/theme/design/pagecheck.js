(()=>{const LEG={};document.querySelectorAll('.sv-key span').forEach(s=>{const i=s.querySelector('.sw');if(i)LEG[i.dataset.b]=s.textContent.trim()});
window.prettyRows=root=>{root.querySelectorAll('.prob').forEach(a=>{if(a.dataset.pr)return;a.dataset.pr='1';const sub=a.querySelector('.sub');if(!sub)return;
  const parts=sub.textContent.split(' · ').map(x=>x.trim()).filter(Boolean);if(!parts.length)return;let ic='';
  if(window.kic&&window.KIC&&KIC.W[parts[0]]){ic=kic(parts[0],13)||'';parts.shift()}const why=parts.length?parts.pop():'';
  sub.innerHTML=ic+(parts.length?`<span class='pr-u'>${parts.join(' · ')}</span>`:'')+(why?`<em class='pr-why'>${why}</em>`:'')});
  root.querySelectorAll('.sw').forEach(i=>{if(LEG[i.dataset.b]&&!i.dataset.tip)i.dataset.tip=LEG[i.dataset.b]})};
prettyRows(document)})();
(()=>{const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
const t=document.querySelector('table.legend2');if(!t)return;const cols=[...t.querySelectorAll('tr:first-child th')].slice(1).map(th=>th.textContent.trim());
const rows=[...t.querySelectorAll('tr')].slice(1).filter(r=>!r.classList.contains('sum')&&r.querySelector('.nm')).map(r=>{const nm=r.querySelector('.nm'),sw=r.querySelector('.sw');const cs=sw?getComputedStyle(sw):null;
  const col=cs?(cs.borderTopStyle!=='none'&&cs.borderTopWidth!=='0px'?cs.borderTopColor:cs.backgroundColor):'var(--ink3)';
  return {n:nm.textContent.trim(),tip:nm.dataset.tip||'',col,v:[...r.querySelectorAll('td.num')].map(td=>+td.textContent.replace(/[,—\s]/g,'')||0)}});
const box=document.createElement('div');box.className='pc-bars';
box.innerHTML=cols.map((c,j)=>{const tot=rows.reduce((a,r)=>a+(r.v[j]||0),0);
  return `<div class='pc-row ${tot?'':'z'}'><span class='pc-n'>${window.kic?kic(c,13)||'':''}${esc(c)}</span><div class='pc-bar'>${rows.filter(r=>r.v[j]).map(r=>`<i style='flex-grow:${r.v[j]};background:${r.col}' data-tip-h='${esc(r.n)} ${r.v[j]}' data-tip='${esc(r.tip)}'></i>`).join('')}</div><b>${tot}</b></div>`}).join('')
  +`<ul class='pc-leg'>${rows.map(r=>`<li data-tip-h='${esc(r.n)}' data-tip='${esc(r.tip)}'><i style='background:${r.col}'></i>${esc(r.n)}</li>`).join('')}</ul>`;
t.replaceWith(box);
const how=document.querySelector('.lg-how');if(how){const tip=[...how.children].map(x=>x.dataset.tip||x.textContent.trim()).join('。');const h2=box.closest('section')&&box.closest('section').querySelector('h2');
  if(h2)h2.insertAdjacentHTML('beforeend',`<button type='button' class='hp pc-hp' data-tip='${esc(tip)}'>?</button>`);how.remove()}})();
