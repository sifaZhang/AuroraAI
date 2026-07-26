"""Centralised A-share symbol, board, limit-rule, and price-limit resolution."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from .contracts import (BoardType, DataSource, LimitPrices, PriceLimitRule, QualityFlag,
                        RuleStatus, SecurityId, SecurityStatus)

_CANONICAL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.I)
_GM = re.compile(r"^(SHSE|SZSE|BJSE)\.(\d{6})$", re.I)
_SINA = re.compile(r"^(SH|SZ|BJ)(\d{6})$", re.I)
_PLAIN = re.compile(r"^\d{6}$")
_EXCHANGE_BY_GM = {"SHSE": "SH", "SZSE": "SZ", "BJSE": "BJ"}

CHINEXT_20_PERCENT_EFFECTIVE = date(2020, 8, 24)
STAR_MARKET_OPEN = date(2019, 7, 22)
BSE_OPEN = date(2021, 11, 15)
TICK_SIZE = Decimal("0.01")


def _date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid trade date: {value}") from exc


def normalize_symbol(value: object, *, exchange: str | None = None) -> SecurityId:
    """Normalize supported A-share formats without guessing a bare-code exchange."""

    if value is None or isinstance(value, bool):
        raise ValueError("security symbol is required")
    text = str(value).strip()
    match = _CANONICAL.fullmatch(text)
    if match:
        code, resolved = match.group(1), match.group(2).upper()
    elif (match := _GM.fullmatch(text)):
        resolved, code = _EXCHANGE_BY_GM[match.group(1).upper()], match.group(2)
    elif (match := _SINA.fullmatch(text)):
        resolved, code = match.group(1).upper(), match.group(2)
    elif _PLAIN.fullmatch(text):
        if not exchange:
            raise ValueError("bare six-digit symbol requires an explicit exchange")
        code, resolved = text, str(exchange).strip().upper()
    else:
        raise ValueError(f"unsupported security symbol: {value}")
    if resolved not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"unsupported exchange: {resolved}")
    return SecurityId(code, resolved)


def resolve_board_type(symbol: SecurityId | object, trade_date: date | str,
                       security_status: SecurityStatus | None = None) -> BoardType:
    """Resolve from recorded historical status, otherwise only deterministic code classes."""

    security = symbol if isinstance(symbol, SecurityId) else normalize_symbol(symbol)
    _date(trade_date)
    if security_status and security_status.board_type != BoardType.UNKNOWN:
        return security_status.board_type
    if security.exchange == "BJ":
        return BoardType.BSE
    if security.exchange == "SZ" and security.code.startswith("300"):
        return BoardType.CHINEXT
    if security.exchange == "SH" and security.code.startswith("688"):
        return BoardType.STAR
    if security.exchange in {"SH", "SZ"} and security.code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return BoardType.MAIN
    return BoardType.UNKNOWN


def resolve_price_limit_rule(symbol: SecurityId | object, trade_date: date | str,
                             security_status: SecurityStatus | None = None) -> PriceLimitRule:
    day = _date(trade_date)
    security = symbol if isinstance(symbol, SecurityId) else normalize_symbol(symbol)
    flags: set[QualityFlag] = set()
    if security_status is None:
        flags.add(QualityFlag.MISSING_SECURITY_STATUS)
    elif security_status.is_suspended:
        return PriceLimitRule(resolve_board_type(security, day, security_status), day, None,
                              RuleStatus.UNSUPPORTED, quality_flags=frozenset({QualityFlag.SUSPENDED, QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT}))
    elif security_status.no_price_limit is True:
        return PriceLimitRule(resolve_board_type(security, day, security_status), day, None,
                              RuleStatus.NO_LIMIT, quality_flags=frozenset({QualityFlag.NO_PRICE_LIMIT, QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT}))
    board = resolve_board_type(security, day, security_status)
    if board == BoardType.UNKNOWN:
        return PriceLimitRule(board, day, None, RuleStatus.UNKNOWN,
                              quality_flags=frozenset(flags | {QualityFlag.MISSING_TRADING_RULE, QualityFlag.UNSUPPORTED_SECURITY}))
    # A known listing day must not be treated as an ordinary limited day without a status row.
    if security_status and security_status.listed_date == day and security_status.no_price_limit is None:
        return PriceLimitRule(board, day, None, RuleStatus.UNKNOWN,
                              quality_flags=frozenset(flags | {QualityFlag.NEW_LISTING_STATUS_UNVERIFIED, QualityFlag.MISSING_TRADING_RULE}))
    if security_status and security_status.is_st is True:
        return PriceLimitRule(board, day, Decimal("0.05"), RuleStatus.SUPPORTED,
                              quality_flags=frozenset(flags | {QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT}))
    if board == BoardType.MAIN:
        rate = Decimal("0.10")
    elif board == BoardType.CHINEXT:
        rate = Decimal("0.20") if day >= CHINEXT_20_PERCENT_EFFECTIVE else Decimal("0.10")
    elif board == BoardType.STAR:
        if day < STAR_MARKET_OPEN:
            return PriceLimitRule(board, day, None, RuleStatus.UNSUPPORTED,
                                  quality_flags=frozenset(flags | {QualityFlag.MISSING_TRADING_RULE}))
        rate = Decimal("0.20")
    else:  # BSE
        if day < BSE_OPEN:
            return PriceLimitRule(board, day, None, RuleStatus.UNSUPPORTED,
                                  quality_flags=frozenset(flags | {QualityFlag.MISSING_TRADING_RULE}))
        rate = Decimal("0.30")
    return PriceLimitRule(board, day, rate, RuleStatus.SUPPORTED, quality_flags=frozenset(flags))


def _decimal(value: Decimal | float | int | str | None, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a positive finite decimal")
    return result


def calculate_limit_prices(pre_close: Decimal | float | int | str | None, rule: PriceLimitRule,
                           tick_size: Decimal | float | str = TICK_SIZE) -> tuple[Decimal | None, Decimal | None, frozenset[QualityFlag]]:
    previous = _decimal(pre_close, "pre_close")
    tick = _decimal(tick_size, "tick_size")
    flags = set(rule.quality_flags)
    if previous is None:
        flags.add(QualityFlag.MISSING_PRE_CLOSE)
    if rule.status != RuleStatus.SUPPORTED or rule.limit_rate is None:
        flags.add(QualityFlag.MISSING_TRADING_RULE)
    if previous is None or rule.status != RuleStatus.SUPPORTED or rule.limit_rate is None:
        return None, None, frozenset(flags)
    upper = (previous * (Decimal("1") + rule.limit_rate)).quantize(tick, rounding=ROUND_HALF_UP)
    lower = (previous * (Decimal("1") - rule.limit_rate)).quantize(tick, rounding=ROUND_HALF_UP)
    return upper, lower, frozenset(flags)


def resolve_limit_prices(pre_close: Decimal | float | int | str | None, rule: PriceLimitRule,
                         *, source_upper_limit: Decimal | float | int | str | None = None,
                         source_lower_limit: Decimal | float | int | str | None = None,
                         tick_size: Decimal | float | str = TICK_SIZE) -> LimitPrices:
    previous = _decimal(pre_close, "pre_close")
    source_upper, source_lower = _decimal(source_upper_limit, "source_upper_limit"), _decimal(source_lower_limit, "source_lower_limit")
    calculated_upper, calculated_lower, flags = calculate_limit_prices(previous, rule, tick_size)
    flags = set(flags)
    source_complete = source_upper is not None and source_lower is not None
    calculated_complete = calculated_upper is not None and calculated_lower is not None
    if source_complete and calculated_complete and (source_upper != calculated_upper or source_lower != calculated_lower):
        flags.update({QualityFlag.SOURCE_CALCULATION_MISMATCH, QualityFlag.DATA_SOURCE_CONFLICT, QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT})
        return LimitPrices(previous, rule.limit_rate, calculated_upper, calculated_lower, source_upper, source_lower,
                           source_upper, source_lower, "source_authoritative_conflict", "conflict", False, frozenset(flags))
    if source_complete:
        return LimitPrices(previous, rule.limit_rate, calculated_upper, calculated_lower, source_upper, source_lower,
                           source_upper, source_lower, "source_authoritative", "consistent" if calculated_complete else "source_only", True, frozenset(flags))
    if calculated_complete:
        return LimitPrices(previous, rule.limit_rate, calculated_upper, calculated_lower, source_upper, source_lower,
                           calculated_upper, calculated_lower, "calculated_fallback", "calculated_only", True, frozenset(flags))
    return LimitPrices(previous, rule.limit_rate, calculated_upper, calculated_lower, source_upper, source_lower,
                       None, None, "unresolved", "unresolved", False, frozenset(flags | {QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT}))


def flags_for_adjustment(adjustment: str) -> frozenset[QualityFlag]:
    normalized = str(adjustment or "").strip().lower()
    if normalized == "none":
        return frozenset()
    if normalized in {"forward", "qfq", "backward", "hfq"}:
        return frozenset({QualityFlag.ADJUSTED_PRICE, QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT})
    return frozenset({QualityFlag.UNKNOWN_ADJUSTMENT, QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT})


def detect_price_anomalies(*, adjustment: str, pre_close: Decimal | float | int | str | None,
                           previous_close: Decimal | float | int | str | None,
                           tick_size: Decimal | float | str = TICK_SIZE) -> frozenset[QualityFlag]:
    """Flag discontinuities; this does not infer a corporate action or rewrite prices."""

    flags = set(flags_for_adjustment(adjustment))
    current_pre_close = _decimal(pre_close, "pre_close")
    prior_close = _decimal(previous_close, "previous_close")
    tick = _decimal(tick_size, "tick_size")
    if current_pre_close is None:
        flags.add(QualityFlag.MISSING_PRE_CLOSE)
    if current_pre_close is not None and prior_close is not None and abs(current_pre_close - prior_close) > tick:
        flags.update({QualityFlag.PRE_CLOSE_DISCONTINUITY, QualityFlag.SUSPECTED_EX_RIGHTS,
                      QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT})
    return frozenset(flags)
