"""Export the dividend observation pool as a Git-tracked CSV snapshot."""

from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.dividend.yield_service import current_basis_dps_for_years, target_years
from backend.expectation_gap.database import PROJECT_ROOT


WATCHLIST_CSV_PATH = PROJECT_ROOT / "data" / "dividend" / "dividend_watchlist.csv"
WATCHLIST_COLUMNS = (
    "symbol", "name", "grade", "entry_yield", "add_yield", "heavy_yield",
    "avg_dps_3y", "enabled", "updated_at",
)
def _sync_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _csv_number(value: float | int | None) -> str:
    return "" if value is None else str(value)


def _watchlist_rows(connection: sqlite3.Connection, *, calculation_date=None) -> list[dict[str, str]]:
    day = calculation_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    years = target_years(day)
    synced_at = _sync_timestamp()
    universe_rows = connection.execute(
        """SELECT market,symbol,company_name,grade,entry_yield,add_yield,heavy_yield,is_enabled
           FROM dividend_stable_universe
           WHERE market='CN'
           ORDER BY is_enabled DESC,
                    CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
                    symbol ASC"""
    ).fetchall()
    rows: list[dict[str, str]] = []
    for item in universe_rows:
        dps = current_basis_dps_for_years(connection, item["market"], item["symbol"], years)
        values = [dps.get(year) for year in years]
        average = sum(values) / len(values) if len(values) == len(years) and all(value is not None for value in values) else None
        rows.append({
            "symbol": item["symbol"],
            "name": item["company_name"],
            "grade": item["grade"] or "",
            "entry_yield": _csv_number(item["entry_yield"]),
            "add_yield": _csv_number(item["add_yield"]),
            "heavy_yield": _csv_number(item["heavy_yield"]),
            "avg_dps_3y": _csv_number(average),
            "enabled": "true" if item["is_enabled"] else "false",
            "updated_at": synced_at,
        })
    return rows


def export_dividend_watchlist_csv(
    connection: sqlite3.Connection,
    output_path: Path = WATCHLIST_CSV_PATH,
    *,
    calculation_date=None,
) -> dict[str, object]:
    """Rebuild the complete watchlist snapshot and atomically replace the old file."""
    rows = _watchlist_rows(connection, calculation_date=calculation_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=WATCHLIST_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(output_path), "row_count": len(rows)}
