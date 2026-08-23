# PR-IR6 Research Orchestrator

IR6 adds a deterministic planner and single-symbol executor for the existing research chain. Planning is pure logic and never calls data providers or LLMs. With no material change it returns `NO_ACTION`, creates no analysis version, and estimates zero LLM calls.

Triggers are merged in this priority: new financial report, material announcement, research staleness, yield-signal change, price move, valuation staleness. Defaults are a 10% relative price move, 30-day valuation staleness, and 90-day full-research staleness; environment variables can override each value.

`FULL_REFRESH` runs source collection, input pack, Analyst, Skeptic, valuation input, Valuation, and Portfolio Manager (four planned LLM agents). `VALUATION_REFRESH` creates a new input pack, copies the prior Analyst and Skeptic outputs byte-for-byte, records their origin in `run.json`, then runs valuation input, Valuation, and Portfolio Manager (two agents). Manual `DECISION_ONLY` additionally reuses valuation and runs Portfolio Manager only.

Source deduplication reuses IR2 identifiers: financial report SHA-256 or period/type, and announcement SHA-256 or announcement ID/source URL. Only a source absent from the previous completed input snapshot triggers a fundamental refresh. Failed versions are marked `FAILED`, record `failed_step`, and never update `current.json`.

Use `python -m backend.research.research_orchestrator --symbol 000858.SZ --dry-run`. Manual modes are `--mode full|valuation|decision`; `--max-llm-calls` rejects execution before side effects when the plan exceeds the limit. V1 intentionally omits schedules, GitHub Actions, Review Agent, database changes, and all-candidate execution.
