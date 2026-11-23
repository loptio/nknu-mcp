// 總覽, the 11-23 design (research/design_2026-11-23/index.html): what its scripts did in the copy, now on live data.
// The page is drawn by the server (nknu.ops.panorama, calendar, visits); this layer adds the browser's part: the hover
// cards read for the eye, the calendar's switch and countdown, the visits card (the live line from /api/usage/live,
// the month's line with ECharts), the three tabs, 標成已知's reasons, the log viewer over /api/logs and its 放大,
// the frame of 內容's chart with this machine's tiles under its donut, and a reload only when the night changed.
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const keep = {
    get(k) { try { return sessionStorage.getItem('nk-home-' + k); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem('nk-home-' + k, v); } catch (e) { /* private window: nothing kept */ } },
    drop(k) { try { sessionStorage.removeItem('nk-home-' + k); } catch (e) { /* nothing kept */ } },
  };

  // ---- the hover cards read for the eye (richtip, owner 11-23: 「很多悬浮窗的文字都太杂了」): the shared card (look.TIP_JS)
  // fills its words as text; where 內容's RICHTIP is loaded the same card shows them as aligned rows. A tip of prose
  // marked data-tip-full (a card's ? help, a row's reason and a night's card in the source's own words) is shown whole,
  // a paragraph a line, in the same card's style: RICHTIP cuts any line past 90 characters.
  (() => {
    let cur = null;
    const pick = t => (t && t.closest ? t.closest('[data-tip],[data-tip-h]') : null);
    const whole = el => {
      const h = el.getAttribute('data-tip-h'), t = el.getAttribute('data-tip') || '';
      return (h ? `<b class='rt-h'>${esc(h)}</b>` : '') + t.split('\n').map(x => x.trim()).filter(Boolean).map(x => `<p class='rt-p'>${esc(x)}</p>`).join('');
    };
    const place = (tip, x, y) => {
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let left = x + 14, top = y + 16;
      if (left + w > innerWidth - 8) left = Math.max(8, x - w - 12);
      if (top + h > innerHeight - 8) top = Math.max(8, y - h - 12);
      tip.style.left = left + 'px'; tip.style.top = top + 'px';
    };
    const up = (el, x, y) => {
      const tip = document.querySelector('body > .tip');
      if (!tip || tip.hidden || !el || typeof window.RICHTIP !== 'function') return;
      if (el !== cur || !tip.querySelector('.rt-h,.rt-r,.rt-p')) { cur = el; tip.innerHTML = el.hasAttribute('data-tip-full') ? whole(el) : window.RICHTIP(el); }
      place(tip, x, y);
    };
    addEventListener('pointermove', ev => { const el = pick(ev.target); if (!el) { cur = null; return; } up(el, ev.clientX, ev.clientY); });
    addEventListener('focusin', ev => { const el = pick(ev.target); if (!el) return; const r = el.getBoundingClientRect(); up(el, r.left, r.bottom - 8); });
    addEventListener('focusout', () => { cur = null; });
  })();

  // ---- the calendar: 週 / 月 (week first, not remembered) and the countdown to the next 01:00 in Taipei
  const cal = $('calendar');
  const calView = v => {
    if (!cal) return;
    cal.dataset.view = v;
    cal.querySelectorAll('.cal-switch button').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v)));
  };
  if (cal) {
    cal.querySelectorAll('.cal-switch button').forEach(b => b.addEventListener('click', () => calView(b.dataset.v)));
    const next = $('calNext');
    if (next) {
      const tick = () => {
        const tp = new Date(new Date().toLocaleString('en-US', {timeZone: 'Asia/Taipei'})), t = new Date(tp);
        t.setHours(1, 0, 0, 0);
        if (t <= tp) t.setDate(t.getDate() + 1);
        const s = Math.floor((t - tp) / 1000), p = x => String(x).padStart(2, '0');
        next.textContent = `${p(Math.floor(s / 3600))}:${p(Math.floor(s % 3600 / 60))}:${p(s % 60)}`;
      };
      tick(); setInterval(tick, 1000);
    }
  }

  // ---- 網站訪問量
  const vs = document.querySelector('.vs');
  let vsView = () => {};
  if (vs && $('vs-data')) {
    const D = JSON.parse($('vs-data').textContent);
    const pct = v => (v == null ? '—' : (v * 100).toFixed(v < 0.1 ? 1 : 0));
    const slots = D.slots || 4;
    const show = day => {
      const v = (D.days || {})[day] || {};
      if (!day) { $('vs-day').textContent = '沒有紀錄'; return; }
      const [, m, d] = day.split('-');
      $('vs-day').textContent = `${+m}/${+d}` + (day === D.today ? '（今天，還沒過完）' : '') + (v.calls != null ? ` · ${v.calls.toLocaleString()} 次` : '');
      $('vs-load').textContent = pct(v.load_p95);
      $('vs-meter').style.width = Math.min(100, (v.load_p95 || 0) * 100) + '%';
      vs.querySelector('.vs-days .vs-main').dataset.tip = `有查詢的每一分鐘裡，${slots} 個搜尋名額平均被占用幾成，取 P95。超過 100% 表示有請求在排隊。`
        + (v.load_max != null ? `\n最忙一分鐘 ${pct(v.load_max)}%` : '');
      $('vs-qpm').textContent = v.qpm_peak ?? '—'; $('vs-qpm-at').textContent = v.qpm_peak_at || '';
      $('vs-conc').textContent = v.conc_peak ?? '—';
      $('vs-p90').textContent = v.p90_ms != null ? (v.p90_ms / 1000).toFixed(1) : '—';
      $('vs-lat-card').dataset.tip = v.p90_ms != null
        ? `九成請求在這時間內回應（含排隊）\nP50 ${((v.p50_ms || 0) / 1000).toFixed(1)} s · P99 ${((v.p99_ms || 0) / 1000).toFixed(1)} s` : '';
      $('vs-err').textContent = v.err_rate != null ? (v.err_rate * 100).toFixed(1) : '—';
    };
    show(D.pick);
    // 每日: the month's calls a day, zooming and panning; a click on a day fills the five numbers
    let chart = null;
    const el = $('vs-chart');
    const draw = () => {
      if (!window.echarts || !el) return;
      const ink3 = css('--ink3'), line = css('--line'), q4 = css('--q4'), surf = css('--surface'), ink = css('--ink');
      const ti = D.dates.indexOf(D.today);
      chart = chart || window.echarts.init(el, null, {renderer: 'svg'});
      chart.setOption({animation: false, grid: {left: 44, right: 14, top: 16, bottom: 54},
        tooltip: {trigger: 'axis', backgroundColor: surf, borderColor: line, textStyle: {color: ink, fontSize: 12},
          formatter: p => { const i = p[0].dataIndex, c = D.calls[i]; return `${D.labels[i]}<br>` + (c == null ? '沒有紀錄' : `${c.toLocaleString()} 次`) + (i === ti ? '（今天還沒過完）' : ''); }},
        xAxis: {type: 'category', data: D.labels, boundaryGap: false, axisLine: {lineStyle: {color: line}}, axisTick: {show: false},
          axisLabel: {color: ink3, fontSize: 11}},
        yAxis: {type: 'value', splitNumber: 3, axisLabel: {color: ink3, fontSize: 11}, splitLine: {lineStyle: {color: line}}},
        dataZoom: [{type: 'inside', zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false},
          {type: 'slider', height: 16, bottom: 8, backgroundColor: 'transparent', borderColor: line, fillerColor: q4 + '2e', handleSize: 16,
            handleStyle: {color: surf, borderColor: ink3}, moveHandleSize: 0, showDetail: false, brushSelect: false,
            dataBackground: {lineStyle: {color: q4, opacity: .5}, areaStyle: {color: q4, opacity: .12}}, textStyle: {color: ink3, fontSize: 10}}],
        series: [{type: 'line', data: D.calls.map((c, i) => (i === ti && c != null ? {value: c, symbol: 'emptyCircle', itemStyle: {borderType: 'dashed'}} : c)),
          connectNulls: false, smooth: false, symbol: 'circle', symbolSize: 8, showSymbol: true,
          lineStyle: {color: q4, width: 2.5}, itemStyle: {color: q4, borderColor: surf, borderWidth: 2},
          areaStyle: {color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [{offset: 0, color: q4 + '55'}, {offset: 1, color: q4 + '00'}])},
          emphasis: {scale: 1.4}}]}, true);
      chart.getZr().off('click');
      chart.getZr().on('click', ev => {
        const p = [ev.offsetX, ev.offsetY];
        if (!chart.containPixel('grid', p)) return;
        const i = Math.round(chart.convertFromPixel({seriesIndex: 0}, p)[0]);
        if (D.calls[i] != null) show(D.dates[i]);
      });
      chart.resize();
    };
    const whenCharts = cb => {
      if (window.echarts) { cb(); return; }
      const s = $('echarts-lib');
      if (s) s.addEventListener('load', cb, {once: true});
    };
    // 即時: requests a second over the last five minutes, each point a 30-second weighted average, sliding left
    const cv = $('vs-ecg'), cx = cv ? cv.getContext('2d') : null, WIN = 300, SM = 30;
    const S = new Map();
    let end = 0, got = 0, read = true, poll = 3000;
    const at = i => S.get(i) || 0;
    const rate = i => { let s = 0, w = 0; for (let k = 0; k < SM; k++) { const q = SM - k; s += at(i - k) * q; w += q; } return s / w; };
    // the log unread, the address not answering or answering wrong: said on the card, never a flat line of 0
    const lost = () => { read = false; const none = $('vs-ecg-none'); if (none) none.hidden = false; };
    const pull = async () => {
      if (!document.hidden && vs.dataset.v !== 'day') {
        try {
          const r = await fetch('/api/usage/live', {cache: 'no-store'});
          if (!r.ok) lost();
          else {
            const j = await r.json();
            read = j.read !== false; poll = j.poll_ms || 3000;
            const per = j.per_sec || [], first = j.end - per.length + 1;
            per.forEach((v, k) => S.set(first + k, v));
            for (const k of [...S.keys()]) if (k < first) S.delete(k);
            end = j.end; got = performance.now();
            const none = $('vs-ecg-none'); if (none) none.hidden = read;
          }
        } catch (e) { lost(); }  // the next ask tries again
      }
      setTimeout(pull, poll);
    };
    const frame = now => {
      requestAnimationFrame(frame);
      if (!cx || document.hidden || vs.dataset.v === 'day') return;
      const W = cv.clientWidth, H = cv.clientHeight, dpr = devicePixelRatio || 1;
      if (!W || !H) return;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
      cx.setTransform(dpr, 0, 0, dpr, 0, 0); cx.clearRect(0, 0, W, H);
      const head = end ? end + Math.min((now - got) / 1000, poll / 1000 + 1) : 0, last = Math.min(Math.floor(head), end);
      const padL = 36, padR = 12, padT = 14, padB = 22, iw = W - padL - padR, ih = H - padT - padB;
      const pts = []; if (end && read) for (let i = last - WIN; i <= last; i++) pts.push([i, rate(i)]);
      const top = Math.max(0.5, ...pts.map(p => p[1])) * 1.25, X = i => padL + (i - (head - WIN)) / WIN * iw, Y = v => padT + ih - v / top * ih;
      const ink3 = css('--ink3'), line = css('--line'), q4 = css('--q4');
      cx.font = '11px ' + (css('--font') || 'system-ui'); cx.fillStyle = ink3; cx.strokeStyle = line; cx.lineWidth = 1;
      for (const f of [0, .5, 1]) {
        const v = top / 1.25 * f, y = Y(v);
        cx.beginPath(); cx.moveTo(padL, y + .5); cx.lineTo(W - padR, y + .5); cx.stroke();
        cx.textAlign = 'right'; cx.fillText(v.toFixed(v < 1 ? 1 : 0), padL - 6, y + 4);
      }
      cx.textAlign = 'center'; for (let m = 5; m >= 1; m--) { const x = padL + (WIN - m * 60) / WIN * iw; cx.fillText(`-${m} 分`, x, H - 6); }
      cx.textAlign = 'right'; cx.fillText('現在', W - padR, H - 6);
      if (!pts.length) { ['vs-rps', 'vs-60', 'vs-peak'].forEach(id => { $(id).textContent = '—'; }); return; }  // nothing measured yet, or unread
      const curve = () => { cx.beginPath(); pts.forEach(([i, v], k) => { const x = X(i), y = Y(v); if (!k) { cx.moveTo(x, y); return; } const [pi, pv] = pts[k - 1], px = X(pi), py = Y(pv); cx.quadraticCurveTo(px, py, (px + x) / 2, (py + y) / 2); }); };
      cx.save(); cx.beginPath(); cx.rect(padL, 0, iw, H); cx.clip();
      const g = cx.createLinearGradient(0, padT, 0, padT + ih); g.addColorStop(0, q4 + '55'); g.addColorStop(1, q4 + '00');
      curve(); cx.lineTo(X(last), padT + ih); cx.lineTo(X(last - WIN), padT + ih); cx.closePath(); cx.fillStyle = g; cx.fill();
      curve(); cx.strokeStyle = q4; cx.lineWidth = 2; cx.lineJoin = 'round'; cx.stroke(); cx.restore();
      const cur = rate(last), xc = X(last), yc = Y(cur); cx.fillStyle = q4; cx.beginPath(); cx.arc(xc, yc, 3.5, 0, 7); cx.fill();
      const lastMin = pts.slice(-60), qpm = lastMin.reduce((a, [i]) => a + at(i), 0), peak = Math.max(...lastMin.map(([i]) => at(i)));
      $('vs-rps').textContent = cur.toFixed(1); $('vs-60').textContent = qpm; $('vs-peak').textContent = peak;
    };
    vsView = v => {
      vs.dataset.v = v;
      vs.querySelectorAll('.vs-switch button').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v)));
      if (v === 'day') whenCharts(draw);  // drawn once it has a size: the zoom starts on the whole month
    };
    vs.querySelectorAll('.vs-switch button').forEach(b => b.addEventListener('click', () => vsView(b.dataset.v)));
    pull(); requestAnimationFrame(frame);
    addEventListener('resize', () => chart && chart.resize());
    new MutationObserver(() => chart && draw()).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  }

  // ---- 日誌: every line of the night's logs, a page at a time from /api/logs (nknu.ops.logs.api_window, the one handler
  // 內容's night log asks too: its names date, lv, src, q, n, before), newest first
  const LV = [['error', '錯誤', 'var(--bad-s)'], ['warn', '警告', 'var(--warn-s)'], ['info', '資訊', 'var(--q4)'], ['debug', '除錯', 'var(--line2)']];
  const SRC = [['crawl', '爬取'], ['export', '導出'], ['totext', '轉文字'], ['build', '建庫'], ['ingest', '進庫']];
  const LW = {error: 'ERROR', warn: 'WARN', info: 'INFO', debug: 'DEBUG'}, SW = Object.fromEntries(SRC);
  window.LOGVIEW = (root, opts = {}) => {
    const on = {lv: new Set(['error', 'warn', 'info']), src: new Set(SRC.map(s => s[0]))};
    const q$ = c => root.querySelector(c), src = opts.src || root.dataset.src || '/api/logs', size = +(root.dataset.page || 500);
    let q = '', rows = [], more = false, next = null, busy = false, started = false, seq = 0, timer = 0;
    const url = extra => src + '?' + new URLSearchParams({...(opts.params || {}), lv: [...on.lv].join(','),
      src: [...on.src].join(','), q, n: String(size), ...extra});
    const chips = counts => {
      const box = (sel, list, set, cnt) => {
        const b = q$(sel); if (!b) return;
        b.innerHTML = list.map(([k, w, c]) => `<button type='button' data-k='${k}' aria-pressed='${set.has(k)}'>${c ? `<i style='background:${c}'></i>` : ''}${w} <small>${(cnt[k] || 0).toLocaleString()}</small></button>`).join('');
        b.querySelectorAll('button').forEach(x => { x.onclick = () => { set.has(x.dataset.k) ? set.delete(x.dataset.k) : set.add(x.dataset.k); load(true); }; });
      };
      box('.lv-chips[id$=lv]', LV, on.lv, counts.levels || {});
      box('.lv-chips[id$=src]', SRC, on.src, counts.sources || {});
    };
    const hist = h => {
      const top = Math.max(1, ...h.map(x => (x.error || 0) + (x.warn || 0) + (x.info || 0) + (x.debug || 0)));
      const el = q$('.lv-hist'); if (!el) return;
      el.innerHTML = h.map(x => `<div data-tip-h='${esc(String(x.t || '').slice(5, 16).replace('T', ' '))}' data-tip='錯誤 ${x.error || 0} · 警告 ${x.warn || 0} · 資訊 ${x.info || 0}'>`
        + LV.map(([k, , c]) => (x[k] ? `<i style='height:${x[k] / top * 100}%;background:${c}'></i>` : '')).join('') + '</div>').join('');
    };
    const list = () => {
      let lastD = '';
      const el = q$('.lv-list');
      el.innerHTML = rows.length ? rows.map(r => {
        const d = String(r[0]).slice(5, 10), sep = d !== lastD ? `<div class='lv-day'>${esc(d)}</div>` : ''; lastD = d;
        const lw = r[1] === 'error' || r[1] === 'warn' ? `<b class='lv-lw'>${LW[r[1]]}</b>` : '';
        return sep + `<div class='lv-row' data-l='${esc(r[1])}'><span class='lv-t'>${esc(String(r[0]).slice(11, 19))}</span><span class='lv-s'>${esc(SW[r[2]] || r[2])}</span>`
          + `<span class='lv-m'>${lw}${esc(String(r[3]).replace(/\s+\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$/, ''))}</span></div>`;
      }).join('') : "<div class='lv-none'>沒有符合的紀錄</div>";
      el.querySelectorAll('.lv-row').forEach(x => { x.onclick = () => x.classList.toggle('open'); });
      const m = q$('.lv-more'); if (m) m.hidden = !more;
    };
    const meta = () => {
      const el = q$('.lv-meta'); if (!el || !rows.length) return;
      const t = x => String(x).slice(5, 16).replace('T', ' ');
      el.innerHTML = `<span>${esc(t(rows[rows.length - 1][0]))}</span><span>${rows.length.toLocaleString()} 行${more ? '（還有更早的）' : ''}</span><span>${esc(t(rows[0][0]))}</span>`;
    };
    const load = async reset => {
      if (busy && !reset) return;
      busy = true; const mine = ++seq;
      if (!reset && !next) { busy = false; return; }
      const extra = reset ? {} : {before: next};  // the window's own cursor for the page after (logs.window)
      try {
        const r = await fetch(url(extra), {cache: 'no-store'});
        const j = await r.json();
        if (mine !== seq) return;
        // every level or every source chip off: nothing is shown (the window would read none as its default)
        const page = on.lv.size && on.src.size ? (j.rows || []).slice().sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0)) : [];
        rows = reset ? page : rows.concat(page); more = !!j.more && !!page.length; next = j.next || null;
        if (reset) { chips(j.counts || {}); hist(j.hist || []); }
        list(); meta();
      } catch (e) {
        if (reset) q$('.lv-list').innerHTML = "<div class='lv-none'>讀不到日誌</div>";
      } finally { if (mine === seq) busy = false; }
    };
    const more$ = q$('.lv-more'), list$ = q$('.lv-list');
    if (more$) more$.onclick = () => load(false);
    if (list$) list$.addEventListener('scroll', () => { if (more && list$.scrollTop + list$.clientHeight > list$.scrollHeight - 40) load(false); });
    if (opts.find) opts.find.addEventListener('input', ev => { clearTimeout(timer); timer = setTimeout(() => { q = ev.target.value.trim(); load(true); }, 250); });
    return {start() { if (!started) { started = true; load(true); } }, reload: () => load(true)};
  };
  const lvRoot = $('lv');
  const logs = lvRoot ? window.LOGVIEW(lvRoot, {find: $('lv-q')}) : null;

  // ---- 待處理 | 日誌 | 已知: tabs (arrows move between them; #known / #log in the address open that one)
  const td = $('todo');
  const tabs = td ? [...td.querySelectorAll('.lg-tabs [role=tab]')] : [];
  const openTab = (p, focus) => {
    if (!td) return;
    tabs.forEach(b => { const on = b.dataset.p === p; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
    td.querySelectorAll('.lg-pane').forEach(x => { x.hidden = x.dataset.p !== p; });
    td.dataset.tab = p;
    if (p === 'log' && logs) logs.start();
    window.dispatchEvent(new Event('resize'));
  };
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => openTab(b.dataset.p));
    b.addEventListener('keydown', ev => {
      const k = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}[ev.key];
      if (k === undefined) return;
      ev.preventDefault(); openTab(tabs[(k + tabs.length) % tabs.length].dataset.p, true);
    });
  });
  const fromHash = () => { const h = location.hash.slice(1); if (['todo', 'log', 'known'].includes(h)) openTab(h); };
  fromHash(); addEventListener('hashchange', fromHash);

  // ---- 標成已知: a reason picked fills the note (still typed in); the form floats over the page beside its row
  const placeForm = d => {
    const f = d.querySelector('form'), r = d.querySelector('summary').getBoundingClientRect();
    f.classList.add('tk-fixed');
    const w = f.offsetWidth, h = f.offsetHeight;
    f.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + 'px';
    f.style.top = (r.bottom + 4 + h > innerHeight - 8 ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
  };
  document.querySelectorAll('.td-known').forEach(d => {
    const f = d.querySelector('form'), note = f && f.querySelector('input[name=note]');
    if (!f || !note) return;
    f.querySelectorAll('.tk-opts button').forEach(b => b.addEventListener('click', () => {
      const on = b.getAttribute('aria-pressed') !== 'true';
      f.querySelectorAll('.tk-opts button').forEach(x => x.setAttribute('aria-pressed', 'false'));
      b.setAttribute('aria-pressed', String(on)); note.value = on ? b.textContent : ''; note.focus();
    }));
    d.addEventListener('toggle', () => {
      if (!d.open) { f.classList.remove('tk-fixed'); f.style.left = f.style.top = ''; return; }
      document.querySelectorAll('.td-known[open]').forEach(o => { if (o !== d) o.open = false; });
      placeForm(d);
    });
  });
  const closeForms = () => document.querySelectorAll('.td-known[open]').forEach(o => { o.open = false; });
  document.addEventListener('click', ev => { document.querySelectorAll('.td-known[open]').forEach(o => { if (!o.contains(ev.target)) o.open = false; }); }, true);
  addEventListener('keydown', ev => { if (ev.key !== 'Escape') return; const o = document.querySelector('.td-known[open]'); if (o) { o.open = false; o.querySelector('summary').focus(); } });
  document.querySelectorAll('#todo .lg-pane').forEach(p => p.addEventListener('scroll', closeForms));
  addEventListener('resize', closeForms);

  // ---- 日誌 放大: the card over the whole window, Esc or a click outside closes it (lvfix.py)
  (() => {
    const set = (card, on) => {
      if (!card) return;
      card.classList.toggle('lv-maxed', on); document.body.classList.toggle('lv-open', on);
      const b = card.querySelector('.lv-max-btn');
      if (b) {
        b.dataset.icon = b.dataset.icon || b.innerHTML;
        b.setAttribute('aria-label', on ? '收回紀錄' : '放大紀錄');
        b.innerHTML = on ? "<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round' aria-hidden='true'><path d='M4 4l8 8M12 4l-8 8'/></svg>" : b.dataset.icon;
      }
    };
    document.addEventListener('click', ev => {
      const b = ev.target.closest && ev.target.closest('#todo .lv-max-btn');
      if (b) { const c = b.closest('#todo'); set(c, !c.classList.contains('lv-maxed')); return; }
      if (document.body.classList.contains('lv-open') && !ev.target.closest('.lv-maxed')) set(document.querySelector('#todo.lv-maxed'), false);
    }, true);
    addEventListener('keydown', ev => { if (ev.key === 'Escape' && document.body.classList.contains('lv-open')) set(document.querySelector('#todo.lv-maxed'), false); });
  })();

  // ---- 文件日期分佈 + 全部網站: 內容's page in a frame, as tall as its card; 這台電腦 under its donut
  const frameEl = $('sxFrame');
  if (frameEl) {
    const fit = () => { try { const g = frameEl.contentDocument.querySelector('.grid'); if (g) frameEl.style.height = Math.ceil(g.getBoundingClientRect().height + 2) + 'px'; } catch (e) { /* not drawn yet */ } };
    const alone = () => { const t = $('mcBlock'), h = $('mcHere'); if (t && h && t.innerHTML.trim() && !h.childElementCount) { h.innerHTML = t.innerHTML; h.hidden = false; } };
    let seenDoc = null;
    const loaded = () => {
      try {
        const doc = frameEl.contentDocument, t = $('mcBlock');
        if (!doc || doc === seenDoc || doc.URL === 'about:blank') return;
        seenDoc = doc;
        if (t && t.innerHTML.trim() && !doc.querySelector('.side-mc')) {
          const side = doc.querySelector('aside.side');
          if (side) side.insertAdjacentHTML('beforeend', t.innerHTML); else alone();
        }
        fit();
        new ResizeObserver(fit).observe(doc.querySelector('.grid'));
      } catch (e) { alone(); }
    };
    frameEl.addEventListener('load', loaded);
    try { if (frameEl.contentDocument && frameEl.contentDocument.readyState === 'complete') loaded(); } catch (e) { /* not ours */ }
  }

  // ---- read again only when the night changed (its signature, /api/tonight), and never while something is being
  // done here (a form open, the log enlarged, a box typed in): the open tab and views come back after it (OV-82)
  const main = $('main'), sig = main && main.dataset.sig;
  const back = keep.get('back');
  if (back) {
    keep.drop('back');
    try {
      const s = JSON.parse(back);
      if (Date.now() - s.at < 120000) { if (s.tab) openTab(s.tab); if (s.vs) vsView(s.vs); if (s.cal) calView(s.cal); }
    } catch (e) { /* an old note: ignored */ }
  }
  if (sig) {
    const idle = () => !document.querySelector('.td-known[open]') && !document.body.classList.contains('lv-open')
      && !(document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName));
    setInterval(async () => {
      if (document.hidden) return;
      try {
        const j = await (await fetch('/api/tonight?brief=1', {cache: 'no-store'})).json();  // the signature, not the lists
        if (j.sig && j.sig !== sig && idle()) {
          keep.set('back', JSON.stringify({at: Date.now(), tab: td && td.dataset.tab, vs: vs && vs.dataset.v, cal: cal && cal.dataset.view}));
          location.reload();
        }
      } catch (e) { /* asked again next minute */ }
    }, 60000);
  }
})();
