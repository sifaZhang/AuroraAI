"""Stable Pydantic contracts for the PR6.10 first-limit API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["tail_preview", "close_confirmed"]
Grade = Literal["S", "A", "B"]
Lifecycle = Literal[
    "watching", "eligible", "pending_close_confirmation", "confirmed",
    "eliminated", "expired", "indeterminate",
]
ChangeType = Literal[
    "unchanged", "upgraded", "downgraded", "newly_qualified",
    "eliminated", "preview_missing",
]
RunStatus = Literal["running", "success", "partial", "failed"]
ItemStatus = Literal["pending", "success", "indeterminate", "skipped", "failed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(StrictModel):
    candidate_id: int
    run_id: str
    first_limit_event_id: int
    symbol: str
    security_name: str | None = None
    trade_date: date
    stage: Stage
    as_of: datetime
    observation_day: int | None
    lifecycle: Lifecycle
    grade: Grade | None
    base_grade: Grade | None
    base_score: float | None
    change_type: ChangeType | None
    reason_code: str | None
    display_text: str | None
    first_limit_date: date
    preview_candidate_id: int | None
    created_at: datetime
    updated_at: datetime
    industry_level2: str | None = None
    industry_level3: str | None = None
    effective_industry_level: int | None = None
    effective_industry_code: str | None = None
    intraday_industry_score: float | None = None
    intraday_industry_rank: int | None = None
    official_industry_score: float | None = None
    official_industry_rank: int | None = None
    capital_activity_score: float | None = None
    leader_score: float | None = None
    intraday_total_score: float | None = None
    intraday_candidate_grade: Grade | None = None
    final_total_score: float | None = None
    final_candidate_grade: Grade | None = None
    final_buy_recommendation: str | None = None
    confirmation_status: str | None = None
    confirmation_change_type: str | None = None
    industry_context_status: str | None = None
    data_cutoff: datetime | None = None
    confirmed_at: datetime | None = None


class CandidatePage(StrictModel):
    items: list[Candidate]
    total: int
    limit: int
    offset: int
    filters: dict[str, Any]
    data_date: date
    stage: Stage
    run_id: str | None
    run_status: RunStatus | None


class Evidence(StrictModel):
    rule_code: str
    result: Literal["pass", "fail", "unknown"]
    actual_value: Any | None
    threshold_value: Any | None
    unit: str | None
    source_date: date | None
    source_time: str | None
    reason_code: str | None
    display_text: str | None
    ordinal: int


class RunSummary(StrictModel):
    run_id: str
    trade_date: date
    stage: Stage
    as_of: datetime
    data_cutoff: datetime
    status: RunStatus
    parameter_hash: str
    strategy_version: str
    detection_version: str
    pullback_version: str
    context_version: str
    requested_count: int
    success_count: int
    pending_count: int
    failed_count: int
    confirmed_count: int
    eliminated_count: int
    indeterminate_count: int
    created_at: datetime
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


class CandidateDetail(StrictModel):
    candidate: Candidate
    evidence: list[Evidence]
    run: RunSummary


class RunPage(StrictModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int
    filters: dict[str, Any]


class FailureSummary(StrictModel):
    first_limit_event_id: int
    symbol: str
    error_code: str | None
    error_message: str | None


class RunDetail(StrictModel):
    run: RunSummary
    item_status_counts: dict[str, int]
    grade_counts: dict[str, int]
    lifecycle_counts: dict[str, int]
    failures: list[FailureSummary]
    terminal: bool


class RunItem(StrictModel):
    item_id: int
    run_id: str
    first_limit_event_id: int
    symbol: str
    status: ItemStatus
    candidate_id: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class RunItemPage(StrictModel):
    items: list[RunItem]
    total: int
    limit: int
    offset: int
    run_id: str


class PreviewComparison(StrictModel):
    first_limit_event_id: int
    symbol: str
    preview_candidate_id: int | None
    close_candidate_id: int
    preview_lifecycle: Lifecycle | None
    close_lifecycle: Lifecycle
    preview_grade: Grade | None
    close_grade: Grade | None
    change_type: ChangeType
    change_reason_code: str | None
    change_display_text: str | None


class PreviewComparisonPage(StrictModel):
    items: list[PreviewComparison]
    total: int
    limit: int
    offset: int
    trade_date: date
    run_id: str | None


class RunRequest(StrictModel):
    trade_date: date
    stage: Stage
    as_of: datetime | None = None
    data_cutoff: datetime | None = None
    symbols: list[str] | None = None
    strategy_version: str | None = None
    detection_version: str | None = None
    pullback_version: str | None = None
    context_version: str | None = None
    detect_missing_events: bool = False


class RunAccepted(StrictModel):
    run_id: str
    status: RunStatus
    reused: bool
    poll_url: str
