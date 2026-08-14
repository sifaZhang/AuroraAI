from fastapi.testclient import TestClient

from backend.api import upcoming_dividend_refresh as refresh_api
from backend.api.app import app


def test_upcoming_dividend_refresh_starts_and_reports_success(monkeypatch):
    monkeypatch.setattr(refresh_api, "_refresh_once", lambda: 3)
    client = TestClient(app)

    response = client.post("/api/upcoming-dividends/refresh")

    assert response.status_code == 202
    for _ in range(20):
        status = client.get("/api/upcoming-dividends/refresh-status").json()
        if status["status"] != "running" and status["status"] != "queued":
            break
    assert status["status"] == "success"
    assert status["row_count"] == 3
