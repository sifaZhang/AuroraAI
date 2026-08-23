from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .json_store import read_json, write_json_atomic


class PriceTriggerState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_price: float
    anchor_date: str
    anchor_analysis_version: str
    previous_anchor_price: float | None = None
    updated_reason: str
    updated_at: str


class ExitConfirmationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed_for_signal: Literal["entry", "watch"]
    completed_at: str
    analysis_version: str


class ResearchTriggerState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    symbol: str
    price_trigger: PriceTriggerState | None = None
    exit_confirmation: ExitConfirmationState | None = None


class TriggerStateStore:
    def __init__(self, research_root: Path) -> None:
        self.research_root = research_root

    def read(self, symbol: str) -> ResearchTriggerState | None:
        payload = read_json(self.path(symbol))
        if not payload:
            return None
        state = ResearchTriggerState.model_validate(payload)
        if state.symbol != symbol.upper():
            raise ValueError("trigger state symbol mismatch")
        return state

    def write_success(
        self,
        *,
        symbol: str,
        analysis_version: str,
        current_price: float,
        current_signal: str | None,
        reset_anchor: bool,
        anchor_reason: str,
        exit_confirmation: bool = False,
        completed_at: str | None = None,
    ) -> ResearchTriggerState:
        symbol = symbol.upper()
        previous = self.read(symbol) or ResearchTriggerState(symbol=symbol)
        timestamp = completed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        price_trigger = previous.price_trigger
        if reset_anchor or price_trigger is None:
            price_trigger = PriceTriggerState(
                anchor_price=current_price,
                anchor_date=timestamp[:10],
                anchor_analysis_version=analysis_version,
                previous_anchor_price=price_trigger.anchor_price if price_trigger else None,
                updated_reason=anchor_reason if reset_anchor else "INITIALIZED_FROM_COMPLETED_RESEARCH",
                updated_at=timestamp,
            )
        confirmation = previous.exit_confirmation
        if exit_confirmation:
            if current_signal not in {"entry", "watch"}:
                raise ValueError("exit confirmation requires entry or watch signal")
            confirmation = ExitConfirmationState(
                completed_for_signal=current_signal,
                completed_at=timestamp,
                analysis_version=analysis_version,
            )
        elif current_signal in {"add", "heavy"}:
            confirmation = None
        state = ResearchTriggerState(symbol=symbol, price_trigger=price_trigger, exit_confirmation=confirmation)
        write_json_atomic(self.path(symbol), state.model_dump(mode="json"))
        return state

    def path(self, symbol: str) -> Path:
        return self.research_root / symbol.upper() / "trigger_state.json"


def latest_completed_yield_signal(research_root: Path, symbol: str) -> str | None:
    analysis = research_root / symbol.upper() / "analysis"
    if not analysis.exists():
        return None
    for folder in sorted((item for item in analysis.iterdir() if item.is_dir()), reverse=True):
        if not (folder / "decision.json").exists():
            continue
        run = read_json(folder / "run.json")
        if run and run.get("status") != "COMPLETED":
            continue
        return read_json(folder / "input_snapshot.json").get("yield_context", {}).get("yield_signal")
    return None


def requires_exit_confirmation(research_root: Path, symbol: str, current_signal: str | None) -> bool:
    if current_signal not in {"entry", "watch"}:
        return False
    previous_signal = latest_completed_yield_signal(research_root, symbol)
    if previous_signal not in {"add", "heavy"}:
        return False
    state = TriggerStateStore(research_root).read(symbol)
    return not state or not state.exit_confirmation or state.exit_confirmation.completed_for_signal != current_signal
