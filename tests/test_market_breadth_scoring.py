import json
from dataclasses import replace
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from backend.collector.calculate_sector_breadth import run_calculation
from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import DailyBar, upsert_daily_bars
from backend.market_data.sector_history_repository import (
    Sector, SectorDailyBar, SectorMember, replace_current_membership,
    upsert_sector_bars, upsert_sectors,
)
from backend.sector_radar.breadth import (
    CALCULATION_VERSION, CORE_METRICS, CURRENT_SNAPSHOT_WARNING,
    MarketBreadthCalculator, piecewise_linear_score,
)
from backend.sector_radar.breadth_repository import upsert_breadth_result
from backend.sector_radar.breadth_service import MarketBreadthService


def stock_frame(*, days=20, start="2026-06-22", closes=None, volumes=None):
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=days),
        "close": closes if closes is not None else list(range(1, days + 1)),
        "volume": volumes if volumes is not None else [100] * (days - 1) + [200],
    })


def calculate(histories, *, members=None, target="2026-07-17", snapshot="2026-07-18", trend=70):
    members = members or sorted(histories)
    return MarketBreadthCalculator().calculate(
        classification_system="sw_level1", sector_code="801010", trade_date=target,
        membership_snapshot_date=snapshot, member_codes=members,
        histories=histories, trend_score=trend,
    )


def test_six_ratios_and_full_30_plus_70_equals_100():
    histories = {f"{index:06d}": stock_frame() for index in range(10)}
    result = calculate(histories)
    assert result.status == "success" and result.valid_members == 10 and result.coverage_ratio == 1
    assert all(metric.ratio == 1 for metric in result.metrics.values())
    assert [result.metrics[name].denominator for name in result.metrics] == [10] * 6
    assert sum(item.score for item in result.components.values()) == 30
    assert result.breadth_score == 30 and result.trend_score == 70 and result.total_score == 100


@pytest.mark.parametrize("ratio,lower,upper,maximum,expected", [
    (.30, .30, .75, 10, 0), (.75, .30, .75, 10, 10),
    (.525, .30, .75, 10, 5), (-1, .30, .75, 10, 0), (2, .30, .75, 10, 10),
])
def test_piecewise_linear_boundaries(ratio, lower, upper, maximum, expected):
    assert piecewise_linear_score(ratio, lower, upper, maximum) == pytest.approx(expected)


def test_metric_denominators_are_independent_and_volume_baseline_excludes_today():
    good = stock_frame()
    short = stock_frame(days=5, start="2026-07-13")
    invalid_volume = good.copy()
    invalid_volume.loc[invalid_volume.index[-1], "volume"] = 0
    histories = {"000001": good, "000002": short, "000003": invalid_volume}
    result = calculate(histories, members=["000001", "000002", "000003"])
    assert result.metrics["above_ma5"].denominator == 3
    assert result.metrics["above_ma10"].denominator == 2
    assert result.metrics["above_ma20"].denominator == 2
    assert result.metrics["advancing"].denominator == 3
    assert result.metrics["volume_expansion"].denominator == 1
    assert result.metrics["volume_expansion"].numerator == 1
    assert result.metrics["volume_expansion"].exclusion_reasons == {
        "insufficient_history": 1, "invalid_or_zero_volume": 1,
    }
    changed = good.copy()
    changed.loc[changed.index[-6:-1], "volume"] = 300
    assert calculate({"000001": changed}, members=["000001"]).metrics["volume_expansion"].numerator == 0


def test_missing_target_short_history_and_future_rows_are_excluded_correctly():
    good = stock_frame()
    missing_target = good.iloc[:-1]
    future = pd.concat([good, pd.DataFrame({"date": ["2026-07-20"], "close": [999], "volume": [999]})])
    result = calculate({"000001": missing_target, "000002": future}, members=["000001", "000002"])
    assert all(metric.denominator == 1 for metric in result.metrics.values())
    assert all(metric.exclusion_reasons == {"missing_target_date": 1} for metric in result.metrics.values())
    assert result.metrics["new_high_20"].ratio == 1  # post-target 999 was not read


def test_invalid_price_and_volume_zero_do_not_enter_denominators():
    invalid = stock_frame().astype({"close": float, "volume": float})
    invalid.loc[invalid.index[-1], "close"] = float("inf")
    invalid.loc[invalid.index[-1], "volume"] = 0
    result = calculate({"000001": invalid}, members=["000001"])
    for name in ("above_ma5", "above_ma10", "above_ma20", "advancing", "new_high_20"):
        assert result.metrics[name].denominator == 0
        assert result.metrics[name].exclusion_reasons == {"invalid_price": 1}
    assert result.metrics["volume_expansion"].exclusion_reasons == {"invalid_or_zero_volume": 1}

    invalid_ohlc = stock_frame()
    invalid_ohlc["open"] = invalid_ohlc["close"]
    invalid_ohlc["high"] = invalid_ohlc["close"]
    invalid_ohlc["low"] = invalid_ohlc["close"]
    invalid_ohlc.loc[invalid_ohlc.index[-1], "high"] = 0
    ohlc_result = calculate({"000001": invalid_ohlc}, members=["000001"])
    assert ohlc_result.metrics["above_ma20"].exclusion_reasons == {"invalid_ohlc": 1}


def test_minimum_members_and_per_core_coverage_prevent_fake_scores():
    only_nine = {f"{index:06d}": stock_frame() for index in range(9)}
    result = calculate(only_nine)
    assert result.status == "insufficient_data"
    assert result.breadth_score is None and result.total_score is None
    assert "total_members_below_10" in result.quality_warnings

    members = [f"{index:06d}" for index in range(20)]
    twelve = {code: stock_frame() for code in members[:12]}
    sufficient = calculate(twelve, members=members)
    assert sufficient.coverage_ratio == .6 and sufficient.status == "success"
    eleven = calculate({code: stock_frame() for code in members[:11]}, members=members)
    assert eleven.coverage_ratio == .55 and eleven.status == "insufficient_data"


def test_snapshot_lookahead_is_explicit_and_latest_snapshot_still_has_warning():
    histories = {f"{index:06d}": stock_frame() for index in range(10)}
    historical = calculate(histories)
    assert historical.is_approximate is True
    assert "current_membership_snapshot_used_for_history" in historical.quality_warnings
    assert historical.lookahead_warning == CURRENT_SNAPSHOT_WARNING
    current = calculate(histories, snapshot="2026-07-17")
    assert current.is_approximate is False and current.lookahead_warning == CURRENT_SNAPSHOT_WARNING


def seed_database(connection, members=10):
    now = "2026-07-18T00:00:00+00:00"
    upsert_sectors(connection, [Sector("801010", "Agriculture")], now)
    upsert_sector_bars(connection, [
        SectorDailyBar("801010", "2026-07-17", 1, 2, 1, 2, 100, 1000, fetched_at=now)
    ])
    member_rows = [SectorMember("801010", f"{index:06d}", f"s{index}", 1, "2026-07-18")
                   for index in range(members)]
    replace_current_membership(connection, "801010", member_rows, "2026-07-18", now)
    bars = []
    for index in range(members):
        code = f"{index:06d}"
        for _, row in stock_frame().iterrows():
            bars.append(DailyBar(code, row["date"].date(), row["close"], row["close"], row["close"],
                                 row["close"], row["volume"], 1000, "fixture", "none", now))
    upsert_daily_bars(connection, bars)
    connection.execute(
        """INSERT INTO sector_scores(source,sector_level,sector_code,sector_name,trade_date,
           trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at)
           VALUES('sw_l1','1','801010','Agriculture','2026-07-17',70,'strong',2,2,2,2,1,1,?)""",
        (now,),
    )
    connection.commit()


def test_repository_upsert_version_and_classification_keys_are_idempotent(tmp_path):
    connection = connect(tmp_path / "breadth.db")
    migrate(connection)
    seed_database(connection)
    outcome = MarketBreadthService(connection).calculate_sector("801010", recalculate=True)
    assert outcome.status == "success" and outcome.written == 1
    upsert_breadth_result(connection, outcome.result)
    alternate = replace(outcome.result, classification_system="future_system")
    upsert_breadth_result(connection, alternate)
    assert connection.execute("SELECT COUNT(*) FROM sector_breadth_scores").fetchone()[0] == 2
    keys = connection.execute(
        "SELECT classification_system,calculation_version FROM sector_breadth_scores ORDER BY classification_system"
    ).fetchall()
    assert [tuple(row) for row in keys] == [("future_system", CALCULATION_VERSION), ("sw_level1", CALCULATION_VERSION)]
    connection.close()


def test_service_and_command_dry_run_skip_recalculate_and_failure_isolation(tmp_path):
    connection = connect(tmp_path / "command.db")
    migrate(connection)
    seed_database(connection)
    dry, outcomes = run_calculation(connection, codes=["801010"], dry_run=True)
    assert dry.success == 1 and dry.written == 0
    assert connection.execute("SELECT COUNT(*) FROM sector_breadth_scores").fetchone()[0] == 0
    first, _ = run_calculation(connection, codes=["801010"])
    assert first.success == first.written == 1
    repeat, _ = run_calculation(connection, codes=["801010"])
    assert repeat.skipped == 1 and repeat.written == 0
    redone, _ = run_calculation(connection, codes=["801010"], recalculate=True)
    assert redone.success == redone.written == 1
    assert connection.execute("SELECT COUNT(*) FROM sector_breadth_scores").fetchone()[0] == 1

    class BrokenService:
        def available_sector_codes(self):
            return ["801010", "801020"]

        def calculate_sector(self, code, **kwargs):
            if code == "801010":
                raise RuntimeError("bad sector")
            from backend.sector_radar.breadth_service import CalculationOutcome
            return CalculationOutcome(code, "skipped")

    summary, results = run_calculation(connection, service=BrokenService())
    assert summary.failed == 1 and summary.skipped == 1
    assert "bad sector" in results[0].error
    connection.close()


def test_current_membership_sector_is_available_without_sector_daily_history(tmp_path):
    connection = connect(tmp_path / "membership-only.db")
    migrate(connection)
    seed_database(connection)
    connection.execute("DELETE FROM sector_daily_bars")
    connection.commit()

    service = MarketBreadthService(connection)
    assert service.available_sector_codes() == ["801010"]
    summary, outcomes = run_calculation(
        connection, codes=["801010"], trade_date="2026-07-17", latest=False,
        recalculate=True, dry_run=True,
    )
    assert summary.success == 1
    assert outcomes[0].result.trend_score == 70
    connection.close()


def test_database_stores_raw_counts_exclusions_quality_and_nullable_scores(tmp_path):
    connection = connect(tmp_path / "quality.db")
    migrate(connection)
    seed_database(connection, members=9)
    outcome = MarketBreadthService(connection).calculate_sector("801010", recalculate=True)
    row = connection.execute("SELECT * FROM sector_breadth_scores").fetchone()
    assert row["status"] == "insufficient_data" and row["breadth_score"] is None and row["total_score"] is None
    assert row["above_ma20_numerator"] == row["above_ma20_valid_count"] == 9
    assert json.loads(row["excluded_members"])["above_ma20"] == {}
    assert "total_members_below_10" in json.loads(row["quality_warnings"])
    connection.close()


def test_service_reuses_existing_70_point_trend_calculator_when_score_row_is_missing(tmp_path):
    connection = connect(tmp_path / "trend-fallback.db")
    migrate(connection)
    seed_database(connection)
    connection.execute("DELETE FROM sector_scores")
    connection.execute("DELETE FROM sector_daily_bars")
    now = "2026-07-18T00:00:00+00:00"
    upsert_sector_bars(connection, [
        SectorDailyBar("801010", day.date(), value, value, value, value, 100 if value < 21 else 200,
                       1000, fetched_at=now)
        for value, day in enumerate(pd.bdate_range("2026-06-19", periods=21), start=1)
    ])
    result = MarketBreadthService(connection).calculate_sector("801010", recalculate=True).result
    assert result.trade_date == "2026-07-17"
    assert result.trend_score == 70
    assert result.total_score == 100
    connection.close()
