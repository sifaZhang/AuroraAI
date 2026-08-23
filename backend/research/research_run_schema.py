from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RunDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    created_at: str
    trigger_reasons: list[str]
    primary_trigger: str
    previous_analysis_version: str | None
    execution_plan: str
    steps: dict[str, str]
    reused_outputs: dict[str, str]
    market_change: dict[str, float | str | None]
    llm_plan: dict[str, bool | int | list[str]]
    status: Literal["PLANNED", "COMPLETED", "PARTIAL", "FAILED"]
    failed_step: str | None = None
    actual_llm_calls: int = 0
