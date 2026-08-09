from datetime import date

import pandas as pd

from backend.data_sources.errors import ProviderUnavailableError
from backend.data_sources.market_price_provider import UnifiedMarketPriceProvider
from backend.data_sources.settings import DataSourceSettings
from backend.expectation_gap.futu_client import CollectionResult


class TushareStub:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if self.fail:
            raise ProviderUnavailableError("offline")
        if endpoint == "trade_cal":
            return pd.DataFrame([
                {"cal_date": "20260807", "is_open": 1},
                {"cal_date": "20260808", "is_open": 0},
            ])
        assert endpoint == "daily"
        assert params == {"trade_date": "20260807"}
        return pd.DataFrame([
            {"ts_code": "600519.SH", "close": 123.45},
            {"ts_code": "000001.SZ", "close": 12.34},
        ])


def settings():
    return DataSourceSettings(tushare_token="test", request_timeout_seconds=1,
                              max_retries=0, requests_per_minute=100000)


def test_a_share_uses_one_tushare_calendar_and_full_market_daily_batch():
    client = TushareStub()
    provider = UnifiedMarketPriceProvider(settings(), tushare_client=client,
                                          today=lambda: date(2026, 8, 9))

    progress = []
    values = provider.fetch_a_share_latest(["600519.SH", "000001.SZ", "300750.SZ"], progress=progress.append)

    assert [call[0] for call in client.calls] == ["trade_cal", "daily"]
    assert values["600519"].price == 123.45 and values["600519"].source == "tushare"
    assert values["000001"].price == 12.34
    assert values["300750"].status == "no_data"
    assert progress == ["正在通过 Tushare 查询最近A股交易日", "正在通过 Tushare 批量读取 20260807 全市场日线", "Tushare 批量行情完成：匹配2/3只"]


def test_a_share_falls_back_to_one_full_market_akshare_snapshot():
    client = TushareStub(fail=True)
    ak_calls = []

    def akshare_batch():
        ak_calls.append(True)
        return pd.DataFrame([{"stock_code": "600519", "current_price": 234.56}])

    provider = UnifiedMarketPriceProvider(settings(), tushare_client=client, akshare_fetcher=akshare_batch)
    progress = []
    values = provider.fetch_a_share_latest(["600519.SH", "000001.SZ"], progress=progress.append)

    assert len(ak_calls) == 1
    assert values["600519"].price == 234.56 and values["600519"].source == "akshare"
    assert values["000001"].status == "no_data"
    assert "正在降级 AKShare" in progress[1]


class HKClient:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def batch_snapshots(self, codes, batch_size=200):
        self.calls.append((codes, batch_size))
        return {code: CollectionResult("success", {"last_price": 10.5, "price_time": "2026-08-07"}) for code in codes}


def test_hk_uses_provider_owned_futu_batch_snapshot():
    client = HKClient()
    provider = UnifiedMarketPriceProvider(settings(), hk_client_factory=lambda: client)

    values = provider.fetch_hk_latest(["HK.00700", "HK.00005"], batch_size=200)

    assert client.calls == [(["HK.00700", "HK.00005"], 200)]
    assert values["HK.00700"].source == "futu_opend" and values["HK.00005"].price == 10.5
