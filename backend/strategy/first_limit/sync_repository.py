"""SQLite access for PR6.2 strategy metadata, minute cache, and resumable runs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping

from .contracts import DataSource, QualityFlag, SecurityId
from .rules import normalize_symbol

SYNC_VERSION = "pr6.2"


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value)).isoformat()


def _flags(flags: Iterable[QualityFlag | str]) -> str:
    return json.dumps(sorted(item.value if isinstance(item, QualityFlag) else str(item) for item in flags), ensure_ascii=False)


def _number(value: Decimal | float | str | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class DailyMetadata:
    symbol: SecurityId
    trade_date: date
    pre_close: Decimal | float | str | None
    source_upper_limit: Decimal | float | str | None
    source_lower_limit: Decimal | float | str | None
    data_source: DataSource = DataSource.GM
    quality_flags: frozenset[QualityFlag] = frozenset()


@dataclass(frozen=True)
class MinuteBar:
    symbol: SecurityId
    bar_time: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    data_source: DataSource = DataSource.GM
    adjustment: str = "none"
    quality_flags: frozenset[QualityFlag] = frozenset()


def upsert_daily_metadata(connection: sqlite3.Connection, values: Iterable[DailyMetadata], updated_at=None) -> int:
    now, records = _timestamp(updated_at), list(values)
    payload = [(item.symbol.canonical, _date(item.trade_date), _number(item.pre_close),
                _number(item.source_upper_limit), _number(item.source_lower_limit), item.data_source.value,
                _flags(item.quality_flags), now) for item in records]
    with connection:
        connection.executemany(
            """INSERT INTO first_limit_daily_metadata(symbol,trade_date,pre_close,source_upper_limit,source_lower_limit,data_source,quality_flags,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
               pre_close=COALESCE(excluded.pre_close,first_limit_daily_metadata.pre_close),
               source_upper_limit=COALESCE(excluded.source_upper_limit,first_limit_daily_metadata.source_upper_limit),
               source_lower_limit=COALESCE(excluded.source_lower_limit,first_limit_daily_metadata.source_lower_limit),
               data_source=excluded.data_source,quality_flags=excluded.quality_flags,updated_at=excluded.updated_at""", payload,
        )
    return len(payload)


def get_daily_metadata(connection: sqlite3.Connection, symbols: Iterable[SecurityId | object],
                       start_date: date | str, end_date: date | str) -> list[sqlite3.Row]:
    items = sorted({(item if isinstance(item, SecurityId) else normalize_symbol(item)).canonical for item in symbols})
    if not items:
        return []
    placeholders = ",".join("?" for _ in items)
    return connection.execute(
        f"SELECT * FROM first_limit_daily_metadata WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ? ORDER BY symbol,trade_date",
        (*items, _date(start_date), _date(end_date)),
    ).fetchall()


def upsert_minute_bars(connection: sqlite3.Connection, values: Iterable[MinuteBar], updated_at=None) -> int:
    now, records = _timestamp(updated_at), list(values)
    payload = []
    for item in records:
        if item.adjustment != "none":
            raise ValueError("minute cache only accepts unadjusted bars")
        moment = item.bar_time
        if moment.tzinfo is None:
            raise ValueError("minute bar_time must include timezone")
        payload.append((item.symbol.canonical, moment.isoformat(timespec="seconds"), "1m", item.open, item.high,
                        item.low, item.close, item.volume, item.amount, item.data_source.value, item.adjustment,
                        _flags(item.quality_flags), now))
    with connection:
        connection.executemany(
            """INSERT INTO first_limit_minute_bars(symbol,bar_time,timeframe,open,high,low,close,volume,amount,data_source,adjustment,quality_flags,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,bar_time,timeframe) DO UPDATE SET
               open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,
               amount=excluded.amount,data_source=excluded.data_source,quality_flags=excluded.quality_flags,updated_at=excluded.updated_at""", payload,
        )
    return len(payload)


def get_minute_bars(connection: sqlite3.Connection, symbol: SecurityId | object, start_time: datetime, end_time: datetime) -> list[sqlite3.Row]:
    security = symbol if isinstance(symbol, SecurityId) else normalize_symbol(symbol)
    if start_time.tzinfo is None or end_time.tzinfo is None:
        raise ValueError("minute query requires timezone-aware timestamps")
    return connection.execute(
        """SELECT * FROM first_limit_minute_bars WHERE symbol=? AND timeframe='1m' AND bar_time BETWEEN ? AND ? ORDER BY bar_time""",
        (security.canonical, start_time.isoformat(timespec="seconds"), end_time.isoformat(timespec="seconds")),
    ).fetchall()


def create_run(connection: sqlite3.Connection, sync_type: str, parameters: Mapping[str, object], *,
               data_source: DataSource = DataSource.GM, dry_run: bool = False, run_id: str | None = None) -> str:
    identifier, now = run_id or uuid.uuid4().hex, _timestamp()
    with connection:
        connection.execute(
            """INSERT INTO first_limit_sync_runs(run_id,sync_type,parameters_json,status,data_source,is_dry_run,sync_version,started_at,created_at,updated_at)
               VALUES(?,?,?,'running',?,?,?,?,?,?)""",
            (identifier, sync_type, json.dumps(parameters, ensure_ascii=False, sort_keys=True, default=str),
             data_source.value, int(dry_run), SYNC_VERSION, now, now, now),
        )
    return identifier


def get_resumable_run(connection: sqlite3.Connection, run_id: str, sync_type: str, parameters: Mapping[str, object]) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM first_limit_sync_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    expected = json.dumps(parameters, ensure_ascii=False, sort_keys=True, default=str)
    if row["sync_type"] != sync_type or row["parameters_json"] != expected:
        raise ValueError("run parameters are incompatible with --resume")
    return row


def completed_item_keys(connection: sqlite3.Connection, run_id: str) -> set[str]:
    """Return terminal work items so a repeated run only retries unfinished work."""
    return {
        row[0]
        for row in connection.execute(
            "SELECT item_key FROM first_limit_sync_items WHERE run_id=? AND status IN ('success','empty','skipped')",
            (run_id,),
        )
    }


def record_item(connection: sqlite3.Connection, run_id: str, item_key: str, status: str, *,
                planned_start: date | str | None = None, planned_end: date | str | None = None,
                row_count: int = 0, retry_count: int = 0, error: str | None = None,
                result: Mapping[str, object] | None = None, commit: bool = True) -> None:
    now = _timestamp()
    def write() -> None:
        connection.execute(
            """INSERT INTO first_limit_sync_items(run_id,item_key,status,planned_start,planned_end,row_count,retry_count,last_error,result_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,item_key) DO UPDATE SET status=excluded.status,
               planned_start=COALESCE(excluded.planned_start,first_limit_sync_items.planned_start),
               planned_end=COALESCE(excluded.planned_end,first_limit_sync_items.planned_end),row_count=excluded.row_count,
               retry_count=excluded.retry_count,last_error=excluded.last_error,
               result_json=COALESCE(excluded.result_json,first_limit_sync_items.result_json),updated_at=excluded.updated_at""",
            (run_id, item_key, status, _date(planned_start) if planned_start else None, _date(planned_end) if planned_end else None,
             row_count, retry_count, (error or "")[:4000] or None,
             json.dumps(result, ensure_ascii=False, sort_keys=True, default=str) if result is not None else None, now),
        )
    if commit:
        with connection:
            write()
    else:
        write()


def finish_run(connection: sqlite3.Connection, run_id: str, *, status: str, planned_count: int,
               success_count: int = 0, empty_count: int = 0, skipped_count: int = 0, failure_count: int = 0,
               inserted_rows: int = 0, updated_rows: int = 0, unchanged_rows: int = 0, retry_count: int = 0,
               last_error: str | None = None) -> None:
    now = _timestamp()
    with connection:
        connection.execute(
            """UPDATE first_limit_sync_runs SET status=?,planned_count=?,success_count=?,empty_count=?,skipped_count=?,failure_count=?,
               inserted_rows=?,updated_rows=?,unchanged_rows=?,retry_count=?,last_error=?,finished_at=?,updated_at=? WHERE run_id=?""",
            (status, planned_count, success_count, empty_count, skipped_count, failure_count, inserted_rows,
             updated_rows, unchanged_rows, retry_count, (last_error or "")[:4000] or None, now, now, run_id),
        )
