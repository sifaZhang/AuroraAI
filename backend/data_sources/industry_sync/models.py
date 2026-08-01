"""Result contracts for the current SW industry snapshot sync."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models import IndustryMembership


@dataclass(frozen=True)
class IndustryMembershipConflict:
    symbol: str
    candidates: tuple[IndustryMembership, ...]


@dataclass(frozen=True)
class IndustrySyncResult:
    status: Literal["success", "partial_success", "failed"]
    provider: str
    fallback_used: bool
    node_count: int
    membership_input_count: int
    membership_written_count: int
    duplicate_count: int
    conflict_count: int
    skipped_count: int
    conflict_symbols: tuple[str, ...]
    conflicts: tuple[IndustryMembershipConflict, ...]
    warnings: tuple[str, ...]
    dry_run: bool
    changed: bool
    forced: bool
