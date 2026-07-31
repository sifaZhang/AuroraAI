"""Offline PR6.3 batch CLI. It never imports or invokes GM/PR6.2 sync."""
from __future__ import annotations
import argparse,json
from bisect import bisect_right
from datetime import date
from decimal import Decimal
from backend.expectation_gap.database import connect,connect_readonly,migrate
from .contracts import BoardType,DataSource,QualityFlag,SecurityStatus
from .detector import (
 EventDecision,DetectionStatus,Reason,Bar,Metadata,classify,
)
from .rules import normalize_symbol
from .detection_runs import run_detection

MAX_DAYS=31
def parser():
 p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True); g.add_argument('--trade-date'); g.add_argument('--start-date');
 p.add_argument('--end-date'); p.add_argument('--codes'); p.add_argument('--detection-version',default='first_limit_v1'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--resume',action='store_true'); p.add_argument('--run-id'); p.add_argument('--max-symbols',type=int); p.add_argument('--force',action='store_true'); return p
def _date(x): return date.fromisoformat(x)
def detect_first_limits(con, *, start, end, codes, detection_version='first_limit_v1',
                        run_id=None, resume=False, force=False, dry_run=False):
 days=[r[0] for r in con.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?",(str(start),str(end)))]
 if not days: raise LookupError('no covered open trade dates')
 symbols=sorted(normalize_symbol(x).canonical for x in codes)
 symbol_set=set(symbols)
 params={'start_date':str(start),'end_date':str(end),'symbols':symbols,'detection_version':detection_version}
 items=[(s,d) for s in symbols for d in days]
 code_to_symbol={normalize_symbol(symbol).code:symbol for symbol in symbols}
 history_days=[r[0] for r in con.execute(
  """SELECT trade_date FROM a_share_trading_calendar
     WHERE market='CN' AND is_open=1 AND trade_date<=?
     ORDER BY trade_date DESC LIMIT 27""",(str(end),)
 ).fetchall()]
 history_start=min(history_days) if history_days else str(start)
 bars_by_code={code:[] for code in code_to_symbol}
 for row in con.execute(
  """SELECT * FROM a_share_daily_bars
     WHERE adjustment='none' AND trade_date BETWEEN ? AND ?
     ORDER BY stock_code,trade_date""",(history_start,str(end))
 ):
  if row['stock_code'] in bars_by_code:
   bars_by_code[row['stock_code']].append(row)
 metadata={(row['symbol'],row['trade_date']):row for row in con.execute(
  """SELECT * FROM first_limit_daily_metadata
     WHERE trade_date BETWEEN ? AND ?""",(history_start,str(end))
 ) if row['symbol'] in symbol_set}
 statuses={symbol:[] for symbol in symbols}
 for row in con.execute(
  """SELECT * FROM a_share_security_status_history
     WHERE effective_date BETWEEN ? AND ?
     ORDER BY symbol,effective_date""",(history_start,str(end))
 ):
  if row['symbol'] not in symbol_set: continue
  security=normalize_symbol(row['symbol'])
  flags=frozenset(QualityFlag(value) for value in json.loads(row['quality_flags']))
  value=SecurityStatus(
   security,_date(row['effective_date']),BoardType(row['board_type']),
   None if row['is_st'] is None else bool(row['is_st']),
   None if row['is_suspended'] is None else bool(row['is_suspended']),
   None if row['no_price_limit'] is None else bool(row['no_price_limit']),
   _date(row['listed_date']) if row['listed_date'] else None,
   _date(row['delisted_date']) if row['delisted_date'] else None,
   DataSource(row['source']),flags,
  )
  statuses[row['symbol']].append(value)
 status_dates={symbol:[value.effective_date for value in values] for symbol,values in statuses.items()}
 def status_asof(symbol,day):
  target=_date(day); index=bisect_right(status_dates[symbol],target)-1
  return statuses[symbol][index] if index>=0 else None
 def decide(symbol,day):
  code=normalize_symbol(symbol).code; available=bars_by_code.get(code,[])
  bar_dates=[row['trade_date'] for row in available]
  index=bisect_right(bar_dates,day)
  rows=available[max(0,index-21):index]
  if not rows or rows[-1]['trade_date']!=day:
   decision=EventDecision(
    DetectionStatus.INDETERMINATE,None,None,None,None,None,None,0,None,
    None,None,frozenset({Reason.MISSING_DAILY_BAR}),frozenset(),
   )
   return (
    symbol,day,detection_version,decision,None,None,None,None,None,None,None
   )
  target=rows[-1]
  def b(r): return Bar(_date(r['trade_date']),*(Decimal(str(r[x])) if r[x] is not None else None for x in ('open','high','low','close','volume','amount')),r['adjustment'])
  def md(d): return Metadata(*(Decimal(str(d[x])) if d and d[x] is not None else None for x in ('pre_close','source_upper_limit','source_lower_limit')))
  def fetch(d): return metadata.get((symbol,str(d)))
  target_meta=fetch(day)
  decision=classify(symbol,b(target),md(target_meta),status_asof(symbol,day),[(b(r),md(fetch(r['trade_date'])),status_asof(symbol,r['trade_date'])) for r in rows[:-1]])
  return (symbol,day,detection_version,decision,target['open'],target['high'],target['low'],target['close'],target_meta['pre_close'] if target_meta else None,str(decision.upper_limit) if decision.upper_limit else None,None)
 return run_detection(con,items,params,decide,run_id=run_id,resume=resume,force=force,dry_run=dry_run)

def main(argv=None):
 try:
  a=parser().parse_args(argv); start=_date(a.trade_date or a.start_date); end=_date(a.trade_date or a.end_date) if (a.trade_date or a.end_date) else None
  if end is None or start>end: raise ValueError('--end-date is required and must not precede --start-date')
  if (end-start).days+1>MAX_DAYS: raise ValueError('date range exceeds safety threshold')
  if a.resume and not a.run_id: raise ValueError('--resume requires --run-id')
  if a.dry_run and (a.resume or a.run_id): raise ValueError('dry-run cannot resume or create a run')
  codes=sorted(normalize_symbol(x).canonical for x in a.codes.split(',')) if a.codes else None
  con=connect_readonly() if a.dry_run else connect()
  if not a.dry_run: migrate(con)
  days=[r[0] for r in con.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?",(str(start),str(end)))]
  if not days: raise LookupError('no covered open trade dates')
  if not codes:
   if a.max_symbols is None: raise ValueError('provide --codes or --max-symbols')
   rows=con.execute("SELECT DISTINCT stock_code,exchange FROM a_share_security_master ORDER BY stock_code LIMIT ?",(a.max_symbols,)).fetchall(); codes=[f'{r[0]}.{r[1]}' for r in rows]
  elif a.max_symbols is not None: codes=codes[:a.max_symbols]
  result=detect_first_limits(con,start=start,end=end,codes=codes,detection_version=a.detection_version,run_id=a.run_id,resume=a.resume,force=a.force,dry_run=a.dry_run)
  planned=len(codes)*len(days)
  print(json.dumps(result,ensure_ascii=False)); return 2 if result.get('failed',0)==planned and planned else (1 if result.get('failed') or result.get('indeterminate') else 0)
 except (ValueError,LookupError,argparse.ArgumentError) as e: print(f'ERROR: {e}'); return 2
 except Exception as e: print(f'ERROR: {type(e).__name__}: {e}'); return 2
if __name__=='__main__': raise SystemExit(main())
