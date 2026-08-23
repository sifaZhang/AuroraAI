# PR-IR12 Research Confidence Calibration

## Purpose

Research Confidence is now a deterministic assessment of the available research package. It is not a prediction accuracy, success probability, trading signal, or model self-assessment. The Portfolio Manager's original `decision_confidence` remains available only as `model_reported_confidence` for audit.

## Deterministic formula

All components are integers from 0 to 100. The final score is rounded to an integer and clamped by the component schemas:

`evidence × 25% + earnings visibility × 25% + valuation reliability × 25% + risk resolution × 15% + agent consistency × 10%`

The model-reported confidence has weight zero.

## V1 component rules

### Evidence Completeness

Starts at 100. Missing latest financial report, annual report, cash-flow evidence, dividend evidence, balance-sheet evidence, and governance/material-event evidence deduct 30, 20, 15, 10, 10, and 5 respectively. A missing compressed summary deducts 5 while valid raw evidence remains usable. Each unresolved referenced evidence ID deducts 20, capped at 40. The checks use evidence IDs, source types, and compressed evidence sections; they do not infer facts from prose.

### Earnings Visibility

Forecast quality maps HIGH/MEDIUM/LOW/INSUFFICIENT to 90/70/40/10. A complete normalized-earnings scenario range adjusts the score by +5 at a spread of at most 25%, 0 above 25% through 50%, -10 above 50% through 100%, and -20 above 100%. The spread is `(bull - bear) / abs(base)`. An Analyst normalized-earnings method of `insufficient_evidence` deducts 5.

### Valuation Reliability

Starts with Valuation Agent confidence multiplied by 100. Complete fair values add 10, normalized earnings add 10, multiple scenarios add 5, and reverse-valuation scenarios add 5. Forecast-quality caps are LOW 55, MEDIUM 80, HIGH 100, and INSUFFICIENT 30. An uncertain valuation with insufficient forecast quality is zero in this component. This measures valuation construction and stability separately from earnings visibility.

### Risk Resolution

Skeptic PASS/CAUTION/BLOCK maps to 85/55/20. Each unresolved CRITICAL/HIGH/MEDIUM/LOW major risk deducts 20/10/3/0. Dividend-trap UNLIKELY/UNKNOWN/POSSIBLE/LIKELY adjusts +5/0/-10/-25. Unsupported or contradicted challenged claims deduct 10/5/2/1 by CRITICAL/HIGH/MEDIUM/LOW severity.

### Agent Consistency

Starts at 50. A Portfolio Manager decision within its cap adds 10. A conservative decision with expensive/uncertain valuation, or a non-conservative decision with undervaluation, adds 10. An uncertain Analyst view with a watch decision adds 5. A strong positive Analyst view followed by avoid/watch deducts 20 only when neither Skeptic nor valuation provides a structured limiting reason. Skeptic BLOCK with a decision above WATCH fails validation.

## Levels and insufficiency

- HIGH: 75–100
- MEDIUM: 55–74
- LOW: 35–54
- VERY_LOW: 1–34
- INSUFFICIENT: 0

Zero is reserved for fatal insufficiency. V1 explicitly uses it when forecast quality is INSUFFICIENT, valuation status is UNCERTAIN, and no fair-value scenario exists. Missing or invalid core artifacts fail calibration instead of producing a partial artifact.

## Artifact and workflow

`confidence_calibration.json` is validated by a strict Pydantic schema and written atomically beside the research version. FULL_REFRESH, VALUATION_REFRESH, and DECISION_ONLY run calibration after Portfolio Manager with no additional LLM call. NO_ACTION creates nothing. The estimated call counts remain 4, 2, 1, and 0.

## API and UI compatibility

Overview returns calibrated confidence as the existing 0–1 `decision_confidence` field, plus `confidence_level` and `confidence_source`. Detail returns score, level, source, components, deterministic strengths/blockers, method, and model-reported confidence. Versions without a valid calibration fall back to the PM value and declare `confidence_source: model_fallback`.

The Overview shows calibrated confidence. The Detail page shows score, level, five progress bars, strengths, blockers, and the raw model value under technical details. Dividend Universe retains its current design without a confidence column.

## POC

No LLM or DeepSeek call was made. Existing artifacts produced:

- `000858.SZ / 2026-08-23_v3`: Evidence 100, Earnings 25, Valuation 55, Risk 19, Consistency 75; final 55 MEDIUM; model-reported 40%.
- `000423.SZ / 2026-08-23_v1`: Evidence 100, Earnings 40, Valuation 55, Risk 22, Consistency 75; final 60 MEDIUM; model-reported 0%.

The rules were fixed before the POC and were not adjusted to raise either result. The primary blockers are unresolved Skeptic risks and low earnings visibility.

## Limitations

V1 uses only existing research artifacts. It adds no external consensus, forward earnings feed, NLP inference, review agent, database migration, trigger change, or trading behavior. Forward consensus could improve earnings visibility in a future separately scoped change, but is not required for calibration correctness.
