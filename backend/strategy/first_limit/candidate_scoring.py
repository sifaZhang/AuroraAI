"""Fixed PR6.13B candidate scoring rules."""
from __future__ import annotations
from dataclasses import dataclass, asdict

VERSION = "first_limit_candidate_score_v1"


def _clamp(value, low=0, high=10): return round(max(low, min(high, value)), 4)
def _tier(value, rules, default=0):
    if value is None: return None
    return next((score for threshold, score in rules if value >= threshold), default)


def capital_activity_score(ratio_5d, ratio_20d, price_volume_state, activity_percentile):
    parts = {
        "ratio_5d": _tier(ratio_5d, ((2,3),(1.5,2.5),(1.2,2),(0.8,1))),
        "ratio_20d": _tier(ratio_20d, ((1.8,2),(1.3,1.5),(1,1))),
        "price_volume": {"volume_up_price_up":3,"flat_volume_price_up":2,"volume_down_price_up":1.5,
            "volume_down_price_down":1,"flat_volume_price_down":.5,"volume_up_price_down":0}.get(price_volume_state),
        "industry_rank": _tier(activity_percentile, ((.9,2),(.75,1.5),(.5,1))),
    }
    known = [value for value in parts.values() if value is not None]
    score = sum(known) * 10 / sum((3,2,3,2)[i] for i,value in enumerate(parts.values()) if value is not None) if known else 5
    return {"score": _clamp(score), "components": parts,
            "status": "complete" if len(known)==4 else "history_insufficient",
            "warnings": [] if len(known)==4 else ["missing_components_normalized"]}


def leader_score(return_rank, member_count, amount_rank, first_limit_quality,
                 relative_strength, pullback_resilience):
    def rank_points(rank, maximum):
        if rank is None or not member_count: return None
        pct = rank/member_count
        return maximum if rank<=3 else maximum*.8333 if pct<=.1 else maximum*.5 if pct<=.25 else 0
    parts={"return_rank":rank_points(return_rank,3),"amount_rank":rank_points(amount_rank,2),
           "first_limit_quality":None if first_limit_quality is None else min(2,max(0,first_limit_quality*2)),
           "relative_strength":None if relative_strength is None else 2 if relative_strength>=.03 else 1 if relative_strength>=0 else 0,
           "pullback_resilience":None if pullback_resilience is None else 1 if pullback_resilience>=0 else 0}
    known=[v for v in parts.values() if v is not None]
    score=sum(known)*10/sum((3,2,2,2,1)[i] for i,v in enumerate(parts.values()) if v is not None) if known else 5
    return {"score":_clamp(score),"components":parts,"status":"complete" if len(known)==5 else "history_insufficient",
            "warnings":[] if len(known)==5 else ["missing_components_normalized"]}


def industry_trend_score(intraday_score, previous_score, first_limit_score,
                         current_rank=None, previous_rank=None, confidence="unavailable"):
    if intraday_score is None or previous_score is None: return {"score":0,"status":"unavailable","rank_change":None}
    delta=intraday_score-previous_score; auxiliary=0 if first_limit_score is None else (intraday_score-first_limit_score)*.15
    rank_change=0 if current_rank is None or previous_rank is None else previous_rank-current_rank
    value=_clamp(delta*.35+auxiliary+rank_change*.3,-10,10)
    if confidence not in {"high","medium"}: value=_clamp(value*.5,-10,10)
    return {"score":value,"status":"complete","rank_change":rank_change,"delta":delta}


def industry_environment_score(first_limit, previous, intraday, trend, confidence):
    parts={"first_limit":None if first_limit is None else first_limit/100*4,
           "previous":None if previous is None else previous/100*3,
           "intraday":None if intraday is None else intraday/100*4,
           "trend":None if trend is None else (trend+10)/20*3,
           "quality":1 if confidence=="high" else .7 if confidence=="medium" else .3 if confidence=="low" else None}
    known=[v for v in parts.values() if v is not None]; maximum=[4,3,4,3,1]
    score=sum(known)*15/sum(maximum[i] for i,v in enumerate(parts.values()) if v is not None) if known else 0
    return {"score":round(min(15,score),4),"components":parts,"status":"complete" if len(known)==5 else "partial"}


@dataclass(frozen=True)
class CandidateScore:
    version: str; components: dict; total_score: float; grade: str|None
    buy_recommendation: str|None; hard_exclusions: tuple[str,...]
    grade_caps: tuple[str,...]; warnings: tuple[str,...]
    def evidence(self): return asdict(self)


def candidate_score(shape, first_limit, industry, capital, leader, market,
                    hard_exclusions=(), industry_available=True):
    components={"shape_pullback":_clamp(shape,0,35),"first_limit":_clamp(first_limit,0,20),
        "industry_environment":_clamp(industry,0,15),"capital_activity":_clamp(capital,0,10),
        "leader":_clamp(leader,0,10),"market_risk":_clamp(market,0,10)}
    total=round(min(100,sum(components.values())),2); caps=() if industry_available else ("industry_unavailable_max_B",)
    grade=None if hard_exclusions or total<65 else "S" if total>=85 else "A" if total>=75 else "B"
    if caps and grade in {"S","A"}: grade="B"
    buy="重点候选" if grade=="S" else "可小仓位" if grade=="A" else None
    return CandidateScore(VERSION,components,total,grade,buy,tuple(hard_exclusions),caps,())
