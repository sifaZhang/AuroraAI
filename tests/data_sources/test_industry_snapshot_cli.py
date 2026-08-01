from datetime import date

from backend.data_sources import cli
from backend.data_sources.industry_snapshots.models import (
    IndustrySnapshotBuildResult, IndustrySnapshotRangeResult,
)


class Connection:
    def close(self):
        self.closed = True


def result(*, partial=0, failed=0):
    return IndustrySnapshotBuildResult(
        date(2026, 7, 31), 3, 3 - partial, partial, failed, 0, 3,
        True, False, True,
    )


def test_build_cli_date_level_dry_run_and_exit_codes(monkeypatch, capsys):
    connection = Connection()
    captured = {}
    monkeypatch.setattr(cli, "connect_readonly", lambda: connection)

    def build(**kwargs):
        captured.update(kwargs)
        return result(partial=1)

    monkeypatch.setattr(cli, "build_industry_daily_snapshots", build)
    code = cli.main([
        "build-industry-snapshots", "--date", "2026-07-31",
        "--level", "2", "--dry-run",
    ])
    assert code == 1
    assert captured["levels"] == (2,) and captured["dry_run"]
    assert connection.closed and '"trade_date": "2026-07-31"' in capsys.readouterr().out


def test_build_cli_range_force_uses_writable_connection(monkeypatch):
    connection = Connection()
    captured = {}
    monkeypatch.setattr(cli, "connect", lambda: connection)
    monkeypatch.setattr(cli, "migrate", lambda value: captured.setdefault("migrated", value))

    def build_range(**kwargs):
        captured.update(kwargs)
        return IndustrySnapshotRangeResult((result(),), (date(2026, 8, 1),))

    monkeypatch.setattr(cli, "build_industry_snapshot_range", build_range)
    code = cli.main([
        "build-industry-snapshots", "--start-date", "2026-07-31",
        "--end-date", "2026-08-01", "--force",
    ])
    assert code == 0 and captured["migrated"] is connection
    assert captured["levels"] == (1, 2, 3) and captured["force"]


class Repository:
    def __init__(self, connection):
        self.connection = connection

    def list_snapshots(self, trade_date, level):
        assert trade_date == date(2026, 7, 31) and level == 3
        return []


def test_snapshot_query_cli_is_read_only(monkeypatch, capsys):
    connection = Connection()
    monkeypatch.setattr(cli, "connect_readonly", lambda: connection)
    monkeypatch.setattr(cli, "IndustrySnapshotRepository", Repository)
    assert cli.main([
        "db-industry-snapshots", "--date", "2026-07-31", "--level", "3",
    ]) == 0
    assert capsys.readouterr().out.strip() == "[]"
    assert connection.closed
