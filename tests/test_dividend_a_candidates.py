import sqlite3
from datetime import date

from backend.dividend.dividend_candidate_rules import CSV_COLUMNS, target_years
from backend.dividend.dividend_candidate_rules import classify_industry
from backend.dividend.dividend_candidate_service import DividendCandidateService, _aggregate_events
from backend.dividend.dividend_candidate_service import TushareDividendProvider
from backend.dividend.generate_dividend_a_candidates import write_exports
from backend.dividend.models import DividendEvent


class Provider:
    def __init__(self, events): self.events = events
    def fetch_events(self, symbols): return self.events


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE a_share_security_master(symbol TEXT, security_name TEXT, listed_date TEXT, is_active INTEGER, delisted_date TEXT, exchange TEXT)")
    connection.execute("CREATE TABLE a_share_security_status_history(symbol TEXT, effective_date TEXT, is_st INTEGER)")
    connection.execute("CREATE TABLE industry_memberships_current(symbol TEXT, level1_name TEXT, level2_name TEXT, level3_name TEXT, source TEXT)")
    return connection


def _seed(connection, symbol, *, name="测试银行", listed="2010-01-01", active=1, industry="股份制银行"):
    connection.execute("INSERT INTO a_share_security_master VALUES(?,?,?,?,?,?)", (symbol, name, listed, active, None, "SH"))
    connection.execute("INSERT INTO a_share_security_status_history VALUES(?,?,?)", (symbol, "2026-08-01", 0))
    connection.execute("INSERT INTO industry_memberships_current VALUES(?,?,?,?,?)", (symbol, "银行", "银行", industry, "tushare"))


def _events(symbol, values=(1.0, 1.0, 1.0)):
    return [DividendEvent(symbol, date(year, 4, 1), date(year, 6, 1), value, "实施") for year, value in zip((2023, 2024, 2025), values)]


def test_filters_st_delisted_and_recent_listing_and_keeps_normal_a_share():
    connection = _connection()
    _seed(connection, "600001.SH")
    _seed(connection, "600002.SH", name="*ST 测试")
    _seed(connection, "600003.SH", listed="2023-09-01")
    _seed(connection, "600004.SH", active=0)
    _seed(connection, "200001.SZ")
    _seed(connection, "201872.SZ")
    candidates, exclusions, _ = DividendCandidateService(connection, Provider(_events("600001.SH"))).generate(date(2026, 8, 7))
    assert candidates["symbol"].tolist() == ["600001.SH"]
    assert set(exclusions["exclusion_reason"]) == {"st_or_delisting", "listed_less_than_5_years", "not_active", "not_common_a_share"}


def test_aggregates_implemented_cash_events_by_natural_year_and_deduplicates():
    events = _events("600001.SH", (0.5, 0.5, 0.5)) + [
        DividendEvent("600001.SH", date(2023, 7, 1), date(2023, 8, 1), .5, "实施"),
        DividendEvent("600001.SH", date(2023, 4, 1), date(2023, 6, 1), .5, "实施"),
        DividendEvent("600001.SH", date(2024, 1, 1), None, 9, "实施"),
        DividendEvent("600001.SH", date(2025, 1, 1), date(2025, 2, 1), 9, "预案"),
    ]
    totals, _ = _aggregate_events(events, (2023, 2024, 2025))
    assert totals["600001.SH"] == {2023: 1.0, 2024: .5, 2025: .5}


def test_requires_all_years_and_latest_dps_ratio_and_stable_sorting():
    connection = _connection()
    _seed(connection, "600002.SH")
    _seed(connection, "600001.SH")
    _seed(connection, "600003.SH")
    events = _events("600002.SH") + _events("600001.SH") + _events("600003.SH", (1, 1, .5))
    candidates, exclusions, _ = DividendCandidateService(connection, Provider(events)).generate(date(2026, 8, 7))
    assert candidates["symbol"].tolist() == ["600001.SH", "600002.SH"]
    assert list(candidates.columns) == CSV_COLUMNS
    assert exclusions.loc[0, "exclusion_reason"] == "latest_year_dividend_decline"


def test_industry_exclusions_and_dynamic_years():
    connection = _connection()
    _seed(connection, "600001.SH", industry="通信设备")
    candidates, exclusions, _ = DividendCandidateService(connection, Provider(_events("600001.SH"))).generate(date(2027, 8, 7))
    assert candidates.empty
    assert exclusions.loc[0, "exclusion_reason"] == "industry_not_allowed"
    assert target_years(date(2026, 8, 7)) == (2023, 2024, 2025)
    assert target_years(date(2027, 8, 7)) == (2024, 2025, 2026)


def test_operating_industry_precedes_generic_service_keyword_and_oil_override_is_explicit():
    assert classify_industry("通信", "通信服务", "电信运营商") == "telecom_network"
    assert classify_industry("石油石化", "炼化及贸易", "炼油化工", "中国石油") == "oil_gas_resource"
    assert classify_industry("石油石化", "油服工程", "油田服务") is None
    assert classify_industry("煤炭", "煤炭开采", "动力煤") is None


def test_tushare_provider_requests_each_symbol():
    class Client:
        def __init__(self): self.calls = []
        def call(self, endpoint, **params):
            self.calls.append((endpoint, params["ts_code"]))
            return __import__("pandas").DataFrame([{"ts_code": params["ts_code"], "ann_date": "2023-01-01", "end_date": "2022-12-31", "ex_date": "2023-06-01", "cash_div_tax": 1, "div_proc": "实施"}])
    client = Client()
    events = TushareDividendProvider(client).fetch_events(["600001.SH", "600002.SH"])
    assert client.calls == [("dividend", "600001.SH"), ("dividend", "600002.SH")]
    assert [event.symbol for event in events] == ["600001.SH", "600002.SH"]


def test_csv_exports_request_bom_and_stable_columns(monkeypatch):
    connection = _connection()
    _seed(connection, "600001.SH")
    candidates, exclusions, _ = DividendCandidateService(connection, Provider(_events("600001.SH"))).generate(date(2026, 8, 7))
    captured = []
    monkeypatch.setattr("pathlib.Path.mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(type(candidates), "to_csv", lambda self, path, **kwargs: captured.append((self, kwargs)))
    from pathlib import Path
    write_exports(candidates, exclusions, Path("ignored.csv"), Path("ignored-exclusions.csv"))
    assert captured[0][1]["encoding"] == "utf-8-sig"
    assert list(captured[0][0].columns) == CSV_COLUMNS
