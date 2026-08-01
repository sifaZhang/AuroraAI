import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api import industry as api
from backend.industry.score_service import build_industry_scores

class Shared:
 def __init__(self,c):self.c=c
 def __getattr__(self,n):return getattr(self.c,n)
 def close(self):pass

def db():
 c=sqlite3.connect(':memory:',check_same_thread=False);c.row_factory=sqlite3.Row;root=Path(__file__).resolve().parents[1]/'database'/'migrations'
 for n in (23,24,25):c.executescript(next(root.glob(f'{n:03d}_*.sql')).read_text(encoding='utf-8'))
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
