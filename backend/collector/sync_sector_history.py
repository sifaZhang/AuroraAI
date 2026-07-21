"""Resumable SW level-1 sector history and current-membership synchronization."""

from __future__ import annotations

import argparse
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

import pandas as pd

from backend.collector.dividend_collector import get_akshare
from backend.expectation_gap.database import connect, migrate
from backend.market_data.sector_history_repository import (
    CLASSIFICATION_SYSTEM, Sector, SectorDailyBar, SectorMember,
    list_failed_sector_codes, record_sync_failure, record_sync_success,
    replace_current_membership, sector_bar_stats, upsert_sector_bars, upsert_sectors,
)

DEFAULT_WORKERS = 2
MAX_WORKERS = 8
DEFAULT_REQUEST_INTERVAL = 0.5
SOURCE = "akshare_sw"


@dataclass(frozen=True)
class SectorDownload:
    sector: Sector
    bars: tuple[SectorDailyBar, ...] = ()
    members: tuple[SectorMember, ...] = ()
    snapshot_date: date | None = None
    error: str | None = None


@dataclass(frozen=True)
class SyncSummary:
    total: int
    processed: int
    success_count: int
    failure_count: int
    downloaded_bars: int
    member_count: int
    elapsed_seconds: float


class RateLimiter:
    def __init__(self, interval_seconds: float = DEFAULT_REQUEST_INTERVAL):
        self.interval = max(0.0, float(interval_seconds))
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


def bounded_workers(value: int) -> int:
    return value if 1 <= value <= MAX_WORKERS else DEFAULT_WORKERS


def load_sw_level1_sectors(ak) -> list[Sector]:
    frame = ak.index_realtime_sw(symbol="一级行业")
    if frame is None or frame.empty:
        raise RuntimeError("index_realtime_sw returned no level-1 industries")
    if len(frame.columns) < 2:
        raise ValueError(f"industry list missing code/name columns: {list(frame.columns)!r}")
    sectors: dict[str, Sector] = {}
    for row in frame.itertuples(index=False, name=None):
        code, name = str(row[0]).strip(), str(row[1]).strip()
        if len(code) == 6 and code.isdigit() and name:
            sectors[code] = Sector(code, name)
    if not sectors:
        raise ValueError(f"industry list contained no valid codes; columns={list(frame.columns)!r}")
    return [sectors[code] for code in sorted(sectors)]


def _number(value: object, field: str, *, non_negative: bool = False) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value}") from exc
    if not math.isfinite(result) or (non_negative and result < 0):
        raise ValueError(f"invalid {field}: {value}")
    return result


def normalize_sector_history(sector: Sector, frame: pd.DataFrame, fetched_at: datetime,
                             start_date: date | None = None) -> tuple[SectorDailyBar, ...]:
    if frame is None or frame.empty:
        raise RuntimeError("no_history_data")
    if len(frame.columns) < 8:
        raise ValueError(f"history requires 8 fields; actual columns={list(frame.columns)!r}")
    bars = []
    for row in frame.itertuples(index=False, name=None):
        parsed = pd.to_datetime(row[1], errors="coerce")
        if pd.isna(parsed):
            continue
        trade_date = parsed.date()
        if start_date and trade_date < start_date:
            continue
        bars.append(SectorDailyBar(
            sector_code=sector.sector_code, trade_date=trade_date,
            open=_number(row[2], "open"), close=_number(row[3], "close"),
            high=_number(row[4], "high"), low=_number(row[5], "low"),
            volume=_number(row[6], "volume", non_negative=True),
            amount=_number(row[7], "amount", non_negative=True),
            fetched_at=fetched_at,
        ))
    return tuple(sorted(bars, key=lambda item: str(item.trade_date)))


def normalize_current_members(sector: Sector, frame: pd.DataFrame, snapshot_date: date) -> tuple[SectorMember, ...]:
    if frame is None or frame.empty:
        raise RuntimeError("no_membership_data")
    if len(frame.columns) < 4:
        raise ValueError(f"membership requires 4 fields; actual columns={list(frame.columns)!r}")
    members = {}
    for row in frame.itertuples(index=False, name=None):
        code = str(row[1]).strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        members[code] = SectorMember(
            sector_code=sector.sector_code, stock_code=code,
            stock_name=str(row[2]).strip() or None,
            weight=_number(row[3], "weight", non_negative=True), snapshot_date=snapshot_date,
        )
    if not members:
        raise ValueError("membership contained no valid stock codes")
    return tuple(members[code] for code in sorted(members))


def download_sector(ak, sector: Sector, start_date: date | None, *, attempts: int = 3,
                    retry_delay: float = 1.0, limiter: RateLimiter | None = None,
                    snapshot_date: date | None = None) -> SectorDownload:
    errors = []
    limiter = limiter or RateLimiter()
    snapshot = snapshot_date or date.today()
    for attempt in range(attempts):
        try:
            limiter.wait()
            history = ak.index_hist_sw(symbol=sector.sector_code, period="day")
            fetched_at = datetime.now(timezone.utc)
            bars = normalize_sector_history(sector, history, fetched_at, start_date)
            limiter.wait()
            component_frame = ak.index_component_sw(symbol=sector.sector_code)
            members = normalize_current_members(sector, component_frame, snapshot)
            return SectorDownload(sector, bars, members, snapshot)
        except Exception as exc:
            errors.append(f"{type(exc).__module__}.{type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                time.sleep(max(0.0, retry_delay))
    return SectorDownload(sector, error=" | ".join(errors)[:4000])


def _select_sectors(connection, available: list[Sector], codes: Iterable[str] | None,
                    retry_failed: bool, limit: int | None) -> list[Sector]:
    by_code = {sector.sector_code: sector for sector in available}
    if codes:
        requested = sorted({str(code).strip() for code in codes if str(code).strip()})
        unknown = [code for code in requested if code not in by_code]
        if unknown:
            raise ValueError(f"unknown sw_level1 sector codes: {','.join(unknown)}")
        selected = [by_code[code] for code in requested]
    elif retry_failed:
        selected = [by_code[code] for code in list_failed_sector_codes(connection) if code in by_code]
    else:
        selected = available
    return selected[:limit] if limit is not None else selected


def sync_sw_level1(connection, *, ak=None, limit: int | None = None,
                   workers: int = DEFAULT_WORKERS, retry_failed: bool = False,
                   codes: Iterable[str] | None = None, attempts: int = 3,
                   retry_delay: float = 1.0, request_interval: float = DEFAULT_REQUEST_INTERVAL,
                   snapshot_date: date | None = None,
                   progress: Callable[[int, int, str], None] | None = None) -> SyncSummary:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    client = ak or get_akshare()
    available = load_sw_level1_sectors(client)
    upsert_sectors(connection, available)
    selected = _select_sectors(connection, available, codes, retry_failed, limit)
    plans = []
    for sector in selected:
        _, latest, _ = sector_bar_stats(connection, sector.sector_code)
        plans.append((sector, date.fromisoformat(latest) + timedelta(days=1) if latest else None))

    started = time.monotonic()
    succeeded = failed = bar_count = member_count = processed = 0
    limiter = RateLimiter(request_interval)
    with ThreadPoolExecutor(max_workers=bounded_workers(workers), thread_name_prefix="sw-sector-history") as executor:
        futures = {
            executor.submit(
                download_sector, client, sector, start, attempts=attempts,
                retry_delay=retry_delay, limiter=limiter, snapshot_date=snapshot_date,
            ): sector
            for sector, start in plans
        }
        for future in as_completed(futures):
            sector = futures[future]
            attempted_at = datetime.now(timezone.utc)
            try:
                result = future.result()
            except Exception as exc:
                result = SectorDownload(sector, error=f"{type(exc).__module__}.{type(exc).__name__}: {exc}")
            if result.error:
                record_sync_failure(connection, sector, result.error, attempted_at)
                failed += 1
            else:
                written = upsert_sector_bars(connection, result.bars)
                members = replace_current_membership(
                    connection, sector.sector_code, result.members,
                    result.snapshot_date or date.today(), seen_at=attempted_at,
                )
                _, _, total_bars = sector_bar_stats(connection, sector.sector_code)
                record_sync_success(
                    connection, sector, result.snapshot_date or date.today(), total_bars,
                    members, attempted_at, datetime.now(timezone.utc),
                )
                succeeded += 1
                bar_count += written
                member_count += members
            processed += 1
            if progress:
                progress(processed, len(plans), sector.sector_code)
    return SyncSummary(len(selected), processed, succeeded, failed, bar_count, member_count,
                       round(time.monotonic() - started, 2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync SW level-1 sector history and current memberships")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--codes", help="comma-separated SW level-1 sector codes")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect()
    try:
        migrate(connection)
        summary = sync_sw_level1(
            connection, limit=args.limit, workers=args.workers, retry_failed=args.retry_failed,
            codes=args.codes.split(",") if args.codes else None, attempts=args.attempts,
            request_interval=args.request_interval,
            progress=lambda done, total, code: print(f"{done}/{total} {code}"),
        )
    finally:
        connection.close()
    print(
        f"total={summary.total} processed={summary.processed} success={summary.success_count} "
        f"failed={summary.failure_count} bars={summary.downloaded_bars} "
        f"members={summary.member_count} elapsed={summary.elapsed_seconds:.2f}s"
    )
    return 0 if summary.failure_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
