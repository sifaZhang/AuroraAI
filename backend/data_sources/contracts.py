"""Protocols used by business services instead of third-party SDKs."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import IndustryMembership, IndustryNode, ProviderHealth, ProviderResult


class IndustryDataProvider(Protocol):
    @property
    def name(self) -> str: ...

    def health_check(self) -> ProviderHealth: ...

    def list_industries(
        self, *, classification: str, version: str, level: int | None = None,
    ) -> ProviderResult[list[IndustryNode]]: ...

    def list_memberships(
        self, *, classification: str, version: str,
        as_of_date: date | None = None, current_only: bool = True,
    ) -> ProviderResult[list[IndustryMembership]]: ...

    def get_symbol_membership(
        self, symbol: str, *, classification: str, version: str,
        as_of_date: date | None = None,
    ) -> ProviderResult[IndustryMembership | None]: ...

    def list_industry_constituents(
        self, industry_code: str, *, as_of_date: date | None = None,
    ) -> ProviderResult[list[IndustryMembership]]: ...
