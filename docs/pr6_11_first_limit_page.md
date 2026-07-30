# PR6.11：首板回调页面与真实使用闭环

## 1. 背景与目标

PR6.9 已实现每日候选生成闭环，PR6.10 已提供候选、证据、运行状态、运行明细、尾盘/收盘对比及手动运行 API。

本 PR 的目标是新增一个本地可直接使用的“首板回调”页面，让用户能够：

1. 查看指定交易日的尾盘预警和收盘确认；
2. 手动生成尾盘预警或执行收盘确认；
3. 清楚区分 S/A/B 候选、等待确认、淘汰、过期和不可确定；
4. 查看每只股票的规则证据、失败原因和数据来源时间；
5. 查看尾盘到收盘的升级、降级、新增、淘汰及不变；
6. 查看 run 状态及成功、失败、未决明细；
7. 页面刷新后恢复最近一次可用结果。

完成后停在“可选择性暂存并提交，但尚未提交”状态。不得 commit、push 或创建 PR。

---

## 2. 范围边界

### 2.1 本 PR 必须包含

- 新增首板回调页面及现有首页/导航入口；
- 交易日选择；
- 阶段切换：`tail_preview` / `close_confirmed`；
- 手动运行按钮；
- S/A/B 候选及其他生命周期状态展示；
- 候选详情与完整 evidence 展示；
- 尾盘—收盘变化对比；
- run 概览和 run item 失败/未决信息；
- 分页、排序和必要筛选；
- 同步长请求期间的运行状态；
- 重复点击保护；
- 页面刷新后的状态恢复；
- 空数据、缺失数据、API 错误和网络错误提示；
- 前端单元/集成测试及必要的后端静态资源路由测试；
- 页面使用说明文档。

### 2.2 本 PR 明确不包含

- 新策略规则、阈值或等级计算；
- 在 JavaScript 中重新计算 S/A/B；
- 自动刷新全市场日线或分钟行情；
- GM 实时数据采集改造；
- 后台任务队列或 WebSocket；
- 定时运行；
- 邮件、微信、Slack 或其他通知；
- 用户认证和公网部署；
- PR6.8 历史分钟复核分析页面；
- 自动下单、仓位建议或收益承诺；
- 移动 App。

页面只能展示和编排 PR6.10 的正式 API。不要直接查询 SQLite，不要复制 PR6.9 规则。

---

## 3. 开始前仓库审计

实现前先检查并记录：

1. 当前前端是原生 HTML/CSS/JavaScript 还是已有框架；
2. 现有页面目录、公共导航、公共样式、API 请求封装及测试方式；
3. `app.py` 的静态页面路由和项目启动方式；
4. PR6.10 OpenAPI 中七个接口的实际请求与响应字段；
5. candidates、runs、items、preview-comparison 的分页格式；
6. 现有日期、时区、symbol 和枚举的序列化格式；
7. 现有页面是否使用相对 `/api/...` 路径；
8. 仓库中是否已有可复用的加载、错误、表格、抽屉或模态框组件。

以仓库真实结构为准，不要凭本文臆造响应字段。如果 PR6.10 的返回字段不足以完成页面，应先确认是否属于纯展示缺口；只允许做最小、向后兼容的 API 补充，并补齐测试和文档。不得借机修改策略语义。

---

## 4. 页面入口与总体布局

建议页面名：

```text
first-limit.html
```

导航名称：

```text
首板回调
```

页面从上到下分为五个区域：

1. 页面说明与数据时间；
2. 运行控制区；
3. 当日概览；
4. 候选列表与详情；
5. 尾盘—收盘变化及运行明细。

不要为了视觉效果引入新的大型前端框架。延续项目现有前端技术和样式；如果当前是原生页面，就继续使用原生 HTML/CSS/JS。

---

## 5. 运行控制区

### 5.1 控件

必须提供：

- 交易日选择器；
- 阶段切换；
- `生成尾盘预警` 按钮；
- `执行收盘确认` 按钮；
- `刷新结果` 按钮；
- 最近运行状态；
- 最近更新时间；
- 可选 symbol 筛选输入，仅用于查询展示；除非现有交互明确要求，不要默认把它传给正式全市场运行。

按钮语义必须清晰：

| 按钮 | stage | 默认时点 |
|---|---|---|
| 生成尾盘预警 | `tail_preview` | 中国市场当日 14:55 |
| 执行收盘确认 | `close_confirmed` | 中国市场当日 15:00 |

前端不得用浏览器本地时区拼接无时区时间。构造请求时必须显式使用中国市场 `+08:00`，并符合 PR6.10 的时间约束。

如果 API 已允许省略默认 `as_of`/`data_cutoff`，优先只发送必要字段，让服务端统一默认值；不要在前端复制复杂时间校验。

### 5.2 页面加载行为

首次打开时：

1. 恢复用户上次选择的交易日和阶段；
2. 如果没有保存值，选择数据库/API 可查询的最近交易日；若 API 不提供交易日列表，则使用今天作为查询起点，但不得自动触发运行；
3. 查询对应 run 和候选；
4. 没有 run 时展示明确空状态；
5. 不得因为打开页面就自动执行 POST。

允许用 `localStorage` 保存纯 UI 状态：

```text
trade_date
stage
grade_filter
lifecycle_filter
sort
page_size
selected_tab
```

不得把候选结果、运行状态或业务结论长期缓存为事实。页面刷新后必须重新从 API 获取。

### 5.3 同步运行体验

PR6.10 V1 的 POST 是同步请求，可能持续较长时间。调用期间：

- 当前运行按钮显示“运行中…”；
- 禁用两个运行按钮，避免同一页面重复提交；
- 保留页面已有结果，不清空表格；
- 显示“请求正在执行，请勿关闭页面”的非阻塞提示；
- 不用虚假的百分比进度条；
- 请求完成后根据返回的 run/poll 信息重新查询正式结果；
- `reused=true` 时提示“已复用相同参数的运行”，不能显示成新建成功；
- 请求失败时恢复按钮，并保留旧结果；
- 使用 `AbortController` 只管理页面生命周期或用户明确取消查询；不要把前端超时误报为后端 run 失败；
- 如果项目已有统一请求超时，手动运行接口应允许合理的长超时或不设置短超时。

同一标签页用内存状态防重复点击。多个标签页或并发请求的最终防重仍依赖 PR6.10 数据库原子认领，前端不能假定自己是唯一调用方。

### 5.4 卡死与 `running` 状态

如果查询到旧 run 仍为 `running`：

- 如实显示 `running`；
- 提供“刷新状态”；
- 不要在页面端擅自改为 failed；
- 不提供“强制重跑”按钮；
- 可提示“若进程曾被强制终止，需通过运维方式恢复”。

---

## 6. 当日概览

概览卡片至少展示：

- 交易日；
- 当前阶段；
- run ID；
- run 状态；
- S / A / B 数量；
- `watching`；
- `eligible`；
- `pending_close_confirmation`；
- `confirmed`；
- `eliminated`；
- `expired`；
- `indeterminate`；
- item success / failed / pending 或 API 实际提供的等价统计；
- `as_of`；
- `data_cutoff`；
- 参数/策略版本（可折叠展示）。

统计必须来自 API 返回或对当前完整查询结果的无歧义汇总。如果候选 API 是服务端分页，禁止只统计当前页并冒充全量统计。优先使用 run 的正式 totals；没有正式 totals 时，应显示“当前页”或提出最小 API 补充。

`partial`、`failed`、`indeterminate` 不得使用与 success 相同的成功色。

页面顶部固定提示：

```text
尾盘预警不是最终确认；以收盘确认结果为准。
```

---

## 7. 候选列表

### 7.1 默认视图

默认优先展示当前阶段仍可行动或仍需关注的项目：

```text
confirmed
eligible
pending_close_confirmation
watching
indeterminate
```

淘汰和过期不能丢失，应通过状态筛选查看。也可设置“全部”选项。

### 7.2 列

按 API 实际字段实现，目标至少包括：

- 等级 S/A/B；
- 股票代码；
- 股票名称（若 API/现有证券主数据已提供）；
- 行业（若已有）；
- 观察日 D1～D6；
- 生命周期；
- 当前阶段；
- 首板日期；
- 核心结论/主要原因；
- 尾盘—收盘变化类型；
- 数据/评价时间；
- 详情操作。

如果股票名称或行业未由 API 提供，不允许前端硬编码映射。可做最小 join/API 补充，或暂时只显示 symbol 并记录限制。

### 7.3 等级与状态

- 等级直接显示 API 的最终 `grade`；
- `grade=null` 必须显示 `—`，不能前端推断；
- S/A/B 使用明显但不过度刺激的视觉区分；
- 状态文字必须有中文展示，但代码值可在详情中保留；
- `unknown` evidence 不能显示为通过；
- `indeterminate` 不能显示为淘汰；
- `pending_close_confirmation` 必须清楚标注“等待收盘确认”。

### 7.4 筛选、排序和分页

筛选至少支持：

- grade：S/A/B/无等级；
- lifecycle；
- symbol；
- change type（在对比视图中）；
- run/item 状态（运行明细中）。

排序和分页必须调用 PR6.10 的白名单查询参数，不在前端拉取全量后伪分页。切换交易日、阶段或筛选条件时重置到第一页。

请求竞态必须处理：快速切换筛选时，旧请求晚返回不能覆盖新选择的结果。可使用请求序号或 `AbortController`。

---

## 8. 候选详情与规则证据

点击候选后打开抽屉、模态框或页面内详情区。详情通过：

```text
GET /api/first-limit/candidates/{candidate_id}
```

获取，不能仅依赖列表行数据。

详情至少包含：

- candidate ID / event ID；
- symbol；
- 交易日和阶段；
- 生命周期；
- grade；
- 观察日；
- 首板事件基础信息；
- 主要淘汰/等待/不可确定原因；
- 完整 evidence；
- 来源日期/时间；
- strategy/detection/pullback/context 版本（API 有则显示）；
- preview comparison（存在则显示）。

Evidence 按 `ordinal` 稳定排序，表格列：

| 规则 | 结果 | 实际值 | 阈值 | 单位 | 原因 | 来源时间 |
|---|---|---:|---:|---|---|---|

显示规则：

- `pass`：通过；
- `fail`：未通过；
- `unknown`：数据不足/无法确定；
- 优先展示 `display_text`；
- 同时保留 `reason_code` 供诊断；
- `0`、`false` 与空值必须严格区分；
- 时间按中国市场语义显示，并保留时区信息或明确标注“北京时间”；
- 详情请求失败不影响主列表，允许单独重试。

不要根据 actual/threshold 在 JavaScript 中重新判断 pass/fail。

---

## 9. 尾盘—收盘变化对比

使用：

```text
GET /api/first-limit/preview-comparison
```

建立独立的“尾盘→收盘变化”标签页或区域。

必须支持以下正式类型：

```text
unchanged
upgraded
downgraded
newly_qualified
eliminated
preview_missing
```

展示：

- symbol；
- 尾盘 grade / lifecycle；
- 收盘 grade / lifecycle；
- change type；
- 变化原因或收盘阶段关键 evidence；
- 详情入口。

中文含义：

| change_type | 展示 |
|---|---|
| unchanged | 不变 |
| upgraded | 升级 |
| downgraded | 降级 |
| newly_qualified | 收盘新增 |
| eliminated | 收盘淘汰 |
| preview_missing | 缺少尾盘快照 |

不得把 `preview_missing` 解释为“收盘新增”。尾盘 run 不存在与某只股票缺少尾盘 snapshot 也应尽量区分；以 API 正式语义为准。

如果指定日期缺少尾盘或收盘 run，应展示缺失哪一侧以及如何生成，不得生成空的“无变化”结论。

---

## 10. 运行记录与 item 明细

提供“运行记录”区域，使用：

```text
GET /api/first-limit/runs
GET /api/first-limit/runs/{run_id}
GET /api/first-limit/runs/{run_id}/items
```

运行列表至少显示：

- run ID；
- trade date；
- stage；
- status；
- 创建/开始/完成时间；
- success/failed/pending 等统计；
- 是否可查看详情。

run 详情展示参数和错误摘要，但不要展示敏感信息。item 明细至少可筛选：

- failed；
- pending/unresolved（依实际枚举）；
- success。

failed item 应显示 symbol/event、稳定错误码和脱敏错误说明。前端不要展示 traceback、SQL、数据库路径或凭据；即使后端意外返回，也应只渲染契约允许的字段。

---

## 11. 空状态和错误状态

必须区分：

1. 指定日期没有 run；
2. 有 run，但没有候选；
3. 有候选，但当前筛选无结果；
4. run 为 `running`；
5. run 为 `partial`；
6. run 为 `failed`；
7. API 业务校验错误；
8. 网络/服务不可达；
9. 请求被新查询取代；
10. 详情单独加载失败。

建议提示：

- 无 run：“该交易日尚未生成此阶段结果。”
- 无候选：“运行已完成，但没有符合当前阶段条件的候选。”
- 筛选为空：“当前筛选条件没有结果。”
- partial：“部分事件处理失败，请查看运行明细。”
- failed：“本次运行失败，未形成可用结果，请查看错误摘要。”

错误提示优先使用 PR6.10 的稳定错误结构。不得直接 `JSON.stringify` 整个响应给普通用户，也不得吞掉 error code。

---

## 12. API 使用原则

仅使用 PR6.10 已有接口：

```text
GET  /api/first-limit/candidates
GET  /api/first-limit/candidates/{candidate_id}
GET  /api/first-limit/runs
GET  /api/first-limit/runs/{run_id}
GET  /api/first-limit/runs/{run_id}/items
GET  /api/first-limit/preview-comparison
POST /api/first-limit/runs
```

要求：

- 所有请求经过现有公共 API 封装；没有封装时建立轻量、可测试的统一函数；
- URL 查询参数使用 `URLSearchParams` 等安全方式；
- 不拼接 SQL 或本地文件路径；
- POST body 只包含 Pydantic 模型允许字段；
- 不发送 `force`、`resume`、`dry_run` 等禁止字段；
- 对 4xx 与 5xx 分开处理；
- 保留后端 error code；
- 不依赖未记录的对象字段；
- API 契约字段变更必须同步 OpenAPI/测试/文档。

---

## 13. 可访问性和基础视觉要求

- 表单控件有 label；
- 按钮有明确文本，不能只用图标；
- 状态不能只依赖颜色；
- 键盘可打开和关闭详情；
- 模态框打开后合理管理焦点，Esc 可关闭；
- 加载状态使用 `aria-live` 或等价可访问提示；
- 表格在窄屏可横向滚动；
- 数字、代码、中文原因有清晰层次；
- 避免使用“强烈买入”等误导性文案。

页面是研究与候选确认工具，不是自动交易终端。

---

## 14. 推荐的前端状态模型

如果当前为原生 JavaScript，可使用单一页面状态对象，不引入框架：

```javascript
{
  tradeDate,
  stage,
  candidateQuery,
  candidates,
  candidatePage,
  selectedCandidate,
  comparisonQuery,
  comparisons,
  runs,
  selectedRun,
  runItems,
  activeRequestIds,
  isRunning,
  errors
}
```

要求：

- 查询条件和结果分离；
- 候选、详情、对比、run 分别管理 loading/error；
- 渲染函数不修改业务数据；
- 服务端枚举到中文文案集中映射；
- HTML 输出避免不安全的 `innerHTML`；若项目必须使用，所有外部字符串必须转义；
- 不基于 UI 文本反向推导业务枚举。

---

## 15. 测试要求

先沿用仓库现有前端测试体系。若现有页面没有自动化前端测试，至少建立适合当前原生技术栈的轻量测试，不要为了本 PR 引入沉重框架。

### 15.1 必测场景

1. 页面入口和静态资源可访问；
2. 首次加载不会自动 POST；
3. 恢复上次交易日和阶段；
4. 无 run 空状态；
5. 成功加载候选；
6. S/A/B 和 null grade 正确显示；
7. lifecycle 中文映射；
8. 服务端分页和排序参数正确；
9. 快速切换时旧响应不会覆盖新响应；
10. 候选详情独立加载；
11. evidence 的 pass/fail/unknown 及 `0`/空值正确；
12. 生成尾盘预警的请求体；
13. 执行收盘确认的请求体；
14. 运行期间按钮禁用且旧结果保留；
15. `reused=true` 的提示；
16. POST 失败后恢复按钮；
17. 运行完成后重新查询；
18. 页面刷新后从 API 恢复，而非使用旧业务缓存；
19. 六种 comparison 类型；
20. 缺少一侧 run 的对比空状态；
21. run 和 item 明细；
22. partial、failed、running 状态；
23. API 稳定错误结构；
24. 用户输入和 API 文本不造成 HTML 注入；
25. 无短超时误杀长 POST；
26. 普通页面没有 force/resume/dry-run 控件或字段。

### 15.2 后端回归

至少执行：

```bash
python -m pytest tests/test_first_limit_api.py -q
python -m pytest tests/strategy -q
python -m pytest -q
```

以及项目现有前端测试命令、静态检查和格式检查。

如果修改了 `app.py` 静态路由，增加路由测试，确认：

- 页面返回 200；
- content type 正确；
- API 404 不会被错误吞成 HTML 页面；
- 现有页面不受影响。

---

## 16. 人工验收

在固定 API fixture 或临时数据库中至少准备：

- S、A、B 各一条；
- `pending_close_confirmation`；
- `eliminated`；
- `indeterminate`；
- pass/fail/unknown evidence；
- 六种 preview comparison；
- success、partial、failed、running run；
- failed item；
- 空交易日。

人工检查：

1. 初次打开；
2. 切换交易日和阶段；
3. 打开候选详情；
4. 使用等级/状态筛选；
5. 切换排序和分页；
6. 查看对比；
7. 查看运行失败明细；
8. 模拟慢 POST；
9. 连续点击；
10. 刷新页面恢复；
11. API 断开；
12. 窄屏表格和键盘操作。

如环境允许，可用本地开发服务器配合浏览器检查；不得联网获取真实行情，也不得操作真实数据库。

---

## 17. 文档

新增：

```text
docs/pr6_11_first_limit_page.md
```

记录：

- 页面入口与启动方式；
- 页面区域；
- 两个运行按钮的真实语义；
- 尾盘预警与收盘确认区别；
- API 映射；
- 页面恢复机制；
- loading/empty/error 状态；
- 已知限制；
- 测试命令和结果；
- 真实环境待验证项。

说明当前 POST 是同步 V1；`poll_url` 仅为未来异步兼容，不得宣称已有后台队列。

---

## 18. 建议文件范围

以仓库审计结果为准，预计包括：

```text
frontend/first-limit.html                  # 或项目实际页面目录
frontend/js/first-limit.js                 # 或现有命名风格
frontend/css/first-limit.css               # 能复用公共样式则减少新增
frontend/index.html                        # 导航入口（若需要）
backend/api/app.py                         # 仅在静态路由确有需要时
docs/pr6_11_first_limit_page.md
tests/...                                  # 页面/API路由/前端测试
```

不要机械创建上述路径；先按仓库现有结构落位。

---

## 19. 完成标准

PR6.11 只有同时满足以下条件才算完成：

- 页面可以查询并展示 PR6.10 的真实返回；
- 可以手动运行两个阶段；
- 没有前端策略复制；
- 尾盘预警与收盘确认明确区分；
- 候选证据和原因可追溯；
- 六种变化类型完整展示；
- run 和失败 item 可诊断；
- 同步长请求体验正确；
- 重复点击受控；
- 刷新后能恢复；
- 空状态和错误状态完整；
- 分页排序使用服务端契约；
- 测试通过；
- `git diff --check` 通过；
- 未操作真实数据库；
- 未 commit、push 或创建 PR。

---

## 20. 最终交付报告格式

完成后报告：

1. 实现范围；
2. 页面入口与启动方式；
3. 页面状态和交互；
4. API 调用映射；
5. 同步运行及重复点击处理；
6. 详情、证据和变化对比；
7. 空状态与错误处理；
8. 测试命令、数量、耗时和退出码；
9. 人工验收结果；
10. 修改/新增文件；
11. `git diff --stat`；
12. `git status --short`；
13. 尚待真实环境验证事项。

如果发现 PR6.10 API 契约存在阻塞页面的缺口，先给出证据，并只做最小兼容修改；不得自行扩大到 PR6.12、自动调度或新策略开发。

---

## 21. 已实现页面使用说明

### 21.1 入口与启动

页面入口：

```text
http://127.0.0.1:8000/first-limit
```

沿用项目现有启动方式：

```bash
uvicorn backend.api.app:app --reload
```

首页、预期差、板块雷达和数据源状态页面均增加“首板回调”导航。页面继续使用原生 HTML、CSS 和 JavaScript，没有引入新的前端框架或构建系统。

### 21.2 页面区域

页面包含：

1. 策略边界提示和数据时间；
2. 交易日、阶段、展示 symbol 及三个操作按钮；
3. 基于完整 run 详情的当日概览；
4. 服务端筛选、排序和分页的候选列表；
5. 独立加载的候选详情和完整 evidence；
6. 六种尾盘—收盘变化；
7. run 账本、run 详情和分页 item 明细。

股票名称和行业当前未由 PR6.10 DTO 提供，页面不硬编码或猜测，只展示正式 symbol。候选 `base_grade` 在 PR6.9 未独立持久化，继续显示正式最终 `grade`，空等级显示 `—`。

### 21.3 两个运行按钮

“生成尾盘预警”发送：

```json
{"trade_date": "YYYY-MM-DD", "stage": "tail_preview"}
```

“执行收盘确认”发送：

```json
{"trade_date": "YYYY-MM-DD", "stage": "close_confirmed"}
```

页面不发送 `as_of` 或 `data_cutoff`，由 PR6.10 统一使用北京时间 14:55 和 15:00 默认值。也不发送 symbol、`force`、`resume`、`dry_run`、数据库路径或凭据。页面 symbol 输入只影响 GET 查询。

PR6.10 当前为同步 V1。POST 期间两个运行按钮及刷新按钮禁用，保留旧候选和统计，显示“请求正在执行，请勿关闭页面”，且不设置短超时或虚假进度。完成后重新查询所有正式 API；`reused=true` 明确显示为复用，不声称新建运行。`poll_url` 仅保留为未来异步兼容字段。

### 21.4 API 映射

| 页面功能 | API |
| --- | --- |
| 候选列表 | `GET /api/first-limit/candidates` |
| 候选详情与证据 | `GET /api/first-limit/candidates/{candidate_id}` |
| 运行列表 | `GET /api/first-limit/runs` |
| 概览与运行详情 | `GET /api/first-limit/runs/{run_id}` |
| 运行 item | `GET /api/first-limit/runs/{run_id}/items` |
| 尾盘—收盘变化 | `GET /api/first-limit/preview-comparison` |
| 手动运行 | `POST /api/first-limit/runs` |

查询参数使用 `URLSearchParams`。候选、变化、run 和 item 均使用服务端 `limit/offset`；候选排序只发送 PR6.10 白名单字段。快速查询通过递增 request id 隔离，旧响应晚返回不会覆盖新结果。

PR6.11 对 PR6.10 只增加一项向后兼容展示筛选：

```text
grade=none
```

它在 repository 中参数化转换为 `candidate_grade IS NULL`，用于服务端筛选无等级候选，不新增等级、不改变候选规则。

### 21.5 状态、证据和安全展示

生命周期、run/item 状态、evidence 结果及六种 change type 使用集中枚举映射。页面不根据分数、实际值或阈值重新计算等级或 pass/fail。

Evidence 严格区分：

- `0` 显示为 `0`；
- `false` 显示为 `false`；
- 空字符串显示为 `""`；
- `null` 显示为 `—`；
- `unknown` 显示为“数据不足/无法确定”，不显示为通过。

所有 API 文本通过 DOM `textContent` 写入，不将 symbol、原因、错误或 evidence 拼入 `innerHTML`。失败 item 只渲染契约允许的 event、symbol、错误码和脱敏说明。

候选详情打开后焦点移到关闭按钮，可用 Escape 关闭。表单有 label，状态同时包含文字，表格窄屏可横向滚动。

### 21.6 恢复、loading、空状态和错误

`localStorage` 仅保存以下 UI 选择：

- trade date；
- stage；
- grade/lifecycle；
- sort/order；
- page size；
- 当前 tab。

候选、run、item、comparison 和业务状态不写入长期缓存。刷新页面后始终重新调用 API，且首次打开不会 POST。

页面区分无 run、run 无候选、筛选为空、缺少收盘 run、running、partial、failed、网络失败、4xx 业务校验、5xx 服务运行错误和详情独立失败。错误消息保留稳定 error code，但不展示完整响应、堆栈、SQL、路径或凭据。

### 21.7 已知限制与真实环境待验证

- 同步 POST 在真实全市场数据量下的浏览器等待时长仍需验证；
- 真实 GM 缓存缺失时的提示和运行耗时仍需验证；
- 当前没有认证，手动运行仅适用于本地可信环境；
- 进程异常终止留下的 `running` run 只能如实显示，需要运维恢复；
- 尚未用真实浏览器和大量候选验证低性能设备上的表格滚动；
- 当前没有股票名称和行业展示字段；
- 没有后台队列、WebSocket、定时运行、通知、页面端 force 或自动交易。

### 21.8 本地验收结果

固定前端 fixture 覆盖 S/A/B/空等级、等待确认、淘汰、无法确定、pass/fail/unknown evidence、六种变化、success/partial/failed/running run、failed item 和空交易日。测试还模拟慢 POST、连续点击、稳定错误、旧响应晚返回、页面状态恢复、HTML 注入文本及键盘关闭详情。

执行结果：

```text
node --test tests/js/market_pulse_frontend.test.js tests/js/first_limit_frontend.test.js
2 passed

python -m pytest tests/test_first_limit_api.py tests/test_first_limit_page.py -q
9 passed

python -m pytest tests/strategy -q
144 passed

python -m pytest -q
349 passed
```

静态路由测试确认 `/first-limit`、JavaScript、CSS 和既有导航可访问，且 API 404 保持 JSON，不会被根静态挂载吞成 HTML。
