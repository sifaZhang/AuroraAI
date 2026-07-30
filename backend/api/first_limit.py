"""Thin FastAPI router for PR6.10 first-limit orchestration."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, Depends, Query

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit import api_service as service
from backend.strategy.first_limit.api_models import (
    CandidateDetail,
    CandidatePage,
    PreviewComparisonPage,
    RunAccepted,
    RunDetail,
    RunItemPage,
    RunPage,
    RunRequest,
)

router = APIRouter(prefix="/api/first-limit", tags=["first-limit"])


def database() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        migrate(connection)
        yield connection
    except sqlite3.Error as exc:
        raise service.FirstLimitAPIError(
            500, "first_limit_database_error", "database operation failed"
        ) from exc
    finally:
        connection.close()


def _values(values: list[str] | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for value in values or ()
        for item in value.split(",")
        if item.strip()
    )


@router.get("/candidates", response_model=CandidatePage)
def candidates(
    trade_date: date,
    stage: str,
    grade: list[str] | None = Query(None),
    lifecycle: list[str] | None = Query(None),
    symbol: str | None = None,
    change_type: str | None = None,
    include_unknown: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = "grade_rank",
    order: str = "asc",
    connection: sqlite3.Connection = Depends(database),
):
    return service.list_candidates(
        connection, trade_date=trade_date, stage=stage, grades=_values(grade),
        lifecycles=_values(lifecycle), symbol=symbol, change_type=change_type,
        include_unknown=include_unknown, limit=limit, offset=offset,
        sort=sort, order=order,
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def candidate_detail(
    candidate_id: int, connection: sqlite3.Connection = Depends(database)
):
    return service.get_candidate(connection, candidate_id)


@router.get("/runs", response_model=RunPage)
def runs(
    trade_date: date | None = None,
    stage: str | None = None,
    status: list[str] | None = Query(None),
    strategy_version: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(database),
):
    return service.list_runs(
        connection, trade_date=trade_date, stage=stage,
        statuses=_values(status), strategy_version=strategy_version,
        limit=limit, offset=offset,
    )


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(
    run_id: str, connection: sqlite3.Connection = Depends(database)
):
    return service.get_run(connection, run_id)


@router.get("/runs/{run_id}/items", response_model=RunItemPage)
def run_items(
    run_id: str,
    status: list[str] | None = Query(None),
    symbol: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(database),
):
    return service.list_run_items(
        connection, run_id=run_id, statuses=_values(status), symbol=symbol,
        limit=limit, offset=offset,
    )


@router.get("/preview-comparison", response_model=PreviewComparisonPage)
def preview_comparison(
    trade_date: date,
    symbol: str | None = None,
    change_type: str | None = None,
    grade: list[str] | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(database),
):
    return service.preview_comparison(
        connection, trade_date=trade_date, symbol=symbol,
        change_type=change_type, grades=_values(grade),
        limit=limit, offset=offset,
    )


@router.post("/runs", response_model=RunAccepted)
def start_run(
    request: RunRequest, connection: sqlite3.Connection = Depends(database)
):
    return service.trigger_run(connection, request)
