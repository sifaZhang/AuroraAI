# PR-IR13 Forward Earnings & Consensus Enrichment

## Scope and source verdict

Phase A used the existing Tushare client and `report_rc` endpoint for only `000858.SZ` and `000423.SZ`. Both returned recent annual FY1–FY3 broker forecast records with report date, institution, forecast period, net profit, revenue, and EPS. The source verdict is **GO WITH REVIEW**: it is usable after local aggregation, but each row is an individual broker forecast rather than a ready-made market consensus.

Tushare documents `np` and `op_rt` in ten-thousand yuan and `eps` in yuan. The collector converts profit and revenue to CNY and labels EPS as CNY per share. Because the source describes `np` only as predicted net profit, comparison with AI normalized earnings is explicitly approximate.

## Aggregation contract

- Filter by `report_date <= as_of_date`; future rows are excluded and counted.
- Keep annual `YYYYQ4` forecasts and dynamically derive fiscal years.
- Within each fiscal year, retain the latest record for each exact `org_name`; no fuzzy institution matching is used.
- Prefer a 90-day window and fall back to 180 days only when fewer than two institutions have usable net-profit forecasts.
- Compute median, mean, minimum, maximum, population standard deviation, institution count, and a visible 50%-from-median outlier count. Values are not silently winsorized.
- Quality is HIGH for at least 10 institutions, MEDIUM for 5–9, LOW for 2–4, and INSUFFICIENT below 2. A 180-day fallback downgrades quality one level.
- Revision direction uses exact institution/year matches with a prior forecast 30–90 days earlier and is supported only with at least two matches.
- Target-price fields are not requested, stored, or displayed.

## Pipeline behavior

FULL_REFRESH and VALUATION_REFRESH collect `forward_consensus.json` before building valuation input. DECISION_ONLY reuses an existing artifact when available, and old versions without one remain valid. Provider, permission, timeout, schema, and empty-coverage failures produce explicit unavailable/source-error states through atomic writes and do not invent estimates.

The Valuation prompt treats consensus as an external expectation cross-check, never as fact or an automatic replacement for AI normalized earnings. Confidence calibration adjusts only Earnings Visibility and, by at most five points, Valuation Reliability. Evidence Completeness, Risk Resolution, Agent Consistency, Research Decision, Decision Cap, Yield Signal, and IR10 triggers are unchanged.

The detail API and UI expose aggregated FY statistics, freshness, institution count, quality, revision availability, approximate AI comparison, source, and as-of date. Raw broker records, local paths, and target prices are not exposed.

## Validation boundary

Unit tests use fake clients and cover point-in-time filtering, exact deduplication, fiscal-year parsing, 90/180-day selection, quality thresholds, statistics and units, revisions, schema drift, provider failure, AI comparison categories, orchestrator routing, deterministic confidence effects, API fallback, and detail rendering. The two formal POC artifacts are generated with Tushare only; no DeepSeek call is required.
