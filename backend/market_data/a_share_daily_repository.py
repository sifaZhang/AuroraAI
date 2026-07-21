"""SQLite repository for normalized, unadjusted A-share daily bars."""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Mapping

MAX_ERROR_LENGTH = 4000
SQL_PARAMETER_BATCH_SIZE = 400
_CODE_PATTERNS = (
    re.compile(r"^(?:sh|sz)?(\d{6})$", re.IGNORECASE),
    re.compile(r"^(\d{6})\.(?:sh|sz)$", re.IGNORECASE),
)


@dataclass(frozen=True)
class DailyBar:
    stock_code: str | int
    trade_date: str | date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    source: str
    adjustment: str = "none"
    fetched_at: str | datetime = ""


@dataclass(frozen=True)
class UpsertResult:
    input_count: int
    normalized_count: int
    unique_count: int
    affected_count: int
    rejected_count: int
    min_trade_date: str | None
    max_trade_date: str | None


@dataclass(frozen=True)
class HistorySyncStatus:
    stock_code: str
    stock_name: str | None
    first_trade_date: str | None
    last_trade_date: str | None
    last_success_at: str | None
    last_attempt_at: str | None
    last_error: str | None
    consecutive_failures: int
    source: str | None
    adjustment: str
    row_count: int
    updated_at: str


def normalize_stock_code(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid stock code")
    if isinstance(value, int):
        if value < 0 or value > 999999:
            raise ValueError("invalid stock code")
        return f"{value:06d}"
    text = str(value).strip()
    if not text:
        raise ValueError("stock code is empty")
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    for pattern in _CODE_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return match.group(1)
    raise ValueError(f"invalid stock code: {value}")


def _iso_date(value: str | date) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid date: {value}") from exc


def _timestamp(value: str | datetime | None = None) -> str:
    if value in (None, ""):
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _number(value: object, field: str, *, non_negative: bool = False) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric or null") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if non_negative and result < 0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _normalize_bar(value: DailyBar | Mapping) -> DailyBar:
    raw = asdict(value) if isinstance(value, DailyBar) else dict(value)
    prices = {field: _number(raw.get(field), field) for field in ("open", "high", "low", "close")}
    high, low = prices["high"], prices["low"]
    if high is not None and low is not None and high < low:
        raise ValueError("high must not be below low")
    for field in ("open", "close"):
        price = prices[field]
        if high is not None and price is not None and high < price:
            raise ValueError(f"high must not be below {field}")
        if low is not None and price is not None and low > price:
            raise ValueError(f"low must not be above {field}")
    source = str(raw.get("source") or "").strip()
    adjustment = str(raw.get("adjustment") or "").strip()
    if not source or not adjustment:
        raise ValueError("source and adjustment are required")
    return DailyBar(
        stock_code=normalize_stock_code(raw.get("stock_code")),
        trade_date=_iso_date(raw.get("trade_date")),
        **prices,
        volume=_number(raw.get("volume"), "volume", non_negative=True),
        amount=_number(raw.get("amount"), "amount", non_negative=True),
        source=source, adjustment=adjustment, fetched_at=_timestamp(raw.get("fetched_at")),
    )


def upsert_daily_bars(connection: sqlite3.Connection, bars: Iterable[DailyBar | Mapping]) -> UpsertResult:
    values = list(bars)
    normalized: list[DailyBar] = []
    rejected = 0
    for value in values:
        try:
            normalized.append(_normalize_bar(value))
        except (TypeError, ValueError):
            rejected += 1
    unique = {(str(item.stock_code), str(item.trade_date), item.adjustment): item for item in normalized}
    records = list(unique.values())
    sql = """INSERT INTO a_share_daily_bars(
        stock_code,trade_date,open,high,low,close,volume,amount,source,adjustment,fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code,trade_date,adjustment) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,amount=excluded.amount,source=excluded.source,fetched_at=excluded.fetched_at"""
    parameters = [
        (item.stock_code, item.trade_date, item.open, item.high, item.low, item.close,
         item.volume, item.amount, item.source, item.adjustment, item.fetched_at)
        for item in records
    ]
    with connection:
        connection.executemany(sql, parameters)
    dates = [str(item.trade_date) for item in records]
    return UpsertResult(len(values), len(normalized), len(records), len(records), rejected,
                        min(dates) if dates else None, max(dates) if dates else None)


def _bar(row: sqlite3.Row) -> DailyBar:
    return DailyBar(**dict(row))


def get_recent_daily_bars(connection: sqlite3.Connection, stock_code: object, limit: int,
                          adjustment: str = "none") -> list[DailyBar]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = connection.execute(
        """SELECT * FROM (SELECT * FROM a_share_daily_bars
           WHERE stock_code=? AND adjustment=? ORDER BY trade_date DESC LIMIT ?)
           ORDER BY trade_date ASC""",
        (normalize_stock_code(stock_code), adjustment, limit),
    ).fetchall()
    return [_bar(row) for row in rows]


def get_daily_bars_between(connection: sqlite3.Connection, stock_code: object,
                           start_date: str | date, end_date: str | date,
                           adjustment: str = "none") -> list[DailyBar]:
    start, end = _iso_date(start_date), _iso_date(end_date)
    if start > end:
        raise ValueError("start_date must not exceed end_date")
    rows = connection.execute(
        """SELECT * FROM a_share_daily_bars WHERE stock_code=? AND adjustment=?
           AND trade_date BETWEEN ? AND ? ORDER BY trade_date ASC""",
        (normalize_stock_code(stock_code), adjustment, start, end),
    ).fetchall()
    return [_bar(row) for row in rows]


def get_recent_daily_bars_for_stocks(connection: sqlite3.Connection, stock_codes: Iterable[object],
                                     limit_per_stock: int, adjustment: str = "none") -> list[DailyBar]:
    if limit_per_stock <= 0:
        raise ValueError("limit_per_stock must be positive")
    if sqlite3.sqlite_version_info < (3, 25, 0):
        raise RuntimeError("SQLite 3.25+ is required for window functions")
    codes = sorted({normalize_stock_code(code) for code in stock_codes})
    results: list[DailyBar] = []
    for offset in range(0, len(codes), SQL_PARAMETER_BATCH_SIZE):
        batch = codes[offset:offset + SQL_PARAMETER_BATCH_SIZE]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""SELECT stock_code,trade_date,open,high,low,close,volume,amount,source,adjustment,fetched_at
                FROM (SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY stock_code ORDER BY trade_date DESC) AS row_number
                    FROM a_share_daily_bars WHERE adjustment=? AND stock_code IN ({placeholders}))
                WHERE row_number <= ? ORDER BY stock_code ASC, trade_date ASC""",
            (adjustment, *batch, limit_per_stock),
        ).fetchall()
        results.extend(_bar(row) for row in rows)
    return sorted(results, key=lambda item: (str(item.stock_code), str(item.trade_date)))


def get_daily_bar_stats(connection: sqlite3.Connection, stock_code: object,
                        adjustment: str = "none") -> tuple[str | None, str | None, int]:
    row = connection.execute(
        """SELECT MIN(trade_date),MAX(trade_date),COUNT(*) FROM a_share_daily_bars
           WHERE stock_code=? AND adjustment=?""",
        (normalize_stock_code(stock_code), adjustment),
    ).fetchone()
    return row[0], row[1], row[2]


def _status(row: sqlite3.Row | None) -> HistorySyncStatus | None:
    return HistorySyncStatus(**dict(row)) if row else None


def get_sync_status(connection: sqlite3.Connection, stock_code: object) -> HistorySyncStatus | None:
    return _status(connection.execute(
        "SELECT * FROM a_share_history_sync_status WHERE stock_code=?",
        (normalize_stock_code(stock_code),),
    ).fetchone())


def upsert_sync_success(connection: sqlite3.Connection, stock_code: object, stock_name: str | None,
                        source: str, adjustment: str, first_trade_date: str | date,
                        last_trade_date: str | date, row_count: int,
                        attempted_at: str | datetime, succeeded_at: str | datetime) -> None:
    if row_count < 0:
        raise ValueError("row_count must be non-negative")
    first, last = _iso_date(first_trade_date), _iso_date(last_trade_date)
    if first > last:
        raise ValueError("first_trade_date must not exceed last_trade_date")
    attempted, succeeded, updated = _timestamp(attempted_at), _timestamp(succeeded_at), _timestamp()
    with connection:
        connection.execute(
            """INSERT INTO a_share_history_sync_status(
               stock_code,stock_name,first_trade_date,last_trade_date,last_success_at,last_attempt_at,
               last_error,consecutive_failures,source,adjustment,row_count,updated_at)
               VALUES(?,?,?,?,?,?,NULL,0,?,?,?,?)
               ON CONFLICT(stock_code) DO UPDATE SET
               stock_name=COALESCE(excluded.stock_name,a_share_history_sync_status.stock_name),
               first_trade_date=excluded.first_trade_date,last_trade_date=excluded.last_trade_date,
               last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,
               last_error=NULL,consecutive_failures=0,source=excluded.source,
               adjustment=excluded.adjustment,row_count=excluded.row_count,updated_at=excluded.updated_at""",
            (normalize_stock_code(stock_code), stock_name, first, last, succeeded, attempted,
             source, adjustment, row_count, updated),
        )


def upsert_sync_failure(connection: sqlite3.Connection, stock_code: object, stock_name: str | None,
                        source: str, adjustment: str, error: object,
                        attempted_at: str | datetime) -> None:
    attempted, updated = _timestamp(attempted_at), _timestamp()
    message = str(error)[:MAX_ERROR_LENGTH]
    with connection:
        connection.execute(
            """INSERT INTO a_share_history_sync_status(
               stock_code,stock_name,last_attempt_at,last_error,consecutive_failures,
               source,adjustment,row_count,updated_at) VALUES(?,?,?,?,1,?,?,0,?)
               ON CONFLICT(stock_code) DO UPDATE SET
               stock_name=COALESCE(excluded.stock_name,a_share_history_sync_status.stock_name),
               last_attempt_at=excluded.last_attempt_at,last_error=excluded.last_error,
               consecutive_failures=a_share_history_sync_status.consecutive_failures+1,
               source=excluded.source,adjustment=excluded.adjustment,updated_at=excluded.updated_at""",
            (normalize_stock_code(stock_code), stock_name, attempted, message,
             source, adjustment, updated),
        )


def list_sync_statuses(connection: sqlite3.Connection, failed_only: bool = False,
                       limit: int | None = None) -> list[HistorySyncStatus]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    sql = "SELECT * FROM a_share_history_sync_status"
    parameters: list[object] = []
    if failed_only:
        sql += " WHERE consecutive_failures > 0"
    sql += " ORDER BY stock_code ASC"
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(limit)
    return [_status(row) for row in connection.execute(sql, parameters).fetchall()]  # type: ignore[misc]
