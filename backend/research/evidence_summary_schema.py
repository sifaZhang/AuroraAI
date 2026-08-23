from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SECTIONS = (
    "financial_performance", "cash_flow", "balance_sheet", "dividend", "business",
    "governance", "material_events", "accounting", "risk_disclosures",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompressedFact(Strict):
    text: str = Field(min_length=1)
    source_anchor: str = Field(min_length=1)


class CompressedEvidence(Strict):
    evidence_id: str
    source_type: str
    period: str | None = None
    published_at: str | None = None
    section: str
    priority: int
    facts: list[CompressedFact]
    key_numbers: dict[str, float | str | None]


class EvidenceIndexItem(Strict):
    source_type: str
    period: str | None = None
    file: str | None = None
    sha256: str | None = None
    included_sections: list[str]
    included_fact_count: int = 0


class CompressionMetrics(Strict):
    method: Literal["deterministic-v1"]
    source_evidence_count: int = Field(ge=0)
    included_evidence_count: int = Field(ge=0)
    source_character_count: int = Field(ge=0)
    compressed_character_count: int = Field(ge=0)
    compression_ratio: float = Field(ge=0)
    estimated_tokens_before: int = Field(ge=0)
    estimated_tokens_after: int = Field(ge=0)
    max_characters: int = Field(gt=0)


class EvidenceSummary(Strict):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    generated_at: str
    compression: CompressionMetrics
    sections: dict[str, list[CompressedEvidence]]
    evidence_index: dict[str, EvidenceIndexItem]


def validate_evidence_summary(payload: object, *, evidence: dict, symbol: str,
                              version: str, max_characters: int) -> EvidenceSummary:
    summary = EvidenceSummary.model_validate(payload)
    if summary.symbol != symbol or summary.analysis_version != version:
        raise ValueError("evidence summary symbol or version mismatch")
    if set(summary.sections) != set(SECTIONS):
        raise ValueError("evidence summary sections mismatch")
    authority = {str(item.get("id")) for item in evidence.get("evidence", []) if item.get("id")}
    if set(summary.evidence_index) != authority:
        raise ValueError("evidence summary index must match authoritative evidence IDs")
    referenced = {item.evidence_id for values in summary.sections.values() for item in values}
    if not referenced <= authority:
        raise ValueError("compressed evidence references unknown evidence ID")
    for section, values in summary.sections.items():
        if any(item.section != section or not item.facts for item in values):
            raise ValueError("compressed item section or facts are invalid")
    measured = sum(len(fact.text) for values in summary.sections.values()
                   for item in values for fact in item.facts)
    if measured != summary.compression.compressed_character_count:
        raise ValueError("compressed character count mismatch")
    if measured > max_characters:
        raise ValueError("compressed facts exceed configured character budget")
    if summary.compression.included_evidence_count != len(referenced):
        raise ValueError("included evidence count mismatch")
    if summary.compression.source_evidence_count != len(authority):
        raise ValueError("source evidence count mismatch")
    return summary
