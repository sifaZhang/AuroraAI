import pytest

from backend.data_sources.akshare import AKShareIndustryProvider
from backend.data_sources.audit import AuditedIndustryProvider
from backend.data_sources.errors import ProviderValidationError
from backend.data_sources.fallback import FallbackIndustryProvider
from backend.data_sources.registry import build_industry_provider
from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareIndustryProvider


def settings(**overrides):
    values = dict(tushare_token="fixture", tushare_enabled=True,
                  akshare_fallback_enabled=True, industry_primary_provider="tushare",
                  industry_fallback_providers=("akshare",))
    values.update(overrides)
    return DataSourceSettings(**values)


def test_registry_builds_tushare_primary_with_akshare_fallback():
    provider = build_industry_provider(settings())
    assert isinstance(provider, FallbackIndustryProvider)
    assert isinstance(provider.primary, AuditedIndustryProvider)
    assert isinstance(provider.primary.provider, TushareIndustryProvider)
    assert isinstance(provider.fallbacks[0].provider, AKShareIndustryProvider)


def test_registry_supports_explicit_provider_and_rejects_unknown():
    explicit = build_industry_provider(settings(), provider="akshare")
    assert isinstance(explicit, AuditedIndustryProvider)
    assert isinstance(explicit.provider, AKShareIndustryProvider)
    with pytest.raises(ProviderValidationError):
        build_industry_provider(settings(), provider="unknown")


def test_settings_read_switches_without_exposing_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setenv("TUSHARE_ENABLED", "false")
    monkeypatch.setenv("INDUSTRY_FALLBACK_PROVIDERS", "akshare")
    loaded = DataSourceSettings.from_env()
    assert loaded.tushare_token == "secret" and not loaded.tushare_enabled
    assert "secret" not in repr(loaded)
