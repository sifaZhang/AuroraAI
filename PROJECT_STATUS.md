# AuroraAI 项目状态

## 2026-08-08: Dividend candidate current-yield sorting (completed, uncommitted)

- Added candidate-list sorting by latest-year current yield and three-year average current yield through both the existing selector and clickable table headers. Header clicks start descending and toggle ascending/descending with direction arrows; the selector and headers stay synchronized. Formal-universe clickable yield-header sorting remains unchanged.
- Shortened the three annual candidate headers to `2023 DPS`, `2024 DPS`, and `2025 DPS` while retaining DPS/yield values in each cell. The 12-column candidate table now uses a fixed, responsive width allocation with wrapped long yield headers so the full table fits the desktop content area without horizontal scrolling.
- Dividend-universe API/page tests passed 7 tests; JavaScript syntax and diff checks passed. No scanner, calculation rule, provider, or database behavior changed.

## 2026-08-08: Ordinary-A-share scope correction for dividend candidates (completed, uncommitted)

- Restricted the V1 full-market universe to ordinary RMB A-share code families (`60xxxx`/`688xxx` on Shanghai and `00xxxx`/`30xxxx` on Shenzhen). This generically excludes CDRs, B shares, and other non-ordinary instruments without a symbol-specific exception.
- The master record for `689009.SH` is `board_type=UNKNOWN`, name `九号公司`, with no dedicated instrument-type column; the `689xxx` CDR range is outside the ordinary STAR `688xxx` range. The existing `unsupported_security` flag cannot be used because it is also present on ordinary shares.
- The corrected batch dry-run scanned 4,994 ordinary A shares in 113.902 seconds, found 2,907 with complete DPS and 128 qualified candidates: 37 stable monopoly, 19 resource cyclical, and 72 ordinary high-dividend watch. The only removed prior candidate was `689009.SH`.
- No corrected candidate has three-year historical average yield above 20%; the highest is `603519.SH` at 10.7034%. D2.6, the per-year 4% rule, classification, page structure, migration, and formal database were unchanged.

## 2026-08-08: High-dividend watch page integration (completed, uncommitted)

- Added migration 031 to extend only `dividend_stable_universe.stability_subtype` with `high_dividend_watch`. It was first validated on a formal-database copy, then applied to `data/aurora.db` after a size- and SHA256-verified backup. All 27 universe rows, 27 enabled flags, the 24 stable/3 resource subtype distribution, and all 81 annual DPS rows remained byte-for-byte equivalent at the SQL-row level; foreign-key validation returned zero errors.
- 031 migration 已正式应用到 `data/aurora.db`；迁移前备份位于 `backups/database/aurora_before_migration_031_20260808_150635.db`，正式库与备份在迁移前均为 475,758,592 字节且 SHA256 均为 `1577D45AF327940C593D42FC937E4BF3EA573584095CBDAFA3DA59A095258445`。
- The dividend page now reads the formal pool and the last completed candidate CSV/summary on open; it never starts a scan automatically. Price/yield refresh and full-market rescan remain separate explicit actions.
- Successful rescans atomically persist review candidates to the existing CSV/summary artifacts. Candidates never enter the formal universe automatically; confirmed additions preserve the scanner subtype and use `inclusion_source=manual_review`.
- Added three-type Chinese display, candidate summary/search/filter/sorting, existing 6%/8% yield coloring, duplicate-safe add controls, disabled running state, elapsed/completion metadata, and failure isolation from the formal pool.
- Real page-API acceptance completed in 115.419 seconds with 129 candidates: 37 stable monopoly, 20 resource cyclical, and 72 ordinary high-dividend watch. Headless Edge rendered the 27 formal rows and 129 candidates without page JavaScript failure.
- Acceptance job `37643256ccb14bfb92b16a8e0b9ca688` completed once; it used 43 paged dividend requests in 85.713 seconds. The small increase from the 81.617-second baseline was provider latency variation, not per-symbol fallback or duplicate scanning. Its temporary Uvicorn and scan processes were stopped.
- The focused dividend/migration/API suite passed 36 tests; Python compileall, JavaScript syntax, and diff whitespace checks passed. The complete repository pytest run was attempted but exceeded the 600-second command limit without producing a final result.
- Current `all_a=4995` and `normal_non_st=4995` is a valid latest-status result: 205 symbols have historical ST observations, while the latest snapshot and current names contain no ST/*ST/退 securities. No ST module expansion was made.

## 2026-08-08: High-dividend watch full-market batch dry-run (completed, uncommitted)

- Replaced impractical per-symbol/per-calendar-day dividend collection in the dry-run runner with twelve report-period `end_date` queries and 2,000-row offset pagination; exact duplicates feed the existing D2.6 lifecycle aggregation.
- Year-end and latest raw closes are collected by full-market trading-date batches with at most ten prior-session fallbacks, never per symbol. No income, cashflow, or financial-quality provider is called.
- Read-only validation scanned 4,995 normal non-ST A-shares in 111.199 seconds: 43 dividend requests (81.617 seconds), 30 year-end daily requests, 6 latest daily requests, 2,908 complete-DPS symbols, 129 qualified symbols, and zero failures.
- Output is `exports/dividend/high_dividend_watch_full_dryrun.csv` with UTF-8 BOM plus an audit summary JSON. No production database, universe, DPS summary, yield snapshot, page/API, migration, commit, or push was changed.

## 2026-08-08: D2.6 annual DPS lifecycle normalization (completed, uncommitted)

- Annual DPS now normalizes Tushare proposal, shareholder-approval, and implementation rows into dividend-plan lifecycles before aggregation. Formal DPS accepts positive `实施`, `实施方案`, and `股东大会通过` rows; proposals alone and cancelled/stopped/rejected plans remain excluded.
- The lifecycle uses report period plus chronology and payout anchors instead of amount-only deduplication. It preserves multiple distributions in one report period, including equal-amount implemented batches, and handles amount revisions before implementation.
- Targeted real-data verification produced Gree DPS of 2.38/3.00/3.00 for 2023–2025, Yangtze Power 2025 DPS of 1.00, and Sinopec 2025 DPS of 0.20. Gree's 2025 year-end unadjusted close was 40.22, giving a 7.459% yield at DPS 3.00.
- Read-only dry-run over the 27 enabled formal-pool symbols found zero stored 2023–2025 DPS changes. No production database write, scanner change/run, full-market scan, migration, commit, or push was performed.

## 2026-08-07: A-class stable dividend candidate generator (completed, uncommitted)

- Added a read-only, repeatable CLI at `backend.dividend.generate_dividend_a_candidates`; it exports review-only candidate and exclusion CSV files and never writes the production SQLite database or stock pool.
- Reuses the existing security master, latest status history, current SW industry membership, environment-backed data-source settings, and unified `TushareClient`; no token is stored in code.
- Applies centrally configured basic eligibility, three complete calendar-year cash-dividend aggregation by ex-date, deterministic event deduplication, continuity/latest-DPS checks, cautious industry mapping, stable sorting, UTF-8 BOM CSV output, and auditable exclusions.
- Offline tests cover eligibility, aggregate/deduplication behavior, continuity/ratio, industry exclusion, and dynamic years. Remaining limitation: the available SW membership is current, so classifications and business-model fit remain subject to manual review.
- Real-data validation corrected three observed implementation defects: per-symbol Tushare dividend access (rather than an unavailable unrestricted request), telecom-operator precedence over generic `通信服务`, and explicit exclusion of oil-service and B-share securities. The final read-only full run generated 86 review candidates; no database tables or stock pools were written.
- Second-phase file-only narrowing now produces `exports/dividend/dividend_a_candidates_final.csv`: 22 included, 65 review-required, and 12 explicitly excluded steel/metal entries. It preserves the first-stage CSVs, handles the explicit China Mobile listing-age exemption, and requires manual financial/concession review for road, gas, and railway candidates.
- Formal stable-dividend universe import completed after a matching read-only dry-run and a timestamped database backup. Migration 029 created the formal universe and annual DPS tables; 24 enabled securities and 72 annual 2023–2025 DPS summaries were written. Re-running the import remained idempotent (24/72, no duplicates); the pool contains 21 `stable_monopoly` and 3 `resource_monopoly_cyclical` securities.

## 2026-08-02：候选列表评分链路修复（已实现，待提交）

- 一键首板尾盘流程与 API/CLI 默认值统一使用修正后的 `first_limit_candidate_score_v2`，并删除旧策略版本兼容入口。
- 新生成的尾盘候选将写入等级和 100 分制分数；已有旧运行快照保持不变，需重新运行尾盘流程生成新快照。
- Pipeline step API、页面和 CLI 报告新增持久化耗时展示。全市场实测 #10 总耗时约 10 分 32 秒，最长为回调观察 166 秒；将逐项提交改为最多 1,000 项批量提交并保留 SAVEPOINT 失败隔离后，同范围 252 项正式复测耗时降至 0.432 秒。
- 修复 #10 的 126 个事件全部无候选：旧上下文 `is_complete=0` 被误作硬淘汰、行业缺失被误作硬淘汰、新评分等级被旧生命周期清空。分钟线同步不再扩展到 2,836 只行业成员；前置筛选也不再误用旧分类 `A1/A2/DEEP_WATCH` 匹配最终 S/A/B，而是保留非日级硬淘汰、关键基础分完整且乐观总分仍可达到 B(65) 的历史首板事件股，再只补这些股票当天分钟线。#17 当前数据对应 3 只；行业分钟覆盖不足按规则最高 B 降级，不作硬淘汰。
- 市场上下文同范围 252 项由逐项提交改为批量事务，实测从 127 秒降至 0.766 秒。
- 候选生成改为批量事务并保留逐事件 SAVEPOINT；同范围 126 个事件的正式运行由 #10 的 73 秒降至 19.845 秒。分钟线范围现已收敛为乐观总分仍可能达到 B(65) 的事件股，需重新运行尾盘流程确认最终候选数。
- 交易日历步骤改为本地优先：目标日、29 个依赖交易日及其间自然日日历均完整时直接复用，不再每次调用 GM 重拉 120 天；仅在本地缺失或断档时回退远端同步。此前约 62 秒主要是 GM 请求耗时，本地命中后只执行 SQLite 覆盖检查。
- 首板数据同步的 GM 公共重试器移除 1 秒、2 秒指数退避等待；日历、证券、状态、日线和分钟线请求失败后仍最多尝试 3 次，但立即重试，不再人为增加步骤耗时。
- 一键任务取消改为真正的终态：保留已取消 job 及步骤审计，但释放其自然唯一键；相同日期和阶段下次点击会创建新 job ID，全部步骤从 pending 重新运行，不再复用已完成步骤续跑。正在阻塞的单次 Provider 请求返回后按取消状态退出。
- 一键任务失败也改为终止的旧尝试：页面不再显示或调用“重试未完成步骤”；相同参数再次点击尾盘预警会保留旧失败审计并创建全新 job ID，所有步骤从头运行，不再复用 #17 一类失败任务。
- 修复一键任务创建后的首次轮询超时导致页面永久停在 `pending` 且没有停止按钮：创建响应返回后立即渲染任务并开放停止操作；非终态轮询暂时失败时保持运行态并每 1.5 秒继续查询，不再把后台仍在运行的任务误显示为停止。
- 服务进程退出遗留的 `running` 任务启动时改记为 `interrupted` 后不再自动续跑；页面将中断视为终态，相同参数下次手动点击释放旧自然键并创建全新任务，避免 #33 一类旧 worker 在重启后自行继续。
- 显式点击尾盘预警仅在同任务仍为 `pending/running` 时复用；`success/partial/failed/cancelled/interrupted` 均保留旧审计后创建新 pipeline。新 pipeline 以 job ID 作为 candidate execution key 创建独立候选 run，不再因相同业务参数直接复用 #35 的候选结果。
- 修复分钟线前置范围为空时同步误报 `minute sync requires --codes`：空范围现在作为 `no_daily_sab_candidates` 正常跳过，pipeline 继续进入候选生成和覆盖验证。

## 2026-08-02：PR6.13C 收盘正式确认闭环（已实现，待提交）

- 补齐申万一级/二级/三级独立尾盘横向排名、实际参与数量和百分位；同分按行业代码稳定排序。
- 新增正式收盘行业上下文、尾盘估算误差、正式行业重评分、S/A/B 变化、硬淘汰、次日计划和四类确认 Evidence；正式行业分未完成时保持 pending。
- 新增 028 最小迁移、历史回测截止保护、现有 API 契约字段、首板页面详情以及 `confirm-close`/本地流水线 CLI。
- 全部验证仅使用临时 SQLite；未联网、未调用 Provider、未写正式数据库、未修改 `industry_score_v1`、未自动交易或创建计划任务。

## 2026-08-02：PR6.13B 尾盘行业估算与新候选评分（已实现，待提交）

- 新增只读本地分钟线 `industry_intraday_score_v1`，严格限制 14:30–14:55 和截止时间，不读取 15:00，不调用 Provider。
- 新增资金活跃度、龙头、行业趋势/环境和 `first_limit_candidate_score_v1` 固定 100 分规则；S/A/B 为 85/75/65，硬淘汰优先，行业缺失最高 B。
- 027 增加最小排序字段和运行 `summary_json`；新尾盘版本只将 S/A/B 写入候选快照，淘汰数量与原因保存在运行账本，完整分项写入现有 Evidence。
- PR6.13C 的收盘正式确认、误差统计、API/页面、回测及自动编排尚未实施。本次未联网、未调用 Provider、未写正式数据库、未修改 `industry_score_v1`。

## 2026-08-02：PR6.13A 最终验收完成

- 有效行业五类回退场景、候选生成到 `daily_candidate_evidence` 的真实持久化与重读均已覆盖；候选总分未改变。
- 026 仅增加六个高频查询字段，并已通过全新临时库、旧候选表升级及四张既有表哨兵数据保持验证。
- 旧行业分不再进入新候选主流程；本次未联网、未调用 Provider、未写正式数据库，PR6.13B 尚未开始。

## 2026-08-02：PR6.13A 首板 IndustryService 上下文（已实现，待提交）

- 新增完整正式评分日期查询、三级到一级有效行业回退及 `FirstLimitIndustryContext`。
- 026 迁移仅扩展候选高频行业代码/状态字段；不写正式业务库。
- 已停用候选服务对旧 `first_limit_context_scores.industry_score` 的 `SECTOR_ENVIRONMENT` 依赖，为 PR6.13B 尾盘估算和候选评分提供入口。

## 2026-08-01：PR5.14 板块雷达每日增量刷新（已实现，待提交）

- 新增统一 `IndustryRadarRefreshService`，由 CLI、API、页面自动检查和手动按钮共用；使用本地 SQLite 优先、交易日历展开断档，并按日期升序补齐快照和 `industry_score_v1` 评分。
- 所有收盘判断使用 `Asia/Shanghai` 15:10；页面同时展示奥克兰时间，`zoneinfo` 自动处理新西兰夏令时。Task Scheduler 仅需周期性执行 `python -m backend.data_sources.cli refresh-industry-radar`，不需要硬编码本地时刻。
- 新增刷新状态/触发 API 和页面“同步到最新交易日”状态：页面打开最多自动触发一次，运行中禁用重复请求，完成后重新加载列表。
- dry-run 只读，不写行业归属、快照或评分；正式刷新只在本地日线覆盖不足时委托既有增量采集器补缺。首次评分为空时默认检查最近 30 个交易日。
- 修正为每次刷新通过统一 Tushare Provider 一次调用 `trade_cal(exchange="SSE")` 并只在内存中复用开放日；不维护本地交易日历表、不新增迁移。空响应、字段异常和 Provider 故障分别显式报告，状态接口使用短时进程内缓存。
- 离线专项测试覆盖 Tushare 日历解析、空响应、一次范围调用、时区、目标日期和断档检测。正式库联网 dry-run 已通过：目标/最新完整日期均为 2026-07-31，返回 `no_work`；未写正式业务数据库。
- 已知限制：历史评分仍用当前申万归属，首板/炸板字段可为 NULL；不提供盘中行业估算，不自动创建 Windows 计划任务。下一步：PR6.13 首板回调接入 IndustryService 与尾盘行业估算。

更新时间：2026-08-01

## 2026-08-01：PR5.10 统一市场数据访问层（已实现，待提交）

- 新增 Provider 中立的申万行业模型、契约、标准化、校验、错误与结构化审计。
- Tushare 作为默认主源，AKShare 作为备用；GM 保持实时和分钟数据职责。
- 新增只读行业健康、目录预览和股票行业查询 CLI，并扩展现有数据源健康 API。
- 自动测试完全离线，不操作正式数据库。
- 本阶段不写入申万主数据，不修改板块评分或首板策略；正式同步安排在 PR5.11。
- AKShare 三级目录可用，但三级成分接口仍受 17→18 列上游变化影响，健康状态为 degraded。
- 真实只读验证通过 Tushare 认证、权限、三级目录（336 项）和 `600519.SH` 行业归属；未写数据库。
- Tushare 全量成员存在 `002141.SZ` 双 current 三级归属冲突，当前按契约明确失败，不静默推断。

## 2026-08-01：PR5.11 当前申万行业快照（已实现，待提交）

- 新增023迁移：`industry_nodes` 和 `industry_memberships_current`，不修改旧板块历史表。
- current 归属冲突按 `in_date` 消解：同一股票先取最大日期；最大日期唯一则采用，完全重复则去重，最大日期仍有不同归属才跳过并报告冲突。
- 新增当前快照Repository和同步服务，支持批量临时表原子替换、失败回滚、幂等与force。
- 全量成员冲突由同步服务审计并跳过，正常股票继续同步并返回 `partial_success`。
- 新增dry-run、正式同步、数据库股票归属和行业成分CLI。
- 本任务默认只执行真实dry-run，不迁移或写入正式数据库。
- 本版本无历史成员关系、冲突数据库或人工覆盖。
- 真实dry-run（归属日期消解规则更新后）：497个行业节点、5924行输入、5866只可写股票、0只冲突、0只跳过，状态为 `success`；Tushare主源成功且未写数据库。
- 运行中的Uvicorn `--reload` 检测到迁移注册代码后自动执行迁移，正式库已创建两张023空表；两表行数均为0，未写入行业数据。
- PR5.11原数据源测试36项通过，健康API测试4项通过；本次归属日期消解相关测试集26项通过。完整回归仍未重新执行。

## 2026-08-01：PR5.12 申万三级行业日快照 V1（已实现，待提交）

- 新增024迁移与 `industry_daily_snapshots`，主键为交易日、分类、版本和行业代码；索引覆盖日期/层级与行业历史查询。
- 一级、二级、三级共用客观快照引擎，指标包括成员与有效样本、停牌、覆盖率、等权/中位收益、涨跌平、3%强势上涨、权威涨跌停、首板/炸板能力、成交额及数据状态。
- 新增单日、日期范围、dry-run、force、非交易日跳过、单行业失败隔离、幂等写入及只读查询 CLI/Repository。
- 首板或炸板事件覆盖不足时保存 `NULL`，不把缺失当作0；所有历史日期暂用当前申万归属，不能还原历史成分。
- 离线专项10项通过，最终 `tests/data_sources` 51项通过；compileall通过。额外交易规则与日线Repository回归受Windows pytest临时目录挂起影响，没有可宣称的完整终态。
- 正式库只读验证明确返回 `current_industry_snapshot_unavailable`，因为PR5.11正式表仍为0行；运行中的Uvicorn reload已自动执行024迁移并创建0行快照表，但未写入PR5.11归属或PR5.12快照数据。
- 使用正式库副本同步Tushare后，对2026-07-30真实dry-run：497个行业（31/130/336），complete 460、partial 2、insufficient 32、empty 3、失败0；唯一缺失行情股票1只；首板/炸板完整覆盖行业均为0。
- 下一步 PR5.13：行业评分 V1。

## 2026-08-01：PR5.13 行业评分与板块雷达收官（已实现，待提交）

- 新增025迁移和固定 `industry_score_v1`：100分七维评分、5/20日成交比、中位成交保护、量价状态、持续性、数据质量、置信度及三级独立稳定排名。
- 新增评分Repository/Service、统一IndustryService、股票三级行业上下文、行业树/列表/详情/历史/成分查询；所有业务查询只读本地SQLite，不调用Provider或个股日线。
- 新增 `/api/industry/tree|list|detail|history|context|constituents`，NULL保持NULL；原 `/api/market-pulse` 保留兼容。
- 升级原 `/market-pulse.html`：默认二级，支持一级/二级/三级切换、搜索、排序、详情、量价与成交比、覆盖率和置信度；NULL显示“—”。
- 固定规则文档为 `docs/development/industry_scoring_rules.md`；规则或阈值变化必须发布新评分版本。
- 专项Python测试9项、页面兼容测试4项通过；最终数据源、行业API/页面与旧Market Pulse组合回归67项通过，旧JavaScript mock测试通过；compileall与diff检查通过。
- 临时副本真实验证联网同步Tushare，生成22个交易日、10,934条评分；2026-07-30三级31/130/336且排名完整独立。量价状态：放量上涨68、缩量上涨7、放量下跌34、缩量下跌124、平量震荡229、历史不足35；贵州茅台上下文complete。
- 正式库未写行业归属、快照或评分业务数据；运行中的Uvicorn reload已自动创建025评分空表（0行）。
- 下一步：PR6.13。

## 2026-08-01：板块雷达正式数据库初始化（已完成）

- 已对正式 SQLite 数据库 `data/aurora.db` 执行非 dry-run 的 `sync-industries`，写入申万2021行业节点497条、股票当前行业归属5866条；同步状态为success，未使用备用源，未发生股票归属冲突或写入失败，仅按既有规则排除非普通股票标的 `T00018.SH`。
- 抽查正式归属正确：`600519.SH` 为食品饮料/白酒Ⅱ/白酒Ⅲ，`300750.SZ` 为电力设备/电池/锂电池，`000001.SZ` 为银行/股份制银行Ⅱ/股份制银行Ⅲ。
- 使用项目现有中国交易日历，并以正式库最新可用日线日期约束结束日，自动确定最近30个交易日为 `2026-06-18` 至 `2026-07-30`；未硬编码一组固定日期。
- 已对上述范围正式执行 `build-industry-snapshots` 和 `build-industry-scores`：快照14910条、评分14910条，均覆盖30个交易日和每日497个行业，一级/二级/三级分别为31/130/336；评分排名在各层级内完整连续。
- 快照生成没有行业执行失败。最早交易日 `2026-06-18` 因前收盘历史元数据不足，多数快照状态为insufficient；首板/炸板事件数据目前无完整覆盖，因此相关字段保存为NULL。这些是数据完整性状态，不是写入失败，评分仍按既定降级规则生成。
- 正式 CLI 查询验证通过：`db-industry-scores` 能返回2026-07-30行业排名，`db-symbol-industry-context` 能返回贵州茅台完整三级上下文（三级均为同层第1名）。
- 运行中项目的真实HTTP接口验证通过：一级31、二级130、三级336；默认二级数据和排名正常；搜索“白酒”返回白酒Ⅱ、非白酒；白酒Ⅱ详情可打开。行业API与页面专项测试2项通过。
- 页面结构与交互验证通过：默认二级、一级/二级/三级切换、搜索、排序、行点击详情均已接线；NULL字段由统一格式化逻辑显示为“—”。
- 本次明确写入正式数据库；除上述历史元数据不足和NULL覆盖状态外，没有同步、快照、评分或API异常。

## 2026-08-01：板块雷达列表列宽调整（已完成）

- 删除与上方一级/二级/三级页签重复的“层级”列，列表由12列调整为11列。
- 同步修正加载状态和空状态的跨列数；行业表格最小宽度最终收窄至1240px，“父行业”和“评分/排名”固定为76px和92px，使右侧量价、覆盖率和置信度字段更容易显示。
- 表格上方新增与底部联动的水平滚动条，窗口尺寸变化和数据重绘后会重新匹配实际表格宽度。
- 行业体系说明栏右侧新增“数据日期”，使用行业列表API返回的实际评分交易日，不使用浏览器当前日期；窄屏自动换行。
- 行业页面与API专项测试2项通过，`industry-radar.js` Node语法检查通过。

## 2026-08-01：板块雷达2026-07-31正式数据增量（已完成）

- 通过GM正式同步2026-07-31中国开市日历、未复权A股日线和证券日状态元数据；写入日线5193条、状态元数据5199条。
- 首次快照发现缺少当天`pre_close`导致收益指标不足，已先补齐证券日状态后强制重建，未使用不完整结果生成最终评分。
- 正式写入2026-07-31行业快照497条、行业评分497条；快照状态为complete 460、partial 2、insufficient 32、empty 3，缺失行情4只，行业执行失败0。
- 实时行业列表API返回最新交易日2026-07-31，二级行业130项且排名从1开始；页面数据日期会自动显示2026-07-31。

## 当前状态

## 2026-08-07: PR-D1 dividend universe management (implemented, uncommitted)

- Added `/dividend/universe` and its SQLite-backed API for listing, searching, manual validation/addition, enable/disable, and advisory candidate rescans.
- Manual additions preserve the existing implemented-cash-dividend, `ex_date`-year aggregation rules and require three positive annual DPS values plus explicit warning acknowledgement.
- Candidate rescan results are intentionally process-memory-only in D1; scans never alter the formal universe automatically.
- No prices, yields, mail, Hong Kong shares, first-limit behavior, or database schema changes are included.

本次形成一个可回退的稳定检查点，重点修复并优化了 A 股预期差刷新和首板尾盘预警数据链路。

### 已完成

- 统一从项目 `.env` 读取 GM Token 及数据路径，补充 `.env.example`。
- A 股预期差刷新支持原数据源失败后回退至掘金 GM，并改进无数据、网络错误和非 JSON 响应提示。
- SQLite 写操作统一串行化，锁冲突返回明确业务错误，降低并发请求导致的 500。
- 尾盘预警允许在所选交易日北京时间 14:30 后运行。
- 增加一键任务取消、取消状态查询及已取消任务重新运行支持。
- 优化全市场证券状态和日线同步：
  - 仅补抓缺失区间；
  - GM 批量请求并按交易时段拆分分钟线；
  - 批量写入数据库；
  - 首板检测预加载数据，避免逐股票重复查询。
- 缺失目标日线时记录为 `indeterminate`，不再导致整个检测任务失败。
- 根据 GM 历史证券名称识别 `ST`、`*ST` 和名称含“退”的风险股票；状态重新同步并重新生成候选后会淘汰这些股票。
- 候选列表显示股票名称、候选分数和首板事件 ID；候选详情显示候选总分。

### 已验证

- Python 全量测试：364 passed（性能优化完成后）。
- 最新状态同步/API针对性测试：33 passed。
- 首板页面前端 mock 测试：passed。
- 隔离数据库全市场一键流程约 3 分 42 秒完成，0 个执行失败；实际时间仍受 GM 服务和本地数据缺口影响。

## 已知限制与下一步

候选行业环境和市场环境评分尚未完整接入：

- 行业环境 20 分目前缺少交易日有效的申万一级行业映射，以及同日行业趋势、宽度、涨停共振、排名和排名变化的正式接线。
- 市场环境 10 分目前缺少沪深 300、中证 1000 均线状态，以及同日全市场涨停/跌停数量与覆盖率的正式接线。
- `score_first_limit_context` 当前会将上述两组输入标记为不可用，因此数据不完整的候选显示分数 `—` 且不生成 `S/A/B` 等级。这是防止使用未来数据或伪造分数的保护行为。
- 建议下一个独立 PR 实现历史时点行业归属、行业/市场数据采集与上下文评分接线，并加入防未来数据、覆盖率和评分边界测试。
- 已保存的旧候选快照不会因代码更新自动变化。ST 排除、名称和完整评分等变更需要重新同步相关数据并重新生成候选后才会体现在新结果中。
