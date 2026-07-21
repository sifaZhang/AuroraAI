"""Incremental A-share history sync driven by current SW level-1 SQLite snapshots."""

from __future__ import annotations

import argparse
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

import pandas as pd

from backend.collector.dividend_collector import get_akshare
from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import (
    DailyBar, get_daily_bar_stats, list_sync_statuses, normalize_stock_code,
    upsert_daily_bars, upsert_sync_failure, upsert_sync_no_data, upsert_sync_success,
)
from backend.market_data.sector_history_repository import list_current_member_stocks

SOURCE = "sina_stock_zh_a_daily"
ADJUSTMENT = "none"
DEFAULT_WORKERS = 2
MAX_WORKERS = 8
DEFAULT_LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 30
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class StockItem:
    code: str
    name: str | None = None


@dataclass(frozen=True)
class SyncPlan:
    stock_code: str
    stock_name: str | None
    start_date: date | None
    end_date: date
    mode: str
    reason: str
    should_download: bool


@dataclass(frozen=True)
class NormalizationResult:
    bars: tuple[DailyBar, ...]
    downloaded_rows: int
    rejected_rows: int


@dataclass(frozen=True)
class DownloadResult:
    plan: SyncPlan
    status: str
    bars: tuple[DailyBar, ...] = ()
    downloaded_rows: int = 0
    rejected_rows: int = 0
    error: str | None = None


@dataclass(frozen=True)
class SyncSummary:
    mode: str
    source: str
    workers: int
    requested_stocks: int
    planned_stocks: int
    skipped_stocks: int
    needs_initialization: int
    successful_stocks: int
    no_data_stocks: int
    failed_stocks: int
    downloaded_rows: int
    accepted_rows: int
    rejected_rows: int
    affected_rows: int
    elapsed_seconds: float
    failures: tuple[tuple[str, str], ...]


def _sina_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"unsupported A-share code: {code}")


def normalize_a_share_code(value: object) -> str:
    code = normalize_stock_code(value)
    _sina_symbol(code)
    return code


def resolve_workers(cli_value: int | None, environment: dict[str, str] | None = None) -> int:
    raw = cli_value if cli_value is not None else (environment or os.environ).get(
        "A_SHARE_HISTORY_WORKERS", str(DEFAULT_WORKERS)
    )
    try:
        workers = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("workers must be an integer between 1 and 8") from exc
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 8")
    return workers


def load_stock_pool(connection, *, codes: Iterable[object] | None = None,
                    retry_failed: bool = False, limit: int | None = None) -> list[StockItem]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if codes is not None and retry_failed:
        raise ValueError("--codes cannot be combined with --retry-failed")
    if codes is not None:
        items = {normalize_a_share_code(value): StockItem(normalize_a_share_code(value)) for value in codes}
    elif retry_failed:
        items = {
            status.stock_code: StockItem(status.stock_code, status.stock_name)
            for status in list_sync_statuses(connection, failed_only=True)
        }
    else:
        items = {
            row["stock_code"]: StockItem(row["stock_code"], row["stock_name"])
            for row in list_current_member_stocks(connection)
        }
    ordered = [items[code] for code in sorted(items)]
    if limit is not None:
        ordered = ordered[:limit]
    if not ordered:
        raise RuntimeError("stock pool is empty")
    return ordered


def build_sync_plans(connection, stocks: Iterable[StockItem], *, mode: str,
                     start_date: date | None, end_date: date,
                     lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[SyncPlan]:
    if mode not in {"initial", "incremental"}:
        raise ValueError("mode must be initial or incremental")
    if not 0 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise ValueError("lookback-days must be between 0 and 30")
    if mode == "initial" and start_date is None:
        raise ValueError("initial mode requires --start-date")
    if start_date is not None and start_date > end_date:
        raise ValueError("start-date must not be later than end-date")
    plans = []
    for stock in stocks:
        if mode == "initial":
            plans.append(SyncPlan(stock.code, stock.name, start_date, end_date, mode,
                                  "explicit_initial_range", True))
            continue
        _, latest_text, _ = get_daily_bar_stats(connection, stock.code, ADJUSTMENT)
        if latest_text is None:
            plans.append(SyncPlan(stock.code, stock.name, None, end_date, mode,
                                  "skipped_needs_initialization", False))
            continue
        latest = date.fromisoformat(latest_text)
        if latest >= end_date and lookback_days == 0:
            plans.append(SyncPlan(stock.code, stock.name, None, end_date, mode,
                                  "skipped_up_to_date", False))
            continue
        planned_start = latest - timedelta(days=lookback_days)
        if planned_start > end_date:
            plans.append(SyncPlan(stock.code, stock.name, None, end_date, mode,
                                  "skipped_up_to_date", False))
        else:
            plans.append(SyncPlan(stock.code, stock.name, planned_start, end_date, mode,
                                  "incremental_lookback", True))
    return plans


def _column(frame: pd.DataFrame, aliases: tuple[str, ...], field: str):
    columns = {str(value).strip().lower(): value for value in frame.columns}
    for alias in aliases:
        if alias.lower() in columns:
            return columns[alias.lower()]
    raise ValueError(f"missing required column {field}; actual columns={list(frame.columns)!r}")


def _finite_number(value: object, field: str, *, positive: bool = False,
                   non_negative: bool = False) -> float:
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0) or (non_negative and number < 0):
        raise ValueError(f"invalid {field}: {value}")
    return number


def normalize_download_frame(stock_code: str, frame: pd.DataFrame, start_date: date,
                             end_date: date, fetched_at: datetime) -> NormalizationResult:
    if frame is None or frame.empty:
        return NormalizationResult((), 0, 0)
    aliases = {
        "date": ("date", "日期"), "open": ("open", "开盘"),
        "high": ("high", "最高"), "low": ("low", "最低"),
        "close": ("close", "收盘"), "volume": ("volume", "成交量"),
    }
    columns = {field: _column(frame, names, field) for field, names in aliases.items()}
    amount_column = next(
        (value for value in frame.columns if str(value).strip().lower() in {"amount", "成交额"}), None
    )
    normalized: dict[str, DailyBar] = {}
    rejected = 0
    for _, row in frame.iterrows():
        try:
            parsed = pd.to_datetime(row[columns["date"]], errors="raise").date()
            if parsed < start_date or parsed > end_date:
                continue
            open_price = _finite_number(row[columns["open"]], "open", positive=True)
            high = _finite_number(row[columns["high"]], "high", positive=True)
            low = _finite_number(row[columns["low"]], "low", positive=True)
            close = _finite_number(row[columns["close"]], "close", positive=True)
            if high < max(open_price, low, close) or low > min(open_price, high, close):
                raise ValueError("invalid OHLC relationship")
            volume = _finite_number(row[columns["volume"]], "volume", non_negative=True)
            amount = (
                _finite_number(row[amount_column], "amount", non_negative=True)
                if amount_column is not None and not pd.isna(row[amount_column]) else None
            )
            item = DailyBar(stock_code, parsed, open_price, high, low, close, volume, amount,
                            SOURCE, ADJUSTMENT, fetched_at)
            normalized[parsed.isoformat()] = item
        except (TypeError, ValueError, OverflowError):
            rejected += 1
    return NormalizationResult(
        tuple(normalized[key] for key in sorted(normalized)), len(frame), rejected,
    )


def default_downloader(stock_code: str, start_date: date, end_date: date):
    ak = get_akshare()
    return ak.stock_zh_a_daily(
        symbol=_sina_symbol(stock_code), start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"), adjust="",
    )


def download_plan(plan: SyncPlan, *, downloader=default_downloader, attempts: int = MAX_ATTEMPTS,
                  sleep: Callable[[float], None] = time.sleep) -> DownloadResult:
    if not plan.should_download or plan.start_date is None:
        raise ValueError("download_plan requires a planned date range")
    errors = []
    for attempt in range(attempts):
        try:
            frame = downloader(plan.stock_code, plan.start_date, plan.end_date)
            normalized = normalize_download_frame(
                plan.stock_code, frame, plan.start_date, plan.end_date, datetime.now(timezone.utc),
            )
            if normalized.downloaded_rows == 0:
                return DownloadResult(plan, "no_data")
            if not normalized.bars:
                raise ValueError(f"no valid rows; rejected={normalized.rejected_rows}")
            return DownloadResult(plan, "success", normalized.bars,
                                  normalized.downloaded_rows, normalized.rejected_rows)
        except Exception as exc:
            errors.append(f"{type(exc).__module__}.{type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                sleep(float(2 ** attempt))
    return DownloadResult(plan, "failed", error=" | ".join(errors)[:4000])


def execute_sync(connection, plans: Iterable[SyncPlan], *, workers: int,
                 downloader=default_downloader, attempts: int = MAX_ATTEMPTS,
                 sleep: Callable[[float], None] = time.sleep, dry_run: bool = False,
                 progress: Callable[[int, int, str], None] | None = None) -> SyncSummary:
    workers = resolve_workers(workers)
    items = list(plans)
    active = [plan for plan in items if plan.should_download]
    skipped = [plan for plan in items if not plan.should_download]
    started = time.monotonic()
    if dry_run:
        return SyncSummary(
            items[0].mode if items else "unknown", SOURCE, workers, len(items), len(active), len(skipped),
            sum(plan.reason == "skipped_needs_initialization" for plan in skipped),
            0, 0, 0, 0, 0, 0, 0, round(time.monotonic() - started, 3), (),
        )
    success = no_data = failed = downloaded = accepted = rejected = affected = completed = 0
    failures = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="a-share-daily-history") as executor:
        futures = {
            executor.submit(download_plan, plan, downloader=downloader, attempts=attempts, sleep=sleep): plan
            for plan in active
        }
        for future in as_completed(futures):
            plan = futures[future]
            attempted_at = datetime.now(timezone.utc)
            try:
                result = future.result()
            except Exception as exc:
                result = DownloadResult(plan, "failed", error=f"{type(exc).__name__}: {exc}"[:4000])
            if result.status == "success":
                try:
                    write = upsert_daily_bars(connection, result.bars)
                    first, last, row_count = get_daily_bar_stats(connection, plan.stock_code, ADJUSTMENT)
                    if first is None or last is None:
                        raise RuntimeError("repository returned no stored range")
                    upsert_sync_success(connection, plan.stock_code, plan.stock_name, SOURCE, ADJUSTMENT,
                                        first, last, row_count, attempted_at, datetime.now(timezone.utc))
                    success += 1
                    downloaded += result.downloaded_rows
                    accepted += len(result.bars)
                    rejected += result.rejected_rows + write.rejected_count
                    affected += write.affected_count
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"[:4000]
                    upsert_sync_failure(connection, plan.stock_code, plan.stock_name, SOURCE, ADJUSTMENT,
                                        message, attempted_at)
                    failed += 1
                    failures.append((plan.stock_code, message))
            elif result.status == "no_data":
                upsert_sync_no_data(connection, plan.stock_code, plan.stock_name, SOURCE, ADJUSTMENT, attempted_at)
                no_data += 1
            else:
                upsert_sync_failure(connection, plan.stock_code, plan.stock_name, SOURCE, ADJUSTMENT,
                                    result.error or "unknown download failure", attempted_at)
                failed += 1
                failures.append((plan.stock_code, result.error or "unknown download failure"))
            completed += 1
            if progress:
                progress(completed, len(active), plan.stock_code)
    return SyncSummary(
        items[0].mode if items else "unknown", SOURCE, workers, len(items), len(active), len(skipped),
        sum(plan.reason == "skipped_needs_initialization" for plan in skipped),
        success, no_data, failed, downloaded, accepted, rejected, affected,
        round(time.monotonic() - started, 3), tuple(sorted(failures)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync local unadjusted A-share daily history")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--codes")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def parse_options(argv: Iterable[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.incremental == bool(args.start_date):
        parser.error("choose exactly one mode: --start-date or --incremental")
    if args.codes and args.retry_failed:
        parser.error("--codes cannot be combined with --retry-failed")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if not 0 <= args.lookback_days <= MAX_LOOKBACK_DAYS:
        parser.error("--lookback-days must be between 0 and 30")
    try:
        args.workers = resolve_workers(args.workers)
        args.start_date = date.fromisoformat(args.start_date) if args.start_date else None
        args.end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    except ValueError as exc:
        parser.error(str(exc))
    if args.start_date and args.start_date > args.end_date:
        parser.error("--start-date must not be later than --end-date")
    args.mode = "incremental" if args.incremental else "initial"
    return args


def _print_plan(plan: SyncPlan) -> None:
    start = plan.start_date.isoformat() if plan.start_date else "-"
    print(f"{plan.stock_code} {start}..{plan.end_date.isoformat()} {plan.reason}")


def _print_summary(summary: SyncSummary) -> None:
    labels = {
        "Mode": summary.mode, "Source": summary.source, "Workers": summary.workers,
        "Requested stocks": summary.requested_stocks, "Planned stocks": summary.planned_stocks,
        "Skipped stocks": summary.skipped_stocks, "Needs initialization": summary.needs_initialization,
        "Successful stocks": summary.successful_stocks, "No data stocks": summary.no_data_stocks,
        "Failed stocks": summary.failed_stocks, "Downloaded rows": summary.downloaded_rows,
        "Accepted rows": summary.accepted_rows, "Rejected rows": summary.rejected_rows,
        "Affected rows": summary.affected_rows, "Elapsed time": f"{summary.elapsed_seconds:.3f}s",
    }
    for label, value in labels.items():
        print(f"{label}: {value}")
    for code, error in summary.failures:
        print(f"FAILED {code}: {error}")


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_options(argv)
    connection = connect()
    try:
        if not args.dry_run:
            migrate(connection)
        stocks = load_stock_pool(
            connection, codes=args.codes.split(",") if args.codes else None,
            retry_failed=args.retry_failed, limit=args.limit,
        )
        plans = build_sync_plans(
            connection, stocks, mode=args.mode, start_date=args.start_date,
            end_date=args.end_date, lookback_days=args.lookback_days,
        )
        if args.dry_run:
            for plan in plans:
                _print_plan(plan)
        summary = execute_sync(
            connection, plans, workers=args.workers, dry_run=args.dry_run,
            progress=lambda done, total, code: print(f"{done}/{total} {code}"),
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        connection.close()
    _print_summary(summary)
    if summary.failed_stocks or summary.needs_initialization:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
