import threading
import time
from datetime import date

import pandas as pd

from backend.collector.sync_a_share_history import (
    DEFAULT_WORKERS, MAX_WORKERS, bounded_workers, load_stock_universe,
    normalize_history_frame, sync_history,
)
from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import (
    DailyBar, get_daily_bar_stats, get_sync_status, upsert_daily_bars, upsert_sync_failure,
)


def frame(start="2026-01-01", periods=3):
    return pd.DataFrame({
        "date": pd.date_range(start, periods=periods),
        "open": [10] * periods, "high": [12] * periods, "low": [9] * periods,
        "close": [11] * periods, "volume": [100] * periods, "amount": [1000] * periods,
    })


class FakeAk:
    __version__ = "fake"

    def __init__(self, codes=("000001", "000002", "600000"), failures=(), empty=()):
        self.codes = codes
        self.failures = set(failures)
        self.empty = set(empty)
        self.calls = []
        self.thread_ids = set()

    def stock_info_a_code_name(self):
        return pd.DataFrame({"code": self.codes, "name": [f"stock-{code}" for code in self.codes]})

    def stock_zh_a_daily(self, symbol, start_date, end_date, adjust):
        code = symbol[2:]
        self.calls.append((code, start_date, end_date, adjust))
        self.thread_ids.add(threading.get_ident())
        if code in self.failures:
            raise ConnectionError("download failed")
        if code in self.empty:
            return pd.DataFrame()
        return frame(start=pd.Timestamp(start_date).strftime("%Y-%m-%d"))


def database(tmp_path):
    connection = connect(tmp_path / "sync.db")
    migrate(connection)
    return connection


def test_full_initialization_limit_status_and_worker_bounds(tmp_path):
    connection = database(tmp_path)
    client = FakeAk()
    summary = sync_history(
        connection, ak=client, limit=2, workers=2, initial_start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10), attempts=1,
    )
    assert (summary.total, summary.processed, summary.success_count,
            summary.failure_count, summary.downloaded_rows) == (2, 2, 2, 0, 6)
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 6
    assert get_sync_status(connection, "000001").row_count == 3
    assert bounded_workers(0) == DEFAULT_WORKERS and bounded_workers(8) == MAX_WORKERS
    connection.close()


def test_increment_starts_after_local_latest_and_repeat_is_idempotent(tmp_path):
    connection = database(tmp_path)
    upsert_daily_bars(connection, [
        DailyBar("000001", "2026-01-03", 10, 12, 9, 11, 100, 1000,
                 "seed", "none", "2026-01-03T00:00:00+00:00")
    ])
    client = FakeAk(codes=("000001",))
    first = sync_history(connection, ak=client, initial_start_date=date(2020, 1, 1),
                         end_date=date(2026, 1, 10), attempts=1)
    assert client.calls[0][1] == "20260104"
    assert first.success_count == 1 and get_daily_bar_stats(connection, "000001")[2] == 4
    second = sync_history(connection, ak=client, initial_start_date=date(2020, 1, 1),
                          end_date=date(2026, 1, 6), attempts=1)
    assert second.skipped_count == 1 and second.processed == 0
    assert get_daily_bar_stats(connection, "000001")[2] == 4
    connection.close()


def test_single_failure_isolated_recorded_and_recovers_with_retry_failed(tmp_path):
    connection = database(tmp_path)
    failing = FakeAk(failures=("000002",))
    result = sync_history(connection, ak=failing, initial_start_date=date(2026, 1, 1),
                          end_date=date(2026, 1, 10), attempts=1)
    assert result.success_count == 2 and result.failure_count == 1
    status = get_sync_status(connection, "000002")
    assert status.consecutive_failures == 1 and "download failed" in status.last_error
    healthy_calls_before = {call[0] for call in failing.calls if call[0] != "000002"}
    recovered = FakeAk()
    retry = sync_history(connection, ak=recovered, retry_failed=True,
                         initial_start_date=date(2026, 1, 1), end_date=date(2026, 1, 10), attempts=1)
    assert retry.total == retry.success_count == 1
    assert [call[0] for call in recovered.calls] == ["000002"]
    assert healthy_calls_before == {"000001", "600000"}
    assert get_sync_status(connection, "000002").consecutive_failures == 0
    assert get_sync_status(connection, "000002").last_error is None
    connection.close()


def test_empty_data_and_invalid_columns_are_failures(tmp_path):
    connection = database(tmp_path)
    empty = FakeAk(codes=("000001",), empty=("000001",))
    summary = sync_history(connection, ak=empty, initial_start_date=date(2026, 1, 1),
                           end_date=date(2026, 1, 10), attempts=1)
    assert summary.failure_count == 1 and "no_data" in get_sync_status(connection, 1).last_error

    class MissingColumnAk(FakeAk):
        def stock_zh_a_daily(self, **kwargs):
            return pd.DataFrame({"date": ["2026-01-01"], "close": [1]})

    missing = MissingColumnAk(codes=("000002",))
    summary = sync_history(connection, ak=missing, initial_start_date=date(2026, 1, 1),
                           end_date=date(2026, 1, 10), attempts=1)
    assert summary.failure_count == 1 and "missing open column" in get_sync_status(connection, 2).last_error
    connection.close()


def test_failure_does_not_overwrite_existing_success_range(tmp_path):
    connection = database(tmp_path)
    upsert_sync_failure(connection, "000001", "one", "akshare_sina", "none", "old", "2026-01-01T00:00:00Z")
    client = FakeAk(codes=("000001",), failures=("000001",))
    sync_history(connection, ak=client, initial_start_date=date(2026, 1, 1),
                 end_date=date(2026, 1, 10), attempts=1)
    assert get_sync_status(connection, 1).consecutive_failures == 2
    connection.close()


def test_explicit_code_success_does_not_clear_existing_stock_name(tmp_path):
    connection = database(tmp_path)
    upsert_sync_failure(connection, "000001", "existing-name", "akshare_sina", "none",
                        "old", "2026-01-01T00:00:00Z")
    sync_history(connection, ak=FakeAk(codes=("000001",)), codes=("000001",),
                 initial_start_date=date(2026, 1, 1), end_date=date(2026, 1, 10), attempts=1)
    assert get_sync_status(connection, 1).stock_name == "existing-name"
    connection.close()


def test_download_threads_never_touch_sqlite(tmp_path):
    connection = database(tmp_path)
    main_thread = threading.get_ident()

    class SlowAk(FakeAk):
        def stock_zh_a_daily(self, *args, **kwargs):
            time.sleep(0.02)
            return super().stock_zh_a_daily(*args, **kwargs)

    client = SlowAk()
    sql_threads = set()
    connection.set_trace_callback(lambda statement: sql_threads.add(threading.get_ident()))
    summary = sync_history(connection, ak=client, workers=3,
                           initial_start_date=date(2026, 1, 1), end_date=date(2026, 1, 10), attempts=1)
    assert summary.success_count == 3
    assert client.thread_ids and main_thread not in client.thread_ids
    assert sql_threads == {main_thread}
    connection.close()


def test_normalization_standardizes_dates_numbers_and_order():
    from backend.collector.sync_a_share_history import StockItem

    raw = frame().iloc[::-1].copy()
    raw["volume"] = raw["volume"].astype(str)
    bars = normalize_history_frame(StockItem("000001", "one"), raw,
                                   pd.Timestamp("2026-01-10", tz="UTC").to_pydatetime())
    assert [item.trade_date.isoformat() for item in bars] == sorted(item.trade_date.isoformat() for item in bars)
    assert all(item.volume == 100 for item in bars)


def test_stock_universe_falls_back_to_complete_sw_constituents():
    class FallbackAk:
        def stock_info_a_code_name(self):
            raise TimeoutError("primary unavailable")

        def index_realtime_sw(self, symbol):
            return pd.DataFrame({"指数代码": ["801010", "801020"]})

        def index_component_sw(self, symbol):
            code = "000001" if symbol == "801010" else "600000"
            return pd.DataFrame({"证券代码": [code], "证券名称": [f"stock-{code}"]})

    assert [stock.code for stock in load_stock_universe(FallbackAk())] == ["000001", "600000"]
