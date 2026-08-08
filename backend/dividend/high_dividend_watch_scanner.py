"""Read-only scanner for the single minimal high-dividend rule."""
from __future__ import annotations
from datetime import date
from backend.dividend.annual_dps import aggregate_events
from backend.dividend.high_dividend_watch_service import qualify_historical_dividend,classify_industry
from backend.dividend.historical_price_provider import HistoricalPriceProvider

def _fetch_dividend_events_with_isolation(provider,symbols):
 try:return provider.fetch_events(symbols),None
 except Exception:
  events=[]
  for symbol in symbols:
   try:events.extend(provider.fetch_events([symbol]))
   except Exception:pass
  return events,'batch dividend fallback used'

def scan_high_dividend_watch(connection,symbols:list[str],calculation_date:date,dividend_provider,price_provider=None):
 years=tuple(range(calculation_date.year-3,calculation_date.year));events,warning=_fetch_dividend_events_with_isolation(dividend_provider,symbols); totals,_=aggregate_events(events,years);provider=price_provider or HistoricalPriceProvider(connection);out=[]
 for symbol in symbols:
  try:
   row=connection.execute("SELECT m.security_name,i.level1_name,i.level2_name FROM a_share_security_master m LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol WHERE m.symbol=?",(symbol,)).fetchone()
   if row is None: raise ValueError('invalid security')
   dps={year:totals.get(symbol,{}).get(year) for year in years};points={year:provider.get_year_end_price(symbol,year) for year in years};prices={year:(p.close if p else None) for year,p in points.items()};yields,failures=qualify_historical_dividend(dps,prices);latest=provider.get_latest_price(symbol,calculation_date);avg=sum(dps.values())/3 if all(v is not None for v in dps.values()) else None;existing=connection.execute("SELECT stability_subtype FROM dividend_stable_universe WHERE market='CN' AND symbol=?",(symbol,)).fetchone();industry=' '.join(filter(None,(row[1],row[2])))
   out.append({'symbol':symbol,'company_name':row[0],'industry':industry,'qualified':not failures,'qualification_failures':failures,'suggested_stability_subtype':classify_industry(industry) if not failures else None,'annual_dps':dps,'reference_prices':prices,'reference_dates':{y:(points[y].trade_date if points[y] else None) for y in years},'historical_yields':yields,'three_year_average_dps':avg,'latest_price':latest.close if latest else None,'price_date':latest.trade_date if latest else None,'latest_year_yield':dps[years[-1]]/latest.close if latest and dps[years[-1]] else None,'three_year_average_yield':avg/latest.close if latest and avg else None,'already_in_universe':existing is not None,'status':'success','warning':warning})
  except Exception as exc: out.append({'symbol':symbol,'status':'failed','error':str(exc)})
 return out
