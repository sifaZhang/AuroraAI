from pathlib import Path

import pytest

from backend.research.daily_research_runner import build_universe, resolve_budget, run_daily
from backend.research.research_git import validate_staged_paths
from backend.research.research_plan import ExecutionPlan, PLAN_AGENTS, ResearchPlan, Trigger
from backend.research.json_store import write_json_atomic
from backend.research.research_trigger_state import TriggerStateStore
from backend.research.source_discovery import DiscoveryResult


def make_plan(symbol, execution):
    return ResearchPlan(symbol, (Trigger.NONE,), Trigger.NONE, execution, "old", 10, 10, 0,
                        "watch", "watch", PLAN_AGENTS[execution])


class Repo:
    def __init__(self, enabled, candidates): self.enabled, self.candidates = enabled, candidates
    def enabled_symbols(self): return self.enabled
    def research_candidates(self): return [{"symbol": item} for item in self.candidates]
    def profile_facts(self, symbol): return {"yield_signal": "add" if symbol in self.candidates else "watch"}


class Discovery:
    def __init__(self, failing=()): self.failing = set(failing)
    def discover(self, symbol):
        if symbol in self.failing: raise RuntimeError("CNINFO unavailable")
        return DiscoveryResult((), ())


class Orchestrator:
    def __init__(self, plans, failing=()): self.plans, self.events, self.failing = plans, [], set(failing)
    def plan(self, symbol, **_kwargs): self.events.append(("plan", symbol)); return self.plans[symbol]
    def execute(self, plan, **_kwargs):
        self.events.append(("execute", plan.symbol))
        if plan.symbol in self.failing: raise RuntimeError("agent failed")
        return {"status": "NO_ACTION" if plan.execution_plan == ExecutionPlan.NO_ACTION else "COMPLETED",
                "actual_llm_calls": plan.estimated_llm_calls}


def test_universe_only_includes_active_add_heavy_without_exit_history(tmp_path):
    for symbol in ("WATCH", "DISABLED"): (tmp_path / symbol).mkdir()
    assert build_universe(Repo(["ADD", "WATCH"], ["ADD"]), tmp_path) == ["ADD"]


def test_universe_includes_exit_once_then_excludes_confirmed_symbol(tmp_path):
    folder = tmp_path / "EXIT" / "analysis" / "2026-08-22_v1"
    folder.mkdir(parents=True)
    write_json_atomic(folder / "decision.json", {"research_decision": "watch"})
    write_json_atomic(folder / "input_snapshot.json", {"yield_context": {"yield_signal": "add"}})
    repo = Repo(["EXIT"], [])
    assert build_universe(repo, tmp_path) == ["EXIT"]
    TriggerStateStore(tmp_path).write_success(symbol="EXIT", analysis_version="v2", current_price=10,
                                              current_signal="watch", reset_anchor=False,
                                              anchor_reason="YIELD_SIGNAL_CHANGE", exit_confirmation=True)
    assert build_universe(repo, tmp_path) == []


def test_exit_confirmed_symbol_reenters_when_signal_add(tmp_path):
    TriggerStateStore(tmp_path).write_success(symbol="REENTER", analysis_version="v1", current_price=10,
                                              current_signal="entry", reset_anchor=False,
                                              anchor_reason="YIELD_SIGNAL_CHANGE", exit_confirmation=True)
    assert build_universe(Repo(["REENTER"], ["REENTER"]), tmp_path) == ["REENTER"]


def test_24_symbol_matrix_plans_ten_calls_before_execution(tmp_path):
    symbols = [f"S{i:02}" for i in range(24)]
    plans = {s: make_plan(s, ExecutionPlan.NO_ACTION) for s in symbols}
    plans.update({s: make_plan(s, ExecutionPlan.VALUATION_REFRESH) for s in symbols[20:23]})
    plans[symbols[23]] = make_plan(symbols[23], ExecutionPlan.FULL_REFRESH)
    orchestrator = Orchestrator(plans)
    result = run_daily(repository=Repo(symbols, symbols), orchestrator=orchestrator, discovery=Discovery(),
                       research_root=tmp_path, execute=True, max_llm_calls=20)
    assert result["estimated_llm_calls"] == result["actual_llm_calls"] == 10
    assert next(i for i, event in enumerate(orchestrator.events) if event[0] == "execute") == 24


def test_budget_blocks_all_execution(tmp_path):
    symbols = [f"S{i}" for i in range(11)]
    orchestrator = Orchestrator({s: make_plan(s, ExecutionPlan.VALUATION_REFRESH) for s in symbols})
    result = run_daily(repository=Repo(symbols, symbols), orchestrator=orchestrator, discovery=Discovery(),
                       research_root=tmp_path, execute=True, max_llm_calls=20)
    assert result["status"] == "BUDGET_BLOCKED" and result["estimated_llm_calls"] == 22
    assert not any(event[0] == "execute" for event in orchestrator.events)


def test_budget_precedence(monkeypatch):
    monkeypatch.setenv("RESEARCH_DAILY_MAX_LLM_CALLS", "7")
    assert resolve_budget(None) == 7
    assert resolve_budget(3) == 3


def test_discovery_failure_is_partial_and_others_execute(tmp_path):
    symbols = ["A", "B", "C"]
    plans = {s: make_plan(s, ExecutionPlan.DECISION_ONLY) for s in symbols}
    result = run_daily(repository=Repo(symbols, symbols), orchestrator=Orchestrator(plans),
                       discovery=Discovery({"B"}), research_root=tmp_path, execute=True)
    assert result["status"] == "PARTIAL"
    assert [(x["symbol"], x["status"]) for x in result["results"]] == [
        ("A", "COMPLETED"), ("B", "DISCOVERY_FAILED"), ("C", "COMPLETED")]


def test_execution_failure_is_isolated(tmp_path):
    symbols = ["A", "B", "C"]
    plans = {s: make_plan(s, ExecutionPlan.DECISION_ONLY) for s in symbols}
    orchestrator = Orchestrator(plans, {"B"})
    result = run_daily(repository=Repo(symbols, symbols), orchestrator=orchestrator,
                       discovery=Discovery(), research_root=tmp_path, execute=True)
    assert result["status"] == "PARTIAL"
    assert [x["status"] for x in result["results"]] == ["COMPLETED", "EXECUTION_FAILED", "COMPLETED"]
    assert orchestrator.events[-1] == ("execute", "C")


def test_all_no_action_does_not_write_summary(tmp_path):
    plans = {"A": make_plan("A", ExecutionPlan.NO_ACTION)}
    run_daily(repository=Repo(["A"], ["A"]), orchestrator=Orchestrator(plans), discovery=Discovery(),
              research_root=tmp_path, execute=True)
    assert not (tmp_path / "runs").exists()


def test_research_git_scope_validation():
    validate_staged_paths(["data/research/A/run.json"])
    with pytest.raises(ValueError, match="frontend/metadata.json"):
        validate_staged_paths(["data/research/A/run.json", "frontend/metadata.json"])


def test_combined_workflow_contract():
    text = Path(".github/workflows/dividend-daily-report.yml").read_text(encoding="utf-8")
    assert 'cron: "0 12 * * *"' in text and "contents: write" in text
    assert "group: dividend-research-daily" in text and "timeout-minutes: 60" in text
    assert text.index("Generate daily dividend position report") < text.index("Run daily research")
    assert text.index("Check A-share trading day") < text.index("Generate daily dividend position report")
    assert "id: trading_day" in text
    gate = "steps.trading_day.outputs.is_trading_day == 'true'"
    assert text.count(gate) == 3
    assert "python -m backend.market.check_trading_day" in text
    assert '--github-output "$GITHUB_OUTPUT"' in text
    assert "Dividend: skipped" in text and "Research: skipped" in text and "Research commit: skipped" in text
    for name in ("send_email", "trade_date", "research_enabled", "research_dry_run", "research_symbol", "research_mode", "research_max_llm_calls"):
        assert f"{name}:" in text
    assert "daily_research_runner \"${ARGS[@]}\" --execute" in text
    assert "git add -- data/research/" in text and "git add ." not in text
    assert "--force" not in text and "PAT" not in text and "printenv" not in text
    assert not Path(".github/workflows/research-daily.yml").exists()
