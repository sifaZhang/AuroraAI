import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import (
    MAX_ERROR_LENGTH, SQL_PARAMETER_BATCH_SIZE, DailyBar, get_daily_bars_between,
    get_recent_daily_bars, get_recent_daily_bars_for_stocks, get_sync_status,
    list_sync_statuses, normalize_stock_code, upsert_daily_bars,
    upsert_sync_failure, upsert_sync_success,
)


def database(tmp_path):
    connection = connect(tmp_path / "daily.db")
    migrate(connection)
    return connection


def bar(code="000001", day="2026-07-20", **changes):
    values = dict(stock_code=code, trade_date=day, open=10, high=12, low=9, close=11,
                  volume=100, amount=1000, source="sina", adjustment="none",
                  fetched_at="2026-07-21T00:00:00+00:00")
    values.update(changes)
    return DailyBar(**values)


@pytest.mark.parametrize(("value", "expected"), [
    (1, "000001"), ("1", "000001"), ("000001", "000001"),
    ("sz000001", "000001"), ("sh600000", "600000"),
    ("600000.SH", "600000"), ("000001.SZ", "000001"),
])
def test_normalize_stock_code(value, expected):
    assert normalize_stock_code(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", "1234567", "HK.00700", -1])
def test_invalid_stock_code_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_stock_code(value)


def test_single_batch_idempotent_conflict_and_duplicate_last_wins(tmp_path):
    connection = database(tmp_path)
    result = upsert_daily_bars(connection, [bar(), bar(close=11.5), bar("000002")])
    assert (result.input_count, result.normalized_count, result.unique_count,
            result.affected_count, result.rejected_count) == (3, 3, 2, 2, 0)
    assert result.min_trade_date == result.max_trade_date == "2026-07-20"
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 2
    assert get_recent_daily_bars(connection, "000001", 1)[0].close == 11.5
    repeat = upsert_daily_bars(connection, [bar(close=10.5, source="sina'; DROP TABLE stocks;--")])
    assert repeat.affected_count == 1
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 2
    assert get_recent_daily_bars(connection, "000001", 1)[0].close == 10.5
    connection.close()


def test_adjustments_coexist_and_volume_zero_is_valid(tmp_path):
    connection = database(tmp_path)
    result = upsert_daily_bars(connection, [bar(volume=0), bar(adjustment="qfq", close=8, high=10, low=7)])
    assert result.rejected_count == 0
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 2
    assert get_recent_daily_bars(connection, "000001", 1)[0].volume == 0
    assert get_recent_daily_bars(connection, "000001", 1, "qfq")[0].close == 8
    connection.close()


@pytest.mark.parametrize("changes", [
    {"close": math.nan}, {"open": math.inf}, {"volume": -1}, {"amount": -1},
    {"high": 8, "low": 9}, {"high": 10, "close": 11}, {"low": 10, "open": 9},
])
def test_invalid_daily_bars_are_rejected(tmp_path, changes):
    connection = database(tmp_path)
    result = upsert_daily_bars(connection, [bar(**changes)])
    assert result.rejected_count == 1 and result.affected_count == 0
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 0
    connection.close()


def test_database_error_rolls_back_entire_batch(tmp_path):
    connection = database(tmp_path)
    connection.execute("""CREATE TRIGGER reject_bad_source BEFORE INSERT ON a_share_daily_bars
                           WHEN NEW.source='blocked' BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        upsert_daily_bars(connection, [bar("000001"), bar("000002", source="blocked")])
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 0
    connection.close()


def seed(connection, stocks=3, days=30):
    start = date(2026, 1, 1)
    values = []
    for stock in range(1, stocks + 1):
        for offset in range(days):
            values.append(bar(stock, (start + timedelta(days=offset)).isoformat()))
    upsert_daily_bars(connection, values)


def test_recent_and_between_queries_are_bounded_and_ordered(tmp_path):
    connection = database(tmp_path)
    seed(connection, 1, 30)
    recent = get_recent_daily_bars(connection, 1, 5)
    assert len(recent) == 5
    assert [item.trade_date for item in recent] == sorted(item.trade_date for item in recent)
    assert recent[0].trade_date == "2026-01-26"
    between = get_daily_bars_between(connection, 1, "2026-01-05", "2026-01-07")
    assert [item.trade_date for item in between] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert get_recent_daily_bars(connection, "999999", 5) == []
    with pytest.raises(ValueError):
        get_recent_daily_bars(connection, 1, 0)
    with pytest.raises(ValueError):
        get_daily_bars_between(connection, 1, "2026-01-07", "2026-01-05")
    connection.close()


def test_multi_stock_window_query_deduplicates_batches_and_limits(tmp_path):
    connection = database(tmp_path)
    stocks = SQL_PARAMETER_BATCH_SIZE + 2
    seed(connection, stocks, 3)
    codes = list(range(1, stocks + 1)) + [1, "000001"]
    rows = get_recent_daily_bars_for_stocks(connection, codes, 2)
    assert len(rows) == stocks * 2
    keys = [(item.stock_code, item.trade_date) for item in rows]
    assert keys == sorted(keys)
    assert max(sum(item.stock_code == code for item in rows)
               for code in {item.stock_code for item in rows}) == 2
    assert sqlite3.sqlite_version_info >= (3, 25, 0)
    connection.close()


def test_sync_success_failure_and_recovery(tmp_path):
    connection = database(tmp_path)
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    upsert_sync_success(connection, 1, "Ping An", "sina", "none",
                        "2020-01-01", "2026-07-20", 100, now, now)
    status = get_sync_status(connection, "sz000001")
    assert status.row_count == 100 and status.consecutive_failures == 0 and status.last_error is None
    upsert_sync_failure(connection, 1, None, "sina", "none", "x" * 5000, now)
    upsert_sync_failure(connection, 1, None, "sina", "none", "again", now)
    failed = get_sync_status(connection, 1)
    assert failed.consecutive_failures == 2 and failed.last_error == "again"
    assert failed.first_trade_date == "2020-01-01" and failed.last_trade_date == "2026-07-20"
    assert failed.last_success_at == status.last_success_at and failed.row_count == 100
    upsert_sync_failure(connection, 2, "Second", "sina", "none", "y" * 5000, now)
    assert len(get_sync_status(connection, 2).last_error) == MAX_ERROR_LENGTH
    assert [item.stock_code for item in list_sync_statuses(connection, failed_only=True)] == ["000001", "000002"]
    assert [item.stock_code for item in list_sync_statuses(connection, limit=1)] == ["000001"]
    upsert_sync_success(connection, 1, "Ping An", "sina", "none",
                        "2020-01-01", "2026-07-21", 101, now, now)
    recovered = get_sync_status(connection, 1)
    assert recovered.consecutive_failures == 0 and recovered.last_error is None
    connection.close()


def test_migration_tables_indexes_checks_idempotence_and_legacy_data(tmp_path):
    connection = database(tmp_path)
    connection.execute("DROP TABLE a_share_daily_bars")
    connection.execute("DROP TABLE a_share_history_sync_status")
    connection.execute(
        """INSERT INTO sector_scores(source,sector_code,sector_name,sector_level,trade_date,
           trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at)
           VALUES('sw_l1','801010','Agriculture',1,'2026-07-20',50,'bullish',1,1,1,1,1,0,'now')"""
    )
    connection.commit()
    migrate(connection)
    migrate(connection)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='a_share_daily_bars'")}
    assert {"a_share_daily_bars", "a_share_history_sync_status"} <= tables
    assert {"idx_a_share_daily_bars_trade_date",
            "idx_a_share_daily_bars_stock_adjustment_date"} <= indexes
    pk = {row[1]: row[5] for row in connection.execute("PRAGMA table_info(a_share_daily_bars)")}
    assert (pk["stock_code"], pk["trade_date"], pk["adjustment"]) == (1, 2, 3)
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO a_share_history_sync_status(stock_code,consecutive_failures,row_count,updated_at) VALUES('000001',-1,0,'now')"
        )
    connection.close()
