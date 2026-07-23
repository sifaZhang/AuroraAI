from backend.collector.refresh_market_pulse_daily import _score_change_counts
from backend.expectation_gap.database import connect, migrate


def test_score_change_counts_compare_each_sector_with_its_previous_result(tmp_path):
    connection = connect(tmp_path / "daily.db")
    migrate(connection)
    rows = [
        ("801010", "2026-07-22", 70),
        ("801010", "2026-07-23", 75),
        ("801020", "2026-07-22", 80),
        ("801020", "2026-07-23", 75),
        ("801030", "2026-07-22", 60),
        ("801030", "2026-07-23", 60),
        ("801040", "2026-07-23", 90),
    ]
    for code, trade_date, total_score in rows:
        connection.execute(
            """INSERT INTO sector_breadth_scores(
                classification_system,sector_code,trade_date,membership_snapshot_date,
                total_members,valid_members,coverage_ratio,excluded_members,
                total_score,status,quality_warnings,is_approximate,lookahead_warning,
                calculation_version,created_at,updated_at)
                VALUES('sw_level1',?,?,?,10,10,1,'{}',?,'success','[]',0,
                       'current snapshot','breadth_v1',?,?)""",
            (code, trade_date, trade_date, total_score, trade_date, trade_date),
        )
    connection.commit()

    assert _score_change_counts(connection, "2026-07-23") == (3, 1, 1, 1)
    connection.close()
