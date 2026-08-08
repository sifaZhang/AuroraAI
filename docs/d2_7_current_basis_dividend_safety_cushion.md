# PR-D2.7: current-basis dividend safety cushion

Raw `cash_dividend_per_share` remains the historical annual DPS used by the
three-year historical-yield qualification rule. It is never rewritten.
Migration 032 adds only `current_basis_dps` and `share_basis_as_of`.

`share_basis_adjustment.py` reuses D2.6 canonical cash events, then converts
each event separately. Later implemented Tushare stock distributions (`实施`
or `实施方案`, positive `stk_div` (or, when absent, the component
`stk_bo_rate + stk_co_rate`), with
`ex_date <= share_basis_as_of`) contribute `1 + ratios` to the divisor.
Same-day cash and stock distributions are included; planned, undated, and
future implementations are not applied.

The UI/API derives latest, average, conservative current yields and stability
without persisting derived columns. Price refresh reads stored basis DPS and
does not fetch dividends or rerun D2.6. Scanner qualification remains raw DPS
and unadjusted year-end prices, while scan artifacts can include both bases.
Migration validation is temporary SQLite only; this PR does not write or
migrate `data/aurora.db`.
