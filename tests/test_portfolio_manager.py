from __future__ import annotations

import pytest

from backend.research.decision_schema import calculate_decision_cap, validate_decision, valuation_status


def inputs(**changes):
    value = {"yield_signal": "heavy", "latest_price": 10.0, "three_year_average_yield": .07, "entry_yield": 5.0, "add_yield": 6.0, "heavy_yield": 7.0, "grade": "S", "skeptic_verdict": "CAUTION", "skeptic_cap": "ENTRY", "valuation_confidence": .3, "forecast_quality": "LOW"}
    value.update(changes); return value


def valuation():
    return {"normalized_earnings": {"forecast_quality": "LOW", "confidence": .3}, "fair_value_per_share": {"bear": 7., "base": 9., "bull": 12.}}


def decision(data=None):
    authoritative = inputs() if data is None else data
    return {"schema_version": 1, "symbol": "000858.SZ", "analysis_version": "v1", "generation": {}, "inputs_summary": authoritative, "research_decision": "entry", "decision_confidence": .45, "decision_cap": calculate_decision_cap(authoritative["skeptic_cap"], authoritative["forecast_quality"]), "exceptional_reason": None, "decision_rationale": {"summary": "Limited initial exposure is supported.", "supporting_points": ["yield support"], "limiting_points": ["uncertainty"]}, "valuation_assessment": {"status": valuation_status(authoritative["latest_price"], 9., authoritative["forecast_quality"]), "current_price": 10., "bear_value": 7., "base_value": 9., "bull_value": 12., "interpretation": "Low-confidence base estimate is not precise."}, "yield_assessment": {"signal": authoritative["yield_signal"], "assessment": "Yield is attractive."}, "risk_assessment": {"skeptic_verdict": authoritative["skeptic_verdict"], "risk_level": "HIGH", "summary": "Risks remain."}, "upgrade_conditions": [], "downgrade_conditions": [], "invalidation_conditions": [], "key_uncertainties": [], "conflicts": []}


@pytest.mark.parametrize(("skeptic", "quality", "expected"), [("WATCH", "HIGH", "watch"), ("ENTRY", "HIGH", "entry"), ("HEAVY", "LOW", "entry"), ("HEAVY", "MEDIUM", "add"), ("HEAVY", "HIGH", "heavy"), ("HEAVY", "INSUFFICIENT", "watch")])
def test_decision_cap(skeptic, quality, expected):
    assert calculate_decision_cap(skeptic, quality) == expected


def test_rejects_decision_above_cap_and_forged_inputs():
    payload = decision(); payload["research_decision"] = "add"
    with pytest.raises(ValueError, match="exceeds"):
        validate_decision(payload, symbol="000858.SZ", version="v1", inputs=inputs(), valuation=valuation(), skeptic={"verdict": "CAUTION"})
    payload = decision(); payload["inputs_summary"]["yield_signal"] = "watch"
    with pytest.raises(ValueError, match="inputs_summary"):
        validate_decision(payload, symbol="000858.SZ", version="v1", inputs=inputs(), valuation=valuation(), skeptic={"verdict": "CAUTION"})


def test_rejects_add_when_price_exceeds_base_and_invalid_extra_fields():
    data = inputs(skeptic_cap="ADD", forecast_quality="MEDIUM", valuation_confidence=.5)
    payload = decision(data); payload["research_decision"] = "add"; payload["decision_cap"] = "add"; payload["valuation_assessment"]["status"] = valuation_status(10., 9., "MEDIUM")
    with pytest.raises(ValueError, match="add decision"):
        validate_decision(payload, symbol="000858.SZ", version="v1", inputs=data, valuation=valuation(), skeptic={"verdict": "CAUTION"})
    payload = decision(); payload["trade_size"] = "10%"
    with pytest.raises(Exception):
        validate_decision(payload, symbol="000858.SZ", version="v1", inputs=inputs(), valuation=valuation(), skeptic={"verdict": "CAUTION"})


@pytest.mark.parametrize("phrase", ["sell the stock", "buy now", "reduce position", "stop loss at 40", "target price 60"])
def test_rejects_trading_instruction_language(phrase):
    payload = decision(); payload["decision_rationale"]["summary"] = phrase
    with pytest.raises(ValueError, match="trading instruction"):
        validate_decision(payload, symbol="000858.SZ", version="v1", inputs=inputs(), valuation=valuation(), skeptic={"verdict": "CAUTION"})


@pytest.mark.parametrize("phrase", ["downgrade the research decision if cash flow deteriorates", "remain on watch until earnings recovery is confirmed", "the thesis would be invalidated by structural erosion in core profitability"])
def test_allows_research_language(phrase):
    payload = decision(); payload["decision_rationale"]["summary"] = phrase
    assert validate_decision(payload, symbol="000858.SZ", version="v1", inputs=inputs(), valuation=valuation(), skeptic={"verdict": "CAUTION"})
