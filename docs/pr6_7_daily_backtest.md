# PR6.7 日线代理回测

PR6.7 提供只读取本地 SQLite 历史数据的首板策略日线代理回测。当前版本为
`daily_backtest_v1`，scope 固定为 `daily_proxy`。它不是分钟级回测，也不联网补采缺失数据。

## 策略与数据边界

- 候选必须来自版本完全匹配的 PR6.3–PR6.6 持久化结果。
- 仅选择完整、非近似、未淘汰、候选池中的 A1/A2，且 `daily_base_score >= 68`。
- 同一首板事件只采用最早符合条件的观察日。
- 入场使用观察日收盘价的日线代理，并明确保存 `approximate_entry=1`。
- 回测只查询 `observation_date` 至 `data_cutoff` 之间的未复权日 K。
- `data_cutoff` 后的日 K 不读取、不参与当日或此前决策。
- 分钟确认、`minute_confirm_score` 和 `total_score` 不参与本版本。
- 固定参数为：-7% 止损、+12% 止盈、+8% 启动且回撤 5% 的移动止盈、S1 日线代理，以及最多 10 个有效持有交易日。
- 同一日同时触及盈利和亏损阈值时，默认采用保守亏损路径。
- 卖出滑点方向对策略不利，A 股价格按 0.01 元报价精度向不利方向取整。

停牌、无量、异常 OHLC、收盘涨停流动性不可验证和一字板不会被当作正常可成交入场。
有效持有日与退出延迟市场日分开计数。

## 退出指令和终态

退出指令首次写入后不可变。只有 trade、signal 日期和原始原因与数据库持久化值完全相同的请求才幂等。
原始原因不去除首尾空格。

退出指令因跌停等原因不能成交时，signal 日不计为第 1 个延迟市场日。随后最多观察 5 个市场日：

- 第 1–5 日恢复成交时，使用恢复日开盘价，并保存对应 delay 记录。
- 第 5 日仍不能成交时立即终结为 `open_unresolved/five_untradable_exit_days`，不读取第 6 日。
- 数据在完整五日窗口前截止时，终结为 `open_unresolved/data_ended`，保存实际观察到的 0–4 日。

合法终态组合只有：

- 成交：`terminal_status=closed`、`exit_order_status=filled`。
- 未决：`terminal_status=open_unresolved`、`exit_order_status=unresolved`。

终态只接受与持久化字段精确一致的幂等重放。冲突请求不会覆盖首次终态。

## Runner

正式入口位于 `backend.strategy.first_limit.run_daily_backtest`：

- `normalize_parameters(...)`：规范化日期、symbol、版本并生成稳定 SHA-256 参数摘要。
- `run_symbol_backtest(...)`：执行一个 symbol；调用方持有事务。
- `run_backtest(...)`：执行多 symbol、维护账本、隔离失败并生成 portfolio 汇总。
- `export_run(...)`：按稳定字段顺序导出 JSON 或 CSV。

runner 对每个候选依次持久化 signal、trade、退出指令、delay 和终态。同一 run/event 的 signal 唯一，
同一 signal 的 trade 唯一，因此 resume 或同一 run 的重复执行不会生成第二笔交易。

终态形成后，状态机停止读取后续业务 bar；五日失败路径不会读取第 6 个延迟日。

## Run 和 item 账本

`backtest_runs` 使用最小状态集合：

- `running`
- `success`
- `partial`
- `failed`

run 保存规范化参数 JSON、参数摘要、策略和检测版本、日期边界、symbol 范围、开始/结束时间、最终计数及受限长度错误。

`backtest_run_items` 以 `(run_id, symbol)` 为自然唯一键，状态为：

- `success`
- `skipped`
- `failed`

item 保存 trade、closed、unresolved、skipped 数量及受限长度的异常类型和消息。
run 的 planned/success/skipped/failed/unresolved 汇总从最终 item 记录重新查询计算，不信任内存计数。
含 unresolved trade 的 item 仍是执行成功；unresolved 通过独立计数表达，不创建重复含义状态。

## 事务边界

一个 symbol 是最小原子执行单元。该 symbol 的 signal、trade、delay、resolve 和成功 item 在同一个
SQLite 事务中。任一步骤异常会回滚该 symbol 的全部业务写入，随后用独立事务保存 failed item。
已成功的其他 symbol 不回滚。

run 初始化单独提交；所有 item 完成后收敛 run 状态。运行级异常会把已经创建的 `running` run
收敛为 `failed`。真实进程被强制终止仍可能留下 `running`，这是账本用于恢复判断的可观察状态。

## dry-run、resume 和 force

- dry-run 只规范化参数、查询候选数量，不创建或修改 run、item、signal、trade 或指标。
- resume 必须指定既有 `run_id`，且当前规范化参数摘要必须与原 run 完全一致。
- resume 跳过成功 item，重新执行 failed 或未完成 item。
- force 必须与 resume 一起使用，只删除并重算该 run 参数范围内的目标 symbol；其他 run 不受影响。
- 参数不一致、缺少 run 或非法参数组合都会明确失败。

幂等依赖规范化参数摘要以及数据库自然键，不依赖自增 ID。

## Portfolio、指标和导出

run 完成时生成 portfolio scope 指标：

- trade 总数
- closed 数量
- open_unresolved 数量
- 完整收益样本数
- coverage ratio
- closed 样本平均净收益
- unresolved 原因分布

只有 `closed` 且 `net_return` 非空的交易进入完整收益统计。`open_unresolved` 不进入胜率、平均收益等完整收益口径，
但进入 coverage 和退出风险原因统计。毛收益、净收益和费用均使用与单笔 trade 相同的持久化结果。

JSON 和 CSV 导出按 symbol、event 排序并使用固定字段顺序。当前已有纯函数
`benchmark_return(...)`，但正式 runner 不会在缺少明确本地 benchmark 序列时伪造基准结果。

## CLI

普通运行：

```bash
python -m backend.strategy.first_limit.run_daily_backtest \
  --start-date 2026-01-01 \
  --end-date 2026-03-31 \
  --data-cutoff 2026-04-15 \
  --symbols 000001.SZ,600000.SH
```

可选版本参数：

- `--strategy-version`
- `--detection-version`
- `--quality-version`
- `--pullback-version`
- `--context-version`

运行控制：

- `--dry-run`
- `--resume --run-id <id>`
- `--resume --force --run-id <id>`

退出码：

- `0`：成功或 dry-run 成功。
- `1`：部分成功。
- `2`：参数、版本、resume 契约错误，或 run 已收敛为 failed。
- `3`：未归类运行异常。

## 已知限制

- 本版本只有日线代理，不实现分钟确认或真实成交队列。
- 不联网补齐缺失日 K、交易日历、指数或 benchmark 数据。
- 未实现并发 worker；同一 SQLite 数据库应由调用方串行启动相同 run。
- 强制终止进程可能留下 `running` 账本，后续必须由明确 resume 操作处理。
- 结果是研究用途的日线代理估计，不是实盘收益承诺。
