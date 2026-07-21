"""Read-only SW level-two and Eastmoney industry trend probe."""

from __future__ import annotations

import argparse
import platform
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable, Literal

import pandas as pd

from backend.collector.dividend_collector import get_akshare
from backend.sector_radar.scoring import calculate_trend_metrics, normalize_daily_bars, trend_stars

SourceName = Literal["sw_l1", "sw_l2", "eastmoney"]
SourceState = Literal["available", "partial", "unavailable"]
SW_DELAYS = (2.0, 5.0)
EASTMONEY_DELAYS = (5.0, 15.0, 30.0)


@dataclass(frozen=True)
class Industry:
    source: SourceName
    code: str
    name: str
    level: int | str

    @property
    def unique_key(self) -> str:
        return f"{self.source}:{self.code}"


@dataclass(frozen=True)
class SectorTrend:
    source: SourceName
    sector_code: str
    sector_name: str
    sector_level: int | str
    trade_date: str
    trend_score: int
    trend_level: str
    close: float
    ma5: float
    ma10: float
    ma20: float
    volume_ratio: float
    is_20d_high: bool
    relative_strength_score: int | None = None
    benchmark_code: str | None = None
    benchmark_trade_date: str | None = None
    sector_return_5d: float | None = None
    benchmark_return_5d: float | None = None
    excess_return_5d: float | None = None
    sector_return_10d: float | None = None
    benchmark_return_10d: float | None = None
    excess_return_10d: float | None = None
    sector_return_20d: float | None = None
    benchmark_return_20d: float | None = None
    excess_return_20d: float | None = None
    relative_strength_updated_at: str | None = None
    capital_flow_score: int | None = None
    composite_score: int | None = None
    score_status: str = "partial"
    missing_components: tuple[str, ...] = ("capital_flow", "relative_strength")


@dataclass(frozen=True)
class SourceStatus:
    source: SourceName
    status: SourceState
    sector_count: int
    successful_sector_count: int
    failed_sector_count: int
    last_error: str | None
    elapsed_seconds: float


@dataclass(frozen=True)
class SourceResult:
    status: SourceStatus
    trends: tuple[SectorTrend, ...]


def find_column(frame: pd.DataFrame, aliases: tuple[str, ...], label: str) -> object:
    normalized = {str(column).strip().lower(): column for column in frame.columns}
    for alias in aliases:
        found = normalized.get(alias.lower())
        if found is not None:
            return found
    raise ValueError(f"缺少{label}字段；真实字段为: {[str(c) for c in frame.columns]}")


def call_with_retry(
    label: str,
    operation: Callable[[], pd.DataFrame],
    delays: tuple[float, ...],
) -> pd.DataFrame:
    errors: list[str] = []
    for attempt in range(len(delays) + 1):
        started = time.monotonic()
        try:
            frame = operation()
            if frame is None or frame.empty:
                raise RuntimeError("接口返回空数据")
            print(f"  {label}: 成功，{time.monotonic() - started:.2f}s")
            return frame
        except Exception as exc:
            message = f"第{attempt + 1}次 {type(exc).__name__}: {exc}"
            errors.append(message)
            print(f"  {label}: {message}", file=sys.stderr)
            if attempt < len(delays):
                delay = delays[attempt]
                print(f"  {delay:.0f}s 后重试", file=sys.stderr)
                time.sleep(delay)
    raise RuntimeError(f"{label}失败；" + " | ".join(errors))


def load_industries(ak: object, source: SourceName) -> tuple[pd.DataFrame, list[Industry]]:
    if source in {"sw_l1", "sw_l2"}:
        sw_level = 1 if source == "sw_l1" else 2
        frame = call_with_retry(
            f"SW{sw_level}级行业列表",
            lambda: ak.index_realtime_sw(symbol="一级行业" if source == "sw_l1" else "二级行业"),
            SW_DELAYS,
        )
        code_col = find_column(frame, ("指数代码",), "指数代码")
        name_col = find_column(frame, ("指数名称",), "指数名称")
        level: int | str = sw_level
    else:
        frame = call_with_retry(
            "Eastmoney行业列表",
            ak.stock_board_industry_name_em,
            EASTMONEY_DELAYS,
        )
        code_col = find_column(frame, ("板块代码", "代码"), "板块代码")
        name_col = find_column(frame, ("板块名称", "名称"), "板块名称")
        level = "industry"
    industries = [
        Industry(source, str(row[code_col]).strip(), str(row[name_col]).strip(), level)
        for _, row in frame.iterrows()
        if str(row[code_col]).strip() and str(row[name_col]).strip()
    ]
    if not industries:
        raise RuntimeError(f"{source}没有有效行业")
    if len({item.unique_key for item in industries}) != len(industries):
        raise RuntimeError(f"{source}行业代码不唯一")
    return frame, industries


def load_history(ak: object, industry: Industry) -> pd.DataFrame:
    if industry.source in {"sw_l1", "sw_l2"}:
        raw = call_with_retry(
            f"{industry.code} {industry.name}",
            lambda: ak.index_hist_sw(symbol=industry.code, period="day"),
            SW_DELAYS,
        )
    else:
        today = date.today()
        raw = call_with_retry(
            f"{industry.code} {industry.name}",
            lambda: ak.stock_board_industry_hist_em(
                symbol=industry.name,
                start_date=(today - timedelta(days=240)).strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
                period="日k",
                adjust="",
            ),
            EASTMONEY_DELAYS,
        )
    bars = normalize_daily_bars(
        raw,
        date_column=find_column(raw, ("日期", "date"), "日期"),
        close_column=find_column(raw, ("收盘", "收盘价", "close"), "收盘价"),
        volume_column=find_column(raw, ("成交量", "volume"), "成交量"),
    ).tail(120)
    if len(bars) < 21:
        raise ValueError(f"有效K线不足：{len(bars)}")
    return bars


def sample_components(ak: object, industries: list[Industry], count: int = 3) -> list[str]:
    summaries: list[str] = []
    for industry in industries[:count]:
        if industry.source in {"sw_l1", "sw_l2"}:
            frame = call_with_retry(
                f"{industry.name}成分股",
                lambda item=industry: ak.index_component_sw(symbol=item.code),
                SW_DELAYS,
            )
        else:
            frame = call_with_retry(
                f"{industry.name}成分股",
                lambda item=industry: ak.stock_board_industry_cons_em(symbol=item.name),
                EASTMONEY_DELAYS,
            )
        code_col = find_column(frame, ("证券代码", "代码", "股票代码"), "成分股代码")
        name_col = find_column(frame, ("证券名称", "名称", "股票名称"), "成分股名称")
        summary = f"{industry.code} {industry.name}: {len(frame)}只，字段={[str(c) for c in frame.columns]}"
        summaries.append(summary)
        print(f"  成分股抽样 {summary}")
        print(frame[[code_col, name_col]].head(3).to_string(index=False))
    return summaries


def to_trend(industry: Industry, bars: pd.DataFrame) -> SectorTrend:
    metrics = calculate_trend_metrics(bars)
    return SectorTrend(
        source=industry.source,
        sector_code=industry.code,
        sector_name=industry.name,
        sector_level=industry.level,
        trade_date=pd.Timestamp(bars.iloc[-1]["date"]).date().isoformat(),
        trend_score=metrics.score,
        trend_level=metrics.level,
        close=metrics.close,
        ma5=metrics.ma5,
        ma10=metrics.ma10,
        ma20=metrics.ma20,
        volume_ratio=metrics.volume_ratio,
        is_20d_high=metrics.is_20d_high,
    )


def run_source(ak: object, source: SourceName) -> SourceResult:
    started = time.monotonic()
    errors: list[str] = []
    try:
        frame, industries = load_industries(ak, source)
    except Exception as exc:
        error = describe_source_error(source, exc)
        return SourceResult(SourceStatus(source, "unavailable", 0, 0, 0, error, round(time.monotonic() - started, 2)), ())

    print(f"\n{source} 行业总数: {len(industries)}")
    print(f"真实列表字段: {[str(c) for c in frame.columns]}")
    print(frame.head(5).to_string(index=False))
    trends: list[SectorTrend] = []
    for position, industry in enumerate(industries, start=1):
        try:
            trends.append(to_trend(industry, load_history(ak, industry)))
        except Exception as exc:
            error = f"{industry.code} {industry.name}: {type(exc).__name__}: {exc}"
            errors.append(error)
            print(f"ERROR {error}", file=sys.stderr)
        print(f"{source} K线进度: {position}/{len(industries)}，成功: {len(trends)}")

    try:
        sample_components(ak, industries, 3)
    except Exception as exc:
        errors.append(f"成分股抽样: {type(exc).__name__}: {exc}")
    state: SourceState = "available" if not errors else ("partial" if trends else "unavailable")
    result = SourceResult(
        SourceStatus(
            source=source,
            status=state,
            sector_count=len(industries),
            successful_sector_count=len(trends),
            failed_sector_count=len(industries) - len(trends),
            last_error=errors[-1] if errors else None,
            elapsed_seconds=round(time.monotonic() - started, 2),
        ),
        tuple(sorted(trends, key=lambda item: (-item.trend_score, item.sector_name))),
    )
    print_source_result(result)
    return result


def describe_source_error(source: SourceName, exc: Exception) -> str:
    """Keep the real exception while clarifying the observed upstream failure."""

    text = f"{type(exc).__name__}: {exc}"
    if source == "sw_l2" and "KeyError" in text and "data" in text:
        return f"HTTP 507 / missing data field / KeyError data; {text}"
    if source == "eastmoney" and "RemoteDisconnected" in text:
        return f"RemoteDisconnected; {text}"
    return text


def print_source_result(result: SourceResult) -> None:
    status = result.status
    print(f"\n{status.source} Trend Top20（满分70）")
    for item in result.trends[:20]:
        print(f"{trend_stars(item.trend_score)} {item.sector_name:<14} Trend {item.trend_score:>2}/70 {item.trade_date}")
    dates = [item.trade_date for item in result.trends]
    print(f"状态: {status.status}")
    print(f"行业总数: {status.sector_count}")
    print(f"成功K线/评分: {status.successful_sector_count}")
    print(f"失败K线: {status.failed_sector_count}")
    print(f"最新交易日: {max(dates) if dates else '—'}")
    print(f"最后错误: {status.last_error or '—'}")
    print(f"总耗时: {status.elapsed_seconds:.2f}s")


def run_selected_sources(ak: object, selection: str) -> tuple[dict[SourceName, SourceResult], int]:
    sources: tuple[SourceName, ...] = (
        ("sw_l1", "sw_l2", "eastmoney") if selection == "all" else (selection,)  # type: ignore[assignment]
    )
    results: dict[SourceName, SourceResult] = {}
    for source in sources:
        result = run_source(ak, source)
        results[source] = result
        if result.status.status == "unavailable":
            print_source_result(result)
    print_summary(results)
    sw_result = results.get("sw_l1")
    exit_code = 1 if sw_result is not None and sw_result.status.status == "unavailable" else 0
    return results, exit_code


def print_summary(results: dict[SourceName, SourceResult]) -> None:
    print("\nMarket Pulse Probe Summary")
    for source in ("sw_l1", "sw_l2", "eastmoney"):
        result = results.get(source)
        if result is None:
            continue
        status = result.status
        print(f"\n{source}:")
        print(f"status = {status.status}")
        print(f"sector_count = {status.sector_count}")
        print(f"successful_sector_count = {status.successful_sector_count}")
        print(f"failed_sector_count = {status.failed_sector_count}")
        print(f"last_error = {status.last_error or '—'}")
    active = results.get("sw_l1")
    active_source = "sw_l1" if active and active.status.status != "unavailable" else "none"
    print(f"\nActive source:\n{active_source}")
    fine_grained_unavailable = any(
        results.get(source) and results[source].status.status == "unavailable"
        for source in ("sw_l2", "eastmoney")
    )
    if fine_grained_unavailable:
        print("Fine-grained sources are currently unavailable.")
        if active_source == "sw_l1":
            print("SW Level-1 remains available as the active fallback.")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SW二级与Eastmoney行业双数据源趋势探针")
    parser.add_argument("--source", choices=("sw_l1", "sw_l2", "eastmoney", "all"), default="all")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    ak = get_akshare()
    print("Dual-source Sector Trend Probe")
    print(f"Python版本: {platform.python_version()}")
    print(f"AKShare版本: {getattr(ak, '__version__', 'unknown')}")
    _, exit_code = run_selected_sources(ak, args.source)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
