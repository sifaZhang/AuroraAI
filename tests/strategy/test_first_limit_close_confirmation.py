from datetime import date

from backend.strategy.first_limit.candidate_scoring import candidate_score
from backend.strategy.first_limit.close_confirmation import (
    OfficialCloseIndustryContext, change_between, estimation_error, next_day_plan,
)
from backend.strategy.first_limit.cli import parser


def official(score=70, rank=3, level=3):
    return OfficialCloseIndustryContext(date(2026,7,31),date(2026,7,31),level,"L3","三级",
        score,rank,20,"industry_score_v1","high","complete")


def test_estimation_error_sign_rank_and_different_level():
    high=estimation_error({"intraday_score":80,"intraday_rank":2,"industry_level":3,"as_of_time":"14:55"},official(70,4))
    assert high.score_error==10 and high.absolute_score_error==10 and high.rank_error==2
    low=estimation_error({"intraday_score":60,"intraday_rank":5,"industry_level":3},official(70,4))
    assert low.score_error==-10 and low.absolute_score_error==10 and low.rank_error==-1
    different=estimation_error({"intraday_score":60,"intraday_rank":5,"industry_level":2},official())
    assert not different.same_level_comparison and different.rank_error is None


def test_change_types_and_next_day_plan():
    def old(grade,total=80): return {"grade":grade,"total_score":total,"buy_recommendation":"x"}
    scores={key:candidate_score(*values) for key,values in {
        "S":(35,20,15,10,10,10),"A":(30,18,10,8,5,7),"B":(25,15,10,7,5,5),
    }.items()}
    assert change_between(old("S",90),scores["S"],None).change_type=="UNCHANGED"
    assert change_between(old("A",80),scores["S"],None).change_type=="UPGRADED"
    assert change_between(old("S",90),scores["A"],None).change_type=="DOWNGRADED"
    removed=candidate_score(35,20,15,10,10,10,("CLOSE_RISK",))
    assert change_between(old("A"),removed,None).change_type=="REMOVED"
    assert change_between(old("A"),scores["A"],None,True).change_type=="PENDING"
    assert next_day_plan("000001.SZ",scores["S"])["final_grade"]=="S"
    assert next_day_plan("000001.SZ",scores["B"]) is None


def test_close_and_pipeline_cli_contracts():
    close=parser().parse_args(["confirm-close","--trade-date","2026-07-31","--dry-run","--output-json"])
    pipeline=parser().parse_args(["run-daily-pipeline","--trade-date","2026-07-31","--stage","close-confirmation","--dry-run"])
    assert close.command=="confirm-close" and close.dry_run
    assert pipeline.stage=="close-confirmation" and pipeline.dry_run
