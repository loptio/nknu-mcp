"""The look the 檢視台 pages share: the colours, the style sheet, the small script, the icons and the page around a
body (nknu.ops.web serves them; nknu.ops.search_page and nknu.ops.trace_page fill them).

Owner 11-15, after two rounds on these pages: 「太難看懂字太雜了…整個面版給人的信息太雜了 沒有很人性化的讓人看到 也沒有
用一些視覺化方法點綴，顏色也沒有考慮過」. This round is 【Claude】's:

- what a sentence said is drawn where it can be (a scoreboard of the test questions, a ruler of where the gold answers
  stand, the candidates' funnel, each score as a bar of its parts, a document's dates on a line, its chunks as a
  strip), and the exact numbers wait behind a hover or a click;
- colour does three jobs and nothing else, as on 資料總覽 (deploy/lab-overview/page.html, whose paper and ink these
  are): how much = one blue, --acc (bars, counts, places, the chunks' strip; also what is picked and the one button
  that acts), the same blue 資料總覽 took for its amounts after the owner said of it, 11-15, 「整體顏色也是單純的黑色」
  (these pages had drawn their amounts in ink too); good or bad = green / amber / red (a verdict, a step's state,
  what raised a score and what a discount took); a document's kind = an icon and its word, not a hue, so that green
  never means both 「行事曆」 and 「好」 on one page;
- the four outcome colours were picked with the dataviz skill's validator (scripts/validate_palette.js, all pairs,
  protanopia and deuteranopia simulated): light #348757 #71c38f #c48d25 #a42d15 on #ffffff (worst pair ΔE 8.9
  colour-blind, 16.6 normal vision), dark #1e7546 #55a674 #c48225 #b54016 on #191918 (8.0 and 15.4); two of the light
  ones are under 3:1 against the surface, so a tile always prints its place as well. The blue beside them (a bar's
  base next to what raised it) passes the same checks: light #3578b8 (16.0 / 17.0 against the green), dark #3d8fd6.

Owner 11-15, after that round: 「很多名字看着不直觉 然后感觉是想缩短文字 但又想保留细节导致不上不下的」, pointing at
資料總覽's 「鼠标放在某个什么才出现悬浮窗来详细解释」. So 【Claude】: a page shows a plain name, and the detail floats over
it in 資料總覽's card (TIP_JS and the .tip rules; Datasette's pages run the same script, deploy/lab-inspect/datasette/
static/tip.js); the two tools are named for what they do, 搜尋測試 and 文件追蹤 (they were 搜尋試驗台 and 逐份追蹤), and a
word means one thing on every panel (類型, 主料／背景, 合併, 出處…, as Datasette's columns say them).
"""

from __future__ import annotations

import functools
import html

from starlette.responses import HTMLResponse

from nknu.ops.theme import bar as theme

FONT = 'system-ui,-apple-system,"PingFang TC","Microsoft JhengHei","Noto Sans CJK TC",sans-serif'

# the panels' one look (colours, type, the top bar), then this panel's own parts
THEME = theme.theme_css()
CSS = """
a{color:inherit;text-underline-offset:2px}button,input,select{font:inherit;color:inherit}
h1,h2,h3{margin:0}
.ic{flex:none;vertical-align:-2px}
.soft{color:var(--ink2)}.faint{color:var(--ink3)}.small{font-size:12px}.mono{font-family:var(--mono);font-size:12px}
.num{font-variant-numeric:tabular-nums}

main{max-width:var(--page);margin:0 auto;padding:18px var(--gutter) 48px}
.note{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--warn);background:var(--warn-bg);
padding:9px 13px;border-radius:10px;margin:0 0 16px;font-size:13px}.note .ic{color:var(--warn-ink);margin-top:3px}

/* cards, section heads, pills */
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);min-width:0}
.pad{padding:16px 18px}
.sec{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:26px 2px 10px;font-size:16px;font-weight:650}
.sec .faint{font-weight:400;font-size:12px}
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 10px;border-radius:999px;font-size:12px;
font-weight:650;white-space:nowrap}
.pill.good{background:var(--good-bg);color:var(--good-ink)}.pill.warn{background:var(--warn-bg);color:var(--warn-ink)}
.pill.bad{background:var(--bad-bg);color:var(--bad-ink)}.pill.none{background:var(--hover);color:var(--ink2)}
.tag{display:inline-flex;align-items:center;gap:4px;padding:0 7px;border-radius:6px;background:var(--hover);
color:var(--ink2);font-size:12px;white-space:nowrap}
.kind{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}

/* the two forms */
form.ask{display:flex;gap:8px;flex-wrap:wrap;margin:0}
form.ask .box{position:relative;flex:1 1 320px;min-width:0;display:flex}
form.ask .box .ic{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--ink3);pointer-events:none}
form.ask input{flex:1 1 auto;min-width:0;width:100%;padding:11px 14px 11px 38px;border:1px solid var(--line2);
border-radius:12px;background:var(--surface);font-size:16px}
form.ask input:focus{outline:2px solid var(--act);outline-offset:1px}
form.ask .box.narrow{flex:0 1 300px}form.ask .box.narrow input{font-size:13px;padding-left:36px}
form.ask button{padding:0 22px;min-height:44px;border:0;border-radius:12px;background:var(--act);color:var(--on-act);
font-weight:650;cursor:pointer}form.ask button:hover{opacity:.88}
.hint{margin:8px 2px 0;color:var(--ink3);font-size:12px}
kbd{display:inline-block;min-width:18px;padding:0 5px;border:1px solid var(--line2);border-bottom-width:2px;
border-radius:5px;background:var(--surface);font:11px/16px var(--mono);text-align:center;color:var(--ink2)}

/* the home page: the two tools side by side */
.duo{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
@media (max-width:860px){.duo{grid-template-columns:minmax(0,1fr)}}
.tool h2{display:flex;align-items:center;gap:8px;font-size:16px;margin:0 0 4px}
.tool p{margin:0 0 12px;color:var(--ink2);font-size:13px}
.tool .ico{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:var(--hover);color:var(--ink)}
.eg{display:flex;flex-direction:column;gap:2px;margin-top:12px}
.eg a{display:flex;align-items:center;gap:8px;padding:6px 8px;margin:0 -8px;border-radius:8px;text-decoration:none;
font-size:13px;min-width:0}.eg a:hover{background:var(--hover)}
.eg a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.eg .ic{color:var(--ink3)}

/* the scoreboard of the test questions */
.tally{display:flex;gap:2px;height:28px;border-radius:7px;overflow:hidden;margin:2px 0 10px}
.tally .seg{display:flex;align-items:center;justify-content:flex-start;padding:0 9px;min-width:0;font-size:13px;
font-weight:700;font-variant-numeric:tabular-nums;border-radius:4px;overflow:hidden;transition:flex-grow .3s}
.tally .seg[data-n="0"]{display:none}
.g-first{background:var(--good);color:var(--on-good)}.g-top3{background:var(--good2);color:var(--on-good2)}
.g-top{background:var(--warn);color:var(--on-warn)}.g-out{background:var(--bad);color:var(--on-bad)}
.g-wait{background:repeating-linear-gradient(135deg,var(--mark2) 0 5px,var(--hover) 5px 10px);color:var(--ink2)}.g-failed{background:var(--ink3);color:var(--surface)}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.legend i{width:10px;height:10px;border-radius:3px;flex:none}
.legend .prog{margin-left:auto;color:var(--ink2);font-variant-numeric:tabular-nums}.legend .prog[hidden]{display:none}
.legend i.cut{background:var(--cut)}.legend i.rel{background:var(--amt)}.legend i.add{background:var(--good)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:2px 14px;margin-top:14px}
.tile{display:flex;align-items:center;gap:11px;padding:6px 8px;border-radius:10px;text-decoration:none;min-width:0}
.tile:hover{background:var(--hover)}
.tile .g{flex:none;display:grid;place-items:center;width:34px;height:26px;border-radius:7px;font-size:13px;
font-weight:700;font-variant-numeric:tabular-nums}
.tile .q{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile .q b{margin-right:8px;color:var(--ink3);font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}
/* a question still waiting: an empty pending box, its words quieter, filled in when its result comes (redesign
   2026-11-17, diagnosis.md 搜尋測試 ①) */
.tile[data-grade=wait] .g{background:var(--hover);box-shadow:inset 0 0 0 1px var(--line2);animation:wait 1.2s ease-in-out infinite alternate}
.tile[data-grade=wait] .q{color:var(--ink3)}
@keyframes wait{from{opacity:.45}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.tile[data-grade=wait] .g{animation:none}.tally .seg{transition:none}}

/* a search: the question's bar, the verdict with its ruler */
.qnav{display:flex;align-items:center;gap:8px;margin:12px 0 0}
.qnav a.step{display:grid;place-items:center;width:32px;height:32px;border-radius:9px;border:1px solid var(--line2);
background:var(--surface);text-decoration:none;color:var(--ink2)}.qnav a.step:hover{color:var(--ink);background:var(--hover)}
.qnav a.step[aria-disabled]{opacity:.35;pointer-events:none}
.qnav select{flex:1 1 auto;min-width:0;max-width:640px;height:32px;padding:0 10px;border:1px solid var(--line2);
border-radius:9px;background:var(--surface);color:var(--ink2);font-size:13px}
.qnav .back{margin-left:auto;font-size:12px;color:var(--ink2);white-space:nowrap}
.verdict{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 28px;align-items:center;margin-top:16px}
@media (max-width:820px){.verdict{grid-template-columns:minmax(0,1fr)}}
.v-main{display:flex;align-items:center;gap:14px}
.v-badge{display:grid;place-items:center;min-width:64px;height:64px;padding:0 12px;border-radius:16px;font-size:26px;
font-weight:750;letter-spacing:-.01em}
.v-badge small{display:block;font-size:11px;font-weight:600;letter-spacing:0;margin-top:-6px;opacity:.9}
.v-text b{display:block;font-size:16px;line-height:1.3}.v-text span{color:var(--ink2);font-size:13px}
.ruler{position:relative;height:74px;min-width:0}
.ruler .track{position:absolute;left:0;right:0;top:30px;height:10px;border-radius:5px;background:var(--mark2);
overflow:hidden}
.ruler .zone{position:absolute;top:0;bottom:0}.ruler .z1{background:var(--good);opacity:.55}
.ruler .z2{background:var(--good2);opacity:.5}.ruler .z3{background:var(--warn);opacity:.28}
.ruler .ticks{position:absolute;left:0;right:0;top:44px;height:18px;font-size:11px;color:var(--ink3);
font-variant-numeric:tabular-nums}
.ruler .ticks span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.ruler .ticks span::before{content:"";position:absolute;left:50%;top:-5px;width:1px;height:4px;background:var(--line2)}
.ruler .marks{position:absolute;left:0;right:0;top:0;height:40px}
.ruler.lost .track,.ruler.lost .ticks,.ruler.lost .marks{right:68px}
.ruler .m{position:absolute;transform:translateX(-50%);display:grid;place-items:center;min-width:26px;height:22px;
padding:0 6px;border-radius:11px;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;text-decoration:none;
box-shadow:0 0 0 2px var(--surface)}
.ruler .m::after{content:"";position:absolute;left:50%;top:100%;width:2px;margin-left:-1px;background:inherit}
.ruler .m.r0{top:4px}.ruler .m.r0::after{height:6px}.ruler .m.r1{top:-22px}.ruler .m.r1::after{height:32px}
.ruler .off{position:absolute;right:0;top:20px;width:52px;text-align:center;font-size:11px;color:var(--ink3);line-height:1.25}
.ruler .off b{display:grid;place-items:center;height:26px;border-radius:8px;font-size:13px;margin-bottom:3px}
.ruler.tall{height:96px}.ruler.tall .track{top:52px}.ruler.tall .ticks{top:66px}.ruler.tall .marks{top:22px}
.ruler.tall .off{top:42px}

/* how the question was read, and the candidates' funnel */
.pipe{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:0;margin-top:16px}
@media (max-width:900px){.pipe{grid-template-columns:minmax(0,1fr)}}
.pipe>div{padding:14px 18px;min-width:0}.pipe>div+div{border-left:1px solid var(--line)}
@media (max-width:900px){.pipe>div+div{border-left:0;border-top:1px solid var(--line)}}
.cap{display:flex;align-items:center;gap:8px;margin:0 0 9px;font-size:12px;color:var(--ink3);font-weight:600;
letter-spacing:.02em}
.words{display:flex;flex-wrap:wrap;gap:6px}
.w{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:8px;font-size:13px;white-space:nowrap;
border:1px solid transparent}
.w.must{background:var(--act);color:var(--on-act);font-weight:600}
.w.plain{border-color:var(--line2);color:var(--ink2)}.w.also{border:1px dashed var(--line2);color:var(--ink2)}
.w.term{background:var(--hover);color:var(--ink)}
.keyline{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:10px;font-size:12px;color:var(--ink3)}
.keyline span{display:inline-flex;align-items:center;gap:5px}
.keyline i{width:14px;height:10px;border-radius:3px;flex:none}
.keyline i.must{background:var(--act)}.keyline i.plain{border:1px solid var(--line2)}
.keyline i.also{border:1px dashed var(--line2)}
.funnel{display:flex;align-items:center;gap:0;height:74px}
.funnel .st{flex:none;display:flex;flex-direction:column;justify-content:center;gap:3px;height:100%}
.funnel .blk{width:10px;border-radius:3px;background:var(--amt)}
.funnel .blk.b{opacity:.55}
.funnel .flow{flex:1 1 0;min-width:18px;height:100%;background:var(--amt3)}
.flabels{display:flex;margin-top:6px;font-size:12px;color:var(--ink2)}
.flabels div{flex:1 1 0;min-width:0;line-height:1.3}.flabels div:last-child{flex:none;text-align:right}
.flabels b{display:block;font-size:16px;color:var(--ink);font-variant-numeric:tabular-nums}
.flabels small{color:var(--ink3);font-size:11px}
.off-note{color:var(--bad-ink);font-size:13px;margin-top:8px}

/* the results: one row per document, its place on each path, its score as a bar of its parts */
.rows{margin-top:10px;overflow:hidden}
.rhead,.row>summary,.row>.sum{display:grid;grid-template-columns:46px minmax(0,1fr) 44px 44px 44px minmax(150px,290px) 54px;
gap:0 12px;align-items:center}
.rhead{padding:9px 16px 8px;border-bottom:1px solid var(--line);font-size:12px;color:var(--ink3);font-weight:600}
.rhead .c{text-align:center}.rhead .r{text-align:right}
.rhead .lg{display:flex;gap:10px;align-items:center;font-weight:400}
.rhead .lg span{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.rhead .lg i{width:9px;height:9px;border-radius:2px}.rhead .lg i.rel{background:var(--amt)}
.rhead .lg i.add{background:var(--good)}.rhead .lg i.cut{background:var(--cut)}
.row{border-top:1px solid var(--line);scroll-margin-top:64px}.rhead+.row{border-top:0}
.row>summary,.row>.sum{padding:11px 16px;list-style:none}.row>summary{cursor:pointer}.row>summary::-webkit-details-marker{display:none}
.row>summary:hover{background:var(--hover)}.row[open]>summary{background:var(--sunk)}
.row .rk{font-size:20px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink)}
.row .rk.far{font-size:14px;color:var(--ink3);font-weight:600}
.row .ttl{font-weight:600;overflow-wrap:anywhere;line-height:1.4}
.row .ttl a{text-decoration:none}.row .ttl a:hover{text-decoration:underline}
.row .about{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px;margin-top:3px;color:var(--ink3);font-size:12px}
.row .why{margin-top:5px;color:var(--ink2);font-size:13px;line-height:1.5}
.star{display:inline-grid;place-items:center;width:18px;height:18px;margin-right:6px;border-radius:50%;
background:var(--act);color:var(--on-act);font-size:11px;vertical-align:1px}
.row .rk b{display:inline-grid;place-items:center;min-width:30px;height:26px;padding:0 6px;border-radius:8px;
font-size:16px;font-weight:700}
.pl{justify-self:center;display:grid;place-items:center;min-width:34px;height:22px;padding:0 5px;border-radius:6px;
font-size:12px;font-variant-numeric:tabular-nums;color:var(--ink3)}
.pl.a{background:var(--amt2);color:var(--ink);font-weight:700}.pl.b{background:var(--amt3);color:var(--ink2)}
.sc{min-width:0}
.bar{display:flex;gap:2px;height:10px}
.bar i{display:block;height:100%;min-width:2px}.bar i:first-child{border-radius:0}
.bar i:last-child{border-radius:0 4px 4px 0}
.bar .rel{background:var(--amt)}.bar .add{background:var(--good)}.bar .cut{background:var(--cut)}
.bar .rest{background:transparent;min-width:0}
.bnotes{display:flex;flex-wrap:wrap;gap:1px 10px;margin-top:4px;font-size:12px;color:var(--ink2);
font-variant-numeric:tabular-nums}
.bnotes span{white-space:nowrap}.bnotes .up::before{content:"▲ ";color:var(--good);font-size:.75em}
.bnotes .down::before{content:"▼ ";color:var(--bad-ink);font-size:.75em}
.tot{text-align:right;font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}
.tot.none{font-weight:400;color:var(--ink3)}
.rdiv{padding:9px 16px;border-top:1px solid var(--line);background:var(--sunk);font-size:12px;color:var(--ink2);
font-weight:600}
.more{padding:4px 16px 16px 74px;background:var(--sunk);display:grid;grid-template-columns:minmax(0,340px) minmax(0,1fr);
gap:14px 34px}
@media (max-width:860px){.rhead .c,.row .pl{display:none}
.rhead,.row>summary,.row>.sum{grid-template-columns:34px minmax(0,1fr) 50px}
.rhead .lg{display:none}.row .sc{grid-column:2/4;margin-top:6px}.row .tot{grid-row:1;grid-column:3}
.more{padding-left:16px;grid-template-columns:minmax(0,1fr)}}
.more h3{font-size:12px;color:var(--ink3);font-weight:600;margin:10px 0 6px}
.calc{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
.calc td{padding:3px 0;vertical-align:baseline}.calc td:nth-child(2){color:var(--ink3);font-size:12px;padding-left:10px}
.calc td:last-child{text-align:right;font-weight:600}
.calc tr.sum td{border-top:1px solid var(--line2);padding-top:5px}.calc tr.off td{color:var(--ink3)}
.calc tr.off td:last-child{font-weight:400}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px 22px;margin:0}
.facts div{min-width:0}.facts dt{font-size:12px;color:var(--ink3);margin:0 0 1px}
.facts dd{margin:0;overflow-wrap:anywhere;font-weight:550}.facts dd small{font-weight:400;color:var(--ink2)}
.facts .wide{grid-column:1/-1}
.mini{display:grid;grid-template-columns:auto minmax(40px,1fr) auto;gap:4px 10px;align-items:center;font-size:13px;
font-variant-numeric:tabular-nums}
.mini .t{height:6px;border-radius:3px;background:var(--mark2);overflow:hidden}
.mini .t i{display:block;height:100%;background:var(--amt);border-radius:3px}
.act{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border:1px solid var(--line2);border-radius:9px;
background:var(--surface);text-decoration:none;font-size:13px;cursor:pointer}.btn:hover{background:var(--hover)}
.btn.done{border-color:var(--good);color:var(--good-ink)}
.help{position:relative;display:inline-block}
.help>summary{list-style:none;display:grid;place-items:center;width:20px;height:20px;border-radius:50%;
border:1px solid var(--line2);color:var(--ink2);font-size:12px;cursor:pointer;font-weight:600}
.help>summary::-webkit-details-marker{display:none}.help>summary:hover{background:var(--hover)}
.help .pop{position:absolute;z-index:15;right:0;top:26px;width:min(430px,86vw);padding:14px 16px;border-radius:12px;
background:var(--surface);border:1px solid var(--line2);box-shadow:0 8px 28px rgba(0,0,0,.16);font-size:13px;
font-weight:400;color:var(--ink2);line-height:1.6}
.help .pop b{color:var(--ink)}.help .pop dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;margin:8px 0 0}
.help .pop dt{color:var(--ink);font-weight:600;white-space:nowrap}.help .pop dd{margin:0}
.empty{padding:28px 18px;text-align:center;color:var(--ink2)}

/* a document followed: who it is, the five steps, each step's facts */
.who{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px 24px;align-items:start;margin-top:16px}
@media (max-width:760px){.who{grid-template-columns:minmax(0,1fr)}}
.who h1{font-size:20px;line-height:1.35;overflow-wrap:anywhere}
.who .about{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;margin-top:8px;color:var(--ink2);font-size:13px}
.who .addr{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-top:10px;font-size:12px;color:var(--ink3);
min-width:0}.who .addr a.url{display:inline-flex;align-items:center;gap:5px;min-width:0;max-width:min(100%,760px)}
.who .addr a.url span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.steps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0;list-style:none;margin:16px 0 0;padding:0}
.steps li{position:relative;min-width:0}
.steps li+li::before{content:"";position:absolute;left:calc(-50% + 17px);right:calc(50% + 17px);top:16px;height:2px;
background:var(--line2)}
.steps a,.steps .a{display:flex;flex-direction:column;align-items:center;gap:5px;padding:0 6px 4px;text-decoration:none;
text-align:center;min-width:0}
.steps .dot{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--surface);
border:2px solid var(--line2);color:var(--ink3);position:relative;z-index:1}
.steps .ok .dot{background:var(--good);border-color:var(--good);color:var(--on-good)}
.steps .warn .dot{background:var(--warn);border-color:var(--warn);color:var(--on-warn)}
.steps .bad .dot{background:var(--bad);border-color:var(--bad);color:var(--on-bad)}
.steps b{font-size:14px}.steps small{display:block;color:var(--ink2);font-size:12px;max-width:100%;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.steps a:hover b{text-decoration:underline}.steps .a b{color:var(--ink3);font-weight:500}
@media (max-width:640px){.steps small{display:none}}
.step{margin-top:16px;scroll-margin-top:64px}
.step>h2{display:flex;align-items:center;gap:10px;padding:14px 18px 0;font-size:16px}
.step>h2 .n{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--hover);
font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.step>h2 .side{margin-left:auto;font-weight:400;font-size:12px;color:var(--ink3)}
.step .in{padding:12px 18px 16px}
.fold{margin-top:12px}.fold>summary{cursor:pointer;color:var(--ink2);font-size:13px;list-style:none;display:inline-flex;
align-items:center;gap:5px}.fold>summary::-webkit-details-marker{display:none}
.fold>summary::before{content:"›";display:inline-block;transition:transform .15s;font-size:14px}
.fold[open]>summary::before{transform:rotate(90deg)}.fold>summary:hover{color:var(--ink)}
.fold .facts{margin-top:12px}
table.grid{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}
table.grid td,table.grid th{padding:6px 10px 6px 0;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;
overflow-wrap:anywhere}
table.grid th{color:var(--ink3);font-weight:600;font-size:12px;white-space:nowrap}
table.grid tr:last-child td{border-bottom:0}
.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:12px}
@media (max-width:900px){.two{grid-template-columns:minmax(0,1fr)}}
.pane{border:1px solid var(--line);border-radius:10px;overflow:hidden;min-width:0;display:flex;flex-direction:column}
.pane>header{display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid var(--line);
background:var(--sunk);font-size:12px;font-weight:600;color:var(--ink2)}
.pane>header .side{margin-left:auto;font-weight:400;color:var(--ink3)}
.pane pre{margin:0;padding:12px;max-height:min(56vh,560px);overflow:auto;white-space:pre-wrap;word-break:break-word;
font:13px/1.65 var(--mono)}
.pane .none{padding:18px 12px;color:var(--ink2);font-size:13px}
iframe.orig{display:block;width:100%;height:min(56vh,560px);border:0;background:#fff}
img.orig{display:block;max-width:100%;max-height:min(56vh,560px);margin:0 auto;background:#fff}
pre.raw{white-space:pre-wrap;word-break:break-word;margin:8px 0 0;font:13px/1.65 var(--mono)}
.meter{display:grid;grid-template-columns:auto minmax(60px,200px) auto;gap:5px 12px;align-items:center;font-size:13px;
font-variant-numeric:tabular-nums}
.meter .t{height:8px;border-radius:4px;background:var(--mark2);overflow:hidden}
.meter .t i{display:block;height:100%;border-radius:4px;background:var(--amt)}.meter .t i.w{background:var(--warn)}
.meter span:first-child{color:var(--ink2)}
.tline{position:relative;height:76px;margin:8px 6px 0}
.tline .axis{position:absolute;left:0;right:0;top:36px;height:2px;background:var(--line2)}
.tline .span{position:absolute;top:31px;height:12px;border-radius:6px;background:var(--amt);opacity:.55;min-width:6px}
.tline .pt{position:absolute;top:30px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:var(--amt);
box-shadow:0 0 0 2px var(--surface)}
.tline .now{position:absolute;top:14px;bottom:14px;width:0;border-left:2px dotted var(--ink3)}
.tline .lab{position:absolute;white-space:nowrap;font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.tline .lab b{color:var(--ink);font-weight:650}.tline .lab.up{top:0}.tline .lab.dn{top:52px}
.tline .lab.l{transform:translateX(0)}.tline .lab.c{transform:translateX(-50%)}.tline .lab.r{transform:translateX(-100%)}
.strip{display:flex;gap:2px;height:26px;margin-top:10px;border-radius:6px;overflow:hidden}
.strip a{display:grid;place-items:center;min-width:3px;background:var(--amt2);color:var(--ink2);font-size:11px;
text-decoration:none;font-variant-numeric:tabular-nums;overflow:hidden}
.strip a:hover,.strip a.on{background:var(--amt);color:var(--on-amt)}.strip.dense{gap:1px}.strip.dense a{font-size:0}
.chunks.many{max-height:430px;overflow:auto;margin-top:12px;padding-right:6px}.chunks.many .chunk:first-child{margin-top:0}
.chunk{margin-top:12px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.chunk>summary{display:flex;align-items:center;gap:10px;padding:7px 12px;background:var(--sunk);cursor:pointer;
font-size:13px;list-style:none}.chunk>summary::-webkit-details-marker{display:none}
.chunk>summary b{font-variant-numeric:tabular-nums;white-space:nowrap}.chunk>summary .side{margin-left:auto;color:var(--ink3);white-space:nowrap}
.chunk>summary .peek{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.chunk pre{margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;font:13px/1.65 var(--mono)}
.chunk .fold{margin:0;padding:0 12px 10px}
.rel{display:flex;flex-direction:column;gap:2px;margin-top:6px}
.rel a{display:flex;align-items:center;gap:8px;padding:5px 8px;margin:0 -8px;border-radius:8px;text-decoration:none;
font-size:13px;min-width:0}.rel a:hover{background:var(--hover)}
.rel a span:first-of-type{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.rel a small{margin-left:auto;color:var(--ink3);white-space:nowrap;padding-left:10px}.rel .ic{color:var(--ink3)}
.found{padding:6px}
.found a{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:4px 12px;align-items:center;padding:8px 12px;
border-radius:10px;text-decoration:none}.found a:hover{background:var(--hover)}
.found a .ic{color:var(--ink3)}.found small{color:var(--ink3);white-space:nowrap}
.found a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gaps{margin:16px 0 0;padding:12px 16px 12px 34px;border:1px dashed var(--line2);border-radius:12px;color:var(--ink2);
font-size:13px}.gaps li{margin:2px 0}

/* the floating word on a mark: 資料總覽's card (deploy/lab-overview/page.html's .tip) */
.tip{position:fixed;z-index:60;max-width:320px;padding:8px 10px;border-radius:10px;background:var(--surface);
border:1px solid var(--line2);color:var(--ink2);font-size:12px;line-height:1.5;white-space:pre-line;pointer-events:none;
box-shadow:0 10px 28px rgba(0,0,0,.16)}
.tip[hidden]{display:none}.tip b{display:block;color:var(--ink);font-size:13px;font-weight:650;margin-bottom:2px}.tip b+span{display:block}
"""

# The script is small and the pages work without it (every value it shows is in the page as text or in a data-tip):
# three keys, copying an id, the scoreboard's filling in, a chunk opened from its strip. The floating word is TIP_JS.
JS = """
(()=>{
addEventListener('keydown',e=>{if(e.metaKey||e.ctrlKey||e.altKey||e.target.closest('input,textarea,select'))return;
if(e.key==='/'){const i=document.querySelector('form.ask input');if(i){e.preventDefault();i.focus();i.select()}}
const go=k=>{const a=document.querySelector('a[data-key="'+k+'"]:not([aria-disabled])');if(a)location.href=a.href};
if(e.key==='ArrowLeft')go('prev');if(e.key==='ArrowRight')go('next')});
addEventListener('click',e=>{const b=e.target.closest('[data-copy]');if(b&&navigator.clipboard){e.preventDefault();
navigator.clipboard.writeText(b.getAttribute('data-copy')).then(()=>{b.classList.add('done');
setTimeout(()=>b.classList.remove('done'),1200)})}
const c=e.target.closest('a[data-chunk]');if(c){const d=document.getElementById(c.getAttribute('data-chunk'));
if(d){e.preventDefault();d.open=true;d.scrollIntoView({block:'nearest',behavior:'smooth'});
document.querySelectorAll('.strip a.on').forEach(a=>a.classList.remove('on'));c.classList.add('on')}}
const h=document.querySelector('.help[open]');if(h&&!e.target.closest('.help'))h.open=false});
const jump=()=>{let d=null;try{d=document.getElementById(decodeURIComponent(location.hash.slice(1)))}catch(_){}
if(d&&d.tagName==='DETAILS'){d.open=true;d.scrollIntoView({block:'center'})}};
addEventListener('hashchange',jump);jump();
const board=document.querySelector('[data-board]');
if(board){const SAID=it=>it.rank?'標準答案第 '+it.rank+' 名':it.grade==='wait'?'還在算':it.grade==='failed'?'這一題沒搜成'
:'標準答案沒排上';
const paint=s=>{for(const it of s.items){const a=board.querySelector('[data-q="'+CSS.escape(it.id)+'"]');if(!a)continue;
a.setAttribute('data-grade',it.grade);const g=a.querySelector('.g');g.className='g g-'+it.grade;
g.setAttribute('data-tip',SAID(it));g.textContent=it.grade==='wait'?'':it.grade==='failed'?'!':it.rank?it.rank:'—'}
let waiting=s.total;for(const k of ['first','top3','top','out','failed']){const n=s.counts[k]||0;waiting-=n;
const seg=board.querySelector('.seg.g-'+k);if(seg){seg.style.flexGrow=n;seg.setAttribute('data-n',n);seg.textContent=n}}
const w=board.querySelector('.seg.g-wait');if(w){w.style.flexGrow=waiting;w.setAttribute('data-n',waiting);
w.setAttribute('data-tip','還在算：'+waiting+' 題')}
const p=board.querySelector('[data-prog]');if(p){p.hidden=!waiting;p.textContent='正在重算 '+(s.total-waiting)+' / '+s.total}};
const poll=()=>fetch(board.getAttribute('data-board'),{cache:'no-store'}).then(r=>r.json()).then(s=>{paint(s);
if(s.done<s.total)setTimeout(poll,1500)}).catch(()=>setTimeout(poll,5000));
if(board.hasAttribute('data-running'))poll()}})();
"""

# The floating word, the same lines as Datasette's deploy/lab-inspect/datasette/static/tip.js (tests/ops/
# test_deploy_files.py keeps the two equal): data-tip (the sentence) and data-tip-h (a name over it), set as text.
TIP_JS = """// The floating word of these panels, the same as 資料總覽's (owner 11-15: 「像资料总览里有很多鼠标放在某个什么才出现悬浮窗来
// 详细解释的」): a page shows a short name and the detail floats beside the pointer over it. An element says it with
// data-tip (the sentence) and data-tip-h (a name over it), both set as text, never as markup. 檢視台 runs the same
// lines (nknu.ops.look.TIP_JS; tests/ops/test_deploy_files.py keeps the two equal). 【Claude】
(() => {
  const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; tip.setAttribute('role', 'tooltip');
  let cur = null;
  const pick = t => t && t.closest ? t.closest('[data-tip],[data-tip-h]') : null;
  const hide = () => { tip.hidden = true; cur = null; };
  const fill = el => {
    tip.textContent = '';
    const h = el.getAttribute('data-tip-h'), t = el.getAttribute('data-tip');
    if (h) { const b = document.createElement('b'); b.textContent = h; tip.append(b); }
    if (t) { const p = document.createElement('span'); p.textContent = t; tip.append(p); }
  };
  const place = (x, y) => {
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + 14, top = y + 16;
    if (left + w > innerWidth - 8) left = Math.max(8, x - w - 12);
    if (top + h > innerHeight - 8) top = Math.max(8, y - h - 12);
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  };
  const show = el => { if (el !== cur) { fill(el); cur = el; } tip.hidden = !tip.childNodes.length; };
  const start = () => {
    document.body.append(tip);
    addEventListener('pointermove', e => { const el = pick(e.target); if (!el) { if (cur) hide(); return; } show(el); place(e.clientX, e.clientY); });
    addEventListener('focusin', e => { const el = pick(e.target); if (!el) return; show(el); const r = el.getBoundingClientRect(); place(r.left, r.bottom - 8); });
    addEventListener('focusout', hide);
    addEventListener('scroll', hide, true);
    document.addEventListener('pointerleave', hide);
  };
  if (document.body) start(); else addEventListener('DOMContentLoaded', start);
})();
"""

_ICONS = {
    "notice": "<path d='M2.5 6.4v3.2h2l4.3 2.9v-9L4.5 6.4z'/><path d='M11.3 5.6a3.3 3.3 0 0 1 0 4.8'/>",
    "page": "<rect x='3' y='2' width='10' height='12' rx='1.6'/><path d='M5.6 5.5h4.8M5.6 8h4.8M5.6 10.5h2.8'/>",
    "attachment": "<path d='m10.9 6.2-4 4a1.3 1.3 0 0 1-1.9-1.9l4.6-4.6a2.4 2.4 0 0 1 3.4 3.4l-4.8 4.8a3.5 3.5 0 0 1-5-5l3.9-3.9'/>",
    "event": "<rect x='2.5' y='3.5' width='11' height='10' rx='1.6'/><path d='M2.5 6.7h11M5.5 2v2.6M10.5 2v2.6'/>",
    "link": "<path d='M9.2 3H13v3.8M13 3 7.6 8.4M11 9.4V12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h2.6'/>",
    "search": "<circle cx='7' cy='7' r='4.3'/><path d='m10.3 10.3 3.2 3.2'/>",
    "trace": "<circle cx='3.5' cy='4' r='1.6'/><circle cx='12.5' cy='12' r='1.6'/><path d='M5.1 4h4.4a2.2 2.2 0 0 1 0 4.4H6.5a2.2 2.2 0 0 0 0 4.4h4.4'/>",
    "ok": "<path d='m3.5 8.4 3 3 6-6.6'/>",
    "no": "<path d='m4.2 4.2 7.6 7.6M11.8 4.2l-7.6 7.6'/>",
    "warn": "<path d='M8 3.2v5.6'/><circle cx='8' cy='11.8' r='.5' fill='currentColor'/>",
    "dash": "<path d='M4.5 8h7'/>",
    "left": "<path d='m9.8 3.5-4.4 4.5 4.4 4.5'/>",
    "right": "<path d='m6.2 3.5 4.4 4.5-4.4 4.5'/>",
    "out": "<path d='M6.5 3.5H12.5V9.5M12.5 3.5 4 12'/>",
    "copy": "<rect x='5.5' y='5.5' width='8' height='8' rx='1.4'/><path d='M3.5 10.5h-.4a.9.9 0 0 1-.9-.9V3.1a.9.9 0 0 1 .9-.9h6.5a.9.9 0 0 1 .9.9v.4'/>",
    "star": "<path d='m8 2.3 1.7 3.5 3.8.5-2.8 2.7.7 3.8L8 11l-3.4 1.8.7-3.8-2.8-2.7 3.8-.5z' fill='currentColor' stroke='none'/>",
}
KIND_ICON = {"notice": "notice", "page": "page", "attachment": "attachment", "event": "event", "link": "link"}


def e(value: object) -> str:
    """value as text in a page ('' for None)."""
    return html.escape("" if value is None else str(value))


def icon(name: str, size: int = 14) -> str:
    """One of _ICONS as an inline drawing in the text's own colour ('' for a name there is none of)."""
    shape = _ICONS.get(name)
    if shape is None:
        return ""
    return (f"<svg class='ic' viewBox='0 0 16 16' width='{size}' height='{size}' fill='none' stroke='currentColor' "
            f"stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{shape}</svg>")


@functools.cache
def _converter(config: str):
    """An OpenCC converter, or None without the package (the Mac's plain environment)."""
    try:
        from opencc import OpenCC
    except ImportError:
        return None
    return OpenCC(config)


def s2t(text: str) -> str:
    """text with Simplified characters turned Traditional: the owner types Simplified, the school's titles are
    Traditional (a title search tries both)."""
    conv = _converter("s2t")
    return conv.convert(text) if conv is not None else text


def t2s(text: str) -> str:
    """text with Traditional characters turned Simplified: what a search compares on both sides, so that the owner's
    Simplified 台湾 finds the school's 台灣 (OpenCC s2t would make it 臺灣, which the school's titles seldom write)."""
    conv = _converter("t2s")
    return conv.convert(text) if conv is not None else text


def tw(value: object) -> str:
    """value as a page shows it, in Traditional Chinese with Taiwan usage ('' for None; as it is without OpenCC). The
    pages are in Traditional (owner 11-15: 「可视化dashboard全部换繁体字」); what they show of the Simplified our own
    system writes (the crawl's and the export's values, the build's kinds and reason keys, the test questions) is
    converted for display only, never where the code matches it, links it or searches it."""
    text = "" if value is None else str(value)
    conv = _converter("s2tw")
    return conv.convert(text) if conv is not None and text else text


def kind_tag(kind: str | None, words: dict[str, str]) -> str:
    """A document's kind as its icon and its word (words: nknu.ops.trace.KINDS)."""
    if not kind:
        return ""
    return f"<span class='kind'>{icon(KIND_ICON.get(kind, 'page'), 13)}{e(words.get(kind, kind))}</span>"


def shell(title: str, body: str, db: dict, status: int = 200, *, tab: str = "") -> HTMLResponse:
    """The page around body: the panels' one top bar (nknu.ops.theme.bar, with when the library was built at its end)
    and, when the installed copy lags the pipeline's code, a note saying so (review 11-15: nothing showed it)."""
    built = (db.get("built_at") or "")[:16].replace("T", " ")
    state = db.get("install")
    hover = "\n".join(x for x in (
        f"正式庫 {built} 建好" + (f"（{db['schema']}）" if db.get("schema") else ""),
        f"檢視台的程式 {state['installed'][:16].replace('T', ' ')} 裝" if state else "") if x)
    meta = f"<span data-tip='{e(hover)}'>資料截至 {e(built)}</span>" if built else ""
    note = ""
    if state and state["drift"]:
        some = "、".join(state["drift"][:3]) + (" 等" if len(state["drift"]) > 3 else "")
        note = (f"<div class='note'>{icon('warn', 16)}<div data-tip='{len(state['drift'])} 個檔不一樣：{e(some)}\n"
                "照 deploy/lab-inspect/README.md「換程序」重裝一次就一致了'>檢視台的程式和建庫的程式不一樣："
                "分數和「沒進庫的原因」可能不是現在的規則</div></div>")
    page = ("<!doctype html><html lang='zh-Hant-TW'><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{e(title)}</title>"
            f"<style>{THEME}{CSS}</style>{theme.bar(tab, 'ops', meta)}"
            f"<main>{note}{body}</main><script>{JS}</script><script>{TIP_JS}</script></html>")
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"})
