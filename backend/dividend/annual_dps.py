"""Normalize dividend lifecycles and aggregate formal DPS by report year."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import DividendEvent

METHOD = "effective_cash_dividend_grouped_by_end_date_v2"
STATUS_PRIORITY = {
    "\u9884\u6848": 1,
    "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7": 2,
    "\u5b9e\u65bd": 3,
    "\u5b9e\u65bd\u65b9\u6848": 3,
}
CANCELLED_STATUSES = {"\u505c\u6b62\u5b9e\u65bd", "\u53d6\u6d88", "\u5426\u51b3"}
FORMAL_MIN_PRIORITY = STATUS_PRIORITY["\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7"]


def event_key(event: DividendEvent) -> tuple[object, ...]:
    return (
        event.symbol, event.end_date, event.cash_div_tax, event.div_proc,
        event.ann_date, event.imp_ann_date, event.base_date,
        event.record_date, event.ex_date, event.pay_date,
    )


def _stage_date(event: DividendEvent):
    if STATUS_PRIORITY.get(event.div_proc or "") == 3:
        return event.imp_ann_date or event.ann_date or event.base_date or event.record_date or event.ex_date or event.pay_date
    return event.ann_date or event.base_date or event.imp_ann_date or event.record_date or event.ex_date or event.pay_date


def _candidate_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    return [
        event for event in events
        if event.end_date
        and event.end_date.year in years
        and (event.cash_div_tax or 0) > 0
        and ((event.div_proc or "") in STATUS_PRIORITY or (event.div_proc or "") in CANCELLED_STATUSES)
    ]


def _select_group(events: list[DividendEvent]) -> list[DividendEvent]:
    """Collapse lifecycle rows while preserving separate same-amount batches."""
    exact = {event_key(event): event for event in events}
    # Tushare can publish the same implemented distribution twice with a
    # different announcement date.  It is one cash event when its report
    # period, ex-rights date and per-share cash amount are identical.
    implemented: dict[tuple[object, object, float], DividendEvent] = {}
    remaining: list[DividendEvent] = []
    for event in exact.values():
        if STATUS_PRIORITY.get(event.div_proc or "") == 3 and event.ex_date is not None:
            key = (event.end_date, event.ex_date, float(event.cash_div_tax or 0))
            previous = implemented.get(key)
            if previous is None or (_stage_date(event) or event.ex_date) > (_stage_date(previous) or previous.ex_date):
                implemented[key] = event
        else:
            remaining.append(event)
    exact_events = [*remaining, *implemented.values()]
    ordered = sorted(
        exact_events,
        key=lambda event: (_stage_date(event) is None, _stage_date(event), STATUS_PRIORITY.get(event.div_proc or "", 4)),
    )
    plans: list[dict[str, object]] = []
    for event in ordered:
        status = event.div_proc or ""
        open_plan = next((plan for plan in reversed(plans) if not plan["closed"]), None)
        if status in CANCELLED_STATUSES:
            target_plan = open_plan or next((plan for plan in reversed(plans) if not plan["cancelled"]), None)
            if target_plan is None:
                plans.append({"event": event, "priority": 0, "closed": True, "cancelled": True})
            else:
                target_plan.update(event=event, priority=0, closed=True, cancelled=True)
            continue
        priority = STATUS_PRIORITY[status]
        if open_plan is None:
            plans.append({"event": event, "priority": priority, "closed": priority == 3, "cancelled": False})
            continue
        if priority >= int(open_plan["priority"]):
            open_plan.update(event=event, priority=priority)
        if priority == 3:
            open_plan["closed"] = True
    return [
        plan["event"] for plan in plans
        if not plan["cancelled"] and int(plan["priority"]) >= FORMAL_MIN_PRIORITY
    ]


def select_effective_dividend_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    groups: dict[tuple[str, object], list[DividendEvent]] = defaultdict(list)
    for event in _candidate_events(events, years):
        groups[(event.symbol, event.end_date)].append(event)
    selected: list[DividendEvent] = []
    for group in groups.values():
        selected.extend(_select_group(group))
    return selected


def valid_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    """Compatibility alias returning normalized formal dividend events."""
    return select_effective_dividend_events(events, years)


def unique_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> list[DividendEvent]:
    return select_effective_dividend_events(events, years)


def aggregate_events(events: Iterable[DividendEvent], years: tuple[int, ...]) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, int]]]:
    totals: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for event in select_effective_dividend_events(events, years):
        assert event.end_date is not None
        totals[event.symbol][event.end_date.year] += float(event.cash_div_tax or 0)
        counts[event.symbol][event.end_date.year] += 1
    return totals, counts
