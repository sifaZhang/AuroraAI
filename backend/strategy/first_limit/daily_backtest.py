"""Pure PR6.7 v1 daily-proxy state machine; it never reads future bars."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

VERSION='daily_backtest_v1'; LOT=100; NOTIONAL=Decimal('100000'); BUY_SLIP=Decimal('.001'); SELL_SLIP=Decimal('.001')
STOP=Decimal('.07'); TAKE=Decimal('.12'); TRAIL_ON=Decimal('.08'); TRAIL_DD=Decimal('.05'); MAX_DAYS=10
@dataclass(frozen=True)
class Bar: date:str; open:Decimal; high:Decimal; low:Decimal; close:Decimal; volume:Decimal; amount:Decimal; upper:Decimal|None=None; lower:Decimal|None=None; suspended:bool=False
def d(x): return None if x is None else Decimal(str(x))
def valid(b): return b and not b.suspended and all(d(x) is not None and d(x)>0 for x in (b.open,b.high,b.low,b.close,b.volume,b.amount)) and b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high
def buy_price(raw,tick=Decimal('.01')): return (d(raw)*(1+BUY_SLIP)/tick).to_integral_value(ROUND_CEILING)*tick
def sell_price(raw,tick=Decimal('.01')): return (d(raw)*(1-SELL_SLIP)/tick).to_integral_value(ROUND_FLOOR)*tick
def fee(amount,sell=False): return max(amount*Decimal('.00025'),Decimal(5))+amount*Decimal('.00001')+(amount*Decimal('.0005') if sell else 0)
def entry(observation,tick=Decimal('.01')):
 if not valid(observation): return {'status':'unfilled','reason':'invalid_or_suspended_daily_bar'}
 if observation.upper is not None and d(observation.close)==d(observation.upper): return {'status':'unfilled','reason':'limit_up_close_liquidity_unverifiable'}
 if d(observation.open)==d(observation.high)==d(observation.low)==d(observation.close):return {'status':'unfilled','reason':'one_price_board'}
 p=buy_price(observation.close,tick); shares=(NOTIONAL/p//LOT)*LOT
 if not shares:return {'status':'unfilled','reason':'insufficient_notional_for_lot'}
 amount=p*shares; return {'status':'filled','date':observation.date,'raw':d(observation.close),'price':p,'shares':int(shares),'cost':fee(amount)}
def exit_trade(ent,bars,o0,tick=Decimal('.01'),ambiguity='conservative'):
 if ent['status']!='filled':return {'status':'unfilled'}
 high=d(ent['price']); mfe=Decimal(0); mae=Decimal(0); pending=0; holding=0; exit_order=None
 for b in bars:
  # After an exit instruction exists, no new stop/profit rules are evaluated;
  # only its sellability may be retried for five market bars.
  if exit_order is not None:
   pending+=1
   if valid(b) and not (b.lower is not None and d(b.open)==d(b.high)==d(b.low)==d(b.close)==d(b.lower)):
    name,_=exit_order;return {'status':'closed','reason':name+'_delayed','date':b.date,'raw':d(b.open),'price':sell_price(b.open,tick),'holding_days':holding,'mfe':mfe,'mae':mae,'intraday_path_ambiguous':False,'exit_delay_market_days':pending}
   if pending>=5:return {'status':'open_unresolved','reason':'five_untradable_exit_days','holding_days':holding,'exit_delay_market_days':pending}
   continue
  lower_locked=b.lower is not None and d(b.open)==d(b.high)==d(b.low)==d(b.close)==d(b.lower)
  if not valid(b):
   continue
  if lower_locked:
   # A lower-limit board can form a stop instruction, but it cannot be sold
   # on that signal day.  The following bars, not this one, are days 1..5.
   if d(b.open)<=d(o0):exit_order=('s1_gap',d(b.open))
   elif d(b.low)<=d(o0):exit_order=('s1_daily_proxy',d(o0))
   elif d(b.low)<=d(ent['price'])*(1-STOP):exit_order=('fixed_stop',d(ent['price'])*(1-STOP))
   continue
  holding+=1; loss=[]; gain=[]; p=d(ent['price']); high=max(high,d(b.high)); mfe=max(mfe,d(b.high)/p-1); mae=min(mae,d(b.low)/p-1)
  if d(b.open)<=d(o0):loss.append(('s1_gap',d(b.open)))
  elif d(b.low)<=d(o0):loss.append(('s1_daily_proxy',d(o0)))
  if d(b.low)<=p*(1-STOP):loss.append(('fixed_stop',p*(1-STOP)))
  if d(b.high)>=p*(1+TAKE):gain.append(('take_profit',p*(1+TAKE)))
  if high/p-1>=TRAIL_ON and d(b.low)<=high*(1-TRAIL_DD):gain.append(('trailing_stop',high*(1-TRAIL_DD)))
  ambiguous=bool(loss and gain)
  if ambiguous and ambiguity=='skip':return {'status':'indeterminate','reason':'intraday_path_ambiguous','holding_days':holding,'mfe':mfe,'mae':mae}
  choices=loss if loss and (ambiguity=='conservative' or not gain) else gain
  if choices:
   # The documented exit priority is deterministic; conservative mode only
   # resolves an otherwise unknowable profit-versus-loss path in favour of loss.
   name,raw=choices[0];return {'status':'closed','reason':name,'date':b.date,'raw':raw,'price':sell_price(raw,tick),'holding_days':holding,'mfe':mfe,'mae':mae,'intraday_path_ambiguous':ambiguous,'exit_delay_market_days':0}
  if holding==MAX_DAYS:
   # The time exit is an instruction at this close.  It cannot be invented
   # after data end, and any later retry uses the next tradable open.
   return {'status':'closed','reason':'max_holding_days','date':b.date,'raw':d(b.close),'price':sell_price(b.close,tick),'holding_days':holding,'mfe':mfe,'mae':mae,'intraday_path_ambiguous':False,'exit_delay_market_days':0}
 return {'status':'open_unresolved','reason':'data_ended','exit_delay_market_days':pending}
def returns(ent,ex):
 if ex.get('status')!='closed':return None
 amount=d(ent['price'])*ent['shares']; proceeds=d(ex['price'])*ent['shares']; return {'gross_return':proceeds/amount-1,'net_return':(proceeds-fee(proceeds,True)-amount-d(ent['cost']))/(amount+d(ent['cost'])),'exit_cost':fee(proceeds,True)}
