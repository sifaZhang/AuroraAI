(function (root, factory) {
  "use strict";
  const exported = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (root) root.FirstLimitPage = exported;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";

  const ACTIVE_LIFECYCLES = [
    "confirmed", "eligible", "pending_close_confirmation", "watching", "indeterminate",
  ];
  const LIFECYCLE_NAMES = {
    watching: "观察中", eligible: "尾盘合格",
    pending_close_confirmation: "等待收盘确认", confirmed: "已确认",
    eliminated: "已淘汰", expired: "已过期", indeterminate: "无法确定",
  };
  const STAGE_NAMES = {tail_preview: "尾盘预警", close_confirmed: "收盘确认"};
  const RUN_STATUS_NAMES = {running: "运行中", success: "成功", partial: "部分完成", failed: "失败"};
  const ITEM_STATUS_NAMES = {
    pending: "未决", success: "成功", indeterminate: "无法确定",
    skipped: "跳过", failed: "失败",
  };
  const CHANGE_NAMES = {
    unchanged: "不变", upgraded: "升级", downgraded: "降级",
    newly_qualified: "收盘新增", eliminated: "收盘淘汰",
    preview_missing: "缺少尾盘快照",
  };
  const EVIDENCE_NAMES = {pass: "通过", fail: "未通过", unknown: "数据不足/无法确定"};
  const STORAGE_PREFIX = "aurora.firstLimit.";

  class RequestError extends Error {
    constructor(message, status, code, details) {
      super(message);
      this.status = status;
      this.code = code || "request_failed";
      this.details = details || {};
    }
  }

  function formatValue(value) {
    if (value === null || value === undefined) return "—";
    if (value === "") return '""';
    if (value === false) return "false";
    if (value === true) return "true";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).formatToParts(parsed);
    const part = type => parts.find(item => item.type === type)?.value || "";
    return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")} 北京时间`;
  }

  function localDate(now = new Date()) {
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function appendQuery(path, values) {
    const query = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach(item => query.append(key, item));
      else if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
    });
    const encoded = query.toString();
    return encoded ? `${path}?${encoded}` : path;
  }

  function createApi(fetcher) {
    async function request(url, init) {
      let response;
      try {
        response = await fetcher(url, init);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new RequestError("无法连接本地服务，请确认 AuroraAI 已启动。", 0, "network_error");
      }
      let data = null;
      try { data = await response.json(); }
      catch (_) {
        throw new RequestError("服务返回了无法解析的数据。", response.status, "invalid_response");
      }
      if (!response.ok) {
        const error = data?.error || {};
        throw new RequestError(
          error.message || `请求失败（HTTP ${response.status}）`,
          response.status, error.code || `http_${response.status}`, error.details,
        );
      }
      return data;
    }
    return {
      get: (url, signal) => request(url, signal ? {signal} : undefined),
      post: (url, body) => request(url, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      }),
      del: url => request(url, {method: "DELETE"}),
    };
  }

  function createPage(options) {
    const doc = options.document;
    const storage = options.storage || root?.localStorage;
    const api = createApi(options.fetch);
    const now = options.now || (() => new Date());
    const byId = id => doc.getElementById(id);
    const elements = {};
    [
      "trade-date", "stage", "symbol-filter", "refresh-results", "run-preview", "run-close",
      "run-message", "page-error", "page-data-time", "page-updated-time", "latest-run-state",
      "overview-grid", "overview-note", "toggle-versions", "version-details",
      "grade-filter", "lifecycle-filter", "candidate-sort", "candidate-order",
      "candidate-page-size", "candidate-rows", "candidate-prev", "candidate-next",
      "candidate-page-label", "change-filter", "comparison-grade", "comparison-rows",
      "comparison-prev", "comparison-next", "comparison-page-label", "run-rows",
      "run-prev", "run-next", "run-page-label", "run-detail-panel", "selected-run-label",
      "run-detail-summary", "item-status-filter", "item-rows", "item-prev", "item-next",
      "item-page-label", "candidate-modal",
      "candidate-modal-title", "close-candidate-modal", "candidate-detail-error",
      "candidate-detail-content", "tab-candidates", "tab-comparison", "tab-runs",
      "panel-candidates", "panel-comparison", "panel-runs",
      "pipeline-progress", "pipeline-status", "pipeline-percent",
      "pipeline-progress-bar", "pipeline-current-step", "pipeline-step-list",
      "pipeline-coverage-note", "pipeline-retry", "pipeline-cancel",
    ].forEach(id => { elements[id] = byId(id); });

    const saved = key => {
      try { return storage?.getItem(`${STORAGE_PREFIX}${key}`); } catch (_) { return null; }
    };
    const persist = (key, value) => {
      try { storage?.setItem(`${STORAGE_PREFIX}${key}`, value); } catch (_) {}
    };
    const validStage = value => ["tail_preview", "close_confirmed"].includes(value) ? value : "tail_preview";
    const state = {
      tradeDate: saved("trade_date") || localDate(now()),
      stage: validStage(saved("stage")),
      grade: saved("grade_filter") || "",
      lifecycle: saved("lifecycle_filter") ?? "active",
      sort: saved("sort") || "grade_rank",
      order: saved("order") || "asc",
      pageSize: Number(saved("page_size")) || 50,
      activeTab: saved("selected_tab") || "candidates",
      candidateOffset: 0, comparisonOffset: 0, runOffset: 0, itemOffset: 0,
      candidatePage: null, comparisonPage: null, runPage: null,
      runDetail: null, runItems: null, selectedCandidate: null,
      selectedRunId: null, pipelineJobId: Number(saved("pipeline_job_id")) || null,
      isRunning: false, pollGeneration: 0,
      requestIds: {candidates: 0, comparison: 0, runs: 0, detail: 0, items: 0},
    };

    function clear(element) {
      while (element?.firstChild) element.removeChild(element.firstChild);
    }
    function text(parent, tag, value, className) {
      const node = doc.createElement(tag);
      node.textContent = value;
      if (className) node.className = className;
      parent.appendChild(node);
      return node;
    }
    function cell(row, value, className) {
      return text(row, "td", value, className);
    }
    function emptyRow(target, columns, message, className = "empty-state") {
      clear(target);
      const row = doc.createElement("tr");
      const value = cell(row, message, className);
      value.colSpan = columns;
      target.appendChild(row);
    }
    function showError(element, error) {
      if (!element) return;
      if (!error) { element.textContent = ""; element.hidden = true; return; }
      const category = error instanceof RequestError
        ? error.status === 0 ? "网络错误"
          : error.status >= 500 ? "服务运行错误" : "请求参数或业务校验错误"
        : "页面错误";
      const message = error instanceof RequestError
        ? `${category}：${error.message}（${error.code}）` : "页面处理请求时发生错误。";
      element.textContent = message;
      element.hidden = false;
    }
    function statusChip(value, names = RUN_STATUS_NAMES) {
      const node = doc.createElement("span");
      node.className = `status-chip ${value || "neutral"}`;
      node.textContent = names[value] || value || "—";
      return node;
    }
    function gradeBadge(value) {
      const node = doc.createElement("span");
      node.className = `grade-badge grade-${value || "none"}`;
      node.textContent = value || "—";
      return node;
    }
    function setUpdated() {
      elements["page-updated-time"].textContent = `最近更新：${formatDateTime(new Date())}`;
    }

    function candidateUrl() {
      const lifecycle = state.lifecycle === "active" ? ACTIVE_LIFECYCLES
        : state.lifecycle ? [state.lifecycle] : [];
      return appendQuery("/api/first-limit/candidates", {
        trade_date: state.tradeDate, stage: state.stage,
        grade: state.grade ? [state.grade] : [],
        lifecycle, symbol: elements["symbol-filter"].value.trim(),
        sort: state.sort, order: state.order, limit: state.pageSize,
        offset: state.candidateOffset,
      });
    }

    function renderCandidates(page) {
      const target = elements["candidate-rows"];
      if (!page.run_id) {
        emptyRow(target, 12, "该交易日尚未生成此阶段结果。");
      } else if (!page.total) {
        const filtered = state.grade || state.lifecycle || elements["symbol-filter"].value.trim();
        emptyRow(target, 12, filtered ? "当前筛选条件没有结果。" : "运行已完成，但没有符合当前阶段条件的候选。");
      } else {
        clear(target);
        page.items.forEach(item => {
          const row = doc.createElement("tr");
          const grade = doc.createElement("td"); grade.appendChild(gradeBadge(item.grade)); row.appendChild(grade);
          cell(row, item.base_score == null ? "—" : Number(item.base_score).toFixed(1));
          cell(
            row,
            item.security_name
              ? `${item.security_name}（${item.symbol}）`
              : item.symbol,
          );
          cell(row, String(item.first_limit_event_id));
          cell(row, item.observation_day == null ? "—" : `D${item.observation_day}`);
          const lifecycle = doc.createElement("td"); lifecycle.appendChild(statusChip(item.lifecycle, LIFECYCLE_NAMES)); row.appendChild(lifecycle);
          cell(row, STAGE_NAMES[item.stage] || item.stage);
          cell(row, item.first_limit_date || "—");
          cell(row, item.display_text || item.reason_code || "—", "reason-cell");
          cell(row, CHANGE_NAMES[item.change_type] || item.change_type || "—", `change-label ${item.change_type || ""}`);
          cell(row, formatDateTime(item.as_of));
          const action = doc.createElement("td");
          const button = text(action, "button", "查看证据", "detail-button");
          button.type = "button";
          button.addEventListener("click", () => openCandidate(item.candidate_id));
          row.appendChild(action);
          target.appendChild(row);
        });
      }
      const pageNumber = Math.floor(page.offset / page.limit) + 1;
      const pages = Math.max(1, Math.ceil(page.total / page.limit));
      elements["candidate-page-label"].textContent = `第 ${pageNumber} / ${pages} 页 · 共 ${page.total} 条`;
      elements["candidate-prev"].disabled = page.offset <= 0;
      elements["candidate-next"].disabled = page.offset + page.limit >= page.total;
      elements["page-data-time"].textContent = `数据时间：${page.data_date} · ${STAGE_NAMES[page.stage]}`;
      const chip = elements["latest-run-state"];
      chip.className = `status-chip ${page.run_status || "neutral"}`;
      chip.textContent = page.run_status ? RUN_STATUS_NAMES[page.run_status] : "无运行";
    }

    async function loadOverview(runId, expectedRequest) {
      if (!runId) {
        renderOverview(null);
        return;
      }
      try {
        const detail = await api.get(`/api/first-limit/runs/${encodeURIComponent(runId)}`);
        if (expectedRequest !== state.requestIds.candidates) return;
        state.runDetail = detail;
        renderOverview(detail);
      } catch (error) {
        if (expectedRequest !== state.requestIds.candidates) return;
        renderOverview(null, error);
      }
    }

    function overviewStat(label, value, compact = false) {
      const wrapper = doc.createElement("div");
      wrapper.className = "overview-stat";
      text(wrapper, "span", label);
      text(wrapper, "strong", formatValue(value), compact ? "compact" : "");
      elements["overview-grid"].appendChild(wrapper);
    }

    function renderOverview(detail, error) {
      clear(elements["overview-grid"]);
      clear(elements["version-details"]);
      if (error) {
        overviewStat("运行详情", "加载失败", true);
        elements["overview-note"].className = "state-note error";
        elements["overview-note"].textContent = "候选列表仍可使用；运行详情加载失败，可点击刷新结果重试。";
        return;
      }
      if (!detail) {
        overviewStat("运行状态", "无运行", true);
        elements["overview-note"].className = "state-note";
        elements["overview-note"].textContent = "该交易日尚未生成此阶段结果。页面不会自动触发运行。";
        return;
      }
      const run = detail.run;
      const grades = detail.grade_counts || {};
      const lifecycles = detail.lifecycle_counts || {};
      const items = detail.item_status_counts || {};
      [
        ["交易日", run.trade_date, true], ["阶段", STAGE_NAMES[run.stage], true],
        ["Run ID", run.run_id, true], ["状态", RUN_STATUS_NAMES[run.status], true],
        ["S", grades.S || 0], ["A", grades.A || 0], ["B", grades.B || 0],
        ["无等级", grades.unknown || 0], ["观察中", lifecycles.watching || 0],
        ["尾盘合格", lifecycles.eligible || 0],
        ["等待收盘确认", lifecycles.pending_close_confirmation || 0],
        ["已确认", lifecycles.confirmed || 0], ["已淘汰", lifecycles.eliminated || 0],
        ["已过期", lifecycles.expired || 0], ["无法确定", lifecycles.indeterminate || 0],
        ["Item 成功", items.success || 0], ["Item 失败", items.failed || 0],
        ["Item 未决", items.pending || 0], ["as_of", formatDateTime(run.as_of), true],
        ["data_cutoff", formatDateTime(run.data_cutoff), true],
      ].forEach(item => overviewStat(...item));
      [
        ["strategy_version", run.strategy_version],
        ["detection_version", run.detection_version],
        ["pullback_version", run.pullback_version],
        ["context_version", run.context_version],
        ["parameter_hash", run.parameter_hash],
      ].forEach(([label, value]) => {
        const wrapper = doc.createElement("div");
        text(wrapper, "dt", label); text(wrapper, "dd", value || "—");
        elements["version-details"].appendChild(wrapper);
      });
      const note = elements["overview-note"];
      note.className = `state-note ${run.status === "failed" ? "error" : run.status === "partial" || run.status === "running" ? "warning" : ""}`;
      note.textContent = run.status === "partial"
        ? "部分事件处理失败，请在运行记录中查看失败 item。"
        : run.status === "failed" ? "本次运行失败，未形成完整可用结果，请查看错误摘要。"
        : run.status === "running" ? "运行仍处于 running；请刷新状态。若进程曾被强制终止，需要运维恢复。"
        : "运行已成功收敛，统计来自完整 run 账本而非当前候选页。";
    }

    async function loadCandidates() {
      const requestId = ++state.requestIds.candidates;
      showError(elements["page-error"], null);
      try {
        const page = await api.get(candidateUrl());
        if (requestId !== state.requestIds.candidates) return false;
        state.candidatePage = page;
        renderCandidates(page);
        await loadOverview(page.run_id, requestId);
        setUpdated();
        return true;
      } catch (error) {
        if (error?.name === "AbortError" || requestId !== state.requestIds.candidates) return false;
        showError(elements["page-error"], error);
        emptyRow(elements["candidate-rows"], 12, "候选加载失败，旧查询已被替换或本地服务不可用。", "empty-state error-state");
        return false;
      }
    }

    function comparisonUrl() {
      return appendQuery("/api/first-limit/preview-comparison", {
        trade_date: state.tradeDate,
        symbol: elements["symbol-filter"].value.trim(),
        change_type: elements["change-filter"].value,
        grade: elements["comparison-grade"].value,
        limit: state.pageSize, offset: state.comparisonOffset,
      });
    }

    function renderComparisons(page) {
      const target = elements["comparison-rows"];
      if (!page.run_id) {
        emptyRow(target, 8, "该交易日没有收盘确认 run，无法形成尾盘—收盘变化。");
      } else if (!page.total) {
        emptyRow(target, 8, "当前筛选没有变化记录；这不代表尾盘与收盘一定无变化。");
      } else {
        clear(target);
        page.items.forEach(item => {
          const row = doc.createElement("tr");
          cell(row, item.symbol); cell(row, item.preview_grade || "—");
          cell(row, LIFECYCLE_NAMES[item.preview_lifecycle] || item.preview_lifecycle || "—");
          cell(row, item.close_grade || "—");
          cell(row, LIFECYCLE_NAMES[item.close_lifecycle] || item.close_lifecycle);
          cell(row, CHANGE_NAMES[item.change_type], `change-label ${item.change_type}`);
          cell(row, item.change_display_text || item.change_reason_code || "—", "reason-cell");
          const action = doc.createElement("td");
          const button = text(action, "button", "查看收盘证据", "detail-button");
          button.type = "button";
          button.addEventListener("click", () => openCandidate(item.close_candidate_id));
          row.appendChild(action); target.appendChild(row);
        });
      }
      const number = Math.floor(page.offset / page.limit) + 1;
      const pages = Math.max(1, Math.ceil(page.total / page.limit));
      elements["comparison-page-label"].textContent = `第 ${number} / ${pages} 页 · 共 ${page.total} 条`;
      elements["comparison-prev"].disabled = page.offset <= 0;
      elements["comparison-next"].disabled = page.offset + page.limit >= page.total;
    }

    async function loadComparison() {
      const requestId = ++state.requestIds.comparison;
      try {
        const page = await api.get(comparisonUrl());
        if (requestId !== state.requestIds.comparison) return false;
        state.comparisonPage = page; renderComparisons(page); return true;
      } catch (error) {
        if (error?.name === "AbortError" || requestId !== state.requestIds.comparison) return false;
        emptyRow(elements["comparison-rows"], 8, `变化数据加载失败（${error.code || "request_failed"}）。`, "empty-state error-state");
        return false;
      }
    }

    function runUrl() {
      return appendQuery("/api/first-limit/runs", {
        trade_date: state.tradeDate, limit: 20, offset: state.runOffset,
      });
    }

    function renderRuns(page) {
      const target = elements["run-rows"];
      if (!page.total) emptyRow(target, 10, "该交易日没有运行记录。");
      else {
        clear(target);
        page.items.forEach(run => {
          const row = doc.createElement("tr");
          cell(row, run.run_id); cell(row, run.trade_date);
          cell(row, STAGE_NAMES[run.stage] || run.stage);
          const status = doc.createElement("td"); status.appendChild(statusChip(run.status)); row.appendChild(status);
          cell(row, String(run.success_count)); cell(row, String(run.failed_count));
          cell(row, String(run.pending_count)); cell(row, formatDateTime(run.started_at));
          cell(row, formatDateTime(run.finished_at));
          const action = doc.createElement("td");
          const button = text(action, "button", "查看明细", "detail-button");
          button.type = "button"; button.addEventListener("click", () => openRun(run.run_id));
          row.appendChild(action); target.appendChild(row);
        });
      }
      const number = Math.floor(page.offset / page.limit) + 1;
      const pages = Math.max(1, Math.ceil(page.total / page.limit));
      elements["run-page-label"].textContent = `第 ${number} / ${pages} 页 · 共 ${page.total} 条`;
      elements["run-prev"].disabled = page.offset <= 0;
      elements["run-next"].disabled = page.offset + page.limit >= page.total;
    }

    async function loadRuns() {
      const requestId = ++state.requestIds.runs;
      try {
        const page = await api.get(runUrl());
        if (requestId !== state.requestIds.runs) return false;
        state.runPage = page; renderRuns(page); return true;
      } catch (error) {
        if (requestId !== state.requestIds.runs) return false;
        emptyRow(elements["run-rows"], 10, `运行记录加载失败（${error.code || "request_failed"}）。`, "empty-state error-state");
        return false;
      }
    }

    function renderRunDetail(detail) {
      elements["run-detail-panel"].hidden = false;
      elements["selected-run-label"].textContent = detail.run.run_id;
      const failures = detail.failures?.length || 0;
      elements["run-detail-summary"].className = `state-note ${detail.run.status === "failed" ? "error" : detail.run.status === "partial" || detail.run.status === "running" ? "warning" : ""}`;
      elements["run-detail-summary"].textContent =
        `${RUN_STATUS_NAMES[detail.run.status]} · 计划 ${detail.run.requested_count} · 成功 ${detail.run.success_count} · 失败 ${detail.run.failed_count} · 失败摘要 ${failures} 条`;
    }

    function renderItems(page) {
      const target = elements["item-rows"];
      if (!page.total) emptyRow(target, 6, "当前筛选没有运行项。");
      else {
        clear(target);
        page.items.forEach(item => {
          const row = doc.createElement("tr");
          cell(row, item.symbol); cell(row, String(item.first_limit_event_id));
          cell(row, ITEM_STATUS_NAMES[item.status] || item.status);
          cell(row, item.candidate_id == null ? "—" : String(item.candidate_id));
          cell(row, item.error_code || "—"); cell(row, item.error_message || "—", "reason-cell");
          target.appendChild(row);
        });
      }
      const number = Math.floor(page.offset / page.limit) + 1;
      const pages = Math.max(1, Math.ceil(page.total / page.limit));
      elements["item-page-label"].textContent = `第 ${number} / ${pages} 页 · 共 ${page.total} 条`;
      elements["item-prev"].disabled = page.offset <= 0;
      elements["item-next"].disabled = page.offset + page.limit >= page.total;
    }

    async function loadItems(runId) {
      const requestId = ++state.requestIds.items;
      const url = appendQuery(`/api/first-limit/runs/${encodeURIComponent(runId)}/items`, {
        status: elements["item-status-filter"].value, limit: 50, offset: state.itemOffset,
      });
      try {
        const page = await api.get(url);
        if (requestId !== state.requestIds.items || runId !== state.selectedRunId) return;
        state.runItems = page; renderItems(page);
      } catch (error) {
        if (requestId !== state.requestIds.items) return;
        emptyRow(elements["item-rows"], 6, `运行项加载失败（${error.code || "request_failed"}）。`, "empty-state error-state");
      }
    }

    async function openRun(runId) {
      state.selectedRunId = runId;
      state.itemOffset = 0;
      elements["run-detail-panel"].hidden = false;
      elements["selected-run-label"].textContent = runId;
      elements["run-detail-summary"].textContent = "正在加载正式运行明细…";
      try {
        const detail = await api.get(`/api/first-limit/runs/${encodeURIComponent(runId)}`);
        if (state.selectedRunId !== runId) return;
        renderRunDetail(detail);
        await loadItems(runId);
      } catch (error) {
        if (state.selectedRunId !== runId) return;
        elements["run-detail-summary"].className = "state-note error";
        elements["run-detail-summary"].textContent = `运行详情加载失败（${error.code || "request_failed"}）。`;
      }
    }

    function metaItem(parent, label, value) {
      const wrapper = doc.createElement("div");
      text(wrapper, "span", label); text(wrapper, "strong", formatValue(value));
      parent.appendChild(wrapper);
    }

    function renderCandidateDetail(detail) {
      const target = elements["candidate-detail-content"];
      clear(target);
      const candidate = detail.candidate;
      elements["candidate-modal-title"].textContent = `${candidate.symbol} · 候选详情`;
      const meta = doc.createElement("div"); meta.className = "candidate-meta";
      [
        ["Candidate ID", candidate.candidate_id], ["Event ID", candidate.first_limit_event_id],
        ["候选总分", candidate.base_score],
        ["交易日", candidate.trade_date], ["阶段", STAGE_NAMES[candidate.stage]],
        ["等级", candidate.grade], ["生命周期", LIFECYCLE_NAMES[candidate.lifecycle]],
        ["观察日", candidate.observation_day == null ? null : `D${candidate.observation_day}`],
        ["首板日期", candidate.first_limit_date], ["主要原因", candidate.reason_code],
        ["尾盘快照", candidate.preview_candidate_id], ["strategy", detail.run.strategy_version],
        ["detection", detail.run.detection_version], ["pullback", detail.run.pullback_version],
        ["context", detail.run.context_version], ["评价时间", formatDateTime(candidate.as_of)],
        ["变化类型", CHANGE_NAMES[candidate.change_type] || candidate.change_type],
      ].forEach(item => metaItem(meta, item[0], item[1]));
      target.appendChild(meta);
      text(target, "h3", "完整规则证据");
      const wrap = doc.createElement("div"); wrap.className = "table-region first-limit-table-wrap";
      const table = doc.createElement("table"); table.className = "first-limit-table evidence-table";
      const head = doc.createElement("thead"); const header = doc.createElement("tr");
      ["规则", "结果", "实际值", "阈值", "单位", "原因", "来源时间"].forEach(label => text(header, "th", label));
      head.appendChild(header); table.appendChild(head);
      const body = doc.createElement("tbody");
      if (!detail.evidence.length) emptyRow(body, 7, "该候选存在，但没有保存 evidence。");
      detail.evidence.forEach(item => {
        const row = doc.createElement("tr");
        cell(row, item.rule_code);
        cell(row, EVIDENCE_NAMES[item.result] || item.result, `evidence-result ${item.result}`);
        cell(row, formatValue(item.actual_value)); cell(row, formatValue(item.threshold_value));
        cell(row, item.unit || "—");
        cell(row, item.display_text || item.reason_code || "—", "reason-cell");
        cell(row, item.source_time || item.source_date || "—");
        body.appendChild(row);
      });
      table.appendChild(body); wrap.appendChild(table); target.appendChild(wrap);
    }

    async function openCandidate(candidateId) {
      const requestId = ++state.requestIds.detail;
      state.selectedCandidate = candidateId;
      elements["candidate-modal"].hidden = false;
      elements["candidate-detail-content"].textContent = "正在加载候选详情…";
      showError(elements["candidate-detail-error"], null);
      elements["close-candidate-modal"].focus?.();
      try {
        const detail = await api.get(`/api/first-limit/candidates/${encodeURIComponent(candidateId)}`);
        if (requestId !== state.requestIds.detail) return;
        renderCandidateDetail(detail);
      } catch (error) {
        if (requestId !== state.requestIds.detail) return;
        showError(elements["candidate-detail-error"], error);
        elements["candidate-detail-content"].textContent = "详情加载失败；主候选列表不受影响，可关闭后重试。";
      }
    }

    function closeCandidate() {
      elements["candidate-modal"].hidden = true;
      state.selectedCandidate = null;
    }

    function setRunning(value, stage) {
      state.isRunning = value;
      elements["run-preview"].disabled = value;
      elements["run-close"].disabled = value;
      if (value) {
        const active = stage === "tail_preview" ? elements["run-preview"] : elements["run-close"];
        active.textContent = "运行中…";
        elements["run-message"].textContent = "后台任务已启动，可刷新或离开页面；已有结果会继续保留。";
      } else {
        elements["run-preview"].textContent = "生成尾盘预警";
        elements["run-close"].textContent = "执行收盘确认";
      }
    }

    const PIPELINE_STEP_NAMES = {
      calendar: "检查交易日历", universe: "确定全市场范围",
      security_master: "补齐证券主数据", daily_status: "同步每日状态",
      daily_bars: "同步日线与涨跌停", limit_detection: "检测首板",
      quality_scoring: "计算首板质量", pullback_observation: "更新回调观察",
      market_context: "更新行业及市场上下文", minute_bars: "补齐候选分钟线",
      candidate_generation: "生成候选", coverage_validation: "验证数据覆盖",
    };
    const PIPELINE_TERMINAL = new Set(["success", "partial", "failed", "cancelled"]);

    function renderPipeline(job, steps = []) {
      if (!elements["pipeline-progress"]) return;
      elements["pipeline-progress"].hidden = false;
      const label = RUN_STATUS_NAMES[job.status] || {
        pending: "等待执行", interrupted: "等待续跑", cancelled: "已取消",
      }[job.status] || job.status;
      elements["pipeline-status"].textContent = `一键任务 #${job.id || job.job_id} · ${label}`;
      const percent = job.progress_percent;
      elements["pipeline-percent"].textContent =
        percent == null ? "进度估算中" : `${Math.round(percent)}%`;
      if (percent != null) elements["pipeline-progress-bar"].value = percent;
      elements["pipeline-current-step"].textContent =
        job.current_step ? (PIPELINE_STEP_NAMES[job.current_step] || job.current_step)
          : (job.message || "等待执行");
      clear(elements["pipeline-step-list"]);
      steps.forEach(step => {
        const node = text(
          elements["pipeline-step-list"], "span",
          `${PIPELINE_STEP_NAMES[step.step_code] || step.step_code}：${RUN_STATUS_NAMES[step.status] || step.status}`,
          step.status,
        );
        node.title = step.error_message || "";
      });
      elements["pipeline-coverage-note"].textContent =
        job.status === "success" && job.coverage_complete ? "全市场必需数据覆盖完整。"
          : job.status === "partial" ? "已有结果可查看，但覆盖不完整；0 条结果不代表全市场无候选。"
          : job.status === "failed" ? `任务失败：${job.error_message || job.error_code || "请查看失败明细"}`
          : "任务尚未完成，当前结果不代表完整筛选。";
      elements["pipeline-retry"].hidden =
        !["failed", "partial", "interrupted"].includes(job.status);
      elements["pipeline-cancel"].hidden = !["pending", "running"].includes(job.status);
    }

    async function pollPipeline(jobId, generation = ++state.pollGeneration) {
      try {
        const [job, stepPage] = await Promise.all([
          api.get(`/api/first-limit/pipeline-jobs/${jobId}`),
          api.get(`/api/first-limit/pipeline-jobs/${jobId}/steps`),
        ]);
        if (generation !== state.pollGeneration) return false;
        showError(elements["page-error"], null);
        renderPipeline(job, stepPage.items || []);
        state.pipelineJobId = jobId;
        persist("pipeline_job_id", String(jobId));
        const terminal = PIPELINE_TERMINAL.has(job.status);
        setRunning(!terminal, job.stage);
        if (terminal) {
          await loadAll();
          elements["run-message"].textContent =
            job.status === "success" && job.coverage_complete
              ? "完整筛选完成，结果已自动刷新。"
              : job.status === "partial"
                ? "部分结果已刷新，请先查看覆盖警告。"
                : "后台任务失败，旧结果已保留。";
          return true;
        }
        const schedule = options.setTimeout || root?.setTimeout;
        if (schedule) schedule(() => pollPipeline(jobId, generation), 1500);
        return true;
      } catch (error) {
        if (generation !== state.pollGeneration) return false;
        showError(elements["page-error"], error);
        setRunning(false);
        return false;
      }
    }

    async function runStage(stage) {
      if (state.isRunning) return false;
      showError(elements["page-error"], null);
      setRunning(true, stage);
      try {
        const result = await api.post("/api/first-limit/pipeline-jobs", {
          trade_date: state.tradeDate, stage,
        });
        state.stage = stage; elements.stage.value = stage;
        persist("stage", stage);
        state.pipelineJobId = result.job_id;
        persist("pipeline_job_id", String(result.job_id));
        await pollPipeline(result.job_id);
        elements["run-message"].textContent = result.reused
          ? `已复用正在运行或已完成的一键任务：#${result.job_id}`
          : `一键任务已创建：#${result.job_id}`;
        return true;
      } catch (error) {
        showError(elements["page-error"], error);
        elements["run-message"].textContent = "运行请求失败，旧结果已保留。";
        setRunning(false, stage);
        return false;
      }
    }

    async function loadAll() {
      return Promise.allSettled([loadCandidates(), loadComparison(), loadRuns()]);
    }

    function selectTab(name) {
      const selected = ["candidates", "comparison", "runs"].includes(name) ? name : "candidates";
      state.activeTab = selected; persist("selected_tab", selected);
      ["candidates", "comparison", "runs"].forEach(value => {
        elements[`tab-${value}`].setAttribute("aria-selected", String(value === selected));
        elements[`panel-${value}`].hidden = value !== selected;
      });
    }

    function resetCandidatePageAndLoad() {
      state.candidateOffset = 0;
      return loadCandidates();
    }

    function bind() {
      elements["trade-date"].value = state.tradeDate;
      elements.stage.value = state.stage;
      elements["grade-filter"].value = state.grade;
      elements["lifecycle-filter"].value = state.lifecycle;
      elements["candidate-sort"].value = state.sort;
      elements["candidate-order"].value = state.order;
      elements["candidate-page-size"].value = String(state.pageSize);
      elements["trade-date"].addEventListener("change", () => {
        state.tradeDate = elements["trade-date"].value; persist("trade_date", state.tradeDate);
        state.candidateOffset = state.comparisonOffset = state.runOffset = 0; loadAll();
      });
      elements.stage.addEventListener("change", () => {
        state.stage = validStage(elements.stage.value); persist("stage", state.stage);
        state.candidateOffset = 0; loadCandidates();
      });
      elements["symbol-filter"].addEventListener("change", () => {
        state.candidateOffset = state.comparisonOffset = 0;
        Promise.allSettled([loadCandidates(), loadComparison()]);
      });
      elements["grade-filter"].addEventListener("change", () => {
        state.grade = elements["grade-filter"].value; persist("grade_filter", state.grade);
        resetCandidatePageAndLoad();
      });
      elements["lifecycle-filter"].addEventListener("change", () => {
        state.lifecycle = elements["lifecycle-filter"].value; persist("lifecycle_filter", state.lifecycle);
        resetCandidatePageAndLoad();
      });
      elements["candidate-sort"].addEventListener("change", () => {
        state.sort = elements["candidate-sort"].value; persist("sort", state.sort);
        resetCandidatePageAndLoad();
      });
      elements["candidate-order"].addEventListener("change", () => {
        state.order = elements["candidate-order"].value; persist("order", state.order);
        resetCandidatePageAndLoad();
      });
      elements["candidate-page-size"].addEventListener("change", () => {
        state.pageSize = Number(elements["candidate-page-size"].value); persist("page_size", String(state.pageSize));
        state.candidateOffset = state.comparisonOffset = 0; loadAll();
      });
      elements["change-filter"].addEventListener("change", () => { state.comparisonOffset = 0; loadComparison(); });
      elements["comparison-grade"].addEventListener("change", () => { state.comparisonOffset = 0; loadComparison(); });
      elements["refresh-results"].addEventListener("click", loadAll);
      elements["run-preview"].addEventListener("click", () => runStage("tail_preview"));
      elements["run-close"].addEventListener("click", () => runStage("close_confirmed"));
      elements["pipeline-retry"]?.addEventListener("click", async () => {
        if (!state.pipelineJobId || state.isRunning) return;
        showError(elements["page-error"], null);
        setRunning(true, state.stage);
        try {
          await api.post(`/api/first-limit/pipeline-jobs/${state.pipelineJobId}/retry`, {});
          await pollPipeline(state.pipelineJobId);
        } catch (error) {
          showError(elements["page-error"], error);
          setRunning(false, state.stage);
        }
      });
      elements["pipeline-cancel"]?.addEventListener("click", async () => {
        if (!state.pipelineJobId || !confirm("停止当前一键任务？已完成的数据会保留。")) return;
        try {
          await api.del(`/api/first-limit/pipeline-jobs/${state.pipelineJobId}`);
          await pollPipeline(state.pipelineJobId);
        } catch (error) { showError(elements["page-error"], error); }
      });
      elements["candidate-prev"].addEventListener("click", () => {
        state.candidateOffset = Math.max(0, state.candidateOffset - state.pageSize); loadCandidates();
      });
      elements["candidate-next"].addEventListener("click", () => {
        state.candidateOffset += state.pageSize; loadCandidates();
      });
      elements["comparison-prev"].addEventListener("click", () => {
        state.comparisonOffset = Math.max(0, state.comparisonOffset - state.pageSize); loadComparison();
      });
      elements["comparison-next"].addEventListener("click", () => {
        state.comparisonOffset += state.pageSize; loadComparison();
      });
      elements["run-prev"].addEventListener("click", () => {
        state.runOffset = Math.max(0, state.runOffset - 20); loadRuns();
      });
      elements["run-next"].addEventListener("click", () => { state.runOffset += 20; loadRuns(); });
      elements["item-status-filter"].addEventListener("change", () => {
        state.itemOffset = 0;
        if (state.selectedRunId) loadItems(state.selectedRunId);
      });
      elements["item-prev"].addEventListener("click", () => {
        state.itemOffset = Math.max(0, state.itemOffset - 50);
        if (state.selectedRunId) loadItems(state.selectedRunId);
      });
      elements["item-next"].addEventListener("click", () => {
        state.itemOffset += 50;
        if (state.selectedRunId) loadItems(state.selectedRunId);
      });
      elements["toggle-versions"].addEventListener("click", () => {
        const show = elements["version-details"].hidden;
        elements["version-details"].hidden = !show;
        elements["toggle-versions"].setAttribute("aria-expanded", String(show));
        elements["toggle-versions"].textContent = show ? "隐藏版本参数" : "显示版本参数";
      });
      ["candidates", "comparison", "runs"].forEach(name => {
        elements[`tab-${name}`].addEventListener("click", () => selectTab(name));
      });
      elements["close-candidate-modal"].addEventListener("click", closeCandidate);
      elements["candidate-modal"].addEventListener("click", event => {
        if (event.target === elements["candidate-modal"]) closeCandidate();
      });
      doc.addEventListener?.("keydown", event => {
        if (event.key === "Escape" && !elements["candidate-modal"].hidden) closeCandidate();
      });
    }

    async function init() {
      bind(); selectTab(state.activeTab);
      elements["run-message"].textContent = "正在从 API 恢复最近可用结果；不会自动运行。";
      await loadAll();
      if (state.pipelineJobId) await pollPipeline(state.pipelineJobId);
      else {
        try {
          const latest = await api.get(appendQuery("/api/first-limit/pipeline-jobs/latest", {
            trade_date: state.tradeDate,
          }));
          await pollPipeline(latest.id);
        } catch (error) {
          if (!(error instanceof RequestError && error.status === 404)) {
            showError(elements["page-error"], error);
          }
        }
      }
      elements["run-message"].textContent = "查询完成。";
    }

    return {
      state, init, loadAll, loadCandidates, loadComparison, loadRuns,
      openCandidate, openRun, runStage, pollPipeline, renderPipeline, selectTab, candidateUrl,
      comparisonUrl, runUrl, renderCandidateDetail, formatValue,
    };
  }

  const exported = {
    createPage, createApi, appendQuery, formatValue, formatDateTime, localDate,
    LIFECYCLE_NAMES, CHANGE_NAMES, EVIDENCE_NAMES,
  };
  if (root?.document && root?.fetch) {
    root.addEventListener("DOMContentLoaded", () => {
      createPage({
        document: root.document, fetch: root.fetch.bind(root),
        storage: root.localStorage,
      }).init();
    });
  }
  return exported;
});
