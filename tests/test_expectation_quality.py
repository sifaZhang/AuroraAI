from datetime import date
import csv
import sqlite3

from backend.expectation_gap.quality import evaluate_quality
from backend.expectation_gap.database import migrate
from backend.collector.import_expectation_quality_overrides import import_overrides


def base_row(**updates):
    row = {"last_price": 10, "price_time": "2026-07-20", "morningstar_fair_value": 20,
           "morningstar_gap_pct": 100, "morningstar_data_date": "2026-07-19",
           "analyst_average_target": 15, "analyst_gap_pct": 50, "analyst_data_date": "2026-07-19",
           "analyst_count": 3}
    row.update(updates)
    return row


def test_extreme_gap_is_not_rankable_but_warning_gap_is():
    suspicious = evaluate_quality(base_row(morningstar_gap_pct=500), [], today=date(2026, 7, 20))
    assert suspicious["morningstar_quality_status"] == "suspicious"
    assert suspicious["morningstar_is_rankable"] is False
    warning = evaluate_quality(base_row(morningstar_gap_pct=250), [], today=date(2026, 7, 20))
    assert warning["morningstar_quality_status"] == "warning"
    assert warning["morningstar_is_rankable"] is True


def test_corporate_action_after_target_excludes_only_affected_ranking():
    result = evaluate_quality(base_row(morningstar_data_date="2026-01-01", analyst_data_date="2026-07-19"),
                              [date(2026, 2, 1)], today=date(2026, 7, 20))
    assert result["morningstar_is_rankable"] is False
    assert "possible_corporate_action" in result["morningstar_quality_reasons"]
    assert result["analyst_is_rankable"] is True


def test_invalid_and_stale_inputs_are_derived_only():
    row = base_row(last_price=0)
    result = evaluate_quality(row, [], today=date(2026, 7, 20))
    assert result["quality_status"] == "excluded"
    assert "invalid_price" in result["quality_reasons"]
    assert row["last_price"] == 0


def test_low_analyst_coverage_is_warning():
    result = evaluate_quality(base_row(analyst_count=2), [], today=date(2026, 7, 20))
    assert result["analyst_is_rankable"] is False
    assert "low_analyst_coverage" in result["analyst_quality_reasons"]


def split_action(event_date="2026-07-10"):
    return {"corporate_action_type": "split", "effective_date": event_date, "ratio": "1:8"}


def test_action_within_30_days_before_target_triggers_review():
    result = evaluate_quality(base_row(analyst_data_date="2026-07-19"), [split_action()], today=date(2026, 7, 20))
    assert "corporate_action_review" in result["analyst_quality_reasons"]


def test_action_within_30_days_after_target_triggers_review():
    result = evaluate_quality(base_row(analyst_data_date="2026-07-01"), [split_action("2026-07-20")], today=date(2026, 7, 20))
    assert "corporate_action_review" in result["analyst_quality_reasons"]


def test_action_more_than_30_days_away_does_not_trigger_review():
    result = evaluate_quality(base_row(analyst_data_date="2026-05-01"), [split_action()], today=date(2026, 7, 20))
    assert "corporate_action_review" not in result["analyst_quality_reasons"]


def test_hesai_unadjusted_target_is_detected_without_mutating_raw_target():
    row = base_row(last_price=14.57, analyst_average_target=240.79, analyst_gap_pct=1552.6424159231296,
                   analyst_count=16, analyst_data_date="2026-07-19")
    result = evaluate_quality(row, [split_action()], today=date(2026, 7, 20))
    assert "possible_unadjusted_target" in result["analyst_quality_reasons"]
    trial = result["analyst_quality_details"]["possible_unadjusted_target"][0]
    assert round(trial["trial_target"], 6) == round(240.79 / 8, 6)
    assert -80 <= trial["trial_gap_pct"] <= 200
    assert row["analyst_average_target"] == 240.79


def test_single_analyst_extreme_excludes_only_analyst():
    result = evaluate_quality(base_row(analyst_count=1, analyst_gap_pct=300), [], today=date(2026, 7, 20))
    assert result["analyst_quality_status"] == "excluded"
    assert result["analyst_is_rankable"] is False
    assert result["morningstar_is_rankable"] is True


def test_low_price_without_targets_only_adds_label():
    result = evaluate_quality(base_row(last_price=.5, morningstar_fair_value=None, morningstar_gap_pct=None,
        analyst_average_target=None, analyst_gap_pct=None), [], today=date(2026, 7, 20))
    assert "low_price" in result["quality_reasons"]
    assert "low_price_extreme_gap" not in result["quality_reasons"]


def test_low_price_extreme_gap_is_source_specific():
    result = evaluate_quality(base_row(last_price=.5, morningstar_fair_value=2, morningstar_gap_pct=300,
        analyst_average_target=1, analyst_gap_pct=100), [], today=date(2026, 7, 20))
    assert "low_price_extreme_gap" in result["morningstar_quality_reasons"]
    assert "low_price_extreme_gap" not in result["analyst_quality_reasons"]


def test_manual_override_is_source_specific_and_does_not_mutate_target():
    row = base_row()
    result = evaluate_quality(row, [], today=date(2026, 7, 20), overrides={"analyst": {
        "action": "exclude", "reason": "manual_check", "note": "test", "reviewed_at": "2026-07-20"}})
    assert result["analyst_is_rankable"] is False
    assert result["morningstar_is_rankable"] is True
    assert row["analyst_average_target"] == 15
    allowed = evaluate_quality(row, [], today=date(2026, 7, 20), overrides={"analyst": {
        "action": "allow", "reason": "reviewed", "note": "test", "reviewed_at": "2026-07-20"}})
    assert allowed["analyst_is_rankable"] is True
    assert row["analyst_average_target"] == 15


def test_override_import_is_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "overrides.db"); connection.row_factory = sqlite3.Row; migrate(connection)
    connection.execute("""INSERT INTO stocks(futu_code,symbol,name,market,exchange,created_at,updated_at)
        VALUES('HK.02525','02525','Hesai','HK','HK','2026-07-20','2026-07-20')""")
    path = tmp_path / "overrides.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle); writer.writerow(["code","source","action","reason","note","reviewed_at"])
        writer.writerow(["HK.02525","analyst","exclude","possible_unadjusted_target","review","2026-07-20"])
    assert import_overrides(connection, path)["imported"] == 1
    assert import_overrides(connection, path)["imported"] == 1
    assert connection.execute("SELECT COUNT(*) FROM expectation_quality_overrides").fetchone()[0] == 1
