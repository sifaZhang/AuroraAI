# Dividend universe management

`/dividend/universe` manages the formal A-share stable-dividend universe held in `dividend_stable_universe` and its annual DPS records in `annual_cash_dividend_summaries`.

The list is read from SQLite and includes enabled/disabled status, source, industry fields, subtype, monopoly type, the latest three available DPS calendar years, and calculated three-year totals and averages. Disabling a company never deletes its DPS history. Re-enabling requires three stored annual DPS rows.

Manual additions search the A-share master data, exclude B shares, validate normal listing status, fetch implemented cash dividends through the existing dividend provider, aggregate by `ex_date` year, and require complete positive DPS for all three target years. Warnings require explicit acknowledgement. The action writes only the two formal-dividend tables in one transaction and is idempotent for an existing symbol.

Candidate rescans reuse `DividendCandidateService`; they do not change the formal universe. A scan reports `still_qualified`, `new_candidate`, and `no_longer_qualified` results. D1 keeps the latest result in process memory, so it disappears when the API process restarts. A future persistent scan-history feature requires a separate migration and retention policy.

This feature does not fetch prices, calculate yields, send mail, support Hong Kong shares, or alter the first-limit workflow.
