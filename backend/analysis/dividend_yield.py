"""Dividend yield calculations."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pandas as pd


DividendMode = Literal["latest", "trailing_12m"]


def calculate_dividend_yield(
    dividends: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    mode: DividendMode = "latest",
    today: date | None = None,
) -> pd.DataFrame:
    """Calculate dividend yield with the project formula.

    Formula:
        dividend_yield = cash_dividend_per_10 / 10 / current_price * 100
    """

    if dividends.empty:
        return _empty_result()
    if prices.empty:
        raise ValueError("prices cannot be empty")

    normalized_dividends = dividends.copy()
    normalized_prices = prices.copy()

    normalized_dividends["stock_code"] = normalized_dividends["stock_code"].astype(str).str.zfill(6)
    normalized_prices["stock_code"] = normalized_prices["stock_code"].astype(str).str.zfill(6)

    selected = (
        _select_latest_dividend(normalized_dividends)
        if mode == "latest"
        else _select_trailing_12m_dividend(normalized_dividends, today=today)
    )

    merged = selected.merge(
        normalized_prices[["stock_code", "stock_name", "current_price"]],
        on="stock_code",
        how="left",
        suffixes=("", "_price"),
    )

    if "stock_name_price" in merged.columns:
        merged["stock_name"] = merged["stock_name"].fillna(merged["stock_name_price"])
        merged = merged.drop(columns=["stock_name_price"])

    merged["current_price"] = pd.to_numeric(merged["current_price"], errors="coerce")
    merged["cash_dividend_per_10"] = pd.to_numeric(merged["cash_dividend_per_10"], errors="coerce")
    merged = merged.dropna(subset=["current_price", "cash_dividend_per_10"])
    merged = merged[merged["current_price"] > 0]

    merged["cash_dividend_per_share"] = merged["cash_dividend_per_10"] / 10
    merged["dividend_yield"] = merged["cash_dividend_per_share"] / merged["current_price"] * 100

    columns = [
        "stock_code",
        "stock_name",
        "current_price",
        "cash_dividend_per_10",
        "cash_dividend_per_share",
        "dividend_yield",
        "announcement_date",
        "record_date",
        "ex_dividend_date",
        "source",
    ]
    available_columns = [column for column in columns if column in merged.columns]
    return merged[available_columns].sort_values("dividend_yield", ascending=False).reset_index(drop=True)


def calculate_dividend_top20(
    dividends: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    as_of_date: date | None = None,
    top: int = 20,
) -> pd.DataFrame:
    """Build the upcoming Dividend Top20 table.

    Only dividend records with a future or same-day record date are included.
    Formula:
        本次股息率 = 每10股派息 / 10 / 最新收盘价 * 100%
    """

    as_of_date = as_of_date or date.today()
    if dividends.empty:
        return _empty_top20_result()

    upcoming = dividends.copy()
    upcoming["record_date"] = pd.to_datetime(upcoming["record_date"], errors="coerce")
    upcoming = upcoming.dropna(subset=["record_date"])
    upcoming = upcoming[upcoming["record_date"].dt.date >= as_of_date]
    if upcoming.empty:
        return _empty_top20_result()

    calculated = calculate_dividend_yield(upcoming, prices, mode="latest", today=as_of_date)
    if calculated.empty:
        return _empty_top20_result()

    calculated = calculated.sort_values("dividend_yield", ascending=False).head(top).reset_index(drop=True)
    calculated.insert(0, "排名", calculated.index + 1)
    calculated["登记日"] = pd.to_datetime(calculated["record_date"]).dt.strftime("%Y-%m-%d")
    calculated["股票"] = calculated.apply(_format_stock_label, axis=1)
    calculated["每10股派息"] = calculated["cash_dividend_per_10"].round(4)
    calculated["最新股价"] = calculated["current_price"].round(4)
    calculated["本次股息率"] = calculated["dividend_yield"].round(4)

    return calculated[["排名", "登记日", "股票", "每10股派息", "最新股价", "本次股息率"]]


def _select_latest_dividend(dividends: pd.DataFrame) -> pd.DataFrame:
    working = dividends.copy()
    working["_event_date"] = working.apply(_best_event_date, axis=1)
    working["_event_date"] = pd.to_datetime(working["_event_date"], errors="coerce")
    working = working.sort_values(["stock_code", "_event_date"], ascending=[True, False], na_position="last")
    return working.drop_duplicates(subset=["stock_code"], keep="first").drop(columns=["_event_date"])


def _select_trailing_12m_dividend(dividends: pd.DataFrame, *, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    cutoff = today - timedelta(days=365)
    working = dividends.copy()
    working["_event_date"] = pd.to_datetime(working.apply(_best_event_date, axis=1), errors="coerce")
    working = working[(working["_event_date"].isna()) | (working["_event_date"].dt.date >= cutoff)]

    group_columns = ["stock_code"]
    optional_columns = ["stock_name", "source", "announcement_date", "record_date", "ex_dividend_date"]
    aggregations = {"cash_dividend_per_10": "sum"}
    for column in optional_columns:
        if column in working.columns:
            aggregations[column] = "first"

    return working.groupby(group_columns, as_index=False).agg(aggregations)


def _best_event_date(row: pd.Series) -> object:
    for column in ("ex_dividend_date", "record_date", "announcement_date"):
        value = row.get(column)
        if pd.notna(value):
            return value
    return None


def _format_stock_label(row: pd.Series) -> str:
    stock_code = str(row.get("stock_code", "")).zfill(6)
    stock_name = row.get("stock_name")
    if pd.notna(stock_name) and str(stock_name).strip():
        return f"{stock_code} {str(stock_name).strip()}"
    return stock_code


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "stock_code",
            "stock_name",
            "current_price",
            "cash_dividend_per_10",
            "cash_dividend_per_share",
            "dividend_yield",
            "announcement_date",
            "record_date",
            "ex_dividend_date",
            "source",
        ]
    )


def _empty_top20_result() -> pd.DataFrame:
    return pd.DataFrame(columns=["排名", "登记日", "股票", "每10股派息", "最新股价", "本次股息率"])
