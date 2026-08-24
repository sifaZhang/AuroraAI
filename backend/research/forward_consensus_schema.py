from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Quality = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricStatistics(Strict):
    unit: Literal["CNY", "CNY_PER_SHARE"]
    median: float
    mean: float
    min: float
    max: float
    std: float = Field(ge=0)
    institution_count: int = Field(ge=1)
    outlier_count: int = Field(ge=0)


class RevisionTrend(Strict):
    supported: bool
    matched_institution_count: int = Field(ge=0)
    revisions_up: int = Field(ge=0)
    revisions_down: int = Field(ge=0)
    revisions_unchanged: int = Field(ge=0)
    median_revision_pct: float | None = None


class ForecastYear(Strict):
    fiscal_year: int
    period: str
    quality: Quality
    freshness_window_days: Literal[90, 180] | None
    latest_report_date: str | None
    oldest_included_report_date: str | None
    median_age_days: float | None = Field(default=None, ge=0)
    fresh_estimate_ratio: float = Field(ge=0, le=1)
    net_profit: MetricStatistics | None
    eps: MetricStatistics | None
    revenue: MetricStatistics | None
    revision: RevisionTrend


class SourceRecords(Strict):
    record_count: int = Field(ge=0)
    eligible_annual_record_count: int = Field(ge=0)
    unique_institution_count: int = Field(ge=0)
    unique_report_count: int = Field(ge=0)
    latest_report_date: str | None
    oldest_report_date: str | None
    future_record_count_excluded: int = Field(ge=0)
    malformed_record_count_excluded: int = Field(ge=0)


class ForwardConsensus(Strict):
    schema_version: Literal[1]
    symbol: str
    analysis_version: str
    as_of_date: str
    source: Literal["tushare_report_rc"]
    status: Literal["AVAILABLE", "NO_COVERAGE", "STALE", "UNAVAILABLE", "SOURCE_ERROR"]
    source_error_type: str | None = None
    source_error_message: str | None = None
    forecast_years: dict[str, ForecastYear]
    source_records: SourceRecords
    generated_at: str
    method: Literal["point-in-time-institution-median-v1"]

    @model_validator(mode="after")
    def validate_status(self) -> "ForwardConsensus":
        if self.status == "AVAILABLE" and not self.forecast_years:
            raise ValueError("available forward consensus requires forecast years")
        if self.status in {"UNAVAILABLE", "SOURCE_ERROR"} and not self.source_error_type:
            raise ValueError("source failure requires an error type")
        if any(str(value.fiscal_year) != key for key, value in self.forecast_years.items()):
            raise ValueError("forecast year key mismatch")
        return self
