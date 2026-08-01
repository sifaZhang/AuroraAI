import pandas as pd
import pytest

from backend.data_sources.akshare.industry_provider import AKShareIndustryProvider
from backend.data_sources.errors import ProviderSchemaError


class BrokenAK:
    def sw_index_first_info(self):
        return pd.DataFrame([{"行业代码": "801000.SI", "行业名称": "一级"}])
    def sw_index_second_info(self):
        return pd.DataFrame([{"行业代码": "801010.SI", "行业名称": "二级", "上级行业": "一级"}])
    def sw_index_third_info(self):
        return pd.DataFrame([{"行业代码": "850111.SI", "行业名称": "三级", "上级行业": "二级"}])
    def sw_index_third_cons(self, **_kwargs):
        raise ValueError("Length mismatch: 18 vs 17")


def test_changed_constituent_schema_is_not_reported_as_empty_success(monkeypatch):
    provider = AKShareIndustryProvider(BrokenAK())
    monkeypatch.setattr("backend.data_sources.akshare.industry_provider.validate_industry_nodes",
                        lambda rows: rows)
    with pytest.raises(ProviderSchemaError, match="schema changed"):
        provider.list_industry_constituents("850111")


def test_health_marks_catalog_usable_but_constituents_degraded(monkeypatch):
    provider = AKShareIndustryProvider(BrokenAK())
    monkeypatch.setattr("backend.data_sources.akshare.industry_provider.validate_industry_nodes",
                        lambda rows: rows)
    health = provider.health_check()
    assert health.status == "degraded"
    assert health.capabilities["industry_catalog"] == "healthy"
    assert health.capabilities["industry_third_constituents"] == "degraded"
