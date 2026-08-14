from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Iterable

from backend.collector.import_manual_a_share_valuations import import_file
from backend.collector.init_hk_expectations import is_due
from backend.expectation_gap.database import (
    PROJECT_ROOT, DatabaseWriteBusyError, acquire_write_job, connect, migrate,
    release_write_job,
)
from backend.expectation_gap.futu_client import CollectionResult, FutuResearchClient, utc_now
from backend.data_sources.market_price_provider import MarketPriceProvider, UnifiedMarketPriceProvider
from backend.expectation_gap.quality import refresh_quality
from backend.expectation_gap.repository import patch_analyst, patch_morningstar, patch_price
from backend.collector.dividend_collector import get_akshare
from backend.sector_radar.service import refresh_source, sources_for

JOB_TYPES = {"refresh_a_share", "refresh_hk_prices", "refresh_hk_ratings", "refresh_market_pulse"}
ACTIVE_STATUSES = {"pending", "running"}
JOB_LABELS = {
    "refresh_a_share": "正在刷新A股",
    "refresh_hk_prices": "正在刷新港股股价",
    "refresh_hk_ratings": "正在刷新港股评级",
    "refresh_market_pulse": "正在刷新行业趋势",
}
_worker_lock = threading.Lock()


class JobConflictError(RuntimeError):
    def __init__(self, message: str, existing_job_id: int | None = None):
        super().__init__(message)
        self.existing_job_id = existing_job_id


def recover_interrupted_jobs(connection) -> int:
    now = utc_now()
    cursor = connection.execute("""UPDATE refresh_jobs SET status='failed',finished_at=?,
        message='服务重启导致任务中断',error_summary='服务重启导致任务中断'
        WHERE status IN ('pending','running')""", (now,))
    connection.commit()
    return cursor.rowcount


def create_job(connection, job_type: str, *, source: str | None = None) -> int:
    if job_type not in JOB_TYPES:
        raise ValueError("不支持的刷新任务类型")
    if not acquire_write_job(blocking=False):
        raise DatabaseWriteBusyError("另一个数据写入任务正在运行，请等待其完成后再刷新")
    try:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute("SELECT id,job_type FROM refresh_jobs WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1").fetchone()
        if active:
            raise JobConflictError(f"已有刷新任务运行中（任务 {active['id']}，类型 {active['job_type']}）", active["id"])
        job_id = connection.execute("INSERT INTO refresh_jobs(job_type,source,status,message,created_at) VALUES(?,?,'pending','等待执行',?)",
                                    (job_type, source, utc_now())).lastrowid
        connection.commit()
        return job_id
    except sqlite3.OperationalError as exc:
        connection.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise DatabaseWriteBusyError("数据库正被其他写入操作占用，请稍后重试") from exc
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        release_write_job()


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


def refresh_a_share_job(connection, job_id: int, *, csv_path: Path | None = None, codes: list[str] | None = None,
                        price_provider: MarketPriceProvider | None = None) -> None:
    imported = 0
    errors: list[str] = []
    if not codes:
        path = csv_path or PROJECT_ROOT / "data" / "manual_a_share_valuations.csv"
        errors = _validate_manual_csv(path)
        if errors:
            raise ValueError("CSV校验失败，未导入估值数据：" + "；".join(errors[:20]))
        imported, import_errors = import_file(connection, path)
        if import_errors:
            raise ValueError("CSV导入失败：" + "；".join(import_errors[:20]))
    query = "SELECT id,futu_code,symbol FROM stocks WHERE market='A' AND is_active=1"
    args = []
    if codes:
        values = [code[2:] if code.startswith("A.") else code for code in codes]
        query += " AND symbol IN (" + ",".join("?" for _ in values) + ")"; args = values
    stocks = connection.execute(query + " ORDER BY futu_code", args).fetchall()
    total = len(stocks)
    _update(connection, job_id, total=total, message=("正在刷新单只A股股价" if codes else f"已导入{imported}条手工估值，正在刷新A股股价"))
    symbols = [row["symbol"] for row in stocks]
    provider = price_provider or UnifiedMarketPriceProvider()
    if codes and stocks and price_provider is None:
        stock = stocks[0]
        futu_code = str(stock["futu_code"])
        ts_code = f"{futu_code[3:]}.{futu_code[:2]}" if futu_code[:2] in {"SH", "SZ"} else str(stock["symbol"])
        single = UnifiedMarketPriceProvider().fetch_a_share_single_latest(ts_code)
        prices = {str(stock["symbol"])[:6]: single}
    else:
        prices = provider.fetch_a_share_latest(symbols, progress=lambda message: _update(connection, job_id, message=message)) if stocks else {}
    counts = Counter()
    for stock in stocks:
        value = prices.get(str(stock["symbol"])[:6])
        result = (CollectionResult("success", {"last_price": value.price, "price_time": value.price_time or utc_now()})
                  if value is not None and value.status == "success" else CollectionResult("no_data"))
        with connection:
            patch_price(connection, stock["id"], result, value.source if value and value.source else "auto")
        counts["processed"] += 1; counts["success" if result.status == "success" else "no_data"] += 1
        _progress(connection, job_id, counts, total, stock["futu_code"], "正在刷新A股股价")
    with connection:
        refresh_quality(connection)
    _finish(
        connection, job_id, counts, total,
        errors=("A-share price refresh returned no matched prices",)
        if total and not counts["success"] else (),
        partial_when_no_data=True,
    )


def refresh_hk_prices_job(connection, job_id: int, *, codes: list[str] | None = None,
                          price_provider: MarketPriceProvider | None = None, batch_size: int = 200) -> None:
    stocks = _hk_stocks(connection, codes)
    total, counts, errors = len(stocks), Counter(), []
    _update(connection, job_id, total=total, message="正在批量刷新港股股价")
    provider = price_provider or UnifiedMarketPriceProvider()
    for start in range(0, total, batch_size):
        batch = stocks[start:start + batch_size]
        outcomes = provider.fetch_hk_latest(
            [row["futu_code"] for row in batch], batch_size=batch_size,
            progress=lambda message: _update(connection, job_id, message=message),
        )
        with connection:
            for stock in batch:
                outcome = outcomes.get(stock["futu_code"])
                result = (CollectionResult("success", {"last_price": outcome.price, "price_time": outcome.price_time or utc_now()})
                          if outcome is not None and outcome.status == "success"
                          else CollectionResult(outcome.status, error=outcome.error) if outcome is not None else CollectionResult("no_data"))
                patch_price(connection, stock["id"], result, outcome.source if outcome and outcome.source else "auto")
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
                           force: bool = False, client_factory=FutuResearchClient) -> None:
    stocks = _hk_stocks(connection, codes)
    total, counts, errors = len(stocks), Counter(), []
    _update(connection, job_id, total=total, message="正在刷新过期的港股评级")
    with client_factory(max_retries=1) if force else client_factory() as client:
        for stock in stocks:
            existing = connection.execute("""SELECT morningstar_next_check_at,analyst_next_check_at
                FROM stock_expectations WHERE stock_id=?""", (stock["id"],)).fetchone()
            morningstar_due = is_due(existing, "morningstar_next_check_at", force)
            analyst_due = is_due(existing, "analyst_next_check_at", force)
            morningstar = client.morningstar(stock["futu_code"]) if morningstar_due else CollectionResult("skipped_fresh")
            analyst = client.analyst(stock["futu_code"]) if analyst_due else CollectionResult("skipped_fresh")
            price = UnifiedMarketPriceProvider().fetch_hk_latest([stock["futu_code"]]).get(stock["futu_code"]) if force else None
            with connection:
                if morningstar_due: patch_morningstar(connection, stock["id"], morningstar, "futu_opend")
                if analyst_due: patch_analyst(connection, stock["id"], analyst, "futu_opend")
                if price and price.status == "success": patch_price(connection, stock["id"], CollectionResult("success", {"last_price": price.price, "price_time": price.price_time or utc_now()}), price.source or "auto")
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


def refresh_market_pulse_job(connection, job_id: int, *, source: str = "sw_l1", ak=None) -> None:
    selected = sources_for(source)
    total_sources = len(selected)
    _update(connection, job_id, total=total_sources, processed=0, progress_pct=0, message="准备刷新行业趋势")
    client = ak or get_akshare()
    results, errors = [], []
    saved_total = 0
    for source_index, current_source in enumerate(selected):
        def report(completed: int, total: int, current: str) -> None:
            fraction = completed / total if total else 1
            progress_pct = round((source_index + fraction) / total_sources * 100, 2)
            _update(
                connection, job_id, current_code=current, progress_pct=progress_pct,
                message=f"正在刷新 {current_source}：{completed}/{total}",
            )

        outcome = refresh_source(connection, current_source, ak=client, progress=report)
        results.append(outcome)
        saved_total += outcome.saved_count
        status = outcome.source_result.status
        if status.status != "available":
            errors.append(f"{current_source}: {status.last_error or status.status}"[:1000])
        if outcome.relative_strength_failures:
            errors.extend(f"relative_strength: {error}"[:1000] for error in outcome.relative_strength_failures[:20])
        _update(
            connection, job_id, processed=source_index + 1,
            success_count=sum(item.source_result.status.status == "available" for item in results),
            failure_count=sum(item.source_result.status.status != "available" for item in results),
            progress_pct=round((source_index + 1) / total_sources * 100, 2),
            current_code=current_source, message=f"{current_source} 刷新完成，保存 {outcome.saved_count} 条",
        )
    sw_l1_failed = source == "all" and results[0].source_result.status.status == "unavailable"
    any_problem = any(item.source_result.status.status != "available" or item.module_partial for item in results)
    if sw_l1_failed or (source != "all" and results[0].source_result.status.status == "unavailable"):
        final_status = "failed"
    elif any_problem:
        final_status = "partial"
    else:
        final_status = "success"
    _update(
        connection, job_id, status=final_status, processed=total_sources, progress_pct=100,
        current_code=None, message=f"行业趋势刷新完成，保存 {saved_total} 条",
        error_summary="；".join(errors)[:1000] or None, finished_at=utc_now(),
    )


def _hk_stocks(connection, codes: list[str] | None):
    if not codes:
        return connection.execute("SELECT id,futu_code FROM stocks WHERE market='HK' AND is_active=1 ORDER BY futu_code").fetchall()
    placeholders = ",".join("?" for _ in codes)
    return connection.execute(f"SELECT id,futu_code FROM stocks WHERE market='HK' AND is_active=1 AND futu_code IN ({placeholders}) ORDER BY futu_code", codes).fetchall()


def _finish(connection, job_id: int, counts: Counter, total: int, errors: Iterable[str] = (),
            partial_when_no_data: bool = False) -> None:
    status = "partial" if counts["failure"] or (
        partial_when_no_data and counts["no_data"]
    ) else "success"
    _update(connection, job_id, status=status, processed=counts["processed"], total=total,
            success_count=counts["success"], no_data_count=counts["no_data"], failure_count=counts["failure"],
            skipped_count=counts["skipped"], progress_pct=100, current_code=None,
            message="刷新完成" if status == "success" else "刷新完成，部分记录失败",
            error_summary="；".join(list(errors)[:50]) or None, finished_at=utc_now())


RUNNERS = {"refresh_a_share": refresh_a_share_job, "refresh_hk_prices": refresh_hk_prices_job,
           "refresh_hk_ratings": refresh_hk_ratings_job, "refresh_market_pulse": refresh_market_pulse_job}


def run_job(job_id: int, *, runner_kwargs: dict | None = None) -> None:
    connection = connect(); migrate(connection)
    try:
        acquire_write_job(blocking=True)
        try:
            with _worker_lock:
                row = connection.execute("SELECT job_type,source,status FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
                if row is None or row["status"] not in ACTIVE_STATUSES:
                    return
                _update(connection, job_id, status="running", started_at=utc_now(), message=JOB_LABELS[row["job_type"]])
                kwargs = dict(runner_kwargs or {})
                if row["job_type"] == "refresh_market_pulse":
                    kwargs.setdefault("source", row["source"] or "sw_l1")
                if row["job_type"] == "refresh_hk_ratings" and row["source"]:
                    kwargs.setdefault("codes", [row["source"]])
                    kwargs.setdefault("force", True)
                if row["job_type"] == "refresh_a_share" and row["source"]:
                    kwargs.setdefault("codes", [row["source"]])
                RUNNERS[row["job_type"]](connection, job_id, **kwargs)
        finally:
            release_write_job()
    except Exception as exc:
        _update(connection, job_id, status="failed", message="刷新失败", error_summary=str(exc), finished_at=utc_now())
    finally:
        connection.close()


def start_background_job(job_type: str, *, source: str | None = None) -> dict:
    if not acquire_write_job(blocking=False):
        raise DatabaseWriteBusyError("另一个数据写入任务正在运行，请等待其完成后再刷新")
    connection = connect()
    try:
        migrate(connection)
        job_id = create_job(connection, job_type, source=source)
        job = get_job(connection, job_id)
    finally:
        connection.close()
        release_write_job()
    threading.Thread(target=run_job, args=(job_id,), name=f"refresh-job-{job_id}", daemon=True).start()
    return job
