from fastapi.testclient import TestClient

from backend.api import market_pulse as routes
from backend.api.app import app
from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.refresh_jobs import JobConflictError
from backend.sector_radar.health_repository import ensure_source
from backend.sector_radar.breadth import (
    BreadthComponentScore, BreadthMetricResult, MarketBreadthResult,
)
from backend.sector_radar.breadth_repository import upsert_breadth_result


def seed(db_path):
    connection = connect(db_path)
    migrate(connection)
    for source in ("sw_l1", "sw_l2", "eastmoney"):
        ensure_source(connection, source)
    rows = [
        ("sw_l1", "1", "801010", "农林牧渔", "2026-07-19", 20),
        ("sw_l1", "1", "801010", "农林牧渔", "2026-07-20", 60),
        ("sw_l1", "1", "801950", "煤炭", "2026-07-20", 70),
    ]
    for source, level, code, name, day, score in rows:
        connection.execute(
            """INSERT INTO sector_scores(source,sector_level,sector_code,sector_name,trade_date,
               trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, level, code, name, day, score, "strong", 100, 95, 90, 80, 1.2, 1, f"{day}T10:00:00+00:00"),
        )
    connection.execute("UPDATE sector_source_status SET status='healthy' WHERE source='sw_l1'")
    connection.execute("UPDATE sector_source_status SET status='unavailable',last_error_type='KeyError',last_error_message='data' WHERE source='sw_l2'")
    metrics = {
        name: BreadthMetricResult(5, 10, .5, 0, {})
        for name in ("above_ma5", "above_ma10", "above_ma20", "advancing", "new_high_20", "volume_expansion")
    }
    components = {
        "above_ma20": BreadthComponentScore("above_ma20", 4, 10),
        "advancing": BreadthComponentScore("advancing", 3, 7),
        "new_high_20": BreadthComponentScore("new_high_20", 2, 6),
        "volume_expansion": BreadthComponentScore("volume_expansion", 5, 7),
    }
    upsert_breadth_result(connection, MarketBreadthResult(
        classification_system="sw_level1", sector_code="801010", trade_date="2026-07-20",
        membership_snapshot_date="2026-07-21", metrics=metrics, components=components,
        total_members=10, valid_members=10, coverage_ratio=1, excluded_members={},
        breadth_score=14, trend_score=60, total_score=74, status="success",
        quality_warnings=("current_membership_snapshot_used_for_history",),
        is_approximate=True, lookahead_warning="approximate membership",
    ))
    connection.commit()
    connection.close()


def test_list_defaults_latest_sort_pagination_and_max_score(tmp_path, monkeypatch):
    db = tmp_path / "market.db"
    seed(db)
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{db}")
    client = TestClient(app)
    response = client.get("/api/market-pulse/sectors?page=1&page_size=1")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "sw_l1"
    assert body["trade_date"] == "2026-07-20"
    assert body["total"] == 2 and len(body["items"]) == 1
    assert body["items"][0]["sector_name"] == "煤炭"
    assert body["items"][0]["trend_max_score"] == 70
    assert body["items"][0]["relative_strength_score"] is None
    assert body["items"][0]["relative_strength_max_score"] == 15
    assert body["items"][0]["capital_flow_score"] is None
    assert body["items"][0]["composite_score"] is None
    assert body["items"][0]["score_status"] == "partial"
    assert body["items"][0]["missing_components"] == ["capital_flow", "relative_strength"]
    assert body["items"][0]["breadth_status"] == "not_calculated"

    agriculture = client.get("/api/market-pulse/sectors?sort_by=total_score").json()["items"][0]
    assert agriculture["sector_name"] == "农林牧渔"
    assert agriculture["total_score"] == 74 and agriculture["breadth_score"] == 14
    assert agriculture["breadth_metrics"]["above_ma20"] == {
        "ratio": .5, "numerator": 5, "denominator": 10,
    }
    assert agriculture["breadth_status"] == "success"
    assert agriculture["is_approximate"] is True
    assert agriculture["lookahead_warning"] == "approximate membership"

    page_two = client.get("/api/market-pulse/sectors?page=2&page_size=1").json()
    assert page_two["items"][0]["sector_name"] == "农林牧渔"
    old = client.get("/api/market-pulse/sectors?trade_date=2026-07-19").json()
    assert old["total"] == 1 and old["items"][0]["trend_score"] == 20
    assert client.get("/api/market-pulse/sectors?page_size=201").status_code == 400


def test_empty_unavailable_source_and_detail_endpoints(tmp_path, monkeypatch):
    db = tmp_path / "market.db"
    seed(db)
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{db}")
    client = TestClient(app)
    empty = client.get("/api/market-pulse/sectors?source=sw_l2")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["source_status"]["status"] == "unavailable"
    detail = client.get("/api/market-pulse/sectors/sw_l1/801950")
    assert detail.status_code == 200 and detail.json()["trend_max_score"] == 70
    assert client.get("/api/market-pulse/sectors/sw_l1/missing").status_code == 404
    breadth_detail = client.get("/api/market-pulse/sectors/sw_l1/801010?trade_date=2026-07-20").json()
    assert breadth_detail["total_score"] == 74
    assert breadth_detail["calculation_version"] == "breadth_v1"


def test_refresh_post_validation_conflict_and_status_mapping(monkeypatch):
    queued = {
        "id": 12, "job_type": "refresh_market_pulse", "source": "sw_l1", "status": "pending",
        "progress_pct": 0, "processed": 0, "total": 0, "message": "等待执行", "error_summary": None,
        "started_at": None, "finished_at": None,
    }
    monkeypatch.setattr(routes, "start_background_job", lambda job_type, source: queued)
    client = TestClient(app)
    response = client.post("/api/market-pulse/refresh", json={"source": "sw_l1"})
    assert response.status_code == 202
    assert response.json()["job_id"] == 12 and response.json()["status"] == "queued"
    assert client.post("/api/market-pulse/refresh", json={"source": "bad"}).status_code == 400

    def conflict(job_type, source):
        raise JobConflictError("already running", 99)

    monkeypatch.setattr(routes, "start_background_job", conflict)
    conflict_response = client.post("/api/market-pulse/refresh", json={"source": "all"})
    assert conflict_response.status_code == 409
    assert conflict_response.json()["detail"]["existing_job_id"] == 99
    assert "traceback" not in str(conflict_response.json()).lower()
