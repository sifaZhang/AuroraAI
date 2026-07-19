from __future__ import annotations

import argparse
import json
from typing import Any

from backend.expectation_gap.database import connect, database_path, migrate
from backend.expectation_gap.futu_client import CollectionResult, FutuResearchClient, utc_now
from backend.expectation_gap.repository import patch_analyst, patch_morningstar, patch_price
from backend.expectation_gap.service import calculate_gap_pct

SAMPLE_STOCKS = {
    "HK.00700": "腾讯控股",
    "HK.09988": "阿里巴巴-W",
    "HK.03690": "美团-W",
    "SH.600519": "贵州茅台",
    "SH.688192": "迪哲医药",
    "SH.600276": "恒瑞医药",
    "SZ.000001": "平安银行",
}


def _stock_parts(code: str) -> tuple[str, str, str]:
    exchange, symbol = code.split(".", 1)
    return symbol, "HK" if exchange == "HK" else "A", exchange


def _result_error(*results: CollectionResult) -> str | None:
    errors = [result.error for result in results if result.status == "error" and result.error]
    return " | ".join(errors) or None


def collect_one(connection, client: FutuResearchClient, code: str, name: str) -> dict[str, Any]:
    now = utc_now()
    symbol, market, exchange = _stock_parts(code)
    if market == "HK":
        price = client.snapshot(code)
        morningstar = client.morningstar(code)
        analyst = client.analyst(code)
    else:
        price = CollectionResult("no_data", error="A股价格改由AKShare/东方财富采集，本命令未请求富途")
        morningstar = CollectionResult("no_data", error="A股晨星改由手工CSV导入，本命令未请求富途")
        analyst = CollectionResult("no_data", error="A股分析师数据改由东方财富采集，本命令未请求富途")
    error = _result_error(price, morningstar, analyst)
    any_success = any(item.status == "success" for item in (price, morningstar, analyst))

    with connection:
        connection.execute(
            """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,created_at,updated_at)
               VALUES(?,?,?,?,?,'STOCK',1,?,?)
               ON CONFLICT(futu_code) DO UPDATE SET name=excluded.name,is_active=1,updated_at=excluded.updated_at""",
            (code, symbol, name, market, exchange, now, now),
        )
        stock_id = connection.execute("SELECT id FROM stocks WHERE futu_code=?", (code,)).fetchone()[0]
        patch_price(connection, stock_id, price, "futu_opend" if market == "HK" else "eastmoney", now)
        patch_morningstar(connection, stock_id, morningstar, "futu_opend", now)
        patch_analyst(connection, stock_id, analyst, "futu_opend" if market == "HK" else "eastmoney", now)

    p = price.data or {}
    m = morningstar.data or {}
    a = analyst.data or {}

    return {
        "code": code,
        "price": {"status": price.status, "data": price.data, "error": price.error},
        "morningstar": {"status": morningstar.status, "data": morningstar.data, "error": morningstar.error},
        "analyst": {"status": analyst.status, "data": analyst.data, "error": analyst.error},
        "morningstar_gap_pct": calculate_gap_pct(m.get("fair_value"), p.get("last_price")),
        "analyst_gap_pct": calculate_gap_pct(a.get("average"), p.get("last_price")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize an explicit reviewed expectation-gap batch; never enumerates a market.")
    parser.add_argument("--codes", default=",".join(SAMPLE_STOCKS), help="Comma-separated explicit Futu codes")
    args = parser.parse_args()
    codes = [code.strip().upper() for code in args.codes.split(",") if code.strip()]
    unknown = [code for code in codes if code not in SAMPLE_STOCKS]
    if unknown:
        parser.error(f"This V1 command only permits the reviewed sample codes: {', '.join(SAMPLE_STOCKS)}")

    connection = connect()
    migrate(connection)
    started = utc_now()
    run_id = connection.execute(
        "INSERT INTO refresh_runs(job_type,status,started_at,total_count) VALUES('sample','running',?,?)",
        (started, len(codes)),
    ).lastrowid
    connection.commit()
    results = []
    try:
        with FutuResearchClient() as client:
            state = client.global_state()
            print(json.dumps({"opend": {"status": state.status, "data": state.raw, "error": state.error}}, ensure_ascii=False, default=str))
            for code in codes:
                result = collect_one(connection, client, code, SAMPLE_STOCKS[code])
                results.append(result)
                print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as exc:
        connection.execute(
            "UPDATE refresh_runs SET status='failed',finished_at=?,error_message=? WHERE id=?",
            (utc_now(), str(exc), run_id),
        )
        connection.commit()
        raise

    failures = sum(all(result[key]["status"] == "error" for key in ("price", "morningstar", "analyst")) for result in results)
    status = "success" if failures == 0 else ("failed" if failures == len(results) else "partial")
    connection.execute(
        """UPDATE refresh_runs SET status=?,finished_at=?,processed_count=?,success_count=?,failure_count=?,last_code=? WHERE id=?""",
        (status, utc_now(), len(results), len(results) - failures, failures, codes[-1] if codes else None, run_id),
    )
    connection.commit()
    connection.close()
    print(f"database={database_path()} run_id={run_id} status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
