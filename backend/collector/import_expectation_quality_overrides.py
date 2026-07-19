from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from pathlib import Path

from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate

VALID_SOURCES = {"morningstar", "analyst"}
VALID_ACTIONS = {"exclude", "allow", "warning"}
REQUIRED = {"code", "source", "action", "reason", "note", "reviewed_at"}


def import_overrides(connection, path: Path) -> dict:
    if not path.exists():
        return {"imported": 0, "skipped": 0, "errors": [f"文件不存在: {path}"]}
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    imported = skipped = 0
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            return {"imported": 0, "skipped": 0, "errors": [f"缺少字段: {', '.join(sorted(missing))}"]}
        seen: set[tuple[str, str]] = set()
        for line, row in enumerate(reader, 2):
            code, source, action = (row.get("code") or "").strip().upper(), (row.get("source") or "").strip().lower(), (row.get("action") or "").strip().lower()
            reason, reviewed_at = (row.get("reason") or "").strip(), (row.get("reviewed_at") or "").strip()
            row_errors = []
            if not (code.startswith("HK.") or code.startswith("SH.") or code.startswith("SZ.")):
                row_errors.append("code格式无效")
            if source not in VALID_SOURCES:
                row_errors.append("source必须为morningstar或analyst")
            if action not in VALID_ACTIONS:
                row_errors.append("action必须为exclude、allow或warning")
            if not reason:
                row_errors.append("reason不能为空")
            try:
                date.fromisoformat(reviewed_at)
            except ValueError:
                row_errors.append("reviewed_at必须为YYYY-MM-DD")
            if (code, source) in seen:
                row_errors.append("同一code和source重复")
            stock = connection.execute("SELECT id FROM stocks WHERE futu_code=?", (code,)).fetchone()
            if stock is None:
                row_errors.append("code不在股票表中")
            if row_errors:
                skipped += 1
                errors.append(f"第{line}行 {code or '<空>'}: {'；'.join(row_errors)}")
                continue
            seen.add((code, source))
            connection.execute("""INSERT INTO expectation_quality_overrides(stock_id,source,action,reason,note,reviewed_at,imported_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(stock_id,source) DO UPDATE SET action=excluded.action,reason=excluded.reason,
                note=excluded.note,reviewed_at=excluded.reviewed_at,imported_at=excluded.imported_at""",
                (stock[0], source, action, reason, (row.get("note") or "").strip() or None, reviewed_at, imported_at))
            imported += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import auditable per-source expectation quality overrides.")
    parser.add_argument("--file", type=Path, default=PROJECT_ROOT / "data" / "manual_expectation_quality_overrides.csv")
    args = parser.parse_args()
    connection = connect(); migrate(connection)
    with connection:
        result = import_overrides(connection, args.file)
    connection.close()
    print(result)
    return 1 if result["errors"] and not result["imported"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
