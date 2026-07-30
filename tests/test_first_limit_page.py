from fastapi.testclient import TestClient

from backend.api.app import app
from backend.expectation_gap.database import connect, migrate


def test_first_limit_page_and_assets_are_served_with_navigation():
    client = TestClient(app)
    page = client.get("/first-limit")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "首板回调" in page.text
    assert 'id="run-preview"' in page.text
    assert 'id="run-close"' in page.text
    assert 'id="pipeline-progress"' in page.text
    assert "运行默认覆盖全市场；股票代码仅筛选已生成结果" in page.text
    assert 'src="first-limit.js"' in page.text
    assert "force" not in page.text.lower()
    assert "dry-run" not in page.text.lower()

    script = client.get("/first-limit.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "run_daily_candidates" not in script.text
    assert "/api/first-limit/pipeline-jobs" in script.text

    stylesheet = client.get("/first-limit.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")

    for route in ("/", "/expectation-gap", "/market-pulse.html", "/data-source-health"):
        existing = client.get(route)
        assert existing.status_code == 200
        assert 'href="/first-limit"' in existing.text


def test_api_404_is_not_swallowed_by_static_html_mount(tmp_path, monkeypatch):
    path = tmp_path / "page-route.db"
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{path.as_posix()}")
    connection = connect(path)
    migrate(connection)
    connection.close()
    response = TestClient(app).get("/api/first-limit/candidates/999999999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["error"]["code"] == "first_limit_candidate_not_found"
    assert "<html" not in response.text.lower()
