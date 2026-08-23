from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.research.research_summary_repository import ResearchSummaryRepository


ROOT = Path(__file__).parents[1]


def database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
      CREATE TABLE dividend_stable_universe(
        market TEXT, symbol TEXT, company_name TEXT, is_enabled INTEGER,
        grade TEXT, entry_yield REAL, add_yield REAL, heavy_yield REAL
      );
      CREATE TABLE dividend_yield_snapshots(
        market TEXT, symbol TEXT, calculation_date TEXT, latest_price REAL,
        three_year_average_yield REAL
      );
    """)
    return connection


def add_company(connection, symbol="000858.SZ", name="五粮液", enabled=1):
    connection.execute("INSERT INTO dividend_stable_universe VALUES('CN',?,?,?,?,?,?,?)",
                       (symbol, name, enabled, "A", 5.0, 6.0, 7.0))
    connection.execute("INSERT INTO dividend_yield_snapshots VALUES('CN',?,?,?,?)",
                       (symbol, "2026-08-23", 71.19, .0729))


def decision(symbol="000858.SZ"):
    version = "2026-08-23_v3" if symbol == "000858.SZ" else "2026-08-23_v1"
    return json.loads((ROOT / "data" / "research" / symbol / "analysis" / version / "decision.json").read_text(encoding="utf-8"))


def write_version(root, symbol, version, payload=None, run_status=None):
    folder = root / symbol / "analysis" / version
    folder.mkdir(parents=True)
    if payload is not None:
        value = dict(payload); value["symbol"] = symbol; value["analysis_version"] = version
        (folder / "decision.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    if run_status:
        (folder / "run.json").write_text(json.dumps({"status": run_status}), encoding="utf-8")
    return folder


def test_current_completed_is_preferred_and_failed_version_is_not_summary(tmp_path):
    connection = database(); add_company(connection)
    write_version(tmp_path, "000858.SZ", "2026-08-22_v1", decision())
    write_version(tmp_path, "000858.SZ", "2026-08-24_v1", decision(), "FAILED")
    write_version(tmp_path, "000858.SZ", "2026-08-25_v1", decision(), "PARTIAL")
    current = tmp_path / "000858.SZ" / "current.json"
    current.write_text(json.dumps({"analysis_version": "2026-08-24_v1"}), encoding="utf-8")
    item = ResearchSummaryRepository(connection, research_root=tmp_path).list_summaries()[0]
    assert item["analysis_version"] == "2026-08-22_v1"


def test_missing_invalid_and_disabled_research_are_safely_ignored(tmp_path):
    connection = database()
    add_company(connection, "000858.SZ", "五粮液")
    add_company(connection, "000423.SZ", "东阿阿胶", enabled=0)
    write_version(tmp_path, "000858.SZ", "2026-08-23_v3")
    write_version(tmp_path, "000423.SZ", "2026-08-23_v1", decision("000423.SZ"))
    assert ResearchSummaryRepository(connection, research_root=tmp_path).list_summaries() == []


def test_zero_confidence_and_formal_yield_logic_are_preserved(tmp_path):
    connection = database(); add_company(connection, "000423.SZ", "东阿阿胶")
    payload = decision("000423.SZ")
    payload["inputs_summary"]["yield_signal"] = "watch"
    write_version(tmp_path, "000423.SZ", "2026-08-23_v1", payload)
    item = ResearchSummaryRepository(connection, research_root=tmp_path).list_summaries()[0]
    assert item["decision_confidence"] == 0
    assert item["yield_signal"] == "heavy"


def test_real_poc_summaries_and_api_filters():
    client = TestClient(app)
    payload = client.get("/api/research").json()
    by_symbol = {item["symbol"]: item for item in payload["items"]}
    assert (by_symbol["000858.SZ"]["yield_signal"], by_symbol["000858.SZ"]["research_decision"],
            by_symbol["000858.SZ"]["decision_confidence"]) == ("heavy", "watch", .4)
    assert (by_symbol["000858.SZ"]["decision_cap"], by_symbol["000858.SZ"]["skeptic_verdict"],
            by_symbol["000858.SZ"]["valuation_status"], by_symbol["000858.SZ"]["forecast_quality"]) == (
                "entry", "CAUTION", "FAIR_TO_EXPENSIVE", "LOW")
    assert (by_symbol["000423.SZ"]["yield_signal"], by_symbol["000423.SZ"]["research_decision"],
            by_symbol["000423.SZ"]["decision_confidence"]) == ("heavy", "watch", 0)
    assert client.get("/api/research", params={"decision": "watch", "q": "000858"}).json()["total"] == 1
    assert client.get("/api/research", params={"decision": "unknown"}).status_code == 422


def test_overview_route_and_dividend_ai_contract():
    client = TestClient(app)
    page = client.get("/dividend/ai-research")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "Yield Signal" in page.text and "AI Research Decision" in page.text
    script = (ROOT / "frontend" / "dividend-universe.js").read_text(encoding="utf-8")
    assert "AI建议" in script and "AI置信度" in script and "AI更新时间" in script
    assert "未分析" in script
    assert "/dividend/ai-research/${encodeURIComponent(item.symbol)}" in script
    assert "research?.decision_confidence" in script
    overview = (ROOT / "frontend" / "ai-research.js").read_text(encoding="utf-8")
    assert "detailUrl(item.symbol)" in overview
    assert "new URLSearchParams(location.search).get('symbol')" in overview
    assert "该股票尚无已完成AI研究。" in overview and "暂无AI研究结果" in overview
    assert all(label in overview for label in ("已研究公司", "Watch", "Entry+", "Signal Disagreements"))
