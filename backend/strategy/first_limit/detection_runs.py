"""Shared-ledger orchestration for local PR6.3 detection batches."""
from __future__ import annotations
from collections import Counter
from typing import Callable,Iterable,Mapping
from .sync_repository import create_run,get_resumable_run,completed_item_keys,record_item,finish_run
from .event_repository import upsert_events

def run_detection(connection, items: Iterable[tuple[str,object]], parameters: Mapping[str,object], decide: Callable[[str,object],tuple], *, run_id=None,resume=False,force=False,dry_run=False):
    """Run isolated symbols; each result is persisted atomically with its ledger item."""
    values=list(items)
    if dry_run:
        return {'run_id':'dry-run','status':'success','scanned':len(values),'failed':0,'indeterminate':0}
    if resume:
        if not run_id: raise ValueError('--resume requires --run-id')
        get_resumable_run(connection,run_id,'detect',parameters); done=completed_item_keys(connection,run_id)
    else:
        run_id=create_run(connection,'detect',parameters,run_id=run_id); done=set()
    counts=Counter(); failures=[]
    for symbol, day in values:
        key=f'{symbol}:{day}'
        if key in done and not force: counts['skipped']+=1; continue
        try:
            result=decide(symbol,day) # event_repository tuple
            result=(*result[:-1], run_id)
            with connection:
                upsert_events(connection,[result]); record_item(connection,run_id,key,'success',planned_start=day,planned_end=day,row_count=1)
            counts['success']+=1
            counts[result[3].status.value]+=1
        except Exception as exc:
            record_item(connection,run_id,key,'failed',planned_start=day,planned_end=day,error=f'{type(exc).__name__}: {exc}')
            counts['failed']+=1; failures.append(str(exc))
    status='failed' if counts['failed']==len(values) else ('partial' if counts['failed'] or counts['indeterminate'] else 'success')
    finish_run(connection,run_id,status=status,planned_count=len(values),success_count=counts['success'],skipped_count=counts['skipped'],failure_count=counts['failed'],inserted_rows=counts['success'],last_error=failures[-1] if failures else None)
    return {'run_id':run_id,'status':status,**counts}
