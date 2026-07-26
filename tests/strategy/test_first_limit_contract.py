from datetime import date
from decimal import Decimal

import pytest

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit.calendar import TradingCalendarService
from backend.strategy.first_limit.contracts import (BoardType, DataSource, QualityFlag,
                                                    RuleStatus, SecurityStatus)
from backend.strategy.first_limit.repository import (CalendarDay, SecurityMaster,
                                                     get_security_status_as_of,
                                                     upsert_calendar_days, upsert_security_master,
                                                     upsert_security_status)
from backend.strategy.first_limit.rules import (calculate_limit_prices, flags_for_adjustment,
                                                detect_price_anomalies, normalize_symbol, resolve_board_type,
                                                resolve_limit_prices, resolve_price_limit_rule)


@pytest.mark.parametrize(("raw", "canonical", "gm", "sina"), [
    ("600000.SH", "600000.SH", "SHSE.600000", "sh600000"),
    ("SZSE.000001", "000001.SZ", "SZSE.000001", "sz000001"),
    ("sh600000", "600000.SH", "SHSE.600000", "sh600000"),
    ("sz000001", "000001.SZ", "SZSE.000001", "sz000001"),
    ("688001.SH", "688001.SH", "SHSE.688001", "sh688001"),
])
def test_symbol_formats_normalize_without_losing_exchange(raw, canonical, gm, sina):
    item = normalize_symbol(raw)
    assert (item.canonical, item.gm_symbol, item.sina_symbol) == (canonical, gm, sina)


def test_plain_symbol_requires_exchange_and_invalid_symbols_fail():
    assert normalize_symbol("000001", exchange="SZ").canonical == "000001.SZ"
    for value in (None, "", "HK.00700", "600000", "SHSE.60000", "xx600000"):
        with pytest.raises(ValueError):
            normalize_symbol(value)


def status(symbol, day="2026-07-20", **changes):
    values = dict(symbol=normalize_symbol(symbol), effective_date=date.fromisoformat(day),
                  board_type=BoardType.MAIN, source=DataSource.GM)
    values.update(changes)
    return SecurityStatus(**values)


def test_board_rules_cover_main_chinext_star_st_bse_and_effective_dates():
    assert resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH")).limit_rate == Decimal("0.10")
    assert resolve_price_limit_rule("300750.SZ", "2020-08-21", status("300750.SZ", board_type=BoardType.CHINEXT)).limit_rate == Decimal("0.10")
    assert resolve_price_limit_rule("300750.SZ", "2020-08-24", status("300750.SZ", board_type=BoardType.CHINEXT)).limit_rate == Decimal("0.20")
    assert resolve_price_limit_rule("688001.SH", "2019-07-22", status("688001.SH", board_type=BoardType.STAR)).limit_rate == Decimal("0.20")
    assert resolve_price_limit_rule("430047.BJ", "2026-07-20", status("430047.BJ", board_type=BoardType.BSE)).limit_rate == Decimal("0.30")
    st_rule = resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH", is_st=True))
    assert st_rule.limit_rate == Decimal("0.05")
    assert QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT in st_rule.quality_flags


def test_new_listing_suspension_and_unknown_rules_are_explicit():
    suspended = resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH", is_suspended=True))
    assert suspended.status == RuleStatus.UNSUPPORTED and QualityFlag.SUSPENDED in suspended.quality_flags
    no_limit = resolve_price_limit_rule("300750.SZ", "2026-07-20", status("300750.SZ", board_type=BoardType.CHINEXT, no_price_limit=True))
    assert no_limit.status == RuleStatus.NO_LIMIT
    listing_day = resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH", listed_date=date(2026, 7, 20)))
    assert listing_day.status == RuleStatus.UNKNOWN and QualityFlag.NEW_LISTING_STATUS_UNVERIFIED in listing_day.quality_flags
    unknown = resolve_price_limit_rule("689999.SH", "2026-07-20")
    assert unknown.status == RuleStatus.UNKNOWN and QualityFlag.UNSUPPORTED_SECURITY in unknown.quality_flags


def test_limit_calculation_uses_decimal_half_up_and_resolves_source_priority():
    rule = resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH"))
    upper, lower, flags = calculate_limit_prices("10.05", rule)
    assert (upper, lower, flags) == (Decimal("11.06"), Decimal("9.05"), frozenset())
    exact = resolve_limit_prices("10.05", rule, source_upper_limit="11.06", source_lower_limit="9.05")
    assert exact.reliable and exact.selection_basis == "source_authoritative" and exact.upper_limit == Decimal("11.06")
    mismatch = resolve_limit_prices("10.05", rule, source_upper_limit="11.05", source_lower_limit="9.05")
    assert not mismatch.reliable and mismatch.upper_limit == Decimal("11.05")
    assert QualityFlag.SOURCE_CALCULATION_MISMATCH in mismatch.quality_flags
    fallback = resolve_limit_prices("10.05", rule)
    assert fallback.reliable and fallback.selection_basis == "calculated_fallback"


def test_missing_preclose_and_adjusted_prices_are_not_silent():
    rule = resolve_price_limit_rule("600000.SH", "2026-07-20", status("600000.SH"))
    result = resolve_limit_prices(None, rule)
    assert not result.reliable and QualityFlag.MISSING_PRE_CLOSE in result.quality_flags
    assert QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT in flags_for_adjustment("qfq")
    assert not flags_for_adjustment("none")
    discontinuity = detect_price_anomalies(adjustment="none", pre_close="8.00", previous_close="10.00")
    assert {QualityFlag.PRE_CLOSE_DISCONTINUITY, QualityFlag.SUSPECTED_EX_RIGHTS,
            QualityFlag.NOT_ELIGIBLE_FOR_FIRST_LIMIT} <= discontinuity


def database(tmp_path):
    connection = connect(tmp_path / "contract.db")
    migrate(connection)
    return connection


def test_security_history_is_as_of_not_current_and_calendar_is_strict(tmp_path):
    connection = database(tmp_path)
    security = normalize_symbol("600000.SH")
    upsert_security_master(connection, SecurityMaster(security, BoardType.MAIN, DataSource.GM, security_name="浦发银行"))
    upsert_security_status(connection, status("600000.SH", "2026-01-02", is_st=False))
    upsert_security_status(connection, status("600000.SH", "2026-07-20", is_st=True))
    assert get_security_status_as_of(connection, security, "2026-06-01").is_st is False
    assert get_security_status_as_of(connection, security, "2026-07-20").is_st is True
    upsert_calendar_days(connection, [
        CalendarDay("CN", date(2026, 2, 13), True, DataSource.GM),
        CalendarDay("CN", date(2026, 2, 16), False, DataSource.GM),
        CalendarDay("CN", date(2026, 2, 17), False, DataSource.GM),
        CalendarDay("CN", date(2026, 2, 23), True, DataSource.GM),
    ])
    calendar = TradingCalendarService(connection)
    assert calendar.is_trading_day("2026-02-13") is True
    assert calendar.is_trading_day("2026-02-16") is False
    assert calendar.next_trading_day("2026-02-13") == "2026-02-23"
    assert calendar.previous_trading_day("2026-02-23") == "2026-02-13"
    assert calendar.trading_days_between("2026-02-13", "2026-02-23") == ["2026-02-13", "2026-02-23"]
    assert calendar.trading_day_offset("2026-02-13", "2026-02-23") == 1
    with pytest.raises(LookupError):
        calendar.is_trading_day("2026-10-01")
    connection.close()


def test_contract_migration_is_idempotent_and_preserves_existing_data(tmp_path):
    connection = database(tmp_path)
    connection.execute("INSERT INTO sector_scores(source,sector_code,sector_name,sector_level,trade_date,trend_score,trend_level,close,ma5,ma10,ma20,volume_ratio,is_20d_high,updated_at) VALUES('sw_l1','801010','农业',1,'2026-07-20',10,'weak',1,1,1,1,1,0,'now')")
    connection.commit()
    migrate(connection)
    migrate(connection)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"a_share_security_master", "a_share_security_status_history", "a_share_trading_calendar"} <= tables
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 1
    with pytest.raises(Exception):
        connection.execute("INSERT INTO a_share_trading_calendar(market,trade_date,is_open,source,quality_flags,updated_at) VALUES('CN','2026-01-01',2,'GM','[]','now')")
    connection.close()
