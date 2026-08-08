from datetime import date
import sqlite3

import pandas as pd

from backend.dividend.run_high_dividend_watch_full_dryrun import (
    PERIODS,
    _batch_dividend_events,
    _batch_prices_with_fallback,
    _is_ordinary_a_share_symbol,
    _normal_a_shares,
)


def test_ordinary_a_share_symbol_ranges_exclude_depository_receipts_and_b_shares():
    assert _is_ordinary_a_share_symbol("600000.SH")
    assert _is_ordinary_a_share_symbol("688001.SH")
    assert _is_ordinary_a_share_symbol("000001.SZ")
    assert _is_ordinary_a_share_symbol("301001.SZ")
    assert not _is_ordinary_a_share_symbol("689009.SH")
    assert not _is_ordinary_a_share_symbol("900901.SH")
    assert not _is_ordinary_a_share_symbol("200001.SZ")


def test_normal_a_share_universe_excludes_cdr_without_symbol_specific_rule(tmp_path):
    connection = sqlite3.connect(tmp_path / "universe.db")
    connection.executescript(
        """CREATE TABLE a_share_security_master(
               symbol TEXT,security_name TEXT,exchange TEXT,is_active INTEGER,delisted_date TEXT
           );
           CREATE TABLE a_share_security_status_history(
               symbol TEXT,effective_date TEXT,is_st INTEGER
           );
           CREATE TABLE industry_memberships_current(
               symbol TEXT,level1_name TEXT,level2_name TEXT
           );"""
    )
    connection.executemany(
        "INSERT INTO a_share_security_master VALUES(?,?,?,?,?)",
        [
            ("688001.SH", "科创普通股", "SH", 1, None),
            ("689001.SH", "存托凭证样本", "SH", 1, None),
            ("301001.SZ", "创业板普通股", "SZ", 1, None),
        ],
    )
    symbols = {row[0] for row in _normal_a_shares(connection, date(2026, 8, 8))}
    assert symbols == {"688001.SH", "301001.SZ"}
    connection.close()


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
