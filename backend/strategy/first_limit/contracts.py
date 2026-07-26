"""Typed, source-traceable contracts used before first-limit detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import FrozenSet


class DataSource(str, Enum):
    SINA = "SINA"
    GM = "GM"
    CALCULATED = "CALCULATED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class BoardType(str, Enum):
    MAIN = "MAIN"
    CHINEXT = "CHINEXT"
    STAR = "STAR"
    BSE = "BSE"
    UNKNOWN = "UNKNOWN"


class Adjustment(str, Enum):
    NONE = "none"
    FORWARD = "forward"
    BACKWARD = "backward"
    UNKNOWN = "unknown"


class RuleStatus(str, Enum):
    SUPPORTED = "supported"
    NO_LIMIT = "no_limit"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class QualityFlag(str, Enum):
    MISSING_PRE_CLOSE = "missing_pre_close"
    MISSING_SECURITY_STATUS = "missing_security_status"
    MISSING_TRADING_RULE = "missing_trading_rule"
    SUSPENDED = "suspended"
    NO_PRICE_LIMIT = "no_price_limit"
    ADJUSTED_PRICE = "adjusted_price"
    UNKNOWN_ADJUSTMENT = "unknown_adjustment"
    SUSPECTED_EX_RIGHTS = "suspected_ex_rights"
    PRE_CLOSE_DISCONTINUITY = "pre_close_discontinuity"
    SOURCE_CALCULATION_MISMATCH = "source_calculation_mismatch"
    DATA_SOURCE_CONFLICT = "data_source_conflict"
    UNSUPPORTED_SECURITY = "unsupported_security"
    NOT_ELIGIBLE_FOR_FIRST_LIMIT = "not_eligible_for_first_limit"
    NEW_LISTING_STATUS_UNVERIFIED = "new_listing_status_unverified"


@dataclass(frozen=True)
class SecurityId:
    """AuroraAI canonical security identifier: six digits plus ``.SH/.SZ/.BJ``."""

    code: str
    exchange: str

    @property
    def canonical(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def gm_symbol(self) -> str:
        return {"SH": "SHSE", "SZ": "SZSE", "BJ": "BJSE"}[self.exchange] + f".{self.code}"

    @property
    def sina_symbol(self) -> str:
        return self.exchange.lower() + self.code


@dataclass(frozen=True)
class SecurityStatus:
    symbol: SecurityId
    effective_date: date
    board_type: BoardType = BoardType.UNKNOWN
    is_st: bool | None = None
    is_suspended: bool | None = None
    no_price_limit: bool | None = None
    listed_date: date | None = None
    delisted_date: date | None = None
    source: DataSource = DataSource.UNKNOWN
    quality_flags: FrozenSet[QualityFlag] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PriceLimitRule:
    board_type: BoardType
    trade_date: date
    limit_rate: Decimal | None
    status: RuleStatus
    source: DataSource = DataSource.CALCULATED
    quality_flags: FrozenSet[QualityFlag] = field(default_factory=frozenset)


@dataclass(frozen=True)
class LimitPrices:
    pre_close: Decimal | None
    limit_rate: Decimal | None
    calculated_upper_limit: Decimal | None
    calculated_lower_limit: Decimal | None
    source_upper_limit: Decimal | None
    source_lower_limit: Decimal | None
    upper_limit: Decimal | None
    lower_limit: Decimal | None
    selection_basis: str
    consistency_status: str
    reliable: bool
    quality_flags: FrozenSet[QualityFlag] = field(default_factory=frozenset)
