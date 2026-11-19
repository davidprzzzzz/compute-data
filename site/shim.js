// Moteur minimal qui fait tourner la page du dashboard hors de Claude.
// Il expose la même API `dash` (data, calc, draw, params, setParams) à partir des fichiers de data/.
(function () {
  const FILES = {
    spot: 'data/spot.json', kalshi: 'data/kalshi.json', kalshi_hist: 'data/kalshi_hist.json', sd_term: 'data/sd_term.json',
    sd_term_hist: 'data/sd_term_hist.json', hist_spot: 'data/hist_spot.json', listes: 'data/listes.json', refs: 'data/refs.json',
    news: 'data/news.json', agenda: 'data/agenda.csv', list_index: 'data/list_index.json', list_checks: 'data/list_checks.json', methode: 'data/methode.json',
  };
  const DEFAULTS = { p_gpu: 'H100', k1: '1M', k2: '2M', k3: '3M', k4: '4M', k5: '5M', k6: '6M', k7: '9M', l1: '1M', l2: '3M', l3: '6M',
    l4: '1A', l5: '2A', l6: '3A', q_curve: 'Kalshi', q_gpu: 'H100', q_ten: '6M', q_n: 64, q_comm: 3, c_series: '' };
  let params = { ...DEFAULTS };
  try { params = { ...DEFAULTS, ...JSON.parse(localStorage.getItem('cg_params') || '{}') }; } catch (e) { /* stockage indisponible */ }
  const store = { cotations: { status: 'ok', data: [] } };
  const calcs = {}, draws = {}, cache = {};
  const fixed = new Set(['cotations']);

  const runDraw = (name) => {
    const fn = draws[name]; if (!fn) return;
    document.querySelectorAll('[data-draw="' + name + '"]').forEach((el) => { if (!el.closest('[hidden]')) { try { fn(el); } catch (e) { console.error(name, e); } } });
  };
  const redrawAll = () => { Object.keys(cache).forEach((k) => delete cache[k]); Object.keys(draws).forEach(runDraw); };

  window.dash = {
    data(id) {
      if (calcs[id]) {
        if (cache[id]) return cache[id];
        const c = calcs[id];
        const ins = c.inputs.map((i) => window.dash.data(i));
        if (ins.some((x) => x.status !== 'ok')) return { status: 'loading', data: null };
        try { cache[id] = { status: 'ok', data: c.fn(...ins.map((x) => x.data)) }; } catch (e) { console.error(id, e); cache[id] = { status: 'error', data: null, message: String(e) }; }
        return cache[id];
      }
      return store[id] || { status: 'loading', data: [] };
    },
    calc(id, def) { calcs[id] = def; },
    draw(name, fn) { if (typeof fn === 'function') draws[name] = fn; else if (fn && fn.update) draws[name] = (el) => fn.update(el, fn.mount ? fn.mount(el) : null); else runDraw(name); },
    params() { return { ...params }; },
    setParams(o) { params = { ...params, ...o }; try { localStorage.setItem('cg_params', JSON.stringify(params)); } catch (e) { /* rien */ } redrawAll(); },
    resetParams() { params = { ...DEFAULTS }; try { localStorage.removeItem('cg_params'); } catch (e) { /* rien */ } redrawAll(); },
    onData(fn) { fn(); },
    setLink() {}, refresh() {}, colors: ['#ffffff', '#4da3ff', '#2bb673', '#e5484d', '#b48cff', '#8fd3ff'],
  };

  window.addEventListener('load', async () => {
    await Promise.all(Object.entries(FILES).map(async ([id, url]) => {
      if (fixed.has(id)) return;
      try {
        const r = await fetch(url + '?v=' + Date.now());
        if (!r.ok) throw new Error(r.status);
        if (url.endsWith('.csv')) store[id] = { status: 'ok', data: d3.csvParse(await r.text()) };
        else { const j = await r.json(); store[id] = { status: 'ok', data: Array.isArray(j) ? j : (j.rows || []), meta: j.meta }; }
      } catch (e) { store[id] = { status: 'error', data: [], message: String(e) }; }
    }));
    redrawAll();
    document.documentElement.setAttribute('data-ready', '1');
  });
})();
