(()=>{const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
const S=k=>window.KIC?KIC.svg(k,13):'';const I={ok:'M3.5 8.4l3 3 6-6.6',size:'M3 12.5h10M5 10V6M8 10V3.5M11 10V7.5',type:'M4 2h5l3 3v9H4zM9 2v3h3',text:'M3 4h10M3 7h10M3 10h7M3 13h5',read:'M2 8s2.5-4.5 6-4.5S14 8 14 8s-2.5 4.5-6 4.5S2 8 2 8zM8 6.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6z',unit:'M3 14V5l5-3 5 3v9M6 14v-4h4v4'};
const sv=k=>window.KIC&&KIC.P[k]?S(k):`<svg viewBox='0 0 16 16' width='13' height='13' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='${I[k]||''}'/></svg>`;
const chip=(ic,txt,h,tip,href,cls)=>`<${href?`a href='${esc(href)}'`:'span'} class='pd-c ${cls||''}' data-tip-h='${esc(h)}' data-tip='${esc(tip||'')}'>${sv(ic)}<span>${esc(txt)}</span></${href?'a':'span'}>`;
const facts=sec=>{const m={};sec.querySelectorAll('dl.facts>div').forEach(d=>{const t=(d.querySelector('dt')||{}).textContent;const dd=d.querySelector('dd');if(t&&dd)m[t.trim()]=dd});return m};
const tx=e=>e?e.textContent.replace(/\s+/g,' ').trim():'';
const put=(sec,html)=>{const row=document.createElement('div');row.className='pd-chips';row.innerHTML=html;const act=sec.querySelector('.in>.act');if(act)row.append(act);
  const first=sec.querySelector('.in>dl.facts')||sec.querySelector('.in>.soft');if(first)first.replaceWith(row);else sec.querySelector('.in').prepend(row);
  sec.querySelectorAll('.in>details.fold').forEach(f=>{if(/^更多/.test(tx(f.querySelector('summary'))))f.remove()})};
// ① 抓取
let s=document.getElementById('s1');if(s){const m=facts(s),from=m['連到它的網頁']?tx(m['連到它的網頁']):'',arc=m['存檔編號']?tx(m['存檔編號']):'';
  put(s,chip('ok',tx(m['結果']),'結果','','', /抓到|取得/.test(tx(m['結果']))?'ok':'')+(m['抓取時間']?chip('clock',tx(m['抓取時間']).slice(5),'抓取時間',tx(m['抓取時間'])):'')
    +(m['大小']?chip('size',tx(m['大小']),'大小',''):'')+(m['怎麼找到的']?chip('link',tx(m['怎麼找到的']),'怎麼找到的',from?'從這一頁：'+from:'',from?'/check?url='+encodeURIComponent(from):''):''));
  const b=s.querySelector('.act .btn');if(b&&arc)b.dataset.tip+='（存檔編號 '+arc+'）'}
// ② 匯出
s=document.getElementById('s2');if(s){const m=facts(s),site=m['網站'];const more=['檔案','連結文字','資料夾'].filter(k=>m[k]).map(k=>k+'：'+tx(m[k])).join('\n');
  put(s,(site?chip('unit',tx(site.querySelector('a')||site),'網站',tx(site.querySelector('small')),(site.querySelector('a')||{}).getAttribute?.('href')||''):'')
    +(m['最後修改時間']?chip('clock',tx(m['最後修改時間']).slice(5),'最後修改時間',tx(m['最後修改時間'])):'')+(m['標題']?chip('type',tx(m['標題']),'導出的檔',more):''))}
// ③ 轉文字
s=document.getElementById('s3');if(s){const soft=s.querySelector('.in>.soft');if(soft){const parts=soft.textContent.split(/　+/).map(x=>x.trim()).filter(x=>x&&!/^[\d,]+\s*字$/.test(x));
  put(s,parts.map((p,i)=>{const m=p.match(/^(.*?)（(.*)）$/);return chip(i?'read':'type',m?m[1]:p,i?'怎麼讀':'檔案類型',m?m[2]:'')}).join(''))}}
// ④ 建庫: what the head has goes; the rest as chips; the date table into the timeline's hover; links that repeat 所屬文件 go
s=document.getElementById('s4');if(s){const m=facts(s),rel=m['和單位的關係'],line=m['單位行'];const read=m['讀到字']?tx(m['讀到字']):'';
  put(s,(read?chip('text',read.replace(/^有\s*/,''),'讀到字',m['文字來源']?'文字來源：'+tx(m['文字來源']):''):'')+(rel?chip('unit',tx(rel).replace(/\s+/g,' '),'和單位的關係',line?tx(line):''):''));
  const tb=[...s.querySelectorAll('details.fold')].find(f=>/日期明細/.test(tx(f.querySelector('summary'))));const pts=s.querySelectorAll('.tline .pt');
  if(tb){tb.querySelectorAll('tbody tr').forEach((tr,i)=>{const td=[...tr.children].map(tx);const pt=pts[i];if(pt)pt.dataset.tip=`${td[0]} ${td[1]}${td[2]&&td[2]!==td[1]?'\n原文：'+td[2]:''}${td[3]&&td[3]!==td[0]?'\n出處：'+td[3]:''}`});tb.remove()}
  const rel2=new Set([...s.querySelectorAll('.rel a')].map(a=>a.getAttribute('href')));
  [...s.querySelectorAll('details.fold')].filter(f=>/連到它的網頁/.test(tx(f.querySelector('summary')))).forEach(f=>{const hs=[...f.querySelectorAll('a')].map(a=>a.getAttribute('href'));if(hs.length&&hs.every(h=>rel2.has(h)))f.remove()})}
})();
(()=>{const DBB=((([...document.querySelectorAll('.nk-bar a')].find(a=>a.textContent.includes('資料庫'))||{}).href||'').replace(/\/$/,''))+'/aca';const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const lis=$$('ol.steps li');let cur=null;
const show=id=>{cur=cur===id?null:id;$$('.card.step').forEach(s=>s.classList.toggle('pd-on',s.id===cur));
  lis.forEach(li=>{const a=li.querySelector('a');li.classList.toggle('pd-sel',!!cur&&!!a&&a.getAttribute('href')==='#'+cur)})};
lis.forEach(li=>{const a=li.querySelector('a');if(!a)return;a.onclick=e=>{e.preventDefault();show(a.getAttribute('href').slice(1))}});
const st=new URLSearchParams(location.search).get('step');if(st)show(st);
// 資料庫's queries about this one document, small, under the head
const id=($('.who [data-copy]')||{}).dataset?.copy;const who=$('.who>div');if(id&&who){const d=document.createElement('div');d.className='pd-db';
  const Q=[['doc','全部欄位'],['doc_dates','讀到的日期'],['doc_chunks','切成的小段'],['copies_of','合併進來的副本'],['doc_scope_rows','和單位的關係'],['why_undated','為什麼沒日期']];
  d.innerHTML=`<i data-tip='在資料庫裡看這份文件'><svg viewBox='0 0 16 16' width='13' height='13' fill='none' stroke='currentColor' stroke-width='1.4' aria-hidden='true'><ellipse cx='8' cy='3.8' rx='5' ry='1.8'/><path d='M3 3.8v8.4c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V3.8M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8'/></svg></i>`+Q.map(([k,n])=>`<a href='${DBB}/${k}?doc_id=${encodeURIComponent(id)}' target='_blank' rel='noopener'>${n}</a>`).join('');who.append(d)}
// opened from a site's 品質提醒 row (site_design.py doc_go): which reminder, why this document, and the way back
const q=new URLSearchParams(location.search),ck=q.get('ck'),NAME={"none": "無文件", "self": "標題搜不到", "empty": "網頁無文字", "no_date": "無日期", "same_title": "標題重複", "same_opening": "選單混入正文", "fragments": "附件逐字斷行", "spike": "檔案時間雷同"},WHAT={"none": "一份文件都沒收進庫", "self": "抽 20 份文件、用自己的標題去搜，排進前 10 的不到七成就提醒", "empty": "網頁轉成文字後是空的；超過兩成才提醒", "no_date": "公告、網頁、附件裡沒有日期；超過一半才提醒", "same_title": "15 份以上的文件用同一個標題，搜尋結果分不出哪份是哪份", "same_opening": "三成以上的網頁開頭 80 個字一樣，多半是選單被當成正文讀了進來", "fragments": "附件轉出來一行只有一個字（多半是圖表或轉向的表格），裡面的字搜不太到", "spike": "同一天有二十個以上的檔共用同一個伺服器時間（搬家或整批上傳），這些檔不拿這個時間當日期"};
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
if(ck&&NAME[ck]){const site=q.get('site')||'',ref=q.get('ref')||'',t=q.get('t')||'';const box=document.createElement('section');box.className='card pd-ck';
  box.innerHTML=`<div class='pd-ck-h'><span class='pd-tag'>品質提醒</span><b data-tip='${esc(WHAT[ck]||'')}'>${esc(NAME[ck])}</b>`
    +(site?`<a class='pd-back' href='/site?site=${encodeURIComponent(site)}&ck=${encodeURIComponent(ck)}'>‹ 回到這個網站的${esc(NAME[ck])}</a>`:'')+`</div>`
    +(q.get('why')?`<p class='pd-why'>${esc(q.get('why'))}</p>`:'')
    +(ref&&id&&ref!==id?`<p class='pd-sample'>設計稿的文件頁只有一份樣本：下面是 ${esc(id)}，不是${t?'「'+esc(t)+'」':' '+esc(ref)}</p>`:'');
  const w=$('.who');if(w)w.before(box)}
else if(q.get('ref')&&id&&q.get('ref')!==id){const p=document.createElement('p');p.className='pd-sample pd-only';const t=q.get('t');
  p.innerHTML=`設計稿的文件頁只有一份樣本：下面是 ${esc(id)}，不是${t?'「'+esc(t)+'」':' '+esc(q.get('ref'))}`;const w=$('.who');if(w)w.before(p)}
})();
(()=>{let cur=null;document.querySelectorAll('.strip a[data-chunk]').forEach(a=>{const c=document.getElementById(a.dataset.chunk);if(!c)return;
  const peek=(c.querySelector('.peek')||{}).textContent||'',side=(c.querySelector('.side')||{}).textContent||'';
  a.dataset.tipH=(c.querySelector('summary b')||{}).textContent+' · '+side.split('·')[0].trim();a.dataset.tip=peek.trim();
  a.onclick=e=>{e.preventDefault();const on=cur!==a;document.querySelectorAll('.chunks .chunk').forEach(x=>{x.classList.remove('pd-show');x.open=false});
    document.querySelectorAll('.strip a.pd-on').forEach(x=>x.classList.remove('pd-on'));cur=on?a:null;if(on){c.classList.add('pd-show');c.open=true;a.classList.add('pd-on')}}})})();
