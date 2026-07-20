"""Lightweight, isolated industry-list health checks."""

from __future__ import annotations

import time
from dataclasses import dataclass

from backend.collector.dividend_collector import get_akshare
from backend.collector.probe_sector_data import describe_source_error, find_column
from backend.sector_radar.health_repository import (
    SOURCES, list_statuses, record_degraded, record_failure, record_success,
)

CHECK_ORDER = ("sw_l1", "sw_l2", "eastmoney")
EASTMONEY_DELAYS = (5.0, 15.0, 30.0)


@dataclass(frozen=True)
class CheckOutcome:
    source: str
    status: str
    latency_ms: float
    sector_count: int
    error_type: str | None = None
    error_message: str | None = None


def _fetch(ak: object, source: str):
    if source == "sw_l1":
        return ak.index_realtime_sw(symbol="一级行业")
    if source == "sw_l2":
        return ak.index_realtime_sw(symbol="二级行业")
    errors = []
    for attempt in range(4):
        try:
            return ak.stock_board_industry_name_em()
        except Exception as exc:
            errors.append(exc)
            if attempt < 3:
                time.sleep(EASTMONEY_DELAYS[attempt])
    raise errors[-1]


def check_one(ak: object, source: str) -> CheckOutcome:
    if source not in SOURCES:
        raise ValueError(f"不支持的数据源: {source}")
    started = time.monotonic()
    try:
        frame = _fetch(ak, source)
        latency = round((time.monotonic() - started) * 1000, 2)
        if frame is None or frame.empty:
            return CheckOutcome(source, "degraded", latency, 0, "EmptyResponse", "行业列表为空")
        try:
            if source.startswith("sw_"):
                find_column(frame, ("指数代码",), "指数代码")
                find_column(frame, ("指数名称",), "指数名称")
            else:
                find_column(frame, ("板块代码", "代码"), "板块代码")
                find_column(frame, ("板块名称", "名称"), "板块名称")
        except ValueError as exc:
            return CheckOutcome(source, "degraded", latency, len(frame), "SchemaError", str(exc))
        return CheckOutcome(source, "healthy", latency, len(frame))
    except Exception as exc:
        latency = round((time.monotonic() - started) * 1000, 2)
        if source == "sw_l2" and isinstance(exc, KeyError) and "data" in str(exc):
            message = f"missing data field / KeyError data; {type(exc).__name__}: {exc}"
        else:
            message = describe_source_error(source, exc)
        return CheckOutcome(source, "unavailable", latency, 0, type(exc).__name__, message)


def run_health_checks(connection, selection: str = "all", *, ak: object | None = None) -> list[dict]:
    if selection not in {*CHECK_ORDER, "all"}:
        raise ValueError(f"不支持的数据源: {selection}")
    client = ak or get_akshare()
    sources = CHECK_ORDER if selection == "all" else (selection,)
    for source in sources:
        outcome = check_one(client, source)
        metadata = {"sector_count": outcome.sector_count, "check_type": "industry_list"}
        if outcome.status == "healthy":
            record_success(connection, source, latency_ms=outcome.latency_ms, metadata=metadata)
        elif outcome.status == "degraded":
            record_degraded(
                connection, source, error_type=outcome.error_type or "Degraded",
                error_message=outcome.error_message or "部分可用", latency_ms=outcome.latency_ms, metadata=metadata,
            )
        else:
            record_failure(
                connection, source, error_type=outcome.error_type or "UnknownError",
                error_message=outcome.error_message or "数据源不可用", latency_ms=outcome.latency_ms, metadata=metadata,
            )
        connection.commit()
    return [row for row in list_statuses(connection) if selection == "all" or row["source"] == selection]
