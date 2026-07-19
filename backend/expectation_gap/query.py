from __future__ import annotations

from typing import Any

SORT_COLUMNS = {
    "symbol": "s.symbol", "name": "s.name", "last_price": "e.last_price",
    "morningstar_fair_value": "e.morningstar_fair_value", "morningstar_gap_pct": "e.morningstar_gap_pct",
    "analyst_average_target": "e.analyst_average_target", "analyst_gap_pct": "e.analyst_gap_pct",
    "analyst_count": "e.analyst_count", "updated_at": "e.updated_at",
}


def list_expectation_gaps(connection, *, market: str = "all", q: str = "", sort_by: str = "morningstar_gap_pct",
                          sort_order: str = "desc", page: int = 1, page_size: int = 50,
                          include_unrated: bool = False) -> dict[str, Any]:
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
    where_sql = " AND ".join(where)
    total = connection.execute(
        f"SELECT COUNT(*) FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id WHERE {where_sql}", params
    ).fetchone()[0]
    sort_column = SORT_COLUMNS[sort_by]
    order_sql = f"CASE WHEN {sort_column} IS NULL THEN 1 ELSE 0 END, {sort_column} {sort_order.upper()}, s.symbol ASC"
    rows = connection.execute(
        f"""SELECT s.market,s.futu_code,s.symbol,s.name,e.last_price,e.price_time,e.price_source,
                   e.morningstar_fair_value,e.morningstar_gap_pct,e.morningstar_star_rating,e.morningstar_data_date,e.morningstar_source,
                   e.analyst_average_target,e.analyst_gap_pct,e.analyst_count,e.analyst_data_date,e.analyst_source,e.updated_at
            FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id
            WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["display_source"] = "手工" if item["market"] == "A" else "富途自动"
        item["data_date"] = max(filter(None, [item["morningstar_data_date"], item["analyst_data_date"]]), default=None)
        items.append(item)
    latest = connection.execute(
        "SELECT status,finished_at FROM refresh_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "last_refresh": dict(latest) if latest else None}
