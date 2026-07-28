from decimal import Decimal
from backend.expectation_gap.database import connect,migrate
from backend.strategy.first_limit.context import *

def test_industry_v1_boundaries_and_conservative_coverage():
 assert industry_trend(60).score==8 and industry_trend(30).score==2 and industry_trend(29).score==0
 assert industry_trend(60,69).status=='indeterminate'
 assert industry_breadth(25).score==5 and industry_breadth(5).score==1 and industry_breadth(4).score==0
 assert industry_limit_resonance(5,Decimal('.95')).score==3 and industry_limit_resonance(2,Decimal('.949')).status=='indeterminate'
 assert industry_rank(1,25).score==2 and industry_rank(6,25).score==1 and industry_rank(11,25).score==0
 assert industry_rank(1,24).status=='indeterminate'
 assert industry_change(10,0).score==2 and industry_change(3,0).score==1 and 'weakening' in industry_change(0,3).reasons

def test_market_v1_boundaries_and_priority():
 assert index_trend(10,10,10,'csi300').score==2
 assert index_trend(10,9,10,'csi300').score==1 and index_trend(9,10,10,'csi300').score==0
 assert market_limit_counts(60,5,Decimal('.95')).score==4
 assert market_risk(100,50,False,False).raw['state']=='extreme'
 assert market_risk(29,30,True,True).raw['state']=='extreme'
 assert market_risk(60,10,False,False).score==2

def test_stock_v1_boundaries_and_pr64_exclusion():
 vals=[10]*20
 assert stock_ma_structure(vals).score==6 and stock_ma_structure(vals).raw['pre_limit_gain_reused'] is False
 assert stock_ma20_position(10.8,10).score==2 and stock_ma20_position(10.8001,10).score==0
 assert stock_ma20_position(9.7,10).score==1 and stock_ma20_position(9.699,10).score==0
 assert stock_acceleration([10,10,10,10,10,10.6]).score==2
 assert stock_acceleration([10,10,10,10,10,10.7]).score==1
 assert stock_acceleration([10,10,10,10,10,10.95]).score==0

def test_aggregate_preserves_90_point_daily_contract_and_minute_gap():
 parts=[scored('x',20,20),scored('y',10,10),scored('z',10,10)]
 s=aggregate(parts,20,30)
 assert s['daily_base_score']==90 and s['is_complete'] and s['total_score'] is None
 assert s['minute_confirm_status']=='not_available' and s['final_candidate_level']=='pending_minute_confirmation'
 assert not aggregate([missing('x',20,'none')],20,30)['is_complete']

def test_context_migration_is_idempotent(tmp_path):
 con=connect(tmp_path/'context.db'); migrate(con); migrate(con)
 tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 assert {'first_limit_context_scores','first_limit_context_components','first_limit_context_runs','first_limit_context_run_items'}<=tables
