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
