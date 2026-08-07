from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DividendEvent:
    symbol: str
    ann_date: date | None
    ex_date: date | None
    cash_div_tax: float | None
    div_proc: str | None
    end_date: date | None = None


@dataclass(frozen=True)
class CandidateSecurity:
    symbol: str
    company_name: str | None
    list_date: date | None
    is_active: bool
    delisted_date: date | None
    is_st: bool | None
    industry_level_1: str | None
    industry_level_2: str | None
    industry_level_3: str | None
    industry_source: str | None
