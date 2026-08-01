"""Industry data contract validation."""

from __future__ import annotations

from collections import defaultdict

from .errors import ProviderEmptyDataError, ProviderValidationError
from .models import IndustryMembership, IndustryNode

LEVEL_RANGES = {1: (20, 60), 2: (80, 180), 3: (250, 500)}


def validate_industry_nodes(
    nodes: list[IndustryNode], *, require_complete_tree: bool = True,
    enforce_count_ranges: bool = True,
) -> list[IndustryNode]:
    if not nodes:
        raise ProviderEmptyDataError("industry catalog is empty")
    identities: dict[str, tuple[str, int]] = {}
    codes = {node.industry_code for node in nodes}
    counts = defaultdict(int)
    for node in nodes:
        if node.classification != "SW" or node.version != "2021":
            raise ProviderValidationError("unsupported industry classification or version")
        if node.industry_level not in {1, 2, 3} or not node.industry_code or not node.industry_name:
            raise ProviderValidationError("invalid industry node")
        identity = (node.industry_name, node.industry_level)
        if node.industry_code in identities and identities[node.industry_code] != identity:
            raise ProviderValidationError(f"conflicting industry code: {node.industry_code}")
        identities[node.industry_code] = identity
        counts[node.industry_level] += 1
        if node.industry_level == 1 and node.parent_code is not None:
            raise ProviderValidationError("level-1 industry must not have a parent")
        if require_complete_tree and node.industry_level > 1 and node.parent_code not in codes:
            raise ProviderValidationError(f"missing parent for industry: {node.industry_code}")
    if enforce_count_ranges:
        for level, (minimum, maximum) in LEVEL_RANGES.items():
            if counts[level] and not minimum <= counts[level] <= maximum:
                raise ProviderValidationError(f"unreasonable level-{level} industry count: {counts[level]}")
    return nodes


def validate_memberships(rows: list[IndustryMembership]) -> list[IndustryMembership]:
    if not rows:
        raise ProviderEmptyDataError("industry memberships are empty")
    unique: dict[tuple[str, str, object, object], IndustryMembership] = {}
    current_by_symbol: dict[str, str] = {}
    for row in rows:
        if not all((row.symbol, row.level1_code, row.level1_name, row.level2_code,
                    row.level2_name, row.level3_code, row.level3_name)):
            raise ProviderValidationError("incomplete industry membership chain")
        if row.in_date and row.out_date and row.in_date > row.out_date:
            raise ProviderValidationError("membership in_date is after out_date")
        if row.is_current:
            existing = current_by_symbol.get(row.symbol)
            if existing and existing != row.level3_code:
                raise ProviderValidationError(f"multiple current level-3 memberships: {row.symbol}")
            current_by_symbol[row.symbol] = row.level3_code
        key = (row.symbol, row.level3_code, row.in_date, row.out_date)
        existing = unique.get(key)
        if existing and existing != row:
            raise ProviderValidationError(f"conflicting duplicate membership: {row.symbol}")
        unique[key] = row
    return list(unique.values())
