"""Shared-ledger orchestration for local PR6.3 detection batches."""
from __future__ import annotations
from collections import Counter
from typing import Callable,Iterable,Mapping
from .sync_repository import create_run,get_resumable_run,completed_item_keys,record_item,finish_run
from .event_repository import upsert_events

def _result_payload(decision):
    return {
        'detection_status': decision.status.value,
        'is_first_limit': decision.is_first_limit,
        'reasons': sorted(reason.value for reason in decision.reasons),
        'quality_flags': sorted(decision.quality_flags),
    }

def run_detection(connection, items: Iterable[tuple[str,object]], parameters: Mapping[str,object], decide: Callable[[str,object],tuple], *, run_id=None,resume=False,force=False,dry_run=False):
    """Run isolated symbols; each result is persisted atomically with its ledger item."""
    values=list(items)
    if dry_run:
        counts=Counter()
        for symbol, day in values:
            try:
                result=decide(symbol,day)
                counts['success'] += 1
                counts[result[3].status.value] += 1
            except Exception:
                counts['failed'] += 1
        status='failed' if counts['failed']==len(values) and values else ('partial' if counts['failed'] or counts['indeterminate'] else 'success')
        return {'run_id':'dry-run','status':status,'scanned':len(values),**counts}
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
                if result[3].is_first_limit:
                    upsert_events(connection,[result])
                record_item(connection,run_id,key,'success',planned_start=day,planned_end=day,row_count=1,
                            result=_result_payload(result[3]),commit=False)
            counts['success']+=1
            counts[result[3].status.value]+=1
        except Exception as exc:
            record_item(connection,run_id,key,'failed',planned_start=day,planned_end=day,error=f'{type(exc).__name__}: {exc}')
            counts['failed']+=1; failures.append(str(exc))
    if resume and not force and counts['skipped']==len(values):
        return {'run_id':run_id,'status':get_resumable_run(connection,run_id,'detect',parameters)['status'],**counts}
    status='failed' if counts['failed']==len(values) else ('partial' if counts['failed'] or counts['indeterminate'] else 'success')
    finish_run(connection,run_id,status=status,planned_count=len(values),success_count=counts['success'],skipped_count=counts['skipped'],failure_count=counts['failed'],inserted_rows=counts['success'],last_error=failures[-1] if failures else None)
    return {'run_id':run_id,'status':status,**counts}
