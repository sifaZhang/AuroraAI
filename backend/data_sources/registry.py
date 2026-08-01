"""Single construction point for industry providers."""

from __future__ import annotations

from .akshare import AKShareIndustryProvider
from .audit import AuditedIndustryProvider
from .contracts import IndustryDataProvider
from .errors import ProviderValidationError
from .fallback import FallbackIndustryProvider
from .settings import DataSourceSettings
from .tushare import TushareClient, TushareIndustryProvider


def _providers(settings: DataSourceSettings, *, tushare_sdk=None, akshare_sdk=None):
    return {
        "tushare": AuditedIndustryProvider(TushareIndustryProvider(
            TushareClient(
                settings.tushare_token,
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
                requests_per_minute=settings.requests_per_minute,
                sdk=tushare_sdk,
            ), enabled=settings.tushare_enabled,
        )),
        "akshare": AuditedIndustryProvider(AKShareIndustryProvider(
            akshare_sdk, enabled=settings.akshare_fallback_enabled,
        )),
    }


def build_industry_provider(
    settings: DataSourceSettings | None = None, *, provider: str = "auto",
    tushare_sdk=None, akshare_sdk=None,
) -> IndustryDataProvider:
    settings = settings or DataSourceSettings.from_env()
    available = _providers(settings, tushare_sdk=tushare_sdk, akshare_sdk=akshare_sdk)
    selected = provider.lower()
    if selected != "auto":
        if selected not in available:
            raise ProviderValidationError(f"unsupported industry provider: {provider}")
        return available[selected]
    primary_name = settings.industry_primary_provider
    if primary_name not in available:
        raise ProviderValidationError(f"unsupported primary provider: {primary_name}")
    fallbacks = [available[name] for name in settings.industry_fallback_providers
                 if name in available and name != primary_name]
    return FallbackIndustryProvider(available[primary_name], fallbacks)


def get_data_source_health(settings: DataSourceSettings | None = None) -> list:
    settings = settings or DataSourceSettings.from_env()
    providers = _providers(settings)
    return [providers[name].health_check() for name in ("tushare", "akshare")]
