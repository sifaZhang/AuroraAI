from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .analyst_schema import AnalystDocument
from .confidence_calibration_schema import (
    ConfidenceCalibration, ConfidenceComponent, ConfidenceComponents, confidence_level,
)
from .decision_schema import DecisionDocument, ORDER
from .forward_consensus_collector import compare_ai_to_consensus
from .json_store import read_json, write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .skeptic_schema import SkepticDocument
from .valuation_schema import ValuationDocument


WEIGHTS = {
    "evidence_completeness": .25,
    "earnings_visibility": .25,
    "valuation_reliability": .25,
    "risk_resolution": .15,
    "agent_consistency": .10,
}
FORECAST_BASE = {"HIGH": 90, "MEDIUM": 70, "LOW": 40, "INSUFFICIENT": 10}
VERDICT_BASE = {"PASS": 85, "CAUTION": 55, "BLOCK": 20}
RISK_PENALTY = {"LOW": 0, "MEDIUM": 3, "HIGH": 10, "CRITICAL": 20}
TRAP_ADJUSTMENT = {"UNLIKELY": 5, "UNKNOWN": 0, "POSSIBLE": -10, "LIKELY": -25}


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


class ConfidenceCalibrator:
    def __init__(self, *, research_root: Path = DEFAULT_RESEARCH_ROOT,
                 now: Callable[[], datetime] | None = None) -> None:
        self.research_root = research_root
        self.now = now or (lambda: datetime.now(timezone.utc))

    def calculate(self, symbol: str, version: str) -> ConfidenceCalibration:
        symbol = symbol.upper()
        folder = self.research_root / symbol / "analysis" / version
        evidence = read_json(folder / "evidence.json")
        summary = read_json(folder / "evidence_summary.json") if (folder / "evidence_summary.json").is_file() else None
        analyst = AnalystDocument.model_validate(read_json(folder / "analyst.json"))
        skeptic = SkepticDocument.model_validate(read_json(folder / "skeptic.json"))
        valuation = ValuationDocument.model_validate(read_json(folder / "valuation.json"))
        decision = DecisionDocument.model_validate(read_json(folder / "decision.json"))
        consensus = read_json(folder / "forward_consensus.json") if (folder / "forward_consensus.json").is_file() else None
        documents = (analyst, skeptic, valuation, decision)
        if any(item.symbol != symbol or item.analysis_version != version for item in documents):
            raise ValueError("confidence inputs do not match symbol and analysis version")
        authority = {str(item.get("id")) for item in evidence.get("evidence", []) if item.get("id")}
        components = ConfidenceComponents(
            evidence_completeness=self._evidence(evidence, summary, analyst, skeptic, valuation, authority),
            earnings_visibility=self._earnings(analyst, valuation, consensus),
            valuation_reliability=self._valuation(valuation, decision, consensus),
            risk_resolution=self._risk(skeptic),
            agent_consistency=self._consistency(analyst, skeptic, decision),
        )
        fair = valuation.fair_value_per_share
        fatal = (valuation.normalized_earnings.forecast_quality == "INSUFFICIENT"
                 and decision.valuation_assessment.status == "UNCERTAIN"
                 and all(value is None for value in (fair.bear, fair.base, fair.bull)))
        if fatal:
            components = ConfidenceComponents(**{
                name: ConfidenceComponent(score=0, weight=weight,
                    reasons=["Fatal insufficiency: valuation is uncertain and no usable valuation scenario exists."])
                for name, weight in WEIGHTS.items()
            })
        values = components.model_dump()
        score = round(sum(values[name]["score"] * WEIGHTS[name] for name in WEIGHTS))
        ranked = sorted(values.items(), key=lambda item: item[1]["score"])
        def blocker_reason(value: dict[str, Any]) -> str:
            markers = ("(-", "caps", "missing", "unavailable", "unresolved", "insufficient", "not fully")
            return next((reason for reason in value["reasons"] if any(marker in reason.lower() for marker in markers)),
                        value["reasons"][-1])
        blockers = [f"{name.replace('_', ' ').title()}: {blocker_reason(value)}" for name, value in ranked[:2]]
        strengths = [f"{name.replace('_', ' ').title()}: {value['reasons'][0]}" for name, value in reversed(ranked[-2:])]
        return ConfidenceCalibration(
            schema_version=1, symbol=symbol, analysis_version=version, score=score,
            level=confidence_level(score), components=components, strengths=strengths, blockers=blockers,
            model_reported_confidence=decision.decision_confidence,
            generated_at=self.now().isoformat(timespec="seconds"), method="deterministic-v1",
        )

    def run(self, symbol: str, version: str) -> ConfidenceCalibration:
        result = self.calculate(symbol, version)
        folder = self.research_root / result.symbol / "analysis" / result.analysis_version
        write_json_atomic(folder / "confidence_calibration.json", result.model_dump(mode="json"))
        return result

    @staticmethod
    def _evidence(evidence: dict[str, Any], summary: dict[str, Any] | None,
                  analyst: AnalystDocument, skeptic: SkepticDocument, valuation: ValuationDocument,
                  authority: set[str]) -> ConfidenceComponent:
        score, reasons = 100, []
        items = evidence.get("evidence", [])
        financial = [item for item in items if item.get("type") == "financial_report"]
        periods = [str(item.get("period") or "").lower() for item in financial]
        if not financial:
            score -= 30; reasons.append("Latest financial report is missing (-30).")
        else:
            reasons.append("Latest financial report evidence is available.")
        if not any("annual" in str(item.get("id", "")).lower() or period.isdigit() for item, period in zip(financial, periods)):
            score -= 20; reasons.append("Annual report evidence is missing (-20).")
        else:
            reasons.append("Recent annual report evidence is available.")
        sections = summary.get("sections", {}) if isinstance(summary, dict) else {}
        for section, penalty, label in (("cash_flow", 15, "Cash-flow"), ("dividend", 10, "Dividend"),
                                        ("balance_sheet", 10, "Balance-sheet")):
            if not sections.get(section):
                score -= penalty; reasons.append(f"{label} evidence is missing (-{penalty}).")
            else:
                reasons.append(f"{label} evidence is available.")
        if not sections.get("governance") and not sections.get("material_events"):
            score -= 5; reasons.append("Governance and material-event evidence are missing (-5).")
        else:
            reasons.append("Governance or material-event evidence is available.")
        if summary is None:
            score -= 5; reasons.append("Compressed evidence summary is unavailable; raw evidence remains usable (-5).")
        referenced = {evidence_id for claim in analyst.claims for evidence_id in claim.evidence_ids}
        referenced.update(evidence_id for risk in skeptic.major_risks for evidence_id in risk.evidence_ids)
        referenced.update(valuation.normalized_earnings.evidence_ids)
        unresolved = sorted(referenced - authority)
        if unresolved:
            penalty = min(40, len(unresolved) * 20)
            score -= penalty; reasons.append(f"{len(unresolved)} referenced evidence IDs are unresolved (-{penalty}).")
        else:
            reasons.append("All key referenced evidence IDs resolve to the evidence index.")
        return ConfidenceComponent(score=_clamp(score), weight=WEIGHTS["evidence_completeness"], reasons=reasons)

    @staticmethod
    def _earnings(analyst: AnalystDocument, valuation: ValuationDocument,
                  consensus: dict[str, Any] | None = None) -> ConfidenceComponent:
        normalized = valuation.normalized_earnings
        score = FORECAST_BASE[normalized.forecast_quality]
        reasons = [f"Forecast quality {normalized.forecast_quality} maps to {score}."]
        values = (normalized.bear, normalized.base, normalized.bull)
        if all(value is not None for value in values) and normalized.base:
            spread = (normalized.bull - normalized.bear) / abs(normalized.base)
            if spread <= .25:
                score += 5; reasons.append("Normalized-earnings scenario spread is at most 25% (+5).")
            elif spread <= .50:
                reasons.append("Normalized-earnings scenario spread is between 25% and 50% (no adjustment).")
            elif spread <= 1:
                score -= 10; reasons.append("Normalized-earnings scenario spread is between 50% and 100% (-10).")
            else:
                score -= 20; reasons.append("Normalized-earnings scenario spread exceeds 100% (-20).")
        else:
            reasons.append("A complete normalized-earnings scenario spread is unavailable (no adjustment).")
        if analyst.normalized_earnings.method == "insufficient_evidence":
            score -= 5; reasons.append("Analyst normalized earnings are insufficient (-5).")
        if consensus and consensus.get("status") == "AVAILABLE":
            years = consensus.get("forecast_years", {})
            match = __import__("re").fullmatch(r"FY(\d{4})E", str(normalized.period or ""))
            key = match.group(1) if match and match.group(1) in years else min(years, default=None)
            if key:
                fy1 = years[key]; quality = fy1.get("quality", "INSUFFICIENT")
                adjustment = {"HIGH": 20, "MEDIUM": 15, "LOW": 5, "INSUFFICIENT": 0}[quality]
                score += adjustment; reasons.append(f"FY{key} consensus quality {quality} adjusts earnings visibility by +{adjustment}.")
                next_year = years.get(str(int(key) + 1), {})
                if next_year.get("quality") in {"HIGH", "MEDIUM"}:
                    score += 10; reasons.append("FY2 consensus has at least medium quality (+10).")
                if fy1.get("revision", {}).get("supported"):
                    score += 5; reasons.append("FY1 exact-institution revision evidence is supported (+5).")
            agreement = compare_ai_to_consensus(valuation.model_dump(mode="json"), consensus)["agreement"]
            delta = {"ALIGNED": 5, "MODERATE_GAP": -5, "LARGE_GAP": -15, "NOT_COMPARABLE": 0}[agreement]
            score += delta; reasons.append(f"AI-to-consensus comparison is {agreement} ({delta:+d}).")
        else:
            reasons.append("Usable forward consensus is unavailable (no adjustment).")
        cap = {"LOW": 75, "INSUFFICIENT": 50}.get(normalized.forecast_quality, 100)
        if score > cap:
            score = cap; reasons.append(f"Forecast quality {normalized.forecast_quality} caps earnings visibility at {cap}.")
        return ConfidenceComponent(score=_clamp(score), weight=WEIGHTS["earnings_visibility"], reasons=reasons)

    @staticmethod
    def _valuation(valuation: ValuationDocument, decision: DecisionDocument,
                   consensus: dict[str, Any] | None = None) -> ConfidenceComponent:
        score = valuation.confidence * 100
        reasons = [f"Valuation-agent confidence supplies the {round(score)}-point base."]
        fair = valuation.fair_value_per_share
        if all(value is not None for value in (fair.bear, fair.base, fair.bull)):
            score += 10; reasons.append("Bear/base/bull fair values are complete (+10).")
        normalized = valuation.normalized_earnings
        if all(value is not None for value in (normalized.bear, normalized.base, normalized.bull)):
            score += 10; reasons.append("Normalized-earnings scenarios are complete (+10).")
        multiples = valuation.multiples
        if all(value is not None for value in (multiples.bear, multiples.base, multiples.bull)):
            score += 5; reasons.append("Valuation multiple scenarios are complete (+5).")
        if valuation.reverse_valuation.scenarios:
            score += 5; reasons.append("Reverse-valuation scenarios are available (+5).")
        if decision.valuation_assessment.status == "UNCERTAIN" and normalized.forecast_quality == "INSUFFICIENT":
            score = 0; reasons.append("Valuation is uncertain with insufficient forecasts (score capped at 0).")
        cap = {"LOW": 55, "MEDIUM": 80, "HIGH": 100, "INSUFFICIENT": 30}[normalized.forecast_quality]
        if score > cap:
            score = cap; reasons.append(f"Forecast quality {normalized.forecast_quality} caps valuation reliability at {cap}.")
        comparison = compare_ai_to_consensus(valuation.model_dump(mode="json"), consensus)
        year = str(comparison.get("fiscal_year") or "")
        quality = (consensus or {}).get("forecast_years", {}).get(year, {}).get("quality")
        if quality in {"HIGH", "MEDIUM"} and comparison["agreement"] in {"ALIGNED", "MODERATE_GAP"}:
            score += 5; reasons.append("Reliable FY1 consensus overlaps the AI earnings range (+5).")
        else:
            reasons.append("Consensus does not provide a reliable overlapping FY1 cross-check (no adjustment).")
        return ConfidenceComponent(score=_clamp(score), weight=WEIGHTS["valuation_reliability"], reasons=reasons)

    @staticmethod
    def _risk(skeptic: SkepticDocument) -> ConfidenceComponent:
        score = VERDICT_BASE[skeptic.verdict]
        reasons = [f"Skeptic verdict {skeptic.verdict} maps to {score}."]
        for risk in skeptic.major_risks:
            penalty = RISK_PENALTY[risk.severity]
            score -= penalty
            if penalty: reasons.append(f"Unresolved {risk.severity} risk {risk.risk_id} (-{penalty}).")
        trap = skeptic.dividend_trap_assessment.status
        adjustment = TRAP_ADJUSTMENT[trap]
        score += adjustment
        reasons.append(f"Dividend-trap assessment {trap} adjusts the score by {adjustment:+d}.")
        challenge_penalty = 0
        for claim in skeptic.challenged_claims:
            if claim.status in {"UNSUPPORTED", "CONTRADICTED"}:
                challenge_penalty += {"LOW": 1, "MEDIUM": 2, "HIGH": 5, "CRITICAL": 10}[claim.severity]
        if challenge_penalty:
            score -= challenge_penalty; reasons.append(f"Unsupported or contradicted challenged claims (-{challenge_penalty}).")
        else:
            reasons.append("No challenged claims are unsupported or contradicted.")
        return ConfidenceComponent(score=_clamp(score), weight=WEIGHTS["risk_resolution"], reasons=reasons)

    @staticmethod
    def _consistency(analyst: AnalystDocument, skeptic: SkepticDocument,
                     decision: DecisionDocument) -> ConfidenceComponent:
        score, reasons = 50, ["Structured agent consistency starts at 50."]
        if ORDER[decision.research_decision] <= ORDER[decision.decision_cap]:
            score += 10; reasons.append("Portfolio decision complies with the decision cap (+10).")
        if skeptic.verdict == "BLOCK" and decision.research_decision not in {"avoid", "watch"}:
            raise ValueError("BLOCK skeptic verdict conflicts with portfolio decision")
        conservative = decision.research_decision in {"avoid", "watch"}
        expensive = decision.valuation_assessment.status in {"FAIR_TO_EXPENSIVE", "EXPENSIVE", "EXTREMELY_EXPENSIVE", "UNCERTAIN"}
        attractive = decision.valuation_assessment.status in {"DEEPLY_UNDERVALUED", "UNDERVALUED"}
        if (expensive and conservative) or (attractive and not conservative):
            score += 10; reasons.append("Portfolio direction is consistent with valuation status (+10).")
        else:
            reasons.append("Portfolio direction is not fully aligned with valuation status (no adjustment).")
        view = analyst.provisional_view.valuation_attractiveness
        if view == "uncertain" and decision.research_decision == "watch":
            score += 5; reasons.append("Uncertain analyst view and watch decision are consistent (+5).")
        positive = view in {"very_attractive", "attractive"}
        limited = skeptic.verdict in {"CAUTION", "BLOCK"} or expensive
        if positive and conservative and not limited:
            score -= 20; reasons.append("Positive analyst view lacks a structured reason for a conservative decision (-20).")
        elif positive and conservative:
            reasons.append("Skeptic or valuation constraints explain the conservative decision.")
        return ConfidenceComponent(score=_clamp(score), weight=WEIGHTS["agent_consistency"], reasons=reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic research confidence calibration")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    try:
        result = ConfidenceCalibrator(research_root=args.research_root).run(args.symbol, args.version)
        print(result.model_dump_json(indent=2))
        return 0
    except (OSError, ValueError, ValidationError) as exc:
        print({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
