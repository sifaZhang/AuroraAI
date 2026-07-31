"""SQLite persistence for PR6.1 metadata contracts, kept independent of daily bars."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from .contracts import BoardType, DataSource, QualityFlag, SecurityId, SecurityStatus
from .rules import normalize_symbol


def _date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value).strip()).isoformat()


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


def _flags(flags: Iterable[QualityFlag]) -> str:
    return json.dumps(sorted(item.value if isinstance(item, QualityFlag) else str(item) for item in flags), ensure_ascii=False)


@dataclass(frozen=True)
class SecurityMaster:
    symbol: SecurityId
    board_type: BoardType
    source: DataSource
    security_name: str | None = None
    listed_date: date | None = None
    delisted_date: date | None = None
    is_active: bool = True
    quality_flags: frozenset[QualityFlag] = frozenset()


@dataclass(frozen=True)
class CalendarDay:
    market: str
    trade_date: date
    is_open: bool
    source: DataSource
    quality_flags: frozenset[QualityFlag] = frozenset()


def upsert_security_master(connection: sqlite3.Connection, item: SecurityMaster, updated_at=None) -> None:
    now = _timestamp(updated_at)
    with connection:
        connection.execute(
            """INSERT INTO a_share_security_master(symbol,stock_code,exchange,board_type,security_name,listed_date,delisted_date,source,quality_flags,updated_at,is_active)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
               board_type=excluded.board_type,security_name=COALESCE(excluded.security_name,a_share_security_master.security_name),
               listed_date=COALESCE(excluded.listed_date,a_share_security_master.listed_date),delisted_date=COALESCE(excluded.delisted_date,a_share_security_master.delisted_date),
               source=excluded.source,quality_flags=excluded.quality_flags,updated_at=excluded.updated_at,is_active=excluded.is_active""",
            (item.symbol.canonical, item.symbol.code, item.symbol.exchange, item.board_type.value, item.security_name,
             _date(item.listed_date) if item.listed_date else None, _date(item.delisted_date) if item.delisted_date else None,
             item.source.value, _flags(item.quality_flags), now, int(item.is_active)),
        )


def upsert_security_status(connection: sqlite3.Connection, item: SecurityStatus, updated_at=None) -> None:
    upsert_security_statuses(connection, [item], updated_at=updated_at)


def upsert_security_statuses(connection: sqlite3.Connection, items: Iterable[SecurityStatus], updated_at=None) -> int:
    now = _timestamp(updated_at)
    records = list(items)
    values = [
        (
            item.symbol.canonical, _date(item.effective_date),
            item.board_type.value,
            None if item.is_st is None else int(item.is_st),
            None if item.is_suspended is None else int(item.is_suspended),
            None if item.no_price_limit is None else int(item.no_price_limit),
            _date(item.listed_date) if item.listed_date else None,
            _date(item.delisted_date) if item.delisted_date else None,
            item.source.value, _flags(item.quality_flags), now,
        )
        for item in records
    ]
    with connection:
        connection.executemany(
            """INSERT INTO a_share_security_status_history(symbol,effective_date,board_type,is_st,is_suspended,no_price_limit,listed_date,delisted_date,source,quality_flags,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,effective_date) DO UPDATE SET
               board_type=excluded.board_type,is_st=excluded.is_st,is_suspended=excluded.is_suspended,no_price_limit=excluded.no_price_limit,
               listed_date=COALESCE(excluded.listed_date,a_share_security_status_history.listed_date),
               delisted_date=COALESCE(excluded.delisted_date,a_share_security_status_history.delisted_date),source=excluded.source,
               quality_flags=excluded.quality_flags,updated_at=excluded.updated_at""",
            values,
        )
    return len(records)


def get_security_status_as_of(connection: sqlite3.Connection, symbol: SecurityId | object, day: date | str) -> SecurityStatus | None:
    security = symbol if isinstance(symbol, SecurityId) else normalize_symbol(symbol)
    row = connection.execute(
        """SELECT * FROM a_share_security_status_history WHERE symbol=? AND effective_date<=?
           ORDER BY effective_date DESC LIMIT 1""", (security.canonical, _date(day)),
    ).fetchone()
    if not row:
        return None
    flags = frozenset(QualityFlag(flag) for flag in json.loads(row["quality_flags"]))
    return SecurityStatus(security, date.fromisoformat(row["effective_date"]), BoardType(row["board_type"]),
                          None if row["is_st"] is None else bool(row["is_st"]),
                          None if row["is_suspended"] is None else bool(row["is_suspended"]),
                          None if row["no_price_limit"] is None else bool(row["no_price_limit"]),
                          date.fromisoformat(row["listed_date"]) if row["listed_date"] else None,
                          date.fromisoformat(row["delisted_date"]) if row["delisted_date"] else None,
                          DataSource(row["source"]), flags)


def upsert_calendar_days(connection: sqlite3.Connection, days: Iterable[CalendarDay], updated_at=None) -> int:
    now, records = _timestamp(updated_at), list(days)
    values = [(item.market.upper(), _date(item.trade_date), int(item.is_open), item.source.value, _flags(item.quality_flags), now) for item in records]
    for market, *_ in values:
        if market not in {"CN", "SH", "SZ", "BJ"}:
            raise ValueError(f"unsupported market: {market}")
    with connection:
        connection.executemany(
            """INSERT INTO a_share_trading_calendar(market,trade_date,is_open,source,quality_flags,updated_at) VALUES(?,?,?,?,?,?)
               ON CONFLICT(market,trade_date) DO UPDATE SET is_open=excluded.is_open,source=excluded.source,
               quality_flags=excluded.quality_flags,updated_at=excluded.updated_at""", values,
        )
    return len(values)
