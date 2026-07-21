import pandas as pd
import pytest

from backend.sector_radar.relative_strength import RelativeStrengthUnavailable, calculate_relative_strength


def frame(closes, dates=None):
    dates = pd.bdate_range("2026-06-01", periods=len(closes)) if dates is None else dates
    return pd.DataFrame({"trade_date": dates, "close": closes})


def test_all_three_horizons_outperform_and_use_exact_offsets():
    benchmark = frame([100.0] * 21)
    sector_closes = [100.0] * 21
    sector_closes[-21], sector_closes[-11], sector_closes[-6], sector_closes[-1] = 80, 85, 90, 110
    result = calculate_relative_strength(frame(sector_closes), benchmark)
    assert result.score == 15
    assert result.sector_return_5d == pytest.approx(110 / 90 - 1)
    assert result.sector_return_10d == pytest.approx(110 / 85 - 1)
    assert result.sector_return_20d == pytest.approx(110 / 80 - 1)


def test_each_horizon_scores_independently_and_equal_excess_does_not_score():
    benchmark = frame([100.0] * 21)
    sector = [100.0] * 21
    sector[-21], sector[-11], sector[-6], sector[-1] = 120, 100, 100, 110
    result = calculate_relative_strength(frame(sector), benchmark)
    assert result.excess_return_5d > 0
    assert result.excess_return_10d > 0
    assert result.excess_return_20d < 0
    assert result.score == 10
    equal = calculate_relative_strength(benchmark, benchmark)
    assert equal.score == 0


def test_alignment_uses_only_common_trading_dates_without_forward_fill():
    dates = pd.bdate_range("2026-05-01", periods=25)
    sector = frame(list(range(100, 125)), dates)
    benchmark = frame([100.0] * 24, dates.delete(7))
    result = calculate_relative_strength(sector, benchmark)
    assert result.score == 15
    assert result.sector_return_20d == pytest.approx(124 / 103 - 1)


def test_latest_date_mismatch_insufficient_history_and_missing_benchmark_are_unavailable():
    dates = pd.bdate_range("2026-06-01", periods=21)
    sector = frame([100.0] * 21, dates)
    with pytest.raises(RelativeStrengthUnavailable, match="benchmark_latest_date_mismatch"):
        calculate_relative_strength(sector, frame([100.0] * 20, dates[:-1]))
    with pytest.raises(RelativeStrengthUnavailable, match="insufficient_common_trading_days"):
        calculate_relative_strength(frame([100.0] * 20), frame([100.0] * 20))
    with pytest.raises(RelativeStrengthUnavailable, match="benchmark_unavailable"):
        calculate_relative_strength(sector, None)
