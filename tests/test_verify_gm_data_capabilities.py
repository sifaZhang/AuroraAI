import json
from datetime import datetime

from tools.verify_gm_data_capabilities import _json_default, probe


class Frame:
    columns = ["open", "close", "upper_limit"]

    def __len__(self):
        return 2


class Api:
    def __init__(self):
        self.token = None

    def set_token(self, token):
        self.token = token

    def history(self, **kwargs):
        if kwargs["frequency"] == "60s":
            raise RuntimeError("minute permission denied")
        return Frame()

    def get_instruments(self, **kwargs):
        return [{"symbol": kwargs["symbols"], "listed_date": "2000-01-01", "is_suspended": False}]

    def get_history_instruments(self, **kwargs):
        return [{"symbol": kwargs["symbols"], "trade_date": "2026-01-01", "is_suspended": False}]

    def get_trading_dates(self, exchange, start, end):
        return ["2026-01-02"]


def test_probe_records_columns_and_provider_errors():
    api = Api()
    report = probe(
        api, ("SHSE.600000",), "2020-01-01 09:30:00", "2026-07-24 15:00:00",
        token="secret", minute_start="2026-01-30 09:30:00", batch_symbols=("SHSE.600000",),
    )
    assert api.token == "secret"
    result = report["samples"]["SHSE.600000"]
    assert result["1d"]["status"] == "ok"
    assert result["1d"]["columns"] == ["open", "close", "upper_limit"]
    assert result["60s"]["status"] == "error"
    assert result["current_instrument"]["status"] == "ok"
    assert report["trading_calendar"]["SHSE"]["rows"] == 1
    assert report["windows"]["daily_start"] == "2020-01-01 09:30:00"
    assert report["request_limits"]["batch_history_instruments"][0]["requested_symbols"] == 1


def test_report_datetime_values_are_json_serializable():
    encoded = json.dumps(
        {"listed_date": datetime(2020, 1, 2, 9, 30)},
        default=_json_default,
    )
    assert "2020-01-02T09:30:00" in encoded
