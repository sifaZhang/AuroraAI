from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.futu_client import utc_now
from backend.expectation_gap.repository import patch_manual_a_share_valuation
from backend.expectation_gap.service import positive_number

FIELDS = {"futu_code", "name", "morningstar_fair_value", "morningstar_star_rating",
          "analyst_average_target", "analyst_count", "data_date", "source", "note"}


def _optional_positive(value: str | None) -> tuple[float | None, bool]:
    text = (value or "").strip()
    if not text:
        return None, True
    parsed = positive_number(text)
    return parsed, parsed is not None


def import_file(connection, path: Path) -> tuple[int, list[str]]:
    if not path.exists():
        return 0, []
    imported = 0
    errors: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = FIELDS - set(reader.fieldnames or [])
        if missing:
            return 0, [f"缺少字段: {', '.join(sorted(missing))}"]
        for line_number, row in enumerate(reader, 2):
            code = (row.get("futu_code") or "").strip().upper()
            fair_value, fair_ok = _optional_positive(row.get("morningstar_fair_value"))
            analyst_target, analyst_ok = _optional_positive(row.get("analyst_average_target"))
            star_text = (row.get("morningstar_star_rating") or "").strip()
            count_text = (row.get("analyst_count") or "").strip()
            try:
                star = int(star_text) if star_text else None
            except ValueError:
                star = -1
            try:
                count = int(count_text) if count_text else None
            except ValueError:
                count = -1
            try:
                data_date = date.fromisoformat((row.get("data_date") or "").strip()).isoformat()
            except ValueError:
                data_date = None
            row_errors = []
            if code in seen:
                row_errors.append("futu_code重复")
            if not re_full_a_code(code):
                row_errors.append("futu_code必须是SH.xxxxxx或SZ.xxxxxx")
            if not fair_ok:
                row_errors.append("morningstar_fair_value必须为正数或留空")
            if star is not None and star not in range(1, 6):
                row_errors.append("morningstar_star_rating必须为1到5或留空")
            if not analyst_ok:
                row_errors.append("analyst_average_target必须为正数或留空")
            if count is not None and count < 0:
                row_errors.append("analyst_count必须为非负整数或留空")
            if data_date is None:
                row_errors.append("data_date必须是YYYY-MM-DD")
            if row_errors:
                errors.append(f"第{line_number}行 {code or '<空代码>'}: {'; '.join(row_errors)}")
                continue
            seen.add(code)
            exchange, symbol = code.split(".", 1)
            now = utc_now()
            with connection:
                connection.execute(
                    """INSERT INTO stocks(futu_code,symbol,name,market,exchange,security_type,is_active,created_at,updated_at)
                       VALUES(?,?,?,'A',?,'STOCK',1,?,?)
                       ON CONFLICT(futu_code) DO UPDATE SET name=excluded.name,is_active=1,updated_at=excluded.updated_at""",
                    (code, symbol, (row.get("name") or "").strip(), exchange, now, now),
                )
                stock_id = connection.execute("SELECT id FROM stocks WHERE futu_code=?", (code,)).fetchone()[0]
                patch_manual_a_share_valuation(connection, stock_id, data_date=data_date,
                    morningstar_fair_value=fair_value, morningstar_star_rating=star,
                    analyst_average_target=analyst_target, analyst_count=count, imported_at=now)
            imported += 1
    return imported, errors


def re_full_a_code(code: str) -> bool:
    import re
    return re.fullmatch(r"(?:SH|SZ)\.\d{6}", code) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Import selected A-share manual valuation fields.")
    parser.add_argument("--file", type=Path, default=Path("data/manual_a_share_valuations.csv"))
    args = parser.parse_args()
    connection = connect()
    migrate(connection)
    imported, errors = import_file(connection, args.file)
    connection.close()
    print(f"imported={imported} skipped={len(errors)}")
    for error in errors:
        print(error)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
