from __future__ import annotations

from pathlib import Path
from typing import Any

from .json_store import read_json


class SourceRepository:
    def __init__(self, company_root: Path) -> None:
        self.root = company_root

    def documents(self, category: str) -> list[dict[str, Any]]:
        directory = self.root / "sources" / category
        return [read_json(path) | {"metadata_path": path} for path in sorted(directory.glob("*.meta.json"))]

    def find(self, document_type: str, period: str | None) -> dict[str, Any] | None:
        for item in self.documents("financial_reports"):
            if item.get("document_type") == document_type and item.get("period") == period:
                return item
        return None

    def source_url_exists(self, source_url: str) -> dict[str, Any] | None:
        for category in ("financial_reports", "announcements"):
            for item in self.documents(category):
                if item.get("source_url") == source_url:
                    return item
        return None
