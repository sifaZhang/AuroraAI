"""Offline PR6.4 first-limit quality scoring CLI; never syncs data or uses network."""
from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import date
from backend.expectation_gap.database import connect,connect_readonly,migrate
from .rules import normalize_symbol
from .quality import pre_position,volume_expansion,amount,turnover,candle_shape,industry_resonance,industry_strength,aggregate
from . import quality_repository as repo

MAX_DAYS=31
def parser():
 p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True); g.add_argument('--trade-date'); g.add_argument('--start-date')
 p.add_argument('--end-date'); p.add_argument('--codes'); p.add_argument('--detection-version',default='first_limit_v1'); p.add_argument('--scoring-version',default='first_limit_quality_v1'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--resume',action='store_true'); p.add_argument('--run-id'); p.add_argument('--max-symbols',type=int); p.add_argument('--force',action='store_true'); return p
def _date(value): return date.fromisoformat(value)
def _components(connection,event):
 prior,target=repo.daily_inputs(connection,event)
 if target is None: raise LookupError('missing target daily bar')
 closes=[r['close'] for r in prior]; volumes=[r['volume'] for r in prior[-5:]]
 mapping=repo.current_industry_mapping(connection,event['symbol'])
 mapping_payload=None if mapping is None else {'classification_system':mapping['classification_system'],'sector_code':mapping['sector_code'],'snapshot_date':mapping['snapshot_date'],'warning':mapping['lookahead_bias_warning']}
 trend=repo.same_day_trend_score(connection,mapping,event['trade_date'])
 return [pre_position(closes),volume_expansion(volumes,target['volume']),amount(target['amount']),turnover(None),candle_shape(target['open'],target['high'],target['low'],target['close'],event['pre_close']),industry_resonance(None,mapping=mapping_payload),industry_strength(None if trend is None else trend['trend_score'],approximate=mapping is not None,mapping=mapping_payload,score_date=event['trade_date'])]
def main(argv=None):
 try:
  a=parser().parse_args(argv); start=_date(a.trade_date or a.start_date); end=_date(a.trade_date or a.end_date) if (a.trade_date or a.end_date) else None
  if end is None or start>end: raise ValueError('--end-date is required and must not precede --start-date')
  if (end-start).days+1>MAX_DAYS: raise ValueError('date range exceeds safety threshold')
  if a.resume and not a.run_id: raise ValueError('--resume requires --run-id')
  if a.dry_run and (a.resume or a.run_id): raise ValueError('dry-run cannot resume or create a run')
  symbols=sorted(normalize_symbol(x).canonical for x in a.codes.split(',')) if a.codes else None
  con=connect_readonly() if a.dry_run else connect()
  if not a.dry_run: migrate(con)
  if symbols is None and a.max_symbols is None: raise ValueError('provide --codes or --max-symbols')
  events=repo.list_scoreable_events(con,start,end,a.detection_version,symbols)
  if symbols is None: events=events[:a.max_symbols]
  elif a.max_symbols is not None: events=events[:a.max_symbols]
  params={'start_date':str(start),'end_date':str(end),'symbols':symbols,'detection_version':a.detection_version,'scoring_version':a.scoring_version}
  if a.dry_run:
   counts=Counter(); summaries=[]
   for event in events:
    try: summary=aggregate(_components(con,event)); counts['success']+=1; counts[summary['score_status']]+=1; summaries.append({'symbol':event['symbol'],'trade_date':event['trade_date'],**summary})
    except Exception as exc: counts['failed']+=1; summaries.append({'symbol':event['symbol'],'error':f'{type(exc).__name__}: {exc}'})
   partial=counts['failed'] or counts['missing'] or counts['indeterminate'] or counts['approximate']
   print(json.dumps({'run_id':'dry-run','scanned':len(events),'status':'failed' if counts['failed']==len(events) and events else 'partial' if partial else 'success',**counts,'results':summaries},ensure_ascii=False,default=str)); return 2 if counts['failed']==len(events) and events else 1 if partial else 0
  with con:
   run=repo.resumable_run(con,a.run_id,params) if a.resume else None; run_id=a.run_id if a.resume else repo.create_run(con,params)
  done=repo.completed_keys(con,run_id) if a.resume else set(); counts=Counter(); last_error=None
  for event in events:
   key=f"{event['id']}:{event['symbol']}:{event['trade_date']}"
   if key in done and not a.force: counts['skipped']+=1; continue
   try:
    components=_components(con,event); summary=aggregate(components)
    with con:
     score_id=repo.upsert_score(con,event,a.scoring_version,summary,components); repo.record_item(con,run_id,key,'success',score_id,summary)
    counts['success']+=1; counts[summary['score_status']]+=1
   except Exception as exc:
    last_error=f'{type(exc).__name__}: {exc}';
    with con: repo.record_item(con,run_id,key,'failed',error=last_error)
    counts['failed']+=1
  partial=counts['failed'] or counts['missing'] or counts['indeterminate'] or counts['approximate']
  if a.resume and not a.force and counts['skipped']==len(events):
   status=run['status']; print(json.dumps({'run_id':run_id,'status':status,**counts},ensure_ascii=False)); return 1 if status=='partial' else 2 if status=='failed' else 0
  status='failed' if counts['failed']==len(events) and events else 'partial' if partial else 'success'
  with con: repo.finish_run(con,run_id,status,len(events),counts['success'],counts['skipped'],counts['failed'],last_error)
  print(json.dumps({'run_id':run_id,'status':status,**counts},ensure_ascii=False)); return 2 if status=='failed' else 1 if status=='partial' else 0
 except (ValueError,LookupError,argparse.ArgumentError) as exc: print(f'ERROR: {exc}'); return 2
 except Exception as exc: print(f'ERROR: {type(exc).__name__}: {exc}'); return 2
if __name__=='__main__': raise SystemExit(main())
