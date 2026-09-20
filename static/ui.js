(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // ---------- Dropdown-Menüs ----------
  function closeMenus(except) {
    $$('.dd.open').forEach(d => {
      if (d === except) return;
      d.classList.remove('open');
      const b = $('[data-menu]', d);
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }

  // ---------- Modal ----------
  function openModal(m) {
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
    document.body.classList.add('lock');
    setTimeout(() => {
      const i = $$('input:not([type=hidden])', m).find(x => x.offsetParent !== null);
      if (i) i.focus();
    }, 60);
  }
  function closeModal(m) {
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
    if (!$('.modal.open')) document.body.classList.remove('lock');
  }

  // ---------- Bestätigungsdialog ----------
  function ask(text) {
    return new Promise(res => {
      const m = document.createElement('div');
      m.className = 'modal open';
      m.dataset.dyn = '1';
      m.innerHTML = '<div class="sheet confirm" role="alertdialog" aria-modal="true"><p></p>' +
        '<div class="btns"><button type="button" class="btn" data-n>Abbrechen</button>' +
        '<button type="button" class="btn primary" data-y>OK</button></div></div>';
      $('p', m).textContent = text;
      document.body.appendChild(m);
      document.body.classList.add('lock');
      const done = v => { m.remove(); if (!$('.modal.open')) document.body.classList.remove('lock'); res(v); };
      m.addEventListener('click', e => {
        if (e.target.closest('[data-y]')) done(true);
        else if (e.target.closest('[data-n]') || e.target === m) done(false);
      });
      m.addEventListener('keydown', e => { if (e.key === 'Escape') done(false); });
      $('[data-y]', m).focus();
    });
  }

  // ---------- Toasts ----------
  function arm(t) {
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, 4500);
  }
  function toast(msg, cat) {
    let box = $('.toasts');
    if (!box) {
      box = document.createElement('div');
      box.className = 'toasts';
      box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    const t = document.createElement('div');
    t.className = 'toast ' + (cat || 'ok');
    t.textContent = msg;
    box.appendChild(t);
    arm(t);
  }
  window.UI = {ask, toast, openModal, closeModal};

  // ---------- Klicks ----------
  document.addEventListener('click', e => {
    const menuBtn = e.target.closest('[data-menu]');
    if (menuBtn) {
      const dd = menuBtn.closest('.dd');
      const open = !dd.classList.contains('open');
      closeMenus(open ? dd : null);
      dd.classList.toggle('open', open);
      menuBtn.setAttribute('aria-expanded', String(open));
      return;
    }
    if (!e.target.closest('.dd .menu') || e.target.closest('.menu .mi')) closeMenus();

    const opener = e.target.closest('[data-open]');
    if (opener) {
      const m = document.getElementById(opener.dataset.open);
      if (m) {
        if (opener.dataset.tab) {
          const t = $(`[data-filter="${opener.dataset.tab}"]`, m);
          if (t) t.click();
        }
        openModal(m);
      }
      return;
    }
    const m = e.target.closest('.modal');
    if (m && !m.dataset.dyn && (e.target.closest('[data-close]') || e.target === m)) closeModal(m);

    // Filter, Kategorien und Tabs
    const f = e.target.closest('[data-filter]');
    if (f) {
      const scope = f.closest('[data-filter-for]');
      if (!scope) return;
      const sel = scope.dataset.filterFor, attr = scope.dataset.filterAttr, val = f.dataset.filter;
      $$('[data-filter]', scope).forEach(x => x.classList.toggle('on', x === f));
      let visible = 0;
      $$(sel).forEach(el => {
        const show = val === 'all' || el.dataset[attr] === val;
        el.hidden = !show;
        if (show) visible++;
      });
      const lbl = $('[data-filter-label]', scope);
      if (lbl) lbl.textContent = f.dataset.label || f.textContent.trim();
      if (scope.dataset.filterEmpty) { const n = $(scope.dataset.filterEmpty); if (n) n.hidden = visible > 0; }
      if (scope.dataset.filterCount) { const n = $(scope.dataset.filterCount); if (n) n.textContent = String(visible); }
    }
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    closeMenus();
    $$('.modal.open:not([data-dyn])').forEach(closeModal);
  });

  // ---------- Bestätigen vor dem Absenden (data-confirm) ----------
  document.addEventListener('submit', e => {
    const f = e.target;
    if (!f.dataset || !f.dataset.confirm) return;
    if (f.dataset.ok === '1') { delete f.dataset.ok; return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    ask(f.dataset.confirm).then(ok => {
      if (!ok) return;
      f.dataset.ok = '1';
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    });
  });

  // ---------- Wechselnder Spruch auf der Startseite ----------
  setInterval(() => {
    const el = $('[data-rotate]');
    if (!el || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
    let lines;
    try { lines = JSON.parse(el.dataset.rotate); } catch (_) { return; }
    el.classList.add('swap');
    setTimeout(() => {
      el.dataset.i = String(((+el.dataset.i || 0) + 1) % lines.length);
      el.textContent = lines[+el.dataset.i];
      el.classList.remove('swap');
    }, 350);
  }, 3800);

  // ---------- Start ----------
  $$('.toast').forEach(arm);
  const auth = document.body.dataset.auth;
  if (auth) {
    const m = document.getElementById('auth');
    if (m) {
      const t = $(`[data-filter="${auth}"]`, m);
      if (t) t.click();
      openModal(m);
    }
  }
})();
