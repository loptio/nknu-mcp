"""The charts of 每晚運行 and a site's page (panels v3, spec docs/superpowers/specs/2026-11-17-panels-v3-questions.md 一,
五; owner 11-17: 「你再按照安装的插件和发现的问题再优化dashboard吧」): the shared look of a chart's card, its 「看表格」 twin,
and the script that draws the two charts made with ECharts (the night clock of the last seven nights, and the nights'
in and out as bars).

How a chart is put on a page (the dataviz method's components.md: every chart has a table-view twin):

- the numbers go in once, as JSON in a <script type='application/json'> (data_tag): the script draws from it and the
  tests read it;
- the table with the same numbers is printed in the page; it is what shows until ECharts has loaded, and stays when it
  does not load (the CDN cannot be reached), so nothing is ever only in the drawing;
- ECharts comes from cdnjs at one fixed version (ECHARTS), async, and draws with the colours of the panels' theme.css
  read at the time of drawing (getComputedStyle): never a colour written here (tests/ops/test_theme.py), and drawn
  again when the theme turns dark or light;
- a chart that is plain HTML (the funnel, a site's reasons and kinds) needs no script; its toggle works the same.

Colours, by job only (theme.css): amounts are violet (--amt, --q2); a step that is done is neutral grey (--line2, the
spec's 五: steps are not kinds and get no hue); the state colours (--bad, --warn) only on the segment or mark that
failed or waits, with an icon glyph and its word. The validator (dataviz scripts/validate_palette.js) passed the pairs
these charts put side by side, light on #ffffff and dark on #191918: --q2/--amt as an ordinal pair (2.03:1 and 2.10:1
at the light end), --line2/--warn/--bad on every pair (worst ΔE 20.0 light, 12.6 colour-blind / 15.4 normal dark; the
grey and the light amber are under 3:1 against the surface, so every segment carries its name or icon and the table
twin is always there). 【Claude】 redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import json

from nknu.ops.look import e

# 5.5.0, not the brief's 5.5.1: cdnjs has no 5.5.1 (404 on 2026-11-17; it lists 5.5.0 and 5.6.0) 【Claude】
ECHARTS = "https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"


def data_tag(tag_id: str, data: object) -> str:
    """The chart's numbers as JSON in the page: every < written as \\u003c, so no text in it (a school's title) ever
    reads as markup or closes the tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f"<script type='application/json' id='{e(tag_id)}'>{text}</script>"


def toggle(fig_id: str) -> str:
    """The 「看表格」 button of a chart's head: hidden until the script can draw (an ECharts chart shows its table
    until then)."""
    return (f"<button type='button' class='v3-tog' data-for='{e(fig_id)}' aria-pressed='false' hidden>看表格</button>")


def figure(fig_id: str, *, chart: str = "", src: str = "", plot: str = "", table: str, label: str,
           height: int = 0, key: str = "") -> str:
    """A chart and its table twin. chart: the ECharts drawing's name (CHARTS_JS's DRAW) with src, the id of its JSON;
    or plot: the chart as plain HTML. An ECharts chart starts as its table (v3-astable); key: what its marks mean,
    shown with the drawing only."""
    if chart:
        size = f" style='height:{height}px'" if height else ""
        return (f"<figure class='v3-fig v3-astable' id='{e(fig_id)}' data-chart='{e(chart)}' data-src='{e(src)}'>"
                f"<div class='v3-plot' role='img' aria-label='{e(label)}'{size}></div>{key}"
                f"<div class='v3-table'>{table}</div></figure>")
    return (f"<figure class='v3-fig' id='{e(fig_id)}' aria-label='{e(label)}'><div class='v3-plot'>{plot}</div>"
            f"<div class='v3-table'>{table}</div></figure>")


def table(head: list[str], rows: list[list[str]], *, nums: tuple[int, ...] = ()) -> str:
    """A plain table of already escaped cells; the columns in nums are right-aligned numbers."""
    th = "".join(f"<th{' class=n' if i in nums else ''}>{h}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{' class=n' if i in nums else ''}>{c}</td>" for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f"<table class='v3-t'><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


CSS = """
.v3-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:26px 2px 10px}
.v3-h h2{font-size:16px;font-weight:650}
.v3-h .faint{font-size:12px}
.v3-tog{margin-left:auto;display:inline-flex;align-items:center;padding:4px 10px;border:1px solid var(--line2);
border-radius:8px;background:var(--surface);color:var(--ink2);font-size:12px;cursor:pointer}
.v3-tog:hover{background:var(--hover);color:var(--ink)}.v3-tog[aria-pressed=true]{background:var(--act);
color:var(--on-act);border-color:var(--act)}
.v3-tog[hidden]{display:none}
figure.v3-fig{margin:0;min-width:0}
.v3-fig.v3-astable .v3-plot{display:none}
.v3-fig:not(.v3-astable) .v3-table{display:none}
.v3-plot{min-width:0;width:100%}
.v3-table{overflow-x:auto}
table.v3-t{border-collapse:collapse;width:100%;font-size:13px}
table.v3-t th{text-align:left;font-weight:600;font-size:12px;color:var(--ink2);padding:6px 10px 6px 0;
border-bottom:1px solid var(--line);white-space:nowrap}
table.v3-t td{padding:6px 10px 6px 0;border-bottom:1px solid var(--line);vertical-align:top}
table.v3-t tr:last-child td{border-bottom:0}
table.v3-t .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.v3-empty{color:var(--ink2);font-size:13px;padding:6px 0}
"""

# The script: the toggles, and the two ECharts drawings. It reads the colours from theme.css when it draws (V), puts
# every label in as text (esc() in the tooltips, textContent elsewhere: names come from the library), and draws
# again on a change of theme or size. nkCharts.ready() is called by ECharts' own <script onload>.
JS = r"""
(()=>{
const root=document.documentElement;
const V=n=>getComputedStyle(root).getPropertyValue(n).trim();
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const num=n=>n==null?'—':Number(n).toLocaleString('en-US');
const charts=new Map();
const figs=()=>[...document.querySelectorAll('figure.v3-fig')];
const read=f=>{try{return JSON.parse(document.getElementById(f.dataset.src).textContent)}catch(_){return null}};
const font=()=>V('--font')||'system-ui,sans-serif';
document.querySelectorAll('.v3-tog').forEach(b=>{const f=document.getElementById(b.dataset.for);if(!f)return;
 if(!f.dataset.chart)b.hidden=false;
 b.addEventListener('click',()=>{const on=!f.classList.contains('v3-astable');f.classList.toggle('v3-astable',on);
  f.dataset.picked='1';b.setAttribute('aria-pressed',String(on));b.textContent=on?'看圖':'看表格';
  if(!on){const c=charts.get(f);if(c)c.resize()}})});
const tipBox=()=>({backgroundColor:V('--surface'),borderColor:V('--line2'),borderWidth:1,padding:[8,10],
 textStyle:{color:V('--ink2'),fontSize:12,fontFamily:font()},confine:true,
 extraCssText:'border-radius:10px;box-shadow:'+V('--pop')+';max-width:300px;white-space:normal;line-height:1.5'});
const head=t=>'<b style="color:'+V('--ink')+';font-size:13px">'+esc(t)+'</b>';
const DRAW={};
// 每晚時間: one row a night (the newest on top), the hours after its midnight across, a segment a step
DRAW.timeline=(d,f)=>{
 const narrow=f.querySelector('.v3-plot').clientWidth<560;
 const C={done:V('--line2'),warn:V('--warn'),bad:V('--bad'),surf:V('--surface'),ink:V('--ink'),ink2:V('--ink2'),
  ink3:V('--ink3'),line:V('--line'),onBad:V('--on-bad'),onWarn:V('--on-warn'),badInk:V('--bad-ink'),warnInk:V('--warn-ink')};
 const rows=d.nights,items=[];
 rows.forEach((n,i)=>{for(const s of n.segs)items.push({value:[i,s.from,s.to],s:s,n:n});
  if(!n.segs.length&&n.mark)items.push({value:[i,1,1],s:{tone:'note',mark:n.mark,markTone:n.markTone},n:n})});
 const glyph={bad:'✕ ',warn:'! ',run:'◷ '};
 // where each label goes, decided once a row in time order: in its bar (its time on the line under), else after it on
 // the bar's line, else on the third line from where it starts; never before it (a cold reader of the lab's page,
 // 11-17, read a label placed before its step as the step before's). P: pixels an hour, from the plot's width
 const plot=f.querySelector('.v3-plot'),P=Math.max(1,((plot&&plot.clientWidth)||900)-96)/d.max;
 const tw=(t,px)=>[...String(t)].reduce((n,c)=>n+(c.charCodeAt(0)>255?px:px*0.56),0);
 rows.forEach(n=>{let l2=-1e9,l3=-1e9;n.segs.forEach((s,k)=>{
  const loud=s.tone==='bad'||s.tone==='warn',x=s.from*P,w=Math.max((s.to-s.from)*P-2,loud?16:8);
  const next=k+1<n.segs.length?n.segs[k+1].from*P:d.max*P,plain=!s.dur;
  const name=(glyph[s.tone]||'')+(plain?(s.label||s.name):s.name),p=s.place={};
  if(tw(name,12)+12<=w){p.inside=name;if(!plain&&x>=l2){p.dur=s.dur;l2=x+tw(s.dur,11)+8}}
  else{const out=plain?(s.label||s.name):s.name+' '+s.dur,need=tw(out,12)+8;p.mark=loud;
   if(x+w+4+need<=next)p.right=out;else if(x>=l3){p.below=out;l3=x+need}}})});
 return {animation:false,textStyle:{fontFamily:font()},
  grid:{left:8,right:24,top:6,bottom:24,containLabel:true},
  tooltip:{...tipBox(),trigger:'item',formatter:p=>{const it=items[p.dataIndex];if(!it)return '';const s=it.s;
   const when=it.n.tip?'<br>'+esc(it.n.tip):'';if(s.tone==='note')return head(it.n.title)+when+'<br>'+esc(s.mark);
   return head(it.n.title+' · '+s.name+(s.run?' 第 '+s.run+' 次':'')+'：'+s.word)+when+'<br>'+esc(s.when)
    +(s.say?'<br>'+esc(s.say):'')}},
  xAxis:{type:'value',min:0,max:d.max,interval:narrow?2:1,axisLine:{show:false},axisTick:{show:false},
   axisLabel:{color:C.ink3,fontSize:11,formatter:v=>(v<10?'0':'')+v+':00'},splitLine:{lineStyle:{color:C.line}}},
  yAxis:{type:'category',inverse:true,data:rows.map(n=>n.title),axisLine:{show:false},axisTick:{show:false},
   axisLabel:{color:C.ink2,fontSize:12}},
  series:[{type:'custom',data:items.map(it=>it.value),encode:{x:[1,2],y:0},clip:true,
   renderItem:(params,api)=>{const it=items[params.dataIndex],s=it.s;const y=api.value(0);
    const a=api.coord([api.value(1),y]),b=api.coord([api.value(2),y]);const band=api.size([0,1])[1];
    const h=Math.min(16,band*0.3),cy=a[1]-band*0.22,y2=a[1]+band*0.06,y3=a[1]+band*0.3;
    if(s.tone==='note'){const col=s.markTone==='bad'?C.badInk:C.ink3;
     return {type:'text',style:{text:s.mark,x:a[0],y:cy,verticalAlign:'middle',fill:col,font:'12px '+font()}}}
    // a short step keeps a visible width (minW): 轉文字 3 分 was a hairline on the lab's page
    const loud=s.tone==='bad'||s.tone==='warn';const minW=loud?16:8;const x=a[0]+1,w=Math.max(b[0]-a[0]-2,minW);
    const fill=s.tone==='bad'?C.bad:s.tone==='warn'?C.warn:s.tone==='run'?C.surf:C.done;
    const ink=s.tone==='bad'?C.onBad:s.tone==='warn'?C.onWarn:C.ink;const weight=loud?'600 ':'';const p=s.place||{};
    const kids=[{type:'rect',shape:{x:x,y:cy-h/2,width:w,height:h,r:4},
     style:{fill:fill,stroke:s.tone==='run'?C.ink:null,lineWidth:s.tone==='run'?1.5:0}}];
    const text=(t,tx,ty,align,fillC,size,bold)=>kids.push({type:'text',style:{text:t,x:tx,y:ty,align:align,
     verticalAlign:'middle',fill:fillC,font:(bold||'')+size+'px '+font()}});
    if(p.inside){text(p.inside,x+6,cy,'left',ink,12,weight);if(p.dur)text(p.dur,x+1,y2,'left',C.ink2,11)}
    else{if(p.mark)text((glyph[s.tone]||'').trim(),x+w/2,cy,'center',ink,12,weight);
     if(p.right)text(p.right,x+w+4,cy,'left',C.ink2,12);else if(p.below)text(p.below,x+1,y3,'left',C.ink2,11)}
    return {type:'group',children:kids}}}]};
};
// 每晚進出: new up, left down, one bar a night; a red dot where some left for no reason found
DRAW.nightbars=(d,f,chart)=>{
 const n=d.nights,C={amt:V('--amt'),q2:V('--q2'),bad:V('--bad'),badInk:V('--bad-ink'),surf:V('--surface'),
  ink2:V('--ink2'),ink3:V('--ink3'),line:V('--line'),line2:V('--line2')};
 const counted=n.map(x=>x&&x.counted);
 chart.off('click');chart.on('click',p=>{const it=n[p.dataIndex];if(it&&it.counted)f.dispatchEvent(
  new CustomEvent('nk-night',{bubbles:true,detail:{night:it.night}}))});
 return {animation:false,textStyle:{fontFamily:font()},
  grid:{left:8,right:16,top:30,bottom:24,containLabel:true},
  tooltip:{...tipBox(),trigger:'axis',axisPointer:{type:'shadow',shadowStyle:{color:V('--hover')}},formatter:ps=>{
   const it=n[ps[0].dataIndex];if(!it)return '';const t=head(it.date+(it.label?' '+it.label:''));
   if(!it.counted)return t+'<br>'+esc(it.why||'沒有紀錄');
   return t+'<br>新進 '+num(it.new)+(it.changed!=null?'<br>改了 '+num(it.changed):'')+'<br>離開 '+num(it.left)
    +(it.unexplained?'<br><b style="color:'+C.badInk+'">✕ 查不出原因 '+num(it.unexplained)+'</b>':'')
    +(it.docs!=null?'<br>庫裡 '+num(it.docs)+' 份':'')+(it.source?'<br><span style="font-size:11px">'+esc(it.source)+'</span>':'')}},
  xAxis:{type:'category',data:n.map(x=>x.date),axisTick:{show:false},axisLine:{lineStyle:{color:C.line2}},
   axisLabel:{color:C.ink3,fontSize:11,interval:'auto',
    formatter:(v,i)=>counted[i]?v:'{off|'+v+'}',rich:{off:{color:C.line2,fontSize:11}}}},
  yAxis:{type:'value',boundaryGap:['22%','4%'],minInterval:1,
   axisLabel:{color:C.ink3,fontSize:11,formatter:v=>num(Math.abs(v))},
   splitLine:{lineStyle:{color:C.line}}},
  graphic:[{type:'text',left:4,top:2,style:{text:'新進 ↑',fill:C.ink2,font:'12px '+font()}},
   {type:'text',left:4,bottom:2,style:{text:'離開 ↓',fill:C.ink2,font:'12px '+font()}}],
  series:[{name:'新進',type:'bar',stack:'f',barMaxWidth:24,itemStyle:{color:C.amt,borderRadius:[4,4,0,0]},
    data:n.map(x=>x.counted?x.new:null)},
   {name:'離開',type:'bar',stack:'f',barMaxWidth:24,itemStyle:{color:C.q2,borderRadius:[0,0,4,4]},
    data:n.map(x=>x.counted?-x.left:null)},
   {name:'查不出原因',type:'scatter',symbolSize:10,z:5,itemStyle:{color:C.bad,borderColor:C.surf,borderWidth:2},
    label:{show:true,position:'bottom',distance:4,color:C.badInk,fontSize:11,fontWeight:600,
     formatter:p=>'✕ '+num(n[p.dataIndex].unexplained)},
    data:n.map(x=>x.counted&&x.unexplained?-x.left:null)}]};
};
const paint=()=>{if(!window.echarts)return;for(const f of figs()){const k=f.dataset.chart;if(!k||!DRAW[k])continue;
 const d=read(f);if(!d)continue;const el=f.querySelector('.v3-plot');let c=charts.get(f);if(c)c.dispose();
 if(!f.dataset.picked)f.classList.remove('v3-astable');
 c=echarts.init(el,null,{renderer:'svg'});charts.set(f,c);c.setOption(DRAW[k](d,f,c))}};
// a night picked on the bars: its sites in the panel under the chart (data-night-panel), drawn as text
document.addEventListener('nk-night',ev=>{const f=ev.target.closest('figure');const box=f&&f.parentElement
 .querySelector('[data-night-panel]');const d=f&&read(f);if(!box||!d)return;const night=ev.detail.night;
 const it=d.nights.find(x=>x.night===night);const rows=(d.sites||{})[night]||[];
 box.querySelector('[data-night-title]').textContent=(it?it.date:night)+' 有變動的網站';
 const list=box.querySelector('ul');list.textContent='';
 if(!rows.length){const li=document.createElement('li');li.className='v3-empty';li.textContent='這一晚沒有網站有變動';list.append(li)}
 for(const s of rows.slice(0,d.shown||8)){const li=document.createElement('li');const a=document.createElement('a');
  a.href=s.href;a.textContent=s.name||s.site;li.append(a);
  for(const [k,w] of [['new','新進'],['left','離開']]){const b=document.createElement('span');b.className='n';
   b.textContent=w+' '+num(s[k]||0);li.append(b)}
  if(s.unexplained){const x=document.createElement('span');x.className='v3-flag';x.textContent='✕ 查不出原因 '+num(s.unexplained);li.append(x)}
  list.append(li)}});
let ready=false;
window.nkCharts={ready(){if(ready||!window.echarts)return;ready=true;
 document.querySelectorAll('.v3-tog').forEach(b=>{b.hidden=false});paint();
 matchMedia('(prefers-color-scheme: dark)').addEventListener('change',paint);
 new MutationObserver(paint).observe(root,{attributes:true,attributeFilter:['data-theme']});
 // a resize draws again: the timeline's labels are placed for the plot's width
 if(window.ResizeObserver){let again=0;const ro=new ResizeObserver(()=>{clearTimeout(again);again=setTimeout(paint,150)});figs().forEach(f=>{const p=f.querySelector('.v3-plot');if(p)ro.observe(p)})}},
 fail(){}};
})();
"""


def scripts() -> str:
    """The script, then ECharts itself, async, calling it when loaded (a page with no chart drawn by ECharts still
    gets the toggles)."""
    return (f"<script>{JS}</script><script src='{ECHARTS}' async crossorigin='anonymous' referrerpolicy='no-referrer' "
            "onload='nkCharts.ready()' onerror='nkCharts.fail()'></script>")
