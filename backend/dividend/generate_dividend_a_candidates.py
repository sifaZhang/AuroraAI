from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.expectation_gap.database import connect_readonly, database_path
from .dividend_candidate_service import DividendCandidateService, TushareDividendProvider
from .dividend_candidate_rules import target_years


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate A-class stable dividend candidates.")
    parser.add_argument("--calculation-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", default="exports/dividend/dividend_a_candidates.csv")
    parser.add_argument("--exclusions-output", default="exports/dividend/dividend_a_candidate_exclusions.csv")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", help="Comma-separated canonical symbols, e.g. 601398.SH,600900.SH")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def write_exports(candidates, exclusions, output: Path, exclusions_output: Path) -> None:
    for frame, path in ((candidates, output), (exclusions, exclusions_output)):
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    symbols = {value.strip().upper() for value in args.symbols.split(",") if value.strip()} if args.symbols else None
    settings = DataSourceSettings.from_env()
    provider = TushareDividendProvider(TushareClient(settings.tushare_token, timeout_seconds=settings.request_timeout_seconds, max_retries=settings.max_retries, requests_per_minute=settings.requests_per_minute))
    try:
        connection = connect_readonly(args.database or database_path())
        candidates, exclusions, summary = DividendCandidateService(connection, provider).generate(args.calculation_date, symbols=symbols, limit=args.limit)
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if "connection" in locals(): connection.close()
    print("A股A类稳定分红候选池生成完成")
    print(f"计算日期: {args.calculation_date.isoformat()}")
    print("目标年度: " + ", ".join(map(str, target_years(args.calculation_date))))
    for key, value in summary.items(): print(f"{key}: {value}")
    if not args.dry_run:
        write_exports(candidates, exclusions, Path(args.output), Path(args.exclusions_output))
        print(f"候选文件: {args.output}")
        print(f"排除明细: {args.exclusions_output}")
    if args.strict and any(exclusions["exclusion_reason"].isin(["data_source_error", "industry_unknown"])):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
