"""Read-only local-first price access for high-dividend scans."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,timedelta

@dataclass(frozen=True)
class PricePoint:
 symbol:str; trade_date:str; close:float; source:str; is_stale:bool=False

class HistoricalPriceProvider:
 def __init__(self,connection,client=None): self.connection,self.client=connection,client
 def _local(self,symbol,start,end):
  r=self.connection.execute("SELECT trade_date,close FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date>=? AND trade_date<=? AND close>0 ORDER BY trade_date DESC LIMIT 1",(symbol.split('.')[0],start,end)).fetchone()
  return None if r is None else PricePoint(symbol,r[0],float(r[1]),'local_daily_bar')
 def _tushare(self,symbol,start,end):
  if self.client is None:return None
  frame=self.client.call('daily',ts_code=symbol,start_date=start.replace('-',''),end_date=end.replace('-',''),fields='ts_code,trade_date,close')
  if frame is None or frame.empty:return None
  rows=[r for r in frame.to_dict('records') if float(r.get('close') or 0)>0 and str(r['trade_date'])<=end.replace('-','')]
  if not rows:return None
  row=max(rows,key=lambda r:str(r['trade_date'])); d=str(row['trade_date']); return PricePoint(symbol,f'{d[:4]}-{d[4:6]}-{d[6:]}',float(row['close']),'tushare_daily')
 def get_year_end_price(self,symbol,year):
  point=self._local(symbol,f'{year}-01-01',f'{year}-12-31')
  if point:return point
  for days in (12,30):
   point=self._tushare(symbol,f'{year}-12-{31-days+1:02d}',f'{year}-12-31')
   if point and point.trade_date.startswith(str(year)): return point
  return None
 def get_latest_price(self,symbol,day):
  local=self._local(symbol,'1900-01-01',day.isoformat())
  if local and date.fromisoformat(local.trade_date)>=day-timedelta(days=10):return local
  remote=self._tushare(symbol,(day-timedelta(days=30)).isoformat(),day.isoformat())
  if remote:return remote
  return None if local is None else PricePoint(local.symbol,local.trade_date,local.close,local.source,True)
