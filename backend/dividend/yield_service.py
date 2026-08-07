from __future__ import annotations
import sqlite3
from datetime import date,datetime,timezone

def target_years(day:date): return (day.year-3,day.year-2,day.year-1)
def calculate(connection:sqlite3.Connection, day:date, symbols:set[str]|None=None):
 years=target_years(day); params=[]; sql="SELECT u.market,u.symbol,u.company_name,u.industry_level_1,u.industry_level_2,u.stability_subtype FROM dividend_stable_universe u WHERE u.is_enabled=1"
 if symbols: sql += " AND u.symbol IN ("+','.join('?'*len(symbols))+")";params=list(symbols)
 rows=[]
 for u in connection.execute(sql,params):
  code=u['symbol'].split('.')[0]; price=connection.execute("SELECT trade_date,close,source FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date<=? AND close>0 ORDER BY trade_date DESC LIMIT 1",(code,day.isoformat())).fetchone()
  d={r['calendar_year']:r['cash_dividend_per_share'] for r in connection.execute("SELECT calendar_year,cash_dividend_per_share FROM annual_cash_dividend_summaries WHERE market=? AND symbol=? AND calendar_year IN (?,?,?)",(u['market'],u['symbol'],*years))}
  complete=len(d)==3; p=float(price['close']) if price else None; pd=date.fromisoformat(price['trade_date']) if price else None; age=(day-pd).days if pd else None
  status='ok'; warnings=[]
  if not price: status='missing_price'
  elif p<=0: status='invalid_price'
  elif not complete: status='missing_dividend_year'
  elif age>7: status='stale_price';warnings=['stale_price']
  total=sum(float(d[y]) for y in years) if complete else None; avg=total/3 if total is not None else None; latest=float(d[years[-1]]) if years[-1] in d else None
  rows.append(dict(market=u['market'],symbol=u['symbol'],company_name=u['company_name'],industry_level_1=u['industry_level_1'],industry_level_2=u['industry_level_2'],stability_subtype=u['stability_subtype'],calculation_date=day.isoformat(),price_date=price['trade_date'] if price else None,latest_price=p,price_source=price['source'] if price else None,price_age_days=age,latest_year=years[-1],latest_year_dps=latest,three_year_start=years[0],three_year_end=years[-1],three_year_total_dps=total,three_year_average_dps=avg,latest_year_yield=(latest/p if p and latest is not None else None),three_year_average_yield=(avg/p if p and avg is not None else None),data_quality_status=status,warning_flags=','.join(warnings),annual_dps={str(y):d.get(y) for y in years}))
 return sorted(rows,key=lambda x:(x['three_year_average_yield'] is None, -(x['three_year_average_yield'] or 0),x['symbol']))
def save(connection, rows):
 now=datetime.now(timezone.utc).isoformat(timespec='seconds'); cols=['market','symbol','calculation_date','price_date','latest_price','price_source','price_age_days','latest_year','latest_year_dps','three_year_start','three_year_end','three_year_total_dps','three_year_average_dps','latest_year_yield','three_year_average_yield','data_quality_status','warning_flags']
 with connection:
  connection.executemany("INSERT INTO dividend_yield_snapshots("+','.join(cols)+",created_at,updated_at) VALUES("+','.join('?'*(len(cols)+2))+") ON CONFLICT(market,symbol,calculation_date) DO UPDATE SET price_date=excluded.price_date,latest_price=excluded.latest_price,price_source=excluded.price_source,price_age_days=excluded.price_age_days,latest_year_dps=excluded.latest_year_dps,three_year_total_dps=excluded.three_year_total_dps,three_year_average_dps=excluded.three_year_average_dps,latest_year_yield=excluded.latest_year_yield,three_year_average_yield=excluded.three_year_average_yield,data_quality_status=excluded.data_quality_status,warning_flags=excluded.warning_flags,updated_at=excluded.updated_at",[tuple(r[c] for c in cols)+(now,now) for r in rows])
