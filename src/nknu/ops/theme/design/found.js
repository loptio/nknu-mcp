(()=>{document.querySelectorAll('.prow').forEach(a=>{const pp=a.querySelector('.pp'),k=a.querySelector('.dc-k');if(!pp)return;
  const parts=pp.textContent.split(' · ');const i=parts.findIndex(x=>window.KIC&&KIC.W[x.trim()]);if(i<0)return;const w=parts[i].trim();parts.splice(i,1);
  pp.textContent=parts.join(' · ');if(k)k.dataset.tip=w})})();
