# PR-IR8 AI Research Overview

PR-IR8 is a read-only projection of completed filesystem research artifacts. It adds compact AI fields to the existing A-share historical-dividend page at `/dividend/universe` and adds `/dividend/ai-research`. It does not implement the company detail route reserved for IR9.

## Data contract

`GET /api/research` scans enabled symbols under `data/research/{symbol}` and returns compact summaries only. A version must have a structurally valid `decision.json`; when `run.json` exists its status must be `COMPLETED`. The version referenced by `current.json` is attempted first, then the newest valid completed version is used. FAILED, PARTIAL, malformed, missing-decision, disabled, and unknown-universe research is excluded.

The returned decision, cap, confidence, skeptic verdict, valuation status, and forecast quality come from the validated decision document. Company name, latest price, and Yield Signal come from the formal dividend universe and latest dividend-yield snapshot. Yield Signal is recalculated with the existing `position_status()` logic and is never inferred from AI output.

The API accepts `decision=all|avoid|watch|entry|add|heavy` and optional `q` symbol/name search. It exposes no full Analyst, Skeptic, or Valuation text and provides no write or Agent execution endpoint.

## UI

The historical-dividend table adds only AI建议, AI置信度, and AI更新时间 beside the existing mechanical Yield Signal. Missing completed research displays 未分析. The decision links to `/dividend/ai-research?symbol={symbol}`.

The overview shows Yield Signal, Research Decision, gap, cap, confidence, Skeptic, valuation status, forecast quality, current price, and research date. It provides decision, yield, disagreement, and symbol/name filters plus four compact KPI cards. A symbol query filters to that company or displays 该股票尚无已完成AI研究. No UI action changes research, dividend settings, or the database.

IR9 remains responsible for `/dividend/ai-research/{symbol}`, full Agent narratives, conditions, valuation details, and research history.
