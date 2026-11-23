// The floating word read for the eye (owner 11-23: 「很多悬浮窗的文字都太杂了」): a line listing 「name number、name number」
// or 「name：number」 becomes aligned rows (the number right, a bracketed aside left out of the name); any other line stays a
// short paragraph. The head (data-tip-h) on top. Used by 內容's own tooltip and by the shared one of the other pages.
// Ported from the design copy (research/design_2026-11-23/scripts/richtip.js) as it is: escaped text only.
window.RICHTIP=(el)=>{
  const E=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const h=el.getAttribute('data-tip-h'),t=el.getAttribute('data-tip')||'';
  const N="([+−\\-]?[\\d,]+(?:\\.\\d+)?\\s*(?:%|個|份|筆|站|頁|項)?)";
  const PART=new RegExp("^(.{1,40}?)\\s*[：:]?\\s*"+N+"$"),LINE=new RegExp("^(.{1,48}?)[：:]\\s*"+N+"$");
  let sub=false;const row=(a,b)=>`<div class='rt-r${sub?' rt-sub':''}'><span>${E(a.replace(/（[^）]*）/g,'').replace(/\s+$/,''))}</span><b>${E(b)}</b></div>`;
  const out=[];
  for(const line of t.split('\n')){const L=line.trim();if(!L)continue;sub=/^[　\s]/.test(line);
    const parts=L.split(/[、；]\s*/).filter(Boolean);
    if(parts.length>1&&parts.every(p=>{const m=PART.exec(p);return m&&/[^\d\s]/.test(m[1])})){parts.forEach(p=>{const m=PART.exec(p);out.push(row(m[1],m[2]))});continue}
    const m=LINE.exec(L);
    if(m){out.push(row(m[1],m[2]));continue}
    out.push(`<p class='rt-p'>${E(L.length>90?L.slice(0,90)+'…':L)}</p>`)}
  return (h?`<b class='rt-h'>${E(h)}</b>`:'')+out.join('')};
