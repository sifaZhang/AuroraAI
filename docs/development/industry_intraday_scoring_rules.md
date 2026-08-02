# 尾盘行业估算规则 V1

版本为 `industry_intraday_score_v1`，仅用于尾盘估算，绝不写入或冒充正式 `industry_score_v1`。

数据只来自本地 `industry_memberships_current`、`first_limit_minute_bars` 和历史未复权日线。截止时间必须在 14:30–14:55，SQL 同时约束交易日及 `bar_time <= as_of_time`；15:00 和后续数据不会参与。

有效层级沿用 PR6.13A。计算该层级全部成员的截至时点涨幅、等权涨幅、中位涨幅、上涨比例、3% 强势上涨比例、覆盖率和累计成交额。有效成员少于 3 返回 `intraday_data_insufficient`；覆盖率达到 80% 为 complete，否则为 partial。置信度按 90%/80% 分为 high/medium/low。

评分固定为：等权涨幅映射 30%、中位涨幅映射 20%、上涨比例 20%、强势上涨比例 15%、覆盖率 15%，总分限制在 0–100。

V1 无本地历史同时点基线时，预计全天成交额等于当前累计额除以实际已完成交易分钟比例。交易时段按 09:30–11:30、13:00–15:00，共 240 分钟；午休不计入，且仅在 14:30–14:55 使用。Evidence 标记 `turnover_estimated=true` 并记录方法。

不联网、不调用 Provider，不修改 `industry_score_v1`。收盘正式替换与误差统计留待 PR6.13C。

PR6.13C 已补齐同层级跨行业排名：一级、二级、三级独立排序，以 `score DESC, industry_code ASC` 稳定处理同分，返回实际参与数量、排名和百分位；覆盖不完整时降低置信解释并记录警告。
