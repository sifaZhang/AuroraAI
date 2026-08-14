"""Background refresh endpoint for the Upcoming Dividends page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock, Thread

from fastapi import APIRouter, HTTPException

from backend.analysis.dividend_yield import calculate_dividend_top20
from backend.collector.collect_dividends import TOP20_OUTPUT_COLUMNS, write_metadata
from backend.collector.dividend_collector import collect_dividend_candidates


router = APIRouter(prefix="/api/upcoming-dividends", tags=["dividend"])
_LOCK = Lock()
_STATE: dict[str, object] = {"status": "idle", "message": "尚未刷新"}
_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _refresh_once() -> int:
    """Collect and publish the page artifacts as one local refresh operation."""
    dividends, prices = collect_dividend_candidates(limit=200)
    result = calculate_dividend_top20(dividends, prices, top=20)
    if len(result.columns) == len(TOP20_OUTPUT_COLUMNS):
        result.columns = TOP20_OUTPUT_COLUMNS

    output = _FRONTEND / "dividend_top20.csv"
    temporary = output.with_suffix(".csv.tmp")
    result.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(output)
    write_metadata(_FRONTEND / "metadata.json", row_count=len(result), as_of_date=None, output_path=output)
    return len(result)


def _run_refresh() -> None:
    with _LOCK:
        _STATE.update(status="running", message="正在获取未来 7 天分红与最新股价", started_at=datetime.now().isoformat())
    try:
        row_count = _refresh_once()
    except Exception as exc:  # The error is returned to the page, not lost in a daemon thread.
        with _LOCK:
            _STATE.update(status="failed", message=f"刷新失败：{type(exc).__name__}: {exc}", finished_at=datetime.now().isoformat())
    else:
        with _LOCK:
            _STATE.update(status="success", message=f"刷新完成，共 {row_count} 条", row_count=row_count, finished_at=datetime.now().isoformat())


@router.post("/refresh", status_code=202)
def start_refresh() -> dict[str, object]:
    with _LOCK:
        if _STATE.get("status") == "running":
            raise HTTPException(409, "分红刷新任务正在进行中")
        _STATE.clear()
        _STATE.update(status="queued", message="刷新任务已提交")
        Thread(target=_run_refresh, daemon=True, name="upcoming-dividend-refresh").start()
        return dict(_STATE)


@router.get("/refresh-status")
def refresh_status() -> dict[str, object]:
    with _LOCK:
        return dict(_STATE)
