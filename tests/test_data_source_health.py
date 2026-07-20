from http.client import RemoteDisconnected

import pandas as pd

from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate
from backend.sector_radar import health_checks
from backend.sector_radar.health_repository import (
    ensure_source,
    get_status,
    list_statuses,
    record_degraded,
    record_failure,
    record_success,
)


def test_repository_lifecycle_counters_metadata_and_error_limit(tmp_path):
    connection = connect(tmp_path / "health.db")
    migrate(connection)
    ensure_source(connection, "sw_l1")
    initial = get_status(connection, "sw_l1")
    assert initial["status"] == "unknown"

    record_failure(connection, "sw_l1", error_type="ConnectionError", error_message="x" * 1200, latency_ms=50)
    failed = get_status(connection, "sw_l1")
    assert failed["status"] == "unavailable"
    assert failed["consecutive_failures"] == 1
    assert failed["total_failures"] == 1
    assert len(failed["last_error_message"]) == 1000

    record_success(connection, "sw_l1", latency_ms=12.5, metadata={"sector_count": 31, "fields": ["指数代码"]})
    healthy = get_status(connection, "sw_l1")
    assert healthy["status"] == "healthy"
    assert healthy["consecutive_failures"] == 0
    assert healthy["total_successes"] == 1
    assert healthy["last_success_at"]
    assert healthy["metadata"]["sector_count"] == 31

    last_success = healthy["last_success_at"]
    record_degraded(connection, "sw_l1", error_type="SchemaError", error_message="missing", latency_ms=20)
    degraded = get_status(connection, "sw_l1")
    assert degraded["status"] == "degraded"
    assert degraded["consecutive_failures"] == 1
    assert degraded["total_failures"] == 2
    assert degraded["last_success_at"] == last_success

    record_failure(connection, "sw_l1", error_type="Timeout", error_message="down", latency_ms=30)
    unavailable = get_status(connection, "sw_l1")
    assert unavailable["consecutive_failures"] == 2
    assert unavailable["last_success_at"] == last_success
    ensure_source(connection, "sw_l1")
    assert connection.execute("SELECT COUNT(*) FROM sector_source_status WHERE source='sw_l1'").fetchone()[0] == 1
    connection.close()


def test_list_statuses_registers_three_unknown_sources(tmp_path):
    connection = connect(tmp_path / "health.db")
    migrate(connection)
    items = list_statuses(connection)
    assert [item["source"] for item in items] == ["sw_l1", "sw_l2", "eastmoney"]
    assert all(item["status"] == "unknown" for item in items)
    connection.close()


def test_legacy_pr2_status_is_mapped_without_losing_last_success(tmp_path):
    connection = connect(tmp_path / "legacy.db")
    migration = PROJECT_ROOT / "database" / "migrations" / "004_sector_scores.sql"
    connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute(
        """INSERT INTO sector_source_status(source,status,sector_count,successful_sector_count,
           failed_sector_count,last_attempt_at,last_success_at,elapsed_seconds,updated_at)
           VALUES('sw_l1','available',31,31,0,'2026-07-20T01:00:00+00:00',
                  '2026-07-20T01:00:00+00:00',1.5,'2026-07-20T01:00:00+00:00')"""
    )
    connection.commit()
    migrate(connection)
    row = get_status(connection, "sw_l1")
    assert row["status"] == "healthy"
    assert row["last_success_at"] == "2026-07-20T01:00:00+00:00"
    assert row["sector_count"] == 31
    connection.close()


class HealthyAk:
    def index_realtime_sw(self, symbol):
        return pd.DataFrame({"指数代码": ["801010"], "指数名称": [symbol]})

    def stock_board_industry_name_em(self):
        return pd.DataFrame({"板块代码": ["BK001"], "板块名称": ["行业"]})


def test_lightweight_checks_success_empty_and_missing_data(monkeypatch):
    healthy = health_checks.check_one(HealthyAk(), "sw_l1")
    assert healthy.status == "healthy" and healthy.sector_count == 1

    empty_ak = HealthyAk()
    empty_ak.index_realtime_sw = lambda symbol: pd.DataFrame()
    assert health_checks.check_one(empty_ak, "sw_l1").status == "degraded"

    missing = HealthyAk()
    missing.index_realtime_sw = lambda symbol: (_ for _ in ()).throw(KeyError("data"))
    outcome = health_checks.check_one(missing, "sw_l2")
    assert outcome.status == "unavailable"
    assert "missing data field" in outcome.error_message
    assert "HTTP 507" not in outcome.error_message


def test_eastmoney_remote_disconnect_and_all_isolation(tmp_path, monkeypatch):
    class MixedAk(HealthyAk):
        def index_realtime_sw(self, symbol):
            if symbol == "二级行业":
                raise KeyError("data")
            return super().index_realtime_sw(symbol)

        def stock_board_industry_name_em(self):
            raise RemoteDisconnected("remote closed")

    monkeypatch.setattr(health_checks.time, "sleep", lambda _: None)
    connection = connect(tmp_path / "health.db")
    migrate(connection)
    items = health_checks.run_health_checks(connection, "all", ak=MixedAk())
    states = {item["source"]: item["status"] for item in items}
    assert states == {"sw_l1": "healthy", "sw_l2": "unavailable", "eastmoney": "unavailable"}
    assert "RemoteDisconnected" in get_status(connection, "eastmoney")["last_error_message"]
    connection.close()


def test_degraded_when_required_fields_are_missing():
    bad = HealthyAk()
    bad.index_realtime_sw = lambda symbol: pd.DataFrame({"other": [1]})
    result = health_checks.check_one(bad, "sw_l1")
    assert result.status == "degraded"
    assert result.error_type == "SchemaError"
