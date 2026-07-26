"""Small, read-only GM API probe for PR6.0; it never writes AuroraAI data."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.expectation_gap.database import connect
from backend.market_data.sector_history_repository import list_current_member_stocks

DEFAULT_SAMPLES = ("SHSE.600000", "SZSE.000001", "SZSE.300750", "SHSE.688001", "SHSE.600145")
CONTROLLED_BATCH_SIZES = (1, 10, 50, 200)


def _frame_summary(frame: Any) -> dict[str, Any]:
    columns = list(getattr(frame, "columns", []))
    sample = []
    if isinstance(frame, list):
        sample = [sorted(item.keys()) for item in frame[:3] if isinstance(item, dict)]
        columns = sorted({key for item in frame if isinstance(item, dict) for key in item})
    values = []
    fields = ("symbol", "trade_date", "board", "is_suspended", "listed_date", "pre_close", "upper_limit", "lower_limit")
    if isinstance(frame, list):
        values = [{key: item[key] for key in fields if key in item} for item in frame[:3] if isinstance(item, dict)]
    return {"rows": len(frame) if frame is not None else 0, "columns": [str(x) for x in columns], "sample_field_sets": sample, "sample_values": values}


def _json_default(value: Any) -> str:
    """Serialize provider-specific date/time and scalar objects without exposing secrets."""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _capture(call) -> dict[str, Any]:
    try:
        return {"status": "ok", **_frame_summary(call())}
    except Exception as exc:
        return {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}


def probe_request_limits(api: Any, symbols: tuple[str, ...], start: str, end: str) -> dict[str, Any]:
    """Bounded metadata-only request and rate probe; never retries or loops indefinitely."""
    attempts = []
    for size in CONTROLLED_BATCH_SIZES:
        batch = symbols[:size]
        if len(batch) < size:
            continue
        started = time.monotonic()
        captured = _capture(lambda: api.get_history_instruments(symbols=list(batch), start_date=start, end_date=end, df=False))
        captured.update({"requested_symbols": len(batch), "elapsed_seconds": round(time.monotonic() - started, 3)})
        attempts.append(captured)
        if captured["status"] == "error":
            break
    sequential = []
    for _ in range(3):
        started = time.monotonic()
        captured = _capture(lambda: api.get_trading_dates("SHSE", start, end))
        captured["elapsed_seconds"] = round(time.monotonic() - started, 3)
        sequential.append(captured)
        if captured["status"] == "error":
            break
    return {"batch_history_instruments": attempts, "sequential_calendar_requests": sequential,
            "note": "This is a bounded observation, not a load test; use the first error as a conservative batch cap."}


def local_a_share_symbols(limit: int = 200) -> tuple[str, ...]:
    """Read up to ``limit`` ordinary A-share symbols from the local pool without writing it."""
    connection = connect()
    try:
        codes = [str(row["stock_code"]).zfill(6) for row in list_current_member_stocks(connection)]
    finally:
        connection.close()
    symbols = []
    for code in codes:
        exchange = "SHSE" if code.startswith(("5", "6", "9")) else "SZSE" if code.startswith(("0", "1", "2", "3")) else None
        if exchange:
            symbols.append(f"{exchange}.{code}")
        if len(symbols) >= limit:
            break
    return tuple(symbols)


def probe(
    api: Any, symbols: tuple[str, ...], daily_start: str, end: str, *, token: str,
    minute_start: str | None = None, batch_symbols: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Probe authenticated read APIs; the token is never included in the report."""
    api.set_token(token)
    minute_start = minute_start or daily_start
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "windows": {"daily_start": daily_start, "minute_start": minute_start, "end": end},
        "samples": {},
    }
    for symbol in symbols:
        result: dict[str, Any] = {
            "current_instrument": _capture(lambda: api.get_instruments(
                symbols=symbol, skip_suspended=False, skip_st=False, df=False)),
            "history_instrument": _capture(lambda: api.get_history_instruments(
                symbols=symbol, start_date=daily_start[:10], end_date=end[:10], df=False)),
        }
        for frequency, fields, start in (
            ("1d", "open,high,low,close,volume,amount,pre_close", daily_start),
            ("60s", "open,high,low,close,volume,amount", minute_start),
        ):
            try:
                frame = api.history(symbol=symbol, frequency=frequency, start_time=start, end_time=end,
                                    fields=fields, adjust=0, df=True)
                result[frequency] = {"status": "ok", **_frame_summary(frame)}
            except Exception as exc:  # provider-specific errors are reportable evidence
                result[frequency] = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
        report["samples"][symbol] = result
    report["trading_calendar"] = {
        "SHSE": _capture(lambda: api.get_trading_dates("SHSE", daily_start[:10], end[:10])),
        "SZSE": _capture(lambda: api.get_trading_dates("SZSE", daily_start[:10], end[:10])),
    }
    if batch_symbols:
        report["request_limits"] = probe_request_limits(api, batch_symbols, daily_start[:10], end[:10])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only GM API capability probe")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SAMPLES))
    parser.add_argument("--batch-symbols", help="comma-separated symbols for bounded 1/10/50/200 metadata checks")
    parser.add_argument("--use-local-stock-pool", action="store_true", help="read up to 200 existing local A-share codes for the bounded batch check")
    parser.add_argument("--daily-start", default="2020-01-01 09:30:00")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d 15:00:00"))
    parser.add_argument("--minute-start", help="override the default 175-day minute-bar window")
    parser.add_argument("--output", type=Path, default=Path("reports/pr6_0_gm_capabilities.json"))
    parser.add_argument("--token-env", default="GM_TOKEN", help="environment variable holding the GM token")
    args = parser.parse_args(argv)
    try:
        from gm import api
    except ImportError as exc:
        raise SystemExit("gm.api is not installed; install/configure GM Python SDK before running this probe") from exc
    token = os.getenv(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"set {args.token_env} before running this read-only probe")
    try:
        end_at = datetime.fromisoformat(args.end)
    except ValueError as exc:
        raise SystemExit("--end must use YYYY-MM-DD HH:MM:SS") from exc
    minute_start = args.minute_start or (end_at - timedelta(days=175)).strftime("%Y-%m-%d 09:30:00")
    if args.batch_symbols and args.use_local_stock_pool:
        parser.error("choose only one of --batch-symbols or --use-local-stock-pool")
    batch_symbols = tuple(item.strip() for item in (args.batch_symbols or "").split(",") if item.strip())
    if args.use_local_stock_pool:
        batch_symbols = local_a_share_symbols()
        if len(batch_symbols) < 200:
            raise SystemExit("local A-share pool has fewer than 200 supported SHSE/SZSE symbols")
    report = probe(
        api, tuple(item.strip() for item in args.symbols.split(",") if item.strip()),
        args.daily_start, args.end, token=token, minute_start=minute_start,
        batch_symbols=batch_symbols,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
