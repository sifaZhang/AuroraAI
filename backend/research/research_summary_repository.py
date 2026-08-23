from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.dividend.position_levels import position_status

from .decision_schema import DecisionDocument
from .json_store import read_json
from .profile_builder import DEFAULT_RESEARCH_ROOT


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

    def _enabled_dividend_facts(self) -> dict[str, dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT u.symbol,u.company_name,u.entry_yield,u.add_yield,u.heavy_yield,
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
