# AuroraAI 三年平均股息率模块设计方案

## 1. 文档目的

本文档定义 AuroraAI 红利投资模块中的“三年平均股息率”功能。

该功能使用上市公司最近三个完整财年的实际每股现金分红，计算其平均年度每股分红，再除以最新有效股价，筛选当前价格下历史分红回报较高的公司。

核心目标：

- 页面展示三年平均股息率不低于 5% 的公司；
- 对三年平均股息率达到或超过 8% 的公司发送邮件提醒；
- 识别特别分红、分红下降、数据缺失等风险；
- 避免同一股票每天重复提醒；
- 同时支持 A 股和港股，允许后续扩展其他市场。

> 本模块提供的是历史分红对应的估值信号，不代表公司未来一定维持分红，也不构成自动买入结论。

---

## 2. 功能名称与页面位置

建议功能名称：

> 三年平均股息率

建议页面结构：

```text
红利投资
├── 即将分红
├── 三年平均股息率
└── 分红历史
```

建议路由或页面文件名：

```text
three-year-dividend-yield.html
```

或：

```text
/dividend/three-year-yield
```

---

## 3. 核心计算公式

### 3.1 三年每股分红总额

```text
三年每股分红总额
= 财年1实际每股现金分红
+ 财年2实际每股现金分红
+ 财年3实际每股现金分红
```

### 3.2 三年平均每股分红

```text
三年平均每股分红
= 三年每股分红总额 ÷ 3
```

### 3.3 三年平均股息率

```text
三年平均股息率
= 三年平均每股分红 ÷ 最新有效股价 × 100%
```

### 3.4 示例

假设某公司：

| 财年 | 每股现金分红 |
|---|---:|
| 2023 | 0.50 元 |
| 2024 | 0.60 元 |
| 2025 | 0.70 元 |

当前股价为 10.00 元。

计算结果：

```text
三年每股分红总额 = 0.50 + 0.60 + 0.70 = 1.80 元
三年平均每股分红 = 1.80 ÷ 3 = 0.60 元
三年平均股息率 = 0.60 ÷ 10.00 × 100% = 6.00%
```

该股票应显示在页面中，因为三年平均股息率大于 5%，但不触发 8% 邮件提醒。

---

## 4. 统计口径

### 4.1 使用最近三个完整财年

默认使用最近三个已经结束的完整财年。

例如当前为 2026 年，则默认统计：

```text
2023 财年
2024 财年
2025 财年
```

如果最近财年的年度分红方案尚未最终确认，则该股票应标记为：

```text
数据不完整
最近财年分红待确认
```

不得静默改用更早的三个年度，也不得将预测分红混入历史实际分红。

### 4.2 按分红所属财年归集

分红应按照其所属财年统计，而不是按照实际派息日期统计。

例如：

- 2025 年度利润对应的年度分红；
- 即使在 2026 年实际派发；
- 仍应归入 2025 财年。

这样可以避免同一自然年内因为派息时间不同而重复或遗漏。

### 4.3 只统计现金分红

计入：

- 年度现金分红；
- 中期现金分红；
- 特别现金分红；
- 同一财年内多次现金派息。

不计入：

- 送股；
- 转增股本；
- 配股；
- 股票股利；
- 回购；
- 资本公积转增。

### 4.4 特别分红单独标记

特别分红可以计入三年每股分红总额，但必须独立记录，避免一次性大额分红造成误导。

建议记录：

```text
特别分红总额
特别分红占三年分红比例
是否包含特别分红
```

### 4.5 统一到当前股本口径

若统计期间发生以下事件：

- 送股；
- 转增；
- 拆股；
- 合股；
- 股本重组；
- 其他影响每股口径的公司行动；

则历史每股分红必须调整到与当前股价可比较的每股口径。

不能直接将未经调整的历史每股分红与当前股价相除。

### 4.6 股价口径

优先使用最近一个有效交易日的收盘价。

股价记录必须同时保存：

```text
latest_price
price_date
price_source
```

如果股价日期不是最近有效交易日，应标记为过期数据，不触发邮件提醒。

---

## 5. 页面展示规则

### 5.1 默认筛选条件

默认只显示：

```text
三年平均股息率 >= 5%
```

### 5.2 分级展示

| 三年平均股息率 | 状态 | 页面标识 |
|---:|---|---|
| 5% 至不足 6% | 进入观察 | 高股息 |
| 6% 至不足 8% | 较有吸引力 | 重点观察 |
| 不低于 8% | 高股息信号触发 | 邮件提醒区 |

建议不要在页面直接显示“可以买入”。

建议显示：

```text
当前价格下，过去三年平均股息率达到高股息观察阈值。
请进一步检查盈利持续性、分红趋势和特别分红影响。
```

### 5.3 默认排序

默认排序：

```text
三年平均股息率，从高到低
```

支持按以下字段排序：

- 最新股价；
- 三年每股分红总额；
- 三年平均每股分红；
- 三年平均股息率；
- 最近一年股息率；
- 连续分红年数；
- 特别分红占比；
- 更新时间。

---

## 6. 页面表头

| 字段 | 类型 | 说明 |
|---|---|---|
| 市场 | 文本 | A 股、港股 |
| 股票代码 | 文本 | 使用系统统一代码格式 |
| 股票名称 | 文本 | 公司简称 |
| 所属行业 | 文本 | A 股优先使用申万一级行业 |
| 最新股价 | 数值 | 最近有效收盘价 |
| 股价日期 | 日期 | 股价对应交易日 |
| 财年 1 | 整数 | 最近第三个完整财年 |
| 财年 1 每股分红 | 数值 | 该财年实际现金分红 |
| 财年 2 | 整数 | 最近第二个完整财年 |
| 财年 2 每股分红 | 数值 | 该财年实际现金分红 |
| 财年 3 | 整数 | 最近完整财年 |
| 财年 3 每股分红 | 数值 | 该财年实际现金分红 |
| 三年每股分红总额 | 数值 | 三个财年每股现金分红之和 |
| 三年平均每股分红 | 数值 | 三年总额除以 3 |
| 三年平均股息率 | 百分比 | 三年平均每股分红除以当前股价 |
| 最近一年股息率 | 百分比 | 最近财年每股分红除以当前股价 |
| 分红趋势 | 枚举 | 增长、稳定、下降、波动 |
| 连续分红年数 | 整数 | 连续有现金分红的财年数量 |
| 特别分红总额 | 数值 | 三年内特别分红合计 |
| 特别分红占比 | 百分比 | 特别分红占三年总分红比例 |
| 数据完整性 | 枚举 | 完整、缺年度、待确认、异常 |
| 风险标签 | 文本 | 分红下降、特别分红过高、亏损等 |
| 提醒状态 | 枚举 | 未触发、今日首次、已提醒、重新进入 |
| 更新时间 | 时间 | 最近计算时间 |

---

## 7. 基础筛选规则

进入页面主列表的股票至少满足：

```text
最近三个完整财年均存在有效现金分红数据
三年平均股息率 >= 5%
最新股价有效
股价日期为最近有效交易日
证券当前处于正常上市状态
非退市整理
非长期停牌
最近财年净利润大于 0
```

可以保留但必须警告的情况：

- 分红下降；
- 最近财年利润显著下降；
- 特别分红占比较高；
- 周期行业处于盈利高点；
- 股价异常下跌；
- 现金流不足；
- 股利支付率异常高；
- 港股低流动性；
- 数据来源不一致。

---

## 8. 分红趋势判断

建议提供简单、透明、可解释的规则。

设三个财年每股分红分别为：

```text
dps_1, dps_2, dps_3
```

其中 `dps_3` 为最近财年。

### 8.1 增长

```text
dps_1 <= dps_2 <= dps_3
```

且最近财年不低于最早财年的 110%。

### 8.2 稳定

三年最大值与最小值差异不超过平均值的 20%。

### 8.3 下降

```text
dps_1 >= dps_2 >= dps_3
```

或最近财年分红低于三年平均值的 70%。

### 8.4 波动

不符合增长、稳定或下降规则的情况。

分红趋势只用于提示，不直接决定是否展示。

---

## 9. 8% 邮件提醒规则

### 9.1 基础触发条件

股票满足以下全部条件时，进入邮件提醒候选：

```text
三年平均股息率 >= 8%
三年分红数据完整
最新股价有效且未过期
证券正常交易
最近财年净利润大于 0
```

### 9.2 质量检查

建议邮件中区分“通过质量检查”和“存在风险”。

建议的质量检查条件：

```text
三年中至少两年分红未下降
最近财年每股分红 >= 三年平均每股分红的 60%
特别分红占三年分红总额 <= 50%
最近财年股利支付率不异常
最近财年经营现金流为正
```

其中部分数据暂时无法获取时，不应伪造通过状态，应标记：

```text
质量检查不完整
```

### 9.3 首次提醒

满足以下条件时发送邮件：

```text
上一计算日三年平均股息率 < 8%
当前计算日三年平均股息率 >= 8%
```

### 9.4 重新提醒

已经提醒过的股票，仅在以下任一条件满足时再次发送：

- 三年平均股息率比上次提醒提高至少 1 个百分点；
- 当前股价比上次提醒价格下跌至少 10%；
- 新财年分红数据发布后重新计算，仍然达到 8%；
- 股票离开 8% 区域后，再次重新进入；
- 风险状态发生重大改善或恶化。

### 9.5 不重复提醒

同一股票、同一交易日、同一提醒类型只能发送一次。

建议幂等键：

```text
market + symbol + calculation_date + alert_type
```

### 9.6 邮件标题

```text
AuroraAI：三年平均股息率达到 8% 的公司（YYYY-MM-DD）
```

### 9.7 邮件内容

建议邮件表格：

| 股票 | 当前价 | 股价日期 | 三年平均股息率 | 三年分红总额 | 分红趋势 | 特别分红占比 | 触发原因 | 风险标签 |
|---|---:|---|---:|---:|---|---:|---|---|

邮件说明：

```text
本提醒表示该公司在当前价格下，过去三年平均现金分红回报达到高股息观察阈值。
历史分红不代表未来分红承诺，请继续检查盈利、现金流、分红政策和行业周期。
```

---

## 10. 数据库设计

### 10.1 三年平均股息率快照表

```sql
CREATE TABLE three_year_dividend_yield_snapshots (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    calculation_date DATE NOT NULL,

    price_date DATE NOT NULL,
    latest_price REAL NOT NULL,
    price_source TEXT,

    fiscal_year_1 INTEGER NOT NULL,
    fiscal_year_1_dps REAL,
    fiscal_year_2 INTEGER NOT NULL,
    fiscal_year_2_dps REAL,
    fiscal_year_3 INTEGER NOT NULL,
    fiscal_year_3_dps REAL,

    three_year_total_dps REAL,
    three_year_average_dps REAL,
    three_year_average_yield REAL,
    latest_year_yield REAL,

    special_dividend_dps REAL DEFAULT 0,
    special_dividend_ratio REAL DEFAULT 0,

    dividend_trend TEXT,
    consecutive_dividend_years INTEGER,

    latest_net_profit REAL,
    latest_operating_cash_flow REAL,
    latest_payout_ratio REAL,

    data_quality_status TEXT NOT NULL,
    warning_flags TEXT,
    alert_eligible INTEGER NOT NULL DEFAULT 0,

    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,

    PRIMARY KEY (market, symbol, calculation_date)
);
```

建议索引：

```sql
CREATE INDEX idx_three_year_yield_date_yield
ON three_year_dividend_yield_snapshots (
    calculation_date,
    three_year_average_yield DESC
);

CREATE INDEX idx_three_year_yield_symbol
ON three_year_dividend_yield_snapshots (
    market,
    symbol,
    calculation_date DESC
);
```

### 10.2 邮件提醒记录表

```sql
CREATE TABLE dividend_yield_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    calculation_date DATE NOT NULL,

    alert_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    yield_at_alert REAL NOT NULL,
    price_at_alert REAL NOT NULL,
    price_date DATE NOT NULL,

    fiscal_years TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    warning_flags TEXT,

    sent_at DATETIME,
    alert_status TEXT NOT NULL,
    error_message TEXT,

    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,

    UNIQUE (
        market,
        symbol,
        calculation_date,
        alert_type
    )
);
```

### 10.3 运行记录表

建议复用 AuroraAI 现有运行账本设计，或者新增：

```sql
CREATE TABLE dividend_yield_calculation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    calculation_date DATE NOT NULL,
    market TEXT,
    status TEXT NOT NULL,

    total_symbols INTEGER DEFAULT 0,
    success_symbols INTEGER DEFAULT 0,
    skipped_symbols INTEGER DEFAULT 0,
    failed_symbols INTEGER DEFAULT 0,
    display_count INTEGER DEFAULT 0,
    alert_count INTEGER DEFAULT 0,

    started_at DATETIME NOT NULL,
    completed_at DATETIME,
    error_message TEXT,
    parameters_json TEXT
);
```

---

## 11. API 设计

### 11.1 查询列表

```http
GET /api/dividends/three-year-yield
```

查询参数：

```text
market=A|HK|ALL
min_yield=5
max_yield=
industry=
trend=
include_warnings=true
sort=three_year_average_yield
order=desc
page=1
page_size=100
```

返回示例：

```json
{
  "calculation_date": "2026-07-27",
  "price_date": "2026-07-27",
  "total": 125,
  "items": [
    {
      "market": "A",
      "symbol": "600000.SH",
      "name": "示例公司",
      "industry": "银行",
      "latest_price": 10.0,
      "fiscal_years": [2023, 2024, 2025],
      "annual_dps": [0.5, 0.6, 0.7],
      "three_year_total_dps": 1.8,
      "three_year_average_dps": 0.6,
      "three_year_average_yield": 6.0,
      "latest_year_yield": 7.0,
      "dividend_trend": "growth",
      "special_dividend_ratio": 0.0,
      "data_quality_status": "complete",
      "warning_flags": [],
      "alert_status": "not_triggered"
    }
  ]
}
```

### 11.2 手动刷新股价

```http
POST /api/dividends/three-year-yield/refresh-prices
```

### 11.3 手动刷新分红数据

```http
POST /api/dividends/three-year-yield/refresh-dividends
```

### 11.4 重新计算

```http
POST /api/dividends/three-year-yield/recalculate
```

请求示例：

```json
{
  "market": "ALL",
  "calculation_date": "2026-07-27",
  "force": false,
  "send_alerts": true
}
```

### 11.5 发送测试邮件

```http
POST /api/dividends/three-year-yield/test-email
```

---

## 12. 页面交互设计

页面顶部建议提供：

- 市场选择：A 股、港股、全部；
- 最低显示阈值，默认 5%；
- 邮件提醒阈值，默认 8%；
- 行业筛选；
- 分红趋势筛选；
- 是否包含风险股票；
- 刷新股价；
- 刷新分红数据；
- 重新计算；
- 发送测试邮件。

页面顶部显示数据状态：

```text
计算日期
最新股价日期
覆盖股票数量
数据完整数量
大于等于 5% 的数量
大于等于 8% 的数量
存在风险警告的数量
最近一次邮件发送时间
```

### 12.1 行样式

建议：

- 5% 至不足 6%：普通展示；
- 6% 至不足 8%：重点标记；
- 不低于 8%：高亮提醒；
- 数据不完整：灰色或警告图标；
- 特别分红占比过高：橙色风险标记；
- 最近年度分红明显下降：红色风险标记。

---

## 13. 后端计算流程

建议流程：

```text
1. 读取证券主数据
2. 筛选当前正常上市证券
3. 确定最近三个完整财年
4. 读取并归集每个财年的现金分红
5. 识别普通分红与特别分红
6. 处理拆股、合股、送转等口径调整
7. 获取最近有效交易日收盘价
8. 计算三年分红总额
9. 计算三年平均每股分红
10. 计算三年平均股息率
11. 计算最近一年股息率
12. 判断分红趋势
13. 执行数据质量检查
14. 写入每日快照
15. 找出大于等于 5% 的页面展示股票
16. 找出大于等于 8% 的邮件提醒候选
17. 根据提醒历史执行去重
18. 发送邮件
19. 写入提醒记录和运行账本
```

---

## 14. CLI 建议

建议新增命令：

```bash
python -m backend.collector.calculate_three_year_dividend_yield \
  --market all \
  --calculation-date 2026-07-27 \
  --display-threshold 5 \
  --alert-threshold 8 \
  --send-alerts
```

建议参数：

```text
--market a|hk|all
--calculation-date YYYY-MM-DD
--display-threshold FLOAT
--alert-threshold FLOAT
--send-alerts
--dry-run
--force
--symbols CODE1,CODE2
--max-symbols N
--resume
--run-id ID
```

退出码建议：

```text
0：全部成功
1：部分股票失败或邮件部分失败
2：参数错误、运行级错误或全部失败
```

---

## 15. 定时任务建议

推荐在每日收盘价完成更新后执行。

A 股和港股可以分开刷新，也可以在两个市场都收盘后统一运行。

建议运行顺序：

```text
1. 刷新 A 股股价
2. 刷新港股股价
3. 检查是否有新的分红公告或公司行动
4. 重新计算三年平均股息率
5. 更新页面快照
6. 检查 8% 邮件提醒
7. 发送并记录邮件
```

分红数据本身不需要每天全量刷新，可以：

- 股价每日刷新；
- 分红公告每日增量检查；
- 分红历史每周或按需全量校验；
- 计算结果每日更新。

---

## 16. 数据质量与异常处理

### 16.1 分红数据缺失

处理方式：

```text
不计算最终三年平均股息率
不发送邮件
页面可选择显示为数据不完整
记录缺失财年
```

### 16.2 股价缺失或过期

处理方式：

```text
不发送邮件
保留上一日结果但标记股价过期
不得使用 0 或默认值参与计算
```

### 16.3 重复分红记录

必须根据公司、财年、方案、实施状态、派息批次等字段去重。

不能因为同一分红方案存在预案、股东大会通过、实施公告等多条记录而重复计算。

### 16.4 方案未实施

仅统计已经最终确认并达到系统认可状态的现金分红。

建议状态优先级：

```text
已实施 > 已确认待实施 > 股东大会通过 > 董事会预案
```

默认只将“已实施”计入历史实际分红。

如果业务需要将“已确认待实施”纳入，应单独标记，不得与已实施数据混为一谈。

### 16.5 币种

A 股通常使用人民币，港股可能存在港元、人民币或其他币种分红。

每股分红和股价必须使用同一币种后再计算。

若需要换汇，应保存：

```text
dividend_currency
price_currency
fx_rate
fx_rate_date
```

不得直接将不同币种相除。

---

## 17. 建议风险标签

```text
DIVIDEND_DECLINING
SPECIAL_DIVIDEND_HIGH
LATEST_PROFIT_NEGATIVE
OPERATING_CASH_FLOW_NEGATIVE
PAYOUT_RATIO_HIGH
PRICE_STALE
DIVIDEND_DATA_INCOMPLETE
CORPORATE_ACTION_UNRESOLVED
LOW_LIQUIDITY
RECENT_SUSPENSION
CURRENCY_MISMATCH
DATA_SOURCE_CONFLICT
```

页面应将内部标签转换为中文说明。

---

## 18. 测试要求

### 18.1 单元测试

至少覆盖：

- 三年总分红计算正确；
- 平均每股分红计算正确；
- 股息率计算正确；
- 5% 边界值；
- 8% 边界值；
- 三年中某一年为 0；
- 三年数据缺失；
- 特别分红占比计算；
- 分红趋势判断；
- 拆股口径调整；
- 合股口径调整；
- 不同币种拒绝直接计算；
- 股价日期过期；
- 负利润过滤；
- 邮件去重；
- 离开 8% 后重新进入；
- 股息率提高 1 个百分点后再次提醒；
- 股价较上次提醒下降 10% 后再次提醒。

### 18.2 集成测试

至少覆盖：

```text
分红数据 -> 股价数据 -> 计算快照 -> API -> 前端展示
```

以及：

```text
计算快照 -> 提醒候选 -> 去重 -> 邮件发送 -> 提醒记录
```

### 18.3 幂等性测试

同一交易日重复运行：

- 不得重复写入同一主键快照；
- 不得重复发送相同提醒；
- `--force` 允许重算，但仍不得重复发同类邮件；
- dry-run 不写数据库、不发邮件。

---

## 19. 验收标准

功能完成至少需要满足：

1. 能正确识别最近三个完整财年；
2. 能按财年汇总普通分红和特别分红；
3. 能处理同一财年多次派息；
4. 能使用最近有效股价计算三年平均股息率；
5. 页面默认只显示不低于 5% 的公司；
6. 页面可以按股息率从高到低排序；
7. 达到 8% 的公司能够进入提醒候选；
8. 同一股票不会每天重复发送邮件；
9. 数据缺失、股价过期、币种不一致时不发送错误提醒；
10. 特别分红和分红下降能够显示风险标记；
11. 支持 A 股、港股和全部市场筛选；
12. 所有计算结果具有可追溯的数据日期和来源；
13. 运行失败不会影响其他股票计算；
14. 运行过程有账本、统计和错误记录；
15. dry-run、force、resume 行为明确且经过测试。

---

## 20. 推荐实施顺序

### PR 1：数据契约与计算核心

- 明确分红所属财年字段；
- 明确普通分红与特别分红；
- 完成三年平均股息率纯函数；
- 完成分红趋势与质量标签；
- 补充单元测试。

### PR 2：数据库与批量计算

- 新增迁移；
- 新增快照表；
- 新增提醒记录表；
- 新增运行账本；
- 完成 CLI；
- 支持 dry-run、force、resume。

### PR 3：API 与页面

- 新增查询 API；
- 新增页面；
- 增加市场、行业和阈值筛选；
- 增加风险标签；
- 增加刷新和重算按钮。

### PR 4：邮件提醒

- 实现 8% 首次提醒；
- 实现提醒去重；
- 实现重新进入和显著变化提醒；
- 增加测试邮件；
- 增加发送失败重试和日志。

### PR 5：真实数据验收与定时运行

- A 股真实样本；
- 港股真实样本；
- 特别分红样本；
- 拆股或合股样本；
- 不同币种样本；
- 邮件真实发送验收；
- 配置每日定时任务。

---

## 21. 最终功能定义

> 使用最近三个完整财年的实际每股现金分红，计算平均年度每股分红，再除以最近有效股价。页面展示三年平均股息率不低于 5% 的公司；对达到或超过 8% 且数据有效的公司发送邮件提醒，同时显示分红趋势、特别分红和数据质量风险。

该功能用于寻找当前价格下历史分红回报较高的公司，但最终投资判断仍应结合：

- 盈利持续性；
- 经营现金流；
- 股利支付率；
- 行业周期；
- 资产负债情况；
- 分红政策变化；
- 特别分红占比；
- 当前估值和未来增长。
