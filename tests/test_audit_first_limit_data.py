import sqlite3

from backend.collector.audit_first_limit_data import audit_connection, render_markdown


def test_audit_reports_daily_field_gaps_and_membership_lookahead_risk():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE a_share_daily_bars(stock_code TEXT, trade_date TEXT, adjustment TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL)")
    connection.execute("INSERT INTO a_share_daily_bars VALUES('000001','2020-01-02','none',1,1,1,1,1,1)")
    connection.execute("CREATE TABLE sector_memberships(stock_code TEXT, snapshot_date TEXT, is_current INTEGER, historical_use_is_approximate INTEGER)")
    connection.execute("INSERT INTO sector_memberships VALUES('000001','2026-07-01',1,1)")
    connection.execute("CREATE TABLE sector_scores(trade_date TEXT)")
    connection.execute("INSERT INTO sector_scores VALUES('2026-07-01')")
    report = audit_connection(connection)
    assert {"pre_close", "upper_limit", "lower_limit"}.issubset(report["daily_bars"]["missing_strategy_fields"])
    assert report["industry_history"]["historical_lookup_safe"] is False
    assert report["decision"]["ready_for_long_history_backtest"] is False
    assert "PR6.1" in render_markdown(report)
