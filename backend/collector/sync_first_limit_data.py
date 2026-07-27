"""Controlled GM-backed input synchronization for the first-limit strategy.

This module intentionally has no strategy detection or trading calls.  Network workers
only return normalized values; all SQLite mutations happen in the caller thread.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import DailyBar, upsert_daily_bars
from backend.market_data.sector_history_repository import list_current_member_stocks
from backend.strategy.first_limit.contracts import BoardType, DataSource, QualityFlag, SecurityStatus
from backend.strategy.first_limit.repository import CalendarDay, SecurityMaster, upsert_calendar_days, upsert_security_master, upsert_security_status
from backend.strategy.first_limit.rules import (detect_price_anomalies, normalize_symbol,
    resolve_board_type, resolve_limit_prices, resolve_price_limit_rule)
from backend.strategy.first_limit.sync_repository import (DailyMetadata, MinuteBar, completed_item_keys,
    create_run, finish_run, get_resumable_run, record_item, upsert_daily_metadata, upsert_minute_bars)

DEFAULT_WORKERS = 2
MAX_WORKERS = 8
STATUS_BATCH_SIZE = 25
MAX_MINUTE_CODES = 5
MAX_MINUTE_DAYS = 5
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    sync_type: str
    planned: int
    success: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0
    rows: int = 0
    retries: int = 0
    failures: tuple[tuple[str, str], ...] = ()

    @property
    def status(self) -> str:
        return "failed" if self.failed == self.planned and self.planned else "partial" if self.failed else "success"


def _parse_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _iso_timestamp(value: Any) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result.astimezone(SHANGHAI)


def _records(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    raise ValueError(f"unsupported provider response: {type(value).__name__}")


def _field(record: Mapping[str, Any], *names: str) -> Any:
    aliases = {str(key).lower(): value for key, value in record.items()}
    for name in names:
        if name.lower() in aliases:
            return aliases[name.lower()]
    return None


def _workers(value: int | None) -> int:
    result = DEFAULT_WORKERS if value is None else int(value)
    if not 1 <= result <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    return result


def _retry(call: Callable[[], Any], *, attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> tuple[Any, int]:
    errors = []
    for attempt in range(attempts):
        try:
            return call(), attempt
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                sleep(float(2 ** attempt))
    raise RuntimeError(" | ".join(errors)[:4000])


def _pool(connection, codes: Iterable[str] | None, max_symbols: int | None) -> list:
    if codes:
        result = [normalize_symbol(value) for value in codes]
    else:
        result = []
        for row in list_current_member_stocks(connection):
            code = str(row["stock_code"])
            exchange = "SH" if code.startswith(("5", "6", "9")) else "SZ" if code.startswith(("0", "1", "2", "3")) else "BJ" if code.startswith(("4", "8")) else None
            if exchange:
                result.append(normalize_symbol(code, exchange=exchange))
    result = sorted({item.canonical: item for item in result}.values(), key=lambda item: item.canonical)
    if max_symbols is not None:
        if max_symbols <= 0:
            raise ValueError("max-symbols must be positive")
        result = result[:max_symbols]
    if not result:
        raise ValueError("no supported A-share symbols selected")
    return result


def _board(raw: Any, symbol, day: date) -> tuple[BoardType, set[QualityFlag]]:
    text = str(raw or "").upper()
    mapping = {
        "MAIN": BoardType.MAIN, "MAIN_BOARD": BoardType.MAIN, "CHINEXT": BoardType.CHINEXT,
        "GEM": BoardType.CHINEXT, "STAR": BoardType.STAR, "KCB": BoardType.STAR,
        "BSE": BoardType.BSE, "BEIJING": BoardType.BSE,
    }
    if text in mapping:
        return mapping[text], set()
    resolved = resolve_board_type(symbol, day)
    return resolved, {QualityFlag.MISSING_SECURITY_STATUS} if raw in (None, "") else {QualityFlag.UNSUPPORTED_SECURITY}


def _is_target_stock(record: Mapping[str, Any]) -> bool:
    raw = _field(record, "sec_type", "security_type", "instrument_type")
    # GM SDK 3.0.185 serializes its documented stock security type as integer 1.
    # Keep unknown numeric/enumeration values conservative: only the explicit stock value passes.
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw == 1
    sec_type = str(raw or "").strip().lower()
    return sec_type in {"stock", "a_stock", "common_stock", "股票"}


def sync_calendar(connection, api: Any, start_date: date, end_date: date, *, dry_run=False, run_id: str | None = None) -> SyncResult:
    if start_date > end_date:
        raise ValueError("start-date must not be later than end-date")
    parameters = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "market": "CN"}
    if dry_run:
        return SyncResult(run_id or "dry-run", "calendar", (end_date - start_date).days + 1,
                          skipped=(end_date - start_date).days + 1)
    completed: set[str] = set()
    if run_id:
        get_resumable_run(connection, run_id, "calendar", parameters)
        completed = completed_item_keys(connection, run_id)
    else:
        run_id = create_run(connection, "calendar", parameters, dry_run=dry_run)
    dates, retries = _retry(lambda: api.get_trading_dates("SHSE", start_date.isoformat(), end_date.isoformat()))
    open_days = {_parse_date(value) for value in dates}
    if not open_days and start_date != end_date:
        raise RuntimeError("provider returned an empty calendar for a multi-day range")
    if any(day < start_date or day > end_date for day in open_days):
        raise ValueError("provider calendar contains out-of-range dates")
    all_days = [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]
    pending_days = [day for day in all_days if day.isoformat() not in completed]
    upsert_calendar_days(connection, [CalendarDay("CN", day, day in open_days, DataSource.GM) for day in pending_days])
    for day in pending_days:
        record_item(connection, run_id, day.isoformat(), "success", planned_start=day, planned_end=day, row_count=1)
    result = SyncResult(run_id, "calendar", len(all_days), success=len(pending_days),
                        skipped=len(all_days) - len(pending_days), rows=len(pending_days), retries=retries)
    finish_run(connection, run_id, status=result.status, planned_count=result.planned, success_count=result.success,
               skipped_count=result.skipped, inserted_rows=result.rows, retry_count=retries)
    return result


def _instrument_to_master(record: Mapping[str, Any], requested) -> SecurityMaster | None:
    raw_symbol = _field(record, "symbol") or requested.gm_symbol
    try:
        symbol = normalize_symbol(raw_symbol)
    except ValueError:
        return None
    if not _is_target_stock(record):
        return None
    flags: set[QualityFlag] = set()
    board, board_flags = _board(_field(record, "board"), symbol, date.today())
    flags.update(board_flags)
    listed = _field(record, "listed_date")
    delisted = _field(record, "delisted_date")
    try:
        listed_day = _parse_date(listed) if listed else None
        delisted_day = _parse_date(delisted) if delisted else None
    except (TypeError, ValueError):
        return None
    # GM returns a future terminal date (currently 2038-01-01) for active instruments.
    # Preserve it for historical as-of checks; only a date reached by today is inactive.
    active_day = date.today()
    is_active = (listed_day is None or listed_day <= active_day) and (delisted_day is None or active_day < delisted_day)
    return SecurityMaster(symbol, board, DataSource.GM, security_name=_field(record, "sec_name", "security_name", "name"),
                          listed_date=listed_day, delisted_date=delisted_day,
                          is_active=is_active, quality_flags=frozenset(flags))


def sync_securities(connection, api: Any, symbols: Iterable, *, workers=DEFAULT_WORKERS, dry_run=False, run_id: str | None = None) -> SyncResult:
    items = list(symbols)
    parameters = {"symbols": [item.canonical for item in items]}
    if dry_run:
        return SyncResult(run_id or "dry-run", "securities", len(items), skipped=len(items))
    completed: set[str] = set()
    if run_id:
        get_resumable_run(connection, run_id, "securities", parameters)
        completed = completed_item_keys(connection, run_id)
    else:
        run_id = create_run(connection, "securities", parameters, dry_run=dry_run)
    results, failures, retries = [], [], 0
    def fetch(item):
        response, retry_count = _retry(lambda: api.get_instruments(symbols=item.gm_symbol, skip_suspended=False, skip_st=False, df=False))
        return item, _records(response), retry_count
    with ThreadPoolExecutor(max_workers=_workers(workers), thread_name_prefix="first-limit-security") as executor:
        futures = {executor.submit(fetch, item): item for item in items if item.canonical not in completed}
        for future in as_completed(futures):
            try:
                item, records, retry_count = future.result(); retries += retry_count
                master = next((value for value in (_instrument_to_master(record, item) for record in records) if value), None)
                if master is None:
                    record_item(connection, run_id, item.canonical, "skipped", error="not a verified stock")
                else:
                    upsert_security_master(connection, master); record_item(connection, run_id, item.canonical, "success", row_count=1); results.append(master)
            except Exception as exc:
                key = futures[future].canonical
                failures.append((key, f"{type(exc).__name__}: {exc}"))
    success = len(results); skipped = len(items) - success - len(failures)
    for key, error in failures: record_item(connection, run_id, key, "failed", error=error)
    result = SyncResult(run_id, "securities", len(items), success, skipped=skipped, failed=len(failures), rows=success, retries=retries, failures=tuple(failures))
    finish_run(connection, run_id, status=result.status, planned_count=result.planned, success_count=result.success,
               skipped_count=result.skipped, failure_count=result.failed, inserted_rows=result.rows, retry_count=retries,
               last_error=failures[-1][1] if failures else None)
    return result


def _status_to_values(record: Mapping[str, Any], requested) -> tuple[SecurityStatus, DailyMetadata] | None:
    raw_symbol = _field(record, "symbol") or requested.gm_symbol
    try:
        symbol = normalize_symbol(raw_symbol)
    except ValueError:
        return None
    day_value = _field(record, "trade_date", "date")
    if not day_value:
        return None
    day = _parse_date(day_value)
    board, flags = _board(_field(record, "board"), symbol, day)
    # GM PR6.0 did not prove a historical ST field. Preserve None rather than infer it.
    suspended = _field(record, "is_suspended")
    no_limit = _field(record, "no_price_limit")
    status = SecurityStatus(symbol, day, board, None, None if suspended is None else bool(suspended),
                            None if no_limit is None else bool(no_limit),
                            _parse_date(_field(record, "listed_date")) if _field(record, "listed_date") else None,
                            _parse_date(_field(record, "delisted_date")) if _field(record, "delisted_date") else None,
                            DataSource.GM, frozenset(flags))
    meta = DailyMetadata(symbol, day, _field(record, "pre_close"), _field(record, "upper_limit"),
                         _field(record, "lower_limit"), DataSource.GM, frozenset(flags))
    return status, meta


def sync_statuses(connection, api: Any, symbols: Iterable, start_date: date, end_date: date, *, dry_run=False, run_id: str | None = None) -> SyncResult:
    if start_date > end_date: raise ValueError("start-date must not be later than end-date")
    items = list(symbols); parameters = {"symbols": [item.canonical for item in items], "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
    if dry_run:
        return SyncResult(run_id or "dry-run", "statuses", len(items), skipped=len(items))
    completed: set[str] = set()
    if run_id:
        get_resumable_run(connection, run_id, "statuses", parameters)
        completed = completed_item_keys(connection, run_id)
    else: run_id = create_run(connection, "statuses", parameters, dry_run=dry_run)
    success = empty = skipped = failed = rows = retries = 0; failures = []
    for offset in range(0, len(items), STATUS_BATCH_SIZE):
        batch = items[offset:offset + STATUS_BATCH_SIZE]; key = ",".join(item.canonical for item in batch)
        if key in completed:
            skipped += len(batch)
            continue
        try:
            response, retry_count = _retry(lambda: api.get_history_instruments(symbols=[item.gm_symbol for item in batch], start_date=start_date.isoformat(), end_date=end_date.isoformat(), df=False)); retries += retry_count
            values = [value for value in (_status_to_values(record, batch[0]) for record in _records(response)) if value]
            if not values:
                empty += len(batch); record_item(connection, run_id, key, "empty", planned_start=start_date, planned_end=end_date); continue
            enriched = []
            for status, meta in values:
                previous = connection.execute(
                    "SELECT close FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date<? ORDER BY trade_date DESC LIMIT 1",
                    (status.symbol.code, status.effective_date.isoformat()),
                ).fetchone()
                flags = set(meta.quality_flags)
                flags.update(detect_price_anomalies(adjustment="none", pre_close=meta.pre_close,
                                                    previous_close=previous[0] if previous else None))
                rule = resolve_price_limit_rule(status.symbol, status.effective_date, status)
                limits = resolve_limit_prices(meta.pre_close, rule, source_upper_limit=meta.source_upper_limit,
                                              source_lower_limit=meta.source_lower_limit)
                flags.update(limits.quality_flags)
                enriched.append((status, replace(meta, quality_flags=frozenset(flags))))
            for status, meta in enriched: upsert_security_status(connection, status)
            upsert_daily_metadata(connection, [meta for _, meta in enriched]); rows += len(enriched); success += len(batch)
            record_item(connection, run_id, key, "success", planned_start=start_date, planned_end=end_date, row_count=len(values))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:4000]; failed += len(batch); failures.append((key, message)); record_item(connection, run_id, key, "failed", planned_start=start_date, planned_end=end_date, error=message)
    result = SyncResult(run_id, "statuses", len(items), success, empty, failed=failed, rows=rows, retries=retries, failures=tuple(failures))
    finish_run(connection, run_id, status=result.status, planned_count=result.planned, success_count=success, empty_count=empty, failure_count=failed, inserted_rows=rows, retry_count=retries, last_error=failures[-1][1] if failures else None)
    return result


def _open_days(connection, start_date: date, end_date: date) -> list[date]:
    rows = connection.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ? ORDER BY trade_date", (start_date.isoformat(), end_date.isoformat())).fetchall()
    if not rows: raise LookupError("calendar coverage is required before daily gap planning")
    return [date.fromisoformat(row[0]) for row in rows]


def plan_daily_gaps(connection, symbols: Iterable, start_date: date, end_date: date) -> dict:
    open_days = _open_days(connection, start_date, end_date); planned = {}
    for symbol in symbols:
        existing = {date.fromisoformat(row[0]) for row in connection.execute("SELECT trade_date FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date BETWEEN ? AND ?", (symbol.code, start_date.isoformat(), end_date.isoformat()))}
        missing = [day for day in open_days if day not in existing]
        intervals = []
        for day in missing:
            if not intervals or day != intervals[-1][1] + timedelta(days=1): intervals.append([day, day])
            else: intervals[-1][1] = day
        planned[symbol] = tuple((pair[0], pair[1]) for pair in intervals)
    return planned


def _daily_bars_from_frame(symbol, frame: Any) -> list[DailyBar]:
    records = _records(frame); values = []
    for record in records:
        day_value = _field(record, "trade_date", "date", "eob", "bob")
        if not day_value: continue
        day = _parse_date(day_value)
        try:
            values.append(DailyBar(symbol.code, day, float(_field(record, "open")), float(_field(record, "high")), float(_field(record, "low")), float(_field(record, "close")), float(_field(record, "volume") or 0), float(_field(record, "amount")) if _field(record, "amount") is not None else None, "gm_api_history", "none", datetime.now(timezone.utc)))
        except (TypeError, ValueError):
            continue
    return values


def sync_daily(connection, api: Any, plans: Mapping, *, workers=DEFAULT_WORKERS, dry_run=False, run_id: str | None = None) -> SyncResult:
    entries = [(symbol, start, end) for symbol, intervals in plans.items() for start, end in intervals]
    parameters = {"intervals": [(symbol.canonical, start.isoformat(), end.isoformat()) for symbol, start, end in entries]}
    if dry_run:
        return SyncResult(run_id or "dry-run", "daily", len(entries), skipped=len(entries))
    completed: set[str] = set()
    if run_id:
        get_resumable_run(connection, run_id, "daily", parameters)
        completed = completed_item_keys(connection, run_id)
    else: run_id = create_run(connection, "daily", parameters, dry_run=dry_run)
    def fetch(entry):
        symbol, start, end = entry
        response, retry_count = _retry(lambda: api.history(symbol=symbol.gm_symbol, frequency="1d", start_time=f"{start.isoformat()} 09:30:00", end_time=f"{end.isoformat()} 15:00:00", fields="open,high,low,close,volume,amount,pre_close", adjust=0, df=True))
        return entry, _daily_bars_from_frame(symbol, response), retry_count
    success = empty = skipped = failed = rows = retries = 0; failures = []
    with ThreadPoolExecutor(max_workers=_workers(workers), thread_name_prefix="first-limit-daily") as executor:
        futures = [executor.submit(fetch, entry) for entry in entries
                   if f"{entry[0].canonical}:{entry[1]}:{entry[2]}" not in completed]
        for future in as_completed(futures):
            entry = None
            try:
                entry, bars, retry_count = future.result(); retries += retry_count; symbol, start, end = entry; key = f"{symbol.canonical}:{start}:{end}"
                if not bars:
                    empty += 1; record_item(connection, run_id, key, "empty", planned_start=start, planned_end=end); continue
                existing_dates = {row[0] for row in connection.execute(
                    "SELECT trade_date FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date BETWEEN ? AND ?",
                    (symbol.code, start.isoformat(), end.isoformat()),
                )}
                missing_only = [bar for bar in bars if start <= _parse_date(bar.trade_date) <= end
                                and str(bar.trade_date) not in existing_dates]
                if not missing_only:
                    skipped += 1; record_item(connection, run_id, key, "skipped", planned_start=start, planned_end=end); continue
                write = upsert_daily_bars(connection, missing_only); success += 1; rows += write.affected_count
                record_item(connection, run_id, key, "success", planned_start=start, planned_end=end, row_count=write.affected_count)
            except Exception as exc:
                key = "unknown" if entry is None else f"{entry[0].canonical}:{entry[1]}:{entry[2]}"; message = f"{type(exc).__name__}: {exc}"[:4000]
                failed += 1; failures.append((key, message)); record_item(connection, run_id, key, "failed", error=message)
    result = SyncResult(run_id, "daily", len(entries), success=success, empty=empty, skipped=skipped, failed=failed,
                        rows=rows, retries=retries, failures=tuple(failures))
    finish_run(connection, run_id, status=result.status, planned_count=result.planned, success_count=success, empty_count=empty, skipped_count=skipped, failure_count=failed, inserted_rows=rows, retry_count=retries, last_error=failures[-1][1] if failures else None)
    return result


def _minute_from_frame(symbol, frame: Any) -> list[MinuteBar]:
    values = []
    for record in _records(frame):
        moment = _field(record, "eob", "bob", "datetime", "bar_time", "date")
        if not moment: continue
        try:
            stamp = _iso_timestamp(moment)
            if not (stamp.hour == 9 and stamp.minute >= 30 or 10 <= stamp.hour <= 14 or stamp.hour == 15 and stamp.minute == 0): continue
            values.append(MinuteBar(symbol, stamp, float(_field(record, "open")), float(_field(record, "high")), float(_field(record, "low")), float(_field(record, "close")), float(_field(record, "volume") or 0), float(_field(record, "amount")) if _field(record, "amount") is not None else None))
        except (TypeError, ValueError): continue
    return values


def sync_minutes(connection, api: Any, symbols: Iterable, start_date: date, end_date: date, *, dry_run=False, allow_large_run=False, run_id: str | None = None) -> SyncResult:
    items = list(symbols); day_count = (end_date - start_date).days + 1
    if not items: raise ValueError("minute sync requires --codes")
    if start_date > end_date: raise ValueError("start-date must not be later than end-date")
    if not allow_large_run and (len(items) > MAX_MINUTE_CODES or day_count > MAX_MINUTE_DAYS):
        raise ValueError("minute request exceeds safety threshold; pass --allow-large-run explicitly")
    parameters = {"symbols": [item.canonical for item in items], "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "timeframe": "1m"}
    if dry_run:
        return SyncResult(run_id or "dry-run", "minute", len(items), skipped=len(items))
    completed: set[str] = set()
    if run_id:
        get_resumable_run(connection, run_id, "minute", parameters)
        completed = completed_item_keys(connection, run_id)
    else: run_id = create_run(connection, "minute", parameters, dry_run=dry_run)
    success = empty = skipped = failed = rows = retries = 0; failures = []
    for symbol in items:
        key = f"{symbol.canonical}:{start_date}:{end_date}"
        if key in completed:
            skipped += 1
            continue
        try:
            frame, retry_count = _retry(lambda: api.history(symbol=symbol.gm_symbol, frequency="60s", start_time=f"{start_date.isoformat()} 09:30:00", end_time=f"{end_date.isoformat()} 15:00:00", fields="open,high,low,close,volume,amount", adjust=0, df=True)); retries += retry_count
            bars = _minute_from_frame(symbol, frame)
            if not bars: empty += 1; record_item(connection, run_id, key, "empty", planned_start=start_date, planned_end=end_date); continue
            rows += upsert_minute_bars(connection, bars); success += 1; record_item(connection, run_id, key, "success", planned_start=start_date, planned_end=end_date, row_count=len(bars))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:4000]; failed += 1; failures.append((key, message)); record_item(connection, run_id, key, "failed", planned_start=start_date, planned_end=end_date, error=message)
    result = SyncResult(run_id, "minute", len(items), success=success, empty=empty, skipped=skipped, failed=failed,
                        rows=rows, retries=retries, failures=tuple(failures))
    finish_run(connection, run_id, status=result.status, planned_count=result.planned, success_count=success,
               empty_count=empty, skipped_count=skipped, failure_count=failed, inserted_rows=rows,
               retry_count=retries, last_error=failures[-1][1] if failures else None)
    return result


def audit(connection, run_id: str | None = None) -> dict[str, Any]:
    quality = {}
    for table, field in (("a_share_security_master", "quality_flags"), ("a_share_security_status_history", "quality_flags"), ("first_limit_daily_metadata", "quality_flags"), ("first_limit_minute_bars", "quality_flags")):
        for row in connection.execute(f"SELECT {field} FROM {table}"):
            for flag in json.loads(row[0] or "[]"): quality[flag] = quality.get(flag, 0) + 1
    calendar = connection.execute(
        "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM a_share_trading_calendar WHERE market='CN'"
    ).fetchone()
    return {"run_id": run_id, "calendar": {"first_date": calendar[0], "last_date": calendar[1], "row_count": calendar[2]},
            "security_count": connection.execute("SELECT COUNT(*) FROM a_share_security_master").fetchone()[0],
            "status_count": connection.execute("SELECT COUNT(*) FROM a_share_security_status_history").fetchone()[0],
            "daily_metadata_count": connection.execute("SELECT COUNT(*) FROM first_limit_daily_metadata").fetchone()[0],
            "minute_count": connection.execute("SELECT COUNT(*) FROM first_limit_minute_bars").fetchone()[0], "quality_flags": quality}


def write_audit_report(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Write a compact, auditable summary without persisting source payloads."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"first_limit_sync_audit_{stamp}.json"
    markdown_path = output_dir / f"first_limit_sync_audit_{stamp}.md"
    json_path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    calendar = report["calendar"]
    flags = report.get("quality_flags", {})
    lines = [
        "# First-limit controlled sync audit",
        "",
        f"- Calendar: {calendar['first_date'] or '—'} to {calendar['last_date'] or '—'} ({calendar['row_count']} days)",
        f"- Security master: {report['security_count']}",
        f"- Security statuses: {report['status_count']}",
        f"- Daily metadata: {report['daily_metadata_count']}",
        f"- Minute bars: {report['minute_count']}",
        "",
        "## Quality flags",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(flags.items())) or lines.append("- none")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _load_api(token_env: str):
    try: from gm import api
    except ImportError as exc: raise RuntimeError("gm.api is not installed") from exc
    token = os.getenv(token_env, "").strip()
    if not token: raise RuntimeError(f"set {token_env} before a non-dry-run GM sync")
    api.set_token(token); return api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled first-limit strategy data synchronization")
    parser.add_argument("command", choices=("calendar", "securities", "statuses", "daily", "minute", "audit"))
    parser.add_argument("--start-date"); parser.add_argument("--end-date"); parser.add_argument("--codes")
    parser.add_argument("--workers", type=int); parser.add_argument("--max-symbols", type=int); parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true"); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-large-run", action="store_true"); parser.add_argument("--output-dir", type=Path, default=Path("data/audits")); parser.add_argument("--token-env", default="GM_TOKEN")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        start = _parse_date(args.start_date) if args.start_date else None; end = _parse_date(args.end_date) if args.end_date else None
        if (start is None) != (end is None) and args.command != "audit": raise ValueError("start-date and end-date must be provided together")
        if args.command in {"calendar", "statuses", "daily", "minute"} and not start: raise ValueError("start-date and end-date are required")
        if args.command == "minute" and not args.codes: raise ValueError("minute sync requires --codes")
        if args.resume and not args.run_id: raise ValueError("--resume requires --run-id")
        connection = connect()
        if not args.dry_run:
            migrate(connection)
        try:
            codes = args.codes.split(",") if args.codes else None
            if args.command in {"securities", "statuses", "daily"} and not codes and args.max_symbols is None:
                raise ValueError("controlled sync requires --codes or --max-symbols; refusing an implicit full-market run")
            symbols = _pool(connection, codes, args.max_symbols) if args.command not in {"calendar", "audit"} else []
            if args.command == "audit":
                result = audit(connection, args.run_id)
                if not args.dry_run:
                    json_path, markdown_path = write_audit_report(result, args.output_dir)
                    result["report_json"] = str(json_path)
                    result["report_markdown"] = str(markdown_path)
            else:
                api = None if args.dry_run else _load_api(args.token_env)
                resume_id = args.run_id if args.resume else None
                if args.command == "calendar": result = sync_calendar(connection, api, start, end, dry_run=args.dry_run, run_id=resume_id)
                elif args.command == "securities": result = sync_securities(connection, api, symbols, workers=_workers(args.workers), dry_run=args.dry_run, run_id=resume_id)
                elif args.command == "statuses": result = sync_statuses(connection, api, symbols, start, end, dry_run=args.dry_run, run_id=resume_id)
                elif args.command == "daily": result = sync_daily(connection, api, plan_daily_gaps(connection, symbols, start, end), workers=_workers(args.workers), dry_run=args.dry_run, run_id=resume_id)
                else: result = sync_minutes(connection, api, symbols, start, end, dry_run=args.dry_run, allow_large_run=args.allow_large_run, run_id=resume_id)
                result = result.__dict__
        finally: connection.close()
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2)); return 2 if result.get("failed", 0) else 0
    except (ValueError, LookupError, RuntimeError) as exc:
        print(f"ERROR: {exc}"); return 2


if __name__ == "__main__": raise SystemExit(main())
