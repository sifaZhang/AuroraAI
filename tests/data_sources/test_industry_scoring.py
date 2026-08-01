import json,sqlite3
from datetime import date
from pathlib import Path
import pytest
from backend.industry.scoring import score_cross_section,activity_state
from backend.industry.repository import IndustryScoreRepository

def row(code,ret,median,limit=0,turnover=100):
 return {'trade_date':'2026-07-30','classification':'SW','classification_version':'2021','industry_code':code,'industry_level':2,'equal_weight_return':ret,'median_return':median,'rise_ratio':.6,'strong_rise_ratio':.3,'strong_rise_count':3,'valid_bar_count':10,'limit_up_count':limit,'first_limit_count':None,'broken_limit_count':None,'turnover_amount':turnover,'median_turnover_amount':10,'coverage_ratio':1,'data_status':'complete'}
def history(n=20,amount=100):return [dict(row('X',1,1),trade_date=f'2026-06-{i+1:02d}',turnover_amount=amount,median_turnover_amount=10) for i in range(n)]

def test_percentiles_ranking_null_evidence_and_score_range():
 scores=score_cross_section([row('B',-1,-1),row('A',2,1)],{'A':[],'B':[]})
 assert [x.industry_code for x in scores]==['A','B'];assert scores[0].rank_in_level==1
 assert scores[0].strength_score==25 and scores[1].strength_score==0
 assert json.loads(scores[0].evidence_json)['first_limit_count'] is None
 assert all(0<=x.total_score<=100 for x in scores)

@pytest.mark.parametrize(('limits','expected'),[(0,0),(1,1),(2,3),(3,5)])
def test_limit_linkage_points(limits,expected):
 score=score_cross_section([row('A',1,1,limits)],{'A':[]})[0]
 assert score.limit_score==pytest.approx(min(10,limits/10/.1*10)+expected)

def test_activity_states_history_and_large_cap_protection():
 assert activity_state(1,1.2,True)=='volume_up_price_up'
 assert activity_state(1,.8,True)=='volume_down_price_up'
 assert activity_state(-1,1.2,True)=='volume_up_price_down'
 assert activity_state(-1,.8,True)=='volume_down_price_down'
 assert activity_state(0,1,True)=='neutral'
 score=score_cross_section([row('A',1,1,turnover=200)],{'A':history(20,100)})[0]
 assert score.turnover_ratio_5d==2 and score.turnover_ratio_20d==2
 assert score.median_turnover_ratio_20d==1 and score.activity_score==15

def test_score_repository_versions_coexist_and_are_idempotent():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;root=Path(__file__).resolve().parents[2]/'database'/'migrations'
 for n in (23,24,25):c.executescript(next(root.glob(f'{n:03d}_*.sql')).read_text(encoding='utf-8'))
 c.execute("INSERT INTO industry_nodes VALUES('SW','2021','A','A',2,'P','x','now')") if False else None
 # Disable foreign keys in this isolated repository contract fixture.
 score=score_cross_section([row('A',1,1)],{'A':[]})[0];repo=IndustryScoreRepository(c)
 assert repo.replace_scores_for_date([score])==1
 assert repo.replace_scores_for_date([score])==0
 assert repo.get_score(date(2026,7,30),'A')==score
