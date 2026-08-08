from datetime import date
from fastapi import APIRouter,Query
from pydantic import BaseModel
from backend.expectation_gap.database import connect,migrate
from backend.dividend.yield_service import calculate,save,target_years
from backend.dividend.share_basis_adjustment import current_yield_metrics
from backend.dividend.price_refresh_service import refresh_enabled_prices
from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
router=APIRouter(prefix='/api/dividend/yields',tags=['dividend'])
class Refresh(BaseModel): calculation_date:date
@router.get('')
def listing(calculation_date:date|None=None,include_disabled:bool=False,stability_subtype:str=''):
 c=connect()
 try:
  day=calculation_date
  if day is None:
   row=c.execute('SELECT MAX(calculation_date) FROM dividend_yield_snapshots').fetchone(); day=date.fromisoformat(row[0]) if row and row[0] else None
  if day is None:return {'calculation_date':None,'target_years':[],'total':0,'items':[]}
  q="SELECT s.*,u.company_name,u.industry_level_1,u.industry_level_2,u.stability_subtype FROM dividend_yield_snapshots s JOIN dividend_stable_universe u ON u.market=s.market AND u.symbol=s.symbol WHERE s.calculation_date=?";args=[day.isoformat()]
  if not include_disabled:q+=' AND u.is_enabled=1'
  if stability_subtype:q+=' AND u.stability_subtype=?';args.append(stability_subtype)
  rows=[dict(x) for x in c.execute(q+' ORDER BY s.three_year_average_yield DESC NULLS LAST,s.symbol',args)]
  years=target_years(day)
  symbols=[row['symbol'] for row in rows]
  dps={symbol:{str(year):None for year in years} for symbol in symbols}
  current_dps={symbol:{str(year):None for year in years} for symbol in symbols}
  basis_dates={}
  if symbols:
   placeholders=','.join('?'*len(symbols))
   for value in c.execute(f"SELECT symbol,calendar_year,cash_dividend_per_share,current_basis_dps,share_basis_as_of FROM annual_cash_dividend_summaries WHERE market='CN' AND symbol IN ({placeholders}) AND calendar_year IN (?,?,?)",(*symbols,*years)):
    dps[value['symbol']][str(value['calendar_year'])]=value['cash_dividend_per_share']
    current_dps.setdefault(value['symbol'],{str(year):None for year in years})[str(value['calendar_year'])]=value['current_basis_dps'] if value['current_basis_dps'] is not None else value['cash_dividend_per_share']
    basis_dates[value['symbol']]=value['share_basis_as_of']
  for row in rows:
   row['annual_dps']=dps[row['symbol']]
   row['current_basis_dps']=current_dps.get(row['symbol'],dps[row['symbol']])
   row['share_basis_as_of']=basis_dates.get(row['symbol'])
   row.update(current_yield_metrics({int(key):value for key,value in row['current_basis_dps'].items() if value is not None},years,row['latest_price']))
  return {'calculation_date':day.isoformat(),'target_years':list(years),'total':len(rows),'items':rows}
 finally:c.close()
@router.post('/refresh')
def refresh(payload:Refresh):
 c=connect()
 try:
  migrate(c); settings=DataSourceSettings.from_env(); summary=refresh_enabled_prices(c,TushareClient(settings.tushare_token),payload.calculation_date); rows=calculate(c,payload.calculation_date);save(c,rows);return {'calculation_date':payload.calculation_date.isoformat(),'total':len(rows),'snapshot_count':len(rows),**summary}
 finally:c.close()
