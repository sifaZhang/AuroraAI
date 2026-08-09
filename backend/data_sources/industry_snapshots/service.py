from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date
from statistics import mean, median
from typing import Sequence

from backend.strategy.first_limit.calendar import TradingCalendarService
from backend.strategy.first_limit.contracts import BoardType, DataSource, SecurityId, SecurityStatus
from backend.strategy.first_limit.rules import resolve_limit_prices, resolve_price_limit_rule

from .models import IndustryDailySnapshot, IndustrySnapshotBuildResult, IndustrySnapshotRangeResult
from .repository import IndustrySnapshotRepository

RETURN_EPSILON = 1e-9
STRONG_RISE_THRESHOLD_PCT = 3.0
MIN_STATISTICAL_SAMPLE = 3
HIGH_COVERAGE_RATIO = 0.95
PRICE_MATCH_TOLERANCE = 0.005


def _security_id(symbol: str) -> SecurityId:
    code, exchange = symbol.split(".")
    return SecurityId(code, exchange)


def _status(row: sqlite3.Row | None, symbol: str) -> SecurityStatus | None:
    if row is None:
        return None
    flags = frozenset()
    return SecurityStatus(
        _security_id(symbol), date.fromisoformat(row["effective_date"]),
        BoardType(row["board_type"]),
        None if row["is_st"] is None else bool(row["is_st"]),
        None if row["is_suspended"] is None else bool(row["is_suspended"]),
        None if row["no_price_limit"] is None else bool(row["no_price_limit"]),
        date.fromisoformat(row["listed_date"]) if row["listed_date"] else None,
        date.fromisoformat(row["delisted_date"]) if row["delisted_date"] else None,
        DataSource(row["source"]), flags,
    )


def _eligible(master: sqlite3.Row | None, status: SecurityStatus | None, target: date) -> bool:
    if master is None:
        return False
    listed = status.listed_date if status and status.listed_date else (
        date.fromisoformat(master["listed_date"]) if master["listed_date"] else None
    )
    delisted = status.delisted_date if status and status.delisted_date else (
        date.fromisoformat(master["delisted_date"]) if master["delisted_date"] else None
    )
    if listed is None or listed > target or (delisted is not None and delisted < target):
        return False
    return bool(master["is_active"]) or delisted is not None


def _load_inputs(connection: sqlite3.Connection, target: date):
    day = target.isoformat()
    nodes = connection.execute(
        """SELECT * FROM industry_nodes
           WHERE classification='SW' AND classification_version='2021'
           ORDER BY industry_level,industry_code"""
    ).fetchall()
    memberships = connection.execute(
        """SELECT * FROM industry_memberships_current
           WHERE classification='SW' AND classification_version='2021'"""
    ).fetchall()
    masters = {row["symbol"]: row for row in connection.execute(
        "SELECT * FROM a_share_security_master"
    )}
    status_rows = connection.execute(
        """SELECT * FROM (
               SELECT s.*,ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY effective_date DESC) AS rn
               FROM a_share_security_status_history s WHERE effective_date<=?
           ) WHERE rn=1""", (day,),
    ).fetchall()
    statuses = {row["symbol"]: _status(row, row["symbol"]) for row in status_rows}
    bars = {row["stock_code"]: row for row in connection.execute(
        "SELECT * FROM a_share_daily_bars WHERE trade_date=? AND adjustment='none'", (day,),
    )}
    previous_bars = {row["stock_code"]: row for row in connection.execute(
        """SELECT b.stock_code,b.close FROM a_share_daily_bars b
           JOIN (SELECT stock_code,MAX(trade_date) AS trade_date
                 FROM a_share_daily_bars WHERE trade_date<? AND adjustment='none'
                 GROUP BY stock_code) p
             ON p.stock_code=b.stock_code AND p.trade_date=b.trade_date
           WHERE b.adjustment='none'""", (day,),
    )}
    metadata = {row["symbol"]: row for row in connection.execute(
        "SELECT * FROM first_limit_daily_metadata WHERE trade_date=?", (day,),
    )}
    event_rows = connection.execute(
        """SELECT * FROM first_limit_events WHERE trade_date=?
           ORDER BY symbol,detected_at DESC,id DESC""", (day,),
    ).fetchall()
    events = {}
    for row in event_rows:
        events.setdefault(row["symbol"], row)
    return nodes, memberships, masters, statuses, bars, previous_bars, metadata, events


def _members_by_industry(memberships: Sequence[sqlite3.Row]) -> dict[tuple[int, str], set[str]]:
    result: dict[tuple[int, str], set[str]] = defaultdict(set)
    for row in memberships:
        for level in (1, 2, 3):
            result[(level, row[f"level{level}_code"])].add(row["symbol"])
    return result


def _data_status(*, eligible_count: int, valid_bar_count: int,
                 return_count: int, coverage_ratio: float) -> str:
    if eligible_count == 0 or valid_bar_count == 0:
        return "empty"
    if return_count < MIN_STATISTICAL_SAMPLE:
        return "insufficient"
    return "complete" if coverage_ratio >= HIGH_COVERAGE_RATIO else "partial"


def _snapshot(node: sqlite3.Row, symbols: set[str], target: date, masters, statuses,
              bars, previous_bars, metadata, events) -> IndustryDailySnapshot:
    eligible = [symbol for symbol in symbols if _eligible(
        masters.get(symbol), statuses.get(symbol), target,
    )]
    valid_bars = [(symbol, bars[symbol.split(".")[0]]) for symbol in eligible
                  if symbol.split(".")[0] in bars and bars[symbol.split(".")[0]]["close"] is not None]
    returns: list[float] = []
    amounts: list[float] = []
    limit_up_count = 0
    limit_down_count = 0
    for symbol, bar in valid_bars:
        if bar["amount"] is not None:
            amounts.append(float(bar["amount"]))
        meta = metadata.get(symbol)
        pre_close = meta["pre_close"] if meta and meta["pre_close"] is not None else (
            previous_bars.get(symbol.split(".")[0])["close"]
            if symbol.split(".")[0] in previous_bars else None
        )
        if pre_close is not None and float(pre_close) > 0:
            returns.append((float(bar["close"]) / float(pre_close) - 1.0) * 100.0)
        status = statuses.get(symbol)
        rule = resolve_price_limit_rule(_security_id(symbol), target, status)
        prices = resolve_limit_prices(
            pre_close, rule,
            source_upper_limit=meta["source_upper_limit"] if meta else None,
            source_lower_limit=meta["source_lower_limit"] if meta else None,
        )
        if prices.reliable and prices.upper_limit is not None:
            limit_up_count += int(abs(float(bar["close"]) - float(prices.upper_limit)) <= PRICE_MATCH_TOLERANCE)
        if prices.reliable and prices.lower_limit is not None:
            limit_down_count += int(abs(float(bar["close"]) - float(prices.lower_limit)) <= PRICE_MATCH_TOLERANCE)

    rise_count = sum(value > RETURN_EPSILON for value in returns)
    fall_count = sum(value < -RETURN_EPSILON for value in returns)
    flat_count = len(returns) - rise_count - fall_count
    strong_rise_count = sum(value >= STRONG_RISE_THRESHOLD_PCT for value in returns)
    eligible_count = len(eligible)
    valid_bar_count = len(valid_bars)
    coverage = valid_bar_count / eligible_count if eligible_count else 0.0
    first_limit_capable = bool(eligible) and all(
        symbol in events and events[symbol]["is_first_limit"] is not None for symbol in eligible
    )
    broken_limit_capable = bool(eligible) and all(
        symbol in events and events[symbol]["touched_upper_limit"] is not None
        and events[symbol]["is_limit_up_close"] is not None for symbol in eligible
    )
    first_limit_count = (sum(events[symbol]["is_first_limit"] == 1 for symbol in eligible)
                         if first_limit_capable else None)
    broken_limit_count = (sum(events[symbol]["touched_upper_limit"] == 1
                              and events[symbol]["is_limit_up_close"] == 0
                              for symbol in eligible) if broken_limit_capable else None)
    sources = sorted({bar["source"] for _, bar in valid_bars})
    source_snapshot = json.dumps({
        "bars": sources,
        "membership": "industry_memberships_current",
        "membership_scope": "current_snapshot_for_all_dates",
        "return_count": len(returns),
        "limit_prices": "first_limit_daily_metadata_or_central_rules",
        "first_limit_capable": first_limit_capable,
        "broken_limit_capable": broken_limit_capable,
    }, ensure_ascii=False, sort_keys=True)
    return IndustryDailySnapshot(
        target, node["classification"], node["classification_version"],
        node["industry_code"], node["industry_level"], len(symbols), eligible_count,
        valid_bar_count, max(0, eligible_count - valid_bar_count),
        sum(bool(statuses.get(symbol) and statuses[symbol].is_suspended) for symbol in eligible),
        coverage,
        mean(returns) if len(returns) >= MIN_STATISTICAL_SAMPLE else None,
        median(returns) if len(returns) >= MIN_STATISTICAL_SAMPLE else None,
        rise_count, fall_count, flat_count,
        rise_count / len(returns) if returns else None,
        fall_count / len(returns) if returns else None,
        strong_rise_count, strong_rise_count / len(returns) if returns else None,
        limit_up_count, limit_down_count, first_limit_count, broken_limit_count,
        sum(amounts) if amounts else None, median(amounts) if amounts else None,
        _data_status(eligible_count=eligible_count, valid_bar_count=valid_bar_count,
                     return_count=len(returns), coverage_ratio=coverage),
        source_snapshot,
    )


def build_industry_daily_snapshots(
    *, connection: sqlite3.Connection, trade_date: date,
    levels: Sequence[int] = (1, 2, 3), dry_run: bool = False,
    force: bool = False, repository: IndustrySnapshotRepository | None = None,
) -> IndustrySnapshotBuildResult:
    selected_levels = tuple(dict.fromkeys(levels))
    if not selected_levels or any(level not in (1, 2, 3) for level in selected_levels):
        raise ValueError("levels must contain 1, 2, or 3")
    if not TradingCalendarService(connection).is_trading_day(trade_date):
        return IndustrySnapshotBuildResult(trade_date, 0, 0, 0, 0, 1, 0,
                                           dry_run, force, False, ("non_trading_day",))
    nodes, memberships, masters, statuses, bars, previous_bars, metadata, events = _load_inputs(connection, trade_date)
    selected_nodes = [node for node in nodes if node["industry_level"] in selected_levels]
    if not nodes or not memberships:
        return IndustrySnapshotBuildResult(
            trade_date, len(selected_nodes), 0, 0, 1, 0, 0, dry_run, force, False,
            ("current_industry_snapshot_unavailable",),
        )
    members = _members_by_industry(memberships)
    snapshots: list[IndustryDailySnapshot] = []
    warnings: list[str] = []
    failed = 0
    for node in selected_nodes:
        try:
            snapshots.append(_snapshot(
                node, members.get((node["industry_level"], node["industry_code"]), set()),
                trade_date, masters, statuses, bars, previous_bars, metadata, events,
            ))
        except (ArithmeticError, TypeError, ValueError) as exc:
            failed += 1
            warnings.append(f"{node['industry_code']}: {type(exc).__name__}: {exc}")
    repo = repository or IndustrySnapshotRepository(connection)
    changed_count = sum(not repo.snapshot_matches(item) for item in snapshots)
    if not dry_run:
        changed_count = repo.write_snapshots(snapshots, force=force)
    partial = sum(item.data_status != "complete" for item in snapshots)
    coverages = [item.coverage_ratio for item in snapshots]
    level_counts = {
        level: sum(item.industry_level == level for item in snapshots)
        for level in selected_levels
    }
    data_status_counts = {
        status: sum(item.data_status == status for item in snapshots)
        for status in ("complete", "partial", "insufficient", "empty")
    }
    capabilities = [json.loads(item.source_snapshot) for item in snapshots]
    unique_symbols = {row["symbol"] for row in memberships}
    missing_symbols = {
        symbol for symbol in unique_symbols
        if _eligible(masters.get(symbol), statuses.get(symbol), trade_date)
        and (symbol.split(".")[0] not in bars or bars[symbol.split(".")[0]]["close"] is None)
    }
    return IndustrySnapshotBuildResult(
        trade_date, len(selected_nodes), len(snapshots) - partial, partial, failed, 0,
        len(snapshots), dry_run, force, bool(changed_count or (force and snapshots)), tuple(warnings),
        level_counts,
        data_status_counts,
        ({"minimum": min(coverages), "median": median(coverages), "maximum": max(coverages)}
         if coverages else {}),
        len(missing_symbols),
        sum(bool(item.get("first_limit_capable")) for item in capabilities),
        sum(bool(item.get("broken_limit_capable")) for item in capabilities),
    )


def build_industry_snapshot_range(
    *, connection: sqlite3.Connection, start_date: date, end_date: date,
    levels: Sequence[int] = (1, 2, 3), dry_run: bool = False, force: bool = False,
) -> IndustrySnapshotRangeResult:
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    calendar = TradingCalendarService(connection)
    open_days = {date.fromisoformat(value) for value in calendar.trading_days_between(start_date, end_date)}
    results = tuple(build_industry_daily_snapshots(
        connection=connection, trade_date=day, levels=levels, dry_run=dry_run, force=force,
    ) for day in sorted(open_days))
    all_days = {date.fromordinal(value) for value in range(start_date.toordinal(), end_date.toordinal() + 1)}
    return IndustrySnapshotRangeResult(results, tuple(sorted(all_days - open_days)))
