import logging
from datetime import datetime, timezone

import pytest

from backend.data_sources.audit import AuditedIndustryProvider
from backend.data_sources.errors import ProviderUnavailableError
from backend.data_sources.models import ProviderResult


class Provider:
    name = "fixture"
    def __init__(self, failure=None): self.failure = failure
    def list_industries(self, **_kwargs):
        if self.failure: raise self.failure
        now = datetime.now(timezone.utc)
        return ProviderResult([], self.name, now, now, 0)


def test_structured_audit_records_success_and_failure_without_payload(caplog):
    caplog.set_level(logging.INFO, logger="aurora.data_sources")
    AuditedIndustryProvider(Provider()).list_industries(secret="must-not-appear")
    with pytest.raises(ProviderUnavailableError):
        AuditedIndustryProvider(Provider(ProviderUnavailableError("also-secret"))).list_industries()
    text = caplog.text
    assert '"operation": "list_industries"' in text
    assert '"success": true' in text and '"success": false' in text
    assert "must-not-appear" not in text and "also-secret" not in text
