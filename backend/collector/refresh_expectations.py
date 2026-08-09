from __future__ import annotations

import argparse

from backend.data_sources.market_price_provider import MarketPriceProvider, UnifiedMarketPriceProvider
from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.futu_client import CollectionResult, FutuResearchClient, utc_now
from backend.expectation_gap.repository import patch_analyst, patch_morningstar, patch_price


def refresh_a_prices(connection, *, price_provider: MarketPriceProvider | None = None) -> list[dict]:
    stocks = connection.execute("SELECT id,futu_code,symbol FROM stocks WHERE market='A' AND is_active=1").fetchall()
    if not stocks:
        return []
    provider = price_provider or UnifiedMarketPriceProvider()
    prices = provider.fetch_a_share_latest([row["symbol"] for row in stocks])
    results = []
    for stock in stocks:
        value = prices.get(stock["symbol"][:6])
        result = (CollectionResult("success", {"last_price": value.price, "price_time": value.price_time or utc_now()})
                  if value is not None and value.status == "success" else CollectionResult("no_data"))
        with connection:
            patch_price(connection, stock["id"], result, value.source if value and value.source else "auto")
        results.append({"code": stock["futu_code"], "price_status": result.status})
    return results


def refresh_hk(connection, codes: list[str] | None = None, *, price_provider: MarketPriceProvider | None = None) -> list[dict]:
    if codes:
        placeholders = ",".join("?" for _ in codes)
        stocks = connection.execute(f"SELECT id,futu_code FROM stocks WHERE market='HK' AND futu_code IN ({placeholders})", codes).fetchall()
    else:
        stocks = connection.execute("SELECT id,futu_code FROM stocks WHERE market='HK' AND is_active=1").fetchall()
    if not stocks:
        return []
    provider = price_provider or UnifiedMarketPriceProvider()
    prices = provider.fetch_hk_latest([stock["futu_code"] for stock in stocks])
    results = []
    with FutuResearchClient() as client:
        for stock in stocks:
            value = prices.get(stock["futu_code"])
            price = (CollectionResult("success", {"last_price": value.price, "price_time": value.price_time or utc_now()})
                     if value is not None and value.status == "success"
                     else CollectionResult(value.status, error=value.error) if value else CollectionResult("no_data"))
            morningstar, analyst = client.morningstar(stock["futu_code"]), client.analyst(stock["futu_code"])
            with connection:
                patch_price(connection, stock["id"], price, "futu_opend")
                patch_morningstar(connection, stock["id"], morningstar, "futu_opend")
                patch_analyst(connection, stock["id"], analyst, "futu_opend")
            results.append({"code": stock["futu_code"], "price": price.status, "morningstar": morningstar.status, "analyst": analyst.status})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh stored expectation-gap stocks only; never enumerates all A shares.")
    parser.add_argument("--market", choices=["a", "hk", "all"], default="all")
    parser.add_argument("--hk-codes", help="Optional comma-separated stored HK sample codes")
    args = parser.parse_args()
    connection = connect()
    migrate(connection)
    started = utc_now()
    run_id = connection.execute("INSERT INTO refresh_runs(job_type,status,started_at) VALUES('daily','running',?)", (started,)).lastrowid
    connection.commit()
    results = []
    try:
        if args.market in {"a", "all"}:
            results.extend(refresh_a_prices(connection))
        if args.market in {"hk", "all"}:
            codes = [x.strip().upper() for x in args.hk_codes.split(",")] if args.hk_codes else None
            results.extend(refresh_hk(connection, codes))
        failures = sum(not any(value == "success" for key, value in row.items() if key != "code") for row in results)
        status = "success" if failures == 0 else "partial"
        connection.execute("""UPDATE refresh_runs SET status=?,finished_at=?,total_count=?,processed_count=?,success_count=?,failure_count=?,last_code=? WHERE id=?""",
                           (status, utc_now(), len(results), len(results), len(results)-failures, failures,
                            results[-1]["code"] if results else None, run_id))
        connection.commit()
        print(results)
        return 0
    except Exception as exc:
        connection.execute("UPDATE refresh_runs SET status='failed',finished_at=?,error_message=? WHERE id=?", (utc_now(), str(exc), run_id))
        connection.commit()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
