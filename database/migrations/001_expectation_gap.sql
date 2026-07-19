PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    futu_code TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('A', 'HK')),
    exchange TEXT NOT NULL CHECK (exchange IN ('SH', 'SZ', 'HK')),
    security_type TEXT NOT NULL DEFAULT 'STOCK',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    listing_date TEXT,
    is_reit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);
CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks(symbol);
CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(name);

CREATE TABLE IF NOT EXISTS stock_expectations (
    stock_id INTEGER PRIMARY KEY REFERENCES stocks(id) ON DELETE CASCADE,
    last_price NUMERIC,
    price_time TEXT,
    price_status TEXT CHECK (price_status IN ('success', 'no_data', 'error')),
    price_source TEXT,
    price_check_status TEXT,
    morningstar_fair_value NUMERIC,
    morningstar_star_rating INTEGER,
    morningstar_rating_type INTEGER,
    morningstar_data_date TEXT,
    morningstar_checked_at TEXT,
    morningstar_status TEXT CHECK (morningstar_status IN ('success', 'no_data', 'error')),
    morningstar_source TEXT,
    morningstar_imported_at TEXT,
    morningstar_gap_pct NUMERIC,
    morningstar_check_status TEXT,
    morningstar_next_check_at TEXT,
    analyst_average_target NUMERIC,
    analyst_high_target NUMERIC,
    analyst_low_target NUMERIC,
    analyst_count INTEGER,
    analyst_rating INTEGER,
    analyst_data_date TEXT,
    analyst_checked_at TEXT,
    analyst_status TEXT CHECK (analyst_status IN ('success', 'no_data', 'error')),
    analyst_source TEXT,
    analyst_report_count INTEGER,
    analyst_window_days INTEGER,
    analyst_gap_pct NUMERIC,
    analyst_check_status TEXT,
    analyst_next_check_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL CHECK (job_type IN ('full', 'daily', 'sample')),
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    no_data_count INTEGER NOT NULL DEFAULT 0,
    last_code TEXT,
    error_message TEXT
);
