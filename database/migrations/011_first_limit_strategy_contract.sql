-- PR6.1: metadata only.  These tables never replace market bars.
CREATE TABLE IF NOT EXISTS a_share_security_master (
    symbol TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL UNIQUE,
    exchange TEXT NOT NULL CHECK(exchange IN ('SH','SZ','BJ')),
    board_type TEXT NOT NULL CHECK(board_type IN ('MAIN','CHINEXT','STAR','BSE','UNKNOWN')),
    security_name TEXT,
    listed_date TEXT,
    delisted_date TEXT,
    source TEXT NOT NULL CHECK(source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_a_share_security_master_board
ON a_share_security_master(board_type, exchange);

CREATE TABLE IF NOT EXISTS a_share_security_status_history (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    board_type TEXT NOT NULL CHECK(board_type IN ('MAIN','CHINEXT','STAR','BSE','UNKNOWN')),
    is_st INTEGER CHECK(is_st IN (0,1)),
    is_suspended INTEGER CHECK(is_suspended IN (0,1)),
    no_price_limit INTEGER CHECK(no_price_limit IN (0,1)),
    listed_date TEXT,
    delisted_date TEXT,
    source TEXT NOT NULL CHECK(source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(symbol, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_a_share_security_status_asof
ON a_share_security_status_history(symbol, effective_date DESC);

CREATE TABLE IF NOT EXISTS a_share_trading_calendar (
    market TEXT NOT NULL CHECK(market IN ('CN','SH','SZ','BJ')),
    trade_date TEXT NOT NULL,
    is_open INTEGER NOT NULL CHECK(is_open IN (0,1)),
    source TEXT NOT NULL CHECK(source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(market, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_a_share_trading_calendar_open
ON a_share_trading_calendar(market, is_open, trade_date);
