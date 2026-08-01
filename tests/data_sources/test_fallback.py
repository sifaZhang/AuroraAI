from datetime import datetime, timezone

import pytest

from backend.data_sources.errors import (AllProvidersFailedError, ProviderSchemaError,
                                         ProviderUnavailableError, ProviderValidationError)
from backend.data_sources.fallback import FallbackIndustryProvider
from backend.data_sources.models import ProviderResult


def result(provider):
    now = datetime.now(timezone.utc)
    return ProviderResult([], provider, now, now, 0)


class Fake:
    def __init__(self, name, value): self.name, self.value, self.calls = name, value, 0
    def list_industries(self, **_kwargs):
        self.calls += 1
        if isinstance(self.value, Exception): raise self.value
        return self.value


def test_primary_success_does_not_call_fallback():
    primary, backup = Fake("primary", result("primary")), Fake("backup", result("backup"))
    answer = FallbackIndustryProvider(primary, [backup]).list_industries(
        classification="SW", version="2021")
    assert answer.provider == "primary" and not answer.fallback_used
    assert (primary.calls, backup.calls) == (1, 0)


def test_allowed_failure_uses_and_marks_fallback():
    primary = Fake("primary", ProviderSchemaError("changed")); backup = Fake("backup", result("backup"))
    answer = FallbackIndustryProvider(primary, [backup]).list_industries(
        classification="SW", version="2021")
    assert answer.provider == "backup" and answer.fallback_used


def test_validation_bug_does_not_fallback_and_all_failures_aggregate():
    backup = Fake("backup", result("backup"))
    with pytest.raises(ProviderValidationError):
        FallbackIndustryProvider(Fake("primary", ProviderValidationError("bad argument")), [backup]).list_industries()
    assert backup.calls == 0
    with pytest.raises(AllProvidersFailedError) as caught:
        FallbackIndustryProvider(Fake("one", ProviderUnavailableError()),
                                 [Fake("two", ProviderSchemaError())]).list_industries()
    assert len(caught.value.failures) == 2
