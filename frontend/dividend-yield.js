(() => {
  const $ = id => document.getElementById(id);
  const threeYear = location.pathname.endsWith('three-year');
  const selectedYield = threeYear ? 'three_year_average_yield' : 'latest_year_yield';
  const subtype = { stable_monopoly: '\u7a33\u5b9a\u5784\u65ad\u578b', resource_monopoly_cyclical: '\u8d44\u6e90\u5468\u671f\u578b' };
  let items = []; let years = [];
  const number = (value, digits) => value == null ? '\u2014' : Number(value).toFixed(digits);
  const percent = value => value == null ? '\u2014' : `${(Number(value) * 100).toFixed(2)}%`;
  function render() {
    const minimum = Math.max(0, Number($('min').value) || 0) / 100;
    const search = $('q').value.trim(); const type = $('type').value;
    const shown = items.filter(item => (item[selectedYield] ?? -1) >= minimum && (!search || `${item.symbol}${item.company_name}`.includes(search)) && (!type || item.stability_subtype === type)).sort((left, right) => (right[selectedYield] ?? -1) - (left[selectedYield] ?? -1));
    $('msg').textContent = shown.length ? `\u7b26\u5408\u6761\u4ef6 ${shown.length} \u53ea` : '\u5f53\u524d\u6ca1\u6709\u7b26\u5408\u6761\u4ef6\u7684\u80a1\u7968';
    const dpsHeaders = threeYear ? `${years.map(year => `<th class="number">${year} DPS</th>`).join('')}<th class="number">\u4e09\u5e74\u5e73\u5747 DPS</th>` : '';
    $('head').innerHTML = `<tr><th>\u80a1\u7968</th><th>\u884c\u4e1a</th><th class="center">\u7c7b\u578b</th><th class="number">\u6700\u65b0\u4ef7</th><th>\u4ef7\u683c\u65e5\u671f</th>${dpsHeaders}<th class="number">${threeYear ? '\u4e09\u5e74\u5e73\u5747' : '\u53bb\u5e74'}\u80a1\u606f\u7387</th><th class="number">${threeYear ? '\u53bb\u5e74' : '\u4e09\u5e74\u5e73\u5747'}\u80a1\u606f\u7387</th><th class="center">\u72b6\u6001</th></tr>`;
    $('rows').innerHTML = shown.map(item => `<tr><td class="stock-cell"><strong>${item.company_name}</strong><small>${item.symbol}</small></td><td>${item.industry_level_1 || '\u2014'}</td><td class="center"><span class="dividend-tag ${item.stability_subtype === 'resource_monopoly_cyclical' ? 'cyclical' : ''}">${subtype[item.stability_subtype] || item.stability_subtype}</span></td><td class="number">${number(item.latest_price, 2)}</td><td>${item.price_date || '\u2014'}</td>${threeYear ? `${years.map(year => `<td class="number">${number(item.annual_dps?.[year], 4)}</td>`).join('')}<td class="number">${number(item.three_year_average_dps, 4)}</td>` : ''}<td class="number">${percent(item[selectedYield])}</td><td class="number">${percent(threeYear ? item.latest_year_yield : item.three_year_average_yield)}</td><td class="center">${item.data_quality_status}</td></tr>`).join('') || `<tr><td colspan="12" class="empty-state">\u5f53\u524d\u6ca1\u6709\u7b26\u5408\u6761\u4ef6\u7684\u80a1\u7968</td></tr>`;
  }
  async function load() {
    try { $('msg').textContent = '\u6b63\u5728\u52a0\u8f7d\u9ad8\u80a1\u606f\u6570\u636e...'; const response = await fetch('/api/dividend/yields'); if (!response.ok) throw Error(`\u540e\u7aef\u8fd4\u56de ${response.status}`); const data = await response.json(); items = data.items || []; years = data.target_years || []; render(); }
    catch (error) { $('msg').textContent = `\u9ad8\u80a1\u606f\u6570\u636e\u52a0\u8f7d\u5931\u8d25\uff1a${error.message}`; }
  }
  $('title').textContent = threeYear ? '\u4e09\u5e74\u5e73\u5747\u9ad8\u80a1\u606f' : '\u53bb\u5e74\u9ad8\u80a1\u606f';
  ['q', 'type', 'min'].forEach(id => $(id).addEventListener('input', render));
  $('refresh').onclick = async () => { const button = $('refresh'); button.disabled = true; try { const response = await fetch('/api/dividend/yields/refresh', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({calculation_date: '2026-08-07'}) }); if (!response.ok) throw Error('\u5237\u65b0\u5931\u8d25'); await load(); } catch (error) { $('msg').textContent = `\u5237\u65b0\u8ba1\u7b97\u7ed3\u679c\u5931\u8d25\uff1a${error.message}`; } finally { button.disabled = false; } };
  load();
})();
