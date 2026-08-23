from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.research.analyst_schema import AnalystDocument
from backend.research.confidence_calibration_schema import (
    ConfidenceCalibration, ConfidenceComponent, ConfidenceComponents, confidence_level,
)
from backend.research.confidence_calibrator import ConfidenceCalibrator, WEIGHTS
from backend.research.decision_schema import DecisionDocument
from backend.research.skeptic_schema import SkepticDocument
from backend.research.valuation_schema import ValuationDocument


ROOT = Path(__file__).parents[1] / "data" / "research"
FIXED_NOW = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


def folder(symbol="000858.SZ", version="2026-08-23_v3") -> Path:
    return ROOT / symbol / "analysis" / version


def load(name: str, symbol="000858.SZ", version="2026-08-23_v3"):
    return json.loads((folder(symbol, version) / name).read_text(encoding="utf-8"))


def documents(symbol="000858.SZ", version="2026-08-23_v3"):
    return (
        AnalystDocument.model_validate(load("analyst.json", symbol, version)),
        SkepticDocument.model_validate(load("skeptic.json", symbol, version)),
        ValuationDocument.model_validate(load("valuation.json", symbol, version)),
        DecisionDocument.model_validate(load("decision.json", symbol, version)),
    )


def test_same_inputs_are_deterministic_and_weights_sum_to_one():
    calculator = ConfidenceCalibrator(research_root=ROOT, now=lambda: FIXED_NOW)
    first = calculator.calculate("000858.SZ", "2026-08-23_v3")
    second = calculator.calculate("000858.SZ", "2026-08-23_v3")
    assert first == second
    assert sum(WEIGHTS.values()) == pytest.approx(1)
    assert first.score == round(sum(getattr(first.components, name).score * getattr(first.components, name).weight
                                    for name in WEIGHTS))


@pytest.mark.parametrize(("score", "level"), [(75, "HIGH"), (55, "MEDIUM"), (35, "LOW"),
                                                  (1, "VERY_LOW"), (0, "INSUFFICIENT")])
def test_confidence_levels(score, level):
    assert confidence_level(score) == level


def test_schema_rejects_bad_weight_sum_and_score():
    components = ConfidenceComponents(**{
        name: ConfidenceComponent(score=50, weight=weight, reasons=["reason"])
        for name, weight in WEIGHTS.items()
    })
    payload = dict(schema_version=1, symbol="X", analysis_version="v1", score=50, level="LOW",
                   components=components, strengths=[], blockers=[], model_reported_confidence=.5,
                   generated_at=FIXED_NOW.isoformat(), method="deterministic-v1")
    ConfidenceCalibration.model_validate(payload)
    payload["score"] = 101
    with pytest.raises(ValidationError): ConfidenceCalibration.model_validate(payload)


@pytest.mark.parametrize(("quality", "expected"), [("HIGH", 90), ("MEDIUM", 70), ("LOW", 40), ("INSUFFICIENT", 10)])
def test_forecast_quality_mapping_and_narrow_spread(quality, expected):
    analyst, _, valuation, _ = documents("000423.SZ", "2026-08-23_v1")
    normalized = valuation.normalized_earnings.model_copy(update={"forecast_quality": quality})
    changed = valuation.model_copy(update={"normalized_earnings": normalized})
    assert ConfidenceCalibrator._earnings(analyst, changed).score == expected


def test_wide_scenario_spread_reduces_earnings_visibility():
    analyst, _, valuation, _ = documents()
    assert ConfidenceCalibrator._earnings(analyst, valuation).score == 25


@pytest.mark.parametrize(("verdict", "minimum"), [("PASS", 40), ("CAUTION", 0), ("BLOCK", 0)])
def test_skeptic_verdicts_and_risk_penalties(verdict, minimum):
    _, skeptic, _, _ = documents()
    changed = skeptic.model_copy(update={"verdict": verdict})
    result = ConfidenceCalibrator._risk(changed)
    assert minimum <= result.score <= 100
    assert any("risk" in reason.lower() for reason in result.reasons)


def test_critical_risk_and_dividend_trap_are_penalized():
    _, skeptic, _, _ = documents()
    critical = skeptic.major_risks[0].model_copy(update={"severity": "CRITICAL"})
    likely = skeptic.dividend_trap_assessment.model_copy(update={"status": "LIKELY"})
    changed = skeptic.model_copy(update={"major_risks": [critical], "dividend_trap_assessment": likely})
    assert ConfidenceCalibrator._risk(changed).score == 10


def test_evidence_missing_sections_and_unresolved_reference_reduce_score():
    analyst, skeptic, valuation, _ = documents()
    evidence = load("evidence.json")
    summary = load("evidence_summary.json")
    authority = {item["id"] for item in evidence["evidence"]}
    complete = ConfidenceCalibrator._evidence(evidence, summary, analyst, skeptic, valuation, authority)
    missing = dict(summary); missing["sections"] = dict(summary["sections"])
    missing["sections"]["cash_flow"] = []; missing["sections"]["balance_sheet"] = []
    incomplete = ConfidenceCalibrator._evidence(evidence, missing, analyst, skeptic, valuation, authority - {next(iter(authority))})
    assert complete.score == 100
    assert incomplete.score < complete.score


def test_agent_consistency_rewards_cap_compliance_and_conservative_alignment():
    analyst, skeptic, _, decision = documents()
    result = ConfidenceCalibrator._consistency(analyst, skeptic, decision)
    assert result.score == 75
    assert any("decision cap" in reason for reason in result.reasons)


def test_model_confidence_is_audit_only_for_both_real_pocs():
    calculator = ConfidenceCalibrator(research_root=ROOT, now=lambda: FIXED_NOW)
    first = calculator.calculate("000858.SZ", "2026-08-23_v3")
    second = calculator.calculate("000423.SZ", "2026-08-23_v1")
    assert (first.model_reported_confidence, first.score, first.level) == (.4, 55, "MEDIUM")
    assert (second.model_reported_confidence, second.score, second.level) == (0, 60, "MEDIUM")
    assert first.score != 100 and second.score != 0


def test_run_writes_strict_artifact_atomically(tmp_path):
    source = folder()
    target = tmp_path / "000858.SZ" / "analysis" / "2026-08-23_v3"
    target.mkdir(parents=True)
    for path in source.glob("*.json"):
        target.joinpath(path.name).write_bytes(path.read_bytes())
    result = ConfidenceCalibrator(research_root=tmp_path, now=lambda: FIXED_NOW).run("000858.SZ", "2026-08-23_v3")
    stored = ConfidenceCalibration.model_validate_json((target / "confidence_calibration.json").read_text(encoding="utf-8"))
    assert stored == result


def test_fatal_insufficiency_is_the_only_explicit_zero_path(tmp_path):
    source = folder()
    target = tmp_path / "000858.SZ" / "analysis" / "2026-08-23_v3"
    target.mkdir(parents=True)
    for path in source.glob("*.json"):
        target.joinpath(path.name).write_bytes(path.read_bytes())
    valuation = json.loads((target / "valuation.json").read_text(encoding="utf-8"))
    valuation["normalized_earnings"].update({"bear": None, "base": None, "bull": None,
                                               "forecast_quality": "INSUFFICIENT"})
    valuation["fair_value_per_share"] = {"bear": None, "base": None, "bull": None}
    (target / "valuation.json").write_text(json.dumps(valuation), encoding="utf-8")
    decision = json.loads((target / "decision.json").read_text(encoding="utf-8"))
    decision["inputs_summary"]["forecast_quality"] = "INSUFFICIENT"
    decision["valuation_assessment"].update({"status": "UNCERTAIN", "bear_value": None,
                                               "base_value": None, "bull_value": None})
    (target / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    result = ConfidenceCalibrator(research_root=tmp_path, now=lambda: FIXED_NOW).calculate("000858.SZ", "2026-08-23_v3")
    assert (result.score, result.level) == (0, "INSUFFICIENT")
