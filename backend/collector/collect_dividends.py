"""Command line entry point for dividend yield collection."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.analysis.dividend_yield import calculate_dividend_top20, calculate_dividend_yield
from backend.collector.dividend_collector import collect_dividend_candidates


TOP20_OUTPUT_COLUMNS = [
    "\u6392\u540d",
    "\u767b\u8bb0\u65e5",
    "\u80a1\u7968",
    "\u6bcf10\u80a1\u6d3e\u606f",
    "\u6700\u65b0\u80a1\u4ef7",
    "\u672c\u6b21\u80a1\u606f\u7387",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect dividend data and calculate dividend yield.")
    parser.add_argument("--limit", type=int, default=200, help="Number of A-share stocks to scan. Use 0 for all.")
    parser.add_argument(
        "--codes",
        help="Comma-separated stock codes, for example: 000001,600519. This avoids full-market scanning.",
    )
    parser.add_argument(
        "--price-overrides",
        help="Comma-separated current prices, for example: 000001=10.50,600519=1500.00.",
    )
    parser.add_argument(
        "--mode",
        choices=["top20", "latest", "trailing_12m"],
        default="top20",
        help="top20 keeps only upcoming record dates; latest/trailing_12m output raw calculation tables.",
    )
    parser.add_argument("--as-of-date", help="Filter date for top20, format YYYY-MM-DD. Default: today.")
    parser.add_argument("--include-tushare", action="store_true", help="Also query Tushare Pro when TUSHARE_TOKEN is set.")
    parser.add_argument("--refresh-prices", action="store_true", help="Fetch latest prices one by one instead of using source fallback prices.")
    parser.add_argument("--top", type=int, default=20, help="Number of rows to print.")
    parser.add_argument(
        "--output",
        default="data/dividend_yield.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--metadata-output",
        help="Optional JSON metadata output path for the UI.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = None if args.limit == 0 else args.limit
    stock_codes = [code.strip() for code in args.codes.split(",") if code.strip()] if args.codes else None
    price_overrides = parse_price_overrides(args.price_overrides)
    as_of_date = parse_as_of_date(args.as_of_date)

    print("Collecting dividend and price data...", flush=True)
    try:
        dividends, prices = collect_dividend_candidates(
            limit=limit,
            include_tushare=args.include_tushare,
            stock_codes=stock_codes,
            price_overrides=price_overrides,
            as_of_date=as_of_date,
            refresh_prices=args.refresh_prices,
        )
    except RuntimeError as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        print(
            "Tip: if full-market price APIs are unstable, use --codes with --price-overrides "
            "to test selected stocks first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Collected {len(dividends)} dividend rows and {len(prices)} price rows.", flush=True)
    if args.mode == "top20":
        result = calculate_dividend_top20(dividends, prices, as_of_date=as_of_date, top=args.top)
        if len(result.columns) == len(TOP20_OUTPUT_COLUMNS):
            result.columns = TOP20_OUTPUT_COLUMNS
    else:
        result = calculate_dividend_yield(dividends, prices, mode=args.mode, today=as_of_date)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, encoding="utf-8-sig")
    if args.metadata_output:
        write_metadata(
            Path(args.metadata_output),
            row_count=len(result),
            as_of_date=as_of_date,
            output_path=output,
        )

    print(f"Saved {len(result)} rows to {output}")
    if not result.empty:
        print(result.head(args.top).to_string(index=False))


def parse_price_overrides(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}

    prices: dict[str, float] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(f"Invalid price override: {item}. Expected CODE=PRICE.")
        code, price = item.split("=", 1)
        prices[code.strip()] = float(price.strip())
    return prices


def parse_as_of_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def write_metadata(output: Path, *, row_count: int, as_of_date: date | None, output_path: Path) -> None:
    beijing_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    metadata = {
        "generated_at": beijing_now.isoformat(timespec="seconds"),
        "generated_at_label": beijing_now.strftime("%Y-%m-%d %H:%M:%S \u5317\u4eac\u65f6\u95f4"),
        "as_of_date": (as_of_date or beijing_now.date()).isoformat(),
        "row_count": row_count,
        "data_file": output_path.name,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
