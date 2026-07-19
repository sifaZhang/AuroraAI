from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from backend.expectation_gap.database import connect, database_path, migrate
from backend.expectation_gap.futu_client import CollectionResult, FutuResearchClient, utc_now
from backend.expectation_gap.repository import ensure_expectation_row, patch_analyst, patch_morningstar, patch_price

REIT_PATTERN = re.compile(r"(?:REIT|房托|房產基金|房地产信托|產業信託|产业信托)", re.IGNORECASE)
TYPE_NAMES = ("STOCK", "ETF", "WARRANT", "BWRT", "BOND", "DRVT", "FUTURE")


def is_reit_name(name: str) -> bool:
    return REIT_PATTERN.search(name or "") is not None


def filter_hk_pool(frame, include_reit: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = Counter()
    stocks: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        stats["received_stock"] += 1
        if str(row.get("stock_type")) != "STOCK":
            stats["excluded_non_stock"] += 1
            continue
        if bool(row.get("delisting")):
            stats["excluded_delisted"] += 1
            continue
        name = str(row.get("name") or "").strip()
        reit = is_reit_name(name)
        if reit and not include_reit:
            stats["excluded_reit"] += 1
            continue
        stocks.append({
            "futu_code": str(row["code"]), "symbol": str(row["code"]).split(".", 1)[1],
            "name": name, "listing_date": None if str(row.get("listing_date")) in {"N/A", "1970-01-01"} else str(row.get("listing_date")),
            "security_type": "REIT" if reit else "STOCK", "is_reit": reit,
        })
    stocks.sort(key=lambda item: item["futu_code"])
    stats["selected"] = len(stocks)
    return stocks, dict(stats)


def is_due(row, field: str, force: bool) -> bool:
    if force or row is None or row[field] is None:
        return True
    try:
        return datetime.fromisoformat(row[field]) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _upsert_stock(connection, stock: dict[str, Any]) -> int:
    now = utc_now()
    connection.execute(
        """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,listing_date,is_reit,created_at,updated_at)
           VALUES(?,?,?,'HK','HK',?,1,?,?,?,?)
           ON CONFLICT(futu_code) DO UPDATE SET name=excluded.name,security_type=excluded.security_type,
             is_active=1,listing_date=excluded.listing_date,is_reit=excluded.is_reit,updated_at=excluded.updated_at""",
        (stock["futu_code"], stock["symbol"], stock["name"], stock["security_type"], stock["listing_date"], int(stock["is_reit"]), now, now),
    )
    return connection.execute("SELECT id FROM stocks WHERE futu_code=?", (stock["futu_code"],)).fetchone()[0]


def run(args) -> dict[str, Any]:
    started_clock = time.monotonic()
    connection = connect()
    migrate(connection)
    with FutuResearchClient() as client:
        pool_result = client.hk_security_pool()
        if pool_result.status != "success":
            raise RuntimeError(pool_result.error or pool_result.status)
        pool, pool_stats = filter_hk_pool(pool_result.raw, args.include_reit)
        type_stats = {name: client.security_type_count(name) for name in TYPE_NAMES}
        if args.codes:
            requested = {code.strip().upper() for code in args.codes.split(",") if code.strip()}
            known = {item["futu_code"] for item in pool}
            missing = sorted(requested - known)
            if missing:
                raise ValueError(f"代码不在筛选后的普通股池: {', '.join(missing)}")
            pool = [item for item in pool if item["futu_code"] in requested]
        if args.only_unrated:
            rated = {row[0] for row in connection.execute(
                """SELECT s.futu_code FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id
                   WHERE s.market='HK' AND (e.morningstar_fair_value IS NOT NULL OR e.analyst_average_target IS NOT NULL)"""
            )}
            pool = [item for item in pool if item["futu_code"] not in rated]
        if args.limit is not None:
            pool = pool[:args.limit]
        dry = {"pool": pool_stats, "security_type_counts": type_stats, "after_options": len(pool), "include_reit": args.include_reit}
        if args.dry_run:
            print(json.dumps(dry, ensure_ascii=False, indent=2))
            connection.close()
            return {"dry_run": dry}
        if args.limit is None and not args.codes:
            raise ValueError("安全限制：非dry-run必须显式提供 --limit 或 --codes")

        run_id = connection.execute(
            "INSERT INTO refresh_runs(job_type,status,started_at,total_count) VALUES('full','running',?,?)",
            (utc_now(), len(pool)),
        ).lastrowid
        connection.commit()
        snapshots = client.batch_snapshots([item["futu_code"] for item in pool])
        counts = Counter(total=len(pool))
        errors = Counter()
        coverage = Counter()
        consecutive_connection_errors = 0
        consecutive_permission_denied = 0
        rate_limited_since: float | None = None
        stopped_reason: str | None = None
        processed = 0
        for index, stock in enumerate(pool, 1):
            with connection:
                stock_id = _upsert_stock(connection, stock)
                ensure_expectation_row(connection, stock_id)
                existing = connection.execute(
                    "SELECT morningstar_next_check_at,analyst_next_check_at,morningstar_fair_value,analyst_average_target FROM stock_expectations WHERE stock_id=?",
                    (stock_id,),
                ).fetchone()
                patch_price(connection, stock_id, snapshots.get(stock["futu_code"], CollectionResult("no_data")), "futu_opend")
                morningstar_due = is_due(existing, "morningstar_next_check_at", args.force)
                analyst_due = is_due(existing, "analyst_next_check_at", args.force)
                morningstar = client.morningstar(stock["futu_code"]) if morningstar_due else CollectionResult("skipped_fresh")
                analyst = client.analyst(stock["futu_code"]) if analyst_due else CollectionResult("skipped_fresh")
                if morningstar_due:
                    patch_morningstar(connection, stock_id, morningstar, "futu_opend")
                if analyst_due:
                    patch_analyst(connection, stock_id, analyst, "futu_opend")
                final = connection.execute(
                    "SELECT morningstar_fair_value,analyst_average_target FROM stock_expectations WHERE stock_id=?", (stock_id,)
                ).fetchone()
                has_morningstar, has_analyst = final[0] is not None, final[1] is not None
                coverage["morningstar"] += has_morningstar
                coverage["analyst"] += has_analyst
                coverage["both"] += has_morningstar and has_analyst
                if morningstar.status == "skipped_fresh" and analyst.status == "skipped_fresh":
                    counts["skipped"] += 1
                elif not has_morningstar and not has_analyst and morningstar.status in {"no_data", "skipped_fresh"} and analyst.status in {"no_data", "skipped_fresh"}:
                    counts["no_data"] += 1
                else:
                    specific_errors = [result.status for result in (morningstar, analyst)
                                       if result.status in {"permission_denied", "rate_limited", "connection_error"}]
                    if specific_errors:
                        counts["failure"] += 1
                        errors.update(specific_errors)
                    else:
                        counts["success"] += 1
                statuses = {morningstar.status, analyst.status}
                consecutive_connection_errors = consecutive_connection_errors + 1 if "connection_error" in statuses else 0
                consecutive_permission_denied = consecutive_permission_denied + 1 if "permission_denied" in statuses else 0
                if "rate_limited" in statuses:
                    rate_limited_since = rate_limited_since or time.monotonic()
                else:
                    rate_limited_since = None
                connection.execute(
                    """UPDATE refresh_runs SET processed_count=?,success_count=?,no_data_count=?,failure_count=?,last_code=? WHERE id=?""",
                    (index, counts["success"], counts["no_data"], counts["failure"], stock["futu_code"], run_id),
                )
            processed = index
            elapsed = time.monotonic() - started_clock
            eta = elapsed / index * (len(pool) - index) if index else 0
            if index % 100 == 0 or index == len(pool):
                state = client.global_state()
                if state.status != "success":
                    stopped_reason = f"OpenD状态检查失败: {state.status}: {state.error}"
                elif not state.raw.get("qot_logined") or state.raw.get("program_status_type") != "READY":
                    stopped_reason = "OpenD登录状态丢失或程序未READY"
                print(f"[{index}/{len(pool)}] morningstar={coverage['morningstar']} analyst={coverage['analyst']} "
                      f"no_data={counts['no_data']} failure={counts['failure']} skipped={counts['skipped']} "
                      f"elapsed={elapsed:.0f}s ETA={eta:.0f}s", flush=True)
            if consecutive_connection_errors >= 20:
                stopped_reason = "连续20只connection_error"
            elif consecutive_permission_denied >= 20:
                stopped_reason = "连续20只permission_denied"
            elif rate_limited_since is not None and time.monotonic() - rate_limited_since > 300:
                stopped_reason = "rate_limited持续超过5分钟"
            if stopped_reason:
                print(f"SAFE_STOP: {stopped_reason}", flush=True)
                break
        status = "success" if stopped_reason is None and counts["failure"] == 0 else "partial"
        connection.execute("UPDATE refresh_runs SET status=?,finished_at=?,error_message=? WHERE id=?", (status, utc_now(), stopped_reason, run_id))
        connection.commit()
    connection.close()
    summary = {"run_id": run_id, "processed": processed, "elapsed_seconds": round(time.monotonic() - started_clock, 2),
               "counts": dict(counts), "coverage": dict(coverage), "errors": dict(errors), "database": str(database_path())}
    summary["stopped_reason"] = stopped_reason
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a filtered HK ordinary-stock pool through Futu OpenD.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true", help="Continue using saved per-field TTL/checkpoints")
    parser.add_argument("--force", action="store_true", help="Ignore TTL and request research data again")
    parser.add_argument("--codes", help="Comma-separated HK codes")
    parser.add_argument("--only-unrated", action="store_true")
    parser.add_argument("--include-reit", action="store_true", help="Include detected REIT names; default excludes them")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit必须大于0")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
