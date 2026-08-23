from __future__ import annotations

from typing import Any

from backend.dividend.position_levels import position_status
from backend.expectation_gap.database import connect_readonly


class ProfileRepository:
    """Read-only access to existing dividend and SW industry facts."""
    def __init__(self, connection=None) -> None:
        self.connection = connection or connect_readonly()
        self._owns_connection = connection is None

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def profile_facts(self, symbol: str) -> dict[str, Any] | None:
        row = self.connection.execute("""SELECT u.market,u.symbol,u.company_name,u.is_enabled,u.grade,
            u.entry_yield,u.add_yield,u.heavy_yield,i.classification,i.classification_version,
            i.level1_code,i.level1_name,i.level2_code,i.level2_name,i.level3_code,i.level3_name,
            s.latest_price,s.three_year_average_dps,s.three_year_average_yield,s.calculation_date,s.price_date
          FROM dividend_stable_universe u
          LEFT JOIN industry_memberships_current i ON i.symbol=u.symbol AND i.classification='SW' AND i.classification_version='2021'
          LEFT JOIN dividend_yield_snapshots s ON s.market=u.market AND s.symbol=u.symbol
            AND s.calculation_date=(SELECT MAX(x.calculation_date) FROM dividend_yield_snapshots x WHERE x.market=u.market AND x.symbol=u.symbol)
          WHERE u.symbol=?""", (symbol.upper(),)).fetchone()
        if row is None:
            return None
        item = dict(row)
        raw_yield = item.get("three_year_average_yield")
        yield_pct = float(raw_yield) * 100 if raw_yield is not None else None
        item["yield_signal"] = position_status(yield_pct, item["entry_yield"], item["add_yield"], item["heavy_yield"])
        return item

    def research_candidates(self) -> list[dict[str, Any]]:
        symbols = [row[0] for row in self.connection.execute("SELECT symbol FROM dividend_stable_universe WHERE is_enabled=1 ORDER BY symbol")]
        return [facts for symbol in symbols if (facts := self.profile_facts(symbol)) and facts["yield_signal"] in {"entry", "add", "heavy"}]
