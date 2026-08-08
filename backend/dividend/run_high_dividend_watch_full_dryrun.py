"""Read-only full-market runner for the minimal three-year dividend-yield rule."""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.dividend.annual_dps import aggregate_events, event_key
from backend.dividend.dividend_candidate_service import _float, _parse_date, _text
from backend.dividend.high_dividend_watch_service import classify_industry, qualify_historical_dividend
from backend.dividend.models import DividendEvent
from backend.expectation_gap.database import connect_readonly


YEARS = (2023, 2024, 2025)
PERIODS = tuple(f"{year}{suffix}" for year in YEARS for suffix in ("0331", "0630", "0930", "1231"))
PAGE_SIZE = 2000
MAX_PRICE_LOOKBACK_DAYS = 10
CSV_FIELDS = [
    "symbol", "company_name", "industry", "industry_level_1", "industry_level_2", "suggested_stability_subtype",
    "2023_event_count", "2024_event_count", "2025_event_count",
    "2023_dps", "2023_reference_date", "2023_reference_price", "2023_historical_yield",
    "2024_dps", "2024_reference_date", "2024_reference_price", "2024_historical_yield",
    "2025_dps", "2025_reference_date", "2025_reference_price", "2025_historical_yield",
    "three_year_historical_average_yield", "three_year_average_dps",
    "latest_price", "price_date", "latest_year_yield", "three_year_average_yield",
    "already_in_universe",
]


def _normal_a_shares(connection, calculation_date: date):
    rows = connection.execute(
        """SELECT m.symbol,m.security_name,i.level1_name,i.level2_name
           FROM a_share_security_master m
           LEFT JOIN industry_memberships_current i ON i.symbol=m.symbol
           WHERE m.exchange IN ('SH','SZ')
             AND m.symbol NOT LIKE '20%.SZ' AND m.symbol NOT LIKE '900%.SH'
             AND m.is_active=1
             AND (m.delisted_date IS NULL OR m.delisted_date>?)
             AND COALESCE((SELECT s.is_st FROM a_share_security_status_history s
                           WHERE s.symbol=m.symbol AND s.effective_date<=?
                           ORDER BY s.effective_date DESC LIMIT 1),0)=0
             AND UPPER(COALESCE(m.security_name,'')) NOT LIKE '%ST%'
             AND INSTR(COALESCE(m.security_name,''), CHAR(36864))=0
             AND COALESCE(m.security_name,'') NOT LIKE '%退%'
           ORDER BY m.symbol""",
        (calculation_date.isoformat(), calculation_date.isoformat()),
    ).fetchall()
    return [tuple(row) for row in rows]


def _trade_dates(client: TushareClient, start: date, end: date) -> list[str]:
    frame = client.call(
        "trade_cal", exchange="SSE", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        fields="cal_date,is_open",
    )
    dates = [str(row["cal_date"]) for row in frame.to_dict("records") if int(row.get("is_open") or 0) == 1]
    if not dates:
        raise ValueError(f"no trading date between {start} and {end}")
    return sorted(dates, reverse=True)


def _daily_prices(client: TushareClient, trade_date: str):
    frame = client.call("daily", trade_date=trade_date, fields="ts_code,trade_date,close")
    result = {}
    for row in frame.to_dict("records"):
        close = float(row.get("close") or 0)
        if close > 0:
            result[str(row["ts_code"]).upper()] = close
    return result


def _format_api_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _batch_dividend_events(client: TushareClient):
    fields = "ts_code,ann_date,end_date,ex_date,cash_div_tax,div_proc,record_date,pay_date,imp_ann_date,base_date"
    events = []
    failures = []
    period_stats = []
    request_count = 0
    started = time.monotonic()
    for period in PERIODS:
        offset = 0
        raw_rows = 0
        pages = 0
        while True:
            try:
                frame = client.call("dividend", end_date=period, offset=offset, limit=PAGE_SIZE, fields=fields)
                request_count += 1
                pages += 1
                raw_rows += len(frame)
                for row in frame.to_dict("records"):
                    events.append(DividendEvent(
                        str(row.get("ts_code") or "").upper(), _parse_date(row.get("ann_date")),
                        _parse_date(row.get("ex_date")), _float(row.get("cash_div_tax")),
                        _text(row.get("div_proc")), _parse_date(row.get("end_date")),
                        _parse_date(row.get("record_date")), _parse_date(row.get("pay_date")),
                        _parse_date(row.get("imp_ann_date")), _parse_date(row.get("base_date")),
                    ))
            except Exception as exc:
                failures.append({"period": period, "offset": offset, "error": f"{type(exc).__name__}: {exc}"})
                break
            if len(frame) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        period_stats.append({"period": period, "raw_rows": raw_rows, "pages": pages})
        print(json.dumps({"phase": "dividend_period", **period_stats[-1]}, ensure_ascii=False), flush=True)
    unique_events = list({event_key(item): item for item in events}.values())
    return unique_events, failures, period_stats, request_count, time.monotonic() - started


def _batch_prices_with_fallback(client: TushareClient, trade_dates: list[str], symbols: set[str]):
    found = {}
    requests = 0
    for trade_date in trade_dates[:MAX_PRICE_LOOKBACK_DAYS]:
        daily = _daily_prices(client, trade_date)
        requests += 1
        for symbol in symbols - found.keys():
            price = daily.get(symbol)
            if price is not None:
                found[symbol] = (trade_date, price)
        if len(found) == len(symbols):
            break
    return found, requests


def run(output: Path, calculation_date: date) -> dict[str, object]:
    started = time.monotonic()
    connection = connect_readonly()
    client = TushareClient(DataSourceSettings.from_env().tushare_token)
    try:
        all_a_count = connection.execute(
            """SELECT COUNT(*) FROM a_share_security_master
               WHERE exchange IN ('SH','SZ')
                 AND symbol NOT LIKE '20%.SZ' AND symbol NOT LIKE '900%.SH'"""
        ).fetchone()[0]
        securities = _normal_a_shares(connection, calculation_date)
        events, request_failures, period_stats, dividend_requests, dividend_seconds = _batch_dividend_events(client)
        failures = [
            {"symbol": "*", "company_name": "", "error": f"{item['period']} offset={item['offset']}: {item['error']}"}
            for item in request_failures
        ]

        totals, event_counts = aggregate_events(events, YEARS)
        complete_symbols = {
            symbol for symbol, *_ in securities
            if all(totals.get(symbol, {}).get(year, 0) > 0 for year in YEARS)
        }
        normal_symbols = {symbol for symbol, *_ in securities}
        reference_points = {}
        year_end_daily_requests = 0
        for year in YEARS:
            trade_dates = _trade_dates(client, date(year, 12, 1), date(year, 12, 31))
            reference_points[year], requests = _batch_prices_with_fallback(client, trade_dates, normal_symbols)
            year_end_daily_requests += requests
        latest_trade_dates = _trade_dates(
            client, calculation_date - timedelta(days=30), calculation_date,
        )
        latest_prices, latest_daily_requests = _batch_prices_with_fallback(
            client, latest_trade_dates, normal_symbols,
        )
        universe = {row[0] for row in connection.execute("SELECT symbol FROM dividend_stable_universe WHERE market='CN'")}

        candidates = []
        for symbol, company_name, level1, level2 in securities:
            if symbol not in complete_symbols:
                continue
            dps = {year: totals[symbol][year] for year in YEARS}
            prices = {
                year: (reference_points[year][symbol][1] if symbol in reference_points[year] else None)
                for year in YEARS
            }
            yields, qualification_failures = qualify_historical_dividend(dps, prices)
            if qualification_failures:
                continue
            latest = latest_prices.get(symbol)
            average_dps = sum(dps.values()) / len(YEARS)
            average_historical_yield = sum(yields.values()) / len(YEARS)
            latest_price = latest[1] if latest else None
            industry = " ".join(filter(None, (level1, level2)))
            row = {
                "symbol": symbol, "company_name": company_name, "industry": industry,
                "industry_level_1": level1, "industry_level_2": level2,
                "suggested_stability_subtype": classify_industry(industry),
                "three_year_historical_average_yield": average_historical_yield,
                "three_year_average_dps": average_dps,
                "latest_price": latest_price, "price_date": _format_api_date(latest[0]) if latest else None,
                "latest_year_yield": dps[2025] / latest_price if latest_price else None,
                "three_year_average_yield": average_dps / latest_price if latest_price else None,
                "already_in_universe": symbol in universe,
            }
            for year in YEARS:
                row[f"{year}_dps"] = dps[year]
                row[f"{year}_event_count"] = event_counts[symbol][year]
                point = reference_points[year].get(symbol)
                row[f"{year}_reference_date"] = _format_api_date(point[0]) if point else None
                row[f"{year}_reference_price"] = prices[year]
                row[f"{year}_historical_yield"] = yields[year]
            candidates.append(row)

        candidates.sort(key=lambda row: (-row["three_year_historical_average_yield"], row["symbol"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        with temporary_output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(candidates)
        temporary_output.replace(output)
        type_counts = {
            subtype: sum(row["suggested_stability_subtype"] == subtype for row in candidates)
            for subtype in ("stable_monopoly", "resource_monopoly_cyclical", "high_dividend_watch")
        }
        summary = {
            "all_a_count": all_a_count,
            "normal_non_st_count": len(securities),
            "complete_three_year_dps_count": len(complete_symbols),
            "qualified_count": len(candidates),
            **{f"{key}_count": value for key, value in type_counts.items()},
            "already_in_universe_count": sum(row["already_in_universe"] for row in candidates),
            "new_candidate_count": sum(not row["already_in_universe"] for row in candidates),
            "failure_count": len(failures),
            "successful_scan_count": len(securities) if not failures else 0,
            "dividend_period_count": len(PERIODS),
            "dividend_request_count": dividend_requests,
            "dividend_elapsed_seconds": round(dividend_seconds, 3),
            "dividend_period_stats": period_stats,
            "year_end_daily_request_count": year_end_daily_requests,
            "latest_daily_request_count": latest_daily_requests,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "output": str(output),
            "failures": failures,
        }
        print("FINAL_SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        audit_symbols = {
            "601328.SH", "600900.SH", "601088.SH", "600028.SH", "000333.SZ",
            "000651.SZ", "000895.SZ", "603519.SH", "603156.SH", "002582.SZ",
        }
        audit = []
        for symbol, company_name, level1, level2 in securities:
            if symbol not in audit_symbols:
                continue
            dps = {year: totals.get(symbol, {}).get(year) for year in YEARS}
            prices = {year: (reference_points[year][symbol][1] if symbol in reference_points[year] else None) for year in YEARS}
            historical_yields, qualification_failures = qualify_historical_dividend(dps, prices)
            latest = latest_prices.get(symbol)
            average_dps = sum(dps.values()) / 3 if all(value is not None for value in dps.values()) else None
            audit.append({
                "symbol": symbol, "company_name": company_name,
                "suggested_stability_subtype": classify_industry(" ".join(filter(None, (level1, level2)))) if not qualification_failures else None,
                "annual_dps": dps, "reference_prices": prices,
                "reference_dates": {year: (_format_api_date(reference_points[year][symbol][0]) if symbol in reference_points[year] else None) for year in YEARS},
                "historical_yields": historical_yields, "qualified": not qualification_failures,
                "qualification_failures": qualification_failures,
                "latest_price": latest[1] if latest else None,
                "price_date": _format_api_date(latest[0]) if latest else None,
                "latest_year_yield": dps[2025] / latest[1] if latest and dps[2025] else None,
                "three_year_average_yield": average_dps / latest[1] if latest and average_dps else None,
            })
        audit_path = output.with_suffix(".summary.json")
        temporary_audit_path = audit_path.with_suffix(audit_path.suffix + ".tmp")
        temporary_audit_path.write_text(
            json.dumps(
                {"summary": summary, "ten_symbol_audit": audit, "candidate_symbols": [row["symbol"] for row in candidates]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        temporary_audit_path.replace(audit_path)
        print("TEN_SYMBOL_AUDIT " + json.dumps(audit, ensure_ascii=False), flush=True)
        print("FINAL_CANDIDATE_SYMBOLS " + json.dumps([row["symbol"] for row in candidates], ensure_ascii=False), flush=True)
        return summary
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calculation-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.calculation_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
