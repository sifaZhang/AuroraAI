(() => {
  const $ = id => document.getElementById(id);
  const labels = {
    stable_monopoly: '稳定垄断型',
    resource_monopoly_cyclical: '资源周期型',
    high_dividend_watch: '普通高股息观察型',
    stable_cashflow: '稳定现金流型'
  };
  let selected = null;
  let years = [];
  let yieldByKey = {};
  let yieldCalculationDate = null;
  let sortKey = null;
  let sortDirection = 'desc';
  let candidateItems = [];
  let candidateSummary = {};
  let candidateSortKey = 'three_year_historical_average_yield';
  let candidateSortDirection = 'desc';
  let syncingScroll = false;

  const api = async (url, options) => {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { throw Error(`后端返回 HTTP ${response.status}，且响应不是 JSON`); }
    if (!response.ok) throw Error(data.detail || `后端返回 HTTP ${response.status}`);
    return data;
  };
  const cell = value => value ?? '-';
  const dps = value => value == null ? '-' : Number(value).toFixed(4);
  const price = value => value == null ? '-' : Number(value).toFixed(2);
  const percentage = value => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
  const yieldKey = item => `${item.market || 'CN'}:${item.symbol}`;
  const displayError = error => { $('load-error').hidden = false; $('load-error-detail').textContent = error.message || String(error); $('universe-table-card').hidden = true; $('empty-state').hidden = true; $('message').textContent = ''; };
  const resetError = () => { $('load-error').hidden = true; };

  const syncScrollbars = () => {
    const table = $('dividend-table'); const bottom = $('dividend-table-wrap');
    if (!table || !bottom) return;
    $('dividend-top-scroll-content').style.width = `${table.scrollWidth}px`;
    $('dividend-top-scroll').hidden = table.scrollWidth <= bottom.clientWidth;
    if (!syncingScroll) $('dividend-top-scroll').scrollLeft = bottom.scrollLeft;
  };
  const syncCandidateScrollbars = () => {
    const table = document.querySelector('.candidate-table'); const bottom = $('candidate-table-wrap');
    if (!table || !bottom) return;
    $('candidate-top-scroll-content').style.width = `${table.scrollWidth}px`;
    $('candidate-top-scroll').hidden = table.scrollWidth <= bottom.clientWidth;
    $('candidate-top-scroll').scrollLeft = bottom.scrollLeft;
  };
  const sortItems = items => {
    if (!sortKey) return items;
    return [...items].sort((left, right) => {
      const a = yieldByKey[yieldKey(left)]?.[sortKey]; const b = yieldByKey[yieldKey(right)]?.[sortKey];
      if (a == null) return b == null ? 0 : 1;
      if (b == null) return -1;
      return sortDirection === 'desc' ? b - a : a - b;
    });
  };
  const overview = data => {
    const stable = data.items.filter(item => item.stability_subtype === 'stable_monopoly').length;
    const cyclical = data.items.filter(item => item.stability_subtype === 'resource_monopoly_cyclical').length;
    const watch = data.items.filter(item => item.stability_subtype === 'high_dividend_watch').length;
    $('overview-grid').innerHTML = [['股票池', data.total], ['稳定垄断型', stable], ['资源周期型', cyclical], ['普通高股息观察型', watch], ['已停用', data.disabled_count]].map(([label, value]) => `<div class="overview-stat"><span>${label}</span><strong>${value}</strong></div>`).join('');
  };
  const renderRows = items => {
    const sortableHeader = (label, key) => {
      const active = sortKey === key; const arrow = active ? (sortDirection === 'desc' ? '↓' : '↑') : '↕';
      return `<th class="yield-sort" data-sort-key="${key}" role="button" tabindex="0">${label} ${arrow}</th>`;
    };
    $('head').innerHTML = '<tr>' + ['股票', '行业', '类型', '当前价', '价格日', ...years.map(year => `${year} DPS`), '三年平均 DPS'].map(value => `<th>${value}</th>`).join('') + sortableHeader('去年股息率', 'latest_year_yield') + sortableHeader('三年平均股息率', 'three_year_average_yield') + '<th>状态</th><th>操作</th></tr>';
    $('rows').innerHTML = sortItems(items).map(item => {
      const snapshot = yieldByKey[yieldKey(item)];
      return `<tr><td class="stock-cell"><strong>${item.company_name}</strong><small>${item.symbol}</small></td><td class="industry-cell">${cell(item.industry_level_1)}<small>${cell(item.industry_level_2)}</small></td><td><span class="dividend-tag ${item.stability_subtype === 'resource_monopoly_cyclical' ? 'cyclical' : ''}">${labels[item.stability_subtype] || item.stability_subtype}</span></td><td class="number-cell">${price(snapshot?.latest_price)}</td><td>${cell(snapshot?.price_date)}</td>${years.map(year => `<td class="dps">${dps(item.annual_dps[year])}</td>`).join('')}<td class="dps">${dps(item.three_year_average_dps)}</td><td class="number-cell ${getYieldClass(snapshot?.latest_year_yield)}">${percentage(snapshot?.latest_year_yield)}</td><td class="number-cell ${getYieldClass(snapshot?.three_year_average_yield)}">${percentage(snapshot?.three_year_average_yield)}</td><td><span class="status-chip ${item.is_enabled ? 'eligible' : 'neutral'}">${item.is_enabled ? '启用' : '已停用'}</span></td><td><button class="detail-button" data-symbol="${item.symbol}" data-enabled="${item.is_enabled}">${item.is_enabled ? '停用' : '重新启用'}</button></td></tr>`;
    }).join('');
    document.querySelectorAll('[data-symbol]').forEach(button => button.onclick = () => change(button.dataset.symbol, button.dataset.enabled !== 'true'));
    document.querySelectorAll('[data-sort-key]').forEach(header => header.onclick = () => { sortDirection = sortKey === header.dataset.sortKey && sortDirection === 'desc' ? 'asc' : 'desc'; sortKey = header.dataset.sortKey; renderRows(items); });
    requestAnimationFrame(syncScrollbars);
  };
  async function loadYields() {
    const data = await api('/api/dividend/yields');
    yieldCalculationDate = data.calculation_date || null;
    yieldByKey = Object.fromEntries((data.items || []).map(item => [yieldKey(item), item]));
  }
  async function load() {
    resetError(); $('empty-state').hidden = true; $('universe-table-card').hidden = false;
    $('rows').innerHTML = '<tr><td colspan="13" class="empty-state">正在加载分红股票池...</td></tr>';
    try {
      const params = new URLSearchParams({ include_disabled: $('disabled').checked, search: $('search').value, stability_subtype: $('subtype').value });
      const data = await api('/api/dividend/universe?' + params);
      try { await loadYields(); } catch { yieldByKey = {}; yieldCalculationDate = null; }
      years = data.target_years || []; overview(data);
      if (!data.items.length) { $('universe-table-card').hidden = true; $('empty-state').hidden = false; $('message').textContent = ''; return; }
      renderRows(data.items);
      $('message').textContent = `共 ${data.total} 只，启用 ${data.enabled_count} 只。股息率数据：${yieldCalculationDate || '暂无'}`;
    } catch (error) { displayError(error); }
  }
  async function change(symbol, isEnabled) {
    if (!confirm(isEnabled ? `确认重新启用 ${symbol}？` : `停用 ${symbol} 后仍会保留分红历史。确认停用？`)) return;
    try { await api(`/api/dividend/universe/${symbol}/status`, {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({is_enabled: isEnabled})}); await load(); } catch (error) { $('message').textContent = error.message; }
  }

  const candidateStats = summary => [
    ['候选总数', summary.qualified_count || 0],
    ['稳定垄断型', summary.stable_monopoly_count || 0],
    ['资源周期型', summary.resource_monopoly_cyclical_count || 0],
    ['普通高股息观察型', summary.high_dividend_watch_count || 0],
    ['已在正式池', summary.already_in_universe_count || 0],
    ['新增候选', summary.new_candidate_count || 0]
  ];
  function renderCandidates() {
    const query = $('candidate-search').value.trim().toLowerCase();
    const subtype = $('candidate-subtype').value;
      const visible = candidateItems.filter(item => (!query || `${item.symbol} ${item.company_name}`.toLowerCase().includes(query)) && (!subtype || item.suggested_stability_subtype === subtype)).sort((a, b) => {
      const left = a[candidateSortKey]; const right = b[candidateSortKey];
      if (left == null) return right == null ? 0 : 1;
      if (right == null) return -1;
      return candidateSortDirection === 'desc' ? Number(right) - Number(left) : Number(left) - Number(right);
    });
    document.querySelectorAll('[data-candidate-sort-arrow]').forEach(arrow => {
      arrow.textContent = arrow.dataset.candidateSortArrow === candidateSortKey ? (candidateSortDirection === 'desc' ? '↓' : '↑') : '↕';
    });
    $('candidate-summary').innerHTML = candidateStats(candidateSummary).map(([label, value]) => `<span><small>${label}</small><strong>${value}</strong></span>`).join('');
    $('candidate-rows').innerHTML = visible.map(item => `<tr><td class="stock-cell"><strong>${item.company_name}</strong><small>${item.symbol}</small></td><td>${cell(item.industry)}</td><td><span class="dividend-tag ${item.suggested_stability_subtype === 'resource_monopoly_cyclical' ? 'cyclical' : ''}">${labels[item.suggested_stability_subtype]}</span></td>${[2023, 2024, 2025].map(year => `<td class="number-cell"><strong>${dps(item[`${year}_dps`])}</strong><small>${percentage(item[`${year}_historical_yield`])}</small></td>`).join('')}<td class="number-cell">${percentage(item.three_year_historical_average_yield)}</td><td class="number-cell">${price(item.latest_price)}<small>${cell(item.price_date)}</small></td><td class="number-cell ${getYieldClass(item.latest_year_yield)}">${percentage(item.latest_year_yield)}</td><td class="number-cell ${getYieldClass(item.three_year_average_yield)}">${percentage(item.three_year_average_yield)}</td><td>${item.already_in_universe ? '<span class="status-chip eligible">已在股票池</span>' : '<span class="status-chip neutral">新候选</span>'}</td><td>${item.already_in_universe ? '—' : `<button class="detail-button" data-add-candidate="${item.symbol}">加入股票池</button>`}</td></tr>`).join('') || '<tr><td colspan="12" class="empty-state">没有符合当前筛选的候选</td></tr>';
    document.querySelectorAll('[data-add-candidate]').forEach(button => button.onclick = () => addCandidate(button.dataset.addCandidate));
  }
  const stabilityLabel = value => ({stable: '&#31283;&#23450;', variable: '&#26377;&#27874;&#21160;', highly_variable: '&#22823;&#24133;&#27874;&#21160;'})[value] || '-';
  const candidateDpsCell = (item, year) => {
    const raw = item[`${year}_dps`];
    const basis = item[`${year}_current_basis_dps`];
    const adjusted = raw != null && basis != null && Math.abs(Number(raw) - Number(basis)) > 0.0000001;
    return `<td class="number-cell dps-cell"><strong>${dps(raw)}</strong><small>${percentage(item[`${year}_historical_yield`])}</small>${adjusted ? `<em>&#24403;&#21069;&#21475;&#24452; ${dps(basis)}</em>` : ''}</td>`;
  };
  function renderCandidates() {
    const query = $('candidate-search').value.trim().toLowerCase();
    const subtype = $('candidate-subtype').value;
    const visible = candidateItems.filter(item => (!query || `${item.symbol} ${item.company_name}`.toLowerCase().includes(query)) && (!subtype || item.suggested_stability_subtype === subtype)).sort((a, b) => {
      const left = a[candidateSortKey]; const right = b[candidateSortKey];
      if (left == null) return right == null ? 0 : 1;
      if (right == null) return -1;
      return candidateSortDirection === 'desc' ? Number(right) - Number(left) : Number(left) - Number(right);
    });
    document.querySelectorAll('[data-candidate-sort-arrow]').forEach(arrow => { arrow.textContent = ''; });
    $('candidate-summary').innerHTML = candidateStats(candidateSummary).map(([label, value]) => `<span><small>${label}</small><strong>${value}</strong></span>`).join('');
    $('candidate-rows').innerHTML = visible.map(item => `<tr><td class="stock-cell"><strong>${item.company_name}</strong><small>${item.symbol}</small></td><td class="industry-cell">${cell(item.industry)}</td><td><span class="dividend-tag ${item.suggested_stability_subtype === 'resource_monopoly_cyclical' ? 'cyclical' : ''}">${labels[item.suggested_stability_subtype]}</span></td>${[2023, 2024, 2025].map(year => candidateDpsCell(item, year)).join('')}<td class="number-cell">${price(item.latest_price)}<small>${cell(item.price_date)}</small></td><td class="number-cell ${getYieldClass(item.latest_year_yield)}">${percentage(item.latest_year_yield)}</td><td class="number-cell ${getYieldClass(item.three_year_average_yield)}">${percentage(item.three_year_average_yield)}</td><td class="number-cell ${getYieldClass(item.conservative_three_year_current_yield)}">${percentage(item.conservative_three_year_current_yield)}</td><td class="number-cell">${item.dividend_variation_ratio == null ? '-' : Number(item.dividend_variation_ratio).toFixed(2)}</td><td><span class="stability-tag ${item.dividend_stability || ''}">${stabilityLabel(item.dividend_stability)}</span></td><td class="basis-date">${cell(item.share_basis_as_of)}</td><td>${item.already_in_universe ? '<span class="status-chip eligible">&#24050;&#22312;&#32929;&#31080;&#27744;</span>' : '<span class="status-chip neutral">&#26032;&#20505;&#36873;</span>'}</td><td>${item.already_in_universe ? '-' : `<button class="detail-button" data-add-candidate="${item.symbol}">&#21152;&#20837;&#32929;&#31080;&#27744;</button>`}</td></tr>`).join('') || '<tr><td colspan="14" class="empty-state">&#27809;&#26377;&#31526;&#21512;&#24403;&#21069;&#31679;&#36873;&#30340;&#20505;&#36873;</td></tr>';
    document.querySelectorAll('[data-add-candidate]').forEach(button => button.onclick = () => addCandidate(button.dataset.addCandidate));
    requestAnimationFrame(syncCandidateScrollbars);
  }
  function applyCandidateResult(result) {
    if (result.status === 'never_run') { $('scan-results').hidden = true; return; }
    candidateItems = result.items || []; candidateSummary = result.summary || {};
    const completed = candidateSummary.completed_at ? new Date(candidateSummary.completed_at).toLocaleString() : '未知';
    const priceRefreshed = candidateSummary.candidate_price_refresh_at ? new Date(candidateSummary.candidate_price_refresh_at).toLocaleString() : null;
    const priceLabel = priceRefreshed ? `；价格刷新：${priceRefreshed}（价格日期：${candidateSummary.candidate_price_date || '—'}）` : '';
    $('scan-meta').textContent = `扫描时间：${completed}；总耗时：${candidateSummary.elapsed_seconds ?? '-'} 秒；候选：${candidateSummary.qualified_count ?? candidateItems.length} 只${priceLabel}`;
    $('scan-results').hidden = false; renderCandidates();
  }
  async function loadCandidates() {
    try { applyCandidateResult(await api('/api/dividend/universe/rescan/latest')); } catch (error) { $('scan-results').hidden = false; $('scan-meta').textContent = `上一次候选结果读取失败：${error.message}`; }
  }
  async function addCandidate(symbol) {
    if (!confirm(`确认将 ${symbol} 按扫描建议类型加入正式股票池？`)) return;
    try {
      const result = await api(`/api/dividend/universe/rescan/candidates/${symbol}/add`, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({confirm: true})});
      $('message').textContent = result.status === 'added' ? `${symbol} 已加入股票池。` : `${symbol} 已在股票池。`;
      await Promise.all([load(), loadCandidates()]);
    } catch (error) { $('message').textContent = `加入股票池失败：${error.message}`; }
  }
  async function poll(runId) {
    try {
      const run = await api('/api/dividend/universe/rescan/' + runId);
      if (run.status === 'running') return setTimeout(() => poll(runId), 1500);
      $('rescan').disabled = false; $('rescan').textContent = '重新筛选候选池';
      if (run.status === 'failed') { $('message').textContent = `筛选失败：${run.error}`; return; }
      applyCandidateResult(run); $('message').textContent = `候选池筛选完成，共 ${run.summary.qualified_count} 只。`;
    } catch (error) { $('rescan').disabled = false; $('rescan').textContent = '重新筛选候选池'; $('message').textContent = error.message; }
  }
  $('rescan').onclick = async () => {
    const button = $('rescan');
    try { button.disabled = true; button.textContent = '正在重新筛选…'; $('message').textContent = '正在重新筛选候选池...'; const run = await api('/api/dividend/universe/rescan', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'}); poll(run.run_id); }
    catch (error) { button.disabled = false; button.textContent = '重新筛选候选池'; $('message').textContent = error.message; }
  };
  $('refresh-yields').onclick = async () => {
    const button = $('refresh-yields');
    button.disabled = true; button.textContent = '正在刷新...';
    try { await api('/api/dividend/yields/refresh', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'}); await Promise.all([load(), loadCandidates()]); }
    catch (error) { $('message').textContent = `刷新股息率失败：${error.message}`; }
    finally { button.disabled = false; button.textContent = '刷新股息率'; }
  };

  function openDialog() { selected = null; $('validation').textContent = ''; $('results').innerHTML = ''; $('add-monopoly').value = ''; $('reason').value = ''; $('ack').checked = false; $('confirm').disabled = true; $('dialog').showModal(); }
  async function validate(symbol) { try { selected = await api('/api/dividend/universe/validate', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({symbol})}); $('validation').textContent = selected.can_add ? `${selected.company_name}：DPS验证完成。${(selected.warnings || []).join('；')}` : (selected.warnings || []).join('；'); $('add-monopoly').value = selected.suggested_monopoly_type || ''; updateConfirm(); } catch (error) { $('validation').textContent = error.message; } }
  function updateConfirm() { $('confirm').disabled = !selected || !selected.can_add || !$('ack').checked; }
  $('find').onclick = async () => { try { const data = await api('/api/dividend/universe/search?q=' + encodeURIComponent($('query').value)); $('results').innerHTML = data.items.map(item => `<button type="button" class="detail-button" data-candidate="${item.symbol}">选择并验证：${item.symbol} ${item.company_name}${item.already_in_universe ? '（已在池中）' : ''}</button>`).join('') || '<p>未找到可用A股</p>'; document.querySelectorAll('[data-candidate]').forEach(button => button.onclick = () => validate(button.dataset.candidate)); if (data.items.length === 1) validate(data.items[0].symbol); } catch (error) { $('validation').textContent = error.message; } };
  $('confirm').onclick = async () => { if (!selected || !$('ack').checked) return; try { const result = await api('/api/dividend/universe', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({symbol: selected.symbol, stability_subtype: $('add-subtype').value, monopoly_type: $('add-monopoly').value, manual_reason: $('reason').value, acknowledge_warnings: true})}); if (result.status === 'added') { $('dialog').close(); await load(); } else $('validation').textContent = '该公司已在股票池。'; } catch (error) { $('validation').textContent = error.message; } };
  $('add').onclick = openDialog; $('empty-add').onclick = openDialog; $('retry').onclick = load;
  $('dialog-close').onclick = () => $('dialog').close(); $('dialog').addEventListener('click', event => { if (event.target === $('dialog')) $('dialog').close(); });
  $('ack').onchange = updateConfirm;
  $('dividend-top-scroll').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('dividend-table-wrap').scrollLeft = $('dividend-top-scroll').scrollLeft; syncingScroll = false; });
  $('dividend-table-wrap').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('dividend-top-scroll').scrollLeft = $('dividend-table-wrap').scrollLeft; syncingScroll = false; });
  $('candidate-top-scroll').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('candidate-table-wrap').scrollLeft = $('candidate-top-scroll').scrollLeft; syncingScroll = false; });
  $('candidate-table-wrap').addEventListener('scroll', () => { if (syncingScroll) return; syncingScroll = true; $('candidate-top-scroll').scrollLeft = $('candidate-table-wrap').scrollLeft; syncingScroll = false; });
  window.addEventListener('resize', () => { syncScrollbars(); syncCandidateScrollbars(); });
  ['disabled', 'subtype'].forEach(id => $(id).onchange = load);
  $('search').oninput = () => { clearTimeout(window.dividendSearchTimer); window.dividendSearchTimer = setTimeout(load, 250); };
  ['candidate-search', 'candidate-subtype'].forEach(id => $(id).oninput = renderCandidates);
  $('candidate-sort').oninput = () => {
    candidateSortKey = {'latest-current': 'latest_year_yield', 'average-current': 'three_year_average_yield', 'conservative-current': 'conservative_three_year_current_yield'}[$('candidate-sort').value] || 'three_year_historical_average_yield';
    candidateSortDirection = 'desc'; renderCandidates();
  };
  document.querySelectorAll('[data-candidate-sort-key]').forEach(header => header.onclick = () => {
    candidateSortDirection = candidateSortKey === header.dataset.candidateSortKey && candidateSortDirection === 'desc' ? 'asc' : 'desc';
    candidateSortKey = header.dataset.candidateSortKey;
    $('candidate-sort').value = candidateSortKey === 'latest_year_yield' ? 'latest-current' : candidateSortKey === 'conservative_three_year_current_yield' ? 'conservative-current' : 'average-current';
    renderCandidates();
  });
  load();
  loadCandidates();
})();
