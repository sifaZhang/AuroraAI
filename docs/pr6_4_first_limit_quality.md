# PR6.4 首板质量 20 分

PR6.4 只对 PR6.3 已确认的正向首板事件评分；不生成候选、不做回调分层、回测、API、页面或交易。

评分版本由 `scoring_version` 隔离。主表保存事件关联、检测/评分版本、已得分、理论上限 20、可确定上限、覆盖率、完整性、近似标记与原因；分项表保存原始输入、公式版本、来源、分项状态和原因。`earned_score` 只累计确定分项，不能把缺失或不确定项当作 0 分。

日线可确定分项为首板前 20 个交易日位置、前 5 个交易日成交量放大、T0 成交额和 K 线形态。金额统一按 `a_share_daily_bars.amount` 的元单位解释。换手率在当前没有权威输入时为 `missing`，不是 0 分。

行业输入只能使用 T0 同日的 `sector_scores.trend_score`。当前行业成分表是当前快照：其历史使用会显式标为 `approximate`，不得增加确定性总分；无可证明历史映射或权威同业涨停集合时，相应分项为 `indeterminate`。分钟线封板、炸板和封单指标不属于本 PR。

CLI：`python -m backend.strategy.first_limit.score_first_limit_quality --trade-date YYYY-MM-DD --codes 600000.SH --dry-run`。dry-run 用只读 SQLite 连接且不写 run、score 或 component；退出码 0/1/2 表示完整成功、部分/不确定、运行级错误。
