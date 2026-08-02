# 首板候选收盘确认规则

收盘确认只通过本地 `IndustryService` 判断当日 `industry_score_v1` 是否完整。未完成时返回 `industry_close_score_pending`，不得使用尾盘估算、上一交易日评分或联网补数冒充正式收盘结论。

正式行业层级沿用 PR6.13A 的三级→二级→一级规则。优先比较尾盘使用的同一层级；层级变化必须在 Evidence 中保留，跨层级误差仅供参考。

误差定义固定为：`score_error = intraday_estimated_score - official_close_score`，正数表示尾盘高估；`rank_error = official_close_rank - intraday_estimated_rank`。只有层级相同时排名误差才是严格同口径比较。

收盘使用正式行业分、排名和置信度重新计算行业趋势、行业环境和 100 分总分。资金活跃度、龙头及尾盘可见事实沿用尾盘 Evidence；结果明确区分 intraday 与 official_close。S/A/B 变化类型固定为 `UNCHANGED`、`UPGRADED`、`DOWNGRADED`、`NEWLY_CONFIRMED`、`REMOVED`、`PENDING`。

收盘重新执行硬性风控。最终 S/A 可进入次日可执行名单；B 仅观察且无买入建议；removed 保留尾盘历史快照和确认 Evidence，但不进入次日名单。次日计划只描述入场/失效条件、仓位建议及既有止盈止损引用，不生成自动交易指令。

回测尾盘阶段必须有历史分钟线并严格截断到 14:30 或 14:55，否则为 `intraday_not_backtestable`。正式行业评分只能在模拟 15:00 后、且评分日等于模拟日时使用，不能参与尾盘入选判断。
