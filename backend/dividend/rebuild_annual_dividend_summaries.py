"""Rebuild enabled-universe annual DPS from implemented dividend report periods."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.expectation_gap.database import connect, connect_readonly, database_path

from .annual_dps import METHOD, aggregate_events
from .dividend_candidate_rules import target_years
from .dividend_candidate_service import TushareDividendProvider
from .yield_service import calculate, save
from .share_basis_adjustment import current_basis_dps


def plan(connection, calculation_date: date):
    years = target_years(calculation_date)
    securities = [dict(row) for row in connection.execute("SELECT market,symbol,company_name FROM dividend_stable_universe WHERE is_enabled=1 ORDER BY symbol")]
    old = {(row["symbol"], row["calendar_year"]): row["cash_dividend_per_share"] for row in connection.execute("SELECT symbol,calendar_year,cash_dividend_per_share FROM annual_cash_dividend_summaries WHERE market='CN' AND calendar_year IN (?,?,?)", years)}
    provider = TushareDividendProvider(TushareClient(DataSourceSettings.from_env().tushare_token))
    events = provider.fetch_events(row["symbol"] for row in securities)
    totals, counts = aggregate_events(events, years)
    basis_totals, basis_warnings = current_basis_dps(events, years, calculation_date)
    missing = [row["symbol"] for row in securities if any(totals[row["symbol"]].get(year, 0) <= 0 for year in years)]
    rows = []
    for item in securities:
        values = [totals[item["symbol"]].get(year, 0.0) for year in years]
        prior = [old.get((item["symbol"], year), 0.0) for year in years]
        rows.append({"symbol": item["symbol"], "company_name": item["company_name"], "old": prior, "new": values, "counts": [counts[item["symbol"]].get(year, 0) for year in years], "changed": prior != values, "abs_delta": sum(abs(a-b) for a, b in zip(prior, values))})
    return years, rows, totals, counts, basis_totals, basis_warnings, missing


def apply(connection, calculation_date: date, years, rows, totals, counts, basis_totals) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection:
        for row in rows:
            for year in years:
                value = totals[row["symbol"]][year]
                count = counts[row["symbol"]][year]
                connection.execute("""INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at,current_basis_dps,share_basis_as_of) VALUES('CN',?,?,?,?,?,'tushare','complete',?,?,?,?) ON CONFLICT(market,symbol,calendar_year) DO UPDATE SET cash_dividend_per_share=excluded.cash_dividend_per_share,dividend_event_count=excluded.dividend_event_count,calculation_method=excluded.calculation_method,source=excluded.source,data_quality_status=excluded.data_quality_status,calculated_at=excluded.calculated_at,updated_at=excluded.updated_at,current_basis_dps=excluded.current_basis_dps,share_basis_as_of=excluded.share_basis_as_of""", (row["symbol"], year, value, count, METHOD, now, now, basis_totals.get(row["symbol"], {}).get(year, value), calculation_date.isoformat()))
        save(connection, calculate(connection, calculation_date))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--calculation-date", type=date.fromisoformat, required=True); parser.add_argument("--database", type=Path); parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(); db = args.database or database_path(); connection = connect(db) if args.apply else connect_readonly(db)
    try:
        years, rows, totals, counts, basis_totals, basis_warnings, missing = plan(connection, args.calculation_date)
        if missing: raise ValueError("missing report-period DPS: " + ",".join(missing))
        for row in sorted(rows, key=lambda item: (-item["abs_delta"], item["symbol"])):
            old, new = row["old"], row["new"]
            print(row["symbol"], row["company_name"], *(f"{value:.6f}" for value in old), *(f"{value:.6f}" for value in new), "changed=" + str(row["changed"]))
        print("enabled=", len(rows), "changed=", sum(row["changed"] for row in rows), "years=", years)
        if basis_warnings: print("basis_warnings=", len(basis_warnings))
        if args.apply: apply(connection, args.calculation_date, years, rows, totals, counts, basis_totals)
    finally: connection.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
