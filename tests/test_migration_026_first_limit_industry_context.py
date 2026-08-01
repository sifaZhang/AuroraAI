import sqlite3
from pathlib import Path

from backend.expectation_gap.database import connect, migrate


FIELDS = {
    "sw_level1_code", "sw_level2_code", "sw_level3_code",
    "effective_industry_level", "effective_industry_code", "industry_context_status",
}
MIGRATION = Path(__file__).resolve().parents[1] / "database/migrations/026_first_limit_industry_context.sql"


def columns(connection):
    return {row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_snapshots)")}


def test_fresh_database_migrates_through_026_and_repeat_is_guarded(tmp_path):
    connection = connect(tmp_path / "fresh-026.db")
    migrate(connection)
    assert FIELDS <= columns(connection)
    migrate(connection)
    assert FIELDS <= columns(connection)


def test_026_upgrades_old_candidate_and_preserves_actual_sentinel_tables():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE daily_candidate_snapshots(id INTEGER PRIMARY KEY, symbol TEXT, score NUMERIC)"
    )
    connection.execute("INSERT INTO daily_candidate_snapshots VALUES(1,'000001.SZ',88)")
    sentinels = {
        "first_limit_events": ("id INTEGER PRIMARY KEY, symbol TEXT", (7, "event")),
        "first_limit_minute_bars": ("symbol TEXT, bar_time TEXT", ("bar", "14:55")),
        "first_limit_sync_runs": ("run_id TEXT PRIMARY KEY, status TEXT", ("ledger", "success")),
        "backtest_parameter_results": ("run_id TEXT, metric_json TEXT", ("backtest", '{"return":1}')),
    }
    for table, (schema, values) in sentinels.items():
        connection.execute(f"CREATE TABLE {table}({schema})")
        connection.execute(
            f"INSERT INTO {table} VALUES({','.join('?' for _ in values)})", values
        )
    before = {
        table: tuple(connection.execute(f"SELECT * FROM {table}").fetchone())
        for table in sentinels
    }
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    assert FIELDS <= columns(connection)
    assert tuple(connection.execute(
        "SELECT id,symbol,score FROM daily_candidate_snapshots"
    ).fetchone()) == (1, "000001.SZ", 88)
    for table, expected in before.items():
        assert tuple(connection.execute(f"SELECT * FROM {table}").fetchone()) == expected
