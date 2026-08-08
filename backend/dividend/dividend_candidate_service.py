"""Read-only candidate generation service; providers are injected for offline tests."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Iterable, Protocol

import pandas as pd

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from .dividend_candidate_rules import (
    CONTINUOUS_DIVIDEND_YEARS, CSV_COLUMNS, EXCLUSION_COLUMNS, LATEST_TO_AVERAGE_MIN_RATIO,
    MIN_LISTING_YEARS, classify_industry, target_years,
)
from .dividend_candidate_rules import LISTING_AGE_EXEMPTIONS
from .models import CandidateSecurity, DividendEvent
from .annual_dps import aggregate_events, unique_events


class DividendProvider(Protocol):
    def fetch_events(self, symbols: Iterable[str]) -> list[DividendEvent]: ...


class TushareDividendProvider:
    """Tushare access adapter, keeping SDK calls out of candidate business logic."""
    def __init__(self, client: TushareClient) -> None:
        self.client = client

    def fetch_events(self, symbols: Iterable[str]) -> list[DividendEvent]:
        events: list[DividendEvent] = []
        fields = "ts_code,ann_date,end_date,ex_date,cash_div_tax,div_proc,record_date,pay_date,imp_ann_date,base_date,stk_div,stk_bo_rate,stk_co_rate"
        for requested_symbol in symbols:
            raw = self.client.call("dividend", ts_code=requested_symbol, fields=fields)
            if raw is None or raw.empty:
                continue
            for row in raw.to_dict("records"):
                symbol = str(row.get("ts_code") or requested_symbol).upper()
                events.append(DividendEvent(
                    symbol, _parse_date(row.get("ann_date")), _parse_date(row.get("ex_date")),
                    _float(row.get("cash_div_tax")), _text(row.get("div_proc")), _parse_date(row.get("end_date")),
                    _parse_date(row.get("record_date")), _parse_date(row.get("pay_date")),
                    _parse_date(row.get("imp_ann_date")), _parse_date(row.get("base_date")),
                    _float(row.get("stk_div")), _float(row.get("stk_bo_rate")), _float(row.get("stk_co_rate")),
                ))
        return events


def _aggregate_events(events: Iterable[DividendEvent], years: tuple[int, int, int]):
    """Compatibility wrapper for callers; report-period aggregation is canonical."""
    totals, _ = aggregate_events(events, years)
    return totals, set()


def _unique_valid_events(events: Iterable[DividendEvent], symbol: str, years: tuple[int, int, int]) -> list[DividendEvent]:
    return [event for event in unique_events(events, years) if event.symbol == symbol]


def _parse_date(value: object) -> date | None:
    if value is None or pd.isna(value): return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


class DividendCandidateService:
    def __init__(self, connection: sqlite3.Connection, provider: DividendProvider) -> None:
        self.connection, self.provider = connection, provider

    def generate(self, calculation_date: date, *, symbols: set[str] | None = None, limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        exclusions: list[dict[str, object]] = []
        securities = self._load_securities(calculation_date, symbols, limit)
        eligible: list[CandidateSecurity] = []
        stages: defaultdict[str, int] = defaultdict(int)
        for security in securities:
            reason, stage = self._basic_exclusion(security, calculation_date)
            if reason:
                exclusions.append(self._exclusion(security, reason, stage, generated_at))
                continue
            monopoly_type = classify_industry(security.industry_level_1, security.industry_level_2, security.industry_level_3, security.company_name)
            if monopoly_type is None:
                reason = "industry_unknown" if not any((security.industry_level_1, security.industry_level_2, security.industry_level_3)) else "industry_not_allowed"
                exclusions.append(self._exclusion(security, reason, "industry", generated_at))
                continue
            eligible.append(security)
            stages["stable_industry_candidates"] += 1
        try:
            events = self.provider.fetch_events(item.symbol for item in eligible)
            dividend_source_failed = False
        except Exception as exc:
            for security in eligible:
                exclusions.append(self._exclusion(security, "data_source_error", "dividend", generated_at, type(exc).__name__))
            events = []
            dividend_source_failed = True
        yearly, _ = aggregate_events(events, target_years(calculation_date))
        invalid_symbols: set[str] = set()
        rows: list[dict[str, object]] = []
        years = target_years(calculation_date)
        for security in eligible:
            if dividend_source_failed:
                continue
            if not any(event.symbol == security.symbol for event in events):
                exclusions.append(self._exclusion(security, "missing_dividend_data", "dividend", generated_at))
                continue
            if security.symbol in invalid_symbols:
                exclusions.append(self._exclusion(security, "duplicate_or_invalid_dividend_records", "dividend", generated_at))
                continue
            amounts = [yearly[security.symbol].get(year, 0.0) for year in years]
            missing = next((i for i, value in enumerate(amounts, 1) if value <= 0), None)
            if missing:
                exclusions.append(self._exclusion(security, f"no_dividend_year_{missing}", "continuity", generated_at))
                continue
            average = sum(amounts) / CONTINUOUS_DIVIDEND_YEARS
            ratio = amounts[-1] / average
            if ratio < LATEST_TO_AVERAGE_MIN_RATIO:
                exclusions.append(self._exclusion(security, "latest_year_dividend_decline", "continuity", generated_at))
                continue
            monopoly_type = classify_industry(security.industry_level_1, security.industry_level_2, security.industry_level_3, security.company_name)
            rows.append(self._candidate(security, monopoly_type or "", years, amounts, average, ratio, len([event for event in unique_events(events, years) if event.symbol == security.symbol]), calculation_date, generated_at))
        result = pd.DataFrame(rows, columns=CSV_COLUMNS).sort_values(["monopoly_type", "industry_level_1", "symbol"], kind="stable") if rows else pd.DataFrame(columns=CSV_COLUMNS)
        excluded = pd.DataFrame(exclusions, columns=EXCLUSION_COLUMNS).sort_values(["symbol", "exclusion_stage", "exclusion_reason"], kind="stable") if exclusions else pd.DataFrame(columns=EXCLUSION_COLUMNS)
        summary = {"securities_total": len(securities), "stable_industry_candidates": stages["stable_industry_candidates"], "final_candidates": len(result), "exclusions": len(excluded)}
        return result, excluded, summary

    def _load_securities(self, calculation_date: date, symbols: set[str] | None, limit: int | None) -> list[CandidateSecurity]:
        query = """SELECT m.symbol,m.security_name,m.listed_date,m.is_active,m.delisted_date,s.is_st,
        i.level1_name,i.level2_name,i.level3_name,i.source
        FROM a_share_security_master m
        LEFT JOIN a_share_security_status_history s ON s.symbol=m.symbol AND s.effective_date=(SELECT MAX(effective_date) FROM a_share_security_status_history x WHERE x.symbol=m.symbol AND x.effective_date<=?)
        LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol
        WHERE m.exchange IN ('SH','SZ') ORDER BY m.symbol"""
        values: list[object] = [calculation_date.isoformat()]
        rows = self.connection.execute(query, values).fetchall()
        records = [CandidateSecurity(str(row[0]), row[1], _parse_date(row[2]), bool(row[3]), _parse_date(row[4]), None if row[5] is None else bool(row[5]), row[6], row[7], row[8], row[9]) for row in rows]
        if symbols is not None: records = [item for item in records if item.symbol in symbols]
        return records[:limit] if limit is not None else records

    def _basic_exclusion(self, item: CandidateSecurity, calculation_date: date) -> tuple[str | None, str]:
        if item.symbol.startswith(("20", "900")):
            return "not_common_a_share", "basic"
        if not item.is_active or (item.delisted_date and item.delisted_date <= calculation_date): return "not_active", "basic"
        if item.is_st or "ST" in (item.company_name or "").upper() or "退" in (item.company_name or ""): return "st_or_delisting", "basic"
        if item.list_date is None: return "listed_less_than_5_years", "basic"
        years = (calculation_date - item.list_date).days / 365.2425
        if years < MIN_LISTING_YEARS and item.symbol not in LISTING_AGE_EXEMPTIONS:
            return "listed_less_than_5_years", "basic"
        return None, ""

    def _exclusion(self, item: CandidateSecurity, reason: str, stage: str, generated_at: str, details: str = "") -> dict[str, object]:
        return {"market":"CN", "symbol":item.symbol, "company_name":item.company_name or "", "industry":item.industry_level_3 or item.industry_level_2 or item.industry_level_1 or "", "exclusion_stage":stage, "exclusion_reason":reason, "details":details, "generated_at":generated_at}

    def _candidate(self, item: CandidateSecurity, monopoly_type: str, years: tuple[int, int, int], amounts: list[float], average: float, ratio: float, event_count: int, calculation_date: date, generated_at: str) -> dict[str, object]:
        listing_years = round((calculation_date - item.list_date).days / 365.2425, 4) if item.list_date else None
        risk = "包含多次年度派息" if event_count > 3 else "行业分类依赖当前申万基础信息"
        if ratio <= .8: risk += ";最近一年分红接近下限"
        return {"market":"CN", "symbol":item.symbol, "company_name":item.company_name or "", "list_date":item.list_date.isoformat() if item.list_date else "", "listing_years":listing_years, "industry_level_1":item.industry_level_1 or "", "industry_level_2":item.industry_level_2 or "", "industry_source":item.industry_source or "unknown", "stability_category":"stable_monopoly_candidate", "monopoly_type":monopoly_type, "target_year_1":years[0], "target_year_1_dps":round(amounts[0],6), "target_year_2":years[1], "target_year_2_dps":round(amounts[1],6), "target_year_3":years[2], "target_year_3_dps":round(amounts[2],6), "three_year_total_dps":round(sum(amounts),6), "three_year_average_dps":round(average,6), "latest_year_dps":round(amounts[-1],6), "latest_to_average_ratio":round(ratio,4), "dividend_event_count_3y":event_count, "candidate_reason":f"{monopoly_type}；连续三年现金分红；最近一年分红未明显下降", "risk_note":risk, "data_quality_status":"complete", "generated_at":generated_at}
