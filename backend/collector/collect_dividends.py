"""Command line entry point for dividend yield collection."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from backend.analysis.dividend_yield import calculate_dividend_top20, calculate_dividend_yield
from backend.collector.dividend_collector import collect_dividend_candidates


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
    else:
        result = calculate_dividend_yield(dividends, prices, mode=args.mode, today=as_of_date)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, encoding="utf-8-sig")

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


if __name__ == "__main__":
    main()
