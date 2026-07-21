from fastapi.testclient import TestClient

from backend.api import data_source_health as routes
from backend.api.app import app
from backend.sector_radar.health_repository import record_failure, record_success


def test_health_get_and_post_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    called = []

    def fake_checks(connection, selection):
        called.append(selection)
        sources = ("sw_l1", "sw_l2", "eastmoney") if selection == "all" else (selection,)
        for source in sources:
            if source == "sw_l1":
                record_success(connection, source, latency_ms=10, metadata={"sector_count": 31})
            else:
                record_failure(connection, source, error_type="UpstreamError", error_message="unavailable", latency_ms=20)
        connection.commit()
        from backend.sector_radar.health_repository import list_statuses
        return [item for item in list_statuses(connection) if selection == "all" or item["source"] == selection]

    monkeypatch.setattr(routes, "run_health_checks", fake_checks)
    client = TestClient(app)
    initial = client.get("/api/data-source-health")
    assert initial.status_code == 200
    assert len(initial.json()["items"]) == 4

    response = client.post("/api/data-source-health/check", json={"source": "all"})
    assert response.status_code == 200
    assert called == ["all"]
    assert {item["source"]: item["status"] for item in response.json()["items"]}["sw_l2"] == "unavailable"

    single = client.post("/api/data-source-health/check", json={"source": "sw_l1"})
    assert single.status_code == 200
    assert called[-1] == "sw_l1"


def test_health_api_rejects_invalid_source_without_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    client = TestClient(app)
    response = client.post("/api/data-source-health/check", json={"source": "shell command"})
    assert response.status_code == 400
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "不支持的数据源" in body["detail"]


def test_one_source_failure_does_not_make_all_500(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")

    def isolated(connection, selection):
        record_success(connection, "sw_l1", latency_ms=1, metadata={"sector_count": 31})
        record_failure(connection, "sw_l2", error_type="KeyError", error_message="data", latency_ms=2)
        connection.commit()
        from backend.sector_radar.health_repository import list_statuses
        return list_statuses(connection)

    monkeypatch.setattr(routes, "run_health_checks", isolated)
    response = TestClient(app).post("/api/data-source-health/check", json={"source": "all"})
    assert response.status_code == 200
