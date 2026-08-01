import sqlite3
from pathlib import Path

import pytest

from backend.data_sources.industry_sync.repository import IndustryRepository
from backend.data_sources.models import IndustryMembership, IndustryNode

MIGRATION = Path(__file__).resolve().parents[2] / "database/migrations/023_current_sw_industry_snapshot.sql"


def database(tmp_path):
    connection = sqlite3.connect(tmp_path / "repo.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    return connection


def snapshot(suffix=""):
    nodes = [
        IndustryNode("SW", "2021", "801000", "一级" + suffix, 1, None, "fixture"),
        IndustryNode("SW", "2021", "801010", "二级" + suffix, 2, "801000", "fixture"),
        IndustryNode("SW", "2021", "850111", "三级" + suffix, 3, "801010", "fixture"),
    ]
    memberships = [IndustryMembership(
        "SW", "2021", "600519.SH", "样本", "801000", "一级" + suffix,
        "801010", "二级" + suffix, "850111", "三级" + suffix,
        None, None, True, "fixture")]
    return nodes, memberships


def test_repository_replaces_and_queries_all_levels(tmp_path):
    connection = database(tmp_path); repository = IndustryRepository(connection)
    nodes, memberships = snapshot()
    assert repository.replace_current_snapshot(nodes=nodes, memberships=memberships)
    assert [item.industry_level for item in repository.list_nodes()] == [1, 2, 3]
    assert repository.list_nodes(level=2, parent_code="801000")[0].industry_name == "二级"
    assert repository.get_symbol_membership("SHSE.600519").level3_code == "850111"
    for level, code in ((1, "801000"), (2, "801010"), (3, "850111.SI")):
        assert [item.symbol for item in repository.list_constituents(code, level=level)] == ["600519.SH"]


def test_idempotent_snapshot_keeps_timestamps_and_force_rewrites(tmp_path):
    connection = database(tmp_path); repository = IndustryRepository(connection)
    nodes, memberships = snapshot()
    assert repository.replace_current_snapshot(nodes=nodes, memberships=memberships)
    first = connection.execute("SELECT updated_at FROM industry_nodes LIMIT 1").fetchone()[0]
    assert not repository.replace_current_snapshot(nodes=nodes, memberships=memberships)
    assert connection.execute("SELECT updated_at FROM industry_nodes LIMIT 1").fetchone()[0] == first
    assert repository.replace_current_snapshot(nodes=nodes, memberships=memberships, force=True)


def test_atomic_failure_preserves_previous_nodes_and_memberships(tmp_path, monkeypatch):
    connection = database(tmp_path); repository = IndustryRepository(connection)
    old_nodes, old_memberships = snapshot(); repository.replace_current_snapshot(
        nodes=old_nodes, memberships=old_memberships)
    monkeypatch.setattr(repository, "_before_swap", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        repository.replace_current_snapshot(nodes=snapshot("新")[0], memberships=snapshot("新")[1])
    assert repository.get_symbol_membership("600519.SH").level1_name == "一级"
    assert repository.list_nodes(level=1)[0].industry_name == "一级"
