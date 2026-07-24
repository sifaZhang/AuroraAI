# AI Investment Research Platform

> An AI-powered investment research platform that automatically collects market data, analyzes financial reports, evaluates investment opportunities, and generates professional daily research reports.

---

## 🚀 Vision

Most investors spend hours every day reading:

- Financial reports
- Dividend announcements
- Institutional research
- Industry news
- Company announcements

This platform automates the entire research workflow using AI.

Instead of reading hundreds of announcements manually, users receive one concise AI-generated report every day.

---

# ✨ Features

## 📈 Mid-Year Golden 20

Automatically identify the Top 20 companies with the highest probability of outperforming during earnings season.

Evaluation includes

- Earnings Surprise
- Valuation
- Industry Trend
- Institutional Expectations
- Capital Flow

---

## 💰 Dividend Top20

Automatically collect

- Dividend Announcement
- Record Date
- Ex-dividend Date
- Cash Dividend

Calculate

Dividend Yield

Formula

Dividend Yield

=
Cash Dividend per 10 Shares
÷10
÷Current Stock Price
×100%

Sort all companies by dividend yield.

---

## 📅 Investment Calendar

Generate daily investment calendar

Including

- Earnings Reports
- Dividend Record Dates
- Ex-dividend Dates
- Economic Events
- Industry Events
- Government Policies

---

## 🤖 AI Investment Score

Every stock receives a score

| Category | Weight |
|-----------|-------:|
| Earnings | 30 |
| Valuation | 20 |
| Industry Trend | 20 |
| Dividend | 15 |
| Institutional Rating | 15 |

Total Score

100

---

## 📰 AI Daily Report

Automatically generate

- Markdown
- HTML
- PDF

Future

- WeChat
- Telegram
- Email

---

## 🔍 AI Research Agent

Future AI Agents

- Earnings Agent
- Dividend Agent
- News Agent
- Industry Agent
- Risk Agent
- Portfolio Agent

---

# 🏗 System Architecture

```text
                         GitHub

                            │

                    GitHub Actions

                (Automatic Scheduler)

                            │

       ┌────────────────────┼─────────────────────┐

       ▼                    ▼                     ▼

 Dividend Data       Earnings Reports       News

       │                    │                     │

       └────────────────────┼─────────────────────┘

                            ▼

                   Data Processing Layer

                            ▼

                      PostgreSQL

                            ▼

                     AI Analysis Engine

               OpenAI / DeepSeek / Gemini

                            ▼

                Report Generation Service

                            ▼

          Markdown / HTML / PDF / Website

```

---

# 🛠 Tech Stack

## Backend

- Python
- FastAPI

---

## Database

- PostgreSQL

---

## Data Sources

- AKShare
- Tushare Pro
- Eastmoney
- Shanghai Stock Exchange
- Shenzhen Stock Exchange

---

## AI

- OpenAI
- DeepSeek
- Gemini (Future)

---

## Frontend

- React
- Vite

---

## Deployment

- GitHub
- GitHub Actions
- GitHub Pages
- Docker
- Railway
- Cloudflare

---

# 📂 Project Structure

```
AI-Investment-Research/

│

├── README.md

│

├── backend/

│     collector/

│     analysis/

│     report/

│     scheduler/

│     api/

│

├── frontend/

│

├── database/

│

├── docs/

│

├── deployment/

│

├── prompts/

│

├── tests/

│

└── .github/

      workflows/

```

---

# ⚙ Daily Workflow

08:00

↓

GitHub Actions

↓

Collect Market Data

↓

Collect Dividend Data

↓

Collect Earnings Reports

↓

Collect News

↓

AI Analysis

↓

Generate Reports

↓

Deploy Website

↓

Done

---

# 📊 Planned Reports

## Daily

- Mid-Year Golden20

- Dividend Top20

- Investment Calendar

- Industry Ranking

---

## Weekly

- Industry Trend

- Institutional Research Summary

- Portfolio Review

---

## Monthly

- Market Review

- Earnings Summary

- Dividend Summary

---

# 🗺 Roadmap

## Version 1.0

- Dividend Top20
- Mid-Year Golden20
- Investment Calendar
- Markdown Report

---

## Version 2.0

- AI Stock Scoring
- Industry Ranking
- Institutional Rating

---

## Version 3.0

- Portfolio Management
- Risk Management
- Email Report
- Telegram

---

## Version 4.0

- Multi-Agent System
- AI Chat
- Portfolio Assistant

---

# 🌐 Deployment

The platform is designed to run completely in the cloud.

```
GitHub

↓

GitHub Actions

↓

Python

↓

Generate Reports

↓

Commit

↓

GitHub Pages

↓

Website
```

No local computer is required.

---

# 📅 Future Plans

- Hong Kong Market

- US Market

- Cryptocurrency

- Quantitative Strategy

- Strategy Backtesting

- Portfolio Optimization

- Mobile App

---

# 📄 License

MIT License

---

# 👨‍💻 Author

Sifa Zhang

Auckland, New Zealand

Built with ❤️ using Python, AI and Cloud Technologies.

---

## 预期差 V1（本地运行）

V1 使用 SQLite：港股价格与评级通过本机富途 OpenD 自动采集；A股仅包含手工CSV维护的重点股票，价格通过 AKShare/东方财富更新。

```powershell
python -m pip install -r requirements.txt
python -m backend.collector.import_manual_a_share_valuations --file data/manual_a_share_valuations.csv
python -m backend.collector.collect_expectations --codes HK.00700,HK.09988,HK.03690
python -m backend.collector.refresh_expectations --market all
python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/expectation-gap`。A股刷新命令只更新数据库中由CSV导入的股票，不遍历全部A股；港股初始化必须显式提供代码，本命令不会自动执行全市场初始化。

手工文件使用 UTF-8-SIG 编码，字段如下：

```text
futu_code,name,morningstar_fair_value,morningstar_star_rating,analyst_average_target,analyst_count,data_date,source,note
```

运行测试：

```powershell
python -m pytest -q tests -p no:cacheprovider
```

### 页面刷新任务与每周港股评级

启动本地服务后，预期差页面提供“刷新A股”“刷新港股股价”“刷新港股评级”三个后台任务按钮。HTTP 请求会立即返回任务编号，页面每2秒显示进度；关闭或刷新页面不会中止后台线程。为保护 SQLite 和 OpenD，同一时间只允许一个刷新任务运行。

服务仅绑定本机：

```powershell
python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

每周港股评级也可由 Windows 任务计划程序调用：

```powershell
cd F:\Stock\Projects\AuroraAI
python -m backend.collector.refresh_weekly_hk_ratings
```

### Market Pulse 每日增量刷新

PR5.9 将行业历史、当前成分快照、成分股日K增量和当日 Breadth 计算串成一个可重复运行的任务：

```bash
python -m backend.collector.refresh_market_pulse_daily --workers 2
```

任务只同步申万一级31行业当前成分范围，不扩展到申万二级、三级或行业外股票。成分股日K继续使用
`sina_stock_zh_a_daily`、不复权，并从各股票已有最后交易日附近增量补齐。目标日期取31行业成功记录中的共同最新交易日，
避免不同日期的行业分数混排。

成功运行后，Market Pulse API 会返回：

```text
previous_trade_date
previous_total_score
total_score_change
trend_score_change
breadth_score_change
```

页面总分下方用红色上箭头、绿色下箭头或横箭头显示相对上一条 `breadth_v1` 记录的变化。首次产生分数、
没有历史基线时变化字段保持为空。

建议每周日或周一的非交易时间运行一次。命令复用页面任务的 TTL/resume 机制，不使用 force；如果已有冲突任务，会安全退出。本项目不会自动修改 Windows 任务计划程序。

### 港股初始化（阶段D）

先只查看富途港股证券池统计，不写入数据：

```powershell
python -m backend.collector.init_hk_expectations --dry-run
```

显式限制样本数量后初始化；不提供 `--limit` 或 `--codes` 时程序会拒绝执行：

```powershell
python -m backend.collector.init_hk_expectations --limit 100
python -m backend.collector.init_hk_expectations --limit 100 --resume
```

可选参数包括 `--force`、`--codes HK.00700,HK.09988`、`--only-unrated` 和 `--include-reit`。默认排除可识别的REIT名称，研究接口无数据使用30天TTL。
# AuroraAI
