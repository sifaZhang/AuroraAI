from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .json_store import read_json, write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .profile_repository import ProfileRepository
from .source_repository import SourceRepository


class InputPackBuilder:
    def __init__(self, repository: ProfileRepository, *, research_root: Path = DEFAULT_RESEARCH_ROOT) -> None:
        self.repository, self.research_root = repository, research_root

    def _next_version(self, analysis: Path, as_of: str) -> str:
        number = 1
        while (analysis / f"{as_of}_v{number}").exists(): number += 1
        return f"{as_of}_v{number}"

    def build(self, symbol: str, *, as_of: str | None = None) -> dict[str, Any]:
        facts = self.repository.profile_facts(symbol)
        if facts is None: raise ValueError(f"stable dividend universe symbol not found: {symbol.upper()}")
        root = self.research_root / facts["symbol"]; company = read_json(root / "company.json")
        as_of = as_of or date.today().isoformat(); analysis = root / "analysis"; version = self._next_version(analysis, as_of)
        target = analysis / version; target.mkdir(parents=True)
        sources = SourceRepository(root)
        reports, announcements = sources.documents("financial_reports"), sources.documents("announcements")
        def reference(item: dict[str, Any]) -> dict[str, Any]:
            name = item["file_name"]; category = "financial_reports" if "document_type" in item else "announcements"
            return {**{key: item.get(key) for key in ("period", "document_type", "announcement_type", "source", "published_at", "sha256")},
                    "file": f"../../sources/{category}/{name}", "metadata_file": f"../../sources/{category}/{Path(name).with_suffix('.meta.json').name}"}
        snapshot = {"schema_version": 1, "symbol": facts["symbol"], "name": facts["company_name"], "as_of": as_of,
                    "company_profile": {"research_type": company.get("research_type", {}), "industry": {"sw_l1": company.get("industry", {}).get("sw_l1_name"), "sw_l2": company.get("industry", {}).get("sw_l2_name"), "sw_l3": company.get("industry", {}).get("sw_l3_name")}},
                    "yield_context": {key: facts.get(key) for key in ("latest_price", "price_date", "three_year_average_dps", "three_year_average_yield", "entry_yield", "add_yield", "heavy_yield", "yield_signal")},
                    "dividend_history": self.repository.dividend_history(facts["symbol"]), "financial_reports": [reference(x) for x in reports], "announcements": [reference(x) for x in announcements]}
        evidence = {"schema_version": 1, "symbol": facts["symbol"], "evidence": [
            *[{"id": f"financial_report:{x.get('period')}:{x.get('document_type')}", "type": "financial_report", "period": x.get("period"), "source": x.get("source"), "file": f"../../sources/financial_reports/{x['file_name']}", "sha256": x.get("sha256"), "published_at": x.get("published_at")} for x in reports],
            *[{"id": f"announcement:{x.get('announcement_id')}", "type": "announcement", "source": x.get("source"), "file": f"../../sources/announcements/{x['file_name']}", "sha256": x.get("sha256"), "published_at": x.get("published_at")} for x in announcements],
            {"id": f"dividend_snapshot:{facts.get('calculation_date')}", "type": "database_snapshot", "source": "aurora_db", "as_of": facts.get("calculation_date")},
        ]}
        write_json_atomic(target / "input_snapshot.json", snapshot); write_json_atomic(target / "evidence.json", evidence)
        return {"symbol": facts["symbol"], "version": version, "directory": str(target)}
