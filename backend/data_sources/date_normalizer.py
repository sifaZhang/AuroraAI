"""Strict date normalization shared by providers."""

from __future__ import annotations

from datetime import date, datetime

from .errors import ProviderValidationError


def normalize_date(value: object, *, allow_none: bool = True) -> date | None:
    if value is None or str(value).strip() in {"", "None", "NaT", "nan"}:
        if allow_none:
            return None
        raise ProviderValidationError("date is required")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ProviderValidationError("invalid provider date") from exc
