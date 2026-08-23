from __future__ import annotations

import argparse
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .fundamental_analyst import FundamentalAnalyst
from .evidence_compressor import EvidenceCompressor
from .input_pack_builder import InputPackBuilder
from .json_store import read_json, write_json_atomic
from .portfolio_manager import PortfolioManager
from .profile_builder import DEFAULT_RESEARCH_ROOT, ProfileBuilder
from .profile_repository import ProfileRepository
from .research_plan import (ExecutionPlan, OrchestratorConfig, ResearchPlan, ResearchState,
                            build_research_plan, enforce_llm_budget)
from .research_run_schema import RunDocument
from .skeptic_analyst import SkepticAnalyst
from .source_collector import CninfoClient, discover_documents, save_document
from .source_metadata import SourceDocument
from .source_repository import SourceRepository
from .valuation_agent import ValuationAgent
from .valuation_input_builder import ValuationInputBuilder


STEP_PLANS = {
    ExecutionPlan.FULL_REFRESH: {"source_collection": "RUN", "input_pack": "REFRESHED", "evidence_compression": "RUN", "analyst": "RUN", "skeptic": "RUN", "valuation_input": "REFRESHED", "valuation": "RUN", "portfolio_manager": "RUN"},
    ExecutionPlan.VALUATION_REFRESH: {"source_collection": "SKIPPED", "input_pack": "REFRESHED", "evidence_compression": "REUSED", "analyst": "REUSED", "skeptic": "REUSED", "valuation_input": "REFRESHED", "valuation": "RUN", "portfolio_manager": "RUN"},
    ExecutionPlan.DECISION_ONLY: {"source_collection": "SKIPPED", "input_pack": "REFRESHED", "evidence_compression": "REUSED", "analyst": "REUSED", "skeptic": "REUSED", "valuation_input": "REUSED", "valuation": "REUSED", "portfolio_manager": "RUN"},
    ExecutionPlan.NO_ACTION: {"source_collection": "SKIPPED", "input_pack": "SKIPPED", "evidence_compression": "SKIPPED", "analyst": "SKIPPED", "skeptic": "SKIPPED", "valuation_input": "SKIPPED", "valuation": "SKIPPED", "portfolio_manager": "SKIPPED"},
}


class MarketDataMissing(ValueError):
    pass


def _source_key(item: dict[str, Any], *, financial: bool) -> str:
    identity = item.get("announcement_id") or item.get("source_url") or item.get("sha256")
    if financial:
        return f"{item.get('period')}:{item.get('document_type')}:{identity or ''}"
    return str(identity)


def _document_key(document: SourceDocument, *, financial: bool) -> str:
    return _source_key({"period": document.period, "document_type": document.document_type,
                        "announcement_id": document.announcement_id, "source_url": document.source_url},
                       financial=financial)


class ResearchOrchestrator:
    def __init__(self, *, repository: ProfileRepository | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT,
                 config: OrchestratorConfig | None = None, today: Callable[[], date] = date.today) -> None:
        self.repository = repository or ProfileRepository(); self._owns_repository = repository is None
        self.research_root, self.config, self.today = research_root, config or OrchestratorConfig.from_environment(), today

    def close(self) -> None:
        if self._owns_repository: self.repository.close()

    def plan(self, symbol: str, *, mode: str | None = None,
             discovered_financial: tuple[SourceDocument, ...] = (),
             discovered_announcements: tuple[SourceDocument, ...] = ()) -> ResearchPlan:
        symbol = symbol.upper(); previous = self._previous_state(symbol)
        current = self._current_state(symbol, previous, discovered_financial, discovered_announcements)
        return build_research_plan(previous, current, today=self.today(), config=self.config, manual_mode=mode)

    def execute(self, plan: ResearchPlan, *, max_llm_calls: int | None = None) -> dict[str, Any]:
        enforce_llm_budget([plan], max_llm_calls)
        if plan.execution_plan == ExecutionPlan.NO_ACTION:
            return {"symbol": plan.symbol, "execution_plan": plan.execution_plan.value, "estimated_llm_calls": 0, "status": "NO_ACTION"}
        previous = self.research_root / plan.symbol / "analysis" / str(plan.previous_analysis_version)
        current_step = "source_collection" if plan.execution_plan == ExecutionPlan.FULL_REFRESH else "input_pack"
        folder: Path | None = None; actual_calls = 0
        try:
            if plan.execution_plan == ExecutionPlan.FULL_REFRESH:
                self._collect_sources(plan.symbol)
            current_step = "input_pack"
            built = InputPackBuilder(self.repository, research_root=self.research_root).build(plan.symbol, as_of=self.today().isoformat())
            folder = Path(built["directory"]); version = str(built["version"])
            run = self._run_document(plan, version, "PLANNED", actual_calls)
            write_json_atomic(folder / "run.json", run.model_dump(mode="json"))
            current_step = "evidence_compression"
            if plan.execution_plan == ExecutionPlan.FULL_REFRESH:
                EvidenceCompressor(research_root=self.research_root).compress(plan.symbol, version)
            elif (previous / "evidence_summary.json").exists():
                reused_summary = read_json(previous / "evidence_summary.json")
                reused_summary["analysis_version"] = version
                write_json_atomic(folder / "evidence_summary.json", reused_summary)
            if plan.execution_plan != ExecutionPlan.FULL_REFRESH:
                if not previous.is_dir(): raise ValueError("previous completed analysis version is unavailable for reuse")
                for name in ("analyst.json", "skeptic.json"):
                    shutil.copyfile(previous / name, folder / name)
                if plan.execution_plan == ExecutionPlan.DECISION_ONLY:
                    for name in ("valuation_input.json", "valuation.json"):
                        shutil.copyfile(previous / name, folder / name)
            if plan.execution_plan == ExecutionPlan.FULL_REFRESH:
                current_step = "analyst"; actual_calls += 1; FundamentalAnalyst(research_root=self.research_root).run(plan.symbol, version=version)
                current_step = "skeptic"; actual_calls += 1; SkepticAnalyst(research_root=self.research_root).run(plan.symbol, version=version)
            if plan.execution_plan != ExecutionPlan.DECISION_ONLY:
                current_step = "valuation_input"; ValuationInputBuilder(research_root=self.research_root).build(plan.symbol, version=version)
                current_step = "valuation"; actual_calls += 1; ValuationAgent(research_root=self.research_root).run(plan.symbol, version=version)
            current_step = "portfolio_manager"; actual_calls += 1; PortfolioManager(research_root=self.research_root, repository=self.repository).run(plan.symbol, version=version)
            run = self._run_document(plan, version, "COMPLETED", actual_calls)
            write_json_atomic(folder / "run.json", run.model_dump(mode="json"))
            write_json_atomic(folder.parents[1] / "current.json", {"schema_version": 1, "status": "analyzed", "analysis_version": version, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            return {"symbol": plan.symbol, "version": version, "execution_plan": plan.execution_plan.value, "estimated_llm_calls": plan.estimated_llm_calls, "actual_llm_calls": actual_calls, "status": "COMPLETED"}
        except Exception:
            if folder is not None:
                failed = self._run_document(plan, folder.name, "FAILED", actual_calls, failed_step=current_step)
                write_json_atomic(folder / "run.json", failed.model_dump(mode="json"))
            raise

    def _previous_state(self, symbol: str) -> ResearchState | None:
        analysis = self.research_root / symbol / "analysis"
        if not analysis.exists(): return None
        choices = []
        for folder in analysis.iterdir():
            if not folder.is_dir() or not (folder / "decision.json").exists(): continue
            run = read_json(folder / "run.json")
            if run and run.get("status") != "COMPLETED": continue
            choices.append(folder)
        if not choices: return None
        folder = sorted(choices, key=lambda item: item.name)[-1]
        snapshot, valuation = read_json(folder / "input_snapshot.json"), read_json(folder / "valuation.json")
        as_of = snapshot.get("as_of"); generated = valuation.get("generation", {}).get("generated_at")
        def snapshot_keys(category: str, *, financial: bool) -> frozenset[str]:
            keys = []
            for item in snapshot.get(category, []):
                metadata = read_json((folder / str(item.get("metadata_file", ""))).resolve()) if item.get("metadata_file") else {}
                keys.append(_source_key({**item, **metadata}, financial=financial))
            return frozenset(keys)
        return ResearchState(symbol, folder.name, date.fromisoformat(as_of) if as_of else None,
                             date.fromisoformat(str(generated)[:10]) if generated else (date.fromisoformat(as_of) if as_of else None),
                             snapshot.get("yield_context", {}).get("latest_price"), snapshot.get("yield_context", {}).get("yield_signal"),
                             snapshot_keys("financial_reports", financial=True),
                             snapshot_keys("announcements", financial=False))

    def _current_state(self, symbol: str, previous: ResearchState | None,
                       discovered_financial: tuple[SourceDocument, ...] = (),
                       discovered_announcements: tuple[SourceDocument, ...] = ()) -> ResearchState:
        facts = self.repository.profile_facts(symbol)
        if facts is None: raise ValueError(f"stable dividend universe symbol not found: {symbol}")
        if facts.get("latest_price") is None or facts.get("yield_signal") is None:
            raise MarketDataMissing(f"latest price or yield signal unavailable for {symbol}")
        sources = SourceRepository(self.research_root / symbol)
        reports, announcements = sources.documents("financial_reports"), sources.documents("announcements")
        report_keys = {_source_key(x, financial=True) for x in reports}
        report_keys.update(_document_key(x, financial=True) for x in discovered_financial)
        announcement_keys = {_source_key(x, financial=False) for x in announcements}
        announcement_keys.update(_document_key(x, financial=False) for x in discovered_announcements)
        return ResearchState(symbol, previous.analysis_version if previous else None, previous.analysis_date if previous else None,
                             previous.valuation_date if previous else None, facts.get("latest_price"), facts.get("yield_signal"),
                             frozenset(report_keys), frozenset(announcement_keys))

    def _collect_sources(self, symbol: str) -> None:
        ProfileBuilder(self.repository, research_root=self.research_root).build(symbol)
        client = CninfoClient()
        try:
            financial, announcements = discover_documents(symbol, client)
            root = self.research_root / symbol
            for document in (*financial, *announcements): save_document(document, root, client)
        finally: client.close()

    @staticmethod
    def _run_document(plan: ResearchPlan, version: str, status: str, actual_calls: int, failed_step: str | None = None) -> RunDocument:
        reused = {} if plan.execution_plan == ExecutionPlan.FULL_REFRESH else {name: str(plan.previous_analysis_version) for name in ("evidence_summary", "analyst", "skeptic")}
        if plan.execution_plan == ExecutionPlan.DECISION_ONLY: reused["valuation"] = str(plan.previous_analysis_version)
        return RunDocument(schema_version=1, symbol=plan.symbol, analysis_version=version,
                           created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           trigger_reasons=[x.value for x in plan.triggers], primary_trigger=plan.primary_trigger.value,
                           previous_analysis_version=plan.previous_analysis_version, execution_plan=plan.execution_plan.value,
                           steps=STEP_PLANS[plan.execution_plan], reused_outputs=reused,
                           market_change={"previous_price": plan.previous_price, "current_price": plan.current_price,
                                          "price_change_pct": plan.price_change_pct, "previous_yield_signal": plan.previous_yield_signal,
                                          "current_yield_signal": plan.current_yield_signal},
                           llm_plan={"analyst": "analyst" in plan.estimated_agents, "skeptic": "skeptic" in plan.estimated_agents,
                                     "valuation": "valuation" in plan.estimated_agents, "portfolio": "portfolio" in plan.estimated_agents,
                                     "estimated_calls": plan.estimated_llm_calls, "estimated_agents": list(plan.estimated_agents)},
                           status=status, failed_step=failed_step, actual_llm_calls=actual_calls)


def _display(plan: ResearchPlan) -> str:
    lines = [f"Symbol: {plan.symbol}", "Triggers:", *[f"- {x.value}" for x in plan.triggers],
             f"Primary trigger: {plan.primary_trigger.value}", f"Previous analysis: {plan.previous_analysis_version}",
             f"Execution plan: {plan.execution_plan.value}"]
    lines.extend(f"- {name}: {value}" for name, value in STEP_PLANS[plan.execution_plan].items())
    lines.append(f"Estimated LLM calls: {plan.estimated_llm_calls}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan and execute deterministic research refreshes")
    parser.add_argument("--symbol", required=True); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("full", "valuation", "decision")); parser.add_argument("--max-llm-calls", type=int)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv); orchestrator = ResearchOrchestrator(research_root=args.research_root)
    try:
        plan = orchestrator.plan(args.symbol, mode=args.mode); enforce_llm_budget([plan], args.max_llm_calls); print(_display(plan))
        if not args.dry_run: print(orchestrator.execute(plan, max_llm_calls=args.max_llm_calls))
        return 0
    except Exception as exc:
        print({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}); return 1
    finally: orchestrator.close()


if __name__ == "__main__": raise SystemExit(main())
