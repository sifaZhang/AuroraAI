from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.dividend.position_levels import position_status

from .analyst_schema import AnalystDocument
from .decision_schema import DecisionDocument
from .evidence_summary_schema import validate_evidence_summary
from .json_store import read_json
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .research_run_schema import RunDocument
from .skeptic_schema import SkepticDocument
from .valuation_schema import ValuationDocument


class ResearchSummaryRepository:
    """Read compact, completed research summaries from filesystem artifacts."""

    def __init__(self, connection, *, research_root: Path = DEFAULT_RESEARCH_ROOT) -> None:
        self.connection = connection
        self.research_root = research_root

    def list_summaries(self) -> list[dict[str, Any]]:
        facts = self._enabled_dividend_facts()
        summaries = []
        for symbol, dividend in facts.items():
            summary = self._summary(symbol, dividend)
            if summary is not None:
                summaries.append(summary)
        priority = {"heavy": 0, "add": 1, "entry": 2, "watch": 3, "avoid": 4}
        return sorted(summaries, key=lambda item: (
            priority[item["research_decision"]], -item["decision_confidence"], item["symbol"]
        ))

    def get_detail(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        dividend = self._enabled_dividend_facts().get(symbol)
        if dividend is None:
            return None
        for folder in self._analysis_folders(symbol):
            try:
                company = read_json(self.research_root / symbol / "company.json")
                analyst = AnalystDocument.model_validate(read_json(folder / "analyst.json"))
                skeptic = SkepticDocument.model_validate(read_json(folder / "skeptic.json"))
                valuation = ValuationDocument.model_validate(read_json(folder / "valuation.json"))
                decision = DecisionDocument.model_validate(read_json(folder / "decision.json"))
                documents = (analyst, skeptic, valuation, decision)
                if company.get("symbol") != symbol or any(
                    item.symbol != symbol or item.analysis_version != folder.name for item in documents
                ):
                    continue
            except (OSError, ValueError, ValidationError):
                continue
            return self._detail(folder, dividend, company, analyst, skeptic, valuation, decision)
        return None

    def _enabled_dividend_facts(self) -> dict[str, dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT u.symbol,u.company_name,u.grade,u.entry_yield,u.add_yield,u.heavy_yield,
                      s.latest_price,s.three_year_average_yield,s.calculation_date
                 FROM dividend_stable_universe u
                 LEFT JOIN dividend_yield_snapshots s ON s.market=u.market AND s.symbol=u.symbol
                  AND s.calculation_date=(SELECT MAX(x.calculation_date)
                    FROM dividend_yield_snapshots x WHERE x.market=u.market AND x.symbol=u.symbol)
                WHERE u.is_enabled=1"""
        )
        return {row["symbol"]: dict(row) for row in rows}

    def _summary(self, symbol: str, dividend: dict[str, Any]) -> dict[str, Any] | None:
        folder = None
        decision = None
        for candidate in self._analysis_folders(symbol):
            try:
                document = DecisionDocument.model_validate(read_json(candidate / "decision.json"))
            except (OSError, ValueError, ValidationError):
                continue
            if document.symbol == symbol and document.analysis_version == candidate.name:
                folder, decision = candidate, document
                break
        if folder is None or decision is None:
            return None
        try:
            research_date = date.fromisoformat(folder.name[:10]).isoformat()
        except ValueError:
            return None
        raw_yield = dividend.get("three_year_average_yield")
        yield_pct = float(raw_yield) * 100 if raw_yield is not None else None
        generated_at = str(decision.generation.get("generated_at") or "") or None
        return {
            "symbol": symbol,
            "company_name": dividend.get("company_name"),
            "yield_signal": position_status(yield_pct, dividend.get("entry_yield"),
                                            dividend.get("add_yield"), dividend.get("heavy_yield")),
            "research_decision": decision.research_decision,
            "decision_confidence": decision.decision_confidence,
            "decision_cap": decision.decision_cap,
            "skeptic_verdict": decision.risk_assessment.skeptic_verdict,
            "valuation_status": decision.valuation_assessment.status,
            "forecast_quality": decision.inputs_summary.forecast_quality,
            "latest_price": dividend.get("latest_price"),
            "analysis_version": folder.name,
            "research_date": research_date,
            "updated_at": generated_at,
        }

    def _detail(self, folder: Path, dividend: dict[str, Any], company: dict[str, Any],
                analyst: AnalystDocument, skeptic: SkepticDocument,
                valuation: ValuationDocument, decision: DecisionDocument) -> dict[str, Any]:
        evidence = read_json(folder / "evidence.json")
        evidence_items = evidence.get("evidence", []) if isinstance(evidence.get("evidence"), list) else []
        summary_available = False
        try:
            summary = read_json(folder / "evidence_summary.json")
            if summary:
                validate_evidence_summary(
                    summary, evidence=evidence, symbol=decision.symbol, version=folder.name,
                    max_characters=int(summary.get("compression", {}).get("max_characters", 0)),
                )
                summary_available = True
        except (OSError, ValueError, ValidationError):
            summary_available = False
        raw_yield = dividend.get("three_year_average_yield")
        yield_pct = float(raw_yield) * 100 if raw_yield is not None else None
        try:
            run_payload = read_json(folder / "run.json")
            run = RunDocument.model_validate(run_payload).model_dump(mode="json") if run_payload else None
        except (OSError, ValueError, ValidationError):
            run = None
        research_type = company.get("research_type") if isinstance(company.get("research_type"), dict) else {}
        generations = {
            "analyst": analyst.generation,
            "skeptic": skeptic.generation,
            "valuation": valuation.generation,
            "portfolio": decision.generation,
        }
        return {
            "symbol": decision.symbol,
            "company_name": dividend.get("company_name") or company.get("name"),
            "research_type": {
                "primary": research_type.get("primary"),
                "secondary": research_type.get("secondary"),
            },
            "analysis_version": folder.name,
            "research_date": folder.name[:10],
            "market": {
                "latest_price": dividend.get("latest_price"),
                "yield_signal": position_status(yield_pct, dividend.get("entry_yield"),
                                                dividend.get("add_yield"), dividend.get("heavy_yield")),
                "three_year_average_yield": raw_yield,
                "grade": dividend.get("grade"),
            },
            "decision": decision.model_dump(mode="json"),
            "valuation": valuation.model_dump(mode="json"),
            "skeptic": skeptic.model_dump(mode="json"),
            "analyst": analyst.model_dump(mode="json"),
            "research_conditions": {
                "upgrade": [item.model_dump(mode="json") for item in decision.upgrade_conditions],
                "downgrade": [item.model_dump(mode="json") for item in decision.downgrade_conditions],
                "invalidation": [item.model_dump(mode="json") for item in decision.invalidation_conditions],
            },
            "run": run,
            "evidence": {
                "summary_available": summary_available,
                "source_count": len(evidence_items),
                "source_ids": [str(item.get("id")) for item in evidence_items if item.get("id")],
            },
            "technical": {name: dict(metadata) for name, metadata in generations.items()},
            "history": {"completed_versions": len(self._analysis_folders(decision.symbol))},
        }

    def _analysis_folders(self, symbol: str) -> list[Path]:
        analysis = self.research_root / symbol / "analysis"
        if not analysis.is_dir():
            return []
        try:
            current = read_json(self.research_root / symbol / "current.json")
        except (OSError, ValueError):
            current = {}
        preferred = current.get("analysis_version")
        candidates = []
        if preferred:
            candidates.append(analysis / str(preferred))
        candidates.extend(path for path in sorted(analysis.iterdir(), key=lambda item: item.name, reverse=True)
                          if path.is_dir() and path.name != preferred)
        valid = []
        for folder in candidates:
            if not (folder / "decision.json").is_file():
                continue
            try:
                run = read_json(folder / "run.json")
            except (OSError, ValueError):
                continue
            if run and run.get("status") != "COMPLETED":
                continue
            valid.append(folder)
        return valid
