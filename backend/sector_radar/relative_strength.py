"""Relative strength against CSI 300 using aligned trading sessions."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


BENCHMARK_CODE = "000300"


class RelativeStrengthUnavailable(ValueError):
    """Raised when real aligned data cannot produce all three RS horizons."""


@dataclass(frozen=True)
class RelativeStrengthMetrics:
    score: int
    benchmark_code: str
    benchmark_trade_date: str
    sector_return_5d: float
    benchmark_return_5d: float
    excess_return_5d: float
    sector_return_10d: float
    benchmark_return_10d: float
    excess_return_10d: float
    sector_return_20d: float
    benchmark_return_20d: float
    excess_return_20d: float


def _normalized(frame: pd.DataFrame, close_name: str) -> pd.DataFrame:
    if not {"trade_date", "close"}.issubset(frame.columns):
        raise RelativeStrengthUnavailable("missing_trade_date_or_close")
    result = frame.loc[:, ["trade_date", "close"]].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
    result[close_name] = pd.to_numeric(result.pop("close"), errors="coerce")
    result = result.dropna().sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    result = result[result[close_name].map(lambda value: math.isfinite(float(value)) and float(value) > 0)]
    return result


def calculate_relative_strength(
    sector_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None,
    *,
    benchmark_code: str = BENCHMARK_CODE,
) -> RelativeStrengthMetrics:
    if benchmark_bars is None or benchmark_bars.empty:
        raise RelativeStrengthUnavailable("benchmark_unavailable")
    sector = _normalized(sector_bars.rename(columns={"date": "trade_date"}), "sector_close")
    benchmark = _normalized(benchmark_bars.rename(columns={"date": "trade_date"}), "benchmark_close")
    if sector.empty:
        raise RelativeStrengthUnavailable("sector_data_unavailable")
    latest_sector_date = sector.iloc[-1]["trade_date"]
    aligned = sector.merge(benchmark, on="trade_date", how="inner").sort_values("trade_date")
    if aligned.empty or aligned.iloc[-1]["trade_date"] != latest_sector_date:
        raise RelativeStrengthUnavailable("benchmark_latest_date_mismatch")
    if len(aligned) < 21:
        raise RelativeStrengthUnavailable(f"insufficient_common_trading_days:{len(aligned)}")

    returns: dict[int, tuple[float, float, float]] = {}
    for period, offset in ((5, 6), (10, 11), (20, 21)):
        sector_return = float(aligned.iloc[-1]["sector_close"] / aligned.iloc[-offset]["sector_close"] - 1)
        benchmark_return = float(aligned.iloc[-1]["benchmark_close"] / aligned.iloc[-offset]["benchmark_close"] - 1)
        returns[period] = (sector_return, benchmark_return, sector_return - benchmark_return)
    score = sum(5 for period in (5, 10, 20) if returns[period][2] > 0)
    return RelativeStrengthMetrics(
        score=score,
        benchmark_code=benchmark_code,
        benchmark_trade_date=latest_sector_date.date().isoformat(),
        sector_return_5d=returns[5][0], benchmark_return_5d=returns[5][1], excess_return_5d=returns[5][2],
        sector_return_10d=returns[10][0], benchmark_return_10d=returns[10][1], excess_return_10d=returns[10][2],
        sector_return_20d=returns[20][0], benchmark_return_20d=returns[20][1], excess_return_20d=returns[20][2],
    )
