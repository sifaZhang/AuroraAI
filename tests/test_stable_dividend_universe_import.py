from datetime import date

import pandas as pd
import pytest

from backend.dividend.models import DividendEvent
from backend.dividend.stable_universe_service import StableUniverseImportService
import sqlite3
from backend.expectation_gap.database import migrate


class Provider:
    def fetch_events(self, symbols):
        rows=[]
        for symbol in symbols:
            rows.extend(DividendEvent(symbol, date(year, 4, 1), date(year, 6, 1), 1.0, "实施") for year in (2023, 2024, 2025))
        return rows


def _database():
    con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;migrate(con)
    for symbol,name in [('600000.SH','浦发银行'),('601728.SH','中国电信'),('601088.SH','中国神华')]:
        code,exchange=symbol.split('.')
        con.execute("INSERT INTO a_share_security_master(symbol,stock_code,exchange,board_type,security_name,source,quality_flags,updated_at,is_active) VALUES(?,?,?,?,?,?,?,?,1)",(symbol,code,exchange,'MAIN',name,'GM','[]','2026-01-01'))
        con.execute("INSERT INTO a_share_security_status_history(symbol,effective_date,board_type,is_st,source,quality_flags,updated_at) VALUES(?,?,?,?,?,?,?)",(symbol,'2026-07-31','MAIN',0,'GM','[]','2026-01-01'))
    con.commit();return con


def _final():
    return pd.DataFrame([{'market':'CN','symbol':'600000.SH','company_name':'浦发银行','industry_level_1':'银行','industry_level_2':'股份制银行','monopoly_type':'banking_license','stability_subtype':'stable_monopoly','final_status':'included','final_reason':'core_bank_whitelist','risk_note':''},{'market':'CN','symbol':'600001.SH','company_name':'待审','industry_level_1':'','industry_level_2':'','monopoly_type':'x','stability_subtype':'stable_monopoly','final_status':'review_required','final_reason':'','risk_note':''}])


def test_plan_adds_manual_telecom_and_shenhua_and_imports_only_included():
    con=_database();service=StableUniverseImportService(con,Provider())
    items,totals,event_counts,summary=service.plan(_final(),date(2026,8,7))
    assert {item.symbol for item in items} == {'600000.SH','601728.SH','601088.SH'}
    assert summary['manual_addition_count']==2 and summary['planned_annual_dps_count']==9
    service.import_items(items,totals,event_counts,date(2026,8,7));service.import_items(items,totals,event_counts,date(2026,8,7))
    assert con.execute('select count(*) from dividend_stable_universe').fetchone()[0]==3
    assert con.execute('select count(*) from annual_cash_dividend_summaries').fetchone()[0]==9
    shenhua=con.execute("select stability_subtype,inclusion_source from dividend_stable_universe where symbol='601088.SH'").fetchone()
    assert tuple(shenhua)==('resource_monopoly_cyclical','manual_addition')


def test_missing_year_prevents_any_import():
    class MissingProvider(Provider):
        def fetch_events(self,symbols): return [DividendEvent('600000.SH',date(2023,4,1),date(2023,6,1),1,'实施')]
    con=_database()
    with pytest.raises(ValueError,match='missing_dps_years'):
        StableUniverseImportService(con,MissingProvider()).plan(_final().iloc[:1],date(2026,8,7))
