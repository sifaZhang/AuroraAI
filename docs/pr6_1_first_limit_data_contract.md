# PR6.1 首板回调策略数据契约与交易规则

## 范围

本 PR 只提供后续首板识别、回测和同步共用的数据契约、SQLite 元数据表与确定性规则服务。它不下载行情、不扫描首板、不产生候选、不提供 API/页面，也不触发任何交易接口。

PR6.0 已验证：GM 的未复权日线提供 OHLCV、成交额和 `pre_close`；`get_history_instruments` 提供按日 `upper_limit`、`lower_limit`、`is_suspended`、`listed_date` 和 `board`；GM 可提供沪深交易日历。本地 `a_share_daily_bars` 是短期 Sina 未复权日线，尚无 `pre_close` 与权威涨跌停价。因此本 PR 不重复创建日K表，而为 PR6.2 保留独立、可追溯的状态和日历存储。

## 统一证券标识

内部格式为 `600000.SH`、`000001.SZ` 或 `430047.BJ`。`normalize_symbol` 支持内部格式、GM 格式（如 `SHSE.600000`）、Sina 格式（如 `sh600000`）和带显式 `exchange` 参数的六位代码。裸六位代码没有交易所时会报错，不作猜测。`SecurityId` 可安全输出 GM 与 Sina 格式。

## SQLite 数据模型

迁移 `011_first_limit_strategy_contract.sql` 新增而不改写现有行情表：

- `a_share_security_master`：证券主数据及最近已知上市/退市信息；
- `a_share_security_status_history`：以 `(symbol, effective_date)` 为主键的按日状态，含板块、ST、停牌、无涨跌幅阶段、来源、质量标记；
- `a_share_trading_calendar`：以 `(market, trade_date)` 为主键的开市日历，含来源、质量标记和更新时间。

状态查询只返回查询日当日或之前最近的真实状态；当前状态不得回填为历史事实。日历服务在日期缺失时抛出 `LookupError`，从不把工作日当作交易日。所有质量标记以稳定 JSON 数组保存，允许多个问题同时存在。

## 交易规则与涨跌停价

`resolve_price_limit_rule` 按交易日解析规则，规则代码集中于 `backend.strategy.first_limit.rules`：

| 类型 | 正常幅度 | 有效日期/处理 |
| --- | ---: | --- |
| 沪深主板非 ST | 10% | 支持 |
| ST | 5% | 标记为不适合首板池 |
| 创业板 | 10% / 20% | 2020-08-24 起 20%，此前 10% |
| 科创板 | 20% | 2019-07-22 起；此前为不支持 |
| 北交所 | 30% | 2021-11-15 起；此前为不支持且 V1 排除 |

新股无涨跌幅阶段、停牌和未知板块不会被猜测：来自历史状态的 `no_price_limit=true` 会返回 `NO_LIMIT`；上市日但没有可靠状态则返回 `UNKNOWN` 与 `new_listing_status_unverified`。这些状态均带 `not_eligible_for_first_limit`，PR6.3 必须跳过。

涨跌停解析优先采用 PR6.0 验证的 GM 权威 `upper_limit/lower_limit`。若它们缺失，才以 `pre_close` 和支持的规则用 `Decimal`、0.01 元最小报价单位和 `ROUND_HALF_UP` 计算。源值和计算值同时存在但不一致时，返回源值以便审计，却标记 `source_calculation_mismatch` 与 `data_source_conflict`、`reliable=false`；不得静默当作有效首板价。

## 价格口径、来源与质量

允许来源：`SINA`、`GM`、`CALCULATED`、`MANUAL`、`UNKNOWN`。涨停识别只能使用 `Adjustment.NONE` 的原始未复权价格；前/后复权、未知复权、疑似除权、昨收不连续、缺少状态、缺少规则及来源冲突均是独立质量标记。PR6.3 应只在 `LimitPrices.reliable=true` 且没有首板排除质量标记时判定涨停。

## 已知限制与后续输入

- PR6.1 不同步 GM 的主数据、日历或历史状态；PR6.2 按受控分批、断点续跑和幂等写入补齐它们。
- 新股无涨跌幅阶段依赖权威历史状态，不能只由上市日期和自然日推断。
- 现有申万行业成员只有当前快照；用于历史回测必须保持 `approximate/lookahead` 警告，不能作为精确历史成分。
- PR6.3 只能通过本契约读取规则/状态/限价，并记录质量标记和排除原因，不能散落代码前缀或“涨幅 >= 9.9%”的硬编码。
