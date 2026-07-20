from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.expectation_gap.database import migrate
from backend.expectation_gap.futu_client import CollectionResult
from backend.expectation_gap.refresh_jobs import (
    JobConflictError, RUNNERS, create_job, get_job, recover_interrupted_jobs,
    refresh_a_share_job, refresh_hk_prices_job, refresh_hk_ratings_job, run_job,
)


def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "jobs.db")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def add_stock(connection, code, market="HK", price=10, fair=20, analyst=15, count=4):
    exchange, symbol = code.split(".")
    connection.execute("""INSERT INTO stocks(futu_code,symbol,name,market,exchange,created_at,updated_at)
        VALUES(?,?,?,?,?,'2026-07-20','2026-07-20')""", (code, symbol, code, market, exchange))
    stock_id = connection.execute("SELECT id FROM stocks WHERE futu_code=?", (code,)).fetchone()[0]
    connection.execute("""INSERT INTO stock_expectations(stock_id,last_price,price_time,morningstar_fair_value,
        morningstar_star_rating,morningstar_data_date,morningstar_source,analyst_average_target,analyst_count,
        analyst_data_date,analyst_source,morningstar_gap_pct,analyst_gap_pct,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (stock_id, price, "2026-07-20", fair, 4, "2026-07-19", "futu_opend",
        analyst, count, "2026-07-19", "futu_opend", 100, 50, "2026-07-20"))
    connection.commit()
    return stock_id


def pending_job(connection, job_type):
    return create_job(connection, job_type)


def write_csv(path: Path, row: str):
    path.write_text("futu_code,name,morningstar_fair_value,morningstar_star_rating,analyst_average_target,analyst_count,data_date,source,note\n" + row + "\n", encoding="utf-8-sig")


def test_a_share_imports_csv_then_refreshes_price_and_keeps_empty_fields(tmp_path):
    connection = db(tmp_path); stock_id = add_stock(connection, "SH.600519", "A", price=100, fair=166, analyst=150)
    csv_path = tmp_path / "a.csv"; write_csv(csv_path, "SH.600519,贵州茅台,,5,180,8,2026-07-20,manual,test")
    job_id = pending_job(connection, "refresh_a_share")
    frame = pd.DataFrame([{"stock_code": "600519", "current_price": 120}])
    refresh_a_share_job(connection, job_id, csv_path=csv_path, price_fetcher=lambda codes, retries: frame)
    row = connection.execute("SELECT last_price,morningstar_fair_value,morningstar_star_rating,analyst_average_target FROM stock_expectations WHERE stock_id=?", (stock_id,)).fetchone()
    assert tuple(row) == (120, 166, 5, 180)
    assert get_job(connection, job_id)["status"] == "success"


def test_bad_csv_fails_job_and_preserves_old_values(tmp_path, monkeypatch):
    connection = db(tmp_path); stock_id = add_stock(connection, "SH.600519", "A", fair=166)
    csv_path = tmp_path / "bad.csv"; write_csv(csv_path, "BAD,坏数据,999,5,180,8,2026-07-20,manual,test")
    job_id = pending_job(connection, "refresh_a_share")
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{tmp_path / 'jobs.db'}")
    run_job(job_id, runner_kwargs={"csv_path": csv_path, "price_fetcher": lambda *_args, **_kw: pd.DataFrame()})
    assert get_job(connection, job_id)["status"] == "failed"
    assert connection.execute("SELECT morningstar_fair_value FROM stock_expectations WHERE stock_id=?", (stock_id,)).fetchone()[0] == 166


class PriceOnlyClient:
    morningstar_calls = analyst_calls = 0
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def batch_snapshots(self, codes, batch_size=200):
        return {code: CollectionResult("success", {"last_price": 12, "price_time": "2026-07-20"}) for code in codes}
    def morningstar(self, code): self.morningstar_calls += 1; raise AssertionError("morningstar must not be called")
    def analyst(self, code): self.analyst_calls += 1; raise AssertionError("analyst must not be called")


def test_hk_price_job_only_changes_prices(tmp_path):
    connection = db(tmp_path); stock_id = add_stock(connection, "HK.00700")
    before = tuple(connection.execute("SELECT morningstar_fair_value,morningstar_star_rating,morningstar_data_date,analyst_average_target,analyst_count,analyst_data_date FROM stock_expectations WHERE stock_id=?", (stock_id,)).fetchone())
    job_id = pending_job(connection, "refresh_hk_prices")
    refresh_hk_prices_job(connection, job_id, client_factory=PriceOnlyClient)
    after = connection.execute("SELECT last_price,morningstar_fair_value,morningstar_star_rating,morningstar_data_date,analyst_average_target,analyst_count,analyst_data_date FROM stock_expectations WHERE stock_id=?", (stock_id,)).fetchone()
    assert after[0] == 12 and tuple(after[1:]) == before
    assert PriceOnlyClient.morningstar_calls == PriceOnlyClient.analyst_calls == 0


class RatingClient:
    def __init__(self, morningstar=CollectionResult("no_data"), analyst=CollectionResult("no_data")):
        self.ms_result, self.an_result, self.ms_calls, self.an_calls = morningstar, analyst, 0, 0
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def morningstar(self, code): self.ms_calls += 1; return self.ms_result
    def analyst(self, code): self.an_calls += 1; return self.an_result


def test_hk_ratings_respects_ttl_and_skips_fresh(tmp_path):
    connection = db(tmp_path); stock_id = add_stock(connection, "HK.00700")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    connection.execute("UPDATE stock_expectations SET morningstar_next_check_at=?,analyst_next_check_at=? WHERE stock_id=?", (future, future, stock_id)); connection.commit()
    client = RatingClient(); job_id = pending_job(connection, "refresh_hk_ratings")
    refresh_hk_ratings_job(connection, job_id, client_factory=lambda: client)
    assert client.ms_calls == client.an_calls == 0
    assert get_job(connection, job_id)["skipped_count"] == 1


def test_no_data_does_not_clear_existing_ratings_and_override_survives(tmp_path):
    connection = db(tmp_path); stock_id = add_stock(connection, "HK.00700", fair=20, analyst=15)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    connection.execute("UPDATE stock_expectations SET morningstar_next_check_at=?,analyst_next_check_at=? WHERE stock_id=?", (past, past, stock_id))
    connection.execute("""INSERT INTO expectation_quality_overrides(stock_id,source,action,reason,reviewed_at,imported_at)
        VALUES(?,'analyst','exclude','manual_review','2026-07-20','2026-07-20')""", (stock_id,)); connection.commit()
    job_id = pending_job(connection, "refresh_hk_ratings")
    refresh_hk_ratings_job(connection, job_id, client_factory=RatingClient)
    row = connection.execute("SELECT morningstar_fair_value,analyst_average_target FROM stock_expectations WHERE stock_id=?", (stock_id,)).fetchone()
    assert tuple(row) == (20, 15)
    assert connection.execute("SELECT COUNT(*) FROM expectation_quality_overrides WHERE stock_id=?", (stock_id,)).fetchone()[0] == 1
    quality = connection.execute("SELECT analyst_is_rankable,analyst_quality_reasons FROM stock_expectation_quality WHERE stock_id=?", (stock_id,)).fetchone()
    assert quality[0] == 0 and "manual_review" in quality[1]


def test_duplicate_and_cross_hk_jobs_conflict(tmp_path):
    connection = db(tmp_path); first = create_job(connection, "refresh_hk_prices")
    with pytest.raises(JobConflictError): create_job(connection, "refresh_hk_prices")
    with pytest.raises(JobConflictError): create_job(connection, "refresh_hk_ratings")
    assert first > 0


def test_worker_exception_marks_job_failed(tmp_path, monkeypatch):
    connection = db(tmp_path); job_id = pending_job(connection, "refresh_a_share"); connection.close()
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{tmp_path / 'jobs.db'}")
    original = RUNNERS["refresh_a_share"]
    RUNNERS["refresh_a_share"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    try: run_job(job_id)
    finally: RUNNERS["refresh_a_share"] = original
    connection = db(tmp_path); job = get_job(connection, job_id)
    assert job["status"] == "failed" and "boom" in job["error_summary"]


def test_restart_marks_pending_and_running_failed(tmp_path):
    connection = db(tmp_path)
    connection.execute("INSERT INTO refresh_jobs(job_type,status,created_at) VALUES('refresh_a_share','pending','2026-07-20')")
    connection.execute("INSERT INTO refresh_jobs(job_type,status,created_at) VALUES('refresh_hk_prices','running','2026-07-20')"); connection.commit()
    assert recover_interrupted_jobs(connection) == 2
    rows = connection.execute("SELECT status,message FROM refresh_jobs ORDER BY id").fetchall()
    assert all(row[0] == "failed" and "服务重启" in row[1] for row in rows)


def test_frontend_polls_and_preserves_query_state():
    source = (Path(__file__).parents[1] / "frontend" / "expectation-gap.js").read_text(encoding="utf-8")
    assert "setTimeout(()=>pollJob(id),2000)" in source
    assert "await load()" in source
    assert "syncUrl()" in source and "include_anomalies" in source
    assert "/api/refresh-jobs/latest" in source


def test_refresh_api_returns_immediately_and_conflict_is_409(monkeypatch):
    from backend.api import app as app_module
    client = TestClient(app_module.app)
    monkeypatch.setattr(app_module, "start_background_job", lambda job_type: {
        "id": 99, "job_type": job_type, "status": "pending", "total": 0, "processed": 0})
    response = client.post("/api/refresh-jobs/a-share")
    assert response.status_code == 202 and response.json()["id"] == 99

    def conflict(_job_type):
        raise JobConflictError("已有刷新任务运行中")
    monkeypatch.setattr(app_module, "start_background_job", conflict)
    response = client.post("/api/refresh-jobs/hk-prices")
    assert response.status_code == 409 and "已有刷新任务" in response.json()["detail"]
