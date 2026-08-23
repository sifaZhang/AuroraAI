from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.research.research_summary_repository import ResearchSummaryRepository


ROOT = Path(__file__).parents[1]
CORE = ("analyst.json", "skeptic.json", "valuation.json", "decision.json")


def database(enabled=1):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
      CREATE TABLE dividend_stable_universe(
        market TEXT, symbol TEXT, company_name TEXT, is_enabled INTEGER, grade TEXT,
        entry_yield REAL, add_yield REAL, heavy_yield REAL
      );
      CREATE TABLE dividend_yield_snapshots(
        market TEXT, symbol TEXT, calculation_date TEXT, latest_price REAL,
        three_year_average_yield REAL
      );
    """)
    connection.execute("INSERT INTO dividend_stable_universe VALUES('CN','000858.SZ','五粮液',?,?,5,6,7)",
                       (enabled, "A"))
    connection.execute("INSERT INTO dividend_yield_snapshots VALUES('CN','000858.SZ','2026-08-23',71.19,.0729)")
    return connection


def copy_version(root: Path, version: str, *, status: str | None = None, summary=True):
    source = ROOT / "data" / "research" / "000858.SZ" / "analysis" / "2026-08-23_v3"
    target = root / "000858.SZ" / "analysis" / version
    target.mkdir(parents=True)
    shutil.copyfile(ROOT / "data" / "research" / "000858.SZ" / "company.json", root / "000858.SZ" / "company.json")
    for name in (*CORE, "evidence.json"):
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if "analysis_version" in payload:
            payload["analysis_version"] = version
        (target / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if summary:
        shutil.copyfile(source / "evidence_summary.json", target / "evidence_summary.json")
    if status:
        (target / "run.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    return target


def test_real_detail_api_for_both_pocs_preserves_zero_confidence():
    client = TestClient(app)
    wuliangye = client.get("/api/research/000858.SZ")
    dong_e = client.get("/api/research/000423.SZ")
    assert wuliangye.status_code == dong_e.status_code == 200
    first, second = wuliangye.json(), dong_e.json()
    assert (first["market"]["yield_signal"], first["decision"]["research_decision"],
            first["decision"]["decision_confidence"]) == ("heavy", "watch", .4)
    assert (first["skeptic"]["verdict"], first["skeptic"]["overall_assessment"]["confidence"]) == ("CAUTION", .65)
    assert first["valuation"]["fair_value_per_share"] == {"bear": 24.73, "base": 55.64, "bull": 98.93}
    assert first["valuation"]["dividend_cross_check"]["sustainable_yield"] == .0656
    assert second["decision"]["decision_confidence"] == 0
    assert (first["confidence"]["score"], first["confidence"]["level"], first["confidence"]["model_reported"]) == (55, "MEDIUM", .4)
    assert (second["confidence"]["score"], second["confidence"]["level"], second["confidence"]["model_reported"]) == (60, "MEDIUM", 0)
    assert first["confidence"]["source"] == second["confidence"]["source"] == "calibrated"
    assert second["valuation"]["fair_value_per_share"] == {"bear": 30.75, "base": 41.46, "bull": 53.68}


def test_detail_uses_current_then_falls_back_from_failed_and_partial(tmp_path):
    connection = database()
    copy_version(tmp_path, "2026-08-20_v1")
    copy_version(tmp_path, "2026-08-21_v1")
    copy_version(tmp_path, "2026-08-22_v1", status="FAILED")
    copy_version(tmp_path, "2026-08-23_v1", status="PARTIAL")
    current = tmp_path / "000858.SZ" / "current.json"
    current.write_text(json.dumps({"analysis_version": "2026-08-20_v1"}), encoding="utf-8")
    repository = ResearchSummaryRepository(connection, research_root=tmp_path)
    assert repository.get_detail("000858.SZ")["analysis_version"] == "2026-08-20_v1"
    current.write_text(json.dumps({"analysis_version": "2026-08-22_v1"}), encoding="utf-8")
    assert repository.get_detail("000858.SZ")["analysis_version"] == "2026-08-21_v1"


def test_missing_evidence_summary_is_nonfatal_and_pdf_content_is_not_returned(tmp_path):
    connection = database(); copy_version(tmp_path, "2026-08-23_v1", summary=False)
    detail = ResearchSummaryRepository(connection, research_root=tmp_path).get_detail("000858.SZ")
    assert detail["evidence"]["summary_available"] is False
    assert detail["evidence"]["source_count"] == 11
    assert detail["confidence"]["source"] == "model_fallback"
    serialized = json.dumps(detail, ensure_ascii=False)
    assert ".pdf" not in serialized and "source_anchor" not in serialized


def test_unknown_disabled_and_incomplete_detail_are_hidden(tmp_path):
    connection = database(enabled=0); copy_version(tmp_path, "2026-08-23_v1")
    repository = ResearchSummaryRepository(connection, research_root=tmp_path)
    assert repository.get_detail("000858.SZ") is None
    assert repository.get_detail("UNKNOWN") is None
    enabled = database(); folder = copy_version(tmp_path / "other", "2026-08-23_v1")
    (folder / "skeptic.json").unlink()
    assert ResearchSummaryRepository(enabled, research_root=tmp_path / "other").get_detail("000858.SZ") is None


def test_detail_route_and_api_are_read_only_with_friendly_404():
    client = TestClient(app)
    page = client.get("/dividend/ai-research/000858.SZ")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "AI Research Overview" in page.text and "research-detail" in page.text
    response = client.get("/api/research/UNKNOWN")
    assert response.status_code == 404 and "no completed AI research" in response.json()["detail"]
    assert client.post("/api/research/000858.SZ").status_code == 405
    assert client.patch("/api/research/000858.SZ").status_code == 405
    assert client.delete("/api/research/000858.SZ").status_code == 405
