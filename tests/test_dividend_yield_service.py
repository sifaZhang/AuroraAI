import sqlite3
from datetime import date
from backend.dividend.yield_service import calculate

def test_price_and_latest_dps_always_produce_latest_yield():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
 c.executescript("""CREATE TABLE dividend_stable_universe(market text,symbol text,company_name text,industry_level_1 text,industry_level_2 text,stability_subtype text,is_enabled integer);
 CREATE TABLE annual_cash_dividend_summaries(market text,symbol text,calendar_year integer,cash_dividend_per_share real);
 CREATE TABLE a_share_daily_bars(stock_code text,trade_date text,close real,source text,adjustment text);
 INSERT INTO dividend_stable_universe VALUES('CN','600001.SH','Test',NULL,NULL,'stable_monopoly',1);
 INSERT INTO annual_cash_dividend_summaries VALUES('CN','600001.SH',2023,1),('CN','600001.SH',2024,2),('CN','600001.SH',2025,3);
 INSERT INTO a_share_daily_bars VALUES('600001','2026-08-01',10,'local_daily_bar','none');""")
 row=calculate(c,date(2026,8,7))[0]
 assert row['latest_year_yield']==.3
 assert row['three_year_average_yield']==.2
