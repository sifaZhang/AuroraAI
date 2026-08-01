# PR5.12：申万行业日快照 V1

## 目标与范围

本版本基于当前申万 2021 一级、二级、三级行业树与股票归属，使用同一个计算引擎生成
客观日快照。快照是后续行业评分的事实层，不包含行业综合分、排名、新闻、资金流或估值。

> 本版本使用当前申万行业归属计算所有日期，不具备历史成分还原能力。

## 数据来源

- 行业树与归属：`industry_nodes`、`industry_memberships_current`。
- 未复权日线：`a_share_daily_bars`，仅使用 `adjustment='none'`。
- 证券资格与停牌：`a_share_security_master`、按目标日 as-of 查询的
  `a_share_security_status_history`。
- 交易日：`a_share_trading_calendar` 的 CN 市场日历。
- 前收盘与权威涨跌停价：`first_limit_daily_metadata`；缺失权威价格时仅通过项目中央
  `resolve_price_limit_rule/resolve_limit_prices` 规则计算。
- 首板与炸板：`first_limit_events`，仅在目标日对行业全部 eligible 股票具有明确检测字段时可用。

`source_snapshot` 保存实际行情来源、收益样本数、当前归属限制以及首板/炸板能力标记。

## 指标定义

- `constituent_count`：当前行业归属中的去重股票数。
- `eligible_count`：存在证券主记录、有上市日期、目标日已上市且尚未退市的股票数。
- `valid_bar_count`：目标日存在未复权日线且收盘价有效的 eligible 股票数。
- `missing_bar_count = eligible_count - valid_bar_count`。
- `suspended_count`：目标日 as-of 状态明确为停牌的 eligible 股票数；不以成交量为零推断。
- `coverage_ratio = valid_bar_count / eligible_count`；分母为零时为 `0`。
- 个股收益为 `(close / pre_close - 1) × 100`。`pre_close` 必须来自统一元数据；缺失时
  该股票不进入收益统计，但仍可计入有效日线覆盖。
- `equal_weight_return`、`median_return` 只在有效收益样本不少于 3 时计算。
- 涨、跌、平使用命名常量 `RETURN_EPSILON=1e-9`；强势上涨事实阈值为日涨幅不低于 3%，
  它不是评分规则。
- 涨停、跌停按中央规则解析后的可靠价格与收盘价比较，误差容忍为半个最小价格单位
  `0.005`，不使用固定 `9.9%`。
- `first_limit_count`、`broken_limit_count` 只有行业全部 eligible 股票的对应事件字段完整时
  才统计；否则保存 `NULL`，不会把缺失伪装成 0。炸板沿用已有事件的
  `touched_upper_limit=1 AND is_limit_up_close=0`。
- 成交额保存有效日线中非空成交额的总和与中位数。

## 数据状态

- `complete`：收益样本不少于 3 且行情覆盖率不低于 95%。
- `partial`：收益样本不少于 3，但覆盖率低于 95%。
- `insufficient`：存在有效日线，但有效收益样本少于 3。
- `empty`：没有 eligible 股票或没有有效日线。

一级、二级、三级共用同一个聚合函数；同一股票在同一层级通过集合去重，只统计一次。

## 写入、幂等和失败隔离

迁移 `024_industry_daily_snapshots.sql` 创建 `industry_daily_snapshots`，主键为交易日、分类、
版本和行业代码。默认只写内容发生变化的行业，`--force` 强制替换计算成功的结果，
`updated_at` 不参与内容比较。单行业计算失败会记录 warning，其他行业继续；失败行业不执行
upsert，因此不会破坏旧快照。`--dry-run` 只计算和比较，不迁移、不写数据库。

## CLI

```powershell
python -m backend.data_sources.cli build-industry-snapshots --date 2026-07-31 --dry-run
python -m backend.data_sources.cli build-industry-snapshots --date 2026-07-31 --level 2
python -m backend.data_sources.cli build-industry-snapshots --date 2026-07-31 --force
python -m backend.data_sources.cli build-industry-snapshots --start-date 2026-07-30 --end-date 2026-07-31
python -m backend.data_sources.cli db-industry-snapshots --date 2026-07-31 --level 2 --limit 20
```

日期范围严格通过交易日历展开。非交易日不生成空快照，并在结果的 `skipped_dates` 中返回。
退出码为：0 全部 complete，1 存在 partial/insufficient/empty 或局部失败，2 无任何可用结果的失败。

## 已知限制

- 使用当前行业归属回算历史日期，存在历史成分前视限制。
- V1 不计算相对 20 日活跃度、评分或排名。
- 收益依赖 `first_limit_daily_metadata.pre_close`；缺失时不会从不可靠口径猜测。
- 首板事件表覆盖不足时首板和炸板字段为 `NULL`。
- 正式库必须先完成 PR5.11 当前行业快照正式同步，才能生成真实行业日快照。

## 测试

离线测试覆盖迁移幂等、三级共用聚合、去重、证券资格、停牌、缺失行情、小样本与空行业、
收益及家数、强势上涨、权威涨跌停、首板/炸板能力、覆盖率除零、dry-run、正式写入、幂等、
force、日期范围、非交易日、查询与单行业失败隔离。

2026-08-01 使用正式库副本完成真实验证：副本先通过 Tushare 同步 497 个行业节点和
5,866 只当前归属股票，再对 2026-07-30 执行 dry-run。结果为一级 31、二级 130、三级
336；`complete=460`、`partial=2`、`insufficient=32`、`empty=3`、计算失败 0；覆盖率
最小值 0、中位数 1、最大值 1；唯一缺失行情股票 1 只。首板和炸板完整覆盖行业均为 0，
因此相关字段保持 `NULL`。正式数据库没有写入 PR5.11 归属或 PR5.12 快照数据；运行中的
Uvicorn reload 自动执行了新增迁移，所以正式库已存在空的 `industry_daily_snapshots` 表（0行）。
