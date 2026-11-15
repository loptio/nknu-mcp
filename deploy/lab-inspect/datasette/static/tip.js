// The floating word of these panels, the same as 資料總覽's (owner 11-15: 「像资料总览里有很多鼠标放在某个什么才出现悬浮窗来
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
