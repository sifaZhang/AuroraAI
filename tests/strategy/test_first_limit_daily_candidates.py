from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.strategy.first_limit.daily_candidates import (
    compare_preview,
    evaluate_candidate,
)
from backend.strategy.first_limit.minute_review import Confirmation


def bar(day, low=10, close=10.5, volume=50, open_=10.4, high=10.6):
    return {
        "trade_date": day, "open": Decimal(str(open_)),
        "high": Decimal(str(high)), "low": Decimal(str(low)),
        "close": Decimal(str(close)), "volume": Decimal(str(volume)),
        "amount": Decimal("1000"),
    }


def event():
    return {
        "id": 1, "symbol": "000001.SZ", "trade_date": "2026-07-20",
        "open": Decimal("10"), "high": Decimal("11"),
        "low": Decimal("9.8"), "close": Decimal("11"),
    }


def history(current_low=10, current_volume=50):
    values = [
        bar("2026-07-13", volume=80),
        bar("2026-07-14", volume=80),
        bar("2026-07-15", volume=80),
        bar("2026-07-16", volume=80),
        bar("2026-07-17", volume=80),
        bar("2026-07-20", low=9.8, close=11, volume=100, open_=10, high=11),
        bar("2026-07-21", low=current_low, close=10.5, volume=current_volume),
    ]
    return values


def context(classification="A1", score=78, industry=15):
    return {
        "classification": classification, "daily_base_score": score,
        "industry_score": industry, "is_complete": 1, "is_approximate": 0,
        "observation_date": "2026-07-21",
    }


def evaluate(**overrides):
    values = {
        "event": event(), "bars": history(),
        "expected_dates": ["2026-07-20", "2026-07-21"],
        "status": {"is_st": 0, "is_suspended": 0},
        "observation_day": 1, "context": context(),
        "stage": "close_confirmed", "evaluation_date": "2026-07-21",
        "calendar_available": True,
    }
    values.update(overrides)
    return evaluate_candidate(**values)


def test_d0_d1_d5_and_after_d5_lifecycle():
    assert evaluate(observation_day=0).lifecycle_status == "watching"
    assert evaluate(observation_day=1).lifecycle_status == "confirmed"
    assert evaluate(observation_day=5).lifecycle_status == "confirmed"
    expired = evaluate(observation_day=6)
    assert expired.lifecycle_status == "expired"
    assert "EXPIRED_AFTER_D5" in expired.primary_reasons


def test_s_a_b_grade_mapping_uses_existing_pullback_classes_without_new_thresholds():
    assert evaluate(context=context("A1")).candidate_grade == "S"
    assert evaluate(context=context("A2")).candidate_grade == "A"
    assert evaluate(context=context("B")).candidate_grade == "B"


def test_first_limit_low_and_previous_elimination_are_permanent():
    broke = evaluate(bars=history(current_low=Decimal("9.79")))
    assert broke.lifecycle_status == "eliminated"
    assert "BROKE_FIRST_LIMIT_LOW" in broke.primary_reasons
    replay = evaluate(previous_eliminated=True, bars=history(current_low=10.2))
    assert replay.lifecycle_status == "eliminated"
    assert "PREVIOUSLY_ELIMINATED" in replay.primary_reasons


def test_drawdown_equal_boundary_passes_and_above_fails():
    boundary = evaluate(
        bars=history(current_low=Decimal("9.68")),
        event={**event(), "low": Decimal("9")},
    )
    evidence = {item.rule_code: item for item in boundary.evidence}
    assert evidence["MAX_DRAWDOWN"].result == "pass"
    above = evaluate(
        bars=history(current_low=Decimal("9.679")),
        event={**event(), "low": Decimal("9")},
    )
    assert "MAX_DRAWDOWN_EXCEEDED" in above.primary_reasons


def test_volume_bearish_sector_and_score_rules_are_auditable():
    expanded = evaluate(bars=history(current_volume=71))
    assert "VOLUME_CONTRACTION_FAILED" in expanded.primary_reasons
    retreat = evaluate(context=context(industry=8))
    assert "SECTOR_RETREAT" in retreat.primary_reasons
    low_score = evaluate(context=context(score=67))
    assert low_score.lifecycle_status == "watching"
    assert "DAILY_SCORE_BELOW_EXECUTABLE" in low_score.primary_reasons


def test_bearish_high_volume_bar_uses_existing_risk_threshold():
    values = history(current_low=Decimal("9.9"), current_volume=70)
    for item in values[:5]:
        item["volume"] = Decimal("20")
    values[-1]["open"] = Decimal("10.6")
    values[-1]["close"] = Decimal("10")
    decision = evaluate(bars=values)
    assert decision.lifecycle_status == "eliminated"
    assert "BEARISH_HIGH_VOLUME_BAR" in decision.primary_reasons


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"status": None}, "MISSING_SECURITY_STATUS"),
        ({"calendar_available": False}, "MISSING_TRADING_CALENDAR"),
        ({"bars": history()[:-1]}, "INSUFFICIENT_DAILY_BARS"),
        ({"context": None}, "SECTOR_CONTEXT_MISSING"),
    ],
)
def test_unknown_inputs_never_produce_final_grade(updates, reason):
    decision = evaluate(**updates)
    assert decision.lifecycle_status == "indeterminate"
    assert decision.candidate_grade is None
    assert reason in decision.primary_reasons
    assert not any(
        item.result == "fail" and item.reason_code == reason
        for item in decision.evidence
    )


def test_suspension_does_not_consume_candidate_or_permanently_eliminate():
    suspended = evaluate(status={"is_st": 0, "is_suspended": 1})
    assert suspended.lifecycle_status == "watching"
    assert suspended.candidate_grade is None
    assert "SUSPENDED" in suspended.primary_reasons


def test_tail_missing_is_pending_and_confirmed_tail_can_be_eligible():
    pending = evaluate(stage="tail_preview", tail_confirmation=None)
    assert pending.lifecycle_status == "pending_close_confirmation"
    confirmed = Confirmation(
        "confirmed", "first_tail_confirmation",
        datetime(2026, 7, 21, 14, 41, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat(),
        Decimal("10.5"), Decimal("10.51"), Decimal("5"), 9500, Decimal(".05"), 2,
    )
    eligible = evaluate(stage="tail_preview", tail_confirmation=confirmed)
    assert eligible.lifecycle_status == "eligible"
    assert eligible.candidate_grade == "S"


@pytest.mark.parametrize(
    ("preview", "close_status", "grade", "expected"),
    [
        (None, "confirmed", "S", "preview_missing"),
        ({"lifecycle_status": "eligible", "candidate_grade": "A"}, "confirmed", "S", "upgraded"),
        ({"lifecycle_status": "eligible", "candidate_grade": "S"}, "confirmed", "A", "downgraded"),
        ({"lifecycle_status": "eligible", "candidate_grade": "S"}, "confirmed", "S", "unchanged"),
        ({"lifecycle_status": "pending_close_confirmation", "candidate_grade": None}, "confirmed", "A", "newly_qualified"),
        ({"lifecycle_status": "eligible", "candidate_grade": "A"}, "eliminated", None, "eliminated"),
    ],
)
def test_preview_close_change_types(preview, close_status, grade, expected):
    decision = evaluate()
    decision = decision.__class__(
        close_status, grade, decision.score, decision.observation_day,
        decision.primary_reasons, decision.evidence,
    )
    assert compare_preview(preview, decision) == expected
