import sqlite3

from backend.expectation_gap.database import migrate


def test_032_adds_only_current_basis_columns_without_changing_raw_dps(tmp_path):
    db=tmp_path/'migration.db'; c=sqlite3.connect(db); c.row_factory=sqlite3.Row
    migrate(c)
    c.execute("INSERT INTO dividend_stable_universe(market,symbol,company_name,monopoly_type,stability_subtype,inclusion_source,inclusion_reason,risk_note,included_at,updated_at) VALUES('CN','600001.SH','Test','x','stable_monopoly','manual_review','x','', '2026-01-01','2026-01-01')")
    c.execute("INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at) VALUES('CN','600001.SH',2025,1.2,1,'x','tushare','complete','2026-01-01','2026-01-01')")
    c.commit(); migrate(c)
    columns={row[1] for row in c.execute('PRAGMA table_info(annual_cash_dividend_summaries)')}
    row=c.execute("SELECT cash_dividend_per_share,current_basis_dps,share_basis_as_of FROM annual_cash_dividend_summaries").fetchone()
    assert {'current_basis_dps','share_basis_as_of'} <= columns
    assert tuple(row)==(1.2,None,None)
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
