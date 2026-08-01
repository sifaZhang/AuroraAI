import sqlite3
from datetime import date
from pathlib import Path
from backend.industry.service import IndustryService

def db():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;r=Path(__file__).resolve().parents[1]/'database/migrations'
 for n in (23,24,25):c.executescript(next(r.glob(f'{n:03d}_*.sql')).read_text())
 for level,code,parent in ((1,'L1',None),(2,'L2','L1'),(3,'L3','L2')):c.execute("INSERT INTO industry_nodes VALUES('SW','2021',?,?,?,?,'x','n')",(code,code,level,parent))
 c.execute("INSERT INTO industry_memberships_current VALUES('SW','2021','600519.SH','L1','L1','L2','L2','L3','L3','x','n')")
 for day in ('2026-07-25','2026-07-28'):
  for level,code in ((1,'L1'),(2,'L2'),(3,'L3')):
   c.execute("INSERT INTO industry_daily_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(day,'SW','2021',code,level,8,8,8,0,0,1,1,1,1,0,0,1,0,1,0,0,0,None,None,1,1,'complete','{}','n'))
   c.execute("INSERT INTO industry_daily_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(day,'SW','2021',code,level,50,1,1,1,1,1,1,1,None,None,None,'neutral',20,1,1,1,'high','industry_score_v1','{}','n'))
 return c
def test_complete_dates_skip_middle_gap_and_previous():
 s=IndustryService(db());assert s.get_latest_score_date()==date(2026,7,28);assert s.get_previous_score_date(date(2026,7,28))==date(2026,7,25);assert s.get_next_score_date(date(2026,7,25))==date(2026,7,28)

def test_versions_and_missing_dates_are_not_complete():
 c=db();s=IndustryService(c);assert s.is_score_complete(date(2026,7,28));assert not s.is_score_complete(date(2026,7,26));assert s.get_latest_score_date('other') is None

def _effective(connection):
 return IndustryService(connection).get_effective_industry_context('600519.SH',date(2026,7,28))

def _assert_effective(value,level,code,reason):
 assert value.effective_level==level;assert value.effective_industry_code==code
 assert value.effective_score==50;assert value.effective_rank==1
 assert value.effective_confidence=='high';assert value.fallback_reason==reason

def test_effective_industry_uses_level3_when_sufficient():
 _assert_effective(_effective(db()),3,'L3',None)

def test_effective_industry_falls_back_for_level3_sample_or_coverage():
 for column,value in (('valid_bar_count',7),('coverage_ratio',.79)):
  c=db();c.execute(f"UPDATE industry_daily_snapshots SET {column}=? WHERE trade_date='2026-07-28' AND industry_code='L3'",(value,))
  _assert_effective(_effective(c),2,'L2','level3_insufficient')

def test_effective_industry_falls_back_to_level1_then_unavailable():
 c=db()
 c.execute("UPDATE industry_daily_snapshots SET valid_bar_count=7 WHERE trade_date='2026-07-28' AND industry_code IN ('L3','L2')")
 _assert_effective(_effective(c),1,'L1','level2_insufficient')
 c.execute("DELETE FROM industry_daily_scores WHERE trade_date='2026-07-28' AND industry_code='L1'")
 value=_effective(c)
 assert value.status=='unavailable';assert value.effective_level is None
 assert value.effective_industry_code is None;assert value.effective_score is None
 assert value.effective_rank is None;assert value.effective_confidence is None
 assert value.fallback_reason=='all_levels_insufficient'
