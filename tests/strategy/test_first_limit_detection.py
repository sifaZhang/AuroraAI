from datetime import date,timedelta
from decimal import Decimal
from backend.strategy.first_limit.contracts import BoardType,DataSource,SecurityStatus
from backend.strategy.first_limit.detector import Bar,Metadata,DetectionStatus,Reason,classify
from backend.strategy.first_limit.rules import normalize_symbol
from backend.expectation_gap.database import connect,migrate
from backend.strategy.first_limit.event_repository import upsert_events,get_events_for_date,get_events_for_symbol,get_indeterminate_events
from backend.strategy.first_limit.detection_runs import run_detection
def bar(day, close='11.00', **k): return Bar(day,Decimal(k.get('open','10')),Decimal(k.get('high',close)),Decimal(k.get('low','10')),Decimal(close),Decimal(k.get('volume','100')),Decimal('1000'))
def meta(): return Metadata(Decimal('10'),Decimal('11'),Decimal('9'))
def status(day): return SecurityStatus(normalize_symbol('600000.SH'),day,BoardType.MAIN,source=DataSource.GM)
def history(n=20):
    start=date(2026,1,1); return [(bar(start+timedelta(days=i),'10.00'),meta(),status(start+timedelta(days=i))) for i in range(n)]
def test_detects_first_limit_with_complete_twenty_day_window():
    day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    assert d.status is DetectionStatus.DETECTED and d.is_first_limit and not d.is_one_word_limit
def test_missing_history_is_indeterminate_not_first_limit():
    day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history(19))
    assert d.status is DetectionStatus.INDETERMINATE and Reason.HISTORICAL_INCOMPLETE in d.reasons
def test_intraday_touch_is_not_close_limit():
    day=date(2026,2,1); d=classify('600000.SH',bar(day,'10.99',high='11.00'),meta(),status(day),history())
    assert d.status is DetectionStatus.NOT_FIRST_LIMIT and d.touched_upper_limit
def test_historical_st_is_excluded_without_using_current_name():
    day=date(2026,2,1); st=SecurityStatus(normalize_symbol('600000.SH'),day,BoardType.MAIN,is_st=True,source=DataSource.GM)
    d=classify('600000.SH',bar(day),meta(),st,history())
    assert d.status is DetectionStatus.EXCLUDED and Reason.INELIGIBLE_SECURITY in d.reasons
def test_previous_limit_and_one_word_are_explicit_exclusions():
    day=date(2026,2,1); h=history(); last=h[-1]; h[-1]=(bar(last[0].trade_date,'11.00'),meta(),last[2])
    d=classify('600000.SH',bar(day,'11.00',open='11.00',high='11.00',low='11.00'),meta(),status(day),h)
    assert d.status is DetectionStatus.EXCLUDED and {Reason.PREVIOUS_LIMIT_UP,Reason.CONSECUTIVE,Reason.ONE_WORD} <= d.reasons
def test_event_migration_and_versioned_upsert_are_idempotent(tmp_path):
    con=connect(tmp_path/'events.db'); migrate(con); day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    row=('600000.SH',day,'v1',d,10,11,10,11,10,11,'run')
    assert upsert_events(con,[row])==1 and upsert_events(con,[row])==1
    assert len(get_events_for_date(con,day,'v1',detected_only=True))==1
def test_shared_detect_run_resume_force_and_failure_isolation(tmp_path):
    con=connect(tmp_path/'runs.db'); migrate(con); day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    item=('600000.SH',day,'v1',d,10,11,10,11,10,11,None); params={'start_date':str(day),'end_date':str(day),'symbols':['600000.SH'],'detection_version':'v1'}
    result=run_detection(con,[('600000.SH',day)],params,lambda *_:item); assert result['status']=='success'
    again=run_detection(con,[('600000.SH',day)],params,lambda *_:item,run_id=result['run_id'],resume=True); assert again['skipped']==1
    forced=run_detection(con,[('600000.SH',day)],params,lambda *_:item,run_id=result['run_id'],resume=True,force=True); assert forced['success']==1
    bad=run_detection(con,[('000001.SZ',day)],params,lambda *_:(_ for _ in ()).throw(RuntimeError('bad')),run_id=result['run_id'],resume=True,force=True); assert bad['status']=='failed'
def test_versions_range_and_audit_arrays_are_preserved(tmp_path):
    con=connect(tmp_path/'versions.db'); migrate(con); day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    ind=classify('600000.SH',bar(day),meta(),status(day),history(1))
    base=('600000.SH',day,'v1',d,10,11,10,11,10,11,None)
    upsert_events(con,[base,('600000.SH',day,'v2',ind,10,11,10,11,10,11,None)])
    assert len(get_events_for_symbol(con,'600000.SH',day,day))==2
    assert len(get_events_for_date(con,day,'v1',detected_only=True))==1 and len(get_indeterminate_events(con,day,'v2'))==1
