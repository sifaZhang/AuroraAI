from datetime import date,timedelta
from decimal import Decimal
import pytest
from backend.strategy.first_limit.contracts import BoardType,DataSource,SecurityStatus
from backend.strategy.first_limit.detector import Bar,Metadata,DetectionStatus,Reason,classify
from backend.strategy.first_limit.rules import normalize_symbol
from backend.expectation_gap.database import connect,connect_readonly,migrate
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
def test_future_delisted_date_does_not_exclude_historical_target():
    day=date(2026,2,1); current=SecurityStatus(normalize_symbol('600000.SH'),day,BoardType.MAIN,listed_date=date(2000,1,1),delisted_date=date(2038,1,1),source=DataSource.GM)
    assert classify('600000.SH',bar(day),meta(),current,history()).status is DetectionStatus.DETECTED
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
    totals=con.execute('SELECT success_count,skipped_count FROM first_limit_sync_runs WHERE run_id=?',(result['run_id'],)).fetchone()
    assert (totals['success_count'],totals['skipped_count'])==(1,0)
    forced=run_detection(con,[('600000.SH',day)],params,lambda *_:item,run_id=result['run_id'],resume=True,force=True); assert forced['success']==1
    bad=run_detection(con,[('000001.SZ',day)],params,lambda *_:(_ for _ in ()).throw(RuntimeError('bad')),run_id=result['run_id'],resume=True,force=True); assert bad['status']=='failed'


def test_not_first_limit_is_a_success_item_with_auditable_reason_but_no_event(tmp_path):
    con=connect(tmp_path/'negative.db'); migrate(con); day=date(2026,2,1)
    decision=classify('600000.SH',bar(day,'10.50'),meta(),status(day),history())
    value=('600000.SH',day,'v1',decision,10,11,10,10.5,10,11,None)
    result=run_detection(con,[('600000.SH',day)],{'start_date':str(day),'end_date':str(day),'symbols':['600000.SH'],'detection_version':'v1'},lambda *_:value)
    item=con.execute('SELECT status,result_json FROM first_limit_sync_items WHERE run_id=?',(result['run_id'],)).fetchone()
    assert result['status']=='success' and item['status']=='success'
    assert 'not_limit_up_close' in item['result_json']
    assert con.execute('SELECT COUNT(*) FROM first_limit_events').fetchone()[0]==0


def test_mixed_detection_only_persists_confirmed_first_limit_events(tmp_path):
    con=connect(tmp_path/'mixed.db'); migrate(con); day=date(2026,2,1)
    positive=classify('600000.SH',bar(day),meta(),status(day),history())
    negative=classify('000001.SZ',bar(day,'10.50'),meta(),status(day),history())
    results={'600000.SH':('600000.SH',day,'v1',positive,10,11,10,11,10,11,None), '000001.SZ':('000001.SZ',day,'v1',negative,10,11,10,10.5,10,11,None)}
    params={'start_date':str(day),'end_date':str(day),'symbols':sorted(results),'detection_version':'v1'}
    run=run_detection(con,[(symbol,day) for symbol in sorted(results)],params,lambda symbol,*_:results[symbol])
    assert con.execute('SELECT symbol FROM first_limit_events').fetchall()[0][0]=='600000.SH'
    assert con.execute('SELECT COUNT(*) FROM first_limit_sync_items WHERE run_id=?',(run['run_id'],)).fetchone()[0]==2


def test_event_repository_rejects_non_first_limit_decisions(tmp_path):
    con=connect(tmp_path/'event-boundary.db'); migrate(con); day=date(2026,2,1)
    negative=classify('600000.SH',bar(day,'10.50'),meta(),status(day),history())
    with pytest.raises(ValueError,match='confirmed first-limit'):
        upsert_events(con,[('600000.SH',day,'v1',negative,10,11,10,10.5,10,11,None)])


def test_dry_run_evaluates_without_creating_run_or_event(tmp_path):
    con=connect(tmp_path/'dry.db'); migrate(con); day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    value=('600000.SH',day,'v1',d,10,11,10,11,10,11,None)
    result=run_detection(con,[('600000.SH',day)],{'detection_version':'v1'},lambda *_:value,dry_run=True)
    assert result['detected']==1 and result['success']==1
    assert con.execute('select count(*) from first_limit_events').fetchone()[0]==0
    assert con.execute("select count(*) from first_limit_sync_runs where sync_type='detect'").fetchone()[0]==0
    negative=classify('000001.SZ',bar(day,'10.50'),meta(),status(day),history())
    negative_value=('000001.SZ',day,'v1',negative,10,11,10,10.5,10,11,None)
    run_detection(con,[('000001.SZ',day)],{'detection_version':'v1'},lambda *_:negative_value,dry_run=True)
    assert con.execute('select count(*) from first_limit_events').fetchone()[0]==0
    assert con.execute("select count(*) from first_limit_sync_runs where sync_type='detect'").fetchone()[0]==0
def test_versions_range_and_audit_arrays_are_preserved(tmp_path):
    con=connect(tmp_path/'versions.db'); migrate(con); day=date(2026,2,1); d=classify('600000.SH',bar(day),meta(),status(day),history())
    base=('600000.SH',day,'v1',d,10,11,10,11,10,11,None)
    upsert_events(con,[base,('600000.SH',day,'v2',d,10,11,10,11,10,11,None)])
    assert len(get_events_for_symbol(con,'600000.SH',day,day))==2
    assert len(get_events_for_date(con,day,'v1',detected_only=True))==1 and not get_indeterminate_events(con,day,'v2')


def test_readonly_connection_never_creates_missing_database(tmp_path):
    missing=tmp_path/'missing.db'
    with pytest.raises(FileNotFoundError): connect_readonly(missing)
    assert not missing.exists()


def test_readonly_connection_preserves_schema_and_business_counts(tmp_path):
    path=tmp_path/'readonly.db'; writable=connect(path); migrate(writable); writable.close()
    before=path.read_bytes(); readonly=connect_readonly(path)
    assert readonly.execute("SELECT COUNT(*) FROM first_limit_events").fetchone()[0] == 0
    readonly.close()
    assert path.read_bytes()==before
