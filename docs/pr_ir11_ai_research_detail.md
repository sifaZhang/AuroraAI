# PR-IR11 Company AI Research Detail

PR-IR11 adds a read-only company research page at `/dividend/ai-research/{symbol}` and a matching `GET /api/research/{symbol}` projection. It presents existing research artifacts; it cannot run an Agent, edit research, or write the database.

## Version and data contract

The detail repository reuses the IR8 version ordering: a valid version referenced by `current.json` is preferred, followed by the newest completed version. A version is eligible only when its decision exists and its `run.json`, when present, is `COMPLETED`. Detail projection additionally validates Analyst, Skeptic, Valuation and Decision documents and verifies their symbol/version integrity. FAILED, PARTIAL, malformed and incomplete versions are skipped.

The DTO contains company/research classification, current formal Dividend facts, validated Agent documents, Portfolio conditions, compact run metadata, evidence counts/IDs, technical generation metadata and a completed-version count. The formal Yield Signal is recalculated from the enabled Dividend universe rather than trusted from an old research file. Disabled and unknown symbols are not exposed.

An evidence summary is optional and marked available only when it passes current validation. No PDF, local path, compressed source text, editable raw JSON or full historical artifacts are returned.

## Page hierarchy

The page leads with the relationship between mechanical Yield Signal and AI Research Decision, then displays:

1. final decision, cap, confidence, valuation, risk and forecast quality;
2. supporting and limiting factors plus Portfolio Manager conflicts;
3. existing normalized-earnings, multiple and fair-value scenarios, an HTML/CSS valuation range and current-vs-base relationship;
4. reverse valuation explicitly labelled as market-implied scenarios, and the existing dividend cross-check;
5. severity-sorted Skeptic risks, dividend-trap assessment and collapsed challenged claims;
6. Analyst thesis, drivers, concerns, quality scores and the distinction between insufficient Analyst normalized earnings and later Valuation scenarios;
7. collapsed FACT/INFERENCE/ASSUMPTION claims with evidence counts and IDs;
8. unchanged upgrade, downgrade and thesis-invalidation conditions; and
9. evidence, model/prompt/token/latency metadata and a count-only research-history placeholder.

The valuation range handles prices outside Bear/Bull and falls back to an insufficient-evidence message for missing scenarios. Confidence formatting checks `null` explicitly, so a valid zero remains `0%`. The responsive layout collapses cards on narrow screens without a charting or UI framework.

Overview company/decision links and Dividend Universe AI Decision links now navigate directly to the detail route. Overview query filtering remains available.

Research history comparison remains outside this PR and should be implemented separately if needed.
