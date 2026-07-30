# PR6.12：首板回调数据自动补齐与一键完整筛选

## 1. 目标

在现有 PR6.1～PR6.11 基础上，实现真正可日常使用的一键运行闭环：

```text
选择目标交易日
→ 点击“生成尾盘预警”或“执行收盘确认”
→ 自动检查并补齐截至目标时点缺失的数据
→ 自动完成全市场首板检测、质量评分、回调观察和上下文计算
→ 自动获取候选所需分钟数据
→ 执行 PR6.9 正式候选 runner
→ 页面展示完整筛选结果、覆盖情况和执行进度
```

用户不应再手动执行 calendar、daily、statuses、detect 等 CLI，也不应自行判断应同步哪些日期。

默认运行范围是全市场合格 A 股证券池。页面现有“股票代码”输入框继续只筛选结果，不改变运行范围。

## 2. 本 PR 的核心产品语义

### 2.1 一次点击完成

点击运行按钮后，一项后台作业必须依次完成：

1. 校验目标日期与目标阶段；
2. 更新并校验中国交易日历；
3. 解析目标交易日及策略所需历史窗口；
4. 同步全市场证券主数据；
5. 同步所需每日证券状态；
6. 增量补齐日线、权威涨跌停价和策略必需字段；
7. 对所需 D0 日期执行完整首板检测；
8. 对首板事件执行已有正式质量评分；
9. 更新 D1～D5 回调观察数据；
10. 补齐行业、板块和市场上下文；
11. `tail_preview` 阶段仅为实际观察候选补齐截至 `as_of` 的分钟数据；
12. 调用 PR6.9 `run_daily_candidates()`；
13. 保存覆盖报告并返回最终结果。

不得在 PR6.12 中复制首板检测、评分或候选规则。所有计算必须调用已有正式 service/runner；若已有模块只有 CLI，应抽取可复用 Python service，再让 CLI 和后台作业共同调用。

### 2.2 “0条候选”的严格含义

- 作业未完成、覆盖不足或任一步骤失败：不能显示“没有候选”；
- `detection_complete=false`：显示“检测覆盖不完整，0条不代表无候选”；
- 只有全市场目标范围和必需窗口均通过覆盖校验，且候选结果为0，才显示“本次完整筛选未发现候选”；
- `partial` 可以展示已生成结果，但必须醒目标注缺失范围和受影响步骤；
- 不允许把网络失败、凭据缺失、数据源空响应或仅检测少量股票解释为无候选。

## 3. 范围与边界

### 3.1 包含

- 全市场一键数据准备；
- 按表和股票计算增量缺口；
- 后台作业、步骤状态、进度查询和日志摘要；
- 作业幂等、重复点击复用、失败恢复和断点续跑；
- 全市场首板检测及后续正式流水线编排；
- PR6.11 页面进度展示和完成后自动加载结果；
- 数据覆盖报告；
- 适用于本地可信环境的凭据检查和可读错误；
- CLI/API/页面共享同一编排 service。

### 3.2 不包含

- 修改首板回调策略规则或阈值；
- 新行情供应商或数据源自动降级；
- 定时任务、邮件、微信或其他通知；
- 云端部署和多用户认证；
- 通用分布式任务队列；
- PR6.8 历史成交复核页面；
- 为全市场无差别下载分钟线。

## 4. 日期、窗口和未来信息边界

### 4.1 目标时点

- `tail_preview`：默认 `14:55 Asia/Shanghai`，允许范围继续沿用 PR6.10 的 14:40～14:55；
- `close_confirmed`：默认 `15:00 Asia/Shanghai`；
- `as_of` 和 `data_cutoff` 必须带时区并属于目标交易日；
- 所有数据查询与下载都必须受 `data_cutoff` 限制；
- 禁止读取目标时点之后的分钟数据、次日日线、后来修订的状态或未来行业信息。

### 4.2 自动计算所需窗口

不得固定为“只下载所选当天”“当天加过去7个自然日”或其他简单自然日区间。用户选择目标日后，系统必须同时补齐目标日和过去观察周期的数据，并继续向前补齐判断这些历史日期所需要的前置窗口。

最低依赖关系是：

```text
目标日 T
├─ T 当天截至 data_cutoff 的日状态和行情
├─ 最近 D0～D6 的交易日：寻找仍在有效观察期内的首板事件
└─ 每一个潜在 D0 之前至少20个交易日：确认它确实是过去20个交易日第一次涨停
```

因此实际最短范围通常已经超过过去7天，大致至少包含观察窗口、20个交易日的前置检测窗口，以及正式评分和行业上下文要求的额外窗口。具体开始日期不得写死为26个自然日或26个交易日，而应由正式模块声明的依赖共同计算。

编排器应根据正式规则计算：

- 首板检测所需至少20个此前交易日；
- D1～D5 候选需要回溯可能产生仍有效事件的 D0；
- 为最近观察周期内的每一个潜在 D0 执行检测，不能只检测目标日；
- 周末、节假日和明确停牌不错误消耗观察日；
- 均线、成交量、行业上下文等已有正式计算若需要更长窗口，以其正式契约为准；
- 交易日历本身不足时，先扩展日历，再计算其他数据窗口；
- 增加少量明确、可测试的安全缓冲交易日，但不得借此读取目标日之后的数据。

最终计划必须记录每类数据实际计算出的 `required_start`、`required_end`、本地水位和缺失区间。

例如选择 `2026-07-30` 时，不是只同步 `2026-07-30`，也不是机械同步 `2026-07-24～2026-07-30`。系统应先通过交易日历找出可能仍处于 D1～D5/D6 的全部 D0 交易日，再从最早 D0 向前取足至少20个交易日，并继续满足质量评分和上下文的正式窗口。已有数据按水位复用，只下载缺失部分。

### 4.3 今日、历史和未来日期

- 目标日早于或等于已完整覆盖日期：直接复用本地数据，必要时只重跑缺失步骤；
- 当天运行：允许获取截至 `data_cutoff` 已产生的数据；
- 未来日期：拒绝；
- 目标日经更新后的权威交易日历确认非开市日：拒绝；
- 历史尾盘分钟数据源不可得时：保留 `indeterminate/pending_close_confirmation`，不得使用收盘日线代替尾盘分钟线。

## 5. 数据范围

### 5.1 全市场证券池

建立目标日的正式 eligible universe，至少排除：

- 非 A 股普通股票；
- ST/退市整理等已有规则明确排除的证券；
- 目标日未上市或已退市证券；
- 明确停牌且不应参与当日检测的证券；
- 缺少关键证券身份且无法可靠判断交易板的记录。

证券池必须有可审计快照或确定性重建依据，记录：

```text
trade_date
universe_version
total_symbols
eligible_symbols
excluded_symbols
exclusion_reason counts
source_cutoff
```

不能用“数据库当前已有证券主数据”作为全市场范围，因为真实库目前只有4只正式主数据。

### 5.2 增量策略

- 按数据表、日期和 symbol 覆盖计算缺口；
- 已有且通过质量校验的数据不重复下载；
- 同一日期零散缺口只补缺失 symbol；
- 空响应必须区分“合法无数据”和“同步失败”；
- 数据写入保持幂等；
- 单股票失败隔离，重试次数和退避复用现有采集约定；
- 不为全市场预下载分钟线，只在首板事件进入有效 D1～D5 观察范围后按需下载。

## 6. 后台作业模型

当前 PR6.10 的同步 POST 不适合全市场长任务。新增专用一键作业，不改变既有候选 run 的自然键。

建议迁移 `022_first_limit_pipeline_jobs.sql` 新增：

### 6.1 `first_limit_pipeline_jobs`

至少包含：

```text
id
trade_date
stage
as_of
data_cutoff
universe_version
parameter_json
parameter_hash
status
current_step
progress_current
progress_total
progress_percent
message
candidate_run_id
coverage_complete
created_at
started_at
finished_at
heartbeat_at
error_code
error_message
```

状态：

```text
pending
running
success
partial
failed
cancelled
```

唯一身份至少由以下规范化参数决定：

```text
(trade_date, stage, parameter_hash)
```

普通重复点击：

- 存在 `pending/running`：返回同一 job；
- 存在已完成且输入、数据版本和覆盖仍有效的 job：返回同一结果；
- 数据水位已经变化且能补齐先前缺口时：允许显式 retry/resume，不悄悄覆盖历史 job；
- 不依赖 Python 全局锁；
- 使用数据库原子认领。

### 6.2 `first_limit_pipeline_steps`

每个步骤至少记录：

```text
job_id
step_code
ordinal
status
progress_current
progress_total
started_at
finished_at
input_summary_json
output_summary_json
error_code
error_message
```

步骤代码稳定，例如：

```text
calendar
universe
security_master
daily_status
daily_bars
limit_detection
quality_scoring
pullback_observation
market_context
minute_bars
candidate_generation
coverage_validation
```

### 6.3 `first_limit_pipeline_coverage`

保存各数据域的覆盖结果：

```text
job_id
domain
required_start
required_end
expected_count
covered_count
missing_count
coverage_ratio
complete
details_json
```

不要把完整逐股票错误塞进 job 主表；应保存汇总和可分页的失败明细，或复用已有 run item/同步账本。

## 7. 执行与恢复

### 7.1 本地后台执行

可采用适合当前单机 FastAPI 的持久化后台 worker，但必须满足：

- HTTP 创建作业后快速返回 `202`；
- 作业不依赖浏览器连接持续存在；
- 页面刷新后可恢复；
- worker 通过数据库认领 pending job；
- 每个步骤持续更新 heartbeat；
- 服务重启后能识别陈旧 `running` job；
- 陈旧 job 不直接永久卡死，可标记 interrupted 后从安全步骤 resume；
- 不得声称具备跨机器可靠队列能力。

如果暂不引入 Celery/Redis，可实现数据库持久化的单进程 worker；文档必须明确它是本地 V1。

### 7.2 断点续跑

- 每一步骤必须幂等；
- 已完成并校验有效的步骤可跳过；
- 失败步骤可重试；
- resume 不改变原作业参数和目标范围；
- force 只在内部维护接口或 CLI 开放，普通页面不展示；
- 重新执行下游步骤前应使旧的派生结果失效或生成新版本，不能把新旧覆盖混成一次“完整”运行。

### 7.3 事务

- 作业和步骤状态更新使用短事务；
- 不在下载全市场数据期间持有长 SQLite 写事务；
- 单 symbol 或小批量为最小数据写入事务；
- 首板事件、评分、观察、上下文和候选仍遵守各自既有事务边界；
- 最终 `coverage_complete=true` 只能在全部硬覆盖检查通过后一次性写入。

## 8. API

建议新增：

```text
POST /api/first-limit/pipeline-jobs
GET  /api/first-limit/pipeline-jobs
GET  /api/first-limit/pipeline-jobs/{job_id}
GET  /api/first-limit/pipeline-jobs/{job_id}/steps
GET  /api/first-limit/pipeline-jobs/{job_id}/coverage
GET  /api/first-limit/pipeline-jobs/{job_id}/failures
POST /api/first-limit/pipeline-jobs/{job_id}/retry
```

创建请求只接受产品必要字段：

```json
{
  "trade_date": "2026-07-30",
  "stage": "tail_preview",
  "as_of": "2026-07-30T14:55:00+08:00",
  "data_cutoff": "2026-07-30T14:55:00+08:00"
}
```

默认不接受 symbols，确保页面运行是全市场完整筛选。测试或运维受控小样本可以通过 CLI 或明确的内部参数实现，且结果必须标记 `scope=partial`，不得显示为全市场完整覆盖。

创建成功返回 `202`：

```json
{
  "job_id": 123,
  "status": "pending",
  "reused": false,
  "poll_url": "/api/first-limit/pipeline-jobs/123"
}
```

所有响应使用 PR6.10 已有稳定错误结构并脱敏。不得返回 GM token、SQL、绝对路径或 traceback。

## 9. 页面修改

PR6.11 页面两个按钮改为调用 pipeline job API，不再直接调用同步候选 run API。

### 9.1 运行体验

页面应展示真实步骤：

```text
正在检查交易日历
正在确定全市场股票范围
正在补齐证券主数据
正在同步日线和状态
正在检测首板
正在计算质量和回调观察
正在更新行业及市场上下文
正在补齐候选分钟线
正在生成候选
正在验证数据覆盖
```

同时展示：

- 当前步骤；
- 已处理/总数量；
- 总体进度；
- 已耗时；
- 最近心跳；
- 部分失败数量；
- 可读错误；
- retry 按钮；
- 页面刷新后恢复最近作业。

不得伪造平滑百分比。无法估算总量时使用不确定进度样式，待 universe 冻结后再显示准确分母。

### 9.2 完成后的结果

- `success + coverage_complete=true`：自动加载候选和对比结果；
- `success + 0 candidates`：显示“本次完整筛选未发现候选”；
- `partial`：展示已有结果及覆盖警告，不显示“完整筛选完成”；
- `failed`：保留步骤和失败摘要；
- `running/pending`：禁用同类重复按钮，但允许离开或刷新页面；
- 同一 job 被复用时显示“已复用正在运行/已完成的任务”。

页面股票代码输入框仍仅筛选结果。应增加帮助文本：

> 运行默认覆盖全市场；股票代码仅筛选已生成结果。

## 10. 数据源与凭据

- 复用项目现有 GM/缓存访问层和 `GM_TOKEN`；
- 不在前端传递或保存 token；
- 创建作业前可以做快速配置检查；
- 缺少凭据时快速失败并提示如何在启动服务的环境中配置；
- 不把 token 写入数据库、日志或错误响应；
- 网络或 GM 限流应产生明确 step 失败/partial，不得被吞掉；
- 不在本 PR 增加第二数据源自动兜底。

## 11. CLI

提供与 API 共用 service 的正式 CLI，例如：

```powershell
python -m backend.strategy.first_limit.run_one_click_pipeline `
  --trade-date 2026-07-30 `
  --stage tail_preview `
  --as-of 2026-07-30T14:55:00+08:00 `
  --data-cutoff 2026-07-30T14:55:00+08:00 `
  --wait
```

至少支持：

```text
--trade-date
--stage
--as-of
--data-cutoff
--resume-job-id
--report json|markdown
--wait
```

测试/运维可提供 `--symbols`，但必须将 scope 记录为 partial，并保证对应结果绝不设置全市场 `coverage_complete=true`。

## 12. 测试要求

至少覆盖：

### 12.1 计划与窗口

- 日历落后于目标日时先补日历；
- 目标日、过去观察周期以及每个潜在 D0 的20交易日前置窗口全部纳入计划；
- 根据20交易日、D0～D6 和评分/上下文依赖自动扩展窗口；
- 周末、节假日、停牌语义；
- 已有完整数据不联网重复下载；
- 局部 symbol 缺失仅补缺口；
- 不读取目标时点之后数据；
- 未来日和非交易日拒绝。

### 12.2 全市场与覆盖

- 数据库只有4只主数据时仍能建立完整 universe；
- 全市场成功后 `coverage_complete=true`；
- 受控 symbols 运行永远是 partial scope；
- 某股票/日期缺失时覆盖报告准确；
- 空响应不被误判为合法无候选；
- 完整覆盖且0候选时才给出正式0结果。

### 12.3 编排

- 各正式 runner 按顺序调用；
- 已完成步骤 resume 时跳过；
- 中间失败不执行依赖它的下游步骤；
- 可隔离失败时收敛为 partial；
- 作业级错误收敛为 failed；
- 服务重启后陈旧 running 作业可恢复；
- heartbeat 更新；
- 同一作业重复和并发创建只产生一个执行者；
- 不持有跨网络调用的长事务。

### 12.4 API 与页面

- POST 快速返回202；
- polling 返回稳定步骤和覆盖结构；
- 重复点击返回同一 job；
- 页面刷新恢复；
- success/partial/failed/pending/running 全状态；
- 0候选完整与不完整两种文案；
- 股票筛选不改变运行请求；
- token、路径、SQL 和 traceback 脱敏。

### 12.5 回归

必须运行：

```text
新增 PR6.12 测试
tests/strategy
tests/test_first_limit_api.py
全仓 pytest
py_compile
git diff --check
SQLite foreign_key_check
SQLite integrity_check
```

测试不得真实联网或写用户正式数据库。使用临时数据库和 fake provider。真实环境另做受控小样本验收。

## 13. 真实验收顺序

在用户明确配置 GM_TOKEN 并允许写本地正式数据库后：

1. 用少量 symbols 通过 CLI 验证数据源和步骤，但标记 partial scope；
2. 选一个近期已收盘交易日执行全市场 close_confirmed；
3. 核对 universe 数量、日线/状态覆盖、检测事件和上下文数量；
4. 人工抽查至少一个首板、一个非首板、一个 ST/停牌样本；
5. 核对 `coverage_complete`；
6. 再对当天执行 tail_preview；
7. 验证页面刷新恢复、重复点击复用和结果展示。

真实验收前不要直接修改用户已有策略阈值，也不要用伪造数据填充真实库。

## 14. 建议文件范围

可根据现有结构调整，但建议包含：

```text
backend/strategy/first_limit/pipeline_models.py
backend/strategy/first_limit/pipeline_repository.py
backend/strategy/first_limit/pipeline_service.py
backend/strategy/first_limit/pipeline_worker.py
backend/strategy/first_limit/run_one_click_pipeline.py
backend/api/first_limit.py
backend/strategy/first_limit/api_models.py
backend/strategy/first_limit/api_service.py
backend/collector/...（仅抽取已有采集 service 所必需的修改）
database/migrations/022_first_limit_pipeline_jobs.sql
frontend/first-limit.html
docs/pr6_12_first_limit_one_click_pipeline.md
tests/strategy/test_first_limit_pipeline_*.py
tests/test_first_limit_api.py
```

迁移必须注册到现有数据库初始化入口，并兼容用户当前真实库逐步升级。

## 15. 完成标准

PR6.12 只有同时满足以下条件才算完成：

- 用户选择当天后点击一次即可启动；
- 自动补齐全部必需缺口，不要求手工 CLI；
- 默认运行范围确为全市场合格证券池；
- 首板检测、评分、观察、上下文、分钟线和候选生成均在同一作业中闭环；
- 页面能显示并恢复真实进度；
- 重复点击和并发不会重复执行；
- 覆盖不完整时绝不把0结果说成无候选；
- 完整覆盖时能明确给出完整筛选结果；
- 未来信息边界没有被破坏；
- 全部自动化测试、数据库检查和真实受控验收通过；
- 停留在“可选择性暂存并提交，但尚未提交”状态，不执行 add、commit、push 或创建 PR。

## 16. 本地 V1 实现说明

本 PR 已实现 SQLite 持久化的单机后台 worker。它不是跨机器队列；HTTP
创建任务后返回 `202`，worker 通过数据库状态原子认领任务。服务重启时，
原 `running` 作业和步骤先标记为 `interrupted`，随后从已成功步骤之后安全
续跑。长步骤由独立短连接刷新 heartbeat，下载期间不持有长 SQLite 写事务。

作业自然键是规范化后的 `(trade_date, stage, parameter_hash)`。哈希覆盖
`as_of`、`data_cutoff`、scope、版本和声明式依赖。页面调用只允许
`full_market`；CLI 的 `--symbols` 是受控小样本，永久记录为 `partial`，
即使所选股票全部完成也不会设置 `coverage_complete=true`。

完整流水线步骤为：

```text
calendar -> universe -> security_master -> daily_status -> daily_bars
-> limit_detection -> quality_scoring -> pullback_observation
-> market_context -> minute_bars -> candidate_generation
-> coverage_validation
```

窗口不是固定自然日数。系统先补交易日历，再按 D0～D6、检测前置 20 个
开市日、质量/上下文正式依赖和 2 个安全开市日计算当前 V1 所需窗口。最终
计划、各域 required_start/required_end、expected/covered/missing 和明细
均写入覆盖账本。所有读取和下载以 `data_cutoff` 为上界；未来交易日和未来
cutoff 均拒绝。

全市场 universe 直接来自目标时点的 GM 证券枚举，而不是复用本地已有的
少量证券主数据。快照保存 eligible/excluded、排除原因和 source_cutoff。
ST/退市整理、目标日未上市或已退市、目标日停牌及非普通 A 股会被排除。
证券主数据、状态和日线只对 eligible universe 增量补缺。分钟线只对仍在
D1～D5/D6 观察链上的正式首板事件按需读取，不做全市场分钟下载。

`coverage_complete=true` 仅在 full_market 且日历、universe、状态、日线、
检测、质量、回调观察、上下文、候选以及尾盘阶段所需分钟数据全部通过硬
覆盖检查时写入。`partial` 可显示已有结果，但 0 条不得解释为“全市场没有
候选”。候选生成会显式传入冻结后的 eligible symbols，防止数据库中历史
少量 detect run 被误判为全市场检测完成。

### API

```text
POST /api/first-limit/pipeline-jobs
GET  /api/first-limit/pipeline-jobs
GET  /api/first-limit/pipeline-jobs/latest
GET  /api/first-limit/pipeline-jobs/{job_id}
GET  /api/first-limit/pipeline-jobs/{job_id}/steps
GET  /api/first-limit/pipeline-jobs/{job_id}/coverage
GET  /api/first-limit/pipeline-jobs/{job_id}/failures
POST /api/first-limit/pipeline-jobs/{job_id}/retry
```

页面两个运行按钮调用上述异步 API，展示实际步骤、真实分母可用时的进度、
heartbeat、覆盖结论和重试入口；刷新页面会从本地保存的 job id 或 latest
接口恢复。股票输入框只过滤已保存结果，不改变运行范围。

### CLI

```powershell
python -m backend.strategy.first_limit.run_one_click_pipeline `
  --trade-date 2026-07-21 `
  --stage close_confirmed `
  --as-of 2026-07-21T15:00:00+08:00 `
  --data-cutoff 2026-07-21T15:00:00+08:00 `
  --wait --report markdown
```

支持 `--resume-job-id`、`--report json|markdown`、`--wait`，以及仅供测试/
运维的 `--symbols`。等待模式退出码：完整成功 `0`，partial `1`，失败或
参数错误 `2`；非等待模式成功创建/复用后台任务后返回 `0`。

### 已知限制与真实验收

- 本地 V1 仅保证单机进程和 SQLite 的持久恢复，不声称分布式可靠队列。
- GM 全市场枚举参数和历史状态字段已隔离在窄 adapter 中；自动化验收使用
  fake provider，未使用真实 token、未联网、未写用户正式数据库。
- 行业历史映射能力仍遵守 PR6.6 的既有限制；缺失时评分可以
  indeterminate/partial，但只要正式计算记录已落库，数据域覆盖与评分结论
  分开审计。
- 真实 GM 验收仍须按第 13 节，在用户配置凭据并明确授权写本地正式库后
  执行；本 PR 不把离线固定样本结果表述为真实全市场验收。
