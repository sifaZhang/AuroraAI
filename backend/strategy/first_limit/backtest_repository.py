"""Strict version-locked PR6.7 input queries."""
from __future__ import annotations
from datetime import datetime,timezone
def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def record_exit_delay(con,trade_id,number,market_date,reason,order_status='pending'):
 if not isinstance(number,int) or not 1<=number<=5: raise ValueError('exit delay must be 1..5')
 if not isinstance(reason,str) or not reason.strip() or not isinstance(order_status,str) or not order_status.strip(): raise ValueError('exit delay reason and status are required')
 row=con.execute('SELECT exit_signal_date,exit_order_status,terminal_status FROM backtest_trades WHERE id=?',(trade_id,)).fetchone()
 if row is None: raise LookupError('trade not found')
 if row['terminal_status'] is not None: raise ValueError('cannot delay terminal trade')
 if row['exit_signal_date'] is None or row['exit_order_status']!='pending': raise ValueError('exit signal must be pending before recording delay')
 existing=con.execute('SELECT market_date,delay_market_day_number,untradable_reason,order_status FROM backtest_exit_delays WHERE trade_id=? AND (market_date=? OR delay_market_day_number=?)',(trade_id,market_date,number)).fetchone()
 if existing is not None:
  if existing['market_date']==market_date and existing['delay_market_day_number']==number and existing['untradable_reason']==reason and existing['order_status']==order_status:return
  raise ValueError('exit delay is immutable')
 con.execute("INSERT INTO backtest_exit_delays(trade_id,market_date,delay_market_day_number,untradable_reason,order_status,created_at) VALUES(?,?,?,?,?,?)",(trade_id,market_date,number,reason,order_status,_now()))
def record_exit_signal(con,trade_id,signal_date,reason):
 if not isinstance(reason,str) or not reason.strip(): raise ValueError('original exit reason is required')
 row=con.execute('SELECT exit_signal_date,original_exit_reason,terminal_status FROM backtest_trades WHERE id=?',(trade_id,)).fetchone()
 if row is None: raise LookupError('trade not found')
 if row['terminal_status'] is not None: raise ValueError('cannot overwrite terminal trade')
 if row['exit_signal_date'] is not None:
  if row['exit_signal_date']==signal_date and row['original_exit_reason']==reason: return
  raise ValueError('exit signal is immutable')
 con.execute("UPDATE backtest_trades SET exit_signal_date=?,original_exit_reason=?,exit_order_status='pending',updated_at=? WHERE id=?",(signal_date,reason,_now(),trade_id))
def resolve_exit(con,trade_id,result,returns=None):
 if not isinstance(result,dict): raise ValueError('exit result is required')
 status=result.get('status');delay=result.get('exit_delay_market_days',0)
 if not isinstance(delay,int) or not 0<=delay<=5: raise ValueError('exit delay must be 0..5')
 row=con.execute('SELECT exit_signal_date,original_exit_reason,exit_order_status,terminal_status,actual_exit_date,exit_price,exit_delay_market_days,gross_return,net_return,unresolved_reason FROM backtest_trades WHERE id=?',(trade_id,)).fetchone()
 if row is None: raise LookupError('trade not found')
 if row['exit_signal_date'] is None: raise ValueError('exit signal is required before resolving')
 reason=result.get('reason')
 if not isinstance(reason,str) or not reason.strip(): raise ValueError('exit reason is required')
 delays=con.execute('SELECT market_date,delay_market_day_number FROM backtest_exit_delays WHERE trade_id=? ORDER BY delay_market_day_number',(trade_id,)).fetchall()
 if len(delays)!=delay or [d['delay_market_day_number'] for d in delays]!=list(range(1,delay+1)): raise ValueError('persisted exit delays must be contiguous')
 if any(delays[i]['market_date']>=delays[i+1]['market_date'] for i in range(len(delays)-1)): raise ValueError('persisted exit delay dates must increase')
 if status=='open_unresolved':
  if returns is not None or set(result)-{'status','reason','exit_delay_market_days'}: raise ValueError('unresolved exit cannot carry fill fields')
  if (reason=='five_untradable_exit_days' and delay!=5) or (reason=='data_ended' and delay>4) or reason not in {'five_untradable_exit_days','data_ended'}: raise ValueError('invalid unresolved exit reason or delay')
  expected=('open_unresolved','unresolved',delay,None,None,None,None,reason)
 else:
  if status!='closed': raise ValueError('unsupported exit status')
  if not isinstance(returns,dict) or any(key not in returns for key in ('gross_return','net_return')): raise ValueError('closed exit requires gross and net returns')
  if not isinstance(result.get('date'),str) or not result['date'] or any(not isinstance(result.get(key),(int,float)) or isinstance(result[key],bool) or result[key]<=0 for key in ('raw','price')): raise ValueError('closed exit requires a positive price and date')
  if delay==0 and result['date']!=row['exit_signal_date']: raise ValueError('same-day exit must match signal date')
  if delay and result['date']!=delays[-1]['market_date']: raise ValueError('delayed exit date must match final delay date')
  expected=('closed','filled',delay,result['date'],result['price'],returns['gross_return'],returns['net_return'],None)
 if row['terminal_status'] is not None:
  actual=(row['terminal_status'],row['exit_order_status'],row['exit_delay_market_days'],row['actual_exit_date'],row['exit_price'],row['gross_return'],row['net_return'],row['unresolved_reason'])
  if actual==expected:return
  raise ValueError('exit terminal state is immutable')
 if row['exit_order_status']!='pending': raise ValueError('exit order must be pending before resolving')
 if status=='open_unresolved':
  con.execute("UPDATE backtest_trades SET exit_order_status='unresolved',terminal_status='open_unresolved',exit_delay_market_days=?,unresolved_reason=?,exit_status='open_unresolved',actual_exit_date=NULL,exit_price_raw=NULL,exit_price=NULL,exit_cost=NULL,gross_return=NULL,net_return=NULL,updated_at=? WHERE id=?",(delay,reason,_now(),trade_id))
 else:
  con.execute("UPDATE backtest_trades SET exit_order_status='filled',terminal_status='closed',exit_delay_market_days=?,unresolved_reason=NULL,exit_status='closed',exit_reason=?,actual_exit_date=?,exit_price_raw=?,exit_price=?,exit_cost=?,gross_return=?,net_return=?,updated_at=? WHERE id=?",(delay,reason,result['date'],result['raw'],result['price'],returns.get('exit_cost'),returns['gross_return'],returns['net_return'],_now(),trade_id))
def candidates(con,start,end,versions,codes=None):
 sql="""SELECT c.*,o.trading_day_offset,o.classification,o.pool_status,o.is_eliminated,o.earned_score pullback_score,
 e.id event_id,e.symbol,e.trade_date first_limit_date,e.open first_open
 FROM first_limit_context_scores c JOIN first_limit_pullback_observations o ON o.id=c.observation_id
 JOIN first_limit_events e ON e.id=c.event_id WHERE c.observation_date BETWEEN ? AND ?
 AND c.detection_version=? AND c.scoring_version=? AND c.pullback_version=? AND c.context_scoring_version=?
 AND c.is_complete=1 AND c.is_approximate=0 AND c.daily_base_score>=68 AND o.is_eliminated=0 AND o.pool_status='candidate' AND o.classification IN ('A1','A2')"""
 args=[str(start),str(end),versions['detection'],versions['quality'],versions['pullback'],versions['context']]
 if codes:sql+=' AND e.symbol IN ('+','.join('?' for _ in codes)+')';args+=codes
 rows=con.execute(sql+' ORDER BY e.id,o.observation_date',args).fetchall();seen=set();out=[]
 for r in rows:
  if r['event_id'] not in seen:out.append(r);seen.add(r['event_id'])
 return out
def bars_after(con,symbol,day):
 return con.execute("SELECT b.*,m.source_upper_limit,m.source_lower_limit,s.is_suspended FROM a_share_daily_bars b LEFT JOIN first_limit_daily_metadata m ON m.symbol=? AND m.trade_date=b.trade_date LEFT JOIN a_share_security_status_history s ON s.symbol=? AND s.trade_date=b.trade_date WHERE b.stock_code=? AND b.adjustment='none' AND b.trade_date>=? ORDER BY b.trade_date",(symbol,symbol,symbol.split('.')[0],str(day))).fetchall()
