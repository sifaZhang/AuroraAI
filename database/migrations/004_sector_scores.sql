CREATE TABLE IF NOT EXISTS sector_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(source IN ('sw_l1','sw_l2','eastmoney')),
    sector_level TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trend_score INTEGER NOT NULL CHECK(trend_score BETWEEN 0 AND 70),
    trend_level TEXT NOT NULL,
    close NUMERIC NOT NULL CHECK(close > 0),
    ma5 NUMERIC NOT NULL,
    ma10 NUMERIC NOT NULL,
    ma20 NUMERIC NOT NULL,
    volume_ratio NUMERIC NOT NULL,
    is_20d_high INTEGER NOT NULL CHECK(is_20d_high IN (0,1)),
    updated_at TEXT NOT NULL,
    UNIQUE(source, sector_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_sector_scores_date_score
    ON sector_scores(trade_date, trend_score DESC);
CREATE INDEX IF NOT EXISTS idx_sector_scores_source_code_date
    ON sector_scores(source, sector_code, trade_date DESC);

CREATE TABLE IF NOT EXISTS sector_source_status (
    source TEXT PRIMARY KEY CHECK(source IN ('sw_l1','sw_l2','eastmoney')),
    status TEXT NOT NULL CHECK(status IN ('available','partial','unavailable')),
    sector_count INTEGER NOT NULL DEFAULT 0,
    successful_sector_count INTEGER NOT NULL DEFAULT 0,
    failed_sector_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL,
    last_success_at TEXT,
    last_error TEXT,
    elapsed_seconds NUMERIC NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
