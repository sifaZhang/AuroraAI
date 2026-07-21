import pandas as pd
import pytest

from backend.sector_radar.scoring import calculate_trend_metrics, calculate_trend_score, trend_level, trend_stars


def test_trend_score_remains_on_70_point_scale_without_breadth():
    bars = pd.DataFrame({"close": list(range(1, 22)), "volume": [100] * 20 + [1000]})
    metrics = calculate_trend_metrics(bars)
    assert metrics.score == 70
    assert calculate_trend_score(bars) == 70
    assert not hasattr(metrics, "breadth")


def test_trend_stars_reject_scores_above_70():
    assert trend_stars(70) == "★★★★★"
    with pytest.raises(ValueError):
        trend_stars(71)


def bars(closes=None, volumes=None):
    return pd.DataFrame({
        "close": closes or list(range(1, 22)),
        "volume": volumes or [100] * 21,
    })


def test_volume_baseline_excludes_today_and_equal_volume_does_not_score():
    baseline = [100, 100, 100, 100, 100]
    high = calculate_trend_metrics(bars(volumes=[50] * 15 + baseline + [101]))
    equal = calculate_trend_metrics(bars(volumes=[50] * 15 + baseline + [100]))
    assert high.volume_ratio == 1.01
    assert high.score == equal.score + 10
    assert equal.volume_ratio == 1.0


def test_today_volume_cannot_raise_its_own_baseline():
    metrics = calculate_trend_metrics(bars(volumes=[100] * 20 + [1000]))
    assert metrics.volume_ratio == 10.0
    assert metrics.score == 70


def test_zero_previous_volume_is_safe_and_invalid_volume_is_rejected():
    metrics = calculate_trend_metrics(bars(volumes=[100] * 15 + [0] * 5 + [100]))
    assert metrics.volume_ratio == 0
    with pytest.raises(ValueError, match="成交量"):
        calculate_trend_metrics(bars(volumes=[100] * 20 + [-1]))
    with pytest.raises(ValueError, match="成交量"):
        calculate_trend_metrics(bars(volumes=[100] * 20 + [float("inf")]))


def test_trend_requires_enough_data_and_level_boundaries_remain_stable():
    with pytest.raises(ValueError, match="至少需要21"):
        calculate_trend_metrics(pd.DataFrame({"close": range(20), "volume": [100] * 20}))
    assert [(score, trend_level(score)) for score in (60, 45, 30, 15, 0)] == [
        (60, "strong"), (45, "bullish"), (30, "neutral"), (15, "weak"), (0, "bearish")
    ]
