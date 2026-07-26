"""Strict local trading-calendar queries; no weekday fallback is ever used."""

from __future__ import annotations

import sqlite3
from datetime import date


def _date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value).strip()).isoformat()


class TradingCalendarService:
    def __init__(self, connection: sqlite3.Connection, market: str = "CN"):
        self.connection, self.market = connection, market.upper()
        if self.market not in {"CN", "SH", "SZ", "BJ"}:
            raise ValueError(f"unsupported market: {market}")

    def is_trading_day(self, value: date | str) -> bool:
        row = self.connection.execute("SELECT is_open FROM a_share_trading_calendar WHERE market=? AND trade_date=?", (self.market, _date(value))).fetchone()
        if row is None:
            raise LookupError(f"calendar unavailable for {self.market} {_date(value)}")
        return bool(row[0])

    def previous_trading_day(self, value: date | str) -> str:
        row = self.connection.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market=? AND is_open=1 AND trade_date<? ORDER BY trade_date DESC LIMIT 1", (self.market, _date(value))).fetchone()
        if row is None:
            raise LookupError("previous trading day unavailable")
        return row[0]

    def next_trading_day(self, value: date | str) -> str:
        row = self.connection.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market=? AND is_open=1 AND trade_date>? ORDER BY trade_date ASC LIMIT 1", (self.market, _date(value))).fetchone()
        if row is None:
            raise LookupError("next trading day unavailable")
        return row[0]

    def trading_days_between(self, start: date | str, end: date | str) -> list[str]:
        start_text, end_text = _date(start), _date(end)
        if start_text > end_text:
            raise ValueError("start date must not exceed end date")
        return [row[0] for row in self.connection.execute("SELECT trade_date FROM a_share_trading_calendar WHERE market=? AND is_open=1 AND trade_date BETWEEN ? AND ? ORDER BY trade_date", (self.market, start_text, end_text))]

    def trading_day_offset(self, start: date | str, end: date | str) -> int:
        days = self.trading_days_between(min(_date(start), _date(end)), max(_date(start), _date(end)))
        if not days or _date(start) not in days or _date(end) not in days:
            raise LookupError("both dates must be present open trading days")
        return (len(days) - 1) * (1 if _date(end) >= _date(start) else -1)
