"""Small structured audit logger with no request payloads or credentials."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

LOGGER = logging.getLogger("aurora.data_sources")


@dataclass(frozen=True)
class AuditEvent:
    operation: str
    provider: str
    fallback_used: bool
    started_at: str
    completed_at: str
    duration_ms: int
    row_count: int
    success: bool
    error_type: str | None
    validation_status: str


def emit(event: AuditEvent) -> None:
    LOGGER.info(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True))


def timestamp() -> datetime:
    return datetime.now(timezone.utc)


class AuditedIndustryProvider:
    """Transparent contract decorator that audits direct and fallback calls alike."""

    def __init__(self, provider):
        self.provider = provider

    @property
    def name(self):
        return self.provider.name

    def health_check(self):
        return self.provider.health_check()

    def _call(self, operation, invoke):
        started = timestamp()
        try:
            result = invoke()
        except Exception as exc:
            completed = timestamp()
            emit(AuditEvent(
                operation, self.name, False, started.isoformat(), completed.isoformat(),
                round((completed-started).total_seconds()*1000), 0, False,
                type(exc).__name__, "failed",
            ))
            raise
        completed = timestamp()
        emit(AuditEvent(
            operation, self.name, result.fallback_used, started.isoformat(), completed.isoformat(),
            round((completed-started).total_seconds()*1000), result.row_count, True,
            None, "passed",
        ))
        return result

    def list_industries(self, **kwargs):
        return self._call("list_industries", lambda: self.provider.list_industries(**kwargs))

    def list_memberships(self, **kwargs):
        return self._call("list_memberships", lambda: self.provider.list_memberships(**kwargs))

    def get_symbol_membership(self, symbol, **kwargs):
        return self._call("get_symbol_membership",
                          lambda: self.provider.get_symbol_membership(symbol, **kwargs))

    def list_industry_constituents(self, industry_code, **kwargs):
        return self._call("list_industry_constituents",
                          lambda: self.provider.list_industry_constituents(industry_code, **kwargs))

    def list_calendar_days(self, **kwargs):
        return self._call("list_calendar_days", lambda: self.provider.list_calendar_days(**kwargs))
