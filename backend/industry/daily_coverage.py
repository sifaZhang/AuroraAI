"""Date-aware ordinary A-share coverage helpers for the Industry Radar."""

from __future__ import annotations

import re
from datetime import date


ORDINARY_A_SHARE_SYMBOL = re.compile(r"^(?:60\d{4}|688\d{3})\.SH$|^(?:00\d{4}|30\d{4})\.SZ$")


def expected_industry_symbols(connection, trade_date: date, traded_symbols: set[str]) -> set[str]:
    """Return current SW members that were ordinary active A shares trading that day.

    ``traded_symbols`` is the authoritative full-market daily response.  Its use
    excludes suspended securities without weakening the local-bar completeness
    check for securities which actually traded.
    """
    day = trade_date.isoformat()
    rows = connection.execute(
        """WITH latest_status AS (
               SELECT * FROM (
                   SELECT s.*, ROW_NUMBER() OVER(
                       PARTITION BY symbol ORDER BY effective_date DESC
                   ) AS rn
                   FROM a_share_security_status_history s WHERE effective_date<=?
               ) WHERE rn=1
           )
           SELECT DISTINCT m.symbol,m.listed_date,m.delisted_date,m.is_active,
                  s.listed_date AS status_listed_date,s.delisted_date AS status_delisted_date,
                  s.is_suspended
           FROM industry_memberships_current i
           JOIN a_share_security_master m ON m.symbol=i.symbol
           LEFT JOIN latest_status s ON s.symbol=m.symbol""",
        (day,),
    ).fetchall()
    result: set[str] = set()
    for row in rows:
        symbol = str(row["symbol"])
        listed = row["status_listed_date"] or row["listed_date"]
        delisted = row["status_delisted_date"] or row["delisted_date"]
        if (ORDINARY_A_SHARE_SYMBOL.fullmatch(symbol) and row["is_active"]
                and listed and listed <= day and (not delisted or delisted >= day)
                and row["is_suspended"] != 1 and symbol in traded_symbols):
            result.add(symbol)
    return result
