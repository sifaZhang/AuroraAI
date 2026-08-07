from datetime import date
from fastapi import APIRouter,Query
from pydantic import BaseModel
from backend.expectation_gap.database import connect,migrate
from backend.dividend.yield_service import calculate,save,target_years
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
  if symbols:
   placeholders=','.join('?'*len(symbols))
   for value in c.execute(f"SELECT symbol,calendar_year,cash_dividend_per_share FROM annual_cash_dividend_summaries WHERE market='CN' AND symbol IN ({placeholders}) AND calendar_year IN (?,?,?)",(*symbols,*years)):
    dps[value['symbol']][str(value['calendar_year'])]=value['cash_dividend_per_share']
  for row in rows: row['annual_dps']=dps[row['symbol']]
  return {'calculation_date':day.isoformat(),'target_years':list(years),'total':len(rows),'items':rows}
 finally:c.close()
@router.post('/refresh')
def refresh(payload:Refresh):
 c=connect()
 try:
  migrate(c);rows=calculate(c,payload.calculation_date);save(c,rows);return {'calculation_date':payload.calculation_date.isoformat(),'total':len(rows),'items':rows}
 finally:c.close()
