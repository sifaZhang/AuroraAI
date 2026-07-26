"""Pure, conservative PR6.3 first-limit event classification."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable
from .contracts import QualityFlag, SecurityStatus
from .rules import detect_price_anomalies, resolve_limit_prices, resolve_price_limit_rule

class DetectionStatus(str, Enum):
    DETECTED='detected'; NOT_FIRST_LIMIT='not_first_limit'; EXCLUDED='excluded'; INDETERMINATE='indeterminate'; FAILED='failed'
class Reason(str, Enum):
    NOT_LIMIT_UP_CLOSE='not_limit_up_close'; PREVIOUS_LIMIT_UP='previous_limit_up_within_20_days'; CONSECUTIVE='consecutive_limit_up'; ONE_WORD='one_word_limit'; HISTORICAL_INCOMPLETE='historical_window_incomplete'; MISSING_DAILY_BAR='missing_daily_bar'; INVALID_OHLC='invalid_ohlc'; SUSPENDED='suspended'; UNKNOWN_RULE='unknown_price_limit_rule'; UNRELIABLE_LIMIT='unreliable_upper_limit'; MISSING_STATUS='missing_security_status'; NON_STOCK='non_stock_security'; DATA_CONFLICT='data_source_conflict'; PRE_CLOSE_DISCONTINUITY='pre_close_discontinuity'; INELIGIBLE_SECURITY='ineligible_security'

@dataclass(frozen=True)
class Bar: trade_date: date; open: Decimal|None; high: Decimal|None; low: Decimal|None; close: Decimal|None; volume: Decimal|None; amount: Decimal|None; adjustment: str='none'
@dataclass(frozen=True)
class Metadata: pre_close: Decimal|None; source_upper_limit: Decimal|None; source_lower_limit: Decimal|None; quality_flags: frozenset[QualityFlag]=frozenset()
@dataclass(frozen=True)
class EventDecision:
    status: DetectionStatus; is_limit_up_close: bool|None; touched_upper_limit: bool|None; is_first_limit: bool|None; is_one_word_limit: bool|None; is_consecutive_limit: bool|None; consecutive_limit_days: int|None; observed_lookback_days: int; previous_limit_up_date: date|None; upper_limit: Decimal|None; upper_limit_source: str|None; reasons: frozenset[Reason]; quality_flags: frozenset[str]

def _valid(bar: Bar) -> bool:
    return all(x is not None and x > 0 for x in (bar.open,bar.high,bar.low,bar.close)) and bar.low <= min(bar.open,bar.close) <= max(bar.open,bar.close) <= bar.high
def classify(symbol, target: Bar, metadata: Metadata|None, status: SecurityStatus|None, history: Iterable[tuple[Bar, Metadata|None, SecurityStatus|None]], *, lookback_days=20) -> EventDecision:
    reasons:set[Reason]=set(); flags:set[str]=set()
    if not _valid(target): return EventDecision(DetectionStatus.INDETERMINATE,None,None,None,None,None,None,0,None,None,None,frozenset({Reason.INVALID_OHLC}),frozenset())
    if metadata is None: return EventDecision(DetectionStatus.INDETERMINATE,None,None,None,None,None,None,0,None,None,None,frozenset({Reason.MISSING_DAILY_BAR}),frozenset())
    rule=resolve_price_limit_rule(symbol,target.trade_date,status); limits=resolve_limit_prices(metadata.pre_close,rule,source_upper_limit=metadata.source_upper_limit,source_lower_limit=metadata.source_lower_limit)
    flags.update(x.value for x in limits.quality_flags|metadata.quality_flags|detect_price_anomalies(adjustment=target.adjustment,pre_close=metadata.pre_close,previous_close=None))
    if status is None: reasons.add(Reason.MISSING_STATUS)
    elif status.is_st is True or status.delisted_date is not None: reasons.add(Reason.INELIGIBLE_SECURITY)
    if not limits.reliable or limits.upper_limit is None: reasons.add(Reason.UNRELIABLE_LIMIT)
    if QualityFlag.SUSPENDED.value in flags: reasons.add(Reason.SUSPENDED)
    if QualityFlag.DATA_SOURCE_CONFLICT.value in flags: reasons.add(Reason.DATA_CONFLICT)
    if QualityFlag.PRE_CLOSE_DISCONTINUITY.value in flags: reasons.add(Reason.PRE_CLOSE_DISCONTINUITY)
    if reasons: return EventDecision(DetectionStatus.EXCLUDED if reasons & {Reason.SUSPENDED,Reason.INELIGIBLE_SECURITY} else DetectionStatus.INDETERMINATE,None,None,None,None,None,None,0,None,limits.upper_limit,limits.selection_basis,frozenset(reasons),frozenset(flags))
    close_up=target.close >= limits.upper_limit; touched=target.high >= limits.upper_limit
    if not close_up: return EventDecision(DetectionStatus.NOT_FIRST_LIMIT,False,touched,False,False,False,0,0,None,limits.upper_limit,limits.selection_basis,frozenset({Reason.NOT_LIMIT_UP_CLOSE}),frozenset(flags))
    one_word=target.open==target.high==target.low==target.close and (target.volume or Decimal(0))>0
    prior=list(history)[-lookback_days:]
    if len(prior)<lookback_days: return EventDecision(DetectionStatus.INDETERMINATE,True,touched,None,one_word,None,None,len(prior),None,limits.upper_limit,limits.selection_basis,frozenset({Reason.HISTORICAL_INCOMPLETE}),frozenset(flags))
    previous_limit=None
    for old, oldmeta, oldstatus in prior:
        if oldmeta is None or not _valid(old): return EventDecision(DetectionStatus.INDETERMINATE,True,touched,None,one_word,None,None,len(prior),None,limits.upper_limit,limits.selection_basis,frozenset({Reason.MISSING_DAILY_BAR}),frozenset(flags))
        oldlimits=resolve_limit_prices(oldmeta.pre_close,resolve_price_limit_rule(symbol,old.trade_date,oldstatus),source_upper_limit=oldmeta.source_upper_limit,source_lower_limit=oldmeta.source_lower_limit)
        if not oldlimits.reliable or oldlimits.upper_limit is None: return EventDecision(DetectionStatus.INDETERMINATE,True,touched,None,one_word,None,None,len(prior),None,limits.upper_limit,limits.selection_basis,frozenset({Reason.UNRELIABLE_LIMIT}),frozenset(flags))
        if old.close >= oldlimits.upper_limit: previous_limit=old.trade_date
    consecutive=previous_limit == prior[-1][0].trade_date
    if one_word: reasons.add(Reason.ONE_WORD)
    if consecutive: reasons.add(Reason.CONSECUTIVE)
    if previous_limit: reasons.add(Reason.PREVIOUS_LIMIT_UP)
    if reasons: return EventDecision(DetectionStatus.EXCLUDED,True,touched,False,one_word,consecutive,2 if consecutive else 1,len(prior),previous_limit,limits.upper_limit,limits.selection_basis,frozenset(reasons),frozenset(flags))
    return EventDecision(DetectionStatus.DETECTED,True,touched,True,False,False,1,len(prior),None,limits.upper_limit,limits.selection_basis,frozenset(),frozenset(flags))
