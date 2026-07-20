"use strict";
const assert = require("assert");
const {createDashboard, getScoreClass, formatDuration} = require("../../frontend/market-pulse.js");

class Element {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.textContent = ""; this.hidden = false; this.disabled = false; this.className = ""; this.listeners = {}; this.value = 0; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0] || null; }
}
function documentMock() {
  const ids = ["sector-rows", "source-statuses", "refresh-sectors", "pulse-error", "sector-error", "health-error", "last-trade-date", "last-refresh", "market-status", "sector-count", "job-status", "job-counts", "job-step", "job-started", "job-finished", "job-duration", "job-progress", "job-error"];
  const elements = {}; ids.forEach(id => { elements[id] = new Element(); });
  return {elements, getElementById: id => elements[id], createElement: tag => new Element(tag)};
}
function response(data, ok = true, status = 200) { return {ok, status, json: async () => data}; }
function sectors(count = 31) { return {trade_date: "2026-07-20", latest_trade_date: "2026-07-20", source_status: {status: "healthy"}, items: Array.from({length: count}, (_, index) => ({source: "sw_l1", sector_name: `行业${index + 1}`, trade_date: "2026-07-20", trend_score: index ? 60 : 70, trend_max_score: 70, trend_level: index ? "B" : "A", updated_at: "2026-07-20T10:00:00Z"}))}; }
function health() { return {items: [{source: "sw_l1", display_name: "申万一级行业", status: "healthy"}, {source: "sw_l2", display_name: "申万二级行业", status: "unavailable", last_error_message: "HTTP 507"}, {source: "eastmoney", display_name: "东方财富行业", status: "unavailable", last_error_message: "RemoteDisconnected"}]}; }

async function testIndependentInitialLoad() {
  const doc = documentMock(), calls = [];
  const dashboard = createDashboard({document: doc, fetch: async url => { calls.push(url); return response(url.includes("data-source-health") ? health() : sectors()); }, setTimeout: () => 1, clearTimeout: () => {}});
  await dashboard.init();
  assert.deepStrictEqual(calls, ["/api/market-pulse/sectors", "/api/data-source-health"]);
  assert.strictEqual(doc.elements["sector-rows"].children.length, 31);
  assert.strictEqual(doc.elements["sector-rows"].children[0].children[2].children[0].textContent, "70/70");
  assert.strictEqual(doc.elements["sector-rows"].children[0].children[3].children[0].textContent, "A");
  assert.strictEqual(doc.elements["source-statuses"].children.length, 3);
  assert.strictEqual(getScoreClass(70), "score-70");

  const partialDoc = documentMock();
  const partial = createDashboard({document: partialDoc, fetch: async url => url.includes("data-source-health") ? response({detail: "down"}, false, 500) : response(sectors()), setTimeout: () => 1, clearTimeout: () => {}});
  await partial.load();
  assert.strictEqual(partialDoc.elements["sector-rows"].children.length, 31);
  assert.strictEqual(partialDoc.elements["health-error"].hidden, false);
}

async function runTerminal(status) {
  const doc = documentMock(), calls = []; let disabledDuringPost = false;
  const fetch = async (url, init = {}) => {
    calls.push([url, init.method || "GET", init.body]);
    if (url === "/api/market-pulse/refresh") { disabledDuringPost = doc.elements["refresh-sectors"].disabled; return response({job_id: 7, status: "queued"}); }
    if (url.endsWith("/7")) return response({job_id: 7, status, progress: 100, completed_count: 1, total_count: 1, started_at: "2026-07-20T10:00:00Z", finished_at: "2026-07-20T10:01:12Z", error_message: status === "completed" ? null : "upstream"});
    return response(url.includes("data-source-health") ? health() : sectors());
  };
  const dashboard = createDashboard({document: doc, fetch, setTimeout: () => 1, clearTimeout: () => {}, now: () => Date.parse("2026-07-20T10:01:12Z")});
  dashboard.renderSectorTable(sectors());
  await dashboard.startRefresh();
  assert(disabledDuringPost); assert.strictEqual(doc.elements["refresh-sectors"].disabled, false);
  assert(calls.some(call => call[0] === "/api/market-pulse/refresh" && JSON.parse(call[2]).source === "sw_l1"));
  assert(!calls.some(call => String(call[2]).includes('"all"')));
  assert(calls.some(call => call[0].endsWith("/7")));
  assert.strictEqual(doc.elements["job-status"].textContent, status === "completed" ? "已完成" : status === "partial" ? "部分完成" : "失败");
  assert.strictEqual(doc.elements["job-duration"].textContent, "1分12秒");
  if (status === "failed") { assert.strictEqual(doc.elements["sector-rows"].children.length, 31); assert(!calls.some(call => call[0] === "/api/market-pulse/sectors")); }
  else assert(calls.some(call => call[0] === "/api/market-pulse/sectors"));
}

async function testConflictAndEmpty() {
  const doc = documentMock(), calls = [];
  const fetch = async (url, init = {}) => {
    calls.push(url);
    if (init.method === "POST") return response({detail: {message: "已有任务", existing_job_id: 9}}, false, 409);
    if (url.endsWith("/9")) return response({job_id: 9, status: "completed", progress: 100, completed_count: 1, total_count: 1});
    return response(url.includes("health") ? health() : sectors());
  };
  const dashboard = createDashboard({document: doc, fetch, setTimeout: () => 1, clearTimeout: () => {}});
  await dashboard.startRefresh();
  assert(calls.includes("/api/market-pulse/refresh/9")); assert.strictEqual(doc.elements["refresh-sectors"].disabled, false);
  dashboard.renderSourceHealth(health()); dashboard.renderSectorTable({items: [], source_status: {status: "unavailable"}});
  assert.strictEqual(doc.elements["sector-rows"].children[0].children[0].textContent, "暂无行业数据，当前数据源不可用。");
}

(async () => {
  await testIndependentInitialLoad(); await runTerminal("completed"); await runTerminal("partial"); await runTerminal("failed"); await testConflictAndEmpty();
  assert.strictEqual(formatDuration("2026-07-20T10:00:00Z", "2026-07-20T10:01:12Z"), "1分12秒");
  console.log("Market Pulse frontend mock tests passed");
})().catch(error => { console.error(error); process.exit(1); });
