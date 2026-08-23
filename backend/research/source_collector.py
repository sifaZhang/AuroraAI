from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .announcement_collector import classify_announcement
from .financial_report_collector import choose_initial_reports, classify_financial_report
from .json_store import write_bytes_atomic, write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT, ProfileBuilder
from .profile_repository import ProfileRepository
from .source_metadata import SourceDocument, source_metadata
from .source_repository import SourceRepository


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_FILE_ROOT = "https://static.cninfo.com.cn/"
USER_AGENT = "AuroraAI-Research/1.0 (official-source collector; contact: repository owner)"
# CNINFO's general feed is useful for event announcements, but it can return
# translated filings ahead of the Chinese filing.  Query the two regular-report
# categories as well so the canonical Chinese annual/H1 PDF is discoverable.
REGULAR_REPORT_CATEGORIES = (
    "category_ndbg_szsh", "category_yjdbg_szsh", "category_bndbg_szsh", "category_sjdbg_szsh",
)


class CninfoClient:
    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 25, retries: int = 2) -> None:
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT, "Referer": "https://www.cninfo.com.cn/"})
        self._owns_client, self.retries = client is None, retries

    def close(self) -> None:
        if self._owns_client: self.client.close()

    def discover(self, symbol: str) -> list[dict[str, Any]]:
        code = symbol.split(".")[0]
        search = self.client.post(CNINFO_SEARCH_URL, data={"keyWord": code, "maxNum": 10})
        search.raise_for_status()
        result = next((item for item in search.json() if str(item.get("code")) == code), None)
        if result is None or not result.get("orgId"):
            raise RuntimeError(f"cninfo organization id unavailable for {symbol}")
        params = {"stock": f"{code},{result['orgId']}", "tabName": "fulltext", "pageSize": 100, "pageNum": 1,
                  "column": "szse" if symbol.endswith(".SZ") else "sse", "plate": "sz" if symbol.endswith(".SZ") else "sh",
                  "searchkey": "", "secid": "", "trade": "", "seDate": "", "sortName": "", "sortType": "", "isHLtitle": "true"}
        rows: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for category in ("", *REGULAR_REPORT_CATEGORIES):
            for item in self._query({**params, "category": category}):
                url = str(item.get("adjunctUrl") or "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    rows.append(item)
        return rows

    def _query(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.post(CNINFO_QUERY_URL, data=params)
                response.raise_for_status(); payload = response.json()
                return list(payload.get("announcements") or [])
            except Exception as exc:
                last_error = exc
                if attempt < self.retries: time.sleep(min(2 ** attempt, 2))
        raise RuntimeError(f"cninfo discovery failed: {type(last_error).__name__}")

    def download(self, url: str) -> bytes:
        response = self.client.get(url)
        response.raise_for_status()
        content = response.content
        if not content or not content.startswith(b"%PDF"):
            raise ValueError("official source response is not a valid PDF")
        return content


def _published(value: Any) -> str | None:
    if value is None: return None
    try: return datetime.fromtimestamp(int(value) / 1000, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError): return str(value)


def discover_documents(symbol: str, client: CninfoClient) -> tuple[list[SourceDocument], list[SourceDocument]]:
    financial, announcements = [], []
    seen: set[tuple[str, str | None]] = set()
    for row in client.discover(symbol):
        title = str(row.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
        relative = str(row.get("adjunctUrl") or "")
        if not relative: continue
        url = relative if relative.startswith("http") else CNINFO_FILE_ROOT + relative.lstrip("/")
        published, identifier = _published(row.get("announcementTime")), str(row.get("announcementId") or "") or None
        report = classify_financial_report(title, symbol, url, published, identifier)
        if report and (report.document_type, report.period) not in seen:
            seen.add((report.document_type, report.period)); financial.append(report); continue
        category = classify_announcement(title)
        if category: announcements.append(SourceDocument(symbol, title, url, published, identifier, "announcement", announcement_type=category))
    return choose_initial_reports(financial), announcements


def _file_name(document: SourceDocument) -> str:
    if document.document_type != "announcement":
        suffix = {"annual_report": "annual", "half_year_report": "h1", "q1_report": "q1", "q3_report": "q3"}[document.document_type]
        return f"{document.period[:4]}_{suffix}.pdf"
    date = (document.published_at or "unknown").replace("-", "")
    return f"{date}_{document.announcement_type}_{document.announcement_id or 'unknown'}.pdf"


def save_document(document: SourceDocument, root: Path, client: CninfoClient) -> str:
    category = "financial_reports" if document.document_type != "announcement" else "announcements"
    directory = root / "sources" / category; repository = SourceRepository(root)
    existing = repository.source_url_exists(document.source_url)
    if existing:
        file_path = directory / existing["file_name"]
        if file_path.exists() and existing.get("sha256"):
            import hashlib
            if hashlib.sha256(file_path.read_bytes()).hexdigest() == existing["sha256"]: return "skipped"
    content = client.download(document.source_url)
    if not content or not content.startswith(b"%PDF"):
        raise ValueError("official source response is not a valid PDF")
    base = _file_name(document); target = directory / base; revision_of = None
    if target.exists():
        number = 1
        while (directory / f"{target.stem}_rev{number}{target.suffix}").exists(): number += 1
        revision_of, target = base, directory / f"{target.stem}_rev{number}{target.suffix}"
    write_bytes_atomic(target, content)
    meta = source_metadata(document, file_name=target.name, content=content, revision_of=revision_of)
    write_json_atomic(target.with_suffix(".meta.json"), meta)
    return "downloaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect official CNINFO sources and build a research input pack")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    repository = ProfileRepository(); client = CninfoClient()
    try:
        root = args.research_root / args.symbol.upper(); ProfileBuilder(repository, research_root=args.research_root).build(args.symbol)
        financial, announcements = discover_documents(args.symbol.upper(), client)
        print({"financial_reports": [x.title for x in financial], "announcements": [x.title for x in announcements]})
        if not args.discover_only and not args.no_download:
            for document in [*financial, *announcements]: save_document(document, root, client)
        from .input_pack_builder import InputPackBuilder
        if not args.discover_only: print(InputPackBuilder(repository, research_root=args.research_root).build(args.symbol))
        return 0
    except Exception as exc:
        print({"status": "source_unavailable", "source": "cninfo", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        client.close(); repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
