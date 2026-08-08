"""Convert canonical cash dividends to a later, implemented share basis.

This module deliberately leaves D2.6 lifecycle normalisation and raw annual
DPS untouched.  It only consumes canonical cash events and implemented stock
expansions from the same Tushare dividend feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .annual_dps import select_effective_dividend_events
from .models import DividendEvent

IMPLEMENTED_STATUSES = frozenset({"实施", "实施方案"})
EPSILON = 1e-12


@dataclass(frozen=True)
class ShareExpansion:
    symbol: str
    ex_date: date
    factor: float


def target_years(as_of: date, count: int = 3) -> tuple[int, ...]:
    """Return the last *count* complete fiscal years for an as-of date."""
    return tuple(range(as_of.year - count, as_of.year))


def _number(value: float | None) -> float:
    return float(value or 0.0)


def share_expansion_factor(event: DividendEvent) -> float:
    """Return the per-share expansion factor represented by one dividend row.

    Tushare exposes stock dividend, bonus issue and capitalisation ratios as
    per-existing-share values.  Only their positive sum changes the share
    basis; for example 10-for-4 is represented as 0.4 and yields 1.4.
    """
    # Tushare's `stk_div` is the reported total stock-distribution ratio and
    # duplicates its bonus/capitalisation component on real rows (for example
    # 301109.SZ reports stk_div=0.4 and stk_co_rate=0.4).  Prefer that total;
    # only fall back to the component sum when the total is absent.
    total = _number(event.stk_div)
    if total <= EPSILON:
        total = _number(event.stk_bo_rate) + _number(event.stk_co_rate)
    return 1.0 + total


def implemented_share_expansions(
    events: Iterable[DividendEvent], share_basis_as_of: date
) -> tuple[list[ShareExpansion], list[str]]:
    """Select implemented, dated stock expansions once, with diagnostics."""
    selected: dict[tuple[str, date, float], ShareExpansion] = {}
    warnings: list[str] = []
    for event in events:
        factor = share_expansion_factor(event)
        if factor <= 1.0 + EPSILON:
            continue
        if event.div_proc not in IMPLEMENTED_STATUSES:
            continue
        if event.ex_date is None:
            warnings.append(f"{event.symbol}: implemented share expansion has no ex_date")
            continue
        if event.ex_date > share_basis_as_of:
            continue
        selected[(event.symbol, event.ex_date, round(factor, 12))] = ShareExpansion(
            event.symbol, event.ex_date, factor
        )
    return sorted(selected.values(), key=lambda item: (item.symbol, item.ex_date, item.factor)), warnings


def current_basis_dps(
    events: Iterable[DividendEvent], years: tuple[int, ...], share_basis_as_of: date
) -> tuple[dict[str, dict[int, float]], list[str]]:
    """Aggregate canonical cash events after converting each event separately.

    An expansion on the cash event's own ex-date is intentionally included,
    which keeps a combined cash-plus-stock distribution on the post-ex-rights
    share basis.  Events lacking an implementation date are left unadjusted
    and surfaced as diagnostics instead of guessed.
    """
    materialised = list(events)
    canonical = select_effective_dividend_events(materialised, years)
    expansions, expansion_warnings = implemented_share_expansions(materialised, share_basis_as_of)
    by_symbol: dict[str, list[ShareExpansion]] = {}
    for expansion in expansions:
        by_symbol.setdefault(expansion.symbol, []).append(expansion)
    totals: dict[str, dict[int, float]] = {}
    warnings = list(expansion_warnings)
    for event in canonical:
        if event.end_date is None or event.end_date.year not in years:
            continue
        event_date = event.ex_date
        if event_date is None:
            warnings.append(f"{event.symbol}: canonical cash dividend has no ex_date; no basis adjustment")
            factor = 1.0
        else:
            factor = 1.0
            for expansion in by_symbol.get(event.symbol, []):
                if event_date <= expansion.ex_date <= share_basis_as_of:
                    factor *= expansion.factor
        totals.setdefault(event.symbol, {}).setdefault(event.end_date.year, 0.0)
        totals[event.symbol][event.end_date.year] += event.cash_div_tax / factor
    return totals, warnings


def current_yield_metrics(dps_by_year: dict[int, float], years: tuple[int, ...], latest_price: float | None) -> dict[str, float | str | None]:
    """Compute display-only current-yield safety-cushion metrics."""
    values = [float(dps_by_year.get(year, 0.0)) for year in years]
    if not latest_price or latest_price <= 0 or not values or any(value <= 0 for value in values):
        return {"latest_year_current_yield": None, "three_year_average_current_yield": None,
                "conservative_three_year_current_yield": None, "dividend_variation_ratio": None,
                "dividend_stability": "unavailable"}
    minimum, maximum = min(values), max(values)
    ratio = maximum / minimum
    stability = "stable" if ratio <= 1.25 else "variable" if ratio <= 1.75 else "highly_variable"
    return {
        "latest_year_current_yield": values[-1] / latest_price,
        "three_year_average_current_yield": sum(values) / len(values) / latest_price,
        "conservative_three_year_current_yield": minimum / latest_price,
        "dividend_variation_ratio": ratio,
        "dividend_stability": stability,
    }
