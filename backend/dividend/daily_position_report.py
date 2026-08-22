"""CSV-only daily dividend position scanner.

This module intentionally has no database, Futu, Eastmoney, or AkShare dependency.
It reads the committed watchlist snapshot and obtains one whole-market Tushare daily
frame for the requested Shanghai trading date.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.dividend.position_levels import position_status


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WATCHLIST_PATH = PROJECT_ROOT / "data" / "dividend" / "dividend_watchlist.csv"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "data" / "dividend" / "daily_position_report.json"
WATCHLIST_HEADER = (
    "symbol", "name", "grade", "entry_yield", "add_yield", "heavy_yield",
    "avg_dps_3y", "enabled", "updated_at",
)
GRADE_ORDER = {"S": 0, "A": 1, "B": 2, None: 3}
POSITION_HINTS = {"S": "about 10%", "A": "about 5%", "B": "about 2%"}


def _optional_float(value: str, field: str, row_number: int) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field} at CSV row {row_number}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"invalid {field} at CSV row {row_number}: {value!r}")
    return parsed


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[dict[str, Any]]:
    """Read and validate the D4A snapshot without touching any database."""
    if not path.exists():
        raise FileNotFoundError(f"watchlist CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != WATCHLIST_HEADER:
            raise ValueError("watchlist CSV header does not match the required schema")
        rows: list[dict[str, Any]] = []
        symbols: set[str] = set()
        for row_number, raw in enumerate(reader, start=2):
            symbol = (raw.get("symbol") or "").strip().upper()
            if not symbol:
                raise ValueError(f"missing symbol at CSV row {row_number}")
            if symbol in symbols:
                raise ValueError(f"duplicate symbol in watchlist CSV: {symbol}")
            symbols.add(symbol)
            enabled_text = (raw.get("enabled") or "").strip().lower()
            if enabled_text not in {"true", "false"}:
                raise ValueError(f"invalid enabled value at CSV row {row_number}: {enabled_text!r}")
            grade = (raw.get("grade") or "").strip().upper() or None
            if grade not in {None, "S", "A", "B"}:
                raise ValueError(f"invalid grade at CSV row {row_number}: {grade!r}")
            rows.append({
                "symbol": symbol,
                "name": (raw.get("name") or "").strip(),
                "grade": grade,
                "entry_yield": _optional_float(raw.get("entry_yield", ""), "entry_yield", row_number),
                "add_yield": _optional_float(raw.get("add_yield", ""), "add_yield", row_number),
                "heavy_yield": _optional_float(raw.get("heavy_yield", ""), "heavy_yield", row_number),
                "avg_dps_3y": _optional_float(raw.get("avg_dps_3y", ""), "avg_dps_3y", row_number),
                "enabled": enabled_text == "true",
                "updated_at": (raw.get("updated_at") or "").strip(),
            })
    return rows


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    if isinstance(frame, list):
        return frame
    raise ValueError("Tushare response is not a tabular frame")


def is_trading_day(client: TushareClient, trade_date: date) -> bool:
    requested = trade_date.strftime("%Y%m%d")
    frame = client.call(
        "trade_cal", exchange="SSE", start_date=requested, end_date=requested,
        fields="cal_date,is_open",
    )
    for item in _records(frame):
        if str(item.get("cal_date", "")).replace("-", "") == requested:
            return str(item.get("is_open", "")).strip() in {"1", "1.0", "True", "true"}
    raise ValueError(f"Tushare trade calendar has no record for {trade_date.isoformat()}")


def fetch_daily_prices(client: TushareClient, trade_date: date) -> dict[str, float]:
    """Make exactly one whole-market daily request for the requested date."""
    frame = client.call(
        "daily", trade_date=trade_date.strftime("%Y%m%d"),
        fields="ts_code,trade_date,close",
    )
    prices: dict[str, float] = {}
    for item in _records(frame):
        symbol = str(item.get("ts_code", "")).strip().upper()
        try:
            close = float(item.get("close"))
        except (TypeError, ValueError):
            continue
        if symbol and math.isfinite(close) and close > 0:
            prices[symbol] = close
    return prices


def _signal_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    return GRADE_ORDER[item["grade"]], item["symbol"]


def scan_watchlist(rows: Iterable[dict[str, Any]], client: TushareClient, trade_date: date) -> dict[str, Any]:
    """Calculate positions. It never retries a prior date when same-day data is absent."""
    all_rows = list(rows)
    enabled = [row for row in all_rows if row["enabled"]]
    report: dict[str, Any] = {
        "trade_date": trade_date.isoformat(),
        "report_status": "completed",
        "watchlist_count": len(all_rows),
        "enabled_count": len(enabled),
        "disabled_count": len(all_rows) - len(enabled),
        "priced_count": 0,
        "missing_price_count": 0,
        "missing_avg_dps_count": 0,
        "watch_count": 0,
        "entry_count": 0,
        "add_count": 0,
        "heavy_count": 0,
        "missing_prices": [],
        "skipped_items": [],
        "add": [],
        "heavy": [],
        "position_hints": POSITION_HINTS,
    }
    if not is_trading_day(client, trade_date):
        report.update(report_status="skipped", skip_reason="non_trading_day")
        return report

    prices = fetch_daily_prices(client, trade_date)
    if not prices:
        report.update(report_status="skipped", skip_reason="market_data_unavailable")
        return report

    signals: list[dict[str, Any]] = []
    for row in enabled:
        close = prices.get(row["symbol"])
        if close is None:
            report["missing_price_count"] += 1
            report["missing_prices"].append({"symbol": row["symbol"], "name": row["name"], "reason": "missing_price"})
            continue
        report["priced_count"] += 1
        if row["avg_dps_3y"] is None:
            report["missing_avg_dps_count"] += 1
            report["skipped_items"].append({"symbol": row["symbol"], "name": row["name"], "reason": "missing_avg_dps_3y"})
            continue
        yield_pct = row["avg_dps_3y"] / close * 100
        status = position_status(yield_pct, row["entry_yield"], row["add_yield"], row["heavy_yield"])
        report[f"{status}_count"] += 1
        signal = {
            **{key: row[key] for key in ("symbol", "name", "grade", "entry_yield", "add_yield", "heavy_yield", "avg_dps_3y")},
            "close": close,
            "three_year_average_yield_pct": yield_pct,
            "status": status,
        }
        signals.append(signal)
    report["heavy"] = sorted((item for item in signals if item["status"] == "heavy"), key=_signal_sort_key)
    report["add"] = sorted((item for item in signals if item["status"] == "add"), key=_signal_sort_key)
    return report


def write_report(report: dict[str, Any], path: Path = DEFAULT_REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"数据日期: {report['trade_date']}",
        f"报告状态: {report['report_status']}",
        f"观察池总行数: {report['watchlist_count']}; enabled: {report['enabled_count']}; disabled: {report['disabled_count']}",
    ]
    if report["report_status"] == "skipped":
        return "\n".join(lines + [f"跳过原因: {report['skip_reason']}"])
    lines.extend([
        f"成功获得价格: {report['priced_count']}; 缺失价格: {report['missing_price_count']}; 缺失 avg_dps_3y: {report['missing_avg_dps_count']}",
        f"watch: {report['watch_count']}; entry: {report['entry_count']}; add: {report['add_count']}; heavy: {report['heavy_count']}",
    ])
    for label, key in (("重仓", "heavy"), ("加仓", "add")):
        lines.append(f"【{label}】")
        for item in report[key]:
            display_item = {**item, "grade": item["grade"] or "unset"}
            lines.append(
                "{symbol} {name} grade={grade} close={close:.4f} avg_dps_3y={avg_dps_3y:.4f} "
                "yield={three_year_average_yield_pct:.2f}% entry={entry_yield} add={add_yield} heavy={heavy_yield} status={status}".format(
                    **display_item
                )
            )
    return "\n".join(lines)


def _default_trade_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CSV-only daily dividend position report")
    parser.add_argument("--date", type=date.fromisoformat, default=_default_trade_date())
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="accepted for GitHub Action dry runs; no side effects beyond JSON output")
    args = parser.parse_args(argv)
    settings = DataSourceSettings.from_env()
    if not settings.tushare_token:
        print("TUSHARE_TOKEN not configured", file=sys.stderr)
        return 2
    try:
        rows = load_watchlist(args.watchlist)
        client = TushareClient(
            settings.tushare_token, timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries, requests_per_minute=settings.requests_per_minute,
        )
        report = scan_watchlist(rows, client, args.date)
        write_report(report, args.output)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"daily position report failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"daily position report failed: {exc}", file=sys.stderr)
        return 1
    print(format_report(report))
    print(f"JSON report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
