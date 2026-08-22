from backend.expectation_gap.database import connect, migrate


def test_033_adds_only_manual_position_level_columns(tmp_path):
    connection = connect(tmp_path / "033-runner.db")
    migrate(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(dividend_stable_universe)")}
    assert {"grade", "entry_yield", "add_yield", "heavy_yield"} <= columns
    assert not {"dividend_email_enabled", "last_alert_status", "last_alert_at"} & columns
    connection.close()
