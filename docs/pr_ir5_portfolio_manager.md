# PR-IR5 Portfolio Manager

Adds a version-local `decision.json` generated from the existing company snapshot, analyst, skeptic, and valuation outputs. It does not alter the database, yield thresholds, preceding agents, or historical versions.

`portfolio-manager-v1` uses `position_status()` for the yield signal. Code, rather than the LLM, determines valuation status and the decision cap: the lower of the skeptic cap and the forecast-quality cap (`INSUFFICIENT`/`LOW`/`MEDIUM`/`HIGH` map to `watch`/`entry`/`add`/`heavy`). Semantic validation rejects mismatched authoritative inputs, decisions above the cap, unsupported add/heavy decisions, and fields outside the strict schema.

Run one version with `python -m backend.research.portfolio_manager --symbol 000858.SZ --version 2026-08-23_v3`. Existing decisions require `--force`; `--dry-run` does not call the LLM or write files.

Known limitation: the current two POC real runs could not reach the local DeepSeek endpoint, so no `decision.json` was written.
