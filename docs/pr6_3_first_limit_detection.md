# PR6.3 首板事件识别

本 PR 仅从本地 SQLite 识别、审计和版本化保存首板事件；不实现回调模型、评分、页面、CSV、交易或任何网络同步。

规则直接复用 PR6.1：仅未复权 OHLC，GM `source_upper_limit` 优先，可信的 Decimal 半角进位计算价仅作后备。收盘达到涨停价才是涨停；盘中触及会保存但不是事件。历史状态按日期读取，绝不以当前名称回填历史 ST。停牌、无涨跌幅、新股状态不明、除权嫌疑、限价冲突和缺失状态均保守排除/不可确定。

首板窗口严格取交易所 `CN` 开市日的此前 20 日，缺任一日K、元数据或可靠限价即为 `indeterminate`。上一交易日涨停为连板；窗口内任何涨停、连板或一字板均为 `excluded`。一字板要求 OHLC 全等、收盘涨停且成交量大于零。

`first_limit_events` 以 `(symbol, trade_date, detection_version)` 幂等保存确认命中的正向首板、限价来源、OHLC、原因、质量标记和可选 `source_run_id`；不复制日K或分钟线。未命中/不确定结果保存在运行 item 的结构化审计记录中。稳定读取接口为 `event_repository.get_events_for_date`、`get_events_for_symbol` 和 `upsert_events`。

命令：`python -m backend.strategy.first_limit.detect_first_limits --trade-date YYYY-MM-DD --codes 600000.SH --dry-run`。日期范围必须显式且最多 31 个自然日；全市场扫描必须显式 `--max-symbols`。非 dry-run 创建共享 PR6.2 账本中的 `detect` run；`--resume --run-id` 只接受参数完全相同的 run，并跳过已成功项目；`--force` 在同一 run 内重新计算目标项，不影响其他 detection version。dry-run 不写事件、run 或 item。退出码 0/1/2 分别为完全成功/部分或不可确定/运行级错误。受控四股票真实验收已完成：1 个 detect run、4 个成功 item、0 个正向事件。

PR6.4 可依赖版本化事件表和上述读取接口，但仍须实现首板质量评分，不能把本 PR 的事件结果视作候选或评分。
