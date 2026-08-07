"""SQLite queries for the dividend universe management API."""
from __future__ import annotations
import sqlite3
from datetime import date


class DividendUniverseRepository:
 def __init__(self, connection: sqlite3.Connection): self.connection=connection
 def list(self, *, include_disabled=False, search="", subtype="", monopoly_type=""):
  clauses=[];params=[]
  if not include_disabled: clauses.append('u.is_enabled=1')
  if search: clauses.append('(u.symbol LIKE ? OR u.company_name LIKE ?)');params += [f'%{search.upper()}%',f'%{search}%']
  if subtype: clauses.append('u.stability_subtype=?');params.append(subtype)
  if monopoly_type: clauses.append('u.monopoly_type=?');params.append(monopoly_type)
  where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
  years=[r[0] for r in self.connection.execute('SELECT DISTINCT calendar_year FROM annual_cash_dividend_summaries ORDER BY calendar_year DESC LIMIT 3')][::-1]
  rows=self.connection.execute(f'''SELECT u.*,a.calendar_year,a.cash_dividend_per_share,a.dividend_event_count FROM dividend_stable_universe u LEFT JOIN annual_cash_dividend_summaries a ON a.market=u.market AND a.symbol=u.symbol{where} ORDER BY u.is_enabled DESC,u.stability_subtype,u.monopoly_type,u.symbol''',params).fetchall()
  items={}
  for r in rows:
   item=items.setdefault(r['symbol'],{key:r[key] for key in r.keys() if key not in {'calendar_year','cash_dividend_per_share','dividend_event_count'}}|{'is_enabled':bool(r['is_enabled']),'annual_dps':{},'dividend_event_counts':{}})
   if r['calendar_year'] is not None:item['annual_dps'][str(r['calendar_year'])]=r['cash_dividend_per_share'];item['dividend_event_counts'][str(r['calendar_year'])]=r['dividend_event_count']
  for item in items.values():
   values=[item['annual_dps'].get(str(y)) for y in years]
   item['three_year_total_dps']=round(sum(values),6) if len(values)==3 and all(v is not None for v in values) else None
   item['three_year_average_dps']=round(item['three_year_total_dps']/3,6) if item['three_year_total_dps'] is not None else None
  return years,list(items.values())
 def search_securities(self,q):
  q=q.strip();like=f'%{q}%';rows=self.connection.execute('''SELECT m.symbol,m.security_name,m.listed_date,m.is_active,m.delisted_date,s.is_st,i.level1_name,i.level2_name,u.is_enabled FROM a_share_security_master m LEFT JOIN a_share_security_status_history s ON s.symbol=m.symbol AND s.effective_date=(SELECT MAX(effective_date) FROM a_share_security_status_history x WHERE x.symbol=m.symbol) LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol LEFT JOIN dividend_stable_universe u ON u.market='CN' AND u.symbol=m.symbol WHERE m.exchange IN ('SH','SZ') AND m.symbol NOT LIKE '20%.SZ' AND m.symbol NOT LIKE '900%.SH' AND (m.symbol LIKE ? OR m.security_name LIKE ?) AND (m.delisted_date IS NULL OR m.delisted_date>date('now')) ORDER BY m.symbol LIMIT 20''',(like,like)).fetchall()
  return [{'symbol':r[0],'company_name':r[1],'list_date':r[2],'listing_status':'active' if r[3] else 'inactive','is_st':bool(r[5]),'industry_level_1':r[6],'industry_level_2':r[7],'already_in_universe':r[8] is not None,'is_enabled':None if r[8] is None else bool(r[8])} for r in rows]
 def security(self,symbol):
  return self.connection.execute('''SELECT m.symbol,m.security_name,m.listed_date,m.is_active,m.delisted_date,s.is_st,i.level1_name,i.level2_name,i.level3_name,i.source FROM a_share_security_master m LEFT JOIN a_share_security_status_history s ON s.symbol=m.symbol AND s.effective_date=(SELECT MAX(effective_date) FROM a_share_security_status_history x WHERE x.symbol=m.symbol) LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol WHERE m.symbol=?''',(symbol,)).fetchone()
