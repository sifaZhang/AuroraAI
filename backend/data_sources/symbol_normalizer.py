"""Conversion helpers around AuroraAI's existing canonical symbol rules."""

from __future__ import annotations

from backend.strategy.first_limit.rules import normalize_symbol as _normalize


def normalize_symbol(value: object) -> str:
    """Return AuroraAI canonical form (for example ``600519.SH``)."""
    return _normalize(value).canonical


def to_tushare_symbol(value: object) -> str:
    return normalize_symbol(value)


def to_gm_symbol(value: object) -> str:
    return _normalize(value).gm_symbol
