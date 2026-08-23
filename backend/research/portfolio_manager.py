from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.dividend.position_levels import position_status

from .decision_schema import calculate_decision_cap, validate_decision, valuation_status
from .fundamental_analyst import AnalystInputError
from .json_store import read_json, write_json_atomic
from .llm.base import LlmProviderError, StructuredProvider
from .llm.factory import create_provider
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .profile_repository import ProfileRepository

PROMPT_VERSION = "portfolio-manager-v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "portfolio_manager.md"


class PortfolioManager:
    def __init__(self, *, provider: StructuredProvider | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT,
                 repository: ProfileRepository | None = None) -> None:
        self.provider, self.research_root, self.repository = provider, research_root, repository

    def run(self, symbol: str, *, version: str | None = None, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        folder, actual = self._folder(symbol, version, allow_existing=force)
        output = folder / "decision.json"
        if output.exists() and not force and not dry_run:
            raise FileExistsError(f"decision.json already exists for {actual}; use --force to replace it")
        company = read_json(folder.parents[1] / "company.json")
        snapshot, analyst, skeptic, valuation = (read_json(folder / name) for name in ("input_snapshot.json", "analyst.json", "skeptic.json", "valuation.json"))
        if not company or any(not item for item in (snapshot, analyst, skeptic, valuation)):
            raise AnalystInputError("company, snapshot, analyst, skeptic, and valuation inputs are required")
        if any(item.get("symbol") != symbol.upper() for item in (snapshot, analyst, skeptic, valuation)):
            raise AnalystInputError("analysis inputs do not match requested symbol")
        inputs = self._inputs(snapshot, skeptic, valuation, symbol)
        cap = calculate_decision_cap(str(inputs["skeptic_cap"]), str(inputs["forecast_quality"]))
        status = valuation_status(inputs["latest_price"], valuation.get("fair_value_per_share", {}).get("base"), str(inputs["forecast_quality"]))
        if dry_run:
            return {"symbol": symbol.upper(), "version": actual, "decision_cap": cap, "valuation_status": status, "dry_run": True}
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        request = self._request(company, snapshot, analyst, skeptic, valuation, actual, inputs, cap, status)
        provider = self.provider or create_provider()
        raw = self._generate(provider, prompt, request)
        try:
            initial_payload = json.loads(raw.content)
            document = self._validate(initial_payload, raw, symbol.upper(), actual, inputs, valuation, skeptic)
        except Exception as first_error:
            repair = provider.generate_structured(system_prompt=prompt, user_prompt=request + f"\nRewrite only the offending text into research-decision language. Do not introduce new facts or change structured decision fields. Validation violation:\n{str(first_error)[:1500]}")
            try:
                repaired_payload = json.loads(repair.content)
                for key in ("research_decision", "decision_cap", "inputs_summary"):
                    if repaired_payload.get(key) != initial_payload.get(key):
                        raise ValueError(f"repair changed protected field: {key}")
                if repaired_payload.get("valuation_assessment", {}).get("current_price") != initial_payload.get("valuation_assessment", {}).get("current_price"):
                    raise ValueError("repair changed protected valuation current_price")
                document = self._validate(repaired_payload, repair, symbol.upper(), actual, inputs, valuation, skeptic)
            except Exception as repair_error:
                raise AnalystInputError(f"LLM output failed schema/semantic validation: {str(repair_error).splitlines()[0]}") from first_error
        write_json_atomic(output, document.model_dump(mode="json"))
        return {"symbol": symbol.upper(), "version": actual, "path": str(output), "research_decision": document.research_decision, "generation": document.generation}

    def _folder(self, symbol: str, version: str | None, *, allow_existing: bool = False) -> tuple[Path, str]:
        analysis = self.research_root / symbol.upper() / "analysis"
        required = ("input_snapshot.json", "analyst.json", "skeptic.json", "valuation.json")
        if version:
            folder = analysis / version
            if not folder.is_dir() or not all((folder / name).exists() for name in required):
                raise AnalystInputError(f"analysis version is incomplete for portfolio manager: {version}")
            return folder, version
        choices = sorted((x for x in analysis.iterdir() if x.is_dir() and all((x / name).exists() for name in required) and (allow_existing or not (x / "decision.json").exists())), key=lambda x: x.name)
        if not choices: raise AnalystInputError("no complete analysis version without decision.json is available")
        return choices[-1], choices[-1].name

    def _inputs(self, snapshot: dict[str, Any], skeptic: dict[str, Any], valuation: dict[str, Any], symbol: str) -> dict[str, object]:
        context = snapshot.get("yield_context")
        if not isinstance(context, dict): raise AnalystInputError("input_snapshot.json has no yield_context")
        needed = ("latest_price", "three_year_average_yield", "entry_yield", "add_yield", "heavy_yield")
        if any(key not in context for key in needed): raise AnalystInputError("input_snapshot.json has incomplete yield context")
        current = context["three_year_average_yield"]
        yield_pct = float(current) * 100 if current is not None else None
        signal = position_status(yield_pct, context["entry_yield"], context["add_yield"], context["heavy_yield"])
        grade = context.get("grade")
        if grade is None and self.repository is not None:
            facts = self.repository.profile_facts(symbol)
            grade = facts.get("grade") if facts else None
        normal = valuation.get("normalized_earnings", {})
        if not all(key in skeptic for key in ("verdict", "recommended_research_cap")) or not all(key in normal for key in ("confidence", "forecast_quality")):
            raise AnalystInputError("skeptic or valuation inputs are incomplete")
        return {"yield_signal": signal, "latest_price": context["latest_price"], "three_year_average_yield": current,
                "entry_yield": context["entry_yield"], "add_yield": context["add_yield"], "heavy_yield": context["heavy_yield"], "grade": grade,
                "skeptic_verdict": skeptic["verdict"], "skeptic_cap": skeptic["recommended_research_cap"],
                "valuation_confidence": valuation.get("confidence"), "forecast_quality": normal["forecast_quality"]}

    @staticmethod
    def _generate(provider: StructuredProvider, prompt: str, request: str):
        try:
            result = provider.generate_structured(system_prompt=prompt, user_prompt=request); json.loads(result.content); return result
        except (LlmProviderError, json.JSONDecodeError) as first_error:
            try: return provider.generate_structured(system_prompt=prompt, user_prompt=request + "\nReturn complete valid JSON only.")
            except LlmProviderError: raise first_error

    @staticmethod
    def _validate(payload: object, raw, symbol: str, version: str, inputs: dict[str, object], valuation: dict[str, Any], skeptic: dict[str, Any]):
        if not isinstance(payload, dict): raise AnalystInputError("LLM JSON output must be an object")
        payload["generation"] = {"provider": raw.provider, "model": raw.model, "prompt_version": PROMPT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "latency_ms": raw.latency_ms}
        return validate_decision(payload, symbol=symbol, version=version, inputs=inputs, valuation=valuation, skeptic=skeptic)

    @staticmethod
    def _request(company, snapshot, analyst, skeptic, valuation, version, inputs, cap, status) -> str:
        condition = {"condition": "Observable fundamental or valuation condition", "impact": "May change the research decision", "category": "earnings"}
        conflict = {"topic": "earnings recovery", "analyst_view": "", "skeptic_view": "", "portfolio_manager_resolution": ""}
        skeleton = {"schema_version": 1, "symbol": snapshot["symbol"], "analysis_version": version, "inputs_summary": inputs, "research_decision": "watch", "decision_confidence": .0, "decision_cap": cap, "exceptional_reason": None, "decision_rationale": {"summary": "", "supporting_points": [], "limiting_points": []}, "valuation_assessment": {"status": status, "current_price": inputs["latest_price"], "bear_value": valuation.get("fair_value_per_share", {}).get("bear"), "base_value": valuation.get("fair_value_per_share", {}).get("base"), "bull_value": valuation.get("fair_value_per_share", {}).get("bull"), "interpretation": ""}, "yield_assessment": {"signal": inputs["yield_signal"], "assessment": ""}, "risk_assessment": {"skeptic_verdict": inputs["skeptic_verdict"], "risk_level": "HIGH", "summary": ""}, "upgrade_conditions": [condition], "downgrade_conditions": [condition], "invalidation_conditions": [condition], "key_uncertainties": [], "conflicts": [conflict]}
        return "Return JSON matching this exact shape (generation is added locally):\n" + json.dumps(skeleton, ensure_ascii=False) + "\nCOMPANY:\n" + json.dumps(company, ensure_ascii=False) + "\nSNAPSHOT:\n" + json.dumps(snapshot, ensure_ascii=False) + "\nANALYST:\n" + json.dumps(analyst, ensure_ascii=False) + "\nSKEPTIC:\n" + json.dumps(skeptic, ensure_ascii=False) + "\nVALUATION:\n" + json.dumps(valuation, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate constrained portfolio research decision")
    parser.add_argument("--symbol", required=True); parser.add_argument("--version"); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--force", action="store_true"); parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    repository = ProfileRepository()
    try:
        print(PortfolioManager(research_root=args.research_root, repository=repository).run(args.symbol, version=args.version, force=args.force, dry_run=args.dry_run)); return 0
    except (AnalystInputError, LlmProviderError, FileExistsError, ValueError) as error:
        print({"status": "failed", "error": str(error)}); return 1
    finally: repository.close()


if __name__ == "__main__": raise SystemExit(main())

