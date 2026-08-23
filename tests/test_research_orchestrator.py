from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.research.json_store import read_json, write_json_atomic
from backend.research.research_orchestrator import STEP_PLANS, MarketDataMissing, ResearchOrchestrator
from backend.research.research_plan import (ExecutionPlan, OrchestratorConfig, ResearchState, Trigger,
                                            build_research_plan, enforce_llm_budget)


TODAY = date(2026, 8, 23)
CONFIG = OrchestratorConfig(.10, 30, 90)


def state(*, price=100., signal="entry", analysis_days=1, valuation_days=1, reports=(), announcements=()):
    return ResearchState("000858.SZ", "2026-08-22_v1", TODAY - timedelta(days=analysis_days),
                         TODAY - timedelta(days=valuation_days), price, signal, frozenset(reports), frozenset(announcements))


def plan(previous, current, mode=None):
    return build_research_plan(previous, current, today=TODAY, config=CONFIG, manual_mode=mode)


def test_no_change_and_small_price_move_are_no_action():
    result = plan(state(), state(price=103.))
    assert result.execution_plan == ExecutionPlan.NO_ACTION and result.estimated_llm_calls == 0


@pytest.mark.parametrize(("old", "new"), [("entry", "add"), ("heavy", "watch")])
def test_yield_change_refreshes_valuation(old, new):
    result = plan(state(signal=old), state(signal=new))
    assert result.execution_plan == ExecutionPlan.VALUATION_REFRESH
    assert result.estimated_agents == ("valuation", "portfolio")


def test_price_threshold_is_inclusive():
    assert plan(state(), state(price=109.)).execution_plan == ExecutionPlan.NO_ACTION
    assert plan(state(), state(price=110.)).execution_plan == ExecutionPlan.VALUATION_REFRESH
    assert plan(state(), state(price=90.)).execution_plan == ExecutionPlan.VALUATION_REFRESH
    assert plan(state(), state(price=88.)).estimated_llm_calls == 2


def test_new_financial_report_triggers_full_once():
    result = plan(state(reports=("annual",)), state(reports=("annual", "h1")))
    assert result.primary_trigger == Trigger.NEW_FINANCIAL_REPORT
    assert result.execution_plan == ExecutionPlan.FULL_REFRESH and result.estimated_llm_calls == 4
    assert plan(state(reports=("annual", "h1")), state(reports=("annual", "h1"))).execution_plan == ExecutionPlan.NO_ACTION


def test_new_announcement_triggers_full_once():
    assert plan(state(announcements=("a",)), state(announcements=("a", "b"))).execution_plan == ExecutionPlan.FULL_REFRESH
    assert plan(state(announcements=("a",)), state(announcements=("a",))).execution_plan == ExecutionPlan.NO_ACTION


def test_discovered_source_identity_dedupes_consumed_reports_and_announcements():
    consumed = state(reports=("2026-H1:half_year_report:report-1",), announcements=("announcement-1",))
    same = state(reports=("2026-H1:half_year_report:report-1",), announcements=("announcement-1",))
    assert plan(consumed, same).execution_plan == ExecutionPlan.NO_ACTION
    new_report = state(reports=(*same.financial_source_keys, "2026-Q3:q3_report:report-2"),
                       announcements=same.announcement_source_keys)
    assert plan(consumed, new_report).primary_trigger == Trigger.NEW_FINANCIAL_REPORT
    new_announcement = state(reports=consumed.financial_source_keys,
                             announcements=(*same.announcement_source_keys, "announcement-2"))
    assert plan(consumed, new_announcement).primary_trigger == Trigger.MATERIAL_ANNOUNCEMENT


def test_staleness_routes_to_correct_plan():
    assert plan(state(valuation_days=31), state(valuation_days=31)).execution_plan == ExecutionPlan.VALUATION_REFRESH
    result = plan(state(analysis_days=91, valuation_days=91), state(analysis_days=91, valuation_days=91))
    assert result.primary_trigger == Trigger.RESEARCH_STALE and result.execution_plan == ExecutionPlan.FULL_REFRESH


def test_trigger_merge_runs_only_one_full_chain():
    result = plan(state(price=100, signal="entry", reports=("a",)), state(price=80, signal="add", reports=("a", "b")))
    assert result.triggers == (Trigger.NEW_FINANCIAL_REPORT, Trigger.YIELD_SIGNAL_CHANGE, Trigger.PRICE_MOVE)
    assert result.estimated_llm_calls == 4


@pytest.mark.parametrize(("mode", "expected", "calls"), [("full", ExecutionPlan.FULL_REFRESH, 4), ("valuation", ExecutionPlan.VALUATION_REFRESH, 2), ("decision", ExecutionPlan.DECISION_ONLY, 1)])
def test_manual_modes(mode, expected, calls):
    result = plan(state(), state(), mode)
    assert result.primary_trigger == Trigger.MANUAL and result.execution_plan == expected and result.estimated_llm_calls == calls


def test_budget_rejects_before_execution():
    plans = [plan(state(), state(price=80.)) for _ in range(12)]
    with pytest.raises(ValueError, match="24 exceed limit 20"): enforce_llm_budget(plans, 20)


def test_no_action_does_not_create_version(tmp_path: Path):
    class Repo: pass
    orchestrator = ResearchOrchestrator(repository=Repo(), research_root=tmp_path)
    before = set(tmp_path.rglob("*")); result = orchestrator.execute(plan(state(), state()))
    assert result["status"] == "NO_ACTION" and set(tmp_path.rglob("*")) == before


def test_compression_step_routing_does_not_add_llm_calls():
    assert STEP_PLANS[ExecutionPlan.FULL_REFRESH]["evidence_compression"] == "RUN"
    assert STEP_PLANS[ExecutionPlan.VALUATION_REFRESH]["evidence_compression"] == "REUSED"
    assert STEP_PLANS[ExecutionPlan.DECISION_ONLY]["evidence_compression"] == "REUSED"
    assert STEP_PLANS[ExecutionPlan.NO_ACTION]["evidence_compression"] == "SKIPPED"
    assert plan(state(), state(reports=("new",))).estimated_llm_calls == 4


def test_missing_market_facts_fail_safe(tmp_path: Path):
    class Repo:
        def profile_facts(self, _symbol): return {"latest_price": None, "yield_signal": "watch"}
    with pytest.raises(MarketDataMissing):
        ResearchOrchestrator(repository=Repo(), research_root=tmp_path).plan("000858.SZ")


def test_run_metadata_records_reuse():
    result = plan(state(), state(price=80.))
    run = ResearchOrchestrator._run_document(result, "2026-08-23_v1", "PLANNED", 0)
    assert run.reused_outputs == {"evidence_summary": "2026-08-22_v1", "analyst": "2026-08-22_v1", "skeptic": "2026-08-22_v1"}
    assert run.steps["analyst"] == "REUSED" and run.steps["valuation"] == "RUN"
    assert run.llm_plan["estimated_calls"] == 2


def test_valuation_failure_stops_portfolio_and_does_not_update_current(tmp_path: Path, monkeypatch):
    folder = tmp_path / "000858.SZ" / "analysis" / "2026-08-23_v1"; current = tmp_path / "000858.SZ" / "current.json"
    current.parent.mkdir(parents=True); write_json_atomic(current, {"schema_version": 1, "status": "not_analyzed"})
    class Repo: pass
    monkeypatch.setattr("backend.research.research_orchestrator.InputPackBuilder.build", lambda *_a, **_k: (folder.mkdir(parents=True), write_json_atomic(folder/"input_snapshot.json", {}), {"directory": str(folder), "version": folder.name})[-1])
    monkeypatch.setattr("backend.research.research_orchestrator.ResearchOrchestrator._collect_sources", lambda *_: None)
    monkeypatch.setattr("backend.research.research_orchestrator.EvidenceCompressor.compress", lambda *_a, **_k: {})
    monkeypatch.setattr("backend.research.research_orchestrator.FundamentalAnalyst.run", lambda *_a, **_k: {})
    monkeypatch.setattr("backend.research.research_orchestrator.SkepticAnalyst.run", lambda *_a, **_k: {})
    monkeypatch.setattr("backend.research.research_orchestrator.ValuationInputBuilder.build", lambda *_a, **_k: {})
    monkeypatch.setattr("backend.research.research_orchestrator.ValuationAgent.run", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("valuation failed")))
    called = []
    monkeypatch.setattr("backend.research.research_orchestrator.PortfolioManager.run", lambda *_a, **_k: called.append(True))
    orchestrator = ResearchOrchestrator(repository=Repo(), research_root=tmp_path)
    full = plan(state(), state(reports=("new",)))
    with pytest.raises(RuntimeError, match="valuation failed"): orchestrator.execute(full)
    assert not called and read_json(folder/"run.json")["status"] == "FAILED"
    assert read_json(folder/"run.json")["failed_step"] == "valuation"
    assert read_json(current)["status"] == "not_analyzed"


def test_portfolio_failure_does_not_complete_version(tmp_path: Path, monkeypatch):
    folder = tmp_path / "000858.SZ" / "analysis" / "2026-08-23_v1"
    class Repo: pass
    monkeypatch.setattr("backend.research.research_orchestrator.InputPackBuilder.build", lambda *_a, **_k: (folder.mkdir(parents=True), {"directory": str(folder), "version": folder.name})[-1])
    monkeypatch.setattr("backend.research.research_orchestrator.ResearchOrchestrator._collect_sources", lambda *_: None)
    monkeypatch.setattr("backend.research.research_orchestrator.EvidenceCompressor.compress", lambda *_a, **_k: {})
    for target in ("FundamentalAnalyst.run", "SkepticAnalyst.run", "ValuationInputBuilder.build", "ValuationAgent.run"):
        monkeypatch.setattr("backend.research.research_orchestrator." + target, lambda *_a, **_k: {})
    monkeypatch.setattr("backend.research.research_orchestrator.PortfolioManager.run", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("portfolio failed")))
    with pytest.raises(RuntimeError): ResearchOrchestrator(repository=Repo(), research_root=tmp_path).execute(plan(state(), state(reports=("new",))))
    assert read_json(folder/"run.json")["status"] == "FAILED"


def test_synthetic_poc_matrix():
    assert plan(state(signal="entry"), state(signal="add")).estimated_llm_calls == 2
    assert plan(state(reports=("annual",)), state(reports=("annual", "h1"))).estimated_llm_calls == 4
    assert plan(state(price=100), state(price=112)).estimated_llm_calls == 2
    assert plan(state(), state()).estimated_llm_calls == 0
