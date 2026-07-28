"""Offline PR6.6 daily context scoring CLI. It never downloads data or minute bars."""
from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import date
from backend.expectation_gap.database import connect,connect_readonly,migrate
from .rules import normalize_symbol
from . import context_repository as repo
from .context import *

def parser():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--observation-date');g.add_argument('--start-date');p.add_argument('--end-date');p.add_argument('--codes');p.add_argument('--detection-version',default='first_limit_v1');p.add_argument('--scoring-version',default='first_limit_quality_v1');p.add_argument('--pullback-version',default='first_limit_pullback_v1');p.add_argument('--context-scoring-version',default=VERSION);p.add_argument('--dry-run',action='store_true');p.add_argument('--resume',action='store_true');p.add_argument('--run-id');p.add_argument('--max-symbols',type=int);p.add_argument('--force',action='store_true');return p
def components(con,row):
 vals=[r['close'] for r in repo.closes(con,row['symbol'],row['observation_date'])]
 # Historical SW mapping and index series are intentionally not substituted with snapshots.
 return [missing('industry_mapping',20,'historical_sw_l1_mapping_unavailable'),missing('market_context',10,'historical_market_index_or_limit_set_unavailable'),stock_ma_structure(vals),stock_ma20_position(vals[-1],sum(vals[-20:])/20) if len(vals)>=20 else missing('stock_ma20_position',2,'requires_20_valid_closes'),stock_acceleration(vals)]
def main(argv=None):
 try:
  a=parser().parse_args(argv); start=date.fromisoformat(a.observation_date or a.start_date);end=date.fromisoformat(a.observation_date or a.end_date) if (a.observation_date or a.end_date) else None
  if end is None or end<start:raise ValueError('--end-date is required and must not precede --start-date')
  if a.resume and not a.run_id:raise ValueError('--resume requires --run-id')
  if a.dry_run and (a.resume or a.run_id):raise ValueError('dry-run cannot resume or create a run')
  syms=sorted(normalize_symbol(x).canonical for x in a.codes.split(',')) if a.codes else None
  if syms is None and a.max_symbols is None:raise ValueError('provide --codes or --max-symbols')
  con=connect_readonly() if a.dry_run else connect()
  if not a.dry_run:migrate(con)
  rows=repo.observations(con,start,end,a.detection_version,a.scoring_version,a.pullback_version,syms); rows=rows[:a.max_symbols] if a.max_symbols is not None else rows
  params={'start_date':str(start),'end_date':str(end),'symbols':syms,'detection_version':a.detection_version,'scoring_version':a.scoring_version,'pullback_version':a.pullback_version,'context_scoring_version':a.context_scoring_version}
  if a.dry_run:
   result=[]; counts=Counter()
   for r in rows:
    s=aggregate(components(con,r),r['first_limit_score'],r['earned_score']);counts[s['score_status']]+=1;result.append({'symbol':r['symbol'],**s})
   print(json.dumps({'run_id':'dry-run','planned':len(rows),'results':result,**counts},ensure_ascii=False,default=str));return 1 if rows and any(k in counts for k in ('missing','indeterminate','approximate','partial')) else 0
  with con: rid=a.run_id if a.resume else repo.create_run(con,params); run=repo.resume_run(con,rid,params) if a.resume else None
  done=repo.done(con,rid) if a.resume else set(); counts=Counter()
  for r in rows:
   key=f"{r['id']}:{r['symbol']}:{r['observation_date']}"
   if key in done and not a.force:counts['skipped']+=1;continue
   try:
    s=aggregate(components(con,r),r['first_limit_score'],r['earned_score'])
    with con:sid=repo.save(con,r,a.context_scoring_version,s,components(con,r));repo.item(con,rid,key,'success',sid,s)
    counts['success']+=1; counts[s['score_status']]+=1
   except Exception as e:
    with con:repo.item(con,rid,key,'failed',error=f'{type(e).__name__}: {e}');counts['failed']+=1
  status='failed' if rows and counts['failed']==len(rows) else 'partial' if counts['failed'] or counts['missing'] or counts['indeterminate'] or counts['approximate'] else 'success'
  with con:repo.finish(con,rid,status,len(rows),counts['success'],counts['skipped'],counts['failed'],counts['indeterminate']+counts['missing']+counts['approximate'])
  print(json.dumps({'run_id':rid,'status':status,**counts},ensure_ascii=False));return 2 if status=='failed' else 1 if status=='partial' else 0
 except (ValueError,LookupError) as e:print('ERROR:',e);return 2
 except Exception as e:print('ERROR:',type(e).__name__,e);return 2
if __name__=='__main__':raise SystemExit(main())
