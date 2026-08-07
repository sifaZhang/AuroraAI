(() => {
  const $ = id => document.getElementById(id);
  const text = { loading: '\u6b63\u5728\u52a0\u8f7d\u5206\u7ea2\u80a1\u7968\u6c60...', loadFailed: '\u80a1\u7968\u6c60\u52a0\u8f7d\u5931\u8d25', retry: '\u91cd\u65b0\u52a0\u8f7d', enabled: '\u542f\u7528', disabled: '\u5df2\u505c\u7528', stable: '\u7a33\u5b9a\u5784\u65ad\u578b', cyclical: '\u8d44\u6e90\u5468\u671f\u578b', automatic: '\u81ea\u52a8\u7b5b\u9009', initial: '\u521d\u59cb\u5316\u8865\u5145', manual: '\u624b\u52a8\u6dfb\u52a0', review: '\u5019\u9009\u5ba1\u6838\u52a0\u5165' };
  let selected = null; let years = []; let yieldByKey = {}; let yieldCalculationDate = null; let sortKey = null; let sortDirection = 'desc'; let syncingScroll = false;
  const subtype = { stable_monopoly: text.stable, resource_monopoly_cyclical: text.cyclical };
  const source = { automatic_rule: text.automatic, manual_addition: text.initial, manual: text.manual, manual_review: text.review };
  const api = async (url, options) => {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { throw Error(`\u540e\u7aef\u8fd4\u56de HTTP ${response.status}\uff0c\u4e14\u54cd\u5e94\u4e0d\u662f JSON`); }
    if (!response.ok) throw Error(data.detail || `\u540e\u7aef\u8fd4\u56de HTTP ${response.status}`);
    return data;
  };
  const cell = value => value ?? '-';
  const dps = value => value == null ? '-' : Number(value).toFixed(4);
  const price = value => value == null ? '-' : Number(value).toFixed(2);
  const percentage = value => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
  const yieldKey = item => `${item.market || 'CN'}:${item.symbol}`;
  const syncScrollbars = () => { const top = $('dividend-top-scroll'); const topContent = $('dividend-top-scroll-content'); const table = $('dividend-table'); const bottom = $('dividend-table-wrap'); if (!table || !bottom) return; topContent.style.width = `${table.scrollWidth}px`; top.hidden = table.scrollWidth <= bottom.clientWidth; if (!syncingScroll) top.scrollLeft = bottom.scrollLeft; };
  const sortItems = items => { if (!sortKey) return items; return [...items].sort((left, right) => { const a = yieldByKey[yieldKey(left)]?.[sortKey]; const b = yieldByKey[yieldKey(right)]?.[sortKey]; if (a == null) return b == null ? 0 : 1; if (b == null) return -1; return sortDirection === 'desc' ? b - a : a - b; }); };
  const displayError = error => { $('load-error').hidden = false; $('load-error-detail').textContent = error.message || String(error); $('universe-table-card').hidden = true; $('empty-state').hidden = true; $('message').textContent = ''; };
  const resetError = () => { $('load-error').hidden = true; };
  const overview = (data) => {
    const stable = data.items.filter(item => item.stability_subtype === 'stable_monopoly').length;
    const cyclical = data.items.filter(item => item.stability_subtype === 'resource_monopoly_cyclical').length;
    $('overview-grid').innerHTML = [[ '\u80a1\u7968\u6c60', data.total ], [ text.stable, stable ], [ text.cyclical, cyclical ], [ '\u5df2\u505c\u7528', data.disabled_count ]].map(([label, value]) => `<div class="overview-stat"><span>${label}</span><strong>${value}</strong></div>`).join('');
  };
  const renderRows = items => {
    const sortableHeader = (label, key) => { const active = sortKey === key; const arrow = active ? (sortDirection === 'desc' ? '\u2193' : '\u2191') : '\u2195'; return `<th class="yield-sort" data-sort-key="${key}" role="button" tabindex="0" title="\u70b9\u51fb\u6392\u5e8f" aria-sort="${active ? (sortDirection === 'desc' ? 'descending' : 'ascending') : 'none'}">${label} <span aria-hidden="true">${arrow}</span></th>`; };
    $('head').innerHTML = '<tr>' + ['\u80a1\u7968', '\u884c\u4e1a', '\u7c7b\u578b', '\u5f53\u524d\u4ef7', '\u4ef7\u683c\u65e5', ...years.map(year => `${year} DPS`), '\u4e09\u5e74\u5e73\u5747 DPS'].map(item => `<th>${item}</th>`).join('') + sortableHeader('\u53bb\u5e74\u80a1\u606f\u7387', 'latest_year_yield') + sortableHeader('\u4e09\u5e74\u5e73\u5747\u80a1\u606f\u7387', 'three_year_average_yield') + '<th>\u72b6\u6001</th><th>\u64cd\u4f5c</th></tr>';
    $('rows').innerHTML = sortItems(items).map(item => { const snapshot = yieldByKey[yieldKey(item)]; return `<tr><td class="stock-cell"><strong>${item.company_name}</strong><small>${item.symbol}</small></td><td class="industry-cell" title="${cell(item.industry_level_1)} ${cell(item.industry_level_2)}">${cell(item.industry_level_1)}<small>${cell(item.industry_level_2)}</small></td><td><span class="dividend-tag ${item.stability_subtype === 'resource_monopoly_cyclical' ? 'cyclical' : ''}" title="${item.risk_note || ''}">${subtype[item.stability_subtype] || item.stability_subtype}</span></td><td class="number-cell">${price(snapshot?.latest_price)}</td><td class="price-date">${cell(snapshot?.price_date)}</td>${years.map(year => `<td class="dps">${dps(item.annual_dps[year])}</td>`).join('')}<td class="dps">${dps(item.three_year_average_dps)}</td><td class="number-cell">${percentage(snapshot?.latest_year_yield)}</td><td class="number-cell">${percentage(snapshot?.three_year_average_yield)}</td><td><span class="status-chip ${item.is_enabled ? 'eligible' : 'neutral'}">${item.is_enabled ? text.enabled : text.disabled}</span></td><td><button class="detail-button" data-symbol="${item.symbol}" data-enabled="${item.is_enabled}">${item.is_enabled ? '\u505c\u7528' : '\u91cd\u65b0\u542f\u7528'}</button></td></tr>`; }).join('');
    document.querySelectorAll('[data-symbol]').forEach(button => button.onclick = () => change(button.dataset.symbol, button.dataset.enabled !== 'true'));
    document.querySelectorAll('[data-sort-key]').forEach(header => { const changeSort = () => { const key = header.dataset.sortKey; sortDirection = sortKey === key && sortDirection === 'desc' ? 'asc' : 'desc'; sortKey = key; renderRows(items); }; header.onclick = changeSort; header.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); changeSort(); } }; });
    requestAnimationFrame(syncScrollbars);
  };
  async function loadYields() {
    const data = await api('/api/dividend/yields');
    yieldCalculationDate = data.calculation_date || null;
    yieldByKey = Object.fromEntries((data.items || []).map(item => [yieldKey(item), item]));
  }
  async function load() {
    resetError(); $('empty-state').hidden = true; $('universe-table-card').hidden = false;
    $('rows').innerHTML = '<tr><td colspan="13" class="empty-state">' + text.loading + '</td></tr>';
    $('message').textContent = text.loading;
    try {
      const params = new URLSearchParams({ include_disabled: $('disabled').checked, search: $('search').value, stability_subtype: $('subtype').value });
      const data = await api('/api/dividend/universe?' + params);
      try { await loadYields(); } catch (error) { yieldByKey = {}; yieldCalculationDate = null; }
      years = data.target_years || [];
      overview(data);
      if (!data.items.length) { $('universe-table-card').hidden = true; $('empty-state').hidden = false; $('message').textContent = ''; return; }
      renderRows(data.items); $('message').textContent = `\u5171 ${data.total} \u53ea\uff0c\u542f\u7528 ${data.enabled_count} \u53ea\u3002\u80a1\u606f\u7387\u6570\u636e\uff1a${yieldCalculationDate || '\u6682\u65e0'}`;
    } catch (error) { displayError(error); }
  }
  async function change(symbol, isEnabled) { if (!confirm(isEnabled ? `\u786e\u8ba4\u91cd\u65b0\u542f\u7528 ${symbol}\uff1f` : `\u505c\u7528 ${symbol} \u540e\u4ecd\u4f1a\u4fdd\u7559\u5206\u7ea2\u5386\u53f2\u3002\u786e\u8ba4\u505c\u7528\uff1f`)) return; try { await api(`/api/dividend/universe/${symbol}/status`, { method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({is_enabled: isEnabled}) }); load(); } catch (error) { $('message').textContent = error.message; } }
  function openDialog() { selected = null; $('validation').textContent = ''; $('validation').className = 'state-note'; $('results').innerHTML = ''; $('add-monopoly').value = ''; $('reason').value = ''; $('ack').checked = false; $('confirm').disabled = true; $('dialog').showModal(); }
  $('add').onclick = openDialog; $('empty-add').onclick = openDialog; $('retry').onclick = load;
  $('dialog-close').onclick = () => $('dialog').close();
  $('dialog').addEventListener('click', event => { if (event.target === $('dialog')) $('dialog').close(); });
  $('dividend-top-scroll').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('dividend-table-wrap').scrollLeft = $('dividend-top-scroll').scrollLeft; syncingScroll = false; });
  $('dividend-table-wrap').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('dividend-top-scroll').scrollLeft = $('dividend-table-wrap').scrollLeft; syncingScroll = false; });
  window.addEventListener('resize', syncScrollbars);
  $('find').onclick = async () => { try { selected = null; updateConfirm(); $('validation').className = 'state-note'; $('validation').textContent = '\u8bf7\u9009\u62e9\u8bc1\u5238\u5e76\u9a8c\u8bc1\u5206\u7ea2\u6570\u636e\u3002'; const data = await api('/api/dividend/universe/search?q=' + encodeURIComponent($('query').value)); $('results').innerHTML = data.items.map(item => `<button type="button" class="detail-button" data-candidate="${item.symbol}">\u9009\u62e9\u5e76\u9a8c\u8bc1：${item.symbol} ${item.company_name}${item.already_in_universe ? '（已在池中）' : ''}</button>`).join('') || '<p class="state-note">\u672a\u627e\u5230\u53ef\u7528 A \u80a1</p>'; document.querySelectorAll('[data-candidate]').forEach(button => button.onclick = () => validate(button.dataset.candidate)); if (data.items.length === 1) validate(data.items[0].symbol); } catch (error) { $('validation').className = 'state-note error'; $('validation').textContent = error.message; } };
  async function validate(symbol) { try { const data = await api('/api/dividend/universe/validate', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({symbol}) }); selected = data; $('validation').textContent = data.can_add ? `${data.company_name}\uff1aDPS \u9a8c\u8bc1\u5b8c\u6210\u3002${(data.warnings || []).join('；')}` : (data.warnings || []).join('；'); $('add-monopoly').value = data.suggested_monopoly_type || ''; updateConfirm(); } catch (error) { $('validation').textContent = error.message; } }
  function updateConfirm() { $('confirm').disabled = !selected || !selected.can_add || !$('ack').checked; }
  ['change', 'input', 'click'].forEach(eventName => $('ack').addEventListener(eventName, () => setTimeout(updateConfirm, 0)));
  $('confirm').onclick = async () => { try { if (!selected || !$('ack').checked) return; const button = $('confirm'); button.disabled = true; $('validation').className = 'state-note'; $('validation').textContent = '\u6b63\u5728\u52a0\u5165\u80a1\u7968\u6c60...'; const result = await api('/api/dividend/universe', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({symbol: selected.symbol, stability_subtype: $('add-subtype').value, monopoly_type: $('add-monopoly').value, manual_reason: $('reason').value, acknowledge_warnings: true}) }); if (result.status === 'added') { $('dialog').close(); load(); } else { $('validation').className = 'state-note warning'; $('validation').textContent = result.status === 'disabled_exists' ? '\u8be5\u516c\u53f8\u5df2\u5728\u80a1\u7968\u6c60\u4f46\u5df2\u505c\u7528\uff0c\u8bf7\u91cd\u65b0\u542f\u7528\u3002' : '\u8be5\u516c\u53f8\u5df2\u5728\u80a1\u7968\u6c60\u3002'; button.disabled = false; } } catch (error) { $('validation').className = 'state-note error'; $('validation').textContent = '\u52a0\u5165\u80a1\u7968\u6c60\u5931\u8d25\uff1a' + error.message; $('confirm').disabled = false; } };
  $('rescan').onclick = async () => { try { const run = await api('/api/dividend/universe/rescan', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'}); $('rescan').disabled = true; $('message').textContent = '\u6b63\u5728\u91cd\u65b0\u7b5b\u9009\u5019\u9009\u6c60...'; poll(run.run_id); } catch (error) { $('message').textContent = error.message; } };
  $('refresh-yields').onclick = async () => { const button = $('refresh-yields'); const date = yieldCalculationDate || new Date().toISOString().slice(0, 10); button.disabled = true; button.textContent = '\u6b63\u5728\u5237\u65b0...'; try { await api('/api/dividend/yields/refresh', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({calculation_date: date})}); await load(); } catch (error) { $('message').textContent = '\u5237\u65b0\u80a1\u606f\u7387\u5931\u8d25\uff1a' + error.message; } finally { button.disabled = false; button.textContent = '\u5237\u65b0\u80a1\u606f\u7387'; } };
  async function poll(runId) { try { const run = await api('/api/dividend/universe/rescan/' + runId); if (run.status === 'running') return setTimeout(() => poll(runId), 1000); $('rescan').disabled = false; if (run.status === 'failed') { $('message').textContent = '\u7b5b\u9009\u5931\u8d25\uff1a' + run.error; return; } const counts = run.items.reduce((all, item) => (all[item.classification] = (all[item.classification] || 0) + 1, all), {}); $('scan-results').hidden = false; $('scan-results').innerHTML = `<p class="scan-summary">\u7b5b\u9009\u5b8c\u6210\uff1a\u4ecd\u7b26\u5408 ${counts.still_qualified || 0} \u53ea\uff0c\u65b0\u5019\u9009 ${counts.new_candidate || 0} \u53ea\uff0c\u4e0d\u518d\u7b26\u5408 ${counts.no_longer_qualified || 0} \u53ea\u3002\u7ed3\u679c\u4ec5\u4fdd\u7559\u5728\u672c\u6b21\u670d\u52a1\u8fdb\u7a0b\u4e2d\u3002</p>`; $('message').textContent = '\u5019\u9009\u6c60\u7b5b\u9009\u5b8c\u6210\u3002'; } catch (error) { $('rescan').disabled = false; $('message').textContent = error.message; } }
  ['disabled', 'subtype'].forEach(id => $(id).onchange = load); $('search').oninput = () => { clearTimeout(window.dividendSearchTimer); window.dividendSearchTimer = setTimeout(load, 250); }; load();
})();
