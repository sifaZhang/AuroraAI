from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChallengedClaim(StrictModel):
    analyst_claim_id: str = Field(min_length=1)
    status: Literal["SUPPORTED", "WEAKLY_SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class MajorRisk(StrictModel):
    risk_id: str = Field(min_length=1)
    category: Literal["dividend_sustainability", "earnings_quality", "cash_flow", "governance", "industry", "valuation", "balance_sheet", "capital_allocation", "accounting", "execution", "other"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)
    invalidate_condition: str = Field(min_length=1)


class DividendTrapAssessment(StrictModel):
    status: Literal["UNLIKELY", "POSSIBLE", "LIKELY", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    supporting_evidence_ids: list[str]
    missing_evidence: list[str]


class EarningsRiskAssessment(StrictModel):
    summary: str
    confidence: float = Field(ge=0, le=1)
    challenged_assumptions: list[str]
    evidence_ids: list[str]


class GovernanceRiskAssessment(StrictModel):
    summary: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    missing_evidence: list[str]


class MissingEvidence(StrictModel):
    item: str = Field(min_length=1)
    importance: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason: str = Field(min_length=1)


class OverallAssessment(StrictModel):
    summary: str
    confidence: float = Field(ge=0, le=1)


class SkepticDocument(StrictModel):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    generation: dict[str, object]
    verdict: Literal["PASS", "CAUTION", "BLOCK"]
    overall_assessment: OverallAssessment
    challenged_claims: list[ChallengedClaim]
    major_risks: list[MajorRisk]
    dividend_trap_assessment: DividendTrapAssessment
    earnings_risk_assessment: EarningsRiskAssessment
    governance_risk_assessment: GovernanceRiskAssessment
    missing_evidence: list[MissingEvidence]
    recommended_research_cap: Literal["WATCH", "ENTRY", "ADD", "HEAVY"]

    @model_validator(mode="after")
    def cap_matches_verdict(self) -> "SkepticDocument":
        if self.verdict == "BLOCK" and self.recommended_research_cap != "WATCH":
            raise ValueError("BLOCK verdict requires WATCH research cap")
        if self.verdict == "CAUTION" and self.recommended_research_cap == "HEAVY":
            raise ValueError("CAUTION verdict cannot have HEAVY research cap")
        return self


def validate_skeptic(payload: object, *, symbol: str, analysis_version: str, evidence_ids: set[str], analyst_claim_ids: set[str]) -> SkepticDocument:
    document = SkepticDocument.model_validate(payload)
    if document.symbol != symbol:
        raise ValueError("skeptic symbol does not match input pack")
    if document.analysis_version != analysis_version:
        raise ValueError("skeptic analysis_version does not match input pack")
    for challenged in document.challenged_claims:
        if challenged.analyst_claim_id not in analyst_claim_ids:
            raise ValueError(f"challenged claim does not exist: {challenged.analyst_claim_id}")
        unknown = set(challenged.evidence_ids) - evidence_ids
        if unknown:
            raise ValueError(f"challenged claim {challenged.analyst_claim_id} references unknown evidence: {', '.join(sorted(unknown))}")
    for risk in document.major_risks:
        unknown = set(risk.evidence_ids) - evidence_ids
        if unknown:
            raise ValueError(f"risk {risk.risk_id} references unknown evidence: {', '.join(sorted(unknown))}")
        if not risk.evidence_ids and not risk.reasoning:
            raise ValueError(f"risk {risk.risk_id} needs evidence or reasoning")
    for values in (document.dividend_trap_assessment.supporting_evidence_ids, document.earnings_risk_assessment.evidence_ids, document.governance_risk_assessment.evidence_ids):
        unknown = set(values) - evidence_ids
        if unknown:
            raise ValueError(f"risk assessment references unknown evidence: {', '.join(sorted(unknown))}")
    return document
