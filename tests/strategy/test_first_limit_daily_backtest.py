from decimal import Decimal
from backend.expectation_gap.database import connect,migrate
from backend.strategy.first_limit.daily_backtest import *
from backend.strategy.first_limit.backtest_metrics import *
from backend.strategy.first_limit.backtest_repository import record_exit_delay,record_exit_signal,resolve_exit
def bar(day,o=10,h=11,l=9,c=10,v=1,a=1,**kw):return Bar(day,*map(Decimal,map(str,(o,h,l,c,v,a))),**kw)
def test_proxy_entry_cost_rounding_and_unfilled_boundaries():
 e=entry(bar('d',10,10,10,10,1,1));assert e['status']=='unfilled'
 e=entry(bar('d',10,11,9,10,100,100));assert e['status']=='filled' and e['price']==Decimal('10.01') and e['shares']%100==0 and e['cost']>=5
 assert entry(bar('d',10,11,9,11,100,100,upper=11))['reason']=='limit_up_close_liquidity_unverifiable'
def test_s1_gap_intraday_and_conservative_ambiguity():
 e=entry(bar('d0',10,11,9,10,100,100));x=exit_trade(e,[bar('d1',8,9,8,8,100,100)],10);assert x['reason']=='s1_gap' and x['raw']==8
 x=exit_trade(e,[bar('d1',11,12,9,11,100,100)],10);assert x['reason']=='s1_daily_proxy'
 x=exit_trade(e,[bar('d1',10,12,9,11,100,100)],10);assert x['intraday_path_ambiguous'] and x['reason'] in {'s1_gap','s1_daily_proxy','fixed_stop'}
def test_take_trailing_time_and_untradable_delay():
 e=entry(bar('d0',10,11,9,10,100,100));assert exit_trade(e,[bar('d1',10,12,10,11,100,100)],9)['reason']=='take_profit'
 halted=Bar('x',Decimal(1),Decimal(1),Decimal(1),Decimal(1),Decimal(0),Decimal(0),suspended=True)
 assert exit_trade(e,[halted]*5,9)['status']=='open_unresolved'
 x=exit_trade(e,[bar(str(i),10,Decimal('10.5'),10,10,100,100) for i in range(10)],9);assert x['reason']=='max_holding_days' and x['holding_days']==10
def test_migration_is_idempotent(tmp_path):
 c=connect(tmp_path/'backtest.db');migrate(c);migrate(c);assert c.execute("SELECT 1 FROM sqlite_master WHERE name='backtest_runs'").fetchone()
 assert c.execute("SELECT 1 FROM sqlite_master WHERE name='backtest_exit_delays'").fetchone()
 c.execute("PRAGMA foreign_keys=ON");assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
def test_metrics_benchmark_and_portfolio_order_are_auditable():
 trades=[{'net_return':Decimal('.1')},{'net_return':Decimal('-.05')},{'net_return':None}]
 m=event_metrics(trades,10);assert m['complete_trade_count']==2 and m['sample_size_warning'] and m['annualized_status']=='unstable'
 assert benchmark_return('a','b',{'a':10,'b':11})==Decimal('.1') and benchmark_return('a','b',{}) is None
 signals=[{'event_id':n,'symbol':str(n),'daily_base_score':68+n,'pullback_score':1,'first_limit_score':1,'observation_date':'2026-01-01'} for n in range(7)]
 got,rejected,cash=admit_portfolio(signals);assert [x['event_id'] for x in got]==[6,5,4,3,2] and len(rejected)==2 and cash==Decimal('500000')

def test_record_exit_signal_contract_idempotency_conflicts_and_atomicity(tmp_path):
 c=connect(tmp_path/'record-exit-signal.db');migrate(c)
 now='2026-01-01T00:00:00+00:00'
 c.execute("INSERT INTO backtest_runs(run_id,parameters_json,status,backtest_version,backtest_scope,data_cutoff_date,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('run','{}','success','v1','daily_proxy','2026-01-01',1,now,now,now))
 def trade(terminal=None,**fields):
  signal_id=c.execute("INSERT INTO backtest_signals(run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,signal_status,signal_available_at,approximate_entry,lookahead_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('run',fields.get('event_id',100+len(list(c.execute('SELECT id FROM backtest_signals')))),1,'000001.SZ','2026-01-01','2026-01-02',1,'d','s','p','c','v1','accepted',now,1,'passed')).lastrowid
  values={'signal_id':signal_id,'entry_status':'filled','entry_reason':'entry','exit_status':'open','created_at':now,'updated_at':now,'terminal_status':terminal,**fields}
  columns=','.join(values);return c.execute(f"INSERT INTO backtest_trades({columns}) VALUES({','.join('?' for _ in values)})",tuple(values.values())).lastrowid
 def snapshot(trade_id):
  return dict(c.execute('SELECT exit_signal_date,original_exit_reason,exit_order_status,terminal_status,unresolved_reason,actual_exit_date,exit_price,gross_return,net_return FROM backtest_trades WHERE id=?',(trade_id,)).fetchone())
 normal=trade(actual_exit_date='should-stay-null',exit_price=12,gross_return=.2,net_return=.1)
 record_exit_signal(c,normal,'2026-02-03',' take_profit ');first=snapshot(normal)
 assert first=={'exit_signal_date':'2026-02-03','original_exit_reason':' take_profit ','exit_order_status':'pending','terminal_status':None,'unresolved_reason':None,'actual_exit_date':'should-stay-null','exit_price':12,'gross_return':.2,'net_return':.1}
 before_changes=c.total_changes;record_exit_signal(c,normal,'2026-02-03',' take_profit ')
 assert snapshot(normal)==first and c.total_changes==before_changes and c.execute('SELECT count(*) FROM backtest_exit_delays WHERE trade_id=?',(normal,)).fetchone()[0]==0
 for date,reason in (('2026-02-04',' take_profit '),('2026-02-03','different')):
  with __import__('pytest').raises(ValueError,match='immutable'):record_exit_signal(c,normal,date,reason)
  assert snapshot(normal)==first
 blank=trade()
 for reason in ('','   '):
  with __import__('pytest').raises(ValueError,match='required'):record_exit_signal(c,blank,'2026-02-03',reason)
  assert snapshot(blank)=={'exit_signal_date':None,'original_exit_reason':None,'exit_order_status':None,'terminal_status':None,'unresolved_reason':None,'actual_exit_date':None,'exit_price':None,'gross_return':None,'net_return':None}
 before_count=c.execute('SELECT count(*) FROM backtest_trades').fetchone()[0]
 with __import__('pytest').raises(LookupError,match='trade not found'):record_exit_signal(c,999999,'2026-02-03','stop')
 assert c.execute('SELECT count(*) FROM backtest_trades').fetchone()[0]==before_count
 closed=trade('closed',actual_exit_date='2026-02-03',exit_price=8,gross_return=-.2,net_return=-.21)
 unresolved=trade('open_unresolved',unresolved_reason='five_untradable_exit_days')
 for terminal in (closed,unresolved):
  saved=snapshot(terminal)
  with __import__('pytest').raises(ValueError,match='terminal'):record_exit_signal(c,terminal,'2026-02-03','stop')
  assert snapshot(terminal)==saved
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
 assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_record_exit_delay_contract_idempotency_conflicts_and_atomicity(tmp_path):
 c=connect(tmp_path/'record-exit-delay.db');migrate(c);now='2026-01-01T00:00:00+00:00'
 c.execute("INSERT INTO backtest_runs(run_id,parameters_json,status,backtest_version,backtest_scope,data_cutoff_date,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('run','{}','success','v1','daily_proxy','2026-01-01',1,now,now,now))
 def trade(terminal=None,signal=True,status='pending',**fields):
  signal_id=c.execute("INSERT INTO backtest_signals(run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,signal_status,signal_available_at,approximate_entry,lookahead_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('run',100+len(list(c.execute('SELECT id FROM backtest_signals'))),1,'000001.SZ','2026-01-01','2026-01-02',1,'d','s','p','c','v1','accepted',now,1,'passed')).lastrowid
  values={'signal_id':signal_id,'entry_status':'filled','exit_status':'open','created_at':now,'updated_at':now,'terminal_status':terminal,'exit_signal_date':'2026-02-03' if signal else None,'original_exit_reason':'stop' if signal else None,'exit_order_status':status if signal else None,**fields}
  return c.execute(f"INSERT INTO backtest_trades({','.join(values)}) VALUES({','.join('?' for _ in values)})",tuple(values.values())).lastrowid
 def delays(trade_id):return [tuple(row) for row in c.execute('SELECT market_date,delay_market_day_number,untradable_reason,order_status FROM backtest_exit_delays WHERE trade_id=?',(trade_id,))]
 pending=trade();record_exit_delay(c,pending,1,'2026-02-04','limit_down','pending');first=delays(pending)
 before_changes=c.total_changes;record_exit_delay(c,pending,1,'2026-02-04','limit_down','pending')
 assert delays(pending)==first and c.total_changes==before_changes
 for number,date,reason,status in ((1,'2026-02-05','limit_down','pending'),(2,'2026-02-04','limit_down','pending'),(1,'2026-02-04','other','pending'),(1,'2026-02-04','limit_down','other')):
  with __import__('pytest').raises(ValueError,match='immutable'):record_exit_delay(c,pending,number,date,reason,status)
  assert delays(pending)==first
 no_signal=trade(signal=False);not_pending=trade(status='filled')
 for blocked in (no_signal,not_pending):
  with __import__('pytest').raises(ValueError,match='pending'):record_exit_delay(c,blocked,1,'2026-02-04','limit_down')
  assert delays(blocked)==[]
 invalid=trade()
 for number,reason in ((0,'limit_down'),(6,'limit_down'),(1,''),(1,'   ')):
  with __import__('pytest').raises(ValueError):record_exit_delay(c,invalid,number,'2026-02-04',reason)
  assert delays(invalid)==[]
 with __import__('pytest').raises(LookupError,match='trade not found'):record_exit_delay(c,999999,1,'2026-02-04','limit_down')
 closed=trade('closed');unresolved=trade('open_unresolved',unresolved_reason='five_untradable_exit_days')
 for blocked in (closed,unresolved):
  with __import__('pytest').raises(ValueError,match='terminal'):record_exit_delay(c,blocked,1,'2026-02-04','limit_down')
  assert delays(blocked)==[]
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[] and c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_resolve_exit_closed_contract_idempotency_and_immutability(tmp_path):
 c=connect(tmp_path/'resolve-closed.db');migrate(c);now='2026-01-01T00:00:00+00:00'
 c.execute("INSERT INTO backtest_runs(run_id,parameters_json,status,backtest_version,backtest_scope,data_cutoff_date,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('run','{}','success','v1','daily_proxy','2026-01-01',1,now,now,now))
 def trade(signal=True,terminal=None,status='pending'):
  sid=c.execute("INSERT INTO backtest_signals(run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,signal_status,signal_available_at,approximate_entry,lookahead_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('run',100+len(list(c.execute('SELECT id FROM backtest_signals'))),1,'000001.SZ','2026-01-01','2026-01-02',1,'d','s','p','c','v1','accepted',now,1,'passed')).lastrowid
  return c.execute("INSERT INTO backtest_trades(signal_id,entry_status,exit_status,exit_signal_date,original_exit_reason,exit_order_status,terminal_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(sid,'filled','open','2026-02-03' if signal else None,'stop' if signal else None,status if signal else None,terminal,now,now)).lastrowid
 def saved(t):return dict(c.execute('SELECT exit_signal_date,original_exit_reason,actual_exit_date,exit_price,exit_delay_market_days,gross_return,net_return,terminal_status,exit_order_status,unresolved_reason FROM backtest_trades WHERE id=?',(t,)).fetchone())
 for delay in range(6):
  t=trade()
  for n in range(1,delay+1):record_exit_delay(c,t,n,f'2026-02-{3+n:02d}',f'limit_down_{n}')
  date='2026-02-03' if delay==0 else f'2026-02-{3+delay:02d}'
  result={'status':'closed','reason':'stop','date':date,'raw':10.1,'price':10.0,'exit_delay_market_days':delay};returns={'gross_return':.1,'net_return':.09,'exit_cost':5}
  resolve_exit(c,t,result,returns);first=saved(t)
  assert first=={'exit_signal_date':'2026-02-03','original_exit_reason':'stop','actual_exit_date':date,'exit_price':10,'exit_delay_market_days':delay,'gross_return':.1,'net_return':.09,'terminal_status':'closed','exit_order_status':'filled','unresolved_reason':None}
  resolve_exit(c,t,result,returns);assert saved(t)==first
  for changed in ({**result,'price':9.99},{**result,'exit_delay_market_days':delay-1 if delay else 1}):
   with __import__('pytest').raises(ValueError):resolve_exit(c,t,changed,returns)
   assert saved(t)==first
  with __import__('pytest').raises(ValueError):record_exit_signal(c,t,'2026-02-03','stop')
  with __import__('pytest').raises(ValueError):record_exit_delay(c,t,1,'2026-02-04','late')
 no_signal=trade(signal=False);before=saved(no_signal)
 with __import__('pytest').raises(ValueError,match='signal'):resolve_exit(c,no_signal,{'status':'closed','reason':'stop','date':'2026-02-03','raw':10,'price':10},{'gross_return':.1,'net_return':.09})
 assert saved(no_signal)==before
 with __import__('pytest').raises(LookupError):resolve_exit(c,999999,{'status':'closed','reason':'stop','date':'2026-02-03','raw':10,'price':10},{'gross_return':.1,'net_return':.09})
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[] and c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_resolve_exit_open_unresolved_contract_idempotency_and_immutability(tmp_path):
 c=connect(tmp_path/'resolve-unresolved.db');migrate(c);now='2026-01-01T00:00:00+00:00'
 c.execute("INSERT INTO backtest_runs(run_id,parameters_json,status,backtest_version,backtest_scope,data_cutoff_date,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('run','{}','success','v1','daily_proxy','2026-01-01',1,now,now,now))
 def trade():
  sid=c.execute("INSERT INTO backtest_signals(run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,signal_status,signal_available_at,approximate_entry,lookahead_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('run',100+len(list(c.execute('SELECT id FROM backtest_signals'))),1,'000001.SZ','2026-01-01','2026-01-02',1,'d','s','p','c','v1','accepted',now,1,'passed')).lastrowid
  return c.execute("INSERT INTO backtest_trades(signal_id,entry_status,exit_status,exit_signal_date,original_exit_reason,exit_order_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(sid,'filled','open','2026-02-03','stop','pending',now,now)).lastrowid
 def delays(t,n):
  for i in range(1,n+1):record_exit_delay(c,t,i,f'2026-02-{3+i:02d}',f'limit_down_{i}')
 def saved(t):return dict(c.execute('SELECT exit_signal_date,original_exit_reason,terminal_status,exit_order_status,exit_delay_market_days,unresolved_reason,actual_exit_date,exit_price_raw,exit_price,exit_cost,gross_return,net_return FROM backtest_trades WHERE id=?',(t,)).fetchone())
 five=trade();delays(five,5);five_result={'status':'open_unresolved','reason':'five_untradable_exit_days','exit_delay_market_days':5};resolve_exit(c,five,five_result);first=saved(five)
 assert first=={'exit_signal_date':'2026-02-03','original_exit_reason':'stop','terminal_status':'open_unresolved','exit_order_status':'unresolved','exit_delay_market_days':5,'unresolved_reason':'five_untradable_exit_days','actual_exit_date':None,'exit_price_raw':None,'exit_price':None,'exit_cost':None,'gross_return':None,'net_return':None}
 resolve_exit(c,five,five_result);assert saved(five)==first
 for bad in ({**five_result,'reason':'data_ended'},{**five_result,'exit_delay_market_days':4}):
  with __import__('pytest').raises(ValueError):resolve_exit(c,five,bad)
  assert saved(five)==first
 for n in range(5):
  cutoff=trade();delays(cutoff,n);result={'status':'open_unresolved','reason':'data_ended','exit_delay_market_days':n};resolve_exit(c,cutoff,result)
  assert saved(cutoff)['exit_delay_market_days']==n and saved(cutoff)['unresolved_reason']=='data_ended'
 for bad in ({'status':'open_unresolved','reason':'','exit_delay_market_days':0},{'status':'open_unresolved','reason':'   ','exit_delay_market_days':0},{'status':'open_unresolved','reason':'five_untradable_exit_days','exit_delay_market_days':0}):
  t=trade()
  with __import__('pytest').raises(ValueError):resolve_exit(c,t,bad)
  assert saved(t)['terminal_status'] is None
 t=trade()
 with __import__('pytest').raises(ValueError,match='cannot carry'):resolve_exit(c,t,{'status':'open_unresolved','reason':'data_ended','exit_delay_market_days':0,'price':10})
 assert saved(t)['terminal_status'] is None
 closed=trade();resolve_exit(c,closed,{'status':'closed','reason':'stop','date':'2026-02-03','raw':10,'price':10,'exit_delay_market_days':0},{'gross_return':.1,'net_return':.09})
 closed_before=saved(closed)
 with __import__('pytest').raises(ValueError):resolve_exit(c,closed,{'status':'open_unresolved','reason':'data_ended','exit_delay_market_days':0})
 assert saved(closed)==closed_before
 with __import__('pytest').raises(ValueError):record_exit_delay(c,five,1,'2026-02-04','late')
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[] and c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_resolve_exit_requires_persisted_contiguous_delay_history(tmp_path):
 c=connect(tmp_path/'resolve-delay-consistency.db');migrate(c);now='2026-01-01T00:00:00+00:00'
 c.execute("INSERT INTO backtest_runs(run_id,parameters_json,status,backtest_version,backtest_scope,data_cutoff_date,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('run','{}','success','v1','daily_proxy','2026-01-01',1,now,now,now))
 def trade():
  sid=c.execute("INSERT INTO backtest_signals(run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,signal_status,signal_available_at,approximate_entry,lookahead_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('run',100+len(list(c.execute('SELECT id FROM backtest_signals'))),1,'000001.SZ','2026-01-01','2026-01-02',1,'d','s','p','c','v1','accepted',now,1,'passed')).lastrowid
  return c.execute("INSERT INTO backtest_trades(signal_id,entry_status,exit_status,exit_signal_date,original_exit_reason,exit_order_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(sid,'filled','open','2026-02-03','stop','pending',now,now)).lastrowid
 def result(day,delay):return {'status':'closed','reason':'stop','date':day,'raw':10,'price':10,'exit_delay_market_days':delay}
 def state(t):return (dict(c.execute('SELECT terminal_status,exit_delay_market_days FROM backtest_trades WHERE id=?',(t,)).fetchone()),[tuple(r) for r in c.execute('SELECT market_date,delay_market_day_number FROM backtest_exit_delays WHERE trade_id=? ORDER BY delay_market_day_number',(t,))])
 missing=trade();record_exit_delay(c,missing,1,'2026-02-04','halt');before=state(missing)
 with __import__('pytest').raises(ValueError,match='contiguous'):resolve_exit(c,missing,result('2026-02-05',2),{'gross_return':.1,'net_return':.09})
 assert state(missing)==before
 too_few=trade();record_exit_delay(c,too_few,1,'2026-02-04','halt');record_exit_delay(c,too_few,2,'2026-02-05','halt')
 before=state(too_few)
 with __import__('pytest').raises(ValueError,match='contiguous'):resolve_exit(c,too_few,result('2026-02-03',0),{'gross_return':.1,'net_return':.09})
 assert state(too_few)==before
 wrong_date=trade();record_exit_delay(c,wrong_date,1,'2026-02-04','halt');before=state(wrong_date)
 with __import__('pytest').raises(ValueError,match='final delay'):resolve_exit(c,wrong_date,result('2026-02-05',1),{'gross_return':.1,'net_return':.09})
 assert state(wrong_date)==before
 reversed_days=trade();record_exit_delay(c,reversed_days,1,'2026-02-05','halt');record_exit_delay(c,reversed_days,2,'2026-02-04','halt');before=state(reversed_days)
 with __import__('pytest').raises(ValueError,match='dates must increase'):resolve_exit(c,reversed_days,result('2026-02-04',2),{'gross_return':.1,'net_return':.09})
 assert state(reversed_days)==before
 sixth=trade()
 with __import__('pytest').raises(ValueError,match='1..5'):record_exit_delay(c,sixth,6,'2026-02-09','halt')
 assert state(sixth)[1]==[]
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[] and c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_exit_state_machine_delay_window_and_data_cutoff_boundaries():
 ent=entry(bar('entry',10,11,9,10,100,100));locked=lambda day:bar(day,8,8,8,8,100,100,lower=8)
 delayed=exit_trade(ent,[locked('signal'),bar('d1',10,11,9,10,100,100)],9)
 assert delayed['reason']=='s1_gap_delayed' and delayed['date']=='d1' and delayed['exit_delay_market_days']==1 and delayed['price']==Decimal('9.99')
 five=exit_trade(ent,[locked('signal')]+[locked(f'd{i}') for i in range(1,6)]+[bar('never-read',10,11,9,10,100,100)],9)
 assert five=={'status':'open_unresolved','reason':'five_untradable_exit_days','holding_days':0,'exit_delay_market_days':5}
 cutoff=exit_trade(ent,[locked('signal'),locked('d1'),locked('d2')],9)
 assert cutoff['status']=='open_unresolved' and cutoff['reason']=='data_ended' and cutoff['exit_delay_market_days']==2
 time_exit=exit_trade(ent,[bar(str(i),10,Decimal('10.5'),Decimal('9.5'),10,100,100) for i in range(10)],0)
 assert time_exit['reason']=='max_holding_days' and time_exit['date']=='9' and time_exit['holding_days']==10 and time_exit['exit_delay_market_days']==0
