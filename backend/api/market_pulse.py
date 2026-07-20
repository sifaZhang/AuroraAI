"""Market Pulse read and asynchronous refresh API."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.refresh_jobs import JobConflictError, get_job, start_background_job
from backend.sector_radar.query import get_sector_score, list_sector_scores
from backend.sector_radar.service import SOURCE_ORDER

router = APIRouter(prefix="/api/market-pulse", tags=["market-pulse"])


class RefreshRequest(BaseModel):
    source: str = "sw_l1"


def _public_job(job: dict) -> dict:
    status_map = {"pending": "queued", "running": "running", "success": "completed", "partial": "partial", "failed": "failed"}
    return {
        "job_id": job["id"], "job_type": job["job_type"], "source": job.get("source") or "sw_l1",
        "status": status_map.get(job["status"], job["status"]), "progress": float(job.get("progress_pct") or 0),
        "current_step": job.get("message"), "completed_count": job.get("processed", 0),
        "total_count": job.get("total", 0), "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"), "error_message": job.get("error_summary"),
    }


@router.get("/sectors")
def market_pulse_sectors(
    source: str = "sw_l1", trade_date: str | None = None, sort_by: str = "trend_score",
    order: str = "desc", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1),
):
    connection = connect()
    try:
        migrate(connection)
        return list_sector_scores(
            connection, source=source, trade_date=trade_date, sort_by=sort_by,
            order=order, page=page, page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(500, "数据库操作失败") from exc
    finally:
        connection.close()


@router.get("/sectors/{source}/{sector_code}")
def market_pulse_sector_detail(source: str, sector_code: str, trade_date: str | None = None):
    connection = connect()
    try:
        migrate(connection)
        try:
            item = get_sector_score(connection, source, sector_code, trade_date)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if item is None:
            raise HTTPException(404, "行业评分不存在")
        return item
    except sqlite3.Error as exc:
        raise HTTPException(500, "数据库操作失败") from exc
    finally:
        connection.close()


@router.post("/refresh", status_code=202)
def start_market_pulse_refresh(request: RefreshRequest):
    if request.source not in {*SOURCE_ORDER, "all"}:
        raise HTTPException(400, f"不支持的数据源: {request.source}")
    try:
        job = start_background_job("refresh_market_pulse", source=request.source)
        return _public_job(job)
    except JobConflictError as exc:
        raise HTTPException(409, {"message": "刷新任务已存在", "existing_job_id": exc.existing_job_id}) from exc


@router.get("/refresh/{job_id}")
def market_pulse_refresh_status(job_id: int):
    connection = connect()
    try:
        migrate(connection)
        job = get_job(connection, job_id)
        if job is None or job["job_type"] != "refresh_market_pulse":
            raise HTTPException(404, "刷新任务不存在")
        return _public_job(job)
    except sqlite3.Error as exc:
        raise HTTPException(500, "数据库操作失败") from exc
    finally:
        connection.close()
