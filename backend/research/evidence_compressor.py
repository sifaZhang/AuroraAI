from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_summary_schema import SECTIONS, validate_evidence_summary
from .json_store import read_json, write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT


DEFAULT_MAX_CHARS = 30_000
KEY_NUMBER_FIELDS = (
    "revenue", "net_profit_parent", "adjusted_net_profit", "operating_cash_flow",
    "cash", "debt", "inventory", "receivables", "contract_liabilities", "capex",
    "dividend", "eps", "roe",
)
SECTION_KEYWORDS = {
    "accounting": ("会计政策", "会计差错", "前期差错", "更正", "重述", "追溯调整", "审计意见", "内部控制", "restatement", "correction"),
    "cash_flow": ("经营活动产生的现金流", "经营现金流", "现金流量", "销售商品、提供劳务收到的现金", "free cash flow", "operating cash flow"),
    "dividend": ("现金分红", "利润分配", "股利", "派息", "每股分红", "分红政策", "股息", "payout", "dividend"),
    "balance_sheet": ("存货", "应收", "合同负债", "货币资金", "现金及现金等价物", "有息负债", "借款", "资产负债", "inventory", "receivable"),
    "governance": ("董事长", "董事会", "监事会", "高级管理人员", "公司治理", "审计委员会", "无法履职", "治理"),
    "material_events": ("股份回购", "回购股份", "重大资产", "控制权", "重大事项", "业绩预告", "业绩快报", "repurchase"),
    "risk_disclosures": ("风险因素", "重大风险", "不确定性", "可能面临", "风险提示", "减值风险", "偿债风险"),
    "financial_performance": ("营业收入", "净利润", "归属于上市公司股东", "扣除非经常性损益", "每股收益", "加权平均净资产收益率", "同比", "revenue", "net profit"),
    "business": ("主营业务", "核心产品", "分产品", "产品收入", "经营情况", "业务构成", "市场销售", "产销量", "客户", "行业", "阿胶及系列产品"),
}
SECTION_WEIGHT = {"accounting": 70, "cash_flow": 65, "dividend": 65, "financial_performance": 60,
                  "risk_disclosures": 58, "governance": 56, "balance_sheet": 54,
                  "material_events": 50, "business": 52}
BOILERPLATE = ("本公司董事会及全体董事保证", "重要提示", "备查文件", "释义", "社会责任", "环境保护", "免责声明")


def _extract_pages(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return [(number, text) for number, page in enumerate(reader.pages, 1)
            if (text := (page.extract_text() or "").strip())]


def _units(text: str) -> list[str]:
    clean = re.sub(r"[ \t]+", " ", text.replace("\u0000", ""))
    parts = re.split(r"(?<=[。！？；.!?;])\s+|\n{1,}", clean)
    return [part.strip() for part in parts if 20 <= len(part.strip()) <= 1200]


def _classify(text: str) -> tuple[str, int] | None:
    if "本公司董事会及全体董事保证" in text:
        return None
    lower = text.lower()
    scores = {section: sum(lower.count(word.lower()) for word in words)
              for section, words in SECTION_KEYWORDS.items()}
    section = max(SECTIONS, key=lambda value: (scores.get(value, 0), SECTION_WEIGHT[value]))
    if scores.get(section, 0) == 0:
        return None
    penalty = 45 if any(word in text for word in BOILERPLATE) else 0
    return section, SECTION_WEIGHT[section] + min(scores[section], 8) * 4 - penalty


def _period_rank(period: str | None) -> int:
    if not period: return 0
    digits = re.sub(r"\D", "", period)
    return int((digits + "0000")[:8]) if digits else 0


class EvidenceCompressor:
    def __init__(self, *, research_root: Path = DEFAULT_RESEARCH_ROOT, max_characters: int | None = None) -> None:
        self.research_root = research_root
        self.max_characters = max_characters or int(os.environ.get("RESEARCH_CONTEXT_MAX_CHARS", DEFAULT_MAX_CHARS))

    def compress(self, symbol: str, version: str, *, force: bool = False) -> dict[str, Any]:
        symbol = symbol.upper(); folder = self.research_root / symbol / "analysis" / version
        evidence, snapshot = read_json(folder / "evidence.json"), read_json(folder / "input_snapshot.json")
        if evidence.get("symbol") != symbol or snapshot.get("symbol") != symbol:
            raise ValueError("input pack symbol mismatch")
        output = folder / "evidence_summary.json"
        if output.exists() and not force:
            raise FileExistsError(f"evidence_summary.json already exists for {version}; use --force")
        candidates: list[dict[str, Any]] = []
        source_chars = 0
        authority = list(evidence.get("evidence", []))
        latest_period = max((_period_rank(item.get("period")) for item in authority), default=0)
        for source_order, item in enumerate(authority):
            evidence_id = item.get("id")
            if not evidence_id: continue
            if item.get("type") == "database_snapshot":
                text = json.dumps({"yield_context": snapshot.get("yield_context"), "dividend_history": snapshot.get("dividend_history")}, ensure_ascii=False, sort_keys=True)
                source_chars += len(text)
                candidates.append(self._candidate(item, "dividend", text, "database_snapshot", 160, source_order))
                continue
            file_name = item.get("file")
            path = (folder / str(file_name)).resolve() if file_name else None
            if path is None or not path.is_file() or path.suffix.lower() != ".pdf": continue
            for page, text in _extract_pages(path):
                source_chars += len(text)
                for unit_order, unit in enumerate(_units(text)):
                    classified = _classify(unit)
                    if not classified: continue
                    section, score = classified
                    recency = 30 if _period_rank(item.get("period")) == latest_period else (20 if item.get("type") == "financial_report" else 10)
                    candidates.append(self._candidate(item, section, unit, f"page:{page}", score + recency, source_order, unit_order))
        selected = self._select(candidates)
        sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTIONS}
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in selected: grouped[(item["section"], item["evidence_id"])].append(item)
        for (section, evidence_id), facts in grouped.items():
            source = facts[0]["source"]
            sections[section].append({"evidence_id": evidence_id, "source_type": source.get("type", "unknown"),
                                      "period": source.get("period"), "published_at": source.get("published_at"),
                                      "section": section, "priority": max(x["priority"] for x in facts),
                                      "facts": [{"text": x["text"], "source_anchor": x["anchor"]} for x in facts],
                                      "key_numbers": {key: None for key in KEY_NUMBER_FIELDS}})
        for values in sections.values(): values.sort(key=lambda item: (-item["priority"], item["evidence_id"]))
        index = {}
        for item in authority:
            evidence_id = str(item.get("id")); matches = [x for x in selected if x["evidence_id"] == evidence_id]
            index[evidence_id] = {"source_type": item.get("type", "unknown"), "period": item.get("period"),
                                  "file": item.get("file"), "sha256": item.get("sha256"),
                                  "included_sections": sorted({x["section"] for x in matches}),
                                  "included_fact_count": len(matches)}
        compressed_chars = sum(len(item["text"]) for item in selected)
        payload = {"schema_version": 1, "symbol": symbol, "analysis_version": version,
                   "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "compression": {"method": "deterministic-v1", "source_evidence_count": len(index),
                                   "included_evidence_count": len({x["evidence_id"] for x in selected}),
                                   "source_character_count": source_chars, "compressed_character_count": compressed_chars,
                                   "compression_ratio": round(compressed_chars / source_chars, 6) if source_chars else 0,
                                   "estimated_tokens_before": (source_chars + 3) // 4,
                                   "estimated_tokens_after": (compressed_chars + 3) // 4,
                                   "max_characters": self.max_characters},
                   "sections": sections, "evidence_index": index}
        document = validate_evidence_summary(payload, evidence=evidence, symbol=symbol, version=version,
                                             max_characters=self.max_characters)
        write_json_atomic(output, document.model_dump(mode="json"))
        return {"symbol": symbol, "version": version, "path": str(output), **document.compression.model_dump()}

    @staticmethod
    def _candidate(source: dict, section: str, text: str, anchor: str, priority: int,
                   source_order: int, unit_order: int = 0) -> dict[str, Any]:
        return {"evidence_id": str(source["id"]), "source": source, "section": section,
                "text": re.sub(r"\s+", " ", text).strip(), "anchor": anchor, "priority": priority,
                "source_order": source_order, "unit_order": unit_order}

    def _select(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set(); selected = []; used = 0
        ordered = sorted(candidates, key=lambda item: (-item["priority"], item["source_order"], item["unit_order"], item["text"]))
        for item in ordered:
            canonical = re.sub(r"[\W_]+", "", item["text"].lower())
            if canonical in seen: continue
            length = len(item["text"])
            if used + length > self.max_characters: continue
            seen.add(canonical); selected.append(item); used += length
        return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic FACT-only compressed research evidence")
    parser.add_argument("--symbol", required=True); parser.add_argument("--version", required=True)
    parser.add_argument("--force", action="store_true"); parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    try:
        print(EvidenceCompressor(research_root=args.research_root).compress(args.symbol, args.version, force=args.force)); return 0
    except (FileExistsError, OSError, ValueError) as exc:
        print({"status": "failed", "error": str(exc)}); return 1


if __name__ == "__main__": raise SystemExit(main())
