import sqlite3
from datetime import date

from backend.industry.daily_coverage import expected_industry_symbols


def db():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE industry_memberships_current(symbol TEXT);
      CREATE TABLE a_share_security_master(symbol TEXT,listed_date TEXT,delisted_date TEXT,is_active INTEGER);
      CREATE TABLE a_share_security_status_history(symbol TEXT,effective_date TEXT,is_suspended INTEGER,listed_date TEXT,delisted_date TEXT);
    """)
    return c


def add(c, symbol, *, listed="2020-01-01", delisted="2038-01-01", active=1, suspended=0):
    c.execute("INSERT INTO industry_memberships_current VALUES(?)", (symbol,))
    c.execute("INSERT INTO a_share_security_master VALUES(?,?,?,?)", (symbol, listed, delisted, active))
    c.execute("INSERT INTO a_share_security_status_history VALUES(?,?,?,?,?)", (symbol, "2026-08-03", suspended, listed, delisted))


def test_expected_universe_excludes_suspended_unlisted_delisted_and_nonordinary():
    c = db(); add(c, "600000.SH"); add(c, "600001.SH", suspended=1); add(c, "600002.SH", listed="2026-08-04")
    add(c, "600003.SH", delisted="2026-08-02"); add(c, "200001.SZ"); add(c, "689009.SH")
    assert expected_industry_symbols(c, date(2026, 8, 3), {"600000.SH", "600001.SH", "600002.SH", "600003.SH", "200001.SZ", "689009.SH"}) == {"600000.SH"}


def test_traded_ordinary_symbol_without_local_bar_remains_expected():
    c = db(); add(c, "000001.SZ")
    assert expected_industry_symbols(c, date(2026, 8, 3), {"000001.SZ"}) == {"000001.SZ"}


def test_absent_from_authoritative_daily_response_is_not_a_trading_expectation():
    c = db(); add(c, "000001.SZ")
    assert expected_industry_symbols(c, date(2026, 8, 3), set()) == set()
