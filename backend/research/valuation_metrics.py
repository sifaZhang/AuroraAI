from __future__ import annotations

from math import floor
from typing import Iterable


def fcf(operating_cash_flow: float | None, capex: float | None) -> float | None:
    return None if operating_cash_flow is None or capex is None else operating_cash_flow - capex


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return None if numerator is None or denominator is None or denominator <= 0 else numerator / denominator


def percentile(values: Iterable[float | None], current: float | None) -> dict[str, object]:
    samples = sorted(float(value) for value in values if value is not None and float(value) > 0)
    if current is None or current <= 0 or not samples:
        return {"percentile": None, "sample_count": len(samples), "method": "positive_only"}
    return {"percentile": sum(value <= current for value in samples) / len(samples), "sample_count": len(samples), "method": "positive_only"}
