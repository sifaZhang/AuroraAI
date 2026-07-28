from decimal import Decimal
from backend.expectation_gap.database import connect,migrate
from backend.strategy.first_limit.pullback import *

def test_a1_a2_deep_eliminated_and_overlap_rules():
    assert classify(10,12,[11])[0]=='A1'
    assert classify(10,12,[Decimal('10.7')])[0]=='A2'
    assert classify(10,12,[Decimal('10.2')])[0]=='DEEP_WATCH'
    assert classify(10,12,[Decimal('9.99')])[0]=='ELIMINATED'
    assert classify(10,10,[10])[0]=='INDETERMINATE'

def test_key_support_boundaries_and_missing_not_failure():
    assert support(10,[10],[10]).score==6
    assert support(10,[Decimal('9.9')],[10]).score==3
    assert support(10,[Decimal('9.89')],[10]).score==0
    assert support(10,[None],[10]).status=='missing'

def test_volume_close_location_rhythm_and_partial_coverage():
    first,continuous=volume(100,50,[80,40]); assert first.score==6 and continuous.score==3
    assert volume(100,70,[80,40])[0].score==4
    assert volume(0,20,[20]).status=='missing'
    assert close_location(10,12,Decimal('11.3')).score==4
    assert close_location(10,10,10).status=='missing'
    assert rhythm([10,Decimal('9.5'),Decimal('9.1')]).score==3
    summary=aggregate([first,missing('industry',2,'no_history')]); assert summary['earned_score']==6 and summary['determinable_max_score']==6 and not summary['is_complete']

def test_migration_is_idempotent_and_pullback_tables_are_isolated(tmp_path):
    con=connect(tmp_path/'pullback.db'); migrate(con); migrate(con)
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'first_limit_pullback_observations','first_limit_pullback_components','first_limit_pullback_runs','first_limit_pullback_run_items'}<=tables

def test_v1_drawdown_c0_volume_risk_and_ma5_boundaries():
    assert max_drawdown(100,[88]).status=='pass'
    assert max_drawdown(100,[Decimal('87.99')]).status=='fail'
    assert max_drawdown(None,[88]).status=='missing'
    assert close_to_c0(100,97).status=='pass' and close_to_c0(100,100).status=='pass'
    assert close_to_c0(100,Decimal('96.99')).status=='fail' and close_to_c0(100,101).status=='fail'
    risk=volume_risks(100,95,100,150,[100]*5); assert risk.status=='fail' and {'obvious_volume_decline','volume_long_bearish'}<=set(risk.reasons)
    assert volume_risks(100,100,100,1,[0]*5).status=='indeterminate'
    assert ma5_status([10,10,10,10,10,11]).raw['state']=='reclaimed'
