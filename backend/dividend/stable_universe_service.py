"""Validated import of the formal stable-dividend universe."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

import pandas as pd

from .dividend_candidate_rules import MANUAL_CORE_ADDITIONS, target_years
from .dividend_candidate_service import DividendProvider, _aggregate_events
from .models import DividendEvent

METHOD = "implemented_cash_dividend_grouped_by_ex_date"


@dataclass(frozen=True)
class PlannedUniverseItem:
    market: str
    symbol: str
    company_name: str
    industry_level_1: str | None
    industry_level_2: str | None
    monopoly_type: str
    stability_subtype: str
    inclusion_source: str
    inclusion_reason: str
    risk_note: str


class StableUniverseImportService:
    def __init__(self, connection: sqlite3.Connection, provider: DividendProvider) -> None:
        self.connection, self.provider = connection, provider

    def plan(self, final: pd.DataFrame, calculation_date: date, symbols: set[str] | None = None) -> tuple[list[PlannedUniverseItem], dict[str, dict[int, float]], dict[str, dict[int, int]], dict[str, object]]:
        selected = final[final["final_status"].eq("included")].copy()
        audit_only = int(final["final_status"].eq("excluded").sum())
        original_count = len(final) - audit_only - int(final["symbol"].eq("600941.SH").sum())
        items = {row.symbol: PlannedUniverseItem(str(row.market), str(row.symbol), str(row.company_name), _value(row, "industry_level_1"), _value(row, "industry_level_2"), str(row.monopoly_type), str(row.stability_subtype), "automatic_rule", str(row.final_reason), str(row.risk_note or "")) for row in selected.itertuples(index=False)}
        for symbol, config in MANUAL_CORE_ADDITIONS.items():
            item = self._manual_item(symbol, config)
            items[symbol] = item
        if symbols is not None:
            items = {symbol: item for symbol, item in items.items() if symbol in symbols}
        self._validate_security_status(items.values(), calculation_date)
        events = self.provider.fetch_events(items)
        totals, _ = _aggregate_events(events, target_years(calculation_date))
        event_counts = _event_counts(events, target_years(calculation_date))
        errors: list[str] = []
        for symbol in items:
            values = totals[symbol]
            missing = [str(year) for year in target_years(calculation_date) if values.get(year, 0) <= 0]
            if missing: errors.append(f"{symbol}: missing_dps_years={','.join(missing)}")
            elif values[target_years(calculation_date)[-1]] < sum(values[year] for year in target_years(calculation_date)) / 3 * .7:
                errors.append(f"{symbol}: latest_year_dividend_decline")
        if errors:
            raise ValueError("Input validation failed: " + " | ".join(errors))
        summary = {"original_candidate_count": original_count, "manual_addition_count": len(MANUAL_CORE_ADDITIONS), "audit_only_exclusion_count": audit_only, "final_output_record_count": len(final), "included_count": len(selected), "review_required_count": int(final.final_status.eq("review_required").sum()), "excluded_count": audit_only, "planned_universe_count": len(items), "planned_annual_dps_count": len(items) * 3}
        return list(items.values()), totals, event_counts, summary

    def import_items(self, items: Iterable[PlannedUniverseItem], totals: dict[str, dict[int, float]], event_counts: dict[str, dict[int, int]], calculation_date: date, *, force: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        years = target_years(calculation_date)
        with self.connection:
            for item in items:
                exists = self.connection.execute("SELECT 1 FROM dividend_stable_universe WHERE market=? AND symbol=?", (item.market, item.symbol)).fetchone()
                if exists and not force:
                    continue
                self.connection.execute("""INSERT INTO dividend_stable_universe(market,symbol,company_name,industry_level_1,industry_level_2,monopoly_type,stability_subtype,inclusion_source,inclusion_reason,risk_note,included_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market,symbol) DO UPDATE SET company_name=excluded.company_name,industry_level_1=excluded.industry_level_1,industry_level_2=excluded.industry_level_2,monopoly_type=excluded.monopoly_type,stability_subtype=excluded.stability_subtype,inclusion_source=excluded.inclusion_source,inclusion_reason=excluded.inclusion_reason,risk_note=excluded.risk_note,updated_at=excluded.updated_at""", (item.market,item.symbol,item.company_name,item.industry_level_1,item.industry_level_2,item.monopoly_type,item.stability_subtype,item.inclusion_source,item.inclusion_reason,item.risk_note,now,now))
                for year in years:
                    self.connection.execute("""INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market,symbol,calendar_year) DO UPDATE SET cash_dividend_per_share=excluded.cash_dividend_per_share,dividend_event_count=excluded.dividend_event_count,calculation_method=excluded.calculation_method,source=excluded.source,data_quality_status=excluded.data_quality_status,calculated_at=excluded.calculated_at,updated_at=excluded.updated_at""", (item.market,item.symbol,year,totals[item.symbol][year],event_counts[item.symbol][year],METHOD,"tushare","complete",now,now))

    def _manual_item(self, symbol: str, config: dict[str, str]) -> PlannedUniverseItem:
        row = self.connection.execute("""SELECT m.security_name,i.level1_name,i.level2_name FROM a_share_security_master m LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol WHERE m.symbol=?""", (symbol,)).fetchone()
        if row is None: raise ValueError(f"manual addition missing security master: {symbol}")
        return PlannedUniverseItem("CN", symbol, str(row[0] or config["company_name"]), row[1], row[2], config["monopoly_type"], config["stability_subtype"], "manual_addition", config["reason"], "煤炭行业默认排除；此为显式人工核心补充。" if symbol == "601088.SH" else "企业经营历史长于A股上市历史。")

    def _validate_security_status(self, items: Iterable[PlannedUniverseItem], calculation_date: date) -> None:
        errors=[]
        for item in items:
            row=self.connection.execute("""SELECT m.is_active,m.delisted_date,s.is_st FROM a_share_security_master m LEFT JOIN a_share_security_status_history s ON s.symbol=m.symbol AND s.effective_date=(SELECT MAX(effective_date) FROM a_share_security_status_history x WHERE x.symbol=m.symbol AND x.effective_date<=?) WHERE m.symbol=?""", (calculation_date.isoformat(), item.symbol)).fetchone()
            if row is None or not row[0] or (row[1] and row[1] <= calculation_date.isoformat()) or row[2]: errors.append(item.symbol)
        if errors: raise ValueError("not active or ST: " + ",".join(errors))


def _value(row, name: str):
    value = getattr(row, name, None)
    return None if value is None or pd.isna(value) else str(value)


def _event_counts(events: Iterable[DividendEvent], years: tuple[int, int, int]) -> dict[str, dict[int, int]]:
    counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int)); seen=set()
    for event in events:
        if not event.ex_date or event.ex_date.year not in years or (event.cash_div_tax or 0) <= 0 or (event.div_proc or "实施") not in {"实施", "实施方案"}: continue
        key=(event.symbol,event.end_date,event.ann_date,event.ex_date,event.cash_div_tax,event.div_proc)
        if key in seen: continue
        seen.add(key);counts[event.symbol][event.ex_date.year]+=1
    return counts
