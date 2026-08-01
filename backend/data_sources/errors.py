"""Stable exceptions exposed by the unified market-data layer."""

from __future__ import annotations


class DataSourceError(Exception):
    """Base class for expected provider and data-contract failures."""


class ProviderUnavailableError(DataSourceError):
    pass


class ProviderAuthenticationError(DataSourceError):
    pass


class ProviderPermissionError(DataSourceError):
    pass


class ProviderRateLimitError(DataSourceError):
    pass


class ProviderTimeoutError(DataSourceError):
    pass


class ProviderSchemaError(DataSourceError):
    pass


class ProviderValidationError(DataSourceError):
    pass


class ProviderEmptyDataError(DataSourceError):
    pass


class AllProvidersFailedError(DataSourceError):
    def __init__(self, operation: str, failures: tuple[tuple[str, DataSourceError], ...]):
        self.operation = operation
        self.failures = failures
        summary = "; ".join(f"{name}: {type(error).__name__}" for name, error in failures)
        super().__init__(f"all providers failed for {operation}: {summary}")
