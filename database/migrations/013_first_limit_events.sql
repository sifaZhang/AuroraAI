CREATE TABLE IF NOT EXISTS first_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL CHECK(exchange IN ('SH','SZ','BJ')),
    trade_date TEXT NOT NULL,
    detection_version TEXT NOT NULL,
    detection_status TEXT NOT NULL CHECK(detection_status IN ('detected','not_first_limit','excluded','indeterminate','failed')),
    is_limit_up_close INTEGER,
    touched_upper_limit INTEGER,
    is_first_limit INTEGER,
    is_one_word_limit INTEGER,
    is_consecutive_limit INTEGER,
    consecutive_limit_days INTEGER,
    lookback_trading_days INTEGER NOT NULL,
    observed_lookback_days INTEGER NOT NULL,
    previous_limit_up_date TEXT,
    open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, pre_close NUMERIC,
    upper_limit_price NUMERIC, upper_limit_source TEXT,
    exclusion_reasons TEXT NOT NULL DEFAULT '[]', quality_flags TEXT NOT NULL DEFAULT '[]',
    source_run_id TEXT, detected_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(symbol, trade_date, detection_version)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_events_date ON first_limit_events(trade_date, detection_version, detection_status);
CREATE INDEX IF NOT EXISTS idx_first_limit_events_symbol ON first_limit_events(symbol, trade_date);
