import math
import sqlite3
import pytest
import pandas as pd

from backend.expectation_gap.database import migrate
from backend.expectation_gap.futu_client import CollectionResult
from backend.expectation_gap.service import calculate_gap_pct, positive_number
from backend.expectation_gap.repository import patch_analyst, patch_morningstar, patch_price
from backend.collector.collect_expectations import collect_one
from backend.collector.init_hk_expectations import filter_hk_pool
from backend.expectation_gap.query import list_expectation_gaps
from backend.expectation_gap.repository import patch_manual_a_share_valuation


def test_calculate_gap_pct():
    assert calculate_gap_pct(100, 80) == 25.0
    assert round(calculate_gap_pct(90, 100), 8) == -10.0


def test_calculate_gap_rejects_invalid_values():
    for invalid in (None, 0, -1, math.nan, math.inf, "bad"):
        assert calculate_gap_pct(100, invalid) is None
        assert calculate_gap_pct(invalid, 100) is None
    assert positive_number(True) is None


class FakeClient:
    def snapshot(self, code):
        return CollectionResult("success", {"last_price": 80.0, "price_time": "2026-07-19 16:00:00"})

    def morningstar(self, code):
        return CollectionResult("success", {"fair_value": 100.0, "star_rating": 4, "rating_type": 2, "data_date": "2026-07-18"})

    def analyst(self, code):
        return CollectionResult("no_data")


def test_sample_collection_is_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    first = collect_one(connection, FakeClient(), "HK.00700", "腾讯控股")
    second = collect_one(connection, FakeClient(), "HK.00700", "腾讯控股")
    assert first["morningstar_gap_pct"] == second["morningstar_gap_pct"] == 25.0
    assert connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM stock_expectations").fetchone()[0] == 1
    row = connection.execute("SELECT morningstar_status, analyst_status FROM stock_expectations").fetchone()
    assert tuple(row) == ("success", "no_data")


def expectation_connection(tmp_path):
    connection = sqlite3.connect(tmp_path / "coverage.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    connection.execute(
        """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,created_at,updated_at)
           VALUES('SH.688192','688192','迪哲医药','A','SH','STOCK',1,'2026-07-19','2026-07-19')"""
    )
    return connection, connection.execute("SELECT id FROM stocks").fetchone()[0]


def seed_manual(connection, stock_id, value=166, data_date="2026-07-19"):
    patch_price(connection, stock_id, CollectionResult("success", {"last_price": 100, "price_time": "2026-07-19"}), "eastmoney")
    patch_morningstar(connection, stock_id, CollectionResult("success", {
        "fair_value": value, "star_rating": 4, "rating_type": 2, "data_date": data_date,
    }), "manual_futu_app", manual=True)


def morningstar_row(connection):
    return connection.execute(
        "SELECT morningstar_fair_value,morningstar_data_date,morningstar_source,morningstar_gap_pct,morningstar_status,last_error FROM stock_expectations"
    ).fetchone()


def test_manual_value_survives_automatic_no_data(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    patch_morningstar(connection, stock_id, CollectionResult("no_data"), "futu_opend")
    row = morningstar_row(connection)
    assert tuple(row[:3]) == (166, "2026-07-19", "manual_futu_app")
    assert row[3] == pytest.approx(66)


def test_manual_value_survives_automatic_error(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    patch_morningstar(connection, stock_id, CollectionResult("error", error="permission_denied"), "futu_opend")
    row = morningstar_row(connection)
    assert row[0] == 166
    assert row[2] == "manual_futu_app"
    assert row[5] == "permission_denied"


def test_manual_value_survives_automatic_zero(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    patch_morningstar(connection, stock_id, CollectionResult("success", {
        "fair_value": 0, "data_date": "2026-07-20",
    }), "futu_opend")
    assert morningstar_row(connection)[0] == 166


def test_newer_manual_date_blocks_older_automatic_value(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id, data_date="2026-07-19")
    patch_morningstar(connection, stock_id, CollectionResult("success", {
        "fair_value": 200, "star_rating": 5, "data_date": "2026-07-18",
    }), "futu_opend")
    row = morningstar_row(connection)
    assert row[0] == 166
    assert row[1] == "2026-07-19"
    assert row[2] == "manual_futu_app"


def test_newer_valid_automatic_value_overwrites_manual(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id, data_date="2026-07-19")
    patch_morningstar(connection, stock_id, CollectionResult("success", {
        "fair_value": 200, "star_rating": 5, "rating_type": 2, "data_date": "2026-07-20",
    }), "futu_opend")
    row = morningstar_row(connection)
    assert tuple(row[:4]) == (200, "2026-07-20", "futu_opend", 100)


def test_analyst_patch_does_not_modify_manual_morningstar(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    before = tuple(morningstar_row(connection)[:4])
    patch_analyst(connection, stock_id, CollectionResult("success", {
        "average": 180, "highest": 200, "lowest": 160, "total": 3, "report_count": 3,
        "data_date": "2026-07-19",
    }), "eastmoney", window_days=90)
    assert tuple(morningstar_row(connection)[:4]) == before


def test_manual_empty_values_do_not_overwrite_existing(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    patch_manual_a_share_valuation(connection, stock_id, data_date="2026-07-20",
        morningstar_fair_value=None, morningstar_star_rating=None,
        analyst_average_target=180, analyst_count=None)
    row = connection.execute("SELECT morningstar_fair_value,morningstar_star_rating,analyst_average_target FROM stock_expectations").fetchone()
    assert tuple(row) == (166, 4, 180)


def test_older_manual_csv_does_not_overwrite_newer_values(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id, data_date="2026-07-20")
    patch_manual_a_share_valuation(connection, stock_id, data_date="2026-07-19",
        morningstar_fair_value=120, morningstar_star_rating=2,
        analyst_average_target=None, analyst_count=None)
    assert tuple(morningstar_row(connection)[:2]) == (166, "2026-07-20")


def test_new_manual_values_overwrite_old_and_price_recalculates(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id, value=166, data_date="2026-07-19")
    patch_manual_a_share_valuation(connection, stock_id, data_date="2026-07-20",
        morningstar_fair_value=180, morningstar_star_rating=5,
        analyst_average_target=150, analyst_count=8)
    patch_price(connection, stock_id, CollectionResult("success", {"last_price": 120, "price_time": "2026-07-20"}), "eastmoney")
    row = connection.execute("SELECT morningstar_gap_pct,analyst_gap_pct,analyst_count FROM stock_expectations").fetchone()
    assert row[0] == pytest.approx(50)
    assert row[1] == pytest.approx(25)
    assert row[2] == 8


def test_query_filters_searches_sorts_and_hides_nulls(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    seed_manual(connection, stock_id)
    result = list_expectation_gaps(connection, market="a", q="迪哲", page_size=20)
    assert result["total"] == 1
    assert result["items"][0]["display_source"] == "手工"
    assert result["items"][0]["morningstar_gap_pct"] == pytest.approx(66)


def test_hk_update_does_not_modify_a_share_manual_data(tmp_path):
    connection, a_stock_id = expectation_connection(tmp_path)
    seed_manual(connection, a_stock_id)
    before = tuple(morningstar_row(connection)[:4])
    connection.execute(
        """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,created_at,updated_at)
           VALUES('HK.00700','00700','腾讯控股','HK','HK','STOCK',1,'2026-07-19','2026-07-19')"""
    )
    hk_id = connection.execute("SELECT id FROM stocks WHERE futu_code='HK.00700'").fetchone()[0]
    patch_price(connection, hk_id, CollectionResult("success", {"last_price": 400}), "futu_opend")
    patch_morningstar(connection, hk_id, CollectionResult("success", {"fair_value": 500, "data_date": "2026-07-20"}), "futu_opend")
    assert tuple(morningstar_row(connection)[:4]) == before


def test_hk_pool_keeps_normal_stock_and_excludes_delisted_reit_and_non_stock():
    frame = pd.DataFrame([
        {"code": "HK.00001", "name": "Normal", "stock_type": "STOCK", "delisting": False, "listing_date": "2000-01-01"},
        {"code": "HK.00002", "name": "Old", "stock_type": "STOCK", "delisting": True, "listing_date": "2000-01-01"},
        {"code": "HK.00003", "name": "Example REIT", "stock_type": "STOCK", "delisting": False, "listing_date": "2000-01-01"},
        {"code": "HK.00004", "name": "Fund", "stock_type": "ETF", "delisting": False, "listing_date": "2000-01-01"},
    ])
    pool, stats = filter_hk_pool(frame)
    assert [item["futu_code"] for item in pool] == ["HK.00001"]
    assert stats["excluded_delisted"] == 1
    assert stats["excluded_reit"] == 1
    assert stats["excluded_non_stock"] == 1


def test_no_data_sets_30_day_ttl_and_specific_check_status(tmp_path):
    connection, stock_id = expectation_connection(tmp_path)
    patch_morningstar(connection, stock_id, CollectionResult("no_data"), "futu_opend", "2026-07-19T00:00:00+00:00")
    row = connection.execute("SELECT morningstar_status,morningstar_check_status,morningstar_next_check_at FROM stock_expectations").fetchone()
    assert tuple(row) == ("no_data", "no_data", "2026-08-18T00:00:00+00:00")


def test_specific_futu_error_classification():
    from backend.expectation_gap.futu_client import FutuResearchClient
    assert FutuResearchClient._classify_error("permission denied") == "permission_denied"
    assert FutuResearchClient._classify_error("rate limit exceeded") == "rate_limited"
    assert FutuResearchClient._classify_error("connection timeout") == "connection_error"
