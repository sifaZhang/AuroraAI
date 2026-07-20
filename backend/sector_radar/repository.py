"""SQLite persistence for multi-source, trend-only sector data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


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
    now = datetime.now().astimezone().isoformat(timespec="seconds")
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
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    last_success_at = now if record.status in {"available", "partial"} and record.successful_sector_count > 0 else None
    connection.execute(
        """INSERT INTO sector_source_status(
               source,status,sector_count,successful_sector_count,failed_sector_count,
               last_attempt_at,last_success_at,last_error,elapsed_seconds,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source) DO UPDATE SET
               status=excluded.status,sector_count=excluded.sector_count,
               successful_sector_count=excluded.successful_sector_count,
               failed_sector_count=excluded.failed_sector_count,last_attempt_at=excluded.last_attempt_at,
               last_success_at=COALESCE(excluded.last_success_at,sector_source_status.last_success_at),
               last_error=excluded.last_error,elapsed_seconds=excluded.elapsed_seconds,
               updated_at=excluded.updated_at""",
        (
            record.source,
            record.status,
            record.sector_count,
            record.successful_sector_count,
            record.failed_sector_count,
            now,
            last_success_at,
            record.last_error,
            record.elapsed_seconds,
            now,
        ),
    )
