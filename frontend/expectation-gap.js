const state = { market: "all", q: "", sort_by: "morningstar_gap_pct", sort_order: "desc", page: 1, page_size: 50, include_unrated: true, total: 0 };
const $ = (selector) => document.querySelector(selector);
const rows = $("#rows");

function valueOrDash(value, digits = 2) { return value === null || value === undefined ? "—" : Number(value).toFixed(digits); }
function gap(value) { if (value === null || value === undefined) return '<span class="muted">—</span>'; const cls = Number(value) >= 0 ? "positive" : "negative"; return `<span class="${cls}">${Number(value).toFixed(2)}%</span>`; }
function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value ?? ""; return div.innerHTML; }

function syncUrl() { const params = new URLSearchParams(); Object.entries(state).forEach(([key, value]) => { if (key !== "total" && value !== "" && value !== false) params.set(key, value); }); history.replaceState(null, "", `${location.pathname}?${params}`); }
function readUrl() { const p = new URLSearchParams(location.search); ["market", "q", "sort_by", "sort_order"].forEach(k => { if (p.has(k)) state[k] = p.get(k); }); ["page", "page_size"].forEach(k => { if (p.has(k)) state[k] = Number(p.get(k)); }); if (p.has("include_unrated")) state.include_unrated = p.get("include_unrated") === "true"; }

async function load() {
  syncUrl(); rows.innerHTML = '<tr><td colspan="12" class="empty-state">加载中…</td></tr>'; $("#error").hidden = true;
  const params = new URLSearchParams(state); params.delete("total");
  try {
    const response = await fetch(`/api/expectation-gaps?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json(); state.total = data.total;
    $("#total-count").textContent = `${data.total} 条`; $("#last-refresh").textContent = `最后更新：${data.last_refresh?.finished_at || "—"}`;
    $("#page-label").textContent = `第 ${state.page} 页 / 共 ${Math.max(1, Math.ceil(data.total / state.page_size))} 页`;
    $("#prev").disabled = state.page <= 1; $("#next").disabled = state.page * state.page_size >= data.total;
    if (!data.items.length) { rows.innerHTML = '<tr><td colspan="12" class="empty-state">没有符合条件的数据</td></tr>'; return; }
    rows.innerHTML = data.items.map(item => `<tr>
      <td><span class="market-tag ${item.market.toLowerCase()}">${item.market === "A" ? "A股" : "港股"}</span></td>
      <td>${escapeHtml(item.symbol)}</td><td class="stock-name">${escapeHtml(item.name)}</td>
      <td>${valueOrDash(item.last_price)}</td><td>${valueOrDash(item.morningstar_fair_value)}</td><td>${gap(item.morningstar_gap_pct)}</td>
      <td>${item.morningstar_star_rating == null ? "—" : "★".repeat(item.morningstar_star_rating)}</td>
      <td>${valueOrDash(item.analyst_average_target)}</td><td>${gap(item.analyst_gap_pct)}</td><td>${item.analyst_count ?? "—"}</td>
      <td>${escapeHtml(item.data_date || "—")}</td><td><span class="source-tag">${escapeHtml(item.display_source)}</span></td></tr>`).join("");
  } catch (error) { $("#error").hidden = false; $("#error").textContent = `加载失败：${error.message}。请确认本地API已启动。`; rows.innerHTML = '<tr><td colspan="12" class="empty-state">数据加载失败</td></tr>'; }
}

let timer; $("#search").addEventListener("input", e => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; state.page = 1; load(); }, 300); });
$("#market").addEventListener("change", e => { state.market = e.target.value; state.page = 1; load(); });
$("#include-unrated").addEventListener("change", e => { state.include_unrated = e.target.checked; state.page = 1; load(); });
$("#page-size").addEventListener("change", e => { state.page_size = Number(e.target.value); state.page = 1; load(); });
$("#reset").addEventListener("click", () => { Object.assign(state, {market:"all",q:"",sort_by:"morningstar_gap_pct",sort_order:"desc",page:1,page_size:50,include_unrated:true}); applyControls(); load(); });
document.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => { const field = button.dataset.sort; state.sort_order = state.sort_by === field && state.sort_order === "desc" ? "asc" : "desc"; state.sort_by = field; state.page = 1; load(); }));
$("#prev").addEventListener("click", () => { if (state.page > 1) { state.page--; load(); } }); $("#next").addEventListener("click", () => { if (state.page * state.page_size < state.total) { state.page++; load(); } });
function applyControls() { $("#search").value=state.q; $("#market").value=state.market; $("#include-unrated").checked=state.include_unrated; $("#page-size").value=String(state.page_size); }
readUrl(); applyControls(); load();
