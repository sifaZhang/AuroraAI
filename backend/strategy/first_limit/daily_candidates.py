"""Pure PR6.9 daily-candidate lifecycle, evidence, grading, and comparison."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from .pullback import MAX_DRAWDOWN, RISK_VOLUME_RATIO

VERSION = "first_limit_daily_candidates_v1"
MIN_DAILY_BASE_SCORE = Decimal("68")
GRADES = {"A1": "S", "A2": "A", "B": "B"}


@dataclass(frozen=True)
class Evidence:
    rule_code: str
    result: str
    actual_value: object = None
    threshold_value: object = None
    unit: str | None = None
    source_date: str | None = None
    source_time: str | None = None
    reason_code: str | None = None
    display_text: str | None = None


@dataclass(frozen=True)
class Decision:
    lifecycle_status: str
    candidate_grade: str | None
    score: Decimal | None
    observation_day: int | None
    primary_reasons: tuple[str, ...]
    evidence: tuple[Evidence, ...]


def d(value):
    return None if value is None else Decimal(str(value))


def ev(code, result, actual=None, threshold=None, unit=None, source_date=None,
       source_time=None, reason=None, text=None):
    if result not in {"pass", "fail", "unknown"}:
        raise ValueError(f"invalid evidence result: {result}")
    return Evidence(
        code, result, actual, threshold, unit, source_date, source_time, reason, text
    )


def _valid_bar(bar):
    values = [d(bar.get(key)) for key in ("open", "high", "low", "close")]
    return (
        all(value is not None and value > 0 for value in values)
        and values[2] <= min(values[0], values[3])
        <= max(values[0], values[3]) <= values[1]
        and d(bar.get("volume")) is not None
        and d(bar.get("volume")) >= 0
    )


def _reasons(evidence):
    return tuple(
        sorted({
            item.reason_code
            for item in evidence
            if item.result != "pass" and item.reason_code
        })
    )


def evaluate_candidate(
    *,
    event,
    bars,
    expected_dates,
    status,
    observation_day,
    context,
    stage,
    evaluation_date,
    tail_confirmation=None,
    previous_eliminated=False,
    calendar_available=True,
):
    """Evaluate only supplied, cutoff-filtered inputs; this function performs no reads."""
    evidence = []
    trade_date = str(evaluation_date)

    if not calendar_available or observation_day is None:
        evidence.append(ev(
            "OBSERVATION_WINDOW", "unknown", reason="MISSING_TRADING_CALENDAR"
        ))
        return Decision(
            "indeterminate", None, None, None, _reasons(evidence), tuple(evidence)
        )
    if observation_day == 0:
        evidence.append(ev(
            "OBSERVATION_WINDOW", "fail", 0, "D1-D5", "trading_day",
            event["trade_date"], reason="NOT_IN_D1_D5"
        ))
        return Decision("watching", None, None, 0, _reasons(evidence), tuple(evidence))
    if observation_day > 5:
        evidence.append(ev(
            "OBSERVATION_WINDOW", "fail", observation_day, "D1-D5", "trading_day",
            trade_date, reason="EXPIRED_AFTER_D5"
        ))
        return Decision(
            "expired", None, None, observation_day, _reasons(evidence), tuple(evidence)
        )
    evidence.append(ev(
        "OBSERVATION_WINDOW", "pass", observation_day, "D1-D5", "trading_day",
        trade_date
    ))

    if previous_eliminated:
        evidence.append(ev(
            "PERMANENT_ELIMINATION", "fail", True, reason="PREVIOUSLY_ELIMINATED"
        ))
        return Decision(
            "eliminated", None, None, observation_day, _reasons(evidence), tuple(evidence)
        )

    if status is None:
        evidence.append(ev(
            "SECURITY_STATUS", "unknown", reason="MISSING_SECURITY_STATUS"
        ))
    elif status.get("is_st") is None or status.get("is_suspended") is None:
        evidence.append(ev(
            "SECURITY_STATUS", "unknown", reason="MISSING_SECURITY_STATUS"
        ))
    elif status["is_st"]:
        evidence.append(ev(
            "SECURITY_STATUS", "fail", "ST", "non-ST", reason="STOCK_IS_ST"
        ))
    elif status["is_suspended"]:
        evidence.append(ev(
            "SECURITY_STATUS", "fail", "suspended", "tradable", reason="SUSPENDED"
        ))
        return Decision(
            "watching", None, None, observation_day, _reasons(evidence), tuple(evidence)
        )
    else:
        evidence.append(ev("SECURITY_STATUS", "pass", "tradable", "non-ST/tradable"))

    by_date = {str(bar["trade_date"]): bar for bar in bars}
    missing_dates = [day for day in expected_dates if day not in by_date]
    invalid_dates = [
        day for day in expected_dates
        if day in by_date and not _valid_bar(by_date[day])
    ]
    if missing_dates or invalid_dates:
        evidence.append(ev(
            "DAILY_BAR_COVERAGE", "unknown",
            {"missing": missing_dates, "invalid": invalid_dates},
            "all_effective_observation_days", reason="INSUFFICIENT_DAILY_BARS"
        ))
    else:
        evidence.append(ev(
            "DAILY_BAR_COVERAGE", "pass", len(expected_dates),
            len(expected_dates), "trading_day"
        ))

    observation_bars = [
        by_date[day] for day in expected_dates
        if day in by_date and _valid_bar(by_date[day])
    ]
    event_low = d(event.get("low"))
    event_close = d(event.get("close"))
    lows = [d(bar["low"]) for bar in observation_bars]
    if event_low is None or not lows:
        evidence.append(ev(
            "FIRST_LIMIT_LOW", "unknown", reason="INSUFFICIENT_DAILY_BARS"
        ))
    else:
        lowest = min(lows)
        evidence.append(ev(
            "FIRST_LIMIT_LOW", "fail" if lowest < event_low else "pass",
            lowest, event_low, "CNY", trade_date,
            reason="BROKE_FIRST_LIMIT_LOW" if lowest < event_low else None
        ))

    if event_close is None or event_close <= 0 or not lows:
        evidence.append(ev(
            "MAX_DRAWDOWN", "unknown", reason="INSUFFICIENT_DAILY_BARS"
        ))
    else:
        drawdown = (event_close - min(lows)) / event_close
        evidence.append(ev(
            "MAX_DRAWDOWN", "fail" if drawdown > MAX_DRAWDOWN else "pass",
            drawdown, MAX_DRAWDOWN, "ratio", trade_date,
            reason="MAX_DRAWDOWN_EXCEEDED" if drawdown > MAX_DRAWDOWN else None
        ))

    current = by_date.get(trade_date) if trade_date else None
    event_bar = by_date.get(str(event["trade_date"]))
    if (
        current is None
        or not _valid_bar(current)
        or event_bar is None
        or d(event_bar.get("volume")) in {None, 0}
    ):
        evidence.append(ev(
            "VOLUME_CONTRACTION", "unknown", reason="INSUFFICIENT_DAILY_BARS"
        ))
    else:
        ratio = d(current["volume"]) / d(event_bar["volume"])
        evidence.append(ev(
            "VOLUME_CONTRACTION", "fail" if ratio > Decimal(".7") else "pass",
            ratio, Decimal(".7"), "ratio", trade_date,
            reason="VOLUME_CONTRACTION_FAILED" if ratio > Decimal(".7") else None
        ))

    ordered = sorted(
        (bar for bar in bars if str(bar["trade_date"]) <= str(trade_date)),
        key=lambda bar: str(bar["trade_date"]),
    )
    current_index = next(
        (index for index, bar in enumerate(ordered)
         if str(bar["trade_date"]) == trade_date),
        None,
    )
    prior = ordered[max(0, (current_index or 0) - 5):(current_index or 0)]
    if (
        current is None
        or not _valid_bar(current)
        or len(prior) != 5
        or any(not _valid_bar(bar) for bar in prior)
    ):
        evidence.append(ev(
            "BEARISH_VOLUME_RISK", "unknown", reason="INSUFFICIENT_DAILY_BARS"
        ))
    else:
        average = sum(d(bar["volume"]) for bar in prior) / 5
        previous_close = d(prior[-1]["close"])
        if average == 0 or previous_close in {None, 0}:
            evidence.append(ev(
                "BEARISH_VOLUME_RISK", "unknown", reason="INSUFFICIENT_DAILY_BARS"
            ))
        else:
            change = (d(current["close"]) - previous_close) / previous_close
            volume_ratio = d(current["volume"]) / average
            bearish = (
                d(current["close"]) < d(current["open"])
                and change <= Decimal("-.03")
                and volume_ratio >= RISK_VOLUME_RATIO
            )
            evidence.append(ev(
                "BEARISH_VOLUME_RISK", "fail" if bearish else "pass",
                {"change": change, "volume_ratio": volume_ratio},
                {"change": Decimal("-.03"), "volume_ratio": RISK_VOLUME_RATIO},
                source_date=trade_date,
                reason="BEARISH_HIGH_VOLUME_BAR" if bearish else None
            ))

    # PR6.13A removes the obsolete sector-score dependency. IndustryService context
    # is attached by the caller for subsequent candidate scoring phases.

    score = d(context.get("daily_base_score")) if context else None
    classification = context.get("classification") if context else None
    complete_context = bool(
        context
        and context.get("is_complete")
        and not context.get("is_approximate")
        and score is not None
        and classification in GRADES
    )
    evidence.append(ev(
        "DAILY_CONTEXT", "pass" if complete_context else "unknown",
        {"score": score, "classification": classification},
        {"minimum_score": MIN_DAILY_BASE_SCORE, "classifications": sorted(GRADES)},
        reason=None if complete_context else "DAILY_CONTEXT_INCOMPLETE"
    ))

    permanent_fail_codes = {
        "STOCK_IS_ST", "BROKE_FIRST_LIMIT_LOW", "MAX_DRAWDOWN_EXCEEDED",
        "VOLUME_CONTRACTION_FAILED", "BEARISH_HIGH_VOLUME_BAR", "SECTOR_RETREAT",
    }
    reasons = _reasons(evidence)
    if permanent_fail_codes.intersection(reasons):
        return Decision(
            "eliminated", None, score, observation_day, reasons, tuple(evidence)
        )
    if any(item.result == "unknown" for item in evidence):
        if stage == "tail_preview":
            evidence.append(ev(
                "CLOSE_CONFIRMATION", "unknown",
                reason="PENDING_CLOSE_CONFIRMATION"
            ))
            return Decision(
                "pending_close_confirmation", None, score, observation_day,
                _reasons(evidence), tuple(evidence)
            )
        return Decision(
            "indeterminate", None, score, observation_day, reasons, tuple(evidence)
        )
    if score < MIN_DAILY_BASE_SCORE:
        evidence.append(ev(
            "EXECUTABLE_SCORE", "fail", score, MIN_DAILY_BASE_SCORE, "score",
            trade_date, reason="DAILY_SCORE_BELOW_EXECUTABLE"
        ))
        return Decision(
            "watching", None, score, observation_day,
            _reasons(evidence), tuple(evidence)
        )
    evidence.append(ev(
        "EXECUTABLE_SCORE", "pass", score, MIN_DAILY_BASE_SCORE, "score",
        trade_date
    ))
    grade = GRADES[classification]

    if stage == "tail_preview":
        if tail_confirmation is None or tail_confirmation.status != "confirmed":
            status_name = tail_confirmation.status if tail_confirmation else "missing"
            reason = (
                "INSUFFICIENT_MINUTE_BARS"
                if tail_confirmation is None or status_name == "indeterminate"
                else "TAIL_CONFIRMATION_MISSING"
            )
            evidence.append(ev(
                "TAIL_CONFIRMATION", "unknown", status_name, "confirmed",
                source_time=trade_date, reason=reason
            ))
            evidence.append(ev(
                "CLOSE_CONFIRMATION", "unknown",
                reason="PENDING_CLOSE_CONFIRMATION"
            ))
            return Decision(
                "pending_close_confirmation", None, score, observation_day,
                _reasons(evidence), tuple(evidence)
            )
        evidence.append(ev(
            "TAIL_CONFIRMATION", "pass", tail_confirmation.confirmation_time,
            "first_confirmation_by_as_of", source_time=tail_confirmation.confirmation_time
        ))
        return Decision(
            "eligible", grade, score, observation_day,
            _reasons(evidence), tuple(evidence)
        )

    evidence.append(ev(
        "CLOSE_CONFIRMATION", "pass", trade_date, "final_daily_close",
        source_date=trade_date
    ))
    return Decision(
        "confirmed", grade, score, observation_day,
        _reasons(evidence), tuple(evidence)
    )


def compare_preview(preview, close_decision):
    if preview is None:
        return "preview_missing"
    qualified = {"eligible", "confirmed"}
    if close_decision.lifecycle_status == "eliminated":
        return "eliminated"
    before = preview.get("lifecycle_status") in qualified
    after = close_decision.lifecycle_status in qualified
    if not before and after:
        return "newly_qualified"
    if before and after:
        rank = {"B": 0, "A": 1, "S": 2}
        old = rank.get(preview.get("candidate_grade"), -1)
        new = rank.get(close_decision.candidate_grade, -1)
        return "upgraded" if new > old else "downgraded" if new < old else "unchanged"
    if preview.get("lifecycle_status") == close_decision.lifecycle_status:
        return "unchanged"
    lifecycle_rank = {
        "eliminated": -2, "expired": -2, "indeterminate": -1, "watching": 0,
        "pending_close_confirmation": 1, "eligible": 2, "confirmed": 2,
    }
    return (
        "upgraded"
        if lifecycle_rank[close_decision.lifecycle_status]
        > lifecycle_rank.get(preview.get("lifecycle_status"), -2)
        else "downgraded"
    )


def evidence_dicts(decision):
    return [asdict(item) for item in decision.evidence]
