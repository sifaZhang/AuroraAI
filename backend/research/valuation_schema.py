from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scenarios(Strict):
    bear: float | None = None
    base: float | None = None
    bull: float | None = None

    @model_validator(mode="after")
    def ordered(self):
        values = [self.bear, self.base, self.bull]
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("bear/base/bull must be all present or all null")
        if all(value is not None for value in values) and not (self.bear <= self.base <= self.bull):
            raise ValueError("bear <= base <= bull required")
        return self


class Normalized(Scenarios):
    period: str
    unit: str
    method: str
    key_assumptions: list[str]
    assumption_metadata: list[dict[str, object]]
    evidence_ids: list[str]
    forecast_quality: Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
    confidence: float | None = Field(default=None, ge=0, le=1)


class Method(Strict):
    primary: str
    secondary: list[str]
    reasoning: str
    multiple_source: Literal["assumption"]


class Multiples(Scenarios):
    metric: Literal["PE", "PB", "EV_FCF", "other"]
    assumption_rationale: list[str]


class ReverseScenario(Strict):
    scenario: str | None = None
    name: str | None = None
    multiple: float = Field(gt=0)
    implied_earnings: float = Field(gt=0)
    interpretation: str


class Reverse(Strict):
    current_price: float | None
    current_market_cap: float | None = Field(default=None, gt=0)
    implied_normalized_earnings: float | None = None
    implied_multiple: float | None = Field(default=None, gt=0)
    scenarios: list[ReverseScenario] = []
    interpretation: str


class Dividend(Strict):
    current_yield: float | None = Field(default=None, ge=0, le=1)
    sustainable_dividend: float | None = None
    sustainable_yield: float | None = Field(default=None, ge=0, le=1)
    assessment: str


class ValuationDocument(Strict):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    generation: dict[str, object]
    normalized_earnings: Normalized
    valuation_method: Method
    multiples: Multiples
    fair_value_per_share: Scenarios
    reverse_valuation: Reverse
    dividend_cross_check: Dividend
    limitations: list[str]
    confidence: float = Field(ge=0, le=1)


def validate_valuation(payload: object, *, symbol: str, version: str, evidence_ids: set[str]) -> ValuationDocument:
    document = ValuationDocument.model_validate(payload)
    if document.symbol != symbol:
        raise ValueError("valuation symbol does not match input pack")
    if document.analysis_version != version:
        raise ValueError("valuation version does not match input pack")
    normal = document.normalized_earnings
    has_earnings = normal.bear is not None
    if has_earnings:
        if normal.forecast_quality == "INSUFFICIENT":
            raise ValueError("numeric normalized earnings cannot be INSUFFICIENT")
        if not normal.evidence_ids or not normal.assumption_metadata:
            raise ValueError("numeric normalized earnings requires evidence_ids and assumption_metadata")
    elif normal.forecast_quality != "INSUFFICIENT":
        raise ValueError("null normalized earnings requires INSUFFICIENT forecast_quality")
    fair_values = [document.fair_value_per_share.bear, document.fair_value_per_share.base, document.fair_value_per_share.bull]
    if any(value is not None for value in fair_values) and (not has_earnings or document.multiples.bear is None):
        raise ValueError("fair value requires normalized earnings and multiples")
    if document.multiples.bear is not None and not document.multiples.assumption_rationale:
        raise ValueError("numeric multiples require assumption_rationale")
    unknown = set(normal.evidence_ids) - evidence_ids
    if unknown:
        raise ValueError(f"normalized earnings references unknown evidence: {', '.join(sorted(unknown))}")
    return document
