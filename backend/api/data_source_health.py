"""FastAPI routes for synchronous lightweight source checks."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.expectation_gap.database import connect, migrate
from backend.sector_radar.health_checks import CHECK_ORDER, run_health_checks
from backend.sector_radar.health_repository import list_statuses

router = APIRouter(prefix="/api/data-source-health", tags=["data-source-health"])


class CheckRequest(BaseModel):
    source: str = "all"


@router.get("")
def get_data_source_health():
    connection = connect()
    try:
        migrate(connection)
        items = list_statuses(connection)
        connection.commit()
        return {"items": items}
    except sqlite3.Error as exc:
        raise HTTPException(500, "数据库操作失败") from exc
    finally:
        connection.close()


@router.post("/check")
def check_data_source_health(request: CheckRequest):
    if request.source not in {*CHECK_ORDER, "all"}:
        raise HTTPException(400, f"不支持的数据源: {request.source}")
    connection = connect()
    try:
        migrate(connection)
        items = run_health_checks(connection, request.source)
        return {"source": request.source, "items": items}
    except sqlite3.Error as exc:
        raise HTTPException(500, "数据库操作失败") from exc
    finally:
        connection.close()
