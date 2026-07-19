from __future__ import annotations

from datetime import date
from typing import Any

from backend.expectation_gap.futu_client import CollectionResult, utc_now
from backend.expectation_gap.service import calculate_gap_pct, positive_number


def ensure_expectation_row(connection, stock_id: int, now: str | None = None) -> None:
    timestamp = now or utc_now()
    connection.execute(
        "INSERT INTO stock_expectations(stock_id,updated_at) VALUES(?,?) ON CONFLICT(stock_id) DO NOTHING",
        (stock_id, timestamp),
    )


def _valid_iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def recalculate_gaps(connection, stock_id: int, now: str | None = None) -> None:
    row = connection.execute(
        "SELECT last_price,morningstar_fair_value,analyst_average_target FROM stock_expectations WHERE stock_id=?",
        (stock_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute(
        "UPDATE stock_expectations SET morningstar_gap_pct=?,analyst_gap_pct=?,updated_at=? WHERE stock_id=?",
        (calculate_gap_pct(row[1], row[0]), calculate_gap_pct(row[2], row[0]), now or utc_now(), stock_id),
    )


def patch_price(connection, stock_id: int, result: CollectionResult, source: str, checked_at: str | None = None) -> None:
    now = checked_at or utc_now()
    ensure_expectation_row(connection, stock_id, now)
    data = result.data or {}
    price = positive_number(data.get("last_price"))
    if result.status == "success" and price is not None:
        connection.execute(
            "UPDATE stock_expectations SET last_price=?,price_time=?,price_status='success',price_source=?,last_error=NULL,last_success_at=?,updated_at=? WHERE stock_id=?",
            (price, data.get("price_time"), source, now, now, stock_id),
        )
    else:
        connection.execute(
            "UPDATE stock_expectations SET price_status=?,last_error=?,updated_at=? WHERE stock_id=?",
            (result.status, result.error, now, stock_id),
        )
    recalculate_gaps(connection, stock_id, now)


def patch_morningstar(
    connection,
    stock_id: int,
    result: CollectionResult,
    source: str,
    checked_at: str | None = None,
    *,
    manual: bool = False,
) -> bool:
    now = checked_at or utc_now()
    ensure_expectation_row(connection, stock_id, now)
    data = result.data or {}
    fair_value = positive_number(data.get("fair_value"))
    data_date = _valid_iso_date(data.get("data_date"))
    existing = connection.execute(
        "SELECT morningstar_data_date FROM stock_expectations WHERE stock_id=?", (stock_id,)
    ).fetchone()
    current_date = _valid_iso_date(existing[0]) if existing else None
    can_write = (
        result.status == "success"
        and fair_value is not None
        and data_date is not None
        and (current_date is None or data_date >= current_date)
    )
    if can_write:
        connection.execute(
            """UPDATE stock_expectations SET
                   morningstar_fair_value=?,morningstar_star_rating=?,morningstar_rating_type=?,morningstar_data_date=?,
                   morningstar_checked_at=?,morningstar_status='success',morningstar_source=?,morningstar_imported_at=?,
                   last_error=NULL,last_success_at=?,updated_at=? WHERE stock_id=?""",
            (fair_value, data.get("star_rating"), data.get("rating_type"), data_date, now, source,
             now if manual else None, now, now, stock_id),
        )
    else:
        error = result.error
        if result.status == "success" and fair_value is None:
            error = error or "invalid fair_value"
        elif result.status == "success" and data_date is None:
            error = error or "invalid data_date"
        elif result.status == "success" and current_date and data_date and data_date < current_date:
            error = error or "stale morningstar data"
        connection.execute(
            "UPDATE stock_expectations SET morningstar_checked_at=?,morningstar_status=?,last_error=?,updated_at=? WHERE stock_id=?",
            (now, result.status, error, now, stock_id),
        )
    recalculate_gaps(connection, stock_id, now)
    return can_write


def patch_analyst(
    connection,
    stock_id: int,
    result: CollectionResult,
    source: str,
    checked_at: str | None = None,
    window_days: int = 90,
) -> bool:
    now = checked_at or utc_now()
    ensure_expectation_row(connection, stock_id, now)
    data = result.data or {}
    average = positive_number(data.get("average"))
    data_date = _valid_iso_date(data.get("data_date"))
    can_write = result.status == "success" and average is not None and data_date is not None
    if can_write:
        connection.execute(
            """UPDATE stock_expectations SET analyst_average_target=?,analyst_high_target=?,analyst_low_target=?,
                   analyst_count=?,analyst_report_count=?,analyst_rating=?,analyst_data_date=?,analyst_checked_at=?,
                   analyst_status='success',analyst_source=?,analyst_window_days=?,last_error=NULL,last_success_at=?,updated_at=?
               WHERE stock_id=?""",
            (average, positive_number(data.get("highest")), positive_number(data.get("lowest")), data.get("total"),
             data.get("report_count", data.get("total")), data.get("rating"), data_date, now, source, window_days, now, now, stock_id),
        )
    else:
        connection.execute(
            "UPDATE stock_expectations SET analyst_checked_at=?,analyst_status=?,last_error=?,updated_at=? WHERE stock_id=?",
            (now, result.status, result.error or ("invalid analyst target" if result.status == "success" else None), now, stock_id),
        )
    recalculate_gaps(connection, stock_id, now)
    return can_write


def patch_manual_a_share_valuation(
    connection,
    stock_id: int,
    *,
    data_date: str,
    morningstar_fair_value: float | None,
    morningstar_star_rating: int | None,
    analyst_average_target: float | None,
    analyst_count: int | None,
    imported_at: str | None = None,
) -> dict[str, bool]:
    """Patch only non-empty manual fields when their date is not stale."""
    now = imported_at or utc_now()
    ensure_expectation_row(connection, stock_id, now)
    row = connection.execute(
        "SELECT morningstar_data_date,analyst_data_date FROM stock_expectations WHERE stock_id=?", (stock_id,)
    ).fetchone()
    morningstar_current = _valid_iso_date(row[0]) if row else None
    analyst_current = _valid_iso_date(row[1]) if row else None
    morningstar_has_value = morningstar_fair_value is not None or morningstar_star_rating is not None
    analyst_has_value = analyst_average_target is not None or analyst_count is not None
    morningstar_updated = morningstar_has_value and (morningstar_current is None or data_date >= morningstar_current)
    analyst_updated = analyst_has_value and (analyst_current is None or data_date >= analyst_current)

    if morningstar_updated:
        assignments = ["morningstar_data_date=?", "morningstar_checked_at=?", "morningstar_status='success'",
                       "morningstar_source='manual_a_share_csv'", "morningstar_imported_at=?", "updated_at=?"]
        values: list[Any] = [data_date, now, now, now]
        if morningstar_fair_value is not None:
            assignments.append("morningstar_fair_value=?")
            values.append(morningstar_fair_value)
        if morningstar_star_rating is not None:
            assignments.append("morningstar_star_rating=?")
            values.append(morningstar_star_rating)
        values.append(stock_id)
        connection.execute(f"UPDATE stock_expectations SET {','.join(assignments)} WHERE stock_id=?", values)

    if analyst_updated:
        assignments = ["analyst_data_date=?", "analyst_checked_at=?", "analyst_status='success'",
                       "analyst_source='manual_a_share_csv'", "updated_at=?"]
        values = [data_date, now, now]
        if analyst_average_target is not None:
            assignments.append("analyst_average_target=?")
            values.append(analyst_average_target)
        if analyst_count is not None:
            assignments.extend(["analyst_count=?", "analyst_report_count=?"])
            values.extend([analyst_count, analyst_count])
        values.append(stock_id)
        connection.execute(f"UPDATE stock_expectations SET {','.join(assignments)} WHERE stock_id=?", values)

    recalculate_gaps(connection, stock_id, now)
    return {"morningstar": morningstar_updated, "analyst": analyst_updated}
