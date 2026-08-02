# PR6.13B 尾盘行业估算与候选评分

本次新增离线尾盘行业估算、资金活跃度、行业龙头、行业趋势/环境和固定 100 分候选评分。一键正式尾盘流程和底层运行器统一使用 `strategy_version=first_limit_candidate_score_v2`。

候选硬淘汰只检查首板分、回调分等真正关键输入；旧上下文因已弃用行业/市场分项而 `is_complete=0` 时不得整批淘汰。行业映射或盘中行业估算缺失按规则最高 B，不作为硬淘汰。一键分钟线范围包含候选所在有效行业的成员，不只下载候选自身分钟线。

027 为候选快照增加 `effective_score`、`effective_rank`、`capital_activity_score`、`leader_score`、`industry_trend_score`、`industry_environment_score`、`buy_recommendation`、`scoring_version`，运行账本增加 `summary_json`。既有 `score` 和 `candidate_grade` 分别承载总分与等级，避免重复同义列。

Evidence 使用 `INTRADAY_INDUSTRY_ESTIMATE`、`CAPITAL_ACTIVITY`、`LEADER_SCORE`、`INDUSTRY_ENVIRONMENT`、`CANDIDATE_SCORE`，并保留 PR6.13A 的 `INDUSTRY_CONTEXT`。运行汇总包含 scanned/candidate/S/A/B/eliminated 数量和淘汰原因统计；幂等运行仍沿用参数哈希和现有账本。

调试入口：`python -m backend.strategy.first_limit.cli score-candidate --symbol 600519.SH --trade-date 2026-07-31 --as-of-time 14:30`。CLI 只读，不迁移、不写库。

本次测试只使用临时 SQLite，未联网、未调用 Provider、未写正式 `data/aurora.db`、未修改 `industry_score_v1`、未执行自动交易。收盘正式行业确认、估算误差、最终 API/页面、回测和自动编排属于 PR6.13C。
