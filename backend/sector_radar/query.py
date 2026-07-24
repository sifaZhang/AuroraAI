"""Read-only Market Pulse sector score queries."""

from __future__ import annotations

import sqlite3
import json
from typing import Any

SOURCES = {"sw_l1", "sw_l2", "eastmoney", "all"}
SORT_FIELDS = {
    "total_score", "trend_score", "breadth_score", "relative_strength_score",
    "sector_name", "trade_date", "updated_at",
}
ORDERS = {"asc", "desc"}
BREADTH_VERSION = "breadth_v1"

BREADTH_SELECT = """
       b.total_score,b.breadth_score,b.status AS breadth_status,
       b.above_ma5_ratio,b.above_ma5_numerator,b.above_ma5_valid_count,
       b.above_ma10_ratio,b.above_ma10_numerator,b.above_ma10_valid_count,
       b.above_ma20_ratio,b.above_ma20_numerator,b.above_ma20_valid_count,
       b.advancing_ratio,b.advancing_numerator,b.advancing_valid_count,
       b.new_high_20_ratio,b.new_high_20_numerator,b.new_high_20_valid_count,
       b.volume_expansion_ratio,b.volume_expansion_numerator,b.volume_expansion_valid_count,
       b.coverage_ratio,b.excluded_members,b.membership_snapshot_date,b.is_approximate,
       b.lookahead_warning,b.calculation_version,b.quality_warnings
"""

BREADTH_JOIN = """
    LEFT JOIN sector_breadth_scores b
      ON b.classification_system=CASE s.source WHEN 'sw_l1' THEN 'sw_level1' END
     AND b.sector_code=s.sector_code
     AND b.trade_date=s.trade_date
     AND b.calculation_version='breadth_v1'
"""


def _status(connection: sqlite3.Connection, source: str) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT source,status,last_attempt_at,last_success_at,last_error_type,last_error_message
           FROM sector_source_status WHERE source=?""",
        (source,),
    ).fetchone()
    return dict(row) if row else {
        "source": source, "status": "unknown", "last_attempt_at": None, "last_success_at": None,
        "last_error_type": None, "last_error_message": None,
    }


def list_sector_scores(
    connection: sqlite3.Connection, *, source: str = "sw_l1", trade_date: str | None = None,
    sort_by: str = "trend_score", order: str = "desc", page: int = 1, page_size: int = 50,
) -> dict[str, Any]:
    if source not in SOURCES:
        raise ValueError(f"不支持的数据源: {source}")
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"不支持的排序字段: {sort_by}")
    if order not in ORDERS:
        raise ValueError(f"不支持的排序方向: {order}")
    if page < 1 or page_size < 1 or page_size > 200:
        raise ValueError("page必须大于0，page_size必须在1到200之间")

    selected_sources = ("sw_l1", "sw_l2", "eastmoney") if source == "all" else (source,)
    latest_dates = {
        item: connection.execute("SELECT MAX(trade_date) FROM sector_scores WHERE source=?", (item,)).fetchone()[0]
        for item in selected_sources
    }
    clauses, params = [], []
    if source != "all":
        clauses.append("s.source=?")
        params.append(source)
    else:
        clauses.append("s.source IN ('sw_l1','sw_l2','eastmoney')")
    if trade_date:
        clauses.append("s.trade_date=?")
        params.append(trade_date)
    else:
        dated = [(item, value) for item, value in latest_dates.items() if value]
        if dated:
            clauses.append("(" + " OR ".join("(s.source=? AND s.trade_date=?)" for _ in dated) + ")")
            for item, value in dated:
                params.extend((item, value))
        else:
            clauses.append("1=0")
    where = " AND ".join(clauses)
    total = connection.execute(f"SELECT COUNT(*) FROM sector_scores s WHERE {where}", params).fetchone()[0]
    sort_expression = {
        "total_score": "b.total_score", "breadth_score": "b.breadth_score",
        "trend_score": "COALESCE(b.trend_score,s.trend_score)",
    }.get(sort_by, f"s.{sort_by}")
    rows = connection.execute(
        f"""SELECT s.source,s.sector_level,s.sector_code,s.sector_name,s.trade_date,
                   COALESCE(b.trend_score,s.trend_score) AS trend_score,s.trend_level,
                   close,ma5,ma10,ma20,volume_ratio,is_20d_high,
                   relative_strength_score,benchmark_code,benchmark_trade_date,
                   sector_return_5d,benchmark_return_5d,excess_return_5d,
                   sector_return_10d,benchmark_return_10d,excess_return_10d,
                   sector_return_20d,benchmark_return_20d,excess_return_20d,
                   relative_strength_updated_at,capital_flow_score,composite_score,
                   score_status,missing_components,s.updated_at,
                   {BREADTH_SELECT}
            FROM sector_scores s {BREADTH_JOIN} WHERE {where}
            ORDER BY ({sort_expression} IS NULL) ASC, {sort_expression} {order.upper()},
                     s.sector_code ASC LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    items = []
    for row in rows:
        item = _public_item(dict(row))
        _attach_score_change(connection, item)
        items.append(item)
    statuses = {item: _status(connection, item) for item in selected_sources}
    resolved_date = trade_date or (latest_dates.get(source) if source != "all" else None)
    return {
        "items": items, "page": page, "page_size": page_size, "total": total, "source": source,
        "trade_date": resolved_date, "latest_trade_date": latest_dates.get(source) if source != "all" else max((d for d in latest_dates.values() if d), default=None),
        "latest_trade_dates": latest_dates,
        "source_status": statuses.get(source) if source != "all" else statuses,
    }


def get_sector_score(connection: sqlite3.Connection, source: str, sector_code: str, trade_date: str | None = None):
    if source not in SOURCES - {"all"}:
        raise ValueError(f"不支持的数据源: {source}")
    params: list[Any] = [source, sector_code]
    date_clause = ""
    if trade_date:
        date_clause = " AND s.trade_date=?"
        params.append(trade_date)
    row = connection.execute(
        f"""SELECT s.source,s.sector_level,s.sector_code,s.sector_name,s.trade_date,
                   COALESCE(b.trend_score,s.trend_score) AS trend_score,s.trend_level,
                   close,ma5,ma10,ma20,volume_ratio,is_20d_high,
                   relative_strength_score,benchmark_code,benchmark_trade_date,
                   sector_return_5d,benchmark_return_5d,excess_return_5d,
                   sector_return_10d,benchmark_return_10d,excess_return_10d,
                   sector_return_20d,benchmark_return_20d,excess_return_20d,
                   relative_strength_updated_at,capital_flow_score,composite_score,
                   score_status,missing_components,s.updated_at,
                   {BREADTH_SELECT}
            FROM sector_scores s {BREADTH_JOIN}
            WHERE s.source=? AND s.sector_code=?{date_clause}
            ORDER BY s.trade_date DESC LIMIT 1""",
        params,
    ).fetchone()
    if not row:
        return None
    item = _public_item(dict(row))
    _attach_score_change(connection, item)
    item["source_status"] = _status(connection, source)
    return item


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    item["sector_level"] = int(item["sector_level"]) if str(item["sector_level"]).isdigit() else item["sector_level"]
    item["is_20d_high"] = bool(item["is_20d_high"])
    item["trend_max_score"] = 70
    item["relative_strength_max_score"] = 15
    item["capital_flow_max_score"] = 15
    item["composite_max_score"] = 100
    if item.get("score_status") is None:
        item["score_status"] = "partial"
    raw_missing = item.get("missing_components")
    try:
        item["missing_components"] = json.loads(raw_missing) if raw_missing else ["capital_flow", "relative_strength"]
    except (json.JSONDecodeError, TypeError):
        item["missing_components"] = ["capital_flow", "relative_strength"]
    if item.get("breadth_status") is None:
        item["breadth_status"] = "not_calculated"
    for field, default in (("excluded_members", {}), ("quality_warnings", [])):
        raw = item.get(field)
        try:
            item[field] = json.loads(raw) if raw else default
        except (json.JSONDecodeError, TypeError):
            item[field] = default
    item["is_approximate"] = bool(item["is_approximate"]) if item.get("is_approximate") is not None else False
    item["breadth_max_score"] = 30
    item["total_max_score"] = 100
    return item


def _attach_score_change(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    """Attach a same-sector comparison with the preceding stored trade date."""
    empty = {
        "previous_trade_date": None, "previous_total_score": None,
        "total_score_change": None, "trend_score_change": None,
        "breadth_score_change": None,
    }
    if item["source"] != "sw_l1":
        item.update(empty)
        return
    row = connection.execute(
        """SELECT trade_date,total_score,trend_score,breadth_score
           FROM sector_breadth_scores
           WHERE classification_system='sw_level1' AND sector_code=?
             AND calculation_version='breadth_v1' AND trade_date<?
           ORDER BY trade_date DESC LIMIT 1""",
        (item["sector_code"], item["trade_date"]),
    ).fetchone()
    if not row:
        item.update(empty)
        return
    item["previous_trade_date"] = row["trade_date"]
    item["previous_total_score"] = row["total_score"]
    for public, current, previous in (
        ("total_score_change", item.get("total_score"), row["total_score"]),
        ("trend_score_change", item.get("trend_score"), row["trend_score"]),
        ("breadth_score_change", item.get("breadth_score"), row["breadth_score"]),
    ):
        item[public] = round(float(current) - float(previous), 4) if current is not None and previous is not None else None
