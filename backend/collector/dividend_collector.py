"""Collect dividend and latest price data from free China A-share sources.

AKShare is the default source. Tushare Pro is supported as an optional
supplement when ``TUSHARE_TOKEN`` is available in the environment.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd


SourceName = Literal["akshare", "eastmoney", "tushare"]


@dataclass(frozen=True)
class DividendRecord:
    stock_code: str
    stock_name: str | None
    cash_dividend_per_10: float
    announcement_date: date | None
    record_date: date | None
    ex_dividend_date: date | None
    source: SourceName


def get_akshare() -> object:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AKShare is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return ak


def get_tushare() -> object:
    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError(
            "Tushare is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return ts


def load_env_file(path: str | Path = ".env") -> bool:
    """Load simple KEY=VALUE pairs from a local .env file."""

    env_path = Path(path)
    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def normalize_stock_code(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def to_tushare_code(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


def parse_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None

    text = text.replace(".", "-").replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_cash_dividend_per_10(value: object, *, value_is_per_share: bool = False) -> float | None:
    """Parse cash dividend amount into RMB per 10 shares.

    Common source examples include numeric values, ``10派1.5元`` and
    ``每10股派发现金红利1.5元``.
    """

    if value is None or pd.isna(value):
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = float(value)
        return amount * 10 if value_is_per_share else amount

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None

    patterns = (
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)",
        r"每\s*10\s*股.*?([0-9]+(?:\.[0-9]+)?)\s*元",
        r"派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))

    numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?", text)
    if not numbers:
        return None
    amount = float(numbers[-1])
    return amount * 10 if value_is_per_share else amount


def _first_existing_column(frame: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    normalized = {str(column).strip().lower(): column for column in frame.columns}
    for alias in aliases:
        found = normalized.get(alias.strip().lower())
        if found is not None:
            return found
    return None


def fetch_latest_prices_akshare() -> pd.DataFrame:
    """Fetch latest A-share prices from Eastmoney through AKShare."""

    ak = get_akshare()
    errors: list[str] = []
    for function_name in ("stock_zh_a_spot_em", "stock_zh_a_spot"):
        if not hasattr(ak, function_name):
            continue
        try:
            raw = getattr(ak, function_name)()
            return _normalize_akshare_price_frame(raw)
        except Exception as exc:
            errors.append(f"{function_name}: {exc}")

    raise RuntimeError("All AKShare price APIs failed: " + " | ".join(errors))


def fetch_latest_prices_akshare_by_codes(
    stock_codes: Iterable[str],
    *,
    lookback_days: int = 370,
    retries: int = 0,
    sleep_seconds: float = 0.2,
    timeout: float = 8,
) -> pd.DataFrame:
    """Fetch latest close prices one stock at a time.

    This is slower than ``stock_zh_a_spot_em`` but is more practical when the
    full-market spot endpoints are temporarily blocked or unstable.
    """

    ak = get_akshare()
    start_date = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []

    for stock_code in stock_codes:
        code = normalize_stock_code(stock_code)
        try:
            raw = _retry(
                lambda: ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    adjust="",
                    timeout=timeout,
                ),
                retries=retries,
                sleep_seconds=sleep_seconds,
            )
        except Exception:
            continue
        if raw is None or raw.empty:
            continue

        latest = raw.tail(1).copy()
        latest["stock_code"] = code
        frames.append(_normalize_akshare_price_frame(latest))

    if not frames:
        return pd.DataFrame(columns=["stock_code", "stock_name", "current_price"])
    return pd.concat(frames, ignore_index=True)


def _normalize_akshare_price_frame(raw: pd.DataFrame) -> pd.DataFrame:
    code_col = _first_existing_column(raw, ["代码", "code", "证券代码", "股票代码"])
    name_col = _first_existing_column(raw, ["名称", "name", "证券简称"])
    price_col = _first_existing_column(raw, ["最新价", "现价", "close", "最新", "收盘", "收盘价"])

    if code_col is None or price_col is None:
        raise ValueError(f"Unexpected AKShare price columns: {list(raw.columns)}")

    prices = pd.DataFrame(
        {
            "stock_code": raw[code_col].map(normalize_stock_code),
            "stock_name": raw[name_col] if name_col is not None else None,
            "current_price": pd.to_numeric(raw[price_col], errors="coerce"),
        }
    )
    return prices.dropna(subset=["stock_code", "current_price"])


def _retry(callable_obj, *, retries: int, sleep_seconds: float):
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return callable_obj()
        except Exception as exc:
            last_error = exc
            if attempt < retries and sleep_seconds > 0:
                time.sleep(sleep_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    return None


def fetch_dividends_akshare(
    stock_codes: Iterable[str],
    *,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    """Fetch dividend records from AKShare for selected stock codes."""

    ak = get_akshare()
    records: list[DividendRecord] = []

    for stock_code in stock_codes:
        code = normalize_stock_code(stock_code)
        records.extend(_fetch_akshare_dividend_records_for_code(ak, code))
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return pd.DataFrame([record.__dict__ for record in records])


def fetch_announced_dividends_akshare() -> pd.DataFrame:
    """Fetch the market-wide announced dividend list from AKShare.

    This should be the first step for Dividend Top20, because it avoids scanning
    every stock just to discover whether a dividend exists.
    """

    ak = get_akshare()
    if not hasattr(ak, "stock_history_dividend"):
        raise RuntimeError("AKShare stock_history_dividend API is not available.")

    try:
        raw = ak.stock_history_dividend()
    except Exception as exc:
        raise RuntimeError(
            "AKShare announced dividend list API failed. "
            "Try --codes for selected stocks or configure Tushare fallback."
        ) from exc
    records = _normalize_akshare_dividend_frame(raw, "")
    if not records:
        raise RuntimeError(
            "AKShare announced dividend list returned no usable records. "
            "Try --codes for selected stocks or configure Tushare fallback."
        )

    return pd.DataFrame([record.__dict__ for record in records])


def fetch_announced_dividends_eastmoney(report_dates: Iterable[str] | None = None) -> pd.DataFrame:
    """Fetch market-wide dividend plans from Eastmoney through AKShare."""

    ak = get_akshare()
    if not hasattr(ak, "stock_fhps_em"):
        raise RuntimeError("AKShare stock_fhps_em API is not available.")

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for report_date in report_dates or _default_report_dates():
        try:
            raw = ak.stock_fhps_em(date=report_date)
            records = _normalize_eastmoney_fhps_frame(raw)
            if not records.empty:
                frames.append(records)
        except Exception as exc:
            errors.append(f"{report_date}: {exc}")

    if frames:
        result = pd.concat(frames, ignore_index=True)
        return result.drop_duplicates(
            subset=["stock_code", "record_date", "cash_dividend_per_10"],
            keep="first",
        )

    raise RuntimeError(
        "Eastmoney announced dividend API returned no usable records. "
        + (" | ".join(errors) if errors else "")
    )


def _default_report_dates(today: date | None = None) -> list[str]:
    today = today or date.today()
    report_dates = [f"{today.year - 1}1231"]
    if today.month >= 7:
        report_dates.append(f"{today.year}0630")
    report_dates.append(f"{today.year - 1}0630")
    return report_dates


def _normalize_eastmoney_fhps_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty or len(raw.columns) < 16:
        return pd.DataFrame()

    # AKShare's Eastmoney function returns stable column order even when
    # localized column names render differently across Windows code pages.
    dividend_yield = pd.to_numeric(raw.iloc[:, 6], errors="coerce")
    cash_dividend_per_10 = pd.to_numeric(raw.iloc[:, 5], errors="coerce")
    implied_price = (cash_dividend_per_10 / 10) / dividend_yield.replace(0, pd.NA)

    normalized = pd.DataFrame(
        {
            "stock_code": raw.iloc[:, 0].map(normalize_stock_code),
            "stock_name": raw.iloc[:, 1],
            "cash_dividend_per_10": cash_dividend_per_10,
            "current_price": pd.to_numeric(implied_price, errors="coerce"),
            "announcement_date": raw.iloc[:, 13].map(parse_date),
            "record_date": raw.iloc[:, 14].map(parse_date),
            "ex_dividend_date": raw.iloc[:, 15].map(parse_date),
            "source": "eastmoney",
        }
    )
    normalized = normalized.dropna(subset=["stock_code", "cash_dividend_per_10"])
    normalized = normalized[normalized["cash_dividend_per_10"] > 0]
    return normalized


def _fetch_akshare_dividend_records_for_code(ak: object, stock_code: str) -> list[DividendRecord]:
    """Try known AKShare dividend APIs until one yields normalized records."""

    if hasattr(ak, "stock_history_dividend_detail"):
        try:
            raw = ak.stock_history_dividend_detail(symbol=stock_code, indicator="分红")
            records = _normalize_akshare_dividend_frame(raw, stock_code)
            if records:
                return records
        except Exception:
            pass

    if hasattr(ak, "stock_dividend_cninfo"):
        try:
            raw = ak.stock_dividend_cninfo(symbol=stock_code)
            records = _normalize_akshare_dividend_frame(raw, stock_code)
            if records:
                return records
        except Exception:
            pass

    return []


def _normalize_akshare_dividend_frame(raw: pd.DataFrame, stock_code: str) -> list[DividendRecord]:
    code_col = _first_existing_column(raw, ["代码", "证券代码", "stock_code", "code"])
    name_col = _first_existing_column(raw, ["名称", "证券简称", "股票简称", "stock_name", "name"])
    cash_col = _first_existing_column(
        raw,
        [
            "派息比例",
            "派息(税前)",
            "派息税前",
            "每10股派息",
            "现金分红",
            "分红方案",
            "实施方案",
            "方案说明",
            "分红说明",
        ],
    )
    ann_col = _first_existing_column(raw, ["公告日期", "预案公告日", "ann_date", "announcement_date"])
    record_col = _first_existing_column(raw, ["股权登记日", "登记日", "record_date"])
    ex_col = _first_existing_column(raw, ["除权除息日", "除息日", "ex_date", "ex_dividend_date"])

    if cash_col is None:
        return []

    records: list[DividendRecord] = []
    for _, row in raw.iterrows():
        cash_per_10 = parse_cash_dividend_per_10(row.get(cash_col))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue

        records.append(
            DividendRecord(
                stock_code=normalize_stock_code(row.get(code_col)) if code_col else stock_code,
                stock_name=str(row.get(name_col)).strip() if name_col and pd.notna(row.get(name_col)) else None,
                cash_dividend_per_10=cash_per_10,
                announcement_date=parse_date(row.get(ann_col)) if ann_col else None,
                record_date=parse_date(row.get(record_col)) if record_col else None,
                ex_dividend_date=parse_date(row.get(ex_col)) if ex_col else None,
                source="akshare",
            )
        )

    return records


def fetch_dividends_tushare(
    stock_codes: Iterable[str],
    *,
    token: str | None = None,
) -> pd.DataFrame:
    """Fetch dividend records from Tushare Pro when a token is configured."""

    load_env_file()
    token = token or os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set. Skip Tushare or export the token first.")

    ts = get_tushare()
    ts.set_token(token)
    pro = ts.pro_api()

    frames: list[pd.DataFrame] = []
    for stock_code in stock_codes:
        raw = pro.dividend(ts_code=to_tushare_code(stock_code), fields="ts_code,ann_date,record_date,ex_date,cash_div_tax")
        if raw is not None and not raw.empty:
            frames.append(raw)

    if not frames:
        return pd.DataFrame()

    raw = pd.concat(frames, ignore_index=True)
    result = pd.DataFrame(
        {
            "stock_code": raw["ts_code"].map(normalize_stock_code),
            "stock_name": None,
            "cash_dividend_per_10": raw["cash_div_tax"].map(
                lambda value: parse_cash_dividend_per_10(value, value_is_per_share=True)
            ),
            "announcement_date": raw["ann_date"].map(parse_date),
            "record_date": raw["record_date"].map(parse_date),
            "ex_dividend_date": raw["ex_date"].map(parse_date),
            "source": "tushare",
        }
    )
    return result.dropna(subset=["cash_dividend_per_10"])


def fetch_announced_dividends_tushare(*, token: str | None = None) -> pd.DataFrame:
    """Fetch announced dividend records from Tushare Pro when a token exists."""

    load_env_file()
    token = token or os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set. Cannot use Tushare announced dividends.")

    ts = get_tushare()
    ts.set_token(token)
    pro = ts.pro_api()
    try:
        raw = pro.dividend(fields="ts_code,ann_date,record_date,ex_date,cash_div_tax")
    except Exception as exc:
        raise RuntimeError(
            "Tushare dividend API failed. Check whether your token has dividend interface permission."
        ) from exc
    if raw is None or raw.empty:
        return pd.DataFrame()

    result = pd.DataFrame(
        {
            "stock_code": raw["ts_code"].map(normalize_stock_code),
            "stock_name": None,
            "cash_dividend_per_10": raw["cash_div_tax"].map(
                lambda value: parse_cash_dividend_per_10(value, value_is_per_share=True)
            ),
            "announcement_date": raw["ann_date"].map(parse_date),
            "record_date": raw["record_date"].map(parse_date),
            "ex_dividend_date": raw["ex_date"].map(parse_date),
            "source": "tushare",
        }
    )
    return result.dropna(subset=["cash_dividend_per_10"])


def collect_dividend_candidates(
    *,
    limit: int | None = 200,
    include_tushare: bool = False,
    stock_codes: Iterable[str] | None = None,
    price_overrides: dict[str, float] | None = None,
    as_of_date: date | None = None,
    refresh_prices: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch latest prices and dividend records."""

    price_overrides = {
        normalize_stock_code(code): float(price)
        for code, price in (price_overrides or {}).items()
    }

    as_of_date = as_of_date or date.today()
    used_tushare_announced = False

    if stock_codes is None:
        try:
            dividends = fetch_announced_dividends_eastmoney()
        except RuntimeError:
            try:
                dividends = fetch_announced_dividends_akshare()
            except RuntimeError:
                if not include_tushare:
                    raise
                dividends = fetch_announced_dividends_tushare()
                used_tushare_announced = True
        upcoming = dividends.copy()
        upcoming["record_date"] = pd.to_datetime(upcoming["record_date"], errors="coerce")
        upcoming = upcoming.dropna(subset=["record_date"])
        upcoming = upcoming[upcoming["record_date"].dt.date >= as_of_date]
        selected_codes = upcoming["stock_code"].drop_duplicates()
        if limit is not None:
            selected_codes = selected_codes.head(limit)
        dividends = upcoming[upcoming["stock_code"].isin(selected_codes)].copy()
        prices = _merge_price_fallbacks(pd.DataFrame(), dividends)
        if refresh_prices or prices.empty:
            live_prices = fetch_latest_prices_akshare_by_codes(selected_codes)
            prices = _merge_price_fallbacks(live_prices, dividends)
    else:
        selected_codes = pd.Series([normalize_stock_code(code) for code in stock_codes]).drop_duplicates()
        codes_needing_prices = [code for code in selected_codes if code not in price_overrides]
        fetched_prices = (
            fetch_latest_prices_akshare_by_codes(codes_needing_prices)
            if codes_needing_prices
            else pd.DataFrame(columns=["stock_code", "stock_name", "current_price"])
        )
        override_prices = pd.DataFrame(
            [
                {"stock_code": code, "stock_name": None, "current_price": price}
                for code, price in price_overrides.items()
                if code in set(selected_codes)
            ]
        )
        prices = pd.concat([fetched_prices, override_prices], ignore_index=True)

    dividend_frames = [dividends] if stock_codes is None else [fetch_dividends_akshare(selected_codes)]
    if include_tushare and not used_tushare_announced:
        dividend_frames.append(fetch_dividends_tushare(selected_codes))

    dividends = pd.concat(
        [frame for frame in dividend_frames if frame is not None and not frame.empty],
        ignore_index=True,
    ) if any(frame is not None and not frame.empty for frame in dividend_frames) else pd.DataFrame()

    return dividends, prices


def _merge_price_fallbacks(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
    if "current_price" not in dividends.columns:
        return prices

    fallback = dividends[["stock_code", "stock_name", "current_price"]].copy()
    fallback["current_price"] = pd.to_numeric(fallback["current_price"], errors="coerce")
    fallback = fallback.dropna(subset=["current_price"])
    fallback = fallback[fallback["current_price"] > 0]
    fallback = fallback.drop_duplicates(subset=["stock_code"], keep="first")
    if fallback.empty:
        return prices
    if prices is None or prices.empty:
        return fallback.reset_index(drop=True)

    known_codes = set(prices["stock_code"].astype(str).str.zfill(6))
    missing = fallback[~fallback["stock_code"].isin(known_codes)]
    if missing.empty:
        return prices
    return pd.concat([prices, missing], ignore_index=True)
