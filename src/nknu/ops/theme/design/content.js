// 內容 (/sites): the 抓取覆蓋 card's clicks. The card is drawn by the server
// (nknu.ops.content_band); the drawer (window.RAWSHOW, the drawers layer) and the night view (window.NM, the night layer)
// are other layers, used when they are there: without them the rows stay plain and 定時更新 is an ordinary link.
(()=>{
  const raw=p=>{if(window.RAWSHOW)window.RAWSHOW('crawl',p)};
  const preset=li=>li.dataset.label?{label:li.dataset.label}:li.dataset.bucket?{bucket:li.dataset.bucket}:{};
  // a cause's row opens its addresses; the headline (not a night's column of the line) opens every address not fetched
  const act=(e,t)=>{
    const card=t.closest&&t.closest('#csCard');if(!card)return false;
    const go=t.closest('.cs-go');
    if(go){if(window.NM){e.preventDefault();window.NM.set(true)}return true}
    const li=t.closest('.cs-go-li');
    if(li){raw(preset(li));return true}
    const cv=t.closest('.cs-cov');
    if(cv&&!t.closest('.h')){raw({});return true}
    return false};
  document.addEventListener('click',e=>{act(e,e.target)});
  document.addEventListener('keydown',e=>{
    if(e.key!=='Enter'&&e.key!==' ')return;
    const t=e.target;if(!t.closest||!t.closest('#csCard .cs-go-li,#csCard .cs-cov'))return;
    e.preventDefault();act(e,t)});
  // /sites?date=<run day> (&step=) is read by the night layer itself once it is up (night.js), never twice
})();
