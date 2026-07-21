"""Reusable sector trend refresh service with per-source transactions."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable

from backend.collector.dividend_collector import get_akshare
from backend.collector.probe_sector_data import (
    SectorTrend, SourceResult, SourceStatus, describe_source_error, load_history, load_industries, to_trend,
)
from backend.collector.collect_sector_scores import persist_results
from backend.sector_radar.benchmark import load_csi300_benchmark
from backend.sector_radar.health_repository import record_failure, record_success, utc_now
from backend.sector_radar.relative_strength import calculate_relative_strength

SOURCE_ORDER = ("sw_l1", "sw_l2", "eastmoney")
DEFAULT_SW_WORKERS = 4
MIN_SW_WORKERS = 1
MAX_SW_WORKERS = 8


def get_sw_worker_count() -> int:
    """Return the bounded worker count for independent SW L1 history calls."""

    raw_value = os.getenv("MARKET_PULSE_SW_WORKERS")
    if raw_value is None:
        return DEFAULT_SW_WORKERS
    try:
        worker_count = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_SW_WORKERS
    if not MIN_SW_WORKERS <= worker_count <= MAX_SW_WORKERS:
        return DEFAULT_SW_WORKERS
    return worker_count


@dataclass(frozen=True)
class RefreshResult:
    source_result: SourceResult
    saved_count: int
    relative_strength_success_count: int = 0
    relative_strength_failures: tuple[str, ...] = ()

    @property
    def module_partial(self) -> bool:
        return bool(self.relative_strength_failures)


@dataclass(frozen=True)
class _IndustryResult:
    index: int
    trend: SectorTrend | None = None
    error: str | None = None
    relative_strength_failure: str | None = None


def _process_industry(index, client, industry, source, benchmark, benchmark_error) -> _IndustryResult:
    """Fetch and score one industry without touching SQLite or job state."""

    try:
        bars = load_history(client, industry)
        trend = to_trend(industry, bars)
        rs_failure = None
        if source == "sw_l1":
            if benchmark is None:
                rs_failure = f"{industry.code} {industry.name}: {benchmark_error or 'benchmark_unavailable'}"
            else:
                try:
                    metrics = calculate_relative_strength(bars, benchmark.bars, benchmark_code=benchmark.code)
                    trend = replace(
                        trend, relative_strength_score=metrics.score,
                        benchmark_code=metrics.benchmark_code, benchmark_trade_date=metrics.benchmark_trade_date,
                        sector_return_5d=metrics.sector_return_5d, benchmark_return_5d=metrics.benchmark_return_5d,
                        excess_return_5d=metrics.excess_return_5d,
                        sector_return_10d=metrics.sector_return_10d, benchmark_return_10d=metrics.benchmark_return_10d,
                        excess_return_10d=metrics.excess_return_10d,
                        sector_return_20d=metrics.sector_return_20d, benchmark_return_20d=metrics.benchmark_return_20d,
                        excess_return_20d=metrics.excess_return_20d,
                        relative_strength_updated_at=utc_now(), score_status="partial",
                        missing_components=("capital_flow",),
                    )
                except Exception as exc:
                    rs_failure = f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"[:1000]
        return _IndustryResult(index, trend=trend, relative_strength_failure=rs_failure)
    except Exception as exc:
        error = f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"[:1000]
        return _IndustryResult(index, error=error)


def refresh_source(
    connection, source: str, *, ak: object | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> RefreshResult:
    client = ak or get_akshare()
    started = time.monotonic()
    benchmark = None
    benchmark_error: str | None = None
    if source == "sw_l1":
        try:
            benchmark = load_csi300_benchmark(client)
            record_success(
                connection, "benchmark_csi300", latency_ms=benchmark.elapsed_seconds * 1000,
                metadata={"benchmark_code": benchmark.code, "data_source": benchmark.source,
                          "sector_count": benchmark.row_count, "returned_rows": benchmark.row_count,
                          "latest_trade_date": benchmark.latest_trade_date},
            )
        except Exception as exc:
            benchmark_error = f"{type(exc).__name__}: {exc}"[:1000]
            record_failure(
                connection, "benchmark_csi300", error_type=type(exc).__name__,
                error_message=benchmark_error, latency_ms=0, metadata={"benchmark_code": "000300"},
            )
    try:
        _, industries = load_industries(client, source)
    except Exception as exc:
        error = describe_source_error(source, exc)[:1000]
        result = SourceResult(SourceStatus(source, "unavailable", 0, 0, 0, error, round(time.monotonic() - started, 2)), ())
        persist_results(connection, [result])
        return RefreshResult(result, 0)

    completed_results: list[_IndustryResult] = []
    total = len(industries)
    if source == "sw_l1":
        worker_count = get_sw_worker_count()
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sw-l1-history") as executor:
            future_to_industry = {
                executor.submit(
                    _process_industry, index, client, industry, source, benchmark, benchmark_error,
                ): (index, industry)
                for index, industry in enumerate(industries)
            }
            for completed, future in enumerate(as_completed(future_to_industry), start=1):
                index, industry = future_to_industry[future]
                try:
                    completed_results.append(future.result())
                except Exception as exc:  # Defensive: a worker must not abort the refresh.
                    error = f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"[:1000]
                    completed_results.append(_IndustryResult(index, error=error))
                if progress:
                    progress(completed, total, f"{industry.code} {industry.name}")
    else:
        for completed, industry in enumerate(industries, start=1):
            completed_results.append(
                _process_industry(completed - 1, client, industry, source, benchmark, benchmark_error)
            )
            if progress:
                progress(completed, total, f"{industry.code} {industry.name}")

    completed_results.sort(key=lambda item: item.index)
    trends = [item.trend for item in completed_results if item.trend is not None]
    errors = [item.error for item in completed_results if item.error is not None]
    rs_failures = [
        item.relative_strength_failure for item in completed_results
        if item.relative_strength_failure is not None
    ]
    state = "available" if not errors else ("partial" if trends else "unavailable")
    status = SourceStatus(
        source, state, total, len(trends), total - len(trends), errors[-1] if errors else None,
        round(time.monotonic() - started, 2),
    )
    result = SourceResult(status, tuple(trends))
    saved = persist_results(connection, [result])
    rs_success = sum(item.relative_strength_score is not None for item in trends)
    return RefreshResult(result, saved, rs_success, tuple(rs_failures))


def sources_for(selection: str) -> tuple[str, ...]:
    if selection == "all":
        return SOURCE_ORDER
    if selection not in SOURCE_ORDER:
        raise ValueError(f"不支持的数据源: {selection}")
    return (selection,)
