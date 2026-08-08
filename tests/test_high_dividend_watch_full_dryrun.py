from datetime import date

import pandas as pd

from backend.dividend.run_high_dividend_watch_full_dryrun import (
    PERIODS,
    _batch_dividend_events,
    _batch_prices_with_fallback,
)


class DividendClient:
    def __init__(self):
        self.calls = []

    def call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        row = {
            "ts_code": "000001.SZ", "ann_date": "20230401",
            "end_date": params["end_date"], "ex_date": "20230501",
            "cash_div_tax": 1.0, "div_proc": "\u5b9e\u65bd",
            "record_date": "20230430", "pay_date": "20230501",
            "imp_ann_date": "20230420", "base_date": "20230420",
        }
        return pd.DataFrame([row, row])


def test_period_batch_uses_end_date_pages_and_exact_deduplication():
    client = DividendClient()
    events, failures, stats, requests, _elapsed = _batch_dividend_events(client)
    assert failures == []
    assert requests == len(PERIODS) == 12
    assert len(events) == len(PERIODS)
    assert [call[1]["end_date"] for call in client.calls] == list(PERIODS)
    assert all(call[1]["offset"] == 0 and call[1]["limit"] == 2000 for call in client.calls)
    assert all(item["raw_rows"] == 2 and item["pages"] == 1 for item in stats)


class PriceClient:
    def __init__(self):
        self.calls = []

    def call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        code = "000001.SZ" if len(self.calls) == 1 else "000002.SZ"
        return pd.DataFrame([{"ts_code": code, "trade_date": params["trade_date"], "close": 10}])


def test_price_batch_backfills_missing_symbols_by_trade_date():
    client = PriceClient()
    result, requests = _batch_prices_with_fallback(
        client, ["20251231", "20251230"], {"000001.SZ", "000002.SZ"},
    )
    assert requests == 2
    assert result == {
        "000001.SZ": ("20251231", 10.0),
        "000002.SZ": ("20251230", 10.0),
    }
    assert all(call[0] == "daily" for call in client.calls)
