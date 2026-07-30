# PR6.8：分钟线复核与 S1 专项分析

## 范围

PR6.8 以一个已经持久化的 PR6.7 日线回测 run 为输入，只处理该 run 中
`entry_status='filled'` 的交易。它不扫描股票池，也不下载全市场分钟线。

正式输出包括：

- 14:40～14:55 的分钟级尾盘确认；
- S0～S4 五种退出规则的逐笔结果；
- S1 专项及分组指标；
- run/item 执行账本；
- 稳定 JSON 导出和 Markdown 摘要。

本 PR 不生成每日候选，不提供 API 或页面，也不执行真实下单。

## 输入、自然键和数据边界

PR6.7 的 `backtest_trades`、`backtest_signals`、首板事件和回调观察提供交易、
事件、O0、A1/A2/B、T+2～T+5、P1/P2 及环境分组输入。

一分钟缓存继续使用已有 `first_limit_minute_bars`，自然键为：

```text
(symbol, bar_time, timeframe)
```

其中本模块只读取或写入 `timeframe='1m'`。通过 GM 补数必须显式指定
`--fetch-missing`，请求范围由 PR6.7 来源交易决定：从观察日 14:40 开始，
到 `data_cutoff` 以内最多第三个已知开市日 15:00。没有交易日历记录时以
`data_cutoff` 为硬上限。缓存已存在时不重复请求；写入沿用缓存层的 upsert。

所有时间必须带时区，决策统一换算为 `Asia/Shanghai`。行情不复权
(`adjust=none`)。`data_cutoff` 之后的来源交易和分钟数据均不进入本次 run。

## 尾盘确认与买入

确认窗口是观察日 14:40～14:55。序列必须从 14:40 开始并逐分钟连续。
程序按时间顺序读取，并在第一次同时满足以下条件的分钟立即确认：

1. 收盘价高于 O0；
2. 相对前一分钟不再下行，且未低于已观察区间的最低收盘；
3. 收盘不低于本分钟开盘；
4. 相对此前分钟成交量中位数没有超过三倍的突然放量；
5. 不是涨停价上的一字分钟。

模拟买入基准价为该确认分钟收盘价，随后施加买入滑点并向上取 A 股
0.01 元报价精度。按 100 股整数手和固定名义本金计算股数，并计入佣金和
过户费。确认以后更晚的分钟不能反向修改该决策。

14:40 缺失、分钟断裂、字段非法、窗口在 14:55 前结束或停牌均标为
`indeterminate`；完整观察到 14:55 但没有满足条件则为 `rejected`。

## S0～S4 与执行规则

五个规则共享 +2% 止盈和最多三个分析交易日的时间退出，以便比较：

- **S0**：无价格止损，仅使用止盈或三交易日时间退出。
- **S1**：盘中首次 `low < O0`。正常盘中触发后使用下一根连续一分钟 K
  线开盘价成交；若某交易日第一根分钟开盘已低于 O0，则使用实际开盘价。
- **S2**：与 S1 相同，但阈值为 `O0 × 0.99`。
- **S3**：15:00 收盘价低于 O0，使用下一交易日第一根可用分钟开盘价。
- **S4**：跌破 O0 后连续 15 个完整交易分钟的最低价和收盘价均未收回
  O0，随后使用下一根连续分钟开盘价。午休不计作连续分钟；收盘重新站上
  O0 会清零计数。

S1 一旦触发，该路径结束，当日不会重新买入。后续行情只用于独立的 S1
误杀分析，不能改变已记录的退出成交。

同一分钟同时达到止盈和价格止损时，分钟 OHLC 无法证明先后顺序。实现
采用保守的止损优先规则，设置 `intraday_path_ambiguous=1`，不得默认盈利。

卖出以规定的原始成交价施加不利滑点，并向下取 0.01 元报价精度；净收益
扣除买卖佣金、过户费和卖出印花税。`entry_price_raw`、`entry_price`、
`exit_price_raw`、`exit_price`、费用、毛收益和净收益均单独保存。

## 缺数据与不可成交

- 触发后没有下一根分钟 K：`unresolved`；
- 触发与执行之间分钟不连续：`indeterminate`；
- 非法、乱序或盘中不连续分钟：`indeterminate`；
- 数据截止早于完整三个分析交易日：`unresolved`；
- 第三个交易日缺少 15:00 分钟：`indeterminate`；
- 一字跌停无法成交：顺延到首根非一字跌停分钟，并记录延迟分钟数；
- 数据结束仍未打开跌停：`unresolved`；
- 观察日明确停牌：`indeterminate`。

这些状态不生成虚假的确定成交，也不进入完整收益均值。它们进入 coverage
和退出风险计数。

## 账本、事务和幂等

`minute_review_runs` 保存规范化参数和 SHA-256 参数哈希；
`minute_review_items` 以 `(run_id, source_trade_id)` 为自然键；
`minute_review_results` 对同一自然键唯一；每个结果恰有 S0～S4 五个
`minute_review_stop_results`。

一个来源交易是最小业务事务单元：result、五个 stop 和成功/未决 item
同事务提交。中途异常会回滚该交易的全部新业务写入，随后单独保存 failed
item；其他交易不受影响。运行级异常会把 run 收敛为 `failed`。

`resume` 要求规范化参数哈希完全一致，跳过已经成功、跳过、不确定或未决的
item，只继续 failed 或未完成 item。`force` 必须与 `resume` 同用；
`force_symbols` 是独立的执行选择器，不参与改变原 run 参数，只删除和重算
该 run 中指定股票的来源交易。唯一约束和 upsert 保证重复执行不会用自增 ID
冒充业务幂等。

run 状态只有 `running/success/partial/failed`；item 状态只有
`success/indeterminate/unresolved/skipped/failed`。run 汇总由最终 item
状态计算，`indeterminate`、`unresolved` 或部分失败使 run 为 `partial`，
全部 item 失败为 `failed`。

## 指标与分组

S1 专项只把 `S1_stop` 和开盘低于阈值视为 S1 触发；止盈和时间退出不会
被误计为 S1 触发。输出：

- S1 触发次数；
- S1 平均实际成交损失；
- 触发后三个观察交易日内重新站回 O0 的比例；
- 触发后当天达到买入价 +2% 的比例；
- 触发后三个观察交易日内最高价重新达到买入价的比例；
- 相对 S0 减少的最大回撤；
- 相对 S2、S3、S4 增加或损失的总净收益。

另为 S0～S4 分别输出样本数、规则触发数、closed/unresolved/
indeterminate 数量、平均及总净收益和最差最大回撤。

后续行情已出现目标时可以立即确认阳性；阴性结果只有在当日或三个交易日的
相应观察窗口完整结束后才记为 `false`。覆盖不足时保存为 `null`，并从比例
分母排除，不能把“未观察到”当成“没有发生”。

上述指标按年份、A1/A2/B、T+2～T+5、10%/20% 板块、P1/P2、市场环境和
行业环境分别分组。缺少可证明的分组输入时使用 `unknown`，不以未来数据
回填。

## 使用

只读取现有分钟缓存：

```bash
python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30
```

限制股票范围并指定稳定 run：

```bash
python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30 \
  --symbols 000001.SZ,300001.SZ \
  --run-id minute-review-20260730
```

离线规划、不写 run/item/result 或分钟缓存：

```bash
python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30 \
  --dry-run
```

从 GM 只补来源事件局部窗口：

```bash
python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30 \
  --fetch-missing
```

恢复及强制重算：

```bash
python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30 \
  --run-id minute-review-20260730 \
  --resume

python -m backend.strategy.first_limit.run_minute_review \
  --source-run-id <PR6.7_RUN_ID> \
  --data-cutoff 2026-07-30 \
  --run-id minute-review-20260730 \
  --resume --force \
  --force-symbols 000001.SZ
```

`--report json` 输出机器可读结果，`--report markdown` 输出人工摘要。
退出码：0 为成功或 dry-run，1 为 partial，2 为参数/来源错误或 failed，
3 为未预期运行错误。

## 已知限制

- 本地自动化验收使用临时 SQLite 和固定分钟样本，没有联网，也没有操作
  真实数据库。
- GM SDK 适配器和局部请求边界已实现；真实分钟可用期、停牌/涨跌停返回形态
  及凭据权限仍需在提供 GM 凭据后做小样本验收。
- OHLC 分钟内无法证明路径先后时只能采用保守规则并保留 ambiguous 标记；
  本 PR 不使用 tick 数据。
- 市场和行业环境沿用 PR6.7 在观察时点持久化的评分分组，不进行未来回填。
