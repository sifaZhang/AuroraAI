import sqlite3
from datetime import date
from pathlib import Path

import pytest

from backend.data_sources.industry_snapshots import (
    IndustrySnapshotRepository, build_industry_daily_snapshots,
    build_industry_snapshot_range,
)
from backend.data_sources.industry_snapshots import service as snapshot_service

ROOT = Path(__file__).resolve().parents[2]


def database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in (8, 11, 12, 13, 23, 24):
        path = next((ROOT / "database" / "migrations").glob(f"{migration:03d}_*.sql"))
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.execute(
        "ALTER TABLE a_share_security_master ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
    )
    return connection


def seed(connection, *, include_events=True):
    now = "2026-07-31T10:00:00+00:00"
    nodes = [
        ("801000", "L1", 1, None), ("801010", "L2", 2, "801000"),
        ("850111", "L3", 3, "801010"), ("850999", "EMPTY", 3, "801010"),
    ]
    connection.executemany(
        "INSERT INTO industry_nodes VALUES('SW','2021',?,?,?,?,?,?)",
        [(code, name, level, parent, "fixture", now) for code, name, level, parent in nodes],
    )
    symbols = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"]
    connection.executemany(
        """INSERT INTO industry_memberships_current VALUES(
             'SW','2021',?,'801000','L1','801010','L2','850111','L3','fixture',?)""",
        [(symbol, now) for symbol in symbols],
    )
    connection.executemany(
        """INSERT INTO a_share_security_master(
             symbol,stock_code,exchange,board_type,security_name,listed_date,delisted_date,
             source,quality_flags,updated_at,is_active) VALUES(?,?,'SH','MAIN',? ,?,NULL,'GM','[]',?,1)""",
        [(symbol, symbol[:6], symbol, "2020-01-01" if symbol != "600005.SH" else "2026-08-01", now)
         for symbol in symbols],
    )
    connection.executemany(
        """INSERT INTO a_share_security_status_history VALUES(
             ?,'2026-07-31','MAIN',0,?,0,?,NULL,'GM','[]',?)""",
        [(symbol, int(symbol == "600003.SH"),
          "2020-01-01" if symbol != "600005.SH" else "2026-08-01", now)
         for symbol in symbols],
    )
    connection.executemany(
        "INSERT INTO a_share_trading_calendar VALUES('CN',?,?,'GM','[]',?)",
        [("2026-07-30", 1, now), ("2026-07-31", 1, now),
         ("2026-08-01", 0, now), ("2026-08-02", 0, now), ("2026-08-03", 1, now)],
    )
    bars = [
        ("600001", 11.0, 100.0), ("600002", 9.0, 200.0),
        ("600003", 10.0, 300.0),
    ]
    connection.executemany(
        """INSERT INTO a_share_daily_bars VALUES(
             ?,'2026-07-31',10,11,9,?,100,?,'GM','none',?)""",
        [(code, close, amount, now) for code, close, amount in bars],
    )
    connection.executemany(
        "INSERT INTO first_limit_daily_metadata VALUES(?,'2026-07-31',10,11,9,'GM','[]',?)",
        [(symbol, now) for symbol in symbols[:4]],
    )
    if include_events:
        for index, symbol in enumerate(symbols[:4]):
            connection.execute(
                """INSERT INTO first_limit_events(
                   symbol,exchange,trade_date,detection_version,detection_status,
                   is_limit_up_close,touched_upper_limit,is_first_limit,lookback_trading_days,
                   observed_lookback_days,exclusion_reasons,quality_flags,detected_at,created_at,updated_at)
                   VALUES(?,'SH','2026-07-31','v1','detected',?,?,?,20,20,'[]','[]',?,?,?)""",
                (symbol, int(index == 0), int(index in (0, 1)), int(index == 0), now, now, now),
            )
    connection.commit()


def test_migration_is_idempotent_and_has_required_key_and_indexes():
    connection = database()
    migration = (ROOT / "database" / "migrations" / "024_industry_daily_snapshots.sql").read_text(encoding="utf-8")
    connection.executescript(migration)
    columns = {row[1]: row for row in connection.execute("PRAGMA table_info(industry_daily_snapshots)")}
    indexes = {row[1] for row in connection.execute("PRAGMA index_list(industry_daily_snapshots)")}
    assert columns["trade_date"][5] == 1
    assert columns["industry_code"][5] == 4
    assert {"idx_industry_daily_snapshots_date_level", "idx_industry_daily_snapshots_code_date"} <= indexes
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='industry_nodes'").fetchone()


def test_all_levels_share_aggregation_and_objective_metrics():
    connection = database(); seed(connection)
    result = build_industry_daily_snapshots(
        connection=connection, trade_date=date(2026, 7, 31), dry_run=True,
    )
    assert result.industry_count == 4 and result.snapshot_count == 4
    assert result.success_count == 0 and result.partial_count == 4
    assert connection.execute("SELECT COUNT(*) FROM industry_daily_snapshots").fetchone()[0] == 0
    # Recompute in write mode, then compare the same member set at all three levels.
    written = build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    assert written.changed
    snapshots = IndustrySnapshotRepository(connection).list_snapshots(date(2026, 7, 31))
    populated = [item for item in snapshots if item.industry_code != "850999"]
    assert len(populated) == 3
    for item in populated:
        assert (item.constituent_count, item.eligible_count, item.valid_bar_count,
                item.missing_bar_count, item.suspended_count) == (5, 4, 3, 1, 1)
        assert item.coverage_ratio == 0.75
        assert item.equal_weight_return == pytest.approx(0.0)
        assert item.median_return == pytest.approx(0.0)
        assert (item.rise_count, item.fall_count, item.flat_count) == (1, 1, 1)
        assert item.strong_rise_count == 1
        assert (item.limit_up_count, item.limit_down_count) == (1, 1)
        assert (item.first_limit_count, item.broken_limit_count) == (1, 1)
        assert item.turnover_amount == 600.0 and item.median_turnover_amount == 200.0
        assert item.data_status == "partial"
    empty = next(item for item in snapshots if item.industry_code == "850999")
    assert empty.data_status == "empty" and empty.coverage_ratio == 0
    assert empty.equal_weight_return is None


def test_incomplete_event_coverage_is_null_not_zero():
    connection = database(); seed(connection, include_events=False)
    build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    item = IndustrySnapshotRepository(connection).get_snapshot(date(2026, 7, 31), "850111")
    assert item.first_limit_count is None and item.broken_limit_count is None
    assert '"first_limit_capable": false' in item.source_snapshot


def test_small_return_sample_is_insufficient_without_division_error():
    connection = database(); seed(connection)
    connection.execute("DELETE FROM a_share_daily_bars WHERE stock_code IN ('600002','600003')")
    build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    item = IndustrySnapshotRepository(connection).get_snapshot(date(2026, 7, 31), "850111")
    assert item.data_status == "insufficient"
    assert item.valid_bar_count == 1 and item.coverage_ratio == 0.25
    assert item.equal_weight_return is None and item.median_return is None


def test_idempotency_force_query_and_date_range_calendar():
    connection = database(); seed(connection)
    first = build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    second = build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    forced = build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31), force=True)
    assert first.changed and not second.changed and forced.changed and forced.forced
    repository = IndustrySnapshotRepository(connection)
    assert len(repository.list_snapshots(date(2026, 7, 31), 2)) == 1
    assert len(repository.list_industry_history("801010", date(2026, 7, 1), date(2026, 8, 1))) == 1
    ranged = build_industry_snapshot_range(
        connection=connection, start_date=date(2026, 7, 31), end_date=date(2026, 8, 3),
        levels=(1,), dry_run=True,
    )
    assert tuple(item.trade_date for item in ranged.results) == (date(2026, 7, 31), date(2026, 8, 3))
    assert ranged.skipped_dates == (date(2026, 8, 1), date(2026, 8, 2))


def test_direct_non_trading_day_is_skipped_without_snapshot():
    connection = database(); seed(connection)
    result = build_industry_daily_snapshots(
        connection=connection, trade_date=date(2026, 8, 1), dry_run=True,
    )
    assert result.skipped_count == 1 and result.snapshot_count == 0
    assert result.warnings == ("non_trading_day",)


def test_one_industry_failure_is_isolated_and_does_not_delete_old_snapshot(monkeypatch):
    connection = database(); seed(connection)
    build_industry_daily_snapshots(connection=connection, trade_date=date(2026, 7, 31))
    original = snapshot_service._snapshot

    def fail_one(node, *args, **kwargs):
        if node["industry_code"] == "801010":
            raise ValueError("fixture failure")
        return original(node, *args, **kwargs)

    monkeypatch.setattr(snapshot_service, "_snapshot", fail_one)
    result = build_industry_daily_snapshots(
        connection=connection, trade_date=date(2026, 7, 31), force=True,
    )
    assert result.failed_count == 1 and result.snapshot_count == 3
    assert any(warning.startswith("801010: ValueError") for warning in result.warnings)
    assert IndustrySnapshotRepository(connection).get_snapshot(date(2026, 7, 31), "801010")
