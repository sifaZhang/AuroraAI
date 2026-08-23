from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(StrictModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    claim_type: Literal["FACT", "INFERENCE", "ASSUMPTION"]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class Assessment(StrictModel):
    score: float | None = Field(default=None, ge=0, le=10)
    reason: str


class QualityAssessment(StrictModel):
    business_quality: Assessment
    earnings_quality: Assessment
    dividend_quality: Assessment
    governance_quality: Assessment


class CompanyClassification(StrictModel):
    primary_research_type: str
    secondary_research_type: str | None = None
    business_model_summary: str


class InvestmentThesis(StrictModel):
    summary: str
    positive_drivers: list[str]
    market_concerns: list[str]


class NormalizedEarnings(StrictModel):
    unit: str
    period: str
    bear: float | None = None
    base: float | None = None
    bull: float | None = None
    method: str
    key_drivers: list[str]
    limitations: list[str]


class Valuation(StrictModel):
    primary_method: str
    secondary_methods: list[str]
    bear_value_per_share: float | None = None
    base_value_per_share: float | None = None
    bull_value_per_share: float | None = None
    methodology: str
    limitations: list[str]


class ProvisionalView(StrictModel):
    summary: str
    valuation_attractiveness: Literal["very_attractive", "attractive", "neutral", "expensive", "very_expensive", "uncertain"]
    confidence: float = Field(ge=0, le=1)


class AnalystDocument(StrictModel):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    company_classification: CompanyClassification
    investment_thesis: InvestmentThesis
    quality_assessment: QualityAssessment
    normalized_earnings: NormalizedEarnings
    valuation: Valuation
    claims: list[Claim]
    key_risks: list[str]
    provisional_view: ProvisionalView
    generation: dict[str, object]

    @model_validator(mode="after")
    def ordered_scenarios(self) -> "AnalystDocument":
        for values, label in (([self.normalized_earnings.bear, self.normalized_earnings.base, self.normalized_earnings.bull], "normalized earnings"),
                              ([self.valuation.bear_value_per_share, self.valuation.base_value_per_share, self.valuation.bull_value_per_share], "valuation")):
            present = [value for value in values if value is not None]
            if present and len(present) != 3:
                raise ValueError(f"{label} must provide bear/base/bull together or leave all null")
            if len(present) == 3 and not (present[0] <= present[1] <= present[2]):
                raise ValueError(f"{label} must satisfy bear <= base <= bull")
        return self


def validate_analyst(payload: object, *, symbol: str, analysis_version: str, evidence_ids: set[str]) -> AnalystDocument:
    document = AnalystDocument.model_validate(payload)
    if document.symbol != symbol:
        raise ValueError("analyst symbol does not match input pack")
    if document.analysis_version != analysis_version:
        raise ValueError("analyst analysis_version does not match input pack")
    for claim in document.claims:
        if claim.claim_type == "FACT":
            if not claim.evidence_ids:
                raise ValueError(f"FACT {claim.claim_id} requires evidence_ids")
            forbidden = ("行业普遍认为", "市场预计", "据说", "可能")
            if any(token in claim.statement for token in forbidden):
                raise ValueError(f"FACT {claim.claim_id} contains unsupported attribution language")
        elif not claim.reasoning or not claim.reasoning.strip():
            raise ValueError(f"{claim.claim_type} {claim.claim_id} requires reasoning")
        unknown = set(claim.evidence_ids) - evidence_ids
        if unknown:
            raise ValueError(f"claim {claim.claim_id} references unknown evidence: {', '.join(sorted(unknown))}")
        if any(item.startswith("dividend_snapshot:") for item in claim.evidence_ids) and any(token in claim.statement.lower() for token in ("回购", "repurchase", "buyback")):
            raise ValueError(f"claim {claim.claim_id} cannot use dividend_snapshot evidence for a repurchase statement")
    return document
