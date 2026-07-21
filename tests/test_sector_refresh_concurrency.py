import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.collector.probe_sector_data import Industry, SectorTrend
from backend.sector_radar import service


def trend(industry):
    return SectorTrend(
        source=industry.source, sector_code=industry.code, sector_name=industry.name,
        sector_level=industry.level, trade_date="2026-07-20", trend_score=50,
        trend_level="bullish", close=10, ma5=9, ma10=8, ma20=7,
        volume_ratio=1.2, is_20d_high=False,
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, 4), ("1", 1), ("2", 2), ("4", 4), ("8", 8),
     ("0", 4), ("-1", 4), ("invalid", 4), ("9", 4)],
)
def test_sw_worker_count_is_configurable_and_bounded(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("MARKET_PULSE_SW_WORKERS", raising=False)
    else:
        monkeypatch.setenv("MARKET_PULSE_SW_WORKERS", configured)
    assert service.get_sw_worker_count() == expected


def run_refresh(monkeypatch, source="sw_l1", workers="4", failures=()):
    industries = [Industry(source, f"80{index:04d}", f"industry-{index}", 1) for index in range(6)]
    counters = {"active": 0, "max_active": 0, "history": 0, "list": 0,
                "benchmark": 0, "persist": 0}
    lock = threading.Lock()
    worker_threads = set()
    progress = []
    main_thread = threading.get_ident()

    def load_industries(client, selected):
        counters["list"] += 1
        return pd.DataFrame(), industries

    def load_history(client, industry):
        with lock:
            counters["active"] += 1
            counters["history"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
            worker_threads.add(threading.get_ident())
        try:
            time.sleep(0.02)
            if industry.code in failures:
                raise ConnectionError("history unavailable")
            return pd.DataFrame()
        finally:
            with lock:
                counters["active"] -= 1

    def load_benchmark(client):
        counters["benchmark"] += 1
        return SimpleNamespace(
            bars=pd.DataFrame(), code="000300", elapsed_seconds=0,
            source="csindex", row_count=21, latest_trade_date="2026-07-20",
        )

    def persist(connection, results):
        assert threading.get_ident() == main_thread
        counters["persist"] += 1
        return len(results[0].trends)

    monkeypatch.setenv("MARKET_PULSE_SW_WORKERS", workers)
    monkeypatch.setattr(service, "load_industries", load_industries)
    monkeypatch.setattr(service, "load_history", load_history)
    monkeypatch.setattr(service, "to_trend", lambda industry, bars: trend(industry))
    monkeypatch.setattr(service, "load_csi300_benchmark", load_benchmark)
    monkeypatch.setattr(service, "calculate_relative_strength", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("rs failed")))
    monkeypatch.setattr(service, "record_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "record_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "persist_results", persist)
    result = service.refresh_source(object(), source, ak=object(), progress=lambda *args: progress.append(args))
    return result, industries, counters, worker_threads, progress, main_thread


def test_sw_l1_four_workers_are_bounded_and_results_are_stable(monkeypatch):
    result, industries, counters, threads, progress, main = run_refresh(monkeypatch)
    assert 2 <= counters["max_active"] <= 4
    assert counters == {**counters, "history": 6, "list": 1, "benchmark": 1, "persist": 1}
    assert [item.sector_code for item in result.source_result.trends] == [item.code for item in industries]
    assert len(result.relative_strength_failures) == len(industries)
    assert len(progress) == len(industries) and progress[-1][0] == len(industries)
    assert threads != {main}
    assert result.source_result.status.status == "available"


def test_worker_one_is_serial(monkeypatch):
    result, industries, counters, _, progress, _ = run_refresh(monkeypatch, workers="1")
    assert counters["max_active"] == 1
    assert len(result.source_result.trends) == len(industries)
    assert len(progress) == len(industries)


@pytest.mark.parametrize("source", ["sw_l2", "eastmoney"])
def test_other_sources_remain_serial(monkeypatch, source):
    result, industries, counters, threads, _, main = run_refresh(monkeypatch, source=source)
    assert counters["max_active"] == 1
    assert threads == {main}
    assert counters["benchmark"] == 0
    assert len(result.source_result.trends) == len(industries)


def test_industry_failures_are_isolated_and_status_is_deterministic(monkeypatch):
    failed = {"800001", "800004"}
    result, industries, counters, _, _, _ = run_refresh(monkeypatch, failures=failed)
    status = result.source_result.status
    assert status.status == "partial" and status.failed_sector_count == 2
    assert [item.sector_code for item in result.source_result.trends] == [
        item.code for item in industries if item.code not in failed
    ]
    assert counters["history"] == len(industries)
    assert "800004 industry-4: ConnectionError" in status.last_error


def test_all_industry_failures_are_unavailable(monkeypatch):
    failures = {f"80{index:04d}" for index in range(6)}
    result, _, _, _, _, _ = run_refresh(monkeypatch, failures=failures)
    assert result.source_result.status.status == "unavailable"
    assert result.saved_count == 0
