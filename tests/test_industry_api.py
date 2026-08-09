import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api import industry as api
from backend.industry.refresh_service import IndustryRadarRefreshResult
from backend.industry.score_service import build_industry_scores

class Shared:
 def __init__(self,c):self.c=c
 def __getattr__(self,n):return getattr(self.c,n)
 def close(self):pass

def db():
 c=sqlite3.connect(':memory:',check_same_thread=False);c.row_factory=sqlite3.Row;root=Path(__file__).resolve().parents[1]/'database'/'migrations'
 for n in (8,11,12,13,14,15,23,24,25):c.executescript(next(root.glob(f'{n:03d}_*.sql')).read_text(encoding='utf-8'))
 now='now';c.execute("INSERT INTO industry_nodes VALUES('SW','2021','801000','一级',1,NULL,'x',?)",(now,));c.execute("INSERT INTO industry_nodes VALUES('SW','2021','801010','二级',2,'801000','x',?)",(now,));c.execute("INSERT INTO industry_nodes VALUES('SW','2021','850111','三级',3,'801010','x',?)",(now,));c.execute("INSERT INTO industry_memberships_current VALUES('SW','2021','600519.SH','801000','一级','801010','二级','850111','三级','x',?)",(now,))
 values=('2026-07-30','SW','2021','801010',2,1,1,1,0,0,1,2,1,1,0,0,1,0,1,.1,0,0,None,None,100,10,'complete','{}',now)
 c.execute("INSERT INTO industry_daily_snapshots VALUES("+','.join('?'*29)+")",values);build_industry_scores(connection=c,trade_date=__import__('datetime').date(2026,7,30),levels=(2,));return c

def test_industry_api_local_queries_and_null_preservation(monkeypatch):
 c=db();monkeypatch.setattr(api,'connect',lambda:Shared(c));client=TestClient(app)
 assert client.get('/api/industry/tree').status_code==200
 listed=client.get('/api/industry/list?level=2').json();assert listed['items'][0]['industry_name']=='二级'
 assert client.get('/api/industry/detail?industry_code=801010').status_code==200
 assert client.get('/api/industry/history?industry_code=801010&start_date=2026-07-01&end_date=2026-07-31').json()['items'][0]['first_limit_count'] is None
 context=client.get('/api/industry/context?symbol=600519.SH&trade_date=2026-07-30').json();assert context['level2_score'] is not None
 assert client.get('/api/industry/constituents?industry_code=801010&level=2').json()['items'][0]['symbol']=='600519.SH'
 assert client.get('/api/industry/list?level=4').status_code==422


def test_constituents_reuse_limit_rules_and_existing_detection_results(monkeypatch):
 c=db();now='now'
 c.executemany("INSERT INTO industry_memberships_current VALUES('SW','2021',?,'801000','一级','801010','二级','850111','三级','x',?)",[(symbol,now) for symbol in ('300001.SZ','600520.SH')])
 c.executemany("INSERT INTO a_share_security_master(symbol,stock_code,exchange,board_type,security_name,listed_date,source,quality_flags,updated_at) VALUES(?,?,?,?,?,'2020-01-01','GM','[]',?)",[('600519.SH','600519','SH','MAIN','fixture',now),('300001.SZ','300001','SZ','CHINEXT','fixture',now),('600520.SH','600520','SH','MAIN','fixture',now)])
 c.executemany("INSERT INTO a_share_security_status_history VALUES(?, '2026-07-30', ?,0,0,0,'2020-01-01',NULL,'GM','[]',?)",[('600519.SH','MAIN',now),('300001.SZ','CHINEXT',now),('600520.SH','MAIN',now)])
 c.executemany("INSERT INTO a_share_daily_bars VALUES(?,?,?, ?,?, ?,?,?,'GM','none',?)",[(code,day,10,close,10,close,1,1,now) for code,day,close in [('600519','2026-07-29',10),('600519','2026-07-30',11),('300001','2026-07-29',10),('300001','2026-07-30',12),('600520','2026-07-29',10),('600520','2026-07-30',10.5)]])
 c.execute("INSERT INTO first_limit_sync_runs VALUES('run','detect','{}','success','GM',1,1,0,0,0,0,0,0,0,NULL,0,'v1',?, ?,?,?)",(now,now,now,now))
 c.execute("INSERT INTO first_limit_sync_items VALUES('run','600519.SH:2026-07-30','success','2026-07-30','2026-07-30',1,0,NULL,?,?)",(now,'{\"detection_status\": \"detected\", \"is_first_limit\": true}'))
 monkeypatch.setattr(api,'connect',lambda:Shared(c));items={x['symbol']:x for x in TestClient(app).get('/api/industry/constituents?industry_code=801010&level=2&trade_date=2026-07-30').json()['items']}
 assert items['600519.SH']['is_close_limit_up'] is True and items['600519.SH']['limit_up_price']==11.0 and items['600519.SH']['first_limit_status']=='first_limit'
 assert items['300001.SZ']['is_close_limit_up'] is True and items['300001.SZ']['limit_up_price']==12.0
 assert items['600520.SH']['is_close_limit_up'] is False and items['600520.SH']['first_limit_status']=='not_detected'


def test_refresh_route_is_registered():
 routes={(route.path,tuple(sorted(route.methods or ()))) for route in api.router.routes}
 assert ('/api/industry/refresh',('POST',)) in routes


def test_background_refresh_exposes_failure_to_status(monkeypatch):
 class Connection:
  def close(self):pass
 class FailedRefresh:
  def __init__(self,_connection):pass
  def refresh(self,**_kwargs):
   return IndustryRadarRefreshResult(None,None,None,(),(),(),(),(),False,0,0,0,0,'failed',False,False,('daily_data_coverage_insufficient',))
 monkeypatch.setattr(api,'connect',lambda:Connection())
 monkeypatch.setattr(api,'IndustryRadarRefreshService',FailedRefresh)
 monkeypatch.setitem(api._STATE,'run_status','running')
 monkeypatch.setitem(api._STATE,'last_error',None)
 api._run_refresh(api.RefreshRequest())
 assert api._STATE['run_status']=='failed'
 assert api._STATE['last_error']=='daily_data_coverage_insufficient'
