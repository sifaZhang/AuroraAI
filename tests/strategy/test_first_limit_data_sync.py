from datetime import date

import pandas as pd
import pytest

from backend.collector.sync_first_limit_data import (MAX_MINUTE_CODES, SyncResult, _pool, _retry,
    _instrument_to_master, _is_target_stock, plan_daily_gaps, plan_security_gaps,
    plan_status_gaps, sync_calendar, sync_daily, sync_minutes, sync_securities,
    sync_statuses)
from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import DailyBar, upsert_daily_bars
from backend.strategy.first_limit.contracts import DataSource, QualityFlag
from backend.strategy.first_limit.repository import CalendarDay, upsert_calendar_days
from backend.strategy.first_limit.rules import normalize_symbol
from backend.strategy.first_limit.sync_repository import (create_run, get_daily_metadata,
    get_minute_bars, get_resumable_run)


class FakeGM:
    def __init__(self, *, failure=None, empty_minutes=False):
        self.failure, self.empty_minutes, self.calls = failure, empty_minutes, []

    def get_trading_dates(self, market, start, end):
        assert market == "SHSE"
        return ["2026-02-13", "2026-02-23"]

    def get_instruments(self, **kwargs):
        self.calls.append(("instrument", kwargs["symbols"]))
        if self.failure == kwargs["symbols"]:
            raise ConnectionError("offline")
        return [{"symbol": kwargs["symbols"], "sec_type": "stock", "sec_name": "样本",
                 "board": "MAIN_BOARD", "listed_date": "2000-01-01"}]

    def get_history_instruments(self, **kwargs):
        self.calls.append(("status", tuple(kwargs["symbols"])))
        result = []
        for symbol in kwargs["symbols"]:
            result.append({"symbol": symbol, "trade_date": "2026-02-23", "board": "MAIN_BOARD",
                           "listed_date": "2000-01-01", "is_suspended": False,
                           "pre_close": 10.05, "upper_limit": 11.06, "lower_limit": 9.05})
        return result

    def history(self, **kwargs):
        self.calls.append((kwargs["frequency"], kwargs["symbol"]))
        assert "eob" in kwargs["fields"].split(",")
        if self.failure == kwargs["symbol"]:
            raise ConnectionError("offline")
        if kwargs["frequency"] == "60s":
            if self.empty_minutes:
                return pd.DataFrame()
            return pd.DataFrame({"eob": ["2026-02-23T14:40:00+08:00", "2026-02-23T14:55:00+08:00"],
                                 "open": [10, 10], "high": [10.2, 10.3], "low": [9.9, 10], "close": [10.1, 10.2],
                                 "volume": [100, 200], "amount": [1000, 2000]})
        return pd.DataFrame({"eob": ["2026-02-13", "2026-02-23"], "open": [10, 10], "high": [11, 11],
                             "low": [9, 9], "close": [10.05, 10.1], "volume": [100, 100], "amount": [1000, 1000]})


def database(tmp_path):
    connection = connect(tmp_path / "first-limit-sync.db")
    migrate(connection)
    return connection


def symbols():
    return [normalize_symbol("600000.SH"), normalize_symbol("000001.SZ")]


def test_gm_retry_retries_immediately_without_sleep_hook():
    calls = []

    def flaky():
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise ConnectionError("temporary")
        return "ok"

    assert _retry(flaky) == ("ok", 2)
    assert calls == [1, 2, 3]


@pytest.mark.parametrize(("record", "expected"), [
    ({"sec_type": 1, "symbol": "SHSE.600000"}, True),  # GM SDK 3.0.185 stock enum
    ({"sec_type": "stock"}, True), ({"security_type": "A_STOCK"}, True),
    ({"sec_type": "fund", "symbol": "SHSE.510300"}, False),
    ({"sec_type": "index"}, False), ({"sec_type": "bond"}, False),
    ({"sec_type": 2}, False), ({}, False),
])
def test_target_stock_type_accepts_explicit_stock_representations_only(record, expected):
    assert _is_target_stock(record) is expected


@pytest.mark.parametrize(("listed", "delisted", "active"), [
    ("2000-01-01", "2038-01-01", True), ("2000-01-01", None, True),
    ("2000-01-01", "2001-01-01", False), ("2999-01-01", None, False),
])
def test_instrument_active_state_uses_dates_not_delisted_field_presence(listed, delisted, active):
    record = {"symbol": "SHSE.600000", "sec_type": 1, "board": "MAIN_BOARD", "listed_date": listed, "delisted_date": delisted}
    assert _instrument_to_master(record, normalize_symbol("600000.SH")).is_active is active


def calendar(connection):
    upsert_calendar_days(connection, [
        CalendarDay("CN", date(2026, 2, 13), True, DataSource.GM),
        CalendarDay("CN", date(2026, 2, 16), False, DataSource.GM),
        CalendarDay("CN", date(2026, 2, 23), True, DataSource.GM),
    ])


def test_calendar_sync_records_real_open_and_closed_days_idempotently(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    result = sync_calendar(connection, api, date(2026, 2, 13), date(2026, 2, 23))
    assert result.success == 11
    assert connection.execute("SELECT is_open FROM a_share_trading_calendar WHERE market='CN' AND trade_date='2026-02-16'").fetchone()[0] == 0
    again = sync_calendar(connection, api, date(2026, 2, 13), date(2026, 2, 23))
    assert again.rows == 11 and connection.execute("SELECT COUNT(*) FROM a_share_trading_calendar").fetchone()[0] == 11
    with pytest.raises(ValueError): sync_calendar(connection, api, date(2026, 2, 24), date(2026, 2, 23))
    connection.close()


def test_calendar_dry_run_does_not_call_or_write(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    result = sync_calendar(connection, None, date(2026, 2, 13), date(2026, 2, 23), dry_run=True)
    assert result.skipped == 11 and not api.calls
    assert connection.execute("SELECT COUNT(*) FROM a_share_trading_calendar").fetchone()[0] == 0
    connection.close()


def test_security_sync_normalizes_gm_symbols_and_filters_non_stock(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    result = sync_securities(connection, api, symbols(), workers=2)
    assert result.success == 2
    row = connection.execute("SELECT symbol,stock_code,exchange,listed_date,is_active FROM a_share_security_master WHERE symbol='600000.SH'").fetchone()
    assert tuple(row) == ("600000.SH", "600000", "SH", "2000-01-01", 1)
    assert connection.execute("SELECT COUNT(*) FROM a_share_security_master").fetchone()[0] == 2
    connection.close()


def test_security_and_status_gap_plans_skip_persisted_coverage(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    calendar(connection)
    sync_securities(connection, api, symbols(), workers=1)
    assert plan_security_gaps(connection, symbols()) == []
    sync_statuses(
        connection, api, [symbols()[0]],
        date(2026, 2, 13), date(2026, 2, 23),
    )
    gaps = plan_status_gaps(
        connection, symbols(), date(2026, 2, 13), date(2026, 2, 23)
    )
    assert symbols()[0] in gaps[(date(2026, 2, 13), date(2026, 2, 13))]
    assert symbols()[0] not in gaps[(date(2026, 2, 13), date(2026, 2, 23))]
    assert symbols()[1] in gaps[(date(2026, 2, 13), date(2026, 2, 23))]
    connection.close()


def test_status_sync_infers_historical_non_st_and_writes_authoritative_limits(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    result = sync_statuses(connection, api, [symbols()[0]], date(2026, 2, 13), date(2026, 2, 23))
    assert result.success == 1 and result.rows == 1
    status = connection.execute("SELECT is_st,is_suspended FROM a_share_security_status_history").fetchone()
    assert tuple(status) == (0, 0)
    metadata = connection.execute("SELECT pre_close,source_upper_limit,source_lower_limit FROM first_limit_daily_metadata").fetchone()
    assert tuple(metadata) == (10.05, 11.06, 9.05)
    flags = connection.execute("SELECT quality_flags FROM first_limit_daily_metadata").fetchone()[0]
    assert QualityFlag.MISSING_PRE_CLOSE.value not in flags
    connection.close()


def test_status_sync_infers_historical_st_from_asof_security_name(tmp_path):
    connection = database(tmp_path)

    class HistoricalST(FakeGM):
        def get_history_instruments(self, **kwargs):
            rows = super().get_history_instruments(**kwargs)
            rows[0]["sec_name"] = "*ST样本"
            return rows

    sync_statuses(
        connection, HistoricalST(), [symbols()[0]],
        date(2026, 2, 23), date(2026, 2, 23),
    )
    assert connection.execute(
        "SELECT is_st FROM a_share_security_status_history"
    ).fetchone()[0] == 1
    connection.close()


def test_daily_gap_plan_handles_initial_tail_and_middle_gaps_without_duplicate_daily_table(tmp_path):
    connection = database(tmp_path); calendar(connection)
    upsert_daily_bars(connection, [DailyBar("600000", "2026-02-13", 10, 11, 9, 10, 1, 1, "seed", "none", "2026-02-13T00:00:00+00:00")])
    plans = plan_daily_gaps(connection, [symbols()[0]], date(2026, 2, 13), date(2026, 2, 23))
    assert plans[symbols()[0]] == ((date(2026, 2, 23), date(2026, 2, 23)),)
    assert "a_share_daily_bars" in {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()


def test_daily_sync_uses_workers_but_writes_once_and_does_not_overwrite_existing_dates(tmp_path):
    connection, api = database(tmp_path), FakeGM(); calendar(connection)
    upsert_daily_bars(connection, [DailyBar("600000", "2026-02-13", 1, 1, 1, 1, 1, 1, "seed", "none", "2026-02-13T00:00:00+00:00")])
    plans = plan_daily_gaps(connection, [symbols()[0]], date(2026, 2, 13), date(2026, 2, 23))
    result = sync_daily(connection, api, plans, workers=2)
    assert result.success == 1 and result.rows == 1
    assert connection.execute("SELECT close,source FROM a_share_daily_bars WHERE stock_code='600000' AND trade_date='2026-02-13'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 2
    connection.close()


def test_daily_sync_batches_symbols_and_preserves_symbol_identity(tmp_path):
    connection = database(tmp_path); calendar(connection)

    class BatchGM(FakeGM):
        def history(self, **kwargs):
            requested = kwargs["symbol"].split(",")
            self.calls.append((kwargs["frequency"], tuple(requested)))
            return pd.DataFrame({
                "symbol": requested,
                "eob": ["2026-02-23"] * len(requested),
                "open": [10] * len(requested), "high": [11] * len(requested),
                "low": [9] * len(requested), "close": [10.1] * len(requested),
                "volume": [100] * len(requested), "amount": [1000] * len(requested),
            })

    api = BatchGM()
    plans = {
        symbol: ((date(2026, 2, 23), date(2026, 2, 23)),)
        for symbol in symbols()
    }
    result = sync_daily(connection, api, plans, workers=1)
    assert result.success == 2 and result.rows == 2
    assert len(api.calls) == 1
    assert {
        tuple(row) for row in connection.execute(
            "SELECT stock_code,trade_date FROM a_share_daily_bars"
        )
    } == {("600000", "2026-02-23"), ("000001", "2026-02-23")}
    connection.close()


def test_daily_dry_run_and_single_interval_failure_are_safe(tmp_path):
    connection = database(tmp_path); calendar(connection)
    plans = {symbols()[0]: ((date(2026, 2, 13), date(2026, 2, 23)),)}
    dry = sync_daily(connection, None, plans, dry_run=True)
    assert dry.skipped == 1 and connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 0
    failed = sync_daily(connection, FakeGM(failure="SHSE.600000"), plans, workers=1)
    assert failed.failed == 1
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 0
    connection.close()


def test_minute_sync_requires_explicit_small_scope_and_is_idempotent(tmp_path):
    connection, api = database(tmp_path), FakeGM()
    with pytest.raises(ValueError): sync_minutes(connection, api, symbols() * 3, date(2026, 2, 23), date(2026, 2, 23))
    result = sync_minutes(connection, api, [symbols()[0]], date(2026, 2, 23), date(2026, 2, 23))
    assert result.success == 1 and result.rows == 2
    bars = get_minute_bars(connection, symbols()[0], pd.Timestamp("2026-02-23T14:40:00+08:00").to_pydatetime(), pd.Timestamp("2026-02-23T14:55:00+08:00").to_pydatetime())
    assert len(bars) == 2
    again = sync_minutes(connection, api, [symbols()[0]], date(2026, 2, 23), date(2026, 2, 23))
    assert again.rows == 2 and connection.execute("SELECT COUNT(*) FROM first_limit_minute_bars").fetchone()[0] == 2
    connection.close()


def test_minute_empty_dry_run_and_resume_parameter_guard(tmp_path):
    connection, api = database(tmp_path), FakeGM(empty_minutes=True)
    dry = sync_minutes(connection, None, [symbols()[0]], date(2026, 2, 23), date(2026, 2, 23), dry_run=True)
    assert dry.skipped == 1 and connection.execute("SELECT COUNT(*) FROM first_limit_minute_bars").fetchone()[0] == 0
    empty = sync_minutes(connection, api, [symbols()[0]], date(2026, 2, 23), date(2026, 2, 23))
    assert empty.empty == 1
    run = create_run(connection, "minute", {"symbols": ["600000.SH"], "start_date": "2026-02-23"})
    with pytest.raises(ValueError): get_resumable_run(connection, run, "minute", {"symbols": ["000001.SZ"]})
    connection.close()


def test_sync_migration_run_records_and_legacy_market_data_are_preserved(tmp_path):
    connection = database(tmp_path)
    connection.execute("INSERT INTO sector_scores(source,sector_code,sector_name,sector_level,trade_date,trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at) VALUES('sw_l1','801010','农业',1,'2026-07-20',10,'weak',1,1,1,1,1,0,'now')")
    connection.commit(); migrate(connection); migrate(connection)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"first_limit_daily_metadata", "first_limit_minute_bars", "first_limit_sync_runs", "first_limit_sync_items"} <= tables
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 1
    connection.close()
