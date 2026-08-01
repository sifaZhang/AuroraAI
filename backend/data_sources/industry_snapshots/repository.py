from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import date, datetime, timezone
from typing import Sequence

from .models import IndustryDailySnapshot

_CONTENT_FIELDS = tuple(field.name for field in fields(IndustryDailySnapshot))
_DB_FIELDS = _CONTENT_FIELDS + ("updated_at",)


def _date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value)).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _values(snapshot: IndustryDailySnapshot) -> tuple:
    return tuple(
        value.isoformat() if isinstance(value, date) else value
        for value in (getattr(snapshot, name) for name in _CONTENT_FIELDS)
    )


class IndustrySnapshotRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _existing(self, snapshot: IndustryDailySnapshot) -> sqlite3.Row | None:
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='industry_daily_snapshots'"
        ).fetchone() is None:
            return None
        return self.connection.execute(
            """SELECT * FROM industry_daily_snapshots
               WHERE trade_date=? AND classification=? AND classification_version=?
                 AND industry_code=?""",
            (snapshot.trade_date.isoformat(), snapshot.classification,
             snapshot.classification_version, snapshot.industry_code),
        ).fetchone()

    def snapshot_matches(self, snapshot: IndustryDailySnapshot) -> bool:
        row = self._existing(snapshot)
        if row is None:
            return False
        expected = _values(snapshot)
        return all(row[name] == expected[index] for index, name in enumerate(_CONTENT_FIELDS))

    def write_snapshots(self, snapshots: Sequence[IndustryDailySnapshot], *, force: bool = False) -> int:
        changed = [item for item in snapshots if force or not self.snapshot_matches(item)]
        if not changed:
            return 0
        placeholders = ",".join("?" for _ in _DB_FIELDS)
        updates = ",".join(
            f"{name}=excluded.{name}" for name in _CONTENT_FIELDS
            if name not in {"trade_date", "classification", "classification_version", "industry_code"}
        ) + ",updated_at=excluded.updated_at"
        now = _timestamp()
        with self.connection:
            self.connection.executemany(
                f"""INSERT INTO industry_daily_snapshots({','.join(_DB_FIELDS)})
                    VALUES({placeholders})
                    ON CONFLICT(trade_date,classification,classification_version,industry_code)
                    DO UPDATE SET {updates}""",
                [_values(item) + (now,) for item in changed],
            )
        return len(changed)

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> IndustryDailySnapshot:
        values = {name: row[name] for name in _CONTENT_FIELDS}
        values["trade_date"] = date.fromisoformat(values["trade_date"])
        return IndustryDailySnapshot(**values)

    def list_snapshots(self, trade_date: date | str, level: int | None = None) -> list[IndustryDailySnapshot]:
        params: list[object] = [_date(trade_date)]
        where = "trade_date=?"
        if level is not None:
            where += " AND industry_level=?"
            params.append(level)
        rows = self.connection.execute(
            f"SELECT * FROM industry_daily_snapshots WHERE {where} ORDER BY industry_level,industry_code",
            params,
        ).fetchall()
        return [self._snapshot(row) for row in rows]

    def get_snapshot(self, trade_date: date | str, industry_code: str) -> IndustryDailySnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM industry_daily_snapshots WHERE trade_date=? AND industry_code=?",
            (_date(trade_date), str(industry_code).strip()),
        ).fetchone()
        return self._snapshot(row) if row else None

    def list_industry_history(self, industry_code: str, start_date: date | str,
                              end_date: date | str) -> list[IndustryDailySnapshot]:
        rows = self.connection.execute(
            """SELECT * FROM industry_daily_snapshots
               WHERE industry_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date""",
            (str(industry_code).strip(), _date(start_date), _date(end_date)),
        ).fetchall()
        return [self._snapshot(row) for row in rows]
