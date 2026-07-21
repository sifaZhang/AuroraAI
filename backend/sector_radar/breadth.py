"""Pure Market Breadth calculation on standardized local daily bars."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

import pandas as pd

CALCULATION_VERSION = "breadth_v1"
MIN_TOTAL_MEMBERS = 10
MIN_CORE_VALID_MEMBERS = 10
MIN_CORE_COVERAGE = 0.60

SCORE_BANDS = {
    "above_ma20": (0.30, 0.75, 10.0),
    "advancing": (0.35, 0.70, 7.0),
    "new_high_20": (0.03, 0.25, 6.0),
    "volume_expansion": (0.20, 0.60, 7.0),
}
CORE_METRICS = tuple(SCORE_BANDS)
ALL_METRICS = (
    "above_ma5", "above_ma10", "above_ma20", "advancing",
    "new_high_20", "volume_expansion",
)
CURRENT_SNAPSHOT_WARNING = (
    "Current membership snapshot has no historical effective interval. Historical use is "
    "approximate and may introduce future-data leakage."
)


@dataclass(frozen=True)
class BreadthMetricResult:
    numerator: int
    denominator: int
    ratio: float | None
    excluded_count: int
    exclusion_reasons: Mapping[str, int]


@dataclass(frozen=True)
class BreadthComponentScore:
    metric: str
    score: float
    maximum: float


@dataclass(frozen=True)
class MarketBreadthResult:
    classification_system: str
    sector_code: str
    trade_date: str
    membership_snapshot_date: str
    metrics: Mapping[str, BreadthMetricResult]
    components: Mapping[str, BreadthComponentScore]
    total_members: int
    valid_members: int
    coverage_ratio: float
    excluded_members: Mapping[str, Mapping[str, int]]
    breadth_score: float | None
    trend_score: float | None
    total_score: float | None
    status: str
    quality_warnings: tuple[str, ...]
    is_approximate: bool
    lookahead_warning: str
    calculation_version: str = CALCULATION_VERSION


def piecewise_linear_score(ratio: float, lower: float, upper: float, maximum: float) -> float:
    if not all(math.isfinite(value) for value in (ratio, lower, upper, maximum)):
        raise ValueError("score inputs must be finite")
    if upper <= lower or maximum < 0:
        raise ValueError("invalid score band")
    if ratio <= lower:
        return 0.0
    if ratio >= upper:
        return maximum
    return maximum * (ratio - lower) / (upper - lower)


def _valid_positive(value: object) -> bool:
    try:
        number = float(value)
        return math.isfinite(number) and number > 0
    except (TypeError, ValueError):
        return False


def _valid_ohlc(frame: pd.DataFrame) -> bool:
    columns = ("open", "high", "low", "close")
    if not set(columns).issubset(frame.columns):
        return True
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        if not all(_valid_positive(value) for value in row):
            return False
        open_price, high, low, close = map(float, row)
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            return False
    return True


def _metric_value(frame: pd.DataFrame, metric: str) -> tuple[bool | None, str | None]:
    window_size = {"above_ma5": 5, "above_ma10": 10, "above_ma20": 20,
                   "new_high_20": 20, "advancing": 2}.get(metric)
    if metric == "volume_expansion":
        tail = frame["volume"].tail(6)
        if len(tail) < 6:
            return None, "insufficient_history"
        if not all(_valid_positive(value) for value in tail):
            return None, "invalid_or_zero_volume"
        return bool(float(tail.iloc[-1]) > float(tail.iloc[:-1].mean())), None

    tail = frame["close"].tail(window_size)
    if len(tail) < window_size:
        return None, "insufficient_history"
    if not all(_valid_positive(value) for value in tail):
        return None, "invalid_price"
    if not _valid_ohlc(frame.tail(window_size)):
        return None, "invalid_ohlc"
    current = float(tail.iloc[-1])
    if metric == "advancing":
        return bool(current > float(tail.iloc[-2])), None
    if metric == "new_high_20":
        return bool(current >= float(tail.max())), None
    return bool(current > float(tail.mean())), None


class MarketBreadthCalculator:
    """Calculate ratios, quality and the 30-point score without database access."""

    def calculate(
        self,
        *,
        classification_system: str,
        sector_code: str,
        trade_date: str | date,
        membership_snapshot_date: str | date,
        member_codes: list[str] | tuple[str, ...],
        histories: Mapping[str, pd.DataFrame],
        trend_score: float | None,
    ) -> MarketBreadthResult:
        target = date.fromisoformat(str(trade_date)).isoformat()
        snapshot = date.fromisoformat(str(membership_snapshot_date)).isoformat()
        codes = tuple(sorted(set(member_codes)))
        values = {metric: [] for metric in ALL_METRICS}
        reasons = {metric: {} for metric in ALL_METRICS}

        for code in codes:
            raw = histories.get(code)
            frame = self._normalize_history(raw, target)
            has_target = frame is not None and not frame.empty and frame.iloc[-1]["date"].date().isoformat() == target
            for metric in ALL_METRICS:
                if not has_target:
                    reason = "missing_target_date" if raw is not None and not raw.empty else "missing_history"
                    reasons[metric][reason] = reasons[metric].get(reason, 0) + 1
                    continue
                outcome, reason = _metric_value(frame, metric)
                if reason:
                    reasons[metric][reason] = reasons[metric].get(reason, 0) + 1
                else:
                    values[metric].append(bool(outcome))

        metrics = {}
        for metric in ALL_METRICS:
            denominator = len(values[metric])
            numerator = sum(values[metric])
            metrics[metric] = BreadthMetricResult(
                numerator=numerator,
                denominator=denominator,
                ratio=(numerator / denominator) if denominator else None,
                excluded_count=len(codes) - denominator,
                exclusion_reasons=dict(sorted(reasons[metric].items())),
            )

        core_denominators = [metrics[name].denominator for name in CORE_METRICS]
        valid_members = min(core_denominators, default=0)
        coverage = valid_members / len(codes) if codes else 0.0
        warnings = []
        if len(codes) < MIN_TOTAL_MEMBERS:
            warnings.append(f"total_members_below_{MIN_TOTAL_MEMBERS}")
        for name in CORE_METRICS:
            metric = metrics[name]
            metric_coverage = metric.denominator / len(codes) if codes else 0.0
            if metric.denominator < MIN_CORE_VALID_MEMBERS:
                warnings.append(f"{name}_valid_members_below_{MIN_CORE_VALID_MEMBERS}")
            if metric_coverage < MIN_CORE_COVERAGE:
                warnings.append(f"{name}_coverage_below_{MIN_CORE_COVERAGE:.0%}")

        sufficient = not warnings
        components = {}
        for name, (lower, upper, maximum) in SCORE_BANDS.items():
            ratio = metrics[name].ratio
            score = piecewise_linear_score(ratio, lower, upper, maximum) if ratio is not None else 0.0
            components[name] = BreadthComponentScore(name, score, maximum)
        breadth_score = sum(component.score for component in components.values()) if sufficient else None
        if trend_score is not None and not 0 <= trend_score <= 70:
            raise ValueError("trend_score must be between 0 and 70")
        total_score = trend_score + breadth_score if trend_score is not None and breadth_score is not None else None
        approximate = target < snapshot
        quality_warnings = tuple(warnings + (["current_membership_snapshot_used_for_history"] if approximate else []))
        return MarketBreadthResult(
            classification_system=classification_system, sector_code=sector_code,
            trade_date=target, membership_snapshot_date=snapshot, metrics=metrics,
            components=components, total_members=len(codes), valid_members=valid_members,
            coverage_ratio=coverage,
            excluded_members={name: metric.exclusion_reasons for name, metric in metrics.items()},
            breadth_score=breadth_score, trend_score=trend_score, total_score=total_score,
            status="success" if sufficient else "insufficient_data",
            quality_warnings=quality_warnings, is_approximate=approximate,
            lookahead_warning=CURRENT_SNAPSHOT_WARNING,
        )

    @staticmethod
    def _normalize_history(raw: pd.DataFrame | None, target: str) -> pd.DataFrame | None:
        if raw is None or raw.empty or not {"date", "close", "volume"}.issubset(raw.columns):
            return None
        optional = [column for column in ("open", "high", "low") if column in raw.columns]
        frame = raw.loc[:, ["date", "close", "volume", *optional]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"])
        frame = frame[frame["date"] <= pd.Timestamp(target)]
        return frame.sort_values("date").drop_duplicates("date", keep="last")
