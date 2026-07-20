from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

from backend.collector.dividend_collector import fetch_latest_prices_akshare_by_codes
from backend.collector.import_manual_a_share_valuations import import_file
from backend.collector.init_hk_expectations import is_due
from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate
from backend.expectation_gap.futu_client import CollectionResult, FutuResearchClient, utc_now
from backend.expectation_gap.quality import refresh_quality
from backend.expectation_gap.repository import patch_analyst, patch_morningstar, patch_price

JOB_TYPES = {"refresh_a_share", "refresh_hk_prices", "refresh_hk_ratings"}
ACTIVE_STATUSES = {"pending", "running"}
JOB_LABELS = {
    "refresh_a_share": "正在刷新A股",
    "refresh_hk_prices": "正在刷新港股股价",
    "refresh_hk_ratings": "正在刷新港股评级",
}
_worker_lock = threading.Lock()


class JobConflictError(RuntimeError):
    pass


def recover_interrupted_jobs(connection) -> int:
    now = utc_now()
    cursor = connection.execute("""UPDATE refresh_jobs SET status='failed',finished_at=?,
        message='服务重启导致任务中断',error_summary='服务重启导致任务中断'
        WHERE status IN ('pending','running')""", (now,))
    connection.commit()
    return cursor.rowcount


def create_job(connection, job_type: str) -> int:
    if job_type not in JOB_TYPES:
        raise ValueError("不支持的刷新任务类型")
    connection.execute("BEGIN IMMEDIATE")
    try:
        active = connection.execute("SELECT id,job_type FROM refresh_jobs WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1").fetchone()
        if active:
            raise JobConflictError(f"已有刷新任务运行中（任务 {active['id']}，类型 {active['job_type']}）")
        job_id = connection.execute("INSERT INTO refresh_jobs(job_type,status,message,created_at) VALUES(?,'pending','等待执行',?)",
                                    (job_type, utc_now())).lastrowid
        connection.commit()
        return job_id
    except Exception:
        connection.rollback()
        raise


def get_job(connection, job_id: int):
    row = connection.execute("SELECT * FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def latest_job(connection):
    row = connection.execute("SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _update(connection, job_id: int, **values) -> None:
    if not values:
        return
    assignments = ",".join(f"{key}=?" for key in values)
    connection.execute(f"UPDATE refresh_jobs SET {assignments} WHERE id=?", [*values.values(), job_id])
    connection.commit()


def _progress(connection, job_id: int, counts: Counter, total: int, code: str, message: str) -> None:
    processed = counts["processed"]
    _update(connection, job_id, total=total, processed=processed, success_count=counts["success"],
            no_data_count=counts["no_data"], failure_count=counts["failure"], skipped_count=counts["skipped"],
            progress_pct=round(processed / total * 100, 2) if total else 100, current_code=code, message=message)


def _validate_manual_csv(path: Path) -> list[str]:
    if not path.exists():
        return [f"CSV文件不存在：{path}"]
    temporary = sqlite3.connect(":memory:")
    temporary.row_factory = sqlite3.Row
    migrate(temporary)
    try:
        _, errors = import_file(temporary, path)
        return errors
    finally:
        temporary.close()


def refresh_a_share_job(connection, job_id: int, *, csv_path: Path | None = None,
                        price_fetcher: Callable = fetch_latest_prices_akshare_by_codes) -> None:
    path = csv_path or PROJECT_ROOT / "data" / "manual_a_share_valuations.csv"
    errors = _validate_manual_csv(path)
    if errors:
        raise ValueError("CSV校验失败，未导入估值数据：" + "；".join(errors[:20]))
    imported, import_errors = import_file(connection, path)
    if import_errors:
        raise ValueError("CSV导入失败：" + "；".join(import_errors[:20]))
    stocks = connection.execute("SELECT id,futu_code,symbol FROM stocks WHERE market='A' AND is_active=1 ORDER BY futu_code").fetchall()
    total = len(stocks)
    _update(connection, job_id, total=total, message=f"已导入{imported}条手工估值，正在刷新A股股价")
    frame = price_fetcher([row["symbol"] for row in stocks], retries=2) if stocks else None
    prices = {str(row["stock_code"]).zfill(6): row["current_price"] for _, row in frame.iterrows()} if frame is not None else {}
    counts = Counter()
    for stock in stocks:
        value = prices.get(stock["symbol"])
        result = CollectionResult("success", {"last_price": value, "price_time": utc_now()}) if value else CollectionResult("no_data")
        with connection:
            patch_price(connection, stock["id"], result, "eastmoney")
        counts["processed"] += 1; counts["success" if result.status == "success" else "no_data"] += 1
        _progress(connection, job_id, counts, total, stock["futu_code"], "正在刷新A股股价")
    with connection:
        refresh_quality(connection)
    _finish(connection, job_id, counts, total)


def refresh_hk_prices_job(connection, job_id: int, *, codes: list[str] | None = None,
                          client_factory=FutuResearchClient, batch_size: int = 200) -> None:
    stocks = _hk_stocks(connection, codes)
    total, counts, errors = len(stocks), Counter(), []
    _update(connection, job_id, total=total, message="正在批量刷新港股股价")
    with client_factory() as client:
        for start in range(0, total, batch_size):
            batch = stocks[start:start + batch_size]
            try:
                results = client.batch_snapshots([row["futu_code"] for row in batch], batch_size=batch_size)
            except Exception as exc:
                results = {row["futu_code"]: CollectionResult("connection_error", error=str(exc)) for row in batch}
                errors.append(str(exc))
            with connection:
                for stock in batch:
                    result = results.get(stock["futu_code"], CollectionResult("no_data"))
                    patch_price(connection, stock["id"], result, "futu_opend")
                    counts["processed"] += 1
                    if result.status == "success": counts["success"] += 1
                    elif result.status == "no_data": counts["no_data"] += 1
                    else: counts["failure"] += 1; errors.append(f"{stock['futu_code']}: {result.status} {result.error or ''}")
            if batch:
                _progress(connection, job_id, counts, total, batch[-1]["futu_code"], "正在批量刷新港股股价")
    with connection:
        refresh_quality(connection)
    _finish(connection, job_id, counts, total, errors)


def refresh_hk_ratings_job(connection, job_id: int, *, codes: list[str] | None = None,
                           client_factory=FutuResearchClient) -> None:
    stocks = _hk_stocks(connection, codes)
    total, counts, errors = len(stocks), Counter(), []
    _update(connection, job_id, total=total, message="正在刷新过期的港股评级")
    with client_factory() as client:
        for stock in stocks:
            existing = connection.execute("""SELECT morningstar_next_check_at,analyst_next_check_at
                FROM stock_expectations WHERE stock_id=?""", (stock["id"],)).fetchone()
            morningstar_due = is_due(existing, "morningstar_next_check_at", False)
            analyst_due = is_due(existing, "analyst_next_check_at", False)
            morningstar = client.morningstar(stock["futu_code"]) if morningstar_due else CollectionResult("skipped_fresh")
            analyst = client.analyst(stock["futu_code"]) if analyst_due else CollectionResult("skipped_fresh")
            with connection:
                if morningstar_due: patch_morningstar(connection, stock["id"], morningstar, "futu_opend")
                if analyst_due: patch_analyst(connection, stock["id"], analyst, "futu_opend")
            statuses = [morningstar.status, analyst.status]
            counts["processed"] += 1
            if all(status == "skipped_fresh" for status in statuses): counts["skipped"] += 1
            elif any(status not in {"success", "no_data", "skipped_fresh"} for status in statuses):
                counts["failure"] += 1; errors.append(f"{stock['futu_code']}: {','.join(statuses)}")
            elif all(status in {"no_data", "skipped_fresh"} for status in statuses): counts["no_data"] += 1
            else: counts["success"] += 1
            _progress(connection, job_id, counts, total, stock["futu_code"], "正在刷新过期的港股评级")
    with connection:
        refresh_quality(connection)
    _finish(connection, job_id, counts, total, errors)


def _hk_stocks(connection, codes: list[str] | None):
    if not codes:
        return connection.execute("SELECT id,futu_code FROM stocks WHERE market='HK' AND is_active=1 ORDER BY futu_code").fetchall()
    placeholders = ",".join("?" for _ in codes)
    return connection.execute(f"SELECT id,futu_code FROM stocks WHERE market='HK' AND is_active=1 AND futu_code IN ({placeholders}) ORDER BY futu_code", codes).fetchall()


def _finish(connection, job_id: int, counts: Counter, total: int, errors: Iterable[str] = ()) -> None:
    status = "partial" if counts["failure"] else "success"
    _update(connection, job_id, status=status, processed=counts["processed"], total=total,
            success_count=counts["success"], no_data_count=counts["no_data"], failure_count=counts["failure"],
            skipped_count=counts["skipped"], progress_pct=100, current_code=None,
            message="刷新完成" if status == "success" else "刷新完成，部分记录失败",
            error_summary="；".join(list(errors)[:50]) or None, finished_at=utc_now())


RUNNERS = {"refresh_a_share": refresh_a_share_job, "refresh_hk_prices": refresh_hk_prices_job,
           "refresh_hk_ratings": refresh_hk_ratings_job}


def run_job(job_id: int, *, runner_kwargs: dict | None = None) -> None:
    connection = connect(); migrate(connection)
    try:
        with _worker_lock:
            row = connection.execute("SELECT job_type,status FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in ACTIVE_STATUSES:
                return
            _update(connection, job_id, status="running", started_at=utc_now(), message=JOB_LABELS[row["job_type"]])
            RUNNERS[row["job_type"]](connection, job_id, **(runner_kwargs or {}))
    except Exception as exc:
        _update(connection, job_id, status="failed", message="刷新失败", error_summary=str(exc), finished_at=utc_now())
    finally:
        connection.close()


def start_background_job(job_type: str) -> dict:
    connection = connect(); migrate(connection)
    try:
        job_id = create_job(connection, job_type)
        job = get_job(connection, job_id)
    finally:
        connection.close()
    threading.Thread(target=run_job, args=(job_id,), name=f"refresh-job-{job_id}", daemon=True).start()
    return job
