from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from backend.data_sources.errors import ProviderUnavailableError
from backend.research.forward_consensus_collector import (
    FIELDS, ForwardConsensusCollector, _CACHE, compare_ai_to_consensus, consensus_quality,
)


AS_OF = date(2026, 8, 23)


class FakeClient:
    def __init__(self, frame=None, error=None): self.frame, self.error, self.calls = frame, error, []
    def call(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        if self.error: raise self.error
        return self.frame.copy()


def rows():
    return pd.DataFrame([
        {"ts_code":"000858.SZ","report_date":"20260820","report_title":"A2","org_name":"A","quarter":"2026Q4","op_rt":800000,"np":300000,"eps":5,"report_type":"","classify":"","author_name":"","rating":"","create_time":""},
        {"ts_code":"000858.SZ","report_date":"20260620","report_title":"A1","org_name":"A","quarter":"2026Q4","op_rt":700000,"np":250000,"eps":4,"report_type":"","classify":"","author_name":"","rating":"","create_time":""},
        {"ts_code":"000858.SZ","report_date":"20260810","report_title":"B","org_name":"B","quarter":"2026Q4","op_rt":1000000,"np":500000,"eps":7,"report_type":"","classify":"","author_name":"","rating":"","create_time":""},
        {"ts_code":"000858.SZ","report_date":"20260901","report_title":"future","org_name":"C","quarter":"2026Q4","op_rt":1,"np":1,"eps":1,"report_type":"","classify":"","author_name":"","rating":"","create_time":""},
        {"ts_code":"000858.SZ","report_date":"bad","report_title":"bad","org_name":"D","quarter":"2026Q4","op_rt":1,"np":1,"eps":1,"report_type":"","classify":"","author_name":"","rating":"","create_time":""},
    ])


def test_point_in_time_dedupe_statistics_units_revision_and_fetch_contract(tmp_path):
    _CACHE.clear(); client=FakeClient(rows())
    result=ForwardConsensusCollector(client=client,research_root=tmp_path,
        now=lambda:datetime(2026,8,23,tzinfo=timezone.utc)).collect("000858.SZ","v1",as_of_date=AS_OF)
    assert result.status == "AVAILABLE" and result.forecast_years["2026"].freshness_window_days == 90
    metric=result.forecast_years["2026"].net_profit
    assert (metric.unit,metric.median,metric.mean,metric.min,metric.max,metric.institution_count)==("CNY",4_000_000_000,4_000_000_000,3_000_000_000,5_000_000_000,2)
    assert result.forecast_years["2026"].revision.matched_institution_count == 1
    assert result.source_records.future_record_count_excluded == 1
    assert result.source_records.malformed_record_count_excluded == 1
    assert client.calls[0][0] == "report_rc" and client.calls[0][1]["fields"] == FIELDS


def test_180_day_fallback_and_quality_thresholds(tmp_path):
    frame=rows().iloc[:2].copy(); frame.loc[1,"org_name"]="B"; frame.loc[0,"report_date"]="20260520"
    _CACHE.clear(); result=ForwardConsensusCollector(client=FakeClient(frame),research_root=tmp_path).collect("X","v",as_of_date=AS_OF)
    assert result.forecast_years["2026"].freshness_window_days == 180
    assert result.forecast_years["2026"].quality == "LOW"
    assert [consensus_quality(n) for n in (10,5,2,1)] == ["HIGH","MEDIUM","LOW","INSUFFICIENT"]


def test_source_failure_and_schema_drift_are_safe(tmp_path):
    _CACHE.clear(); failure=ForwardConsensusCollector(client=FakeClient(error=ProviderUnavailableError("no access")),research_root=tmp_path).collect("X","v",as_of_date=AS_OF)
    assert failure.status == "UNAVAILABLE" and not failure.forecast_years
    _CACHE.clear(); drift=ForwardConsensusCollector(client=FakeClient(pd.DataFrame([{"ts_code":"X"}])),research_root=tmp_path).collect("X","v",as_of_date=AS_OF)
    assert drift.status == "SOURCE_ERROR"


def test_ai_comparison_categories_and_units():
    consensus={"status":"AVAILABLE","forecast_years":{"2026":{"net_profit":{"median":10_000_000_000}}}}
    base={"normalized_earnings":{"period":"FY2026E","unit":"CNY_100M","base":100}}
    assert compare_ai_to_consensus(base,consensus)["agreement"] == "ALIGNED"
    base["normalized_earnings"]["base"]=120; assert compare_ai_to_consensus(base,consensus)["agreement"] == "MODERATE_GAP"
    base["normalized_earnings"]["base"]=140; assert compare_ai_to_consensus(base,consensus)["agreement"] == "LARGE_GAP"
    base["normalized_earnings"]["unit"]="CNY"; assert compare_ai_to_consensus(base,consensus)["agreement"] == "NOT_COMPARABLE"
