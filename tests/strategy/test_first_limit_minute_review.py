from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.strategy.first_limit.minute_review import (
    Confirmation,
    confirm_tail_entry,
    evaluate_all_stops,
    evaluate_stop_rule,
    s1_followup_features,
    s1_metrics,
    stop_rule_metrics,
)


TZ = ZoneInfo("Asia/Shanghai")


def bar(stamp, o=10, h=10.1, low=9.9, close=10, volume=100):
    from backend.strategy.first_limit.minute_review import MinuteBar
    return MinuteBar(
        datetime.fromisoformat(stamp).replace(tzinfo=TZ),
        *map(Decimal, map(str, (o, h, low, close, volume))),
    )


def confirmed(at="2026-02-23T14:42:00", price=10):
    return Confirmation(
        "confirmed", "first_tail_confirmation",
        datetime.fromisoformat(at).replace(tzinfo=TZ).isoformat(timespec="seconds"),
        Decimal(str(price)), Decimal(str(price)), Decimal("5"), 10000, Decimal(".1"), 3,
    )


def test_tail_confirmation_uses_first_satisfying_minute_without_reading_future():
    def bars():
        yield bar("2026-02-23T14:40:00", close=10.1)
        yield bar("2026-02-23T14:41:00", o=10.1, close=10.05)
        yield bar("2026-02-23T14:42:00", o=10.05, close=10.1)
        raise AssertionError("future minute was read after confirmation")

    result = confirm_tail_entry(bars(), Decimal("9.5"))
    assert result.status == "confirmed"
    assert result.confirmation_time.endswith("14:42:00+08:00")
    assert result.entry_price == Decimal("10.12")


@pytest.mark.parametrize(
    ("minutes", "reason"),
    [
        (["14:41", "14:42"], "tail_window_does_not_start_at_1440"),
        (["14:40", "14:42"], "minute_gap_before_confirmation"),
        (["14:40"], "tail_window_ended_before_1455"),
    ],
)
def test_tail_confirmation_marks_missing_or_non_contiguous_data_indeterminate(minutes, reason):
    bars = [
        bar(f"2026-02-23T{minute}:00", o=10.1, close=10.0)
        for minute in minutes
    ]
    assert confirm_tail_entry(bars, Decimal("9.5")).reason == reason


def test_tail_confirmation_rejects_locked_limit_up_and_no_confirmed_pattern():
    bars = [
        bar(f"2026-02-23T14:{minute:02d}:00", 11, 11, 11, 11, 100)
        for minute in range(40, 56)
    ]
    result = confirm_tail_entry(bars, Decimal("9.5"), Decimal("11"))
    assert result.status == "rejected"


def test_s1_normal_breach_uses_next_minute_open_and_never_reenters():
    entry = confirmed()
    bars = [
        bar("2026-02-24T09:30:00", 10.2, 10.3, 9.9, 10.0),
        bar("2026-02-24T09:31:00", 9.8, 10.0, 9.7, 9.9),
        bar("2026-02-24T09:32:00", 11, 11.2, 10.9, 11.1),
    ]
    result = evaluate_stop_rule("S1", bars, entry, Decimal("10"))
    assert result["status"] == "closed"
    assert result["trigger_time"].endswith("09:30:00+08:00")
    assert result["exit_price_raw"] == Decimal("9.8")
    assert result["exit_time"].endswith("09:31:00+08:00")


def test_s1_decision_stops_consuming_minutes_after_execution_bar():
    entry = confirmed()
    def bars():
        yield bar("2026-02-24T09:30:00", 10.2, 10.3, 9.9, 10)
        yield bar("2026-02-24T09:31:00", 9.8, 9.9, 9.7, 9.8)
        raise AssertionError("decision read a future minute after execution")
    assert evaluate_stop_rule("S1", bars(), entry, Decimal("10"))["status"] == "closed"


def test_s1_open_gap_uses_actual_open_and_applies_adverse_tick_rounding():
    result = evaluate_stop_rule(
        "S1",
        [bar("2026-02-24T09:30:00", 9.8, 9.9, 9.7, 9.8)],
        confirmed(),
        Decimal("10"),
    )
    assert result["trigger_reason"] == "open_gap_below_stop"
    assert result["exit_price_raw"] == Decimal("9.8")
    assert result["exit_price"] == Decimal("9.79")


def test_same_minute_profit_and_s1_stop_is_conservative_and_ambiguous():
    result = evaluate_stop_rule(
        "S1",
        [
            bar("2026-02-24T09:30:00", 10.1, 10.3, 9.9, 10.1),
            bar("2026-02-24T09:31:00", 9.9, 10, 9.8, 9.9),
        ],
        confirmed(price=10),
        Decimal("10"),
    )
    assert result["trigger_reason"] == "S1_stop"
    assert result["intraday_path_ambiguous"]
    assert result["audit"]["execution"] == "conservative_stop_first"


def test_no_next_minute_gap_and_lower_limit_lock_are_not_fake_fills():
    entry = confirmed()
    no_next = evaluate_stop_rule(
        "S1", [bar("2026-02-24T09:30:00", 10.1, 10.2, 9.9, 10)], entry, Decimal("10")
    )
    assert no_next["status"] == "unresolved" and no_next["trigger_reason"] == "no_next_minute_bar"
    gap = evaluate_stop_rule(
        "S1",
        [
            bar("2026-02-24T09:30:00", 10.1, 10.2, 9.9, 10),
            bar("2026-02-24T09:32:00", 9.8, 9.9, 9.7, 9.8),
        ],
        entry,
        Decimal("10"),
    )
    assert gap["status"] == "indeterminate"
    locked = evaluate_stop_rule(
        "S1",
        [
            bar("2026-02-24T09:30:00", 10.1, 10.2, 9.9, 10),
            bar("2026-02-24T09:31:00", 9, 9, 9, 9),
        ],
        entry,
        Decimal("10"),
        {"2026-02-24": Decimal("9")},
    )
    assert locked["status"] == "unresolved"


def test_s0_to_s4_have_distinct_trigger_definitions():
    entry = confirmed(price=10)
    start = datetime(2026, 2, 24, 9, 30, tzinfo=TZ)
    bars = []
    for index in range(17):
        moment = start + timedelta(minutes=index)
        close = Decimal("9.9") if index < 15 else Decimal("9.8")
        bars.append(
            bar(moment.replace(tzinfo=None).isoformat(), 9.95, 10.01, 9.8, close)
        )
    results = evaluate_all_stops(bars, entry, Decimal("10"))
    assert results["S1"]["trigger_time"].endswith("09:30:00+08:00")
    assert results["S2"]["trigger_time"].endswith("09:30:00+08:00")
    assert results["S4"]["trigger_time"].endswith("09:44:00+08:00")
    assert results["S0"]["trigger_reason"] != "S1_stop"
    assert results["S3"]["trigger_reason"] != "S1_stop"


def test_s2_s3_and_s4_exact_boundaries():
    entry = confirmed(price=10)
    s2 = evaluate_stop_rule(
        "S2",
        [
            bar("2026-02-24T09:30:00", 10, 10.1, 9.91, 10),
            bar("2026-02-24T09:31:00", 10, 10.1, 9.89, 9.95),
            bar("2026-02-24T09:32:00", 9.9, 10, 9.8, 9.9),
        ],
        entry,
        Decimal("10"),
    )
    assert s2["trigger_time"].endswith("09:31:00+08:00")
    s3 = evaluate_stop_rule(
        "S3",
        [
            bar("2026-02-24T15:00:00", 10, 10.1, 9.8, 9.9),
            bar("2026-02-25T09:30:00", 9.8, 9.9, 9.7, 9.8),
        ],
        entry,
        Decimal("10"),
    )
    assert s3["trigger_time"].endswith("15:00:00+08:00")
    assert s3["exit_price_raw"] == Decimal("9.8")
    start = datetime(2026, 2, 24, 9, 30, tzinfo=TZ)
    s4_bars = []
    for index in range(14):
        moment = start + timedelta(minutes=index)
        s4_bars.append(bar(moment.replace(tzinfo=None).isoformat(), 9.9, 10, 9.8, 9.9))
    recovered = start + timedelta(minutes=14)
    s4_bars.append(bar(recovered.replace(tzinfo=None).isoformat(), 10, 10.1, 9.9, 10))
    for index in range(15, 31):
        moment = start + timedelta(minutes=index)
        s4_bars.append(bar(moment.replace(tzinfo=None).isoformat(), 9.9, 10, 9.8, 9.9))
    s4 = evaluate_stop_rule("S4", s4_bars, entry, Decimal("10"))
    assert s4["trigger_time"].endswith("09:59:00+08:00")


def test_s1_followup_and_metrics_are_analysis_only_after_exit():
    entry = confirmed(price=10)
    bars = [
        bar("2026-02-24T09:30:00", 10.1, 10.2, 9.9, 10),
        bar("2026-02-24T09:31:00", 9.8, 9.9, 9.7, 9.8),
        bar("2026-02-24T09:32:00", 10.1, 10.3, 10, 10.2),
    ]
    stops = evaluate_all_stops(bars, entry, Decimal("10"))
    followup = s1_followup_features(stops, bars, entry, Decimal("10"))
    assert followup["s1_triggered"] and followup["reclaimed_o0"] and followup["same_day_plus_2pct"]
    metrics = s1_metrics([{"stops": stops, "followup": followup}])
    assert metrics["s1_trigger_count"] == 1
    assert stop_rule_metrics([{"stops": stops, "followup": followup}], "S1")["sample_count"] == 1


def test_incomplete_s1_followup_does_not_turn_unobserved_outcome_into_false():
    entry = confirmed(price=10)
    bars = [
        bar("2026-02-24T09:30:00", 10.1, 10.1, 9.9, 9.95),
        bar("2026-02-24T09:31:00", 9.8, 9.9, 9.7, 9.8),
    ]
    stops = evaluate_all_stops(bars, entry, Decimal("10"))
    followup = s1_followup_features(stops, bars, entry, Decimal("10"))
    assert followup["s1_triggered"] is True
    assert followup["reclaimed_o0"] is None
    assert followup["same_day_plus_2pct"] is None
    assert followup["rose_within_3_sessions"] is None
    metrics = s1_metrics([{"stops": stops, "followup": followup}])
    assert metrics["s1_reclaimed_o0_ratio"] is None


def test_s1_metrics_do_not_count_take_profit_or_time_exit_as_s1_trigger():
    entry = confirmed(price=10)
    bars = [
        bar("2026-02-24T09:30:00", 10.1, 10.3, 10, 10.2),
        bar("2026-02-24T09:31:00", 10.2, 10.3, 10.1, 10.2),
    ]
    stops = evaluate_all_stops(bars, entry, Decimal("10"))
    followup = s1_followup_features(stops, bars, entry, Decimal("10"))
    record = {"stops": stops, "followup": followup}
    assert stops["S1"]["trigger_reason"] == "take_profit_2pct"
    assert followup["s1_triggered"] is False
    assert s1_metrics([record])["s1_trigger_count"] == 0
    assert stop_rule_metrics([record], "S1")["trigger_count"] == 0
