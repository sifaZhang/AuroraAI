from fastapi.testclient import TestClient

from backend.api import market_pulse as routes
from backend.api.app import app
from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.refresh_jobs import JobConflictError
from backend.sector_radar.health_repository import ensure_source


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
    breadth_values = (
        "sw_level1", "801950", "2026-07-20", "2026-07-22",
        .60, 60, 100, .55, 55, 100, .50, 50, 100,
        .52, 52, 100, .10, 10, 100, .70, 70, 100,
        100, 100, 1.0, '{"above_ma20": {}}', 5, 3, 1, 7,
        16, 70, 86, "success", '[]', 1, "current snapshot warning",
        "breadth_v1", "2026-07-20T10:00:00+00:00", "2026-07-20T10:00:00+00:00",
    )
    connection.execute(
        """INSERT INTO sector_breadth_scores(
        classification_system,sector_code,trade_date,membership_snapshot_date,
        above_ma5_ratio,above_ma5_numerator,above_ma5_valid_count,
        above_ma10_ratio,above_ma10_numerator,above_ma10_valid_count,
        above_ma20_ratio,above_ma20_numerator,above_ma20_valid_count,
        advancing_ratio,advancing_numerator,advancing_valid_count,
        new_high_20_ratio,new_high_20_numerator,new_high_20_valid_count,
        volume_expansion_ratio,volume_expansion_numerator,volume_expansion_valid_count,
        total_members,valid_members,coverage_ratio,excluded_members,
        ma20_score,advancing_score,new_high_20_score,volume_expansion_score,
        breadth_score,trend_score,total_score,status,quality_warnings,is_approximate,
        lookahead_warning,calculation_version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        breadth_values,
    )
    connection.execute(
        """INSERT INTO sector_breadth_scores(
        classification_system,sector_code,trade_date,membership_snapshot_date,
        total_members,valid_members,coverage_ratio,excluded_members,breadth_score,trend_score,
        total_score,status,quality_warnings,is_approximate,lookahead_warning,
        calculation_version,created_at,updated_at)
        VALUES('sw_level1','801010','2026-07-21','2026-07-22',100,100,1,'{}',30,60,
               90,'success','[]',1,'future','breadth_v1',?,?)""",
        ("2026-07-21T10:00:00+00:00", "2026-07-21T10:00:00+00:00"),
    )
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
    assert body["items"][0]["total_score"] == 86
    assert body["items"][0]["breadth_score"] == 16
    assert body["items"][0]["breadth_status"] == "success"
    assert body["items"][0]["above_ma20_numerator"] == 50
    assert body["items"][0]["above_ma20_valid_count"] == 100
    assert body["items"][0]["coverage_ratio"] == 1.0
    assert body["items"][0]["is_approximate"] is True
    assert body["items"][0]["calculation_version"] == "breadth_v1"
    assert body["items"][0]["relative_strength_score"] is None
    assert body["items"][0]["relative_strength_max_score"] == 15
    assert body["items"][0]["capital_flow_score"] is None
    assert body["items"][0]["composite_score"] is None
    assert body["items"][0]["score_status"] == "partial"
    assert body["items"][0]["missing_components"] == ["capital_flow", "relative_strength"]

    page_two = client.get("/api/market-pulse/sectors?page=2&page_size=1").json()
    assert page_two["items"][0]["breadth_status"] == "not_calculated"
    assert page_two["items"][0]["breadth_score"] is None
    assert page_two["items"][0]["total_score"] is None
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
    assert detail.json()["lookahead_warning"] == "current snapshot warning"
    assert detail.json()["excluded_members"] == {"above_ma20": {}}
    assert client.get("/api/market-pulse/sectors/sw_l1/missing").status_code == 404


def test_breadth_sorting_and_exact_versioned_date_join(tmp_path, monkeypatch):
    db = tmp_path / "market.db"
    seed(db)
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{db}")
    client = TestClient(app)
    by_total = client.get("/api/market-pulse/sectors?sort_by=total_score&order=desc").json()
    assert by_total["items"][0]["sector_code"] == "801950"
    for sort_by in ("trend_score", "breadth_score", "sector_name"):
        assert client.get(f"/api/market-pulse/sectors?sort_by={sort_by}&order=asc").status_code == 200
    old = client.get("/api/market-pulse/sectors?trade_date=2026-07-19").json()["items"][0]
    assert old["breadth_status"] == "not_calculated" and old["total_score"] is None
    current = next(item for item in by_total["items"] if item["sector_code"] == "801010")
    assert current["trade_date"] == "2026-07-20"
    assert current["breadth_status"] == "not_calculated"


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
