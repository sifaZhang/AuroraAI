from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyst_schema import validate_analyst
from .json_store import read_json, write_json_atomic
from .llm.base import LlmProviderError, StructuredProvider
from .llm.factory import create_provider
from .profile_builder import DEFAULT_RESEARCH_ROOT


PROMPT_VERSION = "fundamental-v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "fundamental_analyst.md"
# Keep the POC bounded: financial reports are included first, followed by
# relevant announcements until this total is reached.  This avoids sending an
# unbounded PDF corpus while preserving explicit source boundaries.
MAX_DOCUMENT_CHARS = 16_000
MAX_CONTEXT_CHARS = 32_000


class AnalystInputError(ValueError):
    pass


def _analysis_directory(root: Path, symbol: str, version: str | None) -> tuple[str, Path]:
    analysis = root / symbol.upper() / "analysis"
    if version:
        target = analysis / version
        if not (target / "input_snapshot.json").exists() or not (target / "evidence.json").exists():
            raise AnalystInputError(f"analysis version is incomplete: {version}")
        return version, target
    choices = sorted((item for item in analysis.iterdir() if item.is_dir() and (item / "input_snapshot.json").exists()
                      and (item / "evidence.json").exists() and not (item / "analyst.json").exists()), key=lambda item: item.name)
    if not choices:
        raise AnalystInputError("no analysis version without analyst.json is available")
    return choices[-1].name, choices[-1]


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise AnalystInputError("pypdf is required for research PDF extraction") from exc
    try:
        reader = PdfReader(str(path))
        pieces: list[str] = []
        total = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            fragment = f"[PAGE {number}]\n{text}\n"
            pieces.append(fragment)
            total += len(fragment)
            if total >= MAX_DOCUMENT_CHARS:
                pieces.append("[TRUNCATED: document extraction limit reached]\n")
                break
        return "".join(pieces)[:MAX_DOCUMENT_CHARS]
    except Exception as exc:
        raise AnalystInputError(f"cannot extract PDF {path.name}: {type(exc).__name__}") from exc


def build_context(directory: Path, snapshot: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, set[str]]:
    blocks: list[str] = []
    total = 0
    evidence_ids: set[str] = set()
    items = list(evidence.get("evidence", []))
    # Put the compact database snapshot first so yield and dividend facts remain
    # usable even when PDF text reaches the controlled context ceiling.
    items.sort(key=lambda item: 0 if item.get("type") == "database_snapshot" else 1)
    for item in items:
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str):
            continue
        file_name = item.get("file")
        if not file_name:
            if item.get("type") == "database_snapshot":
                block = f"[SOURCE: {evidence_id}]\n{json.dumps(snapshot, ensure_ascii=False, sort_keys=True)}\n[/SOURCE]\n"
                blocks.append(block); total += len(block); evidence_ids.add(evidence_id)
            continue
        path = (directory / file_name).resolve()
        if not path.exists() or path.suffix.lower() != ".pdf":
            continue
        text = _extract_pdf(path)
        if not text:
            continue
        remaining = MAX_CONTEXT_CHARS - total
        if remaining <= 0:
            break
        block = f"[SOURCE: {evidence_id}]\n{text[:remaining]}\n[/SOURCE]\n"
        blocks.append(block); total += len(block); evidence_ids.add(evidence_id)
    if not blocks:
        raise AnalystInputError("no readable evidence is available")
    return "\n".join(blocks), evidence_ids


class FundamentalAnalyst:
    def __init__(self, *, provider: StructuredProvider | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT) -> None:
        self.provider, self.research_root = provider, research_root

    def prepare(self, symbol: str, version: str | None = None) -> tuple[str, Path, dict[str, Any], dict[str, Any], str, set[str]]:
        actual_version, directory = _analysis_directory(self.research_root, symbol, version)
        snapshot, evidence = read_json(directory / "input_snapshot.json"), read_json(directory / "evidence.json")
        if snapshot.get("symbol") != symbol.upper() or evidence.get("symbol") != symbol.upper():
            raise AnalystInputError("input pack symbol does not match requested symbol")
        context, evidence_ids = build_context(directory, snapshot, evidence)
        return actual_version, directory, snapshot, evidence, context, evidence_ids

    def run(self, symbol: str, *, version: str | None = None, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        actual_version, directory, snapshot, _evidence, context, evidence_ids = self.prepare(symbol, version)
        output = directory / "analyst.json"
        if output.exists() and not force and not dry_run:
            raise FileExistsError(f"analyst.json already exists for {actual_version}; use --force to replace it")
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        company = read_json(directory.parents[1] / "company.json")
        request = self._user_prompt(snapshot, company, actual_version, context)
        if dry_run:
            return {"symbol": symbol.upper(), "version": actual_version, "context_characters": len(context), "evidence_ids": sorted(evidence_ids), "dry_run": True}
        provider = self.provider or create_provider()
        raw = self._generate_with_one_retry(provider, system_prompt, request)
        try:
            payload = json.loads(raw.content)
        except json.JSONDecodeError as exc:
            raise AnalystInputError("LLM returned invalid JSON after retry") from exc
        if not isinstance(payload, dict):
            raise AnalystInputError("LLM JSON output must be an object")
        try:
            document = self._validate_payload(payload, raw, symbol.upper(), actual_version, evidence_ids)
        except Exception as first_error:
            repaired = provider.generate_structured(system_prompt=system_prompt, user_prompt=request + f"\nYour JSON failed validation: {str(first_error).splitlines()[0]}. Return a complete corrected JSON only. Each claims item MUST use claim_id, statement, claim_type, confidence, evidence_ids, reasoning.")
            try:
                repaired_payload = json.loads(repaired.content)
                document = self._validate_payload(repaired_payload, repaired, symbol.upper(), actual_version, evidence_ids)
                raw = repaired
            except Exception as repair_error:
                raise AnalystInputError(f"LLM output failed schema/semantic validation: {str(repair_error).splitlines()[0]}") from first_error
        write_json_atomic(output, document.model_dump(mode="json"))
        return {"symbol": symbol.upper(), "version": actual_version, "path": str(output), "claims": len(document.claims), "generation": document.generation}

    @staticmethod
    def _validate_payload(payload: object, raw, symbol: str, version: str, evidence_ids: set[str]):
        if not isinstance(payload, dict):
            raise AnalystInputError("LLM JSON output must be an object")
        payload["generation"] = {"provider": raw.provider, "model": raw.model, "prompt_version": PROMPT_VERSION,
                                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "latency_ms": raw.latency_ms}
        return validate_analyst(payload, symbol=symbol, analysis_version=version, evidence_ids=evidence_ids)

    def _generate_with_one_retry(self, provider: StructuredProvider, system_prompt: str, request: str):
        try:
            result = provider.generate_structured(system_prompt=system_prompt, user_prompt=request)
            json.loads(result.content)
            return result
        except (LlmProviderError, json.JSONDecodeError) as first_error:
            repair = request + "\nYour prior response was invalid. Return the complete corrected JSON object only."
            try:
                return provider.generate_structured(system_prompt=system_prompt, user_prompt=repair)
            except LlmProviderError:
                raise first_error

    @staticmethod
    def _user_prompt(snapshot: dict[str, Any], company: dict[str, Any], version: str, context: str) -> str:
        skeleton = {"schema_version": 1, "symbol": snapshot["symbol"], "analysis_version": version,
                    "company_classification": {"primary_research_type": "", "secondary_research_type": None, "business_model_summary": ""},
                    "investment_thesis": {"summary": "", "positive_drivers": [], "market_concerns": []},
                    "quality_assessment": {"business_quality": {"score": None, "reason": ""}, "earnings_quality": {"score": None, "reason": ""}, "dividend_quality": {"score": None, "reason": ""}, "governance_quality": {"score": None, "reason": ""}},
                    "normalized_earnings": {"unit": "CNY_100M", "period": "FY2026E", "bear": None, "base": None, "bull": None, "method": "insufficient_evidence", "key_drivers": [], "limitations": []},
                    "valuation": {"primary_method": "insufficient_evidence", "secondary_methods": [], "bear_value_per_share": None, "base_value_per_share": None, "bull_value_per_share": None, "methodology": "", "limitations": []},
                    "claims": [{"claim_id": "C001", "statement": "", "claim_type": "FACT", "confidence": 1.0, "evidence_ids": ["financial_report:YYYY:annual_report"], "reasoning": None}],
                    "key_risks": [], "provisional_view": {"summary": "", "valuation_attractiveness": "uncertain", "confidence": 0.0}}
        return ("Create analyst JSON following this exact shape (generation is added locally; do not include it):\n"
                f"{json.dumps(skeleton, ensure_ascii=False)}\n\nCOMPANY PROFILE:\n{json.dumps(company, ensure_ascii=False)}\n\nINPUT SNAPSHOT:\n{json.dumps(snapshot, ensure_ascii=False)}\n\nEVIDENCE:\n{context}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an evidence-bound fundamental analyst JSON")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--version")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    try:
        print(FundamentalAnalyst(research_root=args.research_root).run(args.symbol, version=args.version, force=args.force, dry_run=args.dry_run))
        return 0
    except (AnalystInputError, LlmProviderError, FileExistsError, ValueError) as exc:
        print({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
