"""Primary-first industry provider with explicit safe fallback rules."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .contracts import IndustryDataProvider
from .errors import (
    AllProvidersFailedError, DataSourceError, ProviderAuthenticationError,
    ProviderEmptyDataError, ProviderPermissionError, ProviderRateLimitError,
    ProviderSchemaError, ProviderTimeoutError, ProviderUnavailableError,
)

FALLBACK_ERRORS = (
    ProviderUnavailableError, ProviderAuthenticationError, ProviderPermissionError,
    ProviderRateLimitError, ProviderTimeoutError, ProviderSchemaError,
    ProviderEmptyDataError,
)


class FallbackIndustryProvider:
    def __init__(self, primary: IndustryDataProvider,
                 fallbacks: Sequence[IndustryDataProvider]):
        self.primary = primary
        self.fallbacks = tuple(fallbacks)

    @property
    def name(self) -> str:
        return "auto"

    def health_check(self):
        health = [self.primary.health_check(), *(item.health_check() for item in self.fallbacks)]
        return health[0]

    def _call(self, operation: str, invoke: Callable[[IndustryDataProvider], object]):
        failures: list[tuple[str, DataSourceError]] = []
        for index, provider in enumerate((self.primary, *self.fallbacks)):
            try:
                result = invoke(provider)
                return result if index == 0 else result.as_fallback(
                    f"primary provider {self.primary.name} failed with {type(failures[0][1]).__name__}"
                )
            except FALLBACK_ERRORS as exc:
                failures.append((provider.name, exc))
            except DataSourceError:
                raise
        raise AllProvidersFailedError(operation, tuple(failures))

    def list_industries(self, **kwargs):
        return self._call("list_industries", lambda provider: provider.list_industries(**kwargs))

    def list_memberships(self, **kwargs):
        return self._call("list_memberships", lambda provider: provider.list_memberships(**kwargs))

    def get_symbol_membership(self, symbol: str, **kwargs):
        return self._call("get_symbol_membership",
                          lambda provider: provider.get_symbol_membership(symbol, **kwargs))

    def list_industry_constituents(self, industry_code: str, **kwargs):
        return self._call("list_industry_constituents",
                          lambda provider: provider.list_industry_constituents(industry_code, **kwargs))
