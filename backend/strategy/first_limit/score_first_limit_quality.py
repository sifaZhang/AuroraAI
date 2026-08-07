"""Offline PR6.4 first-limit quality scoring CLI; never syncs data or uses network."""
from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import date
from backend.expectation_gap.database import connect,connect_readonly,migrate
from .rules import normalize_symbol
from .quality import pre_position,volume_expansion,amount,turnover,candle_shape,industry_resonance,industry_strength,aggregate
from . import quality_repository as repo
from backend.industry.service import IndustryService

MAX_DAYS=31
def parser():
 p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True); g.add_argument('--trade-date'); g.add_argument('--start-date')
 p.add_argument('--end-date'); p.add_argument('--codes'); p.add_argument('--detection-version',default='first_limit_v1'); p.add_argument('--scoring-version',default='first_limit_quality_v1'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--resume',action='store_true'); p.add_argument('--run-id'); p.add_argument('--max-symbols',type=int); p.add_argument('--force',action='store_true'); return p
def _date(value): return date.fromisoformat(value)
def _components(connection,event):
 prior,target=repo.daily_inputs(connection,event)
 if target is None: raise LookupError('missing target daily bar')
 closes=[r['close'] for r in prior]; volumes=[r['volume'] for r in prior[-5:]]
 # The quality score is a T0 fact.  Do not use the legacy sector membership
 # tables here: IndustryService applies the formal 3 -> 2 -> 1 fallback.
 effective=IndustryService(connection).get_effective_industry_context(event['symbol'],event['trade_date'])
 snapshot=(IndustryService(connection).get_industry_snapshot(event['trade_date'],effective.effective_industry_code)
           if effective.effective_industry_code else None)
 mapping_payload={'effective_industry_level':effective.effective_level,
  'effective_industry_code':effective.effective_industry_code,
  'industry_score_date':event['trade_date'],'industry_score':effective.effective_score,
  'industry_rank':effective.effective_rank,'industry_score_version':'industry_score_v1',
  'membership_mode':'industry_memberships_current','fallback_reason':effective.fallback_reason,
  'status':effective.status}
 count=None if snapshot is None else snapshot.get('first_limit_count')
 return [pre_position(closes),volume_expansion(volumes,target['volume']),amount(target['amount']),turnover(None),candle_shape(target['open'],target['high'],target['low'],target['close'],event['pre_close']),industry_resonance(count,mapping=mapping_payload),industry_strength(effective.effective_score,approximate=False,mapping=mapping_payload,score_date=event['trade_date'])]
def score_first_limit_quality(con, *, start, end, symbols=None,
                              detection_version='first_limit_v1',
                              scoring_version='first_limit_quality_v1',
                              run_id=None, resume=False, force=False, dry_run=False,
                              max_items=None):
 events=repo.list_scoreable_events(con,start,end,detection_version,symbols)
 if max_items is not None: events=events[:max_items]
 params={'start_date':str(start),'end_date':str(end),'symbols':symbols,'detection_version':detection_version,'scoring_version':scoring_version}
 if dry_run:
  counts=Counter(); summaries=[]
  for event in events:
   try: summary=aggregate(_components(con,event)); counts['success']+=1; counts[summary['score_status']]+=1; summaries.append({'symbol':event['symbol'],'trade_date':event['trade_date'],**summary})
   except Exception as exc: counts['failed']+=1; summaries.append({'symbol':event['symbol'],'error':f'{type(exc).__name__}: {exc}'})
  partial=counts['failed'] or counts['missing'] or counts['indeterminate'] or counts['approximate']
  return {'run_id':'dry-run','scanned':len(events),'status':'failed' if counts['failed']==len(events) and events else 'partial' if partial else 'success',**counts,'results':summaries}
 with con:
  run=repo.resumable_run(con,run_id,params) if resume else None
  selected_run=run_id if resume else repo.create_run(con,params)
 done=repo.completed_keys(con,selected_run) if resume else set(); counts=Counter(); last_error=None
 for event in events:
  key=f"{event['id']}:{event['symbol']}:{event['trade_date']}"
  if key in done and not force: counts['skipped']+=1; continue
  try:
   components=_components(con,event); summary=aggregate(components)
   with con:
    score_id=repo.upsert_score(con,event,scoring_version,summary,components); repo.record_item(con,selected_run,key,'success',score_id,summary)
   counts['success']+=1; counts[summary['score_status']]+=1
  except Exception as exc:
   last_error=f'{type(exc).__name__}: {exc}'
   with con: repo.record_item(con,selected_run,key,'failed',error=last_error)
   counts['failed']+=1
 partial=counts['failed'] or counts['missing'] or counts['indeterminate'] or counts['approximate']
 if resume and not force and counts['skipped']==len(events):
  return {'run_id':selected_run,'status':run['status'],**counts}
 status='failed' if counts['failed']==len(events) and events else 'partial' if partial else 'success'
 with con: repo.finish_run(con,selected_run,status,len(events),counts['success'],counts['skipped'],counts['failed'],last_error)
 return {'run_id':selected_run,'status':status,**counts}

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
  result=score_first_limit_quality(con,start=start,end=end,symbols=symbols,detection_version=a.detection_version,scoring_version=a.scoring_version,run_id=a.run_id,resume=a.resume,force=a.force,dry_run=a.dry_run,max_items=a.max_symbols)
  print(json.dumps(result,ensure_ascii=False,default=str)); return 2 if result['status']=='failed' else 1 if result['status']=='partial' else 0
 except (ValueError,LookupError,argparse.ArgumentError) as exc: print(f'ERROR: {exc}'); return 2
 except Exception as exc: print(f'ERROR: {type(exc).__name__}: {exc}'); return 2
if __name__=='__main__': raise SystemExit(main())
