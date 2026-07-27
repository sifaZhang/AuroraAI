from datetime import date, timedelta
from decimal import Decimal
import json
from backend.expectation_gap.database import connect,migrate
from backend.market_data.a_share_daily_repository import DailyBar,upsert_daily_bars
from backend.strategy.first_limit.detector import Bar,Metadata,classify
from backend.strategy.first_limit.contracts import BoardType,DataSource,SecurityStatus
from backend.strategy.first_limit.rules import normalize_symbol
from backend.strategy.first_limit.event_repository import upsert_events
from backend.strategy.first_limit.quality import *
from backend.strategy.first_limit.score_first_limit_quality import main

def test_position_volume_amount_turnover_boundaries_and_missing_semantics():
    assert pre_position([10]*20).score==4
    assert pre_position([10]*19+[11]).score==4
    assert pre_position([10]*19+[12]).score==2
    assert pre_position([10]*19+[Decimal('12.01')]).score==0
    assert pre_position([10]*19).status is ComponentStatus.MISSING
    assert volume_expansion([10]*5,15).score==4 and volume_expansion([10]*5,30).score==4
    assert volume_expansion([10]*5,12).score==2 and volume_expansion([10]*5,40).score==2
    assert volume_expansion([10]*5,41).score==0 and volume_expansion([0]*5,1).status is ComponentStatus.INDETERMINATE
    assert amount(200_000_000).score==2 and amount(500_000_000).score==3 and amount(199_999_999).score==0
    assert turnover(None).status is ComponentStatus.MISSING and turnover(5).score==3 and turnover(15).score==3 and turnover(25).score==2

def test_shape_and_industry_semantics_are_conservative():
    assert candle_shape(10,11,10,10.7,10).score==2
    assert candle_shape(10,11,10,10.6,10).score==0
    assert candle_shape(10,10,10,10,10).status is ComponentStatus.INDETERMINATE
    assert industry_resonance(2).score==2 and industry_resonance(1).score==0
    assert industry_resonance(None).status is ComponentStatus.INDETERMINATE
    assert industry_strength(Decimal('69.999')).score==0 and industry_strength(70).score==2
    approx=industry_strength(70,approximate=True,mapping={'sector_code':'801010'},score_date='2026-01-01')
    assert approx.status is ComponentStatus.APPROXIMATE and approx.approximate

def test_aggregate_never_treats_missing_or_approximate_as_zero():
    complete=[Component('a',ComponentStatus.SCORED,Decimal(4),Decimal(4),{}),Component('b',ComponentStatus.SCORED,Decimal(4),Decimal(4),{}),Component('c',ComponentStatus.SCORED,Decimal(3),Decimal(3),{}),Component('d',ComponentStatus.SCORED,Decimal(3),Decimal(3),{}),Component('e',ComponentStatus.SCORED,Decimal(2),Decimal(2),{}),Component('f',ComponentStatus.SCORED,Decimal(2),Decimal(2),{}),Component('g',ComponentStatus.SCORED,Decimal(2),Decimal(2),{})]
    summary=aggregate(complete); assert summary['earned_score']==20 and summary['is_complete']
    partial=aggregate([complete[0],Component('b',ComponentStatus.MISSING,None,Decimal(4),{},('missing',))]); assert partial['earned_score']==4 and partial['determinable_max_score']==4 and not partial['is_complete']
    approximate=aggregate([industry_strength(70,approximate=True)]); assert approximate['earned_score']==0 and approximate['is_approximate']

def _event_db(tmp_path):
    path=tmp_path/'quality.db'; con=connect(path); migrate(con); day=date(2026,2,1); security=normalize_symbol('600000.SH')
    status=SecurityStatus(security,day,BoardType.MAIN,source=DataSource.GM)
    def b(d,close='10',volume='10',amount='100000000'):
        return Bar(d,Decimal('10'),Decimal('11'),Decimal('10'),Decimal(close),Decimal(volume),Decimal(amount))
    metadata=Metadata(Decimal('10'),Decimal('11'),Decimal('9'))
    history=[(b(day-timedelta(days=i+1)),metadata,status) for i in range(20)][::-1]
    decision=classify('600000.SH',b(day,'11','20','500000000'),metadata,status,history)
    upsert_events(con,[('600000.SH',day,'first_limit_v1',decision,10,11,10,11,10,11,'detect-run')])
    bars=[]
    for i in range(20):
        d=day-timedelta(days=20-i); bars.append(DailyBar('600000',d,10,11,10,10,10,100000000,'SINA','none'))
    bars.append(DailyBar('600000',day,10,11,10,11,20,500000000,'SINA','none')); upsert_daily_bars(con,bars)
    return con,path

def test_only_positive_events_score_and_components_are_queryable(tmp_path,monkeypatch):
    con,path=_event_db(tmp_path); monkeypatch.setenv('EXPECTATION_DB_URL','sqlite:///'+path.as_posix())
    assert main(['--trade-date','2026-02-01','--codes','600000.SH'])==1
    score=con.execute('SELECT * FROM first_limit_quality_scores').fetchone(); assert score['earned_score']==13 and not score['is_complete']
    components=con.execute('SELECT component_key,component_status FROM first_limit_quality_components WHERE score_id=?',(score['id'],)).fetchall()
    assert len(components)==7 and dict(components)['turnover']=='missing'
    assert main(['--trade-date','2026-02-01','--codes','600000.SH','--dry-run'])==1
    assert con.execute('SELECT COUNT(*) FROM first_limit_quality_scores').fetchone()[0]==1

def test_cli_resume_force_and_version_isolation(tmp_path,monkeypatch):
    con,path=_event_db(tmp_path); monkeypatch.setenv('EXPECTATION_DB_URL','sqlite:///'+path.as_posix())
    assert main(['--trade-date','2026-02-01','--codes','600000.SH'])==1
    run=con.execute('SELECT run_id FROM first_limit_quality_runs').fetchone()[0]
    assert main(['--trade-date','2026-02-01','--codes','600000.SH','--resume','--run-id',run])==1
    assert con.execute('SELECT COUNT(*) FROM first_limit_quality_run_items WHERE run_id=?',(run,)).fetchone()[0]==1
    assert main(['--trade-date','2026-02-01','--codes','600000.SH','--scoring-version','v2'])==1
    assert con.execute('SELECT COUNT(*) FROM first_limit_quality_scores').fetchone()[0]==2
    assert main(['--trade-date','2026-02-01','--codes','600000.SH','--resume','--run-id',run,'--force'])==1

def test_non_positive_events_are_never_selected_and_migration_is_idempotent(tmp_path,monkeypatch):
    path=tmp_path/'negative.db'; con=connect(path); migrate(con); migrate(con)
    con.execute("""INSERT INTO first_limit_events(symbol,exchange,trade_date,detection_version,detection_status,is_first_limit,lookback_trading_days,observed_lookback_days,exclusion_reasons,quality_flags,detected_at,created_at,updated_at)
      VALUES('000001.SZ','SZ','2026-02-01','first_limit_v1','not_first_limit',0,20,20,'[]','[]','now','now','now')""")
    con.commit()
    monkeypatch.setenv('EXPECTATION_DB_URL','sqlite:///'+path.as_posix())
    assert main(['--trade-date','2026-02-01','--codes','000001.SZ'])==0
    assert con.execute('SELECT COUNT(*) FROM first_limit_quality_scores').fetchone()[0]==0
