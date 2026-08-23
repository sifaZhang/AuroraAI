from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "VERY_LOW", "INSUFFICIENT"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfidenceComponent(Strict):
    score: int = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    reasons: list[str] = Field(min_length=1)


class ConfidenceComponents(Strict):
    evidence_completeness: ConfidenceComponent
    earnings_visibility: ConfidenceComponent
    valuation_reliability: ConfidenceComponent
    risk_resolution: ConfidenceComponent
    agent_consistency: ConfidenceComponent


class ConfidenceCalibration(Strict):
    schema_version: Literal[1]
    symbol: str = Field(min_length=1)
    analysis_version: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)
    level: ConfidenceLevel
    components: ConfidenceComponents
    strengths: list[str]
    blockers: list[str]
    model_reported_confidence: float = Field(ge=0, le=1)
    generated_at: str = Field(min_length=1)
    method: Literal["deterministic-v1"]

    @model_validator(mode="after")
    def validate_calculation(self) -> "ConfidenceCalibration":
        values = list(self.components.model_dump().values())
        if abs(sum(item["weight"] for item in values) - 1.0) > 1e-9:
            raise ValueError("confidence component weights must sum to 1")
        expected = round(sum(item["score"] * item["weight"] for item in values))
        if self.score != expected:
            raise ValueError("confidence score does not match weighted components")
        if self.level != confidence_level(self.score):
            raise ValueError("confidence level does not match score")
        return self


def confidence_level(score: int) -> ConfidenceLevel:
    if score == 0:
        return "INSUFFICIENT"
    if score >= 75:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    if score >= 35:
        return "LOW"
    return "VERY_LOW"
