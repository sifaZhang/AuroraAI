import math

import pandas as pd
import pytest

from backend.collector.probe_market_breadth import (
    StockBreadth, aggregate_industry, bounded_workers, breadth_preview_score,
    calculate_stock_breadth, fetch_constituents, normalize_stock_code, probe_histories,
)


def bars(count=21, *, closes=None, volumes=None):
    closes = closes or list(range(1, count + 1))
    volumes = volumes or [10] * count
    return pd.DataFrame({"date": pd.date_range("2026-06-01", periods=count),
                         "close": closes, "volume": volumes})


def test_stock_code_normalization_and_worker_bounds():
    assert normalize_stock_code(505) == "000505"
    assert normalize_stock_code("600000.0") == "600000"
    with pytest.raises(ValueError, match="invalid_stock_code"):
        normalize_stock_code("HK.00700")
    assert [bounded_workers(value) for value in (1, 4, 8, 0, 9)] == [1, 4, 8, 4, 4]


def test_all_stock_metrics_and_volume_baseline_excludes_today():
    frame = bars(volumes=[10] * 20 + [11])
    result = calculate_stock_breadth("000001", frame)
    assert result.is_up is True
    assert result.above_ma5 is True and result.above_ma20 is True
    assert result.volume_expanded is True
    assert result.at_20d_closing_high is True
    changed = frame.copy()
    changed.loc[15:19, "volume"] = 20
    assert calculate_stock_breadth("000001", changed).volume_expanded is False


def test_insufficient_nan_inf_and_zero_volume_are_not_filled():
    short = calculate_stock_breadth("000001", bars(4))
    assert short.is_up is True and short.above_ma5 is None and short.above_ma20 is None
    invalid = bars().astype({"close": float, "volume": float})
    invalid.loc[20, "close"] = math.inf
    invalid.loc[20, "volume"] = float("nan")
    result = calculate_stock_breadth("000001", invalid)
    assert result.is_up is None and result.above_ma5 is None and result.above_ma20 is None
    assert result.volume_expanded is None and result.at_20d_closing_high is None
    halted = bars(volumes=[0] * 21).drop(index=[5, 8])
    halted_result = calculate_stock_breadth("000001", halted)
    assert halted_result.volume_expanded is False
    assert len(halted) == 19  # no calendar or forward fill was introduced


def metric(code, **values):
    defaults = dict(is_up=None, above_ma5=None, above_ma20=None,
                    volume_expanded=None, at_20d_closing_high=None)
    defaults.update(values)
    return StockBreadth(code, **defaults)


def test_industry_denominators_use_only_valid_stocks():
    result = aggregate_industry([
        metric("000001", is_up=True, above_ma5=True, above_ma20=False),
        metric("000002", is_up=False, above_ma5=None, above_ma20=True),
        metric("000003", volume_expanded=True, at_20d_closing_high=True),
    ], constituent_count=10)
    assert result["constituent_count"] == 10
    assert result["valid_price_count"] == 2 and result["advancing_ratio"] == 0.5
    assert result["valid_ma5_count"] == 1 and result["above_ma5_ratio"] == 1
    assert result["valid_ma20_count"] == 2 and result["above_ma20_ratio"] == 0.5
    assert result["valid_volume_count"] == result["valid_high20_count"] == 1


def test_empty_industry_and_preview_boundaries():
    empty = aggregate_industry([], 0)
    assert all(empty[key] is None for key in (
        "advancing_ratio", "above_ma5_ratio", "above_ma20_ratio",
        "volume_expansion_ratio", "new_20d_closing_high_ratio"))
    assert empty["breadth_score_preview"] == 0 and empty["preview_only"] is True
    thresholds = {"advancing_ratio": .6, "above_ma5_ratio": .6,
                  "above_ma20_ratio": .5, "volume_expansion_ratio": .4,
                  "new_20d_closing_high_ratio": .15}
    assert breadth_preview_score(thresholds) == 15
    assert breadth_preview_score({key: value - .001 for key, value in thresholds.items()}) == 0


class ComponentAk:
    def index_realtime_sw(self, symbol):
        return pd.DataFrame({"指数代码": ["801010", "801020"], "指数名称": ["A", "B"]})

    def index_component_sw(self, symbol):
        if symbol == "801020":
            return pd.DataFrame()
        return pd.DataFrame({"证券代码": [2, "000001"], "证券名称": ["two", "one"]})


def test_empty_industry_is_reported_without_stopping(monkeypatch):
    monkeypatch.setattr("backend.collector.probe_market_breadth.time.sleep", lambda _: None)
    rows, summaries, errors = fetch_constituents(ComponentAk())
    assert [row["stock_code"] for row in rows] == ["000002", "000001"]
    assert summaries[0]["status"] == "success" and summaries[1]["status"] == "failed"
    assert errors and errors[0]["sector_code"] == "801020"


def test_stock_failure_does_not_stop_and_output_order_is_deterministic(monkeypatch):
    memberships = [
        {"sector_code": "801010", "sector_name": "A", "stock_code": "000002"},
        {"sector_code": "801010", "sector_name": "A", "stock_code": "000001"},
    ]

    def history(ak, code, days):
        if code == "000001":
            raise ConnectionError("down")
        return bars()

    monkeypatch.setattr("backend.collector.probe_market_breadth.load_stock_history", history)
    selected, results, statuses, errors, _ = probe_histories(object(), memberships, None, 40, 2)
    assert [row["stock_code"] for row in selected] == ["000001", "000002"]
    assert list(results) == ["000002"]
    assert [row["stock_code"] for row in statuses] == ["000001", "000002"]
    assert errors == [{"stock_code": "000001", "error": "ConnectionError: down"}]


def test_probe_model_has_no_production_score_fields():
    fields = StockBreadth.__dataclass_fields__
    assert "capital_flow_score" not in fields
    assert "composite_score" not in fields
