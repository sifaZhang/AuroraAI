"""Offline PR6.3 batch CLI. It never imports or invokes GM/PR6.2 sync."""
from __future__ import annotations
import argparse,json
from datetime import date
from decimal import Decimal
from backend.expectation_gap.database import connect,migrate
from .detector import Bar,Metadata,classify
from .repository import get_security_status_as_of
from .rules import normalize_symbol
from .detection_runs import run_detection

MAX_DAYS=31
def parser():
 p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True); g.add_argument('--trade-date'); g.add_argument('--start-date');
 p.add_argument('--end-date'); p.add_argument('--codes'); p.add_argument('--detection-version',default='first_limit_v1'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--resume',action='store_true'); p.add_argument('--run-id'); p.add_argument('--max-symbols',type=int); p.add_argument('--force',action='store_true'); return p
def _date(x): return date.fromisoformat(x)
def main(argv=None):
 try:
  a=parser().parse_args(argv); start=_date(a.trade_date or a.start_date); end=_date(a.trade_date or a.end_date) if (a.trade_date or a.end_date) else None
  if end is None or start>end: raise ValueError('--end-date is required and must not precede --start-date')
  if (end-start).days+1>MAX_DAYS: raise ValueError('date range exceeds safety threshold')
  if a.resume and not a.run_id: raise ValueError('--resume requires --run-id')
  if a.dry_run and (a.resume or a.run_id): raise ValueError('dry-run cannot resume or create a run')
  codes=sorted(normalize_symbol(x).canonical for x in a.codes.split(',')) if a.codes else None
  con=connect(); migrate(con)
  days=[r[0] for r in con.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?",(str(start),str(end)))]
  if not days: raise LookupError('no covered open trade dates')
  if not codes:
   if a.max_symbols is None: raise ValueError('provide --codes or --max-symbols')
   rows=con.execute("SELECT DISTINCT stock_code,exchange FROM a_share_security_master ORDER BY stock_code LIMIT ?",(a.max_symbols,)).fetchall(); codes=[f'{r[0]}.{r[1]}' for r in rows]
  elif a.max_symbols is not None: codes=codes[:a.max_symbols]
  params={'start_date':str(start),'end_date':str(end),'symbols':codes,'detection_version':a.detection_version}
  items=[(s,d) for s in codes for d in days]
  def decide(symbol,day):
   code=normalize_symbol(symbol).code; rows=con.execute("SELECT * FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date<=? ORDER BY trade_date DESC LIMIT 21",(code,day)).fetchall()
   if not rows or rows[0]['trade_date']!=day: raise LookupError('missing target daily bar')
   rows=list(reversed(rows)); target=rows[-1]
   def b(r): return Bar(_date(r['trade_date']),*(Decimal(str(r[x])) if r[x] is not None else None for x in ('open','high','low','close','volume','amount')),r['adjustment'])
   def md(d): return Metadata(*(Decimal(str(d[x])) if d and d[x] is not None else None for x in ('pre_close','source_upper_limit','source_lower_limit')))
   def fetch(d): return con.execute('SELECT * FROM first_limit_daily_metadata WHERE symbol=? AND trade_date=?',(symbol,d)).fetchone()
   decision=classify(symbol,b(target),md(fetch(day)),get_security_status_as_of(con,symbol,day),[(b(r),md(fetch(r['trade_date'])),get_security_status_as_of(con,symbol,r['trade_date'])) for r in rows[:-1]])
   return (symbol,day,a.detection_version,decision,target['open'],target['high'],target['low'],target['close'],fetch(day)['pre_close'] if fetch(day) else None,str(decision.upper_limit) if decision.upper_limit else None,None)
  result=run_detection(con,items,params,decide,run_id=a.run_id,resume=a.resume,force=a.force,dry_run=a.dry_run)
  print(json.dumps(result,ensure_ascii=False)); return 2 if result.get('failed',0)==len(items) and items else (1 if result.get('failed') or result.get('indeterminate') else 0)
 except (ValueError,LookupError,argparse.ArgumentError) as e: print(f'ERROR: {e}'); return 2
 except Exception as e: print(f'ERROR: {type(e).__name__}: {e}'); return 2
if __name__=='__main__': raise SystemExit(main())
