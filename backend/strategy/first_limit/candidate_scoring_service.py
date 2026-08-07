"""Tail scoring using formal industry data and candidate-local minute facts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

from .candidate_scoring import capital_activity_score, candidate_score, industry_environment_score, industry_trend_score, leader_score


@dataclass(frozen=True)
class _Estimate:
    status: str; confidence: str; intraday_score: float | None; intraday_rank: int | None
    equal_weight_return: float | None; reason: str
    def evidence(self):
        return {"status": self.status, "confidence": self.confidence,
                "intraday_score": self.intraday_score, "intraday_rank": self.intraday_rank,
                "equal_weight_return": self.equal_weight_return, "reason": self.reason}


@dataclass(frozen=True)
class CandidateScoringResult:
    estimate: object; trend: dict; capital: dict; leader: dict; environment: dict; candidate: object; industry: dict
    def evidence(self):
        return {"INTRADAY_INDUSTRY_ESTIMATE": self.estimate.evidence(), "TAIL_INDUSTRY_CONTEXT": self.industry,
                "CAPITAL_ACTIVITY": self.capital, "LEADER_SCORE": self.leader,
                "INDUSTRY_ENVIRONMENT": {**self.environment, "trend": self.trend}, "CANDIDATE_SCORE": self.candidate.evidence()}


class FirstLimitCandidateScoringService:
    def __init__(self, connection): self.connection=connection

    def score(self,event,quality_row,pullback_row,industry_context,as_of,candidate_symbols=()):
        moment=datetime.fromisoformat(as_of); effective=industry_context.effective
        official=industry_context.previous_score
        industry={"effective_industry_level":getattr(effective,'effective_level',None),
          "effective_industry_code":getattr(effective,'effective_industry_code',None),
          "industry_score_date":str(industry_context.previous_score_date) if industry_context.previous_score_date else None,
          "industry_score":official,"industry_rank":industry_context.previous_rank,
          "fallback_reason":getattr(effective,'fallback_reason',None),
          "industry_intraday_status":"official_previous_close_fallback",
          "intraday_confidence":"low" if official is not None else "unavailable"}
        # No industry-wide minute query: absent a complete local candidate peer
        # panel, the prior complete official score is the explicit fallback.
        estimate=_Estimate("official_previous_close_fallback",industry["intraday_confidence"],official,industry_context.previous_rank,None,"industry_minutes_not_required")
        trend=industry_trend_score(official,industry_context.previous_score,industry_context.first_limit_score,industry_context.previous_rank,industry_context.previous_rank,estimate.confidence)
        ratio5,ratio20,state=self._capital_facts(event['symbol'],moment)
        capital=capital_activity_score(ratio5,ratio20,state,None)
        rr,ar,members,relative=self._leader_facts(event['symbol'],moment,candidate_symbols)
        first=float(quality_row['earned_score'] or 0) if quality_row else 0
        pull=float(pullback_row['earned_score'] or 0) if pullback_row else 0
        leader=leader_score(rr,members,ar,min(1,first/20),relative,pull/30-.5)
        environment=industry_environment_score(industry_context.first_limit_score,official,official,trend['score'],estimate.confidence)
        # Market has not been calculated by a retired context step.  Use a
        # disclosed neutral normalization, not an implicit missing=0 penalty.
        market=5.0
        hard=tuple(x for x in ("KEY_BASE_DATA_MISSING" if quality_row is None or pullback_row is None else None,
                               "ONE_WORD_LIMIT" if event['is_one_word_limit'] else None) if x)
        scored=candidate_score(pull/30*35,first,environment['score'],capital['score'],leader['score'],market,hard,True)
        return CandidateScoringResult(estimate,trend,capital,leader,environment,scored,industry)

    def _capital_facts(self,symbol,moment):
        cutoff=moment.time().replace(tzinfo=None).isoformat(timespec='minutes')
        amount=float(self.connection.execute("SELECT COALESCE(SUM(amount),0) FROM first_limit_minute_bars WHERE symbol=? AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=?",(symbol,moment.date().isoformat(),cutoff)).fetchone()[0] or 0)
        from .intraday_industry import completed_session_ratio
        current=amount/completed_session_ratio(moment.time().replace(tzinfo=None))
        rows=[float(r[0] or 0) for r in self.connection.execute("SELECT amount FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date<? AND amount IS NOT NULL ORDER BY trade_date DESC LIMIT 20",(symbol.split('.')[0],moment.date().isoformat()))]
        ratio5=current/(sum(rows[:5])/len(rows[:5])) if len(rows)>=5 and sum(rows[:5]) else None
        ratio20=current/(sum(rows)/len(rows)) if len(rows)>=20 and sum(rows) else None
        latest=self.connection.execute("SELECT close FROM first_limit_minute_bars WHERE symbol=? AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=? ORDER BY bar_time DESC LIMIT 1",(symbol,moment.date().isoformat(),cutoff)).fetchone()
        previous=self.connection.execute("SELECT close FROM a_share_daily_bars WHERE stock_code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1",(symbol.split('.')[0],moment.date().isoformat())).fetchone()
        value=None if not latest or not previous or not previous[0] else float(latest[0])/float(previous[0])-1
        state=None if value is None or ratio5 is None else ('volume_up_price_up' if ratio5>=1.2 and value>=0 else 'volume_up_price_down' if ratio5>=1.2 else 'volume_down_price_up' if ratio5<.8 and value>=0 else 'volume_down_price_down' if ratio5<.8 else 'flat_volume_price_up' if value>=0 else 'flat_volume_price_down')
        return ratio5,ratio20,state

    def _leader_facts(self,symbol,moment,candidates):
        # Only the already day-prefiltered local candidate set is examined.
        candidates=sorted(set(candidates or (symbol,)))
        if symbol not in candidates:candidates.append(symbol)
        cutoff=moment.time().replace(tzinfo=None).isoformat(timespec='minutes'); facts=[]
        for member in candidates:
            latest=self.connection.execute("SELECT close FROM first_limit_minute_bars WHERE symbol=? AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=? ORDER BY bar_time DESC LIMIT 1",(member,moment.date().isoformat(),cutoff)).fetchone()
            prev=self.connection.execute("SELECT close FROM a_share_daily_bars WHERE stock_code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1",(member.split('.')[0],moment.date().isoformat())).fetchone()
            amount=self.connection.execute("SELECT COALESCE(SUM(amount),0) FROM first_limit_minute_bars WHERE symbol=? AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=?",(member,moment.date().isoformat(),cutoff)).fetchone()[0]
            if latest and prev and prev[0]:facts.append((member,float(latest[0])/float(prev[0])-1,float(amount or 0)))
        returns=sorted(facts,key=lambda x:(-x[1],x[0])); amounts=sorted(facts,key=lambda x:(-x[2],x[0])); own=next((x[1] for x in facts if x[0]==symbol),None); mean=sum(x[1] for x in facts)/len(facts) if facts else None
        return next((i+1 for i,x in enumerate(returns) if x[0]==symbol),None),next((i+1 for i,x in enumerate(amounts) if x[0]==symbol),None),len(facts),None if own is None or mean is None else own-mean
