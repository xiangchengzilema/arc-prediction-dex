/* Pythia frontend shared utilities — loaded on every page */
(function () {
  'use strict';

  // ─── Top progress bar (visible on fetch) ───────────────────────
  const bar = document.createElement('div');
  bar.className = 'px-progress';
  document.documentElement.appendChild(bar);
  let active = 0;
  function start() {
    active++;
    bar.style.opacity = '1';
    bar.style.width = '70%';
  }
  function done() {
    active = Math.max(0, active - 1);
    if (active === 0) {
      bar.style.width = '100%';
      setTimeout(() => {
        bar.style.opacity = '0';
        bar.style.width = '0';
      }, 200);
    }
  }
  const origFetch = window.fetch;
  window.fetch = function () {
    start();
    return origFetch.apply(this, arguments).finally(done);
  };

  // ─── Number counter animation ──────────────────────────────────
  function animateNumber(el, target, duration = 900, format = 'plain') {
    const start = 0;
    const startT = performance.now();
    function step(now) {
      const t = Math.min(1, (now - startT) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      const value = start + (target - start) * eased;
      if (format === 'usd') {
        el.firstChild.textContent = '$' + Math.round(value).toLocaleString();
      } else {
        el.firstChild.textContent = Math.round(value).toLocaleString();
      }
      if (t < 1) requestAnimationFrame(step);
    }
    if (Math.abs(target) >= 1) requestAnimationFrame(step);
  }

  function runCounters() {
    document.querySelectorAll('[data-target]').forEach(el => {
      if (el.dataset.counted === '1') return;
      const target = Number(el.dataset.target);
      const format = el.dataset.format || 'plain';
      if (!Number.isFinite(target)) return;
      el.dataset.counted = '1';
      animateNumber(el, target, 900, format);
    });
  }

  // ─── Command palette (cmd/ctrl + K) ────────────────────────────
  const PAGES = [
    { name: 'Home',         url: '/',            icon: 'bi-house',         keys: 'home markets dashboard' },
    { name: 'Portfolio',    url: '/portfolio',   icon: 'bi-wallet2',       keys: 'portfolio positions wallet' },
    { name: 'Open orders',  url: '/orders',      icon: 'bi-list-check',    keys: 'orders limit pending' },
    { name: 'Create market',url: '/create',      icon: 'bi-plus-circle',   keys: 'create new market' },
    { name: 'Leaderboard',  url: '/leaderboard', icon: 'bi-trophy',        keys: 'leaderboard top traders ranking' },
    { name: 'Pythia agent', url: '/agent',       icon: 'bi-robot',         keys: 'pythia agent ai bot decisions' },
    { name: 'Resolve oracle',url:'/resolve',     icon: 'bi-shield-check',  keys: 'resolve oracle settle' },
    { name: 'GitHub source',url: 'https://github.com/xiangchengzilema/arc-prediction-dex', icon: 'bi-github', keys: 'github source code repo' },
    { name: 'API health',   url: '/api/health',  icon: 'bi-code-slash',    keys: 'api health status' },
  ];

  function buildPalette() {
    const wrap = document.createElement('div');
    wrap.className = 'px-cmdk-backdrop';
    wrap.innerHTML = `
      <div class="px-cmdk" role="dialog" aria-label="Command palette">
        <input class="px-cmdk-input" placeholder="Jump to a page or action..." autocomplete="off" />
        <div class="px-cmdk-list" role="listbox"></div>
      </div>`;
    document.body.appendChild(wrap);
    const input = wrap.querySelector('input');
    const list = wrap.querySelector('.px-cmdk-list');
    let activeIdx = 0;
    let filtered = PAGES;

    function render() {
      if (!filtered.length) {
        list.innerHTML = '<div class="px-cmdk-empty">No matches</div>';
        return;
      }
      list.innerHTML = filtered.map((p, i) => `
        <div class="px-cmdk-item ${i === activeIdx ? 'active' : ''}" data-i="${i}" role="option">
          <i class="bi ${p.icon}"></i>
          <span>${p.name}</span>
        </div>
      `).join('');
    }

    function open() {
      wrap.classList.add('open');
      input.value = '';
      filtered = PAGES;
      activeIdx = 0;
      render();
      setTimeout(() => input.focus(), 30);
    }
    function close() { wrap.classList.remove('open'); }

    input.addEventListener('input', () => {
      const q = input.value.toLowerCase().trim();
      filtered = q ? PAGES.filter(p => (p.name + ' ' + p.keys).toLowerCase().includes(q)) : PAGES;
      activeIdx = 0;
      render();
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowDown') { activeIdx = Math.min(filtered.length - 1, activeIdx + 1); render(); e.preventDefault(); }
      if (e.key === 'ArrowUp')   { activeIdx = Math.max(0, activeIdx - 1); render(); e.preventDefault(); }
      if (e.key === 'Enter' && filtered[activeIdx]) {
        const url = filtered[activeIdx].url;
        if (url.startsWith('http')) window.open(url, '_blank'); else location.href = url;
      }
    });
    list.addEventListener('click', e => {
      const item = e.target.closest('.px-cmdk-item');
      if (!item) return;
      const p = filtered[Number(item.dataset.i)];
      if (!p) return;
      if (p.url.startsWith('http')) window.open(p.url, '_blank'); else location.href = p.url;
    });
    wrap.addEventListener('click', e => { if (e.target === wrap) close(); });

    document.addEventListener('keydown', e => {
      const isMac = navigator.platform.toUpperCase().includes('MAC');
      const k = (isMac ? e.metaKey : e.ctrlKey) && e.key.toLowerCase() === 'k';
      if (k) { e.preventDefault(); wrap.classList.contains('open') ? close() : open(); }
      else if (e.key === '/' && document.activeElement === document.body) {
        const search = document.getElementById('market-search');
        if (search) { e.preventDefault(); search.focus(); }
      }
    });
  }

  // ─── Toast ──────────────────────────────────────────────────────
  window.pxToast = function (msg, ok = true) {
    let c = document.getElementById('pxt-container');
    if (!c) {
      c = document.createElement('div');
      c.id = 'pxt-container';
      c.className = 'toast-container';
      document.body.appendChild(c);
    }
    const el = document.createElement('div');
    el.className = 'toast show mb-2';
    el.style.minWidth = '300px';
    el.style.padding = '12px 16px';
    el.style.borderColor = ok ? 'rgba(25,232,184,0.4)' : 'rgba(255,93,122,0.4)';
    el.style.background = 'rgba(7,7,11,0.94)';
    el.innerHTML = `<i class="bi ${ok ? 'bi-check-circle-fill' : 'bi-x-circle-fill'}" style="color:${ok ? 'var(--pos)' : 'var(--neg)'};margin-right:8px;"></i>${msg}`;
    c.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, 4500);
    setTimeout(() => el.remove(), 4900);
  };

  // ─── Countdown helpers ────────────────────────────────────────
  function fmtCountdown(deadlineISO) {
    if (!deadlineISO) return null;
    const dt = new Date(deadlineISO);
    if (isNaN(dt)) return null;
    const ms = dt - new Date();
    if (ms <= 0) return { text: 'Closed', urgent: false, expired: true };
    const days = Math.floor(ms / 86400000);
    const hrs  = Math.floor((ms % 86400000) / 3600000);
    const mins = Math.floor((ms % 3600000) / 60000);
    let text;
    if (days >= 30) text = `${Math.floor(days/30)}mo ${days % 30}d`;
    else if (days >= 1) text = `${days}d ${hrs}h`;
    else if (hrs >= 1)  text = `${hrs}h ${mins}m`;
    else                text = `${mins}m`;
    return { text, urgent: ms < 86400000 * 3, expired: false };
  }

  function applyCountdowns() {
    document.querySelectorAll('[data-deadline]').forEach(el => {
      const r = fmtCountdown(el.dataset.deadline);
      if (!r) { el.style.display = 'none'; return; }
      el.classList.toggle('urgent', r.urgent);
      el.classList.toggle('expired', r.expired);
      const lbl = el.querySelector('[data-cd-text]');
      if (lbl) lbl.textContent = r.expired ? 'Closed' : `Closes in ${r.text}`;
      else el.textContent = r.expired ? 'Closed' : `Closes in ${r.text}`;
    });
  }

  window.pxApplyCountdowns = applyCountdowns;

  // ─── Boot ──────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { runCounters(); buildPalette(); applyCountdowns(); setInterval(applyCountdowns, 60000); });
  } else {
    runCounters();
    buildPalette();
    applyCountdowns();
    setInterval(applyCountdowns, 60000);
  }
})();
