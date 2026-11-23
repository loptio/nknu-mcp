(()=>{document.querySelectorAll('ul.tq').forEach(ul=>{ul.classList.add('pd-grid');ul.querySelectorAll('li>a').forEach(a=>{
  const id=(a.querySelector('.tq-id')||{}).textContent||'',marks=[...a.querySelectorAll('.sd-m')];const q=[...a.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();
  a.dataset.tipH=id;a.dataset.tip=q+(marks.length?'\n'+marks.map(m=>m.dataset.tip||m.textContent).join('\n'):'');
  a.innerHTML=`<span class='pd-qh'><b>${id}</b></span><span class='pd-qt'></span>`;a.querySelector('.pd-qt').textContent=q;marks.forEach(m=>{m.removeAttribute('data-tip');a.querySelector('.pd-qh').append(m)})})})})();
