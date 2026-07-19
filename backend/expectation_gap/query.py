from __future__ import annotations

import json
from typing import Any

SORT_COLUMNS = {
    "symbol": "s.symbol", "name": "s.name", "last_price": "e.last_price",
    "morningstar_fair_value": "e.morningstar_fair_value", "morningstar_gap_pct": "e.morningstar_gap_pct",
    "analyst_average_target": "e.analyst_average_target", "analyst_gap_pct": "e.analyst_gap_pct",
    "analyst_count": "e.analyst_count", "updated_at": "e.updated_at",
}


def list_expectation_gaps(connection, *, market: str = "all", q: str = "", sort_by: str = "morningstar_gap_pct",
                          sort_order: str = "desc", page: int = 1, page_size: int = 50,
                          include_unrated: bool = False, include_anomalies: bool = False) -> dict[str, Any]:
    market = market.lower()
    if market not in {"all", "a", "hk"}:
        raise ValueError("market must be all, a, or hk")
    if sort_by not in SORT_COLUMNS:
        raise ValueError("unsupported sort_by")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    if page < 1 or page_size not in {20, 50, 100}:
        raise ValueError("invalid pagination")
    where = ["s.is_active=1"]
    params: list[Any] = []
    if market != "all":
        where.append("s.market=?")
        params.append("A" if market == "a" else "HK")
    if q.strip():
        where.append("(s.name LIKE ? OR s.symbol LIKE ? OR s.futu_code LIKE ?)")
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
    if not include_unrated:
        where.append("(e.morningstar_fair_value IS NOT NULL OR e.analyst_average_target IS NOT NULL)")
    if not include_anomalies:
        if sort_by in {"analyst_gap_pct", "analyst_average_target", "analyst_count"}:
            where.append("COALESCE(q.analyst_is_rankable,1)=1")
        elif sort_by in {"morningstar_gap_pct", "morningstar_fair_value"}:
            where.append("COALESCE(q.morningstar_is_rankable,1)=1")
        else:
            where.append("COALESCE(q.is_rankable,1)=1")
    if sort_by in {"analyst_gap_pct", "analyst_average_target"} and not include_anomalies:
        where.append("COALESCE(e.analyst_count,0)>=3")
    where_sql = " AND ".join(where)
    joins = """FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id
               LEFT JOIN stock_expectation_quality q ON q.stock_id=s.id"""
    total = connection.execute(f"SELECT COUNT(*) {joins} WHERE {where_sql}", params).fetchone()[0]
    sort_column = SORT_COLUMNS[sort_by]
    order_sql = f"CASE WHEN {sort_column} IS NULL THEN 1 ELSE 0 END, {sort_column} {sort_order.upper()}, s.symbol ASC"
    rows = connection.execute(f"""SELECT s.market,s.futu_code,s.symbol,s.name,e.last_price,e.price_time,e.price_source,
        e.morningstar_fair_value,e.morningstar_gap_pct,e.morningstar_star_rating,e.morningstar_rating_type,
        e.morningstar_data_date,e.morningstar_source,e.analyst_average_target,e.analyst_gap_pct,e.analyst_count,
        e.analyst_data_date,e.analyst_source,e.updated_at,q.quality_status,q.quality_reasons,q.is_rankable,
        q.morningstar_quality_status,q.morningstar_quality_reasons,q.morningstar_is_rankable,q.morningstar_quality_details,
        q.analyst_quality_status,q.analyst_quality_reasons,q.analyst_is_rankable,q.analyst_quality_details
        {joins} WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size]).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["display_source"] = "手工" if item["market"] == "A" else "富途自动"
        if item["market"] == "A":
            item["rating_label"] = "手工录入"
        else:
            item["rating_label"] = {1: "定量评级", 2: "定性评级"}.get(item["morningstar_rating_type"], "评级类型未知")
        item["data_date"] = max(filter(None, [item["morningstar_data_date"], item["analyst_data_date"]]), default=None)
        for field in ("quality_reasons", "morningstar_quality_reasons", "analyst_quality_reasons",
                      "morningstar_quality_details", "analyst_quality_details"):
            try:
                item[field] = json.loads(item[field]) if item[field] else ({} if field.endswith("details") else [])
            except json.JSONDecodeError:
                item[field] = {} if field.endswith("details") else [item[field]]
        source = "analyst" if sort_by in {"analyst_gap_pct", "analyst_average_target", "analyst_count"} else "morningstar"
        item["display_quality_status"] = item.get(f"{source}_quality_status") or item.get("quality_status")
        item["display_quality_reasons"] = item.get(f"{source}_quality_reasons") or []
        item["display_quality_details"] = item.get(f"{source}_quality_details") or {}
        items.append(item)
    latest = connection.execute("SELECT status,finished_at FROM refresh_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "last_refresh": dict(latest) if latest else None}
