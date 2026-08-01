import pandas as pd
import pytest
import time

from backend.data_sources.errors import (ProviderAuthenticationError, ProviderPermissionError,
                                         ProviderSchemaError, ProviderTimeoutError)
from backend.data_sources.tushare.client import TushareClient
from backend.data_sources.tushare.industry_provider import build_nodes, build_nodes_from_frame, map_memberships


def frame(**overrides):
    row = dict(l1_code="801010", l1_name="一级", l2_code="801011", l2_name="二级",
               l3_code="850111", l3_name="三级", ts_code="600519.SH", name="贵州茅台",
               in_date="20210101", out_date=None, is_new="Y")
    row.update(overrides)
    return pd.DataFrame([row])


def test_tushare_mapping_returns_domain_models_not_dataframe():
    rows = map_memberships(frame())
    assert isinstance(rows, list) and rows[0].symbol == "600519.SH"
    nodes = build_nodes(rows, enforce_count_ranges=False)
    assert [(x.industry_level, x.parent_code) for x in nodes] == [
        (1, None), (2, "801010"), (3, "801011")]


def test_tushare_missing_fields_raise_schema_error():
    with pytest.raises(ProviderSchemaError):
        map_memberships(frame().drop(columns=["l3_name"]))


def test_tushare_non_equity_pseudo_members_are_excluded_with_warning():
    warnings = []
    rows = map_memberships(pd.concat([frame(), frame(ts_code="T00018.SH")]), warnings=warnings)
    assert [row.symbol for row in rows] == ["600519.SH"]
    assert warnings == ["excluded non-equity Tushare member: T00018.SH"]


def test_catalog_can_be_built_without_hiding_membership_conflicts(monkeypatch):
    conflicting = pd.concat([frame(), frame(l3_code="850112", l3_name="另一个三级",
                                             in_date="20260701")])
    monkeypatch.setattr("backend.data_sources.tushare.industry_provider.validate_industry_nodes",
                        lambda rows: rows)
    assert {node.industry_code for node in build_nodes_from_frame(conflicting)} >= {"850111", "850112"}
    from backend.data_sources.errors import ProviderValidationError
    with pytest.raises(ProviderValidationError, match="multiple current"):
        map_memberships(conflicting)


class Pro:
    def denied(self, **_kwargs):
        raise RuntimeError("没有权限 token=super-secret")


class SDK:
    def set_token(self, token): self.token = token
    def pro_api(self): return Pro()


def test_client_classifies_permission_and_redacts_token():
    client = TushareClient("super-secret", sdk=SDK(), max_retries=0, sleeper=lambda _: None)
    with pytest.raises(ProviderPermissionError) as caught:
        client.call("denied")
    assert "super-secret" not in str(caught.value)


def test_missing_token_is_authentication_error():
    with pytest.raises(ProviderAuthenticationError):
        TushareClient("", sdk=SDK()).call("anything")


class SlowPro:
    def slow(self):
        time.sleep(.1)


class SlowSDK(SDK):
    def pro_api(self): return SlowPro()


def test_client_enforces_timeout_without_blocking_caller():
    client = TushareClient("secret", sdk=SlowSDK(), timeout_seconds=.01,
                           max_retries=0, sleeper=lambda _: None)
    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError):
        client.call("slow")
    assert time.monotonic() - started < .08


class FlakyPro:
    def __init__(self): self.calls = 0
    def fetch(self):
        self.calls += 1
        if self.calls == 1: raise TimeoutError("temporary")
        return "ok"


class FlakySDK(SDK):
    def __init__(self): self.pro = FlakyPro()
    def pro_api(self): return self.pro


def test_retryable_timeout_is_retried_a_bounded_number_of_times():
    sdk = FlakySDK()
    client = TushareClient("secret", sdk=sdk, max_retries=1,
                           requests_per_minute=100000, sleeper=lambda _: None)
    assert client.call("fetch") == "ok"
    assert sdk.pro.calls == 2
