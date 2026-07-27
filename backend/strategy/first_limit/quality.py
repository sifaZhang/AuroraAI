"""Pure, conservative PR6.4 first-limit quality scoring rules."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Mapping

RULE_VERSION = 'first_limit_quality_v1'
THEORETICAL_MAX_SCORE = Decimal('20')

class ComponentStatus(str, Enum):
    SCORED='scored'; ZERO_SCORE='zero_score'; MISSING='missing'; INDETERMINATE='indeterminate'; EXCLUDED='excluded'; APPROXIMATE='approximate'; ERROR='error'

@dataclass(frozen=True)
class Component:
    key: str; status: ComponentStatus; score: Decimal|None; maximum: Decimal; raw: Mapping[str, object]; reasons: tuple[str,...]=(); approximate: bool=False

def _d(value):
    return None if value is None else Decimal(str(value))
def _scored(key, score, maximum, raw):
    return Component(key, ComponentStatus.SCORED if score else ComponentStatus.ZERO_SCORE, Decimal(str(score)), Decimal(str(maximum)), raw)
def _missing(key, maximum, reason, raw=None):
    return Component(key, ComponentStatus.MISSING, None, Decimal(str(maximum)), raw or {}, (reason,))
def _indeterminate(key, maximum, reason, raw=None):
    return Component(key, ComponentStatus.INDETERMINATE, None, Decimal(str(maximum)), raw or {}, (reason,))

def pre_position(closes):
    if len(closes) != 20: return _missing('pre_position',4,'requires_20_prior_trading_days',{'observed_days':len(closes)})
    first,last=_d(closes[0]),_d(closes[-1])
    if first is None or last is None or first <= 0: return _indeterminate('pre_position',4,'invalid_prior_close',{'first':str(first),'last':str(last)})
    change=(last-first)/first
    return _scored('pre_position',4 if change<=Decimal('.10') else 2 if change<=Decimal('.20') else 0,4,{'first_close':str(first),'last_close':str(last),'return':str(change)})

def volume_expansion(volumes, target_volume):
    values=[_d(v) for v in volumes]; target=_d(target_volume)
    if len(values)!=5: return _missing('volume_expansion',4,'requires_5_prior_trading_days',{'observed_days':len(values)})
    if target is None or target<0 or any(v is None or v<0 for v in values): return _indeterminate('volume_expansion',4,'invalid_volume')
    average=sum(values)/Decimal(5)
    if average==0: return _indeterminate('volume_expansion',4,'zero_prior_average_volume',{'target_volume':str(target)})
    ratio=target/average
    score=4 if Decimal('1.5')<=ratio<=Decimal('3.0') else 2 if Decimal('1.2')<=ratio<Decimal('1.5') or Decimal('3.0')<ratio<=Decimal('4.0') else 0
    return _scored('volume_expansion',score,4,{'target_volume':str(target),'prior_average':str(average),'ratio':str(ratio)})

def turnover(value):
    raw=_d(value)
    if raw is None: return _missing('turnover',3,'authoritative_turnover_unavailable')
    if raw<0: return _indeterminate('turnover',3,'invalid_turnover',{'turnover':str(raw)})
    score=3 if Decimal('5')<=raw<=Decimal('15') else 2 if Decimal('3')<=raw<Decimal('5') or Decimal('15')<raw<=Decimal('25') else 0
    return _scored('turnover',score,3,{'turnover_percent':str(raw)})

def amount(value):
    raw=_d(value)
    if raw is None: return _missing('amount',3,'missing_amount')
    if raw<0: return _indeterminate('amount',3,'invalid_amount',{'amount_yuan':str(raw)})
    return _scored('amount',3 if raw>=Decimal('500000000') else 2 if raw>=Decimal('200000000') else 0,3,{'amount_yuan':str(raw),'unit':'yuan'})

def candle_shape(open_, high, low, close, pre_close):
    op,hi,lo,cl,pc=map(_d,(open_,high,low,close,pre_close))
    if any(v is None for v in (op,hi,lo,cl,pc)): return _missing('candle_shape',2,'missing_ohlc_or_pre_close')
    if pc<=0 or lo>min(op,cl) or max(op,cl)>hi: return _indeterminate('candle_shape',2,'invalid_ohlc')
    span=hi-lo
    if span==0: return _indeterminate('candle_shape',2,'zero_daily_range')
    body=(cl-op)/pc; lower=(min(op,cl)-lo)/span
    return _scored('candle_shape',2 if body>=Decimal('.07') and lower<=Decimal('.30') else 0,2,{'body_return':str(body),'lower_shadow_ratio':str(lower)})

def industry_resonance(limit_count, *, approximate=False, mapping=None):
    if limit_count is None: return _indeterminate('industry_resonance',2,'authoritative_industry_limit_count_unavailable',{'mapping':mapping} if mapping else {})
    count=int(limit_count); score=2 if count>=2 else 0
    component=_scored('industry_resonance',score,2,{'same_industry_limit_count':count,'mapping':mapping})
    return Component(component.key,ComponentStatus.APPROXIMATE if approximate else component.status,component.score,component.maximum,component.raw,('current_membership_snapshot_used_for_history',) if approximate else (),approximate)

def industry_strength(trend_score, *, approximate=False, mapping=None, score_date=None):
    value=_d(trend_score)
    if value is None: return _indeterminate('industry_strength',2,'same_day_sector_trend_score_unavailable',{'mapping':mapping,'score_date':score_date})
    component=_scored('industry_strength',2 if value>=Decimal('70') else 0,2,{'trend_score':str(value),'mapping':mapping,'score_date':score_date})
    return Component(component.key,ComponentStatus.APPROXIMATE if approximate else component.status,component.score,component.maximum,component.raw,('current_membership_snapshot_used_for_history',) if approximate else (),approximate)

def aggregate(components):
    deterministic=[c for c in components if c.status in {ComponentStatus.SCORED,ComponentStatus.ZERO_SCORE}]
    earned=sum((c.score or Decimal(0) for c in deterministic),Decimal(0))
    maximum=sum((c.maximum for c in deterministic),Decimal(0))
    approximate=any(c.approximate for c in components)
    incomplete=len(deterministic)!=len(components)
    statuses={c.status for c in components}
    status='approximate' if approximate else 'indeterminate' if ComponentStatus.INDETERMINATE in statuses else 'missing' if ComponentStatus.MISSING in statuses else 'scored' if earned else 'zero_score'
    reasons=tuple(sorted({reason for c in components for reason in c.reasons}))
    return {'score_status':status,'earned_score':earned,'determinable_max_score':maximum,'coverage_ratio':maximum/THEORETICAL_MAX_SCORE,'is_complete':not incomplete and not approximate,'is_approximate':approximate,'reasons':reasons}
