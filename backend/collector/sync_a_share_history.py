"""Resumable A-share daily-history synchronization into the local SQLite cache."""

from __future__ import annotations

import argparse
import math
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
    upsert_daily_bars, upsert_sync_failure, upsert_sync_success,
)

DEFAULT_WORKERS = 2
MAX_WORKERS = 8
DEFAULT_START_DATE = date(1990, 1, 1)
SOURCE = "akshare_sina"
ADJUSTMENT = "none"


@dataclass(frozen=True)
class StockItem:
    code: str
    name: str | None


@dataclass(frozen=True)
class DownloadResult:
    stock: StockItem
    start_date: date
    end_date: date
    bars: tuple[DailyBar, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class SyncSummary:
    total: int
    processed: int
    success_count: int
    failure_count: int
    skipped_count: int
    downloaded_rows: int
    rejected_rows: int
    elapsed_seconds: float


def bounded_workers(value: int) -> int:
    return value if 1 <= value <= MAX_WORKERS else DEFAULT_WORKERS


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...], label: str):
    columns = {str(column).strip().lower(): column for column in frame.columns}
    for alias in aliases:
        if alias.lower() in columns:
            return columns[alias.lower()]
    raise ValueError(f"missing {label} column; actual columns: {[str(c) for c in frame.columns]}")


def load_stock_universe(ak) -> list[StockItem]:
    try:
        frame = ak.stock_info_a_code_name()
        if frame is None or frame.empty:
            raise RuntimeError("stock_info_a_code_name returned empty data")
        code_col = _find_column(frame, ("code", "代码", "证券代码"), "stock code")
        name_col = _find_column(frame, ("name", "名称", "证券名称"), "stock name")
        stocks = {
            normalize_stock_code(row[code_col]): StockItem(
                normalize_stock_code(row[code_col]), str(row[name_col]).strip() or None,
            )
            for _, row in frame.iterrows()
        }
    except Exception as primary_error:
        stocks = _load_sw_constituent_universe(ak)
        if not stocks:
            raise RuntimeError(f"all stock-universe sources failed: {primary_error}") from primary_error
    return [stocks[code] for code in sorted(stocks)]


def _load_sw_constituent_universe(ak) -> dict[str, StockItem]:
    industries = ak.index_realtime_sw(symbol="一级行业")
    if industries is None or industries.empty:
        raise RuntimeError("SW industry universe is empty")
    industry_code_col = _find_column(industries, ("指数代码",), "industry code")
    stocks: dict[str, StockItem] = {}
    errors = []
    for raw_code in industries[industry_code_col]:
        try:
            frame = ak.index_component_sw(symbol=str(raw_code).strip())
            if frame is None or frame.empty:
                raise RuntimeError("empty constituents")
            code_col = _find_column(frame, ("证券代码", "代码"), "stock code")
            name_col = _find_column(frame, ("证券名称", "名称"), "stock name")
            for _, row in frame.iterrows():
                code = normalize_stock_code(row[code_col])
                stocks[code] = StockItem(code, str(row[name_col]).strip() or None)
        except Exception as exc:
            errors.append(f"{raw_code}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("SW constituent universe incomplete: " + " | ".join(errors)[:2000])
    return stocks


def _sina_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"unsupported A-share code: {code}")


def _optional_number(value: object, field: str, *, non_negative: bool = False) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value}") from exc
    if not math.isfinite(result) or (non_negative and result < 0):
        raise ValueError(f"invalid {field}: {value}")
    return result


def normalize_history_frame(stock: StockItem, frame: pd.DataFrame,
                            fetched_at: datetime) -> tuple[DailyBar, ...]:
    if frame is None or frame.empty:
        return ()
    columns = {
        field: _find_column(frame, aliases, field)
        for field, aliases in {
            "date": ("date", "日期"), "open": ("open", "开盘"),
            "high": ("high", "最高"), "low": ("low", "最低"),
            "close": ("close", "收盘"), "volume": ("volume", "成交量"),
        }.items()
    }
    amount_col = next((column for column in frame.columns
                       if str(column).strip().lower() in {"amount", "成交额"}), None)
    bars: list[DailyBar] = []
    for _, row in frame.iterrows():
        parsed_date = pd.to_datetime(row[columns["date"]], errors="coerce")
        if pd.isna(parsed_date):
            continue
        bars.append(DailyBar(
            stock_code=stock.code, trade_date=parsed_date.date(),
            open=_optional_number(row[columns["open"]], "open"),
            high=_optional_number(row[columns["high"]], "high"),
            low=_optional_number(row[columns["low"]], "low"),
            close=_optional_number(row[columns["close"]], "close"),
            volume=_optional_number(row[columns["volume"]], "volume", non_negative=True),
            amount=_optional_number(row[amount_col], "amount", non_negative=True) if amount_col else None,
            source=SOURCE, adjustment=ADJUSTMENT, fetched_at=fetched_at,
        ))
    return tuple(sorted(bars, key=lambda item: str(item.trade_date)))


def download_stock_history(ak, stock: StockItem, start_date: date, end_date: date,
                           *, attempts: int = 2, retry_delay: float = 1.0) -> DownloadResult:
    errors = []
    for attempt in range(attempts):
        try:
            frame = ak.stock_zh_a_daily(
                symbol=_sina_symbol(stock.code), start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"), adjust="",
            )
            bars = normalize_history_frame(stock, frame, datetime.now(timezone.utc))
            if not bars:
                raise RuntimeError("no_data")
            return DownloadResult(stock, start_date, end_date, bars)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
    return DownloadResult(stock, start_date, end_date, error=" | ".join(errors)[:4000])


def _select_stocks(connection, ak, retry_failed: bool,
                   codes: Iterable[object] | None, limit: int | None) -> list[StockItem]:
    if codes:
        selected = [StockItem(code, None) for code in sorted({normalize_stock_code(code) for code in codes})]
    elif retry_failed:
        selected = [StockItem(status.stock_code, status.stock_name)
                    for status in list_sync_statuses(connection, failed_only=True)]
    else:
        selected = load_stock_universe(ak)
    selected = sorted(selected, key=lambda stock: stock.code)
    return selected[:limit] if limit is not None else selected


def sync_history(connection, *, ak=None, limit: int | None = None, workers: int = DEFAULT_WORKERS,
                 retry_failed: bool = False, codes: Iterable[object] | None = None,
                 initial_start_date: date = DEFAULT_START_DATE, end_date: date | None = None,
                 attempts: int = 2, retry_delay: float = 1.0,
                 progress: Callable[[int, int, str], None] | None = None) -> SyncSummary:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    client = ak or get_akshare()
    target_end = end_date or date.today()
    if initial_start_date > target_end:
        raise ValueError("initial_start_date must not exceed end_date")
    selected = _select_stocks(connection, client, retry_failed, codes, limit)
    plans, skipped = [], []
    for stock in selected:
        _, latest, _ = get_daily_bar_stats(connection, stock.code, ADJUSTMENT)
        start = date.fromisoformat(latest) + timedelta(days=1) if latest else initial_start_date
        if start > target_end:
            skipped.append(stock)
        else:
            plans.append((stock, start))

    started = time.monotonic()
    success = failures = downloaded = rejected = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=bounded_workers(workers), thread_name_prefix="a-share-history") as executor:
        futures = {
            executor.submit(
                download_stock_history, client, stock, start, target_end,
                attempts=attempts, retry_delay=retry_delay,
            ): stock
            for stock, start in plans
        }
        for future in as_completed(futures):
            stock = futures[future]
            attempted_at = datetime.now(timezone.utc)
            try:
                result = future.result()
            except Exception as exc:  # Defensive isolation around worker failures.
                result = DownloadResult(stock, initial_start_date, target_end,
                                        error=f"{type(exc).__name__}: {exc}"[:4000])
            if result.error:
                upsert_sync_failure(connection, stock.code, stock.name, SOURCE, ADJUSTMENT,
                                    result.error, attempted_at)
                failures += 1
            else:
                write_result = upsert_daily_bars(connection, result.bars)
                downloaded += write_result.affected_count
                rejected += write_result.rejected_count
                first, last, row_count = get_daily_bar_stats(connection, stock.code, ADJUSTMENT)
                if write_result.affected_count == 0 or first is None or last is None:
                    upsert_sync_failure(connection, stock.code, stock.name, SOURCE, ADJUSTMENT,
                                        "no_valid_rows", attempted_at)
                    failures += 1
                else:
                    upsert_sync_success(connection, stock.code, stock.name, SOURCE, ADJUSTMENT,
                                        first, last, row_count, attempted_at, datetime.now(timezone.utc))
                    success += 1
            completed += 1
            if progress:
                progress(completed, len(plans), stock.code)
    return SyncSummary(len(selected), completed, success, failures, len(skipped), downloaded,
                       rejected, round(time.monotonic() - started, 2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize or incrementally sync local A-share daily history")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--codes", help="comma-separated A-share codes")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE.isoformat())
    parser.add_argument("--end-date", default=date.today().isoformat())
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect()
    try:
        migrate(connection)
        summary = sync_history(
            connection, limit=args.limit, workers=args.workers, retry_failed=args.retry_failed,
            codes=args.codes.split(",") if args.codes else None,
            initial_start_date=date.fromisoformat(args.start_date), end_date=date.fromisoformat(args.end_date),
            progress=lambda completed, total, code: print(f"{completed}/{total} {code}"),
        )
    finally:
        connection.close()
    print(
        f"total={summary.total} processed={summary.processed} success={summary.success_count} "
        f"failed={summary.failure_count} skipped={summary.skipped_count} "
        f"rows={summary.downloaded_rows} rejected={summary.rejected_rows} "
        f"elapsed={summary.elapsed_seconds:.2f}s"
    )
    return 0 if summary.failure_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
