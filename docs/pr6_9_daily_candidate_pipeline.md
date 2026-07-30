# PR6.9：首板回调每日候选生成与运行闭环

请在 AuroraAI 仓库中实现 PR6.9。本次基于已经合并到 `main` 的 PR6.1～PR6.8 开发。开始前必须先检查实际代码、迁移、测试和文档，不得仅凭本指令猜测接口；优先复用现有领域模型、repository、交易日历、运行账本、数据截止边界和稳定导出机制。

## 一、目标

把已有能力串成一个每天可运行、可恢复、可审计的候选生成流水线：

```text
日线数据就绪检查
→ 首板事件检测/复用
→ 2～5个有效交易日观察池更新
→ 尾盘预警候选
→ 收盘确认候选
→ S/A/B 分级
→ 候选、淘汰原因、证据及运行状态持久化
→ 稳定 JSON/Markdown 输出
```

本 PR 的完成标准不是页面展示，而是使用一个正式 CLI 即可对指定交易日生成可重复、可追溯的真实每日候选结果。

## 二、先做仓库核查

开始实现前先完成并汇报：

1. 当前分支、HEAD、工作区状态，确认 PR6.8 已提交且没有覆盖用户无关修改。
2. 阅读 PR6.1～PR6.8 相关文档、迁移和代码，整理可以复用的接口。
3. 确认现有代码中：
   - 首板事件如何表示；
   - 2～5日观察和日线回测是否已有共享判定函数；
   - 行业/板块环境从何处读取；
   - `data_cutoff`、交易日历、ST/停牌/涨跌停状态如何处理；
   - run/item 账本、resume/force 和稳定导出采用何种约定；
   - PR6.8 是历史成交后的分钟复核，不能直接承担当日实时尾盘选股。
4. 若旧文档与实际实现冲突，以已合并代码和数据契约为准，并在 PR6.9 文档中记录差异。

## 三、严格范围

本 PR 应实现：

- 单交易日的每日候选流水线。
- 指定股票和全市场两种范围。
- 首板来源事件复用；缺失时可按明确开关触发检测，但不得静默重算。
- 2～5个有效交易日观察池状态更新。
- 尾盘预警版和收盘确认版两个独立阶段。
- S/A/B 分级、观察、淘汰、不可确定状态。
- 每条规则的结构化证据和稳定原因码。
- run/item/candidate/evidence 持久化。
- dry-run、resume、force、失败隔离、幂等。
- 稳定 JSON 和 Markdown 报告。
- 正式 CLI、退出码和完整离线测试。

本 PR 不实现：

- FastAPI 接口（留给 PR6.10）。
- 前端页面和按钮（留给 PR6.11）。
- 定时调度、邮件或其他通知。
- 新的数据供应商或全市场分钟历史初始化。
- PR6.8 的 S0～S4 历史复核重跑。
- 未经历史验证的复杂机器学习评分。
- 自动交易或下单。

## 四、两个阶段必须分离

同一个 `trade_date` 下支持两个阶段：

### 1. `tail_preview`

用于尾盘预警。默认评价时点为 A 股时间 14:55；只能读取不晚于 `as_of` / `data_cutoff` 的数据。

- 分钟数据不足以确认时，可以输出 `pending_close_confirmation`。
- 不得借用 15:00 收盘数据。
- 若没有分钟数据，不得把日线收盘伪装成尾盘确认。
- 如果当前实现尚无适用于实时候选的分钟入口，应通过可注入 provider 建立最小接口，并允许测试使用固定样本；不得大规模复制 PR6.8。

### 2. `close_confirmed`

用于收盘确认。必须基于当日最终有效收盘数据重新评价：

- 输出最终 S/A/B、观察或淘汰状态。
- 保存相对同日尾盘预警版的变化。
- 变化类型至少包括：`unchanged`、`upgraded`、`downgraded`、`newly_qualified`、`eliminated`、`preview_missing`。
- 不允许修改或覆盖尾盘预警快照。

阶段必须进入业务自然键。尾盘和收盘结果是两个不可变快照，不是同一行反复覆盖。

## 五、候选生命周期

首板事件发生日记为 D0，只评价 D1～D5 的有效交易日；自然日不计数。建议状态：

```text
watching
eligible
pending_close_confirmation
confirmed
eliminated
expired
indeterminate
```

要求：

- D0 不进入回调买点候选。
- D1～D5 每日重新评价，但历史快照不可覆盖。
- 已淘汰的事件默认不复活。
- D5 后仍未形成有效候选则 `expired`。
- 停牌日不应简单当作一个正常观察日消耗；必须按照既有交易日/证券状态契约处理并测试。
- 一个股票存在多个首板事件时，必须以 `first_limit_event_id` 区分，禁止仅按股票代码覆盖。
- 任何状态都必须可追溯到来源事件、来源数据日期和规则版本。

## 六、规则与分级

先从现有策略文档和代码提取最终规则，不要另造一套互相冲突的规则。至少落实以下硬条件：

- 非 ST。
- 非停牌。
- D1～D5。
- 回调期间最低价不得跌破首板最低价。
- 最大回撤不超过策略配置阈值，默认 12%（若现有版本已有不同正式参数，以现有参数为准）。
- 无放量长阴等现有硬淘汰条件。
- 板块明显退潮时按现有规则降级或淘汰。
- 尾盘买点规则必须遵守已有数据可得性边界。

每个条件输出三态：

```text
pass / fail / unknown
```

禁止把缺数据当作 `false`。硬条件为 `unknown` 时，候选应进入 `indeterminate` 或明确的待确认状态，不得评为最终 S/A/B。

建议将等级判定实现为可配置、确定性的规则表：

- S：全部硬条件通过，并满足最高等级加分要求。
- A：全部硬条件通过，质量较高但未达到 S。
- B：全部硬条件通过，只满足最低可执行标准。
- `watching`：尚在观察期但买点未形成。
- `eliminated`：明确触发硬淘汰条件。
- `indeterminate`：必要数据缺失或相互矛盾。

不要把 S/A/B 只保存为一个无法解释的总分。即使保留分数，也必须同时保存各项规则结果、阈值、实际值和原因码。

## 七、原因码与证据

建立稳定、面向机器的原因码，展示文案与原因码分离。至少覆盖：

```text
NOT_IN_D1_D5
STOCK_IS_ST
SUSPENDED
BROKE_FIRST_LIMIT_LOW
MAX_DRAWDOWN_EXCEEDED
VOLUME_CONTRACTION_FAILED
BEARISH_HIGH_VOLUME_BAR
SECTOR_RETREAT
TAIL_CONFIRMATION_MISSING
PENDING_CLOSE_CONFIRMATION
INSUFFICIENT_DAILY_BARS
INSUFFICIENT_MINUTE_BARS
MISSING_SECURITY_STATUS
MISSING_TRADING_CALENDAR
DATA_AFTER_CUTOFF
EXPIRED_AFTER_D5
```

名称可根据项目现有风格调整，但一经落库必须稳定。

每条 evidence 至少保存：

- `rule_code`
- `result`
- `actual_value`
- `threshold_value`
- `unit`
- `source_date` 或 `source_time`
- `reason_code`
- 可选展示说明

JSON 字段顺序、原因码顺序和候选排序必须稳定，不能依赖数据库偶然返回顺序。

## 八、持久化建议

根据现有 schema 风格设计迁移，表名可调整。至少需要表达：

### daily runs

- `id`
- `trade_date`
- `stage`
- `as_of`
- `data_cutoff`
- `strategy_version`
- 规范化参数及参数哈希
- `status`
- success/failed/indeterminate 等计数
- started/finished 时间

### run items

最小失败隔离单元建议为 `(run_id, first_limit_event_id)`，记录：

- symbol
- item status
- error type/message
- attempt

### candidate snapshots

业务自然键建议：

```text
(run_id, first_limit_event_id)
```

并建立能防止同一正式运行重复写入的约束。保存：

- `trade_date`
- `stage`
- `symbol`
- `first_limit_event_id`
- `observation_day`
- lifecycle status
- candidate grade
- optional score
- preview comparison/change type
- source run/event/version 信息

### evidence

自然键建议：

```text
(candidate_id, rule_code)
```

迁移必须注册到现有自动迁移入口，并通过 SQLite：

```text
PRAGMA foreign_key_check;
PRAGMA integrity_check;
```

不要为了方便修改或删除 PR6.1～PR6.8 已有账本记录。

## 九、事务、幂等与失败隔离

- 每个来源首板事件是最小业务事务单元：candidate、evidence、成功/未决 item 同事务提交。
- 单个事件异常时完整回滚该事件，再独立保存 failed item；其他股票继续。
- 运行级异常必须将 run 收敛到 `failed`。
- 重复执行相同正式参数不得产生重复业务结果。
- `resume --run-id` 必须校验规范化参数哈希完全一致。
- `force` 只能配合 resume。
- 如支持 `force_symbols`，只重算指定股票，不能改变原 run 参数语义或影响其他项目。
- dry-run 不写 run、item、candidate 或 evidence。
- dry-run 与正式运行对同一固定输入应产生等价的业务输出。

## 十、数据边界与防未来函数

- 所有 provider 必须接受或受到 `data_cutoff/as_of` 限制。
- `tail_preview` 禁止读取评价时点以后的分钟和收盘字段。
- `close_confirmed` 禁止读取 `trade_date` 之后的数据。
- 只为仍处于活动观察池的股票读取所需数据，避免全量分钟读取。
- 终态后停止消费不再需要的未来数据。
- 单元测试使用“读取即报错”的哨兵数据，证明不会访问未来 bar。
- 任何数据不足都必须落为 `unknown` / `indeterminate` / `pending`，不得默认为通过。

## 十一、CLI

建议新增：

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

参数至少包括：

```text
--trade-date
--stage tail_preview|close_confirmed
--as-of
--data-cutoff
--symbols
--strategy-version
--dry-run
--resume --run-id
--resume --force [--force-symbols]
--detect-missing-events（若确有必要，默认关闭）
--report json|markdown
```

若现有 CLI 风格已有一致命名，应遵循现有风格。

退出码建议沿用现有策略 runner：

- `0`：success 或 dry-run 成功
- `1`：partial，存在个别 failed/indeterminate
- `2`：参数、来源、resume 契约错误或运行 failed
- `3`：未预期运行错误

## 十二、输出

JSON 至少包含：

- run 元数据和数据截止时间；
- 市场/数据完整性摘要；
- S/A/B 候选；
- watching/pending/indeterminate；
- 淘汰列表及原因；
- 收盘版相对尾盘版的变化；
- 每个候选的关键证据；
- 运行失败项目。

Markdown 面向人工每日阅读，建议顺序：

1. 运行摘要与数据完整性警告。
2. 最终可执行 S/A/B。
3. 仍需等待确认的候选。
4. 淘汰及主要原因。
5. 尾盘版与收盘版变化。
6. 观察池状态。
7. 失败/不可确定项目。

同等级候选建议按：

```text
grade → score desc → symbol → first_limit_event_id
```

稳定排序。

## 十三、测试要求

新增独立领域测试和 runner/repository/CLI 测试。至少覆盖：

1. D0 不入选，D1～D5 正确计算，D5 后过期。
2. 节假日、周末和停牌不被错误当作普通观察日。
3. 同一股票多个首板事件不冲突。
4. 跌破首板最低价永久淘汰。
5. 最大回撤边界值（等于阈值通过、超过阈值失败，或遵循现有正式定义）。
6. 缩量、放量长阴、板块退潮。
7. S/A/B 的边界和稳定判定。
8. 缺字段、缺日线、缺分钟、缺证券状态、缺交易日历。
9. `unknown` 不进入 fail 比例，也不能产生最终等级。
10. 尾盘预警不读取 14:55 后数据。
11. 收盘确认不读取次日数据。
12. 尾盘到收盘的升级、降级、淘汰、不变和尾盘缺失。
13. 重跑幂等。
14. dry-run 零写入。
15. resume 参数哈希校验。
16. force/force_symbols 范围隔离。
17. 单事件事务回滚和多股票失败隔离。
18. run 状态 success/partial/failed 收敛。
19. JSON/Markdown 稳定输出和排序。
20. migration、foreign key、integrity。
21. 全量既有测试不回归。

测试不得联网，不得操作真实数据库，使用临时 SQLite 和固定行情样本。

## 十四、文档

新增 `docs/pr6_9_daily_candidates.md`，至少说明：

- 目标与非目标；
- 每日运行流程；
- 尾盘预警与收盘确认的区别；
- 候选生命周期；
- S/A/B 和 unknown 语义；
- 原因码；
- 数据边界；
- schema 与自然键；
- dry-run/resume/force；
- CLI 示例；
- 输出示例；
- 已知限制；
- 真实环境验证步骤。

真实验证只列步骤，不在没有明确授权时联网、写真实数据库或调用 GM。

## 十五、验收与交付

完成后执行并汇报：

```bash
python -m pytest <PR6.9新增测试> -q
python -m pytest tests/strategy -q
python -m pytest -q
python -m py_compile <新增或修改的Python文件>
git diff --check
```

对临时数据库执行：

```text
PRAGMA foreign_key_check;
PRAGMA integrity_check;
```

最终报告必须包括：

- 实现范围；
- 关键接口；
- 每日流程和阶段边界；
- 自然键、幂等和事务设计；
- 数据不足的处理；
- CLI；
- 测试结果；
- 修改文件列表；
- `git diff --stat`；
- `git status --short`；
- 尚待真实环境验证事项。

完成代码和测试后停在“可选择性暂存并提交，但尚未提交”的状态。未经用户明确要求：

- 不得 commit；
- 不得 push；
- 不得创建 PR；
- 不得联网获取真实行情；
- 不得操作真实数据库；
- 不得覆盖或清理用户已有修改。

