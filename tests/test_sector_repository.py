from dataclasses import replace

from backend.collector.probe_sector_data import SectorTrend, SourceResult, SourceStatus
from backend.collector.collect_sector_scores import persist_results
from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.database import PROJECT_ROOT
from backend.sector_radar.health_repository import ensure_source, get_status, record_success


def result(source, status="available", trends=()):
    return SourceResult(
        SourceStatus(source, status, len(trends), len(trends), 0, None if trends else "upstream error", 1.25),
        tuple(trends),
    )


def trend(source="sw_l1", code="801010"):
    level = 1 if source == "sw_l1" else (2 if source == "sw_l2" else "industry")
    return SectorTrend(source, code, "行业", level, "2026-07-20", 70, "strong", 100, 95, 90, 80, 2.0, True)


def test_multi_source_scores_and_statuses_are_idempotent(tmp_path):
    connection = connect(tmp_path / "sector.db")
    migrate(connection)
    sw_l1_trends = tuple(trend(code=f"801{index:03d}") for index in range(31))
    results = [result("sw_l1", trends=sw_l1_trends), result("sw_l2", "unavailable"), result("eastmoney", "unavailable")]
    assert persist_results(connection, results) == 31
    assert persist_results(connection, results) == 31
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 31
    assert connection.execute("SELECT COUNT(*) FROM sector_source_status").fetchone()[0] == 3
    connection.close()


def test_unavailable_sources_write_status_but_no_fake_scores(tmp_path):
    connection = connect(tmp_path / "sector.db")
    migrate(connection)
    persist_results(connection, [result("sw_l2", "unavailable"), result("eastmoney", "unavailable")])
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 0
    rows = connection.execute("SELECT source,status,last_error FROM sector_source_status ORDER BY source").fetchall()
    assert [(row["source"], row["status"]) for row in rows] == [("eastmoney", "unavailable"), ("sw_l2", "unavailable")]
    assert all(row["last_error"] == "upstream error" for row in rows)
    connection.close()


def test_unique_key_includes_source_and_schema_has_no_breadth(tmp_path):
    connection = connect(tmp_path / "sector.db")
    migrate(connection)
    persist_results(connection, [result("sw_l1", trends=(trend(),)), result("sw_l2", trends=(trend("sw_l2"),))])
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 2
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sector_scores)")}
    assert not any("breadth" in column for column in columns)
    connection.close()


def test_relative_strength_migration_upsert_and_legacy_compatibility(tmp_path):
    connection = connect(tmp_path / "sector.db")
    migrate(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sector_scores)")}
    assert {"relative_strength_score", "capital_flow_score", "composite_score", "score_status", "missing_components"} <= columns
    enriched = replace(
        trend(), relative_strength_score=15, benchmark_code="000300", benchmark_trade_date="2026-07-20",
        sector_return_5d=.1, benchmark_return_5d=.02, excess_return_5d=.08,
        sector_return_10d=.2, benchmark_return_10d=.04, excess_return_10d=.16,
        sector_return_20d=.3, benchmark_return_20d=.05, excess_return_20d=.25,
        relative_strength_updated_at="2026-07-20T10:00:00+00:00",
        missing_components=("capital_flow",),
    )
    persist_results(connection, [result("sw_l1", trends=(enriched,))])
    row = connection.execute("SELECT trend_score,relative_strength_score,capital_flow_score,composite_score,score_status,missing_components FROM sector_scores").fetchone()
    assert row["trend_score"] == 70 and row["relative_strength_score"] == 15
    assert row["capital_flow_score"] is None and row["composite_score"] is None
    assert row["score_status"] == "partial" and row["missing_components"] == '["capital_flow"]'
    migrate(connection)
    assert connection.execute("SELECT trend_score FROM sector_scores").fetchone()[0] == 70
    connection.close()


def test_benchmark_health_source_is_supported(tmp_path):
    connection = connect(tmp_path / "sector.db")
    migrate(connection)
    ensure_source(connection, "benchmark_csi300")
    record_success(connection, "benchmark_csi300", latency_ms=12, metadata={"sector_count": 120, "latest_trade_date": "2026-07-20"})
    item = get_status(connection, "benchmark_csi300")
    assert item["status"] == "healthy" and item["metadata"]["latest_trade_date"] == "2026-07-20"
    connection.close()


def test_pr2_database_upgrades_without_losing_trend_score(tmp_path):
    connection = connect(tmp_path / "legacy-sector.db")
    connection.executescript((PROJECT_ROOT / "database" / "migrations" / "004_sector_scores.sql").read_text(encoding="utf-8"))
    connection.execute(
        """INSERT INTO sector_scores(source,sector_level,sector_code,sector_name,trade_date,
           trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at)
           VALUES('sw_l1','1','801010','农林牧渔','2026-07-20',60,'strong',100,99,98,97,1.1,1,'2026-07-20T10:00:00+00:00')"""
    )
    connection.commit()
    migrate(connection)
    row = connection.execute("SELECT trend_score,relative_strength_score,capital_flow_score,composite_score FROM sector_scores").fetchone()
    assert tuple(row) == (60, None, None, None)
    migrate(connection)
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 1
    connection.close()
