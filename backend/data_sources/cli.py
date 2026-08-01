"""Read-only command line probes for the unified provider layer."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .registry import build_industry_provider, get_data_source_health


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


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
    args = parser.parse_args(argv)
    if args.command == "industry-health":
        _print([asdict(item) for item in get_data_source_health()])
        return 0
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
