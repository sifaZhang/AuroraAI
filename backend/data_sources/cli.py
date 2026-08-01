"""Read-only command line probes for the unified provider layer."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .registry import build_industry_provider, get_data_source_health
from .industry_sync import IndustryRepository, sync_current_industries
from backend.expectation_gap.database import connect, connect_readonly, migrate


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


class _EmptyDryRunRepository:
    def snapshot_matches(self, **_kwargs):
        return False


def _dry_run_repository():
    try:
        connection = connect_readonly()
    except FileNotFoundError:
        return _EmptyDryRunRepository(), None
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if {"industry_nodes", "industry_memberships_current"} <= tables:
        return IndustryRepository(connection), connection
    connection.close()
    return _EmptyDryRunRepository(), None


def _sync_output(result):
    return {
        "status": result.status, "provider": result.provider,
        "fallback_used": result.fallback_used, "node_count": result.node_count,
        "membership_input_count": result.membership_input_count,
        "membership_written_count": result.membership_written_count,
        "duplicate_count": result.duplicate_count, "conflict_count": result.conflict_count,
        "skipped_count": result.skipped_count,
        "conflict_symbols": result.conflict_symbols, "warnings": result.warnings,
        "dry_run": result.dry_run, "changed": result.changed, "forced": result.forced,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AuroraAI unified data-source probe")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("industry-health")
    preview = commands.add_parser("industry-preview")
    preview.add_argument("--provider", choices=("auto", "tushare", "akshare"), default="auto")
    preview.add_argument("--level", type=int, choices=(1, 2, 3))
    preview.add_argument("--limit", type=int, default=20)
    symbol = commands.add_parser("symbol-industry")
    symbol.add_argument("--provider", choices=("auto", "tushare", "akshare"), default="auto")
    symbol.add_argument("--symbol", required=True)
    sync = commands.add_parser("sync-industries")
    sync.add_argument("--provider", choices=("auto", "tushare", "akshare"), default="auto")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--force", action="store_true")
    sync.add_argument("--export-conflicts", type=Path)
    db_symbol = commands.add_parser("db-symbol-industry")
    db_symbol.add_argument("--symbol", required=True)
    constituents = commands.add_parser("db-industry-constituents")
    constituents.add_argument("--industry-code", required=True)
    constituents.add_argument("--level", type=int, choices=(1, 2, 3), required=True)
    constituents.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "industry-health":
        _print([asdict(item) for item in get_data_source_health()])
        return 0
    if args.command == "sync-industries":
        provider = build_industry_provider(provider=args.provider)
        if args.dry_run:
            repository, connection = _dry_run_repository()
        else:
            connection = connect(); migrate(connection); repository = IndustryRepository(connection)
        try:
            result = sync_current_industries(
                provider=provider, repository=repository,
                dry_run=args.dry_run, force=args.force,
            )
        finally:
            if connection is not None:
                connection.close()
        if args.export_conflicts:
            args.export_conflicts.write_text(json.dumps(
                [asdict(item) for item in result.conflicts], ensure_ascii=False,
                indent=2, default=str,
            ), encoding="utf-8")
        _print(_sync_output(result))
        return {"success": 0, "partial_success": 1, "failed": 2}[result.status]
    if args.command in {"db-symbol-industry", "db-industry-constituents"}:
        try:
            connection = connect_readonly()
            repository = IndustryRepository(connection)
            if args.command == "db-symbol-industry":
                item = repository.get_symbol_membership(args.symbol)
                _print(asdict(item) if item else None)
            else:
                items = repository.list_constituents(
                    args.industry_code, level=args.level,
                )
                _print([asdict(item) for item in items[:max(0, args.limit)]])
            return 0
        except Exception as exc:
            _print({"error": f"{type(exc).__name__}: {exc}"})
            return 2
        finally:
            if "connection" in locals():
                connection.close()
    provider = build_industry_provider(provider=args.provider)
    if args.command == "industry-preview":
        result = provider.list_industries(classification="SW", version="2021", level=args.level)
        _print({"provider": result.provider, "fallback_used": result.fallback_used,
                "row_count": result.row_count,
                "items": [asdict(item) for item in result.data[:max(0, args.limit)]]})
        return 0
    result = provider.get_symbol_membership(
        args.symbol, classification="SW", version="2021"
    )
    _print({"provider": result.provider, "fallback_used": result.fallback_used,
            "item": asdict(result.data) if result.data else None})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
