"""Shared report-period aggregation for implemented cash dividends."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import DividendEvent

METHOD = "implemented_cash_dividend_grouped_by_end_date"
IMPLEMENTED = {"实施", "实施方案"}


def event_key(event: DividendEvent) -> tuple[object, ...]:
    return (event.symbol, event.end_date, event.ann_date, event.ex_date, event.cash_div_tax, event.div_proc)


def valid_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    return [event for event in events if event.end_date and event.end_date.year in years and (event.cash_div_tax or 0) > 0 and (event.div_proc or "实施") in IMPLEMENTED]


def unique_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    rows: dict[tuple[object, ...], DividendEvent] = {}
    for event in valid_events(events, years):
        rows[event_key(event)] = event
    return list(rows.values())


def aggregate_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, int]]]:
    totals: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for event in unique_events(events, years):
        assert event.end_date is not None
        totals[event.symbol][event.end_date.year] += float(event.cash_div_tax or 0)
        counts[event.symbol][event.end_date.year] += 1
    return totals, counts
