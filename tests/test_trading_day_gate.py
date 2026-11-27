from __future__ import annotations

from datetime import date

import pytest

from backend.data_sources.errors import ProviderUnavailableError
from backend.data_sources.settings import DataSourceSettings
from backend.market import check_trading_day
from backend.market.trading_calendar import is_a_share_trading_day


class Frame:
    def __init__(self, records): self.records = records
    def to_dict(self, orient):
        assert orient == "records"
        return self.records


class Client:
    def __init__(self, records=None, error=None): self.records, self.error, self.calls = records, error, []
    def call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if self.error: raise self.error
        return Frame(self.records)


@pytest.mark.parametrize(("is_open", "expected"), [(1, True), (0, False)])
def test_public_calendar_maps_exact_open_status(is_open, expected):
    client = Client([{"cal_date": "20260824", "is_open": is_open}])
    assert is_a_share_trading_day(client, date(2026, 8, 24)) is expected
    assert client.calls == [("trade_cal", {"exchange": "SSE", "start_date": "20260824",
                                            "end_date": "20260824", "fields": "cal_date,is_open"})]


def test_public_calendar_missing_exact_date_fails():
    with pytest.raises(ValueError, match="no record"):
        is_a_share_trading_day(Client([{"cal_date": "20260823", "is_open": 0}]), date(2026, 8, 24))


def _configure_cli(monkeypatch, client):
    monkeypatch.setattr(check_trading_day.DataSourceSettings, "from_env",
                        classmethod(lambda cls: DataSourceSettings(tushare_token="secret")))
    monkeypatch.setattr(check_trading_day, "TushareClient", lambda *_a, **_k: client)


@pytest.mark.parametrize(("requested", "calendar_date", "is_open", "expected"), [
    ("2026-08-24", "20260824", 1, "true"), ("2026-08-23", "20260823", 0, "false"),
])
def test_cli_date_and_github_outputs(monkeypatch, tmp_path, capsys, requested, calendar_date, is_open, expected):
    _configure_cli(monkeypatch, Client([{"cal_date": calendar_date, "is_open": is_open}]))
    output = tmp_path / "github-output"
    assert check_trading_day.main(["--date", requested, "--github-output", str(output)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"trade_date={requested}", f"is_trading_day={expected}"]
    assert output.read_text(encoding="utf-8").splitlines() == [f"trade_date={requested}", f"is_trading_day={expected}"]


def test_cli_provider_failure_is_nonzero_and_writes_no_output(monkeypatch, tmp_path):
    _configure_cli(monkeypatch, Client(error=ProviderUnavailableError("calendar unavailable")))
    output = tmp_path / "github-output"
    assert check_trading_day.main(["--date", "2026-08-24", "--github-output", str(output)]) != 0
    assert not output.exists()


def test_default_report_date_uses_previous_day_before_close():
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    assert check_trading_day.default_report_date(datetime(2026, 11, 27, 4, 52, tzinfo=tz)) == date(2026, 11, 26)
    assert check_trading_day.default_report_date(datetime(2026, 11, 27, 11, 51, tzinfo=tz)) == date(2026, 11, 26)
    assert check_trading_day.default_report_date(datetime(2026, 11, 27, 20, 0, tzinfo=tz)) == date(2026, 11, 27)
    utc = ZoneInfo("UTC")
    assert check_trading_day.default_report_date(datetime(2026, 11, 26, 20, 52, tzinfo=utc)) == date(2026, 11, 26)
