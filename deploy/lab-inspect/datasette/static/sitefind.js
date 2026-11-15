// Finding a site in the list templates/_sitefind.html draws (owner 11-15: 「只能一個個看名字…我覺得這很不人性化」).
// What is typed is matched against each row's keys (data-k: the site's name, code, unit and the unit's short names,
// in both scripts, joined by '|'): a row that holds the typed text comes first, then one that holds its characters
// in that order (軟體系 finds 軟體工程與管理學系; only for Chinese, where it reads as a short name). Enter opens the
// first row; a header sorts the rows by its number. 【Claude】
(() => {
  const norm = s => (s || '').toLowerCase().replace(/\s+/g, '');
  const inOrder = (q, text) => { let i = 0; for (const ch of text) { if (ch === q[i]) i++; if (i === q.length) return true; } return false; };
  for (const box of document.querySelectorAll('.sitefind')) {
    const input = box.querySelector('input'), list = box.querySelector('ol'), none = box.querySelector('.none');
    const rows = [...list.children], keys = rows.map(r => norm(r.dataset.k).split('|'));
    let by = 'n';
    const run = () => {
      const q = norm(input.value), loose = /[^\x00-\x7f]/.test(q);
      const scored = rows.map((row, i) => {
        const hit = !q || keys[i].some(k => k.includes(q)) ? 2 : loose && keys[i].some(k => inOrder(q, k)) ? 1 : 0;
        row.hidden = !hit;
        return { row, hit, n: +row.dataset[by] || 0, i };
      });
      scored.sort((a, b) => b.hit - a.hit || b.n - a.n || a.i - b.i);
      for (const s of scored) list.appendChild(s.row);
      none.hidden = scored.some(s => s.hit);
      list.scrollTop = 0;
    };
    input.addEventListener('input', run);
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const first = [...list.children].find(r => !r.hidden);
      if (first) first.querySelector('a').click();
    });
    for (const head of box.querySelectorAll('[data-sort]')) {
      head.addEventListener('click', () => {
        by = head.dataset.sort;
        for (const h of box.querySelectorAll('[data-sort]')) h.classList.toggle('on', h === head);
        run();
      });
    }
    if (input.value) run();
  }
})();
