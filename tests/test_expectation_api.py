import sqlite3
import pytest

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.expectation_gap.database import migrate
from backend.expectation_gap.futu_client import CollectionResult
from backend.expectation_gap.repository import patch_manual_a_share_valuation, patch_price


def test_expectation_gap_api(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    connection.execute(
        """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,created_at,updated_at)
           VALUES('SH.688192','688192','迪哲医药','A','SH','STOCK',1,'2026-07-19','2026-07-19')"""
    )
    stock_id = connection.execute("SELECT id FROM stocks").fetchone()[0]
    patch_price(connection, stock_id, CollectionResult("success", {"last_price": 100}), "eastmoney")
    patch_manual_a_share_valuation(connection, stock_id, data_date="2026-07-19",
        morningstar_fair_value=166, morningstar_star_rating=4,
        analyst_average_target=150, analyst_count=6)
    connection.commit()
    connection.close()

    response = TestClient(app).get("/api/expectation-gaps", params={"market": "a", "q": "688192", "page_size": 20})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["morningstar_gap_pct"] == pytest.approx(66)
    assert payload["items"][0]["analyst_gap_pct"] == pytest.approx(50)
    assert payload["items"][0]["display_source"] == "手工"
