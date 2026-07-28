# PR6.6 首板回调上下文评分

`context_scoring_version=first_limit_context_v1` 只计算可审计的日线基础分，理论满分为 90：PR6.4 首板质量 20、PR6.5 回调质量 30、行业 20、市场 10、个股趋势 10。

分钟确认尚未实现：`minute_confirm_status=not_available`、`minute_confirm_score=NULL`、`total_score=NULL`，候选层级固定为 `pending_minute_confirmation`。日线 90 分绝不缩放为 100 分，也不使用 75/85 分阈值。

行业历史归属、行业雷达和市场指数/涨跌停集合若不能证明为观察日有效，评分保存为 `missing`、`indeterminate` 或 `approximate`；当前行业快照不得产生确定性得分。CLI 离线读取既有事件、PR6.4 分数、PR6.5 观察和日K，不联网、不下载分钟线：

`python -m backend.strategy.first_limit.score_first_limit_context --observation-date YYYY-MM-DD --codes 600000.SH --dry-run`

普通执行创建独立的 context run；`--resume --run-id` 仅复用参数完全一致的 run，成功 item 默认跳过，`--force` 才重算目标项。dry-run 使用只读连接，不创建 run、item 或分数。
