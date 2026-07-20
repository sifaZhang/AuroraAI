import pandas as pd
import pytest

from backend.sector_radar.scoring import calculate_trend_metrics, calculate_trend_score, trend_stars


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
