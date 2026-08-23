from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.research.json_store import read_json, write_json_atomic
from backend.research.research_plan import ExecutionPlan, OrchestratorConfig, ResearchState, Trigger, build_research_plan
from backend.research.research_trigger_state import TriggerStateStore, requires_exit_confirmation


TODAY = date(2026, 8, 23)
CONFIG = OrchestratorConfig(.10, 30, 90)


def state(*, signal="heavy", price=100.0, anchor=None, analysis_days=1, valuation_days=1,
          full_days=1, exit_confirmed=None, reports=()):
    return ResearchState(
        "000858.SZ", "2026-08-22_v1", TODAY - timedelta(days=analysis_days),
        TODAY - timedelta(days=valuation_days), price, signal, frozenset(reports), frozenset(),
        anchor, exit_confirmed, TODAY - timedelta(days=full_days),
    )


def planned(previous, current, mode=None):
    return build_research_plan(previous, current, today=TODAY, config=CONFIG, manual_mode=mode)


@pytest.mark.parametrize(("signal", "expected", "calls"), [
    ("entry", ExecutionPlan.NO_ACTION, 0),
    ("watch", ExecutionPlan.NO_ACTION, 0),
    ("add", ExecutionPlan.FULL_REFRESH, 4),
    ("heavy", ExecutionPlan.FULL_REFRESH, 4),
])
def test_first_deep_research_scope(signal, expected, calls):
    result = planned(None, state(signal=signal))
    assert result.execution_plan == expected and result.estimated_llm_calls == calls


@pytest.mark.parametrize(("anchor", "price", "expected"), [
    (100, 95, ExecutionPlan.NO_ACTION),
    (100, 91, ExecutionPlan.NO_ACTION),
    (100, 90, ExecutionPlan.VALUATION_REFRESH),
    (90, 85, ExecutionPlan.NO_ACTION),
    (90, 82, ExecutionPlan.NO_ACTION),
    (90, 81, ExecutionPlan.VALUATION_REFRESH),
    (81, 75, ExecutionPlan.NO_ACTION),
    (81, 72.9, ExecutionPlan.VALUATION_REFRESH),
    (100, 110, ExecutionPlan.NO_ACTION),
    (100, 130, ExecutionPlan.NO_ACTION),
])
def test_downside_only_staged_price_trigger(anchor, price, expected):
    result = planned(state(price=anchor), state(price=price, anchor=anchor))
    assert result.execution_plan == expected
    assert (result.primary_trigger == Trigger.PRICE_MOVE) == (expected == ExecutionPlan.VALUATION_REFRESH)


def test_yield_change_remains_independent_from_price_increase():
    result = planned(state(signal="heavy", price=100), state(signal="add", price=130, anchor=100))
    assert result.execution_plan == ExecutionPlan.VALUATION_REFRESH
    assert result.primary_trigger == Trigger.YIELD_SIGNAL_CHANGE
    assert Trigger.PRICE_MOVE not in result.triggers


@pytest.mark.parametrize("signal", ["entry", "watch"])
def test_exit_confirmation_is_once_per_inactive_signal(signal):
    first = planned(state(signal="add"), state(signal=signal))
    assert first.exit_confirmation and first.estimated_llm_calls == 2
    repeated = planned(state(signal=signal), state(signal=signal, exit_confirmed=signal, analysis_days=150, full_days=150,
                                                    reports=("new",)))
    assert repeated.execution_plan == ExecutionPlan.NO_ACTION and repeated.estimated_llm_calls == 0


def test_reentry_uses_valuation_when_fresh_and_full_when_stale():
    fresh = planned(state(signal="entry", full_days=30), state(signal="add", exit_confirmed="entry", full_days=30))
    stale = planned(state(signal="entry", full_days=91), state(signal="add", exit_confirmed="entry", full_days=91))
    assert fresh.execution_plan == ExecutionPlan.VALUATION_REFRESH
    assert stale.execution_plan == ExecutionPlan.FULL_REFRESH


def test_active_stale_and_new_report_full_but_exited_inactive_is_no_action():
    assert planned(state(full_days=91), state(full_days=91)).execution_plan == ExecutionPlan.FULL_REFRESH
    assert planned(state(reports=("annual",)), state(reports=("annual", "h1"))).execution_plan == ExecutionPlan.FULL_REFRESH
    exited = planned(state(signal="entry", full_days=150), state(signal="entry", full_days=150,
                                                              exit_confirmed="entry", reports=("new",)))
    assert exited.execution_plan == ExecutionPlan.NO_ACTION


@pytest.mark.parametrize("signal", ["entry", "watch"])
def test_manual_full_overrides_inactive_scope(signal):
    assert planned(None, state(signal=signal), "full").execution_plan == ExecutionPlan.FULL_REFRESH


def test_environment_prefers_new_price_drop_name(monkeypatch):
    monkeypatch.setenv("RESEARCH_PRICE_MOVE_THRESHOLD", ".20")
    monkeypatch.setenv("RESEARCH_PRICE_DROP_THRESHOLD", ".15")
    assert OrchestratorConfig.from_environment().price_move_threshold == .15


def test_trigger_state_atomic_write_and_anchor_audit(tmp_path, monkeypatch):
    replaced = []
    from backend.research import json_store
    original = json_store.os.replace
    monkeypatch.setattr(json_store.os, "replace", lambda source, target: (replaced.append((Path(source), Path(target))), original(source, target))[1])
    store = TriggerStateStore(tmp_path)
    store.write_success(symbol="000858.SZ", analysis_version="v1", current_price=100, current_signal="heavy",
                        reset_anchor=True, anchor_reason="FULL_REFRESH")
    store.write_success(symbol="000858.SZ", analysis_version="manual-valuation", current_price=95,
                        current_signal="heavy", reset_anchor=False, anchor_reason="MANUAL")
    assert store.read("000858.SZ").price_trigger.anchor_price == 100
    store.write_success(symbol="000858.SZ", analysis_version="v2", current_price=90, current_signal="heavy",
                        reset_anchor=True, anchor_reason="PRICE_MOVE")
    payload = read_json(store.path("000858.SZ"))
    assert payload["price_trigger"]["previous_anchor_price"] == 100
    assert payload["price_trigger"]["anchor_price"] == 90
    assert payload["price_trigger"]["updated_reason"] == "PRICE_MOVE"
    assert len(replaced) == 3 and not list(tmp_path.rglob("*.tmp"))


def test_old_research_without_trigger_state_is_compatible_and_uses_latest_price_anchor(tmp_path):
    folder = tmp_path / "000858.SZ" / "analysis" / "2026-08-22_v1"
    folder.mkdir(parents=True)
    write_json_atomic(folder / "decision.json", {"research_decision": "watch"})
    write_json_atomic(folder / "input_snapshot.json", {"yield_context": {"yield_signal": "add", "latest_price": 100}})
    assert requires_exit_confirmation(tmp_path, "000858.SZ", "entry")
    assert TriggerStateStore(tmp_path).read("000858.SZ") is None
    result = planned(state(price=100, anchor=None), state(price=90, anchor=None))
    assert result.primary_trigger == Trigger.PRICE_MOVE
