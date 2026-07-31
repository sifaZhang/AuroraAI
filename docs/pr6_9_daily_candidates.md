# PR6.9：首板回调每日候选流水线

## 目标与边界

PR6.9 将已持久化的首板事件、交易日历、证券状态、日 K、PR6.5 回调观察、
PR6.6 上下文分数和局部一分钟缓存组合为单交易日候选快照。正式入口为：

```text
backend.strategy.first_limit.run_daily_candidates
```

本 PR 不提供 API、页面、定时调度、通知、自动交易或新数据供应商，也不重跑
PR6.8 的历史 S0～S4 复核。

实际仓库与早期规划存在一个需要明确记录的差异：PR6.5 已合并的是纯
`pullback` 规则和观察持久化契约，没有独立正式 runner。因此 PR6.9 复用已
持久化且版本匹配的观察和上下文分数；缺少这些上游结果时保存
`unknown/indeterminate`，不会在候选 runner 内静默伪造完整评分。

## 每日流程

```text
检查交易日历及检测覆盖
→ 按需显式触发当日首板检测
→ 读取仍处于 D0～D6 的首板事件
→ 排除已有 eliminated/expired 终态事件
→ 按证券停牌状态计算有效观察日
→ 读取截止边界内的日线或分钟输入
→ 生成三态 evidence
→ 形成不可变阶段快照
→ 保存 item 和稳定报告
```

同一股票的多个事件始终由 `first_limit_event_id` 区分。已经在较早交易日
形成 `eliminated` 或 `expired` 的事件停止消费后续行情，不会复活。

## tail_preview 与 close_confirmed

### tail_preview

默认 `as_of=14:30`。分钟 provider 的请求上限就是 `as_of`，默认 provider
只读取 `first_limit_minute_bars` 中不晚于该时点的数据。PR6.8 只复用纯
`confirm_tail_entry()`，不复用历史成交 runner。

当日 OHLC 只能由截至 `as_of` 的分钟聚合产生；数据库里可能已存在的当日
最终日 K 不会进入尾盘决策。分钟序列不足以证明当日完整路径或尾盘确认时，
结果为 `pending_close_confirmation`，不会借用 15:00 收盘。

### close_confirmed

默认 `as_of=15:00`，只读取 `trade_date` 及以前的最终日 K，并要求当前观察
日存在版本匹配的 PR6.5/PR6.6 结果。收盘快照不会修改尾盘快照，而是保存
`preview_candidate_id` 和以下变化：

```text
unchanged
upgraded
downgraded
newly_qualified
eliminated
preview_missing
```

## 生命周期

- `watching`：D0、停牌或尚未达到最低可执行分。
- `eligible`：尾盘阶段已满足完整条件。
- `pending_close_confirmation`：尾盘输入不足，等待收盘。
- `confirmed`：收盘阶段最终 S/A/B 候选。
- `eliminated`：明确触发永久硬淘汰。
- `expired`：D6 时仍未形成有效候选。
- `indeterminate`：必要日历、状态、日 K 或上下文数据无法确定。

D0 不入选。有效观察日是 D1～D5；交易所休市日不计数，明确停牌日也不消耗
股票的观察日。D6 形成一次 `expired` 终态，之后不再读取该事件。

## 规则、等级和 unknown

PR6.9 使用 PR6.5 的正式参数：

- 最低价不得跌破首板最低价；
- 最大回撤阈值为 12%，等于阈值通过；
- 当前量相对首板量不得超过 70%；
- 放量长阴沿用 `RISK_VOLUME_RATIO=1.5`；
- 行业分数不高于 8 视为明显退潮；
- PR6.7 已验收的完整日线可执行下限为 `daily_base_score >= 68`。

等级不引入新的 75/85 阈值，而是使用已有互斥回调分类：

```text
A1 → S
A2 → A
B  → B
```

只有所有硬条件为 `pass`、上下文完整且非近似、分数达到 68 时才能形成等级。
任何必要输入为 `unknown` 时都不能产生最终等级。尾盘阶段可进入 pending；
收盘阶段进入 indeterminate。unknown 不等同于 fail，也不进入硬失败比例。

## 原因码与 evidence

每条 evidence 固定保存：

```text
rule_code
result = pass | fail | unknown
actual_value
threshold_value
unit
source_date/source_time
reason_code
display_text
ordinal
```

稳定原因码覆盖观察窗口、ST、停牌、跌破首板低点、最大回撤、缩量失败、
放量长阴、板块退潮、缺日线、缺分钟、缺证券状态、缺交易日历、缺上下文、
尾盘缺确认、等待收盘和 D6 过期。展示文案不作为业务判断键。

## Schema、自然键和事务

- `daily_candidate_runs`：`(trade_date, stage, parameter_hash)` 唯一。
- `daily_candidate_items`：`(run_id, first_limit_event_id)`。
- `daily_candidate_snapshots`：`(run_id, first_limit_event_id)`。
- `daily_candidate_evidence`：`(candidate_id, rule_code)`。

相同正式参数自动复用既有 run，不通过新自增 ID 制造重复结果。

一个首板事件是最小业务事务：snapshot、全部 evidence 和成功/不确定 item
同事务提交。异常会回滚该事件的全部业务写入，再独立保存 failed item；
其他事件继续。运行级异常将 run 收敛为 failed。

resume 校验完整参数哈希，跳过已完成 event。force 必须与 resume 同用；
`force_symbols` 只重算原 run 范围内指定股票，不改变参数语义。

dry-run 执行相同纯评价但不创建 run/item/snapshot/evidence，也不运行写入型
迁移。显式 `--detect-missing-events` 才会调用已有本地首板检测器；默认仅
复用已有事件，绝不静默重算或联网补数。

## CLI

尾盘预警：

```bash
python -m backend.strategy.first_limit.run_daily_candidates \
  --trade-date 2026-07-30 \
  --stage tail_preview \
  --as-of 2026-07-30T14:55:00+08:00 \
  --data-cutoff 2026-07-30T14:55:00+08:00
```

收盘确认：

```bash
python -m backend.strategy.first_limit.run_daily_candidates \
  --trade-date 2026-07-30 \
  --stage close_confirmed \
  --data-cutoff 2026-07-30T15:00:00+08:00
```

范围和恢复：

```bash
python -m backend.strategy.first_limit.run_daily_candidates \
  --trade-date 2026-07-30 \
  --stage close_confirmed \
  --data-cutoff 2026-07-30T15:00:00+08:00 \
  --symbols 000001.SZ,300001.SZ \
  --run-id candidates-20260730

python -m backend.strategy.first_limit.run_daily_candidates \
  --trade-date 2026-07-30 \
  --stage close_confirmed \
  --data-cutoff 2026-07-30T15:00:00+08:00 \
  --run-id candidates-20260730 \
  --resume --force --force-symbols 000001.SZ
```

其他控制参数：

```text
--strategy-version
--detection-version
--pullback-version
--context-version
--dry-run
--detect-missing-events
--report json|markdown
```

退出码：0 为 success/dry-run，1 为 partial，2 为参数/来源/resume 错误或
failed，3 为未预期运行错误。

## 输出

JSON 包含 run、数据完整性摘要、稳定排序的 S/A/B、等待确认、淘汰/过期、
尾盘到收盘变化、完整 evidence 和失败 item。排序顺序为：

```text
grade(S/A/B) → score desc → symbol → first_limit_event_id
```

Markdown 按运行摘要、S/A/B、等待确认、淘汰、阶段变化和失败项目输出，适合
人工每日复核。

## 已知限制和真实验证

- 当前只使用本地 SQLite；不会自动补齐缺失日线、分钟、行业或市场输入。
- 历史行业映射若不能证明为观察日有效，仍必须保持 unknown/approximate。
- 全市场显式检测可能耗时，应先确认本地日线、元数据、状态和20日历史覆盖。
- 实时分钟 provider 是最小注入接口；本 PR 不建立长驻行情订阅。

真实环境验证步骤：

1. 备份并迁移测试副本，不直接以生产库首次试跑。
2. 选择少量已知 D1～D5 事件运行 tail_preview。
3. 确认 GM/缓存只返回截至 14:55 的分钟。
4. 收盘后运行 close_confirmed，核对变化类型和原尾盘快照不变。
5. 执行 foreign key、integrity 和重复运行检查。
6. 扩展到全市场前审计数据覆盖和运行时长。
