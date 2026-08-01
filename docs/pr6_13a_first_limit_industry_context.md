# PR6.13A 首板行业上下文

PR6.13A 已完成首板模块对本地 `IndustryService` 的基础接入。本阶段不改变候选总分，不包含 PR6.13B 的尾盘行业估算、资金活跃度、龙头评分、新 100 分规则或页面/API 改造。

## 完整评分日与日期接口

完整评分日要求申万一级、二级、三级的行业节点数、当日快照数和指定 `score_version` 评分数逐层相等且均大于零。服务提供最新、上一、下一、最近评分日以及完整性判断；周末和中间断档通过已有完整日期跳过。

## 有效行业回退

默认使用三级。三级或二级只有在存在正式快照和评分、评分置信度为 `high`/`medium`、有效样本不少于 8 且覆盖率不少于 80% 时才可用；否则逐级回退。一级存在正式快照和评分即可兜底，仍缺失则明确返回 `unavailable`。结果携带有效层级、代码、名称、评分、排名、置信度及 `fallback_reason`。

## 候选 Evidence 与 026

候选生成主路径把 `FirstLimitIndustryContext.evidence()` 交给 `save_candidate()`，保存为现有 `daily_candidate_evidence` 的 `INDUSTRY_CONTEXT` 项并可重新读取。JSON 包含申万一级/二级/三级归属，有效行业，首板日及上一完整评分日的评分/排名，以及状态、置信度和回退原因。

026 只为 `daily_candidate_snapshots` 增加六个高频查询字段：`sw_level1_code`、`sw_level2_code`、`sw_level3_code`、`effective_industry_level`、`effective_industry_code`、`industry_context_status`。没有新增证据表或大量重复评分列。

迁移已在全新临时 SQLite 数据库和迁移前旧候选表上验证。旧候选记录以及 `first_limit_events`、`first_limit_minute_bars`、`first_limit_sync_runs`、`backtest_parameter_results` 的哨兵内容保持不变；重复迁移由项目现有迁移检查防重。

## 旧逻辑与边界

新候选主流程的 `SECTOR_ENVIRONMENT` 不再读取 `first_limit_context_scores.industry_score`，旧行业趋势、广度和排名实现仅为历史入口兼容保留并视为 deprecated，不由新候选主流程调用。

本次验收完全离线，未调用 Provider，未访问或写入正式 `data/aurora.db`，也未实施任何 PR6.13B 功能。
