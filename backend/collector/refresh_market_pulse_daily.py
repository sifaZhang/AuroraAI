"""Run the complete daily SW level-1 Market Pulse refresh pipeline."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable, Iterable

from backend.collector.calculate_sector_breadth import RunSummary as BreadthSummary
from backend.collector.calculate_sector_breadth import run_calculation
from backend.collector.sync_a_share_daily_history import (
    DEFAULT_LOOKBACK_DAYS,
    StockItem,
    SyncSummary as StockSyncSummary,
    build_sync_plans,
    execute_sync,
    load_stock_pool,
    resolve_workers,
)
from backend.collector.sync_sector_history import SyncSummary as SectorSyncSummary
from backend.collector.sync_sector_history import sync_sw_level1
from backend.expectation_gap.database import connect, migrate


@dataclass(frozen=True)
class DailyRefreshSummary:
    target_trade_date: str
    sector_sync: SectorSyncSummary
    stock_sync: StockSyncSummary
    breadth: BreadthSummary
    changed_sector_count: int
    improved_sector_count: int
    weakened_sector_count: int
    unchanged_sector_count: int
    elapsed_seconds: float

    @property
    def successful(self) -> bool:
        return (
            self.sector_sync.failure_count == 0
            and self.stock_sync.failed_stocks == 0
            and self.stock_sync.needs_initialization == 0
            and self.breadth.failed == 0
            and self.breadth.insufficient_data == 0
        )


def _latest_common_trade_date(connection) -> date:
    row = connection.execute(
        """SELECT MIN(last_trade_date)
           FROM sector_history_sync_status
           WHERE classification_system='sw_level1' AND status='success'"""
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("no successfully synchronized SW level-1 sector trade date")
    return date.fromisoformat(row[0])


def _score_change_counts(connection, target: str) -> tuple[int, int, int, int]:
    rows = connection.execute(
        """SELECT current.total_score,
                  (SELECT previous.total_score
                   FROM sector_breadth_scores AS previous
                   WHERE previous.classification_system=current.classification_system
                     AND previous.sector_code=current.sector_code
                     AND previous.calculation_version=current.calculation_version
                     AND previous.trade_date<current.trade_date
                     AND previous.total_score IS NOT NULL
                   ORDER BY previous.trade_date DESC LIMIT 1) AS previous_total_score
           FROM sector_breadth_scores AS current
           WHERE current.classification_system='sw_level1'
             AND current.calculation_version='breadth_v1'
             AND current.trade_date=? AND current.total_score IS NOT NULL""",
        (target,),
    ).fetchall()
    deltas = [
        float(row["total_score"]) - float(row["previous_total_score"])
        for row in rows if row["previous_total_score"] is not None
    ]
    return (
        len(deltas),
        sum(delta > 0 for delta in deltas),
        sum(delta < 0 for delta in deltas),
        sum(delta == 0 for delta in deltas),
    )


def run_daily_refresh(
    connection,
    *,
    ak=None,
    workers: int = 2,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sector_syncer: Callable = sync_sw_level1,
    stock_executor: Callable = execute_sync,
    breadth_runner: Callable = run_calculation,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> DailyRefreshSummary:
    """Refresh current snapshots, missing stock bars, and same-day Breadth scores."""

    started = time.monotonic()

    def report(stage: str):
        return lambda done, total, code: progress(stage, done, total, code) if progress else None

    sector_summary = sector_syncer(
        connection, ak=ak, workers=workers, progress=report("sector_history"),
    )
    if sector_summary.success_count == 0:
        raise RuntimeError("all SW level-1 sector history downloads failed")

    target = _latest_common_trade_date(connection)
    stocks: list[StockItem] = load_stock_pool(connection)
    plans = build_sync_plans(
        connection, stocks, mode="incremental", start_date=None,
        end_date=target, lookback_days=lookback_days,
    )
    stock_summary = stock_executor(
        connection, plans, workers=workers, progress=report("stock_daily_bars"),
    )
    if stock_summary.needs_initialization:
        raise RuntimeError(
            f"{stock_summary.needs_initialization} stocks need initial history before daily refresh"
        )

    breadth_summary, _ = breadth_runner(
        connection, trade_date=target, latest=False, recalculate=True,
    )
    changed, improved, weakened, unchanged = _score_change_counts(connection, target.isoformat())
    return DailyRefreshSummary(
        target.isoformat(), sector_summary, stock_summary, breadth_summary,
        changed, improved, weakened, unchanged,
        round(time.monotonic() - started, 3),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incrementally refresh SW level-1 Market Pulse and calculate daily score changes"
    )
    parser.add_argument("--workers", type=int)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workers = resolve_workers(args.workers)
        if not 0 <= args.lookback_days <= 30:
            raise ValueError("lookback-days must be between 0 and 30")
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2

    connection = connect()
    try:
        migrate(connection)
        summary = run_daily_refresh(
            connection, workers=workers, lookback_days=args.lookback_days,
            progress=lambda stage, done, total, code: print(
                json.dumps(
                    {"stage": stage, "completed": done, "total": total, "current_code": code},
                    ensure_ascii=False,
                )
            ),
        )
    except Exception as exc:
        print(json.dumps(
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        ))
        return 2
    finally:
        connection.close()

    payload = asdict(summary)
    payload["status"] = "success" if summary.successful else "partial"
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if summary.successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
