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
        clauses.append("source=?")
        params.append(source)
    else:
        clauses.append("source IN ('sw_l1','sw_l2','eastmoney')")
    if trade_date:
        clauses.append("trade_date=?")
        params.append(trade_date)
    else:
        dated = [(item, value) for item, value in latest_dates.items() if value]
        if dated:
            clauses.append("(" + " OR ".join("(source=? AND trade_date=?)" for _ in dated) + ")")
            for item, value in dated:
                params.extend((item, value))
        else:
            clauses.append("1=0")
    where = " AND ".join(clauses)
    total = connection.execute(f"SELECT COUNT(*) FROM sector_scores WHERE {where}", params).fetchone()[0]
    sort_expression = {
        "total_score": "breadth.total_score",
        "breadth_score": "breadth.breadth_score",
    }.get(sort_by, f"scores.{sort_by}")
    rows = connection.execute(
        f"""SELECT scores.source,scores.sector_level,scores.sector_code,scores.sector_name,
                   scores.trade_date,scores.trend_score,scores.trend_level,
                   scores.close,scores.ma5,scores.ma10,scores.ma20,scores.volume_ratio,scores.is_20d_high,
                   scores.relative_strength_score,scores.benchmark_code,scores.benchmark_trade_date,
                   scores.sector_return_5d,scores.benchmark_return_5d,scores.excess_return_5d,
                   scores.sector_return_10d,scores.benchmark_return_10d,scores.excess_return_10d,
                   scores.sector_return_20d,scores.benchmark_return_20d,scores.excess_return_20d,
                   scores.relative_strength_updated_at,scores.capital_flow_score,scores.composite_score,
                   scores.score_status,scores.missing_components,scores.updated_at,
                   breadth.trade_date AS breadth_trade_date,breadth.membership_snapshot_date,
                   breadth.above_ma5_ratio,breadth.above_ma5_numerator,breadth.above_ma5_valid_count,
                   breadth.above_ma10_ratio,breadth.above_ma10_numerator,breadth.above_ma10_valid_count,
                   breadth.above_ma20_ratio,breadth.above_ma20_numerator,breadth.above_ma20_valid_count,
                   breadth.advancing_ratio,breadth.advancing_numerator,breadth.advancing_valid_count,
                   breadth.new_high_20_ratio,breadth.new_high_20_numerator,breadth.new_high_20_valid_count,
                   breadth.volume_expansion_ratio,breadth.volume_expansion_numerator,
                   breadth.volume_expansion_valid_count,breadth.total_members,breadth.valid_members,
                   breadth.coverage_ratio,breadth.excluded_members,breadth.ma20_score,
                   breadth.advancing_score,breadth.new_high_20_score,breadth.volume_expansion_score,
                   breadth.breadth_score,breadth.total_score,breadth.status AS breadth_status,
                   breadth.quality_warnings,breadth.is_approximate,breadth.lookahead_warning,
                   breadth.calculation_version,breadth.updated_at AS breadth_updated_at
            FROM sector_scores AS scores
            LEFT JOIN sector_breadth_scores AS breadth
              ON scores.source='sw_l1'
             AND breadth.classification_system='sw_level1'
             AND breadth.sector_code=scores.sector_code
             AND breadth.trade_date=scores.trade_date
             AND breadth.calculation_version='breadth_v1'
            WHERE {where.replace('source', 'scores.source').replace('trade_date', 'scores.trade_date')}
            ORDER BY ({sort_expression} IS NULL) ASC, {sort_expression} {order.upper()}, scores.sector_code ASC
            LIMIT ? OFFSET ?""",
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
        "breadth_available_count": sum(item["breadth_status"] == "success" for item in items),
        "source_status": statuses.get(source) if source != "all" else statuses,
    }


def get_sector_score(connection: sqlite3.Connection, source: str, sector_code: str, trade_date: str | None = None):
    if source not in SOURCES - {"all"}:
        raise ValueError(f"不支持的数据源: {source}")
    params: list[Any] = [source, sector_code]
    date_clause = ""
    if trade_date:
        date_clause = " AND trade_date=?"
        params.append(trade_date)
    row = connection.execute(
        f"""SELECT source,sector_level,sector_code,sector_name,trade_date,trend_score,trend_level,
                   close,ma5,ma10,ma20,volume_ratio,is_20d_high,
                   relative_strength_score,benchmark_code,benchmark_trade_date,
                   sector_return_5d,benchmark_return_5d,excess_return_5d,
                   sector_return_10d,benchmark_return_10d,excess_return_10d,
                   sector_return_20d,benchmark_return_20d,excess_return_20d,
                   relative_strength_updated_at,capital_flow_score,composite_score,
                   score_status,missing_components,updated_at
            FROM sector_scores WHERE source=? AND sector_code=?{date_clause}
            ORDER BY trade_date DESC LIMIT 1""",
        params,
    ).fetchone()
    if not row:
        return None
    item = _public_item(dict(row))
    _attach_breadth(connection, item)
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
    _normalize_breadth(item)
    return item


def _attach_score_change(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    """Attach change versus the sector's preceding stored trade date."""
    if item["source"] != "sw_l1":
        item.update({
            "previous_trade_date": None, "previous_total_score": None,
            "total_score_change": None, "trend_score_change": None,
            "breadth_score_change": None,
        })
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
        item.update({
            "previous_trade_date": None, "previous_total_score": None,
            "total_score_change": None, "trend_score_change": None,
            "breadth_score_change": None,
        })
        return
    item["previous_trade_date"] = row["trade_date"]
    item["previous_total_score"] = row["total_score"]
    for public, current, previous in (
        ("total_score_change", item.get("total_score"), row["total_score"]),
        ("trend_score_change", item.get("trend_score"), row["trend_score"]),
        ("breadth_score_change", item.get("breadth_score"), row["breadth_score"]),
    ):
        item[public] = (
            round(float(current) - float(previous), 4)
            if current is not None and previous is not None else None
        )


def _attach_breadth(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    if item["source"] != "sw_l1":
        _normalize_breadth(item)
        return
    row = connection.execute(
        """SELECT trade_date AS breadth_trade_date,membership_snapshot_date,
                  above_ma5_ratio,above_ma5_numerator,above_ma5_valid_count,
                  above_ma10_ratio,above_ma10_numerator,above_ma10_valid_count,
                  above_ma20_ratio,above_ma20_numerator,above_ma20_valid_count,
                  advancing_ratio,advancing_numerator,advancing_valid_count,
                  new_high_20_ratio,new_high_20_numerator,new_high_20_valid_count,
                  volume_expansion_ratio,volume_expansion_numerator,volume_expansion_valid_count,
                  total_members,valid_members,coverage_ratio,excluded_members,
                  ma20_score,advancing_score,new_high_20_score,volume_expansion_score,
                  breadth_score,total_score,status AS breadth_status,quality_warnings,
                  is_approximate,lookahead_warning,calculation_version,updated_at AS breadth_updated_at
           FROM sector_breadth_scores
           WHERE classification_system='sw_level1' AND sector_code=? AND trade_date=?
             AND calculation_version='breadth_v1' LIMIT 1""",
        (item["sector_code"], item["trade_date"]),
    ).fetchone()
    if row:
        item.update(dict(row))
    _normalize_breadth(item)


def _normalize_breadth(item: dict[str, Any]) -> None:
    """Expose a stable public shape without turning unavailable data into zero scores."""
    if not item.get("breadth_trade_date"):
        item.update({
            "breadth_status": "not_calculated", "breadth_score": None, "total_score": None,
            "breadth_max_score": 30, "total_max_score": 100, "breadth_metrics": None,
            "is_approximate": None, "quality_warnings": [], "excluded_members": {},
        })
        return
    item["breadth_max_score"] = 30
    item["total_max_score"] = 100
    item["is_approximate"] = bool(item.get("is_approximate"))
    for field, fallback in (("quality_warnings", []), ("excluded_members", {})):
        raw = item.get(field)
        try:
            item[field] = json.loads(raw) if isinstance(raw, str) else (raw or fallback)
        except (json.JSONDecodeError, TypeError):
            item[field] = fallback
    item["breadth_metrics"] = {
        name: {
            "ratio": item.pop(f"{name}_ratio"),
            "numerator": item.pop(f"{name}_numerator"),
            "denominator": item.pop(f"{name}_valid_count"),
        }
        for name in ("above_ma5", "above_ma10", "above_ma20", "advancing", "new_high_20", "volume_expansion")
    }
