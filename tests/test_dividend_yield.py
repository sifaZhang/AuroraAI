from datetime import date

import pandas as pd

from backend.analysis.dividend_yield import calculate_dividend_top20, calculate_dividend_yield
from backend.collector.dividend_collector import (
    collect_dividend_candidates,
    _normalize_eastmoney_fhps_frame,
    normalize_stock_code,
    parse_cash_dividend_per_10,
    parse_date,
)


def test_parse_cash_dividend_per_10_from_common_text():
    assert parse_cash_dividend_per_10("10派1.5元") == 1.5
    assert parse_cash_dividend_per_10("每10股派发现金红利2.30元") == 2.3
    assert parse_cash_dividend_per_10(0.42, value_is_per_share=True) == 4.2


def test_normalize_stock_code():
    assert normalize_stock_code("600519.SH") == "600519"
    assert normalize_stock_code("1") == "000001"


def test_parse_date():
    assert parse_date("20260707") == date(2026, 7, 7)
    assert parse_date("2026/07/07") == date(2026, 7, 7)
    assert parse_date("") is None


def test_normalize_eastmoney_fhps_frame_by_column_position():
    raw = pd.DataFrame(
        [
            [
                "000001",
                "Ping An Bank",
                None,
                None,
                None,
                3.6,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "2026-06-01",
                "2026-06-11",
                "2026-06-12",
            ]
        ]
    )

    result = _normalize_eastmoney_fhps_frame(raw)

    assert result.loc[0, "stock_code"] == "000001"
    assert result.loc[0, "stock_name"] == "Ping An Bank"
    assert result.loc[0, "cash_dividend_per_10"] == 3.6
    assert result.loc[0, "record_date"] == date(2026, 6, 11)
    assert result.loc[0, "source"] == "eastmoney"


def test_calculate_latest_dividend_yield():
    dividends = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "cash_dividend_per_10": 2.0,
                "announcement_date": date(2025, 3, 1),
                "record_date": date(2025, 6, 1),
                "ex_dividend_date": date(2025, 6, 2),
                "source": "akshare",
            },
            {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "cash_dividend_per_10": 1.0,
                "announcement_date": date(2024, 3, 1),
                "record_date": date(2024, 6, 1),
                "ex_dividend_date": date(2024, 6, 2),
                "source": "akshare",
            },
        ]
    )
    prices = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "current_price": 10.0,
            }
        ]
    )

    result = calculate_dividend_yield(dividends, prices)

    assert len(result) == 1
    assert result.loc[0, "cash_dividend_per_10"] == 2.0
    assert result.loc[0, "cash_dividend_per_share"] == 0.2
    assert result.loc[0, "dividend_yield"] == 2.0


def test_calculate_trailing_12m_dividend_yield_sums_events():
    dividends = pd.DataFrame(
        [
            {
                "stock_code": "000002",
                "stock_name": "万科A",
                "cash_dividend_per_10": 1.0,
                "ex_dividend_date": date(2026, 1, 1),
                "source": "akshare",
            },
            {
                "stock_code": "000002",
                "stock_name": "万科A",
                "cash_dividend_per_10": 2.0,
                "ex_dividend_date": date(2026, 6, 1),
                "source": "akshare",
            },
        ]
    )
    prices = pd.DataFrame([{"stock_code": "000002", "stock_name": "万科A", "current_price": 10.0}])

    result = calculate_dividend_yield(dividends, prices, mode="trailing_12m", today=date(2026, 7, 7))

    assert result.loc[0, "cash_dividend_per_10"] == 3.0
    assert result.loc[0, "dividend_yield"] == 3.0


def test_calculate_dividend_top20_filters_past_record_dates_and_ranks():
    dividends = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "cash_dividend_per_10": 2.0,
                "record_date": date(2026, 7, 8),
                "source": "akshare",
            },
            {
                "stock_code": "000002",
                "stock_name": "Vanke A",
                "cash_dividend_per_10": 5.0,
                "record_date": date(2026, 7, 10),
                "source": "akshare",
            },
            {
                "stock_code": "000003",
                "stock_name": "Past Stock",
                "cash_dividend_per_10": 100.0,
                "record_date": date(2026, 7, 6),
                "source": "akshare",
            },
        ]
    )
    prices = pd.DataFrame(
        [
            {"stock_code": "000001", "stock_name": "Ping An Bank", "current_price": 10.0},
            {"stock_code": "000002", "stock_name": "Vanke A", "current_price": 20.0},
            {"stock_code": "000003", "stock_name": "Past Stock", "current_price": 1.0},
        ]
    )

    result = calculate_dividend_top20(dividends, prices, as_of_date=date(2026, 7, 7), top=20)

    assert list(result.columns) == ["排名", "登记日", "股票", "每10股派息", "最新股价", "本次股息率"]
    assert result["股票"].tolist() == ["000002 Vanke A", "000001 Ping An Bank"]
    assert result["排名"].tolist() == [1, 2]
    assert result["本次股息率"].tolist() == [2.5, 2.0]


def test_collect_candidates_starts_from_announced_upcoming_dividends(monkeypatch):
    announced = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "cash_dividend_per_10": 2.0,
                "record_date": date(2026, 7, 8),
                "source": "akshare",
            },
            {
                "stock_code": "000002",
                "stock_name": "Past Stock",
                "cash_dividend_per_10": 10.0,
                "record_date": date(2026, 7, 6),
                "source": "akshare",
            },
        ]
    )
    prices = pd.DataFrame([{"stock_code": "000001", "stock_name": "Ping An Bank", "current_price": 10.0}])

    def fail_full_market_prices():
        raise AssertionError("full-market prices should not be fetched first")

    monkeypatch.setattr("backend.collector.dividend_collector.fetch_announced_dividends_eastmoney", lambda: announced)
    monkeypatch.setattr(
        "backend.collector.dividend_collector.fetch_announced_dividends_akshare",
        lambda: (_ for _ in ()).throw(AssertionError("Sina fallback should not be used")),
    )
    monkeypatch.setattr("backend.collector.dividend_collector.fetch_latest_prices_akshare", fail_full_market_prices)
    monkeypatch.setattr("backend.collector.dividend_collector.fetch_latest_prices_akshare_by_codes", lambda codes: prices)

    dividends, fetched_prices = collect_dividend_candidates(limit=20, as_of_date=date(2026, 7, 7))

    assert dividends["stock_code"].tolist() == ["000001"]
    assert fetched_prices["stock_code"].tolist() == ["000001"]


def test_collect_candidates_can_fallback_to_tushare_announced_dividends(monkeypatch):
    announced = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "stock_name": None,
                "cash_dividend_per_10": 2.0,
                "record_date": date(2026, 7, 8),
                "source": "tushare",
            }
        ]
    )
    prices = pd.DataFrame([{"stock_code": "000001", "stock_name": None, "current_price": 10.0}])

    monkeypatch.setattr(
        "backend.collector.dividend_collector.fetch_announced_dividends_eastmoney",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney failed")),
    )
    monkeypatch.setattr(
        "backend.collector.dividend_collector.fetch_announced_dividends_akshare",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare failed")),
    )
    monkeypatch.setattr("backend.collector.dividend_collector.fetch_announced_dividends_tushare", lambda: announced)
    monkeypatch.setattr("backend.collector.dividend_collector.fetch_latest_prices_akshare_by_codes", lambda codes: prices)

    dividends, fetched_prices = collect_dividend_candidates(
        limit=20,
        include_tushare=True,
        as_of_date=date(2026, 7, 7),
    )

    assert dividends["source"].tolist() == ["tushare"]
    assert fetched_prices["stock_code"].tolist() == ["000001"]
