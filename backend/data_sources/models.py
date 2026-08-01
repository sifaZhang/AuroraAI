"""Provider-neutral industry data models."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class IndustryNode:
    classification: str
    version: str
    industry_code: str
    industry_name: str
    industry_level: int
    parent_code: str | None
    source: str


@dataclass(frozen=True)
class IndustryMembership:
    classification: str
    version: str
    symbol: str
    security_name: str | None
    level1_code: str
    level1_name: str
    level2_code: str
    level2_name: str
    level3_code: str
    level3_name: str
    in_date: date | None
    out_date: date | None
    is_current: bool
    source: str


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    data: T
    provider: str
    requested_at: datetime
    completed_at: datetime
    row_count: int
    fallback_used: bool = False
    warnings: tuple[str, ...] = ()

    def as_fallback(self, warning: str | None = None) -> "ProviderResult[T]":
        warnings = self.warnings + ((warning,) if warning else ())
        return replace(self, fallback_used=True, warnings=warnings)


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    enabled: bool
    reachable: bool
    authenticated: bool | None
    status: str
    latency_ms: int | None
    capabilities: dict[str, str]
    error_type: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
