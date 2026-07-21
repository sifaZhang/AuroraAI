import threading
from datetime import date

import pandas as pd

from backend.collector.sync_sector_history import (
    MAX_WORKERS, RateLimiter, bounded_workers, load_sw_level1_sectors,
    normalize_current_members, normalize_sector_history, sync_sw_level1,
)
from backend.expectation_gap.database import connect, migrate
from backend.market_data.sector_history_repository import (
    CLASSIFICATION_SYSTEM, LOOKAHEAD_WARNING, Sector, SectorDailyBar,
    SectorMember, list_failed_sector_codes, replace_current_membership,
    sector_bar_stats, upsert_sector_bars,
)


def history_frame(dates=("2026-07-20", "2026-07-21")):
    return pd.DataFrame([
        ["801010", day, 10, 11, 12, 9, 100, 1000] for day in dates
    ], columns=["code", "date", "open", "close", "high", "low", "volume", "amount"])


def member_frame(codes=("000001", "600000")):
    return pd.DataFrame([
        [index + 1, code, f"stock-{code}", index + 0.5, "2021-12-13"]
        for index, code in enumerate(codes)
    ], columns=["seq", "code", "name", "weight", "included_at"])


class FakeAk:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []
        self.thread_ids = set()

    def index_realtime_sw(self, symbol):
        assert symbol == "一级行业"
        return pd.DataFrame([["801010", "Agriculture", 1], ["801030", "Chemicals", 2]],
                            columns=["code", "name", "price"])

    def index_hist_sw(self, symbol, period):
        self.calls.append(("history", symbol, period))
        self.thread_ids.add(threading.get_ident())
        if symbol in self.failures:
            raise ConnectionError("history failed")
        frame = history_frame()
        frame.iloc[:, 0] = symbol
        return frame

    def index_component_sw(self, symbol):
        self.calls.append(("members", symbol))
        self.thread_ids.add(threading.get_ident())
        return member_frame()


def database(tmp_path):
    connection = connect(tmp_path / "sector-history.db")
    migrate(connection)
    return connection


def test_schema_and_repository_are_multisource_ready_and_idempotent(tmp_path):
    connection = database(tmp_path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sector_industries", "sector_daily_bars", "sector_memberships",
            "sector_history_sync_status"} <= tables
    bar = SectorDailyBar("801010", "2026-07-21", 10, 12, 9, 11, 100, 1000)
    assert upsert_sector_bars(connection, [bar, bar]) == 1
    assert upsert_sector_bars(connection, [bar]) == 1
    assert sector_bar_stats(connection, "801010") == ("2026-07-21", "2026-07-21", 1)
    pk = [row[1] for row in connection.execute("PRAGMA table_info(sector_daily_bars)") if row[5]]
    assert pk == ["classification_system", "sector_code", "trade_date"]
    connection.close()


def test_level1_list_and_frames_use_real_field_positions_and_validate():
    client = FakeAk()
    sectors = load_sw_level1_sectors(client)
    assert [(item.classification_system, item.sector_level, item.sector_code)
            for item in sectors] == [("sw_level1", 1, "801010"), ("sw_level1", 1, "801030")]
    bars = normalize_sector_history(sectors[0], history_frame(), pd.Timestamp.now(tz="UTC").to_pydatetime(),
                                    date(2026, 7, 21))
    assert len(bars) == 1 and bars[0].trade_date == date(2026, 7, 21)
    members = normalize_current_members(sectors[0], member_frame(), date(2026, 7, 22))
    assert len(members) == 2 and all(item.snapshot_date == date(2026, 7, 22) for item in members)


def test_initial_incremental_repeat_failure_isolation_and_recovery(tmp_path):
    connection = database(tmp_path)
    failing = FakeAk(failures=("801030",))
    first = sync_sw_level1(connection, ak=failing, workers=2, attempts=1,
                           request_interval=0, snapshot_date=date(2026, 7, 22))
    assert (first.total, first.success_count, first.failure_count) == (2, 1, 1)
    assert sector_bar_stats(connection, "801010")[2] == 2
    assert list_failed_sector_codes(connection) == ["801030"]
    failure = connection.execute(
        "SELECT status,consecutive_failures,last_error FROM sector_history_sync_status WHERE sector_code='801030'"
    ).fetchone()
    assert failure[0] == "failed" and failure[1] == 1 and "history failed" in failure[2]

    recovered = FakeAk()
    retry = sync_sw_level1(connection, ak=recovered, retry_failed=True, attempts=1,
                           request_interval=0, snapshot_date=date(2026, 7, 22))
    assert retry.total == retry.success_count == 1
    assert {call[1] for call in recovered.calls} == {"801030"}
    assert list_failed_sector_codes(connection) == []

    repeated = sync_sw_level1(connection, ak=FakeAk(), codes=("801010",), attempts=1,
                              request_interval=0, snapshot_date=date(2026, 7, 22))
    assert repeated.success_count == 1 and repeated.downloaded_bars == 0
    assert sector_bar_stats(connection, "801010")[2] == 2
    connection.close()


def test_membership_is_current_snapshot_and_removed_member_is_retained_as_inactive(tmp_path):
    connection = database(tmp_path)
    sector = Sector("801010", "Agriculture")
    first = [SectorMember("801010", code, code, 1, "2026-07-21") for code in ("000001", "600000")]
    replace_current_membership(connection, "801010", first, "2026-07-21", "2026-07-21T00:00:00Z")
    second = [SectorMember("801010", "000001", "one", 2, "2026-07-22")]
    replace_current_membership(connection, "801010", second, "2026-07-22", "2026-07-22T00:00:00Z")
    rows = connection.execute(
        """SELECT stock_code,is_current,snapshot_date,membership_scope,
                  historical_use_is_approximate,lookahead_bias_warning,first_seen_at,last_seen_at
           FROM sector_memberships ORDER BY stock_code"""
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("000001", 1), ("600000", 0)]
    assert rows[0][2:5] == ("2026-07-22", "current_snapshot", 1)
    assert rows[0][5] == LOOKAHEAD_WARNING and rows[0][6] < rows[0][7]
    connection.close()


def test_download_workers_never_touch_sqlite_and_limits_are_bounded(tmp_path):
    connection = database(tmp_path)
    main_thread = threading.get_ident()
    sql_threads = set()
    connection.set_trace_callback(lambda statement: sql_threads.add(threading.get_ident()))
    client = FakeAk()
    result = sync_sw_level1(connection, ak=client, workers=8, attempts=1,
                            request_interval=0, snapshot_date=date(2026, 7, 22))
    assert result.success_count == 2
    assert client.thread_ids and main_thread not in client.thread_ids
    assert sql_threads == {main_thread}
    assert bounded_workers(8) == MAX_WORKERS and bounded_workers(9) < 9
    assert RateLimiter(-1).interval == 0
    connection.close()


def test_empty_or_invalid_upstream_data_isolated(tmp_path):
    class EmptyAk(FakeAk):
        def index_component_sw(self, symbol):
            return pd.DataFrame()

    connection = database(tmp_path)
    result = sync_sw_level1(connection, ak=EmptyAk(), limit=1, attempts=1, request_interval=0)
    assert result.failure_count == 1
    assert connection.execute("SELECT COUNT(*) FROM sector_daily_bars").fetchone()[0] == 0
    assert "no_membership_data" in list(connection.execute(
        "SELECT last_error FROM sector_history_sync_status"))[0][0]
    connection.close()
