from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SourceDocument:
    symbol: str
    title: str
    source_url: str
    published_at: str | None
    announcement_id: str | None
    document_type: str
    period: str | None = None
    announcement_type: str | None = None


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_metadata(document: SourceDocument, *, file_name: str, content: bytes,
                    revision_of: str | None = None) -> dict[str, Any]:
    payload = {"schema_version": 1, "symbol": document.symbol, "title": document.title,
               "source": "cninfo", "source_url": document.source_url,
               "announcement_id": document.announcement_id, "published_at": document.published_at,
               "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "sha256": sha256_bytes(content), "file_name": file_name, "file_size": len(content)}
    if document.document_type in {"annual_report", "half_year_report", "q1_report", "q3_report"}:
        payload.update(document_type=document.document_type, period=document.period)
    else:
        payload.update(announcement_type=document.announcement_type)
    if revision_of:
        payload["revision_of"] = revision_of
    return payload
