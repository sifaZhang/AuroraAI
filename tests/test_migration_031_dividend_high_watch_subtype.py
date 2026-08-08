from __future__ import annotations

import sqlite3

import pytest

from backend.expectation_gap.database import (
    DIVIDEND_HIGH_WATCH_SUBTYPE_MIGRATION_PATH,
    DIVIDEND_STABLE_UNIVERSE_MIGRATION_PATH,
    connect,
    migrate,
)


def _snapshot(connection):
    rows = [tuple(row) for row in connection.execute(
        "SELECT * FROM dividend_stable_universe ORDER BY market,symbol"
    )]
    return {
        "rows": rows,
        "row_count": len(rows),
        "enabled_count": connection.execute(
            "SELECT COUNT(*) FROM dividend_stable_universe WHERE is_enabled=1"
        ).fetchone()[0],
        "types": [tuple(row) for row in connection.execute(
            "SELECT stability_subtype,COUNT(*) FROM dividend_stable_universe GROUP BY stability_subtype ORDER BY stability_subtype"
        )],
    }


def _insert(connection, symbol, subtype):
    connection.execute(
        """INSERT INTO dividend_stable_universe(
               market,symbol,company_name,monopoly_type,stability_subtype,
               inclusion_source,included_at,updated_at
           ) VALUES('CN',?,?,?,?,?,'2026-01-01','2026-01-01')""",
        (symbol, symbol, subtype, subtype, "manual_review"),
    )


def test_031_preserves_rows_and_expands_only_subtype_check(tmp_path):
    connection = sqlite3.connect(tmp_path / "031-legacy.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(DIVIDEND_STABLE_UNIVERSE_MIGRATION_PATH.read_text(encoding="utf-8"))
    _insert(connection, "600001.SH", "stable_monopoly")
    _insert(connection, "600002.SH", "resource_monopoly_cyclical")
    connection.commit()
    before = _snapshot(connection)

    connection.executescript(DIVIDEND_HIGH_WATCH_SUBTYPE_MIGRATION_PATH.read_text(encoding="utf-8"))
    after = _snapshot(connection)
    assert after == before
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    _insert(connection, "600003.SH", "high_dividend_watch")
    connection.commit()
    assert connection.execute(
        "SELECT stability_subtype FROM dividend_stable_universe WHERE symbol='600003.SH'"
    ).fetchone()[0] == "high_dividend_watch"
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert(connection, "600004.SH", "invalid_type")
    connection.rollback()
    connection.close()


def test_031_is_guarded_by_project_migration_runner(tmp_path):
    connection = connect(tmp_path / "031-runner.db")
    migrate(connection)
    migrate(connection)
    assert "high_dividend_watch" in connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dividend_stable_universe'"
    ).fetchone()[0]
    connection.close()
