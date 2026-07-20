# AuroraAI 预期差功能实现指导（V1）

> 目标：在现有 AuroraAI 项目中增加“预期差”页面，覆盖 A 股与港股，通过富途 OpenAPI 获取晨星公允价值、分析师平均目标价和当前价格，计算预期差并支持排序、搜索、筛选及每日自动更新。

## 1. Codex 执行指令

请先完整阅读本文档以及项目中的 `README.md`、依赖文件和现有前后端代码，再进行修改。优先复用项目已有技术栈、目录结构、数据库封装、导航栏和页面样式，不要为了该功能重写现有项目。

实施顺序：

1. 检查现有项目结构和启动方式。
2. 创建独立分支，例如 `feature/expectation-gap`。
3. 先用 5～20 只样本股票跑通 OpenD、数据库、API 和页面。
4. 完成单元测试及接口测试。
5. 再开放全市场首次初始化命令。
6. 更新项目 `README.md`，写明安装、运行和刷新方式。

不得把富途账号、密码、数据库文件、日志或其他密钥提交到 Git。

---

## 2. V1 功能范围

### 2.1 页面功能

新增导航入口和页面：

```text
/expectation-gap
```

页面必须支持：

- 展示 A 股与港股上市公司。
- 市场筛选：全部、A 股、港股。
- 输入股票名称或代码进行模糊搜索。
- 按“晨星预期差”升序或降序排列。
- 按“分析师预期差”升序或降序排列。
- 可按当前价、目标价、分析师人数和更新时间排序。
- 分页显示，默认每页 50 条，可选 20、50、100。
- 显示最后一次成功刷新时间和刷新状态。
- 缺少评级数据时显示 `—`，排序时放到有效数据之后。
- 默认只展示至少拥有一个目标价的股票。
- 提供“包含无评级股票”开关。
- 提供“最低分析师人数”筛选，默认值为 3。

### 2.2 暂不实现

- 云端部署和远程数据库。
- 用户注册、权限系统。
- 自动交易、下单和持仓管理。
- 抓取富途网页或 App 私有接口。
- 晨星研报全文展示。
- 历史趋势图（数据库结构应为以后扩展保留空间）。

---

## 3. 数据定义与计算规则

### 3.1 富途字段

| 业务字段 | 富途接口/来源 | 富途返回字段 |
| --- | --- | --- |
| 当前价格 | 批量快照接口 | `last_price` |
| 晨星公允价值 | `get_research_morningstar_report(code)` | `fair_value` |
| 晨星星级 | 同上 | `star_rating` |
| 晨星评级类型 | 同上 | `rating_type` |
| 晨星数据日期 | 同上 | `star_update_time_str`、公允价值内容更新时间 |
| 分析师平均目标价 | `get_research_analyst_consensus(code)` | `average` |
| 分析师最高目标价 | 同上 | `highest` |
| 分析师最低目标价 | 同上 | `lowest` |
| 分析师人数 | 同上 | `total` |
| 综合评级 | 同上 | `rating` |
| 分析师数据日期 | 同上 | `update_time_str` |

富途代码格式：

```text
上交所：SH.600519
深交所：SZ.000001
港股：HK.00700
```

只处理普通股票和需要纳入的 REIT。排除已退市证券、期权、期货、窝轮、牛熊证和其他衍生品。ETF 是否排除应沿用现有项目口径；V1 默认排除 ETF。

### 3.2 预期差公式

```text
晨星预期差(%) = (晨星公允价值 / 当前价 - 1) × 100
分析师预期差(%) = (分析师平均目标价 / 当前价 - 1) × 100
```

示例：

```text
当前价 = 80
晨星公允价值 = 100
晨星预期差 = (100 / 80 - 1) × 100 = 25%
```

规则：

- 当前价为空、为 0 或负数时，不计算预期差。
- 目标价为空、为 0 或负数时，对应预期差为空。
- 数据库存储原始数值，不存带 `%` 的字符串。
- 后端计算并返回预期差，保留足够精度；前端显示两位小数。
- 港股当前价与目标价均按港元比较，A 股均按人民币比较，不跨币种换算。
- 页面需要显示目标价数据日期，防止旧目标价被误认为最新数据。

---

## 4. 推荐架构

保持现有 AuroraAI 架构。如果当前后端是 FastAPI，推荐结构如下；目录名称可根据项目现状调整：

```text
backend/
  app/
    api/
      expectation_gap.py
    models/
      stock_expectation.py
    schemas/
      expectation_gap.py
    services/
      futu_client.py
      expectation_gap_service.py
    jobs/
      refresh_expectation_gap.py
    db/
      database.py
      migrations/
  scripts/
    init_expectation_gap.py
    refresh_expectation_gap.py
  data/
    aurora.db
frontend/
  src/
    pages/ExpectationGap/
    services/expectationGapApi.*
```

职责划分：

- `futu_client`：只负责连接 OpenD、调用接口、限频、重试及返回标准化数据。
- `expectation_gap_service`：负责业务规则、数据更新和预期差计算。
- `refresh_expectation_gap`：负责批量任务、断点续传和日志。
- API 层：只负责参数校验和返回结果。
- 前端：只负责查询条件、表格、分页和展示。

禁止在前端直接连接 OpenD。

---

## 5. OpenD 与环境配置

### 5.1 本机准备

1. 安装并启动富途 OpenD 可视化程序。
2. 使用富途账号登录并完成首次协议确认。
3. 确认 OpenD 监听地址，默认是 `127.0.0.1:11111`。
4. 安装 Python SDK：

```bash
pip install futu-api
```

### 5.2 环境变量

在 `.env.example` 增加：

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
EXPECTATION_DB_URL=sqlite:///./data/aurora.db
EXPECTATION_PRICE_TTL_HOURS=24
EXPECTATION_ANALYST_TTL_HOURS=24
EXPECTATION_MORNINGSTAR_TTL_HOURS=168
FUTU_REQUESTS_PER_30_SECONDS=28
FUTU_MAX_RETRIES=3
```

实际 `.env` 加入 `.gitignore`。不要在项目中保存富途账号和密码；登录由 OpenD 自己完成。

应用启动时不能因为 OpenD 未运行而导致整个网站启动失败。只有采集任务调用富途数据时才检查连接，并返回清晰错误：

```text
无法连接富途 OpenD，请确认 OpenD 已启动、已登录且监听 127.0.0.1:11111。
```

---

## 6. SQLite 数据库设计

V1 使用 SQLite，不要求安装数据库服务器。数据库文件必须加入 `.gitignore`。

### 6.1 `stocks` 股票基础表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `futu_code` | VARCHAR UNIQUE | 例如 `HK.00700` |
| `symbol` | VARCHAR | 显示代码，例如 `00700` |
| `name` | VARCHAR | 股票名称 |
| `market` | VARCHAR | `A` 或 `HK` |
| `exchange` | VARCHAR | `SH`、`SZ`、`HK` |
| `security_type` | VARCHAR | 证券类型 |
| `is_active` | BOOLEAN | 是否正常上市 |
| `listing_date` | DATE NULL | 上市日期 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

索引：

```text
UNIQUE(futu_code)
INDEX(market)
INDEX(symbol)
INDEX(name)
```

### 6.2 `stock_expectations` 最新数据表

每只股票只保留一条最新状态，便于页面快速查询。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `stock_id` | INTEGER PK/FK | 对应 `stocks.id` |
| `last_price` | DECIMAL NULL | 当前/最新收盘价 |
| `price_time` | DATETIME NULL | 价格对应时间 |
| `morningstar_fair_value` | DECIMAL NULL | 晨星公允价值 |
| `morningstar_star_rating` | INTEGER NULL | 1～5 星 |
| `morningstar_rating_type` | INTEGER NULL | 定量/定性类型 |
| `morningstar_data_date` | DATE NULL | 晨星数据日期 |
| `morningstar_checked_at` | DATETIME NULL | 最近一次检查时间 |
| `analyst_average_target` | DECIMAL NULL | 分析师平均目标价 |
| `analyst_high_target` | DECIMAL NULL | 最高目标价 |
| `analyst_low_target` | DECIMAL NULL | 最低目标价 |
| `analyst_count` | INTEGER NULL | 近3个月分析师人数 |
| `analyst_rating` | INTEGER NULL | 综合评级 |
| `analyst_data_date` | DATE NULL | 数据日期 |
| `analyst_checked_at` | DATETIME NULL | 最近一次检查时间 |
| `last_success_at` | DATETIME NULL | 最近成功获取任一数据时间 |
| `last_error` | TEXT NULL | 最近错误摘要 |
| `consecutive_failures` | INTEGER | 连续失败次数，默认0 |
| `updated_at` | DATETIME | 记录更新时间 |

预期差可以在查询时计算，不强制落库。若现有 ORM 对排序实现困难，可以增加两个可空字段：

```text
morningstar_gap_pct
analyst_gap_pct
```

每次价格或目标价更新后同步重算。

### 6.3 `refresh_runs` 刷新任务表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 任务ID |
| `job_type` | VARCHAR | `full`、`daily`、`sample` |
| `status` | VARCHAR | `running`、`success`、`partial`、`failed` |
| `started_at` | DATETIME | 开始时间 |
| `finished_at` | DATETIME NULL | 完成时间 |
| `total_count` | INTEGER | 总数 |
| `processed_count` | INTEGER | 已处理数 |
| `success_count` | INTEGER | 成功数 |
| `failure_count` | INTEGER | 失败数 |
| `last_code` | VARCHAR NULL | 最后处理代码，用于诊断 |
| `error_message` | TEXT NULL | 任务级错误 |

### 6.4 可选历史表

V1 可以暂不启用。如果实现成本很低，可增加 `expectation_history`，只在目标价发生变化时插入一条记录，而不是每天重复保存相同数据。

---

## 7. 富途采集层要求

### 7.1 客户端生命周期

- 一个批次尽量复用同一个 `OpenQuoteContext`。
- 使用 `try/finally` 确保任务结束时调用 `close()`。
- Web 请求不能为每只股票创建一个 OpenD 连接。
- 采集函数不得返回原始 DataFrame 给 API 层，应转换为明确的数据对象或字典。

### 7.2 限频

晨星和分析师接口官方限制均为每30秒最多30次。程序配置为最多28次，留出余量。

要求：

- 晨星接口和分析师接口分别维护限频器；若无法确认是否共享额度，则采用更保守的全局限频。
- 收到频率错误时采用指数退避，例如 2、5、10 秒。
- 最多重试3次。
- 业务无数据不是系统错误，不应反复重试。
- 网络错误、OpenD断开和限频错误要区分记录。

### 7.3 标准化结果

为每个接口定义三种状态：

```text
success：成功且存在有效数据
no_data：接口成功但该股票没有覆盖
error：连接、限频或服务错误
```

`no_data` 也必须更新 `checked_at`，否则任务会每天重复请求完全没有覆盖的公司。

建议为无覆盖数据设置更长 TTL，例如30天；目标价已有数据的股票按正常 TTL 更新。

### 7.4 数据校验

- 拒绝 NaN、Infinity、负数目标价。
- 分析师最低价应不大于平均价，平均价通常不大于最高价；异常时记录警告但保留原始返回，除非明显无效。
- `analyst_count` 不得为负数。
- 富途返回股票代码必须与请求代码对应。
- 更新单只股票时使用数据库事务，避免目标价写入成功但任务状态没有更新。

---

## 8. 刷新策略

### 8.1 首次初始化

提供命令：

```bash
python -m app.scripts.init_expectation_gap --market all
```

必须支持参数：

```text
--market a|hk|all
--limit 20
--codes HK.00700,SH.600519
--resume
--force
```

建议首次执行流程：

1. 获取并写入 A/H 股基础列表。
2. 批量获取当前价格。
3. 按股票逐只获取晨星数据。
4. 按股票逐只获取分析师数据。
5. 每处理一只立即提交数据库，支持中断后继续。
6. 输出进度、预计剩余时间、成功/无数据/失败数量。

先执行样本验证：

```bash
python -m app.scripts.init_expectation_gap \
  --codes HK.00700,HK.09988,SH.600519,SZ.000001
```

样本成功后再运行全量。全量任务可能持续数小时，这是正常现象。

### 8.2 每日刷新

建议每个交易日收盘后执行一次，例如北京时间18:00：

1. 更新正常上市股票列表。
2. 批量更新最新价格。
3. 对全部有效记录重新计算预期差。
4. 更新超过分析师 TTL 的评级数据，默认24小时。
5. 更新超过晨星 TTL 的评级数据，默认7天。
6. 对明确 `no_data` 的股票使用30天 TTL。
7. 保存任务结果。

虽然页面“每天刷新”，不代表晨星数据每天必须请求。晨星公允价值通常变化较慢，但预期差会因股价变化每天改变。

### 8.3 本机自动运行

V1 优先提供手动命令：

```bash
python -m app.scripts.refresh_expectation_gap --daily
```

再增加 Windows Task Scheduler 配置说明。任务应设置为：

```text
频率：每个工作日一次
时间：北京时间18:00（新西兰本地时间需随夏令时调整）
前置条件：电脑开机、网络正常、OpenD正在运行并已登录
```

应用内部调度器不是 V1 必需项。避免开发服务器热重载时重复启动两个定时任务。

---

## 9. 后端 API

### 9.1 查询列表

```http
GET /api/expectation-gaps
```

查询参数：

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `market` | `all`、`a`、`hk` | 市场 |
| `q` | `腾讯`、`00700` | 名称/代码模糊搜索 |
| `sort_by` | `morningstar_gap_pct` | 排序字段 |
| `sort_order` | `desc` | `asc`/`desc` |
| `min_analyst_count` | `3` | 最低分析师人数 |
| `include_unrated` | `false` | 是否显示无评级股票 |
| `page` | `1` | 页码 |
| `page_size` | `50` | 每页数量，最大100 |

允许的 `sort_by` 必须使用白名单，禁止将用户输入直接拼接到 SQL：

```text
symbol
name
last_price
morningstar_fair_value
morningstar_gap_pct
analyst_average_target
analyst_gap_pct
analyst_count
updated_at
```

返回示例：

```json
{
  "items": [
    {
      "market": "HK",
      "futu_code": "HK.00700",
      "symbol": "00700",
      "name": "腾讯控股",
      "last_price": 640.0,
      "price_time": "2026-07-17T16:08:00+08:00",
      "morningstar_fair_value": 800.0,
      "morningstar_gap_pct": 25.0,
      "morningstar_star_rating": 4,
      "morningstar_data_date": "2026-05-09",
      "analyst_average_target": 716.0,
      "analyst_high_target": 820.0,
      "analyst_low_target": 579.51,
      "analyst_gap_pct": 11.88,
      "analyst_count": 44,
      "analyst_rating": 5,
      "analyst_data_date": "2026-05-11",
      "updated_at": "2026-07-18T18:10:00+08:00"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 328,
  "last_refresh": {
    "status": "success",
    "finished_at": "2026-07-18T18:30:00+08:00"
  }
}
```

### 9.2 刷新状态

```http
GET /api/expectation-gaps/refresh-status
```

返回最近任务状态、进度和错误统计。

### 9.3 手动刷新（可选）

```http
POST /api/expectation-gaps/refresh
```

如果实现，V1只能允许本机调用，并防止并发启动两个任务。不得让公开匿名用户触发全市场刷新。

---

## 10. 前端页面设计

页面顶部：

```text
标题：预期差排行
说明：预期差 = 目标价 ÷ 当前价 - 1；目标价不代表未来一定达到。
最后更新：YYYY-MM-DD HH:mm
```

筛选栏：

```text
[搜索股票名称/代码] [全部市场▼] [最低分析师人数▼]
[仅显示有评级 ✓] [重置]
```

表格字段：

| 列 | 展示规则 |
| --- | --- |
| 市场 | A股/H股标签 |
| 股票代码 | 显示代码 |
| 股票名称 | 中文名称 |
| 当前价 | 两位或市场常用精度 |
| 晨星公允价值 | 无数据为 `—` |
| 晨星预期差 | 正数绿色、负数红色；颜色遵循项目现有涨跌习惯 |
| 晨星星级 | 1～5星或 `—` |
| 晨星日期 | `YYYY-MM-DD` |
| 分析师平均目标价 | 无数据为 `—` |
| 分析师预期差 | 与晨星预期差相同格式 |
| 分析师人数 | 数字 |
| 目标价区间 | `最低 - 最高` |
| 分析师日期 | `YYYY-MM-DD` |

交互要求：

- 搜索输入使用300毫秒防抖。
- 查询条件变化后回到第1页。
- 排序、搜索、分页均由后端完成，前端不要一次加载全部股票。
- URL 保留查询参数，刷新网页后筛选条件不丢失。
- 加载时显示骨架或 loading。
- API失败时显示可重试错误，不展示空白页面。
- 手机屏幕允许横向滚动，固定股票代码和名称列可作为增强项。
- 在表格附近显示免责声明：评级和目标价仅供研究，不构成投资建议。

---

## 11. 日志与错误处理

日志至少包含：

```text
任务ID
开始/结束时间
市场
总股票数
已处理数量
成功数量
无数据数量
失败数量
当前股票代码
接口类型
错误分类
重试次数
```

禁止记录：

- 富途账号和密码。
- 完整认证信息。
- 不必要的晨星报告正文。

单只股票失败不得终止整个任务。OpenD整体断开时暂停并尝试重连；超过重试上限后将任务标记为 `partial` 或 `failed`，已成功写入的数据不得回滚。

---

## 12. 测试要求

### 12.1 单元测试

至少覆盖：

- 两种预期差的正确计算。
- 当前价为0或空时返回空。
- 目标价为空时返回空。
- 无效 NaN/负数数据处理。
- 名称和代码搜索。
- 市场筛选。
- `NULL` 排序在有效值之后。
- 最低分析师人数筛选。
- TTL 是否到期的判断。
- `success`、`no_data`、`error` 三种采集状态。
- 限频器和重试逻辑（使用 mock，不调用真实富途接口）。

### 12.2 集成测试

- 使用临时 SQLite 数据库。
- Mock 富途客户端，插入样本数据。
- 验证列表API的分页、排序和筛选。
- 验证重复刷新不会创建重复股票。
- 验证任务中断后 `--resume` 可以继续。

### 12.3 人工测试

OpenD启动并登录后，对以下样本运行真实接口：

```text
HK.00700 腾讯控股
HK.09988 阿里巴巴-W
SH.600519 贵州茅台
SZ.000001 平安银行
```

不要假设每只样本都有晨星覆盖。接口返回无数据时，确认数据库记录为 `no_data` 且页面显示 `—`。

---

## 13. 性能与安全要求

- 列表接口分页查询，不能把全部公司发送到浏览器。
- 数据库查询目标：普通筛选请求本机响应小于500毫秒。
- 添加必要索引，使用 `EXPLAIN QUERY PLAN` 检查主要查询。
- 查询参数必须校验，排序字段使用白名单。
- 手动刷新接口必须避免并发任务。
- OpenD只监听本机地址，不要把11111端口直接暴露到公网。
- 不提交 `.env`、SQLite数据库、采集日志和缓存文件。
- 仅使用富途正式 OpenAPI，不抓取网页私有接口。

建议 `.gitignore` 增加：

```gitignore
.env
*.db
*.sqlite
*.sqlite3
data/
logs/
```

如果现有项目需要提交 `data/` 下的其他文件，应只忽略具体数据库文件，不要粗暴忽略整个目录。

---

## 14. 实施阶段

### 阶段A：数据链路

- SQLite表和迁移。
- OpenD连接检查。
- 4只样本股票采集。
- 预期差计算。
- 命令行输出和数据库检查。

完成标准：样本任务可重复执行，不产生重复记录，部分无数据不会报错退出。

### 阶段B：后端API

- 列表API。
- 搜索、筛选、排序、分页。
- 刷新状态API。
- API测试。

完成标准：使用测试数据库验证所有查询组合。

### 阶段C：前端页面

- 导航入口和页面。
- 筛选栏、表格、排序、分页。
- 加载、空状态和错误状态。
- 响应式显示。

完成标准：用户可通过名称或代码快速找到股票，并分别按两种预期差排序。

### 阶段D：自动刷新和全量初始化

- 全市场股票列表。
- TTL增量刷新。
- 断点续传。
- Windows Task Scheduler说明。
- 全量运行观察及错误统计。

完成标准：任务中断后可恢复，每天只刷新必要数据，不重复全量请求未过期评级。

---

## 15. 验收清单

- [ ] OpenD未运行时网站仍可启动，刷新任务给出清晰错误。
- [ ] 能获取并保存样本股票的当前价。
- [ ] 能保存晨星公允价值或明确记录无覆盖。
- [ ] 能保存分析师平均目标价、人数和日期或明确记录无覆盖。
- [ ] 两种预期差计算正确。
- [ ] 页面支持A股/港股筛选。
- [ ] 页面支持名称和代码模糊搜索。
- [ ] 两种预期差都能升降序排列。
- [ ] 无数据不会显示为0%，也不会排到榜首。
- [ ] 支持后端分页。
- [ ] 页面显示数据日期和最后刷新时间。
- [ ] 每日刷新只请求已到期数据。
- [ ] 无覆盖股票采用更长TTL。
- [ ] 单股失败不会中断整个批次。
- [ ] 全量任务支持断点续传。
- [ ] 测试通过，README已更新。
- [ ] `.env`、数据库和日志没有提交到Git。

---

## 16. Codex 最终交付格式

实现完成后请输出：

1. 修改文件清单。
2. 数据库迁移或初始化方式。
3. OpenD安装和连接要求。
4. 后端、前端启动命令。
5. 样本刷新命令。
6. 全市场首次初始化命令。
7. 每日刷新命令。
8. 测试命令和测试结果。
9. 当前仍存在的限制或风险。

不要只给代码片段，应直接修改项目文件并完成可运行验证。

---

## 17. 关键设计结论

- V1使用 SQLite，它是单个本地文件，不需要部署数据库服务。
- 当前价格每天更新，因此预期差每天重新计算。
- 分析师目标价默认每天检查一次。
- 晨星公允价值默认7天检查一次。
- 明确无覆盖的股票默认30天后再检查。
- 页面查询数据库，不在用户打开页面时调用富途。
- 富途采集必须通过已启动并登录的 OpenD。
- 先以少量样本验证，再进行耗时较长的全市场初始化。

