"""Orchestration of local-only sector breadth calculations."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import pandas as pd

from backend.market_data.a_share_daily_repository import get_daily_bars_for_stocks_through_date
from backend.market_data.sector_history_repository import (
    CLASSIFICATION_SYSTEM, latest_sector_trade_date, list_current_members,
    list_sector_codes_with_history, get_sector_bars_through_date,
)
from backend.sector_radar.breadth import CALCULATION_VERSION, MarketBreadthCalculator, MarketBreadthResult
from backend.sector_radar.breadth_repository import (
    breadth_result_exists, get_trend_score, upsert_breadth_result,
)
from backend.sector_radar.scoring import calculate_trend_metrics


@dataclass(frozen=True)
class CalculationOutcome:
    sector_code: str
    status: str
    result: MarketBreadthResult | None = None
    error: str | None = None
    written: int = 0


class MarketBreadthService:
    def __init__(self, connection: sqlite3.Connection,
                 calculator: MarketBreadthCalculator | None = None):
        self.connection = connection
        self.calculator = calculator or MarketBreadthCalculator()

    def available_sector_codes(self) -> list[str]:
        return list_sector_codes_with_history(self.connection)

    def calculate_sector(self, sector_code: str, *, trade_date: str | date | None = None,
                         recalculate: bool = False, dry_run: bool = False) -> CalculationOutcome:
        target = str(trade_date) if trade_date else latest_sector_trade_date(self.connection, sector_code)
        if not target:
            return CalculationOutcome(sector_code, "failed", error="missing_sector_trade_date")
        target = date.fromisoformat(target).isoformat()
        if not recalculate and breadth_result_exists(
            self.connection, CLASSIFICATION_SYSTEM, sector_code, target, CALCULATION_VERSION,
        ):
            return CalculationOutcome(sector_code, "skipped")
        members = list_current_members(self.connection, sector_code)
        if not members:
            return CalculationOutcome(sector_code, "failed", error="missing_current_membership")
        snapshot = max(row["snapshot_date"] for row in members)
        codes = [row["stock_code"] for row in members]
        bars = get_daily_bars_for_stocks_through_date(self.connection, codes, target, 20)
        grouped = defaultdict(list)
        for bar in bars:
            grouped[str(bar.stock_code)].append({
                "date": str(bar.trade_date), "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
            })
        histories = {code: pd.DataFrame(rows) for code, rows in grouped.items()}
        trend = get_trend_score(self.connection, CLASSIFICATION_SYSTEM, sector_code, target)
        if trend is None:
            sector_bars = get_sector_bars_through_date(self.connection, sector_code, target, 21)
            if sector_bars and sector_bars[-1]["trade_date"] == target:
                trend_frame = pd.DataFrame(
                    [{"close": row["close"], "volume": row["volume"]} for row in sector_bars]
                )
                try:
                    trend = float(calculate_trend_metrics(trend_frame).score)
                except ValueError:
                    trend = None
        result = self.calculator.calculate(
            classification_system=CLASSIFICATION_SYSTEM, sector_code=sector_code,
            trade_date=target, membership_snapshot_date=snapshot,
            member_codes=codes, histories=histories, trend_score=trend,
        )
        written = 0 if dry_run else upsert_breadth_result(self.connection, result)
        return CalculationOutcome(sector_code, result.status, result=result, written=written)
