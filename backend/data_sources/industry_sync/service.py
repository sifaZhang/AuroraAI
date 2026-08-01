"""Validation, conflict handling and persistence for the current SW snapshot."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict

from ..contracts import IndustryDataProvider
from ..errors import DataSourceError, ProviderEmptyDataError, ProviderValidationError
from ..models import IndustryMembership, IndustryNode
from ..validation import validate_industry_nodes
from .models import IndustryMembershipConflict, IndustrySyncResult
from .repository import IndustryRepository

LOGGER = logging.getLogger("aurora.data_sources")


def _snapshot_identity(row: IndustryMembership):
    return (row.classification, row.version, row.symbol,
            row.level1_code, row.level1_name, row.level2_code, row.level2_name,
            row.level3_code, row.level3_name, row.source)


def _chain_identity(row: IndustryMembership):
    return (row.level1_code, row.level1_name, row.level2_code, row.level2_name,
            row.level3_code, row.level3_name)


def _latest_candidates(rows: list[IndustryMembership]) -> list[IndustryMembership]:
    """Keep only rows at the latest effective date for one current symbol."""
    dated_rows = [row for row in rows if row.in_date is not None]
    if not dated_rows:
        return rows
    latest_in_date = max(row.in_date for row in dated_rows)
    return [row for row in rows if row.in_date == latest_in_date]


def _build_nodes(rows: list[IndustryMembership], *, enforce_count_ranges: bool) -> list[IndustryNode]:
    nodes: dict[tuple[str, str, str], IndustryNode] = {}
    for row in rows:
        candidates = (
            IndustryNode(row.classification, row.version, row.level1_code, row.level1_name,
                         1, None, row.source),
            IndustryNode(row.classification, row.version, row.level2_code, row.level2_name,
                         2, row.level1_code, row.source),
            IndustryNode(row.classification, row.version, row.level3_code, row.level3_name,
                         3, row.level2_code, row.source),
        )
        for node in candidates:
            key = (node.classification, node.version, node.industry_code)
            existing = nodes.get(key)
            if existing and existing != node:
                raise ProviderValidationError(
                    f"conflicting industry tree node: {node.industry_code}"
                )
            nodes[key] = node
    return validate_industry_nodes(
        list(nodes.values()), enforce_count_ranges=enforce_count_ranges,
    )


def _resolve_memberships(rows: list[IndustryMembership]):
    current = [row for row in rows if row.is_current]
    if not current:
        raise ProviderEmptyDataError("provider returned no current industry memberships")
    groups: dict[str, list[IndustryMembership]] = defaultdict(list)
    for row in current:
        groups[row.symbol].append(row)
    valid: list[IndustryMembership] = []
    conflicts: list[IndustryMembershipConflict] = []
    duplicates = 0
    for symbol in sorted(groups):
        latest = _latest_candidates(groups[symbol])
        unique: dict[tuple, IndustryMembership] = {}
        for row in latest:
            identity = _snapshot_identity(row)
            if identity in unique:
                duplicates += 1
            else:
                unique[identity] = row
        candidates = list(unique.values())
        chains = {_chain_identity(row) for row in candidates}
        if len(chains) > 1:
            conflict = IndustryMembershipConflict(symbol, tuple(candidates))
            conflicts.append(conflict)
            LOGGER.warning(json.dumps({
                "event": "industry_membership_conflict", "symbol": symbol,
                "candidate_count": len(candidates),
                "candidates": [asdict(item) for item in candidates],
                "provider": candidates[0].source,
            }, ensure_ascii=False, default=str, sort_keys=True))
        else:
            valid.append(candidates[0])
    return valid, conflicts, duplicates


def _failed(*, provider: str, fallback_used: bool, dry_run: bool, force: bool,
            warning: str, input_count: int = 0) -> IndustrySyncResult:
    return IndustrySyncResult(
        "failed", provider, fallback_used, 0, input_count, 0, 0, 0, 0,
        (), (), (warning,), dry_run, False, force,
    )


def sync_current_industries(
    *, provider: IndustryDataProvider, repository: IndustryRepository,
    dry_run: bool = False, force: bool = False,
    enforce_count_ranges: bool = True,
) -> IndustrySyncResult:
    provider_name = provider.name
    fallback_used = False
    input_count = 0
    try:
        page = provider.list_memberships(
            classification="SW", version="2021", current_only=True,
        )
        rows = list(page.data)
        provider_name = page.provider
        fallback_used = page.fallback_used
        input_count = len(rows)
        if not rows:
            raise ProviderEmptyDataError("provider returned no memberships")
        nodes = _build_nodes(rows, enforce_count_ranges=enforce_count_ranges)
        valid, conflicts, duplicate_count = _resolve_memberships(rows)
        if not valid:
            raise ProviderEmptyDataError("no conflict-free memberships are available")
        changed = force or not repository.snapshot_matches(nodes=nodes, memberships=valid)
        if not dry_run and changed:
            repository.replace_current_snapshot(nodes=nodes, memberships=valid, force=force)
        status = "partial_success" if conflicts else "success"
        return IndustrySyncResult(
            status, page.provider, page.fallback_used, len(nodes), len(rows), len(valid),
            duplicate_count, len(conflicts), len(conflicts),
            tuple(item.symbol for item in conflicts), tuple(conflicts), page.warnings,
            dry_run, changed, force,
        )
    except (DataSourceError, ValueError) as exc:
        return _failed(
            provider=provider_name, fallback_used=fallback_used, dry_run=dry_run, force=force,
            warning=f"{type(exc).__name__}: {exc}", input_count=input_count,
        )
    except Exception as exc:
        return _failed(
            provider=provider_name, fallback_used=fallback_used, dry_run=dry_run, force=force,
            warning=f"{type(exc).__name__}: industry snapshot sync failed",
            input_count=input_count,
        )
