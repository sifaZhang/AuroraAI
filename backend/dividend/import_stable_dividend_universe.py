from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
import pandas as pd
from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.expectation_gap.database import connect, connect_readonly, database_path, migrate
from .dividend_candidate_service import TushareDividendProvider
from .stable_universe_service import StableUniverseImportService
from .watchlist_csv import export_dividend_watchlist_csv

def main() -> int:
 parser=argparse.ArgumentParser();parser.add_argument('--input',default='exports/dividend/dividend_a_candidates_final.csv');parser.add_argument('--calculation-date',type=date.fromisoformat,required=True);parser.add_argument('--database',type=Path);parser.add_argument('--dry-run',action='store_true');parser.add_argument('--force',action='store_true');parser.add_argument('--symbols');args=parser.parse_args()
 settings=DataSourceSettings.from_env();db=args.database or database_path();con=connect_readonly(db) if args.dry_run else connect(db)
 if not args.dry_run: migrate(con)
 try:
  symbols={x.strip().upper() for x in args.symbols.split(',')} if args.symbols else None
  service=StableUniverseImportService(con,TushareDividendProvider(TushareClient(settings.tushare_token,timeout_seconds=settings.request_timeout_seconds,max_retries=settings.max_retries,requests_per_minute=settings.requests_per_minute)))
  items, totals, event_counts, summary=service.plan(pd.read_csv(args.input,encoding='utf-8-sig'),args.calculation_date,symbols)
  for key,value in summary.items():print(f'{key}: {value}')
  print('stable_monopoly_count:',sum(x.stability_subtype=='stable_monopoly' for x in items));print('resource_monopoly_cyclical_count:',sum(x.stability_subtype=='resource_monopoly_cyclical' for x in items));print('errors: 0')
  if not args.dry_run:
   service.import_items(items,totals,event_counts,args.calculation_date,force=args.force)
   exported=export_dividend_watchlist_csv(con,calculation_date=args.calculation_date)
   print(f"watchlist_csv_rows: {exported['row_count']}")
 finally: con.close()
 return 0
if __name__=='__main__': raise SystemExit(main())
