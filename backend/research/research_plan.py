from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class Trigger(StrEnum):
    NEW_FINANCIAL_REPORT = "NEW_FINANCIAL_REPORT"
    MATERIAL_ANNOUNCEMENT = "MATERIAL_ANNOUNCEMENT"
    RESEARCH_STALE = "RESEARCH_STALE"
    YIELD_SIGNAL_CHANGE = "YIELD_SIGNAL_CHANGE"
    PRICE_MOVE = "PRICE_MOVE"
    VALUATION_STALE = "VALUATION_STALE"
    MANUAL = "MANUAL"
    NONE = "NONE"


class ExecutionPlan(StrEnum):
    NO_ACTION = "NO_ACTION"
    DECISION_ONLY = "DECISION_ONLY"
    VALUATION_REFRESH = "VALUATION_REFRESH"
    FULL_REFRESH = "FULL_REFRESH"


TRIGGER_PRIORITY = (
    Trigger.NEW_FINANCIAL_REPORT, Trigger.MATERIAL_ANNOUNCEMENT, Trigger.RESEARCH_STALE,
    Trigger.YIELD_SIGNAL_CHANGE, Trigger.PRICE_MOVE, Trigger.VALUATION_STALE,
)
PLAN_AGENTS = {
    ExecutionPlan.NO_ACTION: (), ExecutionPlan.DECISION_ONLY: ("portfolio",),
    ExecutionPlan.VALUATION_REFRESH: ("valuation", "portfolio"),
    ExecutionPlan.FULL_REFRESH: ("analyst", "skeptic", "valuation", "portfolio"),
}


@dataclass(frozen=True)
class OrchestratorConfig:
    price_move_threshold: float = .10
    valuation_stale_days: int = 30
    full_refresh_days: int = 90

    @classmethod
    def from_environment(cls) -> "OrchestratorConfig":
        threshold = os.environ.get("RESEARCH_PRICE_DROP_THRESHOLD", os.environ.get("RESEARCH_PRICE_MOVE_THRESHOLD", ".10"))
        return cls(float(threshold),
                   int(os.environ.get("RESEARCH_VALUATION_STALE_DAYS", "30")),
                   int(os.environ.get("RESEARCH_FULL_REFRESH_DAYS", "90")))


@dataclass(frozen=True)
class ResearchState:
    symbol: str
    analysis_version: str | None
    analysis_date: date | None
    valuation_date: date | None
    price: float | None
    yield_signal: str | None
    financial_source_keys: frozenset[str] = frozenset()
    announcement_source_keys: frozenset[str] = frozenset()
    price_anchor: float | None = None
    exit_confirmed_signal: str | None = None
    full_analysis_date: date | None = None


@dataclass(frozen=True)
class ResearchPlan:
    symbol: str
    triggers: tuple[Trigger, ...]
    primary_trigger: Trigger
    execution_plan: ExecutionPlan
    previous_analysis_version: str | None
    previous_price: float | None
    current_price: float | None
    price_change_pct: float | None
    previous_yield_signal: str | None
    current_yield_signal: str | None
    estimated_agents: tuple[str, ...] = field(default_factory=tuple)
    exit_confirmation: bool = False
    price_trigger_threshold: float = .10

    @property
    def estimated_llm_calls(self) -> int:
        return len(self.estimated_agents)


def build_research_plan(previous: ResearchState | None, current: ResearchState, *, today: date,
                        config: OrchestratorConfig, manual_mode: str | None = None) -> ResearchPlan:
    if manual_mode:
        selected = {"full": ExecutionPlan.FULL_REFRESH, "valuation": ExecutionPlan.VALUATION_REFRESH,
                    "decision": ExecutionPlan.DECISION_ONLY}[manual_mode]
        return _plan(current, previous, (Trigger.MANUAL,), Trigger.MANUAL, selected, config)
    if previous is None:
        selected = ExecutionPlan.FULL_REFRESH if current.yield_signal in {"add", "heavy"} else ExecutionPlan.NO_ACTION
        trigger = Trigger.RESEARCH_STALE if selected == ExecutionPlan.FULL_REFRESH else Trigger.NONE
        return _plan(current, None, (trigger,), trigger, selected, config)
    active = current.yield_signal in {"add", "heavy"}
    previously_active = previous.yield_signal in {"add", "heavy"}
    exit_confirmation = previously_active and not active and current.exit_confirmed_signal != current.yield_signal
    if not active:
        if exit_confirmation:
            return _plan(current, previous, (Trigger.YIELD_SIGNAL_CHANGE,), Trigger.YIELD_SIGNAL_CHANGE,
                         ExecutionPlan.VALUATION_REFRESH, config, exit_confirmation=True)
        return _plan(current, previous, (Trigger.NONE,), Trigger.NONE, ExecutionPlan.NO_ACTION, config)
    reasons: list[Trigger] = []
    if current.financial_source_keys - previous.financial_source_keys: reasons.append(Trigger.NEW_FINANCIAL_REPORT)
    if current.announcement_source_keys - previous.announcement_source_keys: reasons.append(Trigger.MATERIAL_ANNOUNCEMENT)
    full_date = previous.full_analysis_date or previous.analysis_date
    if full_date and (today - full_date).days > config.full_refresh_days: reasons.append(Trigger.RESEARCH_STALE)
    if current.yield_signal != previous.yield_signal: reasons.append(Trigger.YIELD_SIGNAL_CHANGE)
    anchor = current.price_anchor or previous.price
    price_change = _price_change(anchor, current.price)
    if price_change is not None and price_change <= -config.price_move_threshold + 1e-12: reasons.append(Trigger.PRICE_MOVE)
    if previous.valuation_date and (today - previous.valuation_date).days > config.valuation_stale_days: reasons.append(Trigger.VALUATION_STALE)
    ordered = tuple(item for item in TRIGGER_PRIORITY if item in reasons)
    if not ordered: return _plan(current, previous, (Trigger.NONE,), Trigger.NONE, ExecutionPlan.NO_ACTION, config)
    primary = ordered[0]
    if primary in {Trigger.NEW_FINANCIAL_REPORT, Trigger.MATERIAL_ANNOUNCEMENT, Trigger.RESEARCH_STALE}:
        selected = ExecutionPlan.FULL_REFRESH
    else:
        selected = ExecutionPlan.VALUATION_REFRESH
    return _plan(current, previous, ordered, primary, selected, config)


def enforce_llm_budget(plans: list[ResearchPlan], maximum: int | None) -> None:
    total = sum(plan.estimated_llm_calls for plan in plans)
    if maximum is not None and total > maximum:
        raise ValueError(f"planned LLM calls {total} exceed limit {maximum}")


def _price_change(previous: float | None, current: float | None) -> float | None:
    return current / previous - 1 if previous and current is not None else None


def _plan(current: ResearchState, previous: ResearchState | None, triggers: tuple[Trigger, ...],
          primary: Trigger, selected: ExecutionPlan, config: OrchestratorConfig,
          exit_confirmation: bool = False) -> ResearchPlan:
    anchor = current.price_anchor or (previous.price if previous else None)
    return ResearchPlan(current.symbol, triggers, primary, selected,
                        previous.analysis_version if previous else None,
                        anchor, current.price,
                        _price_change(anchor, current.price),
                        previous.yield_signal if previous else None, current.yield_signal,
                        PLAN_AGENTS[selected], exit_confirmation, config.price_move_threshold)
