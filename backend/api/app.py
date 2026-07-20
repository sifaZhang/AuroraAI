from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate
from backend.expectation_gap.query import list_expectation_gaps
from backend.expectation_gap.refresh_jobs import (
    JobConflictError, get_job, latest_job, recover_interrupted_jobs, start_background_job,
)
from backend.api.data_source_health import router as data_source_health_router

app = FastAPI(title="AuroraAI")
app.include_router(data_source_health_router)
FRONTEND = PROJECT_ROOT / "frontend"


@app.on_event("startup")
def recover_refresh_jobs_after_restart():
    connection = connect(); migrate(connection)
    try:
        recover_interrupted_jobs(connection)
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


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
