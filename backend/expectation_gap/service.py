from __future__ import annotations

import math
from typing import Any


def positive_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def calculate_gap_pct(target_price: Any, current_price: Any) -> float | None:
    target = positive_number(target_price)
    current = positive_number(current_price)
    if target is None or current is None:
        return None
    return (target / current - 1.0) * 100.0
