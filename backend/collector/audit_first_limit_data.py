"""Read-only PR6.0 audit of local data needed by the first-limit strategy."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.expectation_gap.database import connect, database_path

REQUIRED_DAILY_FIELDS = (
    "open", "high", "low", "close", "volume", "amount", "pre_close",
    "upper_limit", "lower_limit", "adjustment",
)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def audit_connection(connection: sqlite3.Connection, *, start_date: str = "2020-01-01") -> dict[str, Any]:
    """Return facts only; this function never writes or migrates the database."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": {"daily_coverage_start": start_date},
        "daily_bars": {"present": "a_share_daily_bars" in tables},
        "industry_history": {"present": "sector_memberships" in tables},
        "sector_scores": {"present": "sector_scores" in tables},
    }
    if "a_share_daily_bars" in tables:
        columns = _table_columns(connection, "a_share_daily_bars")
        row = connection.execute(
            """SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date),
                      COUNT(DISTINCT CASE WHEN trade_date<=? THEN stock_code END)
               FROM a_share_daily_bars WHERE adjustment='none'""",
            (start_date,),
        ).fetchone()
        complete = connection.execute(
            """SELECT COUNT(*) FROM (
                    SELECT stock_code FROM a_share_daily_bars WHERE adjustment='none'
                    GROUP BY stock_code HAVING MIN(trade_date)<=? AND MAX(trade_date)>=?
                )""",
            (start_date, date.today().isoformat()),
        ).fetchone()[0]
        report["daily_bars"].update({
            "columns": sorted(columns),
            "missing_strategy_fields": [field for field in REQUIRED_DAILY_FIELDS if field not in columns],
            "stock_count": row[0], "row_count": row[1], "first_trade_date": row[2],
            "last_trade_date": row[3], "stocks_reaching_start_date": row[4],
            "stocks_covering_start_to_today": complete,
            "coverage_to_today_is_strict": True,
        })
    if "sector_memberships" in tables:
        row = connection.execute(
            """SELECT COUNT(*), COUNT(DISTINCT stock_code), MIN(snapshot_date), MAX(snapshot_date),
                      SUM(historical_use_is_approximate)
               FROM sector_memberships WHERE is_current=1"""
        ).fetchone()
        report["industry_history"].update({
            "current_membership_rows": row[0], "current_member_stock_count": row[1],
            "oldest_snapshot_date": row[2], "latest_snapshot_date": row[3],
            "approximate_membership_rows": row[4] or 0,
            "historical_lookup_safe": False,
            "warning": "Only current constituent snapshots are stored; historical memberships are approximate.",
        })
    if "sector_scores" in tables:
        row = connection.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM sector_scores").fetchone()
        report["sector_scores"].update({"row_count": row[0], "first_trade_date": row[1], "last_trade_date": row[2]})
    daily_is_long_history_ready = bool(
        report["daily_bars"].get("stock_count")
        and report["daily_bars"].get("stocks_reaching_start_date") == report["daily_bars"].get("stock_count")
        and not report["daily_bars"].get("missing_strategy_fields")
    )
    report["decision"] = {
        "audit_complete": True,
        "ready_for_long_history_backtest": daily_is_long_history_ready
            and report["industry_history"].get("historical_lookup_safe") is True,
        "must_resolve_before_backtest": [
            "pre_close/upper_limit/lower_limit availability", "historical industry membership lookahead risk"
        ],
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    daily, industry, scores = report["daily_bars"], report["industry_history"], report["sector_scores"]
    return "\n".join([
        "# PR6.0 数据能力审计", "", f"生成时间：{report['generated_at']}", "",
        "## 日线覆盖", "", f"- 股票数：{daily.get('stock_count', 0)}", f"- 行数：{daily.get('row_count', 0)}",
        f"- 日期：{daily.get('first_trade_date')} 至 {daily.get('last_trade_date')}",
        f"- 缺失策略字段：{', '.join(daily.get('missing_strategy_fields', [])) or '无'}", "",
        "## 行业历史", "", f"- 当前成分股行数：{industry.get('current_membership_rows', 0)}",
        f"- 历史回测安全：{'是' if industry.get('historical_lookup_safe') else '否'}", f"- 风险：{industry.get('warning', '无')}", "",
        "## 行业评分", "", f"- 行数：{scores.get('row_count', 0)}；日期：{scores.get('first_trade_date')} 至 {scores.get('last_trade_date')}", "",
        "## PR6.1 前置决策", "", f"- 可进入多年回测：{'是' if report['decision']['ready_for_long_history_backtest'] else '否'}",
        *[f"- {item}" for item in report["decision"]["must_resolve_before_backtest"]], "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PR6.0 local data audit")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--start-date", default="2020-01-01")
    args = parser.parse_args(argv)
    connection = connect(args.db) if args.db else connect()
    try:
        report = audit_connection(connection, start_date=args.start_date)
    finally:
        connection.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pr6_0_data_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "pr6_0_data_audit.md").write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
