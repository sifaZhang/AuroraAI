import pytest
from backend.strategy.first_limit.confirmation_backtest import (
    confirmation_metrics,validate_close_backtest,validate_intraday_backtest,
)


def test_intraday_backtest_never_reads_future_or_fakes_missing_minutes():
    rows=[{"bar_time":"2026-07-31T14:30:00+08:00"},{"bar_time":"2026-07-31T15:00:00+08:00"}]
    result=validate_intraday_backtest(rows,"2026-07-31T14:30:00+08:00")
    assert result["status"]=="complete" and len(result["bars"])==1
    assert validate_intraday_backtest([],"2026-07-31T14:55:00+08:00")["status"]=="intraday_not_backtestable"


def test_official_close_only_after_close_and_same_day():
    assert validate_close_backtest("2026-07-31","2026-07-31T15:00:00+08:00")
    with pytest.raises(ValueError):validate_close_backtest("2026-07-31","2026-07-31T14:55:00+08:00")
    with pytest.raises(ValueError):validate_close_backtest("2026-08-01","2026-07-31T15:00:00+08:00")


def test_confirmation_metrics_separate_intraday_and_final():
    value=confirmation_metrics([{"intraday_grade":"S","final_grade":"A","score_error":5},
        {"intraday_grade":"A","final_grade":"A","score_error":-2}])
    assert value=={"count":2,"change_rate":.5,"industry_overestimate_rate":.5}
