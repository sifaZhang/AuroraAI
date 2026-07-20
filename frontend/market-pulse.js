(function (root) {
  "use strict";

  const TERMINAL_STATUSES = new Set(["completed", "partial", "failed"]);
  const SOURCE_ORDER = ["sw_l1", "sw_l2", "eastmoney"];
  const SOURCE_NAMES = {sw_l1: "申万一级行业", sw_l2: "申万二级行业", eastmoney: "东方财富行业"};
  const TABLE_SOURCE_NAMES = {sw_l1: "申万一级", sw_l2: "申万二级", eastmoney: "东方财富"};
  const STATUS_NAMES = {healthy: "正常", degraded: "部分可用", unavailable: "不可用", unknown: "未检查"};
  const JOB_STATUS_NAMES = {queued: "排队中", running: "刷新中", completed: "已完成", partial: "部分完成", failed: "失败"};
  const LEVEL_NAMES = {strong: "A", bullish: "B", neutral: "C", weak: "D", bearish: "E"};

  class RequestError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data; }
  }

  function formatError(value, fallback) {
    const raw = String(value || fallback || "未知错误").replace(/\s+/g, " ").trim();
    return raw.length > 120 ? `${raw.slice(0, 117)}…` : raw;
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      const parts = new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).formatToParts(parsed);
      const part = type => parts.find(item => item.type === type)?.value || "";
      return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
    }
    return String(value).replace("T", " ").slice(0, 16);
  }

  function formatDuration(startedAt, finishedAt, now = Date.now()) {
    if (!startedAt) return "—";
    const start = new Date(startedAt).getTime();
    const finish = finishedAt ? new Date(finishedAt).getTime() : now;
    if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return "—";
    const seconds = Math.round((finish - start) / 1000);
    return seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  }

  function getScoreClass(value) {
    const score = Number(value);
    if (score >= 70) return "score-70";
    if (score >= 60) return "score-60";
    if (score >= 50) return "score-50";
    if (score >= 40) return "score-40";
    if (score >= 30) return "score-30";
    return "score-low";
  }

  // Display fallback only; business scoring remains on the backend.
  function fallbackLevel(score) {
    if (Number(score) >= 70) return "A";
    if (Number(score) >= 60) return "B";
    if (Number(score) >= 50) return "C";
    if (Number(score) >= 40) return "D";
    return "E";
  }

  function createDashboard(options) {
    const doc = options.document;
    const fetcher = options.fetch;
    const setTimer = options.setTimeout || setTimeout;
    const clearTimer = options.clearTimeout || clearTimeout;
    const now = options.now || (() => Date.now());
    const AbortControllerClass = options.AbortController || root?.AbortController;
    const state = {sectors: [], marketMeta: null, sourceHealth: [], activeJobId: null, pollTimer: null, refreshStartedAt: null, pollErrors: 0};
    const byId = id => doc.getElementById(id);
    const elements = {
      rows: byId("sector-rows"), statuses: byId("source-statuses"), button: byId("refresh-sectors"), pageError: byId("pulse-error"),
      sectorError: byId("sector-error"), healthError: byId("health-error"), tradeDate: byId("last-trade-date"), lastRefresh: byId("last-refresh"),
      marketStatus: byId("market-status"), count: byId("sector-count"), jobStatus: byId("job-status"), jobCounts: byId("job-counts"),
      jobStep: byId("job-step"), jobStarted: byId("job-started"), jobFinished: byId("job-finished"), jobDuration: byId("job-duration"),
      jobProgress: byId("job-progress"), jobError: byId("job-error")
    };

    function showError(element, message) { element.textContent = message || ""; element.hidden = !message; }
    function clearChildren(element) { while (element.firstChild) element.removeChild(element.firstChild); }
    function appendText(parent, tag, text, className) { const node = doc.createElement(tag); node.textContent = text; if (className) node.className = className; parent.appendChild(node); return node; }

    async function fetchJson(url, init = {}, timeoutMs = 30000) {
      let timer = null;
      const controller = AbortControllerClass ? new AbortControllerClass() : null;
      try {
        if (controller) timer = setTimer(() => controller.abort(), timeoutMs);
        const response = await fetcher(url, {...init, ...(controller ? {signal: controller.signal} : {})});
        let data;
        try { data = await response.json(); } catch (_) { throw new RequestError("服务器返回了无法解析的数据", response.status, null); }
        if (!response.ok) {
          const detail = data?.detail;
          const message = typeof detail === "string" ? detail : detail?.message || data?.message || `HTTP ${response.status}`;
          throw new RequestError(message, response.status, data);
        }
        return data;
      } catch (error) {
        if (error?.name === "AbortError") throw new RequestError("请求超时", 0, null);
        throw error;
      } finally { if (timer !== null) clearTimer(timer); }
    }

    function resolveMarketStatus(meta) {
      const direct = meta?.source_status?.status;
      if (direct) return direct;
      return state.sourceHealth.find(item => item.source === "sw_l1")?.status || "unknown";
    }

    function renderSummary() {
      const meta = state.marketMeta || {};
      elements.tradeDate.textContent = meta.latest_trade_date || meta.trade_date || "—";
      const status = resolveMarketStatus(meta);
      elements.marketStatus.textContent = `${status === "healthy" ? "🟢 " : status === "degraded" ? "🟡 " : status === "unavailable" ? "🔴 " : "⚪ "}${STATUS_NAMES[status] || status}`;
      const latest = state.sectors.reduce((value, item) => !value || String(item.updated_at) > String(value) ? item.updated_at : value, null) || meta.source_status?.updated_at;
      elements.lastRefresh.textContent = formatDateTime(latest);
    }

    function emptyMessage(meta) {
      const status = resolveMarketStatus(meta);
      if (status === "unavailable") return "暂无行业数据，当前数据源不可用。";
      if (status === "unknown") return "暂无行业数据，数据源尚未检查。";
      if (status === "healthy") return "数据源正常，但当前没有已保存的行业评分。请尝试刷新。";
      return "暂无行业趋势数据。";
    }

    function renderSectorTable(data) {
      state.marketMeta = data || {};
      state.sectors = Array.isArray(data?.items) ? data.items : [];
      clearChildren(elements.rows);
      elements.count.textContent = `${state.sectors.length} 个行业`;
      if (!state.sectors.length) {
        const row = doc.createElement("tr"); const cell = appendText(row, "td", emptyMessage(data), "empty-state"); cell.colSpan = 7; elements.rows.appendChild(row); renderSummary(); return;
      }
      state.sectors.forEach((item, index) => {
        const row = doc.createElement("tr");
        appendText(row, "td", String(index + 1));
        appendText(row, "td", item.sector_name || "—");
        const scoreCell = doc.createElement("td"); appendText(scoreCell, "span", `${item.trend_score ?? "—"}/${item.trend_max_score || 70}`, `trend-score ${getScoreClass(item.trend_score)}`); row.appendChild(scoreCell);
        const levelCell = doc.createElement("td"); appendText(levelCell, "span", LEVEL_NAMES[item.trend_level] || item.trend_level || fallbackLevel(item.trend_score), "trend-level"); row.appendChild(levelCell);
        appendText(row, "td", item.trade_date || data.latest_trade_date || data.trade_date || "—");
        appendText(row, "td", formatDateTime(item.updated_at), "secondary-column");
        appendText(row, "td", TABLE_SOURCE_NAMES[item.source] || item.source || "—", "secondary-column");
        elements.rows.appendChild(row);
      });
      renderSummary();
    }

    function renderSourceHealth(data) {
      state.sourceHealth = Array.isArray(data?.items) ? data.items : [];
      const indexed = Object.fromEntries(state.sourceHealth.map(item => [item.source, item]));
      clearChildren(elements.statuses);
      SOURCE_ORDER.forEach(source => {
        const item = indexed[source] || {source, status: "unknown"};
        const row = doc.createElement("div"); row.className = "source-status-row";
        const main = doc.createElement("div"); main.className = "source-main";
        appendText(main, "span", item.display_name || SOURCE_NAMES[source], "source-name");
        appendText(main, "span", STATUS_NAMES[item.status] || item.status || STATUS_NAMES.unknown, `source-state ${item.status || "unknown"}`);
        row.appendChild(main);
        if (item.status !== "healthy" && (item.last_error_message || item.last_error)) {
          const full = String(item.last_error_message || item.last_error); const error = appendText(row, "small", formatError(full), "source-error"); error.title = full;
        } else if (item.status === "healthy" && item.last_success_at) appendText(row, "small", `上次成功：${formatDateTime(item.last_success_at)}`, "source-success");
        elements.statuses.appendChild(row);
      });
      renderSummary();
    }

    async function loadMarketPulse({preserve = false} = {}) {
      showError(elements.sectorError, "");
      try { const data = await fetchJson("/api/market-pulse/sectors", {}, 30000); renderSectorTable(data); return data; }
      catch (error) { showError(elements.sectorError, "行业数据加载失败，请确认后端服务是否正在运行。"); if (!preserve) { state.sectors = []; elements.count.textContent = "加载失败"; } throw error; }
    }

    async function loadSourceHealth() {
      showError(elements.healthError, "");
      try { const data = await fetchJson("/api/data-source-health", {}, 30000); renderSourceHealth(data); return data; }
      catch (error) { showError(elements.healthError, "数据源状态加载失败，请稍后重试。"); throw error; }
    }

    async function load() {
      const results = await Promise.allSettled([loadMarketPulse(), loadSourceHealth()]);
      renderSummary();
      return results;
    }

    function setRefreshButtonState(active, submitting = false) {
      elements.button.disabled = active;
      elements.button.textContent = active ? (submitting ? "正在提交…" : "刷新中…") : "刷新板块数据";
    }

    function renderRefreshJob(job) {
      elements.jobStatus.textContent = JOB_STATUS_NAMES[job.status] || job.status || "—";
      const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
      elements.jobProgress.value = progress;
      elements.jobCounts.textContent = `${job.completed_count ?? 0} / ${job.total_count ?? 0}（${progress.toFixed(0)}%）`;
      elements.jobStep.textContent = job.current_step || "—";
      elements.jobStarted.textContent = formatDateTime(job.started_at);
      elements.jobFinished.textContent = formatDateTime(job.finished_at);
      elements.jobDuration.textContent = formatDuration(job.started_at, job.finished_at, now());
      showError(elements.jobError, job.error_message ? formatError(job.error_message) : "");
    }

    function stopPolling() { if (state.pollTimer !== null) clearTimer(state.pollTimer); state.pollTimer = null; }

    async function finishJob(job) {
      stopPolling(); state.activeJobId = null; setRefreshButtonState(false); renderRefreshJob(job);
      if (job.status === "completed" || job.status === "partial") await Promise.allSettled([loadMarketPulse({preserve: true}), loadSourceHealth()]);
      else await Promise.allSettled([loadSourceHealth()]);
      renderSummary(); return job;
    }

    async function pollRefreshJob(jobId) {
      if (state.refreshStartedAt && now() - state.refreshStartedAt > 600000) {
        stopPolling(); state.activeJobId = null; setRefreshButtonState(false); showError(elements.jobError, "刷新任务等待超时，请检查数据源状态或稍后重试。"); return null;
      }
      try {
        const job = await fetchJson(`/api/market-pulse/refresh/${jobId}`, {}, 15000);
        state.pollErrors = 0; renderRefreshJob(job);
        if (TERMINAL_STATUSES.has(job.status)) return finishJob(job);
        state.pollTimer = setTimer(() => { pollRefreshJob(jobId); }, 2000); return job;
      } catch (error) {
        state.pollErrors += 1;
        if (state.pollErrors < 3) { showError(elements.jobError, "刷新任务查询失败，正在重试。"); state.pollTimer = setTimer(() => { pollRefreshJob(jobId); }, 2000); return null; }
        stopPolling(); state.activeJobId = null; setRefreshButtonState(false); showError(elements.jobError, `刷新任务查询失败：${formatError(error.message)}`); return null;
      }
    }

    async function startRefresh() {
      if (state.activeJobId) return null;
      stopPolling(); state.pollErrors = 0; state.refreshStartedAt = now(); setRefreshButtonState(true, true); showError(elements.jobError, ""); showError(elements.pageError, "");
      renderRefreshJob({status: "queued", progress: 0, completed_count: 0, total_count: 0, current_step: "正在提交刷新任务"});
      try {
        const job = await fetchJson("/api/market-pulse/refresh", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source: "sw_l1"})}, 30000);
        state.activeJobId = job.job_id; setRefreshButtonState(true); renderRefreshJob(job); return pollRefreshJob(job.job_id);
      } catch (error) {
        const existingJobId = error.status === 409 ? error.data?.detail?.existing_job_id : null;
        if (existingJobId) {
          state.activeJobId = existingJobId; setRefreshButtonState(true); renderRefreshJob({status: "running", current_step: "已有刷新任务正在运行，正在接管任务状态。"}); return pollRefreshJob(existingJobId);
        }
        state.activeJobId = null; setRefreshButtonState(false); showError(elements.jobError, `刷新提交失败：${formatError(error.message)}`); return null;
      }
    }

    function init() { elements.button.addEventListener("click", () => { startRefresh(); }); if (root?.addEventListener) root.addEventListener("beforeunload", stopPolling); return load(); }
    return {state, init, load, loadMarketPulse, loadSourceHealth, startRefresh, pollRefreshJob, renderSectorTable, renderSourceHealth, renderRefreshJob, stopPolling};
  }

  const api = {createDashboard, getScoreClass, formatDateTime, formatDuration, formatError};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) { root.MarketPulseDashboard = api; if (root.document && !root.__MARKET_PULSE_TEST__) root.document.addEventListener("DOMContentLoaded", () => createDashboard({document: root.document, fetch: root.fetch.bind(root)}).init()); }
})(typeof window !== "undefined" ? window : null);
