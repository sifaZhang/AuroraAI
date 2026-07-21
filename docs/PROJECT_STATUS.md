# AuroraAI Project Status

Last Updated: 2026-07-22

---

# Project Overview

AuroraAI 是一个本地运行的 AI 股票研究平台。

长期目标：

1. A股 / 港股估值研究
2. Market Pulse（板块趋势雷达）
3. Market Breadth（板块广度）
4. 尾盘选股系统
5. 创新药事件驱动估值
6. 本地股票历史数据库
7. AI Stock Research Platform

当前开发阶段：

> Phase 5.4C - SW Level-1 Sector History Sync Completed

---

# Technology Stack

Backend

- Python
- FastAPI
- SQLite
- AKShare
- Futu OpenD

Frontend

- HTML
- CSS
- JavaScript

Deployment（未来）

- Frontend：Vercel
- Backend：Render / FastAPI Cloud
- Database：SQLite（当前）→ PostgreSQL（后期）

---

# Git Workflow

Development Branch

feature/expectation-refresh-jobs

规则：

- 小PR
- 本地测试通过后提交
- 不直接push main
- 用户文件（如板块雷达.md）禁止自动提交

---

# Completed Features

## Dividend Ranking

完成。

支持：

- 平均股息率
- 平均ROE
- 平均分红
- 连续分红
- 综合评分

---

## Expectation Gap

完成。

A股：

- 手工CSV导入

港股：

- Morningstar Fair Value
- Analyst Target Price
- Futu OpenD

支持：

- 排序
- 筛选
- 分页

---

## Market Pulse

### Technical Trend

70分。

组成：

- Close > MA5
- MA5 Rising
- MA5 > MA10
- MA10 > MA20
- 20-day Closing High
- Volume Expansion

---

### Relative Strength

15分。

Benchmark：

CSI300

比较：

- 5 Day
- 10 Day
- 20 Day

总分：

15

---

当前评分：

Technical Trend

70

Relative Strength

15

Capital Flow

NULL（待替换）

Composite

NULL

---

# Completed PRs

## PR5.1

Relative Strength

Status：

Completed

Commit：

a6f1ef1

---

## PR5.2

sw_l1 Parallel Refresh

Status：

Completed

Commit：

b42d167

性能：

| Worker | Time |
|--------|------|
| 1 | 68.54 s |
| 2 | 31.38 s |
| 4 | 19.96 s |

默认：

MARKET_PULSE_SW_WORKERS=4

---

## PR5.3

Market Breadth Probe

Status：

Completed

Commit：

5d137d7

验证结果：

- 31行业全部成功
- 成分股5199只
- 逻辑可行
- 五项Breadth指标全部可计算

结论：

逐股票联网约40分钟。

生产环境必须建立本地历史数据库。

---

## PR5.4A

Status：

Completed

Commit：

f85ebc6

Summary：

新增：

- A-share Daily Repository
- History Sync Status Repository
- SQLite Migration
- Repository数据模型
- 幂等Upsert
- Window Function批量查询

数据库：

新增表：

- a_share_daily_bars
- a_share_history_sync_status

性能：

25,000行：

首次Upsert：

0.94秒

重复Upsert：

0.72秒

100股票最近21日查询：

0.064秒

说明：

Market Breadth、尾盘选股、回测等后续统一使用该Repository。

---

## PR5.4B

Status：

Completed

Commit：

本次提交（feat: add resumable A-share history sync engine）

Summary：

- 支持全市场历史行情初始化
- 按本地最后交易日增量同步
- 断点续跑和幂等写入
- 失败隔离、状态记录及仅重试失败股票
- 下载线程与SQLite写入严格分离
- 默认2线程，最大8线程
- 股票池主接口失败时回退申万一级行业成分

命令：

```powershell
python -m backend.collector.sync_a_share_history --limit 10
python -m backend.collector.sync_a_share_history --retry-failed
python -m backend.collector.sync_a_share_history --codes 000001,600000 --workers 2
```

真实小样本：

- 2只股票同步成功
- 28条日线写入
- 重复运行正确跳过已是最新的数据

---

# Current Architecture

Market Pulse

```
Probe
    │
    ▼
SQLite
    │
    ▼
Health
    │
    ▼
Refresh API
    │
    ▼
Dashboard
```

未来：

```
AKShare
    │
    ▼
History Sync Engine
    │
    ▼
SQLite Daily Bars
    │
    ▼
Market Breadth
    │
    ▼
Market Pulse
    │
    ▼
Dashboard
```

---

# Current Database

已有：

- sector_scores
- sector_source_status
- a_share_daily_bars
- a_share_history_sync_status

---

# Current Roadmap

## PR5.4B

Historical Sync Engine

Status：Completed

目标：

建立A股历史行情同步引擎。

包括：

- 全市场初始化
- 增量更新
- Sync Status
- 断点续跑
- 自动恢复失败
- 有限并发下载
- Repository接入

不包括：

- Breadth正式评分
- API修改
- 前端修改

---

## PR5.5（Next）

Market Breadth Production

包括：

- Breadth Score
- Composite Score
- Dashboard展示
- 替代Capital Flow

---

## 后续规划

### Tail Trading

尾盘选股系统

包括：

- 历史训练
- AI排序
- 特征工程
- 回测

---

### Innovation Drug Center

创新药估值中心

包括：

- BD
- NDA
- Phase III
- FDA
- Event Timeline

---

### AI Research

统一研究中心。

---

# Design Principles

始终坚持：

- 不伪造数据
- 小PR
- 可重复测试
- SQLite写入仅主线程
- 单股票失败不能影响整体
- Migration只新增
- API保持兼容
- Repository负责数据访问
- Collector负责联网获取
- 业务逻辑与数据层解耦

---

# Current Constraints

AKShare：

仍有部分接口存在上游问题：

- sw_l2
- eastmoney

当前Market Pulse生产数据：

使用：

sw_l1

Market Breadth：

逻辑已验证。

瓶颈：

缺少本地历史缓存。

PR5.4A已解决数据层。

PR5.4B已完成初始化、增量同步、断点续跑与失败恢复引擎。

下一步将Market Breadth接入本地历史缓存并进入生产评分。

---

## PR5.4C

Status: Completed

Scope:

- Official V1 classification source: `sw_level1`
- SW level-1 industry metadata
- Industry historical daily bars
- Current constituent snapshots with `snapshot_date`, `first_seen_at`, and `last_seen_at`
- Explicit approximate/look-ahead warning for historical use of current membership
- Incremental, idempotent, resumable synchronization with retry and failure isolation
- Conservative limited concurrency; SQLite writes remain on the main thread
- Schema keeps `classification_system` for future `eastmoney_industry` and `sw_level2` support

Command:

```powershell
python -m backend.collector.sync_sector_history --limit 1 --workers 1
python -m backend.collector.sync_sector_history --codes 801010 --workers 1
python -m backend.collector.sync_sector_history --retry-failed
```

Real validation (one industry only):

- Classification: `sw_level1`
- Sector: `801010`
- Historical bars: 6416
- Current snapshot members: 104
- Latest trade date: 2026-07-21
- Snapshot date: 2026-07-22

No full-market sector history synchronization was run.

---

# Files Never Touch Automatically

以下文件属于用户维护：

- 板块雷达.md

除非用户明确要求，否则不得修改、暂存或提交。

---

# Current Branch

feature/expectation-refresh-jobs

---

# Next Task

PR5.5

Market Breadth Production

目标：

基于本地A股日线缓存计算Breadth并接入Market Pulse生产评分。
