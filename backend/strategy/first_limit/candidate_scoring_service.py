"""Application service for PR6.13B tail-preview scoring."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

from .candidate_scoring import (
    capital_activity_score, candidate_score, industry_environment_score,
    industry_trend_score, leader_score,
)
from .intraday_industry import IntradayIndustryEstimator


@dataclass(frozen=True)
class CandidateScoringResult:
    estimate: object
    trend: dict
    capital: dict
    leader: dict
    environment: dict
    candidate: object

    def evidence(self):
        return {
            "INTRADAY_INDUSTRY_ESTIMATE": self.estimate.evidence(),
            "CAPITAL_ACTIVITY": self.capital,
            "LEADER_SCORE": self.leader,
            "INDUSTRY_ENVIRONMENT": {**self.environment, "trend": self.trend},
            "CANDIDATE_SCORE": self.candidate.evidence(),
        }


class FirstLimitCandidateScoringService:
    def __init__(self, connection):
        self.connection = connection

    def score(self, event, context_row, industry_context, as_of):
        moment = datetime.fromisoformat(as_of)
        effective = industry_context.effective
        if effective is None:
            from backend.industry.models import EffectiveIndustryContext
            effective = EffectiveIndustryContext(None,None,None,None,None,None,None,None,"unavailable")
        estimate = IntradayIndustryEstimator(self.connection).estimate(
            event["symbol"], moment.date(), moment.time().replace(tzinfo=None), effective
        )
        trend = industry_trend_score(
            estimate.intraday_score, industry_context.previous_score,
            industry_context.first_limit_score, estimate.intraday_rank,
            industry_context.previous_rank, estimate.confidence,
        )
        ratio5, ratio20, price_state, activity_percentile = self._capital_facts(
            event["symbol"], moment, estimate, effective
        )
        capital = capital_activity_score(ratio5, ratio20, price_state, activity_percentile)
        return_rank, amount_rank, members, relative = self._leader_facts(
            event["symbol"], moment, effective
        )
        first_quality = None if context_row is None else min(
            1, float(context_row["first_limit_score"] or 0) / 20
        )
        pullback_resilience = None if context_row is None else (
            float(context_row["pullback_score"] or 0) / 30 - .5
        )
        leader = leader_score(
            return_rank, members, amount_rank, first_quality, relative,
            pullback_resilience,
        )
        environment = industry_environment_score(
            industry_context.first_limit_score, industry_context.previous_score,
            estimate.intraday_score, trend["score"], estimate.confidence,
        )
        shape = 0 if context_row is None else float(context_row["pullback_score"] or 0) / 30 * 35
        first = 0 if context_row is None else float(context_row["first_limit_score"] or 0)
        market = 0 if context_row is None else float(context_row["market_score"] or 0)
        hard = self._hard_exclusions(event, context_row, estimate)
        scored = candidate_score(
            shape, first, environment["score"], capital["score"], leader["score"], market,
            hard, estimate.status in {"complete", "partial"},
        )
        return CandidateScoringResult(estimate, trend, capital, leader, environment, scored)

    def _capital_facts(self, symbol, moment, estimate, effective):
        cutoff=moment.time().replace(tzinfo=None).isoformat(timespec="minutes")
        accumulated=float(self.connection.execute("""SELECT COALESCE(SUM(amount),0)
            FROM first_limit_minute_bars WHERE symbol=? AND substr(bar_time,1,10)=?
            AND substr(bar_time,12,5)<=?""",(symbol,moment.date().isoformat(),cutoff)).fetchone()[0] or 0)
        from .intraday_industry import completed_session_ratio
        current = accumulated/completed_session_ratio(moment.time().replace(tzinfo=None))
        rows = [float(row[0] or 0) for row in self.connection.execute(
            """SELECT amount FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none'
               AND trade_date<? AND amount IS NOT NULL ORDER BY trade_date DESC LIMIT 20""",
            (symbol.split(".")[0], moment.date().isoformat()),
        )]
        ratio5 = current/(sum(rows[:5])/len(rows[:5])) if len(rows)>=5 and sum(rows[:5]) else None
        ratio20 = current/(sum(rows)/len(rows)) if len(rows)>=20 and sum(rows) else None
        value = estimate.equal_weight_return
        state = None if value is None or ratio5 is None else (
            "volume_up_price_up" if ratio5>=1.2 and value>=0 else
            "volume_up_price_down" if ratio5>=1.2 else
            "volume_down_price_up" if ratio5<.8 and value>=0 else
            "volume_down_price_down" if ratio5<.8 else
            "flat_volume_price_up" if value>=0 else "flat_volume_price_down"
        )
        activity_percentile=None
        if effective.effective_level and effective.effective_industry_code:
            amounts=[]
            for row in self.connection.execute(
                f"SELECT symbol FROM industry_memberships_current WHERE level{effective.effective_level}_code=?",
                (effective.effective_industry_code,),
            ):
                amount=float(self.connection.execute("""SELECT COALESCE(SUM(amount),0) FROM first_limit_minute_bars
                    WHERE symbol=? AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=?""",
                    (row[0],moment.date().isoformat(),cutoff)).fetchone()[0] or 0)
                amounts.append((row[0],amount))
            ordered=sorted(amounts,key=lambda item:(item[1],item[0]))
            position=next((index for index,item in enumerate(ordered) if item[0]==symbol),None)
            activity_percentile=(position+1)/len(ordered) if position is not None and ordered else None
        return ratio5, ratio20, state, activity_percentile

    def _leader_facts(self, symbol, moment, effective):
        if not effective.effective_level or not effective.effective_industry_code:
            return None, None, 0, None
        members = [row[0] for row in self.connection.execute(
            f"SELECT symbol FROM industry_memberships_current WHERE level{effective.effective_level}_code=?",
            (effective.effective_industry_code,),
        )]
        facts=[]
        cutoff=moment.time().replace(tzinfo=None).isoformat(timespec="minutes")
        for member in members:
            latest=self.connection.execute("""SELECT close FROM first_limit_minute_bars WHERE symbol=?
                AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=? ORDER BY bar_time DESC LIMIT 1""",
                (member,moment.date().isoformat(),cutoff)).fetchone()
            previous=self.connection.execute("""SELECT close FROM a_share_daily_bars WHERE stock_code=?
                AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",(member.split('.')[0],moment.date().isoformat())).fetchone()
            amount=self.connection.execute("""SELECT COALESCE(SUM(amount),0) FROM first_limit_minute_bars WHERE symbol=?
                AND substr(bar_time,1,10)=? AND substr(bar_time,12,5)<=?""",(member,moment.date().isoformat(),cutoff)).fetchone()[0]
            if latest and previous and previous[0]: facts.append((member,float(latest[0])/float(previous[0])-1,float(amount or 0)))
        returns=sorted(facts,key=lambda x:(-x[1],x[0])); amounts=sorted(facts,key=lambda x:(-x[2],x[0]))
        rr=next((i+1 for i,x in enumerate(returns) if x[0]==symbol),None)
        ar=next((i+1 for i,x in enumerate(amounts) if x[0]==symbol),None)
        own=next((x[1] for x in facts if x[0]==symbol),None)
        industry=sum(x[1] for x in facts)/len(facts) if facts else None
        return rr,ar,len(facts),None if own is None or industry is None else own-industry

    @staticmethod
    def _hard_exclusions(event, context, estimate):
        reasons=[]
        if context is None or not context["is_complete"]: reasons.append("KEY_BASE_DATA_MISSING")
        if estimate.status in {"intraday_data_insufficient", "membership_missing", "unavailable"}:
            reasons.append("INTRADAY_DATA_SEVERELY_INSUFFICIENT")
        if event["is_one_word_limit"]: reasons.append("ONE_WORD_LIMIT")
        return tuple(reasons)
