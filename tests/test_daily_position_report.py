from __future__ import annotations

import json

import pytest

from backend.data_sources.settings import DataSourceSettings
from backend.dividend import daily_position_report as report_module


class Frame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        assert orient == "records"
        return self.records


class FakeTushare:
    def __init__(self, calendar, daily):
        self.calendar = calendar
        self.daily = daily
        self.calls = []

    def call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return Frame(self.calendar if endpoint == "trade_cal" else self.daily)


def _write_csv(tmp_path, rows):
    path = tmp_path / "dividend_watchlist.csv"
    content = [",".join(report_module.WATCHLIST_HEADER)]
    content.extend(",".join(row) for row in rows)
    path.write_text("\n".join(content) + "\n", encoding="utf-8")
    return path


def _calendar(open_=True):
    return [{"cal_date": "20260821", "is_open": 1 if open_ else 0}]


def test_csv_scan_maps_one_daily_request_to_multiple_statuses(tmp_path):
    path = _write_csv(tmp_path, [
        ("000001.SZ", "Watch", "S", "5", "6", "7", "0.4", "true", "now"),
        ("000002.SZ", "Entry", "A", "5", "6", "7", "0.5", "true", "now"),
        ("000003.SZ", "Add", "B", "5", "6", "7", "0.61", "true", "now"),
        ("000004.SZ", "Heavy", "", "5", "6", "7", "0.71", "true", "now"),
        ("000005.SZ", "Disabled", "S", "1", "1", "1", "99", "false", "now"),
    ])
    client = FakeTushare(_calendar(), [
        {"ts_code": f"00000{i}.SZ", "close": 10} for i in range(1, 6)
    ])
    result = report_module.scan_watchlist(report_module.load_watchlist(path), client, report_module.date(2026, 8, 21))

    assert result["watch_count"] == result["entry_count"] == result["add_count"] == result["heavy_count"] == 1
    assert result["priced_count"] == 4
    assert result["heavy"][0]["symbol"] == "000004.SZ"
    assert result["add"][0]["three_year_average_yield_pct"] == pytest.approx(6.1)
    assert "【重仓】" in report_module.format_report(result)
    assert [call[0] for call in client.calls] == ["trade_cal", "daily"]
    assert client.calls[1][1] == {"trade_date": "20260821", "fields": "ts_code,trade_date,close"}


def test_blank_settings_and_missing_dps_are_safe(tmp_path):
    path = _write_csv(tmp_path, [
        ("000001.SZ", "Blank", "", "", "", "", "", "true", "now"),
    ])
    result = report_module.scan_watchlist(
        report_module.load_watchlist(path), FakeTushare(_calendar(), [{"ts_code": "000001.SZ", "close": 10}]),
        report_module.date(2026, 8, 21),
    )
    assert result["missing_avg_dps_count"] == 1
    assert result["skipped_items"] == [{"symbol": "000001.SZ", "name": "Blank", "reason": "missing_avg_dps_3y"}]


def test_per_symbol_price_miss_does_not_stop_other_symbols(tmp_path):
    path = _write_csv(tmp_path, [
        ("000001.SZ", "Missing", "A", "1", "2", "3", "1", "true", "now"),
        ("000002.SZ", "Found", "A", "1", "2", "3", "1", "true", "now"),
    ])
    result = report_module.scan_watchlist(
        report_module.load_watchlist(path), FakeTushare(_calendar(), [{"ts_code": "000002.SZ", "close": 10}]),
        report_module.date(2026, 8, 21),
    )
    assert result["missing_price_count"] == 1
    assert result["heavy_count"] == 1
    assert result["missing_prices"][0]["symbol"] == "000001.SZ"


def test_empty_same_day_daily_is_skipped_without_old_price_fallback(tmp_path):
    path = _write_csv(tmp_path, [("000001.SZ", "One", "A", "1", "2", "3", "1", "true", "now")])
    client = FakeTushare(_calendar(), [])
    result = report_module.scan_watchlist(report_module.load_watchlist(path), client, report_module.date(2026, 8, 21))
    assert result["report_status"] == "skipped"
    assert result["skip_reason"] == "market_data_unavailable"
    assert result["add"] == result["heavy"] == []
    assert [call[0] for call in client.calls] == ["trade_cal", "daily"]


def test_non_trading_day_skips_without_daily_request(tmp_path):
    path = _write_csv(tmp_path, [("000001.SZ", "One", "A", "1", "2", "3", "1", "true", "now")])
    client = FakeTushare(_calendar(False), [])
    result = report_module.scan_watchlist(report_module.load_watchlist(path), client, report_module.date(2026, 8, 21))
    assert result["report_status"] == "skipped"
    assert result["skip_reason"] == "non_trading_day"
    assert [call[0] for call in client.calls] == ["trade_cal"]


def test_duplicate_and_wrong_header_fail(tmp_path):
    path = _write_csv(tmp_path, [
        ("000001.SZ", "One", "", "", "", "", "1", "true", "now"),
        ("000001.SZ", "Two", "", "", "", "", "1", "true", "now"),
    ])
    with pytest.raises(ValueError, match="duplicate symbol"):
        report_module.load_watchlist(path)
    path.write_text("wrong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        report_module.load_watchlist(path)


def test_signal_sorting_is_grade_then_symbol(tmp_path):
    path = _write_csv(tmp_path, [
        ("000003.SZ", "B", "B", "1", "2", "3", "1", "true", "now"),
        ("000002.SZ", "A", "A", "1", "2", "3", "1", "true", "now"),
        ("000001.SZ", "S", "S", "1", "2", "3", "1", "true", "now"),
        ("000004.SZ", "Unset", "", "1", "2", "3", "1", "true", "now"),
    ])
    daily = [{"ts_code": f"00000{i}.SZ", "close": 10} for i in range(1, 5)]
    result = report_module.scan_watchlist(report_module.load_watchlist(path), FakeTushare(_calendar(), daily), report_module.date(2026, 8, 21))
    assert [item["symbol"] for item in result["heavy"]] == ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]


def test_json_output_and_missing_token_error(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"
    report_module.write_report({"trade_date": "2026-08-21", "add": [], "heavy": []}, output)
    assert json.loads(output.read_text(encoding="utf-8"))["trade_date"] == "2026-08-21"
    monkeypatch.setattr(report_module.DataSourceSettings, "from_env", classmethod(lambda cls: DataSourceSettings()))
    assert report_module.main(["--watchlist", str(tmp_path / "no.csv")]) == 2
    assert "TUSHARE_TOKEN not configured" in capsys.readouterr().err


def test_workflow_has_schedule_and_manual_trigger_while_old_workflow_remains_existing():
    workflow = (report_module.PROJECT_ROOT / ".github/workflows/dividend-daily-report.yml").read_text(encoding="utf-8")
    old_workflow = (report_module.PROJECT_ROOT / ".github/workflows/dividend-top20.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert 'cron: "0 10 * * *"' in workflow
    assert 'daily_position_report "${ARGS[@]}" --dry-run' in workflow
    assert "name: Dividend Upcoming" in old_workflow
    assert "schedule:" in old_workflow


def test_scanner_has_no_database_or_other_market_source_imports():
    source = (report_module.PROJECT_ROOT / "backend/dividend/daily_position_report.py").read_text(encoding="utf-8").lower()
    assert "import sqlite" not in source
    assert "from futu" not in source
    assert "import akshare" not in source
    assert "import eastmoney" not in source
