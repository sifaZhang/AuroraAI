"""Repository for the shared sector_source_status health table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

SOURCES = {
    "sw_l1": "申万一级行业",
    "sw_l2": "申万二级行业",
    "eastmoney": "东方财富行业",
    "benchmark_csi300": "沪深300基准",
}
ERROR_LIMIT = 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"不支持的数据源: {source}")


def _json(metadata: dict[str, Any] | None) -> str:
    return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)


def _clean_error(value: object) -> str | None:
    if value is None:
        return None
    return str(value)[:ERROR_LIMIT]


def ensure_source(connection: sqlite3.Connection, source: str) -> None:
    _validate_source(source)
    now = utc_now()
    connection.execute(
        """INSERT INTO sector_source_status(source,display_name,status,metadata_json,updated_at)
           VALUES(?,?,'unknown','{}',?) ON CONFLICT(source) DO NOTHING""",
        (source, SOURCES[source], now),
    )


def get_status(connection: sqlite3.Connection, source: str) -> dict[str, Any] | None:
    _validate_source(source)
    row = connection.execute("SELECT * FROM sector_source_status WHERE source=?", (source,)).fetchone()
    return _row(row) if row else None


def list_statuses(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    for source in SOURCES:
        ensure_source(connection, source)
    rows = connection.execute(
        "SELECT * FROM sector_source_status ORDER BY CASE source WHEN 'sw_l1' THEN 1 WHEN 'sw_l2' THEN 2 WHEN 'eastmoney' THEN 3 ELSE 4 END"
    ).fetchall()
    return [_row(row) for row in rows]


def _row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def record_success(
    connection: sqlite3.Connection, source: str, *, latency_ms: float, metadata: dict[str, Any] | None = None
) -> None:
    ensure_source(connection, source)
    now = utc_now()
    sector_count = int((metadata or {}).get("sector_count", 0))
    connection.execute(
        """UPDATE sector_source_status SET status='healthy',sector_count=?,successful_sector_count=?,
           failed_sector_count=0,last_attempt_at=?,last_success_at=?,last_error=NULL,last_error_type=NULL,
           last_error_message=NULL,latency_ms=?,consecutive_failures=0,total_successes=total_successes+1,
           metadata_json=?,elapsed_seconds=?,updated_at=? WHERE source=?""",
        (sector_count, sector_count, now, now, latency_ms, _json(metadata), latency_ms / 1000, now, source),
    )


def record_degraded(
    connection: sqlite3.Connection, source: str, *, error_type: str, error_message: str,
    latency_ms: float, metadata: dict[str, Any] | None = None,
) -> None:
    ensure_source(connection, source)
    now = utc_now()
    message = _clean_error(error_message)
    error_type = _clean_error(error_type) or "Degraded"
    connection.execute(
        """UPDATE sector_source_status SET status='degraded',last_attempt_at=?,last_failure_at=?,
           last_error=?,last_error_type=?,last_error_message=?,latency_ms=?,
           consecutive_failures=consecutive_failures+1,total_failures=total_failures+1,
           metadata_json=?,elapsed_seconds=?,updated_at=? WHERE source=?""",
        (now, now, message, error_type, message, latency_ms, _json(metadata), latency_ms / 1000, now, source),
    )


def record_failure(
    connection: sqlite3.Connection, source: str, *, error_type: str, error_message: str,
    latency_ms: float, metadata: dict[str, Any] | None = None,
) -> None:
    ensure_source(connection, source)
    now = utc_now()
    message = _clean_error(error_message)
    error_type = _clean_error(error_type) or "UnknownError"
    connection.execute(
        """UPDATE sector_source_status SET status='unavailable',last_attempt_at=?,last_failure_at=?,
           last_error=?,last_error_type=?,last_error_message=?,latency_ms=?,
           consecutive_failures=consecutive_failures+1,total_failures=total_failures+1,
           metadata_json=?,elapsed_seconds=?,updated_at=? WHERE source=?""",
        (now, now, message, error_type, message, latency_ms, _json(metadata), latency_ms / 1000, now, source),
    )
