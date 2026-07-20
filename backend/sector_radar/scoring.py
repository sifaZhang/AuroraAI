"""Pure 70-point trend scoring for the Sector Trend Radar."""

from __future__ import annotations

import pandas as pd


def normalize_daily_bars(
    frame: pd.DataFrame,
    *,
    date_column: object,
    close_column: object,
    volume_column: object,
) -> pd.DataFrame:
    """Normalize explicitly selected source columns without guessing fields."""

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


def calculate_trend_score(sector_bars: pd.DataFrame) -> int:
    """Calculate the six V1 trend rules; the maximum is exactly 70."""

    if len(sector_bars) < 21:
        raise ValueError(f"板块K线至少需要21个有效交易日，实际只有{len(sector_bars)}个")
    if not {"close", "volume"}.issubset(sector_bars.columns):
        raise ValueError("板块K线缺少close或volume列")

    closes = sector_bars["close"].astype(float)
    volumes = sector_bars["volume"].astype(float)
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    latest_close = float(closes.iloc[-1])

    score = 0
    score += 10 if latest_close > float(ma5.iloc[-1]) else 0
    score += 10 if float(ma5.iloc[-1]) > float(ma5.iloc[-2]) else 0
    score += 15 if float(ma5.iloc[-1]) > float(ma10.iloc[-1]) else 0
    score += 15 if float(ma10.iloc[-1]) > float(ma20.iloc[-1]) else 0
    score += 10 if latest_close >= float(closes.tail(20).max()) else 0
    score += 10 if float(volumes.iloc[-1]) > float(volumes.tail(5).mean()) else 0
    return score


def trend_stars(score: int) -> str:
    """Render five bands against the 70-point trend scale."""

    if not 0 <= score <= 70:
        raise ValueError("Trend Score必须在0到70之间")
    filled = min(5, max(0, (score * 5 + 69) // 70))
    return "★" * filled + "☆" * (5 - filled)
