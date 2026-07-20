from backend.collector.probe_sector_data import SectorTrend, SourceResult, SourceStatus
from backend.collector.collect_sector_scores import persist_results
from backend.expectation_gap.database import connect, migrate


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
