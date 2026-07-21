import threading
import time
from datetime import date

import pandas as pd
import pytest

import backend.collector.sync_a_share_daily_history as sync_module

from backend.collector.sync_a_share_daily_history import (
    DEFAULT_WORKERS, SOURCE, StockItem, SyncPlan, build_sync_plans, download_plan,
    execute_sync, load_stock_pool, normalize_a_share_code, normalize_download_frame,
    parse_options, resolve_workers,
)
from backend.expectation_gap.database import connect, migrate
from backend.market_data.a_share_daily_repository import (
    DailyBar, get_daily_bar_stats, get_sync_status, upsert_daily_bars, upsert_sync_failure,
)
from backend.market_data.sector_history_repository import SectorMember, replace_current_membership


NOW = "2026-07-22T00:00:00+00:00"


def database(tmp_path):
    connection = connect(tmp_path / "daily-sync.db")
    migrate(connection)
    return connection


def frame(start="2026-07-01", periods=5):
    return pd.DataFrame({
        "date": pd.date_range(start, periods=periods),
        "open": [10.0] * periods, "high": [12.0] * periods, "low": [9.0] * periods,
        "close": [11.0] * periods, "volume": [100.0] * periods, "amount": [1000.0] * periods,
    })


def plan(code="000001", start=date(2026, 7, 1), end=date(2026, 7, 22)):
    return SyncPlan(code, None, start, end, "initial", "explicit_initial_range", True)


def test_cli_modes_dates_workers_lookback_limit_and_environment():
    initial = parse_options(["--start-date", "2026-07-01", "--end-date", "2026-07-22"])
    assert initial.mode == "initial" and initial.workers == DEFAULT_WORKERS
    incremental = parse_options(["--incremental", "--lookback-days", "0", "--workers", "8"])
    assert incremental.mode == "incremental" and incremental.workers == 8
    assert resolve_workers(None, {"A_SHARE_HISTORY_WORKERS": "3"}) == 3
    for arguments in (
        [], ["--incremental", "--start-date", "2026-07-01"],
        ["--start-date", "2026-07-23", "--end-date", "2026-07-22"],
        ["--start-date", "2026-07-01", "--workers", "0"],
        ["--start-date", "2026-07-01", "--lookback-days", "31"],
        ["--start-date", "2026-07-01", "--limit", "0"],
        ["--start-date", "2026-07-01", "--codes", "1", "--retry-failed"],
    ):
        with pytest.raises(SystemExit):
            parse_options(arguments)


def test_code_normalization_accepts_documented_forms_and_stably_deduplicates(tmp_path):
    assert [normalize_a_share_code(value) for value in
            (1, "1", "000001", "sz000001", "600000.SH", "000001.SZ")] == [
                "000001", "000001", "000001", "000001", "600000", "000001",
            ]
    connection = database(tmp_path)
    stocks = load_stock_pool(connection, codes=["600000.SH", 1, "000001", "sh600000"])
    assert [stock.code for stock in stocks] == ["000001", "600000"]
    with pytest.raises(ValueError, match="unsupported"):
        load_stock_pool(connection, codes=["700001"])
    connection.close()


def test_default_pool_reads_only_current_sw_level1_snapshot_and_deduplicates(tmp_path):
    connection = database(tmp_path)
    replace_current_membership(connection, "801010", [
        SectorMember("801010", "000001", "one", 1, "2026-07-22"),
        SectorMember("801010", "600000", "six", 1, "2026-07-22"),
    ], "2026-07-22", NOW)
    replace_current_membership(connection, "801030", [
        SectorMember("801030", "000001", "one", 1, "2026-07-22"),
    ], "2026-07-22", NOW)
    assert [item.code for item in load_stock_pool(connection)] == ["000001", "600000"]
    assert [item.code for item in load_stock_pool(connection, limit=1)] == ["000001"]
    connection.execute("UPDATE sector_memberships SET is_current=0")
    connection.commit()
    with pytest.raises(RuntimeError, match="empty"):
        load_stock_pool(connection)
    connection.close()


def test_retry_failed_selects_only_current_failures_and_recovered_is_removed(tmp_path):
    connection = database(tmp_path)
    upsert_sync_failure(connection, "000002", "two", SOURCE, "none", "failed", NOW)
    upsert_sync_failure(connection, "000001", "one", SOURCE, "none", "failed", NOW)
    assert [item.code for item in load_stock_pool(connection, retry_failed=True)] == ["000001", "000002"]
    bars = [DailyBar("000001", "2026-07-01", 10, 12, 9, 11, 100, 1000, SOURCE, "none", NOW)]
    result = execute_sync(connection, [plan("000001")], workers=1,
                          downloader=lambda *_: frame(periods=1), attempts=1)
    assert result.successful_stocks == 1
    assert [item.code for item in load_stock_pool(connection, retry_failed=True)] == ["000002"]
    connection.close()


def test_initial_and_incremental_planning_lookback_needs_initialization_and_up_to_date(tmp_path):
    connection = database(tmp_path)
    stocks = [StockItem("000001"), StockItem("000002")]
    initial = build_sync_plans(connection, stocks, mode="initial", start_date=date(2026, 7, 1),
                               end_date=date(2026, 7, 22))
    assert all(item.start_date == date(2026, 7, 1) and item.should_download for item in initial)
    upsert_daily_bars(connection, [
        DailyBar("000001", "2026-07-20", 10, 12, 9, 11, 100, 1000, SOURCE, "none", NOW)
    ])
    incremental = build_sync_plans(connection, stocks, mode="incremental", start_date=None,
                                   end_date=date(2026, 7, 22), lookback_days=7)
    assert incremental[0].start_date == date(2026, 7, 13)
    assert incremental[1].reason == "skipped_needs_initialization" and not incremental[1].should_download
    current = build_sync_plans(connection, [StockItem("000001")], mode="incremental", start_date=None,
                               end_date=date(2026, 7, 20), lookback_days=0)
    assert current[0].reason == "skipped_up_to_date"
    connection.close()


def test_normalization_filters_range_deduplicates_last_and_accepts_zero_volume():
    raw = frame("2026-06-30", 4)
    duplicate = raw.iloc[[2]].copy()
    duplicate.loc[:, "close"] = 10.5
    combined = pd.concat([raw, duplicate], ignore_index=True)
    combined.loc[combined["date"] == pd.Timestamp("2026-07-02"), "volume"] = 0
    result = normalize_download_frame(
        "000001", combined, date(2026, 7, 1), date(2026, 7, 3),
        pd.Timestamp(NOW).to_pydatetime(),
    )
    assert result.downloaded_rows == 5 and result.rejected_rows == 0
    assert len(result.bars) == 3
    assert [bar.trade_date.isoformat() for bar in result.bars] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert result.bars[1].close == 10.5 and result.bars[1].volume == 0


@pytest.mark.parametrize("field,value", [
    ("open", float("nan")), ("close", float("inf")), ("volume", -1), ("amount", -1),
])
def test_normalization_rejects_invalid_numbers(field, value):
    raw = frame(periods=2)
    raw.loc[0, field] = value
    result = normalize_download_frame(
        "000001", raw, date(2026, 7, 1), date(2026, 7, 2), pd.Timestamp(NOW).to_pydatetime(),
    )
    assert len(result.bars) == 1 and result.rejected_rows == 1


def test_normalization_rejects_invalid_ohlc_and_missing_columns():
    raw = frame(periods=2)
    raw.loc[0, "high"] = 1
    result = normalize_download_frame(
        "000001", raw, date(2026, 7, 1), date(2026, 7, 2), pd.Timestamp(NOW).to_pydatetime(),
    )
    assert len(result.bars) == 1 and result.rejected_rows == 1
    with pytest.raises(ValueError, match="missing required column"):
        normalize_download_frame("000001", raw.drop(columns="close"), date(2026, 7, 1),
                                 date(2026, 7, 2), pd.Timestamp(NOW).to_pydatetime())


def test_empty_is_no_data_but_abnormal_empty_retries_with_backoff():
    empty = download_plan(plan(), downloader=lambda *_: pd.DataFrame(), attempts=3,
                          sleep=lambda _: pytest.fail("normal empty must not retry"))
    assert empty.status == "no_data"
    sleeps = []
    invalid = frame(periods=1).drop(columns="close")
    failed = download_plan(plan(), downloader=lambda *_: invalid, attempts=3, sleep=sleeps.append)
    assert failed.status == "failed" and sleeps == [1.0, 2.0]
    assert "missing required column close" in failed.error


def test_dry_run_does_not_download_or_write(tmp_path):
    connection = database(tmp_path)
    called = []
    summary = execute_sync(connection, [plan()], workers=1, dry_run=True,
                           downloader=lambda *_: called.append(True))
    assert summary.planned_stocks == 1 and summary.affected_rows == 0 and called == []
    assert connection.execute("SELECT COUNT(*) FROM a_share_daily_bars").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM a_share_history_sync_status").fetchone()[0] == 0
    connection.close()


def test_default_downloader_serializes_first_sina_initialization(monkeypatch):
    calls = []
    first_finished = threading.Event()

    def fake_download(code, *_):
        calls.append((code, "start", first_finished.is_set()))
        if code == "000001":
            time.sleep(0.05)
            first_finished.set()
        calls.append((code, "end", first_finished.is_set()))
        return frame(periods=1)

    monkeypatch.setattr(sync_module, "_download_from_sina", fake_download)
    monkeypatch.setattr(sync_module, "_sina_initialized", False)
    with sync_module.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            sync_module.default_downloader, "000001", date(2026, 7, 1), date(2026, 7, 2)
        )
        time.sleep(0.01)
        second = executor.submit(
            sync_module.default_downloader, "600000", date(2026, 7, 1), date(2026, 7, 2)
        )
        first.result()
        second.result()

    second_start = next(item for item in calls if item[:2] == ("600000", "start"))
    assert second_start[2] is True


def test_concurrency_failure_isolation_main_thread_sql_and_stable_counts(tmp_path):
    connection = database(tmp_path)
    main_thread = threading.get_ident()
    download_threads = set()
    sql_threads = set()
    connection.set_trace_callback(lambda statement: sql_threads.add(threading.get_ident()))

    def downloader(code, *_):
        download_threads.add(threading.get_ident())
        if code == "000002":
            raise ConnectionError("down")
        return frame(periods=2)

    plans = [plan("000003"), plan("000001"), plan("000002")]
    result = execute_sync(connection, plans, workers=3, downloader=downloader, attempts=1)
    assert result.successful_stocks == 2 and result.failed_stocks == 1
    assert result.downloaded_rows == result.accepted_rows == result.affected_rows == 4
    assert result.failures[0][0] == "000002"
    assert download_threads and main_thread not in download_threads
    assert sql_threads == {main_thread}
    assert get_sync_status(connection, "000002").consecutive_failures == 1
    connection.close()


def test_idempotent_repeat_incremental_overlap_and_status_recovery(tmp_path):
    connection = database(tmp_path)
    first = execute_sync(connection, [plan()], workers=1, downloader=lambda *_: frame(periods=5), attempts=1)
    second = execute_sync(connection, [plan()], workers=1, downloader=lambda *_: frame(periods=5), attempts=1)
    assert first.successful_stocks == second.successful_stocks == 1
    assert get_daily_bar_stats(connection, "000001")[2] == 5
    status = get_sync_status(connection, "000001")
    assert status.last_error is None and status.consecutive_failures == 0 and status.source == SOURCE

    failed = execute_sync(connection, [plan()], workers=1,
                          downloader=lambda *_: (_ for _ in ()).throw(ConnectionError("temporary")), attempts=1)
    assert failed.failed_stocks == 1
    failed_status = get_sync_status(connection, "000001")
    assert failed_status.first_trade_date == status.first_trade_date
    assert failed_status.last_success_at == status.last_success_at
    recovered = execute_sync(connection, [plan()], workers=1, downloader=lambda *_: frame(periods=5), attempts=1)
    assert recovered.successful_stocks == 1 and get_sync_status(connection, "000001").consecutive_failures == 0
    connection.close()


def test_no_data_clears_current_failure_without_deleting_success_range(tmp_path):
    connection = database(tmp_path)
    execute_sync(connection, [plan()], workers=1, downloader=lambda *_: frame(periods=2), attempts=1)
    before = get_sync_status(connection, "000001")
    upsert_sync_failure(connection, "000001", None, SOURCE, "none", "temporary", NOW)
    result = execute_sync(connection, [plan()], workers=1, downloader=lambda *_: pd.DataFrame(), attempts=1)
    after = get_sync_status(connection, "000001")
    assert result.no_data_stocks == 1 and after.consecutive_failures == 0 and after.last_error is None
    assert (after.first_trade_date, after.last_trade_date, after.last_success_at) == (
        before.first_trade_date, before.last_trade_date, before.last_success_at,
    )
    connection.close()
