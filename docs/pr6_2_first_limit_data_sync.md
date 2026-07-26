# PR6.2 首板回调策略受控数据同步

## 架构与边界

PR6.2 复用现有 `a_share_daily_bars` 和申万一级当前快照股票池；不创建第二套 OHLCV 表，不扫描首板，也不调用交易接口。GM 网络请求只在受控同步子命令中进行，下载线程不会访问 SQLite，所有写入都由主线程完成。

PR6.0 已验证的 GM 输入是：`get_trading_dates`、`get_instruments`、`get_history_instruments`、未复权日线 `history(..., frequency='1d', adjust=0)` 和近 180 日的 1 分钟 `history`。历史 ST 字段没有得到 PR6.0 的可靠验证，所以同步器只保存来源实际返回的字段；不会用今天名称或状态回填历史。

## 新增数据

- `first_limit_daily_metadata`：以 `(symbol, trade_date)` 保存 GM 权威 `pre_close`、`source_upper_limit`、`source_lower_limit`、来源和多个质量标记；不复制 OHLCV。
- `first_limit_minute_bars`：仅缓存显式代码和日期范围的未复权 1 分钟 bar，键为 `(symbol, bar_time, timeframe)`。
- `first_limit_sync_runs` / `first_limit_sync_items`：记录参数、状态、行数、失败和 resume 兼容性。
- `a_share_security_master` 增加 `is_active`；PR6.1 的按日状态和交易日历表继续复用。

## 同步子命令

```powershell
python -m backend.collector.sync_first_limit_data calendar --start-date 2026-02-13 --end-date 2026-02-23
python -m backend.collector.sync_first_limit_data securities --codes 600000.SH,000001.SZ --max-symbols 2
python -m backend.collector.sync_first_limit_data statuses --codes 600000.SH --start-date 2026-02-13 --end-date 2026-02-23
python -m backend.collector.sync_first_limit_data daily --codes 600000.SH --start-date 2026-02-13 --end-date 2026-02-23 --dry-run
python -m backend.collector.sync_first_limit_data minute --codes 600000.SH --start-date 2026-02-23 --end-date 2026-02-23
python -m backend.collector.sync_first_limit_data audit
```

非 dry-run 从 `GM_TOKEN`（或 `--token-env`）读取凭据，绝不输出或写入 token。`--dry-run` 不联网、不迁移、不写数据库。`--resume --run-id` 只接受完全一致的任务类型和参数。

## 质量与安全

- 日历以 GM 返回的完整交易日集合为权威；范围内非集合日期被写为休市，不能用工作日推断。
- 日K只请求本地缺失的开市日区间，落库前再次过滤已有日期，默认不覆盖有效历史数据。
- `get_history_instruments` 的权威限价优先写入元数据；规则计算仅用于质量校验。昨收不连续、疑似除权、缺少状态/规则、限价冲突均保留质量标记。
- 分钟线必须传 `--codes` 和日期范围；默认最多 5 个代码、5 个自然日，超过阈值必须 `--allow-large-run`。

## 已知限制与 PR6.3 输入

本 PR 不把缺K线自动当停牌，不伪造历史 ST，也不把当前申万成分当历史事实。PR6.3 可以依赖规范 symbol、交易日历、按日状态、策略每日元数据、未复权日K与分钟缓存；它仍必须排除缺失/冲突/复权/除权质量标记以及未覆盖的历史 ST 状态。
