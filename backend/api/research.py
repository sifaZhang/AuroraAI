from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.expectation_gap.database import connect
from backend.research.research_summary_repository import ResearchSummaryRepository


router = APIRouter(prefix="/api/research", tags=["research"])
DECISIONS = {"avoid", "watch", "entry", "add", "heavy"}


@router.get("")
def research_overview(decision: str = "all", q: str = Query("", max_length=80)):
    selected = decision.lower()
    if selected != "all" and selected not in DECISIONS:
        from fastapi import HTTPException
        raise HTTPException(422, "unsupported research decision")
    connection = connect()
    try:
        items = ResearchSummaryRepository(connection).list_summaries()
    finally:
        connection.close()
    query = q.strip().lower()
    if selected != "all":
        items = [item for item in items if item["research_decision"] == selected]
    if query:
        items = [item for item in items if query in item["symbol"].lower()
                 or query in str(item.get("company_name") or "").lower()]
    return {"total": len(items), "items": items}


@router.get("/{symbol}")
def research_detail(symbol: str):
    connection = connect()
    try:
        detail = ResearchSummaryRepository(connection).get_detail(symbol)
    finally:
        connection.close()
    if detail is None:
        raise HTTPException(404, "no completed AI research found for this symbol")
    return detail
