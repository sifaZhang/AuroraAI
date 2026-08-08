from backend.dividend.high_dividend_watch_scanner import _fetch_dividend_events_with_isolation
class P:
 def __init__(self,fail=False):self.calls=[];self.fail=fail
 def fetch_events(self,s):
  self.calls.append(tuple(s))
  if self.fail and len(s)>1:raise RuntimeError('batch')
  return []
def test_batch_first_success_calls_once():
 p=P();_fetch_dividend_events_with_isolation(p,['A','B']);assert p.calls==[('A','B')]
def test_batch_failure_uses_single_symbol_fallback():
 p=P(True);_fetch_dividend_events_with_isolation(p,['A','B']);assert p.calls==[('A','B'),('A',),('B',)]
