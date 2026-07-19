CREATE TABLE IF NOT EXISTS corporate_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    corporate_action_type TEXT NOT NULL,
    effective_date TEXT,
    ratio TEXT,
    possible_price_mismatch INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'futu_opend',
    raw_summary TEXT,
    checked_at TEXT NOT NULL,
    UNIQUE(stock_id, corporate_action_type, effective_date, ratio)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_stock_date
ON corporate_actions(stock_id, effective_date);

CREATE TABLE IF NOT EXISTS stock_expectation_quality (
    stock_id INTEGER PRIMARY KEY REFERENCES stocks(id) ON DELETE CASCADE,
    quality_status TEXT NOT NULL,
    quality_reasons TEXT NOT NULL DEFAULT '[]',
    is_rankable INTEGER NOT NULL DEFAULT 0,
    morningstar_quality_status TEXT NOT NULL,
    morningstar_quality_reasons TEXT NOT NULL DEFAULT '[]',
    morningstar_is_rankable INTEGER NOT NULL DEFAULT 0,
    analyst_quality_status TEXT NOT NULL,
    analyst_quality_reasons TEXT NOT NULL DEFAULT '[]',
    analyst_is_rankable INTEGER NOT NULL DEFAULT 0,
    morningstar_quality_details TEXT NOT NULL DEFAULT '{}',
    analyst_quality_details TEXT NOT NULL DEFAULT '{}',
    calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expectation_quality_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK(source IN ('morningstar','analyst')),
    action TEXT NOT NULL CHECK(action IN ('exclude','allow','warning')),
    reason TEXT NOT NULL,
    note TEXT,
    reviewed_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE(stock_id, source)
);
