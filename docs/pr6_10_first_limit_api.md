# PR6.10：首板回调 API 与安全运行编排

## 范围

本阶段将 PR6.9 已持久化的每日候选、证据、run/item 账本和尾盘/收盘变化暴露为只读 API，并提供一个本地可信环境使用的手动运行入口。API 不重新实现候选规则，不刷新行情，不接受 GM Token，不开放 `dry-run`、`resume` 或 `force`，也不包含页面、定时任务、推送和交易功能。

当前 V1 同步调用正式 `run_daily_candidates()`。运行在 HTTP 响应前完成，因此成功响应使用 `200 OK`，不会伪装成后台 `202`。长任务、进程重启恢复和多机作业队列仍是后续基础设施工作。

## 路由

所有路由使用 `/api/first-limit` 前缀：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/candidates` | 查询某交易日、阶段的最新 run 候选 |
| GET | `/candidates/{candidate_id}` | 查询候选及按 ordinal 排序的证据 |
| GET | `/runs` | 查询运行账本 |
| GET | `/runs/{run_id}` | 查询运行、分组统计和失败摘要 |
| GET | `/runs/{run_id}/items` | 查询 event 级运行项 |
| GET | `/preview-comparison` | 查询已持久化的尾盘/收盘变化 |
| POST | `/runs` | 同步触发或复用正式候选运行 |

列表查询均在 SQLite 中完成分页，`limit` 默认 100、最大 500。候选默认稳定排序为等级 S/A/B/空、分数降序、symbol、event id。自定义排序仅接受 `grade_rank`、`base_score`、`symbol`、`first_limit_event_id`、`created_at`；客户端值不会直接拼接为 SQL 标识符。

`grade`、`lifecycle` 和 `status` 支持重复查询参数或逗号分隔。候选查询额外接受 `grade=none`，用于数据库端筛选正式 `grade=null` 快照；它只是一项向后兼容的展示筛选，不是新等级。symbol 使用项目统一规则规范化为 `000001.SZ` 形式。

## 查询示例

```http
GET /api/first-limit/candidates?trade_date=2026-07-30&stage=close_confirmed&grade=S,A&limit=50
```

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0,
  "filters": {
    "grade": ["S", "A"],
    "lifecycle": [],
    "symbol": null,
    "change_type": null,
    "include_unknown": true,
    "sort": "grade_rank",
    "order": "asc"
  },
  "data_date": "2026-07-30",
  "stage": "close_confirmed",
  "run_id": null,
  "run_status": null
}
```

候选 DTO 只返回正式持久化或直接关联的字段。PR6.9 未单独持久化 `base_grade`，因此该字段保持 `null`；`base_score` 对应快照 `score`。`unknown` 证据结果、空等级和数值 `0` 不会互相转换。详情中的 `actual_value`、`threshold_value` 从正式 JSON 值恢复，不格式化为字符串。

尾盘/收盘变化只读取 close 快照的正式 `change_type` 和 `preview_candidate_id`，允许值为：

- `unchanged`
- `upgraded`
- `downgraded`
- `newly_qualified`
- `eliminated`
- `preview_missing`

API 路由不按等级字符串再次计算变化。`preview_missing` 的尾盘字段保持 `null`。

## 运行请求

```http
POST /api/first-limit/runs
Content-Type: application/json
```

```json
{
  "trade_date": "2026-07-30",
  "stage": "tail_preview",
  "as_of": "2026-07-30T14:55:00+08:00",
  "data_cutoff": "2026-07-30T14:55:00+08:00",
  "symbols": ["000001.SZ"],
  "strategy_version": "first_limit_daily_candidates_v1",
  "detection_version": "first_limit_v1",
  "pullback_version": "first_limit_pullback_v1",
  "context_version": "first_limit_context_v1",
  "detect_missing_events": false
}
```

`as_of` 和 `data_cutoff` 可省略。`tail_preview` 默认二者均为交易日 14:30，`close_confirmed` 默认均为 15:00。显式时间必须带时区；内部由 PR6.9 的 `normalize_parameters()` 转换为上海策略时间并生成完全相同的 `parameter_hash`。symbol 去重排序也由该正式入口完成。

同步响应：

```json
{
  "run_id": "candidate-…",
  "status": "success",
  "reused": false,
  "poll_url": "/api/first-limit/runs/candidate-…"
}
```

前端仍可用 `poll_url` 查询状态，以兼容以后切换到真正后台执行；当前响应返回时运行已经收敛。

## 时间与未来信息边界

- `trade_date` 必须在 `a_share_trading_calendar` 中明确为中国市场开市日；无记录或休市返回 422，不回退到前一日。
- `tail_preview` 的 `as_of` 只能为 14:30～14:55，默认 14:30。
- `close_confirmed` 的 `as_of` 不早于 15:00，默认 15:00。
- `as_of` 与 `data_cutoff` 必须属于 `trade_date`，且 `as_of <= data_cutoff`。
- API 不使用服务器当前时间替代历史请求时间。
- API 将规范化参数原样交给 PR6.9 runner；尾盘分钟读取上界、日线截止、D0～D6 生命周期和终态停止规则仍由正式 runner 保证。
- 缺少日线、分钟线、检测或上下文时保留 PR6.9 的 `indeterminate`、失败或数据完整性语义，不在 API 层补造结果。

## 幂等、并发与事务

正式身份为：

```text
(trade_date, stage, parameter_hash)
```

数据库迁移 021 已有该三元组唯一约束。API 在同一个正式 `daily_candidate_runs` 账本中执行原子 `INSERT OR IGNORE`：

1. 唯一插入成功的请求取得执行权；
2. 执行者调用 `run_daily_candidates(..., resume=True, claimed=True)`；
3. runner 冻结来源 event items，再沿用 PR6.9 的 event 事务和失败隔离；
4. 其他连续或并发请求读取同一 run，返回 `reused=true`，不进入 runner；
5. 已完成的 success/partial/failed run 同样默认复用，不隐式重跑；
6. runner 级异常将已认领 run 收敛为 failed，然后 API 返回稳定 500。

该方案不增加重复的 API 作业表，也不把进程内 Python 锁作为正确性基础。相同 symbols 不同顺序得到同一哈希；不同正式参数可创建不同 run。普通 POST 的 Pydantic 模型禁止额外字段，因此不能传入 `force`、`resume`、`dry_run`、数据库路径、模块名或凭据。

## Run/item 状态和错误

API 沿用 PR6.9 状态：

- run：`running`、`success`、`partial`、`failed`
- item：`pending`、`success`、`indeterminate`、`skipped`、`failed`
- 终态：`success`、`partial`、`failed`

run 详情一次性聚合 item 状态、候选等级和生命周期，不按 run/candidate 发起 N+1 查询。item 的 API `item_id` 使用 run 内稳定自然键 `first_limit_event_id`；完整身份仍为 `(run_id, first_limit_event_id)`。

失败响应：

```json
{
  "error": {
    "code": "first_limit_invalid_run_parameters",
    "message": "data_cutoff must not precede as_of",
    "details": {}
  }
}
```

常用状态码：

- 200：查询成功、同步运行完成或复用已有 run
- 404：candidate/run 不存在
- 422：请求模型、白名单、symbol、日期或时间边界错误
- 500：数据库读取或 runner 未预期失败

失败项只公开 event、symbol、错误类型和通用诊断文字。API 不返回原始 traceback、SQL、绝对路径、Token 或环境变量；请求校验错误也不回显输入值。

## 数据库与查询

PR6.10 没有新增迁移。迁移 021 已提供：

- `UNIQUE(trade_date, stage, parameter_hash)`
- run 的 `(trade_date, stage, status)` 索引
- snapshot 的 `(trade_date, stage, candidate_grade, symbol, first_limit_event_id)` 索引
- item 的 `(run_id, status)` 索引
- evidence 主键前缀 `candidate_id`

当前查询模式未提供增加索引的必要证据，因此没有修改已提交的 021。

## 本地运行

使用现有 FastAPI 启动方式后，可在 OpenAPI 中查看完整请求/响应 schema：

```bash
uvicorn backend.api.app:app --reload
```

POST 运行入口仅适用于当前本地可信环境。项目尚无认证，本 PR 不擅自增加认证或扩大 CORS/网络访问范围。

## 验收与已知限制

固定临时 SQLite 样本覆盖候选过滤、稳定分页、证据顺序、run/item 聚合、六类尾盘/收盘变化、错误脱敏、参数规范化、连续和并发去重、终态复用、runner 失败收敛、非交易日、未来时间边界与 OpenAPI schema。

本地验收结果：

- `python -m pytest tests/test_first_limit_api.py -q`：7 passed
- `python -m pytest tests/strategy -q`：144 passed
- `python -m pytest -q`：347 passed

真实环境仍需验证：

- 大规模历史候选分页的实际延迟；
- 单进程同步 HTTP 在真实每日数据量下的请求耗时；
- 真实 GM 缓存缺失时的运行耗时与诊断体验；
- 若以后部署多进程/多机，SQLite 写锁等待和进程崩溃后的 running run 运维恢复。

本 V1 不提供后台队列、进程崩溃自动接管、认证、真实行情刷新和页面。
