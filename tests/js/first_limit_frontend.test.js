"use strict";
const assert = require("assert");
const {
  createPage, appendQuery, formatValue, localDate, CHANGE_NAMES,
} = require("../../frontend/first-limit.js");

class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.textContent = ""; this.hidden = false;
    this.disabled = false; this.className = ""; this.listeners = {}; this.value = "";
    this.attributes = {}; this.dataset = {}; this.colSpan = 1; this.type = "";
    this.focused = false;
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  focus() { this.focused = true; }
  get firstChild() { return this.children[0] || null; }
}

const IDS = [
  "trade-date", "stage", "symbol-filter", "refresh-results", "run-preview", "run-close",
  "run-message", "page-error", "page-data-time", "page-updated-time", "latest-run-state",
  "overview-grid", "overview-note", "toggle-versions", "version-details", "grade-filter",
  "lifecycle-filter", "candidate-sort", "candidate-order", "candidate-page-size",
  "candidate-rows", "candidate-prev", "candidate-next", "candidate-page-label",
  "change-filter", "comparison-grade", "comparison-rows", "comparison-prev",
  "comparison-next", "comparison-page-label", "run-rows", "run-prev", "run-next",
  "run-page-label", "run-detail-panel", "selected-run-label", "run-detail-summary",
  "item-status-filter", "item-rows", "item-prev", "item-next", "item-page-label",
  "candidate-modal", "candidate-modal-title",
  "close-candidate-modal", "candidate-detail-error", "candidate-detail-content",
  "tab-candidates", "tab-comparison", "tab-runs", "panel-candidates",
  "panel-comparison", "panel-runs",
];

function documentMock() {
  const elements = {};
  IDS.forEach(id => { elements[id] = new Element(); });
  elements["candidate-page-size"].value = "50";
  const listeners = {};
  return {
    elements, listeners,
    getElementById: id => elements[id],
    createElement: tag => new Element(tag),
    addEventListener: (name, handler) => { listeners[name] = handler; },
  };
}

function storageMock(values = {}) {
  const data = {...values};
  return {
    data,
    getItem: key => Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null,
    setItem: (key, value) => { data[key] = String(value); },
  };
}

function response(data, ok = true, status = 200) {
  return {ok, status, json: async () => data};
}

function candidate(id = 1, grade = "S", lifecycle = "confirmed") {
  return {
    candidate_id: id, run_id: "run-1", first_limit_event_id: 100 + id,
    symbol: `00000${id}.SZ`, trade_date: "2026-07-30", stage: "close_confirmed",
    as_of: "2026-07-30T15:00:00+08:00", observation_day: 2,
    lifecycle, grade, base_grade: null, base_score: grade ? 80 : 0,
    change_type: id === 1 ? "upgraded" : null, reason_code: "FORMAL_REASON",
    display_text: id === 2 ? "<img src=x onerror=alert(1)>" : "正式证据结论",
    first_limit_date: "2026-07-20", preview_candidate_id: 50,
    created_at: "2026-07-30T07:00:00+00:00",
    updated_at: "2026-07-30T07:00:00+00:00",
  };
}

function candidatePage(items = [candidate()], overrides = {}) {
  return {
    items, total: items.length, limit: 50, offset: 0, filters: {},
    data_date: "2026-07-30", stage: "close_confirmed",
    run_id: "run-1", run_status: "success", ...overrides,
  };
}

function runSummary(status = "success", id = "run-1") {
  return {
    run_id: id, trade_date: "2026-07-30", stage: "close_confirmed",
    as_of: "2026-07-30T15:00:00+08:00",
    data_cutoff: "2026-07-30T15:00:00+08:00", status,
    parameter_hash: "abc", strategy_version: "candidate-v1",
    detection_version: "detect-v1", pullback_version: "pullback-v1",
    context_version: "context-v1", requested_count: 3, success_count: 2,
    pending_count: status === "running" ? 1 : 0, failed_count: status === "partial" ? 1 : 0,
    confirmed_count: 2, eliminated_count: 0, indeterminate_count: 0,
    created_at: "2026-07-30T06:59:00+00:00",
    started_at: "2026-07-30T07:00:00+00:00",
    finished_at: status === "running" ? null : "2026-07-30T07:00:02+00:00",
    error_message: status === "failed" ? "run_failed: candidate evaluation failed" : null,
  };
}

function runDetail(status = "success", id = "run-1") {
  return {
    run: runSummary(status, id),
    item_status_counts: {success: 2, failed: status === "partial" ? 1 : 0},
    grade_counts: {S: 1, A: 1, unknown: 1},
    lifecycle_counts: {confirmed: 2, indeterminate: 1},
    failures: status === "partial" ? [{
      first_limit_event_id: 4, symbol: "000004.SZ", error_code: "RuntimeError",
      error_message: "RuntimeError: candidate evaluation failed",
    }] : [],
    terminal: status !== "running",
  };
}

function comparisonPage() {
  return {
    items: Object.keys(CHANGE_NAMES).map((change, index) => ({
      first_limit_event_id: index + 1, symbol: `00000${index + 1}.SZ`,
      preview_candidate_id: change === "preview_missing" ? null : 10 + index,
      close_candidate_id: 20 + index,
      preview_lifecycle: change === "preview_missing" ? null : "eligible",
      close_lifecycle: change === "eliminated" ? "eliminated" : "confirmed",
      preview_grade: change === "preview_missing" ? null : "A",
      close_grade: change === "eliminated" ? null : "S",
      change_type: change, change_reason_code: "CHANGE", change_display_text: `变化${index}`,
    })),
    total: 6, limit: 50, offset: 0, trade_date: "2026-07-30", run_id: "run-1",
  };
}

function runsPage(status = "success") {
  return {items: [runSummary(status)], total: 1, limit: 20, offset: 0, filters: {}};
}

function detailPayload() {
  return {
    candidate: candidate(),
    evidence: [
      {rule_code: "PASS", result: "pass", actual_value: 0, threshold_value: 1, unit: "ratio", source_date: "2026-07-30", source_time: null, reason_code: "OK", display_text: "通过", ordinal: 0},
      {rule_code: "FAIL", result: "fail", actual_value: false, threshold_value: true, unit: null, source_date: null, source_time: "2026-07-30T14:55:00+08:00", reason_code: "NO", display_text: "<script>alert(1)</script>", ordinal: 1},
      {rule_code: "UNKNOWN", result: "unknown", actual_value: null, threshold_value: "", unit: null, source_date: null, source_time: null, reason_code: "MISSING", display_text: null, ordinal: 2},
    ],
    run: runSummary(),
  };
}

function defaultFetch(calls, overrides = {}) {
  return async (url, init = {}) => {
    calls.push([url, init]);
    if (overrides.handler) {
      const custom = await overrides.handler(url, init);
      if (custom) return custom;
    }
    if (url.startsWith("/api/first-limit/candidates/")) return response(detailPayload());
    if (url.startsWith("/api/first-limit/candidates")) return response(candidatePage());
    if (url.includes("/preview-comparison")) return response(comparisonPage());
    if (url.endsWith("/items") || url.includes("/items?")) return response({
      items: [{item_id: 4, run_id: "run-1", first_limit_event_id: 4,
        symbol: "000004.SZ", status: "failed", candidate_id: null,
        error_code: "RuntimeError", error_message: "candidate evaluation failed",
        created_at: "2026-07-30T07:00:00Z", updated_at: "2026-07-30T07:00:01Z"}],
      total: 1, limit: 100, offset: 0, run_id: "run-1",
    });
    if (url === "/api/first-limit/runs/run-1") return response(runDetail());
    if (url.startsWith("/api/first-limit/runs")) return response(runsPage());
    throw new Error(`unexpected URL ${url}`);
  };
}

async function testInitialRestoreAndNoAutomaticPost() {
  const doc = documentMock(), calls = [];
  const storage = storageMock({
    "aurora.firstLimit.trade_date": "2026-07-30",
    "aurora.firstLimit.stage": "close_confirmed",
    "aurora.firstLimit.grade_filter": "A",
    "aurora.firstLimit.lifecycle_filter": "confirmed",
    "aurora.firstLimit.sort": "symbol",
    "aurora.firstLimit.page_size": "20",
    "aurora.firstLimit.selected_tab": "comparison",
  });
  const page = createPage({
    document: doc, storage, fetch: defaultFetch(calls),
    now: () => new Date("2026-08-01T00:00:00Z"),
  });
  await page.init();
  assert.strictEqual(page.state.tradeDate, "2026-07-30");
  assert.strictEqual(page.state.stage, "close_confirmed");
  assert.strictEqual(doc.elements["panel-comparison"].hidden, false);
  assert(!calls.some(([, init]) => init.method === "POST"));
  const candidateCall = calls.find(([url]) => url.startsWith("/api/first-limit/candidates?"))[0];
  assert(candidateCall.includes("trade_date=2026-07-30"));
  assert(candidateCall.includes("grade=A"));
  assert(candidateCall.includes("lifecycle=confirmed"));
  assert(candidateCall.includes("sort=symbol"));
  assert(candidateCall.includes("limit=20"));
  assert.strictEqual(doc.elements["candidate-rows"].children.length, 1);
}

async function testServerFiltersRenderingAndInjectionSafety() {
  const doc = documentMock(), calls = [];
  const page = createPage({document: doc, storage: storageMock(), fetch: defaultFetch(calls)});
  doc.elements["grade-filter"].value = "none";
  doc.elements["lifecycle-filter"].value = "";
  page.state.grade = "none"; page.state.lifecycle = "";
  assert(page.candidateUrl().includes("grade=none"));
  const mixed = candidatePage([
    candidate(1, "S", "confirmed"), candidate(2, null, "indeterminate"),
    candidate(3, "A", "pending_close_confirmation"), candidate(4, "B", "eligible"),
  ]);
  page.state.requestIds.candidates += 1;
  const request = page.state.requestIds.candidates;
  // Render through a dedicated request to retain the same public behavior.
  const isolated = createPage({
    document: doc, storage: storageMock(),
    fetch: async url => url.startsWith("/api/first-limit/candidates")
      ? response(mixed) : url === "/api/first-limit/runs/run-1"
        ? response(runDetail("partial")) : defaultFetch([])(url),
  });
  await isolated.loadCandidates();
  const rows = doc.elements["candidate-rows"].children;
  assert.strictEqual(rows.length, 4);
  assert.strictEqual(rows[0].children[0].children[0].textContent, "S");
  assert.strictEqual(rows[1].children[0].children[0].textContent, "—");
  assert.strictEqual(rows[1].children[3].children[0].textContent, "无法确定");
  assert.strictEqual(rows[2].children[3].children[0].textContent, "等待收盘确认");
  assert.strictEqual(rows[3].children[0].children[0].textContent, "B");
  assert.strictEqual(rows[3].children[3].children[0].textContent, "尾盘合格");
  assert.strictEqual(rows[1].children[6].textContent, "<img src=x onerror=alert(1)>");
  assert.strictEqual(rows[1].children[6].children.length, 0);
  assert.strictEqual(doc.elements["overview-note"].className, "state-note warning");
  assert.strictEqual(request > 0, true);
}

async function testLateCandidateResponseCannotOverwriteNewerQuery() {
  const doc = documentMock();
  let resolveFirst, resolveSecond;
  const promises = [
    new Promise(resolve => { resolveFirst = resolve; }),
    new Promise(resolve => { resolveSecond = resolve; }),
  ];
  let count = 0;
  const page = createPage({
    document: doc, storage: storageMock(),
    fetch: async url => {
      if (url.startsWith("/api/first-limit/candidates")) return promises[count++];
      if (url === "/api/first-limit/runs/run-1") return response(runDetail());
      return response({items: [], total: 0, limit: 20, offset: 0, filters: {}});
    },
  });
  const first = page.loadCandidates();
  const second = page.loadCandidates();
  resolveSecond(response(candidatePage([candidate(2, "A")])));
  await second;
  resolveFirst(response(candidatePage([candidate(1, "S")])));
  await first;
  assert.strictEqual(page.state.candidatePage.items[0].candidate_id, 2);
  assert.strictEqual(doc.elements["candidate-rows"].children[0].children[0].children[0].textContent, "A");
}

async function testCandidateDetailEvidenceAndKeyboardClose() {
  const doc = documentMock(), calls = [];
  const page = createPage({document: doc, storage: storageMock(), fetch: defaultFetch(calls)});
  await page.init();
  await page.openCandidate(1);
  assert.strictEqual(doc.elements["candidate-modal"].hidden, false);
  const content = doc.elements["candidate-detail-content"];
  const tableWrap = content.children[2];
  const body = tableWrap.children[0].children[1];
  assert.strictEqual(body.children[0].children[1].textContent, "通过");
  assert.strictEqual(body.children[0].children[2].textContent, "0");
  assert.strictEqual(body.children[1].children[1].textContent, "未通过");
  assert.strictEqual(body.children[1].children[2].textContent, "false");
  assert.strictEqual(body.children[2].children[1].textContent, "数据不足/无法确定");
  assert.strictEqual(body.children[2].children[2].textContent, "—");
  assert.strictEqual(body.children[2].children[3].textContent, '""');
  assert.strictEqual(body.children[1].children[5].textContent, "<script>alert(1)</script>");
  assert.strictEqual(body.children[1].children[5].children.length, 0);
  doc.listeners.keydown({key: "Escape"});
  assert.strictEqual(doc.elements["candidate-modal"].hidden, true);
}

async function testRunButtonsBodiesLongRequestAndReuse() {
  const doc = documentMock(), calls = [];
  let resolvePost;
  const postPromise = new Promise(resolve => { resolvePost = resolve; });
  const fetch = defaultFetch(calls, {
    handler: async (url, init) => {
      if (url === "/api/first-limit/runs" && init.method === "POST") return postPromise;
      return null;
    },
  });
  const page = createPage({document: doc, storage: storageMock(), fetch});
  page.state.tradeDate = "2026-07-30";
  doc.elements["candidate-rows"].appendChild(new Element("existing-row"));
  const running = page.runStage("tail_preview");
  assert.strictEqual(doc.elements["run-preview"].disabled, true);
  assert.strictEqual(doc.elements["run-close"].disabled, true);
  assert.strictEqual(doc.elements["candidate-rows"].children.length, 1);
  assert.strictEqual(await page.runStage("close_confirmed"), false);
  const post = calls.find(([url, init]) => url === "/api/first-limit/runs" && init.method === "POST");
  assert.deepStrictEqual(JSON.parse(post[1].body), {
    trade_date: "2026-07-30", stage: "tail_preview",
  });
  assert(!post[1].body.includes("force"));
  assert(!post[1].body.includes("resume"));
  assert(!post[1].body.includes("dry_run"));
  assert(!("signal" in post[1]));
  resolvePost(response({run_id: "run-1", status: "success", reused: true, poll_url: "/api/first-limit/runs/run-1"}));
  assert.strictEqual(await running, true);
  assert.strictEqual(doc.elements["run-preview"].disabled, false);
  assert(doc.elements["run-message"].textContent.includes("已复用"));
  assert(calls.filter(([url, init]) => url === "/api/first-limit/runs" && init.method === "POST").length === 1);
}

async function testPostFailureRestoresButtonsAndKeepsContractError() {
  const doc = documentMock();
  const page = createPage({
    document: doc, storage: storageMock(),
    fetch: async (url, init = {}) => {
      if (init.method === "POST") return response({
        error: {code: "first_limit_non_trading_day", message: "not open", details: {}},
      }, false, 422);
      return defaultFetch([])(url, init);
    },
  });
  page.state.tradeDate = "2026-07-31";
  assert.strictEqual(await page.runStage("close_confirmed"), false);
  assert.strictEqual(doc.elements["run-close"].disabled, false);
  assert(doc.elements["page-error"].textContent.includes("first_limit_non_trading_day"));
  assert(doc.elements["run-message"].textContent.includes("旧结果已保留"));
}

async function testComparisonRunAndItemViews() {
  const doc = documentMock(), calls = [];
  const page = createPage({document: doc, storage: storageMock(), fetch: defaultFetch(calls)});
  await page.loadComparison();
  const changes = doc.elements["comparison-rows"].children.map(row => row.children[5].textContent);
  assert.deepStrictEqual(changes, [
    "不变", "升级", "降级", "收盘新增", "收盘淘汰", "缺少尾盘快照",
  ]);
  await page.loadRuns();
  assert.strictEqual(doc.elements["run-rows"].children[0].children[3].children[0].textContent, "成功");
  await page.openRun("run-1");
  assert.strictEqual(doc.elements["run-detail-panel"].hidden, false);
  assert.strictEqual(doc.elements["item-rows"].children[0].children[0].textContent, "000004.SZ");
  assert.strictEqual(doc.elements["item-rows"].children[0].children[4].textContent, "RuntimeError");
  assert(!doc.elements["item-rows"].children[0].textContent.includes("Traceback"));

  const emptyDoc = documentMock();
  const emptyPage = createPage({
    document: emptyDoc, storage: storageMock(),
    fetch: async () => response({items: [], total: 0, limit: 50, offset: 0, trade_date: "2026-07-30", run_id: null}),
  });
  await emptyPage.loadComparison();
  assert(emptyDoc.elements["comparison-rows"].children[0].children[0].textContent.includes("没有收盘确认 run"));
}

async function testRunningFailedAndNoRunStates() {
  for (const [status, phrase, className] of [
    ["running", "running", "state-note warning"],
    ["failed", "运行失败", "state-note error"],
  ]) {
    const doc = documentMock();
    const page = createPage({
      document: doc, storage: storageMock(),
      fetch: async url => url.startsWith("/api/first-limit/candidates")
        ? response(candidatePage())
        : response(runDetail(status)),
    });
    await page.loadCandidates();
    assert.strictEqual(doc.elements["overview-note"].className, className);
    assert(doc.elements["overview-note"].textContent.includes(phrase));
  }
  const doc = documentMock();
  const page = createPage({
    document: doc, storage: storageMock(),
    fetch: async () => response(candidatePage([], {
      total: 0, run_id: null, run_status: null,
    })),
  });
  await page.loadCandidates();
  assert(doc.elements["candidate-rows"].children[0].children[0].textContent.includes("尚未生成"));
}

function testPureHelpers() {
  assert.strictEqual(formatValue(0), "0");
  assert.strictEqual(formatValue(false), "false");
  assert.strictEqual(formatValue(null), "—");
  assert.strictEqual(formatValue(""), '""');
  assert.strictEqual(localDate(new Date(2026, 6, 30)), "2026-07-30");
  const url = appendQuery("/api/test", {grade: ["S", "A"], q: "<script>", offset: 0});
  assert(url.includes("grade=S") && url.includes("grade=A"));
  assert(url.includes("q=%3Cscript%3E"));
  assert(url.includes("offset=0"));
}

(async () => {
  testPureHelpers();
  await testInitialRestoreAndNoAutomaticPost();
  await testServerFiltersRenderingAndInjectionSafety();
  await testLateCandidateResponseCannotOverwriteNewerQuery();
  await testCandidateDetailEvidenceAndKeyboardClose();
  await testRunButtonsBodiesLongRequestAndReuse();
  await testPostFailureRestoresButtonsAndKeepsContractError();
  await testComparisonRunAndItemViews();
  await testRunningFailedAndNoRunStates();
  console.log("First-limit frontend mock tests passed");
})().catch(error => { console.error(error); process.exit(1); });
