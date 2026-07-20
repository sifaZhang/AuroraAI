import pandas as pd
import pytest

from backend.sector_radar.scoring import (
    calculate_trend_score,
    normalize_daily_bars,
    trend_stars,
)


def test_trend_score_matches_70_point_v1_rules():
    bars = pd.DataFrame({"close": list(range(1, 22)), "volume": [100] * 20 + [1000]})
    assert calculate_trend_score(bars) == 70


def test_trend_score_is_not_rescaled_to_100():
    bars = pd.DataFrame({"close": [10] * 21, "volume": [100] * 21})
    assert calculate_trend_score(bars) == 10
    assert calculate_trend_score(bars) <= 70


def test_normalize_daily_bars_uses_explicit_columns_and_sorts():
    raw = pd.DataFrame(
        {"日期": ["2026-07-02", "2026-07-01"], "收盘": [2, 1], "成交量": [20, 10]}
    )
    result = normalize_daily_bars(
        raw, date_column="日期", close_column="收盘", volume_column="成交量"
    )
    assert result["close"].tolist() == [1, 2]


def test_trend_stars_use_70_point_scale():
    assert trend_stars(70) == "★★★★★"
    assert trend_stars(0) == "☆☆☆☆☆"
    with pytest.raises(ValueError):
        trend_stars(71)
