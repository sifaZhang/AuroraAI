# Dividend Top50

This module collects A-share dividend data from free data sources, keeps only
upcoming dividend record dates, calculates the current dividend yield, and ranks
stocks by yield.

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
python -m backend.collector.collect_dividends --limit 200 --top 50 --output data\dividend_top20.csv
```

The full-market path first fetches the announced dividend list from Eastmoney's
free dividend distribution data through AKShare, filters records whose record
date has not passed, and then calculates Dividend Top50. It does not scan every
A-share one by one.

Use `--limit 0` to keep all upcoming dividend candidates.

By default, the command uses the price/yield information carried by the
Eastmoney dividend source, which is faster and more stable. To force a live
per-stock price refresh, add:

```powershell
python -m backend.collector.collect_dividends --limit 200 --refresh-prices
```

If the Eastmoney and AKShare announced dividend sources are unstable, configure
Tushare Pro as a fallback:

```powershell
python -m backend.collector.collect_dividends --include-tushare --limit 200
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

## Tushare Pro

Tushare Pro support is optional. Fill `TUSHARE_TOKEN` in `.env`, then add
`--include-tushare`.

```powershell
python -m backend.collector.collect_dividends --codes 000001 --include-tushare
```

Without a token, the default path uses AKShare only.

## UI

Generate UI data locally:

```powershell
python -m backend.collector.collect_dividends `
  --limit 200 `
  --top 50 `
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
