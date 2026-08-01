import sqlite3
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / "database/migrations/023_current_sw_industry_snapshot.sql"


def test_current_industry_migration_creates_only_required_tables_and_indexes(tmp_path):
    connection = sqlite3.connect(tmp_path / "snapshot.db")
    connection.execute("CREATE TABLE existing_table(id INTEGER PRIMARY KEY)")
    sql = MIGRATION.read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"existing_table", "industry_nodes", "industry_memberships_current"} <= tables
    assert "industry_membership_conflicts" not in tables
    indexes = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    assert {"idx_industry_nodes_level", "idx_industry_nodes_parent",
            "idx_industry_memberships_l1", "idx_industry_memberships_l2",
            "idx_industry_memberships_l3"} <= indexes
