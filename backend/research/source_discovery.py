from __future__ import annotations

from dataclasses import dataclass

from .source_collector import CninfoClient, discover_documents
from .source_metadata import SourceDocument


@dataclass(frozen=True)
class DiscoveryResult:
    financial: tuple[SourceDocument, ...]
    announcements: tuple[SourceDocument, ...]


class SourceDiscovery:
    """Discover CNINFO metadata without downloading source documents."""

    def __init__(self, client: CninfoClient | None = None) -> None:
        self.client = client or CninfoClient()
        self._owns_client = client is None

    def discover(self, symbol: str) -> DiscoveryResult:
        financial, announcements = discover_documents(symbol.upper(), self.client)
        return DiscoveryResult(tuple(financial), tuple(announcements))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "SourceDiscovery":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
