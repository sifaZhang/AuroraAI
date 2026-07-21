"""SQLite persistence for multi-source, trend-only sector data."""

from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.sector_radar.health_repository import record_degraded, record_failure, record_success


@dataclass(frozen=True)
class SectorScoreRecord:
    source: str
    sector_level: int | str
    sector_code: str
    sector_name: str
    trade_date: str
    trend_score: int
    trend_level: str
    close: float
    ma5: float
    ma10: float
    ma20: float
    volume_ratio: float
    is_20d_high: bool
    relative_strength_score: int | None = None
    benchmark_code: str | None = None
    benchmark_trade_date: str | None = None
    sector_return_5d: float | None = None
    benchmark_return_5d: float | None = None
    excess_return_5d: float | None = None
    sector_return_10d: float | None = None
    benchmark_return_10d: float | None = None
    excess_return_10d: float | None = None
    sector_return_20d: float | None = None
    benchmark_return_20d: float | None = None
    excess_return_20d: float | None = None
    relative_strength_updated_at: str | None = None
    capital_flow_score: int | None = None
    composite_score: int | None = None
    score_status: str = "partial"
    missing_components: tuple[str, ...] = ("capital_flow", "relative_strength")


@dataclass(frozen=True)
class SourceStatusRecord:
    source: str
    status: str
    sector_count: int
    successful_sector_count: int
    failed_sector_count: int
    last_error: str | None
    elapsed_seconds: float


def upsert_sector_scores(connection: sqlite3.Connection, records: list[SectorScoreRecord]) -> int:
    """Persist only real calculated scores; an empty list writes no placeholder rows."""

    if not records:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = [
        (
            row.source,
            str(row.sector_level),
            row.sector_code,
            row.sector_name,
            row.trade_date,
            row.trend_score,
            row.trend_level,
            row.close,
            row.ma5,
            row.ma10,
            row.ma20,
            row.volume_ratio,
            int(row.is_20d_high),
            row.relative_strength_score, row.benchmark_code, row.benchmark_trade_date,
            row.sector_return_5d, row.benchmark_return_5d, row.excess_return_5d,
            row.sector_return_10d, row.benchmark_return_10d, row.excess_return_10d,
            row.sector_return_20d, row.benchmark_return_20d, row.excess_return_20d,
            row.relative_strength_updated_at, row.capital_flow_score, row.composite_score,
            row.score_status, json.dumps(row.missing_components, ensure_ascii=False),
            now,
        )
        for row in records
    ]
    connection.executemany(
        """INSERT INTO sector_scores(
               source,sector_level,sector_code,sector_name,trade_date,trend_score,trend_level,
               close,ma5,ma10,ma20,volume_ratio,is_20d_high,
               relative_strength_score,benchmark_code,benchmark_trade_date,
               sector_return_5d,benchmark_return_5d,excess_return_5d,
               sector_return_10d,benchmark_return_10d,excess_return_10d,
               sector_return_20d,benchmark_return_20d,excess_return_20d,
               relative_strength_updated_at,capital_flow_score,composite_score,score_status,
               missing_components,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source,sector_code,trade_date) DO UPDATE SET
               sector_level=excluded.sector_level,sector_name=excluded.sector_name,
               trend_score=excluded.trend_score,trend_level=excluded.trend_level,
               close=excluded.close,ma5=excluded.ma5,ma10=excluded.ma10,ma20=excluded.ma20,
               volume_ratio=excluded.volume_ratio,is_20d_high=excluded.is_20d_high,
               relative_strength_score=excluded.relative_strength_score,
               benchmark_code=excluded.benchmark_code,benchmark_trade_date=excluded.benchmark_trade_date,
               sector_return_5d=excluded.sector_return_5d,benchmark_return_5d=excluded.benchmark_return_5d,
               excess_return_5d=excluded.excess_return_5d,
               sector_return_10d=excluded.sector_return_10d,benchmark_return_10d=excluded.benchmark_return_10d,
               excess_return_10d=excluded.excess_return_10d,
               sector_return_20d=excluded.sector_return_20d,benchmark_return_20d=excluded.benchmark_return_20d,
               excess_return_20d=excluded.excess_return_20d,
               relative_strength_updated_at=excluded.relative_strength_updated_at,
               capital_flow_score=excluded.capital_flow_score,composite_score=excluded.composite_score,
               score_status=excluded.score_status,missing_components=excluded.missing_components,
               updated_at=excluded.updated_at""",
        values,
    )
    return len(values)


def upsert_source_status(connection: sqlite3.Connection, record: SourceStatusRecord) -> None:
    metadata = {
        "sector_count": record.sector_count,
        "successful_sector_count": record.successful_sector_count,
        "failed_sector_count": record.failed_sector_count,
        "check_type": "trend_collection",
    }
    latency_ms = record.elapsed_seconds * 1000
    if record.status == "available":
        record_success(connection, record.source, latency_ms=latency_ms, metadata=metadata)
    elif record.status == "partial":
        record_degraded(
            connection, record.source, error_type="PartialCollection",
            error_message=record.last_error or "部分行业采集失败", latency_ms=latency_ms, metadata=metadata,
        )
    else:
        record_failure(
            connection, record.source, error_type="CollectionUnavailable",
            error_message=record.last_error or "行业采集不可用", latency_ms=latency_ms, metadata=metadata,
        )
    connection.execute(
        """UPDATE sector_source_status SET sector_count=?,successful_sector_count=?,failed_sector_count=?
           WHERE source=?""",
        (record.sector_count, record.successful_sector_count, record.failed_sector_count, record.source),
    )
