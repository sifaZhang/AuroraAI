"""SQLite repository for sector history and current membership snapshots."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Mapping

CLASSIFICATION_SYSTEM = "sw_level1"
MEMBERSHIP_SCOPE = "current_snapshot"
LOOKAHEAD_WARNING = (
    "Current constituent snapshot is not historical membership; using it for historical "
    "scores is approximate and may introduce future-data leakage."
)


@dataclass(frozen=True)
class Sector:
    sector_code: str
    sector_name: str
    sector_level: int = 1
    classification_system: str = CLASSIFICATION_SYSTEM


@dataclass(frozen=True)
class SectorDailyBar:
    sector_code: str
    trade_date: str | date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    source: str = "akshare_sw"
    fetched_at: str | datetime = ""
    classification_system: str = CLASSIFICATION_SYSTEM


@dataclass(frozen=True)
class SectorMember:
    sector_code: str
    stock_code: str
    stock_name: str | None
    weight: float | None
    snapshot_date: str | date
    source: str = "akshare_sw"
    classification_system: str = CLASSIFICATION_SYSTEM


def _timestamp(value: str | datetime | None = None) -> str:
    if value in (None, ""):
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _date(value: str | date) -> str:
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value).strip()).isoformat()


def _number(value: object, field: str, *, non_negative: bool = False) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or (non_negative and result < 0):
        raise ValueError(f"invalid {field}: {value}")
    return result


def _system(value: object) -> str:
    result = str(value or "").strip()
    if result != CLASSIFICATION_SYSTEM:
        raise ValueError(f"unsupported classification_system: {result}")
    return result


def upsert_sectors(connection: sqlite3.Connection, sectors: Iterable[Sector], seen_at=None) -> int:
    rows = list(sectors)
    now = _timestamp(seen_at)
    values = []
    for row in rows:
        system = _system(row.classification_system)
        code, name = str(row.sector_code).strip(), str(row.sector_name).strip()
        if not code or not name or row.sector_level != 1:
            raise ValueError("valid level-1 sector code and name are required")
        values.append((system, code, name, row.sector_level, now, now, now))
    with connection:
        connection.executemany(
            """INSERT INTO sector_industries(
                   classification_system,sector_code,sector_name,sector_level,
                   first_seen_at,last_seen_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(classification_system,sector_code) DO UPDATE SET
                   sector_name=excluded.sector_name,sector_level=excluded.sector_level,
                   is_active=1,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            values,
        )
    return len(values)


def upsert_sector_bars(connection: sqlite3.Connection, bars: Iterable[SectorDailyBar | Mapping]) -> int:
    normalized = {}
    for value in bars:
        raw = asdict(value) if isinstance(value, SectorDailyBar) else dict(value)
        system = _system(raw.get("classification_system", CLASSIFICATION_SYSTEM))
        code = str(raw.get("sector_code") or "").strip()
        if not code:
            raise ValueError("sector_code is required")
        prices = {field: _number(raw.get(field), field) for field in ("open", "high", "low", "close")}
        volume = _number(raw.get("volume"), "volume", non_negative=True)
        amount = _number(raw.get("amount"), "amount", non_negative=True)
        item = (system, code, _date(raw["trade_date"]), prices["open"], prices["high"],
                prices["low"], prices["close"], volume, amount,
                str(raw.get("source") or "").strip(), _timestamp(raw.get("fetched_at")))
        if not item[-2]:
            raise ValueError("source is required")
        normalized[(system, code, item[2])] = item
    values = list(normalized.values())
    with connection:
        connection.executemany(
            """INSERT INTO sector_daily_bars(
                   classification_system,sector_code,trade_date,open,high,low,close,
                   volume,amount,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(classification_system,sector_code,trade_date) DO UPDATE SET
                   open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                   volume=excluded.volume,amount=excluded.amount,source=excluded.source,
                   fetched_at=excluded.fetched_at""",
            values,
        )
    return len(values)


def replace_current_membership(connection: sqlite3.Connection, sector_code: str,
                               members: Iterable[SectorMember], snapshot_date: str | date,
                               seen_at=None, classification_system: str = CLASSIFICATION_SYSTEM) -> int:
    system, code, snapshot, now = _system(classification_system), str(sector_code).strip(), _date(snapshot_date), _timestamp(seen_at)
    rows = list(members)
    values = []
    for row in rows:
        if _system(row.classification_system) != system or str(row.sector_code).strip() != code:
            raise ValueError("membership sector does not match target sector")
        stock_code = str(row.stock_code).strip().zfill(6)
        if len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError(f"invalid stock code: {row.stock_code}")
        values.append((system, code, stock_code, row.stock_name,
                       _number(row.weight, "weight", non_negative=True), snapshot,
                       MEMBERSHIP_SCOPE, now, now, LOOKAHEAD_WARNING, row.source, now))
    with connection:
        connection.execute(
            "UPDATE sector_memberships SET is_current=0,updated_at=? WHERE classification_system=? AND sector_code=?",
            (now, system, code),
        )
        connection.executemany(
            """INSERT INTO sector_memberships(
                   classification_system,sector_code,stock_code,stock_name,weight,snapshot_date,
                   membership_scope,first_seen_at,last_seen_at,lookahead_bias_warning,source,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(classification_system,sector_code,stock_code) DO UPDATE SET
                   stock_name=COALESCE(excluded.stock_name,sector_memberships.stock_name),
                   weight=COALESCE(excluded.weight,sector_memberships.weight),
                   snapshot_date=excluded.snapshot_date,membership_scope=excluded.membership_scope,
                   is_current=1,last_seen_at=excluded.last_seen_at,
                   historical_use_is_approximate=1,
                   lookahead_bias_warning=excluded.lookahead_bias_warning,
                   source=excluded.source,updated_at=excluded.updated_at""",
            values,
        )
    return len(values)


def sector_bar_stats(connection: sqlite3.Connection, sector_code: str,
                     classification_system: str = CLASSIFICATION_SYSTEM):
    return tuple(connection.execute(
        """SELECT MIN(trade_date),MAX(trade_date),COUNT(*) FROM sector_daily_bars
           WHERE classification_system=? AND sector_code=?""",
        (_system(classification_system), str(sector_code).strip()),
    ).fetchone())


def list_failed_sector_codes(connection: sqlite3.Connection,
                             classification_system: str = CLASSIFICATION_SYSTEM) -> list[str]:
    rows = connection.execute(
        """SELECT sector_code FROM sector_history_sync_status
           WHERE classification_system=? AND status='failed' ORDER BY sector_code""",
        (_system(classification_system),),
    ).fetchall()
    return [row[0] for row in rows]


def record_sync_success(connection: sqlite3.Connection, sector: Sector, snapshot_date: str | date,
                        bar_count: int, member_count: int, attempted_at=None, succeeded_at=None) -> None:
    attempted, succeeded = _timestamp(attempted_at), _timestamp(succeeded_at)
    first, last, _ = sector_bar_stats(connection, sector.sector_code, sector.classification_system)
    with connection:
        connection.execute(
            """INSERT INTO sector_history_sync_status(
                   classification_system,sector_code,sector_name,status,first_trade_date,last_trade_date,
                   last_snapshot_date,last_success_at,last_attempt_at,last_error,consecutive_failures,
                   bar_count,member_count,updated_at) VALUES(?,?,?,'success',?,?,?,?,?,NULL,0,?,?,?)
               ON CONFLICT(classification_system,sector_code) DO UPDATE SET
                   sector_name=excluded.sector_name,status='success',first_trade_date=excluded.first_trade_date,
                   last_trade_date=excluded.last_trade_date,last_snapshot_date=excluded.last_snapshot_date,
                   last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,
                   last_error=NULL,consecutive_failures=0,bar_count=excluded.bar_count,
                   member_count=excluded.member_count,updated_at=excluded.updated_at""",
            (sector.classification_system, sector.sector_code, sector.sector_name, first, last,
             _date(snapshot_date), succeeded, attempted, bar_count, member_count, succeeded),
        )


def record_sync_failure(connection: sqlite3.Connection, sector: Sector, error: str, attempted_at=None) -> None:
    attempted = _timestamp(attempted_at)
    with connection:
        connection.execute(
            """INSERT INTO sector_history_sync_status(
                   classification_system,sector_code,sector_name,status,last_attempt_at,last_error,
                   consecutive_failures,updated_at) VALUES(?,?,?,'failed',?,?,1,?)
               ON CONFLICT(classification_system,sector_code) DO UPDATE SET
                   sector_name=excluded.sector_name,status='failed',last_attempt_at=excluded.last_attempt_at,
                   last_error=excluded.last_error,consecutive_failures=sector_history_sync_status.consecutive_failures+1,
                   updated_at=excluded.updated_at""",
            (sector.classification_system, sector.sector_code, sector.sector_name,
             attempted, str(error)[:4000], attempted),
        )
