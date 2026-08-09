"""Unified, batch-oriented current-price provider for expectation refreshes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable, Protocol

from backend.collector.dividend_collector import fetch_latest_prices_akshare, normalize_stock_code
from backend.expectation_gap.futu_client import FutuResearchClient

from .errors import AllProvidersFailedError, DataSourceError, ProviderEmptyDataError, ProviderUnavailableError
from .settings import DataSourceSettings
from .tushare.client import TushareClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketPriceResult:
    """Provider-neutral outcome for one requested security."""

    status: str
    price: float | None = None
    price_time: str | None = None
    source: str | None = None
    error: str | None = None


class MarketPriceProvider(Protocol):
    def fetch_a_share_latest(self, symbols: Iterable[str], *, progress: Callable[[str], None] | None = None) -> dict[str, MarketPriceResult]: ...

    def fetch_hk_latest(self, codes: Iterable[str], *, batch_size: int = 200,
                        progress: Callable[[str], None] | None = None) -> dict[str, MarketPriceResult]: ...


class UnifiedMarketPriceProvider:
    """Tushare-first prices; all A-share calls operate on a full market batch."""

    def __init__(self, settings: DataSourceSettings | None = None, *,
                 tushare_client: TushareClient | None = None,
                 akshare_fetcher: Callable[[], object] = fetch_latest_prices_akshare,
                 hk_client_factory=FutuResearchClient,
                 today: Callable[[], date] = date.today) -> None:
        self.settings = settings or DataSourceSettings.from_env()
        self._tushare = tushare_client or TushareClient(
            self.settings.tushare_token, timeout_seconds=self.settings.request_timeout_seconds,
            max_retries=self.settings.max_retries, requests_per_minute=self.settings.requests_per_minute,
        )
        self._akshare_fetcher = akshare_fetcher
        self._hk_client_factory = hk_client_factory
        self._today = today

    def fetch_a_share_latest(self, symbols: Iterable[str], *, progress: Callable[[str], None] | None = None) -> dict[str, MarketPriceResult]:
        requested = {normalize_stock_code(symbol) for symbol in symbols}
        if not requested:
            return {}
        started = time.monotonic()
        failures: list[tuple[str, DataSourceError]] = []
        if self.settings.tushare_enabled and self.settings.tushare_primary:
            try:
                self._report(progress, "正在通过 Tushare 查询最近A股交易日")
                result = self._fetch_tushare_a_share_prices(requested, progress=progress)
                self._report(progress, f"Tushare 批量行情完成：匹配{sum(item.status == 'success' for item in result.values())}/{len(requested)}只")
                logger.info("A-share batch price refresh succeeded provider=tushare symbols=%d elapsed_seconds=%.2f",
                            len(requested), time.monotonic() - started)
                return result
            except DataSourceError as exc:
                failures.append(("tushare", exc))
                logger.warning("A-share batch price provider failed provider=tushare error_type=%s error=%s",
                               type(exc).__name__, exc)
                self._report(progress, f"Tushare 批量行情失败（{type(exc).__name__}），正在降级 AKShare")
        if self.settings.akshare_fallback_enabled:
            try:
                self._report(progress, "正在通过 AKShare 获取全市场批量实时行情")
                result = self._fetch_akshare_a_share_prices(requested)
                self._report(progress, f"AKShare 批量行情完成：匹配{sum(item.status == 'success' for item in result.values())}/{len(requested)}只")
                logger.info("A-share batch price refresh succeeded provider=akshare symbols=%d elapsed_seconds=%.2f",
                            len(requested), time.monotonic() - started)
                return result
            except DataSourceError as exc:
                failures.append(("akshare", exc))
            except Exception as exc:
                failures.append(("akshare", ProviderUnavailableError(str(exc))))
            logger.warning("A-share batch price provider failed provider=akshare error_type=%s error=%s",
                           type(failures[-1][1]).__name__, failures[-1][1])
        raise AllProvidersFailedError("latest A-share prices", tuple(failures))

    def _fetch_tushare_a_share_prices(self, requested: set[str], *, progress: Callable[[str], None] | None = None) -> dict[str, MarketPriceResult]:
        start = self._today() - timedelta(days=10)
        calendar = self._tushare.call("trade_cal", exchange="", start_date=start.strftime("%Y%m%d"),
                                      end_date=self._today().strftime("%Y%m%d"))
        calendar_rows = getattr(calendar, "to_dict", lambda *_: [])("records")
        open_days = [str(row.get("cal_date")) for row in calendar_rows if str(row.get("is_open")) in {"1", "1.0"}]
        if not open_days:
            raise ProviderEmptyDataError("Tushare trade_cal returned no open A-share date")
        trade_date = max(open_days)
        self._report(progress, f"正在通过 Tushare 批量读取 {trade_date} 全市场日线")
        frame = self._tushare.call("daily", trade_date=trade_date)
        rows = getattr(frame, "to_dict", lambda *_: [])("records")
        if not rows:
            raise ProviderEmptyDataError(f"Tushare daily returned no rows for {trade_date}")
        found: dict[str, MarketPriceResult] = {}
        for row in rows:
            code = normalize_stock_code(str(row.get("ts_code") or ""))
            if code not in requested:
                continue
            try:
                price = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if price > 0:
                found[code] = MarketPriceResult("success", price, trade_date, "tushare")
        return self._fill_no_data(requested, found)

    def _fetch_akshare_a_share_prices(self, requested: set[str]) -> dict[str, MarketPriceResult]:
        frame = self._akshare_fetcher()
        rows = getattr(frame, "to_dict", lambda *_: [])("records")
        if not rows:
            raise ProviderEmptyDataError("AKShare full-market spot returned no rows")
        found: dict[str, MarketPriceResult] = {}
        for row in rows:
            code = normalize_stock_code(str(row.get("stock_code") or ""))
            if code not in requested:
                continue
            try:
                price = float(row.get("current_price"))
            except (TypeError, ValueError):
                continue
            if price > 0:
                found[code] = MarketPriceResult("success", price, None, "akshare")
        return self._fill_no_data(requested, found)

    def fetch_hk_latest(self, codes: Iterable[str], *, batch_size: int = 200,
                        progress: Callable[[str], None] | None = None) -> dict[str, MarketPriceResult]:
        requested = list(dict.fromkeys(str(code).upper() for code in codes))
        if not requested:
            return {}
        self._report(progress, f"正在通过 Futu/OpenD 批量读取港股快照（{len(requested)}只）")
        try:
            with self._hk_client_factory() as client:
                snapshots = client.batch_snapshots(requested, batch_size=batch_size)
        except Exception as exc:
            logger.warning("HK batch price provider failed provider=futu_opend error_type=%s error=%s",
                           type(exc).__name__, exc)
            return {code: MarketPriceResult("connection_error", source="futu_opend", error=str(exc)) for code in requested}
        result: dict[str, MarketPriceResult] = {}
        for code in requested:
            snapshot = snapshots.get(code)
            if snapshot is None:
                result[code] = MarketPriceResult("no_data", source="futu_opend")
            elif snapshot.status == "success":
                payload = snapshot.data or {}
                result[code] = MarketPriceResult("success", float(payload["last_price"]), payload.get("price_time"), "futu_opend")
            else:
                result[code] = MarketPriceResult(snapshot.status, source="futu_opend", error=snapshot.error)
        return result

    @staticmethod
    def _report(progress: Callable[[str], None] | None, message: str) -> None:
        logger.info("market_price_provider %s", message)
        if progress is not None:
            progress(message)

    @staticmethod
    def _fill_no_data(requested: set[str], found: dict[str, MarketPriceResult]) -> dict[str, MarketPriceResult]:
        return {code: found.get(code, MarketPriceResult("no_data")) for code in requested}
