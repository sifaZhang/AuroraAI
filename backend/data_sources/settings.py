"""Environment-backed settings for market data providers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from backend.env import load_env_file


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DataSourceSettings:
    tushare_token: str = field(default="", repr=False)
    tushare_enabled: bool = True
    tushare_primary: bool = True
    akshare_fallback_enabled: bool = True
    request_timeout_seconds: float = 30
    max_retries: int = 2
    requests_per_minute: int = 180
    industry_primary_provider: str = "tushare"
    industry_fallback_providers: tuple[str, ...] = ("akshare",)

    @classmethod
    def from_env(cls) -> "DataSourceSettings":
        load_env_file()
        tushare_primary = _bool("TUSHARE_PRIMARY", True)
        fallbacks = tuple(
            item.strip().lower() for item in os.getenv(
                "INDUSTRY_FALLBACK_PROVIDERS", "akshare"
            ).split(",") if item.strip()
        )
        return cls(
            tushare_token=os.getenv("TUSHARE_TOKEN", "").strip(),
            tushare_enabled=_bool("TUSHARE_ENABLED", True),
            tushare_primary=tushare_primary,
            akshare_fallback_enabled=_bool("AKSHARE_FALLBACK_ENABLED", True),
            request_timeout_seconds=float(os.getenv("DATA_SOURCE_REQUEST_TIMEOUT_SECONDS", "30")),
            max_retries=max(0, int(os.getenv("DATA_SOURCE_MAX_RETRIES", "2"))),
            requests_per_minute=max(1, int(os.getenv("DATA_SOURCE_REQUESTS_PER_MINUTE", "180"))),
            industry_primary_provider=os.getenv(
                "INDUSTRY_PRIMARY_PROVIDER", "tushare" if tushare_primary else "akshare"
            ).strip().lower(),
            industry_fallback_providers=fallbacks,
        )
