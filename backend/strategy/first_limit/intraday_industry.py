"""Offline PR6.13B intraday industry estimation from cached SQLite minutes."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time
from statistics import median

VERSION = "industry_intraday_score_v1"
MIN_AS_OF = time(14, 30)
MAX_AS_OF = time(14, 55)
MIN_VALID_MEMBERS = 3
COMPLETE_COVERAGE = .8
STRONG_RISE = .03


@dataclass(frozen=True)
class IntradayIndustryEstimate:
    trade_date: date
    as_of_time: time
    data_cutoff: datetime
    score_type: str
    calculation_version: str
    industry_level: int | None
    industry_code: str | None
    industry_name: str | None
    member_count: int
    valid_member_count: int
    coverage_ratio: float
    intraday_score: float | None
    intraday_rank: int | None
    intraday_total: int | None
    confidence: str
    status: str
    turnover_estimated: bool
    warnings: tuple[str, ...]
    equal_weight_return: float | None = None
    median_return: float | None = None
    rise_ratio: float | None = None
    strong_rise_ratio: float | None = None
    limit_up_count: int | None = None
    projected_turnover: float | None = None

    def evidence(self):
        return asdict(self)


def completed_session_ratio(value: time) -> float:
    if not MIN_AS_OF <= value <= MAX_AS_OF:
        raise ValueError("as_of_time must be between 14:30 and 14:55")
    morning = 120
    afternoon = (value.hour * 60 + value.minute) - (13 * 60)
    return (morning + afternoon) / 240


def _clamp(value, low, high):
    return max(low, min(high, value))


class IntradayIndustryEstimator:
    def __init__(self, connection):
        self.connection = connection

    def estimate(self, symbol, trade_date, as_of_time, effective_context):
        day = date.fromisoformat(str(trade_date))
        cutoff_time = time.fromisoformat(str(as_of_time))
        ratio = completed_session_ratio(cutoff_time)
        cutoff = datetime.combine(day, cutoff_time)
        level = effective_context.effective_level
        code = effective_context.effective_industry_code
        name = effective_context.effective_industry_name
        if not level or not code:
            return self._empty(day, cutoff_time, cutoff, "membership_missing")
        members = [row[0] for row in self.connection.execute(
            f"SELECT symbol FROM industry_memberships_current WHERE level{level}_code=?",
            (code,),
        )]
        observations = []
        for member in members:
            latest = self.connection.execute(
                """SELECT close,amount FROM first_limit_minute_bars
                   WHERE symbol=? AND timeframe='1m' AND substr(bar_time,1,10)=?
                     AND substr(bar_time,12,5)<=? ORDER BY bar_time DESC LIMIT 1""",
                (member, str(day), cutoff_time.isoformat(timespec="minutes")),
            ).fetchone()
            previous = self.connection.execute(
                """SELECT close FROM a_share_daily_bars WHERE stock_code=?
                   AND adjustment='none' AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
                (member.split(".")[0], str(day)),
            ).fetchone()
            if latest and previous and previous[0] and latest[0]:
                amount = self.connection.execute(
                    """SELECT COALESCE(SUM(amount),0) FROM first_limit_minute_bars
                       WHERE symbol=? AND timeframe='1m' AND substr(bar_time,1,10)=?
                         AND substr(bar_time,12,5)<=?""",
                    (member, str(day), cutoff_time.isoformat(timespec="minutes")),
                ).fetchone()[0]
                observations.append(((float(latest[0]) / float(previous[0])) - 1, float(amount or 0)))
        count = len(members)
        valid = len(observations)
        coverage = valid / count if count else 0
        if valid < MIN_VALID_MEMBERS:
            return IntradayIndustryEstimate(day, cutoff_time, cutoff, "intraday_estimated", VERSION,
                level, code, name, count, valid, coverage, None, None, None, "unavailable",
                "intraday_data_insufficient", True, ("valid_members_below_3",))
        returns = [item[0] for item in observations]
        mean_return = sum(returns) / valid
        middle = median(returns)
        rise = sum(value > 0 for value in returns) / valid
        strong = sum(value >= STRONG_RISE for value in returns) / valid
        projected = sum(item[1] for item in observations) / ratio
        score = (
            _clamp(50 + mean_return * 500, 0, 100) * .30
            + _clamp(50 + middle * 500, 0, 100) * .20
            + rise * 20 + strong * 15
            + _clamp(coverage, 0, 1) * 15
        )
        status = "complete" if coverage >= COMPLETE_COVERAGE else "partial"
        confidence = "high" if coverage >= .9 else "medium" if coverage >= .8 else "low"
        return IntradayIndustryEstimate(day, cutoff_time, cutoff, "intraday_estimated", VERSION,
            level, code, name, count, valid, coverage, round(score, 4), None, None,
            confidence, status, True, ("turnover_projected_from_elapsed_trading_minutes",),
            mean_return, middle, rise, strong, sum(value >= .095 for value in returns), projected)

    def _empty(self, day, cutoff_time, cutoff, status):
        return IntradayIndustryEstimate(day, cutoff_time, cutoff, "intraday_estimated", VERSION,
            None, None, None, 0, 0, 0, None, None, None, "unavailable", status,
            False, (status,))
