"""Read-only Market Breadth feasibility probe; never writes production scores."""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from backend.collector.dividend_collector import get_akshare
from backend.collector.probe_sector_data import find_column

DEFAULT_WORKERS = 4
MAX_WORKERS = 8


@dataclass(frozen=True)
class StockBreadth:
    stock_code: str
    is_up: bool | None
    above_ma5: bool | None
    above_ma20: bool | None
    volume_expanded: bool | None
    at_20d_closing_high: bool | None


def normalize_stock_code(value: object) -> str:
    text = str(value).strip().split(".")[0]
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or len(text) > 6:
        raise ValueError(f"invalid_stock_code: {value}")
    return text.zfill(6)


def sina_symbol(code: str) -> str:
    normalized = normalize_stock_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh{normalized}"
    if normalized.startswith(("0", "1", "2", "3")):
        return f"sz{normalized}"
    if normalized.startswith(("4", "8")):
        return f"bj{normalized}"
    raise ValueError(f"unsupported_stock_code: {normalized}")


def bounded_workers(value: int) -> int:
    return value if 1 <= value <= MAX_WORKERS else DEFAULT_WORKERS


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def calculate_stock_breadth(code: str, bars: pd.DataFrame) -> StockBreadth:
    required = {"date", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError(f"missing_columns: {sorted(required - set(bars.columns))}")
    frame = bars.loc[:, ["date", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date")
    close = frame["close"]
    volume = frame["volume"]

    def valid_tail(series: pd.Series, count: int) -> pd.Series | None:
        tail = series.tail(count)
        return tail if len(tail) == count and all(_finite(item) for item in tail) else None

    close2, close5, close20 = valid_tail(close, 2), valid_tail(close, 5), valid_tail(close, 20)
    volume6 = valid_tail(volume, 6)
    return StockBreadth(
        stock_code=normalize_stock_code(code),
        is_up=None if close2 is None else bool(close2.iloc[-1] > close2.iloc[-2]),
        above_ma5=None if close5 is None else bool(close5.iloc[-1] > close5.mean()),
        above_ma20=None if close20 is None else bool(close20.iloc[-1] > close20.mean()),
        volume_expanded=None if volume6 is None else bool(volume6.iloc[-1] > volume6.iloc[-6:-1].mean()),
        at_20d_closing_high=None if close20 is None else bool(close20.iloc[-1] >= close20.max()),
    )


METRICS = {
    "advancing_ratio": ("is_up", "valid_price_count"),
    "above_ma5_ratio": ("above_ma5", "valid_ma5_count"),
    "above_ma20_ratio": ("above_ma20", "valid_ma20_count"),
    "volume_expansion_ratio": ("volume_expanded", "valid_volume_count"),
    "new_20d_closing_high_ratio": ("at_20d_closing_high", "valid_high20_count"),
}


def aggregate_industry(rows: list[StockBreadth], constituent_count: int) -> dict:
    result = {"constituent_count": constituent_count}
    for ratio_name, (field, count_name) in METRICS.items():
        values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
        result[count_name] = len(values)
        result[ratio_name] = (sum(values) / len(values)) if values else None
    result["breadth_score_preview"] = breadth_preview_score(result)
    result["preview_only"] = True
    return result


def breadth_preview_score(metrics: dict) -> int:
    thresholds = {
        "advancing_ratio": 0.60,
        "above_ma5_ratio": 0.60,
        "above_ma20_ratio": 0.50,
        "volume_expansion_ratio": 0.40,
        "new_20d_closing_high_ratio": 0.15,
    }
    return sum(3 for key, threshold in thresholds.items()
               if metrics.get(key) is not None and metrics[key] >= threshold)


def call_with_retry(operation, attempts: int = 2, delay: float = 1.0):
    last_error = None
    for attempt in range(attempts):
        try:
            frame = operation()
            if frame is None or frame.empty:
                raise RuntimeError("empty_dataframe")
            return frame
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise last_error  # type: ignore[misc]


def load_stock_history(ak, code: str, history_days: int) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=max(60, history_days * 2))
    raw = call_with_retry(lambda: ak.stock_zh_a_daily(
        symbol=sina_symbol(code), start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"), adjust="",
    ))
    return raw.rename(columns={"date": "date", "close": "close", "volume": "volume"}).tail(history_days)


def fetch_constituents(ak, industry_limit: int | None = None):
    industry_frame = call_with_retry(lambda: ak.index_realtime_sw(symbol="一级行业"))
    code_col = find_column(industry_frame, ("指数代码",), "指数代码")
    name_col = find_column(industry_frame, ("指数名称",), "指数名称")
    industries = [(str(row[code_col]).strip(), str(row[name_col]).strip())
                  for _, row in industry_frame.iterrows()]
    if industry_limit is not None:
        industries = industries[:industry_limit]
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows, summaries, errors = [], [], []
    for sector_code, sector_name in industries:
        try:
            frame = call_with_retry(lambda code=sector_code: ak.index_component_sw(symbol=code))
            stock_code_col = find_column(frame, ("证券代码",), "证券代码")
            stock_name_col = find_column(frame, ("证券名称",), "证券名称")
            valid = 0
            for _, item in frame.iterrows():
                try:
                    code = normalize_stock_code(item[stock_code_col])
                except ValueError as exc:
                    errors.append({"sector_code": sector_code, "sector_name": sector_name,
                                   "stock_code": str(item[stock_code_col]), "error": str(exc)})
                    continue
                rows.append({"sector_code": sector_code, "sector_name": sector_name,
                             "stock_code": code, "stock_name": str(item[stock_name_col]).strip(),
                             "membership_source": "akshare.index_component_sw", "fetched_at": fetched_at})
                valid += 1
            summaries.append({"sector_code": sector_code, "sector_name": sector_name,
                              "constituent_count": valid, "status": "success" if valid else "empty"})
        except Exception as exc:
            errors.append({"sector_code": sector_code, "sector_name": sector_name,
                           "stock_code": "", "error": f"{type(exc).__name__}: {exc}"})
            summaries.append({"sector_code": sector_code, "sector_name": sector_name,
                              "constituent_count": 0, "status": "failed"})
    return rows, summaries, errors


def probe_histories(ak, memberships: list[dict], stock_limit: int | None, history_days: int, workers: int):
    selected = []
    for sector_code in sorted({row["sector_code"] for row in memberships}):
        sector_rows = sorted((row for row in memberships if row["sector_code"] == sector_code),
                             key=lambda row: row["stock_code"])
        selected.extend(sector_rows[:stock_limit] if stock_limit else sector_rows)
    codes = sorted({row["stock_code"] for row in selected})
    histories, statuses, errors = {}, [], []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=bounded_workers(workers), thread_name_prefix="breadth-history") as executor:
        futures = {executor.submit(load_stock_history, ak, code, history_days): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                bars = future.result()
                metric = calculate_stock_breadth(code, bars)
                histories[code] = metric
                statuses.append({"stock_code": code, "status": "success", "row_count": len(bars), "error": ""})
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                statuses.append({"stock_code": code, "status": "failed", "row_count": 0, "error": message})
                errors.append({"stock_code": code, "error": message})
    ordered_histories = {code: histories[code] for code in sorted(histories)}
    return (selected, ordered_histories, sorted(statuses, key=lambda row: row["stock_code"]),
            sorted(errors, key=lambda row: row["stock_code"]), round(time.monotonic() - started, 2))


def write_outputs(output_dir: Path, memberships, summaries, constituent_errors,
                  selected, histories, history_status, history_errors, timing, metadata):
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(memberships).sort_values(["sector_code", "stock_code"]).to_csv(
        output_dir / "industry_constituents.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(summaries).sort_values("sector_code").to_csv(
        output_dir / "industry_constituent_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(constituent_errors, columns=["sector_code", "sector_name", "stock_code", "error"]).to_csv(
        output_dir / "industry_constituent_errors.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(history_status).to_csv(output_dir / "stock_history_status.csv", index=False, encoding="utf-8-sig")
    breadth_rows = []
    for sector_code in sorted({row["sector_code"] for row in selected}):
        sector = [row for row in selected if row["sector_code"] == sector_code]
        values = [histories[row["stock_code"]] for row in sector if row["stock_code"] in histories]
        breadth_rows.append({"sector_code": sector_code, "sector_name": sector[0]["sector_name"],
                             **aggregate_industry(values, len(sector))})
    pd.DataFrame(breadth_rows).to_csv(output_dir / "industry_breadth_preview.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(history_errors, columns=["stock_code", "error"]).to_csv(
        output_dir / "breadth_errors.csv", index=False, encoding="utf-8-sig")
    (output_dir / "probe_timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "probe_summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = (
        f"Market Breadth feasibility probe\nData source: AKShare {metadata['akshare_version']}\n"
        f"Membership: index_realtime_sw + index_component_sw\nHistory: stock_zh_a_daily (Sina), unadjusted\n"
        f"Industry coverage: {metadata['industries_with_constituents']}/{metadata['industry_count']}\n"
        f"Stock coverage: {metadata['history_coverage_pct']:.2f}%\nMissing: see CSV error files\n"
        "Production recommendation: feasibility evidence only; do not replace Capital Flow yet.\n"
        "Local history storage recommendation: yes for full-market daily refresh efficiency.\n"
    )
    (output_dir / "README.txt").write_text(readme, encoding="utf-8")
    return breadth_rows


def run_probe(ak, *, industry_limit=None, stock_limit=None, history_days=40, workers=4, output_dir: Path):
    started = time.monotonic()
    membership_started = time.monotonic()
    memberships, summaries, constituent_errors = fetch_constituents(ak, industry_limit)
    membership_seconds = round(time.monotonic() - membership_started, 2)
    selected, histories, statuses, history_errors, history_seconds = probe_histories(
        ak, memberships, stock_limit, history_days, workers)
    success = len(histories)
    requested = len(statuses)
    metadata = {
        "akshare_version": getattr(ak, "__version__", "unknown"),
        "industry_interface": "index_realtime_sw",
        "constituent_interface": "index_component_sw",
        "history_interface": "stock_zh_a_daily",
        "history_adjustment": "unadjusted",
        "batch_history_interface_available": False,
        "industry_count": len(summaries),
        "industries_with_constituents": sum(row["constituent_count"] > 0 for row in summaries),
        "constituent_records": len(memberships),
        "unique_stocks": len({row["stock_code"] for row in memberships}),
        "history_requested": requested, "history_success": success,
        "history_failed": requested - success,
        "history_coverage_pct": (success / requested * 100) if requested else 0,
        "preview_only": True, "writes_production_scores": False,
    }
    timing = {"membership_seconds": membership_seconds, "history_seconds": history_seconds,
              "total_seconds": round(time.monotonic() - started, 2), "workers": bounded_workers(workers)}
    breadth = write_outputs(output_dir, memberships, summaries, constituent_errors, selected,
                            histories, statuses, history_errors, timing, metadata)
    print(json.dumps({**metadata, **timing, "output_dir": str(output_dir)}, ensure_ascii=False, indent=2))
    return metadata, breadth


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description="Market Breadth feasibility probe")
    parser.add_argument("--industry-limit", type=int)
    parser.add_argument("--stock-limit", type=int)
    parser.add_argument("--history-days", type=int, default=40)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or Path("data/audits") / f"market_breadth_{timestamp}"
    run_probe(get_akshare(), industry_limit=args.industry_limit, stock_limit=args.stock_limit,
              history_days=args.history_days, workers=args.workers, output_dir=output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
