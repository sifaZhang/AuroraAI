"""PR6.5 pure pullback observation and 30-point quality rules."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

VERSION='first_limit_pullback_v1'; MAX=Decimal(30)
MAX_DRAWDOWN=Decimal('.12'); CLOSE_TO_C0_LOW=Decimal('-.03'); CLOSE_TO_C0_HIGH=Decimal('0'); RISK_VOLUME_RATIO=Decimal('1.5')
@dataclass(frozen=True)
class Component: key:str; status:str; score:Decimal|None; maximum:Decimal; raw:dict; reasons:tuple=(); approximate:bool=False
def d(v): return None if v is None else Decimal(str(v))
def comp(key,score,maximum,raw): return Component(key,'scored' if score else 'zero_score',Decimal(score),Decimal(maximum),raw)
def missing(key,maxi,reason): return Component(key,'missing',None,Decimal(maxi),{},(reason,))
def classify(o0,c0,lows,*,b_flat=False):
 o,c=d(o0),d(c0); vals=[d(x) for x in lows]
 if o is None or c is None or c<=o or not vals or any(x is None for x in vals): return 'INDETERMINATE',('invalid_entity_or_lows',)
 low=min(vals)
 if low<o:return 'ELIMINATED',('intraday_below_o0',)
 ratio=(low-o)/(c-o)
 if Decimal('.40')<=ratio<=Decimal('.60'): return 'A1',()
 if Decimal('.23')<=ratio<=Decimal('.43'): return 'A2',()
 if ratio<Decimal('.23'): return 'DEEP_WATCH',()
 return ('B',()) if b_flat else ('INDETERMINATE',('outside_a1_a2_without_configured_b',))
def max_drawdown(c0,lows):
 c=d(c0); vals=[d(x) for x in lows]
 if c is None or c<=0 or not vals or any(x is None or x<=0 for x in vals): return missing('max_drawdown',0,'missing_or_invalid_drawdown_input')
 value=(c-min(vals))/c; return Component('max_drawdown','pass' if value<=MAX_DRAWDOWN else 'fail',None,Decimal(0),{'value':str(value),'threshold':str(MAX_DRAWDOWN)},('max_drawdown_exceeded',) if value>MAX_DRAWDOWN else ())
def close_to_c0(c0,close):
 c,n=d(c0),d(close)
 if c is None or n is None or c<=0:return missing('close_to_c0',0,'missing_or_invalid_c0')
 value=(n-c)/c; return Component('close_to_c0','pass' if CLOSE_TO_C0_LOW<=value<=CLOSE_TO_C0_HIGH else 'fail',None,Decimal(0),{'value':str(value),'low':str(CLOSE_TO_C0_LOW),'high':str(CLOSE_TO_C0_HIGH)})
def volume_risks(open_,close,prior_close,volume,prior_volumes):
 o,c,p,v=map(d,(open_,close,prior_close,volume)); vals=[d(x) for x in prior_volumes]
 if None in (o,c,p,v) or p<=0 or len(vals)!=5 or any(x is None or x<0 for x in vals):return missing('volume_risk',0,'requires_5_valid_prior_volumes')
 avg=sum(vals)/5
 if avg==0:return Component('volume_risk','indeterminate',None,Decimal(0),{},('zero_prior_average_volume',))
 change=(c-p)/p; ratio=v/avg; obvious=c<p and change<=Decimal('-.03') and ratio>=RISK_VOLUME_RATIO; long=c<o and change<=Decimal('-.05') and (o-c)/o>=Decimal('.04') and ratio>=RISK_VOLUME_RATIO
 reasons=tuple(name for name,matched in (('obvious_volume_decline',obvious),('volume_long_bearish',long)) if matched)
 return Component('volume_risk','fail' if reasons else 'pass',None,Decimal(0),{'change':str(change),'volume_ratio':str(ratio),'obvious_volume_decline':obvious,'volume_long_bearish':long},reasons)
def ma5_status(closes):
 vals=[d(x) for x in closes]
 if len(vals)<6 or any(x is None for x in vals):return Component('ma5','missing',None,Decimal(0),{},('requires_6_closes',))
 prev=sum(vals[-6:-1])/5; now=sum(vals[-5:])/5
 state='reclaimed' if vals[-2]<=prev and vals[-1]>now else 'already_above' if vals[-2]>prev and vals[-1]>now else 'below'
 return Component('ma5','pass',None,Decimal(0),{'previous_ma5':str(prev),'current_ma5':str(now),'state':state})
def support(o0,lows,closes):
 o=d(o0); ls=[d(x) for x in lows]; cs=[d(x) for x in closes]
 if o is None or not ls or len(ls)!=len(cs) or any(x is None for x in ls+cs):return missing('key_support',6,'missing_o0_or_history')
 if all(x>=o for x in ls) and all(x>=o for x in cs):return comp('key_support',6,6,{'mode':'P1'})
 if min(ls)>=o*Decimal('.99') and all(x>=o for x in cs):return comp('key_support',3,6,{'mode':'P2'})
 return comp('key_support',0,6,{'mode':'failed'})
def volume(t0, current, recent):
 t,c=d(t0),d(current); rs=[d(x) for x in recent]
 if t is None or c is None or t<=0:return missing('volume_contraction',6,'missing_or_zero_t0_volume')
 if any(x is None or x<0 for x in rs):return missing('volume_contraction',6,'invalid_recent_volume')
 ratio=c/t; base=comp('volume_contraction',6 if ratio<=Decimal('.5') else 4 if ratio<=Decimal('.7') else 0,6,{'ratio':str(ratio)})
 continuous=comp('continuous_contraction',3 if len(rs)>=2 and rs[-2]>rs[-1] else 0,3,{'recent_volumes':[str(x) for x in rs]})
 return base,continuous
def close_location(low,high,close):
 lo,hi,cl=map(d,(low,high,close))
 if None in (lo,hi,cl) or hi<lo:return missing('close_location',4,'invalid_ohlc')
 if hi==lo:return missing('close_location',4,'zero_range')
 r=(cl-lo)/(hi-lo); return comp('close_location',4 if r>=Decimal('.65') else 0,4,{'ratio':str(r)})
def rhythm(closes):
 vals=[d(x) for x in closes]
 if len(vals)<2 or any(x is None or x<=0 for x in vals):return missing('pullback_rhythm',3,'insufficient_closes')
 returns=[vals[i]/vals[i-1]-1 for i in range(1,len(vals))]
 return comp('pullback_rhythm',3 if all(r>=Decimal('-.05') for r in returns) else 0,3,{'returns':[str(r) for r in returns]})
def aggregate(parts):
 # Gate checks deliberately have no numeric score.  A pass/fail is still a
 # determinate observation; only missing/indeterminate input makes coverage
 # incomplete.
 ok=[p for p in parts if p.status in {'scored','zero_score','pass','fail'}]; earned=sum((p.score or 0 for p in ok),Decimal(0)); maxi=sum((p.maximum for p in ok),Decimal(0)); incomplete=len(ok)!=len(parts)
 return {'earned_score':earned,'theoretical_max_score':MAX,'determinable_max_score':maxi,'coverage_ratio':maxi/MAX,'is_complete':not incomplete,'status':'indeterminate' if incomplete else 'pass' if earned else 'fail','reasons':tuple(sorted({x for p in parts for x in p.reasons}))}
