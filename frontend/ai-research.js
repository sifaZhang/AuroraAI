(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.AIResearch = api; api.init(); }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  const order = {heavy: 0, add: 1, entry: 2, watch: 3, avoid: 4};
  const label = value => ({avoid:'Avoid',watch:'Watch',entry:'Entry',add:'Add',heavy:'Heavy'}[value] || value || '—');
  const confidence = value => value == null ? '—' : `${Math.round(Number(value) * 100)}%`;
  const detailUrl = symbol => `/dividend/ai-research/${encodeURIComponent(symbol)}`;
  const signalGap = item => item.yield_signal === item.research_decision ? 'Matched' : `${label(item.yield_signal)} → ${label(item.research_decision)}`;
  const filterItems = (items, filters) => items.filter(item => {
    const query = (filters.symbol || '').trim().toLowerCase();
    return (!query || item.symbol.toLowerCase().includes(query) || (item.company_name || '').toLowerCase().includes(query))
      && (filters.decision === 'all' || item.research_decision === filters.decision)
      && (filters.yieldSignal === 'all' || item.yield_signal === filters.yieldSignal)
      && (!filters.disagreements || item.yield_signal !== item.research_decision);
  }).sort((a, b) => order[a.research_decision] - order[b.research_decision]
    || b.decision_confidence - a.decision_confidence || a.symbol.localeCompare(b.symbol));

  function init() {
    const $ = id => document.getElementById(id);
    let items = [];
    const querySymbol = new URLSearchParams(location.search).get('symbol') || '';
    $('research-search').value = querySymbol;
    const filters = () => ({symbol:$('research-search').value, decision:$('decision-filter').value,
      yieldSignal:$('yield-filter').value, disagreements:$('disagreement-filter').checked});
    const chip = value => `<span class="research-chip decision-${value}">${label(value)}</span>`;
    const render = () => {
      const shown = filterItems(items, filters());
      const disagreements = items.filter(item => item.yield_signal !== item.research_decision).length;
      const entryPlus = items.filter(item => ['entry','add','heavy'].includes(item.research_decision)).length;
      const watch = items.filter(item => item.research_decision === 'watch').length;
      $('research-kpis').innerHTML = [['已研究公司',items.length],['Watch',watch],['Entry+',entryPlus],['Signal Disagreements',disagreements]].map(([name,value]) => `<div class="overview-stat"><span>${name}</span><strong>${value}</strong></div>`).join('');
      $('research-rows').innerHTML = shown.map(item => `<tr data-research-symbol="${item.symbol}"><td class="stock-cell"><a href="${detailUrl(item.symbol)}"><strong>${item.company_name || item.symbol}</strong><small>${item.symbol}</small></a></td><td>${chip(item.yield_signal)}</td><td><a href="${detailUrl(item.symbol)}">${chip(item.research_decision)}</a></td><td class="${item.yield_signal === item.research_decision ? 'signal-match' : 'signal-gap'}">${signalGap(item)}</td><td>${chip(item.decision_cap)}</td><td class="number-cell" title="${item.confidence_level || 'Legacy confidence'}">${confidence(item.decision_confidence)}</td><td>${item.skeptic_verdict}</td><td>${item.valuation_status}</td><td>${item.forecast_quality}</td><td class="number-cell">${item.latest_price == null ? '—' : Number(item.latest_price).toFixed(2)}</td><td>${item.research_date}</td></tr>`).join('');
      if (!shown.length) {
        const targeted = Boolean(filters().symbol);
        $('research-rows').innerHTML = `<tr><td colspan="11" class="empty-state">${targeted ? '该股票尚无已完成AI研究。' : '暂无AI研究结果'}</td></tr>`;
      }
      $('research-message').textContent = shown.length ? `显示 ${shown.length} / ${items.length} 条已完成研究` : '';
    };
    const load = async () => {
      $('research-error').hidden = true;
      try {
        const response = await fetch('/api/research');
        if (!response.ok) throw Error(`后端返回 ${response.status}`);
        const data = await response.json(); items = data.items || []; render();
      } catch (error) {
        $('research-error').hidden = false; $('research-error-detail').textContent = error.message;
        $('research-rows').innerHTML = '<tr><td colspan="11" class="empty-state">暂无AI研究结果</td></tr>';
      }
    };
    ['research-search','decision-filter','yield-filter','disagreement-filter'].forEach(id => $(id).addEventListener('input', render));
    $('research-retry').onclick = load;
    load();
  }
  return {confidence, detailUrl, filterItems, label, signalGap, init};
});
