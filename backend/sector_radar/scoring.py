"""Pure 70-point trend scoring shared by sector data sources."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class TrendMetrics:
    score: int
    level: str
    close: float
    ma5: float
    ma10: float
    ma20: float
    volume_ratio: float
    is_20d_high: bool


def normalize_daily_bars(
    frame: pd.DataFrame,
    *,
    date_column: object,
    close_column: object,
    volume_column: object,
) -> pd.DataFrame:
    result = frame.loc[:, [date_column, close_column, volume_column]].copy()
    result = result.rename(
        columns={date_column: "date", close_column: "close", volume_column: "volume"}
    )
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce")
    return (
        result.dropna(subset=["date", "close", "volume"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
    )


def calculate_trend_metrics(sector_bars: pd.DataFrame) -> TrendMetrics:
    """Calculate six 70-point trend rules using closing prices and prior volume.

    The moving-average rules describe the current trend structure, not crossover
    events.  The volume rule compares today with the five completed sessions
    before today, so today's volume never participates in its own baseline.
    """

    if len(sector_bars) < 21:
        raise ValueError(f"板块K线至少需要21个有效交易日，实际只有{len(sector_bars)}个")
    if not {"close", "volume"}.issubset(sector_bars.columns):
        raise ValueError("板块K线缺少close或volume列")
    closes = sector_bars["close"].astype(float)
    volumes = sector_bars["volume"].astype(float)
    ma5_series = closes.rolling(5).mean()
    ma10_series = closes.rolling(10).mean()
    ma20_series = closes.rolling(20).mean()
    close = float(closes.iloc[-1])
    ma5 = float(ma5_series.iloc[-1])
    ma10 = float(ma10_series.iloc[-1])
    ma20 = float(ma20_series.iloc[-1])
    volume_window = volumes.iloc[-6:]
    if len(volume_window) != 6 or not all(math.isfinite(value) and value >= 0 for value in volume_window):
        raise ValueError("最近6个交易日成交量包含NaN、无穷值或负数")
    previous_volume_ma5 = float(volume_window.iloc[:-1].mean())
    volume_ratio = float(volume_window.iloc[-1]) / previous_volume_ma5 if previous_volume_ma5 > 0 else 0.0
    is_20d_high = close >= float(closes.tail(20).max())
    score = 0
    score += 10 if close > ma5 else 0
    score += 10 if ma5 > float(ma5_series.iloc[-2]) else 0
    score += 15 if ma5 > ma10 else 0  # MA5位于MA10上方，并非当天上穿
    score += 15 if ma10 > ma20 else 0  # MA10位于MA20上方，并非当天上穿
    score += 10 if is_20d_high else 0
    score += 10 if volume_ratio > 1 else 0
    return TrendMetrics(
        score=score,
        level=trend_level(score),
        close=close,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        volume_ratio=round(volume_ratio, 4),
        is_20d_high=is_20d_high,
    )


def calculate_trend_score(sector_bars: pd.DataFrame) -> int:
    return calculate_trend_metrics(sector_bars).score


def trend_level(score: int) -> str:
    if not 0 <= score <= 70:
        raise ValueError("Trend Score必须在0到70之间")
    if score >= 60:
        return "strong"
    if score >= 45:
        return "bullish"
    if score >= 30:
        return "neutral"
    if score >= 15:
        return "weak"
    return "bearish"


def trend_stars(score: int) -> str:
    if not 0 <= score <= 70:
        raise ValueError("Trend Score必须在0到70之间")
    filled = min(5, max(0, (score * 5 + 69) // 70))
    return "★" * filled + "☆" * (5 - filled)
