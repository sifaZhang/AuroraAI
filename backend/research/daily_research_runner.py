from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .json_store import write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .profile_repository import ProfileRepository
from .research_orchestrator import MarketDataMissing, ResearchOrchestrator
from .research_plan import ExecutionPlan, ResearchPlan
from .research_trigger_state import requires_exit_confirmation
from .source_discovery import SourceDiscovery


DEFAULT_MAX_LLM_CALLS = 20


@dataclass
class SymbolResult:
    symbol: str
    status: str
    execution_plan: str | None = None
    estimated_llm_calls: int = 0
    actual_llm_calls: int = 0
    usage: dict[str, Any] = field(default_factory=lambda: {"prompt_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "total_cost_cny": 0.0})
    agent_usage: dict[str, Any] = field(default_factory=dict)
    agent_costs: dict[str, float] = field(default_factory=dict)
    error: str | None = None


def resolve_budget(value: int | None) -> int:
    if value is not None:
        budget = value
    else:
        budget = int(os.environ.get("RESEARCH_DAILY_MAX_LLM_CALLS", str(DEFAULT_MAX_LLM_CALLS)))
    if budget < 0:
        raise ValueError("daily LLM budget cannot be negative")
    return budget


def build_universe(repository: ProfileRepository, research_root: Path, symbol: str | None = None) -> list[str]:
    enabled = set(repository.enabled_symbols())
    if symbol:
        selected = symbol.upper()
        if selected not in enabled:
            raise ValueError(f"symbol is not enabled in stable dividend universe: {selected}")
        return [selected]
    selected = set()
    for item in enabled:
        facts = repository.profile_facts(item)
        signal = facts.get("yield_signal") if facts else None
        if signal in {"add", "heavy"} or requires_exit_confirmation(research_root, item, signal):
            selected.add(item)
    return sorted(selected)


def _plan_payload(plan: ResearchPlan) -> dict[str, Any]:
    payload = asdict(plan)
    payload["triggers"] = [item.value for item in plan.triggers]
    payload["primary_trigger"] = plan.primary_trigger.value
    payload["execution_plan"] = plan.execution_plan.value
    payload["estimated_llm_calls"] = plan.estimated_llm_calls
    return payload


def run_daily(*, repository: ProfileRepository, orchestrator: ResearchOrchestrator,
              discovery: SourceDiscovery, research_root: Path, execute: bool = False,
              symbol: str | None = None, mode: str | None = None,
              max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
              today: date | None = None) -> dict[str, Any]:
    run_date = today or date.today()
    symbols = build_universe(repository, research_root, symbol)
    results: list[SymbolResult] = []
    plans: list[ResearchPlan] = []

    # Complete discovery and planning for the entire universe before any execution.
    for item in symbols:
        try:
            found = discovery.discover(item)
            plan = orchestrator.plan(item, mode=mode, discovered_financial=found.financial,
                                     discovered_announcements=found.announcements)
            plans.append(plan)
            results.append(SymbolResult(item, "PLANNED", plan.execution_plan.value,
                                        plan.estimated_llm_calls))
        except MarketDataMissing as exc:
            results.append(SymbolResult(item, "MARKET_DATA_MISSING", error=str(exc)))
        except Exception as exc:
            results.append(SymbolResult(item, "DISCOVERY_FAILED", error=f"{type(exc).__name__}: {exc}"))

    estimated = sum(plan.estimated_llm_calls for plan in plans)
    failures = [item for item in results if item.status not in {"PLANNED", "NO_ACTION", "COMPLETED"}]
    if estimated > max_llm_calls:
        for item in results:
            if item.status == "PLANNED":
                item.status = "BUDGET_BLOCKED"
        status = "BUDGET_BLOCKED"
    elif not execute:
        for item in results:
            if item.status == "PLANNED":
                item.status = "NO_ACTION" if item.execution_plan == ExecutionPlan.NO_ACTION.value else "DRY_RUN"
        status = "PARTIAL" if failures else "COMPLETED"
    else:
        by_symbol = {item.symbol: item for item in results}
        for plan in plans:
            item = by_symbol[plan.symbol]
            try:
                outcome = orchestrator.execute(plan, max_llm_calls=max_llm_calls)
                item.status = outcome["status"]
                item.actual_llm_calls = int(outcome.get("actual_llm_calls", 0))
                item.usage = dict(outcome.get("usage", item.usage))
                item.agent_usage = dict(outcome.get("agent_usage", {}))
                item.agent_costs = dict(outcome.get("agent_costs", {}))
                print("research_cost " + " ".join([
                    f"symbol={item.symbol}", f"calls={item.actual_llm_calls}",
                    f"prompt_tokens={item.usage['prompt_tokens']}", f"cache_hit_tokens={item.usage['cache_hit_tokens']}",
                    f"cache_miss_tokens={item.usage['cache_miss_tokens']}",
                    f"completion_tokens={item.usage['completion_tokens']}",
                    f"cost_cny={item.usage['total_cost_cny']:.6f}",
                ]))
            except Exception as exc:
                item.status = "EXECUTION_FAILED"
                item.error = f"{type(exc).__name__}: {exc}"
        failed = [item for item in results if item.status in {"DISCOVERY_FAILED", "MARKET_DATA_MISSING", "EXECUTION_FAILED"}]
        completed = [item for item in results if item.status == "COMPLETED"]
        status = "PARTIAL" if failed and (completed or any(x.status == "NO_ACTION" for x in results)) else ("FAILED" if failed else "COMPLETED")

    usage_fields = ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "completion_tokens", "total_tokens")
    usage = {name: sum(int(item.usage.get(name, 0)) for item in results) for name in usage_fields}
    usage["total_cost_cny"] = sum(float(item.usage.get("total_cost_cny", 0)) for item in results)
    researched_company_count = sum(item.actual_llm_calls > 0 for item in results)
    payload = {
        "schema_version": 1,
        "run_date": run_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode or "auto",
        "dry_run": not execute,
        "status": status,
        "universe_count": len(symbols),
        "budget": max_llm_calls,
        "estimated_llm_calls": estimated,
        "actual_llm_calls": sum(item.actual_llm_calls for item in results),
        "usage": usage,
        "researched_company_count": researched_company_count,
        "average_cost_per_researched_company_cny": usage["total_cost_cny"] / researched_company_count if researched_company_count else 0.0,
        "agent_plan": {agent: sum(agent in plan.estimated_agents for plan in plans)
                       for agent in ("analyst", "skeptic", "valuation", "portfolio")},
        "plan_counts": {execution.value: sum(plan.execution_plan == execution for plan in plans)
                        for execution in ExecutionPlan},
        "plans": [_plan_payload(plan) for plan in plans],
        "results": [asdict(item) for item in results],
    }
    meaningful = status != "COMPLETED" or any(item.status == "COMPLETED" for item in results)
    if execute and meaningful:
        write_json_atomic(research_root / "runs" / f"{run_date.isoformat()}.json", payload)
    print(f"research_cost_total companies={researched_company_count} calls={payload['actual_llm_calls']} cost_cny={usage['total_cost_cny']:.6f}")
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    lines = ["# Daily Dividend + Research", "", "Schedule: Beijing 20:00", "", "## Research", "",
             f"Universe: {payload['universe_count']}", f"Status: **{payload['status']}**",
             f"LLM calls: {payload['actual_llm_calls']} actual / {payload['estimated_llm_calls']} planned / {payload['budget']} budget", "",
             "| Symbol | Status | Plan | Calls |", "|---|---|---|---:|"]
    for item in payload["results"]:
        lines.append(f"| {item['symbol']} | {item['status']} | {item['execution_plan'] or '-'} | {item['estimated_llm_calls']} |")
    lines.extend(["", "## Research Cost", "", "| Symbol | Status | Calls | Prompt | Cache Hit | Cache Miss | Output | Cost CNY |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"])
    for item in payload["results"]:
        usage = item.get("usage", {})
        lines.append(f"| {item['symbol']} | {item['status']} | {item.get('actual_llm_calls', 0)} | "
                     f"{int(usage.get('prompt_tokens', 0)):,} | {int(usage.get('cache_hit_tokens', 0)):,} | "
                     f"{int(usage.get('cache_miss_tokens', 0)):,} | {int(usage.get('completion_tokens', 0)):,} | "
                     f"¥{float(usage.get('total_cost_cny', 0)):.4f} |")
    lines.extend(["", f"Total LLM calls: {payload['actual_llm_calls']}", f"Total cost: ¥{float(payload['usage']['total_cost_cny']):.4f}",
                  f"Researched companies: {payload['researched_company_count']}",
                  f"Average/company: ¥{float(payload['average_cost_per_researched_company_cny']):.4f}"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or execute the daily research universe")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbol")
    parser.add_argument("--mode", choices=("auto", "full", "valuation", "decision"))
    parser.add_argument("--max-llm-calls", type=int)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    repository = ProfileRepository()
    orchestrator = ResearchOrchestrator(repository=repository, research_root=args.research_root)
    discovery = SourceDiscovery()
    try:
        payload = run_daily(repository=repository, orchestrator=orchestrator, discovery=discovery,
                            research_root=args.research_root, execute=args.execute, symbol=args.symbol,
                            mode=None if args.mode == "auto" else args.mode,
                            max_llm_calls=resolve_budget(args.max_llm_calls))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(_markdown(payload))
        return 1 if payload["status"] in {"FAILED", "BUDGET_BLOCKED"} else 0
    finally:
        discovery.close()
        orchestrator.close()
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
