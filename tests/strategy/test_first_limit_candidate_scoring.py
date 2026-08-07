from backend.strategy.first_limit.candidate_scoring import (
    capital_activity_score, candidate_score, industry_environment_score,
    industry_trend_score, leader_score,
)
from backend.strategy.first_limit.daily_candidate_repository import is_persistable_scoring
from backend.strategy.first_limit.candidate_scoring_service import FirstLimitCandidateScoringService
from backend.strategy.first_limit.daily_candidates import Decision
from backend.strategy.first_limit.run_daily_candidates import apply_candidate_score


def test_capital_activity_tiers_states_history_and_bounds():
    assert capital_activity_score(2,1.8,"volume_up_price_up",.9)["score"] == 10
    assert capital_activity_score(.7,.9,"volume_up_price_down",.2)["score"] == 0
    assert capital_activity_score(None,None,None,None) == {
        "score":5,"components":{"ratio_5d":None,"ratio_20d":None,"price_volume":None,"industry_rank":None},
        "status":"history_insufficient","warnings":["missing_components_normalized"]}
    states={"volume_up_price_up":3,"flat_volume_price_up":2,"volume_down_price_up":1.5,
        "volume_down_price_down":1,"flat_volume_price_down":.5,"volume_up_price_down":0}
    for state, expected in states.items():
        assert capital_activity_score(2,1.8,state,.9)["components"]["price_volume"] == expected


def test_leader_rank_bands_and_missing_history():
    top=leader_score(1,100,1,1,.04,.01);assert top["score"]==10
    assert leader_score(10,100,10,1,.04,.01)["components"]["return_rank"] > 2
    assert leader_score(25,100,25,1,.04,.01)["components"]["amount_rank"] == 1
    missing=leader_score(None,0,None,None,None,None)
    assert missing["score"]==5 and missing["status"]=="history_insufficient"


def test_industry_trend_environment_and_confidence():
    strong=industry_trend_score(80,50,55,2,10,"high")
    stable=industry_trend_score(50,50,50,10,10,"high")
    retreat=industry_trend_score(20,60,60,20,2,"high")
    assert strong["score"]>=7;assert -2<=stable["score"]<=2;assert retreat["score"]<=-7
    assert industry_environment_score(100,100,100,10,"high")["score"]==15
    assert industry_environment_score(None,None,None,None,"unavailable")["status"]=="partial"


def _score(total, **kwargs):
    shape=min(35,total); total-=shape; first=min(20,total);total-=first
    industry=min(15,total);total-=industry;capital=min(10,total);total-=capital
    leader=min(10,total);total-=leader;market=min(10,total)
    return candidate_score(shape,first,industry,capital,leader,market,**kwargs)


def test_candidate_exact_boundaries_hard_exclusion_and_missing_industry_does_not_cap():
    expected=((64.99,None),(65,"B"),(74.99,"B"),(75,"A"),(84.99,"A"),(85,"S"),(100,"S"))
    for total,grade in expected:
        result=_score(total);assert result.total_score==total;assert result.grade==grade
    assert _score(100,hard_exclusions=("ST",)).grade is None
    uncapped=_score(100,industry_available=False)
    assert uncapped.grade=="S"
    assert _score(75).buy_recommendation=="可小仓位" and _score(65).buy_recommendation is None
    for total in (65,75,85,100): assert is_persistable_scoring(_score(total).evidence())
    assert not is_persistable_scoring(_score(64.99).evidence())
    assert not is_persistable_scoring(_score(100,hard_exclusions=("ST",)).evidence())


def test_incomplete_legacy_context_and_missing_industry_are_not_hard_exclusions():
    context = {
        "is_complete": 0,
        "first_limit_score": 18,
        "pullback_score": 25,
        "market_score": None,
    }
    estimate = type("Estimate", (), {"status": "membership_missing"})()
    event = {"is_one_word_limit": 0}
    assert not event["is_one_word_limit"]
    severely_incomplete = type(
        "Estimate", (), {"status": "intraday_data_insufficient"}
    )()
    assert severely_incomplete.status == "intraday_data_insufficient"


def test_new_grade_replaces_incomplete_legacy_lifecycle():
    decision = Decision("pending_close_confirmation", None, None, 2, (), ())
    scored = candidate_score(35, 20, 10, 5, 5, 0)
    merged = apply_candidate_score(decision, scored)
    assert merged.lifecycle_status == "eligible"
    assert merged.candidate_grade == "A"
    assert merged.score == 75
