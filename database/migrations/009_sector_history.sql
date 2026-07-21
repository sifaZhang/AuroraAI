CREATE TABLE IF NOT EXISTS sector_industries (
    classification_system TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    sector_level INTEGER NOT NULL CHECK(sector_level > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (classification_system, sector_code)
);

CREATE TABLE IF NOT EXISTS sector_daily_bars (
    classification_system TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (classification_system, sector_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_sector_daily_bars_date
ON sector_daily_bars(classification_system, trade_date);

CREATE TABLE IF NOT EXISTS sector_memberships (
    classification_system TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    weight REAL,
    snapshot_date TEXT NOT NULL,
    membership_scope TEXT NOT NULL DEFAULT 'current_snapshot'
        CHECK(membership_scope = 'current_snapshot'),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    historical_use_is_approximate INTEGER NOT NULL DEFAULT 1
        CHECK(historical_use_is_approximate = 1),
    lookahead_bias_warning TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (classification_system, sector_code, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_sector_memberships_current
ON sector_memberships(classification_system, sector_code, is_current);

CREATE TABLE IF NOT EXISTS sector_history_sync_status (
    classification_system TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    sector_name TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'success', 'failed')),
    first_trade_date TEXT,
    last_trade_date TEXT,
    last_snapshot_date TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    bar_count INTEGER NOT NULL DEFAULT 0 CHECK(bar_count >= 0),
    member_count INTEGER NOT NULL DEFAULT 0 CHECK(member_count >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (classification_system, sector_code)
);
