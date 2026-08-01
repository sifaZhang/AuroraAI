# PR5.13 行业评分与板块雷达 V2

新增025评分表、固定 `industry_score_v1`、量价/持续性分析、三级独立排名、评分Repository、统一IndustryService、六个本地只读API、评分CLI，并升级原 `/market-pulse.html`。API和页面不调用外部Provider；本地缺失时返回空结果或明确状态。

CLI：`build-industry-scores --date/--start-date --end-date [--level] [--dry-run] [--force]`、`db-industry-scores`、`db-symbol-industry-context`。API位于 `/api/industry/tree|list|detail|history|context|constituents`。

页面默认二级，可切换三级，支持搜索和排序；列表展示评分、排名、收益广度、涨停/首板、成交额、5/20日成交比、量价状态、覆盖率和置信度，NULL显示“—”，点击行业打开详情。

限制：所有历史仍使用当前申万归属；首板覆盖不足时为NULL；正式库必须先具备PR5.11归属和PR5.12快照。固定评分详见 `docs/development/industry_scoring_rules.md`。

真实验证使用正式库临时副本并联网调用Tushare同步当前归属，未写正式业务数据。副本生成2026-07-01至07-30共22个交易日的497行业快照和10,934条评分。2026-07-30三级分别为31、130、336条，排名各自完整覆盖1–31、1–130、1–336；量价状态包含放量上涨68、缩量上涨7、放量下跌34、缩量下跌124、平量震荡229、历史不足35。贵州茅台本地上下文状态complete，二级行业评分可查询。497个行业首板字段均为NULL，API与页面保持“—”。
