from datetime import date
from types import SimpleNamespace
import backend.strategy.first_limit.pipeline_service as pipeline
from backend.strategy.first_limit.tushare_price_limits import PriceLimit

class _Con:
 def execute(self,*_,**__): raise AssertionError('database is not used by limit_detection setup')
def _context():
 return SimpleNamespace(connection=_Con(),parameters={},day=date(2026,1,3),job_id=1,
  plan=lambda:{'required_start':'2026-01-01','d0_dates':['2026-01-01','2026-01-02']},
  symbols=lambda:[SimpleNamespace(canonical='600000.SH'),SimpleNamespace(canonical='000001.SZ')])
def test_memory_limits_are_loaded_once_and_passed(monkeypatch):
 calls=[]; captured={}; limits={('600000.SH',date(2026,1,1)):PriceLimit('600000.SH',date(2026,1,1),None,None,None)}
 monkeypatch.setattr(pipeline,'DataSourceSettings',SimpleNamespace(from_env=lambda:SimpleNamespace(tushare_token='x')),raising=False)
 monkeypatch.setattr('backend.data_sources.settings.DataSourceSettings.from_env',lambda:SimpleNamespace(tushare_token='x'))
 monkeypatch.setattr('backend.strategy.first_limit.tushare_price_limits.load_price_limits',lambda client,days:(calls.append(days) or limits,{}))
 monkeypatch.setattr(pipeline,'detect_first_limits',lambda con,**kw:(captured.update(kw) or {'status':'success'}))
 result=pipeline.DefaultExecutor().run_step('limit_detection',_context())
 assert calls==[[date(2026,1,1),date(2026,1,2)]] and captured['price_limits'] is limits
 assert result['loaded_rows']==1 and result['failed_dates']=={}
