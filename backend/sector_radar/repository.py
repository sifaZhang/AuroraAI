"""SQLite persistence for multi-source, trend-only sector data."""

from __future__ import annotations

import sqlite3
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
            now,
        )
        for row in records
    ]
    connection.executemany(
        """INSERT INTO sector_scores(
               source,sector_level,sector_code,sector_name,trade_date,trend_score,trend_level,
               close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source,sector_code,trade_date) DO UPDATE SET
               sector_level=excluded.sector_level,sector_name=excluded.sector_name,
               trend_score=excluded.trend_score,trend_level=excluded.trend_level,
               close=excluded.close,ma5=excluded.ma5,ma10=excluded.ma10,ma20=excluded.ma20,
               volume_ratio=excluded.volume_ratio,is_20d_high=excluded.is_20d_high,
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
