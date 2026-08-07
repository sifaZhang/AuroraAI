from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
from backend.expectation_gap.database import connect,migrate
from .yield_service import calculate,save,target_years
def main():
 p=argparse.ArgumentParser();p.add_argument('--calculation-date',type=date.fromisoformat,required=True);p.add_argument('--database',type=Path);p.add_argument('--symbols');p.add_argument('--dry-run',action='store_true');p.add_argument('--force',action='store_true');a=p.parse_args(); c=connect(a.database)
 try:
  if not a.dry_run:migrate(c)
  rows=calculate(c,a.calculation_date,set(a.symbols.split(',')) if a.symbols else None)
  if not a.dry_run:save(c,rows)
  print('calculation_date',a.calculation_date,'target_years',','.join(map(str,target_years(a.calculation_date))))
  for r in rows: print(r['symbol'],r['company_name'],r['latest_price'],r['three_year_average_yield'],r['data_quality_status'])
 finally:c.close()
if __name__=='__main__':main()
