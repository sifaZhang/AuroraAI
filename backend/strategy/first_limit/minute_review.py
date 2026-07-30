"""Pure PR6.8 minute confirmation, S0-S4 comparison, and S1 analytics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

from .daily_backtest import LOT, NOTIONAL, buy_price, d, fee, sell_price

VERSION = "first_limit_minute_review_v1"
TAIL_START = time(14, 40)
TAIL_END = time(14, 55)
TAKE_PROFIT = Decimal(".02")
STOP_RULES = ("S0", "S1", "S2", "S3", "S4")
MAX_ANALYSIS_SESSIONS = 3


@dataclass(frozen=True)
class MinuteBar:
    moment: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None = None


@dataclass(frozen=True)
class Confirmation:
    status: str
    reason: str
    confirmation_time: str | None = None
    entry_price_raw: Decimal | None = None
    entry_price: Decimal | None = None
    entry_cost: Decimal | None = None
    shares: int | None = None
    stop_distance: Decimal | None = None
    observed_count: int = 0


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


def _valid_bar(bar: MinuteBar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    return (
        bar.moment.tzinfo is not None
        and all(value is not None for value in values)
        and all(value > 0 for value in values[:4])
        and bar.volume >= 0
        and bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
    )


def _same_session_next(previous: MinuteBar, current: MinuteBar) -> bool:
    if previous.moment.date() != current.moment.date():
        return False
    seconds = int((current.moment - previous.moment).total_seconds())
    lunch = (
        previous.moment.timetz().replace(tzinfo=None) == time(11, 30)
        and current.moment.timetz().replace(tzinfo=None) in {time(13, 0), time(13, 1)}
    )
    return seconds == 60 or lunch


def confirm_tail_entry(bars, o0, upper_limit=None) -> Confirmation:
    """Return at the first confirmable minute; no later bar can revise that decision."""
    support = d(o0)
    upper = d(upper_limit)
    if support is None or support <= 0:
        return Confirmation("indeterminate", "invalid_o0")
    observed = []
    previous = None
    for bar in bars:
        minute = bar.moment.timetz().replace(tzinfo=None)
        if minute < TAIL_START:
            continue
        if minute > TAIL_END:
            break
        if not observed and minute != TAIL_START:
            return Confirmation("indeterminate", "tail_window_does_not_start_at_1440")
        if not _valid_bar(bar):
            return Confirmation("indeterminate", "invalid_minute_bar", observed_count=len(observed))
        if previous is not None and not _same_session_next(previous, bar):
            return Confirmation("indeterminate", "minute_gap_before_confirmation", observed_count=len(observed))
        observed.append(bar)
        if len(observed) >= 2:
            prior = observed[:-1]
            baseline_volume = Decimal(str(median([item.volume for item in prior])))
            stable_or_rebounding = bar.close >= previous.close and bar.close >= min(item.close for item in observed)
            no_sudden_sell = bar.close >= bar.open and (
                baseline_volume == 0 or bar.volume <= baseline_volume * Decimal(3)
            )
            one_price_upper = (
                upper is not None
                and bar.open == bar.high == bar.low == bar.close == upper
            )
            if bar.close > support and stable_or_rebounding and no_sudden_sell and not one_price_upper:
                price = buy_price(bar.close)
                shares = int(NOTIONAL / price // LOT * LOT)
                if shares <= 0:
                    return Confirmation("indeterminate", "insufficient_notional_for_lot", observed_count=len(observed))
                cost = fee(price * shares)
                return Confirmation(
                    "confirmed",
                    "first_tail_confirmation",
                    _iso(bar.moment),
                    bar.close,
                    price,
                    cost,
                    shares,
                    (price - support) / price,
                    len(observed),
                )
        previous = bar
    if not observed:
        return Confirmation("indeterminate", "missing_tail_minutes")
    if observed[-1].moment.timetz().replace(tzinfo=None) < TAIL_END:
        return Confirmation("indeterminate", "tail_window_ended_before_1455", observed_count=len(observed))
    return Confirmation("rejected", "no_tail_minute_satisfied_confirmation", observed_count=len(observed))


def _lower_locked(bar: MinuteBar, lower_limit) -> bool:
    lower = d(lower_limit)
    return lower is not None and bar.open == bar.high == bar.low == bar.close == lower


def _closed_result(
    rule,
    confirmation,
    trigger_bar,
    trigger_price,
    trigger_reason,
    fill_bar,
    bars_seen,
    *,
    ambiguous=False,
    delay_minutes=0,
):
    raw = fill_bar.open if fill_bar is not None else trigger_price
    price = sell_price(raw)
    proceeds = price * confirmation.shares
    invested = confirmation.entry_price * confirmation.shares
    exit_cost = fee(proceeds, True)
    gross = proceeds / invested - 1
    net = (proceeds - exit_cost - invested - confirmation.entry_cost) / (invested + confirmation.entry_cost)
    drawdown = min((bar.low / confirmation.entry_price - 1 for bar in bars_seen), default=Decimal(0))
    return {
        "stop_rule": rule,
        "status": "closed",
        "trigger_time": _iso(trigger_bar.moment) if trigger_bar else None,
        "trigger_price": trigger_price,
        "trigger_reason": trigger_reason,
        "exit_time": _iso((fill_bar or trigger_bar).moment),
        "exit_price_raw": raw,
        "exit_price": price,
        "exit_cost": exit_cost,
        "gross_return": gross,
        "net_return": net,
        "max_drawdown": drawdown,
        "intraday_path_ambiguous": ambiguous,
        "delay_minutes": delay_minutes,
        "audit": {"execution": "conservative_stop_first" if ambiguous else "deterministic"},
    }


def _nonterminal(rule, status, reason, trigger=None, ambiguous=False):
    return {
        "stop_rule": rule,
        "status": status,
        "trigger_time": _iso(trigger.moment) if trigger else None,
        "trigger_price": trigger.low if trigger else None,
        "trigger_reason": reason,
        "exit_time": None,
        "exit_price_raw": None,
        "exit_price": None,
        "exit_cost": None,
        "gross_return": None,
        "net_return": None,
        "max_drawdown": None,
        "intraday_path_ambiguous": ambiguous,
        "delay_minutes": 0,
        "audit": {},
    }


def _execution_failure(rule, reason, trigger, ambiguous=False):
    status = "indeterminate" if reason and reason.startswith("minute_gap") else "unresolved"
    return _nonterminal(rule, status, reason, trigger, ambiguous)


def _next_execution(
    iterator,
    signal,
    lower_limits,
    allow_next_session=False,
    expected_sessions=None,
):
    candidate = next(iterator, None)
    if candidate is None:
        return None, 0, "no_next_minute_bar"
    if not _valid_bar(candidate):
        return None, 0, "invalid_execution_minute"
    if signal.moment.date() == candidate.moment.date():
        if not _same_session_next(signal, candidate):
            return None, 0, "minute_gap_after_trigger"
    else:
        if not allow_next_session:
            return None, 0, "no_next_minute_bar"
        later = [day for day in (expected_sessions or []) if day > signal.moment.date()]
        if later and candidate.moment.date() != later[0]:
            return None, 0, "missing_execution_session_minutes"
    delay = 0
    previous = signal
    while candidate is not None:
        if candidate.moment.date() == previous.moment.date() and not _same_session_next(previous, candidate):
            return None, delay, "minute_gap_while_waiting_for_fill"
        if not _lower_locked(candidate, lower_limits.get(str(candidate.moment.date()))):
            return candidate, delay, None
        delay += 1
        previous = candidate
        candidate = next(iterator, None)
    return None, delay, "lower_limit_locked_until_data_end"


def evaluate_stop_rule(
    rule,
    bars,
    confirmation,
    o0,
    lower_limits=None,
    expected_sessions=None,
):
    if rule not in STOP_RULES:
        raise ValueError(f"unsupported stop rule: {rule}")
    if confirmation.status != "confirmed":
        return _nonterminal(rule, "indeterminate", "entry_not_confirmed")
    support = d(o0)
    lower_limits = lower_limits or {}
    iterator = iter(
        bar for bar in bars if _iso(bar.moment) > confirmation.confirmation_time
    )
    sessions = []
    profit_level = confirmation.entry_price * (1 + TAKE_PROFIT)
    stop_level = support if rule in {"S1", "S3", "S4"} else support * Decimal(".99") if rule == "S2" else None
    s4_count = 0
    previous = None
    seen = []
    for bar in iterator:
        if not _valid_bar(bar):
            return _nonterminal(rule, "indeterminate", "invalid_post_entry_minute")
        if previous and bar.moment <= previous.moment:
            return _nonterminal(rule, "indeterminate", "minute_series_not_ordered")
        if bar.moment.date() not in sessions:
            if len(sessions) >= MAX_ANALYSIS_SESSIONS:
                break
            if expected_sessions:
                previous_day = sessions[-1] if sessions else datetime.fromisoformat(
                    confirmation.confirmation_time
                ).date()
                later = [day for day in expected_sessions if day > previous_day]
                if bar.moment.date() != previous_day and later and bar.moment.date() != later[0]:
                    return _nonterminal(
                        rule, "indeterminate", "missing_trading_session_minutes"
                    )
            sessions.append(bar.moment.date())
        if previous and previous.moment.date() == bar.moment.date() and not _same_session_next(previous, bar):
            return _nonterminal(rule, "indeterminate", "minute_series_not_contiguous")
        if previous and previous.moment.timetz().replace(tzinfo=None) == time(11, 30):
            s4_count = 0
        seen.append(bar)
        first_of_session = previous is None or previous.moment.date() != bar.moment.date()
        opening_gap = rule in {"S1", "S2"} and first_of_session and bar.open < stop_level
        stop_hit = rule in {"S1", "S2"} and bar.low < stop_level
        profit_hit = bar.high >= profit_level
        if rule == "S4":
            if bar.low < support and bar.close < support:
                s4_count += 1
            elif bar.close >= support:
                s4_count = 0
            stop_hit = s4_count >= 15
        if rule == "S3":
            is_session_close = bar.moment.timetz().replace(tzinfo=None) == time(15, 0)
            stop_hit = is_session_close and bar.close < support
        ambiguous = bool(stop_hit and profit_hit)
        if opening_gap:
            if _lower_locked(bar, lower_limits.get(str(bar.moment.date()))):
                fill, delay, error = _next_execution(
                    iterator, bar, lower_limits, expected_sessions=expected_sessions
                )
                if fill is None:
                    return _execution_failure(rule, error or "opening_lower_limit_locked", bar, ambiguous)
                return _closed_result(
                    rule, confirmation, bar, bar.open, "open_gap_below_stop",
                    fill, seen, ambiguous=ambiguous, delay_minutes=delay,
                )
            return _closed_result(
                rule, confirmation, bar, bar.open, "open_gap_below_stop",
                bar, seen, ambiguous=ambiguous,
            )
        if stop_hit or profit_hit:
            reason = (
                f"{rule}_stop"
                if stop_hit
                else "take_profit_2pct"
            )
            fill, delay, error = _next_execution(
                iterator,
                bar,
                lower_limits,
                allow_next_session=rule == "S3",
                expected_sessions=expected_sessions,
            )
            if fill is None:
                return _execution_failure(rule, error, bar, ambiguous)
            return _closed_result(
                rule,
                confirmation,
                bar,
                stop_level if stop_hit else profit_level,
                reason,
                fill,
                seen,
                ambiguous=ambiguous,
                delay_minutes=delay,
            )
        if (
            len(sessions) == MAX_ANALYSIS_SESSIONS
            and bar.moment.timetz().replace(tzinfo=None) == time(15, 0)
        ):
            return _closed_result(
                rule, confirmation, bar, bar.close, "three_session_time_exit", None, seen
            )
        previous = bar
    if not seen:
        return _nonterminal(rule, "unresolved", "no_minutes_after_entry")
    if len(sessions) < MAX_ANALYSIS_SESSIONS:
        return _nonterminal(rule, "unresolved", "data_cutoff_before_three_sessions")
    final = seen[-1]
    if final.moment.timetz().replace(tzinfo=None) != time(15, 0):
        return _nonterminal(rule, "indeterminate", "third_session_missing_close")
    return _closed_result(rule, confirmation, final, final.close, "three_session_time_exit", None, seen)


def evaluate_all_stops(
    bars, confirmation, o0, lower_limits=None, expected_sessions=None
):
    return {
        rule: evaluate_stop_rule(
            rule, bars, confirmation, o0, lower_limits, expected_sessions
        )
        for rule in STOP_RULES
    }


def validate_entry_day_after_confirmation(bars, confirmation):
    """The exit path must cover every minute from confirmation through 15:00."""
    if confirmation.status != "confirmed":
        return None
    confirmed_at = datetime.fromisoformat(confirmation.confirmation_time)
    remaining = sorted(
        (
            bar for bar in bars
            if bar.moment.date() == confirmed_at.date() and bar.moment > confirmed_at
        ),
        key=lambda bar: bar.moment,
    )
    if not remaining:
        return "missing_minutes_after_entry_confirmation"
    expected = confirmed_at + timedelta(minutes=1)
    if remaining[0].moment != expected:
        return "minute_gap_after_entry_confirmation"
    previous = remaining[0]
    for bar in remaining[1:]:
        if not _same_session_next(previous, bar):
            return "minute_gap_after_entry_confirmation"
        previous = bar
    if previous.moment.timetz().replace(tzinfo=None) != time(15, 0):
        return "entry_session_ended_before_1500"
    return None


def indeterminate_stop_results(reason):
    return {
        rule: _nonterminal(rule, "indeterminate", reason)
        for rule in STOP_RULES
    }


def s1_followup_features(
    stop_results, bars, confirmation, o0, expected_sessions=None
):
    s1 = stop_results["S1"]
    if s1.get("trigger_reason") not in {"S1_stop", "open_gap_below_stop"}:
        return {
            "s1_triggered": False,
            "reclaimed_o0": None,
            "same_day_plus_2pct": None,
            "rose_within_3_sessions": None,
        }
    trigger = datetime.fromisoformat(s1["trigger_time"])
    ordered = sorted((bar for bar in bars if bar.moment > trigger), key=lambda bar: bar.moment)
    days = []
    for bar in ordered:
        if bar.moment.date() not in days:
            days.append(bar.moment.date())
    target_days = (
        [day for day in expected_sessions if day >= trigger.date()][:3]
        if expected_sessions
        else days[:3]
    )
    observed = [bar for bar in ordered if bar.moment.date() in target_days]
    same_day = [bar for bar in observed if bar.moment.date() == trigger.date()]
    same_day_complete = any(
        bar.moment.timetz().replace(tzinfo=None) == time(15, 0)
        for bar in same_day
    )
    horizon_complete = (
        len(target_days) == 3
        and any(
            bar.moment.date() == target_days[-1]
            and bar.moment.timetz().replace(tzinfo=None) == time(15, 0)
            for bar in observed
        )
    )
    reclaimed = any(bar.close >= d(o0) for bar in observed)
    plus_two = any(
        bar.high >= confirmation.entry_price * (1 + TAKE_PROFIT)
        for bar in same_day
    )
    rose = any(bar.high >= confirmation.entry_price for bar in observed)
    return {
        "s1_triggered": True,
        "reclaimed_o0": True if reclaimed else False if horizon_complete else None,
        "same_day_plus_2pct": True if plus_two else False if same_day_complete else None,
        "rose_within_3_sessions": True if rose else False if horizon_complete else None,
        "analysis_only_bar_count": len(observed),
        "same_day_coverage_complete": same_day_complete,
        "three_session_coverage_complete": horizon_complete,
    }


def s1_metrics(records):
    triggered = [record for record in records if record["followup"]["s1_triggered"]]
    closed = [record for record in triggered if record["stops"]["S1"]["status"] == "closed"]
    def ratio(key):
        values = [record["followup"][key] for record in triggered if record["followup"][key] is not None]
        return Decimal(sum(bool(value) for value in values)) / len(values) if values else None
    s1_returns = [record["stops"]["S1"]["net_return"] for record in closed]
    reductions = []
    relative = {rule: Decimal(0) for rule in ("S2", "S3", "S4")}
    for record in records:
        s0, s1 = record["stops"]["S0"], record["stops"]["S1"]
        if s0["max_drawdown"] is not None and s1["max_drawdown"] is not None:
            reductions.append(abs(s0["max_drawdown"]) - abs(s1["max_drawdown"]))
        if s1["net_return"] is not None:
            for rule in relative:
                other = record["stops"][rule]["net_return"]
                if other is not None:
                    relative[rule] += s1["net_return"] - other
    return {
        "sample_count": len(records),
        "s1_trigger_count": len(triggered),
        "s1_average_actual_loss": sum(s1_returns) / len(s1_returns) if s1_returns else None,
        "s1_reclaimed_o0_ratio": ratio("reclaimed_o0"),
        "s1_same_day_plus_2pct_ratio": ratio("same_day_plus_2pct"),
        "s1_rose_within_3_sessions_ratio": ratio("rose_within_3_sessions"),
        "s1_max_drawdown_reduction_vs_s0": max(reductions) if reductions else None,
        "s1_total_return_delta_vs_other_stops": relative,
    }


def stop_rule_metrics(records, rule):
    results = [record["stops"][rule] for record in records]
    closed = [result for result in results if result["status"] == "closed"]
    returns = [result["net_return"] for result in closed if result["net_return"] is not None]
    drawdowns = [result["max_drawdown"] for result in closed if result["max_drawdown"] is not None]
    return {
        "stop_rule": rule,
        "sample_count": len(results),
        "trigger_count": sum(
            result.get("trigger_reason") in {f"{rule}_stop", "open_gap_below_stop"}
            for result in results
        ),
        "closed_count": len(closed),
        "unresolved_count": sum(result["status"] == "unresolved" for result in results),
        "indeterminate_count": sum(result["status"] == "indeterminate" for result in results),
        "average_net_return": sum(returns) / len(returns) if returns else None,
        "total_net_return": sum(returns) if returns else None,
        "worst_max_drawdown": min(drawdowns) if drawdowns else None,
    }


def confirmation_dict(value: Confirmation):
    return asdict(value)
