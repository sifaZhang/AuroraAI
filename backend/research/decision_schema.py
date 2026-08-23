from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Decision = Literal["avoid", "watch", "entry", "add", "heavy"]
ValuationStatus = Literal["DEEPLY_UNDERVALUED", "UNDERVALUED", "FAIR", "FAIR_TO_EXPENSIVE", "EXPENSIVE", "EXTREMELY_EXPENSIVE", "UNCERTAIN"]
ORDER = {"avoid": 0, "watch": 1, "entry": 2, "add": 3, "heavy": 4}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputsSummary(Strict):
    yield_signal: Decision
    latest_price: float | None
    three_year_average_yield: float | None
    entry_yield: float | None
    add_yield: float | None
    heavy_yield: float | None
    grade: Literal["S", "A", "B"] | None
    skeptic_verdict: Literal["PASS", "CAUTION", "BLOCK"]
    skeptic_cap: Literal["WATCH", "ENTRY", "ADD", "HEAVY"]
    valuation_confidence: float = Field(ge=0, le=1)
    forecast_quality: Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class Rationale(Strict):
    summary: str = Field(min_length=1)
    supporting_points: list[str]
    limiting_points: list[str]


class ValuationAssessment(Strict):
    status: ValuationStatus
    current_price: float | None
    bear_value: float | None
    base_value: float | None
    bull_value: float | None
    interpretation: str


class YieldAssessment(Strict):
    signal: Decision
    assessment: str


class RiskAssessment(Strict):
    skeptic_verdict: Literal["PASS", "CAUTION", "BLOCK"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str


class Condition(Strict):
    condition: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    category: Literal["earnings", "cash_flow", "valuation", "dividend", "industry", "governance", "other"]


class Conflict(Strict):
    topic: str = Field(min_length=1)
    analyst_view: str = Field(min_length=1)
    skeptic_view: str = Field(min_length=1)
    portfolio_manager_resolution: str = Field(min_length=1)


class DecisionDocument(Strict):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    generation: dict[str, object]
    inputs_summary: InputsSummary
    research_decision: Decision
    decision_confidence: float = Field(ge=0, le=1)
    decision_cap: Decision
    exceptional_reason: str | None = None
    decision_rationale: Rationale
    valuation_assessment: ValuationAssessment
    yield_assessment: YieldAssessment
    risk_assessment: RiskAssessment
    upgrade_conditions: list[Condition]
    downgrade_conditions: list[Condition]
    invalidation_conditions: list[Condition]
    key_uncertainties: list[str]
    conflicts: list[Conflict]


def valuation_status(current_price: float | None, base_value: float | None, forecast_quality: str) -> ValuationStatus:
    if forecast_quality == "INSUFFICIENT" or current_price is None or base_value is None or base_value <= 0:
        return "UNCERTAIN"
    ratio = current_price / base_value
    if ratio <= .70: return "DEEPLY_UNDERVALUED"
    if ratio <= .90: return "UNDERVALUED"
    if ratio <= 1.10: return "FAIR"
    if ratio <= 1.30: return "FAIR_TO_EXPENSIVE"
    if ratio <= 1.60: return "EXPENSIVE"
    return "EXTREMELY_EXPENSIVE"


def calculate_decision_cap(skeptic_cap: str, forecast_quality: str) -> Decision:
    quality_cap = {"INSUFFICIENT": "watch", "LOW": "entry", "MEDIUM": "add", "HIGH": "heavy"}[forecast_quality]
    skeptic = skeptic_cap.lower()
    return min((skeptic, quality_cap), key=ORDER.__getitem__)


def validate_decision(payload: object, *, symbol: str, version: str, inputs: dict[str, object], valuation: dict[str, object], skeptic: dict[str, object]) -> DecisionDocument:
    document = DecisionDocument.model_validate(payload)
    if document.symbol != symbol or document.analysis_version != version:
        raise ValueError("decision symbol or analysis_version does not match inputs")
    if document.inputs_summary.model_dump() != inputs:
        raise ValueError("inputs_summary does not match authoritative inputs")
    expected_cap = calculate_decision_cap(str(inputs["skeptic_cap"]), str(inputs["forecast_quality"]))
    if document.decision_cap != expected_cap:
        raise ValueError("decision_cap does not match constraint calculation")
    if ORDER[document.research_decision] > ORDER[document.decision_cap]:
        raise ValueError("research_decision exceeds decision_cap")
    if ORDER[document.research_decision] > ORDER[document.inputs_summary.yield_signal] and not document.exceptional_reason:
        raise ValueError("decision above yield_signal requires exceptional_reason")
    fair = valuation.get("fair_value_per_share", {})
    base, bull = fair.get("base"), fair.get("bull")
    price = inputs["latest_price"]
    if document.valuation_assessment.status != valuation_status(price, base, str(inputs["forecast_quality"])):
        raise ValueError("valuation_assessment.status does not match valuation inputs")
    if document.research_decision == "add":
        if inputs["skeptic_verdict"] == "BLOCK" or inputs["skeptic_cap"] not in {"ADD", "HEAVY"} or inputs["forecast_quality"] == "INSUFFICIENT" or inputs["valuation_confidence"] < .40 or (price is not None and base is not None and price > base):
            raise ValueError("add decision does not satisfy minimum constraints")
    if document.research_decision == "heavy":
        upside = (base - price) / price if price and base is not None else None
        if inputs["skeptic_verdict"] != "PASS" or inputs["skeptic_cap"] != "HEAVY" or inputs["forecast_quality"] not in {"MEDIUM", "HIGH"} or inputs["valuation_confidence"] < .60 or (price is not None and base is not None and price > base) or upside is None or upside < .15:
            raise ValueError("heavy decision does not satisfy minimum constraints")
    if skeptic.get("verdict") == "BLOCK" and document.research_decision not in {"avoid", "watch"}:
        raise ValueError("BLOCK skeptic verdict only permits avoid or watch")
    serialized = document.model_dump_json().lower()
    forbidden = re.compile(r"\b(buy now|sell now|buy shares|sell shares|increase position|reduce position|position size|stop[- ]?loss|target price|take profit|allocate\s+\d+(?:\.\d+)?%|\d+(?:\.\d+)?%\s+position|sell (?:or|the)|buy (?:or|the))\b")
    if forbidden.search(serialized):
        raise ValueError("decision must not contain trading instruction language")
    return document
