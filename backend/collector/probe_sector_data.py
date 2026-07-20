"""Read-only feasibility probe for the SW level-one Sector Trend Radar."""

from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from backend.collector.dividend_collector import get_akshare
from backend.sector_radar.scoring import (
    calculate_trend_score,
    normalize_daily_bars,
    trend_stars,
)

SW_RETRY_DELAYS = (2.0, 5.0)


@dataclass(frozen=True)
class Industry:
    code: str
    name: str


@dataclass(frozen=True)
class TrendResult:
    industry: Industry
    score: int
    trade_date: str
    close: float


def find_column(frame: pd.DataFrame, aliases: tuple[str, ...], label: str) -> object:
    normalized = {str(column).strip().lower(): column for column in frame.columns}
    for alias in aliases:
        found = normalized.get(alias.lower())
        if found is not None:
            return found
    raise ValueError(f"缺少{label}字段；真实字段为: {[str(c) for c in frame.columns]}")


def call_with_retry(label: str, operation: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    errors: list[str] = []
    for attempt in range(len(SW_RETRY_DELAYS) + 1):
        started = time.monotonic()
        try:
            frame = operation()
            if frame is None or frame.empty:
                raise RuntimeError("接口返回空数据")
            print(f"  {label}: 成功，{time.monotonic() - started:.2f}s")
            return frame
        except Exception as exc:
            error = f"第{attempt + 1}次 {type(exc).__name__}: {exc}"
            errors.append(error)
            print(f"  {label}: {error}", file=sys.stderr)
            if attempt < len(SW_RETRY_DELAYS):
                delay = SW_RETRY_DELAYS[attempt]
                print(f"  {delay:.0f}s 后重试", file=sys.stderr)
                time.sleep(delay)
    raise RuntimeError(f"{label}失败；" + " | ".join(errors))


def load_industries(ak: object) -> tuple[pd.DataFrame, list[Industry]]:
    frame = call_with_retry(
        "SW一级行业列表",
        lambda: ak.index_realtime_sw(symbol="一级行业"),
    )
    code_col = find_column(frame, ("指数代码",), "指数代码")
    name_col = find_column(frame, ("指数名称",), "指数名称")
    industries = [
        Industry(str(row[code_col]).strip(), str(row[name_col]).strip())
        for _, row in frame.iterrows()
        if str(row[code_col]).strip() and str(row[name_col]).strip()
    ]
    if not industries:
        raise RuntimeError("SW一级行业列表没有有效行业")
    return frame, industries


def load_history(ak: object, industry: Industry) -> pd.DataFrame:
    raw = call_with_retry(
        f"{industry.code} {industry.name}",
        lambda: ak.index_hist_sw(symbol=industry.code, period="day"),
    )
    bars = normalize_daily_bars(
        raw,
        date_column=find_column(raw, ("日期", "date"), "日期"),
        close_column=find_column(raw, ("收盘", "收盘价", "close"), "收盘价"),
        volume_column=find_column(raw, ("成交量", "volume"), "成交量"),
    ).tail(120)
    if len(bars) < 21:
        raise ValueError(f"{industry.code} {industry.name}有效K线不足：{len(bars)}")
    return bars


def run_probe() -> list[TrendResult]:
    started = time.monotonic()
    ak = get_akshare()
    print("Sector Trend Radar Probe")
    print(f"Python版本: {platform.python_version()}")
    print(f"AKShare版本: {getattr(ak, '__version__', 'unknown')}")
    print("数据源: 申万一级行业（SW）")

    list_started = time.monotonic()
    frame, industries = load_industries(ak)
    print(f"板块数量: {len(industries)}")
    print(f"字段名称: {[str(column) for column in frame.columns]}")
    with pd.option_context("display.max_columns", None, "display.width", 180):
        print("前5条数据:")
        print(frame.head(5).to_string(index=False))
    print(f"行业列表耗时: {time.monotonic() - list_started:.2f}s")

    history_started = time.monotonic()
    results: list[TrendResult] = []
    errors: list[str] = []
    for position, industry in enumerate(industries, start=1):
        try:
            bars = load_history(ak, industry)
            latest = bars.iloc[-1]
            results.append(
                TrendResult(
                    industry=industry,
                    score=calculate_trend_score(bars),
                    trade_date=pd.Timestamp(latest["date"]).date().isoformat(),
                    close=float(latest["close"]),
                )
            )
        except Exception as exc:
            message = f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"
            errors.append(message)
            print(f"ERROR {message}", file=sys.stderr)
        print(f"Trend进度: {position}/{len(industries)}，成功: {len(results)}")

    if errors:
        print("失败行业:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise RuntimeError(f"31个SW一级行业未完整成功，失败{len(errors)}个")

    results.sort(key=lambda item: (-item.score, item.industry.name))
    print("\nTrend Top20（满分70）")
    for item in results[:20]:
        print(
            f"{trend_stars(item.score)} {item.industry.name:<12} "
            f"Trend {item.score:>2}/70  {item.trade_date}"
        )

    elapsed = time.monotonic() - started
    print("\nProbe summary")
    print(f"板块数量: {len(industries)}")
    print(f"成功行业数量: {len(results)}")
    print(f"成功K线数量: {len(results)}")
    print(f"趋势计算数量: {len(results)}")
    print(f"行业K线及评分耗时: {time.monotonic() - history_started:.2f}s")
    print(f"实际总耗时: {elapsed:.2f}s")
    print("Probe completed successfully.")
    return results


def main() -> int:
    try:
        run_probe()
    except Exception as exc:
        print(f"Probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
