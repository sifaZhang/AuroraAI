"""Reusable sector trend refresh service with per-source transactions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from backend.collector.dividend_collector import get_akshare
from backend.collector.probe_sector_data import (
    SourceResult, SourceStatus, describe_source_error, load_history, load_industries, to_trend,
)
from backend.collector.collect_sector_scores import persist_results

SOURCE_ORDER = ("sw_l1", "sw_l2", "eastmoney")


@dataclass(frozen=True)
class RefreshResult:
    source_result: SourceResult
    saved_count: int


def refresh_source(
    connection, source: str, *, ak: object | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> RefreshResult:
    client = ak or get_akshare()
    started = time.monotonic()
    try:
        _, industries = load_industries(client, source)
    except Exception as exc:
        error = describe_source_error(source, exc)[:1000]
        result = SourceResult(SourceStatus(source, "unavailable", 0, 0, 0, error, round(time.monotonic() - started, 2)), ())
        persist_results(connection, [result])
        return RefreshResult(result, 0)

    trends, errors = [], []
    total = len(industries)
    for completed, industry in enumerate(industries, start=1):
        try:
            trends.append(to_trend(industry, load_history(client, industry)))
        except Exception as exc:
            errors.append(f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"[:1000])
        if progress:
            progress(completed, total, f"{industry.code} {industry.name}")
    state = "available" if not errors else ("partial" if trends else "unavailable")
    status = SourceStatus(
        source, state, total, len(trends), total - len(trends), errors[-1] if errors else None,
        round(time.monotonic() - started, 2),
    )
    result = SourceResult(status, tuple(trends))
    saved = persist_results(connection, [result])
    return RefreshResult(result, saved)


def sources_for(selection: str) -> tuple[str, ...]:
    if selection == "all":
        return SOURCE_ORDER
    if selection not in SOURCE_ORDER:
        raise ValueError(f"不支持的数据源: {selection}")
    return (selection,)
