from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .json_store import read_json, write_json_atomic
from .profile_repository import ProfileRepository
from .research_type_mapping import DEFAULT_MAPPING_PATH, load_mapping, resolve_research_type


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESEARCH_ROOT = PROJECT_ROOT / "data" / "research"
SOURCE_DIRECTORIES = ("financial_reports", "announcements", "industry", "market")


class ProfileBuilder:
    def __init__(self, repository: ProfileRepository, *, research_root: Path = DEFAULT_RESEARCH_ROOT,
                 mapping_path: Path = DEFAULT_MAPPING_PATH) -> None:
        self.repository, self.research_root = repository, research_root
        self.mapping = load_mapping(mapping_path)

    def build(self, symbol: str) -> dict[str, Any]:
        facts = self.repository.profile_facts(symbol)
        if facts is None:
            raise ValueError(f"stable dividend universe symbol not found: {symbol.upper()}")
        directory = self.research_root / facts["symbol"]
        (directory / "sources").mkdir(parents=True, exist_ok=True)
        for name in SOURCE_DIRECTORIES:
            (directory / "sources" / name).mkdir(exist_ok=True)
        (directory / "analysis").mkdir(exist_ok=True)
        industry = {"classification": facts.get("classification") or "SW", "classification_version": facts.get("classification_version") or "2021",
                    "sw_l1_code": facts.get("level1_code"), "sw_l1_name": facts.get("level1_name"),
                    "sw_l2_code": facts.get("level2_code"), "sw_l2_name": facts.get("level2_name"),
                    "sw_l3_code": facts.get("level3_code"), "sw_l3_name": facts.get("level3_name")}
        owned = {"schema_version": 1, "symbol": facts["symbol"], "name": facts["company_name"], "industry": industry,
                 "research_type": resolve_research_type(facts["symbol"], facts.get("level3_name"), self.mapping)}
        company_path, current_path = directory / "company.json", directory / "current.json"
        write_json_atomic(company_path, {**read_json(company_path), **owned})
        if not current_path.exists():
            write_json_atomic(current_path, {"schema_version": 1, "status": "not_analyzed"})
        return {"symbol": facts["symbol"], "yield_signal": facts["yield_signal"], "directory": str(directory)}

    def build_candidates(self) -> list[dict[str, Any]]:
        return [self.build(item["symbol"]) for item in self.repository.research_candidates()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local research profile directories from existing SQLite facts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol")
    group.add_argument("--all-candidates", action="store_true")
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    args = parser.parse_args(argv)
    repository = ProfileRepository()
    try:
        builder = ProfileBuilder(repository, research_root=args.research_root, mapping_path=args.mapping)
        result = builder.build(args.symbol) if args.symbol else builder.build_candidates()
        print(result)
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
