# A 股 A 类稳定分红候选池

`python -m backend.dividend.generate_dividend_a_candidates` creates a review-only CSV.  It does not write the stock pool or SQLite data.

```powershell
python -m backend.dividend.generate_dividend_a_candidates `
  --calculation-date 2026-08-07 `
  --output exports/dividend/dividend_a_candidates.csv `
  --exclusions-output exports/dividend/dividend_a_candidate_exclusions.csv
```

The command opens the configured SQLite database read-only, uses `a_share_security_master`, the latest security status at or before the calculation date, and `industry_memberships_current`.  Dividend events are requested through AuroraAI's `TushareClient`; its token remains environment configuration.

The calculation uses the three complete calendar years before the calculation date, groups implemented positive `cash_div_tax` by `ex_date.year`, and deduplicates by `ts_code/end_date/ann_date/ex_date/cash_div_tax/div_proc`.  Candidates must be a normal SH/SZ security listed for five years, be in an allowed stable operating industry, pay in all three years, and have latest DPS at least 70% of the three-year average.  Output is sorted by `monopoly_type`, `industry_level_1`, and `symbol`.

`--dry-run` calculates but writes no files; `--symbols`, `--limit`, `--database`, and `--strict` support focused, repeatable checks.  `--strict` returns non-zero when the industry cannot be identified or the dividend provider fails.

Known limitation: the current industry snapshot is a current (not historical) SW membership, so a human should review classifications and whether an operating-industry label truly represents the intended concession or resource moat.

The initial manual-review mapping contains `中国石油` and `中国石化` as `oil_gas_resource`: their current SW labels are refining/trading even though the requested pool treats the integrated national oil companies as oil-and-gas resource candidates.  This mapping is deliberately narrow and must be reviewed in the review CSV.
