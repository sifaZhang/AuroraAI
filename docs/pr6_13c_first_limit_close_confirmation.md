# PR6.13C 收盘正式确认闭环

本次在 PR6.13A/B 单一评分体系上增加跨行业尾盘排名、正式收盘行业上下文、尾盘估算误差、最终重评分、变化记录、次日计划、回测边界、API/页面字段和本地 CLI。

跨行业排名按申万一级、二级、三级分别计算，固定以 `score DESC, industry_code ASC` 排序；只报告实际参与数量、百分位和部分覆盖警告，不虚构 31/130/336 的完整数量。

028 为候选快照增加尾盘总分/等级、正式行业分/排名、最终总分/等级/建议、确认状态/变化类型和确认时间；回测信号增加尾盘等级、最终等级、变化和次日结果。详细内容仍进入 `OFFICIAL_CLOSE_INDUSTRY`、`INDUSTRY_ESTIMATION_ERROR`、`CLOSE_CONFIRMATION`、`CANDIDATE_CHANGE` Evidence。

API 复用 `/api/first-limit` 现有候选列表和详情，新增字段全部允许 NULL 以兼容旧记录。页面继续复用现有首板页面及 `tail_preview`/`close_confirmed` 阶段切换，详情显示尾盘、正式和最终结果。

CLI：`python -m backend.strategy.first_limit.cli confirm-close --trade-date YYYY-MM-DD [--symbol ...] [--dry-run]`。统一入口还支持 `run-daily-pipeline --stage intraday|close-confirmation`；不会创建操作系统计划任务。

全部验证使用临时 SQLite，未联网、未调用 Provider、未写正式 `data/aurora.db`、未修改 `industry_score_v1`、未自动下单、未创建系统计划任务。
