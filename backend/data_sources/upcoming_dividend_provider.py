"""Provider adapters for the upcoming A-share dividend page."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from backend.collector.dividend_collector import (
    _default_report_dates,
    _normalize_eastmoney_fhps_frame,
    get_akshare,
    normalize_stock_code,
    parse_date,
    parse_cash_dividend_per_10,
)

from .errors import AllProvidersFailedError, DataSourceError, ProviderUnavailableError
from .settings import DataSourceSettings
from .tushare.client import TushareClient


class UpcomingDividendProvider(Protocol):
    """Return announced cash dividends whose record date is in the requested window."""

    def fetch(self, *, start_date: date, end_date: date) -> pd.DataFrame: ...


class TushareUpcomingDividendProvider:
    """Tushare batch adapter; queries each record date, never each security."""

    _FIELDS = "ts_code,ann_date,end_date,div_proc,cash_div_tax,record_date,ex_date,pay_date,imp_ann_date"

    def __init__(self, client: TushareClient) -> None:
        self._client = client

    def fetch(self, *, start_date: date, end_date: date) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for record_date in _days_inclusive(start_date, end_date):
            offset = 0
            while True:
                raw = self._client.call(
                    "dividend",
                    record_date=record_date.strftime("%Y%m%d"),
                    offset=offset,
                    limit=2000,
                    fields=self._FIELDS,
                )
                if raw is None or raw.empty:
                    break
                frames.append(_normalize_tushare(raw))
                if len(raw) < 2000:
                    break
                offset += 2000
        if not frames:
            return _empty_dividends()
        result = pd.concat(frames, ignore_index=True)
        result = result.dropna(subset=["stock_code", "record_date", "cash_dividend_per_10"])
        result = result[result["cash_dividend_per_10"] > 0]
        result = result.drop_duplicates(
            subset=["stock_code", "record_date", "cash_dividend_per_10", "announcement_date"],
            keep="first",
        )
        result["stock_name"] = result["stock_code"].map(self._listed_names())
        return result.reset_index(drop=True)

    def _listed_names(self) -> dict[str, str]:
        """Resolve names with one optional Tushare full-market request."""
        try:
            raw = self._client.call("stock_basic", exchange="", list_status="L", fields="ts_code,name")
        except DataSourceError:
            return {}
        if raw is None or raw.empty:
            return {}
        return {
            normalize_stock_code(row.get("ts_code")): str(row.get("name")).strip()
            for row in raw.to_dict("records")
            if row.get("ts_code") and row.get("name")
        }


class AKShareUpcomingDividendProvider:
    """Batch AKShare/Eastmoney fallback for announced dividend plans."""

    def fetch(self, *, start_date: date, end_date: date) -> pd.DataFrame:
        ak = get_akshare()
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        for report_date in _default_report_dates(start_date):
            try:
                normalized = _normalize_eastmoney_fhps_frame(ak.stock_fhps_em(date=report_date))
                if not normalized.empty:
                    frames.append(normalized)
            except Exception as exc:
                errors.append(f"{report_date}: {type(exc).__name__}")
        if not frames:
            raise ProviderUnavailableError("AKShare announced dividend batches failed: " + ", ".join(errors))
        result = pd.concat(frames, ignore_index=True)
        dates = pd.to_datetime(result["record_date"], errors="coerce")
        return result[(dates.dt.date >= start_date) & (dates.dt.date <= end_date)].reset_index(drop=True)


class TushareFirstUpcomingDividendProvider:
    """Tushare primary with AKShare only as an unavailable-provider fallback."""

    def __init__(self, settings: DataSourceSettings | None = None, *,
                 tushare: UpcomingDividendProvider | None = None,
                 akshare: UpcomingDividendProvider | None = None) -> None:
        settings = settings or DataSourceSettings.from_env()
        self._tushare_enabled = settings.tushare_enabled and settings.tushare_primary
        self._akshare_enabled = settings.akshare_fallback_enabled
        self._tushare = tushare or TushareUpcomingDividendProvider(TushareClient(
            settings.tushare_token,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            requests_per_minute=settings.requests_per_minute,
        ))
        self._akshare = akshare or AKShareUpcomingDividendProvider()

    def fetch(self, *, start_date: date, end_date: date) -> pd.DataFrame:
        failures: list[tuple[str, DataSourceError]] = []
        if self._tushare_enabled:
            try:
                return self._tushare.fetch(start_date=start_date, end_date=end_date)
            except DataSourceError as exc:
                failures.append(("tushare", exc))
            except Exception as exc:
                failures.append(("tushare", ProviderUnavailableError(str(exc))))
        if self._akshare_enabled:
            try:
                return self._akshare.fetch(start_date=start_date, end_date=end_date)
            except DataSourceError as exc:
                failures.append(("akshare", exc))
            except Exception as exc:
                failures.append(("akshare", ProviderUnavailableError(str(exc))))
        raise AllProvidersFailedError("upcoming A-share dividends", tuple(failures))


def _normalize_tushare(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": raw.get("ts_code", pd.Series(dtype=str)).map(normalize_stock_code),
        "stock_name": None,
        "cash_dividend_per_10": raw.get("cash_div_tax", pd.Series(dtype=float)).map(
            lambda value: parse_cash_dividend_per_10(value, value_is_per_share=True)
        ),
        "announcement_date": raw.get("ann_date", pd.Series(dtype=object)).map(parse_date),
        "record_date": raw.get("record_date", pd.Series(dtype=object)).map(parse_date),
        "ex_dividend_date": raw.get("ex_date", pd.Series(dtype=object)).map(parse_date),
        "source": "tushare",
    })


def _days_inclusive(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "stock_code", "stock_name", "cash_dividend_per_10", "announcement_date",
        "record_date", "ex_dividend_date", "source",
    ])
