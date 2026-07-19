from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate
from backend.expectation_gap.query import list_expectation_gaps

app = FastAPI(title="AuroraAI")
FRONTEND = PROJECT_ROOT / "frontend"


@app.get("/api/expectation-gaps")
def expectation_gaps(
    market: str = "all", q: str = "", sort_by: str = "morningstar_gap_pct",
    sort_order: str = "desc", page: int = Query(1, ge=1), page_size: int = 50,
    include_unrated: bool = False,
):
    connection = connect()
    migrate(connection)
    try:
        return list_expectation_gaps(connection, market=market, q=q, sort_by=sort_by,
                                     sort_order=sort_order, page=page, page_size=page_size,
                                     include_unrated=include_unrated)
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


@app.get("/expectation-gap")
def expectation_page():
    return FileResponse(FRONTEND / "expectation-gap.html")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
