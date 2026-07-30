"""Thin FastAPI router for PR6.10 first-limit orchestration."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit import api_service as service
from backend.strategy.first_limit import pipeline_repository as pipeline_repo
from backend.strategy.first_limit import pipeline_service
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


class PipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    stage: str
    as_of: datetime | None = None
    data_cutoff: datetime | None = None


def _pipeline_error(exc):
    raise service.FirstLimitAPIError(
        exc.status_code, exc.code, exc.message, exc.details
    ) from exc


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


@router.post("/pipeline-jobs", status_code=202)
def create_pipeline_job(
    request: PipelineRequest,
    connection: sqlite3.Connection = Depends(database),
):
    try:
        result = pipeline_service.create_job(
            connection, trade_date=request.trade_date, stage=request.stage,
            as_of=request.as_of, data_cutoff=request.data_cutoff,
        )
    except pipeline_service.PipelineError as exc:
        _pipeline_error(exc)
    pipeline_service.start_background(result["job_id"])
    return result


@router.get("/pipeline-jobs")
def pipeline_jobs(
    trade_date: date | None = None,
    stage: str | None = None,
    status: list[str] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(database),
):
    total, rows = pipeline_repo.jobs(
        connection, trade_date=trade_date, stage=stage,
        statuses=_values(status), limit=limit, offset=offset,
    )
    return {"total": total, "limit": limit, "offset": offset,
            "items": pipeline_service.serialize_rows(rows)}


@router.get("/pipeline-jobs/latest")
def latest_pipeline_job(
    trade_date: date | None = None,
    stage: str | None = None,
    connection: sqlite3.Connection = Depends(database),
):
    row = pipeline_repo.latest_job(
        connection, trade_date=trade_date, stage=stage
    )
    if row is None:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        )
    return pipeline_service.serialize_job(row)


@router.get("/pipeline-jobs/{job_id}")
def pipeline_job(
    job_id: int, connection: sqlite3.Connection = Depends(database)
):
    row = pipeline_repo.job(connection, job_id)
    if row is None:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        )
    return pipeline_service.serialize_job(row)


@router.get("/pipeline-jobs/{job_id}/steps")
def pipeline_steps(
    job_id: int, connection: sqlite3.Connection = Depends(database)
):
    if pipeline_repo.job(connection, job_id) is None:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        )
    return {"job_id": job_id, "items": [
        {
            **dict(row),
            "input_summary": pipeline_repo.load(
                row["input_summary_json"], {}
            ),
            "output_summary": pipeline_repo.load(
                row["output_summary_json"], {}
            ),
        }
        for row in pipeline_repo.steps(connection, job_id)
    ]}


@router.get("/pipeline-jobs/{job_id}/coverage")
def pipeline_coverage(
    job_id: int, connection: sqlite3.Connection = Depends(database)
):
    if pipeline_repo.job(connection, job_id) is None:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        )
    return {"job_id": job_id, "items": [
        {
            **dict(row), "complete": bool(row["complete"]),
            "details": pipeline_repo.load(row["details_json"], {}),
        }
        for row in pipeline_repo.coverage(connection, job_id)
    ]}


@router.get("/pipeline-jobs/{job_id}/failures")
def pipeline_failures(
    job_id: int, limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(database),
):
    if pipeline_repo.job(connection, job_id) is None:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        )
    total, rows = pipeline_repo.failures(
        connection, job_id, limit=limit, offset=offset
    )
    return {"job_id": job_id, "total": total, "limit": limit,
            "offset": offset, "items": [dict(row) for row in rows]}


@router.post("/pipeline-jobs/{job_id}/retry", status_code=202)
def retry_pipeline_job(
    job_id: int, connection: sqlite3.Connection = Depends(database)
):
    try:
        return pipeline_service.retry_job(connection, job_id)
    except LookupError as exc:
        raise service.FirstLimitAPIError(
            404, "first_limit_pipeline_job_not_found", "pipeline job not found"
        ) from exc


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
