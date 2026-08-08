import sqlite3
from datetime import date
from backend.dividend.historical_price_provider import HistoricalPriceProvider
def db():
 c=sqlite3.connect(':memory:');c.execute('create table a_share_daily_bars(stock_code,trade_date,close,adjustment)');return c
def test_local_year_end_is_same_year_and_unadjusted():
 c=db();c.executemany('insert into a_share_daily_bars values(?,?,?,?)',[('000001','2023-12-29',10,'none'),('000001','2024-01-02',99,'none'),('000001','2023-12-30',20,'qfq')]);p=HistoricalPriceProvider(c).get_year_end_price('000001.SZ',2023);assert (p.trade_date,p.close)==('2023-12-29',10)
def test_latest_stale_is_marked_without_write():
 c=db();c.execute('insert into a_share_daily_bars values(?,?,?,?)',('000001','2024-01-01',10,'none'));before=c.execute('select count(*) from a_share_daily_bars').fetchone()[0];p=HistoricalPriceProvider(c).get_latest_price('000001.SZ',date(2024,2,1));assert p.is_stale and before==c.execute('select count(*) from a_share_daily_bars').fetchone()[0]
