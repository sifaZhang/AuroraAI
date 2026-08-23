from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fundamental_analyst import AnalystInputError, build_context, context_mode
from .json_store import read_json, write_json_atomic
from .llm.base import LlmProviderError, StructuredProvider
from .llm.factory import create_provider
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .skeptic_schema import validate_skeptic


PROMPT_VERSION = "skeptic-v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "skeptic_analyst.md"


def _directory(root: Path, symbol: str, version: str | None) -> tuple[str, Path]:
    analysis = root / symbol.upper() / "analysis"
    if version:
        folder = analysis / version
        required = ("input_snapshot.json", "evidence.json", "analyst.json")
        if not all((folder / name).exists() for name in required):
            raise AnalystInputError(f"analysis version is incomplete for skeptic: {version}")
        return version, folder
    choices = sorted((item for item in analysis.iterdir() if item.is_dir() and (item / "input_snapshot.json").exists()
                      and (item / "evidence.json").exists() and (item / "analyst.json").exists() and not (item / "skeptic.json").exists()), key=lambda item: item.name)
    if not choices:
        raise AnalystInputError("no analyst version without skeptic.json is available")
    return choices[-1].name, choices[-1]


class SkepticAnalyst:
    def __init__(self, *, provider: StructuredProvider | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT) -> None:
        self.provider, self.research_root = provider, research_root

    def prepare(self, symbol: str, version: str | None = None):
        actual_version, folder = _directory(self.research_root, symbol, version)
        snapshot, evidence, analyst = (read_json(folder / name) for name in ("input_snapshot.json", "evidence.json", "analyst.json"))
        company = read_json(folder.parents[1] / "company.json")
        if not company or snapshot.get("symbol") != symbol.upper() or evidence.get("symbol") != symbol.upper() or analyst.get("symbol") != symbol.upper():
            raise AnalystInputError("company or analysis inputs do not match requested symbol")
        claims = analyst.get("claims")
        if not isinstance(claims, list):
            raise AnalystInputError("analyst.json has no readable claims")
        preferred_evidence_ids = {
            str(evidence_id)
            for claim in claims
            for evidence_id in claim.get("evidence_ids", [])
            if isinstance(evidence_id, str)
        }
        context, evidence_ids = build_context(folder, snapshot, evidence, "skeptic", preferred_evidence_ids)
        return actual_version, folder, company, snapshot, analyst, context, evidence_ids, {str(item.get("claim_id")) for item in claims if item.get("claim_id")}

    def run(self, symbol: str, *, version: str | None = None, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        actual_version, folder, company, snapshot, analyst, context, evidence_ids, claim_ids = self.prepare(symbol, version)
        output = folder / "skeptic.json"
        if output.exists() and not force and not dry_run:
            raise FileExistsError(f"skeptic.json already exists for {actual_version}; use --force to replace it")
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        request = self._request(company, snapshot, analyst, actual_version, context)
        if dry_run:
            return {"symbol": symbol.upper(), "version": actual_version, "context_mode": context_mode(context), "context_characters": len(context), "evidence_ids": sorted(evidence_ids), "analyst_claim_ids": sorted(claim_ids), "dry_run": True}
        provider = self.provider or create_provider(role="skeptic")
        raw = self._generate(provider, prompt, request)
        try:
            document = self._validate(json.loads(raw.content), raw, symbol.upper(), actual_version, evidence_ids, claim_ids)
        except Exception as first_error:
            error_summary = str(first_error)[:1500]
            repaired = provider.generate_structured(system_prompt=prompt, user_prompt=request + f"\nYour JSON failed validation. Correct these errors exactly:\n{error_summary}\nReturn a complete corrected JSON only. challenged_claims must use analyst_claim_id, status, severity, reason, evidence_ids.")
            try:
                document = self._validate(json.loads(repaired.content), repaired, symbol.upper(), actual_version, evidence_ids, claim_ids)
            except Exception as repair_error:
                raise AnalystInputError(f"LLM output failed schema/semantic validation: {str(repair_error).splitlines()[0]}") from first_error
        document.generation.update(context_mode=context_mode(context), context_characters=len(context))
        write_json_atomic(output, document.model_dump(mode="json"))
        return {"symbol": symbol.upper(), "version": actual_version, "path": str(output), "challenged_claims": len(document.challenged_claims), "major_risks": len(document.major_risks), "generation": document.generation}

    @staticmethod
    def _validate(payload: object, raw, symbol: str, version: str, evidence_ids: set[str], claim_ids: set[str]):
        if not isinstance(payload, dict):
            raise AnalystInputError("LLM JSON output must be an object")
        payload["generation"] = {"provider": raw.provider, "model": raw.model, "prompt_version": PROMPT_VERSION,
                                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "input_tokens": raw.input_tokens,
                                 "output_tokens": raw.output_tokens, "latency_ms": raw.latency_ms}
        return validate_skeptic(payload, symbol=symbol, analysis_version=version, evidence_ids=evidence_ids, analyst_claim_ids=claim_ids)

    @staticmethod
    def _generate(provider: StructuredProvider, prompt: str, request: str):
        try:
            result = provider.generate_structured(system_prompt=prompt, user_prompt=request)
            json.loads(result.content)
            return result
        except (LlmProviderError, json.JSONDecodeError) as first_error:
            try:
                return provider.generate_structured(system_prompt=prompt, user_prompt=request + "\nReturn complete valid JSON only.")
            except LlmProviderError:
                raise first_error

    @staticmethod
    def _request(company: dict[str, Any], snapshot: dict[str, Any], analyst: dict[str, Any], version: str, context: str) -> str:
        skeleton = {"schema_version": 1, "symbol": snapshot["symbol"], "analysis_version": version, "verdict": "CAUTION",
                    "overall_assessment": {"summary": "", "confidence": 0.0},
                    "challenged_claims": [{"analyst_claim_id": "C001", "status": "WEAKLY_SUPPORTED", "severity": "HIGH", "reason": "", "evidence_ids": ["financial_report:YYYY:annual_report"]}],
                    "major_risks": [{"risk_id": "R001", "category": "cash_flow", "severity": "HIGH", "statement": "", "evidence_ids": ["financial_report:YYYY:annual_report"], "reasoning": "", "invalidate_condition": ""}],
                    "dividend_trap_assessment": {"status": "UNKNOWN", "confidence": 0.0, "reasons": [], "supporting_evidence_ids": [], "missing_evidence": []},
                    "earnings_risk_assessment": {"summary": "", "confidence": 0.0, "challenged_assumptions": [], "evidence_ids": []},
                    "governance_risk_assessment": {"summary": "", "confidence": 0.0, "evidence_ids": [], "missing_evidence": []},
                    "missing_evidence": [{"item": "", "importance": "HIGH", "reason": ""}], "recommended_research_cap": "ENTRY"}
        return ("Create skeptic JSON matching this exact shape (generation is added locally; do not include it):\n"
                f"{json.dumps(skeleton, ensure_ascii=False)}\n\nCOMPANY PROFILE:\n{json.dumps(company, ensure_ascii=False)}\n\nINPUT SNAPSHOT:\n{json.dumps(snapshot, ensure_ascii=False)}\n\nFUNDAMENTAL ANALYST (challenge target, not evidence):\n{json.dumps(analyst, ensure_ascii=False)}\n\nSOURCE EVIDENCE:\n{context}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate evidence-bound skeptic risk analysis")
    parser.add_argument("--symbol", required=True); parser.add_argument("--version"); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--force", action="store_true")
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    try:
        print(SkepticAnalyst(research_root=args.research_root).run(args.symbol, version=args.version, force=args.force, dry_run=args.dry_run)); return 0
    except (AnalystInputError, LlmProviderError, FileExistsError, ValueError) as exc:
        print({"status": "failed", "error": str(exc)}); return 1


if __name__ == "__main__":
    raise SystemExit(main())
