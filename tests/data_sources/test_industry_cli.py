from datetime import datetime, timezone

from backend.data_sources import cli
from backend.data_sources.industry_sync.models import (
    IndustryMembershipConflict, IndustrySyncResult,
)
from backend.data_sources.models import IndustryMembership


def membership():
    return IndustryMembership("SW", "2021", "600519.SH", None, "801000", "一级",
                              "801010", "二级", "850111", "三级",
                              None, None, True, "fixture")


def sync_result(status="success", conflicts=()):
    return IndustrySyncResult(
        status, "tushare", False, 3, 1, 1, 0, len(conflicts), len(conflicts),
        tuple(item.symbol for item in conflicts), tuple(conflicts), (), True, True, False,
    )


def test_sync_cli_passes_options_exports_conflicts_and_uses_exit_one(tmp_path, monkeypatch, capsys):
    conflict = IndustryMembershipConflict("600519.SH", (membership(),))
    captured = {}
    provider_object = object()
    def build(**kwargs):
        captured["provider_options"] = kwargs
        return provider_object
    monkeypatch.setattr(cli, "build_industry_provider", build)
    monkeypatch.setattr(cli, "_dry_run_repository", lambda: (object(), None))
    def fake_sync(**kwargs):
        captured.update(kwargs)
        return sync_result("partial_success", (conflict,))
    monkeypatch.setattr(cli, "sync_current_industries", fake_sync)
    output = tmp_path / "conflicts.json"
    code = cli.main(["sync-industries", "--dry-run", "--force", "--provider", "tushare",
                     "--export-conflicts", str(output)])
    assert code == 1 and captured["dry_run"] and captured["force"]
    assert captured["provider_options"] == {"provider": "tushare"}
    assert captured["provider"] is provider_object
    assert "600519.SH" in output.read_text(encoding="utf-8")
    assert '"status": "partial_success"' in capsys.readouterr().out


class Connection:
    def close(self): self.closed = True


class FakeRepository:
    def __init__(self, connection): self.connection = connection
    def get_symbol_membership(self, symbol): return membership()
    def list_constituents(self, code, *, level): return [membership()]


def test_database_query_cli_commands_are_read_only(monkeypatch, capsys):
    connections = []
    def readonly():
        item = Connection(); connections.append(item); return item
    monkeypatch.setattr(cli, "connect_readonly", readonly)
    monkeypatch.setattr(cli, "IndustryRepository", FakeRepository)
    assert cli.main(["db-symbol-industry", "--symbol", "600519.SH"]) == 0
    assert "850111" in capsys.readouterr().out
    assert cli.main(["db-industry-constituents", "--industry-code", "850111.SI",
                     "--level", "3", "--limit", "1"]) == 0
    assert "600519.SH" in capsys.readouterr().out
    assert all(item.closed for item in connections)


def test_database_query_cli_returns_two_when_database_is_missing(monkeypatch):
    monkeypatch.setattr(cli, "connect_readonly", lambda: (_ for _ in ()).throw(FileNotFoundError("missing")))
    assert cli.main(["db-symbol-industry", "--symbol", "600519.SH"]) == 2
