"""Real CSI 300 benchmark acquisition for Market Pulse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import time

import pandas as pd

from backend.collector.probe_sector_data import find_column
from backend.sector_radar.relative_strength import BENCHMARK_CODE


@dataclass(frozen=True)
class BenchmarkData:
    bars: pd.DataFrame
    code: str
    source: str
    row_count: int
    latest_trade_date: str
    elapsed_seconds: float


def load_csi300_benchmark(ak: object, *, today: date | None = None) -> BenchmarkData:
    end = today or date.today()
    started = time.monotonic()
    raw = ak.stock_zh_index_hist_csindex(
        symbol=BENCHMARK_CODE,
        start_date=(end - timedelta(days=400)).strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if raw is None or raw.empty:
        raise RuntimeError("沪深300接口返回空数据")
    date_column = find_column(raw, ("日期", "date"), "基准日期")
    close_column = find_column(raw, ("收盘", "close"), "基准收盘价")
    bars = raw.loc[:, [date_column, close_column]].copy()
    bars.columns = ["trade_date", "close"]
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.dropna().sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    bars = bars[bars["close"] > 0]
    if len(bars) < 21:
        raise RuntimeError(f"沪深300有效行情不足21日：{len(bars)}")
    latest = bars.iloc[-1]["trade_date"].date().isoformat()
    return BenchmarkData(bars, BENCHMARK_CODE, "csindex_official", len(bars), latest, round(time.monotonic() - started, 3))
