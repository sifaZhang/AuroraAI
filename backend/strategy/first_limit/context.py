"""PR6.6 deterministic daily context-score v1 rules.

The module is deliberately data-source agnostic: callers must mark unavailable or
look-ahead-prone inputs indeterminate/approximate instead of manufacturing a score.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

VERSION='first_limit_context_v1'; DAILY_MAX=Decimal('90')

@dataclass(frozen=True)
class Component:
    key:str; status:str; score:Decimal|None; maximum:Decimal; raw:dict; reasons:tuple[str,...]=(); approximate:bool=False

def _d(v):
    try: return None if v is None else Decimal(str(v))
    except Exception: return None
def scored(key, score, maximum, raw=None, reasons=()):
    return Component(key,'scored' if score else 'zero_score',Decimal(str(score)),Decimal(str(maximum)),raw or {},tuple(reasons))
def missing(key, maximum, reason, raw=None): return Component(key,'missing',None,Decimal(str(maximum)),raw or {},(reason,))
def indeterminate(key, maximum, reason, raw=None): return Component(key,'indeterminate',None,Decimal(str(maximum)),raw or {},(reason,))
def approximate(key, maximum, reason, raw=None): return Component(key,'approximate',None,Decimal(str(maximum)),raw or {},(reason,),True)

def industry_trend(value, maximum=70):
    v=_d(value)
    if v is None:return missing('industry_trend',8,'missing_sector_trend_score')
    if _d(maximum)!=70:return indeterminate('industry_trend',8,'sector_trend_version_mismatch',{'maximum':maximum})
    return scored('industry_trend',8 if v>=60 else 6 if v>=50 else 4 if v>=40 else 2 if v>=30 else 0,8,{'trend_score':str(v),'maximum':maximum})
def industry_breadth(value, maximum=30):
    v=_d(value)
    if v is None:return missing('industry_breadth',5,'missing_sector_breadth_score')
    if _d(maximum)!=30:return indeterminate('industry_breadth',5,'sector_breadth_version_mismatch',{'maximum':maximum})
    return scored('industry_breadth',5 if v>=25 else 4 if v>=20 else 3 if v>=15 else 2 if v>=10 else 1 if v>=5 else 0,5,{'breadth_score':str(v),'maximum':maximum})
def industry_limit_resonance(count, coverage):
    c=_d(count); cv=_d(coverage)
    if c is None or cv is None:return missing('industry_limit_resonance',3,'missing_industry_limit_coverage')
    if cv<Decimal('.95'):return indeterminate('industry_limit_resonance',3,'industry_limit_coverage_below_95pct',{'coverage':str(cv)})
    return scored('industry_limit_resonance',3 if c>=5 else 2 if c>=3 else 1 if c>=2 else 0,3,{'eligible_limit_count':str(c),'coverage':str(cv)})
def industry_rank(rank, valid_count):
    r=_d(rank); n=_d(valid_count)
    if r is None or n is None:return missing('industry_rank',2,'missing_same_date_radar_ranking')
    if n<25:return indeterminate('industry_rank',2,'fewer_than_25_complete_industries',{'valid_count':str(n)})
    if r<1 or r>n:return indeterminate('industry_rank',2,'invalid_industry_rank')
    p=(r-1)/max(n-1,1)
    return scored('industry_rank',2 if p<=Decimal('.20') else 1 if p<=Decimal('.40') else 0,2,{'rank':str(r),'valid_count':str(n),'percentile':str(p)})
def industry_change(current, previous):
    c,p=_d(current),_d(previous)
    if c is None or p is None:return missing('industry_change',2,'missing_previous_complete_industry_score')
    delta=c-p
    return scored('industry_change',2 if delta>=10 else 1 if delta>=3 else 0,2,{'current':str(c),'previous':str(p),'change':str(delta)},('weakening',) if delta<=-3 else ())

def index_trend(close, ma5, ma20, key):
    c,m5,m20=map(_d,(close,ma5,ma20))
    if None in (c,m5,m20) or m20<=0:return missing(key,2,'requires_20_valid_index_days')
    return scored(key,2 if c>=m20 and m5>=m20 else 1 if c>=m20 else 0,2,{'close':str(c),'ma5':str(m5),'ma20':str(m20)})
def market_limit_counts(up, down, coverage):
    u,d,cv=map(_d,(up,down,coverage))
    if None in (u,d,cv):return missing('market_limit_counts',4,'missing_market_limit_coverage')
    if cv<Decimal('.95'):return indeterminate('market_limit_counts',4,'market_limit_coverage_below_95pct',{'coverage':str(cv)})
    return scored('market_limit_counts',(2 if u>=60 else 1 if u>=30 else 0)+(2 if d<=5 else 1 if d<=19 else 0),4,{'limit_up':str(u),'limit_down':str(d),'coverage':str(cv)})
def market_risk(up, down, csi300_below, csi1000_below):
    u,d=map(_d,(up,down))
    if None in (u,d,csi300_below,csi1000_below):return missing('market_risk',2,'missing_market_risk_inputs')
    both=bool(csi300_below and csi1000_below)
    if d>=50 or (both and d>=30):state,score='extreme',0
    elif d>=20 or (both and u<30):state,score='weak',0
    elif not csi300_below and not csi1000_below and u>=60 and d<=10:state,score='strong',2
    else:state,score='oscillating',1
    return scored('market_risk',score,2,{'limit_up':str(u),'limit_down':str(d),'state':state})

def stock_ma_structure(closes):
    vals=[_d(x) for x in closes]
    if len(vals)<20 or any(x is None or x<=0 for x in vals):return missing('stock_ma_structure',6,'requires_20_valid_closes')
    c=vals[-1]; ma5=sum(vals[-5:])/5; ma10=sum(vals[-10:])/10; ma20=sum(vals[-20:])/20
    score=6 if c>=ma5>=ma10>=ma20 else 4 if c>=ma10 and ma5>=ma20 else 2 if c>=ma20 else 0
    return scored('stock_ma_structure',score,6,{'close':str(c),'ma5':str(ma5),'ma10':str(ma10),'ma20':str(ma20),'pre_limit_gain_reused':False,'exclusion':'already_scored_by_pr6.4'})
def stock_ma20_position(close, ma20):
    c,m=_d(close),_d(ma20)
    if c is None or m is None or m<=0:return missing('stock_ma20_position',2,'missing_ma20')
    distance=(c-m)/m
    reason='extended' if distance>Decimal('.08') else 'below' if distance<Decimal('-.03') else None
    return scored('stock_ma20_position',2 if Decimal(0)<=distance<=Decimal('.08') else 1 if Decimal('-.03')<=distance<0 else 0,2,{'distance':str(distance)},(reason,) if reason else ())
def stock_acceleration(closes):
    vals=[_d(x) for x in closes]
    if len(vals)<6 or any(x is None or x<=0 for x in vals):return missing('stock_acceleration',2,'requires_6_valid_closes')
    gain=vals[-1]/vals[-6]-1; daily=[vals[i]/vals[i-1]-1 for i in range(len(vals)-5,len(vals))]; maximum=max(daily)
    score=2 if gain<=Decimal('.08') and maximum<Decimal('.07') else 1 if gain<=Decimal('.15') and maximum<Decimal('.095') else 0
    return scored('stock_acceleration',score,2,{'gain_5d':str(gain),'max_daily_gain':str(maximum)},('overaccelerated',) if score==0 and (gain>Decimal('.15') or maximum>=Decimal('.095')) else ())

def aggregate(components, first_limit_score=None, pullback_score=None):
    deterministic=[x for x in components if x.status in {'scored','zero_score'}]
    context_earned=sum((x.score or 0 for x in deterministic),Decimal(0)); context_max=sum((x.maximum for x in deterministic),Decimal(0))
    upstream=[_d(first_limit_score),_d(pullback_score)]
    upstream_complete=all(x is not None for x in upstream)
    total=context_earned+sum((x or 0 for x in upstream),Decimal(0)); determinable=context_max+(Decimal(20) if upstream[0] is not None else 0)+(Decimal(30) if upstream[1] is not None else 0)
    approx=any(x.approximate for x in components); complete=upstream_complete and len(deterministic)==len(components) and not approx
    status='complete' if complete else 'approximate' if approx else 'indeterminate' if any(x.status=='indeterminate' for x in components) else 'missing' if any(x.status=='missing' for x in components) else 'partial'
    return {'score_status':status,'daily_base_score':total if determinable else None,'daily_base_determinable_max_score':determinable,'daily_base_coverage_ratio':determinable/DAILY_MAX,'is_complete':complete,'is_approximate':approx,'minute_confirm_score':None,'minute_confirm_status':'not_available','total_score':None,'final_candidate_level':'pending_minute_confirmation','reasons':tuple(sorted({r for x in components for r in x.reasons}))}
