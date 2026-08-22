"""Command-line entry point for rebuilding the dividend watchlist snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.dividend.watchlist_csv import WATCHLIST_CSV_PATH, export_dividend_watchlist_csv
from backend.expectation_gap.database import connect_readonly


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the dividend observation pool CSV snapshot.")
    parser.add_argument("--output", type=Path, default=WATCHLIST_CSV_PATH)
    args = parser.parse_args()
    connection = connect_readonly()
    try:
        result = export_dividend_watchlist_csv(connection, args.output)
    finally:
        connection.close()
    print(f"Exported {result['row_count']} rows to {result['path']}")


if __name__ == "__main__":
    main()
