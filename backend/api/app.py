from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.expectation_gap.database import (
    PROJECT_ROOT, DatabaseWriteBusyError, connect, migrate,
)
from backend.expectation_gap.query import list_expectation_gaps
from backend.expectation_gap.refresh_jobs import (
    JobConflictError, get_job, latest_job, recover_interrupted_jobs, start_background_job,
)
from backend.api.data_source_health import router as data_source_health_router
from backend.api.first_limit import router as first_limit_router
from backend.api.market_pulse import router as market_pulse_router
from backend.api.industry import router as industry_router
from backend.api.dividend_universe import router as dividend_universe_router
from backend.api.dividend_yields import router as dividend_yields_router
from backend.strategy.first_limit.api_service import FirstLimitAPIError

app = FastAPI(title="AuroraAI")
app.include_router(data_source_health_router)
app.include_router(market_pulse_router)
app.include_router(industry_router)
app.include_router(dividend_universe_router)
app.include_router(dividend_yields_router)
app.include_router(first_limit_router)
FRONTEND = PROJECT_ROOT / "frontend"


@app.exception_handler(FirstLimitAPIError)
async def first_limit_error(_request: Request, exc: FirstLimitAPIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code, "message": exc.message, "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/first-limit"):
        errors = [
            {
                "type": item.get("type"),
                "loc": list(item.get("loc", ())),
                "msg": item.get("msg"),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "first_limit_invalid_request",
                    "message": "request validation failed",
                    "details": {"errors": errors},
                }
            },
        )
    return await request_validation_exception_handler(request, exc)


@app.on_event("startup")
def recover_refresh_jobs_after_restart():
    connection = connect(); migrate(connection)
    try:
        recover_interrupted_jobs(connection)
        from backend.strategy.first_limit.pipeline_service import recover_jobs
        recover_jobs(connection)
    finally:
        connection.close()


@app.get("/api/expectation-gaps")
def expectation_gaps(
    market: str = "all", q: str = "", sort_by: str = "morningstar_gap_pct",
    sort_order: str = "desc", page: int = Query(1, ge=1), page_size: int = 50,
    include_unrated: bool = False, include_anomalies: bool = False,
):
    connection = connect()
    migrate(connection)
    try:
        return list_expectation_gaps(connection, market=market, q=q, sort_by=sort_by,
                                     sort_order=sort_order, page=page, page_size=page_size,
                                     include_unrated=include_unrated, include_anomalies=include_anomalies)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        connection.close()


@app.get("/api/expectation-gaps/refresh-status")
def refresh_status():
    connection = connect()
    migrate(connection)
    row = connection.execute("SELECT * FROM refresh_runs ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return dict(row) if row else {"status": "never_run"}


def _start_refresh_job(job_type: str):
    try:
        return start_background_job(job_type)
    except JobConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except DatabaseWriteBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(
                409, "数据库正被其他写入操作占用，请等待当前数据任务完成后再试"
            ) from exc
        raise


@app.post("/api/refresh-jobs/a-share", status_code=202)
def start_a_share_refresh():
    return _start_refresh_job("refresh_a_share")


@app.post("/api/refresh-jobs/hk-prices", status_code=202)
def start_hk_price_refresh():
    return _start_refresh_job("refresh_hk_prices")


@app.post("/api/refresh-jobs/hk-ratings", status_code=202)
def start_hk_rating_refresh():
    return _start_refresh_job("refresh_hk_ratings")


@app.get("/api/refresh-jobs/latest")
def latest_refresh_job():
    connection = connect(); migrate(connection)
    try:
        return latest_job(connection) or {"status": "never_run"}
    finally:
        connection.close()


@app.get("/api/refresh-jobs/{job_id}")
def refresh_job_status(job_id: int):
    connection = connect(); migrate(connection)
    try:
        job = get_job(connection, job_id)
        if job is None:
            raise HTTPException(404, "刷新任务不存在")
        return job
    finally:
        connection.close()


@app.get("/expectation-gap")
def expectation_page():
    return FileResponse(FRONTEND / "expectation-gap.html")


@app.get("/data-source-health")
def data_source_health_page():
    return FileResponse(FRONTEND / "data-source-health.html")


@app.get("/first-limit")
def first_limit_page():
    return FileResponse(FRONTEND / "first-limit.html")

@app.get('/dividend/universe')
def dividend_universe_page(): return FileResponse(FRONTEND / 'dividend-universe.html')


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
