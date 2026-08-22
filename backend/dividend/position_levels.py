"""Manual dividend-yield position levels for the formal dividend universe."""
from __future__ import annotations


VALID_GRADES = {"S", "A", "B"}


def validate_position_levels(
    grade: str | None, entry_yield: float | None, add_yield: float | None,
    heavy_yield: float | None,
) -> None:
    if grade is not None and grade not in VALID_GRADES:
        raise ValueError("等级仅支持 S、A 或 B")
    for name, value in (("建仓", entry_yield), ("加仓", add_yield), ("重仓", heavy_yield)):
        if value is not None and value < 0:
            raise ValueError(f"{name}股息率不能小于 0")
    if entry_yield is not None and add_yield is not None and entry_yield > add_yield:
        raise ValueError("建仓股息率不能高于加仓股息率")
    if add_yield is not None and heavy_yield is not None and add_yield > heavy_yield:
        raise ValueError("加仓股息率不能高于重仓股息率")


def position_status(
    current_yield: float | None, entry_yield: float | None,
    add_yield: float | None, heavy_yield: float | None,
) -> str:
    """Return the highest configured position state reached by current yield."""
    if current_yield is None:
        return "watch"
    if heavy_yield is not None and current_yield >= heavy_yield:
        return "heavy"
    if add_yield is not None and current_yield >= add_yield:
        return "add"
    if entry_yield is not None and current_yield >= entry_yield:
        return "entry"
    return "watch"
