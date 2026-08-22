from __future__ import annotations

import csv
import sqlite3
from datetime import date

from backend.dividend.watchlist_csv import WATCHLIST_COLUMNS, export_dividend_watchlist_csv


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
    CREATE TABLE dividend_stable_universe (
      market TEXT, symbol TEXT, company_name TEXT, grade TEXT,
      entry_yield REAL, add_yield REAL, heavy_yield REAL, is_enabled INTEGER
    );
    CREATE TABLE annual_cash_dividend_summaries (
      market TEXT, symbol TEXT, calendar_year INTEGER,
      cash_dividend_per_share REAL, current_basis_dps REAL
    );
    INSERT INTO dividend_stable_universe VALUES ('CN','600002.SH','测试甲','S',5.0,6.0,7.0,1);
    INSERT INTO dividend_stable_universe VALUES ('CN','600001.SH','测试乙','B',NULL,NULL,NULL,1);
    INSERT INTO dividend_stable_universe VALUES ('CN','600003.SH','测试丙',NULL,2.0,NULL,NULL,0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600002.SH',2023,9.0,1.0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600002.SH',2024,9.0,2.0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600002.SH',2025,9.0,3.0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2023,0.2,NULL);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2024,0.3,NULL);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2025,0.4,NULL);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600003.SH',2023,1.0,1.0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600003.SH',2024,1.0,1.0);
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600003.SH',2025,1.0,1.0);
    """)
    return connection


def test_export_rebuilds_stable_utf8_watchlist_snapshot(tmp_path):
    output = tmp_path / "dividend_watchlist.csv"
    output.write_text("outdated\n", encoding="utf-8")
    result = export_dividend_watchlist_csv(_connection(), output, calculation_date=date(2026, 8, 22))

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert result["row_count"] == 3
    assert list(rows[0]) == list(WATCHLIST_COLUMNS)
    assert [row["symbol"] for row in rows] == ["600002.SH", "600001.SH", "600003.SH"]
    assert len({row["symbol"] for row in rows}) == 3
    assert rows[0]["name"] == "测试甲"
    assert rows[0]["grade"] == "S"
    assert rows[0]["entry_yield"] == "5.0"
    assert rows[0]["avg_dps_3y"] == "2.0"
    assert rows[0]["enabled"] == "true"
    assert rows[1]["grade"] == "B"
    assert rows[1]["entry_yield"] == rows[1]["add_yield"] == rows[1]["heavy_yield"] == ""
    assert rows[1]["avg_dps_3y"] == "0.3"
    assert rows[2]["grade"] == ""
    assert rows[2]["enabled"] == "false"
    assert all(row["updated_at"].endswith("+08:00") for row in rows)
    assert not output.with_suffix(".csv.tmp").exists()
