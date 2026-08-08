# High-dividend watch full-market dry-run

The read-only runner applies only the minimal rule: normal non-ST ordinary RMB A-share, positive D2.6 DPS in 2023–2025, and historical yield of at least 4% in every individual year. Ordinary shares are recognized by exchange code families: Shanghai `60xxxx` and `688xxx`, Shenzhen `00xxxx` and `30xxxx`. This excludes CDRs such as the `689xxx` range, B shares, and other instruments without hard-coding an individual security. It does not call income, cashflow, or financial-quality APIs and does not write SQLite.

Dividend data is fetched for the twelve standard report periods using `dividend(end_date=period, offset=..., limit=2000)`. Pages continue until fewer than 2,000 rows return. Exact duplicates are removed before the existing `annual_dps.py` lifecycle normalization; D2.6 is not reimplemented in the runner.

Year-end and latest unadjusted closes are fetched by full-market `daily(trade_date=...)` calls. Missing suspended securities are filled from at most ten prior trading days, still by trading-day batch rather than per-symbol requests.

Run:

```powershell
python -m backend.dividend.run_high_dividend_watch_full_dryrun `
  --calculation-date 2026-08-08 `
  --output exports/dividend/high_dividend_watch_full_dryrun.csv
```

The CSV is UTF-8 BOM. A sibling `.summary.json` records request counts, timing, the ten-symbol audit, failures, and candidate symbols.

The corrected 2026-08-08 validation scanned 4,994 ordinary A shares in 113.902 seconds. Dividend collection used 43 requests across twelve periods and 84.132 seconds; prices used 30 year-end and 6 latest-date requests. It found 2,907 symbols with complete positive three-year DPS and 128 meeting 4% in every year, with zero provider failures. Compared with the prior 129 candidates, only `689009.SH` was removed. No remaining candidate has three-year historical average yield above 20%; the maximum is `603519.SH` at 10.7034%. No production database or universe row was modified.

## Page integration and persistence

Migration `031_dividend_high_watch_subtype.sql` rebuilds only `dividend_stable_universe` and extends its existing subtype CHECK with `high_dividend_watch`. The project migration runner checks the current table SQL before applying it. Validation against a copy and subsequent application to the formal database preserved all 27 rows, 27 enabled flags, the 24/3 existing subtype distribution, and all 81 annual DPS rows; foreign-key validation returned no errors. Before formal migration, `data/aurora.db` was backed up to `backups/database/aurora_before_migration_031_20260808_150635.db` with matching file size and SHA256.

The page loads the formal universe plus the most recent CSV/summary artifacts. It never starts a scan on page load. `刷新股息率` retains its price/current-yield-only endpoint, while `重新筛选候选池` is the sole trigger for the batch scanner. Successful scans replace the CSV and summary atomically, so a later page visit can read the last completed result and a failed scan leaves the prior result intact. Candidate artifacts and formal universe membership remain separate.

Candidates are displayed as `稳定垄断型`, `资源周期型`, or `普通高股息观察型`, with search, type filtering, historical/current average-yield sorting, and the existing 6%/8% current-yield highlighting. A new candidate requires browser confirmation before the API inserts its scanner-suggested subtype with `inclusion_source=manual_review`; no scan automatically inserts universe rows.

The current security-master result of `all_a=4995` and `normal_non_st=4995` is expected: 205 securities have historical ST observations, but the latest status snapshot has zero ST securities and zero current names containing ST, *ST, or 退. No additional ST subsystem was added.

The final real scan through the page API completed in 115.419 seconds with 129 candidates (37 stable monopoly, 20 resource cyclical, and 72 ordinary high-dividend watch). A headless Microsoft Edge render displayed the 27-row formal pool, all 129 candidates, the three Chinese subtype labels, the designated Gree/Shenhua/Bank of Communications rows, and add controls. Candidate-add write testing used a migrated temporary copy; the formal database received only migration 031 and no candidate rows.
