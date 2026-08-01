from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class IndustryDailySnapshot:
    trade_date: date
    classification: str
    classification_version: str
    industry_code: str
    industry_level: int
    constituent_count: int
    eligible_count: int
    valid_bar_count: int
    missing_bar_count: int
    suspended_count: int
    coverage_ratio: float
    equal_weight_return: float | None
    median_return: float | None
    rise_count: int
    fall_count: int
    flat_count: int
    rise_ratio: float | None
    fall_ratio: float | None
    strong_rise_count: int
    strong_rise_ratio: float | None
    limit_up_count: int
    limit_down_count: int
    first_limit_count: int | None
    broken_limit_count: int | None
    turnover_amount: float | None
    median_turnover_amount: float | None
    data_status: str
    source_snapshot: str


@dataclass(frozen=True)
class IndustrySnapshotBuildResult:
    trade_date: date
    industry_count: int
    success_count: int
    partial_count: int
    failed_count: int
    skipped_count: int
    snapshot_count: int
    dry_run: bool
    forced: bool
    changed: bool
    warnings: tuple[str, ...] = ()
    level_counts: dict[int, int] = field(default_factory=dict)
    data_status_counts: dict[str, int] = field(default_factory=dict)
    coverage_summary: dict[str, float] = field(default_factory=dict)
    missing_bar_count: int = 0
    first_limit_capable_count: int = 0
    broken_limit_capable_count: int = 0


@dataclass(frozen=True)
class IndustrySnapshotRangeResult:
    results: tuple[IndustrySnapshotBuildResult, ...]
    skipped_dates: tuple[date, ...]
