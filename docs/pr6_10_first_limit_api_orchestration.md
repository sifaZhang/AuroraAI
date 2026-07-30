# PR6.10：首板回调 API 与安全运行编排

## 1. 背景

PR6.9 已完成每日候选闭环：

```text
数据与检测覆盖检查
→ D0～D6 活动事件池
→ tail_preview / close_confirmed
→ S/A/B 分级
→ run/item/snapshot/evidence
→ JSON / Markdown
```

现有正式入口为：

- `evaluate_candidate()`
- `compare_preview()`
- `run_daily_candidates()`
- `review_event()`
- `cached_minute_provider()`
- `export_results()` / `human_report()`
- CLI：`backend.strategy.first_limit.run_daily_candidates`

PR6.10 的任务不是增加选股规则，而是把 PR6.9 的查询结果和运行能力以稳定 API 暴露给后续页面。

开始实现前必须先审计仓库已有 FastAPI 路由、依赖注入、数据库连接、异常处理和后台任务模式，优先沿用现有项目结构。

---

## 2. 本 PR 目标

完成以下闭环：

```text
前端/API 调用
→ 参数校验
→ 查询候选、证据、运行和尾盘/收盘变化
→ 安全触发 PR6.9 runner
→ 防重复与并发保护
→ 返回稳定 DTO 和错误契约
```

完成后应允许 PR6.11 页面：

1. 按交易日和阶段查看候选；
2. 筛选 S/A/B、生命周期和股票；
3. 展开查看规则证据与淘汰原因；
4. 对比尾盘预警和收盘确认；
5. 查看运行状态与失败项；
6. 手动触发尾盘或收盘计算；
7. 轮询运行状态，但不重复创建相同运行。

---

## 3. 明确不包含

本 PR 不做：

- 不新增或修改候选策略规则、阈值、S/A/B 映射；
- 不复制 `evaluate_candidate()`、`compare_preview()` 的业务逻辑；
- 不开发 HTML、CSS、JavaScript 页面；
- 不做定时任务、邮件、推送或自动交易；
- 不实现真实行情刷新按钮；
- 不把 PR6.8 历史分钟复核 runner 当成实时选股入口；
- 不联网拉取真实行情，不操作生产数据库；
- 不提交、push 或创建 GitHub PR。

如果 API 触发计算时缺少日线、分钟线、检测事件或行业上下文，应返回 PR6.9 的真实状态或明确错误，不在 API 层伪造数据。

---

## 4. 推荐路由

路由前缀优先遵循仓库现有风格。若项目统一使用 `/api`，建议：

```text
GET  /api/first-limit/candidates
GET  /api/first-limit/candidates/{candidate_id}
GET  /api/first-limit/runs
GET  /api/first-limit/runs/{run_id}
GET  /api/first-limit/runs/{run_id}/items
GET  /api/first-limit/preview-comparison
POST /api/first-limit/runs
```

若现有项目有统一版本前缀或命名约定，应适配现有约定，但不得同时保留两套重复路由。

### 4.1 候选列表

```http
GET /api/first-limit/candidates
```

查询参数：

```text
trade_date       必填，YYYY-MM-DD
stage            必填，tail_preview | close_confirmed
grade            可重复或逗号分隔，S | A | B
lifecycle        可重复或逗号分隔
symbol           可选，规范化后精确匹配
change_type      可选，仅收盘阶段有效
include_unknown  默认 true
limit            默认 100，设置合理上限
offset           默认 0
sort             白名单字段
order            asc | desc
```

默认排序必须稳定，建议：

```text
grade_rank ASC
base_score DESC NULLS LAST
symbol ASC
first_limit_event_id ASC
```

禁止把客户端传入的 `sort` 原样拼入 SQL。必须使用字段白名单映射。

返回：

```json
{
  "items": [],
  "total": 0,
  "limit": 100,
  "offset": 0,
  "filters": {},
  "data_date": "2026-07-30",
  "stage": "tail_preview",
  "run_id": 123,
  "run_status": "success"
}
```

列表项至少包括：

```text
candidate_id
run_id
first_limit_event_id
symbol
trade_date
stage
as_of
observation_day
lifecycle
grade
base_grade / source_grade（若正式持久化已有）
base_score（若已有）
change_type
reason_code
display_text
first_limit_date
preview_candidate_id（收盘阶段若可关联）
created_at / updated_at
```

不要为了填满 DTO 而推导数据库中不存在的字段。可空字段应保持 `null`。

### 4.2 候选详情

```http
GET /api/first-limit/candidates/{candidate_id}
```

返回候选快照及其全部证据。证据顺序必须使用持久化 `ordinal`，并以 `rule_code` 作为稳定次级排序。

```json
{
  "candidate": {},
  "evidence": [
    {
      "rule_code": "example",
      "result": "pass",
      "actual_value": 1.0,
      "threshold_value": 1.2,
      "unit": "ratio",
      "source_date": "2026-07-30",
      "source_time": null,
      "reason_code": "example_passed",
      "display_text": "……",
      "ordinal": 10
    }
  ],
  "run": {}
}
```

必须区分：

- 候选不存在：404；
- 候选存在但证据为空：200，`evidence=[]`；
- 数据库读取失败：统一 500 错误结构。

### 4.3 运行列表

```http
GET /api/first-limit/runs
```

支持：

```text
trade_date
stage
status
strategy_version
limit
offset
```

默认按 `created_at DESC, run_id DESC`。

每个 run 返回：

```text
run_id
trade_date
stage
as_of
data_cutoff
status
parameter_hash
strategy_version
detection_version
pullback_version
context_version
requested_count
success_count
pending_count
failed_count
confirmed_count
eliminated_count
indeterminate_count
created_at
started_at
finished_at
error_message（如正式 schema 已保存）
```

统计优先读取正式账本字段；若必须聚合 item，应在 repository 层完成，避免逐 run N+1 查询。

### 4.4 运行详情

```http
GET /api/first-limit/runs/{run_id}
```

返回：

- run 元数据；
- item 状态统计；
- 候选等级与生命周期统计；
- 失败摘要；
- 可用于轮询的终态标识 `terminal`。

终态至少包括：

```text
success
partial
failed
```

实际状态名称必须以 PR6.9 schema 为准，不得另外发明与数据库不一致的状态。

### 4.5 运行项

```http
GET /api/first-limit/runs/{run_id}/items
```

支持：

```text
status
symbol
limit
offset
```

返回 event 级 item，至少包括：

```text
item_id
run_id
first_limit_event_id
symbol
status
candidate_id
error_code
error_message
created_at
updated_at
```

错误信息用于本地诊断，但 API 不应暴露堆栈、SQL、文件路径、Token 或凭据。

### 4.6 尾盘与收盘对比

```http
GET /api/first-limit/preview-comparison
```

查询参数：

```text
trade_date       必填
symbol           可选
change_type      可选
grade            可选，指收盘最终等级
limit
offset
```

只读取已持久化的尾盘和收盘快照，并复用 PR6.9 已有对比结果或 `compare_preview()` 的正式服务入口。

允许的变化类型以 PR6.9 为准：

```text
unchanged
upgraded
downgraded
newly_qualified
eliminated
preview_missing
```

不得仅凭等级字符串在路由函数中另写一套比较逻辑。

每项至少返回：

```text
first_limit_event_id
symbol
preview_candidate_id
close_candidate_id
preview_lifecycle
close_lifecycle
preview_grade
close_grade
change_type
change_reason_code
change_display_text
```

### 4.7 触发运行

```http
POST /api/first-limit/runs
```

请求体建议：

```json
{
  "trade_date": "2026-07-30",
  "stage": "tail_preview",
  "as_of": "2026-07-30T14:55:00+08:00",
  "data_cutoff": "2026-07-30T14:55:00+08:00",
  "symbols": ["000001.SZ"],
  "strategy_version": "…",
  "detection_version": "…",
  "pullback_version": "…",
  "context_version": "…",
  "detect_missing_events": false
}
```

API 只负责：

1. 校验和规范化参数；
2. 生成与 PR6.9 完全一致的参数哈希；
3. 进行活动运行去重；
4. 调用 `run_daily_candidates()`；
5. 返回 run 标识和当前状态。

禁止：

- 路由中逐事件评价；
- 路由中直接写 snapshot/evidence；
- 通过 shell 启动 CLI；
- 将 `force` 暴露为普通页面按钮；
- 在 Web 请求中隐式扩大 symbols 范围；
- 自动修改已有 run 的参数。

---

## 5. 同步还是后台运行

先审计仓库现有运行方式。优先级：

1. 若项目已有可靠的进程内任务/作业编排抽象，复用它；
2. 若没有，V1 可使用 FastAPI/Starlette 已有后台任务机制，但必须明确其仅适合本地单进程；
3. 不为 PR6.10 新增 Redis、Celery、RQ 等基础设施。

推荐响应：

```text
202 Accepted
Location: /api/first-limit/runs/{run_id}
Retry-After: 2
```

```json
{
  "run_id": 123,
  "status": "pending",
  "reused": false,
  "poll_url": "/api/first-limit/runs/123"
}
```

如果当前架构无法在返回响应前可靠获得 `run_id`，应在 service/repository 层增加最小的“预创建 run + 冻结来源 items”入口，再由 runner 接管；不得创建一张与 PR6.9 重复的 API 作业账本。

若只能同步执行，也必须：

- 保持同样的重复运行保护；
- 返回完成后的 run；
- 在文档中明确这是本地 V1 限制；
- 测试异常时 run 能收敛为失败状态。

不要伪装成异步：如果函数实际在响应返回前完成，就返回 200，而不是虚假的 202。

---

## 6. 重复点击与并发保护

这是 PR6.10 的硬性验收项。

### 6.1 活动运行身份

活动运行唯一身份应与 PR6.9 的正式自然键一致：

```text
(trade_date, stage, parameter_hash)
```

`parameter_hash` 必须复用 PR6.9 的规范化与哈希实现，不能在 API 层重新定义。

### 6.2 行为

当相同身份的 run 已存在：

- 若状态为 pending/running：不新建，返回已有 run，`reused=true`；
- 若状态为 success/partial/failed：默认返回已有终态 run，不自动重跑；
- 需要重跑时必须走已有 CLI/admin 能力，PR6.10 普通 API 不开放 `force`；
- 不同参数哈希允许产生不同 run；
- 相同股票集合但顺序不同必须归一为相同参数哈希；
- 连续或并发请求最多只能有一个活动 run 获得执行权。

### 6.3 原子性

不能仅使用：

```text
先 SELECT
再 INSERT
```

这种实现会在并发下重复创建。

应优先使用数据库唯一约束、原子插入或明确事务锁。若 PR6.9 schema 的唯一约束已经覆盖自然键，应复用；若它允许同一自然键保留历史多次运行，则增加能表达“活动运行唯一性”的最小机制，并解释 SQLite 下的并发语义。

不得依赖单进程内 Python 全局锁作为唯一保护。

---

## 7. 时间与未来信息校验

所有 API 输入时间必须带时区，内部规范遵循现有项目约定。

硬性要求：

- `tail_preview` 的 `as_of` 默认 14:55，且不得晚于 `data_cutoff`；
- `tail_preview` 不得因为 API 调用发生在收盘后就读取 14:55 之后的信息；
- `close_confirmed` 默认 15:00；
- `trade_date`、`as_of`、`data_cutoff` 必须处在逻辑一致的日期；
- 不允许 API 使用服务器当前时间替代用户明确传入的历史 `as_of`；
- 不读取次日数据；
- 无交易日应返回明确的 422 或项目统一业务错误，不悄悄改到前一交易日。

中国市场时间与奥克兰界面显示转换留给 PR6.11；本 API 的策略时间以正式数据契约为准。

---

## 8. Repository、Service 与 Router 分层

建议新增或扩展：

```text
backend/strategy/first_limit/api_models.py
backend/strategy/first_limit/api_repository.py
backend/strategy/first_limit/api_service.py
backend/api/routes/first_limit.py
```

实际路径服从仓库现有结构。

职责：

### Repository

- 参数化 SQL；
- 候选、证据、run、item、对比查询；
- 分页和聚合；
- 原子查找或创建活动 run；
- 不包含 HTTPException；
- 不计算选股规则。

### Service

- 输入规范化；
- 参数哈希复用；
- DTO 组装；
- 运行去重与 runner 调用；
- 将领域错误转换为稳定服务错误；
- 不直接拼 SQL。

### Router

- Pydantic 请求/响应模型；
- HTTP 状态码；
- 依赖注入；
- 调用 service；
- 保持薄层。

禁止路由层直接连接 SQLite 或读取全表后用 Python 分页。

---

## 9. 响应模型与兼容性

使用明确的 Pydantic 模型，不返回裸数据库 Row。

要求：

- 日期和时间输出 ISO 8601；
- `unknown`、`null` 和 `0` 必须保留区别；
- 数值精度遵循已有 DTO 习惯，不在 API 中任意格式化为字符串；
- 枚举值与 PR6.9 一致；
- 列表响应结构一致；
- 可空字段显式声明；
- 不返回 NaN/Infinity；
- OpenAPI 能生成完整 schema。

建议统一错误：

```json
{
  "error": {
    "code": "first_limit_run_conflict",
    "message": "……",
    "details": {}
  }
}
```

如果项目已有全局错误格式，必须复用，不另建第二套。

建议状态码：

```text
200 查询成功或同步运行完成
202 后台运行已接受
404 candidate/run 不存在
409 确有不能复用的运行冲突
422 参数、阶段、日期或时间边界错误
500 未预期服务错误
```

对于“相同活动 run 已存在且可以复用”，优先返回 200/202 和 `reused=true`，不要当成错误。

---

## 10. 查询性能

API 查询不得造成明显 N+1。

检查现有索引是否覆盖：

```text
runs: trade_date, stage, status, parameter_hash
items: run_id, status, first_limit_event_id
snapshots: run_id, symbol, grade, lifecycle, change_type
evidence: candidate_id, ordinal
```

只有在 `EXPLAIN QUERY PLAN` 或明确查询模式表明需要时才新增索引。新索引必须通过下一号迁移文件增加并注册；不要修改已提交迁移021。

分页必须在数据库完成。`limit` 设置上限，例如 500。

---

## 11. 安全要求

- 所有 SQL 参数化；
- symbol、stage、grade、status、sort 均白名单校验；
- POST 不接受任意模块名、命令、数据库路径、Token 环境变量名；
- 不从请求体读取 GM Token；
- 错误响应不暴露堆栈、绝对路径、SQL 或环境变量；
- 保持现有 CORS、认证和本地访问策略，不擅自扩大；
- 如果项目当前没有认证，不在本 PR 引入完整认证系统，但在文档标明运行 POST 只适用于本地可信环境；
- GET 查询不能触发数据刷新或策略运行。

---

## 12. 测试要求

新增 API/service/repository 测试，全部使用临时数据库和固定样本，不联网。

至少覆盖：

### 查询

- 候选列表按日期、阶段、等级、生命周期、symbol 过滤；
- 稳定排序；
- limit/offset 和 total；
- 非法 sort 被拒绝；
- `unknown` 与 `null` 不被错误过滤；
- 候选详情及 evidence ordinal；
- 不存在 candidate/run 返回 404；
- run 列表和统计；
- run items 失败摘要不暴露内部堆栈；
- 尾盘/收盘六种变化类型；
- 没有尾盘快照时的 `preview_missing`。

### 运行

- POST 参数规范化后调用 `run_daily_candidates()`，不复制规则；
- 相同请求连续点击只执行一次；
- 两个并发相同请求只产生一个活动 run；
- symbols 顺序不同仍复用相同 run；
- 不同参数哈希产生不同 run；
- 已完成 run 默认复用，不自动 force；
- runner 异常后 run 收敛为 failed；
- 一个事件失败时 API 能查询 partial 和失败 item；
- tail_preview/close_confirmed 时间默认值；
- `as_of > data_cutoff` 被拒绝；
- 非交易日不自动改日；
- dry-run、force 不通过普通 POST 暴露。

### 契约

- OpenAPI 包含新增路由；
- Pydantic 可空字段正确；
- 无 NaN/Infinity；
- API 返回字段和枚举稳定；
- 全局错误处理格式一致。

测试建议：

```bash
python -m pytest tests/api/test_first_limit_api.py -q
python -m pytest tests/strategy/test_first_limit_api_service.py -q
python -m pytest tests/strategy -q
python -m pytest -q
```

按仓库实际测试目录调整。

---

## 13. 文档

新增：

```text
docs/pr6_10_first_limit_api.md
```

至少说明：

- PR 边界；
- 路由和请求/响应示例；
- 字段、枚举和错误码；
- 尾盘与收盘的时间边界；
- 重复运行和并发语义；
- 同步或后台执行方式及本地限制；
- 如何轮询 run；
- 测试结果；
- 真实环境待验证项。

如果项目已有 API 文档或 README 路由目录，应最小更新入口，但不要在本 PR 写页面使用说明。

---

## 14. 验收标准

PR6.10 只有同时满足以下条件才算完成：

- API 能查询 PR6.9 的候选、证据、run、item 和对比结果；
- API 不重复实现策略规则；
- POST 只调用正式 runner/service；
- 相同活动运行在连续和并发请求下只执行一次；
- 普通 API 不开放 force；
- 尾盘与收盘未来信息边界保持不变；
- 所有列表数据库分页且排序稳定；
- 无 N+1 查询；
- 错误格式稳定且不泄露内部信息；
- OpenAPI schema 正确；
- 新增测试、strategy 测试和全量测试通过；
- 数据库 `foreign_key_check` 与 `integrity_check` 通过；
- `git diff --check` 通过；
- 仅包含 PR6.10 相关文件。

---

## 15. 实施结束时的输出格式

实现完成后报告：

1. 实现范围和未实现范围；
2. 新增路由；
3. 请求/响应模型；
4. 重复点击和并发保护方案；
5. runner 调用链；
6. 时间与未来信息边界；
7. 数据库迁移和索引（若有）；
8. 测试命令与结果；
9. `git diff --stat`；
10. `git status --short`；
11. 真实环境待验证项。

停留在：

> 可选择性暂存并提交，但尚未提交

不要执行：

```text
git add
git commit
git push
gh pr create
```

---

## 16. PR6.10 之后

下一步为 PR6.11：首板回调页面。

PR6.11 再实现：

- 交易日与阶段切换；
- S/A/B 候选表；
- 证据展开；
- 尾盘/收盘变化标识；
- run 状态与失败提示；
- 手动运行按钮及轮询；
- 页面时区展示；
- 与现有 AuroraAI 导航集成。

PR6.10 不应提前实现这些界面。
