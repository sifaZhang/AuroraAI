# Upcoming Dividends

This module keeps only the next seven calendar days of announced A-share
dividend record dates, calculates the current dividend yield, and ranks stocks
by yield.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Recommended First Run

When full-market price APIs are unstable, provide stock codes and current prices:

```powershell
python -m backend.collector.collect_dividends `
  --codes 000001,600519 `
  --price-overrides 000001=10.50,600519=1500.00 `
  --output data\dividend_top20.csv
```

## Full-Market Scan

```powershell
python -m backend.collector.collect_dividends --limit 200 --top 0 --output data\dividend_top20.csv
```

The full-market path uses the unified Provider layer. Tushare is primary: it
queries `dividend(record_date=...)` in date batches for the current date through
seven days ahead, with 2,000-row offset pagination. Latest prices come from one
Tushare full-market daily-bar batch. If Tushare is unavailable, only then does
the flow fall back to AKShare/Eastmoney batch endpoints. It never scans A-shares
one by one.

Use `--limit 0` to keep all upcoming dividend candidates.

The seven-day window can be changed explicitly:

```powershell
python -m backend.collector.collect_dividends --limit 200 --upcoming-days 7
```

Put your Tushare token in `.env`:

```env
TUSHARE_TOKEN=your-token
```

## Calculation

```text
本次股息率 = 每10股派息 / 10 / 最新收盘价 * 100%
```

Rows with a record date earlier than the run date are excluded.

Default output columns:

```text
排名
登记日
股票
每10股派息
最新股价
本次股息率
```

For reproducible historical checks, pass an explicit filter date:

```powershell
python -m backend.collector.collect_dividends --as-of-date 2026-07-07
```

The older raw calculation tables are still available:

```powershell
python -m backend.collector.collect_dividends --mode latest
python -m backend.collector.collect_dividends --mode trailing_12m
```

## Provider priority

Tushare Pro is the default primary Provider. When it is not configured or its
batch request fails, AKShare/Eastmoney is the fallback. The old
`--include-tushare` and `--refresh-prices` flags remain only for command-line
compatibility; the full-market path is already Tushare-first and batch-only.

## UI

Generate UI data locally:

```powershell
python -m backend.collector.collect_dividends `
  --limit 200 `
  --top 0 `
  --output frontend\dividend_top20.csv `
  --metadata-output frontend\metadata.json
```

Start a local static server:

```powershell
cd frontend
python -m http.server 4173
```

Open:

```text
http://localhost:4173
```

## Schedule

GitHub Actions runs every day at 01:00 Beijing time.

```text
cron: 0 17 * * *
timezone: UTC
Beijing time: UTC+8
```

The workflow generates `frontend/dividend_top20.csv` and `frontend/metadata.json`,
then deploys the static UI to GitHub Pages. The UI supports clicking table
headers to sort by record date, stock, dividend, price, or yield.

Configure the repository Actions secret `TUSHARE_TOKEN` so the scheduled build
uses the primary Tushare batch provider; without that secret it automatically
uses the AKShare fallback.
