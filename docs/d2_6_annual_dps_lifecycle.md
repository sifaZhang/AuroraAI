# D2.6 annual DPS lifecycle normalization

## Purpose

D2.6 counts a cash-dividend plan once across its Tushare lifecycle while allowing a positive, formally shareholder-approved plan to enter annual DPS before its ex-date or pay-date. Attribution remains based on `end_date.year`; the historical-yield 4% rule and scanner are unchanged.

## Event normalization

Eligible input requires a valid `end_date` and positive `cash_div_tax`. Status precedence is:

1. `实施` / `实施方案`
2. `股东大会通过`
3. `预案`

`预案` alone is not formal annual DPS. `停止实施`, `取消`, and `否决` invalidate the matching lifecycle.

Rows are first partitioned by `(ts_code, end_date)`. Within one report period they are ordered by lifecycle time. Implementation announcement date is preferred for implementation rows, with announcement, base, record, ex, and pay dates used as fallbacks. Proposal or approval rows update the current open plan; implementation selects the highest stage and closes that plan. A later implementation starts another plan, so two actual distributions in the same report period—including equal-amount distributions—remain separate. Exact duplicate source rows are removed using all lifecycle and payout fields.

The grouping deliberately does not require `record_date`, `ex_date`, or `pay_date` equality because these are normally absent before implementation. It also does not key solely by amount: a plan may revise its amount before implementation, as observed for `601998.SH`.

## Provider fields

`TushareDividendProvider` now maps `record_date`, `pay_date`, `imp_ann_date`, and `base_date` into `DividendEvent`, in addition to the existing report, announcement, ex-date, amount, and status fields. Business aggregation continues to consume domain models rather than raw provider rows.

## Read-only validation on 2026-08-08

- `000651.SZ`: 2023 DPS 2.38, 2024 DPS 3.00, 2025 DPS 3.00. The 2025 result contains implemented 1.00 plus shareholder-approved 2.00.
- `600900.SH`: 2025 DPS remains 1.00 (0.21 + 0.79).
- `600028.SH`: 2025 DPS remains 0.20 (0.088 + 0.112).
- Gree's 2025-12-31 unadjusted close from the targeted Tushare daily query was 40.22; `3.00 / 40.22 = 7.459%`, above 4%.
- The enabled formal pool contained 27 symbols. Comparing its stored 2023–2025 DPS with an in-memory D2.6 recomputation produced zero changed symbols and zero changed rows. Gree was not one of those 27 enabled symbols and was checked separately.

No production database write, migration, scanner run, full-market scan, or Git write operation was performed.

## Known limitation

Tushare exposes no stable dividend-plan identifier. Chronology plus report-period and payout anchors are therefore the safest available rule, but genuinely concurrent same-report-period plans whose pre-implementation rows have no distinguishing dates remain intrinsically ambiguous. Separate implementation rows are always preserved.
