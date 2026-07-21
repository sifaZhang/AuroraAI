CREATE TABLE IF NOT EXISTS a_share_daily_bars (
    stock_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,
    source TEXT NOT NULL,
    adjustment TEXT NOT NULL DEFAULT 'none',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (stock_code, trade_date, adjustment)
);

CREATE INDEX IF NOT EXISTS idx_a_share_daily_bars_trade_date
ON a_share_daily_bars(trade_date);

CREATE INDEX IF NOT EXISTS idx_a_share_daily_bars_stock_adjustment_date
ON a_share_daily_bars(stock_code, adjustment, trade_date DESC);

CREATE TABLE IF NOT EXISTS a_share_history_sync_status (
    stock_code TEXT PRIMARY KEY,
    stock_name TEXT,
    first_trade_date TEXT,
    last_trade_date TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    source TEXT,
    adjustment TEXT NOT NULL DEFAULT 'none',
    row_count INTEGER NOT NULL DEFAULT 0 CHECK(row_count >= 0),
    updated_at TEXT NOT NULL
);
