from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.research.evidence_compressor import EvidenceCompressor
from backend.research.evidence_summary_schema import validate_evidence_summary
from backend.research.fundamental_analyst import build_context, build_raw_context, context_mode
from backend.research.json_store import read_json
from backend.research.research_context import build_compressed_context


ROOT = Path(__file__).parents[1]


def pack(tmp_path: Path):
    folder = tmp_path / "000001.SZ" / "analysis" / "2026-08-23_v1"
    folder.mkdir(parents=True)
    sources = tmp_path / "000001.SZ" / "sources" / "financial_reports"
    sources.mkdir(parents=True)
    for name in ("2025_annual.pdf", "2026_h1.pdf"):
        (sources / name).write_bytes(b"%PDF-fake")
    evidence = {"schema_version": 1, "symbol": "000001.SZ", "evidence": [
        {"id": "financial_report:2025:annual_report", "type": "financial_report", "period": "2025", "file": "../../sources/financial_reports/2025_annual.pdf", "sha256": "a"},
        {"id": "financial_report:2026H1:half_year_report", "type": "financial_report", "period": "2026H1", "file": "../../sources/financial_reports/2026_h1.pdf", "sha256": "b"},
        {"id": "dividend_snapshot:2026-08-23", "type": "database_snapshot"},
    ]}
    snapshot = {"symbol": "000001.SZ", "yield_context": {"yield_signal": "heavy", "latest_price": 10}, "dividend_history": [{"calendar_year": 2025, "cash_dividend_per_share": 1}]}
    (folder / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (folder / "input_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return folder, evidence, snapshot


def extracted(path):
    common = "本公司董事会及全体董事保证本公告内容不存在任何虚假记载。"
    duplicate = "报告期营业收入为100亿元，归属于上市公司股东的净利润为20亿元。"
    if "2026" in path.name:
        return [(1, "\n".join([common, duplicate, duplicate, "2026年上半年经营活动产生的现金流量净额为15亿元。", "公司对前期会计差错进行了更正并追溯调整。", "核心产品收入同比增长5%。"]))]
    return [(1, "\n".join([common, duplicate, "公司现金分红10亿元，分红政策保持不变。", "存货和应收账款余额分别为8亿元和6亿元。"]))]


def test_compression_is_deterministic_deduped_and_prioritized(tmp_path, monkeypatch):
    folder, evidence, _ = pack(tmp_path)
    monkeypatch.setattr("backend.research.evidence_compressor._extract_pages", extracted)
    compressor = EvidenceCompressor(research_root=tmp_path, max_characters=900)
    compressor.compress("000001.SZ", folder.name)
    first = read_json(folder / "evidence_summary.json")
    compressor.compress("000001.SZ", folder.name, force=True)
    second = read_json(folder / "evidence_summary.json")
    first.pop("generated_at"); second.pop("generated_at")
    assert first == second
    facts = [fact["text"] for values in first["sections"].values() for item in values for fact in item["facts"]]
    assert facts.count("报告期营业收入为100亿元，归属于上市公司股东的净利润为20亿元。") == 1
    assert any("会计差错" in text for text in facts)
    assert any("经营活动产生的现金流" in text for text in facts)
    assert any("现金分红" in text for text in facts)
    assert not any("董事会及全体董事保证" in text for text in facts)
    assert set(first["evidence_index"]) == {item["id"] for item in evidence["evidence"]}


def test_validation_rejects_unknown_ids_and_non_fact_fields(tmp_path, monkeypatch):
    folder, evidence, _ = pack(tmp_path)
    monkeypatch.setattr("backend.research.evidence_compressor._extract_pages", extracted)
    EvidenceCompressor(research_root=tmp_path, max_characters=900).compress("000001.SZ", folder.name)
    payload = read_json(folder / "evidence_summary.json")
    item = next(item for values in payload["sections"].values() for item in values)
    item["evidence_id"] = "invented:evidence"
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_evidence_summary(payload, evidence=evidence, symbol="000001.SZ", version=folder.name, max_characters=900)
    for forbidden in ("inference", "recommendation"):
        invalid = read_json(folder / "evidence_summary.json")
        next(item for values in invalid["sections"].values() for item in values)[forbidden] = "not allowed"
        with pytest.raises(ValueError):
            validate_evidence_summary(invalid, evidence=evidence, symbol="000001.SZ", version=folder.name, max_characters=900)


def test_role_budgets_and_raw_fallback(tmp_path, monkeypatch):
    folder, evidence, snapshot = pack(tmp_path)
    monkeypatch.setattr("backend.research.evidence_compressor._extract_pages", extracted)
    EvidenceCompressor(research_root=tmp_path, max_characters=900).compress("000001.SZ", folder.name)
    for role, budget in (("analyst", 700), ("skeptic", 600), ("valuation", 450)):
        monkeypatch.setenv(f"RESEARCH_{role.upper()}_CONTEXT_MAX_CHARS", str(budget))
        context, ids = build_compressed_context(folder, evidence, role=role)
        assert context_mode(context) == "compressed" and len(context) <= budget and ids
    (folder / "evidence_summary.json").unlink()
    for path in (tmp_path / "000001.SZ" / "sources" / "financial_reports").glob("*.pdf"):
        path.unlink()
    context, ids = build_context(folder, snapshot, evidence)
    assert context_mode(context) == "raw_fallback" and "dividend_snapshot:2026-08-23" in ids


def test_invalid_summary_falls_back_to_raw_context(tmp_path):
    folder, evidence, snapshot = pack(tmp_path)
    (folder / "evidence_summary.json").write_text("{}", encoding="utf-8")
    for path in (tmp_path / "000001.SZ" / "sources" / "financial_reports").glob("*.pdf"):
        path.unlink()
    context, ids = build_context(folder, snapshot, evidence)
    assert context_mode(context) == "raw_fallback"
    assert ids == {"dividend_snapshot:2026-08-23"}


def test_skeptic_prefers_analyst_referenced_evidence(tmp_path, monkeypatch):
    folder, evidence, _ = pack(tmp_path)
    monkeypatch.setattr("backend.research.evidence_compressor._extract_pages", extracted)
    EvidenceCompressor(research_root=tmp_path, max_characters=900).compress("000001.SZ", folder.name)
    monkeypatch.setenv("RESEARCH_SKEPTIC_CONTEXT_MAX_CHARS", "350")
    context, ids = build_compressed_context(
        folder,
        evidence,
        role="skeptic",
        preferred_evidence_ids={"financial_report:2025:annual_report"},
    )
    assert "financial_report:2025:annual_report" in ids
    assert "financial_report:2025:annual_report" in context


@pytest.mark.parametrize(("symbol", "version", "terms"), [
    ("000858.SZ", "2026-08-23_v3", ("经营活动产生的现金流", "现金分红", "存货", "会计差错", "回购", "董事长")),
    ("000423.SZ", "2026-08-23_v1", ("经营活动产生的现金流", "应收", "阿胶及系列产品", "合同负债", "现金分红")),
])
def test_poc_golden_evidence_and_traceability(symbol, version, terms):
    folder = ROOT / "data" / "research" / symbol / "analysis" / version
    summary, analyst, skeptic = (read_json(folder / name) for name in ("evidence_summary.json", "analyst.json", "skeptic.json"))
    text = "\n".join(fact["text"] for values in summary["sections"].values() for item in values for fact in item["facts"])
    assert all(term in text for term in terms)
    referenced = {evidence_id for claim in analyst.get("claims", []) if claim.get("claim_type") == "FACT" for evidence_id in claim.get("evidence_ids", [])}
    referenced.update(evidence_id for challenge in skeptic.get("challenged_claims", []) for evidence_id in challenge.get("evidence_ids", []))
    assert referenced <= set(summary["evidence_index"])


def test_poc_contexts_are_compressed_and_role_specific():
    for symbol, version in (("000858.SZ", "2026-08-23_v3"), ("000423.SZ", "2026-08-23_v1")):
        folder = ROOT / "data" / "research" / symbol / "analysis" / version
        snapshot, evidence = read_json(folder / "input_snapshot.json"), read_json(folder / "evidence.json")
        raw, _ = build_raw_context(folder, snapshot, evidence)
        analyst, _ = build_context(folder, snapshot, evidence, "analyst")
        skeptic, _ = build_context(folder, snapshot, evidence, "skeptic")
        valuation, _ = build_context(folder, snapshot, evidence, "valuation")
        assert len(analyst) <= 30_000 and len(skeptic) <= 24_000 and len(valuation) <= 16_000
        assert len(valuation) < len(skeptic) < len(analyst) < len(raw)
