"""Calculate versioned Market Breadth scores exclusively from local SQLite data."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable

from backend.expectation_gap.database import connect, migrate
from backend.sector_radar.breadth_service import MarketBreadthService


@dataclass(frozen=True)
class RunSummary:
    total: int
    success: int
    insufficient_data: int
    failed: int
    skipped: int
    written: int
    elapsed_seconds: float


def run_calculation(connection, *, codes: Iterable[str] | None = None, limit: int | None = None,
                    trade_date: str | date | None = None, latest: bool = True,
                    recalculate: bool = False, dry_run: bool = False,
                    service: MarketBreadthService | None = None):
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if trade_date is not None and latest:
        latest = False
    engine = service or MarketBreadthService(connection)
    available = engine.available_sector_codes()
    if codes:
        selected = sorted({str(code).strip() for code in codes if str(code).strip()})
        unknown = [code for code in selected if code not in available]
        if unknown:
            raise ValueError(f"unknown sector codes: {','.join(unknown)}")
    else:
        selected = available
    if limit is not None:
        selected = selected[:limit]
    started = time.monotonic()
    outcomes = []
    for code in selected:
        try:
            outcomes.append(engine.calculate_sector(
                code, trade_date=trade_date, recalculate=recalculate, dry_run=dry_run,
            ))
        except Exception as exc:
            from backend.sector_radar.breadth_service import CalculationOutcome
            outcomes.append(CalculationOutcome(code, "failed", error=f"{type(exc).__name__}: {exc}"))
    summary = RunSummary(
        total=len(selected), success=sum(row.status == "success" for row in outcomes),
        insufficient_data=sum(row.status == "insufficient_data" for row in outcomes),
        failed=sum(row.status == "failed" for row in outcomes),
        skipped=sum(row.status == "skipped" for row in outcomes),
        written=sum(row.written for row in outcomes),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    return summary, outcomes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate Market Breadth from local SQLite data only")
    parser.add_argument("--codes", help="comma-separated sector codes")
    parser.add_argument("--limit", type=int)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--trade-date")
    group.add_argument("--latest", action="store_true")
    parser.add_argument("--recalculate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _result_payload(outcome):
    if outcome.result is None:
        return {"sector_code": outcome.sector_code, "status": outcome.status, "error": outcome.error}
    result = outcome.result
    return {
        "sector_code": outcome.sector_code, "status": outcome.status,
        "trade_date": result.trade_date,
        "membership_snapshot_date": result.membership_snapshot_date,
        "metrics": {name: asdict(metric) for name, metric in result.metrics.items()},
        "components": {name: asdict(component) for name, component in result.components.items()},
        "total_members": result.total_members, "valid_members": result.valid_members,
        "coverage_ratio": result.coverage_ratio, "breadth_score": result.breadth_score,
        "trend_score": result.trend_score, "total_score": result.total_score,
        "is_approximate": result.is_approximate,
        "lookahead_warning": result.lookahead_warning,
        "quality_warnings": result.quality_warnings, "written": outcome.written,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect()
    try:
        migrate(connection)
        summary, outcomes = run_calculation(
            connection, codes=args.codes.split(",") if args.codes else None, limit=args.limit,
            trade_date=args.trade_date, latest=args.latest or not args.trade_date,
            recalculate=args.recalculate, dry_run=args.dry_run,
        )
    finally:
        connection.close()
    for outcome in outcomes:
        print(json.dumps(_result_payload(outcome), ensure_ascii=False))
    print(json.dumps(asdict(summary), ensure_ascii=False))
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
