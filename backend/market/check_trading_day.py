from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.data_sources.errors import DataSourceError
from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient

from .trading_calendar import is_a_share_trading_day


def beijing_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check one A-share trading date")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    trade_date = args.date or beijing_today()
    try:
        settings = DataSourceSettings.from_env()
        if not settings.tushare_token:
            raise ValueError("TUSHARE_TOKEN not configured")
        client = TushareClient(
            settings.tushare_token, timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries, requests_per_minute=settings.requests_per_minute,
        )
        is_open = is_a_share_trading_day(client, trade_date)
        value = "true" if is_open else "false"
        print(f"trade_date={trade_date.isoformat()}")
        print(f"is_trading_day={value}")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"trade_date={trade_date.isoformat()}\n")
                handle.write(f"is_trading_day={value}\n")
        return 0
    except (DataSourceError, OSError, ValueError) as exc:
        print(f"trading day check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
