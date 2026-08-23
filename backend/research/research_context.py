from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .evidence_summary_schema import validate_evidence_summary
from .json_store import read_json


ROLE_SECTIONS = {
    "analyst": ("accounting", "financial_performance", "cash_flow", "dividend", "governance", "material_events", "business", "balance_sheet", "risk_disclosures"),
    "skeptic": ("accounting", "cash_flow", "risk_disclosures", "balance_sheet", "dividend", "governance", "material_events", "financial_performance"),
    "valuation": ("financial_performance", "cash_flow", "dividend"),
}
ROLE_DEFAULT_BUDGETS = {"analyst": 30_000, "skeptic": 24_000, "valuation": 16_000}
ROLE_ENV = {"analyst": "RESEARCH_ANALYST_CONTEXT_MAX_CHARS", "skeptic": "RESEARCH_SKEPTIC_CONTEXT_MAX_CHARS",
            "valuation": "RESEARCH_VALUATION_CONTEXT_MAX_CHARS"}


def role_budget(role: str) -> int:
    if role not in ROLE_SECTIONS:
        raise ValueError(f"unknown research context role: {role}")
    value = int(os.environ.get(ROLE_ENV[role], ROLE_DEFAULT_BUDGETS[role]))
    if value <= 0: raise ValueError("research context budget must be positive")
    return value


def build_compressed_context(
    directory: Path,
    evidence: dict[str, Any],
    *,
    role: str,
    preferred_evidence_ids: set[str] | None = None,
) -> tuple[str, set[str]]:
    budget = role_budget(role)
    payload = read_json(directory / "evidence_summary.json")
    document = validate_evidence_summary(payload, evidence=evidence, symbol=str(evidence.get("symbol")),
                                         version=directory.name,
                                         max_characters=int(payload.get("compression", {}).get("max_characters", 0)))
    preferred = preferred_evidence_ids or set()
    candidates: list[tuple[int, int, int, str, str]] = []
    for section_rank, section in enumerate(ROLE_SECTIONS[role]):
        for item in document.sections[section]:
            for fact in item.facts:
                block = f"[SOURCE: {item.evidence_id}] [SECTION: {section}] [ANCHOR: {fact.source_anchor}]\n{fact.text}\n[/SOURCE]"
                candidates.append((item.evidence_id not in preferred, -item.priority, section_rank, item.evidence_id, block))
    candidates.sort()

    blocks = ["[CONTEXT_MODE: compressed]"]
    evidence_ids: set[str] = set()
    used = len(blocks[0])
    for _, _, _, evidence_id, block in candidates:
        addition = len(block) + 1
        if used + addition > budget:
            continue
        blocks.append(block); used += addition; evidence_ids.add(evidence_id)
    if not evidence_ids:
        raise ValueError("compressed evidence has no facts for role")
    return "\n".join(blocks), evidence_ids
