from dataclasses import dataclass
from datetime import date

SCORE_VERSION = "industry_score_v1"

@dataclass(frozen=True)
class IndustryScore:
    trade_date: date; classification: str; classification_version: str; industry_code: str
    industry_level: int; total_score: float; strength_score: float; breadth_score: float
    strong_rise_score: float; limit_score: float; activity_score: float
    persistence_score: float; quality_score: float; turnover_ratio_5d: float | None
    turnover_ratio_20d: float | None; median_turnover_ratio_20d: float | None
    price_volume_state: str; history_days_available: int; rank_in_level: int
    industry_count_in_level: int; percentile_in_level: float; confidence: str
    score_version: str; evidence_json: str

@dataclass(frozen=True)
class IndustryScoreBuildResult:
    trade_date: date; industry_count: int; scored_count: int; failed_count: int
    skipped_count: int; dry_run: bool; forced: bool; changed: bool
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class SymbolIndustryContext:
    symbol: str; trade_date: date; level1_code: str | None; level1_name: str | None
    level1_score: float | None; level1_rank: int | None; level1_total: int | None
    level1_confidence: str | None; level1_price_volume_state: str | None
    level2_code: str | None; level2_name: str | None; level2_score: float | None
    level2_rank: int | None; level2_total: int | None; level2_confidence: str | None
    level2_price_volume_state: str | None; level3_code: str | None; level3_name: str | None
    level3_score: float | None; level3_rank: int | None; level3_total: int | None
    level3_confidence: str | None; level3_price_volume_state: str | None
    level2_snapshot: dict | None; level3_snapshot: dict | None; industry_context_status: str
