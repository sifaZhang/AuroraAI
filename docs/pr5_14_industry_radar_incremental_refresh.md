# PR5.14 板块雷达每日增量刷新

## 目标与原则

`IndustryRadarRefreshService` 是 CLI、API 和页面共用的唯一刷新入口。查询始终读取本地 SQLite；只有刷新流程发现行业成员或日线缺失时，才委托既有采集器补齐，不让浏览器或查询 API 访问 Provider。

## 时间、交易日历和目标日

业务判断统一使用 `Asia/Shanghai`。每次刷新通过统一数据源层的 Tushare `trade_cal(exchange="SSE")` 做一次范围查询，读取 `cal_date`、`is_open` 和 `pretrade_date`，仅在内存中保留开放日；不维护、不新增本地交易日历表或迁移。北京时间 15:10 后可包含当天，否则查询上限为前一自然日；周末和节假日由 `is_open=0` 过滤。

空响应、字段变化、网络/权限失败分别报告 `trading_calendar_empty`、`trading_calendar_schema_error`、`trading_calendar_unavailable`，绝不把 Provider 故障误判为休市。奥克兰时间仅展示，并由 `zoneinfo` 自动处理夏令时。

Windows Task Scheduler 仅周期性唤醒程序。程序内部统一使用 `Asia/Shanghai` 判断A股是否收盘和应更新到哪个交易日，因此不受新西兰夏令时切换影响。

推荐在奥克兰本地时间每周一至周五 19:00 开始、每 30 分钟一次、持续 3 小时执行：

```powershell
python -m backend.data_sources.cli refresh-industry-radar
```

不会自动创建或修改 Windows 计划任务。

## 断档、幂等和数据质量

服务用该次 `trade_cal` 返回的开放日展开从起点到目标日的全部日期，逐日升序检查三级行业节点、快照、固定版本 `industry_score_v1` 的评分和排名完整性。完整日期跳过；只有评分缺失时只补评分；其他不完整日期重建当天快照和评分。默认在某天失败时停止，`--continue-on-error` 仅用于排查。`--force` 重建指定范围，不强制刷新行业归属。

首次没有行业评分时默认查看最近 30 个交易日；`--start-date` 可指定更早起点。正式写入前检查当前行业成员的本地日线覆盖；不足时复用现有增量日线采集器，仅补缺口。无法补齐时明确失败，不把缺失数据视为零。

`--dry-run` 允许联网调用一次只读的 Tushare `trade_cal`，以完成必要的目标日判断；不写数据库、不拉取全市场数据。

## 接口和页面

- `GET /api/industry/refresh-status` 返回北京时间、目标日、最新完整日、缺失日期、运行状态和错误；只读本地数据库。
- `POST /api/industry/refresh` 启动一次后台刷新；重复请求返回已有运行状态。
- 打开 `market-pulse.html` 时只检查一次：已最新直接加载，落后时自动触发同一 API；失败不无限重试。
- “同步到最新交易日”按钮调用同一 API，运行中禁用并轮询状态。

## CLI

```powershell
python -m backend.data_sources.cli refresh-industry-radar --dry-run
python -m backend.data_sources.cli refresh-industry-radar --target-date 2026-08-05
python -m backend.data_sources.cli refresh-industry-radar --start-date 2026-07-31 --target-date 2026-08-05 --force
```

支持 `--dry-run`、`--force`、`--refresh-memberships`、`--continue-on-error`。退出码为 0（success/no_work）、1（partial_success/already_running）、2（failed）。

## 测试与限制

离线测试覆盖 Tushare 日历字段解析、空响应、一次范围调用、北京时间收盘边界、奥克兰标准/夏令时转换、日期完整性及交易日断档检测。状态接口使用 10 分钟进程内缓存，避免页面轮询重复请求日历。历史评分仍使用当前申万归属；首板/炸板字段可为 `NULL`；不提供盘中估算，也不修改首板策略。自动刷新要求 AuroraAI 后端和电脑（或未来部署环境）正在运行。
